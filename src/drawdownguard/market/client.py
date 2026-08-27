"""The account, as the broker sees it.

WHY `equity` AND NEVER `buying_power`
-------------------------------------
Alpaca reports both. On the paper account they differ by a factor of four:
equity 1,000,000 against buying power 4,000,000, with `multiplier: 4`. That is
margin, and margin is not capital.

Every percentage the risk gate enforces — `max_position_pct`,
`max_deployed_pct` — divides by `Portfolio.equity`. Reading `buying_power`
instead would quadruple every position while every limit still reported itself
as satisfied. Nothing in the output would look wrong; the account would simply
be four times more leveraged than the configuration says it is.

So `buying_power` is not read here at all. Not read and discarded — never
fetched into a variable that something could later reach for by mistake.

GREEKS ARE COMPUTED, NOT ASKED FOR
-----------------------------------
`Portfolio.net_delta` and `Portfolio.vega` are recomputed from each held
contract's implied volatility with the same Black-Scholes code that prices the
backtest, rather than taken from Alpaca's greeks. The two agree — see
docs/notes/greeks-crosscheck.md — but agreeing today is not the same as being
the same number, and the gate must not be able to disagree with the backtest
about what a delta is.

A portfolio whose greeks were never filled in would report `net_delta` 0.0 and
`vega` 0.0, and would sail through both limits. `build_portfolio` therefore
computes them as part of construction; there is no intermediate object with
the fields left at zero for a caller to forget about.
"""

from decimal import Decimal
from typing import Any

from drawdownguard.domain import SHARES_PER_CONTRACT, Portfolio, Position
from drawdownguard.execution.reconcile import reconcile
from drawdownguard.mcp.alpaca_client import FULL_TOOLSETS, alpaca_session
from drawdownguard.options.payoff import bs_delta, contract_vega


def _money(value: Any) -> Decimal:
    """Alpaca reports money as a string. Via str so a float cannot creep in."""
    return Decimal(str(value))


async def _read(session, tool: str, args: dict | None = None) -> Any:
    from drawdownguard.mcp.alpaca_client import _unwrap

    return _unwrap(await session.call_tool(tool, args or {}), tool)["data"]


def position_greeks(
    positions: dict[str, Position],
    spots: dict[str, float],
    vols: dict[str, float],
    as_of_tau: dict[str, float],
) -> tuple[float, float, float]:
    """Net delta in shares, directional exposure in dollars, and vega.

    Sign conventions, both of which are easy to get backwards:

    *Delta.* A position's delta is `contracts * per_share_delta * 100`, with
    `contracts` negative for a short. Selling four puts at −0.30 gives
    `-4 * -0.30 * 100 = +120`: short puts are long the underlying, which is the
    whole reason the position is a bullish strategy.

    *Vega.* `Portfolio.vega` is *dollars lost* per one point rise in implied
    volatility, so it carries the opposite sign to the position's own vega. We
    write options, so we are short vega, so a rise costs money and the figure
    is positive. Four short contracts at 40 vega each report 160.
    """
    net_delta = 0.0
    net_delta_value = 0.0
    vega = 0.0

    for symbol, position in positions.items():
        net_delta += float(position.shares)
        spot = spots.get(symbol)
        if spot is not None:
            net_delta_value += float(position.shares) * spot
        for contract in position.contracts:
            key = contract.occ_symbol
            vol, tau = vols.get(key), as_of_tau.get(key)
            if spot is None or not vol or not tau or tau <= 0:
                continue
            strike = float(contract.strike)
            per_share = bs_delta(spot, strike, tau, vol, contract.right)
            contribution = contract.contracts * per_share * SHARES_PER_CONTRACT
            net_delta += contribution
            net_delta_value += contribution * spot
            vega -= contract.contracts * contract_vega(spot, strike, tau, vol)

    return net_delta, net_delta_value, vega


async def get_positions() -> list[dict[str, Any]]:
    """Every open position, shares and options alike, as Alpaca reports them."""
    async with alpaca_session(FULL_TOOLSETS) as session:
        data = await _read(session, "get_all_positions")
    return data.get("result") or []


async def get_spot(symbol: str) -> float:
    """Mid of the latest quote on the underlying."""
    async with alpaca_session(FULL_TOOLSETS) as session:
        data = await _read(session, "get_stock_latest_quote", {"symbols": symbol})
    quote = data["quotes"][symbol]
    return (float(quote["bp"]) + float(quote["ap"])) / 2


async def get_account(
    local_positions: dict[str, Position] | None = None,
    peak_equity: Decimal | None = None,
) -> tuple[Portfolio, list[str]]:
    """The broker's account folded into a `Portfolio`, plus any corrections.

    `local_positions` is what this agent believed before asking. The broker is
    authoritative, so the two are run through `reconcile`, and every correction
    comes back as a sentence meant to be journalled — a position that appeared
    without the agent opening it is exactly the event nobody should be able to
    discover only by reading a balance.

    `peak_equity` cannot come from the broker: Alpaca has no idea what this
    strategy's high-water mark is, and the drawdown kill-switch is measured
    against it. Passed in from stored state, and defaulting to today's equity
    only on the very first run, when no drawdown exists yet by definition.
    """
    async with alpaca_session(FULL_TOOLSETS) as session:
        account = await _read(session, "get_account_info")
        positions = (await _read(session, "get_all_positions")).get("result") or []

        positions, discrepancies = reconcile(local_positions or {}, positions)

        # Greeks need a spot per underlying and an implied volatility per held
        # contract. Fetched only when something is actually held.
        held = [c.occ_symbol for w in positions.values() for c in w.contracts]
        spots: dict[str, float] = {}
        vols: dict[str, float] = {}
        taus: dict[str, float] = {}

        if held:
            for symbol in positions:
                if positions[symbol].contracts:
                    quote = (
                        await _read(
                            session, "get_stock_latest_quote", {"symbols": symbol}
                        )
                    )["quotes"][symbol]
                    spots[symbol] = (float(quote["bp"]) + float(quote["ap"])) / 2

            snaps = (
                await _read(session, "get_option_snapshot", {"symbols": ",".join(held)})
            ).get("snapshots") or {}
            from datetime import date

            today = date.today()
            for position in positions.values():
                for contract in position.contracts:
                    snap = snaps.get(contract.occ_symbol) or {}
                    iv = snap.get("impliedVolatility")
                    if iv:
                        vols[contract.occ_symbol] = float(iv)
                    days = (contract.expiry - today).days
                    taus[contract.occ_symbol] = days / 365.0 if days > 0 else 0.0

    net_delta, net_delta_value, vega = position_greeks(positions, spots, vols, taus)

    equity = _money(account["equity"])
    deployed = sum(
        (c.notional for w in positions.values() for c in w.contracts if c.is_short),
        Decimal("0"),
    )

    portfolio = Portfolio(
        equity=equity,
        cash=_money(account["cash"]),
        peak_equity=max(peak_equity or equity, equity),
        deployed=deployed,
        net_delta=net_delta,
        net_delta_value=net_delta_value,
        vega=vega,
        positions=positions,
    )
    return portfolio, discrepancies
