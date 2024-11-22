#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import dataclasses
import json
import logging
from pathlib import Path
from typing import cast
from unittest.mock import PropertyMock, patch

import pytest
import yaml
from ops import MaintenanceStatus, RelationBrokenEvent
from ops.testing import Container, Context, PeerRelation, Relation, State

from charm import ZooKeeperCharm
from literals import CONTAINER, PEER, REL_NAME, SUBSTRATE, Status

logger = logging.getLogger(__name__)

CONFIG = yaml.safe_load(Path("./config.yaml").read_text())
ACTIONS = yaml.safe_load(Path("./actions.yaml").read_text())
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


@pytest.fixture()
def base_state():

    if SUBSTRATE == "k8s":
        state = State(leader=True, containers=[Container(name=CONTAINER, can_connect=True)])

    else:
        state = State(leader=True)

    return state


@pytest.fixture()
def charm_configuration():
    """Enable direct mutation on configuration dict."""
    return json.loads(json.dumps(CONFIG))


@pytest.fixture()
def ctx() -> Context:
    ctx = Context(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS, unit_id=0)
    return ctx


def test_client_relation_updated_defers_if_not_stable_leader(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    client_relation = Relation(REL_NAME, "application", remote_app_data={"database": "balrog"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=False),
        patch("ops.framework.EventBase.defer") as patched_defer,
        patch("managers.quorum.QuorumManager.update_acls") as patched_acls,
    ):
        ctx.run(ctx.on.relation_changed(client_relation), state_in)

        # Then
        patched_acls.assert_not_called()
        patched_defer.assert_called()


def test_client_relation_updated_succeeds(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    client_relation = Relation(REL_NAME, "application", remote_app_data={"database": "balrog"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("ops.framework.EventBase.defer") as patched_defer,
        patch("managers.quorum.QuorumManager.update_acls") as patched_acls,
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
    ):
        ctx.run(ctx.on.relation_changed(client_relation), state_in)

        # Then
        patched_acls.assert_called()
        patched_defer.assert_not_called()


def test_client_relation_updated_creates_passwords_with_chroot(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    client_relation = Relation(REL_NAME, "application")
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
        ctx(ctx.on.relation_created(client_relation), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert not charm.state.cluster.client_passwords

    client_relation = dataclasses.replace(client_relation, remote_app_data={"ungoliant": "spider"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
        ctx(ctx.on.relation_changed(client_relation), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert not charm.state.cluster.client_passwords

    client_relation = dataclasses.replace(client_relation, remote_app_data={"database": "balrog"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
        ctx(ctx.on.relation_changed(client_relation), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert charm.state.cluster.client_passwords


def test_client_relation_broken_sets_acls_with_broken_events(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    client_relation = Relation(REL_NAME, "application", remote_app_data={"database": "balrog"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls") as patched_update_acls,
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
    ):
        ctx.run(ctx.on.relation_changed(client_relation), state_in)

    # Then
    patched_update_acls.assert_called_with(event=None)

    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls") as patched_update_acls,
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
    ):
        ctx.run(ctx.on.relation_broken(client_relation), state_in)

    # Then
    isinstance(patched_update_acls.call_args_list[0], RelationBrokenEvent)


def test_client_relation_broken_removes_passwords(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    client_relation = Relation(REL_NAME, "application", remote_app_data={"database": "balrog"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
        ctx(ctx.on.relation_changed(client_relation), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        state_out = manager.run()

        # Then
        assert charm.state.cluster.client_passwords

    # When
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
        ctx(ctx.on.relation_broken(client_relation), state_out) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        state_out = manager.run()

        # Then
        assert not charm.state.cluster.client_passwords


def test_expose_external_service_down_disconnect_clients(
    charm_configuration: dict, base_state: State
) -> None:
    # Given
    charm_configuration["options"]["expose-external"]["default"] = "nodeport"
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        peers_data={},
        local_unit_data={"state": "started"},
        local_app_data={
            "sync-password": "mellon",
            "super-password": "mellon",
        },
    )
    client_relation = Relation(
        REL_NAME,
        "application",
        remote_app_data={"database": "balrog"},
        local_app_data={"endpoints": "9.9.9.9:2181"},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])
    ctx = Context(
        ZooKeeperCharm, meta=METADATA, config=charm_configuration, actions=ACTIONS, unit_id=0
    )

    # When
    with (
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "core.cluster.ClusterState.endpoints_external",
            new_callable=PropertyMock,
            return_value="",
        ),
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    assert not state_out.get_relation(client_relation.id).local_app_data.get("endpoints", "")
    assert isinstance(state_out.unit_status, MaintenanceStatus)
