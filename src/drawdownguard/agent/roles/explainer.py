"""Turning a decision that has already been made into something a person reads.

WHY THIS IS THE SAFE PLACE FOR A LANGUAGE MODEL
------------------------------------------------
Everything upstream of here is arithmetic. The budget comes from the client,
the strike comes from solving `fall to strike + premium = budget` over the live
chain, and the risk gate has already approved or refused the order. By the time
this module runs there is nothing left to decide.

So the model cannot be wrong in a way that costs money. It can only be wrong in
a way that costs *clarity*, and a wrong sentence sits in the journal next to
the numbers it describes, where anybody can catch it. That is a different class
of risk from a model that sizes a position.

This is deliberately the opposite arrangement from the one that was removed. A
regime classifier that no decision read was a paid call producing nothing; an
explainer that no decision reads is the entire point.

WHAT IT IS NOT ALLOWED TO DO
-----------------------------
It does not get to justify. The numbers are handed to it already decided, and
its job is to say what they mean -- not to argue that they were wise. A model
asked to explain a choice will reliably produce a defence of it, which is why
the prompt asks for the mechanism rather than the merits, and why the cost is
required to appear in the answer.

It does not get to forecast. Nothing in the input says where the market is
going, because nothing upstream asked. An explanation containing a prediction
is describing a different agent.

FAILURE IS SILENCE, NOT AN EXCUSE
----------------------------------
If the model is unreachable, slow, or returns nothing usable, the cycle
continues and the journal carries the numbers without prose. A missing sentence
is a missing sentence. The one thing that must never happen is an explanation
invented to fill the gap, so there is no fallback text and no default string --
`None` means the model did not answer, and the status page shows the figures
alone.
"""

from pathlib import Path
from typing import Any

from drawdownguard.llm import build_llm, response_text

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "explainer.md"

# Long enough for four sentences, short enough that a model inclined to ramble
# is cut off rather than indulged. The audience is a client reading a daily
# note, not a research desk.
MAX_WORDS = 120


def rulebook(path: Path = PROMPT_PATH) -> str:
    return path.read_text()


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"{value:,.0f}"


def render_facts(decision: dict[str, Any]) -> str:
    """The decision as a block of settled numbers.

    Rendered rather than passed as JSON so the model is reading a statement of
    what happened, not a structure it might try to complete. Every field is an
    outcome; none of them is a question.
    """
    lines = [
        f"client's mandate: {decision.get('mandate', 'unknown')}",
        f"downside budget: {_money(decision.get('budget'))}",
        f"equity exposure: {_money(decision.get('exposure'))}",
        f"risk not covered by the promise: {_money(decision.get('uncovered_risk'))}",
        f"action taken: {decision.get('describe', 'nothing')}",
        f"cash cost: {_money(decision.get('premium_cost'))}",
        f"upside given up: {_money(decision.get('forgone_upside'))}",
        f"risk not covered afterwards: {_money(decision.get('uncovered_after'))}",
    ]
    rejected = decision.get("rejected") or []
    for other in rejected:
        lines.append(
            f"not taken: {other.get('kind')} -- {other.get('describe')}"
            f" (cash {_money(other.get('premium_cost'))},"
            f" upside given up {_money(other.get('forgone_upside'))})"
        )
    if because := decision.get("because"):
        lines.append(f"the arithmetic that decided between them: {because}")
    return "\n".join(lines)


def build_prompt(decision: dict[str, Any]) -> str:
    return f"{rulebook()}\n\n## Today's decision\n\n{render_facts(decision)}\n"


def check(text: str) -> str | None:
    """The explanation, or None if it broke one of the two rules that matter.

    Checked rather than trusted. A model told not to forecast will mostly not
    forecast, and "mostly" is not a property to publish under a client's name.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    if len(cleaned.split()) > MAX_WORDS:
        return None
    lowered = cleaned.lower()
    # Words that only appear when the answer has stopped describing and started
    # predicting. The agent takes no view on direction, so neither may its
    # explanation -- a client reading one would reasonably believe it had.
    for tell in (
        "will fall",
        "will rise",
        "will drop",
        "we expect",
        "i expect",
        "likely to fall",
        "likely to rise",
        "forecast",
        "predict",
    ):
        if tell in lowered:
            return None
    return cleaned


async def explain(decision: dict[str, Any], llm: Any = None) -> str | None:
    """One paragraph on what was bought and what it cost, or None.

    None on any failure, and no fallback sentence anywhere. An explanation the
    agent wrote for itself, published beside real numbers under a client's
    mandate, would be worse than no explanation at all.
    """
    try:
        model = llm or build_llm()
        reply = await model.ainvoke(build_prompt(decision))
        return check(response_text(reply))
    except Exception:  # noqa: BLE001 -- prose is never worth a failed cycle
        return None
