#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
from pytest_operator.plugin import OpsTest

from literals import DEPENDENCIES

from . import APP_NAME, TLS_NAME, TLS_OPERATOR_SERIES, ZOOKEEPER_IMAGE
from .helpers import correct_version_running, get_relation_data, ping_servers

logger = logging.getLogger(__name__)

CHANNEL = "3/stable"


@pytest.mark.abort_on_fail
async def test_in_place_upgrade(ops_test: OpsTest, zk_charm):
    await ops_test.model.create_storage_pool("test_pool", "kubernetes")

    await asyncio.gather(
        ops_test.model.deploy(
            APP_NAME,
            application_name=APP_NAME,
            num_units=3,
            channel=CHANNEL,
            trust=True,
            storage={"zookeeper": {"pool": "test_pool", "size": 10240}},
        ),
        ops_test.model.deploy(
            TLS_NAME,
            application_name=TLS_NAME,
            channel="edge",
            num_units=1,
            config={"ca-common-name": "zookeeper"},
            series=TLS_OPERATOR_SERIES,
            # FIXME (certs): Unpin the revision once the charm is fixed
            revision=163,
        ),
    )

    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 3)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, TLS_NAME], status="active", timeout=1000, idle_period=30
    )
    await ops_test.model.add_relation(APP_NAME, TLS_NAME)

    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, TLS_NAME],
            status="active",
            timeout=1000,
            idle_period=30,
        )

    leader_unit = None
    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit
    assert leader_unit

    action = await leader_unit.run_action("pre-upgrade-check")
    await action.wait()
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, idle_period=30
    )

    # ensuring app is safe to upgrade
    assert "upgrade-stack" in get_relation_data(
        model_full_name=ops_test.model_full_name, unit=f"{APP_NAME}/0", endpoint="upgrade"
    )

    test_charm = zk_charm

    await ops_test.model.applications[APP_NAME].refresh(
        path=test_charm, resources={"zookeeper-image": ZOOKEEPER_IMAGE}
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], timeout=1000, idle_period=180, raise_on_error=False
    )

    action = await leader_unit.run_action("resume-upgrade")
    action.wait()
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, idle_period=180
    )

    assert ping_servers(ops_test), "Servers not all running"
    assert correct_version_running(
        ops_test=ops_test, expected_version=DEPENDENCIES["service"]["version"]
    ), "Wrong version running"
