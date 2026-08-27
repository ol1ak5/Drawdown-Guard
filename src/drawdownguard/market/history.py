"""Historical bars with a parquet cache, plus the statistics derived from them."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from drawdownguard.settings import get_settings

TRADING_DAYS = 252


def fetch_bars(symbol: str, start: date, end: date) -> pd.DataFrame:
    settings = get_settings()
    client = StockHistoricalDataClient(
        settings.alpaca_api_key, settings.alpaca_secret_key
    )
    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
    )
    frame = client.get_stock_bars(request).df
    if isinstance(frame.index, pd.MultiIndex):
        frame = frame.xs(symbol, level="symbol")
    frame.index = pd.to_datetime(frame.index).tz_localize(None)
    return frame[["open", "high", "low", "close", "volume"]]


def load_bars(
    symbol: str, start: date, end: date, cache_dir: str | Path = "data"
) -> pd.DataFrame:
    """Fetch once, then serve from parquet. The cache is gitignored."""
    cache = Path(cache_dir) / f"{symbol}_{start}_{end}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    frame = fetch_bars(symbol, start, end)
    cache.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(cache)
    return frame


def realized_vol(closes: pd.Series, window: int = 20) -> pd.Series:
    """Annualised realised volatility from daily log returns."""
    log_returns = np.log(closes / closes.shift(1))
    return log_returns.rolling(window).std() * np.sqrt(TRADING_DAYS)


def return_scenarios(closes: pd.Series, lookback: int = 500) -> np.ndarray:
    """Daily log returns, most recent first-bounded window.

    These are the empirical scenarios the CVaR constraint is built on. Using
    realised history rather than a lognormal assumption is deliberate: the
    tail we care about is the one the market actually produced.
    """
    log_returns = np.log(closes / closes.shift(1)).dropna()
    return log_returns.tail(lookback).to_numpy()
