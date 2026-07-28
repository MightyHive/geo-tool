import { useEffect, useRef, useState } from "react";
import {
  CUSTOM_PROMPTS_LABEL,
  isCustomPromptsCategory,
  parseCustomPromptsCsv,
} from "../lib/customPrompts";
import {
  ensurePromptContainsGeoLocator,
  geoLocatorPhraseForMarket,
} from "../lib/geoLocator";
import type { ProductServiceRow } from "../types";

export interface PromptSelection {
  product: string;
  prompt: string;
  included: boolean;
  tags: string[];
  isCustom: boolean;
  customTopic: boolean;
}

interface WizardPromptsStepProps {
  rows: ProductServiceRow[];
  onRowsChange: (rows: ProductServiceRow[]) => void;
  marketCountry?: string;
  marketCountryCode?: string;
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
      if (prompt) out.push({
        product,
        prompt,
        included: true,
        tags: row.prompt_tags?.[prompt] ?? [],
        isCustom: row.is_custom_topic === true
          || isCustomPromptsCategory(product)
          || (row.custom_prompts ?? []).includes(prompt),
        customTopic: row.is_custom_topic === true || isCustomPromptsCategory(product),
      });
    }
  }
  return out;
}

function selectionsToRows(selections: PromptSelection[]): ProductServiceRow[] {
  const byProduct = new Map<string, PromptSelection[]>();
  for (const s of selections) {
    if (!s.included) continue;
    const list = byProduct.get(s.product) ?? [];
    list.push(s);
    byProduct.set(s.product, list);
  }
  return Array.from(byProduct.entries()).map(([product_or_service, items]) => {
    const prompts = items.map((item) => item.prompt);
    return {
      product_or_service,
      prompts,
      prompt_tags: Object.fromEntries(
        items.filter((item) => item.tags.length).map((item) => [item.prompt, item.tags]),
      ),
      custom_prompts: items.filter((item) => item.isCustom).map((item) => item.prompt),
      is_custom_topic: items.some((item) => item.customTopic),
    };
  });
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

// ── Tag chip colours (cycles through a small palette) ────────────────────────
const TAG_COLOURS = [
  "bg-violet-100 text-violet-800 border-violet-200",
  "bg-sky-100 text-sky-800 border-sky-200",
  "bg-emerald-100 text-emerald-800 border-emerald-200",
  "bg-amber-100 text-amber-800 border-amber-200",
  "bg-rose-100 text-rose-800 border-rose-200",
  "bg-teal-100 text-teal-800 border-teal-200",
];

const tagColourCache = new Map<string, string>();
let tagColourIndex = 0;
function tagColour(tag: string): string {
  if (!tagColourCache.has(tag)) {
    tagColourCache.set(tag, TAG_COLOURS[tagColourIndex % TAG_COLOURS.length]);
    tagColourIndex++;
  }
  return tagColourCache.get(tag)!;
}

function TagChip({
  tag,
  onRemove,
  onClick,
  active,
}: {
  tag: string;
  onRemove?: () => void;
  onClick?: () => void;
  active?: boolean;
}) {
  const colour = tagColour(tag);
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-xs font-medium ${colour} ${active ? "ring-2 ring-offset-1 ring-current" : ""} ${onClick ? "cursor-pointer hover:opacity-80" : ""}`}
      onClick={onClick}
    >
      {tag}
      {onRemove && (
        <button
          type="button"
          className="ml-0.5 leading-none opacity-60 hover:opacity-100"
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
          aria-label={`Remove tag ${tag}`}
        >
          ×
        </button>
      )}
    </span>
  );
}

export function WizardPromptsStep({
  rows,
  onRowsChange,
  marketCountry = "",
  marketCountryCode = "",
  onBack,
  onContinue,
}: WizardPromptsStepProps) {
  const [selections, setSelections] = useState<PromptSelection[]>(() => buildSelections(rows));
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [expandedProduct, setExpandedProduct] = useState<string | null>(null);
  const csvInputRef = useRef<HTMLInputElement | null>(null);

  // ── Custom prompt form state ────────────────────────────────────────────────
  const [customPromptInput, setCustomPromptInput] = useState("");
  const [customCategory, setCustomCategory] = useState(CUSTOM_PROMPTS_LABEL);
  const [customCategoryNew, setCustomCategoryNew] = useState("");
  const [customTagInput, setCustomTagInput] = useState("");
  const [customTags, setCustomTags] = useState<string[]>([]);

  // ── Tag filter ──────────────────────────────────────────────────────────────
  const [activeTagFilter, setActiveTagFilter] = useState<string | null>(null);

  // ── Inline tag-add state: tracks which prompt row is being tagged ───────────
  const [tagAddKey, setTagAddKey] = useState<string | null>(null);
  const [inlineTagInput, setInlineTagInput] = useState("");
  const inlineTagRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setSelections(buildSelections(rows));
    const first = rows.find((r) => r.product_or_service.trim())?.product_or_service.trim();
    setExpandedProduct(first ?? null);
  }, [rows]);

  useEffect(() => {
    if (tagAddKey && inlineTagRef.current) inlineTagRef.current.focus();
  }, [tagAddKey]);

  // ── Derived ─────────────────────────────────────────────────────────────────
  const existingCategories = Array.from(
    new Set(selections.map((s) => s.product).filter((p) => !isCustomPromptsCategory(p))),
  ).sort((a, b) => a.localeCompare(b, undefined, { sensitivity: "base" }));

  const allTags = Array.from(new Set(selections.flatMap((s) => s.tags))).sort();
  const visibleSelections = activeTagFilter
    ? selections.filter((s) => s.tags.includes(activeTagFilter))
    : selections;

  const grouped = sortProductGroups(
    Array.from(
      visibleSelections.reduce<Map<string, PromptSelection[]>>((acc, s) => {
        const list = acc.get(s.product) ?? [];
        list.push(s);
        acc.set(s.product, list);
        return acc;
      }, new Map()).entries(),
    ),
  );

  const totalIncluded = selections.filter((s) => s.included).length;
  const totalPrompts = selections.length;

  // ── Helpers ─────────────────────────────────────────────────────────────────
  function toggle(product: string, prompt: string) {
    setSelections((prev) =>
      prev.map((s) =>
        s.product === product && s.prompt === prompt ? { ...s, included: !s.included } : s,
      ),
    );
  }

  function selectAll() {
    setSelections((prev) => prev.map((s) => ({ ...s, included: true })));
  }

  function deselectAll() {
    setSelections((prev) => prev.map((s) => ({ ...s, included: false })));
  }

  function selectGroup(product: string) {
    setSelections((prev) =>
      prev.map((s) => (s.product === product ? { ...s, included: true } : s)),
    );
  }

  function deselectGroup(product: string) {
    setSelections((prev) =>
      prev.map((s) => (s.product === product ? { ...s, included: false } : s)),
    );
  }

  // ── Custom prompt ────────────────────────────────────────────────────────────
  const resolvedCategory =
    customCategory === "__new__"
      ? customCategoryNew.trim() || CUSTOM_PROMPTS_LABEL
      : customCategory;

  function addCustomTagFromInput() {
    const tag = customTagInput.trim();
    if (tag && !customTags.includes(tag)) setCustomTags((t) => [...t, tag]);
    setCustomTagInput("");
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
    const category = resolvedCategory || CUSTOM_PROMPTS_LABEL;
    const customTopic = customCategory === "__new__" || isCustomPromptsCategory(category);
    setError(null);
    const nextSelections: PromptSelection[] = [
      ...selections,
      { product: category, prompt, included: true, tags: customTags, isCustom: true, customTopic },
    ];
    setSelections(nextSelections);
    setCustomPromptInput("");
    setCustomTags([]);
    setCustomTagInput("");
    setExpandedProduct(category);
  }

  function importCustomPromptsCsv(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      const text = typeof reader.result === "string" ? reader.result : "";
      const parsed = parseCustomPromptsCsv(text);
      if (parsed.error) {
        setNotice(null);
        setError(parsed.error);
        return;
      }
      const existing = new Set(selections.map((s) => s.prompt.toLowerCase()));
      const additions: PromptSelection[] = [];
      let skipped = 0;
      for (const row of parsed.rows) {
        const key = row.prompt.toLowerCase();
        if (existing.has(key)) {
          skipped += 1;
          continue;
        }
        existing.add(key);
        additions.push({
          product: row.category,
          prompt: row.prompt,
          included: true,
          tags: row.tags,
          isCustom: true,
          customTopic: isCustomPromptsCategory(row.category),
        });
      }
      if (!additions.length) {
        setNotice(null);
        setError(
          skipped
            ? "All CSV prompts were already in the list."
            : "No valid prompt rows found in the CSV.",
        );
        return;
      }
      setError(null);
      setSelections([...selections, ...additions]);
      setExpandedProduct(additions[0]?.product ?? null);
      setNotice(
        skipped > 0
          ? `Imported ${additions.length} prompt(s); skipped ${skipped} duplicate(s).`
          : `Imported ${additions.length} prompt(s) from CSV.`,
      );
    };
    reader.onerror = () => {
      setNotice(null);
      setError("Could not read the CSV file.");
    };
    reader.readAsText(file);
  }

  function removeCustomPrompt(product: string, prompt: string) {
    const nextSelections = selections.filter(
      (s) => !(s.product === product && s.prompt === prompt && s.isCustom),
    );
    setSelections(nextSelections);
  }

  // ── Inline tags ──────────────────────────────────────────────────────────────
  function commitInlineTag(product: string, prompt: string) {
    const tag = inlineTagInput.trim();
    if (tag) {
      setSelections((prev) =>
        prev.map((s) =>
          s.product === product && s.prompt === prompt && !s.tags.includes(tag)
            ? { ...s, tags: [...s.tags, tag] }
            : s,
        ),
      );
    }
    setInlineTagInput("");
    setTagAddKey(null);
  }

  function addExistingTag(product: string, prompt: string, tag: string) {
    setSelections((prev) =>
      prev.map((s) =>
        s.product === product && s.prompt === prompt && !s.tags.includes(tag)
          ? { ...s, tags: [...s.tags, tag] }
          : s,
      ),
    );
  }

  function removeTag(product: string, prompt: string, tag: string) {
    setSelections((prev) =>
      prev.map((s) =>
        s.product === product && s.prompt === prompt
          ? { ...s, tags: s.tags.filter((t) => t !== tag) }
          : s,
      ),
    );
    if (activeTagFilter === tag) setActiveTagFilter(null);
  }

  // ── Continue ─────────────────────────────────────────────────────────────────
  function handleContinue() {
    const phrase = geoLocatorPhraseForMarket(marketCountry, marketCountryCode);
    const withMarket = phrase
      ? selections.map((s) =>
          s.isCustom
            ? { ...s, prompt: ensurePromptContainsGeoLocator(s.prompt, phrase) }
            : s,
        )
      : selections;
    const filtered = selectionsToRows(withMarket);
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
  const marketPhrase = geoLocatorPhraseForMarket(marketCountry, marketCountryCode);

  return (
    <div className="card-surface p-6 mb-6">
      <h3>Review AI prompts</h3>
      <p className="text-sm text-gray-600 mb-4">
        Uncheck any prompt you do not want in the audit. Add your own prompts below — you can
        assign them to any category and add tags to organise and filter.
      </p>

      {error && <div className="alert-error mb-4">{error}</div>}
      {notice && !error && (
        <div className="mb-4 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
          {notice}
        </div>
      )}

      {!hasGeneratedPrompts && totalPrompts === 0 && (
        <div className="alert-info mb-4">
          No AI-generated prompts yet. Add your own below, or go back to{" "}
          <strong>Generate prompts</strong> (step 5).
        </div>
      )}

      {/* ── Global select controls ─────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <p className="text-sm text-gray-700">
          <strong>{totalIncluded}</strong> of {totalPrompts} prompts selected
        </p>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="text-xs text-brand-600 hover:text-brand-800 underline underline-offset-2"
            onClick={selectAll}
          >
            Select all
          </button>
          <span className="text-gray-300">|</span>
          <button
            type="button"
            className="text-xs text-gray-500 hover:text-gray-700 underline underline-offset-2"
            onClick={deselectAll}
          >
            Deselect all
          </button>
        </div>
      </div>

      {/* ── Tag filter bar ─────────────────────────────────────────────────── */}
      {allTags.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 mb-4 p-3 rounded-lg bg-gray-50 border border-gray-200">
          <span className="text-xs font-medium text-gray-500 mr-1">Filter by tag:</span>
          <button
            type="button"
            className={`text-xs px-2 py-0.5 rounded-full border ${activeTagFilter === null ? "bg-brand-600 text-white border-brand-600" : "bg-white text-gray-600 border-gray-300 hover:border-gray-400"}`}
            onClick={() => setActiveTagFilter(null)}
          >
            All
          </button>
          {allTags.map((tag) => (
            <TagChip
              key={tag}
              tag={tag}
              active={activeTagFilter === tag}
              onClick={() => setActiveTagFilter(activeTagFilter === tag ? null : tag)}
            />
          ))}
        </div>
      )}

      {/* ── Add custom prompt ──────────────────────────────────────────────── */}
      <div className="mb-6 rounded-lg border border-dashed border-gray-300 bg-gray-50/80 p-4 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <label className="text-sm font-medium text-brand-dark block">Add your own prompt</label>
            <p className="text-xs text-gray-500 mt-1">
              Write a shopper-style question <strong>without naming a country or market</strong>
              {marketCountry ? (
                <>
                  {" "}
                  — we&apos;ll add {marketCountry}
                  {marketPhrase ? ` (${marketPhrase})` : ""} automatically, and adapt it for any
                  extra markets you configured.
                </>
              ) : (
                <> — we&apos;ll add your primary market automatically when probes run.</>
              )}{" "}
              Or upload a CSV with columns{" "}
              <code className="text-[11px]">Prompt</code>,{" "}
              <code className="text-[11px]">Category</code>,{" "}
              <code className="text-[11px]">Tags</code> (comma-separated tags).
            </p>
          </div>
          <div>
            <input
              ref={csvInputRef}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) importCustomPromptsCsv(file);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className="btn-secondary text-xs"
              onClick={() => csvInputRef.current?.click()}
            >
              Upload CSV
            </button>
          </div>
        </div>

        <textarea
          id="custom-prompt"
          className="input-field w-full min-h-[4.5rem] resize-y"
          value={customPromptInput}
          onChange={(e) => setCustomPromptInput(e.target.value)}
          placeholder="e.g. Where can I buy quality brake pads online?"
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              addCustomPrompt();
            }
          }}
        />

        {/* Category row */}
        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex-1 min-w-[12rem]">
            <label className="text-xs font-medium text-gray-600 block mb-1">Category</label>
            <select
              className="input-field w-full text-sm"
              value={customCategory}
              onChange={(e) => {
                setCustomCategory(e.target.value);
                if (e.target.value !== "__new__") setCustomCategoryNew("");
              }}
            >
              <option value={CUSTOM_PROMPTS_LABEL}>{CUSTOM_PROMPTS_LABEL}</option>
              {existingCategories.map((cat) => (
                <option key={cat} value={cat}>
                  {cat}
                </option>
              ))}
              <option value="__new__">+ New category…</option>
            </select>
          </div>
          {customCategory === "__new__" && (
            <div className="flex-1 min-w-[12rem]">
              <label className="text-xs font-medium text-gray-600 block mb-1">New category name</label>
              <input
                type="text"
                className="input-field w-full text-sm"
                value={customCategoryNew}
                onChange={(e) => setCustomCategoryNew(e.target.value)}
                placeholder="e.g. Competitor comparison"
                autoFocus
              />
            </div>
          )}
        </div>

        {/* Tags */}
        <div className="space-y-3">
          <div>
            <label className="text-xs font-medium text-gray-600 block mb-1">Add new tag</label>
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="text"
                className="input-field max-w-xs text-sm"
                value={customTagInput}
                onChange={(e) => setCustomTagInput(e.target.value)}
                placeholder="Enter a tag name"
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === ",") {
                    e.preventDefault();
                    addCustomTagFromInput();
                  }
                }}
              />
              <button
                type="button"
                className="btn-secondary text-xs"
                onClick={addCustomTagFromInput}
                disabled={!customTagInput.trim()}
              >
                Add tag
              </button>
            </div>
          </div>

          {allTags.length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-600 mb-2">Choose existing tag</p>
              <div className="flex flex-wrap gap-2">
                {allTags.map((tag) => (
                  <TagChip
                    key={tag}
                    tag={tag}
                    active={customTags.includes(tag)}
                    onClick={() =>
                      setCustomTags((current) =>
                        current.includes(tag)
                          ? current.filter((item) => item !== tag)
                          : [...current, tag],
                      )
                    }
                  />
                ))}
              </div>
            </div>
          )}

          {customTags.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-gray-500">Selected:</span>
            {customTags.map((tag) => (
              <TagChip
                key={tag}
                tag={tag}
                onRemove={() => setCustomTags((t) => t.filter((x) => x !== tag))}
              />
            ))}
            </div>
          )}
        </div>

        <div className="flex justify-end">
          <button type="button" className="btn-secondary" onClick={addCustomPrompt}>
            Add prompt
          </button>
        </div>
      </div>

      {/* ── Prompt groups ──────────────────────────────────────────────────── */}
      {grouped.length > 0 && (
        <div className="space-y-3 mb-6">
          {grouped.map(([product, items]) => {
            const open = expandedProduct === product;
            const includedCount = items.filter((i) => i.included).length;
            const allSelected = items.every((i) => i.included);
            const noneSelected = items.every((i) => !i.included);

            return (
              <div key={product} className="rounded-lg border border-gray-200 overflow-hidden">
                {/* Accordion header */}
                <div className="flex items-center justify-between gap-2 px-4 py-3 bg-gray-50">
                  <button
                    type="button"
                    className="flex items-center gap-2 text-left flex-1 hover:opacity-80 transition-opacity"
                    onClick={() => setExpandedProduct(open ? null : product)}
                    aria-expanded={open}
                  >
                    <span className="font-medium text-brand-dark">
                      {product}{" "}
                      <span className="text-gray-500 font-normal">
                        ({includedCount}/{items.length})
                      </span>
                    </span>
                    <span className="text-gray-400 text-sm">{open ? "−" : "+"}</span>
                  </button>
                  {/* Per-group select/deselect */}
                  <div className="flex items-center gap-2 shrink-0">
                    {!allSelected && (
                      <button
                        type="button"
                        className="text-xs text-brand-600 hover:text-brand-800 underline underline-offset-2"
                        onClick={(e) => {
                          e.stopPropagation();
                          selectGroup(product);
                        }}
                      >
                        Select all
                      </button>
                    )}
                    {!noneSelected && (
                      <button
                        type="button"
                        className="text-xs text-gray-500 hover:text-gray-700 underline underline-offset-2"
                        onClick={(e) => {
                          e.stopPropagation();
                          deselectGroup(product);
                        }}
                      >
                        Deselect all
                      </button>
                    )}
                  </div>
                </div>

                {/* Prompt list */}
                {open && (
                  <ul className="p-3 space-y-2 border-t border-gray-200">
                    {items.map((item) => {
                      const key = selectionKey(item.product, item.prompt);
                      const isTagging = tagAddKey === key;
                      return (
                        <li
                          key={key}
                          className={`rounded-lg border bg-white p-3 transition-opacity ${item.included ? "" : "opacity-50"}`}
                          style={{ borderColor: item.included ? "#e5e7eb" : "#f3f4f6" }}
                        >
                          <div className="flex items-start gap-3">
                            <input
                              type="checkbox"
                              className="mt-1 shrink-0"
                              checked={item.included}
                              onChange={() => toggle(item.product, item.prompt)}
                              aria-label="Include prompt"
                            />
                            <div className="flex-1 min-w-0">
                              <p className="text-sm text-brand-dark leading-relaxed whitespace-pre-wrap break-words">
                                {item.prompt}
                              </p>

                              {/* Tags row */}
                              <div className="flex flex-wrap items-center gap-1.5 mt-2">
                                {item.tags.map((tag) => (
                                  <TagChip
                                    key={tag}
                                    tag={tag}
                                    onRemove={() => removeTag(item.product, item.prompt, tag)}
                                    onClick={() =>
                                      setActiveTagFilter(activeTagFilter === tag ? null : tag)
                                    }
                                    active={activeTagFilter === tag}
                                  />
                                ))}

                                {/* Inline tag input */}
                                {isTagging ? (
                                  <div className="w-full mt-1 rounded-lg border border-gray-200 bg-gray-50 p-3 space-y-3">
                                    <div>
                                      <label className="text-xs font-medium text-gray-600 block mb-1">
                                        Add new tag
                                      </label>
                                      <div className="flex flex-wrap gap-2">
                                        <input
                                          ref={inlineTagRef}
                                          type="text"
                                          className="input-field max-w-xs text-sm"
                                          value={inlineTagInput}
                                          onChange={(e) => setInlineTagInput(e.target.value)}
                                          placeholder="Enter a tag name"
                                          onKeyDown={(e) => {
                                            if (e.key === "Enter" || e.key === ",") {
                                              e.preventDefault();
                                              commitInlineTag(item.product, item.prompt);
                                            }
                                            if (e.key === "Escape") {
                                              setTagAddKey(null);
                                              setInlineTagInput("");
                                            }
                                          }}
                                        />
                                        <button
                                          type="button"
                                          className="btn-secondary text-xs"
                                          disabled={!inlineTagInput.trim()}
                                          onClick={() => commitInlineTag(item.product, item.prompt)}
                                        >
                                          Add tag
                                        </button>
                                        <button
                                          type="button"
                                          className="text-xs text-gray-500 hover:text-gray-700"
                                          onClick={() => {
                                            setTagAddKey(null);
                                            setInlineTagInput("");
                                          }}
                                        >
                                          Done
                                        </button>
                                      </div>
                                    </div>
                                    {allTags.length > 0 && (
                                      <div>
                                        <p className="text-xs font-medium text-gray-600 mb-2">
                                          Choose existing tag
                                        </p>
                                        <div className="flex flex-wrap gap-2">
                                          {allTags.map((tag) => (
                                            <TagChip
                                              key={tag}
                                              tag={tag}
                                              active={item.tags.includes(tag)}
                                              onClick={() => {
                                                if (item.tags.includes(tag)) {
                                                  removeTag(item.product, item.prompt, tag);
                                                } else {
                                                  addExistingTag(item.product, item.prompt, tag);
                                                }
                                              }}
                                            />
                                          ))}
                                        </div>
                                      </div>
                                    )}
                                  </div>
                                ) : (
                                  <button
                                    type="button"
                                    className="text-xs text-gray-400 hover:text-brand-600 border border-dashed border-gray-300 hover:border-brand-400 rounded-full px-2 py-0.5 transition-colors"
                                    onClick={() => {
                                      setTagAddKey(key);
                                      setInlineTagInput("");
                                    }}
                                  >
                                    + tag
                                  </button>
                                )}
                              </div>
                            </div>

                            {item.isCustom && (
                              <button
                                type="button"
                                className="text-xs text-gray-500 hover:text-red-600 shrink-0 mt-0.5"
                                onClick={() => removeCustomPrompt(item.product, item.prompt)}
                              >
                                Remove
                              </button>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}

      {activeTagFilter && grouped.length === 0 && (
        <div className="text-sm text-gray-500 text-center py-6">
          No prompts tagged <strong>{activeTagFilter}</strong>.{" "}
          <button
            type="button"
            className="underline text-brand-600"
            onClick={() => setActiveTagFilter(null)}
          >
            Clear filter
          </button>
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
