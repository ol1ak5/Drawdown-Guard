"""Why the wheel stopped earning, and whether anything could have restarted it.

Research, not a feature. The wheel in this project spent most of a 2.5-year
backtest holding assigned shares and collecting no option income at all, and
before building a mechanism to prevent that, the mechanism has to be shown to
exist on the data rather than invented to fit the story.

FOUR QUESTIONS
--------------
1. When did the wheel stop completing cycles, and for how long?
2. What was the best call premium available *above the share basis* while it
   was stalled? A stall with $4 on the table is a different problem from a
   stall with $0.40.
3. What did the capital earn while stalled? (Treasury yield only.)
4. The one that matters: would exiting at a loss and restarting the wheel at
   the lower price have beaten sitting still?

Question 4 is the real tension and it is not invented. The wheel's discipline
is never to write a call below the share basis, because that locks in a loss on
the stock. But selling the shares outright *is the same loss*, taken a
different way. So the question is not whether exiting is allowed; it is when
sitting costs more than leaving.

If the answer is "never, on this data", that is a finding and there is no
feature. A rule that only works because it was designed after seeing the
outcome is worse than no rule.
"""

import argparse
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yaml

from drawdownguard.backtest.engine import BarPricer, implied_vol, run_backtest
from drawdownguard.backtest.options_history import monthly_expiries
from drawdownguard.risk.limits import load_limits

CACHE = Path("data")


def bars_for(symbol: str) -> pd.DataFrame:
    matches = sorted(CACHE.glob(f"{symbol}_*.parquet"))
    if not matches:
        raise SystemExit(f"no cached bars for {symbol}")
    return pd.read_parquet(matches[-1])


def chain_loader(symbol: str):
    def load(expiry):
        path = CACHE / f"opt_{symbol}_{expiry}.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    return load


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", default="2024-02-01")
    parser.add_argument("--end", default="2026-08-21")
    args = parser.parse_args()

    start = pd.Timestamp(args.start).date()
    end = pd.Timestamp(args.end).date()
    strategy = yaml.safe_load(Path("config/strategy.yaml").read_text())
    limits = load_limits()

    bars = bars_for(args.symbol)
    window = bars[
        (bars.index >= pd.Timestamp(start)) & (bars.index <= pd.Timestamp(end))
    ]
    expiries = [
        e
        for e in monthly_expiries(start, end)
        if (CACHE / f"opt_{args.symbol}_{e}.parquet").exists()
    ]
    pricer = BarPricer(chain_loader(args.symbol))

    result = run_backtest(
        symbol=args.symbol,
        bars=window,
        pricer=pricer,
        expiries=expiries,
        limits=limits,
        strategy=strategy,
        initial_capital=Decimal("1000000"),
    )

    print(f"{args.symbol}  {start} to {end}")
    print(f"  expiries available : {len(expiries)}")
    print(f"  cycles opened      : {len(result.cycles)}")
    assigned = [c for c in result.cycles if c.outcome == "assigned"]
    print(f"  assignments        : {len(assigned)}")

    # --- 1. when did it stop earning -------------------------------------
    traded_on = sorted({c.entry_date for c in result.cycles})
    print("\n1. WHEN IT EARNED")
    if not traded_on:
        print("   never opened a position at all")
    else:
        print(f"   first cycle {traded_on[0]}, last cycle {traded_on[-1]}")
        gaps = [
            (a, b, (b - a).days) for a, b in zip(traded_on, traded_on[1:], strict=False)
        ]
        worst = max(gaps, key=lambda g: g[2]) if gaps else None
        if worst:
            print(
                f"   longest gap between cycles: {worst[2]} days "
                f"({worst[0]} to {worst[1]})"
            )
        idle_tail = (end - traded_on[-1]).days
        print(f"   idle since the last cycle: {idle_tail} days")

    # --- 2/3. what was on the table while stalled ------------------------
    print("\n2. WHAT A COVERED CALL WOULD HAVE PAID, ABOVE BASIS")
    refusals = [s for s in result.skipped if "basis" in s]
    print(f"   expiries where every call sat below the share basis: {len(refusals)}")
    for note in refusals[:6]:
        print(f"     {note}")

    print("\n3. WHAT THE CAPITAL DID INSTEAD")
    curve = result.equity_curve
    print(f"   equity {float(curve.iloc[0]):,.0f} -> {float(curve.iloc[-1]):,.0f}")
    premium = sum(float(c.proceeds) for c in result.cycles)
    total_gain = float(curve.iloc[-1] - curve.iloc[0])
    print(f"   option premium collected : {premium:,.0f}")
    print(f"   total equity change      : {total_gain:,.0f}")
    share = 100 * premium / total_gain if total_gain else 0.0
    print(f"   share of the gain that was option income: {share:.1f}%")

    # --- 4. would exiting have beaten sitting -----------------------------
    print("\n4. WOULD EXITING AT A LOSS AND RESTARTING HAVE BEATEN SITTING?")
    if not assigned:
        print("   no assignment in this run, so the question does not arise here")
        return

    closes = window["close"]
    for cycle in assigned:
        entry = cycle.expiry
        after = closes[closes.index > pd.Timestamp(entry)]
        if after.empty:
            continue
        basis = float(cycle.strike) - float(cycle.premium)
        spot_then = float(after.iloc[0])
        recovered = after[after >= basis]
        days_to_recover = (
            (recovered.index[0].date() - entry).days if not recovered.empty else None
        )
        print(
            f"   assigned {entry} at strike {float(cycle.strike):,.2f}, "
            f"basis {basis:,.2f}, spot after {spot_then:,.2f} "
            f"({(spot_then / basis - 1) * 100:+.1f}%)"
        )
        print(
            "     time for the price to return to basis: "
            + (
                f"{days_to_recover} days"
                if days_to_recover is not None
                else "never within the window"
            )
        )
        # What a fresh put cycle would have earned on the freed capital, priced
        # from the same chain the agent could actually see.
        rows = pricer.rows(
            min((e for e in expiries if e > entry), default=entry), entry, "P"
        )
        best = 0.0
        for row in rows:
            tau = (row["expiry"] - entry).days / 365.0
            vol = implied_vol(
                float(row["close"]), spot_then, float(row["strike"]), tau, "P"
            )
            if vol is None:
                continue
            if 0.25 <= abs(float(row["strike"]) / spot_then - 1) * 10 <= 0.35:
                best = max(best, float(row["close"]))
        if best:
            print(
                f"     a fresh put after exiting would have paid about "
                f"{best * 100:,.0f} per contract"
            )


if __name__ == "__main__":
    main()
