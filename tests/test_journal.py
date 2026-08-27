from datetime import UTC, date, datetime

from drawdownguard.journal import writer


def _utc_today():
    """The day the journal actually wrote.

    `date.today()` is local and the journal stamps in UTC. The two agree
    for twenty-two hours a day, which is exactly long enough for a test
    to look correct until someone runs it late at night.
    """
    return datetime.now(UTC).date()


def test_write_then_read_day_returns_what_was_written(tmp_path):
    writer.write("order_placed", {"symbol": "SPY", "contracts": -1}, directory=tmp_path)
    entries = writer.read_day(_utc_today(), directory=tmp_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "order_placed"
    assert entries[0]["payload"] == {"symbol": "SPY", "contracts": -1}


def test_write_appends_rather_than_overwrites(tmp_path):
    """Every cycle appends. Losing yesterday's reasoning loses the evidence."""
    writer.write("first", {}, directory=tmp_path)
    writer.write("second", {}, directory=tmp_path)
    events = [entry["event"] for entry in writer.read_day(_utc_today(), tmp_path)]
    assert events == ["first", "second"]


def test_every_line_carries_the_required_fields(tmp_path):
    writer.write("snapshot", {"equity": "1000000"}, directory=tmp_path)
    entry = writer.read_day(_utc_today(), tmp_path)[0]
    assert set(entry) == {
        "timestamp",
        "drawdownguard_env",
        "event",
        "severity",
        "payload",
    }


def test_severity_defaults_to_info(tmp_path):
    writer.write("snapshot", {}, directory=tmp_path)
    assert writer.read_day(_utc_today(), tmp_path)[0]["severity"] == "info"


def test_a_veto_is_recorded_at_its_own_severity(tmp_path):
    writer.write("gate_rejected", {"reason": "delta band"}, "veto", directory=tmp_path)
    assert writer.read_day(_utc_today(), tmp_path)[0]["severity"] == "veto"


def test_a_defect_is_recorded_at_its_own_severity(tmp_path):
    """`defect` means the risk-gate middleware fired: the toolset is misconfigured.

    It gets a level of its own precisely so it cannot be lost among the vetoes,
    which are the system working as designed.
    """
    writer.write("middleware_blocked", {}, "defect", directory=tmp_path)
    assert writer.read_day(_utc_today(), tmp_path)[0]["severity"] == "defect"


def test_an_unknown_severity_is_refused(tmp_path):
    """Free-text severities would make the journal unfilterable within a week."""
    try:
        writer.write("whatever", {}, "catastrophe", directory=tmp_path)
    except ValueError as exc:
        assert "catastrophe" in str(exc)
    else:
        raise AssertionError("an unknown severity must not be accepted")


def test_the_timestamp_is_utc_and_parseable(tmp_path):
    writer.write("snapshot", {}, directory=tmp_path)
    stamp = writer.read_day(_utc_today(), tmp_path)[0]["timestamp"]
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == UTC.utcoffset(None)


def test_reading_a_day_with_no_journal_is_empty_not_an_error(tmp_path):
    assert writer.read_day(date(2020, 1, 1), tmp_path) == []


def test_read_entries_returns_newest_first_across_days(tmp_path):
    """The status page shows recent activity, so ordering is its contract."""
    (tmp_path / "2026-08-24.jsonl").write_text(
        '{"timestamp": "2026-08-24T14:00:00+00:00", "drawdownguard_env": "dev", '
        '"event": "older", "severity": "info", "payload": {}}\n'
    )
    (tmp_path / "2026-08-25.jsonl").write_text(
        '{"timestamp": "2026-08-25T14:00:00+00:00", "drawdownguard_env": "dev", '
        '"event": "newer", "severity": "info", "payload": {}}\n'
    )
    events = [entry["event"] for entry in writer.read_entries(directory=tmp_path)]
    assert events == ["newer", "older"]


def test_read_entries_respects_its_limit(tmp_path):
    for index in range(5):
        writer.write(f"event_{index}", {}, directory=tmp_path)
    assert len(writer.read_entries(limit=2, directory=tmp_path)) == 2


def test_a_file_that_is_not_a_journal_day_is_ignored(tmp_path):
    """The journal directory is committed, so anything can end up beside it."""
    (tmp_path / "notes.jsonl").write_text('{"event": "stray"}\n')
    writer.write("real", {}, directory=tmp_path)
    events = [entry["event"] for entry in writer.read_entries(directory=tmp_path)]
    assert events == ["real"]


def test_a_corrupt_line_does_not_take_the_whole_day_with_it(tmp_path):
    """A half-written line from a killed run must not hide the rest of the day.

    The journal is append-only text and the runner can be terminated mid-write.
    Refusing to parse the entire file over one truncated line would lose the
    evidence for every cycle that ran correctly.
    """
    path = tmp_path / f"{date.today()}.jsonl"
    writer.write("good", {}, directory=tmp_path)
    with path.open("a") as handle:
        handle.write('{"event": "truncated"\n')
    writer.write("also_good", {}, directory=tmp_path)

    events = [entry["event"] for entry in writer.read_day(_utc_today(), tmp_path)]
    assert events == ["good", "also_good"]
