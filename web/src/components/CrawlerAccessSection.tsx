import { useEffect, useState } from "react";
import { Check, Info, X } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { fetchScoreBreakdown } from "../api/client";
import { SampleScriptsLinkButton, TextWithSampleScriptsLink } from "./SampleScriptsLink";

type Breakdown = Awaited<ReturnType<typeof fetchScoreBreakdown>>;

export function CrawlerAccessSection({
  auditDirOrSlug,
  onNavigate,
  breakdown: breakdownProp,
}: {
  auditDirOrSlug: string;
  onNavigate?: (sectionId: string) => void;
  breakdown?: Breakdown | null;
}) {
  const [fetched, setFetched] = useState<Breakdown["crawler_access"] | null>(null);
  const [loading, setLoading] = useState(!breakdownProp);

  useEffect(() => {
    if (breakdownProp) {
      setLoading(false);
      return;
    }
    setLoading(true);
    fetchScoreBreakdown(auditDirOrSlug)
      .then((response) => setFetched(response.crawler_access ?? null))
      .catch(() => setFetched(null))
      .finally(() => setLoading(false));
  }, [auditDirOrSlug, breakdownProp]);

  const data = breakdownProp ? breakdownProp.crawler_access ?? null : fetched;

  if (loading) {
    return <PageLoading />;
  }

  const rows = data?.rows ?? [];
  const hasMisaligned = rows.some((row) => row.aligned === false);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">AI crawler access</h2>
        <p className="mt-1 text-sm text-gray-500">
          <TextWithSampleScriptsLink
            text="Which search and AI crawlers can access the site’s public pages under the current robots.txt rules."
            onNavigate={onNavigate}
          />
        </p>
      </div>

      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
        {rows.length ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[900px] text-sm">
              <thead>
                <tr className="border-b border-gray-100 bg-gray-50">
                  {["Crawler", "Tier", "GEO recommendation", "Reason", "Your robots", "Aligned"].map((header) => (
                    <th key={header} className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">{header}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.crawler} className="border-b border-gray-100 last:border-0">
                    <td className="px-5 py-3 font-mono text-xs font-semibold text-[#0d0d0d]">{row.crawler}</td>
                    <td className="px-5 py-3 text-xs text-gray-500">{row.tier}</td>
                    <td className="px-5 py-3">
                      <span className={`rounded-full px-2 py-1 text-[10px] font-bold ${
                        row.recommendation === "ALLOW"
                          ? "bg-emerald-50 text-emerald-700"
                          : row.recommendation === "BLOCK"
                            ? "bg-red-50 text-red-700"
                            : "bg-gray-100 text-gray-600"
                      }`}>
                        {row.recommendation}
                      </span>
                    </td>
                    <td className="max-w-[320px] px-5 py-3 text-xs leading-relaxed text-gray-500">{row.reason}</td>
                    <td className={`px-5 py-3 text-xs font-semibold ${row.can_fetch ? "text-emerald-600" : "text-red-600"}`}>
                      {row.can_fetch ? "Can fetch" : "Cannot fetch"}
                    </td>
                    <td className="px-5 py-3">
                      {row.aligned == null ? (
                        <span className="text-gray-300">—</span>
                      ) : row.aligned ? (
                        <Check className="h-4 w-4 text-emerald-500" />
                      ) : (
                        <X className="h-4 w-4 text-red-500" />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="flex items-center gap-3 px-5 py-8 text-sm text-gray-400">
            <Info className="h-5 w-5 shrink-0" />
            <span>
              <TextWithSampleScriptsLink
                text="No robots.txt crawler data is available for this audit."
                onNavigate={onNavigate}
              />
            </span>
          </div>
        )}
      </div>

      {onNavigate && (hasMisaligned || rows.length > 0) && (
        <div className="rounded-2xl border border-violet-100 bg-violet-50 px-5 py-4 text-xs leading-relaxed text-violet-900">
          <span className="font-semibold">Need a starter policy?</span>{" "}
          Copy a merged <code className="text-[11px]">robots.txt</code> from{" "}
          <SampleScriptsLinkButton onNavigate={onNavigate} className="text-xs" />
          {hasMisaligned
            ? " when crawler rules are out of line with the GEO recommendation."
            : " to review allow/block rules for AI agents."}
        </div>
      )}
    </div>
  );
}
