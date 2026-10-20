import { describe, expect, it, vi } from "vitest";

vi.mock("./promptPerformanceBootstrap", () => ({
  loadPromptPerformanceBootstrap: vi.fn(),
}));
vi.mock("../api/client", () => ({ invalidatePromptPerformanceDetailCache: vi.fn() }));

import { loadPromptPerformanceBootstrap } from "./promptPerformanceBootstrap";
import {
  ensurePromptPerformance,
  getPromptPerformanceSnapshot,
  invalidatePromptPerformance,
  setPromptPerformanceData,
  subscribePromptPerformance,
} from "./promptPerformanceStore";
import type { PromptPerformanceContext } from "../types";

const ctx = (name: string) => ({ brand_name: name }) as unknown as PromptPerformanceContext;

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("promptPerformanceStore snapshots", () => {
  it("returns a new snapshot object on every change (so React re-renders)", async () => {
    const d = deferred<PromptPerformanceContext>();
    vi.mocked(loadPromptPerformanceBootstrap).mockReturnValueOnce(d.promise);
    const listener = vi.fn();
    const unsub = subscribePromptPerformance("audit-a", listener);

    const idle = getPromptPerformanceSnapshot("audit-a");
    expect(getPromptPerformanceSnapshot("audit-a")).toBe(idle); // stable when unchanged

    const pending = ensurePromptPerformance("audit-a");
    const loading = getPromptPerformanceSnapshot("audit-a");
    expect(loading).not.toBe(idle);
    expect(loading.status).toBe("loading");

    d.resolve(ctx("A"));
    await pending;
    const ready = getPromptPerformanceSnapshot("audit-a");
    expect(ready).not.toBe(loading);
    expect(ready.status).toBe("ready");
    expect(ready.data?.brand_name).toBe("A");
    expect(listener).toHaveBeenCalledTimes(2);
    unsub();
  });

  it("ignores responses superseded by a newer request", async () => {
    const older = deferred<PromptPerformanceContext>();
    const newer = deferred<PromptPerformanceContext>();
    vi.mocked(loadPromptPerformanceBootstrap)
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);

    const first = ensurePromptPerformance("audit-b");
    const second = ensurePromptPerformance("audit-b", { locale: "GB:en" });
    newer.resolve(ctx("new"));
    await second;
    older.reject(new Error("timeout"));
    await expect(first).rejects.toThrow("timeout");

    const snap = getPromptPerformanceSnapshot("audit-b");
    expect(snap.status).toBe("ready");
    expect(snap.data?.brand_name).toBe("new");
    expect(snap.error).toBeNull();
  });

  it("setPromptPerformanceData and invalidate produce new snapshots", () => {
    setPromptPerformanceData("audit-c", ctx("C"));
    const a = getPromptPerformanceSnapshot("audit-c");
    invalidatePromptPerformance("audit-c");
    const b = getPromptPerformanceSnapshot("audit-c");
    expect(b).not.toBe(a);
    expect(b.data).toBeNull();
    expect(b.status).toBe("idle");
  });
});
