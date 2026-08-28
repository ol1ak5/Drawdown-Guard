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

from drawdownguard.risk import period


@pytest.fixture(autouse=True)
def isolated_period(tmp_path, monkeypatch):
    """Every test gets its own empty promise directory.

    Autouse rather than opt-in: a test that forgets it does not fail loudly,
    it passes against the wrong number.
    """
    monkeypatch.setattr(period, "DEFAULT_PATH", tmp_path / "period.json")
