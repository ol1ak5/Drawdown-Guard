"""The page a judge and a client both open.

It answers one question in the first screen -- is the client inside the number
they were given -- and everything below is the working. So these tests are
mostly about two properties: that the answer is the one the journal recorded,
and that nothing on the page was computed by the page.
"""

from datetime import UTC, datetime

import pytest

from drawdownguard.journal import writer
from drawdownguard.journal.site import (
    build_site,
    daily_series,
    entry_from_journal,
    hedged_share,
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
        "worst_case": 2676.0,
        # What the account has already given up since the promise opened,
        # mostly the premium. Part of the total the limit is measured against.
        "already_lost": 1777.23,
        "premium_paid": 4656.0,
        "remaining_budget": 5341.84,
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
    assert "$2,666" in page, "9,998 less 4,656 of premium and 2,676 ahead"
    assert "unused" in page


def test_the_header_takes_the_limit_apart_rather_than_restating_it():
    """4,656 of premium, 2,676 still ahead, 2,666 left, all inside one
    sentence that names the 9,998 they add up to.

    Side by side as three labelled figures they gave no hint that they add up
    to anything, and the labels had to carry the explanation alone -- "worst
    case from here" does not tell a reader it means the distance from today's
    price down to the strike.
    """
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "$4,656" in page, "what the protection cost"
    assert "$2,676" in page and "$2,666" in page
    assert "this client allowed" in page, "the arithmetic is the grammar"


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
    assert "$2,676" in page, "the worst it can still do from here"
    assert "puts take over" in page
    assert "TODAY" in page.upper(), "the label says what the number measures"


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


def test_numeric_columns_are_centred():
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "td.n, th.n { text-align: center;" in page
    assert "text-align: right; font-variant-numeric" not in page


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


def test_every_exposed_holding_hedged_is_a_hundred_per_cent():
    """The plain question: are the options bought on everything that can fall?

    Weighted by value, not counted by symbol -- a book 55% in XLF and 31% in
    IWM is not half hedged when the smaller of the two is covered.
    """
    day = {
        "holdings": [
            {"symbol": "XLF", "shares": 900, "value": 51480.0, "shocked": True},
            {"symbol": "IWM", "shares": 100, "value": 29050.0, "shocked": True},
        ],
        "legs": [
            {"symbol": "XLF", "right": "P", "contracts": 9},
            {"symbol": "IWM", "right": "P", "contracts": 1},
        ],
    }
    assert hedged_share(day) == 1.0


def test_hedging_the_smaller_holding_is_not_half_the_job():
    day = {
        "holdings": [
            {"symbol": "XLF", "shares": 900, "value": 51480.0, "shocked": True},
            {"symbol": "IWM", "shares": 100, "value": 29050.0, "shocked": True},
        ],
        "legs": [{"symbol": "IWM", "right": "P", "contracts": 1}],
    }
    assert hedged_share(day) == pytest.approx(29050 / 80530)


def test_bills_and_cash_are_not_counted_as_things_to_hedge():
    """They do not move with an equity shock, so leaving them in the
    denominator would make a fully hedged book read as partly hedged for
    holding some bills."""
    day = {
        "holdings": [
            {"symbol": "XLF", "shares": 900, "value": 51480.0, "shocked": True},
            {"symbol": "BIL", "shares": 100, "value": 9140.0, "shocked": False},
            {"symbol": "CASH", "shares": 4018, "value": 4018.0, "shocked": False},
        ],
        "legs": [{"symbol": "XLF", "right": "P", "contracts": 9}],
    }
    assert hedged_share(day) == 1.0


def test_a_day_whose_book_was_not_recorded_has_no_share():
    """Different from nothing being hedged."""
    assert hedged_share({}) is None
    assert promise_held({"uncovered": 0.0}) is True


def test_one_close_is_not_a_line():
    entries = [_line("cycle.complete", {"equity": "99000"}, "2026-09-01T19:45:00Z")]
    assert "Two closes are needed" in _page(entries)


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


def test_the_track_says_how_much_of_the_book_is_hedged():
    """A row per holding, not one bar for the book.

    A single track could say how much of the portfolio was covered and never
    which part of it, and "64% hedged" is the same number whether the
    uncovered third is the client's largest position or their smallest.
    """
    page = _page(_week())
    assert "100% hedged" in page, "XLF carries a put for all 900 shares"
    assert ">XLF<" in page and ">IWM<" in page, "a row per holding"


def test_a_day_the_client_did_not_hold_something_is_blank_not_zero():
    """An unheld position is not an unprotected one.

    The row for a symbol the client had not bought yet, or had already sold,
    carries nothing at all rather than an empty bar reading as no cover.
    """
    from drawdownguard.journal.site import per_symbol_cover

    sold = {"holdings": [], "legs": []}
    assert per_symbol_cover(sold) == {}
    assert per_symbol_cover({}) is None


def test_the_header_says_which_cycle_it_is_reporting():
    """Every figure moves with the market, and the same arithmetic an hour
    earlier gives a different answer -- which is how a reader comparing the
    page against a worked example concludes that one of them is wrong.

    Prices are not listed here -- they used to be, and it read as arbitrary:
    one symbol shown out of several, no explanation of why that one. The
    timestamp alone is enough to reproduce a figure by reading the journal
    at that cycle.
    """
    page = _page([_line("mandate.stress", _stress(), "2026-09-01T17:48:00Z")])
    assert "Measured on the 2026-09-01 17:48 UTC cycle" in page
    assert "with XLF at" not in page


def test_a_holding_is_covered_by_degree_not_by_yes_or_no():
    """A contract covers a hundred shares and nothing smaller.

    250 shares behind two puts is eighty percent covered, with fifty shares out
    in the open, and a page that said "hedged" would be describing a position
    that is not.
    """
    from drawdownguard.journal.site import per_symbol_cover

    day = {
        "holdings": [{"symbol": "SPY", "shares": 250, "value": 150000.0}],
        "legs": [{"symbol": "SPY", "right": "P", "contracts": 2}],
    }
    assert per_symbol_cover(day)["SPY"] == pytest.approx(0.8)


def test_more_contracts_than_shares_is_still_fully_covered_and_no_more():
    """Whole contracts round up, so a 64-share holding carries a put standing
    behind a hundred. That is not 156% of anything."""
    from drawdownguard.journal.site import per_symbol_cover

    day = {
        "holdings": [{"symbol": "SPY", "shares": 64, "value": 38000.0}],
        "legs": [{"symbol": "SPY", "right": "P", "contracts": 1}],
    }
    assert per_symbol_cover(day)["SPY"] == 1.0


def test_a_holding_with_no_put_is_uncovered_rather_than_absent():
    from drawdownguard.journal.site import per_symbol_cover

    day = {
        "holdings": [{"symbol": "SPY", "shares": 100, "value": 60000.0}],
        "legs": [],
    }
    assert per_symbol_cover(day) == {"SPY": 0.0}


def test_the_early_days_get_their_book_back_from_the_record():
    """`mandate.stress` only began carrying the book on 2026-09-01, so the days
    before it drew blank rows -- which hid the one thing they show: IWM was
    hedged on the 31st and XLF only on the 1st.

    Reconstructed from two facts the journal has always kept: the legs held on
    a date are the fills up to it, and the holdings are the earliest book it
    does carry, because `book.reviewed` says nothing moved on those days.
    """
    from drawdownguard.journal.site import backfill_books, per_symbol_cover

    entries = [
        _line("cycle.complete", {"equity": "98319.7"}, "2026-09-01T17:48:00Z"),
        _line("mandate.stress", _stress(), "2026-09-01T17:48:00Z"),
        _line(
            "order.filled",
            {"symbol": "IWM", "occ_symbol": "IWM270917P00275000", "contracts": 1},
            "2026-08-31T14:19:00Z",
        ),
        _line("cycle.complete", {"equity": "99183.49"}, "2026-08-31T14:19:00Z"),
        _line(
            "mandate.stress",
            _stress(holdings=None, legs=None, uncovered_risk=71339.0),
            "2026-08-31T14:19:00Z",
            severity="breach",
        ),
    ]
    series = daily_series(entries)
    backfill_books(entries, series)

    monday = next(row for row in series if row["date"] == "2026-08-31")
    cover = per_symbol_cover(monday)
    assert cover["IWM"] == 1.0, "the put that filled that morning"
    assert cover["XLF"] == 0.0, "the one that did not"


def test_a_day_that_recorded_its_own_book_is_never_overwritten():
    from drawdownguard.journal.site import backfill_books

    entries = [
        _line("cycle.complete", {"equity": "98319.7"}, "2026-09-01T17:48:00Z"),
        _line("mandate.stress", _stress(), "2026-09-01T17:48:00Z"),
    ]
    series = daily_series(entries)
    backfill_books(entries, series)
    assert "reconstructed" not in series[0]


def test_the_agent_looking_and_finding_nothing_is_not_the_client_acting():
    """It read "client: nothing in the portfolio changed" -- a line about
    something the client did not do."""
    entries = [
        _line("book.reviewed", {"moved": False, "changes": []}, "2026-09-01T23:29:00Z")
    ]
    page = _page(entries)
    assert 'data-who="agent"' in page
    assert 'data-who="client"' not in page


def test_the_page_shows_the_book_the_client_owns_after_the_cycle_traded():
    """`mandate.stress` is measured before the agent acts, which is the whole
    argument of the cycle -- and it means the published book describes the
    account as it stood before the orders went.

    On 2026-09-02 the agent sold nine redundant XLF puts at 19:47 and the page
    went on showing them all evening, because the next measurement was not due
    until the following morning.
    """
    entries = [
        _line(
            "book.settled",
            {
                "holdings": [
                    {
                        "symbol": "IWM",
                        "shares": 100,
                        "price": 294.12,
                        "value": 29412.0,
                        "shocked": True,
                    }
                ],
                "legs": [
                    {
                        "symbol": "IWM",
                        "right": "P",
                        "strike": "275",
                        "contracts": 1,
                        "premium": "15.06",
                    }
                ],
                "premium_paid": 1506.0,
            },
            "2026-09-02T19:47:00Z",
        ),
        _line("cycle.complete", {"equity": "98693"}, "2026-09-02T19:42:00Z"),
        _line("mandate.stress", _stress(), "2026-09-02T19:42:00Z"),
    ]
    page = _page(entries)
    assert "$1,506" in page, "the premium left once the XLF puts were sold"
    assert "$4,656" not in page, "not the premium from before the sale"


def test_a_measurement_newer_than_the_settlement_still_wins():
    """Tomorrow's reading is not overridden by yesterday's settlement."""
    entries = [
        _line("cycle.complete", {"equity": "98693"}, "2026-09-03T13:45:00Z"),
        _line("mandate.stress", _stress(), "2026-09-03T13:45:00Z"),
        _line(
            "book.settled",
            {"holdings": [], "legs": [], "premium_paid": 1506.0},
            "2026-09-02T19:47:00Z",
        ),
    ]
    assert "$4,656" in _page(entries)


def test_a_hedge_bought_the_same_cycle_shows_covered_that_day():
    """On 2026-09-03 the agent bought AAPL and hedged it in one cycle.
    `mandate.stress` measures before the trade, so the coverage row built
    from it alone said AAPL was 0% covered on the day it was fully covered --
    `book.settled`, written after the fill, has to win.
    """
    entries = [
        _line(
            "book.settled",
            {
                "holdings": [
                    {"symbol": "AAPL", "shares": 100, "value": 32805.0, "shocked": True}
                ],
                "legs": [
                    {
                        "symbol": "AAPL",
                        "right": "P",
                        "strike": "310",
                        "contracts": 1,
                        "premium": "26.55",
                    }
                ],
                "premium_paid": 2655.0,
            },
            "2026-09-03T14:17:00Z",
        ),
        _line("cycle.complete", {"equity": "98902"}, "2026-09-03T14:16:00Z"),
        _line("mandate.stress", _stress(holdings=[], legs=[]), "2026-09-03T14:16:00Z"),
    ]
    from drawdownguard.journal.site import daily_series, per_symbol_cover

    series = daily_series(entries)
    row = next(r for r in series if r["date"] == "2026-09-03")
    assert per_symbol_cover(row)["AAPL"] == 1.0


def test_client_acted_reads_as_the_trade_it_was():
    """It fell through to the event's own name -- "client acted" -- which
    told a reader nothing the Who column had not already said."""
    entries = [
        _line(
            "client.acted",
            {"day": 5, "action": "buy_equity", "symbol": "AAPL", "shares": 100},
            "2026-09-03T13:53:00Z",
        )
    ]
    page = _page(entries)
    assert "bought 100 shares of AAPL" in page
    assert 'data-who="client"' in page
    assert "client acted" not in page
