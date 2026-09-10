import { describe, expect, it } from "vitest";
import {
  nicePercentAxisMax,
  promptSovVisibilityPoints,
} from "../components/SovVisibilityScatter";
import type { PromptPerformanceContext } from "../types";

describe("nicePercentAxisMax", () => {
  it("rounds SOV ceilings to readable ticks", () => {
    expect(nicePercentAxisMax(0)).toBe(10);
    expect(nicePercentAxisMax(9)).toBe(10);
    expect(nicePercentAxisMax(9.4)).toBe(10);
    expect(nicePercentAxisMax(11)).toBe(15);
    expect(nicePercentAxisMax(48)).toBe(50);
    expect(nicePercentAxisMax(100)).toBe(100);
  });
});

describe("promptSovVisibilityPoints", () => {
  it("builds one chart point from each saved prompt response", () => {
    const ctx: PromptPerformanceContext = {
      brand_name: "Example",
      brand_site_url: "https://example.com",
      use_pss: false,
      pss_rows: [],
      flat_prompts: ["Which provider is best?", "How does custody work?"],
      prompt_count: 2,
      competitors: [],
      primary_market: { country: "United Kingdom", country_id: "GB" },
      category_labels: ["Digital assets"],
      industry: "Banking",
      highlight: {
        brand: "Example",
        competitor_urls: [],
        competitor_brands: [],
        brand_match_tokens: ["example"],
      },
      live_probe: {
        brand_match_tokens: ["example"],
        per_prompt: [
          {
            prompt_id: "first",
            prompt: "Which provider is best?",
            gemini_response: "Example is one provider.",
          },
          {
            prompt_id: "second",
            prompt: "How does custody work?",
            openai_response: "Custody protects client assets.",
          },
        ],
      },
    };

    const points = promptSovVisibilityPoints(ctx);

    expect(points.map((point) => point.id)).toEqual(["first", "second"]);
    expect(points.map((point) => point.label)).toEqual(ctx.flat_prompts);
    expect(points[0]?.visibility).toBe(100);
    expect(points[1]?.visibility).toBe(0);
  });

  it("returns no points without saved prompt responses", () => {
    expect(promptSovVisibilityPoints(null)).toEqual([]);
  });
});
