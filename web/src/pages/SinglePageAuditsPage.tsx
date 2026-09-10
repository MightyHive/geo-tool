import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Calendar, Eye, FileSearch, Loader2 } from "lucide-react";
import { fetchAllPageAudits } from "../api/client";
import { PageLoading } from "../components/PageLoading";
import { Card, CardDescription, CardTitle } from "../components/ui/Card";
import { PageHeader } from "../components/PageHeader";
import { formatReportScore } from "../lib/reportScore";
import type { PageAuditListItem } from "../types";

const ACTIVE_STATUSES = new Set(["queued", "running"]);

export function SinglePageAuditsPage() {
  const navigate = useNavigate();
  const [items, setItems] = useState<PageAuditListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchAllPageAudits()
      .then((response) => setItems(response.items || []))
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Could not load page audits"),
      )
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="page-container">
        <PageLoading label="Loading page audits…" />
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader
        title="Single-page audits"
        description="Isolated URL scores created from a master audit. They reuse site-wide signals and do not re-run prompt probes."
      />

      {error ? <div className="alert-error">{error}</div> : null}

      {!items.length && !error ? (
        <Card>
          <FileSearch className="mb-4 h-12 w-12 text-gray-400" />
          <CardTitle>No single-page audits yet</CardTitle>
          <CardDescription>
            Open a master report and use Workshop → Single-page audit to score a URL.
          </CardDescription>
          <Link to="/audits" className="btn-primary mt-2 inline-flex">
            Existing audits
          </Link>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {items.map((item, index) => {
            const parent = item.parent_audit_id;
            const href =
              parent && item.id
                ? `/page-audits/${encodeURIComponent(parent)}/${encodeURIComponent(item.id)}`
                : undefined;
            const overall = item.scores?.overall;
            const running = ACTIVE_STATUSES.has(item.status);
            return (
              <button
                key={`${parent}-${item.id}`}
                type="button"
                disabled={!href}
                onClick={() => href && navigate(href)}
                className="card-surface w-full p-5 text-left transition-transform hover:scale-[1.01] animate-slide-up"
                style={{ animationDelay: `${index * 50}ms` }}
              >
                <h3 className="mb-1 truncate font-semibold text-gray-900">
                  {item.title || item.url}
                </h3>
                <p className="truncate text-sm text-gray-500">{item.url}</p>
                {item.parent_brand_name ? (
                  <p className="mt-1 text-xs text-gray-400">{item.parent_brand_name}</p>
                ) : null}
                {typeof overall === "number" ? (
                  <p className="mt-2 text-2xl font-bold text-brand-accent">
                    {formatReportScore(overall)}
                    <span className="text-sm font-normal text-gray-500"> / 100</span>
                  </p>
                ) : null}
                <div className="mt-2 flex items-center text-sm text-gray-500">
                  <Calendar className="mr-2 h-4 w-4" />
                  {(item.updated_at || item.created_at || "").slice(0, 10) || "—"}
                </div>
                {running ? (
                  <span className="mt-4 inline-flex items-center text-sm font-medium text-gray-500">
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
                    Scoring
                  </span>
                ) : (
                  <span className="mt-4 inline-flex items-center text-sm font-medium text-blue-600">
                    <Eye className="mr-1 h-4 w-4" />
                    View page audit
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
