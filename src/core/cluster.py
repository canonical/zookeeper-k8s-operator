#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global cluster state for the ZooKeeper quorum."""
import logging
from typing import TYPE_CHECKING, Set

from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseProviderData,
    DataPeerData,
    DataPeerOtherUnitData,
    DataPeerUnitData,
)
from lightkube.core.exceptions import ApiError as LightKubeApiError
from ops.framework import Object
from ops.model import Relation, Unit
from tenacity import retry, retry_if_exception_cause_type, stop_after_attempt, wait_fixed

from core.models import SUBSTRATES, ZKClient, ZKCluster, ZKServer
from core.structured_config import ExposeExternal
from literals import (
    CLIENT_PORT,
    PEER,
    REL_NAME,
    SECRETS_UNIT,
    SECURE_CLIENT_PORT,
    Status,
)

if TYPE_CHECKING:
    from charm import ZooKeeperCharm

logger = logging.getLogger(__name__)


class ClusterState(Object):
    """Collection of global cluster state for Framework/Object."""

    def __init__(self, charm: "ZooKeeperCharm", substrate: SUBSTRATES):
        super().__init__(parent=charm, key="charm_state")
        self.substrate: SUBSTRATES = substrate

        self.peer_app_interface = DataPeerData(self.model, relation_name=PEER)
        self.peer_unit_interface = DataPeerUnitData(
            self.model, relation_name=PEER, additional_secret_fields=SECRETS_UNIT
        )
        self.client_provider_interface = DatabaseProviderData(self.model, relation_name=REL_NAME)
        self._servers_data = {}
        self.config = charm.config

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
            relation=self.peer_relation,
            data_interface=self.peer_unit_interface,
            component=self.model.unit,
            substrate=self.substrate,
        )

    @property
    def peer_units_data_interfaces(self) -> dict[Unit, DataPeerOtherUnitData]:
        """The cluster peer relation."""
        if not self.peer_relation or not self.peer_relation.units:
            return {}

        for unit in self.peer_relation.units:
            if unit not in self._servers_data:
                self._servers_data[unit] = DataPeerOtherUnitData(
                    model=self.model, unit=unit, relation_name=PEER
                )
        return self._servers_data

    @property
    def cluster(self) -> ZKCluster:
        """The cluster state of the current running App."""
        return ZKCluster(
            relation=self.peer_relation,
            data_interface=self.peer_app_interface,
            component=self.model.app,
            substrate=self.substrate,
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
        for unit, data_interface in self.peer_units_data_interfaces.items():
            servers.add(
                ZKServer(
                    relation=self.peer_relation,
                    data_interface=data_interface,
                    component=unit,
                    substrate=self.substrate,
                )
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
                    data_interface=self.client_provider_interface,
                    component=relation.app,
                    substrate=self.substrate,
                    local_app=self.cluster.app,
                    password=self.cluster.client_passwords.get(f"relation-{relation.id}", ""),
                    uris=self.endpoints,
                    endpoints=self.endpoints,
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
        port = SECURE_CLIENT_PORT if self.cluster.tls else CLIENT_PORT

        return port

    @property
    @retry(
        wait=wait_fixed(5),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_cause_type(LightKubeApiError),
        reraise=True,
    )
    def endpoints_external(self) -> str:
        """Comma-delimited string of connection uris for all started ZooKeeper unit, for external access."""
        auth = "plain" if not self.cluster.tls else "tls"
        return ",".join(
            sorted(
                {
                    f"{server.node_ip}:{self.unit_server.k8s.get_nodeport(auth)}"
                    for server in self.servers
                }
            )
        )

    @property
    def endpoints(self) -> str:
        """The connection uris for all started ZooKeeper units.

        Returns:
            List of unit addresses
        """
        if self.config.expose_external is not ExposeExternal.FALSE:
            try:
                return self.endpoints_external
            except LightKubeApiError as e:
                # TODO: Push maintenance status
                logger.debug(e)
                return ""

        return ",".join(
            sorted(
                [
                    (
                        f"{server.internal_address}:{self.client_port}"
                        if self.substrate == "k8s"
                        else server.ip
                    )
                    for server in self.servers
                ]
            )
        )

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
    def all_installed(self) -> Status:
        """Gets appropriate Status if all units have finished installing."""
        if not self.all_units_related:
            return Status.NOT_ALL_RELATED

        if not self.all_units_declaring_ip:
            return Status.NOT_ALL_IP

        return Status.ACTIVE

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
    def stable(self) -> Status:
        """Gets appropriate Status if the quorum is in a stable state, with all members up-to-date."""
        if not self.all_units_related:
            return Status.NOT_ALL_RELATED

        if self.stale_quorum:
            return Status.STALE_QUORUM

        if self.cluster.is_restore_in_progress:
            return Status.ONGOING_RESTORE

        if not self.all_servers_added:
            return Status.NOT_ALL_ADDED

        return Status.ACTIVE

    @property
    def ready(self) -> Status:
        """Gets appropriate Status if the charm is ready to handle related applications."""
        if not self.all_units_quorum:
            return Status.NOT_ALL_QUORUM

        if self.cluster.switching_encryption:
            return Status.SWITCHING_ENCRYPTION

        if self.all_units_unified:
            return Status.ALL_UNIFIED

        return self.stable

    @property
    def is_next_restore_step_possible(self) -> bool:
        """Are all units done with the current restore instruction?"""
        current_instruction = self.cluster.restore_instruction
        return all((unit.restore_progress is current_instruction for unit in self.servers))
