import type { ReactNode, SelectHTMLAttributes } from "react";
import { cn } from "../lib/utils";

const SELECT_CLASS =
  "min-w-[12rem] rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-sm text-[#0d0d0d] shadow-sm focus:outline-none focus:ring-2 focus:ring-[#0984e3]/30";

/**
 * Shared report filter control — label above select, optional hint below.
 * Used for Market/Language, Topic, and surface (Chatbots vs AI Overviews) filters.
 */
export function ReportFilterSelect({
  id,
  label,
  hint,
  className,
  children,
  ...selectProps
}: {
  id: string;
  label: string;
  hint?: string;
  className?: string;
  children: ReactNode;
} & Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "className" | "children">) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <label htmlFor={id} className="text-xs font-semibold uppercase tracking-wide text-gray-400">
        {label}
      </label>
      <select id={id} className={SELECT_CLASS} {...selectProps}>
        {children}
      </select>
      {hint ? <p className="max-w-[16rem] text-[11px] leading-snug text-gray-400">{hint}</p> : null}
    </div>
  );
}

export { SELECT_CLASS as REPORT_FILTER_SELECT_CLASS };
