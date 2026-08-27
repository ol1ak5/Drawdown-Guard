"""Does buying a floor pay for itself on our own history?

The hypothesis is that spending part of the premium to cap the loss improves
income per unit of worst case. It is a hypothesis and not a slogan, so it gets
measured before anything is built around it.

For every expiry with a cached chain: find the put the strategy would have
sold, find the floor it could have afforded, and compare the two positions on
what actually happened to the underlying by expiry.

If protection never pays on this data, that is the finding and the feature does
not ship. A rule that survives only because nobody checked is worse than none.
"""

import argparse
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yaml

from drawdownguard.backtest.engine import BarPricer, implied_vol
from drawdownguard.backtest.options_history import monthly_expiries
from drawdownguard.optimizer.payoff import bs_delta
from drawdownguard.optimizer.payoff_shape import (
    MAX_PROTECTION_COST_PCT,
    choose_floor,
)

CACHE = Path("data")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--budget", type=float, default=MAX_PROTECTION_COST_PCT)
    args = parser.parse_args()

    start, end = pd.Timestamp("2024-02-01").date(), pd.Timestamp("2026-08-21").date()
    strategy = yaml.safe_load(Path("config/strategy.yaml").read_text())
    dte = strategy["dte"]
    band = strategy["target_delta"]["calm"]

    bars = pd.read_parquet(sorted(CACHE.glob(f"{args.symbol}_*.parquet"))[-1])
    # Keyed by date rather than looked up with a reconstructed Timestamp: the
    # index carries a time component, so `closes[pd.Timestamp(day)]` misses.
    close_on = {pd.Timestamp(i).date(): float(v) for i, v in bars["close"].items()}
    days = sorted(close_on)

    def loader(expiry):
        path = CACHE / f"opt_{args.symbol}_{expiry}.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    pricer = BarPricer(loader)
    expiries = [
        e
        for e in monthly_expiries(start, end)
        if (CACHE / f"opt_{args.symbol}_{e}.parquet").exists()
    ]

    bare_total = prot_total = 0.0
    bare_worst = prot_worst = 0.0
    protected_count = declined_count = 0
    print(f"{args.symbol}, floor budget {args.budget:.0f}% of premium\n")
    print(
        f"{'expiry':<12}{'short':>8}{'floor':>8}{'cost%':>7}"
        f"{'bare P/L':>11}{'prot P/L':>11}"
    )
    print("-" * 58)

    for expiry in expiries:
        entry = next(
            (d for d in days if dte["min"] <= (expiry - d).days <= dte["max"]), None
        )
        if entry is None:
            continue
        spot = close_on[entry]
        rows = pricer.rows(expiry, entry, "P")
        if not rows:
            continue

        priced = []
        for row in rows:
            tau = (expiry - entry).days / 365.0
            vol = implied_vol(float(row["close"]), spot, float(row["strike"]), tau, "P")
            if vol is None:
                continue
            d = bs_delta(spot, float(row["strike"]), tau, vol, "P")
            priced.append({**row, "delta": d, "ask": row["close"]})

        sellable = [r for r in priced if band["min"] <= abs(r["delta"]) <= band["max"]]
        if not sellable:
            continue
        short = max(sellable, key=lambda r: r["strike"])
        premium = Decimal(str(short["close"]))

        floor = choose_floor(short["strike"], premium, priced, spot, args.budget)
        later = [d for d in days if d > expiry]
        final = close_on[later[0]] if later else close_on[days[-1]]

        k_short = float(short["strike"])
        bare = float(premium) - max(k_short - final, 0.0)
        if floor is None:
            declined_count += 1
            prot = bare
            cost_pct = 0.0
            floor_k = "-"
            worst_prot = -1e9
        else:
            protected_count += 1
            cost = Decimal(str(floor["close"]))
            k_long = float(floor["strike"])
            net = float(premium - cost)
            prot = net - max(k_short - final, 0.0) + max(k_long - final, 0.0)
            cost_pct = float(cost / premium * 100)
            floor_k = f"{k_long:.0f}"
            worst_prot = -(k_short - k_long - net)

        bare_total += bare * 100
        prot_total += prot * 100
        bare_worst = min(bare_worst, -(k_short - float(premium)) * 100)
        prot_worst = min(prot_worst, worst_prot * 100)
        print(
            f"{str(expiry):<12}{k_short:>8.0f}{floor_k:>8}{cost_pct:>7.0f}"
            f"{bare * 100:>11,.0f}{prot * 100:>11,.0f}"
        )

    print("-" * 58)
    print(
        f"\nfloors bought: {protected_count},  declined as too dear: {declined_count}"
    )
    print(
        f"total P/L per contract   bare {bare_total:>12,.0f}   "
        f"protected {prot_total:>12,.0f}"
    )
    print(
        f"worst single outcome     bare {bare_worst:>12,.0f}   "
        f"protected {prot_worst:>12,.0f}"
    )
    if prot_worst < 0 and bare_worst < 0:
        print("\nincome per unit of worst case")
        print(f"  bare      {bare_total / abs(bare_worst):>8.2f}")
        print(f"  protected {prot_total / abs(prot_worst):>8.2f}")
        print("\nHigher is better. If protected is not higher, the floor did not")
        print("pay for itself on this data and the feature should not ship.")


if __name__ == "__main__":
    main()
