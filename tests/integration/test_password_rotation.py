#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import (
    check_key,
    get_user_password,
    ping_servers,
    rotate_passwords,
    write_key,
)

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


@pytest.mark.skip_if_deployed
@pytest.mark.abort_on_fail
@pytest.mark.password_rotation
async def test_deploy_active(ops_test: OpsTest):
    charm = await ops_test.build_charm(".")
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


@pytest.mark.abort_on_fail
@pytest.mark.password_rotation
async def test_password_rotation(ops_test: OpsTest):
    """Test password rotation action."""
    super_password = await get_user_password(ops_test, "super")
    sync_password = await get_user_password(ops_test, "sync")

    logger.info(
        "Zookeeper passwords:\n- super: {}\n- sync: {}".format(super_password, sync_password)
    )

    leader = None
    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader = unit.name
            break

    leader_num = leader.split("/")[-1]
    result = await rotate_passwords(ops_test, leader_num)
    assert result == "Called for password rotation"

    await ops_test.model.wait_for_idle(apps=[APP_NAME], status="active", timeout=1000)
    assert ops_test.model.applications[APP_NAME].status == "active"
    assert ping_servers(ops_test)

    new_super_password = await get_user_password(ops_test, "super")
    new_sync_password = await get_user_password(ops_test, "sync")

    assert super_password != new_super_password
    assert sync_password != new_sync_password

    host = ops_test.model.applications[APP_NAME].units[0].public_address
    write_key(host=host, password=new_super_password)
    check_key(host=host, password=new_super_password)
