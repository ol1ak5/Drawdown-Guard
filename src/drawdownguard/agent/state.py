"""The state one cycle carries from node to node.

A LangGraph `StateGraph` over a TypedDict rather than objects passed by hand.
The reason is not fashion: every node returns a partial update and the graph
merges it, so no node can reach into another's data and mutate it, and the
whole cycle can be replayed from a recorded state.

`halted` is separate from `halt_reason` on purpose. A halt with no reason is
indistinguishable from a crash when read back six days later, and the journal
is the only account anyone will have.
"""

from typing import TypedDict

from drawdownguard.domain import Portfolio, Position
from drawdownguard.execution.orders import OrderResult
from drawdownguard.risk.book import Book
from drawdownguard.risk.remedy import Release, Remedy
from drawdownguard.risk.stress import Rung


class GuardState(TypedDict, total=False):
    """One cycle's working set.

    `total=False` so a node may return only the keys it changed. Requiring
    every key in every update would make each node responsible for carrying
    state it has no opinion about, which is how a node ends up quietly
    resetting something it never meant to touch.
    """

    positions: dict[str, Position]
    portfolio: Portfolio | None
    # What today's book loses at each published shock, and by how much the
    # worst of them breaks the client's promise. `uncovered_risk` is dollars,
    # zero when the mandate holds.
    ladder: list[Rung]
    uncovered_risk: float
    # False when a held position could not be priced, so the ladder above is
    # missing a leg. Carried into the journal rather than dropped: a gap
    # computed from an incomplete book is a weaker claim and has to read as one.
    book_complete: bool
    # The positions the ladder was built from, carried forward so `protect` can
    # work on the same book `mandate` measured. Refetching would let the two
    # nodes disagree about what is held, and the second one would win silently.
    book: Book | None
    # Protection handed back this cycle, and the three ways to close what is
    # left. `protection` is the one the mandate's stated order picked; the other
    # two stay in `protection_options` because the journal has to show what was
    # declined, not only what was done.
    released: Release | None
    # One per sleeve. Each symbol is hedged on its own underlying, because a
    # put matched by notional on the largest holding under-covers the
    # higher-beta ones -- 11,700 of a 100,000 promise on the demonstration
    # book. See `remedy.sleeves`.
    protection: list[Remedy]
    # Orders that close protection the client no longer needs. Separate from
    # `protection` because they are the opposite act, and carried at all
    # because for a while they did not exist: `release` returned an answer and
    # nothing could send it, so the journal reported a handback while the puts
    # stayed in the account.
    release_orders: list
    # The facts `journal` hands the language model. Assembled in `protect`,
    # where they exist, and spent in `journal`, after the orders have gone.
    # The model used to be called in `protect` itself, between choosing a
    # strike and sending the order priced off it -- forty-one seconds in which
    # the ask moved and the limit was left behind. Prose about a settled
    # decision has no business preceding the trade it describes.
    narration: dict
    # What moved in the client's book since the last cycle, and the model's
    # read of what it means for the cover. Written by `mandate`, spent by
    # `journal`. Nothing downstream reads the prose: `protect` decides from the
    # ladder exactly as it did before this key existed, which is the property
    # that makes putting a model this early in the cycle safe at all.
    review: dict
    # Who chose each sleeve's structure -- the model or the rule -- and what
    # the other one would have done. The model only ever picks between
    # structures that all close the risk and all expire, so this records a
    # judgement about price, never about whether the promise is kept. Carried
    # so the terminal and the page can show the disagreement without reopening
    # the journal.
    choice: list
    results: list[OrderResult]
    discrepancies: list[str]
    halted: bool
    halt_reason: str
    dry_run: bool


def initial_state(dry_run: bool = False) -> GuardState:
    """A cycle that has not looked at anything yet.

    Every collection starts empty and `portfolio` starts None rather than as a
    zeroed Portfolio. A Portfolio with zero equity and zero exposure would pass
    several risk checks trivially; absence cannot.
    """
    return GuardState(
        positions={},
        portfolio=None,
        ladder=[],
        uncovered_risk=0.0,
        book_complete=True,
        book=None,
        released=None,
        protection=[],
        release_orders=[],
        narration={},
        results=[],
        discrepancies=[],
        halted=False,
        halt_reason="",
        dry_run=dry_run,
    )
