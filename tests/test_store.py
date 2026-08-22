from datetime import date
from decimal import Decimal

import pytest

from flywheel import store
from flywheel.domain import OpenContract, WheelState


@pytest.fixture
def db(tmp_path):
    """A fresh database per test, so nothing leaks between them."""
    store.init_db(tmp_path / "flywheel.db")
    return tmp_path


def wheel_holding_shares() -> WheelState:
    """A wheel mid-cycle: assigned shares, a covered call open against them.

    Deliberately the most awkward state to serialise — it carries a Decimal
    basis with a trailing zero, a nested model, and a date.
    """
    return WheelState(
        symbol="SPY",
        leg="CALL_OPEN",
        shares=100,
        basis=Decimal("472.70"),
        contracts=[
            OpenContract(
                occ_symbol="SPY260918C00480000",
                right="C",
                strike=Decimal("480"),
                expiry=date(2026, 9, 18),
                contracts=-1,
                premium=Decimal("3.15"),
            )
        ],
        premium_collected=Decimal("7.30"),
        cycle_count=3,
    )


def test_a_wheel_survives_a_round_trip(db):
    original = wheel_holding_shares()
    store.save_wheel(original)
    assert store.load_wheel("SPY") == original


def test_an_unknown_symbol_starts_in_cash(db):
    fresh = store.load_wheel("QQQ")
    assert fresh.leg == "CASH"
    assert fresh.symbol == "QQQ"
    assert fresh.contracts == []


def test_saving_the_same_symbol_twice_updates_rather_than_duplicates(db):
    store.save_wheel(WheelState(symbol="SPY", leg="CASH"))
    store.save_wheel(WheelState(symbol="SPY", leg="PUT_OPEN"))
    assert store.load_wheel("SPY").leg == "PUT_OPEN"
    assert list(store.load_all()) == ["SPY"]


def test_load_all_returns_every_saved_wheel(db):
    store.save_wheel(WheelState(symbol="SPY", leg="PUT_OPEN"))
    store.save_wheel(WheelState(symbol="IWM", leg="SHARES", shares=100))
    assert set(store.load_all()) == {"SPY", "IWM"}


def test_a_snapshot_restores_state_into_a_fresh_database(db, tmp_path):
    """The ephemeral-runner case, and the one that silently corrupts the strategy.

    Every scheduled run starts on a new machine with only what git carries. If
    the snapshot does not reproduce basis, premium and cycle count exactly, the
    agent wakes up each morning believing it has never traded — and the numbers
    the whole narrative rests on quietly reset to zero.
    """
    original = wheel_holding_shares()
    store.save_wheel(original)
    snapshot = tmp_path / "state" / "wheels.json"
    store.export_snapshot(snapshot)

    store.init_db(tmp_path / "second.db")
    assert store.load_wheel("SPY").leg == "CASH"  # genuinely empty first

    store.import_snapshot(snapshot)
    assert store.load_wheel("SPY") == original


def test_the_snapshot_preserves_decimal_precision(db, tmp_path):
    """472.70 must not come back as 472.7, and must never become a float.

    Money is Decimal everywhere in this project. A JSON round trip through
    float would be invisible for one cycle and wrong by cents after twenty.
    """
    store.save_wheel(WheelState(symbol="SPY", basis=Decimal("472.70")))
    snapshot = tmp_path / "state" / "wheels.json"
    store.export_snapshot(snapshot)
    store.init_db(tmp_path / "second.db")
    store.import_snapshot(snapshot)

    restored = store.load_wheel("SPY").basis
    assert isinstance(restored, Decimal)
    assert str(restored) == "472.70"


def test_the_snapshot_is_written_in_a_stable_order(db, tmp_path):
    """The snapshot is committed after every cycle, so its diff must be honest.

    Dictionary iteration order would reshuffle untouched symbols and bury the
    one line that actually changed.
    """
    store.save_wheel(WheelState(symbol="SPY"))
    store.save_wheel(WheelState(symbol="IWM"))
    store.save_wheel(WheelState(symbol="QQQ"))
    snapshot = tmp_path / "state" / "wheels.json"
    store.export_snapshot(snapshot)

    text = snapshot.read_text()
    assert text.index('"IWM"') < text.index('"QQQ"') < text.index('"SPY"')


def test_init_db_seeds_itself_from_a_snapshot_when_empty(db, tmp_path):
    """What actually happens at the top of a cycle on a fresh runner."""
    store.save_wheel(wheel_holding_shares())
    snapshot = tmp_path / "state" / "wheels.json"
    store.export_snapshot(snapshot)

    store.init_db(tmp_path / "third.db", snapshot=snapshot)
    assert store.load_wheel("SPY") == wheel_holding_shares()


def test_init_db_does_not_overwrite_a_populated_database(db, tmp_path):
    """A stale snapshot must never clobber fresher local state mid-run."""
    snapshot = tmp_path / "state" / "wheels.json"
    store.save_wheel(WheelState(symbol="SPY", cycle_count=1))
    store.export_snapshot(snapshot)
    store.save_wheel(WheelState(symbol="SPY", cycle_count=9))

    store.init_db(tmp_path / "flywheel.db", snapshot=snapshot)
    assert store.load_wheel("SPY").cycle_count == 9


def test_exporting_an_empty_store_writes_an_empty_snapshot(db, tmp_path):
    snapshot = tmp_path / "state" / "wheels.json"
    store.export_snapshot(snapshot)
    assert snapshot.exists()
    assert store.load_all() == {}


def test_importing_a_missing_snapshot_is_not_an_error(db, tmp_path):
    """The first run ever has no snapshot. That is not a failure."""
    store.import_snapshot(tmp_path / "state" / "absent.json")
    assert store.load_all() == {}
