"""The cycle graph, with the market and the broker mocked out.

Nothing here touches Alpaca. The claims worth testing are about control flow —
that a halted cycle still writes a journal entry, that a halted cycle submits
nothing — and those are exactly the claims a live test would verify least
reliably, because a live run that happens not to trade proves nothing.
"""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from flywheel.agent.graph import build_graph
from flywheel.agent.state import initial_state
from flywheel.domain import Portfolio, WheelState
from flywheel.journal import writer
from flywheel.market.features import MarketSnapshot

SYMBOLS = ["SPY", "QQQ", "IWM"]


@pytest.fixture
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "JOURNAL_DIR", tmp_path)
    return tmp_path


def entries(directory) -> list[dict]:
    """Read the day the journal actually wrote, which is the UTC one.

    `date.today()` is local, and the journal stamps entries in UTC. The two
    agree for twenty-two hours a day, so this read passed every time it was
    tried until a run happened to land between 22:00 UTC and midnight. A test
    that is correct most of the day is a test that fails in someone else's
    timezone and nobody knows why.
    """
    return writer.read_day(datetime.now(UTC).date(), directory=directory)


def healthy_portfolio(**overrides) -> Portfolio:
    values = {
        "equity": Decimal("1000000"),
        "cash": Decimal("1000000"),
        "peak_equity": Decimal("1000000"),
        "wheels": {s: WheelState(symbol=s) for s in SYMBOLS},
    }
    values.update(overrides)
    return Portfolio(**values)


def snapshot(symbol: str) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        spot=500.0,
        realized_vol_20d=0.15,
        realized_vol_60d=0.16,
        atm_iv=0.20,
        iv_rank=None,
        returns=np.random.default_rng(0).normal(0, 0.01, 300),
    )


async def run(
    portfolio, chain_rows=None, journal_dir=None, positions=None, positions_error=None
):
    """Invoke the graph with the broker and the market replaced.

    `positions_error` belongs here rather than in a `patch` around the call:
    this helper patches `get_positions` itself, so an outer patch of the same
    name is shadowed by the inner one and the test silently exercises nothing.
    """
    chain_rows = chain_rows if chain_rows is not None else []
    reader = (
        AsyncMock(side_effect=positions_error)
        if positions_error
        else AsyncMock(return_value=positions if positions is not None else [])
    )
    with (
        patch(
            "flywheel.agent.nodes.get_account",
            new=AsyncMock(return_value=(portfolio, [])),
        ),
        patch("flywheel.agent.nodes.get_positions", new=reader),
        patch(
            "flywheel.agent.nodes.build_snapshot",
            new=AsyncMock(side_effect=lambda s, *a, **k: snapshot(s)),
        ),
        patch(
            "flywheel.market.chain.load_chain",
            new=AsyncMock(return_value=chain_rows),
        ),
        patch("flywheel.journal.writer.JOURNAL_DIR", journal_dir),
        patch(
            "flywheel.execution.orders.call_tool",
            new=AsyncMock(return_value={"data": {"id": "abc-123"}}),
        ) as broker,
    ):
        final = await build_graph().ainvoke(initial_state())
        return final, broker


async def test_the_graph_completes_and_journals_the_cycle(journal_dir):
    final, _ = await run(healthy_portfolio(), journal_dir=journal_dir)
    assert final is not None
    written = entries(journal_dir)
    assert any(e["event"] == "cycle.complete" for e in written)


async def test_a_drawdown_halt_still_writes_a_journal_entry(journal_dir):
    """The reason the halt routes to the journal instead of returning early.

    A cycle that stopped and said nothing cannot be told apart, days later,
    from a cycle that crashed.
    """
    drawn = healthy_portfolio(equity=Decimal("800000"), peak_equity=Decimal("1000000"))
    final, broker = await run(drawn, journal_dir=journal_dir)

    assert final["halted"] is True
    assert "drawdown" in final["halt_reason"].lower()
    broker.assert_not_awaited()

    written = entries(journal_dir)
    complete = [e for e in written if e["event"] == "cycle.complete"]
    assert complete, "a halted cycle must still complete its journal entry"
    assert complete[-1]["payload"]["halted"] is True
    assert complete[-1]["payload"]["halt_reason"]


async def test_a_halted_cycle_submits_nothing(journal_dir):
    drawn = healthy_portfolio(equity=Decimal("500000"), peak_equity=Decimal("1000000"))
    final, broker = await run(drawn, journal_dir=journal_dir)
    broker.assert_not_awaited()
    assert not final.get("results")


async def test_an_empty_chain_produces_no_orders_but_a_full_record(journal_dir):
    """Skipping is the most common outcome, and it has to read as a decision."""
    final, broker = await run(
        healthy_portfolio(), chain_rows=[], journal_dir=journal_dir
    )
    broker.assert_not_awaited()
    assert final["candidates"] == []
    assert final["allocations"] == []
    payload = [e for e in entries(journal_dir) if e["event"] == "cycle.complete"][-1]
    assert payload["payload"]["halted"] is False
    assert payload["payload"]["candidates"] == 0


async def test_a_broker_that_cannot_be_read_halts_rather_than_guesses(journal_dir):
    """No account means no limits can be checked. Trading blind is not the
    conservative option; refusing is."""
    with (
        patch(
            "flywheel.agent.nodes.get_account",
            new=AsyncMock(side_effect=RuntimeError("connection reset")),
        ),
        patch("flywheel.journal.writer.JOURNAL_DIR", journal_dir),
        patch("flywheel.execution.orders.call_tool", new=AsyncMock()) as broker,
    ):
        final = await build_graph().ainvoke(initial_state())

    assert final["halted"] is True
    assert "connection reset" in final["halt_reason"]
    broker.assert_not_awaited()
    assert any(e["event"] == "cycle.complete" for e in entries(journal_dir))


async def test_the_gap_is_measured_before_the_market_is_looked_at(journal_dir):
    """Ordering, which is the argument of the whole design.

    `mandate` runs second, on the positions already held, so the protection gap
    is an input to the cycle rather than a report written after it decided what
    it wanted. A cycle that measured risk last would be checking its homework.
    """
    exposed = [
        {
            "symbol": "SPY",
            "qty": "1200",
            "current_price": "500",
            "avg_entry_price": "500",
        }
    ]
    final, _ = await run(
        healthy_portfolio(), journal_dir=journal_dir, positions=exposed
    )

    # 600,000 of exposure against a 100,000 budget: 120,000 lost at -20%.
    assert final["protection_gap"] == pytest.approx(20_000, abs=1)
    assert final["book_complete"] is True

    stress = [e for e in entries(journal_dir) if e["event"] == "mandate.stress"]
    assert stress, "the ladder has to be journalled every cycle, gap or not"
    payload = stress[-1]["payload"]
    assert payload["mandate"] == "balanced"
    assert payload["unprotected_limit"] == pytest.approx(500_000)
    assert stress[-1]["severity"] == "breach"
    assert [r["shock"] for r in payload["ladder"]] == [-0.05, -0.10, -0.20, -0.35]
    # The 35% rung is deeper and worse, and it is disclosed rather than acted
    # on. The agent closes what it promised, and tells the client the rest.
    assert payload["worst_shock"] == -0.35
    assert payload["worst_gap"] > payload["gap"]


async def test_a_book_inside_its_budget_reports_no_gap(journal_dir):
    """The mandate sizing rule, from the other side.

    500,000 of exposure loses exactly the 100,000 budget at the 20% shock the
    mandate promises against. Nothing to close, and the journal says so at info
    rather than breach — an agent that raised a breach on every healthy cycle
    would train its reader to ignore it.

    The 35% rung still breaches here, by 75,000, and that is the reason the
    agent measures the promised shock rather than the worst one. Acting on the
    deepest row would make this perfectly compliant portfolio report a deficit
    it could only close by holding a quarter of its capital in equities.
    """
    inside = [
        {
            "symbol": "SPY",
            "qty": "1000",
            "current_price": "500",
            "avg_entry_price": "500",
        }
    ]
    final, _ = await run(healthy_portfolio(), journal_dir=journal_dir, positions=inside)
    assert final["protection_gap"] == 0.0
    stress = [e for e in entries(journal_dir) if e["event"] == "mandate.stress"][-1]
    assert stress["severity"] == "info"
    assert stress["payload"]["gap"] == 0.0
    assert stress["payload"]["worst_gap"] == pytest.approx(75_000, abs=1)


async def test_a_broker_that_cannot_list_positions_does_not_kill_the_cycle(journal_dir):
    """A missing ladder is a missing measurement, not a reason to stop.

    The drawdown kill-switch has already passed by this point; the risk limits
    downstream are unaffected. Halting here would mean one flaky read stops an
    agent whose other protections are all intact.
    """
    final, _ = await run(
        healthy_portfolio(),
        journal_dir=journal_dir,
        positions_error=RuntimeError("positions endpoint down"),
    )

    assert final["halted"] is False
    written = entries(journal_dir)
    assert any(e["event"] == "mandate.unreadable" for e in written)
    assert any(e["event"] == "cycle.complete" for e in written)


async def test_a_halted_cycle_never_reaches_the_ladder(journal_dir):
    """The halt edge routes past `mandate` straight to the journal."""
    drawn = healthy_portfolio(equity=Decimal("800000"), peak_equity=Decimal("1000000"))
    _, _ = await run(drawn, journal_dir=journal_dir)
    assert not [e for e in entries(journal_dir) if e["event"] == "mandate.stress"]


async def test_symbols_already_holding_a_position_are_not_traded_again(journal_dir):
    """Route drops anything on HOLD. The wheel writes one contract at a time."""
    busy = healthy_portfolio(
        wheels={s: WheelState(symbol=s, leg="PUT_OPEN") for s in SYMBOLS}
    )
    final, broker = await run(busy, journal_dir=journal_dir)
    assert final["actionable"] == []
    broker.assert_not_awaited()
