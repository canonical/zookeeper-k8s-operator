#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Upgrades implementation."""
import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v0.upgrade import (
    ClusterNotReadyError,
    DataUpgrade,
    DependencyModel,
    KubernetesClientError,
)
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.apps_v1 import StatefulSet
from ops.framework import EventBase
from ops.model import ModelError
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_random
from typing_extensions import override

from literals import CONTAINER

if TYPE_CHECKING:
    from charm import ZooKeeperCharm

logger = logging.getLogger(__name__)


class ZooKeeperDependencyModel(BaseModel):
    """Model for ZooKeeper Operator dependencies."""

    service: DependencyModel


class ZKUpgradeEvents(DataUpgrade):
    """Implementation of :class:`DataUpgrade` overrides for in-place upgrades."""

    def __init__(self, charm: "ZooKeeperCharm", **kwargs):
        super().__init__(charm, **kwargs)
        self.charm = charm

        self.framework.observe(
            getattr(self.charm.on, "upgrade_charm"), self._on_zookeeper_pebble_ready_upgrade
        )

    def _on_zookeeper_pebble_ready_upgrade(self, _: EventBase) -> None:
        """Handler for the `upgrade-charm` events handled during in-place upgrades."""
        # ensure pebble-ready only fires after normal peer-relation-driven server init
        if not self.charm.workload.alive or not self.charm.state.unit_server.started or self.idle:
            return

        self.apply_backwards_compatibility_fixes()

        # useful to have a log here to indicate that the upgrade is progressing
        try:
            self.charm.workload.healthy
        except ModelError:
            logger.info(f"{CONTAINER} workload service not running, re-initialising...")

        # re-initialise + replan pebble layer if no service, or service not running
        self.charm.init_server()

        try:
            self.post_upgrade_check()
        except ClusterNotReadyError as e:
            logger.error(e.cause)
            self.set_unit_failed()
            return

        self.set_unit_completed()

    @property
    def idle(self) -> bool:
        """Checks if cluster state is idle.

        Returns:
            True if cluster state is idle. Otherwise False
        """
        return not bool(self.upgrade_stack)

    @retry(stop=stop_after_attempt(5), wait=wait_random(min=1, max=5), reraise=True)
    def post_upgrade_check(self) -> None:
        """Runs necessary checks validating the unit is in a healthy state after upgrade."""
        self.pre_upgrade_check()

        if not self.charm.workload.healthy:
            raise ClusterNotReadyError(
                message="Post-upgrade check failed and cannot safely upgrade",
                cause="Container service not ruunning",
            )

    @override
    def pre_upgrade_check(self) -> None:
        # setting initial partition to last unit, ensures upgrades stop until first unit sets completed
        if self.idle:
            self._set_rolling_update_partition(partition=len(self.charm.state.servers) - 1)

        status = self.charm.quorum_manager.is_syncing()
        if not status.passed:
            raise ClusterNotReadyError(
                message="Pre-upgrade check failed and cannot safely upgrade", cause=status.cause
            )

    @override
    def log_rollback_instructions(self) -> None:
        logger.critical(
            "\n".join(
                [
                    "Unit failed to upgrade and requires manual rollback to previous stable version.",
                    "    1. Re-run `pre-upgrade-check` action on the leader unit to enter 'recovery' state",
                    "    2. Run `juju refresh` to the previously deployed charm revision",
                ]
            )
        )
        return

    @override
    def _set_rolling_update_partition(self, partition: int) -> None:
        """Set the rolling update partition to a specific value."""
        try:
            patch = {"spec": {"updateStrategy": {"rollingUpdate": {"partition": partition}}}}
            Client().patch(  # pyright: ignore [reportGeneralTypeIssues]
                StatefulSet,
                name=self.charm.model.app.name,
                namespace=self.charm.model.name,
                obj=patch,
            )
            logger.debug(f"Kubernetes StatefulSet partition set to {partition}")
        except ApiError as e:
            if e.status.code == 403:
                cause = "`juju trust` needed"
            else:
                cause = str(e)
            raise KubernetesClientError("Kubernetes StatefulSet patch failed", cause)

    def apply_backwards_compatibility_fixes(self) -> None:
        """A range of functions needed for backwards compatibility."""
        return
