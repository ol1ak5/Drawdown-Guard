"""How much of the portfolio's risk is really one bet.

Selling option premium is selling risk. A book that is short puts on SPY, QQQ
and IWM and short a call on SPY looks like four independent income streams and
is, mostly, a single wager that US large-cap equities do not fall. Those three
ETFs correlate around 0.85 to 0.95. Four positions, one risk.

This module measures that, so a limit can be placed on it.

WHAT IS BEING COMPUTED, EXACTLY
-------------------------------
**Component contribution to portfolio variance**, from observed daily returns.

That choice matters and there is no neutral option. Marginal contribution to
variance, contribution to volatility, beta contribution, a fitted factor model
and a PCA decomposition all answer "how concentrated is this?" with different
numbers, and quoting one while implying another is how a risk figure becomes
decoration.

Component contribution is used because it is reproducible from data already on
hand, it sums exactly to 100% by construction, and it needs no estimated factor
model — with four instruments and two years of history, a factor model would be
fitting more parameters than the data supports.

WHAT IS ASSUMED RATHER THAN DISCOVERED
--------------------------------------
The mapping from instrument to risk bucket is **assigned by hand**: TLT is
rates, everything else is equity. That is an assumption stated in a constant,
not an output of the analysis, and it must be read as such. A discovered
grouping would need a clustering step this project does not have the data to
support honestly.
"""

from collections.abc import Mapping

import numpy as np

# Assigned, not derived. See the module docstring.
RISK_BUCKETS: dict[str, str] = {
    "SPY": "equity",
    "QQQ": "equity",
    "IWM": "equity",
    "DIA": "equity",
    "ARKB": "crypto",
    "TLT": "rates",
    "IEF": "rates",
    "GLD": "commodity",
}

DEFAULT_BUCKET = "equity"


def bucket_of(symbol: str) -> str:
    """The risk family an instrument belongs to.

    Unknown symbols default to equity — the crowded bucket, not an empty one.
    Defaulting an unrecognised ticker into its own category would let anything
    unmapped look like diversification for free.
    """
    return RISK_BUCKETS.get(symbol.upper(), DEFAULT_BUCKET)


def component_contributions(
    exposures: Mapping[str, float], returns: Mapping[str, np.ndarray]
) -> dict[str, float]:
    """Each position's share of total portfolio variance, summing to 1.

    `exposures` are signed dollar exposures per symbol — what the position is
    worth in terms of the underlying, not the premium collected. `returns` are
    daily return series, which need not be the same length: they are trimmed to
    the shortest so the covariance is computed over a common window rather than
    over whatever history each symbol happens to have.

    Returns an empty dict when the portfolio has no variance to divide — an
    empty book, or a single position with no observed movement. Zero risk
    contributions are not the same as an evenly split one, and returning an
    empty answer says so.
    """
    symbols = [s for s in exposures if s in returns and len(returns[s]) > 1]
    if not symbols:
        return {}

    width = min(len(returns[s]) for s in symbols)
    if width < 2:
        return {}

    matrix = np.vstack([np.asarray(returns[s])[-width:] for s in symbols])
    weights = np.array([float(exposures[s]) for s in symbols])
    if not np.any(weights):
        return {}

    covariance = np.cov(matrix)
    if covariance.ndim == 0:
        covariance = covariance.reshape(1, 1)

    variance = float(weights @ covariance @ weights)
    if variance <= 0:
        return {}

    # Component contribution: w_i * (Cov @ w)_i, which sums to w'Cov w exactly.
    contributions = weights * (covariance @ weights)
    return {s: float(c / variance) for s, c in zip(symbols, contributions, strict=True)}


def by_bucket(
    exposures: Mapping[str, float], returns: Mapping[str, np.ndarray]
) -> dict[str, float]:
    """Variance contribution grouped into risk families, as percentages.

    This is the number a mandate constrains: not "how much SPY do you hold" but
    "how much of what you are selling is the same risk".
    """
    grouped: dict[str, float] = {}
    for symbol, share in component_contributions(exposures, returns).items():
        grouped[bucket_of(symbol)] = grouped.get(bucket_of(symbol), 0.0) + share * 100
    return grouped


def dominant(
    exposures: Mapping[str, float], returns: Mapping[str, np.ndarray]
) -> tuple[str, float]:
    """The largest risk family and its share, or ("none", 0.0).

    The single figure a limit is checked against. A portfolio with nothing in
    it is not concentrated, and reporting 100% of nothing would fail every
    check for no reason.
    """
    grouped = by_bucket(exposures, returns)
    if not grouped:
        return "none", 0.0
    name = max(grouped, key=lambda key: grouped[key])
    return name, grouped[name]


def describe(
    exposures: Mapping[str, float], returns: Mapping[str, np.ndarray], width: int = 20
) -> str:
    """A plain-text bar chart of where the risk actually sits.

    Written for the journal and the status page. The point it has to make in
    one glance is that a list of four positions can be a single bet.
    """
    grouped = by_bucket(exposures, returns)
    if not grouped:
        return "no measurable risk concentration (the book is empty or flat)"
    lines = []
    for name, pct in sorted(grouped.items(), key=lambda kv: -kv[1]):
        filled = int(round(pct / 100 * width))
        lines.append(f"{name:<10} {'#' * filled}{'.' * (width - filled)} {pct:5.1f}%")
    return "\n".join(lines)
