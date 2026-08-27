"""How a symbol's position moves between states, as pure functions.

Every transition returns a new state.

CASH --sell put--> PUT_OPEN --expired--> CASH
                            \\--assigned--> SHARES
SHARES --sell call--> CALL_OPEN --expired--> SHARES
                                \\--assigned--> CASH
"""

from decimal import Decimal
from typing import Literal

from drawdownguard.domain import SHARES_PER_CONTRACT, Leg, OpenContract, Position

Action = Literal["SELL_PUT", "SELL_CALL", "HOLD"]


class IllegalTransition(Exception):
    """Raised when a transition would produce an unrepresentable position."""


def next_action(state: Position) -> Action:
    if state.leg == "CASH":
        return "SELL_PUT"
    if state.leg == "SHARES":
        return "SELL_CALL"
    return "HOLD"


def _premium_cash(contract: OpenContract) -> Decimal:
    return contract.premium * abs(contract.contracts) * SHARES_PER_CONTRACT


def on_sold_put(state: Position, contract: OpenContract) -> Position:
    if state.leg != "CASH":
        raise IllegalTransition(f"cannot sell a put from leg {state.leg}")
    if contract.right != "P" or contract.contracts >= 0:
        raise IllegalTransition("expected a short put")
    return state.model_copy(
        update={
            "leg": "PUT_OPEN",
            "contracts": [contract],
            "premium_collected": state.premium_collected + _premium_cash(contract),
        }
    )


def on_sold_call(state: Position, contract: OpenContract) -> Position:
    if state.leg != "SHARES":
        raise IllegalTransition(
            f"cannot sell a call from leg {state.leg}: that would be naked"
        )
    if contract.right != "C" or contract.contracts >= 0:
        raise IllegalTransition("expected a short call")
    required = abs(contract.contracts) * SHARES_PER_CONTRACT
    if state.shares < required:
        raise IllegalTransition(
            f"naked call: {state.shares} shares held, {required} required"
        )
    new_basis = None
    if state.basis is not None:
        new_basis = state.basis - contract.premium
    return state.model_copy(
        update={
            "leg": "CALL_OPEN",
            "contracts": [contract],
            "premium_collected": state.premium_collected + _premium_cash(contract),
            "basis": new_basis,
        }
    )


def on_expired_worthless(state: Position) -> Position:
    resting_leg: Leg
    if state.leg == "PUT_OPEN":
        resting_leg = "CASH"
    elif state.leg == "CALL_OPEN":
        resting_leg = "SHARES"
    else:
        raise IllegalTransition(f"nothing open to expire in leg {state.leg}")
    return state.model_copy(
        update={
            "leg": resting_leg,
            "contracts": [],
            "cycle_count": state.cycle_count + 1,
        }
    )


def on_put_assigned(state: Position) -> Position:
    if state.leg != "PUT_OPEN":
        raise IllegalTransition(f"no open put to assign in leg {state.leg}")
    contract = state.contracts[0]
    shares = abs(contract.contracts) * SHARES_PER_CONTRACT
    return state.model_copy(
        update={
            "leg": "SHARES",
            "shares": state.shares + shares,
            "basis": contract.strike - contract.premium,
            "contracts": [],
            "cycle_count": state.cycle_count + 1,
        }
    )


def on_call_assigned(state: Position) -> Position:
    if state.leg != "CALL_OPEN":
        raise IllegalTransition(f"no open call to assign in leg {state.leg}")
    contract = state.contracts[0]
    shares = abs(contract.contracts) * SHARES_PER_CONTRACT
    remaining = state.shares - shares
    return state.model_copy(
        update={
            "leg": "SHARES" if remaining > 0 else "CASH",
            "shares": remaining,
            "basis": state.basis if remaining > 0 else None,
            "contracts": [],
            "cycle_count": state.cycle_count + 1,
        }
    )
