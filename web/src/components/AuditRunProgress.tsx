import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { cn } from "../lib/utils";
import { formatProbeRunEta } from "../lib/probeRunEta";
import type { AuditRunProgressPayload, ProbeProgressSummary } from "../types";

interface AuditRunProgressProps {
  progress: AuditRunProgressPayload | null;
  /** Fallback market count from wizard config before probe events arrive. */
  marketCount?: number;
}

const DEFAULT_STEPS = [
  { id: "crawl", label: "Crawling your site", status: "active" as const },
  { id: "competitors", label: "Analysing competitors", status: "pending" as const },
  { id: "ga4", label: "Pulling GA4 traffic", status: "pending" as const },
  { id: "report", label: "Building report and scores", status: "pending" as const },
  {
    id: "prompt_probes",
    label: "AI prompt probes (share of voice)",
    status: "pending" as const,
  },
  { id: "sentiment", label: "Sentiment analysis", status: "pending" as const },
  { id: "finish", label: "Finishing up", status: "pending" as const },
];

function probeEtaLine(probe: ProbeProgressSummary | null | undefined): string | null {
  if (!probe) return null;
  const planned = probe.planned_calls ?? 0;
  const completed = probe.completed_calls ?? 0;
  if (planned > 0) {
    const remaining = Math.max(0, planned - completed);
    return formatProbeRunEta({ remainingCalls: remaining, totalCalls: planned });
  }
  if (probe.eta_seconds != null && probe.eta_seconds > 0) {
    const mins = Math.max(1, Math.ceil(probe.eta_seconds / 60));
    return `About ${mins} minute${mins === 1 ? "" : "s"} remaining`;
  }
  if (probe.eta_total_seconds != null && probe.eta_total_seconds > 0) {
    const mins = Math.max(1, Math.ceil(probe.eta_total_seconds / 60));
    return `Estimated time: ~${mins} minute${mins === 1 ? "" : "s"}`;
  }
  return null;
}

function probeStatsLine(
  probe: ProbeProgressSummary | null | undefined,
  fallbackMarkets: number | undefined,
): string | null {
  const markets = probe?.market_count || fallbackMarkets || 0;
  const parts: string[] = [];

  if (probe?.prompt_total && probe.prompt_total > 0) {
    const done = Math.min(probe.prompt_index || 0, probe.prompt_total);
    parts.push(`Prompts ${done}/${probe.prompt_total}`);
  }

  if (probe?.planned_calls && probe.planned_calls > 0) {
    const label =
      markets > 1
        ? `Platform calls ${probe.completed_calls ?? 0}/${probe.planned_calls} per market`
        : `Platform calls ${probe.completed_calls ?? 0}/${probe.planned_calls}`;
    parts.push(label);
  }

  if (markets > 0) {
    parts.push(markets === 1 ? "1 market" : `${markets} markets`);
  }

  const eta = probeEtaLine(probe);
  if (eta) parts.push(eta);

  return parts.length ? parts.join(" · ") : null;
}

export function AuditRunProgress({ progress, marketCount }: AuditRunProgressProps) {
  const percent = progress?.percent ?? 2;
  const detail = progress?.detail ?? "Starting audit…";
  const steps = progress?.steps?.length ? progress.steps : DEFAULT_STEPS;
  const probe = progress?.probe_progress;
  const stats = probeStatsLine(probe, progress?.market_count ?? marketCount);
  const probing = progress?.current_step === "prompt_probes";
  const etaOnly = !stats ? probeEtaLine(probe) : null;

  return (
    <div className="mt-4" aria-live="polite" aria-busy="true">
      <div className="flex items-center justify-between gap-3 mb-2">
        <p className="text-sm font-medium text-brand-dark">Running audit</p>
        <span className="text-sm text-gray-600 tabular-nums">{percent}%</span>
      </div>
      <div className="h-2 w-full rounded-full bg-gray-200 overflow-hidden mb-3">
        <div
          className="h-full rounded-full bg-brand-dark transition-all duration-500 ease-out"
          style={{ width: `${percent}%` }}
        />
      </div>
      <p className="text-sm text-gray-600 mb-1">{detail}</p>
      {stats && (
        <p
          className={cn(
            "text-sm mb-4 tabular-nums",
            probing ? "text-brand-dark font-medium" : "text-gray-500",
          )}
        >
          {stats}
        </p>
      )}
      {!stats && etaOnly && (
        <p
          className={cn(
            "text-sm mb-4 tabular-nums",
            probing ? "text-brand-dark font-medium" : "text-gray-500",
          )}
        >
          {etaOnly}
        </p>
      )}
      {!stats && !etaOnly && <div className="mb-3" />}
      <ul className="space-y-2">
        {steps.map((step) => (
          <li key={step.id} className="flex items-start gap-2 text-sm">
            {step.status === "done" ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0 mt-0.5" aria-hidden />
            ) : step.status === "active" ? (
              <Loader2 className="w-4 h-4 text-brand-accent animate-spin shrink-0 mt-0.5" aria-hidden />
            ) : (
              <Circle className="w-4 h-4 text-gray-300 shrink-0 mt-0.5" aria-hidden />
            )}
            <span
              className={cn(
                step.status === "active" && "font-medium text-brand-dark",
                step.status === "done" && "text-gray-700",
                step.status === "pending" && "text-gray-400",
              )}
            >
              {step.label}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
