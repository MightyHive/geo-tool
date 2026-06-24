import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useAuth } from "../auth/AuthContext";
import {
  fetchAuditRunStatus,
  fetchConfig,
  fetchGa4Status,
  fetchIndustries,
  startAuditBackground,
  probeSiteProtection,
  verifyBrandSite,
} from "../api/client";
import { AuditRunProgress } from "../components/AuditRunProgress";
import { BrandAuditPreviewRow } from "../components/BrandAuditPreviewRow";
import { auditSlug } from "../lib/auditPath";
import { CountryCombobox } from "../components/CountryCombobox";
import { PageHeader } from "../components/PageHeader";
import { StepIndicator } from "../components/StepIndicator";
import {
  UrlAutocomplete,
  type UrlAutocompleteHandle,
} from "../components/UrlAutocomplete";
import { WizardGa4Step } from "../components/WizardGa4Step";
import { WizardCompetitorsStep } from "../components/WizardCompetitorsStep";
import { WizardGeneratePromptsStep } from "../components/WizardGeneratePromptsStep";
import { WizardPromptsStep } from "../components/WizardPromptsStep";
import { WizardProductsStep } from "../components/WizardProductsStep";
import { isCustomPromptsCategory } from "../lib/customPrompts";
import {
  clearAuditRunDraft,
  clearWizardDraft,
  consumeWizardFreshIntent,
  loadAuditRunDraft,
  loadWizardDraft,
  saveAuditRunDraft,
  saveWizardDraft,
  type SitePreviewPhase,
  type WizardDraft,
} from "../lib/wizardDraft";
import type {
  AuditRunProgressPayload,
  AuditRunStatusResponse,
  CompetitorDetail,
  ProductServiceRow,
  VerifiedSite,
} from "../types";

const MAX_WIZARD_STEP = 7;

function parseApiDetail(raw: string): string {
  try {
    const data = JSON.parse(raw) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    /* use raw */
  }
  return raw || "Request failed";
}

const WIZARD_STEPS = [
  { id: 1, label: "Brand" },
  { id: 2, label: "GA4" },
  { id: 3, label: "Products" },
  { id: 4, label: "Competitors" },
  { id: 5, label: "Generate prompts" },
  { id: 6, label: "Review prompts" },
  { id: 7, label: "Run" },
];

function runStatusToProgress(status: AuditRunStatusResponse): AuditRunProgressPayload | null {
  if (!status.steps?.length && status.percent == null) return null;
  return {
    percent: status.percent ?? 0,
    detail: status.detail ?? "",
    current_step: status.current_step ?? "",
    steps: status.steps ?? [],
  };
}

const INDUSTRY_PLACEHOLDER = "— Select an industry —";

export function NewAuditPage() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { loggedIn, user } = useAuth();

  const stepFromUrl = Number(searchParams.get("step") || "1");
  const [step, setStep] = useState(
    stepFromUrl >= 1 && stepFromUrl <= MAX_WIZARD_STEP ? stepFromUrl : 1,
  );
  const [activeAuditDir, setActiveAuditDir] = useState<string | null>(null);

  const [industries, setIndustries] = useState<string[]>([]);
  const [brandName, setBrandName] = useState("");
  const [brandWebsite, setBrandWebsite] = useState("");
  const [industry, setIndustry] = useState(INDUSTRY_PLACEHOLDER);
  const [marketCountry, setMarketCountry] = useState("");
  const [marketCountryCode, setMarketCountryCode] = useState("");
  const [productRows, setProductRows] = useState<ProductServiceRow[]>([]);
  const [selectedProducts, setSelectedProducts] = useState<string[]>([]);
  const [competitorDetails, setCompetitorDetails] = useState<CompetitorDetail[]>([]);
  const [ga4PropertyId, setGa4PropertyId] = useState("");
  const [ga4AiChannels, setGa4AiChannels] = useState("");
  const [sitePreviewPhase, setSitePreviewPhase] = useState<SitePreviewPhase>("form");
  const [siteVerifyMessage, setSiteVerifyMessage] = useState("Checking site…");
  const [verifiedSite, setVerifiedSite] = useState<VerifiedSite | null>(null);
  const previewUnlocked = sitePreviewPhase !== "form";
  const [running, setRunning] = useState(false);
  const [auditProgress, setAuditProgress] = useState<AuditRunProgressPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const brandWebsiteRef = useRef<UrlAutocompleteHandle>(null);
  const skipPersistRef = useRef(false);
  const wizardInitRef = useRef<string | null>(null);

  const hydrateFromDraft = useCallback((draft: Partial<WizardDraft>) => {
    if (draft.brandName) setBrandName(draft.brandName);
    if (draft.brandWebsite) setBrandWebsite(draft.brandWebsite);
    if (draft.industry) setIndustry(draft.industry);
    if (draft.marketCountry) setMarketCountry(draft.marketCountry);
    if (draft.marketCountryCode) setMarketCountryCode(draft.marketCountryCode);
    if (draft.productRows?.length) setProductRows(draft.productRows);
    if (draft.selectedProducts?.length) setSelectedProducts(draft.selectedProducts);
    if (draft.competitorDetails?.length) setCompetitorDetails(draft.competitorDetails);
    if (draft.ga4PropertyId) setGa4PropertyId(draft.ga4PropertyId);
    if (draft.ga4AiChannels) setGa4AiChannels(draft.ga4AiChannels);
    if (draft.verifiedSite) setVerifiedSite(draft.verifiedSite);
    if (draft.sitePreviewPhase) setSitePreviewPhase(draft.sitePreviewPhase);
    if (
      draft.wizardStep &&
      draft.wizardStep >= 1 &&
      draft.wizardStep <= MAX_WIZARD_STEP
    ) {
      setStep(draft.wizardStep);
    }
  }, []);

  const resetWizardState = useCallback(() => {
    skipPersistRef.current = true;
    clearWizardDraft();
    clearAuditRunDraft();
    setBrandName("");
    setBrandWebsite("");
    setIndustry(INDUSTRY_PLACEHOLDER);
    setMarketCountry("");
    setMarketCountryCode("");
    setProductRows([]);
    setSelectedProducts([]);
    setCompetitorDetails([]);
    setGa4PropertyId("");
    setGa4AiChannels("");
    setSitePreviewPhase("form");
    setVerifiedSite(null);
    setActiveAuditDir(null);
    setRunning(false);
    setAuditProgress(null);
    setError(null);
    setStep(1);
    window.setTimeout(() => {
      skipPersistRef.current = false;
    }, 0);
  }, []);

  useEffect(() => {
    const freshParam = searchParams.get("fresh") === "1";
    const freshIntent = consumeWizardFreshIntent();
    const fresh = freshParam || freshIntent;
    const resume = searchParams.get("resume") === "1";
    const ga4Return =
      searchParams.has("ga4_connected") || searchParams.has("ga4_error");
    const runDraft = loadAuditRunDraft();

    const initToken = [
      fresh ? "fresh" : "",
      resume ? "resume" : "",
      ga4Return ? "ga4" : "",
      runDraft?.auditDir ?? "",
    ].join("|");
    if (wizardInitRef.current === initToken) return;
    wizardInitRef.current = initToken;

    if (runDraft && !fresh) {
      setActiveAuditDir(runDraft.auditDir);
      setRunning(true);
      if (runDraft.brandName) setBrandName(runDraft.brandName);
      if (runDraft.brandWebsite) setBrandWebsite(runDraft.brandWebsite);
      const urlStep = Number(searchParams.get("step") || "0");
      if (urlStep < 1 || urlStep > MAX_WIZARD_STEP) {
        setStep(MAX_WIZARD_STEP);
        const params = new URLSearchParams(searchParams);
        params.set("step", String(MAX_WIZARD_STEP));
        setSearchParams(params, { replace: true });
      }
      const draft = loadWizardDraft();
      if (draft) hydrateFromDraft(draft);
      return;
    }

    if (fresh) {
      resetWizardState();
      const params = new URLSearchParams(searchParams);
      params.delete("fresh");
      params.set("step", "1");
      setSearchParams(params, { replace: true });
      return;
    }

    if (resume || ga4Return) {
      const draft = loadWizardDraft();
      if (draft) hydrateFromDraft(draft);
      const urlStep = Number(searchParams.get("step") || "0");
      if (urlStep >= 1 && urlStep <= MAX_WIZARD_STEP) setStep(urlStep);

      // WizardGa4Step strips ga4_connected from the URL via window.history.replaceState.
      // React Router v7 intercepts replaceState and updates searchParams, which would
      // cause this effect to re-run without ga4Return=true and call resetWizardState().
      // Pre-mark the post-cleanup initToken so that re-run is treated as already handled.
      if (ga4Return) {
        const postCleanupToken = [
          fresh ? "fresh" : "",
          resume ? "resume" : "",
          "", // ga4Return will be false once ga4_connected is stripped
          runDraft?.auditDir ?? "",
        ].join("|");
        wizardInitRef.current = postCleanupToken;
      }
      return;
    }

    // Default: new audit setup — do not autofill from a previous wizard session
    resetWizardState();
    const params = new URLSearchParams(searchParams);
    if (params.get("step") !== "1") {
      params.set("step", "1");
      setSearchParams(params, { replace: true });
    }
  }, [
    searchParams,
    setSearchParams,
    resetWizardState,
    hydrateFromDraft,
  ]);

  useEffect(() => {
    fetchIndustries().then(setIndustries).catch(() => setIndustries([]));
    fetchConfig().catch(() => undefined);
  }, []);

  useEffect(() => {
    const s = Number(searchParams.get("step") || "0");
    if (s >= 1 && s <= MAX_WIZARD_STEP) setStep(s);
  }, [searchParams]);

  useEffect(() => {
    if (!running || !activeAuditDir) return;
    const auditDir = activeAuditDir;

    let cancelled = false;
    // Allow several transient 404s before surfacing an error — GCS FUSE write-back
    // caching can delay the status file being visible across Cloud Run instances.
    let consecutiveFailures = 0;
    const MAX_CONSECUTIVE_FAILURES = 6;

    async function poll() {
      try {
        const status = await fetchAuditRunStatus(auditDir);
        if (cancelled) return;
        consecutiveFailures = 0;
        const progress = runStatusToProgress(status);
        if (progress) setAuditProgress(progress);
        if (status.status === "done" && status.audit_dir) {
          clearAuditRunDraft();
          setRunning(false);
          setActiveAuditDir(null);
          navigate(`/report/${auditSlug(status.audit_dir)}/summary`, { replace: true });
        } else if (status.status === "error") {
          setError(status.error || status.detail || "Audit failed");
          clearAuditRunDraft();
          setRunning(false);
          setActiveAuditDir(null);
        }
      } catch (e) {
        if (!cancelled) {
          consecutiveFailures += 1;
          if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
            setError(e instanceof Error ? e.message : "Could not check audit status");
          }
          // else: swallow transient failure and retry on the next interval
        }
      }
    }

    void poll();
    const id = window.setInterval(poll, 2500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [running, activeAuditDir, navigate]);

  useEffect(() => {
    if (step < 2) return;
    syncBrandWebsite();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sync when entering later steps
  }, [step]);

  useEffect(() => {
    if (skipPersistRef.current) return;
    const websiteForDraft =
      verifiedSite?.canonical_url?.trim() || brandWebsite.trim() || resolveBrandWebsite();
    saveWizardDraft({
      brandName,
      brandWebsite: websiteForDraft,
      industry,
      marketCountry,
      marketCountryCode,
      previewUnlocked,
      sitePreviewPhase,
      verifiedSite,
      productRows,
      selectedProducts,
      competitorDetails,
      ga4PropertyId,
      ga4AiChannels,
      wizardStep: step,
      promptsReady: step > 5,
    });
  }, [
    brandName,
    brandWebsite,
    industry,
    marketCountry,
    marketCountryCode,
    previewUnlocked,
    sitePreviewPhase,
    verifiedSite,
    productRows,
    selectedProducts,
    competitorDetails,
    ga4PropertyId,
    ga4AiChannels,
    step,
  ]);

  function selectedProductNames(): string[] {
    const names = selectedProducts.length
      ? selectedProducts
      : productRows.map((r) => r.product_or_service.trim()).filter(Boolean);
    return names.filter((n) => !isCustomPromptsCategory(n));
  }

  function includedCompetitorUrls(): string[] {
    return competitorDetails
      .filter((r) => r.included && r.competitor_website.trim())
      .map((r) => r.competitor_website.trim());
  }

  function totalSelectedPrompts(): number {
    return productRows.reduce(
      (n, r) => n + r.prompts.filter((p) => String(p).trim()).length,
      0,
    );
  }

  /** Prefer verified URL, then state, autocomplete ref, then session draft. */
  function resolveBrandWebsite(): string {
    const verified = verifiedSite?.canonical_url?.trim();
    if (verified) return verified;
    const fromState = brandWebsite.trim();
    const fromRef = (brandWebsiteRef.current?.getQuery() ?? "").trim();
    let merged = fromState;
    if (fromRef) {
      if (!merged) merged = fromRef;
      else if (fromRef.includes(".") || /^https?:\/\//i.test(fromRef)) merged = fromRef;
      else if (merged.length < fromRef.length) merged = fromRef;
    }
    return merged;
  }

  function syncBrandWebsite(): string {
    const website = resolveBrandWebsite();
    if (website && website !== brandWebsite) {
      setBrandWebsite(website);
    }
    return website;
  }

  function goToStep(next: number) {
    if (next > 1) {
      const website = syncBrandWebsite();
      if (!website) {
        setError(
          "Complete step 1 first: enter your brand website URL and click **Show audit preview** (brand name alone is not enough for product suggestions).",
        );
        setStep(1);
        const params = new URLSearchParams(searchParams);
        params.set("step", "1");
        setSearchParams(params, { replace: true });
        return;
      }
    }
    setStep(next);
    const params = new URLSearchParams(searchParams);
    params.set("step", String(next));
    setSearchParams(params, { replace: true });
  }

  function resetSitePreview() {
    setSitePreviewPhase("form");
    setVerifiedSite(null);
  }

  async function unlockPreview(e: FormEvent) {
    e.preventDefault();
    const website = syncBrandWebsite();
    if (!brandName.trim()) {
      setError("Enter your brand name.");
      return;
    }
    if (!website) {
      setError("Enter your brand website URL.");
      return;
    }
    setError(null);
    setSiteVerifyMessage("Checking site…");
    setSitePreviewPhase("loading");
    try {
      const probe = await probeSiteProtection(website);
      if (probe.bot_wall && probe.provider === "cloudflare") {
        setSiteVerifyMessage(
          "Site is protected by Cloudflare, this may take up to a minute…",
        );
      } else if (probe.bot_wall) {
        setSiteVerifyMessage("Site uses bot protection, this may take up to a minute…");
      } else {
        setSiteVerifyMessage("Verifying site…");
      }
      const result = await verifyBrandSite(website);
      setBrandWebsite(result.canonical_url);
      setVerifiedSite(result);
      setSitePreviewPhase("confirmed");
    } catch (err) {
      setSitePreviewPhase("form");
      const raw = err instanceof Error ? err.message : "Could not verify that site";
      const msg =
        raw.includes("timed out") || raw.includes("TimeoutError")
          ? "Site verification is taking longer than expected (common with Cloudflare). Try again — it can take up to a minute."
          : parseApiDetail(raw);
      setError(msg);
    }
  }

  const sitePreviewReady = sitePreviewPhase === "confirmed" && Boolean(verifiedSite);

  function handleMarketChange(name: string, code: string) {
    setMarketCountry(name);
    setMarketCountryCode(code);
  }

  async function runAudit() {
    setRunning(true);
    setAuditProgress(null);
    setError(null);
    const website = syncBrandWebsite();
    const comps = includedCompetitorUrls();
    let ga4Prop = ga4PropertyId.trim();
    let ga4Ch = ga4AiChannels.trim();
    if (!ga4Prop) {
      try {
        const ga4Status = await fetchGa4Status();
        ga4Prop = ga4Status.selected_property_id?.trim() || "";
        if (!ga4Ch) ga4Ch = ga4Status.ai_channel_names?.trim() || "";
        if (ga4Prop) setGa4PropertyId(ga4Prop);
        if (ga4Ch) setGa4AiChannels(ga4Ch);
      } catch {
        /* GA4 optional */
      }
    }
    try {
      const { audit_dir } = await startAuditBackground({
        brand_name: brandName.trim(),
        brand_website: website,
        industry: industry === INDUSTRY_PLACEHOLDER ? "" : industry,
        competitors: comps,
        wizard_market_country: marketCountry,
        wizard_market_country_code: marketCountryCode,
        wizard_products: productRows.map((r) => ({
          product_or_service: r.product_or_service.trim(),
          prompts: r.prompts.map((p) => String(p).trim()).filter(Boolean),
        })),
        wizard_competitors: competitorDetails.map((r) => ({
          competitor_brand: r.competitor_brand,
          competitor_website: r.competitor_website,
          included: r.included,
        })),
        ...(ga4Prop ? { ga4_property_id: ga4Prop } : {}),
        ...(ga4Ch ? { ga4_ai_channels: ga4Ch } : {}),
      });
      setActiveAuditDir(audit_dir);
      saveAuditRunDraft({
        active: true,
        auditDir: audit_dir,
        brandName: brandName.trim(),
        brandWebsite: website,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Audit failed");
      setRunning(false);
      setActiveAuditDir(null);
    }
  }

  return (
    <div className="page-container">
      <PageHeader
        title="New audit setup"
        description="Configure brand, competitors, and prompts—then run the crawl."
      />
      {loggedIn && user && (
        <div className="alert-success mb-6">
          Signed in as <strong>{user.email}</strong> — saved to your archive.
        </div>
      )}
      <StepIndicator steps={WIZARD_STEPS} current={step} />

      {error && <div className="alert-error">{error}</div>}

      {step === 1 && sitePreviewPhase === "form" && (
        <form className="card-surface p-6 mb-6" onSubmit={unlockPreview}>
          <h3>Brand & website</h3>
          <p className="text-sm text-gray-600 mb-4">
            Primary crawl target and how the report labels your brand.
          </p>
          <div className="mb-4">
            <label htmlFor="brandName">Brand name (required)</label>
            <input
              className="input-field"
              id="brandName"
              value={brandName}
              onChange={(e) => setBrandName(e.target.value)}
              placeholder="e.g. the name customers use in search"
            />
          </div>
          <UrlAutocomplete
            ref={brandWebsiteRef}
            id="brandWebsite"
            label="Brand website (required)"
            value={brandWebsite}
            onChange={setBrandWebsite}
            help="Search popular sites (Tranco list) or paste a full URL."
            placeholder="Type to search popular sites…"
          />
          <button type="submit" className="btn-primary">
            Show audit preview
          </button>
        </form>
      )}

      {step === 1 && previewUnlocked && (
        <div className="card-surface p-6 mb-6">
          <h3>Brand & website</h3>
          <p className="text-sm text-gray-600 mb-4">How this will appear in the audit.</p>

          {sitePreviewPhase === "loading" && (
            <p className="alert-info flex items-center gap-2 mb-4" role="status">
              <Loader2 className="w-5 h-5 shrink-0 animate-spin text-brand-accent" />
              {siteVerifyMessage}
            </p>
          )}

          {sitePreviewReady && verifiedSite && (
            <div className="mb-4">
              <BrandAuditPreviewRow
                faviconUrl={verifiedSite.favicon_url}
                brandName={brandName.trim()}
                websiteUrl={verifiedSite.canonical_url}
              />
              {verifiedSite.warning && (
                <p className="text-sm text-amber-700 mt-2">{verifiedSite.warning}</p>
              )}
            </div>
          )}

          {sitePreviewReady && (
            <>
              <div className="mb-4 mt-6">
                <label htmlFor="industry">Brand industry (required)</label>
                <select
                  className="input-field"
                  id="industry"
                  value={industry}
                  onChange={(e) => setIndustry(e.target.value)}
                >
                  <option>{INDUSTRY_PLACEHOLDER}</option>
                  {industries.map((ind) => (
                    <option key={ind} value={ind}>
                      {ind}
                    </option>
                  ))}
                </select>
              </div>
              <CountryCombobox
                id="marketCountry"
                label="Country / region (optional)"
                value={marketCountry}
                countryCode={marketCountryCode}
                onChange={handleMarketChange}
                help="Used for AI prompt wording and competitor suggestions. ISO code is set from your selection."
              />
            </>
          )}

          <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
            <button
              type="button"
              className="btn-secondary"
              disabled={sitePreviewPhase === "loading"}
              onClick={resetSitePreview}
            >
              ← Change website
            </button>
            <button
              type="button"
              className="btn-primary"
              disabled={!sitePreviewReady || industry === INDUSTRY_PLACEHOLDER}
              onClick={() => {
                if (verifiedSite?.canonical_url) {
                  setBrandWebsite(verifiedSite.canonical_url);
                }
                goToStep(2);
              }}
            >
              Continue →
            </button>
          </div>
        </div>
      )}

      {step === 2 && (
        <WizardGa4Step
          onBack={() => goToStep(1)}
          onContinue={() => goToStep(3)}
          onSkip={() => goToStep(3)}
          onSelectionSaved={(propertyId, aiChannelNames) => {
            setGa4PropertyId(propertyId);
            setGa4AiChannels(aiChannelNames);
          }}
        />
      )}

      {step === 3 && (
        <WizardProductsStep
          brandName={brandName.trim()}
          brandWebsite={resolveBrandWebsite()}
          marketCountry={marketCountry}
          marketCountryCode={marketCountryCode}
          rows={productRows}
          selected={selectedProducts}
          onRowsChange={setProductRows}
          onSelectedChange={setSelectedProducts}
          onBack={() => goToStep(2)}
          onContinue={() => {
            const byName = new Map(
              productRows.map((r) => [r.product_or_service.trim(), r] as const),
            );
            const picked = selectedProducts
              .map((n) => byName.get(n))
              .filter((r): r is ProductServiceRow => Boolean(r))
              .filter((r) => !isCustomPromptsCategory(r.product_or_service));
            setProductRows(picked);
            goToStep(4);
          }}
        />
      )}

      {step === 4 && (
        <WizardCompetitorsStep
          brandWebsite={resolveBrandWebsite()}
          brandName={brandName.trim()}
          productNames={selectedProductNames()}
          marketCountry={marketCountry}
          marketCountryCode={marketCountryCode}
          rows={competitorDetails}
          onRowsChange={setCompetitorDetails}
          onBack={() => goToStep(3)}
          onContinue={() => goToStep(5)}
        />
      )}

      {step === 5 && (
        <WizardGeneratePromptsStep
          brandWebsite={resolveBrandWebsite()}
          marketCountry={marketCountry}
          marketCountryCode={marketCountryCode}
          rows={productRows}
          onRowsChange={setProductRows}
          onBack={() => goToStep(4)}
          onContinue={() => goToStep(6)}
        />
      )}

      {step === 6 && (
        <WizardPromptsStep
          rows={productRows}
          onRowsChange={setProductRows}
          onBack={() => goToStep(5)}
          onContinue={() => goToStep(7)}
        />
      )}

      {step === 7 && (
        <div className="card-surface p-6 mb-6">
          <h3>Run audit</h3>
          <p className="text-sm text-gray-600 mb-4">
            Crawl, scoring, report generation, <strong>AI prompt probes (share of voice)</strong>,
            and <strong>sentiment analysis</strong> run on the server in the background. This
            usually takes several minutes.
          </p>
          <div className="alert-info mb-4">
            <p className="mb-0">
              You can <strong>leave this page</strong> (open another tab, go to Home, or close the
              browser) and come back later — the audit keeps running. Return to this step or check{" "}
              <Link to="/audits" className="underline font-medium">
                Existing audits
              </Link>{" "}
              when it finishes. Progress is saved in this browser while the run is active.
            </p>
          </div>
          <p>
            <strong>{brandName}</strong> — {brandWebsite}
          </p>
          {marketCountry && (
            <p className="text-sm text-gray-600 mt-2">
              Primary market: {marketCountry}
              {marketCountryCode ? ` (${marketCountryCode})` : ""}
            </p>
          )}
          <p className="text-sm text-gray-600 mt-2">
            Prompts: {totalSelectedPrompts()} · Competitors:{" "}
            {includedCompetitorUrls().length || "none"}
          </p>
          {running && <AuditRunProgress progress={auditProgress} />}
          <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
            <button
              type="button"
              className="btn-secondary"
              onClick={() => goToStep(6)}
              disabled={running}
            >
              ← Back
            </button>
            <button
              type="button"
              className="btn-primary"
              onClick={runAudit}
              disabled={running}
            >
              {running ? "Running…" : "Run audit"}
            </button>
          </div>
        </div>
      )}

      <p className="mt-4">
        <Link to="/" className="text-sm text-gray-600 hover:text-brand-dark">
          ← Back to home
        </Link>
      </p>
    </div>
  );
}
