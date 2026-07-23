import { describe, expect, it } from "vitest";
import {
  annotateProbeRowsWithCategory,
  averageVisibilityAcrossPrompts,
  buildGroupedPromptEntries,
  buildTopicMarketPromptTree,
  marketLanguageLabel,
  promptGroupKey,
} from "./promptCategoryGrouping";
import { OVERALL_LOCALE_KEY } from "./localeProbeView";
import type { PromptPerformanceContext } from "../types";

function makeCtx(partial: Partial<PromptPerformanceContext> = {}): PromptPerformanceContext {
  return {
    brand_name: "Acme",
    brand_site_url: "https://acme.com",
    use_pss: true,
    pss_rows: [
      {
        product_or_service: "Widgets",
        prompts: ["best widget brand", "widget comparison"],
      },
    ],
    flat_prompts: ["best widget brand", "widget comparison"],
    prompt_count: 2,
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
      {
        country: "Luxembourg",
        country_code: "LU",
        language: "fr",
        language_name: "French",
        key: "LU:fr",
        label: "Luxembourg - French",
      },
    ],
    default_locale_key: "BE:en",
    category_labels: ["Widgets"],
    industry: "",
    live_probe: null,
    locale_probes: {
      "BE:en": {
        source_prompts: ["best widget brand", "widget comparison"],
        prompts_probed: ["best widget brand", "widget comparison"],
        live_probe: {
          per_prompt: [
            { prompt: "best widget brand", _locale_key: "BE:en" },
            { prompt: "widget comparison", _locale_key: "BE:en" },
          ],
        },
      },
      "LU:fr": {
        source_prompts: ["best widget brand", "widget comparison"],
        prompts_probed: ["meilleure marque widget", "comparaison widget"],
        live_probe: {
          per_prompt: [
            { prompt: "meilleure marque widget", _locale_key: "LU:fr" },
            { prompt: "comparaison widget", _locale_key: "LU:fr" },
          ],
        },
      },
    },
    highlight: { brand: "Acme", competitor_urls: [], competitor_brands: [] },
    ...partial,
  };
}

describe("promptCategoryGrouping", () => {
  it("keeps category for translated locale prompts via source_prompts", () => {
    const ctx = makeCtx();
    const live = {
      per_prompt: [
        { prompt: "meilleure marque widget", _locale_key: "LU:fr" },
        { prompt: "comparaison widget", _locale_key: "LU:fr" },
        { prompt: "best widget brand", _locale_key: "BE:en" },
        { prompt: "widget comparison", _locale_key: "BE:en" },
      ],
    };
    const annotated = annotateProbeRowsWithCategory(live, ctx);
    expect(annotated.map((a) => a.meta.productLabel)).toEqual([
      "Widgets",
      "Widgets",
      "Widgets",
      "Widgets",
    ]);
    expect(annotated.every((a) => a.meta.productLabel !== "Other")).toBe(true);
  });

  it("does not dump extra overall rows into Other via global positional index", () => {
    const ctx = makeCtx();
    const live = {
      per_prompt: [
        { prompt: "best widget brand", _locale_key: "BE:en" },
        { prompt: "widget comparison", _locale_key: "BE:en" },
        // Second locale without source map still uses per-locale positional index
        { prompt: "nl prompt 1", _locale_key: "BE:nl" },
        { prompt: "nl prompt 2", _locale_key: "BE:nl" },
      ],
    };
    const annotated = annotateProbeRowsWithCategory(live, ctx);
    expect(annotated[2].meta.productLabel).toBe("Widgets");
    expect(annotated[3].meta.productLabel).toBe("Widgets");
  });

  it("collapses overall locale variants under one prompt with Market:Language keys", () => {
    const ctx = makeCtx();
    const live = {
      per_prompt: [
        { prompt: "best widget brand", _locale_key: "BE:en", list_metrics: { visibility_pct: 50 } },
        { prompt: "meilleure marque widget", _locale_key: "LU:fr", list_metrics: { visibility_pct: 80 } },
        { prompt: "widget comparison", _locale_key: "BE:en" },
        { prompt: "comparaison widget", _locale_key: "LU:fr" },
      ],
    };
    const grouped = buildGroupedPromptEntries(live, ctx, { localeKey: OVERALL_LOCALE_KEY });
    expect(grouped).toHaveLength(2);
    const first = grouped.find((g) => g.sourcePrompt === "best widget brand");
    expect(first?.variants.map((v) => v.localeKey).sort()).toEqual(["BE:en", "LU:fr"]);
    expect(first?.productLabel).toBe("Widgets");
    expect(first?.selectedLocaleKey).toBe("BE:en");

    const switched = buildGroupedPromptEntries(live, ctx, {
      localeKey: OVERALL_LOCALE_KEY,
      selectedLocales: {
        [promptGroupKey("Widgets", "best widget brand")]: "LU:fr",
      },
    });
    const selected = switched.find((g) => g.sourcePrompt === "best widget brand");
    expect(selected?.selectedLocaleKey).toBe("LU:fr");
    expect(selected?.row.list_metrics?.visibility_pct).toBe(80);
  });

  it("builds Topic → Market:Language → Prompt tree for Overall", () => {
    const ctx = makeCtx();
    const live = {
      per_prompt: [
        { prompt: "best widget brand", _locale_key: "BE:en", list_metrics: { visibility_pct: 50 } },
        { prompt: "meilleure marque widget", _locale_key: "LU:fr", list_metrics: { visibility_pct: 80 } },
        { prompt: "widget comparison", _locale_key: "BE:en" },
        { prompt: "comparaison widget", _locale_key: "LU:fr" },
      ],
    };
    const tree = buildTopicMarketPromptTree(live, ctx, { localeKey: OVERALL_LOCALE_KEY });
    expect(tree).toHaveLength(1);
    expect(tree[0].productLabel).toBe("Widgets");
    expect(tree[0].promptCount).toBe(2);
    expect(tree[0].markets.map((m) => m.localeKey)).toEqual(["BE:en", "LU:fr"]);
    expect(tree[0].markets[0].prompts).toHaveLength(2);
    expect(tree[0].markets[1].prompts).toHaveLength(2);
    expect(tree[0].markets[0].prompts[0].sourcePrompt).toBe("best widget brand");
    expect(tree[0].markets[1].prompts[0].sourcePrompt).toBe("best widget brand");
    expect(tree[0].allRows).toHaveLength(4);
  });

  it("keeps single-locale tree as Topic → one market → prompts", () => {
    const ctx = makeCtx();
    const live = {
      per_prompt: [
        { prompt: "best widget brand", _locale_key: "BE:en" },
        { prompt: "widget comparison", _locale_key: "BE:en" },
      ],
    };
    const tree = buildTopicMarketPromptTree(live, ctx, { localeKey: "BE:en" });
    expect(tree).toHaveLength(1);
    expect(tree[0].markets).toHaveLength(1);
    expect(tree[0].markets[0].localeKey).toBe("BE:en");
    expect(tree[0].markets[0].prompts).toHaveLength(2);
  });

  it("marketLanguageLabel shows Country - Language, not BE:en", () => {
    const ctx = makeCtx();
    expect(marketLanguageLabel("BE:en", ctx.prompt_locales as never)).toBe("Belgium - English");
    expect(marketLanguageLabel("LU:fr")).toBe("Luxembourg - French");
    expect(marketLanguageLabel("BE:en")).not.toBe("BE:en");
  });

  it("averages visibility across prompts under a market/topic group", () => {
    expect(
      averageVisibilityAcrossPrompts([
        { list_metrics: { visibility_pct: 50 } },
        { list_metrics: { visibility_pct: 80 } },
      ]),
    ).toBe(65);
    expect(
      averageVisibilityAcrossPrompts(
        [{ list_metrics: null }, { list_metrics: { visibility_pct: 40 } }],
        [20, null],
      ),
    ).toBe(30);
    expect(averageVisibilityAcrossPrompts([])).toBe(0);
  });
});
