"""Turning what the broker reports into something the stress ladder can read.

`stress.py` is pure arithmetic over `Holding` and `OptionLeg`. This module is
the adapter between that and the untidy list of dictionaries Alpaca actually
returns. It is separate for one reason: the ladder should stay checkable by
hand, and mixing broker field names into it would make every test about the
mathematics also a test about Alpaca's JSON.

WHAT COUNTS AS PROTECTION, AND WHAT ONLY LOOKS LIKE IT
------------------------------------------------------
Short-duration Treasury funds are marked `shocked=False`: they sit still while
equities fall. That is *ballast*, not a hedge — it does not gain when the
market drops, it merely fails to lose, and the ladder never credits it as
protection.

The list is short and hand-written, and that is deliberate. A rule like "any
ETF with 'bond' in the name" would sweep in TLT, which fell 31% in 2022 *with*
equities. Anything not on the list is treated as fully exposed, so the failure
mode of an unknown ticker is to overstate risk rather than to hide it.

WHEN THE BOOK CANNOT BE PRICED
-------------------------------
An option needs its underlying's spot to be shocked. If that price is missing,
the leg is not silently skipped. Skipping a long put would understate the
protection held; skipping a short put would understate the loss — and the
second error is the one that gets somebody hurt. So unpriced positions come
back in `unpriced`, the caller journals them, and a ladder built on an
incomplete book is labelled as such rather than quietly reported as fact.
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from drawdownguard.execution.reconcile import parse_occ
from drawdownguard.risk.stress import Holding, OptionLeg

# Instruments that hold their value through an equity shock. Cash-like by
# duration, not by asset class: these are bills and near-bill funds, where a
# 20% drawdown in equities moves the price by basis points.
CASH_LIKE = frozenset({"BIL", "BILS", "SGOV", "SHV", "USFR", "TFLO", "ICSH"})


def _decimal(value: object, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _quantity(position: dict) -> int | None:
    """The share or contract count, or None if the broker did not give one.

    None rather than zero, because the two mean opposite things. A missing
    *price* already fails loudly into `unpriced`; a missing *quantity* used to
    fail silently -- 340 shares of QQQ absent from the response became a
    holding of zero, 600,000 of exposure vanished from the ladder, and the book
    still reported `complete` with nothing in `unpriced`. The agent would have
    sized protection for a portfolio two thirds the size of the real one.

    A fraction truncates, as it always has: Alpaca reports fractional shares
    and an option contract cannot be fractional, so the remainder is under one
    share and worth less than the spread on the order that would hedge it.
    """
    raw = position.get("qty")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(Decimal(str(raw)))
    except (InvalidOperation, ValueError, TypeError):
        return None


@dataclass
class Book:
    """The portfolio in the shape the ladder wants, plus what could not be read."""

    holdings: list[Holding] = field(default_factory=list)
    legs: list[OptionLeg] = field(default_factory=list)
    unpriced: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """False when something held could not be priced. See the module docstring."""
        return not self.unpriced

    @property
    def equity_exposure(self) -> float:
        """Dollars that actually move with the market.

        Cash and bills are excluded, which is the number the mandate is really
        about: a book that is 40% bills has 40% less to lose and the promise
        should be measured against the part that can.
        """
        return sum(h.value for h in self.holdings if h.shocked)


def to_book(
    positions: list[dict],
    cash: Decimal | float = 0,
    spots: dict[str, float] | None = None,
) -> Book:
    """Broker positions into ladder inputs.

    `spots` supplies the underlying price for option legs. Share positions
    carry their own price, so they need no help; options do, because an option
    position reports the price of the *option*, and shocking that number would
    be shocking the wrong thing.
    """
    spots = dict(spots or {})
    book = Book()

    # Two passes. Share prices discovered in the first pass are available as
    # underlying spots in the second, so holding SPY shares is enough to price
    # a SPY put without a second network call.
    options: list[dict] = []
    for position in positions:
        symbol = str(position.get("symbol", "")).upper()
        occ = parse_occ(symbol)
        if occ:
            options.append(position)
            continue

        qty = _quantity(position)
        if qty is None:
            book.unpriced.append(
                f"{symbol}: no readable quantity, holding left out of the ladder"
            )
            continue
        price = float(_decimal(position.get("current_price")))
        if price <= 0:
            book.unpriced.append(f"{symbol}: no price reported")
            continue
        spots.setdefault(symbol, price)
        book.holdings.append(
            Holding(
                symbol=symbol,
                shares=qty,
                price=price,
                shocked=symbol not in CASH_LIKE,
            )
        )

    for position in options:
        symbol = str(position["symbol"]).upper()
        occ = parse_occ(symbol)
        assert occ is not None  # already matched in the first pass
        spot = spots.get(occ["underlying"])
        if spot is None:
            book.unpriced.append(
                f"{symbol}: no spot for {occ['underlying']}, leg left out of the ladder"
            )
            continue
        contracts = _quantity(position)
        if contracts is None:
            book.unpriced.append(
                f"{symbol}: no readable quantity, leg left out of the ladder"
            )
            continue
        book.legs.append(
            OptionLeg(
                symbol=occ["underlying"],
                right=occ["right"],
                strike=occ["strike"],
                expiry=occ["expiry"],
                contracts=contracts,
                premium=_decimal(position.get("avg_entry_price")),
                spot=spot,
            )
        )

    # Cash is a holding rather than a subtraction from the loss, so the ladder
    # sees one list and the table adds up in the obvious way.
    balance = _decimal(cash)
    if balance:
        book.holdings.append(
            Holding(symbol="CASH", shares=int(balance), price=1.0, shocked=False)
        )
    return book
