"""The value the promise is measured against, fixed for the life of the promise.

WHY THIS FILE EXISTS
--------------------
The budget used to be ten percent of whatever the account was worth that
morning, recomputed every cycle. That is not a promise, it is a promise that
re-bases: lose ten percent, and tomorrow the agent starts defending ten percent
of the smaller number, then ten percent of the one after that. Five steps of
that permits a 47% loss and reports every one of them as kept -- and the budget
shrinks fastest exactly when a fall has already started, so the agent buys
*less* protection precisely as it becomes most necessary.

So the reference is written down once, when the promise starts, and does not
move until the promise is renewed.

WHY NOT THE HIGH-WATER MARK
----------------------------
A floor at ten percent below the peak is a stronger guarantee and it was the
first choice here. It was rejected for a reason that has nothing to do with
cost: it moves when the market makes a new high, which means the agent would
re-strike its hedge because prices went up. This project's entire claim is that
it never acts on where the market is going, and one place where it does is
enough to lose the argument.

Measured, so it is a fact rather than an aesthetic: with a peak-following floor
the hedge bought today is already inadequate after a 10% rally -- the account
would breach its floor before the puts engaged. With a fixed one it still holds
after 40%, because the gains themselves are the cushion.

The client's gains are not left unprotected forever. They are locked in at
renewal, on the calendar, rather than continuously and on the market's cue.
"""

from calendar import monthrange
from datetime import date
from pathlib import Path

from pydantic import BaseModel

DEFAULT_PATH = Path("data/state/period.json")


class Period(BaseModel):
    """One promise: when it started, and what it was measured against.

    `reference` is the account value on the day the promise began. Every budget
    for the next `horizon_months` is a percentage of this and of nothing else.
    """

    started: date
    reference: float
    horizon_months: int = 12

    def ends(self) -> date:
        """The day the promise runs out and has to be renewed."""
        month = self.started.month - 1 + self.horizon_months
        year, month = self.started.year + month // 12, month % 12 + 1
        # A promise started on the 31st renews on the 30th where there is no
        # 31st. Clamping rather than rolling into the next month, so a renewal
        # date never drifts forward a day at a time across the years.
        last = monthrange(year, month)[1]
        return date(year, month, min(self.started.day, last))

    def expired(self, today: date | None = None) -> bool:
        return (today or date.today()) >= self.ends()


def load(path: Path | None = None) -> Period | None:
    # Resolved at call time, not bound as a default. A default argument is
    # evaluated once when the module is imported, so a test that redirects
    # `DEFAULT_PATH` afterwards changes nothing -- which is how the suite came
    # to be asserting against the promise on the live account.
    path = path or DEFAULT_PATH
    if not path.exists():
        return None
    return Period.model_validate_json(path.read_text())


def save(period: Period, path: Path | None = None) -> None:
    path = path or DEFAULT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(period.model_dump_json(indent=2) + "\n")


def current(
    equity: float,
    horizon_months: int = 12,
    today: date | None = None,
    path: Path | None = None,
) -> tuple[Period, bool]:
    """The promise in force, and whether it was just written.

    Started on the first cycle and renewed when it runs out, at whatever the
    account is worth on that day -- so a client who made money spends the next
    year protecting the larger number. Renewal is the only thing that moves the
    reference, and it happens on a date rather than on a price.
    """
    today, path = today or date.today(), path or DEFAULT_PATH
    existing = load(path)
    if existing is not None and not existing.expired(today):
        return existing, False

    fresh = Period(started=today, reference=equity, horizon_months=horizon_months)
    save(fresh, path)
    return fresh, True


