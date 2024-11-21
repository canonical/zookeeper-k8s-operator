#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""Base objects for workload operations across VM + K8s charms."""
from abc import ABC, abstractmethod

from literals import PATHS


class ZKPaths:
    """Collection of expected paths for the ZooKeeper workload."""

    def __init__(self):
        self.conf_path = PATHS["CONF"]
        self.data_path = PATHS["DATA"]
        self.binaries_path = PATHS["BIN"]
        self.logs_path = PATHS["LOGS"]

    @property
    def data_dir(self) -> str:
        """The directory where ZooKeeper will store the in-memory database snapshots."""
        return f"{self.data_path}/data"

    @property
    def datalog_dir(self) -> str:
        """The directory where ZooKeeper will store the transaction log of updates to the database."""
        return f"{self.data_path}/data-log"

    @property
    def myid(self) -> str:
        """The myid filepath."""
        return f"{self.data_dir}/myid"

    @property
    def properties(self) -> str:
        """The main zoo.cfg properties filepath.

        Contains all the main configuration for the service.
        """
        return f"{self.conf_path}/zoo.cfg"

    @property
    def dynamic(self) -> str:
        """The initial zookeeper-dynamic.properties filepath.

        Initially contains a set of ZooKeeper server strings for the service to
        connect to.
        """
        return f"{self.conf_path}/zookeeper-dynamic.properties"

    @property
    def jaas(self) -> str:
        """The zookeeper-jaas.cfg filepath.

        Contains internal+external user credentials used in SASL auth.
        """
        return f"{self.conf_path}/zookeeper-jaas.cfg"

    @property
    def client_jaas(self) -> str:
        """The client-jaas.cfg filepath.

        Contains internal user credentials used in SASL auth.
        """
        return f"{self.conf_path}/client-jaas.cfg"

    @property
    def jmx_prometheus_javaagent(self) -> str:
        """The JMX exporter JAR filepath.

        Used for scraping and exposing mBeans of a JMX target.
        """
        return f"{self.binaries_path}/lib/jmx_prometheus_javaagent.jar"

    @property
    def jmx_prometheus_config(self) -> str:
        """The configuration for the JMX exporter."""
        return f"{self.conf_path}/jmx_prometheus.yaml"

    @property
    def server_key(self) -> str:
        """The private-key for the service to identify itself with for TLS auth."""
        return f"{self.conf_path}/server.key"

    @property
    def ca(self) -> str:
        """The shared cluster CA."""
        return f"{self.conf_path}/ca.pem"

    @property
    def certificate(self) -> str:
        """The certificate for the service to identify itself with for TLS auth."""
        return f"{self.conf_path}/server.pem"

    @property
    def truststore(self) -> str:
        """The Java Truststore containing trusted CAs + certificates."""
        return f"{self.conf_path}/truststore.jks"

    @property
    def keystore(self) -> str:
        """The Java Truststore containing service private-key and signed certificates."""
        return f"{self.conf_path}/keystore.p12"


class WorkloadBase(ABC):
    """Base interface for common workload operations."""

    paths = ZKPaths()

    @abstractmethod
    def start(self) -> None:
        """Starts the workload service."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stops the workload service."""
        ...

    @abstractmethod
    def restart(self) -> None:
        """Restarts the workload service."""
        ...

    @abstractmethod
    def read(self, path: str) -> list[str]:
        """Reads a file from the workload.

        Args:
            path: the full filepath to read from

        Returns:
            List of string lines from the specified path
        """
        ...

    @abstractmethod
    def write(self, content: str, path: str) -> None:
        """Writes content to a workload file.

        Args:
            content: string of content to write
            path: the full filepath to write to
        """
        ...

    @abstractmethod
    def exec(self, command: list[str], working_dir: str | None = None) -> str:
        """Runs a command on the workload substrate."""
        ...

    @property
    @abstractmethod
    def alive(self) -> bool:
        """Checks that the workload is alive."""
        ...

    @property
    @abstractmethod
    def healthy(self) -> bool:
        """Checks that the workload is healthy."""
        ...

    @abstractmethod
    def get_version(self) -> str:
        """Get the workload version.

        Returns:
            String of zookeeper version
        """
        ...
