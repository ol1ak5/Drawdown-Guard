"""The cycle, wired.

Linear from reconcile to journal, with exactly one conditional edge: a halt
after reconcile jumps straight to the journal.

`mandate` sits second and `protect` third, both before any market data is
fetched. The stress ladder is built from the positions already held, so it
needs nothing from the market, and running it first means the protection gap is
an input to the cycle's decisions rather than a report written after them.
`protect` then decides what closing that gap would take.

The order is the claim. An agent that picked its trades and then measured the
risk would be checking its own homework, and every number it published would be
a justification rather than a constraint.

That edge is the whole point of using a graph rather than a function that
returns early. An early return can skip the journal — and a cycle that stopped
without writing anything is indistinguishable, six days later, from a cycle
that crashed. Routing the halt *to* the journal makes "we deliberately did
nothing, here is why" a recorded outcome rather than an absence.
"""

from langgraph.graph import END, START, StateGraph

from drawdownguard.agent.nodes import (
    execute_node,
    journal_node,
    mandate_node,
    protect_node,
    reconcile_node,
)
from drawdownguard.agent.state import GuardState, initial_state

# `route`, `candidates` and `optimize` used to sit between `regime` and
# `execute`. They were the options wheel: `route` asked a state machine what to
# do next, and on a book holding shares that machine answers SELL_CALL --
# unconditionally, on every symbol, for income.
#
# Removed on 2026-08-27 rather than left disabled. On the client book this
# project now describes, those three nodes would have sold calls against all of
# the client's equity and capped the upside of a mandate that never asked for
# it, and they would have done it on the first morning of an eight-day
# unattended run. A path that dangerous is not made safe by a flag somebody has
# to remember to set.
#
# Selling a call is not forbidden -- a collar sells one. The difference is that
# a collar sells it to finance a put, sized against the promise, and only when
# the chain makes it the better of the two. That decision belongs to `protect`,
# which is where it now lives.
#
# `snapshot` and `regime` are not here, and their absence is a decision rather
# than an omission.
#
# `regime` called a language model every cycle, wrote the answer to the
# journal, and was read by nothing. It once narrowed the delta band and shrank
# the size multiplier; both of those belonged to the options overlay and left
# with it. What remained was a paid API call whose result could not reach a
# decision -- the most expensive way to have no opinion, and billed daily.
#
# `snapshot` existed to feed it.
#
# `roles/analyst.py` is gone too, and was kept for a while on the argument that
# there is a real job here -- reading what the options market charges for
# protection, and why, which is the one thing a model does better than
# arithmetic. The argument still holds and the code was still dead: nothing but
# its own tests imported it, and a module kept against a future that has not
# arrived is a module a reader has to rule out before understanding the cycle.
# It can come back when it decides something. Until then the honest version of
# "we plan to" is an empty directory.
#
# What remains of the model is two calls, and the shared plumbing lives in
# `drawdownguard/llm.py` -- named for what it is rather than for the role that
# used to own it.
#
# `mandate` asks it what the change in the client's book means for the cover,
# and only on a morning when something actually moved. `journal` asks it to
# write up a decision already finished. The rule both obey is the one `regime`
# broke from the other side: a model may be read by a person, and may not be
# read by an order. Nothing between `mandate` and `execute` looks at either
# answer.
NODES = (
    ("reconcile", reconcile_node),
    ("mandate", mandate_node),
    ("protect", protect_node),
    ("execute", execute_node),
    ("journal", journal_node),
)


def _after_reconcile(state: GuardState) -> str:
    return "journal" if state.get("halted") else "mandate"


def build_graph():
    """The compiled cycle graph."""
    graph = StateGraph(GuardState)
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


async def run_cycle(dry_run: bool = False) -> GuardState:
    """Run one cycle end to end and return the state it finished in."""
    return await build_graph().ainvoke(initial_state(dry_run=dry_run))
