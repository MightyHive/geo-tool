import { beforeEach, describe, expect, it, vi } from "vitest";
import type { PromptPerformanceContext } from "../types";
import { OVERALL_LOCALE_KEY } from "./localeProbeView";
import { isHugeMultiLocaleAudit, preferredInitialLocaleKey } from "./defaultLocaleView";

vi.mock("../api/client", () => ({
  fetchPromptPerformanceSummary: vi.fn(),
  fetchPromptPerformanceContext: vi.fn(),
  fetchPromptPerformanceLocale: vi.fn(),
}));

import {
  fetchPromptPerformanceContext,
  fetchPromptPerformanceLocale,
  fetchPromptPerformanceSummary,
} from "../api/client";
import { loadPromptPerformanceBootstrap } from "./promptPerformanceBootstrap";

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
    label: "Germany - German",
  },
];

function summaryHuge() {
  return {
    brand_name: "Samsung",
    prompt_count: 80,
    prompt_locales: threeLocales,
    default_locale_key: "BE:en",
    primary_market: { country: "Belgium", country_id: "BE" },
  };
}

function localeCtx(key: string): PromptPerformanceContext {
  return {
    brand_name: "Samsung",
    brand_site_url: "https://www.samsung.com",
    use_pss: false,
    pss_rows: [],
    flat_prompts: [],
    prompt_count: 80,
    competitors: [],
    primary_market: { country: "Belgium", country_id: "BE" },
    prompt_locales: threeLocales,
    default_locale_key: "BE:en",
    live_probe: {
      per_prompt: [{ prompt: "best phone", citations_openai: [] }],
      prompt_count: 1,
    },
    locale_probes: {
      [key]: {
        live_probe: {
          per_prompt: [{ prompt: "best phone", citations_openai: [] }],
          prompt_count: 1,
        },
      },
    },
  } as unknown as PromptPerformanceContext;
}

describe("loadPromptPerformanceBootstrap", () => {
  beforeEach(() => {
    vi.mocked(fetchPromptPerformanceSummary).mockReset();
    vi.mocked(fetchPromptPerformanceContext).mockReset();
    vi.mocked(fetchPromptPerformanceLocale).mockReset();
  });

  it("loads preferred locale for huge audits (not all-locales)", async () => {
    const summary = summaryHuge();
    expect(isHugeMultiLocaleAudit(summary as PromptPerformanceContext)).toBe(true);
    expect(preferredInitialLocaleKey(summary as PromptPerformanceContext)).toBe("BE:en");

    vi.mocked(fetchPromptPerformanceSummary).mockResolvedValue(summary);
    vi.mocked(fetchPromptPerformanceLocale).mockResolvedValue(localeCtx("BE:en"));

    const result = await loadPromptPerformanceBootstrap("www.samsung.com_test");

    expect(fetchPromptPerformanceLocale).toHaveBeenCalledWith(
      "www.samsung.com_test",
      "BE:en",
    );
    expect(fetchPromptPerformanceContext).not.toHaveBeenCalled();
    expect(result.default_locale_key).toBe("BE:en");
  });

  it("falls back to preferred locale when all-locales times out", async () => {
    vi.mocked(fetchPromptPerformanceContext).mockRejectedValue(new Error("Timed out"));
    vi.mocked(fetchPromptPerformanceSummary).mockResolvedValue(summaryHuge());
    vi.mocked(fetchPromptPerformanceLocale).mockResolvedValue(localeCtx("BE:en"));

    const result = await loadPromptPerformanceBootstrap("www.samsung.com_test", {
      allLocales: true,
    });

    expect(fetchPromptPerformanceLocale).toHaveBeenCalledWith(
      "www.samsung.com_test",
      "BE:en",
    );
    expect(result.live_probe?.per_prompt?.length).toBe(1);
  });

  it("loads explicit Overall via locale endpoint", async () => {
    vi.mocked(fetchPromptPerformanceLocale).mockResolvedValue(localeCtx("BE:en"));
    await loadPromptPerformanceBootstrap("www.samsung.com_test", {
      locale: OVERALL_LOCALE_KEY,
    });
    expect(fetchPromptPerformanceLocale).toHaveBeenCalledWith(
      "www.samsung.com_test",
      OVERALL_LOCALE_KEY,
    );
  });
});
