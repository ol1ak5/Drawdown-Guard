"""One JSON object per line, one file per day, never rewritten.

The journal is the only durable account of the agent's reasoning, and it is
what the status page and the final report are built from. Two properties earn
their keep:

*Append-only.* A cycle adds lines and touches nothing already written. There is
no code path here that opens a journal file for writing.

*No dependency on settings.* The environment name is read straight from the
process environment rather than through `flywheel.settings`. Settings refuse to
construct without broker credentials, and the single most important thing the
journal has to record is a misconfiguration — a logger that fails exactly when
the system is broken is worse than no logger.
"""

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

JOURNAL_DIR = Path("journal")

# `defect` is not a louder `veto`. A veto is the risk gate working: an order was
# proposed, examined and refused. A defect means the middleware fired, which can
# only happen if the toolset let something through that should never have
# reached it. One is evidence the design holds; the other is evidence it leaks.
SEVERITIES = ("info", "veto", "defect")


def _path_for(day: date, directory: str | Path) -> Path:
    return Path(directory) / f"{day.isoformat()}.jsonl"


def write(
    event: str,
    payload: dict,
    severity: str = "info",
    directory: str | Path | None = None,
) -> None:
    """Append one entry to today's journal.

    `directory` resolves to `JOURNAL_DIR` at call time, not at definition
    time. A default argument would bind the module constant once at import,
    so a test that redirected the journal would still write to the real one
    — and would pass, because it never looked at where the line landed.
    """
    directory = JOURNAL_DIR if directory is None else directory
    if severity not in SEVERITIES:
        raise ValueError(
            f"unknown severity {severity!r}; expected one of {', '.join(SEVERITIES)}"
        )
    now = datetime.now(UTC)
    entry = {
        "timestamp": now.isoformat(),
        "flywheel_env": os.environ.get("FLYWHEEL_ENV", "dev"),
        "event": event,
        "severity": severity,
        "payload": payload,
    }
    path = _path_for(now.date(), directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, default=str) + "\n")


def read_day(day: date, directory: str | Path = JOURNAL_DIR) -> list[dict]:
    """Every entry for one day, oldest first. A day with no journal is empty.

    A line that will not parse is skipped rather than raised on. The runner can
    be terminated mid-write, and refusing to read the file over one truncated
    line would throw away the evidence for every cycle that ran correctly.
    """
    path = _path_for(day, directory)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def read_entries(limit: int = 100, directory: str | Path = JOURNAL_DIR) -> list[dict]:
    """The most recent entries across all days, newest first.

    Days are read newest first and the loop stops once the limit is met, so the
    cost does not grow with the length of the run.
    """
    root = Path(directory)
    if not root.exists():
        return []
    entries: list[dict] = []
    for path in sorted(root.glob("*.jsonl"), reverse=True):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue  # not a journal file; the directory is not ours alone
        entries.extend(reversed(read_day(day, root)))
        if len(entries) >= limit:
            break
    return entries[:limit]
