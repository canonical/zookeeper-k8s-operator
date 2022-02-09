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


def _natural_sort_set(_set: Set[str]) -> Set[str]:
    """Sort a set "naturally".

    This function orders a set taking into account numbers inside the string.
    Example:
        Input: {my-host-2, my-host-1, my-host-11}
        Output: {my-host-1, my-host-2, my-host-11}

    Args:
        _set (Set[str]): The set to be sorted.

    Returns:
        Set[str]: [description]
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
                    self.cluster_relation_joined,
                    self._on_cluster_relation_joined
                )

            def _on_servers_changed(self, event):
                # Handle the servers changed event.

            def _on_cluster_relation_joined(self, event):
                self.cluster.register_server(socket.getfqdn(), 2888, 3888)
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
        self._stored.set_default(last_servers=[])

    @property
    def servers(self) -> List[str]:
        """Get the zookeeper servers from the relation.

        Returns:
            List[str]: list containing the zookeeper servers. The servers are represented
                       with a string that follows this format: <host>:<server-port>:<election-port>
        """
        servers = self._get_servers_from_app_relation()
        return _natural_sort_set(servers)

    @property
    def _relation(self) -> Relation:
        return self.framework.model.get_relation("cluster")

    def register_server(self, host: str, server_port: int, election_port: int) -> None:
        """Register a server as part of the cluster.

        This method will cause a relation-changed event in the other units,
        since it sets the keys "host", "server-port", and "election-port" in
        the unit relation data.

        Args:
            host (str): IP or hostname of the zookeeper server to be registered.
            server_port (int): Zookeeper server port.
            election_port (int): Zookeeper election port.
        """
        relation_data = self._relation.data[self.model.unit]
        relation_data["host"] = host
        relation_data["server-port"] = str(server_port)
        relation_data["election-port"] = str(election_port)
        if self.model.unit.is_leader():
            self._update_servers()

    def _handle_servers_update(self, event):
        """Handler for the relation-changed event.

        If the application data has been updated,
        the ZookeeperClusterEvents.servers_changed event will be triggered.
        """
        # Only need to continue if:
        #   - The unit is the leader
        #   - The relation object is initialized
        #   - The event was triggered by a change
        #     in the unit data of the remote unit
        if self.model.unit.is_leader() and self._relation:
            self._update_servers()
        if self._stored.last_servers != self.servers:
            self._stored.last_servers = self.servers
            self.charm.on.servers_changed.emit()

    def _build_server_string(self, host: str, server_port: str, election_port: str) -> str:
        """Build server string.

        Args:
            host (str): IP or hostname of the zookeeper server to be registered.
            server_port (str): Zookeeper server port.
            election_port (str): Zookeeper election port.

        Returns:
            str: String with the following format: <host>:<server-port>:<election-port>.
        """
        return f"{host}:{server_port}:{election_port}"

    def _update_servers(self):
        """Update server string in peer relation.

        This function writes in the application relation data, therefore, only the leader
        can call it. If a non-leader unit calls this function, an exception will be raised.
        The function will take the server and add it to the "servers" key in the application
        relation data.
        """
        servers = set()
        units = self._relation.units.copy()
        units.add(self.model.unit)
        for unit in units:
            # Check that the required unit data is present in the remote unit
            required_data = ["host", "server-port", "election-port"]
            relation_data = self._relation.data[unit]
            if not all(data in relation_data for data in required_data):
                continue

            server = self._build_server_string(
                host=relation_data["host"],
                server_port=relation_data["server-port"],
                election_port=relation_data["election-port"],
            )
            servers.add(server)

        self._relation.data[self.model.app]["servers"] = str(servers)
        logger.debug(f"{self._relation.data}")

    def _get_servers_from_app_relation(self) -> Set[str]:
        servers_str = (
            self._relation.data[self.model.app].get("servers") if self._relation else None
        )
        return eval(servers_str) if servers_str else set()
