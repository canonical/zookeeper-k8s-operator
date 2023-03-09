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
from ops.model import Relation

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
        if password:
            acls_added = "true"
        else:
            password = generate_password()
            acls_added = "false"

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
            "acls-added": acls_added,
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
            key: name of the key to be used
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
        if not self.charm.unit.is_leader():
            return

        relations_config = self.relations_config(event=event)
        for relation_id, config in relations_config.items():
            # avoid adding relation data for related apps without acls
            if config["acls-added"] != "true":
                logger.debug(f"{relation_id} has yet to add acls")
                continue

            hosts = self.charm.cluster.active_hosts

            relation_data = {}
            relation_data["username"] = config["username"]
            relation_data["password"] = config["password"]
            relation_data["chroot"] = config["chroot"]
            relation_data["endpoints"] = ",".join(list(hosts))

            if self.charm.cluster.quorum == "ssl":
                relation_data["tls"] = "enabled"
                port = self.charm.cluster.secure_client_port
            else:
                relation_data["tls"] = "disabled"
                port = self.charm.cluster.client_port

            relation_data["uris"] = (
                ",".join([f"{host}:{port}" for host in hosts]) + config["chroot"]
            )

            logger.debug(f"setting relation data - {relation_data.items()}")

            self.charm.model.get_relation(REL_NAME, int(relation_id)).data[self.charm.app].update(
                relation_data
            )

    def _on_client_relation_updated(self, event: RelationEvent) -> None:
        """Updates ACLs while handling `client_relation_joined` events..

        Once credentals and ACLs are added for the event username, sets them to relation data.
        Future `client_relation_changed` events called on non-leader units checks passwords before
            restarting.
        """
        if not self.charm.unit.is_leader() or not self.charm.cluster.stable:
            event.defer()
            return

        # ACLs created before passwords set to avoid restarting before successful adding
        try:
            logger.debug(f"adding acls for {getattr(event.app, 'name', None)}")
            self.update_acls(event=event)
        except (
                MembersSyncingError,
                MemberNotReadyError,
                QuorumLeaderNotFoundError,
                KazooTimeoutError,
                UnitNotFoundError,
        ) as e:
            logger.warning(str(e))
            event.defer()
            return

        # new users won't have a password yet, one will be generated here
        relation_config = self.relation_config(relation=event.relation)
        if relation_config and relation_config.get("acls-added"):
            logger.debug(f"updating passwords for {getattr(event.app, 'name', None)}")
            # triggers relation_changed for other units to restart
            self.app_relation.data[self.charm.app].update(
                {relation_config["username"]: relation_config["password"]}
            )

            # roll leader unit to apply password to jaas config
            self.charm.on[f"{self.charm.restart.name}"].acquire_lock.emit()


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

    @property
    def ready(self) -> bool:
        """Checks whether the cluster is ready to accept client relations.
                Returns:
                    True if ready. Otherwise False
                """
        if not self.charm.cluster.all_units_quorum:
            logger.debug("provider not ready - not all units quorum")
            return False

        if self.charm.tls.upgrading:
            logger.debug("provider not ready - upgrading")
            return False

        if self.charm.tls.all_units_unified:
            logger.debug("provider not ready - all units unified")
            return False

        if not self.charm.cluster.stable:
            logger.debug("provider not ready - cluster not stable")
            return False

        return True


