# Alpaca's greeks against ours

Measured 2026-08-24 on 781 SPY put contracts expiring 2026-08-28 to 2026-09-08,
with SPY at 764.18. Alpaca's implied volatility was used as the input, so this
compares the greek formulas rather than the volatility surfaces.

## What was being tested

`contract_vega`'s docstring asserts that three vega conventions are in
circulation and that they differ by factors of 100: textbook vega is per share
per 1.00 of volatility, Alpaca quotes per share per point, and traders speak
per contract per point.

That assertion had never been measured. It was a belief written down
confidently, which is the most dangerous kind. If it were wrong, every vega in
the project would be off by a hundred, `max_vega: 500` would be either
unreachable or completely inert, and no individual number would look wrong.

## Result

| Quantity | Comparison | Median |
|---|---|---|
| Delta | ours − Alpaca | −0.0005 |
| Vega | `bs_vega` ÷ Alpaca's `vega` | 101.3 |

**Delta matches directly.** Both are per share and signed the same way. No
conversion is needed and none is applied.

**Vega is confirmed at ×100.** Alpaca quotes vega per share per point, exactly
as the docstring claimed. `contract_vega` divides `bs_vega` by 100 for the
one-point move and multiplies by 100 shares per contract; the two cancel, and
the result is numerically equal to `bs_vega`. That cancellation is why the
function exists under a name instead of being written inline — the units are
invisible in the arithmetic.

## The residual 1.3 percent, and what it is not

The ratio is 101.3, not 100.0. That gap is real and unexplained by the
convention. Candidates, none confirmed: a different risk-free rate (this
project uses a flat 0.04), SPY's dividend yield, which the model does not
carry, and a different convention for time to expiry — calendar days over 365
here, against whatever Alpaca uses intraday.

The distinction worth holding on to: **this check was built to catch a factor
of 100, and it did so decisively.** A 1.3 percent residual on a test designed
to separate 1 from 100 says nothing either way about the third decimal place.
It would be a misreading to now cite this note as evidence that the greeks
agree to within 1.3 percent — the experiment does not support that claim, and
the plan's 0.5 percent tolerance was never met.

Nothing in the project consumes Alpaca's greeks. Delta, vega and assignment
probability are all recomputed from the implied volatility with the same
Black-Scholes code that prices the backtest, so the live path and the
historical path cannot silently disagree about what a delta is. Alpaca's greeks
are used here as an independent second opinion and nowhere else.

## What would make this tighter

Fit the rate and dividend yield implied by Alpaca's own greeks and see whether
the residual collapses. Worth doing only if a vega limit ever binds in
practice; today capital binds first — see the note in `config/risk.yaml`.
