#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
from unittest.mock import call, patch

import pytest
import yaml
from charms.zookeeper.v0.client import (
    MembersSyncingError,
    QuorumLeaderNotFoundError,
    ZooKeeperClient,
    ZooKeeperManager,
)
from kazoo.client import logging

logger = logging.getLogger(__name__)


class DummyClient:
    def __init__(self, follower=False, syncing=False, ready=True):
        self.follower = follower
        self.syncing = syncing
        self.ready = ready
        self.SRVR = "Zookeeper version: version\nOutstanding: 0\nMode: leader"
        self.MNTR = "zk_pending_syncs	0\nzk_peer_state	leading - broadcast"

        if self.follower:
            self.SRVR = self.SRVR.replace("leader", "follower")
        if not self.ready:
            self.MNTR = self.MNTR.replace("broadcast", "other")
        if self.syncing:
            self.MNTR = self.MNTR.replace("0", "1")

    def get(self, _):
        return (b"server.1=bilbo.baggins\nserver.2=sam.gamgee\nnversion=100000000", b"ZnodeStat()")

    def command(self, *args):
        if args[0] == b"srvr":
            return self.SRVR

        if args[0] == b"mntr":
            return self.MNTR

    def reconfig(self):
        pass

    def connected(self):
        return True

    def start(self):
        pass

    def stop(self):
        pass


CONFIG = str(yaml.safe_load(Path("./config.yaml").read_text()))
ACTIONS = str(yaml.safe_load(Path("./actions.yaml").read_text()))
METADATA = str(yaml.safe_load(Path("./metadata.yaml").read_text()))


def test_config():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()):
        client = ZooKeeperClient(host="", client_port=0, username="", password="")
        result, version = client.config
        servers = [server.split("=")[0] for server in result]

        assert version == 4294967296
        assert "server.1" in servers
        assert "server.2" in servers


def test_srvr():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()):
        client = ZooKeeperClient(host="", client_port=0, username="", password="")
        result = client.srvr

        assert set(result.keys()) == set(["Zookeeper version", "Outstanding", "Mode"])


def test_mntr():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()):
        client = ZooKeeperClient(host="", client_port=0, username="", password="")
        result = client.mntr

        assert set(result.keys()) == set(["zk_pending_syncs", "zk_peer_state"])


def test_is_ready():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(ready=False)):
        client = ZooKeeperClient(host="", client_port=0, username="", password="")
        assert not client.is_ready


def test_init_raises_if_leader_not_found():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(follower=True)):
        with pytest.raises(QuorumLeaderNotFoundError):
            ZooKeeperManager(hosts=["host"], username="", password="")


def test_init_finds_leader():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()):
        zk = ZooKeeperManager(hosts=["host"], username="", password="")
        assert zk.leader == "host"


def test_members_syncing():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(syncing=True)):
        zk = ZooKeeperManager(hosts=["host"], username="", password="")
        assert zk.members_syncing


def test_add_members_raises():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(syncing=True)):
        zk = ZooKeeperManager(hosts=["host"], username="", password="")
        with pytest.raises(MembersSyncingError):
            zk.add_members(["server.1=bilbo.baggins"])


@patch.object(DummyClient, "reconfig")
def test_add_members_correct_args(reconfig):
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()):
        zk = ZooKeeperManager(hosts=["server.1=bilbo.baggins"], username="", password="")
        zk.add_members(["server.2=sam.gamgee"])

        reconfig.assert_called_with(
            joining="server.2=sam.gamgee", leaving=None, new_members=None, from_config=4294967296
        )


@patch.object(DummyClient, "reconfig")
def test_add_members_runs_on_leader(_):
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()) as client:
        zk = ZooKeeperManager(hosts=["server.1=bilbo.baggins"], username="", password="")
        zk.leader = "leader"
        zk.add_members(["server.2=sam.gamgee"])

        calls = client.call_args_list
        assert (
            call(
                hosts="leader:2181",
                timeout=1.0,
                sasl_options={"mechanism": "DIGEST-MD5", "username": "", "password": ""},
                keyfile="",
                keyfile_password="",
                certfile="",
                verify_certs=False,
                use_ssl=False,
            )
            in calls
        )


def test_remove_members_raises():
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(syncing=True)):
        zk = ZooKeeperManager(hosts=["host"], username="", password="")
        with pytest.raises(MembersSyncingError):
            zk.remove_members(["server.1=bilbo.baggins"])


@patch.object(DummyClient, "reconfig")
def test_remove_members_correct_args(reconfig):
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()):
        zk = ZooKeeperManager(hosts=["server.1=bilbo.baggins"], username="", password="")
        zk.remove_members(["server.2=sam.gamgee"])

        reconfig.assert_called_with(
            joining=None, leaving="2", new_members=None, from_config=4294967296
        )


@patch.object(DummyClient, "reconfig")
def test_remove_members_handles_zeroes(reconfig):
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()):
        zk = ZooKeeperManager(
            hosts=[
                "server.1=bilbo.baggins",
                "server.10=pippin.took",
                "server.300=merry.brandybuck",
            ],
            username="",
            password="",
        )
        zk.remove_members(["server.2=sam.gamgee"])
        reconfig.assert_called_with(
            joining=None, leaving="2", new_members=None, from_config=4294967296
        )
        zk.remove_members(["server.300=merry.brandybuck"])
        reconfig.assert_called_with(
            joining=None, leaving="300", new_members=None, from_config=4294967296
        )


@patch.object(DummyClient, "reconfig")
def test_remove_members_runs_on_leader(_):
    with patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient()) as client:
        zk = ZooKeeperManager(hosts=["server.1=bilbo.baggins"], username="", password="")
        zk.leader = "leader"
        zk.remove_members(["server.2=sam.gamgee"])

        calls = client.call_args_list
        assert (
            call(
                hosts="leader:2181",
                timeout=1.0,
                sasl_options={"mechanism": "DIGEST-MD5", "username": "", "password": ""},
                keyfile="",
                keyfile_password="",
                certfile="",
                verify_certs=False,
                use_ssl=False,
            )
            in calls
        )
