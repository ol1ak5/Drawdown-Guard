# Alpaca data API — verified surface

Everything here was run against the live `dev` paper account on 2026-08-22 with
`alpaca-py` 0.44.0, not read from documentation. Re-verify if the SDK is bumped.

## Imports

Both historical clients are exported from the package root. The plan's imports
were correct as written:

```python
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, OptionBarsRequest, OptionChainRequest
from alpaca.data.timeframe import TimeFrame
```

## Request fields

| Request | Fields |
|---|---|
| `OptionBarsRequest` | `symbol_or_symbols`, `timeframe`, `start`, `end`, `limit`, `sort`, `currency` |
| `StockBarsRequest` | the same, plus `adjustment`, `asof`, `feed` |

`OptionHistoricalDataClient.get_option_bars` and
`StockHistoricalDataClient.get_stock_bars` both exist and both return an object
whose `.df` is a `MultiIndex[symbol, timestamp]` DataFrame with columns
`open, high, low, close, volume, trade_count, vwap`.

## Entitlement, confirmed

Historical option bars are available on the default feed. No Algo Trader Plus
subscription was needed. This closes the open question in the plan's Task 8.

## OCC symbol construction, confirmed against real data

`occ_symbol("SPY", date(2024, 4, 19), "P", 480.0)` produces `SPY240419P00480000`,
and that symbol returns 34 daily bars between 2024-03-01 and expiry. The series
decays from 1.44 to 0.02 — a put that expired worthless, which is one full
successful leg of this wheel visible in the raw data.

This check matters more than it looks. A wrong strike encoding does not raise;
it silently requests contracts that never existed and returns nothing, and a
backtest built on that reports a clean history of trades that never happened.

## Monthly expiry is not always the third Friday

The first full download returned **zero option bars for exactly two expiries,
and the same two for all three tickers**: 2025-04-18 and 2026-06-19. Identical
gaps across three unrelated underlyings is not missing data — it is a wrong
question being asked three times.

Both dates are market closures. 2025-04-18 was Good Friday, which the NYSE
observes but the federal holiday calendar does not list. 2026-06-19 was
Juneteenth. When the third Friday is a closure, contracts expire on the
**Thursday** instead.

`monthly_expiry()` now steps back a day for those cases; `third_friday()` is
still there and still means what it says. Refetching the six affected expiries
recovered 33,304 option bars — about 6% of every monthly cycle in the backtest,
silently absent.

This is the failure mode the plan warned about, and it is worth restating: a
wrong OCC symbol does not raise. It returns an empty frame, which is
indistinguishable from a month the market did not trade. The only reason it was
caught is that the download printed a per-expiry row count and two of them were
zero in a suspiciously regular pattern.

## Option chain snapshots

`get_option_chain(OptionChainRequest(...))` returns a dict keyed by OCC symbol.
Each snapshot carries `latest_quote`, `implied_volatility`, and `greeks`
(`delta, gamma, rho, theta, vega`). 1,749 SPY puts came back for expiries 3 to
16 days out.

**Alpaca's greeks are quoted per share per one point of implied volatility.**
Ours are per contract per point — see the vega section of `handoff.md`. Measured
across 85 live quotes, `contract_vega` matched Alpaca's vega times 100 to within
0.5%.

## CBOE benchmark history — the plan overstated this

The plan says `^PUT` runs from June 1986 and calls it "40 years", and the report
outline claims the series covers the 1987 crash. Yahoo returns:

| Ticker | First close | Last close | Rows |
|---|---|---|---|
| `^PUT` | 1996-08-02 | 2026-08-21 | 7,554 |
| `^BXM` | 1988-06-01 | 2026-07-17 | 9,603 |

So the honest claims are **30 years** for `^PUT` and **38** for `^BXM`, and
**neither series covers October 1987**. They do cover 2000, 2008 and 2020, which
is enough for the argument being made — but the report must not say 1987.

`^BXM` is also about five weeks stale on Yahoo. Check the last date before
plotting; if the gap widens, say so in the caption rather than letting a chart
imply the series simply ended.
