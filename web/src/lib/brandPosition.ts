/**
 * brandPosition.ts — Extract the position of a brand in an AI-generated ranked/numbered response.
 *
 * "Avg. Position" = the ordinal rank (1-based) at which the brand first appears in a numbered
 * list within the AI response.  Lower is better (position 1 = top mention).
 *
 * Algorithm:
 * 1. Split the response into lines.
 * 2. Detect numbered or bulleted recommendation lines.
 * 3. Walk lines in response order and return the rank attached to the first brand mention.
 * 4. For bullets, derive rank from the item's order within its list block.
 */

/** Extract brand position (1-based) from a single response string, or null if not found. */
export function extractBrandPosition(
  responseText: string,
  brandTokens: string[],
): number | null {
  if (!responseText || !brandTokens.length) return null;

  const lines = responseText.split(/\r?\n/);
  const tokensLower = brandTokens.map((t) => t.toLowerCase());

  let bulletPosition = 0;
  for (const line of lines) {
    // Match numbered list patterns: "1.", "1)", "**1.", "#1", "## 1."
    const match = line.match(/^\s*(?:#{1,3}\s*)?(?:\*{0,2})(\d+)[.)]\s*/);
    const bullet = line.match(/^\s*[-*•]\s+/);
    if (!match && !bullet) {
      if (!line.trim()) bulletPosition = 0;
      continue;
    }
    const position = match ? parseInt(match[1], 10) : ++bulletPosition;
    if (position < 1 || position > 20) continue; // guard against false positives

    const lineLower = line.toLowerCase();
    if (tokensLower.some((t) => lineLower.includes(t))) {
      return position;
    }
  }
  return null;
}

/**
 * Compute the average position of a brand across all platforms for one prompt row.
 * Returns null if the brand was not found in any numbered list.
 */
export function computePromptAvgPosition(
  row: Record<string, unknown>,
  brandTokens: string[],
  platforms: string[],
): number | null {
  const positions: number[] = [];
  for (const plat of platforms) {
    const resp = String(row[`${plat}_response`] ?? "");
    if (!resp) continue;
    const pos = extractBrandPosition(resp, brandTokens);
    if (pos != null) positions.push(pos);
  }
  if (!positions.length) return null;
  return positions.reduce((a, b) => a + b, 0) / positions.length;
}

/** Format a position value for display, e.g. "2.3" or "#2". */
export function formatPosition(avg: number | null): string {
  if (avg == null) return "—";
  return `#${avg % 1 === 0 ? avg : avg.toFixed(1)}`;
}
