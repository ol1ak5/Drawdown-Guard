# 🛡️ Drawdown Guard

**Investors can say how much they can afford to lose in the worst case. But portfolios can't keep that promise on their own.**

So we built an agent that does.

Drawdown Guard is an autonomous AI trading agent that checks a portfolio every weekday against its client's downside limit, and steps in with an options overlay the moment it doesn't.

**The market moves. The loss mandate doesn't. Drawdown Guard keeps the two in line.**

🔴 Live decisions: **[status page](https://ol1ak5.github.io/Drawdown-Guard/)**
📊 Backtest: **[report](docs/backtest-report.md)**
📓 Every decision ever made: **[journal](journal/)**

---

## 🎯 The problem

**Just knowing a loss tolerance limit is not a loss control.**

An investor may have a clear idea of how much loss they can realistically tolerate. But the portfolio can't enforce that limit on its own. It holds what it holds and it falls when the market falls, quietly drifting away from the initial number.

Predicting the next crash is hard. Knowing whether today's portfolio still respects yesterday's promise is a different problem. Trillions of dollars of software do the first. Almost nothing does the second the second - that's what we're solving.

## 💡 The solution

Drawdown Guard stands between the promise and the portfolio, turning a client's downside limit into a continuously monitored portfolio constraint the portfolio answers to every day.

Every weekday, it wakes up and asks one question: **if the market fell right now, would the portfolio still be inside the number it was given?** If yes, it says so, in writing, and continues to be on guard. If no, it measures the existing gap, and buys protection on today's actual option chain.

## 👤 One client, one promise

For this hackathon, we turn the problem into a concrete situation.

### Initial settings

Our simulated client has a c. $1,000,000 portfolio that includes: 
- 80% equity: SPY, QQQ, IWM
- 15% fixed income: BIL
- 5% cash

| Ticker | Shares | Entrance price | Total Amount | Exposure | 
|---|---|---|
| **SPY** | XXX | Aug 28 | XXX | **$400,000** | Equity exposure |
| **QQQ** | XXX | Aug 28 | XXX | **$200,000** | Equity exposure |
| **IWM** | XXX | Aug 28 | XXX | **$200,000** | Equity exposure |
| **BIL** | XXX | Aug 28 | XXX | **$150,000** | Protection reserve |
| **Cash** | n.a. | n.a. | n.a. | **$50,000** | liquidity for hedge |

### The promise

The client's mandate is simple:

> *“In the worst case, I can tolerate a 10% loss per year.”*

### Just two numbers:**

- 🔟 **10%** — the most the client can lose. $100,000 of a $1,000,000 account.
- 📆 **12 months** — the window that promise has to hold.

### Activity during the hackathon

The client changes the portfolio mid-flight:
- Sells 250 shares of IWM on September 1st
- Buys 130 shares of AAPL on September 3rd

| Day | Date | Client does | Agent does |
|---|---|---|---|
| Day 1 | Aug 28 | - | Buys the protection - steps in |
| Day 2 | Aug 31 | - | Checks the portfolio. Still within limit - holds |
| Day 3 | Sep 1 | Sells 250 IWM | Risk drops - release the now-unnecessary protection |
| Day 4 | Sep 2 | - | Checks the portfolio. Still within limit - holds |
| Day 5 | Sep 3 | Buys 130 AAPL | Risk rises again — rebalances protection to match |
| Day 5 | Sep 4 | - | Checks the portfolio. Confirms the mandate still holds |

The portfolio is intentionally not static. A client who never touches their allocation isn't the point. That gives Drawdown Guard a real job: it keep the changing portfolio aligned with a fixed risk mandate.

## ⚙️ How the agent actually works

Seven steps, once every weekday, fully autonomous.

| # | Step | The agent does|
|---|---|---|
| 1️⃣ | **Reconcile** | Asks the broker what is actually held in the portfolio - never assumes |
| 2️⃣ | **Mandate** | Turns the client's tolerance into a live dollar budget |
| 3️⃣ | **Stresso** | Runs the book through a range of market drops, finds the gap |
| 4️⃣ | **Protects** | Solves for the cheapest hedge that closes the gap, sleeve by sleeve |
| 5️⃣ | **Gates** | Checks every order against hard limits before it can reach the broker |
|  | **Executes** | Sends the approved orders and confirms the fills |
|  | **Journal** | Writes down what happened and why, in plain language, with LLM |

### 1. Reconcile
The cycle starts by reading the client's account, not by trusting what the agent thought it owned yesterday. Every number is computed from what's actually there this morning.

### 2. Mandate
The client's sentence becomes a dollar figure: **10% of $1,006,000 = $100,589**. That is the whole downside budget, and nothing the agent does may spend more of it than the client agreed to.

### 3. Stress-scenario

The agent stresses the book across a range of hypothetical drops:

| If the market falls | The portfolio loses | Budget | Verdict |
|---|---|---|---|
| −5% | $30,208 | $100,589 | ✅ inside the promise |
| −10% | $60,415 | $100,589 | ✅ inside the promise |
| −20% | $120,831 | $100,589 | 🚨 **$20,287 past it** |
| −35% | $211,454 | $100,589 | 🚨 **$110,865 past it** |
| −50% | $302,077 | $100,589 | 🚨 **$201,488 past it** |

**This is not a forecast.** The agent doesn't say the market will fall 20%. It says: *if it did, this book would break a promise that was already made.*

### 4. Protects

The agent splits the book and the hedge budget by ticker. For each sleeve, it solves one equation against the live option chain:

```
        fall down to the strike   +   premium paid   =   that sleeve's budget
        └── unprotected drop ──┘      └── certain ──┘
```

It takes the **lowest strike that still fits**. Go lower and the market has too far to fall before the put engages. Go higher and the client will pay for protection he never asked for.

### 4. Gate

Every order faces a deterministic risk gate before it ever reaches the broker. It refuses naked shorts, illiquid strikes, and anything past the configured limits - no exceptions, no overrides.

### 4. Execute

Approved orders go out, fills get confirmed, and the portfolio's actual position is updated to match what was really bought.

### 5. Journal

Every number, every refusal, and every quiet morning goes into an append-only record. An LLM then writes one paragraph explaining the decision, in plain language.


## 🔗 The Chain of Decision 

**1️⃣ How much protection?** Enough that the client's worst case *at any depth*
is the promised 10% — no more, and pointedly no less.

Match the contracts to the shares, and below the strike every dollar lost on
the portfolio is a dollar gained on the put. The loss stops falling. So the
worst the client can do is **the drop down to the strike, plus the premium
paid**, and the agent solves for the strike where those two add to exactly the
budget. Here that is a strike 9.96% below the market, costing 2.03% of the
account.

That is why nobody has to guess how bad it gets. 🎯 The floor holds at −20%, at
−35%, at −50%. Protection is a cost, though, and a dollar spent beyond the
promise is a dollar taken from the client for nothing — so the agent solves for
the strike rather than rounding up to something that feels safe.

**2️⃣ What closes it?** Options. The portfolio stays exactly where it is:

| | 💵 Costs | 📈 Keeps | ↩️ Undoable |
|---|---|---|---|
| 🛡️ **Protective put** | cash, up front | **all** the upside | yes — it expires |
| 🎯 **Collar** | little or nothing — the put is paid for by selling a call | upside up to the call strike | yes — it expires |

**The shares are never sold.** 🚫 The code can do it and every mandate we ship
switches it off, because a promise kept by permanently shrinking the client's
portfolio is not a promise kept — it is the client paying for the guarantee
with the thing the guarantee was supposed to protect.

**3️⃣ Which one, today?** Both are priced on the live chain, every cycle, and
the cheaper one that fits the client's constraints wins. 💰

The ranking is deliberately *not* written in a config file. Whether a collar
beats a bare put depends on what calls are worth **this morning** — when
implied volatility is rich, the call pays for the put and the collar is nearly
free; when it is cheap, the collar sells away upside for almost nothing and the
plain put wins. A config file would answer that question the same way on every
day of every market. The chain answers it correctly on this one.

**4️⃣ For how long?** ⏳ This is the question almost nobody asks, and getting it
wrong quietly destroys the whole guarantee.

Short-dated puts look irresistible. A 30-day put 10% out of the money is
*cheap* — roll one every month for a year and you pay a third of what a single
12-month put costs. Every spreadsheet says buy the cheap one.

**We tested it on real SPY history**, priced with the same code the agent
trades with:

| SPY drawdown | Shape | 🔁 Rolled 30-day puts | 🛡️ One put, held throughout |
|---|---|---:|---:|
| **2022** −25.4% over 279 days | slow grind | paid 2.54, **received 0.00** ❌ | paid 9.58, received 64.98 ✅ |
| **2020** −34.2% over 31 days | fast crash | paid 0.34, received 80.61 ✅ | paid 0.24, received 80.61 ✅ |

Read the 2022 row twice. **Nine consecutive puts, every one expired worthless,
while the market destroyed a quarter of its value.** No single month fell far
enough to put any of them in the money — and by the time each one expired, the
next was struck against an already-lower market. The client paid for insurance
all year and collected nothing.

Short-dated protection is cheap **because it only covers fast crashes**. A slow
grind walks straight past it, and slow grinds are how most real drawdowns
happen. The client's promise is not about one terrible day; it is about not
losing 10% of their money, *however slowly it goes*.

So the agent buys protection that outlives the promise, and then leaves it
alone. 🧘 Long-dated positions held for months — the opposite of churn. An agent
that reshuffles its hedge every week pays the spread every week, and that bill
arrives whether or not the crash ever does.

**5️⃣ Never let it all expire at once.** 🪜 Protection is bought in a ladder of
expiries rather than in one lump. If everything matured on the same Friday, the
agent would be *forced* to buy a year of coverage at whatever price the market
happened to offer that morning — possibly in the middle of the panic the client
is paying to be protected from. A ladder means every roll is a small one, and
no single day can hold the promise hostage.

**6️⃣ Then the agent gives the protection back.** ♻️ When the book returns
inside its budget *with room to spare*, the hedge is released — on a margin,
not on the line itself, so ordinary daily wobble cannot walk a position across
the boundary and back while paying the spread each time.

This is the half that most hedging stops at. Protection is easy to buy and
nobody remembers to sell it, so the client ends up paying for a wall around a
risk that went away months ago. The gap closing is as much a signal as the gap
opening.

### When the agent steps in 🚦

Never on a hunch, and never because it thinks it knows what the market will do
next. There are exactly three triggers, all of them mechanical, all of them
slow enough to act on calmly:

| | Trigger | Why it opens a gap |
|---|---|---|
| 📈 | **The portfolio grew** | More equity behind the same promise. The floor has to be re-struck higher. |
| ⏳ | **The hedge aged** | Time passed, the market moved, and the strike that used to hold the floor no longer does. |
| 📅 | **Something expired** | Coverage silently ended. Nothing but recomputation notices. |

Not one of these is a market call. They are all arithmetic on what the account
already holds — which is why the agent trades rarely, deliberately, and can
explain every trade it makes without ever claiming to know the future.

### What the client actually gets 🎁

Eighty contracts — one for every hundred shares — struck 9.96% below the
market, dated past the client's twelve months, bought on a quiet morning for
**2.03% of the account**. Here is the same portfolio before and after:

| If the market falls | Without the agent | With the agent | |
|---|---:|---:|---|
| −5% | loses 4.0% | loses 6.0% | 💸 the premium, and this is what it costs |
| −10% | loses 8.0% | loses **10.0%** | at the line |
| −20% | loses **16.0%** 🚨 | loses **10.0%** | ✅ the promise, kept |
| −35% | loses **28.0%** 🚨 | loses **10.0%** | ✅ the promise, kept |
| −50% | loses **40.0%** 🚨 | loses **10.0%** | ✅ the promise, kept |

**The floor does not care how far the market falls.** Below the strike, every
dollar the shares lose is a dollar the puts gain, so the line simply stops
going down — at −20%, at −50%, at whatever comes. No scenario had to be
guessed, and nothing here depends on anyone being right about the future.

And read the first row, because it is the honest one. 📏 In a mild dip the
client is **worse off by the premium** — 6% instead of 4%. That is not a flaw
to be explained away; that is what insurance is. You pay every year to be
whole in the year that matters.

**Name the promise and its window → check the book against it → solve for the
protection that floors the loss → buy it long, in a ladder → hand it back when
it is no longer needed.** Every weekday, in writing. 📓

---

## 🛑 How to stop it

It is an autonomous agent trading an account on a schedule, so there has to be
a way to stop it from a phone, without a laptop and without touching the code:

```bash
touch HALT && git add HALT && git commit -m "halt" && git push
```

The next run reads that file before it does anything else and shuts down. There
is an automatic version too — a drawdown past the configured limit halts the
cycle **before** it reads a single price, so a bad week cannot become a worse
one while nobody is watching. 🚨

**It stops the agent, not the protection.** Every position stays exactly where
it is; hedges already bought keep working. A stop button that liquidated the
client's protection would disarm the portfolio at the precise moment somebody
was worried enough to press it. 🛡️

## 🏁 Main Tracks

**Track 03 — Hedging & Risk Protection Agents** 🛡️

Built directly against the four agent types this track names:

| Track agent type | How Drawdown-Guard implements it |
|---|---|
| 🛡️ **Protective put agents** | Sizes long puts against the exact dollar shortfall, not a fixed percentage of the book |
| 🎯 **Collar agents** | Prices the financing call every cycle and takes the collar only when the live chain actually favours it |
| 📉 **Drawdown-defense agents** | The entire product: a client-stated loss budget, checked against the real book daily |
| ♻️ **Hedge rebalancers for equity portfolios** | Adds protection when the gap opens and *releases it* when the gap closes, on a margin band |

## 🧰 Technologies

| | |
|---|---|
| 🦙 **Alpaca Trading API** | Live paper account - equities and options, level 3 |
| 🔌 **Alpaca MCP Server** | Every broker call goes through MCP. Read-only toolsets for the AI. Order tools reachable only on the deterministic path |
| 🧠 **Google Gemini** | Writes the plain-language explanation of the drawdown strategy the agent choses today |
| 🕸️ **LangGraph** | The seven-node cycle, including the conditional halt edge |
| 🔗 **LangChain** | Model plumbing and structured output for the analyst |
| 📐 **CVXPY + HiGHS** | Convex program sizing positions under tail-risk and exposure constraints |
| 🔢 **NumPy · SciPy · pandas** | Black-Scholes, the stress ladder, the backtest engine |
| ✅ **Pydantic** | Mandates and limits are validated types - an impossible promise fails at load time, not at runtime |
| ⚙️ **GitHub Actions** | The agent's heartbeat - one autonomous cycle every weekday at 14:00 UTC |
| 🌐 **GitHub Pages** | The live status page, rebuilt from the journal after every cycle |
| 🐍 **Python 3.11** | The language everything runs on |

## ▶️ Try it

```bash
uv sync
cp .env.example .env                          # your Alpaca paper keys
uv run python3 scripts/healthcheck.py         # says why it won't trade, if it won't
uv run python3 scripts/run_cycle.py --dry-run # decides, journals, submits nothing
uv run python3 scripts/run_backtest.py --symbol SPY
uv run pytest                                 # 378 tests
```

🔐 `ALPACA_PAPER_TRADE=true` is a **hard interlock** — the program refuses to
start without it. This has never traded real money and cannot.

## 📁 Layout

```
src/drawdownguard/
  risk/        mandate, stress ladder, remedies, and the gate that enforces them
  agent/       the cycle, its nodes, the analyst, the guards
  market/      Alpaca adapters: account, chain, snapshot
  optimizer/   candidate filtering, Black-Scholes, the convex program
  execution/   order submission and broker reconciliation
  backtest/    the same modules, driven by history
  journal/     append-only record, and the status page built from it
config/        risk.yaml, the permanent limits; mandates.yaml, the promises
docs/notes/    what we measured, and what turned out not to be true
```

The backtest imports the live optimizer and the live risk gate instead of
reimplementing them. That is the whole reason to trust it: a backtest running
different code from the agent measures a strategy nobody is going to trade.
