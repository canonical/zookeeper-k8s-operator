#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.

"""Library for the ZooKeeper Cluster peer relation."""

# The unique Charmhub library identifier, never change it
LIBID = "96a636f0d6384b3da051a095c12c8437"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


import logging
import re
from typing import List, Set

from ops.charm import CharmBase, CharmEvents
from ops.framework import EventBase, EventSource, Object
from ops.model import Relation

logger = logging.getLogger(__name__)

# Relation unit data keys
HOST_UNIT_KEY = "host"

# Relation app data keys
CLUSTER_ADDRESSES_APP_KEY = "cluster-addresses"
CLIENT_ADDRESSES_APP_KEY = "client-addresses"
CLIENT_PORT_APP_KEY = "client-port"
SERVER_PORT_APP_KEY = "server-port"
ELECTION_PORT_APP_KEY = "election-port"


def _natural_sort_set(_set: Set[str]) -> List[str]:
    """Sort a set "naturally".

    This function orders a set taking into account numbers inside the string.
    Example:
        Input: {my-host-2, my-host-1, my-host-11}
        Output: {my-host-1, my-host-2, my-host-11}

    Args:
        _set (Set[str]): The set to be sorted.

    Returns:
        List[str]: Sorted list of the set.
    """

    def convert(text):
        return int(text) if text.isdigit() else text.lower()

    def alphanum_key(key):
        return [convert(c) for c in re.split("([0-9]+)", key)]

    return sorted(_set, key=alphanum_key)


class _ServersChangedEvent(EventBase):
    """Event emitted whenever there is a change in the set of zookeeper servers."""


class ZooKeeperClusterEvents(CharmEvents):
    """ZooKeeper cluster events.

    This class defines the events that the ZooKeeper cluster can emit.

    Events:
        servers_changed (_ServersChangedEvent)

    Example:
        class ZooKeeperK8sCharm(CharmBase):
            on = ZooKeeperClusterEvents()

            def __init__(self, *args):
                super().__init__(*args)

                self.framework.observe(self.servers_changed, self._on_servers_changed)

            def _on_servers_changed(self, event):
                # Handle the servers changed event.
    """

    servers_changed = EventSource(_ServersChangedEvent)


class ZooKeeperCluster(Object):
    """ZooKeeper cluster peer relation.

    Example:
        import socket


        class ZooKeeperK8sCharm(CharmBase):
            on = ZooKeeperClusterEvents()

            def __init__(self, *args):
                super().__init__(*args)
                self.cluster = ZooKeeperCluster(self)
                self.framework.observe(
                    self.servers_changed,
                    self._on_servers_changed
                )
                self.framework.observe(
                    self.cluster_relation_created,
                    self._on_cluster_relation_created
                )

            def _on_servers_changed(self, event):
                # Handle the servers changed event.
                cluster_addresses = self.cluster.cluster_addresses
                client_addresses = self.cluster.client_addresses

            def _on_cluster_relation_created(self, event):
                # Handle the cluster-relation-created event.
                self.cluster.register_server(socket.getfqdn())
    """

    def __init__(
        self,
        charm: CharmBase,
        client_port: int = 2181,
        server_port: int = 2888,
        election_port: int = 3888,
    ) -> None:
        """Constructor.

        Args:
            charm (CharmBase): The charm that implements the relation.
            client_port (int, optional): Client port. Defaults to 2181.
            server_port (int, optional): Server port. Defaults to 2888.
            election_port (int, optional): Election port. Defaults to 3888.
        """
        super().__init__(charm, "cluster")
        self.charm = charm
        self.client_port = client_port
        self.server_port = server_port
        self.election_port = election_port

        # Observe charm events
        event_observe_mapping = {
            charm.on.leader_elected: self._on_leader_elected,
            charm.on.cluster_relation_changed: self._handle_servers_update,
            charm.on.cluster_relation_departed: self._handle_servers_update,
        }
        for event, observer in event_observe_mapping.items():
            self.framework.observe(event, observer)

    @property
    def cluster_addresses(self) -> List[str]:
        """Get the zookeeper servers from the relation.

        Returns:
            List[str]: list containing the zookeeper servers. The servers are represented
                       with a string that follows this format: <host>:<server-port>:<election-port>
        """
        cluster_addresses = self._get_cluster_addresses_from_app_relation()
        return cluster_addresses.split(",") if cluster_addresses else []

    @property
    def client_addresses(self) -> List[str]:
        """Get the zookeeper servers from the relation.

        Returns:
            List[str]: list containing the zookeeper servers. The servers are represented
                       with a string that follows this format: <host>:<server-port>:<election-port>
        """
        client_addresses = self._get_client_addresses_from_app_relation()
        return client_addresses.split(",") if client_addresses else []

    def register_server(self, host: str) -> None:
        """Register a server as part of the cluster.

        This method will cause a relation-changed event in the other units,
        since it sets the key "host" in the unit relation data.

        Args:
            host (str): IP or hostname of the zookeeper server to be registered.
        """
        relation_data = self._relation.data[self.model.unit]
        relation_data[HOST_UNIT_KEY] = host
        self._update_servers()

    @property
    def _relation(self) -> Relation:
        return self.framework.model.get_relation("cluster")

    def _on_leader_elected(self, _) -> None:
        """Handler for the leader-elected event."""
        if self._relation:
            self._update_servers()

    def _handle_servers_update(self, _):
        """Handler for the relation-changed, and relation-departed events.

        If the application data has been updated,
        the ZooKeeperClusterEvents.servers_changed event will be triggered.
        """
        # Only need to continue if:
        #   - The unit is the leader
        #   - The relation object is initialized
        if self._relation:
            self._update_servers()
            self.charm.on.servers_changed.emit()

    def _update_servers(self) -> None:
        """Update servers in peer relation.

        This function writes in the application relation data, therefore, it checks whether
        the current unit is the leader or not. If a non-leader unit calls this function,
        it won't do anything.
        """
        if not self.model.unit.is_leader():
            return
        cluster_addresses = set()
        client_addresses = set()
        units = self._relation.units.copy()
        units.add(self.model.unit)
        for unit in units:
            # Check that the required unit data is present in the remote unit
            relation_data = self._relation.data[unit]
            if HOST_UNIT_KEY not in relation_data:
                continue
            # Build client and cluster addresses
            cluster_address = self._build_cluster_address(
                host=relation_data[HOST_UNIT_KEY],
                server_port=self.server_port,
                election_port=self.election_port,
            )
            client_address = self._build_client_address(
                host=relation_data[HOST_UNIT_KEY],
                client_port=self.client_port,
            )
            # Store addresses
            cluster_addresses.add(cluster_address)
            client_addresses.add(client_address)
        # Write sorted addresses in application relation data.
        app_data = self._relation.data[self.model.app]
        app_data[CLUSTER_ADDRESSES_APP_KEY] = ",".join(_natural_sort_set(cluster_addresses))
        app_data[CLIENT_ADDRESSES_APP_KEY] = ",".join(_natural_sort_set(client_addresses))
        app_data[CLIENT_PORT_APP_KEY] = str(self.client_port)
        app_data[SERVER_PORT_APP_KEY] = str(self.server_port)
        app_data[ELECTION_PORT_APP_KEY] = str(self.election_port)

    def _get_cluster_addresses_from_app_relation(self) -> str:
        """Get cluster addresses from the app relation data.

        Returns:
            str: Comma-separated list of cluster addresses.
        """
        return (
            self._relation.data[self.model.app].get(CLUSTER_ADDRESSES_APP_KEY)
            if self._relation
            else None
        )

    def _get_client_addresses_from_app_relation(self) -> str:
        """Get client addresses from the app relation data.

        Returns:
            str: Comma-separated list of client addresses.
        """
        return (
            self._relation.data[self.model.app].get(CLIENT_ADDRESSES_APP_KEY)
            if self._relation
            else None
        )

    def _build_cluster_address(self, host: str, server_port: int, election_port: int) -> str:
        """Build cluster address.

        Args:
            host (str): IP or hostname of the zookeeper server to be registered.
            server_port (int): ZooKeeper server port.
            election_port (int): ZooKeeper election port.

        Returns:
            str: String with the following format: <host>:<server-port>:<election-port>.
        """
        return f"{host}:{server_port}:{election_port}"

    def _build_client_address(self, host: str, client_port: int) -> str:
        """Build client address.

        Args:
            host (str): IP or hostname of the zookeeper server to be registered.
            client_port (int): ZooKeeper client port.

        Returns:
            str: String with the following format: <host>:<client-port>.
        """
        return f"{host}:{client_port}"
