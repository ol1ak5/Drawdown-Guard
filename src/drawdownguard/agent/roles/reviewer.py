"""The morning read: what in this book needs attention, and why.

WHAT IT IS ASKED, AND WHAT IT IS DELIBERATELY NOT TOLD
-------------------------------------------------------
It gets the material -- holdings, the promise, the protection standing against
them, and what the book loses at four depths -- and it is not given the
conclusion. `uncovered_risk` is a single number the arithmetic already worked
out, and handing it over turns the call into a paraphrase: a model told the
answer will restate it in nicer words every time and look like analysis doing
it.

So the facts below are per position, and the mismatches that matter only appear
when two of them are read together. Nine puts and no shares to put them
against. New equity and an overlay struck on something else. Whether the model
finds those is a real question with a real answer, which is the only kind worth
paying for.

WHY ITS ANSWER DOES NOT STEER THE CYCLE
-----------------------------------------
It could not usefully steer it. `protect` already examines every symbol holding
shares and every symbol carrying a leg, which is both of the cases above -- a
finding cannot point at something the cycle would have skipped.

What it can do is disagree. The agent's checks are arithmetic and they are
narrow by design; a finding on a symbol the cycle then did nothing about is
either the model being wrong or the checks having a blind spot, and neither is
discoverable if the two are never compared. So findings are journalled, matched
against what the cycle actually did, and the unmatched ones are recorded as
such. That is a second opinion, not a second decision.

FAILURE IS SILENCE
-------------------
No model, no answer, no fallback sentence. `None` means the model did not
speak, and the journal carries the facts without it.
"""

from pathlib import Path
from typing import Any

from drawdownguard.llm import build_llm, response_text
from drawdownguard.risk.book import Book
from drawdownguard.risk.changes import Diff
from drawdownguard.risk.stress import Rung

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "reviewer.md"

# One line per finding. A reply that grew past this stopped naming issues and
# started explaining itself, and the explaining is a different role's job.
MAX_LINE_WORDS = 45

# A book of any size has a handful of real issues. A model returning more than
# this is listing positions rather than finding problems, and the list is
# dropped whole -- picking the "best" few would be inventing a ranking the
# model did not give.
MAX_FINDINGS = 6


def rulebook(path: Path = PROMPT_PATH) -> str:
    return path.read_text()


def _money(value: float | None) -> str:
    return "unknown" if value is None else f"{value:,.0f}"


def render_facts(
    book: Book, diff: Diff, ladder: list[Rung], context: dict[str, Any]
) -> str:
    """The material, position by position, with no conclusion drawn from it.

    Holdings and legs are listed separately and neither is annotated with the
    other. Writing "XLF: 0 shares, 9 puts (redundant)" would be handing over the
    finding and asking the model to agree with it; the mismatch has to be
    something it puts together, or the call is measuring nothing.

    The ladder is included because a promise is about depth, and a position
    whose loss is fine at -10% and not at -30% is a different issue from one
    that is uncovered everywhere.
    """
    lines = [f"mandate: {context.get('mandate', 'unknown')}"]
    lines.append(
        f"the client agreed to lose at most {_money(context.get('budget'))} "
        f"over twelve months, measured against "
        f"{_money(context.get('reference'))}"
    )

    lines.append("")
    lines.append("equity held:")
    holdings = [h for h in book.holdings if h.symbol != "CASH"]
    for holding in sorted(holdings, key=lambda h: -h.value):
        lines.append(
            f"  {holding.symbol}: {holding.shares} shares at "
            f"{holding.price:,.2f} = {_money(holding.value)}"
            + ("" if holding.shocked else "  (cash-like; does not fall with equities)")
        )
    if not holdings:
        lines.append("  none")

    lines.append("")
    lines.append("option protection held:")
    for leg in book.legs:
        side = "long" if leg.contracts > 0 else "short"
        lines.append(
            f"  {leg.symbol}: {side} {abs(leg.contracts)}x {leg.strike} "
            f"{'put' if leg.right == 'P' else 'call'}"
            + (f", expires {leg.expiry}" if getattr(leg, "expiry", None) else "")
        )
    if not book.legs:
        lines.append("  none")

    lines.append("")
    lines.append("what the whole book loses if the market falls:")
    for rung in ladder:
        lines.append(
            f"  at {rung.shock:.0%}: loses {_money(rung.portfolio_loss)}, "
            f"of which {_money(rung.protected_by_options)} is met by the "
            f"options above"
        )
    if not ladder:
        lines.append("  not measured this cycle")

    lines.append("")
    lines.append("what the client did since the last cycle:")
    if diff.first:
        lines.append("  no previous snapshot exists; nothing to compare against")
    elif not diff.moved:
        lines.append("  nothing")
    else:
        lines.extend(f"  {change.describe()}" for change in diff.changes)

    if book.unpriced:
        lines += ["", f"could not be priced: {', '.join(book.unpriced)}"]
    return "\n".join(lines)


def build_prompt(
    book: Book, diff: Diff, ladder: list[Rung], context: dict[str, Any]
) -> str:
    facts = render_facts(book, diff, ladder, context)
    return f"{rulebook()}\n\n## This morning\n\n{facts}\n"


FORECASTS = (
    "will fall",
    "will rise",
    "will drop",
    "we expect",
    "i expect",
    "likely to fall",
    "likely to rise",
    "forecast",
    "predict",
)

# Advice against covering. Matched on the phrasing a model actually produces
# when it is being reasonable at the client's expense -- not a dramatic
# argument, a mild sentence about how the exposure is small.
STAND_DOWNS = (
    "no need to hedge",
    "no need for protection",
    "should not buy protection",
    "skip the hedge",
    "leave it uncovered",
    "leave the risk uncovered",
    "not worth hedging",
    "hold off on protection",
)


def parse(text: str, known: set[str]) -> list[dict[str, str]] | None:
    """`SYMBOL: issue -- what to review` lines, as findings.

    None when the answer is unusable, which is different from an empty list:
    empty means the model looked and found nothing, None means it did not
    answer. The journal has to be able to tell those apart.

    A symbol the model invented drops that line. `known` is every symbol in the
    material it was given, so a name outside it did not come from the facts.
    """
    findings: list[dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip("-").strip()
        if not line or ":" not in line:
            continue
        symbol, _, rest = line.partition(":")
        symbol = symbol.strip().upper()
        if symbol == "NONE":
            return []
        if symbol not in known:
            continue
        issue, _, recommend = rest.partition("--")
        issue, recommend = " ".join(issue.split()), " ".join(recommend.split())
        if not issue or not recommend:
            continue
        if len(f"{issue} {recommend}".split()) > MAX_LINE_WORDS:
            continue
        lowered = f"{issue} {recommend}".lower()
        if any(tell in lowered for tell in FORECASTS + STAND_DOWNS):
            continue
        findings.append({"symbol": symbol, "issue": issue, "recommendation": recommend})
    if not findings:
        return None
    if len(findings) > MAX_FINDINGS:
        return None
    return findings


async def review(
    book: Book,
    diff: Diff,
    ladder: list[Rung],
    context: dict[str, Any],
    llm: Any = None,
) -> list[dict[str, str]] | None:
    """What the model says needs attention, or None if it did not answer."""
    known = {h.symbol for h in book.holdings if h.symbol != "CASH"}
    known |= {leg.symbol for leg in book.legs}
    known |= {c.symbol.split()[0] for c in diff.changes}
    try:
        model = llm or build_llm()
        reply = await model.ainvoke(build_prompt(book, diff, ladder, context))
        return parse(response_text(reply), known)
    except Exception:  # noqa: BLE001 -- prose is never worth a failed cycle
        return None
