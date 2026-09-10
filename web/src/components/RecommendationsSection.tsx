import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  CheckCircle2,
  Eye,
  FileText,
  Wrench,
} from "lucide-react";
import {
  fetchPromptSentiment,
  fetchScoreBreakdown,
} from "../api/client";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import type {
  PromptPerformanceContext,
  PromptSentimentAnalysis,
} from "../types";
import { textMentionsBrand } from "../lib/brandMatch";
import {
  completedPlatformRuns,
  COMPETITOR_PLATFORMS,
} from "./CompetitorComparisonSection";
import {
  computePlatformVisibility,
  PLATFORM_READINESS_CONFIG,
} from "./PlatformReadinessSection";
import { usePageLoadingSignal } from "./PageLoading";
import {
  GOOD_SCORE_MIN,
  formatReportScore,
  isOkOrBelow,
  scoreColor,
  scoreTone,
} from "../lib/reportScore";
import {
  AI_OVERVIEW_PLATFORMS,
  CHATBOT_PLATFORMS,
} from "../lib/visibilityMetrics";
import {
  SAMPLE_SCRIPT_ARTIFACT_RE,
  mentionsSampleScriptArtifact,
} from "../lib/sampleScriptsArtifacts";
import type { VisibilityPlatform } from "../lib/brandVisibilityRows";

type ScoreBreakdown = Awaited<ReturnType<typeof fetchScoreBreakdown>>;
type RecommendationTab = "ai" | "technical" | "content";
type Priority = "High" | "Medium";

/** Platform readiness keys treated as chatbots (probe + crawl-only assistants). */
export const CHATBOT_PLATFORM_KEYS = [
  "gemini",
  "openai",
  "claude",
  "perplexity",
  "copilot",
] as const;
export const AI_OVERVIEW_PLATFORM_KEYS = ["google_aio"] as const;

export { SAMPLE_SCRIPT_ARTIFACT_RE, mentionsSampleScriptArtifact };

export interface RecommendationItem {
  id: string;
  title: string;
  detail: string;
  actions: string[];
  priority: Priority;
  score?: number;
  /** When set, item CTA navigates here (overrides group default for sample scripts). */
  sectionId?: string;
  topic?: string;
}

export type NavigateToReportSection = (
  sectionId: string,
  options?: { topic?: string },
) => void;

interface RecommendationGroup {
  id: string;
  title: string;
  description: string;
  sectionId: string;
  emptyMessage: string;
  items: RecommendationItem[];
}

/**
 * Drop Good/Excellent items and sort lowest score → highest within a category.
 * Items without a score (e.g. crawler mismatches, sentiment) are kept and sorted last.
 */
export function prepareRecommendations(items: RecommendationItem[]): RecommendationItem[] {
  return items
    .filter((item) => item.score == null || isOkOrBelow(item.score))
    .sort((left, right) => {
      const leftScore = left.score ?? Number.POSITIVE_INFINITY;
      const rightScore = right.score ?? Number.POSITIVE_INFINITY;
      return leftScore - rightScore || left.title.localeCompare(right.title);
    });
}

function percentage(value: number, total: number): number {
  return total > 0 ? (value / total) * 100 : 0;
}

function priorityForScore(score: number): Priority {
  return score < 40 ? "High" : "Medium";
}

function findingKey(value: string): string {
  return value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function unique(values: string[]): string[] {
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = findingKey(value);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function promptTopicMap(ctx: PromptPerformanceContext): Map<string, string> {
  const result = new Map<string, string>();
  const rows = ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows;
  for (const row of rows ?? []) {
    const topic = row.product_or_service?.trim() || "Other";
    for (const prompt of row.prompts ?? []) {
      result.set(prompt.trim().toLowerCase(), topic);
    }
  }
  return result;
}

export function buildLowVisibilityTopicRecommendations(
  ctx: PromptPerformanceContext,
  platforms: readonly VisibilityPlatform[] = COMPETITOR_PLATFORMS,
  idPrefix = "topic",
): RecommendationItem[] {
  const rows = ctx.live_probe?.per_prompt ?? [];
  if (!rows.length) return [];
  const topics = promptTopicMap(ctx);
  const configuredRows = ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows;
  const positionalTopics = (configuredRows ?? []).flatMap((row) =>
    (row.prompts ?? []).map(() => row.product_or_service?.trim() || "Other"),
  );
  const totals = new Map<string, { responses: number; mentions: number }>();
  const brandTokens = ctx.live_probe?.brand_match_tokens ?? [];

  rows.forEach((row, index) => {
    const prompt = String(row.prompt ?? "").trim().toLowerCase();
    const topic = topics.get(prompt) ?? positionalTopics[index] ?? "Other";
    const counts = totals.get(topic) ?? { responses: 0, mentions: 0 };
    for (const platform of platforms) {
      for (const run of completedPlatformRuns(row, platform)) {
        counts.responses += 1;
        const visible = Number(run.scores.brand_signal ?? 0) > 0
          || textMentionsBrand(run.response ?? "", ctx.brand_name, brandTokens);
        if (visible) counts.mentions += 1;
      }
    }
    totals.set(topic, counts);
  });

  return prepareRecommendations(
    Array.from(totals, ([topic, counts]) => {
      const visibility = percentage(counts.mentions, counts.responses);
      return {
        topic,
        counts,
        visibility,
      };
    })
      .filter(({ counts, visibility }) =>
        counts.responses > 0 && isOkOrBelow(visibility)
      )
      .map(({ topic, counts, visibility }) => ({
        id: `${idPrefix}-${topic}`,
        title: topic,
        detail: `Your brand appeared in ${counts.mentions} of ${counts.responses} analysed responses for this topic (${Math.round(visibility)}% visibility).`,
        actions: [
          `Create or strengthen content that directly answers the priority questions associated with “${topic}”.`,
          "Use clear question-led headings, concise answer passages, supporting evidence, and internal links to the most relevant commercial pages.",
        ],
        priority: priorityForScore(visibility),
        score: visibility,
        sectionId: "content-outline-generator",
        topic,
      })),
  );
}

export function buildNegativeSentimentRecommendations(
  sentiment: PromptSentimentAnalysis | null,
): RecommendationItem[] {
  return prepareRecommendations(
    (sentiment?.by_category ?? [])
      .filter((row) => row.sentiment.toLowerCase().includes("negative"))
      .map((row) => ({
        id: `sentiment-${row.category}`,
        title: row.category,
        detail: row.summary || "AI responses show negative sentiment for this category.",
        actions: [
          `Review the recurring concerns associated with “${row.category}” and publish content that addresses them directly.`,
          "Support corrective claims with verifiable evidence, transparent limitations, and clear customer guidance.",
        ],
        priority: "High" as const,
      })),
  );
}

export function buildCrawlerRecommendations(
  breakdown: ScoreBreakdown | null,
): RecommendationItem[] {
  const rowItems: RecommendationItem[] = (breakdown?.crawler_access?.rows ?? [])
    .filter((row) => row.aligned === false)
    .map((row) => ({
      id: `crawler-${row.crawler}`,
      title: `${row.recommendation} ${row.crawler}`,
      detail: row.reason,
      actions: [
        row.recommendation === "ALLOW"
          ? `Update robots.txt so ${row.crawler} can fetch the intended public pages.`
          : `Update robots.txt so ${row.crawler} is blocked in line with the recommended policy.`,
        "Re-test the live robots.txt rules after deployment.",
      ],
      priority: Number(row.tier) <= 2 ? ("High" as const) : ("Medium" as const),
      sectionId: "sample-scripts",
    }));
  const policyActions = unique(breakdown?.crawler_access?.improvements ?? []);
  if (policyActions.length) {
    rowItems.push({
      id: "crawler-policy-discovery",
      title: "Crawler policy and discovery",
      detail: "Additional access or discovery findings recorded by the crawler assessment.",
      actions: policyActions,
      priority: "High",
      sectionId: mentionsSampleScriptArtifact(...policyActions)
        ? "sample-scripts"
        : undefined,
    });
  }
  return prepareRecommendations(rowItems);
}

export function buildCitabilityRecommendations(
  breakdown: ScoreBreakdown | null,
): RecommendationItem[] {
  const components = (breakdown?.details?.technical_setup?.components ?? [])
    .filter((component) =>
      ["ai_citability", "ai_search_success", "query_coverage_footprint"].includes(component.key)
    );
  const items: RecommendationItem[] = [];

  for (const component of components) {
    if (component.key === "ai_search_success" && component.criteria?.length) {
      for (const criterion of component.criteria) {
        if (!isOkOrBelow(criterion.score)) continue;
        const actions = unique(criterion.improvements ?? []);
        if (!actions.length) continue;
        items.push({
          id: `citability-${component.key}-${criterion.key}`,
          title: criterion.title,
          detail: `${component.title} criterion, currently ${formatReportScore(criterion.score)}/100.`,
          actions,
          priority: priorityForScore(criterion.score),
          score: criterion.score,
          sectionId: mentionsSampleScriptArtifact(...actions) ? "sample-scripts" : undefined,
        });
      }
      continue;
    }
    if (!isOkOrBelow(component.score)) continue;
    const actions = unique(component.improvements ?? []);
    if (!actions.length) continue;
    items.push({
      id: `citability-${component.key}`,
      title: component.title,
      detail: component.finding_summary || component.detail,
      actions,
      priority: priorityForScore(component.score),
      score: component.score,
      sectionId: mentionsSampleScriptArtifact(...actions) ? "sample-scripts" : undefined,
    });
  }
  return prepareRecommendations(items);
}

export function buildPlatformRecommendations(
  ctx: PromptPerformanceContext | null,
  breakdown: ScoreBreakdown | null,
  platformKeys?: readonly string[],
): RecommendationItem[] {
  const allowed = platformKeys ? new Set(platformKeys) : null;
  const baseScores = new Map((breakdown?.platform_readiness ?? []).map((row) => [row.key, row]));
  return prepareRecommendations(
    PLATFORM_READINESS_CONFIG.flatMap((config) => {
      if (allowed && !allowed.has(config.key)) return [];
      const probe = ctx && config.probeKey
        ? computePlatformVisibility(ctx, config.probeKey)
        : null;
      const base = baseScores.get(config.baseKey);
      const hasProbe = Boolean(probe && probe.total > 0);
      const score = hasProbe
        ? Math.min(
            100,
            Math.round(
              0.55 * probe!.brandPct
              + 0.25 * probe!.sovPct
              + 0.20 * (base?.score ?? 50),
            ),
          )
        : base?.score == null ? null : Math.round(base.score);
      if (score == null || !isOkOrBelow(score)) return [];
      const actions = unique([
        ...(base?.gap ? [base.gap] : []),
        ...config.improvements,
      ]);
      return [{
        id: `platform-${config.key}`,
        title: config.label,
        detail: hasProbe
          ? `${Math.round(probe!.brandPct)}% response visibility and a combined readiness score of ${score}/100.`
          : `${score}/100 technical readiness. Prompt visibility data is not available for this platform.`,
        actions,
        priority: priorityForScore(score),
        score,
        sectionId: mentionsSampleScriptArtifact(...actions) ? "sample-scripts" : undefined,
      }];
    }),
  );
}

function eeatContentActions(name: string): string[] {
  const key = name.toLowerCase();
  if (key.includes("experience")) {
    return [
      "Publish first-hand case studies, worked examples, product tests, and practitioner observations.",
      "Show who performed the work, what happened, and the evidence supporting the outcome.",
    ];
  }
  if (key.includes("expert")) {
    return [
      "Commission expert-authored guides with visible credentials, biographies, and review dates.",
      "Reference reputable primary sources and explain the methodology behind specialist claims.",
    ];
  }
  if (key.includes("author")) {
    return [
      "Publish original research, benchmarks, expert commentary, and resources that other sites can reference.",
      "Strengthen author profiles and earn corroborating mentions from relevant industry sources.",
    ];
  }
  return [
    "Add transparent policies, sources, review dates, contact details, and editorial ownership to priority content.",
    "Keep claims specific, verifiable, and consistent across the site.",
  ];
}

export function buildEeatRecommendations(
  breakdown: ScoreBreakdown | null,
): RecommendationItem[] {
  return prepareRecommendations(
    (breakdown?.content_quality_details?.eeat ?? [])
      .filter((row) => isOkOrBelow(row.score))
      .map((row) => ({
        id: `eeat-${row.name}`,
        title: row.name,
        detail: `${row.what_it_means} Current score: ${formatReportScore(row.score)}/100.`,
        actions: eeatContentActions(row.name),
        priority: priorityForScore(row.score),
        score: row.score,
      })),
  );
}

const STRUCTURE_ACTIONS: Record<string, string[]> = {
  original_information_gain: [
    "Add original research, proprietary data, expert analysis, comparisons, or tested examples that are not available elsewhere.",
    "State the new insight clearly near the top of each priority page.",
  ],
  passage_answerability: [
    "Place concise, self-contained answers immediately below question-led headings.",
    "Define the subject, answer the question, and support the answer within the same passage.",
  ],
  content_formatting: [
    "Use descriptive heading levels, short paragraphs, lists, and comparison tables where they improve comprehension.",
    "Keep each section focused on one intent so AI systems can extract it without surrounding context.",
  ],
};

export function buildStructureRecommendations(
  breakdown: ScoreBreakdown | null,
): RecommendationItem[] {
  return prepareRecommendations(
    (breakdown?.content_quality_details?.structure_answerability ?? [])
      .filter((row) => isOkOrBelow(row.score))
      .map((row) => ({
        id: `structure-${row.key}`,
        title: row.title,
        detail: `${row.description} Current score: ${formatReportScore(row.score)}/100.`,
        actions: STRUCTURE_ACTIONS[row.key] ?? [
          row.empty_message || "Improve this criterion across priority content templates.",
        ],
        priority: priorityForScore(row.score),
        score: row.score,
      })),
  );
}

export function buildSchemaRecommendations(
  breakdown: ScoreBreakdown | null,
): RecommendationItem[] {
  const section = breakdown?.content_quality_details?.schema_entity;
  if (!section?.improvements?.length || !isOkOrBelow(section.score)) return [];
  const actions = unique(section.improvements);
  return prepareRecommendations([{
    id: "schema-entity",
    title: "Schema and entity coverage",
    detail: section.summary,
    actions,
    priority: priorityForScore(section.score),
    score: section.score,
    sectionId: mentionsSampleScriptArtifact(...actions) ? "sample-scripts" : undefined,
  }]);
}

export function buildSampleScriptsRecommendations(
  breakdown: ScoreBreakdown | null,
): RecommendationItem[] {
  if (!breakdown) return [];
  const items: RecommendationItem[] = [];
  const crawlerImprovements = unique(breakdown.crawler_access?.improvements ?? []);
  const crawlerText = crawlerImprovements.join(" ").toLowerCase();
  const hasRobotsMismatch = (breakdown.crawler_access?.rows ?? []).some(
    (row) => row.aligned === false,
  );
  const schemaImprovements = unique(
    breakdown.content_quality_details?.schema_entity?.improvements ?? [],
  );
  const schemaText = schemaImprovements.join(" ").toLowerCase();
  const citabilityImprovements = unique(
    (breakdown.details?.technical_setup?.components ?? [])
      .filter((component) =>
        ["ai_citability", "ai_search_success", "query_coverage_footprint", "ai_crawler_report"]
          .includes(component.key)
      )
      .flatMap((component) => component.improvements ?? []),
  );
  const citabilityText = citabilityImprovements.join(" ").toLowerCase();
  const combined = `${crawlerText} ${schemaText} ${citabilityText}`;

  if (hasRobotsMismatch || /robots\.txt/.test(combined)) {
    const crawlerScore = breakdown.crawler_access?.score;
    items.push({
      id: "sample-robots",
      title: "Implement the sample robots.txt merge",
      detail:
        "Crawler access findings suggest robots.txt still needs policy or discovery updates for AI agents.",
      actions: unique([
        "Open Sample scripts and copy the merged robots.txt suggestion for your origin.",
        "Adjust Allow/Disallow rules to match your AI crawler policy, then re-test after publish.",
        ...crawlerImprovements.slice(0, 2),
      ]),
      priority: hasRobotsMismatch ? "High" : "Medium",
      // Finding-driven: keep the action even when the section score is Good.
      score: typeof crawlerScore === "number" && isOkOrBelow(crawlerScore)
        ? crawlerScore
        : undefined,
      sectionId: "sample-scripts",
    });
  }

  if (/llms\.txt/.test(combined)) {
    items.push({
      id: "sample-llms",
      title: "Publish an llms.txt from the sample skeleton",
      detail:
        "A live llms.txt helps AI systems discover and summarise site structure for citation.",
      actions: unique([
        "Open Sample scripts and adapt the generated llms.txt skeleton to your products and key URLs.",
        "Publish it at the site root (or documented alternate location) and verify it is crawlable.",
        ...crawlerImprovements.filter((item) => /llms\.txt/i.test(item)).slice(0, 2),
      ]),
      priority: "Medium",
      sectionId: "sample-scripts",
    });
  }

  if (schemaImprovements.length || /json-?ld|structured data|schema/.test(combined)) {
    const schemaScore = breakdown.content_quality_details?.schema_entity?.score;
    if (typeof schemaScore !== "number" || isOkOrBelow(schemaScore)) {
      items.push({
        id: "sample-jsonld",
        title: "Apply the sample JSON-LD WebSite markup",
        detail:
          "Structured data samples from this audit illustrate entity markup that improves AI citability.",
        actions: unique([
          "Open Sample scripts and review the WebSite JSON-LD sample for homepage templates.",
          "Extend Organisation / Product schema as needed, then validate on priority templates.",
          ...schemaImprovements.slice(0, 2),
        ]),
        priority: priorityForScore(typeof schemaScore === "number" ? schemaScore : 50),
        score: typeof schemaScore === "number" ? schemaScore : undefined,
        sectionId: "sample-scripts",
      });
    }
  }

  return prepareRecommendations(items);
}

export function buildBrandAuthorityRecommendations(
  breakdown: ScoreBreakdown | null,
): RecommendationItem[] {
  const section = breakdown?.content_quality_details?.brand_visibility_authority;
  if (!section || !isOkOrBelow(section.score)) return [];
  const directActions = unique(section.improvements ?? []);
  const missingPlatformActions = (section.rows ?? [])
    .filter((row) => !row.present && row.platform)
    .map((row) => `Create or complete the official ${row.platform} presence and keep brand details consistent with the website.`);
  const actions = unique([...directActions, ...missingPlatformActions]);
  if (!actions.length) return [];
  return prepareRecommendations([{
    id: "brand-authority",
    title: "Third-party brand authority",
    detail: section.brand_query
      ? `Authority signals assessed for “${section.brand_query}”.`
      : "Third-party sources do not yet provide enough corroborating brand signals.",
    actions,
    priority: priorityForScore(section.score),
    score: section.score,
  }]);
}

function RecommendationScoreBadge({ score }: { score: number }) {
  const color = scoreColor(scoreTone(score));
  return (
    <span
      className="h-fit rounded-full px-2.5 py-1 text-xs font-bold tabular-nums"
      style={{ color, backgroundColor: `${color}1a` }}
    >
      {formatReportScore(score)}/100
    </span>
  );
}

function PriorityBadge({ priority }: { priority: Priority }) {
  return (
    <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
      priority === "High"
        ? "bg-red-50 text-red-700"
        : "bg-amber-50 text-amber-700"
    }`}>
      {priority}
    </span>
  );
}

function RecommendationGroupView({
  group,
  onNavigate,
}: {
  group: RecommendationGroup;
  onNavigate?: NavigateToReportSection;
}) {
  return (
    <section aria-labelledby={`${group.id}-heading`}>
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 id={`${group.id}-heading`} className="text-sm font-bold text-[#0d0d0d]">{group.title}</h3>
          <p className="mt-1 max-w-3xl text-xs leading-relaxed text-gray-500">{group.description}</p>
        </div>
        {onNavigate && (
          <button
            type="button"
            onClick={() => onNavigate(group.sectionId)}
            className="inline-flex items-center gap-1 rounded-md text-xs font-semibold text-blue-600 hover:text-blue-800 focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            {group.sectionId === "sample-scripts" ? "Open sample scripts" : "View source"}{" "}
            <ArrowRight className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        {group.items.length ? (
          <ol className="divide-y divide-gray-100">
            {group.items.map((item, index) => (
              <li key={item.id} className="grid gap-3 px-5 py-4 sm:grid-cols-[2rem_minmax(0,1fr)_auto]">
                <span className="flex h-7 w-7 items-center justify-center rounded-full bg-gray-100 text-xs font-bold text-gray-500">
                  {index + 1}
                </span>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h4 className="text-sm font-semibold text-[#0d0d0d]">{item.title}</h4>
                    <PriorityBadge priority={item.priority} />
                    {onNavigate && item.sectionId === "content-outline-generator" && item.topic && (
                      <button
                        type="button"
                        onClick={() => onNavigate(item.sectionId!, { topic: item.topic })}
                        className="inline-flex items-center gap-1 rounded-md text-[11px] font-semibold text-blue-700 hover:text-blue-900 focus:outline-none focus:ring-2 focus:ring-blue-500"
                      >
                        Build outline <ArrowRight className="h-3 w-3" />
                      </button>
                    )}
                    {onNavigate && item.sectionId === "sample-scripts" && group.sectionId !== "sample-scripts" && (
                      <button
                        type="button"
                        onClick={() => onNavigate("sample-scripts")}
                        className="inline-flex items-center gap-1 rounded-md text-[11px] font-semibold text-violet-700 hover:text-violet-900 focus:outline-none focus:ring-2 focus:ring-violet-400"
                      >
                        Open sample scripts <ArrowRight className="h-3 w-3" />
                      </button>
                    )}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-gray-500">{item.detail}</p>
                  <ul className="mt-3 space-y-1.5">
                    {item.actions.map((action) => {
                      const linkSampleScripts = Boolean(
                        onNavigate && mentionsSampleScriptArtifact(action),
                      );
                      return (
                        <li key={action} className="flex gap-2 text-xs leading-relaxed text-gray-700">
                          <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-500" />
                          <span>
                            {action}
                            {linkSampleScripts && (
                              <>
                                {" "}
                                <button
                                  type="button"
                                  onClick={() => onNavigate!("sample-scripts")}
                                  className="inline font-semibold text-violet-700 underline decoration-violet-300 underline-offset-2 hover:text-violet-900"
                                >
                                  Open sample scripts
                                </button>
                              </>
                            )}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
                {item.score != null && <RecommendationScoreBadge score={item.score} />}
              </li>
            ))}
          </ol>
        ) : (
          <div className="flex items-center gap-3 px-5 py-6 text-sm text-gray-500">
            <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-500" />
            {group.emptyMessage}
          </div>
        )}
      </div>
    </section>
  );
}

function LoadingRecommendations() {
  usePageLoadingSignal(true, "recommendations");
  return (
    <div className="space-y-4" aria-label="Loading recommendations" aria-busy="true">
      <div className="mx-auto mb-2 w-full max-w-[220px]">
        <div className="h-1.5 overflow-hidden rounded-full bg-gray-200/90">
          <div className="page-loading-bar-indeterminate h-full w-1/3 rounded-full bg-[#0984e3]" />
        </div>
      </div>
      <div className="h-9 w-72 animate-pulse rounded-lg bg-gray-200" />
      <div className="h-10 w-full animate-pulse rounded-lg bg-gray-100" />
      {[0, 1, 2].map((index) => (
        <div key={index} className="h-28 animate-pulse rounded-xl border border-gray-100 bg-white" />
      ))}
    </div>
  );
}

const TAB_META = [
  { id: "ai" as const, label: "AI Visibility", icon: Eye },
  { id: "technical" as const, label: "Technical Setup", icon: Wrench },
  { id: "content" as const, label: "Content Quality", icon: FileText },
];

export function RecommendationsSection({
  auditDirOrSlug,
  onNavigate,
  breakdown: breakdownProp,
  pageScoped = false,
}: {
  auditDirOrSlug: string;
  onNavigate?: NavigateToReportSection;
  breakdown?: ScoreBreakdown | null;
  pageScoped?: boolean;
}) {
  const [activeTab, setActiveTab] = useState<RecommendationTab>("ai");
  const {
    ctx,
    loading: ctxLoading,
    ensureScope,
  } = usePromptPerformanceContext(auditDirOrSlug);
  const [fetchedBreakdown, setFetchedBreakdown] = useState<ScoreBreakdown | null>(null);
  const [sentiment, setSentiment] = useState<PromptSentimentAnalysis | null>(null);
  const [extraLoading, setExtraLoading] = useState(!(breakdownProp && pageScoped));

  useEffect(() => {
    if (pageScoped) return;
    void ensureScope({ allLocales: true }).catch(() => {
      // The shared store exposes the fetch error; existing recommendation
      // sections continue to render any already-cached audit evidence.
    });
  }, [ensureScope, pageScoped]);

  useEffect(() => {
    if (breakdownProp && pageScoped) {
      setFetchedBreakdown(breakdownProp);
      setExtraLoading(false);
      return;
    }
    setExtraLoading(true);
    Promise.all([
      breakdownProp
        ? Promise.resolve(breakdownProp)
        : fetchScoreBreakdown(auditDirOrSlug).catch(() => null),
      pageScoped ? Promise.resolve(null) : fetchPromptSentiment(auditDirOrSlug).catch(() => null),
    ]).then(([scores, sentimentResponse]) => {
      setFetchedBreakdown(scores);
      setSentiment(sentimentResponse?.sentiment ?? null);
    }).finally(() => setExtraLoading(false));
  }, [auditDirOrSlug, breakdownProp, pageScoped]);

  const loading = extraLoading || (!pageScoped && ctxLoading);
  const breakdown = breakdownProp ?? fetchedBreakdown;

  const groups = useMemo<Record<RecommendationTab, RecommendationGroup[]>>(() => ({
    ai: pageScoped
      ? [
          {
            id: "page-citability",
            title: "Page citability and citations",
            description: "Extractability and citation findings for this URL from the page audit.",
            sectionId: "citability",
            emptyMessage: "No material citability improvement is recorded for this page.",
            items: buildCitabilityRecommendations(breakdown),
          },
        ]
      : [
          {
            id: "low-visibility-topics-chatbots",
            title: "Low visibility topics — Chatbots",
            description: `Topics at OK or below (${GOOD_SCORE_MIN - 1}/100 or less) across Gemini, ChatGPT, and Claude, ordered from the largest gap.`,
            sectionId: "prompts",
            emptyMessage: ctx
              ? "No tested chatbot topic is at OK or below."
              : "Run prompt probes to identify low-visibility chatbot topics.",
            items: ctx
              ? buildLowVisibilityTopicRecommendations(ctx, CHATBOT_PLATFORMS, "topic-chatbots")
              : [],
          },
          {
            id: "low-visibility-topics-overviews",
            title: "Low visibility topics — AI Overviews",
            description: `Topics at OK or below (${GOOD_SCORE_MIN - 1}/100 or less) in Google AI Overviews, ordered from the largest gap.`,
            sectionId: "prompts",
            emptyMessage: ctx
              ? "No tested AI Overview topic is at OK or below."
              : "Run prompt probes to identify low-visibility AI Overview topics.",
            items: ctx
              ? buildLowVisibilityTopicRecommendations(ctx, AI_OVERVIEW_PLATFORMS, "topic-overviews")
              : [],
          },
          {
            id: "platform-actions-chatbots",
            title: "Low visibility platforms — Chatbots",
            description: `Improvement actions for chatbot platforms scoring OK or below (below ${GOOD_SCORE_MIN}/100).`,
            sectionId: "platform-readiness",
            emptyMessage: "No chatbot platform is currently at OK or below.",
            items: buildPlatformRecommendations(ctx, breakdown, CHATBOT_PLATFORM_KEYS),
          },
          {
            id: "platform-actions-overviews",
            title: "Low visibility platforms — AI Overviews",
            description: `Improvement actions for AI Overview platforms scoring OK or below (below ${GOOD_SCORE_MIN}/100).`,
            sectionId: "platform-readiness",
            emptyMessage: "No AI Overview platform is currently at OK or below.",
            items: buildPlatformRecommendations(ctx, breakdown, AI_OVERVIEW_PLATFORM_KEYS),
          },
          {
            id: "negative-sentiment",
            title: "Negative sentiment categories",
            description: "Categories where AI responses contain concerns or unfavourable brand framing.",
            sectionId: "ai-visibility-overview",
            emptyMessage: sentiment
              ? "No negative sentiment category was detected."
              : "Run sentiment analysis to identify categories that need attention.",
            items: buildNegativeSentimentRecommendations(sentiment),
          },
        ],
    technical: [
      {
        id: "crawler-changes",
        title: "Crawler access changes",
        description: "Robots.txt rules that do not match the recommended access policy. Actions that mention robots.txt or llms.txt link to Sample scripts.",
        sectionId: "crawler-access",
        emptyMessage: "Current crawler access rules align with the recorded recommendations.",
        items: buildCrawlerRecommendations(breakdown),
      },
      {
        id: "citability-work",
        title: "Citability improvements",
        description: "The current What needs work findings from the Citability report.",
        sectionId: "citability",
        emptyMessage: "No material Citability improvement is recorded.",
        items: buildCitabilityRecommendations(breakdown),
      },
      {
        id: "sample-scripts",
        title: "Sample scripts to implement",
        description:
          "When technical findings call for robots.txt, llms.txt, or JSON-LD changes, use the Workshop sample scripts as copy-ready starting points.",
        sectionId: "sample-scripts",
        emptyMessage:
          "No sample-script actions are required from the current technical findings. Open Sample scripts anytime for reference implementations.",
        items: buildSampleScriptsRecommendations(breakdown),
      },
    ],
    content: [
      {
        id: "eeat-content",
        title: "E-E-A-T content required",
        description: `Content formats to strengthen E-E-A-T criteria scoring OK or below (below ${GOOD_SCORE_MIN}/100).`,
        sectionId: "eeat-signals",
        emptyMessage: "No E-E-A-T criterion is at OK or below.",
        items: buildEeatRecommendations(breakdown),
      },
      {
        id: "structure-changes",
        title: "Content structure and answerability",
        description: "Changes that make priority pages more original, extractable, and answer-ready.",
        sectionId: "content-structure-answerability",
        emptyMessage: "No content structure criterion is at OK or below.",
        items: buildStructureRecommendations(breakdown),
      },
      {
        id: "schema-changes",
        title: "Schema and entity markup",
        description: "Structured data changes drawn from the current schema assessment. JSON-LD fixes link to Sample scripts.",
        sectionId: "schema-entity-markup",
        emptyMessage: "No material schema or entity markup change is recorded.",
        items: buildSchemaRecommendations(breakdown),
      },
      {
        id: "brand-actions",
        title: "Brand visibility and authority",
        description: "Actions that improve third-party corroboration of the brand entity.",
        sectionId: "brand-visibility-authority",
        emptyMessage: "No material brand authority action is recorded.",
        items: buildBrandAuthorityRecommendations(breakdown),
      },
    ],
  }), [breakdown, ctx, sentiment, pageScoped]);

  if (loading) return <LoadingRecommendations />;

  const counts = Object.fromEntries(
    Object.entries(groups).map(([tab, tabGroups]) => [
      tab,
      tabGroups.reduce((sum, group) => sum + group.items.length, 0),
    ]),
  ) as Record<RecommendationTab, number>;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Recommendations</h2>
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-gray-500">
          {pageScoped
            ? "Prioritised actions generated from this URL’s page audit. Inherited site-wide findings (crawler access, brand authority) are included when they still apply."
            : "Prioritised actions generated from the latest visibility probes and audit scores. Each recommendation links back to its supporting report section."}
        </p>
      </div>

      <div
        role="tablist"
        aria-label="Recommendation categories"
        className="flex overflow-x-auto border-b border-gray-200"
      >
        {TAB_META.map((tab) => {
          const active = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={`recommendations-panel-${tab.id}`}
              id={`recommendations-tab-${tab.id}`}
              onClick={() => setActiveTab(tab.id)}
              className={`flex shrink-0 items-center gap-2 border-b-2 px-4 py-3 text-xs font-bold uppercase tracking-wide transition-colors focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-500 ${
                active
                  ? "border-blue-600 text-blue-700"
                  : "border-transparent text-gray-400 hover:text-gray-700"
              }`}
            >
              <tab.icon className="h-4 w-4" />
              {tab.label}
              <span className={`rounded-full px-2 py-0.5 text-[10px] ${
                active ? "bg-blue-50 text-blue-700" : "bg-gray-100 text-gray-500"
              }`}>
                {counts[tab.id]}
              </span>
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`recommendations-panel-${activeTab}`}
        aria-labelledby={`recommendations-tab-${activeTab}`}
        className="space-y-8"
      >
        {groups[activeTab].map((group) => (
          <RecommendationGroupView
            key={group.id}
            group={group}
            onNavigate={onNavigate}
          />
        ))}
      </div>
    </div>
  );
}
