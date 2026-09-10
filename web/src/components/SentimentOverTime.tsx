import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PlatformDailySummary, ProbeHistoryResponse } from "../types";
import { legendItemSorterByLatestValueDesc } from "../lib/chartLegend";
import { tooltipItemSorterByValueDesc } from "../lib/chartTooltip";
import { fetchProbeHistory } from "../lib/probeHistoryFetch";
import { PLATFORM_META } from "./PlatformLogo";

const CHATBOT_PLATFORMS = ["gemini", "openai", "claude"] as const;
const ALL_PLATFORMS = [...CHATBOT_PLATFORMS, "google_aio"] as const;

function formatDate(value: string): string {
  try {
    return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return value;
  }
}

export default function SentimentOverTime({
  auditId,
  topic,
}: {
  auditId: string;
  topic?: string;
}) {
  const [history, setHistory] = useState<ProbeHistoryResponse["entries"]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selectedPlatform, setSelectedPlatform] = useState("all");

  useEffect(() => {
    if (!auditId) return;
    let cancelled = false;
    setLoading(true);
    setError(false);
    fetchProbeHistory(auditId)
      .then((response) => {
        if (!cancelled) setHistory(response.entries);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [auditId]);

  const summaryFor = (entry: ProbeHistoryResponse["entries"][number], platform: string): PlatformDailySummary | undefined => {
    const topicSummary = topic && entry.topic_summaries?.[topic]?.[platform as keyof typeof entry.summary];
    const summary = entry.summary?.[platform as keyof typeof entry.summary];
    return (topicSummary && !Array.isArray(topicSummary) ? topicSummary : undefined)
      || (summary && !Array.isArray(summary) ? summary : undefined);
  };
  const platformOptions = ALL_PLATFORMS.filter((platform) =>
    history.some((entry) => Boolean(
      summaryFor(entry, platform),
    )),
  );
  const rows = useMemo(
    () => history
      .slice()
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((entry) => {
        const row: Record<string, string | number | null> = { date: entry.date };
        const platforms = selectedPlatform === "all" ? ALL_PLATFORMS : [selectedPlatform];
        const summaries = platforms
          .map((platform) => summaryFor(entry, platform))
          .filter(Boolean);
        if (selectedPlatform === "all") {
          for (const group of [
            { key: "chatbots", platforms: CHATBOT_PLATFORMS },
            { key: "overviews", platforms: ["google_aio"] as const },
          ]) {
            const groupSummaries = group.platforms.map((platform) => summaryFor(entry, platform)).filter(Boolean);
            const mentions = groupSummaries.reduce((sum, summary) => sum + Number(summary?.brand_mentioned_count ?? 0), 0);
            const positive = groupSummaries.reduce((sum, summary) => sum + Number(summary?.positive_brand_mention_count ?? 0), 0);
            row[group.key] = mentions > 0 ? Math.round((positive / mentions) * 100) : null;
          }
        } else {
          const key = selectedPlatform;
          const summary = summaries[0];
          const platformScore = summary?.sentiment_score;
          row[key] = platformScore == null ? null : Math.round(Number(platformScore) * 100);
        }
        return row;
      }),
    [history, selectedPlatform, topic],
  );
  const activeKeys = selectedPlatform === "all" ? ["chatbots", "overviews"] : [selectedPlatform];
  const activePlatforms = activeKeys.filter((key) => rows.some((row) => typeof row[key] === "number"));

  return (
    <div className="h-full rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
      <div className="mb-4">
        <h3 className="text-sm font-bold text-[#0d0d0d]">Sentiment Over Time</h3>
        <p className="mt-0.5 text-[11px] text-gray-400">
          Positive brand responses ÷ all responses mentioning the brand
        </p>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-1.5">
        <select
          value={selectedPlatform}
          onChange={(event) => setSelectedPlatform(event.target.value)}
          className="rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[10px] font-semibold text-gray-500"
        >
          <option value="all">Chatbots vs AI Overviews</option>
          {platformOptions.map((platform) => (
            <option key={platform} value={platform}>{PLATFORM_META[platform]?.label ?? platform}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <div className="flex h-52 items-center justify-center text-sm text-gray-400">Loading sentiment history…</div>
      ) : error ? (
        <div className="flex h-52 items-center justify-center text-sm text-red-500">Unable to load sentiment history.</div>
      ) : activePlatforms.length === 0 ? (
        <div className="flex h-52 items-center justify-center text-center text-sm text-gray-400">
          No brand sentiment history is available yet.
        </div>
      ) : (
        <>
          {rows.length < 2 && (
            <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-[11px] text-amber-600">
              Daily re-runs will add trend points here.
            </div>
          )}
          <ResponsiveContainer width="100%" height={236}>
            <LineChart data={rows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid stroke="#f0f0f0" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="date"
                tickFormatter={formatDate}
                tick={{ fontSize: 10, fill: "#aaa" }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                domain={[0, 100]}
                tickFormatter={(value) => `${value}%`}
                tick={{ fontSize: 10, fill: "#aaa" }}
                axisLine={false}
                tickLine={false}
                width={36}
              />
              <Tooltip
                itemSorter={tooltipItemSorterByValueDesc}
                labelFormatter={(value) => formatDate(String(value))}
                formatter={(value, name) => [
                  `${Number(value)}%`,
                  String(name) === "chatbots" ? "Chatbots" : String(name) === "overviews" ? "AI Overviews" : PLATFORM_META[String(name)]?.label ?? String(name),
                ]}
              />
              <Legend
                formatter={(value) => String(value) === "chatbots"
                  ? "Chatbots"
                  : String(value) === "overviews"
                    ? "AI Overviews"
                    : PLATFORM_META[String(value)]?.label ?? String(value)}
                iconType="circle"
                iconSize={8}
                wrapperStyle={{ fontSize: 10, paddingTop: 8 }}
                itemSorter={legendItemSorterByLatestValueDesc(rows)}
              />
              {activePlatforms.map((platform) => (
                <Line
                  key={platform}
                  type="monotone"
                  dataKey={platform}
                  stroke={platform === "chatbots" ? "#8B5CF6" : platform === "overviews" ? "#EA4335" : PLATFORM_META[platform]?.color ?? "#4B5563"}
                  strokeWidth={2}
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
  );
}
