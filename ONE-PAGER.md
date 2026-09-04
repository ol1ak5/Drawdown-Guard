# Drawdown Guard - Technical One-Pager

**Track 03 · Hedging & Risk Protection Agents**

Drawdown Guard is an autonomous AI agent that keeps a real portfolio inside a client-stated maximum drawdown: *"In the worst case, I can tolerate a 10% loss per year."*

It re-checks the book every 30 minutes on every trading day, measures the downside, and adds or removes an options overlay as the portfolio changes. It can use protective puts or collars, but it never sells shares simply to repair a drawdown gap.

## 1 · AI Logic

Drawdown Guard combines hard arithmetic with LLM judgement.

**The arithmetic owns the promise.** It does every step where the promise could break: calculates the loss budget, allocates that budget across positions by risk contribution, sizes contracts with `ceil(shares / 100)`, solves the strike from *fall-to-strike + premium = budget*, and verifies that the final hedge actually closes the gap. If the numbers don't satisfy the mandate, the trade can't happen.

**The LLM owns the judgement around those numbers.** Google Gemini via LangChain handles the parts a formula can't: it reads what changed and what deserves attention, picks the eligible hedge that makes the most sense today, and explains the decision to the client.

So, the arithmetic decides what is allowed. The model decides which allowed option is best today.

Three model calls per cycle:

1. **Risk analyst.** The model receives the holdings, mandate and existing protection and looks for meaningful changes or risks that deserve attention. For instance, `XLF 900 → 0` ⇒ *"the existing XLF puts no longer stand behind an equity position; review them for removal."*
2. **Hedge chooser.** The deterministic engine first evaluates every available hedge structure and removes anything that doesn't fully close the uncovered gap and expires within the mandate window. The LLM then reasons over today's market conditions and picks the structure that fits best: *"At today's prices, the collar gives up more upside than it saves in premium, so the outright put is the better hedge."* The model can choose the structure, but it can never choose one that fails the mathematical constraint. If the model fails or returns an invalid choice, the deterministic rule takes over.
3. **Client's note writer.** Once the orders have been approved, placed and read back from the broker, the model turns the completed decision into a short client-facing explanation: what changed, what was done, why, and what it cost.

## 2 · Risk Gates

The **Gate** (`risk/gate.py`) is a pure, deterministic function. Every order passes it before reaching the broker.

Ten checks, most-severe first, stopping at the first breach:

| # | Check |
|---|---|
| 1 | Permitted purpose - a buy may only *reduce* risk |
| 2 | Never naked (`forbid_naked`, absolute) |
| 3 | Drawdown kill-line (15%) |
| 4 | Per-instrument concentration ≤ 25% |
| 5 | Total deployed capital ≤ 60% |
| 6 | Directional-exposure band (±50% of equity) |
| 7 | Vega budget ($500 per IV point) |
| 8 | Assignment probability ≤ 0.35 (writers only) |
| 9 | Open interest ≥ 500 |
| 10 | Bid–ask spread ≤ 5% |

A veto is journalled with its reason and the cycle stops. The order is never sent.

The controls around it:

- **Eligibility filter** - only hedges that fully close the gap and expire within the mandate window reach the model. Partial protection is never offered.
- **Kill-switch guard** - a drawdown breach, or a `HALT` file in the repo, ends the cycle. `touch HALT && git commit && git push` stops the agent remotely. Hedges already bought keep working.
- **Market-hours guard** - orders are sent only when the market is open.
- **Analyst tripwire** - the risk-analysis model holds a read-only toolset. If an order tool ever reaches it, that is journalled as a *configuration defect*, not a veto.
- **Paper interlock** - `ALPACA_PAPER_TRADE=true` is a hard precondition. The program refuses to start without it, and has never traded real money.

## 3 · Alpaca Infrastructure Implementation

- **Paper account.** Everything runs on a single Alpaca **paper** account ($100,000, options trading level 3). ㅆhe program is hard-locked to paper mode. Starting book: 100 IWM · 900 XLF · 100 BIL + cash.
- **Alpaca Trading API.** The agent uses the API to read the account, positions and buying power, pull live quotes and the full option chain, submit option orders, and read their fills back. The historical daily bars behind the volatility estimates come from the Alpaca market-data API through the `alpaca-py` SDK (cached locally as parquet).
- **Alpaca MCP Server.** Those broker calls go through the Alpaca MCP Server rather than the REST client directly, so the agent works against a small, named set of tools: `get_account_info`, `get_all_positions`, `get_option_chain`, `get_option_contracts`, `get_option_snapshot` and `get_stock_latest_quote` to read; `place_option_order` to trade; `get_order_by_client_id` to confirm each fill. The risk-analyst call opens its own MCP session with read-only tools only and cannot place an order.

*Live `ol1ak5.github.io/Drawdown-Guard/` · Code `github.com/ol1ak5/Drawdown-Guard`*
