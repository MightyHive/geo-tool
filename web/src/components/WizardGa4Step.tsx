import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import {
  disconnectGa4,
  fetchGa4Status,
  ga4LoginUrl,
  saveGa4Selection,
} from "../api/client";
import { SearchableSelect } from "./SearchableSelect";
import type { Ga4Status } from "../types";

interface WizardGa4StepProps {
  onBack: () => void;
  onContinue: () => void;
  onSkip: () => void;
  onSelectionSaved?: (propertyId: string, aiChannelNames: string) => void;
}

function applyStatusSelection(
  s: Ga4Status,
  setAccountId: (id: string) => void,
  setPropertyId: (id: string) => void,
) {
  const pid = s.selected_property_id?.trim() || "";
  let aid = s.selected_account_id?.trim() || "";
  if (!aid && pid) {
    const match = s.properties.find((p) => p.id === pid);
    aid = match?.account_id?.trim() || "";
  }
  if (aid) setAccountId(aid);
  if (pid) setPropertyId(pid);
}

export function WizardGa4Step({ onBack, onContinue, onSkip, onSelectionSaved }: WizardGa4StepProps) {
  const [phase, setPhase] = useState<"choice" | "connect">("choice");
  const [wantGa4, setWantGa4] = useState<boolean | null>(null);
  const [status, setStatus] = useState<Ga4Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [accountId, setAccountId] = useState("");
  const [propertyId, setPropertyId] = useState("");
  const [aiChannels, setAiChannels] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    fetchGa4Status()
      .then((s) => {
        setStatus(s);
        applyStatusSelection(s, setAccountId, setPropertyId);
        if (s.ai_channel_names) setAiChannels(s.ai_channel_names);
        if (s.error) setError(s.error);
        if (s.connected) setPhase("connect");
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Could not load GA4 status"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("ga4_connected") === "1" || params.get("ga4_error")) {
      setPhase("connect");
      setWantGa4(true);
      const err = params.get("ga4_error");
      if (err) {
        setError(
          err === "state"
            ? "GA4 sign-in session expired. Try connecting again."
            : "GA4 sign-in failed. Check OAuth redirect URIs in Google Cloud.",
        );
      }
      params.delete("ga4_connected");
      params.delete("ga4_error");
      const qs = params.toString();
      window.history.replaceState(
        {},
        "",
        qs ? `${window.location.pathname}?${qs}` : window.location.pathname,
      );
      refresh();
    }
  }, [refresh]);

  const accountOptions = useMemo(() => {
    const rows = status?.accounts?.length
      ? status.accounts
      : Array.from(
          new Map(
            (status?.properties ?? []).map((p) => [
              p.account_id,
              { id: p.account_id, name: p.account || p.account_id },
            ]),
          ).values(),
        ).filter((a) => a.id);
    return rows.map((a) => ({
      id: a.id,
      label: a.name,
      sublabel: `Account ID ${a.id}`,
    }));
  }, [status]);

  const propertyOptions = useMemo(() => {
    if (!status?.properties?.length) return [];
    const filtered = accountId
      ? status.properties.filter((p) => p.account_id === accountId)
      : [];
    return filtered.map((p) => ({
      id: p.id,
      label: p.name,
      sublabel: `Property ID ${p.id}`,
    }));
  }, [status, accountId]);

  useEffect(() => {
    if (!propertyId || !accountId) return;
    const stillValid = propertyOptions.some((o) => o.id === propertyId);
    if (!stillValid) setPropertyId("");
  }, [accountId, propertyId, propertyOptions]);

  function handleAccountChange(nextAccountId: string) {
    setAccountId(nextAccountId);
    if (!nextAccountId) {
      setPropertyId("");
      return;
    }
    const current = status?.properties.find((p) => p.id === propertyId);
    if (current && current.account_id !== nextAccountId) {
      setPropertyId("");
    }
  }

  async function handleContinueFromConnect() {
    if (!status?.connected) {
      setError("Sign in with Google for Analytics first.");
      return;
    }
    if (!accountId.trim()) {
      setError("Select a GA4 account.");
      return;
    }
    if (!propertyId.trim()) {
      setError("Select a GA4 property.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const pid = propertyId.trim();
      const aid = accountId.trim();
      const ch = aiChannels.trim();
      await saveGa4Selection(pid, ch, aid);
      onSelectionSaved?.(pid, ch);
      onContinue();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save GA4 selection");
    } finally {
      setSaving(false);
    }
  }

  if (loading && !status) {
    return (
      <div className="card-surface p-6 mb-6 flex justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-brand-accent" />
      </div>
    );
  }

  if (status && !status.configured) {
    return (
      <div className="card-surface p-6 mb-6">
        <h3>Google Analytics (optional)</h3>
        <div className="alert-info mt-4">
          <p className="mb-2">
            GA4 OAuth is not configured on the server. Set{" "}
            <code>GA4_OAUTH_CLIENT_ID</code> and <code>GA4_OAUTH_CLIENT_SECRET</code> (or reuse your{" "}
            <code>GOOGLE_CLIENT_ID</code> / <code>GOOGLE_CLIENT_SECRET</code>) on Cloud Run — locally,
            use <code>[ga4_oauth]</code> or <code>[auth]</code> in{" "}
            <code>.streamlit/secrets.toml</code>.
          </p>
          <p className="text-sm mb-0">
            Enable <strong>Analytics Admin API</strong> and <strong>Google Analytics Data API</strong>,
            then add this redirect URI in Google Cloud → Credentials → your OAuth client:{" "}
            <code>{status.redirect_uri ?? "{origin}/api/ga4/callback"}</code>
          </p>
        </div>
        <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
          <button type="button" className="btn-secondary" onClick={onBack}>
            ← Back
          </button>
          <button type="button" className="btn-primary" onClick={onSkip}>
            Skip for now →
          </button>
        </div>
      </div>
    );
  }

  if (phase === "choice") {
    return (
      <div className="card-surface p-6 mb-6">
        <h3>Google Analytics (optional)</h3>
        <p className="text-sm text-gray-600 mb-4">
          Connect GA4 for traffic context, primary market hints, and AI channel reporting.
        </p>
        <fieldset className="space-y-2 mb-6">
          <legend className="sr-only">Connect GA4?</legend>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="ga4_choice"
              checked={wantGa4 === true}
              onChange={() => setWantGa4(true)}
            />
            Yes — connect GA4
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="ga4_choice"
              checked={wantGa4 === false}
              onChange={() => setWantGa4(false)}
            />
            Skip
          </label>
        </fieldset>
        {wantGa4 === false && (
          <p className="alert-info">Next you will define products or services for AI prompts.</p>
        )}
        {error && <div className="alert-error">{error}</div>}
        <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
          <button type="button" className="btn-secondary" onClick={onBack}>
            ← Back
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={wantGa4 === null}
            onClick={() => {
              if (wantGa4 === true) setPhase("connect");
              else if (wantGa4 === false) onSkip();
            }}
          >
            {wantGa4 ? "Continue →" : "Skip for now →"}
          </button>
        </div>
      </div>
    );
  }

  if (!status) return null;

  return (
    <div className="card-surface p-6 mb-6">
      <h3>Google Analytics</h3>
      <p className="text-sm text-gray-600 mb-4">
        Sign in with Google, choose the <strong>account</strong>, then the <strong>property</strong> for
        this brand. Type to search either list.
      </p>

      {error && <div className="alert-error">{error}</div>}

      {!status.connected ? (
        <a href={ga4LoginUrl(2, true)} className="btn-primary inline-flex mb-4">
          <ExternalLink className="w-4 h-4" />
          Connect Google for GA4
        </a>
      ) : (
        <>
          <div className="alert-success mb-4">Google Analytics is connected.</div>
          <SearchableSelect
            id="ga4-account"
            label="GA4 account"
            placeholder="Search accounts…"
            options={accountOptions}
            value={accountId}
            onChange={handleAccountChange}
            help="Accounts are listed alphabetically. Select one to see its properties."
            emptyHint="No accounts match your search."
          />
          <SearchableSelect
            id="ga4-property"
            label="GA4 property"
            placeholder={accountId ? "Search properties in this account…" : "Select an account first"}
            options={propertyOptions}
            value={propertyId}
            onChange={setPropertyId}
            disabled={!accountId}
            help={
              accountId
                ? `${propertyOptions.length} propert${propertyOptions.length === 1 ? "y" : "ies"} in this account (A–Z).`
                : undefined
            }
            emptyHint={
              accountId
                ? "No properties match your search in this account."
                : "Select an account above first."
            }
          />
          <div className="mb-4">
            <label htmlFor="ga4-channels">
              AI channel — only enter a value if you have set up a custom channel for AI sources in
              GA4
            </label>
            <p className="text-sm text-gray-600 mb-2">
              If you don&apos;t have an AI channel set up, we&apos;ll look for AI sources in your
              traffic reports to analyse AI traffic.
            </p>
            <input
              id="ga4-channels"
              className="input-field"
              value={aiChannels}
              onChange={(e) => setAiChannels(e.target.value)}
              placeholder="e.g. AI"
            />
            <p className="text-xs text-gray-500 mt-1">
              Comma-separated custom channel group names (optional).
            </p>
          </div>
          <button
            type="button"
            className="btn-ghost text-sm mb-4"
            onClick={() => disconnectGa4().then(refresh)}
          >
            Disconnect Google
          </button>
        </>
      )}

      <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            setPhase("choice");
            setWantGa4(null);
          }}
        >
          ← Back
        </button>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" onClick={onSkip}>
            Skip
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={!status.connected || saving}
            onClick={handleContinueFromConnect}
          >
            {saving ? "Saving…" : "Continue →"}
          </button>
        </div>
      </div>
    </div>
  );
}
