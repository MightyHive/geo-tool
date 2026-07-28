import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ExternalLink, Info } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { fetchScoreBreakdown, type ContentEvidenceExample, type ContentQualityDetails } from "../api/client";
import { scoreColor, scoreTone, formatReportScore } from "../lib/reportScore";

function useContentDetails(auditDirOrSlug: string) {
  const [details, setDetails] = useState<ContentQualityDetails | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    fetchScoreBreakdown(auditDirOrSlug)
      .then((response) => setDetails(response.content_quality_details ?? null))
      .catch(() => setDetails(null))
      .finally(() => setLoading(false));
  }, [auditDirOrSlug]);
  return { details, loading };
}

function Loading() {
  return <PageLoading />;
}

function ScorePill({ score }: { score: number }) {
  const color = scoreColor(scoreTone(score));
  return (
    <span className="inline-flex min-w-16 justify-center rounded-full px-3 py-1 text-sm font-bold" style={{ background: `${color}18`, color }}>
      {formatReportScore(score)}/100
    </span>
  );
}

function EvidenceList({ examples, emptyMessage }: { examples: ContentEvidenceExample[]; emptyMessage: string }) {
  if (!examples.length) {
    return (
      <div className="flex gap-2 rounded-lg border border-dashed border-gray-200 bg-gray-50 px-3 py-2.5 text-xs leading-relaxed text-gray-500">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {emptyMessage}
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {examples.map((example, index) => (
        <div key={`${example.url}-${index}`} className="rounded-lg border border-gray-100 bg-gray-50 p-3">
          <a href={example.url} target="_blank" rel="noopener noreferrer" className="mb-1 flex items-center gap-1 text-[11px] font-semibold text-blue-600 hover:underline">
            <span className="truncate">{example.title || example.url}</span>
            <ExternalLink className="h-3 w-3 shrink-0" />
          </a>
          <blockquote className="border-l-2 border-gray-200 pl-2 text-xs leading-relaxed text-gray-600">
            {example.snippet}
          </blockquote>
        </div>
      ))}
    </div>
  );
}

export function EeatSignalsSection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const { details, loading } = useContentDetails(auditDirOrSlug);
  if (loading) return <Loading />;
  const rows = details?.eeat ?? [];
  const aggregate = details?.components.find((component) => component.key === "eeat");
  const gemini = details?.gemini_overlay;
  const geminiApplied = Boolean(gemini?.available && gemini.status === "applied");
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">E-E-A-T Signals</h2>
        <p className="mt-1 text-sm text-gray-500">
          {geminiApplied
            ? "Gemini qualitative scores on sampled pages (source-language analysis; English findings). Schema and formatting stay crawl-based."
            : "Direct 0–100 content-fit scores with examples from sampled site pages."}
        </p>
        {geminiApplied && gemini?.finding_summary ? (
          <p className="mt-2 text-xs leading-relaxed text-gray-400">{gemini.finding_summary}</p>
        ) : null}
      </div>
      {aggregate && (
        <div className="flex items-center gap-4 rounded-2xl border border-gray-200 bg-white p-5">
          <ScorePill score={aggregate.score} />
          <div>
            <p className="text-sm font-semibold text-[#0d0d0d]">Combined E-E-A-T score</p>
            <p className="mt-0.5 text-xs leading-relaxed text-gray-500">{aggregate.finding_summary}</p>
          </div>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {rows.map((row) => (
          <article key={row.name} className="rounded-2xl border border-gray-200 bg-white p-5">
            <div className="mb-3 flex items-start justify-between gap-4">
              <div>
                <h3 className="text-sm font-bold text-[#0d0d0d]">{row.name}</h3>
                <p className="mt-0.5 text-xs font-medium text-gray-400">{row.tagline}</p>
              </div>
              <ScorePill score={row.score} />
            </div>
            <p className="text-xs leading-relaxed text-gray-600">{row.what_it_means}</p>
            <p className="my-3 text-[11px] leading-relaxed text-gray-400">{row.how_scored}</p>
            <EvidenceList examples={row.evidence ?? []} emptyMessage={row.evidence_note} />
          </article>
        ))}
      </div>
      {!rows.length && <EvidenceList examples={[]} emptyMessage="No E-E-A-T evidence is available for this audit." />}
    </div>
  );
}

export function ContentStructureAnswerabilitySection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const { details, loading } = useContentDetails(auditDirOrSlug);
  if (loading) return <Loading />;
  const rows = details?.structure_answerability ?? [];
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Content Structure &amp; Answerability</h2>
        <p className="mt-1 text-sm text-gray-500">How distinctive, answer-ready and extractable the sampled content is for AI systems.</p>
      </div>
      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="w-[210px] px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">Criterion</th>
                <th className="w-[110px] px-5 py-3 text-center text-[10px] font-semibold uppercase tracking-wide text-gray-400">Score</th>
                <th className="w-[260px] px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">What this measures</th>
                <th className="px-5 py-3 text-left text-[10px] font-semibold uppercase tracking-wide text-gray-400">Example content</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.key} className="border-b border-gray-100 align-top last:border-0">
                  <td className="px-5 py-4 font-semibold text-[#0d0d0d]">{row.title}</td>
                  <td className="px-5 py-4 text-center"><ScorePill score={row.score} /></td>
                  <td className="px-5 py-4 text-xs leading-relaxed text-gray-500">{row.description}</td>
                  <td className="px-5 py-4"><EvidenceList examples={row.examples ?? []} emptyMessage={row.empty_message} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function SchemaEntityMarkupSection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const { details, loading } = useContentDetails(auditDirOrSlug);
  if (loading) return <Loading />;
  const section = details?.schema_entity;
  if (!section) return <EvidenceList examples={[]} emptyMessage="No schema evidence is available for this audit." />;
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Schema &amp; Entity Markup</h2>
        <p className="mt-1 text-sm leading-relaxed text-gray-500">{section.summary}</p>
      </div>
      <div className="flex items-center gap-4 rounded-2xl border border-gray-200 bg-white p-5">
        <ScorePill score={section.score} />
        <p className="text-sm text-gray-600">Combined JSON-LD coverage, schema depth and entity-linking score.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border border-gray-200 bg-white p-5">
          <h3 className="mb-3 text-sm font-bold text-[#0d0d0d]">What is working well</h3>
          <ul className="space-y-2">
            {section.strengths.map((finding) => <li key={finding} className="flex gap-2 text-xs text-gray-600"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />{finding}</li>)}
            {!section.strengths.length && <li className="text-xs italic text-gray-400">No positive schema finding recorded.</li>}
          </ul>
        </div>
        <div className="rounded-2xl border border-gray-200 bg-white p-5">
          <h3 className="mb-3 text-sm font-bold text-[#0d0d0d]">What needs work</h3>
          <ul className="space-y-2">
            {section.improvements.map((finding) => <li key={finding} className="flex gap-2 text-xs text-gray-600"><AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />{finding}</li>)}
            {!section.improvements.length && <li className="text-xs italic text-gray-400">No material schema gap recorded.</li>}
          </ul>
        </div>
      </div>
      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-gray-100 bg-gray-50"><th className="px-5 py-3 text-left text-[10px] uppercase tracking-wide text-gray-400">Page</th><th className="px-5 py-3 text-left text-[10px] uppercase tracking-wide text-gray-400">Schema types</th><th className="px-5 py-3 text-center text-[10px] uppercase tracking-wide text-gray-400">Blocks</th><th className="px-5 py-3 text-center text-[10px] uppercase tracking-wide text-gray-400">sameAs</th></tr></thead>
          <tbody>
            {section.evidence.map((item) => <tr key={item.url} className="border-b border-gray-100 last:border-0"><td className="px-5 py-3"><a href={item.url} target="_blank" rel="noopener noreferrer" className="text-xs font-semibold text-blue-600 hover:underline">{item.title}</a></td><td className="px-5 py-3 text-xs text-gray-500">{item.types.join(", ") || "Unclassified JSON-LD"}</td><td className="px-5 py-3 text-center text-xs">{item.blocks}</td><td className="px-5 py-3 text-center text-xs">{item.same_as_count}</td></tr>)}
            {!section.evidence.length && <tr><td colSpan={4} className="px-5 py-8 text-center text-sm text-gray-400">No JSON-LD was found on sampled pages.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function BrandVisibilityAuthoritySection({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const { details, loading } = useContentDetails(auditDirOrSlug);
  if (loading) return <Loading />;
  const section = details?.brand_visibility_authority;
  if (!section) return <EvidenceList examples={[]} emptyMessage="No brand visibility scan is available for this audit." />;
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Brand Visibility &amp; Authority</h2>
        <p className="mt-1 text-sm text-gray-500">Third-party presence signals used by AI systems to corroborate the brand.</p>
      </div>
      <div className="flex items-center gap-4 rounded-2xl border border-gray-200 bg-white p-5">
        <ScorePill score={section.score} />
        <p className="text-sm text-gray-600">{section.brand_query ? `Visibility signals for “${section.brand_query}”.` : "Off-site brand visibility score."}</p>
      </div>
      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-sm">
            <thead><tr className="border-b border-gray-100 bg-gray-50"><th className="px-5 py-3 text-left text-[10px] uppercase tracking-wide text-gray-400">Platform</th><th className="px-5 py-3 text-center text-[10px] uppercase tracking-wide text-gray-400">Presence</th><th className="px-5 py-3 text-left text-[10px] uppercase tracking-wide text-gray-400">Channel / page</th><th className="px-5 py-3 text-left text-[10px] uppercase tracking-wide text-gray-400">Impact on AI visibility</th></tr></thead>
            <tbody>
              {section.rows.map((row, index) => (
                <tr key={`${row.platform}-${index}`} className="border-b border-gray-100 last:border-0">
                  <td className="px-5 py-3 font-semibold text-[#0d0d0d]">{row.platform ?? "—"}</td>
                  <td className="px-5 py-3 text-center"><span className={`rounded-full px-2.5 py-1 text-[10px] font-bold ${row.present ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"}`}>{row.present ? "Likely yes" : "No / unclear"}</span></td>
                  <td className="px-5 py-3 text-xs text-gray-500">{row.status ?? "—"} {row.url && <a href={row.url} target="_blank" rel="noopener noreferrer" className="ml-1 text-blue-600 hover:underline">Open</a>}</td>
                  <td className="px-5 py-3 text-xs text-gray-500">{row.impact ?? "—"}</td>
                </tr>
              ))}
              {!section.rows.length && <tr><td colSpan={4} className="px-5 py-8 text-center text-sm text-gray-400">No platform rows are available.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
