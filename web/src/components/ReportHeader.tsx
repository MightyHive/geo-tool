import type { ReportMeta } from "../types";
import { formatReportScore, scoreColor, scoreLabel } from "../lib/reportScore";
import { ReportDownloadMenu } from "./ReportDownloadMenu";

interface ReportHeaderProps {
  meta: ReportMeta;
  auditDirOrSlug?: string;
  downloadUrl?: string;
  allPagesHtmlUrl?: string;
  pdfFilename?: string;
  htmlFilename?: string;
}

export function ReportHeader({
  meta,
  auditDirOrSlug,
  downloadUrl,
  allPagesHtmlUrl,
  pdfFilename,
  htmlFilename,
}: ReportHeaderProps) {
  const score = meta.overall_score;
  const tone = meta.score_tone ?? "yellow";
  const gaugeColor = scoreColor(tone);
  const label = meta.overall_label || (score != null ? scoreLabel(score) : "");
  const deg = score != null ? Math.max(0, Math.min(360, score * 3.6)) : 0;

  const faviconUrl = meta.favicon_url?.trim() || "";

  const ring = 128;
  const inner = 96;
  const hasDownload = Boolean(auditDirOrSlug || downloadUrl || allPagesHtmlUrl);

  return (
    <header className="report-site-header shrink-0 bg-[#0d0d0d] text-white border-b border-white/10">
      <div className="max-w-[1200px] mx-auto px-5 py-5 md:py-6">
        <div className="flex flex-wrap items-center justify-between gap-6 md:gap-8">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2.5 mb-1 min-w-0">
              {faviconUrl ? (
                <img
                  src={faviconUrl}
                  alt=""
                  width={28}
                  height={28}
                  className="rounded shrink-0 bg-white/10"
                  loading="lazy"
                />
              ) : null}
              <h1 className="text-xl md:text-[1.45rem] font-semibold tracking-tight truncate">
                {meta.brand_name?.trim() || "GEO Audit Report"}
              </h1>
            </div>
            {meta.base_url ? (
              <p className="text-sm text-white/70 mb-2 break-all leading-snug">{meta.base_url}</p>
            ) : null}
            {meta.industry ? (
              <p className="text-xs text-white/88 mb-2 leading-snug">Industry: {meta.industry}</p>
            ) : null}
            <div className="flex flex-wrap items-start gap-2">
              <span className="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide bg-white/15 text-white">
                Full audit
              </span>
              {meta.generated_at ? (
                <span className="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide bg-white/10 text-white/80">
                  {meta.generated_at}
                </span>
              ) : null}

              {hasDownload ? (
                <ReportDownloadMenu
                  variant="header"
                  label="Download"
                  auditDirOrSlug={auditDirOrSlug}
                  pdfUrl={downloadUrl}
                  htmlUrl={allPagesHtmlUrl}
                  pdfFilename={pdfFilename}
                  htmlFilename={htmlFilename}
                />
              ) : null}
            </div>
          </div>
          {score != null ? (
            <div
              className="relative shrink-0"
              style={{ width: ring, height: ring }}
              aria-label="Overall score gauge"
            >
              <div
                className="rounded-full flex items-center justify-center"
                style={{
                  width: ring,
                  height: ring,
                  background: `conic-gradient(${gaugeColor} 0deg, ${gaugeColor} ${deg}deg, rgba(255,255,255,0.12) ${deg}deg, rgba(255,255,255,0.12) 360deg)`,
                }}
              >
                <div
                  className="rounded-full bg-[#0d0d0d] flex flex-col items-center justify-center"
                  style={{ width: inner, height: inner }}
                >
                  <span className="text-2xl font-bold leading-none">{formatReportScore(score)}</span>
                  <span className="text-xs font-semibold mt-0.5" style={{ color: gaugeColor }}>
                    {label}
                  </span>
                  <span className="text-[10px] text-white/50 mt-0.5">/ 100</span>
                </div>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </header>
  );
}
