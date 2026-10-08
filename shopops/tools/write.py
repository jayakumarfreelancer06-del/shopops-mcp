"""Guarded write tools. They run as the `shopops_rw` role, which can only INSERT tickets and UPDATE an order's status."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from shopops.config import get_settings
from shopops.db import execute, fetch_one
from shopops.tools.read import OrderStatus, get_order

Priority = Literal["low", "normal", "high", "urgent"]

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"paid", "cancelled"},
    "paid": {"shipped", "cancelled", "refunded"},
    "shipped": {"delivered"},
    "delivered": {"refunded"},
    "cancelled": set(),
    "refunded": set(),
}


class ToolInputError(ValueError):
    """A problem with the caller's input; the message is safe to show to the model."""


async def create_support_ticket(customer_id: int, subject: str, body: str, order_id: int | None = None, priority: Priority = "normal") -> dict:
    subject, body = subject.strip(), body.strip()
    if not subject or not body:
        raise ToolInputError("subject and body must not be empty")
    if len(subject) > 200 or len(body) > 5000:
        raise ToolInputError("subject is limited to 200 characters and body to 5000")
    if not await fetch_one("rw", "SELECT id FROM customers WHERE id = :id", id=customer_id):
        raise ToolInputError(f"customer {customer_id} does not exist")
    if order_id is not None and not await fetch_one("rw", "SELECT id FROM orders WHERE id = :o AND customer_id = :c", o=order_id, c=customer_id):
        raise ToolInputError(f"order {order_id} does not belong to customer {customer_id}")
    rows = await execute(
        "rw",
        """INSERT INTO support_tickets (customer_id, order_id, subject, body, priority, source)
           VALUES (:customer_id, :order_id, :subject, :body, :priority, 'mcp')
           RETURNING id, customer_id, order_id, subject, status, priority, created_at""",
        customer_id=customer_id, order_id=order_id, subject=subject, body=body, priority=priority,
    )
    return rows[0]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def update_order_status(order_id: int, status: OrderStatus, principal: str, confirm_token: str | None = None) -> dict:
    """Two-step write. Without a token: validate and return a preview + confirm_token. With the token: apply."""
    order = await get_order(order_id)
    if order is None:
        raise ToolInputError(f"order {order_id} does not exist")
    current = order["status"]
    if status == current:
        raise ToolInputError(f"order {order_id} is already {status}")
    if status not in ALLOWED_TRANSITIONS[current]:
        allowed = ", ".join(sorted(ALLOWED_TRANSITIONS[current])) or "none (terminal status)"
        raise ToolInputError(f"cannot change order {order_id} from {current} to {status}; allowed: {allowed}")

    if confirm_token is None:
        return await _preview(order, status, principal)
    return await _apply(order, status, principal, confirm_token)


async def _preview(order: dict, status: str, principal: str) -> dict:
    s = get_settings()
    token = "cfm_" + secrets.token_urlsafe(18)
    expires = datetime.now(timezone.utc) + timedelta(seconds=s.confirm_ttl_seconds)
    payload = {"order_id": order["id"], "from_status": order["status"], "to_status": status}
    await execute(
        "app",
        """INSERT INTO app.pending_confirmations (token_hash, action, principal, payload, expires_at)
           VALUES (:h, 'update_order_status', :p, CAST(:payload AS JSONB), :exp)""",
        h=_hash(token), p=principal, payload=json.dumps(payload), exp=expires,
    )
    return {
        "requires_confirmation": True,
        "preview": {
            "order_id": order["id"],
            "customer": order["customer"]["name"],
            "current_status": order["status"],
            "new_status": status,
            "order_total": order["total"],
            "items": [f"{i['quantity']} x {i['name']}" for i in order["items"]],
            "open_tickets": [t["id"] for t in order["tickets"] if t["status"] in ("open", "pending")],
        },
        "confirm_token": token,
        "expires_at": expires.isoformat(),
        "instructions": "Show this preview to the user. Only if the user explicitly confirms, call update_order_status again "
                        "with the same order_id and status plus this confirm_token. The token is single-use and expires in "
                        f"{s.confirm_ttl_seconds // 60} minutes.",
    }


async def _apply(order: dict, status: str, principal: str, token: str) -> dict:
    # consume the token atomically: it must exist, match this exact change and caller, be unused and unexpired
    consumed = await execute(
        "app",
        """UPDATE app.pending_confirmations SET used_at = now()
           WHERE token_hash = :h AND action = 'update_order_status' AND principal = :p AND used_at IS NULL AND expires_at > now()
             AND payload->>'order_id' = :order_id AND payload->>'to_status' = :status
           RETURNING payload""",
        h=_hash(token), p=principal, order_id=str(order["id"]), status=status,
    )
    if not consumed:
        raise ToolInputError("invalid, expired or already used confirm_token for this change; call without a token to get a new preview")
    from_status = consumed[0]["payload"]["from_status"]
    # optimistic concurrency: only apply if nobody changed the order since the preview
    updated = await execute(
        "rw",
        "UPDATE orders SET status = :to, updated_at = now() WHERE id = :id AND status = :from RETURNING id, status, updated_at",
        to=status, id=order["id"], **{"from": from_status},
    )
    if not updated:
        raise ToolInputError(f"order {order['id']} changed since the preview (now {order['status']}); request a new preview")
    return {"applied": True, "order_id": order["id"], "previous_status": from_status, "status": status, "updated_at": updated[0]["updated_at"]}
