#!/usr/bin/env python3
"""Write web/src/components/PromptPerformanceSection.tsx (full file)."""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "web/src/components/PromptPerformanceSection.tsx"
PLACEHOLDER = "XXTAGXX"


def fix_tags(s: str) -> str:
    return s.replace(PLACEHOLDER, "motion").replace("motion", "motion").replace("motion", "motion")


# Use PLACEHOLDER for HTML tag name, then replace with div
def fix_tags(s: str) -> str:
    return s.replace(PLACEHOLDER, "motion").replace("motion", "div")


# Actually: placeholder MOTAG then replace
PLACEHOLDER = "MOTAG"


def fix_tags(s: str) -> str:
    return s.replace(PLACEHOLDER, "motion").replace("motion", "div")


content = r'''import { useCallback, useEffect, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import {
  fetchPromptPerformanceContext,
  highlightPromptReply,
  runPromptPerformanceProbes,
  trackPromptCompetitor,
} from "../api/client";
import type {
  LiveProbePerPrompt,
  LiveProbeResult,
  PromptPerformanceContext,
  PromptPerformancePssRow,
} from "../types";
import { Card, CardDescription, CardTitle } from "./ui/Card";

const SOV_GREEN = "#00b894";
const SOV_BLUE = "#0984e3";
const SOV_PURPLE = "#7c3aed";

function slugWidget(s: string, maxLen = 48): string {
  const x = s
    .trim()
    .replace(/[^a-zA-Z0-9]+/g, "_")
    .slice(0, maxLen)
    .replace(/^_|_$/g, "")
    .toLowerCase();
  return x || "x";
}

function meanBrandVisibility(rows: LiveProbePerPrompt[]): number {
  if (!rows.length) return 0;
  const vals = rows.map((r) => {
    const g = Number(r.gemini_brand_mention_pct ?? 0);
    const o = Number(r.openai_brand_mention_pct ?? 0);
    const c = Number(r.claude_brand_mention_pct ?? 0);
    const hasC = r.claude_brand_mention_pct != null || r.claude_response;
    return hasC ? (g + o + c) / 3 : (g + o) / 2;
  });
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function meanCompetitorVisibility(rows: LiveProbePerPrompt[]): number {
  if (!rows.length) return 0;
  const vals = rows.map((r) => {
    const g = Number(r.gemini_competitor_mention_pct ?? 0);
    const o = Number(r.openai_competitor_mention_pct ?? 0);
    const c = Number(r.claude_competitor_mention_pct ?? 0);
    const hasC = r.claude_competitor_mention_pct != null || r.claude_response;
    return hasC ? (g + o + c) / 3 : (g + o) / 2;
  });
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function promptsScannedLabel(rows: LiveProbePerPrompt[], expected: number): string {
  let ok = 0;
  for (const r of rows) {
    const ge = String(r.error_gemini ?? "").trim();
    const oe = String(r.error_openai ?? "").trim();
    const ce = String(r.error_claude ?? "").trim();
    if (!ge || !oe || !ce) ok += 1;
  }
  return `${ok} / ${Math.max(expected, 0)}`;
}

function splitLiveByProduct(
  pssRows: PromptPerformancePssRow[],
  live: LiveProbeResult,
): Record<string, LiveProbePerPrompt[]> {
  const flat: string[] = [];
  const meta: { product_or_service: string }[] = [];
  for (const r of pssRows) {
    const pos = r.product_or_service;
    for (const p of r.prompts) {
      const s = p.trim();
      if (s) {
        flat.push(s);
        meta.push({ product_or_service: pos });
      }
    }
  }
  const per = (live.per_prompt ?? []).filter(Boolean) as LiveProbePerPrompt[];
  const out: Record<string, LiveProbePerPrompt[]> = {};
  for (let i = 0; i < flat.length; i++) {
    if (i >= per.length) break;
    const prod = meta[i].product_or_service;
    if (!out[prod]) out[prod] = [];
    out[prod].push(per[i]);
  }
  return out;
}

function SovBar({ brandPct, compPct }: { brandPct: number; compPct: number }) {
  const b = Math.max(0, Math.min(100, brandPct));
  const c = Math.max(0, Math.min(100, compPct));
  const t = b + c + 1e-9;
  const bw = (100 * b) / t;
  return (
    <MOTAG className="mt-2">
      <MOTAG className="flex h-3.5 rounded-lg overflow-hidden border border-gray-200">
        <MOTAG style={{ width: `${bw}%`, minWidth: 2, background: SOV_GREEN }} />
        <MOTAG className="flex-1" style={{ background: SOV_BLUE }} />
      </MOTAG>
      <MOTAG className="flex justify-between text-xs text-gray-500 mt-1.5">
        <span>Brand {b.toFixed(1)}%</span>
        <span>Competitors {c.toFixed(1)}%</span>
      </MOTAG>
    </MOTAG>
  );
}

function MetricCard({
  tone,
  label,
  value,
  sub,
}: {
  tone: "green" | "blue" | "purple";
  label: string;
  value: string;
  sub?: string;
}) {
  const top = tone === "green" ? SOV_GREEN : tone === "purple" ? SOV_PURPLE : SOV_BLUE;
  return (
    <MOTAG
      className="rounded-xl border border-gray-200/80 bg-white p-4 shadow-sm"
      style={{ borderTopWidth: 4, borderTopColor: top }}
    >
      <p className="text-xs font-medium text-gray-500 uppercase tracking-wide">{label}</p>
      <p className="text-xl font-semibold text-brand-dark mt-1 break-words">{value}</p>
      {sub ? <p className="text-xs text-gray-500 mt-2">{sub}</p> : null}
    </MOTAG>
  );
}

function ReplyHighlight({
  auditSlug,
  text,
  enabled,
}: {
  auditSlug: string;
  text: string;
  enabled: boolean;
}) {
  const [html, setHtml] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!enabled || !text.trim()) {
      setHtml(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    highlightPromptReply(auditSlug, text)
      .then((r) => {
        if (!cancelled) setHtml(r.html);
      })
      .catch(() => {
        if (!cancelled) setHtml(null);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [auditSlug, text, enabled]);

  if (loading) {
    return (
      <MOTAG className="flex items-center gap-2 text-sm text-gray-500 py-4">
        <Loader2 className="w-4 h-4 animate-spin" />
        Loading reply…
      </MOTAG>
    );
  }
  if (html) {
    return (
      <MOTAG
        className="prompt-reply-html text-sm"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }
  return (
    <pre className="text-sm whitespace-pre-wrap border border-gray-200 rounded-lg p-3 max-h-96 overflow-auto bg-gray-50">
      {text || "(empty response)"}
    </pre>
  );
}

function PromptReplyBlock({
  auditSlug,
  row,
}: {
  auditSlug: string;
  row: LiveProbePerPrompt;
}) {
  const [model, setModel] = useState<"Gemini" | "OpenAI" | "Claude">("Gemini");
  const isGemini = model === "Gemini";
  const isClaude = model === "Claude";
  const brandPct = isGemini
    ? Number(row.gemini_brand_mention_pct ?? 0)
    : isClaude
      ? Number(row.claude_brand_mention_pct ?? 0)
      : Number(row.openai_brand_mention_pct ?? 0);
  const compPct = isGemini
    ? Number(row.gemini_competitor_mention_pct ?? 0)
    : isClaude
      ? Number(row.claude_competitor_mention_pct ?? 0)
      : Number(row.openai_competitor_mention_pct ?? 0);
  const err = isGemini ? row.error_gemini : isClaude ? row.error_claude : row.error_openai;
  const body = isGemini ? row.gemini_response : isClaude ? row.claude_response : row.openai_response;
  const detail = isGemini
    ? row.mention_scores_gemini?.competitor_detail
    : isClaude
      ? row.mention_scores_claude?.competitor_detail
      : row.mention_scores_openai?.competitor_detail;

  return (
    <MOTAG className="space-y-3">
      <MOTAG className="flex flex-wrap items-center gap-3">
        <label className="text-sm font-medium text-gray-700">Model</label>
        <select
          className="input-field max-w-[180px] py-2 text-sm"
          value={model}
          onChange={(e) => setModel(e.target.value as "Gemini" | "OpenAI" | "Claude")}
        >
          <option>Gemini</option>
          <option>OpenAI</option>
          <option>Claude</option>
        </select>
      </MOTAG>
      <MOTAG className="grid grid-cols-2 gap-4 max-w-md">
        <MOTAG>
          <p className="text-xs text-gray-500">Brand mention %</p>
          <p className="text-lg font-semibold">{brandPct.toFixed(1)}%</p>
        </MOTAG>
        <MOTAG>
          <p className="text-xs text-gray-500">Competitors mention %</p>
          <p className="text-lg font-semibold">{compPct.toFixed(1)}%</p>
        </MOTAG>
      </MOTAG>
      {detail && Object.keys(detail).length > 0 ? (
        <p className="text-xs text-gray-500">
          Competitor hits:{" "}
          {Object.entries(detail)
            .sort(([a], [b]) => a.localeCompare(b))
            .slice(0, 10)
            .map(([k, v]) => `${k}: ${v}`)
            .join(", ")}
        </p>
      ) : null}
      {err ? (
        <MOTAG className="alert-error text-sm">{err}</MOTAG>
      ) : (
        <ReplyHighlight auditSlug={auditSlug} text={String(body ?? "")} enabled />
      )}
    </MOTAG>
  );
}

function ProductGroup({
  auditSlug,
  gi,
  row,
  liveRows,
}: {
  auditSlug: string;
  gi: number;
  row: PromptPerformancePssRow;
  liveRows: LiveProbePerPrompt[];
}) {
  const [open, setOpen] = useState(gi === 0);
  const [metric, setMetric] = useState<"brand" | "competitor" | "scanned">("brand");
  const label = row.product_or_service;
  const flatP = row.prompts.map((p) => p.trim()).filter(Boolean);
  const expN = flatP.length;

  return (
    <MOTAG className="border border-gray-200 rounded-xl bg-white/90 overflow-hidden mb-3">
      <button
        type="button"
        className="w-full flex items-center gap-2 px-4 py-3 text-left font-medium text-brand-dark hover:bg-gray-50"
        onClick={() => setOpen((o) => !o)}
      >
        {open ? (
          <ChevronDown className="w-4 h-4 shrink-0" />
        ) : (
          <ChevronRight className="w-4 h-4 shrink-0" />
        )}
        {label} — {expN} prompt(s)
      </button>
      {open ? (
        <MOTAG className="px-4 pb-4 border-t border-gray-100">
          <MOTAG className="mt-3 flex flex-wrap items-center gap-2">
            <label className="text-sm text-gray-600">Summary</label>
            <select
              className="input-field max-w-xs py-2 text-sm"
              value={metric}
              onChange={(e) =>
                setMetric(e.target.value as "brand" | "competitor" | "scanned")
              }
            >
              <option value="brand">Brand visibility %</option>
              <option value="competitor">Avg competitor visibility %</option>
              <option value="scanned">Prompts scanned</option>
            </select>
          </MOTAG>
          <p className="text-2xl font-semibold mt-2">
            {metric === "brand"
              ? `${meanBrandVisibility(liveRows).toFixed(1)}%`
              : metric === "competitor"
                ? `${meanCompetitorVisibility(liveRows).toFixed(1)}%`
                : promptsScannedLabel(liveRows, expN)}
          </p>
          <p className="text-xs text-gray-500 mb-4">
            {metric === "brand"
              ? "Brand visibility (Gemini + OpenAI + Claude average)"
              : metric === "competitor"
                ? "Average competitor visibility (combined models)"
                : "Prompts scanned (replies without full failure)"}
          </p>
          {flatP.map((pq, pi) => {
            const lr = liveRows[pi];
            return (
              <MOTAG key={`${label}-${pi}`}>
                <hr className="my-4 border-gray-100" />
                <p className="text-sm font-medium text-brand-dark mb-2">
                  <span className="text-gray-500">Prompt:</span> {pq}
                </p>
                {!lr ? (
                  <p className="text-sm text-gray-500">
                    No live probe row for this prompt—re-run probes after changing products.
                  </p>
                ) : (
                  <>
                    <p className="text-xs text-gray-500 mb-3">
                      Brand visibility — Gemini {Number(lr.gemini_brand_mention_pct ?? 0).toFixed(1)}% · OpenAI{" "}
                      {Number(lr.openai_brand_mention_pct ?? 0).toFixed(1)}%
                      {lr.claude_brand_mention_pct != null || lr.claude_response
                        ? ` · Claude ${Number(lr.claude_brand_mention_pct ?? 0).toFixed(1)}%`
                        : ""}
                      {" "}(competitors — Gemini{" "}
                      {Number(lr.gemini_competitor_mention_pct ?? 0).toFixed(1)}% · OpenAI{" "}
                      {Number(lr.openai_competitor_mention_pct ?? 0).toFixed(1)}%
                      {lr.claude_competitor_mention_pct != null || lr.claude_response
                        ? ` · Claude ${Number(lr.claude_competitor_mention_pct ?? 0).toFixed(1)}%`
                        : ""}
                      )
                    </p>
                    <PromptReplyBlock auditSlug={auditSlug} row={lr} />
                  </>
                )}
              </MOTAG>
            );
          })}
        </MOTAG>
      ) : null}
    </MOTAG>
  );
}

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
    <MOTAG className="mt-8">
      <h4 className="text-base font-semibold text-brand-dark mb-1">
        Competitors found from SOV analysis
      </h4>
      <p className="text-sm text-gray-600 mb-4">
        Brands inferred from assistant reply excerpts across all probed models. Track competitor saves
        the row to this audit&apos;s competitor list.
      </p>
      <ul className="space-y-3">
        {rows.map((r, i) => (
          <li
            key={`${r.website_url}-${i}`}
            className="flex flex-wrap items-center gap-3 p-3 rounded-lg border border-gray-200 bg-white"
          >
            <MOTAG className="flex-1 min-w-[140px]">
              <p className="font-medium text-brand-dark">{r.brand_name}</p>
              {r.website_url ? (
                <p className="text-sm text-gray-500 break-all">{r.website_url}</p>
              ) : (
                <p className="text-xs text-gray-400">No homepage URL</p>
              )}
            </MOTAG>
            {r.website_url ? (
              <button
                type="button"
                className="btn-secondary py-2 px-4 text-sm"
                disabled={tracking === r.website_url}
                onClick={async () => {
                  setTracking(r.website_url);
                  try {
                    await trackPromptCompetitor(auditSlug, r.website_url, r.brand_name);
                    onTracked();
                  } catch (e) {
                    alert(e instanceof Error ? e.message : "Could not track competitor");
                  } finally {
                    setTracking(null);
                  }
                }}
              >
                {tracking === r.website_url ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  "Track competitor"
                )}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </MOTAG>
  );
}

function SovAnalysis({
  auditSlug,
  live,
  ctx,
  showSov,
  onRevealSov,
  onTracked,
}: {
  auditSlug: string;
  live: LiveProbeResult;
  ctx: PromptPerformanceContext;
  showSov: boolean;
  onRevealSov: () => void;
  onTracked: () => void;
}) {
  const agg = live.aggregate ?? {};
  const gm = agg.gemini ?? {};
  const oa = agg.openai ?? {};
  const cl = agg.claude ?? {};
  const gBp = Number(gm.brand_share_pct ?? 0);
  const gCp = Number(gm.competitor_share_pct ?? 0);
  const oBp = Number(oa.brand_share_pct ?? 0);
  const oCp = Number(oa.competitor_share_pct ?? 0);
  const cBp = Number(cl.brand_share_pct ?? 0);
  const cCp = Number(cl.competitor_share_pct ?? 0);
  const hasClaude = Boolean(agg.claude);

  const byProd = useMemo(
    () => (ctx.use_pss ? splitLiveByProduct(ctx.pss_rows, live) : {}),
    [ctx.use_pss, ctx.pss_rows, live],
  );

  if (!showSov) {
    return (
      <MOTAG className="mt-6">
        <h4 className="text-base font-semibold text-brand-dark mb-1">Share of voice (SOV)</h4>
        <p className="text-sm text-gray-600 mb-4">
          Probes are done. Open SOV to load mention bars for Gemini, OpenAI, and Claude, per-prompt
          replies, and the competitors table (with Track competitor).
        </p>
        <button type="button" className="btn-primary" onClick={onRevealSov}>
          Show SOV analysis & detected competitors
        </button>
      </MOTAG>
    );
  }

  return (
    <MOTAG className="mt-6 space-y-6">
      <MOTAG>
        <h4 className="text-base font-semibold text-brand-dark mb-1">
          Share of voice from live replies (all prompts combined)
        </h4>
        {live.disclaimer ? (
          <p className="text-xs text-gray-500 mb-4">{live.disclaimer}</p>
        ) : null}
        <MOTAG className={`grid gap-4 ${hasClaude ? "md:grid-cols-3" : "md:grid-cols-2"}`}>
          <MOTAG>
            <MetricCard
              tone="green"
              label="Gemini — brand share"
              value={`${gBp.toFixed(1)}%`}
              sub="Brand vs competitor mention share in Gemini replies"
            />
            <SovBar brandPct={gBp} compPct={gCp} />
          </MOTAG>
          <MOTAG>
            <MetricCard
              tone="blue"
              label="OpenAI — brand share"
              value={`${oBp.toFixed(1)}%`}
              sub="Brand vs competitor mention share in OpenAI replies"
            />
            <SovBar brandPct={oBp} compPct={oCp} />
          </MOTAG>
          {hasClaude ? (
            <MOTAG>
              <MetricCard
                tone="purple"
                label="Claude — brand share"
                value={`${cBp.toFixed(1)}%`}
                sub="Brand vs competitor mention share in Claude replies"
              />
              <SovBar brandPct={cBp} compPct={cCp} />
            </MOTAG>
          ) : null}
        </MOTAG>
      </MOTAG>

      {ctx.use_pss ? (
        <MOTAG>
          <h4 className="text-base font-semibold text-brand-dark mb-3">By product or service</h4>
          {ctx.pss_rows.map((row, gi) => (
            <ProductGroup
              key={slugWidget(`${gi}_${row.product_or_service}`)}
              auditSlug={auditSlug}
              gi={gi}
              row={row}
              liveRows={byProd[row.product_or_service] ?? []}
            />
          ))}
        </MOTAG>
      ) : (
        <MOTAG>
          <h4 className="text-base font-semibold text-brand-dark mb-3">
            Per-prompt answers & mention split
          </h4>
          {(live.per_prompt ?? []).map((row) => {
            if (!row?.prompt) return null;
            return (
              <MOTAG key={row.index} className="mb-8 pb-6 border-b border-gray-100 last:border-0">
                <p className="font-medium mb-3">
                  {row.index}. {row.prompt}
                </p>
                <PromptReplyBlock auditSlug={auditSlug} row={row} />
              </MOTAG>
            );
          })}
        </MOTAG>
      )}

      {live.reply_detected_brands_error && !(live.reply_detected_brands?.length) ? (
        <p className="text-xs text-gray-500">
          Reply brand detection: {String(live.reply_detected_brands_error).slice(0, 220)}
        </p>
      ) : null}

      <DetectedCompetitorsTable auditSlug={auditSlug} live={live} onTracked={onTracked} />
    </MOTAG>
  );
}

export function PromptPerformanceSection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const [ctx, setCtx] = useState<PromptPerformanceContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [showSov, setShowSov] = useState(false);
  const [probeMsg, setProbeMsg] = useState<string | null>(null);

  const slug = auditDirOrSlug;

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchPromptPerformanceContext(slug)
      .then((data) => {
        setCtx(data);
        if (data.live_probe?.per_prompt?.length) {
          setShowSov(false);
        }
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    load();
  }, [load]);

  const live = ctx?.live_probe ?? null;
  const hasProbes = Boolean(live?.per_prompt?.length);

  const runProbes = async () => {
    setProbing(true);
    setProbeMsg(null);
    setShowSov(false);
    try {
      const res = await runPromptPerformanceProbes(slug, true);
      setCtx((prev) =>
        prev
          ? {
              ...prev,
              live_probe: res.live_probe,
              highlight: res.highlight,
            }
          : prev,
      );
      setProbeMsg("Live probes complete—open SOV analysis below.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Probe run failed");
    } finally {
      setProbing(false);
    }
  };

  if (loading) {
    return (
      <MOTAG className="flex justify-center py-16">
        <Loader2 className="w-8 h-8 animate-spin text-brand-accent" />
      </MOTAG>
    );
  }

  if (error && !ctx) {
    return <MOTAG className="alert-error">{error}</MOTAG>;
  }

  if (!ctx) return null;

  const mcc = ctx.primary_market?.country;
  const mid = ctx.primary_market?.country_id;

  return (
    <Card className="!mb-0">
      <CardTitle>Prompt performance</CardTitle>
      <CardDescription>
        Prompts are grouped by product or service from setup. Run live Gemini, OpenAI, and Claude
        probes, then open SOV analysis for mention share, replies, and inferred competitors.
      </CardDescription>

      {(mcc || mid) && (
        <p className="text-xs text-gray-500 -mt-2 mb-4">
          Primary market for probes: <strong>{mcc || "—"}</strong>
          {mid ? ` (${mid})` : ""}
        </p>
      )}

      <MOTAG className="grid sm:grid-cols-2 gap-4 mb-6">
        <MetricCard
          tone="green"
          label="Your brand"
          value={ctx.brand_name || "—"}
          sub="Used for live probes and mention highlighting"
        />
        <MetricCard
          tone="blue"
          label="Your website"
          value={ctx.brand_site_url || "—"}
          sub="Hostname counts toward brand signal in replies"
        />
      </MOTAG>

      <p className="text-sm text-gray-600 mb-4">
        Competitor URLs are not edited here. After live probes, use Show SOV analysis to see peers
        inferred from replies, then Track competitor to save them to this audit.
      </p>

      {ctx.use_pss && ctx.prompt_count > 0 ? (
        <p className="text-sm text-gray-600 mb-4">
          <strong>{ctx.pss_rows.length}</strong> product or service line(s),{" "}
          <strong>{ctx.prompt_count}</strong> prompts — expand groups after live probes.
        </p>
      ) : ctx.prompt_count > 0 ? (
        <MOTAG className="mb-4 max-h-72 overflow-y-auto border border-gray-200 rounded-xl divide-y">
          {ctx.flat_prompts.map((p, i) => (
            <MOTAG key={i} className="px-4 py-3 text-sm whitespace-pre-wrap break-words">
              <span className="text-gray-500 font-semibold mr-2">{i + 1}.</span>
              {p}
            </MOTAG>
          ))}
        </MOTAG>
      ) : (
        <MOTAG className="alert-info mb-4">
          No prompts on file for this audit. Complete the wizard products & prompts step, then re-run
          the audit.
        </MOTAG>
      )}

      {ctx.prompt_count > 0 ? (
        <MOTAG className="mb-6">
          <h4 className="text-base font-semibold text-brand-dark mb-2">Live probe (Gemini + OpenAI + Claude)</h4>
          <p className="text-sm text-gray-600 mb-3">
            Each prompt is answered independently by Gemini, OpenAI, and Claude. After probes, open
            Show SOV analysis for mention-based share and inferred competitors.
          </p>
          <button
            type="button"
            className="btn-secondary"
            disabled={probing}
            onClick={runProbes}
          >
            {probing ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                Running probes…
              </>
            ) : (
              "Run live probes (Gemini + OpenAI + Claude for each prompt)"
            )}
          </button>
          {probeMsg ? <p className="alert-success mt-3 text-sm">{probeMsg}</p> : null}
        </MOTAG>
      ) : null}

      {hasProbes && live ? (
        <SovAnalysis
          auditSlug={slug}
          live={live}
          ctx={ctx}
          showSov={showSov}
          onRevealSov={() => setShowSov(true)}
          onTracked={load}
        />
      ) : null}

      {error && ctx ? <p className="alert-error mt-4 text-sm">{error}</p> : null}
    </Card>
  );
}
'''

# Fix MOTAG open/close tags
import re

def motag_to_div(s: str) -> str:
    s = re.sub(r"<MOTAG\b", "<div", s)
    s = re.sub(r"</MOTAG>", "</div>", s)
    s = re.sub(r"<MOTAG\s*/>", "<motion />", s)  # noop fix below
    s = s.replace("<motion />", "<div />")
    return s

OUT.write_text(motag_to_div(content))
print("Wrote", OUT)
