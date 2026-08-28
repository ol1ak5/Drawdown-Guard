from datetime import datetime

from drawdownguard.journal import writer
from drawdownguard.journal.site import build_site, entry_from_journal, render_site


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


def _stress(**overrides):
    """A `mandate.stress` line, which is what the promise and floor read.

    The page renders those two sections only when a cycle has measured the
    book, so a test about the controls has to hand it one -- otherwise it is
    asserting about a page that legitimately has no controls on it.
    """
    payload = {
        "mandate": "balanced",
        "downside_budget_pct": 10.0,
        "budget": 100000.0,
        "equity_exposure": 600000.0,
        "uncovered_risk": 20000.0,
        "ladder": [
            {"shock": -0.05, "loss": -30000.0, "from_options": 0, "shortfall": 0.0},
            {"shock": -0.10, "loss": -60000.0, "from_options": 0, "shortfall": 0.0},
            {
                "shock": -0.20, "loss": -120000.0,
                "from_options": 0, "shortfall": 20000.0,
            },
            {
                "shock": -0.35, "loss": -210000.0,
                "from_options": 0, "shortfall": 110000.0,
            },
        ],
    }
    payload.update(overrides)
    return {
        "ts": "2026-08-25T14:01:00Z",
        "symbol": "",
        "action": "mandate.stress",
        "regime": "",
        "verdict": "rejected",
        "detail": "",
        "event": "mandate.stress",
        "payload": payload,
    }


def _plan(**overrides):
    """A `protection.plan` line: one sleeve, hedged on its own underlying."""
    payload = {
        "mandate": "balanced",
        "uncovered_risk": 20287.0,
        "total_premium": 8272.0,
        "sleeves": [
            {
                "symbol": "SPY",
                "spot": 770.13,
                "exposure": 301891.0,
                "budget": 50252.0,
                "chosen": "protective_put",
                "because": "bought outright",
                "offers": [
                    {
                        "kind": "protective_put",
                        "detail": "buy 4x SPY 670 put at 20.68",
                        "premium_cost": 8272.0,
                        "forgone_upside": 0.0,
                        "protection_iv": 0.228,
                        "uncovered_after": 0.0,
                        "covers_the_risk": True,
                    }
                ],
            }
        ],
    }
    payload.update(overrides)
    return {
        "ts": "2026-08-25T14:02:00Z",
        "symbol": "",
        "action": "protection.plan",
        "regime": "",
        "verdict": "rejected",
        "detail": "",
        "event": "protection.plan",
        "payload": payload,
    }


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


def test_the_book_is_read_from_the_journal_not_from_a_snapshot():
    """The page believes the record the agent actually writes.

    It used to render a state snapshot exported by the strategy this project
    no longer runs. Nothing has written that file since, so the published page
    reported "no position has opened yet" for an account holding 800,000 of
    equity -- wrong, and confident about it, which a reader cannot detect.
    """
    html = render_site([_plan(), _stress()], [], datetime(2026, 8, 25, 14, 5))
    assert "SPY" in html
    assert "buy 4x SPY 670 put" in html
    assert "$301,891" in html   # the sleeve's exposure
    assert "$50,252" in html    # its share of the budget


def test_html_is_escaped():
    html = render_site(
        [_entry(detail="<script>alert(1)</script>")], [], datetime(2026, 8, 25, 14, 5)
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_an_empty_journal_still_renders():
    html = render_site([], [], datetime(2026, 8, 25, 14, 5))
    assert "No cycles recorded yet" in html


def test_a_sleeve_that_needed_nothing_says_so_rather_than_showing_a_blank():
    """Needing no protection is an outcome, not missing data. A blank cell
    reads as a number the page failed to fetch."""
    quiet = _plan()
    quiet["payload"]["sleeves"][0]["chosen"] = None
    html = render_site([quiet, _stress()], [], datetime(2026, 8, 25, 14, 5))
    assert "nothing needed" in html


def test_the_page_fetches_nothing_from_anywhere():
    """A judge's click must not depend on someone else's uptime.

    This is about requests, not about JavaScript. An earlier version of this
    test also banned `<script`, which conflated two unrelated things and cost
    the page its interactivity for no safety gained: an inline script makes no
    request. What must stay true is that the document loads nothing remote —
    which is also what keeps a public page incapable of reaching the broker.
    """
    html = render_site([_entry()], [], datetime(2026, 8, 25, 14, 5))
    for forbidden in ("http://", "//cdn", "@import", "src=", "fetch(", "XMLHttp"):
        assert forbidden not in html


def test_the_decision_log_can_be_filtered():
    """ "Interactive evaluation" means a judge does something, not that a page loads."""
    html = render_site([_entry()], [], datetime(2026, 8, 25, 14, 5))
    assert 'id="f-symbol"' in html
    assert 'id="f-verdict"' in html
    assert "<script>" in html


def test_rows_carry_the_attributes_the_filter_needs():
    html = render_site(
        [_entry(symbol="QQQ", verdict="rejected")], [], datetime(2026, 8, 25, 14, 5)
    )
    assert 'data-symbol="QQQ"' in html
    assert 'data-verdict="rejected"' in html


def test_an_empty_journal_renders_no_filter_to_operate_on():
    """Controls over an empty table are furniture, and imply data that is absent."""
    html = render_site([], [], datetime(2026, 8, 25, 14, 5))
    assert 'id="f-symbol"' not in html


def test_the_full_decision_record_is_available_on_the_row():
    """The raw journal payload, so a judge can check the summary against it."""
    html = render_site(
        [_entry(full='{"assignment_prob": 0.41}')], [], datetime(2026, 8, 25, 14, 5)
    )
    assert "<details" in html
    assert "assignment_prob" in html


def test_a_journal_line_becomes_a_row():
    """The journal's own shape is not the page's shape; this is the seam."""
    line = {
        "timestamp": "2026-08-25T14:02:11+00:00",
        "drawdownguard_env": "dev",
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
        "drawdownguard_env": "dev",
        "event": "order_placed",
        "severity": "info",
        "payload": {"symbol": "SPY", "detail": "SPY260918P00620000 x1 @ 4.20"},
    }
    assert entry_from_journal(line)["verdict"] == "approved"


def test_a_defect_line_is_not_flattened_into_a_rejection():
    """`defect` means the middleware fired: a leak, not the gate working."""
    line = {
        "timestamp": "2026-08-25T14:02:11+00:00",
        "drawdownguard_env": "dev",
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
    snapshot = tmp_path / "positions.json"

    out = build_site(tmp_path / "index.html", tmp_path / "journal", snapshot)

    assert out.exists()
    page = out.read_text()
    assert "SPY260918P00620000 x1" in page


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
        repository_url="https://github.com/example/drawdown-guard-agent",
    )
    assert 'href="https://github.com/example/drawdown-guard-agent"' in html


def test_the_journal_payload_becomes_the_expandable_record():
    line = {
        "timestamp": "2026-08-25T14:02:11+00:00",
        "drawdownguard_env": "dev",
        "event": "order_rejected",
        "severity": "veto",
        "payload": {"symbol": "SPY", "reason": "net delta band", "net_delta": 168},
    }
    row = entry_from_journal(line)
    assert "168" in row["full"]
    assert "net_delta" in row["full"]


def test_every_element_the_script_looks_up_exists_in_the_page():
    """A typo in an id does not raise. The script simply does nothing forever.

    The guard clause that keeps an empty journal from throwing would also
    swallow a renamed control, so nothing at runtime would ever complain. This
    ties the two halves together at build time instead.
    """
    import re

    from drawdownguard.journal.site import _SCRIPT

    wanted = set(re.findall(r"getElementById\('([^']+)'\)", _SCRIPT))
    assert wanted, "the script looks nothing up; this test has gone stale"

    html = render_site([_entry(), _stress()], [], datetime(2026, 8, 25, 14, 5))
    for element_id in wanted:
        assert f'id="{element_id}"' in html, element_id


def test_the_script_selector_matches_the_rendered_rows():
    """The filter selects on data-symbol; the rows must actually carry it."""
    import re

    from drawdownguard.journal.site import _SCRIPT

    assert "tr[data-symbol]" in _SCRIPT
    html = render_site([_entry()], [], datetime(2026, 8, 25, 14, 5))
    assert re.search(r"<tr[^>]*data-symbol=", html)


# --- the promise and the floor ----------------------------------------------


def test_the_promise_is_stated_in_the_client_s_own_terms():
    """Percent and dollars both. "10%" is what the client said; "$100,000" is
    what it costs them, and only the second is a number they can weigh."""
    html = render_site([_stress()], [], datetime(2026, 8, 25, 14, 5))
    assert "10.0%" in html
    assert "$100,000" in html
    assert "$600,000" in html


def test_a_broken_promise_is_labelled_as_broken():
    """Named as risk, not as a shortfall.

    The figure is `worst_case - budget`, so on a bare equity book it is most of
    the portfolio -- 20,000 here and 72,000 on the live account. "Short by"
    reads as money that has to be found, and it is closed by a few thousand of
    premium. What it measures is risk nobody has agreed to carry."""
    html = render_site([_stress()], [], datetime(2026, 8, 25, 14, 5))
    assert "$20,000 of risk not covered" in html


def test_a_promise_that_holds_says_so_rather_than_showing_a_zero():
    """Zero dollars of shortfall is a number. "The promise holds" is the
    sentence a client is owed, and a page that only prints figures makes them
    do the interpreting."""
    intact = _stress(uncovered_risk=0.0)
    html = render_site([intact], [], datetime(2026, 8, 25, 14, 5))
    assert "the promise holds" in html


def test_the_measured_rungs_are_handed_to_the_browser_not_a_model_of_them():
    """The slider interpolates between the rungs the agent actually recorded.

    Straight lines between measured points are exact here, not a fit: the
    payoff bends only at a strike. A page that recomputed the ladder in
    JavaScript could disagree with the journal, and then neither would be
    evidence.
    """
    html = render_site([_stress()], [], datetime(2026, 8, 25, 14, 5))
    assert 'data-rungs=' in html
    assert '"shock": -0.2' in html
    assert '"loss": 120000.0' in html  # positive, the way a reader says it


def test_an_explanation_is_shown_when_the_model_wrote_one():
    note = {
        "ts": "2026-08-25T14:03:00Z",
        "symbol": "",
        "action": "protection.explained",
        "regime": "",
        "verdict": "approved",
        "detail": "",
        "event": "protection.explained",
        "payload": {"chosen": "protective_put", "note": "We bought eight puts."},
    }
    html = render_site([note, _stress()], [], datetime(2026, 8, 25, 14, 5))
    assert "We bought eight puts." in html


def test_a_missing_explanation_is_an_empty_field_not_an_invented_one():
    """There is no fallback sentence anywhere, and the page must not add one.

    A reader cannot tell generated filler from an explanation, so the honest
    thing is to say none was written.
    """
    html = render_site([_stress()], [], datetime(2026, 8, 25, 14, 5))
    assert "No note was written" in html


def test_a_page_built_before_any_cycle_has_run_says_so():
    html = render_site([], [], datetime(2026, 8, 25, 14, 5))
    assert "No cycle has measured the promise yet." in html
    assert "No ladder has been measured yet." in html


def test_a_breach_is_not_rendered_as_an_approval():
    """`breach` is the one severity that means the client's promise broke.

    The map held only `veto` and `defect`, and everything else fell through to
    the default -- so the stress ladder reporting a broken promise wore the
    same badge as a routine fill, on the page built to surface exactly that.
    """
    line = {
        "timestamp": "2026-08-25T14:02:11+00:00",
        "drawdownguard_env": "dev",
        "event": "mandate.stress",
        "severity": "breach",
        "payload": {"symbol": "SPY", "uncovered_risk": 20362.56},
    }
    assert entry_from_journal(line)["verdict"] == "breach"


def test_a_breach_row_is_findable_and_filterable():
    html = render_site(
        [_entry(verdict="breach", action="mandate.stress")],
        [],
        datetime(2026, 8, 25, 14, 5),
    )
    assert 'class="breach"' in html
    assert 'data-verdict="breach"' in html
    assert '<option value="breach">' in html


def test_a_still_market_costs_nothing_on_the_floor_slider():
    """The slider starts at 0% and the measured ladder starts at -5%.

    Anything milder than the shallowest rung used to return that rung's loss,
    so dragging to "if the market falls 0%" reported the loss at -5% -- a book
    that had not moved, shown as down thirty thousand dollars.
    """
    import json as _json
    import re
    import shutil
    import subprocess

    import pytest

    if not shutil.which("node"):
        pytest.skip("node is needed to run the page's own script")

    html = render_site([_stress()], [], datetime(2026, 8, 25, 14, 5))
    found = re.search(r"function lossAt\(shock\) \{.*?\n  \}", html, re.S)
    assert found, "lossAt is no longer in the page under that name"

    harness = (
        "var rungs=[{shock:-0.05,loss:30000},{shock:-0.10,loss:60000},"
        "{shock:-0.20,loss:120000}];\n"
        + found.group(0)
        + "\nconsole.log(JSON.stringify("
        "[lossAt(0),lossAt(-0.025),lossAt(-0.05),lossAt(-0.15)]));"
    )
    out = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, check=True
    )
    flat, halfway, measured, between = _json.loads(out.stdout)

    assert flat == 0
    assert halfway == 15000  # straight line from flat to the first rung
    assert measured == 30000  # the measured rung itself is untouched
    assert between == 90000  # and so is every segment between two rungs
