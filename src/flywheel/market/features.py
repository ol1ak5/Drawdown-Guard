"""What the agent knows about a market before it looks at any single contract.

IV RANK, AND WHY IT IS ALLOWED TO BE UNKNOWN
--------------------------------------------
IV rank is where the current at-the-money implied volatility sits against its
own trailing year. It needs a year of implied volatility, and the live API
serves only today's. So it is accumulated: every snapshot appends one
observation to a local record, and the rank is computed from that.

Which means that on the first run there is no history, and the honest answer is
**not a number**. `iv_rank` is `None` until `MIN_OBSERVATIONS` have been
collected, and every consumer has to handle `None`.

The alternative — seeding the rank from realised volatility — was rejected on
purpose. The entire variance-risk-premium argument is the gap between implied
and realised. Substituting one for the other collapses that gap to zero and
returns a number that looks perfectly reasonable, which is worse than
returning nothing at all. A missing input announces itself; a plausible wrong
one does not.
"""

import csv
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict

from flywheel.backtest.data import load_bars, realized_vol, return_scenarios
from flywheel.market.chain import load_chain
from flywheel.market.client import get_spot

# A year of trading days. Below this the rank is reported as unknown rather
# than computed from a window too short to have seen a regime change.
MIN_OBSERVATIONS = 60
IV_WINDOW_DAYS = 365
IV_HISTORY_DIR = Path("data/iv_history")

# How much history the realised-volatility windows need. 60-day volatility over
# a 60-day window would be a single observation, so the fetch reaches back far
# enough for the rolling window to have somewhere to roll.
BARS_LOOKBACK_DAYS = 800


class MarketSnapshot(BaseModel):
    """Everything regime classification and scenario building need."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    symbol: str
    spot: float
    realized_vol_20d: float
    realized_vol_60d: float
    atm_iv: float | None = None
    iv_rank: float | None = None
    returns: np.ndarray

    @property
    def variance_risk_premium(self) -> float | None:
        """Implied minus realised. Positive is what makes writing options pay.

        None when today's implied volatility could not be observed, rather than
        zero — zero is a claim about the market, absent is a claim about the
        data.
        """
        if self.atm_iv is None:
            return None
        return self.atm_iv - self.realized_vol_20d


def _history_path(symbol: str) -> Path:
    return IV_HISTORY_DIR / f"{symbol}.csv"


def record_iv(symbol: str, as_of: date, atm_iv: float) -> None:
    """Append one at-the-money observation, idempotently for the day.

    Re-running on the same date overwrites rather than appends: an agent
    restarted three times in a morning must not weight that morning three times
    in its own percentile.
    """
    path = _history_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: dict[str, str] = {}
    if path.exists():
        with path.open() as handle:
            rows = {r[0]: r[1] for r in csv.reader(handle) if len(r) == 2}
    rows[as_of.isoformat()] = f"{atm_iv:.6f}"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        for day in sorted(rows):
            writer.writerow([day, rows[day]])


def iv_rank(symbol: str, atm_iv: float, as_of: date | None = None) -> float | None:
    """Percentile of today's implied volatility within its trailing year.

    None when too few observations exist to say anything. The caller must not
    read that as a middling rank.
    """
    as_of = as_of or date.today()
    path = _history_path(symbol)
    if not path.exists():
        return None

    cutoff = as_of - timedelta(days=IV_WINDOW_DAYS)
    values = []
    with path.open() as handle:
        for row in csv.reader(handle):
            if len(row) != 2:
                continue
            day = date.fromisoformat(row[0])
            if cutoff <= day <= as_of:
                values.append(float(row[1]))

    if len(values) < MIN_OBSERVATIONS:
        return None
    below = sum(1 for v in values if v < atm_iv)
    return 100.0 * below / len(values)


def _atm_iv(rows: list[dict], spot: float) -> float | None:
    """Implied volatility of the contract closest to the money.

    Nearest strike rather than an interpolated surface: this feeds a regime
    label, not a price, and a label does not repay the complexity of a fit.
    """
    if not rows:
        return None
    nearest = min(rows, key=lambda r: abs(float(r["strike"]) - spot))
    return float(nearest["implied_vol"])


def dte_band(path: str | Path = "config/strategy.yaml") -> tuple[int, int]:
    """The entry window, read from the same file the backtest reads.

    Not a default argument. A hardcoded 5 and 14 sitting here while
    `config/strategy.yaml` says 30 and 45 is a divergence nothing would report:
    the live agent and the backtest would quietly trade different strategies
    and both would look correct.
    """
    band = yaml.safe_load(Path(path).read_text())["dte"]
    return int(band["min"]), int(band["max"])


async def build_snapshot(
    symbol: str,
    min_dte: int | None = None,
    max_dte: int | None = None,
    as_of: date | None = None,
    record: bool = True,
) -> MarketSnapshot:
    """Spot, realised volatility, today's implied volatility, and its rank."""
    as_of = as_of or date.today()
    if min_dte is None or max_dte is None:
        configured_min, configured_max = dte_band()
        min_dte = configured_min if min_dte is None else min_dte
        max_dte = configured_max if max_dte is None else max_dte

    closes = load_bars(symbol, as_of - timedelta(days=BARS_LOOKBACK_DAYS), as_of)[
        "close"
    ]

    spot = await get_spot(symbol)
    rows = await load_chain(symbol, "P", min_dte, max_dte, as_of)
    atm = _atm_iv(rows, spot)

    if atm is not None and record:
        record_iv(symbol, as_of, atm)

    return MarketSnapshot(
        symbol=symbol,
        spot=spot,
        realized_vol_20d=float(realized_vol(closes, 20).iloc[-1]),
        realized_vol_60d=float(realized_vol(closes, 60).iloc[-1]),
        atm_iv=atm,
        iv_rank=iv_rank(symbol, atm, as_of) if atm is not None else None,
        returns=return_scenarios(closes),
    )
