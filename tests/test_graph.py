"""The cycle graph, with the market and the broker mocked out.

Nothing here touches Alpaca. The claims worth testing are about control flow —
that a halted cycle still writes a journal entry, that a halted cycle submits
nothing — and those are exactly the claims a live test would verify least
reliably, because a live run that happens not to trade proves nothing.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from flywheel.agent.graph import build_graph
from flywheel.agent.nodes import strategy
from flywheel.agent.state import initial_state
from flywheel.domain import Portfolio, WheelState
from flywheel.journal import writer
from flywheel.market.features import MarketSnapshot
from flywheel.risk.mandate import load_mandate

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
    portfolio,
    chain_rows=None,
    journal_dir=None,
    positions=None,
    positions_error=None,
    chain_error=None,
):
    """Invoke the graph with the broker and the market replaced.

    `positions_error` belongs here rather than in a `patch` around the call:
    this helper patches `get_positions` itself, so an outer patch of the same
    name is shadowed by the inner one and the test silently exercises nothing.

    The chain mock filters by `right`, because two nodes now ask for chains and
    they want different things: `protect` needs puts and calls in the same cycle
    to price a collar, while `candidates` asks for one side at a time. Handing
    both nodes the same undifferentiated list would let the collar sell a put as
    though it were a call and still pass.
    """
    chain_rows = chain_rows if chain_rows is not None else []
    reader = (
        AsyncMock(side_effect=positions_error)
        if positions_error
        else AsyncMock(return_value=positions if positions is not None else [])
    )

    def by_right(symbol, right, *args, **kwargs):
        return [row for row in chain_rows if row.get("right", right) == right]

    chain = (
        AsyncMock(side_effect=chain_error)
        if chain_error
        else AsyncMock(side_effect=by_right)
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
        patch("flywheel.market.chain.load_chain", new=chain),
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


# --- closing the gap --------------------------------------------------------


def chain_row(
    strike: float, bid: float, ask: float, right: str, iv: float = 0.20
) -> dict:
    """One tradable contract, with every field both consumers read."""
    return {
        "occ_symbol": f"SPY__{right}{strike:.0f}",
        "strike": Decimal(str(strike)),
        "expiry": date.today() + timedelta(days=28),
        "right": right,
        "bid": Decimal(str(bid)),
        "ask": Decimal(str(ask)),
        "open_interest": 5_000,
        "implied_vol": iv,
    }


# A put that pays 40 a share at the promised shock, and a call above spot rich
# enough to fund it. Together they make all three remedies available, which is
# what lets a test about *choosing* mean anything.
CHAIN = [chain_row(440, 3.90, 4.00, "P"), chain_row(540, 5.00, 5.10, "C")]

EXPOSED = [
    {"symbol": "SPY", "qty": "1200", "current_price": "500", "avg_entry_price": "500"}
]


async def test_the_gap_is_closed_the_way_todays_prices_favour(journal_dir):
    """The choice is made on the chain, and the journal has to show the working.

    Both option remedies close this gap. The call is quoted at the same implied
    volatility as the put, so selling it finances the protection on terms that
    are fair by the market's own measure, and the collar comes out. Nothing was
    read from a config file to get here.
    """
    final, _ = await run(
        healthy_portfolio(), chain_rows=CHAIN, journal_dir=journal_dir,
        positions=EXPOSED,
    )

    assert final["protection"].kind == "collar"
    plan = [e for e in entries(journal_dir) if e["event"] == "protection.plan"][-1]
    assert plan["payload"]["chosen"] == "collar"
    assert plan["payload"]["symbol"] == "SPY"
    # The reason travels with the answer rather than being reconstructed later.
    assert "richer leg" in plan["payload"]["because"]
    assert plan["severity"] == "breach", "a plan is not a position"


async def test_the_same_client_gets_a_different_answer_from_a_different_chain(
    journal_dir,
):
    """What a stated preference could never do.

    Same mandate, same book, same budget, same shock, same puts. Only the call's
    implied volatility differs, and the answer flips. This is the test that
    replaced one asserting `conservative` and `balanced` diverge: they diverged
    because a config file said so, which proved that the file was being read,
    not that anything was being decided.
    """
    cheap_calls = [CHAIN[0], chain_row(540, 5.00, 5.10, "C", iv=0.10)]
    final, _ = await run(
        healthy_portfolio(), chain_rows=cheap_calls, journal_dir=journal_dir,
        positions=EXPOSED,
    )

    assert final["protection"].kind == "protective_put"
    plan = [e for e in entries(journal_dir) if e["event"] == "protection.plan"][-1]
    assert "underpriced upside" in plan["payload"]["because"]

    # And back again on the original chain, with nothing about the client
    # touched in between.
    again, _ = await run(
        healthy_portfolio(), chain_rows=CHAIN, journal_dir=journal_dir,
        positions=EXPOSED,
    )
    assert again["protection"].kind == "collar"


async def test_the_remedy_that_was_declined_is_recorded_too(journal_dir):
    """An agent that logged only what it did would be unfalsifiable.

    The reader has to be able to see the alternative and check the comparison
    that rejected it, rather than take the answer on trust.
    """
    final, _ = await run(
        healthy_portfolio(), chain_rows=CHAIN, journal_dir=journal_dir,
        positions=EXPOSED,
    )
    plan = [e for e in entries(journal_dir) if e["event"] == "protection.plan"][-1]
    priced = {o["kind"]: o for o in plan["payload"]["offers"]}

    # Two, not three: no shipped mandate may sell the client's shares, and the
    # exclusion is stated rather than left as a silence.
    assert set(priced) == {"protective_put", "collar"}
    assert plan["payload"]["excluded"] == ["reduce_exposure"]
    assert len(final["protection_options"]) == 2
    assert all(o["closes_the_gap"] for o in priced.values())

    # The two prices in the two units they are actually paid in, plus the terms
    # of the financing that decided between them.
    assert priced["protective_put"]["forgone_upside"] == 0.0
    assert priced["collar"]["premium_cost"] < priced["protective_put"]["premium_cost"]
    assert priced["collar"]["financed_fairly"] is True
    assert priced["collar"]["upside_price"] > 0
    assert priced["protective_put"]["financed_fairly"] is None


async def test_the_sale_appears_only_for_a_client_who_granted_it(journal_dir):
    """Both directions of the switch, because only one of them is the default.

    No shipped mandate may sell, so the excluded path is what every other test
    in this file already exercises. What needs its own test is the granted one:
    the machinery must still work for a client who says yes, or the permission
    is decorative and the default is not a choice.

    Granted, the sale is computed and offered. It is still not taken here --
    `choose` prefers anything that expires -- and the record shows both facts,
    which is the point of journalling the declined options at all.
    """
    plain = {**strategy(), "mandate": "balanced"}
    permitted = load_mandate("balanced").model_copy(
        update={"allow_reduce_exposure": True}
    )
    with (
        patch("flywheel.agent.nodes.strategy", return_value=plain),
        patch("flywheel.agent.nodes.load_mandate", return_value=permitted),
    ):
        final, _ = await run(
            healthy_portfolio(),
            chain_rows=CHAIN,
            journal_dir=journal_dir,
            positions=EXPOSED,
        )

    kinds = {remedy.kind for remedy in final["protection_options"]}
    assert kinds == {"protective_put", "collar", "reduce_exposure"}
    plan = [e for e in entries(journal_dir) if e["event"] == "protection.plan"][-1]
    assert plan["payload"]["excluded"] == []
    # Offered, priced, and passed over: the one-way door loses to a remedy that
    # expires even though it costs no cash at all.
    priced = {o["kind"]: o for o in plan["payload"]["offers"]}
    assert priced["reduce_exposure"]["permanent"] is True
    assert priced["reduce_exposure"]["premium_cost"] == 0.0
    assert plan["payload"]["chosen"] == "collar"


async def test_the_uniform_shock_assumption_is_written_down_next_to_the_hedge(
    journal_dir,
):
    """One symbol carries the whole hedge, and that only works because the
    ladder moves every equity holding by the same percentage. Real declines do
    not, so the assumption is recorded rather than left to be discovered."""
    _, _ = await run(
        healthy_portfolio(), chain_rows=CHAIN, journal_dir=journal_dir,
        positions=EXPOSED,
    )
    plan = [e for e in entries(journal_dir) if e["event"] == "protection.plan"][-1]
    assert "uniform shock" in plan["payload"]["assumes"]


async def test_spent_protection_is_handed_back_before_anything_is_bought(journal_dir):
    """The roll, in the live cycle.

    A 380 put against a 500 stock pays nothing at -20%, so it is not holding the
    promise up and it goes back. The gap it leaves behind is unchanged, and the
    same cycle then buys a strike that actually reaches.
    """
    held = [
        *EXPOSED,
        {
            "symbol": "SPY260925P00380000",
            "qty": "2",
            "current_price": "1.00",
            "avg_entry_price": "5.00",
        },
    ]
    final, _ = await run(
        healthy_portfolio(), chain_rows=CHAIN, journal_dir=journal_dir, positions=held
    )

    given = [e for e in entries(journal_dir) if e["event"] == "protection.released"]
    assert given, "a spent leg has to be released even while the gap is open"
    assert given[-1]["payload"]["reason"] == "spent"
    assert given[-1]["payload"]["contracts"] == 2
    # Releasing it cost no headroom, which is what makes it safe to do first.
    assert given[-1]["payload"]["headroom_after"] == pytest.approx(
        given[-1]["payload"]["headroom_before"]
    )
    # And the cycle still closed the gap afterwards.
    assert final["protection"] is not None


async def test_a_chain_that_cannot_be_read_leaves_the_gap_open_and_says_so(journal_dir):
    """The failure that must never be quiet.

    No chain means no protection can be priced, so the client's promise stays
    broken. That is a breach in the journal and an unclosed gap in the state —
    not a skipped cycle, and not a zero.
    """
    final, _ = await run(
        healthy_portfolio(),
        chain_rows=CHAIN,
        journal_dir=journal_dir,
        positions=EXPOSED,
        chain_error=RuntimeError("chain endpoint down"),
    )

    written = entries(journal_dir)
    failed = [e for e in written if e["event"] == "protection.chain_unreadable"]
    assert failed and failed[-1]["severity"] == "breach"
    assert final["protection"] is None
    assert final["protection_gap"] == pytest.approx(20_000, abs=1)
    assert final["halted"] is False, "one bad chain is not a reason to stop"


async def test_a_book_inside_its_budget_is_offered_no_protection(journal_dir):
    """No gap, no plan, no journal noise. An agent that produced a protection
    plan every cycle would be an agent with a reason to find a gap."""
    inside = [
        {
            "symbol": "SPY",
            "qty": "1000",
            "current_price": "500",
            "avg_entry_price": "500",
        }
    ]
    final, _ = await run(
        healthy_portfolio(), chain_rows=CHAIN, journal_dir=journal_dir, positions=inside
    )
    assert final["protection"] is None
    assert not [e for e in entries(journal_dir) if e["event"] == "protection.plan"]


async def test_symbols_already_holding_a_position_are_not_traded_again(journal_dir):
    """Route drops anything on HOLD. The wheel writes one contract at a time."""
    busy = healthy_portfolio(
        wheels={s: WheelState(symbol=s, leg="PUT_OPEN") for s in SYMBOLS}
    )
    final, broker = await run(busy, journal_dir=journal_dir)
    assert final["actionable"] == []
    broker.assert_not_awaited()
