# Flywheel

**An AI trading agent you are allowed to distrust.**

Every "AI trades the market" demo shows a chart going up. None of them let you
check. This one publishes its refusals next to its fills, marks its own results
as probably wrong where they look too good, and is built so that the language
model inside it **cannot** place a bad trade — not because we asked it nicely,
but because it has no hands.

Live decisions → **[status page](https://ol1ak5.github.io/Flywheel-Agent/)** ·
Backtest → **[report](docs/backtest-report.md)**

---

## The problem, in one paragraph

If you own index ETFs, you are leaving income on the table. Selling options
against your holdings — the "wheel" — is how professionals harvest it, and
funds that do it for you (JEPI, QYLD, XYLD) run tens of billions of dollars and
charge you 0.35–0.60% a year for the privilege.

Doing it yourself means opening the option chain every week and answering four
questions correctly: put or call, which strike, which expiry, how many. Get one
wrong and a year of premium disappears in a day. Most people either overpay a
fund or don't bother.

## What Flywheel does

It runs the wheel for you, on your own account, and shows its work.

Every weekday, thirty minutes after the open, it wakes up and asks: *what
should I do right now?* Then it does one of two things — place a specific
trade, or explain why it is sitting this one out. **Sitting it out is the most
common answer**, and it is written down just as carefully as a trade.

```
Regime: stress — the variance risk premium is compressed, notably in QQQ
Sold:   QQQ 660 put, 25 Sep, 1 contract, delta 0.145
Sold:   IWM 280 put, 25 Sep, 4 contracts, delta 0.147
Skipped: SPY — no contract passed the filters
```

That is a real cycle, from a real run, on a real paper account.

## Why the AI can't hurt you

This is the part worth stealing.

Most AI trading projects put the model in charge and bolt on guardrails.
Flywheel does the opposite: the model is **structurally incapable** of a bad
trade. Three properties, none of which depend on the prompt being obeyed:

**It has no hands.** The analyst is connected to the broker with a read-only
toolset. There is no order tool in its reach and no code path from its output
to a trade. If it decided to sell everything, nothing would happen.

**Its answer can only make the agent more careful.** It returns one of `calm →
elevated → stress → crash`. Every step *narrows* how far out of the money the
agent goes and *shrinks* how much it risks. There is no answer it can give that
loosens a limit.

**A broken answer means caution, not confidence.** If the model returns
nonsense, times out, or is unreachable, the agent proceeds as if the market
were stressed — never as if it were calm. A default that reads "carry on as
normal" is how an outage becomes a position.

So: the LLM proposes, the math decides, and a deterministic risk gate holds
veto power over both. The worst a compromised model achieves is a skipped day.

## Why you can check our numbers

The fastest way to spot a dishonest backtest is that it never says anything
against itself. Ours does, out loud, in the report:

**We underperform the benchmark.** Over the same window CBOE's PUT index
returned 36.5%. We returned 14–16%. That is in the report, not omitted.

**Our Sharpe ratio is probably a bug.** The report prints 3.0–6.0 and
immediately says this is more likely a defect than an edge — the published
index for this exact strategy runs under 1 over most decades. It names the
cause too.

**We separate our income from the Treasury's.** A cash-secured put ties up
cash, and that cash earns interest. Most of our return *is* that interest. We
print both columns, because quoting the total would be claiming credit for the
US Treasury.

**We say what is modelled rather than measured.** Four numbers in the backtest
are estimates, not observations — and each one is named, in the report, not in
a footnote.

## Kill switch

```bash
touch HALT && git add HALT && git commit -m "halt" && git push
```

The next run stops before doing anything. It works from a phone, needs no code,
and needs no access to the machine. There is an automatic one too: a drawdown
past the configured limit halts the cycle before it reads a single price.

## How it works

Eight steps, once a day.

| | | |
|---|---|---|
| 1 | **Reconcile** | Ask the broker what is held. Believe it, not our own records. |
| 2 | **Look** | Spot, realised volatility, implied volatility, IV rank. |
| 3 | **Judge** | The LLM names the regime. This is its only job. |
| 4 | **Route** | Put or call — decided by where the wheel is, not by the model. |
| 5 | **Filter** | From ~2,000 contracts down to the handful that are choices at all. |
| 6 | **Optimise** | Convex program: most premium, subject to tail risk and exposure. |
| 7 | **Gate** | Every order faces the risk gate. There is no bypass, no flag, no override. |
| 8 | **Write it down** | Including — especially — the decision to do nothing. |

Built with LangGraph, Alpaca's MCP server, CVXPY, and Gemini.

## Try it

```bash
uv sync
cp .env.example .env                          # your Alpaca paper keys
uv run python3 scripts/healthcheck.py         # says why it won't trade, if it won't
uv run python3 scripts/run_cycle.py --dry-run # decides, journals, submits nothing
uv run python3 scripts/run_backtest.py --symbol SPY
uv run pytest                                 # 251 tests
```

`ALPACA_PAPER_TRADE=true` is a hard interlock — the program refuses to start
without it. This has never traded real money and cannot.

## What we would not claim

The honest limits, since the whole pitch is that we state them:

- **20 cycles** of real-quote history. That is a description of one window, not
  a property of the strategy.
- **No crash in the window.** Feb 2024 to Aug 2026 contains no 2008 and no
  March 2020. Writing puts is short a crash, and both CBOE indices lost around
  a third in 2008. What happens to us in one is untested.
- **Early assignment is ignored.** We resolve at expiry, which flatters the
  result.
- **The market beat us.** Buy and hold outperformed over this window, as it
  usually does in a bull market. The wheel trades upside for income and lower
  drawdown; that is the deal, and we are not going to pretend otherwise.

## Layout

```
src/flywheel/
  agent/       the cycle, its nodes, the analyst, the guards
  market/      Alpaca adapters: account, chain, snapshot
  optimizer/   candidate filtering, Black-Scholes, the convex program
  risk/        the limits, and the gate that enforces them
  execution/   order submission and broker reconciliation
  backtest/    the same modules, driven by history
  journal/     append-only record, and the status page built from it
docs/notes/    what we measured, and what turned out not to be true
```

The backtest imports the live optimizer and the live risk gate instead of
reimplementing them. That is the whole reason to trust it: a backtest running
different code from the agent measures a strategy nobody is going to trade.
