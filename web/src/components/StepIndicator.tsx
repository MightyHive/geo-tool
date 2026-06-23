import { cn } from "../lib/utils";

interface Step {
  id: number;
  label: string;
}

interface StepIndicatorProps {
  steps: Step[];
  current: number;
}

export function StepIndicator({ steps, current }: StepIndicatorProps) {
  return (
    <div
      className="flex justify-center items-center flex-wrap gap-1 py-4 mb-6"
      role="navigation"
      aria-label="Progress"
    >
      {steps.map((s, i) => {
        const done = s.id < current;
        const isCurrent = s.id === current;
        return (
          <div key={s.id} className="flex items-center">
            {i > 0 && (
              <div
                className={cn(
                  "w-6 sm:w-8 h-0.5 mx-0.5",
                  done ? "bg-brand-accent" : "bg-gray-300",
                )}
              />
            )}
            <div className="flex items-center gap-1.5">
              <div
                className={cn(
                  "w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-colors",
                  done && "bg-brand-accent border-brand-accent text-white",
                  isCurrent &&
                    "bg-white border-brand-accent text-brand-dark shadow-sm",
                  !done &&
                    !isCurrent &&
                    "bg-white border-gray-300 text-gray-400",
                )}
              >
                {done ? "✓" : s.id}
              </div>
              <span
                className={cn(
                  "text-xs hidden sm:inline",
                  isCurrent ? "font-semibold text-brand-dark" : "text-gray-500",
                )}
              >
                {s.label}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
