"""The broker is the source of truth. On any mismatch, the broker wins.

Assignment happens overnight without asking. The agent goes to sleep short a
put and wakes up owning stock; nothing notifies it, and the only evidence is
that the broker's positions no longer match its own record. So every cycle
starts here, before anything is proposed.

`reconcile` is pure and takes no network. The caller fetches positions and
passes them in, which keeps it testable and lets the backtest drive the same
code path the live agent runs.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from drawdownguard.domain import Leg, OpenContract, Position
from drawdownguard.position import (
    on_call_assigned,
    on_expired_worthless,
    on_put_assigned,
)

# OCC: up to six letters of underlying, YYMMDD, P or C, then the strike in
# thousandths of a dollar. Anchored, because `SPY` on its own must not match.
_OCC = re.compile(
    r"^(?P<underlying>[A-Z]{1,6})(?P<expiry>\d{6})(?P<right>[PC])(?P<strike>\d{8})$"
)


def parse_occ(symbol: str) -> dict | None:
    """Decode an OCC symbol, or return None when it is an ordinary ticker.

    Public because the backtest reads these symbols back out of historical
    bars. `options_history.occ_symbol` builds them; this is the inverse.
    """
    match = _OCC.match(symbol.upper())
    if match is None:
        return None
    expiry = match.group("expiry")
    return {
        "underlying": match.group("underlying"),
        "expiry": date(2000 + int(expiry[:2]), int(expiry[2:4]), int(expiry[4:6])),
        "right": match.group("right"),
        "strike": Decimal(match.group("strike")) / 1000,
    }


def _quantity(position: dict) -> int:
    """Alpaca returns qty as a string. Reading it as an int would raise or lie."""
    return int(Decimal(str(position.get("qty", 0))))


def _premium(position: dict) -> Decimal:
    """Entry price per share, when the broker reports one.

    A missing or unparseable price becomes zero, which understates
    `premium_collected` rather than inventing a number. The caller is told.
    """
    try:
        return Decimal(str(position.get("avg_entry_price", "0")))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _adopt(position: dict, occ: dict) -> OpenContract:
    """Rebuild a contract from a broker position alone.

    Everything except the premium is recoverable from the OCC symbol, which is
    why the symbol is worth parsing rather than storing separately.
    """
    return OpenContract(
        occ_symbol=position["symbol"].upper(),
        right=occ["right"],
        strike=occ["strike"],
        expiry=occ["expiry"],
        contracts=_quantity(position),
        premium=_premium(position),
    )


def _group(positions: list[dict]) -> dict[str, dict]:
    """Index the broker's positions by underlying symbol."""
    grouped: dict[str, dict] = {}
    for position in positions:
        occ = parse_occ(position["symbol"])
        symbol = occ["underlying"] if occ else position["symbol"].upper()
        holding = grouped.setdefault(symbol, {"shares": 0, "options": []})
        if occ:
            holding["options"].append((position, occ))
        else:
            holding["shares"] += _quantity(position)
    return grouped


def _broker_leg(holding: dict) -> Leg:
    """What the broker's positions say the position's leg is."""
    for _position, occ in holding["options"]:
        if occ["right"] == "P":
            return "PUT_OPEN"
        if occ["right"] == "C":
            return "CALL_OPEN"
    return "SHARES" if holding["shares"] > 0 else "CASH"


# Every mismatch that has an ordinary explanation, and the transition that
# explains it. Anything not in here is not a normal outcome of an overnight
# expiry or assignment, and is handled by the blunt fallback below.
_EXPLAINED = {
    ("PUT_OPEN", "SHARES"): (on_put_assigned, "the put was assigned"),
    ("PUT_OPEN", "CASH"): (on_expired_worthless, "the put expired worthless"),
    ("CALL_OPEN", "CASH"): (on_call_assigned, "the call was assigned"),
    ("CALL_OPEN", "SHARES"): (on_expired_worthless, "the call expired worthless"),
}


def _adopt_wholesale(
    state: Position, leg: Leg, holding: dict
) -> tuple[Position, str]:
    """Take the broker's view when no ordinary transition explains the gap.

    A fill that landed after the last snapshot, or a hand-placed trade. Basis
    is left as None rather than guessed: the covered-call floor is computed
    from it, and a fabricated basis would let the agent write calls below what
    it actually paid for the shares.
    """
    contracts = [_adopt(position, occ) for position, occ in holding["options"]]
    updated = state.model_copy(
        update={
            "leg": leg,
            "shares": holding["shares"],
            "contracts": contracts,
            "basis": state.basis if leg in ("SHARES", "CALL_OPEN") else None,
        }
    )
    note = (
        f"{state.symbol}: local {state.leg} but broker shows {leg}; "
        f"adopted the broker's view"
    )
    if updated.basis is None and leg in ("SHARES", "CALL_OPEN"):
        note += " with no basis, which cannot be recovered from a position"
    return updated, note


def reconcile(
    local: dict[str, Position], broker_positions: list[dict]
) -> tuple[dict[str, Position], list[str]]:
    """Correct local state against the broker, and describe every correction.

    Returns a new mapping; the one passed in is never mutated. Discrepancies
    are plain sentences meant to be journalled and read by a human.
    """
    grouped = _group(broker_positions)
    corrected: dict[str, Position] = {}
    discrepancies: list[str] = []

    for symbol in sorted(set(local) | set(grouped)):
        state = local.get(symbol, Position(symbol=symbol))
        holding = grouped.get(symbol, {"shares": 0, "options": []})
        leg = _broker_leg(holding)

        if leg == state.leg:
            corrected[symbol] = state
        elif (state.leg, leg) in _EXPLAINED:
            transition, explanation = _EXPLAINED[(state.leg, leg)]
            corrected[symbol] = transition(state)
            discrepancies.append(
                f"{symbol}: local said {state.leg}, broker says {leg} — {explanation}"
            )
        else:
            corrected[symbol], note = _adopt_wholesale(state, leg, holding)
            discrepancies.append(note)

        # The share count is checked after the leg, because a transition can
        # set it too and the broker still outranks whatever the transition
        # computed.
        held = corrected[symbol].shares
        if held != holding["shares"]:
            corrected[symbol] = corrected[symbol].model_copy(
                update={"shares": holding["shares"]}
            )
            discrepancies.append(
                f"{symbol}: local held {held} shares, broker holds "
                f"{holding['shares']} — corrected to the broker's count"
            )

    return corrected, discrepancies
