#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging

import pytest
import requests
from pytest_operator.plugin import OpsTest

from literals import JMX_PORT, METRICS_PROVIDER_PORT

from . import APP_NAME, SERIES, ZOOKEEPER_IMAGE
from .helpers import check_jaas_config, get_address, ping_servers

logger = logging.getLogger(__name__)

DUMMY_NAME_1 = "app"
DUMMY_NAME_2 = "appii"


@pytest.mark.abort_on_fail
async def test_deploy_charms_relate_active(ops_test: OpsTest, zk_charm):
    app_charm = await ops_test.build_charm("tests/integration/app-charm")

    await asyncio.gather(
        ops_test.model.deploy(
            zk_charm,
            application_name=APP_NAME,
            num_units=3,
            resources={"zookeeper-image": ZOOKEEPER_IMAGE},
            series=SERIES,
            trust=True,
            config={"expose-external": "nodeport"},
        ),
        ops_test.model.deploy(
            app_charm,
            application_name=DUMMY_NAME_1,
            num_units=1,
            series=SERIES,
        ),
    )
    await ops_test.model.wait_for_idle(apps=[APP_NAME, DUMMY_NAME_1])
    await ops_test.model.add_relation(APP_NAME, DUMMY_NAME_1)
    await ops_test.model.wait_for_idle(apps=[APP_NAME, DUMMY_NAME_1])
    assert ops_test.model.applications[APP_NAME].status == "active"
    assert ops_test.model.applications[DUMMY_NAME_1].status == "active"

    assert ping_servers(ops_test)
    for unit in ops_test.model.applications[APP_NAME].units:
        jaas_config = check_jaas_config(model_full_name=ops_test.model_full_name, unit=unit.name)
        assert "sync" in jaas_config
        assert "super" in jaas_config

        # includes the related unit
        assert len(jaas_config) == 3


@pytest.mark.abort_on_fail
async def test_metrics_endpoints(ops_test: OpsTest):
    unit_address = await get_address(ops_test=ops_test)
    jmx_exporter_url = f"http://{unit_address}:{JMX_PORT}/metrics"
    zk_exporter_url = f"http://{unit_address}:{METRICS_PROVIDER_PORT}/metrics"

    jmx_resp = requests.get(jmx_exporter_url)
    zk_resp = requests.get(zk_exporter_url)

    assert jmx_resp.ok
    assert zk_resp.ok


@pytest.mark.abort_on_fail
async def test_deploy_multiple_charms_relate_active(ops_test: OpsTest):
    app_charm = await ops_test.build_charm("tests/integration/app-charm")

    await ops_test.model.deploy(
        app_charm,
        application_name=DUMMY_NAME_2,
        num_units=1,
        series=SERIES,
    )
    await ops_test.model.wait_for_idle(apps=[APP_NAME, DUMMY_NAME_2])
    await ops_test.model.add_relation(APP_NAME, DUMMY_NAME_2)
    await ops_test.model.wait_for_idle(apps=[APP_NAME, DUMMY_NAME_2])
    assert ops_test.model.applications[APP_NAME].status == "active"
    assert ops_test.model.applications[DUMMY_NAME_2].status == "active"

    assert ping_servers(ops_test)
    for unit in ops_test.model.applications[APP_NAME].units:
        jaas_config = check_jaas_config(model_full_name=ops_test.model_full_name, unit=unit.name)
        assert "sync" in jaas_config
        assert "super" in jaas_config

        # includes the related units
        assert len(jaas_config) == 4


@pytest.mark.abort_on_fail
async def test_scale_up_gets_new_jaas_users(ops_test: OpsTest):
    await ops_test.model.applications[APP_NAME].scale(scale=4)
    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 4)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, idle_period=30
    )

    assert ping_servers(ops_test)
    for unit in ops_test.model.applications[APP_NAME].units:
        jaas_config = check_jaas_config(model_full_name=ops_test.model_full_name, unit=unit.name)
        assert "sync" in jaas_config
        assert "super" in jaas_config

        # includes the related units
        assert len(jaas_config) == 4


@pytest.mark.abort_on_fail
async def test_remove_applications(ops_test: OpsTest):
    await ops_test.model.applications[DUMMY_NAME_1].remove()
    await ops_test.model.applications[DUMMY_NAME_2].remove()
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, idle_period=60
    )
    assert ping_servers(ops_test)
    for unit in ops_test.model.applications[APP_NAME].units:
        jaas_config = check_jaas_config(model_full_name=ops_test.model_full_name, unit=unit.name)
        assert "sync" in jaas_config
        assert "super" in jaas_config
        # doesn't include the departed units
        assert len(jaas_config) == 2
