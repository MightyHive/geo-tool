import { describe, expect, it } from "vitest";
import type { PromptPerformanceContext, ProductServiceRow } from "../types";
import {
  cleanPersonas,
  computePersonaPerformance,
  mergePersonaPrompts,
  personaTag,
  stripPersonaPrompts,
} from "./personas";
import {
  buildTopicMarketPromptTree,
  filterContextByPersona,
  listProbePersonas,
} from "./promptCategoryGrouping";
import {
  buildSelections,
  countIncludedByBudget,
  NO_PERSONA_GROUP,
  selectionGroupKey,
  selectionsToRows,
} from "./promptSelections";

const rows: ProductServiceRow[] = [
  { product_or_service: "Car insurance", prompts: ["best car cover"] },
  { product_or_service: "Home insurance", prompts: ["cheap home cover"] },
  { product_or_service: "Custom prompts", prompts: ["mine"], is_custom_topic: true },
];

describe("mergePersonaPrompts", () => {
  it("adds persona prompts to their mapped topic with persona + tag", () => {
    const merged = mergePersonaPrompts(rows, [
      {
        name: "Young drivers",
        prompts: [
          { prompt: "First car insurance tips?", product_or_service: "car insurance" },
          { prompt: "Best car cover", product_or_service: "Car insurance" }, // duplicate
          { prompt: "Renting first flat cover?", product_or_service: "Unknown" },
        ],
      },
    ]);
    expect(merged[0].prompts).toEqual(["best car cover", "First car insurance tips?", "Renting first flat cover?"]);
    expect(merged[0].prompt_personas).toEqual({
      "First car insurance tips?": "Young drivers",
      "Renting first flat cover?": "Young drivers",
    });
    expect(merged[0].prompt_tags?.["First car insurance tips?"]).toEqual([personaTag("Young drivers")]);
    expect(merged[2].prompts).toEqual(["mine"]);
  });

  it("replaces earlier persona prompts on regeneration", () => {
    const first = mergePersonaPrompts(rows, [
      { name: "A", prompts: [{ prompt: "old", product_or_service: "Home insurance" }] },
    ]);
    const second = mergePersonaPrompts(first, [
      { name: "B", prompts: [{ prompt: "new", product_or_service: "Home insurance" }] },
    ]);
    expect(second[1].prompts).toEqual(["cheap home cover", "new"]);
    expect(stripPersonaPrompts(second)[1].prompts).toEqual(["cheap home cover"]);
  });
});

describe("cleanPersonas", () => {
  it("trims, drops blanks and duplicate names", () => {
    expect(
      cleanPersonas([
        { name: " Savers ", description: " value " },
        { name: "savers", description: "dupe" },
        { name: "", description: "x" },
      ]),
    ).toEqual([{ name: "Savers", description: "value" }]);
  });
});

describe("prompt selections with personas", () => {
  it("round-trips prompt_personas and groups by persona", () => {
    const merged = mergePersonaPrompts(rows, [
      { name: "Savers", prompts: [{ prompt: "cheapest cover?", product_or_service: "Home insurance" }] },
    ]);
    const selections = buildSelections(merged);
    const persona = selections.find((s) => s.prompt === "cheapest cover?")!;
    expect(persona.persona).toBe("Savers");
    expect(selectionGroupKey(persona, "persona")).toBe("Savers");
    expect(selectionGroupKey(selections[0], "persona")).toBe(NO_PERSONA_GROUP);
    expect(countIncludedByBudget(selections)).toEqual({ topic: 2, persona: 1, custom: 1 });
    const back = selectionsToRows(selections);
    expect(back.find((r) => r.product_or_service === "Home insurance")?.prompt_personas).toEqual({
      "cheapest cover?": "Savers",
    });
  });
});

function makeCtx(): PromptPerformanceContext {
  return {
    brand_name: "Acme",
    brand_site_url: "https://acme.com",
    use_pss: true,
    pss_rows: [
      {
        product_or_service: "Widgets",
        prompts: ["best widget", "widget for kids", "widget for pros"],
        prompt_personas: { "widget for kids": "Parents", "widget for pros": "Pros" },
      },
    ],
    personas: [
      { name: "Pros", description: "Professionals" },
      { name: "Parents", description: "Busy parents" },
    ],
    flat_prompts: [],
    prompt_count: 3,
    competitors: [],
    primary_market: { country: "UK", country_id: "GB" },
    category_labels: ["Widgets"],
    live_probe: {
      brand_match_tokens: ["acme"],
      per_prompt: [
        { prompt: "best widget", gemini_response: "Acme is great." },
        {
          prompt: "widget for kids",
          gemini_response: "Try Acme.",
          citations_gemini: [{ url: "https://acme.com/kids", domain: "acme.com" }],
        },
        { prompt: "widget for pros", gemini_response: "Other brands." },
      ],
    },
  } as unknown as PromptPerformanceContext;
}

describe("persona reporting", () => {
  it("lists personas in wizard order and filters rows", () => {
    const ctx = makeCtx();
    expect(listProbePersonas(ctx)).toEqual(["Pros", "Parents"]);
    expect(filterContextByPersona(ctx, "Parents")?.live_probe?.per_prompt?.map((r) => r.prompt)).toEqual([
      "widget for kids",
    ]);
    expect(filterContextByPersona(ctx, "All personas")).toBe(ctx);
  });

  it("carries persona onto Prompts-page tree entries", () => {
    const tree = buildTopicMarketPromptTree(makeCtx().live_probe, makeCtx(), { localeKey: "" });
    const entries = tree.flatMap((node) => node.markets.flatMap((m) => m.prompts));
    expect(entries.find((e) => e.row.prompt === "widget for kids")?.persona).toBe("Parents");
    expect(entries.find((e) => e.row.prompt === "best widget")?.persona).toBeUndefined();
  });

  it("computes per-persona performance", () => {
    const perf = computePersonaPerformance(makeCtx());
    const parents = perf.find((p) => p.name === "Parents")!;
    expect(parents.description).toBe("Busy parents");
    expect(parents.visibilityPct).toBe(100);
    expect(parents.citationRatePct).toBe(100);
    expect(perf.find((p) => p.name === "Pros")!.visibilityPct).toBe(0);
  });
});
