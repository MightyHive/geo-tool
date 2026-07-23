/**
 * Shared Content Quality sub-areas used by Content Overview and the
 * Summary "how is this score calculated?" overlay so names, weights,
 * scores, and report-section links stay aligned.
 */

export interface ContentQualityScoreComponent {
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

export const CONTENT_QUALITY_SUB_AREAS = [
  {
    key: "eeat",
    label: "E-E-A-T Signals",
    description:
      "Experience, Expertise, Authoritativeness, and Trustworthiness signals across key pages. Strong E-E-A-T is the primary determinant of AI citation likelihood.",
    tooltip:
      "Evaluates first-hand experience, credible expertise and authorship, authority markers, and trust and transparency signals.",
    sectionId: "eeat-signals",
    componentKeys: ["eeat"] as const,
  },
  {
    key: "structure_answerability",
    label: "Content Structure & Answerability",
    description:
      "Whether content contributes genuinely original information and is structured into clear, self-contained passages AI can extract.",
    tooltip:
      "Combines dedicated originality signals, passage-level answerability, and content formatting. Routine first-hand experience does not count as original information.",
    sectionId: "content-structure-answerability",
    componentKeys: [
      "original_information_gain",
      "passage_answerability",
      "content_formatting",
    ] as const,
  },
  {
    key: "schema_entity_markup",
    label: "Schema & Entity Markup",
    description:
      "JSON-LD structured data coverage and entity clarity. Schema helps AI tools understand your brand identity, products, and relationships.",
    tooltip:
      "Covers Organisation, Product, FAQPage, BreadcrumbList, Article schema types plus sameAs links for entity corroboration.",
    sectionId: "schema-entity-markup",
    componentKeys: ["schema_entity_markup"] as const,
  },
  {
    key: "brand_visibility_authority",
    label: "Brand Visibility & Authority",
    description:
      "How prominently the brand appears across Wikipedia, YouTube, Reddit, LinkedIn, and other third-party surfaces that AI models use to verify entities.",
    tooltip:
      "Scans four major platforms for brand presence and measures consistency of brand information across the web.",
    sectionId: "brand-visibility-authority",
    componentKeys: ["brand_visibility_authority"] as const,
  },
] as const;

export type ContentQualitySubArea = (typeof CONTENT_QUALITY_SUB_AREAS)[number];

/** Weighted average of component scores (same formula as Content Overview). */
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

function areaFindingSummary(parts: ContentQualityScoreComponent[]): string {
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
 * Collapse backend leaf components into the four Content Quality Overview
 * criteria, preserving weighted scores and tab deep-links.
 */
export function groupContentQualityComponents(
  components: ContentQualityScoreComponent[],
): ContentQualityScoreComponent[] {
  const byKey = new Map(components.map((component) => [component.key, component]));

  return CONTENT_QUALITY_SUB_AREAS.flatMap((area) => {
    const parts = area.componentKeys
      .map((key) => byKey.get(key))
      .filter((component): component is ContentQualityScoreComponent => Boolean(component));
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
