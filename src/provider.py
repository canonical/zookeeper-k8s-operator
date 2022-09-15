#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""ZooKeeperProvider class and methods."""
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Set

from charms.zookeeper.v0.client import (
    MemberNotReadyError,
    MembersSyncingError,
    QuorumLeaderNotFoundError,
    ZooKeeperManager,
)
from kazoo.handlers.threading import KazooTimeoutError
from kazoo.security import ACL, make_acl
from ops.charm import RelationBrokenEvent, RelationEvent
from ops.framework import EventBase, Object
from ops.model import MaintenanceStatus, Relation

from cluster import UnitNotFoundError
from literals import PEER, REL_NAME
from utils import generate_password

logger = logging.getLogger(__name__)


class ZooKeeperProvider(Object):
    """Handler for updating client relations to ZooKeeper."""

    def __init__(self, charm) -> None:
        super().__init__(charm, "client")

        self.charm = charm

        self.framework.observe(
            self.charm.on[REL_NAME].relation_changed, self._on_client_relation_updated
        )
        self.framework.observe(
            self.charm.on[REL_NAME].relation_broken, self._on_client_relation_broken
        )

    @property
    def app_relation(self) -> Relation:
        """Gets the current ZK peer relation."""
        return self.charm.model.get_relation(PEER)

    @property
    def client_relations(self) -> List[Relation]:
        """Gets the relations for all related client applications."""
        return self.charm.model.relations[REL_NAME]

    def relation_config(
        self, relation: Relation, event: Optional[EventBase] = None
    ) -> Optional[Dict[str, str]]:
        """Gets the auth config for a currently related application.

        Args:
            relation: the relation you want to build config for
            event (optional): the corresponding event.
                If passed and is `RelationBrokenEvent`, will skip and return `None`

        Returns:
            Dict containing relation `username`, `password`, `chroot`, `acl`
            `None` if `RelationBrokenEvent` is passed as event
        """
        if isinstance(event, RelationBrokenEvent) and event.relation.id == relation.id:
            return None

        # generating username
        username = f"relation-{relation.id}"

        # Default to empty string in case passwords not set
        password = self.app_relation.data[self.charm.app].get(username, "")

        # Default to full permissions if not set by the app
        acl = relation.data[relation.app].get("chroot-acl", "cdrwa")

        # Attempt to default to `database` app value. Else None, it's unset
        chroot = relation.data[relation.app].get(
            "chroot", relation.data[relation.app].get("database", "")
        )

        # If chroot is unset, skip, we don't want it part of the config
        if not chroot:
            return None

        if not str(chroot).startswith("/"):
            chroot = f"/{chroot}"

        return {
            "username": username,
            "password": password,
            "chroot": chroot,
            "acl": acl,
        }

    def build_jaas_users(self, event: Optional[EventBase] = None) -> str:
        """Builds the necessary user strings to add to ZK JAAS config files.

        Args:
            event (optional): used for checking `RelationBrokenEvent`

        Returns:
            Newline delimited string of JAAS users from relation data
        """
        jaas_users = []
        for relation in self.relations_config(event=event).values():
            username = relation.get("username", None)
            password = relation.get("password", None)

            # For during restarts when passwords are unset for departed relations
            if username and not password:
                continue

            jaas_users.append(f'user_{username}="{password}"')

        return "\n".join(jaas_users)

    def relations_config(self, event: Optional[EventBase] = None) -> Dict[str, Dict[str, str]]:
        """Gets auth configs for all currently related applications.

        Args:
            event (optional): used for checking `RelationBrokenEvent`

        Returns:
            Dict of key = `relation_id`, value = `relations_config()` for all related apps
        """
        relations_config = {}
        for relation in self.client_relations:
            config = self.relation_config(relation=relation, event=event)

            # in the case of RelationBroken or unset chroot
            if not config:
                continue

            relations_config[str(relation.id)] = config

        return relations_config

    def build_acls(self, event: Optional[EventBase] = None) -> Dict[str, List[ACL]]:
        """Gets ACLs for all currently related applications.

        Args:
            event (optional): used for checking `RelationBrokenEvent`

        Returns:
            Dict of `chroot`s with value as list of ACLs for the `chroot`
        """
        acls = defaultdict(list)

        for _, relation_config in self.relations_config(event=event).items():
            chroot = relation_config["chroot"]
            generated_acl = make_acl(
                scheme="sasl",
                credential=relation_config["username"],
                read="r" in relation_config["acl"],
                write="w" in relation_config["acl"],
                create="c" in relation_config["acl"],
                delete="d" in relation_config["acl"],
                admin="a" in relation_config["acl"],
            )

            acls[chroot].append(generated_acl)

        return dict(acls)

    def relations_config_values_for_key(
        self, key: str, event: Optional[EventBase] = None
    ) -> Set[str]:
        """Grabs a specific auth config value from all related applications.

        Args:
            event (optional): used for checking `RelationBrokenEvent`

        Returns:
            Set of all app values matching a specific key from `relations_config()`
        """
        return {config.get(key, "") for config in self.relations_config(event=event).values()}

    def update_acls(self, event: Optional[EventBase] = None) -> None:
        """Compares leader auth config to incoming relation config.

        Applies necessary add/update/remove actions.

        Args:
            event (optional): used for checking `RelationBrokenEvent`
        """
        super_password, _ = self.charm.cluster.passwords
        zk = ZooKeeperManager(
            hosts=self.charm.cluster.active_hosts,
            client_port=self.charm.cluster.client_port,
            username="super",
            password=super_password,
        )

        leader_chroots = zk.leader_znodes(path="/")
        logger.debug(f"{leader_chroots=}")

        relation_chroots = self.relations_config_values_for_key("chroot", event=event)
        logger.debug(f"{relation_chroots=}")

        acls = self.build_acls(event=event)
        logger.debug(f"{acls=}")

        # Looks for newly related applications not in config yet
        for chroot in relation_chroots - leader_chroots:
            logger.debug(f"CREATE CHROOT - {chroot}")
            zk.create_znode_leader(chroot, acls[chroot])

        # Looks for existing related applications
        for chroot in relation_chroots & leader_chroots:
            logger.debug(f"UPDATE CHROOT - {chroot}")
            zk.set_acls_znode_leader(chroot, acls[chroot])

        # Looks for applications no longer in the relation but still in config
        for chroot in leader_chroots - relation_chroots:
            if not self._is_child_of(chroot, relation_chroots):
                logger.debug(f"DROP CHROOT - {chroot}")
                zk.delete_znode_leader(chroot)

    @staticmethod
    def _is_child_of(path: str, chroots: Set[str]) -> bool:
        """Checks if given path is a child znode from a set of chroot paths.

        Args:
            path: the desired znode path to check parenthood of
            chroots: the potential parent znode paths

        Returns:
            True if `path` is a child of a znode in `chroots`. Otherwise False.
        """
        for chroot in chroots:
            if path.startswith(chroot.rstrip("/") + "/"):
                return True

        return False

    def apply_relation_data(self, event: Optional[EventBase] = None) -> None:
        """Updates relation data with new auth values upon concluded client_relation events.

        Args:
            event (optional): used for checking `RelationBrokenEvent`
        """
        relations_config = self.relations_config(event=event)
        for relation_id, config in relations_config.items():
            hosts = self.charm.cluster.active_hosts

            relation_data = {}
            relation_data["username"] = config["username"]
            relation_data["password"] = config["password"] or generate_password()
            relation_data["chroot"] = config["chroot"]
            relation_data["endpoints"] = ",".join(list(hosts))
            relation_data["uris"] = (
                ",".join([f"{host}:{self.charm.cluster.client_port}" for host in hosts])
                + config["chroot"]
            )

            self.app_relation.data[self.charm.app].update(
                {relation_data["username"]: relation_data["password"]}
            )

            self.charm.model.get_relation(REL_NAME, int(relation_id)).data[self.charm.app].update(
                relation_data
            )

    def _on_client_relation_updated(self, event: RelationEvent) -> None:
        """Updates ACLs while handling `client_relation_changed`.

        Args:
            event (optional): used for checking `RelationBrokenEvent`
        """
        if self.charm.unit.is_leader():
            try:
                self.update_acls(event=event)
            except (
                MembersSyncingError,
                MemberNotReadyError,
                QuorumLeaderNotFoundError,
                KazooTimeoutError,
                UnitNotFoundError,
            ) as e:
                logger.debug(str(e))
                self.charm.unit.status = MaintenanceStatus(str(e))
                event.defer()
                return

            self.apply_relation_data(event=event)
            logger.debug(f"passwords set for {event.relation}")

        # don't trigger rolling restart until leader has finished updating relation data
        for config in self.relations_config(event=event).values():
            if config.get("chroot", None) and not config.get("password", None):
                logger.debug("passwords not set for {event.relation}")
                event.defer()
                return

        # All units restart after relation changed event to add new users
        self.charm.on[self.charm.restart.name].acquire_lock.emit()

    def _on_client_relation_broken(self, event: RelationBrokenEvent) -> None:
        """Removes user from ZK app data on `client_relation_broken`.

        Args:
            event: used for passing `RelationBrokenEvent` to subequent methods
        """
        if self.charm.unit.is_leader():
            username = f"relation-{event.relation.id}"
            if username in self.charm.cluster.relation.data[self.charm.app]:
                del self.charm.cluster.relation.data[self.charm.app][username]

        # call normal updated handler
        self._on_client_relation_updated(event=event)
