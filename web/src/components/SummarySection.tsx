/**
 * SummarySection — new React replacement for the report.html#summary iframe.
 *
 * Score model:
 *   AI Visibility   = 60% × visibility_pct  +  40% × relative SOV rank
 *                     (computed purely from live probe and competitor data)
 *   Technical Setup = backend AI-readiness score (citability, platform
 *                     readiness, AI search success and brand/entity signals)
 *   Content Quality = backend "content_structure" score (E-E-A-T, structure
 *                     & answerability, schema, brand visibility & authority)
 *   Overall         = 40% × AI Visibility + 30% × Technical + 30% × Content
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertCircle, ArrowRight, ExternalLink, Info, X } from "lucide-react";
import { PageLoading } from "./PageLoading";
import {
  fetchScoreBreakdown,
  fetchExecutiveSummary,
  fetchPromptSentiment,
} from "../api/client";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import { groupContentQualityComponents } from "../lib/contentQualityAreas";
import { groupTechnicalSetupComponents } from "../lib/technicalSetupAreas";
import { formatReportScore, scoreLabel, scoreTone, scoreColor } from "../lib/reportScore";
import ScoreOverTime from "./ScoreOverTime";
import { platformScoreColor } from "../lib/platformScoreColor";
import {
  computeVisibilityMetrics,
  visibilityPlatformsWithResults,
} from "../lib/visibilityMetrics";
import { PlatformLogo, PLATFORM_META } from "./PlatformLogo";
import { ViewportOverlay } from "./ViewportOverlay";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ScoreBreakdown {
  ai_visibility: number | null;
  technical_setup: number | null;
  content_structure: number | null;
  overall: number | null;
  prompt_metrics?: {
    score: number;
    visibility_pct: number;
    sov_pct: number;
    sov_performance_score: number;
    sov_rank: number | null;
    competitor_count: number;
    top_competitor_sov_pct: number;
    average_competitor_sov_pct: number;
    visible_prompt_count: number;
    prompt_count: number;
    brand_hits: number;
    competitor_hits: number;
  } | null;
  details?: Record<string, {
    score: number;
    components: ScoreComponent[];
  }>;
}

interface ScoreComponent {
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

function conciseFinding(text: string, maxLength = 125): string {
  const cleaned = text.replace(/\s+/g, " ").trim().replace(/[.;:]$/, "");
  return cleaned.length <= maxLength ? cleaned : `${cleaned.slice(0, maxLength - 1).trimEnd()}…`;
}

function pillarFindingSummary(components: ScoreComponent[], fallback: string): string {
  if (!components.length) return fallback;
  const ranked = [...components].sort((left, right) => left.score - right.score);
  const weakest = ranked[0];
  const strongest = ranked[ranked.length - 1];
  const strength = strongest.strengths?.[0]
    || (strongest.score >= 60 ? strongest.finding_summary : "");
  const improvement = weakest.improvements?.[0]
    || (weakest.score < 75 ? weakest.finding_summary : "");
  const parts: string[] = [];
  if (strength) parts.push(`${strongest.title}: ${conciseFinding(strength)}`);
  if (improvement && (weakest.key !== strongest.key || !strength)) {
    parts.push(`${weakest.title} needs work: ${conciseFinding(improvement)}`);
  }
  return parts.length ? `${parts.join(". ")}.` : fallback;
}

interface ExecSummary {
  paragraph_html?: string;
  key_findings?: string[];
}

// ── Tooltip (portal-based to avoid overflow clipping) ─────────────────────────

function Tooltip({ text }: { text: string }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const ref = useRef<HTMLButtonElement>(null);
  const show = () => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      setPos({ x: r.left + r.width / 2, y: r.bottom + 6 });
    }
  };
  return (
    <button
      type="button"
      ref={ref}
      className="ml-1 inline-flex items-center cursor-help align-middle"
      aria-label={text}
      onMouseEnter={show}
      onMouseLeave={() => setPos(null)}
      onFocus={show}
      onBlur={() => setPos(null)}
    >
      <Info className="w-3.5 h-3.5 text-gray-300" />
      {pos && createPortal(
        <span
          className="fixed w-64 rounded-xl bg-gray-900 text-white text-xs px-3 py-2.5 leading-relaxed z-[9999] shadow-xl pointer-events-none"
          style={{ left: pos.x, top: pos.y, transform: "translateX(-50%)" }}
        >
          {text}
        </span>,
        document.body,
      )}
    </button>
  );
}

// ── Score gauge ────────────────────────────────────────────────────────────────

function ScoreGauge({ score, size = 96 }: { score: number; size?: number }) {
  const r = (size - 12) / 2;
  const circ = 2 * Math.PI * r;
  const dash = (Math.min(100, Math.max(0, score)) / 100) * circ;
  const tone = scoreTone(score);
  const color = scoreColor(tone);
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e5e7eb" strokeWidth={10} />
      <circle
        cx={size / 2} cy={size / 2} r={r}
        fill="none" stroke={color} strokeWidth={10}
        strokeDasharray={`${dash} ${circ - dash}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text
        x="50%" y="50%"
        dominantBaseline="middle" textAnchor="middle"
        fontSize={size * 0.22} fontWeight="700" fill="#0d0d0d"
      >
        {formatReportScore(score)}
      </text>
    </svg>
  );
}

// ── Pillar card ────────────────────────────────────────────────────────────────

function PillarCard({
  label,
  score,
  description,
  weight,
  onExplain,
}: {
  label: string;
  score: number | null;
  description?: string;
  weight: number;
  onExplain: () => void;
}) {
  if (score === null) {
    return (
      <div className="bg-white rounded-2xl border border-gray-200 p-5 flex flex-col gap-3">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">{label}</span>
          <span className="ml-auto text-[10px] text-gray-300">Weight: {weight}%</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center">
            <span className="text-xs text-gray-300 font-bold">—</span>
          </div>
          <div>
            <p className="text-xs text-gray-400">Not yet available</p>
          </div>
        </div>
        {description && <p className="text-xs text-gray-400 leading-snug">{description}</p>}
        <button type="button" onClick={onExplain} className="mt-auto text-left text-[11px] font-semibold text-violet-700 hover:text-violet-900">
          How is this score calculated?
        </button>
      </div>
    );
  }

  const tone = scoreTone(score);
  const color = scoreColor(tone);
  const label_ = scoreLabel(score);
  return (
    <div className="bg-white rounded-2xl border border-gray-200 p-5 flex flex-col gap-3">
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">{label}</span>
        <span className="ml-auto text-[10px] text-gray-300">Weight: {weight}%</span>
      </div>
      <div className="flex items-center gap-4">
        <ScoreGauge score={score} size={72} />
        <div>
          <p className="text-2xl font-bold text-[#0d0d0d] leading-none">{formatReportScore(score)}</p>
          <p className="text-xs font-semibold mt-0.5" style={{ color }}>{label_}</p>
        </div>
      </div>
      {description && <p className="text-xs text-gray-400 leading-snug">{description}</p>}
      {/* Progress bar */}
      <div className="h-1.5 bg-gray-100 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${score}%`, background: color }}
        />
      </div>
      <button type="button" onClick={onExplain} className="mt-auto text-left text-[11px] font-semibold text-violet-700 hover:text-violet-900">
        How is this score calculated?
      </button>
    </div>
  );
}

function ScoreCalculationOverlay({
  label,
  score,
  components,
  onNavigate,
  onClose,
}: {
  label: string;
  score: number | null;
  components: ScoreComponent[];
  onNavigate?: (section: string) => void;
  onClose: () => void;
}) {
  return (
    <ViewportOverlay onClose={onClose} labelledBy="score-calculation-title">
      <div className="bg-white rounded-2xl w-full max-w-2xl max-h-[min(86vh,900px)] flex flex-col shadow-2xl overflow-hidden">
        <div className="flex items-start gap-4 px-6 py-4 border-b border-gray-100 bg-gray-50">
          <div className="flex-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Score calculation</p>
            <h3 id="score-calculation-title" className="text-base font-bold text-[#0d0d0d]">{label}</h3>
          </div>
          <div className="text-right">
            <p className="text-2xl font-bold text-[#0d0d0d]">{score === null ? "—" : formatReportScore(score)}</p>
            <p className="text-[10px] text-gray-400">Current site score</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close score calculation" className="w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="overflow-y-auto px-6 py-5">
          <p className="text-sm text-gray-600 mb-4">
            The score is the weighted total of the components below. Each component is scored from 0 to 100 using this site’s audit or live-probe evidence.
          </p>
          {!!components.length && (
            <p className="mb-4 rounded-lg bg-gray-50 px-3 py-2 text-xs leading-relaxed text-gray-500">
              Current calculation: {components.map((component) => `${component.weight_pct}% × ${component.title} (${formatReportScore(component.score)})`).join(" + ")}.
            </p>
          )}
          <div className="divide-y divide-gray-100 border-y border-gray-100">
            {components.map((component) => (
              <div key={component.key} className="py-3.5">
                <div className="flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold text-[#0d0d0d]">{component.title}</p>
                    <p className="text-[11px] text-gray-400 mt-0.5 leading-relaxed">{component.detail}</p>
                    {component.finding_summary && (
                      <div className="mt-2 rounded-lg bg-violet-50 px-3 py-2">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-violet-700">What we found</p>
                        <p className="mt-0.5 text-xs leading-relaxed text-gray-700">{component.finding_summary}</p>
                      </div>
                    )}
                    {component.evidence_example && (
                      <div className="mt-2 rounded-lg border border-gray-100 px-3 py-2">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Example evidence</p>
                        <p className="mt-0.5 text-xs leading-relaxed text-gray-600">{component.evidence_example}</p>
                      </div>
                    )}
                    {!!component.site_examples?.length && (
                      <div className="mt-2 space-y-2">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Examples from this site</p>
                        {component.site_examples.map((example) => (
                          <div key={`${component.key}-${example.url}`} className="rounded-lg border border-gray-100 px-3 py-2.5">
                            <div className="flex items-start justify-between gap-3">
                              <p className="text-xs font-semibold text-gray-800">{example.title}</p>
                              {example.url && (
                                <a
                                  href={example.url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="inline-flex shrink-0 items-center gap-1 text-[10px] font-semibold text-violet-700 hover:text-violet-900"
                                >
                                  Open page <ExternalLink className="h-3 w-3" />
                                </a>
                              )}
                            </div>
                            {example.excerpt && (
                              <blockquote className="mt-1.5 text-xs leading-relaxed text-gray-600">“{example.excerpt}”</blockquote>
                            )}
                            <p className="mt-1.5 text-[10px] leading-relaxed text-gray-400">{example.context}</p>
                          </div>
                        ))}
                      </div>
                    )}
                    {component.report_section && onNavigate && (
                      <button
                        type="button"
                        onClick={() => {
                          onClose();
                          onNavigate(component.report_section!);
                        }}
                        className="mt-2 inline-flex items-center gap-1 text-[11px] font-semibold text-violet-700 hover:text-violet-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-400 focus-visible:ring-offset-2 rounded"
                      >
                        View detailed report <ArrowRight className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                  <div className="text-right shrink-0">
                    <p className="text-sm font-bold text-[#0d0d0d]">{formatReportScore(component.score)}</p>
                    <p className="text-[10px] text-gray-400">{component.weight_pct}% weight</p>
                  </div>
                </div>
                <div className="mt-2 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, component.score))}%`, background: platformScoreColor(component.score) }} />
                </div>
              </div>
            ))}
          </div>
          {components.length === 0 && <p className="text-sm text-gray-400 italic">Calculation detail is not available for this audit.</p>}
        </div>
      </div>
    </ViewportOverlay>
  );
}

// ── Platform summary mini-bars ─────────────────────────────────────────────────

function PlatformMiniRow({
  platform,
  brandPct,
  totalPrompts,
}: {
  platform: string;
  brandPct: number;
  totalPrompts: number;
}) {
  const meta = PLATFORM_META[platform];
  const color = platformScoreColor(brandPct);
  return (
    <div className="flex items-center gap-3">
      <span className="flex items-center gap-2 text-xs font-medium text-[#0d0d0d] w-32 shrink-0">
        <PlatformLogo platform={platform} size={18} />
        {meta?.label ?? platform}
      </span>
      <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.min(brandPct, 100)}%`, background: color }}
        />
      </div>
      <span className="text-xs text-gray-400 w-10 text-right">{Math.round(brandPct)}%</span>
      <span className="text-[10px] text-gray-300">/ {totalPrompts} prompts</span>
    </div>
  );
}

// ── Sentiment badge (Gemini qualitative — same source as Overview AI Sentiment) ─

const GEMINI_SENTIMENT_COLORS: Record<string, { fg: string; bg: string }> = {
  Positive: { fg: "#00b894", bg: "#e8f8f5" },
  Mixed: { fg: "#e17055", bg: "#fdf0ed" },
  Negative: { fg: "#d63031", bg: "#ffeaea" },
  Neutral: { fg: "#636e72", bg: "#f0f0f0" },
};

function OverallSentimentBadge({
  auditDirOrSlug,
  hasProbe,
}: {
  auditDirOrSlug: string;
  hasProbe: boolean;
}) {
  const [label, setLabel] = useState<string | null>(null);

  useEffect(() => {
    if (!hasProbe) {
      setLabel(null);
      return;
    }
    let cancelled = false;
    fetchPromptSentiment(auditDirOrSlug)
      .then((r) => {
        if (cancelled) return;
        const overall = r.sentiment?.overall_sentiment?.trim();
        setLabel(overall || null);
      })
      .catch(() => {
        if (!cancelled) setLabel(null);
      });
    return () => {
      cancelled = true;
    };
  }, [auditDirOrSlug, hasProbe]);

  if (!label) return null;

  const { fg, bg } = GEMINI_SENTIMENT_COLORS[label] ?? GEMINI_SENTIMENT_COLORS.Neutral;
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-400">Sentiment</span>
      <span
        className="text-xs font-semibold px-2.5 py-0.5 rounded-full"
        style={{ background: bg, color: fg }}
        title="Gemini qualitative sentiment (same as AI Visibility Overview)"
      >
        {label}
      </span>
    </div>
  );
}

function synchronizeExecutiveScore(html: string, score: number): string {
  const formatted = formatReportScore(score);
  const explicitlySynced = html.replace(
    /((?:overall\s+(?:GEO\s+)?score|GEO\s+readiness\s+score|GEO\s+score)(?:\s+(?:of|is|at))?\s*(?:<strong>)?)(\d{1,3}(?:\.\d+)?)/gi,
    (_match, prefix: string) => `${prefix}${formatted}`,
  );
  return explicitlySynced.replace(
    /((?:<strong>)?)(\d{1,3}(?:\.\d+)?)(\s*(?:\/\s*100|out of 100))/i,
    (_match, opening: string, _oldScore: string, suffix: string) => `${opening}${formatted}${suffix}`,
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

export function SummarySection({
  auditDirOrSlug,
  fallbackOverallScore,
  onNavigate,
}: {
  auditDirOrSlug: string;
  fallbackOverallScore?: number | null;
  onNavigate?: (section: string) => void;
}) {
  const { ctx, loading: ctxLoading } = usePromptPerformanceContext(auditDirOrSlug);
  const [breakdown, setBreakdown] = useState<ScoreBreakdown | null>(null);
  const [exec, setExec] = useState<ExecSummary | null>(null);
  const [extraLoading, setExtraLoading] = useState(true);
  const [calculationPillar, setCalculationPillar] = useState<string | null>(null);

  const load = useCallback(() => {
    setExtraLoading(true);
    Promise.all([
      fetchScoreBreakdown(auditDirOrSlug).catch(() => null),
      fetchExecutiveSummary(auditDirOrSlug).catch(() => null),
    ]).then(([b, e]) => {
      setBreakdown(b);
      setExec(e);
    }).finally(() => setExtraLoading(false));
  }, [auditDirOrSlug]);

  useEffect(() => { load(); }, [load]);

  const loading = ctxLoading || extraLoading;

  if (loading) {
    return <PageLoading />;
  }

  // ── Compute scores ──────────────────────────────────────────────────────────
  const aiVisibilityMetrics = ctx ? computeVisibilityMetrics(ctx) : null;
  const promptMetrics = breakdown?.prompt_metrics ?? ctx?.overall_metrics ?? null;
  const aiVisibilityScore = breakdown?.ai_visibility ?? promptMetrics?.score ?? aiVisibilityMetrics?.score ?? null;
  const technicalScore = breakdown?.technical_setup ?? null;
  const contentScore = breakdown?.content_structure ?? null;
  const overallScore = breakdown?.overall ?? fallbackOverallScore ?? null;

  const hasProbe = !!ctx?.live_probe?.per_prompt?.length || !!promptMetrics;
  const perPlatform = aiVisibilityMetrics?.perPlatform ?? {};
  const brandName = ctx?.brand_name || "Your brand";
  const visibilityPct = promptMetrics?.visibility_pct ?? aiVisibilityMetrics?.visibilityPct ?? 0;
  const visiblePromptCount = promptMetrics?.visible_prompt_count ?? aiVisibilityMetrics?.visiblePromptCount ?? 0;
  const promptCount = promptMetrics?.prompt_count ?? aiVisibilityMetrics?.promptCount ?? 0;
  const sovPct = promptMetrics?.sov_pct ?? aiVisibilityMetrics?.sovPct ?? 0;
  const sovPerformanceScore = promptMetrics?.sov_performance_score ?? aiVisibilityMetrics?.sovPerformanceScore ?? 0;
  const sovRank = promptMetrics?.sov_rank ?? aiVisibilityMetrics?.sovRank ?? null;
  const competitorCount = promptMetrics?.competitor_count ?? aiVisibilityMetrics?.competitorCount ?? 0;
  const topCompetitorSovPct = promptMetrics?.top_competitor_sov_pct ?? aiVisibilityMetrics?.topCompetitorSovPct ?? 0;
  const averageCompetitorSovPct = promptMetrics?.average_competitor_sov_pct ?? aiVisibilityMetrics?.averageCompetitorSovPct ?? 0;
  const hasVisibilityMetrics = !!promptMetrics || !!aiVisibilityMetrics;
  const calculationComponents: Record<string, ScoreComponent[]> = {
    "AI Visibility": hasVisibilityMetrics ? [
      {
        key: "brand_visibility",
        title: "Brand visibility",
        score: visibilityPct,
        weight_pct: 60,
        detail: "Platform responses mentioning the brand divided by all platform responses analysed.",
        finding_summary: `${brandName} appeared in ${visiblePromptCount} of ${promptCount} analysed platform responses.`,
        evidence_example: `${visibilityPct.toFixed(1)}% response-level visibility across the tested AI platforms.`,
        report_section: "ai-visibility-overview",
      },
      {
        key: "share_of_voice",
        title: "Share of voice performance",
        score: sovPerformanceScore,
        weight_pct: 40,
        detail: "Relative rank against the 10 most-mentioned website-backed competitors. Incidental names without website evidence are excluded.",
        finding_summary: sovRank === null
          ? `${brandName} and competitors had no measurable share of voice in the tested responses.`
          : `${brandName} ranks #${sovRank} against the ${competitorCount} most-mentioned competitors, producing a ${formatReportScore(sovPerformanceScore)}/100 relative SOV score.`,
        evidence_example: `${brandName}: ${sovPct.toFixed(1)}% raw SOV. Top competitor: ${topCompetitorSovPct.toFixed(1)}%; average competitor: ${averageCompetitorSovPct.toFixed(1)}%.`,
        report_section: "competitor-visibility",
      },
    ] : [],
    "Technical Setup": groupTechnicalSetupComponents(
      breakdown?.details?.technical_setup?.components ?? [],
    ),
    "Content Quality": groupContentQualityComponents(
      breakdown?.details?.content_structure?.components ?? [],
    ),
  };
  const calculationScores: Record<string, number | null> = {
    "AI Visibility": aiVisibilityScore,
    "Technical Setup": technicalScore,
    "Content Quality": contentScore,
  };
  const technicalComponents = calculationComponents["Technical Setup"];
  const contentComponents = calculationComponents["Content Quality"];
  const technicalSummary = pillarFindingSummary(
    technicalComponents,
    "Technical findings are not available for this audit.",
  );
  const contentSummary = pillarFindingSummary(
    contentComponents,
    "Content-quality findings are not available for this audit.",
  );

  return (
    <div className="space-y-6">
      {/* ── Header: overall score + title ── */}
      <div className="bg-white rounded-2xl border border-gray-200 p-6 flex flex-col lg:flex-row lg:items-center gap-6">
        {overallScore !== null ? (
          <>
            <div className="flex items-center gap-5 shrink-0">
            <ScoreGauge score={overallScore} size={100} />
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-0.5">
                  Overall GEO Score
                </p>
                <p className="text-3xl font-bold text-[#0d0d0d] leading-none mb-1">
                  {formatReportScore(overallScore)}
                  <span className="text-base font-normal text-gray-400 ml-1">/100</span>
                </p>
                <p className="text-sm font-semibold" style={{ color: scoreColor(scoreTone(overallScore)) }}>
                  {scoreLabel(overallScore)}
                </p>
                <div className="mt-2">
                  <OverallSentimentBadge
                    auditDirOrSlug={auditDirOrSlug}
                    hasProbe={!!ctx?.live_probe?.per_prompt?.length}
                  />
                </div>
              </div>
            </div>
            {exec?.paragraph_html && (
              <div className="flex-1 lg:border-l lg:border-gray-100 lg:pl-6">
                <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1.5">Executive summary</p>
                <div
                  className="prose prose-sm max-w-none text-gray-600 leading-relaxed"
                  dangerouslySetInnerHTML={{ __html: synchronizeExecutiveScore(exec.paragraph_html, overallScore) }}
                />
              </div>
            )}
          </>
        ) : (
          <div className="flex items-center gap-3 text-gray-400">
            <AlertCircle className="w-6 h-6" />
            <p className="text-sm">
              Run the audit and probes to see GEO scores.
            </p>
          </div>
        )}
      </div>

      {/* ── Three pillar cards ── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <PillarCard
          label="AI Visibility"
          score={aiVisibilityScore}
          weight={40}
          description={
            hasProbe && hasVisibilityMetrics
              ? `${brandName} is mentioned in ${Math.round(visibilityPct)}% of analysed responses across ${Object.values(perPlatform).filter((metrics) => metrics.responseCount > 0).length} platforms.`
              : "Run probes to calculate AI Visibility score."
          }
          onExplain={() => setCalculationPillar("AI Visibility")}
        />
        <PillarCard
          label="Technical Setup"
          score={technicalScore}
          weight={30}
          description={technicalSummary}
          onExplain={() => setCalculationPillar("Technical Setup")}
        />
        <PillarCard
          label="Content Quality"
          score={contentScore}
          weight={30}
          description={contentSummary}
          onExplain={() => setCalculationPillar("Content Quality")}
        />
      </div>

      <ScoreOverTime
        auditId={auditDirOrSlug}
        brandLabel={brandName}
        metrics={["ai_visibility", "technical_setup", "content_structure"]}
        hideCompetitors
        title="Scores over time"
        description="Your brand’s AI Visibility, Technical Setup, and Content Quality from automated and manual refreshes."
      />

      {/* ── Per-platform visibility (if probes run) ── */}
      {hasProbe && visibilityPlatformsWithResults(perPlatform).length > 0 && (
        <div className="bg-white rounded-2xl border border-gray-200 p-5">
          <h3 className="text-sm font-bold text-[#0d0d0d] mb-4 inline-flex items-center">
            Brand visibility by AI platform
            <Tooltip text="Visibility is the percentage of tested prompts where the platform's response mentions the brand. Each bar uses the same violet scale, with a deeper shade indicating a higher score." />
          </h3>
          <div className="space-y-3">
            {visibilityPlatformsWithResults(perPlatform).map((plat) => {
              const metrics = perPlatform[plat];
              return (
                <PlatformMiniRow
                  key={plat}
                  platform={plat}
                  brandPct={metrics.visibilityPct}
                  totalPrompts={metrics.responseCount}
                />
              );
            })}
          </div>
        </div>
      )}

      {/* ── Key findings ── */}
      {exec?.key_findings && exec.key_findings.length > 0 && (
        <div className="bg-white rounded-2xl border border-gray-200 p-5">
          <h3 className="text-sm font-bold text-[#0d0d0d] mb-3">Key Findings</h3>
          <ul className="space-y-2">
            {exec.key_findings.map((f, i) => (
              <li key={i} className="flex items-start gap-2.5 text-sm text-gray-600">
                <span className="mt-0.5 w-1.5 h-1.5 rounded-full bg-brand-accent shrink-0 mt-1.5" />
                {f}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Score methodology note */}
      <p className="text-[11px] text-gray-300 text-center pb-2">
        AI Visibility is calculated from live probe data (60% brand visibility + 40% competitor-relative SOV performance).
        Technical Setup and Content Quality are generated by the GEO audit engine.
        Overall = 40% AI Visibility + 30% Technical + 30% Content.
      </p>
      {calculationPillar && (
        <ScoreCalculationOverlay
          label={calculationPillar}
          score={calculationScores[calculationPillar] ?? null}
          components={calculationComponents[calculationPillar] ?? []}
          onNavigate={onNavigate}
          onClose={() => setCalculationPillar(null)}
        />
      )}
    </div>
  );
}
