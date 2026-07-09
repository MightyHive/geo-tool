/**
 * Competitor comparison for AI Visibility.
 *
 * Shows every brand (yours + all competitors found in probe replies) as rows,
 * with columns for Visibility %, SOV %, and per-platform brand mention share —
 * derived from the live probe data that's already on the context.
 */
import { useEffect, useMemo, useState } from "react";
import { Loader2 } from "lucide-react";
import { fetchPromptPerformanceContext } from "../api/client";
import type { LiveProbePerPrompt, MentionScores, PromptPerformanceContext } from "../types";
import { CompetitorFavicon, PlatformLogo, PLATFORM_META } from "./PlatformLogo";

interface CompetitorComparisonSectionProps {
  auditDirOrSlug: string;
}

const PLATFORMS = ["gemini", "openai", "claude", "google_aio"] as const;
type Platform = typeof PLATFORMS[number];

interface BrandRow {
  name: string;
  website?: string;
  isOwnBrand: boolean;
  /** Per platform: brand_signal hits */
  platformHits: Record<Platform, number>;
  /** Per platform: total combined hits (brand + all competitors) */
  platformTotal: Record<Platform, number>;
  /** Prompts where the brand was mentioned at least once, across all platforms */
  promptMentionCount: number;
  totalPrompts: number;
}

function pct(val: number, total: number): number {
  return total > 0 ? (val / total) * 100 : 0;
}

function fmt(n: number): string {
  return n.toFixed(1) + "%";
}

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const w = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="flex items-center gap-2 min-w-[90px]">
      <div className="flex-1 h-1.5 rounded-full bg-gray-100 overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${w}%`, background: color }}
        />
      </div>
      <span className="text-xs tabular-nums text-[#0d0d0d] w-10 text-right shrink-0">
        {fmt(value)}
      </span>
    </div>
  );
}

function buildBrandRows(
  ctx: PromptPerformanceContext,
): BrandRow[] {
  const live = ctx.live_probe;
  if (!live?.per_prompt?.length) return [];

  const perPrompt = live.per_prompt as LiveProbePerPrompt[];
  const brandTokens = live.brand_match_tokens ?? [];
  const brandName = ctx.brand_name || "Your brand";
  const brandWebsite = ctx.brand_site_url || "";

  // Collect all competitor names from competitor_detail across all prompts/platforms
  const competitorNameSet = new Set<string>();
  for (const row of perPrompt) {
    for (const p of PLATFORMS) {
      const scores = (row as Record<string, MentionScores | undefined>)[`mention_scores_${p}`];
      if (!scores?.competitor_detail) continue;
      Object.keys(scores.competitor_detail).forEach((k) => competitorNameSet.add(k));
    }
  }

  // Also add configured competitors
  for (const c of ctx.competitors ?? []) {
    if (c.competitor_brand) competitorNameSet.add(c.competitor_brand);
  }

  // Build a website map for configured competitors
  const compWebsiteMap = new Map<string, string>();
  for (const c of ctx.competitors ?? []) {
    if (c.competitor_brand) compWebsiteMap.set(c.competitor_brand, c.competitor_website);
  }

  const totalPrompts = perPrompt.length;

  // Accumulate hits per brand × platform
  const brandHits: Map<string, Record<Platform, number>> = new Map();
  const brandTotal: Map<string, Record<Platform, number>> = new Map();

  function getOrInit(map: Map<string, Record<Platform, number>>, key: string) {
    if (!map.has(key)) {
      map.set(key, { gemini: 0, openai: 0, claude: 0, google_aio: 0 });
    }
    return map.get(key)!;
  }

  for (const row of perPrompt) {
    for (const p of PLATFORMS) {
      const scores = (row as Record<string, MentionScores | undefined>)[`mention_scores_${p}`];
      if (!scores) continue;

      const brandSig = Number(scores.brand_signal ?? 0);
      const compDetail = scores.competitor_detail ?? {};
      const compTotal = Object.values(compDetail).reduce((s, v) => s + Number(v), 0);
      const rowTotal = brandSig + compTotal;

      // brand row
      getOrInit(brandHits, brandName)[p] += brandSig;
      getOrInit(brandTotal, brandName)[p] += rowTotal;

      // competitor rows
      for (const [cName, hits] of Object.entries(compDetail)) {
        getOrInit(brandHits, cName)[p] += Number(hits);
        getOrInit(brandTotal, cName)[p] += rowTotal;
      }
    }
  }

  // Prompt-level mention count (across all platforms, count a prompt as "mentioned" if any platform saw the brand)
  const promptMentionCounts: Map<string, number> = new Map();

  for (const row of perPrompt) {
    // brand
    const brandMentioned = PLATFORMS.some((p) => {
      const resp = (row as Record<string, string | undefined>)[`${p}_response`];
      if (!resp) return false;
      const lower = resp.toLowerCase();
      return brandTokens.some((tok) => lower.includes(tok.toLowerCase()));
    });
    if (brandMentioned) {
      promptMentionCounts.set(brandName, (promptMentionCounts.get(brandName) ?? 0) + 1);
    }

    for (const p of PLATFORMS) {
      const scores = (row as Record<string, MentionScores | undefined>)[`mention_scores_${p}`];
      if (!scores?.competitor_detail) continue;
      for (const [cName, hits] of Object.entries(scores.competitor_detail)) {
        if (Number(hits) > 0) {
          promptMentionCounts.set(cName, (promptMentionCounts.get(cName) ?? 0) + 1);
        }
      }
    }
  }

  // Build rows — own brand first, then competitors sorted by total hits desc
  const rows: BrandRow[] = [];

  const makeRow = (name: string, isOwn: boolean): BrandRow => ({
    name,
    website: isOwn ? brandWebsite : compWebsiteMap.get(name),
    isOwnBrand: isOwn,
    platformHits: getOrInit(brandHits, name),
    platformTotal: getOrInit(brandTotal, name),
    promptMentionCount: promptMentionCounts.get(name) ?? 0,
    totalPrompts,
  });

  rows.push(makeRow(brandName, true));
  const compNames = Array.from(competitorNameSet).sort((a, b) => {
    const aHits = Object.values(getOrInit(brandHits, a)).reduce((s, v) => s + v, 0);
    const bHits = Object.values(getOrInit(brandHits, b)).reduce((s, v) => s + v, 0);
    return bHits - aHits;
  });
  for (const name of compNames) {
    rows.push(makeRow(name, false));
  }

  return rows;
}

export function CompetitorComparisonSection({ auditDirOrSlug }: CompetitorComparisonSectionProps) {
  const [ctx, setCtx] = useState<PromptPerformanceContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetchPromptPerformanceContext(auditDirOrSlug)
      .then(setCtx)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [auditDirOrSlug]);

  const rows = useMemo(() => (ctx ? buildBrandRows(ctx) : []), [ctx]);

  const activePlatforms = useMemo(
    () => PLATFORMS.filter((p) => rows.some((r) => r.platformTotal[p] > 0)),
    [rows],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[40vh]">
        <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
      </div>
    );
  }
  if (error) return <div className="alert-error m-6">{error}</div>;

  if (!ctx?.live_probe?.per_prompt?.length) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] text-center px-6">
        <p className="text-sm text-gray-400">No probe data yet. Run live probes from the Prompts section.</p>
      </div>
    );
  }

  const maxVisibility = Math.max(...rows.map((r) => pct(r.promptMentionCount, r.totalPrompts)), 1);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-[#0d0d0d] mb-1">Competitor comparison</h2>
        <p className="text-sm text-gray-400">
          Brand visibility, SOV and per-platform mention share across {rows[0]?.totalPrompts ?? 0} prompts.
        </p>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white overflow-x-auto">
        <table className="w-full text-sm min-w-[640px]">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400 min-w-[160px]">
                Brand
              </th>
              <th className="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400 min-w-[140px]">
                Visibility
              </th>
              <th className="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400 min-w-[140px]">
                SOV
              </th>
              {activePlatforms.map((p) => (
                <th key={p} className="px-4 py-3 text-center min-w-[100px]">
                  <div className="flex items-center justify-center">
                    <PlatformLogo platform={p} size={18} />
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const visibilityPct = pct(row.promptMentionCount, row.totalPrompts);
              const totalHitsAcrossAll = PLATFORMS.reduce(
                (s, p) => s + row.platformHits[p],
                0,
              );
              const totalAllBrandsHits = PLATFORMS.reduce(
                (s, p) => s + row.platformTotal[p],
                0,
              );
              const sovPct = pct(totalHitsAcrossAll, totalAllBrandsHits);

              return (
                <tr
                  key={i}
                  className={`border-b border-gray-50 last:border-0 ${row.isOwnBrand ? "bg-emerald-50/40" : "hover:bg-gray-50/40"}`}
                >
                  {/* Brand name */}
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2.5">
                      <CompetitorFavicon name={row.name} website={row.website} size={18} />
                      <div>
                        <p className={`font-semibold ${row.isOwnBrand ? "text-emerald-700" : "text-[#0d0d0d]"}`}>
                          {row.name}
                        </p>
                        {row.isOwnBrand && (
                          <span className="text-[10px] text-emerald-600 font-semibold">Your brand</span>
                        )}
                      </div>
                    </div>
                  </td>

                  {/* Visibility */}
                  <td className="px-4 py-3">
                    <Bar value={visibilityPct} max={maxVisibility} color={row.isOwnBrand ? "#00b894" : "#74b9ff"} />
                  </td>

                  {/* SOV */}
                  <td className="px-4 py-3">
                    <Bar value={sovPct} max={100} color={row.isOwnBrand ? "#6c5ce7" : "#a29bfe"} />
                  </td>

                  {/* Per-platform */}
                  {activePlatforms.map((p) => {
                    const platHits = row.platformHits[p];
                    const platTotal = row.platformTotal[p];
                    const platPct = pct(platHits, platTotal);
                    const platMax = Math.max(
                      ...rows.map((r) => pct(r.platformHits[p], r.platformTotal[p])),
                      1,
                    );
                    return (
                      <td key={p} className="px-4 py-3">
                        <Bar
                          value={platPct}
                          max={platMax}
                          color={PLATFORM_META[p]?.color ?? "#888"}
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

      <p className="text-xs text-gray-400">
        Visibility = % of prompts where the brand was mentioned by at least one platform.
        SOV = brand's share of all brand + competitor signal hits across all platforms and prompts.
        Per-platform columns show each brand's share of total hits for that platform.
      </p>
    </div>
  );
}
