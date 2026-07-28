/**
 * Shared probe / citation history fetches for over-time charts.
 *
 * VisibilityOverTime, SentimentOverTime, and BrandCompetitorVisibility each
 * need the same /probe-history payload — without dedupe the overview fires
 * three identical heavy requests on mount.
 */
import { auditSlug } from "./auditPath";
import type { CitationHistoryResponse, ProbeHistoryResponse } from "../types";

const CACHE_TTL_MS = 60_000;

type CacheEntry<T> = {
  data: T;
  expiresAt: number;
};

const probeCache = new Map<string, CacheEntry<ProbeHistoryResponse>>();
const probeInflight = new Map<string, Promise<ProbeHistoryResponse>>();
const citationCache = new Map<string, CacheEntry<CitationHistoryResponse>>();
const citationInflight = new Map<string, Promise<CitationHistoryResponse>>();

function cacheGet<T>(map: Map<string, CacheEntry<T>>, key: string): T | null {
  const hit = map.get(key);
  if (!hit) return null;
  if (Date.now() > hit.expiresAt) {
    map.delete(key);
    return null;
  }
  return hit.data;
}

function cacheSet<T>(map: Map<string, CacheEntry<T>>, key: string, data: T): void {
  map.set(key, { data, expiresAt: Date.now() + CACHE_TTL_MS });
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url, { credentials: "include" });
  if (!response.ok) {
    throw new Error(`History request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

/** Deduped + short-TTL cached probe-history (metrics summaries only). */
export function fetchProbeHistory(
  auditDirOrSlug: string,
  opts?: { force?: boolean },
): Promise<ProbeHistoryResponse> {
  const key = auditSlug(auditDirOrSlug);
  if (!opts?.force) {
    const cached = cacheGet(probeCache, key);
    if (cached) return Promise.resolve(cached);
    const pending = probeInflight.get(key);
    if (pending) return pending;
  }

  const url = `/api/audits/${encodeURIComponent(key)}/probe-history`;
  const promise = getJson<ProbeHistoryResponse>(url)
    .then((raw) => {
      const normalized: ProbeHistoryResponse = {
        entries: Array.isArray(raw.entries) ? raw.entries : [],
        total: typeof raw.total === "number" ? raw.total : (raw.entries?.length ?? 0),
      };
      cacheSet(probeCache, key, normalized);
      return normalized;
    })
    .finally(() => {
      probeInflight.delete(key);
    });

  probeInflight.set(key, promise);
  return promise;
}

/** Kick off probe-history after paint without awaiting (warm cache for charts). */
export function prefetchProbeHistory(auditDirOrSlug: string): void {
  if (!auditDirOrSlug) return;
  void fetchProbeHistory(auditDirOrSlug).catch(() => undefined);
}

/** Deduped + short-TTL cached citation-history. */
export function fetchCitationHistory(
  auditDirOrSlug: string,
  opts?: { force?: boolean },
): Promise<CitationHistoryResponse> {
  const key = auditSlug(auditDirOrSlug);
  if (!opts?.force) {
    const cached = cacheGet(citationCache, key);
    if (cached) return Promise.resolve(cached);
    const pending = citationInflight.get(key);
    if (pending) return pending;
  }

  const url = `/api/audits/${encodeURIComponent(key)}/citation-history`;
  const promise = getJson<CitationHistoryResponse>(url)
    .then((raw) => {
      const normalized: CitationHistoryResponse = {
        ...raw,
        rows: Array.isArray(raw.rows) ? raw.rows : [],
        dates: Array.isArray(raw.dates) ? raw.dates : [],
      };
      cacheSet(citationCache, key, normalized);
      return normalized;
    })
    .finally(() => {
      citationInflight.delete(key);
    });

  citationInflight.set(key, promise);
  return promise;
}

/** Test helper — clear in-memory caches between vitest cases. */
export function clearProbeHistoryFetchCache(): void {
  probeCache.clear();
  probeInflight.clear();
  citationCache.clear();
  citationInflight.clear();
}
