import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { FileText, Loader2 } from "lucide-react";
import { fetchLocalAudits } from "../api/client";
import { AuditListCard } from "../components/AuditListCard";
import { Card, CardDescription, CardTitle } from "../components/ui/Card";
import { PageHeader } from "../components/PageHeader";
import type { LocalAudit } from "../types";

export function ExistingAuditsPage() {
  const navigate = useNavigate();
  const [audits, setAudits] = useState<LocalAudit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchLocalAudits()
      .then(setAudits)
      .catch((err) =>
        setError(err instanceof Error ? err.message : "Could not load audits"),
      )
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="page-container flex justify-center py-24">
        <Loader2 className="w-8 h-8 animate-spin text-brand-accent" />
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader
        title="Existing audits"
        description="All completed audits available on this environment."
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
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {audits.map((a, index) => (
            <AuditListCard
              key={a.id}
              audit={a}
              index={index}
              onOpen={() => navigate(`/report/${a.id}/summary`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
