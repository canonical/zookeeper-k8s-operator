#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import re
import unittest

import ops.testing
from charms.zookeeper.v0.cluster import (
    NoPasswordError,
    NotUnitTurnError,
    UnitNotFoundError,
    ZooKeeperCluster,
)
from ops.charm import CharmBase
from ops.model import Unit
from ops.testing import Harness

ops.testing.SIMULATE_CAN_CONNECT = True


METADATA = """
    name: zookeeper
    peers:
        cluster:
            interface: cluster
"""


class DummyZooKeeperCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.cluster = ZooKeeperCluster(self)


class TestCluster(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(DummyZooKeeperCharm, meta=METADATA)
        self.addCleanup(self.harness.cleanup)
        self.relation_id = self.harness.add_relation("cluster", "cluster")
        self.harness.begin_with_initial_hooks()

    @property
    def cluster(self):
        return self.harness.charm.cluster

    def test_peer_units_contains_unit(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")

        self.assertEqual(len(self.cluster.peer_units), 2)

    def test_started_units_ignores_ready_units(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")
        self.harness.update_relation_data(self.relation_id, "zookeeper/1", {"state": "ready"})
        self.harness.add_relation_unit(self.relation_id, "zookeeper/2")
        self.harness.update_relation_data(self.relation_id, "zookeeper/2", {"state": "started"})
        self.harness.add_relation_unit(self.relation_id, "zookeeper/3")
        self.harness.update_relation_data(self.relation_id, "zookeeper/3", {"state": "started"})

        self.assertEqual(len(self.cluster.started_units), 2)

    def test_get_unit_id(self):
        self.assertEqual(self.harness.charm.cluster.get_unit_id(self.harness.charm.unit), 0)

    def test_get_unit_from_id_succeeds(self):
        unit = self.cluster.get_unit_from_id(0)

        self.assertTrue(isinstance(unit, Unit))

    def test_get_unit_from_id_raises(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")

        with self.assertRaises(UnitNotFoundError):
            self.cluster.get_unit_from_id(100)

    def test_unit_config_raises_for_missing_unit(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")

        with self.assertRaises(UnitNotFoundError):
            self.cluster.get_unit_from_id(100)

    def test_unit_config_succeeds_for_id(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/1", {"private-address": "treebeard"}
        )

        self.cluster.unit_config(unit=1)

    def test_unit_config_succeeds_for_unit(self):
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )

        self.cluster.unit_config(self.harness.charm.unit)

    def test_unit_config_has_all_keys(self):
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        config = self.cluster.unit_config(0)

        self.assertEqual(
            set(config.keys()),
            set(["host", "server_string", "server_id", "unit_id", "unit_name", "state"]),
        )

    def test_unit_config_server_string_format(self):
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        server_string = self.cluster.unit_config(0)["server_string"]
        split_string = re.split("=|:|;", server_string)

        self.assertEqual(len(split_string), 7)
        self.assertIn("treebeard", split_string)

    def test_get_updated_servers(self):
        added_servers = [
            "server.2=gandalf.the.grey",
        ]
        removed_servers = [
            "server.2=gandalf.the.grey",
            "server.3=in.a.hole.in.the.ground.there.lived.a:hobbit",
        ]
        updated_servers = self.cluster._get_updated_servers(
            added_servers=added_servers, removed_servers=removed_servers
        )

        self.assertDictEqual(updated_servers, {"2": "removed", "1": "added"})

    def test_is_unit_turn_succeeds_scaleup(self):
        self.harness.update_relation_data(
            self.relation_id, "zookeeper", {"0": "added", "1": "added", "2": "added"}
        )

        self.assertTrue(self.cluster._is_unit_turn(3))

    def test_is_unit_turn_fails_scaleup(self):
        self.harness.update_relation_data(
            self.relation_id,
            "zookeeper",
            {"0": "added", "1": "added", "sync_password": "gollum", "super_password": "precious"},
        )

        self.assertFalse(self.cluster._is_unit_turn(3))

    def test_is_unit_turn_succeeds_failover(self):
        self.harness.update_relation_data(
            self.relation_id,
            "zookeeper",
            {"0": "added", "1": "added", "sync_password": "gollum", "super_password": "precious"},
        )

        self.assertTrue(self.cluster._is_unit_turn(0))
        self.assertTrue(self.cluster._is_unit_turn(2))

    def test_is_unit_turn_fails_failover(self):
        self.harness.update_relation_data(
            self.relation_id,
            "zookeeper",
            {"0": "added", "1": "added", "sync_password": "gollum", "super_password": "precious"},
        )

        self.assertFalse(self.cluster._is_unit_turn(3))

    def test_ready_to_start_raises_no_password(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/1", {"private-address": "gandalf"}
        )
        self.harness.update_relation_data(self.relation_id, "zookeeper", {"0": "removed"})

        with self.assertRaises(NoPasswordError):
            self.cluster.ready_to_start(unit=1)

    def test_generate_units_scaleup_adds_all_servers(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")
        self.harness.add_relation_unit(self.relation_id, "zookeeper/2")
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/1", {"private-address": "gandalf"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/2", {"private-address": "gimli"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper", {"0": "added", "1": "added"}
        )

        new_unit_string = self.cluster.unit_config(2, state="ready", role="observer")[
            "server_string"
        ]
        generated_servers = self.cluster._generate_units(unit_string=new_unit_string)

        self.assertIn("server.3=gimli", generated_servers)
        self.assertEqual(len(generated_servers.splitlines()), 4)

    def test_generate_units_scaleup_adds_correct_roles_for_added_units(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")
        self.harness.add_relation_unit(self.relation_id, "zookeeper/2")
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/1", {"private-address": "gandalf"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/2", {"private-address": "gimli"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper", {"0": "added", "1": "removed"}
        )

        new_unit_string = self.cluster.unit_config(2, state="ready", role="observer")[
            "server_string"
        ]
        generated_servers = self.cluster._generate_units(unit_string=new_unit_string)

        self.assertEqual(len(re.findall("participant", generated_servers)), 1)
        self.assertEqual(len(re.findall("observer", generated_servers)), 1)

    def test_generate_units_failover(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")
        self.harness.add_relation_unit(self.relation_id, "zookeeper/2")
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/1", {"private-address": "gandalf"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper", {"0": "removed", "1": "added", "2": "removed"}
        )

        new_unit_string = self.cluster.unit_config(0, state="ready", role="observer")[
            "server_string"
        ]
        generated_servers = self.cluster._generate_units(unit_string=new_unit_string)

        self.assertEqual(len(generated_servers.splitlines()), 3)

    def test_ready_to_start_raises_for_missing_data(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/2")
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper", {"0": "removed", "sync_password": "Mellon"}
        )

        with self.assertRaises(UnitNotFoundError):
            self.cluster.ready_to_start(unit=2)

    def test_ready_to_start_raises_for_not_unit_turn(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")
        self.harness.add_relation_unit(self.relation_id, "zookeeper/2")
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/1", {"private-address": "gandalf"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/2", {"private-address": "gimli"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper", {"0": "removed", "sync_password": "Mellon"}
        )

        with self.assertRaises(NotUnitTurnError):
            self.cluster.ready_to_start(unit=2)

    def test_ready_to_start_succeeds_init(self):
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        self.harness.update_relation_data(
            self.relation_id,
            "zookeeper",
            {"sync_password": "gollum", "super_password": "precious"},
        )
        servers, _ = self.cluster.ready_to_start(unit=0)
        self.assertNotIn("observer", servers)

    def test_ready_to_start_succeeds_failover_after_init(self):
        self.harness.add_relation_unit(self.relation_id, "zookeeper/1")
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/0", {"private-address": "treebeard"}
        )
        self.harness.update_relation_data(
            self.relation_id, "zookeeper/1", {"private-address": "gandalf"}
        )
        self.harness.update_relation_data(
            self.relation_id,
            "zookeeper",
            {"0": "removed", "1": "added", "sync_password": "Mellon"},
        )

        generated_servers, _ = self.cluster.ready_to_start(unit=0)

        self.assertEqual(len(re.findall("participant", generated_servers)), 1)
        self.assertEqual(len(re.findall("observer", generated_servers)), 1)
