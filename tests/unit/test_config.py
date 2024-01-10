#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import io
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from config import ZooKeeperConfig
from ops.testing import Harness

from charm import ZooKeeperK8sCharm
from literals import CHARM_KEY, PEER, REL_NAME

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


def test_build_static_properties_removes_necessary_rows():
    properties = [
        "clientPort=2181",
        "authProvider.sasl=org.apache.zookeeper.server.auth.SASLAuthenticationProvider",
        "maxClientCnxns=60",
        "dynamicConfigFile=/etc/zookeeper/zoo.cfg.dynamic.100000041",
    ]

    static = ZooKeeperConfig.build_static_properties(properties=properties)

    assert len(static) == 3
    assert "clientPort" not in "".join(static)


def test_server_jvmflags_has_opts(harness):
    server_jvmflags = ZooKeeperConfig(harness.charm).server_jvmflags
    assert (
        f"-Djava.security.auth.login.config={harness.charm.zookeeper_config.jaas_filepath}"
        in server_jvmflags
    )


def test_jaas_users_are_added(harness):
    with harness.hooks_disabled():
        harness.add_relation(REL_NAME, "application")
        harness.update_relation_data(
            harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        harness.update_relation_data(
            harness.charm.peer_relation.id, CHARM_KEY, {"relation-2": "password"}
        )

    assert len(harness.charm.zookeeper_config.jaas_users) == 1


def test_multiple_jaas_users_are_added(harness):
    with harness.hooks_disabled():
        harness.add_relation(REL_NAME, "application")
        harness.add_relation(REL_NAME, "application2")
        harness.update_relation_data(
            harness.charm.provider.client_relations[0].id, "application", {"chroot": "app"}
        )
        harness.update_relation_data(
            harness.charm.provider.client_relations[1].id, "application2", {"chroot": "app2"}
        )
        harness.update_relation_data(
            harness.charm.peer_relation.id,
            CHARM_KEY,
            {"relation-2": "password", "relation-3": "password"},
        )

    assert len(harness.charm.zookeeper_config.jaas_users) == 2


def test_tls_enabled(harness):
    with harness.hooks_disabled():
        with patch(
            "ops.model.Container.pull",
            return_value=io.StringIO("dynamicConfigFile=/gandalf/the/grey"),
        ):
            harness.update_relation_data(
                harness.charm.peer_relation.id, CHARM_KEY, {"tls": "enabled"}
            )
            assert "ssl.clientAuth=none" in harness.charm.zookeeper_config.zookeeper_properties


def test_tls_disabled(harness):
    with patch(
        "ops.model.Container.pull", return_value=io.StringIO("dynamicConfigFile=/gandalf/the/grey")
    ):
        assert "ssl.clientAuth=none" not in harness.charm.zookeeper_config.zookeeper_properties


def test_tls_upgrading(harness):
    with harness.hooks_disabled():
        with patch(
            "ops.model.Container.pull",
            return_value=io.StringIO("dynamicConfigFile=/gandalf/the/grey"),
        ):
            harness.update_relation_data(
                harness.charm.peer_relation.id, CHARM_KEY, {"upgrading": "started"}
            )
            assert "portUnification=true" in harness.charm.zookeeper_config.zookeeper_properties

            harness.update_relation_data(
                harness.charm.peer_relation.id, CHARM_KEY, {"upgrading": ""}
            )
            assert (
                "portUnification=true" not in harness.charm.zookeeper_config.zookeeper_properties
            )


def test_tls_ssl_quorum(harness):
    with harness.hooks_disabled():
        with patch(
            "ops.model.Container.pull",
            return_value=io.StringIO("dynamicConfigFile=/gandalf/the/grey"),
        ):
            harness.update_relation_data(
                harness.charm.peer_relation.id, CHARM_KEY, {"quorum": "ssl"}
            )
            assert "sslQuorum=true" in harness.charm.zookeeper_config.zookeeper_properties

            harness.update_relation_data(
                harness.charm.peer_relation.id, CHARM_KEY, {"quorum": "non-ssl"}
            )
            assert "sslQuorum=true" not in harness.charm.zookeeper_config.zookeeper_properties


def test_properties_tls_uses_passwords(harness):
    with patch(
        "ops.model.Container.pull", return_value=io.StringIO("dynamicConfigFile=/gandalf/the/grey")
    ):
        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.charm.peer_relation.id, CHARM_KEY, {"tls": "enabled"}
            )
            harness.update_relation_data(
                harness.charm.peer_relation.id, f"{CHARM_KEY}/0", {"keystore-password": "mellon"}
            )
        assert (
            "ssl.keyStore.password=mellon" in harness.charm.zookeeper_config.zookeeper_properties
        )
        assert (
            "ssl.trustStore.password=mellon" in harness.charm.zookeeper_config.zookeeper_properties
        )


def test_properties_tls_gets_dynamic_config_file_property(harness):
    with open("/tmp/zookeeper.properties", "w") as fp, patch(
        "ops.model.Container.pull", return_value=io.StringIO("dynamicConfigFile=/gandalf/the/grey")
    ):
        fp.write("ensuring file exists")
        with harness.hooks_disabled():
            harness.update_relation_data(
                harness.charm.peer_relation.id, CHARM_KEY, {"tls": "enabled"}
            )

        assert (
            "dynamicConfigFile=/gandalf/the/grey"
            in harness.charm.zookeeper_config.zookeeper_properties
        )
