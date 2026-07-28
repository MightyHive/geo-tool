import { useEffect, useState } from "react";
import { CheckCircle2, FileCode2 } from "lucide-react";
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
  const [signals, setSignals] = useState({ robots: false, llms: false, anyJsonLd: false });

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
      setSignals(nextSignals);
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

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className="border-b border-gray-100 bg-gray-50 px-6 py-3">
          <h2 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            <FileCode2 className="h-3.5 w-3.5" aria-hidden="true" />
            Sample scripts
          </h2>
        </div>
        <div className="space-y-3 px-6 py-4">
          <p className="text-sm leading-relaxed text-gray-700">
            These are <strong>example versions of key files</strong> you can implement on the site
            to improve AI crawler access and citability: a merged <code className="text-xs">robots.txt</code>,
            a WebSite <code className="text-xs">JSON-LD</code> sample, and a generated{" "}
            <code className="text-xs">llms.txt</code> skeleton. Expand each block to review and copy.
          </p>
          <p className="text-xs leading-relaxed text-gray-500">
            Use them as starting points — adapt hosts, paths, and policy to your brand before
            publishing.{" "}
            {!signals.robots || !signals.llms || !signals.anyJsonLd ? (
              <>
                Current crawl signals: robots.txt{" "}
                {signals.robots ? "found" : "missing"}, llms.txt{" "}
                {signals.llms ? "live" : "not live"}, JSON-LD{" "}
                {signals.anyJsonLd ? "present" : "not detected on sampled pages"}.
              </>
            ) : (
              <>All three artefacts were detected on this audit’s crawl.</>
            )}
          </p>
        </div>
      </div>
    </div>
  );
}
