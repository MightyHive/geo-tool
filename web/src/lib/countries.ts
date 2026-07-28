import countries from "i18n-iso-countries";
import en from "i18n-iso-countries/langs/en.json";

countries.registerLocale(en);

const names = Object.values(
  countries.getNames("en", { select: "official" }),
) as string[];

/** Sorted unique country / region names (English). */
export const COUNTRY_NAMES: readonly string[] = Object.freeze(
  [...new Set(names.map((n) => n.trim()).filter(Boolean))].sort((a, b) =>
    a.localeCompare(b),
  ),
);

export function countryCodeForName(name: string): string {
  const code = countries.getAlpha2Code(name.trim(), "en");
  return code ? code.toUpperCase() : "";
}

/** English display name for an ISO 3166-1 alpha-2 code (e.g. BE → Belgium). */
export function countryNameForCode(code: string): string {
  const cc = code.trim().toUpperCase();
  if (!cc) return "";
  return countries.getName(cc, "en", { select: "official" }) || cc;
}

export function filterCountries(query: string, limit = 12): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...COUNTRY_NAMES].slice(0, limit);
  return COUNTRY_NAMES.filter((n) => n.toLowerCase().includes(q)).slice(0, limit);
}
