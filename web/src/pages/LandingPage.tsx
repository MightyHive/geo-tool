import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { FileText, Loader2, RefreshCw } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import {
  fetchLatestAudit,
  fetchLocalAudits,
} from "../api/client";
import { AuditListCard } from "../components/AuditListCard";
import { Card, CardDescription, CardTitle } from "../components/ui/Card";
import { PageHeader } from "../components/PageHeader";
import { auditSlug, BUNDLED_SAMPLE_AUDIT_SLUG } from "../lib/auditPath";
import type { LocalAudit } from "../types";

export function LandingPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { mode, enabled, loggedIn, user, loading: authLoading, login } = useAuth();
  const [audits, setAudits] = useState<LocalAudit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [landingStep, setLandingStep] = useState(1);

  const authError = searchParams.get("auth_error");

  useEffect(() => {
    if (authError) {
      const msg =
        authError === "domain"
          ? "Your Google account is not allowed for this app."
          : authError === "email"
            ? "Google did not return an email."
            : authError === "state"
              ? "Sign-in session expired (localhost vs 127.0.0.1). Use one URL consistently, e.g. http://localhost:5173"
              : "Sign-in failed. In Google Cloud, add this redirect URI exactly: http://localhost:5173/api/auth/callback (and http://127.0.0.1:5173/api/auth/callback if you use that host).";
      setError(msg);
      setSearchParams({}, { replace: true });
    }
  }, [authError, setSearchParams]);

  useEffect(() => {
    if (loggedIn || mode === "iap") setLandingStep(2);
  }, [loggedIn, mode]);

  useEffect(() => {
    fetchLocalAudits().then(setAudits).catch(() => setAudits([]));
  }, []);

  async function openLatest() {
    setLoading("latest");
    setError(null);
    try {
      const { audit_dir } = await fetchLatestAudit();
      navigate(`/report/${auditSlug(audit_dir)}/summary`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "No local audits found");
    } finally {
      setLoading(null);
    }
  }

  function openSample() {
    navigate(`/report/${BUNDLED_SAMPLE_AUDIT_SLUG}/summary`);
  }

  if (authLoading) {
    return (
      <div className="page-container flex justify-center py-24">
        <Loader2 className="w-8 h-8 animate-spin text-brand-accent" />
      </div>
    );
  }

  return (
    <div className="page-container">
      <PageHeader
        title="GEO Audit"
        description="Guided setup, then a full crawl and scored report—including AI visibility, technical setup, and prompt performance."
      />

      {error && <div className="alert-error">{error}</div>}

      {mode === "oauth" && landingStep === 1 && !loggedIn && (
        <Card>
          <CardTitle>Step 1 — Google account</CardTitle>
          <CardDescription>
            Sign in to attach audits to your identity and reopen them from Existing
            audits. You can skip and still run audits locally.
          </CardDescription>
          <div className="flex flex-wrap gap-3">
            <button type="button" className="btn-primary" onClick={() => login("/")}>
              Sign in with Google
            </button>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => setLandingStep(2)}
            >
              Continue without signing in
            </button>
          </div>
        </Card>
      )}

      {(landingStep === 2 || loggedIn || !enabled) && (
        <>
          {loggedIn && user && (
            <div className="alert-success">
              Signed in as <strong>{user.email}</strong>
              {mode === "iap" && (
                <span className="block text-sm mt-1 opacity-80">
                  via Google Cloud Identity-Aware Proxy
                </span>
              )}
            </div>
          )}

          <Card>
            <CardTitle>Quick open</CardTitle>
            <CardDescription>
              Open a report without running a new crawl.
            </CardDescription>
            <div className="grid gap-4 sm:grid-cols-2">
              <button
                type="button"
                className="btn-primary w-full"
                disabled={loading !== null}
                onClick={openLatest}
              >
                {loading === "latest" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                Load latest local audit
              </button>
              <button
                type="button"
                className="btn-secondary w-full"
                disabled={loading !== null}
                onClick={openSample}
              >
                {loading === "sample" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <FileText className="h-4 w-4" />
                )}
                Open bundled sample
              </button>
            </div>
          </Card>

          {audits.length > 0 && (
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 mb-6">
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

          <Card>
            <CardTitle>Step 2 — Choose an audit path</CardTitle>
            <CardDescription>
              Brand setup, optional GA4, products, competitors, prompts—then run the
              crawl.
            </CardDescription>
            <div className="flex flex-wrap gap-3">
              <Link to="/audit/new?fresh=1" className="btn-primary">
                New audit
              </Link>
              <Link to="/audits" className="btn-secondary">
                Existing audits
              </Link>
              {enabled && !loggedIn && landingStep === 2 && (
                <button
                  type="button"
                  className="btn-ghost"
                  onClick={() => setLandingStep(1)}
                >
                  ← Back to sign-in
                </button>
              )}
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
