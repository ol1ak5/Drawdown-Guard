"""Core domain types. Money is Decimal; greeks and probabilities are float."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

Leg = Literal["CASH", "SHARES", "PUT_OPEN", "CALL_OPEN"]
Regime = Literal["calm", "elevated", "stress", "crash"]
Right = Literal["P", "C"]

SHARES_PER_CONTRACT = 100


class OpenContract(BaseModel):
    occ_symbol: str
    right: Right
    strike: Decimal
    expiry: date
    contracts: int  # negative when short
    premium: Decimal  # per share, received on open

    @property
    def is_short(self) -> bool:
        return self.contracts < 0

    @property
    def notional(self) -> Decimal:
        return self.strike * abs(self.contracts) * SHARES_PER_CONTRACT


class WheelState(BaseModel):
    symbol: str
    leg: Leg = "CASH"
    shares: int = 0
    basis: Decimal | None = None  # strike minus all premiums collected
    contracts: list[OpenContract] = Field(default_factory=list)
    premium_collected: Decimal = Decimal("0")
    cycle_count: int = 0


class ProposedOrder(BaseModel):
    """A single short option the optimizer wants to open."""

    symbol: str
    right: Right
    strike: Decimal
    expiry: date
    contracts: int  # negative: sell to open
    limit_price: Decimal
    delta: float
    vega: float
    assignment_prob: float
    open_interest: int
    spread_pct: float

    @property
    def collateral(self) -> Decimal:
        """Cash a short put ties up. Calls are collateralised by shares."""
        return self.strike * abs(self.contracts) * SHARES_PER_CONTRACT


class Portfolio(BaseModel):
    equity: Decimal
    cash: Decimal
    peak_equity: Decimal
    deployed: Decimal = Decimal("0")
    net_delta: float = 0.0
    vega: float = 0.0
    wheels: dict[str, WheelState] = Field(default_factory=dict)

    @property
    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return float((self.peak_equity - self.equity) / self.peak_equity * 100)


class Verdict(BaseModel):
    approved: bool
    reason: str = ""

    @model_validator(mode="after")
    def rejection_must_explain_itself(self) -> "Verdict":
        if not self.approved and not self.reason.strip():
            raise ValueError("a rejected verdict must carry a reason")
        return self

    @classmethod
    def approve(cls) -> "Verdict":
        return cls(approved=True)

    @classmethod
    def reject(cls, reason: str) -> "Verdict":
        return cls(approved=False, reason=reason)
