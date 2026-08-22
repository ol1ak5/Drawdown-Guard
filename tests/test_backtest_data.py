import numpy as np
import pandas as pd
import pytest

from flywheel.backtest.data import realized_vol, return_scenarios


def closes(values):
    index = pd.date_range("2026-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=index, name="close")


def test_a_flat_series_has_zero_realized_volatility():
    result = realized_vol(closes([100.0] * 40), window=20)
    assert result.dropna().iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_realized_volatility_is_annualised():
    rng = np.random.default_rng(3)
    daily = rng.normal(0, 0.01, 600)  # 1% daily -> ~15.9% annualised
    series = closes(list(100 * np.exp(np.cumsum(daily))))
    result = realized_vol(series, window=250).dropna().iloc[-1]
    assert result == pytest.approx(0.159, abs=0.03)


def test_return_scenarios_are_daily_log_returns_of_the_requested_length():
    series = closes(list(np.linspace(100, 120, 800)))
    scenarios = return_scenarios(series, lookback=500)
    assert scenarios.shape == (500,)
    assert np.all(np.abs(scenarios) < 0.5)


def test_return_scenarios_take_the_most_recent_window():
    series = closes(list(np.linspace(100, 120, 100)))
    assert return_scenarios(series, lookback=500).shape == (99,)
