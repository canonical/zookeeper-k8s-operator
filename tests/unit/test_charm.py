#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import re
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
import yaml
from ops.framework import EventBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.testing import Harness

from charm import ZooKeeperK8sCharm
from core.models import ZKClient
from literals import CHARM_KEY, CONTAINER, PEER, REL_NAME, SUBSTRATE

logger = logging.getLogger(__name__)

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperK8sCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)

    if SUBSTRATE == "k8s":
        harness.set_can_connect(CONTAINER, True)

    harness.add_relation("restart", CHARM_KEY)
    upgrade_rel_id = harness.add_relation("upgrade", CHARM_KEY)
    harness.update_relation_data(upgrade_rel_id, f"{CHARM_KEY}/0", {"state": "idle"})
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_install_fails_create_passwords_until_peer_relation(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with patch("workload.ZKWorkload.install"):
        harness.charm.on.install.emit()

    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)

    assert not harness.charm.state.cluster.internal_user_credentials


def test_install_fails_creates_passwords_succeeds(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)

    with patch("workload.ZKWorkload.install"):
        harness.charm.on.install.emit()

        assert harness.charm.state.cluster.internal_user_credentials


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="Snap not used on K8s charms")
def test_install_blocks_snap_install_failure(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)

    with patch("workload.ZKWorkload.install", return_value=False):
        harness.charm.on.install.emit()

        assert isinstance(harness.model.unit.status, BlockedStatus)


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="DNS managed by Kubernetes for K8s charms")
def test_install_sets_ip_hostname_fqdn(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)

    with patch("workload.ZKWorkload.install", return_value=False):
        harness.charm.on.install.emit()

        assert harness.charm.state.unit_server.ip
        assert harness.charm.state.unit_server.hostname
        assert harness.charm.state.unit_server.fqdn


@pytest.mark.skipif(SUBSTRATE == "k8s", reason="DNS managed by Kubernetes for K8s charms")
def test_relation_changed_updates_ip_hostname_fqdn(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_leader(True)
        harness.update_relation_data(
            peer_rel_id, f"{CHARM_KEY}/0", {"ip": "gandalf-the-grey", "state": "started"}
        )

    with (
        patch(
            "managers.quorum.QuorumManager.get_hostname_mapping",
            return_value={"ip": "gandalf-the-white"},
        ),
        patch("managers.config.ConfigManager.config_changed", return_value=False),
        patch("charm.ZooKeeperK8sCharm.update_quorum"),
    ):
        harness.charm.on.cluster_relation_changed.emit(harness.charm.state.peer_relation)

    assert harness.charm.state.unit_server.ip == "gandalf-the-white"


def test_relation_changed_emitted_for_leader_elected(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with (patch("charm.ZooKeeperK8sCharm._on_cluster_relation_changed") as patched):
        harness.set_leader(True)
        patched.assert_called_once()


def test_relation_changed_emitted_for_config_changed(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with patch("charm.ZooKeeperK8sCharm._on_cluster_relation_changed") as patched:
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_changed(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with patch("charm.ZooKeeperK8sCharm._on_cluster_relation_changed") as patched:
        harness.charm.on.cluster_relation_changed.emit(harness.charm.state.peer_relation)
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_joined(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with patch("charm.ZooKeeperK8sCharm._on_cluster_relation_changed") as patched:
        harness.charm.on.cluster_relation_joined.emit(harness.charm.state.peer_relation)
        patched.assert_called_once()


def test_relation_changed_emitted_for_relation_departed(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with patch("charm.ZooKeeperK8sCharm._on_cluster_relation_changed") as patched:
        harness.charm.on.cluster_relation_departed.emit(harness.charm.state.peer_relation)
        patched.assert_called_once()


def test_relation_changed_starts_units(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_planned_units(1)

    with (
        patch("charm.ZooKeeperK8sCharm.init_server") as patched,
        patch("managers.config.ConfigManager.config_changed"),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_relation_changed_does_not_start_units_again(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})

    with (
        patch("charm.ZooKeeperK8sCharm.init_server") as patched,
        patch("managers.config.ConfigManager.config_changed"),
    ):
        harness.charm.on.config_changed.emit()
        patched.assert_not_called()


def test_relation_changed_does_not_restart_on_departing(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with patch("charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock") as patched:
        harness.remove_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        patched.assert_not_called()


def test_relation_changed_updates_quorum(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with (
        patch("charm.ZooKeeperK8sCharm.update_quorum") as patched,
        patch("managers.config.ConfigManager.config_changed"),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_relation_changed_restarts(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})

    with (
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"
        ) as patched_restart,
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        harness.charm.on.config_changed.emit()
        patched_restart.assert_called_once()


def test_relation_changed_defers_switching_encryption_single_unit(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
        harness.update_relation_data(peer_rel_id, CHARM_KEY, {"switching-encryption": "started"})

    with (
        patch("ops.framework.EventBase.defer") as patched,
        patch("managers.config.ConfigManager.config_changed"),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        harness.charm.on.config_changed.emit()
        patched.assert_called_once()


def test_restart_fails_not_related(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with (
        patch("workload.ZKWorkload.restart") as patched,
        patch("ops.framework.EventBase.defer"),
    ):
        harness.charm._restart(EventBase)
        patched.assert_not_called()


def test_restart_fails_not_started(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_planned_units(1)

    with (
        patch("workload.ZKWorkload.restart") as patched,
        patch("ops.framework.EventBase.defer"),
    ):
        harness.charm._restart(EventBase)
        patched.assert_not_called()


def test_restart_fails_not_added(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_planned_units(1)
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})

    with (
        patch("workload.ZKWorkload.restart") as patched,
        patch("ops.framework.EventBase.defer"),
    ):
        harness.charm._restart(EventBase)
        patched.assert_not_called()


@pytest.mark.parametrize("stable, restarts", [(True, 1), (False, 0)])
def test_restart_restarts_with_sleep(harness, stable, restarts):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_planned_units(1)
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}", {"0": "added"})

    with (
        patch("workload.ZKWorkload.restart") as patched_restart,
        patch("time.sleep") as patched_sleep,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=stable),
    ):
        harness.charm._restart(EventBase(harness.charm))
        assert patched_restart.call_count == restarts
        assert patched_sleep.call_count == restarts


def test_restart_restarts_snap_sets_active_status(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_planned_units(1)
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}", {"0": "added"})

    with (
        patch("workload.ZKWorkload.restart"),
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("time.sleep"),
    ):
        harness.charm._restart(EventBase(harness.charm))
        assert isinstance(harness.model.unit.status, ActiveStatus)


def test_restart_sets_password_rotated_on_unit(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.set_planned_units(1)
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
        harness.update_relation_data(
            peer_rel_id, f"{CHARM_KEY}", {"0": "added", "rotate-passwords": "true"}
        )

    with (
        patch("workload.ZKWorkload.restart"),
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("time.sleep"),
    ):
        harness.charm._restart(EventBase(harness.charm))

        assert harness.charm.state.unit_server.password_rotated


def test_restart_sets_unified(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"state": "started"})
        harness.update_relation_data(peer_rel_id, CHARM_KEY, {"switching-encryption": "started"})

    with (
        patch("workload.ZKWorkload.restart"),
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("time.sleep"),
    ):
        harness.charm._restart(EventBase(harness.charm))
        assert harness.charm.state.unit_server.unified

    with harness.hooks_disabled():
        harness.update_relation_data(peer_rel_id, CHARM_KEY, {"switching-encryption": ""})

        with (
            patch("workload.ZKWorkload.restart"),
            patch(
                "core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True
            ),
            patch("time.sleep"),
        ):
            harness.charm._restart(EventBase)
            assert not harness.charm.state.unit_server.unified


def test_init_server_maintenance_if_no_passwords(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    harness.charm.init_server()

    assert isinstance(harness.charm.unit.status, MaintenanceStatus)


def test_init_server_maintenance_if_not_turn(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.update_relation_data(
            peer_rel_id, CHARM_KEY, {"sync-password": "mellon", "super-password": "mellon"}
        )

    with patch("core.cluster.ClusterState.next_server", return_value=None):
        harness.charm.init_server()

        assert isinstance(harness.charm.unit.status, MaintenanceStatus)


def test_init_server_calls_necessary_methods(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.update_relation_data(
            peer_rel_id,
            f"{CHARM_KEY}/0",
            {"ip": "aragorn", "fqdn": "legolas", "hostname": "gimli"},
        )
        harness.update_relation_data(
            peer_rel_id,
            CHARM_KEY,
            {
                "sync-password": "mellon",
                "super-password": "mellon",
                "switching-encryption": "started",
                "quorum": "ssl",
            },
        )
    with (
        patch("managers.config.ConfigManager.set_zookeeper_myid") as zookeeper_myid,
        patch("managers.config.ConfigManager.set_server_jvmflags") as server_jvmflags,
        patch(
            "managers.config.ConfigManager.set_zookeeper_dynamic_properties"
        ) as zookeeper_dynamic_properties,
        patch("managers.config.ConfigManager.set_zookeeper_properties") as zookeeper_properties,
        patch("managers.config.ConfigManager.set_jaas_config") as zookeeper_jaas_config,
        patch("workload.ZKWorkload.start") as start,
    ):
        harness.charm.init_server()

        zookeeper_myid.assert_called_once()
        server_jvmflags.assert_called_once()
        zookeeper_dynamic_properties.assert_called_once()
        zookeeper_properties.assert_called_once()
        zookeeper_jaas_config.assert_called_once()
        start.assert_called_once()

        assert harness.charm.state.unit_server.quorum == "ssl"
        assert harness.charm.state.unit_server.unified
        assert harness.charm.state.unit_server.started
        assert isinstance(harness.charm.unit.status, ActiveStatus)


def test_adding_units_updates_relation_data(harness):
    with (
        patch("managers.quorum.QuorumManager.update_cluster", return_value={"1": "added"}),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/1", {"quorum": "ssl"})

        assert 1 in harness.charm.state.cluster.quorum_unit_ids


def test_update_quorum_skips_relation_departed(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")

    with (
        patch("managers.quorum.QuorumManager.update_cluster") as patched_update_cluster,
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        harness.remove_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        patched_update_cluster.assert_not_called()


def test_update_quorum_updates_cluster_for_relation_departed(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
        harness.set_leader(True)

    with (
        patch("managers.quorum.QuorumManager.update_cluster") as patched_update_cluster,
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        harness.remove_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
        patched_update_cluster.assert_called()


def test_update_quorum_updates_cluster_for_leader_elected(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")

    with (
        patch("managers.quorum.QuorumManager.update_cluster") as patched_update_cluster,
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("charm.ZooKeeperK8sCharm.init_server"),
    ):
        harness.set_leader(True)
        patched_update_cluster.assert_called()


def test_update_quorum_adds_init_leader(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
        harness.update_relation_data(
            peer_rel_id,
            f"{CHARM_KEY}/0",
            {"state": "started"},
        )

    with (
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("charm.ZooKeeperK8sCharm.init_server"),
        patch("managers.quorum.QuorumManager.update_cluster"),
    ):
        harness.set_leader(True)

        assert harness.charm.state.cluster.quorum_unit_ids


def test_update_quorum_does_not_set_ssl_quorum_until_unified(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)
        harness.update_relation_data(peer_rel_id, CHARM_KEY, {"tls": "enabled"})
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")

    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"unified": ""})

    assert not harness.charm.state.cluster.quorum == "ssl"


def test_update_quorum_does_not_unset_upgrading_until_all_quorum(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)
        harness.update_relation_data(
            peer_rel_id,
            CHARM_KEY,
            {"tls": "enabled", "switching-encryption": "started", "quorum": "non-ssl"},
        )
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")

    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"quorum": "non-ssl"})

    assert not harness.charm.state.cluster.quorum == "ssl"


def test_update_quorum_unsets_upgrading_when_all_quorum(harness):
    with harness.hooks_disabled():
        peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)
        harness.update_relation_data(
            peer_rel_id,
            CHARM_KEY,
            {"tls": "enabled", "switching-encryption": "started", "quorum": "ssl"},
        )
        harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/1")
        harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/1", {"quorum": "ssl"})

    harness.update_relation_data(peer_rel_id, f"{CHARM_KEY}/0", {"quorum": "ssl"})

    assert harness.charm.state.cluster.quorum == "ssl"


def test_config_changed_applies_relation_data(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("charm.ZooKeeperK8sCharm.update_client_data", return_value=None) as patched,
        patch("core.cluster.ClusterState.stable", return_value=True),
        patch("core.cluster.ClusterState.ready", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("core.cluster.ClusterState.all_units_related", return_value=True),
        patch("core.cluster.ClusterState.all_units_declaring_ip", return_value=True),
    ):
        harness.charm.on.config_changed.emit()

        patched.assert_called_once()


def test_config_changed_fails_apply_relation_data_not_ready(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("core.models.ZKClient.update") as patched_update,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("core.cluster.ClusterState.ready", new_callable=PropertyMock, return_value=False),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        harness.charm.on.config_changed.emit()

        patched_update.assert_not_called()


def test_config_changed_fails_apply_relation_data_not_stable(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("charm.ZooKeeperK8sCharm.update_client_data", return_value=None) as patched,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=False),
        patch("core.cluster.ClusterState.ready", new_callable=PropertyMock, return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        harness.charm.on.config_changed.emit()

        patched.assert_not_called()


def test_update_quorum_updates_relation_data(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("charm.ZooKeeperK8sCharm.update_client_data", return_value=None) as patched,
        patch("core.cluster.ClusterState.stable", return_value=True),
        patch("core.cluster.ClusterState.ready", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        harness.charm.update_quorum(EventBase)

        patched.assert_called_once()


def test_update_quorum_fails_update_relation_data_if_not_stable(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("charm.ZooKeeperK8sCharm.update_client_data", return_value=None) as patched,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock(return_value=False)),
        patch("core.cluster.ClusterState.ready", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        harness.charm.update_quorum(EventBase)

        patched.assert_not_called()


def test_update_quorum_fails_update_relation_data_if_not_ready(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("core.models.ZKClient.update") as patched_update,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("core.cluster.ClusterState.ready", new_callable=PropertyMock, return_value=False),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        harness.charm.update_quorum(EventBase)

        patched_update.assert_not_called()


def test_restart_updates_relation_data(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("charm.ZooKeeperK8sCharm.update_client_data", return_value=None) as patched,
        patch("core.cluster.ClusterState.stable", return_value=True),
        patch("core.cluster.ClusterState.ready", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        harness.charm._restart(EventBase)

        patched.assert_called_once()


def test_restart_defers_if_not_stable(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("charm.ZooKeeperK8sCharm.update_client_data", return_value=None) as patched_apply,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock(return_value=False)),
        patch("core.cluster.ClusterState.ready", return_value=True),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
        patch("ops.framework.EventBase.defer") as patched_defer,
    ):
        harness.charm._restart(EventBase)

        patched_apply.assert_not_called()
        patched_defer.assert_called_once()


def test_restart_fails_update_relation_data_if_not_ready(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("core.models.ZKClient.update") as patched_update,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("core.cluster.ClusterState.ready", new_callable=PropertyMock, return_value=False),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        harness.charm._restart(EventBase)

        patched_update.assert_not_called()


def test_restart_fails_update_relation_data_if_not_idle(harness):
    with harness.hooks_disabled():
        _ = harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)

    with (
        patch("core.models.ZKClient.update") as patched_update,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("core.cluster.ClusterState.ready", new_callable=PropertyMock, return_value=False),
        patch("managers.config.ConfigManager.config_changed", return_value=True),
    ):
        harness.charm._restart(EventBase)

        patched_update.assert_not_called()


def test_port_updates_if_tls(harness):
    with harness.hooks_disabled():
        harness.add_relation(PEER, CHARM_KEY)
        app_id = harness.add_relation(REL_NAME, "application")
        harness.set_leader(True)
        harness.update_relation_data(app_id, "application", {"chroot": "app"})

        # checking if ssl port and ssl flag are passed
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {"private-address": "treebeard", "state": "started"},
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            CHARM_KEY,
            {"quorum": "ssl", "relation-0": "mellon", "tls": "enabled"},
        )
        harness.charm.update_client_data()

    uris = ""

    for client in harness.charm.state.clients:
        assert client.tls == "enabled"
        uris = client.uris

    with harness.hooks_disabled():
        # checking if normal port and non-ssl flag are passed
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {"private-address": "treebeard", "state": "started", "quorum": "non-ssl"},
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            CHARM_KEY,
            {"quorum": "non-ssl", "relation-0": "mellon", "tls": ""},
        )
        harness.charm.update_client_data()

    for client in harness.charm.state.clients:
        assert client.tls == "disabled"
        assert client.uris != uris


def test_update_relation_data(harness):
    with harness.hooks_disabled():
        harness.add_relation(PEER, CHARM_KEY)
        harness.set_leader(True)
        app_1_id = harness.add_relation(REL_NAME, "application")
        app_2_id = harness.add_relation(REL_NAME, "new_application")
        harness.update_relation_data(app_1_id, "application", {"chroot": "app"})
        harness.update_relation_data(
            app_2_id,
            "new_application",
            {"chroot": "new_app", "chroot-acl": "rw"},
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {
                "ip": "treebeard",
                "state": "started",
                "private-address": "glamdring",
                "hostname": "frodo",
            },
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/1")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/1",
            {"ip": "shelob", "state": "ready", "private-address": "narsil", "hostname": "sam"},
        )
        harness.add_relation_unit(harness.charm.state.peer_relation.id, f"{CHARM_KEY}/2")
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/2",
            {
                "ip": "balrog",
                "state": "started",
                "private-address": "anduril",
                "hostname": "merry",
            },
        )
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            CHARM_KEY,
            {f"relation-{app_1_id}": "mellon", f"relation-{app_2_id}": "friend"},
        )

    with patch("core.cluster.ClusterState.ready", new_callable=PropertyMock, return_value=True):
        harness.charm.update_client_data()

    # building bare clients for validation
    usernames = []
    passwords = []
    for relation in harness.charm.state.client_relations:
        client = ZKClient(
            relation=relation,
            substrate=SUBSTRATE,
            component=relation.app,
            local_app=harness.charm.app,
        )

        assert client.username, (
            client.password in harness.charm.state.cluster.client_passwords.items()
        )
        assert client.username not in usernames
        assert client.password not in passwords

        logger.info(client.endpoints)

        assert len(client.endpoints.split(",")) == 3
        assert len(client.uris.split(",")) == 3, client.uris

        # checking ips are used
        for ip in ["treebeard", "shelob", "balrog"]:
            assert ip in client.endpoints
            assert ip in client.uris

        # checking private-address or hostnames are NOT used
        for hostname_address in ["glamdring", "narsil", "anduril", "sam", "frodo", "merry"]:
            assert hostname_address not in client.endpoints
            assert hostname_address not in client.uris

        for uri in client.uris.split(","):
            # checking client_port in uri
            assert re.search(r":[\d]+", uri)

        assert client.uris.endswith(client.chroot)

        usernames.append(client.username)
        passwords.append(client.password)
