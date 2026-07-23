/** English geographic locator phrases aligned with backend ``geo_locator_phrase_for_market``. */

const ISO2_PHRASE: Record<string, string> = {
  GB: "in the UK",
  US: "in the US",
  IE: "in Ireland",
  AU: "in Australia",
  NZ: "in New Zealand",
  CA: "in Canada",
  DE: "in Germany",
  AT: "in Austria",
  CH: "in Switzerland",
  FR: "in France",
  BE: "in Belgium",
  LU: "in Luxembourg",
  NL: "in the Netherlands",
  ES: "in Spain",
  PT: "in Portugal",
  IT: "in Italy",
  PL: "in Poland",
  SE: "in Sweden",
  NO: "in Norway",
  DK: "in Denmark",
  FI: "in Finland",
  AE: "in the UAE",
  SA: "in Saudi Arabia",
  IN: "in India",
  SG: "in Singapore",
  JP: "in Japan",
  KR: "in South Korea",
  PH: "in the Philippines",
  MY: "in Malaysia",
  TH: "in Thailand",
  ID: "in Indonesia",
  VN: "in Vietnam",
  ZA: "in South Africa",
  BR: "in Brazil",
  MX: "in Mexico",
  AR: "in Argentina",
};

const NAME_PHRASE: Record<string, string> = {
  "united kingdom": "in the UK",
  "great britain": "in the UK",
  ireland: "in Ireland",
  "united states": "in the US",
  "united states of america": "in the US",
  netherlands: "in the Netherlands",
  "the netherlands": "in the Netherlands",
  belgium: "in Belgium",
};

export function geoLocatorPhraseForMarket(country: string, countryCode: string): string {
  const code = (countryCode || "").trim().toUpperCase().slice(0, 2);
  if (code && ISO2_PHRASE[code]) return ISO2_PHRASE[code];
  const name = (country || "").trim();
  if (!name) return "";
  const mapped = NAME_PHRASE[name.toLowerCase()];
  if (mapped) return mapped;
  return `in ${name}`;
}

/** If ``phrase`` is set and missing from ``text``, insert before final punctuation (or append). */
export function ensurePromptContainsGeoLocator(text: string, phrase: string): string {
  const t = (text || "").trim();
  const p = (phrase || "").trim();
  if (!t || !p) return t;
  if (new RegExp(p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i").test(t)) return t;
  const ts = t.replace(/\s+$/, "");
  for (const punct of ["?", "!", "."] as const) {
    if (ts.endsWith(punct)) {
      const core = ts.slice(0, -1).replace(/\s+$/, "");
      return `${core} ${p}${punct}`;
    }
  }
  return `${ts} ${p}`;
}
