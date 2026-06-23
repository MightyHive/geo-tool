import { useRef, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { normalizeCompetitorUrl, suggestCompetitors } from "../api/client";
import type { CompetitorDetail } from "../types";
import {
  UrlAutocomplete,
  type UrlAutocompleteHandle,
} from "./UrlAutocomplete";

const MAX_COMPETITORS = 12;
const INITIAL_GEMINI_BATCH = 3;

interface WizardCompetitorsStepProps {
  brandWebsite: string;
  brandName: string;
  productNames: string[];
  marketCountry: string;
  marketCountryCode: string;
  rows: CompetitorDetail[];
  onRowsChange: (rows: CompetitorDetail[]) => void;
  onBack: () => void;
  onContinue: () => void;
}

function parseApiError(raw: string): string {
  try {
    const data = JSON.parse(raw) as { detail?: string | { msg?: string }[] };
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail) && data.detail[0]?.msg) return data.detail[0].msg;
  } catch {
    /* use raw */
  }
  return raw || "Request failed";
}

function toDetail(
  row: { competitor_brand: string; competitor_website: string; favicon_url: string },
  included = true,
): CompetitorDetail {
  return {
    competitor_brand: row.competitor_brand,
    competitor_website: row.competitor_website,
    favicon_url: row.favicon_url,
    included,
  };
}

export function WizardCompetitorsStep({
  brandWebsite,
  brandName,
  productNames,
  marketCountry,
  marketCountryCode,
  rows,
  onRowsChange,
  onBack,
  onContinue,
}: WizardCompetitorsStepProps) {
  const [mode, setMode] = useState<"manual" | "ai">("ai");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [manualUrl, setManualUrl] = useState("");
  const manualUrlRef = useRef<UrlAutocompleteHandle>(null);

  const siteReady = Boolean(brandWebsite.trim());
  const productsReady = productNames.length > 0;
  const rowCount = rows.filter((r) => r.competitor_website.trim()).length;

  function toggleIncluded(url: string) {
    onRowsChange(
      rows.map((r) =>
        r.competitor_website === url ? { ...r, included: !r.included } : r,
      ),
    );
  }

  async function handleGeminiInitial() {
    if (!siteReady) {
      setError("Brand website missing—complete step 1 first.");
      return;
    }
    if (!productsReady) {
      setError("Select products or services in step 3 first.");
      return;
    }
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const { rows: found } = await suggestCompetitors({
        brand_website: brandWebsite.trim(),
        products_and_services: productNames,
        market_country: marketCountry.trim(),
        market_country_code: marketCountryCode.trim(),
      });
      if (!found.length) {
        setError("Gemini returned no competitor suggestions.");
        return;
      }
      const limited = found.slice(0, INITIAL_GEMINI_BATCH).map((r) => toDetail(r, true));
      onRowsChange(limited);
      setSuccess(
        `Suggested ${limited.length} competitor(s). Use Suggest more to merge additional results.`,
      );
    } catch (e) {
      setError(parseApiError(e instanceof Error ? e.message : "Competitor search failed"));
    } finally {
      setLoading(false);
    }
  }

  async function handleGeminiMore() {
    if (!siteReady || !productsReady) {
      setError(
        !siteReady
          ? "Brand website missing—complete step 1 first."
          : "Select products or services in step 3 first.",
      );
      return;
    }
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const { rows: found } = await suggestCompetitors({
        brand_website: brandWebsite.trim(),
        products_and_services: productNames,
        market_country: marketCountry.trim(),
        market_country_code: marketCountryCode.trim(),
      });
      const next = [...rows];
      const seen = new Set(next.map((r) => r.competitor_website.toLowerCase()));
      let added = 0;
      for (const row of found) {
        const url = row.competitor_website.trim();
        if (!url || seen.has(url.toLowerCase())) continue;
        if (next.length >= MAX_COMPETITORS) break;
        seen.add(url.toLowerCase());
        next.push(toDetail(row, true));
        added += 1;
      }
      onRowsChange(next);
      if (added > 0) {
        setSuccess(`Merged ${added} new competitor(s) (${next.length} total).`);
      } else {
        setSuccess("No new competitor URLs in this pass (duplicates were skipped).");
      }
    } catch (e) {
      setError(parseApiError(e instanceof Error ? e.message : "Competitor search failed"));
    } finally {
      setLoading(false);
    }
  }

  async function addManualCompetitor() {
    const raw = (manualUrlRef.current?.getQuery() ?? manualUrl).trim();
    if (!raw) {
      setError("Enter or select a competitor homepage URL.");
      return;
    }
    if (rowCount >= MAX_COMPETITORS) {
      setError(`You can list at most ${MAX_COMPETITORS} competitors.`);
      return;
    }
    setError(null);
    try {
      const { canonical_url, favicon_url } = await normalizeCompetitorUrl(raw);
      if (rows.some((r) => r.competitor_website.toLowerCase() === canonical_url.toLowerCase())) {
        setError("That competitor is already in the list.");
        return;
      }
      const host = canonical_url.replace(/^https?:\/\//, "").split("/")[0] ?? "";
      const label =
        host.replace(/^www\./, "").split(".")[0]?.replace(/-/g, " ") || "Competitor";
      onRowsChange([
        ...rows,
        {
          competitor_brand: label.charAt(0).toUpperCase() + label.slice(1),
          competitor_website: canonical_url,
          favicon_url,
          included: true,
        },
      ]);
      setManualUrl("");
    } catch (e) {
      setError(parseApiError(e instanceof Error ? e.message : "Could not add URL"));
    }
  }

  return (
    <div className="card-surface p-6 mb-6">
      <h3>Competitors</h3>
      <p className="text-sm text-gray-600 mb-4">
        Optional: up to {MAX_COMPETITORS} peer site URLs for prompt performance and comparison.
        {brandName && (
          <>
            {" "}
            Brand context: <strong>{brandName}</strong>
          </>
        )}
      </p>

      <fieldset className="mb-4 space-y-2">
        <legend className="text-sm font-medium text-brand-dark mb-2 block">
          How do you want to choose competitor websites?
        </legend>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="radio"
            name="comp_mode"
            checked={mode === "manual"}
            onChange={() => setMode("manual")}
          />
          I will enter URLs myself
        </label>
        <label className="flex items-center gap-2 cursor-pointer">
          <input
            type="radio"
            name="comp_mode"
            checked={mode === "ai"}
            onChange={() => setMode("ai")}
          />
          Suggest competitors with Gemini (AI)
        </label>
      </fieldset>

      {mode === "ai" && (
        <div className="mb-4">
          <p className="text-sm text-gray-600 mb-3">
            Gemini suggests competitor brands and homepages from your site URL, the products or
            services from step 3, and your primary market. The first run fills up to{" "}
            {INITIAL_GEMINI_BATCH} rows; use Suggest more to merge additional results.
          </p>
          <div className="flex flex-wrap gap-2 mb-2">
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-2"
              disabled={loading || !siteReady || !productsReady}
              onClick={handleGeminiInitial}
            >
              {loading ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Sparkles className="w-4 h-4" />
              )}
              Suggest competitors with Gemini
            </button>
            <button
              type="button"
              className="btn-secondary inline-flex items-center gap-2"
              disabled={loading || rowCount === 0 || rowCount >= MAX_COMPETITORS}
              onClick={handleGeminiMore}
            >
              Suggest more competitors (Gemini)
            </button>
          </div>
        </div>
      )}

      {loading && (
        <p className="alert-info flex items-center gap-2 mb-4" role="status">
          <Loader2 className="w-5 h-5 shrink-0 animate-spin text-brand-accent" />
          Asking Gemini for competitors…
        </p>
      )}

      {error && <div className="alert-error mb-4">{error}</div>}
      {success && <div className="alert-success mb-4">{success}</div>}

      {rowCount > 0 && (
        <div className="mb-6">
          <h4 className="text-sm font-medium text-brand-dark mb-2">Competitors</h4>
          <p className="text-xs text-gray-500 mb-3">
            Favicon, brand, and site per row. Toggle Include for the audit.
          </p>
          <ul className="space-y-2">
            {rows.map((row) => {
              const url = row.competitor_website.trim();
              if (!url) return null;
              return (
                <li
                  key={url}
                  className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 p-3"
                >
                  {row.favicon_url ? (
                    <img
                      src={row.favicon_url}
                      alt=""
                      width={28}
                      height={28}
                      className="rounded shrink-0"
                    />
                  ) : (
                    <span className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded bg-gray-200 text-xs text-gray-500">
                      ?
                    </span>
                  )}
                  <span className="font-medium text-brand-dark min-w-[6rem]">
                    {row.competitor_brand || "—"}
                  </span>
                  <span className="text-sm text-gray-600 flex-1 min-w-[12rem]">{url}</span>
                  <label className="flex items-center gap-2 text-sm cursor-pointer ml-auto">
                    <input
                      type="checkbox"
                      checked={row.included}
                      onChange={() => toggleIncluded(url)}
                    />
                    Include
                  </label>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {rowCount === 0 && (
        <p className="text-sm text-gray-500 mb-4">
          No competitors yet—use Suggest competitors with Gemini or add a site below.
        </p>
      )}

      <div className="mb-4">
        <h4 className="text-sm font-medium text-brand-dark mb-2">Add a competitor</h4>
        {rowCount >= MAX_COMPETITORS ? (
          <p className="text-sm text-gray-500">
            You have {MAX_COMPETITORS} competitor rows (the limit).
          </p>
        ) : (
          <>
            <UrlAutocomplete
              ref={manualUrlRef}
              id="competitorManual"
              label="Search or paste one competitor homepage"
              value={manualUrl}
              onChange={setManualUrl}
              placeholder="Type to search popular sites…"
              help="Adds one site to the table above. Duplicates are skipped."
            />
            <button
              type="button"
              className="btn-secondary"
              disabled={loading}
              onClick={addManualCompetitor}
            >
              Add to table
            </button>
          </>
        )}
      </div>

      <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
        <button type="button" className="btn-secondary" onClick={onBack} disabled={loading}>
          ← Back
        </button>
        <button type="button" className="btn-primary" onClick={onContinue} disabled={loading}>
          Continue →
        </button>
      </div>
    </div>
  );
}
