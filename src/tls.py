#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling ZooKeeper TLS configuration."""

import base64
import logging
import os
import re
import socket
from typing import Dict, List, Optional

from charms.tls_certificates_interface.v1.tls_certificates import (
    CertificateAvailableEvent,
    TLSCertificatesRequiresV1,
    generate_csr,
    generate_private_key,
)
from literals import PEER
from ops.charm import ActionEvent, RelationCreatedEvent, RelationJoinedEvent
from ops.framework import Object
from ops.model import Relation, Unit
from ops.pebble import ExecError

from literals import CONF_PATH, PEER
from utils import generate_password, push

logger = logging.getLogger(__name__)


class TLSDataNotFoundError(Exception):
    """Missing required data for TLS."""


class ZooKeeperTLS(Object):
    """Handler for managing the client and unit TLS keys/certs."""

    def __init__(self, charm):
        super().__init__(charm, "tls")
        self.charm = charm
        self.container = self.charm.unit.get_container("zookeeper")
        self.certificates = TLSCertificatesRequiresV1(self.charm, "certificates")

        self.framework.observe(
            getattr(self.charm.on, "set_tls_private_key_action"), self._set_tls_private_key
        )

        self.framework.observe(
            getattr(self.charm.on, "certificates_relation_created"), self._on_certificates_created
        )
        self.framework.observe(
            getattr(self.charm.on, "certificates_relation_joined"), self._on_certificates_joined
        )
        self.framework.observe(
            getattr(self.certificates.on, "certificate_available"), self._on_certificate_available
        )
        self.framework.observe(
            getattr(self.certificates.on, "certificate_expiring"), self._on_certificate_expiring
        )
        self.framework.observe(
            getattr(self.charm.on, "certificates_relation_broken"), self._on_certificates_broken
        )

    @property
    def cluster(self) -> Relation:
        """Relation property to be used by both the instance and charm.

        Returns:
            The peer relation instance
        """
        return self.charm.model.get_relation(PEER)

    @property
    def private_key(self) -> Optional[str]:
        """The unit private-key set during `certificates_joined`.

        Returns:
            String of key contents
            None if key not yet generated
        """
        return self.cluster.data[self.charm.unit].get("private-key", None)

    @property
    def keystore_password(self) -> Optional[str]:
        """The unit keystore password set during `certificates_joined`.

        Password is to be assigned to keystore + truststore.
        Passwords need to be the same for both stores in ZK.

        Returns:
            String of password
            None if password not yet generated
        """
        return self.cluster.data[self.charm.unit].get("keystore-password", None)

    @property
    def csr(self) -> Optional[str]:
        """The unit cert signing request.

        Returns:
            String of csr contents
            None if csr not yet generated
        """
        return self.cluster.data[self.charm.unit].get("csr", None)

    @property
    def certificate(self) -> Optional[str]:
        """The signed unit certificate from the provider relation.

        Returns:
            String of cert contents in PEM format
            None if cert not yet generated/signed
        """
        return self.cluster.data[self.charm.unit].get("certificate", None)

    @property
    def ca(self) -> Optional[str]:
        """The ca used to sign unit cert.

        Returns:
            String of ca contents in PEM format
            None if cert not yet generated/signed
        """
        return self.cluster.data[self.charm.unit].get("ca", None)

    @property
    def enabled(self) -> bool:
        """Flag to check the cluster should run with SSL quorum encryption.

        Returns:
            True if SSL quorum encryption should be active. Otherwise False
        """
        return self.cluster.data[self.charm.app].get("tls", None) == "enabled"

    @property
    def upgrading(self) -> bool:
        """Flag to check the cluster is switching between SSL <> non-SSL quorum encryption.

        Returns:
            True if the cluster is switching. Otherwise False
        """
        return bool(self.cluster.data[self.charm.app].get("upgrading", None) == "started")

    def unit_unified(self, unit: Unit) -> bool:
        """Checks if the unit is running `portUnification` configuration option.

        Pertinent during an upgrade between `ssl` <-> `non-ssl` encryption switch.


        Returns:
            True if the cluster is running `portUnification`. Otherwise False
        """
        if self.charm.cluster.relation.data[unit].get("unified"):
            return True

        return False

    @property
    def all_units_unified(self) -> bool:
        """Flag to check whether all started units are currently running with `portUnification`.

        Returns:
            True if all units are running with `portUnification`. Otherwise False
        """
        if not self.charm.cluster.all_units_related:
            return False

        for unit in self.charm.cluster.started_units:
            if not self.unit_unified(unit):
                return False

        return True

    def _on_certificates_created(self, event: RelationCreatedEvent) -> None:
        """Handler for `certificates_relation_created` event."""
        if not self.charm.unit.is_leader():
            return

        if not self.charm.cluster.stable:
            logger.debug("certificates relation created - quorum not stable - deferring")
            event.defer()
            return

        # if this event fired, we don't know whether the cluster was fully running or not
        # assume it's already running, and trigger `upgrade` from non-ssl -> ssl
        # ideally trigger this before any other `certificates_*` step
        self.cluster.data[self.charm.app].update({"tls": "enabled", "upgrading": "started"})

    def _on_certificates_joined(self, event: RelationJoinedEvent) -> None:
        """Handler for `certificates_relation_joined` event."""
        if not self.enabled:
            logger.debug(
                "certificates relation joined - tls not enabled and not upgrading - deferring"
            )
            event.defer()
            return

        # generate unit private key if not already created by action
        if not self.private_key:
            self.cluster.data[self.charm.unit].update(
                {"private-key": generate_private_key().decode("utf-8")}
            )

        # generate unit keystore password if not already created by action
        if not self.keystore_password:
            self.cluster.data[self.charm.unit].update({"keystore-password": generate_password()})

        self._request_certificate()

    def _on_certificate_available(self, event: CertificateAvailableEvent) -> None:
        """Handler for `certificates_available` event after provider updates signed certs."""
        if not self.charm.container.can_connect():
            event.defer()
            return

        # avoid setting tls files and restarting
        if event.certificate_signing_request != self.csr:
            logger.error("Can't use certificate, found unknown CSR")
            return

        # if certificate already exists, this event must be new, flag manual restart
        if self.certificate:
            self.charm.on[f"{self.charm.restart.name}"].acquire_lock.emit()

        self.cluster.data[self.charm.unit].update(
            {"certificate": event.certificate, "ca": event.ca}
        )

        self.set_server_key()
        self.set_ca()
        self.set_certificate()
        self.set_truststore()
        self.set_p12_keystore()

        self.charm.on[f"{self.charm.restart.name}"].acquire_lock.emit()

    def _on_certificates_broken(self, _) -> None:
        """Handler for `certificates_relation_broken` event."""
        self.cluster.data[self.charm.unit].update({"csr": "", "certificate": "", "ca": ""})

        # remove all existing keystores from the unit so we don't preserve certs
        self.remove_stores()

        if not self.charm.unit.is_leader():
            return

        # if this event fired, trigger `upgrade` from ssl -> non-ssl
        # ideally trigger this before any other `certificates_*` step
        self.cluster.data[self.charm.app].update({"tls": "", "upgrading": "started"})

    def _on_certificate_expiring(self, _) -> None:
        """Handler for `certificate_expiring` event."""
        if not self.private_key or not self.csr:
            logger.error("Missing unit private key and/or old csr")
            return
        new_csr = generate_csr(
            private_key=self.private_key.encode("utf-8"),
            subject=os.uname()[1],
            **self._sans,
        )

        self.certificates.request_certificate_renewal(
            old_certificate_signing_request=self.csr.encode("utf-8"),
            new_certificate_signing_request=new_csr,
        )

        self.cluster.data[self.charm.unit].update({"csr": new_csr.decode("utf-8").strip()})

    def _set_tls_private_key(self, event: ActionEvent) -> None:
        """Handler for `set_tls_private_key` action."""
        if private_key := event.params.get("internal-key", None):
            self.cluster.data[self.charm.unit].update({"private-key": private_key})
            self._request_certificate()
        else:
            event.fail("Could not set key - no internal-key found")

    def _request_certificate(self) -> None:
        """Generates and submits CSR to provider."""
        if not self.private_key:
            logger.error("Can't request certificate, missing private key")
            return

        csr = generate_csr(
            private_key=self.private_key.encode("utf-8"),
            subject=os.uname()[1],
            **self._sans,
        )
        self.cluster.data[self.charm.unit].update({"csr": csr.decode("utf-8").strip()})

        self.certificates.request_certificate_creation(certificate_signing_request=csr)

    def set_server_key(self) -> None:
        """Sets the unit private-key."""
        if not self.private_key:
            logger.error("Can't set private-key to unit, missing private-key in relation data")
            return

        push(
            container=self.container,
            content=self.private_key,
            path=f"{CONF_PATH}/server.key",
        )

    def set_ca(self) -> None:
        """Sets the unit ca."""
        if not self.ca:
            logger.error("Can't set CA to unit, missing CA in relation data")
            return

        push(
            container=self.container,
            content=self.ca,
            path=f"{CONF_PATH}/ca.pem",
        )

    def set_certificate(self) -> None:
        """Sets the unit signed certificate."""
        if not self.certificate:
            logger.error("Can't set certificate to unit, missing certificate in relation data")
            return

        push(
            container=self.container,
            content=self.certificate,
            path=f"{CONF_PATH}/server.pem",
        )

    def set_truststore(self) -> None:
        """Adds CA to JKS truststore."""
        try:
            self.container.exec(
                [
                    "keytool",
                    "-import",
                    "-v",
                    "-alias",
                    "ca",
                    "-file",
                    "ca.pem",
                    "-keystore",
                    "truststore.jks",
                    "-storepass",
                    f"{self.keystore_password}",
                    "-noprompt",
                ],
                working_dir=CONF_PATH,
            )
        except ExecError as e:
            # in case this reruns and fails
            if "already exists" in str(e.stdout):
                return
            logger.error(e.stdout)
            raise e

    def set_p12_keystore(self) -> None:
        """Creates and adds unit cert and private-key to a PCKS12 keystore."""
        try:
            self.container.exec(
                [
                    "openssl",
                    "pkcs12",
                    "-export",
                    "-in",
                    "server.pem",
                    "-inkey",
                    "server.key",
                    "-passin",
                    f"pass:{self.keystore_password}",
                    "-certfile",
                    "server.pem",
                    "-out",
                    "keystore.p12",
                    "-password",
                    f"pass:{self.keystore_password}",
                ],
                working_dir=CONF_PATH,
            )
        except ExecError as e:
            logger.error(str(e.stdout))
            raise e

    def remove_stores(self) -> None:
        """Cleans up all keys/certs/stores on a unit."""
        try:
            self.container.exec(
                [
                    "rm",
                    "-r",
                    "*.pem",
                    "*.key",
                    "*.p12",
                    "*.jks",
                ],
                working_dir=CONF_PATH,
            )
        except ExecError as e:
            logger.error(e.stdout)
            raise e

    @staticmethod
    def _parse_tls_file(raw_content: str) -> str:
        """Parse TLS files from both plain text or base64 format."""
        if re.match(r"(-+(BEGIN|END) [A-Z ]+-+)", raw_content):
            return raw_content

        return base64.b64decode(raw_content).decode("utf-8")

    @property
    def _sans(self) -> Dict[str, List[str]]:
        """Builds a SAN dict of DNS names and IPs for the unit."""
        unit_id = self.charm.unit.name.split("/")[1]

        return {
            "sans_ip": [f"{self.charm.app.name}-{unit_id}"],
            "sans_dns": [
                f"{self.charm.app.name}-{unit_id}.{self.charm.app.name}-endpoints",
                socket.getfqdn(),
            ],
        }
