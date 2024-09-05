from unittest.mock import PropertyMock, patch

import pytest
from interface_tester.plugin import InterfaceTester
from scenario import Container, PeerRelation, State

from charm import ZooKeeperCharm
from literals import CONTAINER, PEER, SUBSTRATE, Status


@pytest.fixture()
def base_state():

    if SUBSTRATE == "k8s":
        state = State(leader=True, containers=[Container(name=CONTAINER, can_connect=True)])

    else:
        state = State(leader=True)

    return state


@pytest.fixture
def interface_tester(interface_tester: InterfaceTester, base_state: State):

    pwd = "speakfriend"
    cluster_peer = PeerRelation(PEER, PEER, local_app_data={str(i): "added" for i in range(4)})
    state = base_state.replace(relations=[cluster_peer])

    interface_tester.configure(
        charm_type=ZooKeeperCharm,
        repo="https://github.com/Batalex/charm-relation-interfaces",
        branch="feat/DPE-3737",
        state_template=state,
    )
    with (
        patch(
            "core.cluster.ClusterState.stable",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch(
            "core.cluster.ClusterState.ready",
            new_callable=PropertyMock,
            return_value=Status.ACTIVE,
        ),
        patch("workload.ZKWorkload.generate_password", return_value=pwd),
        patch(
            "managers.config.ConfigManager.current_jaas",
            new_callable=PropertyMock,
            return_value=[pwd],
        ),
        patch("managers.quorum.QuorumManager.update_acls"),
        patch(
            "charms.rolling_ops.v0.rollingops.RollingOpsManager._on_acquire_lock", autospec=True
        ),
    ):
        yield interface_tester
