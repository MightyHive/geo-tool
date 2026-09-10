import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, ExternalLink, Loader2 } from "lucide-react";
import {
  createPageAudit,
  fetchPageAudits,
} from "../api/client";
import type { PageAuditListItem, PageAuditScores } from "../types";
import {
  formatReportScore,
  scoreColor,
  scoreLabel,
  scoreTone,
} from "../lib/reportScore";
import { cn } from "../lib/utils";

const ACTIVE_STATUSES = new Set(["queued", "running"]);
const POLL_MS = 2500;

function apiErrorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "The page audit could not be started.";
  try {
    const parsed = JSON.parse(error.message) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
  } catch {
    /* body is plain text */
  }
  return error.message || "The page audit could not be started.";
}

export function PageAuditScoreCards({
  scores,
}: {
  scores?: PageAuditScores | null;
}) {
  const pillars = [
    { key: "ai_visibility", label: "AI visibility", weight: "40%" },
    { key: "technical_setup", label: "Technical setup", weight: "30%" },
    { key: "content_quality", label: "Content quality", weight: "30%" },
    { key: "overall", label: "Overall", weight: "—" },
  ] as const;

  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {pillars.map((pillar) => {
        const value = scores?.[pillar.key];
        const numeric = typeof value === "number" ? value : null;
        const tone = numeric == null ? null : scoreTone(numeric);
        return (
          <div
            key={pillar.key}
            className="rounded-2xl border border-gray-200 bg-white p-4"
          >
            <p className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
              {pillar.label}
            </p>
            {numeric == null ? (
              <p className="mt-2 text-2xl font-bold text-gray-300">—</p>
            ) : (
              <>
                <p className="mt-2 text-2xl font-bold text-[#0d0d0d]">
                  {formatReportScore(numeric)}
                  <span className="ml-1 text-sm font-normal text-gray-400">/100</span>
                </p>
                <p
                  className="text-xs font-semibold"
                  style={{ color: tone ? scoreColor(tone) : undefined }}
                >
                  {scoreLabel(numeric)}
                </p>
              </>
            )}
            <p className="mt-2 text-[11px] text-gray-400">Weight {pillar.weight}</p>
          </div>
        );
      })}
    </div>
  );
}

export function PageAuditList({
  items,
  parentId,
}: {
  items: PageAuditListItem[];
  parentId?: string;
}) {
  if (!items.length) {
    return (
      <p className="text-sm text-gray-500">No single-page audits yet.</p>
    );
  }
  return (
    <ul className="divide-y divide-gray-100 rounded-xl border border-gray-200 bg-white">
      {items.map((item) => {
        const parent = parentId || item.parent_audit_id;
        const href =
          parent && item.id
            ? `/page-audits/${encodeURIComponent(parent)}/${encodeURIComponent(item.id)}`
            : undefined;
        const overall = item.scores?.overall;
        return (
          <li key={`${parent}-${item.id}`}>
            {href ? (
              <Link
                to={href}
                className="flex items-start justify-between gap-3 px-4 py-3 hover:bg-gray-50"
              >
                <PageAuditListBody item={item} overall={overall} />
              </Link>
            ) : (
              <div className="flex items-start justify-between gap-3 px-4 py-3">
                <PageAuditListBody item={item} overall={overall} />
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function PageAuditListBody({
  item,
  overall,
}: {
  item: PageAuditListItem;
  overall?: number | null;
}) {
  return (
    <>
      <div className="min-w-0">
        <p className="truncate text-sm font-semibold text-gray-900">
          {item.title || item.url}
        </p>
        <p className="truncate text-xs text-gray-500">{item.url}</p>
        <p className="mt-1 text-[11px] uppercase tracking-wide text-gray-400">
          {item.status === "running" && item.stage === "generating_prompts"
            ? "Generating prompts"
            : item.status === "running" && item.stage === "probing"
              ? "Running page probes"
              : item.status}
          {item.parent_brand_name ? ` · ${item.parent_brand_name}` : ""}
        </p>
        <p className="mt-1 text-xs font-semibold text-violet-700">Open full page audit</p>
      </div>
      <div className="shrink-0 text-right">
        {typeof overall === "number" ? (
          <p className="text-lg font-bold text-[#0d0d0d]">
            {formatReportScore(overall)}
          </p>
        ) : ACTIVE_STATUSES.has(item.status) ? (
          <Loader2 className="h-4 w-4 animate-spin text-gray-400" />
        ) : (
          <span className="text-sm text-gray-300">—</span>
        )}
      </div>
    </>
  );
}

export function SinglePageAuditSection({
  auditDirOrSlug,
}: {
  auditDirOrSlug: string;
}) {
  const [url, setUrl] = useState("");
  const [items, setItems] = useState<PageAuditListItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    return fetchPageAudits(auditDirOrSlug)
      .then((response) => setItems(response.items || []))
      .catch((err) => {
        setError(apiErrorMessage(err));
      });
  }, [auditDirOrSlug]);

  useEffect(() => {
    setLoading(true);
    void load().finally(() => setLoading(false));
  }, [load]);

  const anyActive = items.some((item) => ACTIVE_STATUSES.has(item.status));
  useEffect(() => {
    if (!anyActive) return;
    const id = window.setInterval(() => {
      void load();
    }, POLL_MS);
    return () => window.clearInterval(id);
  }, [anyActive, load]);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await createPageAudit(auditDirOrSlug, url.trim());
      setUrl("");
      await load();
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const latestDone = items.find((item) => item.status === "done");

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold text-[#0d0d0d]">Single-page audit</h2>
        <p className="mt-1 max-w-2xl text-sm text-gray-600">
          Score one URL from this site for AI visibility, technical setup, and
          content quality. Site-wide signals from the master audit are reused
          and labelled as inherited. Page-specific prompts are generated and
          probed automatically before the score is finalised.
        </p>
      </div>

      <form onSubmit={onSubmit} className="flex flex-col gap-3 sm:flex-row">
        <input
          type="url"
          required
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://example.com/page"
          className="flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm"
        />
        <button type="submit" className="btn-primary inline-flex" disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          Run page audit
        </button>
      </form>

      {error ? (
        <div className={cn("alert-error flex items-start gap-2")}>
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {latestDone?.scores ? (
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            Latest result
          </p>
          <PageAuditScoreCards scores={latestDone.scores} />
        </div>
      ) : null}

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold text-gray-800">
            Page audits for this report
          </h3>
          <Link
            to="/page-audits"
            className="inline-flex items-center gap-1 text-xs font-semibold text-violet-700 hover:text-violet-900"
          >
            View all <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
        {loading ? (
          <p className="text-sm text-gray-500">Loading page audits…</p>
        ) : (
          <PageAuditList items={items} parentId={auditDirOrSlug} />
        )}
      </div>
    </div>
  );
}
