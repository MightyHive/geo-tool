import { useEffect, useState } from "react";
import { Loader2, TrendingUp, Eye, BarChart2, Award } from "lucide-react";
import { fetchPromptPerformanceContext } from "../api/client";
import type { PromptPerformanceContext, TopCitedSite } from "../types";

interface AiVisibilityOverviewProps {
  auditDirOrSlug: string;
}

const PLATFORM_CONFIG: Record<string, { label: string; color: string }> = {
  gemini: { label: "Gemini", color: "#4285F4" },
  openai: { label: "ChatGPT", color: "#10a37f" },
  claude: { label: "Claude", color: "#D97706" },
  google_aio: { label: "Google AI", color: "#EA4335" },
};

function pct(val: number | undefined): string {
  if (val == null) return "—";
  return `${Math.round(val)}%`;
}

function ScoreCard({
  icon: Icon,
  label,
  value,
  sub,
  color,
}: {
  icon: React.ElementType;
  label: string;
  value: string;
  sub?: string;
  color: string;
}) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 p-5">
      <div className="flex items-center gap-2 mb-3">
        <span
          className="inline-flex h-8 w-8 items-center justify-center rounded-lg"
          style={{ background: color + "18" }}
        >
          <Icon className="w-4 h-4" style={{ color }} />
        </span>
        <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">{label}</span>
      </div>
      <p className="text-3xl font-bold text-[#0d0d0d] leading-none mb-1">{value}</p>
      {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function PlatformBar({
  label,
  color,
  brandPct,
  compPct,
}: {
  label: string;
  color: string;
  brandPct: number;
  compPct: number;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium text-[#0d0d0d]">{label}</span>
        <span className="text-gray-400">{pct(brandPct)} brand · {pct(compPct)} competitor</span>
      </div>
      <div className="flex gap-1 h-2 rounded-full overflow-hidden bg-gray-100">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${Math.min(brandPct, 100)}%`, background: color }}
        />
      </div>
    </div>
  );
}

function TopDomainsTable({ sites }: { sites: TopCitedSite[] }) {
  if (!sites.length) return null;
  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      <div className="px-6 py-4 border-b border-gray-100">
        <h3 className="text-sm font-semibold text-[#0d0d0d]">Top cited domains</h3>
      </div>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-100 bg-gray-50">
            <th className="px-6 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Domain</th>
            <th className="px-6 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-400">Citations</th>
            <th className="px-6 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">Platforms</th>
          </tr>
        </thead>
        <tbody>
          {sites.slice(0, 8).map((s) => (
            <tr key={s.domain} className="border-b border-gray-50 last:border-0">
              <td className="px-6 py-3 flex items-center gap-2">
                <img
                  src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(s.domain)}&sz=32`}
                  alt=""
                  width={16}
                  height={16}
                  className="rounded shrink-0 opacity-80"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = "none";
                  }}
                />
                <span className="font-medium text-[#0d0d0d]">{s.domain}</span>
              </td>
              <td className="px-6 py-3 text-right tabular-nums font-semibold text-[#0d0d0d]">
                {s.count}
              </td>
              <td className="px-6 py-3">
                <div className="flex flex-wrap gap-1">
                  {(s.platforms ?? []).map((p) => (
                    <span
                      key={p}
                      className="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold text-white"
                      style={{ background: PLATFORM_CONFIG[p]?.color ?? "#888" }}
                    >
                      {PLATFORM_CONFIG[p]?.label ?? p}
                    </span>
                  ))}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AiVisibilityOverview({ auditDirOrSlug }: AiVisibilityOverviewProps) {
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

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[40vh]">
        <Loader2 className="w-8 h-8 animate-spin text-gray-400" />
      </div>
    );
  }

  if (error) {
    return <div className="alert-error m-6">{error}</div>;
  }

  const live = ctx?.live_probe;
  const agg = live?.aggregate;
  const perPrompt = live?.per_prompt ?? [];
  const topSites = live?.top_cited_sites ?? [];

  const platforms = ["gemini", "openai", "claude", "google_aio"] as const;

  // Visibility score: avg brand mention pct across all platforms and prompts
  let totalBrandPct = 0;
  let brandPctCount = 0;
  platforms.forEach((p) => {
    const share = agg?.[p]?.brand_share_pct;
    if (share != null) {
      totalBrandPct += share;
      brandPctCount++;
    }
  });
  const visibilityScore = brandPctCount > 0 ? totalBrandPct / brandPctCount : null;

  // SOV: brand share vs competitor share
  let totalComp = 0;
  let compCount = 0;
  platforms.forEach((p) => {
    const share = agg?.[p]?.competitor_share_pct;
    if (share != null) {
      totalComp += share;
      compCount++;
    }
  });
  const avgCompPct = compCount > 0 ? totalComp / compCount : null;
  const sovNumerator = visibilityScore != null && avgCompPct != null
    ? visibilityScore
    : null;
  const sovDenominator = (visibilityScore ?? 0) + (avgCompPct ?? 0);
  const sovPct = sovNumerator != null && sovDenominator > 0
    ? (sovNumerator / sovDenominator) * 100
    : null;

  // Average rank: how often brand is mentioned first vs later in responses
  let avgPosition = 0;
  let posCount = 0;
  perPrompt.forEach((row) => {
    platforms.forEach((p) => {
      const resp = row[`${p}_response` as keyof typeof row] as string | undefined;
      if (!resp || typeof resp !== "string") return;
      const brandTokens = live?.brand_match_tokens ?? [];
      if (!brandTokens.length) return;
      const lower = resp.toLowerCase();
      // Find earliest mention position (normalised 0-100 based on position in text)
      for (const tok of brandTokens) {
        const idx = lower.indexOf(tok.toLowerCase());
        if (idx >= 0) {
          avgPosition += (idx / resp.length) * 10 + 1; // 1–10 scale
          posCount++;
          break;
        }
      }
    });
  });
  const avgPos = posCount > 0 ? avgPosition / posCount : null;

  const hasData = agg != null || perPrompt.length > 0;

  if (!hasData) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[40vh] text-center px-6">
        <Eye className="w-10 h-10 text-gray-300 mb-3" />
        <p className="text-sm text-gray-400">No prompt probe data yet. Run prompt probes to see your AI visibility.</p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-lg font-semibold text-[#0d0d0d] mb-1">AI visibility overview</h2>
        <p className="text-sm text-gray-400">
          Aggregated across {perPrompt.length} prompt{perPrompt.length !== 1 ? "s" : ""} and {brandPctCount} platform{brandPctCount !== 1 ? "s" : ""}.
        </p>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <ScoreCard
          icon={Eye}
          label="Visibility"
          value={visibilityScore != null ? pct(visibilityScore) : "—"}
          sub="Prompts mentioning your brand"
          color="#4285F4"
        />
        <ScoreCard
          icon={TrendingUp}
          label="SOV"
          value={sovPct != null ? pct(sovPct) : "—"}
          sub="Share of AI mentions vs competitors"
          color="#10a37f"
        />
        <ScoreCard
          icon={Award}
          label="Position"
          value={avgPos != null ? avgPos.toFixed(1) : "—"}
          sub="Avg mention position (lower = earlier)"
          color="#D97706"
        />
        <ScoreCard
          icon={BarChart2}
          label="Platforms"
          value={String(brandPctCount)}
          sub="Active AI platforms scored"
          color="#8B5CF6"
        />
      </div>

      {brandPctCount > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <h3 className="text-sm font-semibold text-[#0d0d0d] mb-5">Brand visibility by platform</h3>
          <div className="space-y-4">
            {platforms.map((p) => {
              const cfg = PLATFORM_CONFIG[p];
              const share = agg?.[p]?.brand_share_pct;
              const compShare = agg?.[p]?.competitor_share_pct;
              if (share == null) return null;
              return (
                <PlatformBar
                  key={p}
                  label={cfg.label}
                  color={cfg.color}
                  brandPct={share}
                  compPct={compShare ?? 0}
                />
              );
            })}
          </div>
        </div>
      )}

      <TopDomainsTable sites={topSites} />

      {perPrompt.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-100">
            <h3 className="text-sm font-semibold text-[#0d0d0d]">Prompt visibility breakdown</h3>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  <th className="px-6 py-2 text-left text-xs font-semibold uppercase tracking-wide text-gray-400 min-w-[200px]">Prompt</th>
                  {platforms.map((p) => (
                    agg?.[p] != null ? (
                      <th key={p} className="px-4 py-2 text-right text-xs font-semibold uppercase tracking-wide text-gray-400 whitespace-nowrap">
                        {PLATFORM_CONFIG[p].label}
                      </th>
                    ) : null
                  ))}
                </tr>
              </thead>
              <tbody>
                {perPrompt.slice(0, 10).map((row, i) => (
                  <tr key={i} className="border-b border-gray-50 last:border-0">
                    <td className="px-6 py-3 text-gray-700 text-xs max-w-xs">
                      <span className="line-clamp-2">{row.prompt}</span>
                    </td>
                    {platforms.map((p) => {
                      if (agg?.[p] == null) return null;
                      const mentionPct = row[`${p}_brand_mention_pct` as keyof typeof row] as number | undefined;
                      const mentioned = (mentionPct ?? 0) > 0;
                      return (
                        <td key={p} className="px-4 py-3 text-right">
                          <span
                            className={`inline-flex items-center justify-center rounded-full w-6 h-6 text-xs font-bold ${mentioned ? "bg-emerald-100 text-emerald-700" : "bg-gray-100 text-gray-400"}`}
                          >
                            {mentioned ? "✓" : "—"}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
