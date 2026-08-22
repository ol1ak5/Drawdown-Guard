"""Real historical option bars, addressed by constructed OCC symbols."""

from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import pandas as pd
from alpaca.data.historical import OptionHistoricalDataClient
from alpaca.data.requests import OptionBarsRequest
from alpaca.data.timeframe import TimeFrame
from pandas.tseries.holiday import GoodFriday, USFederalHolidayCalendar

from flywheel.settings import get_settings

# Alpaca's option history begins here. Requesting earlier returns nothing.
OPTION_HISTORY_START = date(2024, 2, 1)


def occ_symbol(underlying: str, expiry: date, right: str, strike: float) -> str:
    """Build an OCC option symbol, e.g. SPY240419P00480000.

    Strike is encoded in thousandths of a dollar, zero-padded to eight digits.
    """
    return (
        underlying.upper()
        + expiry.strftime("%y%m%d")
        + right.upper()
        + f"{round(strike * 1000):08d}"
    )


def third_friday(year: int, month: int) -> date:
    """The standard monthly option expiry."""
    first = date(year, month, 1)
    offset = (4 - first.weekday()) % 7  # Monday is 0, Friday is 4
    return first + timedelta(days=offset + 14)


@lru_cache(maxsize=1)
def _market_closures() -> frozenset[date]:
    """Days the exchange is shut that can land on a third Friday.

    Federal holidays plus Good Friday, which the NYSE observes and the federal
    calendar does not. Only those two kinds can ever fall on a third Friday:
    every other federal holiday is a Monday, or falls before the 15th.
    """
    start, end = "1990-01-01", "2035-12-31"
    federal = USFederalHolidayCalendar().holidays(start=start, end=end)
    good_friday = GoodFriday.dates(start, end)
    return frozenset(stamp.date() for stamp in (*federal, *good_friday))


def monthly_expiry(year: int, month: int) -> date:
    """The tradable monthly expiry: the third Friday, or the day before it.

    When the exchange is closed on the third Friday the contracts expire on the
    Thursday instead. Ignoring that does not fail loudly — it builds OCC symbols
    for contracts that never existed, which return no rows and look exactly like
    a month with no data. Two of every 31 expiries were being lost this way.
    """
    friday = third_friday(year, month)
    return friday - timedelta(days=1) if friday in _market_closures() else friday


def monthly_expiries(start: date, end: date) -> list[date]:
    expiries = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        candidate = monthly_expiry(year, month)
        if start <= candidate <= end:
            expiries.append(candidate)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return expiries


def strike_grid(spot: float, width_pct: float = 0.25, step: float = 1.0) -> list[float]:
    """Strikes bracketing the spot, on the exchange's listing increment."""
    low = round((spot * (1 - width_pct)) / step) * step
    high = round((spot * (1 + width_pct)) / step) * step
    count = int(round((high - low) / step)) + 1
    return [round(low + i * step, 2) for i in range(count)]


def load_option_bars(
    underlying: str,
    expiry: date,
    strikes: list[float],
    start: date,
    end: date,
    cache_dir: str | Path = "data",
) -> pd.DataFrame:
    """Daily bars for every put and call on the given strikes. Parquet-cached.

    Returns an empty frame rather than raising when nothing existed: a strike
    that was never listed is a normal outcome of generating candidates.

    The cache key is the underlying and the expiry only, not the strike grid.
    Widening `strikes` for an expiry already on disk will therefore serve the
    narrower cached result. Delete the file to refetch.
    """
    cache = Path(cache_dir) / f"opt_{underlying}_{expiry}.parquet"
    if cache.exists():
        return pd.read_parquet(cache)

    symbols = [
        occ_symbol(underlying, expiry, right, strike)
        for strike in strikes
        for right in ("P", "C")
    ]
    settings = get_settings()
    client = OptionHistoricalDataClient(
        settings.alpaca_api_key, settings.alpaca_secret_key
    )
    frames = []
    for batch_start in range(0, len(symbols), 100):  # keep URLs under the limit
        batch = symbols[batch_start : batch_start + 100]
        request = OptionBarsRequest(
            symbol_or_symbols=batch,
            timeframe=TimeFrame.Day,
            start=max(start, OPTION_HISTORY_START),
            end=end,
        )
        frame = client.get_option_bars(request).df
        if not frame.empty:
            frames.append(frame)

    result = pd.concat(frames) if frames else pd.DataFrame()
    cache.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(cache)
    return result
