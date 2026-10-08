"""The database itself enforces least privilege, independent of the Python code."""

import pytest
from sqlalchemy.exc import DBAPIError

from shopops.db import execute, fetch_one


@pytest.mark.parametrize(
    "role, sql",
    [
        ("ro", "INSERT INTO support_tickets (customer_id, subject, body) VALUES (1, 'x', 'y')"),
        ("ro", "UPDATE orders SET status = 'shipped' WHERE id = 1042"),
        ("ro", "DELETE FROM customers WHERE id = 1"),
        ("ro", "SELECT * FROM app.api_keys"),
        ("rw", "DELETE FROM orders WHERE id = 1042"),
        ("rw", "UPDATE orders SET total = 0 WHERE id = 1042"),
        ("rw", "UPDATE customers SET email = 'x@y.z' WHERE id = 1"),
        ("rw", "SELECT * FROM app.tool_calls"),
        ("app", "SELECT * FROM customers"),
        ("ro", "CREATE TABLE evil (id int)"),
    ],
)
async def test_role_cannot(role, sql):
    with pytest.raises(DBAPIError, match="permission denied"):
        await execute(role, sql)


async def test_read_role_can_read():
    assert (await fetch_one("ro", "SELECT count(*) AS n FROM orders"))["n"] == 500
