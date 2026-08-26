"""Buy the client portfolio the agent is hired to protect.

Run once, before the agent starts. Everything after this is an overlay on what
this script establishes.

WHY THE PORTFOLIO EXISTS AT ALL
-------------------------------
Until now the agent traded from cash and held almost nothing, which made every
risk number it reported meaningless: a book with no exposure cannot draw down.
A maximum drawdown of 0.11% was not discipline, it was an absence.

The mandate is a promise about losses. A promise about losses needs something
that can lose.

HOW THE SIZE WAS CHOSEN
-----------------------
Not by taste. The mandate fixes it.

A downside budget of 10% on 1,000,000 is 100,000. Under a 20% equity shock,
exposure E loses 0.2E, so the largest exposure that honours the promise with no
protection at all is:

    0.2 * E <= 100,000   ->   E <= 500,000

600,000 is deliberately above that line. At -20% it produces a 20,000 gap the
agent has to close, and at -10% no gap at all. A portfolio that never breaches
gives the agent nothing to do; one that always breaches makes the mandate
theatre. This one breaches where hedging is actually the question.

WHY MARKET ORDERS HERE, HAVING BANNED THEM FOR OPTIONS
-------------------------------------------------------
`execution/orders.py` refuses to send a market order, because an options spread
is wide and crossing it repeatedly is how a bot donates its premium. These are
not options. Measured at the time of writing: SPY 1.6 bp, QQQ 1.1 bp, IWM
8.7 bp, BIL 1.1 bp. Crossing a spread of one hundredth of a percent once, to
establish a position held for months, is not the same act.

BIL IS BALLAST, NOT A HEDGE
---------------------------
Short-duration Treasury bills sit where capital waits. They are not protection
and this file will not call them protection: in 2022 long-duration Treasuries
fell alongside equities, and a portfolio that treats bonds as insurance
discovers the correlation at the worst moment. Only the puts are protection.
BIL is here because idle cash in an Alpaca paper account earns nothing, while
the backtest assumes collateral earns the bill rate — the ETF closes that gap
between what is modelled and what is lived.
"""

import argparse
import asyncio
from decimal import Decimal

from flywheel.journal import writer
from flywheel.mcp.alpaca_client import FULL_TOOLSETS, _unwrap, alpaca_session

# Target dollars per instrument. Equity totals 600,000 — see the docstring.
TARGET = {
    "SPY": Decimal("300000"),
    "QQQ": Decimal("150000"),
    "IWM": Decimal("150000"),
    "BIL": Decimal("250000"),
}

# What each holding is for. Carried into the journal so the roles are recorded
# with the position rather than living only in someone's memory.
ROLE = {
    "SPY": "equity exposure",
    "QQQ": "equity exposure",
    "IWM": "equity exposure",
    "BIL": "ballast — not protection",
}


async def quote(session, symbol: str) -> float:
    data = _unwrap(
        await session.call_tool("get_stock_latest_quote", {"symbols": symbol}), "q"
    )["data"]
    q = data["quotes"][symbol]
    return (float(q["bp"]) + float(q["ap"])) / 2


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually place the orders; without this nothing is sent",
    )
    args = parser.parse_args()

    async with alpaca_session(FULL_TOOLSETS) as session:
        account = _unwrap(await session.call_tool("get_account_info", {}), "a")["data"]
        equity = Decimal(str(account["equity"]))
        cash = Decimal(str(account["cash"]))

        positions = (
            (
                _unwrap(await session.call_tool("get_all_positions", {}), "p")["data"]
            ).get("result")
            or []
        )
        if positions:
            print(f"refusing: the account already holds {len(positions)} positions.")
            print("This script establishes a portfolio; it does not adjust one.")
            for p in positions:
                print(f"  {p.get('symbol')}  qty {p.get('qty')}")
            return 1

        clock = _unwrap(await session.call_tool("get_clock", {}), "c")["data"]
        if not clock.get("is_open"):
            print(f"market closed; next open {clock.get('next_open')}")
            if args.execute:
                print("refusing to send orders into a closed market.")
                return 1

        print(f"equity {equity:,.0f}   cash {cash:,.0f}\n")
        print(f"{'symbol':<7}{'role':<26}{'price':>9}{'shares':>9}{'value':>13}")
        print("-" * 64)

        plan = []
        spent = Decimal("0")
        for symbol, target in TARGET.items():
            price = await quote(session, symbol)
            shares = int(target / Decimal(str(price)))
            value = Decimal(str(price)) * shares
            spent += value
            plan.append((symbol, shares, price, value))
            print(
                f"{symbol:<7}{ROLE[symbol]:<26}{price:>9.2f}{shares:>9}{value:>13,.0f}"
            )

        print("-" * 64)
        remaining = cash - spent
        equity_value = sum(v for s, _, _, v in plan if s != "BIL")
        print(f"{'':<42}{'deployed':>9}{spent:>13,.0f}")
        print(f"{'':<42}{'cash left':>9}{remaining:>13,.0f}")
        print(
            f"\nequity exposure {equity_value:,.0f} "
            f"({equity_value / equity * 100:.1f}% of capital)"
        )

        # What the mandate implies, printed so the choice is visible rather
        # than buried in a config file.
        budget = equity * Decimal("0.10")
        loss_at_20 = equity_value * Decimal("0.20")
        gap = max(loss_at_20 - budget, Decimal("0"))
        print(
            f"downside budget {budget:,.0f}  "
            f"loss at -20% {loss_at_20:,.0f}  gap {gap:,.0f}"
        )

        if not args.execute:
            print("\ndry run — nothing sent. Re-run with --execute to place orders.")
            return 0

        print()
        for symbol, shares, price, value in plan:
            try:
                result = _unwrap(
                    await session.call_tool(
                        "place_stock_order",
                        {
                            "symbol": symbol,
                            "side": "buy",
                            "qty": str(shares),
                            "type": "market",
                            "time_in_force": "day",
                        },
                    ),
                    "place_stock_order",
                )
                order_id = (result.get("data") or {}).get("id")
                print(f"  {symbol}: {shares} shares submitted, id {order_id}")
                writer.write(
                    "portfolio.established",
                    {
                        "symbol": symbol,
                        "role": ROLE[symbol],
                        "shares": shares,
                        "reference_price": price,
                        "value": str(value),
                        "broker_order_id": order_id,
                    },
                    severity="info",
                )
            except Exception as exc:  # noqa: BLE001 — report, do not abort the rest
                print(f"  {symbol}: FAILED {exc}")
                writer.write(
                    "portfolio.failed",
                    {"symbol": symbol, "shares": shares, "detail": str(exc)},
                    severity="info",
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
