#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from ops.pebble import ExecError
from ops.testing import Harness

from charm import ZooKeeperCharm
from literals import CHARM_KEY, CONTAINER, SUBSTRATE

logger = logging.getLogger(__name__)

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


# override conftest fixture
@pytest.fixture(autouse=False)
def patched_healthy():
    yield


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)

    if SUBSTRATE == "k8s":
        harness.set_can_connect(CONTAINER, True)

    harness.add_relation("restart", CHARM_KEY)
    upgrade_rel_id = harness.add_relation("upgrade", CHARM_KEY)
    harness.update_relation_data(upgrade_rel_id, f"{CHARM_KEY}/0", {"state": "idle"})
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_healthy_does_not_raise(harness, patched_healthy):
    with (
        patch("ops.model.Container.get_service"),
        patch("ops.pebble.ServiceInfo.is_running"),
        patch(
            "workload.ZKWorkload.exec", side_effect=ExecError(["It's"], 1, "dangerous", "business")
        ),
    ):
        assert not harness.charm.workload.healthy
