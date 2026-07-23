import { reportSectionHtmlUrl } from "../api/client";
import { ReportDownloadMenu } from "./ReportDownloadMenu";

/** Sections that have a refreshed HTML/PDF export matching the React UI. */
const EXPORTABLE_SECTIONS = new Set([
  "summary",
  "recommendations",
  "ai-traffic-dashboard",
  "competitor-comparison",
  "citability",
  "ai-visibility-overview",
  "crawler-access",
  "technical-overview",
  "content-overview",
  "eeat-signals",
  "content-structure-answerability",
  "schema-entity-markup",
  "brand-visibility-authority",
  "prompts",
  "competitor-visibility",
  "citations",
  "reddit-citations",
  "youtube-citations",
  "platform-readiness",
]);

export function sectionHasPageDownload(sectionId: string): boolean {
  return EXPORTABLE_SECTIONS.has(sectionId);
}

export function SectionDownloadMenu({
  auditDirOrSlug,
  sectionId,
  enabled = true,
}: {
  auditDirOrSlug: string;
  sectionId: string;
  enabled?: boolean;
}) {
  if (!enabled || !sectionHasPageDownload(sectionId) || !auditDirOrSlug) {
    return null;
  }

  const safeSection = sectionId.replace(/[^\w\-]+/g, "-");
  return (
    <ReportDownloadMenu
      variant="section"
      label="Download page"
      auditDirOrSlug={auditDirOrSlug}
      pdfSection={sectionId}
      htmlUrl={reportSectionHtmlUrl(auditDirOrSlug, sectionId)}
      pdfFilename={`geo-report-${safeSection}.pdf`}
      htmlFilename={`geo-report-${safeSection}.html`}
    />
  );
}
