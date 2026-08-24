"""Preflight. Exit 0 when it is safe to trade, non-zero with a reason when not.

Every check prints its own line whether it passes or fails, so a green run says
what it verified rather than only that nothing complained. A scheduled job that
fails quietly is worse than no scheduled job: you spend the week believing the
agent is trading.

The checks are ordered cheapest-and-most-fundamental first. There is no point
asking the broker whether the market is open if the credentials are wrong, and
no point doing anything at all if the paper-trading interlock is off.
"""

import asyncio
from datetime import date, datetime

from flywheel.mcp.alpaca_client import FULL_TOOLSETS, alpaca_session
from flywheel.settings import get_settings
from flywheel.store import load_all

# A normal US equity session closes at 16:00 ET. A half day closes at 13:00,
# and the option chains thin out badly into an early close.
FULL_SESSION_CLOSE = "16:00"

# Below this the account cannot carry a single position at the configured
# per-instrument share of capital on the cheapest instrument in the universe.
MIN_EQUITY = 25_000.0


class Failure(Exception):
    """A check that says trading must not proceed."""


def _root_cause(exc: BaseException) -> str:
    """The innermost real error, unwrapped from any ExceptionGroup.

    Task groups nest, so a connection refused three levels down surfaces as
    "unhandled errors in a TaskGroup" unless it is dug out.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


async def _read(session, tool: str, args: dict | None = None):
    from flywheel.mcp.alpaca_client import _unwrap

    return _unwrap(await session.call_tool(tool, args or {}), tool)["data"]


def check_paper_interlock() -> str:
    """The one check that must never be reachable in a state where it fails.

    `Settings` refuses to construct when `ALPACA_PAPER_TRADE` is not true, so
    by the time this runs it has already held. It is asserted anyway: the value
    of an interlock is that it is checked in more places than strictly needed.
    """
    settings = get_settings()
    if settings.alpaca_paper_trade is not True:
        raise Failure("ALPACA_PAPER_TRADE is not true — refusing to trade")
    if not settings.alpaca_api_key or not settings.alpaca_secret_key:
        raise Failure("Alpaca credentials are missing")
    return f"paper trading interlock on, env={settings.flywheel_env}"


async def check_account(session) -> str:
    try:
        account = await _read(session, "get_account_info")
    except Exception as exc:  # noqa: BLE001
        raise Failure(f"could not read the account: {exc}") from exc

    for blocker in ("trading_blocked", "account_blocked"):
        if account.get(blocker):
            raise Failure(f"the broker reports {blocker} — refusing to trade")

    equity = float(account.get("equity", 0))
    if equity < MIN_EQUITY:
        raise Failure(f"equity {equity:,.0f} is below the {MIN_EQUITY:,.0f} floor")

    # Deliberately not reported: buying_power. It is four times equity on this
    # account and nothing may size against it. See market/client.py.
    return (
        f"account active, equity {equity:,.0f}, "
        f"options level {account.get('options_trading_level')}"
    )


async def check_market_open(session) -> str:
    try:
        clock = await _read(session, "get_clock")
    except Exception as exc:  # noqa: BLE001
        raise Failure(f"could not read the market clock: {exc}") from exc

    if not clock.get("is_open"):
        raise Failure(f"the market is closed; next open {clock.get('next_open')}")

    today = date.today().isoformat()
    try:
        calendar = await _read(session, "get_calendar", {"start": today, "end": today})
        sessions = calendar.get("result") or calendar.get("calendar") or []
    except Exception:  # noqa: BLE001
        sessions = []

    for entry in sessions if isinstance(sessions, list) else []:
        close = str(entry.get("close", ""))
        if close and not close.startswith(FULL_SESSION_CLOSE):
            raise Failure(
                f"today is a half day, closing {close} — the chains thin out "
                f"into an early close and the fills are not representative"
            )
    return "market open, full session"


async def check_state_reconciles(session) -> str:
    """Local belief against the broker's record.

    A discrepancy is not automatically fatal — the reconciler exists precisely
    to absorb assignments and expiries the agent did not initiate — but it must
    be *seen* before a cycle runs on top of it.
    """
    from flywheel.execution.reconcile import reconcile

    try:
        positions = (await _read(session, "get_all_positions")).get("result") or []
    except Exception as exc:  # noqa: BLE001
        raise Failure(f"could not read positions: {exc}") from exc

    try:
        local = load_all()
    except Exception:  # noqa: BLE001
        local = {}

    _, discrepancies = reconcile(local, positions)
    if discrepancies:
        return "state differs from the broker: " + "; ".join(discrepancies)
    return f"state reconciles, {len(positions)} broker positions"


async def run() -> int:
    print(f"flywheel healthcheck {datetime.now().isoformat(timespec='seconds')}")
    try:
        print(f"  ok   {check_paper_interlock()}")
    except Failure as failure:
        print(f"  FAIL {failure}")
        return 1

    # The failure is captured rather than raised through the session context.
    #
    # `alpaca_session` is an anyio task group, and an exception crossing its
    # boundary is re-raised wrapped in an ExceptionGroup. A `except Failure`
    # outside the block therefore never matches, and the operator is told
    # "unhandled errors in a TaskGroup (1 sub-exception)" instead of "the
    # market is closed". For a script whose entire purpose is to say plainly
    # why it will not trade, swallowing the reason is the worst possible bug.
    failure: Failure | None = None
    lines: list[str] = []
    try:
        async with alpaca_session(FULL_TOOLSETS) as session:
            for check in (check_account, check_market_open, check_state_reconciles):
                try:
                    lines.append(await check(session))
                except Failure as caught:
                    failure = caught
                    break
    except Exception as exc:  # noqa: BLE001
        for line in lines:
            print(f"  ok   {line}")
        print(f"  FAIL could not reach the broker: {_root_cause(exc)}")
        return 1

    for line in lines:
        print(f"  ok   {line}")
    if failure is not None:
        print(f"  FAIL {failure}")
        return 1

    print("safe to trade")
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
