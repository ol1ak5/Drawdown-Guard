# Hackathon logistics

Source: [lablab.ai event page](https://lablab.ai/event/alpaca-ai-trading-agents-hackathon)
and [lablab.ai hackathon guidelines](https://lablab.ai/ai-articles/hackathon-guidelines),
read 2026-08-22.

## Event

| Item | Value |
|---|---|
| Name | Alpaca AI Trading Agents Hackathon |
| Format | fully online |
| Build window | 28 August – 4 September 2026 |
| Kick-off | Friday 28 August 2026, 17:00 CEST |
| Registered | ~1,635 at time of reading |
| Team | created 2026-08-22, solo, invite-only |

**Still not known:** the exact submission deadline and its timezone, the track
list, the judging rubric, and the prize structure. Fill these in as soon as they
become visible. Items below marked *(unconfirmed)* are inferred from lablab's
generic guidelines rather than the event page.

## Core requirements (confirmed, quoted from the event conditions)

> - **Autonomous agents** — participants must build autonomous AI trading agents
>   using Alpaca's Trading API.
> - **MCP or CLI** — projects must utilise either Alpaca's MCP server or its CLI
>   tools.
> - **Options trading** — all strategies must incorporate options trading.

How this project meets each:

| Requirement | How it is met |
|---|---|
| Autonomous | scheduled once per trading day, no human in the loop, decisions and rejections written to a committed journal |
| Trading API | Trading API, not Broker API. Broker API is for firms opening accounts on behalf of end users and is out of scope. |
| MCP or CLI | both. The MCP server backs the analyst role (read-only toolsets: account, stock-data, options-data, news). The CLI backs a pre-flight check before each run. |
| Options | the entire strategy is short options — cash-secured puts and covered calls. |

**Note on the "MCP or CLI" wording.** The requirement says projects must
*utilise* one of the two; it does not say every order must be routed through
them. Order placement stays deterministic Python. This is a deliberate boundary,
not an omission: the LLM is given read access to everything and write access to
nothing, and a test asserts the analyst is constructed without order tools. Say
this plainly in the demo rather than waiting to be asked.

## Alpaca accounts

Paper-only accounts need no funding and no KYC, and are available worldwide.

| Item | Value |
|---|---|
| Account limit | 3 paper accounts per login |
| Creation | dashboard → paper account number, top left → "Open New Paper Account" |
| Starting equity | chosen at creation, maximum 1,000,000 USD, **not editable afterwards** |
| Reset | no longer exists; delete the account and create a new one |
| API base URL | `https://paper-api.alpaca.markets` |
| API keys | generated per account; the secret is displayed once only |
| Options level | Level 1 covers covered calls and cash-secured puts, which is this entire strategy. **Verified 2026-08-22: the paper account is granted Level 3**, two levels more than we use. |

**Planned accounts:**

| Purpose | Equity | Keys live in |
|---|---|---|
| scratch (the original account) | 100,000 | nowhere — used only to prove keys can be read |
| `dev` | 1,000,000 | `.env` |
| `judging` | 1,000,000 | `.env.judging` |

**`dev` connected and verified 2026-08-22.** Account `PA3BER5PHGFO`, status ACTIVE,
equity and cash 1,000,000 USD, options level 3, options buying power 1,000,000,
trading not blocked. The key begins `PK`, which is the paper prefix — live keys
begin `AK`. That is a third independent confirmation of paper, alongside the
`ALPACA_PAPER_TRADE` interlock and the CLI's paper default.

**Reported buying power is 4,000,000 and must be ignored.** That is 4x margin.
Every position this agent takes is cash-secured, and the risk gate sizes against
`equity`, never against buying power. If a future change ever reads
`buying_power`, it has quietly turned a cash-secured strategy into a margin one.

**Market data verified the same day.** `OptionHistoricalDataClient.get_option_chain`
returns 1,749 SPY put contracts for expiries 3 to 16 days out, each with a quote,
an implied volatility, and greeks. Alpaca supplying greeks directly was not
assumed by the design; see the vega-units note in `handoff.md`.

**Why 1,000,000 rather than 250,000.** Option collateral is quantised: one
cash-secured put on SPY ties up roughly 60–65k, and that figure cannot be scaled
down because a contract is always 100 shares. On a 250k account a single
position is a quarter of the portfolio, so either the risk gate rejects
everything or its limits are set so wide they constrain nothing. At 1,000,000
the same contract is about 6% and ten to fifteen concurrent wheels across three
or four ETFs become possible, which is what makes the diversification claim in
the report true rather than aspirational.

The cost of the larger account is that idle cash dilutes percentage return. That
is addressed by the target-utilisation parameter in `risk.yaml` — aim for 60–70%
of capital posted as collateral — not by shrinking the account.

**Why `judging` is created now but connected later.** Creating an account starts
nothing; the equity curve begins at the first trade, not at the opening date.
Creating both today means any surprise — wrong options level, account limit
already reached, dashboard trouble — surfaces on a quiet Saturday instead of
during kick-off. The keys are not placed in GitHub Secrets until 27 August, and
`DRAWDOWNGUARD_ENV=dev` reads `.env`, so the judging account is never touched by a
development run. If it is contaminated anyway, delete and recreate takes two
minutes.

## Enrolment sequence

1. Complete the lablab.ai profile.
2. Enrol in the hackathon ("Enrol Now").
3. **Connect a Discord account** — required before a team can be created.
4. Create a team. Required even when building solo.
5. Submit the project through the form on the event page.

## Submission form fields

The Technical Details block is quoted from the event conditions and is
confirmed. The remaining fields are *(unconfirmed)* — taken from lablab's
generic guidelines.

| Field | Constraint |
|---|---|
| Submission title | max 50 characters *(unconfirmed)* |
| Short description | max 255 characters *(unconfirmed)* |
| Long description | minimum 100 words *(unconfirmed)* |
| Main tracks | selected from the event's list *(unconfirmed)* |
| Technologies | from lablab.ai/tech *(unconfirmed)* |
| Cover image | 16:9 recommended *(unconfirmed)* |
| Video presentation | link, under 5 minutes, under 300 MB *(unconfirmed)* |
| GitHub repository | public URL; extra repositories listed in the README |
| Demo Application Platform | where the application is deployed — a label, e.g. GitHub Pages |
| **Application URL** | *"Required for interactive evaluation"* — the link a judge opens |
| Additional information | free text, e.g. how the solution scales beyond the hackathon |

The two are a pair, not alternatives: the platform names the host, the URL is
the clickable link. **The parenthetical on the URL field is quoted from the real
form** (seen 2026-08-22); the surrounding rows are still inferred from lablab's
generic guidelines.

That parenthetical changes what the page has to be. "Interactive evaluation"
means a judge clicks and *does something*, not that a document loads.

## The demo-URL problem, and the decision taken

The submission form asks for a **live demo URL**. This agent is a scheduled job
with no user interface, so there is nothing to link — and the Streamlit
dashboard that would have covered it was, in the original plan, the first thing
on the cut list.

**Decision: publish a static status page to GitHub Pages instead of running a
dashboard server.** The agent already commits its journal and state snapshot
back to the repository after every cycle; one more step regenerates
`docs/index.html` from those files. This is strictly better than a hosted
dashboard for this project:

- nothing to keep running, so nothing can be down while a judge is looking;
- no cold start, no free-tier sleep, no secrets on a third-party host;
- it is read-only by construction — a public page that cannot touch the broker;
- it refreshes itself every trading day, which demonstrates the autonomy claim
  rather than merely asserting it.

Answers for the two form fields:

| Field | Answer |
|---|---|
| Demo Application Platform | GitHub Pages |
| Application URL | `https://<username>.github.io/drawdown-guard-agent/` |

Built at Task 12b, alongside the journal it reads.

### Static is not the same as non-interactive

The URL field says *"Required for interactive evaluation"*, and the first
version of the page was a read-only document. Those are not the same
constraint, and conflating them cost the page its strongest feature.

Two separate things were wanted:

- **No external resource.** No CDN, no font, no remote fetch. This one is real:
  a judge's click must not depend on someone else's uptime, and a page that
  cannot reach out cannot reach the broker. `test_the_page_references_no_external_resource`
  enforces it.
- **No JavaScript at all.** This one was never required and was smuggled in by
  the same test. An inline script makes no request and weakens nothing.

So the page keeps its single-file, zero-request shape and gains client-side
interaction: filter the decision log by symbol and by verdict, expand any row
to see the gate's full reasoning, and isolate the refusals. The refusals are the
argument, and letting a judge pull them up in one click makes the argument
themselves rather than reading a claim that it happened.

## Open items

- [ ] Record the real deadline, timezone, tracks, and rubric here.
- [ ] Record the final team name.
- [x] Record the options level actually granted on the paper accounts
      (closes spec §13.3) and confirm buying power (closes §13.2). Level 3,
      1,000,000 options buying power.
- [ ] Create the `judging` account and fill `.env.judging`.
- [x] Settle the vega units question. Fixed 2026-08-22: per contract, per one
      point of implied volatility. See `handoff.md`.
- [x] Decide whether the live agent uses Alpaca's greeks or our own. Ours, for
      consistency with the backtest; Alpaca's logged as a cross-check. See `handoff.md`.
- [x] Settle which LLM drives the analyst. Fixed 2026-08-24: **Gemini**, via
      `GOOGLE_API_KEY`, which the author already holds. Anthropic was the
      original plan and would have cost roughly two cents a day, but there is
      no reason to buy credits for a component whose provider is a cost
      decision rather than a safety one — the analyst may only tighten the risk
      parameters, never loosen them, so a weaker or differently-behaved model
      cannot talk the agent into a bad trade. Model: `gemini-3.7-flash`.
- [x] Create the public repository. Done 2026-08-24:
      `github.com/ol1ak5/Drawdown-Guard`. `DRAWDOWNGUARD_REPO_URL` still needs
      setting so the status page footer carries a real source link.
- [x] Connect Discord and create the team.
- [x] Confirm whether a demo URL is required — it is, and the answer is a
      GitHub Pages status page.
