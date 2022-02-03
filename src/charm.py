#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""Zookeeper K8s charm module."""

import logging

from ops.charm import CharmBase, ConfigChangedEvent
from ops.main import main
from ops.model import (
    ActiveStatus,
    BlockedStatus,
    Container,
    MaintenanceStatus,
    WaitingStatus,
)
from ops.pebble import ServiceStatus

logger = logging.getLogger(__name__)


class ZookeeperK8sCharm(CharmBase):
    """Zookeeper K8s Charm operator."""

    def __init__(self, *args):
        super().__init__(*args)

        # Observe charm events
        event_observe_mapping = {
            self.on.zookeeper_k8s_pebble_ready: self._on_config_changed,
            self.on.config_changed: self._on_config_changed,
            self.on.update_status: self._on_update_status,
        }
        for event, observer in event_observe_mapping.items():
            self.framework.observe(event, observer)

    # ---------------------------------------------------------------------------
    #   Handlers for Charm Events
    # ---------------------------------------------------------------------------

    def _on_config_changed(self, event: ConfigChangedEvent):
        """Handler for the config-changed event."""
        # Validate charm configuration
        try:
            self._validate_config()
        except Exception as e:
            self.unit.status = BlockedStatus(f"{e}")
            return

        # Check Pebble has started in the container
        container: Container = self.unit.get_container("zookeeper-k8s")
        if not container.can_connect():
            logger.debug("waiting for pebble to start")
            self.unit.status = MaintenanceStatus("waiting for pebble to start")
            return

        # Add Pebble layer with the zookeeper-k8s service
        container.add_layer(
            "zookeeper-k8s",
            {
                "summary": "zookeeper-k8s layer",
                "description": "pebble config layer for zookeeper-k8s",
                "services": {
                    "zookeeper-k8s": {
                        "override": "replace",
                        "summary": "zookeeper-k8s service",
                        "command": "sleep infinity",
                        "startup": "enabled",
                        "environment": {},
                    }
                },
            },
            combine=True,
        )
        container.replan()

        # Update charm status
        self._on_update_status()

    def _on_update_status(self, _=None) -> None:
        """Handler for the update-status event."""
        # Check if the zookeeper-k8s service is configured
        container: Container = self.unit.get_container("zookeeper-k8s")
        if not container.can_connect() or "zookeeper-k8s" not in container.get_plan().services:
            self.unit.status = WaitingStatus("zookeeper-k8s service not configured yet")
            return

        # Check if the zookeeper-k8s service is running
        if container.get_service("zookeeper-k8s").current == ServiceStatus.ACTIVE:
            self.unit.status = ActiveStatus()
        else:
            self.unit.status = BlockedStatus("zookeeper-k8s service is not running")

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
    main(ZookeeperK8sCharm, use_juju_for_storage=True)
