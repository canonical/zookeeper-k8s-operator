#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Types module."""
from __future__ import annotations

from enum import Enum
from typing import TypedDict

S3ConnectionInfo = TypedDict(
    "S3ConnectionInfo",
    {
        "access-key": str,
        "secret-key": str,
        "bucket": str,
        "path": str,
        "endpoint": str,
        "region": str,
    },
)


BackupMetadata = TypedDict("BackupMetadata", {"id": str, "log-sequence-number": int, "path": str})


class RestoreStep(str, Enum):
    """Represent restore flow step."""

    NOT_STARTED = ""
    STOP_WORKFLOW = "stop"
    RESTORE = "restore"
    RESTART = "restart"
    CLEAN = "clean"

    def next_step(self) -> RestoreStep:
        """Get the next logical restore flow step."""
        match self:
            case RestoreStep.NOT_STARTED:
                return RestoreStep.STOP_WORKFLOW
            case RestoreStep.STOP_WORKFLOW:
                return RestoreStep.RESTORE
            case RestoreStep.RESTORE:
                return RestoreStep.RESTART
            case RestoreStep.RESTART:
                return RestoreStep.CLEAN
            case RestoreStep.CLEAN:
                return RestoreStep.NOT_STARTED
