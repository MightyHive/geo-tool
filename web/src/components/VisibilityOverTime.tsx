import { useEffect, useState } from "react";
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
import type { ProbeHistoryEntry } from "../types";
import { auditSlug } from "../lib/auditPath";
import { legendItemSorterByLatestValueDesc } from "../lib/chartLegend";
import { sortTooltipItemsByValueDesc } from "../lib/chartTooltip";
import { fetchProbeHistory } from "../lib/probeHistoryFetch";
import { PLATFORM_META } from "./PlatformLogo";

const PLATFORM_LABELS: Record<string, string> = {
  gemini: "Gemini",
  openai: "OpenAI",
  google_aio: "Google AI",
  claude: "Claude",
};

const BRAND_STYLE = "solid";
const COMP_STYLE = "dashed";

interface ChartRow {
  date: string;
  [key: string]: number | string;
}

function buildChartRows(entries: ProbeHistoryEntry[]): ChartRow[] {
  return entries
    .slice()
    .sort((a, b) => a.date.localeCompare(b.date))
    .map((entry) => {
      const row: ChartRow = { date: entry.date };
      for (const plat of ["gemini", "openai", "google_aio", "claude"] as const) {
        const s = entry.summary?.[plat];
        if (s) {
          row[`${plat}_brand`] = Math.round((s.brand_visibility ?? 0) * 100);
          row[`${plat}_comp`] = Math.round((s.avg_competitor_visibility ?? 0) * 100);
        }
      }
      return row;
    });
}

function formatDate(d: string): string {
  try {
    return new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return d;
  }
}

interface TooltipPayloadItem {
  name: string;
  value: number;
  color: string;
  dataKey: string;
}

function CustomTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const items = sortTooltipItemsByValueDesc(payload);
  return (
    <div className="bg-white rounded-xl shadow-lg border border-gray-100 p-3 min-w-[160px]">
      <p className="text-[11px] font-semibold text-gray-500 mb-2">{formatDate(String(label))}</p>
      {items.map((item) => (
        <div key={item.dataKey} className="flex items-center justify-between gap-3 mb-0.5">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: item.color }} />
            <span className="text-[11px] text-gray-600">{item.name}</span>
          </div>
          <span className="text-[11px] font-bold text-gray-800">{item.value}%</span>
        </div>
      ))}
    </div>
  );
}

export default function VisibilityOverTime({
  auditId,
  brandLabel,
}: {
  auditId: string;
  brandLabel?: string;
}) {
  const [data, setData] = useState<ProbeHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activePlatforms, setActivePlatforms] = useState<string[]>([
    "gemini",
    "openai",
    "google_aio",
    "claude",
  ]);
  const [showCompetitor, setShowCompetitor] = useState(true);
  const [rerunning, setRerunning] = useState(false);

  useEffect(() => {
    if (!auditId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchProbeHistory(auditId)
      .then((resp) => {
        if (cancelled) return;
        setData(resp.entries);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [auditId]);

  const chartRows = buildChartRows(data);
  const hasMultipleDates = chartRows.length > 1;

  const triggerRerun = () => {
    const slug = auditSlug(auditId);
    setRerunning(true);
    fetch(`/api/audits/${encodeURIComponent(slug)}/re-run`, { method: "POST" })
      .then(() => {
        setTimeout(() => {
          setRerunning(false);
          // Reload after a brief delay (re-run is async)
          fetchProbeHistory(slug, { force: true })
            .then((resp) => setData(resp.entries));
        }, 2000);
      })
      .catch(() => setRerunning(false));
  };

  const platforms = ["gemini", "openai", "google_aio", "claude"].filter((p) =>
    data.some((e) => e.summary?.[p as keyof typeof e.summary])
  );

  const colorForPlatform = (platform: string, competitor = false): string => {
    const meta = PLATFORM_META[platform];
    return competitor ? (meta?.lightColor ?? "#D1D5DB") : (meta?.color ?? "#4B5563");
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400 text-sm">
        Loading visibility history…
      </div>
    );
  }

  if (error) {
    return (
      <div className="text-red-500 text-sm p-4">Failed to load history: {error}</div>
    );
  }

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-bold text-[#0d0d0d]">Visibility Over Time</h3>
          <p className="text-[11px] text-gray-400 mt-0.5">
            Daily brand visibility % and avg. competitor visibility by platform
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setShowCompetitor((v) => !v)}
            className={`text-[10px] font-semibold px-2.5 py-1 rounded-full border transition-colors ${
              showCompetitor ? "bg-blue-50 text-blue-600 border-blue-200" : "bg-gray-50 text-gray-400 border-gray-200"
            }`}
          >
            Competitor avg.
          </button>
          <button
            type="button"
            onClick={triggerRerun}
            disabled={rerunning}
            className="text-[10px] font-semibold px-2.5 py-1 rounded-full border border-gray-200 bg-gray-50 text-gray-500 hover:bg-gray-100 transition-colors disabled:opacity-50"
          >
            {rerunning ? "Queued…" : "Re-run today"}
          </button>
        </div>
      </div>

      {/* Platform toggles */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        {platforms.map((p) => {
          const active = activePlatforms.includes(p);
          const color = colorForPlatform(p);
          return (
            <button
              key={p}
              type="button"
              onClick={() =>
                setActivePlatforms((prev) =>
                  active ? prev.filter((x) => x !== p) : [...prev, p]
                )
              }
              className="text-[10px] font-semibold px-2.5 py-1 rounded-full border transition-colors"
              style={
                active
                  ? { background: color + "18", color, borderColor: color + "44" }
                  : { background: "#f5f5f5", color: "#aaa", borderColor: "#e5e5e5" }
              }
            >
              {PLATFORM_LABELS[p] ?? p}
            </button>
          );
        })}
      </div>

      {!hasMultipleDates && (
        <div className="text-[11px] text-amber-600 bg-amber-50 rounded-lg px-3 py-2 mb-3">
          Only one data point available. Run daily re-runs to see the trend over time.
        </div>
      )}

      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={chartRows} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            tick={{ fontSize: 10, fill: "#aaa" }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tickFormatter={(v) => `${v}%`}
            tick={{ fontSize: 10, fill: "#aaa" }}
            axisLine={false}
            tickLine={false}
            domain={[0, 100]}
            width={36}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend
            iconType="circle"
            iconSize={8}
            wrapperStyle={{ fontSize: 10, paddingTop: 8 }}
            itemSorter={legendItemSorterByLatestValueDesc(chartRows)}
          />
          {activePlatforms.flatMap((p) => {
            const brandColor = colorForPlatform(p);
            const competitorColor = colorForPlatform(p, true);
            const lines = [
              <Line
                key={`${p}_brand`}
                type="monotone"
                dataKey={`${p}_brand`}
                name={`${PLATFORM_LABELS[p] ?? p} — ${brandLabel ?? "Brand"}`}
                stroke={brandColor}
                strokeWidth={2}
                dot={{ r: 3, fill: brandColor }}
                activeDot={{ r: 5 }}
                strokeDasharray={BRAND_STYLE === "solid" ? undefined : "4 2"}
                connectNulls
              />,
            ];
            if (showCompetitor) {
              lines.push(
                <Line
                  key={`${p}_comp`}
                  type="monotone"
                  dataKey={`${p}_comp`}
                  name={`${PLATFORM_LABELS[p] ?? p} — Competitors`}
                  stroke={competitorColor}
                  strokeWidth={2}
                  strokeDasharray={COMP_STYLE === "dashed" ? "4 2" : undefined}
                  dot={false}
                  strokeOpacity={1}
                  connectNulls
                />
              );
            }
            return lines;
          })}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
