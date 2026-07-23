import { Fragment, useEffect, useMemo, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import { fetchCompetitorComparison } from "../api/client";
import type {
  CompetitorComparisonRow,
  CompetitorPillarComponent,
  CompetitorPillarRationale,
} from "../types";
import {
  normalizeComparisonCriteria,
  type ComparisonPillar,
} from "../lib/competitorComparisonCriteria";
import { formatReportScore, scoreColor, scoreTone } from "../lib/reportScore";
import { CompetitorFavicon } from "./PlatformLogo";

function overallFromPillars(row: CompetitorComparisonRow): number {
  if (typeof row.overall === "number" && Number.isFinite(row.overall)) {
    return row.overall;
  }
  return Math.round((0.4 * row.ai_visibility + 0.3 * row.technical_setup + 0.3 * row.content_quality) * 10) / 10;
}

function ScoreCell({ value }: { value: number }) {
  const color = scoreColor(scoreTone(value));
  return (
    <td className="px-4 py-3 text-right font-semibold tabular-nums" style={{ color }}>
      {formatReportScore(value)}
    </td>
  );
}

type CriterionPair = {
  key: string;
  title: string;
  competitor?: CompetitorPillarComponent;
  brand?: CompetitorPillarComponent;
};

function mergeCriterionPairs(
  competitorComponents: CompetitorPillarComponent[] | undefined,
  brandComponents: CompetitorPillarComponent[] | undefined,
): CriterionPair[] {
  const byKey = new Map<string, CriterionPair>();
  const order: string[] = [];

  const upsert = (component: CompetitorPillarComponent, side: "competitor" | "brand") => {
    const key = component.key || component.title;
    let pair = byKey.get(key);
    if (!pair) {
      pair = { key, title: component.title || key };
      byKey.set(key, pair);
      order.push(key);
    }
    if (!pair.title && component.title) pair.title = component.title;
    if (side === "competitor") pair.competitor = component;
    else pair.brand = component;
  };

  // Prefer brand (Summary) criterion order when present, then competitor-only keys.
  for (const component of brandComponents ?? []) upsert(component, "brand");
  for (const component of competitorComponents ?? []) upsert(component, "competitor");

  return order.map((key) => byKey.get(key)!).filter(Boolean);
}

function FindingBlock({
  label,
  finding,
  evidence,
  score,
  verified = true,
}: {
  label: string;
  finding?: string;
  evidence?: string;
  score?: number;
  /** When false, show score only (no “What we found” blurbs). */
  verified?: boolean;
}) {
  const hasFindingBlurb = Boolean(finding?.trim() || evidence?.trim()) && verified;

  if (!hasFindingBlurb) {
    if (score === undefined) {
      return null;
    }
    return (
      <div className="rounded-lg bg-violet-50/60 px-2.5 py-2">
        <div className="flex items-start justify-between gap-2">
          <p className="text-[9px] font-semibold uppercase tracking-wide text-violet-700">{label}</p>
          <span className="text-[10px] font-bold tabular-nums text-gray-700">
            {formatReportScore(score)}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg bg-violet-50 px-2.5 py-2">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[9px] font-semibold uppercase tracking-wide text-violet-700">{label}</p>
        {typeof score === "number" && Number.isFinite(score) ? (
          <span className="text-[10px] font-bold tabular-nums text-gray-700">
            {formatReportScore(score)}
          </span>
        ) : null}
      </div>
      <p className="mt-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-600/80">
        What we found
      </p>
      <p className="mt-0.5 text-[11px] leading-snug text-gray-700">
        {finding || "No specific finding was recorded."}
      </p>
      {evidence && evidence !== finding ? (
        <div className="mt-1.5 rounded-md border border-violet-100/80 bg-white/70 px-2 py-1.5">
          <p className="text-[9px] font-semibold uppercase tracking-wide text-gray-400">
            Example evidence
          </p>
          <p className="mt-0.5 text-[11px] leading-snug text-gray-600">{evidence}</p>
        </div>
      ) : null}
    </div>
  );
}

function RationaleCell({
  value,
  rationale,
  pillar,
  competitorName,
  brandName,
}: {
  value: number;
  rationale?: CompetitorPillarRationale | null;
  pillar: ComparisonPillar;
  competitorName: string;
  brandName: string;
}) {
  const color = scoreColor(scoreTone(value));
  const competitorComponents = normalizeComparisonCriteria(pillar, rationale?.components);
  const brandComponents = normalizeComparisonCriteria(pillar, rationale?.brand_components);
  const pairs = mergeCriterionPairs(competitorComponents, brandComponents);
  const competitorHasVerified = competitorComponents.some(
    (component) =>
      Boolean(component.finding_summary?.trim() || component.evidence_example?.trim())
      && (component as CompetitorPillarComponent & { verified?: boolean }).verified !== false,
  );
  const hasVerifiedFindings =
    typeof rationale?.has_verified_findings === "boolean"
      ? rationale.has_verified_findings
      : competitorHasVerified;
  const hasDetail = pairs.length > 0;

  return (
    <td className="px-3 py-3 align-top">
      <div className="mb-2 flex items-center justify-end gap-2">
        <span className="text-xs font-bold tabular-nums" style={{ color }}>
          {formatReportScore(value)}
        </span>
      </div>
      <div className="mb-2 h-1.5 overflow-hidden rounded-full bg-gray-100">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color }}
        />
      </div>
      {!hasDetail ? (
        <p className="text-[11px] leading-relaxed text-gray-400">
          Criterion findings are not available for this crawl.
        </p>
      ) : !hasVerifiedFindings ? (
        <div className="divide-y divide-gray-100 border-y border-gray-100">
          {pairs.map((pair) => (
            <div key={pair.key} className="space-y-1.5 py-2.5">
              <p className="text-[11px] font-semibold text-[#0d0d0d]">{pair.title}</p>
              <FindingBlock
                label={competitorName}
                score={pair.competitor?.score ?? pair.brand?.score}
                verified={false}
              />
            </div>
          ))}
        </div>
      ) : (
        <div className="divide-y divide-gray-100 border-y border-gray-100">
          {pairs.map((pair) => {
            const competitorVerified = Boolean(
              pair.competitor
              && (pair.competitor.finding_summary?.trim() || pair.competitor.evidence_example?.trim())
              && (pair.competitor as CompetitorPillarComponent & { verified?: boolean }).verified !== false,
            );
            return (
              <div key={pair.key} className="space-y-1.5 py-2.5">
                <p className="text-[11px] font-semibold text-[#0d0d0d]">{pair.title}</p>
                <div className="space-y-1.5">
                  <FindingBlock
                    label={competitorName}
                    finding={competitorVerified ? pair.competitor?.finding_summary : undefined}
                    evidence={competitorVerified ? pair.competitor?.evidence_example : undefined}
                    score={pair.competitor?.score}
                    verified={competitorVerified}
                  />
                  {pair.brand ? (
                    <FindingBlock
                      label={brandName}
                      finding={pair.brand.finding_summary}
                      evidence={pair.brand.evidence_example}
                      score={pair.brand.score}
                      verified={Boolean(
                        pair.brand.finding_summary?.trim() || pair.brand.evidence_example?.trim(),
                      )}
                    />
                  ) : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </td>
  );
}

export function SiteCrawlComparisonTable({
  auditDirOrSlug,
  refreshKey = 0,
}: {
  auditDirOrSlug: string;
  refreshKey?: number;
}) {
  const [rows, setRows] = useState<CompetitorComparisonRow[]>([]);
  const [hasComparison, setHasComparison] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expandedNames, setExpandedNames] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchCompetitorComparison(auditDirOrSlug)
      .then((result) => {
        if (cancelled) return;
        setRows(result.rows ?? []);
        setHasComparison(Boolean(result.has_comparison));
      })
      .catch((reason) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : "Could not load comparison");
        setRows([]);
        setHasComparison(false);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [auditDirOrSlug, refreshKey]);

  const sortedRows = useMemo(
    () =>
      [...rows].sort(
        (a, b) => overallFromPillars(b) - overallFromPillars(a) || a.name.localeCompare(b.name),
      ),
    [rows],
  );

  const primaryBrandName =
    sortedRows.find((row) => row.is_primary)?.name?.trim() || "Your brand";

  const toggleExpanded = (key: string) => {
    setExpandedNames((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <section className="overflow-hidden rounded-xl border border-gray-200 bg-white">
      <div className="border-b border-gray-100 px-5 py-4">
        <h3 className="text-sm font-semibold text-[#0d0d0d]">Site crawl comparison</h3>
        <p className="mt-1 text-[11px] text-gray-400">
          Primary brand pillars match Summary. Competitor AI Visibility uses prompt visibility and
          SOV when the competitor URL matches probe data; otherwise it falls back to crawl signals.
          Expand a competitor for criterion-level findings.
        </p>
      </div>

      {loading ? (
        <div className="flex items-center justify-center gap-2 px-5 py-12 text-sm text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading comparison…
        </div>
      ) : error ? (
        <p className="px-5 py-8 text-center text-sm text-red-600">{error}</p>
      ) : !sortedRows.length ? (
        <p className="px-5 py-8 text-center text-sm text-gray-400">
          {hasComparison
            ? "Comparison data is incomplete. Re-run the competitor crawl from Config."
            : "Competitor comparison appears after the audit finishes crawling configured competitor sites."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="px-4 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                  Brand
                </th>
                <th className="px-4 py-3 text-right text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                  Overall
                </th>
                <th className="px-4 py-3 text-right text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                  AI visibility
                </th>
                <th className="px-4 py-3 text-right text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                  Technical setup
                </th>
                <th className="px-4 py-3 text-right text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                  Content quality
                </th>
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => {
                const rowKey = `${row.name}-${row.url}`;
                const expanded = !row.is_primary && expandedNames.has(rowKey);
                const overall = overallFromPillars(row);
                return (
                  <Fragment key={rowKey}>
                    <tr
                      className={`border-b border-gray-50 ${
                        row.is_primary ? "bg-emerald-50/40" : ""
                      }`}
                    >
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          {row.is_primary ? (
                            <span className="inline-block h-6 w-6 shrink-0" aria-hidden />
                          ) : (
                            <button
                              type="button"
                              aria-expanded={expanded}
                              aria-label={expanded ? `Collapse ${row.name}` : `Expand ${row.name}`}
                              onClick={() => toggleExpanded(rowKey)}
                              className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-gray-400 hover:bg-gray-100 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                              <ChevronDown
                                className={`h-4 w-4 transition-transform ${expanded ? "rotate-0" : "-rotate-90"}`}
                              />
                            </button>
                          )}
                          <CompetitorFavicon name={row.name} website={row.url} size={17} />
                          {row.is_primary ? (
                            <span className="inline-flex flex-wrap items-center gap-2 font-semibold text-emerald-700">
                              {row.name}
                              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700">
                                Your Brand
                              </span>
                            </span>
                          ) : row.url ? (
                            <a
                              href={row.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="rounded-sm font-semibold text-gray-800 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500"
                            >
                              {row.name}
                            </a>
                          ) : (
                            <span className="font-semibold text-gray-800">{row.name}</span>
                          )}
                        </div>
                      </td>
                      <ScoreCell value={overall} />
                      <ScoreCell value={row.ai_visibility} />
                      <ScoreCell value={row.technical_setup} />
                      <ScoreCell value={row.content_quality} />
                    </tr>
                    {expanded && (
                      <tr className="border-b border-gray-50 bg-gray-50/50">
                        <td className="px-4 py-3 align-top" />
                        <td className="px-4 py-3 align-top" />
                        <RationaleCell
                          value={row.ai_visibility}
                          rationale={row.ai_visibility_rationale}
                          pillar="ai_visibility"
                          competitorName={row.name}
                          brandName={primaryBrandName}
                        />
                        <RationaleCell
                          value={row.technical_setup}
                          rationale={row.technical_setup_rationale}
                          pillar="technical_setup"
                          competitorName={row.name}
                          brandName={primaryBrandName}
                        />
                        <RationaleCell
                          value={row.content_quality}
                          rationale={row.content_quality_rationale}
                          pillar="content_quality"
                          competitorName={row.name}
                          brandName={primaryBrandName}
                        />
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
