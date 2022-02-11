#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.

"""Zookeeper cluster peer relation module."""

__all__ = ["ZookeeperClusterEvents", "ZookeeperCluster"]

import logging
import re
from typing import List, Set

from ops.charm import CharmBase, CharmEvents
from ops.framework import EventBase, EventSource, Object, StoredState
from ops.model import Relation

logger = logging.getLogger(__name__)

# Relation unit data keys
HOST_UNIT_KEY = "host"
CLIENT_PORT_UNIT_KEY = "client-port"
SERVER_PORT_UNIT_KEY = "server-port"
ELECTION_PORT_UNIT_KEY = "election-port"
_REQUIRED_UNIT_KEYS = (
    HOST_UNIT_KEY,
    CLIENT_PORT_UNIT_KEY,
    SERVER_PORT_UNIT_KEY,
    ELECTION_PORT_UNIT_KEY,
)

# Relation app data keys
CLUSTER_ADDRESSES_APP_KEY = "cluster-addresses"
CLIENT_ADDRESSES_APP_KEY = "client-addresses"


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


class ZookeeperClusterEvents(CharmEvents):
    """Zookeeper cluster events.

    This class defines the events that the Zookeeper cluster can emit.

    Events:
        servers_changed (_ServersChangedEvent)

    Example:
        class ZookeeperK8sCharm(CharmBase):
            on = ZookeeperClusterEvents()

            def __init__(self, *args):
                super().__init__(*args)

                self.framework.observe(self.servers_changed, self._on_servers_changed)

            def _on_servers_changed(self, event):
                # Handle the servers changed event.
    """

    servers_changed = EventSource(_ServersChangedEvent)


class ZookeeperCluster(Object):
    """Zookeeper cluster peer relation.

    Example:
        import socket


        class ZookeeperK8sCharm(CharmBase):
            on = ZookeeperClusterEvents()

            def __init__(self, *args):
                super().__init__(*args)
                self.cluster = ZookeeperCluster(self)
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

            def _on_cluster_relation_created(self, event):
                self.cluster.register_server(socket.getfqdn(), 2181, 2888, 3888)
    """

    _stored = StoredState()

    def __init__(self, charm: CharmBase) -> None:
        """Constructor.

        Args:
            charm (CharmBase): The charm that implements the relation.
        """
        super().__init__(charm, "cluster")
        self.charm = charm
        self.framework.observe(charm.on.leader_elected, self._handle_servers_update)
        self.framework.observe(charm.on.cluster_relation_changed, self._handle_servers_update)
        self.framework.observe(charm.on.cluster_relation_departed, self._handle_servers_update)
        self._stored.set_default(last_cluster_addresses=[], last_client_addresses=[])

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

    def register_server(
        self, host: str, client_port: int, server_port: int, election_port: int
    ) -> None:
        """Register a server as part of the cluster.

        This method will cause a relation-changed event in the other units,
        since it sets the keys "host", "server-port", and "election-port" in
        the unit relation data.

        Args:
            host (str): IP or hostname of the zookeeper server to be registered.
            client_port (int): Zookeeper client port.
            server_port (int): Zookeeper server port.
            election_port (int): Zookeeper election port.
        """
        relation_data = self._relation.data[self.model.unit]
        relation_data[HOST_UNIT_KEY] = host
        relation_data[CLIENT_PORT_UNIT_KEY] = str(client_port)
        relation_data[SERVER_PORT_UNIT_KEY] = str(server_port)
        relation_data[ELECTION_PORT_UNIT_KEY] = str(election_port)
        if self.model.unit.is_leader():
            self._update_servers()

    @property
    def _relation(self) -> Relation:
        return self.framework.model.get_relation("cluster")

    def _handle_servers_update(self, _):
        """Handler for the leader-elected, relation-changed, and relation-departed events.

        If the application data has been updated,
        the ZookeeperClusterEvents.servers_changed event will be triggered.
        """
        # Only need to continue if:
        #   - The unit is the leader
        #   - The relation object is initialized
        #   - The event was triggered by a change
        #     in the unit data of the remote unit
        if self._relation:
            if self.model.unit.is_leader():
                self._update_servers()
            if (
                self._stored.last_cluster_addresses != self.cluster_addresses
                or self._stored.last_client_addresses != self.client_addresses
            ):
                self._stored.last_cluster_addresses = self.cluster_addresses
                self._stored.last_client_addresses = self.client_addresses
                self.charm.on.servers_changed.emit()

    def _update_servers(self) -> None:
        """Update servers in peer relation.

        This function writes in the application relation data, therefore, only the leader
        can call it. If a non-leader unit calls this function, an exception will be raised.
        """
        cluster_addresses = set()
        client_addresses = set()
        units = self._relation.units.copy()
        units.add(self.model.unit)
        for unit in units:
            # Check that the required unit data is present in the remote unit
            relation_data = self._relation.data[unit]
            if not all(data in relation_data for data in _REQUIRED_UNIT_KEYS):
                continue
            # Build client and cluster addresses
            cluster_address = self._build_cluster_address(
                host=relation_data[HOST_UNIT_KEY],
                server_port=relation_data[SERVER_PORT_UNIT_KEY],
                election_port=relation_data[ELECTION_PORT_UNIT_KEY],
            )
            client_address = self._build_client_address(
                host=relation_data[HOST_UNIT_KEY],
                client_port=relation_data[CLIENT_PORT_UNIT_KEY],
            )
            # Store addresses
            cluster_addresses.add(cluster_address)
            client_addresses.add(client_address)
        # Write sorted addresses in application relation data.
        app_data = self._relation.data[self.model.app]
        app_data[CLUSTER_ADDRESSES_APP_KEY] = ",".join(_natural_sort_set(cluster_addresses))
        app_data[CLIENT_ADDRESSES_APP_KEY] = ",".join(_natural_sort_set(client_addresses))

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

    def _build_cluster_address(self, host: str, server_port: str, election_port: str) -> str:
        """Build cluster address.

        Args:
            host (str): IP or hostname of the zookeeper server to be registered.
            server_port (str): Zookeeper server port.
            election_port (str): Zookeeper election port.

        Returns:
            str: String with the following format: <host>:<server-port>:<election-port>.
        """
        return f"{host}:{server_port}:{election_port}"

    def _build_client_address(self, host: str, client_port: str) -> str:
        """Build client address.

        Args:
            host (str): IP or hostname of the zookeeper server to be registered.
            client_port (str): Zookeeper client port.

        Returns:
            str: String with the following format: <host>:<client-port>.
        """
        return f"{host}:{client_port}"
