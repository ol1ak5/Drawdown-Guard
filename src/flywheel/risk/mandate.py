"""The client's mandate: a promise stated so a machine can check it.

Two constitutions govern this agent, and the distinction is the whole design.

**The permanent one** is `config/risk.yaml`, loaded as `Limits`. It never
changes. `forbid_naked` in particular has no override anywhere in the system.

**The temporary one** is the mandate. It changes per client and may be
*stricter* than the permanent constitution — never weaker. `Mandate.validate`
refuses to construct one that tries, so "the client asked for it" can never
become a route around a risk limit.

That ordering is what makes a mandate worth anything. A profile that could
loosen a limit would not be a promise, it would be a preference.

WHAT A MANDATE PROMISES
-----------------------
Five checkable things: which instruments may be traded at all, how much capital
may be deployed, how far out of the money the agent must stay, how much of the
portfolio's risk may sit in one factor, and how much the client accepts losing
in a market shock. Every one is verified before an order and audited after the
cycle.

The last is different in kind from the other four. Those constrain what may be
opened; the downside budget constrains what the book already held would cost if
the market moved, which is a promise no single order can keep or break on its
own. `risk/stress.py` is where it is measured.
"""

from decimal import Decimal
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

from flywheel.risk.limits import Limits
from flywheel.risk.stress import DEFAULT_SHOCKS

MANDATES_PATH = Path("config/mandates.yaml")


class MandateViolation(BaseModel):
    """One promise, and whether it was kept.

    `passed` is about risk, not about exact obedience. `cautious` marks a
    deviation that went the safe way: the agent did less than the mandate
    permitted. An audit that scored those the same as a breach would report a
    failure every time the agent was careful, and nobody would read it twice.
    """

    check: str
    passed: bool
    detail: str
    cautious: bool = False


class Mandate(BaseModel):
    """What this client's portfolio is allowed to want."""

    name: str
    description: str = ""
    universe: list[str]
    max_deployed_pct: float
    target_delta: dict[str, float]
    max_concentration_pct: float = 100.0
    stress_allows_new_risk: bool = False
    # The most of the account the client accepts losing in a market shock,
    # stated in advance and in the calm. This is the number the stress ladder is
    # measured against, and the only one in this file that is forward-looking:
    # everything else constrains what may be opened today, while this constrains
    # what today's book would cost if the market moved.
    downside_budget_pct: float = 10.0
    # The shock the budget is promised against, as a positive percentage. The
    # ladder reports deeper ones too; this is the one the agent is obliged to
    # close. See `stress.gap_at` for why promising the deepest rung would be
    # both unclosable and unaffordable.
    stress_shock_pct: float = 20.0

    def budget(self, equity: float | Decimal) -> float:
        """The downside budget in dollars."""
        return float(equity) * self.downside_budget_pct / 100

    @property
    def binding_shock(self) -> float:
        """The promised shock as a negative fraction, the way the ladder uses it."""
        return -self.stress_shock_pct / 100

    @property
    def delta_band(self) -> tuple[float, float]:
        return self.target_delta["min"], self.target_delta["max"]

    @model_validator(mode="after")
    def coherent(self) -> "Mandate":
        low, high = self.delta_band
        if low > high:
            raise ValueError(f"{self.name}: delta band {low} is above {high}")
        if not self.universe:
            raise ValueError(f"{self.name}: an empty universe can never trade")
        if not 0 < self.max_deployed_pct <= 100:
            raise ValueError(f"{self.name}: deployment must be within (0, 100]")
        if not 0 < self.downside_budget_pct <= 100:
            raise ValueError(f"{self.name}: a downside budget must be within (0, 100]")
        if -self.binding_shock not in [-s for s in DEFAULT_SHOCKS]:
            raise ValueError(
                f"{self.name}: promises against a {self.stress_shock_pct}% shock, "
                f"which is not a rung on the published ladder. The ladder is fixed "
                f"so a bad day cannot redefine what safe means; a mandate that "
                f"picked its own shock could pick a flattering one."
            )
        return self

    def validate_against(self, limits: Limits) -> "Mandate":
        """Refuse a mandate that is looser than the permanent constitution.

        A client mandate is allowed to tie the agent's hands further. It is
        never allowed to untie them — otherwise "the client chose aggressive"
        becomes a way to walk past a risk limit, and the permanent constitution
        stops being permanent.
        """
        if self.max_deployed_pct > limits.max_deployed_pct:
            raise ValueError(
                f"{self.name} asks to deploy {self.max_deployed_pct}% of capital, "
                f"more than the permanent limit of {limits.max_deployed_pct}%. "
                f"A mandate may be stricter than the constitution, never weaker."
            )
        if self.downside_budget_pct > limits.max_drawdown_pct:
            raise ValueError(
                f"{self.name} promises the client a "
                f"{self.downside_budget_pct}% downside budget, but the kill-switch "
                f"halts the agent at a {limits.max_drawdown_pct}% drawdown. The "
                f"promise could never be exercised, because trading stops first."
            )
        return self


def load_mandate(
    name: str = "balanced",
    path: Path | str = MANDATES_PATH,
    limits: Limits | None = None,
) -> Mandate:
    """One mandate by name, checked against the permanent limits."""
    from flywheel.risk.limits import load_limits

    profiles = yaml.safe_load(Path(path).read_text())
    if name not in profiles:
        raise KeyError(f"unknown mandate {name!r}; have {', '.join(sorted(profiles))}")
    mandate = Mandate(name=name, **profiles[name])
    return mandate.validate_against(limits or load_limits())


def load_all(
    path: Path | str = MANDATES_PATH, limits: Limits | None = None
) -> dict[str, Mandate]:
    """Every mandate. Used by the side-by-side view: one market, three answers."""
    profiles = yaml.safe_load(Path(path).read_text())
    return {name: load_mandate(name, path, limits) for name in profiles}


def audit(
    mandate: Mandate,
    deployed_pct: float,
    concentration_pct: float,
    traded_symbols: list[str],
    deltas: list[float],
) -> list[MandateViolation]:
    """Did the agent keep the promise? One verdict per clause.

    Deliberately has no power to fix anything. The auditor reports; it does not
    intervene. An auditor that could correct the thing it audits is not an
    auditor, and the separation is what makes the verdict worth reading.

    An empty cycle passes every check. Trading nothing cannot breach a cap, and
    reporting a violation for a cycle that did nothing would make the audit
    noise rather than signal.
    """
    low, high = mandate.delta_band
    # Above the band is more risk than promised. Below it is less. Only the
    # first is a breach.
    #
    # This distinction was not in the first version and the real cycle of
    # 2026-08-25 exposed it: the analyst called stress, tightened delta to
    # 0.145, and the audit reported a mandate violation for an agent that had
    # been more careful than required. The delta floor exists to keep the
    # premium worth collecting, not to stop the agent from being safe.
    over = [d for d in deltas if abs(d) > high]
    under = [d for d in deltas if abs(d) < low]
    off_universe = [s for s in traded_symbols if s not in mandate.universe]

    return [
        MandateViolation(
            check="deployment",
            passed=deployed_pct <= mandate.max_deployed_pct + 1e-9,
            detail=(
                f"{deployed_pct:.1f}% of capital deployed against a "
                f"{mandate.max_deployed_pct:.0f}% cap"
            ),
        ),
        MandateViolation(
            check="universe",
            passed=not off_universe,
            detail=(
                f"traded outside the mandate: {', '.join(off_universe)}"
                if off_universe
                else f"all trades within {', '.join(mandate.universe)}"
            ),
        ),
        MandateViolation(
            check="delta",
            passed=not over,
            cautious=bool(under and not over),
            detail=(
                f"{len(over)} contract(s) above the {high:.2f} delta ceiling"
                if over
                else (
                    f"{len(under)} contract(s) below the {low:.2f} floor — safer "
                    f"than the mandate requires, not a breach"
                    if under
                    else f"every contract inside the {low:.2f}-{high:.2f} band"
                )
            ),
        ),
        MandateViolation(
            check="concentration",
            passed=concentration_pct <= mandate.max_concentration_pct + 1e-9,
            detail=(
                f"{concentration_pct:.1f}% of variance in the dominant risk "
                f"bucket against a {mandate.max_concentration_pct:.0f}% cap"
            ),
        ),
    ]


def compliance_pct(violations: list[MandateViolation]) -> float:
    if not violations:
        return 100.0
    return 100.0 * sum(1 for v in violations if v.passed) / len(violations)


def verdict(violations: list[MandateViolation]) -> str:
    """A sentence a human reads first, before the table."""
    failed = [v for v in violations if not v.passed]
    if not failed:
        cautious = [v for v in violations if v.cautious]
        if cautious:
            names = ", ".join(v.check for v in cautious)
            return f"MANDATE KEPT, more cautiously than required: {names}"
        return "MANDATE KEPT"
    names = ", ".join(v.check for v in failed)
    return f"MANDATE BREACHED: {names}"


class Counterfactual(BaseModel):
    """What the discipline cost, in dollars.

    Without this a reader can fairly say: of course the constrained agent took
    less risk, it simply traded less. The answer has to be a number — here is
    the premium that was available, here is what we took, here is the
    difference and the reason.
    """

    premium_taken: float
    premium_available: float
    concentration_taken: float
    concentration_available: float
    reason: str = ""

    @property
    def forgone(self) -> float:
        return max(self.premium_available - self.premium_taken, 0.0)

    def describe(self) -> str:
        if self.forgone <= 0:
            return "the constraint cost nothing this cycle"
        return (
            f"declined {self.forgone:,.0f} of premium to hold concentration at "
            f"{self.concentration_taken:.1f}% instead of "
            f"{self.concentration_available:.1f}%"
        )
