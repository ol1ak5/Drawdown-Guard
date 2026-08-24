"""Run the backtest over cached history and print what it found.

Reads only what is already on disk. `scripts/fetch_history.py` is what talks
to Alpaca; keeping the two apart means a backtest can be re-run offline, on a
plane, or by a judge who has no keys.
"""

import argparse
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import yaml

from flywheel.backtest.engine import BarPricer, run_backtest
from flywheel.backtest.options_history import monthly_expiries
from flywheel.risk.limits import load_limits

CACHE = Path("data")


def _cached_bars(symbol: str) -> pd.DataFrame:
    """The underlying bars already fetched, whatever window they cover."""
    matches = sorted(CACHE.glob(f"{symbol}_*.parquet"))
    if not matches:
        raise SystemExit(
            f"no cached bars for {symbol}; run scripts/fetch_history.py first"
        )
    return pd.read_parquet(matches[-1])


def _chain_loader(symbol: str):
    """Serve an expiry's option bars from the parquet cache only.

    `load_option_bars` would happily fetch a missing expiry from Alpaca. That
    is the wrong behaviour in a backtest: a silent network call mid-run makes
    the result depend on when it was executed.
    """

    def bars_for(expiry: date) -> pd.DataFrame:
        path = CACHE / f"opt_{symbol}_{expiry}.parquet"
        return pd.read_parquet(path) if path.exists() else pd.DataFrame()

    return bars_for


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--start", default="2024-02-01")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--capital", default="1000000")
    parser.add_argument("--haircut-pct", type=float, default=2.0)
    parser.add_argument(
        "--cash-rate",
        type=float,
        default=None,
        help="annual yield on idle collateral; 0 to see premium alone",
    )
    parser.add_argument("--out", default=None, help="write the result as JSON")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.strptime(args.end, "%Y-%m-%d").date()

    bars = _cached_bars(args.symbol)
    bars = bars[(bars.index >= pd.Timestamp(start)) & (bars.index <= pd.Timestamp(end))]
    if bars.empty:
        raise SystemExit(f"no cached bars for {args.symbol} between {start} and {end}")

    expiries = [
        expiry
        for expiry in monthly_expiries(start, end)
        if (CACHE / f"opt_{args.symbol}_{expiry}.parquet").exists()
    ]
    if not expiries:
        raise SystemExit(f"no cached option chains for {args.symbol} in that window")

    strategy = yaml.safe_load(Path("config/strategy.yaml").read_text())
    result = run_backtest(
        symbol=args.symbol,
        bars=bars,
        pricer=BarPricer(_chain_loader(args.symbol), haircut_pct=args.haircut_pct),
        expiries=expiries,
        limits=load_limits(),
        strategy=strategy,
        initial_capital=Decimal(args.capital),
        **({} if args.cash_rate is None else {"cash_rate": args.cash_rate}),
    )

    curve = result.equity_curve
    start_equity, end_equity = curve.iloc[0], curve.iloc[-1]
    peak = curve.cummax()
    worst_drawdown = float(((peak - curve) / peak).max() * 100)
    assigned = sum(1 for cycle in result.cycles if cycle.outcome == "assigned")

    print(f"{args.symbol}  {start} to {end}")
    print(f"  expiries with cached chains : {len(expiries)}")
    print(f"  positions opened            : {len(result.cycles)}")
    print(f"  assigned                    : {assigned}")
    print(f"  declined                    : {len(result.skipped)}")
    print(f"  equity                      : {start_equity:,.0f} -> {end_equity:,.0f}")
    print(
        f"  return                      : {(end_equity / start_equity - 1) * 100:.2f}%"
    )
    print(f"  worst drawdown              : {worst_drawdown:.2f}%")
    print(f"  priced from                 : {result.params['priced_from']}")
    print(f"  execution haircut           : {result.params['haircut_pct']}%")
    print(f"  cash rate on collateral     : {result.params['cash_rate']:.3%}")
    print(f"  checks disabled             : {result.params['disabled_checks']}")

    if result.skipped:
        print("\n  first refusals:")
        for note in result.skipped[:5]:
            print(f"    {note}")

    if args.out:
        payload = {
            "params": result.params,
            "equity_curve": {
                stamp.date().isoformat(): value for stamp, value in curve.items()
            },
            "cycles": [json.loads(cycle.model_dump_json()) for cycle in result.cycles],
            "skipped": result.skipped,
        }
        Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
        print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
