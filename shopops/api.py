"""HTTP entrypoint: the MCP server over Streamable HTTP at /mcp (API-key auth) + a small console API (Google SSO)."""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from shopops import keys
from shopops.console_auth import User, current_user
from shopops.console_auth import router as auth_router
from shopops.db import dispose_all, execute, fetch_all, fetch_one
from shopops.server import jsonable, mcp

RATE_LIMIT_PER_MINUTE = 120

mcp_http = mcp.streamable_http_app(streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0")


class ApiKeyAuth:
    """ASGI middleware for the MCP endpoint: `Authorization: Bearer <key>` or `X-API-Key: <key>`, plus a per-key rate limit."""

    def __init__(self, app):
        self.app = app
        self.calls: dict[str, deque] = defaultdict(deque)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        auth = headers.get("authorization", "")
        key = auth[7:].strip() if auth.lower().startswith("bearer ") else headers.get("x-api-key")
        principal = await keys.verify(key)
        if principal is None:
            return await self._reject(send, 401, "invalid_api_key", "Missing or invalid API key. Send `Authorization: Bearer <key>`.")
        window = self.calls[principal.id]
        now = time.monotonic()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= RATE_LIMIT_PER_MINUTE:
            return await self._reject(send, 429, "rate_limited", f"Rate limit of {RATE_LIMIT_PER_MINUTE} requests per minute exceeded.")
        window.append(now)
        scope.setdefault("state", {})["principal"] = principal
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, code: str, message: str):
        body = json.dumps({"error": code, "message": message}).encode()
        headers = [(b"content-type", b"application/json")]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="shopops-mcp"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with mcp.session_manager.run():
        yield
    await dispose_all()


app = FastAPI(title="ShopOps MCP", version="1.0.0", lifespan=lifespan)
app.include_router(auth_router)


@app.get("/health")
async def health() -> dict:
    try:
        orders = (await fetch_one("ro", "SELECT count(*) AS n FROM orders"))["n"]
        return {"status": "ok", "orders": orders}
    except Exception as exc:
        raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc


# ---------------------------------------------------------------- console API (Google SSO)
class NewKey(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    scope: Literal["read", "read_write"] = "read"


@app.get("/api/keys")
async def list_keys(user: User = Depends(current_user)) -> list[dict]:
    rows = await fetch_all(
        "app",
        "SELECT id, name, prefix, scope, created_at, last_used_at, revoked_at FROM app.api_keys WHERE owner_email = :e ORDER BY created_at DESC",
        e=user.email,
    )
    return jsonable(rows)


@app.post("/api/keys")
async def create_key(body: NewKey, user: User = Depends(current_user)) -> dict:
    key, row = await keys.create_key(body.name, body.scope, user.email)
    return {"key": key, **jsonable(row)}


@app.delete("/api/keys/{key_id}")
async def revoke_key(key_id: int, user: User = Depends(current_user)) -> dict:
    rows = await execute(
        "app",
        "UPDATE app.api_keys SET revoked_at = now() WHERE id = :id AND owner_email = :e AND revoked_at IS NULL RETURNING id",
        id=key_id, e=user.email,
    )
    if not rows:
        raise HTTPException(404, "Key not found or already revoked")
    return {"revoked": key_id}


@app.get("/api/audit")
async def audit_log(
    _: User = Depends(current_user),
    limit: int = Query(100, ge=1, le=500),
    tool: str | None = None,
    status: str | None = None,
) -> list[dict]:
    rows = await fetch_all(
        "app",
        """SELECT id, at, principal, transport, tool, arguments, status, duration_ms, error FROM app.tool_calls
           WHERE (CAST(:tool AS TEXT) IS NULL OR tool = :tool) AND (CAST(:status AS TEXT) IS NULL OR status = :status)
           ORDER BY at DESC LIMIT :limit""",
        tool=tool, status=status, limit=limit,
    )
    return jsonable(rows)


@app.get("/api/overview")
async def overview(_: User = Depends(current_user)) -> dict:
    stats = await fetch_one(
        "app",
        """SELECT count(*) FILTER (WHERE at > now() - interval '24 hours') AS calls_24h,
                  count(*) FILTER (WHERE at > now() - interval '24 hours' AND status <> 'ok') AS failed_24h,
                  coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) FILTER (WHERE at > now() - interval '24 hours'), 0) AS p95_ms
           FROM app.tool_calls""",
    )
    tools = [{"name": t.name, "description": (t.description or "").split("\n")[0], "read_only": bool(t.annotations and t.annotations.read_only_hint)}
             for t in await mcp.list_tools()]
    return {**jsonable(stats), "tools": tools, "resources": ["schema://tables", "docs://refund-policy"], "prompts": ["weekly_sales_report", "triage_ticket"]}


# The MCP endpoint itself. Mounted last so the routes above take precedence.
app.mount("/", ApiKeyAuth(mcp_http))
