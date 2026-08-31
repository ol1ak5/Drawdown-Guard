// The thing that presses the button on time.
//
// WHY THIS EXISTS
// ---------------
// GitHub's scheduled workflows are a request, not a promise. A cycle booked
// for 14:00 UTC arrived at 23:37 on 2026-08-27 and 23:24 on 2026-08-28 -- by
// which time the market had been shut for hours, the healthcheck declined
// correctly, and the day produced no cycle at all under a green tick. Two days
// of an eight-day unattended run were lost that way.
//
// So the schedule moved somewhere that keeps time. This worker does one thing:
// at 09:45 New York it asks GitHub to run the workflow. Everything else is
// unchanged -- the cycle still runs in Actions, still journals, still commits.
// Only the trigger moved.
//
// The workflow's own cron entries are deliberately still there. This is a
// second way of asking, not a replacement for the first, and a day that
// somehow gets asked for twice is refused the second time by
// `check_not_already_run`.

const WORKFLOW =
  "https://api.github.com/repos/ol1ak5/Drawdown-Guard/actions/workflows/trade.yml/dispatches";

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },

  // Also reachable by hand, for the morning something looks wrong and the
  // answer is "ask it to run now". Guarded by the same secret the schedule
  // uses, so the URL alone is not enough to trade with.
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.searchParams.get("key") !== env.TRIGGER_KEY) {
      return new Response("no", { status: 403 });
    }
    const result = await dispatch(env);
    return new Response(result, { status: result === "dispatched" ? 200 : 502 });
  },
};

async function dispatch(env) {
  const response = await fetch(WORKFLOW, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      // GitHub rejects an API call with no user agent, and the rejection
      // reads like an auth failure. Named so a log line says who asked.
      "User-Agent": "drawdown-guard-scheduler",
    },
    body: JSON.stringify({ ref: "main" }),
  });

  // 204 is the success. Anything else is logged with its body: a failure that
  // prints only a status code is a morning spent guessing between an expired
  // token, a renamed workflow and a branch that does not exist.
  if (response.status === 204) return "dispatched";
  const detail = await response.text();
  console.error(`dispatch failed: ${response.status} ${detail}`);
  return `failed: ${response.status}`;
}
