import type { LiveProbePerPrompt, MentionScores } from "../types";
import type { ProbePlatform } from "./probePlatforms";

export function normalizeBrandKey(value: string): string {
  return value.toLowerCase().replace(/[\s\-.'']+/g, "");
}

export function textMentionsBrand(
  text: string,
  brandName: string,
  brandMatchTokens?: string[],
): boolean {
  if (!text?.trim()) return false;
  const brand = brandName.trim();
  if (brand) {
    const words = brand.split(/[\s\-]+/).filter((w) => w.length > 0);
    if (words.length >= 2) {
      const pattern = words
        .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
        .join("[\\s\\-]+");
      if (new RegExp(pattern, "i").test(text)) return true;
    } else if (text.toLowerCase().includes(brand.toLowerCase())) {
      return true;
    }
  }
  const tl = text.toLowerCase();
  for (const token of brandMatchTokens ?? []) {
    const t = token.trim();
    if (t.length >= 2 && tl.includes(t.toLowerCase())) return true;
  }
  return false;
}

export function brandSignalForPlatform(
  row: LiveProbePerPrompt,
  platform: ProbePlatform,
): number {
  const scores = row[`mention_scores_${platform}` as keyof LiveProbePerPrompt] as
    | MentionScores
    | undefined;
  return Number(scores?.brand_signal ?? 0);
}

export function defaultPlatformForRow(
  row: LiveProbePerPrompt,
  platforms: ProbePlatform[],
): ProbePlatform {
  if (!platforms.length) return "gemini";
  let best = platforms[0];
  let bestHits = -1;
  for (const platform of platforms) {
    const hits = brandSignalForPlatform(row, platform);
    if (hits > bestHits) {
      bestHits = hits;
      best = platform;
    }
  }
  return best;
}
