/** Compact figure with up to 2 significant digits (en-GB). */
export function formatCompactEstimate(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat("en-GB", {
    notation: "compact",
    maximumSignificantDigits: 2,
  })
    .format(n)
    .replace(/([KMB])\b/g, (suffix) => suffix.toLowerCase());
}

/**
 * Display range of −10% to +10% around a central indirect-sessions estimate.
 * Example: 7700 → "6.9k – 8.5k"
 */
export function formatIndirectSessionsRange(central: number): string {
  if (!Number.isFinite(central)) return "—";
  if (central === 0) return formatCompactEstimate(0);
  const a = central * 0.9;
  const b = central * 1.1;
  const low = Math.min(a, b);
  const high = Math.max(a, b);
  return `${formatCompactEstimate(low)} – ${formatCompactEstimate(high)}`;
}

/** Probability of result = (1 − p) × 100, rounded to the nearest percent. */
export function probabilityOfResultPercent(
  pValue: number | null | undefined,
): number | null {
  if (pValue == null || !Number.isFinite(pValue)) return null;
  const clamped = Math.min(1, Math.max(0, pValue));
  return Math.round((1 - clamped) * 100);
}
