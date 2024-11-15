#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import dataclasses
import logging
from pathlib import Path
from typing import cast
from unittest.mock import DEFAULT, MagicMock, patch

import pytest
import yaml
from ops.testing import Container, Context, PeerRelation, Relation, State

from charm import ZooKeeperCharm
from literals import CONTAINER, PEER, REL_NAME, SUBSTRATE

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


@pytest.mark.skipif(SUBSTRATE == "vm", reason="Explicit removal not possible on vm")
def test_get_updated_servers_explicit_removal(ctx: Context, base_state: State) -> None:
    # Given
    added_servers = [
        "server.2=gandalf.the.grey",
    ]
    removed_servers = [
        "server.2=gandalf.the.grey",
        "server.3=in.a.hole.in.the.ground.there.lived.a:hobbit",
    ]
    state_in = base_state

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        updated_servers = charm.quorum_manager._get_updated_servers(
            add=added_servers, remove=removed_servers
        )

    # Then
    assert updated_servers == {"2": "removed", "1": "added"}


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Implicit removal only on vm")
def test_get_updated_servers_implicit_removal(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER, PEER, local_app_data={"0": "added", "3": "removed", "4": "added"}
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])
    added_servers = [
        "server.2=gandalf.the.grey",
    ]

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        # at this stage, we have only 1 peer unit-0, and have 'lost' unit-4, which was 'added' but not yet removed
        # we add unit-1 below, and expect the leader to set unit-1 to 'added', and unit-4 to 'removed'

        updated_servers = charm.quorum_manager._get_updated_servers(add=added_servers)

    assert updated_servers == {"1": "added", "4": "removed"}


def test_is_child_of(ctx: Context, base_state: State) -> None:
    # Given
    chroot = "/gandalf/the/white"
    chroots = {"/gandalf", "/saruman"}
    state_in = base_state

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert charm.quorum_manager._is_child_of(path=chroot, chroots=chroots)


def test_is_child_of_not(ctx: Context, base_state: State) -> None:
    # Given
    chroot = "/the/one/ring"
    chroots = {"/gandalf", "/saruman"}
    state_in = base_state

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.quorum_manager._is_child_of(path=chroot, chroots=chroots)


def test_update_acls_does_not_add_empty_chroot(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(PEER, PEER)
    client_relation = Relation(
        REL_NAME,
        "application",
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch.multiple(
            "charms.zookeeper.v0.client.ZooKeeperManager",
            get_leader=DEFAULT,
            leader_znodes=MagicMock(return_value={"/"}),
            create_znode_leader=DEFAULT,
            set_acls_znode_leader=DEFAULT,
            delete_znode_leader=DEFAULT,
        ) as patched_manager,
        ctx(ctx.on.start(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.quorum_manager.update_acls()

    # Then
    patched_manager["create_znode_leader"].assert_not_called()


def test_update_acls_correctly_handles_relation_chroots(ctx: Context, base_state: State) -> None:
    # Given
    dummy_leader_znodes = {
        "/fellowship",
        "/fellowship/men",
        "/fellowship/dwarves",
        "/fellowship/men/aragorn",
        "/fellowship/men/boromir",
        "/fellowship/elves/legolas",
        "/fellowship/dwarves/gimli",
        "/fellowship/men/aragorn/anduril",
    }
    cluster_peer = PeerRelation(PEER, PEER)
    client_relation = Relation(REL_NAME, "application", remote_app_data={"database": "/rohan"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with (
        patch.multiple(
            "charms.zookeeper.v0.client.ZooKeeperManager",
            get_leader=DEFAULT,
            leader_znodes=MagicMock(return_value=dummy_leader_znodes),
            create_znode_leader=DEFAULT,
            set_acls_znode_leader=DEFAULT,
            delete_znode_leader=DEFAULT,
        ) as patched_manager,
        ctx(ctx.on.start(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.quorum_manager.update_acls()

        # Then
        for _, kwargs in patched_manager["create_znode_leader"].call_args_list:
            assert "/rohan" in kwargs["path"]

        _, kwargs = patched_manager["set_acls_znode_leader"].call_args_list[0]
        assert "/rohan" in kwargs["path"]

        paths_updated = {
            kwargs["path"] for _, kwargs in patched_manager["set_acls_znode_leader"].call_args_list
        }

        # all paths saw their acls updated
        assert not dummy_leader_znodes - paths_updated
