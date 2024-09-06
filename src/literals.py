#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the ZooKeeper charm."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase, WaitingStatus

SUBSTRATE = "k8s"
CHARM_KEY = "zookeeper-k8s"

PEER = "cluster"
REL_NAME = "zookeeper"
CONTAINER = "zookeeper"
CHARM_USERS = ["super", "sync"]
CERTS_REL_NAME = "certificates"
CLIENT_PORT = 2181
SECURE_CLIENT_PORT = 2182
SERVER_PORT = 2888
ADMIN_SERVER_PORT = 8080
ELECTION_PORT = 3888
JMX_PORT = 9998
METRICS_PROVIDER_PORT = 7000

# Unused on k8s, here for compatibility reasons
# '584788' refers to snap_daemon, which do not exists on the storage-attached hook prior to the
# snap install.
# FIXME (24.04): From snapd 2.61 onwards, snap_daemon is being deprecated and replaced with _daemon_,
# which now possesses a UID of 584792.
# See https://snapcraft.io/docs/system-usernames.
USER = 584788
GROUP = "root"

S3_REL_NAME = "s3-credentials"
S3_BACKUPS_PATH = "zookeeper_backups"

DEPENDENCIES = {
    "service": {
        "dependencies": {},
        "name": "zookeeper",
        "upgrade_supported": "^3.6",
        "version": "3.8.4",
    },
}

PATHS = {
    "CONF": "/etc/zookeeper",
    "DATA": "/var/lib/zookeeper",
    "LOGS": "/var/log/zookeeper",
    "BIN": "/opt/zookeeper",
}

METRICS_RULES_DIR = "./src/alert_rules/prometheus"
LOGS_RULES_DIR = "./src/alert_rules/loki"

# --- TYPES ---

DebugLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


@dataclass
class StatusLevel:
    status: StatusBase
    log_level: DebugLevel


class Status(Enum):
    ACTIVE = StatusLevel(ActiveStatus(), "DEBUG")
    NO_PEER_RELATION = StatusLevel(MaintenanceStatus("no peer relation yet"), "DEBUG")
    SERVICE_NOT_INSTALLED = StatusLevel(
        BlockedStatus("unable to install zookeeper service"), "ERROR"
    )
    SERVICE_NOT_RUNNING = StatusLevel(BlockedStatus("zookeeper service not running"), "ERROR")
    CONTAINER_NOT_CONNECTED = StatusLevel(
        MaintenanceStatus("zookeeper container not ready"), "DEBUG"
    )
    NO_PASSWORDS = StatusLevel(
        WaitingStatus("waiting for leader to create internal user credentials"), "DEBUG"
    )
    NOT_UNIT_TURN = StatusLevel(WaitingStatus("other units starting first"), "DEBUG")
    NOT_ALL_IP = StatusLevel(MaintenanceStatus("not all units registered IP"), "DEBUG")
    NO_CERT = StatusLevel(WaitingStatus("unit waiting for signed certificates"), "INFO")
    NOT_ALL_RELATED = StatusLevel(
        MaintenanceStatus("cluster not stable - not all units related"), "DEBUG"
    )
    STALE_QUORUM = StatusLevel(MaintenanceStatus("cluster not stable - quorum is stale"), "DEBUG")
    NOT_ALL_ADDED = StatusLevel(
        MaintenanceStatus("cluster not stable - not all units added to quorum"), "DEBUG"
    )
    NOT_ALL_QUORUM = StatusLevel(
        MaintenanceStatus("provider not ready - not all units using same encryption"), "DEBUG"
    )
    SWITCHING_ENCRYPTION = StatusLevel(
        MaintenanceStatus("provider not ready - switching quorum encryption"), "DEBUG"
    )
    ALL_UNIFIED = StatusLevel(
        MaintenanceStatus("provider not ready - portUnification not yet disabled"), "DEBUG"
    )
    SERVICE_UNHEALTHY = StatusLevel(
        BlockedStatus("zookeeper service is unreachable or not serving requests"), "ERROR"
    )
    MISSING_S3_CONFIG = StatusLevel(
        BlockedStatus("invalid s3 configuration - missing mandatory parameters"), "ERROR"
    )
    BUCKET_NOT_CREATED = StatusLevel(BlockedStatus("cannot create s3 bucket"), "ERROR")


SECRETS_APP = ["sync-password", "super-password"]
SECRETS_UNIT = [
    "ca-cert",
    "csr",
    "certificate",
    "truststore-password",
    "keystore-password",
    "private-key",
]
