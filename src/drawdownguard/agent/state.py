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

from drawdownguard.domain import Portfolio, Position, Regime
from drawdownguard.execution.orders import OrderResult
from drawdownguard.market.features import MarketSnapshot
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

    snapshots: dict[str, MarketSnapshot]
    positions: dict[str, Position]
    portfolio: Portfolio | None
    # What today's book loses at each published shock, and by how much the
    # worst of them breaks the client's promise. `protection_gap` is dollars,
    # zero when the mandate holds.
    ladder: list[Rung]
    protection_gap: float
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
    protection: Remedy | None
    protection_options: list[Remedy]
    regime: Regime
    regime_rationale: str
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
        snapshots={},
        positions={},
        portfolio=None,
        ladder=[],
        protection_gap=0.0,
        book_complete=True,
        book=None,
        released=None,
        protection=None,
        protection_options=[],
        regime="calm",
        regime_rationale="",
        results=[],
        discrepancies=[],
        halted=False,
        halt_reason="",
        dry_run=dry_run,
    )
