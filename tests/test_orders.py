"""The barrier between a proposed order and the broker.

Every test here is about one claim: nothing reaches Alpaca without the risk
gate approving it first. The broker is a mock throughout — a test that places
real orders to prove it does not place real orders is not a test.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from drawdownguard.execution.orders import (
    OrderResult,
    _order_arguments,
    confirm,
    submit_order,
)
from tests.test_risk_gate import LIMITS, portfolio
from tests.test_risk_gate import order as make_order

WRAPPED = {
    "_alpaca_mcp_security": {"trust": "untrusted_tool_output"},
    "data": {"id": "abc-123", "status": "accepted"},
}


async def test_a_rejected_order_never_reaches_the_broker():
    """The whole point. A naked call is refused before the network is touched."""
    naked = make_order(right="C")  # no shares held
    with patch("drawdownguard.execution.orders.call_tool", new=AsyncMock()) as broker:
        result = await submit_order(naked, portfolio(), LIMITS)
    broker.assert_not_awaited()
    assert result.submitted is False
    assert "naked" in result.reason.lower()


async def test_an_approved_order_reaches_the_broker_once():
    with patch(
        "drawdownguard.execution.orders.call_tool", new=AsyncMock(return_value=WRAPPED)
    ) as broker:
        result = await submit_order(make_order(), portfolio(), LIMITS)
    assert broker.await_count == 1
    assert result.submitted is True
    assert result.broker_order_id == "abc-123"


async def test_dry_run_never_reaches_the_broker():
    with patch("drawdownguard.execution.orders.call_tool", new=AsyncMock()) as broker:
        result = await submit_order(make_order(), portfolio(), LIMITS, dry_run=True)
    broker.assert_not_awaited()
    assert result.submitted is False
    assert "dry run" in result.reason.lower()


async def test_dry_run_still_reports_the_refusal_rather_than_hiding_it():
    """The safest mode must not be the one that tells you least.

    If dry_run short-circuited before the gate, a dry run of a forbidden order
    would report only that nothing was attempted, and the operator would learn
    nothing about whether the trade was allowed.
    """
    with patch("drawdownguard.execution.orders.call_tool", new=AsyncMock()) as broker:
        result = await submit_order(
            make_order(right="C"), portfolio(), LIMITS, dry_run=True
        )
    broker.assert_not_awaited()
    assert "naked" in result.reason.lower()
    assert "dry run" not in result.reason.lower()


async def test_a_broker_failure_is_reported_not_raised():
    """Unattended on a schedule, an exception is a silently dead agent."""
    with patch(
        "drawdownguard.execution.orders.call_tool",
        new=AsyncMock(side_effect=RuntimeError("connection reset")),
    ):
        result = await submit_order(make_order(), portfolio(), LIMITS)
    assert result.submitted is False
    assert "connection reset" in result.reason


async def test_a_failed_submission_still_reports_its_idempotency_key():
    """Without it a timeout is unresolvable.

    If the request died after the order reached Alpaca, the only safe retry is
    one carrying the same client_order_id. Losing the key on the error path is
    how a retry becomes a second position.
    """
    with patch(
        "drawdownguard.execution.orders.call_tool",
        new=AsyncMock(side_effect=RuntimeError("timeout")),
    ):
        result = await submit_order(make_order(), portfolio(), LIMITS)
    assert result.client_order_id
    assert result.client_order_id.startswith("dg-")


async def test_the_same_order_twice_carries_the_same_key():
    """The retry is the point, and a random key made it impossible.

    The broker refuses a duplicate `client_order_id`, which is what stops a
    timed-out request from filling twice. A `uuid4` minted inside `submit_order`
    was new on every attempt, so the second send was a different order as far
    as Alpaca was concerned -- and because options are day orders, an unfilled
    limit appears in no position listing, so a re-run the same morning measures
    the same gap and sends the same hedge again.
    """
    with patch(
        "drawdownguard.execution.orders.call_tool", new=AsyncMock(return_value=WRAPPED)
    ):
        first = await submit_order(make_order(), portfolio(), LIMITS)
        second = await submit_order(make_order(), portfolio(), LIMITS)
    assert first.client_order_id == second.client_order_id


async def test_a_different_order_carries_a_different_key():
    """Same day, same symbol, different size is a different trade."""
    with patch(
        "drawdownguard.execution.orders.call_tool", new=AsyncMock(return_value=WRAPPED)
    ):
        one = await submit_order(make_order(contracts=-1), portfolio(), LIMITS)
        two = await submit_order(make_order(contracts=-2), portfolio(), LIMITS)
    assert one.client_order_id != two.client_order_id


# --- the payload ------------------------------------------------------------


def arguments(**overrides):
    return _order_arguments(make_order(**overrides), "drawdownguard-test")


def test_the_order_is_a_limit_never_a_market_order():
    """A market order on a wide options spread donates the premium."""
    args = arguments()
    assert args["type"] == "limit"
    assert args["limit_price"] == "2.35"


def test_quantity_and_price_are_strings_as_the_schema_asks():
    """A float would be rounded somewhere in the stack, silently."""
    args = arguments()
    assert isinstance(args["qty"], str)
    assert isinstance(args["limit_price"], str)


def test_selling_to_open_is_stated_not_left_to_be_inferred():
    """Writing a put and buying one back are the same symbol.

    Without position_intent the broker has to guess which one this is, and the
    position does both.
    """
    args = arguments(contracts=-2)
    assert args["side"] == "sell"
    assert args["position_intent"] == "sell_to_open"
    assert args["qty"] == "2"


def test_a_buy_is_not_one_action_and_the_book_decides_which():
    """`position_intent` used to read `buy_to_close` for every purchase, which
    held while the only thing the agent ever bought was its own short. A
    protective put sent that way asks the broker to close a position that does
    not exist, so the book is consulted -- exactly as the risk gate consults it.
    """
    from datetime import date

    from drawdownguard.domain import OpenContract, Position

    short = OpenContract(
        occ_symbol="SPY260828P00560000",
        right="P",
        strike=Decimal("560"),
        expiry=date(2026, 8, 28),
        contracts=-2,
        premium=Decimal("2.35"),
    )
    owns_the_short = portfolio(
        positions={"SPY": Position(symbol="SPY", contracts=[short])}
    )
    closing = _order_arguments(make_order(contracts=2), "id", owns_the_short)
    assert closing["side"] == "buy"
    assert closing["position_intent"] == "buy_to_close"

    opening = _order_arguments(make_order(contracts=2), "id", portfolio())
    assert opening["side"] == "buy"
    assert opening["position_intent"] == "buy_to_open"


def test_options_are_day_orders_because_nothing_else_is_accepted():
    assert arguments()["time_in_force"] == "day"


def test_the_symbol_is_a_well_formed_occ_symbol():
    from drawdownguard.execution.reconcile import parse_occ

    decoded = parse_occ(arguments()["symbol"])
    assert decoded is not None
    assert decoded["strike"] == Decimal("560")
    assert decoded["right"] == "P"


def test_a_sale_is_not_one_action_either_and_the_book_decides_which():
    """The mirror of the buy-side case above, which was fixed alone.

    Writing a put opens a short; handing back a protective put the account
    owns closes a long. Sent as `sell_to_open`, a handback asks the broker to
    open a fresh naked short and leaves the original long in place -- twice the
    position, and no protection given back.
    """
    from datetime import date

    from drawdownguard.domain import OpenContract, Position

    owned = OpenContract(
        occ_symbol="SPY260828P00560000",
        right="P",
        strike=Decimal("560"),
        expiry=date(2026, 8, 28),
        contracts=2,
        premium=Decimal("2.35"),
    )
    owns_the_put = portfolio(
        positions={"SPY": Position(symbol="SPY", contracts=[owned])}
    )
    handback = _order_arguments(make_order(contracts=-2), "id", owns_the_put)
    assert handback["side"] == "sell"
    assert handback["position_intent"] == "sell_to_close"

    writing = _order_arguments(make_order(contracts=-2), "id", portfolio())
    assert writing["side"] == "sell"
    assert writing["position_intent"] == "sell_to_open"


FILLED = {
    "_alpaca_mcp_security": {"trust": "untrusted_tool_output"},
    "data": {
        "id": "abc-123",
        "status": "filled",
        "qty": "2",
        "filled_qty": "2",
        "filled_avg_price": "2.41",
    },
}

WORKING = {
    "_alpaca_mcp_security": {"trust": "untrusted_tool_output"},
    "data": {
        "id": "abc-123",
        "status": "new",
        "qty": "2",
        "filled_qty": "0",
        "filled_avg_price": None,
    },
}


async def test_a_fill_is_read_back_rather_than_assumed():
    """`submitted` means the broker took it, which is not the same as bought."""
    accepted = OrderResult(
        submitted=True, reason="submitted", occ_symbol="SPY", client_order_id="k"
    )
    with patch(
        "drawdownguard.execution.orders.call_tool",
        new=AsyncMock(return_value=FILLED),
    ):
        done = await confirm(accepted)
    assert done.filled_qty == 2
    assert done.filled_avg_price == Decimal("2.41")
    assert done.broker_status == "filled"


async def test_an_accepted_order_that_did_not_fill_says_so():
    """The case that made this necessary.

    Two protective puts were accepted at limits set to the ask at the moment of
    the decision, the ask moved a few cents, and both sat unfilled until the
    close. The cycle reported `submitted: 2` and the account held no options.
    """
    accepted = OrderResult(
        submitted=True, reason="submitted", occ_symbol="SPY", client_order_id="k"
    )
    with patch(
        "drawdownguard.execution.orders.call_tool",
        new=AsyncMock(return_value=WORKING),
    ):
        done = await confirm(accepted)
    assert done.submitted is True, "the order was accepted; that part is true"
    assert done.filled_qty == 0, "and it bought nothing"
    assert done.broker_status == "new"


async def test_a_fill_that_cannot_be_read_is_not_downgraded_to_a_failure():
    """The order may well have filled. What is unknown is the outcome, and an
    unreadable answer must not be recorded as a refusal."""
    accepted = OrderResult(
        submitted=True, reason="submitted", occ_symbol="SPY", client_order_id="k"
    )
    with patch(
        "drawdownguard.execution.orders.call_tool",
        new=AsyncMock(side_effect=RuntimeError("gateway timeout")),
    ):
        done = await confirm(accepted)
    assert done.submitted is True
    assert done.approved is True
    assert "unread" in done.broker_status


async def test_nothing_is_read_back_for_an_order_that_never_went():
    """A refused or dry-run order has no broker state to ask about."""
    refused = OrderResult(
        submitted=False, reason="naked call", occ_symbol="SPY", approved=False
    )
    with patch("drawdownguard.execution.orders.call_tool", new=AsyncMock()) as broker:
        done = await confirm(refused)
    broker.assert_not_awaited()
    assert done.filled_qty == 0
    assert done.broker_status is None


@pytest.mark.asyncio
async def test_an_order_already_live_is_recognised_rather_than_sent_again():
    """The cycle runs every half hour and re-proposes the same unfilled put.

    Without this the broker refuses each one on the duplicate key -- the
    interlock working -- and the journal fills with twelve rejections that read
    like twelve failures.
    """
    from drawdownguard.execution.orders import already_working

    live = {"data": {"status": "new", "filled_qty": "0", "filled_avg_price": None}}
    with patch(
        "drawdownguard.execution.orders.call_tool", new=AsyncMock(return_value=live)
    ):
        standing = await already_working(make_order())
    assert standing is not None
    assert standing.broker_status == "new"
    assert standing.filled_qty == 0


@pytest.mark.asyncio
async def test_a_finished_order_does_not_block_the_rest_of_the_day():
    """Cancelled, expired and rejected are gone, and the day may try again.

    A filled one is not a blocker either: the position it created is what the
    next cycle measures, so nothing will ask for it a second time.
    """
    from drawdownguard.execution.orders import already_working

    for status in ("canceled", "expired", "rejected", "done_for_day"):
        with patch(
            "drawdownguard.execution.orders.call_tool",
            new=AsyncMock(return_value={"data": {"status": status}}),
        ):
            assert await already_working(make_order()) is None, status


@pytest.mark.asyncio
async def test_an_unreadable_order_is_treated_as_absent():
    """Sending again is safe -- the duplicate key still refuses a second fill.

    The cost of guessing wrong in this direction is the noisy journal this
    avoids, not a doubled position.
    """
    from drawdownguard.execution.orders import already_working

    with patch(
        "drawdownguard.execution.orders.call_tool",
        new=AsyncMock(side_effect=RuntimeError("not found")),
    ):
        assert await already_working(make_order()) is None
