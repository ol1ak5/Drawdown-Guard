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


class Position(BaseModel):
    symbol: str
    leg: Leg = "CASH"
    shares: int = 0
    basis: Decimal | None = None  # strike minus all premiums collected
    contracts: list[OpenContract] = Field(default_factory=list)
    premium_collected: Decimal = Decimal("0")
    cycle_count: int = 0


class ProposedOrder(BaseModel):
    """One option order: sold to earn, or bought to defend."""

    symbol: str
    right: Right
    strike: Decimal
    expiry: date
    contracts: int  # negative: sell to open
    limit_price: Decimal
    delta: float  # per share, signed
    vega: float  # per contract, per one point of implied volatility
    assignment_prob: float
    open_interest: int
    spread_pct: float
    # Price of the underlying when the order was proposed. Carried because the
    # directional limit is measured in dollars, and share equivalents cannot be
    # converted to dollars after the fact without knowing what a share cost.
    spot: float

    @property
    def delta_value(self) -> float:
        """Directional exposure in dollars, signed.

        Quantity times per-share delta times the contract multiplier, priced at
        spot. A short put (delta -0.30, contracts -1) is positive: selling a put
        is a bullish position.
        """
        return self.delta * self.contracts * SHARES_PER_CONTRACT * self.spot

    @property
    def collateral(self) -> Decimal:
        """Cash a short put ties up. Calls are collateralised by shares."""
        return self.strike * abs(self.contracts) * SHARES_PER_CONTRACT

    @property
    def is_purchase(self) -> bool:
        """Positive contracts: the account pays out rather than takes in."""
        return self.contracts > 0

    @property
    def debit(self) -> Decimal:
        """Cash paid for a long option, which is also its whole maximum loss."""
        return self.limit_price * abs(self.contracts) * SHARES_PER_CONTRACT

    @property
    def capital_at_risk(self) -> Decimal:
        """What this position can cost the account -- and the two sides of the
        same contract are nothing alike.

        A short put must be able to buy the shares if it is assigned, so the
        entire strike is committed. A long option can only ever lose what it
        cost. Ten 560 puts tie up 560,000 when sold and 2,350 when bought, and
        reading the first number on a purchase makes every hedge look like a
        position two hundred times its real size. The limits are stated as a
        share of equity, so getting this wrong does not merely mis-report --
        it refuses the trade.
        """
        return self.debit if self.is_purchase else self.collateral


class Portfolio(BaseModel):
    equity: Decimal
    cash: Decimal
    peak_equity: Decimal
    deployed: Decimal = Decimal("0")
    # Share equivalents, signed. Kept for reporting: it is the number a trader
    # reads. It is *not* what the limit is measured against — see below.
    net_delta: float = 0.0
    # Directional exposure in dollars, signed. This is what `max_net_delta_pct`
    # constrains.
    #
    # Share equivalents do not compare across instruments. At a quarter of a
    # 1,000,000 account, one fully sized position is 300 shares of SPY, 400 of
    # QQQ or 1,000 of IWM — the same dollar risk, share counts differing by
    # 3.3x. A band in shares is strict on cheap instruments and permissive on
    # expensive ones for identical exposure, and it means something different
    # at every account size. Dollars compare; share counts do not.
    net_delta_value: float = 0.0
    vega: float = 0.0  # dollars lost per one point rise in implied volatility
    positions: dict[str, Position] = Field(default_factory=dict)

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
