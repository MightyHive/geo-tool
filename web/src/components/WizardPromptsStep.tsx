import { useEffect, useState } from "react";
import {
  CUSTOM_PROMPTS_LABEL,
  isCustomPromptsCategory,
  mergeCustomPromptsRow,
} from "../lib/customPrompts";
import type { ProductServiceRow } from "../types";

export interface PromptSelection {
  product: string;
  prompt: string;
  included: boolean;
}

interface WizardPromptsStepProps {
  rows: ProductServiceRow[];
  onRowsChange: (rows: ProductServiceRow[]) => void;
  onBack: () => void;
  onContinue: () => void;
}

function buildSelections(rows: ProductServiceRow[]): PromptSelection[] {
  const out: PromptSelection[] = [];
  for (const row of rows) {
    const product = row.product_or_service.trim();
    if (!product) continue;
    for (const p of row.prompts) {
      const prompt = String(p).trim();
      if (prompt) out.push({ product, prompt, included: true });
    }
  }
  return out;
}

function selectionsToRows(selections: PromptSelection[]): ProductServiceRow[] {
  const byProduct = new Map<string, string[]>();
  for (const s of selections) {
    if (!s.included) continue;
    const list = byProduct.get(s.product) ?? [];
    list.push(s.prompt);
    byProduct.set(s.product, list);
  }
  return Array.from(byProduct.entries()).map(([product_or_service, prompts]) => ({
    product_or_service,
    prompts,
  }));
}

function selectionKey(product: string, prompt: string): string {
  return `${product}\u0000${prompt}`;
}

function sortProductGroups(
  entries: [string, PromptSelection[]][],
): [string, PromptSelection[]][] {
  return [...entries].sort(([a], [b]) => {
    const aCustom = isCustomPromptsCategory(a);
    const bCustom = isCustomPromptsCategory(b);
    if (aCustom && !bCustom) return 1;
    if (!aCustom && bCustom) return -1;
    return a.localeCompare(b, undefined, { sensitivity: "base" });
  });
}

export function WizardPromptsStep({
  rows,
  onRowsChange,
  onBack,
  onContinue,
}: WizardPromptsStepProps) {
  const [selections, setSelections] = useState<PromptSelection[]>(() => buildSelections(rows));
  const [error, setError] = useState<string | null>(null);
  const [expandedProduct, setExpandedProduct] = useState<string | null>(null);
  const [customPromptInput, setCustomPromptInput] = useState("");

  useEffect(() => {
    setSelections(buildSelections(rows));
    const first = rows.find((r) => r.product_or_service.trim())?.product_or_service.trim();
    setExpandedProduct(first ?? null);
  }, [rows]);

  const grouped = sortProductGroups(
    Array.from(
      selections.reduce<Map<string, PromptSelection[]>>((acc, s) => {
        const list = acc.get(s.product) ?? [];
        list.push(s);
        acc.set(s.product, list);
        return acc;
      }, new Map()).entries(),
    ),
  );

  const totalIncluded = selections.filter((s) => s.included).length;
  const totalPrompts = selections.length;

  function syncCustomPromptsToRows(nextSelections: PromptSelection[]) {
    const customPrompts = nextSelections
      .filter((s) => isCustomPromptsCategory(s.product))
      .map((s) => s.prompt);
    onRowsChange(mergeCustomPromptsRow(rows, customPrompts));
  }

  function toggle(product: string, prompt: string) {
    setSelections((prev) =>
      prev.map((s) =>
        s.product === product && s.prompt === prompt ? { ...s, included: !s.included } : s,
      ),
    );
  }

  function addCustomPrompt() {
    const prompt = customPromptInput.trim();
    if (!prompt) {
      setError("Enter a prompt before adding.");
      return;
    }
    const duplicate = selections.some(
      (s) => s.prompt.toLowerCase() === prompt.toLowerCase(),
    );
    if (duplicate) {
      setError("That prompt is already in the list.");
      return;
    }
    setError(null);
    const nextSelections = [
      ...selections,
      { product: CUSTOM_PROMPTS_LABEL, prompt, included: true },
    ];
    setSelections(nextSelections);
    syncCustomPromptsToRows(nextSelections);
    setCustomPromptInput("");
    setExpandedProduct(CUSTOM_PROMPTS_LABEL);
  }

  function removeCustomPrompt(prompt: string) {
    const nextSelections = selections.filter(
      (s) => !(isCustomPromptsCategory(s.product) && s.prompt === prompt),
    );
    setSelections(nextSelections);
    syncCustomPromptsToRows(nextSelections);
  }

  function handleContinue() {
    const filtered = selectionsToRows(selections);
    if (!filtered.length || !filtered.some((r) => r.prompts.length > 0)) {
      setError("Keep at least one prompt selected.");
      return;
    }
    setError(null);
    onRowsChange(filtered);
    onContinue();
  }

  const hasGeneratedPrompts = rows.some(
    (r) => !isCustomPromptsCategory(r.product_or_service) && r.prompts.some((p) => String(p).trim()),
  );

  return (
    <div className="card-surface p-6 mb-6">
      <h3>Review AI prompts</h3>
      <p className="text-sm text-gray-600 mb-4">
        Uncheck any prompt you do not want in the audit or in Prompt performance. You can also add
        your own prompts — they appear under <strong>{CUSTOM_PROMPTS_LABEL}</strong>.
      </p>
      <p className="text-sm text-gray-700 mb-4">
        <strong>{totalIncluded}</strong> of {totalPrompts} prompts selected
      </p>

      {error && <div className="alert-error mb-4">{error}</div>}

      {!hasGeneratedPrompts && totalPrompts === 0 && (
        <div className="alert-info mb-4">
          No AI-generated prompts yet. Add your own below, or go back to{" "}
          <strong>Generate prompts</strong> (step 5).
        </div>
      )}

      <div className="mb-6 rounded-lg border border-dashed border-gray-300 bg-gray-50/80 p-4">
        <label htmlFor="custom-prompt" className="text-sm font-medium text-brand-dark block mb-2">
          Add your own prompt
        </label>
        <p className="text-xs text-gray-500 mb-3">
          Write a shopper-style question you want tracked in live AI probes and on the Prompt
          performance dashboard.
        </p>
        <div className="flex flex-wrap gap-2">
          <textarea
            id="custom-prompt"
            className="input-field flex-1 min-w-[16rem] min-h-[4.5rem] resize-y"
            value={customPromptInput}
            onChange={(e) => setCustomPromptInput(e.target.value)}
            placeholder="e.g. Where can I buy quality brake pads online in the UK?"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                addCustomPrompt();
              }
            }}
          />
          <button type="button" className="btn-secondary self-end" onClick={addCustomPrompt}>
            Add prompt
          </button>
        </div>
      </div>

      {grouped.length > 0 && (
        <div className="space-y-3 mb-6">
          {grouped.map(([product, items]) => {
            const open = expandedProduct === product;
            const includedCount = items.filter((i) => i.included).length;
            const isCustom = isCustomPromptsCategory(product);
            return (
              <div key={product} className="rounded-lg border border-gray-200 overflow-hidden">
                <button
                  type="button"
                  className="w-full flex items-center justify-between gap-2 px-4 py-3 text-left bg-gray-50 hover:bg-gray-100 transition-colors"
                  onClick={() => setExpandedProduct(open ? null : product)}
                  aria-expanded={open}
                >
                  <span className="font-medium text-brand-dark">
                    {product}{" "}
                    <span className="text-gray-500 font-normal">
                      ({includedCount}/{items.length} prompts)
                    </span>
                  </span>
                  <span className="text-gray-400 text-sm">{open ? "−" : "+"}</span>
                </button>
                {open && (
                  <ul className="p-3 space-y-2 border-t border-gray-200">
                    {items.map((item) => (
                      <li
                        key={selectionKey(item.product, item.prompt)}
                        className="flex items-start gap-3 rounded-lg border border-gray-100 bg-white p-3"
                      >
                        <input
                          type="checkbox"
                          className="mt-1 shrink-0"
                          checked={item.included}
                          onChange={() => toggle(item.product, item.prompt)}
                          aria-label="Include prompt"
                        />
                        <p className="text-sm text-brand-dark leading-relaxed whitespace-pre-wrap break-words flex-1">
                          {item.prompt}
                        </p>
                        {isCustom && (
                          <button
                            type="button"
                            className="text-xs text-gray-500 hover:text-red-600 shrink-0"
                            onClick={() => removeCustomPrompt(item.prompt)}
                          >
                            Remove
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

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
