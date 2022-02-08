#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.


import logging
from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build the charm zookeeper-k8s and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    await ops_test.model.set_config({"update-status-hook-interval": "10s"})

    charm = await ops_test.build_charm(".")
    resources = {
        "zookeeper-k8s-image": METADATA["resources"]["zookeeper-k8s-image"]["upstream-source"],
    }
    await ops_test.model.deploy(charm, resources=resources, application_name="zookeeper-k8s")
    await ops_test.model.wait_for_idle(apps=["zookeeper-k8s"], status="active", timeout=1000)
    assert ops_test.model.applications["zookeeper-k8s"].units[0].workload_status == "active"

    logger.debug("Setting update-status-hook-interval to 60m")
    await ops_test.model.set_config({"update-status-hook-interval": "60m"})
