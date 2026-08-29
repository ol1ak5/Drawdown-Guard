"""What moved since yesterday, and the three ways that question goes wrong.

The diff is the input a language model is handed, so it has to be right before
anything downstream of it is worth reading. The cases that matter are not the
happy one: a first cycle mistaken for a quiet day, and a corrupt snapshot read
as an empty book, would each report a day of trades that never happened.
"""

import json
from decimal import Decimal

from drawdownguard.risk.book import Book
from drawdownguard.risk.changes import compare, load, save, snapshot
from drawdownguard.risk.stress import Holding, OptionLeg


def book(shares: int = 900, legs: int = 0) -> Book:
    return Book(
        holdings=[
            Holding(symbol="XLF", shares=shares, price=58.0),
            Holding(symbol="CASH", shares=5000, price=1.0, shocked=False),
        ],
        legs=[
            OptionLeg(
                symbol="XLF",
                right="P",
                strike=Decimal("54"),
                contracts=legs,
                premium=Decimal("1.5"),
                spot=58.0,
            )
        ]
        if legs
        else [],
    )


def test_cash_is_not_a_holding_that_can_change():
    """Cash moves every time anything is bought and means nothing on its own."""
    assert "CASH" not in snapshot(book())


def test_legs_key_on_strike_so_a_roll_is_visible():
    counts = snapshot(book(legs=9))
    assert counts["XLF P54"] == 9


def test_a_sale_reads_as_a_closed_position():
    diff = compare(snapshot(book(900)), snapshot(book(0)))
    assert diff.moved
    assert "closed all 900 shares of XLF" in diff.describe()


def test_an_unchanged_book_is_not_a_change():
    diff = compare(snapshot(book()), snapshot(book()))
    assert not diff.moved
    assert diff.describe() == "nothing moved"


def test_first_cycle_is_not_a_quiet_day():
    """The distinction the whole module exists for.

    Treating a missing snapshot as an empty book would report the client as
    having bought their entire portfolio this morning.
    """
    diff = compare(None, snapshot(book()))
    assert diff.first
    assert not diff.moved
    assert "nothing to compare" in diff.describe()


def test_missing_file_reads_as_no_snapshot(tmp_path):
    assert load(tmp_path / "absent.json") is None


def test_corrupt_snapshot_reads_as_no_snapshot(tmp_path):
    """Not as an empty one. See the module docstring."""
    path = tmp_path / "holdings.json"
    path.write_text("{ this is not json")
    assert load(path) is None


def test_snapshot_round_trips(tmp_path):
    path = tmp_path / "holdings.json"
    counts = snapshot(book(legs=9))
    save(counts, path)
    assert load(path) == counts
    assert json.loads(path.read_text())["XLF"] == 900
