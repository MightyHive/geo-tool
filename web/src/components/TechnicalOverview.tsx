/**
 * TechnicalOverview — summary of the Technical Setup pillar.
 *
 * Shows the Technical GEO Setup score (from the backend report.html), explains
 * what it measures, and surfaces key sub-area findings.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bot, ChevronRight, Info, Layers3, Quote } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { fetchScoreBreakdown } from "../api/client";
import {
  TECHNICAL_SETUP_SUB_AREAS,
  weightedComponentsScore,
  type TechnicalSetupSubArea,
} from "../lib/technicalSetupAreas";
import { scoreLabel, scoreTone, scoreColor, formatReportScore } from "../lib/reportScore";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import ScoreOverTime from "./ScoreOverTime";
import { SampleScriptsLinkButton, TextWithSampleScriptsLink } from "./SampleScriptsLink";

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
          className="fixed w-64 rounded-xl bg-gray-900 text-white text-xs px-3 py-2.5 leading-relaxed z-[9999] shadow-xl pointer-events-none"
          style={{ left: pos.x, top: pos.y, transform: "translateX(-50%)" }}
        >
          {text}
        </span>,
        document.body,
      )}
    </span>
  );
}

function ScoreGauge({ score, size = 80 }: { score: number; size?: number }) {
  const r = (size - 10) / 2;
  const circ = 2 * Math.PI * r;
  const dash = (Math.min(100, Math.max(0, score)) / 100) * circ;
  const tone = scoreTone(score);
  const color = scoreColor(tone);
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e5e7eb" strokeWidth={8} />
      <circle
        cx={size / 2} cy={size / 2} r={r}
        fill="none" stroke={color} strokeWidth={8}
        strokeDasharray={`${dash} ${circ - dash}`}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="50%" dominantBaseline="middle" textAnchor="middle"
        fontSize={size * 0.24} fontWeight="700" fill="#0d0d0d">
        {formatReportScore(score)}
      </text>
    </svg>
  );
}

const AREA_ICONS: Record<TechnicalSetupSubArea["key"], React.ElementType> = {
  crawler_access: Bot,
  citability: Quote,
  platform_readiness: Layers3,
};

function ScoreBandDescription({ score }: { score: number }) {
  const label = scoreLabel(score);
  if (label === "Excellent") return <p className="text-sm text-gray-600">Excellent technical foundation. AI crawlers can access, understand, and extract content reliably. Only minor optimisations needed.</p>;
  if (label === "Good") return <p className="text-sm text-gray-600">Strong technical setup. Most AI tools can access and parse the site. A few specific areas need attention to maximise citability.</p>;
  if (label === "OK") return <p className="text-sm text-gray-600">OK technical readiness. Some content is accessible and structured, but gaps in schema, crawl access, or page quality are limiting citation potential.</p>;
  if (label === "Weak") return <p className="text-sm text-gray-600">Weak technical setup. AI tools may struggle to reliably access, parse, or understand the content. Structured fixes will have meaningful impact.</p>;
  return <p className="text-sm text-gray-600">Critical technical gaps. AI crawlers and answer engines face significant barriers to accessing or using the content. Foundational work is required.</p>;
}

export function TechnicalOverview({
  auditDirOrSlug,
  onNavigate,
  breakdown: breakdownProp,
  hideScoreHistory = false,
}: {
  auditDirOrSlug: string;
  onNavigate?: (sectionId: string) => void;
  breakdown?: Awaited<ReturnType<typeof fetchScoreBreakdown>> | null;
  hideScoreHistory?: boolean;
}) {
  const [fetched, setFetched] = useState<Awaited<ReturnType<typeof fetchScoreBreakdown>> | null>(null);
  const [loading, setLoading] = useState(!breakdownProp);
  const { ctx } = usePromptPerformanceContext(auditDirOrSlug);
  const brandName = ctx?.brand_name?.trim() || "Your Brand";

  const load = useCallback(() => {
    if (breakdownProp) {
      setLoading(false);
      return;
    }
    setLoading(true);
    fetchScoreBreakdown(auditDirOrSlug)
      .then((b) => setFetched(b))
      .catch(() => setFetched(null))
      .finally(() => setLoading(false));
  }, [auditDirOrSlug, breakdownProp]);

  useEffect(() => { load(); }, [load]);

  const breakdown = breakdownProp ?? fetched;

  if (loading) {
    return <PageLoading />;
  }

  const score = breakdown?.technical_setup ?? null;
  const tone = score !== null ? scoreTone(score) : null;
  const color = score !== null ? scoreColor(tone!) : "#9ca3af";
  const readinessComponents = breakdown?.details?.technical_setup?.components ?? [];
  const componentByKey = new Map(readinessComponents.map((component) => [component.key, component]));

  const subAreaScore = (area: TechnicalSetupSubArea): number | null => {
    const components = area.componentKeys
      .map((key) => componentByKey.get(key))
      .filter((component): component is NonNullable<typeof component> => Boolean(component))
      .map((component) => ({ score: component.score, weight_pct: component.weight_pct }));
    return weightedComponentsScore(components);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Technical Setup</h2>
        <p className="text-sm text-gray-500 mt-1">
          How well AI tools can access and use the site. Covers crawler access, citability,
          and platform readiness.
        </p>
      </div>

      {/* Score hero card */}
      <div className="bg-white rounded-2xl border border-gray-200 p-6">
        {score !== null ? (
          <div className="flex items-start gap-5">
            <ScoreGauge score={score} size={88} />
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                  Technical GEO Setup
                </span>
                <Tooltip text="The Technical Setup score combines crawler access (25%), citability (50%), and platform readiness (25%). Brand and entity visibility is measured separately under Content Quality." />
              </div>
              <div className="flex items-baseline gap-3 mb-2">
                <span className="text-4xl font-bold text-[#0d0d0d]">{formatReportScore(score)}</span>
                <span className="text-lg font-semibold" style={{ color }}>{scoreLabel(score)}</span>
                <span className="text-sm text-gray-400">/ 100</span>
              </div>
              <div className="h-2 bg-gray-100 rounded-full overflow-hidden mb-3" style={{ maxWidth: 320 }}>
                <div className="h-full rounded-full transition-all" style={{ width: `${score}%`, background: color }} />
              </div>
              <ScoreBandDescription score={score} />
            </div>
          </div>
        ) : (
          <div className="flex items-center gap-3 text-gray-400 py-4">
            <Info className="w-5 h-5 shrink-0" />
            <p className="text-sm">Run the GEO audit to generate the Technical Setup score.</p>
          </div>
        )}
      </div>

      {hideScoreHistory ? null : (
      <ScoreOverTime
        auditId={auditDirOrSlug}
        brandLabel={brandName}
        metrics={["technical_setup"]}
        title="Technical Setup over time"
        description="Brand technical score trend, with competitor lines when competitor crawls have run."
      />
      )}

      <div className="rounded-2xl border border-gray-200 bg-white p-5">
        <h3 className="mb-4 text-sm font-bold text-[#0d0d0d]">What this score measures</h3>
        <div className="divide-y divide-gray-50">
          {TECHNICAL_SETUP_SUB_AREAS.map((area) => {
            const areaScore = subAreaScore(area);
            const areaColor = areaScore == null ? "#9ca3af" : scoreColor(scoreTone(areaScore));
            const Icon = AREA_ICONS[area.key];
            return (
              <div key={area.key} className="flex items-start gap-4 py-3.5">
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-50">
                  <Icon className="h-4 w-4 text-gray-400" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="mb-0.5 flex items-center gap-1">
                    <span className="text-sm font-semibold text-[#0d0d0d]">{area.label}</span>
                    <Tooltip text={area.tooltip} />
                  </div>
                  <p className="text-xs leading-snug text-gray-500">
                    <TextWithSampleScriptsLink text={area.description} onNavigate={onNavigate} />
                  </p>
                </div>
                <div className="shrink-0 text-right">
                  <span
                    className="inline-flex min-w-14 justify-center rounded-full px-2.5 py-1 text-xs font-bold"
                    style={{ color: areaColor, background: `${areaColor}18` }}
                  >
                    {areaScore == null ? "—" : Math.round(areaScore)}
                  </span>
                  <span className="ml-1 text-[10px] text-gray-300">/100</span>
                </div>
                {onNavigate && (
                  <button
                    type="button"
                    onClick={() => onNavigate(area.sectionId)}
                    className="mt-0.5 flex shrink-0 items-center gap-1 text-[11px] font-medium text-blue-500 hover:text-blue-700"
                  >
                    View <ChevronRight className="h-3 w-3" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Score context */}
      <div className="bg-blue-50 rounded-2xl border border-blue-100 px-5 py-4">
        <p className="text-xs text-blue-700 leading-relaxed">
          <span className="font-semibold">Score context:</span> Technical Setup is part of the overall GEO score
          (weighted 30%). It is generated by the audit engine and reflects the state of the site at
          last crawl time. Re-run the audit after making technical fixes to see updated scores.
          Citability includes AI search success and query coverage. Platform Readiness also
          incorporates live probe data from the Prompts section.
          {onNavigate ? (
            <>
              {" "}When findings call for <code className="text-[11px]">robots.txt</code>,{" "}
              <code className="text-[11px]">llms.txt</code>, or JSON-LD changes, use{" "}
              <SampleScriptsLinkButton onNavigate={onNavigate} className="text-xs no-underline" label="Sample scripts" />
              {" "}as copy-ready starting points.
            </>
          ) : null}
        </p>
      </div>
    </div>
  );
}
