"""The script that establishes the client's portfolio, before the agent runs.

This ran untested for the whole of its first life, which was survivable while
it divided dollar targets by a price. It no longer does: the targets are share
counts now, because an option contract covers a hundred shares and a hedge only
fits a position that is a whole multiple of a hundred. Getting the count wrong
is not a rounding error, it is the wrong portfolio.
"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_portfolio import (  # noqa: E402
    CASH_BUFFER,
    ROLE,
    TARGET,
    short_put_collateral,
    size_against_cash,
)

# The prices this allocation was chosen against.
PRICES = {"IWM": Decimal("299.88"), "XLF": Decimal("57.88"), "BIL": Decimal("91.60")}


def test_every_equity_target_is_a_whole_number_of_contracts():
    """The reason the targets are share counts at all.

    A contract covers a hundred shares. A position that is not a multiple of a
    hundred cannot be hedged without buying protection for shares the client
    does not own, and on a 100,000 account that overshoot is not small: 52
    shares of a 770 stock would carry a hedge standing behind 100.
    """
    for symbol, shares in TARGET.items():
        if symbol == "BIL":
            continue  # ballast is never hedged, so it is not held in hundreds
        assert shares % 100 == 0, f"{symbol}: {shares} shares is not whole contracts"


def test_the_equity_sleeve_breaches_the_promise_but_can_still_be_hedged():
    """The portfolio has to give the agent something to do, and something it
    can actually finish.

    A book that never breaches makes the mandate theatre; one that breaches
    beyond what a 10% budget can buy back makes it a permanent failure. At
    -20% this loses 16,416 against a 10,000 budget, which is a gap the chain
    can close.
    """
    equity = sum(PRICES[s] * q for s, q in TARGET.items() if s != "BIL")
    budget = Decimal("10000")
    assert equity * Decimal("0.20") > budget, "no gap means no work"
    # The whole-descent worst case for a matched hedge is the fall to the
    # strike plus premium; measured on the live chain that came to 6,138 of a
    # 6,346 sleeve budget on XLF, so the promise is closeable.
    assert equity < Decimal("120000"), "past this a 10,000 budget cannot buy it back"


def test_a_plan_that_fits_is_left_alone():
    targets, note, refusal = size_against_cash(
        TARGET, PRICES, cash=Decimal("100000"), reserved=Decimal("0")
    )
    assert refusal is None
    assert note is None
    assert targets == TARGET


def test_collateral_comes_out_of_the_ballast_and_never_the_equity_sleeve():
    """The rule the whole demonstration depends on.

    Shrinking the equity sleeve to make room would shrink the protection gap
    with it, and the portfolio would stop showing the thing it was built to
    show -- the demo would look better for the wrong reason.
    """
    targets, note, refusal = size_against_cash(
        TARGET, PRICES, cash=Decimal("100000"), reserved=Decimal("9160")
    )
    assert refusal is None
    assert targets["IWM"] == TARGET["IWM"]
    assert targets["XLF"] == TARGET["XLF"]
    assert targets["BIL"] < TARGET["BIL"]
    assert "ballast reduced" in note


def test_the_ballast_is_given_back_in_whole_shares_rounded_up():
    """A reduction rounded down leaves the short puts a few dollars uncovered,
    which is the one thing `forbid_naked` exists to prevent."""
    cash = Decimal("100000")
    reserved = Decimal("5000")
    targets, _, refusal = size_against_cash(TARGET, PRICES, cash, reserved)
    assert refusal is None

    spent = sum(PRICES[s] * q for s, q in targets.items())
    assert cash - spent >= reserved + CASH_BUFFER, (
        "the plan must leave the collateral and the buffer untouched"
    )


def test_an_overlay_too_large_to_fit_is_refused_rather_than_squeezed():
    targets, note, refusal = size_against_cash(
        TARGET, PRICES, cash=Decimal("100000"), reserved=Decimal("80000")
    )
    assert refusal is not None
    assert "refusing" in refusal
    assert note is None


def test_every_target_has_a_role_recorded_with_it():
    """The roles are journalled with the position, so a reader a month later
    knows which holding was exposure and which was ballast."""
    assert set(TARGET) == set(ROLE)


def test_short_put_collateral_reserves_the_whole_strike():
    """`forbid_naked` secures a written put with the entire strike in cash, not
    with the margin a broker would actually demand."""
    positions = [
        {"symbol": "IWM260918P00280000", "qty": "-2"},
        {"symbol": "IWM", "qty": "100"},  # shares, not an option
    ]
    assert short_put_collateral(positions) == Decimal("280") * 2 * 100


def test_a_long_put_ties_up_no_collateral():
    """It was paid for in full when it was opened; there is no obligation left
    to secure."""
    positions = [{"symbol": "IWM260918P00280000", "qty": "3"}]
    assert short_put_collateral(positions) == Decimal("0")


@pytest.mark.parametrize("bad", [{"bp": 294.98, "ap": 0}, {"bp": 0, "ap": 294.98}])
def test_a_one_sided_quote_is_not_averaged_into_half_a_price(bad):
    """The bug this script shipped with, and the reason it now imports `mid_of`.

    After the close a stock quote is routinely one-sided. Averaging with the
    missing side gives exactly half the price, with no error raised -- and this
    script divides a dollar figure by it, so a half price buys twice the shares.
    """
    from drawdownguard.market.chain import mid_of

    assert mid_of(bad) == pytest.approx(294.98)
