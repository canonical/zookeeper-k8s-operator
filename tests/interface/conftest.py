import pytest
from interface_tester.plugin import InterfaceTester

from charm import ZooKeeperCharm


@pytest.fixture
def interface_tester(interface_tester: InterfaceTester):
    interface_tester.configure(charm_type=ZooKeeperCharm)
    yield interface_tester
