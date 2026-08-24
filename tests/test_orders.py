"""The barrier between a proposed order and the broker.

Every test here is about one claim: nothing reaches Alpaca without the risk
gate approving it first. The broker is a mock throughout — a test that places
real orders to prove it does not place real orders is not a test.
"""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

from flywheel.execution.orders import _order_arguments, submit_order
from tests.test_risk_gate import LIMITS, portfolio
from tests.test_risk_gate import order as make_order

WRAPPED = {
    "_alpaca_mcp_security": {"trust": "untrusted_tool_output"},
    "data": {"id": "abc-123", "status": "accepted"},
}


async def test_a_rejected_order_never_reaches_the_broker():
    """The whole point. A naked call is refused before the network is touched."""
    naked = make_order(right="C")  # no shares held
    with patch("flywheel.execution.orders.call_tool", new=AsyncMock()) as broker:
        result = await submit_order(naked, portfolio(), LIMITS)
    broker.assert_not_awaited()
    assert result.submitted is False
    assert "naked" in result.reason.lower()


async def test_an_approved_order_reaches_the_broker_once():
    with patch(
        "flywheel.execution.orders.call_tool", new=AsyncMock(return_value=WRAPPED)
    ) as broker:
        result = await submit_order(make_order(), portfolio(), LIMITS)
    assert broker.await_count == 1
    assert result.submitted is True
    assert result.broker_order_id == "abc-123"


async def test_dry_run_never_reaches_the_broker():
    with patch("flywheel.execution.orders.call_tool", new=AsyncMock()) as broker:
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
    with patch("flywheel.execution.orders.call_tool", new=AsyncMock()) as broker:
        result = await submit_order(
            make_order(right="C"), portfolio(), LIMITS, dry_run=True
        )
    broker.assert_not_awaited()
    assert "naked" in result.reason.lower()
    assert "dry run" not in result.reason.lower()


async def test_a_broker_failure_is_reported_not_raised():
    """Unattended on a schedule, an exception is a silently dead agent."""
    with patch(
        "flywheel.execution.orders.call_tool",
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
        "flywheel.execution.orders.call_tool",
        new=AsyncMock(side_effect=RuntimeError("timeout")),
    ):
        result = await submit_order(make_order(), portfolio(), LIMITS)
    assert result.client_order_id
    assert result.client_order_id.startswith("flywheel-")


async def test_every_submission_carries_a_distinct_idempotency_key():
    with patch(
        "flywheel.execution.orders.call_tool", new=AsyncMock(return_value=WRAPPED)
    ):
        first = await submit_order(make_order(), portfolio(), LIMITS)
        second = await submit_order(make_order(), portfolio(), LIMITS)
    assert first.client_order_id != second.client_order_id


# --- the payload ------------------------------------------------------------


def arguments(**overrides):
    return _order_arguments(make_order(**overrides), "flywheel-test")


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
    wheel does both.
    """
    args = arguments(contracts=-2)
    assert args["side"] == "sell"
    assert args["position_intent"] == "sell_to_open"
    assert args["qty"] == "2"


def test_buying_to_close_is_recognised_as_the_other_direction():
    args = arguments(contracts=2)
    assert args["side"] == "buy"
    assert args["position_intent"] == "buy_to_close"


def test_options_are_day_orders_because_nothing_else_is_accepted():
    assert arguments()["time_in_force"] == "day"


def test_the_symbol_is_a_well_formed_occ_symbol():
    from flywheel.execution.reconcile import parse_occ

    decoded = parse_occ(arguments()["symbol"])
    assert decoded is not None
    assert decoded["strike"] == Decimal("560")
    assert decoded["right"] == "P"
