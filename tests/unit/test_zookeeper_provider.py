#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import re
import unittest
from collections import namedtuple

import ops.testing
from charms.rolling_ops.v0.rollingops import RollingOpsManager
from charms.zookeeper.v0.cluster import ZooKeeperCluster
from charms.zookeeper.v0.zookeeper_provider import ZooKeeperProvider
from ops.charm import CharmBase, RelationBrokenEvent
from ops.testing import Harness

ops.testing.SIMULATE_CAN_CONNECT = True

logger = logging.getLogger(__name__)

METADATA = """
    name: zookeeper
    peers:
        cluster:
            interface: cluster
        restart:
            interface: rolling_op
    provides:
        zookeeper:
            interface: zookeeper
"""

CustomRelation = namedtuple("Relation", ["id"])


class DummyZooKeeperCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.cluster = ZooKeeperCluster(self)
        self.client_relation = ZooKeeperProvider(self)
        self.restart = RollingOpsManager(self, relation="restart", callback=lambda x: x)


class TestProvider(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(DummyZooKeeperCharm, meta=METADATA)
        self.addCleanup(self.harness.cleanup)
        self.harness.add_relation("zookeeper", "application")
        self.harness.begin_with_initial_hooks()

    @property
    def provider(self):
        return self.harness.charm.client_relation

    def test_relation_config_new_relation_no_chroot(self):
        config = self.harness.charm.client_relation.relation_config(
            relation=self.provider.client_relations[0]
        )
        self.assertIsNone(config)

    def test_relation_config_new_relation(self):
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        self.harness.update_relation_data(
            self.provider.app_relation.id, "zookeeper", {"relation-0": "password"}
        )

        config = self.harness.charm.client_relation.relation_config(
            relation=self.provider.client_relations[0]
        )

        self.assertEqual(
            config,
            {
                "username": "relation-0",
                "password": "password",
                "chroot": "/app",
                "acl": "cdrwa",
            },
        )

    def test_relation_config_new_relation_defaults_to_database(self):
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"database": "app"}
        )
        self.harness.update_relation_data(
            self.provider.app_relation.id, "zookeeper", {"relation-0": "password"}
        )

        config = self.harness.charm.client_relation.relation_config(
            relation=self.provider.client_relations[0]
        )

        self.assertEqual(
            config,
            {
                "username": "relation-0",
                "password": "password",
                "chroot": "/app",
                "acl": "cdrwa",
            },
        )

    def test_relation_config_new_relation_empty_password(self):
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )

        config = self.harness.charm.client_relation.relation_config(
            relation=self.provider.client_relations[0]
        )

        self.assertEqual(
            config,
            {
                "username": "relation-0",
                "password": "",
                "chroot": "/app",
                "acl": "cdrwa",
            },
        )

    def test_relation_config_new_relation_app_permissions(self):
        self.harness.update_relation_data(
            self.provider.client_relations[0].id,
            "application",
            {"chroot": "app", "chroot-acl": "rw"},
        )

        config = self.harness.charm.client_relation.relation_config(
            relation=self.provider.client_relations[0]
        )

        self.assertEqual(
            config,
            {
                "username": "relation-0",
                "password": "",
                "chroot": "/app",
                "acl": "rw",
            },
        )

    def test_relation_config_new_relation_skips_relation_broken(self):
        self.harness.update_relation_data(
            self.provider.client_relations[0].id,
            "application",
            {"chroot": "app", "chroot-acl": "rw"},
        )

        custom_relation = CustomRelation(id=self.provider.client_relations[0].id)

        config = self.harness.charm.client_relation.relation_config(
            relation=self.provider.client_relations[0],
            event=RelationBrokenEvent(handle="", relation=custom_relation),
        )

        self.assertIsNone(config)

    def test_relations_config_multiple_relations(self):
        self.harness.add_relation("zookeeper", "new_application")
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        self.harness.update_relation_data(
            self.provider.client_relations[1].id, "new_application", {"chroot": "new_app"}
        )

        relations_config = self.harness.charm.client_relation.relations_config()

        self.assertEqual(
            relations_config,
            {
                "0": {
                    "username": "relation-0",
                    "password": "",
                    "chroot": "/app",
                    "acl": "cdrwa",
                },
                "3": {
                    "username": "relation-3",
                    "password": "",
                    "chroot": "/new_app",
                    "acl": "cdrwa",
                },
            },
        )

    def test_build_acls(self):
        self.harness.add_relation("zookeeper", "new_application")
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        self.harness.update_relation_data(
            self.provider.client_relations[1].id,
            "new_application",
            {"chroot": "new_app", "chroot-acl": "rw"},
        )

        acls = self.harness.charm.client_relation.build_acls()

        self.assertEqual(len(acls), 2)
        self.assertEqual(sorted(acls.keys()), ["/app", "/new_app"])
        self.assertIsInstance(acls["/app"], list)

        new_app_acl = acls["/new_app"][0]

        self.assertEqual(new_app_acl.acl_list, ["READ", "WRITE"])
        self.assertEqual(new_app_acl.id.scheme, "sasl")
        self.assertEqual(new_app_acl.id.id, "relation-3")

    def test_relations_config_values_for_key(self):
        self.harness.add_relation("zookeeper", "new_application")
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        self.harness.update_relation_data(
            self.provider.client_relations[1].id,
            "new_application",
            {"chroot": "new_app", "chroot-acl": "rw"},
        )

        config_values = self.harness.charm.client_relation.relations_config_values_for_key(
            key="username"
        )

        self.assertEqual(config_values, {"relation-3", "relation-0"})

    def test_is_child_of(self):
        chroot = "/gandalf/the/white"
        chroots = {"/gandalf", "/saruman"}

        self.assertTrue(
            self.harness.charm.client_relation._is_child_of(path=chroot, chroots=chroots)
        )

    def test_is_child_of_not(self):
        chroot = "/the/one/ring"
        chroots = {"/gandalf", "/saruman"}

        self.assertFalse(
            self.harness.charm.client_relation._is_child_of(path=chroot, chroots=chroots)
        )

    def test_apply_relation_data(self):
        self.harness.set_leader(True)
        self.harness.add_relation("zookeeper", "new_application")
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        self.harness.update_relation_data(
            self.provider.client_relations[1].id,
            "new_application",
            {"chroot": "new_app", "chroot-acl": "rw"},
        )
        self.harness.update_relation_data(
            self.provider.app_relation.id,
            "zookeeper/0",
            {"private-address": "treebeard", "state": "started"},
        )
        self.harness.add_relation_unit(self.provider.app_relation.id, "zookeeper/1")
        self.harness.update_relation_data(
            self.provider.app_relation.id,
            "zookeeper/1",
            {"private-address": "shelob", "state": "ready"},
        )
        self.harness.add_relation_unit(self.provider.app_relation.id, "zookeeper/2")
        self.harness.update_relation_data(
            self.provider.app_relation.id,
            "zookeeper/2",
            {"private-address": "balrog", "state": "started"},
        )

        self.harness.charm.client_relation.apply_relation_data()

        self.assertIsNotNone(
            self.harness.charm.cluster.relation.data[self.harness.charm.app].get(
                "relation-0", None
            )
        )
        self.assertIsNotNone(
            self.harness.charm.cluster.relation.data[self.harness.charm.app].get(
                "relation-3", None
            )
        )

        app_data = self.harness.charm.cluster.relation.data[self.harness.charm.app]
        passwords = []
        usernames = []
        for relation in self.provider.client_relations:
            # checking existence of all necessary keys
            self.assertEqual(
                sorted(relation.data[self.harness.charm.app].keys()),
                sorted(["chroot", "endpoints", "password", "uris", "username"]),
            )

            username = relation.data[self.harness.charm.app]["username"]
            password = relation.data[self.harness.charm.app]["password"]

            # checking ZK app data got updated
            self.assertIn(username, app_data)
            self.assertEqual(password, app_data.get(username, None))

            # checking unique passwords and usernames for all relations
            self.assertNotIn(username, usernames)
            self.assertNotIn(password, passwords)

            # checking multiple endpoints and uris
            self.assertEqual(len(relation.data[self.harness.charm.app]["endpoints"].split(",")), 2)
            self.assertEqual(len(relation.data[self.harness.charm.app]["uris"].split(",")), 2)

            for uri in relation.data[self.harness.charm.app]["uris"].split(","):
                # checking client_port in uri
                self.assertTrue(re.search(r":[\d]+", uri))

            self.assertTrue(
                relation.data[self.harness.charm.app]["uris"].endswith(
                    relation.data[self.harness.charm.app]["chroot"]
                )
            )

            passwords.append(username)
            usernames.append(password)
