import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import {
  fetchPromptPerformanceContext,
  fetchPromptSentiment,
  highlightPromptReply,
  runPromptPerformanceProbes,
  trackPromptCompetitor,
} from "../api/client";
import type {
  CategorySentiment,
  LiveProbePerPrompt,
  LiveProbeResult,
  MentionScores,
  PromptPerformanceContext,
  PromptSentimentAnalysis,
} from "../types";
import { filterSentimentCategories } from "../lib/customPrompts";
import { visibilityByCategory } from "../lib/categoryVisibility";
import {
  activeProbePlatforms,
  isProbePlatformActive,
  probePlatformsLabel,
  type ProbePlatform,
} from "../lib/probePlatforms";
import { Card, CardDescription, CardTitle } from "./ui/Card";

const SOV_GREEN = "#00b894";
const SOV_BLUE = "#0984e3";
const PLATFORM_GEMINI = "#4285F4";
const PLATFORM_OPENAI = "#10a37f";
const PLATFORM_CLAUDE = "#D97706";

// ── Platform icons ────────────────────────────────────────────────────────────

function GeminiIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-label="Gemini">
      <defs>
        <linearGradient id="gem-g" x1="0" y1="0" x2="24" y2="24" gradientUnits="userSpaceOnUse">
          <stop stopColor="#4285F4" />
          <stop offset="1" stopColor="#9333EA" />
        </linearGradient>
      </defs>
      <path
        fill="url(#gem-g)"
        d="M12 1.5C12 1.5 13 9 17 12C21 15 23.5 15.3 23.5 15.3C23.5 15.3 21 15.6 17 18.5C13 21.4 12 22.5 12 22.5C12 22.5 11 21.4 7 18.5C3 15.6.5 15.3.5 15.3C.5 15.3 3 15 7 12C11 9 12 1.5 12 1.5Z"
      />
    </svg>
  );
}

function OpenAIIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="#10a37f" aria-label="OpenAI">
      <path d="M22.3 9.8a5.9 5.9 0 00-.5-4.9A6 6 0 0015.3 2a5.9 5.9 0 00-4.5-2A6 6 0 005.1 4.2 5.9 5.9 0 001.7 7a6 6 0 00.7 7.2 5.9 5.9 0 00.5 4.9A6 6 0 009.4 22a5.9 5.9 0 004.5 2A6 6 0 0019.6 20a5.9 5.9 0 003.4-2.9 6 6 0 00-.7-7.3zM13.2 21a4.4 4.4 0 01-2.8-1l4.8-2.8a.8.8 0 00.4-.7v-6.7l2 1.2v5.6A4.5 4.5 0 0113.2 21zm-9.6-4.1a4.4 4.4 0 01-.5-3l4.8 2.8a.8.8 0 00.8 0l5.8-3.4v2.3l-5.3 3.1a4.5 4.5 0 01-5.6-1.8zM2.3 7.9a4.5 4.5 0 012.4-2v5.5a.8.8 0 00.4.7l5.8 3.3-2 1.2-4.8-2.8A4.5 4.5 0 012.3 7.9zm16.5 3.9l-5.8-3.4 2-1.2 4.8 2.8a4.5 4.5 0 01-.1 6.4v-5.4a.8.8 0 00-.4-.7l-.5.5zm2-3-.1-.1-4.8-2.8a.8.8 0 00-.8 0L9.4 9.2V6.9l4.8-2.8a4.5 4.5 0 016.6 4.7zM8.3 12.9l-2-1.2V6.1a4.5 4.5 0 017.4-3.5l-4.8 2.8a.8.8 0 00-.4.7l-.2 6.8zm1.1-2.4l2.6-1.5 2.6 1.5v3l-2.6 1.5-2.6-1.5v-3z" />
    </svg>
  );
}

function ClaudeIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-label="Claude">
      <circle cx="12" cy="12" r="11" fill="#FEF3C7" />
      <path
        fill={PLATFORM_CLAUDE}
        d="M12 3l2.1 6.4h6.7l-5.4 3.9 2.1 6.4L12 15.9l-5.5 3.8 2.1-6.4-5.4-3.9h6.7z"
      />
    </svg>
  );
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function avgPerCompetitorSovPct(
  perPrompt: LiveProbePerPrompt[],
  platform: "gemini" | "openai" | "claude",
): number {
  const compHits: Record<string, number> = {};
  let grandTotal = 0;
  for (const row of perPrompt) {
    const scores: MentionScores | undefined =
      platform === "gemini"
        ? row.mention_scores_gemini
        : platform === "openai"
          ? row.mention_scores_openai
          : row.mention_scores_claude;
    if (!scores) continue;
    const brand = Number(scores.brand_signal ?? 0);
    const compsTotal = Number(scores.competitors_combined_hits ?? 0);
    const total = brand + compsTotal;
    if (total <= 0) continue;
    grandTotal += total;
    for (const [key, hits] of Object.entries(scores.competitor_detail ?? {})) {
      compHits[key] = (compHits[key] ?? 0) + Number(hits);
    }
  }
  const keys = Object.keys(compHits);
  if (!keys.length || grandTotal <= 0) return 0;
  const shares = keys.map((k) => (compHits[k] / grandTotal) * 100);
  return shares.reduce((a, b) => a + b, 0) / shares.length;
}

// ── Sentiment components ──────────────────────────────────────────────────────

const SENTIMENT_COLORS: Record<string, { fg: string; bg: string }> = {
  Positive: { fg: SOV_GREEN, bg: "#e8f8f5" },
  Mixed: { fg: "#e17055", bg: "#fdf0ed" },
  Negative: { fg: "#d63031", bg: "#ffeaea" },
  Neutral: { fg: "#636e72", bg: "#f0f0f0" },
};

function SentimentChip({ value }: { value: string }) {
  const { fg, bg } = SENTIMENT_COLORS[value] ?? { fg: "#636e72", bg: "#f0f0f0" };
  return (
    <span
      className="inline-block px-2.5 py-0.5 rounded-full text-xs font-bold"
      style={{ background: bg, color: fg }}
    >
      {value}
    </span>
  );
}

function OverallSentimentCard({ sentiment }: { sentiment: PromptSentimentAnalysis }) {
  return (
    <div className="rounded-xl bg-[#0d0d0d] text-white p-5 mb-5">
      <div className="flex items-center gap-3 mb-3">
        <span className="text-base font-bold">Overall AI Sentiment</span>
        <SentimentChip value={sentiment.overall_sentiment} />
      </div>
      <p className="text-sm leading-relaxed" style={{ color: "rgba(255,255,255,.75)" }}>
        {sentiment.overall_summary}
      </p>
    </div>
  );
}

function SentimentByCategory({
  categories,
  visibilityByCategory,
  brandLabel,
}: {
  categories: CategorySentiment[];
  visibilityByCategory: Record<string, { brandPct: number; compPct: number }>;
  brandLabel: string;
}) {
  if (!categories.length) return null;
  return (
    <div className="mb-6">
      <h4 className="text-sm font-bold text-brand-dark mb-3">Sentiment by Category</h4>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {categories.map((cat, i) => {
          const vis = visibilityByCategory[cat.category];
          return (
          <div
            key={i}
            className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
          >
            <div className="flex items-start justify-between gap-2 mb-2">
              <span className="text-sm font-semibold text-brand-dark leading-tight">
                {cat.category}
              </span>
              <SentimentChip value={cat.sentiment} />
            </div>
            <p className="text-xs text-gray-500 leading-relaxed">{cat.summary}</p>
            {vis ? (
              <CategoryVisibilityBars
                brandPct={vis.brandPct}
                compPct={vis.compPct}
                brandLabel={brandLabel}
              />
            ) : null}
          </div>
          );
        })}
      </div>
    </div>
  );
}

function CategoryVisibilityBars({
  brandPct,
  compPct,
  brandLabel,
}: {
  brandPct: number;
  compPct: number;
  brandLabel: string;
}) {
  const maxPct = Math.max(brandPct, compPct, 1);
  const trackHeight = 48;
  const brandHeight = (brandPct / maxPct) * trackHeight;
  const compHeight = (compPct / maxPct) * trackHeight;
  const shortBrand =
    brandLabel.length > 10 ? `${brandLabel.slice(0, 9)}…` : brandLabel;

  return (
    <div className="mt-3 pt-3 border-t border-gray-100">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">
        Visibility
      </p>
      <div className="flex items-end justify-center gap-5">
        <div className="flex flex-col items-center gap-1 min-w-[52px]">
          <div
            className="w-4 rounded-sm bg-gray-100 flex items-end overflow-hidden"
            style={{ height: trackHeight }}
          >
            <div
              className="w-full rounded-sm"
              style={{
                height: `${Math.max(brandHeight, brandPct > 0 ? 2 : 0)}px`,
                background: SOV_GREEN,
              }}
            />
          </div>
          <span className="text-[10px] text-gray-500 text-center leading-tight" title={brandLabel}>
            {shortBrand}
          </span>
          <span className="text-[10px] font-semibold text-brand-dark">{brandPct.toFixed(1)}%</span>
        </div>
        <div className="flex flex-col items-center gap-1 min-w-[52px]">
          <div
            className="w-4 rounded-sm bg-gray-100 flex items-end overflow-hidden"
            style={{ height: trackHeight }}
          >
            <div
              className="w-full rounded-sm"
              style={{
                height: `${Math.max(compHeight, compPct > 0 ? 2 : 0)}px`,
                background: SOV_BLUE,
              }}
            />
          </div>
          <span className="text-[10px] text-gray-500 text-center leading-tight">Avg. comp.</span>
          <span className="text-[10px] font-semibold text-gray-600">{compPct.toFixed(1)}%</span>
        </div>
      </div>
    </div>
  );
}

// ── SOV bar row ───────────────────────────────────────────────────────────────

function SovBarRow({
  label,
  pct,
  maxPct,
  color,
  isHighlight,
}: {
  label: string;
  pct: number;
  maxPct: number;
  color: string;
  isHighlight?: boolean;
}) {
  const width = maxPct > 0 ? (pct / maxPct) * 100 : 0;
  return (
    <div className="flex items-center gap-3 mb-2 last:mb-0">
      <span className="text-xs text-gray-600 shrink-0 text-right truncate" style={{ width: 90 }} title={label}>
        {label}
      </span>
      <div className="flex-1 h-5 bg-gray-100 rounded overflow-hidden">
        <div
          className="h-full rounded transition-all duration-300"
          style={{ width: `${Math.max(width, pct > 0 ? 2 : 0)}%`, background: color }}
        />
      </div>
      <span className={`text-xs w-10 shrink-0 ${isHighlight ? "font-bold text-brand-dark" : "text-gray-500"}`}>
        {pct.toFixed(1)}%
      </span>
    </div>
  );
}

function PlatformSovCard({
  title,
  subtitle,
  accentColor,
  icon,
  brandLabel,
  brandPct,
  compPct,
}: {
  title: string;
  subtitle?: string;
  accentColor: string;
  icon?: React.ReactNode;
  brandLabel: string;
  brandPct: number;
  compPct: number;
}) {
  const maxPct = Math.max(brandPct, compPct, 1);
  return (
    <div
      className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
      style={{ borderTopWidth: 3, borderTopColor: accentColor }}
    >
      <div className="flex items-center gap-2 mb-0.5">
        {icon}
        <p className="text-sm font-semibold text-brand-dark">{title}</p>
      </div>
      {subtitle ? <p className="text-xs text-gray-400 mb-3">{subtitle}</p> : <div className="mb-3" />}
      <SovBarRow label={brandLabel} pct={brandPct} maxPct={maxPct} color={SOV_GREEN} isHighlight />
      <SovBarRow label="Avg. competitor" pct={compPct} maxPct={maxPct} color={SOV_BLUE} />
    </div>
  );
}

// ── Reply highlight ───────────────────────────────────────────────────────────

function ReplyHighlight({ auditSlug, text, enabled }: { auditSlug: string; text: string; enabled: boolean }) {
  const [html, setHtml] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!enabled || !text.trim()) { setHtml(null); return; }
    let cancelled = false;
    setLoading(true);
    highlightPromptReply(auditSlug, text)
      .then((r) => { if (!cancelled) setHtml(r.html); })
      .catch(() => { if (!cancelled) setHtml(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [auditSlug, text, enabled]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500 py-4">
        <Loader2 className="w-4 h-4 animate-spin" />Loading reply…
      </div>
    );
  }
  if (html) {
    return <div className="prompt-reply-html text-sm" dangerouslySetInnerHTML={{ __html: html }} />;
  }
  return (
    <pre className="text-sm whitespace-pre-wrap border border-gray-200 rounded-lg p-3 max-h-96 overflow-auto bg-gray-50">
      {text || "(empty response)"}
    </pre>
  );
}

// ── Per-prompt card ───────────────────────────────────────────────────────────

const PLATFORM_CONFIG = [
  { key: "gemini" as const, label: "Gemini", color: PLATFORM_GEMINI, icon: <GeminiIcon size={14} /> },
  { key: "openai" as const, label: "OpenAI", color: PLATFORM_OPENAI, icon: <OpenAIIcon size={14} /> },
  { key: "claude" as const, label: "Claude", color: PLATFORM_CLAUDE, icon: <ClaudeIcon size={14} /> },
];

function PromptCard({
  auditSlug,
  row,
  brandLabel,
  productLabel,
  activePlatforms,
}: {
  auditSlug: string;
  row: LiveProbePerPrompt;
  brandLabel: string;
  productLabel?: string;
  activePlatforms: ProbePlatform[];
}) {
  const [open, setOpen] = useState(false);
  const [activeModel, setActiveModel] = useState<ProbePlatform>(
    activePlatforms[0] ?? "gemini",
  );

  const platforms = PLATFORM_CONFIG.filter((p) => {
    if (!activePlatforms.includes(p.key)) return false;
    const resp = row[`${p.key}_response` as keyof LiveProbePerPrompt];
    const err = row[`error_${p.key}` as keyof LiveProbePerPrompt];
    return resp || err;
  });

  return (
    <div className="rounded-xl bg-stone-50 border border-stone-200 p-4 mb-3">
      {/* Product label */}
      {productLabel && (
        <span className="inline-block text-[11px] font-semibold bg-stone-200 text-stone-600 px-2 py-0.5 rounded-full mb-2">
          {productLabel}
        </span>
      )}

      {/* Q# + prompt */}
      <div className="flex items-start gap-2 mb-3">
        <span className="text-[11px] font-bold text-gray-400 shrink-0 pt-0.5">Q{row.index}</span>
        <span className="text-sm font-semibold text-brand-dark leading-snug">{row.prompt}</span>
      </div>

      {/* Platform mention indicators */}
      <div className="flex flex-wrap gap-2 mb-3">
        {platforms.map((p) => {
          const resp = String(row[`${p.key}_response` as keyof LiveProbePerPrompt] ?? "");
          const err = row[`error_${p.key}` as keyof LiveProbePerPrompt];
          const mentioned = !err && brandLabel && resp.toLowerCase().includes(brandLabel.toLowerCase());
          return (
            <span
              key={p.key}
              className="inline-flex items-center gap-1.5 text-[11px] font-semibold px-2.5 py-0.5 rounded-full"
              style={{ background: `${p.color}18`, color: p.color }}
            >
              {p.icon}
              {p.label}
              <span style={{ color: mentioned ? SOV_GREEN : "#e17055" }}>
                {mentioned ? "✓" : "✗"}
              </span>
            </span>
          );
        })}
      </div>

      {/* Expand / collapse */}
      <button
        type="button"
        className="flex items-center gap-1.5 text-xs font-semibold text-gray-500 hover:text-gray-700 transition-colors"
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronDown
          className="w-3.5 h-3.5 transition-transform duration-150"
          style={{ transform: open ? "rotate(180deg)" : "rotate(0deg)" }}
          strokeWidth={2.5}
        />
        {open ? "Hide" : "View"} AI responses
      </button>

      {open && (
        <div className="mt-4">
          {/* Platform tabs */}
          <div className="flex flex-wrap gap-2 mb-4">
            {platforms.map((p) => (
              <button
                key={p.key}
                type="button"
                onClick={() => setActiveModel(p.key)}
                className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-full transition-colors"
                style={
                  activeModel === p.key
                    ? { background: p.color, color: "#fff" }
                    : { background: `${p.color}18`, color: p.color }
                }
              >
                {p.icon}
                {p.label}
              </button>
            ))}
          </div>

          {/* Active platform response */}
          {platforms.map((p) => {
            if (p.key !== activeModel) return null;
            const err = row[`error_${p.key}` as keyof LiveProbePerPrompt] as string | undefined;
            const body = row[`${p.key}_response` as keyof LiveProbePerPrompt] as string | undefined;
            const brandPct = Number(row[`${p.key}_brand_mention_pct` as keyof LiveProbePerPrompt] ?? 0);
            const compPct = Number(row[`${p.key}_competitor_mention_pct` as keyof LiveProbePerPrompt] ?? 0);
            const detail = (
              p.key === "gemini"
                ? row.mention_scores_gemini?.competitor_detail
                : p.key === "openai"
                  ? row.mention_scores_openai?.competitor_detail
                  : row.mention_scores_claude?.competitor_detail
            );
            return (
              <div key={p.key} className="rounded-lg border-l-2 pl-4 py-1" style={{ borderColor: p.color }}>
                <div className="flex flex-wrap gap-4 mb-3">
                  <div>
                    <p className="text-xs text-gray-400">Brand mention</p>
                    <p className="text-lg font-bold" style={{ color: SOV_GREEN }}>{brandPct.toFixed(1)}%</p>
                  </div>
                  <div>
                    <p className="text-xs text-gray-400">Competitors</p>
                    <p className="text-lg font-bold" style={{ color: SOV_BLUE }}>{compPct.toFixed(1)}%</p>
                  </div>
                </div>
                {detail && Object.keys(detail).length > 0 && (
                  <p className="text-xs text-gray-500 mb-3">
                    Competitor hits: {Object.entries(detail).sort(([a], [b]) => a.localeCompare(b)).slice(0, 8).map(([k, v]) => `${k}: ${v}`).join(", ")}
                  </p>
                )}
                {err ? (
                  <div className="alert-error text-sm">{err}</div>
                ) : (
                  <ReplyHighlight auditSlug={auditSlug} text={String(body ?? "")} enabled />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Per-prompt list with show-more ────────────────────────────────────────────

function PerPromptList({
  auditSlug,
  ctx,
  live,
  brandLabel,
  activePlatforms,
}: {
  auditSlug: string;
  ctx: PromptPerformanceContext;
  live: LiveProbeResult;
  brandLabel: string;
  activePlatforms: ProbePlatform[];
}) {
  const [showAll, setShowAll] = useState(false);
  const DEFAULT_VISIBLE = 3;

  // Flatten prompts preserving product/service labels when pss is enabled
  const allRows = useMemo(() => {
    const perPrompt = (live.per_prompt ?? []).filter(Boolean) as LiveProbePerPrompt[];
    const mappingRows =
      ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows;
    if (!ctx.use_pss || !mappingRows.length) {
      return perPrompt.map((row) => ({ row, productLabel: "" }));
    }
    const result: { row: LiveProbePerPrompt; productLabel: string }[] = [];
    let idx = 0;
    for (const pssRow of mappingRows) {
      for (const prompt of pssRow.prompts) {
        if (!prompt.trim()) continue;
        if (idx >= perPrompt.length) break;
        result.push({ row: perPrompt[idx], productLabel: pssRow.product_or_service });
        idx++;
      }
    }
    // Append any remaining rows not covered by pss_rows
    while (idx < perPrompt.length) {
      result.push({ row: perPrompt[idx], productLabel: "" });
      idx++;
    }
    return result;
  }, [live, ctx]);

  const visible = showAll ? allRows : allRows.slice(0, DEFAULT_VISIBLE);
  const hidden = allRows.length - DEFAULT_VISIBLE;

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h4 className="text-sm font-bold text-brand-dark">Per-Prompt AI Responses</h4>
        <span className="text-xs text-gray-400">
          {showAll || allRows.length <= DEFAULT_VISIBLE
            ? `${allRows.length} prompt${allRows.length !== 1 ? "s" : ""}`
            : `Showing ${DEFAULT_VISIBLE} of ${allRows.length}`}
        </span>
      </div>

      {visible.map(({ row, productLabel }, i) => (
        <PromptCard
          key={`${row.index}-${i}`}
          auditSlug={auditSlug}
          row={row}
          brandLabel={brandLabel}
          productLabel={productLabel}
          activePlatforms={activePlatforms}
        />
      ))}

      {!showAll && hidden > 0 && (
        <button
          type="button"
          className="w-full mt-1 py-2.5 rounded-xl border border-dashed border-gray-300 text-sm font-semibold text-gray-500 hover:border-gray-400 hover:text-gray-700 transition-colors"
          onClick={() => setShowAll(true)}
        >
          Show {hidden} more prompt{hidden !== 1 ? "s" : ""}
        </button>
      )}
    </div>
  );
}

// ── Detected competitors table ────────────────────────────────────────────────

function DetectedCompetitorsTable({
  auditSlug,
  live,
  onTracked,
}: {
  auditSlug: string;
  live: LiveProbeResult;
  onTracked: () => void;
}) {
  const rd = live.reply_detected_brands;
  const rows = useMemo(() => {
    if (!Array.isArray(rd)) return [];
    const out: { brand_name: string; website_url: string }[] = [];
    const seen = new Set<string>();
    for (const x of rd) {
      const bn = String(x?.brand_name ?? "").trim();
      const u = String(x?.website_url ?? "").trim();
      if (!bn && !u) continue;
      const key = (u || bn).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ brand_name: bn || "—", website_url: u });
    }
    return out;
  }, [rd]);

  const [tracking, setTracking] = useState<string | null>(null);
  if (!rows.length) return null;

  return (
    <div className="mt-8 pt-6 border-t border-gray-100">
      <h4 className="text-sm font-bold text-brand-dark mb-1">Competitors found from SOV analysis</h4>
      <p className="text-sm text-gray-500 mb-4">
        Brands inferred from assistant reply excerpts across all probed models.
      </p>
      <ul className="space-y-2">
        {rows.map((r, i) => (
          <li
            key={`${r.website_url}-${i}`}
            className="flex flex-wrap items-center gap-3 p-3 rounded-lg border border-gray-200 bg-white"
          >
            <div className="flex-1 min-w-[140px]">
              <p className="font-medium text-brand-dark text-sm">{r.brand_name}</p>
              {r.website_url ? (
                <p className="text-xs text-gray-500 break-all">{r.website_url}</p>
              ) : (
                <p className="text-xs text-gray-400">No homepage URL</p>
              )}
            </div>
            {r.website_url ? (
              <button
                type="button"
                disabled={!!tracking}
                className="btn-secondary text-xs py-1.5"
                onClick={async () => {
                  setTracking(r.website_url);
                  try {
                    await trackPromptCompetitor(auditSlug, r.website_url, r.brand_name);
                    onTracked();
                  } catch {
                    // ignore
                  } finally {
                    setTracking(null);
                  }
                }}
              >
                {tracking === r.website_url ? (
                  <><Loader2 className="w-3 h-3 animate-spin" /> Saving…</>
                ) : (
                  "Track competitor"
                )}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export function PromptPerformanceSection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const [ctx, setCtx] = useState<PromptPerformanceContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeMsg, setProbeMsg] = useState<string | null>(null);
  const [sentiment, setSentiment] = useState<PromptSentimentAnalysis | null>(null);

  const slug = auditDirOrSlug;

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchPromptPerformanceContext(slug)
      .then((data) => setCtx(data))
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => { load(); }, [load]);

  const live = ctx?.live_probe ?? null;
  const hasProbes = Boolean(live?.per_prompt?.length);
  const activePlatforms = useMemo(() => activeProbePlatforms(live), [live]);
  const probeLabel = probePlatformsLabel(activePlatforms);

  // Load sentiment whenever probes are available
  useEffect(() => {
    if (!hasProbes) return;
    fetchPromptSentiment(slug)
      .then((r) => { if (r.sentiment) setSentiment(r.sentiment); })
      .catch(() => {});
  }, [slug, hasProbes]);

  const sentimentCategories = useMemo(
    () =>
      sentiment
        ? filterSentimentCategories(
            sentiment.by_category,
            ctx?.probed_pss_rows ?? ctx?.pss_rows,
          )
        : [],
    [sentiment, ctx?.probed_pss_rows, ctx?.pss_rows],
  );

  const categoryVisibility = useMemo(() => {
    if (!live?.per_prompt?.length || !ctx) return {};
    const mappingRows = ctx.probed_pss_rows?.length
      ? ctx.probed_pss_rows
      : ctx.pss_rows;
    if (!mappingRows.length) return {};
    return visibilityByCategory(mappingRows, live, activePlatforms);
  }, [live, ctx, activePlatforms]);

  const runProbes = async () => {
    setProbing(true);
    setProbeMsg(null);
    setSentiment(null);
    try {
      const res = await runPromptPerformanceProbes(slug, true);
      setCtx((prev) => prev ? { ...prev, live_probe: res.live_probe, highlight: res.highlight } : prev);
      setProbeMsg("Live probes complete — sentiment and SOV analysis updated.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Probe run failed");
    } finally {
      setProbing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="w-8 h-8 animate-spin text-brand-accent" />
      </div>
    );
  }
  if (error && !ctx) return <div className="alert-error">{error}</div>;
  if (!ctx) return null;

  const brandLabel = ctx.brand_name?.trim() || "Brand";
  const mcc = ctx.primary_market?.country;
  const mid = ctx.primary_market?.country_id;

  // SOV numbers from aggregate
  const perPrompt = (live?.per_prompt ?? []) as LiveProbePerPrompt[];
  const hasClaude = isProbePlatformActive("claude", live);
  const numPlatforms = Math.max(activePlatforms.length, 1);

  const gBp = live?.aggregate?.gemini?.brand_share_pct ?? 0;
  const gCp = avgPerCompetitorSovPct(perPrompt, "gemini");
  const oBp = live?.aggregate?.openai?.brand_share_pct ?? 0;
  const oCp = avgPerCompetitorSovPct(perPrompt, "openai");
  const cBp = live?.aggregate?.claude?.brand_share_pct ?? 0;
  const cCp = avgPerCompetitorSovPct(perPrompt, "claude");

  const platformBrandPcts: Record<ProbePlatform, number> = {
    gemini: gBp,
    openai: oBp,
    claude: cBp,
  };
  const platformCompPcts: Record<ProbePlatform, number> = {
    gemini: gCp,
    openai: oCp,
    claude: cCp,
  };
  const overallBp =
    activePlatforms.reduce((sum, p) => sum + platformBrandPcts[p], 0) / numPlatforms;
  const overallCp =
    activePlatforms.reduce((sum, p) => sum + platformCompPcts[p], 0) / numPlatforms;

  return (
    <Card className="!mb-0">
      <CardTitle>Prompt Performance</CardTitle>
      <CardDescription>
        Live AI probes measure how often your brand is mentioned vs competitors across your
        configured AI assistants for each tracked prompt.
      </CardDescription>

      {/* ── Brand / Website / Competitors header ── */}
      <div className="grid sm:grid-cols-3 gap-4 mb-5">
        <div className="rounded-xl bg-white border-2 p-4 shadow-sm" style={{ borderColor: SOV_GREEN }}>
          <p className="text-[10px] font-bold uppercase tracking-wider mb-1.5" style={{ color: SOV_GREEN }}>
            Your Brand
          </p>
          <p className="text-xl font-semibold text-brand-dark break-words">{ctx.brand_name || "—"}</p>
        </div>
        <div className="rounded-xl bg-white border-2 p-4 shadow-sm" style={{ borderColor: SOV_GREEN }}>
          <p className="text-[10px] font-bold uppercase tracking-wider mb-1.5" style={{ color: SOV_GREEN }}>
            Your Website
          </p>
          <p className="text-base font-semibold text-brand-dark break-all">{ctx.brand_site_url || "—"}</p>
        </div>
        <div className="rounded-xl bg-white border-2 p-4 shadow-sm" style={{ borderColor: SOV_BLUE }}>
          <p className="text-[10px] font-bold uppercase tracking-wider mb-1.5" style={{ color: SOV_BLUE }}>
            Tracked Competitors
          </p>
          {ctx.competitors.length > 0 ? (
            <ul className="space-y-1">
              {ctx.competitors.map((c, i) => (
                <li key={i} className="text-sm text-brand-dark truncate" title={c.competitor_brand || c.competitor_website}>
                  {c.competitor_brand || c.competitor_website || "—"}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-gray-400">None yet — track from detected competitors below</p>
          )}
        </div>
      </div>

      {(mcc || mid) && (
        <p className="text-xs text-gray-500 mb-4">
          Primary market: <strong>{mcc || "—"}</strong>{mid ? ` (${mid})` : ""}
        </p>
      )}

      {/* ── Run probes button ── */}
      {ctx.prompt_count > 0 && (
        <div className="mb-6">
          <button
            type="button"
            className="btn-secondary"
            disabled={probing}
            onClick={runProbes}
          >
            {probing ? (
              <><Loader2 className="w-4 h-4 animate-spin" />Running probes…</>
            ) : (
              hasProbes
                ? `Re-run live probes (${probeLabel})`
                : `Run live probes (${probeLabel})`
            )}
          </button>
          {probeMsg && <p className="alert-success mt-3 text-sm">{probeMsg}</p>}
          {!hasProbes && ctx.prompt_count > 0 && (
            <p className="text-sm text-gray-500 mt-2">
              {ctx.use_pss
                ? `${(ctx.probed_pss_rows ?? ctx.pss_rows).length} product line(s), ${ctx.prompt_count} prompts ready for probing.`
                : `${ctx.prompt_count} prompts ready.`}
            </p>
          )}
        </div>
      )}

      {ctx.prompt_count === 0 && (
        <div className="alert-info mb-4">
          No prompts on file. Complete the wizard products &amp; prompts step, then re-run the audit.
        </div>
      )}

      {/* ── Post-probe analysis ── */}
      {hasProbes && live && (
        <div className="space-y-6">

          {/* Overall AI sentiment */}
          {sentiment && <OverallSentimentCard sentiment={sentiment} />}

          {/* Sentiment by category */}
          {sentimentCategories.length ? (
            <SentimentByCategory
              categories={sentimentCategories}
              visibilityByCategory={categoryVisibility}
              brandLabel={brandLabel}
            />
          ) : null}

          {/* Share of voice */}
          <div>
            <h4 className="text-sm font-bold text-brand-dark mb-1">
              Share of voice from live replies (all prompts combined)
            </h4>
            {live.disclaimer && (
              <p className="text-xs text-gray-500 mb-3">{live.disclaimer}</p>
            )}
            <PlatformSovCard
              title="Overall (all platforms)"
              subtitle={`Brand share vs avg per-competitor share · ${numPlatforms} platform${numPlatforms > 1 ? "s" : ""} averaged`}
              accentColor={SOV_GREEN}
              brandLabel={brandLabel}
              brandPct={overallBp}
              compPct={overallCp}
            />
            <div className={`grid gap-4 mt-4 ${activePlatforms.length >= 3 ? "md:grid-cols-3" : activePlatforms.length === 2 ? "md:grid-cols-2" : "md:grid-cols-1"}`}>
              {isProbePlatformActive("gemini", live) && (
              <PlatformSovCard
                title="Gemini"
                accentColor={PLATFORM_GEMINI}
                icon={<GeminiIcon size={18} />}
                brandLabel={brandLabel}
                brandPct={gBp}
                compPct={gCp}
              />
              )}
              {isProbePlatformActive("openai", live) && (
              <PlatformSovCard
                title="OpenAI"
                accentColor={PLATFORM_OPENAI}
                icon={<OpenAIIcon size={18} />}
                brandLabel={brandLabel}
                brandPct={oBp}
                compPct={oCp}
              />
              )}
              {hasClaude && (
                <PlatformSovCard
                  title="Claude"
                  accentColor={PLATFORM_CLAUDE}
                  icon={<ClaudeIcon size={18} />}
                  brandLabel={brandLabel}
                  brandPct={cBp}
                  compPct={cCp}
                />
              )}
            </div>
          </div>

          {/* Per-prompt responses */}
          <PerPromptList
            auditSlug={slug}
            ctx={ctx}
            live={live}
            brandLabel={brandLabel}
            activePlatforms={activePlatforms}
          />

          {/* Detected competitors table */}
          {live.reply_detected_brands_error && !live.reply_detected_brands?.length ? (
            <p className="text-xs text-gray-500">
              Reply brand detection: {String(live.reply_detected_brands_error).slice(0, 220)}
            </p>
          ) : null}
          <DetectedCompetitorsTable auditSlug={slug} live={live} onTracked={load} />
        </div>
      )}

      {error && ctx && <p className="alert-error mt-4 text-sm">{error}</p>}
    </Card>
  );
}
