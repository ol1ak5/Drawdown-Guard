"""Mandates: promises a machine can check, and an audit that means something.

The load-bearing tests are the two that keep the audit honest: a mandate can
never loosen the permanent constitution, and being safer than promised is not
the same event as breaching a promise.
"""

import pytest

from flywheel.risk.limits import load_limits
from flywheel.risk.mandate import (
    Counterfactual,
    Mandate,
    audit,
    compliance_pct,
    load_all,
    load_mandate,
    verdict,
)


def mandate(**overrides) -> Mandate:
    values = {
        "name": "test",
        "universe": ["SPY", "TLT"],
        "max_deployed_pct": 30.0,
        "target_delta": {"min": 0.10, "max": 0.20},
        "max_concentration_pct": 75.0,
    }
    values.update(overrides)
    return Mandate(**values)


# --- the two constitutions --------------------------------------------------


def test_a_mandate_may_not_loosen_the_permanent_constitution():
    """The claim the whole design rests on.

    A client mandate can tie the agent's hands further. If it could untie them,
    "the client chose aggressive" would be a route around a risk limit and
    nothing would be permanent.
    """
    limits = load_limits()
    greedy = mandate(max_deployed_pct=limits.max_deployed_pct + 10)
    with pytest.raises(ValueError, match="never weaker"):
        greedy.validate_against(limits)


def test_a_stricter_mandate_is_accepted():
    limits = load_limits()
    careful = mandate(max_deployed_pct=limits.max_deployed_pct / 2)
    assert careful.validate_against(limits) is careful


def test_every_shipped_mandate_fits_under_the_constitution():
    """Caught a real mistake: aggressive was written at 85% against a 60% cap."""
    limits = load_limits()
    for name, loaded in load_all().items():
        assert loaded.max_deployed_pct <= limits.max_deployed_pct, name


def test_an_unknown_mandate_is_refused_rather_than_defaulted():
    with pytest.raises(KeyError, match="unknown mandate"):
        load_mandate("reckless")


def test_an_empty_universe_can_never_trade_and_is_rejected():
    with pytest.raises(ValueError, match="empty universe"):
        mandate(universe=[])


def test_an_inverted_delta_band_is_rejected():
    with pytest.raises(ValueError, match="above"):
        mandate(target_delta={"min": 0.40, "max": 0.10})


# --- the audit --------------------------------------------------------------


def test_a_clean_cycle_keeps_the_mandate():
    result = audit(mandate(), 20.0, 50.0, ["SPY"], [-0.15])
    assert verdict(result) == "MANDATE KEPT"
    assert compliance_pct(result) == 100.0


def test_trading_outside_the_universe_is_a_breach():
    result = audit(mandate(), 20.0, 50.0, ["QQQ"], [-0.15])
    assert "universe" in verdict(result)


def test_too_much_delta_is_a_breach():
    result = audit(mandate(), 20.0, 50.0, ["SPY"], [-0.45])
    assert "delta" in verdict(result)


def test_being_safer_than_promised_is_not_a_breach():
    """The distinction a real cycle exposed.

    On 2026-08-25 the analyst called stress and tightened delta to 0.145,
    below the balanced mandate's 0.15 floor. The first version of this audit
    reported a mandate violation for an agent that had been *more* careful than
    required. The floor exists to keep premium worth collecting, not to stop
    the agent from being safe.
    """
    result = audit(mandate(), 20.0, 50.0, ["SPY"], [-0.05])
    delta_check = next(v for v in result if v.check == "delta")
    assert delta_check.passed is True
    assert delta_check.cautious is True
    assert "not a breach" in delta_check.detail
    assert "KEPT" in verdict(result)
    assert "cautiously" in verdict(result)


def test_concentration_beyond_the_cap_is_a_breach():
    result = audit(mandate(max_concentration_pct=50.0), 20.0, 100.0, ["SPY"], [-0.15])
    assert "concentration" in verdict(result)


def test_a_cycle_that_traded_nothing_breaches_nothing():
    """Trading nothing cannot breach a cap, and reporting one would make the
    audit noise rather than signal."""
    assert verdict(audit(mandate(), 0.0, 0.0, [], [])) == "MANDATE KEPT"


def test_the_auditor_cannot_change_anything():
    """It reports; it does not intervene. An auditor able to correct what it
    audits is not an auditor."""
    before = mandate()
    audit(before, 99.0, 99.0, ["QQQ"], [-0.9])
    assert before.max_deployed_pct == 30.0
    assert before.universe == ["SPY", "TLT"]


# --- the counterfactual -----------------------------------------------------


def test_the_counterfactual_prices_the_discipline():
    """Otherwise a reader can fairly say the constrained agent simply traded
    less. The answer has to be a number."""
    cost = Counterfactual(
        premium_taken=240,
        premium_available=420,
        concentration_taken=47,
        concentration_available=68,
    )
    assert cost.forgone == 180
    assert "180" in cost.describe()
    assert "47.0%" in cost.describe()


def test_a_constraint_that_cost_nothing_says_so():
    cost = Counterfactual(
        premium_taken=300,
        premium_available=300,
        concentration_taken=40,
        concentration_available=40,
    )
    assert cost.forgone == 0
    assert "cost nothing" in cost.describe()


def test_the_counterfactual_never_reports_a_negative_saving():
    cost = Counterfactual(
        premium_taken=400,
        premium_available=300,
        concentration_taken=40,
        concentration_available=40,
    )
    assert cost.forgone == 0


# --- the downside budget ----------------------------------------------------


def test_a_budget_the_kill_switch_would_never_let_you_reach_is_refused():
    """The promise has to be one the system can actually be around to keep.

    The permanent kill-switch halts the agent at a 15% drawdown. A mandate
    offering the client a 20% downside budget would be selling tolerance the
    agent stops trading before it could ever exercise -- true on paper, useless
    in fact. It fails at load time rather than quietly on the worst day.
    """
    with pytest.raises(ValueError, match="kill-switch"):
        mandate(downside_budget_pct=20.0).validate_against(load_limits())


def test_a_mandate_may_not_invent_its_own_shock():
    """The ladder is fixed and published, and a mandate picks a rung from it.

    Left free, a mandate could promise against a 3% shock and report a perfect
    record forever. The rungs are the same every day for every client, which is
    what makes two mandates comparable at all.
    """
    with pytest.raises(ValueError, match="not a rung"):
        mandate(stress_shock_pct=7.0)


def test_the_budget_is_dollars_and_scales_with_the_account():
    """A promise stated in percent has to survive the account changing size."""
    m = mandate(downside_budget_pct=10.0)
    assert m.budget(1_000_000) == 100_000
    assert m.budget(400_000) == 40_000


def test_the_three_shipped_mandates_imply_three_different_portfolio_sizes():
    """The mandate sizes the book. This is the demonstration, in one assertion.

    Same market, same shock, three clients: the exposure each can carry
    unprotected differs by 3x, and nobody had to have a view on the market to
    work that out.
    """
    limits = {
        name: m.budget(1_000_000) / (m.stress_shock_pct / 100)
        for name, m in load_all().items()
    }
    assert limits["conservative"] == pytest.approx(250_000)
    assert limits["balanced"] == pytest.approx(500_000)
    assert limits["aggressive"] == pytest.approx(750_000)
