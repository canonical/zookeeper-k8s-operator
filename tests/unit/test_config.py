#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import patch

import ops.testing
import pytest
import yaml
from ops.testing import Harness

from charm import ZooKeeperK8sCharm
from config import ZooKeeperConfig
from literals import CHARM_KEY

ops.testing.SIMULATE_CAN_CONNECT = True


@pytest.fixture(autouse=True)
def patched_pull():
    with patch("ops.model.Container.pull"):
        yield


CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture(scope="function")
def harness():
    harness = Harness(ZooKeeperK8sCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    harness.begin()
    return harness


def test_build_static_properties_removes_necessary_rows():
    properties = [
        "clientPort=2181",
        "authProvider.sasl=org.apache.zookeeper.server.auth.SASLAuthenticationProvider",
        "maxClientCnxns=60",
    ]

    static = ZooKeeperConfig.build_static_properties(properties=properties)

    assert len(static) == 2
    assert "clientPort" not in "".join(static)
    assert "dynamicConfigFile" not in "".join(static)


def test_kafka_opts_has_jaas(harness):
    opts = harness.charm.zookeeper_config.kafka_opts
    assert (
        f"-Djava.security.auth.login.config={harness.charm.zookeeper_config.default_config_path}/zookeeper-jaas.cfg"
        in opts
    )


def test_jaas_users_are_added(harness):
    with harness.hooks_disabled():
        rel_id = harness.add_relation("zookeeper", "application")
        peer_id = harness.add_relation("cluster", CHARM_KEY)
        harness.update_relation_data(rel_id, "application", {"chroot": "app"})
        harness.update_relation_data(peer_id, CHARM_KEY, {"relation-0": "password"})

    assert len(harness.charm.zookeeper_config.jaas_users) == 1


def test_multiple_jaas_users_are_added(harness):
    with harness.hooks_disabled():
        rel_id = harness.add_relation("zookeeper", "application")
        rel_id_2 = harness.add_relation("zookeeper", "application2")
        peer_id = harness.add_relation("cluster", CHARM_KEY)

        harness.update_relation_data(rel_id, "application", {"chroot": "app"})
        harness.update_relation_data(rel_id_2, "application2", {"chroot": "app2"})
        harness.update_relation_data(
            peer_id,
            CHARM_KEY,
            {"relation-0": "password", "relation-1": "password"},
        )

    assert len(harness.charm.zookeeper_config.jaas_users) == 2


def test_tls_enabled(harness):
    with harness.hooks_disabled():
        peer_id = harness.add_relation("cluster", CHARM_KEY)
        harness.update_relation_data(peer_id, CHARM_KEY, {"tls": "enabled"})
        assert "ssl.clientAuth=none" in harness.charm.zookeeper_config.zookeeper_properties


def test_tls_disabled(harness):
    with harness.hooks_disabled():
        harness.add_relation("cluster", CHARM_KEY)
    assert "ssl.clientAuth=none" not in harness.charm.zookeeper_config.zookeeper_properties


def test_tls_upgrading(harness):
    with harness.hooks_disabled():
        peer_id = harness.add_relation("cluster", CHARM_KEY)
        harness.update_relation_data(peer_id, CHARM_KEY, {"upgrading": "started"})
        assert "portUnification=true" in harness.charm.zookeeper_config.zookeeper_properties

        harness.update_relation_data(peer_id, CHARM_KEY, {"upgrading": ""})
        assert "portUnification=true" not in harness.charm.zookeeper_config.zookeeper_properties


def test_tls_ssl_quorum(harness):
    with harness.hooks_disabled():
        peer_id = harness.add_relation("cluster", CHARM_KEY)
        harness.update_relation_data(peer_id, CHARM_KEY, {"quorum": "ssl"})
        assert "sslQuorum=true" in harness.charm.zookeeper_config.zookeeper_properties

        harness.update_relation_data(peer_id, CHARM_KEY, {"quorum": "non-ssl"})
        assert "sslQuorum=true" not in harness.charm.zookeeper_config.zookeeper_properties
