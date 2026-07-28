/**
 * Helpers for Gemini qualitative prompt sentiment (Positive / Mixed / Neutral / Negative).
 * Used by AI Visibility → Prompts chips/columns — not keyword heuristics.
 */

import type { LiveProbePerPrompt, PerPromptSentiment, PromptSentimentAnalysis } from "../types";

export type GeminiSentimentLabel = "Positive" | "Mixed" | "Neutral" | "Negative";

const CANON: Record<string, GeminiSentimentLabel> = {
  positive: "Positive",
  mixed: "Mixed",
  neutral: "Neutral",
  negative: "Negative",
};

export function normalizeGeminiSentimentLabel(
  raw: string | null | undefined,
): GeminiSentimentLabel | null {
  if (!raw) return null;
  const key = String(raw).trim().toLowerCase();
  return CANON[key] ?? null;
}

/** Map prompt_id → Gemini label for O(1) table lookups. */
export function buildGeminiPromptSentimentMap(
  sentiment: PromptSentimentAnalysis | null | undefined,
): Map<string, GeminiSentimentLabel> {
  const map = new Map<string, GeminiSentimentLabel>();
  for (const row of sentiment?.by_prompt ?? []) {
    const label = normalizeGeminiSentimentLabel(row.sentiment);
    const id = String(row.prompt_id || "").trim();
    if (id && label) map.set(id, label);
  }
  return map;
}

export function buildGeminiPromptSentimentSummaryMap(
  sentiment: PromptSentimentAnalysis | null | undefined,
): Map<string, PerPromptSentiment> {
  const map = new Map<string, PerPromptSentiment>();
  for (const row of sentiment?.by_prompt ?? []) {
    const id = String(row.prompt_id || "").trim();
    if (id) map.set(id, row);
  }
  return map;
}

/**
 * Resolve Gemini qualitative sentiment for a probe row.
 * Prefer prompt_id; fall back to matching by_prompt rows is already id-keyed.
 */
export function geminiSentimentForPrompt(
  row: LiveProbePerPrompt | Record<string, unknown>,
  byPromptId: Map<string, GeminiSentimentLabel>,
): GeminiSentimentLabel | null {
  const pid = String((row as LiveProbePerPrompt).prompt_id || "").trim();
  if (pid && byPromptId.has(pid)) return byPromptId.get(pid) ?? null;
  return null;
}

/** Dominant Gemini label across a set of prompt rows (for topic headers). */
export function dominantGeminiSentiment(
  labels: Array<GeminiSentimentLabel | null | undefined>,
): GeminiSentimentLabel | null {
  const counts: Record<GeminiSentimentLabel, number> = {
    Positive: 0,
    Mixed: 0,
    Neutral: 0,
    Negative: 0,
  };
  let any = false;
  for (const label of labels) {
    if (!label) continue;
    counts[label] += 1;
    any = true;
  }
  if (!any) return null;
  // Prefer stronger polarity when tied with Neutral/Mixed.
  const order: GeminiSentimentLabel[] = ["Positive", "Negative", "Mixed", "Neutral"];
  let best: GeminiSentimentLabel = "Neutral";
  let bestCount = -1;
  for (const label of order) {
    const n = counts[label];
    if (n > bestCount) {
      best = label;
      bestCount = n;
    }
  }
  return bestCount > 0 ? best : null;
}
