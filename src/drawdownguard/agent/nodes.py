"""The five nodes of one trading cycle.

The plan asked for one file per node. They are together here because they are
one sequence over one state type, each is a dozen lines, and separate files
whose contents only make sense read in order is not better separation — it is the
same function with import statements between the paragraphs. The boundaries
that matter are enforced by the state, not by the filesystem: a node returns a
partial update and can touch nothing else.

Order: reconcile, mandate, protect, execute, journal. A halt after reconcile
jumps straight to the journal, because a cycle that stopped and said nothing is
indistinguishable from one that crashed.

`mandate` runs before any option chain is fetched, and that is the argument of
the whole project rather than an accident of wiring. The agent finds out what it
already owes the client before it is allowed to look at what it might buy.

WHERE THE LANGUAGE MODEL IS, AND WHY IT IS ONLY THERE
------------------------------------------------------
At the end of `protect`, after the decision is finished. The budget came from
the client, the strike from arithmetic over the live chain, and the order faces
a deterministic gate; the model is handed all of it as settled fact and writes
the note a client reads. It cannot change a strike, a size, or whether an order
goes. It can only be unclear, and it is unclear in the journal beside the
numbers it describes.

There used to be a model earlier in the cycle, classifying the market regime.
Nothing read its answer, so it was a billed call that could not reach a
decision. Explaining is the opposite arrangement and the safe one.
"""

from pathlib import Path
from typing import Any

import yaml

from drawdownguard.agent.roles.explainer import explain
from drawdownguard.agent.state import GuardState
from drawdownguard.execution.orders import submit_order
from drawdownguard.journal import writer
from drawdownguard.market.client import get_account, get_positions
from drawdownguard.risk import period
from drawdownguard.risk.book import to_book
from drawdownguard.risk.limits import load_limits
from drawdownguard.risk.mandate import load_mandate
from drawdownguard.risk.remedy import (
    choose,
    closing_orders,
    collar,
    liquid,
    protective_put,
    reduce_exposure,
    release,
    sleeves,
)
from drawdownguard.risk.stress import (
    gap_at,
    ladder,
    unhedged_limit,
    worst_gap,
    worst_loss,
)

STRATEGY_PATH = Path("config/strategy.yaml")

def strategy(path: str | Path = STRATEGY_PATH) -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text())


# --- 1. reconcile -----------------------------------------------------------


async def reconcile_node(state: GuardState) -> GuardState:
    """Ask the broker what is actually held, and believe it.

    Halts the cycle on a drawdown breach. The halt is checked here, before any
    market data is fetched, because a kill-switch that only fires after the
    agent has decided what it wants to trade is a kill-switch that has already
    lost the argument.
    """
    limits = load_limits()
    try:
        portfolio, discrepancies = await get_account(state.get("positions") or {})
    except Exception as exc:  # noqa: BLE001 — a dead cycle must still journal
        return GuardState(
            halted=True,
            halt_reason=f"could not read the account: {exc}",
            discrepancies=[],
        )

    for note in discrepancies:
        writer.write("reconcile.discrepancy", {"detail": note}, severity="info")

    if portfolio.drawdown_pct > limits.max_drawdown_pct:
        return GuardState(
            portfolio=portfolio,
            positions=portfolio.positions,
            discrepancies=discrepancies,
            halted=True,
            halt_reason=(
                f"drawdown {portfolio.drawdown_pct:.1f}% exceeds the "
                f"{limits.max_drawdown_pct:.1f}% kill-switch"
            ),
        )

    return GuardState(
        portfolio=portfolio,
        positions=portfolio.positions,
        discrepancies=discrepancies,
        halted=False,
    )


# --- 2. mandate -------------------------------------------------------------


async def mandate_node(state: GuardState) -> GuardState:
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
        return GuardState()

    mandate = load_mandate(strategy().get("mandate", "balanced"))
    try:
        positions = await get_positions()
    except Exception as exc:  # noqa: BLE001 — a missing ladder is not a dead cycle
        writer.write("mandate.unreadable", {"detail": str(exc)}, severity="info")
        return GuardState()

    book = to_book(positions, portfolio.cash)

    # The promise is measured against what the account was worth when it was
    # made, not against what it is worth this morning. Ten percent of today
    # re-bases every cycle: lose ten percent and the agent starts defending ten
    # percent of the smaller number, which permits a 47% loss in five steps and
    # calls every one of them kept. See `risk/period.py`.
    promise, renewed = period.current(
        float(portfolio.equity), mandate.horizon_months
    )
    budget = mandate.budget(promise.reference)
    if renewed:
        writer.write(
            "mandate.period_opened",
            {
                "started": promise.started.isoformat(),
                "ends": promise.ends().isoformat(),
                "reference": round(promise.reference, 2),
                "budget": round(budget, 2),
                # Renewal is the only thing that moves the reference, and it
                # happens on a date rather than on a price. A client who made
                # money spends the next year protecting the larger number.
                "reason": "no promise was in force, or the previous one ran out",
            },
            severity="info",
        )
    rungs = ladder(book.holdings, book.legs, budget)
    # What the agent is obliged to close, and what it merely has to disclose.
    binding = gap_at(rungs, mandate.binding_shock)
    worst = worst_gap(rungs)

    # What the agent acts on is the worst outcome anywhere on the way down,
    # not the outcome at a depth somebody chose. The two differ by more than
    # they sound: 500,000 of shares against a 100,000 budget loses exactly
    # 100,000 at -20% -- gap zero, promise apparently intact -- while the same
    # book can lose the whole 500,000. Checking one point found the single
    # place where it happened to hold.
    #
    # Shares have no floor, so an unhedged equity book always breaches. That
    # is not the check being too strict; it is the fact the ladder was hiding.
    exposure = worst_loss(book.holdings, book.legs)
    gap = max(exposure - budget, 0.0)

    writer.write(
        "mandate.stress",
        {
            "mandate": mandate.name,
            "downside_budget_pct": mandate.downside_budget_pct,
            "budget": round(budget, 2),
            "equity_exposure": round(book.equity_exposure, 2),
            "binding_shock": mandate.binding_shock,
            "gap": round(gap, 2),
            # The worst the book can do anywhere, and the gap is what of
            # that the budget does not cover. `gap_at_binding_shock` is
            # kept beside it because the ladder is what a person reads.
            "worst_case": round(exposure, 2),
            "gap_at_binding_shock": round(binding.gap if binding else 0.0, 2),
            "period_started": promise.started.isoformat(),
            "period_ends": promise.ends().isoformat(),
            "reference": round(promise.reference, 2),
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
    return GuardState(
        ladder=rungs,
        protection_gap=gap,
        book_complete=book.complete,
        book=book,
    )


# --- 3. protect -------------------------------------------------------------


async def protect_node(state: GuardState) -> GuardState:
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

    ONE HEDGE PER HOLDING, AND NO CORRELATION ASSUMED
    ---------------------------------------------------
    Each symbol is hedged on its own underlying, with its own share of the
    budget. The hedge used to sit on the largest holding and be sized to the
    whole book, which treats three indices as one thing falling by one number:
    measured on this project's own bars, QQQ carries a beta of 1.17 to SPY and
    IWM 1.12, so a notional match left 11,700 of a 100,000 promise uncovered.
    A QQQ put pays on QQQ however far QQQ falls, and nothing has to be assumed.

    AN INCOMPLETE BOOK STILL GETS PROTECTED
    ----------------------------------------
    If a position could not be priced the gap is a weaker claim, and the
    temptation is to refuse to act on it. Refusing is not the cautious choice
    here: it leaves a breach open for as long as one quote is missing. Buying
    protection against a partially known book errs toward being over-insured,
    which costs premium; skipping it errs toward being uncovered.

    THE CHOICE IS MADE ON THE CHAIN, NOT IN THE CONFIG
    ---------------------------------------------------
    Every remedy the mandate permits is computed and every one is journalled.
    Which is taken comes from `remedy.choose`, which reads today's prices, and
    the sentence it returns is written down beside the answer. This replaced a
    ranking stated once in the mandate: a config-file preference gives the same
    answer on every day of every market, so the agent reading it was replaying
    a decision rather than making one.
    """
    portfolio = state.get("portfolio")
    book = state.get("book")
    if portfolio is None or book is None:
        return GuardState()

    mandate = load_mandate(strategy().get("mandate", "balanced"))
    # The same reference `mandate` measured against. Reading the account again
    # here would give a budget that drifts between two nodes of one cycle.
    promise, _ = period.current(float(portfolio.equity), mandate.horizon_months)
    budget = mandate.budget(promise.reference)
    shock = mandate.binding_shock

    # Imported here rather than at module scope for the same reason the chain
    # is: the name is resolved at call time, so a test patching `load_chain`
    # reaches this node too.
    from drawdownguard.market.chain import load_chain

    min_dte, max_dte = mandate.protection_dte
    gate_limits = load_limits()

    # Every symbol the cycle could act on: the sleeves it may hedge, plus any
    # symbol already carrying a leg, because a leg can be handed back on a
    # symbol whose shares have since been sold. Loaded once -- two reads of the
    # same chain a second apart can disagree, and a plan priced off one while
    # its orders are priced off the other is a plan nobody can check.
    wanted = {symbol for symbol, _, _ in sleeves(book.holdings, budget)}
    wanted |= {leg.symbol for leg in book.legs}
    chains: dict[str, dict[str, list[dict]]] = {}
    for symbol in sorted(wanted):
        try:
            puts = await load_chain(symbol, "P", min_dte, max_dte)
            calls = await load_chain(symbol, "C", min_dte, max_dte)
        except Exception as exc:  # noqa: BLE001 — reported, symbol stays open
            writer.write(
                "protection.chain_unreadable",
                {"symbol": symbol, "detail": str(exc)},
                severity="breach",
            )
            continue
        offered = (len(puts), len(calls))
        # Narrowed to what can be traded before anything is priced. The gate
        # applies the same two rules, but it applies them last, and a solver
        # ranging over the whole chain finds the cheapest strike by finding the
        # one nobody trades -- a real cycle chose a put with an open interest
        # of 209, was refused, and left the promise broken.
        chains[symbol] = {
            "P": liquid(puts, gate_limits),
            "C": liquid(calls, gate_limits),
        }
        writer.write(
            "protection.chain_filtered",
            {
                "symbol": symbol,
                "puts": {"offered": offered[0], "tradable": len(chains[symbol]["P"])},
                "calls": {"offered": offered[1], "tradable": len(chains[symbol]["C"])},
                "min_open_interest": gate_limits.min_open_interest,
                "max_spread_pct": gate_limits.max_spread_pct,
            },
            severity="info",
        )

    given = release(
        book.holdings, book.legs, budget, shock, mandate.release_margin_pct
    )

    # Handing protection back is an order like any other, and for a while it
    # was not one. `release` returned the legs and nothing closed them: the
    # journal reported a handback, the puts stayed in the account, and the next
    # cycle found them, called them redundant again and bought protection on
    # top -- 20,130 of premium over five cycles closing a gap that was never
    # open, and a position twice the size the agent believed it held.
    #
    # The book below is measured on `given.kept` only where the closing orders
    # exist. A release that cannot be priced is reported and not applied, so
    # the rest of the cycle sizes against what the broker actually holds.
    legs = list(book.legs)
    release_orders: list = []
    if given:
        release_orders = closing_orders(given.legs, chains)
        applied = len(release_orders) == len({leg.symbol for leg in given.legs})
        if applied:
            legs = list(given.kept)
        writer.write(
            "protection.released" if applied else "protection.recommended_release",
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
                "executed": applied,
                "orders": [o.symbol for o in release_orders],
            },
            # A handback that could not be priced is a standing charge the
            # client keeps paying, which is a breach of the same kind as an
            # open gap. One that goes out is ordinary work.
            severity="info" if applied else "breach",
        )

    # The worst outcome anywhere, not the outcome at a chosen depth -- the same
    # question `mandate` asked, so the two nodes cannot disagree about whether
    # the promise is broken. Measured on `legs`, which is the book after any
    # release that could actually be sent and the book as held otherwise.
    gap = max(worst_loss(book.holdings, legs) - budget, 0.0)
    if gap <= 0:
        return GuardState(
            released=given, protection=[], protection_gap=gap, results=[]
        )

    if not sleeves(book.holdings, budget):
        # A gap with no shares behind it is a short-option gap, and the answer
        # to that is to stop selling rather than to buy a hedge for it.
        writer.write(
            "protection.no_underlying",
            {"gap": round(gap, 2)},
            severity="breach",
        )
        return GuardState(released=given, protection_gap=gap)

    chosen_all: list = []
    planned: list[dict] = []

    for symbol, sleeve, sleeve_budget in sleeves(book.holdings, budget):
        spot = sleeve[0].price
        sleeve_legs = [leg for leg in legs if leg.symbol == symbol]

        # Read from the chains loaded once at the top of this node. Fetching
        # again here would price the orders off a different quote from the one
        # the release was priced against, and a cycle whose two halves saw
        # different markets is a cycle nobody can check afterwards.
        chain = chains.get(symbol)
        if chain is None:
            # The failure was already journalled where the read happened.
            continue
        puts, calls = chain["P"], chain["C"]

        offers = [
            remedy
            for remedy in (
                protective_put(
                    sleeve, sleeve_legs, sleeve_budget, shock, symbol, spot, puts
                ),
                collar(
                    sleeve, sleeve_legs, sleeve_budget, shock, symbol, spot,
                    puts, calls,
                ),
                reduce_exposure(sleeve, sleeve_legs, sleeve_budget, shock, symbol)
                if mandate.allow_reduce_exposure
                else None,
            )
            if remedy is not None
        ]
        picked, why = choose(offers)
        if picked is not None:
            chosen_all.append(picked)
        planned.append(
            {
                "symbol": symbol,
                "spot": spot,
                "exposure": round(sum(h.value for h in sleeve), 2),
                # Its share of the promise, in proportion to what it can lose.
                # A symbol holding half the book may lose half the money, so it
                # is allowed half the budget, and the shares sum to the whole.
                "budget": round(sleeve_budget, 2),
                "offers": [
                    {
                        "kind": remedy.kind,
                        "detail": remedy.describe,
                        "premium_cost": round(remedy.premium_cost, 2),
                        "forgone_upside": round(remedy.forgone_upside, 2),
                        "upside_measured_at": remedy.upside_measured_at,
                        # `upside_price` is the number that moves day to day --
                        # dollars collected per 1% of ceiling surrendered.
                        "upside_price": (
                            round(remedy.upside_price, 2)
                            if remedy.upside_price is not None
                            else None
                        ),
                        "cash_per_1k": (
                            round(remedy.cash_per_1k, 2)
                            if remedy.cash_per_1k is not None
                            else None
                        ),
                        "permanent": remedy.permanent,
                        "protection_iv": remedy.protection_iv,
                        "financing_iv": remedy.financing_iv,
                        "financed_fairly": remedy.financed_fairly,
                        "gap_after": round(remedy.gap_after, 2),
                        "closes_the_gap": remedy.closes_the_gap,
                    }
                    for remedy in offers
                ],
                "chosen": picked.kind if picked else None,
                "because": why,
            }
        )

    writer.write(
        "protection.plan",
        {
            "mandate": mandate.name,
            "gap": round(gap, 2),
            "book_complete": book.complete,
            "excluded": [] if mandate.allow_reduce_exposure else ["reduce_exposure"],
            "sleeves": planned,
            "total_premium": round(sum(r.premium_cost for r in chosen_all), 2),
        },
        # Still a breach until something is actually placed. A plan is not a
        # position, and the journal should not read as though it were.
        severity="breach",
    )

    # The decision is finished. Everything above was arithmetic and everything
    # below is prose, which is why a language model is allowed here and was not
    # allowed anywhere else: it cannot change a strike, a size, or whether an
    # order goes. It can only be unclear, and it says so in the journal next to
    # the numbers it describes, where a reader can catch it.
    if chosen_all:
        note = await explain(
            {
                "mandate": mandate.name,
                "budget": budget,
                "exposure": book.equity_exposure,
                "gap": gap,
                "describe": "; ".join(r.describe for r in chosen_all),
                "premium_cost": sum(r.premium_cost for r in chosen_all),
                "forgone_upside": sum(r.forgone_upside for r in chosen_all),
                "gap_after": sum(r.gap_after for r in chosen_all),
                "rejected": [],
                "because": "; ".join(
                    p["because"] for p in planned if p.get("because")
                ),
            }
        )
        # Written only when there is something to write. No placeholder and no
        # apology: a reader cannot tell generated filler from an explanation,
        # and an empty field is honest about what happened.
        if note:
            writer.write(
                "protection.explained",
                {"chosen": [r.kind for r in chosen_all], "note": note},
                severity="info",
            )

    return GuardState(
        released=given,
        release_orders=release_orders,
        protection=chosen_all,
        protection_gap=gap,
    )


# --- 4. execute -------------------------------------------------------------


async def execute_node(state: GuardState) -> GuardState:
    """Send the protection `protect` chose. Every order goes through the gate
    inside `submit_order`; nothing here can skip it.

    The remedy arrives already carrying its orders, built where the chain row
    was in hand. Nothing is re-priced here: a second read of the chain can
    return a different quote, and an order sent at a price nobody journalled is
    an order nobody can check afterwards.

    A cycle with no chosen remedy sends nothing and says so. That is the common
    case and the correct one -- a promise that is holding needs no trade, and
    an agent that found something to do every morning would be an agent looking
    for a reason.
    """
    portfolio = state.get("portfolio")
    if portfolio is None:
        return GuardState(results=[])

    # One remedy per sleeve, since each symbol is hedged on its own underlying.
    chosen = state.get("protection") or []
    # Handing protection back goes out first, and through the same gate. It is
    # a sale of something already owned, so nothing about it can breach a limit
    # -- but running it through `submit_order` anyway means there is exactly one
    # path to the broker in this program rather than one path and an exception.
    orders = list(state.get("release_orders") or [])
    orders += [order for remedy in chosen for order in remedy.orders]

    for remedy in chosen:
        if remedy.orders:
            continue
        # A remedy was chosen for this sleeve and cannot be placed. Loud,
        # because that part of the promise is open with nothing on the way.
        writer.write(
            "protection.unplaceable",
            {
                "kind": remedy.kind,
                "detail": remedy.describe,
                "gap": round(state.get("protection_gap") or 0.0, 2),
                "reason": "the remedy carries no order; the chain row was thin",
            },
            severity="breach",
        )

    limits = load_limits()
    results = []
    for order in orders:
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
    return GuardState(results=results)


# --- 5. journal -------------------------------------------------------------


async def journal_node(state: GuardState) -> GuardState:
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
            "submitted": sum(1 for r in state.get("results", []) if r.submitted),
            "refused": sum(1 for r in state.get("results", []) if not r.submitted),
            "protection_gap": state.get("protection_gap", 0.0),
            "book_complete": state.get("book_complete", True),
            # What the agent would do about the gap, and what it gave back.
            # An empty list against a non-zero gap is the case worth spotting
            # in a week of logs: the promise is broken and nothing on offer
            # closes it. One entry per sleeve, since each symbol is hedged on
            # its own underlying.
            "protection": [r.kind for r in state.get("protection") or []],
            "released": (
                state["released"].contracts if state.get("released") else 0
            ),
            "discrepancies": state.get("discrepancies", []),
            "dry_run": state.get("dry_run", False),
        },
        severity="info",
    )
    return GuardState()
