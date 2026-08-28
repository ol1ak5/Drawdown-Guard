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

An investor can decide how much downside he can accept. But once the portfolio is built, that number does not enforce itself.

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

| Ticker | Shares | Price | Value | Exposure | Description |
|---|---:|---:|---:|---|---|
| **IWM** | 100 | 297.61 | **$29,761** | Equity | Small-cap index |
| **XLF** | 900 | 58.25 | **$52,429** | Equity | Financial sector |
| **BIL** | 100 | 91.66 | **$9,166** | Fixed income | 1–3 month T-bills |
| **Cash** | n.a. | n.a. | **$8,644** | Liquidity | Used for the hedge |

### The promise

The client's mandate is simple:

> *“In the worst case, I can tolerate a 10% loss per year.”*

### Just two numbers

- 🔟 **10%** — the most the client can lose. $10,000 of a $100,000 account.
- 📆 **12 months** — the window that promise has to hold.

### Activity during the hackathon

The client changes the portfolio mid-flight:
- Sells the whole XLF position — 900 shares — on September 2nd
- Buys 100 shares of AAPL on September 3rd

| Day | Date | Client does | Agent does |
|---|---|---|---|
| Day 1 | Aug 28 | - | Steps in — sends the protection |
| Day 2 | Aug 31 | - | Day one's limits expired unfilled; prices and sends again |
| Day 3 | Sep 1 | - | **Nothing.** The promise holds and the book has not moved |
| Day 4 | Sep 2 | Sells 900 XLF | Risk drops — hands back all nine XLF puts |
| Day 5 | Sep 3 | Buys 100 AAPL | Risk rises again — hedges the new holding on its own underlying |
| Day 6 | Sep 4 | - | Confirms the mandate still holds |

**Day 2 is not in the original script.** Day one's orders were limits at the
ask, the ask moved while the cycle was still running, and they expired unfilled
over the weekend — journalled as `order.working`, never as a purchase. The
promise is not held by one order landing; it is held by the same question being
asked every morning until the answer is yes. That is the version of this the
week actually produced, and it is left in rather than tidied away.

**Day 3 is the claim being tested.** An agent that traded on a quiet Tuesday
would be an agent that trades because it is running, and every number it
reported afterwards would have to be read in that light.

The portfolio is intentionally not static. A client who never touches their allocation isn't the point. That gives Drawdown Guard a real job: it keep the changing portfolio aligned with a fixed risk mandate.

## ⚙️ How the agent actually works

Seven steps, once every weekday, fully autonomous. Five of them are nodes in
the LangGraph cycle; the stress ladder runs inside **Mandate** and the risk
gate runs inside **Execute**, because a gate that can be skipped by taking a
different path is not a gate.

| # | Step | The agent does | Where it lives |
|---|---|---|---|
| 1️⃣ | **Reconcile** | Asks the broker what is actually held — never assumes | `reconcile` node |
| 2️⃣ | **Mandate** | Turns the client's tolerance into a live dollar budget | `mandate` node |
| 3️⃣ | **Stress** | Runs the book down the whole descent and measures the uncovered risk | inside `mandate` |
| 4️⃣ | **Protect** | Solves for the cheapest hedge that covers it, sleeve by sleeve | `protect` node |
| 5️⃣ | **Gate** | Checks every order against hard limits before it can reach the broker | inside `execute` |
| 6️⃣ | **Execute** | Sends the approved orders, then reads back what the broker actually did with each | `execute` node |
| 7️⃣ | **Journal** | Writes down what happened and why, in plain language, with an LLM | `journal` node |

### 1. Reconcile

The cycle starts by reading the client's account, not by trusting what the
agent thought it owned yesterday. Assignment and expiry happen overnight
without asking anyone, and the only evidence is that the broker's positions no
longer match the agent's record. Every number below is computed from what is
actually there this morning.

### 2. Mandate — the budget

The client's sentence becomes a dollar figure:

```
10% × $100,000  =  $10,000
       └── the reference ──┘
```

**The reference does not move.** It is the account value on the day the promise
started, written down once and held for the whole twelve months.

This matters more than it looks. A budget of "10% of whatever the account is
worth this morning" is not a promise — it is a promise that re-bases. Lose ten
percent and tomorrow the agent defends ten percent of the smaller number, then
ten percent of the one after that. Five steps of that permits a **47% loss** and
reports every one of them as kept. Worse, the budget shrinks fastest exactly
when a fall has already begun, so the agent would buy *less* protection
precisely as it became most necessary.

A high-water mark was the other candidate and was rejected for a different
reason: it moves when the market makes a new high, so the agent would re-strike
its hedge because prices went **up**. This project's whole claim is that it
never acts on where the market is going. One place where it does is enough to
lose the argument.

### 3. Stress — the ladder, and the risk

The agent prices the book at four published shocks, the same four every day:

| If the market falls | The portfolio loses | Budget | Verdict |
|---|---:|---:|---|
| −5% | $4,110 | $10,000 | ✅ inside the promise |
| −10% | $8,219 | $10,000 | ✅ inside the promise |
| −20% | $16,438 | $10,000 | 🚨 **$6,438 past it** |
| −35% | $28,766 | $10,000 | 🚨 **$18,766 past it** |

The rungs are fixed and published on purpose. A ladder that moved with the
market would let a bad day quietly redefine what "safe" means.

**This is not a forecast.** The agent does not say the market will fall 20%. It
says: *if it did, this book would break a promise that was already made.*

## 📏 What "uncovered risk" actually measures

This is the number everything else hangs on, and it is **not** the −20% row
above. That row is the one a human reads. The number the agent sizes against is
different, and larger, and the difference is the whole idea.

**Nobody can name the next shock.** Not the client, not the agent. A promise
that only holds down to a depth somebody guessed is not much of a promise. So
the agent does not pick a rung. It asks the question that needs no guess:

> What is the most this book can lose, **anywhere on the way down**?

For a book of bare shares, the answer is *everything*. The loss keeps growing
as the price approaches zero — there is no bottom, so the worst case is the
entire equity exposure:

```
worst case (unprotected)    $82,190     ← the whole equity sleeve
downside budget           − $10,000
─────────────────────────────────────
uncovered risk              $72,190     ← risk nobody has agreed to carry
```

That is why the journal reports seventy-two thousand of uncovered risk on a book
whose −20% shortfall is only $6,438. Both numbers are true and they answer
different questions. The agent acts on the first one.

**It is deliberately not called a "gap."** This field was named `gap` for most
of the project's life and the name was doing real damage: a gap reads as money
that has to be found, and a reader seeing "gap: 72,190" on a $100,000 account
reasonably concludes something has gone badly wrong. Nothing has. The budget is
the covered part — $10,000 the client has agreed in advance to absorb — and the
rest is simply risk nobody has taken responsibility for. It is closed by
**$4,159 of premium**, which would be impossible if it were a shortfall in
dollars. The word `shortfall` is reserved for the numbers that really are
one: `shortfall_at_shock`, and the per-rung `shortfall` inside the ladder.

**Matching puts to shares is what makes the answer finite.** One contract per
hundred shares, and below the strike every dollar the shares lose is a dollar
the puts gain. The line stops falling. The worst case becomes a number you can
write down:

```
worst case (protected)  =  the fall down to the strike  +  the premium paid
                           └── unprotected drop ──┘        └── certain ──┘
```

Both terms move against each other as the strike moves — lower strike, further
to fall, cheaper premium — so the total is monotonic and the answer is unique.
The agent takes the **lowest strike that still fits inside the budget**. Go
lower and the unprotected drop alone spends the promise. Go higher and the
client pays for protection they never asked for.

A real sleeve, priced on the live chain: XLF at $58.25 with a $6,379 share of
the budget — its share because it is 900 of the book's shares, and a symbol
that can lose most of the money is allowed most of the promise.

```
buy 9 × XLF 54 put @ 2.61

fall to the strike    (58.25 − 54) × 900 shares  =  $3,825
premium                       2.61 × 9 × 100     =  $2,349
                                                    ───────
worst case                                          $6,174   ≤  $6,379  ✅
```

Below $54 the book stops losing. At −20%, at −50%, at whatever comes. **No
scenario had to be guessed**, and nothing here depends on anyone being right
about the future.

> ⚠️ **Note for the demo:** the measure is charged for the hedge's own cost. A
> hedge sized as though its premium were free comes up short by exactly that
> premium — which is the amount that has to come out of the same account the
> promise is written against.

## 🔗 The chain of decision

**1️⃣ How much protection?** Enough that the worst case *at any depth* is the
promised 10% — no more, and pointedly no less. Protection is a cost, and a
dollar spent past the promise is a dollar taken from the client for nothing, so
the agent solves for the strike rather than rounding up to something that feels
safe.

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

So the agent buys protection dated **past the end of the promise** — the window
is the horizon itself out to a year beyond it — and then leaves it alone. 🧘 An
agent that reshuffles its hedge every week pays the spread every week, and that
bill arrives whether or not the crash ever does.

**5️⃣ At what price does the order go?** 💵 Every order is a limit, never a
market order — an options spread is wide and crossing it repeatedly is how a
bot donates its premium one contract at a time.

But a limit set *exactly* at the offer fills only if nobody moves, and this is
the one rule here that was written by the market rather than by us. On the
first live day two protective puts were sent at the ask, the ask rose a few
cents while the cycle was still running, and both sat unfilled until the close:

| | our limit | ask, minutes later | filled |
|---|---:|---:|---|
| XLF 54 put ×9 | 2.64 | 2.72 | ❌ |
| IWM 275 put ×1 | 14.25 | 14.37 | ❌ |

**$71,985 of risk left uncovered overnight to avoid paying $20.** So the limit
now reaches a quarter of the spread past the side being crossed to — still
never the mid, which is a price nobody is offering.

A *fraction of the spread* rather than a number of cents, because two cents is
0.8% of a $2.64 contract and 6.7% of a $0.30 one. It also adapts: an hour
before the close those same two spreads had more than doubled, and the
tolerance went from two cents to five and ten on its own. And it is bounded by
a limit that already existed — the gate refuses anything wider than 5%, so a
quarter of that is **1.25% past the offer in the worst case it admits at all**.

The premium is charged where the promise is sized, not discovered at the fill:
a strike admitted on a price the agent will not pay is a strike sized against
the wrong number.

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

Never on a hunch, and never because it thinks it knows what the market will do
next. The trigger is one line of arithmetic, recomputed every morning:

```
uncovered_risk = worst_loss(what is held today) − budget      > 0  →  act
```

Because the budget is fixed and the book is not, there are exactly four ways
that line can turn positive — all of them mechanical, none of them a market
call:

| | Trigger | Why it uncovers risk |
|---|---|---|
| 📈 | **The portfolio grew** | More exposure behind the same fixed budget. The floor has to be re-struck. |
| 🛒 | **The client bought** | New holdings arrive unhedged. Adding protection when a client invests is the unglamorous half of the job. |
| ⏳ | **The hedge aged** | The market moved and the strike that used to hold the floor no longer reaches it. |
| 📅 | **Something expired** | Coverage silently ended. Nothing but recomputation notices. |

And one way it turns negative — the client sold, or the book fell back inside
its budget — which is the release above.

Not one of these is a forecast. They are all arithmetic on what the account
already holds, which is why the agent trades rarely, deliberately, and can
explain every trade it makes without ever claiming to know the future.

### What the client actually gets 🎁

Contracts matched one for every hundred shares, struck below the market, dated
past the client's twelve months. Here is the same portfolio before and after:

| If the market falls | Without the agent | With the agent | |
|---|---:|---:|---|
| −5% | loses 4.0% | loses 6.0% | 💸 the premium, and this is what it costs |
| −10% | loses 8.0% | loses **10.0%** | at the line |
| −20% | loses **16.0%** 🚨 | loses **10.0%** | ✅ the promise, kept |
| −35% | loses **28.0%** 🚨 | loses **10.0%** | ✅ the promise, kept |
| −50% | loses **40.0%** 🚨 | loses **10.0%** | ✅ the promise, kept |

**The floor does not care how far the market falls.** Below the strike, every
dollar the shares lose is a dollar the puts gain, so the line simply stops
going down — at −20%, at −50%, at whatever comes.

And read the first row, because it is the honest one. 📏 In a mild dip the
client is **worse off by the premium** — 6% instead of 4%. That is not a flaw
to be explained away; that is what insurance is. You pay every year to be whole
in the year that matters.

**Name the promise and its window → check the book against it → solve for the
protection that floors the loss → buy it long → hand it back when it is no
longer needed.** Every weekday, in writing. 📓

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
| ♻️ **Hedge rebalancers for equity portfolios** | Adds protection when risk is uncovered and *releases it* when it no longer is, on a margin band |

## 🧰 Technologies

| | |
|---|---|
| 🦙 **Alpaca Trading API** | Live paper account — equities and options, level 3 |
| 🔌 **Alpaca MCP Server** | Every broker call goes through MCP. Read-only toolsets for the AI. Order tools reachable only on the deterministic path |
| 🧠 **Google Gemini** | Writes the plain-language explanation of the decision the arithmetic already made |
| 🕸️ **LangGraph** | The five-node cycle, including the conditional halt edge |
| 🔗 **LangChain** | Model plumbing and structured output for the analyst |
| 🔢 **NumPy · SciPy · pandas** | Black-Scholes, the stress ladder, the payoff maths |
| ✅ **Pydantic** | Mandates and limits are validated types — an impossible promise fails at load time, not at runtime |
| ⚙️ **GitHub Actions** | The agent's heartbeat — one autonomous cycle every weekday |
| 🌐 **GitHub Pages** | The live status page, rebuilt from the journal after every cycle |
| 🐍 **Python 3.11** | The language everything runs on |

## ▶️ Try it

```bash
uv sync
cp .env.example .env                          # your Alpaca paper keys
uv run python3 scripts/healthcheck.py         # says why it won't trade, if it won't
uv run python3 scripts/run_cycle.py --dry-run # decides, journals, submits nothing
uv run python3 scripts/build_site.py          # rebuilds the status page
uv run pytest
```

🔐 `ALPACA_PAPER_TRADE=true` is a **hard interlock** — the program refuses to
start without it. This has never traded real money and cannot.

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
docs/          the published status page
```

The risk gate is imported by the execution path rather than reimplemented
beside it. That is the whole reason to trust it: a second copy of a limit is a
limit that will disagree with itself the first time one copy is edited.
