#!/usr/bin/env python3
# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest
from unittest.mock import call, patch

from charms.zookeeper.v0.client import (
    MembersSyncingError,
    QuorumLeaderNotFoundError,
    ZooKeeperClient,
    ZooKeeperManager,
)


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


class TestClient(unittest.TestCase):
    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    def test_config(self, client):
        client = ZooKeeperClient(host="", client_port=0, username="", password="")
        result, version = client.config
        servers = [server.split("=")[0] for server in result]

        self.assertEqual(version, 4294967296)
        self.assertIn("server.1", servers)
        self.assertIn("server.2", servers)

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    def test_srvr(self, client):
        client = ZooKeeperClient(host="", client_port=0, username="", password="")
        result = client.srvr

        self.assertEqual(set(result.keys()), set(["Zookeeper version", "Outstanding", "Mode"]))

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    def test_mntr(self, client):
        client = ZooKeeperClient(host="", client_port=0, username="", password="")
        result = client.mntr

        self.assertEqual(
            set(result.keys()),
            set(["zk_pending_syncs", "zk_peer_state"]),
        )

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(ready=False))
    def test_is_ready(self, client):
        client = ZooKeeperClient(host="", client_port=0, username="", password="")
        self.assertFalse(client.is_ready)


class TestManager(unittest.TestCase):
    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(follower=True))
    def test_init_raises_if_leader_not_found(self, _):
        with self.assertRaises(QuorumLeaderNotFoundError):
            ZooKeeperManager(hosts=["host"], username="", password="")

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    def test_init_finds_leader(self, _):
        zk = ZooKeeperManager(hosts=["host"], username="", password="")
        self.assertEqual(zk.leader, "host")

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(syncing=True))
    def test_members_syncing(self, _):
        zk = ZooKeeperManager(hosts=["host"], username="", password="")
        self.assertTrue(zk.members_syncing)

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(syncing=True))
    def test_add_members_raises(self, _):
        zk = ZooKeeperManager(hosts=["host"], username="", password="")
        with self.assertRaises(MembersSyncingError):
            zk.add_members(["server.1=bilbo.baggins"])

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    @patch.object(DummyClient, "reconfig")
    def test_add_members_correct_args(self, reconfig, _):
        zk = ZooKeeperManager(hosts=["server.1=bilbo.baggins"], username="", password="")
        zk.add_members(["server.2=sam.gamgee"])

        reconfig.assert_called_with(
            joining="server.2=sam.gamgee", leaving=None, new_members=None, from_config=4294967296
        )

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    @patch.object(DummyClient, "reconfig")
    def test_add_members_runs_on_leader(self, _, client):
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
        ) in calls

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient(syncing=True))
    def test_remove_members_raises(self, _):
        zk = ZooKeeperManager(hosts=["host"], username="", password="")
        with self.assertRaises(MembersSyncingError):
            zk.remove_members(["server.1=bilbo.baggins"])

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    @patch.object(DummyClient, "reconfig")
    def test_remove_members_correct_args(self, reconfig, _):
        zk = ZooKeeperManager(hosts=["server.1=bilbo.baggins"], username="", password="")
        zk.remove_members(["server.2=sam.gamgee"])

        reconfig.assert_called_with(
            joining=None, leaving="2", new_members=None, from_config=4294967296
        )

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    @patch.object(DummyClient, "reconfig")
    def test_remove_members_handles_zeroes(self, reconfig, _):
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

    @patch("charms.zookeeper.v0.client.KazooClient", return_value=DummyClient())
    @patch.object(DummyClient, "reconfig")
    def test_remove_members_runs_on_leader(self, _, client):
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
        ) in calls
