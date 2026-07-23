import { CheckCircle2, RefreshCw } from "lucide-react";
import type { CompetitorCrawlStatusResponse } from "../types";

interface CompetitorCrawlControlsProps {
  competitorCount?: number;
  compact?: boolean;
  status: CompetitorCrawlStatusResponse;
  busy: boolean;
  error: string | null;
  onStart: () => void;
}

function formatWhen(value?: string): string | null {
  if (!value) return null;
  try {
    return new Date(value).toLocaleString(undefined, {
      day: "numeric",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return value;
  }
}

/** Status + optional re-run for Config after editing competitors (not an audit opt-in). */
export function CompetitorCrawlControls({
  competitorCount = 0,
  compact = false,
  status,
  busy,
  error,
  onStart,
}: CompetitorCrawlControlsProps) {
  const estimateMinutes = Math.max(3, Math.ceil(Math.max(competitorCount, 1) * 1.5));
  const finishedLabel = formatWhen(status.finished_at);
  const archiveCount = status.archives?.length ?? 0;
  const hasPrior =
    status.status === "done" || status.has_comparison || (status.archives?.length ?? 0) > 0;

  const buttonLabel = busy
    ? "Crawling…"
    : hasPrior
      ? "Re-run competitor crawl"
      : "Refresh competitor crawl";

  const statusLine = (() => {
    if (busy) {
      return (
        <>
          <RefreshCw className="mr-1.5 inline h-3.5 w-3.5 animate-spin" />
          {status.detail || "Crawling competitor sites…"} You can leave this page — the crawl
          keeps running in the background.
        </>
      );
    }
    if (status.status === "done") {
      return (
        <>
          <CheckCircle2 className="mr-1.5 inline h-3.5 w-3.5" />
          Latest crawl complete
          {finishedLabel ? ` · ${finishedLabel}` : ""}
          {typeof status.competitor_count === "number"
            ? ` · ${status.competitor_count} site${status.competitor_count === 1 ? "" : "s"}`
            : ""}
          {archiveCount > 0 ? ` · ${archiveCount} earlier crawl${archiveCount === 1 ? "" : "s"} saved` : ""}
          . Showing the most recent results below.
        </>
      );
    }
    if (status.has_comparison) {
      return <>Existing competitor comparison on file. Re-run to refresh after config changes.</>;
    }
    return (
      <>
        Competitor site crawl runs automatically with the audit when competitors are configured.
        Use re-run after editing competitor URLs without a full audit.
      </>
    );
  })();

  if (compact) {
    return (
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={onStart}
            disabled={busy || competitorCount === 0}
            className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-violet-300 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
            {buttonLabel}
          </button>
          {competitorCount === 0 && (
            <p className="text-xs text-gray-500">Add competitors in Config first.</p>
          )}
        </div>
        {statusLine && (
          <p className={`text-xs ${busy ? "text-blue-700" : status.status === "done" ? "text-emerald-700" : "text-gray-500"}`}>
            {statusLine}
          </p>
        )}
        {error && <p className="text-xs text-red-700">{error}</p>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="text-sm font-semibold text-[#0d0d0d]">Competitor site crawl</p>
        <p className="mt-1 text-xs leading-relaxed text-gray-500">
          Runs automatically with each audit when competitors are configured. Re-run here after
          editing competitor sites without re-crawling your brand or re-running prompts.
        </p>
        <p className="mt-1 text-[11px] font-medium text-gray-400">
          {competitorCount > 0
            ? `${competitorCount} competitor site${competitorCount === 1 ? "" : "s"} · ~${estimateMinutes} minutes`
            : "Add competitor websites above first"}
        </p>
        {statusLine && (
          <p className={`mt-2 text-xs ${busy ? "text-blue-700" : status.status === "done" ? "text-emerald-700" : "text-gray-500"}`}>
            {statusLine}
          </p>
        )}
        {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
      </div>
      <button
        type="button"
        onClick={onStart}
        disabled={busy || competitorCount === 0}
        className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-violet-300 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
      >
        <RefreshCw className={`h-4 w-4 ${busy ? "animate-spin" : ""}`} />
        {buttonLabel}
      </button>
    </div>
  );
}
