#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Zookeeper K8s charm module."""

import logging
import socket
from typing import Any, Dict

from ops.charm import (
    CharmBase,
    ConfigChangedEvent,
    RelationCreatedEvent,
    RelationJoinedEvent,
)
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    Container,
    MaintenanceStatus,
    WaitingStatus,
)
from ops.pebble import ServiceStatus

from cluster import ZookeeperCluster, ZookeeperClusterEvents
from zookeeper_provides import ZookeeperProvides

logger = logging.getLogger(__name__)


def _convert_key_to_confluent_syntax(key: str) -> str:
    new_key = key.replace("_", "___").replace("-", "__").replace(".", "_")
    new_key = "".join([f"_{char}" if char.isupper() else char for char in new_key])
    return f"ZOOKEEPER_{new_key.upper()}"


class ZookeeperK8sCharm(CharmBase):
    """Zookeeper K8s Charm operator."""

    on = ZookeeperClusterEvents()

    def __init__(self, *args):
        super().__init__(*args)

        # Relation objects
        self.cluster = ZookeeperCluster(self)  # Peer relation
        self.zookeeper = ZookeeperProvides(self)  # Zookeeper relation

        # Observe charm events
        event_observe_mapping = {
            self.on.zookeeper_pebble_ready: self._on_config_changed,
            self.on.config_changed: self._on_config_changed,
            self.on.servers_changed: self._on_config_changed,
            self.on.update_status: self._on_update_status,
            self.on.cluster_relation_created: self._on_cluster_relation_created,
            self.on.zookeeper_relation_joined: self._on_zookeeper_relation_joined,
        }
        for event, observer in event_observe_mapping.items():
            self.framework.observe(event, observer)

    # ---------------------------------------------------------------------------
    #   Properties
    # ---------------------------------------------------------------------------

    @property
    def server_id(self) -> str:
        """Get zookeeper server id.

        The server id must be a string containing a number from 1 to 255.
        This function uses the charm unit number to generate the server id.
        Since the unit numbers start with 0, the returned number will be the
        number of the unit plus 1.

        Returns:
            A string with the zookeeper server id.
        """
        unit_number = int(self.unit.name.split("/")[1])
        return str(unit_number + 1)

    @property
    def zookeeper_properties(self) -> Dict[str, Any]:
        """Get Zookeeper environment variables.

        This function uses the template `templates/zookeeper.properties`,
        replaces the variables taking into account the charm configuration,
        and converts it to the expected environment variables in the container.

        Returns:
            Dictionary with the environment variables needed for Zookeeper container.
        """
        envs = {}
        for zookeeper_property in self.config["zookeeper-properties"].splitlines():
            if "=" not in zookeeper_property:
                continue
            key, value = zookeeper_property.split("=")
            key = _convert_key_to_confluent_syntax(key)
            envs[key] = value
        return envs

    # ---------------------------------------------------------------------------
    #   Handlers for Charm Events
    # ---------------------------------------------------------------------------

    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        """Handler for the config-changed event."""
        # Validate charm configuration
        try:
            self._validate_config()
        except Exception as e:
            self.unit.status = BlockedStatus(f"{e}")
            return

        # Check Pebble has started in the container
        container: Container = self.unit.get_container("zookeeper")
        if not container.can_connect():
            logger.debug("waiting for pebble to start")
            self.unit.status = MaintenanceStatus("waiting for pebble to start")
            return

        # Add Pebble layer with the zookeeper service
        heap_size = self.config["heap-size"]
        layer = {
            "summary": "zookeeper layer",
            "description": "pebble config layer for zookeeper",
            "services": {
                "zookeeper": {
                    "override": "replace",
                    "summary": "zookeeper service",
                    "command": "/etc/confluent/docker/run",
                    "startup": "enabled",
                    "environment": {
                        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                        "container": "oci",
                        "LANG": "C.UTF-8",
                        "CUB_CLASSPATH": "/usr/share/java/cp-base-new/*",
                        "COMPONENT": "zookeeper",
                        "ZOOKEEPER_SERVER_ID": self.server_id,
                        "ZOOKEEPER_SERVERS": ";".join(self.cluster.cluster_addresses),
                        "KAFKA_HEAP_OPTS": f"-Xmx{heap_size} -Xms{heap_size}",
                        **self.zookeeper_properties,
                    },
                }
            },
        }
        container.add_layer("zookeeper", layer, combine=True)
        container.replan()

        # Provide zookeeper client addresses through the relation
        if self.cluster.client_addresses:
            self.zookeeper.update_hosts(",".join(self.cluster.client_addresses))

        # Update charm status
        self._on_update_status()

    def _on_update_status(self, _=None) -> None:
        """Handler for the update-status event."""
        # Check if the zookeeper service is configured
        container: Container = self.unit.get_container("zookeeper")
        if not container.can_connect() or "zookeeper" not in container.get_plan().services:
            self.unit.status = WaitingStatus("zookeeper service not configured yet")
            return

        # Check if the zookeeper service is running
        if container.get_service("zookeeper").current == ServiceStatus.ACTIVE:
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = BlockedStatus("zookeeper service is not running")

    def _on_cluster_relation_created(self, _: RelationCreatedEvent) -> None:
        """Handler for the cluster relation-created event."""
        self.cluster.register_server(
            host=socket.getfqdn(),
            client_port=2181,
            server_port=2888,
            election_port=3888,
        )

    def _on_zookeeper_relation_joined(self, event: RelationJoinedEvent) -> None:
        """Handler for the zookeeper relation-joined event."""
        if self.unit.is_leader():
            self.zookeeper.update_hosts(
                ",".join(self.cluster.client_addresses), relation_id=event.relation.id
            )

    # ---------------------------------------------------------------------------
    #   Validation and configuration
    # ---------------------------------------------------------------------------

    def _validate_config(self) -> None:
        """Validate charm configuration.

        Raises:
            Exception: if charm configuration is invalid.
        """
        pass


if __name__ == "__main__":  # pragma: no cover
    main(ZookeeperK8sCharm)
