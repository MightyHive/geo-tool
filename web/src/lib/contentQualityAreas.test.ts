import { describe, expect, it } from "vitest";
import {
  CONTENT_QUALITY_SUB_AREAS,
  groupContentQualityComponents,
  weightedComponentsScore,
  type ContentQualityScoreComponent,
} from "./contentQualityAreas";

function component(
  overrides: Partial<ContentQualityScoreComponent> & Pick<ContentQualityScoreComponent, "key" | "score" | "weight_pct">,
): ContentQualityScoreComponent {
  return {
    title: overrides.key,
    detail: "",
    finding_summary: `${overrides.key} finding`,
    evidence_example: `${overrides.key} evidence`,
    report_section: "content",
    ...overrides,
  };
}

describe("contentQualityAreas", () => {
  it("defines four overview criteria with the expected component keys", () => {
    expect(CONTENT_QUALITY_SUB_AREAS.map((area) => area.label)).toEqual([
      "E-E-A-T Signals",
      "Content Structure & Answerability",
      "Schema & Entity Markup",
      "Brand Visibility & Authority",
    ]);
    expect(CONTENT_QUALITY_SUB_AREAS.map((area) => area.sectionId)).toEqual([
      "eeat-signals",
      "content-structure-answerability",
      "schema-entity-markup",
      "brand-visibility-authority",
    ]);
  });

  it("groups leaf components into overview criteria with matching weighted scores", () => {
    const leaf: ContentQualityScoreComponent[] = [
      component({ key: "eeat", score: 70, weight_pct: 35 }),
      component({ key: "original_information_gain", score: 40, weight_pct: 15 }),
      component({ key: "passage_answerability", score: 60, weight_pct: 15 }),
      component({ key: "content_formatting", score: 80, weight_pct: 10 }),
      component({ key: "schema_entity_markup", score: 50, weight_pct: 15 }),
      component({ key: "brand_visibility_authority", score: 30, weight_pct: 10 }),
    ];

    const grouped = groupContentQualityComponents(leaf);
    expect(grouped).toHaveLength(4);
    expect(grouped.map((row) => row.title)).toEqual([
      "E-E-A-T Signals",
      "Content Structure & Answerability",
      "Schema & Entity Markup",
      "Brand Visibility & Authority",
    ]);
    expect(grouped.map((row) => row.report_section)).toEqual([
      "eeat-signals",
      "content-structure-answerability",
      "schema-entity-markup",
      "brand-visibility-authority",
    ]);
    expect(grouped.map((row) => row.weight_pct)).toEqual([35, 40, 15, 10]);

    const structureScore = weightedComponentsScore([
      { score: 40, weight_pct: 15 },
      { score: 60, weight_pct: 15 },
      { score: 80, weight_pct: 10 },
    ]);
    expect(grouped[1].score).toBe(Math.round((structureScore ?? 0) * 10) / 10);

    const leafTotal = leaf.reduce((sum, row) => sum + row.score * row.weight_pct / 100, 0);
    const groupedTotal = grouped.reduce((sum, row) => sum + row.score * row.weight_pct / 100, 0);
    expect(groupedTotal).toBeCloseTo(leafTotal, 5);
  });

  it("overrides legacy report_section values with Content Quality tab ids", () => {
    const grouped = groupContentQualityComponents([
      component({ key: "schema_entity_markup", score: 55, weight_pct: 15, report_section: "content" }),
    ]);
    expect(grouped[0]?.report_section).toBe("schema-entity-markup");
  });
});
