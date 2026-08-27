"""Download and cache the daily bars the agent reads volatility from.

`market/features.py` measures realised volatility over 20 and 60 days, and it
reads the cached parquet rather than the network so a cycle is not one API
outage away from having no risk estimate at all.

This used to fetch three things: stock bars, option bars, and the CBOE
benchmark series. The last two existed for a backtest of the options wheel and
went with it -- what remains is the only history the live agent consults.
"""

from datetime import date

from drawdownguard.market.history import load_bars

UNIVERSE = ["SPY", "QQQ", "IWM"]
START = date(2019, 1, 1)
END = date(2026, 8, 21)

if __name__ == "__main__":
    for symbol in UNIVERSE:
        bars = load_bars(symbol, START, END)
        print(f"{symbol}: {len(bars)} daily bars, {bars.index[0]} to {bars.index[-1]}")
