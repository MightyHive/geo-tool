/**
 * ContentOverview — summary of the Content Quality pillar.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Info, ChevronRight, BookOpen, Award, Link2, Users } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { fetchScoreBreakdown } from "../api/client";
import {
  CONTENT_QUALITY_SUB_AREAS,
  weightedComponentsScore,
  type ContentQualitySubArea,
} from "../lib/contentQualityAreas";
import { scoreLabel, scoreTone, scoreColor, formatReportScore } from "../lib/reportScore";
import { usePromptPerformanceContext } from "../lib/promptPerformanceStore";
import ScoreOverTime from "./ScoreOverTime";

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

const AREA_ICONS: Record<ContentQualitySubArea["key"], React.ElementType> = {
  eeat: Award,
  structure_answerability: BookOpen,
  schema_entity_markup: Link2,
  brand_visibility_authority: Users,
};

function ScoreBandDescription({ score }: { score: number }) {
  const label = scoreLabel(score);
  if (label === "Excellent") return <p className="text-sm text-gray-600">Excellent content quality. AI systems can reliably identify, trust, and cite your content as an authoritative source.</p>;
  if (label === "Good") return <p className="text-sm text-gray-600">Strong content quality. Most content signals are in place, with a few areas to improve for broader AI citation coverage.</p>;
  if (label === "OK") return <p className="text-sm text-gray-600">OK content quality. Some good signals present, but gaps in E-E-A-T, content structure, or entity clarity are limiting AI citability.</p>;
  if (label === "Weak") return <p className="text-sm text-gray-600">Weak content quality. AI systems may find it difficult to identify the content as credible or well-structured enough to cite.</p>;
  return <p className="text-sm text-gray-600">Critical content quality gaps. Foundational E-E-A-T and content structure improvements are needed before AI tools will regularly cite this content.</p>;
}

export function ContentOverview({
  auditDirOrSlug,
  onNavigate,
}: {
  auditDirOrSlug: string;
  onNavigate?: (sectionId: string) => void;
}) {
  const [breakdown, setBreakdown] = useState<Awaited<ReturnType<typeof fetchScoreBreakdown>> | null>(null);
  const [loading, setLoading] = useState(true);
  const { ctx } = usePromptPerformanceContext(auditDirOrSlug);
  const brandName = ctx?.brand_name?.trim() || "Your Brand";

  const load = useCallback(() => {
    setLoading(true);
    fetchScoreBreakdown(auditDirOrSlug)
      .then((b) => setBreakdown(b))
      .catch(() => setBreakdown(null))
      .finally(() => setLoading(false));
  }, [auditDirOrSlug]);

  useEffect(() => { load(); }, [load]);

  if (loading) {
    return <PageLoading />;
  }

  const score = breakdown?.content_structure ?? null;
  const tone = score !== null ? scoreTone(score) : null;
  const color = score !== null ? scoreColor(tone!) : "#9ca3af";
  const contentComponents = breakdown?.details?.content_structure?.components ?? [];
  const componentByKey = new Map(contentComponents.map((component) => [component.key, component]));

  const subAreaScore = (area: ContentQualitySubArea): number | null => {
    const components = area.componentKeys
      .map((key) => componentByKey.get(key))
      .filter((component): component is NonNullable<typeof component> => Boolean(component))
      .map((component) => ({ score: component.score, weight_pct: component.weight_pct }));
    return weightedComponentsScore(components);
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Content Quality</h2>
        <p className="text-sm text-gray-500 mt-1">
          How helpful, trustworthy, and structured your content is for AI citation. Covers
          E-E-A-T signals, content answerability, schema markup, and brand authority.
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
                  Content Quality Score
                </span>
                <Tooltip text="The Content Quality score measures whether content is helpful, trustworthy, and well-structured for AI citation. Covers E-E-A-T signals, content answerability, schema markup, and brand authority across third-party platforms." />
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
            <p className="text-sm">Run the GEO audit to generate the Content Quality score.</p>
          </div>
        )}
      </div>

      <ScoreOverTime
        auditId={auditDirOrSlug}
        brandLabel={brandName}
        metrics={["content_structure"]}
        title="Content Quality over time"
        description="Brand content score trend, with competitor lines when competitor crawls have run."
      />

      {/* What's measured */}
      <div className="bg-white rounded-2xl border border-gray-200 p-5">
        <h3 className="text-sm font-bold text-[#0d0d0d] mb-4">What this score measures</h3>
        <div className="space-y-0 divide-y divide-gray-50">
          {CONTENT_QUALITY_SUB_AREAS.map((area) => {
            const areaScore = subAreaScore(area);
            const areaColor = areaScore == null ? "#9ca3af" : scoreColor(scoreTone(areaScore));
            const Icon = AREA_ICONS[area.key];
            return (
            <div key={area.key} className="py-3.5 flex items-start gap-4">
              <div className="w-8 h-8 rounded-lg bg-gray-50 flex items-center justify-center shrink-0 mt-0.5">
                <Icon className="w-4 h-4 text-gray-400" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1 mb-0.5">
                  <span className="text-sm font-semibold text-[#0d0d0d]">{area.label}</span>
                  <Tooltip text={area.tooltip} />
                </div>
                <p className="text-xs text-gray-500 leading-snug">{area.description}</p>
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
                  className="shrink-0 flex items-center gap-1 text-[11px] text-blue-500 hover:text-blue-700 font-medium mt-0.5"
                >
                  View <ChevronRight className="w-3 h-3" />
                </button>
              )}
            </div>
          )})}
        </div>
      </div>

      {/* Score context */}
      <div className="bg-emerald-50 rounded-2xl border border-emerald-100 px-5 py-4">
        <p className="text-xs text-emerald-700 leading-relaxed">
          <span className="font-semibold">Score context:</span> Content Quality is weighted at 30% of the overall GEO score.
          It is generated at audit time. To improve it, focus on E-E-A-T — adding author bios, citations,
          original research, and well-structured answer-style content will have the greatest impact.
          Brand visibility across Reddit, YouTube, LinkedIn, and Wikipedia also contributes.
        </p>
      </div>
    </div>
  );
}
