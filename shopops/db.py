"""Async engines, one per database role. Every query goes through SQLAlchemy `text()` with bound parameters."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shopops.config import get_settings

Role = Literal["ro", "rw", "app", "admin"]


@lru_cache
def engine(role: Role) -> AsyncEngine:
    return create_async_engine(get_settings().url(role), pool_size=5, max_overflow=5, pool_pre_ping=True)


async def fetch_all(role: Role, sql: str, **params: Any) -> list[dict]:
    async with engine(role).connect() as conn:
        result = await conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


async def fetch_one(role: Role, sql: str, **params: Any) -> dict | None:
    rows = await fetch_all(role, sql, **params)
    return rows[0] if rows else None


async def execute(role: Role, sql: str, **params: Any) -> list[dict]:
    """Run a write statement in its own transaction; returns RETURNING rows if any."""
    async with engine(role).begin() as conn:
        result = await conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result] if result.returns_rows else []


async def dispose_all() -> None:
    for role in ("ro", "rw", "app", "admin"):
        if engine.cache_info().currsize:
            await engine(role).dispose()
    engine.cache_clear()
