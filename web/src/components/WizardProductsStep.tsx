import { useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { CUSTOM_PROMPTS_LABEL, isCustomPromptsCategory } from "../lib/customPrompts";
import type { ProductServiceRow } from "../types";

export type ProductsSuggestStatus = "idle" | "loading" | "ready" | "error";

interface WizardProductsStepProps {
  brandName: string;
  brandWebsite: string;
  rows: ProductServiceRow[];
  selected: string[];
  suggestStatus: ProductsSuggestStatus;
  suggestError: string | null;
  modelCategory: string | null;
  onRowsChange: (rows: ProductServiceRow[]) => void;
  onSelectedChange: (selected: string[]) => void;
  onRetrySuggest: () => void;
  onBack: () => void;
  onContinue: () => void;
}

export function WizardProductsStep({
  brandName,
  brandWebsite,
  rows,
  selected,
  suggestStatus,
  suggestError,
  modelCategory,
  onRowsChange,
  onSelectedChange,
  onRetrySuggest,
  onBack,
  onContinue,
}: WizardProductsStepProps) {
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [customLine, setCustomLine] = useState("");

  const siteReady = Boolean(brandWebsite.trim());
  const loading = suggestStatus === "loading";

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

  function handleContinue() {
    if (!rows.length) {
      setError("Wait for website suggestions or add at least one product line.");
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
        Gemini suggests <strong>five</strong> product or service lines from your website as soon as
        it is verified on step 1. Choose which lines to keep; the Markets &amp; prompts step
        generates AI prompts for any custom lines you add.
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

      {loading && (
        <p className="alert-info flex items-center gap-2 mb-4" role="status">
          <Loader2 className="w-5 h-5 shrink-0 animate-spin text-brand-accent" />
          Asking Gemini for product or service lines…
        </p>
      )}

      {modelCategory && suggestStatus === "ready" && (
        <p className="text-xs text-gray-500 mb-3">
          Suggested category: <span className="font-medium text-brand-dark">{modelCategory}</span>
        </p>
      )}

      <button
        type="button"
        className="btn-primary inline-flex items-center gap-2 mb-4"
        disabled={!siteReady || loading}
        onClick={() => {
          setError(null);
          setSuccess(null);
          onRetrySuggest();
        }}
      >
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Sparkles className="w-4 h-4" />
        )}
        {loading
          ? "Asking Gemini…"
          : rows.length
            ? "Refresh suggestions from website"
            : "Suggest products or services from website (Gemini)"}
      </button>

      {(error || suggestError) && (
        <div className="alert-error mb-4">{error || suggestError}</div>
      )}
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
        <button type="button" className="btn-primary" onClick={handleContinue} disabled={loading}>
          Continue →
        </button>
      </div>
    </div>
  );
}
