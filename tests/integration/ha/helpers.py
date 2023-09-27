import logging
import re
import subprocess
from pathlib import Path

import yaml
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
ZOOKEEPER_IMAGE = METADATA["resources"]["zookeeper-image"]["upstream-source"]
SERIES = "jammy"
TLS_OPERATOR_SERIES = "jammy"
USERNAME="super"

PROCESS = "org.apache.zookeeper.server.quorum.QuorumPeerMain"


async def wait_idle(ops_test, apps: list[str] = [APP_NAME], units: int = 3) -> None:
    """Waits for active/idle on specified application.

    Args:
        ops_test: OpsTest
        apps: list of application names to wait for
        units: integer number of units to wait for for each application
    """
    await ops_test.model.wait_for_idle(
        apps=apps, status="active", timeout=3600, idle_period=30, wait_for_exact_units=units
    )
    assert ops_test.model.applications[APP_NAME].status == "active"


@retry(
    wait=wait_fixed(5),
    stop=stop_after_attempt(60),
    reraise=True,
)
def srvr(host: str) -> dict:
    """Calls srvr 4lw command to specified host.

    Args:
        host: ZooKeeper address and port to issue srvr 4lw command to

    Returns:
        Dict of srvr command output key/values
    """
    response = subprocess.check_output(
        f"echo srvr | nc {host} 2181", stderr=subprocess.PIPE, shell=True, universal_newlines=True
    )

    assert response, "ZooKeeper not running"

    result = {}
    for item in response.splitlines():
        k = re.split(": ", item)[0]
        v = re.split(": ", item)[1]
        result[k] = v

    return result


def get_hosts_from_status(
    ops_test: OpsTest, app_name: str = APP_NAME, port: int = 2181
) -> list[str]:
    """Manually calls `juju status` and grabs the host addresses from there for a given application.

    Needed as after an ip change (e.g network cut test), OpsTest does not recognise the new address.

    Args:
        ops_test: OpsTest
        app_name: the Juju application to get hosts from
            Defaults to `zookeeper`
        port: the desired ZooKeeper port.
            Defaults to `2181`

    Returns:
        List of ZooKeeper server addresses and ports
    """
    ips = subprocess.check_output(
        f"JUJU_MODEL={ops_test.model_full_name} juju status {app_name} --format json | jq '.applications | .\"{app_name}\" | .units | .. .address? // empty' | xargs | tr -d '\"'",
        shell=True,
        universal_newlines=True,
    ).split()

    return [f"{ip}:{port}" for ip in ips]


def get_hosts(ops_test: OpsTest, app_name: str = APP_NAME, port: int = 2181) -> str:
    """Gets all ZooKeeper server addresses for a given application.

    Args:
        ops_test: OpsTest
        app_name: the Juju application to get hosts from
            Defaults to `zookeeper`
        port: the desired ZooKeeper port.
            Defaults to `2181`

    Returns:
        Comma-delimited string of ZooKeeper server addresses and ports
    """
    return ",".join(
        [
            f"{unit.get_public_address()}:{str(port)}"
            for unit in ops_test.model.applications[app_name].units
        ]
    )


def get_unit_host(
    ops_test: OpsTest, unit_name: str, app_name: str = APP_NAME, port: int = 2181
) -> str:
    """Gets ZooKeeper server address for a given unit name.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit to get host from
        app_name: the Juju application the unit belongs to
            Defaults to `zookeeper`
        port: the desired ZooKeeper port.
            Defaults to `2181`

    Returns:
        String of ZooKeeper server address and port
    """
    return [
        f"{unit.get_public_address()}:{str(port)}"
        for unit in ops_test.model.applications[app_name].units
        if unit.name == unit_name
    ][0]


def get_unit_name_from_host(ops_test: OpsTest, host: str, app_name: str = APP_NAME) -> str:
    """Gets unit name for a given ZooKeeper server address.

    Args:
        ops_test: OpsTest
        host: the ZooKeeper ip address and port
        app_name: the Juju application the ZooKeeper server belongs to
            Defaults to `zookeeper`

    Returns:
        String of unit name
    """
    return [
        unit.name
        for unit in ops_test.model.applications[app_name].units
        if unit.get_public_address() == host.split(":")[0]
    ][0]


def get_leader_name(ops_test: OpsTest, hosts: str, app_name: str = APP_NAME) -> str:
    """Gets current ZooKeeper quorum leader for a given application.

    Args:
        ops_test: OpsTest
        hosts: comma-delimited list of ZooKeeper ip addresses and ports
        app_name: the Juju application the unit belongs to
            Defaults to `zookeeper`

    Returns:
        String of unit name of the ZooKeeper quorum leader
    """
    for host in hosts.split(","):
        try:
            mode = srvr(host.split(":")[0])["Mode"]
        except subprocess.CalledProcessError:  # unit is down
            continue
        if mode == "leader":
            leader_name = get_unit_name_from_host(ops_test, host, app_name)
            return leader_name

    return ""


async def get_unit_machine_name(ops_test: OpsTest, unit_name: str) -> str:
    """Gets current LXD machine name for a given unit name.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit name to get from

    Returns:
        String of LXD machine name
            e.g juju-123456-0
    """
    _, raw_hostname, _ = await ops_test.juju("ssh", unit_name, "hostname")
    return raw_hostname.strip()


def cut_unit_network(machine_name: str) -> None:
    """Cuts network access for a given LXD container.

    Args:
        machine_name: the LXD machine name to cut network for
            e.g `juju-123456-0`
    """
    cut_network_command = f"lxc config device add {machine_name} eth0 none"
    subprocess.check_call(cut_network_command.split())


def restore_unit_network(machine_name: str) -> None:
    """Restores network access for a given LXD container.

    Args:
        machine_name: the LXD machine name to restore network for
            e.g `juju-123456-0`
    """
    restore_network_command = f"lxc config device remove {machine_name} eth0"
    subprocess.check_call(restore_network_command.split())


def get_storage_id(ops_test, unit_name: str) -> str:
    """Gets the current Juju storage ID for a given unit name.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit name to get storage ID of

    Returns:
        String of Juju storage ID
    """
    proc = subprocess.check_output(
        f"JUJU_MODEL={ops_test.model_full_name} juju storage --format yaml",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    )
    response = yaml.safe_load(proc)
    storages = response["storage"]

    for storage_id, storage in storages.items():
        if unit_name in storage["attachments"]["units"]:
            return storage_id

    raise Exception(f"storage id not found for {unit_name}")


async def reuse_storage(ops_test, unit_storage_id: str, app_name: str = APP_NAME) -> None:
    """Removes and adds back a unit, reusing it's storage.

    Args:
        ops_test: OpsTest
        unit_storage_id: the Juju storage id to be re-used
        app_name: the Juju application ZooKeeper belongs to
            Defaults to `zookeeper`
    """
    logger.info("Adding new unit with old unit's storage...")
    subprocess.check_output(
        f"JUJU_MODEL={ops_test.model_full_name} juju add-unit {app_name} --attach-storage={unit_storage_id}",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    )
    await wait_idle(ops_test, apps=[app_name])


def get_super_password(ops_test: OpsTest, app_name: str = APP_NAME) -> str:
    """Gets current `super-password` for a given ZooKeeper application.

    Args:
        ops_test: OpsTest
        app_name: the ZooKeeper Juju application


    Returns:
        String of password for the `super` user
    """
    for unit in ops_test.model.applications[app_name].units:
        show_unit = subprocess.check_output(
            f"JUJU_MODEL={ops_test.model_full_name} juju show-unit {unit.name}",
            stderr=subprocess.PIPE,
            shell=True,
            universal_newlines=True,
        )
        response = yaml.safe_load(show_unit)
        relations_info = response[f"{unit.name}"]["relation-info"]

        password = None
        for info in relations_info:
            if info["endpoint"] == "cluster":
                password = info["application-data"]["super-password"]
                return password

        if not password:
            raise Exception("no relations found")


async def send_control_signal(
    ops_test: OpsTest, unit_name: str, signal: str, app_name: str = APP_NAME, container_name: str = "zookeeper",
) -> None:
    """Issues given job control signals to a ZooKeeper process on a given Juju unit.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit running the ZooKeeper process
        signal: the signal to issue
            e.g `SIGKILL`, `SIGSTOP`, `SIGCONT` etc
        app_name: the ZooKeeper Juju application
    """
    if len(ops_test.model.applications[app_name].units) < 3:
        await ops_test.model.applications[app_name].add_unit(count=1)
        await ops_test.model.wait_for_idle(apps=[app_name], status="active", timeout=1000)

    cmd = f"ssh --container {container_name} {unit_name} pkill --signal {signal} -f {PROCESS}"
    return_code, _, _ = await ops_test.juju(*cmd.split())

    if return_code != 0:
        raise Exception(f"Expected kill command {cmd} to succeed instead it failed: {return_code}")

