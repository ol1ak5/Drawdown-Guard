from datetime import date
from decimal import Decimal

from drawdownguard.domain import OpenContract


def short_put(strike="560", premium="2.35"):
    return OpenContract(
        occ_symbol=f"SPY260828P00{strike}000",
        right="P",
        strike=Decimal(strike),
        expiry=date(2026, 8, 28),
        contracts=-1,
        premium=Decimal(premium),
    )


def short_call(strike="570", premium="1.80"):
    return OpenContract(
        occ_symbol=f"SPY260904C00{strike}000",
        right="C",
        strike=Decimal(strike),
        expiry=date(2026, 9, 4),
        contracts=-1,
        premium=Decimal(premium),
    )
