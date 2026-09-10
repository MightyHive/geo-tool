import { useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";
import { fetchAudit, fetchScoreBreakdown } from "../api/client";

const STRONG_TECHNICAL_SCORE = 75;

interface SampleScriptsIntroProps {
  auditDirOrSlug: string;
}

function scriptSignals(summary: Record<string, unknown> | null | undefined) {
  const robots = (summary?.robots_txt as { exists?: boolean } | undefined)?.exists === true;
  const llms = (summary?.llms_txt as { exists?: boolean } | undefined)?.exists === true;
  const pageSummary = summary?.summary as { any_json_ld?: boolean } | undefined;
  const anyJsonLd = pageSummary?.any_json_ld === true;
  return { robots, llms, anyJsonLd };
}

export function SampleScriptsIntro({ auditDirOrSlug }: SampleScriptsIntroProps) {
  const [strongSetup, setStrongSetup] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchAudit(auditDirOrSlug).catch(() => null),
      fetchScoreBreakdown(auditDirOrSlug).catch(() => null),
    ]).then(([audit, breakdown]) => {
      if (cancelled) return;
      const nextSignals = scriptSignals(
        (audit?.summary ?? null) as Record<string, unknown> | null,
      );
      const techScore = breakdown?.technical_setup;
      setStrongSetup(
        typeof techScore === "number"
          && techScore >= STRONG_TECHNICAL_SCORE
          && nextSignals.robots
          && nextSignals.llms
          && nextSignals.anyJsonLd,
      );
    });
    return () => {
      cancelled = true;
    };
  }, [auditDirOrSlug]);

  return (
    <div className="space-y-4">
      {strongSetup ? (
        <div
          className="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-4"
          role="status"
        >
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-emerald-600" aria-hidden="true" />
          <div>
            <p className="text-sm font-semibold text-emerald-900">
              Technical setup looks strong — key scripts appear to be in place
            </p>
            <p className="mt-1 text-xs leading-relaxed text-emerald-800">
              This audit found a live robots.txt, a live llms.txt, and JSON-LD on crawled pages,
              with a technical readiness score of {STRONG_TECHNICAL_SCORE}+. The samples below are
              still useful as reference implementations and merge suggestions, not as a to-do list.
            </p>
          </div>
        </div>
      ) : null}

    </div>
  );
}
