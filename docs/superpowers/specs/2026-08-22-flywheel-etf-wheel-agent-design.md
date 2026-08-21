# Flywheel — ETF Wheel Overlay Agent

**Date:** 2026-08-22
**Hackathon:** Alpaca AI Trading Agents Hackathon, August 28 – September 4, 2026
**Track:** Income & Portfolio Overlay Agents
**Status:** design approved, implementation not started

---

## 1. Context

Alpaca hackathon, 7 days, $5,000 prize pool. The Alpaca Trading API and its MCP server are mandatory, paper trading only, and the strategy must involve options. Judging criteria: P&L Performance, Technology Implementation, Creativity & Originality, Presentation & Execution.

The track description sets the bar explicitly:

> "Consistency is the bar here — a good overlay agent should run on a schedule and hold up across many cycles, not just one lucky quarter."

That yields two hard requirements, not preferences:
- the agent **runs autonomously on a schedule**, with nobody at the keyboard;
- there is **evidence of robustness across many cycles** — that is, a backtest.

**Builder constraint:** one person, 2–3 hours per day. This is the primary project risk and it drives every decision below.

**Evaluation-window constraint:** the period Aug 28 – Sep 4 contains **6 trading sessions** (Aug 28 Fri, Aug 31 Mon, Sep 1–4). A directional strategy over that span is pure noise. One week of live P&L proves nothing statistically. Therefore the backtest is the primary evidence for the judges, and the live run is evidence of autonomy and operational soundness.

---

## 2. Non-goals

This section matters more than most of the others. It exists so that the last-minute rush does not turn this into a different project.

**We do NOT forecast market direction.** Neither the LLM, nor the optimizer, nor the backtest answers "will it go up or down." If we could answer that, selling options would be irrational — trading the underlying with leverage would pay far better.

**Choosing "put or call" is not a forecast.** It is fully determined by position state:

```
CASH   → sell a put
SHARES → sell a call
```

Neither branch of that condition asks where the market is heading.

**The source of profit is the variance risk premium**, not forecast accuracy. Implied volatility is systematically higher than realized volatility: option buyers overpay for insurance. We are repeatedly on the side that gets paid for accepting risk, plus time decay accrues to the seller.

**We do not train a model.** No ML, no training, no predictor. A backtest is not training; it is rule validation and parameter calibration.

**The one thing we do assess is the volatility regime, and nothing else.** The justification is an asymmetry in autocorrelation: price direction has essentially none, while volatility clusters. Regime shifts slowly and is observable from current data. Assessing the regime is therefore a measurement of the present, not a prediction of the future.

**Regime affects size and distance, never direction:**

| Regime | Position size | Strike distance from spot |
|---|---|---|
| calm | full | closer to the money |
| elevated | reduced | further out |
| stress | minimal | significantly further out |
| crash | trading halted | — |

**We do not build a multi-agent swarm.** One agent. Rationale in section 4.

---

## 3. Strategy

**ETF wheel on SPY / QQQ / IWM, driven by an optimizer.**

### 3.1 Wheel mechanics

A state machine per ticker:

```
CASH ──sell cash-secured put──► PUT_OPEN
                                   │
              ┌────────────────────┴────────────────────┐
        expires worthless                          assigned
              │                                          │
              ▼                                          ▼
            CASH  (premium kept)                      SHARES
                                                         │
                                          sell covered call
                                                         ▼
                                                    CALL_OPEN
                                   ┌─────────────────────┴──────────────┐
                             expires worthless                    assigned
                                   │                                    │
                                   ▼                                    ▼
                                SHARES                                CASH
```

Effective basis = strike − all premiums collected. Every subsequent covered call lowers the basis further. The basis is the position's breakeven.

### 3.2 Why SPY / QQQ / IWM

- deep option liquidity, cent-wide spreads — slippage does not eat the premium;
- near-daily expirations (Mon/Wed/Fri) — flexibility on tenor;
- no earnings-gap risk and no single-company risk;
- three distinct volatility profiles (broad market / tech / small caps) — the optimizer has something meaningful to allocate between.

### 3.3 How this differs from a typical wheel bot

The standard GitHub wheel bot is hardcoded: `delta = 0.30`, `DTE = 7`. We solve an optimization problem every cycle:

**maximize** expected premium per unit of deployed capital

**subject to:**
- CVaR@95 ≤ limit
- portfolio net delta within a band
- vega budget
- per-instrument capital cap
- assignment-probability budget
- liquidity filter (spread, open interest, volume)

### 3.4 The role of the LLM

Narrow and defensible:
1. regime classification: calm / elevated / stress / crash;
2. calendar and news awareness (FOMC, CPI, expirations);
3. plain-language explanation of each decision, for the journal and the demo.

**The LLM never picks a strike, never sizes a position, and never places an order.**

Pitch formulation:

> **The LLM proposes, the math decides, the risk gate holds veto power.**

---

## 4. Architecture

### 4.1 One agent, four roles

A multi-agent design was rejected: across 6 trading sessions it does not repay the added latency, token spend, and new failure modes. Instead of a swarm, responsibilities are separated so that **the LLM is permitted in exactly one role**:

| Role | Implementation | LLM |
|---|---|---|
| Analyst | `create_agent` + dynamic prompt | yes |
| Optimizer | CVXPY, deterministic cycle node | no |
| Risk Officer | risk gate + middleware | no |
| Executor | Alpaca MCP | no |

Agent capabilities are implemented as **tools**, not sub-agents.

**The analyst is given a read-only toolset only** — `ALPACA_TOOLSETS=account,stock-data,options-data,news`. It has no order tools at all: it physically has nothing to trade with. The optimizer is not a model tool; it is a deterministic node in the cycle (see 4.2). It is exposed outward as a tool through our own MCP server, for external consumers.

### 4.2 Trading cycle

Runs on a schedule, 30 minutes after the market opens:

```
 1. reconcile ──────── pull broker positions, reconcile with our own state
 2. market_snapshot ── prices, realized volatility, IV rank, event calendar
 3. classify_regime ── [LLM] calm / elevated / stress / crash
 4. route_by_state ─── per ETF: sell put? sell call? manage?
 5. candidates ─────── load the option chain, filter by liquidity
 6. optimize ───────── [CVXPY] pick the set subject to risk constraints
 7. execute ────────── orders via Alpaca MCP, each preceded by risk.gate.veto()
 8. journal ────────── record everything: prompt, response, decision, veto, fill
```

The cycle skeleton is a deterministic LangGraph `StateGraph`. The model's freedom is confined to node 3.

### 4.3 Middleware — the control layer

Requires `langchain>=1.0`.

The risk gate protects **two independent paths**, and that redundancy is deliberate:

**Path 1 — execution (primary).** The `execute` node is deterministic; `execution/orders.py` calls `risk.gate.veto()` unconditionally before every order. The model plays no part in this path.

**Path 2 — model tools (defense in depth).** `RiskGateMiddleware` wraps the analyst's tool calls. In the normal configuration the analyst holds a read-only toolset and has no order tools, so this middleware should never fire. It exists precisely for the case of an `ALPACA_TOOLSETS` misconfiguration or a future toolset expansion: if an order tool ever reaches the model, it is blocked. A firing of this middleware is a **signal of a configuration defect**, and the journal flags it at its own severity level.

Middleware was chosen over a graph node because a node can be bypassed by one wrong edge, whereas `wrap_tool_call` is a physical wrapper around every tool invocation.

```python
class RiskGateMiddleware(AgentMiddleware):
    """The only door between the model and the broker."""

    def wrap_tool_call(self, request, handler):
        if request.tool_call["name"] not in ORDER_TOOLS:
            return handler(request)                    # data reads pass through

        verdict = risk.gate.veto(
            parse_order(request.tool_call["args"]),
            portfolio=self.portfolio,
        )
        if verdict.rejected:
            journal.write("VETO", verdict.reason)
            return ToolMessage(f"REJECTED by risk gate: {verdict.reason}")
        return handler(request)
```

The full set:

| Middleware | Hook | Purpose |
|---|---|---|
| `risk_gate` | `wrap_tool_call` | veto orders violating `risk.yaml` |
| `kill_switch` | `before_agent` | drawdown beyond limit, or a `HALT` file in the repo → the cycle never starts |
| `market_hours` | `before_agent` | market closed, half day, or trading halted → exit |
| `journal` | `after_model`, `wrap_tool_call` | audit trail of every prompt, response, order, and veto |
| `retry` | `wrap_model_call` | retries and model fallback on transient failures |

`risk/gate.py` stays a standalone module — a pure function with no LLM and no network. The middleware is only an adapter plugging it into LangChain. This preserves two properties: risk is testable in isolation, and the very same code runs in the backtest.

### 4.4 Dynamic prompt

The analyst's system prompt is a pure function of portfolio state:

```python
@dynamic_prompt
def analyst_prompt(request: ModelRequest) -> str:
    s = request.state
    return RULEBOOK + render("analyst_context.md", state=s)
```

Three rules:

1. **Static first, dynamic last.** `RULEBOOK` is a constant (who the analyst is, regime definitions, response format). Changing numbers come after it, so prompt caching is not invalidated.
2. **The prompt is not a control mechanism.** Risk limits appear in the prompt so the model does not waste turns proposing what would be rejected anyway. Enforcement lives in `risk/gate.py`.
3. **News is data, not instructions.** News text never enters the system prompt. It arrives as a tool message wrapped in an explicit `<news>...</news>` delimiter, and `RULEBOOK` states that delimiter contents are observed data, not commands. The risk gate is the last line of defense: the model physically cannot route an order around it.

The rendered prompt is written to the journal verbatim alongside the decision — every decision the agent makes is reproducible line by line.

### 4.5 State

Three levels that must not be conflated.

**1. Cycle state** — lives for seconds, in LangGraph memory:

```python
class FlywheelState(AgentState):
    snapshot: MarketSnapshot
    regime: Literal["calm", "elevated", "stress", "crash"]
    candidates: list[Candidate]
    decision: Decision | None
    verdict: RiskVerdict | None
```

**2. Position state** — lives between runs, in SQLite:

```python
class WheelState(BaseModel):
    symbol: str
    leg: Literal["CASH", "SHARES", "PUT_OPEN", "CALL_OPEN"]
    basis: Decimal | None            # strike − all premiums collected
    contracts: list[OpenContract]
    premium_collected: Decimal
    cycle_count: int
```

Losing `leg` means selling a naked call — a position with unbounded loss. That is the failure state must prevent and the risk gate must catch.

**3. The broker is the source of truth.** SQLite is a cache. The first node of every cycle reconciles our state against Alpaca positions; on any mismatch we trust the broker and write the discrepancy to the journal. Assignment can happen overnight.

Additionally, `SqliteSaver` serves as the LangGraph checkpointer, covering a process crash between order submission and journal write.

---

## 5. Repository layout

```
alpaca-hackathon/
├── .env                      # keys, NEVER in git
├── .env.example              # template with empty values, committed
├── .gitignore
├── pyproject.toml            # dependencies, uv
├── README.md                 # for judges: what it is, how to run, results
│
├── config/
│   ├── strategy.yaml         # tickers, delta ranges, DTE, roll rules
│   └── risk.yaml             # hard limits
│
├── src/flywheel/
│   ├── settings.py           # .env + yaml → typed settings
│   ├── state.py              # WheelState, Position, Decision
│   ├── store.py              # state persistence, SQLite
│   │
│   ├── market/
│   │   ├── client.py         # Alpaca wrapper for quotes and bars
│   │   ├── chain.py          # option chain loading + liquidity filter
│   │   └── features.py       # realized vol, IV rank, term structure, trend
│   │
│   ├── mcp/
│   │   ├── alpaca_client.py  # connection to the Alpaca MCP server
│   │   └── server.py         # our own MCP server: exposes the optimizer
│   │
│   ├── optimizer/
│   │   ├── candidates.py     # chain → scored candidates
│   │   ├── payoff.py         # expected return, CVaR, greeks, P(assignment)
│   │   └── model.py          # the CVXPY problem
│   │
│   ├── risk/
│   │   ├── limits.py         # limit definitions, loaded from risk.yaml
│   │   └── gate.py           # veto(decision) -> Approved | Rejected(reason)
│   │
│   ├── agent/
│   │   ├── graph.py          # StateGraph assembly
│   │   ├── state.py          # FlywheelState(AgentState)
│   │   ├── nodes/            # one file per node
│   │   ├── middleware/
│   │   │   ├── risk_gate.py
│   │   │   ├── kill_switch.py
│   │   │   ├── market_hours.py
│   │   │   ├── journal.py
│   │   │   ├── prompt.py
│   │   │   └── retry.py
│   │   ├── roles/
│   │   │   └── analyst.py    # create_agent(...)
│   │   └── prompts/
│   │       ├── analyst.md    # RULEBOOK
│   │       └── narrator.md
│   │
│   ├── execution/
│   │   ├── orders.py         # order placement via MCP, retries
│   │   └── reconcile.py      # intended vs actually filled
│   │
│   ├── journal/
│   │   └── writer.py
│   │
│   └── backtest/
│       ├── data.py           # history loading, cache
│       ├── engine.py         # replays the SAME logic over history
│       └── report.py         # Sharpe, max DD, % profitable cycles, charts
│
├── dashboard/app.py          # Streamlit: P&L, positions, greeks, decision feed
├── scripts/
│   ├── run_cycle.py          # cron entry point — one trading cycle
│   ├── run_backtest.py
│   └── healthcheck.py
├── tests/
├── data/                     # history cache, gitignored
├── journal/                  # decision journal, IN GIT — evidence for judges
└── .github/workflows/trade.yml
```

### Rationale for the key boundaries

**`risk/` is a top-level module** because it is the most important part of the system and must be verifiable in isolation: no LLM, no network, no broker. A pure function of "proposed decision + portfolio state → Approved | Rejected(reason)." Its tests are written first.

**`optimizer/` knows nothing about Alpaca or the LLM** — numbers in, chosen set out. That is what lets it run in the backtest without a single network call.

**`backtest/engine.py` calls the same `optimizer/` and `risk/` modules** the live agent uses, not copies of them. Otherwise the backtest validates code that is not in production and the argument to the judges collapses.

**`agent/prompts/` are separate files** because prompts get rewritten dozens of times; strings in code turn every prompt edit into a logic diff.

**`journal/` is committed to git.** Every run records what the agent saw, what it chose, why, and what filled. By the end of the week this is a ready-made chronology of autonomous operation: evidence for judges and raw material for the demo video.

**`mcp/server.py`** is our own MCP server exposing the optimizer outward as a tool. It turns the project from "a bot" into "a tool," which speaks directly to Creativity & Originality.

**Language:** all project artifacts — code, comments, specs, README, journal, commit messages — are written in English.

---

## 6. Dependencies

```toml
[project]
name = "flywheel"
requires-python = ">=3.11"
dependencies = [
    "alpaca-py",
    "langchain>=1.0",          # middleware landed here
    "langgraph",
    "langchain-anthropic",
    "mcp",
    "cvxpy",
    "numpy", "pandas", "scipy",
    "pydantic", "pydantic-settings",
    "pyyaml",
    "streamlit", "plotly",
    "structlog",
]

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "ruff", "mypy"]
```

Package manager: `uv`. On this machine use `python3`, not `python`.

---

## 7. Security and secrets

- Secrets live **only** in `.env`, which is listed in `.gitignore`. A `.env.example` with empty values is committed.
- Keys are **not** placed in any MCP client config stored in the repository. The MCP server receives them from the environment.
- `ALPACA_PAPER_TRADE=true` is a safety interlock. `settings.py` **fails at startup** if the value is not `true`. Real money cannot be touched by accident.
- `FLYWHEEL_ENV` separates `dev` (account #1, pre-hackathon shakedown) from `judging` (clean account #2 whose P&L is scored).

```bash
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_PAPER_TRADE=true
ANTHROPIC_API_KEY=
FLYWHEEL_ENV=dev
```

---

## 8. Risk model

`risk.yaml` holds hard limits, checked deterministically:

| Limit | Meaning |
|---|---|
| max_position_pct | capital share per instrument |
| max_deployed_pct | total capital in open positions |
| max_drawdown_pct | kill-switch trigger |
| max_net_delta | portfolio net delta band |
| max_vega | vega budget |
| max_assignment_prob | assignment-probability budget per cycle |
| min_open_interest | liquidity filter |
| max_spread_pct | liquidity filter |
| forbid_naked | absolute ban on uncovered positions |

Limits are set **from backtest results** — derived from the worst historical cycle, not guessed.

Premium is a buffer, not protection. Against a genuine crash it is useless. Three design consequences follow, already built in: shrink size and widen strike distance as volatility rises; wheel only broad ETFs that survive crises; enforce a hard drawdown kill switch the LLM cannot bypass.

---

## 9. Backtest

Its purpose is three specific things, none of which is training:

1. **Robustness evidence.** The same rules replayed over 3–5 years: how many cycles, what share were profitable, maximum drawdown, behavior in March 2020 and through 2022.
2. **Parameter calibration:** delta, DTE, position size — derived from data rather than invented.
3. **Locating the break point:** how bad the worst cycle was, with risk limits set from there.

**Overfitting guard:** parameters are calibrated on an early slice of history and validated on a later one (walk-forward). If results survive only under hand-picked constants, that is fitting noise and must be acknowledged, not hidden.

Report: Sharpe, maximum drawdown, share of profitable cycles, per-cycle return distribution, equity curve, and comparison against buy-and-hold for each ticker.

---

## 10. Testing

The order in which tests are written reflects priority:

1. **`risk/gate.py`** — first. Table-driven tests: each limit violated individually, asserting rejection and the reason text. No network, no LLM.
2. **The wheel state machine** — every transition, including assignment mid-cycle and broker mismatch.
3. **`optimizer/`** — solution properties: constraints hold, and a solution exists when the candidate set is empty.
4. **Middleware** — that a veto actually blocks the tool call, and that the kill switch stops the cycle.
5. **Integration** — one full cycle against a mocked Alpaca.

---

## 11. Deployment

The live run uses GitHub Actions on a cron schedule. Rationale: free, no infrastructure to run, the laptop can be off, and the journal is committed back to the repository, serving as the audit trail for judges by itself.

`scripts/healthcheck.py` verifies before each cycle: keys valid, account funded, market open, state reconciled with the broker.

---

## 12. Timeline

**August 22–27 — all development.** Shakedown on paper account #1.
**August 28 – September 4 — run, observe, film, pitch only.** Clean account #2, whose P&L is scored.

Given 2–3 hours per day, the agent must be fully autonomous and deployed by August 28. The hackathon week is not budgeted for development.

The day-by-day breakdown lives in a separate implementation plan.

## 12.1 Cut line

The scope exceeds what reliably fits into 2–3 hours a day across six days. Priority is therefore fixed now, not in a panic on August 27.

**Mandatory minimum — without this there is nothing to submit:**
the wheel state machine, `risk/gate.py` with tests, Alpaca MCP integration, order execution with reconciliation, the journal, scheduled runs, README.

**Core value — what makes this different from a stock wheel bot:**
the CVXPY optimizer, LLM regime classification, the backtest with its report.

**First to be cut if time runs out:**
the Streamlit dashboard (replaced by backtest report charts), our own MCP server, the narrator role, the third ticker (IWM).

The order is deliberate: an agent that reliably turns the wheel and cannot breach a limit beats an agent with a beautiful dashboard that died on Wednesday morning.

---

## 13. Open questions

To be verified immediately after registration and key issuance:

1. **Submission format and exact deadline time.** The public lablab.ai page returns 403 to automated requests and shows only a short description in a browser without login. The typical lablab set is a public repository, a 2–5 minute demo video, slides, and a description — but this must be confirmed in the account dashboard.
2. **Paper account starting capital.** One contract is 100 shares; a covered call on SPY requires tens of thousands of dollars. Alpaca defaults to roughly $100k, which supports only a couple of parallel wheels. Position size must be an optimizer parameter, never a constant. If capital is short: reset the account with a larger starting balance, or shift the universe toward cheaper ETFs.
3. **Options data entitlement.** 15-minute delayed data requires Algo Trader Plus; confirm what the free tier provides and whether it suffices for a cycle running 30 minutes after the open.

The earlier open question about a "base portfolio source" for the overlay is resolved: no base portfolio is needed. The wheel **is** the overlay — the put leg acquires shares, the call leg overlays them. Every cycle starts from CASH.

---

## 14. Success criteria

| Judging criterion | How it is addressed |
|---|---|
| P&L Performance | 6 live sessions plus a 3–5 year backtest as the primary evidence |
| Technology Implementation | LangGraph, a middleware control layer, CVXPY, our own MCP server, risk tests |
| Creativity & Originality | an optimizer instead of a hardcoded delta; the risk gate as unbypassable middleware; an MCP server exposing the optimizer outward |
| Presentation & Execution | the decision journal as a ready chronology of autonomous operation; a Streamlit dashboard; a demo video built from real runs |
