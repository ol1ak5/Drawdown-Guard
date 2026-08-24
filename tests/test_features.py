"""Market features: the IV record, the rank, and the portfolio greek signs.

No network. `build_snapshot` needs a live chain and is exercised by the
eyeball check in scripts/, not here; what is worth pinning in a unit test is
the arithmetic that is easy to get backwards and impossible to notice.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from flywheel.domain import OpenContract, WheelState
from flywheel.market import features
from flywheel.market.client import position_greeks
from flywheel.market.features import MIN_OBSERVATIONS, iv_rank, record_iv

TODAY = date(2026, 8, 24)


@pytest.fixture(autouse=True)
def isolated_history(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "IV_HISTORY_DIR", tmp_path)


def seed(symbol: str, values: list[float], end: date = TODAY) -> None:
    for offset, value in enumerate(values):
        record_iv(symbol, end - timedelta(days=len(values) - offset), value)


def test_the_rank_is_unknown_before_there_is_history():
    """Absent, not middling. A caller must not read None as fifty."""
    assert iv_rank("SPY", 0.20, TODAY) is None


def test_the_rank_stays_unknown_below_the_observation_floor():
    seed("SPY", [0.15] * (MIN_OBSERVATIONS - 1))
    assert iv_rank("SPY", 0.20, TODAY) is None


def test_a_high_reading_ranks_near_the_top():
    seed("SPY", [0.10 + 0.001 * i for i in range(MIN_OBSERVATIONS + 20)])
    assert iv_rank("SPY", 0.99, TODAY) == 100.0


def test_a_low_reading_ranks_at_the_bottom():
    seed("SPY", [0.10 + 0.001 * i for i in range(MIN_OBSERVATIONS + 20)])
    assert iv_rank("SPY", 0.01, TODAY) == 0.0


def test_rerunning_on_the_same_day_does_not_weight_that_day_twice():
    """An agent restarted three times in a morning is still one observation."""
    for _ in range(3):
        record_iv("SPY", TODAY, 0.30)
    seed("SPY", [0.10] * MIN_OBSERVATIONS)
    rank = iv_rank("SPY", 0.20, TODAY)
    # One high reading among MIN_OBSERVATIONS low ones. Counted three times it
    # would drag the rank measurably below this.
    assert rank == pytest.approx(100.0 * MIN_OBSERVATIONS / (MIN_OBSERVATIONS + 1))


def test_observations_outside_the_trailing_year_are_ignored():
    seed("SPY", [0.10] * MIN_OBSERVATIONS, end=TODAY - timedelta(days=500))
    assert iv_rank("SPY", 0.20, TODAY) is None


# --- portfolio greeks ------------------------------------------------------


def wheel_with_short_puts(contracts: int, strike: str = "700") -> WheelState:
    return WheelState(
        symbol="SPY",
        leg="PUT_OPEN",
        contracts=[
            OpenContract(
                occ_symbol="SPY260918P00700000",
                right="P",
                strike=Decimal(strike),
                expiry=date(2026, 9, 18),
                contracts=contracts,
                premium=Decimal("5"),
            )
        ],
    )


def test_short_puts_are_long_the_underlying():
    """The sign that makes the wheel a bullish strategy.

    Selling four puts at −0.30 delta is +120 share equivalents, not −120. Get
    this backwards and the delta limit binds hardest exactly when the position
    is most neutral.
    """
    wheels = {"SPY": wheel_with_short_puts(-4)}
    net_delta, _value, _ = position_greeks(
        wheels,
        {"SPY": 764.0},
        {"SPY260918P00700000": 0.20},
        {"SPY260918P00700000": 0.07},
    )
    assert net_delta > 0


def test_shares_count_one_for_one():
    wheels = {"SPY": WheelState(symbol="SPY", leg="SHARES", shares=400)}
    net_delta, _value, vega = position_greeks(wheels, {"SPY": 764.0}, {}, {})
    assert net_delta == 400.0
    assert vega == 0.0


def test_writing_options_costs_money_when_volatility_rises():
    """Portfolio vega is dollars *lost* per point, so a short book is positive."""
    wheels = {"SPY": wheel_with_short_puts(-4)}
    _, _value, vega = position_greeks(
        wheels,
        {"SPY": 764.0},
        {"SPY260918P00700000": 0.20},
        {"SPY260918P00700000": 0.07},
    )
    assert vega > 0


def test_a_contract_with_no_implied_volatility_is_skipped_not_zeroed():
    """A missing input must not silently read as a position with no risk."""
    wheels = {"SPY": wheel_with_short_puts(-4)}
    net_delta, _value, vega = position_greeks(wheels, {"SPY": 764.0}, {}, {})
    assert net_delta == 0.0
    assert vega == 0.0


def test_four_hundred_shares_against_the_delta_band_is_the_recorded_deadlock():
    """The finding that stalls the wheel, pinned as an executable fact.

    An assignment of four SPY puts leaves 400 shares, and `max_net_delta` is
    150. Until that band is recalibrated the gate cannot approve the covered
    call that would bring it back — see docs/notes/handoff.md.
    """
    wheels = {"SPY": WheelState(symbol="SPY", leg="SHARES", shares=400)}
    net_delta, _value, _ = position_greeks(wheels, {"SPY": 764.0}, {}, {})
    assert abs(net_delta) > 150.0
