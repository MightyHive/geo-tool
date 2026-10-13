import { useEffect, useRef, useState } from "react";
import { Loader2, Plus, RefreshCw, Trash2, Users } from "lucide-react";
import { suggestPersonas } from "../api/client";
import { cleanPersonas, MAX_PERSONAS } from "../lib/personas";
import type { Persona } from "../types";

interface WizardPersonasStepProps {
  brandWebsite: string;
  brandName: string;
  industry: string;
  productNames: string[];
  marketCountry: string;
  marketCountryCode: string;
  /** null = step skipped / not generated yet. */
  personas: Persona[] | null;
  onPersonasChange: (personas: Persona[] | null) => void;
  onBack: () => void;
  onContinue: () => void;
}

function parseApiError(raw: string): string {
  try {
    const data = JSON.parse(raw) as { detail?: string };
    if (typeof data.detail === "string") return data.detail;
  } catch {
    /* use raw */
  }
  return raw || "Request failed";
}

export function WizardPersonasStep({
  brandWebsite,
  brandName,
  industry,
  productNames,
  marketCountry,
  marketCountryCode,
  personas,
  onPersonasChange,
  onBack,
  onContinue,
}: WizardPersonasStepProps) {
  const [drafts, setDrafts] = useState<Persona[]>(personas ?? []);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [edited, setEdited] = useState(false);
  const requestedKeyRef = useRef("");

  const requestKey = `${brandWebsite}|${marketCountryCode}|${productNames.join(",")}`;

  async function generate() {
    setLoading(true);
    setError(null);
    try {
      const res = await suggestPersonas({
        brand_website: brandWebsite.trim(),
        brand_name: brandName,
        industry,
        products: productNames,
        market_country: marketCountry,
        market_country_code: marketCountryCode,
      });
      setDrafts(res.personas);
      setEdited(false);
    } catch (e) {
      setError(parseApiError(e instanceof Error ? e.message : String(e)));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (personas && personas.length) return;
    if (!brandWebsite.trim() || requestedKeyRef.current === requestKey) return;
    requestedKeyRef.current = requestKey;
    void generate();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- generate once per brand/market/products
  }, [requestKey]);

  function update(index: number, patch: Partial<Persona>) {
    setEdited(true);
    setDrafts((prev) => prev.map((p, i) => (i === index ? { ...p, ...patch } : p)));
  }

  function remove(index: number) {
    setEdited(true);
    setDrafts((prev) => prev.filter((_, i) => i !== index));
  }

  function add() {
    setEdited(true);
    setDrafts((prev) => [...prev, { name: "", description: "" }]);
  }

  function regenerate() {
    if (edited && !window.confirm("Replace your edited personas with a new Gemini suggestion?")) return;
    void generate();
  }

  const cleaned = cleanPersonas(drafts);

  return (
    <div className="card-surface p-6 mb-6 space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="flex items-center gap-2">
            <Users className="h-5 w-5 text-blue-600" />
            Buyer personas <span className="text-sm font-normal text-gray-500">(optional)</span>
          </h3>
          <p className="text-sm text-gray-600 mt-1">
            Gemini suggests the groups of people most likely to buy from {brandName || "this brand"}.
            Edit, remove or add personas. Each persona gets up to 5 prompts written in its own voice,
            probed <strong>in addition to</strong> your topic prompts (up to 25 persona prompts).
          </p>
        </div>
        <button
          type="button"
          className="btn-secondary text-xs inline-flex items-center gap-1 shrink-0"
          onClick={regenerate}
          disabled={loading}
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Regenerate
        </button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-gray-500 py-4">
          <Loader2 className="h-4 w-4 animate-spin" />
          Generating personas with Gemini…
        </div>
      )}

      {error && !loading && (
        <div className="alert-error flex items-center justify-between gap-3">
          <span>{error}</span>
          <button type="button" className="underline font-medium" onClick={() => void generate()}>
            Retry
          </button>
        </div>
      )}

      {!loading && (
        <div className="grid gap-3 md:grid-cols-2">
          {drafts.map((persona, index) => (
            <div key={index} className="rounded-lg border border-gray-200 bg-white p-3 space-y-2">
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={persona.name}
                  onChange={(e) => update(index, { name: e.target.value })}
                  placeholder="Persona name"
                  aria-label={`Persona ${index + 1} name`}
                  className="flex-1 min-w-0 rounded-md border border-gray-300 px-2 py-1.5 text-sm font-medium focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none"
                />
                <button
                  type="button"
                  onClick={() => remove(index)}
                  className="rounded p-1 text-gray-400 hover:text-red-500 hover:bg-red-50"
                  title="Remove persona"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
              <textarea
                value={persona.description}
                onChange={(e) => update(index, { description: e.target.value })}
                placeholder="Who they are, what they need, what triggers a purchase"
                aria-label={`Persona ${index + 1} description`}
                rows={3}
                className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none"
              />
            </div>
          ))}
          {drafts.length < MAX_PERSONAS && (
            <button
              type="button"
              onClick={add}
              className="flex min-h-[120px] items-center justify-center gap-1.5 rounded-lg border border-dashed border-gray-300 text-sm font-medium text-gray-500 hover:border-blue-400 hover:text-blue-600"
            >
              <Plus className="h-4 w-4" />
              Add persona
            </button>
          )}
        </div>
      )}

      <div className="flex flex-wrap justify-between items-center gap-3 pt-4 border-t border-gray-200">
        <button type="button" className="btn-secondary" onClick={onBack} disabled={loading}>
          ← Back
        </button>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            className="btn-secondary"
            disabled={loading}
            onClick={() => {
              onPersonasChange(null);
              onContinue();
            }}
          >
            Skip personas
          </button>
          <button
            type="button"
            className="btn-primary"
            disabled={loading || cleaned.length === 0}
            onClick={() => {
              onPersonasChange(cleaned);
              onContinue();
            }}
          >
            Use {cleaned.length} persona{cleaned.length === 1 ? "" : "s"} →
          </button>
        </div>
      </div>
    </div>
  );
}
