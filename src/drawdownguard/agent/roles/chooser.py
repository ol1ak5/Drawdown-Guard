"""Which of the day's admissible structures to buy.

THE ONLY PLACE A MODEL'S ANSWER REACHES AN ORDER
-------------------------------------------------
Everywhere else in this program a language model writes prose. Here it picks,
and the pick becomes a trade -- so the question is not whether the model is
good at this. It is what the pick can possibly be.

The answer is: one of two or three structures that have already passed the two
checks that are not negotiable. `eligible` below applies them in code, exactly
as `remedy.choose` always has:

1. it closes the risk in full -- `covers_the_risk`
2. it expires -- nothing permanent while something reversible is available

So the model cannot leave the promise broken, cannot sell the client's shares,
and cannot reach a structure that was not priced this morning. It chooses
between hedges that all keep the promise, on the one question that is genuinely
a judgement about today's prices: whether the upside a collar sells is worth
what the collar saves in cash.

`remedy.choose` still answers that question, and its answer is the fallback on
every failure -- an unreachable model, an unparseable reply, a symbol missing
from the answer, a kind that was not offered. The rule was not deleted to make
room for the model. It is what the model is checked against.

WHY ONE CALL FOR EVERY SLEEVE AT ONCE
--------------------------------------
Latency here is not a comfort issue, it is the failure this project already
had. On 2026-08-28 a model sat forty-one seconds between the chain being read
and the order being priced off it; the ask moved, both limits landed under the
market, and the cycle bought nothing while reporting a plan. Limits now reach a
quarter of the spread past the offer, which absorbs a few seconds of drift --
but the way to stay inside that tolerance is to spend the seconds once rather
than once per symbol.

So: one call, every sleeve in it, and no call at all on a morning when no
sleeve has more than one admissible structure.
"""

from pathlib import Path
from typing import Any

from drawdownguard.llm import build_llm, response_text
from drawdownguard.risk.remedy import Remedy

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "chooser.md"

# One line per sleeve and nothing else. A reply longer than the book is a
# model that started explaining, and the explanation is not this role's job.
MAX_LINE_WORDS = 60


def rulebook(path: Path = PROMPT_PATH) -> str:
    return path.read_text()


def eligible(offers: list[Remedy]) -> list[Remedy]:
    """The structures the model is allowed to choose between.

    The first two questions of `remedy.choose`, applied here so the model never
    sees a candidate that would break the promise or sell something the client
    cannot buy back. Returns the permanent ones only when nothing that expires
    closes the risk -- and in that case there is nothing to choose anyway.
    """
    closing = [remedy for remedy in offers if remedy.covers_the_risk]
    reversible = [remedy for remedy in closing if not remedy.permanent]
    return reversible or closing


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"{value:,.0f}"


def _vol(value: float | None) -> str:
    return "unknown" if value is None else f"{value:.1%}"


def render_facts(choices: dict[str, list[Remedy]]) -> str:
    """Each sleeve and its candidates, as a block of priced facts."""
    blocks = []
    for symbol in sorted(choices):
        lines = [f"### {symbol}"]
        for remedy in choices[symbol]:
            lines += [
                f"- kind: {remedy.kind}",
                f"  what it is: {remedy.describe}",
                f"  cash cost: {_money(remedy.premium_cost)}",
                f"  upside given up: {_money(remedy.forgone_upside)}",
                f"  ceiling: {remedy.ceiling_pct:.1f}% above spot"
                if remedy.ceiling_pct
                else "  ceiling: none, the upside is untouched",
                f"  put vol: {_vol(remedy.protection_iv)}"
                f"   call vol: {_vol(remedy.financing_iv)}",
                f"  risk left after: {_money(remedy.uncovered_after)}",
            ]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_prompt(choices: dict[str, list[Remedy]]) -> str:
    return f"{rulebook()}\n\n## This morning\n\n{render_facts(choices)}\n"


def parse(text: str) -> dict[str, tuple[str, str]]:
    """`SYMBOL: kind -- reason` lines, as a mapping. Junk lines are dropped.

    Dropped rather than raised on: a model that got one symbol's line wrong
    should not cost the other symbol its answer, and a symbol missing from the
    result falls back to the rule like any other failure.
    """
    picks: dict[str, tuple[str, str]] = {}
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        if not line or ":" not in line:
            continue
        symbol, _, rest = line.partition(":")
        symbol = symbol.strip().upper()
        if not symbol.isalnum() or len(symbol) > 6:
            continue
        kind, _, reason = rest.partition("--")
        kind, reason = kind.strip(), " ".join(reason.split())
        if not kind or not reason:
            continue
        if len(reason.split()) > MAX_LINE_WORDS:
            continue
        picks[symbol] = (kind, reason)
    return picks


def admit(
    picks: dict[str, tuple[str, str]], choices: dict[str, list[Remedy]]
) -> dict[str, tuple[Remedy, str]]:
    """The picks that name a structure actually on offer for that sleeve.

    A kind the model invented, or one belonging to a different symbol, is not
    a near miss to be resolved generously -- it is evidence the answer was not
    grounded in the facts it was given, and the rule handles that sleeve.
    """
    admitted: dict[str, tuple[Remedy, str]] = {}
    for symbol, (kind, reason) in picks.items():
        for remedy in choices.get(symbol, []):
            if remedy.kind == kind:
                admitted[symbol] = (remedy, reason)
                break
    return admitted


async def pick(
    choices: dict[str, list[Remedy]], llm: Any = None
) -> dict[str, tuple[Remedy, str]]:
    """The model's admissible picks, keyed by symbol. Empty on any failure.

    Empty rather than partial-on-error: every symbol the caller does not find
    here falls through to `remedy.choose`, which is the same code path the
    agent ran before this role existed.
    """
    if not choices:
        return {}
    try:
        model = llm or build_llm()
        reply = await model.ainvoke(build_prompt(choices))
        return admit(parse(response_text(reply)), choices)
    except Exception:  # noqa: BLE001 -- the rule decides when the model cannot
        return {}
