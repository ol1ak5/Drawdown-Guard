"""The first language model this program puts *before* an action.

The explainer speaks after everything is settled, so its worst failure is an
unclear sentence. This one speaks before `protect` runs, which changes what has
to be tested: not only that it does not forecast, but that it cannot talk the
agent out of keeping the client's promise.

It cannot, structurally -- nothing downstream reads the prose. `check` is the
second line, and these tests are about that line.
"""

from unittest.mock import AsyncMock

import pytest

from drawdownguard.agent.roles.reviewer import (
    MAX_WORDS,
    build_prompt,
    check,
    render_facts,
    review,
)
from drawdownguard.risk.changes import Change, Diff

MOVED = Diff(changes=[Change(symbol="XLF", kind="shares", before=900, after=0)])
CONTEXT = {
    "legs_held": 9,
    "exposure": 29_665.0,
    "budget": 9_997.84,
    "uncovered_risk": 0.0,
}


def test_facts_carry_the_change_and_the_standing():
    """Both, because they are the same question. See `render_facts`."""
    facts = render_facts(MOVED, CONTEXT)
    assert "closed all 900 shares of XLF" in facts
    assert "9,998" in facts


def test_a_first_cycle_says_so_rather_than_reading_as_quiet():
    facts = render_facts(Diff(first=True), CONTEXT)
    assert "nothing to compare against" in facts
    assert "nothing moved" not in facts


def test_the_rulebook_travels_with_the_facts():
    assert "You must never predict" in build_prompt(MOVED, CONTEXT)


def test_a_plain_verdict_passes():
    text = (
        "You sold your entire XLF position, 900 shares. The nine puts held "
        "against it now stand behind nothing and should go back."
    )
    assert check(text) == text


def test_a_forecast_is_dropped():
    assert check("The book is unchanged, though we expect a fall from here.") is None


def test_advice_against_covering_is_dropped():
    """The one sentence that could make this call expensive.

    A model reasoning its way to "the change is small, no need to hedge" is not
    a wrong opinion -- it is an opinion about a decision the client already
    made, published in the journal as though the agent had weighed it.
    """
    assert check("Exposure barely moved, so there is no need to hedge today.") is None


def test_a_ramble_is_dropped():
    assert check(" ".join(["word"] * (MAX_WORDS + 1))) is None


def test_empty_is_dropped():
    assert check("   ") is None


@pytest.mark.asyncio
async def test_a_dead_model_is_silence_not_an_excuse():
    """No fallback sentence anywhere. The journal carries the diff alone."""
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("no key")
    assert await review(MOVED, CONTEXT, llm=llm) is None


@pytest.mark.asyncio
async def test_a_good_answer_comes_back_cleaned():
    llm = AsyncMock()
    llm.ainvoke.return_value = type("M", (), {"content": "  Nothing\n moved.  "})()
    assert await review(MOVED, CONTEXT, llm=llm) == "Nothing moved."
