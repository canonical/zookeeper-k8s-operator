#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import json
import logging
import socket

import boto3
import pytest
import pytest_microceph
from mypy_boto3_s3.service_resource import Bucket
from pytest_operator.plugin import OpsTest

from . import ZOOKEEPER_IMAGE
from .helpers import APP_NAME, check_key, get_address, get_user_password, write_key

logger = logging.getLogger(__name__)

S3_INTEGRATOR = "s3-integrator"
S3_CHANNEL = "latest/stable"
APP_TO_RESTORE = "restored"


@pytest.fixture(scope="session")
def cloud_credentials(microceph: pytest_microceph.ConnectionInformation) -> dict[str, str]:
    """Read cloud credentials."""
    return {
        "access-key": microceph.access_key_id,
        "secret-key": microceph.secret_access_key,
    }


@pytest.fixture(scope="session")
def cloud_configs(microceph: pytest_microceph.ConnectionInformation):
    host_ip = socket.gethostbyname(socket.gethostname())
    return {
        "endpoint": f"http://{host_ip}",
        "bucket": microceph.bucket,
        "path": "mysql",
        "region": "",
    }


@pytest.fixture(scope="function")
def s3_bucket(cloud_credentials, cloud_configs):

    session = boto3.Session(
        aws_access_key_id=cloud_credentials["access-key"],
        aws_secret_access_key=cloud_credentials["secret-key"],
        region_name=cloud_configs["region"] if cloud_configs["region"] else None,
    )
    s3 = session.resource("s3", endpoint_url=cloud_configs["endpoint"])
    bucket = s3.Bucket(cloud_configs["bucket"])
    yield bucket


@pytest.mark.abort_on_fail
async def test_deploy_active(ops_test: OpsTest, zk_charm, cloud_configs, cloud_credentials):
    await asyncio.gather(
        ops_test.model.deploy(
            zk_charm,
            application_name=APP_NAME,
            num_units=3,
            resources={"zookeeper-image": ZOOKEEPER_IMAGE},
        ),
        ops_test.model.deploy(S3_INTEGRATOR, channel=S3_CHANNEL),
    )

    await ops_test.model.wait_for_idle(apps=[S3_INTEGRATOR], status="blocked", timeout=1000)

    logger.info("Syncing credentials")

    await ops_test.model.applications[S3_INTEGRATOR].set_config(cloud_configs)

    for unit in ops_test.model.applications[S3_INTEGRATOR].units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    sync_action = await leader_unit.run_action(
        "sync-s3-credentials",
        **cloud_credentials,
    )
    await sync_action.wait()

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, S3_INTEGRATOR],
        status="active",
        timeout=1000,
    )


@pytest.mark.abort_on_fail
async def test_relate_active_bucket_created(ops_test: OpsTest, s3_bucket):
    await ops_test.model.add_relation(APP_NAME, S3_INTEGRATOR)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, S3_INTEGRATOR],
        status="active",
        timeout=1000,
    )

    # bucket exists
    assert s3_bucket.meta.client.head_bucket(Bucket=s3_bucket.name)


@pytest.mark.abort_on_fail
async def test_create_backup(ops_test: OpsTest, s3_bucket: Bucket):
    for unit in ops_test.model.applications[APP_NAME].units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    super_password = await get_user_password(ops_test, "super")
    host = await get_address(ops_test, APP_NAME, leader_unit.name.split("/")[-1])
    write_key(host=host, password=super_password)

    create_action = await leader_unit.run_action("create-backup")
    await create_action.wait()

    list_action = await leader_unit.run_action("list-backups")
    response = await list_action.wait()

    backups = json.loads(response.results.get("backups", "[]"))
    assert len(backups) == 1


@pytest.mark.abort_on_fail
async def test_restore_backup_new_app(ops_test: OpsTest, s3_bucket: Bucket, zk_charm):

    await ops_test.model.deploy(
        zk_charm,
        application_name=APP_TO_RESTORE,
        num_units=3,
        resources={"zookeeper-image": ZOOKEEPER_IMAGE},
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_TO_RESTORE], status="active", timeout=1000, raise_on_error=False
    )
    await ops_test.model.add_relation(APP_TO_RESTORE, S3_INTEGRATOR)
    await ops_test.model.wait_for_idle(
        apps=[APP_TO_RESTORE, S3_INTEGRATOR], status="active", timeout=1000, raise_on_error=False
    )
    for unit in ops_test.model.applications[APP_TO_RESTORE].units:
        if await unit.is_leader_from_status():
            leader_unit = unit

    list_action = await leader_unit.run_action("list-backups")
    response = await list_action.wait()

    backups = json.loads(response.results.get("backups", "[]"))
    backup_to_restore = backups[0]["id"]
    list_action = await leader_unit.run_action("restore", **{"backup-id": backup_to_restore})

    await ops_test.model.wait_for_idle(
        apps=[APP_TO_RESTORE, S3_INTEGRATOR], status="active", timeout=1000, idle_period=30
    )
    super_password = await get_user_password(ops_test, "super")

    for unit in ops_test.model.applications[APP_TO_RESTORE].units:
        host = await get_address(ops_test, APP_NAME, unit.name.split("/")[-1])
        check_key(host=host, password=super_password)
