import { describe, expect, it } from "vitest";
import {
  TECHNICAL_SETUP_SUB_AREAS,
  groupTechnicalSetupComponents,
  weightedComponentsScore,
  type TechnicalSetupScoreComponent,
} from "./technicalSetupAreas";

function component(
  overrides: Partial<TechnicalSetupScoreComponent> & Pick<TechnicalSetupScoreComponent, "key" | "score" | "weight_pct">,
): TechnicalSetupScoreComponent {
  return {
    title: overrides.key,
    detail: "",
    finding_summary: `${overrides.key} finding`,
    evidence_example: `${overrides.key} evidence`,
    report_section: "technical",
    ...overrides,
  };
}

describe("technicalSetupAreas", () => {
  it("defines three overview criteria with the expected component keys", () => {
    expect(TECHNICAL_SETUP_SUB_AREAS.map((area) => area.label)).toEqual([
      "Crawler access",
      "Citability",
      "Platform readiness",
    ]);
    expect(TECHNICAL_SETUP_SUB_AREAS.map((area) => area.sectionId)).toEqual([
      "crawler-access",
      "citability",
      "platform-readiness",
    ]);
    expect(TECHNICAL_SETUP_SUB_AREAS.map((area) => [...area.componentKeys])).toEqual([
      ["ai_crawler_report"],
      ["ai_citability", "ai_search_success", "query_coverage_footprint"],
      ["platform_readiness"],
    ]);
  });

  it("groups leaf components into overview criteria with matching weighted scores", () => {
    const leaf: TechnicalSetupScoreComponent[] = [
      component({ key: "ai_crawler_report", score: 80, weight_pct: 25 }),
      component({ key: "ai_citability", score: 40, weight_pct: 25 }),
      component({ key: "ai_search_success", score: 60, weight_pct: 12.5 }),
      component({ key: "query_coverage_footprint", score: 20, weight_pct: 12.5 }),
      component({ key: "platform_readiness", score: 70, weight_pct: 25 }),
    ];

    const grouped = groupTechnicalSetupComponents(leaf);
    expect(grouped).toHaveLength(3);
    expect(grouped.map((row) => row.title)).toEqual([
      "Crawler access",
      "Citability",
      "Platform readiness",
    ]);
    expect(grouped.map((row) => row.report_section)).toEqual([
      "crawler-access",
      "citability",
      "platform-readiness",
    ]);
    expect(grouped.map((row) => row.weight_pct)).toEqual([25, 50, 25]);

    const citabilityScore = weightedComponentsScore([
      { score: 40, weight_pct: 25 },
      { score: 60, weight_pct: 12.5 },
      { score: 20, weight_pct: 12.5 },
    ]);
    expect(grouped[1].score).toBe(Math.round((citabilityScore ?? 0) * 10) / 10);

    const leafTotal = leaf.reduce((sum, row) => sum + row.score * row.weight_pct / 100, 0);
    const groupedTotal = grouped.reduce((sum, row) => sum + row.score * row.weight_pct / 100, 0);
    expect(groupedTotal).toBeCloseTo(leafTotal, 5);
  });

  it("overrides legacy report_section values with Technical Setup tab ids", () => {
    const grouped = groupTechnicalSetupComponents([
      component({ key: "platform_readiness", score: 55, weight_pct: 25, report_section: "technical" }),
    ]);
    expect(grouped[0]?.report_section).toBe("platform-readiness");
  });
});
