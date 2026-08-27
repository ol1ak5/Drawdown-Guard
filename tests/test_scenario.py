"""The client's week, fixed before it runs.

These tests are not about the agent. They are about the demonstration being
evidence rather than a story: every action decided in advance, committed, and
readable off the file rather than reconstructed afterwards from what happened.

A scenario chosen day by day as the market moved would look like a forecast
however the code behaved, and this project's whole claim is that it never makes
one. The commit date on `config/scenario.yaml` is what settles that, and these
tests keep the file in the shape that makes the commit mean something.
"""

from datetime import date

import pytest

from scripts.client_action import day_plan, scenario

ACTIONS = {"none", "sell_equity", "buy_equity"}


def test_the_scenario_records_the_day_it_was_decided():
    """Without this the file is just a plan, and a plan can be rewritten.

    The recorded date is what a reader checks against `git log` to see that
    the client's moves were fixed before the first cycle ran.
    """
    assert str(scenario()["recorded"]) == "2026-08-27"


def test_every_day_is_a_trading_day_and_they_run_in_order():
    days = scenario()["days"]
    assert [d["day"] for d in days] == list(range(1, len(days) + 1))
    previous = None
    for entry in days:
        when = date.fromisoformat(str(entry["date"]))
        assert when.weekday() < 5, f"day {entry['day']} falls on a weekend"
        if previous:
            assert when > previous
        previous = when


@pytest.mark.parametrize("number", range(1, 7))
def test_every_day_states_what_it_expects_before_it_happens(number):
    """The expectation is written down beside the action, in advance.

    An agent's behaviour is only evidence if somebody said what it should be
    first. Read afterwards, any outcome can be made to sound intended.
    """
    plan = day_plan(number)
    assert plan is not None
    assert plan["client_action"] in ACTIONS
    assert len(plan["expect"].split()) > 15


def test_the_week_contains_a_release_and_a_rebalance_not_only_a_purchase():
    """The three things Track 03 asks for are three different events, and none
    of them is triggered by the market.

    A week of ordinary prices demonstrates buying protection and nothing else.
    Selling shares is what makes an existing hedge surplus; buying them is what
    makes it insufficient. Without both, two thirds of the claim is untested.
    """
    actions = [d["client_action"] for d in scenario()["days"]]
    assert "sell_equity" in actions
    assert "buy_equity" in actions


def test_the_quiet_days_are_deliberate_and_there_is_more_than_one():
    """An agent that does nothing while prices move is the claim being tested.

    It cannot be shown by describing it -- only by leaving the agent alone and
    letting the journal record the silence. One quiet day could be an accident
    of scheduling; three is a statement.
    """
    quiet = [d for d in scenario()["days"] if d["client_action"] == "none"]
    assert len(quiet) >= 3


def test_an_action_names_its_instrument_and_its_size():
    """Nothing is left to be decided at the prompt. A quantity chosen on the
    day is a quantity that could have been chosen after seeing the market."""
    for entry in scenario()["days"]:
        if entry["client_action"] == "none":
            continue
        detail = entry["detail"]
        assert detail["symbol"]
        assert isinstance(detail["shares"], int)
        assert detail["shares"] > 0
