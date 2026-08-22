import numpy as np
import pytest

from flywheel.optimizer.payoff import (
    assignment_prob,
    bs_delta,
    bs_price,
    bs_vega,
    loss_scenarios,
)

SPOT, TAU, VOL = 100.0, 30 / 365, 0.20


def test_atm_put_and_call_are_close_in_price():
    put = bs_price(SPOT, 100.0, TAU, VOL, "P")
    call = bs_price(SPOT, 100.0, TAU, VOL, "C")
    assert put == pytest.approx(call, abs=0.5)


def test_put_delta_is_negative_and_call_delta_is_positive():
    assert bs_delta(SPOT, 100.0, TAU, VOL, "P") < 0
    assert bs_delta(SPOT, 100.0, TAU, VOL, "C") > 0


def test_deeper_out_of_the_money_puts_are_cheaper():
    near = bs_price(SPOT, 98.0, TAU, VOL, "P")
    far = bs_price(SPOT, 90.0, TAU, VOL, "P")
    assert far < near


def test_higher_volatility_raises_the_premium():
    cheap = bs_price(SPOT, 95.0, TAU, 0.15, "P")
    rich = bs_price(SPOT, 95.0, TAU, 0.35, "P")
    assert rich > cheap


def test_vega_is_positive_and_peaks_near_the_money():
    atm = bs_vega(SPOT, 100.0, TAU, VOL)
    otm = bs_vega(SPOT, 85.0, TAU, VOL)
    assert atm > 0
    assert atm > otm


def test_atm_assignment_probability_is_near_one_half():
    assert assignment_prob(SPOT, 100.0, TAU, VOL, "P") == pytest.approx(0.5, abs=0.08)


def test_far_out_of_the_money_put_is_unlikely_to_be_assigned():
    assert assignment_prob(SPOT, 80.0, TAU, VOL, "P") < 0.05


def test_loss_scenarios_cap_the_gain_at_the_premium():
    returns = np.array([0.05, 0.02, 0.0, -0.02, -0.20])
    losses = loss_scenarios(SPOT, 95.0, TAU, premium=1.50, returns=returns, right="P")
    # premium 1.50 per share on one contract = 150 collected
    assert losses[0] == pytest.approx(-150.0)  # market up: keep the full premium
    assert losses[-1] == pytest.approx(-150.0 + 1500.0)  # spot 80 vs strike 95
    assert losses.shape == returns.shape


def test_a_zero_time_option_is_worth_its_intrinsic_value():
    assert bs_price(SPOT, 110.0, 0.0, VOL, "P") == pytest.approx(10.0)
    assert bs_price(SPOT, 110.0, 0.0, VOL, "C") == pytest.approx(0.0)
