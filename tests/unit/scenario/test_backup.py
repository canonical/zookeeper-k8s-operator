#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from unittest.mock import patch

from scenario import Context, PeerRelation, Relation, State

from literals import (
    PEER,
    S3_REL_NAME,
    Status,
)

logger = logging.getLogger(__name__)


def test_credentials_changed_not_leader_no_op(ctx: Context, base_state: State):
    # Given
    relation_s3 = Relation(
        interface="s3",
        endpoint=S3_REL_NAME,
        remote_app_name="s3",
        remote_app_data={"access-key": "speakfriend", "secret-key": "mellon", "bucket": "moria"},
    )
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = base_state.replace(leader=False, relations=[relation_s3, cluster_peer])

    # When
    with patch("charms.data_platform_libs.v0.s3.S3Requirer") as patched_requirer:
        _ = ctx.run(relation_s3.changed_event, state_in)

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
    state_in = base_state.replace(relations=[relation_s3])

    # When
    with (patch("charms.data_platform_libs.v0.s3.S3Requirer") as patched_requirer,):
        state_out = ctx.run(relation_s3.changed_event, state_in)

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
    state_in = base_state.replace(relations=[cluster_peer, relation_s3])

    # When
    state_out = ctx.run(relation_s3.changed_event, state_in)

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
    state_in = base_state.replace(relations=[cluster_peer, relation_s3])

    # When
    with patch("managers.backup.BackupManager.create_bucket", return_value=False):
        state_out = ctx.run(relation_s3.changed_event, state_in)

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
    state_in = base_state.replace(relations=[cluster_peer, relation_s3])

    # When
    with (
        patch("managers.backup.BackupManager.create_bucket", return_value=True),
        patch("core.models.ZKCluster.update") as patched_state,
    ):
        _ = ctx.run(relation_s3.changed_event, state_in)

    # Then
    assert patched_state.called
    assert "speakfriend" in patched_state.call_args[0][0].get("s3-credentials", "")
