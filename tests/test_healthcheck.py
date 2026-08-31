"""The gate that decides whether a cycle may run at all.

One thing is tested here and it is the one that silently cost two days: the
schedule fires several times because GitHub's cron drifts, so something has to
tell an attempt that the day is already done. If that check is wrong in one
direction the agent trades twice on one book; in the other it refuses a day it
should have run.
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from healthcheck import Declined, check_not_already_run  # noqa: E402

from drawdownguard.journal import writer  # noqa: E402


@pytest.fixture(autouse=True)
def journal_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "JOURNAL_DIR", tmp_path)


def write(tmp_path, *events: str) -> None:
    (tmp_path / f"{date.today().isoformat()}.jsonl").write_text(
        "\n".join(json.dumps({"event": e, "payload": {}}) for e in events)
    )


def test_a_day_with_no_journal_may_run():
    assert "no cycle has run today" in check_not_already_run()


def test_a_day_that_only_got_as_far_as_measuring_may_run_again():
    """The case the retries exist for.

    An attempt that reached `mandate.stress` and then failed left a journal
    file behind. Treating any file as "already done" would turn a half-finished
    cycle into a day the agent refused to revisit.
    """
    write(writer.JOURNAL_DIR, "mandate.stress", "protection.plan")
    assert "no cycle has run today" in check_not_already_run()


def test_a_completed_cycle_declines_the_rest_of_the_day():
    write(writer.JOURNAL_DIR, "mandate.stress", "cycle.complete")
    with pytest.raises(Declined):
        check_not_already_run()


def test_an_unreadable_journal_does_not_cost_the_day():
    """Not evidence that nothing ran, and not a reason to refuse. See the check."""
    (writer.JOURNAL_DIR / f"{date.today().isoformat()}.jsonl").write_text("{ broken")
    assert "could not be read" in check_not_already_run()
