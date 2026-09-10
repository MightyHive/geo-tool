import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronRight, ExternalLink, Loader2, X, Filter, Plus, Tags } from "lucide-react";
import { PageLoading } from "./PageLoading";
import {
  highlightPromptReply,
  runPromptPerformanceProbes,
  addPromptToAudit,
  addTagsToPrompts,
  updatePromptLocales,
  fetchPromptPerformanceDetail,
  peekPromptPerformanceDetail,
  fetchPromptSentiment,
} from "../api/client";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import {
  derivePlatformCallsPerMarket,
  formatProbeRunEta,
} from "../lib/probeRunEta";
import type {
  CitationItem,
  LiveProbePerPrompt,
  LiveProbeResult,
  MentionScores,
  PromptPerformanceContext,
  PromptSentimentAnalysis,
  SingleRunData,
} from "../types";
import {
  CUSTOM_PROMPTS_LABEL,
  filterSentimentCategories as _filterSentimentCategories,
} from "../lib/customPrompts";
import { normalizePromptLocales, type PromptLocale } from "../lib/promptLocales";
import { PromptLocaleEditor } from "./PromptLocaleEditor";
import { PromptLocaleNestedFilter, liveProbeForLocale } from "./PromptLocaleFilter";
import { OVERALL_LOCALE_KEY, configuredPromptLocales, localesNeedingProbe } from "../lib/localeProbeView";
import { preferredInitialLocaleKey } from "../lib/defaultLocaleView";
import { VirtualScrollTable } from "./VirtualScrollTable";
import {
  averageVisibilityAcrossPrompts,
  buildTopicMarketPromptTree,
  marketLanguageLabel,
  topicMarketKey,
  type TopicMarketNode,
} from "../lib/promptCategoryGrouping";
import { computePromptAvgPosition, extractBrandPosition, formatPosition } from "../lib/brandPosition";
import { visibilityByCategory as _visibilityByCategory } from "../lib/categoryVisibility";
import {
  textMentionsBrand,
} from "../lib/brandMatch";
import { isVendorBrand } from "../lib/vendorDomains";
import {
  isPromptCitationSource,
  isPromptVendorCitation,
} from "../lib/citationSource";
import {
  buildCanonicalNameMap,
  isDomainString,
  domainToLabel,
  isPlausibleCompetitorName,
} from "../lib/brandNormalize";
import {
  activeProbePlatforms,
  probePlatformsLabel,
  type ProbePlatform,
} from "../lib/probePlatforms";
import {
  platformsForSurface,
  type SurfaceFilter,
} from "../lib/visibilityMetrics";
import { ReportFilterSelect } from "./ReportFilterSelect";
import { Card, CardDescription, CardTitle } from "./ui/Card";
import { CompetitorFavicon, PlatformLogo, PLATFORM_META } from "./PlatformLogo";
import {
  computePlatformResponseSentiment,
  type SentimentLabel,
} from "../lib/sentimentCalc";
import {
  buildGeminiPromptSentimentMap,
  buildGeminiPromptSentimentSummaryMap,
  dominantGeminiSentiment,
  geminiSentimentForPrompt,
  type GeminiSentimentLabel,
} from "../lib/geminiPromptSentiment";
import { ViewportOverlay } from "./ViewportOverlay";

const SOV_GREEN = "#00b894";
const PLATFORM_GEMINI = PLATFORM_META.gemini.color;
const PLATFORM_OPENAI = PLATFORM_META.openai.color;
const PLATFORM_CLAUDE = PLATFORM_META.claude.color;
const PLATFORM_GOOGLE_AIO = PLATFORM_META.google_aio.color;

// ── Small helpers ─────────────────────────────────────────────────────────────

/** Per-prompt visibility % (list_metrics preferred; else brand mention rate across runs). */
function promptVisibilityPct(
  row: LiveProbePerPrompt,
  brandLabel: string,
  brandMatchTokens: string[],
  activePlatforms: ProbePlatform[],
): number {
  if (row.list_metrics?.visibility_pct != null) {
    return Number(row.list_metrics.visibility_pct);
  }
  const activeCfg = PLATFORM_CONFIG.filter((p) => activePlatforms.includes(p.key));
  const responseRuns = activeCfg.flatMap((platform) => completedPlatformRuns(row, platform.key));
  if (!responseRuns.length) return 0;
  const mentionCount = responseRuns.filter((run) =>
    Number(run.mention_scores?.brand_signal ?? 0) > 0
    || (run.response ? textMentionsBrand(run.response, brandLabel, brandMatchTokens) : false),
  ).length;
  return (mentionCount / responseRuns.length) * 100;
}

function completedPlatformRuns(row: LiveProbePerPrompt, platform: ProbePlatform): SingleRunData[] {
  const runs = (row.runs as Record<string, SingleRunData[]> | undefined)?.[platform] ?? [];
  const completed = runs.filter((run) => {
    if (run.error) return false;
    const hasBody = Boolean(String(run.response || "").trim());
    return hasBody || Boolean(run.has_response);
  });
  if (completed.length) return completed;
  const response = String(row[`${platform}_response` as keyof LiveProbePerPrompt] ?? "");
  const error = String(row[`error_${platform}` as keyof LiveProbePerPrompt] ?? "");
  if (response && !error) {
    return [{
      run_index: 1,
      response,
      citations: ((row as Record<string, unknown>)[`citations_${platform}`] ?? []) as CitationItem[],
      mention_scores: ((row as Record<string, unknown>)[`mention_scores_${platform}`] ?? {}) as MentionScores,
    }];
  }
  // Slim list rows: reply bodies omitted but scores retained.
  const hasFlag = Boolean((row as Record<string, unknown>)[`has_response_${platform}`]);
  const listed = (row.list_metrics?.platforms_responded ?? []).includes(platform);
  const scores = ((row as Record<string, unknown>)[`mention_scores_${platform}`] ?? {}) as MentionScores;
  if ((hasFlag || listed) && !error) {
    return [{
      run_index: 1,
      response: "",
      citations: ((row as Record<string, unknown>)[`citations_${platform}`] ?? []) as CitationItem[],
      mention_scores: scores,
    }];
  }
  return [];
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
    <ul className="space-y-2.5">
      {citations.map((c, i) => (
        <li key={i} className="flex items-start gap-2">
          <div className="mt-0.5 shrink-0">
            <FaviconImg domain={c.domain} />
          </div>
          <div className="min-w-0 flex-1">
            <a
              href={c.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs font-semibold hover:underline leading-snug"
              style={{ color: platformColor }}
            >
              {c.title || c.domain}
            </a>
            {c.title && (
              <p className="text-[11px] text-gray-400 truncate">{c.domain}</p>
            )}
            <a
              href={c.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] text-gray-400 hover:text-gray-600 truncate block max-w-full"
              title={c.url}
            >
              {c.url.length > 80 ? c.url.slice(0, 80) + "…" : c.url}
            </a>
            {c.views != null && (
              <p className="text-[10px] text-gray-400 mt-0.5">
                {c.views >= 1_000_000
                  ? `${(c.views / 1_000_000).toFixed(1)}M views`
                  : c.views >= 1_000
                  ? `${(c.views / 1_000).toFixed(0)}K views`
                  : `${c.views} views`}
              </p>
            )}
          </div>
          <a
            href={c.url}
            target="_blank"
            rel="noopener noreferrer"
            className="shrink-0 mt-0.5"
          >
            <ExternalLink className="w-3 h-3 text-gray-300 hover:text-gray-500" />
          </a>
        </li>
      ))}
    </ul>
  );
}

// ── Per-prompt card ───────────────────────────────────────────────────────────

const PLATFORM_CONFIG: { key: ProbePlatform; label: string; color: string; icon: React.ReactNode }[] = [
  { key: "gemini", label: "Gemini", color: PLATFORM_GEMINI, icon: <PlatformLogo platform="gemini" size={14} /> },
  { key: "openai", label: "OpenAI", color: PLATFORM_OPENAI, icon: <PlatformLogo platform="openai" size={14} /> },
  { key: "google_aio", label: "Google AI Summaries", color: PLATFORM_GOOGLE_AIO, icon: <PlatformLogo platform="google_aio" size={14} /> },
  { key: "claude", label: "Claude", color: PLATFORM_CLAUDE, icon: <PlatformLogo platform="claude" size={14} /> },
];

// ── Shared tooltip ────────────────────────────────────────────────────────────

function ColTooltip({ text }: { text: string }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  return (
    <span
      ref={ref}
      className="ml-1 inline-flex items-center cursor-help"
      onMouseEnter={() => {
        if (ref.current) {
          const r = ref.current.getBoundingClientRect();
          setPos({ x: r.left + r.width / 2, y: r.bottom + 6 });
        }
      }}
      onMouseLeave={() => setPos(null)}
    >
      <span className="text-gray-300 text-[10px] font-bold">ⓘ</span>
      {pos && createPortal(
        <span
          className="fixed w-52 rounded-lg bg-gray-900 text-white text-xs px-2.5 py-2 leading-snug z-[9999] shadow-lg pointer-events-none"
          style={{ left: pos.x, top: pos.y, transform: "translateX(-50%)" }}
        >
          {text}
        </span>,
        document.body,
      )}
    </span>
  );
}

// ── Sentiment badge (Gemini qualitative: Positive / Mixed / Neutral / Negative)

function SentimentBadge({
  sentiment,
}: {
  sentiment: SentimentLabel | GeminiSentimentLabel | string | null;
}) {
  if (!sentiment) {
    return <span className="text-xs text-gray-300 font-bold">—</span>;
  }
  const s = String(sentiment).trim().toLowerCase();
  if (s === "positive") {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-emerald-100 text-emerald-600 text-sm font-bold" title="Positive">
        +
      </span>
    );
  }
  if (s === "negative") {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-red-100 text-red-500 text-sm font-bold" title="Negative">
        −
      </span>
    );
  }
  if (s === "mixed") {
    return (
      <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-orange-100 text-orange-600 text-sm font-bold" title="Mixed">
        ±
      </span>
    );
  }
  return (
    <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-gray-100 text-gray-400 text-sm font-bold" title="Neutral">
      ~
    </span>
  );
}



// ── Prompt detail overlay ─────────────────────────────────────────────────────

export function PromptDetailOverlay({
  auditSlug,
  row: initialRow,
  productLabel,
  brandLabel,
  brandMatchTokens,
  activePlatforms,
  compWebsiteMap,
  citationCtx,
  localeKey,
  geminiSentiment = null,
  geminiSummary = null,
  onClose,
}: {
  auditSlug: string;
  row: LiveProbePerPrompt;
  productLabel: string;
  brandLabel: string;
  brandMatchTokens: string[];
  activePlatforms: ProbePlatform[];
  compWebsiteMap: Map<string, string>;
  /** Shared brand/competitor context for Citations vs Vendors split. */
  citationCtx?: PromptPerformanceContext | null;
  localeKey?: string;
  /** Gemini qualitative label for this prompt (not keyword heuristics). */
  geminiSentiment?: GeminiSentimentLabel | null;
  geminiSummary?: string | null;
  onClose: () => void;
}) {
  const [row, setRow] = useState(initialRow);
  const [detailLoading, setDetailLoading] = useState(Boolean(initialRow.replies_omitted));

  useEffect(() => {
    setRow(initialRow);
    if (!initialRow.replies_omitted) {
      setDetailLoading(false);
      return;
    }
    const promptId = String(initialRow.prompt_id || "").trim();
    if (!promptId) {
      setDetailLoading(false);
      return;
    }
    const cached = peekPromptPerformanceDetail(auditSlug, promptId, localeKey);
    if (cached?.prompt) {
      setRow(cached.prompt);
      setDetailLoading(false);
      return;
    }
    let cancelled = false;
    const ac = new AbortController();
    // Overlay shell stays open immediately; fill replies when the detail fetch lands.
    setDetailLoading(true);
    void fetchPromptPerformanceDetail(auditSlug, promptId, localeKey, { signal: ac.signal })
      .then((res) => {
        if (!cancelled && res.prompt) setRow(res.prompt);
      })
      .catch(() => undefined)
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });
    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [auditSlug, initialRow, localeKey]);

  const platforms = PLATFORM_CONFIG.filter((p) => {
    if (!activePlatforms.includes(p.key)) return false;
    const resp = row[`${p.key}_response` as keyof LiveProbePerPrompt];
    const err = row[`error_${p.key}` as keyof LiveProbePerPrompt];
    const hasFlag = Boolean((row as Record<string, unknown>)[`has_response_${p.key}`]);
    return resp || err || hasFlag;
  });

  const [activeTab, setActiveTab] = useState<ProbePlatform>(
    platforms[0]?.key ?? ("gemini" as ProbePlatform),
  );

  // Run toggle: detect how many runs exist for the active platform
  const platformRuns = (row.runs as Record<string, SingleRunData[]> | undefined)?.[activeTab] ?? [];
  const numRuns = platformRuns.length;
  const [activeRunIdx, setActiveRunIdx] = useState(0);
  // Reset run index when platform changes
  useEffect(() => { setActiveRunIdx(0); }, [activeTab]);

  // ── Summary scorecards (across all platforms for this one prompt) ──────────
  const responseRuns = platforms.flatMap((platform) =>
    completedPlatformRuns(row, platform.key).map((run) => ({ platform: platform.key, run })),
  );
  const mentioningRuns = responseRuns.filter(({ run }) =>
    Number(run.mention_scores?.brand_signal ?? 0) > 0
    || textMentionsBrand(run.response ?? "", brandLabel, brandMatchTokens),
  );
  const totalPlatforms = responseRuns.length;
  const brandVisibilityPct = totalPlatforms > 0 ? (mentioningRuns.length / totalPlatforms) * 100 : 0;

  // Avg competitor visibility for this prompt (deduplicated via canonical names)
  const compVisRaw = new Map<string, number>();
  for (const p of activePlatforms) {
    for (const run of completedPlatformRuns(row, p)) {
      for (const [name, hits] of Object.entries(run.mention_scores?.competitor_detail ?? {})) {
        if (Number(hits) > 0 && !isVendorBrand(name) && isPlausibleCompetitorName(name)) {
          compVisRaw.set(name, (compVisRaw.get(name) ?? 0) + 1);
        }
      }
    }
  }
  const rawCompVisNames = Array.from(compVisRaw.keys());
  const canonVisMap = buildCanonicalNameMap(rawCompVisNames);
  const compVisMap = new Map<string, number>();
  for (const raw of rawCompVisNames) {
    const canon = canonVisMap.get(raw) ?? raw;
    compVisMap.set(canon, Math.max(compVisMap.get(canon) ?? 0, compVisRaw.get(raw) ?? 0));
  }
  const compNames = Array.from(compVisMap.keys());
  const avgCompVisibilityPct =
    compNames.length > 0
      ? compNames.reduce((sum, n) => sum + ((compVisMap.get(n) ?? 0) / totalPlatforms) * 100, 0) /
        compNames.length
      : 0;
  let compVisTotal = 0; void compVisTotal;

  // SOV for this prompt: brand hits / (brand hits + all comp hits)
  let brandHits = 0;
  let compHitsTotal = 0;
  for (const p of activePlatforms) {
    for (const run of completedPlatformRuns(row, p)) {
      const storedBrandSignal = Number(run.mention_scores?.brand_signal ?? 0);
      const visible = storedBrandSignal > 0
        || textMentionsBrand(run.response ?? "", brandLabel, brandMatchTokens);
      brandHits += storedBrandSignal > 0 ? storedBrandSignal : visible ? 1 : 0;
      compHitsTotal += Object.values(run.mention_scores?.competitor_detail ?? {})
        .reduce((sum, hits) => sum + Number(hits ?? 0), 0);
    }
  }
  const totalHits = brandHits + compHitsTotal;
  const brandSovPct = totalHits > 0 ? (brandHits / totalHits) * 100 : 0;

  // Sentiment for this prompt — Gemini qualitative (same method as Summary badge)
  const promptSentiment = geminiSentiment;
  const sentimentLabel = promptSentiment ?? "—";
  const sentimentColor =
    promptSentiment === "Positive"
      ? "#00b894"
      : promptSentiment === "Negative"
        ? "#d63031"
        : promptSentiment === "Mixed"
          ? "#e17055"
          : "#636e72";

  const activeP = platforms.find((p) => p.key === activeTab) ?? platforms[0];

  // Avg position for this prompt across all platforms
  const promptAvgPosition = computePromptAvgPosition(
    row as Record<string, unknown>,
    brandMatchTokens,
    activePlatforms,
  );

  return (
    <ViewportOverlay onClose={onClose} backdropOpacity={0.6}>
      <div className="bg-white rounded-2xl w-full max-w-3xl max-h-[min(90vh,920px)] flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="sticky top-0 z-10 bg-white border-b border-gray-100 px-6 py-4 flex items-start gap-4">
          <div className="flex-1 min-w-0">
            {productLabel && (
              <span className="inline-block text-[10px] font-semibold px-2 py-0.5 rounded-full bg-stone-100 text-stone-500 mb-1.5">
                {productLabel}
              </span>
            )}
            <p className="text-sm font-bold text-[#0d0d0d] leading-snug">{row.prompt}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="overflow-y-auto flex-1">
          {detailLoading && (
            <div className="px-6 py-3 text-xs text-gray-500 border-b border-gray-100 bg-amber-50/60">
              Loading full AI replies…
            </div>
          )}
          {/* Summary scorecards use slim metrics immediately; reply body fills below. */}
          <div className="grid grid-cols-5 divide-x divide-gray-100 border-b border-gray-100 bg-gray-50/60">
            <div className="px-4 py-3.5 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">Brand Visibility</p>
              <p className={`text-xl font-black tabular-nums ${brandVisibilityPct >= 75 ? "text-emerald-600" : brandVisibilityPct >= 50 ? "text-amber-500" : "text-gray-400"}`}>
                {Math.round(brandVisibilityPct)}%
              </p>
            </div>
            <div className="px-4 py-3.5 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">Avg Comp. Visibility</p>
              <p className="text-xl font-black tabular-nums text-blue-500">
                {Math.round(avgCompVisibilityPct)}%
              </p>
            </div>
            <div className="px-4 py-3.5 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">Brand SOV</p>
              <p className="text-xl font-black tabular-nums" style={{ color: SOV_GREEN }}>
                {Math.round(brandSovPct)}%
              </p>
            </div>
            <div className="px-4 py-3.5 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">Avg. Position</p>
              <p className={`text-xl font-black tabular-nums ${promptAvgPosition != null && promptAvgPosition <= 3 ? "text-emerald-600" : promptAvgPosition != null && promptAvgPosition <= 5 ? "text-amber-500" : "text-gray-400"}`}>
                {formatPosition(promptAvgPosition)}
              </p>
            </div>
            <div className="px-4 py-3.5 text-center">
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-1">Sentiment</p>
              <p
                className="text-base font-black"
                style={{ color: sentimentColor }}
                title={geminiSummary || "Gemini qualitative sentiment"}
              >
                {sentimentLabel}
              </p>
            </div>
          </div>

          {/* Platform tabs */}
          <div className="flex border-b border-gray-100 bg-white">
            {platforms.map((p) => {
              const isActive = p.key === activeTab;
              return (
                <button
                  key={p.key}
                  type="button"
                  onClick={() => setActiveTab(p.key)}
                  className={`flex items-center gap-2 px-5 py-3 text-sm font-semibold transition-colors border-b-2 ${
                    isActive
                      ? "border-current text-[#0d0d0d]"
                      : "border-transparent text-gray-400 hover:text-gray-600"
                  }`}
                  style={isActive ? { borderColor: p.color } : {}}
                >
                  <PlatformLogo platform={p.key} size={16} />
                  {p.label}
                  {/* Consistency badge: show fraction of runs where brand appeared */}
                  {(() => {
                    const runs = (row.runs as Record<string, SingleRunData[]> | undefined)?.[p.key] ?? [];
                    if (runs.length < 2) return null;
                    const brandHits = runs.filter((r) => textMentionsBrand(r.response ?? "", brandLabel, brandMatchTokens)).length;
                    const pct = Math.round((brandHits / runs.length) * 100);
                    const color = pct === 100 ? "#00b894" : pct >= 50 ? "#fdcb6e" : "#d63031";
                    return (
                      <span className="ml-1 text-[10px] font-bold px-1.5 py-0.5 rounded-full" style={{ background: color + "22", color }}>
                        {brandHits}/{runs.length}
                      </span>
                    );
                  })()}
                </button>
              );
            })}
            {/* Run selector — shown when there are multiple runs */}
            {numRuns > 1 && (
              <div className="ml-auto flex items-center gap-1 px-4 py-2">
                <span className="text-[10px] font-semibold text-gray-400 mr-1">RUN</span>
                {Array.from({ length: numRuns }, (_, i) => {
                  const runData = platformRuns[i];
                  const hasBrand = textMentionsBrand(runData?.response ?? "", brandLabel, brandMatchTokens);
                  return (
                    <button
                      key={i}
                      type="button"
                      onClick={() => setActiveRunIdx(i)}
                      title={`Run ${i + 1} — brand ${hasBrand ? "mentioned" : "not mentioned"}`}
                      className={`relative w-7 h-7 rounded-full text-xs font-bold transition-colors border ${
                        activeRunIdx === i
                          ? "bg-[#0d0d0d] text-white border-[#0d0d0d]"
                          : "bg-white text-gray-500 border-gray-200 hover:border-gray-400"
                      }`}
                    >
                      {i + 1}
                      {hasBrand && (
                        <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-emerald-500 border border-white" />
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Active platform content */}
          {activeP && (() => {
            // If we have per-run data and user selected a specific run, use that; else fall back to aggregated row data
            const activeRunData = platformRuns[activeRunIdx];
            const err = activeRunData?.error || (row[`error_${activeP.key}` as keyof LiveProbePerPrompt] as string | undefined);
            const body = (activeRunData?.response || row[`${activeP.key}_response` as keyof LiveProbePerPrompt]) as string | undefined;
            const citations = ((activeRunData?.citations ?? (row as Record<string, unknown>)[`citations_${activeP.key}`]) ?? []) as CitationItem[];
            if (detailLoading && !String(body || "").trim() && !err) {
              return (
                <div className="px-6 py-10 text-sm text-gray-400 animate-pulse">
                  Fetching platform replies…
                </div>
              );
            }
            const mentioned = !err && textMentionsBrand(String(body ?? ""), brandLabel, brandMatchTokens);
            const platformPosition = body ? extractBrandPosition(String(body), brandMatchTokens) : null;
            const sentiment = computePlatformResponseSentiment(
              row as Record<string, unknown>,
              brandMatchTokens,
              activeP.key,
            );

            // Competitors for this specific platform — deduplicated, vendor-filtered
            const platScores = (row as Record<string, MentionScores | undefined>)[`mention_scores_${activeP.key}`];
            const rawPlatformComps = new Set<string>();
            for (const [name, hits] of Object.entries(platScores?.competitor_detail ?? {})) {
              if (Number(hits) > 0 && !isVendorBrand(name) && isPlausibleCompetitorName(name)) {
                rawPlatformComps.add(name);
              }
            }
            const platCanonMap = buildCanonicalNameMap(rawPlatformComps);
            const seenPlatCanonicals = new Set<string>();
            const platformCompetitors: { name: string; displayName: string; website?: string }[] = [];
            for (const raw of rawPlatformComps) {
              const canon = platCanonMap.get(raw) ?? raw;
              if (seenPlatCanonicals.has(canon)) continue;
              seenPlatCanonicals.add(canon);
              const displayName = isDomainString(canon) ? domainToLabel(canon) : canon;
              const website = compWebsiteMap.get(raw.toLowerCase()) ?? compWebsiteMap.get(canon.toLowerCase())
                ?? (isDomainString(raw) ? `https://${raw}` : undefined);
              platformCompetitors.push({ name: canon, displayName, website });
            }

            // Same Citations / Vendors split as the Citations page (shared helper)
            const vendorCitations = citations.filter((c) => isPromptVendorCitation(c));
            const sourceCitations = citations.filter((c) => isPromptCitationSource(c, citationCtx));

            return (
              <div>
                {/* Mini status bar */}
                <div className="flex items-center gap-2.5 px-5 py-2 bg-gray-50 border-b border-gray-100">
                  <SentimentBadge sentiment={sentiment} />
                  <span
                    className="inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full"
                    style={mentioned ? { background: "#e8f8f5", color: SOV_GREEN } : { background: "#f5f5f5", color: "#bbb" }}
                  >
                    {mentioned ? "✓ Brand mentioned" : "Not mentioned"}
                  </span>
                  {platformPosition != null && (
                    <span className="inline-flex items-center text-xs font-semibold px-2 py-0.5 rounded-full bg-blue-50 text-blue-600">
                      Position {platformPosition}
                    </span>
                  )}
                </div>
                {err ? (
                  <div className="px-5 py-5 text-sm text-red-500 bg-red-50">{err}</div>
                ) : (
                  <div>
                    {/* Response text */}
                    <div className="px-5 py-5 text-sm text-gray-700 leading-relaxed max-h-72 overflow-y-auto border-b border-gray-50">
                      <ReplyHighlight auditSlug={auditSlug} text={String(body ?? "")} enabled />
                    </div>

                    {/* Competitors section — competitor brands mentioned */}
                    {platformCompetitors.length > 0 && (
                      <div className="px-5 py-4 border-b border-gray-100">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2.5">
                          Competitors ({platformCompetitors.length})
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {platformCompetitors.map((b) => (
                            <div
                              key={b.name}
                              className="flex items-center gap-1.5 text-xs font-medium bg-gray-50 border border-gray-100 rounded-full px-2.5 py-1"
                            >
                              <CompetitorFavicon name={b.displayName} website={b.website} size={14} />
                              {b.website ? (
                                <a
                                  href={b.website}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="rounded-sm text-[#0d0d0d] hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500"
                                >
                                  {b.displayName}
                                </a>
                              ) : (
                                <span className="text-[#0d0d0d]">{b.displayName}</span>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Vendors section — where-to-buy / retailer links */}
                    {vendorCitations.length > 0 && (
                      <div className="px-5 py-4 border-b border-gray-100">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2.5">
                          Vendors ({vendorCitations.length})
                          <span className="ml-1.5 normal-case font-normal text-gray-400">— where to buy</span>
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {vendorCitations.map((c) => (
                            <a
                              key={c.url}
                              href={c.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="flex items-center gap-1.5 text-xs font-medium bg-amber-50 border border-amber-100 rounded-full px-2.5 py-1 hover:bg-amber-100 transition-colors"
                            >
                              <FaviconImg domain={c.domain} />
                              <span className="text-amber-800">{c.domain.replace(/^www\./, "")}</span>
                            </a>
                          ))}
                        </div>
                      </div>
                    )}

                    {/* Citations section — information sources only */}
                    {sourceCitations.length > 0 && (
                      <div className="px-5 py-4">
                        <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2.5">
                          Citations ({sourceCitations.length})
                          <span className="ml-1.5 normal-case font-normal text-gray-400">— information sources</span>
                        </p>
                        <CitationsList citations={sourceCitations} platformColor={activeP.color} />
                      </div>
                    )}

                    {sourceCitations.length === 0 && vendorCitations.length === 0 && citations.length === 0 && (
                      <div className="px-5 py-4">
                        <p className="text-xs text-gray-400 italic">No citations returned for this response.</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })()}
        </div>
      </div>
    </ViewportOverlay>
  );
}

// ── New prompts table (8-column with filters + overlay) ───────────────────────

export function PromptTableRow({
  row,
  productLabel: _productLabel,
  brandLabel,
  brandMatchTokens,
  activePlatforms,
  compWebsiteMap,
  citationCtx,
  tags,
  selectionMode = false,
  selected = false,
  onToggleSelected,
  onClick,
  nestLevel = 0,
  geminiSentiment = null,
}: {
  row: LiveProbePerPrompt;
  productLabel: string;
  brandLabel: string;
  brandMatchTokens: string[];
  activePlatforms: ProbePlatform[];
  compWebsiteMap: Map<string, string>;
  citationCtx?: PromptPerformanceContext | null;
  tags: string[];
  selectionMode?: boolean;
  selected?: boolean;
  onToggleSelected?: () => void;
  onClick: () => void;
  /** Indent under Topic / Market rows in Overall hierarchy. */
  nestLevel?: number;
  geminiSentiment?: GeminiSentimentLabel | null;
}) {
  const activeCfg = PLATFORM_CONFIG.filter((p) => activePlatforms.includes(p.key));

  // Platforms that have a non-error response for this prompt
  const platformsWithResponse = activeCfg.filter((p) => {
    const resp = row[`${p.key}_response` as keyof LiveProbePerPrompt];
    const err = row[`error_${p.key}` as keyof LiveProbePerPrompt];
    const hasFlag = Boolean((row as Record<string, unknown>)[`has_response_${p.key}`]);
    const listed = (row.list_metrics?.platforms_responded ?? []).includes(p.key);
    return (resp || hasFlag || listed) && !err;
  });

  // Visibility: % of responding platforms that mention the brand
  const responseRuns = activeCfg.flatMap((platform) => completedPlatformRuns(row, platform.key));
  const mentionCount = responseRuns.filter((run) =>
    Number(run.mention_scores?.brand_signal ?? 0) > 0
    || (run.response ? textMentionsBrand(run.response, brandLabel, brandMatchTokens) : false),
  ).length;
  const visibilityPct = row.list_metrics?.visibility_pct != null
    ? Number(row.list_metrics.visibility_pct)
    : responseRuns.length > 0 ? (mentionCount / responseRuns.length) * 100 : 0;
  void (mentionCount > 0); // brandMentioned — available in overlay, not used in table row directly
  void platformsWithResponse;

  // Gemini qualitative sentiment (same method as Summary / AI Sentiment)
  const sentiment = geminiSentiment;

  // Avg position in ranked lists across platforms
  const avgPosition = row.list_metrics?.avg_position != null
    ? Number(row.list_metrics.avg_position)
    : computePromptAvgPosition(row as Record<string, unknown>, brandMatchTokens, activePlatforms);

  // Competitors: deduplicated competitor brands only (own brand shown in Visibility column)
  const rawCompMentioned = new Set<string>();
  for (const p of activePlatforms) {
    const scores = (row as Record<string, MentionScores | undefined>)[`mention_scores_${p}`];
    for (const [cName, hits] of Object.entries(scores?.competitor_detail ?? {})) {
      if (Number(hits) > 0 && !isVendorBrand(cName) && isPlausibleCompetitorName(cName)) {
        rawCompMentioned.add(cName);
      }
    }
  }
  // Deduplicate: "The Ordinary" and "theordinary.com" → keep "The Ordinary"
  const canonMap = buildCanonicalNameMap(rawCompMentioned);
  const seenCanonicals = new Set<string>();
  const mentionedBrands: { name: string; displayName: string; website?: string }[] = [];
  for (const raw of rawCompMentioned) {
    const canon = canonMap.get(raw) ?? raw;
    if (seenCanonicals.has(canon)) continue;
    seenCanonicals.add(canon);
    const displayName = isDomainString(canon) ? domainToLabel(canon) : canon;
    const website = compWebsiteMap.get(raw.toLowerCase()) ?? compWebsiteMap.get(canon.toLowerCase())
      ?? (isDomainString(raw) ? `https://${raw}` : undefined);
    mentionedBrands.push({ name: canon, displayName, website });
  }

  // Citations: same predicate as Citations page (shared helper)
  const citationDomains = new Map<string, string>();
  for (const p of activePlatforms) {
    const cits = ((row as Record<string, unknown>)[`citations_${p}`] ?? []) as CitationItem[];
    for (const c of cits) {
      if (!isPromptCitationSource(c, citationCtx)) continue;
      if (!citationDomains.has(c.domain)) citationDomains.set(c.domain, c.url);
    }
  }
  const citationList = Array.from(citationDomains.entries());

  return (
    <tr
      className={`border-b border-gray-50 last:border-0 transition-colors group ${
        selectionMode
          ? selected ? "bg-violet-50/70 hover:bg-violet-50" : "hover:bg-gray-50"
          : "cursor-pointer hover:bg-blue-50/20"
      }`}
      onClick={selectionMode ? onToggleSelected : onClick}
    >
      {/* Prompt */}
      <td className="px-5 py-3 min-w-[200px] max-w-[280px]" style={nestLevel > 0 ? { paddingLeft: 20 + nestLevel * 16 } : undefined}>
        <div className="flex items-start gap-2.5">
          {selectionMode && (
            <input
              type="checkbox"
              className="mt-0.5 shrink-0"
              checked={selected}
              onChange={onToggleSelected}
              onClick={(event) => event.stopPropagation()}
              aria-label={`Select prompt: ${row.prompt ?? ""}`}
            />
          )}
          <div className="min-w-0 flex-1">
            <p className="text-sm text-[#0d0d0d] line-clamp-2 leading-snug group-hover:text-blue-900">
              {row.prompt}
            </p>
          </div>
        </div>
        {tags.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {tags.map((tag) => (
              <span key={tag} className="rounded-full bg-violet-50 px-1.5 py-0.5 text-[9px] font-semibold text-violet-700">
                {tag}
              </span>
            ))}
          </div>
        )}
      </td>

      {/* Visibility % */}
      <td className="px-4 py-3 text-center whitespace-nowrap">
        <span
          className={`text-sm font-bold tabular-nums ${
            visibilityPct >= 75 ? "text-emerald-600" : visibilityPct >= 50 ? "text-amber-500" : "text-gray-400"
          }`}
        >
          {Math.round(visibilityPct)}%
        </span>
      </td>

      {/* Sentiment */}
      <td className="px-4 py-3 text-center">
        <SentimentBadge sentiment={sentiment} />
      </td>

      {/* Avg Position */}
      <td className="px-4 py-3 text-center whitespace-nowrap">
        <span className={`text-sm font-bold tabular-nums ${avgPosition != null && avgPosition <= 3 ? "text-emerald-600" : avgPosition != null && avgPosition <= 5 ? "text-amber-500" : "text-gray-400"}`}>
          {formatPosition(avgPosition)}
        </span>
      </td>

      {/* Platforms */}
      <td className="px-4 py-3">
        <div className="flex items-center gap-1 flex-wrap">
          {platformsWithResponse.map((p) => (
            <PlatformLogo key={p.key} platform={p.key} size={18} />
          ))}
        </div>
      </td>

      {/* Competitors — deduplicated competitor brands */}
      <td className="px-4 py-3">
        <div className="flex items-center gap-1 flex-wrap">
          {mentionedBrands.slice(0, 6).map((b) => (
            b.website ? (
              <a
                key={b.name}
                href={b.website}
                target="_blank"
                rel="noopener noreferrer"
                title={`Open ${b.displayName} website`}
                className="rounded-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <CompetitorFavicon name={b.displayName} website={b.website} size={18} />
              </a>
            ) : (
              <CompetitorFavicon key={b.name} name={b.displayName} size={18} />
            )
          ))}
          {mentionedBrands.length > 6 && (
            <span className="text-[10px] text-gray-400">+{mentionedBrands.length - 6}</span>
          )}
          {mentionedBrands.length === 0 && (
            <span className="text-xs text-gray-300">—</span>
          )}
        </div>
      </td>

      {/* Citations — source URLs the AI used to generate its response */}
      <td className="px-4 py-3">
        <div className="flex items-center gap-1 flex-wrap">
          {citationList.slice(0, 5).map(([domain]) => (
            <img
              key={domain}
              src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`}
              alt={domain}
              title={domain}
              width={18}
              height={18}
              className="rounded-sm object-contain"
              onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
            />
          ))}
          {citationList.length > 5 && (
            <span className="text-[10px] text-gray-400">+{citationList.length - 5}</span>
          )}
          {citationList.length === 0 && (
            <span className="text-xs text-gray-300">—</span>
          )}
        </div>
      </td>
    </tr>
  );
}

export function PromptTable({
  auditSlug,
  ctx,
  live,
  brandLabel,
  brandMatchTokens,
  activePlatforms,
  localeKey,
  onRefresh,
  enableTagging = true,
  enableSentimentFetch = true,
  enableAddPrompt = true,
}: {
  auditSlug: string;
  ctx: PromptPerformanceContext;
  live: LiveProbeResult;
  brandLabel: string;
  brandMatchTokens: string[];
  activePlatforms: ProbePlatform[];
  localeKey?: string;
  onRefresh?: () => void;
  enableTagging?: boolean;
  enableSentimentFetch?: boolean;
  enableAddPrompt?: boolean;
}) {
  const [platformFilter, setPlatformFilter] = useState<ProbePlatform | "">("");
  const [surfaceFilter, setSurfaceFilter] = useState<SurfaceFilter>("all");
  const [topicFilter, setTopicFilter] = useState<string>("");
  const [tagFilter, setTagFilter] = useState<string>("");
  /** Topics present in this set are expanded. Overall defaults to all collapsed. */
  const [expandedTopics, setExpandedTopics] = useState<Set<string>>(new Set());
  /** Market groups under topics (Overall only). Empty = collapsed. */
  const [expandedMarkets, setExpandedMarkets] = useState<Set<string>>(new Set());
  const [topicsSeeded, setTopicsSeeded] = useState(false);
  const [selectedDetail, setSelectedDetail] = useState<{
    row: LiveProbePerPrompt;
    productLabel: string;
    localeKey?: string;
  } | null>(null);
  const [taggingMode, setTaggingMode] = useState(false);
  const [bulkTag, setBulkTag] = useState("");
  const [bulkTagInput, setBulkTagInput] = useState("");
  const [selectedPromptKeys, setSelectedPromptKeys] = useState<Set<string>>(new Set());
  const [savingTags, setSavingTags] = useState(false);
  const [taggingMessage, setTaggingMessage] = useState<string | null>(null);
  const [llmSentiment, setLlmSentiment] = useState<PromptSentimentAnalysis | null>(null);

  const viewLocaleKey = localeKey || OVERALL_LOCALE_KEY;
  const isOverallView = viewLocaleKey === OVERALL_LOCALE_KEY;
  const localeOptions = useMemo(() => configuredPromptLocales(ctx), [ctx]);

  useEffect(() => {
    if (!enableSentimentFetch) return;
    let cancelled = false;
    let timer: number | undefined;
    const load = () => {
      fetchPromptSentiment(auditSlug)
        .then((r) => {
          if (cancelled) return;
          if (r.sentiment) {
            setLlmSentiment(r.sentiment);
            return;
          }
          // Job still running — poll persisted cache.
          if (r.status && ["queued", "starting", "running"].includes(r.status)) {
            timer = window.setTimeout(load, 4000);
          }
        })
        .catch(() => {
          if (!cancelled) setLlmSentiment(null);
        });
    };
    load();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [auditSlug, enableSentimentFetch, live?.per_prompt?.length]);

  const geminiByPromptId = useMemo(
    () => buildGeminiPromptSentimentMap(llmSentiment),
    [llmSentiment],
  );
  const geminiSummaryByPromptId = useMemo(
    () => buildGeminiPromptSentimentSummaryMap(llmSentiment),
    [llmSentiment],
  );

  const compWebsiteMap = useMemo(() => {
    const map = new Map<string, string>();
    // Primary source: explicit competitors from the wizard
    for (const c of ctx.competitors ?? []) {
      if (c.competitor_brand) map.set(c.competitor_brand.toLowerCase(), c.competitor_website);
    }
    // Enrichment: scan all citations to find websites for competitors not in the wizard.
    // Match by comparing the domain's root against the competitor name's root
    // (e.g. "cerave.com" → "cerave" matches competitor "CeraVe").
    const citDomains = new Map<string, string>(); // domain → "https://domain"
    for (const row of (live?.per_prompt ?? []) as LiveProbePerPrompt[]) {
      for (const p of ["gemini", "openai", "claude", "google_aio"]) {
        const cits = ((row as Record<string, unknown>)[`citations_${p}`] ?? []) as CitationItem[];
        for (const c of cits) {
          if (!citDomains.has(c.domain)) citDomains.set(c.domain, `https://${c.domain}`);
        }
      }
    }
    // Collect all competitor names that appeared in mention_scores
    const detectedComps = new Set<string>();
    for (const row of (live?.per_prompt ?? []) as LiveProbePerPrompt[]) {
      for (const p of ["gemini", "openai", "claude", "google_aio"]) {
        const scores = (row as Record<string, MentionScores | undefined>)[`mention_scores_${p}`];
        for (const name of Object.keys(scores?.competitor_detail ?? {})) {
          if (!isVendorBrand(name) && isPlausibleCompetitorName(name)) detectedComps.add(name);
        }
      }
    }
    const toKey = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "");
    for (const name of detectedComps) {
      if (map.has(name.toLowerCase())) continue;
      const nameKey = toKey(name);
      for (const [domain, url] of citDomains) {
        const domKey = toKey(domain.replace(/^www\./, "").split(".")[0]);
        if (domKey === nameKey || (nameKey.length > 3 && domKey.includes(nameKey)) || (domKey.length > 3 && nameKey.includes(domKey))) {
          map.set(name.toLowerCase(), url);
          break;
        }
      }
    }
    return map;
  }, [ctx.competitors, live]);

  const topicTree = useMemo(
    () =>
      buildTopicMarketPromptTree(live, ctx, {
        localeKey: viewLocaleKey,
      }),
    [live, ctx, viewLocaleKey],
  );

  // Include configured topics plus any probe-derived topics.
  const allTopics = useMemo(
    () => topicTree.map((node) => node.productLabel),
    [topicTree],
  );
  const allPromptEntries = useMemo(
    () => topicTree.flatMap((node) => node.markets.flatMap((market) => market.prompts)),
    [topicTree],
  );
  const allTags = useMemo(
    () => Array.from(new Set(allPromptEntries.flatMap((entry) => entry.tags))).sort(),
    [allPromptEntries],
  );

  // Seed expansion: Overall stays collapsed; single-locale expands topics once.
  useEffect(() => {
    setTopicsSeeded(false);
    setExpandedTopics(new Set());
    setExpandedMarkets(new Set());
  }, [viewLocaleKey]);

  useEffect(() => {
    if (topicsSeeded || allTopics.length === 0) return;
    if (!isOverallView) {
      setExpandedTopics(new Set(allTopics));
    }
    setTopicsSeeded(true);
  }, [allTopics, isOverallView, topicsSeeded]);

  // Platforms available under the current Chatbots / AI Overviews surface filter.
  const surfacePlatforms = useMemo(
    () => platformsForSurface(surfaceFilter, activePlatforms) as ProbePlatform[],
    [surfaceFilter, activePlatforms],
  );

  // When a single platform is selected, metrics use that platform only.
  const metricPlatforms = useMemo(
    () => (platformFilter ? [platformFilter] : surfacePlatforms),
    [platformFilter, surfacePlatforms],
  );

  function rowHasPlatformResponse(row: LiveProbePerPrompt, platform: ProbePlatform): boolean {
    return completedPlatformRuns(row, platform).length > 0;
  }

  // Filter tree by topic / platform / surface / tag without flattening the hierarchy.
  const filteredTopicTree = useMemo(() => {
    if (!topicFilter && !platformFilter && !tagFilter && surfaceFilter === "all") {
      return topicTree;
    }
    const nodes: TopicMarketNode[] = [];
    for (const node of topicTree) {
      if (topicFilter && node.productLabel !== topicFilter) continue;
      const markets = node.markets
        .map((market) => ({
          ...market,
          prompts: market.prompts.filter((entry) => {
            if (platformFilter) {
              if (!rowHasPlatformResponse(entry.row, platformFilter)) return false;
            } else if (surfaceFilter !== "all") {
              const hasSurfaceResponse = surfacePlatforms.some((platform) =>
                rowHasPlatformResponse(entry.row, platform),
              );
              if (!hasSurfaceResponse) return false;
            }
            if (tagFilter && !entry.tags.includes(tagFilter)) return false;
            return true;
          }),
        }))
        .filter((market) => market.prompts.length > 0);
      if (!markets.length) continue;
      const prompts = markets.flatMap((m) => m.prompts);
      nodes.push({
        productLabel: node.productLabel,
        markets,
        allRows: prompts.map((p) => p.row),
        promptCount: new Set(prompts.map((p) => p.sourcePrompt.trim().toLowerCase())).size
          || prompts.length,
      });
    }
    return nodes;
  }, [topicTree, topicFilter, platformFilter, tagFilter, surfaceFilter, surfacePlatforms]);

  const hasFilter = !!platformFilter || !!topicFilter || !!tagFilter || surfaceFilter !== "all";
  const activeConfig = PLATFORM_CONFIG.filter((p) => surfacePlatforms.includes(p.key));

  function toggleTopic(topic: string) {
    setExpandedTopics((current) => {
      const next = new Set(current);
      if (next.has(topic)) next.delete(topic);
      else next.add(topic);
      return next;
    });
  }

  function toggleMarket(topic: string, marketKey: string) {
    const key = topicMarketKey(topic, marketKey);
    setExpandedMarkets((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function promptSelectionKey(productLabel: string, prompt: string) {
    return `${productLabel.trim().toLowerCase()}\u0000${prompt.trim().toLowerCase()}`;
  }

  function togglePromptSelection(productLabel: string, prompt: string) {
    const key = promptSelectionKey(productLabel, prompt);
    setSelectedPromptKeys((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function closeTaggingMode() {
    setTaggingMode(false);
    setBulkTag("");
    setBulkTagInput("");
    setSelectedPromptKeys(new Set());
    setTaggingMessage(null);
  }

  async function applyBulkTag() {
    const tag = (bulkTagInput.trim() || bulkTag).trim();
    if (!tag || selectedPromptKeys.size === 0) return;
    const selections = allPromptEntries
      .map((entry) => ({
        product_or_service: entry.productLabel,
        prompt: entry.sourcePrompt || String(entry.row.prompt ?? "").trim(),
      }))
      .filter(({ product_or_service, prompt }) =>
        selectedPromptKeys.has(promptSelectionKey(product_or_service, prompt)),
      );
    // Deduplicate logical prompts across markets for tagging.
    const seen = new Set<string>();
    const uniqueSelections = selections.filter((sel) => {
      const key = promptSelectionKey(sel.product_or_service, sel.prompt);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    setSavingTags(true);
    setTaggingMessage(null);
    try {
      const result = await addTagsToPrompts(auditSlug, { selections: uniqueSelections, tags: [tag] });
      setTaggingMessage(`Added “${tag}” to ${result.updated} prompt${result.updated === 1 ? "" : "s"}.`);
      setSelectedPromptKeys(new Set());
      setBulkTag("");
      setBulkTagInput("");
      onRefresh?.();
    } catch (error) {
      setTaggingMessage(error instanceof Error ? error.message : "Failed to add tags.");
    } finally {
      setSavingTags(false);
    }
  }

  // ── Add Prompt form state ──────────────────────────────────────────────────
  const [showAddForm, setShowAddForm] = useState(
    () => new URLSearchParams(window.location.search).get("addPrompt") === "1",
  );
  const [newPromptText, setNewPromptText] = useState("");
  const [newPromptCategory, setNewPromptCategory] = useState("");
  const [newPromptCategoryCustom, setNewPromptCategoryCustom] = useState("");
  const [newPromptTagInput, setNewPromptTagInput] = useState("");
  const [newPromptTags, setNewPromptTags] = useState<string[]>([]);
  const [addingPrompt, setAddingPrompt] = useState(false);
  const [addPromptMsg, setAddPromptMsg] = useState<string | null>(null);

  async function handleAddPrompt() {
    const text = newPromptText.trim();
    if (!text) return;
    const category = newPromptCategory === "__new__"
      ? newPromptCategoryCustom.trim() || CUSTOM_PROMPTS_LABEL
      : newPromptCategory || CUSTOM_PROMPTS_LABEL;
    setAddingPrompt(true);
    setAddPromptMsg(null);
    try {
      await addPromptToAudit(auditSlug, {
        prompt: text,
        category,
        tags: newPromptTags,
        run_probes: true,
      });
      setNewPromptText("");
      setNewPromptTags([]);
      setNewPromptTagInput("");
      setShowAddForm(false);
      setAddPromptMsg("Prompt added. Probe run queued.");
      onRefresh?.();
    } catch (e) {
      setAddPromptMsg(e instanceof Error ? e.message : "Failed to add prompt.");
    } finally {
      setAddingPrompt(false);
    }
  }

  return (
    <div>
      {/* ── Add Prompt panel ─────────────────────────────────────────────── */}
      {enableAddPrompt ? (
      <div className="mb-4">
        <button
          type="button"
          onClick={() => setShowAddForm((v) => !v)}
          className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-dashed border-blue-300 text-blue-600 bg-blue-50 hover:bg-blue-100 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          Add prompt
        </button>
        {addPromptMsg && (
          <span className="ml-3 text-xs text-emerald-600 font-semibold">{addPromptMsg}</span>
        )}

        {showAddForm && (
          <div className="mt-3 p-4 rounded-xl border border-blue-100 bg-blue-50/40 space-y-3 max-w-2xl">
            <textarea
              className="w-full text-sm border border-gray-200 rounded-lg px-3 py-2 min-h-[3.5rem] resize-y bg-white focus:outline-none focus:ring-1 focus:ring-blue-400"
              placeholder="e.g. Which skincare brand is best for dry skin?"
              value={newPromptText}
              onChange={(e) => setNewPromptText(e.target.value)}
            />
            <p className="text-[11px] text-gray-500 leading-relaxed">
              Do <strong>not</strong> include a country or market in the prompt text. We add your
              primary market automatically and adapt the wording for any extra markets/languages
              configured above.
            </p>
            <div className="flex flex-wrap gap-3">
              {/* Category */}
              <div className="flex-1 min-w-[10rem]">
                <label className="text-xs font-medium text-gray-600 block mb-1">Category</label>
                <select
                  className="text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white w-full focus:outline-none focus:ring-1 focus:ring-blue-400"
                  value={newPromptCategory}
                  onChange={(e) => setNewPromptCategory(e.target.value)}
                >
                  <option value="">{CUSTOM_PROMPTS_LABEL}</option>
                  {allTopics.map((t) => <option key={t} value={t}>{t}</option>)}
                  <option value="__new__">+ New category…</option>
                </select>
              </div>
              {newPromptCategory === "__new__" && (
                <div className="flex-1 min-w-[10rem]">
                  <label className="text-xs font-medium text-gray-600 block mb-1">Category name</label>
                  <input
                    type="text"
                    list="prompt-performance-known-tags"
                    className="text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white w-full focus:outline-none focus:ring-1 focus:ring-blue-400"
                    placeholder="e.g. Brand awareness"
                    value={newPromptCategoryCustom}
                    onChange={(e) => setNewPromptCategoryCustom(e.target.value)}
                  />
                  <datalist id="prompt-performance-known-tags">
                    {allTags.map((tag) => <option key={tag} value={tag} />)}
                  </datalist>
                </div>
              )}
              {/* Tags */}
              <div className="flex-1 min-w-[10rem]">
                <label className="text-xs font-medium text-gray-600 block mb-1">Tags</label>
                <div className="flex flex-wrap items-center gap-1.5 border border-gray-200 rounded-lg px-2 py-1 bg-white min-h-[2rem]">
                  {newPromptTags.map((tag) => (
                    <span key={tag} className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">
                      {tag}
                      <button type="button" onClick={() => setNewPromptTags((t) => t.filter((x) => x !== tag))} className="text-blue-400 hover:text-blue-700">×</button>
                    </span>
                  ))}
                  <input
                    type="text"
                    className="flex-1 min-w-[4rem] text-xs outline-none"
                    placeholder="Tag, Enter"
                    value={newPromptTagInput}
                    onChange={(e) => setNewPromptTagInput(e.target.value)}
                    onKeyDown={(e) => {
                      if ((e.key === "Enter" || e.key === ",") && newPromptTagInput.trim()) {
                        e.preventDefault();
                        const t = newPromptTagInput.trim().replace(/,$/, "");
                        if (t && !newPromptTags.includes(t)) setNewPromptTags((prev) => [...prev, t]);
                        setNewPromptTagInput("");
                      }
                    }}
                  />
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={!newPromptText.trim() || addingPrompt}
                onClick={handleAddPrompt}
                className="text-xs font-semibold px-4 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                {addingPrompt ? <><Loader2 className="w-3 h-3 inline animate-spin mr-1" />Adding & running…</> : "Add & run probe"}
              </button>
              <button
                type="button"
                onClick={() => setShowAddForm(false)}
                className="text-xs text-gray-400 hover:text-gray-600"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
      ) : null}

      {/* Bulk prompt tagging */}
      {enableTagging ? (
      <div className="mb-4">
        <button
          type="button"
          onClick={() => {
            if (taggingMode) closeTaggingMode();
            else {
              setTaggingMode(true);
              setTaggingMessage(null);
            }
          }}
          className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${
            taggingMode
              ? "border-violet-300 bg-violet-50 text-violet-700"
              : "border-gray-200 bg-white text-gray-600 hover:border-violet-300 hover:text-violet-700"
          }`}
        >
          <Tags className="h-3.5 w-3.5" />
          {taggingMode ? "Cancel tagging" : "Add tags"}
        </button>

        {taggingMode && (
          <div className="mt-3 rounded-xl border border-violet-200 bg-violet-50/40 p-4">
            <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] md:items-end">
              <div>
                <label className="mb-1 block text-xs font-medium text-gray-700">Add new tag</label>
                <input
                  type="text"
                  className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-violet-400"
                  value={bulkTagInput}
                  onChange={(event) => {
                    setBulkTagInput(event.target.value);
                    if (event.target.value) setBulkTag("");
                  }}
                  placeholder="Enter a tag name"
                />
              </div>

              {allTags.length > 0 && (
                <div>
                  <p className="mb-2 text-xs font-medium text-gray-700">Choose existing tag</p>
                  <div className="flex flex-wrap gap-1.5">
                    {allTags.map((tag) => (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => {
                          setBulkTag(bulkTag === tag ? "" : tag);
                          setBulkTagInput("");
                        }}
                        className={`rounded-full border px-2.5 py-1 text-xs font-medium transition-colors ${
                          bulkTag === tag
                            ? "border-violet-400 bg-violet-100 text-violet-800"
                            : "border-gray-200 bg-white text-gray-600 hover:border-violet-300"
                        }`}
                      >
                        {tag}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <button
                type="button"
                className="btn-primary whitespace-nowrap text-xs"
                onClick={applyBulkTag}
                disabled={
                  savingTags
                  || selectedPromptKeys.size === 0
                  || !(bulkTagInput.trim() || bulkTag)
                }
              >
                {savingTags
                  ? "Applying…"
                  : `Apply to ${selectedPromptKeys.size} prompt${selectedPromptKeys.size === 1 ? "" : "s"}`}
              </button>
            </div>
            <p className="mt-3 text-xs text-gray-500">
              Choose or create a tag, then select prompts from the expanded topic rows below.
            </p>
            {taggingMessage && (
              <p className="mt-2 text-xs font-medium text-violet-700" role="status">
                {taggingMessage}
              </p>
            )}
          </div>
        )}
      </div>
      ) : null}

      {/* Filter bar — dropdowns */}
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <span className="flex items-center gap-1.5 pb-1.5 text-xs font-semibold text-gray-400 shrink-0">
          <Filter className="w-3.5 h-3.5" /> Filter
        </span>

        <ReportFilterSelect
          id="prompts-surface-select"
          label="Surface"
          hint="Chatbots (Gemini, ChatGPT, Claude) or Google AI Overviews."
          value={surfaceFilter}
          onChange={(e) => {
            const next = e.target.value as SurfaceFilter;
            setSurfaceFilter(next);
            const nextPlatforms = platformsForSurface(next, metricPlatforms);
            if (platformFilter && !nextPlatforms.includes(platformFilter)) {
              setPlatformFilter("");
            }
          }}
        >
          <option value="all">All surfaces</option>
          <option value="chatbots">Chatbots</option>
          <option value="overviews">AI Overviews</option>
        </ReportFilterSelect>

        <ReportFilterSelect
          id="prompts-platform-select"
          label="Platform"
          hint="Show prompts with a response from this platform."
          value={platformFilter}
          onChange={(e) => setPlatformFilter(e.target.value as ProbePlatform | "")}
        >
          <option value="">All platforms</option>
          {activeConfig.map((p) => (
            <option key={p.key} value={p.key}>{p.label}</option>
          ))}
        </ReportFilterSelect>

        {allTopics.length > 0 && (
          <ReportFilterSelect
            id="prompts-topic-select"
            label="Topic"
            hint="Limit the table to one product or service topic."
            value={topicFilter}
            onChange={(e) => setTopicFilter(e.target.value)}
          >
            <option value="">All topics</option>
            {allTopics.map((topic) => (
              <option key={topic} value={topic}>{topic}</option>
            ))}
          </ReportFilterSelect>
        )}

        {allTags.length > 0 && (
          <ReportFilterSelect
            id="prompts-tag-select"
            label="Tag"
            value={tagFilter}
            onChange={(e) => setTagFilter(e.target.value)}
          >
            <option value="">All tags</option>
            {allTags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}
          </ReportFilterSelect>
        )}

        {hasFilter && (
          <button
            type="button"
            onClick={() => {
              setPlatformFilter("");
              setSurfaceFilter("all");
              setTopicFilter("");
              setTagFilter("");
            }}
            className="pb-1.5 text-xs text-gray-400 underline hover:text-gray-600"
          >
            Clear
          </button>
        )}

        <span className="ml-auto pb-1.5 text-xs text-gray-400">
          {filteredTopicTree.length} topic{filteredTopicTree.length !== 1 ? "s" : ""}
          {hasFilter && ` of ${allTopics.length}`}
        </span>
      </div>

      {/* Table: Topic → (Market:Language) → Prompt. Overall keeps topics/markets collapsed by default. */}
      <div className="rounded-xl border border-gray-200 overflow-hidden">
        <VirtualScrollTable
          colSpan={7}
          estimateSize={52}
          tableClassName="w-full text-sm min-w-[680px]"
          head={(
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="px-5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">Topics</th>
              <th className="px-4 py-2.5 text-center text-[10px] font-semibold uppercase tracking-wide text-gray-400 whitespace-nowrap">
                Visibility
                <ColTooltip text="% of AI platform responses for this prompt that mention your brand." />
              </th>
              <th className="px-4 py-2.5 text-center text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                Sentiment
                <ColTooltip text="Overall tone of AI responses that mention your brand for this prompt: + positive, − negative, ~ neutral." />
              </th>
              <th className="px-4 py-2.5 text-center text-[10px] font-semibold uppercase tracking-wide text-gray-400 whitespace-nowrap">
                Avg. Position
                <ColTooltip text="Average rank at which your brand first appears in numbered recommendation lists across platform responses. Lower is better (#1 = top mention)." />
              </th>
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                Platforms
                <ColTooltip text="AI platforms that returned a response for this prompt." />
              </th>
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                Competitors
                <ColTooltip text="Competitor brands mentioned or recommended in the AI responses. Retailer/vendor links are shown separately in the row overlay." />
              </th>
              <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                Citations
                <ColTooltip text="Information sources the AI cited to generate its response. Retailer where-to-buy links are classified as Vendors and shown in the row overlay." />
              </th>
            </tr>
          )}
          empty={(
            <tr>
              <td colSpan={7} className="px-6 py-8 text-center text-sm text-gray-400">
                No configured topics match the current filters.
              </td>
            </tr>
          )}
          rows={filteredTopicTree.flatMap((node) => {
              const topic = node.productLabel;
              const rows = node.allRows;
              const isTopicExpanded = expandedTopics.has(topic);
              const showMarketLevel = isOverallView && node.markets.length > 1;
              const activeCfg = PLATFORM_CONFIG.filter((p) => metricPlatforms.includes(p.key));

              const avgVis = averageVisibilityAcrossPrompts(
                rows,
                rows.map((row) =>
                  promptVisibilityPct(row, brandLabel, brandMatchTokens, metricPlatforms),
                ),
              );

              const topicPositions: number[] = [];
              for (const row of rows) {
                const pos = computePromptAvgPosition(row as Record<string, unknown>, brandMatchTokens, metricPlatforms);
                if (pos != null) topicPositions.push(pos);
              }
              const avgTopicPosition = topicPositions.length > 0
                ? topicPositions.reduce((a, b) => a + b, 0) / topicPositions.length
                : null;

              const dominantSentiment = dominantGeminiSentiment(
                rows.map((row) => geminiSentimentForPrompt(row, geminiByPromptId)),
              );

              const topicPlatforms = activeCfg.filter(({ key }) =>
                rows.some((row) =>
                  !!(row as Record<string, unknown>)[`${key}_response`]
                  || (row.list_metrics?.platforms_responded ?? []).includes(key)
                  || Boolean((row as Record<string, unknown>)[`has_response_${key}`]),
                ),
              );

              const rawTopicMentions = new Map<string, { name: string; website?: string }>();
              for (const row of rows) {
                for (const p of metricPlatforms) {
                  const scores = (row as Record<string, MentionScores | undefined>)[`mention_scores_${p}`];
                  for (const [cName, hits] of Object.entries(scores?.competitor_detail ?? {})) {
                    if (
                      Number(hits) > 0
                      && !isVendorBrand(cName)
                      && isPlausibleCompetitorName(cName)
                      && !rawTopicMentions.has(cName)
                    ) {
                      rawTopicMentions.set(cName, { name: cName, website: compWebsiteMap.get(cName.toLowerCase()) });
                    }
                  }
                }
              }
              const topicCanonMap = buildCanonicalNameMap(rawTopicMentions.keys());
              const seenTopicCanonicals = new Set<string>();
              const topicMentions: { name: string; displayName: string; website?: string }[] = [];
              for (const [raw, info] of rawTopicMentions) {
                const canon = topicCanonMap.get(raw) ?? raw;
                if (seenTopicCanonicals.has(canon)) continue;
                seenTopicCanonicals.add(canon);
                const displayName = isDomainString(canon) ? domainToLabel(canon) : canon;
                const website = info.website ?? (isDomainString(raw) ? `https://${raw}` : undefined);
                topicMentions.push({ name: canon, displayName, website });
              }

              const topicCitDomains = new Set<string>();
              for (const row of rows) {
                for (const p of metricPlatforms) {
                  const cits = ((row as Record<string, unknown>)[`citations_${p}`] ?? []) as CitationItem[];
                  for (const c of cits) {
                    if (isPromptCitationSource(c, ctx)) topicCitDomains.add(c.domain);
                  }
                }
              }
              const topicCitList = Array.from(topicCitDomains);

              const childRows: ReactNode[] = [];
              if (isTopicExpanded) {
                if (showMarketLevel) {
                  for (const market of node.markets) {
                    const marketKey = topicMarketKey(topic, market.localeKey);
                    const isMarketExpanded = expandedMarkets.has(marketKey);
                    const marketRows = market.prompts.map((p) => p.row);
                    const marketVis = averageVisibilityAcrossPrompts(
                      marketRows,
                      marketRows.map((row) =>
                        promptVisibilityPct(row, brandLabel, brandMatchTokens, metricPlatforms),
                      ),
                    );
                    childRows.push(
                      <tr
                        key={`market-${topic}-${market.localeKey}`}
                        className="cursor-pointer border-b border-stone-100 bg-stone-50/80 transition-colors hover:bg-stone-100/80"
                        onClick={() => toggleMarket(topic, market.localeKey)}
                      >
                        <td className="px-5 py-2.5" style={{ paddingLeft: 36 }}>
                          <div className="flex items-center gap-2">
                            {isMarketExpanded ? (
                              <ChevronDown className="h-3.5 w-3.5 shrink-0 text-stone-500" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 shrink-0 text-stone-500" />
                            )}
                            <span className="text-xs font-bold text-stone-700">
                              {marketLanguageLabel(market.localeKey, localeOptions)}
                            </span>
                            <span className="text-[10px] font-semibold text-stone-500 px-1.5 py-0.5 bg-stone-200/70 rounded-full">
                              {market.prompts.length} prompt{market.prompts.length !== 1 ? "s" : ""}
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-center whitespace-nowrap">
                          <span className={`text-xs font-bold tabular-nums ${marketVis >= 75 ? "text-emerald-600" : marketVis >= 50 ? "text-amber-500" : "text-gray-400"}`}>
                            {marketVis}%
                          </span>
                        </td>
                        <td className="px-4 py-2.5 text-center text-stone-300">—</td>
                        <td className="px-4 py-2.5 text-center text-stone-300">—</td>
                        <td className="px-4 py-2.5 text-stone-300">—</td>
                        <td className="px-4 py-2.5 text-stone-300">—</td>
                        <td className="px-4 py-2.5 text-stone-300">—</td>
                      </tr>,
                    );
                    if (isMarketExpanded) {
                      for (const [index, entry] of market.prompts.entries()) {
                        const { row, productLabel, tags, sourcePrompt, localeKey: entryLocale } = entry;
                        childRows.push(
                          <PromptTableRow
                            key={`${topic}-${entryLocale}-${sourcePrompt || String(row.prompt ?? index)}`}
                            row={row}
                            productLabel={productLabel}
                            brandLabel={brandLabel}
                            brandMatchTokens={brandMatchTokens}
                            activePlatforms={metricPlatforms}
                            compWebsiteMap={compWebsiteMap}
                            citationCtx={ctx}
                            tags={tags}
                            nestLevel={2}
                            geminiSentiment={geminiSentimentForPrompt(row, geminiByPromptId)}
                            selectionMode={taggingMode}
                            selected={selectedPromptKeys.has(
                              promptSelectionKey(productLabel, sourcePrompt || String(row.prompt ?? "")),
                            )}
                            onToggleSelected={() =>
                              togglePromptSelection(productLabel, sourcePrompt || String(row.prompt ?? ""))
                            }
                            onClick={() => setSelectedDetail({
                              row,
                              productLabel,
                              localeKey: entryLocale || row._locale_key,
                            })}
                          />,
                        );
                      }
                    }
                  }
                } else {
                  const prompts = node.markets.flatMap((m) => m.prompts);
                  for (const [index, entry] of prompts.entries()) {
                    const { row, productLabel, tags, sourcePrompt, localeKey: entryLocale } = entry;
                    childRows.push(
                      <PromptTableRow
                        key={`${topic}-${entryLocale}-${sourcePrompt || String(row.prompt ?? index)}`}
                        row={row}
                        productLabel={productLabel}
                        brandLabel={brandLabel}
                        brandMatchTokens={brandMatchTokens}
                        activePlatforms={metricPlatforms}
                        compWebsiteMap={compWebsiteMap}
                        citationCtx={ctx}
                        tags={tags}
                        nestLevel={1}
                        geminiSentiment={geminiSentimentForPrompt(row, geminiByPromptId)}
                        selectionMode={taggingMode}
                        selected={selectedPromptKeys.has(
                          promptSelectionKey(productLabel, sourcePrompt || String(row.prompt ?? "")),
                        )}
                        onToggleSelected={() =>
                          togglePromptSelection(productLabel, sourcePrompt || String(row.prompt ?? ""))
                        }
                        onClick={() => setSelectedDetail({
                          row,
                          productLabel,
                          localeKey: entryLocale
                            || row._locale_key
                            || (viewLocaleKey !== OVERALL_LOCALE_KEY ? viewLocaleKey : undefined),
                        })}
                      />,
                    );
                  }
                }
              }

              return [
                <tr
                  key={`header-${topic}`}
                  className="cursor-pointer border-b border-blue-100 bg-blue-50/60 transition-colors hover:bg-blue-100/70"
                  onClick={() => toggleTopic(topic)}
                >
                  <td className="px-5 py-3">
                    <div className="flex items-center gap-2.5">
                      {isTopicExpanded ? (
                        <ChevronDown className="h-4 w-4 shrink-0 text-blue-500" />
                      ) : (
                        <ChevronRight className="h-4 w-4 shrink-0 text-blue-500" />
                      )}
                      <span className="text-sm font-bold text-[#0d0d0d]">{topic}</span>
                      <span className="text-xs font-semibold text-blue-500 px-1.5 py-0.5 bg-blue-100 rounded-full">
                        {node.promptCount} tested
                      </span>
                      {showMarketLevel && (
                        <span className="text-[10px] font-semibold text-blue-400">
                          {node.markets.length} market{node.markets.length !== 1 ? "s" : ""}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-center whitespace-nowrap">
                    <span className={`text-sm font-bold tabular-nums ${avgVis >= 75 ? "text-emerald-600" : avgVis >= 50 ? "text-amber-500" : "text-gray-400"}`}>
                      {avgVis}%
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <SentimentBadge sentiment={dominantSentiment} />
                  </td>
                  <td className="px-4 py-3 text-center whitespace-nowrap">
                    <span className={`text-sm font-bold tabular-nums ${avgTopicPosition != null && avgTopicPosition <= 3 ? "text-emerald-600" : avgTopicPosition != null && avgTopicPosition <= 5 ? "text-amber-500" : "text-gray-400"}`}>
                      {formatPosition(avgTopicPosition)}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 flex-wrap">
                      {topicPlatforms.map((p) => <PlatformLogo key={p.key} platform={p.key} size={16} />)}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 flex-wrap">
                      {topicMentions.slice(0, 6).map((b) => (
                        b.website ? (
                          <a
                            key={b.name}
                            href={b.website}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={`Open ${b.displayName} website`}
                            onClick={(event) => event.stopPropagation()}
                            className="rounded-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                          >
                            <CompetitorFavicon name={b.displayName} website={b.website} size={16} />
                          </a>
                        ) : (
                          <CompetitorFavicon key={b.name} name={b.displayName} size={16} />
                        )
                      ))}
                      {topicMentions.length > 6 && <span className="text-[10px] text-gray-400">+{topicMentions.length - 6}</span>}
                      {topicMentions.length === 0 && <span className="text-xs text-gray-300">—</span>}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-1 flex-wrap">
                      {topicCitList.slice(0, 5).map((domain) => (
                        <img
                          key={domain}
                          src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=32`}
                          alt={domain}
                          title={domain}
                          width={16}
                          height={16}
                          className="rounded-sm object-contain"
                          onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                        />
                      ))}
                      {topicCitList.length > 5 && <span className="text-[10px] text-gray-400">+{topicCitList.length - 5}</span>}
                      {topicCitList.length === 0 && <span className="text-xs text-gray-300">—</span>}
                    </div>
                  </td>
                </tr>,
                ...childRows,
              ];
            })}
        />
      </div>

      {selectedDetail && (
        <PromptDetailOverlay
          auditSlug={auditSlug}
          row={selectedDetail.row}
          productLabel={selectedDetail.productLabel}
          brandLabel={brandLabel}
          brandMatchTokens={brandMatchTokens}
          activePlatforms={metricPlatforms}
          compWebsiteMap={compWebsiteMap}
          citationCtx={ctx}
          localeKey={selectedDetail.localeKey || localeKey}
          geminiSentiment={geminiSentimentForPrompt(selectedDetail.row, geminiByPromptId)}
          geminiSummary={
            geminiSummaryByPromptId.get(String(selectedDetail.row.prompt_id || "").trim())?.summary
            ?? null
          }
          onClose={() => setSelectedDetail(null)}
        />
      )}

    </div>
  );
}


// ── Main export ───────────────────────────────────────────────────────────────

export function PromptPerformanceSection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const { ctx, loading, error: storeError, refetch, ensureScope, setCtx } = usePromptPerformanceContext(auditDirOrSlug);
  const [error, setError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [probeMsg, setProbeMsg] = useState<string | null>(null);
  const [selectedLocaleKey, setSelectedLocaleKey] = useState<string | null>(null);
  const [localeDraft, setLocaleDraft] = useState<PromptLocale[]>([]);
  const [localeSaving, setLocaleSaving] = useState(false);
  const [showLocaleEditor, setShowLocaleEditor] = useState(false);

  const slug = auditDirOrSlug;
  const errorMsg = error || storeError;

  useEffect(() => {
    setSelectedLocaleKey(null);
  }, [auditDirOrSlug]);

  useEffect(() => {
    if (!ctx) return;
    const locales = normalizePromptLocales(
      ctx.prompt_locales as PromptLocale[] | undefined,
      ctx.primary_market?.country ?? "",
      ctx.primary_market?.country_id ?? "",
    );
    setLocaleDraft(locales);
    setSelectedLocaleKey((prev) => (prev == null ? preferredInitialLocaleKey(ctx) : prev));
  }, [ctx]);

  const onLocaleChange = (key: string) => {
    setSelectedLocaleKey(key);
    void (async () => {
      try {
        if (key === OVERALL_LOCALE_KEY) {
          await ensureScope({ allLocales: true });
        } else {
          await ensureScope({ locale: key });
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load locale data");
      }
    })();
  };

  useEffect(() => {
    if (!ctx?.live_probe_in_progress) return;
    const progressDone =
      ctx.probe_progress?.status === "complete" || ctx.probe_progress?.status === "error";
    if (progressDone) {
      setProbing(false);
      return;
    }
    setProbing(true);
    const timer = window.setInterval(() => {
      void refetch()
        .then((data) => {
          const done =
            !data.live_probe_in_progress
            || data.probe_progress?.status === "complete"
            || data.probe_progress?.status === "error";
          if (done) {
            window.clearInterval(timer);
            setProbing(false);
            if (!data.live_probe_in_progress) {
              setProbeMsg("Live probes complete. Sentiment and SOV analysis updated.");
            }
          }
        })
        .catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [ctx?.live_probe_in_progress, ctx?.probe_progress?.status, refetch]);

  const localeKey = selectedLocaleKey || OVERALL_LOCALE_KEY;
  const live = liveProbeForLocale(ctx, localeKey);
  const hasProbes = Boolean(live?.per_prompt?.length);
  const activePlatforms = useMemo(() => activeProbePlatforms(live), [live]);
  const probeLabel = probePlatformsLabel(activePlatforms);
  const filterLocales = normalizePromptLocales(
    (ctx?.prompt_locales as PromptLocale[] | undefined) ?? localeDraft,
    ctx?.primary_market?.country ?? "",
    ctx?.primary_market?.country_id ?? "",
  );

  const probeProgress = ctx?.probe_progress;
  const probeProgressTerminal =
    probeProgress?.status === "complete" || probeProgress?.status === "error";
  const showLiveProbeBanner =
    (probing || Boolean(ctx?.live_probe_in_progress)) && !probeProgressTerminal;
  const probeEtaLabel = useMemo(() => {
    if (!showLiveProbeBanner) return null;
    const planned = probeProgress?.planned_calls ?? 0;
    const completed = probeProgress?.completed_calls ?? 0;
    if (planned > 0) {
      const remaining = Math.max(0, planned - completed);
      if (remaining <= 0) return null;
      return formatProbeRunEta({
        remainingCalls: remaining,
        totalCalls: planned,
      });
    }
    const prompts = ctx?.prompt_count ?? 0;
    if (prompts <= 0) return null;
    const derived = derivePlatformCallsPerMarket({
      promptCount: prompts,
      platformCount: Math.max(activePlatforms.length, 1),
    });
    return formatProbeRunEta({ totalCalls: derived });
  }, [
    showLiveProbeBanner,
    probeProgress?.planned_calls,
    probeProgress?.completed_calls,
    ctx?.prompt_count,
    activePlatforms.length,
  ]);

  const runProbes = async (opts?: { failedOnly?: boolean; localeKeys?: string[] }) => {
    setProbing(true);
    setProbeMsg(null);
    try {
      await updatePromptLocales(slug, localeDraft);
      await runPromptPerformanceProbes(slug, {
        reportMode: true,
        failedOnly: opts?.failedOnly,
        localeKeys: opts?.localeKeys,
      });
      if (ctx) setCtx({ ...ctx, live_probe_in_progress: true });
      setProbeMsg(
        opts?.failedOnly || opts?.localeKeys?.length
          ? "Re-run queued for selected/failed markets only. Successful markets are kept."
          : "Prompt run queued for all configured markets/languages. Results will update automatically.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Probe run failed");
      setProbing(false);
    }
  };

  const missingLocales = localesNeedingProbe(ctx);
  const runFailedLocales = () => void runProbes({
    failedOnly: true,
    localeKeys: missingLocales.length ? missingLocales : undefined,
  });

  const saveLocales = async () => {
    setLocaleSaving(true);
    setError(null);
    try {
      const res = await updatePromptLocales(slug, localeDraft);
      const saved = normalizePromptLocales(
        res.prompt_locales as PromptLocale[] | undefined,
        ctx?.primary_market?.country ?? "",
        ctx?.primary_market?.country_id ?? "",
      );
      setLocaleDraft(saved);
      if (ctx) setCtx({ ...ctx, prompt_locales: saved });
      setProbeMsg("Markets/languages saved. Re-run probes to refresh results for every locale.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save locales");
    } finally {
      setLocaleSaving(false);
    }
  };

  if (loading) {
    return <PageLoading />;
  }
  if (errorMsg && !ctx) return <div className="alert-error">{errorMsg}</div>;
  if (!ctx) return null;

  const brandLabel = ctx.brand_name?.trim() || "Brand";
  const brandMatchTokens =
    ctx.highlight?.brand_match_tokens?.length
      ? ctx.highlight.brand_match_tokens
      : live?.brand_match_tokens ?? [];

  return (
    <Card className="!mb-0">
      <div className="flex items-center justify-between mb-5">
        <div>
          <CardTitle>Prompts</CardTitle>
          <CardDescription>
            AI probe results for each tracked prompt — click a row to see full responses.
          </CardDescription>
        </div>
        {/* Re-run probes button (subtle) */}
        {ctx.prompt_count > 0 && (
          <div className="flex items-center gap-2 shrink-0">
            {missingLocales.length > 0 ? (
              <button
                type="button"
                className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-amber-300 text-amber-800 bg-amber-50 hover:bg-amber-100 transition-colors"
                disabled={probing}
                onClick={runFailedLocales}
              >
                {probing ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />Running…</> : `Re-run failed markets (${missingLocales.length})`}
              </button>
            ) : null}
            <button
              type="button"
              className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-gray-200 text-gray-500 bg-white hover:border-gray-300 hover:text-gray-700 transition-colors"
              disabled={probing}
              onClick={() => void runProbes()}
            >
              {probing ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />Running…</> : "Re-run all markets"}
            </button>
          </div>
        )}
      </div>

      <PromptLocaleNestedFilter
        locales={filterLocales}
        selectedKey={localeKey}
        onChange={onLocaleChange}
        ctx={ctx}
        failedLocaleKeys={missingLocales}
      />

      {localeKey !== OVERALL_LOCALE_KEY && !live ? (
        <div className="alert-info mb-4 text-sm">
          No probe data for this market/language.
          {missingLocales.includes(localeKey) ? (
            <>
              {" "}
              <button
                type="button"
                className="underline font-semibold"
                disabled={probing}
                onClick={() => void runProbes({ localeKeys: [localeKey] })}
              >
                Re-run this market only
              </button>
            </>
          ) : null}
        </div>
      ) : null}

      {probeMsg && <p className="alert-success mb-4 text-sm">{probeMsg}</p>}

      {showLiveProbeBanner && (
        <div className="alert-info mb-4 text-sm" role="status" aria-live="polite">
          <p className="font-medium text-brand-dark flex items-center gap-2">
            <Loader2 className="w-4 h-4 animate-spin shrink-0" aria-hidden />
            Live probes running
            {probeProgress?.market_count && probeProgress.market_count > 1
              ? ` across ${probeProgress.market_count} markets`
              : ""}
            …
          </p>
          {probeProgress?.planned_calls && probeProgress.planned_calls > 0 ? (
            <p className="mt-1 tabular-nums text-gray-600">
              Platform calls {probeProgress.completed_calls ?? 0}/{probeProgress.planned_calls}
              {(probeProgress.market_count ?? 0) > 1 ? " per market" : ""}
              {probeEtaLabel ? ` · ${probeEtaLabel}` : ""}
            </p>
          ) : probeEtaLabel ? (
            <p className="mt-1 text-gray-600">{probeEtaLabel}</p>
          ) : null}
        </div>
      )}

      <div className="mb-6">
        <button
          type="button"
          onClick={() => setShowLocaleEditor((v) => !v)}
          className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-dashed border-blue-300 text-blue-600 bg-blue-50 hover:bg-blue-100 transition-colors"
          aria-expanded={showLocaleEditor}
        >
          <Plus className="w-3.5 h-3.5" />
          Prompt markets &amp; languages
        </button>

        {showLocaleEditor && (
          <div className="mt-3 rounded-xl border border-gray-200 p-4">
            <PromptLocaleEditor
              compact
              marketCountry={ctx.primary_market?.country ?? ""}
              marketCountryCode={ctx.primary_market?.country_id ?? ""}
              locales={localeDraft}
              onChange={setLocaleDraft}
            />
            <div className="mt-3 flex justify-end">
              <button
                type="button"
                className="btn-secondary text-xs"
                disabled={localeSaving}
                onClick={() => void saveLocales()}
              >
                {localeSaving ? "Saving…" : "Save markets & languages"}
              </button>
            </div>
          </div>
        )}
      </div>

      {ctx.prompt_count === 0 && (
        <div className="alert-info mb-4">
          No prompts on file. Complete the wizard products &amp; prompts step, then re-run the audit.
        </div>
      )}

      {!hasProbes && ctx.prompt_count > 0 && (
        <div className="flex flex-col items-center justify-center py-12 text-center">
          <p className="text-sm text-gray-400 mb-4">No probe results yet. Run probes to see prompt performance.</p>
          <button type="button" className="btn-secondary" disabled={probing} onClick={() => void runProbes()}>
            {probing ? <><Loader2 className="w-4 h-4 animate-spin" />Running probes…</> : `Run live probes (${probeLabel})`}
          </button>
        </div>
      )}

      {hasProbes && live && (
        <PromptTable
          auditSlug={slug}
          ctx={ctx}
          live={live}
          brandLabel={brandLabel}
          brandMatchTokens={brandMatchTokens}
          activePlatforms={activePlatforms}
          localeKey={localeKey}
          onRefresh={() => void refetch()}
        />
      )}

      {error && ctx && <p className="alert-error mt-4 text-sm">{error}</p>}
    </Card>
  );
}
