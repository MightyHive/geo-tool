/**
 * Topics — AI visibility metrics compared across product/service topics.
 */

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Layers3 } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { PromptLocaleFilter, liveProbeForLocale } from "./PromptLocaleFilter";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import { normalizePromptLocales, type PromptLocale } from "../lib/promptLocales";
import { OVERALL_LOCALE_KEY } from "../lib/localeProbeView";
import { preferredInitialLocaleKey } from "../lib/defaultLocaleView";
import { filterContextByTopic, listProbeTopics } from "../lib/promptCategoryGrouping";
import {
  AI_OVERVIEW_PLATFORMS,
  CHATBOT_PLATFORMS,
  computeVisibilityMetrics,
  computeVisibilityMetricsForPlatforms,
  type VisibilityMetrics,
} from "../lib/visibilityMetrics";
import { tooltipItemSorterByValueDesc } from "../lib/chartTooltip";
import SovVisibilityScatter, { topicSovVisibilityPoints } from "./SovVisibilityScatter";

function formatPct(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value.toFixed(1)}%`;
}

function metricOrNull(metrics: VisibilityMetrics | null): {
  visibility: number | null;
  sov: number | null;
  responses: number;
} {
  if (!metrics || metrics.promptCount <= 0) {
    return { visibility: null, sov: null, responses: 0 };
  }
  return {
    visibility: metrics.visibilityPct,
    sov: metrics.sovPct,
    responses: metrics.promptCount,
  };
}

type TopicRow = {
  topic: string;
  promptCount: number;
  overall: ReturnType<typeof metricOrNull>;
  chatbot: ReturnType<typeof metricOrNull>;
  overview: ReturnType<typeof metricOrNull>;
};

export function TopicsVisibilitySection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const { ctx, loading, error, ensureScope } = usePromptPerformanceContext(auditDirOrSlug);
  const [selectedLocaleKey, setSelectedLocaleKey] = useState<string | null>(null);

  useEffect(() => {
    setSelectedLocaleKey(null);
  }, [auditDirOrSlug]);

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
  const localeCtx = useMemo(() => {
    if (!ctx) return null;
    return { ...ctx, live_probe: live };
  }, [ctx, live]);

  const topicRows = useMemo<TopicRow[]>(() => {
    if (!localeCtx) return [];
    const topics = listProbeTopics(localeCtx);
    return topics
      .map((topic) => {
        const filtered = filterContextByTopic(localeCtx, topic);
        if (!filtered) {
          return {
            topic,
            promptCount: 0,
            overall: metricOrNull(null),
            chatbot: metricOrNull(null),
            overview: metricOrNull(null),
          };
        }
        return {
          topic,
          promptCount: filtered.live_probe?.per_prompt?.length ?? 0,
          overall: metricOrNull(computeVisibilityMetrics(filtered)),
          chatbot: metricOrNull(
            computeVisibilityMetricsForPlatforms(filtered, CHATBOT_PLATFORMS),
          ),
          overview: metricOrNull(
            computeVisibilityMetricsForPlatforms(filtered, AI_OVERVIEW_PLATFORMS),
          ),
        };
      })
      .filter((row) => row.promptCount > 0)
      .sort(
        (left, right) =>
          (right.overall.visibility ?? -1) - (left.overall.visibility ?? -1)
          || left.topic.localeCompare(right.topic),
      );
  }, [localeCtx]);

  const chartData = useMemo(
    () =>
      topicRows.map((row) => ({
        topic: row.topic.length > 28 ? `${row.topic.slice(0, 26)}…` : row.topic,
        fullTopic: row.topic,
        chatbots: Number((row.chatbot.visibility ?? 0).toFixed(1)),
        overviews: Number((row.overview.visibility ?? 0).toFixed(1)),
      })),
    [topicRows],
  );

  const filterLocales = normalizePromptLocales(
    ctx?.prompt_locales as PromptLocale[] | undefined,
    ctx?.primary_market?.country ?? "",
    ctx?.primary_market?.country_id ?? "",
  );

  if (error) return <div className="alert-error m-6">{error}</div>;
  if (loading || !ctx || !localeCtx) return <PageLoading />;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[#0d0d0d]">Topics</h2>
        <p className="mt-1 text-sm text-gray-400">
          Compare AI visibility and share of voice across product and service topics from your prompts.
        </p>
      </div>

      <PromptLocaleFilter
        locales={filterLocales}
        selectedKey={localeKey}
        onChange={onLocaleChange}
        hideIfSingle={false}
        ctx={ctx}
      />

      {!topicRows.length ? (
        <div className="flex flex-col items-center justify-center px-6 py-16 text-center">
          <Layers3 className="mb-3 h-10 w-10 text-gray-300" />
          <p className="text-sm text-gray-400">
            {localeKey === OVERALL_LOCALE_KEY
              ? "No topic-tagged probe data yet. Run prompts to compare visibility by topic."
              : "No probe data for this market/language yet. Re-run failed markets from the Prompts section."}
          </p>
        </div>
      ) : (
        <>
          <SovVisibilityScatter
            title="SOV vs Visibility: Topics"
            subtitle="Your brand by product or service topic"
            dimensionLabel="Topic"
            points={topicSovVisibilityPoints(localeCtx)}
            emptyMessage="No topic data available yet."
          />

          <div className="rounded-2xl border border-gray-200 bg-white p-5">
            <div className="mb-4">
              <h3 className="text-sm font-semibold text-[#0d0d0d]">Visibility by topic</h3>
              <p className="mt-1 text-[11px] text-gray-400">
                Brand mention rate across Chatbots vs AI Overviews for each topic.
              </p>
            </div>
            <ResponsiveContainer width="100%" height={Math.max(220, topicRows.length * 36)}>
              <BarChart
                data={chartData}
                layout="vertical"
                margin={{ top: 4, right: 16, bottom: 4, left: 8 }}
              >
                <CartesianGrid stroke="#f0f0f0" strokeDasharray="3 3" horizontal={false} />
                <XAxis
                  type="number"
                  domain={[0, 100]}
                  tickFormatter={(value) => `${value}%`}
                  tick={{ fontSize: 10, fill: "#aaa" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="topic"
                  width={140}
                  tick={{ fontSize: 11, fill: "#4b5563" }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  cursor={{ fill: "#f9fafb" }}
                  itemSorter={tooltipItemSorterByValueDesc}
                  formatter={(value) => `${Number(value ?? 0).toFixed(1)}%`}
                  labelFormatter={(_, payload) =>
                    String(payload?.[0]?.payload?.fullTopic ?? "")
                  }
                  contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }}
                />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="chatbots" name="Chatbots" fill="#8B5CF6" radius={[0, 4, 4, 0]} barSize={10} />
                <Bar dataKey="overviews" name="AI Overviews" fill="#EA4335" radius={[0, 4, 4, 0]} barSize={10} />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-xs">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="px-4 py-2.5 text-left text-[9px] font-semibold uppercase tracking-wide text-gray-400">
                      Topic
                    </th>
                    <th className="px-3 py-2.5 text-right text-[9px] font-semibold uppercase tracking-wide text-gray-400">
                      Prompts
                    </th>
                    <th
                      colSpan={2}
                      className="border-l border-gray-200 px-3 py-2.5 text-center text-[9px] font-semibold uppercase tracking-wide text-violet-600"
                    >
                      Chatbots
                    </th>
                    <th
                      colSpan={2}
                      className="border-l border-gray-200 px-3 py-2.5 text-center text-[9px] font-semibold uppercase tracking-wide text-red-600"
                    >
                      AI Overviews
                    </th>
                    <th
                      colSpan={2}
                      className="border-l border-gray-200 px-3 py-2.5 text-center text-[9px] font-semibold uppercase tracking-wide text-gray-500"
                    >
                      Overall
                    </th>
                  </tr>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    <th className="px-4 py-1.5" />
                    <th className="px-3 py-1.5" />
                    {["Visibility", "SOV", "Visibility", "SOV", "Visibility", "SOV"].map((label, index) => (
                      <th
                        key={`${label}-${index}`}
                        className={`px-3 py-1.5 text-right text-[9px] font-semibold uppercase tracking-wide text-gray-400 ${
                          index === 0 || index === 2 || index === 4 ? "border-l border-gray-200" : ""
                        }`}
                      >
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {topicRows.map((row) => (
                    <tr key={row.topic} className="border-b border-gray-50 last:border-0">
                      <td className="px-4 py-2.5 font-semibold text-[#0d0d0d]">{row.topic}</td>
                      <td className="px-3 py-2.5 text-right tabular-nums text-gray-500">{row.promptCount}</td>
                      <td className="border-l border-gray-200 px-3 py-2.5 text-right font-semibold tabular-nums text-gray-700">
                        {formatPct(row.chatbot.visibility)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-semibold tabular-nums text-gray-700">
                        {formatPct(row.chatbot.sov)}
                      </td>
                      <td className="border-l border-gray-200 px-3 py-2.5 text-right font-semibold tabular-nums text-gray-700">
                        {formatPct(row.overview.visibility)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-semibold tabular-nums text-gray-700">
                        {formatPct(row.overview.sov)}
                      </td>
                      <td className="border-l border-gray-200 px-3 py-2.5 text-right font-semibold tabular-nums text-gray-700">
                        {formatPct(row.overall.visibility)}
                      </td>
                      <td className="px-3 py-2.5 text-right font-semibold tabular-nums text-gray-700">
                        {formatPct(row.overall.sov)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
