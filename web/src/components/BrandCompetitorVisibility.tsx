import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Check, Loader2, Plus } from "lucide-react";
import { PageLoading } from "./PageLoading";
import type {
  LiveProbePerPrompt,
  PlatformDailySummary,
  ProbeHistoryEntry,
  PromptPerformanceContext,
} from "../types";
import { sortByLatestSeriesValueDesc } from "../lib/chartLegend";
import { tooltipItemSorterByValueDesc } from "../lib/chartTooltip";
import { fetchProbeHistory } from "../lib/probeHistoryFetch";
import { trackCompetitor } from "../api/client";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import { stemBrand } from "../lib/brandNormalize";
import { textMentionsBrand } from "../lib/brandMatch";
import { normalizePromptLocales, type PromptLocale } from "../lib/promptLocales";
import { CompetitorFavicon } from "./PlatformLogo";
import { PromptLocaleFilter, liveProbeForLocale } from "./PromptLocaleFilter";
import { OVERALL_LOCALE_KEY } from "../lib/localeProbeView";
import { preferredInitialLocaleKey } from "../lib/defaultLocaleView";
import {
  buildBrandRows,
  completedPlatformRuns,
  COMPETITOR_PLATFORMS,
  type BrandRow,
} from "./CompetitorComparisonSection";
import { globalSignalHits, rowSignalHits } from "../lib/brandVisibilityRows";

const SERIES_COLORS = [
  "#047857",
  "#2563EB",
  "#7C3AED",
  "#C2410C",
  "#0E7490",
  "#A21CAF",
  "#4D7C0F",
  "#B45309",
  "#475569",
  "#BE123C",
  "#0369A1",
];

interface ChartDatum {
  date: string;
  [key: string]: string | number;
}

interface TopicResult {
  topic: string;
  brandVisibility: number;
  competitorVisibility: number;
  delta: number;
}

function percentage(value: number, total: number): number {
  return total > 0 ? (value / total) * 100 : 0;
}

function formatPct(value: number): string {
  return `${value.toFixed(1)}%`;
}

function formatDate(value: string): string {
  try {
    return new Date(value).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  } catch {
    return value;
  }
}

function CompetitorLink({
  name,
  website,
  className = "",
}: {
  name: string;
  website?: string;
  className?: string;
}) {
  if (!website) return <span className={className}>{name}</span>;
  return (
    <a
      href={website}
      target="_blank"
      rel="noopener noreferrer"
      className={`${className} rounded-sm hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500`}
    >
      {name}
    </a>
  );
}

function competitorVisibilityFor(
  summary: PlatformDailySummary,
  competitorName: string,
): number {
  const target = stemBrand(competitorName);
  const visibility = summary.competitor_visibility ?? {};
  const matches = Object.entries(visibility)
    .filter(([name]) => stemBrand(name) === target)
    .map(([, value]) => Number(value) || 0);
  if (matches.length) return Math.max(...matches);
  const responseCount = Number(summary.response_count ?? 0);
  const detailMatches = Object.entries(summary.competitor_detail ?? {})
    .filter(([name]) => stemBrand(name) === target)
    .map(([, count]) => percentage(Number(count) || 0, responseCount) / 100);
  return detailMatches.length ? Math.max(...detailMatches) : 0;
}

function buildHistoryRows(
  entries: ProbeHistoryEntry[],
  series: BrandRow[],
): ChartDatum[] {
  return entries
    .slice()
    .sort((left, right) => left.date.localeCompare(right.date))
    .map((entry) => {
      const result: ChartDatum = { date: entry.date };
      const platformRows = COMPETITOR_PLATFORMS
        .map((platform) => entry.summary?.[platform])
        .filter((summary): summary is PlatformDailySummary => Boolean(summary?.response_count));
      const totalResponses = platformRows.reduce(
        (sum, summary) => sum + Number(summary.response_count ?? 0),
        0,
      );
      series.forEach((brand, index) => {
        const weightedMentions = platformRows.reduce((sum, summary) => {
          const responses = Number(summary.response_count ?? 0);
          const visibility = brand.isOwnBrand
            ? Number(summary.brand_visibility ?? 0)
            : competitorVisibilityFor(summary, brand.name);
          return sum + visibility * responses;
        }, 0);
        result[`series_${index}`] = Math.round(percentage(weightedMentions, totalResponses));
      });
      return result;
    });
}

function promptTopicMap(ctx: PromptPerformanceContext): Map<string, string> {
  const result = new Map<string, string>();
  const metadataRows = ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows;
  for (const row of metadataRows ?? []) {
    const topic = row.product_or_service || "Other";
    for (const prompt of row.prompts ?? []) {
      result.set(prompt.trim().toLowerCase(), topic);
    }
  }
  return result;
}

function earliestMention(text: string, labels: string[]): number {
  let earliest = -1;
  for (const raw of labels) {
    const label = raw.trim();
    if (label.length < 2) continue;
    const words = label.split(/[\s\-]+/).filter(Boolean);
    const pattern = words
      .map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("[\\s\\-]+");
    const match = new RegExp(pattern, "i").exec(text);
    if (match && (earliest < 0 || match.index < earliest)) earliest = match.index;
  }
  return earliest;
}

function pairwiseMetrics(
  ctx: PromptPerformanceContext,
  rows: BrandRow[],
  competitor: BrandRow,
) {
  const own = rows.find((row) => row.isOwnBrand)!;
  // Same raw SOV denominator as Brand & competitor visibility table / Overview scorecard.
  const totalHits = globalSignalHits(own);
  const brandTokens = ctx.live_probe?.brand_match_tokens?.length
    ? ctx.live_probe.brand_match_tokens
    : [ctx.brand_name];
  const competitorTokens = [competitor.name, competitor.website ?? ""].filter(Boolean);
  const topics = promptTopicMap(ctx);
  const orderedTopicRows = (ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows) ?? [];
  const positionalTopics = orderedTopicRows.flatMap((row) =>
    (row.prompts ?? []).map(() => row.product_or_service || "Other"),
  );
  const topicCounts = new Map<string, { responses: number; brand: number; competitor: number }>();
  let brandFirst = 0;
  let competitorFirst = 0;

  (ctx.live_probe?.per_prompt ?? []).forEach((promptRow, promptIndex) => {
    const prompt = String(promptRow.prompt ?? "").trim().toLowerCase();
    const topic = topics.get(prompt) ?? positionalTopics[promptIndex] ?? "Other";
    for (const platform of COMPETITOR_PLATFORMS) {
      for (const run of completedPlatformRuns(promptRow as LiveProbePerPrompt, platform)) {
        const responseText = run.response ?? "";
        const lower = responseText.toLowerCase();
        const brandMentioned = Number(run.scores.brand_signal ?? 0) > 0
          || textMentionsBrand(responseText, ctx.brand_name, brandTokens);
        const competitorSignal = Object.entries(run.scores.competitor_detail ?? {})
          .some(([name, hits]) => stemBrand(name) === stemBrand(competitor.name) && Number(hits) > 0);
        const competitorMentioned = competitorSignal
          || textMentionsBrand(responseText, competitor.name, competitorTokens);
        const counts = topicCounts.get(topic) ?? { responses: 0, brand: 0, competitor: 0 };
        counts.responses += 1;
        if (brandMentioned) counts.brand += 1;
        if (competitorMentioned) counts.competitor += 1;
        topicCounts.set(topic, counts);

        if (brandMentioned || competitorMentioned) {
          const brandPosition = brandMentioned ? earliestMention(lower, brandTokens) : -1;
          const competitorPosition = competitorMentioned
            ? earliestMention(lower, competitorTokens)
            : -1;
          if (brandPosition >= 0 && (competitorPosition < 0 || brandPosition <= competitorPosition)) {
            brandFirst += 1;
          } else if (competitorPosition >= 0) {
            competitorFirst += 1;
          }
        }
      }
    }
  });

  const topicResults: TopicResult[] = Array.from(topicCounts, ([topic, counts]) => {
    const brandVisibility = percentage(counts.brand, counts.responses);
    const competitorVisibility = percentage(counts.competitor, counts.responses);
    return {
      topic,
      brandVisibility,
      competitorVisibility,
      delta: brandVisibility - competitorVisibility,
    };
  });
  const firstTotal = brandFirst + competitorFirst;

  return {
    brandVisibility: percentage(own.promptMentionCount, own.totalPrompts),
    competitorVisibility: percentage(competitor.promptMentionCount, competitor.totalPrompts),
    brandSov: percentage(rowSignalHits(own), totalHits),
    competitorSov: percentage(rowSignalHits(competitor), totalHits),
    brandFirst: percentage(brandFirst, firstTotal),
    competitorFirst: percentage(competitorFirst, firstTotal),
    brandTopics: topicResults.filter((topic) => topic.delta > 0).sort((a, b) => b.delta - a.delta),
    competitorTopics: topicResults.filter((topic) => topic.delta < 0).sort((a, b) => a.delta - b.delta),
    firstTotal,
  };
}

function MetricRow({
  label,
  brandName,
  competitorName,
  brandValue,
  competitorValue,
}: {
  label: string;
  brandName: string;
  competitorName: string;
  brandValue: number;
  competitorValue: number;
}) {
  return (
    <tr className="border-b border-gray-100 last:border-0">
      <th className="px-4 py-3 text-left text-xs font-medium text-gray-500">{label}</th>
      <td className="px-4 py-3 text-center text-sm font-bold text-emerald-700" aria-label={`${brandName}: ${formatPct(brandValue)}`}>
        {formatPct(brandValue)}
      </td>
      <td className="px-4 py-3 text-center text-sm font-bold text-gray-800" aria-label={`${competitorName}: ${formatPct(competitorValue)}`}>
        {formatPct(competitorValue)}
      </td>
    </tr>
  );
}

/** Rank by Visibility desc (same basis as Competitor visibility chart series). */
function topComparisonRows(ctx: PromptPerformanceContext, competitorLimit: number): BrandRow[] {
  const rows = buildBrandRows(ctx);
  const ownBrand = rows.find((row) => row.isOwnBrand);
  const competitors = rows
    .filter((row) => !row.isOwnBrand && row.promptMentionCount > 0)
    .sort((left, right) =>
      right.promptMentionCount - left.promptMentionCount
      || left.name.localeCompare(right.name),
    )
    .slice(0, competitorLimit);
  const combined = ownBrand ? [ownBrand, ...competitors] : competitors;
  return combined.slice().sort((left, right) =>
    right.promptMentionCount - left.promptMentionCount
    || left.name.localeCompare(right.name),
  );
}

export function BrandCompetitorVisibilityTable({
  ctx,
  auditId,
  title = "Brand & competitor visibility",
  competitorLimit = 10,
  showTrackCompetitor = false,
}: {
  ctx: PromptPerformanceContext;
  auditId: string;
  title?: string;
  competitorLimit?: number;
  /** When true, show Track competitor / Tracked controls (Competitor visibility page only). */
  showTrackCompetitor?: boolean;
}) {
  const rows = useMemo(
    () => topComparisonRows(ctx, competitorLimit),
    [ctx, competitorLimit],
  );
  const [trackedNames, setTrackedNames] = useState(
    () => new Set(ctx.competitors.map((competitor) => stemBrand(competitor.competitor_brand ?? ""))),
  );
  const [trackingName, setTrackingName] = useState("");
  const [trackError, setTrackError] = useState("");
  const totalHits = globalSignalHits(rows[0]);

  return (
    <section className="overflow-hidden rounded-xl border border-gray-200 bg-white">
      <div className="border-b border-gray-100 px-5 py-4">
        <h3 className="text-sm font-semibold text-[#0d0d0d]">{title}</h3>
        <p className="mt-1 text-[11px] text-gray-400">Current response-level performance.</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">Brand</th>
              <th className="px-4 py-3 text-right text-[10px] font-semibold uppercase tracking-wide text-gray-400">Visibility</th>
              <th className="px-4 py-3 text-right text-[10px] font-semibold uppercase tracking-wide text-gray-400">SOV</th>
              {showTrackCompetitor && (
                <th className="px-4 py-3 text-right text-[10px] font-semibold uppercase tracking-wide text-gray-400">Tracking</th>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const visibility = percentage(row.promptMentionCount, row.totalPrompts);
              const sov = percentage(rowSignalHits(row), totalHits);
              return (
                  <tr
                    key={row.name}
                    className={`border-b border-gray-50 ${row.isOwnBrand ? "bg-emerald-50/40" : ""}`}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <CompetitorFavicon name={row.name} website={row.website} size={17} />
                        {row.isOwnBrand ? (
                          <span className="inline-flex flex-wrap items-center gap-2 font-semibold text-emerald-700">
                            {row.name}
                            <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700">
                              Your Brand
                            </span>
                          </span>
                        ) : (
                          <CompetitorLink
                            name={row.name}
                            website={row.website}
                            className="font-semibold text-gray-800"
                          />
                        )}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums text-gray-700">{formatPct(visibility)}</td>
                    <td className="px-4 py-3 text-right font-semibold tabular-nums text-gray-700">{formatPct(sov)}</td>
                    {showTrackCompetitor && (
                      <td className="px-4 py-3 text-right">
                        {!row.isOwnBrand && (
                          trackedNames.has(stemBrand(row.name)) ? (
                            <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700">
                              <Check className="h-3.5 w-3.5" /> Tracked
                            </span>
                          ) : (
                            <button
                              type="button"
                              disabled={trackingName === row.name}
                              onClick={async () => {
                                setTrackingName(row.name);
                                setTrackError("");
                                try {
                                  await trackCompetitor(auditId, { name: row.name, website: row.website });
                                  setTrackedNames((current) => new Set(current).add(stemBrand(row.name)));
                                } catch (error) {
                                  setTrackError(error instanceof Error ? error.message : "Could not track competitor");
                                } finally {
                                  setTrackingName("");
                                }
                              }}
                              className="inline-flex items-center gap-1 rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-semibold text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                            >
                              {trackingName === row.name
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <Plus className="h-3.5 w-3.5" />}
                              Track competitor
                            </button>
                          )
                        )}
                      </td>
                    )}
                  </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {showTrackCompetitor && trackError && (
        <p className="border-t border-red-100 bg-red-50 px-4 py-2 text-xs text-red-700">{trackError}</p>
      )}
    </section>
  );
}

export function BrandCompetitorVisibility({
  auditId,
  ctx,
  showComparison = true,
  title = "Brand & competitor visibility over time",
  competitorLimit = 10,
}: {
  auditId: string;
  ctx: PromptPerformanceContext;
  showComparison?: boolean;
  title?: string;
  competitorLimit?: number;
}) {
  const allRows = useMemo(() => buildBrandRows(ctx), [ctx]);
  const competitors = allRows
    .filter((row) => !row.isOwnBrand && row.promptMentionCount > 0)
    .sort((left, right) => right.promptMentionCount - left.promptMentionCount)
    .slice(0, competitorLimit);
  const chartCompetitors = competitors;
  const chartSeries = [allRows[0], ...chartCompetitors].filter(Boolean);
  const [history, setHistory] = useState<ProbeHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedName, setSelectedName] = useState(competitors[0]?.name ?? "");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchProbeHistory(auditId)
      .then((result) => {
        if (!cancelled) setHistory(result.entries);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [auditId]);

  useEffect(() => {
    if (!competitors.some((competitor) => competitor.name === selectedName)) {
      setSelectedName(competitors[0]?.name ?? "");
    }
  }, [competitors, selectedName]);

  const selected = competitors.find((competitor) => competitor.name === selectedName);
  const comparison = selected ? pairwiseMetrics(ctx, allRows, selected) : null;
  const chartRows = buildHistoryRows(history, chartSeries);
  const legendSeries = useMemo(
    () =>
      sortByLatestSeriesValueDesc(
        chartSeries.map((brand, index) => ({
          brand,
          index,
          dataKey: `series_${index}`,
        })),
        chartRows,
      ),
    [chartSeries, chartRows],
  );

  return (
    <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
      <div className="border-b border-gray-100 px-5 py-4">
        <h3 className="text-sm font-bold text-[#0d0d0d]">{title}</h3>
        <p className="mt-1 text-xs text-gray-500">
          Daily response-level visibility for {ctx.brand_name} and tracked competitors.
        </p>
      </div>

      <div className="p-5">
        {loading ? (
          <div className="flex h-64 items-center justify-center text-sm text-gray-400">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading visibility history…
          </div>
        ) : (
          <>
            {chartRows.length < 2 && (
              <p className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700">
                One daily data point is available. The trend will build as scheduled probes run.
              </p>
            )}
            <div className="mb-3 flex flex-wrap gap-x-4 gap-y-2" aria-label="Chart series">
              {legendSeries.map(({ brand, index }) => (
                <div key={brand.name} className="flex items-center gap-1.5 text-[11px] text-gray-600">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: SERIES_COLORS[index % SERIES_COLORS.length] }} />
                  {brand.isOwnBrand ? brand.name : (
                    <CompetitorLink name={brand.name} website={brand.website} />
                  )}
                </div>
              ))}
            </div>
            <ResponsiveContainer width="100%" height={290}>
              <LineChart data={chartRows} margin={{ top: 8, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="#eef0f2" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tickFormatter={formatDate} tick={{ fontSize: 10, fill: "#9CA3AF" }} axisLine={false} tickLine={false} />
                <YAxis domain={[0, 100]} tickFormatter={(value) => `${value}%`} tick={{ fontSize: 10, fill: "#9CA3AF" }} axisLine={false} tickLine={false} width={38} />
                <Tooltip
                  itemSorter={tooltipItemSorterByValueDesc}
                  formatter={(value, _name, item) => [
                    `${Number(value).toFixed(0)}%`,
                    chartSeries[Number(String(item.dataKey).replace("series_", ""))]?.name ?? "Brand",
                  ]}
                  labelFormatter={(label) => formatDate(String(label))}
                />
                {chartSeries.map((brand, index) => (
                  <Line
                    key={brand.name}
                    type="monotone"
                    dataKey={`series_${index}`}
                    name={brand.name}
                    stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
                    strokeWidth={brand.isOwnBrand ? 3 : 2}
                    dot={{ r: 3 }}
                    activeDot={{ r: 5 }}
                    connectNulls
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </>
        )}
      </div>

      {showComparison && competitors.length > 0 && (
        <div className="border-t border-gray-100 bg-gray-50/60 p-5">
          <label htmlFor="competitor-comparison" className="block text-xs font-semibold text-gray-700">
            Compare {ctx.brand_name} with
          </label>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <select
              id="competitor-comparison"
              value={selectedName}
              onChange={(event) => setSelectedName(event.target.value)}
              className="w-full max-w-sm rounded-lg border border-gray-300 bg-white px-3 py-2 text-sm text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-200"
            >
              {competitors.map((competitor) => (
                <option key={competitor.name} value={competitor.name}>{competitor.name}</option>
              ))}
            </select>
            {selected?.website ? (
              <a
                href={selected.website}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded-sm text-xs font-semibold text-blue-700 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                Open competitor website
              </a>
            ) : null}
          </div>

          {selected && comparison && (
            <div className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
              <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-gray-100">
                      <th className="px-4 py-3 text-left text-xs font-semibold text-gray-500">Metric</th>
                      <th className="px-4 py-3 text-center">
                        <span className="inline-flex items-center justify-center gap-1.5 text-xs font-semibold text-emerald-700">
                          <CompetitorFavicon name={ctx.brand_name} website={ctx.brand_site_url} size={14} />
                          {ctx.brand_name}
                        </span>
                      </th>
                      <th className="px-4 py-3 text-center">
                        <span className="inline-flex items-center justify-center gap-1.5 text-xs font-semibold text-gray-700">
                          <CompetitorFavicon name={selected.name} website={selected.website} size={14} />
                          <CompetitorLink name={selected.name} website={selected.website} />
                        </span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    <MetricRow label="Visibility" brandName={ctx.brand_name} competitorName={selected.name} brandValue={comparison.brandVisibility} competitorValue={comparison.competitorVisibility} />
                    <MetricRow label="Share of voice" brandName={ctx.brand_name} competitorName={selected.name} brandValue={comparison.brandSov} competitorValue={comparison.competitorSov} />
                    <MetricRow label="First in response" brandName={ctx.brand_name} competitorName={selected.name} brandValue={comparison.brandFirst} competitorValue={comparison.competitorFirst} />
                  </tbody>
                </table>
                <p className="px-4 pb-3 text-[10px] leading-relaxed text-gray-400">
                  “First” is measured across responses mentioning either brand ({comparison.firstTotal} responses).
                </p>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <h4 className="text-xs font-bold text-emerald-700">{ctx.brand_name} performs better</h4>
                  <ul className="mt-2 space-y-2">
                    {comparison.brandTopics.slice(0, 6).map((topic) => (
                      <li key={topic.topic} className="rounded-lg border border-emerald-100 bg-white px-3 py-2">
                        <p className="text-xs font-semibold text-gray-800">{topic.topic}</p>
                        <p className="mt-0.5 text-[10px] text-gray-400">{formatPct(topic.brandVisibility)} vs {formatPct(topic.competitorVisibility)}</p>
                      </li>
                    ))}
                    {!comparison.brandTopics.length && <li className="text-xs italic text-gray-400">No topic advantage detected.</li>}
                  </ul>
                </div>
                <div>
                  <h4 className="text-xs font-bold text-gray-700">
                    <CompetitorLink name={selected.name} website={selected.website} /> performs better
                  </h4>
                  <ul className="mt-2 space-y-2">
                    {comparison.competitorTopics.slice(0, 6).map((topic) => (
                      <li key={topic.topic} className="rounded-lg border border-gray-200 bg-white px-3 py-2">
                        <p className="text-xs font-semibold text-gray-800">{topic.topic}</p>
                        <p className="mt-0.5 text-[10px] text-gray-400">{formatPct(topic.competitorVisibility)} vs {formatPct(topic.brandVisibility)}</p>
                      </li>
                    ))}
                    {!comparison.competitorTopics.length && <li className="text-xs italic text-gray-400">No topic advantage detected.</li>}
                  </ul>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export function CompetitorComparisonDashboard({ auditId }: { auditId: string }) {
  const { ctx, loading, error, ensureScope } = usePromptPerformanceContext(auditId);
  const [selectedLocaleKey, setSelectedLocaleKey] = useState<string | null>(null);

  useEffect(() => {
    setSelectedLocaleKey(null);
  }, [auditId]);

  useEffect(() => {
    if (!ctx) return;
    setSelectedLocaleKey((prev) => (prev == null ? preferredInitialLocaleKey(ctx) : prev));
  }, [ctx]);

  const onLocaleChange = (key: string) => {
    setSelectedLocaleKey(key);
    void ensureScope(
      key === OVERALL_LOCALE_KEY ? { allLocales: true } : { locale: key },
    ).catch(() => undefined);
  };

  const localeKey = selectedLocaleKey || OVERALL_LOCALE_KEY;
  const live = liveProbeForLocale(ctx, localeKey);
  const localeCtx = ctx ? { ...ctx, live_probe: live } : null;
  const filterLocales = normalizePromptLocales(
    ctx?.prompt_locales as PromptLocale[] | undefined,
    ctx?.primary_market?.country ?? "",
    ctx?.primary_market?.country_id ?? "",
  );

  if (error) return <div className="alert-error m-6">{error}</div>;
  if (loading || !ctx || !localeCtx) {
    return <PageLoading />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[#0d0d0d]">Competitor visibility</h2>
        <p className="mt-1 text-sm text-gray-400">
          Compare your brand with the 10 most visible competitors found in AI responses.
        </p>
      </div>
      <PromptLocaleFilter
        locales={filterLocales}
        selectedKey={localeKey}
        onChange={onLocaleChange}
        hideIfSingle={false}
        ctx={ctx}
      />
      {!localeCtx.live_probe?.per_prompt?.length ? (
        <div className="px-6 py-16 text-center text-sm text-gray-400">
          {localeKey === OVERALL_LOCALE_KEY
            ? "No probe data yet. Run prompt probes to compare competitors."
            : "No probe data for this market/language yet. Re-run failed markets from the Prompts section."}
        </div>
      ) : (
        <>
          <BrandCompetitorVisibilityTable
            ctx={localeCtx}
            auditId={auditId}
            showTrackCompetitor
          />
          <BrandCompetitorVisibility auditId={auditId} ctx={localeCtx} />
        </>
      )}
    </div>
  );
}
