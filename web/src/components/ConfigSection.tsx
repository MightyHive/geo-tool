import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import type { CompetitorCrawlStatusResponse, OnboardingContext, ProductServiceRow } from "../types";
import {
  fetchAuditRunStatus,
  rerunAllPrompts,
  startAuditBackground,
  updatePromptLocales,
} from "../api/client";
import { auditSlug } from "../lib/auditPath";
import {
  DEFAULT_PROBE_PLATFORM_COUNT,
  DEFAULT_PROBE_RUNS,
  derivePlatformCallsPerMarket,
  estimateProbeRunMinutes,
} from "../lib/probeRunEta";
import { normalizePromptLocales, type PromptLocale } from "../lib/promptLocales";
import { CompetitorCrawlControls } from "./CompetitorCrawlControls";
import { PromptLocaleEditor } from "./PromptLocaleEditor";

interface ConfigSectionProps {
  config: OnboardingContext | null | undefined;
  auditId: string;
  competitorCrawl: {
    status: CompetitorCrawlStatusResponse;
    busy: boolean;
    error: string | null;
    onStart: () => void;
  };
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (!value) return null;
  return (
    <div className="grid grid-cols-[160px_1fr] gap-4 py-3 border-b border-gray-100 last:border-0">
      <dt className="text-xs font-semibold uppercase tracking-wide text-gray-400 pt-0.5">
        {label}
      </dt>
      <dd className="text-sm text-[#0d0d0d] leading-relaxed">{value}</dd>
    </div>
  );
}

export function ConfigSection({ config, auditId, competitorCrawl }: ConfigSectionProps) {
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [promptRerunState, setPromptRerunState] = useState<"idle" | "queueing" | "queued">("idle");
  const [rerunError, setRerunError] = useState<string | null>(null);
  const [brandName, setBrandName] = useState("");
  const [brandWebsite, setBrandWebsite] = useState("");
  const [industry, setIndustry] = useState("");
  const [marketCountry, setMarketCountry] = useState("");
  const [marketCountryCode, setMarketCountryCode] = useState("");
  const [promptLocales, setPromptLocales] = useState<PromptLocale[]>([]);
  const [editableProducts, setEditableProducts] = useState<ProductServiceRow[]>([]);
  const [competitorRows, setCompetitorRows] = useState<Array<{ competitor_brand: string; competitor_website: string }>>([]);
  const [pageUrls, setPageUrls] = useState<string[]>([]);
  const [fullRunState, setFullRunState] = useState<"idle" | "starting" | "running">("idle");
  const [fullRunDetail, setFullRunDetail] = useState("");
  const [activeAuditId, setActiveAuditId] = useState<string | null>(null);

  useEffect(() => {
    if (!config || editing) return;
    const rows = config.products_and_services_rows?.length
      ? config.products_and_services_rows
      : (config.products_and_services ?? []).map((product) => ({
          product_or_service: product,
          prompts: [],
        }));
    setBrandName(config.brand_name_used ?? "");
    setBrandWebsite(config.brand_website_used ?? "");
    setIndustry(config.industry_used ?? "");
    setMarketCountry(config.geo_market_country ?? "");
    setMarketCountryCode(config.geo_market_country_code ?? "");
    setPromptLocales(
      normalizePromptLocales(
        config.prompt_locales as PromptLocale[] | undefined,
        config.geo_market_country ?? "",
        config.geo_market_country_code ?? "",
      ),
    );
    setEditableProducts(rows);
    setCompetitorRows(config.competitors_detail ?? []);
    setPageUrls(config.crawl_urls ?? []);
  }, [config, editing]);

  useEffect(() => {
    if (!activeAuditId) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await fetchAuditRunStatus(activeAuditId);
        if (cancelled) return;
        setFullRunDetail(status.detail ?? "Audit running…");
        if (status.status === "done" && !status.still_running) {
          setActiveAuditId(null);
          setFullRunState("idle");
          navigate(`/report/${auditSlug(status.audit_dir ?? activeAuditId)}/config`);
        } else if (status.status === "error") {
          setActiveAuditId(null);
          setFullRunState("idle");
          setRerunError(status.error || status.detail || "Audit failed");
        }
      } catch {
        if (!cancelled) setFullRunDetail("Waiting for audit progress…");
      }
    };
    void poll();
    const timer = window.setInterval(poll, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeAuditId, navigate]);

  if (!config) {
    return (
      <div className="max-w-2xl mx-auto py-12 text-center text-gray-400 text-sm">
        No configuration data found for this audit.
      </div>
    );
  }

  const products = config.products_and_services ?? [];
  const competitors = config.competitors_detail ?? [];
  const crawlUrls = config.crawl_urls ?? [];
  const productRows = config.products_and_services_rows?.length
    ? config.products_and_services_rows
    : products.map((product) => ({ product_or_service: product, prompts: [] }));
  const promptCount = productRows.reduce((sum, row) => sum + (row.prompts?.length ?? 0), 0);
  // Markets run in parallel → ETA from per-market platform calls only.
  const promptPlatformCalls = derivePlatformCallsPerMarket({
    promptCount,
    platformCount: DEFAULT_PROBE_PLATFORM_COUNT,
    runsPerPrompt: DEFAULT_PROBE_RUNS,
  });
  const promptEstimateMins = estimateProbeRunMinutes(promptPlatformCalls);
  const promptEstimate =
    promptEstimateMins > 0
      ? `~${promptEstimateMins} minute${promptEstimateMins === 1 ? "" : "s"}`
      : "a few minutes";
  // Site crawl / config re-run skips prompt probes → flat ETA (upper end of timed runs).
  const crawlEstimate = "About 10 minutes";

  const saveAndRerunAudit = async () => {
    if (!brandName.trim() || !brandWebsite.trim()) {
      setRerunError("Brand name and website are required.");
      return;
    }
    const cleanedCompetitors = competitorRows
      .filter((row) => row.competitor_website.trim())
      .slice(0, 10);
    const wizardProducts = editableProducts
      .map((row) => ({ ...row, product_or_service: row.product_or_service.trim() }))
      .filter((row) => row.product_or_service);
    setFullRunState("starting");
    setRerunError(null);
    setFullRunDetail("Starting updated audit…");
    try {
      const result = await startAuditBackground({
        brand_name: brandName.trim(),
        brand_website: brandWebsite.trim(),
        industry: industry.trim(),
        competitors: cleanedCompetitors.map((row) => row.competitor_website),
        wizard_market_country: marketCountry.trim(),
        wizard_market_country_code: marketCountryCode.trim(),
        wizard_prompt_locales: promptLocales,
        wizard_products: wizardProducts,
        wizard_competitors: cleanedCompetitors.map((row) => ({
          ...row,
          included: true,
        })),
        ...(config.ga4_property_id ? { ga4_property_id: config.ga4_property_id } : {}),
        crawl_urls: pageUrls.map((url) => url.trim()).filter(Boolean),
        skip_prompt_probes: true,
        ...(config.model_category ? { model_category: config.model_category } : {}),
      });
      setFullRunState("running");
      setActiveAuditId(result.audit_dir);
      setEditing(false);
    } catch (error) {
      setFullRunState("idle");
      setRerunError(error instanceof Error ? error.message : "Could not start updated audit");
    }
  };

  const rerunPrompts = async () => {
    setPromptRerunState("queueing");
    setRerunError(null);
    try {
      await updatePromptLocales(auditId, promptLocales);
      await rerunAllPrompts(auditId);
      setPromptRerunState("queued");
    } catch (error) {
      setPromptRerunState("idle");
      setRerunError(error instanceof Error ? error.message : "Could not queue prompt re-run");
    }
  };

  return (
    <div className="max-w-2xl mx-auto">
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-[#0d0d0d] mb-1">Audit configuration</h2>
        <p className="text-sm text-gray-500">
          Settings used when this audit was run.
        </p>
      </div>

      <section className="mb-6 overflow-hidden rounded-xl border border-gray-200 bg-white">
        <div className="border-b border-gray-100 bg-gray-50 px-6 py-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Re-run audit</h3>
        </div>
        <div className="divide-y divide-gray-100">
          <div className="flex flex-col gap-4 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-[#0d0d0d]">Edit configuration and re-run</p>
              <p className="mt-1 text-xs leading-relaxed text-gray-500">
                Edit the brand, products and services, competitors, and crawled pages on this page.
              </p>
              <p className="mt-1 text-[11px] font-medium text-gray-400">Estimated time: {crawlEstimate}</p>
            </div>
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg bg-[#0d0d0d] px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-gray-800 focus:outline-none focus:ring-2 focus:ring-gray-400 focus:ring-offset-2"
            >
              <Pencil className="h-4 w-4" />
              Edit config
            </button>
          </div>
          <div className="flex flex-col gap-4 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-[#0d0d0d]">Re-run all prompts</p>
              <p className="mt-1 text-xs leading-relaxed text-gray-500">
                Keep this configuration and run every prompt three times on all available AI platforms.
              </p>
              <p className="mt-1 text-[11px] font-medium text-gray-400">Estimated time: {promptEstimate}</p>
            </div>
            <button
              type="button"
              onClick={rerunPrompts}
              disabled={promptRerunState !== "idle"}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-violet-300 focus:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${promptRerunState === "queueing" ? "animate-spin" : ""}`} />
              {promptRerunState === "queueing"
                ? "Queueing…"
                : promptRerunState === "queued"
                  ? "Prompts queued"
                  : "Re-run prompts"}
            </button>
          </div>
          <div className="flex flex-col gap-4 px-6 py-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-[#0d0d0d]">Edit &amp; re-run prompts</p>
              <p className="mt-1 text-xs leading-relaxed text-gray-500">
                Open the Prompts page with the add-prompt form ready to edit, categorise and run.
              </p>
            </div>
            <button
              type="button"
              onClick={() => navigate(`/report/${auditSlug(auditId)}/prompt_performance?addPrompt=1`)}
              className="inline-flex shrink-0 items-center justify-center gap-2 rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-violet-300 focus:ring-offset-2"
            >
              <Pencil className="h-4 w-4" />
              Edit prompts
            </button>
          </div>
          <CompetitorCrawlControls
            competitorCount={
              editing
                ? competitorRows.filter((row) => row.competitor_website.trim()).length
                : competitors.filter((row) => row.competitor_website?.trim()).length
            }
            status={competitorCrawl.status}
            busy={competitorCrawl.busy}
            error={competitorCrawl.error}
            onStart={competitorCrawl.onStart}
          />
        </div>
        {rerunError && <p className="border-t border-red-100 bg-red-50 px-6 py-3 text-xs text-red-700">{rerunError}</p>}
        {promptRerunState === "queued" && (
          <p className="border-t border-emerald-100 bg-emerald-50 px-6 py-3 text-xs text-emerald-700">
            Prompt re-run queued. Updated visibility and citation history will appear when it completes.
          </p>
        )}
        {fullRunState !== "idle" && (
          <p className="border-t border-blue-100 bg-blue-50 px-6 py-3 text-xs text-blue-700">
            <RefreshCw className="mr-2 inline h-3.5 w-3.5 animate-spin" />
            {fullRunDetail || "Updated audit running…"}
          </p>
        )}
      </section>

      {editing && (
        <section className="mb-6 overflow-hidden rounded-xl border border-gray-200 bg-white">
          <div className="border-b border-gray-100 bg-gray-50 px-6 py-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Edit audit configuration</h3>
          </div>
          <div className="space-y-7 p-6">
            <fieldset className="space-y-3">
              <legend className="text-xs font-bold uppercase tracking-wide text-gray-500">Brand</legend>
              <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-medium text-gray-600">
                  Brand name
                  <input value={brandName} onChange={(event) => setBrandName(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                </label>
                <label className="text-xs font-medium text-gray-600">
                  Website
                  <input value={brandWebsite} onChange={(event) => setBrandWebsite(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                </label>
                <label className="text-xs font-medium text-gray-600">
                  Industry
                  <input value={industry} onChange={(event) => setIndustry(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                </label>
                <div className="grid grid-cols-[1fr_90px] gap-2">
                  <label className="text-xs font-medium text-gray-600">
                    Market
                    <input value={marketCountry} onChange={(event) => setMarketCountry(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                  </label>
                  <label className="text-xs font-medium text-gray-600">
                    Code
                    <input value={marketCountryCode} onChange={(event) => setMarketCountryCode(event.target.value)} className="mt-1 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm uppercase text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                  </label>
                </div>
              </div>
            </fieldset>

            <fieldset>
              <PromptLocaleEditor
                compact
                marketCountry={marketCountry}
                marketCountryCode={marketCountryCode}
                locales={promptLocales}
                onChange={setPromptLocales}
              />
            </fieldset>

            <fieldset>
              <div className="mb-2 flex items-center justify-between">
                <legend className="text-xs font-bold uppercase tracking-wide text-gray-500">Products &amp; services</legend>
                <button type="button" onClick={() => setEditableProducts((rows) => [...rows, { product_or_service: "", prompts: [] }])} className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-700">
                  <Plus className="h-3.5 w-3.5" /> Add
                </button>
              </div>
              <div className="space-y-2">
                {editableProducts.map((row, index) => (
                  <div key={index} className="flex gap-2">
                    <input
                      value={row.product_or_service}
                      onChange={(event) => setEditableProducts((rows) => rows.map((item, itemIndex) => itemIndex === index ? { ...item, product_or_service: event.target.value } : item))}
                      className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100"
                    />
                    <button type="button" aria-label={`Remove ${row.product_or_service || "product"}`} onClick={() => setEditableProducts((rows) => rows.filter((_, itemIndex) => itemIndex !== index))} className="rounded-lg p-2 text-gray-400 hover:bg-red-50 hover:text-red-600">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <div className="mb-2 flex items-center justify-between">
                <legend className="text-xs font-bold uppercase tracking-wide text-gray-500">Competitors</legend>
                <button type="button" disabled={competitorRows.length >= 10} onClick={() => setCompetitorRows((rows) => [...rows, { competitor_brand: "", competitor_website: "" }])} className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-700 disabled:text-gray-300">
                  <Plus className="h-3.5 w-3.5" /> Add
                </button>
              </div>
              <div className="space-y-2">
                {competitorRows.map((row, index) => (
                  <div key={index} className="grid grid-cols-[minmax(0,0.7fr)_minmax(0,1fr)_auto] gap-2">
                    <input aria-label="Competitor brand" placeholder="Brand" value={row.competitor_brand} onChange={(event) => setCompetitorRows((rows) => rows.map((item, itemIndex) => itemIndex === index ? { ...item, competitor_brand: event.target.value } : item))} className="min-w-0 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                    <input aria-label="Competitor website" placeholder="https://…" value={row.competitor_website} onChange={(event) => setCompetitorRows((rows) => rows.map((item, itemIndex) => itemIndex === index ? { ...item, competitor_website: event.target.value } : item))} className="min-w-0 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-800 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                    <button type="button" aria-label={`Remove ${row.competitor_brand || "competitor"}`} onClick={() => setCompetitorRows((rows) => rows.filter((_, itemIndex) => itemIndex !== index))} className="rounded-lg p-2 text-gray-400 hover:bg-red-50 hover:text-red-600">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <div className="mb-2 flex items-center justify-between">
                <legend className="text-xs font-bold uppercase tracking-wide text-gray-500">Crawled pages</legend>
                <button type="button" onClick={() => setPageUrls((urls) => [...urls, ""])} className="inline-flex items-center gap-1 text-xs font-semibold text-blue-600 hover:text-blue-700">
                  <Plus className="h-3.5 w-3.5" /> Add
                </button>
              </div>
              <div className="max-h-72 space-y-2 overflow-y-auto pr-1">
                {pageUrls.map((url, index) => (
                  <div key={index} className="flex gap-2">
                    <input value={url} onChange={(event) => setPageUrls((urls) => urls.map((item, itemIndex) => itemIndex === index ? event.target.value : item))} className="min-w-0 flex-1 rounded-lg border border-gray-300 px-3 py-2 text-xs text-gray-700 focus:border-violet-500 focus:outline-none focus:ring-2 focus:ring-violet-100" />
                    <button type="button" aria-label="Remove crawled page" onClick={() => setPageUrls((urls) => urls.filter((_, itemIndex) => itemIndex !== index))} className="rounded-lg p-2 text-gray-400 hover:bg-red-50 hover:text-red-600">
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                ))}
              </div>
            </fieldset>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 pt-5">
              <p className="text-[11px] font-medium text-gray-400">Estimated re-run time: {crawlEstimate}</p>
              <div className="flex gap-2">
                <button type="button" onClick={() => setEditing(false)} className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-semibold text-gray-600 hover:bg-gray-50">Cancel</button>
                <button type="button" disabled={fullRunState !== "idle"} onClick={saveAndRerunAudit} className="inline-flex items-center gap-2 rounded-lg bg-[#0d0d0d] px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:opacity-50">
                  <RefreshCw className={`h-4 w-4 ${fullRunState !== "idle" ? "animate-spin" : ""}`} />
                  Save &amp; re-run audit
                </button>
              </div>
            </div>
          </div>
        </section>
      )}

      {!editing && (
        <>
      <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100 overflow-hidden mb-6">
        <div className="px-6 py-3 bg-gray-50">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Brand</h3>
        </div>
        <dl className="px-6">
          <Row label="Brand name" value={config.brand_name_used} />
          <Row
            label="Website"
            value={
              config.brand_website_used ? (
                <a
                  href={config.brand_website_used}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-brand-accent hover:underline break-all"
                >
                  {config.brand_website_used}
                </a>
              ) : null
            }
          />
          <Row label="Industry" value={config.industry_used} />
          <Row
            label="Market"
            value={
              config.geo_market_country
                ? `${config.geo_market_country}${config.geo_market_country_code ? ` (${config.geo_market_country_code})` : ""}`
                : null
            }
          />
          <Row
            label="Prompt locales"
            value={
              normalizePromptLocales(
                (config.prompt_locales as PromptLocale[] | undefined) ?? promptLocales,
                config.geo_market_country ?? "",
                config.geo_market_country_code ?? "",
              )
                .map((l) => l.label)
                .join(" · ") || null
            }
          />
          {config.ga4_property_id && (
            <Row label="GA4 property" value={config.ga4_property_id} />
          )}
        </dl>
      </div>

      {products.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mb-6">
          <div className="px-6 py-3 bg-gray-50">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Products &amp; services ({products.length})
            </h3>
          </div>
          <ul className="divide-y divide-gray-100">
            {products.map((p, i) => (
              <li key={i} className="px-6 py-3 text-sm text-[#0d0d0d]">
                {p}
              </li>
            ))}
          </ul>
        </div>
      )}

      {competitors.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mb-6">
          <div className="px-6 py-3 bg-gray-50">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Competitors ({competitors.length})
            </h3>
          </div>
          <ul className="divide-y divide-gray-100">
            {competitors.map((c, i) => (
              <li key={i} className="px-6 py-3 flex items-center gap-3">
                <img
                  src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(c.competitor_website)}&sz=32`}
                  alt=""
                  width={20}
                  height={20}
                  className="rounded shrink-0"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = "none";
                  }}
                />
                <span className="text-sm font-medium text-[#0d0d0d] min-w-[8rem]">
                  {c.competitor_brand || "—"}
                </span>
                <a
                  href={c.competitor_website}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-gray-400 hover:text-brand-accent hover:underline break-all"
                >
                  {c.competitor_website}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {crawlUrls.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mb-6">
          <div className="px-6 py-3 bg-gray-50">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Crawled pages ({crawlUrls.length})
            </h3>
          </div>
          <ul className="divide-y divide-gray-100 max-h-64 overflow-y-auto">
            {crawlUrls.map((u, i) => (
              <li key={i} className="px-6 py-2">
                <a
                  href={u}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-gray-500 hover:text-brand-accent hover:underline break-all"
                >
                  {u}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
        </>
      )}
    </div>
  );
}
