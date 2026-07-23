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
import type { ProbeHistoryResponse } from "../types";
import { legendItemSorterByLatestValueDesc } from "../lib/chartLegend";
import { tooltipItemSorterByValueDesc } from "../lib/chartTooltip";
import { fetchProbeHistory } from "../lib/probeHistoryFetch";
import { PLATFORM_META } from "./PlatformLogo";

const PLATFORMS = ["gemini", "openai", "google_aio", "claude"] as const;

function formatDate(value: string): string {
  try {
    return new Date(value).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return value;
  }
}

export default function SentimentOverTime({ auditId }: { auditId: string }) {
  const [history, setHistory] = useState<ProbeHistoryResponse["entries"]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

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

  const rows = useMemo(
    () => history
      .slice()
      .sort((a, b) => a.date.localeCompare(b.date))
      .map((entry) => {
        const row: Record<string, string | number | null> = { date: entry.date };
        for (const platform of PLATFORMS) {
          const score = entry.summary?.[platform]?.sentiment_score;
          row[platform] = score == null ? null : Math.round(score * 100);
        }
        return row;
      }),
    [history],
  );
  const activePlatforms = PLATFORMS.filter((platform) =>
    rows.some((row) => typeof row[platform] === "number"),
  );

  return (
    <div className="h-full rounded-2xl border border-gray-100 bg-white p-5 shadow-sm">
      <div className="mb-4">
        <h3 className="text-sm font-bold text-[#0d0d0d]">Sentiment Over Time</h3>
        <p className="mt-0.5 text-[11px] text-gray-400">
          Positive brand responses ÷ all responses mentioning the brand
        </p>
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
                  PLATFORM_META[String(name)]?.label ?? String(name),
                ]}
              />
              <Legend
                formatter={(value) => PLATFORM_META[String(value)]?.label ?? String(value)}
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
                  stroke={PLATFORM_META[platform]?.color ?? "#4B5563"}
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
