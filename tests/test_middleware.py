"""The guards, and the distinction between a veto and a defect.

The load-bearing test here is that an order tool reaching the analyst is
recorded at `defect` severity and not at `veto`. Those two mean opposite
things: a veto is the risk gate working, a defect is the design leaking, and
recording both the same way buries the one that means the system is wrong.
"""

import sys
from datetime import UTC, datetime
from decimal import Decimal

import pytest

sys.path.insert(0, "scripts")

from flywheel.agent.middleware.guards import (
    HALT_FILE,
    ORDER_TOOLS,
    JournalMiddleware,
    KillSwitchMiddleware,
    MarketHoursMiddleware,
    RetryMiddleware,
    RiskGateMiddleware,
    default_stack,
)
from flywheel.domain import Portfolio
from flywheel.journal import writer


@pytest.fixture(autouse=True)
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "JOURNAL_DIR", tmp_path)
    return tmp_path


def entries(directory):
    # UTC, not local: the journal stamps in UTC and the two dates
    # disagree between 22:00 UTC and midnight.
    return writer.read_day(datetime.now(UTC).date(), directory=directory)


def portfolio(equity="1000000", peak="1000000"):
    return Portfolio(
        equity=Decimal(equity), cash=Decimal(equity), peak_equity=Decimal(peak)
    )


# --- the risk gate tripwire -------------------------------------------------


def test_a_read_only_tool_passes_through_untouched():
    called = {}

    def call_next(name, arguments):
        called["name"] = name
        return {"role": "tool", "content": "ok"}

    result = RiskGateMiddleware().wrap_tool_call(
        "get_option_chain", {"underlying_symbol": "SPY"}, call_next
    )
    assert called["name"] == "get_option_chain"
    assert result["content"] == "ok"


def test_an_order_tool_is_blocked_and_never_reaches_the_broker():
    def call_next(name, arguments):
        raise AssertionError("the order tool must not be executed")

    result = RiskGateMiddleware().wrap_tool_call(
        "place_option_order", {"qty": "4"}, call_next
    )
    assert result["is_error"] is True
    assert "read-only" in result["content"]


def test_a_blocked_order_tool_is_a_defect_not_a_veto(journal_dir):
    """The distinction the whole module exists to preserve.

    A veto means an order was proposed, examined and refused — the design
    working. A defect means the analyst could see a tool it should never have
    been given, which is a configuration failure. Logging this as a veto would
    hide a broken toolset among ordinary refused trades.
    """
    RiskGateMiddleware().wrap_tool_call(
        "place_option_order", {"qty": "4"}, lambda n, a: None
    )
    written = [e for e in entries(journal_dir) if e["event"].startswith("middleware")]
    assert written
    assert written[-1]["severity"] == "defect"
    assert written[-1]["severity"] != "veto"


@pytest.mark.parametrize("tool", sorted(ORDER_TOOLS))
def test_every_mutating_tool_is_covered(tool):
    result = RiskGateMiddleware().wrap_tool_call(tool, {}, lambda n, a: None)
    assert result["is_error"] is True


def test_reads_that_merely_contain_the_word_order_still_pass():
    """`get_orders` reads; `close_all_positions` destroys and has no "order" in
    it. A substring test would get both backwards."""
    assert "get_orders" not in ORDER_TOOLS
    assert "close_all_positions" in ORDER_TOOLS


# --- the kill switch --------------------------------------------------------


def test_a_drawdown_breach_ends_the_cycle():
    guard = KillSwitchMiddleware(max_drawdown_pct=15.0)
    breached = portfolio(equity="800000", peak="1000000")
    assert guard.before_agent({"portfolio": breached}) == {"jump_to": "end"}


def test_a_healthy_account_is_not_stopped():
    guard = KillSwitchMiddleware(max_drawdown_pct=15.0)
    assert guard.before_agent({"portfolio": portfolio()}) is None


def test_a_halt_file_stops_the_cycle_before_anything_else(tmp_path):
    """The control that works from a phone: commit a file, push, done."""
    (tmp_path / HALT_FILE).touch()
    guard = KillSwitchMiddleware(max_drawdown_pct=15.0, root=tmp_path)
    assert guard.before_agent({"portfolio": portfolio()}) == {"jump_to": "end"}


def test_the_halt_file_overrides_a_perfectly_healthy_account(tmp_path, journal_dir):
    (tmp_path / HALT_FILE).touch()
    guard = KillSwitchMiddleware(max_drawdown_pct=15.0, root=tmp_path)
    guard.before_agent({"portfolio": portfolio()})
    reasons = [e["payload"].get("reason", "") for e in entries(journal_dir)]
    assert any(HALT_FILE in r for r in reasons)


# --- market hours -----------------------------------------------------------


def test_a_closed_market_ends_the_cycle():
    guard = MarketHoursMiddleware({"is_open": False, "next_open": "2026-08-25T09:30"})
    assert guard.before_agent({}) == {"jump_to": "end"}


def test_a_half_day_ends_the_cycle():
    guard = MarketHoursMiddleware({"is_open": True, "close": "13:00:00-04:00"})
    assert guard.before_agent({}) == {"jump_to": "end"}


def test_a_full_session_proceeds():
    guard = MarketHoursMiddleware({"is_open": True, "close": "16:00:00-04:00"})
    assert guard.before_agent({}) is None


# --- retry ------------------------------------------------------------------


async def test_a_transient_failure_is_retried_and_then_succeeds():
    attempts = {"n": 0}

    async def flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise RuntimeError("503 overloaded")
        return "ok"

    result = await RetryMiddleware(attempts=3, base_delay=0.0).wrap_model_call(flaky)
    assert result == "ok"
    assert attempts["n"] == 2


async def test_retries_are_bounded_and_the_failure_finally_surfaces():
    """Unbounded retry against a persistently failing model turns one broken
    cycle into a scheduled job that never finishes."""
    attempts = {"n": 0}

    async def always_fails():
        attempts["n"] += 1
        raise RuntimeError("still down")

    with pytest.raises(RuntimeError, match="still down"):
        await RetryMiddleware(attempts=3, base_delay=0.0).wrap_model_call(always_fails)
    assert attempts["n"] == 3


# --- the stack --------------------------------------------------------------


def test_the_halt_check_runs_before_anything_that_costs_a_network_call():
    """A HALT file overrides everything, so asking the broker first would be
    work done to reach a decision already made."""
    stack = default_stack(max_drawdown_pct=15.0)
    assert isinstance(stack[0], KillSwitchMiddleware)
    assert any(isinstance(m, RiskGateMiddleware) for m in stack)
    assert any(isinstance(m, JournalMiddleware) for m in stack)


# --- the healthcheck's three outcomes ---------------------------------------


def test_a_decline_and_a_failure_are_different_exit_codes():
    """Alert fatigue is a safety problem, not a cosmetic one.

    A closed market and a revoked key are both reasons not to trade. Reported
    identically, the weekend runs teach everyone to ignore the mail, and the
    one notification that means something arrives into a folder nobody opens.
    """
    import scripts.healthcheck as hc

    assert hc.Declined is not hc.Failure
    assert not issubclass(hc.Declined, hc.Failure)
    assert not issubclass(hc.Failure, hc.Declined)


def test_a_closed_market_is_a_decline_not_a_failure():
    import inspect

    import scripts.healthcheck as hc

    source = inspect.getsource(hc.check_market_open)
    assert "raise Declined" in source
    assert "the market is closed" in source
    # The credential and account checks must stay Failures: those are real.
    assert "raise Failure" in inspect.getsource(hc.check_paper_interlock)
    assert "raise Failure" in inspect.getsource(hc.check_account)
