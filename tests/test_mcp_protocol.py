"""End-to-end over the MCP protocol: in-process (as stdio clients see it) and over Streamable HTTP with API keys."""

import asyncio
import json

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from shopops import keys
from shopops.api import app
from shopops.db import fetch_all
from shopops.server import mcp

EXPECTED_TOOLS = {
    "search_customers", "get_order", "list_orders", "low_stock_products", "sales_summary",
    "search_tickets", "create_support_ticket", "update_order_status",
}


def data(result):
    assert not result.is_error, result.content
    return result.structured_content.get("result", result.structured_content) if result.structured_content else json.loads(result.content[0].text)


async def test_lists_tools_resources_and_prompts():
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == EXPECTED_TOOLS
        assert tools["get_order"].annotations.read_only_hint is True
        assert tools["update_order_status"].annotations.destructive_hint is True
        assert all(t.description for t in tools.values())
        uris = {str(r.uri) for r in (await client.list_resources()).resources}
        assert uris == {"schema://tables", "docs://refund-policy"}
        prompts = {p.name for p in (await client.list_prompts()).prompts}
        assert prompts == {"weekly_sales_report", "triage_ticket"}


async def test_two_tool_chain_low_stock_then_open_orders():
    async with Client(mcp) as client:
        low = data(await client.call_tool("low_stock_products", {"within_days": 7}))
        assert low["items"]
        orders = data(await client.call_tool("list_orders", {"status": ["pending", "paid"], "product_id": low["items"][0]["product_id"]}))
        assert orders["total"] > 0


async def test_invalid_arguments_are_rejected_by_schema():
    async with Client(mcp) as client:
        res = await client.call_tool("list_orders", {"status": ["teleported"]})
        assert res.is_error
        res = await client.call_tool("search_customers", {"query": "a", "limit": 5000})
        assert res.is_error


async def test_resources_and_prompts_render():
    async with Client(mcp) as client:
        schema = await client.read_resource("schema://tables")
        assert "order_items" in schema.contents[0].text
        policy = await client.read_resource("docs://refund-policy")
        assert "30 days" in policy.contents[0].text
        prompt = await client.get_prompt("triage_ticket", {"ticket_id": "1"})
        assert "Triage support ticket #1" in prompt.messages[0].content.text
        report = await client.get_prompt("weekly_sales_report")
        assert "sales_summary" in report.messages[0].content.text


async def test_confirmed_write_over_mcp_and_audit_trail():
    async with Client(mcp) as client:
        order_id = data(await client.call_tool("list_orders", {"status": ["paid"], "limit": 1}))["items"][0]["id"]
        preview = data(await client.call_tool("update_order_status", {"order_id": order_id, "status": "shipped"}))
        assert preview["requires_confirmation"]
        done = data(await client.call_tool("update_order_status", {"order_id": order_id, "status": "shipped", "confirm_token": preview["confirm_token"]}))
        assert done["applied"] and done["status"] == "shipped"
    rows = await fetch_all("app", "SELECT tool, principal, transport, arguments, status FROM app.tool_calls WHERE tool = 'update_order_status' ORDER BY id")
    assert rows[-1]["status"] == "ok" and rows[-1]["transport"] == "stdio"
    assert rows[-1]["arguments"]["confirm_token"] == "***"  # secrets never reach the audit log


# --------------------------------------------------------------------- Streamable HTTP + API keys
@pytest.fixture(scope="module")
async def http_app():
    # enter and exit the lifespan (an anyio task group) inside one task
    started, stop = asyncio.Event(), asyncio.Event()

    async def run():
        async with app.router.lifespan_context(app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(run())
    await started.wait()
    yield app
    stop.set()
    await task


def http_client(app, key: str | None):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://shopops.test", headers=headers, timeout=30)


async def test_http_requires_api_key(http_app):
    async with http_client(http_app, None) as c:
        r = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401 and r.json()["error"] == "invalid_api_key"
    async with http_client(http_app, "sk_shopops_wrong") as c:
        assert (await c.post("/mcp", json={})).status_code == 401


async def test_http_with_read_only_key_can_read_but_not_write(http_app):
    key, _ = await keys.create_key("ci-read", "read", "ci@example.com")
    async with http_client(http_app, key) as c, Client(streamable_http_client("http://shopops.test/mcp", http_client=c)) as client:
        order = data(await client.call_tool("get_order", {"order_id": 1042}))
        assert order["id"] == 1042
        res = await client.call_tool("create_support_ticket", {"customer_id": 1, "subject": "s", "body": "b"})
        assert res.is_error and "read-only" in res.content[0].text
    rows = await fetch_all("app", "SELECT principal, transport, status FROM app.tool_calls WHERE principal LIKE 'key:ci-read%' ORDER BY id")
    assert [r["status"] for r in rows] == ["ok", "denied"] and rows[0]["transport"] == "http"


async def test_http_revoked_key_is_rejected(http_app):
    key, row = await keys.create_key("to-revoke", "read", "ci@example.com")
    from shopops.db import execute
    await execute("app", "UPDATE app.api_keys SET revoked_at = now() WHERE id = :id", id=row["id"])
    async with http_client(http_app, key) as c:
        assert (await c.post("/mcp", json={})).status_code == 401


async def test_console_endpoints_require_google_session(http_app):
    async with http_client(http_app, None) as c:
        assert (await c.get("/api/keys")).status_code == 401
        assert (await c.get("/health")).json()["orders"] == 500
