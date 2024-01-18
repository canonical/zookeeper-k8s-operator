#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global cluster state for the ZooKeeper quorum."""
import logging
from typing import Set

from ops.framework import Framework, Object
from ops.model import Relation

from core.models import SUBSTRATES, ZKClient, ZKCluster, ZKServer
from literals import CLIENT_PORT, PEER, REL_NAME, SECURE_CLIENT_PORT

logger = logging.getLogger(__name__)


class ClusterState(Object):
    """Collection of global cluster state for the ZooKeeper quorum."""

    def __init__(self, charm: Framework | Object, substrate: SUBSTRATES):
        super().__init__(parent=charm, key="charm_state")
        self.substrate: SUBSTRATES = substrate

    # --- RAW RELATION ---

    @property
    def peer_relation(self) -> Relation | None:
        """The cluster peer relation."""
        return self.model.get_relation(PEER)

    @property
    def client_relations(self) -> Set[Relation]:
        """The relations of all client applications."""
        return set(self.model.relations[REL_NAME])

    # --- CORE COMPONENTS---

    @property
    def unit_server(self) -> ZKServer:
        """The server state of the current running Unit."""
        return ZKServer(
            relation=self.peer_relation, component=self.model.unit, substrate=self.substrate
        )

    @property
    def cluster(self) -> ZKCluster:
        """The cluster state of the current running App."""
        return ZKCluster(
            relation=self.peer_relation, component=self.model.app, substrate=self.substrate
        )

    @property
    def servers(self) -> Set[ZKServer]:
        """Grabs all servers in the current peer relation, including the running unit server.

        Returns:
            Set of ZKServers in the current peer relation, including the running unit server.
        """
        if not self.peer_relation:
            return set()

        servers = set()
        for unit in self.peer_relation.units:
            servers.add(
                ZKServer(relation=self.peer_relation, component=unit, substrate=self.substrate)
            )
        servers.add(self.unit_server)

        return servers

    @property
    def clients(self) -> Set[ZKClient]:
        """The state for all related client Applications."""
        clients = set()
        for relation in self.client_relations:
            if not relation.app:
                continue

            clients.add(
                ZKClient(
                    relation=relation,
                    component=relation.app,
                    substrate=self.substrate,
                    local_app=self.cluster.app,
                    password=self.cluster.client_passwords.get(f"relation-{relation.id}", ""),
                    uris=",".join(
                        [f"{endpoint}:{self.client_port}" for endpoint in self.endpoints]
                    ),
                    endpoints=",".join(self.endpoints),
                    tls="enabled" if self.cluster.tls else "disabled",
                )
            )

        return clients

    # --- CLUSTER INIT ---

    @property
    def client_port(self) -> int:
        """The port for clients to use.

        Returns:
            Int of client port
                2181 if TLS is not enabled
                2182 if TLS is enabled
        """
        if self.cluster.tls:
            return SECURE_CLIENT_PORT

        return CLIENT_PORT

    @property
    def endpoints(self) -> list[str]:
        """The connection uris for all started ZooKeeper units.

        Returns:
            List of unit addresses
        """
        return [server.host for server in self.servers]

    @property
    def started_servers(self) -> Set[ZKServer]:
        """The server states of all started peer-related Units."""
        return {server for server in self.servers if server.started}

    @property
    def all_units_related(self) -> bool:
        """Checks if currently related units make up all planned units.

        Returns:
            True if all units are related. Otherwise False
        """
        return len(self.servers) == self.model.app.planned_units()

    @property
    def all_units_declaring_ip(self) -> bool:
        """Flag confirming if all units have set `ip` to their unit data."""
        for server in self.servers:
            if not server.ip:
                return False

        return True

    @property
    def all_servers_added(self) -> bool:
        """Flag confirming that all units have been added to the ZooKeeper quorum.

        Returns:
            True if all unit-ids found in app peer data as 'added'. Otherwise False
        """
        if not self.cluster:
            return False

        return len(self.cluster.added_unit_ids) == len(self.servers)

    @property
    def lowest_unit_id(self) -> int | None:
        """Grabs the first unit in the currently deployed application.

        Returns:
            Integer of lowest unit-id in the app.
            None if not all planned units are related to the currently running unit.
        """
        # avoid passing valid results until all units peered
        if not self.all_units_related:
            return None

        return min([server.unit_id for server in self.servers])

    @property
    def init_leader(self) -> ZKServer | None:
        """The first ZooKeeper server to initialise the cluster with.

        When building the ZooKeeper quorum, non-init-leader servers join initially
        as quourm 'observer's before being granted quorum voting.
        """
        if not self.cluster:
            return None

        for server in self.servers:
            if (
                server.unit_id == self.lowest_unit_id
                and server.unit_id not in self.cluster.quorum_unit_ids
            ):
                return server

    @property
    def next_server(self) -> ZKServer | None:
        """The next ordinal server allowed to join the voting quorum."""
        if self.lowest_unit_id == None or not self.cluster:  # noqa: E711
            # not all units have related yet
            return None

        if self.init_leader:
            return self.init_leader

        # in the case of failover, but server is still in the quorum
        if self.all_servers_added:
            return self.unit_server

        for server in self.servers:
            if (
                self.cluster.quorum_unit_ids
                and server.unit_id == max(self.cluster.quorum_unit_ids) + 1
            ):
                return server

    @property
    def startup_servers(self) -> str:
        """The initial server strings of started units for cluster initialization.

        NOTE - These will be overwritten automatically by the ZooKeeper workload
        during dynamic reconfiguration as units join/depart.

        Returns:
            Comma-delimted string of ZooKeeper server strings
        """
        if not self.cluster:
            return ""

        if self.init_leader:
            return self.init_leader.server_string

        server_strings = set()
        for server in self.servers:
            if server.unit_id in self.cluster.added_unit_ids:
                server_strings.add(server.server_string)

        server_strings.add(self.unit_server.server_string.replace("participant", "observer"))

        return "\n".join(server_strings)

    @property
    def stale_quorum(self) -> bool:
        """Flag to check if units/servers need adding to the quorum."""
        if not self.all_units_related:
            return False

        if self.all_servers_added:
            return False

        return True

    # --- PASSWORD ROTATION --

    @property
    def all_rotated(self) -> bool:
        """Flag to check if all units have rotated their passwords."""
        for server in self.servers:
            if not server.password_rotated:
                return False

        return True

    # --- TLS ---

    @property
    def all_units_unified(self) -> bool:
        """Flag to check if all units have restarted and set portUnification."""
        if not self.all_units_related:
            return False

        for server in self.started_servers:
            if not server.unified:
                return False

        return True

    @property
    def all_units_quorum(self) -> bool:
        """Flag to check if all units are using the same quorum encryption."""
        if not self.cluster:
            return False

        for server in self.servers:
            if server.quorum != self.cluster.quorum:
                return False

        return True

    # --- HEALTH ---

    @property
    def healthy(self) -> bool:
        """Flag to check if the cluster is safe to update quorum members."""
        if (
            self.all_servers_added
            and not self.cluster.rotate_passwords
            and self.all_rotated
            and not self.cluster.switching_encryption
        ):
            return True

        return False

    @property
    def stable(self) -> bool:
        """Flag to check if the quorum is in a stable state, with all members up-to-date."""
        if not self.all_units_related:
            logger.debug("cluster not stable - not all units related")
            return False

        if self.stale_quorum:
            logger.debug("cluster not stable - quorum needs updating")
            return False

        if not self.all_servers_added:
            logger.debug("cluster not stable - not all units added")
            return False

        return True

    @property
    def ready(self) -> bool:
        """Flag to check if the charm is ready to handle related applications."""
        if not self.all_units_quorum:
            logger.debug("provider not ready - not all units quorum")
            return False

        if self.cluster.switching_encryption:
            logger.debug("provider not ready - switching encryption")
            return False

        if self.all_units_unified:
            logger.debug("provider not ready - all units unified")
            return False

        if not self.stable:
            logger.debug("provider not ready - cluster not stable")
            return False

        return True
