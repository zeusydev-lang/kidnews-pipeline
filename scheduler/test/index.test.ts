import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dispatch, type Env } from "../src/dispatch";
import worker from "../src/index";

const env: Env = {
  GITHUB_TOKEN: "github_pat_test_not_real",
  REPO: "zeusydev-lang/kidnews-pipeline",
  WORKFLOW: "daily.yml",
  REF: "main",
};

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(console, "log").mockImplementation(() => {});
});
afterEach(() => vi.unstubAllGlobals());

describe("dispatch", () => {
  it("asks GitHub to run daily.yml on main with task both", async () => {
    await dispatch(env);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "https://api.github.com/repos/zeusydev-lang/kidnews-pipeline/actions/workflows/daily.yml/dispatches",
    );
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer github_pat_test_not_real");
    expect(init.headers["X-GitHub-Api-Version"]).toBe("2022-11-28");
    expect(JSON.parse(init.body)).toEqual({ ref: "main", inputs: { task: "both" } });
  });

  it.each([401, 403, 404, 422, 500])("throws on HTTP %i, without the token", async (status) => {
    fetchMock.mockResolvedValueOnce(new Response("details github_pat_test_not_real", { status }));
    const error = await dispatch(env).catch((e: Error) => e);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe(`GitHub refused the workflow dispatch: HTTP ${status}`);
    expect((error as Error).message).not.toContain("github_pat");
  });
});

describe("handlers", () => {
  it("the cron handler dispatches once", async () => {
    await worker.scheduled({} as ScheduledController, env);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("there is no public endpoint", async () => {
    const r = await worker.fetch();
    expect(r.status).toBe(404);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
