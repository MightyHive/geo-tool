import { useState, useRef, useEffect } from "react";
import { Download, FileText, Code, ChevronDown } from "lucide-react";
import type { ReportMeta } from "../types";
import { scoreColor, scoreLabel } from "../lib/reportScore";

interface ReportHeaderProps {
  meta: ReportMeta;
  downloadUrl?: string;
  allPagesHtmlUrl?: string;
}

export function ReportHeader({ meta, downloadUrl, allPagesHtmlUrl }: ReportHeaderProps) {
  const score = meta.overall_score;
  const tone = meta.score_tone ?? "yellow";
  const gaugeColor = scoreColor(tone);
  const label = meta.overall_label || (score != null ? scoreLabel(score) : "");
  const deg = score != null ? Math.max(0, Math.min(360, score * 3.6)) : 0;

  const faviconUrl = meta.favicon_url?.trim() || "";

  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    }
    if (dropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [dropdownOpen]);

  const ring = 128;
  const inner = 96;
  const hasDownload = downloadUrl || allPagesHtmlUrl;

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
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide bg-white/15 text-white">
                Full audit
              </span>
              {meta.generated_at ? (
                <span className="inline-block px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide bg-white/10 text-white/80">
                  {meta.generated_at}
                </span>
              ) : null}

              {hasDownload ? (
                <div className="relative" ref={dropdownRef}>
                  <button
                    onClick={() => setDropdownOpen((v) => !v)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold uppercase tracking-wide bg-white/10 text-white/80 hover:bg-white/20 hover:text-white transition-colors select-none"
                  >
                    <Download className="w-3 h-3" strokeWidth={2.5} />
                    Download
                    <ChevronDown
                      className="w-3 h-3 transition-transform duration-150"
                      style={{ transform: dropdownOpen ? "rotate(180deg)" : "rotate(0deg)" }}
                      strokeWidth={2.5}
                    />
                  </button>

                  {dropdownOpen && (
                    <div className="absolute top-full left-0 mt-1.5 bg-white rounded-xl shadow-xl border border-gray-200/80 py-1 z-50 min-w-[160px] overflow-hidden">
                      {downloadUrl && (
                        <a
                          href={downloadUrl}
                          download
                          onClick={() => setDropdownOpen(false)}
                          className="flex items-center gap-2.5 px-3.5 py-2.5 text-sm text-gray-700 hover:bg-gray-50 no-underline transition-colors"
                        >
                          <FileText className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                          <span className="font-medium">Download PDF</span>
                        </a>
                      )}
                      {allPagesHtmlUrl && (
                        <a
                          href={allPagesHtmlUrl}
                          download
                          onClick={() => setDropdownOpen(false)}
                          className="flex items-center gap-2.5 px-3.5 py-2.5 text-sm text-gray-700 hover:bg-gray-50 no-underline transition-colors"
                        >
                          <Code className="w-3.5 h-3.5 text-gray-400 shrink-0" />
                          <span className="font-medium">Download HTML</span>
                        </a>
                      )}
                    </div>
                  )}
                </div>
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
                  <span className="text-2xl font-bold leading-none">{score.toFixed(1)}</span>
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
