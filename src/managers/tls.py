#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for building necessary files for Java TLS auth."""
import logging
import subprocess

import ops.pebble

from core.cluster import SUBSTRATES, ClusterState
from core.workload import WorkloadBase
from literals import GROUP, USER

logger = logging.getLogger(__name__)


class TLSManager:
    """Manager for building necessary files for Java TLS auth."""

    def __init__(self, state: ClusterState, workload: WorkloadBase, substrate: SUBSTRATES):
        self.state = state
        self.workload = workload
        self.substrate = substrate

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
