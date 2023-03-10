#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import DEFAULT, patch

import pytest
import yaml
from ops.testing import Harness

from charm import ZooKeeperK8sCharm
from literals import CERTS_REL_NAME, CHARM_KEY, CONTAINER, PEER

CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


@pytest.fixture
def harness():
    harness = Harness(ZooKeeperK8sCharm, meta=METADATA, config=CONFIG, actions=ACTIONS)
    peer_rel_id = harness.add_relation(PEER, CHARM_KEY)
    harness.add_relation_unit(peer_rel_id, f"{CHARM_KEY}/0")
    harness._update_config({"init-limit": 5, "sync-limit": 2, "tick-time": 2000})
    harness.set_can_connect(CONTAINER, True)
    harness.begin()
    return harness


def test_all_units_unified_succeeds(harness):
    harness.update_relation_data(
        harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"unified": "true"}
    )
    harness.set_planned_units(1)
    assert harness.charm.tls.all_units_unified


def test_all_units_unified_fails(harness):
    assert not harness.charm.tls.all_units_unified


def test_all_units_unified_fails_if_not_all_units_related(harness):
    harness.set_planned_units(3)
    assert not harness.charm.tls.all_units_unified


def test_certificates_created_defers_if_not_stable(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with patch("ops.framework.EventBase.defer") as patched:
        harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

    patched.assert_called_once()
    assert not harness.charm.tls.enabled


def test_certificates_created_sets_upgrading_enabled(harness):
    with harness.hooks_disabled():
        harness.set_leader(True)

    with patch("ops.framework.EventBase.defer"), patch("cluster.ZooKeeperCluster.stable"):
        harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

    assert harness.charm.tls.enabled
    assert harness.charm.tls.upgrading


def test_certificates_joined_defers_if_disabled(harness):
    with (
        patch("ops.framework.EventBase.defer") as patched,
        patch("tls.ZooKeeperTLS._request_certificate"),
        patch("cluster.ZooKeeperCluster.stable", return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
        harness.add_relation_unit(cert_rel_id, "tls-certificates-operator/1")

    patched.assert_called_once()
    assert not harness.charm.tls.private_key


def test_certificates_joined_creates_private_key_if_enabled(harness):
    with (
        patch("tls.ZooKeeperTLS._request_certificate"),
        patch("cluster.ZooKeeperCluster.stable", return_value=True),
        patch("tls.ZooKeeperTLS.enabled", return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
        harness.add_relation_unit(cert_rel_id, "tls-certificates-operator/1")

    assert harness.charm.tls.private_key
    assert "BEGIN RSA PRIVATE KEY" in harness.charm.tls.private_key.splitlines()[0]


def test_certificates_joined_creates_new_keystore_password(harness):
    assert not harness.charm.tls.keystore_password

    with (
        patch("tls.ZooKeeperTLS._request_certificate"),
        patch("cluster.ZooKeeperCluster.stable", return_value=True),
        patch("tls.ZooKeeperTLS.enabled", return_value=True),
    ):
        cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
        harness.add_relation_unit(cert_rel_id, "tls-certificates-operator/1")

    assert harness.charm.tls.keystore_password


def test_certificates_available_fails_wrong_csr(harness):
    cert_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")
    harness.update_relation_data(cert_rel_id, f"{CHARM_KEY}/0", {"csr": "not-missing"})

    harness.charm.tls.certificates.on.certificate_available.emit(
        certificate_signing_request="missing", certificate="cert", ca="ca", chain=["ca", "cert"]
    )

    assert not harness.charm.tls.certificate
    assert not harness.charm.tls.ca


def test_certificates_available_succeeds(harness):
    harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

    # implicitly tests restart call
    harness.add_relation(harness.charm.restart.name, "{CHARM_KEY}/0")

    harness.update_relation_data(
        harness.charm.cluster.relation.id, f"{CHARM_KEY}/0", {"csr": "not-missing"}
    )

    # implicitly tests these method calls
    with patch.multiple(
        "tls.ZooKeeperTLS",
        set_server_key=DEFAULT,
        set_ca=DEFAULT,
        set_certificate=DEFAULT,
        set_truststore=DEFAULT,
        set_p12_keystore=DEFAULT,
    ):
        harness.charm.tls.certificates.on.certificate_available.emit(
            certificate_signing_request="not-missing",
            certificate="cert",
            ca="ca",
            chain=["ca", "cert"],
        )

        assert harness.charm.tls.certificate
        assert harness.charm.tls.ca


def test_certificates_broken(harness):
    with harness.hooks_disabled():
        certs_rel_id = harness.add_relation(CERTS_REL_NAME, "tls-certificates-operator")

        harness.update_relation_data(
            harness.charm.cluster.relation.id,
            f"{CHARM_KEY}/0",
            {"csr": "not-missing", "certificate": "cert", "ca": "exists"},
        )
        harness.set_leader(True)

    assert harness.charm.tls.certificate
    assert harness.charm.tls.ca
    assert harness.charm.tls.csr

    # implicitly tests these method calls
    with patch.multiple(
        "tls.ZooKeeperTLS",
        remove_stores=DEFAULT,
    ):
        harness.remove_relation(certs_rel_id)

        assert not harness.charm.tls.certificate
        assert not harness.charm.tls.ca
        assert not harness.charm.tls.csr

        assert not harness.charm.tls.enabled
        assert harness.charm.tls.upgrading


def test_certificates_expiring(harness):
    key = open("tests/keys/0.key").read()

    harness.update_relation_data(
        harness.charm.cluster.relation.id,
        f"{CHARM_KEY}/0",
        {"csr": "csr", "private-key": key, "certificate": "cert"},
    )

    with patch(
        "charms.tls_certificates_interface.v1.tls_certificates.TLSCertificatesRequiresV1.request_certificate_renewal",
        return_value=None,
    ):
        harness.charm.tls.certificates.on.certificate_expiring.emit(
            certificate="cert", expiry=None
        )

        assert harness.charm.tls.csr != "csr"


def test_parse_tls_pem(harness):
    key = open("tests/keys/0.key").read()

    parsed_key = harness.charm.tls._parse_tls_file(raw_content=key)
    assert parsed_key.encode("utf-8") == key.encode("utf-8")


def test_parse_tls_b64(harness):
    key = open("tests/keys/0.key").read()
    key_b64 = open("tests/keys/0.key.enc").read()

    parsed_key = harness.charm.tls._parse_tls_file(raw_content=key_b64)

    assert parsed_key == key
