import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CheckCircle2, ChevronRight, Loader2 } from "lucide-react";
import { fetchAudit, fetchConfig, reportHtmlUrl, reportAllPagesHtmlUrl } from "../api/client";
import { PageLoading, usePageLoadingSignal } from "../components/PageLoading";
import { AiVisibilityOverview } from "../components/AiVisibilityOverview";
import { AiImpactDashboard } from "../components/AiImpactDashboard";
import { AiTrafficMethodsIntro } from "../components/AiTrafficMethodsIntro";
import { CitationsPage } from "../components/CitationsPage";
import { CompetitorComparisonDashboard } from "../components/BrandCompetitorVisibility";
import { SiteCrawlComparisonTable } from "../components/SiteCrawlComparisonTable";
import { ConfigSection } from "../components/ConfigSection";
import { ContentOutlineGeneratorSection } from "../components/ContentOutlineGeneratorSection";
import { ContentOverview } from "../components/ContentOverview";
import { PlatformReadinessSection } from "../components/PlatformReadinessSection";
import { PromptPerformanceSection } from "../components/PromptPerformanceSection";
import { TopicsVisibilitySection } from "../components/TopicsVisibilitySection";
import { RedditInsightsSection } from "../components/RedditInsightsSection";
import { RecommendationsSection } from "../components/RecommendationsSection";
import { ReportHeader } from "../components/ReportHeader";
import { ReportSectionErrorBoundary } from "../components/ReportSectionErrorBoundary";
import { SampleScriptsIntro } from "../components/SampleScriptsIntro";
import { SectionDownloadMenu, sectionHasPageDownload } from "../components/SectionDownloadMenu";
import ScoreOverTime from "../components/ScoreOverTime";
import { SummarySection } from "../components/SummarySection";
import { TechnicalOverview } from "../components/TechnicalOverview";
import { CrawlerAccessSection } from "../components/CrawlerAccessSection";
import { CitabilitySection } from "../components/CitabilitySection";
import {
  BrandVisibilityAuthoritySection,
  ContentStructureAnswerabilitySection,
  EeatSignalsSection,
  SchemaEntityMarkupSection,
} from "../components/ContentQualitySections";
import { WipSection } from "../components/WipSection";
import { YouTubeInsightsSection } from "../components/YouTubeInsightsSection";
import { WorkshopDashboard } from "../components/WorkshopDashboard";
import { SinglePageAuditSection } from "../components/SinglePageAuditSection";
import { Card, CardDescription } from "../components/ui/Card";
import { useCompetitorCrawl } from "../hooks/useCompetitorCrawl";
import {
  DEFAULT_REPORT_SECTIONS,
  normalizeReportSection,
  resolveReportSectionId,
  type ReportSectionDef,
} from "../lib/reportSections";
import { cn } from "../lib/utils";
import type { AppConfig, AuditDetail } from "../types";

interface SectionGroup {
  header: string;
  sections: ReportSectionDef[];
}

const GROUP_ORDER = [
  "Overview",
  "AI visibility",
  "Technical setup",
  "Content quality",
  "Workshop",
];

// Sections that are rendered as React components (not iframe)
const REACT_SECTIONS = new Set([
  "summary",
  "recommendations",
  "prompts",
  "topics",
  "citations",
  "config",
  "ai-visibility-overview",
  "ai-traffic-dashboard",
  "competitor-visibility",
  "competitor-comparison",
  "technical-overview",
  "content-overview",
  "reddit-citations",
  "youtube-citations",
  "content-outline-generator",
  "platform-readiness",
  "eeat-signals",
  "content-structure-answerability",
  "schema-entity-markup",
  "brand-visibility-authority",
  "crawler-access",
  "citability",
  "sample-scripts",
  "dashboard",
  "single-page-audits",
]);

// WIP sections (show placeholder)
const WIP_SECTIONS = new Set<string>();

const WIP_LABELS: Record<string, { title: string; description?: string }> = {
};

function buildGroups(sections: ReportSectionDef[]): SectionGroup[] {
  const map = new Map<string, ReportSectionDef[]>();
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

/** Same-origin report section path. */
function reportSectionPath(slug: string, sectionId: string): string {
  return `/report/${slug}/${sectionId}`;
}

/**
 * Mount only the active section, so switching sections tears the previous tree
 * down. Keyed by id so the shared fallback pane cannot carry a captured error
 * from the section the user just left.
 */
function ReportSectionPane({
  id,
  activeId,
  label,
  children,
}: {
  id: string;
  activeId: string;
  label?: string;
  children: ReactNode;
}) {
  if (activeId !== id) return null;
  return (
    <ReportSectionErrorBoundary key={id} sectionId={id} sectionLabel={label}>
      {children}
    </ReportSectionErrorBoundary>
  );
}

export function ReportPage() {
  const { auditId: slug, section: sectionParam } = useParams<{
    auditId: string;
    section?: string;
  }>();
  const navigate = useNavigate();
  const contentRef = useRef<HTMLDivElement>(null);
  const [audit, setAudit] = useState<AuditDetail | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [competitorsIframeKey, setCompetitorsIframeKey] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Derived once per config: an unstable identity here would re-fire the
  // canonical-path effect on every render and push duplicate history entries.
  const sections = useMemo(() => {
    const raw = (config?.report_sections ?? DEFAULT_REPORT_SECTIONS) as ReportSectionDef[];
    return raw
      .filter((item) => item.id !== "ai-impact")
      .map(normalizeReportSection);
  }, [config]);
  const sectionIds = useMemo(() => sections.map((s) => s.id), [sections]);
  const knownSectionIds = useMemo(() => new Set(sectionIds), [sectionIds]);
  const groups = useMemo(() => buildGroups(sections), [sections]);

  const section = useMemo(() => {
    const resolved = resolveReportSectionId(sectionParam);
    if (resolved === "dashboard" && config && !knownSectionIds.has("dashboard")) {
      return "summary";
    }
    if (knownSectionIds.has(resolved) || REACT_SECTIONS.has(resolved)) {
      return resolved;
    }
    return "summary";
  }, [sectionParam, knownSectionIds]);

  const auditRefEarly = audit?.audit_dir ?? slug ?? "";
  const competitorCrawl = useCompetitorCrawl(auditRefEarly);

  useEffect(() => {
    if (competitorCrawl.justCompleted) {
      setCompetitorsIframeKey((key) => key + 1);
      competitorCrawl.clearJustCompleted();
    }
  }, [competitorCrawl.justCompleted, competitorCrawl.clearJustCompleted]);

  useEffect(() => {
    if (section === "competitor-comparison") {
      void competitorCrawl.markSeen();
    }
  }, [section, competitorCrawl.status.status, competitorCrawl.status.seen, competitorCrawl.markSeen]);

  useEffect(() => {
    fetchConfig().then(setConfig).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!slug) return;
    const ac = new AbortController();
    setLoading(true);
    setError(null);
    fetchAudit(slug, { signal: ac.signal })
      .then(setAudit)
      .catch((e) => {
        if (ac.signal.aborted) return;
        setError(e instanceof Error ? e.message : "Could not load audit");
      })
      .finally(() => {
        if (!ac.signal.aborted) setLoading(false);
      });
    return () => ac.abort();
  }, [slug]);

  useEffect(() => {
    if (!slug || !audit || audit.has_report_html) return;
    const ac = new AbortController();
    const t = window.setInterval(() => {
      fetchAudit(slug, { signal: ac.signal })
        .then(setAudit)
        .catch(() => undefined);
    }, 4000);
    return () => {
      ac.abort();
      window.clearInterval(t);
    };
  }, [slug, audit?.has_report_html]);

  // Legacy / missing section ids → canonical path, replacing so Back still
  // returns to wherever the user entered the report from.
  useEffect(() => {
    if (!slug) return;
    const resolved = resolveReportSectionId(sectionParam);
    if (sectionParam && resolved !== sectionParam) {
      navigate(reportSectionPath(slug, resolved), { replace: true });
      return;
    }
    if (!sectionParam) {
      navigate(reportSectionPath(slug, section), { replace: true });
      return;
    }
    if (config && resolved === "dashboard" && !knownSectionIds.has("dashboard")) {
      navigate(reportSectionPath(slug, "summary"), { replace: true });
      return;
    }
    // Only rewrite an unrecognised section once the real list has loaded, or a
    // config-only section would be bounced to summary on first paint.
    if (config && !knownSectionIds.has(resolved) && !REACT_SECTIONS.has(resolved)) {
      navigate(reportSectionPath(slug, section), { replace: true });
    }
  }, [slug, sectionParam, section, knownSectionIds, config, navigate]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.data?.type !== "geo-report-nav") return;
      const next = resolveReportSectionId(String(event.data.section || ""));
      if (!slug || !knownSectionIds.has(next)) return;
      // Embedded reports announce their own hash, so ignore an echo of the
      // section we are already showing rather than pushing a duplicate entry.
      if (next === section) return;
      navigate(reportSectionPath(slug, next));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [slug, knownSectionIds, section, navigate]);

  // Each section is a fresh pane, so carry over none of the previous scroll.
  useEffect(() => {
    contentRef.current?.scrollTo({ top: 0 });
  }, [section]);

  const auditRef = audit?.audit_dir ?? slug ?? "";

  // Legacy report.html panel hashes (not renamed with sidebar paths).
  const ga4TrafficSrc = useMemo(
    () => reportHtmlUrl(auditRef, "ga4-traffic", true),
    [auditRef],
  );
  const samplesIframeSrc = useMemo(
    () => reportHtmlUrl(auditRef, "samples", true),
    [auditRef],
  );

  const goToSection = useCallback(
    (id: string, options?: { topic?: string }) => {
      if (!slug || id === section) return;
      const params = new URLSearchParams();
      if (options?.topic) params.set("topic", options.topic);
      const query = params.toString();
      navigate(`${reportSectionPath(slug, id)}${query ? `?${query}` : ""}`);
    },
    [navigate, section, slug],
  );

  /** Keep clicks on the current section from stacking identical history entries. */
  const onSectionLinkClick = (event: MouseEvent<HTMLAnchorElement>, id: string) => {
    if (id === section) event.preventDefault();
  };

  usePageLoadingSignal(loading, "report-audit");

  if (loading) {
    return <PageLoading label="Loading report…" />;
  }

  const meta = audit?.report_meta;
  const isIframeFallback =
    !REACT_SECTIONS.has(section) &&
    !WIP_SECTIONS.has(section) &&
    section !== "competitor-comparison" &&
    section !== "crawler-access" &&
    section !== "citability" &&
    section !== "sample-scripts";

  return (
    <div className="report-page flex flex-col min-h-full bg-[#e8e5e0]">
      {meta ? (
        <ReportHeader
          meta={meta}
          auditDirOrSlug={auditRef}
          allPagesHtmlUrl={audit?.has_report_html || audit ? reportAllPagesHtmlUrl(auditRef) : undefined}
        />
      ) : null}

      {error ? (
        <div className="px-6 py-4">
          <div className="alert-error">{error}</div>
        </div>
      ) : null}

      <div className="flex flex-1 min-h-0 flex-col lg:flex-row">
        {/* ── Sidebar — client-side routing, real hrefs for open-in-new-tab ── */}
        <nav
          className="report-section-nav lg:w-56 shrink-0 border-b lg:border-b-0 lg:border-r border-gray-200 bg-white/80 backdrop-blur-sm px-3 py-4 lg:py-6 overflow-y-auto"
          aria-label="Report sections"
        >
          {/* Mobile: flat scrollable list */}
          <ul className="flex lg:hidden gap-1 overflow-x-auto pb-1">
            {sections.map((s) => (
              <li key={s.id} className="shrink-0">
                <Link
                  to={slug ? reportSectionPath(slug, s.id) : "#"}
                  onClick={(e) => onSectionLinkClick(e, s.id)}
                  aria-current={section === s.id ? "page" : undefined}
                  className={cn(
                    "inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap",
                    section === s.id
                      ? "bg-[#0d0d0d] text-white"
                      : "text-gray-600 hover:bg-gray-100",
                  )}
                >
                  {s.label}
                  {s.id === "competitor-comparison" && competitorCrawl.busy && (
                    <Loader2 className="h-3.5 w-3.5 animate-spin opacity-80" aria-label="Competitor crawl running" />
                  )}
                  {s.id === "competitor-comparison" && competitorCrawl.showCompleteBadge && (
                    <CheckCircle2
                      className={cn("h-3.5 w-3.5", section === s.id ? "text-emerald-300" : "text-emerald-600")}
                      aria-label="Competitor crawl complete"
                    />
                  )}
                </Link>
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
                        <Link
                          to={slug ? reportSectionPath(slug, s.id) : "#"}
                          onClick={(e) => onSectionLinkClick(e, s.id)}
                          aria-current={isActive ? "page" : undefined}
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
                          {s.id === "competitor-comparison" && competitorCrawl.busy && (
                            <Loader2
                              className={cn(
                                "h-3.5 w-3.5 shrink-0 animate-spin",
                                isActive ? "text-white/80" : "text-blue-600",
                              )}
                              aria-label="Competitor crawl running"
                            />
                          )}
                          {s.id === "competitor-comparison" && competitorCrawl.showCompleteBadge && (
                            <CheckCircle2
                              className={cn(
                                "h-3.5 w-3.5 shrink-0",
                                isActive ? "text-emerald-300" : "text-emerald-600",
                              )}
                              aria-label="Competitor crawl complete"
                            />
                          )}
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
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        </nav>

        {/* ── Main content ── */}
        <div
          ref={contentRef}
          className="report-section-body relative flex-1 min-w-0 min-h-0 overflow-auto"
        >
          {sectionHasPageDownload(section) && auditRef ? (
            <div className="sticky top-0 z-20 flex justify-end border-b border-gray-200/80 bg-[#e8e5e0]/90 px-6 py-2 backdrop-blur-sm">
              <SectionDownloadMenu
                auditDirOrSlug={auditRef}
                sectionId={section}
                enabled
              />
            </div>
          ) : null}

          <ReportSectionPane id="ai-traffic-dashboard" activeId={section}>
            <div className="mx-auto flex w-full max-w-[1200px] flex-col gap-6 px-6 py-6 pb-8">
              <AiTrafficMethodsIntro />
              <section
                className="flex h-[900px] w-full min-w-0 flex-col overflow-hidden rounded-lg border border-neutral-300 bg-white"
                aria-labelledby="ai-impact-estimate-heading"
              >
                <h2
                  id="ai-impact-estimate-heading"
                  className="shrink-0 border-b border-neutral-200 px-5 py-3 text-sm font-semibold text-neutral-900"
                >
                  AI impact estimate
                </h2>
                <div className="min-h-0 flex-1 overflow-auto p-4">
                  <AiImpactDashboard auditDirOrSlug={auditRef} />
                </div>
              </section>
              {audit?.has_report_html ? (
                <section
                  className="flex h-[900px] w-full min-w-0 flex-col overflow-hidden rounded-lg border border-neutral-300 bg-white"
                  aria-labelledby="direct-ai-traffic-heading"
                >
                  <h2
                    id="direct-ai-traffic-heading"
                    className="shrink-0 border-b border-neutral-200 px-5 py-3 text-sm font-semibold text-neutral-900"
                  >
                    Direct AI traffic
                  </h2>
                  <iframe
                    title="Direct AI Traffic"
                    className="report-embed-frame min-h-0 w-full flex-1 border-0 bg-[#e8e5e0]"
                    src={ga4TrafficSrc}
                  />
                </section>
              ) : null}
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="summary" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <SummarySection
                auditDirOrSlug={auditRef}
                fallbackOverallScore={meta?.overall_score}
                onNavigate={goToSection}
              />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="recommendations" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <RecommendationsSection
                auditDirOrSlug={auditRef}
                onNavigate={goToSection}
              />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="prompts" activeId={section} label="Prompts">
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <PromptPerformanceSection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="topics" activeId={section} label="Topics">
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <TopicsVisibilitySection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane
            id="content-outline-generator"
            activeId={section}
            label="Content outline generator"
          >
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <ContentOutlineGeneratorSection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane
            id="single-page-audits"
            activeId={section}
            label="Single-page audit"
          >
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <SinglePageAuditSection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="citations" activeId={section} label="Citations">
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <CitationsPage auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="config" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <ConfigSection
                config={audit?.onboarding_context}
                auditId={auditRef}
                competitorCrawl={{
                  status: competitorCrawl.status,
                  busy: competitorCrawl.busy,
                  error: competitorCrawl.error,
                  onStart: () => {
                    void competitorCrawl.start();
                  },
                }}
              />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="ai-visibility-overview" activeId={section} label="AI visibility overview">
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <AiVisibilityOverview auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="competitor-visibility" activeId={section} label="Competitor visibility">
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <CompetitorComparisonDashboard auditId={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="competitor-comparison" activeId={section}>
            <div className="mx-auto w-full max-w-[1200px] space-y-6 px-6 py-8">
              <ScoreOverTime
                auditId={auditRef}
                brandLabel={
                  meta?.brand_name
                  || audit?.onboarding_context?.brand_name_used
                  || "Your brand"
                }
                metricToggle
                defaultMetric="overall"
                title="Scores over time"
                description="Compare your brand with tracked competitors across Overall and pillar scores."
              />
              {(competitorCrawl.busy ||
                competitorCrawl.status.status === "done" ||
                competitorCrawl.error) && (
                <div className="overflow-hidden rounded-xl border border-gray-200 bg-white px-6 py-4">
                  {competitorCrawl.busy ? (
                    <p className="text-xs text-blue-700">
                      <Loader2 className="mr-1.5 inline h-3.5 w-3.5 animate-spin" />
                      {competitorCrawl.status.detail || "Crawling competitor sites…"} Progress
                      continues in the background.
                    </p>
                  ) : competitorCrawl.error ? (
                    <p className="text-xs text-red-700">{competitorCrawl.error}</p>
                  ) : (
                    <p className="text-xs text-emerald-700">
                      <CheckCircle2 className="mr-1.5 inline h-3.5 w-3.5" />
                      Competitor site crawl complete
                      {competitorCrawl.status.finished_at
                        ? ` · ${new Date(competitorCrawl.status.finished_at).toLocaleString(undefined, {
                            day: "numeric",
                            month: "short",
                            hour: "2-digit",
                            minute: "2-digit",
                          })}`
                        : ""}
                      .
                    </p>
                  )}
                </div>
              )}
              <SiteCrawlComparisonTable
                auditDirOrSlug={auditRef}
                refreshKey={competitorsIframeKey}
              />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="sample-scripts" activeId={section}>
            <div className="flex flex-col">
              <div className="mx-auto w-full max-w-[1200px] px-6 pt-6">
                <SampleScriptsIntro auditDirOrSlug={auditRef} />
              </div>
              {audit?.has_report_html ? (
                <iframe
                  title="Sample scripts"
                  className="report-embed-frame mt-4 w-full border-0 bg-[#e8e5e0] min-h-[calc(100vh-12rem)]"
                  src={samplesIframeSrc}
                />
              ) : (
                <div className="max-w-[1200px] mx-auto px-6 py-8">
                  <Card>
                    <CardDescription>
                      Sample script files are not available for this audit yet.
                    </CardDescription>
                  </Card>
                </div>
              )}
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="dashboard" activeId={section}>
            <div className="mx-auto w-full max-w-[1400px] px-6 py-8">
              <WorkshopDashboard auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="platform-readiness" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <PlatformReadinessSection auditDirOrSlug={auditRef} onNavigate={goToSection} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="technical-overview" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <TechnicalOverview auditDirOrSlug={auditRef} onNavigate={goToSection} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="crawler-access" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <CrawlerAccessSection auditDirOrSlug={auditRef} onNavigate={goToSection} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="citability" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <CitabilitySection auditDirOrSlug={auditRef} onNavigate={goToSection} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="content-overview" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <ContentOverview auditDirOrSlug={auditRef} onNavigate={goToSection} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="eeat-signals" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <EeatSignalsSection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="content-structure-answerability" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <ContentStructureAnswerabilitySection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="schema-entity-markup" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <SchemaEntityMarkupSection auditDirOrSlug={auditRef} onNavigate={goToSection} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="brand-visibility-authority" activeId={section}>
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <BrandVisibilityAuthoritySection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="reddit-citations" activeId={section} label="Reddit Citations">
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <RedditInsightsSection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          <ReportSectionPane id="youtube-citations" activeId={section} label="YouTube Citations">
            <div className="max-w-[1200px] mx-auto px-6 py-8">
              <YouTubeInsightsSection auditDirOrSlug={auditRef} />
            </div>
          </ReportSectionPane>

          {WIP_SECTIONS.has(section) ? (
            <ReportSectionPane id={section} activeId={section}>
              <div className="max-w-[1200px] mx-auto px-6 py-8">
                <WipSection
                  title={WIP_LABELS[section]?.title ?? section}
                  description={WIP_LABELS[section]?.description}
                />
              </div>
            </ReportSectionPane>
          ) : null}

          {isIframeFallback ? (
            <ReportSectionPane id={section} activeId={section}>
              {audit?.has_report_html ? (
                // Sections share one report.html and differ only by hash, so a
                // reused frame would fire hashchange inside the embed (bouncing a
                // geo-report-nav message back at us) and add a history entry that
                // swallows the Back button. Keying forces a fresh frame instead.
                <iframe
                  key={`${auditRef}:${section}`}
                  title={`GEO audit report section ${section}`}
                  className="report-embed-frame w-full border-0 bg-[#e8e5e0] min-h-[calc(100vh-12rem)]"
                  src={reportHtmlUrl(auditRef, section, true)}
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
            </ReportSectionPane>
          ) : null}
        </div>
      </div>
    </div>
  );
}
