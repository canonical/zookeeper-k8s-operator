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
async def test_scale_down_up_data(ops_test: OpsTest, request):
    """Tests unit scale-down + up returns with data."""
    hosts = helpers.get_hosts(ops_test)
    password = helpers.get_super_password(ops_test)
    parent = request.node.name
    current_scale = len(hosts.split(","))
    scaling_unit_name = sorted(
        [unit.name for unit in ops_test.model.applications[helpers.APP_NAME].units]
    )[-1]

    logger.info("Starting continuous_writes...")
    cw.start_continuous_writes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # letting client set up and start writing

    logger.info("Checking writes are running at all...")
    assert cw.count_znodes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )

    logger.info("Getting transaction and snapshot files...")
    current_files = helpers.get_transaction_logs_and_snapshots(
        ops_test, unit_name=scaling_unit_name
    )

    logger.info(f"Scaling down to {current_scale - 1} units...")
    await ops_test.model.applications[helpers.APP_NAME].scale(current_scale - 1)
    await helpers.wait_idle(ops_test, units=current_scale - 1)

    surviving_hosts = helpers.get_hosts(ops_test)

    logger.info("Checking writes are increasing...")
    writes = cw.count_znodes(
        parent=parent, hosts=surviving_hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # increasing writes
    new_writes = cw.count_znodes(
        parent=parent, hosts=surviving_hosts, username=helpers.USERNAME, password=password
    )
    assert new_writes > writes, "writes not continuing to ZK"

    logger.info(f"Scaling back up to {current_scale} units...")
    await ops_test.model.applications[helpers.APP_NAME].scale(current_scale)
    await helpers.wait_idle(ops_test, units=current_scale)

    logger.info("Stopping continuous_writes...")
    cw.stop_continuous_writes()

    logger.info("Counting writes on surviving units...")
    last_write = cw.get_last_znode(
        parent=parent, hosts=surviving_hosts, username=helpers.USERNAME, password=password
    )
    total_writes = cw.count_znodes(
        parent=parent, hosts=surviving_hosts, username=helpers.USERNAME, password=password
    )
    assert last_write == total_writes

    logger.info("Checking new unit caught up...")
    new_host = max(set(helpers.get_hosts(ops_test).split(",")) - set(surviving_hosts.split(",")))
    last_write_new = cw.get_last_znode(
        parent=parent, hosts=new_host, username=helpers.USERNAME, password=password
    )
    total_writes_new = cw.count_znodes(
        parent=parent, hosts=new_host, username=helpers.USERNAME, password=password
    )
    assert last_write == last_write_new
    assert total_writes == total_writes_new

    logger.info("Getting new transaction and snapshot files...")
    new_files = helpers.get_transaction_logs_and_snapshots(ops_test, unit_name=scaling_unit_name)

    # zookeeper rolls snapshots + txn logs when a unit re-joins, meaning we can't check log timestamps
    # checking file existence ensures re-use, as new files will have a different file suffix
    # if storage wasn't re-used, there would be no files with the original suffix
    for txn_log in current_files["transactions"]:
        assert txn_log in new_files["transactions"], "storage not re-used, missing txn logs"

    for snapshot in current_files["snapshots"]:
        assert snapshot in new_files["snapshots"], "storage not re-used, missing snapshots"


@pytest.mark.abort_on_fail
async def test_pod_reschedule(ops_test: OpsTest, request):
    """Forcefully reschedules ZooKeeper pod."""
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

    logger.info("Removing leader pod...")
    await helpers.delete_pod(ops_test=ops_test, unit_name=leader_name)
    await asyncio.sleep(CLIENT_TIMEOUT)  # waiting for process to restore

    logger.info("Checking leader IP changed...")
    new_leader_host = helpers.get_unit_host(ops_test, leader_name)
    assert new_leader_host != leader_host  # ensures pod was actually rescheduled

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
        parent=parent, hosts=new_leader_host, username=helpers.USERNAME, password=password
    )
    total_writes_leader = cw.count_znodes(
        parent=parent, hosts=new_leader_host, username=helpers.USERNAME, password=password
    )
    assert last_write == last_write_leader
    assert total_writes == total_writes_leader


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

    logger.info("Getting leader pid...")
    current_pid = await helpers.get_pid(ops_test, leader_name)

    logger.info("Killing leader process...")
    await helpers.send_control_signal(ops_test=ops_test, unit_name=leader_name, signal="SIGKILL")
    await asyncio.sleep(CLIENT_TIMEOUT)  # waiting for process to restore

    logger.info("Checking leader pid changed...")
    new_pid = await helpers.get_pid(ops_test, leader_name)
    assert new_pid != current_pid  # validates process actually stopped

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
async def test_restart_db_process(ops_test: OpsTest, request):
    """SIGTERMSs leader process and checks recovery + re-election."""
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
    await helpers.send_control_signal(ops_test=ops_test, unit_name=leader_name, signal="SIGTERM")

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

    assert await helpers.process_stopped(ops_test, leader_name)

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
async def test_full_cluster_crash(ops_test: OpsTest, request):
    hosts = helpers.get_hosts(ops_test)
    leader_name = helpers.get_leader_name(ops_test, hosts)
    leader_host = helpers.get_unit_host(ops_test, leader_name)
    password = helpers.get_super_password(ops_test)
    parent = request.node.name
    non_leader_hosts = ",".join([host for host in hosts.split(",") if host != leader_host])

    logger.info("Extending pebble restart delay on all units...")
    helpers.modify_pebble_restart_delay(ops_test, policy="extend")

    # letting the cluster settle
    await helpers.wait_idle(ops_test)

    logger.info("Starting continuous_writes...")
    cw.start_continuous_writes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)

    logger.info("Counting writes are running at all...")
    assert cw.count_znodes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )

    # kill all units "simultaneously"
    await asyncio.gather(
        *[
            helpers.send_control_signal(ops_test, unit.name, signal="SIGKILL")
            for unit in ops_test.model.applications[helpers.APP_NAME].units
        ]
    )

    # Check that all servers are down at the same time before restoring them
    try:
        assert helpers.all_db_processes_down(ops_test), "Not all units down at the same time."
    finally:
        logger.info("Restoring pebble restart delay on all units...")
        helpers.modify_pebble_restart_delay(ops_test, policy="restore")

    # letting the cluster settle
    await helpers.wait_idle(ops_test)

    logger.info("Checking writes are increasing...")
    writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # letting client set up and start writing
    new_writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    assert new_writes > writes, "writes not continuing to ZK"

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


@pytest.mark.abort_on_fail
async def test_full_cluster_restart(ops_test: OpsTest, request):
    hosts = helpers.get_hosts(ops_test)
    leader_name = helpers.get_leader_name(ops_test, hosts)
    leader_host = helpers.get_unit_host(ops_test, leader_name)
    password = helpers.get_super_password(ops_test)
    parent = request.node.name
    non_leader_hosts = ",".join([host for host in hosts.split(",") if host != leader_host])

    logger.info("Extending pebble restart delay on all units...")
    helpers.modify_pebble_restart_delay(ops_test, policy="extend")

    # letting the cluster settle
    await helpers.wait_idle(ops_test)

    logger.info("Starting continuous_writes...")
    cw.start_continuous_writes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)

    logger.info("Counting writes are running at all...")
    assert cw.count_znodes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )

    # kill all units "simultaneously"
    await asyncio.gather(
        *[
            helpers.send_control_signal(ops_test, unit.name, signal="SIGTERM")
            for unit in ops_test.model.applications[helpers.APP_NAME].units
        ]
    )

    # Check that all servers are down at the same time before restoring them
    try:
        assert helpers.all_db_processes_down(ops_test), "Not all units down at the same time."
    finally:
        logger.info("Restoring pebble restart delay on all units...")
        helpers.modify_pebble_restart_delay(ops_test, policy="restore")

    # letting the cluster settle
    await helpers.wait_idle(ops_test)

    logger.info("Checking writes are increasing...")
    writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # letting client set up and start writing
    new_writes = cw.count_znodes(
        parent=parent, hosts=non_leader_hosts, username=helpers.USERNAME, password=password
    )
    assert new_writes > writes, "writes not continuing to ZK"

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


@pytest.mark.abort_on_fail
async def test_network_cut_without_ip_change(ops_test: OpsTest, request, chaos_mesh):
    """Cuts and restores network on leader, cluster self-heals after IP change."""
    hosts = helpers.get_hosts(ops_test)
    leader_name = helpers.get_leader_name(ops_test, hosts)
    initial_leader_host = helpers.get_unit_host(ops_test, leader_name)
    password = helpers.get_super_password(ops_test)
    parent = request.node.name
    non_leader_hosts = ",".join([host for host in hosts.split(",") if host != initial_leader_host])

    logger.info("Starting continuous_writes...")
    cw.start_continuous_writes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )
    await asyncio.sleep(CLIENT_TIMEOUT * 3)  # letting client set up and start writing

    logger.info("Checking writes are running at all...")
    assert cw.count_znodes(
        parent=parent, hosts=hosts, username=helpers.USERNAME, password=password
    )

    logger.info("Cutting leader network...")
    helpers.isolate_instance_from_cluster(ops_test, leader_name)
    await asyncio.sleep(CLIENT_TIMEOUT * 3)
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

    logger.info("Restoring leader network...")
    helpers.remove_instance_isolation(ops_test)

    # livenessProbe should fail after 10*3 seconds by default, then the container is restarted
    # allows dead ZK server to rejoin quorum
    await asyncio.sleep(CLIENT_TIMEOUT * 6)

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
        parent=parent, hosts=initial_leader_host, username=helpers.USERNAME, password=password
    )
    total_writes_leader = cw.count_znodes(
        parent=parent, hosts=initial_leader_host, username=helpers.USERNAME, password=password
    )
    assert last_write == last_write_leader
    assert total_writes == total_writes_leader
