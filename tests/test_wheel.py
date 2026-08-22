from datetime import date
from decimal import Decimal

import pytest

from flywheel.domain import OpenContract, WheelState
from flywheel.wheel import (
    IllegalTransition,
    next_action,
    on_call_assigned,
    on_expired_worthless,
    on_put_assigned,
    on_sold_call,
    on_sold_put,
)


def short_put(strike="560", premium="2.35"):
    return OpenContract(
        occ_symbol=f"SPY260828P00{strike}000",
        right="P",
        strike=Decimal(strike),
        expiry=date(2026, 8, 28),
        contracts=-1,
        premium=Decimal(premium),
    )


def short_call(strike="570", premium="1.80"):
    return OpenContract(
        occ_symbol=f"SPY260904C00{strike}000",
        right="C",
        strike=Decimal(strike),
        expiry=date(2026, 9, 4),
        contracts=-1,
        premium=Decimal(premium),
    )


def test_cash_wants_to_sell_a_put():
    assert next_action(WheelState(symbol="SPY")) == "SELL_PUT"


def test_shares_want_to_sell_a_call():
    state = WheelState(symbol="SPY", leg="SHARES", shares=100)
    assert next_action(state) == "SELL_CALL"


def test_open_legs_hold():
    for leg in ("PUT_OPEN", "CALL_OPEN"):
        assert next_action(WheelState(symbol="SPY", leg=leg)) == "HOLD"


def test_selling_a_put_moves_cash_to_put_open_and_banks_premium():
    state = on_sold_put(WheelState(symbol="SPY"), short_put())
    assert state.leg == "PUT_OPEN"
    assert state.premium_collected == Decimal("235")  # 2.35 * 100
    assert len(state.contracts) == 1


def test_put_expiring_worthless_returns_to_cash_and_keeps_premium():
    state = on_expired_worthless(on_sold_put(WheelState(symbol="SPY"), short_put()))
    assert state.leg == "CASH"
    assert state.contracts == []
    assert state.premium_collected == Decimal("235")
    assert state.cycle_count == 1


def test_put_assignment_delivers_shares_and_sets_basis_below_strike():
    state = on_put_assigned(on_sold_put(WheelState(symbol="SPY"), short_put()))
    assert state.leg == "SHARES"
    assert state.shares == 100
    # basis = strike - premium per share = 560 - 2.35
    assert state.basis == Decimal("557.65")


def test_each_covered_call_lowers_the_basis_further():
    state = on_put_assigned(on_sold_put(WheelState(symbol="SPY"), short_put()))
    state = on_expired_worthless(on_sold_call(state, short_call()))
    assert state.leg == "SHARES"
    assert state.basis == Decimal("555.85")  # 557.65 - 1.80


def test_call_assignment_sells_the_shares_and_returns_to_cash():
    state = on_put_assigned(on_sold_put(WheelState(symbol="SPY"), short_put()))
    state = on_call_assigned(on_sold_call(state, short_call()))
    assert state.leg == "CASH"
    assert state.shares == 0
    assert state.basis is None


def test_selling_a_call_without_shares_is_refused():
    with pytest.raises(IllegalTransition, match="naked"):
        on_sold_call(WheelState(symbol="SPY"), short_call())


def test_selling_a_call_against_too_few_shares_is_refused():
    state = WheelState(symbol="SPY", leg="SHARES", shares=50)
    with pytest.raises(IllegalTransition, match="naked"):
        on_sold_call(state, short_call())


def test_selling_a_second_put_while_one_is_open_is_refused():
    state = on_sold_put(WheelState(symbol="SPY"), short_put())
    with pytest.raises(IllegalTransition):
        on_sold_put(state, short_put(strike="555"))
