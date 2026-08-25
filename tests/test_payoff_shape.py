"""Spending income to bound the loss, and the decision of whether to.

The claims worth pinning are the ones a demo will make out loud: the loss is
finite, the floor is paid for out of the premium it protects, and declining
protection is a recorded decision rather than an omission.
"""

from decimal import Decimal

import numpy as np
import pytest

from flywheel.optimizer.payoff_shape import (
    MAX_PROTECTION_COST_PCT,
    ProtectedPut,
    choose_floor,
    payoff_at,
    unprotected_payoff,
    worth_protecting,
)


def position(**overrides) -> ProtectedPut:
    values = {
        "symbol": "SPY",
        "short_strike": Decimal("600"),
        "long_strike": Decimal("580"),
        "short_premium": Decimal("3.00"),
        "long_cost": Decimal("0.60"),
        "contracts": 1,
        "expiry": None,
    }
    values.update(overrides)
    return ProtectedPut(**values)


def chain(strikes, asks) -> list[dict]:
    return [
        {"right": "P", "strike": Decimal(str(k)), "ask": Decimal(str(a))}
        for k, a in zip(strikes, asks, strict=True)
    ]


# --- the payoff -------------------------------------------------------------


def test_the_loss_is_finite_which_the_bare_put_is_not():
    """The whole reason to spend the premium.

    A cash-secured put's floor is the strike going to zero. This one stops.
    """
    crash = np.array([0.0, 100.0, 300.0])
    protected = payoff_at(crash, position())
    bare = unprotected_payoff(crash, position())
    assert protected.min() > bare.min()
    assert protected.min() == pytest.approx(float(-position().worst_case))
    # And it is the same floor no matter how far the crash goes.
    assert protected[0] == pytest.approx(protected[1])


def test_the_worst_case_is_the_width_less_what_was_collected():
    p = position()  # 20 wide, 2.40 net
    assert p.worst_case == Decimal("1760.00")


def test_above_both_strikes_the_position_simply_keeps_the_net_premium():
    calm = np.array([620.0, 700.0])
    assert payoff_at(calm, position()) == pytest.approx(240.0)


def test_protection_costs_income_and_the_test_says_so():
    """Not free. The bare put keeps more when nothing bad happens, and a demo
    that showed only the protected line would make the trade look costless."""
    calm = np.array([650.0])
    assert unprotected_payoff(calm, position())[0] > payoff_at(calm, position())[0]


def test_the_cost_is_reported_as_a_share_of_the_premium_it_came_from():
    assert position().protection_cost_pct == pytest.approx(20.0)


# --- choosing the floor -----------------------------------------------------


def test_the_floor_bought_is_the_nearest_one_affordable():
    """Caught a real bug: the first version bought the *furthest* floor.

    The cap is the distance between the strikes. A floor at 580 under a short
    600 caps the loss at 20 a share; one at 540 caps it at 60. The distant
    floor is cheaper precisely because it protects less, so given a budget the
    nearest affordable strike is the right one.
    """
    rows = chain([580, 560, 540], [0.90, 0.60, 0.30])
    chosen = choose_floor(Decimal("600"), Decimal("3.00"), rows, spot=600.0)
    assert chosen["strike"] == Decimal("580")


def test_when_the_nearest_floor_is_unaffordable_it_steps_further_out():
    """Weaker protection is still protection, and still finite."""
    rows = chain([580, 560, 540], [2.90, 0.60, 0.30])
    chosen = choose_floor(Decimal("600"), Decimal("3.00"), rows, spot=600.0)
    assert chosen["strike"] == Decimal("560")


def test_nothing_affordable_returns_none_rather_than_the_cheapest_thing():
    """Declining is a real answer. Buying a floor that eats the trade is not."""
    rows = chain([580, 560], [2.90, 2.80])
    assert choose_floor(Decimal("600"), Decimal("3.00"), rows, spot=600.0) is None


def test_a_floor_above_the_short_strike_is_not_a_floor():
    rows = chain([610, 620], [0.20, 0.10])
    assert choose_floor(Decimal("600"), Decimal("3.00"), rows, spot=600.0) is None


def test_a_zero_premium_short_leg_buys_nothing():
    rows = chain([580], [0.10])
    assert choose_floor(Decimal("600"), Decimal("0"), rows, spot=600.0) is None


# --- the decision -----------------------------------------------------------


def test_cheap_protection_is_bought_and_says_why():
    ok, reason = worth_protecting(Decimal("3.00"), Decimal("0.60"), 35.0)
    assert ok is True
    assert "20%" in reason and "bought" in reason


def test_expensive_protection_is_declined_and_says_why():
    """Both outcomes are decisions. A log that recorded only purchases would
    read as an agent that never considered the alternative."""
    ok, reason = worth_protecting(Decimal("3.00"), Decimal("2.50"), 35.0)
    assert ok is False
    assert "83%" in reason and "declined" in reason


def test_the_budget_boundary_is_inclusive():
    ok, _ = worth_protecting(Decimal("100"), Decimal("35"), 35.0)
    assert ok is True


def test_the_default_budget_leaves_most_of_the_income_intact():
    """A position that spends most of its premium on a floor has stopped being
    an income trade."""
    assert MAX_PROTECTION_COST_PCT < 50
