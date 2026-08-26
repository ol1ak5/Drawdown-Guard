"""The ten nodes of one trading cycle.

The plan asked for one file per node. They are together here because they are
one sequence over one state type, each is a dozen lines, and ten files whose
contents only make sense read in order is not better separation — it is the
same function with import statements between the paragraphs. The boundaries
that matter are enforced by the state, not by the filesystem: a node returns a
partial update and can touch nothing else.

Order: reconcile, mandate, protect, snapshot, regime, route, candidates,
optimize, execute, journal. A halt after reconcile jumps straight to the
journal, because a cycle that stopped and said nothing is indistinguishable
from one that crashed.

The first three run before any market data is fetched, and that is the argument
of the whole project rather than an accident of wiring. The agent finds out what
it already owes the client, and what it would take to make good on it, before it
is allowed to look at what it might like to buy.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from flywheel.agent.state import FlywheelState
from flywheel.execution.orders import submit_order, to_proposed_order
from flywheel.journal import writer
from flywheel.market.client import get_account, get_positions
from flywheel.market.features import build_snapshot
from flywheel.optimizer.candidates import build_candidates
from flywheel.optimizer.model import optimize
from flywheel.risk.book import to_book
from flywheel.risk.limits import load_limits
from flywheel.risk.mandate import load_mandate
from flywheel.risk.remedy import collar, protective_put, reduce_exposure, release
from flywheel.risk.stress import gap_at, ladder, unhedged_limit, worst_gap
from flywheel.wheel import next_action

STRATEGY_PATH = Path("config/strategy.yaml")

# The CVaR ceiling handed to the optimizer, as a share of equity. Expressed
# relative to equity for the same reason the delta band is: an absolute dollar
# tail limit means something different at every account size.
CVAR_PCT = 2.0


def strategy(path: str | Path = STRATEGY_PATH) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


# --- 1. reconcile -----------------------------------------------------------


async def reconcile_node(state: FlywheelState) -> FlywheelState:
    """Ask the broker what is actually held, and believe it.

    Halts the cycle on a drawdown breach. The halt is checked here, before any
    market data is fetched, because a kill-switch that only fires after the
    agent has decided what it wants to trade is a kill-switch that has already
    lost the argument.
    """
    limits = load_limits()
    try:
        portfolio, discrepancies = await get_account(state.get("wheels") or {})
    except Exception as exc:  # noqa: BLE001 — a dead cycle must still journal
        return FlywheelState(
            halted=True,
            halt_reason=f"could not read the account: {exc}",
            discrepancies=[],
        )

    for note in discrepancies:
        writer.write("reconcile.discrepancy", {"detail": note}, severity="info")

    if portfolio.drawdown_pct > limits.max_drawdown_pct:
        return FlywheelState(
            portfolio=portfolio,
            wheels=portfolio.wheels,
            discrepancies=discrepancies,
            halted=True,
            halt_reason=(
                f"drawdown {portfolio.drawdown_pct:.1f}% exceeds the "
                f"{limits.max_drawdown_pct:.1f}% kill-switch"
            ),
        )

    return FlywheelState(
        portfolio=portfolio,
        wheels=portfolio.wheels,
        discrepancies=discrepancies,
        halted=False,
    )


# --- 2. mandate -------------------------------------------------------------


async def mandate_node(state: FlywheelState) -> FlywheelState:
    """Rebuild the stress ladder from the positions that actually exist.

    This runs before the agent looks at the market, and that ordering is the
    argument of the whole project. A cycle that first decided what it wanted to
    trade and then measured the risk would be checking its homework. Measuring
    first means the gap is an input to the decision rather than a report on it.

    Recomputed from scratch every cycle rather than carried forward, because
    the gap moves without anyone trading: an option expires overnight and the
    protection it provided is simply gone the next morning. Nothing but
    recomputation notices that.

    The gap the agent acts on is the one at the shock the mandate names, not
    the worst row on the ladder. The deepest rung breaches for any normally
    invested portfolio, so acting on it would mean reporting an unclosable
    deficit every cycle — and hedging a 35% tail costs more than the loss it
    insures. It is disclosed instead. `stress.gap_at` carries the argument.

    A book that cannot be fully priced still produces a ladder, marked
    incomplete. The alternative — refusing to report anything — would leave the
    agent with no risk estimate at all on the day a single quote is missing,
    which is worse than a labelled partial one.
    """
    portfolio = state.get("portfolio")
    if portfolio is None:
        return FlywheelState()

    mandate = load_mandate(strategy().get("mandate", "balanced"))
    try:
        positions = await get_positions()
    except Exception as exc:  # noqa: BLE001 — a missing ladder is not a dead cycle
        writer.write("mandate.unreadable", {"detail": str(exc)}, severity="info")
        return FlywheelState()

    book = to_book(positions, portfolio.cash)
    budget = mandate.budget(portfolio.equity)
    rungs = ladder(book.holdings, book.legs, budget)
    # What the agent is obliged to close, and what it merely has to disclose.
    binding = gap_at(rungs, mandate.binding_shock)
    worst = worst_gap(rungs)
    gap = binding.gap if binding else 0.0

    writer.write(
        "mandate.stress",
        {
            "mandate": mandate.name,
            "downside_budget_pct": mandate.downside_budget_pct,
            "budget": round(budget, 2),
            "equity_exposure": round(book.equity_exposure, 2),
            "binding_shock": mandate.binding_shock,
            "gap": round(gap, 2),
            "unprotected_limit": round(
                unhedged_limit(budget, mandate.binding_shock), 2
            ),
            "complete": book.complete,
            "unpriced": book.unpriced,
            "ladder": [
                {
                    "shock": r.shock,
                    "loss": round(r.portfolio_loss, 2),
                    "from_options": round(r.protected_by_options, 2),
                    "gap": round(r.gap, 2),
                }
                for r in rungs
            ],
            # Disclosed, not promised. The deepest rung nearly always breaches
            # and closing it costs more than it insures; the client is still
            # owed the number.
            "worst_gap": round(worst.gap, 2) if worst else 0.0,
            "worst_shock": worst.shock if worst else None,
        },
        severity="breach" if gap > 0 else "info",
    )
    return FlywheelState(
        ladder=rungs,
        protection_gap=gap,
        book_complete=book.complete,
        book=book,
    )


# --- 3. protect -------------------------------------------------------------


async def protect_node(state: FlywheelState) -> FlywheelState:
    """Close the gap the ladder found, the way the client said to close it.

    Works on the book `mandate` measured, carried in state rather than fetched
    again. Two reads of the same account a second apart can disagree, and the
    second one would win without saying so — the gap would then be closed
    against a book nobody reported.

    RELEASING COMES FIRST, EVEN WITH A GAP OPEN
    --------------------------------------------
    Protection that pays nothing at the promised shock is cleared out before
    anything is bought. That is what makes this a roll rather than an
    accumulation: after a rally the old strike is dead weight, and an agent that
    only ever added would carry every dead strike it had ever bought. Releasing
    it cannot widen the gap, because a leg worth nothing at that rung was not
    holding the promise up.

    ONE SYMBOL, AND THE ASSUMPTION THAT MAKES IT HONEST
    -----------------------------------------------------
    The hedge is placed on the largest exposed holding rather than spread across
    every one. That works because the ladder shocks every equity holding by the
    same percentage, so protection sized on the total gap does close the gap the
    ladder measures. The assumption is uniform, the book is not — QQQ and IWM
    move more than SPY in a real decline — so the assumption is written into the
    journal beside the number rather than left for a reader to discover.

    AN INCOMPLETE BOOK STILL GETS PROTECTED
    ----------------------------------------
    If a position could not be priced the gap is a weaker claim, and the
    temptation is to refuse to act on it. Refusing is not the cautious choice
    here: it leaves a breach open for as long as one quote is missing. Buying
    protection against a partially known book errs toward being over-insured,
    which costs premium; skipping it errs toward being uncovered.

    THE CHOICE IS NOT MADE HERE
    ----------------------------
    All three remedies are computed and all three are journalled. Which one is
    taken comes from `protection_order`, stated by the client in advance. The
    agent has no ranking of its own, because ranking a certain premium against a
    contingent ceiling requires a view on the market and this project does not
    have one.
    """
    portfolio = state.get("portfolio")
    book = state.get("book")
    if portfolio is None or book is None:
        return FlywheelState()

    mandate = load_mandate(strategy().get("mandate", "balanced"))
    budget = mandate.budget(portfolio.equity)
    shock = mandate.binding_shock

    given = release(
        book.holdings, book.legs, budget, shock, mandate.release_margin_pct
    )
    legs = given.kept if given else list(book.legs)
    if given:
        writer.write(
            "protection.released",
            {
                "reason": given.reason,
                "contracts": given.contracts,
                "detail": given.describe,
                "headroom_before": round(given.slack_before, 2),
                "headroom_after": round(given.slack_after, 2),
                "margin_required": round(given.margin_required, 2),
                "tail_given_up": round(given.tail_given_up, 2),
                "tail_shock": given.tail_shock,
                "leaves_ceiling": given.leaves_ceiling,
            },
            severity="info",
        )

    rung = gap_at(ladder(book.holdings, legs, budget), shock)
    gap = rung.gap if rung else 0.0
    if gap <= 0:
        return FlywheelState(released=given, protection_gap=gap)

    exposed = [h for h in book.holdings if h.shocked and h.shares > 0]
    if not exposed:
        # A gap with no shares behind it is a short-option gap, and the answer
        # to that is to stop selling rather than to buy a hedge for it.
        writer.write(
            "protection.no_underlying",
            {"gap": round(gap, 2)},
            severity="breach",
        )
        return FlywheelState(released=given, protection_gap=gap)

    # Imported here rather than at module scope for the same reason
    # `candidates_node` does it: the name is resolved at call time, so a test
    # patching `flywheel.market.chain.load_chain` reaches this node too. A
    # top-level import would bind the real function before any patch was applied
    # and the test would exercise the network it meant to replace.
    from flywheel.market.chain import load_chain

    holding = max(exposed, key=lambda h: h.value)
    config = strategy()
    try:
        puts = await load_chain(
            holding.symbol, "P", config["dte"]["min"], config["dte"]["max"]
        )
        calls = await load_chain(
            holding.symbol, "C", config["dte"]["min"], config["dte"]["max"]
        )
    except Exception as exc:  # noqa: BLE001 — reported, and the gap stays open
        writer.write(
            "protection.chain_unreadable",
            {"symbol": holding.symbol, "gap": round(gap, 2), "detail": str(exc)},
            severity="breach",
        )
        return FlywheelState(released=given, protection_gap=gap)

    offers = [
        remedy
        for remedy in (
            protective_put(
                book.holdings, legs, budget, shock, holding.symbol, holding.price, puts
            ),
            collar(
                book.holdings,
                legs,
                budget,
                shock,
                holding.symbol,
                holding.price,
                puts,
                calls,
            ),
            reduce_exposure(book.holdings, legs, budget, shock, holding.symbol),
        )
        if remedy is not None
    ]
    rank = {kind: order for order, kind in enumerate(mandate.protection_order)}
    closing = [remedy for remedy in offers if remedy.closes_the_gap]
    chosen = min(closing, key=lambda r: rank[r.kind]) if closing else None

    writer.write(
        "protection.plan",
        {
            "mandate": mandate.name,
            "symbol": holding.symbol,
            "spot": holding.price,
            "gap": round(gap, 2),
            "book_complete": book.complete,
            # Stated, not implied: the ladder moves every equity holding by the
            # same percentage, so one symbol can carry the whole hedge.
            "assumes": "a uniform shock across every exposed holding",
            "preference": list(mandate.protection_order),
            "offers": [
                {
                    "kind": remedy.kind,
                    "detail": remedy.describe,
                    "premium_cost": round(remedy.premium_cost, 2),
                    "forgone_upside": round(remedy.forgone_upside, 2),
                    "upside_measured_at": remedy.upside_measured_at,
                    "gap_after": round(remedy.gap_after, 2),
                    "closes_the_gap": remedy.closes_the_gap,
                }
                for remedy in offers
            ],
            "chosen": chosen.kind if chosen else None,
        },
        # Still a breach until something is actually placed. A plan is not a
        # position, and the journal should not read as though it were.
        severity="breach",
    )
    return FlywheelState(
        released=given,
        protection=chosen,
        protection_options=offers,
        protection_gap=gap,
    )


# --- 4. snapshot ------------------------------------------------------------


async def snapshot_node(state: FlywheelState) -> FlywheelState:
    """One market snapshot per symbol in the universe.

    A symbol whose snapshot fails is dropped rather than defaulted. A snapshot
    invented from nothing would produce candidates priced against a volatility
    nobody observed.
    """
    snapshots = {}
    for symbol in strategy()["universe"]:
        try:
            snapshots[symbol] = await build_snapshot(symbol)
        except Exception as exc:  # noqa: BLE001
            writer.write(
                "snapshot.failed",
                {"symbol": symbol, "detail": str(exc)},
                severity="info",
            )
    return FlywheelState(snapshots=snapshots)


# --- 5. regime --------------------------------------------------------------


async def regime_node(state: FlywheelState) -> FlywheelState:
    """Ask the analyst which volatility regime this is.

    The analyst proposes; it does not decide. Its answer can only move the
    agent along `calm -> elevated -> stress -> crash`, and every step narrows
    the delta band and shrinks the size multiplier. There is no value it can
    return that loosens a limit or approves a trade the gate would refuse, so
    the worst a wrong or compromised answer costs is a skipped cycle.

    A failure lands on `stress`, never on `calm`. An analyst that could not
    answer is not evidence of a calm market.

    The rendered prompt is journalled verbatim. A decision that cannot be
    reproduced line by line is not auditable, and the prompt is half of what
    produced this one.
    """
    from flywheel.agent.roles.analyst import classify_regime

    regime, rationale, prompt = await classify_regime(state.get("snapshots") or {})
    writer.write(
        "regime.classified",
        {"regime": regime, "rationale": rationale, "prompt": prompt},
        severity="info",
    )
    return FlywheelState(regime=regime, regime_rationale=rationale)


# --- 6. route ---------------------------------------------------------------


async def route_node(state: FlywheelState) -> FlywheelState:
    """Which symbols have something to do this cycle.

    A symbol with a position already open returns HOLD and is dropped. This is
    where the wheel decides put or call — from the leg it is on, not from any
    model's opinion.
    """
    wheels = state.get("wheels") or {}
    actionable = [
        symbol
        for symbol in state.get("snapshots", {})
        if next_action(wheels.get(symbol) or _resting(symbol)) != "HOLD"
    ]
    return FlywheelState(actionable=actionable)


def _resting(symbol: str):
    from flywheel.domain import WheelState

    return WheelState(symbol=symbol)


# --- 7. candidates ----------------------------------------------------------


async def candidates_node(state: FlywheelState) -> FlywheelState:
    """Build the tradable set for every actionable symbol.

    The delta band comes from the regime. Nothing here chooses a strike: it
    filters to the contracts that are choices at all, and the optimizer picks
    among them.
    """
    from flywheel.market.chain import load_chain

    config = strategy()
    band = config["target_delta"][state.get("regime", "calm")]
    limits = load_limits()
    wheels = state.get("wheels") or {}
    today = date.today()

    candidates = []
    for symbol in state.get("actionable", []):
        snapshot = state["snapshots"][symbol]
        wheel = wheels.get(symbol) or _resting(symbol)
        right = "P" if next_action(wheel) == "SELL_PUT" else "C"
        try:
            rows = await load_chain(
                symbol, right, config["dte"]["min"], config["dte"]["max"], today
            )
        except Exception as exc:  # noqa: BLE001
            writer.write(
                "chain.failed", {"symbol": symbol, "detail": str(exc)}, severity="info"
            )
            continue

        # Never write a call below what the shares cost. The premium is not
        # worth locking in a loss on the stock.
        if right == "C" and wheel.basis is not None:
            rows = [r for r in rows if r["strike"] >= wheel.basis]

        candidates.extend(
            build_candidates(
                chain_rows=rows,
                spot=snapshot.spot,
                symbol=symbol,
                right=right,
                as_of=today,
                limits=limits,
                returns=snapshot.returns,
                target_delta=(band["min"], band["max"]),
            )
        )
    return FlywheelState(candidates=candidates)


# --- 8. optimize ------------------------------------------------------------


async def optimize_node(state: FlywheelState) -> FlywheelState:
    """Choose how many of what to sell. Allocating nothing is a valid answer."""
    portfolio = state.get("portfolio")
    candidates = state.get("candidates") or []
    if portfolio is None or not candidates:
        return FlywheelState(allocations=[])

    config = strategy()
    limits = load_limits()
    multiplier = config["size_multiplier"][state.get("regime", "calm")]
    budget = (
        portfolio.equity
        * Decimal(str(limits.max_deployed_pct / 100))
        * Decimal(str(multiplier))
    )
    allocations = optimize(
        candidates=candidates,
        portfolio=portfolio,
        limits=limits,
        capital_budget=budget,
        cvar_limit=float(portfolio.equity) * CVAR_PCT / 100,
    )
    return FlywheelState(allocations=[a for a in allocations if a.contracts > 0])


# --- 9. execute -------------------------------------------------------------


async def execute_node(state: FlywheelState) -> FlywheelState:
    """Submit each allocation. Every one goes through the gate inside
    `submit_order`; nothing here can skip it."""
    portfolio = state.get("portfolio")
    if portfolio is None:
        return FlywheelState(results=[])

    limits = load_limits()
    results = []
    for allocation in state.get("allocations", []):
        order = to_proposed_order(allocation)
        result = await submit_order(
            order, portfolio, limits, dry_run=state.get("dry_run", False)
        )
        results.append(result)
        writer.write(
            "order.submitted" if result.submitted else "order.refused",
            {
                "symbol": order.symbol,
                "occ_symbol": result.occ_symbol,
                "contracts": order.contracts,
                "limit_price": str(order.limit_price),
                "delta": order.delta,
                "assignment_prob": order.assignment_prob,
                "reason": result.reason,
                "broker_order_id": result.broker_order_id,
            },
            severity="info" if result.submitted else "veto",
        )
    return FlywheelState(results=results)


# --- 10. journal -------------------------------------------------------------


async def journal_node(state: FlywheelState) -> FlywheelState:
    """Write what this cycle decided, including deciding nothing.

    A cycle that skipped is the most common outcome and the most informative
    one. Journalling only the cycles that traded would make the record look
    like a strategy that trades constantly and never explains itself.
    """
    portfolio = state.get("portfolio")
    writer.write(
        "cycle.complete",
        {
            "regime": state.get("regime"),
            "regime_rationale": state.get("regime_rationale", ""),
            "halted": state.get("halted", False),
            "halt_reason": state.get("halt_reason", ""),
            "equity": str(portfolio.equity) if portfolio else None,
            "net_delta": portfolio.net_delta if portfolio else None,
            "net_delta_value": portfolio.net_delta_value if portfolio else None,
            "vega": portfolio.vega if portfolio else None,
            "actionable": state.get("actionable", []),
            "candidates": len(state.get("candidates") or []),
            "allocations": len(state.get("allocations") or []),
            "submitted": sum(1 for r in state.get("results", []) if r.submitted),
            "refused": sum(1 for r in state.get("results", []) if not r.submitted),
            "protection_gap": state.get("protection_gap", 0.0),
            "book_complete": state.get("book_complete", True),
            # What the agent would do about the gap, and what it gave back.
            # `protection` being None with a non-zero gap is the case worth
            # spotting in a week of logs: the promise is broken and nothing on
            # offer closes it.
            "protection": (
                state["protection"].kind if state.get("protection") else None
            ),
            "protection_offers": len(state.get("protection_options") or []),
            "released": (
                state["released"].contracts if state.get("released") else 0
            ),
            "discrepancies": state.get("discrepancies", []),
            "dry_run": state.get("dry_run", False),
        },
        severity="info",
    )
    return FlywheelState()
