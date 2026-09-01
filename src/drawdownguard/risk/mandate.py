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

from drawdownguard.risk.limits import Limits
from drawdownguard.risk.stress import DEFAULT_SHOCKS

MANDATES_PATH = Path("config/mandates.yaml")


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
    # How far inside the budget the book must sit before protection is given
    # back, as a percentage of the budget. This is the second of two thresholds:
    # protection is bought the moment the gap opens and released only with this
    # much headroom, so the agent cannot oscillate across the line paying the
    # spread each way. Stated by the client in advance, like everything else
    # here -- a hysteresis band invented by the agent would be a tuning
    # parameter, and tuning parameters are where forecasts hide.
    # How long the promise has to hold, in months, and so how long the
    # protection bought to hold it up must live.
    #
    # This is the number everybody forgets, and it decides more than any other
    # field here. "I can lose 10%" is not a promise until somebody says over
    # what: 10% by Friday and 10% over a year are different guarantees with
    # different price tags. Until the window is named there is nothing to buy.
    #
    # It exists because short-dated protection is cheap in exactly the way that
    # should make a buyer suspicious. Measured on real SPY history with this
    # project's own pricing: through the 2022 decline -- 25.4% over 279 days --
    # rolling 30-day puts 10% out of the money paid **nothing at all**. Nine
    # contracts in a row expired worthless while the market destroyed a quarter
    # of its value, because no single month fell far enough to put any of them
    # in the money. One put held across the whole period paid 65.
    #
    # Short protection covers fast crashes and nothing else, and a slow grind
    # walks straight past it. The client did not ask to be protected from one
    # terrible day; they asked not to lose 10% of their money, however slowly
    # it happens.
    horizon_months: int = 12
    release_margin_pct: float = 15.0
    # Whether the agent may sell the client's shares to close a gap at all.
    #
    # Default off, which is the unusual direction for a default and the right
    # one. Every other field here is a limit the client relaxes; this is a power
    # the client grants. Disposing of somebody's assets is not a thing to
    # inherit from a profile name, and a mandate silent on the question has not
    # said yes.
    #
    # Off, a cycle can end with the gap still open. That is the honest outcome:
    # the agent reports a promise it was not permitted to keep, rather than
    # keeping it by doing the one thing it was told not to do.
    #
    # There is deliberately no companion field ranking the two option remedies.
    # A ranking stated in a config gives the same answer on every day of every
    # market, which is not a choice but a replay; `remedy.choose` decides it
    # from the chain instead. What a client can usefully fix in advance is a
    # constraint, not an observation.
    allow_reduce_exposure: bool = False

    def budget(self, equity: float | Decimal) -> float:
        """The downside budget in dollars."""
        return float(equity) * self.downside_budget_pct / 100

    @property
    def binding_shock(self) -> float:
        """The promised shock as a negative fraction, the way the ladder uses it."""
        return -self.stress_shock_pct / 100

    @property
    def protection_dte(self) -> tuple[int, int]:
        """The days-to-expiry window protection must be bought inside.

        Floored at the horizon, because anything expiring sooner leaves the
        client uncovered for the rest of a promise that is still running. The
        ceiling is a year past it: further out costs more and buys nothing the
        promise asked for, and liquidity thins to the point where the quote is
        a suggestion.

        This deliberately ignores `config/strategy.yaml`'s `dte`, which is 20
        to 33 days. That window was calibrated against the option *history* on
        disk for a backtest of the options wheel, and it has nothing to say
        about how long a client's protection should last. Left in charge it
        bought 22-day puts against a twelve-month promise -- for 0.25 a share,
        which is what three weeks of coverage 16% out of the money is worth,
        and it is worth that because it is worth nothing.
        """
        floor = round(self.horizon_months * 30.44)
        return floor, floor + 365

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
        if not 0 <= self.release_margin_pct < 100:
            raise ValueError(
                f"{self.name}: a release margin of {self.release_margin_pct}% is "
                f"not a band. At 0 the agent buys and sells at the same line and "
                f"pays the spread to stand still; at 100 it would have to hold "
                f"the whole budget in reserve before letting any hedge go."
            )
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
    from drawdownguard.risk.limits import load_limits

    profiles = yaml.safe_load(Path(path).read_text())
    if name not in profiles:
        raise KeyError(f"unknown mandate {name!r}; have {', '.join(sorted(profiles))}")
    mandate = Mandate(name=name, **profiles[name])
    return mandate.validate_against(limits or load_limits())
