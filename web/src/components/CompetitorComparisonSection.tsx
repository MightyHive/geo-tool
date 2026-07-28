/**
 * Competitor comparison for AI Visibility.
 *
 * Shows every brand (yours + all competitors found in probe replies) as rows,
 * with columns for Visibility %, SOV %, and per-platform SOV —
 * derived from the live probe data that's already on the context.
 */
import { useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Info } from "lucide-react";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import { PageLoading } from "./PageLoading";
import type { LiveProbePerPrompt, PromptPerformanceContext } from "../types";
import { CompetitorFavicon, PlatformLogo, PLATFORM_META } from "./PlatformLogo";
import { computeOverallSentiment, type SentimentLabel } from "../lib/sentimentCalc";
import {
  accumulateBrandVisibilityHits,
  completedPlatformRuns,
  globalSignalHits,
  rowSignalHits,
  VISIBILITY_PLATFORMS,
  type VisibilityPlatform,
} from "../lib/brandVisibilityRows";

interface CompetitorComparisonSectionProps {
  auditDirOrSlug: string;
}

export const COMPETITOR_PLATFORMS = VISIBILITY_PLATFORMS;
export type CompetitorPlatform = VisibilityPlatform;
export { completedPlatformRuns };

export interface BrandRow {
  name: string;
  website?: string;
  isOwnBrand: boolean;
  /** Per platform: brand_signal hits */
  platformHits: Record<CompetitorPlatform, number>;
  /** Per platform: global total hits (brand + ALL competitors) across ALL prompts */
  globalPlatformTotal: Record<CompetitorPlatform, number>;
  /** Completed platform responses where the brand was mentioned. */
  promptMentionCount: number;
  /** Total completed platform responses. */
  totalPrompts: number;
  sentiment: SentimentLabel | null;
}

function pct(val: number, total: number): number {
  return total > 0 ? (val / total) * 100 : 0;
}

function fmt(n: number): string {
  return n.toFixed(1) + "%";
}

function SentimentBadge({ sentiment }: { sentiment: SentimentLabel | null }) {
  if (!sentiment) return <span className="text-xs text-gray-300">—</span>;
  if (sentiment === "positive")
    return <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-emerald-100 text-emerald-600 text-sm font-bold" title="Positive">+</span>;
  if (sentiment === "negative")
    return <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-red-100 text-red-500 text-sm font-bold" title="Negative">−</span>;
  return <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-gray-100 text-gray-400 text-sm font-bold" title="Neutral">~</span>;
}

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const w = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <div className="flex-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${w}%`, background: color }} />
      </div>
      <span className="text-xs tabular-nums text-[#0d0d0d] w-10 text-right shrink-0">{fmt(value)}</span>
    </div>
  );
}

function Tooltip({ text }: { text: string }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  const show = () => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      setPos({ x: r.left + r.width / 2, y: r.bottom + 6 });
    }
  };
  return (
    <span
      ref={ref}
      className="ml-1 inline-flex items-center cursor-help rounded-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
      tabIndex={0}
      aria-label={text}
      onMouseEnter={show}
      onMouseLeave={() => setPos(null)}
      onFocus={show}
      onBlur={() => setPos(null)}
    >
      <Info className="w-3 h-3 text-gray-400" />
      {pos && createPortal(
        <span
          className="fixed w-56 rounded-lg bg-gray-900 text-white text-xs px-2.5 py-2 leading-snug z-[9999] shadow-lg pointer-events-none"
          style={{ left: pos.x, top: pos.y, transform: "translateX(-50%)" }}
        >
          {text}
        </span>,
        document.body,
      )}
    </span>
  );
}

function Th({ label, tooltip, center }: { label: string; tooltip?: string; center?: boolean }) {
  return (
    <th className={`px-4 py-3 min-w-[130px] ${center ? "text-center" : "text-left"}`}>
      <span className={`inline-flex items-center gap-0.5 text-[10px] font-semibold uppercase tracking-wide text-gray-400`}>
        {label}
        {tooltip && <Tooltip text={tooltip} />}
      </span>
    </th>
  );
}

export function buildBrandRows(ctx: PromptPerformanceContext): BrandRow[] {
  const live = ctx.live_probe;
  if (!live?.per_prompt?.length) return [];

  const perPrompt = live.per_prompt as LiveProbePerPrompt[];
  const brandTokens = live.brand_match_tokens ?? [];
  const hitRows = accumulateBrandVisibilityHits(ctx);

  return hitRows.map((row) => {
    const tokens = row.isOwnBrand ? brandTokens : [row.name];
    const sentResult = computeOverallSentiment(
      perPrompt as Array<Record<string, unknown>>,
      tokens,
      Array.from(COMPETITOR_PLATFORMS),
    );
    return {
      ...row,
      sentiment: sentResult.scorePercent != null ? sentResult.label : null,
    };
  });
}

export function CompetitorVisibilityTable({
  rows,
  title,
}: {
  rows: BrandRow[];
  title?: string;
}) {
  const activePlatforms = COMPETITOR_PLATFORMS.filter((platform) =>
    rows.some((row) => row.globalPlatformTotal[platform] > 0)
  );
  const maxVisibility = Math.max(
    ...rows.map((row) => pct(row.promptMentionCount, row.totalPrompts)),
    1,
  );
  // Raw SOV — same definition as Brand & competitor visibility table / Overview scorecard.
  const totalHits = globalSignalHits(rows[0]);

  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      {title && (
        <div className="px-5 py-4 border-b border-gray-100">
          <h3 className="text-sm font-semibold text-[#0d0d0d]">{title}</h3>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[640px]">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400 min-w-[160px]">
                Brand
              </th>
              <Th
                label="Visibility %"
                tooltip="Platform responses mentioning the brand ÷ all platform responses analysed."
              />
              <Th
                label="SOV %"
                tooltip="Share of Voice: brand signal hits ÷ total brand + website-backed competitor hits."
              />
              <Th
                label="Sentiment"
                tooltip="Overall tone of AI responses mentioning this brand: + positive, − negative, ~ neutral."
                center
              />
              {activePlatforms.map((platform) => (
                <th key={platform} className="px-4 py-3 text-center min-w-[130px]">
                  <div className="flex flex-col items-center gap-0.5">
                    <div className="flex items-center gap-1.5">
                      <PlatformLogo platform={platform} size={14} />
                      <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                        {PLATFORM_META[platform]?.label ?? platform}
                      </span>
                    </div>
                    <span className="text-[9px] text-gray-300 font-medium uppercase tracking-widest">SOV</span>
                    <Tooltip text={`${PLATFORM_META[platform]?.label ?? platform}: brand's share of all brand + competitor signal hits on this platform.`} />
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const visibilityPct = pct(row.promptMentionCount, row.totalPrompts);
              const sovPct = pct(rowSignalHits(row), totalHits);
              return (
                <tr
                  key={row.name}
                  className={`border-b border-gray-50 last:border-0 ${row.isOwnBrand ? "bg-emerald-50/40" : "hover:bg-gray-50/40"}`}
                >
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2.5">
                      <CompetitorFavicon name={row.name} website={row.website} size={18} />
                      <div>
                        {row.isOwnBrand || !row.website ? (
                          <p className={`font-semibold ${row.isOwnBrand ? "text-emerald-700" : "text-[#0d0d0d]"}`}>
                            {row.name}
                          </p>
                        ) : (
                          <a
                            href={row.website}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="rounded-sm font-semibold text-[#0d0d0d] hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500"
                          >
                            {row.name}
                          </a>
                        )}
                        {row.isOwnBrand && (
                          <span className="text-[10px] text-emerald-600 font-semibold">Your brand</span>
                        )}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <Bar value={visibilityPct} max={maxVisibility} color={row.isOwnBrand ? "#00b894" : "#74b9ff"} />
                  </td>
                  <td className="px-4 py-3">
                    <Bar value={sovPct} max={100} color={row.isOwnBrand ? "#6c5ce7" : "#a29bfe"} />
                  </td>
                  <td className="px-4 py-3 text-center">
                    <SentimentBadge sentiment={row.sentiment} />
                  </td>
                  {activePlatforms.map((platform) => {
                    const platformPct = pct(
                      row.platformHits[platform],
                      row.globalPlatformTotal[platform],
                    );
                    const platformMax = Math.max(
                      ...rows.map((item) =>
                        pct(item.platformHits[platform], item.globalPlatformTotal[platform])
                      ),
                      1,
                    );
                    return (
                      <td key={platform} className="px-4 py-3">
                        <Bar
                          value={platformPct}
                          max={platformMax}
                          color={PLATFORM_META[platform]?.color ?? "#4B5563"}
                        />
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
  );
}

export function CompetitorComparisonSection({ auditDirOrSlug }: CompetitorComparisonSectionProps) {
  const { ctx, loading, error } = usePromptPerformanceContext(auditDirOrSlug);

  const rows = useMemo(() => (ctx ? buildBrandRows(ctx) : []), [ctx]);

  if (loading) {
    return <PageLoading />;
  }
  if (error) return <div className="alert-error m-6">{error}</div>;

  if (!ctx?.live_probe?.per_prompt?.length) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] text-center px-6">
        <p className="text-sm text-gray-400">No probe data yet. Run live probes from the Prompts section.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[#0d0d0d] mb-1">Competitor comparison</h2>
        <p className="text-sm text-gray-400">
          Brand visibility, SOV and platform-level SOV across {rows[0]?.totalPrompts ?? 0} analysed platform responses.
        </p>
      </div>

      <CompetitorVisibilityTable rows={rows} />

      <p className="text-xs text-gray-400">
        <strong>Visibility</strong> = platform responses mentioning the brand ÷ all platform responses analysed. &nbsp;
        <strong>SOV</strong> = brand signal hits ÷ total brand + website-backed competitor hits. &nbsp;
        <strong>Platform SOV</strong> = brand's signal hits ÷ total brand + competitor hits on that platform.
      </p>
    </div>
  );
}



