"""The value the promise is measured against, and why it does not move.

The budget was ten percent of whatever the account was worth that morning.
These tests are about the two things wrong with that -- it re-bases downward
while a fall is happening, and it makes "10%" mean something different every
day -- and about the one thing that must still move it: the calendar.
"""

from datetime import date

import pytest

from drawdownguard.risk.period import Period, current, load, save


def test_the_reference_is_written_once_and_read_back(tmp_path):
    path = tmp_path / "period.json"
    first, opened = current(1_006_000.0, today=date(2026, 8, 28), path=path)
    assert opened is True
    assert first.reference == 1_006_000.0

    # A later cycle, a smaller account, and the promise does not follow it down.
    again, opened = current(880_000.0, today=date(2026, 9, 15), path=path)
    assert opened is False
    assert again.reference == 1_006_000.0


def test_a_falling_account_does_not_shrink_the_promise(tmp_path):
    """The defect this file exists for.

    Ten percent of today re-bases every morning: lose ten percent, and the
    agent starts defending ten percent of the smaller number. Five steps of
    that permits a 47% loss and reports every one of them as kept -- and the
    budget shrinks fastest exactly when a fall has already started, so the
    agent would buy less protection as it became more necessary.
    """
    path = tmp_path / "period.json"
    current(1_000_000.0, today=date(2026, 8, 28), path=path)
    for equity in (900_000.0, 810_000.0, 729_000.0):
        held, _ = current(equity, today=date(2026, 10, 1), path=path)
        assert held.reference == 1_000_000.0
        assert held.reference * 0.10 == 100_000.0


def test_a_rising_account_does_not_move_it_either(tmp_path):
    """Not even upward, and that is the deliberate half.

    A floor tracking the peak is a stronger guarantee, and it moves when the
    market makes a new high -- which is the agent re-striking its hedge because
    prices went up. This project claims never to act on where the market is
    going, and one place where it does is enough to lose the argument.

    The gains are locked in at renewal, on the calendar rather than on a price.
    """
    path = tmp_path / "period.json"
    current(1_000_000.0, today=date(2026, 8, 28), path=path)
    grown, opened = current(1_400_000.0, today=date(2026, 11, 3), path=path)
    assert opened is False
    assert grown.reference == 1_000_000.0


def test_renewal_re_bases_at_whatever_the_account_is_worth_that_day(tmp_path):
    """A client who made money spends the next year protecting the larger
    number. This is the only thing that moves the reference."""
    path = tmp_path / "period.json"
    current(1_000_000.0, today=date(2026, 8, 28), path=path)
    renewed, opened = current(1_300_000.0, today=date(2027, 8, 28), path=path)
    assert opened is True
    assert renewed.reference == 1_300_000.0
    assert renewed.started == date(2027, 8, 28)


@pytest.mark.parametrize(
    ("started", "ends"),
    [
        (date(2026, 8, 28), date(2027, 8, 28)),
        (date(2026, 1, 31), date(2027, 1, 31)),
        # No 29th of February in 2027, so the renewal clamps rather than
        # rolling into March -- a date that rolled would drift a day at a time.
        (date(2024, 2, 29), date(2025, 2, 28)),
    ],
)
def test_the_promise_runs_exactly_twelve_months(started, ends):
    assert Period(started=started, reference=1.0).ends() == ends


def test_a_promise_expires_on_its_end_date_not_after_it(tmp_path):
    promise = Period(started=date(2026, 8, 28), reference=1.0)
    assert promise.expired(date(2027, 8, 27)) is False
    assert promise.expired(date(2027, 8, 28)) is True


def test_nothing_on_disk_means_no_promise_rather_than_a_default(tmp_path):
    """A reference invented from a default would be a promise nobody made."""
    assert load(tmp_path / "absent.json") is None


def test_the_horizon_travels_with_the_promise(tmp_path):
    """A mandate renewed under a different horizon must not silently keep the
    old one: the window is half of what the client actually said."""
    path = tmp_path / "period.json"
    save(Period(started=date(2026, 8, 28), reference=1.0, horizon_months=6), path)
    assert load(path).ends() == date(2027, 2, 28)


def test_the_promise_spends_what_the_account_has_already_lost():
    """9,998 of allowance, 1,777 already gone, 8,221 left to cover the rest."""
    from drawdownguard.risk.period import remaining_budget

    assert remaining_budget(9997.84, 99978.43, 98201.20) == pytest.approx(8220.61)


def test_a_gain_does_not_enlarge_the_promise():
    """The budget is 10% of the reference, not 10% of the best day since.

    Letting a gain add to the allowance would ratchet the promise upward on
    every good week and quietly permit a deeper fall than the client agreed to.
    """
    from drawdownguard.risk.period import remaining_budget

    assert remaining_budget(9997.84, 99978.43, 105000.0) == pytest.approx(9997.84)


def test_an_account_already_past_its_budget_has_nothing_left():
    """Not a negative allowance.

    A negative number would make every hedge look unaffordable; the breach is
    reported by `uncovered_risk`, which is the field that exists to say so.
    """
    from drawdownguard.risk.period import remaining_budget

    assert remaining_budget(9997.84, 99978.43, 80000.0) == 0.0
