#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""General purpose helper functions for managing common charm functions."""

import secrets
import string

from ops.model import Container


def push(container: Container, content: str, path: str) -> None:
    """Simple wrapper for writing a file and contents to a container.

    Args:
        container: the container to run on
        content: the text content to write to a file path
        path: the full path of the desired file
    """
    container.push(path, content, make_dirs=True)


def pull(container: Container, path: str) -> str:
    """Simple wrapper for reading a file and contents from a container.

    Args:
        container: the container to run on
        path: the full path of the desired file

    Returns:
        String of container file contents
    """
    return str(container.pull(path, encoding="utf-8").read())


def generate_password() -> str:
    """Creates randomized string for use as app passwords.

    Returns:
        String of 32 randomized letter+digit characters
    """
    return "".join([secrets.choice(string.ascii_letters + string.digits) for _ in range(32)])
