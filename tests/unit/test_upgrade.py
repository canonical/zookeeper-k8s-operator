#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import logging
from pathlib import Path
from typing import cast
from unittest.mock import PropertyMock, patch

import pytest
import yaml
from charms.data_platform_libs.v0.upgrade import ClusterNotReadyError, DependencyModel, EventBase
from charms.zookeeper.v0.client import ZooKeeperManager
from kazoo.client import KazooClient
from ops.testing import Container, Context, PeerRelation, State
from tenacity import RetryError

from charm import ZooKeeperCharm
from core.cluster import ClusterState
from core.models import ZKServer
from events.upgrade import ZKUpgradeEvents, ZooKeeperDependencyModel
from literals import CHARM_KEY, CONTAINER, DEPENDENCIES, PEER, SUBSTRATE
from managers.config import ConfigManager
from workload import ZKWorkload

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def patched_client(mocker):
    mocker.patch.object(ZooKeeperManager, "get_leader", return_value="000.000.000")
    mocker.patch.object(KazooClient, "start")


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


# @pytest.fixture
# def harness():
#     harness = Harness(ZooKeeperCharm, meta=str(METADATA), config=str(CONFIG), actions=str(ACTIONS))
#     harness.add_relation("cluster", CHARM_KEY)
#     harness.add_relation("restart", CHARM_KEY)
#     harness.add_relation("upgrade", CHARM_KEY)
#     harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
#     harness.begin()
#     with harness.hooks_disabled():
#         harness.update_relation_data(
#             harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"hostname": "000.000.000"}
#         )

#     return harness


def test_pre_upgrade_check_raises_not_all_members_broadcasting(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(
        ZooKeeperManager, "members_broadcasting", new_callable=PropertyMock, return_value=False
    )

    # When
    with pytest.raises(ClusterNotReadyError), ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events.pre_upgrade_check()


def test_pre_upgrade_check_raises_not_all_units_members(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(
        ZooKeeperManager, "members_broadcasting", new_callable=PropertyMock, return_value=True
    )
    mocker.patch.object(
        ZooKeeperManager, "server_members", new_callable=PropertyMock, return_value=[0, 1]
    )

    # When
    with pytest.raises(ClusterNotReadyError), ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events.pre_upgrade_check()


def test_pre_upgrade_check_raises_members_syncing(ctx: Context, base_state: State, mocker) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(
        ZooKeeperManager, "members_broadcasting", new_callable=PropertyMock, return_value=True
    )
    mocker.patch.object(
        ZooKeeperManager, "server_members", new_callable=PropertyMock, return_value=[0]
    )
    mocker.patch.object(
        ZooKeeperManager, "members_syncing", new_callable=PropertyMock, return_value=True
    )

    # When
    with pytest.raises(ClusterNotReadyError), ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events.pre_upgrade_check()


def test_pre_upgrade_check_raises_not_stable(ctx: Context, base_state: State, mocker) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(
        ZooKeeperManager, "members_broadcasting", new_callable=PropertyMock, return_value=True
    )
    mocker.patch.object(
        ZooKeeperManager, "server_members", new_callable=PropertyMock, return_value=[0]
    )
    mocker.patch.object(
        ZooKeeperManager, "members_syncing", new_callable=PropertyMock, return_value=False
    )
    mocker.patch.object(ClusterState, "stable", new_callable=PropertyMock, return_value=False)

    # When
    with pytest.raises(ClusterNotReadyError), ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events.pre_upgrade_check()


def test_pre_upgrade_check_raises_leader_not_found(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(
        ZooKeeperManager, "members_broadcasting", new_callable=PropertyMock, return_value=True
    )
    mocker.patch.object(
        ZooKeeperManager, "server_members", new_callable=PropertyMock, return_value=[0]
    )
    mocker.patch.object(
        ZooKeeperManager, "members_syncing", new_callable=PropertyMock, return_value=False
    )
    mocker.patch.object(ClusterState, "stable", new_callable=PropertyMock, return_value=True)
    mocker.patch.object(ZooKeeperManager, "get_leader", side_effect=RetryError)

    # When
    with pytest.raises(ClusterNotReadyError), ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events.pre_upgrade_check()


def test_pre_upgrade_check_succeeds(ctx: Context, base_state: State, mocker) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(
        ZooKeeperManager, "members_broadcasting", new_callable=PropertyMock, return_value=True
    )
    mocker.patch.object(
        ZooKeeperManager, "server_members", new_callable=PropertyMock, return_value=[0]
    )
    mocker.patch.object(
        ZooKeeperManager, "members_syncing", new_callable=PropertyMock, return_value=False
    )
    mocker.patch.object(ClusterState, "stable", new_callable=PropertyMock, return_value=True)

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events.pre_upgrade_check()


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Upgrade stack not built on K8s charms")
def test_build_upgrade_stack(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"hostname": "000.000.000"},
        peers_data={
            1: {"hostname": "111.111.111"},
            2: {"hostname": "222.222.222"},
            3: {"hostname": "333.333.333"},
        },
    )
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        stack = charm.upgrade_events.build_upgrade_stack()

    # Then
    assert stack[0] == 0
    assert len(stack) == 4


@pytest.mark.nopatched_idle
@pytest.mark.parametrize("upgrade_stack", ([], [0]))
def test_run_password_rotation_while_upgrading(
    ctx: Context, base_state: State, mocker, upgrade_stack
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY, local_app_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mock_event = mocker.MagicMock()
    mock_event.params = {"username": "super"}

    # When
    with (
        mocker.patch.object(ConfigManager, "set_zookeeper_myid"),
        mocker.patch.object(ConfigManager, "set_server_jvmflags"),
        mocker.patch.object(ConfigManager, "set_zookeeper_dynamic_properties"),
        mocker.patch.object(ConfigManager, "set_zookeeper_properties"),
        mocker.patch.object(ConfigManager, "set_jaas_config"),
        mocker.patch.object(ConfigManager, "set_client_jaas_config"),
        patch("charm.ZooKeeperCharm.update_quorum"),
        patch(
            "events.upgrade.ZKUpgradeEvents.upgrade_stack",
            new_callable=PropertyMock,
            return_value=upgrade_stack,
        ),
        ctx(ctx.on.start(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.password_action_events._set_password_action(mock_event)

    # Then
    if not upgrade_stack:
        mock_event.set_results.assert_called()
    else:
        mock_event.fail.assert_called_with(
            f"Cannot set password while upgrading (upgrade_stack: {upgrade_stack})"
        )


def test_zookeeper_dependency_model():
    assert sorted(ZooKeeperDependencyModel.__fields__.keys()) == sorted(DEPENDENCIES.keys())

    for value in DEPENDENCIES.values():
        assert DependencyModel(**value)


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Upgrade granted not used on K8s charms")
def test_upgrade_granted_sets_failed_if_failed_snap(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(ZKWorkload, "stop")
    mocker.patch.object(ZKWorkload, "restart")
    mocker.patch.object(ZKWorkload, "install", return_value=False)
    mocker.patch.object(ZKUpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(ZKUpgradeEvents, "set_unit_completed")
    mocker.patch.object(ZKUpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events._on_upgrade_granted(mock_event)

    # Then
    ZKWorkload.stop.assert_called_once()
    ZKWorkload.install.assert_called_once()
    ZKWorkload.restart.assert_not_called()
    ZKUpgradeEvents.set_unit_completed.assert_not_called()
    ZKUpgradeEvents.set_unit_failed.assert_called_once()


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Upgrade granted not used on K8s charms")
def test_upgrade_granted_sets_failed_if_failed_upgrade_check(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(ZKWorkload, "stop")
    mocker.patch.object(ZKWorkload, "restart")
    mocker.patch.object(ZKWorkload, "install", return_value=True)
    mocker.patch.object(ZKUpgradeEvents, "set_unit_completed")
    mocker.patch.object(ZKUpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events._on_upgrade_granted(mock_event)

    # Then
    ZKWorkload.stop.assert_called_once()
    ZKWorkload.install.assert_called_once()
    ZKUpgradeEvents.set_unit_completed.assert_not_called()
    ZKUpgradeEvents.set_unit_failed.assert_called_once()


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Upgrade granted not used on K8s charms")
def test_upgrade_granted_succeeds(ctx: Context, base_state: State, mocker) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(ZKWorkload, "stop")
    mocker.patch.object(ZKWorkload, "restart")
    mocker.patch.object(ZKWorkload, "install")
    mocker.patch.object(ZKUpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(ZKUpgradeEvents, "set_unit_completed")
    mocker.patch.object(ZKUpgradeEvents, "set_unit_failed")
    mocker.patch.object(ZKUpgradeEvents, "apply_backwards_compatibility_fixes")

    mock_event = mocker.MagicMock()

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events._on_upgrade_granted(mock_event)

    # Then
    ZKWorkload.stop.assert_called_once()
    ZKWorkload.install.assert_called_once()
    ZKUpgradeEvents.apply_backwards_compatibility_fixes.assert_called_once()
    ZKWorkload.restart.assert_called_once()
    ZKUpgradeEvents.set_unit_completed.assert_called_once()
    ZKUpgradeEvents.set_unit_failed.assert_not_called()


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Upgrade granted not used on K8s charms")
def test_upgrade_granted_recurses_upgrade_changed_on_leader(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(
        base_state, relations=[cluster_peer, restart_relation], leader=False
    )
    mocker.patch.object(ZKWorkload, "stop")
    mocker.patch.object(ZKWorkload, "restart")
    mocker.patch.object(ZKWorkload, "install")
    mocker.patch.object(ZKUpgradeEvents, "pre_upgrade_check")
    mocker.patch.object(ZKUpgradeEvents, "on_upgrade_changed", autospec=True)

    mock_event = mocker.MagicMock()

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events._on_upgrade_granted(mock_event)

    # Then
    ZKUpgradeEvents.on_upgrade_changed.assert_not_called()

    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events._on_upgrade_granted(mock_event)

    # Then
    ZKUpgradeEvents.on_upgrade_changed.assert_called_once()


@pytest.mark.skipif(SUBSTRATE == "vm", reason="No rolling partition on VM charms")
def test_pre_upgrade_check_sets_partition_if_idle(ctx: Context, base_state: State, mocker) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(ZKUpgradeEvents, "idle", new_callable=PropertyMock, return_value=True)

    # When
    with pytest.raises(ClusterNotReadyError), ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events.pre_upgrade_check()

    # Then
    ZKUpgradeEvents._set_rolling_update_partition.assert_called_once()


@pytest.mark.skipif(SUBSTRATE == "vm", reason="No rolling partition on VM charms")
def test_pre_upgrade_check_skips_partition_if_not_idle(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(ZKUpgradeEvents, "idle", new_callable=PropertyMock, return_value=False)

    # When
    with pytest.raises(ClusterNotReadyError), ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events.pre_upgrade_check()

    # Then
    ZKUpgradeEvents._set_rolling_update_partition.assert_not_called()


@pytest.mark.skipif(SUBSTRATE == "vm", reason="No pebble on VM charms")
def test_zookeeper_pebble_ready_upgrade_does_not_defer_for_dead_service(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(ZKWorkload, "alive", new_callable=PropertyMock, return_value=False)
    mocker.patch.object(EventBase, "defer")

    mock_event = mocker.MagicMock()

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events._on_zookeeper_pebble_ready_upgrade(mock_event)

    # Then
    mock_event.defer.assert_not_called()


@pytest.mark.skipif(SUBSTRATE == "vm", reason="No pebble on VM charms")
def test_zookeeper_pebble_ready_upgrade_reinits_and_sets_failed(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(ZKServer, "started", new_callable=PropertyMock, return_value=True)
    mocker.patch.object(ZKUpgradeEvents, "idle", new_callable=PropertyMock, return_value=False)
    mocker.patch.object(ZKWorkload, "alive", new_callable=PropertyMock, return_value=True)
    mocker.patch.object(ZKWorkload, "healthy", new_callable=PropertyMock, return_value=False)
    mocker.patch.object(ZKUpgradeEvents, "set_unit_failed")

    mock_event = mocker.MagicMock()

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events._on_zookeeper_pebble_ready_upgrade(mock_event)

    # Then
    ZKUpgradeEvents.set_unit_failed.assert_called_once()


@pytest.mark.skipif(SUBSTRATE == "vm", reason="No pebble on VM charms")
def test_zookeeper_pebble_ready_upgrade_sets_completed(
    ctx: Context, base_state: State, mocker
) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={})
    restart_relation = PeerRelation("restart", CHARM_KEY)
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, restart_relation])
    mocker.patch.object(ZKServer, "started", new_callable=PropertyMock, return_value=True)
    mocker.patch.object(ZKUpgradeEvents, "idle", new_callable=PropertyMock, return_value=False)
    mocker.patch.object(ZKWorkload, "alive", new_callable=PropertyMock, return_value=True)
    mocker.patch.object(ZKWorkload, "healthy", new_callable=PropertyMock, return_value=True)
    mocker.patch.object(ZKUpgradeEvents, "apply_backwards_compatibility_fixes")
    mocker.patch.object(ZKUpgradeEvents, "post_upgrade_check", return_value=None)
    mocker.patch.object(ZKUpgradeEvents, "set_unit_completed")

    mock_event = mocker.MagicMock()

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.upgrade_events._on_zookeeper_pebble_ready_upgrade(mock_event)

    # Then
    ZKUpgradeEvents.apply_backwards_compatibility_fixes.assert_called_once()
    ZKUpgradeEvents.set_unit_completed.assert_called_once()
