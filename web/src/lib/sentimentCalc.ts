/**
 * Client-side keyword-based sentiment calculation for prompt responses.
 *
 * Analyses each AI response to determine whether the brand is mentioned
 * in a positive or negative context, then aggregates into a sentiment score.
 *
 * Score definition (per user spec):
 *   (responses with positive brand mention) / (responses where brand is mentioned at all)
 */

const POSITIVE_WORDS = [
  "best",
  "top",
  "recommend",
  "recommended",
  "leading",
  "excellent",
  "great",
  "trusted",
  "award",
  "premium",
  "renowned",
  "outstanding",
  "popular",
  "highly rated",
  "highly regarded",
  "well-known",
  "preferred",
  "regarded",
  "favorite",
  "favourite",
  "praised",
  "featured",
  "celebrated",
  "notable",
  "widely used",
  "market leader",
  "well-regarded",
  "stand out",
  "go-to",
  "first choice",
  "gold standard",
  "dermatologist-recommended",
  "widely recommended",
];

const NEGATIVE_WORDS = [
  "worst",
  "avoid",
  "poor",
  "bad",
  "inferior",
  "terrible",
  "disappointing",
  "overpriced",
  "controversial",
  "concern",
  "issue",
  "problem",
  "complaint",
  "unreliable",
  "ineffective",
  "inadequate",
  "subpar",
  "recall",
  "lawsuit",
  "banned",
  "not recommended",
  "side effect",
];

export type SentimentLabel = "positive" | "negative" | "neutral";

/**
 * Classify a single AI response as positive/negative/neutral for the brand.
 * Only meaningful when the brand IS mentioned in the text.
 */
export function computeResponseSentiment(
  text: string,
  brandTokens: string[],
): SentimentLabel {
  if (!text || !brandTokens.length) return "neutral";
  const lower = text.toLowerCase();

  // Collect character positions of all brand token occurrences
  const positions: number[] = [];
  for (const tok of brandTokens) {
    const t = tok.toLowerCase();
    let idx = lower.indexOf(t);
    while (idx >= 0) {
      positions.push(idx);
      idx = lower.indexOf(t, idx + 1);
    }
  }
  if (!positions.length) return "neutral";

  let pos = 0;
  let neg = 0;
  for (const p of positions) {
    // Check ±280 characters around each brand mention
    const window = lower.slice(Math.max(0, p - 280), p + 280);
    for (const w of POSITIVE_WORDS) {
      if (window.includes(w)) pos++;
    }
    for (const w of NEGATIVE_WORDS) {
      if (window.includes(w)) neg++;
    }
  }

  if (pos > neg) return "positive";
  if (neg > pos) return "negative";
  return "neutral";
}

export interface SentimentResult {
  /** Total (prompt × platform) pairs where the brand was mentioned */
  mentionedCount: number;
  /** Of those, how many had positive sentiment */
  positiveCount: number;
  /** Of those, how many had negative sentiment */
  negativeCount: number;
  /** positiveCount / mentionedCount as 0–100, or null if nothing mentioned */
  scorePercent: number | null;
  /** Simple label derived from scorePercent */
  label: SentimentLabel;
}

function platformResponses(row: Record<string, unknown>, platform: string): string[] {
  const runs = (row.runs as Record<string, Array<{ response?: string; error?: string }>> | undefined)?.[platform] ?? [];
  const completed = runs
    .filter((run) => run.response && !run.error)
    .map((run) => String(run.response));
  if (completed.length) return completed;
  const response = String(row[`${platform}_response`] ?? "");
  const error = String(row[`error_${platform}`] ?? "");
  return response && !error ? [response] : [];
}

/**
 * Aggregate sentiment score across all prompts × platforms.
 * Used for the top-level Sentiment scorecard.
 */
export function computeOverallSentiment(
  perPrompt: Array<Record<string, unknown>>,
  brandTokens: string[],
  platforms: string[],
): SentimentResult {
  // Prefer server-precomputed keyword sentiment when reply bodies are omitted.
  const first = perPrompt[0];
  void first;
  let mentioned = 0;
  let positive = 0;
  let negative = 0;
  let usedPrecomputed = false;

  for (const row of perPrompt) {
    const lm = row.list_metrics as
      | { sentiment_votes?: Record<string, number>; brand_mention_count?: number }
      | undefined;
    const repliesOmitted = Boolean(row.replies_omitted);
    if (repliesOmitted && lm?.sentiment_votes) {
      usedPrecomputed = true;
      const votes = lm.sentiment_votes;
      positive += Number(votes.positive || 0);
      negative += Number(votes.negative || 0);
      mentioned += Number(votes.positive || 0) + Number(votes.negative || 0) + Number(votes.neutral || 0);
      continue;
    }
    for (const p of platforms) {
      for (const resp of platformResponses(row, p)) {
        const lower = resp.toLowerCase();
        const brandMentioned = brandTokens.some((tok) => lower.includes(tok.toLowerCase()));
        if (!brandMentioned) continue;
        mentioned++;
        const sent = computeResponseSentiment(resp, brandTokens);
        if (sent === "positive") positive++;
        else if (sent === "negative") negative++;
      }
    }
  }

  void usedPrecomputed;
  const scorePercent = mentioned > 0 ? (positive / mentioned) * 100 : null;
  let label: SentimentLabel = "neutral";
  if (scorePercent != null) {
    if (scorePercent >= 55) label = "positive";
    else if (scorePercent < 30) label = "negative";
  }

  return { mentionedCount: mentioned, positiveCount: positive, negativeCount: negative, scorePercent, label };
}

/** Build SentimentResult from live_probe.keyword_sentiment when present. */
export function sentimentFromKeywordAggregate(
  keyword: {
    mentioned_count?: number;
    positive_count?: number;
    negative_count?: number;
    score_percent?: number | null;
    label?: string;
  } | null | undefined,
): SentimentResult | null {
  if (!keyword) return null;
  const mentioned = Number(keyword.mentioned_count || 0);
  if (!mentioned) return null;
  const positive = Number(keyword.positive_count || 0);
  const negative = Number(keyword.negative_count || 0);
  const scorePercent =
    keyword.score_percent != null ? Number(keyword.score_percent) : (positive / mentioned) * 100;
  let label: SentimentLabel = "neutral";
  const raw = String(keyword.label || "").toLowerCase();
  if (raw === "positive" || raw === "negative" || raw === "neutral") {
    label = raw;
  } else if (scorePercent >= 55) {
    label = "positive";
  } else if (scorePercent < 30) {
    label = "negative";
  }
  return {
    mentionedCount: mentioned,
    positiveCount: positive,
    negativeCount: negative,
    scorePercent,
    label,
  };
}

/**
 * Compute a prompt-level sentiment: what was the overall sentiment of brand
 * mentions across all platforms for this single prompt row?
 * Returns null if the brand was not mentioned.
 */
export function computePromptSentiment(
  row: Record<string, unknown>,
  brandTokens: string[],
  platforms: string[],
): SentimentLabel | null {
  const lm = row.list_metrics as { sentiment?: string; brand_mention_count?: number } | undefined;
  if (row.replies_omitted && lm?.sentiment) {
    if (!lm.brand_mention_count) return null;
    const s = String(lm.sentiment).toLowerCase();
    if (s === "positive" || s === "negative" || s === "neutral") return s;
  }
  const sentiments: SentimentLabel[] = [];
  for (const p of platforms) {
    for (const resp of platformResponses(row, p)) {
      const lower = resp.toLowerCase();
      const brandMentioned = brandTokens.some((tok) => lower.includes(tok.toLowerCase()));
      if (!brandMentioned) continue;
      sentiments.push(computeResponseSentiment(resp, brandTokens));
    }
  }
  if (!sentiments.length) return null;
  const pos = sentiments.filter((s) => s === "positive").length;
  const neg = sentiments.filter((s) => s === "negative").length;
  if (pos > neg) return "positive";
  if (neg > pos) return "negative";
  return "neutral";
}

/**
 * Compute sentiment for a specific platform response within a prompt row.
 * Returns null if platform didn't respond or brand not mentioned.
 */
export function computePlatformResponseSentiment(
  row: Record<string, unknown>,
  brandTokens: string[],
  platform: string,
): SentimentLabel | null {
  const sentiments = platformResponses(row, platform)
    .filter((response) => brandTokens.some((token) => response.toLowerCase().includes(token.toLowerCase())))
    .map((response) => computeResponseSentiment(response, brandTokens));
  if (!sentiments.length) return null;
  const positive = sentiments.filter((sentiment) => sentiment === "positive").length;
  const negative = sentiments.filter((sentiment) => sentiment === "negative").length;
  if (positive > negative) return "positive";
  if (negative > positive) return "negative";
  return "neutral";
}

/**
 * Compute per-brand sentiment score for the Competitor comparison table.
 * Returns null if the brand was never mentioned.
 */
export function computeBrandSentiment(
  perPrompt: Array<Record<string, unknown>>,
  brandTokens: string[],
  platforms: string[],
): SentimentResult {
  return computeOverallSentiment(perPrompt, brandTokens, platforms);
}
