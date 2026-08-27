"""The one place a language model is allowed to speak.

Everything the explainer is handed has already been decided: the budget by the
client, the strike by arithmetic over the chain, the order by the risk gate. So
these tests are not about whether it decides well. They are about the two ways
prose can do damage — by predicting, and by inventing — and about failing
silently rather than filling the gap.
"""

from unittest.mock import AsyncMock

import pytest

from drawdownguard.agent.roles.explainer import (
    MAX_WORDS,
    build_prompt,
    check,
    explain,
    render_facts,
)

DECISION = {
    "mandate": "balanced",
    "budget": 100_693.0,
    "exposure": 605_422.0,
    "gap": 20_391.0,
    "describe": "buy 8x SPY 670 put at 20.96",
    "premium_cost": 16_455.0,
    "forgone_upside": 0.0,
    "gap_after": 0.0,
    "rejected": [
        {
            "kind": "collar",
            "describe": "buy 8x 670 put, sell 3x 850 call",
            "premium_cost": -1_773.0,
            "forgone_upside": 14_523.0,
        }
    ],
    "because": "the call priced below the put per unit of risk",
}


def reply(text: str) -> AsyncMock:
    return AsyncMock(ainvoke=AsyncMock(return_value=type("M", (), {"content": text})))


# --- what the model is told --------------------------------------------------


def test_every_number_the_model_may_use_is_handed_to_it():
    """It is forbidden from inventing figures, so the ones it needs must all be
    present. A prompt missing the cost invites a paragraph without one."""
    facts = render_facts(DECISION)
    for number in ("100,693", "605,422", "20,391", "16,455"):
        assert number in facts


def test_the_alternative_and_the_reason_it_lost_are_included():
    """A note that mentions only what was bought reads like an advertisement.
    What was turned down, and on what arithmetic, is half the explanation."""
    facts = render_facts(DECISION)
    assert "collar" in facts
    assert "the call priced below the put" in facts


def test_a_missing_number_says_so_rather_than_printing_zero():
    """Zero is a claim. Absent is a different claim, and the model must be able
    to tell them apart -- a hedge that cost nothing and a hedge whose cost was
    not recorded are not the same event."""
    facts = render_facts({**DECISION, "premium_cost": None})
    assert "cash cost: unknown" in facts


def test_the_prompt_forbids_forecasting_in_the_rules_it_carries():
    prompt = build_prompt(DECISION)
    assert "never predict where the market is going" in prompt.lower()


# --- what comes back ---------------------------------------------------------


def test_a_plain_description_is_accepted():
    text = (
        "Your mandate allows a 10% loss and the book had drifted past it. We "
        "bought eight SPY 670 puts, so the loss stops at the strike however "
        "far the market falls. That cost 16,455."
    )
    assert check(text) == " ".join(text.split())


@pytest.mark.parametrize(
    "forecast",
    [
        "We expect the market to fall, so we bought puts.",
        "The market will fall further this quarter, so protection was bought.",
        "Our forecast is a sharp decline, and the hedge reflects it.",
        "Prices are likely to fall, which is why the put was purchased.",
    ],
)
def test_a_sentence_that_forecasts_is_refused(forecast):
    """The agent takes no view on direction and neither may the note.

    This is the one failure that could actually mislead a client: they would
    reasonably conclude the agent had a market opinion and was trading on it,
    when the entire design is that it does not and never will. Checked rather
    than trusted -- a model told not to forecast will *mostly* not forecast,
    and 'mostly' is not a property to publish under somebody's name.
    """
    assert check(forecast) is None


def test_a_rambling_answer_is_refused():
    assert check("word " * (MAX_WORDS + 1)) is None


def test_an_empty_answer_is_refused():
    assert check("   \n  ") is None


# --- when it goes wrong ------------------------------------------------------


async def test_a_model_that_fails_costs_the_prose_and_nothing_else():
    """The cycle has already traded by the time this runs. An exception here
    would throw away a completed hedge over a missing sentence."""
    broken = AsyncMock(ainvoke=AsyncMock(side_effect=RuntimeError("no key")))
    assert await explain(DECISION, llm=broken) is None


async def test_nothing_is_invented_to_fill_the_gap():
    """There is no fallback sentence anywhere in this module, deliberately.

    Text the agent wrote for itself, published beside real numbers under a
    client's mandate, would be worse than an empty field -- the reader cannot
    tell a generated apology from an explanation.
    """
    assert await explain(DECISION, llm=reply("we predict a crash")) is None


async def test_a_good_answer_comes_back_whole():
    text = "The book had drifted 20,391 past the promise, so we bought puts."
    assert await explain(DECISION, llm=reply(text)) == text
