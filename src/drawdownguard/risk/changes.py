"""What moved in the client's book since the last cycle.

The comparison itself is arithmetic and stays arithmetic. A language model
asked whether nine hundred shares left the account would be right almost every
time, and "almost" is not a property to build a hedge on -- so the diff below
is a set difference over two snapshots, and the model is only ever handed the
answer.

WHY A SNAPSHOT FILE
-------------------
Each scheduled run starts on a fresh machine holding only what git carries, so
"yesterday" has to be written down. `store.py` already persists the option
bookkeeping the broker does not track for us; this is deliberately separate and
much dumber -- symbol to quantity, nothing derived -- because its only job is
to be compared against, and a snapshot that carried opinions would make the
diff an argument rather than a fact.

The first cycle has nothing to compare against. That is reported as `first`,
not as "nothing changed": a book seen for the first time is not a quiet book,
and the two must not read the same in the journal.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from drawdownguard.risk.book import Book

# Read inside the functions rather than bound as a default argument. A default
# is evaluated once at import, so a test redirecting this constant at a
# temporary directory would still have been served the live path -- and the
# suite would have read the real book as "yesterday" and overwritten it on the
# way out, quietly becoming the reference the next real cycle compared against.
DEFAULT_SNAPSHOT = Path("data/state/holdings.json")


@dataclass
class Change:
    """One line of the diff, in the units a person counts in."""

    symbol: str
    kind: str  # "shares" or "contracts"
    before: int
    after: int

    @property
    def delta(self) -> int:
        return self.after - self.before

    def describe(self) -> str:
        if self.before == 0:
            return f"opened {self.after:+d} {self.kind} of {self.symbol}"
        if self.after == 0:
            return f"closed all {self.before} {self.kind} of {self.symbol}"
        return (
            f"{self.symbol} {self.kind} {self.before} -> {self.after} ({self.delta:+d})"
        )


@dataclass
class Diff:
    """Everything that moved, and whether there was anything to compare with."""

    changes: list[Change] = field(default_factory=list)
    first: bool = False

    @property
    def moved(self) -> bool:
        return bool(self.changes)

    def describe(self) -> str:
        if self.first:
            return "first cycle against this book; nothing to compare with"
        if not self.changes:
            return "nothing moved"
        return "; ".join(change.describe() for change in self.changes)


def _leg_key(symbol: str, right: str, strike: object) -> str:
    return f"{symbol} {right}{strike}"


def snapshot(book: Book) -> dict[str, int]:
    """The book as counts, keyed so a diff means something.

    Shares under the bare symbol, option legs under symbol-right-strike, so a
    roll from one strike to another shows as two lines rather than as no change
    at all.
    """
    counts: dict[str, int] = {}
    for holding in book.holdings:
        if holding.symbol == "CASH":
            continue
        counts[holding.symbol] = int(holding.shares)
    for leg in book.legs:
        key = _leg_key(leg.symbol, leg.right, leg.strike)
        counts[key] = counts.get(key, 0) + int(leg.contracts)
    return counts


def compare(before: dict[str, int] | None, after: dict[str, int]) -> Diff:
    """The lines that differ, in a stable order.

    `None` means no previous snapshot existed, which is reported rather than
    silently treated as an empty book -- otherwise the first cycle would claim
    the client had just bought everything they own.
    """
    if before is None:
        return Diff(first=True)
    changes = []
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key, 0), after.get(key, 0)
        if was == now:
            continue
        kind = "contracts" if " " in key else "shares"
        changes.append(Change(symbol=key, kind=kind, before=was, after=now))
    return Diff(changes=changes)


def load(path: str | Path | None = None) -> dict[str, int] | None:
    """Last cycle's counts, or None if there was no last cycle.

    None on unreadable or malformed content too. A corrupt snapshot compared
    against as if it were empty would report a day of enormous invented trades,
    and the honest answer to "what did the book look like yesterday" is that we
    do not know.
    """
    file = Path(path or DEFAULT_SNAPSHOT)
    if not file.exists():
        return None
    try:
        loaded = json.loads(file.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(loaded, dict):
        return None
    return {str(k): int(v) for k, v in loaded.items() if isinstance(v, int | float)}


def save(counts: dict[str, int], path: str | Path | None = None) -> None:
    """Record today's counts as tomorrow's reference."""
    file = Path(path or DEFAULT_SNAPSHOT)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(counts, indent=2, sort_keys=True) + "\n")
