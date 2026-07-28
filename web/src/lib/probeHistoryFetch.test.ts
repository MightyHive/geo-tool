import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearProbeHistoryFetchCache,
  fetchCitationHistory,
  fetchProbeHistory,
} from "./probeHistoryFetch";

describe("probeHistoryFetch", () => {
  beforeEach(() => {
    clearProbeHistoryFetchCache();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("citation-history")) {
          return new Response(JSON.stringify({ rows: [], dates: [] }), { status: 200 });
        }
        return new Response(
          JSON.stringify({ entries: [{ date: "2026-07-01", summary: {} }], total: 1 }),
          { status: 200 },
        );
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearProbeHistoryFetchCache();
  });

  it("dedupes concurrent probe-history requests for the same audit", async () => {
    const [a, b, c] = await Promise.all([
      fetchProbeHistory("audit_output/foo"),
      fetchProbeHistory("foo"),
      fetchProbeHistory("foo"),
    ]);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(a.entries).toHaveLength(1);
    expect(b.total).toBe(1);
    expect(c.entries[0]?.date).toBe("2026-07-01");
  });

  it("serves cached probe-history without a second network call", async () => {
    await fetchProbeHistory("foo");
    await fetchProbeHistory("foo");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("force bypasses the probe-history cache", async () => {
    await fetchProbeHistory("foo");
    await fetchProbeHistory("foo", { force: true });
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("dedupes citation-history fetches", async () => {
    await Promise.all([fetchCitationHistory("bar"), fetchCitationHistory("bar")]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
