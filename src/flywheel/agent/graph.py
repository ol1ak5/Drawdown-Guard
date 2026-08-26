"""The cycle, wired.

Linear from reconcile to journal, with exactly one conditional edge: a halt
after reconcile jumps straight to the journal.

`mandate` sits second, before any market data is fetched. The stress ladder is
built from the positions already held, so it needs nothing from the market, and
running it first means the protection gap is an input to the cycle's decisions
rather than a report written after them.

That edge is the whole point of using a graph rather than a function that
returns early. An early return can skip the journal — and a cycle that stopped
without writing anything is indistinguishable, six days later, from a cycle
that crashed. Routing the halt *to* the journal makes "we deliberately did
nothing, here is why" a recorded outcome rather than an absence.
"""

from langgraph.graph import END, START, StateGraph

from flywheel.agent.nodes import (
    candidates_node,
    execute_node,
    journal_node,
    mandate_node,
    optimize_node,
    reconcile_node,
    regime_node,
    route_node,
    snapshot_node,
)
from flywheel.agent.state import FlywheelState, initial_state

NODES = (
    ("reconcile", reconcile_node),
    ("mandate", mandate_node),
    ("snapshot", snapshot_node),
    ("regime", regime_node),
    ("route", route_node),
    ("candidates", candidates_node),
    ("optimize", optimize_node),
    ("execute", execute_node),
    ("journal", journal_node),
)


def _after_reconcile(state: FlywheelState) -> str:
    return "journal" if state.get("halted") else "mandate"


def build_graph():
    """The compiled cycle graph."""
    graph = StateGraph(FlywheelState)
    for name, node in NODES:
        graph.add_node(name, node)

    graph.add_edge(START, "reconcile")
    graph.add_conditional_edges(
        "reconcile", _after_reconcile, {"journal": "journal", "mandate": "mandate"}
    )
    linear = [name for name, _ in NODES[1:]]
    for source, target in zip(linear, linear[1:], strict=False):
        graph.add_edge(source, target)
    graph.add_edge("journal", END)
    return graph.compile()


async def run_cycle(dry_run: bool = False) -> FlywheelState:
    """Run one cycle end to end and return the state it finished in."""
    return await build_graph().ainvoke(initial_state(dry_run=dry_run))
