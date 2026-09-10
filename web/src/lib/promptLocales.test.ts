import { describe, expect, it } from "vitest";
import {
  formatLocaleKeyLabel,
  localeLabel,
  makeLocale,
  normalizePromptLocales,
} from "./promptLocales";

describe("localeLabel / formatLocaleKeyLabel", () => {
  it("formats as Country - Language", () => {
    expect(
      localeLabel({
        country: "Belgium",
        country_code: "BE",
        language: "en",
        language_name: "English",
      }),
    ).toBe("Belgium - English");
  });

  it("makeLocale uses dash label format", () => {
    expect(makeLocale({ country: "Belgium", country_code: "BE", language: "en" }).label).toBe(
      "Belgium - English",
    );
    expect(makeLocale({ country: "Luxembourg", country_code: "LU", language: "fr" }).label).toBe(
      "Luxembourg - French",
    );
  });

  it("resolves common locale keys without configured metadata", () => {
    expect(formatLocaleKeyLabel("BE:en")).toBe("Belgium - English");
    expect(formatLocaleKeyLabel("BE:fr")).toBe("Belgium - French");
    expect(formatLocaleKeyLabel("LU:fr")).toBe("Luxembourg - French");
    expect(formatLocaleKeyLabel("NL:nl")).toBe("Netherlands - Dutch");
  });

  it("prefers configured locale country/language names", () => {
    expect(
      formatLocaleKeyLabel("BE:en", [
        {
          key: "BE:en",
          country: "Belgium",
          country_code: "BE",
          language: "en",
          language_name: "English",
          label: "ignored",
        },
      ]),
    ).toBe("Belgium - English");
  });

  it("returns Overall for overall key", () => {
    expect(formatLocaleKeyLabel("__overall__")).toBe("Overall");
    expect(formatLocaleKeyLabel("")).toBe("Overall");
  });

  it("keeps the configured primary-market language", () => {
    const locales = normalizePromptLocales(
      [makeLocale({ country: "Italy", country_code: "IT", language: "it" })],
      "Italy",
      "IT",
    );
    expect(locales[0].key).toBe("IT:it");
    expect(locales[0].language_name).toBe("Italian");
  });
});
