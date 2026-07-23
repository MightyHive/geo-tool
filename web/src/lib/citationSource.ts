import type {
  CitationItem,
  MentionScores,
  PromptPerformanceContext,
  TopCitedSite,
  TopCitedUrl,
} from "../types";
import { stemBrand } from "./brandNormalize";
import { isVendorDomain } from "./vendorDomains";

const MULTI_PART_PUBLIC_SUFFIXES = new Set([
  "co.uk", "org.uk", "gov.uk", "ac.uk",
  "com.au", "net.au", "org.au",
  "co.nz", "co.za", "co.in", "co.jp",
  "com.br", "com.sg", "com.hk", "com.mx",
]);

/** Platforms that store per-prompt citation arrays as `citations_${platform}`. */
export const PROBE_CITATION_PLATFORMS = [
  "gemini",
  "openai",
  "claude",
  "google_aio",
] as const;

export type ProbeCitationPlatform = (typeof PROBE_CITATION_PLATFORMS)[number];

function websiteDomain(website?: string): string {
  if (!website) return "";
  try {
    return new URL(website.startsWith("http") ? website : `https://${website}`)
      .hostname.replace(/^www\./, "").toLowerCase();
  } catch {
    return "";
  }
}

/** Reduce a hostname to its registrable/root domain while preserving common country suffixes. */
export function registrableDomain(rawDomain: string): string {
  const hostname = websiteDomain(rawDomain) || rawDomain
    .replace(/^https?:\/\//i, "")
    .split("/")[0]
    .replace(/^www\./, "")
    .toLowerCase();
  const parts = hostname.split(".").filter(Boolean);
  if (parts.length <= 2) return hostname;
  const suffix = parts.slice(-2).join(".");
  return MULTI_PART_PUBLIC_SUFFIXES.has(suffix)
    ? parts.slice(-3).join(".")
    : parts.slice(-2).join(".");
}

/** True only for a URL that points below the domain homepage. */
export function hasPagePath(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl.startsWith("http") ? rawUrl : `https://${rawUrl}`);
    return url.pathname.replace(/^\/+|\/+$/g, "").length > 0;
  } catch {
    return false;
  }
}

type EntitySets = {
  brandDomains: Set<string>;
  brandStems: Set<string>;
  competitorDomains: Set<string>;
  competitorStems: Set<string>;
};

function buildEntitySets(ctx: PromptPerformanceContext | null | undefined): EntitySets {
  const brandDomains = new Set<string>();
  const brandStems = new Set<string>();
  const competitorDomains = new Set<string>();
  const competitorStems = new Set<string>();

  const add = (
    domains: Set<string>,
    stems: Set<string>,
    name?: string,
    website?: string,
  ) => {
    const domain = websiteDomain(website);
    const nameStem = stemBrand(name ?? "");
    const domainStem = stemBrand(domain);
    if (domain) domains.add(domain);
    if (nameStem) stems.add(nameStem);
    if (domainStem) stems.add(domainStem);
  };

  add(brandDomains, brandStems, ctx?.brand_name, ctx?.brand_site_url);
  for (const competitor of ctx?.competitors ?? []) {
    add(
      competitorDomains,
      competitorStems,
      competitor.competitor_brand,
      competitor.competitor_website,
    );
  }
  for (const competitor of ctx?.live_probe?.reply_detected_brands ?? []) {
    add(
      competitorDomains,
      competitorStems,
      competitor.brand_name,
      competitor.website_url,
    );
  }

  return { brandDomains, brandStems, competitorDomains, competitorStems };
}

function normalizeCitationDomain(rawDomain: string | undefined): string {
  return String(rawDomain ?? "").replace(/^www\./, "").toLowerCase();
}

/**
 * Domains eligible for the Citations list.
 *
 * Excludes the audited brand and retailer/vendor domains. Competitors are
 * allowed when they appear as real cited sources (label separately) — they are
 * never force-injected just for being competitors.
 */
export function buildCitationEligibleDomainPredicate(
  ctx: PromptPerformanceContext | null | undefined,
): (rawDomain: string) => boolean {
  const { brandDomains, brandStems } = buildEntitySets(ctx);
  return (rawDomain: string): boolean => {
    const domain = normalizeCitationDomain(rawDomain);
    return Boolean(domain)
      && !isVendorDomain(domain)
      && !brandDomains.has(domain)
      && !brandStems.has(stemBrand(domain));
  };
}

/**
 * Independent information-source domains (excludes brand, competitors, vendors).
 * Used by AI visibility summaries that want third-party sources only.
 */
export function buildInformationSourceDomainPredicate(
  ctx: PromptPerformanceContext | null | undefined,
): (rawDomain: string) => boolean {
  const {
    brandDomains,
    brandStems,
    competitorDomains,
    competitorStems,
  } = buildEntitySets(ctx);
  return (rawDomain: string): boolean => {
    const domain = normalizeCitationDomain(rawDomain);
    const stem = stemBrand(domain);
    return Boolean(domain)
      && !isVendorDomain(domain)
      && !brandDomains.has(domain)
      && !brandStems.has(stem)
      && !competitorDomains.has(domain)
      && !competitorStems.has(stem);
  };
}

/**
 * Source of truth for Prompts "Citations" and the Citations page.
 *
 * A citation belongs in Citations when it is a real platform-referenced source
 * (from `citations_*`), not a retailer/vendor link, and not the audited brand.
 * `competitor_cited` is brand-context metadata for aggregates/labels — it must
 * not drop competitor websites that were actually cited.
 */
export function isPromptCitationSource(
  citation: Pick<CitationItem, "domain"> | CitationItem | { domain?: string },
  ctx?: PromptPerformanceContext | null,
): boolean {
  const domain = normalizeCitationDomain(citation.domain);
  if (!domain || isVendorDomain(domain)) return false;
  if (!ctx) return true;
  return buildCitationEligibleDomainPredicate(ctx)(domain);
}

/** Vendor / where-to-buy links shown separately from Citations in Prompts. */
export function isPromptVendorCitation(
  citation: Pick<CitationItem, "domain"> | { domain?: string },
): boolean {
  const domain = normalizeCitationDomain(citation.domain);
  return Boolean(domain) && isVendorDomain(domain);
}

export type PromptCitationOccurrence = CitationItem & {
  platform: string;
  brand_mentioned: boolean;
  competitor_mentioned: boolean;
  competitor_names: string[];
};

function mentionSummary(
  row: Record<string, unknown>,
  platform: string,
): { brand: boolean; competitor: boolean; names: string[] } {
  const scores = (row[`mention_scores_${platform}`] ?? {}) as MentionScores;
  const detail = scores.competitor_detail ?? {};
  const names = Object.entries(detail)
    .filter(([, hits]) => Number(hits) > 0)
    .map(([name]) => name);
  return {
    brand: Number(scores.brand_signal ?? 0) > 0,
    competitor: names.length > 0 || Number(scores.competitors_combined_hits ?? 0) > 0,
    names,
  };
}

/**
 * Walk per-prompt probe rows and yield every citation that belongs in the
 * Prompts Citations column / Citations page (same predicate).
 */
export function collectPromptCitationSources(
  perPrompt: Array<Record<string, unknown>> | undefined | null,
  ctx?: PromptPerformanceContext | null,
  platforms: readonly string[] = PROBE_CITATION_PLATFORMS,
): PromptCitationOccurrence[] {
  const out: PromptCitationOccurrence[] = [];
  for (const row of perPrompt ?? []) {
    if (!row || typeof row !== "object") continue;
    for (const platform of platforms) {
      const citations = (row[`citations_${platform}`] ?? []) as CitationItem[];
      if (!Array.isArray(citations)) continue;
      const mentions = mentionSummary(row, platform);
      for (const citation of citations) {
        if (!isPromptCitationSource(citation, ctx)) continue;
        out.push({
          ...citation,
          domain: normalizeCitationDomain(citation.domain),
          platform,
          brand_mentioned: Boolean(citation.brand_cited) || mentions.brand,
          competitor_mentioned: Boolean(citation.competitor_cited) || mentions.competitor,
          competitor_names: [...mentions.names],
        });
      }
    }
  }
  return out;
}

/** Domains that appear as Citations on at least one prompt (for the active probe view). */
export function promptCitationDomainSet(
  perPrompt: Array<Record<string, unknown>> | undefined | null,
  ctx?: PromptPerformanceContext | null,
  platforms?: readonly string[],
): Set<string> {
  const domains = new Set<string>();
  for (const citation of collectPromptCitationSources(perPrompt, ctx, platforms)) {
    const root = registrableDomain(citation.domain);
    if (root) domains.add(root);
  }
  return domains;
}

/** Aggregate Top Domains for the Citations page from prompt-level Citations. */
export function aggregateCitationSitesFromPrompts(
  perPrompt: Array<Record<string, unknown>> | undefined | null,
  ctx?: PromptPerformanceContext | null,
  platforms?: readonly string[],
): TopCitedSite[] {
  const grouped = new Map<string, TopCitedSite>();
  for (const citation of collectPromptCitationSources(perPrompt, ctx, platforms)) {
    const domain = registrableDomain(citation.domain);
    if (!domain) continue;
    const existing = grouped.get(domain);
    if (!existing) {
      grouped.set(domain, {
        domain,
        count: 1,
        platforms: [citation.platform],
        example_url: citation.url || `https://${domain}`,
        title: citation.title,
        thumbnail_url: citation.thumbnail_url,
        views: citation.views,
        platform: citation.platform,
        brand_mentioned: citation.brand_mentioned,
        competitor_mentioned: citation.competitor_mentioned,
        competitor_names: [...citation.competitor_names],
        unresolved_redirect: citation.unresolved_redirect,
      });
      continue;
    }
    existing.count += 1;
    if (!existing.platforms.includes(citation.platform)) {
      existing.platforms.push(citation.platform);
    }
    existing.brand_mentioned = Boolean(existing.brand_mentioned || citation.brand_mentioned);
    existing.competitor_mentioned = Boolean(
      existing.competitor_mentioned || citation.competitor_mentioned,
    );
    existing.competitor_names = Array.from(new Set([
      ...(existing.competitor_names ?? []),
      ...citation.competitor_names,
    ]));
    if (!existing.example_url && citation.url) existing.example_url = citation.url;
    if (!existing.title && citation.title) existing.title = citation.title;
  }
  return Array.from(grouped.values()).sort(
    (left, right) => Number(right.count ?? 0) - Number(left.count ?? 0),
  );
}

/** Aggregate Top URLs for the Citations page from prompt-level Citations. */
export function aggregateCitationUrlsFromPrompts(
  perPrompt: Array<Record<string, unknown>> | undefined | null,
  ctx?: PromptPerformanceContext | null,
  platforms?: readonly string[],
): TopCitedUrl[] {
  const grouped = new Map<string, TopCitedUrl>();
  for (const citation of collectPromptCitationSources(perPrompt, ctx, platforms)) {
    const url = String(citation.url ?? "").trim();
    // Top URLs must be specific page paths — not bare domains / homepages.
    if (!url || !hasPagePath(url)) continue;
    const key = url.toLowerCase().replace(/\/$/, "");
    const domain = normalizeCitationDomain(citation.domain) || registrableDomain(url);
    const existing = grouped.get(key);
    if (!existing) {
      grouped.set(key, {
        url,
        domain,
        title: citation.title,
        thumbnail_url: citation.thumbnail_url,
        views: citation.views,
        platform: citation.platform,
        frequency: 1,
        probe_platforms: [citation.platform],
        brand_mentioned: citation.brand_mentioned,
        competitor_mentioned: citation.competitor_mentioned,
        competitor_names: [...citation.competitor_names],
        unresolved_redirect: citation.unresolved_redirect,
      });
      continue;
    }
    existing.frequency += 1;
    if (!existing.probe_platforms.includes(citation.platform)) {
      existing.probe_platforms.push(citation.platform);
    }
    existing.brand_mentioned = Boolean(existing.brand_mentioned || citation.brand_mentioned);
    existing.competitor_mentioned = Boolean(
      existing.competitor_mentioned || citation.competitor_mentioned,
    );
    existing.competitor_names = Array.from(new Set([
      ...(existing.competitor_names ?? []),
      ...citation.competitor_names,
    ]));
    if (!existing.title && citation.title) existing.title = citation.title;
  }
  return Array.from(grouped.values()).sort(
    (left, right) => Number(right.frequency ?? 0) - Number(left.frequency ?? 0),
  );
}
