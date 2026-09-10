/**
 * Platform Readiness — replaces the WIP placeholder.
 *
 * Shows per-platform AI readiness by combining:
 *   - Live probe visibility % per platform (from prompt_performance_live_probe.json)
 *   - Backend platform readiness signals from report.html (rendered inline via the
 *     existing ai-visibility iframe which contains platform cards, OR approximated
 *     from score breakdown)
 *
 * Score per platform = 0.55 × probe_visibility_pct + 0.45 × technical_readiness
 * where technical_readiness is the backend "ai_visibility" score (best proxy available
 * until a per-platform breakdown endpoint exists).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Info, ExternalLink, MessageCircle, Search } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { fetchScoreBreakdown } from "../api/client";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import type { PromptPerformanceContext } from "../types";
import { scoreColor, scoreLabel, scoreTone } from "../lib/reportScore";
import { computeVisibilityMetrics } from "../lib/visibilityMetrics";
import { PlatformLogo } from "./PlatformLogo";
import { TextWithSampleScriptsLink } from "./SampleScriptsLink";

// ── Tooltip ───────────────────────────────────────────────────────────────────

function Tooltip({ text }: { text: string }) {
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
      <Info className="w-3.5 h-3.5 text-gray-300" />
      {pos && createPortal(
        <span
          className="fixed w-60 rounded-xl bg-gray-900 text-white text-xs px-3 py-2.5 leading-relaxed z-[9999] shadow-xl pointer-events-none"
          style={{ left: pos.x, top: pos.y, transform: "translateX(-50%)" }}
        >
          {text}
        </span>,
        document.body,
      )}
    </span>
  );
}

// ── Platform config ────────────────────────────────────────────────────────────

export type PlatformSurface = "chatbots" | "overviews";

export const PLATFORM_READINESS_CONFIG = [
  {
    key: "gemini",
    baseKey: "gemini",
    probeKey: "gemini",
    surface: "chatbots" as const,
    label: "Gemini",
    description: "Google Gemini conversational answers. Relies heavily on schema.org markup, entity clarity, and Google Search indexing. Strong structured data and E-E-A-T signals matter most.",
    improvements: ["Add or improve JSON-LD schema (FAQPage, Product, Organization)", "Strengthen E-E-A-T signals", "Ensure Google Search Console indexing is clean"],
    docLink: "https://developers.google.com/search/docs/appearance/ai-overviews",
  },
  {
    key: "openai",
    baseKey: "chatgpt",
    probeKey: "openai",
    surface: "chatbots" as const,
    label: "ChatGPT / OpenAI",
    description: "ChatGPT web search and GPT-4o retrieval. Values brand authority, Wikipedia presence, and comprehensive web mentions. Clean robots.txt access for GPTBot is critical.",
    improvements: ["Ensure GPTBot is allowed in robots.txt", "Build brand authority (press coverage, Wikipedia)", "Use structured data and author markup"],
    docLink: "https://platform.openai.com/docs/plugins/bot",
  },
  {
    key: "claude",
    baseKey: "claude",
    probeKey: "claude",
    surface: "chatbots" as const,
    label: "Claude (Anthropic)",
    description: "Claude's web retrieval mode. Relies on ClaudeBot crawl access, transparent authorship, structured content, and high E-E-A-T. Privacy and governance signals carry weight.",
    improvements: ["Allow ClaudeBot in robots.txt", "Add author bios and content governance pages", "Ensure llms.txt is present and well-structured"],
    docLink: "https://support.anthropic.com/en/articles/8896518-does-anthropic-crawl-the-web-and-how-can-site-owners-block-the-crawler",
  },
  {
    key: "perplexity",
    baseKey: "perplexity",
    probeKey: undefined,
    surface: "chatbots" as const,
    label: "Perplexity",
    description: "Perplexity relies on accessible pages, passage-level citability, authoritative sources, and strong entity signals.",
    improvements: ["Allow PerplexityBot", "Improve answer-focused passages", "Build source authority and entity consistency"],
    docLink: "https://docs.perplexity.ai/guides/bots",
  },
  {
    key: "copilot",
    baseKey: "copilot",
    probeKey: undefined,
    surface: "chatbots" as const,
    label: "Microsoft Copilot",
    description: "Copilot readiness depends on Bing discovery, crawl access, structured content, and Microsoft ecosystem signals.",
    improvements: ["Verify Bing indexing and sitemap coverage", "Use IndexNow where appropriate", "Strengthen structured entity signals"],
    docLink: "https://www.bing.com/webmasters/help/webmasters-guidelines-30fba23a",
  },
  {
    key: "google_aio",
    baseKey: "aio",
    probeKey: "google_aio",
    surface: "overviews" as const,
    label: "Google AI Overviews",
    description: "Google's AI-generated answer summaries in Search. Prioritises highly credible, well-structured content with strong topical authority and passage-level citability.",
    improvements: ["Create clear, answer-focused headings", "Use FAQ and HowTo schema", "Build topical depth on key subjects"],
    docLink: "https://support.google.com/websearch/answer/14901683",
  },
] as const;

type ProbePlatformKey = "gemini" | "openai" | "google_aio" | "claude";

const SURFACE_GROUPS: Array<{
  id: PlatformSurface;
  label: string;
  description: string;
  icon: typeof MessageCircle;
  accentClass: string;
}> = [
  {
    id: "chatbots",
    label: "Chatbots",
    description: "Conversational assistants people ask directly — Gemini, ChatGPT, Claude, Perplexity, and Copilot.",
    icon: MessageCircle,
    accentClass: "text-violet-600",
  },
  {
    id: "overviews",
    label: "AI Overviews",
    description: "Search-generated answer summaries shown inside Google results.",
    icon: Search,
    accentClass: "text-red-600",
  },
];

// ── Score computation ──────────────────────────────────────────────────────────

export function computePlatformVisibility(
  ctx: PromptPerformanceContext,
  platform: ProbePlatformKey,
): { brandPct: number; sovPct: number; mentions: number; total: number } {
  const metrics = computeVisibilityMetrics(ctx)?.perPlatform[platform];
  return {
    brandPct: metrics?.visibilityPct ?? 0,
    sovPct: metrics?.sovPct ?? 0,
    mentions: metrics?.visibleResponseCount ?? 0,
    total: metrics?.responseCount ?? 0,
  };
}

// ── Platform card ──────────────────────────────────────────────────────────────

function PlatformCard({
  config,
  probe,
  baseScore,
  baseGap,
  onNavigate,
}: {
  config: (typeof PLATFORM_READINESS_CONFIG)[number];
  probe: { brandPct: number; sovPct: number; mentions: number; total: number } | null;
  baseScore: number | null;
  baseGap?: string;
  onNavigate?: (sectionId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasProbe = Boolean(probe && probe.total > 0);
  const score = hasProbe
    ? Math.min(100, Math.round(0.55 * probe!.brandPct + 0.25 * probe!.sovPct + 0.20 * (baseScore ?? 50)))
    : baseScore == null ? null : Math.round(baseScore);
  const color = score !== null ? scoreColor(scoreTone(score)) : "#9ca3af";

  return (
    <div className="bg-white rounded-2xl border border-gray-200 overflow-hidden">
      <div className="p-5">
        {/* Header row */}
        <div className="flex items-center gap-3 mb-4">
          <div
            className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0"
            style={{ background: `${color}18` }}
          >
            <PlatformLogo platform={config.key} size={22} />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-bold text-[#0d0d0d]">{config.label}</h3>
            <p className="text-[11px] text-gray-400 truncate">{config.description.slice(0, 60)}…</p>
          </div>
          {score !== null ? (
            <div className="text-right shrink-0">
              <p className="text-2xl font-bold leading-none" style={{ color }}>{score}</p>
              <p className="text-[10px] font-semibold" style={{ color }}>{scoreLabel(score)}</p>
              <p className="mt-0.5 text-[9px] text-gray-400">{hasProbe ? "Probe-enhanced" : "Technical baseline"}</p>
            </div>
          ) : (
            <div className="text-right shrink-0">
              <p className="text-sm text-gray-300 font-bold">—</p>
              <p className="text-[10px] text-gray-300">No probes</p>
            </div>
          )}
        </div>

        {/* Score bar */}
        <div className="h-2 bg-gray-100 rounded-full overflow-hidden mb-4">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${score ?? 0}%`, background: color }}
          />
        </div>

        {/* Probe metrics */}
        {hasProbe && probe ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-lg bg-gray-50 px-3 py-2 text-center">
              <p className="text-lg font-bold text-[#0d0d0d]">{Math.round(probe.brandPct)}%</p>
              <p className="text-[10px] text-gray-400">Visibility
                <Tooltip text="% of prompts on this platform where your brand was mentioned in the response." />
              </p>
            </div>
            <div className="rounded-lg bg-gray-50 px-3 py-2 text-center">
              <p className="text-lg font-bold text-[#0d0d0d]">{Math.round(probe.sovPct)}%</p>
              <p className="text-[10px] text-gray-400">SOV
                <Tooltip text="Share of Voice: brand mentions / (brand + mentions of the 10 most-mentioned competitors) for this platform." />
              </p>
            </div>
            <div className="rounded-lg bg-gray-50 px-3 py-2 text-center">
              <p className="text-lg font-bold text-[#0d0d0d]">{probe.mentions}/{probe.total}</p>
              <p className="text-[10px] text-gray-400">Responses</p>
            </div>
            <div className="rounded-lg bg-gray-50 px-3 py-2 text-center">
              <p className="text-lg font-bold text-[#0d0d0d]">{baseScore == null ? "—" : Math.round(baseScore)}</p>
              <p className="text-[10px] text-gray-400">Technical</p>
            </div>
          </div>
        ) : (
          <p className="text-xs text-gray-500">
            No prompt tests are available. The displayed score uses the existing AI platform readiness assessment.
          </p>
        )}
        {baseGap && (
          <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-800">
            <TextWithSampleScriptsLink text={baseGap} onNavigate={onNavigate} />
          </p>
        )}
      </div>

      {/* Expandable improvements */}
      <button
        type="button"
        className="w-full px-5 py-3 bg-gray-50 border-t border-gray-100 text-left text-xs font-semibold text-gray-400 hover:text-gray-600 transition-colors flex items-center justify-between"
        onClick={() => setExpanded(!expanded)}
      >
        <span>Improvement actions</span>
        <span>{expanded ? "▲" : "▼"}</span>
      </button>
      {expanded && (
        <div className="px-5 pb-4 pt-3 border-t border-gray-100">
          <p className="text-xs text-gray-500 leading-relaxed mb-3">
            <TextWithSampleScriptsLink text={config.description} onNavigate={onNavigate} />
          </p>
          <ul className="space-y-1.5">
            {config.improvements.map((imp, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-gray-600">
                <span className="mt-0.5 w-1.5 h-1.5 rounded-full bg-blue-400 shrink-0 mt-1" />
                <TextWithSampleScriptsLink text={imp} onNavigate={onNavigate} />
              </li>
            ))}
          </ul>
          <a
            href={config.docLink}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-3 inline-flex items-center gap-1 text-[11px] text-blue-500 hover:underline"
          >
            Platform documentation
            <ExternalLink className="w-2.5 h-2.5" />
          </a>
        </div>
      )}
    </div>
  );
}

// ── Main export ────────────────────────────────────────────────────────────────

export function PlatformReadinessSection({
  auditDirOrSlug,
  onNavigate,
  breakdown: breakdownProp,
  pageScoped = false,
}: {
  auditDirOrSlug: string;
  onNavigate?: (sectionId: string) => void;
  breakdown?: Awaited<ReturnType<typeof fetchScoreBreakdown>> | null;
  pageScoped?: boolean;
}) {
  const { ctx, loading: ctxLoading } = usePromptPerformanceContext(auditDirOrSlug);
  const [fetched, setFetched] = useState<Awaited<ReturnType<typeof fetchScoreBreakdown>> | null>(null);
  const [extraLoading, setExtraLoading] = useState(!breakdownProp);

  const load = useCallback(() => {
    if (breakdownProp) {
      setExtraLoading(false);
      return;
    }
    setExtraLoading(true);
    fetchScoreBreakdown(auditDirOrSlug)
      .then((b) => setFetched(b))
      .catch(() => setFetched(null))
      .finally(() => setExtraLoading(false));
  }, [auditDirOrSlug, breakdownProp]);

  useEffect(() => { load(); }, [load]);

  const breakdown = breakdownProp ?? fetched;
  const loading = extraLoading || (!pageScoped && ctxLoading);

  if (loading) {
    return <PageLoading />;
  }

  const probeCtx = pageScoped ? null : ctx;
  const hasProbes = !!probeCtx?.live_probe?.per_prompt?.length;
  const baseScores = new Map((breakdown?.platform_readiness ?? []).map((row) => [row.key, row]));

  return (
    <div className="space-y-8">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Platform Readiness</h2>
        <p className="text-sm text-gray-500 mt-1">
          How ready your brand is to be cited across major AI platforms, grouped by how people
          encounter answers: conversational chatbots versus search-generated AI Overviews.
          Readiness is scored using probe visibility data (55%) + share of voice (25%) + the
          existing platform readiness score (20%). Platforms without prompt tests retain their
          existing readiness score.
        </p>
        {!hasProbes && !pageScoped && (
          <div className="mt-3 flex items-center gap-2 text-amber-600 bg-amber-50 rounded-lg px-4 py-2.5 text-sm">
            <Info className="w-4 h-4 shrink-0" />
            <span>Run probes from the <strong>Prompts</strong> section to get probe-based readiness scores.</span>
          </div>
        )}
      </div>

      {SURFACE_GROUPS.map((group) => {
        const Icon = group.icon;
        const platforms = PLATFORM_READINESS_CONFIG.filter((config) => config.surface === group.id);
        return (
          <section key={group.id} className="space-y-3" aria-labelledby={`platform-surface-${group.id}`}>
            <div className="flex items-start gap-3">
              <div className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-50 ${group.accentClass}`}>
                <Icon className="h-4 w-4" />
              </div>
              <div>
                <h3 id={`platform-surface-${group.id}`} className={`text-sm font-bold ${group.accentClass}`}>
                  {group.label}
                </h3>
                <p className="mt-0.5 text-xs text-gray-500">{group.description}</p>
              </div>
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              {platforms.map((config) => (
                <PlatformCard
                  key={config.key}
                  config={config}
                  probe={probeCtx && config.probeKey ? computePlatformVisibility(probeCtx, config.probeKey) : null}
                  baseScore={baseScores.get(config.baseKey)?.score ?? null}
                  baseGap={baseScores.get(config.baseKey)?.gap}
                  onNavigate={onNavigate}
                />
              ))}
            </div>
          </section>
        );
      })}

      {/* Score formula note */}
      <p className="text-[11px] text-gray-300 text-center pb-2">
        Tested platforms: 55% × response visibility + 25% × brand SOV + 20% × existing readiness.
        Untested platforms use the existing AI platform readiness score unchanged.
      </p>
    </div>
  );
}
