#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the ZooKeeper charm."""
import logging
from enum import Enum

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import Field

logger = logging.getLogger(__name__)


class LogLevel(str, Enum):
    """Enum for the `log-level` field."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    DEBUG = "DEBUG"


class ExposeExternal(str, Enum):
    """Enum for the `expose-external` field."""

    FALSE = "false"
    NODEPORT = "nodeport"
    # TODO(lb): Uncomment
    # LOADBALANCER = "loadbalancer"


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    init_limit: int = Field(gt=0)
    sync_limit: int = Field(gt=0)
    tick_time: int = Field(gt=0)
    log_level: LogLevel
    expose_external: ExposeExternal
