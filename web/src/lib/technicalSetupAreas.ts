/**
 * Shared Technical Setup sub-areas used by Technical Overview and the
 * Summary "how is this score calculated?" overlay so names, weights,
 * scores, and report-section links stay aligned.
 */

export interface TechnicalSetupScoreComponent {
  key: string;
  title: string;
  score: number;
  weight_pct: number;
  detail: string;
  finding_summary: string;
  evidence_example: string;
  strengths?: string[];
  improvements?: string[];
  report_section?: string;
  site_examples?: Array<{
    url: string;
    title: string;
    excerpt: string;
    context: string;
  }>;
}

export const TECHNICAL_SETUP_SUB_AREAS = [
  {
    key: "crawler_access",
    label: "Crawler access",
    description:
      "Whether major AI crawlers are permitted to access the site through robots.txt and related controls.",
    tooltip:
      "Checks access for major AI search, training and retrieval crawlers. Detailed crawler-by-crawler results are shown on the Crawler access page.",
    sectionId: "crawler-access",
    componentKeys: ["ai_crawler_report"] as const,
  },
  {
    key: "citability",
    label: "Citability",
    description:
      "How successfully the site supports AI extraction and citation across tested queries and content.",
    tooltip:
      "Combines AI citability, AI search success, and query coverage and citation footprint. Detailed findings are consolidated on the Citability page.",
    sectionId: "citability",
    componentKeys: [
      "ai_citability",
      "ai_search_success",
      "query_coverage_footprint",
    ] as const,
  },
  {
    key: "platform_readiness",
    label: "Platform readiness",
    description:
      "Technical and prompt-performance readiness for each supported AI platform.",
    tooltip:
      "Combines platform-specific technical readiness with observed prompt visibility where tests are available.",
    sectionId: "platform-readiness",
    componentKeys: ["platform_readiness"] as const,
  },
] as const;

export type TechnicalSetupSubArea = (typeof TECHNICAL_SETUP_SUB_AREAS)[number];

/** Weighted average of component scores (same formula as Technical Overview). */
export function weightedComponentsScore(
  components: Array<{ score: number; weight_pct: number }>,
): number | null {
  if (!components.length) return null;
  const totalWeight = components.reduce((sum, component) => sum + component.weight_pct, 0);
  if (totalWeight <= 0) {
    return components.reduce((sum, component) => sum + component.score, 0) / components.length;
  }
  return (
    components.reduce(
      (sum, component) => sum + component.score * component.weight_pct,
      0,
    ) / totalWeight
  );
}

function conciseFinding(text: string, maxLength = 160): string {
  const cleaned = text.replace(/\s+/g, " ").trim().replace(/[.;:]$/, "");
  return cleaned.length <= maxLength ? cleaned : `${cleaned.slice(0, maxLength - 1).trimEnd()}…`;
}

function areaFindingSummary(parts: TechnicalSetupScoreComponent[]): string {
  if (parts.length === 1) return parts[0].finding_summary;
  const ranked = [...parts].sort((left, right) => left.score - right.score);
  const weakest = ranked[0];
  const strongest = ranked[ranked.length - 1];
  const strength = strongest.strengths?.[0]
    || (strongest.score >= 60 ? strongest.finding_summary : "");
  const improvement = weakest.improvements?.[0]
    || (weakest.score < 75 ? weakest.finding_summary : "");
  const chunks: string[] = [];
  if (strength) chunks.push(`${strongest.title}: ${conciseFinding(strength)}`);
  if (improvement && (weakest.key !== strongest.key || !strength)) {
    chunks.push(`${weakest.title} needs work: ${conciseFinding(improvement)}`);
  }
  if (chunks.length) return `${chunks.join(". ")}.`;
  return weakest.finding_summary || strongest.finding_summary || "";
}

/**
 * Collapse backend leaf components into the three Technical Setup Overview
 * criteria, preserving weighted scores and tab deep-links.
 */
export function groupTechnicalSetupComponents(
  components: TechnicalSetupScoreComponent[],
): TechnicalSetupScoreComponent[] {
  const byKey = new Map(components.map((component) => [component.key, component]));

  return TECHNICAL_SETUP_SUB_AREAS.flatMap((area) => {
    const parts = area.componentKeys
      .map((key) => byKey.get(key))
      .filter((component): component is TechnicalSetupScoreComponent => Boolean(component));
    if (!parts.length) return [];

    const score = weightedComponentsScore(parts);
    if (score === null) return [];

    const weightPct = parts.reduce((sum, component) => sum + component.weight_pct, 0);
    const siteExamples = parts.flatMap((component) => component.site_examples ?? []).slice(0, 3);
    const strengths = parts.flatMap((component) => component.strengths ?? []);
    const improvements = parts.flatMap((component) => component.improvements ?? []);

    return [{
      key: area.key,
      title: area.label,
      score: Math.round(score * 10) / 10,
      weight_pct: weightPct,
      detail: area.description,
      finding_summary: areaFindingSummary(parts),
      evidence_example:
        parts.map((component) => component.evidence_example).find((value) => value.trim()) ?? "",
      strengths: strengths.length ? strengths : undefined,
      improvements: improvements.length ? improvements : undefined,
      report_section: area.sectionId,
      site_examples: siteExamples.length ? siteExamples : undefined,
    }];
  });
}
