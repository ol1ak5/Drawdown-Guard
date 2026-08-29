"""The one place a model's answer becomes a trade.

So these tests are not about taste. They are about what the model is even able
to reach: that a structure leaving the promise broken never enters the choice,
that a permanent one never wins while something reversible closes the risk, and
that every way the answer can be malformed lands on the rule instead of on a
guess.
"""

from unittest.mock import AsyncMock

import pytest

from drawdownguard.agent.roles.chooser import (
    admit,
    build_prompt,
    eligible,
    parse,
    pick,
    render_facts,
)
from drawdownguard.risk.remedy import Remedy


def remedy(kind: str, covers: bool = True, permanent: bool = False, **kw) -> Remedy:
    """A priced structure.

    `covers_the_risk` and `permanent` are properties rather than fields -- one
    reads `uncovered_after`, the other reads shares actually sold -- so they are
    set here the only way they can be set, through the facts they are derived
    from. A fixture that could assert them directly would be testing a different
    object from the one the agent builds.
    """
    return Remedy(
        kind=kind,
        describe=kw.get("describe", f"a {kind}"),
        legs=[],
        shares_sold={"XLF": 100} if permanent else {},
        premium_cost=kw.get("premium_cost", 1000.0),
        forgone_upside=kw.get("forgone_upside", 0.0),
        upside_measured_at=0.2,
        uncovered_before=5000.0,
        uncovered_after=0.0 if covers else 5_000.0,
        ceiling_pct=kw.get("ceiling_pct", 0.0),
        protection_iv=kw.get("protection_iv", 0.22),
        financing_iv=kw.get("financing_iv", 0.24),
    )


PUT = remedy("protective_put")
RING = remedy("collar", premium_cost=-1287.0, forgone_upside=1793.0, ceiling_pct=6.5)
CHOICES = {"XLF": [PUT, RING]}


def test_a_structure_that_leaves_the_promise_broken_is_never_offered():
    """The model cannot choose it because it never sees it."""
    kinds = [r.kind for r in eligible([PUT, remedy("collar", covers=False)])]
    assert kinds == ["protective_put"]


def test_a_permanent_structure_loses_to_anything_that_expires():
    sale = remedy("reduce_exposure", permanent=True)
    assert [r.kind for r in eligible([PUT, sale])] == ["protective_put"]


def test_a_permanent_structure_stands_when_nothing_else_closes_the_risk():
    """Not a preference for selling -- the only thing left that keeps the promise."""
    sale = remedy("reduce_exposure", permanent=True)
    assert [r.kind for r in eligible([remedy("collar", covers=False), sale])] == [
        "reduce_exposure"
    ]


def test_facts_price_both_legs_of_the_choice():
    facts = render_facts(CHOICES)
    assert "cash cost: -1,287" in facts
    assert "upside given up: 1,793" in facts
    assert "call vol: 24.0%" in facts


def test_a_hedge_with_no_ceiling_says_the_upside_is_untouched():
    assert "the upside is untouched" in render_facts({"XLF": [PUT]})


def test_the_rulebook_travels_with_the_facts():
    assert "Never predict where the market" in build_prompt(CHOICES)


def test_a_clean_answer_parses():
    picks = parse("XLF: collar -- the call sells 2 points richer than the put")
    assert picks["XLF"][0] == "collar"


def test_prose_around_the_answer_is_ignored():
    text = "Here is my view:\n```\nXLF: collar -- richer call\n```\nHope that helps."
    assert set(parse(text)) == {"XLF"}


def test_a_line_without_a_reason_is_dropped():
    assert parse("XLF: collar") == {}


def test_a_rambling_reason_is_dropped():
    assert parse("XLF: collar -- " + " ".join(["word"] * 80)) == {}


def test_an_invented_kind_is_refused_rather_than_resolved_generously():
    """Evidence the answer was not grounded in the facts. The rule takes it."""
    assert admit({"XLF": ("iron_condor", "sounds good")}, CHOICES) == {}


def test_a_kind_from_the_wrong_symbol_is_refused():
    assert admit({"IWM": ("collar", "richer call")}, CHOICES) == {}


def test_an_admitted_pick_carries_the_remedy_itself():
    """Not the kind string. The order is built from the object that was priced."""
    got = admit({"XLF": ("collar", "richer call")}, CHOICES)
    assert got["XLF"][0] is RING


@pytest.mark.asyncio
async def test_no_sleeves_means_no_call_at_all():
    llm = AsyncMock()
    assert await pick({}, llm=llm) == {}
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_dead_model_hands_every_sleeve_to_the_rule():
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("no key")
    assert await pick(CHOICES, llm=llm) == {}


@pytest.mark.asyncio
async def test_a_good_answer_comes_back_as_the_priced_remedy():
    llm = AsyncMock()
    llm.ainvoke.return_value = type(
        "M", (), {"content": "XLF: collar -- the call sells at 24.0% against 22.0%"}
    )()
    got = await pick(CHOICES, llm=llm)
    assert got["XLF"][0] is RING
    assert "24.0%" in got["XLF"][1]
