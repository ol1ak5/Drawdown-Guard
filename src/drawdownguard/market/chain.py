"""The option chain adapter: Alpaca's shape in, the optimizer's shape out.

This module is the only place that knows what an Alpaca chain response looks
like. Everything downstream sees plain dicts with the keys Task 6 fixed:
`occ_symbol, strike, expiry, bid, ask, open_interest, implied_vol`.

TWO ENDPOINTS, NOT ONE
----------------------
`get_option_chain` carries quotes, greeks and implied volatility. It does not
carry open interest. Open interest lives on `get_option_contracts`, which knows
nothing about quotes. The risk gate needs both, so `load_chain` reads both and
joins them on the OCC symbol.

This is the reverse of the backtest's situation, where open interest could not
be obtained at all and `min_open_interest` had to be disabled and recorded in
`DISABLED_CHECKS`. Live, the check is enforceable. It just costs a second call.

BOTH ENDPOINTS PAGINATE
-----------------------
The chain returns 100 contracts per page. A single SPY expiry runs to well over
a thousand. Code that reads `snapshots` once and stops has not read the chain,
it has read an arbitrary hundredth of it — and it will look like it worked,
because a hundred contracts is plenty to produce a plausible-looking answer.

IMPLIED VOLATILITY IS PREFERRED, NEVER INVENTED
-----------------------------------------------
Alpaca supplies `impliedVolatility` for about two thirds of contracts. Where it
is missing, the volatility is solved from the mid price with the same
`implied_vol` the backtest uses. It is never replaced by realised volatility:
the whole variance-risk-premium argument is the gap between the two, and
substituting one for the other would erase the thing being measured and leave
the number looking entirely reasonable.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from drawdownguard.backtest.engine import implied_vol
from drawdownguard.domain import Right
from drawdownguard.execution.reconcile import parse_occ
from drawdownguard.mcp.alpaca_client import FULL_TOOLSETS, alpaca_session, call_tool

# The chain endpoint's page size. Named so the pagination loop reads as
# deliberate rather than as a magic number someone might tidy away.
CHAIN_PAGE_SIZE = 100

# Alpaca quotes greeks per share, and vega per share per point of volatility.
# Measured on 2026-08-24 across 781 SPY contracts: our textbook `bs_vega`
# divided by Alpaca's came to a median of 101.3, and our delta matched theirs
# to within 0.0005. See docs/notes/greeks-crosscheck.md.
#
# Nothing here consumes Alpaca's greeks. They are recomputed from the implied
# volatility instead, so that the same Black-Scholes code prices the backtest
# and the live chain. The constant exists to document the comparison, not to
# perform a conversion.
ALPACA_VEGA_PER_POINT = True


def _decimal(value: Any) -> Decimal:
    """Money as Decimal, via str so a float's binary error is not inherited."""
    return Decimal(str(value))


def adapt_chain_row(
    occ_symbol: str, snapshot: dict[str, Any], open_interest: int
) -> dict[str, Any] | None:
    """One Alpaca snapshot into one optimizer chain row, or None.

    None means the contract is not a choice: no quote, no bid or ask, or a
    price no volatility can reproduce. Returning None rather than a row with
    zeroes in it matters — a zero bid would pass through the optimizer as a
    contract that pays nothing, which is a different claim from a contract that
    cannot be traded.

    `occ_symbol` is a separate argument because Alpaca returns the symbol as
    the dictionary key, not as a field inside the snapshot.
    """
    meta = parse_occ(occ_symbol)
    if meta is None:
        return None

    quote = snapshot.get("latestQuote") or {}
    bid, ask = quote.get("bp"), quote.get("ap")
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None

    vol = snapshot.get("impliedVolatility")
    if not vol:
        # No IV from the vendor. Solve it from the mid rather than reach for
        # realised volatility, which would answer a different question.
        mid = (float(bid) + float(ask)) / 2
        spot = (snapshot.get("underlyingPrice") or 0) or None
        if spot is None:
            return None
        tau = (meta["expiry"] - date.today()).days / 365.0
        if tau <= 0:
            return None
        vol = implied_vol(mid, float(spot), float(meta["strike"]), tau, meta["right"])
        if vol is None:
            return None

    return {
        "occ_symbol": occ_symbol,
        "strike": meta["strike"],
        "expiry": meta["expiry"],
        "right": meta["right"],
        "bid": _decimal(bid),
        "ask": _decimal(ask),
        "open_interest": int(open_interest),
        "implied_vol": float(vol),
    }


async def _paged(session, tool: str, args: dict, key: str) -> list | dict:
    """Drain every page of a paginated tool.

    Both endpoints signal the end with a null `next_page_token`, and both will
    happily hand back a first page to a caller who never asks for the second.
    """
    out: Any = None
    token = None
    while True:
        page_args = dict(args)
        if token:
            page_args["page_token"] = token
        result = await session.call_tool(tool, page_args)
        from drawdownguard.mcp.alpaca_client import _unwrap

        data = _unwrap(result, tool)["data"]
        chunk = data.get(key)
        if isinstance(chunk, dict):
            out = {**(out or {}), **chunk}
        elif isinstance(chunk, list):
            out = (out or []) + chunk
        token = data.get("next_page_token")
        if not token:
            return out if out is not None else ({} if key == "snapshots" else [])


async def load_chain(
    symbol: str,
    right: Right,
    min_dte: int,
    max_dte: int,
    as_of: date | None = None,
) -> list[dict[str, Any]]:
    """Every tradable contract for `symbol` inside the DTE window.

    The window is applied server-side, so the pagination cost is proportional
    to what is actually wanted rather than to the whole chain.
    """
    as_of = as_of or date.today()
    gte = as_of.fromordinal(as_of.toordinal() + min_dte).isoformat()
    lte = as_of.fromordinal(as_of.toordinal() + max_dte).isoformat()
    kind = "put" if right == "P" else "call"

    async with alpaca_session(FULL_TOOLSETS) as session:
        snapshots = await _paged(
            session,
            "get_option_chain",
            {
                "underlying_symbol": symbol,
                "expiration_date_gte": gte,
                "expiration_date_lte": lte,
                "type": kind,
            },
            "snapshots",
        )
        contracts = await _paged(
            session,
            "get_option_contracts",
            {
                "underlying_symbols": symbol,
                "expiration_date_gte": gte,
                "expiration_date_lte": lte,
                "type": kind,
                "limit": 500,
            },
            "option_contracts",
        )

    # Contracts absent from the open-interest response default to zero, which
    # the gate reads as illiquid and refuses. Failing closed is the right way
    # round: an unknown open interest is not evidence of a healthy market.
    interest = {c["symbol"]: int(c.get("open_interest") or 0) for c in contracts}

    rows = []
    for occ, snapshot in (snapshots or {}).items():
        row = adapt_chain_row(occ, snapshot, interest.get(occ, 0))
        if row is not None:
            rows.append(row)
    return rows


async def get_spot(symbol: str) -> float:
    """Mid of the latest quote on the underlying."""
    payload = await call_tool("get_stock_latest_quote", {"symbols": symbol})
    quote = payload["data"]["quotes"][symbol]
    return (float(quote["bp"]) + float(quote["ap"])) / 2
