#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event handlers for creating and restoring backups."""
import json
import logging
from typing import TYPE_CHECKING, cast

from charms.data_platform_libs.v0.s3 import (
    CredentialsChangedEvent,
    CredentialsGoneEvent,
    S3Requirer,
)
from ops import (
    ActionEvent,
    RelationEvent,
)
from ops.framework import Object
from tenacity import retry, retry_if_result, stop_after_attempt, wait_fixed

from core.stubs import RestoreStep, S3ConnectionInfo
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

        self.framework.observe(
            getattr(self.charm.on, "cluster_relation_changed"), self._restore_event_dispatch
        )

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

        s3_parameters.setdefault("endpoint", "https://s3.amazonaws.com")
        s3_parameters.setdefault("region", "")
        s3_parameters.setdefault("path", S3_BACKUPS_PATH)

        for key, value in s3_parameters.items():
            if isinstance(value, str):
                s3_parameters[key] = value.strip()

        s3_parameters = cast(S3ConnectionInfo, s3_parameters)
        if not self.backup_manager.create_bucket(s3_parameters):
            self.charm._set_status(Status.BUCKET_NOT_CREATED)
            return

        self.charm.state.cluster.update({"s3-credentials": json.dumps(s3_parameters)})

    def _on_s3_credentials_gone(self, event: CredentialsGoneEvent):
        if not self.charm.unit.is_leader():
            return

        self.charm.state.cluster.update({"s3-credentials": ""})

    def _on_create_backup_action(self, event: ActionEvent):
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

        backup_metadata = self.backup_manager.create_backup()

        output = self.backup_manager.format_backups_table(
            [backup_metadata], title="Backup created"
        )
        event.log(output)

    def _on_list_backups_action(self, event: ActionEvent):
        failure_conditions = [
            (not self.charm.unit.is_leader(), "Action must be ran on the application leader"),
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

        backups_metadata = self.backup_manager.list_backups()
        output = self.backup_manager.format_backups_table(backups_metadata)
        event.log(output)
        event.set_results({"backups": json.dumps(backups_metadata)})

    def _on_restore_action(self, event: ActionEvent):
        """Restore a snapshot referenced by its id.

        Steps:
        - stop client traffic
        - stop all units
        - backup local state so that we can rollback if anything goes wrong (manual op)
        - wipe data folders
        - get snapshot from object storage, save in data folder
        - restart units
        - cleanup leftover files
        - notify clients
        """
        id_to_restore = event.params.get("backup-id", "")
        failure_conditions = [
            (
                lambda: not self.charm.unit.is_leader(),
                "Action must be ran on the application leader",
            ),
            (
                lambda: not self.charm.state.cluster.s3_credentials,
                "Cluster needs an access to an object storage to make a backup",
            ),
            (
                lambda: not id_to_restore,
                "No backup id to restore provided",
            ),
            (
                lambda: not self.backup_manager.is_snapshot_in_bucket(id_to_restore),
                "Backup id not found in storage object",
            ),
            (
                lambda: bool(self.charm.state.cluster.is_restore_in_progress),
                "A snapshot restore is currently ongoing",
            ),
        ]

        for check, msg in failure_conditions:
            if check():
                logging.error(msg)
                event.set_results({"error": msg})
                event.fail(msg)
                return

        self.charm.state.cluster.update(
            {
                "id-to-restore": id_to_restore,
                "restore-instruction": RestoreStep.NOT_STARTED.value,
            }
        )
        self.charm.disconnect_clients()

        event.log(f"Beginning restore flow for snapshot {id_to_restore}")

    def _restore_event_dispatch(self, event: RelationEvent):
        """Dispatch restore event to the proper method."""
        if not self.charm.state.cluster.is_restore_in_progress:
            if self.charm.state.unit_server.restore_progress is not RestoreStep.NOT_STARTED:
                self.charm.state.unit_server.update(
                    {"restore-progress": RestoreStep.NOT_STARTED.value}
                )
                self.charm._set_status(self.charm.state.ready)
            return

        if self.charm.unit.is_leader():
            self._maybe_progress_step()

        match self.charm.state.cluster.restore_instruction, self.charm.state.unit_server.restore_progress:
            case RestoreStep.STOP_WORKFLOW, RestoreStep.NOT_STARTED:
                self._stop_workflow()
            case RestoreStep.RESTORE, RestoreStep.STOP_WORKFLOW:
                self._download_and_restore()
            case RestoreStep.RESTART, RestoreStep.RESTORE:
                self._restart_workflow()
            case RestoreStep.CLEAN, RestoreStep.RESTART:
                self._cleaning()
            case _:
                pass

    def _maybe_progress_step(self):
        """Check that all units are done with the current instruction and move to the next if applicable."""
        current_instruction = self.charm.state.cluster.restore_instruction
        next_instruction = current_instruction.next_step()

        if self.charm.state.is_next_restore_step_possible:
            payload = {"restore-instruction": next_instruction.value}
            if current_instruction is RestoreStep.CLEAN:
                payload = payload | {"id-to-restore": "", "to_restore": ""}
                # Update ACLs for already related clients and trigger a relation-changed
                # on their side to enable them to reconnect.
                self.charm.update_client_data()
                self.charm.quorum_manager.update_acls()

            self.charm.state.cluster.update(payload)

    def _stop_workflow(self) -> None:
        self.charm._set_status(Status.ONGOING_RESTORE)
        logger.info("Restoring - stopping workflow")
        self.charm.workload.stop()
        self.charm.state.unit_server.update({"restore-progress": RestoreStep.STOP_WORKFLOW.value})

    def _download_and_restore(self) -> None:
        logger.info("Restoring - restore snapshot")
        self.backup_manager.restore_snapshot(
            self.charm.state.cluster.id_to_restore, self.charm.workload
        )
        self.charm.state.unit_server.update({"restore-progress": RestoreStep.RESTORE.value})

    def _restart_workflow(self) -> None:
        logger.info("Restoring - restarting workflow")
        self.charm.workload.restart()
        self.charm.state.unit_server.update({"restore-progress": RestoreStep.RESTART.value})

    @retry(
        wait=wait_fixed(5),
        stop=stop_after_attempt(3),
        retry=retry_if_result(lambda res: res is False),
    )
    def _cleaning(self) -> bool | None:
        if not self.charm.workload.healthy:
            return False
        logger.info("Restoring - cleaning files")
        self.backup_manager.cleanup_leftover_files(self.charm.workload)
        self.charm.state.unit_server.update({"restore-progress": RestoreStep.CLEAN.value})
