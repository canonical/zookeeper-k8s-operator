#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
import yaml
from ops.pebble import ExecError
from ops.testing import Container, Context, State

from charm import ZooKeeperCharm
from literals import CONTAINER, SUBSTRATE

logger = logging.getLogger(__name__)

CONFIG = yaml.safe_load(Path("./config.yaml").read_text())
ACTIONS = yaml.safe_load(Path("./actions.yaml").read_text())
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


# override conftest fixture
@pytest.fixture(autouse=False)
def patched_healthy():
    yield


@pytest.fixture()
def base_state():

    if SUBSTRATE == "k8s":
        state = State(leader=True, containers=[Container(name=CONTAINER, can_connect=True)])

    else:
        state = State(leader=True)

    return state


@pytest.fixture()
def ctx() -> Context:
    ctx = Context(ZooKeeperCharm, meta=METADATA, config=CONFIG, actions=ACTIONS, unit_id=0)
    return ctx


def test_healthy_does_not_raise(ctx: Context, base_state: State, patched_healthy) -> None:
    # Given
    state_in = base_state

    # When
    with (
        patch("ops.model.Container.get_service"),
        patch("ops.pebble.ServiceInfo.is_running"),
        patch(
            "workload.ZKWorkload.exec", side_effect=ExecError(["It's"], 1, "dangerous", "business")
        ),
        ctx(ctx.on.start(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        assert not charm.workload.healthy
