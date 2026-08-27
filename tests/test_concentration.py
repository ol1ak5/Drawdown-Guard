"""Risk concentration: how much of the book is really one bet.

The measurement exists to be constrained, so the tests are about the
properties a limit depends on — that contributions sum, that correlated
instruments do not count as diversification, and that an empty book is not
reported as concentrated.
"""

import numpy as np
import pytest

from drawdownguard.risk.concentration import (
    RISK_BUCKETS,
    bucket_of,
    by_bucket,
    component_contributions,
    describe,
    dominant,
)

RNG = np.random.default_rng(7)


def correlated(n: int = 500, rho: float = 0.95) -> tuple[np.ndarray, np.ndarray]:
    base = RNG.normal(0, 0.01, n)
    other = rho * base + np.sqrt(1 - rho**2) * RNG.normal(0, 0.01, n)
    return base, other


def test_contributions_sum_to_one():
    """Component contribution is exact by construction, not approximate."""
    a, b = correlated()
    shares = component_contributions(
        {"SPY": 100_000, "QQQ": 80_000}, {"SPY": a, "QQQ": b}
    )
    assert sum(shares.values()) == pytest.approx(1.0)


def test_two_highly_correlated_positions_are_one_risk():
    """The claim the whole module is built to demonstrate.

    Two positions in instruments correlating at 0.95 are not two income
    streams. Grouped into one bucket they report as a single concentrated
    exposure, which is what they are.
    """
    a, b = correlated(rho=0.95)
    grouped = by_bucket({"SPY": 100_000, "QQQ": 100_000}, {"SPY": a, "QQQ": b})
    assert grouped["equity"] == pytest.approx(100.0)
    assert dominant({"SPY": 100_000, "QQQ": 100_000}, {"SPY": a, "QQQ": b})[1] > 99


def test_an_uncorrelated_instrument_actually_reduces_concentration():
    equity, _ = correlated()
    rates = RNG.normal(0, 0.01, 500)
    only_equity = dominant({"SPY": 100_000}, {"SPY": equity})[1]
    with_rates = dominant(
        {"SPY": 100_000, "TLT": 100_000}, {"SPY": equity, "TLT": rates}
    )[1]
    assert with_rates < only_equity


def test_an_empty_book_is_not_concentrated():
    """100% of nothing would fail every check for no reason."""
    assert dominant({}, {}) == ("none", 0.0)
    assert component_contributions({"SPY": 0}, {"SPY": RNG.normal(0, 0.01, 100)}) == {}


def test_a_single_observation_cannot_support_a_covariance():
    assert component_contributions({"SPY": 1000}, {"SPY": np.array([0.01])}) == {}


def test_series_of_different_lengths_are_compared_over_a_common_window():
    """Otherwise the covariance is computed across mismatched dates."""
    long, short = RNG.normal(0, 0.01, 500), RNG.normal(0, 0.01, 120)
    shares = component_contributions(
        {"SPY": 100_000, "TLT": 100_000}, {"SPY": long, "TLT": short}
    )
    assert sum(shares.values()) == pytest.approx(1.0)


def test_an_unmapped_symbol_defaults_into_the_crowded_bucket():
    """Defaulting an unknown ticker to its own family would make anything
    unmapped look like free diversification."""
    assert bucket_of("SOMETHING_NEW") == "equity"
    assert bucket_of("TLT") == "rates"
    assert bucket_of("spy") == "equity"


def test_the_bucket_map_is_an_assumption_not_a_discovery():
    """Pinned so nobody reads the grouping as a model output.

    TLT is rates and the rest is equity because a human decided so. A derived
    grouping would need a clustering step this project has no data to support.
    """
    assert RISK_BUCKETS["TLT"] == "rates"
    assert RISK_BUCKETS["SPY"] == "equity"


def test_the_description_reads_as_a_chart():
    a, b = correlated()
    text = describe({"SPY": 100_000, "QQQ": 50_000}, {"SPY": a, "QQQ": b})
    assert "equity" in text
    assert "%" in text
