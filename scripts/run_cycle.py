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

    submitted = sum(1 for r in state.get("results", []) if r.submitted)
    refused = sum(1 for r in state.get("results", []) if not r.submitted)
    print(f"gap         : {state.get('protection_gap') or 0:,.0f}")
    kinds = [r.kind for r in state.get("protection") or []]
    print(f"protection  : {', '.join(kinds) or 'none'}")
    print(f"halted      : {state.get('halted')} {state.get('halt_reason', '')}".strip())
    print(f"submitted   : {submitted}")
    print(f"refused     : {refused}")
    for result in state.get("results", []):
        print(f"  {result.occ_symbol}: {result.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
