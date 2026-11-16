/**
 * Daily trigger for the kidnews "Daily story" workflow.
 *
 * GitHub's scheduled workflows are best-effort and may be delayed or dropped;
 * Cloudflare Cron Triggers run on time. At 12:17 UTC this asks GitHub to run
 * daily.yml (task "both": publish approved stories, then draft today's).
 *
 * A failed request throws, so it shows as an error in the Worker's logs
 * (npx wrangler tail kidnews-scheduler). It has no public URL.
 */

import { dispatch, type Env } from "./dispatch";

export default {
  async scheduled(_controller: ScheduledController, env: Env): Promise<void> {
    await dispatch(env);
  },
  async fetch(): Promise<Response> {
    return new Response("Not found.", { status: 404 });
  },
};
