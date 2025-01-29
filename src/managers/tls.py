#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for building necessary files for Java TLS auth."""
import logging
import socket
import subprocess

import ops.pebble
from lightkube.core.exceptions import ApiError as LightKubeApiError
from tenacity import retry, retry_if_exception_cause_type, stop_after_attempt, wait_fixed

from core.cluster import SUBSTRATES, ClusterState
from core.stubs import SANs
from core.workload import WorkloadBase
from literals import GROUP, USER

logger = logging.getLogger(__name__)


class TLSManager:
    """Manager for building necessary files for Java TLS auth."""

    def __init__(self, state: ClusterState, workload: WorkloadBase, substrate: SUBSTRATES):
        self.state = state
        self.workload = workload
        self.substrate = substrate

    @retry(
        wait=wait_fixed(5),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_cause_type(LightKubeApiError),
        reraise=True,
    )
    def build_sans(self) -> SANs:
        """Builds a SAN structure of DNS names and IPs for the unit."""
        if self.substrate == "vm":
            return SANs(
                sans_ip=[self.state.unit_server.internal_address],
                sans_dns=[self.state.unit_server.unit.name, socket.getfqdn()],
            )
        else:
            sans_ip = [str(self.state.bind_address)]

            if node_ip := self.state.unit_server.node_ip:
                sans_ip.append(node_ip)

            try:
                sans_ip.append(self.state.unit_server.loadbalancer_ip)
            except Exception:
                pass

            return SANs(
                sans_ip=sorted(sans_ip),
                sans_dns=sorted(
                    [
                        self.state.unit_server.internal_address.split(".")[0],
                        self.state.unit_server.internal_address,
                        socket.getfqdn(),
                    ]
                ),
            )

    def get_current_sans(self) -> SANs | None:
        """Gets the current SANs for the unit cert."""
        if not self.state.unit_server.certificate:
            return

        command = ["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", "server.pem"]

        try:
            sans_lines = self.workload.exec(
                command=command, working_dir=self.workload.paths.conf_path
            ).splitlines()
        except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:
            logger.error(e.stdout)
            raise e

        for line in sans_lines:
            if "DNS" in line and "IP" in line:
                break

        sans_ip = []
        sans_dns = []
        for item in line.split(", "):
            san_type, san_value = item.split(":")

            if san_type.strip() == "DNS":
                sans_dns.append(san_value)
            if san_type.strip() == "IP Address":
                sans_ip.append(san_value)

        return SANs(sans_ip=sorted(sans_ip), sans_dns=sorted(sans_dns))

    def set_private_key(self) -> None:
        """Sets the unit private-key."""
        if not self.state.unit_server.private_key:
            logger.error("Can't set private-key to unit, missing private-key in relation data")
            return

        self.workload.write(
            content=self.state.unit_server.private_key, path=self.workload.paths.server_key
        )

    def set_ca(self) -> None:
        """Sets the unit CA."""
        if not self.state.unit_server.ca_cert:
            logger.error("Can't set CA to unit, missing CA in relation data")
            return

        self.workload.write(content=self.state.unit_server.ca_cert, path=self.workload.paths.ca)

    def set_certificate(self) -> None:
        """Sets the unit certificate."""
        if not self.state.unit_server.certificate:
            logger.error("Can't set certificate to unit, missing certificate in relation data")
            return

        self.workload.write(
            content=self.state.unit_server.certificate, path=self.workload.paths.certificate
        )

    def set_chain(self) -> None:
        """Sets the unit chain."""
        if not self.state.unit_server.chain:
            logger.error("Can't set chain to unit, missing chain in relation data")
            return

        for i, chain_cert in enumerate(self.state.unit_server.chain):
            self.workload.write(
                content=chain_cert, path=f"{self.workload.paths.conf_path}/chain{i}.pem"
            )

    def set_truststore(self) -> None:
        """Creates the unit Java Truststore and adds the unit CA."""
        trust_aliases = [f"chain{i}" for i in range(len(self.state.unit_server.chain))] + ["ca"]
        for alias in trust_aliases:

            try:
                self._import_to_truststore(alias=alias)

                if self.substrate == "vm":
                    self.workload.exec(
                        command=["chown", f"{USER}:{GROUP}", self.workload.paths.truststore],
                    )
                    self.workload.exec(command=["chmod", "770", self.workload.paths.truststore])
            except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:
                if "already exists" in str(e.stdout):
                    if (
                        alias != "ca"
                    ):  # we only care about the subsequent handling for the rootCA support
                        logger.warning(str(e.stdout))
                        continue

                    # Replacement strategy:
                    # - We need to own the file, otherwise keytool throws a permission error upon removing an entry
                    # - We need to make sure that the keystore is not empty at any point, hence the three steps.
                    #  Otherwise, ZK would pick up the file change when it's empty, and crash its internal watcher thread
                    try:
                        if self.substrate == "vm":
                            self.workload.exec(
                                command=[
                                    "chown",
                                    f"{GROUP}:{GROUP}",
                                    self.workload.paths.truststore,
                                ],
                            )
                        self._rename_ca_in_truststore()
                        self._delete_ca_in_truststore()
                        self._import_to_truststore()
                        if self.substrate == "vm":
                            self.workload.exec(
                                command=[
                                    "chown",
                                    f"{USER}:{GROUP}",
                                    self.workload.paths.truststore,
                                ],
                            )
                    except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:

                        logger.error(str(e.stdout))
                        raise e

                    return

                logger.error(str(e.stdout))
                raise e

    def _import_to_truststore(self, alias: str = "ca") -> None:
        keytool_cmd = "charmed-zookeeper.keytool" if self.substrate == "vm" else "keytool"
        self.workload.exec(
            command=[
                keytool_cmd,
                "-import",
                "-v",
                "-alias",
                alias,
                "-file",
                f"{self.workload.paths.conf_path}/{alias}.pem",
                "-keystore",
                self.workload.paths.truststore,
                "-storepass",
                self.state.unit_server.truststore_password,
                "-noprompt",
            ],
        )

    def _rename_ca_in_truststore(self, from_alias: str = "ca", to_alias: str = "old-ca") -> None:
        keytool_cmd = "charmed-zookeeper.keytool" if self.substrate == "vm" else "keytool"
        self.workload.exec(
            command=[
                keytool_cmd,
                "-changealias",
                "-alias",
                from_alias,
                "-destalias",
                to_alias,
                "-keystore",
                self.workload.paths.truststore,
                "-storepass",
                self.state.unit_server.truststore_password,
            ],
        )

    def _delete_ca_in_truststore(self, alias: str = "old-ca") -> None:
        keytool_cmd = "charmed-zookeeper.keytool" if self.substrate == "vm" else "keytool"
        self.workload.exec(
            command=[
                keytool_cmd,
                "-delete",
                "-v",
                "-alias",
                alias,
                "-keystore",
                self.workload.paths.truststore,
                "-storepass",
                self.state.unit_server.truststore_password,
            ],
        )

    def set_p12_keystore(self) -> None:
        """Creates the unit Java Keystore and adds unit certificate + private-key."""
        try:
            self.workload.exec(
                command=[
                    "openssl",
                    "pkcs12",
                    "-export",
                    "-in",
                    self.workload.paths.certificate,
                    "-inkey",
                    self.workload.paths.server_key,
                    "-passin",
                    f"pass:{self.state.unit_server.keystore_password}",
                    "-certfile",
                    self.workload.paths.certificate,
                    "-out",
                    self.workload.paths.keystore,
                    "-password",
                    f"pass:{self.state.unit_server.keystore_password}",
                ],
            )
            if self.substrate == "vm":
                self.workload.exec(
                    command=["chown", f"{USER}:{GROUP}", self.workload.paths.keystore],
                )

        except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:
            logger.error(str(e.stdout))
            raise e

    def remove_stores(self) -> None:
        """Removes all certs, keys, stores from the unit."""
        try:
            self.workload.exec(
                command=[
                    "rm",
                    "-rf",
                    self.workload.paths.ca,
                    self.workload.paths.certificate,
                    self.workload.paths.keystore,
                    self.workload.paths.truststore,
                    "*.pem",
                    "*.key",
                ],
                working_dir=self.workload.paths.conf_path,
            )
        except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:
            logger.error(str(e.stdout))
            raise e
