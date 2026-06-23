import type { LiveProbePerPrompt, PromptPerformanceCompetitor } from "../types";

export type TrackedCompetitor = PromptPerformanceCompetitor;

export interface MentionScores {
  brand_signal?: number;
  competitors_combined_hits?: number;
  competitor_detail?: Record<string, number>;
}

export function normalizeCompetitorUrl(raw: string): string {
  let s = (raw || "").trim();
  if (!s) return "";
  if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//.test(s)) s = `https://${s}`;
  return s;
}

export function matchTokensForTracked(tc: TrackedCompetitor): string[] {
  const tokens = new Set<string>();
  const brand = tc.competitor_brand.trim().toLowerCase();
  if (brand.length >= 2) tokens.add(brand);
  try {
    const u = normalizeCompetitorUrl(tc.competitor_website);
    if (u) {
      const host = new URL(u).hostname.replace(/^www\./, "").toLowerCase();
      if (host.length >= 3) tokens.add(host);
      const base = host.split(".")[0];
      if (base && base.length >= 3) tokens.add(base);
    }
  } catch {
    /* ignore invalid URL */
  }
  return [...tokens];
}

export function detailKeyMatchesTracked(detailKey: string, tc: TrackedCompetitor): boolean {
  const dk = detailKey.toLowerCase().trim();
  if (!dk) return false;
  for (const token of matchTokensForTracked(tc)) {
    if (dk === token) return true;
    if (token.length >= 3 && dk.length >= 3 && (dk.includes(token) || token.includes(dk))) {
      return true;
    }
  }
  return false;
}

/** One row per brand or URL — avoids duplicate chart rows (e.g. Halfords twice). */
export function dedupeTrackedCompetitors(tracked: TrackedCompetitor[]): TrackedCompetitor[] {
  const out: TrackedCompetitor[] = [];
  const seenBrands = new Set<string>();
  const seenUrls = new Set<string>();
  for (const tc of tracked) {
    const brand = tc.competitor_brand.trim().toLowerCase();
    const url = normalizeCompetitorUrl(tc.competitor_website);
    if (brand && seenBrands.has(brand)) continue;
    if (url && seenUrls.has(url)) continue;
    if (brand) seenBrands.add(brand);
    if (url) seenUrls.add(url);
    out.push(tc);
  }
  return out;
}

export function trackedCompetitorKey(tc: TrackedCompetitor): string {
  const url = normalizeCompetitorUrl(tc.competitor_website);
  if (url) return url;
  const brand = tc.competitor_brand.trim().toLowerCase();
  return brand ? `brand:${brand}` : "unknown";
}

export function isTrackedCompetitor(
  brandName: string,
  websiteUrl: string,
  tracked: TrackedCompetitor[],
): boolean {
  const urlNorm = normalizeCompetitorUrl(websiteUrl);
  const brandLc = brandName.trim().toLowerCase();
  return tracked.some((t) => {
    const tUrl = normalizeCompetitorUrl(t.competitor_website);
    if (urlNorm && tUrl && urlNorm === tUrl) return true;
    const tBrand = t.competitor_brand.trim().toLowerCase();
    if (brandLc && tBrand && brandLc === tBrand) return true;
    return false;
  });
}

export function mentionScoresFromRow(
  row: LiveProbePerPrompt,
  platform: "gemini" | "openai",
): MentionScores | undefined {
  return platform === "gemini" ? row.mention_scores_gemini : row.mention_scores_openai;
}

export function trackedHitsInScores(
  scores: MentionScores,
  tc: TrackedCompetitor,
): number {
  let hits = 0;
  for (const [key, count] of Object.entries(scores.competitor_detail ?? {})) {
    if (detailKeyMatchesTracked(key, tc)) hits += Number(count);
  }
  return hits;
}

/** SOV % for one tracked competitor in one reply (null if not mentioned). */
export function trackedSovInReply(
  scores: MentionScores | undefined,
  tc: TrackedCompetitor,
): number | null {
  if (!scores) return null;
  const hits = trackedHitsInScores(scores, tc);
  if (hits <= 0) return null;
  const brand = Number(scores.brand_signal ?? 0);
  const total = brand + Number(scores.competitors_combined_hits ?? 0);
  if (total <= 0) return null;
  return (100 * hits) / total;
}

/** Mean SOV % across tracked competitors that appear in this reply. */
export function avgTrackedCompetitorSov(
  scores: MentionScores | undefined,
  tracked: TrackedCompetitor[],
): number {
  if (!tracked.length || !scores) return 0;
  const pcts = tracked
    .map((tc) => trackedSovInReply(scores, tc))
    .filter((v): v is number => v != null);
  if (!pcts.length) return 0;
  return pcts.reduce((a, b) => a + b, 0) / pcts.length;
}

/** Each competitor's share of mention counts within one reply (platform), tracked only. */
export function perTrackedCompetitorSovMap(
  scores: MentionScores | undefined,
  tracked: TrackedCompetitor[],
): Record<string, number> {
  if (!scores || !tracked.length) return {};
  const brand = Number(scores.brand_signal ?? 0);
  const total = brand + Number(scores.competitors_combined_hits ?? 0);
  if (total <= 0) return {};
  const out: Record<string, number> = {};
  for (const tc of tracked) {
    const hits = trackedHitsInScores(scores, tc);
    if (hits > 0) {
      const label = tc.competitor_brand.trim() || tc.competitor_website;
      out[label] = (100 * hits) / total;
    }
  }
  return out;
}

/** Mean brand mention share % across all prompts on both platforms. */
export function brandAvgSovAcrossProbes(rows: LiveProbePerPrompt[]): number {
  if (!rows.length) return 0;
  const vals = rows.flatMap((r) => [
    brandSharePct(r.mention_scores_gemini),
    brandSharePct(r.mention_scores_openai),
  ]);
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

export function brandSharePct(scores: MentionScores | undefined): number {
  if (!scores) return 0;
  const brand = Number(scores.brand_signal ?? 0);
  const total = brand + Number(scores.competitors_combined_hits ?? 0);
  if (total <= 0) return 0;
  return (100 * brand) / total;
}

export function mergeMentionScores(
  rows: LiveProbePerPrompt[],
  platform: "gemini" | "openai",
): MentionScores {
  let brand = 0;
  let comp = 0;
  const detail: Record<string, number> = {};
  for (const row of rows) {
    const s = mentionScoresFromRow(row, platform);
    if (!s) continue;
    brand += Number(s.brand_signal ?? 0);
    comp += Number(s.competitors_combined_hits ?? 0);
    for (const [k, v] of Object.entries(s.competitor_detail ?? {})) {
      detail[k] = (detail[k] ?? 0) + Number(v);
    }
  }
  return { brand_signal: brand, competitors_combined_hits: comp, competitor_detail: detail };
}

export interface CompetitorSovRow {
  key: string;
  name: string;
  avgSovPct: number;
  appearances: number;
  isBrand?: boolean;
}

/** Average SOV % per tracked competitor across all prompts and both platforms. */
export function competitorSovSummaryTracked(
  rows: LiveProbePerPrompt[],
  tracked: TrackedCompetitor[],
): CompetitorSovRow[] {
  const unique = dedupeTrackedCompetitors(tracked);
  if (!unique.length) return [];
  return unique
    .map((tc) => {
      const vals: number[] = [];
      for (const row of rows) {
        for (const platform of ["gemini", "openai"] as const) {
          const pct = trackedSovInReply(mentionScoresFromRow(row, platform), tc);
          if (pct != null) vals.push(pct);
        }
      }
      return {
        key: trackedCompetitorKey(tc),
        name: tc.competitor_brand.trim() || tc.competitor_website || "—",
        avgSovPct: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0,
        appearances: vals.length,
      };
    })
    .sort((a, b) => b.avgSovPct - a.avgSovPct);
}

export interface PlatformSovRow {
  platform: string;
  brandAvgPct: number;
  competitorAvgPct: number;
}

export function platformSovSummaryTracked(
  rows: LiveProbePerPrompt[],
  tracked: TrackedCompetitor[],
): PlatformSovRow[] {
  if (!rows.length) return [];
  const mean = (arr: number[]) =>
    arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
  return [
    {
      platform: "Gemini",
      brandAvgPct: mean(rows.map((r) => brandSharePct(r.mention_scores_gemini))),
      competitorAvgPct: mean(
        rows.map((r) => avgTrackedCompetitorSov(r.mention_scores_gemini, tracked)),
      ),
    },
    {
      platform: "ChatGPT (OpenAI)",
      brandAvgPct: mean(rows.map((r) => brandSharePct(r.mention_scores_openai))),
      competitorAvgPct: mean(
        rows.map((r) => avgTrackedCompetitorSov(r.mention_scores_openai, tracked)),
      ),
    },
  ];
}

export interface PromptAppearance {
  index: number;
  prompt: string;
  platforms: string[];
}

/** Prompts where a detected / tracked competitor name appears in probe mention data. */
export function promptsForCompetitor(
  brandName: string,
  websiteUrl: string,
  perPrompt: LiveProbePerPrompt[],
): PromptAppearance[] {
  const tc: TrackedCompetitor = {
    competitor_brand: brandName,
    competitor_website: websiteUrl,
  };
  const out: PromptAppearance[] = [];
  for (const row of perPrompt) {
    const platforms: string[] = [];
    if (trackedSovInReply(row.mention_scores_gemini, tc) != null) {
      platforms.push("Gemini");
    }
    if (trackedSovInReply(row.mention_scores_openai, tc) != null) {
      platforms.push("ChatGPT");
    }
    if (platforms.length) {
      out.push({
        index: Number(row.index ?? 0),
        prompt: String(row.prompt ?? "").trim(),
        platforms,
      });
    }
  }
  return out;
}
