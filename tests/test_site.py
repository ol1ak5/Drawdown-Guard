import json
from datetime import datetime

from flywheel.journal import writer
from flywheel.journal.site import build_site, entry_from_journal, render_site


def _entry(**overrides):
    values = {
        "ts": "2026-08-25T14:02:11Z",
        "symbol": "SPY",
        "action": "sell_put",
        "regime": "calm",
        "verdict": "approved",
        "detail": "SPY260918P00620000 x1 @ 4.20",
    }
    values.update(overrides)
    return values


def test_the_page_is_a_complete_html_document():
    html = render_site([_entry()], [], datetime(2026, 8, 25, 14, 5))
    assert html.startswith("<!doctype html>")
    assert "</html>" in html


def test_journal_entries_appear_newest_first():
    old = _entry(ts="2026-08-24T14:00:00Z", detail="older")
    new = _entry(ts="2026-08-25T14:00:00Z", detail="newer")
    html = render_site([old, new], [], datetime(2026, 8, 25, 14, 5))
    assert html.index("newer") < html.index("older")


def test_rejections_are_shown_not_hidden():
    """The gate refusing a trade is the most interesting thing this agent does.

    A status page listing only fills throws away the strongest evidence on it.
    """
    html = render_site(
        [_entry(verdict="rejected", detail="assignment probability 0.41 > 0.35")],
        [],
        datetime(2026, 8, 25, 14, 5),
    )
    assert "rejected" in html
    assert "0.41" in html


def test_open_wheels_render_their_basis():
    wheels = [{"symbol": "SPY", "leg": "SHARES", "basis": "472.70", "cycles": 3}]
    html = render_site([], wheels, datetime(2026, 8, 25, 14, 5))
    assert "472.70" in html
    assert "SHARES" in html


def test_html_is_escaped():
    html = render_site(
        [_entry(detail="<script>alert(1)</script>")], [], datetime(2026, 8, 25, 14, 5)
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_an_empty_journal_still_renders():
    html = render_site([], [], datetime(2026, 8, 25, 14, 5))
    assert "No cycles recorded yet" in html


def test_a_wheel_with_no_basis_yet_renders_without_inventing_one():
    """A wheel in CASH has no basis. The cell must be blank, not 0.00."""
    wheels = [{"symbol": "IWM", "leg": "CASH", "basis": None, "cycles": 0}]
    html = render_site([], wheels, datetime(2026, 8, 25, 14, 5))
    assert "IWM" in html
    assert "0.00" not in html


def test_the_page_references_no_external_resource():
    """No CDN, no font, no script. A judge's click must not depend on a network.

    It also keeps the published artifact provably incapable of doing anything
    beyond rendering: the page cannot reach the broker if it cannot reach out.
    """
    html = render_site([_entry()], [], datetime(2026, 8, 25, 14, 5))
    for forbidden in ("http://", "https://cdn", "<script", "@import"):
        assert forbidden not in html


def test_a_journal_line_becomes_a_row():
    """The journal's own shape is not the page's shape; this is the seam."""
    line = {
        "timestamp": "2026-08-25T14:02:11+00:00",
        "flywheel_env": "dev",
        "event": "order_rejected",
        "severity": "veto",
        "payload": {
            "symbol": "SPY",
            "action": "sell_put",
            "regime": "elevated",
            "reason": "net delta band",
        },
    }
    row = entry_from_journal(line)
    assert row["symbol"] == "SPY"
    assert row["verdict"] == "rejected"
    assert row["regime"] == "elevated"
    assert "net delta band" in row["detail"]


def test_an_info_line_reads_as_approved():
    line = {
        "timestamp": "2026-08-25T14:02:11+00:00",
        "flywheel_env": "dev",
        "event": "order_placed",
        "severity": "info",
        "payload": {"symbol": "SPY", "detail": "SPY260918P00620000 x1 @ 4.20"},
    }
    assert entry_from_journal(line)["verdict"] == "approved"


def test_a_defect_line_is_not_flattened_into_a_rejection():
    """`defect` means the middleware fired: a leak, not the gate working."""
    line = {
        "timestamp": "2026-08-25T14:02:11+00:00",
        "flywheel_env": "dev",
        "event": "middleware_blocked",
        "severity": "defect",
        "payload": {"symbol": "SPY"},
    }
    assert entry_from_journal(line)["verdict"] == "defect"


def test_build_site_writes_a_page_from_the_journal_and_the_snapshot(tmp_path):
    writer.write(
        "order_placed",
        {"symbol": "SPY", "action": "sell_put", "detail": "SPY260918P00620000 x1"},
        directory=tmp_path / "journal",
    )
    snapshot = tmp_path / "wheels.json"
    snapshot.write_text(
        json.dumps({"SPY": {"leg": "PUT_OPEN", "basis": "615.80", "cycle_count": 2}})
    )

    out = build_site(tmp_path / "index.html", tmp_path / "journal", snapshot)

    assert out.exists()
    page = out.read_text()
    assert "SPY260918P00620000 x1" in page
    assert "615.80" in page
    assert "PUT_OPEN" in page


def test_build_site_runs_on_a_cycle_that_traded_nothing(tmp_path):
    """ "Considered and declined" is a state worth publishing.

    A page that only updates on fills would imply the agent was asleep on the
    days it was most careful.
    """
    out = build_site(
        tmp_path / "index.html", tmp_path / "journal", tmp_path / "absent.json"
    )
    assert "No cycles recorded yet" in out.read_text()


def test_no_source_link_is_published_when_the_repository_is_unknown():
    """A guessed link is worse than a missing one: it looks authoritative.

    The repository does not exist yet, so the footer must not invent a URL.
    """
    html = render_site([_entry()], [], datetime(2026, 8, 25, 14, 5))
    assert "<a href" not in html
    assert "paper trading only" in html


def test_a_supplied_repository_link_is_rendered():
    html = render_site(
        [_entry()],
        [],
        datetime(2026, 8, 25, 14, 5),
        repository_url="https://github.com/example/flywheel-agent",
    )
    assert 'href="https://github.com/example/flywheel-agent"' in html
