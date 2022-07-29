#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

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
        f"JUJU_MODEL={model_full_name} juju show-unit {APP_NAME}/0",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )
    response = yaml.safe_load(show_unit)
    relations_info = response[f"{APP_NAME}/0"]["relation-info"]

    for info in relations_info:
        if info["endpoint"] == "cluster":
            password = info["application-data"]["super_password"]
            return password
    else:
        raise Exception("no relations found")


def restart_unit(model_full_name: str, unit: str) -> None:
    # getting juju id
    machine_id = check_output(
        f"JUJU_MODEL={model_full_name} juju status | grep {unit} | awk '{{ print $4 }}'",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )

    # getting lxc machine name
    machine_name = check_output(
        f"JUJU_MODEL={model_full_name} juju machines | grep awk '{{print $4}}' | grep -e '-{machine_id}'| head -1",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )
    _ = check_output(
        f"lxc restart {machine_name}",
        stderr=PIPE,
        shell=True,
        universal_newlines=True,
    )


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


def check_jaas_config(model_full_name: str, unit: str):
    config = check_output(
        f"JUJU_MODEL={model_full_name} juju exec cat /var/snap/kafka/common/zookeeper-jaas.cfg --unit {unit}",
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
