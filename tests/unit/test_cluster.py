#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import re
from pathlib import Path

import pytest
import yaml
from ops.testing import Harness

from charm import ZooKeeperK8sCharm
from literals import CHARM_KEY, PEER

logger = logging.getLogger(__name__)

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperK8sCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    harness.add_relation("restart", CHARM_KEY)
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.update_relation_data(
        peer_rel_id, f"{CHARM_KEY}/0", {"ip": "123", "hostname": "treebeard"}
    )
    harness.begin()
    return harness


def test_servers_contains_unit(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")

    assert len(harness.charm.state.servers) == 2


def test_client_port_changes_for_tls(harness):
    current_port = harness.charm.state.client_port

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"tls": "enabled"}
        )

    assert harness.charm.state.client_port != current_port


def test_started_units_ignores_ready_units(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1", {"state": "ready"}
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2", {"state": "started"}
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/3")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/3", {"state": "started"}
        )

    assert len(harness.charm.state.started_servers) == 2


def test_all_units_related_fails_new_units(harness):
    harness.set_planned_units(1)
    assert harness.charm.state.all_units_related

    harness.set_planned_units(2)
    assert not harness.charm.state.all_units_related


def test_all_servers_added_fails(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"0": "added"}
        )

    assert harness.charm.state.all_servers_added

    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")

    assert not harness.charm.state.all_servers_added


def test_lowest_unit_id_returns_none(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")

    harness.set_planned_units(4)

    assert harness.charm.state.lowest_unit_id == None  # noqa: E711

    harness.set_planned_units(3)

    assert harness.charm.state.lowest_unit_id == 0


def test_init_leader(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")

    harness.set_planned_units(3)

    assert harness.charm.state.init_leader.unit_id == 0

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"0": "added"}
        )

    assert harness.charm.state.init_leader == None  # noqa: E711


def test_next_server_succeeds_scaleup(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}",
            {"0": "added"},
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")

    harness.set_planned_units(2)
    assert harness.charm.state.next_server.unit_id == 1


def test_next_server_succeeds_failover(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}",
            {
                "0": "removed",
                "1": "added",
                "sync_password": "gollum",
                "super_password": "precious",
            },
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/3")

    harness.set_planned_units(4)
    assert harness.charm.state.next_server.unit_id == 0


def test_next_server_fails_failover(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}",
            {"0": "added", "1": "added", "sync_password": "gollum", "super_password": "precious"},
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/3")

    harness.set_planned_units(4)
    assert not harness.charm.state.next_server.unit_id == 3


def test_startup_servers_defaults_to_init_leader(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.set_planned_units(2)

    assert harness.charm.state.startup_servers
    assert "participant" in harness.charm.state.startup_servers


def test_startup_servers_succeeds_failover_after_init(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}",
            {"0": "added", "1": "added"},
        )
        harness.set_planned_units(3)

    assert len(re.findall("participant", harness.charm.state.startup_servers)) == 2
    assert len(re.findall("observer", harness.charm.state.startup_servers)) == 1


def test_stale_quorum_not_all_related(harness):
    assert harness.charm.state.stale_quorum


def test_stale_quorum_unit_departed(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"0": "added", "1": "removed"}
        )

    assert not harness.charm.state.stale_quorum


def test_stale_quorum_new_unit(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.set_planned_units(2)
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"0": "added", "1": ""}
        )

    assert harness.charm.state.stale_quorum


def test_stale_quorum_all_added(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.set_planned_units(2)
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"0": "added", "1": "added"}
        )

    assert not harness.charm.state.stale_quorum


def test_all_rotated_fails(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"password-rotated": "true"}
        )

    assert not harness.charm.state.all_rotated


def test_all_rotated_succeeds(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1", {"password-rotated": "true"}
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"password-rotated": "true"}
        )

    assert harness.charm.state.all_rotated


def test_all_units_unified_fails(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/1",
            {"unified": "true", "state": "started"},
        )

    assert not harness.charm.state.all_units_unified


def test_all_units_unified_succeeds(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {"unified": "true", "state": "started"},
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/1",
            {"unified": "true", "state": "started"},
        )

    assert harness.charm.state.all_units_unified


def test_all_units_quorum_fails_wrong_quorum(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1", {"quorum": "ssl"}
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"quorum": "non-ssl"}
        )

        assert not harness.charm.state.all_units_quorum

        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"quorum": "ssl"}
        )

        assert not harness.charm.state.all_units_quorum


def test_all_units_quorum_succeeds(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"quorum": "ssl"}
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1", {"quorum": "ssl"}
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"quorum": "ssl"}
        )

    assert harness.charm.state.all_units_quorum
