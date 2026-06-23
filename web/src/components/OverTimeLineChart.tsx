import { useMemo } from "react";

export interface OverTimePoint {
  date: string;
  brand_share_pct?: number;
  competitor_avg_sov_pct?: number;
}

const BRAND_COLOR = "#00b894";
const COMP_COLOR = "#0984e3";

function formatAxisDate(iso: string): string {
  const s = (iso || "").slice(0, 10);
  if (s.length !== 10) return s || "—";
  const d = new Date(`${s}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
}

function pickTickIndices(n: number): Set<number> {
  if (n <= 1) return new Set([0]);
  if (n === 2) return new Set([0, 1]);
  if (n <= 5) return new Set(Array.from({ length: n }, (_, i) => i));
  return new Set([0, Math.floor(n / 2), n - 1]);
}

export function SovOverTimeChart({
  points,
  className = "",
  height = 112,
}: {
  points: OverTimePoint[];
  className?: string;
  height?: number;
}) {
  const sorted = useMemo(
    () =>
      [...points]
        .filter((p) => p.date)
        .sort((a, b) => a.date.localeCompare(b.date)),
    [points],
  );

  const w = 240;
  const h = height;
  const padL = 32;
  const padR = 10;
  const padT = 12;
  const padB = 28;
  const innerW = w - padL - padR;
  const innerH = h - padT - padB;

  const yFor = (v: number) => padT + innerH - (Math.max(0, Math.min(100, v)) / 100) * innerH;

  if (sorted.length < 2) {
    return (
      <div
        className={`rounded-lg border border-dashed border-gray-200 bg-gray-50/80 px-3 py-4 text-sm text-gray-500 ${className}`}
      >
        Re-run audits for this site (saved under the same output folder) to build an over-time trend.
      </div>
    );
  }

  const n = sorted.length;
  const brandCoords = sorted.map((p, i) => {
    const x = padL + (n > 1 ? (i / (n - 1)) * innerW : innerW / 2);
    const y = yFor(Number(p.brand_share_pct ?? 0));
    return { x, y, date: p.date };
  });
  const compCoords = sorted.map((p, i) => {
    const x = padL + (n > 1 ? (i / (n - 1)) * innerW : innerW / 2);
    const y = yFor(Number(p.competitor_avg_sov_pct ?? 0));
    return { x, y };
  });

  const tickIdx = pickTickIndices(n);
  const gridY = [0, 50, 100].map((tick) => {
    const y = yFor(tick);
    return (
      <g key={tick}>
        <line x1={padL} y1={y} x2={w - padR} y2={y} stroke="rgba(0,0,0,0.06)" strokeWidth={1} />
        <text x={padL - 4} y={y + 3.5} textAnchor="end" fontSize={9} fill="#888">
          {tick}
        </text>
      </g>
    );
  });

  const xLabels = [...tickIdx].map((i) => {
    const { x, date } = brandCoords[i];
    return (
      <text key={date + i} x={x} y={h - 6} textAnchor="middle" fontSize={9} fill="#666">
        {formatAxisDate(date)}
      </text>
    );
  });

  const poly = (coords: { x: number; y: number }[], color: string) => {
    const pts = coords.map((c) => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(" ");
    return (
      <polyline
        fill="none"
        stroke={color}
        strokeWidth={2.25}
        strokeLinecap="round"
        strokeLinejoin="round"
        points={pts}
      />
    );
  };

  const lastBrand = brandCoords[n - 1];
  const lastComp = compCoords[n - 1];

  return (
    <div className={className}>
      <div className="text-[10px] font-semibold uppercase tracking-wider text-gray-500 mb-1">
        Over time
      </div>
      <svg
        className="w-full max-w-[240px] ml-auto block"
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-label="Brand and competitor share of voice over time"
      >
        {gridY}
        {poly(brandCoords, BRAND_COLOR)}
        {poly(compCoords, COMP_COLOR)}
        <circle
          cx={lastBrand.x}
          cy={lastBrand.y}
          r={3.5}
          fill={BRAND_COLOR}
          stroke={BRAND_COLOR}
          strokeWidth={2}
        />
        <circle
          cx={lastComp.x}
          cy={lastComp.y}
          r={3.5}
          fill={COMP_COLOR}
          stroke={COMP_COLOR}
          strokeWidth={2}
        />
        {xLabels}
      </svg>
      <div className="flex flex-wrap gap-3 justify-end text-[11px] text-gray-500 mt-1">
        <span className="inline-flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: BRAND_COLOR }} />
          Brand SOV
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: COMP_COLOR }} />
          Avg tracked competitor SOV
        </span>
      </div>
      <p className="text-[11px] text-gray-400 mt-1 text-right">
        {sorted.length} saved audit{sorted.length === 1 ? "" : "s"} for this URL
      </p>
    </div>
  );
}
