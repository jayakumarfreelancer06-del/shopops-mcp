"""API keys for the remote (Streamable HTTP) transport. Only a SHA-256 hash of each key is stored."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from shopops.config import get_settings
from shopops.db import execute, fetch_one
from shopops.server import Principal

KEY_PREFIX = "sk_shopops_"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


async def create_key(name: str, scope: str, owner_email: str) -> tuple[str, dict]:
    key = KEY_PREFIX + secrets.token_urlsafe(24)
    row = (await execute(
        "app",
        """INSERT INTO app.api_keys (name, prefix, key_hash, scope, owner_email) VALUES (:n, :p, :h, :s, :o)
           RETURNING id, name, prefix, scope, owner_email, created_at, last_used_at, revoked_at""",
        n=name, p=key[: len(KEY_PREFIX) + 4], h=hash_key(key), s=scope, o=owner_email,
    ))[0]
    return key, row


async def verify(key: str | None) -> Principal | None:
    if not key:
        return None
    s = get_settings()
    if s.bootstrap_api_key and hmac.compare_digest(key, s.bootstrap_api_key):
        return Principal(id="bootstrap-key", scope="read_write", transport="http")
    row = await fetch_one(
        "app",
        "UPDATE app.api_keys SET last_used_at = now() WHERE key_hash = :h AND revoked_at IS NULL RETURNING id, name, scope, owner_email",
        h=hash_key(key),
    )
    if row is None:
        return None
    return Principal(id=f"key:{row['name']}#{row['id']} ({row['owner_email']})", scope=row["scope"], transport="http")
