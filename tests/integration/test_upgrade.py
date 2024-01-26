#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import time
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from literals import DEPENDENCIES

from .helpers import correct_version_running, get_relation_data, ping_servers

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]

# FIXME: update this to 'stable' when `pre-upgrade-check` is released to 'stable'
CHANNEL = "edge"


@pytest.mark.abort_on_fail
@pytest.mark.skip(reason="Not yet released to stable")
async def test_in_place_upgrade(ops_test: OpsTest):
    build_charm = asyncio.ensure_future(ops_test.build_charm("."))

    await ops_test.model.deploy(APP_NAME, application_name=APP_NAME, num_units=3, channel=CHANNEL)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, idle_period=60
    )

    leader_unit = None
    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit
    assert leader_unit

    action = await leader_unit.run_action("pre-upgrade-check")
    await action.wait()

    # ensure action completes
    time.sleep(10)

    # ensuring app is safe to upgrade
    assert "upgrade-stack" in get_relation_data(
        model_full_name=ops_test.model_full_name, unit=f"{APP_NAME}/0", endpoint="upgrade"
    )

    test_charm = await build_charm

    await ops_test.model.applications[APP_NAME].refresh(path=test_charm)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, idle_period=120
    )

    assert ping_servers(ops_test), "Servers not all running"
    assert correct_version_running(
        ops_test=ops_test, expected_version=DEPENDENCIES["service"]["version"]
    ), "Wrong version running"
