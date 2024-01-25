#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
import yaml
from ops import RelationBrokenEvent
from ops.testing import Harness

from charm import ZooKeeperCharm
from literals import CHARM_KEY, PEER, REL_NAME

logger = logging.getLogger(__name__)

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    upgrade_rel_id = harness.add_relation("upgrade", CHARM_KEY)
    harness.add_relation("restart", CHARM_KEY)
    harness.update_relation_data(upgrade_rel_id, f"{CHARM_KEY}/0", {"state": "idle"})
    harness.add_relation(PEER, CHARM_KEY)
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_client_relation_updated_defers_if_not_stable_leader(harness):
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=False),
        patch("ops.framework.EventBase.defer") as patched_defer,
        patch("managers.quorum.QuorumManager.update_acls") as patched_acls,
    ):
        app_id = harness.add_relation(REL_NAME, "application")
        harness.update_relation_data(app_id, "application", {"chroot": "balrog"})

        patched_acls.assert_not_called()
        patched_defer.assert_called()


def test_client_relation_updated_succeeds(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("ops.framework.EventBase.defer") as patched_defer,
        patch("managers.quorum.QuorumManager.update_acls") as patched_acls,
        patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"),
    ):
        app_id = harness.add_relation(REL_NAME, "application")
        harness.update_relation_data(app_id, "application", {"chroot": "balrog"})

        patched_acls.assert_called()
        patched_defer.assert_not_called()


def test_client_relation_updated_creates_passwords_with_chroot(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"),
    ):
        app_id = harness.add_relation(REL_NAME, "application")
        assert not harness.charm.state.cluster.client_passwords

        harness.update_relation_data(app_id, "application", {"ungoliant": "spider"})
        assert not harness.charm.state.cluster.client_passwords

        harness.update_relation_data(app_id, "application", {"chroot": "balrog"})
        assert harness.charm.state.cluster.client_passwords


def test_client_relation_broken_sets_acls_with_broken_events(harness):
    with harness.hooks_disabled():
        app_id = harness.add_relation(REL_NAME, "application")
        harness.set_leader(True)

    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls") as patched_update_acls,
        patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"),
    ):
        harness.update_relation_data(app_id, "application", {"chroot": "balrog"})
        patched_update_acls.assert_called_with(event=None)

    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls") as patched_update_acls,
        patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"),
    ):
        harness.remove_relation(app_id)

        isinstance(patched_update_acls.call_args_list[0], RelationBrokenEvent)


def test_client_relation_broken_removes_passwords(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)
        harness.set_planned_units(1)
        app_id = harness.add_relation(REL_NAME, "application")

    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"),
    ):
        harness.update_relation_data(app_id, "application", {"chroot": "balrog"})
        assert harness.charm.state.cluster.client_passwords

        harness.remove_relation(app_id)
        assert not harness.charm.state.cluster.client_passwords
