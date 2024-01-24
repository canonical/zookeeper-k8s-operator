#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for for handling quorum + ACL updates."""
import logging
import re
import socket
from functools import cached_property
from typing import Set

from charms.zookeeper.v0.client import (
    MemberNotReadyError,
    MembersSyncingError,
    QuorumLeaderNotFoundError,
    ZooKeeperManager,
)
from kazoo.exceptions import BadArgumentsError
from kazoo.handlers.threading import KazooTimeoutError
from kazoo.security import make_acl
from ops.charm import RelationEvent

from core.cluster import ClusterState
from literals import CLIENT_PORT

logger = logging.getLogger(__name__)


class QuorumManager:
    """Manager for for handling quorum + ACL updates."""

    def __init__(self, state: ClusterState):
        self.state = state

    @cached_property
    def client(self) -> ZooKeeperManager:
        """Cached client manager application for performing ZK commands."""
        admin_username = "super"
        admin_password = self.state.cluster.internal_user_credentials.get(admin_username, "")
        active_hosts = [server.host for server in self.state.started_servers]

        return ZooKeeperManager(
            hosts=active_hosts,
            client_port=CLIENT_PORT,
            username=admin_username,
            password=admin_password,
        )

    def get_hostname_mapping(self) -> dict[str, str]:
        """Collects hostname mapping for current unit.

        Returns:
            Dict of string keys 'hostname', 'fqdn', 'ip' and their values
        """
        hostname = socket.gethostname()
        fqdn = socket.getfqdn()

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0)
        s.connect(("10.10.10.10", 1))
        ip = s.getsockname()[0]
        s.close()

        return {"hostname": hostname, "fqdn": fqdn, "ip": ip}

    def _get_updated_servers(self, add: list[str], remove: list[str]) -> dict[str, str]:
        """Simple wrapper for building `updated_servers` for passing to app data updates."""
        servers_to_update = add + remove

        updated_servers = {}
        for server_string in servers_to_update:
            unit_id = str(int(re.findall(r"server.([0-9]+)", server_string)[0]) - 1)
            if server_string in add:
                updated_servers[unit_id] = "added"
            elif server_string in remove:
                updated_servers[unit_id] = "removed"

        return updated_servers

    def update_cluster(self) -> dict[str, str]:
        """Adds and removes members from the current ZK quorum.

        To be ran by the Juju leader.

        After grabbing all the "started" units that the leader can see in the peer relation
            unit data.
        Removes members not in the quorum anymore (i.e `relation_departed`/`leader_elected` event)
        Adds new members to the quorum (i.e `relation_joined` event).

        Returns:
            A mapping of Juju unit IDs and updated state for changed units
            To be used in updating the app data
                e.g {"0": "added", "1": "removed"}
        """
        # NOTE - BUG in Apache ZooKeeper - https://issues.apache.org/jira/browse/ZOOKEEPER-3577
        # This means that we cannot dynamically reconfigure without also having a PLAIN port open
        # Ideally, have a check here for `client_port=self.secure_client_port` if tls.enabled
        # Until then, we can just use the insecure port for convenience
        active_server_strings = {server.server_string for server in self.state.started_servers}

        try:
            # remove units first, faster due to no startup/sync delay
            zk_members = self.client.server_members
            servers_to_remove = list(zk_members - active_server_strings)
            logger.debug(f"{servers_to_remove=}")

            self.client.remove_members(members=servers_to_remove)

            # sorting units to ensure units are added in id order
            zk_members = self.client.server_members
            servers_to_add = sorted(active_server_strings - zk_members)
            logger.debug(f"{servers_to_add=}")

            self.client.add_members(members=servers_to_add)

            return self._get_updated_servers(add=servers_to_add, remove=servers_to_remove)

        # caught errors relate to a unit/zk_server not yet being ready to change
        except (
            MembersSyncingError,
            MemberNotReadyError,
            QuorumLeaderNotFoundError,
            KazooTimeoutError,
            BadArgumentsError,
        ) as e:
            logger.warning(str(e))
            return {}

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

    def update_acls(self, event: RelationEvent | None = None) -> None:
        """Compares leader auth config to incoming relation config, applies add/remove actions.

        Args:
            event (optional): used for checking `RelationBrokenEvent`
        """
        leader_chroots = self.client.leader_znodes(path="/")
        logger.debug(f"{leader_chroots=}")

        requested_acls = set()
        requested_chroots = set()
        for client in self.state.clients:
            generated_acl = make_acl(
                scheme="sasl",
                credential=client.username,
                read="r" in client.chroot_acl,
                write="w" in client.chroot_acl,
                create="c" in client.chroot_acl,
                delete="d" in client.chroot_acl,
                admin="a" in client.chroot_acl,
            )
            logger.debug(f"{generated_acl=}")

            requested_acls.add(generated_acl)

            # FIXME: data-platform-libs should handle this when it's implemented
            if client.chroot:
                if event and client.relation and client.relation.id == event.relation.id:
                    continue  # skip broken chroots, so they're removed
                else:
                    requested_chroots.add(client.chroot)

            # Looks for newly related applications not in config yet
            if client.chroot not in leader_chroots:
                logger.debug(f"CREATE CHROOT - {client.chroot}")
                self.client.create_znode_leader(client.chroot, [generated_acl])

            # Looks for existing related applications
            logger.debug(f"UPDATE CHROOT - {client.chroot}")
            self.client.set_acls_znode_leader(client.chroot, [generated_acl])

        # Looks for applications no longer in the relation but still in config
        for chroot in sorted(leader_chroots - requested_chroots, reverse=True):
            if not self._is_child_of(chroot, requested_chroots):
                logger.debug(f"DROP CHROOT - {chroot}")
                self.client.delete_znode_leader(chroot)
