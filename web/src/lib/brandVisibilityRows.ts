/**
 * Shared brand / competitor hit accumulation for SOV and visibility tables.
 *
 * Only website-backed competitors count toward SOV so Overview scorecards and
 * the Brand & competitor visibility table use the same denominator.
 */
import type { LiveProbePerPrompt, MentionScores, PromptPerformanceContext } from "../types";
import {
  domainToLabel,
  isDomainString,
  stemBrand,
} from "./brandNormalize";
import { isVendorBrand } from "./vendorDomains";
import { textMentionsBrand } from "./brandMatch";

export const VISIBILITY_PLATFORMS = ["gemini", "openai", "google_aio", "claude"] as const;
export type VisibilityPlatform = (typeof VISIBILITY_PLATFORMS)[number];

export interface CompetitorEntity {
  key: string;
  name: string;
  website: string;
  priority: number;
  aliases: Set<string>;
}

export interface BrandVisibilityHitRow {
  name: string;
  website?: string;
  isOwnBrand: boolean;
  platformHits: Record<VisibilityPlatform, number>;
  /** Shared across rows: brand + all website-backed competitor hits per platform. */
  globalPlatformTotal: Record<VisibilityPlatform, number>;
  promptMentionCount: number;
  totalPrompts: number;
  platformResponseCounts: Record<VisibilityPlatform, number>;
  platformMentionCounts: Record<VisibilityPlatform, number>;
}

export function completedPlatformRuns(
  row: LiveProbePerPrompt,
  platform: VisibilityPlatform,
): { response: string; scores: MentionScores }[] {
  const runs = (row.runs as Record<string, Array<{
    response?: string;
    error?: string;
    has_response?: boolean;
    mention_scores?: MentionScores;
  }>> | undefined)?.[platform] ?? [];
  // Slim metrics omit reply bodies but keep `has_response` + mention_scores.
  const completed = runs
    .filter((run) => {
      if (run.error) return false;
      const hasBody = Boolean(String(run.response || "").trim());
      return hasBody || Boolean(run.has_response);
    })
    .map((run) => ({
      response: String(run.response || ""),
      scores: run.mention_scores ?? {},
    }));
  if (completed.length) return completed;
  const response = String((row as Record<string, unknown>)[`${platform}_response`] ?? "");
  const error = String((row as Record<string, unknown>)[`error_${platform}`] ?? "");
  const scores = (row as Record<string, MentionScores | undefined>)[`mention_scores_${platform}`] ?? {};
  if (response && !error) return [{ response, scores }];
  // Row-level slim flags (no runs[], or runs stripped without has_response).
  const hasFlag = Boolean((row as Record<string, unknown>)[`has_response_${platform}`]);
  const listed = (row.list_metrics?.platforms_responded ?? []).includes(platform);
  if ((hasFlag || listed) && !error) {
    return [{ response: "", scores }];
  }
  return [];
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

function websiteEntityKey(value: string): string {
  const parts = websiteHost(value).split(".").filter(Boolean);
  if (parts.length < 2) return stemBrand(parts[0] ?? "");
  const registrableLabel = (
    parts.length >= 3
    && parts.at(-1)?.length === 2
    && ["co", "com", "net", "org"].includes(parts.at(-2) ?? "")
  )
    ? parts.at(-3)
    : parts.at(-2);
  return stemBrand(registrableLabel ?? "");
}

function emptyPlatformHits(): Record<VisibilityPlatform, number> {
  return { gemini: 0, openai: 0, claude: 0, google_aio: 0 };
}

/** Resolve website-backed competitor entities from onboarding, reply detection, and domain keys. */
export function buildCompetitorEntities(ctx: PromptPerformanceContext): Map<string, CompetitorEntity> {
  const live = ctx.live_probe;
  const perPrompt = (live?.per_prompt ?? []) as LiveProbePerPrompt[];
  const brandWebsite = ctx.brand_site_url || "";
  const rawCompetitorNames = new Set<string>();
  for (const row of perPrompt) {
    for (const platform of VISIBILITY_PLATFORMS) {
      for (const run of completedPlatformRuns(row, platform)) {
        Object.keys(run.scores.competitor_detail ?? {}).forEach((key) => rawCompetitorNames.add(key));
      }
    }
  }

  const ownHostKey = websiteEntityKey(brandWebsite);
  const entities = new Map<string, CompetitorEntity>();

  function addEntity(name: string, website: string, priority: number): void {
    const host = websiteHost(website);
    const key = websiteEntityKey(website);
    const cleanName = name.trim();
    if (!host || !key || key === ownHostKey || isVendorBrand(host) || isVendorBrand(cleanName)) return;
    const existing = entities.get(key);
    const aliases = existing?.aliases ?? new Set<string>();
    [cleanName, host, `www.${host}`, domainToLabel(host)]
      .filter(Boolean)
      .forEach((alias) => aliases.add(stemBrand(alias)));
    if (!existing || priority > existing.priority) {
      entities.set(key, {
        key,
        name: cleanName || domainToLabel(host),
        website: website.includes("://") ? website : `https://${host}`,
        priority,
        aliases,
      });
    } else {
      existing.aliases = aliases;
    }
  }

  for (const competitor of ctx.competitors ?? []) {
    addEntity(competitor.competitor_brand ?? "", competitor.competitor_website ?? "", 3);
  }
  for (const competitor of live?.reply_detected_brands ?? []) {
    if (competitor.website_url) {
      addEntity(competitor.brand_name ?? "", competitor.website_url, 2);
    }
  }
  for (const raw of rawCompetitorNames) {
    if (isDomainString(raw)) addEntity(domainToLabel(raw), raw, 1);
  }

  function entityFor(name: string): CompetitorEntity | undefined {
    const stem = stemBrand(name);
    for (const entity of entities.values()) {
      if (entity.key === stem || entity.aliases.has(stem)) return entity;
    }
    return undefined;
  }

  for (const raw of rawCompetitorNames) {
    if (isDomainString(raw)) continue;
    const entity = entityFor(raw);
    if (entity && entity.priority === 1 && stemBrand(raw) === entity.key) {
      entity.name = raw;
      entity.aliases.add(stemBrand(raw));
    }
  }

  return entities;
}

export function entityForName(
  entities: Map<string, CompetitorEntity>,
  name: string,
): CompetitorEntity | undefined {
  const stem = stemBrand(name);
  for (const entity of entities.values()) {
    if (entity.key === stem || entity.aliases.has(stem)) return entity;
  }
  return undefined;
}

/**
 * Accumulate probe hits using the same website-backed competitor rules as the
 * Brand & competitor visibility table.
 */
export function accumulateBrandVisibilityHits(
  ctx: PromptPerformanceContext,
): BrandVisibilityHitRow[] {
  const live = ctx.live_probe;
  if (!live?.per_prompt?.length) return [];

  const perPrompt = live.per_prompt as LiveProbePerPrompt[];
  const brandTokens = live.brand_match_tokens ?? [];
  const brandName = ctx.brand_name || "Your brand";
  const brandWebsite = ctx.brand_site_url || "";
  const entities = buildCompetitorEntities(ctx);
  const isValidCompetitor = (name: string) => Boolean(entityForName(entities, name));
  const getCanonical = (name: string) => entityForName(entities, name)?.name ?? name;
  const competitorNameSet = new Set(Array.from(entities.values(), (entity) => entity.name));
  const compWebsiteMap = new Map<string, string>();
  for (const entity of entities.values()) {
    compWebsiteMap.set(entity.name.toLowerCase(), entity.website);
  }

  const totalPrompts = perPrompt.reduce(
    (count, row) => count + VISIBILITY_PLATFORMS.reduce(
      (rowCount, platform) => rowCount + completedPlatformRuns(row, platform).length,
      0,
    ),
    0,
  );

  const brandHits: Map<string, Record<VisibilityPlatform, number>> = new Map();
  const globalPlatformTotal = emptyPlatformHits();

  function getOrInit(map: Map<string, Record<VisibilityPlatform, number>>, key: string) {
    if (!map.has(key)) map.set(key, emptyPlatformHits());
    return map.get(key)!;
  }

  for (const row of perPrompt) {
    for (const platform of VISIBILITY_PLATFORMS) {
      for (const run of completedPlatformRuns(row, platform)) {
        const storedBrandSignal = Number(run.scores.brand_signal ?? 0);
        const textVisible = textMentionsBrand(run.response ?? "", brandName, brandTokens);
        const brandSig = storedBrandSignal > 0 ? storedBrandSignal : textVisible ? 1 : 0;
        const compDetail = run.scores.competitor_detail ?? {};
        const filteredCompTotal = Object.entries(compDetail)
          .filter(([name]) => isValidCompetitor(name))
          .reduce((sum, [, value]) => sum + Number(value), 0);
        globalPlatformTotal[platform] += brandSig + filteredCompTotal;
        getOrInit(brandHits, brandName)[platform] += brandSig;
        for (const [cName, hits] of Object.entries(compDetail)) {
          if (isValidCompetitor(cName)) {
            getOrInit(brandHits, getCanonical(cName))[platform] += Number(hits);
          }
        }
      }
    }
  }

  const promptMentionCounts: Map<string, number> = new Map();
  const platformResponseCounts: Record<VisibilityPlatform, number> = emptyPlatformHits();
  const platformMentionCounts: Map<string, Record<VisibilityPlatform, number>> = new Map();
  const getMentionCounts = (name: string) => {
    if (!platformMentionCounts.has(name)) platformMentionCounts.set(name, emptyPlatformHits());
    return platformMentionCounts.get(name)!;
  };
  for (const row of perPrompt) {
    for (const platform of VISIBILITY_PLATFORMS) {
      for (const run of completedPlatformRuns(row, platform)) {
        platformResponseCounts[platform] += 1;
        const brandMentioned = Number(run.scores.brand_signal ?? 0) > 0
          || textMentionsBrand(run.response ?? "", brandName, brandTokens);
        if (brandMentioned) {
          promptMentionCounts.set(brandName, (promptMentionCounts.get(brandName) ?? 0) + 1);
          getMentionCounts(brandName)[platform] += 1;
        }
        const competitorsMentionedThisResponse = new Set<string>();
        for (const [cName, hits] of Object.entries(run.scores.competitor_detail ?? {})) {
          if (Number(hits) > 0 && isValidCompetitor(cName)) {
            competitorsMentionedThisResponse.add(getCanonical(cName));
          }
        }
        for (const cName of competitorsMentionedThisResponse) {
          promptMentionCounts.set(cName, (promptMentionCounts.get(cName) ?? 0) + 1);
          getMentionCounts(cName)[platform] += 1;
        }
      }
    }
  }

  const makeRow = (name: string, isOwn: boolean): BrandVisibilityHitRow => ({
    name,
    website: isOwn ? brandWebsite : compWebsiteMap.get(name.toLowerCase()),
    isOwnBrand: isOwn,
    platformHits: getOrInit(brandHits, name),
    globalPlatformTotal,
    promptMentionCount: promptMentionCounts.get(name) ?? 0,
    totalPrompts,
    platformResponseCounts,
    platformMentionCounts: platformMentionCounts.get(name) ?? emptyPlatformHits(),
  });

  const rows: BrandVisibilityHitRow[] = [makeRow(brandName, true)];
  const compNames = Array.from(competitorNameSet).sort((a, b) => {
    const aHits = Object.values(getOrInit(brandHits, a)).reduce((s, v) => s + v, 0);
    const bHits = Object.values(getOrInit(brandHits, b)).reduce((s, v) => s + v, 0);
    return bHits - aHits;
  });
  for (const name of compNames) rows.push(makeRow(name, false));
  return rows;
}

export function rowSignalHits(row: BrandVisibilityHitRow): number {
  return Object.values(row.platformHits).reduce((sum, value) => sum + value, 0);
}

export function globalSignalHits(row: BrandVisibilityHitRow | undefined): number {
  if (!row) return 0;
  return Object.values(row.globalPlatformTotal).reduce((sum, hits) => sum + hits, 0);
}

/** Brand raw SOV % — same definition as the Brand & competitor visibility table. */
export function brandRawSovPct(ctx: PromptPerformanceContext): number | null {
  const rows = accumulateBrandVisibilityHits(ctx);
  if (!rows.length) return null;
  const own = rows.find((row) => row.isOwnBrand) ?? rows[0];
  const total = globalSignalHits(own);
  if (total <= 0) return 0;
  return (rowSignalHits(own) / total) * 100;
}
