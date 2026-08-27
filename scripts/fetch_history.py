"""Download and cache every historical input the backtest needs."""

from datetime import date, timedelta

import pandas as pd

from drawdownguard.backtest.benchmarks import BENCHMARKS, load_benchmark
from drawdownguard.backtest.data import load_bars
from drawdownguard.backtest.options_history import (
    OPTION_HISTORY_START,
    load_option_bars,
    monthly_expiries,
    strike_grid,
)

UNIVERSE = ["SPY", "QQQ", "IWM"]
START = date(2019, 1, 1)
END = date(2026, 8, 21)

if __name__ == "__main__":
    for ticker in BENCHMARKS:
        series = load_benchmark(ticker)
        print(
            f"{ticker}: {len(series)} closes, {series.index[0].date()} to "
            f"{series.index[-1].date()}"
        )

    for symbol in UNIVERSE:
        bars = load_bars(symbol, START, END)
        print(f"{symbol}: {len(bars)} daily bars, {bars.index[0]} to {bars.index[-1]}")

        for expiry in monthly_expiries(OPTION_HISTORY_START, END):
            # Plain date arithmetic, then an explicit cast for the slice. Mixing
            # pd.Timedelta into date maths returns a date, not a Timestamp, and
            # slicing a DatetimeIndex with a date is deprecated in pandas 3.
            entry = expiry - timedelta(days=35)
            window = bars.loc[: pd.Timestamp(entry)]
            if window.empty:
                continue
            spot = float(window["close"].iloc[-1])
            frame = load_option_bars(symbol, expiry, strike_grid(spot), entry, expiry)
            print(f"  {symbol} {expiry}: {len(frame)} option bars")
