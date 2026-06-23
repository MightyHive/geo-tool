import { CheckCircle2, Circle, Loader2 } from "lucide-react";
import { cn } from "../lib/utils";

export interface WizardStepProgressItem {
  id: string;
  label: string;
  status: "pending" | "active" | "done";
}

interface WizardStepProgressProps {
  title?: string;
  detail?: string;
  steps: WizardStepProgressItem[];
}

export function WizardStepProgress({ title, detail, steps }: WizardStepProgressProps) {
  return (
    <div className="mb-6" aria-live="polite">
      {title ? <p className="text-sm font-medium text-brand-dark mb-2">{title}</p> : null}
      {detail ? <p className="text-sm text-gray-600 mb-3">{detail}</p> : null}
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
