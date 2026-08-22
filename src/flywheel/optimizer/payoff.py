"""Black-Scholes pricing, greeks, and empirical loss scenarios.

Pure numerics. Nothing here touches a network or a broker, and the only project
import is a share-count constant, so the backtest and the live agent run exactly
the same code.
"""

import numpy as np
from scipy.stats import norm

from flywheel.domain import SHARES_PER_CONTRACT

DEFAULT_RATE = 0.04


def _d1_d2(
    spot: float, strike: float, tau: float, vol: float, rate: float
) -> tuple[float, float]:
    denominator = vol * np.sqrt(tau)
    d1 = (np.log(spot / strike) + (rate + 0.5 * vol**2) * tau) / denominator
    return d1, d1 - denominator


def bs_price(
    spot: float,
    strike: float,
    tau: float,
    vol: float,
    right: str,
    rate: float = DEFAULT_RATE,
) -> float:
    """Option value per share."""
    if tau <= 0 or vol <= 0:
        intrinsic = strike - spot if right == "P" else spot - strike
        return float(max(intrinsic, 0.0))
    d1, d2 = _d1_d2(spot, strike, tau, vol, rate)
    discount = np.exp(-rate * tau)
    if right == "P":
        value = strike * discount * norm.cdf(-d2) - spot * norm.cdf(-d1)
    else:
        value = spot * norm.cdf(d1) - strike * discount * norm.cdf(d2)
    return float(value)


def bs_delta(
    spot: float,
    strike: float,
    tau: float,
    vol: float,
    right: str,
    rate: float = DEFAULT_RATE,
) -> float:
    if tau <= 0 or vol <= 0:
        in_the_money = (strike > spot) if right == "P" else (spot > strike)
        sign = -1.0 if right == "P" else 1.0
        return sign if in_the_money else 0.0
    d1, _ = _d1_d2(spot, strike, tau, vol, rate)
    return float(norm.cdf(d1) - 1.0 if right == "P" else norm.cdf(d1))


def bs_vega(
    spot: float, strike: float, tau: float, vol: float, rate: float = DEFAULT_RATE
) -> float:
    """Sensitivity per share to a 1.00 (i.e. 100 point) change in volatility."""
    if tau <= 0 or vol <= 0:
        return 0.0
    d1, _ = _d1_d2(spot, strike, tau, vol, rate)
    return float(spot * norm.pdf(d1) * np.sqrt(tau))


def assignment_prob(
    spot: float,
    strike: float,
    tau: float,
    vol: float,
    right: str,
    rate: float = DEFAULT_RATE,
) -> float:
    """Risk-neutral probability of finishing in the money.

    A proxy for assignment probability, not the true figure: it ignores early
    exercise. Adequate for budgeting, and stated as an approximation wherever
    it is reported.
    """
    if tau <= 0 or vol <= 0:
        in_the_money = (strike > spot) if right == "P" else (spot > strike)
        return 1.0 if in_the_money else 0.0
    _, d2 = _d1_d2(spot, strike, tau, vol, rate)
    return float(norm.cdf(-d2) if right == "P" else norm.cdf(d2))


def loss_scenarios(
    spot: float,
    strike: float,
    tau: float,
    premium: float,
    returns: np.ndarray,
    right: str,
) -> np.ndarray:
    """Dollar loss per contract at expiry under each historical return.

    Positive values are losses, which is the sign convention the CVaR
    formulation in model.py expects. Returns are total returns over the
    holding period, not annualised.
    """
    terminal = spot * (1.0 + np.asarray(returns, dtype=float))
    if right == "P":
        intrinsic = np.maximum(strike - terminal, 0.0)
    else:
        intrinsic = np.maximum(terminal - strike, 0.0)
    return (intrinsic - premium) * SHARES_PER_CONTRACT
