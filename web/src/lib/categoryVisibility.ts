import type {
  LiveProbePerPrompt,
  LiveProbeResult,
  MentionScores,
  PromptPerformancePssRow,
} from "../types";
import type { ProbePlatform } from "./probePlatforms";

export function splitLiveByProduct(
  pssRows: PromptPerformancePssRow[],
  live: LiveProbeResult,
): Record<string, LiveProbePerPrompt[]> {
  const flat: string[] = [];
  const meta: string[] = [];
  for (const r of pssRows) {
    const label = r.product_or_service;
    for (const p of r.prompts) {
      const s = p.trim();
      if (s) {
        flat.push(s);
        meta.push(label);
      }
    }
  }
  const per = (live.per_prompt ?? []).filter(Boolean) as LiveProbePerPrompt[];
  const out: Record<string, LiveProbePerPrompt[]> = {};
  for (let i = 0; i < flat.length; i++) {
    if (i >= per.length) break;
    const prod = meta[i];
    if (!out[prod]) out[prod] = [];
    out[prod].push(per[i]);
  }
  return out;
}

function avgPerCompetitorSovPct(
  perPrompt: LiveProbePerPrompt[],
  platform: ProbePlatform,
): number {
  const compHits: Record<string, number> = {};
  let grandTotal = 0;
  for (const row of perPrompt) {
    const scores: MentionScores | undefined =
      platform === "gemini"
        ? row.mention_scores_gemini
        : platform === "openai"
          ? row.mention_scores_openai
          : row.mention_scores_claude;
    if (!scores) continue;
    const brand = Number(scores.brand_signal ?? 0);
    const compsTotal = Number(scores.competitors_combined_hits ?? 0);
    const total = brand + compsTotal;
    if (total <= 0) continue;
    grandTotal += total;
    for (const [key, hits] of Object.entries(scores.competitor_detail ?? {})) {
      compHits[key] = (compHits[key] ?? 0) + Number(hits);
    }
  }
  const keys = Object.keys(compHits);
  if (!keys.length || grandTotal <= 0) return 0;
  const shares = keys.map((k) => (compHits[k] / grandTotal) * 100);
  return shares.reduce((a, b) => a + b, 0) / shares.length;
}

/** Brand SOV and avg per-competitor SOV for a category's probed rows (mirrors platform SOV cards). */
export function categoryVisibilityMetrics(
  rows: LiveProbePerPrompt[],
  activePlatforms: ProbePlatform[],
): { brandPct: number; compPct: number } {
  if (!rows.length || !activePlatforms.length) {
    return { brandPct: 0, compPct: 0 };
  }

  const brandPcts: number[] = [];
  const compPcts: number[] = [];

  for (const platform of activePlatforms) {
    let brandHits = 0;
    let compHits = 0;
    for (const row of rows) {
      const scores: MentionScores | undefined =
        platform === "gemini"
          ? row.mention_scores_gemini
          : platform === "openai"
            ? row.mention_scores_openai
            : row.mention_scores_claude;
      if (!scores) continue;
      brandHits += Number(scores.brand_signal ?? 0);
      compHits += Number(scores.competitors_combined_hits ?? 0);
    }
    const total = brandHits + compHits;
    if (total > 0) {
      brandPcts.push((100 * brandHits) / total);
    }
    compPcts.push(avgPerCompetitorSovPct(rows, platform));
  }

  return {
    brandPct: brandPcts.length
      ? brandPcts.reduce((a, b) => a + b, 0) / brandPcts.length
      : 0,
    compPct: compPcts.length ? compPcts.reduce((a, b) => a + b, 0) / compPcts.length : 0,
  };
}

export function visibilityByCategory(
  pssRows: PromptPerformancePssRow[],
  live: LiveProbeResult,
  activePlatforms: ProbePlatform[],
): Record<string, { brandPct: number; compPct: number }> {
  const byProd = splitLiveByProduct(pssRows, live);
  const out: Record<string, { brandPct: number; compPct: number }> = {};
  for (const [label, rows] of Object.entries(byProd)) {
    out[label] = categoryVisibilityMetrics(rows, activePlatforms);
  }
  return out;
}
