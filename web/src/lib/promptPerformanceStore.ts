/**
 * Shared in-memory cache for prompt-performance context so report sections
 * do not each re-fetch the same large payload from the API/GCS.
 *
 * Huge multi-locale audits bootstrap with a single preferred locale (via
 * ``loadPromptPerformanceBootstrap``) so Citations / Prompts are not blocked
 * on an all-locales slim GET or client Overall merge.
 */
import { useCallback, useEffect, useSyncExternalStore } from "react";
import { invalidatePromptPerformanceDetailCache } from "../api/client";
import { auditSlug } from "./auditPath";
import { OVERALL_LOCALE_KEY, collectAvailableLiveProbes } from "./localeProbeView";
import { loadPromptPerformanceBootstrap } from "./promptPerformanceBootstrap";
import type { PromptPerformanceContext } from "../types";

type StoreStatus = "idle" | "loading" | "ready" | "error";

/** What the current cached payload covers. */
export type PromptPerfLoadScope =
  | { kind: "bootstrap" }
  | { kind: "locale"; locale: string }
  | { kind: "all" }
  | { kind: "overall" };

type StoreEntry = {
  status: StoreStatus;
  data: PromptPerformanceContext | null;
  error: string | null;
  promise: Promise<PromptPerformanceContext> | null;
  version: number;
  scope: PromptPerfLoadScope | null;
};

const entries = new Map<string, StoreEntry>();
const listeners = new Map<string, Set<() => void>>();

function keyFor(auditDirOrSlug: string): string {
  return auditSlug(auditDirOrSlug);
}

function ensureEntry(key: string): StoreEntry {
  let entry = entries.get(key);
  if (!entry) {
    entry = {
      status: "idle",
      data: null,
      error: null,
      promise: null,
      version: 0,
      scope: null,
    };
    entries.set(key, entry);
  }
  return entry;
}

function notify(key: string): void {
  const set = listeners.get(key);
  if (!set) return;
  for (const listener of set) listener();
}

export type EnsurePromptPerformanceOpts = {
  /** Force all-locales slim GET (user selected Overall). */
  allLocales?: boolean;
  /** Load a specific locale (or ``__overall__`` server merge). */
  locale?: string;
};

export function getPromptPerformanceEntry(auditDirOrSlug: string): StoreEntry {
  return ensureEntry(keyFor(auditDirOrSlug));
}

export function subscribePromptPerformance(
  auditDirOrSlug: string,
  onStoreChange: () => void,
): () => void {
  const key = keyFor(auditDirOrSlug);
  let set = listeners.get(key);
  if (!set) {
    set = new Set();
    listeners.set(key, set);
  }
  set.add(onStoreChange);
  return () => {
    set?.delete(onStoreChange);
  };
}

/** True when enough locale per_prompt rows exist for a meaningful Overall merge. */
export function contextHasMultiLocaleRows(
  ctx: PromptPerformanceContext | null | undefined,
): boolean {
  return collectAvailableLiveProbes(ctx).length >= 2;
}

function scopeSatisfies(
  entry: StoreEntry,
  opts?: EnsurePromptPerformanceOpts,
): boolean {
  if (entry.status !== "ready" || !entry.data) return false;
  if (opts?.allLocales) {
    return entry.scope?.kind === "all" || contextHasMultiLocaleRows(entry.data);
  }
  if (opts?.locale) {
    if (opts.locale === OVERALL_LOCALE_KEY) {
      return (
        entry.scope?.kind === "overall"
        || entry.scope?.kind === "all"
        || contextHasMultiLocaleRows(entry.data)
        || Boolean(entry.data.live_probe?.per_prompt?.length)
      );
    }
    const rows = entry.data.locale_probes?.[opts.locale]?.live_probe?.per_prompt?.length
      ?? (opts.locale === entry.data.default_locale_key
        ? entry.data.live_probe?.per_prompt?.length
        : 0)
      ?? 0;
    return rows > 0;
  }
  // Bootstrap / default: any ready payload is fine.
  return true;
}

function runFetch(
  key: string,
  entry: StoreEntry,
  opts?: EnsurePromptPerformanceOpts,
): Promise<PromptPerformanceContext> {
  entry.status = "loading";
  entry.error = null;
  notify(key);

  const nextScope: PromptPerfLoadScope = opts?.allLocales
    ? { kind: "all" }
    : opts?.locale === OVERALL_LOCALE_KEY
      ? { kind: "overall" }
      : opts?.locale
        ? { kind: "locale", locale: opts.locale }
        : { kind: "bootstrap" };

  entry.promise = loadPromptPerformanceBootstrap(key, {
    allLocales: opts?.allLocales,
    locale: opts?.locale,
  })
    .then((data) => {
      entry.data = data;
      entry.status = "ready";
      entry.error = null;
      entry.promise = null;
      entry.scope = nextScope;
      entry.version += 1;
      notify(key);
      return data;
    })
    .catch((err) => {
      entry.status = "error";
      entry.error = err instanceof Error ? err.message : "Failed to load";
      entry.promise = null;
      entry.version += 1;
      notify(key);
      throw err;
    });

  return entry.promise;
}

export function ensurePromptPerformance(
  auditDirOrSlug: string,
  opts?: EnsurePromptPerformanceOpts,
): Promise<PromptPerformanceContext> {
  const key = keyFor(auditDirOrSlug);
  const entry = ensureEntry(key);
  if (scopeSatisfies(entry, opts)) {
    return Promise.resolve(entry.data!);
  }
  // Reuse in-flight bootstrap only when the caller has no stronger scope need.
  if (entry.promise && !opts?.allLocales && !opts?.locale) {
    return entry.promise;
  }
  return runFetch(key, entry, opts);
}

/** Refetch while keeping previous data visible (no loading flash). */
export function refreshPromptPerformance(
  auditDirOrSlug: string,
  opts?: EnsurePromptPerformanceOpts,
): Promise<PromptPerformanceContext> {
  const key = keyFor(auditDirOrSlug);
  const entry = ensureEntry(key);
  if (entry.promise && !opts?.allLocales && !opts?.locale) return entry.promise;

  if (!entry.data) {
    entry.status = "loading";
    entry.error = null;
    notify(key);
  }

  const nextScope: PromptPerfLoadScope = opts?.allLocales
    ? { kind: "all" }
    : opts?.locale === OVERALL_LOCALE_KEY
      ? { kind: "overall" }
      : opts?.locale
        ? { kind: "locale", locale: opts.locale }
        : entry.scope ?? { kind: "bootstrap" };

  entry.promise = loadPromptPerformanceBootstrap(key, {
    allLocales: opts?.allLocales ?? entry.scope?.kind === "all",
    locale: opts?.locale,
  })
    .then((data) => {
      entry.data = data;
      entry.status = "ready";
      entry.error = null;
      entry.promise = null;
      entry.scope = nextScope;
      entry.version += 1;
      notify(key);
      return data;
    })
    .catch((err) => {
      if (!entry.data) {
        entry.status = "error";
        entry.error = err instanceof Error ? err.message : "Failed to load";
      }
      entry.promise = null;
      entry.version += 1;
      notify(key);
      throw err;
    });

  return entry.promise;
}

export function invalidatePromptPerformance(auditDirOrSlug: string): void {
  const key = keyFor(auditDirOrSlug);
  const entry = ensureEntry(key);
  entry.data = null;
  entry.status = "idle";
  entry.error = null;
  entry.promise = null;
  entry.scope = null;
  entry.version += 1;
  notify(key);
  // Overlay reply bodies must not outlive a re-probe / context reset.
  invalidatePromptPerformanceDetailCache(auditDirOrSlug);
}

export function setPromptPerformanceData(
  auditDirOrSlug: string,
  data: PromptPerformanceContext,
  scope: PromptPerfLoadScope = { kind: "bootstrap" },
): void {
  const key = keyFor(auditDirOrSlug);
  const entry = ensureEntry(key);
  entry.data = data;
  entry.status = "ready";
  entry.error = null;
  entry.promise = null;
  entry.scope = scope;
  entry.version += 1;
  notify(key);
}

export function usePromptPerformanceContext(auditDirOrSlug: string) {
  const entry = useSyncExternalStore(
    (onStoreChange) => subscribePromptPerformance(auditDirOrSlug, onStoreChange),
    () => getPromptPerformanceEntry(auditDirOrSlug),
    () => getPromptPerformanceEntry(auditDirOrSlug),
  );

  useEffect(() => {
    void ensurePromptPerformance(auditDirOrSlug).catch(() => {
      /* error stored on entry */
    });
  }, [auditDirOrSlug]);

  const refetch = useCallback(async (opts?: EnsurePromptPerformanceOpts) => {
    return refreshPromptPerformance(auditDirOrSlug, opts);
  }, [auditDirOrSlug]);

  const ensureScope = useCallback(async (opts: EnsurePromptPerformanceOpts) => {
    return ensurePromptPerformance(auditDirOrSlug, opts);
  }, [auditDirOrSlug]);

  const setCtx = useCallback(
    (data: PromptPerformanceContext) => {
      setPromptPerformanceData(auditDirOrSlug, data);
    },
    [auditDirOrSlug],
  );

  return {
    ctx: entry.data,
    loading: entry.status === "idle" || entry.status === "loading",
    error: entry.error,
    scope: entry.scope,
    refetch,
    ensureScope,
    setCtx,
  };
}
