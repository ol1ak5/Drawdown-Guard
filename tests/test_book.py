"""Broker positions into ladder inputs.

The arithmetic lives in `test_stress.py`. These are about the translation, and
translation is where the quiet errors are: a sign dropped, a bill counted as a
hedge, a leg silently omitted from a risk number that then reads as complete.
"""

from decimal import Decimal

import pytest

from drawdownguard.risk.book import CASH_LIKE, to_book
from drawdownguard.risk.stress import ladder, worst_shortfall


def share(symbol: str, qty: int, price: str) -> dict:
    return {
        "symbol": symbol,
        "qty": str(qty),
        "current_price": price,
        "avg_entry_price": price,
    }


def option(occ: str, qty: int, premium: str) -> dict:
    return {"symbol": occ, "qty": str(qty), "avg_entry_price": premium}


def test_shares_are_exposed_and_bills_are_not():
    book = to_book([share("SPY", 392, "765.13"), share("BIL", 2728, "91.62")])
    exposed = {h.symbol: h.shocked for h in book.holdings}
    assert exposed == {"SPY": True, "BIL": False}
    # And the difference shows up where it matters: only SPY moves.
    rungs = ladder(book.holdings, [], budget=1_000_000.0, shocks=(-0.10,))
    assert rungs[0].portfolio_loss == pytest.approx(-392 * 765.13 * 0.10)


def test_an_unknown_ticker_is_treated_as_fully_exposed():
    """The safe direction to be wrong in.

    A ticker nobody classified might be a bond fund, but assuming so would
    understate the loss. Assuming it is equity overstates it, and an overstated
    gap makes the agent buy protection it may not need — a cost, not an injury.
    """
    book = to_book([share("XYZQ", 100, "50")])
    assert book.holdings[0].shocked is True


def test_tlt_is_not_on_the_cash_like_list():
    """Pinned deliberately. In 2022 long Treasuries fell 31% alongside equities.

    A rule that classified by asset class rather than by duration would sweep
    TLT in here and book a third of its drawdown as ballast.
    """
    assert "TLT" not in CASH_LIKE
    assert to_book([share("TLT", 100, "90")]).holdings[0].shocked is True


def test_a_long_put_is_read_as_protection_and_a_short_put_as_exposure():
    """Same underlying, same strike, opposite sign — and opposite effect."""
    long_put = to_book(
        [share("SPY", 100, "765"), option("SPY270115P00700000", 1, "8.00")]
    )
    short_put = to_book(
        [share("SPY", 100, "765"), option("SPY270115P00700000", -1, "8.00")]
    )
    assert long_put.legs[0].contracts == 1
    assert short_put.legs[0].contracts == -1

    long_rung = ladder(long_put.holdings, long_put.legs, 100_000.0, shocks=(-0.20,))[0]
    short_rung = ladder(short_put.holdings, short_put.legs, 100_000.0, shocks=(-0.20,))[
        0
    ]
    assert long_rung.protected_by_options > 0
    assert short_rung.protected_by_options < 0
    assert long_rung.portfolio_loss > short_rung.portfolio_loss


def test_the_underlying_price_comes_from_the_shares_not_the_option():
    """An option position reports the price of the option.

    Shocking that number would shock the wrong thing: a 20% fall in a put's own
    price is not a 20% fall in SPY, and the resulting ladder would be nonsense
    that still rendered as a neat table.
    """
    book = to_book([share("SPY", 100, "765.13"), option("SPY270115P00700000", 1, "8")])
    assert book.legs[0].spot == pytest.approx(765.13)


def test_an_option_with_no_underlying_price_is_reported_not_dropped():
    """Silence here would be the dangerous outcome.

    A short put left out of the ladder makes the book look safer than it is.
    So the leg is named in `unpriced` and the ladder is marked incomplete.
    """
    book = to_book([option("QQQ270115P00600000", -3, "5")])
    assert book.legs == []
    assert book.complete is False
    assert "QQQ" in book.unpriced[0]


def test_a_fully_priced_book_says_so():
    book = to_book([share("SPY", 100, "765"), option("SPY270115P00700000", 1, "8")])
    assert book.complete is True
    assert book.unpriced == []


def test_cash_is_a_holding_that_does_not_move():
    book = to_book([share("SPY", 100, "500")], cash=Decimal("150000"))
    cash = [h for h in book.holdings if h.symbol == "CASH"]
    assert cash and cash[0].shocked is False
    assert cash[0].value == pytest.approx(150_000)


def test_equity_exposure_excludes_the_ballast():
    """The number the mandate is really about."""
    book = to_book(
        [share("SPY", 100, "1000"), share("BIL", 1000, "100")],
        cash=Decimal("50000"),
    )
    assert book.equity_exposure == pytest.approx(100_000)


def test_the_demonstration_book_breaches_at_twenty_and_holds_at_ten():
    """The portfolio build_portfolio.py establishes, end to end.

    Sized so the mandate holds at -10% and breaks at -20%. A book that never
    breached would give the agent nothing to do; one that always breached would
    make the promise theatre.
    """
    positions = [
        share("SPY", 392, "765.13"),
        share("QQQ", 211, "710.75"),
        share("IWM", 501, "299.27"),
        share("BIL", 2728, "91.62"),
    ]
    book = to_book(positions, cash=Decimal("150214"))
    rungs = ladder(book.holdings, book.legs, budget=100_000.0)

    by_shock = {r.shock: r for r in rungs}
    assert by_shock[-0.10].breached is False
    assert by_shock[-0.20].breached is True
    assert by_shock[-0.20].shortfall == pytest.approx(19_967, abs=50)
    assert worst_shortfall(rungs).shock == -0.35


def test_three_puts_close_the_gap_the_book_actually_has():
    """The headline claim, checked rather than asserted in a README."""
    positions = [
        share("SPY", 392, "765.13"),
        share("QQQ", 211, "710.75"),
        share("IWM", 501, "299.27"),
        share("BIL", 2728, "91.62"),
        option("SPY270115P00700000", 3, "8.00"),
    ]
    book = to_book(positions, cash=Decimal("150214"))
    rung = ladder(book.holdings, book.legs, 100_000.0, shocks=(-0.20,))[0]
    assert rung.breached is False
    # And it was not free: 3 contracts at 8.00 a share.
    assert sum(float(leg.premium) * leg.contracts * 100 for leg in book.legs) == 2_400
