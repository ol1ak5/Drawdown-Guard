"""Mandates: promises a machine can check, and an audit that means something.

The load-bearing tests are the two that keep the audit honest: a mandate can
never loosen the permanent constitution, and being safer than promised is not
the same event as breaching a promise.
"""

import pytest

from drawdownguard.risk.limits import load_limits
from drawdownguard.risk.mandate import (
    Mandate,
    load_mandate,
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


def test_an_unknown_mandate_is_refused_rather_than_defaulted():
    with pytest.raises(KeyError, match="unknown mandate"):
        load_mandate("reckless")


def test_an_empty_universe_can_never_trade_and_is_rejected():
    with pytest.raises(ValueError, match="empty universe"):
        mandate(universe=[])


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


def test_the_release_band_is_a_band_and_not_a_line():
    """Both ends refused, for opposite reasons.

    At 0 the agent buys and sells protection at the same threshold and pays the
    spread twice to end where it started. At 100 it would have to hold the
    entire budget as unused headroom before letting any hedge go, which is a
    band so wide nothing ever leaves.
    """
    with pytest.raises(ValueError, match="not a band"):
        mandate(release_margin_pct=-1.0)
    with pytest.raises(ValueError, match="not a band"):
        mandate(release_margin_pct=100.0)


def test_a_mandate_that_says_nothing_has_not_granted_the_power_to_sell():
    """The one default in this file that points at less freedom, not more.

    Every other field is a limit the client relaxes, so silence sensibly means
    the permissive value. This one is a power the client grants, and silence
    cannot mean yes: inheriting permission to dispose of somebody's assets from
    an unwritten line is exactly the failure the mandate exists to prevent.
    """
    assert not mandate().allow_reduce_exposure
    assert mandate(allow_reduce_exposure=True).allow_reduce_exposure


def test_the_mandate_no_longer_ranks_the_option_remedies():
    """Removed, not renamed, and this test is the reason it stays removed.

    `protection_order` gave the same answer on every day of every market. An
    agent reading it was replaying a decision somebody made once, which is the
    opposite of the behaviour a mandate is supposed to constrain. Choosing
    between a put and a collar is an observation about today's prices and lives
    in `remedy.choose`; the mandate keeps the constraints.
    """
    m = mandate()
    assert not hasattr(m, "protection_order")
    # Pydantic ignores unknown keys by default, so this asserts the field is
    # gone rather than merely unset.
    assert "protection_order" not in m.model_dump()


def test_the_promise_names_how_long_it_runs():
    """Twelve months, and the window protection is bought in follows from it.

    365 days of floor, a year of ceiling: anything expiring sooner leaves the
    client bare for the rest of a promise still running, and anything further
    out costs more while buying nothing that was asked for.
    """
    mandate = load_mandate("balanced")
    assert mandate.horizon_months == 12
    low, high = mandate.protection_dte
    assert low == 365
    assert high == 730


def test_the_wheel_window_has_no_say_over_how_long_protection_lasts():
    """`config/strategy.yaml` says 20 to 33 days. That was calibrated against
    the option history on disk for a backtest of the options wheel, and it has
    nothing to say about a client's protection horizon.

    Left in charge it bought 22-day puts against a twelve-month promise, at
    0.25 a share -- which is what three weeks of coverage 16% out of the money
    is worth, and it is worth that because it is worth nothing.
    """
    low, _ = load_mandate("balanced").protection_dte
    assert low > 33
