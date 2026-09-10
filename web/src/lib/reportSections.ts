/**
 * Report sidebar section ids — kebab-case paths that match sidebar labels.
 *
 * Slug rules:
 * - lowercase the label
 * - drop `&` (and surrounding spaces become a single hyphen)
 * - spaces / underscores → hyphens
 * - strip punctuation (e.g. E-E-A-T → eeat)
 * - collapse repeated hyphens
 * - duplicate "Overview" labels keep a group prefix for uniqueness
 */

export interface ReportSectionDef {
  id: string;
  label: string;
  group?: string;
}

/** Canonical section list (fallback when config API is unavailable). */
export const DEFAULT_REPORT_SECTIONS: ReportSectionDef[] = [
  { id: "summary", label: "Summary", group: "Overview" },
  { id: "config", label: "Config", group: "Overview" },
  { id: "recommendations", label: "Recommendations", group: "Overview" },
  { id: "ai-traffic-dashboard", label: "AI Traffic Dashboard", group: "Overview" },
  { id: "competitor-comparison", label: "Competitor comparison", group: "Overview" },
  { id: "ai-visibility-overview", label: "Overview", group: "AI visibility" },
  { id: "prompts", label: "Prompts", group: "AI visibility" },
  { id: "topics", label: "Topics", group: "AI visibility" },
  { id: "competitor-visibility", label: "Competitor visibility", group: "AI visibility" },
  { id: "citations", label: "Citations", group: "AI visibility" },
  { id: "reddit-citations", label: "Reddit Citations", group: "AI visibility" },
  { id: "youtube-citations", label: "YouTube Citations", group: "AI visibility" },
  { id: "technical-overview", label: "Overview", group: "Technical setup" },
  { id: "crawler-access", label: "Crawler access", group: "Technical setup" },
  { id: "citability", label: "Citability", group: "Technical setup" },
  { id: "platform-readiness", label: "Platform readiness", group: "Technical setup" },
  { id: "content-overview", label: "Overview", group: "Content quality" },
  { id: "eeat-signals", label: "E-E-A-T Signals", group: "Content quality" },
  { id: "content-structure-answerability", label: "Content Structure & Answerability", group: "Content quality" },
  { id: "schema-entity-markup", label: "Schema & Entity Markup", group: "Content quality" },
  { id: "brand-visibility-authority", label: "Brand Visibility & Authority", group: "Content quality" },
  { id: "sample-scripts", label: "Sample scripts", group: "Workshop" },
  { id: "content-outline-generator", label: "Content outline generator", group: "Workshop" },
  { id: "single-page-audits", label: "Single-page audit", group: "Workshop" },
];

/**
 * Old URL / API section ids → canonical sidebar-matching ids.
 * Keep forever so bookmarks and stored report_section values keep working.
 */
export const REPORT_SECTION_ALIASES: Record<string, string> = {
  // Traffic
  "ai-impact": "ai-traffic-dashboard",
  "ga4-traffic": "ai-traffic-dashboard",
  // Overview competitor page
  competitors: "competitor-comparison",
  // AI visibility
  prompt_performance: "prompts",
  "reddit-insights": "reddit-citations",
  "youtube-insights": "youtube-citations",
  "competitor-performance": "competitor-visibility",
  // Technical setup
  technical: "crawler-access",
  "ai-visibility": "citability",
  // Content quality (legacy catch-all + prior tab ids)
  content: "eeat-signals",
  "content-eeat": "eeat-signals",
  "content-structure": "content-structure-answerability",
  "content-schema": "schema-entity-markup",
  "content-brand-visibility": "brand-visibility-authority",
  // Workshop
  samples: "sample-scripts",
  "content-outline": "content-outline-generator",
  "single-page-audit": "single-page-audits",
};

/** Resolve a URL/API section id to the canonical sidebar id. */
export function resolveReportSectionId(sectionId: string | undefined | null): string {
  const key = (sectionId || "").trim();
  if (!key) return "summary";
  return REPORT_SECTION_ALIASES[key] ?? key;
}

/** Normalize a section def from API config (rewrite legacy ids). */
export function normalizeReportSection(section: ReportSectionDef): ReportSectionDef {
  return {
    ...section,
    id: resolveReportSectionId(section.id),
  };
}
