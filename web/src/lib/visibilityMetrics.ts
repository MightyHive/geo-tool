import type { LiveProbePerPrompt, PromptPerformanceContext } from "../types";
import { textMentionsBrand } from "./brandMatch";
import {
  accumulateBrandVisibilityHits,
  completedPlatformRuns,
  VISIBILITY_PLATFORMS,
  type VisibilityPlatform,
} from "./brandVisibilityRows";

export const PRIMARY_VISIBILITY_PLATFORMS = ["gemini", "openai", "google_aio", "claude"] as const;
export const CHATBOT_PLATFORMS = ["gemini", "openai", "claude"] as const;
export const AI_OVERVIEW_PLATFORMS = ["google_aio"] as const;
export const SOV_COMPETITOR_LIMIT = 10;

export type SurfaceFilter = "all" | "chatbots" | "overviews";

export function platformsForSurface(
  surface: SurfaceFilter,
  available: readonly string[] = PRIMARY_VISIBILITY_PLATFORMS,
): string[] {
  if (surface === "chatbots") {
    return available.filter((platform) =>
      (CHATBOT_PLATFORMS as readonly string[]).includes(platform),
    );
  }
  if (surface === "overviews") {
    return available.filter((platform) =>
      (AI_OVERVIEW_PLATFORMS as readonly string[]).includes(platform),
    );
  }
  return [...available];
}

/**
 * Platforms with completed probe results for Summary / Overview charts.
 * Prefer “has results” (responseCount > 0) over any static allowlist that
 * drops platforms — includes Claude whenever prompts have Claude runs.
 */
export function visibilityPlatformsWithResults(
  perPlatform: Record<string, PlatformVisibilityMetrics>,
  platforms: readonly string[] = PRIMARY_VISIBILITY_PLATFORMS,
): string[] {
  return platforms.filter((platform) => (perPlatform[platform]?.responseCount ?? 0) > 0);
}

export interface PlatformVisibilityMetrics {
  responseCount: number;
  visibleResponseCount: number;
  visibilityPct: number;
  brandHits: number;
  competitorHits: number;
  sovPct: number;
}

export interface VisibilityMetrics {
  score: number;
  /** Total platform responses analysed (prompt × responding platform). */
  promptCount: number;
  /** Platform responses that mention the brand. */
  visiblePromptCount: number;
  visibilityPct: number;
  brandHits: number;
  competitorHits: number;
  /** Raw share of voice — same definition as Brand & competitor visibility table. */
  sovPct: number;
  sovPerformanceScore: number;
  sovRank: number | null;
  competitorCount: number;
  topCompetitorSovPct: number;
  averageCompetitorSovPct: number;
  perPlatform: Record<string, PlatformVisibilityMetrics>;
}

export interface RelativeSovPerformance {
  score: number;
  rank: number | null;
  competitorCount: number;
  topCompetitorSovPct: number;
  averageCompetitorSovPct: number;
}

/**
 * Score brand SOV by its position against the detected competitors, not by
 * treating the raw percentage as progress towards an unrealistic 100% share.
 * A sole leader scores 100; ties split rank credit; an absent brand scores 0.
 */
export function computeRelativeSovPerformance(
  brandHits: number,
  competitorHitsByName: Record<string, number>,
): RelativeSovPerformance {
  const competitorHits = Object.entries(competitorHitsByName)
    .map(([name, value]) => [name, Number(value ?? 0)] as const)
    .filter(([, value]) => value > 0)
    .sort(([nameA, valueA], [nameB, valueB]) => valueB - valueA || nameA.localeCompare(nameB))
    .slice(0, SOV_COMPETITOR_LIMIT)
    .map(([, value]) => value);
  const competitorTotal = competitorHits.reduce((sum, value) => sum + value, 0);
  const totalHits = brandHits + competitorTotal;
  const competitorShares = totalHits > 0
    ? competitorHits.map((hits) => (hits / totalHits) * 100)
    : [];

  if (brandHits <= 0) {
    return {
      score: 0,
      rank: competitorShares.length ? competitorShares.length + 1 : null,
      competitorCount: competitorShares.length,
      topCompetitorSovPct: competitorShares.length ? Math.max(...competitorShares) : 0,
      averageCompetitorSovPct: competitorShares.length
        ? competitorShares.reduce((sum, value) => sum + value, 0) / competitorShares.length
        : 0,
    };
  }
  if (!competitorShares.length) {
    return {
      score: 100,
      rank: 1,
      competitorCount: 0,
      topCompetitorSovPct: 0,
      averageCompetitorSovPct: 0,
    };
  }

  const brandShare = (brandHits / totalHits) * 100;
  const epsilon = 1e-9;
  const below = competitorShares.filter((value) => value < brandShare - epsilon).length;
  const tied = competitorShares.filter((value) => Math.abs(value - brandShare) <= epsilon).length;
  const ahead = competitorShares.filter((value) => value > brandShare + epsilon).length;
  return {
    score: (100 * (below + 0.5 * tied)) / competitorShares.length,
    rank: ahead + 1,
    competitorCount: competitorShares.length,
    topCompetitorSovPct: Math.max(...competitorShares),
    averageCompetitorSovPct:
      competitorShares.reduce((sum, value) => sum + value, 0) / competitorShares.length,
  };
}

/** Canonical visibility and SOV calculation — SOV matches the brand visibility table. */
export function computeVisibilityMetricsForPlatforms(
  ctx: PromptPerformanceContext,
  platforms: readonly VisibilityPlatform[],
): VisibilityMetrics | null {
  const rows = (ctx.live_probe?.per_prompt ?? []) as LiveProbePerPrompt[];
  if (!rows.length) return null;

  const brandName = ctx.brand_name ?? "";
  const brandTokens = ctx.live_probe?.brand_match_tokens ?? [];
  const hitRows = accumulateBrandVisibilityHits(ctx);
  const ownHitRow = hitRows.find((row) => row.isOwnBrand);

  const perPlatform: Record<string, PlatformVisibilityMetrics> = {};

  for (const platform of platforms) {
    let responseCount = 0;
    let visibleResponseCount = 0;
    for (const row of rows) {
      for (const run of completedPlatformRuns(row, platform)) {
        responseCount++;
        const storedBrandSignal = Number(run.scores?.brand_signal ?? 0);
        // When replies are omitted, rely on stored brand_signal (text match unavailable).
        const visible = storedBrandSignal > 0
          || (run.response
            ? textMentionsBrand(run.response, brandName, brandTokens)
            : false);
        if (visible) visibleResponseCount++;
      }
    }
    const brandHits = ownHitRow?.platformHits[platform] ?? 0;
    const competitorHits = hitRows
      .filter((row) => !row.isOwnBrand)
      .reduce((sum, row) => sum + (row.platformHits[platform] ?? 0), 0);
    const totalHits = brandHits + competitorHits;
    perPlatform[platform] = {
      responseCount,
      visibleResponseCount,
      visibilityPct: responseCount > 0 ? (visibleResponseCount / responseCount) * 100 : 0,
      brandHits,
      competitorHits,
      sovPct: totalHits > 0 ? (brandHits / totalHits) * 100 : 0,
    };
  }

  const visiblePromptCount = platforms.reduce(
    (sum, platform) => sum + perPlatform[platform].visibleResponseCount,
    0,
  );
  const promptCount = platforms.reduce(
    (sum, platform) => sum + perPlatform[platform].responseCount,
    0,
  );
  const visibilityPct = promptCount > 0 ? (visiblePromptCount / promptCount) * 100 : 0;
  const brandHits = ownHitRow
    ? platforms.reduce((sum, platform) => sum + ownHitRow.platformHits[platform], 0)
    : 0;
  const competitorHitsByName: Record<string, number> = {};
  for (const row of hitRows) {
    if (row.isOwnBrand) continue;
    const hits = platforms.reduce((sum, platform) => sum + row.platformHits[platform], 0);
    if (hits > 0) competitorHitsByName[row.name] = hits;
  }
  const relativeSov = computeRelativeSovPerformance(brandHits, competitorHitsByName);
  // competitorHits reports the top-10 slice used for relative SOV performance.
  const competitorHits = Object.entries(competitorHitsByName)
    .sort(([nameA, valueA], [nameB, valueB]) => valueB - valueA || nameA.localeCompare(nameB))
    .slice(0, SOV_COMPETITOR_LIMIT)
    .reduce((sum, [, value]) => sum + value, 0);
  // Raw SOV uses the full website-backed denominator (table definition), not top-10 only.
  const totalTableHits = ownHitRow
    ? platforms.reduce((sum, platform) => sum + ownHitRow.globalPlatformTotal[platform], 0)
    : 0;
  const sovPct = totalTableHits > 0 ? (brandHits / totalTableHits) * 100 : 0;

  return {
    score: Math.min(100, 0.60 * visibilityPct + 0.40 * relativeSov.score),
    promptCount,
    visiblePromptCount,
    visibilityPct,
    brandHits,
    competitorHits,
    sovPct,
    sovPerformanceScore: relativeSov.score,
    sovRank: relativeSov.rank,
    competitorCount: relativeSov.competitorCount,
    topCompetitorSovPct: relativeSov.topCompetitorSovPct,
    averageCompetitorSovPct: relativeSov.averageCompetitorSovPct,
    perPlatform,
  };
}

/** Canonical visibility and SOV calculation across all response surfaces. */
export function computeVisibilityMetrics(ctx: PromptPerformanceContext): VisibilityMetrics | null {
  return computeVisibilityMetricsForPlatforms(ctx, VISIBILITY_PLATFORMS);
}

/** Absolute score gap (0–100) treated as a notable Chatbots vs AI Overviews difference. */
export const NOTABLE_SURFACE_SCORE_GAP = 15;

/**
 * Client-side callout when Chatbots and AI Overviews scores diverge notably.
 * Returns null when either surface lacks probe data or the gap is below threshold.
 */
export function notableSurfaceScoreDifference(
  chatbotScore: number | null | undefined,
  overviewScore: number | null | undefined,
  chatbotHasData: boolean,
  overviewHasData: boolean,
  threshold = NOTABLE_SURFACE_SCORE_GAP,
): string | null {
  if (!chatbotHasData || !overviewHasData) return null;
  if (chatbotScore == null || overviewScore == null) return null;
  const gap = Math.abs(chatbotScore - overviewScore);
  if (gap < threshold) return null;
  const chat = Math.round(chatbotScore);
  const overview = Math.round(overviewScore);
  if (overviewScore > chatbotScore) {
    return (
      `Chatbots score ${chat} vs AI Overviews ${overview} — stronger presence in ` +
      `Google AI Overviews than in chatbot answers.`
    );
  }
  return (
    `Chatbots score ${chat} vs AI Overviews ${overview} — stronger presence in ` +
    `chatbot answers than in Google AI Overviews.`
  );
}
