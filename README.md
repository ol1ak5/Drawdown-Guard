# 🛡️ Drawdown Guard

**Investors can say how much they can afford to lose in the worst scenario. But portfolios can't keep that promise on their own.**

So we built an agent that does.

Drawdown Guard is an autonomous AI trading agent that checks a portfolio every weekday against its client's downside limit, and steps in with an options overlay the moment it doesn't.

**The market moves. The loss mandate doesn't. Drawdown Guard keeps the two in line.**

🔴 Live decisions: **[status page](https://ol1ak5.github.io/Drawdown-Guard/)**
📓 Every decision ever made: **[journal](journal/)**

---

## 🎯 The problem

**Just knowing a loss tolerance limit is not a loss control.**

Investors can decide how much downside they can accept. But once the portfolio is built, that number does not enforce itself.

Markets move. Positions change. Options expire. New exposure is added.

The portfolio keeps evolving, while the client's risk limit stays the same. Over time, the two can drift apart, and the investor may not discover the gap until the market tests it.

Predicting the next crash is hard. Knowing whether today's portfolio still respects the risk limit set for it is a different problem. That's exactly what Drawdown Guard is built to solve.

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
- 82% equity: IWM, XLF
- 9% fixed income: BIL
- 9% cash

| Ticker | Shares | Fill price | Value | Exposure | Description |
|---|---|---|---|---|---|
| **IWM** | 100 | 297.34 | **$29,734** | Equity | Small-cap index |
| **XLF** | 900 | 58.25 | **$52,425** | Equity | Financial sector |
| **BIL** | 100 | 91.66 | **$9,166** | Fixed income | 1-3 month T-bills |
| **Cash** | n.a. | n.a. | **$8,675** | Liquidity | Used for the hedge |
| **Total** | | | **$100,000** | | |

### The promise

The client's mandate is simple:

> *“In the worst case, I can tolerate a 10% loss per year.”*

**Just two numbers:**

- 🔟 **10%** — the most the client can lose. Roughly $10,000 on this account;
  the exact figure is fixed the day the promise opens and never moves after.
- 📆 **12 months** — the window that promise has to hold.

### Activity during the hackathon

The client changes the portfolio mid-flight:
- Sells the whole XLF position of 900 shares on September 2nd
- Buys 100 shares of AAPL on September 3rd

The portfolio is intentionally not static. A client who never touches their allocation isn't the point. That gives Drawdown Guard a real job: it keep the changing portfolio aligned with a fixed risk mandate.

| Day | Date | Client does | Agent does |
|---|---|---|---|
| Day 1 | Aug 28 | Buys 100 IWM, 900 XLF, 100 BIL | Checks the portfolio. Steps in. Orders expired unfilled. **Execution logic fixed for the next week** |
| Day 2 | Aug 31 | - | Checks the portfolio. Prices and sends the orders again |
| Day 3 | Sep 1 | - | **Nothing.** The promise holds and the book has not moved |
| Day 4 | Sep 2 | Sells 900 XLF | Risk drops. Hands back all nine XLF puts |
| Day 5 | Sep 3 | Buys 100 AAPL | Risk rises again. Hedges the new holding on its own underlying |
| Day 6 | Sep 4 | - | Confirms the mandate still holds |

**What happened?**
Day 1 exposed an execution issue. The agent submitted limit orders at the ask, but the ask moved while the cycle was still running. The orders expired without filling, journalled as `order.working`. We changed the execution rule so the limit can move a quarter of the spread beyond the price being crossed. This keeps every order a limit order while giving it enough room to follow a moving market instead of expiring unfilled.

## ⚙️ How the agent actually works

Seven steps, once every weekday, fully autonomous. Five of them are nodes in the LangGraph cycle.

| # | Step | The agent does | Where it lives |
|---|---|---|---|
| 1️⃣ | **Reconcile** | Asks the broker what is actually held. Never assumes | `reconcile` node |
| 2️⃣ | **Mandate** | Turns the client's tolerance into a live dollar budget | `mandate` node |
| 3️⃣ | **Stress** | Runs the book down the whole descent and measures the uncovered risk | `mandate` node |
| 4️⃣ | **Protect** | Solves for the cheapest hedge that covers it, sleeve by sleeve | `protect` node |
| 5️⃣ | **Gate** | Checks every order against hard limits before it can reach the broker | `execute` node |
| 6️⃣ | **Execute** | Sends the approved orders, then reads back what the broker actually did with each | `execute` node |
| 7️⃣ | **Journal** | Writes down what happened and why, in plain language, with an LLM | `journal` node |

### 1. Reconcile

The cycle starts by reading the client's account, not by trusting what the agent thought it owned yesterday. Every number below is computed from what is actually there this morning.

### 2. Mandate — the drawdown budget

The client's drawdown limit becomes a dollar figure:

```
10% × $99,978  =  $9,998
      └── the reference ──┘
```

**Why $99,978 and not the $100,000 in the table above.** The portfolio was
bought at 15:44 and the promise opened at 15:48. In those four minutes the
market marked the new positions down by $22. The reference is the account as it
stood at the second moment, and that is the number every budget for the next
twelve months is a percentage of.

**The reference does not move.** Not with the market, and not when the client
buys or sells. It is written down once and held for the whole twelve months.

That is deliberate, and the alternative is worse than it sounds. A budget of
"10% of whatever the account is worth this morning" is not a promise — it is a
promise that re-bases. Lose ten percent and tomorrow the agent defends ten
percent of the smaller number, then ten percent of the one after that. Five
steps of that permit a **47% loss** and report every one of them as kept. Worse,
the budget shrinks fastest exactly when a fall has already begun, so the agent
would buy *less* protection precisely as it became most necessary.

A high-water mark was the other candidate. It was rejected for a different
reason: it moves when the market makes a new high, so the agent would re-strike
its hedge because prices went **up**. This project's whole claim is that it
never acts on where the market is going. One place where it does is enough to
lose the argument.

The client's gains are not left unprotected forever. They are locked in at
renewal, on the calendar — 28 August 2027 — rather than continuously and on the
market's cue.

### 3. Stress — how much can this book actually lose?

**Nobody can name the next shock.** Not the client, not the agent. A promise
that only holds down to a depth somebody guessed is not much of a promise. So
the agent does not pick a depth. It asks the question that needs no guess:

> What is the most this book can lose, **anywhere on the way down**?

For bare shares the answer is *everything*. The loss keeps growing as the price
approaches zero — there is no bottom. So the worst case is the entire equity
sleeve, and the part of it nobody has agreed to carry is what the agent is
hired to close:

```
worst case (unprotected)    $82,023     ← the whole equity sleeve
downside budget           −  $9,998     ← what the client agreed to absorb
─────────────────────────────────────
uncovered risk              $72,025     ← risk nobody has agreed to carry
```

**This is not a forecast.** The agent never says the market will fall. It says:
*if it did, this book would break a promise that was already made.*

### 4. Protect — what the hedge actually does

Here is the same book before and after the day-one hedge. Every figure comes
from the project's own stress code:

| If the market falls | Without the agent | **With the agent** |
|---|---:|---:|
| −10% | $8,202 | **$5,923** |
| −20% | $16,404 | **$5,923** |
| −35% | $28,708 | **$5,923** |
| −50% | $41,011 | **$5,923** |
| −90% | $73,820 | **$5,923** |
| −100% | $82,022 | **$5,923** |

**The column stops growing.** The market can go to zero and the client still
loses $5,923 against a budget of $9,998. That is not a threshold past which
losses resume — it is the floor.

Below the strike, every dollar the shares lose is a dollar the puts gain, so
the line simply lies flat. Infinity becomes a number, and that number cost
**$3,801 in premium**.

#### How the hedge is sized — two separate decisions

**Contracts come from the share count, never from the size of the risk.**

```
XLF:  900 shares → ceil(900/100) = 9 contracts
IWM:  100 shares → ceil(100/100) = 1 contract
```

Always one contract per hundred shares. Cover only part of the book and the
uncovered shares keep falling to zero, so the worst case runs away again — a
hedge over half a portfolio is not half a promise kept, it is a promise broken
at half the price.

**The strike comes from the budget.** Each symbol gets a share of the promise
in proportion to what it can lose, and the agent solves for the deepest strike
that still fits:

```
XLF   share of the budget $6,382
  fall to the strike   (58.17 − 54) × 900  =  $3,758
  premium                   2.64 × 9 × 100 =  $2,376
                                              ───────
  worst case                                   $6,134   ≤  $6,382  ✅

IWM   share of the budget $3,616
  fall to the strike  (296.65 − 275) × 100  =  $2,165
  premium                  14.25 × 1 × 100  =  $1,425
                                               ───────
  worst case                                    $3,590   ≤  $3,616  ✅
```

The two terms move against each other — a lower strike means further to fall
but a cheaper premium — so the total is monotonic and the answer is unique. The
agent takes the **lowest strike that still fits**. Go lower and the unprotected
drop alone spends the promise; go higher and the client pays for protection
they never asked for.

Nothing here is an optimiser with weights. It is the first strike that fits,
walking the live chain from the bottom.

> The premium is charged against the promise, not treated as free. A hedge
> sized as though its own cost were nothing comes up short by exactly that
> cost — which comes out of the same account the promise is written against.

**Four fixed shocks are published alongside all of this** — −5%, −10%, −20% and
−35% — and the [status page](https://ol1ak5.github.io/Drawdown-Guard/) lets a
reader drag the floor across them. They are how a person reads the book; they
are not how the agent sizes it. The rungs are fixed on purpose, and a mandate
cannot pick its own: one that promised against a flattering shock would be
choosing the exam as well as sitting it.

## 🔗 The Chain of Decision

**1️⃣ How much protection?** Answered above: contracts from the share count, strike from the budget. Enough that the worst case *at any depth* is inside the promise, and no more — protection is a cost, and a dollar spent past the promise is a dollar taken from the client for nothing.

**2️⃣ What closes it?** Options. The portfolio stays exactly where it is:

| Option | Costs | Keeps | Undoable |
|---|---|---|---|
| 🛡️ **Protective put** | Cash, up front | **All** the upside | Yes — it expires |
| 🎯 **Collar** | Little or nothing — the put is paid for by selling a call | Upside up to the call strike | Yes — it expires |

**The shares are never sold to cover the existing gap.** The client can sell the shares when he wants, but they are never used to cover the gap. **CHECK**

**3️⃣ Which option type, today?** Both are priced on the live chain, every cycle, and the cheaper one that fits the client's constraints wins.

**4️⃣ For how long?** Historic data showed that short-dated protection is cheap **because it only covers fast crashes**. A slow grind walks straight past it, and slow grinds are how most real drawdowns happen. The client's promise is not about one terrible day. It is about not losing 10% of their money in 12 months, so the agent buys protection dated **past the end of the promise**. An agent that reshuffles its hedge every week pays the spread every week, and that bill arrives whether or not the crash ever does.

**5️⃣ At what price does the order go?** Every order is a limit, never a market order. But a limit set *exactly* at the offer fills only if nobody moves, and we saw it on the first live day. Two protective puts were sent at the ask, the ask rose a few cents while the cycle was still running, and both sat unfilled until the close:

| Options | Our limit | Ask, minutes later | Filled |
|---|---|---|---|
| XLF 54 put ×9 | 2.64 | 2.72 | ❌ |
| IWM 275 put ×1 | 14.25 | 14.37 | ❌ |

**$71,985 of risk left uncovered overnight to avoid paying $20.** So we changed the rule, and now the limit reaches a quarter of the spread past the side being crossed to. 

**6️⃣ Then the agent gives the protection back.** ♻️ When the book returns
inside its budget *with room to spare*, the hedge is released — on a margin,
not on the line itself, so ordinary daily wobble cannot walk a position across
the boundary and back while paying the spread each time.

Protection is released in the two senses that differ:

| | What it means | Does the margin apply? |
|---|---|---|
| 🪦 **Spent** | The strike no longer reaches. A 440 put behind a stock that rallied to 550 pays nothing at the promised shock — it is not holding the promise up. | No. Removing something worth nothing cannot widen the risk. |
| 📦 **Redundant** | The protection still pays, but the promise holds without it and with headroom to spare. | Yes — 15% of the budget on the balanced mandate. |

This is the half that most hedging stops at. Protection is easy to buy and
nobody remembers to sell it, so the client ends up paying for a wall around a
risk that went away months ago. **Risk becoming covered is as much a signal as
risk opening up.**

### When the agent steps in 🚦

Never on a hunch, and never because it thinks it knows what the market will do next. The trigger is one line of arithmetic, recomputed every morning:

```
uncovered_risk = worst_loss(what is held today) − budget      > 0  →  act
```

Because the budget is fixed and the book is not, there are exactly four ways that line can turn positive. All of them mechanical, none of them a market call:

| | Trigger | Why it uncovers risk |
|---|---|---|
| 📈 | **The portfolio grew** | More exposure behind the same fixed budget. The floor has to be re-struck. |
| 🛒 | **The client bought** | New holdings arrive unhedged. Adding protection when a client invests is the unglamorous half of the job. |
| ⏳ | **The hedge aged** | The market moved and the strike that used to hold the floor no longer reaches it. |
| 📅 | **Something expired** | Coverage silently ended. Nothing but recomputation notices. |


### What the client actually gets 🎁

The promise was "in the worst case, I can tolerate a 10% loss." Here is the
whole answer, in three lines:

```
premium paid                      $3,801     3.80%   ← certain, spent on day one
worst the market can still do     $5,923     5.92%   ← at any depth, to zero
                                  ───────   ──────
worst outcome, all in             $9,724     9.73%
the client agreed to              $9,998    10.00%   ✅
```

**9.73% against a promise of 10%.** Not because the market was kind — because
the floor was bought and the arithmetic was checked against it. Unprotected,
the same book loses **82% if the market goes to zero**.

The margin is $274, and it is there because contracts are lumpy: nine XLF puts
cannot be bought as eight and a half. Slightly over-covered is the correct
direction to round.

**Read the first line, because it is the honest one.** 📏 The premium is spent
whether or not anything happens. In a quiet year the client is worse off by
$3,801 and gets nothing back. That is not a flaw to be explained away — that is
what insurance is. You pay every year to be whole in the year that matters.

**Name the promise and its window → check the book against it → solve for the
protection that floors the loss → buy it long → hand it back when it is no
longer needed.** Every weekday, in writing. 📓

---

## 🛑 How to Stop the Agent

It is an autonomous agent trading an account on a schedule, so there has to be a way to stop it from a phone, without a laptop and without touching the code:

```bash
touch HALT && git add HALT && git commit -m "halt" && git push
```
**It stops the agent, not the protection.** Every position stays exactly where it is; hedges already bought keep working. A stop button that liquidated the client's protection would disarm the portfolio at the precise moment somebody was worried enough to press it.

## 🏁 Main Tracks

**Track 03 — Hedging & Risk Protection Agents** 🛡️

Built directly against the four agent types this track names:

| Track agent type | How Drawdon Guard implements it |
|---|---|
| 🛡️ **Protective put agents** | Sizes long puts against the exact dollar shortfall |
| 🎯 **Collar agents** | Prices the financing call every cycle and takes it only when the call's implied volatility is at or above the put's |
| 📉 **Drawdown-defense agents** | The entire product: a client-stated loss budget, checked against the real book daily |
| ♻️ **Hedge rebalancers for equity portfolios** | Adds protection when risk is uncovered and hands it back on a margin band. Demonstrated on day 4, when the client sells the whole XLF position |

## 🧰 Technologies

| Technology | Description |
|---|---|
| **Alpaca Trading API** | Live paper account, level 3 |
| **Alpaca MCP Server** | Every broker call goes through MCP |
| **LangGraph** | The five-node cycle, including the conditional halt edge |
| **LangChain** | The provider-agnostic chat interface behind the journal's explanation |
| **Google Gemini** | Writes the plain-language explanation of the decision the arithmetic already made |
| **NumPy · SciPy · pandas** | Black-Scholes, the stress ladder, the payoff maths |
| **Pydantic** | Mandates and limits are validated types |
| **GitHub Actions** | The agent's heartbeat. One autonomous cycle every weekday |
| **GitHub Pages** | The live status page, rebuilt from the journal after every cycle |
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

🔐 `ALPACA_PAPER_TRADE=true` is a **hard interlock**. The program refuses to start without it. This has never traded real money.

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
  config/      risk.yaml, the permanent limits; mandates.yaml, the promises
               scenario.yaml, the client's week, committed before it runs
  scripts/     run_cycle, healthcheck, build_portfolio, build_site, client_action
  docs/        the published status page
```
