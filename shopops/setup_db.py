"""Create the database, the three least-privilege roles and the schema, then seed demo data.

    python -m shopops.setup_db            # idempotent: seeds only if the database is empty
    python -m shopops.setup_db --reseed   # wipe business data and seed again
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import asyncpg

from shopops.config import Settings, get_settings
from shopops.seed import seed

SCHEMA = Path(__file__).with_name("schema.sql")


async def _admin(s: Settings, database: str) -> asyncpg.Connection:
    return await asyncpg.connect(host=s.db_host, port=s.db_port, user=s.postgres_user, password=s.postgres_password, database=database)


async def ensure_database(s: Settings) -> None:
    conn = await _admin(s, "postgres")
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", s.db_name):
            await conn.execute(f'CREATE DATABASE "{s.db_name}"')
        for role, pw in (("shopops_ro", s.ro_password), ("shopops_rw", s.rw_password), ("shopops_app", s.app_password)):
            exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
            verb = "ALTER" if exists else "CREATE"
            # role names are constants and passwords are quoted by Postgres' quote_literal
            pw_literal = await conn.fetchval("SELECT quote_literal($1)", pw)
            await conn.execute(f"{verb} ROLE {role} WITH LOGIN PASSWORD {pw_literal} NOSUPERUSER NOCREATEDB NOCREATEROLE")
    finally:
        await conn.close()


async def apply_schema(s: Settings) -> None:
    conn = await _admin(s, s.db_name)
    try:
        await conn.execute(SCHEMA.read_text())
        await conn.execute(f'GRANT CONNECT ON DATABASE "{s.db_name}" TO shopops_ro, shopops_rw, shopops_app')
    finally:
        await conn.close()


async def setup(reseed: bool = False, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    await ensure_database(s)
    await apply_schema(s)
    conn = await _admin(s, s.db_name)
    try:
        empty = not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM orders)")
        if reseed or empty:
            await conn.execute("TRUNCATE support_tickets, order_items, orders, inventory, products, customers RESTART IDENTITY CASCADE")
            await seed(conn)
            print("Seeded demo data:", {t: await conn.fetchval(f"SELECT count(*) FROM {t}") for t in ("customers", "products", "orders", "order_items", "support_tickets")})
        else:
            print("Database already seeded")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reseed", action="store_true")
    asyncio.run(setup(reseed=parser.parse_args().reseed))
