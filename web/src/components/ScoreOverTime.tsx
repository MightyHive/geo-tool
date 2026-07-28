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
import type { ScoreHistoryEntry, ScoreHistoryResponse } from "../types";
import { auditSlug } from "../lib/auditPath";
import { legendItemSorterByLatestValueDesc } from "../lib/chartLegend";
import { tooltipItemSorterByValueDesc } from "../lib/chartTooltip";
import { formatReportScore } from "../lib/reportScore";

export type ScoreHistoryMetric =
  | "overall"
  | "ai_visibility"
  | "technical_setup"
  | "content_structure";

const METRIC_LABELS: Record<ScoreHistoryMetric, string> = {
  overall: "Overall",
  ai_visibility: "AI Visibility",
  technical_setup: "Technical Setup",
  content_structure: "Content Quality",
};

const BRAND_COLOR = "#0984e3";
const PILLAR_COLORS: Record<ScoreHistoryMetric, string> = {
  overall: BRAND_COLOR,
  ai_visibility: "#6c5ce7",
  technical_setup: "#00b894",
  content_structure: "#e17055",
};
const COMPETITOR_PALETTE = ["#e17055", "#00b894", "#6c5ce7", "#fdcb6e", "#636e72", "#00cec9"];

function formatDate(d: string): string {
  try {
    return new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return d;
  }
}

type ScorePoint = Pick<
  ScoreHistoryEntry,
  "overall" | "ai_visibility" | "technical_setup" | "content_structure"
>;

function metricValue(entry: ScorePoint, metric: ScoreHistoryMetric): number | null {
  const raw = entry[metric];
  return typeof raw === "number" && Number.isFinite(raw) ? Math.round(raw) : null;
}

/** Legend/tooltip label for the brand series — never a bare "Brand". */
function formatBrandSeriesLabel(brandLabel: string, metricLabel?: string): string {
  const raw = brandLabel.trim();
  const isGeneric = !raw || /^brand$/i.test(raw) || /^your\s*brand$/i.test(raw);
  const brandPart = isGeneric ? "Your Brand" : `${raw} (Your Brand)`;
  return metricLabel ? `${brandPart} · ${metricLabel}` : brandPart;
}

interface ScoreOverTimeProps {
  auditId: string;
  brandLabel?: string;
  /** Which pillar series to plot for the brand (and competitors when shown). */
  metrics?: ScoreHistoryMetric[];
  title?: string;
  description?: string;
  /** When true, never plot competitor series (brand-only chart). */
  hideCompetitors?: boolean;
  /**
   * Show a metric toggle (Overall / AI Visibility / Technical / Content).
   * Plots brand + competitors for the selected metric.
   */
  metricToggle?: boolean;
  /** Initial metric when `metricToggle` is enabled. */
  defaultMetric?: ScoreHistoryMetric;
}

export default function ScoreOverTime({
  auditId,
  brandLabel = "Your Brand",
  metrics = ["overall", "ai_visibility", "technical_setup", "content_structure"],
  title = "Scores over time",
  description = "Tracked from automated and manual audit refreshes.",
  hideCompetitors = false,
  metricToggle = false,
  defaultMetric = "overall",
}: ScoreOverTimeProps) {
  const [entries, setEntries] = useState<ScoreHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedMetric, setSelectedMetric] = useState<ScoreHistoryMetric>(defaultMetric);

  useEffect(() => {
    setSelectedMetric(defaultMetric);
  }, [defaultMetric]);

  useEffect(() => {
    if (!auditId) return;
    const slug = auditSlug(auditId);
    setLoading(true);
    setError(null);
    fetch(`/api/audits/${encodeURIComponent(slug)}/score-history`, { credentials: "include" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`Failed to load score history (${r.status})`);
        return r.json() as Promise<ScoreHistoryResponse>;
      })
      .then((resp) => {
        setEntries(Array.isArray(resp.entries) ? resp.entries : []);
        setLoading(false);
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      });
  }, [auditId]);

  const activeMetrics = useMemo((): ScoreHistoryMetric[] => {
    if (metricToggle) return [selectedMetric];
    return metrics;
  }, [metricToggle, selectedMetric, metrics]);

  const competitorNames = useMemo(() => {
    if (hideCompetitors) return [] as string[];
    const names = new Set<string>();
    for (const entry of entries) {
      for (const row of entry.competitors ?? []) {
        const name = String(row.name || "").trim();
        if (name) names.add(name);
      }
    }
    return Array.from(names).slice(0, 5);
  }, [entries, hideCompetitors]);

  const chartRows = useMemo(() => {
    return entries
      .slice()
      .sort((a, b) => {
        const aTime = Date.parse(a.date);
        const bTime = Date.parse(b.date);
        if (Number.isFinite(aTime) && Number.isFinite(bTime)) {
          return aTime - bTime;
        }
        return String(a.date).localeCompare(String(b.date));
      })
      .map((entry) => {
        const row: Record<string, string | number | null> = { date: entry.date };
        for (const metric of activeMetrics) {
          row[`brand_${metric}`] = metricValue(entry, metric);
        }
        const competitorMetric: ScoreHistoryMetric =
          activeMetrics.length === 1 ? activeMetrics[0] : "overall";
        for (const name of competitorNames) {
          const comp = (entry.competitors ?? []).find((c) => c.name === name);
          row[`comp_${name}`] = comp ? metricValue(comp, competitorMetric) : null;
        }
        return row;
      });
  }, [entries, activeMetrics, competitorNames]);

  const hasMultipleDates = chartRows.length > 1;
  const toggleOptions: ScoreHistoryMetric[] = [
    "overall",
    "ai_visibility",
    "technical_setup",
    "content_structure",
  ];

  if (loading) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <p className="text-sm text-gray-400">Loading score history…</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <p className="text-sm text-amber-700">{error}</p>
      </div>
    );
  }

  if (!chartRows.length) {
    return (
      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h3 className="text-sm font-semibold text-[#0d0d0d] mb-1">{title}</h3>
        <p className="text-xs text-gray-500">
          Score history will appear after the next automated or manual refresh.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h3 className="text-sm font-semibold text-[#0d0d0d]">{title}</h3>
          <p className="text-xs text-gray-500 mt-0.5">{description}</p>
          {!hasMultipleDates ? (
            <p className="text-[11px] text-amber-700 mt-2">
              Only one snapshot so far — daily prompt and weekly crawl refreshes will build the trend.
            </p>
          ) : null}
        </div>
        {metricToggle ? (
          <div
            className="flex flex-wrap gap-1 rounded-lg bg-gray-50 p-1"
            role="group"
            aria-label="Score metric"
          >
            {toggleOptions.map((metric) => {
              const active = selectedMetric === metric;
              return (
                <button
                  key={metric}
                  type="button"
                  onClick={() => setSelectedMetric(metric)}
                  className={`rounded-md px-2.5 py-1 text-[11px] font-semibold transition-colors ${
                    active
                      ? "bg-white text-[#0d0d0d] shadow-sm"
                      : "text-gray-500 hover:text-gray-700"
                  }`}
                >
                  {METRIC_LABELS[metric]}
                </button>
              );
            })}
          </div>
        ) : null}
      </div>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartRows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
            <XAxis
              dataKey="date"
              tickFormatter={formatDate}
              tick={{ fontSize: 11, fill: "#9ca3af" }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[0, 100]}
              tick={{ fontSize: 11, fill: "#9ca3af" }}
              axisLine={false}
              tickLine={false}
              width={32}
            />
            <Tooltip
              itemSorter={tooltipItemSorterByValueDesc}
              labelFormatter={(label) => formatDate(String(label))}
              formatter={(value, name) => [
                value == null || value === "" || !Number.isFinite(Number(value))
                  ? "—"
                  : formatReportScore(Number(value)),
                String(name ?? ""),
              ]}
            />
            <Legend
              wrapperStyle={{ fontSize: 11 }}
              itemSorter={legendItemSorterByLatestValueDesc(chartRows)}
            />
            {activeMetrics.map((metric) => (
              <Line
                key={`brand_${metric}`}
                type="monotone"
                dataKey={`brand_${metric}`}
                name={
                  activeMetrics.length === 1
                    ? formatBrandSeriesLabel(brandLabel)
                    : formatBrandSeriesLabel(brandLabel, METRIC_LABELS[metric])
                }
                stroke={
                  activeMetrics.length === 1
                    ? BRAND_COLOR
                    : PILLAR_COLORS[metric]
                }
                strokeWidth={2.5}
                dot={{ r: 3 }}
                connectNulls
              />
            ))}
            {competitorNames.map((name, index) => (
              <Line
                key={`comp_${name}`}
                type="monotone"
                dataKey={`comp_${name}`}
                name={name}
                stroke={COMPETITOR_PALETTE[index % COMPETITOR_PALETTE.length]}
                strokeWidth={1.75}
                strokeDasharray="5 4"
                dot={{ r: 2 }}
                connectNulls
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {!hideCompetitors && competitorNames.length === 0 ? (
        <p className="text-[11px] text-gray-400 mt-2">
          Competitor score lines appear after a competitor crawl has completed.
        </p>
      ) : null}
    </div>
  );
}
