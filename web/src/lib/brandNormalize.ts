/**
 * Brand name normalisation & deduplication.
 *
 * AI responses often produce both a text form ("The Ordinary") and a domain
 * form ("theordinary.com") for the same competitor. This module unifies them.
 */

/** Strips TLDs, "www.", spaces and punctuation to get a comparable stem. */
export function stemBrand(s: string): string {
  return s
    .toLowerCase()
    .replace(/\.(com|co\.uk|co|org|net|io|uk|au|ca|de|fr|es|it)(\.[a-z]{2})?$/i, "")
    .replace(/^www\./, "")
    .replace(/[\s\-_'.]+/g, "");
}

/** Returns true if the string looks like a domain name ("theordinary.com"). */
export function isDomainString(s: string): boolean {
  return /\.[a-z]{2,6}$/i.test(s);
}

const NON_ENTITY_WORDS = new Set([
  "a", "an", "and", "are", "as", "at", "be", "best", "but", "buy", "by",
  "can", "do", "does", "for", "from", "get", "good", "here", "how", "if",
  "in", "is", "it", "its", "known", "more", "most", "of", "on", "or",
  "our", "recommended", "review", "see", "shop", "source", "that", "the",
  "their", "these", "they", "this", "those", "to", "top", "try", "use",
  "using", "view", "was", "we", "website", "what", "when", "where", "which",
  "with", "you", "your",
]);

/** Reject obvious prose fragments accidentally emitted as competitor entities. */
export function isPlausibleCompetitorName(value: string): boolean {
  const name = value.trim();
  if (name.length < 2 || name.length > 80 || !/[a-z]/i.test(name)) return false;
  if (isDomainString(name)) return true;
  const words = name
    .toLowerCase()
    .split(/\s+/)
    .map((word) => word.replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, ""))
    .filter(Boolean);
  if (!words.length) return false;
  return !(words.length === 1 && NON_ENTITY_WORDS.has(words[0]))
    && !words.every((word) => NON_ENTITY_WORDS.has(word));
}

/**
 * Convert a domain-form name to a human-readable brand name.
 * "theordinary.com" → "theordinary"
 * (Used when no text-form alternative exists.)
 */
export function domainToLabel(domain: string): string {
  return domain
    .replace(/^www\./, "")
    .replace(/\.(com|co\.uk|co|org|net|io|uk|au|ca|de|fr|es|it)(\.[a-z]{2})?$/i, "");
}

/**
 * Given a collection of competitor names/domains (potentially duplicated),
 * returns a Map from every original name → canonical (preferred) name.
 *
 * Rules:
 *  1. Names with the same stem are grouped together.
 *  2. Within a group, the non-domain form wins ("The Ordinary" beats
 *     "theordinary.com").
 *  3. Between two text names, the longer wins (more descriptive).
 */
export function buildCanonicalNameMap(names: Iterable<string>): Map<string, string> {
  const nameArray = Array.from(names);
  const stemToCanonical = new Map<string, string>();

  for (const name of nameArray) {
    const s = stemBrand(name);
    if (!stemToCanonical.has(s)) {
      stemToCanonical.set(s, name);
    } else {
      const existing = stemToCanonical.get(s)!;
      const existingIsDomain = isDomainString(existing);
      const nameIsDomain = isDomainString(name);
      if (existingIsDomain && !nameIsDomain) {
        // Always prefer text form
        stemToCanonical.set(s, name);
      } else if (!existingIsDomain && !nameIsDomain && name.length > existing.length) {
        // Between two text names, prefer the longer one
        stemToCanonical.set(s, name);
      }
    }
  }

  return new Map(
    nameArray.map((name) => [name, stemToCanonical.get(stemBrand(name)) ?? name]),
  );
}
