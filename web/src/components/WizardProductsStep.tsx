import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { suggestProductsServices } from "../api/client";
import { CUSTOM_PROMPTS_LABEL, isCustomPromptsCategory } from "../lib/customPrompts";
import type { ProductServiceRow } from "../types";

interface WizardProductsStepProps {
  brandName: string;
  brandWebsite: string;
  marketCountry: string;
  marketCountryCode: string;
  rows: ProductServiceRow[];
  selected: string[];
  onRowsChange: (rows: ProductServiceRow[]) => void;
  onSelectedChange: (selected: string[]) => void;
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

export function WizardProductsStep({
  brandName,
  brandWebsite,
  marketCountry,
  marketCountryCode,
  rows,
  selected,
  onRowsChange,
  onSelectedChange,
  onBack,
  onContinue,
}: WizardProductsStepProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [customLine, setCustomLine] = useState("");

  const siteReady = Boolean(brandWebsite.trim());

  function toggleName(name: string) {
    if (selected.includes(name)) {
      onSelectedChange(selected.filter((n) => n !== name));
    } else {
      onSelectedChange([...selected, name]);
    }
  }

  function addCustomProduct() {
    const name = customLine.trim();
    if (!name) {
      setError("Enter a product or service name.");
      return;
    }
    if (isCustomPromptsCategory(name)) {
      setError(`“${CUSTOM_PROMPTS_LABEL}” is reserved for prompts you add on the Review prompts step.`);
      return;
    }
    setError(null);
    const exists = rows.some((r) => r.product_or_service.trim().toLowerCase() === name.toLowerCase());
    if (!exists) {
      onRowsChange([...rows, { product_or_service: name, prompts: [] }]);
    }
    if (!selected.includes(name)) {
      onSelectedChange([...selected, name]);
    }
    setCustomLine("");
    setSuccess(`Added “${name}”. Prompts will be generated on the Generate prompts step.`);
  }

  async function handleSuggest() {
    if (!siteReady) {
      setError(
        "Add and verify your brand website on step 1 (Show audit preview) — the brand name alone is not enough.",
      );
      return;
    }
    setLoading(true);
    setError(null);
    setSuccess(null);
    try {
      const { rows: next } = await suggestProductsServices({
        brand_website: brandWebsite.trim(),
        market_country: marketCountry.trim(),
        market_country_code: marketCountryCode.trim(),
      });
      onRowsChange(next);
      const labels = next.map((r) => r.product_or_service.trim()).filter(Boolean);
      onSelectedChange(labels);
      setSuccess(`Received ${labels.length} lines — adjust the list below, then continue.`);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Gemini request failed";
      setError(parseApiError(msg));
    } finally {
      setLoading(false);
    }
  }

  function handleContinue() {
    if (!rows.length) {
      setError("Use Suggest products or services from website and keep at least one line.");
      return;
    }
    if (!selected.length) {
      setError("Select at least one product or service from the list.");
      return;
    }
    setError(null);
    onContinue();
  }

  return (
    <div className="card-surface p-6 mb-6">
      <h3>Products or services</h3>
      <p className="text-sm text-gray-600 mb-4">
        By default you get <strong>five</strong> product or service lines and{" "}
        <strong>five</strong> shopper-style prompts each, using your primary market from step 1 when
        set. Choose which lines to keep; step 5 generates AI prompts for any custom lines you add.
      </p>

      {!siteReady && (
        <div className="alert-info mb-4">
          <p className="mb-2">
            Gemini needs your <strong>brand website URL</strong> from step 1 — not just the brand
            name{brandName.trim() ? ` (“${brandName.trim()}”)` : ""}.
          </p>
          <p className="text-sm mb-0">
            On step 1, enter the site URL, click <strong>Show audit preview</strong>, choose an
            industry, then continue. Use <strong>← Back</strong> to fix step 1 if you skipped that.
          </p>
        </div>
      )}

      <button
        type="button"
        className="btn-primary inline-flex items-center gap-2 mb-4"
        disabled={!siteReady || loading}
        onClick={handleSuggest}
      >
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Sparkles className="w-4 h-4" />
        )}
        {loading ? "Asking Gemini…" : "Suggest products or services from website (Gemini)"}
      </button>

      {error && <div className="alert-error mb-4">{error}</div>}
      {success && <div className="alert-success mb-4">{success}</div>}

      {rows.length > 0 && (
        <fieldset className="mb-4">
          <legend className="text-sm font-medium text-brand-dark mb-2 block">
            Select products or services to keep
          </legend>
          <ul className="space-y-2">
            {rows.map((row) => {
              const name = row.product_or_service.trim();
              if (!name || isCustomPromptsCategory(name)) return null;
              const checked = selected.includes(name);
              return (
                <li key={name}>
                  <label className="flex items-start gap-2 cursor-pointer rounded-lg border border-gray-200 p-3 hover:bg-gray-50">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={checked}
                      onChange={() => toggleName(name)}
                    />
                    <span>
                      <span className="font-medium text-brand-dark">{name}</span>
                      {row.prompts.length > 0 && (
                        <span className="block text-xs text-gray-500 mt-1">
                          {row.prompts.filter(Boolean).length} AI prompts generated
                        </span>
                      )}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
        </fieldset>
      )}

      <div className="mb-4 rounded-lg border border-dashed border-gray-300 bg-gray-50/80 p-4">
        <label htmlFor="custom-product" className="text-sm font-medium text-brand-dark block mb-2">
          Add your own product or service
        </label>
        <p className="text-xs text-gray-500 mb-3">
          Deselect a Gemini suggestion above, or add a plain-text line that is not in the list.
        </p>
        <div className="flex flex-wrap gap-2">
          <input
            id="custom-product"
            className="input-field flex-1 min-w-[12rem]"
            value={customLine}
            onChange={(e) => setCustomLine(e.target.value)}
            placeholder="e.g. Mobile tyre fitting"
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addCustomProduct();
              }
            }}
          />
          <button type="button" className="btn-secondary" onClick={addCustomProduct}>
            Add
          </button>
        </div>
      </div>

      <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
        <button type="button" className="btn-secondary" onClick={onBack}>
          ← Back
        </button>
        <button type="button" className="btn-primary" onClick={handleContinue}>
          Continue →
        </button>
      </div>
    </div>
  );
}
