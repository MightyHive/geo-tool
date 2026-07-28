/**
 * Locale view selection for multi-market probes: Overall + per market/language.
 */
import type { LiveProbeResult, PromptPerformanceContext, TopCitedSite, TopCitedUrl } from "../types";
import type { PromptLocale } from "./promptLocales";
import { normalizePromptLocales } from "./promptLocales";

export const OVERALL_LOCALE_KEY = "__overall__";

export type LocaleAvailability = "ready" | "missing" | "failed";

export interface LocaleViewOption {
  key: string;
  label: string;
  country?: string;
  country_code?: string;
  language?: string;
  language_name?: string;
  availability: LocaleAvailability;
}

function websiteHost(value: string): string {
  const raw = value.trim();
  if (!raw) return "";
  try {
    return new URL(raw.includes("://") ? raw : `https://${raw}`).hostname
      .toLowerCase()
      .replace(/^www\./, "");
  } catch {
    return "";
  }
}

/** Configured locales for the audit (primary + prompt_locales). */
export function configuredPromptLocales(ctx: PromptPerformanceContext | null | undefined): PromptLocale[] {
  if (!ctx) return [];
  return normalizePromptLocales(
    ctx.prompt_locales as PromptLocale[] | undefined,
    ctx.primary_market?.country ?? "",
    ctx.primary_market?.country_id ?? "",
  );
}

export function localeHasProbeData(
  ctx: PromptPerformanceContext | null | undefined,
  localeKey: string,
): boolean {
  if (!ctx || !localeKey || localeKey === OVERALL_LOCALE_KEY) return false;
  const entry = ctx.locale_probes?.[localeKey];
  if (entry?.live_probe?.per_prompt?.length) return true;
  // Slim/metrics-only stubs still advertise prompt_count after locale scoping.
  if (Number(entry?.live_probe?.prompt_count ?? 0) > 0) return true;
  if (localeKey === ctx.default_locale_key && ctx.live_probe?.per_prompt?.length) return true;
  if (localeKey === ctx.default_locale_key && Number(ctx.live_probe?.prompt_count ?? 0) > 0) return true;
  // locale_spread is enough to know a market was probed.
  const spread = ctx.locale_spread?.find((row) => row.key === localeKey);
  if (spread && (spread.prompt_count ?? 0) > 0) return true;
  return false;
}

export function buildLocaleViewOptions(
  ctx: PromptPerformanceContext | null | undefined,
  failedKeys: Iterable<string> = [],
): LocaleViewOption[] {
  const locales = configuredPromptLocales(ctx);
  const failed = new Set(Array.from(failedKeys).map(String));
  const options: LocaleViewOption[] = [
    {
      key: OVERALL_LOCALE_KEY,
      label: "Overall",
      availability: "ready",
    },
  ];
  for (const loc of locales) {
    const ready = localeHasProbeData(ctx, loc.key);
    let availability: LocaleAvailability = "ready";
    if (!ready) availability = failed.has(loc.key) ? "failed" : "missing";
    options.push({
      key: loc.key,
      label: loc.label,
      country: loc.country,
      country_code: loc.country_code,
      language: loc.language,
      language_name: loc.language_name,
      availability,
    });
  }
  return options;
}

/** Locales configured but without successful probe data. */
export function localesNeedingProbe(
  ctx: PromptPerformanceContext | null | undefined,
  failedKeys: Iterable<string> = [],
): string[] {
  const failed = new Set(Array.from(failedKeys).map(String));
  return buildLocaleViewOptions(ctx, failed)
    .filter((o) => o.key !== OVERALL_LOCALE_KEY && o.availability !== "ready")
    .map((o) => o.key);
}

function mergeCitedSites(lists: TopCitedSite[][]): TopCitedSite[] {
  const map = new Map<string, TopCitedSite>();
  for (const list of lists) {
    for (const site of list) {
      const domain = String(site.domain || "").replace(/^www\./, "").toLowerCase();
      if (!domain) continue;
      const existing = map.get(domain);
      if (!existing) {
        map.set(domain, {
          ...site,
          domain,
          platforms: [...(site.platforms ?? [])],
          competitor_names: [...(site.competitor_names ?? [])],
        });
        continue;
      }
      existing.count = Number(existing.count ?? 0) + Number(site.count ?? 0);
      existing.platforms = Array.from(new Set([...(existing.platforms ?? []), ...(site.platforms ?? [])]));
      existing.brand_mentioned = Boolean(existing.brand_mentioned || site.brand_mentioned);
      existing.competitor_mentioned = Boolean(existing.competitor_mentioned || site.competitor_mentioned);
      existing.competitor_names = Array.from(new Set([
        ...(existing.competitor_names ?? []),
        ...(site.competitor_names ?? []),
      ]));
    }
  }
  return Array.from(map.values()).sort((a, b) => Number(b.count ?? 0) - Number(a.count ?? 0));
}

function mergeCitedUrls(lists: TopCitedUrl[][]): TopCitedUrl[] {
  const map = new Map<string, TopCitedUrl>();
  for (const list of lists) {
    for (const row of list) {
      const url = String(row.url || "").trim();
      if (!url) continue;
      const existing = map.get(url);
      if (!existing) {
        map.set(url, {
          ...row,
          probe_platforms: [...(row.probe_platforms ?? [])],
          competitor_names: [...(row.competitor_names ?? [])],
        });
        continue;
      }
      existing.frequency = Number(existing.frequency ?? 0) + Number(row.frequency ?? 0);
      existing.probe_platforms = Array.from(new Set([
        ...(existing.probe_platforms ?? []),
        ...(row.probe_platforms ?? []),
      ]));
      existing.brand_mentioned = Boolean(existing.brand_mentioned || row.brand_mentioned);
      existing.competitor_mentioned = Boolean(existing.competitor_mentioned || row.competitor_mentioned);
      existing.competitor_names = Array.from(new Set([
        ...(existing.competitor_names ?? []),
        ...(row.competitor_names ?? []),
      ]));
    }
  }
  return Array.from(map.values()).sort(
    (a, b) => Number(b.frequency ?? 0) - Number(a.frequency ?? 0),
  );
}

/** Collect successful live probes from locale_probes (+ default when needed). */
export function collectAvailableLiveProbes(
  ctx: PromptPerformanceContext | null | undefined,
): Array<{ key: string; live: LiveProbeResult }> {
  if (!ctx) return [];
  const out: Array<{ key: string; live: LiveProbeResult }> = [];
  const seen = new Set<string>();
  const localeProbes = ctx.locale_probes ?? {};
  for (const [key, entry] of Object.entries(localeProbes)) {
    const live = entry?.live_probe;
    if (!live?.per_prompt?.length) continue;
    out.push({ key, live });
    seen.add(key);
  }
  if (ctx.live_probe?.per_prompt?.length) {
    const defaultKey = ctx.default_locale_key || "default";
    if (!seen.has(defaultKey)) {
      out.push({ key: defaultKey, live: ctx.live_probe });
    }
  }
  return out;
}

/** Cache Overall merges per context object — large audits must not re-flatten on every render. */
const overallMergeCache = new WeakMap<object, LiveProbeResult | null>();

/** Merge all successful locale probes into an Overall live_probe view. */
export function mergeOverallLiveProbe(
  ctx: PromptPerformanceContext | null | undefined,
): LiveProbeResult | null {
  if (!ctx) return null;
  const cached = overallMergeCache.get(ctx);
  if (cached !== undefined) return cached;

  const parts = collectAvailableLiveProbes(ctx);
  let result: LiveProbeResult | null;
  if (!parts.length) {
    result = ctx.live_probe ?? null;
  } else if (parts.length === 1) {
    result = parts[0].live;
  } else {
    const per_prompt = parts.flatMap(({ key, live }) =>
      (live.per_prompt ?? []).map((row) => ({
        ...row,
        // Keep rows distinguishable when the same prompt text exists in multiple markets
        _locale_key: key,
      })),
    );

    const brands = new Map<string, { brand_name?: string; website_url?: string }>();
    for (const { live } of parts) {
      for (const b of live.reply_detected_brands ?? []) {
        const name = String(b.brand_name || "").trim();
        if (!name) continue;
        const prev = brands.get(name.toLowerCase());
        if (!prev || (!prev.website_url && b.website_url)) {
          brands.set(name.toLowerCase(), b);
        }
      }
    }

    result = {
      ...parts[0].live,
      per_prompt,
      top_cited_sites: mergeCitedSites(parts.map((p) => p.live.top_cited_sites ?? [])),
      top_cited_urls: mergeCitedUrls(parts.map((p) => p.live.top_cited_urls ?? [])),
      reply_detected_brands: Array.from(brands.values()),
      reply_detected_brand_names: Array.from(brands.values()).map((b) => String(b.brand_name || "")).filter(Boolean),
      disclaimer: "Overall combines successful market/language probe runs.",
    };
  }

  overallMergeCache.set(ctx, result);
  return result;
}

/**
 * Resolve live_probe for a locale view key.
 * - Overall → merged successful locales
 * - Missing locale → null (no silent fallback to default)
 */
export function liveProbeForLocaleView(
  ctx: PromptPerformanceContext | null | undefined,
  localeKey: string,
): LiveProbeResult | null {
  if (!ctx) return null;
  if (!localeKey || localeKey === OVERALL_LOCALE_KEY) {
    return mergeOverallLiveProbe(ctx);
  }
  const entry = ctx.locale_probes?.[localeKey];
  if (entry?.live_probe) return entry.live_probe;
  if (localeKey === ctx.default_locale_key) return ctx.live_probe;
  return null;
}

/** Match citation domains against Competitor visibility website-backed entities. */
export function buildCompetitorCitationMatcher(
  ctx: PromptPerformanceContext | null | undefined,
): (rawDomain: string) => { isCompetitor: boolean; name?: string; website?: string } {
  // Lazy import avoided — keep dependency light via competitors + reply brands,
  // matching the Competitor visibility website-backed set.
  const entities: Array<{ name: string; website: string; hosts: Set<string> }> = [];
  const push = (name: string, website: string) => {
    const host = websiteHost(website);
    if (!host && !name) return;
    const hosts = new Set<string>();
    if (host) {
      hosts.add(host);
      const parts = host.split(".");
      if (parts.length > 2) hosts.add(parts.slice(-2).join("."));
    }
    if (entities.some((e) => (host && e.hosts.has(host)) || (name && e.name.toLowerCase() === name.toLowerCase()))) {
      return;
    }
    entities.push({ name: name || host, website, hosts });
  };

  for (const c of ctx?.competitors ?? []) {
    push(String(c.competitor_brand || "").trim(), String(c.competitor_website || "").trim());
  }
  for (const b of ctx?.live_probe?.reply_detected_brands ?? []) {
    push(String(b.brand_name || "").trim(), String(b.website_url || "").trim());
  }

  return (rawDomain: string) => {
    const domain = rawDomain
      .replace(/^https?:\/\//i, "")
      .split("/")[0]
      .replace(/^www\./, "")
      .toLowerCase();
    if (!domain) return { isCompetitor: false };
    for (const entity of entities) {
      for (const host of entity.hosts) {
        if (domain === host || domain.endsWith(`.${host}`)) {
          return { isCompetitor: true, name: entity.name, website: entity.website };
        }
      }
    }
    return { isCompetitor: false };
  };
}
