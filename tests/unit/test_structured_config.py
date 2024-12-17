#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from typing import Iterable

import pytest
import yaml
from pydantic import ValidationError

from core.structured_config import CharmConfig

logger = logging.getLogger(__name__)
CONFIG = yaml.safe_load(Path("./config.yaml").read_text())


def to_underscore(string: str) -> str:
    """Convert dashes to underscores.

    This function is used to automatically generate aliases for our charm since the
    config.yaml file uses kebab-case.
    """
    return string.replace("-", "_")


def check_valid_values(field: str, accepted_values: Iterable) -> None:
    """Check the correctness of the passed values for a field."""
    flat_config_options = {
        to_underscore(option_name): mapping.get("default")
        for option_name, mapping in CONFIG["options"].items()
    }
    for value in accepted_values:
        CharmConfig(**{**flat_config_options, **{field: value}})


def check_invalid_values(field: str, erroneus_values: Iterable) -> None:
    """Check the incorrectness of the passed values for a field."""
    flat_config_options = {
        to_underscore(option_name): mapping.get("default")
        for option_name, mapping in CONFIG["options"].items()
    }
    for value in erroneus_values:
        with pytest.raises(ValidationError) as excinfo:
            CharmConfig(**{**flat_config_options, **{field: value}})

        assert field in excinfo.value.errors()[0]["loc"]


def test_values_gt_zero() -> None:
    """Check fields greater than zero."""
    gt_zero_fields = ["init_limit", "sync_limit", "tick_time"]
    erroneus_values = [0, -2147483649, -34]
    valid_values = [42, 1000, 1, 9223372036854775807]
    for field in gt_zero_fields:
        check_invalid_values(field, erroneus_values)
        check_valid_values(field, valid_values)


def test_incorrect_log_level():
    """Accepted log-level values must be part of the defined enumeration and uppercase."""
    erroneus_values = ["", "something_else", "warning", "DEBUG,INFO"]
    valid_values = ["INFO", "WARNING", "DEBUG", "ERROR"]
    check_invalid_values("log_level", erroneus_values)
    check_valid_values("log_level", valid_values)


def test_incorrect_expose_external():
    """Accepted expose-external values must be part of the defined enumeration and uppercase."""
    erroneus_values = ["", "something_else", "false,nodeport", "load_balancer"]
    valid_values = ["false", "nodeport", "loadbalancer"]
    check_invalid_values("expose_external", erroneus_values)
    check_valid_values("expose_external", valid_values)
