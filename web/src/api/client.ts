import { auditSlug } from "../lib/auditPath";
import type {
  AppConfig,
  ArchiveResponse,
  AuditDetail,
  AuditRunProgressPayload,
  AuditRunProgressStep,
  AuditRunStatusResponse,
  AuthStatus,
  CompetitorCrawlStatusResponse,
  CompetitorComparisonResponse,
  DomainOption,
  Ga4Status,
  Ga4TopPagesResponse,
  LocalAudit,
  CompetitorDetail,
  ProductServiceRow,
  PromptPerformanceContext,
  PromptSentimentResponse,
  LiveProbePerPrompt,
  RedditPost,
  TopCitedSite,
  TopCitedUrl,
  VerifiedSite,
  ProbeSiteProtection,
  YouTubeVideo,
} from "../types";

const API = "/api";

const withCredentials: RequestInit = { credentials: "include" };

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...withCredentials,
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export function fetchAuthStatus(): Promise<AuthStatus> {
  return json<AuthStatus>("/auth/status");
}

export async function logoutApi(): Promise<void> {
  await json<{ ok: boolean }>("/auth/logout", { method: "POST" });
}

export function fetchConfig(): Promise<AppConfig> {
  return json<AppConfig>("/config");
}

export function fetchLocalAudits(opts?: {
  limit?: number;
  offset?: number;
}): Promise<LocalAudit[]> {
  const params = new URLSearchParams();
  if (opts?.limit != null) params.set("limit", String(opts.limit));
  if (opts?.offset != null && opts.offset > 0) {
    params.set("offset", String(opts.offset));
  }
  const qs = params.toString();
  return json<LocalAudit[]>(`/audits/local${qs ? `?${qs}` : ""}`);
}

/** ``auditDirOrSlug`` — full ``audit_output/...`` path or folder id under ``audit_output/``. */
export function fetchAudit(
  auditDirOrSlug: string,
  init?: RequestInit,
): Promise<AuditDetail> {
  return json<AuditDetail>(
    `/audits/${encodeURIComponent(auditSlug(auditDirOrSlug))}`,
    init,
  );
}

export function fetchLatestAudit(): Promise<{ audit_dir: string; summary: AuditDetail["summary"] }> {
  return json("/audits/latest");
}

export function fetchSampleAudit(): Promise<{ audit_dir: string; summary: AuditDetail["summary"] }> {
  return json("/audits/sample");
}

export function fetchArchive(): Promise<ArchiveResponse> {
  return json<ArchiveResponse>("/archive?mine_only=true");
}

export function fetchIndustries(): Promise<string[]> {
  return json<string[]>("/industries");
}

export function fetchDomainSuggest(q: string): Promise<DomainOption[]> {
  return json<DomainOption[]>(`/domains/suggest?q=${encodeURIComponent(q)}`);
}

export function fetchGa4Status(): Promise<Ga4Status> {
  return json<Ga4Status>("/ga4/status");
}

export function ga4LoginUrl(
  wizardStep = 2,
  afterYes = true,
  returnTo?: string | null,
): string {
  const params = new URLSearchParams({
    wizard_step: String(wizardStep),
    after_yes: afterYes ? "1" : "0",
  });
  if (returnTo && returnTo.startsWith("/")) {
    params.set("return_to", returnTo);
  }
  return `${API}/ga4/login?${params}`;
}

export function saveGa4Selection(
  propertyId: string,
  aiChannelNames: string,
  accountId = "",
  conversionEventName?: string,
): Promise<{ ok: boolean }> {
  return json("/ga4/selection", {
    method: "PUT",
    body: JSON.stringify({
      property_id: propertyId,
      account_id: accountId,
      ai_channel_names: aiChannelNames,
      ...(conversionEventName ? { conversion_event_name: conversionEventName } : {}),
    }),
  });
}

export function clearGa4Selection(): Promise<{ ok: boolean }> {
  return json("/ga4/selection", { method: "DELETE" });
}

export function disconnectGa4(): Promise<{ ok: boolean }> {
  return json("/ga4/disconnect", { method: "POST" });
}

export function fetchGa4TopPages(
  origin: string,
  limit = 100,
): Promise<Ga4TopPagesResponse> {
  const params = new URLSearchParams({
    origin,
    limit: String(Math.min(Math.max(limit, 1), 100)),
  });
  return json<Ga4TopPagesResponse>(`/ga4/top-pages?${params}`);
}

export function discoverPages(payload: {
  brand_website: string;
  market_country?: string;
  market_country_code?: string;
}): Promise<{ urls: string[]; total_discovered: number; truncated: boolean }> {
  return json("/wizard/discover-pages", {
    method: "POST",
    body: JSON.stringify(payload),
    signal: AbortSignal.timeout(30_000),
  });
}

export function probeSiteProtection(url: string): Promise<ProbeSiteProtection> {
  return json("/wizard/probe-site-protection", {
    method: "POST",
    body: JSON.stringify({ url }),
    signal: AbortSignal.timeout(15_000),
  });
}

export function verifyBrandSite(url: string): Promise<VerifiedSite> {
  // Cloudflare-protected sites need Playwright (warm + challenge wait); allow up to 2 min.
  return json("/wizard/verify-site", {
    method: "POST",
    body: JSON.stringify({ url }),
    signal: AbortSignal.timeout(120_000),
  });
}

export function suggestCompetitors(payload: {
  brand_website: string;
  products_and_services: string[];
  market_country?: string;
  market_country_code?: string;
}): Promise<{ rows: Omit<CompetitorDetail, "included">[] }> {
  return json("/wizard/suggest-competitors", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function normalizeCompetitorUrl(
  url: string,
): Promise<{ canonical_url: string; favicon_url: string }> {
  return json("/wizard/normalize-competitor-url", {
    method: "POST",
    body: JSON.stringify({ url }),
  });
}

export function suggestProductsServices(payload: {
  brand_website: string;
  market_country?: string;
  market_country_code?: string;
}): Promise<{ rows: ProductServiceRow[] }> {
  return json("/wizard/suggest-products", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function suggestPromptsForProducts(payload: {
  brand_website: string;
  products: string[];
  market_country?: string;
  market_country_code?: string;
}): Promise<{ rows: ProductServiceRow[] }> {
  return json("/wizard/suggest-prompts-for-products", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchRedditInsights(
  auditDirOrSlug: string,
): Promise<{ posts: RedditPost[]; total: number; brand_name: string }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/reddit-insights`);
}

export function fetchYouTubeInsights(
  auditDirOrSlug: string,
): Promise<{
  videos: YouTubeVideo[];
  total: number;
  reviewed_prompt_count: number;
  brand_name: string;
  api_available: boolean;
}> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/youtube-insights`);
}

export interface ContentEvidenceExample {
  url: string;
  title: string;
  snippet: string;
}

export interface ContentQualityDetails {
  score: number;
  overall_cap?: number | null;
  overall_cap_notes?: string[];
  gemini_overlay?: {
    available?: boolean;
    status?: string;
    merge_rule?: string;
    pages_analyzed?: number;
    languages_seen?: string[];
    finding_summary?: string;
    schema_version?: number;
  };
  components: {
    key: string;
    title: string;
    score: number;
    weight_pct: number;
    detail: string;
    finding_summary: string;
    evidence_example: string;
    report_section?: string;
    scoring_source?: string;
    site_examples?: {
      url: string;
      title: string;
      excerpt: string;
      context: string;
    }[];
  }[];
  eeat: {
    name: string;
    tagline: string;
    what_it_means: string;
    how_scored: string;
    score: number;
    evidence: ContentEvidenceExample[];
    evidence_note: string;
    scoring_source?: string;
  }[];
  structure_answerability: {
    key: string;
    title: string;
    score: number;
    description: string;
    examples: ContentEvidenceExample[];
    empty_message: string;
    scoring_source?: string;
  }[];
  schema_entity: {
    score: number;
    summary: string;
    strengths: string[];
    improvements: string[];
    evidence: {
      url: string;
      title: string;
      types: string[];
      blocks: number;
      same_as_count: number;
    }[];
  };
  brand_visibility_authority: {
    score: number;
    brand_query: string;
    method_note: string;
    strengths: string[];
    improvements: string[];
    rows: {
      platform?: string;
      present?: boolean;
      status?: string;
      url?: string;
      impact?: string;
      citation_confirmed?: boolean;
    }[];
  };
}

export function fetchScoreBreakdown(
  auditDirOrSlug: string,
): Promise<{
  ai_visibility: number | null;
  technical_setup: number | null;
  content_structure: number | null;
  overall: number | null;
  prompt_metrics?: {
    score: number;
    visibility_pct: number;
    sov_pct: number;
    sov_performance_score: number;
    sov_rank: number | null;
    competitor_count: number;
    detected_competitor_count?: number;
    sov_competitor_limit?: number;
    top_competitor_sov_pct: number;
    average_competitor_sov_pct: number;
    visible_prompt_count: number;
    prompt_count: number;
    visible_response_count?: number;
    response_count?: number;
    tested_prompt_count?: number;
    brand_hits: number;
    competitor_hits: number;
  } | null;
  platform_readiness?: {
    key: string;
    name: string;
    score: number;
    gap: string;
  }[];
  crawler_access?: {
    score?: number;
    strengths?: string[];
    improvements?: string[];
    rows: {
      crawler: string;
      tier: number;
      recommendation: string;
      reason: string;
      can_fetch: boolean;
      aligned: boolean | null;
    }[];
  };
  content_quality_details?: ContentQualityDetails;
  details?: Record<string, {
    score: number;
    components: {
      key: string;
      title: string;
      score: number;
      weight_pct: number;
      detail: string;
      finding_summary: string;
      evidence_example: string;
      strengths?: string[];
      improvements?: string[];
      criteria?: {
        key: string;
        title: string;
        score: number;
        weight_pct: number;
        strengths?: string[];
        improvements?: string[];
      }[];
      report_section?: string;
      site_examples?: {
        url: string;
        title: string;
        excerpt: string;
        context: string;
      }[];
    }[];
  }>;
}> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/score-breakdown`);
}

export function fetchExecutiveSummary(
  auditDirOrSlug: string,
): Promise<{ paragraph_html: string; key_findings?: string[]; generated_at?: string }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/executive-summary`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh: false }),
  });
}

/** Slim all-locales GET can be multi‑MB on huge audits — fail fast instead of spinning forever. */
export const PROMPT_PERFORMANCE_FETCH_TIMEOUT_MS = 45_000;
export const PROMPT_PERFORMANCE_SUMMARY_TIMEOUT_MS = 15_000;

function promptPerfSignal(ms: number, init?: RequestInit): AbortSignal {
  const timeout = AbortSignal.timeout(ms);
  if (!init?.signal) return timeout;
  if (typeof AbortSignal.any === "function") {
    return AbortSignal.any([timeout, init.signal]);
  }
  return init.signal;
}

function isAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === "TimeoutError")
    || (err instanceof DOMException && err.name === "AbortError")
    || (err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError"))
  );
}

export function fetchPromptPerformanceContext(
  auditDirOrSlug: string,
  opts?: { locale?: string; full?: boolean; signal?: AbortSignal; timeoutMs?: number },
): Promise<PromptPerformanceContext> {
  const slug = auditSlug(auditDirOrSlug);
  const params = new URLSearchParams();
  if (opts?.locale) params.set("locale", opts.locale);
  if (opts?.full) params.set("full", "1");
  const qs = params.toString();
  const timeoutMs = opts?.timeoutMs ?? PROMPT_PERFORMANCE_FETCH_TIMEOUT_MS;
  return json<PromptPerformanceContext>(
    `/audits/${encodeURIComponent(slug)}/prompt-performance${qs ? `?${qs}` : ""}`,
    { signal: promptPerfSignal(timeoutMs, opts) },
  ).catch((err) => {
    if (isAbortError(err)) {
      throw new Error(
        opts?.locale
          ? `Timed out loading prompt data for ${opts.locale}. Try again or pick another market.`
          : "Timed out loading prompt-performance data. Try a single market/language view.",
      );
    }
    throw err;
  });
}

export function fetchPromptPerformanceSummary(auditDirOrSlug: string, init?: RequestInit): Promise<{
  brand_name?: string;
  brand_site_url?: string;
  prompt_count?: number;
  competitors?: PromptPerformanceContext["competitors"];
  primary_market?: PromptPerformanceContext["primary_market"];
  prompt_locales?: PromptPerformanceContext["prompt_locales"];
  default_locale_key?: string;
  locale_spread?: PromptPerformanceContext["locale_spread"];
  overall_metrics?: PromptPerformanceContext["overall_metrics"];
  keyword_sentiment?: {
    mentioned_count: number;
    positive_count: number;
    negative_count: number;
    score_percent: number | null;
    label: string;
  };
  highlight?: { brand?: string; brand_match_tokens?: string[] };
  live_probe_in_progress?: boolean;
  has_probe_data?: boolean;
  active_platforms?: string[];
  metrics_from_cache?: boolean;
}> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/summary`, {
    ...init,
    signal: promptPerfSignal(PROMPT_PERFORMANCE_SUMMARY_TIMEOUT_MS, init),
  });
}

export function fetchPromptPerformanceLocale(
  auditDirOrSlug: string,
  localeKey: string,
  init?: RequestInit,
): Promise<PromptPerformanceContext> {
  const slug = auditSlug(auditDirOrSlug);
  const key = localeKey === "__overall__" ? "overall" : localeKey;
  return json<PromptPerformanceContext>(
    `/audits/${encodeURIComponent(slug)}/prompt-performance/locales/${encodeURIComponent(key)}`,
    {
      ...init,
      signal: promptPerfSignal(PROMPT_PERFORMANCE_FETCH_TIMEOUT_MS, init),
    },
  ).catch((err) => {
    if (isAbortError(err)) {
      throw new Error(
        `Timed out loading locale “${localeKey}”. Try again or pick another market.`,
      );
    }
    throw err;
  });
}

/** Citations page: aggregated domains/URLs only (no per_prompt / runs). */
export type CitationsViewPayload = {
  brand_name?: string;
  brand_site_url?: string;
  competitors?: PromptPerformanceContext["competitors"];
  primary_market?: PromptPerformanceContext["primary_market"];
  prompt_locales?: PromptPerformanceContext["prompt_locales"];
  default_locale_key?: string;
  locale_spread?: PromptPerformanceContext["locale_spread"];
  locale_key: string;
  prompt_count?: number;
  has_probe_data?: boolean;
  top_cited_sites: TopCitedSite[];
  top_cited_urls: TopCitedUrl[];
  aio_cited_urls: TopCitedUrl[];
  metrics_from_cache?: boolean;
};

export function fetchCitationsView(
  auditDirOrSlug: string,
  opts?: { locale?: string; signal?: AbortSignal },
): Promise<CitationsViewPayload> {
  const slug = auditSlug(auditDirOrSlug);
  const params = new URLSearchParams();
  if (opts?.locale) {
    const key = opts.locale === "__overall__" ? "overall" : opts.locale;
    params.set("locale", key);
  }
  const qs = params.toString();
  return json<CitationsViewPayload>(
    `/audits/${encodeURIComponent(slug)}/prompt-performance/citations${qs ? `?${qs}` : ""}`,
    { signal: promptPerfSignal(PROMPT_PERFORMANCE_SUMMARY_TIMEOUT_MS, opts) },
  ).catch((err) => {
    if (isAbortError(err)) {
      throw new Error("Timed out loading citations. Try a single market/language view.");
    }
    throw err;
  });
}

type PromptDetailPayload = { prompt: LiveProbePerPrompt; locale_key?: string };

/** Client-side cache: audit + locale + promptId → full reply row (overlay re-opens). */
const promptDetailCache = new Map<string, PromptDetailPayload>();

function promptDetailCacheKey(slug: string, promptId: string, locale?: string): string {
  const loc = locale && locale !== "__overall__" ? locale : "";
  return `${slug}::${loc}::${promptId}`;
}

export function fetchPromptPerformanceDetail(
  auditDirOrSlug: string,
  promptId: string,
  locale?: string,
  init?: RequestInit,
): Promise<PromptDetailPayload> {
  const slug = auditSlug(auditDirOrSlug);
  const cacheKey = promptDetailCacheKey(slug, promptId, locale);
  const cached = promptDetailCache.get(cacheKey);
  if (cached) {
    return Promise.resolve(cached);
  }
  const params = new URLSearchParams();
  if (locale && locale !== "__overall__") params.set("locale", locale);
  const qs = params.toString();
  return json<PromptDetailPayload>(
    `/audits/${encodeURIComponent(slug)}/prompt-performance/prompts/${encodeURIComponent(promptId)}${qs ? `?${qs}` : ""}`,
    init,
  ).then((res) => {
    if (res?.prompt) {
      promptDetailCache.set(cacheKey, res);
      // Cap cache size for long sessions across many prompts.
      if (promptDetailCache.size > 200) {
        const oldest = promptDetailCache.keys().next().value;
        if (oldest) promptDetailCache.delete(oldest);
      }
    }
    return res;
  });
}

/** Synchronous cache peek for overlay open (avoid loading flash on re-open). */
export function peekPromptPerformanceDetail(
  auditDirOrSlug: string,
  promptId: string,
  locale?: string,
): PromptDetailPayload | null {
  return promptDetailCache.get(promptDetailCacheKey(auditSlug(auditDirOrSlug), promptId, locale)) ?? null;
}

/** Drop cached overlay replies (e.g. after a re-probe). */
export function invalidatePromptPerformanceDetailCache(auditDirOrSlug?: string): void {
  if (!auditDirOrSlug) {
    promptDetailCache.clear();
    return;
  }
  const prefix = `${auditSlug(auditDirOrSlug)}::`;
  for (const key of [...promptDetailCache.keys()]) {
    if (key.startsWith(prefix)) promptDetailCache.delete(key);
  }
}

export function trackCompetitor(
  auditDirOrSlug: string,
  competitor: { name: string; website?: string },
): Promise<{ tracked: boolean; already_tracked: boolean }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/competitors/track`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(competitor),
  });
}

export function runCompetitorCrawl(
  auditDirOrSlug: string,
): Promise<{
  ok: boolean;
  audit_dir: string;
  status: string;
  job_type?: string;
  competitor_count?: number;
}> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/competitors/crawl`, {
    method: "POST",
  });
}

export function fetchCompetitorCrawlStatus(
  auditDirOrSlug: string,
): Promise<CompetitorCrawlStatusResponse> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/competitors/crawl-status`);
}

export function fetchCompetitorComparison(
  auditDirOrSlug: string,
): Promise<CompetitorComparisonResponse> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/competitors/comparison`);
}

export function markCompetitorCrawlSeen(
  auditDirOrSlug: string,
): Promise<CompetitorCrawlStatusResponse> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/competitors/crawl-status/seen`, {
    method: "POST",
  });
}

export function fetchPromptSentiment(auditDirOrSlug: string): Promise<PromptSentimentResponse> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/sentiment`);
}

export function runPromptPerformanceProbes(
  auditDirOrSlug: string,
  reportModeOrOpts:
    | boolean
    | {
        reportMode?: boolean;
        failedOnly?: boolean;
        localeKeys?: string[];
      } = true,
): Promise<{
  status: string;
  audit_id: string;
  request_id: string;
  execution: string;
  already_running: boolean;
  locale_count?: number;
  failed_only?: boolean;
}> {
  const slug = auditSlug(auditDirOrSlug);
  const opts = typeof reportModeOrOpts === "boolean"
    ? { reportMode: reportModeOrOpts }
    : reportModeOrOpts;
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/run-probes`, {
    method: "POST",
    body: JSON.stringify({
      report_mode: opts.reportMode ?? true,
      failed_only: opts.failedOnly ?? false,
      locale_keys: opts.localeKeys ?? null,
    }),
  });
}

export function highlightPromptReply(
  auditDirOrSlug: string,
  text: string,
): Promise<{ html: string }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/highlight`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export function runAioProbes(
  auditDirOrSlug: string,
  maxPrompts = 25,
): Promise<{
  status: string;
  audit_id: string;
  request_id: string;
  execution: string;
  already_running: boolean;
}> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/run-aio-probes`, {
    method: "POST",
    body: JSON.stringify({ max_prompts: maxPrompts }),
  });
}

export function fetchAioAvailability(
  auditDirOrSlug: string,
): Promise<{ available: boolean; reason: string | null }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/aio-availability`);
}

export function trackPromptCompetitor(
  auditDirOrSlug: string,
  websiteUrl: string,
  brandName: string,
): Promise<{ ok: boolean; added: boolean }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/track-competitor`, {
    method: "POST",
    body: JSON.stringify({ website_url: websiteUrl, brand_name: brandName }),
  });
}

export function addPromptToAudit(
  auditDirOrSlug: string,
  payload: { prompt: string; category?: string; tags?: string[]; run_probes?: boolean },
): Promise<{ added: boolean; run_probes: boolean; live_probe?: unknown }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/add-prompt`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function addTagsToPrompts(
  auditDirOrSlug: string,
  payload: {
    selections: { product_or_service: string; prompt: string }[];
    tags: string[];
  },
): Promise<{ updated: number; tags: string[] }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/add-tags`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updatePromptLocales(
  auditDirOrSlug: string,
  locales: {
    country?: string;
    country_code?: string;
    language?: string;
    language_name?: string;
    key?: string;
    label?: string;
  }[],
): Promise<{ ok: boolean; prompt_locales: unknown[] }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/locales`, {
    method: "PUT",
    body: JSON.stringify({ locales }),
  });
}

export function reportHtmlUrl(
  auditDirOrSlug: string,
  section?: string,
  embed = false,
): string {
  const slug = auditSlug(auditDirOrSlug);
  const params = embed ? "?embed=1" : "";
  const base = `${API}/audits/${encodeURIComponent(slug)}/report.html${params}`;
  if (!section || section === "prompts" || section === "prompt_performance" || section === "citations") return base;
  return `${base}#${section}`;
}

export function reportPdfUrl(auditDirOrSlug: string, section?: string): string {
  const slug = auditSlug(auditDirOrSlug);
  const base = `${API}/audits/${encodeURIComponent(slug)}/report.pdf`;
  if (!section) return base;
  return `${base}?section=${encodeURIComponent(section)}`;
}

export function reportPdfExportFileUrl(auditDirOrSlug: string, section?: string): string {
  const slug = auditSlug(auditDirOrSlug);
  const base = `${API}/audits/${encodeURIComponent(slug)}/exports/pdf/file`;
  if (!section) return base;
  return `${base}?section=${encodeURIComponent(section)}`;
}

export interface PdfExportStatus {
  audit_id: string;
  section: string;
  status: string;
  ready: boolean;
  request_id?: string;
  execution?: string;
  error?: string | null;
  artifact?: string | null;
  bytes?: number | null;
}

export function startPdfExport(
  auditDirOrSlug: string,
  section?: string,
): Promise<PdfExportStatus & { already_running?: boolean }> {
  const slug = auditSlug(auditDirOrSlug);
  const qs = section ? `?section=${encodeURIComponent(section)}` : "";
  return json(`/audits/${encodeURIComponent(slug)}/exports/pdf${qs}`, { method: "POST" });
}

export function fetchPdfExportStatus(
  auditDirOrSlug: string,
  section?: string,
): Promise<PdfExportStatus> {
  const slug = auditSlug(auditDirOrSlug);
  const qs = section ? `?section=${encodeURIComponent(section)}` : "";
  return json(`/audits/${encodeURIComponent(slug)}/exports/pdf${qs}`);
}

export function reportAllPagesHtmlUrl(auditDirOrSlug: string): string {
  const slug = auditSlug(auditDirOrSlug);
  return `${API}/audits/${encodeURIComponent(slug)}/report-all-pages.html`;
}

export function reportSectionHtmlUrl(auditDirOrSlug: string, section: string): string {
  const slug = auditSlug(auditDirOrSlug);
  return `${API}/audits/${encodeURIComponent(slug)}/report-section.html?section=${encodeURIComponent(section)}`;
}

export interface RunAuditPayload {
  brand_name: string;
  brand_website: string;
  industry: string;
  competitors: string[];
  max_urls?: number;
  delay?: number;
  wizard_market_country?: string;
  wizard_market_country_code?: string;
  wizard_prompt_locales?: {
    country?: string;
    country_code?: string;
    language?: string;
    language_name?: string;
    key?: string;
    label?: string;
  }[];
  wizard_products?: {
    product_or_service: string;
    prompts: string[];
    prompt_tags?: Record<string, string[]>;
    custom_prompts?: string[];
    is_custom_topic?: boolean;
  }[];
  wizard_competitors?: {
    competitor_brand: string;
    competitor_website: string;
    included: boolean;
  }[];
  ga4_property_id?: string;
  ga4_ai_channels?: string;
  ga4_conversion_event_name?: string;
  crawl_urls?: string[];
  notification_email?: string;
  skip_prompt_probes?: boolean;
  /** Ignored by API when competitors are set — competitor crawl always follows. */
  follow_on_competitor_crawl?: boolean;
}

export function startAuditBackground(
  payload: RunAuditPayload,
): Promise<{ ok: boolean; audit_dir: string; status: string }> {
  return json("/audits/run-background", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function fetchAuditRunStatus(auditDirOrSlug: string): Promise<AuditRunStatusResponse> {
  const slug = auditSlug(auditDirOrSlug);
  return json<AuditRunStatusResponse>(`/audits/${encodeURIComponent(slug)}/run-status`);
}

export function rerunAllPrompts(
  auditDirOrSlug: string,
): Promise<{ status: string; audit_id: string }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/re-run`, { method: "POST" });
}

export async function runAuditStream(
  payload: RunAuditPayload,
  onLog: (line: string) => void,
  onDone: (auditDir: string, score?: number) => void,
  onError: (message: string) => void,
  onStarted?: (auditDir: string) => void,
  onProgress?: (progress: AuditRunProgressPayload) => void,
): Promise<void> {
  const res = await fetch(`${API}/audits/run`, {
    ...withCredentials,
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok || !res.body) {
    throw new Error(await res.text());
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data: ")) continue;
      try {
        const data = JSON.parse(line.slice(6)) as {
          type: string;
          line?: string;
          audit_dir?: string;
          overall_score?: number;
          message?: string;
          percent?: number;
          detail?: string;
          current_step?: string;
          steps?: AuditRunProgressStep[];
        };
        if (data.type === "started" && data.audit_dir) onStarted?.(data.audit_dir);
        if (data.type === "log" && data.line) onLog(data.line);
        if (data.type === "progress" && typeof data.percent === "number" && data.steps) {
          onProgress?.({
            percent: data.percent,
            detail: data.detail ?? "",
            current_step: data.current_step ?? "",
            steps: data.steps,
          });
        }
        if (data.type === "done" && data.audit_dir) onDone(data.audit_dir, data.overall_score);
        if (data.type === "error" && data.message) onError(data.message);
      } catch {
        /* ignore malformed chunks */
      }
    }
  }
}

/* ── AI Impact dashboard ─────────────────────────────────────────── */

export type AiImpactRange = { low: number; central: number; high: number };

export type AiImpactEstimate = {
  window_start: string;
  window_end: string;
  direct_ai_sessions: number;
  direct_ai_purchases: number;
  total_sessions: number;
  total_purchases: number;
  seo_sessions: number;
  seo_purchases: number;
  sessions_overall_net: AiImpactRange;
  sessions_overall_gross: AiImpactRange;
  sessions_seo_net: AiImpactRange;
  purchases_overall: AiImpactRange;
  purchases_seo: AiImpactRange;
  site_cvr: number;
  ai_cvr: number;
  seo_cvr: number;
  model_quality_score?: number;
  confidence_score?: number;
  p_value?: number | null;
  quality_narrative: string;
  method_notes?: string[];
  weekly_series?: AiImpactWeeklyPoint[];
};

export type AiImpactWeeklyPoint = {
  week: string;
  total_sessions: number;
  ai_sessions: number;
  seo_sessions: number;
  non_ai_sessions: number;
  indirect_ai_sessions: number;
  estimated_ai_sessions: number;
  counterfactual_sessions: number;
  gsc_clicks?: number;
  [key: string]: string | number | null | undefined;
};

export type AiImpactRunStatus = {
  run_id: string;
  status: string;
  created_at: string;
  ga4_property_id?: string | null;
  gsc_site_url?: string | null;
  conversion_event_name?: string;
  conversion_events?: Array<{ event: string; label: string }>;
  jobs: Record<string, string>;
  estimate?: AiImpactEstimate | null;
  error?: string | null;
  trends_upload?: AiImpactTrendsUpload | null;
  needs_ga4_reauth?: boolean;
  ga4_login_path?: string;
};

export type AiImpactTrendsUpload = {
  upload_id: string;
  filename: string;
  terms: string[];
  start_date: string;
  end_date: string;
  week_count: number;
  warnings: string[];
};

export type GscStatus = {
  configured: boolean;
  connected: boolean;
  site_url?: string | null;
  scopes?: string[];
  login_path?: string;
  redirect_uri?: string;
  error?: string | null;
};

export type GscSite = {
  site_url: string;
  permission_level: string;
};

export function fetchAiImpactConfig(): Promise<Record<string, unknown>> {
  return json("/ai-impact/config");
}

export function createAiImpactRun(body: {
  window_weeks?: number;
  trends_upload_id?: string | null;
  local_panel_path?: string | null;
  ga4_property_id?: string | null;
  ga4_property_name?: string | null;
  gsc_site_url?: string | null;
  conversion_event_name?: string;
  start_date?: string;
  end_date?: string | null;
}): Promise<AiImpactRunStatus> {
  return json("/ai-impact/runs", { method: "POST", body: JSON.stringify(body) });
}

export async function uploadAiImpactTrends(
  file: File,
  startDate: string,
  endDate: string,
): Promise<AiImpactTrendsUpload> {
  const form = new FormData();
  form.append("file", file);
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  const response = await fetch(`${API}/ai-impact/trends-upload?${params}`, {
    ...withCredentials,
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload?.detail;
    const message = Array.isArray(detail)
      ? detail.join(" ")
      : typeof detail === "string"
        ? detail
        : "The Google Trends CSV could not be validated.";
    throw new Error(message);
  }
  return response.json() as Promise<AiImpactTrendsUpload>;
}

export function fetchAiImpactRun(runId: string): Promise<AiImpactRunStatus> {
  return json(`/ai-impact/runs/${encodeURIComponent(runId)}`);
}

/** Persist estimate on the audit so PDF/HTML exports can include the chart. */
export function saveAiImpactEstimateForAudit(
  auditDirOrSlug: string,
  estimate: AiImpactEstimate,
  runId?: string,
): Promise<{ ok: boolean }> {
  return json(`/audits/${encodeURIComponent(auditDirOrSlug)}/ai-impact-estimate`, {
    method: "PUT",
    body: JSON.stringify({ estimate, run_id: runId ?? null }),
  });
}

export function fetchGscStatus(): Promise<GscStatus> {
  return json("/gsc/status");
}

export function gscLoginUrl(returnTo?: string | null): string {
  const params = new URLSearchParams();
  if (returnTo && returnTo.startsWith("/")) {
    params.set("return_to", returnTo);
  }
  const query = params.toString();
  return `${API}/gsc/login${query ? `?${query}` : ""}`;
}

export function fetchGscSites(): Promise<{ sites: GscSite[] }> {
  return json("/gsc/sites");
}

export function saveGscSelection(siteUrl: string): Promise<{ site_url: string }> {
  return json("/gsc/selection", {
    method: "PUT",
    body: JSON.stringify({ site_url: siteUrl }),
  });
}

export function disconnectGsc(): Promise<{ ok: boolean }> {
  return json("/gsc/disconnect", { method: "POST" });
}
