#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import json
import logging
import re
from pathlib import Path
from typing import cast
from unittest.mock import DEFAULT, Mock, PropertyMock, patch

import httpx
import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Container, Context, PeerRelation, Relation, State

from charm import ZooKeeperCharm
from core.models import ZKClient
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
def ctx() -> Context:
    ctx = Context(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS, unit_id=0)
    return ctx


def test_install_fails_create_passwords_until_peer_relation(
    ctx: Context, base_state: State
) -> None:
    # Given
    state_in = base_state

    # When
    with (
        patch("workload.ZKWorkload.install", return_value=True),
        ctx(ctx.on.install(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.cluster.internal_user_credentials


def test_install_create_passwords_succeeds(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={}, peers_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("workload.ZKWorkload.install", return_value=True),
        ctx(ctx.on.install(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert charm.state.cluster.relation_data
        assert charm.state.cluster.internal_user_credentials


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Snap not used on K8s charms")
def test_install_blocks_snap_install_failure(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("workload.ZKWorkload.install", return_value=False):
        state_out = ctx.run(ctx.on.install(), state_in)

    # Then
    assert isinstance(state_out.unit_status, BlockedStatus)


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="DNS managed by Kubernetes for K8s charms")
def test_install_sets_ip_hostname_fqdn(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("workload.ZKWorkload.install", return_value=True),
        ctx(ctx.on.install(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert charm.state.unit_server.ip
        assert charm.state.unit_server.hostname
        assert charm.state.unit_server.fqdn


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="DNS managed by Kubernetes for K8s charms")
def test_relation_changed_updates_ip_hostname_fqdn(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER, PEER, local_app_data={"ip": "gandalf-the-grey", "state": "started"}
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    with (
        patch(
            "managers.quorum.QuorumManager.get_hostname_mapping",
            return_value={"ip": "gandalf-the-white"},
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=False),
        patch("charm.ZooKeeperCharm.update_quorum"),
        ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager,
    ):
        manager.run()
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.unit_server.ip == "gandalf-the-white"


def test_relation_changed_defers_if_upgrading(
    ctx: Context, base_state: State, patched_idle
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])
    patched_idle.return_value = False

    # When
    with (
        patch("ops.framework.EventBase.defer") as patched_defer,
        patch("managers.config.ConfigManager.config_changed") as patched_config_changed,
    ):
        ctx.run(ctx.on.relation_changed(cluster_peer), state_in)

    # Then
    patched_defer.assert_called_once()
    patched_config_changed.assert_not_called()


def test_relation_changed_emitted_for_leader_elected(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed", autospec=True) as patched:
        ctx.run(ctx.on.leader_elected(), state_in)

    # Then
    patched.assert_called_once()


def test_relation_changed_emitted_for_config_changed(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed", autospec=True) as patched:
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched.assert_called_once()


def test_relation_changed_emitted_for_relation_changed(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed", autospec=True) as patched:
        ctx.run(ctx.on.relation_changed(cluster_peer), state_in)

    # Then
    patched.assert_called_once()


def test_relation_changed_emitted_for_relation_joined(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed", autospec=True) as patched:
        ctx.run(ctx.on.relation_joined(cluster_peer), state_in)

    # Then
    patched.assert_called_once()


@pytest.mark.skipif(SUBSTRATE == "vm", reason="Strategy is different on VM")
def test_relation_changed_emitted_for_relation_departed(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("charm.ZooKeeperCharm._on_cluster_relation_changed", autospec=True) as patched:
        ctx.run(ctx.on.relation_departed(cluster_peer), state_in)

    # Then
    patched.assert_called_once()


def test_relation_changed_starts_units(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("workload.ZKWorkload.alive", new_callable=PropertyMock, return_value=False),
        patch("charm.ZooKeeperCharm.init_server") as patched,
        patch("managers.config.ConfigManager.config_changed"),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched.assert_called_once()


def test_relation_changed_does_not_start_units_again(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER, PEER, local_app_data={}, local_unit_data={"state": "started"}
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_peer])

    # When
    with (
        patch("charm.ZooKeeperCharm.init_server") as patched,
        patch("managers.config.ConfigManager.config_changed"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_run_with_lock", autospec=True
        ),
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched.assert_not_called()


def test_relation_changed_does_not_restart_on_departing(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={}, local_unit_data={}, peers_data={})
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_peer])

    # When
    with patch(
        "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
    ) as patched:
        ctx.run(ctx.on.relation_departed(cluster_peer, remote_unit=0), state_in)

    # Then
    patched.assert_not_called()


def test_relation_changed_updates_quorum(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER, PEER, local_app_data={}, local_unit_data={"state": "started"}
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_peer])

    # When
    with (
        patch("charm.ZooKeeperCharm.update_quorum") as patched,
        patch("managers.config.ConfigManager.config_changed"),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched.assert_called_once()


def test_relation_changed_restarts(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER, PEER, local_app_data={}, local_unit_data={"state": "started"}
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_peer])

    # When
    with (
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ) as patched_restart,
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("managers.quorum.QuorumManager.update_cluster"),  # speedup test
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched_restart.assert_called_once()


def test_relation_changed_defers_switching_encryption_single_unit(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"switching-encryption": "started"},
        local_unit_data={"state": "started"},
        peers_data={},
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(
        base_state, relations=[cluster_peer, restart_peer], planned_units=1
    )

    # When
    with (
        patch("ops.framework.EventBase.defer") as patched,
        patch("managers.config.ConfigManager.config_changed"),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
        patch("managers.quorum.QuorumManager.update_cluster"),  # speedup test
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched.assert_called_once()


def test_relation_changed_checks_alive_and_healthy(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"state": "started"},
        peers_data={},
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_peer])

    # When
    with (
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
        patch("core.models.ZKServer.started", new_callable=PropertyMock, return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=False),
        patch(
            "workload.ZKWorkload.alive", new_callable=PropertyMock, return_value=True
        ) as patched_alive,
        patch(
            "workload.ZKWorkload.healthy", new_callable=PropertyMock, return_value=True
        ) as patched_healthy,
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched_alive.assert_called()
    patched_healthy.assert_called()


def test_restart_fails_not_related(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])
    mock_event = Mock()

    # When
    with (
        patch("workload.ZKWorkload.restart") as patched,
        patch("ops.framework.EventBase.defer"),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

    # Then
    patched.assert_not_called()


def test_restart_fails_not_started(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=1)
    mock_event = Mock()

    # When
    with (
        patch("workload.ZKWorkload.restart") as patched,
        patch("ops.framework.EventBase.defer"),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

    # Then
    patched.assert_not_called()


def test_restart_fails_not_added(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_unit_data={"state": "started"})
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(
        base_state, relations=[cluster_peer, restart_peer], planned_units=1
    )
    mock_event = Mock()

    # When
    with (
        patch("workload.ZKWorkload.restart") as patched,
        patch("ops.framework.EventBase.defer"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

    # Then
    patched.assert_not_called()


def test_restart_restarts_with_sleep(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER, PEER, local_unit_data={"state": "started"}, local_app_data={"0": "added"}
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(
        base_state, relations=[cluster_peer, restart_peer], planned_units=1
    )
    mock_event = Mock()

    # When
    with (
        patch("time.sleep") as patched_sleep,
        patch("workload.ZKWorkload.restart"),
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

    # Then
    patched_sleep.assert_called_once()


def test_restart_restarts_snap_sets_active_status(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"state": "started"},
        local_app_data={"0": "added"},
        peers_data={},
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(
        base_state, relations=[cluster_peer, restart_peer], planned_units=1
    )

    # When
    with (
        patch("workload.ZKWorkload.restart"),
        patch("workload.ZKWorkload.write"),
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch("time.sleep"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
    ):
        state_out = ctx.run(ctx.on.config_changed(), state_in)

    # Then
    assert state_out.unit_status == ActiveStatus()


def test_restart_sets_password_rotated_on_unit(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"state": "started"},
        local_app_data={"0": "added", "rotate-passwords": "true"},
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(
        base_state, relations=[cluster_peer, restart_peer], planned_units=1
    )
    mock_event = Mock()

    # When
    with (
        patch("workload.ZKWorkload.restart"),
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch("time.sleep"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

    # Then
    assert charm.state.unit_server.password_rotated


def test_restart_sets_unified(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"state": "started"},
        local_app_data={"switching-encryption": "started"},
    )
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(
        base_state,
        relations=[cluster_peer, restart_peer],
    )
    mock_event = Mock()

    # When
    with (
        patch("workload.ZKWorkload.restart"),
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch("time.sleep"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

        # Then
        assert charm.state.unit_server.unified

    cluster_peer = dataclasses.replace(cluster_peer, local_app_data={"switching-encryption": ""})
    state_in = dataclasses.replace(
        base_state,
        relations=[cluster_peer, restart_peer],
    )

    # When
    with (
        patch("workload.ZKWorkload.restart"),
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch("time.sleep"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

        # Then
        assert not charm.state.unit_server.unified


def test_init_server_waiting_if_no_passwords(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("charm.ZooKeeperCharm._set_status") as patched,
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.init_server()

    # Then
    assert patched.call_args_list[0].args[0] == Status.NO_PASSWORDS


def test_init_server_waiting_if_not_turn(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"sync-password": "mellon", "super-password": "mellon"},
        peers_data={},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=1)

    # When
    with (
        patch("charm.ZooKeeperCharm._set_status") as patched,
        patch("core.cluster.ClusterState.next_server", return_value=None),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.init_server()

    # Then
    assert patched.call_args_list[0].args[0] == Status.NOT_UNIT_TURN


def test_init_server_sets_blocked_if_not_alive(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={
            "sync-password": "mellon",
            "super-password": "mellon",
            "switching-encryption": "started",
            "quorum": "ssl",
        },
        local_unit_data={"ip": "aragorn", "fqdn": "legolas", "hostname": "gimli"},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("charm.ZooKeeperCharm._set_status") as patched,
        patch("managers.config.ConfigManager.set_zookeeper_myid"),
        patch("managers.config.ConfigManager.set_server_jvmflags"),
        patch("managers.config.ConfigManager.set_zookeeper_dynamic_properties"),
        patch("managers.config.ConfigManager.set_zookeeper_properties"),
        patch("managers.config.ConfigManager.set_jaas_config"),
        patch("managers.config.ConfigManager.set_client_jaas_config"),
        patch("workload.ZKWorkload.start"),
        patch("workload.ZKWorkload.alive", new_callable=PropertyMock, return_value=False),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.init_server()

    # Then
    assert not any(args[0] == Status.ACTIVE for args in patched.call_args_list)


def test_init_server_calls_necessary_methods(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={
            "tls": "enabled",
            "sync-password": "mellon",
            "super-password": "mellon",
            "switching-encryption": "started",
            "quorum": "ssl",
        },
        local_unit_data={
            "ip": "aragorn",
            "fqdn": "legolas",
            "hostname": "gimli",
            "ca-cert": "keep it secret",
            "certificate": "keep it safe",
        },
        peers_data={},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("managers.config.ConfigManager.set_zookeeper_myid") as zookeeper_myid,
        patch("managers.config.ConfigManager.set_server_jvmflags") as server_jvmflags,
        patch(
            "managers.config.ConfigManager.set_zookeeper_dynamic_properties"
        ) as zookeeper_dynamic_properties,
        patch("managers.config.ConfigManager.set_zookeeper_properties") as zookeeper_properties,
        patch("managers.config.ConfigManager.set_jaas_config") as zookeeper_jaas_config,
        patch(
            "managers.config.ConfigManager.set_client_jaas_config"
        ) as zookeeper_client_jaas_config,
        patch("managers.tls.TLSManager.set_private_key") as patched_private_key,
        patch("managers.tls.TLSManager.set_ca") as patched_ca,
        patch("managers.tls.TLSManager.set_chain") as patched_chain,
        patch("managers.tls.TLSManager.set_bundle") as patched_bundle,
        patch("managers.tls.TLSManager.set_certificate") as patched_certificate,
        patch("managers.tls.TLSManager.set_truststore") as patched_truststore,
        patch("managers.tls.TLSManager.set_p12_keystore") as patched_keystore,
        patch("workload.ZKWorkload.start") as start,
        patch("managers.tls.TLSManager.get_current_sans", return_value=""),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.init_server()

        # Then
        zookeeper_myid.assert_called_once()
        server_jvmflags.assert_called_once()
        zookeeper_dynamic_properties.assert_called_once()
        zookeeper_properties.assert_called_once()
        zookeeper_jaas_config.assert_called_once()
        zookeeper_client_jaas_config.assert_called_once()
        patched_private_key.assert_called_once()
        patched_ca.assert_called_once()
        patched_chain.assert_called_once()
        patched_bundle.assert_called_once()
        patched_certificate.assert_called_once()
        patched_truststore.assert_called_once()
        patched_keystore.assert_called_once()
        start.assert_called_once()

        assert charm.state.unit_server.quorum == "ssl"
        assert charm.state.unit_server.unified
        assert charm.state.unit_server.started


def test_adding_units_updates_relation_data(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"quorum": "ssl"},
        peers_data={1: {"quorum": "ssl"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("managers.quorum.QuorumManager.update_cluster", return_value={"1": "added"}),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
        ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert 1 in charm.state.cluster.quorum_unit_ids


def test_update_quorum_skips_relation_departed(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("managers.quorum.QuorumManager.update_cluster") as patched_update_cluster,
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        ctx.run(ctx.on.relation_departed(cluster_peer, departing_unit=0), state_in)

        # Then
        patched_update_cluster.assert_not_called()


@pytest.mark.skipif(SUBSTRATE == "vm", reason="Strategy exclusive to K8s")
def test_update_quorum_updates_cluster_for_relation_departed(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={1: {}})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("managers.quorum.QuorumManager.update_cluster") as patched_update_cluster,
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        ctx.run(ctx.on.relation_departed(cluster_peer, departing_unit=1), state_in)

    # Then
    patched_update_cluster.assert_called()


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Strategy exclusive to VM")
def test_relation_departed_removes_members(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_unit_data={}, peers_data={1: {}, 2: {}})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch.multiple(
        "charms.zookeeper.v0.client.ZooKeeperManager",
        get_leader=DEFAULT,
        remove_members=DEFAULT,
    ) as patched_manager:
        ctx.run(ctx.on.relation_departed(cluster_peer, departing_unit=2), state_in)

        assert patched_manager["remove_members"].call_count == 1


def test_update_quorum_updates_cluster_for_leader_elected(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={1: {}})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("managers.quorum.QuorumManager.update_cluster") as patched_update_cluster,
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("charm.ZooKeeperCharm.init_server"),
    ):
        ctx.run(ctx.on.leader_elected(), state_in)

    # Then
    patched_update_cluster.assert_called()


def test_update_quorum_adds_init_leader(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_unit_data={"state": "started"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("charm.ZooKeeperCharm.init_server"),
        patch("managers.quorum.QuorumManager.update_cluster"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock",
            autospec=True,
        ),
        ctx(ctx.on.leader_elected(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert charm.state.cluster.quorum_unit_ids


def test_update_quorum_does_not_set_ssl_quorum_until_unified(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"tls": "enabled"},
        local_unit_data={"unified": ""},
        peers_data={1: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.relation_joined(cluster_peer, remote_unit=1), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert not charm.state.cluster.quorum == "ssl"


def test_update_quorum_does_not_unset_upgrading_until_all_quorum(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"tls": "enabled", "switching-encryption": "started", "quorum": "non-ssl"},
        local_unit_data={"quorum": "non-ssl"},
        peers_data={1: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        manager.run()

        # Then
        assert not charm.state.cluster.quorum == "ssl"


def test_update_quorum_unsets_upgrading_when_all_quorum(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"tls": "enabled", "switching-encryption": "started", "quorum": "ssl"},
        local_unit_data={"quorum": "ssl"},
        peers_data={1: {"quorum": "ssl"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert charm.state.cluster.quorum == "ssl"


def test_config_changed_applies_relation_data(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("charm.ZooKeeperCharm.update_client_data", return_value=None) as patched,
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
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched.assert_called_once()


def test_config_changed_fails_apply_relation_data_not_ready(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("core.models.ZKClient.update") as patched_update,
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.NOT_ALL_QUORUM,
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched_update.assert_not_called()


def test_config_changed_fails_apply_relation_data_not_stable(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("charm.ZooKeeperCharm.update_client_data", return_value=None) as patched,
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.STALE_QUORUM,
        ),
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        ctx.run(ctx.on.config_changed(), state_in)

    # Then
    patched.assert_not_called()


def test_update_quorum_updates_relation_data(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    mock_event = Mock()

    # When
    with (
        patch("charm.ZooKeeperCharm.update_client_data", return_value=None) as patched,
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
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.update_quorum(mock_event)

    # Then
    patched.assert_called()


def test_update_quorum_fails_update_relation_data_if_not_stable(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    mock_event = Mock()

    # When
    with (
        patch("charm.ZooKeeperCharm.update_client_data", return_value=None) as patched,
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.STALE_QUORUM,
        ),
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.update_quorum(mock_event)

    # Then
    patched.assert_not_called()


def test_update_quorum_fails_update_relation_data_if_not_ready(
    ctx: Context, base_state: State
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])
    mock_event = Mock()

    # When
    with (
        patch("core.models.ZKClient.update") as patched_update,
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.NOT_ALL_QUORUM,
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.update_quorum(mock_event)

    # Then
    patched_update.assert_not_called()


def test_restart_defers_if_not_stable(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])
    mock_event = Mock()

    # When
    with (
        patch("charm.ZooKeeperCharm.update_client_data", return_value=None) as patched_apply,
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.STALE_QUORUM,
        ),
        patch(
            "core.cluster.ClusterState.ready", new_calable=PropertyMock, return_value=Status.ACTIVE
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

    # Then
    patched_apply.assert_not_called()
    mock_event.defer.assert_called_once()


def test_restart_fails_update_relation_data_if_not_ready(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])
    mock_event = Mock()

    # When
    with (
        patch("core.models.ZKClient.update") as patched_update,
        patch("workload.ZKWorkload.restart"),
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.NOT_ALL_QUORUM,
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

    # Then
    patched_update.assert_not_called()


def test_restart_fails_update_relation_data_if_not_idle(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])
    mock_event = Mock()

    # When
    with (
        patch("core.models.ZKClient.update") as patched_update,
        patch("workload.ZKWorkload.restart"),
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.NOT_ALL_QUORUM,
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm._restart(mock_event)

    # Then
    patched_update.assert_not_called()


def test_port_updates_if_tls(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"quorum": "ssl", "relation-0": "mellon", "tls": "enabled"},
        local_unit_data={"private-address": "treebeard", "state": "started"},
        peers_data={},
    )
    client_relation = Relation(REL_NAME, "application", remote_app_data={"database": "app"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("managers.quorum.QuorumManager.update_acls"),  # Speedup test
        ctx(ctx.on.relation_changed(client_relation), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        state_intermediary = manager.run()

        # Then
        uris = ""

        for client in charm.state.clients:
            assert client.tls == "enabled"
            uris = client.uris

    # Given
    cluster_peer = dataclasses.replace(
        cluster_peer,
        local_app_data={"quorum": "non-ssl", "relation-0": "mellon", "tls": ""},
        local_unit_data={"private-address": "treebeard", "state": "started", "quorum": "non-ssl"},
    )
    state_in = dataclasses.replace(state_intermediary, relations=[cluster_peer, client_relation])

    # When
    with (
        patch("managers.quorum.QuorumManager.update_acls"),  # Speedup test
        ctx(ctx.on.relation_changed(client_relation), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        for client in charm.state.clients:
            assert client.tls == "disabled"
            assert client.uris != uris


def test_update_relation_data(ctx: Context, base_state: State) -> None:
    # Given
    client_1_relation = Relation(
        REL_NAME,
        "application",
        remote_app_data={
            "database": "app",
            "requested-secrets": json.dumps(["username", "password"]),
        },
    )
    client_2_relation = Relation(
        REL_NAME,
        "new_application",
        remote_app_data={
            "database": "new_app",
            "extra-user-roles": "rw",
            "requested-secrets": json.dumps(["username", "password"]),
        },
    )
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={
            "ip": "treebeard",
            "state": "started",
            "private-address": "glamdring",
            "hostname": "frodo",
        },
        peers_data={
            1: {"ip": "shelob", "state": "ready", "private-address": "narsil", "hostname": "sam"},
            2: {
                "ip": "balrog",
                "state": "started",
                "private-address": "anduril",
                "hostname": "merry",
            },
        },
        local_app_data={
            f"relation-{client_1_relation.id}": "mellon",
            f"relation-{client_2_relation.id}": "friend",
        },
    )
    state_in = dataclasses.replace(
        base_state, relations=[cluster_peer, client_1_relation, client_2_relation]
    )

    # When
    with (
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "managers.config.ConfigManager.current_jaas",
            new_callable=PropertyMock,
            return_value=["mellon", "friend"],
        ),
        patch("managers.quorum.QuorumManager.update_acls"),  # Speedup test
        ctx(ctx.on.relation_changed(client_1_relation), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.update_client_data()

        # building bare clients for validation
        usernames = []
        passwords = []

        for relation in charm.state.client_relations:
            myclient = None
            for client in charm.state.clients:
                if client.relation == relation:
                    myclient = client
            client = ZKClient(
                relation=relation,
                data_interface=charm.state.client_provider_interface,
                substrate=SUBSTRATE,
                component=relation.app,
                local_app=charm.app,
                password=myclient.relation_data.get("password", ""),
                endpoints=myclient.relation_data.get("endpoints", ""),
                uris=myclient.relation_data.get("uris", ""),
                tls=myclient.relation_data.get("tls", ""),
            )

            assert client.username, client.password in charm.state.cluster.client_passwords.items()
            assert client.username not in usernames
            assert client.password not in passwords

            logger.info(client.endpoints)

            assert len(client.endpoints.split(",")) == 3
            assert len(client.uris.split(",")) == 3, client.uris

            if SUBSTRATE == "vm":
                # checking ips are used
                for ip in ["treebeard", "shelob", "balrog"]:
                    assert ip in client.endpoints
                    assert ip in client.uris

                # checking private-address or hostnames are NOT used
                for hostname_address in [
                    "glamdring",
                    "narsil",
                    "anduril",
                    "sam",
                    "frodo",
                    "merry",
                ]:
                    assert hostname_address not in client.endpoints
                    assert hostname_address not in client.uris

            if SUBSTRATE == "k8s":
                assert "endpoints" in client.endpoints
                assert "endpoints" in client.uris

            for uri in client.uris.split(","):
                # checking client_port in uri
                assert re.search(r":[\d]+", uri)

            assert client.uris.endswith(client.database)

            usernames.append(client.username)
            passwords.append(client.password)


@pytest.mark.nopatched_version
def test_workload_version_is_setted(ctx: Context, base_state: State, monkeypatch):
    # Given
    expected_version_installed = "3.8.1"
    expected_version_changed = "3.8.2"
    output_install = {
        "version": "3.8.1-ubuntu0-${mvngit.commit.id}, built on 2023-11-21 15:33 UTC"
    }
    output_changed = {
        "version": "3.8.2-ubuntu0-${mvngit.commit.id}, built on 2023-11-21 15:33 UTC"
    }
    response_mock = Mock()
    response_mock.return_value.json.side_effect = [output_install, output_changed]
    monkeypatch.setattr(
        httpx,
        "get",
        response_mock,
    )

    cluster_peer = PeerRelation(PEER, PEER)
    restart_peer = PeerRelation("restart", "rolling_op")
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_peer])

    # When
    with (
        patch("workload.ZKWorkload.install", return_value=True),
        patch("charm.ZooKeeperCharm.init_server"),
        patch("charm.ZooKeeperCharm.update_quorum"),
        patch("managers.config.ConfigManager.config_changed"),
        patch("core.cluster.ClusterState.all_units_related"),
        patch("core.cluster.ClusterState.all_units_declaring_ip"),
        patch("events.upgrade.ZKUpgradeEvents.idle", return_value=True),
    ):
        state_intermediary = ctx.run(ctx.on.install(), state_in)
        state_out = ctx.run(ctx.on.config_changed(), state_intermediary)

    # Then
    assert ctx.workload_version_history == [expected_version_installed]
    assert state_out.workload_version == expected_version_changed
