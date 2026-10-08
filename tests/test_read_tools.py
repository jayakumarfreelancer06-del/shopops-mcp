from datetime import date, timedelta

from shopops.tools import read


async def test_search_customers_matches_name_email_city_and_paginates():
    page = await read.search_customers("example.com", limit=10)
    assert page.total == 150 and len(page.items) == 10 and page.next_offset == 10
    last = await read.search_customers("example.com", limit=10, offset=140)
    assert len(last.items) == 10 and last.next_offset is None
    assert {"orders", "lifetime_value", "open_orders"} <= set(page.items[0])


async def test_page_size_is_capped():
    page = await read.list_orders(limit=10_000)
    assert page.limit == 100 and len(page.items) == 100


async def test_like_wildcards_and_sql_are_treated_as_text():
    assert (await read.search_customers("%")).total == 0
    assert (await read.search_customers("'; DROP TABLE orders; --")).total == 0
    assert (await read.list_orders()).total == 500  # still there


async def test_get_order_includes_customer_items_and_tickets():
    order = await read.get_order(1042)
    assert order["status"] == "paid"
    assert order["customer"]["name"] and order["items"]
    assert all(i["line_total"] == i["quantity"] * i["unit_price"] for i in order["items"])
    assert await read.get_order(999_999) is None


async def test_list_orders_filters():
    open_orders = await read.list_orders(status=["pending", "paid"], limit=100)
    assert open_orders.total > 0 and all(o["status"] in ("pending", "paid") for o in open_orders.items)
    week = await read.list_orders(date_from=date.today() - timedelta(days=7), limit=100)
    assert 0 < week.total < 500
    with_grinder = await read.list_orders(product_id=3, status=["pending", "paid"])
    for o in with_grinder.items:
        assert any(i["product_id"] == 3 for i in (await read.get_order(o["id"]))["items"])


async def test_low_stock_within_a_week_has_open_orders():
    low = await read.low_stock_products(within_days=7)
    assert low.total >= 3
    assert all(p["days_of_cover"] <= 7 for p in low.items)
    assert all(p["available"] == p["on_hand"] - p["reserved"] for p in low.items)
    first = low.items[0]
    assert (await read.list_orders(status=["pending", "paid"], product_id=first["product_id"])).total > 0


async def test_low_stock_threshold():
    page = await read.low_stock_products(threshold=5)
    assert all(p["available"] <= 5 for p in page.items)


async def test_sales_summary_returns_aggregates_only():
    s = await read.sales_summary("last_30_days")
    assert s["orders"] > 0 and s["revenue"] > 0
    assert len(s["top_products"]) == 5
    assert abs(sum(r["revenue"] for r in s["revenue_by_category"]) - s["revenue"]) < 0.01
    assert "customer_email" not in str(s)


async def test_period_bounds_previous_period_has_equal_length():
    start, end, prev = read.period_bounds("last_7_days")
    assert (end - start) == (start - prev) == timedelta(days=7)


async def test_search_tickets_by_text_and_status():
    damaged = await read.search_tickets("damaged")
    assert damaged.total > 0 and all("damaged" in (t["subject"] + t["body"]).lower() for t in damaged.items)
    closed = await read.search_tickets(status="closed", limit=100)
    assert all(t["status"] == "closed" for t in closed.items)


async def test_schema_resource_lists_business_tables_only():
    tables = {t["table_name"] for t in await read.table_schema()}
    assert tables == {"customers", "products", "inventory", "orders", "order_items", "support_tickets"}
