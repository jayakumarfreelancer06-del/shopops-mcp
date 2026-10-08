"""Tests run against a dedicated `shopops_test` database that is re-created and re-seeded once per session."""

import os

os.environ["DB_NAME"] = os.environ.get("TEST_DB_NAME", "shopops_test")
os.environ["BOOTSTRAP_API_KEY"] = "sk_shopops_test_bootstrap"
os.environ.setdefault("APP_JWT_SECRET", "test-secret")

import pytest  # noqa: E402

from shopops.config import get_settings  # noqa: E402
from shopops.db import dispose_all, execute  # noqa: E402
from shopops.setup_db import setup  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def database():
    get_settings.cache_clear()
    await setup(reseed=True)
    await execute("admin", "TRUNCATE app.tool_calls, app.api_keys, app.pending_confirmations RESTART IDENTITY")
    yield
    await dispose_all()
