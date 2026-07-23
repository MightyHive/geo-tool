import { describe, expect, it } from "vitest";
import {
  normalizeAiVisibilityCriteria,
  normalizeContentQualityCriteria,
  normalizeTechnicalSetupCriteria,
} from "./competitorComparisonCriteria";
import type { CompetitorPillarComponent } from "../types";

function leaf(
  overrides: Partial<CompetitorPillarComponent> & Pick<CompetitorPillarComponent, "key" | "score" | "weight_pct">,
): CompetitorPillarComponent {
  return {
    title: overrides.key,
    finding_summary: `${overrides.key} finding`,
    evidence_example: `${overrides.key} evidence`,
    ...overrides,
  };
}

describe("competitorComparisonCriteria", () => {
  it("keeps AI Visibility as Brand visibility + Share of voice", () => {
    const normalized = normalizeAiVisibilityCriteria([
      leaf({ key: "brand_visibility", score: 50, weight_pct: 60 }),
      leaf({ key: "share_of_voice", score: 40, weight_pct: 40 }),
      leaf({ key: "ai_citability", score: 70, weight_pct: 30 }),
    ]);
    expect(normalized.map((row) => row.title)).toEqual([
      "Brand visibility",
      "Share of voice performance",
    ]);
  });

  it("groups Technical Setup leafs into Overview criteria", () => {
    const normalized = normalizeTechnicalSetupCriteria([
      leaf({ key: "ai_crawler_report", score: 80, weight_pct: 25 }),
      leaf({ key: "ai_citability", score: 40, weight_pct: 25 }),
      leaf({ key: "ai_search_success", score: 60, weight_pct: 12.5 }),
      leaf({ key: "query_coverage_footprint", score: 20, weight_pct: 12.5 }),
      leaf({ key: "platform_readiness", score: 70, weight_pct: 25 }),
    ]);
    expect(normalized.map((row) => row.key)).toEqual([
      "crawler_access",
      "citability",
      "platform_readiness",
    ]);
    expect(normalized.map((row) => row.title)).toEqual([
      "Crawler access",
      "Citability",
      "Platform readiness",
    ]);
  });

  it("groups Content Quality leafs into Overview criteria", () => {
    const normalized = normalizeContentQualityCriteria([
      leaf({ key: "eeat", score: 70, weight_pct: 35 }),
      leaf({ key: "original_information_gain", score: 40, weight_pct: 15 }),
      leaf({ key: "passage_answerability", score: 60, weight_pct: 15 }),
      leaf({ key: "content_formatting", score: 80, weight_pct: 10 }),
      leaf({ key: "schema_entity_markup", score: 50, weight_pct: 15 }),
      leaf({ key: "brand_visibility_authority", score: 30, weight_pct: 10 }),
    ]);
    expect(normalized.map((row) => row.title)).toEqual([
      "E-E-A-T Signals",
      "Content Structure & Answerability",
      "Schema & Entity Markup",
      "Brand Visibility & Authority",
    ]);
  });
});
