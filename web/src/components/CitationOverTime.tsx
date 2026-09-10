import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CitationHistoryResponse } from "../types";
import { legendItemSorterByLatestValueDesc } from "../lib/chartLegend";
import { sortTooltipItemsByValueDesc } from "../lib/chartTooltip";
import { fetchCitationHistory } from "../lib/probeHistoryFetch";

const PALETTE = [
  "#4285F4", "#EA4335", "#FBBC05", "#34A853",
  "#7B68EE", "#FF7043", "#00BCD4", "#E91E63",
  "#8BC34A", "#FF9800",
];

function formatDate(d: string): string {
  try {
    return new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return d;
  }
}

interface ChartRow {
  date: string;
  [domain: string]: number | string;
}

function buildChartData(
  rows: { date: string; domain: string; frequency: number }[],
  topDomains: string[],
): ChartRow[] {
  const byDate = new Map<string, ChartRow>();
  for (const row of rows) {
    if (!topDomains.includes(row.domain)) continue;
    if (!byDate.has(row.date)) byDate.set(row.date, { date: row.date });
    const entry = byDate.get(row.date)!;
    entry[row.domain] = (Number(entry[row.domain] ?? 0)) + row.frequency;
  }
  return Array.from(byDate.values()).sort((a, b) =>
    String(a.date).localeCompare(String(b.date))
  );
}

function getTopDomains(
  rows: { date: string; domain: string; frequency: number }[],
  n = 10,
): string[] {
  const totals = new Map<string, number>();
  for (const r of rows) {
    if (r.domain) totals.set(r.domain, (totals.get(r.domain) ?? 0) + r.frequency);
  }
  return Array.from(totals.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([d]) => d);
}

interface TooltipPayloadItem {
  name: string;
  value: number;
  fill: string;
  dataKey: string;
}

function CustomTooltip({ active, payload, label }: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const items = sortTooltipItemsByValueDesc(payload.filter((p) => p.value > 0));
  return (
    <div className="bg-white rounded-xl shadow-lg border border-gray-100 p-3 min-w-[160px]">
      <p className="text-[11px] font-semibold text-gray-500 mb-2">{formatDate(String(label))}</p>
      {items.map((item) => (
        <div key={item.dataKey} className="flex items-center justify-between gap-3 mb-0.5">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm inline-block" style={{ background: item.fill }} />
            <span className="text-[11px] text-gray-600 max-w-[120px] truncate">{item.name}</span>
          </div>
          <span className="text-[11px] font-bold text-gray-800">{item.value}</span>
        </div>
      ))}
    </div>
  );
}

export default function CitationOverTime({
  auditId,
  selectedDomain,
  allowedDomains,
}: {
  auditId: string;
  /** If provided, highlight only this domain's trend */
  selectedDomain?: string | null;
  /** If provided, exclude entity/vendor domains that are not information sources. */
  allowedDomains?: string[];
}) {
  const [raw, setRaw] = useState<CitationHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [topN, setTopN] = useState(10);

  useEffect(() => {
    if (!auditId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchCitationHistory(auditId)
      .then((resp) => {
        if (cancelled) return;
        setRaw(resp);
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

  const topDomains = useMemo(() => {
    if (!raw) return [];
    const allowed = allowedDomains ? new Set(allowedDomains) : null;
    const sourceRows = allowed ? raw.rows.filter((row) => allowed.has(row.domain)) : raw.rows;
    const domains = selectedDomain ? [selectedDomain] : getTopDomains(sourceRows, topN);
    return domains;
  }, [raw, topN, selectedDomain, allowedDomains]);

  const chartData = useMemo(() => {
    if (!raw) return [];
    return buildChartData(raw.rows, topDomains);
  }, [raw, topDomains]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-40 text-gray-400 text-sm">
        Loading citation history…
      </div>
    );
  }

  if (error) {
    return <div className="text-red-500 text-sm p-4">Failed to load citation history: {error}</div>;
  }

  const hasMultipleDates = (raw?.dates?.length ?? 0) > 1;

  return (
    <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-bold text-[#0d0d0d]">
            {selectedDomain ? `Citations: ${selectedDomain}` : "Citation Frequency Over Time"}
          </h3>
          <p className="text-[11px] text-gray-400 mt-0.5">
            {selectedDomain
              ? "Daily citation count for this domain"
              : "Daily citation frequency by domain"}
          </p>
        </div>
        {!selectedDomain && (
          <select
            value={topN}
            onChange={(e) => setTopN(Number(e.target.value))}
            className="text-[10px] font-semibold px-2 py-1 rounded-lg border border-gray-200 bg-gray-50 text-gray-600"
          >
            {[5, 8, 10, 15].map((n) => (
              <option key={n} value={n}>
                Top {n} domains
              </option>
            ))}
          </select>
        )}
      </div>

      {!hasMultipleDates && (
        <div className="text-[11px] text-amber-600 bg-amber-50 rounded-lg px-3 py-2 mb-3">
          Only one data point. Run daily re-runs to see citation trends over time.
        </div>
      )}

      <ResponsiveContainer width="100%" height={260}>
        <BarChart data={chartData} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDate}
            tick={{ fontSize: 10, fill: "#aaa" }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fontSize: 10, fill: "#aaa" }}
            axisLine={false}
            tickLine={false}
            width={28}
          />
          <Tooltip content={<CustomTooltip />} />
          {!selectedDomain && (
            <Legend
              iconType="square"
              iconSize={8}
              wrapperStyle={{ fontSize: 10, paddingTop: 8 }}
              itemSorter={legendItemSorterByLatestValueDesc(chartData)}
            />
          )}
          {selectedDomain
            ? (
              <Bar dataKey={selectedDomain} name={selectedDomain} fill={PALETTE[0]} radius={[3, 3, 0, 0]} maxBarSize={40}>
                {chartData.map((_entry, idx) => (
                  <Cell key={idx} fill={PALETTE[0]} />
                ))}
              </Bar>
            )
            : topDomains.map((domain, i) => (
              <Bar
                key={domain}
                dataKey={domain}
                name={domain}
                stackId="a"
                fill={PALETTE[i % PALETTE.length]}
                radius={i === topDomains.length - 1 ? [3, 3, 0, 0] : [0, 0, 0, 0]}
                maxBarSize={40}
              />
            ))
          }
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
