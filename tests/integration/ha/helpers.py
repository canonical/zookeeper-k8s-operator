import json
import logging
import os
import string
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

import kubernetes as kubernetes
import yaml
from kazoo.client import KazooClient
from pytest_operator.plugin import OpsTest
from tenacity import retry, retry_if_not_result, stop_after_attempt, wait_fixed

from literals import ADMIN_SERVER_PORT

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
ZOOKEEPER_IMAGE = METADATA["resources"]["zookeeper-image"]["upstream-source"]
SERIES = "jammy"
TLS_OPERATOR_SERIES = "jammy"
USERNAME = "super"
CONTAINER = "zookeeper"
SERVICE = CONTAINER
PROCESS = "org.apache.zookeeper.server.quorum.QuorumPeerMain"
PEER = "cluster"


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
        host: ZooKeeper address to issue srvr 4lw command to

    Returns:
        Dict of srvr command output key/values
    """
    response = subprocess.check_output(
        f"curl {host}:{ADMIN_SERVER_PORT}/commands/srvr -m 10",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    )

    assert response, "ZooKeeper not running"

    return json.loads(response)


def get_unit_address_map(ops_test: OpsTest, app_name: str = APP_NAME) -> dict[str, str]:
    f"""Returns map on unit name and host.

    Args:
        ops_test: OpsTest
        app_name: the Juju application to get hosts from
            Defaults to {app_name}

    Returns:
        Dict of key unit name, value unit address
    """
    ips = subprocess.check_output(
        f"JUJU_MODEL={ops_test.model_full_name} juju status {app_name} --format json | jq '.applications | .\"{app_name}\" | .units | .. .address? // empty' | xargs | tr -d '\"'",
        shell=True,
        universal_newlines=True,
    ).split()
    hosts = subprocess.check_output(
        f'JUJU_MODEL={ops_test.model_full_name} juju status {app_name} --format json | jq \'.applications | ."{app_name}" | .units | keys | join(" ")\' | tr -d \'"\'',
        shell=True,
        universal_newlines=True,
    ).split()

    return {hosts[i]: ips[i] for i in range(len(ips))}


def get_hosts(ops_test: OpsTest, app_name: str = APP_NAME, port: int = 2181) -> str:
    f"""Gets all ZooKeeper server addresses for a given application.

    Args:
        ops_test: OpsTest
        app_name: the Juju application to get hosts from
            Defaults to {app_name}
        port: the desired ZooKeeper port.
            Defaults to `2181`

    Returns:
        List of ZooKeeper server addresses and ports
    """
    return ",".join(f"{host}:{port}" for host in get_unit_address_map(ops_test, app_name).values())


def get_unit_host(
    ops_test: OpsTest, unit_name: str, app_name: str = APP_NAME, port: int = 2181
) -> str:
    f"""Gets ZooKeeper server address for a given unit name.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit to get host from
        app_name: the Juju application the unit belongs to
            Defaults to {app_name}
        port: the desired ZooKeeper port.
            Defaults to `2181`

    Returns:
        String of ZooKeeper server address and port
    """
    return f"{get_unit_address_map(ops_test, app_name)[unit_name]}:{port}"


def get_unit_name_from_host(ops_test: OpsTest, host: str, app_name: str = APP_NAME) -> str:
    f"""Gets unit name for a given ZooKeeper server address.

    Args:
        ops_test: OpsTest
        host: the ZooKeeper ip address and port
        app_name: the Juju application the ZooKeeper server belongs to
            Defaults to {app_name}

    Returns:
        String of unit name
    """
    for unit, address in get_unit_address_map(ops_test, app_name).items():
        if address == host.split(":")[0]:
            return unit

    raise KeyError(f"{host} not found")


def get_leader_name(ops_test: OpsTest, hosts: str, app_name: str = APP_NAME) -> str:
    f"""Gets current ZooKeeper quorum leader for a given application.

    Args:
        ops_test: OpsTest
        hosts: comma-delimited list of ZooKeeper ip addresses and ports
        app_name: the Juju application the unit belongs to
            Defaults to {app_name}

    Returns:
        String of unit name of the ZooKeeper quorum leader
    """
    for host in hosts.split(","):
        try:
            mode = srvr(host.split(":")[0]).get("server_stats", {}).get("server_state", "")
        except subprocess.CalledProcessError:  # unit is down
            continue
        if mode == "leader":
            leader_name = get_unit_name_from_host(ops_test, host, app_name)
            return leader_name

    return ""


async def send_control_signal(
    ops_test: OpsTest,
    unit_name: str,
    signal: str,
    container_name: str = CONTAINER,
) -> None:
    f"""Issues given job control signals to a ZooKeeper process on a given Juju unit.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit running the ZooKeeper process
        signal: the signal to issue
            e.g `SIGKILL`, `SIGSTOP`, `SIGCONT` etc
        container_name: the container to run command on
            Defaults to '{container_name}'
    """
    subprocess.check_output(
        f"kubectl exec {unit_name.replace('/', '-')} -c {container_name} -n {ops_test.model.info.name} -- pkill --signal {signal} -f {PROCESS}",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    )


async def get_pid(
    ops_test: OpsTest,
    unit_name: str,
    process: str = PROCESS,
    container_name: str = CONTAINER,
) -> str:
    f"""Gets current PID for active process.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit running the ZooKeeper process
        process: process name to search for
            Defaults to '{process}'
        container_name: the container to run command on
            Defaults to '{container_name}'
    """
    pid = subprocess.check_output(
        f"JUJU_MODEL={ops_test.model_full_name} juju ssh --container {container_name} {unit_name} 'pgrep -f {process} | head -n 1'",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    )

    return pid


async def process_stopped(
    ops_test: OpsTest,
    unit_name: str,
    process: str = PROCESS,
    container_name: str = CONTAINER,
) -> bool:
    f"""Checks if process is stopped.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit running the ZooKeeper process
        process: process name to search for
            Defaults to '{process}'
        container_name: the container to run command on
            Defaults to '{container_name}'
    """
    proc = subprocess.check_output(
        f"JUJU_MODEL={ops_test.model_full_name} juju ssh --container {container_name} {unit_name} 'ps -aux | grep {process}'",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    )

    return "Tl" in proc.split()[7]


async def get_password(ops_test, user: str | None = "super", app_name: str | None = None) -> str:
    if not app_name:
        app_name = APP_NAME
    secret_data = await get_secret_by_label(ops_test, f"{PEER}.{app_name}.app", app_name)
    return secret_data.get(f"{user}-password")


async def get_secret_by_label(ops_test, label: str, owner: str | None = None) -> dict[str, str]:
    secrets_meta_raw = await ops_test.juju("list-secrets", "--format", "json")
    secrets_meta = json.loads(secrets_meta_raw[1])

    for secret_id in secrets_meta:
        if owner and not secrets_meta[secret_id]["owner"] == owner:
            continue
        if secrets_meta[secret_id]["label"] == label:
            break

    secret_data_raw = await ops_test.juju("show-secret", "--format", "json", "--reveal", secret_id)
    secret_data = json.loads(secret_data_raw[1])
    return secret_data[secret_id]["content"]["Data"]


def write_key(host: str, password: str, username: str = USERNAME) -> None:
    f"""Write value 'hobbits' to '/legolas' zNode.

    Args:
        host: host to connect to
        username: user for ZooKeeper
            Defaults to {username}
        password: password of the user
    """
    kc = KazooClient(
        hosts=host,
        sasl_options={"mechanism": "DIGEST-MD5", "username": username, "password": password},
    )
    kc.start()
    kc.create_async("/legolas", b"hobbits")
    kc.stop()
    kc.close()


def check_key(host: str, password: str, username: str = USERNAME) -> None:
    f"""Asserts value 'hobbits' read successfully at '/legolas' zNode.

    Args:
        host: host to connect to
        username: user for ZooKeeper
            Defaults to {username}
        password: password of the user

    Raises:
        KeyError if expected value not found
    """
    kc = KazooClient(
        hosts=host,
        sasl_options={"mechanism": "DIGEST-MD5", "username": username, "password": password},
    )
    kc.start()
    assert kc.exists_async("/legolas")
    value, _ = kc.get_async("/legolas") or None, None

    stored_value = ""
    if value:
        stored_value = value.get()
    if stored_value:
        assert stored_value[0] == b"hobbits"
        return

    raise KeyError


def deploy_chaos_mesh(namespace: str) -> None:
    """Deploy chaos mesh to the provided namespace.

    Args:
        namespace: The namespace to deploy chaos mesh to
    """
    env = os.environ
    env["KUBECONFIG"] = os.path.expanduser("~/.kube/config")

    subprocess.check_output(
        " ".join(
            [
                "tests/integration/ha/scripts/deploy_chaos_mesh.sh",
                namespace,
            ]
        ),
        shell=True,
        env=env,
    )


def destroy_chaos_mesh(namespace: str) -> None:
    """Remove chaos mesh from the provided namespace.

    Args:
        namespace: The namespace to deploy chaos mesh to
    """
    env = os.environ
    env["KUBECONFIG"] = os.path.expanduser("~/.kube/config")

    subprocess.check_output(
        f"tests/integration/ha/scripts/destroy_chaos_mesh.sh {namespace}",
        shell=True,
        env=env,
    )


def isolate_instance_from_cluster(ops_test: OpsTest, unit_name: str) -> None:
    """Apply a NetworkChaos file to use chaos-mesh to simulate a network cut.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit running the ZooKeeper process
    """
    with tempfile.NamedTemporaryFile() as temp_file:
        with open(
            "tests/integration/ha/manifests/chaos_network_loss.yaml", "r"
        ) as chaos_network_loss_file:
            template = string.Template(chaos_network_loss_file.read())
            chaos_network_loss = template.substitute(
                namespace=ops_test.model.info.name,
                pod=unit_name.replace("/", "-"),
            )

            temp_file.write(str.encode(chaos_network_loss))
            temp_file.flush()

        env = os.environ
        env["KUBECONFIG"] = os.path.expanduser("~/.kube/config")

        subprocess.check_output(
            " ".join(["kubectl", "apply", "-f", temp_file.name]), shell=True, env=env
        )


def remove_instance_isolation(ops_test: OpsTest) -> None:
    """Delete the NetworkChaos that is isolating the primary unit of the cluster.

    Args:
        ops_test: OpsTest
    """
    env = os.environ
    env["KUBECONFIG"] = os.path.expanduser("~/.kube/config")

    subprocess.check_output(
        f"kubectl -n {ops_test.model.info.name} delete --ignore-not-found=true networkchaos network-loss-primary",
        shell=True,
        env=env,
    )


def modify_pebble_restart_delay(
    ops_test: OpsTest,
    policy: Literal["extend", "restore"],
    app_name: str = APP_NAME,
    container_name: str = CONTAINER,
    service_name: str = SERVICE,
) -> None:
    f"""Modify the pebble restart delay of the underlying process.

    Args:
        ops_test: OpsTest
        policy: the pebble restart delay policy to apply
            Either 'extend' or 'restore'
        app_name: the ZooKeeper Juju application
        container_name: the container to run command on
            Defaults to '{container_name}'
        service_name: the service running in the container
            Defaults to '{service_name}'
    """
    now = datetime.now().isoformat()
    pebble_patch_path = f"/tmp/pebble_plan_{now}.yaml"

    for unit in ops_test.model.applications[app_name].units:
        logger.info(
            f"Copying extend_pebble_restart_delay manifest to {unit.name} {container_name} container..."
        )
        subprocess.check_output(
            f"kubectl cp tests/integration/ha/manifests/{policy}_pebble_restart_delay.yaml {unit.name.replace('/', '-')}:{pebble_patch_path} -c {container_name} -n {ops_test.model.info.name}",
            stderr=subprocess.PIPE,
            shell=True,
            universal_newlines=True,
        )

        logger.info(f"Adding {policy} policy to {container_name} pebble plan...")
        subprocess.check_output(
            f"kubectl exec {unit.name.replace('/', '-')} -c {container_name} -n {ops_test.model.info.name} -- /charm/bin/pebble add --combine {service_name} {pebble_patch_path}",
            stderr=subprocess.PIPE,
            shell=True,
            universal_newlines=True,
        )

        logger.info(f"Replanning {service_name} service...")
        subprocess.check_output(
            f"kubectl exec {unit.name.replace('/', '-')} -c {container_name} -n {ops_test.model.info.name} -- /charm/bin/pebble replan",
            stderr=subprocess.PIPE,
            shell=True,
            universal_newlines=True,
        )


@retry(
    wait=wait_fixed(5),
    stop=stop_after_attempt(10),
    retry_error_callback=(lambda state: state.outcome.result()),  # type: ignore
    retry=retry_if_not_result(lambda result: True if result else False),
)
def all_db_processes_down(
    ops_test: OpsTest,
    app_name: str = APP_NAME,
    container_name: str = CONTAINER,
    process: str = PROCESS,
) -> bool:
    f"""Verifies that all units of the charm do not have the DB process running.

    Args:
        ops_test: OpsTest
        app_name: the ZooKeeper Juju application
        container_name: the container to run command on
            Defaults to '{container_name}'
        process: process name to search for
            Defaults to '{process}'

    Returns:
        True if all processes are down. Otherwise False
    """
    for unit in ops_test.model.applications[app_name].units:
        try:
            result = subprocess.check_output(
                f"kubectl exec {unit.name.replace('/', '-')} -c {container_name} -n {ops_test.model.info.name} -- pgrep -f {process}",
                stderr=subprocess.PIPE,
                shell=True,
                universal_newlines=True,
            )

            if result:
                logger.info(f"{unit.name} service is still up...")
                return False

        except subprocess.CalledProcessError:
            logger.info(f"{unit.name} service is down successfully...")
            continue

    return True


async def delete_pod(ops_test, unit_name: str) -> None:
    """Deletes K8s pod associated with a provided unit name.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit to kill pod of
    """
    subprocess.check_output(
        f"kubectl delete pod {unit_name.replace('/', '-')} -n {ops_test.model.info.name}",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    )

    await wait_idle(ops_test)


def get_transaction_logs_and_snapshots(
    ops_test, unit_name: str, container_name: str = CONTAINER
) -> dict[str, list[str]]:
    """Gets the most recent transaction log and snapshot files.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit to get timestamps from
        container_name: the container to run command on
            Defaults to '{container_name}'

    Returns:
        Dict of keys "transactions", "snapshots" and value of list of filenames
    """
    transaction_files = subprocess.check_output(
        f"kubectl exec {unit_name.replace('/', '-')} -c {container_name} -n {ops_test.model.info.name} -- ls -1 /var/lib/zookeeper/data-log/version-2",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    ).splitlines()

    snapshot_files = subprocess.check_output(
        f"kubectl exec {unit_name.replace('/', '-')} -c {container_name} -n {ops_test.model.info.name} -- ls -1 /var/lib/zookeeper/data/version-2",
        stderr=subprocess.PIPE,
        shell=True,
        universal_newlines=True,
    ).splitlines()

    return {"transactions": transaction_files, "snapshots": snapshot_files}
