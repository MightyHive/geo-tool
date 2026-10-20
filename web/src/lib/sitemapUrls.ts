import { normalizeHeader, splitCsvLine } from "./customPrompts";
import { siteHostKey, urlIsOnSite, withHttpScheme } from "./urlHost";

const URL_HEADERS = new Set(["url", "urls", "page url", "page", "address", "loc"]);

export type SitemapUrlCheck =
  | { ok: true; url: string }
  | { ok: false; reason: "invalid" | "off-domain" };

/** Normalise one typed/uploaded URL and check it belongs to the brand website. */
export function checkSitemapUrl(raw: string, brandWebsite: string): SitemapUrlCheck {
  const value = withHttpScheme(raw);
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    return { ok: false, reason: "invalid" };
  }
  if (!/^https?:$/.test(parsed.protocol) || !parsed.hostname.includes(".")) {
    return { ok: false, reason: "invalid" };
  }
  if (siteHostKey(brandWebsite) && !urlIsOnSite(parsed.href, brandWebsite)) {
    return { ok: false, reason: "off-domain" };
  }
  return { ok: true, url: value };
}

export interface SitemapUrlsCsvResult {
  /** New URLs not already in `existing`. */
  urls: string[];
  /** Valid URLs that were already in `existing` (callers may re-select them). */
  alreadyListed: string[];
  warnings: string[];
  duplicates: number;
  error?: string;
}

/**
 * Parse a CSV of page URLs (one per row). A header row with a URL column is optional;
 * without one, the first column of every row is read. Invalid or off-domain rows are
 * skipped and reported as warnings.
 */
export function parseSitemapUrlsCsv(
  text: string,
  brandWebsite: string,
  existing: Iterable<string> = [],
): SitemapUrlsCsvResult {
  const raw = (text || "").replace(/^\ufeff/, "").trim();
  if (!raw) return { urls: [], alreadyListed: [], warnings: [], duplicates: 0, error: "CSV file is empty." };

  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0);
  const firstCells = splitCsvLine(lines[0]).map(normalizeHeader);
  const headerIdx = firstCells.findIndex((cell) => URL_HEADERS.has(cell));
  const urlIdx = headerIdx >= 0 ? headerIdx : 0;
  const startRow = headerIdx >= 0 ? 1 : 0;

  const brandHost = siteHostKey(brandWebsite);
  const existingSet = new Set(existing);
  const seen = new Set<string>();
  const urls: string[] = [];
  const alreadyListed: string[] = [];
  const warnings: string[] = [];
  let duplicates = 0;

  for (let i = startRow; i < lines.length; i++) {
    const cell = (splitCsvLine(lines[i])[urlIdx] || "").trim();
    if (!cell) continue;
    const rowLabel = `Row ${i + 1}`;
    const check = checkSitemapUrl(cell, brandWebsite);
    if (!check.ok) {
      warnings.push(
        check.reason === "invalid"
          ? `${rowLabel}: "${cell}" is not a valid URL.`
          : `${rowLabel}: ${cell} does not match the website domain (${brandHost}).`,
      );
      continue;
    }
    if (seen.has(check.url)) {
      duplicates++;
      continue;
    }
    seen.add(check.url);
    if (existingSet.has(check.url)) {
      duplicates++;
      alreadyListed.push(check.url);
    } else {
      urls.push(check.url);
    }
  }

  if (!urls.length && !alreadyListed.length) {
    return {
      urls,
      alreadyListed,
      warnings,
      duplicates,
      error: "No valid URLs found in the CSV.",
    };
  }
  return { urls, alreadyListed, warnings, duplicates };
}

export function sitemapUrlsCsvTemplate(brandWebsite: string): string {
  const host = siteHostKey(brandWebsite) || "example.com";
  return `URL\nhttps://${host}/\nhttps://${host}/example-page\n`;
}
