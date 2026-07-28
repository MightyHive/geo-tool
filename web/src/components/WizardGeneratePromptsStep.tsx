import { useEffect, useMemo, useState } from "react";
import { suggestPromptsForProducts } from "../api/client";
import { isCustomPromptsCategory } from "../lib/customPrompts";
import type { PromptLocale } from "../lib/promptLocales";
import type { ProductServiceRow } from "../types";
import { PromptLocaleEditor } from "./PromptLocaleEditor";
import { WizardStepProgress, type WizardStepProgressItem } from "./WizardStepProgress";

interface WizardGeneratePromptsStepProps {
  brandWebsite: string;
  marketCountry: string;
  marketCountryCode: string;
  promptLocales: PromptLocale[];
  onPromptLocalesChange: (locales: PromptLocale[]) => void;
  rows: ProductServiceRow[];
  onRowsChange: (rows: ProductServiceRow[]) => void;
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

function rowHasPrompts(row: ProductServiceRow): boolean {
  return row.prompts.some((p) => String(p).trim());
}

function mergeGeneratedPrompts(
  existing: ProductServiceRow[],
  generated: ProductServiceRow[],
): ProductServiceRow[] {
  const byLabel = new Map(
    generated.map((r) => [r.product_or_service.trim().toLowerCase(), r] as const),
  );
  return existing.map((row) => {
    const label = row.product_or_service.trim();
    if (!label || rowHasPrompts(row)) return row;
    const gen = byLabel.get(label.toLowerCase());
    if (gen?.prompts?.length) {
      return {
        ...row,
        product_or_service: label,
        prompts: gen.prompts.map((p) => String(p).trim()).filter(Boolean),
      };
    }
    return row;
  });
}

type Phase = "configure" | "generating" | "ready" | "error";

export function WizardGeneratePromptsStep({
  brandWebsite,
  marketCountry,
  marketCountryCode,
  promptLocales,
  onPromptLocalesChange,
  rows,
  onRowsChange,
  onBack,
  onContinue,
}: WizardGeneratePromptsStepProps) {
  const [phase, setPhase] = useState<Phase>("configure");
  const [error, setError] = useState<string | null>(null);
  const [progressSteps, setProgressSteps] = useState<WizardStepProgressItem[]>([
    { id: "check", label: "Checking product lines", status: "pending" },
    { id: "generate", label: "Generating AI prompts (Gemini)", status: "pending" },
    { id: "ready", label: "Prompts ready to review", status: "pending" },
  ]);
  const cancelledRef = useRef(false);

  useEffect(() => {
    cancelledRef.current = false;
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  const linesNeedingPrompts = useMemo(
    () =>
      rows
        .filter((r) => {
          const label = r.product_or_service.trim();
          return label && !isCustomPromptsCategory(label) && !rowHasPrompts(r);
        })
        .map((r) => r.product_or_service.trim()),
    [rows],
  );

  const extraLocaleCount = Math.max(0, promptLocales.length - 1);

  async function runGeneration() {
    setError(null);
    setPhase("generating");
    setProgressSteps([
      { id: "check", label: "Checking product lines", status: "active" },
      { id: "generate", label: "Generating AI prompts (Gemini)", status: "pending" },
      { id: "ready", label: "Prompts ready to review", status: "pending" },
    ]);

    await new Promise((r) => setTimeout(r, 300));
    if (cancelledRef.current) return;

    if (linesNeedingPrompts.length === 0) {
  useEffect(() => {
    let cancelled = false;

    async function run() {
      setReady(false);
      setError(null);
      setProgressSteps([
        { id: "check", label: "Checking product lines", status: "done" },
        {
          id: "generate",
          label: "All product lines already have prompts",
          status: "done",
        },
        { id: "ready", label: "Prompts ready to review", status: "done" },
      ]);
      setPhase("ready");
      return;
    }

    setProgressSteps([
      { id: "check", label: "Checking product lines", status: "done" },
      {
        id: "generate",
        label: `Generating prompts for ${linesNeedingPrompts.length} line(s) in ${marketCountry || "your market"}…`,
        status: "active",
      },
      { id: "ready", label: "Prompts ready to review", status: "pending" },
    ]);

    try {
      const { rows: generated } = await suggestPromptsForProducts({
        brand_website: brandWebsite.trim(),
        products: linesNeedingPrompts,
        market_country: marketCountry.trim(),
        market_country_code: marketCountryCode.trim(),
      });
      if (cancelledRef.current) return;
      onRowsChange(mergeGeneratedPrompts(rows, generated));
      setProgressSteps([
        { id: "check", label: "Checking product lines", status: "done" },
        {
          id: "generate",
          label: extraLocaleCount
            ? `AI prompts generated for ${marketCountry || "primary market"} (extra markets adapted at probe time)`
            : "AI prompts generated",
          status: "done",
        },
        { id: "ready", label: "Prompts ready to review", status: "done" },
      ]);
      setPhase("ready");
    } catch (e) {
      if (cancelledRef.current) return;
      setError(e instanceof Error ? e.message : "Gemini request failed");
      setProgressSteps((prev) =>
        prev.map((s) =>
          s.id === "generate"
            ? { ...s, label: "Prompt generation failed", status: "done" }
            : s,
        ),
      );
      setPhase("error");

      try {
        const { rows: generated } = await suggestPromptsForProducts({
          brand_website: brandWebsite.trim(),
          products: linesNeedingPrompts,
          market_country: marketCountry.trim(),
          market_country_code: marketCountryCode.trim(),
        });
        if (cancelled) return;
        onRowsChange(mergeGeneratedPrompts(rows, generated));
        setProgressSteps([
          { id: "check", label: "Checking product lines", status: "done" },
          { id: "generate", label: "AI prompts generated", status: "done" },
          { id: "ready", label: "Prompts ready to review", status: "done" },
        ]);
        setReady(true);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Gemini request failed");
        setProgressSteps((prev) =>
          prev.map((s) =>
            s.id === "generate"
              ? { ...s, label: "Prompt generation failed", status: "done" }
              : s,
          ),
        );
      }
    }
  }

  const busy = phase === "generating";
  const ready = phase === "ready";
    void run();
    return () => {
      cancelled = true;
    };
  }, [
    brandWebsite,
    linesNeedingPrompts.join("|"),
    marketCountry,
    marketCountryCode,
    onRowsChange,
    rows,
  ]);

  return (
    <div className="card-surface p-6 mb-6 space-y-6">
      <div>
        <h3>Markets &amp; AI prompts</h3>
        <p className="text-sm text-gray-600 mt-1">
          Confirm which markets and languages to probe first. Prompts are generated for your
          primary market, then adapted for each additional market (for example changing
          &ldquo;in Belgium&rdquo; to &ldquo;in the Netherlands&rdquo;) when probes run.
        </p>
      </div>

      <PromptLocaleEditor
        marketCountry={marketCountry}
        marketCountryCode={marketCountryCode}
        locales={promptLocales}
        onChange={onPromptLocalesChange}
      />

      {(phase === "generating" || phase === "ready" || phase === "error") && (
        <WizardStepProgress
          title="Progress"
          detail={
            linesNeedingPrompts.length > 0
              ? `Generating prompts for: ${linesNeedingPrompts.join(", ")}`
              : "Every selected line already has prompts."
          }
          steps={progressSteps}
        />
      )}

      {error && <div className="alert-error">{parseApiError(error)}</div>}

      <div className="flex flex-wrap justify-between items-center gap-3 pt-4 border-t border-gray-200">
        <button type="button" className="btn-secondary" onClick={onBack} disabled={busy}>
          ← Back
        </button>
        <div className="flex flex-wrap items-center gap-2">
          {!ready && (
            <button
              type="button"
              className="btn-primary"
              disabled={busy || !marketCountry.trim()}
              onClick={() => void runGeneration()}
            >
              {phase === "error" ? "Retry generate prompts" : "Generate prompts"}
            </button>
          )}
          <button type="button" className="btn-primary" disabled={!ready} onClick={onContinue}>
            Review prompts →
          </button>
        </div>
      </div>
    </div>
  );
}
