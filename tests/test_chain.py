"""The chain adapter, against a recorded Alpaca response.

The fixture is a real capture, not a hand-written one, and it deliberately
keeps the contracts Alpaca returned without greeks or without a quote. A
fixture curated down to the well-formed rows would never exercise the paths
that decide a contract is not tradable, which is most of what this adapter does.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from drawdownguard.market.chain import adapt_chain_row

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "spy_chain.json").read_text()
)
SNAPSHOTS = FIXTURE["snapshots"]


def adapted():
    """Every row the adapter accepts, keyed by symbol."""
    out = {}
    for occ, snap in SNAPSHOTS.items():
        row = adapt_chain_row(occ, snap, snap.get("_open_interest", 0))
        if row is not None:
            out[occ] = row
    return out


def test_the_fixture_is_a_real_capture_with_something_to_adapt():
    assert FIXTURE["underlying"] == "SPY"
    assert len(SNAPSHOTS) >= 20
    assert len(adapted()) >= 1


def test_every_adapted_row_has_the_keys_the_optimizer_needs():
    required = {
        "occ_symbol",
        "strike",
        "expiry",
        "bid",
        "ask",
        "open_interest",
        "implied_vol",
    }
    rows = adapted()
    assert rows
    for row in rows.values():
        assert required <= set(row.keys())


def test_prices_are_decimal_and_the_expiry_is_a_date():
    row = next(iter(adapted().values()))
    assert isinstance(row["bid"], Decimal)
    assert isinstance(row["ask"], Decimal)
    assert isinstance(row["strike"], Decimal)
    assert isinstance(row["expiry"], date)


def test_implied_volatility_is_a_plausible_fraction_not_a_percentage():
    """The single most likely integration bug in this project.

    A vendor returning 18.5 where the code expects 0.185 would make every
    downstream number wrong by a hundred, and the optimizer would still return
    an answer that looked entirely reasonable.
    """
    for row in adapted().values():
        assert 0.01 < row["implied_vol"] < 3.0


def test_a_contract_with_no_quote_is_not_offered():
    """Not tradable is a different claim from priced at zero.

    A row with a zero bid would reach the optimizer as a contract that pays
    nothing. A contract nobody is quoting is not a worse choice, it is not a
    choice, and the two must not arrive in the same shape.
    """
    assert adapt_chain_row("SPY260828P00615000", {"latestQuote": {}}, 100) is None
    assert (
        adapt_chain_row(
            "SPY260828P00615000",
            {"latestQuote": {"bp": 0, "ap": 0}, "impliedVolatility": 0.2},
            100,
        )
        is None
    )


def test_an_ordinary_ticker_is_not_mistaken_for_a_contract():
    assert adapt_chain_row("SPY", {"latestQuote": {"bp": 1, "ap": 2}}, 100) is None


def test_the_strike_and_expiry_come_from_the_symbol_itself():
    """Decoded, not trusted from a parallel field that could disagree."""
    row = adapt_chain_row(
        "SPY260918P00700000",
        {"latestQuote": {"bp": 1.0, "ap": 1.1}, "impliedVolatility": 0.22},
        1234,
    )
    assert row["strike"] == Decimal("700")
    assert row["expiry"] == date(2026, 9, 18)
    assert row["right"] == "P"
    assert row["open_interest"] == 1234


def test_open_interest_defaults_closed_rather_than_open():
    """An unknown open interest must not read as a healthy market.

    Contracts missing from the open-interest response arrive as zero, which the
    gate refuses. Defaulting the other way would let a contract nobody could
    measure pass the liquidity filter.
    """
    row = adapt_chain_row(
        "SPY260918P00700000",
        {"latestQuote": {"bp": 1.0, "ap": 1.1}, "impliedVolatility": 0.22},
        0,
    )
    assert row["open_interest"] == 0
