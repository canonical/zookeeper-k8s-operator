#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import json
import logging
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
import yaml
from ops.testing import ActionFailed
from scenario import Container, Context, PeerRelation, Relation, State

from charm import ZooKeeperCharm
from literals import (
    CONTAINER,
    PEER,
    S3_REL_NAME,
    SUBSTRATE,
    Status,
)

logger = logging.getLogger(__name__)


CONFIG = yaml.safe_load(Path("./config.yaml").read_text())
ACTIONS = yaml.safe_load(Path("./actions.yaml").read_text())
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


@pytest.fixture()
def charm_configuration():
    """Enable direct mutation on configuration dict."""
    return json.loads(json.dumps(CONFIG))


@pytest.fixture()
def base_state():

    if SUBSTRATE == "k8s":
        state = State(leader=True, containers=[Container(name=CONTAINER, can_connect=True)])

    else:
        state = State(leader=True)

    return state


@pytest.fixture()
def ctx() -> Context:
    ctx = Context(
        ZooKeeperCharm,
        meta=METADATA,
        config=CONFIG,
        actions=ACTIONS,
    )
    return ctx


def test_credentials_changed_not_leader_no_op(ctx: Context, base_state: State):
    # Given
    relation_s3 = Relation(
        interface="s3",
        endpoint=S3_REL_NAME,
        remote_app_name="s3",
        remote_app_data={"access-key": "speakfriend", "secret-key": "mellon", "bucket": "moria"},
    )
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, leader=False, relations=[relation_s3, cluster_peer])

    # When
    with patch("charms.data_platform_libs.v0.s3.S3Requirer") as patched_requirer:
        _ = ctx.run(ctx.on.relation_changed(relation_s3), state_in)

    # Then
    assert not patched_requirer.get_s3_connection_info.called


def test_credentials_changed_no_peers_defered(ctx: Context, base_state: State):
    # Given
    relation_s3 = Relation(
        interface="s3",
        endpoint=S3_REL_NAME,
        remote_app_name="s3",
        remote_app_data={"access-key": "speakfriend", "secret-key": "mellon", "bucket": "moria"},
    )
    state_in = dataclasses.replace(base_state, relations=[relation_s3])

    # When
    with (patch("charms.data_platform_libs.v0.s3.S3Requirer") as patched_requirer,):
        state_out = ctx.run(ctx.on.relation_changed(relation_s3), state_in)

    # Then
    assert state_out.unit_status == Status.NO_PEER_RELATION.value.status
    assert not patched_requirer.get_s3_connection_info.called
    assert len(state_out.deferred) == 1
    assert state_out.deferred[0].name == "credentials_changed"


def test_missing_config_status_blocked(ctx: Context, base_state: State):
    # Given
    relation_s3 = Relation(
        interface="s3",
        endpoint=S3_REL_NAME,
        remote_app_name="s3",
        # missing mandatory 'bucket'
        remote_app_data={"access-key": "speakfriend", "secret-key": "mellon"},
    )
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, relation_s3])

    # When
    state_out = ctx.run(ctx.on.relation_changed(relation_s3), state_in)

    # Then
    assert state_out.unit_status == Status.MISSING_S3_CONFIG.value.status


def test_bucket_not_created_status_blocked(ctx: Context, base_state: State):
    # Given
    relation_s3 = Relation(
        interface="s3",
        endpoint=S3_REL_NAME,
        remote_app_name="s3",
        remote_app_data={"access-key": "speakfriend", "secret-key": "mellon", "bucket": "moria"},
    )
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, relation_s3])

    # When
    with patch("managers.backup.BackupManager.create_bucket", return_value=False):
        state_out = ctx.run(ctx.on.relation_changed(relation_s3), state_in)

    # Then
    assert state_out.unit_status == Status.BUCKET_NOT_CREATED.value.status


def test_bucket_created_bag_written(ctx: Context, base_state: State):
    # Given
    relation_s3 = Relation(
        interface="s3",
        endpoint=S3_REL_NAME,
        remote_app_name="s3",
        remote_app_data={"access-key": "speakfriend", "secret-key": "mellon", "bucket": "moria"},
    )
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, relation_s3])

    # When
    with (
        patch("managers.backup.BackupManager.create_bucket", return_value=True),
        patch("core.models.ZKCluster.update") as patched_state,
    ):
        _ = ctx.run(ctx.on.relation_changed(relation_s3), state_in)

    # Then
    assert patched_state.called
    assert "speakfriend" in patched_state.call_args[0][0].get("s3-credentials", "")


def test_action_create_backup_not_leader(ctx: Context, base_state: State):
    # Given
    state_in = dataclasses.replace(base_state, leader=False)

    # When
    # Then
    with pytest.raises(ActionFailed) as exc_info:
        _ = ctx.run(ctx.on.action("create-backup"), state_in)

    assert exc_info.value.message == "Action must be ran on the application leader"


def test_action_create_backup_unstable(ctx: Context, base_state: State):
    # Given
    state_in = base_state

    # When
    # Then
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=False),
        pytest.raises(ActionFailed) as exc_info,
    ):
        _ = ctx.run(ctx.on.action("create-backup"), state_in)

    assert exc_info.value.message == "Cluster must be stable before making a backup"


def test_action_create_backup_no_creds(ctx: Context, base_state: State):
    # Given
    state_in = base_state

    # When
    # Then
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        pytest.raises(ActionFailed) as exc_info,
    ):
        _ = ctx.run(ctx.on.action("create-backup"), state_in)

    assert (
        exc_info.value.message == "Cluster needs an access to an object storage to make a backup"
    )


def test_action_list_backups_not_leader(ctx: Context, base_state: State):
    # Given
    state_in = dataclasses.replace(base_state, leader=False)

    # When
    # Then
    with pytest.raises(ActionFailed) as exc_info:
        _ = ctx.run(ctx.on.action("list-backups"), state_in)

    assert exc_info.value.message == "Action must be ran on the application leader"


def test_action_list_backups_no_creds(ctx: Context, base_state: State):
    # Given
    state_in = base_state

    # When
    # Then
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        pytest.raises(ActionFailed) as exc_info,
    ):
        _ = ctx.run(ctx.on.action("list-backups"), state_in)

    assert (
        exc_info.value.message == "Cluster needs an access to an object storage to make a backup"
    )


def test_action_restore_not_leader(ctx: Context, base_state: State):
    # Given
    state_in = dataclasses.replace(base_state, leader=False)

    # When
    # Then
    with pytest.raises(ActionFailed) as exc_info:
        _ = ctx.run(ctx.on.action("restore"), state_in)

    assert exc_info.value.message == "Action must be ran on the application leader"


def test_action_restore_no_creds(ctx: Context, base_state: State):
    # Given
    state_in = base_state

    # When
    # Then
    with pytest.raises(ActionFailed) as exc_info:
        _ = ctx.run(ctx.on.action("restore"), state_in)

    assert (
        exc_info.value.message == "Cluster needs an access to an object storage to make a backup"
    )


def test_action_restore_no_id_param(ctx: Context, base_state: State):
    # Given
    s3_params = {
        "access-key": "speakfriend",
        "secret-key": "mellon",
        "bucket": "moria",
        "region": "",
    }
    state_in = base_state

    # When
    # Then
    with (
        pytest.raises(ActionFailed) as exc_info,
        patch("core.models.ZKCluster.s3_credentials", new_callable=PropertyMock, value=s3_params),
        patch("managers.backup.BackupManager.is_snapshot_in_bucket", side_effect=[False]),
    ):
        _ = ctx.run(ctx.on.action("restore"), state_in)

    assert exc_info.value.message == "No backup id to restore provided"


def test_action_restore_snapshot_not_found(ctx: Context, base_state: State):
    # Given
    s3_params = {
        "access-key": "speakfriend",
        "secret-key": "mellon",
        "bucket": "moria",
        "region": "",
    }
    state_in = base_state

    # When
    # Then
    with (
        pytest.raises(ActionFailed) as exc_info,
        patch("core.models.ZKCluster.s3_credentials", new_callable=PropertyMock, value=s3_params),
        patch("managers.backup.BackupManager.is_snapshot_in_bucket", side_effect=[False]),
    ):
        _ = ctx.run(ctx.on.action("restore", params={"backup-id": "notfound"}), state_in)

    assert exc_info.value.message == "Backup id not found in storage object"


def test_action_restore_ongoing_restore(ctx: Context, base_state: State):
    # Given
    s3_params = {
        "access-key": "speakfriend",
        "secret-key": "mellon",
        "bucket": "moria",
        "region": "",
    }
    restore_peer = PeerRelation(PEER, PEER, local_app_data={"id-to-restore": "ongoing-backup-id"})
    state_in = dataclasses.replace(base_state, relations=[restore_peer])

    # When
    # Then
    with (
        pytest.raises(ActionFailed) as exc_info,
        patch("core.models.ZKCluster.s3_credentials", new_callable=PropertyMock, value=s3_params),
        patch("managers.backup.BackupManager.is_snapshot_in_bucket", side_effect=[True]),
    ):
        _ = ctx.run(ctx.on.action("restore", params={"backup-id": "new-backup-id"}), state_in)

    assert exc_info.value.message == "A snapshot restore is currently ongoing"
