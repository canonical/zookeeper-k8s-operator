#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implementation of WorkloadBase for running on K8s."""
import logging
import re
import secrets
import string
from subprocess import CalledProcessError

from ops.model import Container
from ops.pebble import ChangeError, ExecError, Layer
from tenacity import retry, retry_if_result, stop_after_attempt, wait_fixed
from typing_extensions import override

from core.workload import WorkloadBase
from literals import CLIENT_PORT

logger = logging.getLogger(__name__)


class ZKWorkload(WorkloadBase):
    """Implementation of WorkloadBase for running on K8s."""

    def __init__(self, container: Container):
        self.container = container

    @override
    def start(self, layer: Layer) -> None:
        # ensures paths exist on mounted storage to use
        self.container.make_dir(self.paths.data_dir, make_parents=True)
        self.container.make_dir(self.paths.datalog_dir, make_parents=True)

        try:
            self.container.add_layer(self.container.name, layer, combine=True)
            self.container.replan()
        except ChangeError:
            return

    @override
    def stop(self) -> None:
        self.container.stop(self.container.name)

    @override
    def restart(self) -> None:
        self.container.restart(self.container.name)

    @override
    def read(self, path: str) -> list[str]:
        if not self.container.exists(path):
            return []

        return str(self.container.pull(path, encoding="utf-8").read()).split("\n")

    @override
    def write(self, content: str, path: str) -> None:
        self.container.push(path, content, make_dirs=True)

    @override
    def exec(self, command: list[str], working_dir: str | None = None) -> str:
        return str(self.container.exec(command, working_dir=working_dir).wait_output())

    @property
    @override
    def alive(self) -> bool:
        if not self.container_can_connect:
            return False

        return self.container.get_service(self.container.name).is_running()

    @property
    def container_can_connect(self) -> bool:
        """Check if a connection can be made to the container."""
        return self.container.can_connect()

    @property
    @override
    @retry(
        wait=wait_fixed(1),
        stop=stop_after_attempt(5),
        retry=retry_if_result(lambda result: result is False),
        retry_error_callback=lambda _: False,
    )
    def healthy(self) -> bool:
        """Flag to check if the unit service is reachable and serving requests."""
        if not self.alive:
            return False

        # netcat isn't a default utility, so can't guarantee it's on the charm containers
        # this ugly hack avoids needing netcat
        bash_netcat = (
            f"echo '4lw' | (exec 3<>/dev/tcp/localhost/{CLIENT_PORT}; cat >&3; cat <&3; exec 3<&-)"
        )
        ruok = [bash_netcat.replace("4lw", "ruok")]
        srvr = [bash_netcat.replace("4lw", "srvr")]

        # timeout needed as it can sometimes hang forever if there's a problem
        # for example when the endpoint is unreachable
        timeout = ["timeout", "10s", "bash", "-c"]

        try:
            ruok_response = self.exec(command=timeout + ruok)
            if not ruok_response or "imok" not in ruok_response:
                return False

            srvr_response = self.exec(command=timeout + srvr)
            if not srvr_response or "not currently serving requests" in srvr_response:
                return False
        except (ExecError, CalledProcessError):
            return False

        return True

    # --- ZK Specific ---

    def install(self) -> None:
        """Loads the ZooKeeper snap from LP, returning a StatusBase for the Charm to set."""
        raise NotImplementedError

    def generate_password(self) -> str:
        """Creates randomized string for use as app passwords.

        Returns:
            String of 32 randomized letter+digit characters
        """
        return "".join([secrets.choice(string.ascii_letters + string.digits) for _ in range(32)])

    @override
    def get_version(self) -> str:
        if not self.alive:
            return ""

        if not self.healthy:
            return ""

        stat = [
            "bash",
            "-c",
            f"echo 'stat' | (exec 3<>/dev/tcp/localhost/{CLIENT_PORT}; cat >&3; cat <&3; exec 3<&-)",
        ]

        try:
            stat_response = self.exec(command=stat)
            if not stat_response:
                return ""

            matcher = re.search(r"(?P<version>\d\.\d\.\d)", stat_response)
            version = matcher.group("version") if matcher else ""

        except (ExecError, CalledProcessError):
            return ""

        return version
