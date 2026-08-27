"""Sending an order, and the barrier it has to clear first.

This is the primary risk barrier. `submit_order` calls `veto` before it does
anything else, and there is no argument, flag, environment variable or
configuration that skips it. `dry_run` does not skip the gate — it is checked
*after* the gate, so a dry run of a forbidden order still reports the refusal
rather than reporting that nothing was attempted.

That ordering is deliberate. If `dry_run` short-circuited first, the safest
mode would be the one that told you least about whether the trade was allowed.

LIMIT ORDERS, NEVER MARKET
--------------------------
Options spreads are wide, and a market order on a wide spread is how a bot
donates its premium one contract at a time. Everything here is a limit order at
the price the optimizer decided on.

FAILURES ARE RETURNED, NOT RAISED
----------------------------------
The broker call is wrapped and any exception comes back as an `OrderResult`
with `submitted=False`. This runs unattended on a schedule: an unhandled
exception at ten in the morning is a silently dead agent, and a dead agent that
holds short options is worse than one that never opened them.
"""

import uuid
from decimal import Decimal

from pydantic import BaseModel

from drawdownguard.domain import Portfolio, ProposedOrder
from drawdownguard.mcp.alpaca_client import call_tool
from drawdownguard.risk.gate import closes_a_short, veto
from drawdownguard.risk.limits import Limits

# From docs/notes/mcp-tools.md, read off the running server. Options accept no
# time in force other than "day".
ORDER_TOOL = "place_option_order"
TIME_IN_FORCE = "day"


class OrderResult(BaseModel):
    submitted: bool
    reason: str
    occ_symbol: str
    broker_order_id: str | None = None
    client_order_id: str | None = None


def _occ_symbol(order: ProposedOrder) -> str:
    """Rebuild the OCC symbol the broker expects."""
    from drawdownguard.execution.reconcile import occ_symbol

    return occ_symbol(order.symbol, order.expiry, order.right, order.strike)


def _position_intent(order: ProposedOrder, portfolio: Portfolio | None) -> str:
    """Which of the four things this order is, in the broker's vocabulary.

    A buy is not one action. Buying back a short the account already carries
    closes a position; buying a protective put opens one. The two are the same
    symbol and the same side, and only the book can tell them apart -- so the
    book is asked, exactly as the risk gate asks it.

    This used to read `buy_to_close` for every purchase, which was true while
    the only thing the agent ever bought was its own short. A protective put
    sent that way asks the broker to close a position that does not exist.
    """
    if order.contracts < 0:
        return "sell_to_open"
    if portfolio is not None and closes_a_short(order, portfolio):
        return "buy_to_close"
    return "buy_to_open"


def _order_arguments(
    order: ProposedOrder,
    client_order_id: str,
    portfolio: Portfolio | None = None,
) -> dict:
    """The broker payload, in the exact shapes the server documented.

    `qty` and `limit_price` are strings because that is what the tool schema
    asks for. Passing a float would be quietly rounded somewhere in the stack,
    and a limit price rounded in the wrong direction is a worse fill on every
    contract.

    `position_intent` is always sent. The schema calls it optional, but without
    it the broker has to infer whether this opens or closes a position, and
    this agent does both — writing a put, buying one back, and buying one to
    hold are three different orders wearing the same symbol.
    """
    return {
        "symbol": _occ_symbol(order),
        "qty": str(abs(order.contracts)),
        "side": "sell" if order.contracts < 0 else "buy",
        "position_intent": _position_intent(order, portfolio),
        "type": "limit",
        "limit_price": f"{order.limit_price:.2f}",
        "time_in_force": TIME_IN_FORCE,
        "client_order_id": client_order_id,
    }


def _broker_order_id(payload: object) -> str | None:
    """Dig the broker's order id out of whatever shape came back.

    Every Alpaca MCP response is wrapped in `{_alpaca_mcp_security, data}`, but
    this also accepts a bare object so a caller holding an already-unwrapped
    response is not silently told the order failed.
    """
    if not isinstance(payload, dict):
        return None
    body = payload.get("data", payload)
    if isinstance(body, dict):
        found = body.get("id") or body.get("order_id")
        return str(found) if found is not None else None
    return None


async def submit_order(
    order: ProposedOrder,
    portfolio: Portfolio,
    limits: Limits,
    dry_run: bool = False,
) -> OrderResult:
    """Put one order past the gate and, if it survives, to the broker.

    The gate runs first and unconditionally. Nothing below it can be reached by
    an order it refused.
    """
    occ = _occ_symbol(order)

    verdict = veto(order, portfolio, limits)
    if not verdict.approved:
        return OrderResult(submitted=False, reason=verdict.reason, occ_symbol=occ)

    if dry_run:
        return OrderResult(
            submitted=False, reason="dry run: not submitted", occ_symbol=occ
        )

    # A fresh idempotency key per attempt. The broker rejects duplicates, so a
    # request that times out after the order landed can be retried with the
    # same key without opening a second position — which is the one question a
    # reconciling agent must never have to guess at.
    client_order_id = f"drawdownguard-{uuid.uuid4()}"

    try:
        payload = await call_tool(
            ORDER_TOOL, _order_arguments(order, client_order_id, portfolio)
        )
    except Exception as exc:  # noqa: BLE001 — a dead agent is worse than a logged one
        return OrderResult(
            submitted=False,
            reason=f"broker call failed: {exc}",
            occ_symbol=occ,
            client_order_id=client_order_id,
        )

    return OrderResult(
        submitted=True,
        reason="submitted",
        occ_symbol=occ,
        broker_order_id=_broker_order_id(payload),
        client_order_id=client_order_id,
    )


def limit_price_of(order: ProposedOrder) -> Decimal:
    """The price actually sent, rounded as the broker will see it."""
    return Decimal(f"{order.limit_price:.2f}")
