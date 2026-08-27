# 🛡️ Drawdown Guard

**Investors can say how much they can afford to lose in the worst case scenario. But portfolios can't keep that promise on its own.**

So we built an agent that does.

Drawdown Guard is an autonomous AI trading agent that checks a portfolio every weekday against its client's downside limit, and steps in with an options overlay the moment it doesn't.

**The market moves. The loss mandate doesn't. Drawdown Guard keeps the two in line.**

🔴 Live decisions → **[status page](https://ol1ak5.github.io/Drawdown-Guard/)** ·
📊 Backtest → **[report](docs/backtest-report.md)** ·
📓 Every decision ever made → **[journal](journal/)**

---

## 🎯 The problem

**Just knowing a loss tolerance limit is not a loss control.**

An investor may have a clear idea of how much loss they can realistically tolerate. But the portfolio can't enforce that limit on its own. It holds what it holds and it falls when the market falls. 

The hard part is predicting the next crash. But a hardest one is to know, in real time, whether the portfolio still respects the limit it was supposed to maintain.

There are trillions of dollars of software for *predicting* the market. There is almost nothing for *keeping a promise* about it.

## 💡 The solution

Drawdown Guard stands between the promise and the portfolio, turning a client's downside limit into a continuously monitored portfolio constraint the portfolio answers to every day.

Every weekday, it wakes up and asks one question: **if the market fell right now, would this client still be inside the number they were given?** If yes, it says so, in writing, and keep on the guard. If no, it measures exactly how far outside, prices the ways of getting back in, and buys protection on today's actual option chain.


## 👤 One client, one promise, two numbers

For this hackathon, we turn the problem into a concrete situation.

### Initial settings

Our simulated client has a c. $1,000,000 portfolio that includes: 
- 80% equity: SPY, QQQ, IWM
- 15% T-bills (BIL)
- 5% cash

| Amount | Instrument | Type |
|---|---|---|
| **$400,000** | SPY | Equity portfolio |
| **$200,000** | QQQ | Equity portfolio |
| **$200,000** | IWM | Equity portfolio |
| **$150,000** | BIL | Protection reserve |
| **$50,000** | Cash | Liquidity/Protection reserve - the money the agent is allowed to spend on hedges |

### The promise

The client's mandate is simple:

> *“In the worst case scenario, I can tolerate a 10% loss per year.”*

**Just two numbers:**

- 🔟 **10%** — the most the client can lose. $100,000 of a $1,000,000 account.
- 📆 **12 months** — the window that promise has to hold.

### Activity during Hackathon

The client changes the portfolio mid-flight:
- Sell 250 shares of IWM on September 1st
- Buy 130 shares of AAPL on September 3rd

| Day | Date | Client does | Agent steps |
|---|---|---|---|
| Day 1 | 28 Aug | - | Checks the portfolio. Buys protection - steps in |
| Day 2 | 31 Aug | - | Checks the portfolio. Adjusts the protection if necessary |
| Day 3 | 1 Sep | Sells 250 IWM | Buys the unecessary protection - release |
| Day 4 | 2 Sep | - | Checks the portfolio. Adjusts the protection if necessary |
| Day 5 | 3 Sep | Buys 130 AAPL | Buys protection - rebalance |
| Day 5 | 4 Sep | - | Result |

The portfolio is intentionally not static. That gives Drawdown Guard a real job: keep the changing portfolio aligned with a fixed risk mandate.

## ⚙️ How the agent actually works

Ten steps, once a day, fully autonomous.

| | | |
|---|---|---|
| 1️⃣ | **Reconcile** | Ask the broker what is held. |
| 2️⃣ | **Mandate** | Load the client's promise and turn the budget into dollars. |
| 3️⃣ | **Protect** | Stress the book, find the gap, price the remedies. |
| 4️⃣ | **Look** | Spot, realised volatility, implied volatility, IV rank. |
| 5️⃣ | **Judge** | The LLM names the regime. This is its only job. |
| 6️⃣ | **Route** | Which instrument — decided by the gap, not by the model. |
| 7️⃣ | **Filter** | From ~2,000 contracts down to the handful that are choices at all. |
| 8️⃣ | **Optimise** | Convex program, subject to tail risk and exposure. |
| 9️⃣ | **Gate** | Every order faces the risk gate. No bypass, no flag, no override. |
| 🔟 | **Write it down** | Including, especially, the decision to do nothing. |










### The morning check ⚠️

The agent stresses the book down the whole range and reports what the client
would actually be holding — not to predict any one of these, but because a
promise that only survives some of them is not a promise:

| If the market falls | Portfolio loses | Budget | Verdict |
|---|---:|---:|---|
| −5% | $40,000 | $100,000 | ✅ inside the promise |
| −10% | $80,000 | $100,000 | ✅ inside the promise |
| −20% | $160,000 | $100,000 | 🚨 **$60,000 past it** |
| −35% | $280,000 | $100,000 | 🚨 **$180,000 past it** |

**The promise is broken, and here is exactly why:**

📈 $800,000 of equity has to fall only 12.5% to burn a $100,000 budget, and
markets do that roughly once every few years. The promise and the portfolio
were built by different people who never spoke.

Nobody did anything wrong to get here. Eighty percent in equities and twenty in
reserve is a textbook allocation, and any adviser in the world would sign it.
This is simply what a sensible portfolio looks like the first time anyone holds
it up against the sentence the client actually said. 🤷

### The chain of decision 🔗

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
| 🧠 **Google Gemini** | LLM explains the protection strategy the agent chooses |
| 🕸️ **LangGraph** | The ten-node cycle, including the conditional halt edge |
| 🔗 **LangChain** | Model plumbing and structured output for the analyst |
| 📐 **CVXPY + HiGHS** | Convex program sizing positions under tail-risk and exposure constraints |
| 🔢 **NumPy · SciPy · pandas** | Black-Scholes, the stress ladder, the backtest engine |
| ✅ **Pydantic** | Mandates and limits are validated types — an impossible promise fails at load time, not at runtime |
| ⚙️ **GitHub Actions** | The agent's heartbeat: one autonomous cycle every weekday at 14:00 UTC |
| 🌐 **GitHub Pages** | The live status page, rebuilt from the journal after every cycle |
| 🐍 **Python 3.11 · uv · ruff · pytest** | 378 tests, zero lint errors |

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
