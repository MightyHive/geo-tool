import { describe, expect, it } from "vitest";
import type { PromptPerformanceContext } from "../types";
import { GOOD_SCORE_MIN, isOkOrBelow } from "../lib/reportScore";
import {
  buildBrandAuthorityRecommendations,
  buildCitabilityRecommendations,
  buildCrawlerRecommendations,
  buildEeatRecommendations,
  buildLowVisibilityTopicRecommendations,
  buildNegativeSentimentRecommendations,
  buildSampleScriptsRecommendations,
  buildSchemaRecommendations,
  buildStructureRecommendations,
  prepareRecommendations,
  type RecommendationItem,
} from "./RecommendationsSection";

function promptContext(): PromptPerformanceContext {
  return {
    brand_name: "Example",
    brand_site_url: "https://example.com",
    competitors: [],
    pss_rows: [
      { product_or_service: "Weak topic", prompts: ["Weak prompt"] },
      { product_or_service: "Strong topic", prompts: ["Strong prompt"] },
    ],
    probed_pss_rows: [],
    use_pss: true,
    category_labels: [],
    live_probe: {
      brand_match_tokens: ["example"],
      per_prompt: [
        {
          prompt: "Weak prompt",
          runs: {
            gemini: [{
              response: "A generic response with no brand.",
              mention_scores: { brand_signal: 0 },
            }],
          },
        },
        {
          prompt: "Strong prompt",
          runs: {
            gemini: [{
              response: "Example is recommended.",
              mention_scores: { brand_signal: 1 },
            }],
          },
        },
      ],
    },
  } as unknown as PromptPerformanceContext;
}

function item(partial: Partial<RecommendationItem> & Pick<RecommendationItem, "id" | "title">): RecommendationItem {
  return {
    detail: "",
    actions: ["Do the thing."],
    priority: "Medium",
    ...partial,
  };
}

describe("prepareRecommendations", () => {
  it("keeps OK-or-below scores and drops Good/Excellent", () => {
    expect(isOkOrBelow(GOOD_SCORE_MIN - 1)).toBe(true);
    expect(isOkOrBelow(GOOD_SCORE_MIN)).toBe(false);

    const prepared = prepareRecommendations([
      item({ id: "good", title: "Good", score: 75 }),
      item({ id: "ok", title: "OK", score: 74 }),
      item({ id: "excellent", title: "Excellent", score: 92 }),
      item({ id: "poor", title: "Poor", score: 20 }),
      item({ id: "unscored", title: "Unscored" }),
    ]);

    expect(prepared.map((row) => row.id)).toEqual(["poor", "ok", "unscored"]);
  });

  it("sorts lowest score to highest within a category", () => {
    const prepared = prepareRecommendations([
      item({ id: "b", title: "B", score: 60 }),
      item({ id: "a", title: "A", score: 35 }),
      item({ id: "c", title: "C", score: 60 }),
    ]);
    expect(prepared.map((row) => row.id)).toEqual(["a", "b", "c"]);
  });
});

describe("recommendation builders", () => {
  it("recommends content only for topics below the visibility threshold", () => {
    const recommendations = buildLowVisibilityTopicRecommendations(promptContext());

    expect(recommendations).toHaveLength(1);
    expect(recommendations[0].title).toBe("Weak topic");
    expect(recommendations[0].score).toBe(0);
    expect(recommendations[0].actions[0]).toContain("Weak topic");
  });

  it("includes only negative sentiment categories", () => {
    const recommendations = buildNegativeSentimentRecommendations({
      overall_sentiment: "Mixed",
      overall_summary: "",
      by_category: [
        { category: "Service", sentiment: "Negative", summary: "Slow support." },
        { category: "Quality", sentiment: "Positive", summary: "Well regarded." },
      ],
    });

    expect(recommendations.map((row) => row.title)).toEqual(["Service"]);
  });

  it("uses crawler mismatches and Citability needs-work findings", () => {
    const breakdown = {
      crawler_access: {
        improvements: ["Publish a live llms.txt file."],
        rows: [
          {
            crawler: "GPTBot",
            tier: 1,
            recommendation: "ALLOW",
            reason: "Required for retrieval.",
            can_fetch: false,
            aligned: false,
          },
          {
            crawler: "Googlebot",
            tier: 1,
            recommendation: "ALLOW",
            reason: "Already available.",
            can_fetch: true,
            aligned: true,
          },
        ],
      },
      details: {
        technical_setup: {
          score: 50,
          components: [
            {
              key: "ai_search_success",
              title: "AI Search Success",
              score: 55,
              weight_pct: 20,
              detail: "",
              finding_summary: "",
              evidence_example: "",
              criteria: [
                {
                  key: "answers",
                  title: "Direct answers",
                  score: 35,
                  weight_pct: 10,
                  improvements: ["Add direct answer passages."],
                },
                {
                  key: "ok-band",
                  title: "OK band criterion",
                  score: 70,
                  weight_pct: 10,
                  improvements: ["Still worth improving."],
                },
                {
                  key: "sources",
                  title: "Sources",
                  score: 90,
                  weight_pct: 10,
                  improvements: ["This should not appear."],
                },
                {
                  key: "good-band",
                  title: "Good band criterion",
                  score: 75,
                  weight_pct: 10,
                  improvements: ["Good should not appear."],
                },
              ],
            },
          ],
        },
      },
    } as never;

    expect(buildCrawlerRecommendations(breakdown)).toHaveLength(2);
    expect(buildCrawlerRecommendations(breakdown)[0].title).toBe("ALLOW GPTBot");
    expect(buildCrawlerRecommendations(breakdown)[1].actions).toContain("Publish a live llms.txt file.");

    const citability = buildCitabilityRecommendations(breakdown);
    expect(citability.map((row) => row.title)).toEqual(["Direct answers", "OK band criterion"]);

    const sampleScripts = buildSampleScriptsRecommendations(breakdown);
    expect(sampleScripts.map((row) => row.id)).toEqual(
      expect.arrayContaining(["sample-robots", "sample-llms"]),
    );
    expect(sampleScripts.find((row) => row.id === "sample-robots")?.actions[0]).toContain(
      "Sample scripts",
    );
  });

  it("turns each low content-quality area into actionable recommendations", () => {
    const breakdown = {
      content_quality_details: {
        eeat: [
          {
            name: "Experience",
            tagline: "",
            what_it_means: "First-hand evidence is limited.",
            how_scored: "",
            score: 30,
            evidence: [],
            evidence_note: "",
          },
          {
            name: "Trust",
            tagline: "",
            what_it_means: "Already strong.",
            how_scored: "",
            score: 80,
            evidence: [],
            evidence_note: "",
          },
        ],
        structure_answerability: [{
          key: "passage_answerability",
          title: "Passage answerability",
          score: 45,
          description: "Answers are difficult to extract.",
          examples: [],
          empty_message: "",
        }],
        schema_entity: {
          score: 50,
          summary: "Coverage is partial.",
          strengths: [],
          improvements: ["Add Organization schema."],
          evidence: [],
        },
        brand_visibility_authority: {
          score: 25,
          brand_query: "Example",
          method_note: "",
          strengths: [],
          improvements: [],
          rows: [{ platform: "Wikipedia", present: false }],
        },
      },
    } as never;

    expect(buildEeatRecommendations(breakdown)).toHaveLength(1);
    expect(buildEeatRecommendations(breakdown)[0].actions[0]).toContain("first-hand");
    expect(buildStructureRecommendations(breakdown)[0].actions[0]).toContain("concise");
    expect(buildSchemaRecommendations(breakdown)[0].actions).toEqual(["Add Organization schema."]);
    expect(buildBrandAuthorityRecommendations(breakdown)[0].actions[0]).toContain("Wikipedia");
  });

  it("omits schema recommendations when the score is already Good", () => {
    const breakdown = {
      content_quality_details: {
        schema_entity: {
          score: 78,
          summary: "Coverage is strong.",
          strengths: [],
          improvements: ["Optional polish."],
          evidence: [],
        },
      },
    } as never;

    expect(buildSchemaRecommendations(breakdown)).toEqual([]);
  });
});
