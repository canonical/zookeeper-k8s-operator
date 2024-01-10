#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import yaml
from ops.testing import Harness

from charm import ZooKeeperCharm
from literals import CHARM_KEY

logger = logging.getLogger(__name__)

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    harness.add_relation("restart", CHARM_KEY)
    upgrade_rel_id = harness.add_relation("upgrade", CHARM_KEY)
    harness.update_relation_data(upgrade_rel_id, f"{CHARM_KEY}/0", {"state": "idle"})
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_get_updated_servers(harness):
    added_servers = [
        "server.2=gandalf.the.grey",
    ]
    removed_servers = [
        "server.2=gandalf.the.grey",
        "server.3=in.a.hole.in.the.ground.there.lived.a:hobbit",
    ]
    updated_servers = harness.charm.quorum_manager._get_updated_servers(
        add=added_servers, remove=removed_servers
    )

    assert updated_servers == {"2": "removed", "1": "added"}


def test_is_child_of(harness):
    chroot = "/gandalf/the/white"
    chroots = {"/gandalf", "/saruman"}

    assert harness.charm.quorum_manager._is_child_of(path=chroot, chroots=chroots)


def test_is_child_of_not(harness):
    chroot = "/the/one/ring"
    chroots = {"/gandalf", "/saruman"}

    assert not harness.charm.quorum_manager._is_child_of(path=chroot, chroots=chroots)
