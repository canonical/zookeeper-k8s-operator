#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
import time
from pathlib import Path

import pytest
import yaml
from lightkube.core.client import AsyncClient
from lightkube.resources.core_v1 import Pod
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import check_key, get_password, ping_servers, write_key

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_deploy_active(ops_test: OpsTest):
    charm = await ops_test.build_charm(".")
    await ops_test.model.deploy(
        charm,
        application_name=APP_NAME,
        num_units=3,
        resources={"zookeeper-image": "jardon/zookeeper:3.6.3"},
        series="focal",
    ),
    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 3)
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    assert ops_test.model.applications[APP_NAME].status == "active"

    await ops_test.model.set_config({"update-status-hook-interval": "60m"})


@pytest.mark.abort_on_fail
async def test_simple_scale_up(ops_test: OpsTest):
    await ops_test.model.applications[APP_NAME].scale(scale=6)
    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 6)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert ping_servers(ops_test)


@pytest.mark.abort_on_fail
@pytest.mark.skip
async def test_simple_scale_down(ops_test: OpsTest):
    await ops_test.model.applications[APP_NAME].scale(scale=3)
    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 3)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert ping_servers(ops_test)


@pytest.mark.abort_on_fail
@pytest.mark.skip
async def test_scale_up_replication(ops_test: OpsTest):
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert ping_servers(ops_test)
    host = ops_test.model.applications[APP_NAME].units[0].public_address
    model_full_name = ops_test.model_full_name
    password = get_password(model_full_name or "")
    write_key(host=host, password=password)
    await ops_test.model.applications[APP_NAME].scale(scale=4)
    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 4)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    check_key(host=host, password=password)


@pytest.mark.abort_on_fail
@pytest.mark.skip
async def test_kill_quorum_leader_remove(ops_test: OpsTest):
    """Gracefully removes ZK quorum leader using `juju remove`."""
    await ops_test.model.set_config({"update-status-hook-interval": "1m"})
    await ops_test.model.applications[APP_NAME].destroy_units(f"{APP_NAME}/0")
    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 3)
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert ping_servers(ops_test)
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})


@pytest.mark.abort_on_fail
@pytest.mark.skip
async def test_kill_juju_leader_remove(ops_test: OpsTest):
    """Gracefully removes Juju leader using `juju remove`."""
    await ops_test.model.set_config({"update-status-hook-interval": "1m"})
    leader = None
    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader = unit.name
            break

    if leader:
        k8s_client = AsyncClient(namespace=ops_test.model_name)
        await k8s_client.delete(Pod, name=leader.replace("/", "-"))
        time.sleep(60)
        await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
        assert ping_servers(ops_test)
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})


@pytest.mark.abort_on_fail
@pytest.mark.skip
async def test_same_model_application_deploys(ops_test: OpsTest):
    """Ensures that re-deployments of the charm starts on the same model."""
    await asyncio.gather(ops_test.model.applications[APP_NAME].remove())
    charm = await ops_test.build_charm(".")
    time.sleep(10)
    await ops_test.model.deploy(
        charm,
        application_name=APP_NAME,
        num_units=3,
        resources={"zookeeper-image": "ubuntu/zookeeper:latest"},
    ),
    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 3)
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})
    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)

    assert ops_test.model.applications[APP_NAME].status == "active"

    await ops_test.model.set_config({"update-status-hook-interval": "60m"})
