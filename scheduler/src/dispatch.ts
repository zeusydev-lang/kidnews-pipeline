/** Asks GitHub to run the daily workflow. Kept out of index.ts: the Workers
 * runtime treats every named export of the entry module as a handler. */

export interface Env {
  GITHUB_TOKEN: string;
  REPO: string; // owner/name
  WORKFLOW: string; // workflow file name
  REF: string; // branch
}

export async function dispatch(env: Env): Promise<void> {
  const url = `https://api.github.com/repos/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "kidnews-scheduler",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.REF, inputs: { task: "both" } }),
  });
  // GitHub answers 204 No Content when the run is queued.
  if (response.status !== 204) {
    // Status only: the body could echo request details.
    throw new Error(`GitHub refused the workflow dispatch: HTTP ${response.status}`);
  }
  console.log(`Queued ${env.WORKFLOW} on ${env.REPO}@${env.REF}`);
}
