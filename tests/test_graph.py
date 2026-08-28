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

from drawdownguard.agent.graph import build_graph
from drawdownguard.agent.nodes import strategy
from drawdownguard.agent.state import initial_state
from drawdownguard.domain import Portfolio, Position
from drawdownguard.journal import writer
from drawdownguard.market.features import MarketSnapshot
from drawdownguard.risk.mandate import load_mandate

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
        "positions": {s: Position(symbol=s) for s in SYMBOLS},
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



def only_sleeve(plan: dict) -> dict:
    """The one sleeve in a plan, for tests whose book holds a single symbol.

    `protection.plan` carries a list now: each symbol is hedged on its own
    underlying, because a put matched by notional on the largest holding
    under-covers the higher-beta ones. Tests written against a one-symbol book
    are asking about its only sleeve, and say so here rather than indexing
    into a list at every call site.
    """
    sleeves = plan["payload"]["sleeves"]
    assert len(sleeves) == 1, f"expected one sleeve, got {len(sleeves)}"
    return sleeves[0]



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
            "drawdownguard.agent.nodes.get_account",
            new=AsyncMock(return_value=(portfolio, [])),
        ),
        patch("drawdownguard.agent.nodes.get_positions", new=reader),
        patch("drawdownguard.market.chain.load_chain", new=chain),
        patch("drawdownguard.journal.writer.JOURNAL_DIR", journal_dir),
        patch(
            "drawdownguard.execution.orders.call_tool",
            new=AsyncMock(return_value=BROKER_FILLED),
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
    """Doing nothing is the most common outcome, and it has to read as a
    decision rather than as an absence.

    With no chain there is nothing to price, so no remedy can be chosen and no
    order can be sent -- and the cycle still writes its entry. A run that
    stopped without journalling is indistinguishable, a week later, from a run
    that crashed.
    """
    final, broker = await run(
        healthy_portfolio(), chain_rows=[], journal_dir=journal_dir
    )
    broker.assert_not_awaited()
    assert not final.get("results")
    payload = [e for e in entries(journal_dir) if e["event"] == "cycle.complete"][-1]
    assert payload["payload"]["halted"] is False
    assert payload["payload"]["submitted"] == 0


async def test_a_broker_that_cannot_be_read_halts_rather_than_guesses(journal_dir):
    """No account means no limits can be checked. Trading blind is not the
    conservative option; refusing is."""
    with (
        patch(
            "drawdownguard.agent.nodes.get_account",
            new=AsyncMock(side_effect=RuntimeError("connection reset")),
        ),
        patch("drawdownguard.journal.writer.JOURNAL_DIR", journal_dir),
        patch("drawdownguard.execution.orders.call_tool", new=AsyncMock()) as broker,
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

    # 600,000 of exposure against a 100,000 budget. Shares have no floor, so
    # the worst is losing all 600,000 and the gap is the 500,000 the budget
    # does not cover -- not the 20,000 that a -20% probe used to report.
    assert final["uncovered_risk"] == pytest.approx(500_000, abs=1)
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
    # The ladder is disclosure now, not the trigger. Its deepest rung is
    # milder than the number the agent acts on -- 110,000 at -35% against a
    # worst case of 600,000 -- because a rung is one price and the gap is
    # every price. Both are published; only one decides.
    assert payload["worst_shortfall"] < payload["uncovered_risk"]
    assert payload["shortfall_at_shock"] == pytest.approx(20_000, abs=1)


async def test_a_book_inside_its_budget_reports_no_gap(journal_dir):
    """The mandate sizing rule, from the other side.

    80,000 of exposure against a 100,000 budget. Shares have no floor, so the
    worst this book can do is lose all 80,000 of it -- which is inside what the
    client agreed to, and there is nothing to close. The journal says so at
    info rather than breach: an agent that raised a breach on every healthy
    cycle would train its reader to ignore it.

    This used to hold 500,000, on the reasoning that 500,000 loses exactly the
    budget at the 20% shock the mandate names. That reasoning was the defect.
    A book is not safe because it survives the one depth somebody chose -- the
    same 500,000 can lose all of itself, five times the promise, and checking
    -20% found the single point where it happened to hold.
    """
    inside = [
        {
            "symbol": "SPY",
            "qty": "160",
            "current_price": "500",
            "avg_entry_price": "500",
        }
    ]
    final, _ = await run(healthy_portfolio(), journal_dir=journal_dir, positions=inside)
    assert final["uncovered_risk"] == 0.0
    stress = [e for e in entries(journal_dir) if e["event"] == "mandate.stress"][-1]
    assert stress["severity"] == "info"
    assert stress["payload"]["uncovered_risk"] == 0.0
    assert stress["payload"]["worst_shortfall"] == 0.0
    assert stress["payload"]["worst_case"] == pytest.approx(80_000, abs=1)


async def test_a_broker_that_cannot_list_positions_stops_the_cycle(journal_dir):
    """Not knowing what is held is not evidence that the promise is kept.

    This used to continue on the reasoning that the risk limits downstream were
    unaffected -- true of a strategy that sells, false of one that measures a
    book. Returning an empty state left `uncovered_risk` at its initial 0.0 and
    `book_complete` at True, so a cycle that read nothing came out
    byte-identical to a healthy one and the status page republished yesterday's
    numbers as today's.

    The account read one node above already fails closed. This one failing open
    was the asymmetry, and it was on the side that matters.
    """
    final, _ = await run(
        healthy_portfolio(),
        journal_dir=journal_dir,
        positions_error=RuntimeError("positions endpoint down"),
    )

    assert final["halted"] is True
    written = entries(journal_dir)
    unreadable = [e for e in written if e["event"] == "mandate.unreadable"]
    assert unreadable and unreadable[-1]["severity"] == "breach"
    # And it still journals, which is the whole reason the halt routes here.
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

# One payload answers both calls the execute node makes: placing the order,
# which wants an id back, and reading the fill, which wants a status and a
# quantity. `filled_qty` is deliberately larger than any order these tests
# send, so a test asserting "this reached the broker" gets `order.filled`.
BROKER_FILLED = {
    "data": {
        "id": "abc-123",
        "status": "filled",
        "filled_qty": "99",
        "filled_avg_price": "1.00",
    }
}

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

    assert final["protection"][0].kind == "collar"
    plan = [e for e in entries(journal_dir) if e["event"] == "protection.plan"][-1]
    assert only_sleeve(plan)["chosen"] == "collar"
    assert only_sleeve(plan)["symbol"] == "SPY"
    # The reason travels with the answer rather than being reconstructed later.
    assert "richer leg" in only_sleeve(plan)["because"]
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

    assert final["protection"][0].kind == "protective_put"
    plan = [e for e in entries(journal_dir) if e["event"] == "protection.plan"][-1]
    assert "underpriced upside" in only_sleeve(plan)["because"]

    # And back again on the original chain, with nothing about the client
    # touched in between.
    again, _ = await run(
        healthy_portfolio(), chain_rows=CHAIN, journal_dir=journal_dir,
        positions=EXPOSED,
    )
    assert again["protection"][0].kind == "collar"


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
    priced = {o["kind"]: o for o in only_sleeve(plan)["offers"]}

    # Two, not three: no shipped mandate may sell the client's shares, and the
    # exclusion is stated rather than left as a silence.
    assert set(priced) == {"protective_put", "collar"}
    assert plan["payload"]["excluded"] == ["reduce_exposure"]
    assert len(only_sleeve(plan)["offers"]) == 2
    assert all(o["covers_the_risk"] for o in priced.values())

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
        patch("drawdownguard.agent.nodes.strategy", return_value=plain),
        patch("drawdownguard.agent.nodes.load_mandate", return_value=permitted),
    ):
        final, _ = await run(
            healthy_portfolio(),
            chain_rows=CHAIN,
            journal_dir=journal_dir,
            positions=EXPOSED,
        )

    plan = [e for e in entries(journal_dir) if e["event"] == "protection.plan"][-1]
    kinds = {o["kind"] for o in only_sleeve(plan)["offers"]}
    assert kinds == {"protective_put", "collar", "reduce_exposure"}
    assert plan["payload"]["excluded"] == []
    # Offered, priced, and passed over: the one-way door loses to a remedy that
    # expires even though it costs no cash at all.
    priced = {o["kind"]: o for o in only_sleeve(plan)["offers"]}
    assert priced["reduce_exposure"]["permanent"] is True
    assert priced["reduce_exposure"]["premium_cost"] == 0.0
    assert only_sleeve(plan)["chosen"] == "collar"


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
    # The assumption is gone because the approximation is gone: each sleeve
    # is hedged on its own underlying, so nothing is being assumed uniform.
    assert [s["symbol"] for s in plan["payload"]["sleeves"]]
    for sleeve in plan["payload"]["sleeves"]:
        assert sleeve["budget"] > 0


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

    given = [
        e
        for e in entries(journal_dir)
        if e["event"] == "protection.recommended_release"
    ]
    assert given, "a spent leg has to be identified even while the gap is open"
    # Recommended, not done, and the journal says which. An `OptionLeg` carries
    # no expiry, so no closing order can be built from it -- and this used to
    # be reported as a completed handback while the puts stayed in the account,
    # so the next cycle found them, called them spent again, and bought fresh
    # protection on top. 20,130 of premium over five cycles on the demo book.
    assert given[-1]["payload"]["executed"] is False
    assert given[-1]["severity"] == "breach"
    assert given[-1]["payload"]["reason"] == "spent"
    assert given[-1]["payload"]["contracts"] == 2
    # A leg worth nothing at the promised shock can still be the floor deeper
    # down, so the whole-descent measure prices the handback rather than
    # calling it free. The trade is still right -- the client pays to carry a
    # leg that is not holding the promise up -- but the journal reports what it
    # costs instead of asserting it costs nothing.
    assert (
        given[-1]["payload"]["headroom_after"]
        < given[-1]["payload"]["headroom_before"]
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
    assert final["protection"] == []
    assert final["uncovered_risk"] == pytest.approx(500_000, abs=1)
    assert final["halted"] is False, "one bad chain is not a reason to stop"


async def test_a_book_inside_its_budget_is_offered_no_protection(journal_dir):
    """No gap, no plan, no journal noise. An agent that produced a protection
    plan every cycle would be an agent with a reason to find a gap."""
    inside = [
        {
            "symbol": "SPY",
            "qty": "160",
            "current_price": "500",
            "avg_entry_price": "500",
        }
    ]
    final, _ = await run(
        healthy_portfolio(), chain_rows=CHAIN, journal_dir=journal_dir, positions=inside
    )
    assert final["protection"] == []
    assert not [e for e in entries(journal_dir) if e["event"] == "protection.plan"]


def test_the_wheel_cannot_come_back_into_the_cycle():
    """A guard, not a description.

    `route`, `candidates` and `optimize` were the options wheel. `route` asked
    a state machine what to do next, and on a book holding shares that machine
    answers SELL_CALL -- unconditionally, on every symbol, for income.

    On the client book this project now describes that path would have sold
    calls against all of the client's equity and capped the upside of a mandate
    that never asked for it, on the first morning of an eight-day unattended
    run. Re-adding any of them to `NODES` should fail here rather than in the
    journal a week later.

    Selling a call is still allowed: a collar sells one. The difference is that
    a collar sells it to finance a put, sized against the promise, and only
    when the chain favours it -- a decision that belongs to `protect`.
    """
    from drawdownguard.agent.graph import NODES

    names = [name for name, _ in NODES]
    assert names == [
        "reconcile",
        "mandate",
        "protect",
        "execute",
        "journal",
    ]


def test_no_model_is_called_for_an_answer_nothing_reads():
    """`regime` classified the market with a language model every cycle and
    was read by no decision. It narrowed the delta band and the size multiplier
    once; both belonged to the options overlay and left with it, leaving a paid
    API call that could not reach anything.

    The analyst module and its tests are kept. There is a real job here --
    reading what the options market charges for protection, and why -- and it
    becomes reachable once the agent can buy ahead of need. It returns when it
    decides something, and this test should be deleted then.
    """
    from drawdownguard.agent.graph import NODES

    assert "regime" not in [name for name, _ in NODES]


async def test_the_halt_file_stops_the_cycle_and_still_journals(journal_dir, tmp_path):
    """The kill switch was enforced outside the graph, and only on one path.

    `halt_file_present` was called from `healthcheck.py` alone. The scheduled
    workflow runs that first, so the deployed path was covered -- and
    `run_cycle.py`, the entry point a human types, was not. Somebody stopping
    the agent by hand and then running a cycle by hand got a cycle.

    Checked inside `reconcile` now, so there is one answer to "is this agent
    stopped" and so a halt writes an entry like every other outcome. A HALT day
    that recorded nothing would be indistinguishable from a day it was dead.
    """
    from drawdownguard.agent.middleware import guards

    halt = tmp_path / "HALT"
    halt.touch()
    with patch.object(guards, "HALT_FILE", halt):
        final, broker = await run(healthy_portfolio(), journal_dir=journal_dir)

    assert final["halted"] is True
    assert "stopped by hand" in final["halt_reason"]
    broker.assert_not_awaited()
    complete = [e for e in entries(journal_dir) if e["event"] == "cycle.complete"]
    assert complete and complete[-1]["payload"]["halted"] is True


def occ_for(strike: int, expiry: date, right: str = "P") -> str:
    """The OCC symbol the book parses a held leg out of."""
    return f"SPY{expiry:%y%m%d}{right}{int(strike * 1000):08d}"


async def test_a_handback_that_closes_the_gap_still_reaches_the_broker(journal_dir):
    """The path that had no order on it, and the journal that said it did.

    A redundant release keeps headroom at or above the margin by construction,
    which is to say it leaves the worst case inside the budget -- so `gap <= 0`
    is the ordinary outcome of handing protection back, not a corner. That
    return carried `released` and the journal line saying "executed": True, and
    dropped the orders that would have executed it. `execute` then read the
    empty list the state was initialised with and sent nothing, so the puts
    stayed in the account while the record said they had gone.
    """
    # Eight hundred shares behind twelve puts: the shape the third day of the
    # scenario produces, where the client sold stock and the hedge bought
    # against the larger book is now bigger than the book it stands behind.
    # Four contracts are redundant, and the eight that remain still hold the
    # promise with room to spare -- which is exactly why `gap` comes out zero.
    expiry = date.today() + timedelta(days=28)
    over_protected = [
        {
            "symbol": "SPY",
            "qty": "800",
            "current_price": "500",
            "avg_entry_price": "500",
        },
        {
            "symbol": occ_for(440, expiry),
            "qty": "12",
            "current_price": "4.00",
            "avg_entry_price": "4.00",
        },
    ]
    final, _ = await run(
        healthy_portfolio(),
        chain_rows=CHAIN,
        journal_dir=journal_dir,
        positions=over_protected,
    )

    released = [e for e in entries(journal_dir) if e["event"] == "protection.released"]
    assert released, "twelve puts behind eight hundred shares is four to give back"
    assert released[-1]["payload"]["executed"] is True

    # The state carries the orders out of `protect`...
    assert final["release_orders"], "the orders were priced and then dropped"
    # ...and `execute` actually sent them.
    sent = [e for e in entries(journal_dir) if e["event"] == "order.filled"]
    assert sent, "a handback reported as executed has to have reached the broker"
    assert any(e["payload"]["contracts"] < 0 for e in sent)


async def test_a_dry_run_is_not_journalled_as_a_refusal(journal_dir):
    """The gate runs before `dry_run` is looked at, so an order that reaches
    the dry-run check was approved and then deliberately not sent.

    It used to be written as `order.refused` at severity `veto` -- the same
    record a genuine rejection leaves -- and the status page reads that
    severity as "rejected", so a clean dry run displayed two approved orders in
    red. Three outcomes, three names.
    """
    # The shares have to actually be held, or `_permitted_purpose` refuses the
    # put for standing behind nothing -- which is a real refusal and would be
    # the wrong thing for this test to be measuring.
    holds_the_shares = healthy_portfolio(
        positions={
            "SPY": Position(symbol="SPY", leg="SHARES", shares=1200),
            **{s: Position(symbol=s) for s in SYMBOLS if s != "SPY"},
        }
    )
    with (
        patch(
            "drawdownguard.agent.nodes.get_account",
            new=AsyncMock(return_value=(holds_the_shares, [])),
        ),
        patch(
            "drawdownguard.agent.nodes.get_positions",
            new=AsyncMock(return_value=EXPOSED),
        ),
        patch(
            "drawdownguard.market.chain.load_chain",
            new=AsyncMock(
                side_effect=lambda s, r, *a, **k: [
                    row for row in CHAIN if row.get("right", r) == r
                ]
            ),
        ),
        patch("drawdownguard.journal.writer.JOURNAL_DIR", journal_dir),
        patch("drawdownguard.execution.orders.call_tool", new=AsyncMock()) as broker,
    ):
        await build_graph().ainvoke(initial_state(dry_run=True))

    broker.assert_not_awaited()
    written = entries(journal_dir)

    simulated = [e for e in written if e["event"] == "order.simulated"]
    assert simulated, "a dry run has to leave a record of what it would have sent"
    assert all(e["severity"] == "info" for e in simulated)
    assert all("dry run" in e["payload"]["reason"] for e in simulated)

    refused = [e for e in written if e["event"] == "order.refused"]
    assert not refused, "unexpected refusals: " + "; ".join(
        f"{e['payload']['occ_symbol']} x{e['payload']['contracts']}: "
        f"{e['payload']['reason']}"
        for e in refused
    )


async def test_a_dry_run_still_reports_a_real_refusal_as_one(journal_dir):
    """The other half, and the reason the verdict is carried rather than
    inferred from `dry_run`.

    The gate runs *before* the dry-run check, so an order refused during a dry
    run was genuinely refused. Reading "not submitted, and this was a dry run"
    as "simulated" would hide exactly the finding a dry run is performed to
    surface -- here, a put bought against shares the client does not own.
    """
    with (
        patch(
            "drawdownguard.agent.nodes.get_account",
            new=AsyncMock(return_value=(healthy_portfolio(), [])),  # holds no shares
        ),
        patch(
            "drawdownguard.agent.nodes.get_positions",
            new=AsyncMock(return_value=EXPOSED),
        ),
        patch(
            "drawdownguard.market.chain.load_chain",
            new=AsyncMock(
                side_effect=lambda s, r, *a, **k: [
                    row for row in CHAIN if row.get("right", r) == r
                ]
            ),
        ),
        patch("drawdownguard.journal.writer.JOURNAL_DIR", journal_dir),
        patch("drawdownguard.execution.orders.call_tool", new=AsyncMock()) as broker,
    ):
        await build_graph().ainvoke(initial_state(dry_run=True))

    broker.assert_not_awaited()
    refused = [e for e in entries(journal_dir) if e["event"] == "order.refused"]
    assert refused, "the gate refused this and the journal has to say so"
    assert all(e["severity"] == "veto" for e in refused)
    assert not [e for e in entries(journal_dir) if e["event"] == "order.simulated"]


async def test_an_order_the_market_walked_away_from_is_not_reported_as_bought(
    journal_dir,
):
    """The failure this cycle could not previously describe.

    An option order is a day limit priced at the ask the decision was made on.
    On 2026-08-28 two protective puts were accepted, the ask moved a few cents
    while the cycle was still running, and both sat unfilled until the close.
    The journal said `submitted: 2` and the account held no options -- and
    nothing in the record connected those two facts.

    Accepted is now read back and reported as what it is: working, not bought,
    and a breach because the book is still over its budget.
    """
    accepted_unfilled = {
        "data": {
            "id": "abc-123",
            "status": "new",
            "filled_qty": "0",
            "filled_avg_price": None,
        }
    }
    holds_the_shares = healthy_portfolio(
        positions={"SPY": Position(symbol="SPY", leg="SHARES", shares=1200)}
    )
    with (
        patch(
            "drawdownguard.agent.nodes.get_account",
            new=AsyncMock(return_value=(holds_the_shares, [])),
        ),
        patch(
            "drawdownguard.agent.nodes.get_positions",
            new=AsyncMock(return_value=EXPOSED),
        ),
        patch(
            "drawdownguard.market.chain.load_chain",
            new=AsyncMock(
                side_effect=lambda s, r, *a, **k: [
                    row for row in CHAIN if row.get("right", r) == r
                ]
            ),
        ),
        patch("drawdownguard.journal.writer.JOURNAL_DIR", journal_dir),
        patch(
            "drawdownguard.execution.orders.call_tool",
            new=AsyncMock(return_value=accepted_unfilled),
        ),
    ):
        await build_graph().ainvoke(initial_state())

    written = entries(journal_dir)
    working = [e for e in written if e["event"] == "order.working"]
    assert working, "an accepted order that bought nothing has to say so"
    assert all(e["severity"] == "breach" for e in working), (
        "the promise is still broken, which is what breach is for"
    )
    assert all(e["payload"]["filled"] == 0 for e in working)
    assert not [e for e in written if e["event"] == "order.filled"]
