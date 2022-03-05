# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.testing import Harness
from pytest_mock import MockerFixture

from charm import CharmError, ZooKeeperK8sCharm


@pytest.fixture
def harness(mocker: MockerFixture):
    mocker.patch("charm.socket.getfqdn", return_value="zookeeper-0")
    zookeeper_harness = Harness(ZooKeeperK8sCharm)
    yield zookeeper_harness
    zookeeper_harness.cleanup()


def test_on_config_changed(mocker: MockerFixture, harness: Harness):
    harness.begin()
    # test config validation
    _validate_config_original = harness.charm._validate_config
    harness.charm._validate_config = mocker.Mock()
    harness.charm._validate_config.side_effect = [CharmError("invalid configuration")]
    harness.charm.on.config_changed.emit()
    assert harness.charm.unit.status == BlockedStatus("invalid configuration")
    harness.charm._validate_config = _validate_config_original
    # test pebble not ready
    harness.charm.unit.get_container("zookeeper").can_connect = mocker.Mock()
    harness.charm.unit.get_container("zookeeper").can_connect.side_effect = [False]
    harness.charm.on.config_changed.emit()
    assert harness.charm.unit.status == MaintenanceStatus("waiting for pebble to start")
    harness.charm.unit.get_container("zookeeper").can_connect.side_effect = None
    # test pebble ready
    harness.charm.on.zookeeper_pebble_ready.emit("zookeeper")
    zookeeper_properties = """clientPort=2181

    # comment
    invalid-line
    """
    harness.update_config({"zookeeper-properties": zookeeper_properties})
    assert harness.charm.unit.status == ActiveStatus()
    assert harness.charm.zookeeper_properties == {"ZOOKEEPER_CLIENT_PORT": "2181"}


def test_on_update_status(mocker: MockerFixture, harness: Harness):
    harness.begin()
    # test service not ready
    harness.charm.on.update_status.emit()
    assert harness.charm.unit.status == WaitingStatus("zookeeper service not configured yet")
    # test service ready
    harness.charm.on.zookeeper_pebble_ready.emit("zookeeper")
    assert harness.charm.unit.status == ActiveStatus()
    # test service not running
    harness.charm.unit.get_container("zookeeper").stop("zookeeper")
    harness.charm.on.update_status.emit()
    assert harness.charm.unit.status == BlockedStatus("zookeeper service is not running")


def test_scaling(harness: Harness):
    harness.begin()
    # Set current unit the leader
    harness.set_leader(True)
    # Add remote unit
    remote_unit = f"{harness.charm.app.name}/1"
    relation_id = harness.add_relation("cluster", harness.charm.app.name)
    harness.add_relation_unit(relation_id, remote_unit)
    harness.update_relation_data(
        relation_id,
        remote_unit,
        {
            "host": "zookeeper-1",
        },
    )

    assert [
        "zookeeper-0:2888:3888",
        "zookeeper-1:2888:3888",
    ] == harness.charm.cluster.cluster_addresses
    assert [
        "zookeeper-0:2181",
        "zookeeper-1:2181",
    ] == harness.charm.cluster.client_addresses


def test_zookeeper_relation(harness: Harness):
    harness.begin_with_initial_hooks()
    # Set current unit the leader
    harness.set_leader(True)

    # Add remote unit
    remote_app = "kafka"
    relation_id = harness.add_relation("zookeeper", remote_app)
    harness.add_relation_unit(relation_id, f"{remote_app}/0")

    assert harness.get_relation_data(relation_id, harness.charm.app.name) == {
        "hosts": "zookeeper-0:2181"
    }
