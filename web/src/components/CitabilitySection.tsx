import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Info } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { fetchScoreBreakdown } from "../api/client";
import { scoreColor, scoreTone, formatReportScore } from "../lib/reportScore";
import { TextWithSampleScriptsLink } from "./SampleScriptsLink";

type Breakdown = Awaited<ReturnType<typeof fetchScoreBreakdown>>;
type Component = NonNullable<Breakdown["details"]>[string]["components"][number];
type TableRow = {
  key: string;
  title: string;
  detail: string;
  score: number;
  strengths: string[];
  improvements: string[];
};

const ORDER = [
  "ai_citability",
  "ai_search_success",
  "query_coverage_footprint",
];

function findingKey(value: string): string {
  return value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").trim();
}

function dedupeFindings(components: Component[]): Component[] {
  const seenStrengths = new Set<string>();
  const seenImprovements = new Set<string>();
  return components.map((component) => ({
    ...component,
    strengths: (component.strengths ?? []).filter((finding) => {
      const key = findingKey(finding);
      if (!key || seenStrengths.has(key)) return false;
      seenStrengths.add(key);
      return true;
    }),
    improvements: (component.improvements ?? []).filter((finding) => {
      const key = findingKey(finding);
      if (!key || seenImprovements.has(key)) return false;
      seenImprovements.add(key);
      return true;
    }),
  }));
}

export function CitabilitySection({
  auditDirOrSlug,
  onNavigate,
  breakdown: breakdownProp,
}: {
  auditDirOrSlug: string;
  onNavigate?: (sectionId: string) => void;
  breakdown?: Breakdown | null;
}) {
  const [fetched, setFetched] = useState<Breakdown | null>(null);
  const [loading, setLoading] = useState(!breakdownProp);

  useEffect(() => {
    if (breakdownProp) {
      setLoading(false);
      return;
    }
    setLoading(true);
    fetchScoreBreakdown(auditDirOrSlug)
      .then(setFetched)
      .catch(() => setFetched(null))
      .finally(() => setLoading(false));
  }, [auditDirOrSlug, breakdownProp]);

  const breakdown = breakdownProp ?? fetched;

  if (loading) {
    return <PageLoading />;
  }

  const components = dedupeFindings((breakdown?.details?.technical_setup?.components ?? [])
    .filter((component) => ORDER.includes(component.key))
    .slice()
    .sort((a, b) => ORDER.indexOf(a.key) - ORDER.indexOf(b.key)) as Component[]);
  const rows: TableRow[] = components.map((component) => {
    if (component.key === "ai_search_success" && component.criteria?.length) {
      const strengths: string[] = [];
      const improvements: string[] = [];
      for (const criterion of component.criteria) {
        const result = criterion.score >= 80
          ? criterion.strengths?.[0] ?? "This criterion is performing well."
          : criterion.improvements?.[0] ?? "This criterion needs further work.";
        const labelledResult = `${criterion.title} (${formatReportScore(criterion.score)}/100): ${result}`;
        (criterion.score >= 80 ? strengths : improvements).push(labelledResult);
      }
      return {
        key: component.key,
        title: component.title,
        detail: component.detail,
        score: component.score,
        strengths,
        improvements,
      };
    }
    return {
      key: component.key,
      title: component.title,
      detail: component.detail,
      score: component.score,
      strengths: component.strengths ?? [],
      improvements: component.improvements ?? [],
    };
  });

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Citability</h2>
        <p className="mt-1 text-sm text-gray-500">
          AI citability, nine AI Search Success criteria, and query coverage.
          Findings that mention <code className="text-xs">llms.txt</code>,{" "}
          <code className="text-xs">robots.txt</code>, or JSON-LD link to Sample scripts.
        </p>
      </div>

      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="w-[190px] px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">Criteria</th>
                <th className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">What is working well</th>
                <th className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">What needs work</th>
                <th className="w-[100px] px-5 py-3 text-center text-[10px] font-semibold uppercase tracking-wide text-gray-400">Score</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const color = scoreColor(scoreTone(row.score));
                return (
                  <tr key={row.key} className="border-b border-gray-100 align-top last:border-0">
                    <td className="px-5 py-4">
                      <div className="font-semibold text-[#0d0d0d]">{row.title}</div>
                      <p className="mt-1 text-[11px] leading-relaxed text-gray-400">
                        <TextWithSampleScriptsLink text={row.detail} onNavigate={onNavigate} />
                      </p>
                    </td>
                    <td className="px-5 py-4">
                      {row.strengths.length ? (
                        <ul className="space-y-2">
                          {row.strengths.map((finding, index) => (
                            <li key={`${finding}-${index}`} className="flex gap-2 text-xs leading-relaxed text-gray-600">
                              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                              <TextWithSampleScriptsLink text={finding} onNavigate={onNavigate} />
                            </li>
                          ))}
                        </ul>
                      ) : <span className="text-xs italic text-gray-400">No positive finding recorded yet.</span>}
                    </td>
                    <td className="px-5 py-4">
                      {row.improvements.length ? (
                        <ul className="space-y-2">
                          {row.improvements.map((finding, index) => (
                            <li key={`${finding}-${index}`} className="flex gap-2 text-xs leading-relaxed text-gray-600">
                              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                              <TextWithSampleScriptsLink text={finding} onNavigate={onNavigate} />
                            </li>
                          ))}
                        </ul>
                      ) : <span className="text-xs italic text-gray-400">No material gap recorded.</span>}
                    </td>
                    <td className="px-5 py-4 text-center">
                      <span className="inline-flex min-w-14 justify-center rounded-full px-2.5 py-1 text-sm font-bold" style={{ background: `${color}18`, color }}>
                        {formatReportScore(row.score)}
                      </span>
                    </td>
                  </tr>
                );
              })}
              {!rows.length && (
                <tr>
                  <td colSpan={4} className="px-5 py-8">
                    <div className="flex items-center justify-center gap-2 text-sm text-gray-400">
                      <Info className="h-4 w-4" />
                      No citability findings are available for this audit.
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
