"""Read tools. All run as the read-only `shopops_ro` role with bound parameters, and every list is paginated."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from shopops.config import get_settings
from shopops.db import fetch_all, fetch_one

OrderStatus = Literal["pending", "paid", "shipped", "delivered", "cancelled", "refunded"]
TicketStatus = Literal["open", "pending", "resolved", "closed"]
Period = Literal["last_7_days", "last_30_days", "last_90_days", "month_to_date", "year_to_date"]


class Page(BaseModel):
    items: list[dict]
    total: int
    limit: int
    offset: int
    next_offset: int | None


def clamp(limit: int | None) -> int:
    s = get_settings()
    return max(1, min(limit or s.default_page_size, s.max_page_size))


def _page(items: list[dict], total: int, limit: int, offset: int) -> Page:
    return Page(items=items, total=total, limit=limit, offset=offset, next_offset=offset + limit if offset + limit < total else None)


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").strip() + "%"


async def search_customers(query: str, limit: int | None = None, offset: int = 0) -> Page:
    limit = clamp(limit)
    where = "c.name ILIKE :q OR c.email ILIKE :q OR c.city ILIKE :q"
    rows = await fetch_all(
        "ro",
        f"""SELECT c.id, c.name, c.email, c.city, c.country,
                   count(o.id) AS orders, coalesce(sum(o.total) FILTER (WHERE o.status NOT IN ('cancelled','refunded')), 0) AS lifetime_value,
                   count(o.id) FILTER (WHERE o.status IN ('pending','paid')) AS open_orders
            FROM customers c LEFT JOIN orders o ON o.customer_id = c.id
            WHERE {where}
            GROUP BY c.id ORDER BY c.name LIMIT :limit OFFSET :offset""",
        q=_like(query), limit=limit, offset=offset,
    )
    total = (await fetch_one("ro", f"SELECT count(*) AS n FROM customers c WHERE {where}", q=_like(query)))["n"]
    return _page(rows, total, limit, offset)


async def get_order(order_id: int) -> dict | None:
    order = await fetch_one(
        "ro",
        """SELECT o.id, o.status, o.total, o.shipping_city, o.created_at, o.updated_at,
                  json_build_object('id', c.id, 'name', c.name, 'email', c.email) AS customer
           FROM orders o JOIN customers c ON c.id = o.customer_id WHERE o.id = :id""",
        id=order_id,
    )
    if order is None:
        return None
    order["items"] = await fetch_all(
        "ro",
        """SELECT p.id AS product_id, p.sku, p.name, oi.quantity, oi.unit_price, oi.quantity * oi.unit_price AS line_total
           FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE oi.order_id = :id ORDER BY p.name""",
        id=order_id,
    )
    order["tickets"] = await fetch_all(
        "ro", "SELECT id, subject, status, priority, created_at FROM support_tickets WHERE order_id = :id ORDER BY created_at", id=order_id
    )
    return order


async def list_orders(
    status: list[OrderStatus] | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    customer_id: int | None = None,
    product_id: int | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Page:
    limit = clamp(limit)
    clauses, params = ["TRUE"], {}
    if status:
        clauses.append("o.status = ANY(:status)")
        params["status"] = list(status)
    if date_from:
        clauses.append("o.created_at >= :date_from")
        params["date_from"] = datetime.combine(date_from, datetime.min.time(), timezone.utc)
    if date_to:
        clauses.append("o.created_at < :date_to")
        params["date_to"] = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), timezone.utc)
    if customer_id:
        clauses.append("o.customer_id = :customer_id")
        params["customer_id"] = customer_id
    if product_id:
        clauses.append("EXISTS (SELECT 1 FROM order_items x WHERE x.order_id = o.id AND x.product_id = :product_id)")
        params["product_id"] = product_id
    where = " AND ".join(clauses)
    rows = await fetch_all(
        "ro",
        f"""SELECT o.id, o.status, o.total, o.created_at, c.id AS customer_id, c.name AS customer_name, c.email AS customer_email,
                   (SELECT count(*) FROM order_items x WHERE x.order_id = o.id) AS item_count
            FROM orders o JOIN customers c ON c.id = o.customer_id
            WHERE {where} ORDER BY o.created_at DESC LIMIT :limit OFFSET :offset""",
        limit=limit, offset=offset, **params,
    )
    total = (await fetch_one("ro", f"SELECT count(*) AS n FROM orders o WHERE {where}", **params))["n"]
    return _page(rows, total, limit, offset)


async def low_stock_products(threshold: int | None = None, within_days: int | None = None, limit: int | None = None, offset: int = 0) -> Page:
    """threshold: available units at or below this. within_days: expected to run out within N days. Neither: at or below reorder point."""
    limit = clamp(limit)
    available = "(i.on_hand - i.reserved)"
    cover = f"CASE WHEN i.avg_daily_sales > 0 THEN round({available} / i.avg_daily_sales, 1) END"
    if threshold is not None:
        cond, params = f"{available} <= :threshold", {"threshold": threshold}
    elif within_days is not None:
        cond, params = f"i.avg_daily_sales > 0 AND {available} / i.avg_daily_sales <= :days", {"days": within_days}
    else:
        cond, params = f"{available} <= i.reorder_point", {}
    rows = await fetch_all(
        "ro",
        f"""SELECT p.id AS product_id, p.sku, p.name, p.category, i.on_hand, i.reserved, {available} AS available,
                   i.reorder_point, i.avg_daily_sales, {cover} AS days_of_cover,
                   CASE WHEN i.avg_daily_sales > 0 THEN (now() + make_interval(days => floor({available} / i.avg_daily_sales)::int))::date END AS est_stockout_date
            FROM inventory i JOIN products p ON p.id = i.product_id
            WHERE p.active AND {cond}
            ORDER BY {cover} NULLS LAST, available LIMIT :limit OFFSET :offset""",
        limit=limit, offset=offset, **params,
    )
    total = (await fetch_one("ro", f"SELECT count(*) AS n FROM inventory i JOIN products p ON p.id = i.product_id WHERE p.active AND {cond}", **params))["n"]
    return _page(rows, total, limit, offset)


def period_bounds(period: Period, now: datetime | None = None) -> tuple[datetime, datetime, datetime]:
    """Returns (start, end, previous_start) for the period and the equally long period before it."""
    now = now or datetime.now(timezone.utc)
    if period.startswith("last_"):
        days = int(period.split("_")[1])
        start = now - timedelta(days=days)
    elif period == "month_to_date":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, now, start - (now - start)


async def sales_summary(period: Period = "last_7_days") -> dict:
    start, end, prev_start = period_bounds(period)
    valid = "o.status NOT IN ('cancelled', 'refunded')"
    agg = """SELECT count(DISTINCT o.id) AS orders, coalesce(sum(o.total), 0) AS revenue,
                    coalesce(round(avg(o.total), 2), 0) AS avg_order_value
             FROM orders o WHERE {valid} AND o.created_at >= :a AND o.created_at < :b"""
    cur = await fetch_one("ro", agg.format(valid=valid), a=start, b=end)
    prev = await fetch_one("ro", agg.format(valid=valid), a=prev_start, b=start)
    units = await fetch_one(
        "ro",
        f"SELECT coalesce(sum(oi.quantity), 0) AS units FROM order_items oi JOIN orders o ON o.id = oi.order_id WHERE {valid} AND o.created_at >= :a AND o.created_at < :b",
        a=start, b=end,
    )
    by_status = await fetch_all(
        "ro", "SELECT status, count(*) AS orders FROM orders WHERE created_at >= :a AND created_at < :b GROUP BY status ORDER BY orders DESC", a=start, b=end
    )
    top_products = await fetch_all(
        "ro",
        f"""SELECT p.name, p.category, sum(oi.quantity) AS units, sum(oi.quantity * oi.unit_price) AS revenue
            FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id
            WHERE {valid} AND o.created_at >= :a AND o.created_at < :b
            GROUP BY p.id ORDER BY revenue DESC LIMIT 5""",
        a=start, b=end,
    )
    by_category = await fetch_all(
        "ro",
        f"""SELECT p.category, sum(oi.quantity * oi.unit_price) AS revenue
            FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id
            WHERE {valid} AND o.created_at >= :a AND o.created_at < :b
            GROUP BY p.category ORDER BY revenue DESC""",
        a=start, b=end,
    )

    def delta(a: Decimal | int, b: Decimal | int) -> float | None:
        return None if not b else round((float(a) - float(b)) / float(b) * 100, 1)

    return {
        "period": period,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "orders": cur["orders"],
        "revenue": cur["revenue"],
        "avg_order_value": cur["avg_order_value"],
        "units_sold": units["units"],
        "previous_period": {"orders": prev["orders"], "revenue": prev["revenue"]},
        "change_pct": {"orders": delta(cur["orders"], prev["orders"]), "revenue": delta(cur["revenue"], prev["revenue"])},
        "orders_by_status": by_status,
        "top_products": top_products,
        "revenue_by_category": by_category,
        "note": "Revenue excludes cancelled and refunded orders. Amounts in USD.",
    }


async def search_tickets(query: str = "", status: TicketStatus | None = None, limit: int | None = None, offset: int = 0) -> Page:
    limit = clamp(limit)
    clauses, params = ["TRUE"], {}
    if query.strip():
        clauses.append("(t.subject ILIKE :q OR t.body ILIKE :q)")
        params["q"] = _like(query)
    if status:
        clauses.append("t.status = :status")
        params["status"] = status
    where = " AND ".join(clauses)
    rows = await fetch_all(
        "ro",
        f"""SELECT t.id, t.subject, t.body, t.status, t.priority, t.source, t.order_id, t.created_at,
                   c.id AS customer_id, c.name AS customer_name
            FROM support_tickets t JOIN customers c ON c.id = t.customer_id
            WHERE {where} ORDER BY t.created_at DESC LIMIT :limit OFFSET :offset""",
        limit=limit, offset=offset, **params,
    )
    total = (await fetch_one("ro", f"SELECT count(*) AS n FROM support_tickets t WHERE {where}", **params))["n"]
    return _page(rows, total, limit, offset)


async def get_ticket(ticket_id: int) -> dict | None:
    return await fetch_one(
        "ro",
        """SELECT t.*, c.name AS customer_name, c.email AS customer_email
           FROM support_tickets t JOIN customers c ON c.id = t.customer_id WHERE t.id = :id""",
        id=ticket_id,
    )


async def table_schema() -> list[dict]:
    return await fetch_all(
        "ro",
        """SELECT c.table_name, obj_description(('public.' || c.table_name)::regclass) AS table_comment,
                  json_agg(json_build_object('column', c.column_name, 'type', c.data_type, 'nullable', c.is_nullable = 'YES')
                           ORDER BY c.ordinal_position) AS columns
           FROM information_schema.columns c
           WHERE c.table_schema = 'public'
             AND c.table_name IN ('customers','products','inventory','orders','order_items','support_tickets')
           GROUP BY c.table_name ORDER BY c.table_name""",
    )
