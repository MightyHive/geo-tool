import { describe, expect, it } from "vitest";
import type { PromptPerformanceContext } from "../types";
import { OVERALL_LOCALE_KEY } from "./localeProbeView";
import { isHugeMultiLocaleAudit, preferredInitialLocaleKey } from "./defaultLocaleView";

function ctx(partial: Partial<PromptPerformanceContext>): PromptPerformanceContext {
  return {
    brand_name: "Acme",
    brand_site_url: "https://acme.example",
    use_pss: false,
    pss_rows: [],
    flat_prompts: [],
    prompt_count: 10,
    competitors: [],
    primary_market: { country: "Belgium", country_id: "BE" },
    prompt_locales: [
      {
        country: "Belgium",
        country_code: "BE",
        language: "en",
        language_name: "English",
        key: "BE:en",
        label: "Belgium - English",
      },
    ],
    default_locale_key: "BE:en",
    ...partial,
  } as PromptPerformanceContext;
}

const threeLocales = [
  {
    country: "Belgium",
    country_code: "BE",
    language: "en",
    language_name: "English",
    key: "BE:en",
    label: "Belgium - English",
  },
  {
    country: "France",
    country_code: "FR",
    language: "fr",
    language_name: "French",
    key: "FR:fr",
    label: "France - French",
  },
  {
    country: "Germany",
    country_code: "DE",
    language: "de",
    language_name: "German",
    key: "DE:de",
    label: "Germany: German",
  },
];

describe("preferredInitialLocaleKey", () => {
  it("defaults to Overall for small single-locale audits", () => {
    expect(preferredInitialLocaleKey(ctx({}))).toBe(OVERALL_LOCALE_KEY);
    expect(isHugeMultiLocaleAudit(ctx({}))).toBe(false);
  });

  it("defaults to Overall for two small locales", () => {
    const two = threeLocales.slice(0, 2);
    expect(
      preferredInitialLocaleKey(ctx({ prompt_locales: two, prompt_count: 10 })),
    ).toBe(OVERALL_LOCALE_KEY);
  });

  it("defaults to primary locale when many locales", () => {
    expect(
      preferredInitialLocaleKey(
        ctx({ prompt_locales: threeLocales, prompt_count: 10, default_locale_key: "BE:en" }),
      ),
    ).toBe("BE:en");
  });

  it("defaults to primary locale when many prompts across 2+ locales", () => {
    expect(
      preferredInitialLocaleKey(
        ctx({
          prompt_locales: threeLocales.slice(0, 2),
          prompt_count: 50,
          default_locale_key: "FR:fr",
        }),
      ),
    ).toBe("FR:fr");
  });
});
