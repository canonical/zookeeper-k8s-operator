#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import logging
import re
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
import yaml
from ops.testing import Container, Context, PeerRelation, State

from charm import ZooKeeperCharm
from literals import CONTAINER, PEER, SUBSTRATE

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


def test_servers_contains_unit(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={1: {}})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert len(charm.state.servers) == 2


def test_client_port_changes_for_tls(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={1: {}})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        current_port = charm.state.client_port

    cluster_peer = dataclasses.replace(cluster_peer, local_app_data={"tls": "enabled"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.client_port != current_port


def test_started_units_ignores_ready_units(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        peers_data={
            1: {"state": "ready"},
            2: {"state": "started"},
            3: {"state": "started"},
        },
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert len(charm.state.started_servers) == 2


def test_all_units_related_fails_new_units(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_unit_data={}, peers_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("workload.ZKWorkload.write"), ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.all_units_related

    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.all_units_related


def test_all_servers_added_fails(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={"0": "added"}, peers_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("workload.ZKWorkload.write"), ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.all_servers_added

    cluster_peer = PeerRelation(
        PEER, PEER, local_app_data={"0": "added"}, local_unit_data={}, peers_data={1: {}}
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.all_servers_added


def test_lowest_unit_id_returns_none(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, peers_data={1: {}, 2: {}})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=4)

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        state_out = manager.run()

        # Then
        assert charm.state.lowest_unit_id == None  # noqa: E711

    state_in = dataclasses.replace(state_out, relations=[cluster_peer], planned_units=3)

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert charm.state.lowest_unit_id == 0


def test_init_leader(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={},
        local_unit_data={},
        peers_data={1: {}, 2: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=3)

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        state_out = manager.run()

        # Then
        assert charm.state.init_leader.unit_id == 0

    cluster_peer = dataclasses.replace(cluster_peer, local_app_data={"0": "added"})
    state_in = dataclasses.replace(state_out, relations=[cluster_peer])

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.init_leader == None  # noqa: E711


def test_next_server_succeeds_scaleup(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={"0": "added"}, peers_data={1: {}})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert charm.state.next_server.unit_id == 1


def test_next_server_succeeds_failover(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"0": "added", "1": "added", "2": "added", "3": "added"},
        local_unit_data={},
        peers_data={1: {}, 2: {}, 3: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=4)

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert charm.state.next_server.unit_id == 0


def test_next_server_fails_failover(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"0": "added", "1": "added", "2": "added", "3": "added"},
        local_unit_data={},
        peers_data={1: {}, 2: {}, 3: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=4)

    # When
    with ctx(ctx.on.relation_changed(cluster_peer), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        manager.run()

        # Then
        assert not charm.state.next_server.unit_id == 3


def test_startup_servers_defaults_to_init_leader(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER, PEER, local_app_data={}, local_unit_data={}, peers_data={1: {}}
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.startup_servers
        assert "participant" in charm.state.startup_servers


def test_startup_servers_succeeds_failover_after_init(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"0": "added", "1": "added"},
        local_unit_data={},
        peers_data={1: {}, 2: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=3)
    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then

        assert len(re.findall("participant", charm.state.startup_servers)) == 2
        assert len(re.findall("observer", charm.state.startup_servers)) == 1


def test_stale_quorum_not_all_related(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={}, local_unit_data={}, peers_data={})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("workload.ZKWorkload.write"), ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.stale_quorum


def test_stale_quorum_unit_departed(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"0": "added", "1": "removed"},
        local_unit_data={},
        peers_data={},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with patch("workload.ZKWorkload.write"), ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.stale_quorum


def test_stale_quorum_new_unit(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"0": "added", "1": ""},
        local_unit_data={},
        peers_data={1: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.stale_quorum


def test_stale_quorum_all_added(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"0": "added", "1": "added"},
        local_unit_data={},
        peers_data={1: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.stale_quorum


def test_all_rotated_fails(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={},
        local_unit_data={"password-rotated": "true"},
        peers_data={1: {}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.all_rotated


def test_all_rotated_succeeds(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={},
        local_unit_data={"password-rotated": "true"},
        peers_data={1: {"password-rotated": "true"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.all_rotated


def test_all_units_unified_fails(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={},
        local_unit_data={"state": "started"},
        peers_data={1: {"unified": "true", "state": "started"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.all_units_unified


def test_all_units_unified_succeeds(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={},
        local_unit_data={"unified": "true", "state": "started"},
        peers_data={1: {"unified": "true", "state": "started"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.all_units_unified


def test_all_units_quorum_fails_wrong_quorum(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"quorum": "non-ssl"},
        peers_data={1: {"quorum": "ssl"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with patch("workload.ZKWorkload.write"), ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.all_units_quorum

    cluster_peer = dataclasses.replace(cluster_peer, local_app_data={"quorum": "ssl"})
    state_in = dataclasses.replace(state_in, relations=[cluster_peer])

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.state.all_units_quorum


def test_all_units_quorum_succeeds(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"quorum": "ssl"},
        local_unit_data={"quorum": "ssl"},
        peers_data={1: {"quorum": "ssl"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.state.all_units_quorum
