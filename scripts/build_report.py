"""Generate the backtest report: run each symbol twice, write markdown and figures.

Twice on purpose. The premium-only run is not decoration: it is the only way a
reader can separate what the strategy earned from what the Treasury bill it sat
next to earned.
"""

import argparse
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yaml

from flywheel.backtest.engine import BarPricer, run_backtest
from flywheel.backtest.options_history import monthly_expiries
from flywheel.backtest.report import build_report
from flywheel.risk.limits import load_limits

CACHE = Path("data")
SYMBOLS = ("SPY", "QQQ", "IWM")


def bars_for_symbol(symbol: str) -> pd.DataFrame:
    matches = sorted(CACHE.glob(f"{symbol}_*.parquet"))
    if not matches:
        raise SystemExit(f"no cached bars for {symbol}; run scripts/fetch_history.py")
    return pd.read_parquet(matches[-1])


def chain_loader(symbol: str):
    def bars_for(expiry):
        path = CACHE / f"opt_{symbol}_{expiry}.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    return bars_for


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
    limits = load_limits()

    results, premium_only, underlying = {}, {}, {}
    for symbol in SYMBOLS:
        bars = bars_for_symbol(symbol)
        window = bars[
            (bars.index >= pd.Timestamp(start)) & (bars.index <= pd.Timestamp(end))
        ]
        expiries = [
            e
            for e in monthly_expiries(start, end)
            if (CACHE / f"opt_{symbol}_{e}.parquet").exists()
        ]
        common = dict(
            symbol=symbol,
            bars=window,
            expiries=expiries,
            limits=limits,
            strategy=strategy,
            initial_capital=capital,
        )
        print(f"{symbol}: {len(expiries)} expiries", flush=True)
        results[symbol] = run_backtest(pricer=BarPricer(chain_loader(symbol)), **common)
        premium_only[symbol] = run_backtest(
            pricer=BarPricer(chain_loader(symbol)), cash_rate=0.0, **common
        )
        underlying[symbol] = window["close"]

    path = build_report(results, premium_only, underlying)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
