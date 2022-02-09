#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from pathlib import Path

import pytest
import yaml
from kazoo.client import KazooClient
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


async def get_zookeeper_uri(ops_test: OpsTest):
    status = await ops_test.model.get_status()
    unit_ips = [
        f"{unit_status.address}:2181"
        for unit_status in status.applications["zookeeper-k8s"].units.values()
    ]
    return ",".join(unit_ips)


@pytest.fixture
async def kazoo(ops_test: OpsTest):
    zookeeper_uri = await get_zookeeper_uri(ops_test)
    zk = KazooClient(hosts=zookeeper_uri, read_only=True)
    zk.start()
    yield zk
    zk.stop()


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm zookeeper-k8s and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})

    charm = await ops_test.build_charm(".")
    resources = {
        "zookeeper-image": METADATA["resources"]["zookeeper-image"]["upstream-source"],
    }
    await ops_test.model.deploy(charm, resources=resources, application_name="zookeeper-k8s")
    await ops_test.model.wait_for_idle(apps=["zookeeper-k8s"], status="active", timeout=1000)
    assert ops_test.model.applications["zookeeper-k8s"].units[0].workload_status == "active"

    logger.debug("Setting update-status-hook-interval to 60m")
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})


async def test_zookeeper(kazoo):
    # Create path
    kazoo.ensure_path("/testing/node")
    assert kazoo.exists("/testing/node")
    # Create node
    kazoo.create("/testing/node/message", b"hello")
    assert kazoo.exists("/testing/node/message")
    # Get data
    data, stat = kazoo.get("/testing/node/message")
    assert data.decode("utf-8") == "hello"
    # Update data
    kazoo.set("/testing/node/message", b"hello world!")
    data, stat = kazoo.get("/testing/node/message")
    assert data.decode("utf-8") == "hello world!"
    # Delete nodes
    kazoo.delete("/testing/node", recursive=True)
    assert not kazoo.exists("/testing/node")
    assert kazoo.exists("/testing")
    kazoo.delete("/testing", recursive=True)
    assert not kazoo.exists("/testing")


async def test_zookeeper_scaling(ops_test: OpsTest, kazoo):
    # Create node
    kazoo.ensure_path("/scaling/test")
    kazoo.create("/scaling/test/message", b"hello")
    data, stat = kazoo.get("/scaling/test/message")
    assert data.decode("utf-8") == "hello"
    # Scale zookeeper
    await ops_test.model.applications["zookeeper-k8s"].scale(3)
    await ops_test.model.wait_for_idle(apps=["zookeeper-k8s"], status="active", timeout=1000)
    # Run zookeeper test
    test_zookeeper(kazoo)
    # Check created node
    for _ in range(100):
        data, stat = kazoo.get("/scaling/test/message")
        assert data.decode("utf-8") == "hello"
