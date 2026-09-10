import { useEffect, useMemo, useState } from "react";
import { TrendingUp, Eye, Smile, Award, MessageCircle, Search } from "lucide-react";
import { fetchPromptSentiment } from "../api/client";
import { PageLoading } from "./PageLoading";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import type { PromptPerformanceContext, PromptSentimentAnalysis, TopCitedSite, TopCitedUrl } from "../types";
import { PlatformLogo, PLATFORM_META } from "./PlatformLogo";
import { computeOverallSentiment } from "../lib/sentimentCalc";
import {
  AI_OVERVIEW_PLATFORMS,
  CHATBOT_PLATFORMS,
  computeVisibilityMetrics,
  computeVisibilityMetricsForPlatforms,
} from "../lib/visibilityMetrics";
import { annotateProbeRowsWithCategory, filterContextByTopic, listProbeTopics } from "../lib/promptCategoryGrouping";
import { ReportFilterSelect } from "./ReportFilterSelect";
import { completedPlatformRuns, type VisibilityPlatform } from "../lib/brandVisibilityRows";
import { normalizePromptLocales, type PromptLocale } from "../lib/promptLocales";
import VisibilityOverTime from "./VisibilityOverTime";
import CitationOverTime from "./CitationOverTime";
import SentimentOverTime from "./SentimentOverTime";
import SovVisibilityScatter, {
  platformSovVisibilityPoints,
  topicSovVisibilityPoints,
} from "./SovVisibilityScatter";
import {
  BrandCompetitorVisibility,
  BrandCompetitorVisibilityTable,
} from "./BrandCompetitorVisibility";
import {
  buildBrandDomainPredicate,
  buildCompetitorDomainPredicate,
  buildInformationSourceDomainPredicate,
} from "../lib/citationSource";
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

function topSitesForPlatforms(
  sites: TopCitedSite[],
  urls: TopCitedUrl[] | undefined,
  allowedPlatforms: readonly string[],
  perPrompt?: Array<Record<string, unknown>>,
): TopCitedSite[] {
  const allowed = new Set(allowedPlatforms);
  const aggregated = new Map<string, TopCitedSite>();
  for (const row of perPrompt ?? []) {
    for (const platform of allowedPlatforms) {
      const citations = row[`citations_${platform}`];
      if (!Array.isArray(citations)) continue;
      for (const citation of citations as Array<{ domain?: string; url?: string; title?: string }>) {
        const domain = String(citation.domain ?? "").trim();
        if (!domain) continue;
        const current = aggregated.get(domain) ?? { domain, count: 0, platforms: [] };
        current.count += 1;
        current.platforms = Array.from(new Set([...current.platforms, platform]));
        current.example_url ??= citation.url;
        current.title ??= citation.title;
        aggregated.set(domain, current);
      }
    }
  }
  if (aggregated.size) {
    return Array.from(aggregated.values()).sort((left, right) => right.count - left.count);
  }
  for (const item of urls ?? []) {
    const platforms = (item.probe_platforms ?? []).filter((platform) => allowed.has(platform));
    if (!platforms.length) continue;
    const current = aggregated.get(item.domain) ?? {
      domain: item.domain,
      count: 0,
      platforms: [],
    };
    current.count += Number(item.frequency ?? 0);
    current.platforms = Array.from(new Set([...current.platforms, ...platforms]));
    current.example_url ??= item.url;
    current.title ??= item.title;
    aggregated.set(item.domain, current);
  }
  if (aggregated.size) {
    return Array.from(aggregated.values()).sort((left, right) => right.count - left.count);
  }
  return sites
    .filter((site) => site.platforms?.some((platform) => allowed.has(platform)))
    .map((site) => ({
      ...site,
      platforms: site.platforms.filter((platform) => allowed.has(platform)),
    }));
}

function averagePositionForPlatforms(
  ctx: PromptPerformanceContext,
  platforms: readonly string[],
): number | null {
  const rows = ctx.live_probe?.per_prompt ?? [];
  const tokens = ctx.live_probe?.brand_match_tokens ?? [];
  let total = 0;
  let count = 0;
  for (const row of rows) {
    for (const platform of platforms) {
      const seenResponses = new Set<string>();
      const runs = completedPlatformRuns(row, platform as VisibilityPlatform);
      const textRuns = runs.filter((run) => Boolean(run.response));
      for (const run of textRuns) {
        if (!run.response || !tokens.length) continue;
        if (seenResponses.has(run.response)) continue;
        seenResponses.add(run.response);
        const lower = run.response.toLowerCase();
        const idx = tokens
          .map((token) => lower.indexOf(token.toLowerCase()))
          .filter((position) => position >= 0)
          .sort((a, b) => a - b)[0];
        if (idx == null) continue;
        total += (idx / run.response.length) * 10 + 1;
        count++;
      }
      if (!textRuns.length) {
        const listMetrics = row.list_metrics as {
          platform_metrics?: Record<string, { avg_position?: number | null; position_count?: number }>;
        } | undefined;
        const aggregate = listMetrics?.platform_metrics?.[platform];
        if (aggregate?.avg_position != null && (aggregate.position_count ?? 0) > 0) {
          total += aggregate.avg_position * (aggregate.position_count ?? 0);
          count += aggregate.position_count ?? 0;
        }
      }
    }
  }
  return count > 0 ? total / count : null;
}

function ScoreCard({
  icon: Icon,
  label,
  value,
  sub,
  color,
  labelColor,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: string;
  color: string;
  labelColor?: string;
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
        <span
          className="text-xs font-semibold uppercase tracking-wide"
          style={{ color: labelColor ?? "#9ca3af" }}
        >
          {label}
        </span>
      </div>
      <p className="text-3xl font-bold text-[#0d0d0d] leading-none mb-1">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function VisibilityScoreCard({
  label,
  description,
  metrics,
  icon: Icon,
  color,
}: {
  label: string;
  description: string;
  metrics: ReturnType<typeof computeVisibilityMetricsForPlatforms>;
  icon: React.ElementType;
  color: string;
}) {
  const hasResults = (metrics?.promptCount ?? 0) > 0;
  return (
    <div className="flex items-center gap-4 rounded-xl border border-gray-200 bg-white p-4">
      <div
        className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full border-[6px] text-lg font-bold"
        style={{
          borderColor: `${color}33`,
          color: hasResults ? color : "#9ca3af",
        }}
      >
        {hasResults ? Math.round(metrics!.score) : "—"}
      </div>
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4" style={{ color }} />
          <p className="text-sm font-semibold text-[#0d0d0d]">{label}</p>
        </div>
        <p className="mt-1 text-xs leading-relaxed text-gray-500">{description}</p>
        <p className="mt-1 text-xs text-gray-400">
          {hasResults
            ? `${pct(metrics!.visibilityPct)} visibility · ${pct(metrics!.sovPct)} SOV`
            : "No probe results yet"}
        </p>
      </div>
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

function OverallSentimentSummary({
  sentiment,
  overall,
  chatbot,
  overview,
  topicSentiments,
}: {
  sentiment: PromptSentimentAnalysis | null;
  overall: ReturnType<typeof computeOverallSentiment>;
  chatbot: ReturnType<typeof computeOverallSentiment>;
  overview: ReturnType<typeof computeOverallSentiment>;
  topicSentiments: Array<{
    topic: string;
    chatbot: ReturnType<typeof computeOverallSentiment>;
    overview: ReturnType<typeof computeOverallSentiment>;
  }>;
}) {
  const { fg, bg } = SENTIMENT_COLORS[sentiment?.overall_sentiment ?? "Neutral"] ?? SENTIMENT_COLORS.Neutral;
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <div>
          <div className="flex items-center gap-3 mb-3">
            <Smile className="w-4 h-4" style={{ color: fg }} />
            <span className="text-sm font-semibold text-[#0d0d0d]">AI Sentiment</span>
            <span
              className="inline-block px-2.5 py-0.5 rounded-full text-xs font-bold"
              style={{ background: bg, color: fg }}
            >
              {sentiment?.overall_sentiment ?? "Calculated from probe responses"}
            </span>
          </div>
          <p className="text-sm text-gray-500 leading-relaxed">
            {sentiment?.overall_summary ?? "Sentiment is calculated from brand mentions in the selected probe responses."}
          </p>
          <div className="mt-3 rounded-lg bg-gray-50 px-3 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Overall score</p>
            <p className="mt-1 text-lg font-bold text-gray-800">
              {overall.scorePercent == null ? "—" : `${overall.scorePercent.toFixed(0)}% positive`}
            </p>
            <p className="text-[11px] text-gray-400">
              {overall.mentionedCount
                ? `${overall.positiveCount}/${overall.mentionedCount} brand mentions`
                : "No brand mentions"}
            </p>
          </div>
          <div className="mt-4 grid gap-3 border-t border-gray-100 pt-4 sm:grid-cols-2">
            {[
              { label: "Chatbots", result: chatbot, color: "#8B5CF6" },
              { label: "AI Overviews", result: overview, color: "#EA4335" },
            ].map(({ label, result, color }) => {
              const colors = SENTIMENT_COLORS[result.label] ?? SENTIMENT_COLORS.Neutral;
              return (
                <div key={label} className="rounded-lg bg-gray-50 p-3">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="flex items-center gap-2 text-xs font-semibold text-gray-600">
                      <span className="h-2 w-2 rounded-full" style={{ background: color }} />
                      {label}
                    </span>
                    <span className="rounded-full px-2 py-0.5 text-[10px] font-bold" style={{ background: colors.bg, color: colors.fg }}>
                      {result.label}
                    </span>
                  </div>
                  <p className="text-sm font-bold text-gray-800">
                    {result.scorePercent == null ? "—" : `${result.scorePercent.toFixed(0)}% positive`}
                  </p>
                  <p className="mt-1 text-[11px] text-gray-400">
                    {result.mentionedCount ? `${result.positiveCount}/${result.mentionedCount} brand mentions` : "No brand mentions"}
                  </p>
                </div>
              );
            })}
          </div>
        </div>
        <div className="border-t border-gray-100 pt-4 lg:border-l lg:border-t-0 lg:pl-5 lg:pt-0">
          {topicSentiments.length > 0 ? (
            <div className="overflow-x-auto">
              <p className="mb-2 text-xs font-semibold text-gray-600">% Positive mentions by topic</p>
              <table className="w-full min-w-[280px] text-xs">
            <thead>
              <tr className="border-b border-gray-100 text-[10px] uppercase tracking-wide text-gray-400">
                <th className="px-2 py-2 text-left">Topic</th>
                <th className="px-2 py-2 text-center text-violet-600">Chatbots</th>
                <th className="px-2 py-2 text-center text-red-600">AI Overviews</th>
              </tr>
            </thead>
            <tbody>
              {topicSentiments.map((item) => (
                <tr key={item.topic} className="border-b border-gray-50 last:border-0">
                  <td className="px-2 py-2 font-medium text-gray-600">{item.topic}</td>
                  <td className="px-2 py-2 text-center font-semibold text-violet-600">
                    {item.chatbot.scorePercent == null ? "—" : `${item.chatbot.scorePercent.toFixed(0)}%`}
                  </td>
                  <td className="px-2 py-2 text-center font-semibold text-red-600">
                    {item.overview.scorePercent == null ? "—" : `${item.overview.scorePercent.toFixed(0)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
              </table>
            </div>
          ) : (
            <div className="flex h-full min-h-28 items-center justify-center text-center text-xs text-gray-400">
              Topic sentiment is unavailable for the selected prompt data.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TopDomainsTable({
  sites,
  title = "Top cited chatbot domains",
  isCompetitorDomain,
  isBrandDomain,
  titleColor = "#8B5CF6",
}: {
  sites: TopCitedSite[];
  title?: string;
  isCompetitorDomain?: (domain: string) => boolean;
  isBrandDomain?: (domain: string) => boolean;
  titleColor?: string;
}) {
  if (!sites.length) return null;
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100">
        <h3 className="text-sm font-semibold" style={{ color: titleColor }}>{title}</h3>
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
          {sites.slice(0, 5).map((s) => (
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
                <span className="font-medium text-[#0d0d0d]">
                  {s.domain}
                  <span
                    className={`ml-2 text-[10px] font-semibold uppercase tracking-wide ${
                      isBrandDomain?.(s.domain)
                        ? "text-emerald-600"
                        : isCompetitorDomain?.(s.domain)
                          ? "text-amber-600"
                          : "text-gray-400"
                    }`}
                  >
                    {isBrandDomain?.(s.domain) ? "YOUR BRAND" : isCompetitorDomain?.(s.domain) ? "COMPETITOR" : "SOURCE"}
                  </span>
                </span>
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
  const [selectedLocaleKey, setSelectedLocaleKey] = useState<string | null>(null);
  const [selectedTopic, setSelectedTopic] = useState("All topics");

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
    setSelectedTopic("All topics");
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
  const topics = useMemo(() => listProbeTopics(localeCtx), [localeCtx]);
  const overviewCtx = useMemo(
    () => filterContextByTopic(localeCtx, selectedTopic),
    [localeCtx, selectedTopic],
  );

  const topicSentiments = useMemo(() => {
    if (!overviewCtx) return [];
    const overviewLive = overviewCtx.live_probe;
    const brandTokens = overviewLive?.brand_match_tokens ?? [];
    const grouped = new Map<string, Array<Record<string, unknown>>>();
    for (const { row, meta } of annotateProbeRowsWithCategory(overviewLive, overviewCtx)) {
      const rows = grouped.get(meta.productLabel) ?? [];
      rows.push(row as Record<string, unknown>);
      grouped.set(meta.productLabel, rows);
    }
    return Array.from(grouped.entries())
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([topic, rows]) => ({
        topic,
        chatbot: computeOverallSentiment(rows, brandTokens, [...CHATBOT_PLATFORMS]),
        overview: computeOverallSentiment(rows, brandTokens, [...AI_OVERVIEW_PLATFORMS]),
      }));
  }, [overviewCtx]);

  if (loading) {
    return <PageLoading />;
  }

  if (error) {
    return <div className="alert-error m-6">{error}</div>;
  }

  const overviewLive = overviewCtx?.live_probe;
  const agg = overviewLive?.aggregate;
  const perPrompt = overviewLive?.per_prompt ?? [];
  const isInformationSourceDomain = buildInformationSourceDomainPredicate(overviewCtx);
  const isCompetitorDomain = buildCompetitorDomainPredicate(overviewCtx);
  const isBrandDomain = buildBrandDomainPredicate(overviewCtx);
  const topSites = (overviewLive?.top_cited_sites ?? []).filter((site) =>
    isInformationSourceDomain(site.domain)
  );

  const platforms = [...CHATBOT_PLATFORMS, ...AI_OVERVIEW_PLATFORMS] as const;

  const brandTokens = overviewLive?.brand_match_tokens ?? [];

  const visibilityMetrics = overviewCtx ? computeVisibilityMetrics(overviewCtx) : null;
  const brandPctCount = visibilityMetrics
    ? platforms.filter((platform) => visibilityMetrics.perPlatform[platform]?.responseCount > 0).length
    : 0;

  const filterLocales = normalizePromptLocales(
    ctx?.prompt_locales as PromptLocale[] | undefined,
    ctx?.primary_market?.country ?? "",
    ctx?.primary_market?.country_id ?? "",
  );

  const hasData = agg != null || perPrompt.length > 0;
  const chatbotMetrics = overviewCtx
    ? computeVisibilityMetricsForPlatforms(overviewCtx, CHATBOT_PLATFORMS)
    : null;
  const aiOverviewMetrics = overviewCtx
    ? computeVisibilityMetricsForPlatforms(overviewCtx, AI_OVERVIEW_PLATFORMS)
    : null;
  const chatbotSentiment = computeOverallSentiment(
    perPrompt as Array<Record<string, unknown>>,
    brandTokens,
    [...CHATBOT_PLATFORMS],
  );
  const aiOverviewSentiment = computeOverallSentiment(
    perPrompt as Array<Record<string, unknown>>,
    brandTokens,
    [...AI_OVERVIEW_PLATFORMS],
  );
  const overallSentiment = computeOverallSentiment(
    perPrompt as Array<Record<string, unknown>>,
    brandTokens,
    [...platforms],
  );
  const chatbotPosition = overviewCtx
    ? averagePositionForPlatforms(overviewCtx, CHATBOT_PLATFORMS)
    : null;
  const aiOverviewPosition = overviewCtx
    ? averagePositionForPlatforms(overviewCtx, AI_OVERVIEW_PLATFORMS)
    : null;

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

      <div className="flex flex-wrap items-end gap-4">
        <PromptLocaleFilter
          locales={filterLocales}
          selectedKey={localeKey}
          onChange={onLocaleChange}
          hideIfSingle={false}
          ctx={ctx}
          className="mb-0"
        />
        <ReportFilterSelect
          id="overview-topic-select"
          label="Topic"
          hint="Limit the overview to prompts tagged with one product or service topic."
          value={selectedTopic}
          onChange={(event) => setSelectedTopic(event.target.value)}
        >
          <option>All topics</option>
          {topics.map((topic) => <option key={topic}>{topic}</option>)}
        </ReportFilterSelect>
      </div>

      {localeKey !== OVERALL_LOCALE_KEY && !overviewLive?.per_prompt?.length ? (
        <div className="alert-info">
          No probe data for this market/language yet. Re-run failed markets from the Prompts section to fill it in.
        </div>
      ) : (
        <>
      <div>
        <div className="mb-3">
          <h3 className="text-sm font-semibold text-[#0d0d0d]">AI visibility scores</h3>
          <p className="mt-1 text-xs text-gray-400">
            Scores are separated by how people encounter AI answers: conversational chatbots versus search-generated summaries.
          </p>
        </div>
        <div className="grid gap-4 lg:grid-cols-2">
          <VisibilityScoreCard
            label="Chatbots"
            description="Gemini, ChatGPT, and Claude responses to your prompts."
            metrics={chatbotMetrics}
            icon={MessageCircle}
            color="#8B5CF6"
          />
          <VisibilityScoreCard
            label="AI Overviews"
            description="Google AI Overviews generated from Google Search results."
            metrics={aiOverviewMetrics}
            icon={Search}
            color="#EA4335"
          />
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        {[
          {
            label: "Chatbots",
            color: "#8B5CF6",
            metrics: chatbotMetrics,
            position: chatbotPosition,
            sentiment: chatbotSentiment,
          },
          {
            label: "AI Overviews",
            color: "#EA4335",
            metrics: aiOverviewMetrics,
            position: aiOverviewPosition,
            sentiment: aiOverviewSentiment,
          },
        ].map((surface) => (
          <div key={surface.label} className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="h-2 w-2 rounded-full" style={{ background: surface.color }} />
              <h3 className="text-sm font-semibold text-[#0d0d0d]">{surface.label}</h3>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <ScoreCard
                icon={Eye}
                label="Visibility"
                value={surface.metrics ? pct(surface.metrics.visibilityPct) : "—"}
                sub="Responses mentioning your brand"
                color={surface.color}
                labelColor={surface.color}
              />
              <ScoreCard
                icon={TrendingUp}
                label="SOV"
                value={surface.metrics ? pct(surface.metrics.sovPct) : "—"}
                sub="Brand share of brand + competitor hits"
                color={surface.color}
                labelColor={surface.color}
              />
              <ScoreCard
                icon={Award}
                label="Position"
                value={surface.position != null ? surface.position.toFixed(1) : "—"}
                sub="Avg mention position (lower = earlier)"
                color={surface.color}
                labelColor={surface.color}
              />
              <ScoreCard
                icon={Smile}
                label="Positive rate"
                value={surface.sentiment.scorePercent != null ? `${surface.sentiment.scorePercent.toFixed(0)}%` : "—"}
                sub={surface.sentiment.mentionedCount
                  ? `${surface.sentiment.positiveCount}/${surface.sentiment.mentionedCount} positive mentions`
                  : "No brand mentions yet"}
                color={surface.color}
                labelColor={surface.color}
              />
            </div>
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] items-stretch">
        {brandPctCount > 0 && visibilityMetrics && (
          <div className="bg-white rounded-xl border border-gray-200 p-5 h-full">
            <h3 className="text-sm font-semibold text-[#0d0d0d] mb-5">Brand visibility by platform</h3>
            {[
              { label: "Chatbots", color: "#8B5CF6", items: CHATBOT_PLATFORMS },
              { label: "AI Overviews", color: "#EA4335", items: AI_OVERVIEW_PLATFORMS },
            ].map((group) => (
              <div key={group.label} className="mb-5 last:mb-0">
                <p className="mb-3 flex items-center gap-2 text-xs font-semibold text-gray-500">
                  <span className="h-2 w-2 rounded-full" style={{ background: group.color }} />
                  {group.label}
                </p>
                <div className="space-y-4">
                  {group.items.map((p) => {
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
            ))}
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
            topic={selectedTopic === "All topics" ? undefined : selectedTopic}
          />
        </DeferredChart>
      </div>
      <div className="space-y-4">
        <SovVisibilityScatter
          title="SOV vs Visibility: Platforms"
          subtitle="Your brand by responding platform"
          dimensionLabel="Platform"
          points={platformSovVisibilityPoints(visibilityMetrics)}
          emptyMessage="No platform data available yet."
        />
        <SovVisibilityScatter
          title="SOV vs Visibility: Topics"
          subtitle="Your brand by product or service topic"
          dimensionLabel="Topic"
          points={topicSovVisibilityPoints(localeCtx)}
          emptyMessage="No topic data available yet."
        />
      </div>

      <div className="grid items-stretch gap-6 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
        <OverallSentimentSummary
          sentiment={llmSentiment}
          overall={overallSentiment}
          chatbot={chatbotSentiment}
          overview={aiOverviewSentiment}
          topicSentiments={topicSentiments}
        />
        <DeferredChart
          fallback={
            <div className="flex h-52 items-center justify-center rounded-2xl border border-gray-100 bg-white text-sm text-gray-400 shadow-sm">
              Loading sentiment history…
            </div>
          }
        >
          <SentimentOverTime
            auditId={auditDirOrSlug}
            topic={selectedTopic === "All topics" ? undefined : selectedTopic}
          />
        </DeferredChart>
      </div>

      {overviewCtx && (
        <div className="grid items-stretch gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,0.75fr)]">
          <BrandCompetitorVisibilityTable
            ctx={overviewCtx}
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
              ctx={overviewCtx}
              showComparison={false}
              title="Brand & competitor visibility over time"
              competitorLimit={5}
              topic={selectedTopic === "All topics" ? undefined : selectedTopic}
            />
          </DeferredChart>
        </div>
      )}

      <div className="space-y-6">
        <div className="grid gap-6 lg:grid-cols-2 items-stretch">
          <TopDomainsTable
            sites={topSitesForPlatforms(topSites, overviewLive?.top_cited_urls, CHATBOT_PLATFORMS, perPrompt as Array<Record<string, unknown>>)}
            isCompetitorDomain={isCompetitorDomain}
            isBrandDomain={isBrandDomain}
            titleColor="#8B5CF6"
          />
          <TopDomainsTable
            sites={topSitesForPlatforms(topSites, overviewLive?.top_cited_urls, AI_OVERVIEW_PLATFORMS, perPrompt as Array<Record<string, unknown>>)}
            title="Top cited AI Overview domains"
            isCompetitorDomain={isCompetitorDomain}
            isBrandDomain={isBrandDomain}
            titleColor="#EA4335"
          />
        </div>
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
                  <th colSpan={CHATBOT_PLATFORMS.length} className="border-l border-gray-200 px-4 py-2 text-center text-[10px] font-semibold uppercase tracking-wide text-violet-600">
                    Chatbots
                  </th>
                  <th colSpan={AI_OVERVIEW_PLATFORMS.length} className="border-l border-gray-200 px-4 py-2 text-center text-[10px] font-semibold uppercase tracking-wide text-red-600">
                    AI Overviews
                  </th>
                </tr>
                <tr className="border-b border-gray-100 bg-gray-50">
                  <th className="px-6 py-1" />
                  {platforms.map((p, index) => (
                    <th
                      key={p}
                      className={`px-4 py-2 text-center ${index === CHATBOT_PLATFORMS.length ? "border-l border-gray-200" : ""}`}
                    >
                      <div className="flex items-center justify-center gap-1">
                        <PlatformLogo platform={p} size={16} />
                        <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                          {PLATFORM_META[p]?.label ?? p}
                        </span>
                      </div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {perPrompt.slice(0, 10).map((row, i) => {
                  return (
                  <tr key={i} className="border-b border-gray-50 last:border-0">
                    <td className="px-6 py-3 text-gray-700 text-xs max-w-xs">
                      <span className="line-clamp-2">{row.prompt}</span>
                    </td>
                    {platforms.map((p) => {
                      const mentionPct = row[`${p}_brand_mention_pct` as keyof typeof row] as number | undefined;
                      const mentioned = (mentionPct ?? 0) > 0;
                      return (
                        <td key={p} className={`px-4 py-3 text-center ${p === "google_aio" ? "border-l border-gray-200" : ""}`}>
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
