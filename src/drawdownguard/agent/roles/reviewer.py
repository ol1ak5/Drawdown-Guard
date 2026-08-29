"""The morning read: what changed in the book, and what it means for the cover.

WHY THIS ONE IS DIFFERENT FROM THE EXPLAINER
---------------------------------------------
`explainer` writes about a decision already taken. This runs before `protect`
decides anything, which puts a language model upstream of an action for the
first time in this program -- so the boundary has to be drawn explicitly rather
than assumed.

The model cannot reach the decision. It is handed a diff that arithmetic
produced, and it returns prose. `protect` does not read it, `gate` does not
read it, and no strike, size or order depends on a word of it. What it changes
is what a person reads in the journal on a morning when the book moved: the
difference between a row of numbers and a sentence saying the puts now stand
behind nothing.

That is worth a call. It is not worth letting the answer matter.

WHY IT MAY NOT SAY "DO NOTHING"
--------------------------------
The one failure mode that would cost money is a model that talks the agent out
of protection -- and the way that happens is not a dramatic argument, it is a
reasonable-sounding sentence about how the change is small. So the prompt
forbids it and `check` enforces it: an answer that tells the agent to leave
uncovered risk alone is dropped, and the cycle proceeds exactly as it would
have. The promise is the client's instruction. It is not open for review by the
thing named reviewer.

FAILURE IS SILENCE
-------------------
As with the explainer: no model, no answer, no fallback sentence. `None` means
the model did not speak, and the journal carries the diff without prose.
"""

from pathlib import Path
from typing import Any

from drawdownguard.llm import build_llm, response_text
from drawdownguard.risk.changes import Diff

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "reviewer.md"

# Shorter than the explainer's. This is a note about what moved, not the
# account of a decision, and three sentences is the whole job.
MAX_WORDS = 80


def rulebook(path: Path = PROMPT_PATH) -> str:
    return path.read_text()


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"{value:,.0f}"


def render_facts(diff: Diff, context: dict[str, Any]) -> str:
    """The diff and the standing position, as settled statements.

    The uncovered figure is included because the two questions are the same
    question: a change matters exactly insofar as it moves what the book owes
    the promise.
    """
    lines = ["what changed since the last cycle:"]
    if diff.first:
        lines.append("  no previous snapshot exists; nothing to compare against")
    elif not diff.moved:
        lines.append("  nothing moved")
    else:
        lines.extend(f"  {change.describe()}" for change in diff.changes)
    lines += [
        "",
        f"protection currently held: {context.get('legs_held', 0)} option legs",
        f"equity exposure: {_money(context.get('exposure'))}",
        f"downside budget: {_money(context.get('budget'))}",
        f"risk not covered by the promise: {_money(context.get('uncovered_risk'))}",
    ]
    return "\n".join(lines)


def build_prompt(diff: Diff, context: dict[str, Any]) -> str:
    return f"{rulebook()}\n\n## This morning\n\n{render_facts(diff, context)}\n"


def check(text: str) -> str | None:
    """The verdict, or None if it broke one of the rules that matter.

    Two classes of tell. The first is a forecast, which the explainer screens
    for the same way and for the same reason. The second is advice to stand
    down -- harmless prose in most contexts and the single sentence that could
    make this call expensive here, so it is refused rather than published.
    """
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    if len(cleaned.split()) > MAX_WORDS:
        return None
    lowered = cleaned.lower()
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
    # Advice against covering. Matched on the phrasing a model actually
    # produces when it is being reasonable at the client's expense.
    for tell in (
        "no need to hedge",
        "no need for protection",
        "should not buy protection",
        "skip the hedge",
        "leave it uncovered",
        "leave the risk uncovered",
        "not worth hedging",
        "hold off on protection",
    ):
        if tell in lowered:
            return None
    return cleaned


async def review(diff: Diff, context: dict[str, Any], llm: Any = None) -> str | None:
    """Two or three sentences on what moved and what it calls for, or None."""
    try:
        model = llm or build_llm()
        reply = await model.ainvoke(build_prompt(diff, context))
        return check(response_text(reply))
    except Exception:  # noqa: BLE001 -- prose is never worth a failed cycle
        return None
