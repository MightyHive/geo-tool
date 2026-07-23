/** Market + language pairs for multi-locale prompt probes. */

import { countryNameForCode } from "./countries";

export interface PromptLocale {
  country: string;
  country_code: string;
  language: string;
  language_name: string;
  key: string;
  label: string;
}

export const SUPPORTED_LANGUAGES: ReadonlyArray<{ code: string; name: string }> = [
  { code: "en", name: "English" },
  { code: "fr", name: "French" },
  { code: "de", name: "German" },
  { code: "es", name: "Spanish" },
  { code: "it", name: "Italian" },
  { code: "nl", name: "Dutch" },
  { code: "pt", name: "Portuguese" },
  { code: "pl", name: "Polish" },
  { code: "sv", name: "Swedish" },
  { code: "da", name: "Danish" },
  { code: "no", name: "Norwegian" },
  { code: "fi", name: "Finnish" },
  { code: "cs", name: "Czech" },
  { code: "hu", name: "Hungarian" },
  { code: "ro", name: "Romanian" },
  { code: "el", name: "Greek" },
  { code: "tr", name: "Turkish" },
  { code: "ar", name: "Arabic" },
  { code: "he", name: "Hebrew" },
  { code: "ja", name: "Japanese" },
  { code: "ko", name: "Korean" },
  { code: "zh", name: "Chinese" },
];

const COUNTRY_DEFAULT_LANGUAGE: Record<string, string> = {
  GB: "en",
  US: "en",
  IE: "en",
  AU: "en",
  NZ: "en",
  CA: "en",
  FR: "fr",
  BE: "fr",
  CH: "de",
  DE: "de",
  AT: "de",
  ES: "es",
  MX: "es",
  AR: "es",
  CL: "es",
  CO: "es",
  IT: "it",
  NL: "nl",
  PT: "pt",
  BR: "pt",
  PL: "pl",
  SE: "sv",
  DK: "da",
  NO: "no",
  FI: "fi",
  CZ: "cs",
  HU: "hu",
  RO: "ro",
  GR: "el",
  TR: "tr",
  SA: "ar",
  AE: "ar",
  IL: "he",
  JP: "ja",
  KR: "ko",
  CN: "zh",
  TW: "zh",
  HK: "zh",
};

export const MAX_PROMPT_LOCALES = 12;

export function languageName(code: string): string {
  const c = code.trim().toLowerCase();
  return SUPPORTED_LANGUAGES.find((l) => l.code === c)?.name ?? (c || "English");
}

export function defaultLanguageForCountry(countryCode: string): { code: string; name: string } {
  const code = (COUNTRY_DEFAULT_LANGUAGE[countryCode.trim().toUpperCase()] ?? "en").toLowerCase();
  return { code, name: languageName(code) };
}

export function localeKey(countryCode: string, language: string): string {
  const cc = countryCode.trim().toUpperCase() || "XX";
  const lang = language.trim().toLowerCase() || "en";
  return `${cc}:${lang}`;
}

/** Display format: "Belgium - English". */
export function localeLabel(locale: Pick<PromptLocale, "country" | "country_code" | "language_name" | "language">): string {
  const country =
    locale.country?.trim()
    || countryNameForCode(locale.country_code || "")
    || locale.country_code?.trim()
    || "Market";
  const lang = locale.language_name?.trim() || languageName(locale.language || "en");
  return `${country} - ${lang}`;
}

/**
 * Human-readable Market:Language from a locale key (`BE:en` → `Belgium - English`).
 * Prefers configured locale metadata when provided.
 */
export function formatLocaleKeyLabel(
  key: string,
  locales?: Array<Pick<PromptLocale, "key" | "country" | "country_code" | "language" | "language_name" | "label">> | null,
): string {
  const trimmed = key.trim();
  if (!trimmed || trimmed === "__overall__") return "Overall";

  const fromList = locales?.find((loc) => loc.key === trimmed);
  if (fromList) {
    if (fromList.country || fromList.language_name || fromList.language) {
      return localeLabel(fromList);
    }
    if (fromList.label?.trim()) return fromList.label.trim();
  }

  const sep = trimmed.indexOf(":");
  if (sep <= 0) return trimmed;
  const countryCode = trimmed.slice(0, sep);
  const language = trimmed.slice(sep + 1);
  return localeLabel({
    country: countryNameForCode(countryCode),
    country_code: countryCode,
    language,
    language_name: languageName(language),
  });
}

export function makeLocale(input: {
  country: string;
  country_code: string;
  language?: string;
  language_name?: string;
}): PromptLocale {
  const language = (input.language || "en").trim().toLowerCase() || "en";
  const language_name = input.language_name?.trim() || languageName(language);
  const country = input.country.trim();
  const country_code = input.country_code.trim().toUpperCase();
  const key = localeKey(country_code, language);
  const locale = { country, country_code, language, language_name, key, label: "" };
  locale.label = localeLabel(locale);
  return locale;
}

/** Always starts with primary market + English. */
export function normalizePromptLocales(
  raw: PromptLocale[] | undefined | null,
  marketCountry: string,
  marketCountryCode: string,
): PromptLocale[] {
  const primary = makeLocale({
    country: marketCountry,
    country_code: marketCountryCode,
    language: "en",
    language_name: "English",
  });
  const out: PromptLocale[] = [primary];
  const seen = new Set([primary.key]);
  for (const item of raw ?? []) {
    if (!item?.country && !item?.country_code) continue;
    const loc = makeLocale({
      country: item.country || "",
      country_code: item.country_code || "",
      language: item.language || "en",
      language_name: item.language_name,
    });
    if (seen.has(loc.key)) continue;
    seen.add(loc.key);
    out.push(loc);
    if (out.length >= MAX_PROMPT_LOCALES) break;
  }
  return out;
}
