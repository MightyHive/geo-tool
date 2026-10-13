import { useMemo } from "react";
import { Users } from "lucide-react";
import type { PromptPerformanceContext } from "../types";
import { computePersonaPerformance } from "../lib/personas";
import CitationVisibilityScatter, { personaCitationVisibilityPoints } from "./CitationVisibilityScatter";

function pct(value: number): string {
  return `${value.toFixed(1)}%`;
}

/** Buyer persona cards + Citation Rate vs Visibility scatter (only when the audit has personas). */
export default function PersonaOverviewSection({
  ctx,
}: {
  ctx: PromptPerformanceContext | null | undefined;
}) {
  const rows = useMemo(() => computePersonaPerformance(ctx), [ctx]);
  const points = useMemo(() => personaCitationVisibilityPoints(ctx), [ctx]);
  if (!rows.length) return null;

  return (
    <section className="space-y-4" aria-labelledby="persona-overview-heading">
      <div className="rounded-xl border border-gray-200 bg-white p-5">
        <h3 id="persona-overview-heading" className="flex items-center gap-2 text-sm font-semibold text-[#0d0d0d]">
          <Users className="h-4 w-4 text-gray-500" />
          Persona overview
        </h3>
        <p className="mt-1 text-[11px] text-gray-400">
          How your brand performs for prompts written in each buyer persona&rsquo;s voice.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {rows.map((row) => (
            <div key={row.name} className="rounded-lg border border-gray-100 bg-gray-50 p-3">
              <p className="text-sm font-semibold text-[#0d0d0d]">{row.name}</p>
              {row.description && (
                <p className="mt-0.5 text-xs text-gray-500 line-clamp-3">{row.description}</p>
              )}
              <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-gray-400">Visibility</dt>
                  <dd className="text-sm font-semibold tabular-nums">{pct(row.visibilityPct)}</dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-gray-400">Citation rate</dt>
                  <dd className="text-sm font-semibold tabular-nums">
                    {row.citationCount > 0 ? pct(row.citationRatePct) : "–"}
                  </dd>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wide text-gray-400">SOV</dt>
                  <dd className="text-sm font-semibold tabular-nums">{pct(row.sovPct)}</dd>
                </div>
              </dl>
              <p className="mt-2 text-[11px] text-gray-500">
                {row.promptCount} prompt{row.promptCount === 1 ? "" : "s"} · {row.responseCount} responses
                {row.topCompetitor ? ` · Top competitor: ${row.topCompetitor}` : ""}
              </p>
            </div>
          ))}
        </div>
      </div>
      <CitationVisibilityScatter
        title="Citation Rate vs Visibility: Personas"
        subtitle="Your brand by buyer persona"
        dimensionLabel="Persona"
        points={points}
        emptyMessage="No persona responses available yet."
      />
    </section>
  );
}
