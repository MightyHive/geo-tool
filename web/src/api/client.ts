import { auditSlug } from "../lib/auditPath";
import type {
  AppConfig,
  ArchiveResponse,
  AuditDetail,
  AuditRunProgressPayload,
  AuditRunProgressStep,
  AuditRunStatusResponse,
  AuthStatus,
  DomainOption,
  Ga4Status,
  LocalAudit,
  CompetitorDetail,
  LiveProbeResult,
  ProductServiceRow,
  PromptPerformanceContext,
  PromptSentimentResponse,
  VerifiedSite,
  ProbeSiteProtection,
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

export function fetchLocalAudits(): Promise<LocalAudit[]> {
  return json<LocalAudit[]>("/audits/local");
}

/** ``auditDirOrSlug`` — full ``audit_output/...`` path or folder id under ``audit_output/``. */
export function fetchAudit(auditDirOrSlug: string): Promise<AuditDetail> {
  return json<AuditDetail>(`/audits/${encodeURIComponent(auditSlug(auditDirOrSlug))}`);
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

export function ga4LoginUrl(wizardStep = 2, afterYes = true): string {
  const params = new URLSearchParams({
    wizard_step: String(wizardStep),
    after_yes: afterYes ? "1" : "0",
  });
  return `${API}/ga4/login?${params}`;
}

export function saveGa4Selection(
  propertyId: string,
  aiChannelNames: string,
  accountId = "",
): Promise<{ ok: boolean }> {
  return json("/ga4/selection", {
    method: "PUT",
    body: JSON.stringify({
      property_id: propertyId,
      account_id: accountId,
      ai_channel_names: aiChannelNames,
    }),
  });
}

export function disconnectGa4(): Promise<{ ok: boolean }> {
  return json("/ga4/disconnect", { method: "POST" });
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

export function fetchPromptPerformanceContext(
  auditDirOrSlug: string,
): Promise<PromptPerformanceContext> {
  const slug = auditSlug(auditDirOrSlug);
  return json<PromptPerformanceContext>(`/audits/${encodeURIComponent(slug)}/prompt-performance`);
}

export function fetchPromptSentiment(auditDirOrSlug: string): Promise<PromptSentimentResponse> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/sentiment`);
}

export function runPromptPerformanceProbes(
  auditDirOrSlug: string,
  reportMode = true,
): Promise<{ live_probe: LiveProbeResult; highlight: PromptPerformanceContext["highlight"] }> {
  const slug = auditSlug(auditDirOrSlug);
  return json(`/audits/${encodeURIComponent(slug)}/prompt-performance/run-probes`, {
    method: "POST",
    body: JSON.stringify({ report_mode: reportMode }),
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

export function reportHtmlUrl(
  auditDirOrSlug: string,
  section?: string,
  embed = false,
): string {
  const slug = auditSlug(auditDirOrSlug);
  const params = embed ? "?embed=1" : "";
  const base = `${API}/audits/${encodeURIComponent(slug)}/report.html${params}`;
  if (!section || section === "prompt_performance") return base;
  return `${base}#${section}`;
}

export function reportPdfUrl(auditDirOrSlug: string): string {
  const slug = auditSlug(auditDirOrSlug);
  return `${API}/audits/${encodeURIComponent(slug)}/report.pdf`;
}

export function reportAllPagesHtmlUrl(auditDirOrSlug: string): string {
  const slug = auditSlug(auditDirOrSlug);
  return `${API}/audits/${encodeURIComponent(slug)}/report-all-pages.html`;
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
  wizard_products?: { product_or_service: string; prompts: string[] }[];
  wizard_competitors?: {
    competitor_brand: string;
    competitor_website: string;
    included: boolean;
  }[];
  ga4_property_id?: string;
  ga4_ai_channels?: string;
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
