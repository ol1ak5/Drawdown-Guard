"""The backtest report: the evidence, in order of how much it should be trusted.

The published CBOE indices come first. They are the record of this strategy
class through 2000, 2008 and 2020, they were not produced by this project, and
they are therefore the least suspect thing in the document. Our own numbers come
after, and are read against them.

That ordering is the point. A report that opens with its own headline figure is
asking to be believed before it has earned it.

WHAT THIS REPORT REFUSES TO DO
-------------------------------
Report a return without saying how much of it is premium and how much is the
Treasury bill the collateral sat in. A cash-secured put ties up cash; in a real
account that cash earns. Quoting only the total claims credit for the yield,
and quoting only the premium describes a strategy nobody would run. Both
columns, always.

Report a Sharpe ratio without the sample size in the same sentence. Thirty
cycles is not enough to estimate a Sharpe, and a number quoted without its n
invites exactly the reading it cannot support.

Bury the losses. The worst cycle and the maximum drawdown are the first things
an options-literate reader looks for, and a document that puts them in a
footnote reads as a pitch rather than as evidence.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS = 252

# Above this, a wheel result is more likely a bug than a discovery. `^PUT`
# itself runs a Sharpe well under 1 over most decades; anything far above the
# published index for the same strategy is a defect until proven otherwise —
# most likely lookahead, mid-pricing, or a sign error in the loss scenarios.
SUSPICIOUS_SHARPE = 3.0
SUSPICIOUS_DRAWDOWN_PCT = 5.0


@dataclass
class Stats:
    total_return_pct: float
    annualised_pct: float
    sharpe: float
    max_drawdown_pct: float
    days: int

    @property
    def years(self) -> float:
        return self.days / TRADING_DAYS


def stats_from_curve(curve: pd.Series) -> Stats:
    """Return, Sharpe and worst drawdown from an equity curve.

    Sharpe is computed on daily returns and annualised, with no risk-free
    subtraction. That is stated rather than hidden: for a strategy whose return
    is substantially the risk-free rate itself, subtracting it would be the
    more meaningful figure, and not subtracting it flatters this result. The
    premium-only column is the one to read if that matters.
    """
    values = curve.astype(float)
    returns = values.pct_change().dropna()
    total = float(values.iloc[-1] / values.iloc[0] - 1) * 100
    days = len(values)
    years = max(days / TRADING_DAYS, 1e-9)
    annualised = ((1 + total / 100) ** (1 / years) - 1) * 100
    volatility = float(returns.std())
    sharpe = (
        float(returns.mean() / volatility * np.sqrt(TRADING_DAYS))
        if volatility > 0
        else 0.0
    )
    peak = values.cummax()
    drawdown = float(((peak - values) / peak).max() * 100)
    return Stats(total, annualised, sharpe, drawdown, days)


def load_benchmark(path: Path, start: date, end: date) -> pd.Series:
    frame = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")["close"]
    return frame[
        (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
    ]


def cycle_table(cycles: list) -> dict:
    """What the cycles did, including how the worst one went."""
    if not cycles:
        return {"count": 0}
    proceeds = [float(c.proceeds) for c in cycles]
    assigned = sum(1 for c in cycles if c.outcome == "assigned")
    return {
        "count": len(cycles),
        "assigned": assigned,
        "assigned_pct": 100.0 * assigned / len(cycles),
        "median_premium": float(np.median(proceeds)),
        "total_premium": float(sum(proceeds)),
        "worst": min(proceeds),
        "best": max(proceeds),
    }


def _fig(path: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    path.parent.mkdir(parents=True, exist_ok=True)
    return plt


def plot_benchmarks(put: pd.Series, bxm: pd.Series, out: Path) -> Path:
    """The long record, with drawdowns, because that is the honest half."""
    plt = _fig(out)
    fig, (top, bottom) = plt.subplots(
        2, 1, figsize=(10, 7), sharex=True, height_ratios=[2, 1]
    )
    for series, label in ((put, "CBOE PUT"), (bxm, "CBOE BXM")):
        normalised = series / series.iloc[0] * 100
        top.plot(normalised.index, normalised.values, label=label, linewidth=1)
        peak = series.cummax()
        drawdown = (series - peak) / peak * 100
        bottom.plot(drawdown.index, drawdown.values, linewidth=1, label=label)
    top.set_yscale("log")
    top.set_ylabel("growth of 100, log scale")
    top.legend()
    top.set_title("The published record of this strategy class")
    bottom.set_ylabel("drawdown, %")
    bottom.axhline(0, color="black", linewidth=0.5)
    bottom.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def plot_equity(curves: dict[str, pd.Series], out: Path, title: str) -> Path:
    plt = _fig(out)
    fig, ax = plt.subplots(figsize=(10, 5))
    for label, curve in curves.items():
        normalised = curve.astype(float) / float(curve.iloc[0]) * 100
        ax.plot(normalised.index, normalised.values, label=label, linewidth=1.2)
    ax.set_ylabel("growth of 100")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return out


def _suspicion(stats: Stats, deployed_note: str) -> str:
    """Say so in the report when a number looks too good.

    A reader should not have to work out for themselves that a drawdown this
    small is a consequence of low deployment rather than of skill.
    """
    notes = []
    if stats.sharpe > SUSPICIOUS_SHARPE:
        notes.append(
            f"A Sharpe of {stats.sharpe:.2f} is above what the published index "
            f"achieves over most decades. Treat it as a defect to be found, not "
            f"as an edge."
        )
    if 0 < stats.max_drawdown_pct < SUSPICIOUS_DRAWDOWN_PCT:
        notes.append(
            f"A maximum drawdown of {stats.max_drawdown_pct:.2f}% is small for "
            f"a wheel. {deployed_note}"
        )
    return " ".join(notes)


def build_report(
    results: dict,
    premium_only: dict,
    underlying: dict[str, pd.Series],
    out_dir: Path | str = "docs",
    benchmarks: Path | str = "data/benchmarks",
) -> Path:
    """Write the report and its figures. Returns the markdown path."""
    out_dir = Path(out_dir)
    img = out_dir / "img"
    bench = Path(benchmarks)

    any_curve = next(iter(results.values())).equity_curve
    start = any_curve.index[0].date()
    end = any_curve.index[-1].date()

    put_full = load_benchmark(bench / "PUT.csv", date(1900, 1, 1), date(2100, 1, 1))
    bxm_full = load_benchmark(bench / "BXM.csv", date(1900, 1, 1), date(2100, 1, 1))
    plot_benchmarks(put_full, bxm_full, img / "benchmarks.png")

    put_window = load_benchmark(bench / "PUT.csv", start, end)

    curves = {s: r.equity_curve for s, r in results.items()}
    curves["CBOE PUT"] = put_window
    for symbol, series in underlying.items():
        window = series[
            (series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))
        ]
        curves[f"{symbol} buy and hold"] = window
    plot_equity(curves, img / "equity.png", f"{start} to {end}")

    put_stats = stats_from_curve(put_window)
    put_all = stats_from_curve(put_full)
    bxm_all = stats_from_curve(bxm_full)

    lines = [
        "# Flywheel: backtest report",
        "",
        f"Window {start} to {end}. Capital 1,000,000. Universe SPY, QQQ, IWM.",
        "",
        "Read the layers in order. The published indices come first because "
        "they were not produced by this project and are the least suspect "
        "evidence in the document. Our own numbers are read against them.",
        "",
        "## 1. The published record, which we did not produce",
        "",
        "![benchmarks](img/benchmarks.png)",
        "",
        "CBOE's `PUT` index writes cash-secured S&P 500 puts monthly; `BXM` "
        "writes covered calls. They are the same strategy class as this agent, "
        "with a public record across the 2000, 2008 and 2020 drawdowns.",
        "",
        f"- `PUT`, {put_full.index[0].date()} to {put_full.index[-1].date()}: "
        f"{put_all.annualised_pct:.2f}% a year, worst drawdown "
        f"**{put_all.max_drawdown_pct:.1f}%**, Sharpe {put_all.sharpe:.2f}.",
        f"- `BXM`, {bxm_full.index[0].date()} to {bxm_full.index[-1].date()}: "
        f"{bxm_all.annualised_pct:.2f}% a year, worst drawdown "
        f"**{bxm_all.max_drawdown_pct:.1f}%**, Sharpe {bxm_all.sharpe:.2f}.",
        "",
        "Those drawdowns are the honest half of this strategy class. Writing "
        "puts is short a crash, and both indices took roughly a third off in "
        "2008. Any result below that does not show a comparable loss has "
        "either avoided the regime or has a bug.",
        "",
        "## 2. Our engine over the same window",
        "",
        "![equity](img/equity.png)",
        "",
        f"`PUT` over {start} to {end}: {put_stats.total_return_pct:.2f}% total, "
        f"worst drawdown {put_stats.max_drawdown_pct:.2f}%.",
        "",
        "## 3. Results",
        "",
        "Two columns, always. A cash-secured put ties up cash, and in a real "
        "account that cash earns Treasury yield. Reporting only the total "
        "claims credit for the yield; reporting only the premium describes a "
        "strategy nobody would run.",
        "",
        "**These are two runs, not one run split two ways.** Turning the "
        "collateral yield off changes the decisions, not only the "
        "accounting: a smaller balance means a smaller capital budget, "
        "smaller positions, fewer assignments, and so a different number of "
        "cycles. On SPY the premium-only configuration opened eight cycles "
        "and the funded one opened three. The left column is therefore not "
        "the premium component of the right column, and subtracting one "
        "from the other does not isolate anything. Read each as its own "
        "result.",
        "",
        "| symbol | premium only | with collateral | ann. | worst DD |"
        " Sharpe | cycles |",
        "|---|---|---|---|---|---|---|",
    ]

    for symbol, result in sorted(results.items()):
        full = stats_from_curve(result.equity_curve)
        bare = stats_from_curve(premium_only[symbol].equity_curve)
        counts = cycle_table(result.cycles)
        lines.append(
            f"| {symbol} | {bare.total_return_pct:.2f}% | "
            f"{full.total_return_pct:.2f}% | {full.annualised_pct:.2f}% | "
            f"**{full.max_drawdown_pct:.2f}%** | {full.sharpe:.2f} | "
            f"{counts['count']} |"
        )

    lines += ["", "### The losses, stated first", ""]
    for symbol, result in sorted(results.items()):
        counts = cycle_table(result.cycles)
        if not counts["count"]:
            lines.append(f"- **{symbol}**: no cycles opened.")
            continue
        full = stats_from_curve(result.equity_curve)
        lines.append(
            f"- **{symbol}**: worst cycle {counts['worst']:,.0f}, "
            f"{counts['assigned']} of {counts['count']} cycles assigned "
            f"({counts['assigned_pct']:.0f}%), maximum drawdown "
            f"{full.max_drawdown_pct:.2f}%."
        )

    lines += ["", "### Where these numbers should not be believed", ""]
    for symbol, result in sorted(results.items()):
        full = stats_from_curve(result.equity_curve)
        note = _suspicion(
            full,
            "It follows from low deployment rather than from skill: the wheel "
            "held a position on a minority of days and sat in cash otherwise, "
            "so there was little exposure to draw down.",
        )
        if note:
            lines.append(f"- **{symbol}**: {note}")

    total_cycles = sum(len(r.cycles) for r in results.values())
    params = next(iter(results.values())).params
    lines += [
        "",
        f"- The sample is **{total_cycles} cycles across three symbols**. A "
        f"Sharpe ratio estimated from that many observations is a description "
        f"of this window, not a property of the strategy. It is quoted above "
        f"only because omitting it would look like concealment.",
        "",
        "## 4. What is modelled rather than measured",
        "",
        "Alpaca's option history returns daily bars, not quotes. Four "
        "quantities are therefore modelled, and each is carried in "
        "`BacktestResult.params` rather than left in a footnote:",
        "",
        f"- **Execution price**: bar close less a {params['haircut_pct']}% "
        f"haircut, booked as the fill and never as the mid. Mid-pricing is the "
        f"most common way a wheel backtest invents returns nobody could have "
        f"captured.",
        "- **Implied volatility**: back-solved from that close, so the delta "
        "band and the vega budget come from the price actually printed.",
        f"- **Collateral yield**: a flat {params['cash_rate']:.2%} compounded "
        f"per trading day. The honest version is the daily bill series, which "
        f"is not cached.",
        f"- **Open interest**: does not exist historically at all. The check is "
        f"disabled explicitly — `{params['disabled_checks']}` — and replaced by "
        f"a measured filter: the contract must have traded that day.",
        "",
        "## 5. Limitations",
        "",
        f"1. **Sample size.** {total_cycles} cycles over {put_stats.years:.1f} "
        f"years. Not enough to distinguish skill from window.",
        "2. **No spreads in the modelled path.** The haircut stands in for a "
        "spread that would really have been crossed; it is a constant, and "
        "real spreads widen exactly when it hurts most.",
        "3. **Assignment is resolved European-style at expiry**, by the actual "
        "underlying close. Early exercise is ignored, which flatters the "
        "result: American options on dividend-paying ETFs are exercised early "
        "around ex-dividend dates, and the wheel would have been assigned "
        "sooner and more often than shown.",
        "4. **The window contains no crash.** February 2024 to August 2026 has "
        "no 2008 and no March 2020. The published indices in section 1 are the "
        "only evidence here about how this strategy behaves in one.",
        "",
        "## 6. Reproducing this",
        "",
        "```bash",
        "uv run python3 scripts/run_backtest.py --symbol SPY",
        "uv run python3 scripts/run_backtest.py --symbol SPY --cash-rate 0",
        "uv run python3 scripts/build_report.py",
        "```",
        "",
        "The backtest imports the live optimizer and the live risk gate rather "
        "than reimplementing them. A backtest running different code from the "
        "agent measures a strategy nobody is going to trade.",
        "",
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "backtest-report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
