#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import DEFAULT, PropertyMock, patch

import pytest
import yaml
from ops.testing import Harness

from charm import ZooKeeperCharm
from literals import CERTS_REL_NAME, CHARM_KEY, PEER

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    harness.add_relation(PEER, CHARM_KEY)
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.begin()
    return harness


def test_all_units_unified_succeeds(harness):
    harness.update_relation_data(
        harness.charm.state.peer_relation.id, f"{CHARM_KEY}/0", {"unified": "true"}
    )
    harness.set_planned_units(1)
    assert harness.charm.state.all_units_unified


def test_all_units_unified_fails(harness):
    harness.set_planned_units(3)
    harness.update_relation_data(
        harness.charm.state.peer_relation.id,
        f"{CHARM_KEY}/0",
        {"unified": "true", "state": "started"},
    )
    assert not harness.charm.state.all_units_unified


def test_all_units_unified_fails_if_not_all_units_related(harness):
    harness.set_planned_units(3)
    assert not harness.charm.state.all_units_unified


def test_certificates_created_defers_if_not_stable(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with patch("ops.framework.EventBase.defer") as patched:
        harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

    patched.assert_called_once()
    assert not harness.charm.state.cluster.tls


def test_certificates_created_sets_upgrading_enabled(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with (
        patch("ops.framework.EventBase.defer"),
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
    ):
        harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

    assert harness.charm.state.cluster.tls
    assert harness.charm.state.cluster.switching_encryption


def test_certificates_joined_defers_if_disabled(harness):
    with (
        patch("ops.framework.EventBase.defer") as patched,
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
        harness.add_relation_unit(cert_rel_id, "tls-certificates-operator/1")

    patched.assert_called_once()
    assert not harness.charm.state.unit_server.private_key


def test_certificates_joined_creates_private_key_if_enabled(harness):
    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("core.models.ZKCluster.tls", new_callable=PropertyMock, return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
        harness.add_relation_unit(cert_rel_id, "tls-certificates-operator/1")

    assert harness.charm.state.unit_server.private_key
    assert "BEGIN RSA PRIVATE KEY" in harness.charm.state.unit_server.private_key.splitlines()[0]


def test_certificates_joined_creates_new_key_trust_store_password(harness):
    assert not harness.charm.state.unit_server.keystore_password
    assert not harness.charm.state.unit_server.truststore_password

    with (
        patch("core.cluster.ClusterState.stable", new_callable=PropertyMock, return_value=True),
        patch("core.models.ZKCluster.tls", new_callable=PropertyMock, return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
        harness.add_relation_unit(cert_rel_id, "tls-certificates-operator/1")

    assert harness.charm.state.unit_server.keystore_password
    assert harness.charm.state.unit_server.truststore_password

    assert (
        harness.charm.state.unit_server.keystore_password
        != harness.charm.state.unit_server.truststore_password
    )


def test_certificates_available_fails_wrong_csr(harness):
    cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
    harness.update_relation_data(cert_rel_id, f"{CHARM_KEY}/0", {"csr": "not-missing"})

    harness.charm.tls_events.certificates.on.certificate_available.emit(
        certificate_signing_request="missing", certificate="cert", ca="ca", chain=["ca", "cert"]
    )

    assert not harness.charm.state.unit_server.certificate
    assert not harness.charm.state.unit_server.ca


def test_certificates_available_succeeds(harness):
    harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

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
        assert harness.charm.state.unit_server.ca


def test_certificates_broken(harness):
    with harness.hooks_disabled():
        certs_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

        harness.update_relation_data(
            harness.charm.state.peer_relation.id,
            f"{CHARM_KEY}/0",
            {"csr": "not-missing", "certificate": "cert", "ca": "exists"},
        )
        harness.set_leader(True)

    assert harness.charm.state.unit_server.certificate
    assert harness.charm.state.unit_server.ca
    assert harness.charm.state.unit_server.csr

    # implicitly tests these method calls
    with patch.multiple(
        "managers.tls.TLSManager",
        remove_stores=DEFAULT,
    ):
        harness.remove_relation(certs_rel_id)

        assert not harness.charm.state.unit_server.certificate
        assert not harness.charm.state.unit_server.ca
        assert not harness.charm.state.unit_server.csr
        assert not harness.charm.state.cluster.tls
        assert harness.charm.state.cluster.switching_encryption


def test_certificates_expiring(harness):
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


def test_set_tls_private_key(harness):
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
