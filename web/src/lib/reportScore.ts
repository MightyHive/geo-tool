export type ScoreTone = "green" | "blue" | "yellow" | "red";

const TONE_COLORS: Record<ScoreTone, string> = {
  green: "#00b894",
  blue: "#0984e3",
  yellow: "#fdcb6e",
  red: "#e17055",
};

export function scoreTone(score: number): ScoreTone {
  if (score >= 75) return "green";
  if (score >= 60) return "blue";
  if (score >= 40) return "yellow";
  return "red";
}

export function scoreColor(tone: ScoreTone): string {
  return TONE_COLORS[tone];
}

export function scoreLabel(score: number): string {
  if (score >= 90) return "Excellent";
  if (score >= 75) return "Good";
  if (score >= 60) return "Moderate";
  if (score >= 40) return "Weak";
  return "Poor";
}
