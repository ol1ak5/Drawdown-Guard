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

800,000 is deliberately above that line. At -20% it produces a 60,000 gap the
agent has to close, and at -10% no gap at all. A portfolio that never breaches
gives the agent nothing to do; one that always breaches makes the mandate
theatre. This one breaches where hedging is actually the question.

It is also, deliberately, an allocation nobody would query. Eighty percent in
equities and twenty in reserve is what any adviser would sign, and that is the
argument: the promise is broken by an ordinary portfolio, not by one built to
break.

WHY MARKET ORDERS HERE, HAVING BANNED THEM FOR OPTIONS
-------------------------------------------------------
`execution/orders.py` refuses to send a market order, because an options spread
is wide and crossing it repeatedly is how a bot donates its premium. These are
not options. Measured at the time of writing: SPY 1.6 bp, QQQ 1.1 bp, IWM
8.7 bp, BIL 1.1 bp. Crossing a spread of one hundredth of a percent once, to
establish a position held for months, is not the same act.

WHEN THE AGENT ALREADY HAS AN OVERLAY OPEN
-------------------------------------------
The first version refused if the account held any position at all, which was
too blunt: it counted the agent's own short puts as an established portfolio
and blocked the very thing it exists to do. It now refuses only if a *target
holding* already exists, and otherwise reserves the collateral those puts
require and sizes around it.

The reduction comes out of BIL. The equity sleeve is sized by the mandate, and
shrinking it to make room would quietly shrink the protection gap as well --
the portfolio would stop demonstrating the thing it was built to demonstrate,
and the demo would look better for the wrong reason.

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

from drawdownguard.domain import SHARES_PER_CONTRACT
from drawdownguard.execution.reconcile import parse_occ
from drawdownguard.journal import writer
from drawdownguard.mcp.alpaca_client import FULL_TOOLSETS, _unwrap, alpaca_session

# Target dollars per instrument. Equity totals 800,000 — see the docstring.
#
# Raised from 600,000 on 2026-08-27 to match what the README describes, because
# the two had drifted apart and the document was the thing being read. An
# 80/20 split is also the allocation a reader recognises: nobody argues with
# it, which is the point -- the promise breaks on an ordinary portfolio rather
# than on one built to break.
#
# 150,000 of the reserve sits in bills and the rest stays as cash. The agent
# spends cash on protection, and a reserve entirely in an ETF would be a
# reserve it cannot reach without selling something first.
TARGET = {
    "SPY": Decimal("400000"),
    "QQQ": Decimal("200000"),
    "IWM": Decimal("200000"),
    "BIL": Decimal("150000"),
}

# Cash held back beyond the collateral, so a fill a few cents through the quote
# cannot leave a short put uncovered by rounding.
CASH_BUFFER = Decimal("10000")

# What each holding is for. Carried into the journal so the roles are recorded
# with the position rather than living only in someone's memory.
ROLE = {
    "SPY": "equity exposure",
    "QQQ": "equity exposure",
    "IWM": "equity exposure",
    "BIL": "ballast — not protection",
}

# Everything the client may end up holding, for the guard that refuses to run
# on an account that already has a portfolio on it. TLT
# is here because the scenario has the client buy it mid-week believing it
# diversifies; it is not in TARGET, and the agent counts it as exposure rather
# than protection for the reason written in risk/book.py.
KNOWN = (*TARGET, "TLT")


def short_put_collateral(positions: list[dict]) -> Decimal:
    """Cash the existing overlay ties up, at full strike value.

    Alpaca reports four million of buying power on this account, because it is
    a margin account and margin is how a broker prices a short put. This
    project does not use it: `forbid_naked` requires every short put to be
    covered by the whole strike in cash, and that rule has no override
    anywhere. So the reservation is computed here rather than read from the
    broker, and the number is deliberately larger than the one Alpaca would
    demand.
    """
    total = Decimal("0")
    for position in positions:
        occ = parse_occ(str(position["symbol"]))
        if occ is None or occ["right"] != "P":
            continue
        qty = int(Decimal(str(position.get("qty", 0))))
        if qty < 0:
            total += occ["strike"] * abs(qty) * SHARES_PER_CONTRACT
    return total


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
        held_shares = [
            p
            for p in positions
            if parse_occ(str(p["symbol"])) is None and str(p["symbol"]) in KNOWN
        ]
        if held_shares:
            print(f"refusing: {len(held_shares)} of the target holdings already exist.")
            print("This script establishes a portfolio; it does not adjust one.")
            for p in held_shares:
                print(f"  {p.get('symbol')}  qty {p.get('qty')}")
            return 1

        reserved = short_put_collateral(positions)
        if reserved:
            print("the agent already has an overlay open. Collateral reserved:")
            for p in positions:
                occ = parse_occ(str(p["symbol"]))
                if occ and occ["right"] == "P" and int(Decimal(str(p["qty"]))) < 0:
                    qty = abs(int(Decimal(str(p["qty"]))))
                    print(
                        f"  {p['symbol']}  {qty} short  "
                        f"{occ['strike'] * qty * SHARES_PER_CONTRACT:,.0f}"
                    )
            print(f"  {'total':<26}{reserved:>14,.0f}\n")

        clock = _unwrap(await session.call_tool("get_clock", {}), "c")["data"]
        if not clock.get("is_open"):
            print(f"market closed; next open {clock.get('next_open')}")
            if args.execute:
                print("refusing to send orders into a closed market.")
                return 1

        print(f"equity {equity:,.0f}   cash {cash:,.0f}\n")

        targets = dict(TARGET)
        # Room for the collateral comes out of the ballast, never out of the
        # equity sleeve. The equity sleeve is sized by the mandate -- shrinking
        # it to fit an overlay would quietly shrink the protection gap too, and
        # the portfolio would stop demonstrating the thing it exists to
        # demonstrate. BIL is where capital waits, so BIL is what waits.
        shortfall = reserved + CASH_BUFFER - (cash - sum(targets.values()))
        if shortfall > 0:
            if targets["BIL"] - shortfall < 0:
                print(
                    f"refusing: {reserved:,.0f} of collateral plus a "
                    f"{CASH_BUFFER:,.0f} buffer leaves no room for the "
                    f"{sum(v for k, v in targets.items() if k != 'BIL'):,.0f} "
                    f"equity sleeve. Close the overlay first, or size it smaller."
                )
                return 1
            targets["BIL"] -= shortfall
            print(
                f"ballast reduced by {shortfall:,.0f} to keep the short puts "
                f"cash-secured. Equity exposure is unchanged.\n"
            )

        print(f"{'symbol':<7}{'role':<26}{'price':>9}{'shares':>9}{'value':>13}")
        print("-" * 64)
        plan = []
        spent = Decimal("0")
        for symbol, target in targets.items():
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
        if reserved:
            print(f"{'':<42}{'reserved':>9}{reserved:>13,.0f}")
            print(f"{'':<42}{'free':>9}{remaining - reserved:>13,.0f}")
            if remaining < reserved:
                print("\nrefusing: this plan would leave a short put uncovered.")
                return 1
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
