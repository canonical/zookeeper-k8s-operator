#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import re
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from charm import ZooKeeperK8sCharm
from literals import CHARM_KEY, CONTAINER, PEER, REL_NAME
from ops.charm import RelationBrokenEvent
from ops.testing import Harness

logger = logging.getLogger(__name__)

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))

CustomRelation = namedtuple("Relation", ["id"])


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperK8sCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    harness.add_relation(REL_NAME, "application")
    peer_rel_id = harness.add_relation("restart", CHARM_KEY)
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.set_can_connect(CONTAINER, True)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_relation_config_new_relation_no_chroot(harness):
    with harness.hooks_disabled():
        config = harness.charm.provider.relation_config(
            relation=harness.charm.provider.client_relations[0]
        )
    assert not config


def test_relation_config_new_relation(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, CHARM_KEY, {"relation-0": "password"}
        )

        config = harness.charm.provider.relation_config(
            relation=harness.charm.provider.client_relations[0]
        )

    assert config == {
        "username": "relation-0",
        "password": "password",
        "chroot": "/app",
        "acl": "cdrwa",
        "acls-added": "true",
    }


def test_relation_config_new_relation_defaults_to_database(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.provider.client_relations[0].id, "application", {"database": "app"}
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id, CHARM_KEY, {"relation-0": "password"}
        )

        config = harness.charm.provider.relation_config(
            relation=harness.charm.provider.client_relations[0]
        )

        assert config == {
            "username": "relation-0",
            "password": "password",
            "chroot": "/app",
            "acl": "cdrwa",
            "acls-added": "true",
        }


def test_relation_config_new_relation_empty_password(harness):
    harness.update_relation_data(
        harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
    )

    with patch("provider.generate_password", return_value="mellon"):
        config = harness.charm.provider.relation_config(
            relation=harness.charm.provider.client_relations[0]
        )

    assert config == {
        "username": "relation-0",
        "password": "mellon",
        "chroot": "/app",
        "acl": "cdrwa",
        "acls-added": "false",
    }


def test_relation_config_new_relation_app_permissions(harness):
    harness.update_relation_data(
        harness.charm.provider.client_relations[0].id,
        "application",
        {"chroot": "app", "chroot-acl": "rw"},
    )

    with patch("provider.generate_password", return_value="mellon"):
        config = harness.charm.provider.relation_config(
            relation=harness.charm.provider.client_relations[0]
        )

    assert config == {
        "username": "relation-0",
        "password": "mellon",
        "chroot": "/app",
        "acl": "rw",
        "acls-added": "false",
    }


def test_relation_config_new_relation_skips_relation_broken(harness):
    harness.update_relation_data(
        harness.charm.provider.client_relations[0].id,
        "application",
        {"chroot": "app", "chroot-acl": "rw"},
    )

    custom_relation = CustomRelation(id=harness.charm.provider.client_relations[0].id)

    config = harness.charm.provider.relation_config(
        relation=harness.charm.provider.client_relations[0],
        event=RelationBrokenEvent(handle="", relation=custom_relation),
    )

    assert not config


def test_relations_config_multiple_relations(harness):
    harness.add_relation(REL_NAME, "new_application")
    harness.update_relation_data(
        harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
    )
    harness.update_relation_data(
        harness.charm.provider.client_relations[1].id, "new_application", {"chroot": "new_app"}
    )

    with patch("provider.generate_password", return_value="mellon"):
        relations_config = harness.charm.provider.relations_config()

    assert relations_config == {
        "0": {
            "username": "relation-0",
            "password": "mellon",
            "chroot": "/app",
            "acl": "cdrwa",
            "acls-added": "false",
        },
        "3": {
            "username": "relation-3",
            "password": "mellon",
            "chroot": "/new_app",
            "acl": "cdrwa",
            "acls-added": "false",
        },
    }


def test_build_acls(harness):
    harness.add_relation(REL_NAME, "new_application")
    harness.update_relation_data(
        harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
    )
    harness.update_relation_data(
        harness.charm.provider.client_relations[1].id,
        "new_application",
        {"chroot": "new_app", "chroot-acl": "rw"},
    )

    acls = harness.charm.provider.build_acls()

    assert len(acls) == 2
    assert sorted(acls.keys()) == ["/app", "/new_app"]
    assert isinstance(acls["/app"], list)

    new_app_acl = acls["/new_app"][0]

    assert new_app_acl.acl_list == ["READ", "WRITE"]
    assert new_app_acl.id.scheme == "sasl"
    assert new_app_acl.id.id == "relation-3"


def test_relations_config_values_for_key(harness):
    harness.add_relation(REL_NAME, "new_application")
    harness.update_relation_data(
        harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
    )
    harness.update_relation_data(
        harness.charm.provider.client_relations[1].id,
        "new_application",
        {"chroot": "new_app", "chroot-acl": "rw"},
    )

    config_values = harness.charm.provider.relations_config_values_for_key(key="username")

    assert config_values == {"relation-3", "relation-0"}


def test_is_child_of(harness):
    chroot = "/gandalf/the/white"
    chroots = {"/gandalf", "/saruman"}

    assert harness.charm.provider._is_child_of(path=chroot, chroots=chroots)


def test_is_child_of_not(harness):
    chroot = "/the/one/ring"
    chroots = {"/gandalf", "/saruman"}

    assert not harness.charm.provider._is_child_of(path=chroot, chroots=chroots)


def test_port_updates_if_tls(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.update_relation_data(
            harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
        )

        # checking if ssl port and ssl flag are passed
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}/0",
            {"private-address": "treebeard", "state": "started"},
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            CHARM_KEY,
            {"quorum": "ssl", "relation-0": "mellon"},
        )
        harness.charm.provider.apply_relation_data()

    for relation in harness.charm.provider.client_relations:
        uris = relation.data[harness.charm.app].get("uris", "")
        ssl = relation.data[harness.charm.app].get("tls", "")

        assert str(harness.charm.cluster.secure_client_port) in uris
        assert ssl == "enabled"

    with harness.hooks_disabled():
        # checking if normal port and non-ssl flag are passed
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}/0",
            {"private-address": "treebeard", "state": "started", "quorum": "non-ssl"},
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            CHARM_KEY,
            {"quorum": "non-ssl", "relation-0": "mellon"},
        )
        harness.charm.provider.apply_relation_data()

    for relation in harness.charm.provider.client_relations:
        uris = relation.data[harness.charm.app].get("uris", "")
        ssl = relation.data[harness.charm.app].get("tls", "")

        assert str(harness.charm.cluster.client_port) in uris
        assert ssl == "disabled"


def test_provider_relation_data_updates_port_if_stable_and_ready(harness):
    with (
        patch("provider.ZooKeeperProvider.apply_relation_data", return_value=None) as patched,
        patch("cluster.ZooKeeperCluster.stable", return_value=True),
        patch("provider.ZooKeeperProvider.ready", return_value=True),
        patch("charm.ZooKeeperK8sCharm.config_changed", return_value=True),
    ):
        harness.set_leader(True)
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            CHARM_KEY,
            {"quorum": "non-ssl"},
        )

        patched.assert_called_once()


def test_apply_relation_data(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.add_relation(REL_NAME, "new_application")
        harness.update_relation_data(
            harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        harness.update_relation_data(
            harness.charm.provider.client_relations[1].id,
            "new_application",
            {"chroot": "new_app", "chroot-acl": "rw"},
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}/0",
            {"private-address": "treebeard", "state": "started"},
        )
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}/1",
            {"private-address": "shelob", "state": "ready"},
        )
        harness.add_relation_unit(harness.charm.cluster.relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}/2",
            {"private-address": "balrog", "state": "started"},
        )
        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            CHARM_KEY,
            {"relation-0": "mellon", "relation-3": "friend"},
        )

    with (
        patch("cluster.ZooKeeperCluster.stable", return_value=True),
        patch("provider.ZooKeeperProvider.ready", return_value=True),
        patch("charm.ZooKeeperK8sCharm.config_changed", return_value=True),
    ):
        harness.charm.provider.apply_relation_data()

    assert harness.charm.cluster.relation.data[harness.charm.app].get("relation-0", None)
    assert harness.charm.cluster.relation.data[harness.charm.app].get("relation-3", None)

    app_data = harness.charm.cluster.relation.data[harness.charm.app]
    passwords = []
    usernames = []
    for relation in harness.charm.provider.client_relations:
        # checking existence of all necessary keys

        assert sorted(relation.data[harness.charm.app].keys()) == sorted(
            ["chroot", "endpoints", "password", "tls", "uris", "username"]
        )

        username = relation.data[harness.charm.app]["username"]
        password = relation.data[harness.charm.app]["password"]

        # checking ZK app data got updated
        assert username in app_data
        assert password == app_data.get(username, None)

        # checking unique passwords and usernames for all relations
        assert username not in usernames
        assert password not in passwords

        # checking multiple endpoints and uris
        assert len(relation.data[harness.charm.app]["endpoints"].split(",")) == 2
        assert len(relation.data[harness.charm.app]["uris"].split(",")) == 2

        for uri in relation.data[harness.charm.app]["uris"].split(","):
            # checking client_port in uri
            assert re.search(r":[\d]+", uri)

        assert relation.data[harness.charm.app]["uris"].endswith(
            relation.data[harness.charm.app]["chroot"]
        )

        passwords.append(username)
        usernames.append(password)
