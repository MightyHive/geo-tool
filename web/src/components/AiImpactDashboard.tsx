/**
 * AI Impact Estimates — separate from the iframe GA4 Traffic dashboard.
 * Connect GA4 via the same wizard OAuth; pull channels via ga4_channel_export.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
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
  fetchAiImpactEstimateForAudit,
  fetchAiImpactRun,
  fetchAudit,
  fetchGa4Status,
  fetchGscSites,
  fetchGscStatus,
  ga4LoginUrl,
  gscLoginUrl,
  saveGa4Selection,
  saveGscSelection,
  type SavedAiImpactEstimate,
  setAuditModelCategory,
  uploadAiImpactTrends,
  type AiImpactConfig,
  type AiImpactEstimate,
  type AiImpactPosteriorInterval,
  type AiImpactRunStatus,
  type AiImpactSensitivityOutcome,
  type AiImpactTrendsUpload,
  type AiImpactWeeklyPoint,
  type GscSite,
  type GscStatus,
} from "../api/client";
import { filterCompletedWeeks } from "../lib/completedSundayWeeks";
import { legendItemSorterByLatestValueDesc } from "../lib/chartLegend";
import { sortTooltipItemsByValueDesc } from "../lib/chartTooltip";
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

export function aiImpactConfigPresentation(
  modelHydrated: boolean,
): "loading" | "collapsed" {
  // Always keep Config available once hydrated so users can re-run even when
  // a completed estimate is already on screen (collapsed by default).
  if (!modelHydrated) return "loading";
  return "collapsed";
}

export function stripSavedEstimateRunId(saved: SavedAiImpactEstimate): {
  estimate: AiImpactEstimate;
  runId: string | null;
} {
  const { _run_id: storedRunId, ...estimate } = saved;
  const runId = typeof storedRunId === "string" && storedRunId.trim() ? storedRunId.trim() : null;
  return { estimate: estimate as AiImpactEstimate, runId };
}

export function hydrateAiImpactRunFromSaved(
  saved: SavedAiImpactEstimate,
  run: AiImpactRunStatus | null,
): AiImpactRunStatus {
  const { estimate, runId } = stripSavedEstimateRunId(saved);
  if (run?.estimate) {
    return run;
  }
  return {
    run_id: runId || run?.run_id || "",
    status: run?.status || "completed",
    created_at: run?.created_at || "",
    conversion_event_name: run?.conversion_event_name,
    jobs: run?.jobs || {},
    estimate,
    hierarchical_refit: run?.hierarchical_refit,
    category: estimate.category ?? run?.category,
    estimate_mode: estimate.estimate_mode ?? run?.estimate_mode,
    model_artifact_version: estimate.model_artifact_version ?? run?.model_artifact_version,
    signal_artifact_version: estimate.signal_artifact_version ?? run?.signal_artifact_version,
  };
}

export function aiImpactResultLabel(estimateMode?: string | null): string {
  return estimateMode === "site_refit"
    ? "Site-inclusive hierarchical refit"
    : "Category-level AI effect applied to this site's traffic";
}

export function aiImpactRefitStatus(run: AiImpactRunStatus | null): string | null {
  const state = run?.hierarchical_refit?.status;
  if (state) return state;
  const legacyJobState = run?.jobs?.hierarchical_refit;
  return legacyJobState || null;
}

/** Hide portfolio-lag copy once the site refit has finished (or produced a refit estimate). */
export function shouldShowAwaitingSignalWeeks(
  awaitingWeeks: number | undefined,
  estimateMode?: string | null,
  refitStatus?: string | null,
): boolean {
  if (!awaitingWeeks) return false;
  if (estimateMode === "site_refit") return false;
  if (refitStatus === "completed") return false;
  return true;
}

function intervalConclusion(interval: AiImpactPosteriorInterval): "positive" | "negative" | "uncertain" {
  if (interval.lower_94 > 0) return "positive";
  if (interval.upper_94 < 0) return "negative";
  return "uncertain";
}

export function isRobustSensitivity(outcome: AiImpactSensitivityOutcome): boolean {
  const uncappedConclusion = intervalConclusion(outcome.uncapped);
  const cappedConclusion = intervalConclusion(outcome.capped);
  const derivedDirectionAgreement =
    Math.sign(outcome.uncapped.posterior_mean) === Math.sign(outcome.capped.posterior_mean);
  const derivedUncertaintyAgreement =
    uncappedConclusion === cappedConclusion && uncappedConclusion !== "uncertain";
  return (
    (outcome.direction_agrees ?? derivedDirectionAgreement)
    && (outcome.uncertainty_agrees ?? derivedUncertaintyAgreement)
    && uncappedConclusion === cappedConclusion
    && uncappedConclusion !== "uncertain"
  );
}

/** Calendar days covered by the estimate window (Sunday weeks × 7). */
export function estimateWindowDayCount(
  estimate: Pick<AiImpactEstimate, "window_start" | "window_end" | "weekly_series">,
): number {
  const weeks = filterCompletedWeeks(estimate.weekly_series ?? []).length;
  if (weeks > 0) return weeks * 7;
  const start = Date.parse(estimate.window_start);
  const end = Date.parse(estimate.window_end);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return NaN;
  // window_* are Sunday week starts; include the full last week.
  return Math.round((end - start) / 86_400_000) + 7;
}

/** Primary (uncapped) impact sessions averaged over calendar days in the window. */
export function estimatedSessionsPerDay(
  impactSessions: number,
  dayCount: number,
): number {
  if (!Number.isFinite(impactSessions) || !Number.isFinite(dayCount) || dayCount <= 0) {
    return NaN;
  }
  return impactSessions / dayCount;
}

/** Primary impact as a share of all-channel sessions in the window. */
export function estimatedImpactShareOfTotal(
  impactSessions: number,
  totalSessions: number,
): number {
  if (
    !Number.isFinite(impactSessions)
    || !Number.isFinite(totalSessions)
    || totalSessions <= 0
  ) {
    return NaN;
  }
  return impactSessions / totalSessions;
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

/** Temporarily hide Search Console from the AI Impact UI (backend still accepts it). */
const SHOW_GSC_SECTION = false;

const MODEL_CATEGORY_OPTIONS = [
  { value: "advertiser-retail", label: "Advertiser — retail" },
  { value: "advertiser-services", label: "Advertiser — services" },
  { value: "publisher", label: "Publisher" },
] as const;

export function AiImpactDashboard({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const [formOpen, setFormOpen] = useState(false);
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
  const [brandTrendsTerm, setBrandTrendsTerm] = useState("");
  const [brandName, setBrandName] = useState("");
  const [trendsConfig, setTrendsConfig] = useState<AiImpactConfig | null>(null);
  const [trendsUploadBusy, setTrendsUploadBusy] = useState(false);
  const [trendsUploadError, setTrendsUploadError] = useState<string | null>(null);
  const [showManualTrends, setShowManualTrends] = useState(false);
  const [modelCategory, setModelCategory] = useState<string | null>(null);
  const [modelCategoryDraft, setModelCategoryDraft] = useState("advertiser-retail");
  const [modelCategoryBusy, setModelCategoryBusy] = useState(false);
  const [run, setRun] = useState<AiImpactRunStatus | null>(null);
  const [runAuditId, setRunAuditId] = useState<string | null>(null);
  const [modelHydrated, setModelHydrated] = useState(false);
  const [hydratedAuditId, setHydratedAuditId] = useState("");
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
    let cancelled = false;
    setBrandName("");
    setBrandTrendsTerm("");
    setTrendsUpload(null);
    setTrendsUploadError(null);
    setShowManualTrends(false);
    setModelCategory(null);

    void fetchAiImpactConfig()
      .then((cfg) => {
        if (!cancelled) setTrendsConfig(cfg);
      })
      .catch(() => {
        if (!cancelled) setTrendsConfig(null);
      });
    void fetchAudit(auditDirOrSlug)
      .then((audit) => {
        if (cancelled) return;
        const name = (audit.onboarding_context?.brand_name_used || "").trim();
        setBrandName(name);
        setBrandTrendsTerm(name);
        const category = (audit.onboarding_context?.model_category || "").trim();
        if (category) {
          setModelCategory(category);
          setModelCategoryDraft(category);
        } else {
          setModelCategory(null);
        }
      })
      .catch(() => undefined);
    refreshGa4();
    if (SHOW_GSC_SECTION) refreshGsc();
    return () => {
      cancelled = true;
    };
  }, [auditDirOrSlug, refreshGa4, refreshGsc]);

  async function saveModelCategory(opts: { category?: string; classify?: boolean }) {
    setModelCategoryBusy(true);
    setError(null);
    try {
      const result = await setAuditModelCategory(auditDirOrSlug, opts);
      setModelCategory(result.model_category);
      setModelCategoryDraft(result.model_category);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save model category");
    } finally {
      setModelCategoryBusy(false);
    }
  }

  const applyHydratedRun = useCallback((savedRun: AiImpactRunStatus) => {
    if (!savedRun.estimate) return;
    setRun(savedRun);
    setRunAuditId(auditDirOrSlug);
    setFormOpen(false);
    if (savedRun.conversion_event_name) {
      const hydrated = hydrateConversionFields(savedRun.conversion_event_name);
      setConversionEventsInput(hydrated.eventsInput);
      setConversionLabel(hydrated.label);
    }
    if (savedRun.run_id) {
      window.sessionStorage.setItem(savedRunKey(auditDirOrSlug), savedRun.run_id);
    }
  }, [auditDirOrSlug]);

  useEffect(() => {
    let cancelled = false;

    async function hydrate() {
      setModelHydrated(false);
      setHydratedAuditId("");
      setRun(null);
      setRunAuditId(null);
      try {
        const saved = await fetchAiImpactEstimateForAudit(auditDirOrSlug);
        if (cancelled) return;
        if (saved) {
          const { runId } = stripSavedEstimateRunId(saved);
          let liveRun: AiImpactRunStatus | null = null;
          if (runId) {
            try {
              liveRun = await fetchAiImpactRun(runId);
            } catch {
              liveRun = null;
            }
          }
          if (cancelled) return;
          applyHydratedRun(hydrateAiImpactRunFromSaved(saved, liveRun));
          return;
        }

        const sessionRunId = window.sessionStorage.getItem(savedRunKey(auditDirOrSlug));
        if (sessionRunId) {
          try {
            const savedRun = await fetchAiImpactRun(sessionRunId);
            if (cancelled) return;
            if (savedRun.estimate) {
              applyHydratedRun(savedRun);
            }
          } catch {
            window.sessionStorage.removeItem(savedRunKey(auditDirOrSlug));
          }
        }
      } catch {
        // No saved estimate for this audit.
      } finally {
        if (!cancelled) {
          setHydratedAuditId(auditDirOrSlug);
          setModelHydrated(true);
        }
      }
    }

    void hydrate();
    return () => {
      cancelled = true;
    };
  }, [auditDirOrSlug, applyHydratedRun]);

  useEffect(() => {
    const refitStatus = aiImpactRefitStatus(run);
    if (
      runAuditId !== auditDirOrSlug
      || !run?.run_id
      || !["queued", "running"].includes(refitStatus || "")
    ) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void fetchAiImpactRun(run.run_id)
        .then((latest) => {
          if (cancelled) return;
          setRun(latest);
          setRunAuditId(auditDirOrSlug);
          if (latest.estimate) {
            window.sessionStorage.setItem(savedRunKey(auditDirOrSlug), latest.run_id);
            setFormOpen(false);
            setError(null);
          }
        })
        .catch(() => undefined);
    }, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [auditDirOrSlug, run, runAuditId]);

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
    const autoTrends = Boolean(trendsConfig?.trends_auto_fetch) && !trendsUpload;
    setRunProgress({
      percent: 8,
      label: autoTrends
        ? "Fetching Google Trends for brand…"
        : "Preparing data sources",
    });
    let succeeded = false;
    let progressTimer: ReturnType<typeof window.setInterval> | undefined;
    progressTimer = window.setInterval(() => {
      setRunProgress((current) => {
        const percent = Math.min((current?.percent ?? 8) + 4, 88);
        const label = autoTrends
          ? percent < 35
            ? "Fetching Google Trends for brand…"
            : percent < 60
              ? "Pulling analytics data"
              : percent < 80
                ? "Building the weekly model"
                : "Finalising estimate"
          : percent < 28
            ? "Connecting to Google"
            : percent < 58
              ? "Pulling analytics data"
              : percent < 78
                ? "Building the weekly model"
                : "Finalising estimate";
        return { percent, label };
      });
    }, autoTrends ? 5000 : 3500);
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
        audit_id: auditDirOrSlug,
        brand_trends_term: brandTrendsTerm || brandName || null,
        start_date: trendsConfig?.trends_expected_start || "2023-06-01",
      });
      setRun(created);
      setRunAuditId(auditDirOrSlug);
      if (created.needs_ga4_reauth) {
        setError(
          created.error ||
            "Google Analytics session expired. Disconnect and reconnect, then try again.",
        );
      } else if (created.run_id) {
        let latest = created;
        try {
          latest = await fetchAiImpactRun(created.run_id);
          setRun(latest);
          setRunAuditId(auditDirOrSlug);
        } catch {
          // The POST already returned; a follow-up GET can  fail with
          // "Failed to fetch" (proxy, timeout) even when the estimate is ready.
        }
        if (latest.jobs?.trends === "failed" && !latest.estimate) {
          setError(
            latest.trends_upload?.error
              || "Google Trends fetch failed. You can upload a CSV manually and retry.",
          );
        } else if ((latest.status === "failed" || latest.error) && !latest.estimate) {
          setError(latest.error || created.error || "Estimate failed.");
        }
        const estimate = latest.estimate ?? created.estimate;
        if (estimate) {
          window.sessionStorage.setItem(savedRunKey(auditDirOrSlug), latest.run_id || created.run_id);
          succeeded = true;
          setError(null);
          setFormOpen(false);
        }
      } else if (created.error) {
        setError(created.error);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (progressTimer) window.clearInterval(progressTimer);
      setRunProgress({
        percent: succeeded ? 100 : 0,
        label: succeeded ? "Complete" : "Could not complete",
      });
      // Keep failure state visible longer; success can clear quickly.
      window.setTimeout(() => setRunProgress(null), succeeded ? 800 : 8000);
      setBusy(false);
    }
  }

  const ga4Connected = Boolean(ga4Status?.connected);
  const gscConnected = Boolean(gscStatus?.connected);
  const est = (
    runAuditId === auditDirOrSlug ? run?.estimate : null
  ) as AiImpactEstimate | null | undefined;
  const configPresentation = aiImpactConfigPresentation(
    modelHydrated && hydratedAuditId === auditDirOrSlug,
  );
  const returnPath = connectorReturnPath();
  const loginHref = ga4LoginUrl(2, true, returnPath);
  const gscLoginHref = gscLoginUrl(returnPath);
  const trendsAutoFetch = Boolean(trendsConfig?.trends_auto_fetch);
  const trendsStartDate = trendsConfig?.trends_expected_start || "2023-06-01";
  const trendsEndDate =
    trendsConfig?.trends_expected_end || new Date().toISOString().slice(0, 10);
  const effectiveBrandTerm = (brandTrendsTerm || brandName).trim();
  const trendsReady = Boolean(trendsUpload) || (trendsAutoFetch && Boolean(effectiveBrandTerm));
  const canRunEstimate =
    !busy
    && !trendsUploadBusy
    && !modelCategoryBusy
    && Boolean(modelCategory)
    && (!!localPanel.trim() || !!propertyId)
    && conversionSpecValid
    && trendsReady
    && (!trendsUpload || trendsUpload.terms.length <= 1 || !!effectiveBrandTerm);
  const trendsUrl =
    `https://trends.google.com/trends/explore?date=${trendsStartDate}%20${trendsEndDate}`
    + `&geo=GB&q=${encodeURIComponent(effectiveBrandTerm || "brand")}&hl=en`;

  async function onTrendsFile(file: File | undefined) {
    if (!file) return;
    setTrendsUploadBusy(true);
    setTrendsUpload(null);
    setTrendsUploadError(null);
    try {
      const validated = await uploadAiImpactTrends(file, trendsStartDate, trendsEndDate);
      setTrendsUpload(validated);
      setBrandTrendsTerm(
        validated.terms.length === 1 ? validated.terms[0] : brandName || "",
      );
      setShowManualTrends(true);
    } catch (uploadError) {
      setTrendsUploadError(
        uploadError instanceof Error ? uploadError.message : "The CSV could not be validated.",
      );
    } finally {
      setTrendsUploadBusy(false);
    }
  }

  return (
    <div className="space-y-5 p-0">
      {configPresentation === "loading" ? (
        <div className="px-5 py-4 text-sm text-neutral-500" role="status">
          Loading completed AI impact model…
        </div>
      ) : configPresentation === "collapsed" ? (
        <details
        open={formOpen}
        onToggle={(event) => {
          setFormOpen((event.currentTarget as HTMLDetailsElement).open);
        }}
        className="group overflow-hidden bg-white"
      >
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 font-semibold text-neutral-900 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-blue-600">
          <span>Config</span>
          <ChevronDown
            className="h-5 w-5 shrink-0 text-neutral-500 transition-transform group-open:rotate-180"
            aria-hidden="true"
          />
        </summary>

        <div className="space-y-6 border-t border-neutral-200 px-5 py-5">
          {!modelCategory ? (
            <section className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
              <div className="font-semibold">Model category required</div>
              <p className="mt-1 text-xs leading-5">
                Existing audits created before category classification need one of
                advertiser-retail, advertiser-services, or publisher. This does not
                re-run the crawl — it only updates onboarding metadata.
              </p>
              <div className="mt-3 flex flex-wrap items-end gap-3">
                <label className="block text-xs font-medium">
                  Category
                  <select
                    className="mt-1 block rounded border border-amber-300 bg-white px-3 py-2 text-sm text-neutral-900"
                    value={modelCategoryDraft}
                    disabled={modelCategoryBusy}
                    onChange={(event) => setModelCategoryDraft(event.target.value)}
                  >
                    {MODEL_CATEGORY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  className="rounded-md bg-amber-900 px-3 py-2 text-sm font-semibold text-white disabled:opacity-60"
                  disabled={modelCategoryBusy}
                  onClick={() => void saveModelCategory({ category: modelCategoryDraft })}
                >
                  {modelCategoryBusy ? "Saving…" : "Save category"}
                </button>
                <button
                  type="button"
                  className="rounded-md border border-amber-400 bg-white px-3 py-2 text-sm font-semibold text-amber-950 disabled:opacity-60"
                  disabled={modelCategoryBusy}
                  onClick={() => void saveModelCategory({ classify: true })}
                >
                  Classify with Gemini
                </button>
              </div>
            </section>
          ) : (
            <p className="text-xs text-neutral-500">
              Model category: <span className="font-medium text-neutral-800">{modelCategory}</span>
            </p>
          )}

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

          {/* Search Console temporarily hidden from AI Impact UI */}
          {SHOW_GSC_SECTION && (
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
          )}

          <section className="border-t border-neutral-200 pt-5">
            <div className="mb-2 flex items-center gap-2">
              <img
                src="/assets/logos/trends.png"
                alt=""
                className="h-5 w-5 shrink-0 object-contain"
              />
              <h3 className="text-sm font-semibold text-neutral-900">
                Google Trends{" "}
                <span className="font-normal text-neutral-500">
                  {effectiveBrandTerm ? "(auto from brand name)" : "(required)"}
                </span>
              </h3>
            </div>
            <p className="max-w-2xl text-xs leading-5 text-neutral-600">
              Weekly UK search interest controls for demand shifts in the estimate.
              {effectiveBrandTerm
                ? " When you run the estimate, we fetch Trends for your setup brand automatically."
                : " Add a brand name in setup, or upload a weekly Interest over time CSV."}
            </p>

            {effectiveBrandTerm ? (
              <div
                className={`mt-3 flex max-w-2xl items-start gap-2 rounded-md border px-3 py-2.5 text-sm ${
                  trendsAutoFetch
                    ? "border-green-200 bg-green-50 text-green-900"
                    : "border-amber-200 bg-amber-50 text-amber-950"
                }`}
                role="status"
              >
                {trendsAutoFetch ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                ) : (
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                )}
                <div>
                  {trendsAutoFetch ? (
                    <>
                      <div className="font-semibold">
                        Will fetch Trends for “{effectiveBrandTerm}”
                      </div>
                      <div className="mt-0.5 text-xs leading-5">
                        United Kingdom · weekly · {trendsStartDate} to last completed Saturday.
                        Saved under this audit as <code className="text-[11px]">google_trends/</code>.
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="font-semibold">
                        Brand “{effectiveBrandTerm}” is ready, but auto-fetch is not enabled here
                      </div>
                      <div className="mt-0.5 text-xs leading-5">
                        Redeploy geo-audit with <code className="text-[11px]">GOOGLE_TRENDS_JOB_NAME</code>{" "}
                        set, or upload a CSV below.
                      </div>
                    </>
                  )}
                </div>
              </div>
            ) : (
              <div
                className="mt-3 flex max-w-2xl items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-sm text-amber-950"
                role="status"
              >
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <div>
                  <div className="font-semibold">Brand name missing</div>
                  <div className="mt-0.5 text-xs leading-5">
                    Add a brand name in setup / config, or upload a Trends CSV below.
                  </div>
                </div>
              </div>
            )}

            {run?.jobs?.trends && run.jobs.trends !== "skipped" && run.jobs.trends !== "not_started" ? (
              <div className="mt-3 max-w-2xl text-xs text-neutral-600">
                Last Trends step:{" "}
                <span className="font-medium text-neutral-800">{run.jobs.trends}</span>
                {run.trends_upload?.week_count
                  ? ` · ${run.trends_upload.week_count} weeks`
                  : null}
                {run.trends_upload?.query_term || run.trends_upload?.terms?.[0]
                  ? ` · ${run.trends_upload.query_term || run.trends_upload.terms[0]}`
                  : null}
              </div>
            ) : null}

            <details
              className="mt-4 max-w-2xl"
              open={
                showManualTrends
                || !trendsAutoFetch
                || !effectiveBrandTerm
                || Boolean(trendsUploadError)
              }
              onToggle={(event) =>
                setShowManualTrends((event.target as HTMLDetailsElement).open)
              }
            >
              <summary className="cursor-pointer text-xs font-semibold text-neutral-700 underline decoration-neutral-300 underline-offset-2">
                {trendsAutoFetch && effectiveBrandTerm
                  ? "Use a manual CSV instead (optional)"
                  : "Upload Google Trends CSV"}
              </summary>

              <ol className="mt-3 list-decimal space-y-1.5 pl-5 text-xs leading-5 text-neutral-700">
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
                <li>
                  Keep United Kingdom and {trendsStartDate} to {trendsEndDate}
                  {trendsAutoFetch ? " (brand term is enough for auto-fetch)." : "."}
                </li>
                <li>
                  Download the Interest over time CSV and upload it unchanged.
                </li>
              </ol>

              <label className="mt-4 flex cursor-pointer items-center gap-3 rounded-md border border-dashed border-neutral-300 bg-neutral-50 px-4 py-3 text-sm transition-colors hover:border-neutral-400 hover:bg-neutral-100 focus-within:ring-2 focus-within:ring-blue-600">
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
                  className="mt-3 flex items-start gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2.5 text-sm text-green-900"
                  role="status"
                >
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  <div>
                    <div className="font-semibold">
                      {trendsUpload.filename || "CSV"} is ready ({trendsUpload.week_count} weeks)
                    </div>
                    <div className="mt-0.5 text-xs">
                      {trendsUpload.start_date} to {trendsUpload.end_date}:{" "}
                      {trendsUpload.terms.join(", ")}
                    </div>
                    <button
                      type="button"
                      className="mt-2 text-xs font-semibold underline"
                      onClick={() => {
                        setTrendsUpload(null);
                        setBrandTrendsTerm(brandName);
                      }}
                    >
                      Clear upload and use auto-fetch
                    </button>
                    {trendsUpload.warnings.map((warning) => (
                      <div key={warning} className="mt-1 text-xs">
                        {warning}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
              {trendsUpload && trendsUpload.terms.length > 1 ? (
                <label className="mt-3 block text-sm font-medium text-neutral-800">
                  Brand search term
                  <select
                    className="mt-1 w-full rounded border border-neutral-300 bg-white px-3 py-2"
                    value={brandTrendsTerm}
                    onChange={(event) => setBrandTrendsTerm(event.target.value)}
                  >
                    <option value="">Select the brand term</option>
                    {trendsUpload.terms.map((term) => (
                      <option key={term} value={term}>{term}</option>
                    ))}
                  </select>
                </label>
              ) : null}

              {trendsUploadError ? (
                <div
                  className="mt-3 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2.5 text-sm text-red-900"
                  role="alert"
                >
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  <div>
                    <div className="font-semibold">This CSV cannot be used</div>
                    <div className="mt-0.5 text-xs leading-5">{trendsUploadError}</div>
                  </div>
                </div>
              ) : null}
            </details>
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
            disabled={!canRunEstimate}
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
      ) : null}

      {est ? (
        <OverallImpactTable
          estimate={est}
          run={run}
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

function fmtPercent(value: number | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(2)}%`;
}

function fmtSignedPercent(value: number | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const pct = value * 100;
  const body = Math.abs(pct) >= 10 ? pct.toFixed(0) : pct.toFixed(digits);
  return `${pct > 0 ? "+" : ""}${body}%`;
}

function fmtPerDay(value: number | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const abs = Math.abs(value);
  const body = abs >= 100 ? Math.round(value).toLocaleString() : value.toLocaleString(undefined, {
    maximumFractionDigits: abs >= 10 ? 0 : 1,
  });
  return value > 0 ? `+${body}` : body;
}

function fmtInterval(interval: AiImpactPosteriorInterval): string {
  return `${fmt(interval.posterior_mean)} (${fmt(interval.lower_94)} to ${fmt(interval.upper_94)})`;
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
  run,
  conversionEventName,
}: {
  estimate: AiImpactEstimate;
  run: AiImpactRunStatus | null;
  conversionEventName: string;
}) {
  const conversionEvents = useMemo(() => {
    try {
      return parseConversionEvents(conversionEventName);
    } catch {
      return parseConversionEvents("purchase");
    }
  }, [conversionEventName]);
  const conversionMethodNote = formatConversionMethodNote(conversionEvents);
  const weekly = useMemo(
    () => filterCompletedWeeks(estimate.weekly_series ?? []),
    [estimate.weekly_series],
  );
  const outcomes = estimate.posterior_outcomes;
  const refitStatus = aiImpactRefitStatus(run);
  const windowDays = estimateWindowDayCount(estimate);

  const chartData = useMemo(() => {
    return weekly.map((point: AiImpactWeeklyPoint) => ({
      week: point.week,
      "SEO actual": point.seo_sessions,
      "SEO counterfactual":
        point.seo_uncapped_counterfactual?.posterior_mean,
      "Direct actual": point.direct_sessions,
      "Direct counterfactual":
        point.direct_uncapped_counterfactual?.posterior_mean,
    }));
  }, [weekly]);

  const methodNotes = estimate.method_notes?.length
    ? estimate.method_notes
    : [
        "This is a legacy estimate. Re-run to use the hierarchical posterior.",
      ];

  return (
    <section className="overflow-hidden rounded-lg border border-neutral-300 bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
        <div>
          <h3 className="font-semibold text-neutral-900">Estimated AI impact</h3>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-neutral-500">
            {aiImpactResultLabel(estimate.estimate_mode)}
          </p>
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-neutral-600">
            {estimate.category ? <span>Category: {estimate.category}</span> : null}
            {estimate.model_artifact_version ? (
              <span>Model: {estimate.model_artifact_version}</span>
            ) : null}
            {estimate.signal_artifact_version ? (
              <span>Signal: {estimate.signal_artifact_version}</span>
            ) : null}
            {shouldShowAwaitingSignalWeeks(
              estimate.awaiting_signal_weeks,
              estimate.estimate_mode,
              refitStatus,
            ) ? (
              <span className="text-amber-700">
                {estimate.awaiting_signal_weeks} week(s) awaiting portfolio refresh
              </span>
            ) : null}
          </div>
        </div>
        <div className="text-right">
          {refitStatus ? <div className="text-sm font-semibold text-neutral-800">
            Refit: {refitStatus.replaceAll("_", " ")}
          </div> : null}
          <div className="mt-0.5 text-xs text-neutral-500">
            {estimate.window_start} to {estimate.window_end}
            {Number.isFinite(windowDays) ? (
              <span> · {windowDays} days</span>
            ) : null}
          </div>
        </div>
      </div>

      {chartData.length > 0 ? (
        <div className="border-t border-neutral-200 px-5 py-5">
          <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Weekly actual vs counterfactual
          </h4>
          <p className="mb-4 max-w-3xl text-xs leading-relaxed text-neutral-500">
            Counterfactual lines freeze the portfolio AI signal at its pre-ramp baseline.
            Google Search Console is chart context only and is not a model covariate.
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
              <Line type="monotone" dataKey="SEO actual" stroke="#0f766e" strokeWidth={2} dot={false} />
              <Line
                type="monotone"
                dataKey="SEO counterfactual"
                stroke="#14b8a6"
                strokeWidth={2}
                strokeDasharray="4 3"
                dot={false}
              />
              <Line type="monotone" dataKey="Direct actual" stroke="#1d4ed8" strokeWidth={2} dot={false} />
              <Line
                type="monotone"
                dataKey="Direct counterfactual"
                stroke="#60a5fa"
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

      {outcomes ? <div className="overflow-x-auto border-t border-neutral-200">
        <table className="w-full text-left text-sm">
          <thead className="bg-neutral-50 text-xs uppercase tracking-wide text-neutral-500">
            <tr>
              <th className="px-5 py-3 font-medium">Channel</th>
              <th className="px-5 py-3 font-medium">Primary posterior mean (94% CI)</th>
              <th className="px-5 py-3 font-medium">Est. avg / day</th>
              <th className="px-5 py-3 font-medium">Est. % of total sessions</th>
              <th className="px-5 py-3 font-medium">Capped sensitivity (94% CI)</th>
              <th className="px-5 py-3 font-medium">Conclusion</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-neutral-200">
            {(["seo", "direct"] as const).map((channel) => {
              const outcome = outcomes[channel];
              const robust = isRobustSensitivity(outcome);
              const impact = outcome.uncapped.posterior_mean;
              const perDay = estimatedSessionsPerDay(impact, windowDays);
              const share = estimatedImpactShareOfTotal(impact, estimate.total_sessions);
              return <tr key={channel}>
                <th className="px-5 py-4 font-medium uppercase text-neutral-900">
                  {channel}
                </th>
                <td className="px-5 py-4 tabular-nums">{fmtInterval(outcome.uncapped)}</td>
                <td className="px-5 py-4 tabular-nums">
                  {fmtPerDay(perDay)}
                  <div className="mt-1 text-xs font-normal normal-case text-neutral-500">
                    sessions / day
                  </div>
                </td>
                <td className="px-5 py-4 tabular-nums">
                  {fmtSignedPercent(share)}
                  <div className="mt-1 text-xs font-normal normal-case text-neutral-500">
                    of {fmt(estimate.total_sessions)} total
                  </div>
                </td>
                <td className="px-5 py-4 tabular-nums">
                  {fmtInterval(outcome.capped)}
                  <div className="mt-1 text-xs text-neutral-500">
                    Delta {fmt(outcome.sensitivity_delta)}
                  </div>
                </td>
                <td className="px-5 py-4">
                  <span className={robust ? "text-green-700" : "text-amber-700"}>
                    {robust ? "Robust" : "Tail-sensitive / uncertain"}
                  </span>
                </td>
              </tr>;
            })}
          </tbody>
        </table>
      </div> : (
        <div className="border-t border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-900">
          This saved run predates hierarchical posterior scoring. Re-run it to obtain
          SEO and Direct posterior intervals.
        </div>
      )}

      <div className="grid gap-4 border-t border-neutral-200 px-5 py-4 sm:grid-cols-3">
        <div>
          <div className="text-xs uppercase tracking-wide text-neutral-500">Measured AI sessions</div>
          <div className="mt-1 text-lg font-semibold">{fmt(estimate.direct_ai_sessions)}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-neutral-500">Measured conversions</div>
          <div className="mt-1 text-lg font-semibold">{fmt(estimate.direct_ai_purchases)}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-neutral-500">Measured AI CVR</div>
          <div className="mt-1 text-lg font-semibold">{fmtPercent(estimate.ai_cvr)}</div>
        </div>
      </div>

      <div className="space-y-3 border-t border-neutral-200 px-5 py-4">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          How this estimate is generated
        </h4>
        <ol className="list-decimal space-y-1.5 pl-4 text-xs leading-relaxed text-neutral-600">
          <li>
            Build a weekly panel from GA4 channel data
            {conversionMethodNote}
            , plus one designated brand Google Trends series.
          </li>
          <li>
            Apply the inferred category&apos;s posterior AI coefficient draws to this
            site&apos;s observed SEO and Direct traffic.
          </li>
          <li>
            Compute the primary uncapped scaled-log signal and capped sensitivity.
          </li>
          <li>
            After eight eligible weeks, queue a site-inclusive hierarchical refit.
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
        <p className="text-xs leading-relaxed text-neutral-500">
          Purchase impact is not modeled. Purchases and CVR above are measured GA4 quantities.
        </p>
      </div>
    </section>
  );
}

