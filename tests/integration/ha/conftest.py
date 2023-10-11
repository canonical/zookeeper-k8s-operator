from collections.abc import AsyncGenerator

import helpers
import pytest
from pytest_operator.plugin import OpsTest


@pytest.fixture()
async def chaos_mesh(ops_test: OpsTest) -> AsyncGenerator:
    """Deploys chaos mesh to the namespace and uninstalls it at the end."""
    helpers.deploy_chaos_mesh(ops_test.model.info.name)

    yield

    helpers.remove_instance_isolation(ops_test)
    helpers.destroy_chaos_mesh(ops_test.model.info.name)
