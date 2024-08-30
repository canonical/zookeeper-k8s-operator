#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import DEFAULT, PropertyMock, patch

import pytest
import yaml
from ops.testing import Harness

from charm import ZooKeeperCharm
from literals import CERTS_REL_NAME, CHARM_KEY, PEER, Status

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))

TLS_NAME = "self-signed-certificates"


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    harness.add_relation(PEER, CHARM_KEY)
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_all_units_unified_succeeds(harness: Harness[ZooKeeperCharm]):
    harness.update_relation_data(
        harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"unified": "true"}
    )
    harness.set_planned_units(1)
    assert harness.charm.state.all_units_unified


def test_all_units_unified_fails(harness: Harness[ZooKeeperCharm]):
    harness.set_planned_units(3)
    harness.update_relation_data(
        harness.charm.state.peer_relation.id,
        f"{CHARM_KEY}/0",
        {"unified": "true", "state": "started"},
    )
    assert not harness.charm.state.all_units_unified


def test_all_units_unified_fails_if_not_all_units_related(harness: Harness[ZooKeeperCharm]):
    harness.set_planned_units(3)
    assert not harness.charm.state.all_units_unified


def test_certificates_created_defers_if_not_stable(harness: Harness[ZooKeeperCharm]):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with patch("ops.framework.EventBase.defer") as patched:
        harness.add_relation(CERTS_REL_NAME, TLS_NAME)

    patched.assert_called_once()
    assert not harness.charm.state.cluster.tls


def test_certificates_created_sets_upgrading_enabled(harness: Harness[ZooKeeperCharm]):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with (
        patch("ops.framework.EventBase.defer"),
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
    ):
        harness.add_relation(CERTS_REL_NAME, TLS_NAME)

    assert harness.charm.state.cluster.tls
    assert harness.charm.state.cluster.switching_encryption


def test_certificates_joined_defers_if_disabled(harness: Harness[ZooKeeperCharm]):
    with (
        patch("ops.framework.EventBase.defer") as patched,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, TLS_NAME)
        harness.add_relation_unit(cert_rel_id, f"{TLS_NAME}/1")

    patched.assert_called_once()
    assert not harness.charm.state.unit_server.private_key


def test_certificates_joined_creates_private_key_if_enabled(harness: Harness[ZooKeeperCharm]):
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("core.models.ZKCluster.tls", new_callable=PropertyMock, return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, TLS_NAME)
        harness.add_relation_unit(cert_rel_id, f"{TLS_NAME}/1")

    assert harness.charm.state.unit_server.private_key
    assert "BEGIN RSA PRIVATE KEY" in harness.charm.state.unit_server.private_key.splitlines()[0]


def test_certificates_joined_creates_new_key_trust_store_password(
    harness: Harness[ZooKeeperCharm],
):
    assert not harness.charm.state.unit_server.keystore_password
    assert not harness.charm.state.unit_server.truststore_password

    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("core.models.ZKCluster.tls", new_callable=PropertyMock, return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, TLS_NAME)
        harness.add_relation_unit(cert_rel_id, f"{TLS_NAME}/1")

    assert harness.charm.state.unit_server.keystore_password
    assert harness.charm.state.unit_server.truststore_password

    assert (
        harness.charm.state.unit_server.keystore_password
        != harness.charm.state.unit_server.truststore_password
    )


def test_certificates_available_fails_wrong_csr(harness: Harness[ZooKeeperCharm]):
    cert_rel_id = harness.add_relation(CERTS_REL_NAME, TLS_NAME)
    harness.update_relation_data(cert_rel_id, f"{CHARM_KEY}/0", {"csr": "not-missing"})

    harness.charm.tls_events.certificates.on.certificate_available.emit(
        certificate_signing_request="missing", certificate="cert", ca="ca", chain=["ca", "cert"]
    )

    assert not harness.charm.state.unit_server.certificate
    assert not harness.charm.state.unit_server.ca_cert


def test_certificates_available_succeeds(harness: Harness[ZooKeeperCharm]):
    harness.add_relation(CERTS_REL_NAME, TLS_NAME)

    # implicitly tests restart call
    harness.charm.unit.add_secret({"csr": "not-missing"}, label=f"{PEER}.zookeeper-k8s.unit")

    # implicitly tests these method calls
    with patch.multiple(
        "managers.tls.TLSManager",
        set_private_key=DEFAULT,
        set_ca=DEFAULT,
        set_certificate=DEFAULT,
        set_truststore=DEFAULT,
        set_p12_keystore=DEFAULT,
    ):
        harness.charm.tls_events.certificates.on.certificate_available.emit(
            certificate_signing_request="not-missing",
            certificate="cert",
            ca="ca",
            chain=["ca", "cert"],
        )

        assert harness.charm.state.unit_server.certificate
        assert harness.charm.state.unit_server.ca_cert

    # The certs are saved in a secret, with expected keys
    secret = harness.charm.model.get_secret(label=f"{PEER}.zookeeper-k8s.unit")
    assert secret.peek_content() == {
        "csr": "not-missing",
        "certificate": "cert",
        "ca-cert": "ca",
    }


def test_renew_certificates_auto_reload(harness: Harness[ZooKeeperCharm]):
    # Setup working relation
    harness.add_relation(CERTS_REL_NAME, TLS_NAME)
    harness.add_relation(harness.charm.restart.name, "{CHARM_KEY}/0")

    harness.update_relation_data(
        harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"csr": "not-missing"}
    )

    with patch.multiple(
        "managers.tls.TLSManager",
        set_private_key=DEFAULT,
        set_ca=DEFAULT,
        set_certificate=DEFAULT,
        set_truststore=DEFAULT,
        set_p12_keystore=DEFAULT,
    ):
        harness.charm.tls_events.certificates.on.certificate_available.emit(
            certificate_signing_request="not-missing",
            certificate="cert",
            ca="ca",
            chain=["ca", "cert"],
        )

    with (
        patch.multiple(
            "managers.tls.TLSManager",
            set_private_key=DEFAULT,
            set_ca=DEFAULT,
            set_certificate=DEFAULT,
            set_truststore=DEFAULT,
            set_p12_keystore=DEFAULT,
        ),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock"
        ) as patched_restart,
    ):
        harness.charm.tls_events.certificates.on.certificate_available.emit(
            certificate_signing_request="not-missing",
            certificate="new-cert",
            ca="ca",
            chain=["ca", "cert"],
        )

    assert harness.charm.state.unit_server.certificate == "new-cert"
    assert not patched_restart.called


def test_certificates_available_halfway_through_upgrade_succeeds(harness: Harness[ZooKeeperCharm]):
    harness.add_relation(CERTS_REL_NAME, TLS_NAME)

    # implicitly tests restart call
    harness.add_relation(harness.charm.restart.name, "{CHARM_KEY}/0")

    harness.update_relation_data(
        harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"csr": "not-missing"}
    )

    # implicitly tests these method calls
    with patch.multiple(
        "managers.tls.TLSManager",
        set_private_key=DEFAULT,
        set_ca=DEFAULT,
        set_certificate=DEFAULT,
        set_truststore=DEFAULT,
        set_p12_keystore=DEFAULT,
    ):
        harness.charm.tls_events.certificates.on.certificate_available.emit(
            certificate_signing_request="not-missing",
            certificate="cert",
            ca="ca",
            chain=["ca", "cert"],
        )

        assert harness.charm.state.unit_server.certificate
        assert harness.charm.state.unit_server.ca_cert

        # The certs are saved in a secret, with expected keys
        secret = harness.charm.model.get_secret(label=f"{PEER}.zookeeper-k8s.unit")
        assert secret.get_content() == {"certificate": "cert", "ca-cert": "ca"}

        # The correct CSR is preserved still in databag
        assert harness.charm.state.unit_server.csr == "not-missing"


def test_certificates_broken(harness: Harness[ZooKeeperCharm]):
    with harness.hooks_disabled():
        certs_rel_id = harness.add_relation(CERTS_REL_NAME, TLS_NAME)

        harness.charm.unit.add_secret(
            {"csr": "not-missing", "certificate": "cert", "ca-cert": "exists"},
            label=f"{PEER}.zookeeper-k8s.unit",
        )
        harness.set_leader(True)

    assert harness.charm.state.unit_server.certificate
    assert harness.charm.state.unit_server.ca_cert
    assert harness.charm.state.unit_server.csr

    # implicitly tests these method calls
    with patch.multiple(
        "managers.tls.TLSManager",
        remove_stores=DEFAULT,
    ):
        harness.remove_relation(certs_rel_id)

        assert not harness.charm.state.unit_server.certificate
        assert not harness.charm.state.unit_server.ca_cert
        assert not harness.charm.state.unit_server.csr
        assert not harness.charm.state.cluster.tls
        assert harness.charm.state.cluster.switching_encryption


def test_certificates_broken_after_upgrade(harness: Harness[ZooKeeperCharm]):
    with harness.hooks_disabled():
        certs_rel_id = harness.add_relation(CERTS_REL_NAME, TLS_NAME)

        # Databag was used before the upgrade
        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {"csr": "not-missing", "certificate": "cert", "ca": "exists"},
        )

        harness.set_leader(True)

    assert harness.charm.state.unit_server.certificate
    assert harness.charm.state.unit_server.ca_cert
    assert harness.charm.state.unit_server.csr

    # implicitly tests these method calls
    with patch.multiple(
        "managers.tls.TLSManager",
        remove_stores=DEFAULT,
    ):
        harness.remove_relation(certs_rel_id)

        assert not harness.charm.state.unit_server.certificate
        assert not harness.charm.state.unit_server.ca_cert
        assert not harness.charm.state.unit_server.csr
        assert not harness.charm.state.cluster.tls
        assert harness.charm.state.cluster.switching_encryption


def test_certificates_expiring(harness: Harness[ZooKeeperCharm]):
    key = open("tests/keys/0.key").read()
    harness.update_relation_data(
        harness.charm.state.peer_relation.id,
        f"{CHARM_KEY}/0",
        {
            "csr": "csr",
            "private-key": key,
            "certificate": "cert",
            "hostname": "treebeard",
            "ip": "1.1.1.1",
            "fqdn": "fangorn",
        },
    )

    with patch(
        "charms.tls_certificates_interface.v1.tls_certificates.TLSCertificatesRequiresV1.request_certificate_renewal",
        return_value=None,
    ):
        harness.charm.tls_events.certificates.on.certificate_expiring.emit(
            certificate="cert", expiry=None
        )

        assert harness.charm.state.unit_server.csr != "csr"


def test_set_tls_private_key(harness: Harness[ZooKeeperCharm]):
    harness.update_relation_data(
        harness.charm.state.peer_relation.id,
        f"{CHARM_KEY}/0",
        {
            "csr": "csr",
            "private-key": "mellon",
            "certificate": "cert",
            "hostname": "treebeard",
            "ip": "1.1.1.1",
            "fqdn": "fangorn",
        },
    )
    key = open("tests/keys/0.key").read()

    with patch(
        "charms.tls_certificates_interface.v1.tls_certificates.TLSCertificatesRequiresV1.request_certificate_renewal",
        return_value=None,
    ):
        harness.run_action("set-tls-private-key", {"internal-key": key})

        assert harness.charm.state.unit_server.csr != "csr"
