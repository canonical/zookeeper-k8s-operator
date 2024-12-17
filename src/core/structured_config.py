#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Structured configuration for the ZooKeeper charm."""
import logging

from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import Field

from core.stubs import ExposeExternal, LogLevel

logger = logging.getLogger(__name__)


class CharmConfig(BaseConfigModel):
    """Manager for the structured configuration."""

    init_limit: int = Field(gt=0)
    sync_limit: int = Field(gt=0)
    tick_time: int = Field(gt=0)
    log_level: LogLevel
    expose_external: ExposeExternal
