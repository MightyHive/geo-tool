import { useEffect, useRef, useState } from "react";
import { ChevronDown, Code, Download, FileText, Loader2 } from "lucide-react";
import {
  downloadFileWithProgress,
  downloadPdfViaExportJob,
  downloadProgressLabel,
} from "../lib/downloadWithProgress";

interface DownloadMenuProps {
  /** Visual variant for header (dark) vs section toolbar (light). */
  variant?: "header" | "section";
  /** When set, PDF uses the async export job instead of a sync request. */
  auditDirOrSlug?: string;
  pdfSection?: string;
  /** Legacy direct PDF URL (sync). Used only when auditDirOrSlug is omitted. */
  pdfUrl?: string;
  htmlUrl?: string;
  pdfFilename?: string;
  htmlFilename?: string;
  /** Button label next to the icon. */
  label?: string;
}

function ProgressBlock({
  percent,
  kind,
  variant,
  error,
}: {
  percent: number;
  kind: "pdf" | "html" | null;
  variant: "header" | "section";
  error: string | null;
}) {
  if (error) {
    return (
      <p
        className={
          variant === "header"
            ? "mt-1 max-w-[11rem] text-[10px] leading-snug text-red-300"
            : "mt-1 max-w-[12rem] text-[10px] leading-snug text-red-600"
        }
        role="alert"
      >
        {error}
      </p>
    );
  }
  if (kind == null) return null;
  const track = variant === "header" ? "bg-white/20" : "bg-gray-200";
  const fill = variant === "header" ? "bg-white" : "bg-gray-800";
  const text = variant === "header" ? "text-white/70" : "text-gray-500";

  return (
    <div className="mt-1.5 w-[11rem]" aria-live="polite">
      <div className={`mb-1 flex items-center gap-1 ${text}`}>
        <Loader2 className="h-2.5 w-2.5 shrink-0 animate-spin" aria-hidden />
        <span className="text-[10px] font-medium leading-none">
          {downloadProgressLabel(kind)}
        </span>
      </div>
      <div className={`h-1 overflow-hidden rounded-full ${track}`}>
        <div
          className={`h-full rounded-full transition-[width] duration-300 ease-out ${fill}`}
          style={{ width: `${Math.max(4, Math.min(100, percent))}%` }}
        />
      </div>
    </div>
  );
}

export function ReportDownloadMenu({
  variant = "section",
  auditDirOrSlug,
  pdfSection,
  pdfUrl,
  htmlUrl,
  pdfFilename = "geo-report.pdf",
  htmlFilename = "geo-report.html",
  label = "Download",
}: DownloadMenuProps) {
  const [open, setOpen] = useState(false);
  const [busyKind, setBusyKind] = useState<"pdf" | "html" | null>(null);
  const [percent, setPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const pdfEnabled = Boolean(auditDirOrSlug || pdfUrl);

  useEffect(() => {
    function onDocClick(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    if (open) document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  if (!pdfEnabled && !htmlUrl) return null;

  const busy = busyKind != null;

  async function startDownload(kind: "pdf" | "html") {
    if (busy) return;
    setOpen(false);
    setError(null);
    setBusyKind(kind);
    setPercent(4);
    try {
      if (kind === "pdf") {
        if (auditDirOrSlug) {
          await downloadPdfViaExportJob(auditDirOrSlug, pdfFilename, {
            section: pdfSection,
            onProgress: (update) => setPercent(update.percent),
          });
        } else if (pdfUrl) {
          await downloadFileWithProgress(pdfUrl, pdfFilename, (update) => {
            setPercent(update.percent);
          });
        } else {
          throw new Error("PDF download is not available");
        }
      } else {
        if (!htmlUrl) throw new Error("HTML download is not available");
        await downloadFileWithProgress(htmlUrl, htmlFilename, (update) => {
          setPercent(update.percent);
        });
      }
      setPercent(100);
      window.setTimeout(() => {
        setBusyKind(null);
        setPercent(0);
      }, 600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Download failed");
      setBusyKind(null);
      setPercent(0);
    }
  }

  const isHeader = variant === "header";

  return (
    <div className="relative flex flex-col items-start" ref={ref}>
      <button
        type="button"
        disabled={busy}
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={
          isHeader
            ? "inline-flex items-center gap-1.5 rounded-full bg-white/10 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-white/80 transition-colors hover:bg-white/20 hover:text-white select-none disabled:opacity-60"
            : "inline-flex items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-gray-600 shadow-sm hover:bg-gray-50 hover:text-gray-900 disabled:opacity-60"
        }
      >
        {busy ? (
          <Loader2 className={isHeader ? "h-3 w-3 animate-spin" : "h-3.5 w-3.5 animate-spin"} strokeWidth={2.5} />
        ) : (
          <Download className={isHeader ? "h-3 w-3" : "h-3.5 w-3.5"} strokeWidth={2.25} />
        )}
        {label}
        <ChevronDown
          className={`${isHeader ? "h-3 w-3" : "h-3.5 w-3.5"} transition-transform duration-150`}
          style={{ transform: open ? "rotate(180deg)" : "rotate(0deg)" }}
          strokeWidth={2.5}
        />
      </button>

      <ProgressBlock percent={percent} kind={busyKind} variant={variant} error={error} />

      {open && !busy ? (
        <div
          role="menu"
          className={
            isHeader
              ? "absolute top-full left-0 z-50 mt-1.5 min-w-[160px] overflow-hidden rounded-xl border border-gray-200/80 bg-white py-1 shadow-xl"
              : "absolute right-0 z-40 mt-1.5 min-w-[168px] overflow-hidden rounded-xl border border-gray-200/80 bg-white py-1 shadow-xl"
          }
        >
          {pdfEnabled ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => void startDownload("pdf")}
              className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-gray-700 transition-colors hover:bg-gray-50"
            >
              <FileText className="h-3.5 w-3.5 shrink-0 text-gray-400" />
              <span className="font-medium">Download PDF</span>
            </button>
          ) : null}
          {htmlUrl ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => void startDownload("html")}
              className="flex w-full items-center gap-2.5 px-3.5 py-2.5 text-left text-sm text-gray-700 transition-colors hover:bg-gray-50"
            >
              <Code className="h-3.5 w-3.5 shrink-0 text-gray-400" />
              <span className="font-medium">Download HTML</span>
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
