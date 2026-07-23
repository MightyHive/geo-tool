export type ScoreTone = "green" | "blue" | "yellow" | "red";

/** Minimum score for Good (and Excellent). Matches scoreLabel / scoreTone. */
export const GOOD_SCORE_MIN = 75;

const TONE_COLORS: Record<ScoreTone, string> = {
  green: "#00b894",
  blue: "#0984e3",
  yellow: "#fdcb6e",
  red: "#e17055",
};

/**
 * Round a report score for display / banding.
 * Uses Math.round (half away from zero for non-negative scores): 60.0 → 60, 60.9 → 61.
 * Banding (scoreTone / scoreLabel / isOkOrBelow) uses this integer so e.g. 74.6 → 75 “Good”.
 */
export function roundReportScore(score: number): number {
  return Math.round(score);
}

function bandScore(score: number): number {
  return roundReportScore(score);
}

export function scoreTone(score: number): ScoreTone {
  const s = bandScore(score);
  if (s >= GOOD_SCORE_MIN) return "green";
  if (s >= 60) return "blue";
  if (s >= 40) return "yellow";
  return "red";
}

export function scoreColor(tone: ScoreTone): string {
  return TONE_COLORS[tone];
}

export function scoreLabel(score: number): string {
  const s = bandScore(score);
  if (s >= 90) return "Excellent";
  if (s >= GOOD_SCORE_MIN) return "Good";
  if (s >= 60) return "OK";
  if (s >= 40) return "Weak";
  return "Poor";
}

/** True when rounded display score is OK, Weak, or Poor — i.e. not Good/Excellent. */
export function isOkOrBelow(score: number): boolean {
  return Number.isFinite(score) && bandScore(score) < GOOD_SCORE_MIN;
}

/** Nearest-integer display for GEO / pillar / criterion scores (60.0 → "60", 60.9 → "61"). */
export function formatReportScore(score: number): string {
  return Number.isFinite(score) ? String(roundReportScore(score)) : "—";
}
