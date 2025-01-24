#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
import re
import tempfile
from pathlib import Path
from subprocess import PIPE, CalledProcessError, check_output
from typing import Dict

from kazoo.client import KazooClient
from pytest_operator.plugin import OpsTest

from literals import ADMIN_SERVER_PORT

from . import APP_NAME

PEER = "cluster"

logger = logging.getLogger(__name__)


async def get_password(ops_test) -> str:
    secret_data = await get_secret_by_label(ops_test, f"{PEER}.{APP_NAME}.app")
    return secret_data.get("super-password")


async def get_secret_by_label(ops_test, label: str, owner: str = APP_NAME) -> dict[str, str]:
    secrets_meta_raw = await ops_test.juju("list-secrets", "--format", "json")
    secrets_meta = json.loads(secrets_meta_raw[1])

    for secret_id in secrets_meta:
        if (
            secrets_meta[secret_id]["label"] == label
            and secrets_meta[secret_id]["owner"] == "owner"
        ):
            break

    secret_data_raw = await ops_test.juju("show-secret", "--format", "json", "--reveal", secret_id)
    secret_data = json.loads(secret_data_raw[1])
    return secret_data[secret_id]["content"]["Data"]


async def get_user_password(ops_test: OpsTest, user: str, num_unit=0) -> str:
    """Use the charm action to retrieve the password for user.

    Return:
        String with the password stored on the peer relation databag.
    """
    action = await ops_test.model.units.get(f"{APP_NAME}/{num_unit}").run_action(
        f"get-{user}-password"
    )
    password = await action.wait()
    return password.results[f"{user}-password"]


async def set_password(ops_test: OpsTest, username="super", password=None, num_unit=0) -> str:
    """Use the charm action to start a password rotation."""
    params = {"username": username}
    if password:
        params["password"] = password

    action = await ops_test.model.units.get(f"{APP_NAME}/{num_unit}").run_action(
        "set-password", **params
    )
    password = await action.wait()
    return password.results


def write_key(host: str, password: str, username: str = "super") -> None:
    kc = KazooClient(
        hosts=host,
        sasl_options={"mechanism": "DIGEST-MD5", "username": username, "password": password},
    )
    kc.start()
    kc.create_async("/legolas", b"hobbits")
    kc.stop()
    kc.close()


def check_key(host: str, password: str, username: str = "super") -> None:
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


def srvr(model_full_name: str, unit: str) -> dict:
    """Retrieves attributes returned from the 'srvr' 4lw command.

    Specifically for this test, we are interested in the "Mode" of the ZK server,
    which allows checking quorum leadership and follower active status.
    """
    response = check_output(
        f"JUJU_MODEL={model_full_name} juju ssh {unit} sudo -i 'curl localhost:{ADMIN_SERVER_PORT}/commands/srvr -m 10'",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    assert response, "ZooKeeper not running"

    return json.loads(response)


async def ping_servers(ops_test: OpsTest) -> bool:
    for unit in ops_test.model.applications[APP_NAME].units:
        srvr_response = srvr(ops_test.model_full_name, unit.name)

        if srvr_response.get("error", None) is not None:
            return False

        mode = srvr_response.get("server_stats", {}).get("server_state", "")
        if mode not in ["leader", "follower"]:
            return False

    return True


def check_properties(model_full_name: str, unit: str):
    properties = check_output(
        f"JUJU_MODEL={model_full_name} juju ssh --container zookeeper {unit} 'cat /etc/zookeeper/zoo.cfg'",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )
    return properties.splitlines()


def check_jaas_config(model_full_name: str, unit: str):
    config = check_output(
        f"JUJU_MODEL={model_full_name} juju ssh --container zookeeper {unit} 'cat /etc/zookeeper/zookeeper-jaas.cfg'",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    user_lines = {}
    for line in config.splitlines():
        matched = re.search(pattern=r"user_([a-zA-Z\-\d]+)=\"([a-zA-Z0-9]+)\"", string=line)
        if matched:
            user_lines[matched[1]] = matched[2]

    return user_lines


async def get_address(ops_test: OpsTest, app_name=APP_NAME, unit_num=0) -> str:
    """Get the address for a unit."""
    status = await ops_test.model.get_status()  # noqa: F821
    address = status["applications"][app_name]["units"][f"{app_name}/{unit_num}"]["address"]
    return address


def _get_show_unit_json(model_full_name: str, unit: str) -> Dict:
    """Retrieve the show-unit result in json format."""
    show_unit_res = check_output(
        f"JUJU_MODEL={model_full_name} juju show-unit {unit} --format json",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    try:
        show_unit_res_dict = json.loads(show_unit_res)
        return show_unit_res_dict
    except json.JSONDecodeError:
        raise ValueError


async def correct_version_running(ops_test: OpsTest, expected_version: str) -> bool:
    for unit in ops_test.model.applications[APP_NAME].units:
        srvr_response = srvr(ops_test.model_full_name, unit.name)

        if expected_version not in srvr_response.get("version", ""):
            return False

    return True


def get_relation_data(model_full_name: str, unit: str, endpoint: str):
    show_unit = _get_show_unit_json(model_full_name=model_full_name, unit=unit)
    d_relations = show_unit[unit]["relation-info"]
    for relation in d_relations:
        if relation["endpoint"] == endpoint:
            return relation["application-data"]
    raise Exception("No relation found!")


def count_lines_with(model_full_name: str, unit: str, file: str, pattern: str) -> int:
    result = check_output(
        f"JUJU_MODEL={model_full_name} juju ssh --container zookeeper {unit} 'grep \"{pattern}\" {file} | wc -l'",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    return int(result)


def delete_pod(ops_test, unit_name: str) -> None:
    """Deletes K8s pod associated with a provided unit name.

    Args:
        ops_test: OpsTest
        unit_name: the Juju unit to kill pod of
    """
    check_output(
        f"kubectl delete pod {unit_name.replace('/', '-')} -n {ops_test.model.info.name}",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )


def sign_manual_certs(ops_test: OpsTest, manual_app: str = "manual-tls-certificates") -> None:
    delim = "-----BEGIN CERTIFICATE REQUEST-----"

    csrs_cmd = f"JUJU_MODEL={ops_test.model_full_name} juju run {manual_app}/0 get-outstanding-certificate-requests --format=json | jq -r '.[\"{manual_app}/0\"].results.result' | jq '.[].csr' | sed 's/\\\\n/\\n/g' | sed 's/\\\"//g'"
    csrs = check_output(csrs_cmd, stderr=PIPE, universal_newlines=True, shell=True).split(delim)

    for i, csr in enumerate(csrs):
        if not csr:
            continue

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            csr_file = tmp_dir / f"csr{i}"
            csr_file.write_text(delim + csr)

            cert_file = tmp_dir / f"{i}.crt"

            try:
                sign_cmd = f"openssl x509 -req -in {csr_file} -CAkey tests/integration/data/inter.key -CA tests/integration/data/inter.crt -days 100 -CAcreateserial -out {cert_file} -copy_extensions copyall --passin pass:password"
                provide_cmd = f'JUJU_MODEL={ops_test.model_full_name} juju run {manual_app}/0 provide-certificate ca-certificate="$(base64 -w0 tests/integration/data/inter.crt)" ca-chain="$(base64 -w0 tests/integration/data/chain)" certificate="$(base64 -w0 {cert_file})" certificate-signing-request="$(base64 -w0 {csr_file})"'

                check_output(sign_cmd, stderr=PIPE, universal_newlines=True, shell=True)
                check_output(provide_cmd, stderr=PIPE, universal_newlines=True, shell=True)
            except CalledProcessError as e:
                logger.error(f"{e.stdout=}, {e.stderr=}, {e.output=}")
                raise e


async def list_truststore_aliases(ops_test: OpsTest, unit: str = f"{APP_NAME}/0") -> list[str]:
    secret_data = await get_secret_by_label(
        ops_test=ops_test, label=f"{PEER}.{APP_NAME}.unit", owner=unit
    )
    truststore_password = secret_data.get("truststore-password")

    try:
        result = check_output(
            f"JUJU_MODEL={ops_test.model_full_name} juju ssh --container zookeeper {unit} 'keytool -list -keystore /etc/zookeeper/truststore.jks -storepass {truststore_password}'",
            stderr=PIPE,
            shell=True,
            universal_newlines=True,
        )
    except CalledProcessError as e:
        logger.error(f"{e.output=}, {e.stdout=}, {e.stderr=}")
        raise e

    trusted_aliases = []
    for line in result.splitlines():
        if "trustedCertEntry" not in line:
            continue

        trusted_aliases.append(line.split(",")[0])

    return trusted_aliases
