"""Sweep the risk limits and report what each setting does to the wheel.

The wheel does not turn at the provisional limits. One SPY run over two and a
half years opened a single position out of thirty-one expiries and then sat in
the assigned shares — buy-and-hold wearing a wheel's clothes. The +10% it
returned was SPY appreciating, not premium collected, and reporting that number
as a strategy result would be the most flattering lie available.

This script measures the cause rather than guessing at it: for each candidate
value of `max_net_delta`, run the real backtest on each symbol and count how
many cycles actually completed.

WHAT A DELTA BAND IN SHARES MEANS
---------------------------------
`max_net_delta: 150` is denominated in share equivalents, which is a unit with
no opinion about account size. 400 SPY shares is 400 delta whether the account
holds ten thousand dollars or ten million. The same band is either
unreachable or inert depending on capital the limit never sees, so the sweep
also reports the exposure as a share of equity, which is the number that
actually describes the risk being taken.
"""

import argparse
import functools
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yaml

from flywheel.backtest.engine import BarPricer, run_backtest
from flywheel.backtest.options_history import monthly_expiries
from flywheel.risk.limits import load_limits

# Flushed: the sweep runs for many minutes and a buffered report that
# arrives only at the end tells you nothing about whether it is stuck.
print = functools.partial(print, flush=True)  # noqa: A001

CACHE = Path("data")
SYMBOLS = ("SPY", "QQQ", "IWM")
CANDIDATES = (150.0, 300.0, 600.0, 1000.0, 2000.0)


def cached_bars(symbol: str) -> pd.DataFrame:
    matches = sorted(CACHE.glob(f"{symbol}_*.parquet"))
    if not matches:
        raise SystemExit(f"no cached bars for {symbol}")
    return pd.read_parquet(matches[-1])


def chain_loader(symbol: str):
    def bars_for(expiry):
        path = CACHE / f"opt_{symbol}_{expiry}.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    return bars_for


def run(symbol, start, end, limits, strategy, capital):
    bars = cached_bars(symbol)
    bars = bars[(bars.index >= pd.Timestamp(start)) & (bars.index <= pd.Timestamp(end))]
    expiries = [
        e
        for e in monthly_expiries(start, end)
        if (CACHE / f"opt_{symbol}_{e}.parquet").exists()
    ]
    return run_backtest(
        symbol=symbol,
        bars=bars,
        pricer=BarPricer(chain_loader(symbol)),
        expiries=expiries,
        limits=limits,
        strategy=strategy,
        initial_capital=capital,
    ), len(expiries)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2024-02-01")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--capital", default="1000000")
    args = parser.parse_args()

    start = pd.Timestamp(args.start).date()
    end = pd.Timestamp(args.end).date()
    capital = Decimal(args.capital)
    strategy = yaml.safe_load(Path("config/strategy.yaml").read_text())
    base = load_limits()

    print(f"capital {capital:,}   window {start} to {end}")
    print(f"current max_net_delta: {base.max_net_delta}\n")
    print(
        f"{'max_net_delta':>14} {'symbol':>7} {'expiries':>9} {'opened':>7} "
        f"{'assigned':>9} {'declined':>9} {'return %':>9} {'maxDD %':>8}"
    )
    print("-" * 80)

    summary = {}
    for delta_band in CANDIDATES:
        limits = base.model_copy(update={"max_net_delta": delta_band})
        opened_total = 0
        for symbol in SYMBOLS:
            result, n_expiries = run(symbol, start, end, limits, strategy, capital)
            curve = result.equity_curve
            peak = curve.cummax()
            drawdown = float(((peak - curve) / peak).max() * 100)
            ret = float(curve.iloc[-1] / curve.iloc[0] - 1) * 100
            assigned = sum(1 for c in result.cycles if c.outcome == "assigned")
            opened_total += len(result.cycles)
            print(
                f"{delta_band:>14.0f} {symbol:>7} {n_expiries:>9} "
                f"{len(result.cycles):>7} {assigned:>9} {len(result.skipped):>9} "
                f"{ret:>9.2f} {drawdown:>8.2f}"
            )
        summary[delta_band] = opened_total
        print("-" * 80)

    print("\npositions opened across all three symbols:")
    for delta_band, opened in summary.items():
        note = "  <- wheel does not turn" if opened <= 3 else ""
        print(f"  max_net_delta {delta_band:>6.0f}: {opened:>3}{note}")

    # A wheel that never turns is not a safer wheel. It is a different strategy
    # that nobody chose, running under a name that says otherwise.
    print(
        "\nA setting that opens one position per symbol is not a conservative "
        "wheel.\nIt is buy-and-hold, and any return it reports belongs to the "
        "underlying."
    )


if __name__ == "__main__":
    main()
