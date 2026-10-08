-- Business data ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT,
    city        TEXT NOT NULL,
    country     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS inventory (
    product_id       INTEGER PRIMARY KEY REFERENCES products(id),
    on_hand          INTEGER NOT NULL CHECK (on_hand >= 0),
    reserved         INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    reorder_point    INTEGER NOT NULL,
    avg_daily_sales  NUMERIC(8, 2) NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS orders (
    id             SERIAL PRIMARY KEY,
    customer_id    INTEGER NOT NULL REFERENCES customers(id),
    status         TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'shipped', 'delivered', 'cancelled', 'refunded')),
    total          NUMERIC(10, 2) NOT NULL,
    shipping_city  TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_status_created_idx ON orders (status, created_at);
CREATE INDEX IF NOT EXISTS orders_customer_idx ON orders (customer_id);

CREATE TABLE IF NOT EXISTS order_items (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id  INTEGER NOT NULL REFERENCES products(id),
    quantity    INTEGER NOT NULL CHECK (quantity > 0),
    unit_price  NUMERIC(10, 2) NOT NULL
);
CREATE INDEX IF NOT EXISTS order_items_order_idx ON order_items (order_id);
CREATE INDEX IF NOT EXISTS order_items_product_idx ON order_items (product_id);

CREATE TABLE IF NOT EXISTS support_tickets (
    id           SERIAL PRIMARY KEY,
    customer_id  INTEGER NOT NULL REFERENCES customers(id),
    order_id     INTEGER REFERENCES orders(id),
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'pending', 'resolved', 'closed')),
    priority     TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    source       TEXT NOT NULL DEFAULT 'email',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tickets_status_idx ON support_tickets (status);

COMMENT ON TABLE customers IS 'People who have placed orders.';
COMMENT ON TABLE products IS 'Catalogue. price is the current list price in USD.';
COMMENT ON TABLE inventory IS 'Stock per product. available = on_hand - reserved. avg_daily_sales is a trailing 30-day average.';
COMMENT ON TABLE orders IS 'Lifecycle: pending -> paid -> shipped -> delivered; cancelled / refunded are terminal.';
COMMENT ON TABLE order_items IS 'Line items; unit_price is the price at the time of the order.';
COMMENT ON TABLE support_tickets IS 'Customer support tickets, optionally linked to an order.';

-- App data (separate schema, separate role) -------------------------------------
CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.api_keys (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    prefix        TEXT NOT NULL,
    key_hash      TEXT NOT NULL UNIQUE,
    scope         TEXT NOT NULL CHECK (scope IN ('read', 'read_write')),
    owner_email   TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app.pending_confirmations (
    token_hash    TEXT PRIMARY KEY,
    action        TEXT NOT NULL,
    principal     TEXT NOT NULL,
    payload       JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL,
    used_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app.tool_calls (
    id           BIGSERIAL PRIMARY KEY,
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    principal    TEXT NOT NULL,
    transport    TEXT NOT NULL,
    tool         TEXT NOT NULL,
    arguments    JSONB NOT NULL,
    status       TEXT NOT NULL,
    duration_ms  INTEGER NOT NULL,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS tool_calls_at_idx ON app.tool_calls (at DESC);

-- Least-privilege grants ---------------------------------------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- read tools: SELECT only, on business tables only
GRANT USAGE ON SCHEMA public TO shopops_ro;
GRANT SELECT ON customers, products, inventory, orders, order_items, support_tickets TO shopops_ro;

-- write tools: may create tickets and change an order's status, nothing else
GRANT USAGE ON SCHEMA public TO shopops_rw;
GRANT SELECT ON customers, products, inventory, orders, order_items, support_tickets TO shopops_rw;
GRANT INSERT ON support_tickets TO shopops_rw;
GRANT USAGE ON SEQUENCE support_tickets_id_seq TO shopops_rw;
GRANT UPDATE (status, updated_at) ON orders TO shopops_rw;

-- app role: its own schema only
GRANT USAGE ON SCHEMA app TO shopops_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO shopops_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO shopops_app;
