import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { ChevronRight, Loader2 } from "lucide-react";
import { fetchAudit, fetchConfig, reportHtmlUrl, reportAllPagesHtmlUrl, reportPdfUrl } from "../api/client";
import { AiVisibilityOverview } from "../components/AiVisibilityOverview";
import { CitationsPage } from "../components/CitationsPage";
import { CompetitorComparisonSection } from "../components/CompetitorComparisonSection";
import { ConfigSection } from "../components/ConfigSection";
import { PromptPerformanceSection } from "../components/PromptPerformanceSection";
import { ReportHeader } from "../components/ReportHeader";
import { WipSection } from "../components/WipSection";
import { Card, CardDescription } from "../components/ui/Card";
import { cn } from "../lib/utils";
import type { AppConfig, AuditDetail } from "../types";

interface SectionDef {
  id: string;
  label: string;
  group?: string;
}

interface SectionGroup {
  header: string;
  sections: SectionDef[];
}

const DEFAULT_SECTIONS: SectionDef[] = [
  { id: "summary", label: "Summary", group: "Overview" },
  { id: "config", label: "Config", group: "Overview" },
  { id: "ga4-traffic", label: "AI Traffic Dashboard", group: "Overview" },
  { id: "ai-visibility-overview", label: "Overview", group: "AI visibility" },
  { id: "prompt_performance", label: "Prompts", group: "AI visibility" },
  { id: "competitor-performance", label: "Competitor comparison", group: "AI visibility" },
  { id: "citations", label: "Citations", group: "AI visibility" },
  { id: "technical-overview", label: "Overview", group: "Technical setup" },
  { id: "technical", label: "Crawler access", group: "Technical setup" },
  { id: "ai-visibility", label: "Citability", group: "Technical setup" },
  { id: "competitors", label: "Competitor sites", group: "Technical setup" },
  { id: "platform-readiness", label: "Platform readiness", group: "Technical setup" },
  { id: "content-overview", label: "Overview", group: "Content quality" },
  { id: "content", label: "EEAT & Brand visibility", group: "Content quality" },
  { id: "reddit-insights", label: "Reddit insights", group: "Content quality" },
  { id: "youtube-insights", label: "YouTube insights", group: "Content quality" },
  { id: "samples", label: "Sample scripts", group: "Workshop" },
  { id: "content-outline", label: "Content outline generator", group: "Workshop" },
];

const GROUP_ORDER = [
  "Overview",
  "AI visibility",
  "Technical setup",
  "Content quality",
  "Workshop",
];

// Sections that are rendered as React components (not iframe)
const REACT_SECTIONS = new Set([
  "prompt_performance",
  "citations",
  "config",
  "ai-visibility-overview",
  "competitor-performance",
  "technical-overview",
  "content-overview",
  "reddit-insights",
  "youtube-insights",
  "content-outline",
  "platform-readiness",
]);

// WIP sections
const WIP_SECTIONS = new Set([
  "technical-overview",
  "content-overview",
  "platform-readiness",
  "reddit-insights",
  "youtube-insights",
  "content-outline",
]);

const WIP_LABELS: Record<string, { title: string; description?: string }> = {
  "technical-overview": {
    title: "Technical setup overview",
    description: "A highlights summary of crawler access, citability and platform readiness. Coming soon.",
  },
  "content-overview": {
    title: "Content quality overview",
    description: "A highlights summary of EEAT, brand visibility and content insights. Coming soon.",
  },
  "platform-readiness": {
    title: "Platform readiness",
    description: "Detailed AI platform readiness checks across ChatGPT, Gemini, Claude and more. Coming soon.",
  },
  "reddit-insights": {
    title: "Reddit insights",
    description: "Brand and competitor mention analysis across Reddit communities. Coming soon.",
  },
  "youtube-insights": {
    title: "YouTube insights",
    description: "Brand and competitor visibility in YouTube search results and content. Coming soon.",
  },
  "content-outline": {
    title: "Content outline generator",
    description: "AI-powered content outline generation based on your brand and target prompts. Coming soon.",
  },
};

function buildGroups(sections: SectionDef[]): SectionGroup[] {
  const map = new Map<string, SectionDef[]>();
  for (const s of sections) {
    const g = s.group ?? "Other";
    if (!map.has(g)) map.set(g, []);
    map.get(g)!.push(s);
  }
  const ordered: SectionGroup[] = [];
  for (const g of GROUP_ORDER) {
    if (map.has(g)) {
      ordered.push({ header: g, sections: map.get(g)! });
    }
  }
  for (const [g, secs] of map) {
    if (!GROUP_ORDER.includes(g)) {
      ordered.push({ header: g, sections: secs });
    }
  }
  return ordered;
}

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

  const rawSections = (config?.report_sections ?? DEFAULT_SECTIONS) as SectionDef[];
  const sections = rawSections;
  const sectionIds = useMemo(() => sections.map((s) => s.id), [sections]);
  const groups = useMemo(() => buildGroups(sections), [sections]);

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
    if (REACT_SECTIONS.has(section)) return null;
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
        {/* ── Sidebar ── */}
        <nav
          className="report-section-nav lg:w-56 shrink-0 border-b lg:border-b-0 lg:border-r border-gray-200 bg-white/80 backdrop-blur-sm px-3 py-4 lg:py-6 overflow-y-auto"
          aria-label="Report sections"
        >
          {/* Mobile: flat scrollable list */}
          <ul className="flex lg:hidden gap-1 overflow-x-auto pb-1">
            {sections.map((s) => (
              <li key={s.id} className="shrink-0">
                <button
                  type="button"
                  onClick={() => goToSection(s.id)}
                  className={cn(
                    "px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap",
                    section === s.id
                      ? "bg-[#0d0d0d] text-white"
                      : "text-gray-600 hover:bg-gray-100",
                  )}
                >
                  {s.label}
                </button>
              </li>
            ))}
          </ul>

          {/* Desktop: grouped list */}
          <div className="hidden lg:block space-y-4">
            {groups.map((grp) => (
              <div key={grp.header}>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 px-2 mb-1">
                  {grp.header}
                </p>
                <ul className="space-y-0.5">
                  {grp.sections.map((s) => {
                    const isWip = WIP_SECTIONS.has(s.id);
                    const isActive = section === s.id;
                    return (
                      <li key={s.id}>
                        <button
                          type="button"
                          onClick={() => goToSection(s.id)}
                          className={cn(
                            "w-full text-left px-2.5 py-2 rounded-md text-[13px] transition-colors flex items-center gap-1.5 group",
                            isActive
                              ? "bg-[#0d0d0d] text-white font-medium"
                              : "text-gray-600 hover:bg-gray-100 hover:text-[#0d0d0d]",
                          )}
                        >
                          <ChevronRight
                            className={cn(
                              "w-3 h-3 shrink-0 transition-transform",
                              isActive ? "opacity-100 text-white" : "opacity-0 group-hover:opacity-40",
                            )}
                          />
                          <span className="flex-1">{s.label}</span>
                          {isWip && (
                            <span
                              className={cn(
                                "text-[9px] font-bold px-1.5 py-0.5 rounded",
                                isActive ? "bg-white/20 text-white" : "bg-amber-100 text-amber-600",
                              )}
                            >
                              WIP
                            </span>
                          )}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </nav>

        {/* ── Main content ── */}
        <div className="report-section-body flex-1 min-w-0 min-h-0 overflow-auto">
          {section === "prompt_performance" ? (
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <PromptPerformanceSection auditDirOrSlug={auditRef} />
            </div>
          ) : section === "citations" ? (
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <CitationsPage auditDirOrSlug={auditRef} />
            </div>
          ) : section === "config" ? (
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <ConfigSection config={audit?.onboarding_context} />
            </div>
          ) : section === "ai-visibility-overview" ? (
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <AiVisibilityOverview auditDirOrSlug={auditRef} />
            </div>
          ) : section === "competitor-performance" ? (
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <CompetitorComparisonSection auditDirOrSlug={auditRef} />
            </div>
          ) : WIP_SECTIONS.has(section) ? (
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <WipSection
                title={WIP_LABELS[section]?.title ?? section}
                description={WIP_LABELS[section]?.description}
              />
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
