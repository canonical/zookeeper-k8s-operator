#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the ZooKeeper K8s charm."""

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
ELECTION_PORT = 3888
JMX_PORT = 9998
METRICS_PROVIDER_PORT = 7000

DEPENDENCIES = {
    "service": {
        "dependencies": {},
        "name": "zookeeper",
        "upgrade_supported": "^3.6",
        "version": "3.8.2",
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
