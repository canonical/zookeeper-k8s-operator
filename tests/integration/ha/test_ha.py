import asyncio
import logging

import continuous_writes as cw
import helpers
import pytest
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

CLIENT_TIMEOUT = 10


@pytest.mark.abort_on_fail
async def test_deploy_active(ops_test: OpsTest):
    charm = await ops_test.build_charm(".")
    await ops_test.model.deploy(
        charm,
        application_name=helpers.APP_NAME,
        num_units=3,
        resources={"zookeeper-image": helpers.ZOOKEEPER_IMAGE},
        series=helpers.SERIES,
    )
    await helpers.wait_idle(ops_test)


@pytest.mark.abort_on_fail
async def test_kill_db_process(ops_test: OpsTest, request):
    """SIGKILLs leader process and checks recovery + re-election."""
    hosts = helpers.get_hosts(ops_test)
    leader_name = helpers.get_leader_name(ops_test, hosts)
    leader_host = helpers.get_unit_host(ops_test, leader_name)
    password = helpers.get_super_password(ops_test)
    parent = request.node.name
    non_leader_hosts = ",".join([host for host in hosts.split(",") if host != leader_host])

    logger.info("Starting continuous_writes...")
    cw.start_continuous_writes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # letting client set up and start writing

    logger.info("Checking writes are running at all...")
    assert cw.count_znodes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )

    logger.info("Killing leader process...")
    await helpers.send_control_signal(ops_test=ops_test, unit_name=leader_name, signal="SIGKILL")

    logger.info("Checking writes are increasing...")
    writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # increasing writes
    new_writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    assert new_writes > writes, "writes not continuing to ZK"

    logger.info("Checking leader re-election...")
    new_leader_name = helpers.get_leader_name(ops_test, non_leader_hosts)
    assert new_leader_name != leader_name

    logger.info("Stopping continuous_writes...")
    cw.stop_continuous_writes()

    logger.info("Counting writes on surviving units...")
    last_write = cw.get_last_znode(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    total_writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    assert last_write == total_writes

    logger.info("Checking old leader caught up...")
    last_write_leader = cw.get_last_znode(
        parent=parent, hosts=leader_host, username=helpers.USERNAME, password=password
    )
    total_writes_leader = cw.count_znodes(
        parent=parent, hosts=leader_host, username=helpers.USERNAME, password=password
    )
    assert last_write == last_write_leader
    assert total_writes == total_writes_leader


@pytest.mark.abort_on_fail
async def test_freeze_db_process(ops_test: OpsTest, request):
    """SIGSTOPs leader process and checks recovery + re-election after SIGCONT."""
    hosts = helpers.get_hosts(ops_test)
    leader_name = helpers.get_leader_name(ops_test, hosts)
    leader_host = helpers.get_unit_host(ops_test, leader_name)
    password = helpers.get_super_password(ops_test)
    parent = request.node.name
    non_leader_hosts = ",".join([host for host in hosts.split(",") if host != leader_host])

    logger.info("Starting continuous_writes...")
    cw.start_continuous_writes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # letting client set up and start writing

    logger.info("Checking writes are running at all...")
    assert cw.count_znodes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )

    logger.info("Stopping leader process...")
    await helpers.send_control_signal(ops_test=ops_test, unit_name=leader_name, signal="SIGSTOP")
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # to give time for re-election

    logger.info("Checking writes are increasing...")
    writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # increasing writes
    new_writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    assert new_writes > writes, "writes not continuing to ZK"

    logger.info("Checking leader re-election...")
    new_leader_name = helpers.get_leader_name(ops_test, non_leader_hosts)
    assert new_leader_name != leader_name

    logger.info("Continuing leader process...")
    await helpers.send_control_signal(ops_test=ops_test, unit_name=leader_name, signal="SIGCONT")
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # letting writes continue while unit rejoins

    logger.info("Stopping continuous_writes...")
    cw.stop_continuous_writes()
    await asyncio.sleep(CLIENT_TIMEOUT)  # buffer to ensure writes sync

    logger.info("Counting writes on surviving units...")
    last_write = cw.get_last_znode(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    total_writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    assert last_write == total_writes

    logger.info("Checking old leader caught up...")
    last_write_leader = cw.get_last_znode(
        parent=parent, hosts=leader_host, username=helpers.USERNAME, password=password
    )
    total_writes_leader = cw.count_znodes(
        parent=parent, hosts=leader_host, username=helpers.USERNAME, password=password
    )
    assert last_write == last_write_leader
    assert total_writes == total_writes_leader


@pytest.mark.abort_on_fail
async def test_two_clusters_not_replicated(ops_test: OpsTest, request):
    """Confirms that writes to one cluster are not replicated to another."""
    zk_2 = f"{helpers.APP_NAME}2"

    logger.info("Deploying second cluster...")
    new_charm = await ops_test.build_charm(".")
    await ops_test.model.deploy(
        new_charm,
        application_name=zk_2,
        num_units=3,
        resources={"zookeeper-image": helpers.ZOOKEEPER_IMAGE},
        series=helpers.SERIES,
    )
    await helpers.wait_idle(ops_test, apps=[helpers.APP_NAME, zk_2])

    parent = request.node.name

    hosts_1 = helpers.get_hosts(ops_test)
    hosts_2 = helpers.get_hosts(ops_test, app_name=zk_2)
    password_1 = helpers.get_super_password(ops_test)
    password_2 = helpers.get_super_password(ops_test, app_name=zk_2)

    logger.info("Starting continuous_writes on original cluster...")
    cw.start_continuous_writes(
        parent=parent, hosts=hosts_1, username=helpers.USERNAME, password=password_1
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # letting client set up and start writing

    logger.info("Checking writes are running at all...")
    assert cw.count_znodes(
        parent=parent, hosts=hosts_1, username=helpers.USERNAME, password=password_1
    )

    logger.info("Stopping continuous_writes...")
    cw.stop_continuous_writes()

    logger.info("Confirming writes on original cluster...")
    assert cw.count_znodes(
        parent=parent, hosts=hosts_1, username=helpers.USERNAME, password=password_1
    )

    logger.info("Confirming writes not replicated to new cluster...")
    with pytest.raises(Exception):
        cw.count_znodes(
            parent=parent, hosts=hosts_2, username=helpers.USERNAME, password=password_2
        )

    logger.info("Cleaning up old cluster...")
    await ops_test.model.applications[zk_2].remove()
    await helpers.wait_idle(ops_test)
