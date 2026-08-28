"""Run one trading cycle.

Exits non-zero if the cycle raised. Cron has no other way to tell that a run
failed, and a scheduler that reports success for a crashed agent is worse than
no scheduler.
"""

import argparse
import asyncio
import sys

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

    kinds = [r.kind for r in state.get("protection") or []]
    print(f"uncovered risk : {state.get('uncovered_risk') or 0:,.0f}")
    print(f"protection     : {', '.join(kinds) or 'none'}")
    print(
        f"halted         : {state.get('halted')} "
        f"{state.get('halt_reason', '')}".strip()
    )
    print(f"submitted      : {submitted}")
    print(f"  filled       : {filled}")
    if working:
        print(f"  still working: {working}  ← accepted, not bought")
    print(f"refused        : {refused}")
    if held:
        print(f"not sent       : {held} (approved by the gate)")
    for result in results:
        note = result.reason
        if result.submitted:
            note = f"{result.broker_status or 'unknown'}, filled {result.filled_qty}"
            if result.filled_avg_price is not None:
                note += f" @ {result.filled_avg_price}"
        print(f"  {result.occ_symbol}: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
