from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from drawdownguard.domain import OpenContract, Portfolio, Position, Verdict


def test_wheel_state_defaults_to_cash():
    state = Position(symbol="SPY")
    assert state.leg == "CASH"
    assert state.shares == 0
    assert state.basis is None
    assert state.premium_collected == Decimal("0")


def test_short_contract_has_negative_quantity():
    contract = OpenContract(
        occ_symbol="SPY260828P00560000",
        right="P",
        strike=Decimal("560"),
        expiry=date(2026, 8, 28),
        contracts=-1,
        premium=Decimal("2.35"),
    )
    assert contract.is_short is True
    assert contract.notional == Decimal("56000")


def test_verdict_rejected_requires_a_reason():
    with pytest.raises(ValidationError, match="reason"):
        Verdict(approved=False, reason="")


def test_portfolio_drawdown_is_computed_from_peak():
    portfolio = Portfolio(
        equity=Decimal("90000"),
        cash=Decimal("90000"),
        peak_equity=Decimal("100000"),
    )
    assert portfolio.drawdown_pct == pytest.approx(10.0)
