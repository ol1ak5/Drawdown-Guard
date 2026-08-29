"""The analyst that is given the material and not the answer.

The point of these tests is the second word. A model handed `uncovered_risk`
will restate it in nicer words and look like analysis doing it, so what is
checked here is mostly what the prompt does *not* contain, and then the two
rules that keep a finding from becoming an instruction: no forecast, and no
recommending that a risk be left alone.
"""

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from drawdownguard.agent.roles.reviewer import (
    MAX_FINDINGS,
    build_prompt,
    parse,
    render_facts,
    review,
)
from drawdownguard.risk.book import Book
from drawdownguard.risk.changes import Change, Diff
from drawdownguard.risk.stress import Holding, OptionLeg, Rung

BOOK = Book(
    holdings=[
        Holding(symbol="IWM", shares=100, price=296.65),
        Holding(symbol="BIL", shares=100, price=91.66, shocked=False),
        Holding(symbol="CASH", shares=5000, price=1.0, shocked=False),
    ],
    legs=[
        OptionLeg(
            symbol="XLF",
            right="P",
            strike=Decimal("54"),
            contracts=9,
            premium=Decimal("4.07"),
            spot=58.17,
        )
    ],
)
LADDER = [
    Rung(shock=-0.2, portfolio_loss=6000.0, protected_by_options=0.0, budget=9_997.84)
]
SOLD = Diff(changes=[Change(symbol="XLF", kind="shares", before=900, after=0)])
CONTEXT = {"mandate": "balanced", "budget": 9_997.84, "reference": 99_978.43}
KNOWN = {"IWM", "BIL", "XLF"}


def test_the_material_lists_holdings_and_legs_without_pairing_them():
    """The mismatch has to be the model's to find. See `render_facts`."""
    facts = render_facts(BOOK, SOLD, LADDER, CONTEXT)
    assert "IWM: 100 shares" in facts
    assert "XLF: long 9x 54 put" in facts
    assert "redundant" not in facts
    assert "uncovered" not in facts


def test_the_answer_is_not_handed_over():
    """`uncovered_risk` is the conclusion, and it is deliberately absent."""
    assert "risk not covered" not in render_facts(BOOK, SOLD, LADDER, CONTEXT)


def test_bills_are_labelled_rather_than_left_looking_like_equity():
    """BIL is a holding and is not exposure, and only the label says so.

    The bare `CASH` line is dropped -- it is a balance, not a position, and a
    model asked which positions need attention should not be handed one that
    cannot have an issue.
    """
    facts = render_facts(BOOK, SOLD, LADDER, CONTEXT)
    assert "BIL: 100 shares" in facts
    assert "does not fall with equities" in facts
    assert "CASH:" not in facts


def test_the_ladder_travels_so_depth_is_visible():
    assert "at -20%: loses 6,000" in render_facts(BOOK, SOLD, LADDER, CONTEXT)


def test_a_first_cycle_says_so_rather_than_reading_as_quiet():
    facts = render_facts(BOOK, Diff(first=True), LADDER, CONTEXT)
    assert "nothing to compare against" in facts


def test_the_question_travels_with_the_facts():
    assert "Finding the problem is the job" in build_prompt(BOOK, SOLD, LADDER, CONTEXT)


def test_a_finding_parses_into_issue_and_recommendation():
    got = parse(
        "XLF: 9 puts stand against a position that no longer exists "
        "-- review the XLF hedge for removal",
        KNOWN,
    )
    assert got[0]["symbol"] == "XLF"
    assert "no longer exists" in got[0]["issue"]
    assert got[0]["recommendation"].startswith("review")


def test_nothing_to_report_is_an_empty_list_not_a_silence():
    """The distinction the caller records as `answered`.

    A model that looked and found nothing and a model that did not answer are
    different mornings, and a journal that renders both as blank cannot tell
    them apart six days later.
    """
    assert parse("NONE: the book and the protection correspond", KNOWN) == []
    assert parse("", KNOWN) is None


def test_a_symbol_not_in_the_material_is_dropped():
    assert parse("TSLA: unhedged -- review it", KNOWN) is None


def test_a_forecast_is_dropped():
    assert parse("IWM: the market will fall -- buy puts", KNOWN) is None


def test_advice_to_leave_a_risk_uncovered_is_dropped():
    """The one finding that could cost money if a reader acted on it."""
    assert parse("IWM: exposure is small, no need to hedge -- ignore", KNOWN) is None


def test_a_wall_of_findings_is_dropped_whole():
    """Listing positions is not finding problems, and trimming would invent a rank."""
    text = "\n".join(f"IWM: issue {i} -- review it" for i in range(MAX_FINDINGS + 1))
    assert parse(text, KNOWN) is None


def test_prose_around_the_answer_is_ignored():
    text = "Here is what I found:\n```\nXLF: stale cover -- review for removal\n```"
    assert len(parse(text, KNOWN)) == 1


@pytest.mark.asyncio
async def test_a_dead_model_is_silence_not_an_excuse():
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("no key")
    assert await review(BOOK, SOLD, LADDER, CONTEXT, llm=llm) is None


@pytest.mark.asyncio
async def test_a_symbol_the_client_just_sold_is_still_known():
    """It has left the holdings and it is exactly what needs flagging."""
    llm = AsyncMock()
    llm.ainvoke.return_value = type(
        "M", (), {"content": "XLF: cover with nothing behind it -- review for removal"}
    )()
    got = await review(BOOK, SOLD, LADDER, CONTEXT, llm=llm)
    assert got[0]["symbol"] == "XLF"
