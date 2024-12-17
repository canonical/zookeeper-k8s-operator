#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import dataclasses
import logging
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
import yaml
from ops.testing import Container, Context, PeerRelation, Relation, State

from charm import ZooKeeperCharm
from literals import CONTAINER, PEER, REL_NAME, SUBSTRATE

logger = logging.getLogger(__name__)

CONFIG = yaml.safe_load(Path("./config.yaml").read_text())
ACTIONS = yaml.safe_load(Path("./actions.yaml").read_text())
METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


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


def test_etc_hosts_entries_empty_if_not_all_units_related(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"ip": "aragorn", "hostname": "legolas", "fqdn": "gimli"},
        peers_data={1: {"ip": "aragorn", "hostname": "legolas", "fqdn": "gimli"}, 2: {}},
    )

    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=3)

    # With
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.config_manager.etc_hosts_entries


def test_etc_hosts_entries_empty_if_unit_not_yet_set(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_unit_data={"ip": "aragorn", "hostname": "legolas", "fqdn": "gimli"},
        peers_data={1: {"ip": "sam", "hostname": "frodo", "state": "started"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # With
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert not charm.config_manager.etc_hosts_entries

    cluster_peer = dataclasses.replace(
        cluster_peer,
        local_unit_data={
            "ip": "aragorn",
            "hostname": "legolas",
            "fqdn": "gimli",
            "state": "started",
        },
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # With
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "sam" not in "".join(charm.config_manager.etc_hosts_entries)

    cluster_peer = dataclasses.replace(
        cluster_peer,
        peers_data={1: {"ip": "sam", "hostname": "frodo", "state": "started", "fqdn": "merry"}},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer], planned_units=2)

    # With
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "sam" in "".join(charm.config_manager.etc_hosts_entries)


def test_build_static_properties_removes_necessary_rows(ctx: Context, base_state: State) -> None:
    # Given
    properties = [
        "clientPort=2181",
        "authProvider.sasl=org.apache.zookeeper.server.auth.SASLAuthenticationProvider",
        "maxClientCnxns=60",
    ]
    state_in = base_state

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)
        static = charm.config_manager.build_static_properties(properties=properties)

    # Then
    assert len(static) == 2
    assert "clientPort" not in "".join(static)


def test_server_jvmflags_has_opts(ctx: Context, base_state: State) -> None:
    # Given
    state_in = base_state

    # When
    with ctx(ctx.on.start(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "-Djava.security.auth.login.config" in "".join(charm.config_manager.server_jvmflags)


def test_jaas_users_are_added(ctx: Context, base_state: State) -> None:
    # Given
    client_relation = Relation(REL_NAME, "application", remote_app_data={"database": "app"})
    cluster_peer = PeerRelation(
        PEER, PEER, local_app_data={f"relation-{client_relation.id}": "password"}
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer, client_relation])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert len(charm.config_manager.jaas_users) == 1


def test_multiple_jaas_users_are_added(ctx: Context, base_state: State) -> None:
    # Given
    client1_relation = Relation(REL_NAME, "application", remote_app_data={"database": "app"})
    client2_relation = Relation(REL_NAME, "application2", remote_app_data={"database": "app2"})
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={
            f"relation-{client1_relation.id}": "password",
            f"relation-{client2_relation.id}": "password",
        },
    )
    state_in = dataclasses.replace(
        base_state, relations=[cluster_peer, client1_relation, client2_relation]
    )

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert len(charm.config_manager.jaas_users) == 2


def test_tls_enabled(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"tls": "enabled", "quorum": "ssl"},
        local_unit_data={"certificate": "foo"},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("managers.tls.TLSManager.get_current_sans", return_value=""),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "sslQuorum=true" in charm.config_manager.zookeeper_properties


def test_tls_disabled(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={},
        local_unit_data={},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "sslQuorum=true" not in charm.config_manager.zookeeper_properties


def test_tls_switching_encryption(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"switching-encryption": "started"},
        local_unit_data={},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "portUnification=true" in charm.config_manager.zookeeper_properties

    cluster_peer = dataclasses.replace(cluster_peer, local_app_data={"switching-encryption": ""})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "portUnification=true" not in charm.config_manager.zookeeper_properties


def test_tls_ssl_quorum(ctx: Context, base_state: State) -> None:
    # When
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"quorum": "non_ssl"},
        local_unit_data={},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "sslQuorum=true" not in charm.config_manager.zookeeper_properties

    cluster_peer = dataclasses.replace(cluster_peer, local_app_data={"quorum": "ssl"})
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "sslQuorum=true" not in charm.config_manager.zookeeper_properties

    cluster_peer = dataclasses.replace(
        cluster_peer, local_unit_data={"certificate": "keep it safe"}
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with (
        patch("managers.tls.TLSManager.get_current_sans", return_value=""),
        ctx(ctx.on.config_changed(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "sslQuorum=true" in charm.config_manager.zookeeper_properties


def test_properties_tls_uses_passwords(ctx: Context, base_state: State) -> None:
    # Given
    cluster_peer = PeerRelation(
        PEER,
        PEER,
        local_app_data={"tls": "enabled"},
        local_unit_data={"keystore-password": "mellon", "truststore-password": "friend"},
    )
    state_in = dataclasses.replace(base_state, relations=[cluster_peer])

    # When
    with ctx(ctx.on.config_changed(), state_in) as manager:
        charm = cast(ZooKeeperCharm, manager.charm)

        # Then
        assert "ssl.keyStore.password=mellon" in charm.config_manager.zookeeper_properties
        assert "ssl.trustStore.password=friend" in charm.config_manager.zookeeper_properties


def test_config_changed_updates_properties_jaas_hosts(ctx: Context, base_state: State) -> None:
    # Given
    state_in = base_state

    # When
    with (
        patch(
            "managers.config.ConfigManager.build_static_properties", return_value=["gandalf=white"]
        ),
        patch("managers.config.ConfigManager.static_properties", return_value="gandalf=grey"),
        patch("managers.config.ConfigManager.set_jaas_config"),
        patch("managers.config.ConfigManager.set_client_jaas_config"),
        patch("managers.config.ConfigManager.set_zookeeper_properties") as set_props,
        patch("managers.config.ConfigManager.set_server_jvmflags"),
        ctx(ctx.on.start(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.config_manager.config_changed()

        # Then
        set_props.assert_called_once()

    with (
        patch("managers.config.ConfigManager.jaas_config", return_value="gandalf=white"),
        patch("workload.ZKWorkload.read", return_value=["gandalf=grey"]),
        patch("managers.config.ConfigManager.set_zookeeper_properties"),
        patch("managers.config.ConfigManager.set_jaas_config") as set_jaas,
        patch("managers.config.ConfigManager.set_client_jaas_config") as set_client_jaas,
        patch("managers.config.ConfigManager.set_server_jvmflags"),
        ctx(ctx.on.start(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.config_manager.config_changed()

        # Then
        set_jaas.assert_called_once()
        set_client_jaas.assert_called_once()


def test_update_environment(ctx: Context, base_state: State) -> None:
    # Given
    example_env = [
        "",
        "KAFKA_OPTS=orcs -Djava=wargs -Dkafka=goblins",
        "SERVER_JVMFLAGS=dwarves -Djava=elves -Dzookeeper=men",
    ]
    example_new_env = {"SERVER_JVMFLAGS": "gimli -Djava=legolas -Dzookeeper=aragorn"}
    state_in = base_state

    # When
    with (
        patch("workload.ZKWorkload.read", return_value=example_env),
        patch("workload.ZKWorkload.write") as patched_write,
        ctx(ctx.on.start(), state_in) as manager,
    ):
        charm = cast(ZooKeeperCharm, manager.charm)
        charm.config_manager._update_environment(example_new_env)

        # Then
        assert all(
            updated in patched_write.call_args.kwargs["content"]
            for updated in ["gimli", "legolas", "aragorn"]
        )
        assert "KAFKA_OPTS" in patched_write.call_args.kwargs["content"]
        assert patched_write.call_args.kwargs["path"] == "/etc/environment"
