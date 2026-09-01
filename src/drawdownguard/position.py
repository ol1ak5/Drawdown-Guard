"""How a symbol's position moves when the broker reports something new.

Assignment and expiry happen to the account whether or not anybody asked, so
the transitions here are the ones the reconciler needs to explain a leg that
changed overnight.

`next_action` used to live here and returned SELL_CALL for any book holding
shares. The nodes that called it were removed when the options wheel was, and
the function outlived them by a week -- unreachable, still readable as a
description of the agent, and one import away from writing calls against a
client's equity again.

Every transition returns a new state.

CASH --sell put--> PUT_OPEN --expired--> CASH
                            \\--assigned--> SHARES
SHARES --sell call--> CALL_OPEN --expired--> SHARES
                                \\--assigned--> CASH
"""

from drawdownguard.domain import SHARES_PER_CONTRACT, Leg, Position


class IllegalTransition(Exception):
    """Raised when a transition would produce an unrepresentable position."""


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
