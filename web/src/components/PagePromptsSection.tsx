import { useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Trash2 } from "lucide-react";
import { runPageAuditPrompts, suggestPageAuditPrompts } from "../api/client";
import type { PageAuditDetail } from "../types";
import { formatReportScore } from "../lib/reportScore";
import { PromptTable } from "./PromptPerformanceSection";
import { PAGE_PROMPT_LOCALE_KEY, pageAuditPromptContext } from "../lib/pageAuditPromptContext";
import { activeProbePlatforms } from "../lib/probePlatforms";
import { computeVisibilityMetrics } from "../lib/visibilityMetrics";
import SovVisibilityScatter, {
  platformSovVisibilityPoints,
  promptSovVisibilityPoints,
} from "./SovVisibilityScatter";

type VisibilityMetrics = {
  score?: number;
  visibility_pct?: number;
  sov_pct?: number;
  sov_performance_score?: number;
  response_count?: number;
  visible_response_count?: number;
  per_platform?: Record<
    string,
    {
      response_count?: number;
      visibility_pct?: number;
      sov_pct?: number;
    }
  >;
};

const PROBE_ACTIVE = new Set(["queued", "starting", "running"]);
const MAX_PAGE_PROMPTS = 25;

export function PagePromptsSection({
  parentId,
  pageId,
  audit,
  onRefresh,
}: {
  parentId: string;
  pageId: string;
  audit: PageAuditDetail;
  onRefresh: () => Promise<void> | void;
}) {
  const saved = audit.page_prompts?.prompts ?? [];
  const [prompts, setPrompts] = useState<string[]>(saved.length ? saved : [""]);
  const [busy, setBusy] = useState<"suggest" | "run" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const next = audit.page_prompts?.prompts ?? [];
    if (next.length) setPrompts(next);
  }, [audit.page_prompts?.generated_at, audit.page_prompts?.updated_at]);

  const probeActive = PROBE_ACTIVE.has(String(audit.probe_status || ""));
  const { ctx, live } = pageAuditPromptContext(audit);
  const rows = live?.per_prompt ?? [];
  const visibilityMetrics = live ? computeVisibilityMetrics(ctx) : null;
  const platformChartPoints = platformSovVisibilityPoints(visibilityMetrics);
  const promptChartPoints = promptSovVisibilityPoints(ctx);
  const done = audit.status === "done";
  const promptMetrics = (audit.page_probe as { prompt_metrics?: VisibilityMetrics } | null | undefined)
    ?.prompt_metrics;
  const surfaces =
    (audit.page_probe as { surface_metrics?: Record<string, VisibilityMetrics | null> } | null | undefined)
      ?.surface_metrics ?? {};
  const platformMetrics =
    (audit.page_probe as { platform_metrics?: Record<string, VisibilityMetrics | null> } | null | undefined)
      ?.platform_metrics ?? {};
  const chatbotMetrics = surfaces.chatbots;
  const overviewMetrics = surfaces.overviews;
  const surfaceGap =
    Number(chatbotMetrics?.response_count || 0) > 0 &&
    Number(overviewMetrics?.response_count || 0) > 0
      ? Math.abs(Number(chatbotMetrics?.score || 0) - Number(overviewMetrics?.score || 0))
      : 0;
  const progress = audit.probe_progress;
  const usable = useMemo(
    () => prompts.map((item) => item.trim()).filter((item) => item.length >= 8),
    [prompts],
  );

  async function handleGenerate() {
    setError(null);
    setBusy("suggest");
    try {
      const payload = await suggestPageAuditPrompts(parentId, pageId);
      const next = (payload.prompts || []).filter(Boolean);
      setPrompts(next.length ? next : [""]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not generate prompts");
    } finally {
      setBusy(null);
    }
  }

  async function handleRun() {
    if (!usable.length) {
      setError("Enter at least one prompt");
      return;
    }
    setError(null);
    setBusy("run");
    try {
      await runPageAuditPrompts(parentId, pageId, usable);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start page probes");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5">
      <h2 className="text-xl font-bold text-[#0d0d0d]">Page prompts</h2>
      <p className="mt-1 text-sm text-gray-500">
        Questions are generated automatically from this URL’s title, headings, structured data,
        and copy, then tested before the page audit can finish. Results stay page-specific and
        are not mixed into site-wide prompt performance.
      </p>

      {probeActive || audit.status === "running" ? (
        <p className="mt-4 inline-flex items-center gap-2 text-sm text-gray-600">
          <Loader2 className="h-4 w-4 animate-spin" />
          {audit.stage === "generating_prompts"
            ? "Generating prompts from this page…"
            : "Running required page prompts…"}
          {typeof progress?.completed_calls === "number" &&
          typeof progress?.planned_calls === "number"
            ? ` ${progress.completed_calls}/${progress.planned_calls} calls`
            : ""}
        </p>
      ) : null}

      {saved.length ? (
        <>
          {done ? (
          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-2"
              onClick={() => void handleGenerate()}
              disabled={busy !== null || probeActive}
            >
              {busy === "suggest" ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Regenerate prompts
            </button>
            <button
              type="button"
              className="btn-primary inline-flex items-center gap-2"
              onClick={() => void handleRun()}
              disabled={busy !== null || probeActive || !usable.length}
            >
              {busy === "run" || probeActive ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Run probes again
            </button>
          </div>
          ) : null}

          {error || audit.probe_error ? (
            <p className="alert-error mt-3">{error || audit.probe_error}</p>
          ) : null}

          <ul className="mt-4 space-y-2">
            {prompts.map((prompt, index) => (
              <li key={index} className="flex gap-2">
                <textarea
                  value={prompt}
                  rows={2}
                  onChange={(event) => {
                    const next = [...prompts];
                    next[index] = event.target.value;
                    setPrompts(next);
                  }}
                  className="min-h-[2.5rem] w-full rounded-lg border border-gray-200 px-3 py-2 text-sm"
                  placeholder="Question a user would ask an AI assistant…"
                  disabled={!done || probeActive}
                />
                <button
                  type="button"
                  className="shrink-0 rounded-lg border border-gray-200 p-2 text-gray-500 hover:text-red-700"
                  aria-label="Remove prompt"
                  onClick={() => setPrompts(prompts.filter((_, i) => i !== index))}
                  disabled={!done || probeActive || prompts.length <= 1}
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            className="mt-2 inline-flex items-center gap-1 text-xs font-semibold text-violet-700"
            onClick={() => setPrompts([...prompts, ""])}
            disabled={!done || probeActive || prompts.length >= MAX_PAGE_PROMPTS}
          >
            <Plus className="h-3.5 w-3.5" />
            Add prompt
          </button>
        </>
      ) : audit.status === "error" ? (
        <p className="mt-4 text-sm text-red-700">Required page prompts could not be generated.</p>
      ) : null}

      {promptMetrics ? (
        <div className="mt-6 space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            {[
              {
                key: "chatbots",
                label: "Chatbots",
                description: "Gemini, ChatGPT, and Claude",
                color: "#8B5CF6",
              },
              {
                key: "overviews",
                label: "AI Overviews",
                description: "Google AI Overviews",
                color: "#EA4335",
              },
            ].map((surface) => {
              const metrics = surfaces[surface.key];
              return (
                <div key={surface.key} className="rounded-xl border border-gray-200 p-4">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: surface.color }}
                    />
                    <h3 className="font-semibold text-gray-900">{surface.label}</h3>
                  </div>
                  <p className="mt-1 text-xs text-gray-400">{surface.description}</p>
                  {metrics && Number(metrics.response_count || 0) > 0 ? (
                    <dl className="mt-3 grid grid-cols-3 gap-3">
                      <div>
                        <dt className="text-[10px] uppercase tracking-wide text-gray-400">Score</dt>
                        <dd className="text-xl font-bold">
                          {formatReportScore(Number(metrics.score || 0))}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[10px] uppercase tracking-wide text-gray-400">
                          Visibility
                        </dt>
                        <dd className="text-xl font-bold">
                          {Math.round(Number(metrics.visibility_pct || 0))}%
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[10px] uppercase tracking-wide text-gray-400">SOV</dt>
                        <dd className="text-xl font-bold">
                          {Math.round(Number(metrics.sov_pct || 0))}%
                        </dd>
                      </div>
                    </dl>
                  ) : (
                    <p className="mt-3 text-sm text-gray-400">No completed responses.</p>
                  )}
                </div>
              );
            })}
          </div>
          {surfaceGap >= 15 ? (
            <p className="rounded-lg border border-violet-100 bg-violet-50 px-3 py-2 text-xs text-gray-700">
              Chatbots score {formatReportScore(Number(chatbotMetrics?.score || 0))} vs AI
              Overviews {formatReportScore(Number(overviewMetrics?.score || 0))} — this page has
              materially different visibility across the two answer surfaces.
            </p>
          ) : null}

          <div className="rounded-xl border border-gray-200 p-4">
            <h3 className="text-sm font-semibold text-gray-900">AI visibility calculation</h3>
            <p className="mt-1 text-xs text-gray-500">
              Same model as the main report: 60% response visibility + 40% relative share of
              voice.
            </p>
            <dl className="mt-3 grid gap-3 sm:grid-cols-3">
              <div>
                <dt className="text-[10px] uppercase tracking-wide text-gray-400">AI visibility</dt>
                <dd className="text-xl font-bold">
                  {formatReportScore(Number(promptMetrics.score || 0))}
                </dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase tracking-wide text-gray-400">
                  Brand visibility
                </dt>
                <dd className="text-xl font-bold">
                  {Math.round(Number(promptMetrics.visibility_pct || 0))}%
                </dd>
              </div>
              <div>
                <dt className="text-[10px] uppercase tracking-wide text-gray-400">
                  Relative SOV score
                </dt>
                <dd className="text-xl font-bold">
                  {formatReportScore(Number(promptMetrics.sov_performance_score || 0))}
                </dd>
              </div>
            </dl>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-gray-900">Platform breakdown</h3>
            <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
              {[
                ["gemini", "Gemini"],
                ["openai", "ChatGPT"],
                ["claude", "Claude"],
                ["google_aio", "Google AI Overview"],
              ].map(([key, label]) => {
                const metrics = platformMetrics[key];
                const hasData = Number(metrics?.response_count || 0) > 0;
                return (
                  <div key={key} className="rounded-lg border border-gray-200 p-3">
                    <p className="text-xs font-semibold text-gray-700">{label}</p>
                    {hasData && metrics ? (
                      <>
                        <p className="mt-1 text-lg font-bold">
                          {formatReportScore(Number(metrics.score || 0))}
                          <span className="text-xs font-normal text-gray-400">/100</span>
                        </p>
                        <p className="text-[11px] text-gray-400">
                          {Math.round(Number(metrics.visibility_pct || 0))}% visibility ·{" "}
                          {Math.round(Number(metrics.sov_pct || 0))}% SOV
                        </p>
                      </>
                    ) : (
                      <p className="mt-2 text-xs text-gray-400">Not tested</p>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      ) : null}

      {live && rows.length ? (
        <div className="mt-8 space-y-8">
          <section aria-labelledby="page-prompt-charts-heading" className="space-y-4">
            <div>
              <h3
                id="page-prompt-charts-heading"
                className="text-sm font-semibold text-[#0d0d0d]"
              >
                Prompt visibility charts
              </h3>
              <p className="mt-1 max-w-3xl text-xs text-gray-500">
                Visibility and share of voice calculated only from this page’s saved prompt
                responses.
              </p>
            </div>
            <SovVisibilityScatter
              title="SOV vs Visibility: Platforms"
              subtitle="This page’s brand performance by responding platform"
              dimensionLabel="Platform"
              points={platformChartPoints}
              emptyMessage="No completed platform responses are available yet."
            />
            <SovVisibilityScatter
              title="SOV vs Visibility: Prompts"
              subtitle="Each point represents one prompt tested for this page"
              dimensionLabel="Prompt"
              points={promptChartPoints}
              emptyMessage="No completed prompt responses are available yet."
            />
          </section>

          <PromptTable
            auditSlug={parentId}
            ctx={ctx}
            live={live}
            brandLabel={ctx.brand_name?.trim() || "Brand"}
            brandMatchTokens={ctx.highlight.brand_match_tokens ?? []}
            activePlatforms={activeProbePlatforms(live)}
            localeKey={PAGE_PROMPT_LOCALE_KEY}
            enableTagging={false}
            enableSentimentFetch={false}
            enableAddPrompt={false}
          />
        </div>
      ) : null}
    </div>
  );
}
