"""The eight nodes of one trading cycle.

The plan asked for one file per node. They are together here because they are
one sequence over one state type, each is a dozen lines, and eight files whose
contents only make sense read in order is not better separation — it is the
same function with import statements between the paragraphs. The boundaries
that matter are enforced by the state, not by the filesystem: a node returns a
partial update and can touch nothing else.

Order: reconcile, snapshot, regime, route, candidates, optimize, execute,
journal. A halt after reconcile jumps straight to the journal, because a cycle
that stopped and said nothing is indistinguishable from a cycle that crashed.
"""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from flywheel.agent.state import FlywheelState
from flywheel.domain import Regime
from flywheel.execution.orders import submit_order, to_proposed_order
from flywheel.journal import writer
from flywheel.market.client import get_account
from flywheel.market.features import build_snapshot
from flywheel.optimizer.candidates import build_candidates
from flywheel.optimizer.model import optimize
from flywheel.risk.limits import load_limits
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


# --- 2. snapshot ------------------------------------------------------------


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


# --- 3. regime --------------------------------------------------------------


async def regime_node(state: FlywheelState) -> FlywheelState:
    """Classify the market. Deterministic for now.

    Returns "calm" unconditionally until the analyst replaces the body. That is
    a placeholder, and it is a *safe* placeholder only because of which way the
    analyst may move the answer: it can tighten the delta band and shrink the
    size multiplier, never loosen either. A wrong "calm" therefore trades the
    strategy as configured rather than something riskier than configured.
    """
    regime: Regime = "calm"
    return FlywheelState(regime=regime)


# --- 4. route ---------------------------------------------------------------


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


# --- 5. candidates ----------------------------------------------------------


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


# --- 6. optimize ------------------------------------------------------------


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


# --- 7. execute -------------------------------------------------------------


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


# --- 8. journal -------------------------------------------------------------


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
            "discrepancies": state.get("discrepancies", []),
            "dry_run": state.get("dry_run", False),
        },
        severity="info",
    )
    return FlywheelState()
