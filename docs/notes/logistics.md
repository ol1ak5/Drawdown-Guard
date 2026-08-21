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

**Not yet known — requires an enrolled account to see:** the exact submission
deadline and its timezone, the track list, the judging rubric, and the prize
structure. Fill these in immediately after enrolling. Everything below marked
*(unconfirmed)* is inferred from lablab's generic guidelines, not from the event
page.

## Enrolment sequence

1. Complete the lablab.ai profile.
2. Enrol in the hackathon ("Enrol Now").
3. **Connect a Discord account** — required before a team can be created.
4. Create a team. Required even when building solo.
5. Submit the project through the form on the event page.

## Submission form fields *(unconfirmed — from generic guidelines)*

| Field | Constraint |
|---|---|
| Submission title | max 50 characters |
| Short description | max 255 characters |
| Long description | minimum 100 words |
| Main tracks | selected from the event's list |
| Technologies | from lablab.ai/tech |
| Cover image | 16:9 recommended |
| Video presentation | **link**, under 5 minutes, under 300 MB |
| GitHub repository | public URL; extra repos listed in the README |
| Demo application platform | where the app is deployed |
| **Demo application URL** | **direct link to a live demo** |
| Additional information | free text, e.g. how the solution scales |

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

Scheduled as Task 12b on D4, alongside the journal it reads.

## Open items

- [ ] Enrol and record the real deadline, timezone, tracks, and rubric here.
- [ ] Confirm whether the demo URL field is mandatory or optional.
- [ ] Connect Discord and create the team.
- [ ] Record Alpaca paper account buying power and options entitlement level
      (closes spec §13.2 and §13.3).
