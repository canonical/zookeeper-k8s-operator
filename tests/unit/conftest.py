# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest
from ops import JujuVersion
from tests.unit.test_charm import PropertyMock


@pytest.fixture(autouse=True)
def patched_idle(mocker, request):
    if "nopatched_idle" in request.keywords:
        yield
    else:
        yield mocker.patch(
            "events.upgrade.ZKUpgradeEvents.idle", new_callable=PropertyMock, return_value=True
        )


@pytest.fixture(autouse=True)
def patched_wait(mocker):
    mocker.patch("tenacity.nap.time")


@pytest.fixture(autouse=True)
def patched_set_rolling_update_partition(mocker):
    mocker.patch("events.upgrade.ZKUpgradeEvents._set_rolling_update_partition")


@pytest.fixture(autouse=True)
def patched_pebble_restart(mocker):
    mocker.patch("ops.model.Container.restart")


@pytest.fixture(autouse=True)
def patched_healthy(mocker):
    mocker.patch("workload.ZKWorkload.healthy", new_callable=PropertyMock, return_value=True)


@pytest.fixture(autouse=True)
def patched_etc_hosts_environment():
    with (
        patch("managers.config.ConfigManager.set_etc_hosts"),
        patch("managers.config.ConfigManager.set_server_jvmflags"),
    ):
        yield


@pytest.fixture(autouse=True)
def juju_has_secrets(mocker):
    """Using Juju3 we should always have secrets available."""
    mocker.patch.object(JujuVersion, "has_secrets", new_callable=PropertyMock).return_value = True


@pytest.fixture(autouse=True)
def patched_version(mocker, request):
    if "nopatched_version" in request.keywords:
        yield
    else:
        yield mocker.patch("workload.ZKWorkload.get_version", return_value=""),

