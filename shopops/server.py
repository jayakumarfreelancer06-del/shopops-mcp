"""The ShopOps MCP server: tools, resources and prompts.

Transport-agnostic. `shopops.stdio` runs it over stdio for Claude Desktop; `shopops.api` mounts it as
Streamable HTTP behind API-key auth.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Awaitable, Callable

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from shopops import audit
from shopops.config import get_settings
from shopops.tools import read, write

INSTRUCTIONS = """ShopOps gives you safe access to the Kettle & Crate store backend (customers, orders, products,
inventory, support tickets). Read tools are read-only and paginated: follow `next_offset` to get more rows.
Aggregates come from `sales_summary`; there is no raw SQL tool. `update_order_status` is a two-step write:
the first call returns a preview and a confirm_token; show the preview to the user and only call again with the
token after the user explicitly confirms. Never invent confirmation on the user's behalf."""

mcp = MCPServer(name="shopops", title="ShopOps MCP", version="1.0.0", instructions=INSTRUCTIONS)

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
LIMIT = Annotated[int | None, Field(description="Page size (default 20, max 100).", ge=1, le=100)]
OFFSET = Annotated[int, Field(description="Rows to skip; use next_offset from the previous page.", ge=0)]


# ---------------------------------------------------------------------------- identity + audit
@dataclass
class Principal:
    id: str
    scope: str  # "read" | "read_write"
    transport: str


def principal_of(ctx: Context) -> Principal:
    """HTTP requests carry the principal set by the API-key middleware; stdio is the local operator."""
    try:
        request = ctx.request_context.request
    except ValueError:
        request = None
    state = getattr(request, "scope", {}).get("state", {}) if request is not None else {}
    if "principal" in state:
        return state["principal"]
    s = get_settings()
    return Principal(id=s.stdio_principal, scope=s.stdio_scope, transport="stdio")


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return jsonable(value.model_dump())
    return value


async def run_tool(ctx: Context, tool: str, args: dict, fn: Callable[[Principal], Awaitable[Any]], *, write_access: bool = False) -> Any:
    p = principal_of(ctx)
    start = time.perf_counter()
    status, error = "ok", None
    try:
        if write_access and p.scope != "read_write":
            raise ToolError("This API key is read-only. Ask an admin for a read_write key to use write tools.")
        return jsonable(await fn(p))
    except write.ToolInputError as exc:
        status, error = "rejected", str(exc)
        raise ToolError(str(exc)) from exc
    except ToolError as exc:
        status, error = "denied", str(exc)
        raise
    except Exception as exc:
        status, error = "error", type(exc).__name__
        audit.log.exception("tool_failed", tool=tool, principal=p.id)
        raise ToolError("Internal error while running the tool. The incident was logged.") from exc
    finally:
        await audit.record(
            principal=p.id, transport=p.transport, tool=tool, arguments=jsonable(args), status=status,
            duration_ms=int((time.perf_counter() - start) * 1000), error=error,
        )


# ---------------------------------------------------------------------------- read tools
@mcp.tool(annotations=READ)
async def search_customers(
    query: Annotated[str, Field(description="Part of a name, email address or city.", min_length=1, max_length=100)],
    ctx: Context,
    limit: LIMIT = None,
    offset: OFFSET = 0,
) -> dict:
    """Find customers by name, email or city. Returns order count, lifetime value and number of open orders."""
    return await run_tool(ctx, "search_customers", {"query": query, "limit": limit, "offset": offset},
                          lambda _: read.search_customers(query, limit, offset))


@mcp.tool(annotations=READ)
async def get_order(order_id: Annotated[int, Field(description="Order id, e.g. 1042.")], ctx: Context) -> dict:
    """Get one order with its customer, line items and linked support tickets."""
    async def fn(_: Principal):
        order = await read.get_order(order_id)
        if order is None:
            raise write.ToolInputError(f"order {order_id} does not exist")
        return order
    return await run_tool(ctx, "get_order", {"order_id": order_id}, fn)


@mcp.tool(annotations=READ)
async def list_orders(
    ctx: Context,
    status: Annotated[list[read.OrderStatus] | None, Field(description="Filter by one or more statuses. Open orders = pending + paid.")] = None,
    date_from: Annotated[date | None, Field(description="Created on or after this date (YYYY-MM-DD).")] = None,
    date_to: Annotated[date | None, Field(description="Created on or before this date (YYYY-MM-DD).")] = None,
    customer_id: Annotated[int | None, Field(description="Only this customer's orders.")] = None,
    product_id: Annotated[int | None, Field(description="Only orders that contain this product.")] = None,
    limit: LIMIT = None,
    offset: OFFSET = 0,
) -> dict:
    """List orders, newest first, with optional filters. Paginated."""
    args = {"status": status, "date_from": date_from, "date_to": date_to, "customer_id": customer_id, "product_id": product_id, "limit": limit, "offset": offset}
    return await run_tool(ctx, "list_orders", args, lambda _: read.list_orders(status, date_from, date_to, customer_id, product_id, limit, offset))


@mcp.tool(annotations=READ)
async def low_stock_products(
    ctx: Context,
    threshold: Annotated[int | None, Field(description="Return products with available units at or below this number.", ge=0)] = None,
    within_days: Annotated[int | None, Field(description="Return products expected to run out within this many days at the current sales rate.", ge=1, le=365)] = None,
    limit: LIMIT = None,
    offset: OFFSET = 0,
) -> dict:
    """Products that are low on stock. available = on_hand - reserved; days_of_cover and est_stockout_date use the
    30-day average daily sales. With no arguments, returns products at or below their reorder point."""
    args = {"threshold": threshold, "within_days": within_days, "limit": limit, "offset": offset}
    return await run_tool(ctx, "low_stock_products", args, lambda _: read.low_stock_products(threshold, within_days, limit, offset))


@mcp.tool(annotations=READ)
async def sales_summary(
    ctx: Context,
    period: Annotated[read.Period, Field(description="Reporting period.")] = "last_7_days",
) -> dict:
    """Aggregated sales for a period compared with the previous period of equal length: orders, revenue, average
    order value, units, orders by status, top 5 products and revenue by category. Returns aggregates only."""
    return await run_tool(ctx, "sales_summary", {"period": period}, lambda _: read.sales_summary(period))


@mcp.tool(annotations=READ)
async def search_tickets(
    ctx: Context,
    query: Annotated[str, Field(description="Text to look for in the subject or body. Empty = all.", max_length=100)] = "",
    status: Annotated[read.TicketStatus | None, Field(description="Filter by ticket status.")] = None,
    limit: LIMIT = None,
    offset: OFFSET = 0,
) -> dict:
    """Search support tickets, newest first. Paginated."""
    args = {"query": query, "status": status, "limit": limit, "offset": offset}
    return await run_tool(ctx, "search_tickets", args, lambda _: read.search_tickets(query, status, limit, offset))


# ---------------------------------------------------------------------------- write tools
@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False))
async def create_support_ticket(
    customer_id: Annotated[int, Field(description="Customer the ticket is for.")],
    subject: Annotated[str, Field(description="Short summary, max 200 characters.", min_length=1, max_length=200)],
    body: Annotated[str, Field(description="Details of the issue, max 5000 characters.", min_length=1, max_length=5000)],
    ctx: Context,
    order_id: Annotated[int | None, Field(description="Related order, if any. Must belong to the customer.")] = None,
    priority: Annotated[write.Priority, Field(description="Ticket priority.")] = "normal",
) -> dict:
    """Create a support ticket for a customer. Requires a read_write key."""
    args = {"customer_id": customer_id, "subject": subject, "body": body, "order_id": order_id, "priority": priority}
    return await run_tool(ctx, "create_support_ticket", args,
                          lambda _: write.create_support_ticket(customer_id, subject, body, order_id, priority), write_access=True)


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False))
async def update_order_status(
    order_id: Annotated[int, Field(description="Order to change.")],
    status: Annotated[read.OrderStatus, Field(description="New status. Allowed: pending->paid|cancelled, paid->shipped|cancelled|refunded, shipped->delivered, delivered->refunded.")],
    ctx: Context,
    confirm_token: Annotated[str | None, Field(description="Leave empty on the first call. Pass the token from the preview only after the user confirms.")] = None,
) -> dict:
    """Change an order's status. Two steps: the first call (no confirm_token) validates the change and returns a
    preview plus a single-use confirm_token that expires in 5 minutes. Show the preview to the user; call again with
    the token only after the user explicitly confirms. Requires a read_write key."""
    args = {"order_id": order_id, "status": status, "confirm_token": confirm_token}
    return await run_tool(ctx, "update_order_status", args,
                          lambda p: write.update_order_status(order_id, status, p.id, confirm_token), write_access=True)


# ---------------------------------------------------------------------------- resources
@mcp.resource("schema://tables", name="Database schema", description="Business tables, columns and what they mean.", mime_type="application/json")
async def schema_tables() -> str:
    return json.dumps(jsonable(await read.table_schema()), indent=2)


@mcp.resource("docs://refund-policy", name="Refund policy", description="Kettle & Crate's refund and returns policy.", mime_type="text/markdown")
def refund_policy() -> str:
    return (Path(__file__).parent / "docs" / "refund_policy.md").read_text()


# ---------------------------------------------------------------------------- prompts
@mcp.prompt(title="Weekly sales report")
def weekly_sales_report() -> str:
    """Draft the weekly sales report for the team."""
    return """Draft this week's sales report for the Kettle & Crate team.

1. Call `sales_summary` with period "last_7_days".
2. Call `low_stock_products` with within_days=7 to list stock risks.
3. Write the report in Markdown with these sections:
   - **Headline**: revenue and orders vs the previous 7 days (with % change).
   - **What sold**: top products and the category mix.
   - **Operations**: orders by status, and anything pending that needs attention.
   - **Stock risks**: products likely to run out within a week, with estimated stock-out dates.
   - **Next steps**: 2-3 concrete actions.
Keep it under 300 words. Use only numbers returned by the tools."""


@mcp.prompt(title="Triage a support ticket")
async def triage_ticket(ticket_id: Annotated[int, Field(description="Ticket to triage.")]) -> str:
    """Triage a ticket: classify it, check the order and refund policy, and draft a reply."""
    ticket = await read.get_ticket(ticket_id)
    if ticket is None:
        return f"Ticket {ticket_id} does not exist. Ask the user for a valid ticket id (use `search_tickets` to find one)."
    order_hint = f"Call `get_order` with order_id={ticket['order_id']} to check the order." if ticket["order_id"] else "The ticket is not linked to an order."
    return f"""Triage support ticket #{ticket['id']}.

Customer: {ticket['customer_name']} <{ticket['customer_email']}> (customer_id {ticket['customer_id']})
Status: {ticket['status']} · Priority: {ticket['priority']} · Received: {ticket['created_at']:%Y-%m-%d}
Subject: {ticket['subject']}
Body:
{ticket['body']}

Steps:
1. {order_hint}
2. Read the resource `docs://refund-policy`.
3. Reply with: category (delivery / damage / refund / wrong item / billing / question), recommended priority with a one-line
   reason, the policy rule that applies, a short customer-facing reply in a friendly tone, and the next internal action.
Do not change anything in the system; suggest write actions (e.g. a status change) for the user to approve."""
