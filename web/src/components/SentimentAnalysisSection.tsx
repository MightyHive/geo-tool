import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { fetchPromptSentiment } from "../api/client";
import type { CategorySentiment, PromptSentimentAnalysis } from "../types";

function sentimentTone(s: string): "green" | "blue" | "yellow" | "red" | "neutral" {
  const v = (s || "").toLowerCase();
  if (v.includes("positive")) return "green";
  if (v.includes("negative")) return "red";
  if (v.includes("mixed")) return "yellow";
  if (v.includes("neutral")) return "neutral";
  return "blue";
}

function SentimentBadge({ label }: { label: string }) {
  const tone = sentimentTone(label);
  const styles: Record<string, string> = {
    green: "bg-emerald-50 text-emerald-800 border-emerald-200",
    blue: "bg-sky-50 text-sky-800 border-sky-200",
    yellow: "bg-amber-50 text-amber-900 border-amber-200",
    red: "bg-red-50 text-red-800 border-red-200",
    neutral: "bg-gray-100 text-gray-700 border-gray-200",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${styles[tone]}`}
    >
      {label}
    </span>
  );
}

function CategorySentimentCard({ row }: { row: CategorySentiment }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white/90">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <h5 className="font-medium text-brand-dark">{row.category}</h5>
        <SentimentBadge label={row.sentiment} />
      </div>
      <p className="text-sm text-gray-600 leading-relaxed">{row.summary}</p>
    </div>
  );
}

export function SentimentAnalysisSection({
  auditDirOrSlug,
  enabled,
  refreshKey = 0,
}: {
  auditDirOrSlug: string;
  enabled: boolean;
  refreshKey?: number;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<PromptSentimentAnalysis | null>(null);

  useEffect(() => {
    if (!enabled) {
      setData(null);
      setError(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPromptSentiment(auditDirOrSlug)
      .then((res) => {
        if (cancelled) return;
        if (res.available && res.sentiment) {
          setData(res.sentiment);
          setError(null);
        } else {
          setData(null);
          setError(res.error || "Sentiment analysis is not available.");
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setData(null);
          setError(e instanceof Error ? e.message : "Failed to load sentiment analysis");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [auditDirOrSlug, enabled, refreshKey]);

  if (!enabled) return null;

  return (
    <section className="mt-8 pt-6 border-t border-gray-200" aria-labelledby="sentiment-heading">
      <h4 id="sentiment-heading" className="text-base font-semibold text-brand-dark mb-1">
        Sentiment analysis
      </h4>
      <p className="text-sm text-gray-600 mb-4">
        Gemini reviews live probe replies for how favourably assistants portray your brand, overall and
        by prompt category.
      </p>

      {loading ? (
        <p className="text-sm text-gray-500 flex items-center gap-2" role="status">
          <Loader2 className="w-4 h-4 animate-spin text-brand-accent" />
          Analysing reply sentiment…
        </p>
      ) : null}

      {error && !loading ? <div className="alert-error text-sm">{error}</div> : null}

      {data && !loading ? (
        <div className="space-y-4">
          <div className="rounded-xl border border-gray-200 bg-[#faf9f7] p-4">
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <span className="text-sm font-medium text-gray-600">Overall</span>
              <SentimentBadge label={data.overall_sentiment} />
            </div>
            <p className="text-sm text-gray-700 leading-relaxed">{data.overall_summary}</p>
          </div>

          {data.by_category?.length ? (
            <div>
              <h5 className="text-sm font-semibold text-brand-dark mb-3">By prompt category</h5>
              <div className="grid gap-3 sm:grid-cols-2">
                {data.by_category.map((row) => (
                  <CategorySentimentCard key={row.category} row={row} />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
