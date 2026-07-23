import { useEffect, useMemo, useState } from "react";
import { TrendingUp, Eye, Smile, Award } from "lucide-react";
import { fetchPromptSentiment, fetchScoreBreakdown } from "../api/client";
import { PageLoading } from "./PageLoading";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import type { PromptSentimentAnalysis, TopCitedSite } from "../types";
import { PlatformLogo, PLATFORM_META } from "./PlatformLogo";
import { computeOverallSentiment, sentimentFromKeywordAggregate } from "../lib/sentimentCalc";
import {
  buildGeminiPromptSentimentMap,
  geminiSentimentForPrompt,
  type GeminiSentimentLabel,
} from "../lib/geminiPromptSentiment";
import { platformScoreColor } from "../lib/platformScoreColor";
import { computeVisibilityMetrics, PRIMARY_VISIBILITY_PLATFORMS } from "../lib/visibilityMetrics";
import { normalizePromptLocales, type PromptLocale } from "../lib/promptLocales";
import VisibilityOverTime from "./VisibilityOverTime";
import CitationOverTime from "./CitationOverTime";
import SentimentOverTime from "./SentimentOverTime";
import {
  BrandCompetitorVisibility,
  BrandCompetitorVisibilityTable,
} from "./BrandCompetitorVisibility";
import { buildInformationSourceDomainPredicate } from "../lib/citationSource";
import { PromptLocaleFilter, liveProbeForLocale } from "./PromptLocaleFilter";
import { OVERALL_LOCALE_KEY } from "../lib/localeProbeView";
import { preferredInitialLocaleKey } from "../lib/defaultLocaleView";
import { DeferredChart } from "./DeferredChart";
import { prefetchProbeHistory } from "../lib/probeHistoryFetch";

interface AiVisibilityOverviewProps {
  auditDirOrSlug: string;
}


function pct(val: number | undefined): string {
  if (val == null) return "—";
  return `${val.toFixed(1)}%`;
}

function ScoreCard({
  icon: Icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: string;
  color: string;
}) {
  return (
    <div className="h-full bg-white rounded-xl border border-gray-200 p-5">
      <div className="flex items-center gap-2 mb-3">
        <span
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg"
          style={{ background: color + "18" }}
        >
          <Icon className="w-4 h-4" style={{ color }} />
        </span>
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">{label}</span>
      </div>
      <p className="text-3xl font-bold text-[#0d0d0d] leading-none mb-1">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function PlatformBar({
  platform,
  visibilityPct,
  sovPct,
}: {
  platform: string;
  visibilityPct: number;
  sovPct: number;
}) {
  const meta = PLATFORM_META[platform];
  const color = meta?.color ?? "#4B5563";
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <div className="flex items-center gap-1.5">
          <PlatformLogo platform={platform} size={16} />
          <span className="font-medium text-[#0d0d0d]">{meta?.label ?? platform}</span>
        </div>
        <span className="text-gray-400">{pct(visibilityPct)} visibility · {pct(sovPct)} SOV</span>
      </div>
      <div className="flex gap-1 h-2 rounded-full overflow-hidden bg-gray-100">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${Math.min(visibilityPct, 100)}%`, background: color }}
        />
      </div>
    </div>
  );
}

// ── Sentiment helpers ─────────────────────────────────────────────────────────

const SENTIMENT_COLORS: Record<string, { fg: string; bg: string }> = {
  Positive: { fg: "#00b894", bg: "#e8f8f5" },
  positive: { fg: "#00b894", bg: "#e8f8f5" },
  Mixed: { fg: "#e17055", bg: "#fdf0ed" },
  Negative: { fg: "#d63031", bg: "#ffeaea" },
  negative: { fg: "#d63031", bg: "#ffeaea" },
  Neutral: { fg: "#636e72", bg: "#f0f0f0" },
  neutral: { fg: "#636e72", bg: "#f0f0f0" },
};

function SentimentBadge({ sentiment }: { sentiment: GeminiSentimentLabel | string | null }) {
  if (!sentiment) return <span className="text-xs text-gray-300 font-bold">—</span>;
  const s = String(sentiment).trim().toLowerCase();
  if (s === "positive") {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-emerald-100 text-emerald-600 text-sm font-bold" title="Positive">+</span>
    );
  }
  if (s === "negative") {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-red-100 text-red-500 text-sm font-bold" title="Negative">−</span>
    );
  }
  if (s === "mixed") {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-orange-100 text-orange-600 text-sm font-bold" title="Mixed">±</span>
    );
  }
  return (
    <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-gray-100 text-gray-400 text-sm font-bold" title="Neutral">~</span>
  );
}

function OverallSentimentSummary({ sentiment }: { sentiment: PromptSentimentAnalysis }) {
  const { fg, bg } = SENTIMENT_COLORS[sentiment.overall_sentiment] ?? SENTIMENT_COLORS.Neutral;
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <div className="flex items-center gap-3 mb-3">
        <Smile className="w-4 h-4" style={{ color: fg }} />
        <span className="text-sm font-semibold text-[#0d0d0d]">AI Sentiment</span>
        <span
          className="inline-block px-2.5 py-0.5 rounded-full text-xs font-bold"
          style={{ background: bg, color: fg }}
        >
          {sentiment.overall_sentiment}
        </span>
      </div>
      <p className="text-sm text-gray-500 leading-relaxed">{sentiment.overall_summary}</p>
      {sentiment.by_category?.length > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-100 space-y-2">
          {sentiment.by_category.map((cat) => {
            const c = SENTIMENT_COLORS[cat.sentiment] ?? SENTIMENT_COLORS.Neutral;
            return (
              <div key={cat.category} className="flex items-center justify-between gap-3">
                <span className="text-xs text-gray-600 font-medium">{cat.category}</span>
                <span
                  className="text-xs font-bold px-2 py-0.5 rounded-full shrink-0"
                  style={{ background: c.bg, color: c.fg }}
                >
                  {cat.sentiment}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function TopDomainsTable({ sites }: { sites: TopCitedSite[] }) {
  if (!sites.length) return null;
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100">
        <h3 className="text-sm font-semibold text-[#0d0d0d]">Top cited domains</h3>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50">
            <th className="px-6 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Domain</th>
            <th className="px-6 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-400">Citations</th>
            <th className="px-6 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Platforms</th>
          </tr>
        </thead>
        <tbody>
          {sites.slice(0, 8).map((s) => (
            <tr key={s.domain} className="border-b border-gray-50 last:border-0">
              <td className="px-6 py-3 flex items-center gap-2">
                <img
                  src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(s.domain)}&sz=32`}
                  alt=""
                  width={16}
                  height={16}
                  className="rounded shrink-0 opacity-80"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = "none";
                  }}
                />
                <span className="font-medium text-[#0d0d0d]">{s.domain}</span>
              </td>
              <td className="px-6 py-3 text-right tabular-nums font-semibold text-[#0d0d0d]">
                {s.count}
              </td>
              <td className="px-6 py-3">
                <div className="flex items-center gap-1.5 flex-wrap">
                  {(s.platforms ?? []).map((p) => (
                    <PlatformLogo key={p} platform={p} size={18} />
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AiVisibilityOverview({ auditDirOrSlug }: AiVisibilityOverviewProps) {
  const { ctx, loading: ctxLoading, error, ensureScope } = usePromptPerformanceContext(auditDirOrSlug);
  const [llmSentiment, setLlmSentiment] = useState<PromptSentimentAnalysis | null>(null);
  const [scoreBreakdown, setScoreBreakdown] = useState<Awaited<ReturnType<typeof fetchScoreBreakdown>> | null>(null);
  const [selectedLocaleKey, setSelectedLocaleKey] = useState<string | null>(null);

  useEffect(() => {
    fetchScoreBreakdown(auditDirOrSlug)
      .then((scores) => setScoreBreakdown(scores))
      .catch(() => setScoreBreakdown(null));
  }, [auditDirOrSlug]);

  useEffect(() => {
    if (!auditDirOrSlug) return;
    // Warm probe-history after first paint so over-time charts share one fetch.
    const ric = window.requestIdleCallback?.bind(window);
    if (ric) {
      const id = ric(() => prefetchProbeHistory(auditDirOrSlug), { timeout: 500 });
      return () => window.cancelIdleCallback?.(id);
    }
    const timer = window.setTimeout(() => prefetchProbeHistory(auditDirOrSlug), 0);
    return () => window.clearTimeout(timer);
  }, [auditDirOrSlug]);

  useEffect(() => {
    setSelectedLocaleKey(null);
  }, [auditDirOrSlug]);

  useEffect(() => {
    if (!ctx) return;
    setSelectedLocaleKey((prev) => (prev == null ? preferredInitialLocaleKey(ctx) : prev));
    if (!ctx.live_probe?.per_prompt?.length) return;
    let cancelled = false;
    let timer: number | undefined;
    const load = () => {
      fetchPromptSentiment(auditDirOrSlug)
        .then((r) => {
          if (cancelled) return;
          if (r.sentiment) {
            setLlmSentiment(r.sentiment);
            return;
          }
          if (r.status && ["queued", "starting", "running"].includes(r.status)) {
            timer = window.setTimeout(load, 4000);
          }
        })
        .catch(() => {
          if (!cancelled) setLlmSentiment(null);
        });
    };
    load();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [auditDirOrSlug, ctx]);

  const geminiByPromptId = useMemo(
    () => buildGeminiPromptSentimentMap(llmSentiment),
    [llmSentiment],
  );

  const onLocaleChange = (key: string) => {
    setSelectedLocaleKey(key);
    void ensureScope(
      key === OVERALL_LOCALE_KEY ? { allLocales: true } : { locale: key },
    ).catch(() => undefined);
  };

  // Don't block first paint on score-breakdown — probe metrics are preferred.
  const loading = ctxLoading;

  const localeKey = selectedLocaleKey || OVERALL_LOCALE_KEY;
  const live = liveProbeForLocale(ctx, localeKey);
  const localeCtx = useMemo(() => {
    if (!ctx) return null;
    return { ...ctx, live_probe: live };
  }, [ctx, live]);

  if (loading) {
    return <PageLoading />;
  }

  if (error) {
    return <div className="alert-error m-6">{error}</div>;
  }

  const agg = live?.aggregate;
  const perPrompt = live?.per_prompt ?? [];
  const isInformationSourceDomain = buildInformationSourceDomainPredicate(localeCtx);
  const topSites = (live?.top_cited_sites ?? []).filter((site) =>
    isInformationSourceDomain(site.domain)
  );

  const platforms = PRIMARY_VISIBILITY_PLATFORMS;

  const brandTokens = live?.brand_match_tokens ?? [];

  const visibilityMetrics = localeCtx ? computeVisibilityMetrics(localeCtx) : null;
  // Prefer locale-scoped probe metrics so scorecards match the Brand & competitor
  // table on this page (same ctx / same SOV definition).
  const visibilityScore = visibilityMetrics?.visibilityPct ?? scoreBreakdown?.prompt_metrics?.visibility_pct ?? null;
  const sovPct = visibilityMetrics?.sovPct ?? scoreBreakdown?.prompt_metrics?.sov_pct ?? null;
  const aiVisibilityScore = visibilityMetrics?.score ?? scoreBreakdown?.ai_visibility ?? null;
  const brandPctCount = visibilityMetrics
    ? platforms.filter((platform) => visibilityMetrics.perPlatform[platform]?.responseCount > 0).length
    : 0;

  const filterLocales = normalizePromptLocales(
    ctx?.prompt_locales as PromptLocale[] | undefined,
    ctx?.primary_market?.country ?? "",
    ctx?.primary_market?.country_id ?? "",
  );

  // Sentiment score from keyword analysis (prefer server precompute when replies omitted)
  // eslint-disable-next-line react-hooks/rules-of-hooks — sentimentResult must be computed unconditionally
  const sentimentResult =
    sentimentFromKeywordAggregate(live?.keyword_sentiment)
    ?? computeOverallSentiment(
      perPrompt as Array<Record<string, unknown>>,
      brandTokens,
      Array.from(platforms),
    );
  // Average rank: how often brand is mentioned first vs later in responses
  let avgPosition = 0;
  let posCount = 0;
  perPrompt.forEach((row) => {
    const precomputed = (row as { list_metrics?: { avg_position?: number | null } }).list_metrics?.avg_position;
    if (precomputed != null && Number.isFinite(Number(precomputed))) {
      avgPosition += Number(precomputed);
      posCount++;
      return;
    }
    platforms.forEach((p) => {
      const resp = row[`${p}_response` as keyof typeof row] as string | undefined;
      if (!resp || typeof resp !== "string") return;
      const tokens = live?.brand_match_tokens ?? [];
      if (!tokens.length) return;
      const lower = resp.toLowerCase();
      for (const tok of tokens) {
        const idx = lower.indexOf(tok.toLowerCase());
        if (idx >= 0) {
          avgPosition += (idx / resp.length) * 10 + 1;
          posCount++;
          break;
        }
      }
    });
  });
  const avgPos = posCount > 0 ? avgPosition / posCount : null;

  const hasData = agg != null || perPrompt.length > 0;

  if (!hasData) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] text-center px-6">
        <Eye className="w-10 h-10 text-gray-300 mb-3" />
        <p className="text-sm text-gray-400">No prompt probe data yet. Run prompt probes to see your AI visibility.</p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-[#0d0d0d] mb-1">AI visibility overview</h2>
        <p className="text-sm text-gray-400">
          Aggregated across {perPrompt.length} prompt{perPrompt.length !== 1 ? "s" : ""} and {brandPctCount} platform{brandPctCount !== 1 ? "s" : ""}.
        </p>
      </div>

      <PromptLocaleFilter
        locales={filterLocales}
        selectedKey={localeKey}
        onChange={onLocaleChange}
        hideIfSingle={false}
        ctx={ctx}
      />

      {localeKey !== OVERALL_LOCALE_KEY && !live?.per_prompt?.length ? (
        <div className="alert-info">
          No probe data for this market/language yet. Re-run failed markets from the Prompts section to fill it in.
        </div>
      ) : (
        <>
      {aiVisibilityScore != null && (
        <div className="rounded-2xl border border-gray-200 bg-white p-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
            <div
              className="flex h-20 w-20 shrink-0 items-center justify-center rounded-full border-[8px] text-xl font-bold"
              style={{
                borderColor: `${platformScoreColor(aiVisibilityScore)}33`,
                color: platformScoreColor(aiVisibilityScore),
              }}
            >
              {Math.round(aiVisibilityScore)}
            </div>
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">AI Visibility Score</p>
              <p className="mt-1 text-sm leading-relaxed text-gray-600">
                {ctx?.brand_name || "The brand"} appears in {(visibilityScore ?? 0).toFixed(1)}% of analysed platform responses
                and has {(sovPct ?? 0).toFixed(1)}% raw share of voice.
              </p>
              <p className="mt-1 text-xs text-gray-400">
                60% response-level visibility + 40% share-of-voice performance against the 10 most-mentioned competitors.
              </p>
            </div>
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <ScoreCard
          icon={Eye}
          label="Visibility"
          value={visibilityScore != null ? pct(visibilityScore) : "—"}
          sub="Responses mentioning your brand"
          color={platformScoreColor(visibilityScore ?? 0)}
        />
        <ScoreCard
          icon={TrendingUp}
          label="SOV"
          value={sovPct != null ? pct(sovPct) : "—"}
          sub="Your brand’s mention hits ÷ brand + website-backed competitor hits"
          color={platformScoreColor(sovPct ?? 0)}
        />
        <ScoreCard
          icon={Award}
          label="Position"
          value={avgPos != null ? avgPos.toFixed(1) : "—"}
          sub="Avg mention position (lower = earlier)"
          color={platformScoreColor(avgPos == null ? 0 : Math.max(0, 100 - avgPos * 10))}
        />
        <ScoreCard
          icon={Smile}
          label="Positive rate"
          value={
            sentimentResult?.scorePercent != null
              ? `${sentimentResult.scorePercent.toFixed(0)}%`
              : "—"
          }
          sub={
            sentimentResult?.mentionedCount
              ? `${sentimentResult.positiveCount}/${sentimentResult.mentionedCount} keyword-positive mentions`
              : "No brand mentions yet"
          }
          color={
            sentimentResult?.label === "positive"
              ? "#00b894"
              : sentimentResult?.label === "negative"
                ? "#d63031"
                : "#8B5CF6"
          }
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] items-stretch">
        {brandPctCount > 0 && visibilityMetrics && (
          <div className="bg-white rounded-xl border border-gray-200 p-5 h-full">
            <h3 className="text-sm font-semibold text-[#0d0d0d] mb-5">Brand visibility by platform</h3>
            <div className="space-y-4">
              {platforms.map((p) => {
                const metrics = visibilityMetrics.perPlatform[p];
                if (!metrics || metrics.responseCount === 0) return null;
                return (
                  <PlatformBar
                    key={p}
                    platform={p}
                    visibilityPct={metrics.visibilityPct}
                    sovPct={metrics.sovPct}
                  />
                );
              })}
            </div>
          </div>
        )}
        <DeferredChart
          waitUntilVisible={false}
          fallback={
            <div className="flex h-40 items-center justify-center rounded-2xl border border-gray-100 bg-white text-sm text-gray-400 shadow-sm">
              Loading visibility history…
            </div>
          }
        >
          <VisibilityOverTime
            auditId={auditDirOrSlug}
            brandLabel={ctx?.brand_name ?? "Brand"}
          />
        </DeferredChart>
      </div>

      <div className={`grid items-stretch gap-6 ${llmSentiment ? "lg:grid-cols-[minmax(0,0.75fr)_minmax(0,1.25fr)]" : ""}`}>
        {llmSentiment && <OverallSentimentSummary sentiment={llmSentiment} />}
        <DeferredChart
          fallback={
            <div className="flex h-52 items-center justify-center rounded-2xl border border-gray-100 bg-white text-sm text-gray-400 shadow-sm">
              Loading sentiment history…
            </div>
          }
        >
          <SentimentOverTime auditId={auditDirOrSlug} />
        </DeferredChart>
      </div>

      {localeCtx && (
        <div className="grid items-stretch gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)]">
          <BrandCompetitorVisibilityTable
            ctx={localeCtx}
            auditId={auditDirOrSlug}
            competitorLimit={5}
          />
          <DeferredChart
            fallback={
              <div className="flex h-64 items-center justify-center rounded-2xl border border-gray-200 bg-white text-sm text-gray-400">
                Loading visibility history…
              </div>
            }
          >
            <BrandCompetitorVisibility
              auditId={auditDirOrSlug}
              ctx={localeCtx}
              showComparison={false}
              title="Brand & competitor visibility over time"
              competitorLimit={5}
            />
          </DeferredChart>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] items-stretch">
        <TopDomainsTable sites={topSites} />
        <DeferredChart
          fallback={
            <div className="flex h-40 items-center justify-center rounded-2xl border border-gray-100 bg-white text-sm text-gray-400 shadow-sm">
              Loading citation history…
            </div>
          }
        >
          <CitationOverTime
            auditId={auditDirOrSlug}
            allowedDomains={topSites.map((site) => site.domain)}
          />
        </DeferredChart>
      </div>

      {perPrompt.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-100">
            <h3 className="text-sm font-semibold text-[#0d0d0d]">Prompt visibility breakdown</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  <th className="px-6 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-400 min-w-[200px]">Prompt</th>
                  <th className="px-4 py-2 text-center text-xs font-semibold uppercase tracking-wide text-gray-400">Sentiment</th>
                  {platforms.map((p) => (
                    agg?.[p] != null ? (
                      <th key={p} className="px-4 py-2 text-center">
                        <div className="flex items-center justify-center gap-1">
                          <PlatformLogo platform={p} size={16} />
                          <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                            {PLATFORM_META[p]?.label ?? p}
                          </span>
                        </div>
                      </th>
                    ) : null
                  ))}
                </tr>
              </thead>
              <tbody>
                {perPrompt.slice(0, 10).map((row, i) => {
                  const rowSentiment = geminiSentimentForPrompt(row, geminiByPromptId);
                  return (
                  <tr key={i} className="border-b border-gray-50 last:border-0">
                    <td className="px-6 py-3 text-gray-700 text-xs max-w-xs">
                      <span className="line-clamp-2">{row.prompt}</span>
                    </td>
                    <td className="px-4 py-3 text-center">
                      <SentimentBadge sentiment={rowSentiment} />
                    </td>
                    {platforms.map((p) => {
                      if (agg?.[p] == null) return null;
                      const mentionPct = row[`${p}_brand_mention_pct` as keyof typeof row] as number | undefined;
                      const mentioned = (mentionPct ?? 0) > 0;
                      return (
                        <td key={p} className="px-4 py-3 text-center">
                          <span
                            className={`inline-flex items-center justify-center rounded-full w-6 h-6 text-xs font-bold ${mentioned ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-400"}`}
                          >
                            {mentioned ? "✓" : "—"}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
        </>
      )}

    </div>
  );
}
