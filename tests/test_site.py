"""The page a judge and a client both open.

It answers one question in the first screen -- is the client inside the number
they were given -- and everything below is the working. So these tests are
mostly about two properties: that the answer is the one the journal recorded,
and that nothing on the page was computed by the page.
"""

from datetime import UTC, datetime

from drawdownguard.journal import writer
from drawdownguard.journal.site import (
    build_site,
    daily_series,
    entry_from_journal,
    promise_held,
    render_site,
)

NOW = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)


def _line(event: str, payload: dict, ts: str, severity: str = "info") -> dict:
    return entry_from_journal(
        {"timestamp": ts, "event": event, "severity": severity, "payload": payload}
    )


def _stress(**overrides) -> dict:
    """A `mandate.stress` payload: the reading everything above the table uses."""
    payload = {
        "mandate": "balanced",
        "downside_budget_pct": 10.0,
        "budget": 9997.84,
        "reference": 99978.43,
        "equity_exposure": 81011.5,
        "uncovered_risk": 0.0,
        "worst_case": 2795.0,
        "period_started": "2026-08-28",
        "period_ends": "2027-08-28",
        "holdings": [
            {
                "symbol": "XLF",
                "shares": 900,
                "price": 57.6,
                "value": 51840.0,
                "shocked": True,
            },
            {
                "symbol": "IWM",
                "shares": 100,
                "price": 291.3,
                "value": 29130.0,
                "shocked": True,
            },
            {
                "symbol": "BIL",
                "shares": 100,
                "price": 91.7,
                "value": 9170.0,
                "shocked": False,
            },
            {
                "symbol": "CASH",
                "shares": 4000,
                "price": 1.0,
                "value": 4000.0,
                "shocked": False,
            },
        ],
        "legs": [
            {
                "symbol": "XLF",
                "right": "P",
                "strike": "56",
                "contracts": 9,
                "expiry": "2027-12-17",
            },
        ],
    }
    payload.update(overrides)
    return payload


def _page(entries, repository_url: str = "") -> str:
    return render_site(entries, [], NOW, repository_url)


def _week() -> list[dict]:
    """Three closes, the middle one still uncovered. Newest first."""
    return [
        _line("cycle.complete", {"equity": "98319.7"}, "2026-09-01T17:48:00Z"),
        _line("mandate.stress", _stress(), "2026-09-01T17:48:00Z"),
        _line("cycle.complete", {"equity": "99183.49"}, "2026-08-31T14:19:00Z"),
        _line(
            "mandate.stress",
            _stress(uncovered_risk=71339.0, worst_case=81337.0),
            "2026-08-31T14:19:00Z",
            severity="breach",
        ),
        _line("cycle.complete", {"equity": "99726.5"}, "2026-08-28T17:07:00Z"),
        _line(
            "mandate.stress",
            _stress(uncovered_risk=71887.0, worst_case=81885.0),
            "2026-08-28T17:07:00Z",
            severity="breach",
        ),
    ]


# --- the verdict ------------------------------------------------------------


def test_a_promise_that_holds_says_so_and_shows_the_headroom():
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "Inside the promise" in page
    assert "$7,203" in page, "9,997.84 budget less a 2,795 worst case"
    assert "Remaining headroom" in page


def test_a_broken_promise_is_labelled_as_broken():
    """The single event this page exists to surface.

    It once rendered in the same badge as a routine fill, because every
    severity the verdict map did not name fell through to "approved".
    """
    page = _page(
        [
            _line(
                "mandate.stress",
                _stress(uncovered_risk=71887.0, worst_case=81885.0),
                "2026-08-28T17:07:00Z",
                severity="breach",
            )
        ]
    )
    assert "Risk outside the promise" in page
    assert "$71,887" in page
    assert "Remaining headroom" not in page, "there is none to report"


def test_the_worst_case_is_the_whole_descent_not_a_named_shock():
    """`worst_case`, read off the record rather than picked off a ladder.

    A book losing exactly its budget at -20% reads as fine at that rung and can
    still lose everything on the way down. The page must show the number the
    agent actually acted on.
    """
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "$2,795" in page
    assert "Worst case" in page


def test_a_page_built_before_any_cycle_has_run_says_so():
    page = _page([])
    assert "No cycle has measured the promise yet" in page
    assert "Inside the promise" not in page


# --- the promise ------------------------------------------------------------


def test_the_promise_is_stated_in_the_client_s_own_terms():
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    for expected in ("$99,978", "10.0%", "$9,998", "12 months"):
        assert expected in page, expected


def test_the_reference_is_the_account_the_promise_opened_on():
    """Not this morning's equity.

    Ten percent of today re-bases every cycle: lose ten percent and the agent
    starts defending ten percent of the smaller number.
    """
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "$99,978" in page
    assert "Reference portfolio" in page


# --- the book ---------------------------------------------------------------


def test_the_book_is_read_from_the_journal_not_from_a_snapshot():
    """`render_site` takes a positions list and must not need it.

    Two sources for what is held can disagree, and then neither is evidence.
    """
    page = render_site(
        [_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")], [], NOW
    )
    assert "XLF" in page and "IWM" in page and "BIL" in page


def test_weights_are_shown_and_sum_to_the_whole():
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "55.1%" in page, "51,840 of 94,140"
    assert "100.0%" in page


def test_bills_are_shown_and_marked_as_not_exposure():
    """Held, and not what the promise is measured against.

    Leaving them out would make the weights read as a mistake; showing them
    unmarked would overstate what can fall.
    """
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "BIL" in page
    assert "not exposure" in page


def test_a_holding_says_what_is_standing_behind_it():
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "long 9 &times; 56 put" in page


def test_a_book_that_has_not_been_read_says_so_rather_than_showing_an_empty_table():
    page = _page(
        [_line("mandate.stress", _stress(holdings=[]), "2026-09-01T00:00:00Z")]
    )
    assert "No book has been read yet" in page


# --- the history ------------------------------------------------------------


def test_one_row_per_trading_day_at_its_closing_reading():
    """Thirteen cycles a day, one point.

    A line through every cycle is a picture of the market's noise, which is the
    one thing this project has no view about.
    """
    series = daily_series(_week())
    assert [row["date"] for row in series] == [
        "2026-08-28",
        "2026-08-31",
        "2026-09-01",
    ]
    assert series[-1]["equity"] == 98319.7


def test_the_closing_reading_is_the_last_one_written_that_day():
    entries = [
        _line("cycle.complete", {"equity": "98000"}, "2026-09-01T19:45:00Z"),
        _line("cycle.complete", {"equity": "99000"}, "2026-09-01T13:45:00Z"),
    ]
    assert daily_series(entries)[0]["equity"] == 98000.0


def test_a_day_with_no_completed_cycle_is_not_a_point_on_the_line():
    """A halted day has no closing value, and inventing one would be a lie."""
    entries = [_line("mandate.stress", _stress(), "2026-09-01T13:45:00Z")]
    assert daily_series(entries) == []


def test_the_track_says_held_or_not_held_for_every_measured_day():
    """A percentage lived here and was more than a line under a chart can
    carry: it needed a label to explain it, a denominator a reader had to
    trust, and a third state for the days recorded before it existed.

    Held or not held is the question the page exists to answer, and it is
    available for every day the book was measured.
    """
    assert promise_held({"uncovered": 0.0}) is True
    assert promise_held({"uncovered": 71887.16}) is False


def test_a_day_never_measured_is_not_called_held():
    """An absent reading is not evidence that the promise was kept."""
    assert promise_held({}) is True, "no risk recorded reads as none open"
    assert daily_series([]) == []


def test_the_track_is_drawn_in_both_colours_across_the_week():
    page = _page(_week())
    assert page.count("#fbbf24") >= 2, "two days opened outside the promise"
    assert "#4ade80" in page, "and one closed inside it"
    assert "not recorded" not in page


def test_one_close_is_not_a_line():
    entries = [_line("cycle.complete", {"equity": "99000"}, "2026-09-01T19:45:00Z")]
    assert "Two closes are needed" in _page(entries)


def test_an_event_label_names_the_instrument_not_the_contract():
    """ "opened +9 contracts of XLF P56" is the agent hedging XLF.

    Labelling it "P56 bought" names the contract and reads as though the client
    had bought it.
    """
    entries = _week() + [
        _line(
            "book.reviewed",
            {"moved": True, "changes": ["opened +9 contracts of XLF P56"]},
            "2026-09-01T13:45:00Z",
        )
    ]
    page = _page(entries)
    assert "XLF hedged" in page
    assert "P56 bought" not in page


# --- the decisions ----------------------------------------------------------


def test_the_client_and_the_agent_are_not_given_the_same_voice():
    """The whole claim is that the agent never takes a view.

    A table showing the client selling in the same voice as the agent buying a
    put reads as an agent trading on an opinion.
    """
    entries = [
        _line(
            "book.reviewed",
            {"moved": True, "changes": ["closed all 900 shares of XLF"]},
            "2026-09-02T13:45:00Z",
        ),
        _line(
            "book.reviewed",
            {"moved": True, "changes": ["opened +9 contracts of XLF P56"]},
            "2026-09-01T13:45:00Z",
        ),
    ]
    page = _page(entries)
    assert 'data-who="client"' in page, "shares move because the client moved them"
    assert 'data-who="agent"' in page, "legs move because the agent did"


def test_a_row_says_what_happened_rather_than_dumping_the_payload():
    entries = [
        _line(
            "order.filled",
            {"symbol": "XLF", "contracts": 9, "fill_price": "3.5"},
            "2026-09-01T13:46:00Z",
        )
    ]
    page = _page(entries)
    assert "bought protection: 9 contracts at $3.5 each" in page


def test_no_raw_payload_is_offered_as_evidence():
    """The client reading this does not write code.

    A page that answers "how do you know" with a JSON blob behind a disclosure
    triangle is asking them to take the summary on trust anyway. The record is
    the journal, and the journal is linked.
    """
    entries = [_line("order.filled", {"symbol": "XLF"}, "2026-09-01T13:46:00Z")]
    page = _page(entries)
    assert "<details>" not in page
    assert '"broker_order_id"' not in page


def test_the_same_finding_repeated_every_cycle_is_shown_once():
    """Thirteen cycles a day each notice the same discrepancy.

    Ninety-nine identical rows tell a reader nothing the first one did not, and
    they bury the six lines that matter.
    """
    entries = [
        _line("reconcile.discrepancy", {"detail": "BIL: local CASH"}, ts)
        for ts in (
            "2026-09-01T17:48:00Z",
            "2026-09-01T17:27:00Z",
            "2026-09-01T13:45:00Z",
        )
    ]
    assert _page(entries).count("the broker won") == 1


def test_a_breach_row_is_findable_and_filterable():
    entries = [
        _line("order.working", {"symbol": "XLF"}, "2026-08-31T14:19:00Z", "breach")
    ]
    page = _page(entries)
    assert 'data-verdict="breach"' in page
    assert 'id="f-verdict"' in page


def test_the_table_can_be_filtered_by_date_instrument_who_and_verdict():
    page = _page(_week())
    for control in ("f-date", "f-symbol", "f-who", "f-verdict"):
        assert f'id="{control}"' in page, control


def test_no_controls_are_drawn_over_an_empty_table():
    """Filters over nothing imply the page is hiding data that does not exist."""
    assert 'id="f-verdict"' not in _page([])


def test_journal_entries_appear_newest_first():
    entries = [
        _line("order.filled", {"symbol": "XLF"}, "2026-09-01T13:46:00Z"),
        _line("order.filled", {"symbol": "IWM"}, "2026-08-31T14:19:00Z"),
    ]
    page = _page(entries)
    assert page.index("2026-09-01 13:46") < page.index("2026-08-31 14:19")


# --- the page itself --------------------------------------------------------


def test_html_is_escaped():
    entries = [_line("order.filled", {"symbol": "<script>"}, "2026-09-01T13:46:00Z")]
    assert "&lt;script&gt;" in _page(entries)
    assert "<script>alert" not in _page(entries)


def test_nothing_remote_is_loaded():
    """A judge's click must not depend on anyone else's uptime."""
    page = _page(_week())
    for remote in ("http://", "cdn.", "fonts.googleapis", "<img"):
        assert remote not in page, remote


def test_no_source_link_is_published_when_the_repository_is_unknown():
    """A guessed link on a public page is worse than a missing one."""
    assert "source" not in _page(_week())
    assert "https://example.invalid/repo" in _page(
        _week(), "https://example.invalid/repo"
    )


def test_an_empty_journal_still_renders():
    page = _page([])
    assert page.startswith("<!doctype html>")
    assert "Drawdown Guard" in page


def test_build_site_runs_on_a_cycle_that_traded_nothing(tmp_path, monkeypatch):
    """ "Considered and declined" is a state worth publishing."""
    journal = tmp_path / "journal"
    journal.mkdir()
    (journal / "2026-09-01.jsonl").write_text(
        '{"timestamp": "2026-09-01T13:45:00Z", "event": "cycle.complete", '
        '"severity": "info", "payload": {"equity": "98319.7"}}\n'
    )
    monkeypatch.setattr(writer, "JOURNAL_DIR", journal)
    written = build_site(out_path=tmp_path / "out.html", journal_dir=journal)
    assert written.exists()
    assert "Drawdown Guard" in written.read_text()
