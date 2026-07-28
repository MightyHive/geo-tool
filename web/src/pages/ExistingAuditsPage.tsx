import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronDown, ChevronUp, FileText } from "lucide-react";
import { fetchLocalAudits } from "../api/client";
import { AuditListCard } from "../components/AuditListCard";
import { PageLoading } from "../components/PageLoading";
import { Card, CardDescription, CardTitle } from "../components/ui/Card";
import { PageHeader } from "../components/PageHeader";
import type { LocalAudit } from "../types";

/** First paint: three most recent; remainder loads on “Show more”. */
const INITIAL_VISIBLE = 3;

export function ExistingAuditsPage() {
  const navigate = useNavigate();
  const [audits, setAudits] = useState<LocalAudit[]>([]);
  const [expanded, setExpanded] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    setExpanded(false);
    fetchLocalAudits({ limit: INITIAL_VISIBLE + 1 })
      .then((rows) => {
        setHasMore(rows.length > INITIAL_VISIBLE);
        setAudits(rows.slice(0, INITIAL_VISIBLE));
      })
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Could not load audits"),
      )
      .finally(() => setLoading(false));
  }, []);

  // Refresh list while any audit pipeline is still running so cards unlock when idle.
  const anyStillRunning = audits.some((a) => a.still_running);
  useEffect(() => {
    if (loading || !anyStillRunning) return;
    const id = window.setInterval(() => {
      const limit = expanded ? undefined : INITIAL_VISIBLE + 1;
      fetchLocalAudits(limit != null ? { limit } : undefined)
        .then((rows) => {
          if (expanded) {
            setAudits(rows);
            setHasMore(false);
          } else {
            setHasMore(rows.length > INITIAL_VISIBLE);
            setAudits(rows.slice(0, INITIAL_VISIBLE));
          }
        })
        .catch(() => {
          /* keep current list on transient poll errors */
        });
    }, 8000);
    return () => window.clearInterval(id);
  }, [anyStillRunning, expanded, loading]);

  async function showMore() {
    setLoadingMore(true);
    setError(null);
    try {
      const rows = await fetchLocalAudits();
      setAudits(rows);
      setExpanded(true);
      setHasMore(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load audits");
    } finally {
      setLoadingMore(false);
    }
  }

  function showLess() {
    setAudits((prev) => prev.slice(0, INITIAL_VISIBLE));
    setExpanded(false);
    setHasMore(true);
  }

  if (loading) {
    return (
      <div className="page-container">
        <PageLoading label="Loading audits…" />
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader
        title="Existing audits"
        description="Audits on this environment. Reports that are still running stay locked until the full pipeline finishes."
      />

      {error && <div className="alert-error">{error}</div>}

      {!audits.length && !error && (
        <Card>
          <FileText className="w-12 h-12 text-gray-400 mb-4" />
          <CardTitle>No audits yet</CardTitle>
          <CardDescription>
            Run a new audit to generate your first report.
          </CardDescription>
          <Link to="/audit/new?fresh=1" className="btn-primary mt-2 inline-flex">
            New audit
          </Link>
        </Card>
      )}

      {audits.length > 0 && (
        <>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {audits.map((a, index) => (
              <AuditListCard
                key={a.id}
                audit={a}
                index={index}
                onOpen={() => {
                  if (a.still_running) return;
                  navigate(`/report/${a.id}/summary`);
                }}
              />
            ))}
          </div>
          {(hasMore || expanded) && (
            <div className="mt-4 flex justify-center">
              {expanded ? (
                <button
                  type="button"
                  className="btn-secondary inline-flex items-center gap-2"
                  onClick={showLess}
                >
                  <ChevronUp className="h-4 w-4" />
                  Show less
                </button>
              ) : (
                <button
                  type="button"
                  className="btn-secondary inline-flex items-center gap-2"
                  disabled={loadingMore}
                  onClick={() => void showMore()}
                >
                  <ChevronDown className="h-4 w-4" />
                  {loadingMore ? "Loading…" : "Show more"}
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
