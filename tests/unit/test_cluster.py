#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import re
from pathlib import Path

import pytest
import yaml
from ops.model import Unit
from ops.testing import Harness

from charm import ZooKeeperK8sCharm
from cluster import UnitNotFoundError
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
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_peer_units_contains_unit(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
    assert len(harness.charm.cluster.peer_units) == 2


def test_started_units_ignores_ready_units(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"state": "ready"}
        )
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/2", {"state": "started"}
        )
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/3")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/3", {"state": "started"}
        )

    assert len(harness.charm.cluster.started_units) == 2


def test_get_unit_id(harness):
    assert harness.charm.cluster.get_unit_id(harness.charm.unit) == 0


def test_get_unit_from_id_succeeds(harness):
    unit = harness.charm.cluster.get_unit_from_id(0)

    assert isinstance(unit, Unit)


def test_get_unit_from_id_raises(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")

    with pytest.raises(UnitNotFoundError):
        harness.charm.cluster.get_unit_from_id(100)


def test_unit_config_raises_for_missing_unit(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")

    with pytest.raises(UnitNotFoundError):
        harness.charm.cluster.get_unit_from_id(100)


def test_unit_config_succeeds_for_id(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"private-address": "treebeard"}
        )

    assert harness.charm.cluster.unit_config(unit=1)


def test_unit_config_succeeds_for_unit(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )

    assert harness.charm.cluster.unit_config(harness.charm.unit)


def test_unit_config_has_all_keys(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )

    config = harness.charm.cluster.unit_config(0)

    assert set(config.keys()) == {
        "host",
        "server_string",
        "server_id",
        "unit_id",
        "unit_name",
        "state",
    }


def test_unit_config_server_string_format(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )

    server_string = harness.charm.cluster.unit_config(0)["server_string"]
    split_string = re.split("=|:|;", server_string)

    assert len(split_string) == 7
    assert f"{CHARM_KEY}-0.zookeeper-k8s-endpoints" in split_string


def test_get_updated_servers(harness):
    added_servers = [
        "server.2=gandalf.the.grey",
    ]
    removed_servers = [
        "server.2=gandalf.the.grey",
        "server.3=in.a.hole.in.the.ground.there.lived.a:hobbit",
    ]
    updated_servers = harness.charm.cluster._get_updated_servers(
        added_servers=added_servers, removed_servers=removed_servers
    )

    assert updated_servers == {"2": "removed", "1": "added"}


def test_is_unit_turn_succeeds_scaleup(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}",
            {"0": "added", "1": "added", "2": "added"},
        )
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/0")

    units = sorted(harness.charm.cluster.relation.units, key=lambda x: x.name)
    harness.set_planned_units(1)

    assert harness.charm.cluster.is_unit_turn(units[0])


def test_is_unit_turn_fails_scaleup(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}",
            {"0": "added", "1": "added", "sync_password": "gollum", "super_password": "precious"},
        )
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/0")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/3")

    units = sorted(harness.charm.cluster.relation.units, key=lambda x: x.name)
    harness.set_planned_units(4)

    assert not harness.charm.cluster.is_unit_turn(units[3])


def test_is_unit_turn_succeeds_failover(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}",
            {"0": "added", "1": "added", "sync_password": "gollum", "super_password": "precious"},
        )
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/0")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/3")

    units = sorted(harness.charm.cluster.relation.units, key=lambda x: x.name)
    harness.set_planned_units(4)

    assert harness.charm.cluster.is_unit_turn(units[0])
    assert harness.charm.cluster.is_unit_turn(units[2])
    assert not harness.charm.cluster.is_unit_turn(units[3])


def test_is_unit_turn_fails_failover(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}",
            {"0": "added", "1": "added", "sync_password": "gollum", "super_password": "precious"},
        )
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/0")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/3")

    units = sorted(harness.charm.cluster.relation.units, key=lambda x: x.name)
    harness.set_planned_units(4)

    assert not harness.charm.cluster.is_unit_turn(units[3])


def test_generate_units_scaleup_adds_all_servers(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"private-address": "gandalf"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/2", {"private-address": "gimli"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}", {"0": "added", "1": "added"}
        )

    new_unit_string = harness.charm.cluster.unit_config(2, state="ready", role="observer")[
        "server_string"
    ]
    generated_servers = harness.charm.cluster._generate_units(unit_string=new_unit_string)

    assert (
        f"server.3={CHARM_KEY}-2.zookeeper-k8s-endpoints:2888:3888:observer;0.0.0.0:2181"
        in generated_servers
    )
    assert len(generated_servers.splitlines()) == 4


def test_generate_units_scaleup_adds_correct_roles_for_added_units(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"private-address": "gandalf"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/2", {"private-address": "gimli"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}", {"0": "added", "1": "removed"}
        )

        new_unit_string = harness.charm.cluster.unit_config(2, state="ready", role="observer")[
            "server_string"
        ]
        generated_servers = harness.charm.cluster._generate_units(unit_string=new_unit_string)

    assert len(re.findall("participant", generated_servers)) == 1
    assert len(re.findall("observer", generated_servers)) == 1


def test_generate_units_failover(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"private-address": "gandalf"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}",
            {"0": "removed", "1": "added", "2": "removed"},
        )

    new_unit_string = harness.charm.cluster.unit_config(0, state="ready", role="observer")[
        "server_string"
    ]
    generated_servers = harness.charm.cluster._generate_units(unit_string=new_unit_string)

    assert len(generated_servers.splitlines()) == 3


def test_startup_servers_raises_for_missing_data(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}",
            {"0": "removed", "sync_password": "Mellon"},
        )

    with pytest.raises(UnitNotFoundError):
        harness.charm.cluster.startup_servers(unit=3)


def test_startup_servers_succeeds_init(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}",
            {"sync_password": "gollum", "super_password": "precious"},
        )

    harness.set_planned_units(1)
    servers = harness.charm.cluster.startup_servers(unit=0)

    assert "observer" not in servers


def test_startup_servers_succeeds_failover_after_init(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"private-address": "treebeard"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"private-address": "gandalf"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}",
            {"0": "removed", "1": "added", "sync_password": "Mellon"},
        )

    generated_servers = harness.charm.cluster.startup_servers(unit=0)

    assert len(re.findall("participant", generated_servers)) == 1
    assert len(re.findall("observer", generated_servers)) == 1


def test_all_units_related(harness):
    with harness.hooks_disabled():
        assert not harness.charm.cluster.all_units_related

        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.set_planned_units(2)

        assert harness.charm.cluster.all_units_related


def test_lowest_unit_id_none_if_not_all_related(harness):
    assert harness.charm.cluster.lowest_unit_id == None  # noqa: E711


def test_lowest_unit_id(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")

    harness.set_planned_units(2)

    assert harness.charm.cluster.lowest_unit_id == 0


def test_stale_quorum_not_all_related(harness):
    assert not harness.charm.cluster.stale_quorum


def test_stale_quorum_unit_departed(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id, CHARM_KEY, {"0": "added", "1": "removed"}
        )

    assert not harness.charm.cluster.stale_quorum


def test_stale_quorum_new_unit(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.set_planned_units(2)
        harness.update_relation_data(
            harness.charm.cluster.relation.id, CHARM_KEY, {"0": "added", "1": ""}
        )

    assert harness.charm.cluster.stale_quorum


def test_stale_quorum_all_added(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.set_planned_units(2)
        harness.update_relation_data(
            harness.charm.cluster.relation.id, CHARM_KEY, {"0": "added", "1": "added"}
        )
    assert not harness.charm.cluster.stale_quorum


def test_all_rotated_fails(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"password-rotated": "true"}
        )
    assert not harness.charm.cluster._all_rotated()


def test_all_rotated_succeeds(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"password-rotated": "true"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"password-rotated": "true"}
        )

    assert harness.charm.cluster._all_rotated()


def test_passwords_set_fails_missing_unit_passwords(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}/0",
            {"super-password": "mellon", "sync-password": "mellon"},
        )

    assert not harness.charm.cluster.passwords_set


def test_passwords_set_fails_missing_password(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"super-password": "mellon"}
        )

    assert not harness.charm.cluster.passwords_set


def test_passwords_set_succeeds(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}/0",
            {"super-password": "mellon", "sync-password": "mellon"},
        )

    assert not harness.charm.cluster.passwords_set


def test_all_units_quorum_fails_wrong_quorum(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"quorum": "ssl"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"quorum": "non-ssl"}
        )

        assert not harness.charm.cluster.all_units_quorum

        harness.update_relation_data(
            harness.charm.cluster.relation.id, CHARM_KEY, {"quorum": "ssl"}
        )

        assert not harness.charm.cluster.all_units_quorum


def test_all_units_quorum_succeeds(harness):
    with harness.hooks_disabled():
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id, CHARM_KEY, {"quorum": "ssl"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/1", {"quorum": "ssl"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"quorum": "ssl"}
        )

    assert harness.charm.cluster.all_units_quorum
