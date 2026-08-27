from datetime import date
from decimal import Decimal

import pytest

from drawdownguard.domain import Portfolio, ProposedOrder, WheelState
from drawdownguard.risk.gate import veto
from drawdownguard.risk.limits import Limits

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


def order(**overrides) -> ProposedOrder:
    values = {
        "symbol": "SPY",
        "right": "P",
        "strike": Decimal("560"),
        "expiry": date(2026, 8, 28),
        "contracts": -1,
        "limit_price": Decimal("2.35"),
        "delta": -0.30,
        "vega": 40.0,
        "assignment_prob": 0.25,
        "open_interest": 5000,
        "spread_pct": 0.5,
        "spot": 600.0,
    }
    values.update(overrides)
    return ProposedOrder(**values)


def portfolio(**overrides) -> Portfolio:
    values = {
        "equity": Decimal("300000"),
        "cash": Decimal("300000"),
        "peak_equity": Decimal("300000"),
        "deployed": Decimal("0"),
        "net_delta": 0.0,
        "vega": 0.0,
        "wheels": {"SPY": WheelState(symbol="SPY")},
    }
    values.update(overrides)
    return Portfolio(**values)


def test_a_clean_order_is_approved():
    assert veto(order(), portfolio(), LIMITS).approved is True


def test_naked_call_is_rejected_when_no_shares_are_held():
    verdict = veto(order(right="C"), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "naked" in verdict.reason.lower()


def test_covered_call_is_approved_when_shares_are_held():
    held = portfolio(wheels={"SPY": WheelState(symbol="SPY", leg="SHARES", shares=100)})
    assert veto(order(right="C", delta=0.30), held, LIMITS).approved is True


def test_cash_secured_put_is_rejected_without_the_cash():
    # one contract at strike 560 needs 56,000 of cash
    broke = portfolio(cash=Decimal("10000"))
    verdict = veto(order(), broke, LIMITS)
    assert verdict.approved is False
    assert "cash" in verdict.reason.lower()


def test_drawdown_beyond_the_limit_rejects_everything():
    drawn = portfolio(equity=Decimal("250000"), peak_equity=Decimal("300000"))
    verdict = veto(order(), drawn, LIMITS)  # 16.7% > 15%
    assert verdict.approved is False
    assert "drawdown" in verdict.reason.lower()


def test_position_concentration_limit():
    # 56,000 collateral on 200,000 equity = 28% > 25%.
    # peak_equity tracks equity so the drawdown check stays silent and the
    # concentration check is the one under test.
    small = portfolio(
        equity=Decimal("200000"),
        cash=Decimal("200000"),
        peak_equity=Decimal("200000"),
    )
    verdict = veto(order(), small, LIMITS)
    assert verdict.approved is False
    assert "position" in verdict.reason.lower()


def test_total_deployed_limit():
    loaded = portfolio(deployed=Decimal("160000"))  # +56,000 = 72% > 60%
    verdict = veto(order(), loaded, LIMITS)
    assert verdict.approved is False
    assert "deployed" in verdict.reason.lower()


def test_net_delta_band():
    """Equity 300,000, band 50% = 150,000. This order adds 18,000."""
    skewed = portfolio(net_delta_value=140_000.0)
    verdict = veto(order(delta=-0.30), skewed, LIMITS)
    assert verdict.approved is False
    assert "directional" in verdict.reason.lower()


def test_short_put_contributes_positive_delta():
    """A short put is a bullish position, so it offsets a short book."""
    skewed = portfolio(net_delta_value=-140_000.0)
    assert veto(order(delta=-0.30), skewed, LIMITS).approved is True


def test_the_band_treats_equal_exposure_equally_across_instruments():
    """The reason the unit changed.

    A cheap instrument and an expensive one, carrying the same dollar risk,
    must get the same answer. Under the old share-equivalent band they did not:
    at a quarter of a 1,000,000 account one position is 300 shares of SPY or
    1,000 of IWM, so a band in shares was strict on the cheap one and
    permissive on the expensive one for identical exposure.
    """
    # 1 contract of a 0.30-delta put: 30 share equivalents either way, but
    # 18,000 dollars at 600 a share and 18,000 at 60 a share with ten contracts.
    expensive = order(delta=-0.30, contracts=-1, spot=600.0)
    cheap = order(delta=-0.30, contracts=-10, spot=60.0)
    assert expensive.delta_value == cheap.delta_value

    book = portfolio(net_delta_value=140_000.0)
    assert veto(expensive, book, LIMITS).approved is False
    assert veto(cheap, book, LIMITS).approved is False


def test_the_band_scales_with_the_account():
    """A percentage limit means the same thing at every account size.

    The old absolute share count did not: 150 shares was most of a small
    account and a rounding error in a large one, under the same configuration.
    """
    small = portfolio(equity=Decimal("100000"), net_delta_value=45_000.0)
    large = portfolio(equity=Decimal("1000000"), net_delta_value=450_000.0)
    proposed = order(delta=-0.30, contracts=-1, spot=600.0)  # +18,000
    assert veto(proposed, small, LIMITS).approved is False  # 63k of 100k = 63%
    assert veto(proposed, large, LIMITS).approved is True  # 468k of 1M = 47%


def test_vega_budget():
    loaded = portfolio(vega=480.0)
    verdict = veto(order(vega=40.0), loaded, LIMITS)
    assert verdict.approved is False
    assert "vega" in verdict.reason.lower()


def test_assignment_probability_budget():
    verdict = veto(order(assignment_prob=0.55), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "assignment" in verdict.reason.lower()


def test_open_interest_floor():
    verdict = veto(order(open_interest=50), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "open interest" in verdict.reason.lower()


def test_spread_ceiling():
    verdict = veto(order(spread_pct=12.0), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "spread" in verdict.reason.lower()


def test_buying_to_open_is_rejected_outright():
    verdict = veto(order(contracts=1), portfolio(), LIMITS)
    assert verdict.approved is False
    assert "short" in verdict.reason.lower()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"right": "C"},
        {"assignment_prob": 0.55},
        {"open_interest": 50},
        {"spread_pct": 12.0},
    ],
)
def test_every_rejection_carries_a_non_empty_reason(kwargs):
    verdict = veto(order(**kwargs), portfolio(), LIMITS)
    assert verdict.approved is False
    assert verdict.reason.strip() != ""
