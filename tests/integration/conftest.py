#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import os
import shutil

import pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture(scope="module", autouse=True)
def copy_data_interfaces_library_into_charm(ops_test: OpsTest):
    """Copy the data_interfaces library to the different charm folder."""
    library_path = "lib/charms/data_platform_libs/v0/data_interfaces.py"
    install_path = "tests/integration/app-charm/" + library_path
    os.makedirs(os.path.dirname(install_path), exist_ok=True)
    shutil.copyfile(library_path, install_path)
