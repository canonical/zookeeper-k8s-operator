#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
import yaml
from scenario import Container, Context, PeerRelation, Relation, State

from charm import ZooKeeperCharm
from literals import (
    CONTAINER,
    PEER,
    REL_NAME,
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


def test_client_relation_updated_create_passwords_with_chroot(ctx: Context, base_state: State):
    # Given
    requested = "lotr"
    pwd = "speakfriend"

    relation_client = Relation(
        interface=REL_NAME,
        endpoint=REL_NAME,
        remote_app_name="app",
        remote_app_data={
            "database": requested,
            "requested-secrets": '["username","password","tls","tls-ca","uris"]',
        },
    )

    cluster_peer = PeerRelation(PEER, PEER, local_app_data={str(i): "added" for i in range(4)})
    state_in = base_state.replace(relations=[cluster_peer, relation_client])

    # When
    with (
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch("workload.ZKWorkload.generate_password", return_value=pwd),
        patch(
            "managers.config.ConfigManager.current_jaas",
            new_callable=PropertyMock,
            return_value=[pwd],
        ),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
    ):
        state_intermediary = ctx.run(relation_client.changed_event, state_in)
        state_out = ctx.run(cluster_peer.changed_event, state_intermediary)

    # Then
    databag = state_out.relations[1].local_app_data
    assert databag.get("database", "") == f"/{requested}"
    assert "secret-user" in databag
    assert "secret-tls" in databag
    assert "endpoints" in databag
