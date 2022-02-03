# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.testing import Harness
from pytest_mock import MockerFixture

from charm import ZookeeperK8sCharm


@pytest.fixture
def harness(mocker: MockerFixture):
    zookeeper_k8s_harness = Harness(ZookeeperK8sCharm)
    zookeeper_k8s_harness.begin()
    yield zookeeper_k8s_harness
    zookeeper_k8s_harness.cleanup()


def test_on_config_changed(mocker: MockerFixture, harness: Harness):
    # test config validation
    _validate_config_original = harness.charm._validate_config
    harness.charm._validate_config = mocker.Mock()
    harness.charm._validate_config.side_effect = [Exception()]
    harness.charm.on.config_changed.emit()
    assert harness.charm.unit.status == BlockedStatus("")
    harness.charm._validate_config = _validate_config_original
    # test pebble not ready
    harness.charm.unit.get_container("zookeeper-k8s").can_connect = mocker.Mock()
    harness.charm.unit.get_container("zookeeper-k8s").can_connect.side_effect = [False, True, True]
    harness.charm.on.config_changed.emit()
    assert harness.charm.unit.status == MaintenanceStatus("waiting for pebble to start")
    # test pebble ready
    spy = mocker.spy(harness.charm.unit.get_container("zookeeper-k8s"), "replan")
    harness.charm.on.zookeeper_k8s_pebble_ready.emit("zookeeper-k8s")
    assert harness.charm.unit.status == ActiveStatus()
    assert spy.call_count == 1


def test_on_update_status(mocker: MockerFixture, harness: Harness):
    # Make sure the service zookeeper-k8s is set
    harness.charm.on.zookeeper_k8s_pebble_ready.emit("zookeeper-k8s")
    harness.charm.unit.get_container("zookeeper-k8s").can_connect = mocker.Mock()
    harness.charm.unit.get_container("zookeeper-k8s").can_connect.side_effect = [
        False,
        True,
        True,
        True,
    ]
    # test service not ready
    original_get_plan = harness.charm.unit.get_container("zookeeper-k8s").get_plan
    harness.charm.unit.get_container("zookeeper-k8s").get_plan = mocker.Mock()
    harness.charm.unit.get_container("zookeeper-k8s").get_plan().services = {}
    harness.charm.on.update_status.emit()
    assert harness.charm.unit.status == WaitingStatus("zookeeper-k8s service not configured yet")
    harness.charm.unit.get_container("zookeeper-k8s").get_plan = original_get_plan
    # test service not running
    harness.charm.unit.get_container("zookeeper-k8s").stop("zookeeper-k8s")
    harness.charm.on.update_status.emit()
    assert harness.charm.unit.status == BlockedStatus("zookeeper-k8s service is not running")
    # test service running
    harness.charm.unit.get_container("zookeeper-k8s").start("zookeeper-k8s")
    harness.charm.on.zookeeper_k8s_pebble_ready.emit("zookeeper-k8s")
    assert harness.charm.unit.status == ActiveStatus()
