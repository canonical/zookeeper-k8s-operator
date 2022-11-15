#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import re
from pathlib import Path
from subprocess import PIPE, check_output
from typing import Dict

import yaml
from kazoo.client import KazooClient
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]


def get_password(model_full_name: str) -> str:
    # getting relation data
    show_unit = check_output(
        f"JUJU_MODEL={model_full_name} juju show-unit {APP_NAME}/0 --format json",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )
    response = json.loads(show_unit)
    relations_info = response[f"{APP_NAME}/0"]["relation-info"]

    for info in relations_info:
        if info["endpoint"] == "cluster":
            password = info["application-data"]["super_password"]
            return password
    else:
        raise Exception("no relations found")


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


def srvr(host: str) -> Dict:
    """Retrieves attributes returned from the 'srvr' 4lw command.

    Specifically for this test, we are interested in the "Mode" of the ZK server,
    which allows checking quorum leadership and follower active status.
    """
    response = check_output(
        f"echo srvr | nc {host} 2181", stderr=PIPE, shell=True, universal_newlines=True
    )

    result = {}
    for item in response.splitlines():
        k = re.split(": ", item)[0]
        v = re.split(": ", item)[1]
        result[k] = v

    return result


async def ping_servers(ops_test: OpsTest) -> bool:
    for unit in ops_test.model.applications[APP_NAME].units:
        host = unit.public_address
        mode = srvr(host)["Mode"]
        if mode not in ["leader", "follower"]:
            return False

    return True


def check_properties(model_full_name: str, unit: str):
    properties = check_output(
        f"JUJU_MODEL={model_full_name} juju ssh --container zookeeper {unit} 'cat /opt/zookeeper/conf/zoo.cfg'",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )
    return properties.splitlines()


def check_jaas_config(model_full_name: str, unit: str):
    config = check_output(
        f"JUJU_MODEL={model_full_name} juju ssh --container zookeeper {unit} 'cat /opt/zookeeper/conf/zookeeper-jaas.cfg'",
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
