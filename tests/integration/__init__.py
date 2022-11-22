#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path

import yaml

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
APP_NAME = METADATA["name"]
ZOOKEEPER_IMAGE = METADATA["resources"]["zookeeper-image"]["upstream-source"]
SERIES = "focal"
