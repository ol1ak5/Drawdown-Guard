"""Do what the client said they would do, on the day they said it.

The client is a person, not a strategy. They rebalance, they add money, they
take some off the table -- and none of that is the agent's decision. This
script performs one day's client action from `config/scenario.yaml`, so the
agent's response to it can be watched in the journal.

WHY THE SCENARIO IS READ AND NOT ARGUED WITH
---------------------------------------------
Every action was fixed and committed before the first cycle ran. This script
refuses to take a symbol or a quantity on the command line: if the actions
could be chosen at the prompt they could be chosen after seeing the market, and
the demonstration would prove nothing about an agent that claims to take no
view. The day is the only argument, and even that is checked against the date.

    .venv/bin/python scripts/client_action.py --day 3            # shows it
    .venv/bin/python scripts/client_action.py --day 3 --submit   # does it

Nothing is sent without `--submit`.

These are the client's trades, not the agent's. They never pass the risk gate,
because the gate governs what the agent may do with somebody's portfolio and
has no business overruling the owner of it. The agent finds out the same way it
finds out about everything: by reading the account next morning.
"""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

import yaml

from drawdownguard.journal import writer
from drawdownguard.mcp.alpaca_client import call_tool
from drawdownguard.settings import get_settings

SCENARIO_PATH = Path("config/scenario.yaml")


def scenario(path: Path = SCENARIO_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def day_plan(number: int, path: Path = SCENARIO_PATH) -> dict | None:
    for entry in scenario(path).get("days", []):
        if entry.get("day") == number:
            return entry
    return None


async def submit(action: str, detail: dict) -> str | None:
    """Send the client's own order. Market, because this is not a hedge.

    A limit here would be the script deciding what the client's rebalance is
    worth, and a rebalance that does not fill is a scenario that did not
    happen. The agent's orders are limits; the client's are not, and the
    difference is whose money is making the choice.
    """
    response = await call_tool(
        "place_stock_order",
        {
            "symbol": detail["symbol"],
            "qty": str(detail["shares"]),
            "side": "sell" if action == "sell_equity" else "buy",
            "type": "market",
            "time_in_force": "day",
        },
    )
    body = response.get("data", {})
    return body.get("id") if isinstance(body, dict) else None


async def main(number: int, send: bool) -> int:
    if not get_settings().alpaca_paper_trade:
        print("refusing to run: this is not a paper account")
        return 1

    plan = day_plan(number)
    if plan is None:
        print(f"no day {number} in {SCENARIO_PATH}")
        return 1

    action = plan.get("client_action", "none")
    print(f"day {number}  planned for {plan['date']}  today is {date.today()}")
    if str(plan["date"]) != str(date.today()):
        # A warning rather than a refusal. A market holiday or a late start
        # should not strand the scenario, but running the wrong day by accident
        # is exactly the mistake that makes a demonstration unreadable.
        print("  WARNING: this is not the day the scenario names")
    print(f"action: {action}")
    print(f"expect: {' '.join(plan.get('expect', '').split())}")

    if action == "none":
        print("\nNothing for the client to do today. The agent runs on its own.")
        return 0

    detail = plan.get("detail") or {}
    verb = "sell" if action == "sell_equity" else "buy"
    print(f"\norder: {verb} {detail.get('shares')} {detail.get('symbol')} at market")

    if not send:
        print("\nnothing sent. re-run with --submit to place it.")
        return 0

    order_id = await submit(action, detail)
    print(f"sent  order {order_id}")
    writer.write(
        "client.acted",
        {
            "day": number,
            "action": action,
            "symbol": detail.get("symbol"),
            "shares": detail.get("shares"),
            "broker_order_id": order_id,
            # Carried into the journal so the agent's response the next morning
            # can be read against the reason for it, without anyone having to
            # go and find the scenario file.
            "expect": " ".join(plan.get("expect", "").split()),
            "decided": scenario().get("recorded"),
        },
        severity="info",
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=int, required=True, help="which scenario day")
    parser.add_argument(
        "--submit", action="store_true", help="actually place the client's order"
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.day, args.submit)))
