"""Buy back every short option on the account.

The book is meant to hold equity and protection, and nothing else. A short
option is neither: it is an obligation, and an obligation sitting under a
promise about losses works directly against it. At a total collapse a short put
costs the whole strike, so four IWM 280s and one QQQ 660 add 178,000 to the
worst case of a client whose entire budget is 100,572 -- 1.77 times the promise,
from two positions nobody would describe as risky.

They were written for income by the strategy this project used to be. Nothing
about the mandate wants them.

WHY THIS IS A SCRIPT AND NOT A NODE
------------------------------------
The agent buys protection. It does not tidy up after a previous strategy, and a
one-off cleanup does not belong in a cycle that runs every weekday. Run this
once, by hand, and read what it says before letting it send anything.

    .venv/bin/python scripts/close_short_options.py            # shows the plan
    .venv/bin/python scripts/close_short_options.py --submit   # sends the orders

Nothing is sent without `--submit`. The default prints the plan and exits, so
the safe invocation is also the shortest one to type.

PRICING
-------
Each order is a limit at the ask, which is what it costs to buy back a short
immediately. Marketable, but still a limit: a market order on a wide options
spread is how an account donates money one contract at a time.
"""

import argparse
import asyncio
import sys
from decimal import Decimal

from drawdownguard.journal import writer
from drawdownguard.mcp.alpaca_client import call_tool
from drawdownguard.settings import get_settings

SHARES_PER_CONTRACT = 100


async def short_option_positions() -> list[dict]:
    """Every option position the account is short, straight from the broker.

    Read live rather than from our own state file. What the broker says it
    holds is the only thing that can be closed.
    """
    response = await call_tool("get_all_positions", {}, "trading")
    rows = response["data"]["result"]
    return [
        row
        for row in rows
        if row.get("asset_class") == "us_option" and int(row["qty"]) < 0
    ]


async def ask_price(occ_symbol: str) -> Decimal | None:
    """Today's ask for one contract, or None if the quote is unusable.

    A missing or zero ask means nobody is offering the contract, and a limit
    priced off a number that is not there is a limit that will never fill.

    The parameter is `symbols`, not `symbol_or_symbols`: the server forwards it
    to the data API, which names it the other way and answers a 400 rather than
    an empty quote when it is wrong.
    """
    response = await call_tool("get_option_latest_quote", {"symbols": occ_symbol})
    quotes = response.get("data", {}).get("quotes", {})
    quote = quotes.get(occ_symbol)
    if not isinstance(quote, dict):
        return None
    ask = quote.get("ap")
    if ask is None or float(ask) <= 0:
        return None
    return Decimal(str(ask))


async def main(submit: bool) -> int:
    if not get_settings().alpaca_paper_trade:
        print("refusing to run: this is not a paper account")
        return 1

    shorts = await short_option_positions()
    if not shorts:
        print("no short options on the account; nothing to close")
        return 0

    print(f"{'contract':<24}{'qty':>6}{'ask':>9}{'cost':>12}")
    plan, total = [], Decimal("0")
    for row in shorts:
        occ = row["symbol"]
        quantity = abs(int(row["qty"]))
        ask = await ask_price(occ)
        if ask is None:
            print(f"{occ:<24}{quantity:>6}{'no quote':>9}{'skipped':>12}")
            continue
        cost = ask * quantity * SHARES_PER_CONTRACT
        total += cost
        plan.append((occ, quantity, ask))
        print(f"{occ:<24}{quantity:>6}{float(ask):>9.2f}{float(cost):>12,.0f}")

    print(f"\n{len(plan)} orders, {float(total):,.0f} to close them all")

    if not submit:
        print("\nnothing sent. re-run with --submit to place these orders.")
        return 0

    for occ, quantity, ask in plan:
        response = await call_tool(
            "place_option_order",
            {
                "symbol": occ,
                "qty": str(quantity),
                "side": "buy",
                "position_intent": "buy_to_close",
                "type": "limit",
                "limit_price": f"{ask:.2f}",
                "time_in_force": "day",
            },
        )
        body = response.get("data", {})
        order_id = body.get("id") if isinstance(body, dict) else None
        print(f"sent  {occ}  x{quantity}  order {order_id}")
        writer.write(
            "short_option_closed",
            {
                "occ_symbol": occ,
                "contracts": quantity,
                "limit_price": str(ask),
                "reason": (
                    "a short option is an obligation, and the mandate promises "
                    "about losses; these were written for income by the earlier "
                    "strategy and work against the promise"
                ),
                "broker_order_id": order_id,
            },
        )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--submit",
        action="store_true",
        help="actually send the orders; without it the plan is only printed",
    )
    sys.exit(asyncio.run(main(parser.parse_args().submit)))
