import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Loader2 } from "lucide-react";
import { fetchPageAudit, type ScoreBreakdown } from "../api/client";
import { PageAuditScoreCards } from "../components/SinglePageAuditSection";
import { PagePromptsSection } from "../components/PagePromptsSection";
import { PageLoading } from "../components/PageLoading";
import { PageHeader } from "../components/PageHeader";
import { RecommendationsSection } from "../components/RecommendationsSection";
import { CitabilitySection } from "../components/CitabilitySection";
import { CrawlerAccessSection } from "../components/CrawlerAccessSection";
import { PlatformReadinessSection } from "../components/PlatformReadinessSection";
import { TechnicalOverview } from "../components/TechnicalOverview";
import { ContentOverview } from "../components/ContentOverview";
import {
  BrandVisibilityAuthoritySection,
  ContentStructureAnswerabilitySection,
  EeatSignalsSection,
  SchemaEntityMarkupSection,
} from "../components/ContentQualitySections";
import type { PageAuditDetail } from "../types";
import { formatReportScore } from "../lib/reportScore";

const ACTIVE_STATUSES = new Set(["queued", "running"]);

const NAV_ITEMS: Array<{ id: string; label: string; scope: "page_specific" | "inherited" }> = [
  { id: "summary", label: "Summary", scope: "page_specific" },
  { id: "recommendations", label: "Recommendations", scope: "page_specific" },
  { id: "ai-visibility", label: "AI visibility", scope: "page_specific" },
  { id: "citations", label: "Citations", scope: "page_specific" },
  { id: "page-prompts", label: "Page prompts", scope: "page_specific" },
  { id: "technical-overview", label: "Technical", scope: "page_specific" },
  { id: "crawler-access", label: "Crawler access", scope: "inherited" },
  { id: "citability", label: "Citability", scope: "page_specific" },
  { id: "platform-readiness", label: "Platform readiness", scope: "inherited" },
  { id: "content-overview", label: "Content", scope: "page_specific" },
  { id: "eeat-signals", label: "E-E-A-T", scope: "page_specific" },
  { id: "content-structure-answerability", label: "Structure", scope: "page_specific" },
  { id: "schema-entity-markup", label: "Schema", scope: "page_specific" },
  { id: "brand-visibility-authority", label: "Brand visibility", scope: "inherited" },
];

function ScopeBanner({ scope }: { scope: "page_specific" | "inherited" }) {
  const inherited = scope === "inherited";
  return (
    <p
      className={`mb-4 inline-flex rounded-full px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${
        inherited ? "bg-amber-50 text-amber-800" : "bg-violet-50 text-violet-800"
      }`}
    >
      {inherited
        ? "Inherited from master audit — site-wide signal"
        : "Page-specific"}
    </p>
  );
}

function scrollToSection(sectionId: string) {
  document.getElementById(sectionId)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

export function SinglePageAuditDetailPage() {
  const { parentId = "", pageId = "" } = useParams();
  const [audit, setAudit] = useState<PageAuditDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!parentId || !pageId) return;
    let cancelled = false;
    setLoading(true);
    fetchPageAudit(parentId, pageId)
      .then((payload) => {
        if (!cancelled) setAudit(payload);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not load page audit");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [parentId, pageId]);

  useEffect(() => {
    const status = audit?.probe_status || "";
    if (!parentId || !pageId || !["queued", "starting", "running"].includes(status)) return;
    const timer = window.setInterval(() => {
      fetchPageAudit(parentId, pageId)
        .then(setAudit)
        .catch(() => {});
    }, 3000);
    return () => window.clearInterval(timer);
  }, [parentId, pageId, audit?.probe_status]);

  const active = audit ? ACTIVE_STATUSES.has(audit.status) : false;
  useEffect(() => {
    if (!active || !parentId || !pageId) return;
    const id = window.setInterval(() => {
      fetchPageAudit(parentId, pageId)
        .then(setAudit)
        .catch(() => undefined);
    }, 2500);
    return () => window.clearInterval(id);
  }, [active, parentId, pageId]);

  if (loading && !audit) {
    return (
      <div className="page-container">
        <PageLoading label="Loading page audit…" />
      </div>
    );
  }

  if (error || !audit) {
    return (
      <div className="page-container">
        <div className="alert-error">{error || "Page audit not found"}</div>
        <Link to="/page-audits" className="mt-4 inline-flex text-sm font-semibold text-violet-700">
          Back to single-page audits
        </Link>
      </div>
    );
  }

  const masterHref = `/report/${encodeURIComponent(parentId)}/summary`;
  const workshopHref = `/report/${encodeURIComponent(parentId)}/single-page-audits`;
  const citationsHref = `/report/${encodeURIComponent(parentId)}/citations`;
  const breakdown = (audit.breakdown ?? null) as ScoreBreakdown | null;
  const contentDetails = breakdown?.content_quality_details ?? null;
  const scoreDetails = audit.scores?.details ?? {};
  const promptMetrics = audit.scores?.prompt_metrics ?? {};
  const surfaceMetrics = audit.scores?.surface_metrics ?? {};
  const citationCount = Number(scoreDetails.citation_count ?? audit.citations?.length ?? 0);
  const citability = Number(scoreDetails.page_citability ?? 0);
  const visibilityPct = Number(promptMetrics.visibility_pct ?? 0);
  const sovPerformance = Number(promptMetrics.sov_performance_score ?? 0);

  return (
    <div className="page-container space-y-8">
      <Link
        to="/page-audits"
        className="inline-flex items-center gap-1 text-sm font-medium text-gray-600 hover:text-gray-900"
      >
        <ArrowLeft className="h-4 w-4" />
        All single-page audits
      </Link>
      <PageHeader
        title={audit.title || audit.url}
        description={audit.url}
      />

      {ACTIVE_STATUSES.has(audit.status) ? (
        <p className="inline-flex items-center gap-2 text-sm text-gray-600">
          <Loader2 className="h-4 w-4 animate-spin" />
          {audit.stage === "generating_prompts"
            ? "Generating required page prompts…"
            : audit.stage === "probing"
              ? "Running required AI visibility probes…"
              : "Analysing this page…"}
        </p>
      ) : null}

      {audit.error ? <div className="alert-error">{audit.error}</div> : null}

      <nav className="sticky top-0 z-10 -mx-2 overflow-x-auto bg-[#f7f6f3] py-2">
        <ul className="flex min-w-max gap-2 px-2">
          {NAV_ITEMS.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => scrollToSection(item.id)}
                className="rounded-full border border-gray-200 bg-white px-3 py-1 text-xs font-semibold text-gray-700 hover:border-violet-300 hover:text-violet-800"
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <section id="summary" className="scroll-mt-16 space-y-3">
        <ScopeBanner scope="page_specific" />
        <PageAuditScoreCards scores={audit.scores} />
        {audit.scores?.details ? (
          <div className="rounded-2xl border border-gray-200 bg-white p-5 text-sm text-gray-700">
            <p>
              Brand visibility {Math.round(visibilityPct)}%
              {" · "}
              Relative SOV score {formatReportScore(sovPerformance)}
              {" · "}
              Page citability {formatReportScore(citability)}
            </p>
            <p className="mt-2 text-xs text-gray-500">
              Overall = 40% AI visibility + 30% technical + 30% content. AI visibility uses the
              same formula as the main report: 60% response visibility + 40% relative share of
              voice, calculated only from prompts generated for this page.
            </p>
          </div>
        ) : null}
      </section>

      <section id="recommendations" className="scroll-mt-16 space-y-3">
        <ScopeBanner scope="page_specific" />
        {breakdown ? (
          <RecommendationsSection
            auditDirOrSlug={parentId}
            breakdown={breakdown}
            pageScoped
            onNavigate={scrollToSection}
          />
        ) : (
          <p className="text-sm text-gray-500">Recommendations appear after scoring finishes.</p>
        )}
      </section>

      <section id="ai-visibility" className="scroll-mt-16 space-y-3">
        <ScopeBanner scope="page_specific" />
        <div className="rounded-2xl border border-gray-200 bg-white p-5">
          <h2 className="text-xl font-bold text-[#0d0d0d]">AI visibility for this page</h2>
          <p className="mt-1 text-sm text-gray-500">
            Isolated from the master audit’s site-wide probes and calculated from required
            page-content prompts.
          </p>
          <dl className="mt-4 grid gap-4 sm:grid-cols-4">
            <div>
              <dt className="text-xs uppercase tracking-wide text-gray-400">AI visibility</dt>
              <dd className="text-2xl font-bold">
                {formatReportScore(Number(audit.scores?.ai_visibility || 0))}
              </dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-gray-400">Brand visibility</dt>
              <dd className="text-2xl font-bold">{Math.round(visibilityPct)}%</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-gray-400">Relative SOV</dt>
              <dd className="text-2xl font-bold">{formatReportScore(sovPerformance)}</dd>
            </div>
            <div>
              <dt className="text-xs uppercase tracking-wide text-gray-400">Page citations</dt>
              <dd className="text-2xl font-bold">{citationCount}</dd>
            </div>
          </dl>
          <div className="mt-4 flex flex-wrap gap-4 text-xs text-gray-600">
            {[
              ["Chatbots", surfaceMetrics.chatbots],
              ["AI Overviews", surfaceMetrics.overviews],
            ].map(([label, raw]) => {
              const metrics = (raw ?? {}) as Record<string, unknown>;
              return (
                <span key={String(label)}>
                  <strong>{String(label)}:</strong>{" "}
                  {Number(metrics.response_count || 0) > 0
                    ? `${formatReportScore(Number(metrics.score || 0))}/100 · ${Math.round(
                        Number(metrics.visibility_pct || 0),
                      )}% visibility`
                    : "No completed responses"}
                </span>
              );
            })}
          </div>
          <p className="mt-4 text-xs text-gray-500">
            Page citability is reported separately at {formatReportScore(citability)}/100.
            Master prompt probes remain inherited and do not affect this page-specific AI
            visibility score.
          </p>
        </div>
      </section>

      <section id="citations" className="scroll-mt-16 space-y-3">
        <ScopeBanner scope="page_specific" />
        <div className="rounded-2xl border border-gray-200 bg-white p-5">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-xl font-bold text-[#0d0d0d]">Citations of this URL</h2>
            <Link to={citationsHref} className="text-xs font-semibold text-violet-700 hover:text-violet-900">
              See all site citations
            </Link>
          </div>
          {audit.citations?.length ? (
            <ul className="divide-y divide-gray-100">
              {audit.citations.map((citation, index) => (
                <li key={`${citation.url}-${index}`} className="py-2 text-sm">
                  <p className="truncate text-gray-800">{citation.url}</p>
                  <p className="text-xs text-gray-400">
                    {[citation.platform, citation.prompt].filter(Boolean).join(" · ") || "Master probe"}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-500">
              Required page probes did not cite this URL.
            </p>
          )}
        </div>
      </section>

      <section id="page-prompts" className="scroll-mt-16 space-y-3">
        <ScopeBanner scope="page_specific" />
        <PagePromptsSection
          parentId={parentId}
          pageId={pageId}
          audit={audit}
          onRefresh={async () => {
            const payload = await fetchPageAudit(parentId, pageId);
            setAudit(payload);
          }}
        />
      </section>

      {breakdown ? (
        <>
          <section id="technical-overview" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="page_specific" />
            <TechnicalOverview
              auditDirOrSlug={parentId}
              breakdown={breakdown}
              hideScoreHistory
              onNavigate={scrollToSection}
            />
          </section>
          <section id="crawler-access" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="inherited" />
            <CrawlerAccessSection
              auditDirOrSlug={parentId}
              breakdown={breakdown}
              onNavigate={scrollToSection}
            />
          </section>
          <section id="citability" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="page_specific" />
            <CitabilitySection
              auditDirOrSlug={parentId}
              breakdown={breakdown}
              onNavigate={scrollToSection}
            />
          </section>
          <section id="platform-readiness" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="inherited" />
            <PlatformReadinessSection
              auditDirOrSlug={parentId}
              breakdown={breakdown}
              pageScoped
              onNavigate={scrollToSection}
            />
          </section>
          <section id="content-overview" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="page_specific" />
            <ContentOverview
              auditDirOrSlug={parentId}
              breakdown={breakdown}
              hideScoreHistory
              onNavigate={scrollToSection}
            />
          </section>
          <section id="eeat-signals" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="page_specific" />
            <EeatSignalsSection
              auditDirOrSlug={parentId}
              contentQualityDetails={contentDetails}
            />
          </section>
          <section id="content-structure-answerability" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="page_specific" />
            <ContentStructureAnswerabilitySection
              auditDirOrSlug={parentId}
              contentQualityDetails={contentDetails}
            />
          </section>
          <section id="schema-entity-markup" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="page_specific" />
            <SchemaEntityMarkupSection
              auditDirOrSlug={parentId}
              contentQualityDetails={contentDetails}
              onNavigate={scrollToSection}
            />
          </section>
          <section id="brand-visibility-authority" className="scroll-mt-16 space-y-3">
            <ScopeBanner scope="inherited" />
            <BrandVisibilityAuthoritySection
              auditDirOrSlug={parentId}
              contentQualityDetails={contentDetails}
            />
          </section>
        </>
      ) : null}

      <div className="flex flex-wrap gap-3 text-sm">
        <Link to={masterHref} className="btn-secondary inline-flex">
          Master summary
        </Link>
        <Link to={workshopHref} className="btn-secondary inline-flex">
          Workshop
        </Link>
      </div>
    </div>
  );
}
