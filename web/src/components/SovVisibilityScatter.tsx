import { useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { PromptPerformanceContext } from "../types";
import {
  computeVisibilityMetrics,
  type VisibilityMetrics,
} from "../lib/visibilityMetrics";
import { filterContextByTopic, listProbeTopics } from "../lib/promptCategoryGrouping";
import { PLATFORM_META } from "./PlatformLogo";

/** Round an axis ceiling up to a readable percent tick (e.g. 9 → 10). */
export function nicePercentAxisMax(maxValue: number): number {
  const value = Math.max(0, Number(maxValue) || 0);
  if (value <= 0) return 10;
  if (value >= 100) return 100;
  const steps = [1, 2, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75, 100];
  return steps.find((step) => step >= value) ?? 100;
}

const TOPIC_COLORS = [
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

export type SovVisibilityPoint = {
  id: string;
  label: string;
  visibility: number;
  sov: number;
  color: string;
};

export function platformSovVisibilityPoints(
  metrics: VisibilityMetrics | null | undefined,
): SovVisibilityPoint[] {
  if (!metrics) return [];
  return Object.entries(metrics.perPlatform)
    .filter(([, value]) => value.responseCount > 0)
    .map(([platform, value]) => ({
      id: platform,
      label: PLATFORM_META[platform]?.label ?? platform,
      visibility: Number(value.visibilityPct.toFixed(1)),
      sov: Number(value.sovPct.toFixed(1)),
      color: PLATFORM_META[platform]?.color ?? "#8B5CF6",
    }));
}

/** One point per product/service topic (overall visibility + SOV). */
export function topicSovVisibilityPoints(
  ctx: PromptPerformanceContext | null | undefined,
): SovVisibilityPoint[] {
  if (!ctx) return [];
  return listProbeTopics(ctx)
    .map((topic, index) => {
      const filtered = filterContextByTopic(ctx, topic);
      const metrics = filtered ? computeVisibilityMetrics(filtered) : null;
      if (!metrics || metrics.promptCount <= 0) return null;
      return {
        id: topic,
        label: topic,
        visibility: Number(metrics.visibilityPct.toFixed(1)),
        sov: Number(metrics.sovPct.toFixed(1)),
        color: TOPIC_COLORS[index % TOPIC_COLORS.length],
      } satisfies SovVisibilityPoint;
    })
    .filter((point): point is SovVisibilityPoint => point != null);
}

/** One point per prompt, calculated only from that prompt's saved platform responses. */
export function promptSovVisibilityPoints(
  ctx: PromptPerformanceContext | null | undefined,
): SovVisibilityPoint[] {
  const rows = ctx?.live_probe?.per_prompt ?? [];
  if (!ctx || !rows.length) return [];
  return rows
    .map((row, index) => {
      const prompt = String(row.prompt || ctx.flat_prompts[index] || `Prompt ${index + 1}`).trim();
      const promptCtx: PromptPerformanceContext = {
        ...ctx,
        flat_prompts: [prompt],
        prompt_count: 1,
        live_probe: {
          ...ctx.live_probe,
          per_prompt: [row],
          prompt_count: 1,
        },
      };
      const metrics = computeVisibilityMetrics(promptCtx);
      if (!metrics || metrics.promptCount <= 0) return null;
      return {
        id: String(row.prompt_id || row.index || `${index}-${prompt}`),
        label: prompt,
        visibility: Number(metrics.visibilityPct.toFixed(1)),
        sov: Number(metrics.sovPct.toFixed(1)),
        color: TOPIC_COLORS[index % TOPIC_COLORS.length],
      } satisfies SovVisibilityPoint;
    })
    .filter((point): point is SovVisibilityPoint => point != null);
}

function ScatterTooltip({
  active,
  payload,
  dimensionLabel,
}: {
  active?: boolean;
  payload?: Array<{ payload?: SovVisibilityPoint }>;
  dimensionLabel: string;
}) {
  if (!active || !payload?.[0]?.payload) return null;
  const point = payload[0].payload;
  return (
    <div className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs shadow-lg">
      <div className="font-semibold" style={{ color: point.color }}>
        {dimensionLabel}: {point.label}
      </div>
      <div className="mt-1 text-gray-600">Visibility: {point.visibility?.toFixed(1)}%</div>
      <div className="text-gray-600">SOV: {point.sov?.toFixed(1)}%</div>
    </div>
  );
}

function truncateLabel(label: string, max = 26): string {
  if (label.length <= max) return label;
  return `${label.slice(0, max - 1)}…`;
}

// ── Label de-confliction ──────────────────────────────────────────────────────

const MARKER_RADIUS = 9;
const LABEL_FONT_SIZE = 13;
const LABEL_CHAR_WIDTH = 7.1;
const LABEL_HEIGHT = 16;
/** Must match the chart margins / axis sizes below so estimated pixels line up. */
const CHART_MARGIN = { top: 32, right: 92, bottom: 24, left: 10 };
const Y_AXIS_WIDTH = 44;
const X_AXIS_HEIGHT = 30;

type Anchor = "start" | "middle" | "end";
type Placement = { dx: number; dy: number; anchor: Anchor };
type Box = { x1: number; y1: number; x2: number; y2: number };

/** Candidate label slots around a marker, nearest first. */
const LABEL_CANDIDATES: Placement[] = [
  { dx: MARKER_RADIUS + 6, dy: 5, anchor: "start" },
  { dx: -(MARKER_RADIUS + 6), dy: 5, anchor: "end" },
  { dx: 0, dy: -(MARKER_RADIUS + 8), anchor: "middle" },
  { dx: 0, dy: MARKER_RADIUS + 17, anchor: "middle" },
  { dx: MARKER_RADIUS + 4, dy: -(MARKER_RADIUS + 4), anchor: "start" },
  { dx: MARKER_RADIUS + 4, dy: MARKER_RADIUS + 14, anchor: "start" },
  { dx: -(MARKER_RADIUS + 4), dy: -(MARKER_RADIUS + 4), anchor: "end" },
  { dx: -(MARKER_RADIUS + 4), dy: MARKER_RADIUS + 14, anchor: "end" },
  { dx: MARKER_RADIUS + 6, dy: -(MARKER_RADIUS + 18), anchor: "start" },
  { dx: -(MARKER_RADIUS + 6), dy: -(MARKER_RADIUS + 18), anchor: "end" },
  { dx: MARKER_RADIUS + 6, dy: MARKER_RADIUS + 30, anchor: "start" },
  { dx: -(MARKER_RADIUS + 6), dy: MARKER_RADIUS + 30, anchor: "end" },
  { dx: 0, dy: -(MARKER_RADIUS + 26), anchor: "middle" },
  { dx: 0, dy: MARKER_RADIUS + 34, anchor: "middle" },
];

function labelBox(cx: number, cy: number, placement: Placement, textWidth: number): Box {
  const anchorX = cx + placement.dx;
  const x1 = placement.anchor === "start"
    ? anchorX
    : placement.anchor === "end"
      ? anchorX - textWidth
      : anchorX - textWidth / 2;
  const baseline = cy + placement.dy;
  return {
    x1,
    x2: x1 + textWidth,
    y1: baseline - LABEL_HEIGHT + 4,
    y2: baseline + 4,
  };
}

function overlapArea(a: Box, b: Box): number {
  const width = Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1);
  const height = Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1);
  if (width <= 0 || height <= 0) return 0;
  return width * height;
}

/**
 * Greedy label layout: each label takes the nearest free slot around its marker,
 * scored against already-placed labels, every marker, and the canvas bounds.
 */
function resolveLabelPlacements(
  points: SovVisibilityPoint[],
  geometry: {
    width: number;
    height: number;
    sovDomainMax: number;
  },
): Map<string, Placement> {
  const { width, height, sovDomainMax } = geometry;
  const result = new Map<string, Placement>();
  if (!points.length || width <= 0) return result;

  const plotLeft = CHART_MARGIN.left + Y_AXIS_WIDTH;
  const plotRight = Math.max(plotLeft + 1, width - CHART_MARGIN.right);
  const plotTop = CHART_MARGIN.top;
  const plotBottom = Math.max(plotTop + 1, height - CHART_MARGIN.bottom - X_AXIS_HEIGHT);
  const plotWidth = plotRight - plotLeft;
  const plotHeight = plotBottom - plotTop;

  const positioned = points.map((point) => ({
    point,
    cx: plotLeft + (Math.min(100, Math.max(0, point.visibility)) / 100) * plotWidth,
    cy: plotBottom
      - (sovDomainMax > 0 ? Math.min(1, Math.max(0, point.sov / sovDomainMax)) : 0) * plotHeight,
    textWidth: truncateLabel(point.label).length * LABEL_CHAR_WIDTH,
  }));

  const markerBoxes: Box[] = positioned.map(({ cx, cy }) => ({
    x1: cx - MARKER_RADIUS - 2,
    x2: cx + MARKER_RADIUS + 2,
    y1: cy - MARKER_RADIUS - 2,
    y2: cy + MARKER_RADIUS + 2,
  }));

  // Longest labels first — they are hardest to fit, so they get priority.
  const order = positioned
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) =>
      right.entry.textWidth - left.entry.textWidth
      || left.entry.point.label.localeCompare(right.entry.point.label));

  const placedBoxes: Box[] = [];

  for (const { entry, index } of order) {
    let best: { placement: Placement; box: Box; penalty: number } | null = null;

    for (const candidate of LABEL_CANDIDATES) {
      const box = labelBox(entry.cx, entry.cy, candidate, entry.textWidth);
      let penalty = 0;

      for (const placed of placedBoxes) penalty += overlapArea(box, placed) * 3;
      markerBoxes.forEach((marker, markerIndex) => {
        if (markerIndex === index) return;
        penalty += overlapArea(box, marker) * 2;
      });

      // Keep labels on canvas; clipping is worse than a slightly odd position.
      const outLeft = Math.max(0, 4 - box.x1);
      const outRight = Math.max(0, box.x2 - (width - 4));
      const outTop = Math.max(0, 14 - box.y1);
      const outBottom = Math.max(0, box.y2 - (height - 6));
      penalty += (outLeft + outRight + outTop + outBottom) * 60;

      if (!best || penalty < best.penalty) {
        best = { placement: candidate, box, penalty };
      }
      if (penalty === 0) break;
    }

    if (best) {
      placedBoxes.push(best.box);
      result.set(entry.point.id, best.placement);
    }
  }

  return result;
}

export default function SovVisibilityScatter({
  title,
  subtitle,
  points,
  dimensionLabel = "Item",
  emptyMessage = "No data available yet.",
  className,
}: {
  title: string;
  subtitle?: string;
  points: SovVisibilityPoint[];
  dimensionLabel?: string;
  emptyMessage?: string;
  className?: string;
}) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [chartWidth, setChartWidth] = useState(0);

  useEffect(() => {
    const node = wrapperRef.current;
    if (!node) return;
    setChartWidth(node.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const next = entries[0]?.contentRect.width ?? node.clientWidth;
      setChartWidth(Math.round(next));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const sovMax = points.reduce((max, point) => Math.max(max, point.sov), 0);
  const sovDomainMax = nicePercentAxisMax(sovMax);
  const chartHeight = Math.max(320, 260 + Math.min(points.length, 8) * 10);

  const placements = useMemo(
    () =>
      resolveLabelPlacements(points, {
        width: chartWidth || 640,
        height: chartHeight,
        sovDomainMax,
      }),
    [points, chartWidth, chartHeight, sovDomainMax],
  );

  return (
    <div className={`rounded-xl border border-gray-200 bg-white p-5 ${className ?? ""}`.trim()}>
      <div className="mb-4">
        <h3 className="text-sm font-semibold text-[#0d0d0d]">{title}</h3>
        {subtitle ? <p className="mt-1 text-[11px] text-gray-400">{subtitle}</p> : null}
      </div>
      {points.length === 0 ? (
        <div className="flex h-56 items-center justify-center text-sm text-gray-400">{emptyMessage}</div>
      ) : (
        <div ref={wrapperRef} className="[&_svg]:overflow-visible">
          <ResponsiveContainer width="100%" height={chartHeight}>
            <ScatterChart margin={CHART_MARGIN}>
              <CartesianGrid stroke="#f0f0f0" strokeDasharray="3 3" />
              <XAxis
                type="number"
                dataKey="visibility"
                name="Visibility"
                domain={[0, 100]}
                tickFormatter={(value) => `${value}%`}
                tick={{ fontSize: 11, fill: "#aaa" }}
                axisLine={false}
                tickLine={false}
                height={X_AXIS_HEIGHT}
                label={{ value: "Visibility (%)", position: "insideBottom", offset: -2, fill: "#6b7280", fontSize: 12 }}
              />
              <YAxis
                type="number"
                dataKey="sov"
                name="SOV"
                domain={[0, sovDomainMax]}
                tickFormatter={(value) => `${value}%`}
                tick={{ fontSize: 11, fill: "#aaa" }}
                axisLine={false}
                tickLine={false}
                width={Y_AXIS_WIDTH}
                allowDecimals={sovDomainMax < 5}
                label={{ value: "SOV (%)", angle: -90, position: "insideLeft", fill: "#6b7280", fontSize: 12 }}
              />
              <Tooltip
                cursor={{ strokeDasharray: "3 3" }}
                content={<ScatterTooltip dimensionLabel={dimensionLabel} />}
              />
              <Scatter
                name={dimensionLabel}
                data={points}
                fill="transparent"
                isAnimationActive={false}
                shape={(props: {
                  cx?: number;
                  cy?: number;
                  payload?: SovVisibilityPoint;
                }) => {
                  const point = props.payload;
                  if (!point || props.cx == null || props.cy == null) return <g />;
                  const color = point.color ?? "#8B5CF6";
                  const placement = placements.get(point.id) ?? LABEL_CANDIDATES[0];
                  return (
                    <g>
                      {/* Halo keeps the ring readable where markers overlap. */}
                      <circle
                        cx={props.cx}
                        cy={props.cy}
                        r={MARKER_RADIUS}
                        fill="transparent"
                        stroke="#ffffff"
                        strokeWidth={6}
                      />
                      <circle
                        cx={props.cx}
                        cy={props.cy}
                        r={MARKER_RADIUS}
                        fill="transparent"
                        stroke={color}
                        strokeWidth={3.5}
                      />
                      <text
                        x={props.cx + placement.dx}
                        y={props.cy + placement.dy}
                        fill={color}
                        fontSize={LABEL_FONT_SIZE}
                        fontWeight={700}
                        textAnchor={placement.anchor}
                        stroke="#ffffff"
                        strokeWidth={4}
                        paintOrder="stroke"
                        style={{ pointerEvents: "none" }}
                      >
                        {truncateLabel(point.label)}
                      </text>
                    </g>
                  );
                }}
              />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
