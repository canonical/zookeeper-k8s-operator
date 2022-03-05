#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""ZooKeeper K8s charm module."""

import logging
import socket
from typing import Any, Dict

from charms.zookeeper_k8s.v0.cluster import ZooKeeperCluster, ZooKeeperClusterEvents
from charms.zookeeper_k8s.v0.zookeeper import ZooKeeperProvides
from ops.charm import CharmBase, RelationJoinedEvent
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    Container,
    MaintenanceStatus,
    StatusBase,
    WaitingStatus,
)
from ops.pebble import ServiceStatus

logger = logging.getLogger(__name__)


def _convert_key_to_confluent_syntax(key: str) -> str:
    new_key = key.replace("_", "___").replace("-", "__").replace(".", "_")
    new_key = "".join([f"_{char}" if char.isupper() else char for char in new_key])
    return f"ZOOKEEPER_{new_key.upper()}"


class CharmError(Exception):
    """Charm Error Exception."""

    def __init__(self, message: str, status: StatusBase = BlockedStatus) -> None:
        self.message = message
        self.status = status


class ZooKeeperK8sCharm(CharmBase):
    """ZooKeeper K8s Charm operator."""

    on = ZooKeeperClusterEvents()

    def __init__(self, *args):
        super().__init__(*args)

        # Relation objects
        self.cluster = ZooKeeperCluster(self, self.client_port)  # Peer relation
        self.zookeeper = ZooKeeperProvides(self)  # ZooKeeper relation

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
        """Get environment variables for zookeeper.properties.

        This function uses the configuration zookeeper-properties to generate the
        environment variables needed to configure ZooKeeper and in the format expected
        by the container.

        Returns:
            Dictionary with the environment variables needed by the ZooKeeper container.
        """
        envs = {}
        for zk_prop in self.config["zookeeper-properties"].splitlines():
            zookeeper_property = zk_prop.strip()
            if zookeeper_property.startswith("#") or "=" not in zookeeper_property:
                continue
            key, value = zookeeper_property.split("=")
            key = _convert_key_to_confluent_syntax(key)
            envs[key] = value
        return envs

    @property
    def client_port(self) -> int:
        """Get the client port from the config.

        Returns:
            int: The clientPort specified in the zookeeper-properties config. Default=2181.
        """
        return self.zookeeper_properties.get("ZOOKEEPER_CLIENT_PORT", 2181)

    # ---------------------------------------------------------------------------
    #   Handlers for Charm Events
    # ---------------------------------------------------------------------------

    def _on_config_changed(self, _) -> None:
        """Handler for the config-changed event."""
        try:
            # Validations
            self._validate_config()
            container: Container = self.unit.get_container("zookeeper")
            self._check_container_ready(container)

            # Add Pebble layer with the zookeeper service
            container.add_layer("zookeeper", self._get_zookeeper_layer(), combine=True)
            container.replan()

            # Provide zookeeper client addresses through the relation
            if self.unit.is_leader() and self.cluster.client_addresses:
                self.zookeeper.update_hosts(",".join(self.cluster.client_addresses))

            # Update charm status
            self._on_update_status()
        except CharmError as e:
            logger.debug(e.message)
            self.unit.status = e.status(e.message)

    def _on_update_status(self, _=None) -> None:
        """Handler for the update-status event."""
        try:
            container: Container = self.unit.get_container("zookeeper")
            self._check_container_ready(container)
            self._check_service_configured(container)
            self._check_service_active(container)
            self.unit.status = ActiveStatus()
        except CharmError as e:
            logger.debug(e.message)
            self.unit.status = e.status(e.message)

    def _on_cluster_relation_created(self, _) -> None:
        """Handler for the cluster relation-created event."""
        self.cluster.register_server(host=socket.getfqdn())

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
            CharmError: if charm configuration is invalid.
        """
        pass

    def _check_container_ready(self, container: Container) -> None:
        """Check Pebble has started in the container.

        Args:
            container (Container): Container to be checked.

        Raises:
            CharmError: if container is not ready.
        """
        if not container.can_connect():
            raise CharmError("waiting for pebble to start", MaintenanceStatus)

    def _check_service_configured(self, container: Container) -> None:
        """Check if zookeeper service has been successfully configured.

        Args:
            container (Container): Container to be checked.

        Raises:
            CharmError: if zookeeper service has not been configured.
        """
        if "zookeeper" not in container.get_plan().services:
            raise CharmError("zookeeper service not configured yet", WaitingStatus)

    def _check_service_active(self, container: Container) -> None:
        """Check if the zookeeper service is running.

        Raises:
            CharmError: if zookeeper service is not running.
        """
        if container.get_service("zookeeper").current != ServiceStatus.ACTIVE:
            raise CharmError("zookeeper service is not running")

    def _get_zookeeper_layer(self) -> Dict[str, Any]:
        """Get ZooKeeper layer for Pebble."""
        heap_size = self.config["heap-size"]
        return {
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


if __name__ == "__main__":  # pragma: no cover
    main(ZooKeeperK8sCharm)
