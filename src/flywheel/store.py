"""Where the wheel's bookkeeping lives between cycles.

Two layers, and the second one exists because of how the agent is deployed.

`data/flywheel.db` is a SQLite file: the working store for one run. It is
gitignored. `data/state/wheels.json` is a snapshot committed to the repository
after every cycle.

The duplication is not redundancy. Each scheduled run starts on a fresh GitHub
Actions machine holding only what git carries, so a database in an ignored
directory is recreated empty every morning. Basis, premium collected and cycle
count are exactly the numbers the broker does not track for us and the strategy
cannot be reconstructed without — losing them would not raise anything, it
would just reset the agent's memory of its own history to zero each day.

Reconciliation against the broker still runs first every cycle and still wins
on conflict. The snapshot restores the bookkeeping, not the positions.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from flywheel.domain import WheelState

DEFAULT_DB = Path("data/flywheel.db")
DEFAULT_SNAPSHOT = Path("data/state/wheels.json")

_db_path: Path = DEFAULT_DB


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(_db_path)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def init_db(
    path: str | Path = DEFAULT_DB, snapshot: str | Path = DEFAULT_SNAPSHOT
) -> None:
    """Open (creating if needed) the store, and seed it from the snapshot.

    Seeding happens only when the database is empty. A populated store is
    fresher than anything on disk from a previous run, and a stale snapshot
    overwriting it mid-cycle would undo work already done.
    """
    global _db_path
    _db_path = Path(path)
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS wheels ("
            "  symbol TEXT PRIMARY KEY,"
            "  state  TEXT NOT NULL"
            ")"
        )
    if not load_all():
        import_snapshot(snapshot)


def save_wheel(state: WheelState) -> None:
    """Persist one wheel, replacing any earlier version of it.

    The whole model goes into a single TEXT column as JSON. A column per field
    would buy nothing — nothing queries these by anything but symbol — and
    would cost a schema migration every time the domain model gains a field.
    """
    with _connect() as connection:
        connection.execute(
            "INSERT INTO wheels (symbol, state) VALUES (?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET state = excluded.state",
            (state.symbol, state.model_dump_json()),
        )


def load_wheel(symbol: str) -> WheelState:
    """The stored wheel, or a fresh `CASH` one when the symbol is unknown.

    Absence is not an error: every symbol starts here on the first cycle.
    """
    with _connect() as connection:
        row = connection.execute(
            "SELECT state FROM wheels WHERE symbol = ?", (symbol,)
        ).fetchone()
    if row is None:
        return WheelState(symbol=symbol)
    return WheelState.model_validate_json(row[0])


def load_all() -> dict[str, WheelState]:
    with _connect() as connection:
        rows = connection.execute("SELECT symbol, state FROM wheels").fetchall()
    return {symbol: WheelState.model_validate_json(state) for symbol, state in rows}


def export_snapshot(path: str | Path = DEFAULT_SNAPSHOT) -> None:
    """Write the committed JSON snapshot of every wheel.

    Keys are sorted and the file is indented because this lands in a git commit
    after every cycle. Unsorted output would reshuffle untouched symbols and
    bury the one line that actually changed under a diff of noise.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        symbol: json.loads(state.model_dump_json())
        for symbol, state in load_all().items()
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def import_snapshot(path: str | Path = DEFAULT_SNAPSHOT) -> None:
    """Load the committed snapshot into the store. A missing file is not an error.

    Decimals survive because pydantic serialises them as JSON strings and parses
    them back the same way. Going through float would be invisible for one cycle
    and wrong by cents after twenty.
    """
    source = Path(path)
    if not source.exists():
        return
    for state in json.loads(source.read_text()).values():
        save_wheel(WheelState.model_validate(state))
