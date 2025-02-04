#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import asyncio
import logging
from subprocess import PIPE, check_output

import pytest
from pytest_operator.plugin import OpsTest

from . import APP_NAME, SERIES, TLS_OPERATOR_SERIES, ZOOKEEPER_IMAGE
from .helpers import (
    check_properties,
    delete_pod,
    get_address,
    list_truststore_aliases,
    ping_servers,
    sign_manual_certs,
)

logger = logging.getLogger(__name__)

TLS_NAME = "self-signed-certificates"
MANUAL_TLS_NAME = "manual-tls-certificates"


@pytest.mark.abort_on_fail
async def test_deploy_ssl_quorum(ops_test: OpsTest, zk_charm):
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

    assert ping_servers(ops_test)

    for unit in ops_test.model.applications[APP_NAME].units:
        assert "sslQuorum=true" in check_properties(
            model_full_name=ops_test.model_full_name, unit=unit.name
        )


@pytest.mark.abort_on_fail
async def test_remove_tls_provider(ops_test: OpsTest):
    await ops_test.model.applications[APP_NAME].remove_relation(
        f"{APP_NAME}:certificates", f"{TLS_NAME}:certificates"
    )

    # ensuring enough time for multiple rolling-restart with update-status
    async with ops_test.fast_forward(fast_interval="20s"):
        await asyncio.sleep(180)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, idle_period=30
    )

    assert ping_servers(ops_test)

    for unit in ops_test.model.applications[APP_NAME].units:
        assert "sslQuorum=true" not in check_properties(
            model_full_name=ops_test.model_full_name, unit=unit.name
        )


@pytest.mark.abort_on_fail
async def test_manual_tls_chain(ops_test: OpsTest):
    await ops_test.model.deploy(
        MANUAL_TLS_NAME, application_name=MANUAL_TLS_NAME, channel="stable"
    )

    await asyncio.gather(
        ops_test.model.add_relation(APP_NAME, MANUAL_TLS_NAME),
    )

    # ensuring enough time for multiple rolling-restart with update-status
    async with ops_test.fast_forward(fast_interval="20s"):
        await asyncio.sleep(180)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, MANUAL_TLS_NAME], idle_period=60, timeout=1000
    )

    sign_manual_certs(ops_test)

    # ensuring enough time for multiple rolling-restart with update-status
    async with ops_test.fast_forward(fast_interval="20s"):
        await asyncio.sleep(180)

    # verifying servers can communicate with one-another
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, MANUAL_TLS_NAME], idle_period=60, timeout=1000
    )

    # verifying the chain is in there
    trusted_aliases = await list_truststore_aliases(ops_test)

    assert len(trusted_aliases) == 3  # CA, intermediate, rootca

    # cleanup

    await ops_test.model.applications[APP_NAME].remove_relation(
        f"{APP_NAME}:certificates", f"{MANUAL_TLS_NAME}:certificates"
    )

    # ensuring enough time for multiple rolling-restart with update-status
    async with ops_test.fast_forward(fast_interval="20s"):
        await asyncio.sleep(180)

    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(apps=[APP_NAME], idle_period=30, timeout=1000)


@pytest.mark.abort_on_fail
async def test_add_tls_provider_succeeds_after_removal(ops_test: OpsTest):
    await ops_test.model.add_relation(APP_NAME, TLS_NAME)

    # ensuring enough time for multiple rolling-restart with update-status
    async with ops_test.fast_forward(fast_interval="20s"):
        await asyncio.sleep(180)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, TLS_NAME], status="active", timeout=1000, idle_period=30
    )

    assert ping_servers(ops_test)

    for unit in ops_test.model.applications[APP_NAME].units:
        assert "sslQuorum=true" in check_properties(
            model_full_name=ops_test.model_full_name, unit=unit.name
        )


@pytest.mark.abort_on_fail
async def test_scale_up_tls(ops_test: OpsTest):
    await ops_test.model.applications[APP_NAME].add_units(count=1)
    await ops_test.model.block_until(lambda: len(ops_test.model.applications[APP_NAME].units) == 4)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME], status="active", timeout=1000, idle_period=40
    )
    assert ping_servers(ops_test)


@pytest.mark.abort_on_fail
async def test_client_relate_maintains_quorum(ops_test: OpsTest):
    dummy_name = "app"
    app_charm = await ops_test.build_charm("tests/integration/app-charm")
    await ops_test.model.deploy(app_charm, application_name=dummy_name, num_units=1, series=SERIES)
    await ops_test.model.wait_for_idle(
        [APP_NAME, dummy_name], status="active", timeout=1000, idle_period=30
    )

    await ops_test.model.add_relation(APP_NAME, dummy_name)
    await ops_test.model.wait_for_idle(
        [APP_NAME, dummy_name], status="active", timeout=1000, idle_period=30
    )

    assert ping_servers(ops_test)


@pytest.mark.abort_on_fail
async def test_pod_reschedule_tls(ops_test: OpsTest):
    delete_pod(ops_test, unit_name=f"{APP_NAME}/0")

    async with ops_test.fast_forward(fast_interval="60s"):
        await ops_test.model.wait_for_idle(
            [APP_NAME], status="active", timeout=1000, idle_period=30
        )

    await asyncio.sleep(180)  # ensure Juju picks up new port


@pytest.mark.abort_on_fail
async def test_renew_cert(ops_test: OpsTest):
    # invalidate previous certs
    await ops_test.model.applications[TLS_NAME].set_config({"ca-common-name": "new-name"})

    await ops_test.model.wait_for_idle([APP_NAME], status="active", timeout=1000, idle_period=30)
    async with ops_test.fast_forward(fast_interval="20s"):
        await asyncio.sleep(60)

    # check quorum TLS
    assert ping_servers(ops_test)

    # check client-presented certs
    host = await get_address(ops_test, unit_num=0)

    response = check_output(
        f"openssl s_client -showcerts -connect {host}:2182 < /dev/null",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    assert "CN = new-name" in response
