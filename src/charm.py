#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed k8s Operator for Apache ZooKeeper."""

import logging

from charms.rolling_ops.v0.rollingops import RollingOpsManager
from ops.charm import ActionEvent, CharmBase
from ops.framework import EventBase
from ops.main import main
from ops.model import ActiveStatus, Container
from ops.pebble import Layer, PathError

from cluster import (
    NoPasswordError,
    NotUnitTurnError,
    UnitNotFoundError,
    ZooKeeperCluster,
)
from config import ZooKeeperConfig
from literals import CHARM_KEY, CHARM_USERS
from provider import ZooKeeperProvider

logger = logging.getLogger(__name__)


class ZooKeeperK8sCharm(CharmBase):
    """Charmed Operator for ZooKeeper K8s."""

    def __init__(self, *args):
        super().__init__(*args)
        self.cluster = ZooKeeperCluster(self)
        self.zookeeper_config = ZooKeeperConfig(self)
        self.provider = ZooKeeperProvider(self)
        self.restart = RollingOpsManager(self, relation="restart", callback=self._restart)

        self.framework.observe(
            getattr(self.on, "zookeeper_pebble_ready"), self._on_zookeeper_pebble_ready
        )
        self.framework.observe(
            getattr(self.on, "leader_elected"), self._on_cluster_relation_updated
        )
        self.framework.observe(
            getattr(self.on, "cluster_relation_changed"), self._on_cluster_relation_updated
        )
        self.framework.observe(
            getattr(self.on, "cluster_relation_joined"), self._on_cluster_relation_updated
        )
        self.framework.observe(
            getattr(self.on, "cluster_relation_departed"), self._on_cluster_relation_updated
        )
        self.framework.observe(
            getattr(self.on, "get_super_password_action"), self._get_super_password_action
        )
        self.framework.observe(
            getattr(self.on, "get_sync_password_action"), self._get_sync_password_action
        )
        self.framework.observe(getattr(self.on, "set_password_action"), self._set_password_action)

    @property
    def container(self) -> Container:
        """Grabs the current ZooKeeper container."""
        return self.unit.get_container(CHARM_KEY)

    @property
    def _zookeeper_layer(self) -> Layer:
        """Returns a Pebble configuration layer for ZooKeeper."""
        layer_config = {
            "summary": "zookeeper layer",
            "description": "Pebble config layer for zookeeper",
            "services": {
                CHARM_KEY: {
                    "override": "replace",
                    "summary": "zookeeper",
                    "command": self.zookeeper_config.zookeeper_command,
                    "startup": "enabled",
                    "environment": {"KAFKA_OPTS": self.zookeeper_config.extra_args},
                }
            },
        }
        return Layer(layer_config)

    def _on_zookeeper_pebble_ready(self, event: EventBase) -> None:
        """Handler for the `zookeeper_pebble_ready` event.

        This includes:
            - Writing config to config files
        """
        if not self.container.can_connect():
            event.defer()
            return

        # setting default app passwords on leader start
        if self.unit.is_leader():
            for password in ["super_password", "sync_password"]:
                current_value = self.cluster.relation.data[self.app].get(password, None)
                self.cluster.relation.data[self.app].update(
                    {password: current_value or self.cluster.generate_password()}
                )

        if not self.cluster.passwords_set:
            event.defer()
            return

        # checks if the unit is next, grabs the servers to add, and it's own config for debugging
        try:
            servers, unit_config = self.cluster.ready_to_start(self.unit)
        except (NotUnitTurnError, UnitNotFoundError, NoPasswordError) as e:
            logger.info(str(e))
            self.unit.status = self.cluster.status
            event.defer()
            return

        # grabbing up-to-date jaas users from the relations
        super_password, sync_password = self.cluster.passwords
        users = self.provider.build_jaas_users(event=event)

        try:
            # servers properties needs to be written to dynamic config
            self.zookeeper_config.set_zookeeper_myid()
            self.zookeeper_config.set_zookeeper_properties()
            self.zookeeper_config.set_zookeeper_dynamic_properties(servers=servers)
            self.zookeeper_config.set_jaas_config(
                sync_password=sync_password, super_password=super_password, users=users
            )
        except PathError:
            event.defer()
            return

        self.container.add_layer(CHARM_KEY, self._zookeeper_layer, combine=True)
        self.container.replan()
        self.unit.status = ActiveStatus()

        # unit flags itself as 'started' so it can be retrieved by the leader
        self.cluster.relation.data[self.unit].update(unit_config)
        self.cluster.relation.data[self.unit].update({"state": "started"})

    def _on_cluster_relation_updated(self, event: EventBase) -> None:
        """Handler for events triggered by changing units.

        This includes:
            - Adding ready-to-start units to app data
            - Updating ZK quorum config
            - Updating app data state
        """
        # Logic for password rotation
        if self.cluster.relation.data[self.app].get("rotate-passwords"):
            # All units have rotated the password, we can remove the global flag
            if self.unit.is_leader() and self.cluster._all_rotated():
                self.cluster.relation.data[self.app]["rotate-passwords"] = ""
                return

            # Own unit finished rotation, no need to issue a new lock
            if self.cluster.relation.data[self.unit].get("password-rotated"):
                return

            logger.info("Acquiring lock for password rotation")
            self.on[self.restart.name].acquire_lock.emit()
            return

        else:
            # After removal of global flag, each unit can reset its state so more
            # password rotations can happen
            self.cluster.relation.data[self.unit]["password-rotated"] = ""

        if not self.unit.is_leader():
            return

        # ensures leader doesn't remove all units upon departure
        if getattr(event, "departing_unit", None) == self.unit:
            return

        # units need to exist in the app data to be iterated through for next_turn
        for unit in self.cluster.started_units:
            unit_id = self.cluster.get_unit_id(unit)
            current_value = self.cluster.relation.data[self.app].get(str(unit_id), None)

            # sets to "added" for init quorum leader, if not already exists
            # may already exist if during the case of a failover of the first unit
            if unit_id == self.cluster.lowest_unit_id:
                self.cluster.relation.data[self.app].update(
                    {str(unit_id): current_value or "added"}
                )

        if not self.cluster.passwords_set:
            event.defer()
            return

        # adds + removes members for all self-confirmed started units
        updated_servers = self.cluster.update_cluster()

        # either Active if successful, else Maintenance
        self.unit.status = self.cluster.status

        if self.cluster.status == ActiveStatus():
            self.cluster.relation.data[self.app].update(updated_servers)
        else:
            # in the event some unit wasn't started/ready
            event.defer()
            return

    def _restart(self, event: EventBase):
        """Handler for rolling restart events triggered by zookeeper_relation_changed/broken."""
        # for when relations trigger during start-up of the cluster
        if (not self.cluster.relation.data[self.unit].get("state", None) == "started") or (
            not self.cluster.relation.data[self.app].get(
                str(self.cluster.get_unit_id(self.unit)), None
            )
        ):
            event.defer()
            return

        # grabbing up-to-date jaas users from the relations
        super_password, sync_password = self.cluster.passwords
        users = self.provider.build_jaas_users(event=event)

        try:
            self.zookeeper_config.set_jaas_config(
                sync_password=sync_password, super_password=super_password, users=users
            )
        except PathError:
            event.defer()
            return

        self.container.restart(CHARM_KEY)

        # Indicate that unit has completed restart
        if self.cluster.relation.data[self.app].get("rotate-passwords"):
            self.cluster.relation.data[self.unit]["password-rotated"] = "true"

        # If leader runs last on RollingOps restart, this code would be enough
        """
        if self.unit.is_leader() and self.cluster.relation.data[self.app].get("rotate-passwords"):
            logger.error("LEADER REMOVING ROTATE_PASSWORDS")
            self.cluster.relation.data[self.app]["rotate-passwords"] = ""
        """

    def _get_super_password_action(self, event: ActionEvent) -> None:
        """Handler for get-super-password action event."""
        event.set_results({"super-password": self.cluster.passwords[0]})

    def _get_sync_password_action(self, event: ActionEvent) -> None:
        """Handler for get-sync-password action event."""
        event.set_results({"sync-password": self.cluster.passwords[1]})

    def _set_password_action(self, event: ActionEvent) -> None:
        """Handler for set-password action.

        Set the password for a specific user, if no passwords are passed, generate them.
        """
        if not self.unit.is_leader():
            msg = "Password rotation must be called on leader unit"
            logger.error(msg)
            event.fail(msg)
            return

        username = event.params.get("username", "super")
        if username not in CHARM_USERS:
            msg = f"The action can be run only for users used by the charm: {CHARM_USERS} not {username}."
            logger.error(msg)
            event.fail(msg)
            return

        new_password = event.params.get("password", self.cluster.generate_password())

        # Passwords should not be the same.
        if new_password in self.cluster.passwords:
            event.log("The old and new passwords are equal.")
            event.set_results({f"{username}-password": new_password})
            return

        # Store those passwords on application databag
        self.cluster.relation.data[self.app].update({f"{username}_password": new_password})

        # Add password flag
        self.cluster.relation.data[self.app]["rotate-passwords"] = "true"
        event.set_results({f"{username}-password": new_password})


if __name__ == "__main__":
    main(ZooKeeperK8sCharm)
