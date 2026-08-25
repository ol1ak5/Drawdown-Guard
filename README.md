# Flywheel

An autonomous ETF wheel overlay agent, running on Alpaca paper trading.

It sells cash-secured puts on SPY, QQQ and IWM; if assigned, it holds the
shares and sells covered calls against them until they are called away; then it
starts again. The premium is the income. The agent decides how far out of the
money to go, how many contracts, and — most often — whether to trade at all.

**The LLM proposes, the math decides, the risk gate holds veto power.**

## What is actually automated

A scheduled run each weekday, thirty minutes after the open:

1. **Reconcile** — ask the broker what is held and believe it, not local state.
2. **Snapshot** — spot, realised volatility, implied volatility, IV rank.
3. **Regime** — an LLM classifies `calm | elevated | stress | crash`.
4. **Route** — put or call, decided by the wheel's leg, not by the model.
5. **Candidates** — filter the chain to contracts that are choices at all.
6. **Optimize** — convex program: maximise premium subject to CVaR, directional
   exposure and position size. Allocating nothing is a valid answer.
7. **Execute** — every order passes the risk gate. There is no bypass.
8. **Journal** — write the decision, including the decision to do nothing.

## Where the safety actually lives

The interesting claim is not that an LLM trades. It is that an LLM's mistake
cannot cost anything.

- **The analyst holds no order tools.** It is constructed with a read-only
  toolset. There is no code path from its output to an order.
- **Its output can only tighten.** `calm → elevated → stress → crash` is
  ordered; each step narrows the delta band and shrinks the size multiplier.
  No value it can return loosens a limit.
- **It cannot fail into permission.** A malformed or unreachable answer becomes
  `stress`, never `calm`. An analyst that could not answer is not evidence of a
  calm market.
- **The risk gate is unconditional.** `submit_order` calls `veto` first; no
  flag, argument or configuration skips it. `--dry-run` is checked *after* the
  gate, so a dry run of a forbidden order still reports the refusal.
- **A tripwire watches the gate.** If an order tool ever reaches the analyst,
  it is blocked and journalled at `defect` severity — not `veto`. A veto is the
  design working; a defect means it leaked.
- **`buying_power` is never read.** On this paper account it is four times
  equity. Sizing against it would quadruple every position while every limit
  still reported itself satisfied.

## Kill switches

```bash
touch HALT && git add HALT && git commit -m "halt" && git push
```

The next scheduled run stops before doing anything. It works from a phone and
needs no code to be run. Remove the file to resume.

There is also an automatic one: drawdown beyond `max_drawdown_pct` halts the
cycle in the first node, before any market data is fetched.

## Honest accounting

Backtest over 2024-02 to 2026-08, three symbols, 1,000,000 of capital:

| symbol | premium only | with collateral at 4.5% |
|---|---|---|
| SPY | 1.01% | 14.15% |
| QQQ | 6.29% | 15.80% |
| IWM | 3.07% | 14.08% |

Both columns matter. A cash-secured put ties up cash, and in a real account
that cash earns Treasury yield — quoting only the first column describes a
strategy nobody would run. Quoting only the second claims credit for the
Treasury. `--cash-rate 0` reproduces the left column exactly.

Four quantities in the backtest are **modelled, not measured**, and each is
named in `BacktestResult.params`: the execution price (bar close less a
haircut, because history has no bid), the implied volatility (back-solved from
that close), the collateral yield (a flat rate, not the daily bill series), and
open interest — which does not exist historically at all, so the check is
disabled explicitly rather than fed an invented number.

## Running it

```bash
uv sync
cp .env.example .env          # fill in your keys; .env is gitignored
uv run python3 scripts/healthcheck.py     # exits non-zero with a reason
uv run python3 scripts/run_cycle.py --dry-run
uv run python3 scripts/run_backtest.py --symbol SPY
uv run pytest
```

`ALPACA_PAPER_TRADE=true` is a hard interlock: `Settings` refuses to construct
without it.

## Layout

```
src/flywheel/
  agent/        the cycle graph, its nodes, the analyst, the guards
  market/       Alpaca adapters: account, chain, snapshot
  optimizer/    candidate filtering, Black-Scholes, the convex program
  risk/         the limits and the gate that enforces them
  execution/    order submission and broker reconciliation
  backtest/     the same modules, driven by history
  journal/      append-only decision record
docs/notes/     what was measured, and what turned out not to be true
```

The backtest imports the live optimizer and the live risk gate rather than
reimplementing them. That is the whole argument for trusting it: a backtest
running different code measures a strategy nobody is going to trade.
