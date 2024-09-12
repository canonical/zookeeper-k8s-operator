#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Types module."""
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
