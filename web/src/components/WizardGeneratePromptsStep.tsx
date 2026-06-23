import { useEffect, useMemo, useRef, useState } from "react";
import { suggestPromptsForProducts } from "../api/client";
import { isCustomPromptsCategory } from "../lib/customPrompts";
import type { ProductServiceRow } from "../types";
import { WizardStepProgress, type WizardStepProgressItem } from "./WizardStepProgress";

interface WizardGeneratePromptsStepProps {
  brandWebsite: string;
  marketCountry: string;
  marketCountryCode: string;
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
        product_or_service: label,
        prompts: gen.prompts.map((p) => String(p).trim()).filter(Boolean),
      };
    }
    return row;
  });
}

export function WizardGeneratePromptsStep({
  brandWebsite,
  marketCountry,
  marketCountryCode,
  rows,
  onRowsChange,
  onBack,
  onContinue,
}: WizardGeneratePromptsStepProps) {
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [progressSteps, setProgressSteps] = useState<WizardStepProgressItem[]>([
    { id: "check", label: "Checking product lines", status: "active" },
    { id: "generate", label: "Generating AI prompts (Gemini)", status: "pending" },
    { id: "ready", label: "Prompts ready to review", status: "pending" },
  ]);
  const started = useRef(false);

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

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    let cancelled = false;

    async function run() {
      setError(null);
      setProgressSteps([
        { id: "check", label: "Checking product lines", status: "active" },
        { id: "generate", label: "Generating AI prompts (Gemini)", status: "pending" },
        { id: "ready", label: "Prompts ready to review", status: "pending" },
      ]);

      await new Promise((r) => setTimeout(r, 400));
      if (cancelled) return;

      if (linesNeedingPrompts.length === 0) {
        setProgressSteps([
          { id: "check", label: "Checking product lines", status: "done" },
          {
            id: "generate",
            label: "All product lines already have prompts",
            status: "done",
          },
          { id: "ready", label: "Prompts ready to review", status: "done" },
        ]);
        setReady(true);
        return;
      }

      setProgressSteps([
        { id: "check", label: "Checking product lines", status: "done" },
        {
          id: "generate",
          label: `Generating prompts for ${linesNeedingPrompts.length} line(s)…`,
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
        started.current = false;
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- run once per visit
  }, []);

  return (
    <div className="card-surface p-6 mb-6">
      <h3>Generate AI prompts</h3>
      <p className="text-sm text-gray-600 mb-4">
        We create shopper-style prompts for each product or service you selected — including any
        lines you typed manually. You can leave this page and come back; your wizard progress is
        saved in this browser.
      </p>

      <WizardStepProgress
        title="Progress"
        detail={
          linesNeedingPrompts.length > 0
            ? `Generating prompts for: ${linesNeedingPrompts.join(", ")}`
            : "Every selected line already has prompts from step 3."
        }
        steps={progressSteps}
      />

      {error && <div className="alert-error mb-4">{parseApiError(error)}</div>}

      <div className="flex justify-between items-center mt-6 pt-4 border-t border-gray-200">
        <button type="button" className="btn-secondary" onClick={onBack} disabled={!ready && !error}>
          ← Back
        </button>
        <button type="button" className="btn-primary" disabled={!ready} onClick={onContinue}>
          Review prompts →
        </button>
      </div>
    </div>
  );
}
