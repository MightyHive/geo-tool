/**
 * AI Impact Estimates — separate from the iframe GA4 Traffic dashboard.
 * Connect GA4 via the same wizard OAuth; pull channels via ga4_channel_export.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, ChevronDown, ExternalLink, Upload } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  createAiImpactRun,
  disconnectGa4,
  disconnectGsc,
  fetchAiImpactConfig,
  fetchAiImpactRun,
  fetchGa4Status,
  fetchGscSites,
  fetchGscStatus,
  ga4LoginUrl,
  gscLoginUrl,
  saveGa4Selection,
  saveGscSelection,
  saveAiImpactEstimateForAudit,
  uploadAiImpactTrends,
  type AiImpactEstimate,
  type AiImpactRunStatus,
  type AiImpactTrendsUpload,
  type AiImpactWeeklyPoint,
  type GscSite,
  type GscStatus,
} from "../api/client";
import { filterCompletedWeeks } from "../lib/completedSundayWeeks";
import { legendItemSorterByLatestValueDesc } from "../lib/chartLegend";
import { sortTooltipItemsByValueDesc } from "../lib/chartTooltip";
import {
  formatIndirectSessionsRange,
  probabilityOfResultPercent,
} from "../lib/estimateRange";
import {
  buildConversionEventSpec,
  eventNamesInput,
  formatConversionMethodNote,
  parseConversionEvents,
  requiresSeriesLabel,
  seriesLabelFromEvents,
  tryParseConversionEvents,
  validateConversionSpec,
} from "../lib/conversionEvents";
import { SearchableSelect } from "./SearchableSelect";
import type { Ga4Status } from "../types";

function fmt(n: number | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return Math.round(n).toLocaleString();
}

function fmtShare(value: number, total: number): string {
  if (!Number.isFinite(value) || !Number.isFinite(total) || total <= 0) return "—";
  const percentage = (100 * value) / total;
  const digits = percentage < 0.1 ? 2 : 1;
  return `${percentage.toFixed(digits)}% of total`;
}

function connectorReturnPath(): string {
  const { pathname, search } = window.location;
  const params = new URLSearchParams(search);
  params.delete("ga4_connected");
  params.delete("ga4_error");
  params.delete("gsc_connected");
  params.delete("gsc_error");
  const qs = params.toString();
  return qs ? `${pathname}?${qs}` : pathname;
}

function savedRunKey(auditDirOrSlug: string): string {
  return `geo-ai-impact-run:${auditDirOrSlug}`;
}

function hydrateConversionFields(stored: string): { eventsInput: string; label: string } {
  const parsed = tryParseConversionEvents(stored.trim() || "purchase");
  if (!parsed.ok) {
    return { eventsInput: stored.trim() || "purchase", label: "" };
  }
  return {
    eventsInput: eventNamesInput(parsed.events),
    label: seriesLabelFromEvents(parsed.events) || "",
  };
}

export function AiImpactDashboard({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const formDetailsRef = useRef<HTMLDetailsElement>(null);
  const [windowWeeks, setWindowWeeks] = useState(13);
  const [localPanel, setLocalPanel] = useState("");
  const [ga4Status, setGa4Status] = useState<Ga4Status | null>(null);
  const [accountId, setAccountId] = useState("");
  const [propertyId, setPropertyId] = useState("");
  const [conversionEventsInput, setConversionEventsInput] = useState("purchase");
  const [conversionLabel, setConversionLabel] = useState("");
  const [gscStatus, setGscStatus] = useState<GscStatus | null>(null);
  const [gscSites, setGscSites] = useState<GscSite[]>([]);
  const [gscSiteUrl, setGscSiteUrl] = useState("");
  const [gscLoading, setGscLoading] = useState(false);
  const [trendsUpload, setTrendsUpload] = useState<AiImpactTrendsUpload | null>(null);
  const [trendsUploadBusy, setTrendsUploadBusy] = useState(false);
  const [trendsUploadError, setTrendsUploadError] = useState<string | null>(null);
  const [run, setRun] = useState<AiImpactRunStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [runProgress, setRunProgress] = useState<{ percent: number; label: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshGa4 = useCallback(() => {
    fetchGa4Status()
      .then((s) => {
        setGa4Status(s);
        const pid = s.selected_property_id?.trim() || "";
        let aid = s.selected_account_id?.trim() || "";
        if (!aid && pid) {
          aid = s.properties.find((p) => p.id === pid)?.account_id?.trim() || "";
        }
        if (aid) setAccountId(aid);
        if (pid) setPropertyId(pid);
        const hydrated = hydrateConversionFields(s.conversion_event_name?.trim() || "purchase");
        setConversionEventsInput(hydrated.eventsInput);
        setConversionLabel(hydrated.label);
        if (s.error) setError(s.error);
      })
      .catch(() => setGa4Status(null));
  }, []);

  const refreshGsc = useCallback(() => {
    setGscLoading(true);
    fetchGscStatus()
      .then(async (status) => {
        setGscStatus(status);
        setGscSiteUrl(status.site_url?.trim() || "");
        if (status.error) setError(status.error);
        if (status.connected) {
          const response = await fetchGscSites();
          setGscSites(response.sites);
        } else {
          setGscSites([]);
        }
      })
      .catch((e) => {
        setGscStatus(null);
        setError(e instanceof Error ? e.message : "Could not load Search Console");
      })
      .finally(() => setGscLoading(false));
  }, []);

  useEffect(() => {
    void fetchAiImpactConfig().catch(() => null);
    refreshGa4();
    refreshGsc();
  }, [refreshGa4, refreshGsc]);

  useEffect(() => {
    const runId = window.sessionStorage.getItem(savedRunKey(auditDirOrSlug));
    if (!runId) return;
    fetchAiImpactRun(runId)
      .then((savedRun) => {
        if (!savedRun.estimate) return;
        setRun(savedRun);
        if (savedRun.conversion_event_name) {
          const hydrated = hydrateConversionFields(savedRun.conversion_event_name);
          setConversionEventsInput(hydrated.eventsInput);
          setConversionLabel(hydrated.label);
        }
        void saveAiImpactEstimateForAudit(
          auditDirOrSlug,
          savedRun.estimate as AiImpactEstimate,
          savedRun.run_id,
        ).catch(() => undefined);
      })
      .catch(() => window.sessionStorage.removeItem(savedRunKey(auditDirOrSlug)));
  }, [auditDirOrSlug]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const hasGa4Result = params.get("ga4_connected") === "1" || params.get("ga4_error");
    const hasGscResult = params.get("gsc_connected") === "1" || params.get("gsc_error");
    if (hasGa4Result || hasGscResult) {
      const err = params.get("ga4_error");
      const gscErr = params.get("gsc_error");
      if (err) {
        setError(
          err === "state"
            ? "GA4 sign-in session expired. Try connecting again."
            : "GA4 sign-in failed. Check OAuth redirect URIs in Google Cloud.",
        );
      } else if (gscErr) {
        setError(
          gscErr === "state_mismatch"
            ? "Search Console sign-in session expired. Try connecting again."
            : "Search Console sign-in failed. Check the API and OAuth redirect settings.",
        );
      }
      params.delete("ga4_connected");
      params.delete("ga4_error");
      params.delete("gsc_connected");
      params.delete("gsc_error");
      const qs = params.toString();
      window.history.replaceState(
        {},
        "",
        qs ? `${window.location.pathname}?${qs}` : window.location.pathname,
      );
      if (hasGa4Result) refreshGa4();
      if (hasGscResult) refreshGsc();
    }
  }, [refreshGa4, refreshGsc]);

  const accountOptions = useMemo(() => {
    const rows = ga4Status?.accounts?.length
      ? ga4Status.accounts
      : Array.from(
          new Map(
            (ga4Status?.properties ?? []).map((p) => [
              p.account_id,
              { id: p.account_id, name: p.account || p.account_id },
            ]),
          ).values(),
        ).filter((a) => a.id);
    return rows.map((a) => ({ id: a.id, label: a.name || a.id }));
  }, [ga4Status]);

  const propertyOptions = useMemo(() => {
    const props = (ga4Status?.properties ?? []).filter(
      (p) => !accountId || p.account_id === accountId,
    );
    return props.map((p) => ({
      id: p.id,
      label: `${p.name || p.id} (${p.id})`,
    }));
  }, [ga4Status, accountId]);

  const conversionValidation = validateConversionSpec(
    conversionEventsInput,
    conversionLabel,
  );
  const conversionEventsParsed = conversionValidation.events;
  const labelRequired = requiresSeriesLabel(conversionEventsParsed);
  const conversionSpecValid = conversionValidation.ok;
  const storedConversionSpec = useMemo(() => {
    if (!conversionSpecValid) return conversionEventsInput.trim() || "purchase";
    try {
      return buildConversionEventSpec(
        conversionEventsInput.trim() || "purchase",
        conversionLabel,
      );
    } catch {
      return conversionEventsInput.trim() || "purchase";
    }
  }, [conversionSpecValid, conversionEventsInput, conversionLabel]);

  function currentConversionSpec(): string {
    return buildConversionEventSpec(
      conversionEventsInput.trim() || "purchase",
      conversionLabel,
    );
  }

  function handleConversionEventsChange(value: string) {
    setConversionEventsInput(value);
    const parsed = tryParseConversionEvents(value.trim() || "purchase");
    if (!parsed.ok) return;
    const migrated = seriesLabelFromEvents(parsed.events);
    if (migrated && !conversionLabel.trim()) {
      setConversionLabel(migrated);
      setConversionEventsInput(eventNamesInput(parsed.events));
    }
  }

  async function persistProperty(nextPropertyId: string, nextAccountId: string) {
    if (!nextPropertyId) return;
    try {
      await saveGa4Selection(
        nextPropertyId,
        "",
        nextAccountId,
        currentConversionSpec(),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function onRun() {
    const parsed = tryParseConversionEvents(conversionEventsInput.trim() || "purchase");
    if (!parsed.ok) {
      setError(parsed.error);
      return;
    }
    if (requiresSeriesLabel(parsed.events) && !conversionLabel.trim()) {
      setError("Enter a display name for the summed conversion events.");
      return;
    }
    let conversionSpec: string;
    try {
      conversionSpec = currentConversionSpec();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid conversion events");
      return;
    }
    setBusy(true);
    setError(null);
    setRunProgress({ percent: 8, label: "Preparing data sources" });
    let succeeded = false;
    let progressTimer: ReturnType<typeof window.setInterval> | undefined;
    progressTimer = window.setInterval(() => {
      setRunProgress((current) => {
        const percent = Math.min((current?.percent ?? 8) + 6, 88);
        const label =
          percent < 28
            ? "Connecting to Google"
            : percent < 58
              ? "Pulling analytics data"
              : percent < 78
                ? "Building the weekly model"
                : "Finalising estimate";
        return { percent, label };
      });
    }, 3500);
    try {
      if (propertyId) {
        await persistProperty(propertyId, accountId);
      }
      const propName =
        ga4Status?.properties.find((p) => p.id === propertyId)?.name || propertyId || undefined;
      const created = await createAiImpactRun({
        window_weeks: windowWeeks,
        trends_upload_id: trendsUpload?.upload_id || null,
        local_panel_path: localPanel.trim() || null,
        ga4_property_id: propertyId || null,
        ga4_property_name: propName || null,
        gsc_site_url: gscSiteUrl || null,
        conversion_event_name: conversionSpec,
      });
      setRun(created);
      if (created.needs_ga4_reauth) {
        setError(
          created.error ||
            "Google Analytics session expired. Disconnect and reconnect, then try again.",
        );
      } else if (created.run_id) {
        const latest = await fetchAiImpactRun(created.run_id);
        setRun(latest);
        if (latest.estimate && formDetailsRef.current) {
          window.sessionStorage.setItem(savedRunKey(auditDirOrSlug), latest.run_id);
          void saveAiImpactEstimateForAudit(
            auditDirOrSlug,
            latest.estimate as AiImpactEstimate,
            latest.run_id,
          ).catch(() => undefined);
          succeeded = true;
          formDetailsRef.current.open = false;
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (progressTimer) window.clearInterval(progressTimer);
      setRunProgress({
        percent: succeeded ? 100 : 0,
        label: succeeded ? "Complete" : "Could not complete",
      });
      window.setTimeout(() => setRunProgress(null), 800);
      setBusy(false);
    }
  }

  const ga4Connected = Boolean(ga4Status?.connected);
  const gscConnected = Boolean(gscStatus?.connected);
  const est = run?.estimate as AiImpactEstimate | null | undefined;
  const returnPath = connectorReturnPath();
  const loginHref = ga4LoginUrl(2, true, returnPath);
  const gscLoginHref = gscLoginUrl(returnPath);
  const trendsStartDate = "2023-01-01";
  const trendsEndDate = new Date().toISOString().slice(0, 10);
  const trendsUrl =
    `https://trends.google.com/trends/explore?date=${trendsStartDate}%20${trendsEndDate}`
    + "&geo=GB&hl=en-GB";

  async function onTrendsFile(file: File | undefined) {
    if (!file) return;
    setTrendsUploadBusy(true);
    setTrendsUpload(null);
    setTrendsUploadError(null);
    try {
      const validated = await uploadAiImpactTrends(file, trendsStartDate, trendsEndDate);
      setTrendsUpload(validated);
    } catch (uploadError) {
      setTrendsUploadError(
        uploadError instanceof Error ? uploadError.message : "The CSV could not be validated.",
      );
    } finally {
      setTrendsUploadBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <details
        ref={formDetailsRef}
        className="group overflow-hidden rounded-lg border border-neutral-300 bg-white"
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 font-semibold text-neutral-900 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-600">
          <span>Estimate overall AI impact on traffic and conversions</span>
          <ChevronDown
            className="h-5 w-5 shrink-0 text-neutral-500 transition-transform group-open:rotate-180"
            aria-hidden="true"
          />
        </summary>

        <div className="space-y-6 border-t border-neutral-200 px-5 py-5">
          <section>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <img
                  src="/assets/logos/GA4-logo.png"
                  alt=""
                  className="h-5 w-5 shrink-0 object-contain"
                />
                <h3 className="text-sm font-semibold text-neutral-900">
                  Google Analytics <span className="font-normal text-neutral-500">(Required)</span>
                </h3>
              </div>
              {ga4Connected ? (
                <span className="inline-flex items-center gap-1.5 text-sm text-green-700">
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                  Connected
                </span>
              ) : null}
            </div>
            <p className="mb-3 text-xs text-neutral-500">
              GA4 provides the sessions and conversion data required for the estimate.
            </p>
            {!ga4Connected ? (
              <a
                href={loginHref}
                className="inline-flex rounded-md bg-neutral-900 px-4 py-2 text-sm font-semibold text-white"
              >
                Connect GA4
              </a>
            ) : (
              <>
                <div className="grid gap-4 md:grid-cols-2">
                  <SearchableSelect
                    id="ai-impact-ga4-account"
                    label="Account"
                    placeholder="Search accounts…"
                    options={accountOptions}
                    value={accountId}
                    onChange={(id) => {
                      setAccountId(id);
                      setPropertyId("");
                    }}
                    emptyHint="No accounts found."
                  />
                  <SearchableSelect
                    id="ai-impact-ga4-property"
                    label="Property"
                    placeholder={accountId ? "Search properties…" : "Select an account first"}
                    options={propertyOptions}
                    value={propertyId}
                    onChange={(id) => {
                      setPropertyId(id);
                      void persistProperty(id, accountId);
                    }}
                    disabled={!accountId}
                    emptyHint="No properties found."
                  />
                </div>
                <label className="mt-4 block max-w-md text-sm font-medium text-neutral-800">
                  Conversion events
                  <input
                    className="mt-1 w-full rounded border border-neutral-300 px-3 py-2"
                    value={conversionEventsInput}
                    onChange={(event) => handleConversionEventsChange(event.target.value)}
                    placeholder="purchase"
                    aria-describedby="ai-impact-conversion-event-help"
                  />
                  <span
                    id="ai-impact-conversion-event-help"
                    className="mt-1 block text-xs font-normal text-neutral-500"
                  >
                    Comma-separated GA4 event names. Multiple events are summed into one
                    conversions metric.
                  </span>
                </label>
                <label className="mt-4 block max-w-md text-sm font-medium text-neutral-800">
                  Conversion label{labelRequired ? "" : " (optional)"}
                  <input
                    className="mt-1 w-full rounded border border-neutral-300 px-3 py-2"
                    value={conversionLabel}
                    onChange={(event) => setConversionLabel(event.target.value)}
                    placeholder={
                      labelRequired
                        ? "Name for the summed conversions metric"
                        : "Override display name in charts/reports"
                    }
                    aria-describedby="ai-impact-conversion-label-help"
                    required={labelRequired}
                  />
                  <span
                    id="ai-impact-conversion-label-help"
                    className="mt-1 block text-xs font-normal text-neutral-500"
                  >
                    {labelRequired
                      ? "Required when multiple events are summed — used as the chart/report heading."
                      : "Optional display name for this conversion series."}
                  </span>
                </label>
                <label className="mt-4 block max-w-md text-sm font-medium text-neutral-800">
                  Analysis window
                  <select
                    className="mt-1 w-full rounded border border-neutral-300 bg-white px-3 py-2"
                    value={windowWeeks}
                    onChange={(event) => setWindowWeeks(Number(event.target.value))}
                  >
                    <option value={13}>13 weeks</option>
                    <option value={26}>26 weeks</option>
                    <option value={52}>52 weeks</option>
                  </select>
                </label>
                <ConnectorActions
                  loginHref={loginHref}
                  onDisconnect={() => disconnectGa4().then(refreshGa4)}
                />
              </>
            )}
          </section>

          <section className="border-t border-neutral-200 pt-5">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <img
                  src="/assets/logos/gsc.png"
                  alt=""
                  className="h-5 w-5 shrink-0 object-contain"
                />
                <h3 className="text-sm font-semibold text-neutral-900">
                  Search Console <span className="font-normal text-neutral-500">(Recommended)</span>
                </h3>
              </div>
              {gscConnected ? (
                <span className="inline-flex items-center gap-1.5 text-sm text-green-700">
                  <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                  Connected
                </span>
              ) : null}
            </div>
            <p className="mb-3 text-xs text-neutral-500">
              Optional. Search Console input improves the accuracy of the estimate.
            </p>
            {!gscConnected ? (
              <a
                className="inline-flex items-center gap-2 rounded-md bg-blue-700 px-4 py-2 text-sm font-semibold text-white"
                href={gscLoginHref}
              >
                Connect Search Console
                <ExternalLink className="h-4 w-4" aria-hidden="true" />
              </a>
            ) : (
              <>
                <SearchableSelect
                  id="ai-impact-gsc-site"
                  label="Property"
                  placeholder={gscLoading ? "Loading…" : "Search properties…"}
                  options={gscSites.map((site) => ({
                    id: site.site_url,
                    label: site.site_url,
                  }))}
                  value={gscSiteUrl}
                  onChange={(siteUrl) => {
                    setGscSiteUrl(siteUrl);
                    if (siteUrl) {
                      void saveGscSelection(siteUrl).catch((e) =>
                        setError(e instanceof Error ? e.message : String(e)),
                      );
                    }
                  }}
                  disabled={gscLoading}
                  emptyHint="No verified properties found."
                />
                <ConnectorActions
                  loginHref={gscLoginHref}
                  onDisconnect={() =>
                    disconnectGsc().then(() => {
                      setGscSiteUrl("");
                      setGscSites([]);
                      refreshGsc();
                    })
                  }
                />
              </>
            )}
          </section>

          <section className="border-t border-neutral-200 pt-5">
            <div className="mb-2 flex items-center gap-2">
              <img
                src="/assets/logos/trends.png"
                alt=""
                className="h-5 w-5 shrink-0 object-contain"
              />
              <h3 className="text-sm font-semibold text-neutral-900">
                Google Trends{" "}
                <span className="font-normal text-neutral-500">(Optional, manual upload)</span>
              </h3>
            </div>
            <p className="max-w-2xl text-xs leading-5 text-neutral-600">
              Add weekly search-interest data to control for changes in underlying demand. The file
              is checked before it is used in the estimate.
            </p>

            <ol className="mt-3 max-w-2xl list-decimal space-y-1.5 pl-5 text-xs leading-5 text-neutral-700">
              <li>
                <a
                  href={trendsUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 font-semibold text-blue-700 underline decoration-blue-300 underline-offset-2 hover:text-blue-900"
                >
                  Open Google Trends
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                </a>
                .
              </li>
              <li>Add one to five search terms that represent demand for this business.</li>
              <li>
                Keep the location as United Kingdom and use{" "}
                <strong>{trendsStartDate}</strong> to <strong>{trendsEndDate}</strong>.
              </li>
              <li>
                In the <strong>Interest over time</strong> chart, select Download CSV. Upload that
                file unchanged below.
              </li>
            </ol>

            <label className="mt-4 flex max-w-2xl cursor-pointer items-center gap-3 rounded-md border border-dashed border-neutral-300 bg-neutral-50 px-4 py-3 text-sm transition-colors hover:border-neutral-400 hover:bg-neutral-100 focus-within:ring-2 focus-within:ring-blue-600">
              <Upload className="h-5 w-5 shrink-0 text-neutral-500" aria-hidden="true" />
              <span className="min-w-0">
                <span className="block font-semibold text-neutral-900">
                  {trendsUploadBusy ? "Checking CSV…" : "Choose Google Trends CSV"}
                </span>
                <span className="block text-xs text-neutral-500">CSV only, maximum 2 MB</span>
              </span>
              <input
                type="file"
                accept=".csv,text/csv"
                className="sr-only"
                disabled={trendsUploadBusy}
                onChange={(event) => void onTrendsFile(event.target.files?.[0])}
              />
            </label>

            {trendsUpload ? (
              <div
                className="mt-3 flex max-w-2xl items-start gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2.5 text-sm text-green-900"
                role="status"
              >
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <div>
                  <div className="font-semibold">
                    {trendsUpload.filename} is ready ({trendsUpload.week_count} weeks)
                  </div>
                  <div className="mt-0.5 text-xs">
                    {trendsUpload.start_date} to {trendsUpload.end_date}:{" "}
                    {trendsUpload.terms.join(", ")}
                  </div>
                  {trendsUpload.warnings.map((warning) => (
                    <div key={warning} className="mt-1 text-xs">
                      {warning}
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {trendsUploadError ? (
              <div
                className="mt-3 flex max-w-2xl items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-900"
                role="alert"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <div>
                  <div className="font-semibold">This CSV cannot be used</div>
                  <div className="mt-0.5 text-xs leading-5">{trendsUploadError}</div>
                </div>
              </div>
            ) : null}
          </section>

          {import.meta.env.DEV ? (
            <label className="block text-sm font-medium text-neutral-500">
              Local panel CSV
              <input
                className="mt-1 w-full rounded border border-neutral-200 px-3 py-2 text-xs"
                value={localPanel}
                onChange={(e) => setLocalPanel(e.target.value)}
              />
            </label>
          ) : null}

          <button
            type="button"
            disabled={
              busy
              || trendsUploadBusy
              || (!localPanel.trim() && !propertyId)
              || !conversionSpecValid
            }
            onClick={() => void onRun()}
            className="rounded-md bg-neutral-900 px-5 py-2.5 text-sm font-semibold text-white disabled:opacity-40"
          >
            {busy ? "Running estimate…" : "Run estimate"}
          </button>
          {runProgress ? (
            <div className="max-w-xl" aria-live="polite">
              <div className="mb-1.5 flex items-center justify-between text-xs text-neutral-600">
                <span>{runProgress.label}</span>
                <span>{runProgress.percent}%</span>
              </div>
              <div
                className="h-2 overflow-hidden rounded-full bg-neutral-200"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={runProgress.percent}
              >
                <div
                  className="h-full rounded-full bg-blue-700 transition-[width] duration-500"
                  style={{ width: `${runProgress.percent}%` }}
                />
              </div>
            </div>
          ) : null}
          {error ? <p className="text-sm text-red-700">{error}</p> : null}
          {run?.needs_ga4_reauth ? (
            <a className="text-sm font-medium text-blue-700 underline" href={loginHref}>
              Re-authenticate GA4
            </a>
          ) : null}
        </div>
      </details>

      {est ? (
        <OverallImpactTable
          estimate={est}
          conversionEventName={run?.conversion_event_name || storedConversionSpec}
        />
      ) : null}
    </div>
  );
}

function ConnectorActions({
  loginHref,
  onDisconnect,
}: {
  loginHref: string;
  onDisconnect: () => void;
}) {
  return (
    <div className="flex gap-3 text-xs">
      <button type="button" className="text-neutral-600 underline" onClick={onDisconnect}>
        Disconnect
      </button>
      <a className="text-neutral-600 underline" href={loginHref}>
        Re-authenticate
      </a>
    </div>
  );
}

function rangeAfterDirect(
  range: { low: number; central: number; high: number },
  direct: number,
): { low: number; central: number; high: number } {
  return {
    low: range.low - direct,
    central: range.central - direct,
    high: range.high - direct,
  };
}

function formatWeekLabel(week: string): string {
  try {
    return new Date(week).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  } catch {
    return week;
  }
}

function SeriesTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ name?: string; value?: number; color?: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const items = sortTooltipItemsByValueDesc(payload);
  return (
    <div className="rounded-md border border-neutral-200 bg-white px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 font-semibold text-neutral-900">{label ? formatWeekLabel(label) : ""}</div>
      {items.map((entry) => (
        <div key={entry.name} className="flex items-center gap-2 text-neutral-600">
          <span className="h-2 w-2 rounded-full" style={{ background: entry.color }} />
          <span>{entry.name}: {fmt(entry.value)}</span>
        </div>
      ))}
    </div>
  );
}

function OverallImpactTable({
  estimate,
  conversionEventName,
}: {
  estimate: AiImpactEstimate;
  conversionEventName: string;
}) {
  const indirectSessions = rangeAfterDirect(
    estimate.sessions_overall_net,
    estimate.direct_ai_sessions,
  );
  const conversionEvents = useMemo(() => {
    try {
      return parseConversionEvents(conversionEventName);
    } catch {
      return parseConversionEvents("purchase");
    }
  }, [conversionEventName]);
  const conversionMethodNote = formatConversionMethodNote(conversionEvents);
  const modelQualityScore = estimate.model_quality_score ?? estimate.confidence_score;
  const probabilityPercent = probabilityOfResultPercent(estimate.p_value);
  const qualityNarrative =
    estimate.quality_narrative && estimate.quality_narrative !== "sessions_up_quality_down"
      ? estimate.quality_narrative
      : null;
  const weekly = useMemo(
    () => filterCompletedWeeks(estimate.weekly_series ?? []),
    [estimate.weekly_series],
  );
  const hasGsc = weekly.some((point) => typeof point.gsc_clicks === "number");
  const trendKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const point of weekly) {
      for (const key of Object.keys(point)) {
        if (key.startsWith("trends_")) keys.add(key);
      }
    }
    return Array.from(keys);
  }, [weekly]);

  const chartData = useMemo(() => {
    return weekly.map((point: AiImpactWeeklyPoint) => ({
      week: point.week,
      "Total sessions": point.total_sessions,
      "Tracked AI": point.ai_sessions,
      SEO: point.seo_sessions,
      "Estimated AI (direct + indirect)": point.estimated_ai_sessions,
      "Counterfactual (no AI)": point.counterfactual_sessions,
    }));
  }, [weekly]);

  const methodNotes = estimate.method_notes?.length
    ? estimate.method_notes
    : [
        "Direct AI = measured AI-channel sessions in the analysis window.",
        "Indirect central estimate comes from detrended associations between AI traffic and other channels, capped at ±15% of window totals.",
        "Displayed indirect sessions range is ±10% around the central estimate; it is not a statistical confidence interval.",
      ];

  return (
    <section className="overflow-hidden rounded-lg border border-neutral-300 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
        <div>
          <h3 className="font-semibold text-neutral-900">Estimated AI impact</h3>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-neutral-500">
            Weekly sources used in the model, with a counterfactual line showing what sessions
            look like after removing tracked AI and the central indirect estimate.
          </p>
        </div>
        <div className="text-right">
          {modelQualityScore != null ? (
            <div className="text-sm font-semibold text-neutral-800">
              Data & model quality {Math.round(modelQualityScore)}%
            </div>
          ) : null}
          {probabilityPercent != null ? (
            <div className="text-sm font-semibold text-neutral-800">
              Probability of result {probabilityPercent}%
            </div>
          ) : null}
          <div className="mt-0.5 text-xs text-neutral-500">
            {estimate.window_start} to {estimate.window_end}
          </div>
        </div>
      </div>

      {chartData.length > 0 ? (
        <div className="border-t border-neutral-200 px-5 py-5">
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Weekly sessions, sources, and counterfactual
          </h4>
          <p className="mb-4 max-w-3xl text-xs leading-relaxed text-neutral-500">
            <strong>Total sessions</strong>, <strong>Tracked AI</strong>, and <strong>SEO</strong>{" "}
            come from the GA4 weekly panel
            {hasGsc ? " (Search Console was also connected for this run)" : ""}
            {trendKeys.length
              ? ` · ${trendKeys.length} Google Trends series used as demand controls`
              : ""}
            . <strong>Estimated AI</strong> adds the modelled indirect spillover.{" "}
            <strong>Counterfactual (no AI)</strong> is total sessions minus tracked AI and that
            central indirect estimate — the path the model implies without AI influence.
          </p>
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#eee" vertical={false} />
              <XAxis
                dataKey="week"
                tickFormatter={formatWeekLabel}
                tick={{ fontSize: 10, fill: "#a3a3a3" }}
                axisLine={false}
                tickLine={false}
                minTickGap={28}
              />
              <YAxis
                tick={{ fontSize: 10, fill: "#a3a3a3" }}
                axisLine={false}
                tickLine={false}
                width={44}
              />
              <Tooltip content={<SeriesTooltip />} />
              <Legend
                wrapperStyle={{ fontSize: 11, paddingTop: 10 }}
                itemSorter={legendItemSorterByLatestValueDesc(chartData)}
              />
              <Line type="monotone" dataKey="Total sessions" stroke="#525252" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="Tracked AI" stroke="#1d4ed8" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="SEO" stroke="#0f766e" strokeWidth={1.75} dot={false} />
              <Line
                type="monotone"
                dataKey="Estimated AI (direct + indirect)"
                stroke="#7c3aed"
                strokeWidth={2}
                strokeDasharray="4 3"
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="Counterfactual (no AI)"
                stroke="#b45309"
                strokeWidth={2}
                strokeDasharray="6 4"
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="border-t border-neutral-200 px-5 py-4 text-xs text-neutral-500">
          Weekly series is not available for this run. Re-run the estimate to generate the
          time-series view.
        </div>
      )}

      <div className="overflow-x-auto border-t border-neutral-200">
        <table className="w-full text-left text-sm">
          <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="px-5 py-3 font-medium">Impact</th>
              <th className="px-5 py-3 font-medium">Sessions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-200">
            <tr>
              <th className="px-5 py-4 font-medium text-neutral-900">Direct (tracked)</th>
              <td className="px-5 py-4 tabular-nums">
                <div>{fmt(estimate.direct_ai_sessions)}</div>
                <div className="mt-1 text-xs text-neutral-500">
                  {fmtShare(estimate.direct_ai_sessions, estimate.total_sessions)}
                </div>
              </td>
            </tr>
            <tr>
              <th className="px-5 py-4 font-medium text-neutral-900">Indirect estimate</th>
              <td className="px-5 py-4 tabular-nums">
                <div>{formatIndirectSessionsRange(indirectSessions.central)}</div>
                <div className="mt-1 text-xs text-neutral-500">
                  {fmtShare(indirectSessions.central, estimate.total_sessions)}
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div className="space-y-3 border-t border-neutral-200 px-5 py-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          How this estimate is generated
        </h4>
        {qualityNarrative ? (
          <p className="text-xs leading-relaxed text-neutral-600">{qualityNarrative}</p>
        ) : null}
        <ol className="list-decimal space-y-1.5 pl-4 text-xs leading-relaxed text-neutral-600">
          <li>
            Build a weekly panel from GA4 channel data
            {conversionMethodNote}
            , optionally enriched with Search Console and a Google Trends CSV.
          </li>
          <li>
            <strong>Tracked (direct) AI</strong> is the weekly AI-channel series from GA4.
          </li>
          <li>
            <strong>Indirect AI</strong> is estimated from detrended associations between AI volume
            and other channels, then allocated across weeks in proportion to tracked AI.
          </li>
          <li>
            The <strong>counterfactual</strong> line is total sessions minus tracked AI and that
            central indirect allocation — the model’s implied path without AI influence.
          </li>
        </ol>
        <ul className="space-y-1 text-xs leading-relaxed text-neutral-500">
          {methodNotes.map((note) => (
            <li key={note} className="flex gap-2">
              <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-neutral-400" />
              <span>{note}</span>
            </li>
          ))}
        </ul>
        {modelQualityScore != null || probabilityPercent != null ? (
          <p className="text-xs leading-relaxed text-neutral-500">
            {modelQualityScore != null
              ? "Quality reflects data coverage, estimable relationships, and agreement across model scenarios. "
              : ""}
            {probabilityPercent != null
              ? "Probability of result is (1 − p) × 100 from the AI→non-AI sessions association. "
              : ""}
            The displayed sessions range is ±10% around the central indirect estimate; it is not a
            statistical confidence interval.
          </p>
        ) : null}
      </div>
    </section>
  );
}

