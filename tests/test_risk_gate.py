from datetime import date
from decimal import Decimal

import pytest

from flywheel.domain import Portfolio, ProposedOrder, WheelState
from flywheel.risk.gate import veto
from flywheel.risk.limits import Limits

LIMITS = Limits(
    max_position_pct=25.0,
    max_deployed_pct=60.0,
    max_drawdown_pct=15.0,
    max_net_delta=150.0,
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
    skewed = portfolio(net_delta=140.0)  # +30 from this order = 170 > 150
    verdict = veto(order(delta=-0.30), skewed, LIMITS)
    assert verdict.approved is False
    assert "delta" in verdict.reason.lower()


def test_short_put_contributes_positive_delta():
    """A short put is a bullish position: selling a -0.30 delta put adds +30."""
    skewed = portfolio(net_delta=-140.0)
    assert veto(order(delta=-0.30), skewed, LIMITS).approved is True


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
