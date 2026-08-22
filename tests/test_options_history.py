from datetime import date

import pytest

from flywheel.backtest.options_history import (
    monthly_expiries,
    monthly_expiry,
    occ_symbol,
    strike_grid,
    third_friday,
)


def test_occ_symbol_encodes_a_whole_dollar_strike():
    assert occ_symbol("SPY", date(2024, 4, 19), "P", 480.0) == "SPY240419P00480000"


def test_occ_symbol_encodes_a_half_dollar_strike():
    assert occ_symbol("SPY", date(2024, 4, 19), "C", 512.5) == "SPY240419C00512500"


def test_occ_symbol_uppercases_the_right():
    assert occ_symbol("qqq", date(2025, 1, 17), "p", 400.0) == "QQQ250117P00400000"


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2024, 4, date(2024, 4, 19)),
        (2024, 3, date(2024, 3, 15)),
        (2025, 8, date(2025, 8, 15)),
        (2026, 5, date(2026, 5, 15)),
    ],
)
def test_third_friday(year, month, expected):
    assert third_friday(year, month) == expected


def test_an_ordinary_expiry_is_the_third_friday():
    assert monthly_expiry(2024, 4) == date(2024, 4, 19)


def test_a_good_friday_expiry_moves_back_to_thursday():
    """2025-04-18 was Good Friday. The exchange is shut; expiry is the 17th.

    Found by downloading: every symbol returned zero bars for this expiry and
    for Juneteenth 2026, and for no others. Identical gaps across three
    unrelated tickers is a bug in the symbol we asked for, not missing data.
    """
    assert monthly_expiry(2025, 4) == date(2025, 4, 17)


def test_a_juneteenth_expiry_moves_back_to_thursday():
    assert monthly_expiry(2026, 6) == date(2026, 6, 18)


def test_monthly_expiries_use_the_adjusted_date():
    assert monthly_expiries(date(2025, 4, 1), date(2025, 4, 30)) == [date(2025, 4, 17)]


def test_monthly_expiries_are_ordered_and_bounded():
    result = monthly_expiries(date(2024, 2, 1), date(2024, 6, 30))
    assert result == [
        date(2024, 2, 16),
        date(2024, 3, 15),
        date(2024, 4, 19),
        date(2024, 5, 17),
        date(2024, 6, 21),
    ]


def test_strike_grid_brackets_the_spot():
    grid = strike_grid(spot=500.0, width_pct=0.10, step=5.0)
    assert min(grid) == pytest.approx(450.0)
    assert max(grid) == pytest.approx(550.0)
    assert 500.0 in grid
