from datetime import date
from decimal import Decimal

import numpy as np

from flywheel.optimizer.candidates import build_candidates
from flywheel.risk.limits import Limits

LIMITS = Limits(
    max_position_pct=25.0,
    max_deployed_pct=60.0,
    max_drawdown_pct=15.0,
    max_net_delta_pct=50.0,
    max_vega=500.0,
    max_assignment_prob=0.35,
    min_open_interest=500,
    max_spread_pct=5.0,
    forbid_naked=True,
)
RETURNS = np.random.default_rng(0).normal(0, 0.01, 500)


def row(strike, bid=1.00, ask=1.04, oi=5000, iv=0.18):
    """A liquid quote by default.

    The default spread is deliberately inside `max_spread_pct`: (1.04-1.00)/1.02
    is 3.9%. A wider default would be filtered on liquidity before any other
    check could run, which would make the delta and expiry tests pass for the
    wrong reason.
    """
    return {
        "occ_symbol": f"SPY260904P00{int(strike)}000",
        "strike": Decimal(str(strike)),
        "expiry": date(2026, 9, 4),
        "bid": Decimal(str(bid)),
        "ask": Decimal(str(ask)),
        "open_interest": oi,
        "implied_vol": iv,
    }


def build(rows, **kwargs):
    params = {
        "spot": 100.0,
        "symbol": "SPY",
        "right": "P",
        "as_of": date(2026, 8, 24),
        "limits": LIMITS,
        "returns": RETURNS,
        "target_delta": (0.10, 0.45),
    }
    params.update(kwargs)
    return build_candidates(rows, **params)


def test_illiquid_strikes_are_dropped():
    assert build([row(95, oi=10)]) == []


def test_wide_spreads_are_dropped():
    assert build([row(95, bid=Decimal("1.00"), ask=Decimal("2.00"))]) == []


def test_strikes_outside_the_target_delta_band_are_dropped():
    # a strike far below spot has delta near zero
    assert build([row(50)]) == []


def test_a_liquid_strike_in_band_survives_and_is_priced():
    candidates = build([row(97)])
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.mid == Decimal("1.02")
    assert candidate.delta < 0
    assert 0.0 < candidate.assignment_prob < 1.0
    assert candidate.collateral == Decimal("9700")
    assert candidate.losses.shape == RETURNS.shape


def test_expired_rows_are_dropped():
    stale = row(97)
    stale["expiry"] = date(2026, 8, 1)  # before as_of
    assert build([stale]) == []


def test_zero_bid_rows_are_dropped():
    assert build([row(97, bid=Decimal("0"))]) == []
