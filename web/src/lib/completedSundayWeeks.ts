/** Sunday-start week helpers matching GA4 / Trends weekly bucketing. */

function parseIsoDateUtc(iso: string): Date {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function toUtcDate(value: Date): Date {
  return new Date(Date.UTC(value.getUTCFullYear(), value.getUTCMonth(), value.getUTCDate()));
}

/** Sunday-start week label for a calendar date (UTC). */
export function sundayWeekStart(isoOrDate: string | Date): string {
  const d = typeof isoOrDate === "string" ? parseIsoDateUtc(isoOrDate) : toUtcDate(isoOrDate);
  // JS: Sunday = 0 … Saturday = 6
  const day = d.getUTCDay();
  d.setUTCDate(d.getUTCDate() - day);
  return d.toISOString().slice(0, 10);
}

/**
 * True when the Sunday-start week has fully ended (covers Sun–Sat).
 * Complete once `asOf` is strictly after that Saturday.
 */
export function isCompletedSundayWeek(
  weekStartIso: string,
  asOf: Date | string = new Date(),
): boolean {
  const start = parseIsoDateUtc(sundayWeekStart(weekStartIso));
  const weekEnd = new Date(start);
  weekEnd.setUTCDate(weekEnd.getUTCDate() + 6);
  const asOfDate =
    typeof asOf === "string" ? parseIsoDateUtc(asOf) : toUtcDate(asOf);
  return asOfDate.getTime() > weekEnd.getTime();
}

/** Drop the current in-progress Sunday week from a weekly chart series. */
export function filterCompletedWeeks<T extends { week: string }>(
  points: T[],
  asOf: Date | string = new Date(),
): T[] {
  return points.filter((point) => isCompletedSundayWeek(point.week, asOf));
}
