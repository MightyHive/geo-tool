import { describe, expect, it } from "vitest";
import type { PromptPerformanceContext } from "../types";
import {
  accumulateBrandVisibilityHits,
  brandRawSovPct,
  globalSignalHits,
  rowSignalHits,
} from "./brandVisibilityRows";
import {
  computeVisibilityMetrics,
  computeVisibilityMetricsForPlatforms,
  notableSurfaceScoreDifference,
  visibilityPlatformsWithResults,
} from "./visibilityMetrics";

function makeCtx(overrides: Partial<PromptPerformanceContext> = {}): PromptPerformanceContext {
  return {
    brand_name: "Example",
    brand_site_url: "https://example.com",
    use_pss: false,
    competitors: [
      { competitor_brand: "Rival", competitor_website: "https://rival.com" },
    ],
    primary_market: { country: "UK", country_id: "GB" },
    live_probe: {
      brand_match_tokens: ["example"],
      per_prompt: [
        {
          prompt: "Who is best?",
          gemini_response: "Example and Rival are options.",
          mention_scores_gemini: {
            brand_signal: 2,
            competitor_detail: {
              Rival: 1,
              "noise-category": 9,
              "rival.com": 1,
            },
          },
        },
      ],
    },
    ...overrides,
  } as PromptPerformanceContext;
}

describe("score consistency", () => {
  it("uses the same raw SOV for metrics and the brand visibility table", () => {
    const ctx = makeCtx();
    const metrics = computeVisibilityMetrics(ctx);
    const tableSov = brandRawSovPct(ctx);
    const rows = accumulateBrandVisibilityHits(ctx);
    const own = rows.find((row) => row.isOwnBrand);

    expect(metrics).not.toBeNull();
    expect(tableSov).not.toBeNull();
    expect(own).toBeTruthy();
    expect(metrics!.sovPct).toBeCloseTo(tableSov!, 5);
    // Noise category words without websites are excluded; Rival aliases merge.
    expect(metrics!.sovPct).toBeCloseTo((2 / (2 + 2)) * 100, 5);
  });

  it("uses the same raw SOV for competitors across table and scorecard inputs", () => {
    const ctx = makeCtx();
    const rows = accumulateBrandVisibilityHits(ctx);
    const own = rows.find((row) => row.isOwnBrand);
    const rival = rows.find((row) => !row.isOwnBrand && row.name === "Rival");
    expect(own).toBeTruthy();
    expect(rival).toBeTruthy();

    const totalHits = globalSignalHits(own);
    const rivalSov = (rowSignalHits(rival!) / totalHits) * 100;
    // Rival aliases (Rival + rival.com) merge to 2 hits → 50% SOV.
    expect(rivalSov).toBeCloseTo(50, 5);
    expect(brandRawSovPct(ctx)).toBeCloseTo(50, 5);
  });

  it("keeps relative SOV performance on website-backed competitors only", () => {
    const metrics = computeVisibilityMetrics(makeCtx());
    expect(metrics?.sovPerformanceScore).toBe(50);
    expect(metrics?.competitorCount).toBe(1);
  });

  it("computes non-zero overview + competitors from slim metrics (replies omitted)", () => {
    // Mirrors prompt_performance_metrics.json slim rows: has_response flags +
    // mention_scores, no reply bodies (P0 regression after slim GET work).
    const slimCtx = makeCtx({
      live_probe: {
        brand_match_tokens: ["example"],
        replies_omitted: true,
        per_prompt: [
          {
            prompt: "Who is best?",
            replies_omitted: true,
            has_response_gemini: true,
            has_response_openai: true,
            has_response_claude: true,
            list_metrics: {
              platforms_responded: ["gemini", "openai", "claude"],
              response_count: 3,
              brand_mention_count: 3,
            },
            mention_scores_gemini: {
              brand_signal: 2,
              competitor_detail: { Rival: 1, "rival.com": 1 },
            },
            mention_scores_openai: {
              brand_signal: 1,
              competitor_detail: { Rival: 2 },
            },
            mention_scores_claude: {
              brand_signal: 1,
              competitor_detail: { Rival: 1 },
            },
            runs: {
              gemini: [
                {
                  run_index: 1,
                  has_response: true,
                  citations: [],
                  mention_scores: {
                    brand_signal: 2,
                    competitor_detail: { Rival: 1, "rival.com": 1 },
                  },
                },
              ],
              openai: [
                {
                  run_index: 1,
                  has_response: true,
                  citations: [],
                  mention_scores: {
                    brand_signal: 1,
                    competitor_detail: { Rival: 2 },
                  },
                },
              ],
              claude: [
                {
                  run_index: 1,
                  has_response: true,
                  citations: [],
                  mention_scores: {
                    brand_signal: 1,
                    competitor_detail: { Rival: 1 },
                  },
                },
              ],
            },
          },
        ],
      },
    });

    const metrics = computeVisibilityMetrics(slimCtx);
    expect(metrics).not.toBeNull();
    expect(metrics!.promptCount).toBeGreaterThan(0);
    expect(metrics!.visibilityPct).toBeGreaterThan(0);
    expect(metrics!.score).toBeGreaterThan(0);
    expect(metrics!.competitorCount).toBeGreaterThan(0);
    // Claude must appear whenever slim rows have Claude results (Summary chart).
    expect(metrics!.perPlatform.claude.responseCount).toBeGreaterThan(0);
    expect(visibilityPlatformsWithResults(metrics!.perPlatform)).toEqual(
      expect.arrayContaining(["gemini", "openai", "claude"]),
    );

    const rows = accumulateBrandVisibilityHits(slimCtx);
    const rival = rows.find((row) => !row.isOwnBrand && row.name === "Rival");
    expect(rival).toBeTruthy();
    expect(rowSignalHits(rival!)).toBeGreaterThan(0);
    expect(rival!.promptMentionCount).toBeGreaterThan(0);
  });

  it("includes every platform with results in Summary chart selection (no Claude exclusion)", () => {
    const ctx = makeCtx({
      live_probe: {
        brand_match_tokens: ["example"],
        per_prompt: [
          {
            prompt: "Best tools?",
            runs: {
              gemini: [{ run_index: 1, citations: [], response: "Example is solid.", mention_scores: { brand_signal: 1 } }],
              openai: [{ run_index: 1, citations: [], response: "Try Example.", mention_scores: { brand_signal: 1 } }],
              claude: [{ run_index: 1, citations: [], response: "Example works well.", mention_scores: { brand_signal: 1 } }],
              google_aio: [{ run_index: 1, citations: [], response: "Example leads.", mention_scores: { brand_signal: 1 } }],
            },
          },
        ],
      },
    });
    const metrics = computeVisibilityMetrics(ctx);
    expect(metrics).not.toBeNull();
    expect(visibilityPlatformsWithResults(metrics!.perPlatform)).toEqual([
      "gemini",
      "openai",
      "google_aio",
      "claude",
    ]);
  });

  it("calculates chatbot and AI Overview scores from separate platform groups", () => {
    const ctx = makeCtx({
      live_probe: {
        brand_match_tokens: ["example"],
        per_prompt: [
          {
            prompt: "Best tools?",
            runs: {
              gemini: [{ run_index: 1, citations: [], response: "Example.", mention_scores: { brand_signal: 1 } }],
              openai: [{ run_index: 1, citations: [], response: "Rival.", mention_scores: { competitor_detail: { Rival: 1 } } }],
              google_aio: [{ run_index: 1, citations: [], response: "Example.", mention_scores: { brand_signal: 1 } }],
            },
          },
        ],
      },
    });

    const chatbots = computeVisibilityMetricsForPlatforms(ctx, ["gemini", "openai", "claude"]);
    const overviews = computeVisibilityMetricsForPlatforms(ctx, ["google_aio"]);

    expect(chatbots?.promptCount).toBe(2);
    expect(chatbots?.visiblePromptCount).toBe(1);
    expect(overviews?.promptCount).toBe(1);
    expect(overviews?.visiblePromptCount).toBe(1);
    expect(overviews?.score).toBeGreaterThan(chatbots?.score ?? 0);
  });
});

describe("notableSurfaceScoreDifference", () => {
  it("returns null when either surface lacks data or the gap is small", () => {
    expect(notableSurfaceScoreDifference(40, 50, true, true)).toBeNull();
    expect(notableSurfaceScoreDifference(40, 80, false, true)).toBeNull();
    expect(notableSurfaceScoreDifference(40, 80, true, false)).toBeNull();
    expect(notableSurfaceScoreDifference(null, 80, true, true)).toBeNull();
  });

  it("describes which surface leads when the gap is notable", () => {
    expect(notableSurfaceScoreDifference(42, 71, true, true)).toContain(
      "stronger presence in Google AI Overviews",
    );
    expect(notableSurfaceScoreDifference(71, 42, true, true)).toContain(
      "stronger presence in chatbot answers",
    );
  });
});
