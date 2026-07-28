import { describe, expect, it } from "vitest";
import {
  OVERALL_LOCALE_KEY,
  buildCompetitorCitationMatcher,
  liveProbeForLocaleView,
  localesNeedingProbe,
  mergeOverallLiveProbe,
} from "./localeProbeView";
import type { PromptPerformanceContext } from "../types";

function makeCtx(partial: Partial<PromptPerformanceContext> = {}): PromptPerformanceContext {
  return {
    brand_name: "Acme",
    brand_site_url: "https://acme.com",
    use_pss: false,
    pss_rows: [],
    flat_prompts: ["best widget"],
    prompt_count: 1,
    competitors: [
      { competitor_brand: "Rival", competitor_website: "https://rival.com" },
    ],
    primary_market: { country: "Belgium", country_id: "BE" },
    prompt_locales: [
      { country: "Belgium", country_code: "BE", language: "en", language_name: "English", key: "BE:en", label: "Belgium - English" },
      { country: "Belgium", country_code: "BE", language: "nl", language_name: "Dutch", key: "BE:nl", label: "Belgium - Dutch" },
    ],
    default_locale_key: "BE:nl",
    category_labels: [],
    industry: "",
    live_probe: {
      per_prompt: [{ prompt: "best widget" }],
      top_cited_sites: [{ domain: "news.com", count: 2, platforms: ["chatgpt"] }],
    },
    locale_probes: {
      "BE:nl": {
        live_probe: {
          per_prompt: [{ prompt: "best widget nl" }],
          top_cited_sites: [
            { domain: "news.com", count: 2, platforms: ["chatgpt"] },
            { domain: "rival.com", count: 1, platforms: ["chatgpt"] },
          ],
        },
      },
    },
    highlight: { brand: "Acme", competitor_urls: [], competitor_brands: [] },
    ...partial,
  };
}

describe("localeProbeView", () => {
  it("does not silently fall back for missing locales", () => {
    const ctx = makeCtx();
    expect(liveProbeForLocaleView(ctx, "BE:en")).toBeNull();
    expect(liveProbeForLocaleView(ctx, "BE:nl")?.per_prompt?.length).toBe(1);
  });

  it("merges overall across successful locales", () => {
    const ctx = makeCtx();
    const overall = mergeOverallLiveProbe(ctx);
    expect(overall?.top_cited_sites?.find((s) => s.domain === "news.com")?.count).toBe(2);
    expect(localesNeedingProbe(ctx)).toContain("BE:en");
    expect(liveProbeForLocaleView(ctx, OVERALL_LOCALE_KEY)).toBeTruthy();
  });

  it("flags competitor citation domains", () => {
    const match = buildCompetitorCitationMatcher(makeCtx());
    expect(match("www.rival.com").isCompetitor).toBe(true);
    expect(match("news.com").isCompetitor).toBe(false);
  });
});
