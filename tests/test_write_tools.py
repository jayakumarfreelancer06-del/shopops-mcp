import pytest

from shopops.db import execute
from shopops.tools import read, write


async def test_create_support_ticket():
    t = await write.create_support_ticket(1, "Test subject", "Test body", priority="high")
    assert t["status"] == "open" and t["priority"] == "high"
    found = await read.search_tickets("Test subject")
    assert found.items[0]["source"] == "mcp"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"customer_id": 999_999, "subject": "s", "body": "b"}, "does not exist"),
        ({"customer_id": 1, "subject": " ", "body": "b"}, "must not be empty"),
        ({"customer_id": 1, "subject": "s", "body": "b", "order_id": 999_999}, "does not belong"),
    ],
)
async def test_create_support_ticket_validation(kwargs, message):
    with pytest.raises(write.ToolInputError, match=message):
        await write.create_support_ticket(**kwargs)


async def _paid_order() -> int:
    return (await read.list_orders(status=["paid"], limit=1)).items[0]["id"]


async def test_update_order_status_requires_preview_then_confirmation():
    order_id = await _paid_order()
    preview = await write.update_order_status(order_id, "shipped", principal="alice")
    assert preview["requires_confirmation"] is True
    assert preview["preview"]["current_status"] == "paid" and preview["preview"]["new_status"] == "shipped"
    assert (await read.get_order(order_id))["status"] == "paid"  # nothing changed yet

    done = await write.update_order_status(order_id, "shipped", principal="alice", confirm_token=preview["confirm_token"])
    assert done == {**done, "applied": True, "previous_status": "paid", "status": "shipped"}
    assert (await read.get_order(order_id))["status"] == "shipped"


async def test_confirm_token_is_single_use():
    order_id = await _paid_order()
    token = (await write.update_order_status(order_id, "cancelled", principal="alice"))["confirm_token"]
    await write.update_order_status(order_id, "cancelled", principal="alice", confirm_token=token)
    with pytest.raises(write.ToolInputError):
        await write.update_order_status(order_id, "cancelled", principal="alice", confirm_token=token)


async def test_confirm_token_is_bound_to_caller_order_and_status():
    order_id = await _paid_order()
    token = (await write.update_order_status(order_id, "shipped", principal="alice"))["confirm_token"]
    with pytest.raises(write.ToolInputError, match="confirm_token"):
        await write.update_order_status(order_id, "shipped", principal="mallory", confirm_token=token)
    with pytest.raises(write.ToolInputError, match="confirm_token"):
        await write.update_order_status(order_id, "refunded", principal="alice", confirm_token=token)
    with pytest.raises(write.ToolInputError, match="confirm_token"):
        await write.update_order_status(order_id, "shipped", principal="alice", confirm_token="cfm_made_up")


async def test_expired_token_is_rejected():
    order_id = await _paid_order()
    token = (await write.update_order_status(order_id, "shipped", principal="alice"))["confirm_token"]
    await execute("app", "UPDATE app.pending_confirmations SET expires_at = now() - interval '1 second' WHERE used_at IS NULL")
    with pytest.raises(write.ToolInputError, match="expired"):
        await write.update_order_status(order_id, "shipped", principal="alice", confirm_token=token)


async def test_change_after_preview_is_not_applied():
    order_id = await _paid_order()
    token = (await write.update_order_status(order_id, "shipped", principal="alice"))["confirm_token"]
    await execute("admin", "UPDATE orders SET status = 'cancelled' WHERE id = :id", id=order_id)  # someone else acted
    with pytest.raises(write.ToolInputError):
        await write.update_order_status(order_id, "shipped", principal="alice", confirm_token=token)
    assert (await read.get_order(order_id))["status"] == "cancelled"


@pytest.mark.parametrize("target", ["pending", "delivered"])
async def test_invalid_transitions_are_rejected(target):
    order_id = await _paid_order()
    with pytest.raises(write.ToolInputError, match="cannot change"):
        await write.update_order_status(order_id, target, principal="alice")


async def test_unknown_order():
    with pytest.raises(write.ToolInputError, match="does not exist"):
        await write.update_order_status(999_999, "shipped", principal="alice")
