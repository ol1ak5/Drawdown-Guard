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

A downside budget of 10% on 100,000 is 10,000. Under a 20% equity shock,
exposure E loses 0.2E, so the largest exposure that honours the promise with no
protection at all is:

    0.2 * E <= 10,000   ->   E <= 50,000

82,080 is deliberately above that line. At -20% it produces a 6,416 shortfall
the agent has to close, and at -10% none at all. A portfolio that never breaches
gives the agent nothing to do; one that always breaches makes the mandate
theatre. This one breaches where hedging is actually the question.

It is also, deliberately, an allocation nobody would query. Eighty percent in
equities and twenty in reserve is what any adviser would sign, and that is the
argument: the promise is broken by an ordinary portfolio, not by one built to
break.

WHY THIS FILE COUNTS SHARES AND NOT DOLLARS
--------------------------------------------
It used to hold a dollar target per symbol and divide by the price. That is the
natural way to write an allocation and it is the wrong way to write this one,
because an option contract covers a hundred shares and nothing smaller. A
40,000 target in a 300 stock buys 133 shares, and the smallest hedge that
stands behind 133 shares covers 200 -- the client pays for fifty percent more
protection than they own, and the surplus is a position that pays only if
prices fall.

On the old 1,000,000 account the rounding was invisible: 523 shares against 600
covered is a 15% overshoot and nobody notices. At a tenth of the size the same
arithmetic gives 52 shares against 100, and the hedge is twice the holding.

So the target is a share count, chosen as a round hundred, and the dollar value
is whatever the market says that morning. The allocation drifts with prices by
a few percent and the hedge lands exactly; the reverse trades an exact
allocation for a hedge that never fits.

This is also why SPY and QQQ are not here. At 770 and 720 a share, a hundred
shares is most of the account, and any smaller holding cannot be hedged without
overshooting by half. IWM at 300 and XLF at 58 are the same kind of instrument
priced where whole contracts land on whole positions.

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
from decimal import ROUND_CEILING, Decimal

from drawdownguard.domain import SHARES_PER_CONTRACT
from drawdownguard.execution.reconcile import parse_occ
from drawdownguard.journal import writer
from drawdownguard.market.chain import mid_of
from drawdownguard.mcp.alpaca_client import FULL_TOOLSETS, _unwrap, alpaca_session

# Target dollars per instrument. Equity totals 800,000 — see the docstring.
#
# Raised from 600,000 on 2026-08-27 to match what the README describes, because
# the two had drifted apart and the document was the thing being read. An
# 80/20 split is also the allocation a reader recognises: nobody argues with
# it, which is the point -- the promise breaks on an ordinary portfolio rather
# than on one built to break.
#
# Shares, not dollars -- see the docstring. Every equity line is a round
# hundred so a whole contract stands behind a whole position.
#
# At the prices this was written against (IWM 297.90, XLF 58.30, BIL 91.66)
# that is 29,790 + 52,466 = 82,256 of equity exposure and 9,166 of ballast,
# leaving roughly 8,600 in cash. The reserve sits in bills rather than in cash
# because idle cash in an Alpaca paper account earns nothing, and part of it
# stays liquid because the agent spends cash on protection.
#
# THE BALLAST IS 100 AND NOT 150 BECAUSE OF THE PREMIUM
# ------------------------------------------------------
# A long option is paid for in full the moment it is opened, and the first
# cycle opens the whole hedge at once. Priced on the live chain the day this
# was written that came to 3,913 -- nine XLF puts and one on IWM.
#
# At 150 shares of ballast the plan left 3,991 in cash: a margin of 78 dollars
# against a number that moves with every quote. The agent would have been
# funded on Tuesday and unable to buy its own protection on Wednesday, and the
# journal would have reported an open gap it could not close.
#
# `CASH_BUFFER` does not cover this. It guards the collateral behind a written
# put, and on a clean account there is no written put, so nothing is held back
# at all. The premium is a different obligation and this is where it is met.
TARGET = {
    "IWM": 100,
    "XLF": 900,
    "BIL": 100,
}

# Cash held back beyond the collateral, so a fill a few cents through the quote
# cannot leave a short put uncovered by rounding. A tenth of the old buffer,
# for a tenth of the account.
CASH_BUFFER = Decimal("1000")

# What each holding is for. Carried into the journal so the roles are recorded
# with the position rather than living only in someone's memory.
ROLE = {
    "IWM": "equity exposure",
    "XLF": "equity exposure",
    "BIL": "ballast — not protection",
}

# Everything the client may end up holding, for the guard that refuses to run
# on an account that already has a portfolio on it. AAPL is not in TARGET --
# the client buys it mid-week in the scenario -- but an account carrying it
# is an account partway through a run, not an empty one.
KNOWN = (*TARGET, "AAPL")


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


def size_against_cash(
    target: dict[str, int],
    prices: dict[str, Decimal],
    cash: Decimal,
    reserved: Decimal,
) -> tuple[dict[str, int], str | None, str | None]:
    """Fit the target share counts into the cash actually available.

    Returns the counts to buy, a note to print when anything moved, and a
    refusal when the plan cannot be made to fit. Pure: no session, no clock, no
    printing, so the arithmetic that decides what gets bought can be tested
    without a broker.

    Room for the collateral comes out of the ballast, never out of the equity
    sleeve. The equity sleeve is sized by the mandate -- shrinking it to fit an
    overlay would quietly shrink the protection gap too, and the portfolio
    would stop demonstrating the thing it exists to demonstrate. BIL is where
    capital waits, so BIL is what waits.

    The reduction is in whole shares rounded up, so it is never a fraction
    short of what the collateral needs. Rounded with `ROUND_CEILING` and not
    with the usual `-(-a // b)`: that idiom is a ceiling only where `//` floors,
    and `Decimal` truncates toward zero instead. 1,820 of shortfall over a
    91.60 share needs 20 shares and the idiom returned 19, leaving the buffer
    92 dollars short of the collateral it exists to protect.
    """
    targets = dict(target)
    cost = sum(prices[s] * q for s, q in targets.items())
    shortfall = reserved + CASH_BUFFER - (cash - cost)
    if shortfall <= 0:
        return targets, None, None

    give_back = int(
        (shortfall / prices["BIL"]).to_integral_value(rounding=ROUND_CEILING)
    )
    if give_back > targets["BIL"]:
        equity_cost = sum(prices[s] * q for s, q in targets.items() if s != "BIL")
        return (
            targets,
            None,
            f"refusing: {reserved:,.0f} of collateral plus a "
            f"{CASH_BUFFER:,.0f} buffer leaves no room for the "
            f"{equity_cost:,.0f} equity sleeve. Close the overlay first, "
            "or size it smaller.",
        )

    targets["BIL"] -= give_back
    return (
        targets,
        f"ballast reduced by {give_back} shares "
        f"({give_back * prices['BIL']:,.0f}) to keep the short puts "
        "cash-secured. Equity exposure is unchanged.",
        None,
    )


async def quote(session, symbol: str) -> float:
    """The price, through the one function that knows what a missing side means.

    This used to average `bp` and `ap` here. After the close a stock quote is
    routinely one-sided -- AAPL on 2026-08-27 was bid 294.98, ask 0 -- and that
    average is exactly half the price, arriving as a plausible number with no
    error raised. This script divides a dollar target by it, so a half price
    buys twice the shares: a 40,000 target becomes 271 shares of a 295 stock
    instead of 135, and the portfolio the mandate was sized for is double the
    one the account gets.

    `mid_of` is that function and already carries the regression note. It is
    imported rather than reimplemented because this is the third copy of this
    arithmetic in the project and the previous fix reached only two of them.
    """
    data = _unwrap(
        await session.call_tool("get_stock_latest_quote", {"symbols": symbol}), "q"
    )["data"]
    return mid_of(data["quotes"][symbol])


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

        # Priced before anything is sized, because the targets are share counts
        # and only the market says what they cost this morning.
        prices = {s: Decimal(str(await quote(session, s))) for s in TARGET}
        targets, note, refusal = size_against_cash(TARGET, prices, cash, reserved)
        if refusal:
            print(refusal)
            return 1
        if note:
            print(note + "\n")

        print(f"{'symbol':<7}{'role':<26}{'price':>9}{'shares':>9}{'value':>13}")
        print("-" * 64)
        plan = []
        spent = Decimal("0")
        for symbol, shares in targets.items():
            price = float(prices[symbol])
            value = prices[symbol] * shares
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
        #
        # This is the *shortfall* at one named depth, not the uncovered risk
        # the agent sizes against -- that one measures the whole descent and on
        # a bare equity book comes to the entire sleeve. Both are reported by
        # the cycle; only this one can be checked against a single row of the
        # ladder by hand, which is what it is here for.
        budget = equity * Decimal("0.10")
        loss_at_20 = equity_value * Decimal("0.20")
        shortfall = max(loss_at_20 - budget, Decimal("0"))
        print(
            f"downside budget {budget:,.0f}  "
            f"loss at -20% {loss_at_20:,.0f}  shortfall {shortfall:,.0f}"
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
