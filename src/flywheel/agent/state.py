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

from flywheel.domain import Portfolio, Regime, WheelState
from flywheel.execution.orders import OrderResult
from flywheel.market.features import MarketSnapshot
from flywheel.optimizer.candidates import Candidate
from flywheel.optimizer.model import Allocation


class FlywheelState(TypedDict, total=False):
    """One cycle's working set.

    `total=False` so a node may return only the keys it changed. Requiring
    every key in every update would make each node responsible for carrying
    state it has no opinion about, which is how a node ends up quietly
    resetting something it never meant to touch.
    """

    snapshots: dict[str, MarketSnapshot]
    wheels: dict[str, WheelState]
    portfolio: Portfolio | None
    regime: Regime
    actionable: list[str]
    candidates: list[Candidate]
    allocations: list[Allocation]
    results: list[OrderResult]
    discrepancies: list[str]
    halted: bool
    halt_reason: str
    dry_run: bool


def initial_state(dry_run: bool = False) -> FlywheelState:
    """A cycle that has not looked at anything yet.

    Every collection starts empty and `portfolio` starts None rather than as a
    zeroed Portfolio. A Portfolio with zero equity and zero exposure would pass
    several risk checks trivially; absence cannot.
    """
    return FlywheelState(
        snapshots={},
        wheels={},
        portfolio=None,
        regime="calm",
        actionable=[],
        candidates=[],
        allocations=[],
        results=[],
        discrepancies=[],
        halted=False,
        halt_reason="",
        dry_run=dry_run,
    )
