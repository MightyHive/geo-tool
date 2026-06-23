import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { fetchAudit, fetchConfig, reportHtmlUrl, reportAllPagesHtmlUrl, reportPdfUrl } from "../api/client";
import { PromptPerformanceSection } from "../components/PromptPerformanceSection";
import { ReportHeader } from "../components/ReportHeader";
import { Card, CardDescription } from "../components/ui/Card";
import { cn } from "../lib/utils";
import type { AppConfig, AuditDetail } from "../types";

const DEFAULT_SECTIONS = [
  { id: "summary", label: "Summary" },
  { id: "ga4-traffic", label: "AI traffic (GA4)" },
  { id: "recommendations", label: "Recommendations" },
  { id: "competitors", label: "Competitor comparison" },
  { id: "ai-visibility", label: "AI visibility" },
  { id: "technical", label: "Technical setup" },
  { id: "content", label: "Content quality" },
  { id: "samples", label: "Sample scripts" },
  { id: "prompt_performance", label: "Prompt performance" },
];

export function ReportPage() {
  const { auditId: slug, section: sectionParam } = useParams<{
    auditId: string;
    section?: string;
  }>();
  const navigate = useNavigate();
  const [audit, setAudit] = useState<AuditDetail | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const sections = config?.report_sections ?? DEFAULT_SECTIONS;
  const sectionIds = useMemo(() => sections.map((s) => s.id), [sections]);

  const section = useMemo(() => {
    if (sectionParam && sectionIds.includes(sectionParam)) return sectionParam;
    return "summary";
  }, [sectionParam, sectionIds]);

  useEffect(() => {
    fetchConfig().then(setConfig).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!slug) return;
    setLoading(true);
    setError(null);
    fetchAudit(slug)
      .then(setAudit)
      .catch((e) =>
        setError(e instanceof Error ? e.message : "Could not load audit"),
      )
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    if (!slug || !audit || audit.has_report_html) return;
    const t = window.setInterval(() => {
      fetchAudit(slug)
        .then(setAudit)
        .catch(() => undefined);
    }, 4000);
    return () => window.clearInterval(t);
  }, [slug, audit?.has_report_html]);

  useEffect(() => {
    if (!slug) return;
    if (!sectionParam || !sectionIds.includes(sectionParam)) {
      navigate(`/report/${slug}/${section}`, { replace: true });
    }
  }, [slug, sectionParam, section, sectionIds, navigate]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.data?.type !== "geo-report-nav") return;
      const next = String(event.data.section || "");
      if (!slug || !sectionIds.includes(next)) return;
      navigate(`/report/${slug}/${next}`);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [slug, sectionIds, navigate]);

  const auditRef = audit?.audit_dir ?? slug ?? "";

  const iframeSrc = useMemo(() => {
    if (section === "prompt_performance") return null;
    return reportHtmlUrl(auditRef, section, true);
  }, [auditRef, section]);

  const goToSection = (id: string) => {
    if (!slug) return;
    navigate(`/report/${slug}/${id}`);
  };

  if (loading) {
    return (
      <div className="flex flex-1 items-center justify-center min-h-[50vh]">
        <Loader2 className="w-8 h-8 animate-spin text-brand-accent" />
      </div>
    );
  }

  const meta = audit?.report_meta;

  return (
    <div className="report-page flex flex-col min-h-full bg-[#e8e5e0]">
      {meta ? (
        <ReportHeader
          meta={meta}
          downloadUrl={audit?.has_report_html ? reportPdfUrl(auditRef) : undefined}
          allPagesHtmlUrl={audit?.has_report_html ? reportAllPagesHtmlUrl(auditRef) : undefined}
        />
      ) : null}

      {error ? (
        <div className="px-6 py-4">
          <div className="alert-error">{error}</div>
        </div>
      ) : null}

      <div className="flex flex-1 min-h-0 flex-col lg:flex-row">
        <nav
          className="report-section-nav lg:w-60 shrink-0 border-b lg:border-b-0 lg:border-r border-gray-200 bg-white/80 backdrop-blur-sm px-4 py-4 lg:py-6"
          aria-label="Report sections"
        >
          <p className="hidden lg:block text-xs font-semibold uppercase tracking-wider text-gray-500 mb-3 px-2">
            Sections
          </p>
          <ul className="flex lg:flex-col gap-1 overflow-x-auto lg:overflow-visible pb-1 lg:pb-0">
            {sections.map((s) => (
              <li key={s.id} className="shrink-0">
                <button
                  type="button"
                  onClick={() => goToSection(s.id)}
                  className={cn(
                    "w-full text-left px-3 py-2.5 rounded-lg text-sm font-medium transition-colors whitespace-nowrap lg:whitespace-normal",
                    section === s.id
                      ? "bg-[#0d0d0d] text-white"
                      : "text-gray-600 hover:bg-gray-100 hover:text-[#0d0d0d]",
                  )}
                >
                  {s.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <div className="report-section-body flex-1 min-w-0 min-h-0 overflow-auto">
          {section === "prompt_performance" ? (
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <PromptPerformanceSection auditDirOrSlug={auditRef} />
            </div>
          ) : audit?.has_report_html && iframeSrc ? (
            <iframe
              title="GEO audit report section"
              className="report-embed-frame w-full border-0 bg-[#e8e5e0] min-h-[calc(100vh-12rem)]"
              src={iframeSrc}
              key={iframeSrc}
            />
          ) : (
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <Card>
                <CardDescription>
                  Report HTML is not available for this audit.
                </CardDescription>
              </Card>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
