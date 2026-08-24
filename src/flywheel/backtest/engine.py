"""The backtest engine: the live modules, driven by history.

The engine imports `optimizer` and `risk` rather than reimplementing them. That
is the entire argument. A backtest running different code from the agent
measures a strategy nobody is going to trade, and the moment `veto` is
duplicated here the claim collapses.

WHAT THE HISTORICAL DATA ACTUALLY IS
------------------------------------
Alpaca's option history endpoint returns daily **bars** — open, high, low,
close, volume, vwap. It does not return quotes. There is no historical bid, no
ask, no open interest and no implied volatility. The plan for this task assumed
quotes, so three quantities are modelled here. Each one is named, parameterised
and reported in `BacktestResult.params`, because a modelled number that looks
like a measured one is the thing that makes a backtest dishonest.

- **Execution price.** There is no bid to sell at, so one is constructed: the
  bar close less a haircut. The fill is booked at that constructed bid and
  never at the mid. Mid-pricing is the most common way a wheel backtest invents
  returns nobody could have captured, and the haircut is the stand-in for the
  spread that really would have been crossed.
- **Implied volatility** is solved for from the bar close, so the delta band,
  the vega budget and the assignment estimate all come from the price actually
  printed rather than from a guess.
- **Open interest does not exist historically at all.** Substituting volume for
  it would put a fabricated number into a field the risk gate reads. Instead
  the engine requires the contract to have genuinely traded that day — a
  stricter test of "could this have been filled", and a measured one — and
  disables the open-interest check explicitly. See `DISABLED_CHECKS`.

NO LOOKAHEAD, ENFORCED STRUCTURALLY
-----------------------------------
Every read of a price goes through `Cursor`, which raises on any timestamp at
or after the day being decided. Filtering a chain by information that only
existed later is the leak that produces the too-good result, and it is easy to
introduce without noticing.

Assignment is resolved by the actual underlying close on the actual expiry
date. Never by `assignment_prob`, which exists only to inform the gate before
the fact.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pandas as pd
from pydantic import BaseModel, ConfigDict
from scipy.optimize import brentq

from flywheel.backtest.data import return_scenarios
from flywheel.domain import (
    SHARES_PER_CONTRACT,
    OpenContract,
    Portfolio,
    ProposedOrder,
    Right,
    WheelState,
)
from flywheel.execution.reconcile import parse_occ
from flywheel.optimizer.candidates import Candidate, build_candidates
from flywheel.optimizer.model import optimize
from flywheel.optimizer.payoff import bs_price
from flywheel.risk.gate import veto
from flywheel.risk.limits import Limits
from flywheel.wheel import (
    next_action,
    on_call_assigned,
    on_expired_worthless,
    on_put_assigned,
    on_sold_call,
    on_sold_put,
)

# The checks the historical data cannot support. Anything listed here is turned
# off in the open and reported in the result, rather than fed a made-up input.
DISABLED_CHECKS = ("min_open_interest",)

DEFAULT_HAIRCUT_PCT = 2.0
VOL_BOUNDS = (0.01, 3.0)


class LookaheadError(Exception):
    """A price was requested that had not been printed yet."""


class Cursor:
    """The day being decided. Nothing at or after it may be read.

    Deliberately a small object rather than a convention: a rule that has to be
    remembered at every call site is a rule that will be forgotten at one.
    """

    def __init__(self, today: date) -> None:
        self.today = today

    def check(self, stamp: date) -> None:
        if stamp > self.today:
            raise LookaheadError(
                f"asked for {stamp} while deciding {self.today}; "
                f"that price had not printed yet"
            )


def backtest_limits(limits: Limits) -> Limits:
    """The live limits, with the historically unsupportable checks switched off.

    Returns a copy. Mutating the caller's limits would silently weaken the live
    agent if the two ever shared an object.
    """
    return limits.model_copy(update={"min_open_interest": 0})


def implied_vol(
    price: float, spot: float, strike: float, tau: float, right: str
) -> float | None:
    """Back out the volatility that reproduces an observed option price.

    Returns None when no volatility in `VOL_BOUNDS` can produce the price —
    typically a stale bar printed below intrinsic value, which is not a
    contract anyone could have sold at that number.
    """
    if tau <= 0 or price <= 0:
        return None

    def difference(vol: float) -> float:
        return bs_price(spot, strike, tau, vol, right) - price

    low, high = VOL_BOUNDS
    if difference(low) > 0 or difference(high) < 0:
        return None
    try:
        return float(brentq(difference, low, high, xtol=1e-6))
    except (ValueError, RuntimeError):
        return None


class BarPricer:
    """A tradable chain reconstructed from daily option bars.

    `bars_for` maps an expiry to a frame indexed by (symbol, timestamp), which
    is exactly what `load_option_bars` returns. Passing the loader in rather
    than calling it keeps the engine offline and the tests honest.
    """

    def __init__(
        self,
        bars_for: Callable[[date], pd.DataFrame],
        haircut_pct: float = DEFAULT_HAIRCUT_PCT,
    ) -> None:
        self._bars_for = bars_for
        self.haircut_pct = haircut_pct
        self._cache: dict[date, dict[date, list[dict]]] = {}

    def _by_day(self, expiry: date) -> dict[date, list[dict]]:
        if expiry in self._cache:
            return self._cache[expiry]

        frame = self._bars_for(expiry)
        indexed: dict[date, list[dict]] = {}
        if frame is not None and not frame.empty:
            for (symbol, stamp), bar in frame.iterrows():
                occ = parse_occ(str(symbol))
                if occ is None or occ["expiry"] != expiry:
                    continue
                day = (
                    pd.Timestamp(stamp).tz_localize(None).date()
                    if pd.Timestamp(stamp).tzinfo
                    else pd.Timestamp(stamp).date()
                )
                indexed.setdefault(day, []).append(
                    {
                        "occ_symbol": str(symbol),
                        "right": occ["right"],
                        "strike": occ["strike"],
                        "expiry": expiry,
                        "close": Decimal(str(round(float(bar["close"]), 4))),
                        "volume": int(bar["volume"]),
                        "as_of": day,
                    }
                )
        self._cache[expiry] = indexed
        return indexed

    def close_of(self, occ_symbol: str, expiry: date, as_of: date) -> Decimal | None:
        """The contract's own printed close, for marking an open position."""
        for row in self._by_day(expiry).get(as_of, []):
            if row["occ_symbol"] == occ_symbol:
                return row["close"]
        return None

    def rows(self, expiry: date, as_of: date, right: Right | None = None) -> list[dict]:
        """Chain rows for one day, in the shape `build_candidates` consumes.

        A contract that printed no volume is omitted: it may have been quoted,
        but nothing says it could have been sold.
        """
        haircut = Decimal(str(self.haircut_pct)) / 100
        out = []
        for row in self._by_day(expiry).get(as_of, []):
            if row["volume"] <= 0:
                continue
            if right is not None and row["right"] != right:
                continue
            close = row["close"]
            out.append(
                {
                    **row,
                    "bid": close * (1 - haircut),
                    "ask": close * (1 + haircut),
                }
            )
        return out


class CycleRecord(BaseModel):
    """One position, from the decision that opened it to how it ended."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    entry_date: date
    symbol: str
    right: Right
    occ_symbol: str
    strike: Decimal
    expiry: date
    contracts: int  # positive: the count sold
    premium: Decimal  # per share, the constructed bid
    mid: Decimal  # per share, the bar close — recorded so the haircut is visible
    proceeds: Decimal
    delta: float
    vega: float
    assignment_prob: float
    spread_pct: float
    equity_before: Decimal
    cash_before: Decimal
    wheel_before: WheelState
    outcome: str = "open"  # open | expired | assigned
    close_at_expiry: Decimal | None = None


class BacktestResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    equity_curve: pd.Series
    cycles: list[CycleRecord]
    skipped: list[str]
    params: dict


def _entry_days(expiry: date, days: list[date], dte: dict) -> date | None:
    """The day the position is opened: the earliest inside the DTE band.

    Earliest, not latest, because a wheel is paid for time. Within a band that
    the strategy has already declared acceptable, more of it is better.
    """
    eligible = [d for d in days if dte["min"] <= (expiry - d).days <= dte["max"]]
    return max(eligible, key=lambda d: (expiry - d).days) if eligible else None


def _order_from(candidate: Candidate, contracts: int, symbol: str) -> ProposedOrder:
    return ProposedOrder(
        symbol=symbol,
        right=candidate.right,
        strike=candidate.strike,
        expiry=candidate.expiry,
        contracts=-contracts,  # negative: sell to open
        limit_price=candidate.bid,
        delta=candidate.delta,
        vega=candidate.vega,
        assignment_prob=candidate.assignment_prob,
        open_interest=0,  # not available historically; the check is disabled
        spread_pct=candidate.spread_pct,
    )


def run_backtest(
    symbol: str,
    bars: pd.DataFrame,
    pricer: BarPricer,
    expiries: list[date],
    limits: Limits,
    strategy: dict,
    initial_capital: Decimal = Decimal("1000000"),
    regime: str = "calm",
    cvar_pct: float = 2.0,
) -> BacktestResult:
    """Walk the window one trading day at a time and record every decision.

    `regime` is fixed rather than inferred. Regime detection is the LLM's job
    in the live agent and does not exist yet; hard-coding it here keeps the
    backtest measuring the mechanical strategy, which is the part being tested.
    """
    days = [pd.Timestamp(stamp).date() for stamp in bars.index]
    closes = bars["close"]
    entry_for = {}
    for expiry in expiries:
        day = _entry_days(expiry, days, strategy["dte"])
        if day is not None:
            entry_for[day] = expiry

    gate_limits = backtest_limits(limits)
    delta_band = strategy["target_delta"][regime]
    target_delta = (delta_band["min"], delta_band["max"])
    multiplier = strategy["size_multiplier"][regime]

    wheel = WheelState(symbol=symbol)
    cash = initial_capital
    shares = 0
    peak_equity = initial_capital
    cycles: list[CycleRecord] = []
    skipped: list[str] = []
    equity_by_day: list[float] = []

    for position, today in enumerate(days):
        cursor = Cursor(today)
        spot = float(closes.iloc[position])

        # 1. Resolve anything expiring today, by the close that actually printed.
        if wheel.contracts and wheel.contracts[0].expiry == today:
            contract = wheel.contracts[0]
            in_the_money = (
                spot < float(contract.strike)
                if contract.right == "P"
                else spot > float(contract.strike)
            )
            quantity = abs(contract.contracts)
            record = next(c for c in cycles if c.occ_symbol == contract.occ_symbol)
            record.close_at_expiry = Decimal(str(round(spot, 4)))
            if in_the_money and contract.right == "P":
                cash -= contract.strike * quantity * SHARES_PER_CONTRACT
                shares += quantity * SHARES_PER_CONTRACT
                wheel = on_put_assigned(wheel)
                record.outcome = "assigned"
            elif in_the_money:
                cash += contract.strike * quantity * SHARES_PER_CONTRACT
                shares -= quantity * SHARES_PER_CONTRACT
                wheel = on_call_assigned(wheel)
                record.outcome = "assigned"
            else:
                wheel = on_expired_worthless(wheel)
                record.outcome = "expired"

        # 2. Open a position, if today is an entry day and the wheel is resting.
        expiry = entry_for.get(today)
        if expiry is not None and next_action(wheel) != "HOLD":
            action = next_action(wheel)
            right: Right = "P" if action == "SELL_PUT" else "C"
            cursor.check(today)
            rows = pricer.rows(expiry, today, right)
            priced = []
            below_basis = 0
            for row in rows:
                tau = (expiry - today).days / 365.0
                vol = implied_vol(
                    float(row["close"]), spot, float(row["strike"]), tau, right
                )
                if vol is None:
                    continue
                # Never write a call below what the shares cost. This is the
                # wheel's whole discipline: the premium is not worth locking in
                # a loss on the stock. Recorded rather than silently dropped —
                # a cycle that traded nothing because every call was underwater
                # is a decision, and it should read as one.
                if (
                    right == "C"
                    and wheel.basis is not None
                    and row["strike"] < wheel.basis
                ):
                    below_basis += 1
                    continue
                priced.append({**row, "implied_vol": vol, "open_interest": 0})

            if below_basis:
                skipped.append(
                    f"{today}: {below_basis} calls below the {wheel.basis} share "
                    f"basis were not offered; writing one locks in a loss"
                )

            # An entry day that priced nothing at all is a data problem wearing
            # a strategy's clothes. Without this note the run reports zero
            # trades and zero refusals, which reads as a cautious strategy
            # rather than as a chain that was never fetched for this day. That
            # is exactly how a DTE band pointing outside the cached window went
            # undiagnosed: silence looked like discipline.
            if not rows:
                skipped.append(
                    f"{today}: no option bars at all for the {expiry} expiry "
                    f"{(expiry - today).days} days out — the chain was not "
                    f"fetched this far from expiry, so nothing could be priced"
                )
            elif not priced:
                skipped.append(
                    f"{today}: {len(rows)} contracts had bars but none could be "
                    f"priced for the {expiry} expiry"
                )

            history = closes.iloc[: position + 1]
            candidates = build_candidates(
                chain_rows=priced,
                spot=spot,
                symbol=symbol,
                right=right,
                as_of=today,
                limits=gate_limits,
                returns=return_scenarios(history),
                target_delta=target_delta,
            )

            equity = cash + Decimal(str(shares * spot))
            deployed = (
                wheel.contracts[0].notional if wheel.leg == "PUT_OPEN" else Decimal("0")
            )
            portfolio = Portfolio(
                equity=equity,
                cash=cash,
                peak_equity=peak_equity,
                deployed=deployed,
                net_delta=float(shares),
                wheels={symbol: wheel},
            )
            budget = (
                equity
                * Decimal(str(limits.max_deployed_pct / 100))
                * Decimal(str(multiplier))
            )
            allocations = optimize(
                candidates=candidates,
                portfolio=portfolio,
                limits=gate_limits,
                capital_budget=budget,
                cvar_limit=float(equity) * cvar_pct / 100,
            )
            if not allocations and candidates:
                skipped.append(f"{today}: optimizer allocated nothing")

            for allocation in allocations:
                candidate = allocation.candidate
                order = _order_from(candidate, allocation.contracts, symbol)
                verdict = veto(order, portfolio, gate_limits)
                if not verdict.approved:
                    skipped.append(
                        f"{today}: {candidate.occ_symbol} — {verdict.reason}"
                    )
                    continue
                contract = OpenContract(
                    occ_symbol=candidate.occ_symbol,
                    right=candidate.right,
                    strike=candidate.strike,
                    expiry=candidate.expiry,
                    contracts=-allocation.contracts,
                    premium=candidate.bid,
                )
                proceeds = candidate.bid * allocation.contracts * SHARES_PER_CONTRACT
                cycles.append(
                    CycleRecord(
                        entry_date=today,
                        symbol=symbol,
                        right=candidate.right,
                        occ_symbol=candidate.occ_symbol,
                        strike=candidate.strike,
                        expiry=candidate.expiry,
                        contracts=allocation.contracts,
                        premium=candidate.bid,
                        mid=candidate.mid,
                        proceeds=proceeds,
                        delta=candidate.delta,
                        vega=candidate.vega,
                        assignment_prob=candidate.assignment_prob,
                        spread_pct=candidate.spread_pct,
                        equity_before=equity,
                        cash_before=cash,
                        wheel_before=wheel,
                    )
                )
                wheel = (
                    on_sold_put(wheel, contract)
                    if right == "P"
                    else on_sold_call(wheel, contract)
                )
                cash += proceeds
                break  # one contract open per wheel; the state machine allows no more

        # 3. Mark to market. The open short is a liability, valued at its own
        #    printed close where there is one and at intrinsic where there is not.
        liability = Decimal("0")
        if wheel.contracts:
            contract = wheel.contracts[0]
            printed = pricer.close_of(contract.occ_symbol, contract.expiry, today)
            if printed is None:
                intrinsic = (
                    max(float(contract.strike) - spot, 0.0)
                    if contract.right == "P"
                    else max(spot - float(contract.strike), 0.0)
                )
                printed = Decimal(str(round(intrinsic, 4)))
            liability = printed * abs(contract.contracts) * SHARES_PER_CONTRACT

        equity = cash + Decimal(str(shares * spot)) - liability
        peak_equity = max(peak_equity, equity)
        equity_by_day.append(float(equity))

    curve = pd.Series(
        equity_by_day, index=pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    )
    return BacktestResult(
        equity_curve=curve,
        cycles=cycles,
        skipped=skipped,
        params={
            "symbol": symbol,
            "regime": regime,
            "initial_capital": str(initial_capital),
            "haircut_pct": pricer.haircut_pct,
            "priced_from": "daily bars, not quotes",
            "disabled_checks": list(DISABLED_CHECKS),
            "cvar_pct": cvar_pct,
            "expiries": [e.isoformat() for e in expiries],
        },
    )
