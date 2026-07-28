const LOW_SCORE = [216, 199, 255] as const;
const HIGH_SCORE = [92, 38, 158] as const;

function toHex(value: number): string {
  return Math.round(value).toString(16).padStart(2, "0");
}

/** One platform-neutral violet scale: pale for low scores, deep for high scores. */
export function platformScoreColor(score: number): string {
  const ratio = Math.min(100, Math.max(0, score)) / 100;
  const eased = ratio * (2 - ratio);
  const rgb = LOW_SCORE.map((start, index) =>
    start + (HIGH_SCORE[index] - start) * eased,
  );
  return `#${rgb.map(toHex).join("")}`;
}

export const PLATFORM_ACCENT_COLOR = platformScoreColor(65);
