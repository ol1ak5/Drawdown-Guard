# The scheduler

One worker with one job: ask GitHub to run the trading cycle at 09:45 New
York, on a clock that keeps time.

## Why it is not just the workflow's own cron

It was. GitHub's scheduled workflows sit in a shared queue and arrive when they
arrive: the 14:00 UTC booking landed at 23:37 on 2026-08-27 and 23:24 on
2026-08-28, hours after the market shut. The healthcheck declined both
correctly, so both runs are green in the list and both days produced no cycle.
Nothing about that is visible without reading logs.

Cloudflare's cron is not a guarantee either, but it misses by seconds where
GitHub missed by nine hours.

The workflow's own six cron entries are still there on purpose. This is a
second way of asking, and a day asked for twice is declined the second time by
`check_not_already_run` in `scripts/healthcheck.py`.

## Deploying

You need a GitHub token. Create it yourself and put it in Cloudflare yourself
-- it should not pass through anything else.

1. GitHub → Settings → Developer settings → **Fine-grained personal access
   token**, scoped to `ol1ak5/Drawdown-Guard` only, with **Actions: Read and
   write**. Nothing else. Give it an expiry past the event.

2. From this directory:

   ```
   npx wrangler login
   npx wrangler secret put GH_TOKEN
   npx wrangler secret put TRIGGER_KEY
   npx wrangler deploy
   ```

   `TRIGGER_KEY` is any long random string. It guards the manual URL below so
   that knowing the worker's address is not enough to start a cycle.

3. Check it works without waiting for tomorrow:

   ```
   curl "https://drawdown-guard-scheduler.<your-subdomain>.workers.dev/?key=<TRIGGER_KEY>"
   ```

   `dispatched` means GitHub accepted it; the run appears in Actions within a
   few seconds. Anything else prints the status, and `npx wrangler tail` shows
   the body GitHub sent back.

## When the clocks change

`crons` is UTC. `45 13` is 09:45 ET under EDT, which holds through
2026-11-01. After that it fires at 08:45 ET, before the open, and the
healthcheck will decline the day. Change it to `45 14` then.
