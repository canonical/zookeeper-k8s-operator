# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import patch

import pytest
from tests.unit.test_charm import PropertyMock


@pytest.fixture(autouse=True)
def patched_wait(mocker):
    mocker.patch("tenacity.nap.time")


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
