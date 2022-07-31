#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling Kafka configuration."""

import logging

from ops.charm import CharmBase

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_OPTIONS = """
clientPort=2181
maxClientCnxns=60
minSessionTimeout=4000
maxSessionTimeout=40000
autopurge.snapRetainCount=3
autopurge.purgeInterval=0
reconfigEnabled=true
standaloneEnabled=false
4lw.commands.whitelist=*
DigestAuthenticationProvider.digestAlg=SHA3-256
quorum.auth.enableSasl=true
quorum.auth.learnerRequireSasl=true
quorum.auth.serverRequireSasl=true
authProvider.sasl=org.apache.zookeeper.server.auth.SASLAuthenticationProvider
audit.enable=true
"""


class ZooKeeperConfig:
    """Manager for handling ZooKeeper configuration."""

    def __init__(self, charm: CharmBase):
        self.charm = charm
        self.container = self.charm.unit.get_container("zookeeper")
        self.default_config_path = f"{self.charm.config['data-dir']}/config"
        self.properties_filepath = f"{self.default_config_path}/zookeeper.properties"
        self.dynamic_filepath = f"{self.default_config_path}/zookeeper-dynamic.properties"
        self.jaas_filepath = f"{self.default_config_path}/zookeeper-jaas.cfg"

    def push(self, content: str, path: str) -> None:
        """Simple wrapper for writing a file and contents to a container.

        Args:
            content: the text content to write to a file path
            path: the full path of the desired file
        """
        self.container.push(path, content, make_dirs=True)

    def set_zookeeper_properties(self) -> None:
        """Sets all zookeeper config properties to the zookeeper.properties path."""
        zookeeper_properties = (
            DEFAULT_CONFIG_OPTIONS,
            f"dynamicConfigFile={self.dynamic_filepath}",
            f"dataDir={self.charm.config['data-dir']}",
            f"dataLogDir={self.charm.config['log-dir']}",
            f"tickTime={self.charm.config['tick-time']}",
            f"initLimit={self.charm.config['init-limit']}",
            f"syncLimit={self.charm.config['sync-limit']}",
        )

        self.push(
            content="\n".join(zookeeper_properties),
            path=self.properties_filepath,
        )

    def set_zookeeper_dynamic_properties(self, servers: str) -> None:
        """Sets the zookeeper-dynamic.properties file to the container.

        Args:
            servers: the ZK server strings to write to the file
        """
        self.push(content=servers, path=self.dynamic_filepath)

    def set_jaas_config(self, sync_password: str, super_password: str, users: str) -> None:
        """Sets the Kafka JAAS config using zookeeper relation data."""
        jaas_config = f"""
            QuorumServer {{
                org.apache.zookeeper.server.auth.DigestLoginModule required
                user_sync="{sync_password}";
            }};

            QuorumLearner {{
                org.apache.zookeeper.server.auth.DigestLoginModule required
                username="sync"
                password="{sync_password}";
            }};

            Server {{
                org.apache.zookeeper.server.auth.DigestLoginModule required
                {users}
                user_super="{super_password}";
            }};
        """
        self.push(content=jaas_config, path=self.jaas_filepath)

    def set_zookeeper_myid(self) -> None:
        """Sets the ZooKeeper myid file to the data directory."""
        myid = str(int(self.charm.unit.name.split("/")[1]) + 1)
        path = f"{self.charm.config['data-dir']}/myid"

        self.push(content=myid, path=path)

    @property
    def extra_args(self) -> str:
        """Collection of Java config arguments for SASL auth.

        Returns:
            String of command argument to be prepended to start-up command
        """
        opts = (
            "-Dzookeeper.requireClientAuthScheme=sasl",
            "-Dzookeeper.superUser=super",
            f"-Djava.security.auth.login.config={self.default_config_path}/zookeeper-jaas.cfg",
        )

        return " ".join(opts)

    @property
    def zookeeper_command(self) -> str:
        """The run command for starting the ZooKeeper service.

        Returns:
            String of startup command and expected config filepath
        """
        entrypoint = "/opt/kafka/bin/zookeeper-server-start.sh"
        return f"{entrypoint} {self.properties_filepath}"
