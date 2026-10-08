"""Deterministic demo data for "Kettle & Crate", a small online home-and-kitchen store.

* 150 customers, 40 products, 500 orders over the last 120 days, ~120 support tickets
* Order ids run 551..1050, so order 1042 is one of the newest and sits in `paid` (ready to ship)
* A handful of fast-selling products are low on stock and appear in open orders, so
  "which products will run out this week, and who has open orders for them?" has a real answer
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import asyncpg

FIRST = ["Aarav", "Maya", "Liam", "Sofia", "Noah", "Isla", "Ethan", "Zara", "Lucas", "Ava", "Arjun", "Mia", "Leo", "Chloe", "Omar",
         "Priya", "Daniel", "Hannah", "Kenji", "Elena", "Mateo", "Grace", "Ravi", "Nora", "Samuel", "Leah", "Felix", "Amara", "Jonas", "Ines"]
LAST = ["Sharma", "Garcia", "Smith", "Rossi", "Khan", "Murphy", "Tanaka", "Okafor", "Silva", "Novak", "Iyer", "Brown", "Weber", "Haddad",
        "Kim", "Lopez", "Nair", "Fischer", "Dubois", "Cohen"]
CITIES = [("Austin", "US"), ("Seattle", "US"), ("Chicago", "US"), ("Denver", "US"), ("Boston", "US"), ("Toronto", "CA"), ("Vancouver", "CA"),
          ("London", "GB"), ("Manchester", "GB"), ("Dublin", "IE"), ("Berlin", "DE"), ("Amsterdam", "NL")]

PRODUCTS = {
    "Coffee & Tea": [("Pour-Over Kettle 1L", 64), ("Ceramic Dripper", 28), ("Burr Grinder Pro", 189), ("Glass Teapot 800ml", 36),
                     ("Cold Brew Jar", 32), ("Milk Frother", 45), ("Espresso Tamper", 24), ("Paper Filters (100)", 9)],
    "Cookware": [("Cast Iron Skillet 26cm", 58), ("Carbon Steel Wok", 72), ("Dutch Oven 5L", 145), ("Nonstick Pan 28cm", 49),
                 ("Stock Pot 8L", 69), ("Sauce Pan 2L", 44), ("Grill Pan", 52), ("Baking Sheet Set", 34)],
    "Knives": [("Chef Knife 20cm", 119), ("Santoku 18cm", 98), ("Paring Knife", 29), ("Bread Knife", 54), ("Whetstone 1000/6000", 39),
               ("Magnetic Knife Strip", 35)],
    "Tableware": [("Stoneware Plate Set (4)", 68), ("Pasta Bowls (4)", 56), ("Linen Napkins (6)", 38), ("Tumbler Glasses (6)", 42),
                  ("Serving Board Oak", 47), ("Espresso Cups (2)", 26)],
    "Storage": [("Glass Containers (10)", 59), ("Spice Jars (12)", 33), ("Bread Bin", 48), ("Vacuum Canister 1.5L", 27),
                ("Beeswax Wraps (3)", 18), ("Oil Dispenser", 22)],
    "Small Appliances": [("Digital Scale", 31), ("Hand Blender", 79), ("Electric Kettle", 85), ("Toaster 2-Slice", 69),
                         ("Rice Cooker", 99), ("Sous Vide Stick", 149)],
}
# indexes (into the flattened product list) of fast sellers that will be low on stock
LOW_STOCK = {2: 4, 8: 6, 14: 3, 33: 5, 37: 2}  # product index -> on_hand

TICKETS = [
    ("Where is my order?", "Hi, my order #{order} still hasn't arrived and tracking hasn't updated for days. Can you check?", "normal"),
    ("Item arrived damaged", "The {product} in order #{order} arrived with a dent/crack. I'd like a replacement or refund.", "high"),
    ("Refund request", "I'd like to return the {product} from order #{order}. It isn't what I expected.", "normal"),
    ("Wrong item received", "I ordered the {product} (order #{order}) but received something else.", "high"),
    ("Question about {product}", "Is the {product} dishwasher safe and does it work on induction?", "low"),
    ("Charged twice", "I see two charges for order #{order} on my card statement. Please fix this ASAP.", "urgent"),
    ("Change shipping address", "Can I change the delivery address for order #{order}? I moved last week.", "normal"),
]


async def seed(conn: asyncpg.Connection) -> None:
    rnd = random.Random(42)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    # customers -------------------------------------------------------------------
    customers = []
    seen = set()
    while len(customers) < 150:
        first, last = rnd.choice(FIRST), rnd.choice(LAST)
        email = f"{first}.{last}{rnd.randint(1, 99)}@example.com".lower()
        if email in seen:
            continue
        seen.add(email)
        city, country = rnd.choice(CITIES)
        created = now - timedelta(days=rnd.randint(120, 900))
        customers.append((f"{first} {last}", email, f"+1-555-{rnd.randint(1000, 9999)}", city, country, created))
    await conn.executemany("INSERT INTO customers (name, email, phone, city, country, created_at) VALUES ($1,$2,$3,$4,$5,$6)", customers)

    # products --------------------------------------------------------------------
    products = []
    for cat, items in PRODUCTS.items():
        for name, price in items:
            sku = f"{cat[:3].upper()}-{len(products) + 1:03d}"
            products.append((sku, name, cat, Decimal(price)))
    await conn.executemany("INSERT INTO products (sku, name, category, price) VALUES ($1,$2,$3,$4)", products)
    n_products = len(products)
    popularity = [rnd.uniform(0.3, 1.0) for _ in range(n_products)]
    for idx in LOW_STOCK:
        popularity[idx] = 3.0  # fast sellers

    # orders: ids 551..1050 in chronological order ------------------------------------
    await conn.execute("ALTER SEQUENCE orders_id_seq RESTART WITH 551")
    dates = sorted(now - timedelta(days=120 * rnd.random() ** 1.3) for _ in range(500))  # skewed towards recent orders
    open_by_product: dict[int, int] = {}
    for i, created in enumerate(dates):
        order_id = 551 + i
        age = (now - created).days
        if order_id == 1042:
            status = "paid"
        elif age <= 2:
            status = rnd.choice(["pending", "paid", "paid"])
        elif age <= 6:
            status = rnd.choices(["paid", "shipped", "cancelled"], [2, 7, 1])[0]
        elif age <= 12:
            status = rnd.choices(["shipped", "delivered", "cancelled"], [3, 6, 1])[0]
        else:
            status = rnd.choices(["delivered", "cancelled", "refunded"], [90, 5, 5])[0]

        customer_id = rnd.randint(1, len(customers))
        k = rnd.choices([1, 2, 3, 4], [45, 30, 17, 8])[0]
        weights = popularity if status in ("pending", "paid") else [p if p < 3 else 1.6 for p in popularity]
        picks = set(rnd.choices(range(n_products), weights=weights, k=k))
        if order_id == 1042:
            picks |= {2}
        items = [(idx, rnd.choices([1, 2, 3], [70, 22, 8])[0]) for idx in picks]
        total = sum(products[idx][3] * qty for idx, qty in items)
        city = customers[customer_id - 1][3]
        updated = created + timedelta(days=min(age, rnd.randint(0, 5)))
        await conn.execute(
            "INSERT INTO orders (id, customer_id, status, total, shipping_city, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7)",
            order_id, customer_id, status, total, city, created, updated,
        )
        await conn.executemany(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES ($1,$2,$3,$4)",
            [(order_id, idx + 1, qty, products[idx][3]) for idx, qty in items],
        )
        if status in ("pending", "paid"):
            for idx, qty in items:
                open_by_product[idx] = open_by_product.get(idx, 0) + qty
    await conn.execute("SELECT setval('orders_id_seq', (SELECT max(id) FROM orders))")

    # inventory: avg_daily_sales from the last 30 days, reserved = open orders --------------
    sold = dict(await conn.fetch(
        """SELECT oi.product_id, sum(oi.quantity) FROM order_items oi JOIN orders o ON o.id = oi.order_id
           WHERE o.created_at >= now() - interval '30 days' AND o.status NOT IN ('cancelled') GROUP BY oi.product_id"""
    ))
    inventory = []
    for idx in range(n_products):
        avg = Decimal(sold.get(idx + 1, 0)) / 30
        reserved = open_by_product.get(idx, 0)
        on_hand = LOW_STOCK.get(idx, rnd.randint(40, 400)) + reserved
        reorder_point = max(5, int(avg * 10))
        inventory.append((idx + 1, on_hand, reserved, reorder_point, round(avg, 2), now))
    await conn.executemany(
        "INSERT INTO inventory (product_id, on_hand, reserved, reorder_point, avg_daily_sales, updated_at) VALUES ($1,$2,$3,$4,$5,$6)", inventory
    )

    # support tickets -----------------------------------------------------------------
    orders = await conn.fetch(
        "SELECT o.id, o.customer_id, o.created_at, p.name AS product FROM orders o "
        "JOIN LATERAL (SELECT product_id FROM order_items WHERE order_id = o.id LIMIT 1) oi ON TRUE "
        "JOIN products p ON p.id = oi.product_id WHERE o.status <> 'cancelled' ORDER BY o.id"
    )
    tickets = []
    for o in rnd.sample(list(orders), 120):
        subject, body, priority = rnd.choice(TICKETS)
        created = o["created_at"] + timedelta(days=rnd.uniform(1, 8))
        if created > now:
            created = now - timedelta(hours=rnd.randint(1, 20))
        age = (now - created).days
        status = rnd.choices(["open", "pending", "resolved", "closed"], [5, 3, 1, 1] if age < 7 else [1, 1, 4, 6])[0]
        fill = {"order": o["id"], "product": o["product"]}
        tickets.append((o["customer_id"], o["id"], subject.format(**fill), body.format(**fill), status, priority, rnd.choice(["email", "chat"]), created, created))
    await conn.executemany(
        "INSERT INTO support_tickets (customer_id, order_id, subject, body, status, priority, source, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
        tickets,
    )
