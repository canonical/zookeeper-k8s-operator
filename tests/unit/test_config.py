#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from ops.testing import Harness

from charm import ZooKeeperCharm
from literals import CHARM_KEY, CONTAINER, PEER, REL_NAME, SUBSTRATE

logger = logging.getLogger(__name__)

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)

    if SUBSTRATE == "k8s":
        harness.set_can_connect(CONTAINER, True)

    harness.add_relation("restart", CHARM_KEY)
    harness.add_relation(PEER, CHARM_KEY)
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_etc_hosts_entries_empty_if_not_all_units_related(harness):
    with harness.hooks_disabled():
        harness.set_planned_units(3)
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {"ip": "aragorn", "hostname": "legolas", "fqdn": "gimli"},
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/1",
            {"ip": "aragorn", "hostname": "legolas", "fqdn": "gimli"},
        )

    assert not harness.charm.config_manager.etc_hosts_entries


def test_etc_hosts_entries_empty_if_unit_not_yet_set(harness):
    with harness.hooks_disabled():
        harness.set_planned_units(2)
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {"ip": "aragorn", "hostname": "legolas", "fqdn": "gimli"},
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/1",
            {"ip": "sam", "hostname": "frodo", "state": "started"},
        )

    assert not harness.charm.config_manager.etc_hosts_entries

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"state": "started"}
        )

    assert "sam" not in "".join(harness.charm.config_manager.etc_hosts_entries)

    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1", {"fqdn": "merry"}
        )

    assert "sam" in "".join(harness.charm.config_manager.etc_hosts_entries)


def test_build_static_properties_removes_necessary_rows(harness):
    properties = [
        "clientPort=2181",
        "authProvider.sasl=org.apache.zookeeper.server.auth.SASLAuthenticationProvider",
        "maxClientCnxns=60",
    ]

    static = harness.charm.config_manager.build_static_properties(properties=properties)

    assert len(static) == 2
    assert "clientPort" not in "".join(static)


def test_server_jvmflags_has_opts(harness):
    assert "-Djava.security.auth.login.config" in "".join(
        harness.charm.config_manager.server_jvmflags
    )


def test_jaas_users_are_added(harness):
    with harness.hooks_disabled():
        app_id = harness.add_relation(REL_NAME, "application")
        harness.update_relation_data(app_id, "application", {"chroot": "app"})
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"relation-2": "password"}
        )

    assert len(harness.charm.config_manager.jaas_users) == 1


def test_multiple_jaas_users_are_added(harness):
    with harness.hooks_disabled():
        app_1_id = harness.add_relation(REL_NAME, "application")
        app_2_id = harness.add_relation(REL_NAME, "application2")
        harness.update_relation_data(app_1_id, "application", {"chroot": "app"})
        harness.update_relation_data(app_2_id, "application2", {"chroot": "app2"})
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            CHARM_KEY,
            {"relation-2": "password", "relation-3": "password"},
        )

    assert len(harness.charm.config_manager.jaas_users) == 2


def test_tls_enabled(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"tls": "enabled"}
        )

    assert "ssl.clientAuth=none" in harness.charm.config_manager.zookeeper_properties


def test_tls_disabled(harness):
    assert "ssl.clientAuth=none" not in harness.charm.config_manager.zookeeper_properties


def test_tls_switching_encryption(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"switching-encryption": "started"}
        )

        assert "portUnification=true" in harness.charm.config_manager.zookeeper_properties

        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"switching-encryption": ""}
        )

        assert "portUnification=true" not in harness.charm.config_manager.zookeeper_properties


def test_tls_ssl_quorum(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"quorum": "non-ssl"}
        )

        assert "sslQuorum=true" not in harness.charm.config_manager.zookeeper_properties

        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"quorum": "ssl"}
        )

        assert "sslQuorum=true" not in harness.charm.config_manager.zookeeper_properties

        harness.update_relation_data(
            harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"certificate": "keep it safe"}
        )

        assert "sslQuorum=true" in harness.charm.config_manager.zookeeper_properties

def test_properties_tls_uses_passwords(harness):
    with harness.hooks_disabled():
        harness.update_relation_data(
            harness.charm.state.peer_relation.id, CHARM_KEY, {"tls": "enabled"}
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {"keystore-password": "mellon", "truststore-password": "friend"},
        )

    assert "ssl.keyStore.password=mellon" in harness.charm.config_manager.zookeeper_properties
    assert "ssl.trustStore.password=friend" in harness.charm.config_manager.zookeeper_properties


def test_config_changed_updates_properties_jaas_hosts(harness):
    with (
        patch(
            "managers.config.ConfigManager.build_static_properties", return_value=["gandalf=white"]
        ),
        patch("managers.config.ConfigManager.static_properties", return_value="gandalf=grey"),
        patch("managers.config.ConfigManager.set_jaas_config"),
        patch("managers.config.ConfigManager.set_zookeeper_properties") as set_props,
        patch("managers.config.ConfigManager.set_server_jvmflags"),
    ):
        harness.charm.config_manager.config_changed()
        set_props.assert_called_once()

    with (
        patch("managers.config.ConfigManager.jaas_config", return_value="gandalf=white"),
        patch("workload.ZKWorkload.read", return_value=["gandalf=grey"]),
        patch("managers.config.ConfigManager.set_zookeeper_properties"),
        patch("managers.config.ConfigManager.set_jaas_config") as set_jaas,
        patch("managers.config.ConfigManager.set_server_jvmflags"),
    ):
        harness.charm.config_manager.config_changed()
        set_jaas.assert_called_once()


def test_update_environment(harness):
    example_env = [
        "",
        "KAFKA_OPTS=orcs -Djava=wargs -Dkafka=goblins",
        "SERVER_JVMFLAGS=dwarves -Djava=elves -Dzookeeper=men",
    ]
    example_new_env = {"SERVER_JVMFLAGS": "gimli -Djava=legolas -Dzookeeper=aragorn"}

    with (
        patch("workload.ZKWorkload.read", return_value=example_env),
        patch("workload.ZKWorkload.write") as patched_write,
    ):
        harness.charm.config_manager._update_environment(example_new_env)

        assert all(
            updated in patched_write.call_args.kwargs["content"]
            for updated in ["gimli", "legolas", "aragorn"]
        )
        assert "KAFKA_OPTS" in patched_write.call_args.kwargs["content"]
        assert patched_write.call_args.kwargs["path"] == "/etc/environment"
