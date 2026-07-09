import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ExternalLink, Loader2 } from "lucide-react";
import {
  fetchPromptPerformanceContext,
  fetchPromptSentiment,
  highlightPromptReply,
  runAioProbes,
  runPromptPerformanceProbes,
  trackPromptCompetitor,
} from "../api/client";
import type {
  AioProbeResult,
  CategorySentiment,
  CitationItem,
  LiveProbePerPrompt,
  LiveProbeResult,
  MentionScores,
  PromptPerformanceContext,
  PromptSentimentAnalysis,
  TopCitedSite,
  TopCitedUrl,
} from "../types";
import { filterSentimentCategories } from "../lib/customPrompts";
import { visibilityByCategory } from "../lib/categoryVisibility";
import {
  textMentionsBrand,
} from "../lib/brandMatch";
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
const PLATFORM_GOOGLE_AIO = "#EA4335";

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

function GoogleAioIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-label="Google AI Summaries">
      <circle cx="12" cy="12" r="11" fill="#fff" stroke="#e0e0e0" strokeWidth="0.8" />
      <text
        x="12" y="16"
        textAnchor="middle"
        fontSize="13"
        fontWeight="bold"
        fontFamily="Arial,sans-serif"
        fill={PLATFORM_GOOGLE_AIO}
      >G</text>
    </svg>
  );
}

// ── Small helpers ─────────────────────────────────────────────────────────────

function avgPerCompetitorSovPct(
  perPrompt: LiveProbePerPrompt[],
  platform: ProbePlatform,
): number {
  const compHits: Record<string, number> = {};
  let grandTotal = 0;
  for (const row of perPrompt) {
    const scores: MentionScores | undefined =
      (row as Record<string, MentionScores | undefined>)[`mention_scores_${platform}`];
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

// ── Citations ─────────────────────────────────────────────────────────────────

function FaviconImg({ domain }: { domain: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <span className="w-4 h-4 rounded-sm bg-gray-200 inline-block shrink-0" />
    );
  }
  return (
    <img
      src={`https://www.google.com/s2/favicons?domain=${domain}&sz=16`}
      alt=""
      width={16}
      height={16}
      className="rounded-sm shrink-0"
      onError={() => setFailed(true)}
    />
  );
}

function CitationsList({
  citations,
  platformColor,
}: {
  citations: CitationItem[];
  platformColor: string;
}) {
  if (!citations.length) {
    return (
      <p className="text-xs text-gray-400 italic">No external sites cited in this response.</p>
    );
  }
  return (
    <ul className="space-y-1.5">
      {citations.map((c, i) => (
        <li key={i} className="flex items-center gap-2 text-xs">
          <FaviconImg domain={c.domain} />
          <a
            href={c.url}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium hover:underline truncate"
            style={{ color: platformColor }}
          >
            {c.domain}
          </a>
          <ExternalLink className="w-3 h-3 shrink-0 text-gray-300" />
        </li>
      ))}
    </ul>
  );
}

function CitationsSummaryTable({
  sites,
  urls,
  activePlatforms,
}: {
  sites: TopCitedSite[];
  urls?: TopCitedUrl[];
  activePlatforms: string[];
}) {
  // Prefer URL-level data if available; fall back to domain-level sites
  const rows = useMemo(() => {
    if (urls?.length) {
      return urls
        .filter((u) => u.probe_platforms.some((p) => activePlatforms.includes(p)))
        .slice(0, 15);
    }
    return sites
      .filter((s) => s.platforms.some((p) => activePlatforms.includes(p)))
      .slice(0, 15)
      .map((s) => ({
        url: s.example_url ?? `https://${s.domain}`,
        domain: s.domain,
        frequency: s.count,
        brand_mentioned: s.brand_mentioned ?? false,
        competitor_mentioned: s.competitor_mentioned ?? false,
        competitor_names: s.competitor_names ?? [],
        content_type: undefined as string | undefined,
        channel_type: undefined as string | undefined,
      }));
  }, [urls, sites, activePlatforms]);

  if (!rows.length) return null;

  return (
    <div className="mb-6">
      <h4 className="text-sm font-bold text-brand-dark mb-1">Most Cited Sites</h4>
      <p className="text-xs text-gray-500 mb-3">
        Websites most commonly referenced in AI-generated answers. Full breakdown in the{" "}
        <strong>Citations</strong> tab.
      </p>
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5">URL</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-3 py-2.5 whitespace-nowrap">Frequency</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-3 py-2.5 whitespace-nowrap">Brand</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-3 py-2.5 whitespace-nowrap hidden sm:table-cell">Competitors</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {rows.map((row, i) => (
              <tr key={i} className="hover:bg-gray-50/60 transition-colors">
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <FaviconImg domain={row.domain} />
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-brand-dark truncate">{row.domain}</p>
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[10px] text-gray-400 hover:text-brand-dark truncate block"
                      >
                        {row.url}
                      </a>
                    </div>
                  </div>
                </td>
                <td className="px-3 py-3 text-center">
                  <span className="inline-block font-bold text-brand-dark text-xs py-0.5 px-2 bg-gray-100 rounded-full min-w-[24px] text-center">
                    {row.frequency}
                  </span>
                </td>
                <td className="px-3 py-3 text-center text-xs">
                  {row.brand_mentioned
                    ? <span className="font-semibold text-emerald-600">Yes</span>
                    : <span className="text-gray-300">—</span>}
                </td>
                <td className="px-3 py-3 text-center text-xs hidden sm:table-cell">
                  {row.competitor_mentioned
                    ? <span className="font-semibold text-blue-600">Yes</span>
                    : <span className="text-gray-300">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Google AIO ────────────────────────────────────────────────────────────────

const AIO_COLOR = "#4285F4";

function GoogleAioSection({
  aio,
  onRun,
  running,
}: {
  aio: AioProbeResult | null | undefined;
  onRun: () => void;
  running: boolean;
}) {
  const [expanded, setExpanded] = useState<number | null>(null);
  const hasResults = Boolean(aio?.per_prompt?.length);

  return (
    <div className="mb-6">
      <div className="flex items-start justify-between gap-3 mb-3">
        <div>
          <h4 className="text-sm font-bold text-brand-dark">Google AI Overview Probe</h4>
          <p className="text-xs text-gray-500 mt-0.5">
            Uses Gemini with Google Search grounding to simulate AI Overview responses and extract cited sources.
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary shrink-0 text-xs py-1.5"
          disabled={running}
          onClick={onRun}
        >
          {running ? (
            <><Loader2 className="w-3.5 h-3.5 animate-spin" />Running…</>
          ) : hasResults ? "Re-run AIO probes" : "Run AIO probes"}
        </button>
      </div>

      {aio && !aio.available && aio.error && (
        <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-3">
          {aio.error}
        </p>
      )}

      {hasResults && aio && (
        <>
          {/* AIO top cited sites mini-table */}
          {aio.top_cited_sites?.length ? (
            <div className="mb-4 rounded-xl border border-blue-100 bg-blue-50/40 overflow-hidden">
              <div className="px-4 py-2.5 border-b border-blue-100 flex items-center gap-2">
                <svg width={14} height={14} viewBox="0 0 24 24" fill={AIO_COLOR} aria-hidden="true">
                  <path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/>
                </svg>
                <span className="text-xs font-semibold text-blue-800">
                  Top Google-cited sources ({aio.top_cited_sites.length})
                </span>
              </div>
              <ul className="divide-y divide-blue-100">
                {aio.top_cited_sites.slice(0, 8).map((site, i) => (
                  <li key={i} className="flex items-center gap-3 px-4 py-2">
                    <FaviconImg domain={site.domain} />
                    <a
                      href={site.example_url ?? `https://${site.domain}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-medium text-blue-700 hover:underline flex-1 truncate"
                    >
                      {site.title || site.domain}
                    </a>
                    <span className="text-[10px] font-bold text-blue-600 shrink-0">
                      ×{site.count}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* Per-prompt AIO accordion */}
          <div className="space-y-2">
            {aio.per_prompt.map((row) => (
              <div
                key={row.index}
                className="rounded-xl border border-blue-200 bg-white overflow-hidden"
              >
                <button
                  type="button"
                  className="w-full flex items-start gap-2 px-4 py-3 text-left hover:bg-blue-50/50 transition-colors"
                  onClick={() => setExpanded(expanded === row.index ? null : row.index)}
                >
                  <span className="text-[11px] font-bold text-blue-400 shrink-0 pt-0.5">Q{row.index}</span>
                  <span className="text-sm font-medium text-brand-dark flex-1 leading-snug">{row.prompt}</span>
                  <div className="flex items-center gap-2 shrink-0">
                    {row.error ? (
                      <span className="text-[10px] font-semibold text-red-500">Error</span>
                    ) : (
                      <span
                        className="text-[10px] font-semibold px-2 py-0.5 rounded-full"
                        style={{ background: `${AIO_COLOR}18`, color: AIO_COLOR }}
                      >
                        {row.citations.length} source{row.citations.length !== 1 ? "s" : ""}
                      </span>
                    )}
                    <ChevronDown
                      className="w-3.5 h-3.5 text-gray-400 transition-transform"
                      style={{ transform: expanded === row.index ? "rotate(180deg)" : "rotate(0deg)" }}
                    />
                  </div>
                </button>

                {expanded === row.index && (
                  <div className="px-4 pb-4 border-t border-blue-100">
                    {row.error ? (
                      <p className="text-xs text-red-500 mt-3">{row.error}</p>
                    ) : (
                      <>
                        {row.response && (
                          <p className="text-xs text-gray-600 leading-relaxed mt-3 mb-3 line-clamp-4">
                            {row.response}
                          </p>
                        )}
                        {row.citations.length > 0 ? (
                          <CitationsList citations={row.citations} platformColor={AIO_COLOR} />
                        ) : (
                          <p className="text-xs text-gray-400 italic mt-2">No sources cited.</p>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// ── Per-prompt card ───────────────────────────────────────────────────────────

const PLATFORM_CONFIG: { key: ProbePlatform; label: string; color: string; icon: React.ReactNode }[] = [
  { key: "gemini", label: "Gemini", color: PLATFORM_GEMINI, icon: <GeminiIcon size={14} /> },
  { key: "openai", label: "OpenAI", color: PLATFORM_OPENAI, icon: <OpenAIIcon size={14} /> },
  { key: "claude", label: "Claude", color: PLATFORM_CLAUDE, icon: <ClaudeIcon size={14} /> },
  { key: "google_aio", label: "Google AI Summaries", color: PLATFORM_GOOGLE_AIO, icon: <GoogleAioIcon size={14} /> },
];

// ── New: interactive vertical-expand table ────────────────────────────────────

function PromptTableRow({
  auditSlug,
  row,
  brandLabel,
  brandMatchTokens,
  productLabel,
  activePlatforms,
  isEven,
}: {
  auditSlug: string;
  row: LiveProbePerPrompt;
  brandLabel: string;
  brandMatchTokens: string[];
  productLabel?: string;
  activePlatforms: ProbePlatform[];
  isEven: boolean;
}) {
  const [open, setOpen] = useState(false);

  const platforms = PLATFORM_CONFIG.filter((p) => {
    if (!activePlatforms.includes(p.key)) return false;
    const resp = row[`${p.key}_response` as keyof LiveProbePerPrompt];
    const err = row[`error_${p.key}` as keyof LiveProbePerPrompt];
    return resp || err;
  });

  const colSpan = 2 + platforms.length + 1;

  return (
    <>
      <tr
        className={`cursor-pointer transition-colors ${
          open
            ? "bg-[#0d0d0d] text-white"
            : isEven
              ? "bg-white hover:bg-gray-50"
              : "bg-gray-50/50 hover:bg-gray-100/50"
        }`}
        onClick={() => setOpen((v) => !v)}
      >
        <td className={`px-4 py-3 text-xs font-bold tabular-nums ${open ? "text-gray-300" : "text-gray-400"}`}>
          Q{row.index}
        </td>
        <td className="px-4 py-3">
          {productLabel && (
            <span className={`inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded-full mb-1 ${open ? "bg-white/15 text-gray-200" : "bg-stone-200 text-stone-600"}`}>
              {productLabel}
            </span>
          )}
          <p className={`text-sm font-medium leading-snug ${open ? "text-white" : "text-[#0d0d0d]"}`}>
            {row.prompt}
          </p>
        </td>
        {platforms.map((p) => {
          const resp = String(row[`${p.key}_response` as keyof LiveProbePerPrompt] ?? "");
          const err = row[`error_${p.key}` as keyof LiveProbePerPrompt];
          const mentioned = !err && textMentionsBrand(resp, brandLabel, brandMatchTokens);
          return (
            <td key={p.key} className="px-3 py-3 text-center">
              <span
                className="inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-bold"
                style={
                  mentioned
                    ? { background: "#e8f8f5", color: SOV_GREEN }
                    : { background: open ? "rgba(255,255,255,.1)" : "#f5f5f5", color: open ? "rgba(255,255,255,.4)" : "#ccc" }
                }
              >
                {mentioned ? "✓" : "✗"}
              </span>
            </td>
          );
        })}
        <td className="px-3 py-3 text-center">
          <ChevronDown
            className={`w-4 h-4 mx-auto transition-transform duration-150 ${open ? "text-gray-300 rotate-180" : "text-gray-400"}`}
          />
        </td>
      </tr>

      {open && (
        <tr>
          <td colSpan={colSpan} className="px-0 py-0">
            <div className="border-t border-gray-700 bg-gray-50 divide-y divide-gray-100">
              {platforms.map((p) => {
                const err = row[`error_${p.key}` as keyof LiveProbePerPrompt] as string | undefined;
                const body = row[`${p.key}_response` as keyof LiveProbePerPrompt] as string | undefined;
                const brandPct = Number(row[`${p.key}_brand_mention_pct` as keyof LiveProbePerPrompt] ?? 0);
                const compPct = Number(row[`${p.key}_competitor_mention_pct` as keyof LiveProbePerPrompt] ?? 0);
                const citations = ((row as Record<string, unknown>)[`citations_${p.key}`] ?? []) as CitationItem[];
                const mentioned = !err && textMentionsBrand(String(body ?? ""), brandLabel, brandMatchTokens);

                return (
                  <div key={p.key} className="px-6 py-5">
                    <div className="flex items-center gap-2 mb-3">
                      <span
                        className="inline-flex h-7 w-7 items-center justify-center rounded-lg"
                        style={{ background: `${p.color}18` }}
                      >
                        {p.icon}
                      </span>
                      <span className="text-sm font-bold text-[#0d0d0d]">{p.label}</span>
                      <span
                        className="inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ml-1"
                        style={mentioned ? { background: "#e8f8f5", color: SOV_GREEN } : { background: "#f5f5f5", color: "#bbb" }}
                      >
                        {mentioned ? "Brand mentioned" : "Not mentioned"}
                      </span>
                      <span className="text-xs text-gray-400 ml-auto">
                        Brand {brandPct.toFixed(0)}% · Comp {compPct.toFixed(0)}%
                      </span>
                    </div>
                    {err ? (
                      <div className="alert-error text-sm">{err}</div>
                    ) : (
                      <div className="space-y-4">
                        <div className="text-sm text-gray-700 leading-relaxed bg-white rounded-lg border border-gray-100 p-4 max-h-64 overflow-y-auto">
                          <ReplyHighlight auditSlug={auditSlug} text={String(body ?? "")} enabled />
                        </div>
                        {citations.length > 0 && (
                          <div>
                            <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 mb-2">
                              Citations ({citations.length})
                            </p>
                            <CitationsList citations={citations} platformColor={p.color} />
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function PromptTable({
  auditSlug,
  ctx,
  live,
  brandLabel,
  brandMatchTokens,
  activePlatforms,
}: {
  auditSlug: string;
  ctx: PromptPerformanceContext;
  live: LiveProbeResult;
  brandLabel: string;
  brandMatchTokens: string[];
  activePlatforms: ProbePlatform[];
}) {
  const [showAll, setShowAll] = useState(false);
  const DEFAULT_VISIBLE = 8;

  const allRows = useMemo(() => {
    const perPrompt = (live.per_prompt ?? []).filter(Boolean) as LiveProbePerPrompt[];
    const mappingRows = ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows;
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
    while (idx < perPrompt.length) {
      result.push({ row: perPrompt[idx], productLabel: "" });
      idx++;
    }
    return result;
  }, [live, ctx]);

  const platforms = PLATFORM_CONFIG.filter((p) => activePlatforms.includes(p.key));
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

      <div className="rounded-xl border border-gray-200 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400 w-10">#</th>
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">Prompt</th>
              {platforms.map((p) => (
                <th key={p.key} className="px-3 py-2.5 text-center text-[10px] font-semibold uppercase tracking-wide" style={{ color: p.color }}>
                  {p.label}
                </th>
              ))}
              <th className="px-3 py-2.5 w-8" />
            </tr>
          </thead>
          <tbody>
            {visible.map(({ row, productLabel }, i) => (
              <PromptTableRow
                key={`${row.index}-${i}`}
                auditSlug={auditSlug}
                row={row}
                brandLabel={brandLabel}
                brandMatchTokens={brandMatchTokens}
                productLabel={productLabel}
                activePlatforms={activePlatforms}
                isEven={i % 2 === 0}
              />
            ))}
          </tbody>
        </table>
      </div>

      {!showAll && hidden > 0 && (
        <button
          type="button"
          className="w-full mt-2 py-2.5 rounded-xl border border-dashed border-gray-300 text-sm font-semibold text-gray-500 hover:border-gray-400 hover:text-gray-700 transition-colors"
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
  const [aioRunning, setAioRunning] = useState(false);

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

  const runAio = async () => {
    setAioRunning(true);
    try {
      const res = await runAioProbes(slug);
      setCtx((prev) => prev ? { ...prev, aio_probe: res.aio_probe } : prev);
    } catch (e) {
      setError(e instanceof Error ? e.message : "AIO probe run failed");
    } finally {
      setAioRunning(false);
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
  const brandMatchTokens =
    ctx.highlight?.brand_match_tokens?.length
      ? ctx.highlight.brand_match_tokens
      : live?.brand_match_tokens ?? [];
  const mcc = ctx.primary_market?.country;
  const mid = ctx.primary_market?.country_id;

  // SOV numbers from aggregate
  const perPrompt = (live?.per_prompt ?? []) as LiveProbePerPrompt[];
  const hasClaude = isProbePlatformActive("claude", live);
  const hasGoogleAio = isProbePlatformActive("google_aio", live);
  const numPlatforms = Math.max(activePlatforms.length, 1);

  const gBp = live?.aggregate?.gemini?.brand_share_pct ?? 0;
  const gCp = avgPerCompetitorSovPct(perPrompt, "gemini");
  const oBp = live?.aggregate?.openai?.brand_share_pct ?? 0;
  const oCp = avgPerCompetitorSovPct(perPrompt, "openai");
  const cBp = live?.aggregate?.claude?.brand_share_pct ?? 0;
  const cCp = avgPerCompetitorSovPct(perPrompt, "claude");
  const aiBp = live?.aggregate?.google_aio?.brand_share_pct ?? 0;
  const aiCp = avgPerCompetitorSovPct(perPrompt, "google_aio");

  const platformBrandPcts: Record<ProbePlatform, number> = {
    gemini: gBp,
    openai: oBp,
    claude: cBp,
    google_aio: aiBp,
  };
  const platformCompPcts: Record<ProbePlatform, number> = {
    gemini: gCp,
    openai: oCp,
    claude: cCp,
    google_aio: aiCp,
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
            <div className={`grid gap-4 mt-4 ${activePlatforms.length >= 4 ? "md:grid-cols-4" : activePlatforms.length >= 3 ? "md:grid-cols-3" : activePlatforms.length === 2 ? "md:grid-cols-2" : "md:grid-cols-1"}`}>
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
              {hasGoogleAio && (
                <PlatformSovCard
                  title="Google AI Summaries"
                  accentColor={PLATFORM_GOOGLE_AIO}
                  icon={<GoogleAioIcon size={18} />}
                  brandLabel={brandLabel}
                  brandPct={aiBp}
                  compPct={aiCp}
                />
              )}
            </div>
          </div>

          {/* Most cited sites */}
          {(live.top_cited_urls?.length || live.top_cited_sites?.length) ? (
            <CitationsSummaryTable
              sites={live.top_cited_sites ?? []}
              urls={live.top_cited_urls}
              activePlatforms={activePlatforms}
            />
          ) : null}

          {/* Google AI Overview probe */}
          <GoogleAioSection
            aio={ctx.aio_probe}
            onRun={runAio}
            running={aioRunning}
          />

          {/* Per-prompt responses */}
          <PromptTable
            auditSlug={slug}
            ctx={ctx}
            live={live}
            brandLabel={brandLabel}
            brandMatchTokens={brandMatchTokens}
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
