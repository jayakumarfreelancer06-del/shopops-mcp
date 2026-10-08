"""Structured logging for every tool call: who, what, how long, outcome. Written to stdout (JSON) and app.tool_calls."""

from __future__ import annotations

import json
import logging
import sys

import structlog

from shopops.db import execute

structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),  # stderr: stdout belongs to the stdio transport
)
log = structlog.get_logger("shopops")
_fallback = logging.getLogger("shopops.audit")

REDACT = {"confirm_token"}
MAX_ARG_CHARS = 300


def redact(args: dict) -> dict:
    out = {}
    for k, v in args.items():
        if k in REDACT and v:
            out[k] = "***"
        elif isinstance(v, str) and len(v) > MAX_ARG_CHARS:
            out[k] = v[:MAX_ARG_CHARS] + "…"
        else:
            out[k] = v
    return out


async def record(*, principal: str, transport: str, tool: str, arguments: dict, status: str, duration_ms: int, error: str | None) -> None:
    args = redact(arguments)
    log.info("tool_call", tool=tool, principal=principal, transport=transport, status=status, duration_ms=duration_ms, error=error, arguments=args)
    try:
        await execute(
            "app",
            """INSERT INTO app.tool_calls (principal, transport, tool, arguments, status, duration_ms, error)
               VALUES (:principal, :transport, :tool, CAST(:arguments AS JSONB), :status, :duration_ms, :error)""",
            principal=principal, transport=transport, tool=tool, arguments=json.dumps(args, default=str),
            status=status, duration_ms=duration_ms, error=error,
        )
    except Exception:  # auditing must never break the tool call itself
        _fallback.exception("failed to write audit row")
