# 🛡️ Drawdown Guard

**Investors can say how much they can afford to lose in the worst scenario. But portfolios can't keep that promise on their own.**

So we built an agent that does.

Drawdown Guard is an autonomous AI trading agent that checks a portfolio every weekday against its client's downside limit, and steps in with an options overlay the moment it doesn't.

**The market moves. The loss mandate doesn't. Drawdown Guard keeps the two in line.**

🔴 **[Demo Application Platform](https://ol1ak5.github.io/Drawdown-Guard/)** · 📓 **[Journal](journal/)**

---

## 🎯 The problem

**Just knowing a loss tolerance limit is not a loss control.**

Investors can decide how much downside they can accept. But once the portfolio is built, that number doesn't enforce itself.

Markets move. Positions change. Options expire. New exposure is added.

**The problem is not knowing what the market will do next.** It's keeping the portfolio aligned with the risk limit the client already chose. That's exactly what Drawdown Guard is built to solve.

## 💡 The solution

Drawdown Guard stands between the client's promise and the portfolio, turning a downside limit into a continuously monitored constraint the portfolio is checked against.

Every weekday, the agent wakes up and asks one question: 
> **If the market fell today, would the portfolio still be inside the number it was given?**

If yes, the agent records the result and stays on guard.
If no, it measures the existing gap, and evaluates the available options on today's actual option chain to bring the portfolio back within its mandate.

## 👤 One client, one promise

For this hackathon, we turn the problem into a concrete situation.

### Initial settings

Our simulated client has a c. $100,000 portfolio that includes:

| Ticker | Shares | Entry price | Value | Exposure | Description |
|---|---|---|---|---|---|
| **IWM** | 100 | 297.34 | **$29,734** | Equity | Small-cap index |
| **XLF** | 900 | 58.25 | **$52,425** | Equity | Financial sector |
| **BIL** | 100 | 91.66 | **$9,166** | Fixed income | 1-3 month T-bills |
| **Cash** | n.a. | n.a. | **$8,675** | Liquidity | Used for the hedge |

### The promise

The client's mandate is simple:

> *“In the worst case, I can tolerate a 10% loss per year.”*

**Just two numbers:**

- 🔟 **10%** - the most the client can lose in our case. Roughly $10,000 on this account. The exact figure is fixed the day the promise opens.
- 📆 **12 months** - the window that promise has to hold.

### Activity during the hackathon

The client changes the portfolio mid-flight:
- Sells the whole XLF position of 900 shares on September 2nd
- Buys 100 shares of AAPL on September 3rd

The portfolio is intentionally not static. A client who never touches their allocation isn't the point. That gives Drawdown Guard a real job - it keeps the changing portfolio aligned with a fixed risk mandate.

## ⚙️ How the agent actually works

Seven steps presented as five nodes in the LangGraph cycle. Every half hour while the market is open. Fully autonomous.

| # | Step | The agent does | Node |
|---|---|---|---|
| 1️⃣ | **Reconcile** | Asks the broker what is actually held. Never assumes | `reconcile` |
| 2️⃣ | **Mandate** | Turns the client's tolerance into a fixed dollar budget. The LLM acts as a risk analyst: it is handed the material and not the conclusion, and names what needs attention | `mandate` |
| 3️⃣ | **Stress** | Runs the book down the whole descent and measures the uncovered risk | `mandate` |
| 4️⃣ | **Protect** | Builds valid hedge candidates. The LLM chooses between eligible structures | `protect` |
| 5️⃣ | **Gate** | Checks every order against hard limits before it can reach the broker | `execute` |
| 6️⃣ | **Execute** | Sends the approved orders, then reads back what the broker actually did | `execute` |
| 7️⃣ | **Journal** | Writes down what happened and why, in plain language, with an LLM | `journal` |

## 🚩 Where the AI is

The deterministic risk engine calculates the numbers. The LLM reasons over the decisions that require judgment.

**LLM call #1 - Risk analysis**

On every cycle, the LLM receives the material and answers the following question:

> *Given this portfolio, this mandate and this existing protection, which positions carry a risk issue that needs attention, and what should be reviewed?*

**It is not given the conclusion.** `uncovered_risk` is a single number the arithmetic has already worked out, and handing it over would turn the call into a paraphrase. What it gets instead is the raw material: holdings position by position, the promise and what it is measured against, the option legs held **listed separately and not paired with the holdings**, and what the book loses at each stress rung.

So "nine puts and no shares behind them" is something the model has to put together rather than agree with.

For example:

```
Portfolio change:
XLF: 900 → 0

Existing protection:
9 XLF puts

LLM:
Risk issue: existing XLF protection no longer corresponds to an equity position.
Recommendation: review the XLF hedge for removal.
```

**LLM call #2 - Protection choice**

When protection is required, the LLM chooses between eligible hedge structures. **Eligible is decided in code, not by the model:** a candidate is only shown to it if the structure closes the risk in full and expires. So the model cannot leave the promise broken and cannot sell the client's shares - it chooses between hedges that all keep the promise, on the one question that is genuinely about today's prices.

If the LLM fails, returns an invalid choice or names a structure that was not offered, the deterministic rule remains the fallback.

Every protection choice records:
- `decided_by` - the model or the rule
- `rule_would_have` - what the rule would have taken
- `rule_because` - the rule's own reasoning

The rule runs on every sleeve regardless, so a disagreement between the model and the arithmetic is visible in the journal rather than inferred.

**LLM call #3 - The client's note**

After the orders have gone and their fills have been read back, the LLM writes the paragraph a client reads. Nothing is left to decide by then, so it can be unclear but it cannot be expensive.

**Where the AI cannot reach.** No LLM output is read by an order. `protect` sizes the hedge from `uncovered_risk`, which is settled before the model is called; the deterministic Gate is the last check either way. A finding the cycle did not act on is recorded as `review.unaddressed` - either the model was wrong or the checks have a blind spot, and neither is discoverable if the two are never compared.

## 🔗 The Chain of Decision

**1️⃣ How much loss can the client tolerate?** 

The client only defines the maximum loss he is willing to tolerate:

> *Maximum drawdown: 10%*

On 28 August 2026, at the moment the promise opened, the portfolio was worth $99,978. So the maximum loss allowed by the mandate is $9,998. That becomes the drawdown budget.

**$9,998 is not the amount we expect the client to lose.** It's the maximum loss the protection framework is allowed to leave exposed. The objective is always to lose less.

**2️⃣ How much of that budget belongs to each position?**

Our client's portfolio contains more than one instrument:
- XLF: 900 shares
- IWM: 100 shares

The agent allocates the budget proportionally to each instrument's weighted contribution to portfolio risk, as specified by the mandate.

**For this portfolio, that gives:**

```
XLF → $6,385
IWM → $3,613
────────────
Total $9,998
```

**3️⃣ How do we protect the portfolio?**

Drawdown Guard says: "With options".

| Protection | How it works | Cost | Upside |
|---|---|---|---|
| 🛡️ **Protective put** | Buy a put against the shares | Pay premium upfront | Keep all upside |
| 🎯 **Collar** | Buy a put and sell a call | Call premium helps pay for the put | Upside is capped at the call strike |

**The shares are never sold to cover the existing gap.** The client can sell shares at any time, but the agent doesn't liquidate them simply to close the drawdown gap.

**4️⃣ Which option should we use today?**

The agent compares both structures using the live option chain. The LLM chooses between candidates that have already passed the deterministic risk filters.

The decision depends on:
- the current option prices;
- implied volatility;
- the cost of protection;
- the client's constraints.

**5️⃣ How many option contracts do we need?**

The number of contracts comes from **the number of shares**. The objective is to avoid leaving part of the portfolio exposed. A hedge over half a portfolio is not half a promise kept, it's a promise broken at half the price.

One standard equity-option contract covers 100 shares.

```
XLF:  900 shares → ceil(900/100) = 9 contracts
IWM:  100 shares → ceil(100/100) = 1 contract
```

**6️⃣ Which strike fits the mandate?**

Once the agent knows how many contracts are required, it has to decide which strike to buy. The strike **always** comes from the budget.

```
XLF   share of the budget $6,382
  fall to the strike    (58.17 − 54) × 900  =  $3,753
  premium                   2.64 × 9 × 100  =  $2,376
                                               ───────
  worst case                                   $6,134   ≤  $6,382  ✅

IWM   share of the budget $3,616
  fall to the strike  (296.65 − 275) × 100  =  $2,165
  premium                  14.25 × 1 × 100  =  $1,425
                                               ───────
  worst case                                   $3,590   ≤  $3,616  ✅
```

> The premium is part of the risk budget.
> Protection is not free: the cost of buying the hedge is charged against the same loss allowance.

### Execution price

Every order is a limit order. After Day 1 unfilled orders, we allowed the limit price to reach past the crossed price by a fraction of the spread. That fraction was a quarter, and on Day 2 it was still not enough - so it is now the whole spread.

On Day 1, the initial limits were exactly at the ask:

| Options | Our limit | Ask, minutes later | Filled |
|---|---|---|---|
| XLF 54 put ×9 | 2.71 | 2.78 | ❌ |
| IWM 275 put ×1 | 14.48 | 14.60 | ❌ |

On Day 2, one of the two filled:

| Options | Our limit | Result |
|---|---|---|
| IWM 275 put ×1 | 15.06 | ✅ filled at 15.06 |
| XLF 54 put ×9 | 2.78 | ❌ still working at the close |

The IWM put is the first protection this account actually holds.

The XLF order sat a cent under the market all day. It was priced at 14:19 against a 2.72 ask and sent at 2.78; by the close the ask was 2.87 against a 2.63 bid. A day limit is set once and cannot chase, so the tolerance is not really "how much are we willing to overpay" - it is "how far may the offer drift before the promise goes unheld for another day".

The trade is not symmetric, which is the whole argument for crossing the full spread: $216 of possible overpayment against $52,000 of exposure left unprotected overnight. It remains a limit order. A market order on an option with a 9% spread is not the faster version of this, it is the unbounded one.


**7️⃣ What does the protection actually do?** 

The answer is below. Without protection, losses continue to grow as the market falls. With the options in place, the loss reaches a floor.

**Protective put**

| Market falls | Without the agent | **With the agent** | Premium paid | **Floor + Premium** | Drawdown budget | Promise |
|---|---|---|---|---|---|---|
| -10% | $8,202 | **$5,923** | $3,801 | **$9,724** | $9,998 | ✅ |
| -20% | $16,404 | **$5,923** |$3,801 | **$9,724** | $9,998 | ✅ |
| -35% | $28,708 | **$5,923** | $3,801 | **$9,724** | $9,998 | ✅ |
| -50% | $41,011 | **$5,923** | $3,801 | **$9,724** |$9,998 | ✅ |
| -90% | $73,820 | **$5,923** | $3,801 | **$9,724** |$9,998 | ✅ |
| -100% | $82,022 | **$5,923** | $3,801 | **$9,724** |$9,998 | ✅ |

**Collar**

| Market falls | Without the agent | **With the agent** | Net premium | **Floor + Net premium** | Drawdown budget | Promise |
|---|---|---|---|---|---|---|
| -10% | $8,202 | **$5,923** | $2,408 | **$8,331** | $9,998 | ✅ |
| -20% | $16,404 | **$5,923** | $2,408 | **$8,331** | $9,998 | ✅ |
| -35% | $28,708 | **$5,923** | $2,408 | **$8,331** | $9,998 | ✅ |
| -50% | $41,011 | **$5,923** | $2,408 | **$8,331** |$9,998 | ✅ |
| -90% | $73,820 | **$5,923** | $2,408 | **$8,331** |$9,998 | ✅ |
| -100% | $82,022 | **$5,923** | $2,408 | **$8,331** |$9,998 | ✅ |

For a collar, the net premium the client pays is calculated as s premium paid for the put reduced by the premium received by selling a call:

```
$3,801 - $1,393 =  $2,408 
```

**8️⃣ When the agent steps in**

The trigger is mechanical:

```
uncovered_risk = worst_loss(current_book) − budget

uncovered_risk > 0
        ↓
    Action Required
```

The portfolio can become uncovered for several reasons:

| Trigger | Why it uncovers risk |
|---|---|
| 📈 **Portfolio grew** | More exposure behind the same fixed budget. The floor has to be re-struck |
| 🛒 **Client bought** | New holdings arrive unhedged. Adding protection when a client invests is the unglamorous half of the job |
| 💵 **Client sold** | Risk exposure falls. The agent reassesses the book and returns any protection that is no longer needed |
| ⏳ **Hedge aged** | The market moved and the strike that used to hold the floor no longer reaches it |
| 📅 **Coverage expired** | Coverage silently ended. Nothing but recomputation notices |

## 🔄 The agent loop

```mermaid
flowchart TD
    CRON["⏰ Every 30 min, 09:45-15:45 ET<br/>Cloudflare worker + GitHub Actions"] --> HALT{HALT file?}
    HALT -- yes --> JOURNAL
    HALT -- no --> RECONCILE

    RECONCILE["1️⃣ RECONCILE<br/>Ask the broker what is held.<br/>Never assume"] --> KILL{Drawdown ><br/>kill-switch?}
    KILL -- yes --> JOURNAL
    KILL -- no --> MANDATE

    MANDATE["2️⃣ MANDATE + 3️⃣ STRESS<br/>Budget fixed once when the promise opened.<br/>Run the book down the whole descent"] --> LLM1
    LLM1(["🤖 LLM · risk analyst<br/>given the material, not the answer<br/>→ issue + recommendation"]) --> GAP{uncovered_risk > 0<br/>or a sleeve exposed?}

    GAP -- no --> JOURNAL
    GAP -- yes --> PROTECT

    PROTECT["4️⃣ PROTECT<br/>Release what is redundant.<br/>Price every permitted structure<br/>on the live chain"] --> ELIGIBLE{Closes the risk?<br/>Expires?}
    ELIGIBLE -- no --> DROP[Never shown to the model]
    ELIGIBLE -- yes --> LLM2

    LLM2(["🤖 LLM · chooser<br/>picks between admissible structures<br/>fallback: deterministic rule"]) --> GATE

    GATE{"5️⃣ GATE<br/>capital at risk · liquidity · spread<br/>drawdown · net delta"}
    GATE -- veto --> JOURNAL
    GATE -- approve --> EXECUTE

    EXECUTE["6️⃣ EXECUTE<br/>Already live at the broker? Do not resend.<br/>Otherwise send, then read the fill back"] --> JOURNAL

    JOURNAL["7️⃣ JOURNAL<br/>Append-only record · status page<br/>🤖 LLM · the client's note"] --> END([Commit and stop])

    style LLM1 fill:#7c3aed,stroke:#4c1d95,color:#fff
    style LLM2 fill:#7c3aed,stroke:#4c1d95,color:#fff
    style JOURNAL fill:#0f766e,stroke:#134e4a,color:#fff
    style GATE fill:#b91c1c,stroke:#7f1d1d,color:#fff
    style DROP fill:#57534e,stroke:#292524,color:#fff
```

**Read it as two colours.** Purple is where a language model speaks; red is the boundary it cannot cross. Every purple box is downstream of a number that was already settled and upstream of a check it cannot influence.

**The halt edge is the reason this is a graph and not a function.** An early return can skip the journal, and a cycle that stopped without writing anything is indistinguishable, six days later, from a cycle that crashed. Routing every halt *to* the journal makes "we deliberately did nothing, here is why" a recorded outcome rather than an absence.

## 🔌 Alpaca Trading API and MCP Server

Drawdown Guard uses the Alpaca Trading API and Alpaca MCP Server to read the account, positions, market data and option chain, submit orders, and reconcile execution.

```
Drawdown Guard
      ↓
Alpaca MCP tools
      ↓
Alpaca Trading API
      ↓
Paper account
```

The agent decides what should happen. MCP provides the controlled interface to the broker. The deterministic Gate remains the final trading boundary.

## 🛑 How to Stop the Agent

It is an autonomous agent trading an account on a schedule, so there has to be a way to stop it from a phone, without a laptop and without touching the code:

```bash
touch HALT && git add HALT && git commit -m "halt" && git push
```
**It stops the agent, not the protection.** Every position stays exactly where it is. Hedges already bought keep working. A stop button that liquidated the client's protection would disarm the portfolio at the precise moment somebody was worried enough to press it.

## 🔧 What broke, and how we knew

Every one of these was found in the journal, not in a stack trace. That is the claim worth making: an autonomous agent trading unattended does not get to fail loudly, so the only defence is a record detailed enough to be read against reality.

| When | What broke | How we found out | The fix |
|---|---|---|---|
| Aug 28 | Limits set exactly at the ask never filled | Journal said `submitted: 2`; the account held nothing | The limit reaches past the offer by a fraction of the spread |
| Aug 28 | "Sent" was being reported as "bought" | Two facts sat in the record with nothing connecting them | Three outcomes instead of two: `filled`, `partial`, `working` |
| Aug 28 | The LLM sat 41 seconds between pricing an order and sending it | Timestamps in the journal | The model now writes *after* the fills are read back, not before |
| Aug 27-28 | The cycle did not run at all | Scheduled runs arrived at 23:24 UTC, hours after the close | Thirteen attempts a day, plus a Cloudflare worker that presses the first one on time |
| Aug 31 | A quarter of the spread was still not enough | The XLF ask drifted 2.72 → 2.87 across the session | Cross the whole spread |
| Aug 31 | A client selling near the close would go unseen until the next morning | Reading the schedule against the scenario | A full cycle every half hour, all session |

Plus fifteen defects from a full-codebase audit: a state key dropped on an early return that silently discarded release orders, a mid price averaged against a missing quote side, a hedge released and immediately rebought because two checks disagreed about scope, and twelve more.

**Two of these could not have been found by testing.** The unfilled limits and the missing cycles are both cases where every component behaved exactly as written and the outcome was still wrong. Only the record caught them.

## 🏁 Main Tracks

**Track 03 — Hedging & Risk Protection Agents**

Built directly against the four agent types this track names:

| Track agent type | How Drawdown Guard implements it |
|---|---|
| 📉 **Drawdown-defense agents** | Checks the portfolio against a client-defined loss budget and keeps the book within its mandate |
| 🛡️ **Protective put strategy** | Uses long puts to cover the calculated downside shortfall |
| 🎯 **Collar strategy** | Priced on every cycle against the put, and taken when the call is the richer leg. It has not been taken yet: on this book the call has priced below the put on every reading, so buying outright won 37 times out of 37. The journal records every declined collar and the volatilities that declined it |
| ♻️ **Hedge rebalancers for equity portfolios** | Adds protection when risk is uncovered and hands it back on a margin band. The client's sale on day 4 and purchase on day 5 are what exercise both halves |

## 🧰 Technologies

| Technology | Description |
|---|---|
| **Alpaca Trading API** | Live paper account, level 3 |
| **Alpaca MCP Server** | Every broker call goes through MCP |
| **LangGraph** | The five-node cycle, including the conditional halt edge |
| **LangChain** | The provider-agnostic chat interface behind all three model calls |
| **Google Gemini** | Risk analysis, hedge structure selection, and the client's note |
| **NumPy · SciPy · pandas** | Black-Scholes, the stress ladder, the payoff maths |
| **Pydantic** | Mandates and limits are validated types |
| **GitHub Actions** | The agent's heartbeat. Thirteen autonomous cycles a day, every weekday |
| **GitHub Pages** | The live status page, rebuilt from the journal after every cycle |
| **Cloudflare Workers** | Presses the first cycle of the day on time, because GitHub's cron does not |
| **Python 3.11** | The language the project runs on |

## ▶️ Try it

```bash
uv sync
cp .env.example .env                           # your Alpaca paper keys
uv run python3 scripts/healthcheck.py          # says why it won't trade, if it won't
uv run python3 scripts/run_cycle.py --dry-run  # decides, journals, submits nothing
uv run python3 scripts/build_site.py           # rebuilds the status page
uv run pytest
```

🔐 `ALPACA_PAPER_TRADE=true` is a hard interlock. The program refuses to start without it. This has never traded real money.

## 📁 Layout

```
src/drawdownguard/
  risk/        mandate, period, stress ladder, remedies, and the gate
  agent/       the cycle, its nodes, the roles, the guards
  market/      Alpaca adapters: account, chain, snapshot, history
  options/     Black-Scholes pricing and payoff
  execution/   order submission and broker reconciliation
  mcp/         the Alpaca MCP client and its toolsets
  journal/     append-only record, and the status page built from it

config/        risk.yaml, the permanent limits; mandates.yaml, the promises
               scenario.yaml, the client's week, committed before it runs
scripts/       run_cycle, healthcheck, build_portfolio, build_site, client_action
scheduler/     the Cloudflare worker that presses the button on time
journal/       the append-only record, one file per day
docs/          the published status page
```
