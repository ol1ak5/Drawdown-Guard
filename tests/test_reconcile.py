from datetime import date
from decimal import Decimal

from flywheel.domain import OpenContract, WheelState
from flywheel.execution.reconcile import reconcile

SHORT_PUT = OpenContract(
    occ_symbol="SPY260918P00620000",
    right="P",
    strike=Decimal("620"),
    expiry=date(2026, 9, 18),
    contracts=-1,
    premium=Decimal("4.20"),
)

SHORT_CALL = OpenContract(
    occ_symbol="SPY260918C00640000",
    right="C",
    strike=Decimal("640"),
    expiry=date(2026, 9, 18),
    contracts=-1,
    premium=Decimal("3.10"),
)


def shares(symbol: str, qty: int) -> dict:
    return {"symbol": symbol, "qty": qty, "asset_class": "us_equity"}


def option(occ: str, qty: int, avg_entry_price: str = "0") -> dict:
    return {
        "symbol": occ,
        "qty": qty,
        "asset_class": "us_option",
        "avg_entry_price": avg_entry_price,
    }


def test_an_overnight_put_assignment_is_detected():
    """The single most important case: assignment happens without asking.

    The agent goes to sleep short a put and wakes up owning stock. Nothing
    notifies it; the only evidence is that the broker's positions no longer
    match its own record.
    """
    local = {"SPY": WheelState(symbol="SPY", leg="PUT_OPEN", contracts=[SHORT_PUT])}
    state, discrepancies = reconcile(local, [shares("SPY", 100)])

    assert state["SPY"].leg == "SHARES"
    assert state["SPY"].shares == 100
    assert state["SPY"].basis == Decimal("615.80")  # strike less the premium
    assert state["SPY"].cycle_count == 1
    assert state["SPY"].contracts == []
    assert len(discrepancies) == 1
    assert "SPY" in discrepancies[0]


def test_an_expired_put_returns_the_wheel_to_cash():
    local = {"SPY": WheelState(symbol="SPY", leg="PUT_OPEN", contracts=[SHORT_PUT])}
    state, discrepancies = reconcile(local, [])

    assert state["SPY"].leg == "CASH"
    assert state["SPY"].cycle_count == 1
    assert discrepancies


def test_a_call_assignment_returns_the_wheel_to_cash():
    local = {
        "SPY": WheelState(
            symbol="SPY",
            leg="CALL_OPEN",
            shares=100,
            basis=Decimal("615.80"),
            contracts=[SHORT_CALL],
        )
    }
    state, discrepancies = reconcile(local, [])

    assert state["SPY"].leg == "CASH"
    assert state["SPY"].shares == 0
    assert state["SPY"].basis is None
    assert discrepancies


def test_an_expired_call_leaves_the_shares_in_place():
    local = {
        "SPY": WheelState(
            symbol="SPY",
            leg="CALL_OPEN",
            shares=100,
            basis=Decimal("615.80"),
            contracts=[SHORT_CALL],
        )
    }
    state, discrepancies = reconcile(local, [shares("SPY", 100)])

    assert state["SPY"].leg == "SHARES"
    assert state["SPY"].shares == 100
    assert state["SPY"].basis == Decimal("615.80")
    assert discrepancies


def test_agreement_produces_no_discrepancies():
    local = {
        "SPY": WheelState(symbol="SPY", leg="PUT_OPEN", contracts=[SHORT_PUT]),
        "IWM": WheelState(symbol="IWM", leg="CASH"),
    }
    state, discrepancies = reconcile(local, [option("SPY260918P00620000", -1)])

    assert discrepancies == []
    assert state == local


def test_an_open_position_the_agent_never_recorded_is_adopted():
    """A fill that landed after the last snapshot, or a hand-placed trade."""
    local: dict[str, WheelState] = {}
    state, discrepancies = reconcile(
        local, [option("QQQ260918P00500000", -2, avg_entry_price="3.40")]
    )

    assert state["QQQ"].leg == "PUT_OPEN"
    assert len(state["QQQ"].contracts) == 1
    adopted = state["QQQ"].contracts[0]
    assert adopted.strike == Decimal("500")
    assert adopted.expiry == date(2026, 9, 18)
    assert adopted.contracts == -2
    assert adopted.premium == Decimal("3.40")
    assert discrepancies


def test_an_unknown_symbol_holding_shares_gets_a_state():
    state, discrepancies = reconcile({}, [shares("IWM", 100)])

    assert state["IWM"].leg == "SHARES"
    assert state["IWM"].shares == 100
    assert discrepancies


def test_an_adopted_position_has_no_basis_and_says_so():
    """Basis cannot be reconstructed from a position, and silence would be a lie.

    The covered-call floor is computed from basis. A fabricated one would let
    the agent write calls below what it actually paid.
    """
    state, discrepancies = reconcile({}, [shares("IWM", 100)])

    assert state["IWM"].basis is None
    assert any("basis" in message for message in discrepancies)


def test_the_broker_share_count_wins():
    local = {"SPY": WheelState(symbol="SPY", leg="SHARES", shares=100)}
    state, discrepancies = reconcile(local, [shares("SPY", 200)])

    assert state["SPY"].shares == 200
    assert any("200" in message and "100" in message for message in discrepancies)


def test_a_long_equity_position_does_not_look_like_an_option():
    """`SPY` must not be parsed as an OCC symbol just because it is uppercase."""
    state, _ = reconcile({}, [shares("SPY", 100)])
    assert state["SPY"].contracts == []


def test_reconcile_does_not_mutate_the_state_it_was_given():
    """It is pure so the backtest can use it, and so a bad cycle is recoverable."""
    original = WheelState(symbol="SPY", leg="PUT_OPEN", contracts=[SHORT_PUT])
    local = {"SPY": original}
    reconcile(local, [shares("SPY", 100)])

    assert local["SPY"].leg == "PUT_OPEN"
    assert original.leg == "PUT_OPEN"


def test_a_string_quantity_from_the_broker_is_accepted():
    """Alpaca returns qty as a string. Treating it as an int would silently fail."""
    state, _ = reconcile({}, [shares("SPY", "100")])
    assert state["SPY"].shares == 100


def test_several_symbols_are_reconciled_independently():
    local = {
        "SPY": WheelState(symbol="SPY", leg="PUT_OPEN", contracts=[SHORT_PUT]),
        "IWM": WheelState(symbol="IWM", leg="CASH"),
    }
    state, discrepancies = reconcile(local, [shares("SPY", 100)])

    assert state["SPY"].leg == "SHARES"
    assert state["IWM"].leg == "CASH"
    assert len(discrepancies) == 1
