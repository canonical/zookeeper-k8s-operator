#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for building necessary files for Java TLS auth."""
import logging
import socket
import subprocess
from typing import TypedDict

import ops.pebble
from lightkube.core.exceptions import ApiError as LightKubeApiError
from tenacity import retry, retry_if_exception_cause_type, stop_after_attempt, wait_fixed

from core.cluster import SUBSTRATES, ClusterState
from core.workload import WorkloadBase
from literals import GROUP, USER

logger = logging.getLogger(__name__)

Sans = TypedDict("Sans", {"sans_ip": list[str], "sans_dns": list[str]})


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
    def build_sans(self) -> Sans:
        """Builds a SAN dict of DNS names and IPs for the unit."""
        if self.substrate == "vm":
            return {
                "sans_ip": [
                    self.state.unit_server.host,
                ],
                "sans_dns": [self.state.unit_server.unit.name, socket.getfqdn()],
            }
        else:
            sans_ip = [str(self.state.bind_address)]
            if node_ip := self.state.unit_server.node_ip:
                sans_ip.append(node_ip)

            try:
                if lb_ip := self.state.unit_server.loadbalancer_ip:
                    sans_ip.append(lb_ip)
            except Exception:
                pass

            sans: Sans = {
                "sans_ip": sorted(sans_ip),
                "sans_dns": sorted(
                    [
                        self.state.unit_server.internal_address.split(".")[0],
                        self.state.unit_server.internal_address,
                        socket.getfqdn(),
                    ]
                ),
            }

            return sans

    def get_current_sans(self) -> Sans | None:
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

        return {"sans_ip": sorted(sans_ip), "sans_dns": sorted(sans_dns)}

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

    def set_truststore(self) -> None:
        """Creates the unit Java Truststore and adds the unit CA."""
        try:
            self._import_ca_in_truststore()

            if self.substrate == "vm":
                self.workload.exec(
                    command=["chown", f"{USER}:{GROUP}", self.workload.paths.truststore],
                )
        except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:
            if "already exists" in str(e.stdout):
                # Replacement strategy:
                # - We need to own the file, otherwise keytool throws a permission error upon removing an entry
                # - We need to make sure that the keystore is not empty at any point, hence the three steps.
                #  Otherwise, ZK would pick up the file change when it's empty, and crash its internal watcher thread
                try:
                    if self.substrate == "vm":
                        self.workload.exec(
                            command=["chown", f"{GROUP}:{GROUP}", self.workload.paths.truststore],
                        )
                    self._rename_ca_in_truststore()
                    self._delete_ca_in_truststore()
                    self._import_ca_in_truststore()
                    if self.substrate == "vm":
                        self.workload.exec(
                            command=["chown", f"{USER}:{GROUP}", self.workload.paths.truststore],
                        )
                except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:

                    logger.error(str(e.stdout))
                    raise e

                return

            logger.error(str(e.stdout))
            raise e

    def _import_ca_in_truststore(self, alias: str = "ca") -> None:
        keytool_cmd = "charmed-zookeeper.keytool" if self.substrate == "vm" else "keytool"
        self.workload.exec(
            command=[
                keytool_cmd,
                "-import",
                "-v",
                "-alias",
                alias,
                "-file",
                self.workload.paths.ca,
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
                ],
                working_dir=self.workload.paths.conf_path,
            )
        except (subprocess.CalledProcessError, ops.pebble.ExecError) as e:
            logger.error(str(e.stdout))
            raise e
