"""Run one trading cycle.

Exits non-zero if the cycle raised. Cron has no other way to tell that a run
failed, and a scheduler that reports success for a crashed agent is worse than
no scheduler.
"""

import argparse
import asyncio
import sys
import textwrap

from drawdownguard.agent.graph import run_cycle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="run every check and decide, but submit nothing",
    )
    args = parser.parse_args()

    try:
        state = asyncio.run(run_cycle(dry_run=args.dry_run))
    except Exception as exc:  # noqa: BLE001 — the exit code is the report
        print(f"cycle failed: {exc}", file=sys.stderr)
        return 1

    results = state.get("results", [])
    submitted = sum(1 for r in results if r.submitted)
    refused = sum(1 for r in results if not r.approved)
    # Approved and deliberately not sent: a dry run, or a broker call that
    # failed after the gate had already said yes. Counted apart from `refused`
    # because the gate refusing an order and the operator holding it back are
    # different outcomes, and reporting both as "refused" made a clean dry run
    # read as though the risk checks had rejected everything.
    held = sum(1 for r in results if r.approved and not r.submitted)
    # Accepted is not bought. An option order is a day limit priced at the ask
    # the decision was made on, and a limit the market has walked away from
    # sits until the close. Reporting the send as the outcome is how a cycle
    # claims the client is protected while the account holds nothing.
    filled = sum(1 for r in results if r.submitted and r.filled_qty >= 1)
    working = sum(1 for r in results if r.submitted and r.filled_qty == 0)

    if state.get("halted"):
        _block("HALTED", [state.get("halt_reason", "no reason recorded")])
        return 0

    _changed(state)
    _risk(state)
    _decision(state)
    _orders(results, submitted, filled, working, refused, held)
    return 0


def _block(title: str, lines: list[str]) -> None:
    """One labelled block, or nothing at all.

    A heading with no lines under it reads as a section that failed rather than
    one with nothing to report, so an empty block is not printed.
    """
    if not lines:
        return
    print(f"\n{title}")
    for line in lines:
        print(line)


def _changed(state: dict) -> None:
    """What the client did, and what the model made of it."""
    review = state.get("review") or {}
    if not review:
        return
    if review.get("first"):
        _block("PORTFOLIO CHANGE", ["first cycle against this book"])
    elif not review.get("moved"):
        _block("PORTFOLIO CHANGE", ["nothing moved"])
    else:
        _block("PORTFOLIO CHANGE", list(review.get("changes") or []))
    if verdict := review.get("verdict"):
        _block("LLM REVIEW", _wrap(verdict))


def _risk(state: dict) -> None:
    """The arithmetic, printed next to the prose that described it.

    Deliberately adjacent. The verdict above is a language model's reading and
    this is the measurement it was reading; a reader has to be able to see both
    without deciding which one to trust, which is only possible if the page
    never puts one of them alone.
    """
    uncovered = state.get("uncovered_risk") or 0
    lines = [
        f"uncovered risk   {uncovered:,.0f}"
        + ("" if uncovered > 0 else "   the promise holds")
    ]
    if released := state.get("released"):
        lines.append(
            f"release          {released.contracts} contracts, {released.reason}"
        )
    if not state.get("book_complete", True):
        lines.append("book             incomplete: a holding could not be priced")
    _block("RISK CHECK", lines)


def _decision(state: dict) -> None:
    """What was chosen per sleeve, and by whom.

    `decided_by` is printed on every line rather than only when the model
    decided. "Chosen by the rule" is a fact about the run, and a field that
    appears only sometimes reads as an exception being flagged.
    """
    lines = []
    for entry in state.get("choice") or []:
        if not entry.get("chosen"):
            continue
        lines.append(f"{entry['symbol']:<6} {entry['chosen']}  [{entry['decided_by']}]")
        lines += [f"       {line}" for line in _wrap(entry.get("because") or "", 66)]
        # Only when they disagree. Printing "the rule agreed" on every sleeve
        # would bury the one morning they did not.
        rule = entry.get("rule_would_have")
        if entry["decided_by"] == "model" and rule and rule != entry["chosen"]:
            lines.append(f"       the rule would have taken the {rule}")
    _block("DECISION", lines)


def _orders(
    results: list, submitted: int, filled: int, working: int, refused: int, held: int
) -> None:
    """What actually went to the broker, and what came back.

    Accepted is not bought, so `filled` and `working` are separate counts and
    the working ones say so in words. A cycle that reported the send as the
    outcome is how an agent claims a client is protected while the account
    holds nothing.
    """
    if not results:
        return
    lines = [f"submitted {submitted}   filled {filled}   refused {refused}"]
    if working:
        lines.append(f"still working {working}  <- accepted, not bought")
    if held:
        lines.append(f"not sent {held} (approved by the gate)")
    for result in results:
        note = result.reason
        if result.submitted:
            note = f"{result.broker_status or 'unknown'}, filled {result.filled_qty}"
            if result.filled_avg_price is not None:
                note += f" @ {result.filled_avg_price}"
        lines.append(f"  {result.occ_symbol}: {note}")
    _block("ORDERS", lines)


def _wrap(text: str, width: int = 72) -> list[str]:
    return textwrap.wrap(" ".join(text.split()), width=width) if text else []


if __name__ == "__main__":
    raise SystemExit(main())
