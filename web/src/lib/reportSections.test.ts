import { describe, expect, it } from "vitest";
import {
  DEFAULT_REPORT_SECTIONS,
  REPORT_SECTION_ALIASES,
  resolveReportSectionId,
} from "./reportSections";

describe("reportSections", () => {
  it("uses sidebar-matching kebab-case ids", () => {
    const byLabel = Object.fromEntries(
      DEFAULT_REPORT_SECTIONS.map((section) => [section.label, section.id]),
    );
    expect(byLabel["AI Traffic Dashboard"]).toBe("ai-traffic-dashboard");
    expect(byLabel["Competitor comparison"]).toBe("competitor-comparison");
    expect(byLabel["Prompts"]).toBe("prompts");
    expect(byLabel["Reddit Citations"]).toBe("reddit-citations");
    expect(byLabel["YouTube Citations"]).toBe("youtube-citations");
    expect(byLabel["Crawler access"]).toBe("crawler-access");
    expect(byLabel["Citability"]).toBe("citability");
    expect(byLabel["E-E-A-T Signals"]).toBe("eeat-signals");
    expect(byLabel["Content Structure & Answerability"]).toBe("content-structure-answerability");
    expect(byLabel["Schema & Entity Markup"]).toBe("schema-entity-markup");
    expect(byLabel["Brand Visibility & Authority"]).toBe("brand-visibility-authority");
    expect(byLabel["Sample scripts"]).toBe("sample-scripts");
    expect(byLabel["Content outline generator"]).toBe("content-outline-generator");
  });

  it("keeps group-prefixed Overview ids unique", () => {
    const overviews = DEFAULT_REPORT_SECTIONS.filter((section) => section.label === "Overview");
    expect(overviews.map((section) => section.id)).toEqual([
      "ai-visibility-overview",
      "technical-overview",
      "content-overview",
    ]);
  });

  it("resolves legacy aliases to canonical ids", () => {
    expect(resolveReportSectionId("ga4-traffic")).toBe("ai-traffic-dashboard");
    expect(resolveReportSectionId("competitors")).toBe("competitor-comparison");
    expect(resolveReportSectionId("prompt_performance")).toBe("prompts");
    expect(resolveReportSectionId("competitor-performance")).toBe("competitor-visibility");
    expect(resolveReportSectionId("technical")).toBe("crawler-access");
    expect(resolveReportSectionId("ai-visibility")).toBe("citability");
    expect(resolveReportSectionId("content-eeat")).toBe("eeat-signals");
    expect(resolveReportSectionId("samples")).toBe("sample-scripts");
    expect(REPORT_SECTION_ALIASES["reddit-insights"]).toBe("reddit-citations");
  });
});
