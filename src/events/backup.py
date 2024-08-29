#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handlers for creating and restoring backups."""
import json
import logging
from typing import TYPE_CHECKING

from charms.data_platform_libs.v0.s3 import (
    CredentialsChangedEvent,
    CredentialsGoneEvent,
    S3Requirer,
)
from ops import ActionEvent
from ops.framework import Object

from literals import S3_BACKUPS_PATH, S3_REL_NAME, Status
from managers.backup import BackupManager

if TYPE_CHECKING:
    from charm import ZooKeeperCharm

logger = logging.getLogger(__name__)


class BackupEvents(Object):
    """Event handlers for creating and restoring backups."""

    def __init__(self, charm):
        super().__init__(charm, "backup")
        self.charm: "ZooKeeperCharm" = charm
        self.s3_requirer = S3Requirer(self.charm, S3_REL_NAME)
        self.backup_manager = BackupManager(self.charm.state)

        self.framework.observe(
            self.s3_requirer.on.credentials_changed, self._on_s3_credentials_changed
        )
        self.framework.observe(self.s3_requirer.on.credentials_gone, self._on_s3_credentials_gone)

        self.framework.observe(self.charm.on.create_backup_action, self._on_create_backup_action)
        self.framework.observe(self.charm.on.list_backups_action, self._on_list_backups_action)
        self.framework.observe(self.charm.on.restore_action, self._on_restore_action)

    def _on_s3_credentials_changed(self, event: CredentialsChangedEvent):
        if not self.charm.unit.is_leader():
            return

        if not self.charm.state.peer_relation:
            self.charm._set_status(Status.NO_PEER_RELATION)
            event.defer()
            return

        s3_parameters = self.s3_requirer.get_s3_connection_info()
        required_parameters = [
            "bucket",
            "access-key",
            "secret-key",
        ]
        missing_required_parameters = [
            param for param in required_parameters if param not in s3_parameters
        ]
        if missing_required_parameters:
            logger.warning(
                f"Missing required S3 parameters in relation with S3 integrator: {missing_required_parameters}"
            )
            self.charm._set_status(Status.MISSING_S3_CONFIG)
            return

        s3_parameters = self.s3_requirer.get_s3_connection_info()

        # Add some sensible defaults (as expected by the code) for missing optional parameters
        s3_parameters.setdefault("endpoint", "https://s3.amazonaws.com")
        s3_parameters.setdefault("region", "")
        s3_parameters.setdefault("path", S3_BACKUPS_PATH)
        s3_parameters.setdefault("s3-uri-style", "host")
        s3_parameters.setdefault("delete-older-than-days", "9999999")

        # Strip whitespaces from all parameters.
        for key, value in s3_parameters.items():
            if isinstance(value, str):
                s3_parameters[key] = value.strip()

        if not self.backup_manager.create_bucket(s3_parameters):
            self.charm._set_status(Status.BUCKET_NOT_CREATED)
            return

        self.charm.state.cluster.update({"s3-credentials": json.dumps(s3_parameters)})

    def _on_s3_credentials_gone(self, event: CredentialsGoneEvent):
        if not self.charm.unit.is_leader():
            return

        self.charm.state.cluster.update({"s3-credentials": ""})

    def _on_create_backup_action(self, event: ActionEvent):
        # TODO
        failure_conditions = [
            (not self.charm.unit.is_leader(), "Action must be ran on the application leader"),
            (
                not self.charm.state.stable,
                "Cluster must be stable before making a backup",
            ),
            (
                not self.charm.state.cluster.s3_credentials,
                "Cluster needs an access to an object storage to make a backup",
            ),
        ]

        for check, msg in failure_conditions:
            if check:
                logging.error(msg)
                event.set_results({"error": msg})
                event.fail(msg)
                return

        self.backup_manager.write_test_string(self.charm.state.cluster.s3_credentials)

    def _on_list_backups_action(self, _):
        # TODO
        pass

    def _on_restore_action(self, _):
        # TODO
        pass
