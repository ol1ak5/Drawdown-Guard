"""CBOE strategy benchmark indices — the published version of this strategy."""

from pathlib import Path

import pandas as pd
import yfinance as yf

# ^PUT  - CBOE S&P 500 PutWrite Index, the put-selling half of the wheel
# ^BXM  - CBOE S&P 500 BuyWrite Index, the covered-call half
BENCHMARKS = ("^PUT", "^BXM")


def load_benchmark(ticker: str, cache_dir: str | Path = "data/benchmarks") -> pd.Series:
    """Daily closes for a CBOE strategy index, cached as committed CSV.

    The CSV is committed rather than gitignored: it is small, it never changes
    retroactively, and a judge re-running the report must get our numbers
    without needing network access or a Yahoo session.
    """
    cache = Path(cache_dir) / f"{ticker.lstrip('^')}.csv"
    if cache.exists():
        frame = pd.read_csv(cache, index_col=0, parse_dates=True)
        return frame["close"]

    frame = yf.download(ticker, start="1986-01-01", auto_adjust=False, progress=False)
    series = frame["Close"].squeeze().rename("close").dropna()
    cache.parent.mkdir(parents=True, exist_ok=True)
    series.to_frame().to_csv(cache)
    return series
