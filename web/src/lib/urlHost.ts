/** Add https:// when a typed URL has no scheme. */
export function withHttpScheme(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return trimmed;
  return /^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
}

/** Lowercased hostname without a leading www. ("" when unparseable). */
export function siteHostKey(value: string | null | undefined): string {
  const raw = (value || "").trim();
  if (!raw) return "";
  try {
    return new URL(withHttpScheme(raw)).hostname.toLowerCase().replace(/^www\./, "").replace(/\.$/, "");
  } catch {
    return "";
  }
}

/** Same host, or one is a subdomain of the other (mirrors api/page_audits.urls_are_same_site). */
export function hostsAreSameSite(a: string, b: string): boolean {
  if (!a || !b) return false;
  return a === b || a.endsWith(`.${b}`) || b.endsWith(`.${a}`);
}

export function urlIsOnSite(url: string, siteWebsite: string): boolean {
  return hostsAreSameSite(siteHostKey(url), siteHostKey(siteWebsite));
}
