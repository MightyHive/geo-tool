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

export function filterCountries(query: string, limit = 12): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...COUNTRY_NAMES].slice(0, limit);
  return COUNTRY_NAMES.filter((n) => n.toLowerCase().includes(q)).slice(0, limit);
}
