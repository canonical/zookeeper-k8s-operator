#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import re
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

import yaml
from ops.charm import RelationBrokenEvent
from ops.testing import Harness

from charm import ZooKeeperK8sCharm
from literals import CHARM_KEY, PEER, REL_NAME

logger = logging.getLogger(__name__)

METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))
CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))

CustomRelation = namedtuple("Relation", ["id"])


class TestProvider(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(ZooKeeperK8sCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
        self.addCleanup(self.harness.cleanup)
        self.client_rel_id = self.harness.add_relation(REL_NAME, "application")
        self.peer_rel_id = self.harness.add_relation(PEER, CHARM_KEY)
        self.harness.begin()

    @property
    def provider(self):
        return self.harness.charm.provider

    def test_relation_config_new_relation_no_chroot(self):
        config = self.harness.charm.provider.relation_config(
            relation=self.provider.client_relations[0]
        )
        self.assertIsNone(config)

    def test_relation_config_new_relation(self):

        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        self.harness.update_relation_data(
            self.provider.app_relation.id, CHARM_KEY, {"relation-0": "password"}
        )

        config = self.harness.charm.provider.relation_config(
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
            self.provider.app_relation.id, CHARM_KEY, {"relation-0": "password"}
        )

        config = self.harness.charm.provider.relation_config(
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

        config = self.harness.charm.provider.relation_config(
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

        config = self.harness.charm.provider.relation_config(
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

        config = self.harness.charm.provider.relation_config(
            relation=self.provider.client_relations[0],
            event=RelationBrokenEvent(handle="", relation=custom_relation),
        )

        self.assertIsNone(config)

    def test_relations_config_multiple_relations(self):
        self.harness.add_relation(REL_NAME, "new_application")
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        self.harness.update_relation_data(
            self.provider.client_relations[1].id, "new_application", {"chroot": "new_app"}
        )

        relations_config = self.harness.charm.provider.relations_config()

        self.assertEqual(
            relations_config,
            {
                "0": {
                    "username": "relation-0",
                    "password": "",
                    "chroot": "/app",
                    "acl": "cdrwa",
                },
                "2": {
                    "username": "relation-2",
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

        acls = self.harness.charm.provider.build_acls()

        self.assertEqual(len(acls), 2)
        self.assertEqual(sorted(acls.keys()), ["/app", "/new_app"])
        self.assertIsInstance(acls["/app"], list)

        new_app_acl = acls["/new_app"][0]

        self.assertEqual(new_app_acl.acl_list, ["READ", "WRITE"])
        self.assertEqual(new_app_acl.id.scheme, "sasl")
        self.assertEqual(new_app_acl.id.id, "relation-2")

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

        config_values = self.harness.charm.provider.relations_config_values_for_key(key="username")

        self.assertEqual(config_values, {"relation-2", "relation-0"})

    def test_is_child_of(self):
        chroot = "/gandalf/the/white"
        chroots = {"/gandalf", "/saruman"}

        self.assertTrue(self.harness.charm.provider._is_child_of(path=chroot, chroots=chroots))

    def test_is_child_of_not(self):
        chroot = "/the/one/ring"
        chroots = {"/gandalf", "/saruman"}

        self.assertFalse(self.harness.charm.provider._is_child_of(path=chroot, chroots=chroots))

    def test_port_updates_if_tls(self):
        self.harness.set_leader(True)
        self.harness.update_relation_data(
            self.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        # checking if ssl port and ssl flag are passed
        self.harness.update_relation_data(
            self.provider.app_relation.id,
            f"{CHARM_KEY}/0",
            {"state": "started"},
        )
        self.harness.update_relation_data(
            self.provider.app_relation.id,
            CHARM_KEY,
            {"quorum": "ssl"},
        )
        self.harness.charm.provider.apply_relation_data()

        for relation in self.provider.client_relations:
            uris = relation.data[self.harness.charm.app].get("uris", "")
            ssl = relation.data[self.harness.charm.app].get("tls", "")

            self.assertIn(str(self.harness.charm.cluster.secure_client_port), uris)
            self.assertEqual(ssl, "enabled")

        self.harness.update_relation_data(
            self.provider.app_relation.id,
            CHARM_KEY,
            {"quorum": "non-ssl"},
        )
        self.harness.charm.provider.apply_relation_data()

        for relation in self.provider.client_relations:
            uris = relation.data[self.harness.charm.app].get("uris", "")
            ssl = relation.data[self.harness.charm.app].get("tls", "")

            self.assertIn(str(self.harness.charm.cluster.client_port), uris)
            self.assertEqual(ssl, "disabled")

    @patch("ops.model.Container.can_connect")
    @patch(
        "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", return_value=None
    )
    def test_provider_relation_data_updates_port(self, *_):
        with patch("provider.ZooKeeperProvider.apply_relation_data", return_value=None) as patched:
            self.harness.set_leader(True)
            self.harness.update_relation_data(
                self.peer_rel_id,
                CHARM_KEY,
                {"quorum": "non-ssl"},
            )

            patched.assert_called_once()

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
        self.harness.add_relation_unit(self.provider.app_relation.id, "{CHARM_KEY}/0")
        self.harness.update_relation_data(
            self.provider.app_relation.id,
            "{CHARM_KEY}/0",
            {"state": "started"},
        )
        self.harness.add_relation_unit(self.provider.app_relation.id, "{CHARM_KEY}/1")
        self.harness.update_relation_data(
            self.provider.app_relation.id,
            "{CHARM_KEY}/1",
            {"state": "ready"},
        )
        self.harness.add_relation_unit(self.provider.app_relation.id, "{CHARM_KEY}/2")
        self.harness.update_relation_data(
            self.provider.app_relation.id,
            "{CHARM_KEY}/2",
            {"state": "started"},
        )

        self.harness.charm.provider.apply_relation_data()

        self.assertIsNotNone(
            self.harness.charm.cluster.relation.data[self.harness.charm.app].get(
                "relation-0", None
            )
        )
        self.assertIsNotNone(
            self.harness.charm.cluster.relation.data[self.harness.charm.app].get(
                "relation-2", None
            )
        )

        app_data = self.harness.charm.cluster.relation.data[self.harness.charm.app]
        passwords = []
        usernames = []
        for relation in self.provider.client_relations:
            # checking existence of all necessary keys
            self.assertEqual(
                sorted(relation.data[self.harness.charm.app].keys()),
                sorted(["chroot", "endpoints", "password", "tls", "uris", "username"]),
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
