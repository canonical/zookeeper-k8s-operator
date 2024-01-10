#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Implementation of WorkloadBase for running on K8s."""
import logging
import secrets
import string

from ops.model import Container
from ops.pebble import ChangeError, Layer
from typing_extensions import override

from core.workload import WorkloadBase

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

    @override
    def alive(self) -> bool:
        return self.container.can_connect()

    @override
    def healthy(self) -> bool:
        return self.container.get_service(self.container.name).is_running()

    # --- ZK Specific ---

    def generate_password(self) -> str:
        """Creates randomized string for use as app passwords.

        Returns:
            String of 32 randomized letter+digit characters
        """
        return "".join([secrets.choice(string.ascii_letters + string.digits) for _ in range(32)])
