import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { cn } from "../lib/utils";
import type { AuditRunProgressPayload } from "../types";

interface AuditRunProgressProps {
  progress: AuditRunProgressPayload | null;
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

export function AuditRunProgress({ progress }: AuditRunProgressProps) {
  const percent = progress?.percent ?? 2;
  const detail = progress?.detail ?? "Starting audit…";
  const steps = progress?.steps?.length ? progress.steps : DEFAULT_STEPS;

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
      <p className="text-sm text-gray-600 mb-4">{detail}</p>
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
