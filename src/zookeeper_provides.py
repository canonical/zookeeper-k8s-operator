#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.

"""ZooKeeper provides relation module."""

__all__ = ["ZooKeeperProvides"]

import logging
from typing import Optional

from ops.charm import CharmBase
from ops.framework import Object
from ops.model import Relation

logger = logging.getLogger(__name__)

# Relation app data keys
HOSTS_APP_KEY = "hosts"


class ZooKeeperProvides(Object):
    """ZooKeeper provides relation.

    Example:
        class ZooKeeperK8sCharm(CharmBase):
            on = ZooKeeperClusterEvents()

            def __init__(self, *args):
                super().__init__(*args)
                self.cluster = ZooKeeperCluster(self)
                self.zookeeper = ZooKeeperProvides(self)
                self.framework.observe(
                    self.on.zookeeper_relation_changed,
                    self._update_hosts
                )
                self.framework.observe(self.on.servers_changed, self._on_servers_changed)

            def _update_hosts(self, _=None):
                if self.unit.is_leader():
                    self.zookeeper.update_hosts(self.cluster.client_addresses)

            def _on_servers_changed(self, event):
                # Reload the service...
                self._update_hosts()
    """

    def __init__(self, charm: CharmBase, endpoint_name: str = "zookeeper") -> None:
        """Constructor.

        Args:
            charm (CharmBase): The charm that implements the relation.
            endpoint_name (str): Endpoint name of the relation.
        """
        super().__init__(charm, endpoint_name)
        self._endpoint_name = endpoint_name

    def update_hosts(self, client_addresses: str, relation_id: Optional[int] = None) -> None:
        """Update hosts in the zookeeper relation.

        This method will cause a relation-changed event in the requirer units
        of the relation.

        Args:
            client_addresses (str): Comma-listed addresses of zookeeper clients.
            relation_id (Optional[int]): Id of the relation. If set, it will be used to update
                                         the relation data of the specified relation. If not set,
                                         the data for all the relations will be updated.
        """
        relation: Relation
        if relation_id:
            relation = self.model.get_relation(self._endpoint_name, relation_id)
            self._update_hosts_in_relation(relation, client_addresses)
            relation.data[self.model.app][HOSTS_APP_KEY] = client_addresses
        else:
            for relation in self.model.relations[self._endpoint_name]:
                self._update_hosts_in_relation(relation, client_addresses)

    def _update_hosts_in_relation(self, relation: Relation, hosts: str) -> None:
        """Update hosts relation data if needed.

        Args:
            relation (Relation): Relation to be updated.
            hosts (str): String with the zookeeper hosts.
        """
        if relation.data[self.model.app].get(HOSTS_APP_KEY) != hosts:
            relation.data[self.model.app][HOSTS_APP_KEY] = hosts
