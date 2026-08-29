"""Test isolation for state the agent writes to disk.

`period.current` reads and *writes* `data/state/period.json` at a path relative
to the working directory. Without this fixture the suite runs against whatever
promise the live account happens to be under: two graph tests were failing on a
clean tree with `expected 500,000, got 499,411.07`, which is 600,000 minus ten
percent of the reference committed in that file. The tests were not wrong and
the code was not wrong -- the tests were reading production state, which means
a red suite could not be told apart from an edited file.
"""

import pytest

from drawdownguard.agent import nodes
from drawdownguard.risk import changes, period


@pytest.fixture(autouse=True)
def isolated_period(tmp_path, monkeypatch):
    """Every test gets its own empty promise directory.

    Autouse rather than opt-in: a test that forgets it does not fail loudly,
    it passes against the wrong number.
    """
    monkeypatch.setattr(period, "DEFAULT_PATH", tmp_path / "period.json")


@pytest.fixture(autouse=True)
def isolated_holdings(tmp_path, monkeypatch):
    """Every test gets its own empty holdings snapshot.

    Same argument as the promise above: `mandate` writes `holdings.json` at a
    path relative to the working directory, so without this the suite both
    reads the live book as "yesterday" and overwrites it on the way out --
    a test run would silently become the reference the next real cycle
    compares against.
    """
    monkeypatch.setattr(changes, "DEFAULT_SNAPSHOT", tmp_path / "holdings.json")


@pytest.fixture(autouse=True)
def offline_reviewer(monkeypatch):
    """No test reaches a language model.

    `mandate` asks the reviewer whenever the book moved, and a developer with a
    real key in `.env` was getting live network calls out of the unit suite --
    slow, billed, and non-deterministic. Tests that care about the reviewer
    pass their own model in; everything else gets silence, which is the same
    thing the cycle does when the model is unreachable.
    """

    async def unreachable(*_args, **_kwargs):
        return None  # the model did not answer

    monkeypatch.setattr(nodes, "review", unreachable)


@pytest.fixture(autouse=True)
def offline_chooser(monkeypatch):
    """No test lets a language model choose a structure.

    `protect` asks the chooser whenever a sleeve has more than one admissible
    hedge. Left live, the suite would make billed network calls and the pick
    would differ between runs -- and the thing most of these tests assert on is
    exactly which structure came out. Returning nothing sends every sleeve
    through `remedy.choose`, which is the behaviour they were written against.
    """

    async def undecided(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(nodes, "pick", undecided)
