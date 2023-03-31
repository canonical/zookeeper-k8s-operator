#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

"""Collection of global literals for the ZooKeeper K8s charm."""

PEER = "cluster"
REL_NAME = "zookeeper"
STATE = "state"
CHARM_KEY = "zookeeper-k8s"
CONTAINER = "zookeeper"
CHARM_USERS = ["super", "sync"]
CERTS_REL_NAME = "certificates"
JMX_PORT = 9998
METRICS_PROVIDER_PORT = 7000

CONF_PATH = "/etc/zookeeper"
DATA_PATH = "/var/lib/zookeeper"
LOGS_PATH = "/var/log/zookeeper"
BINARIES_PATH = "/opt/zookeeper"
