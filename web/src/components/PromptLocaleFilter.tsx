import { useMemo, useState } from "react";
import type { PromptLocale } from "../lib/promptLocales";
import {
  OVERALL_LOCALE_KEY,
  buildLocaleViewOptions,
  liveProbeForLocaleView,
  type LocaleViewOption,
} from "../lib/localeProbeView";
import type { LiveProbeResult, PromptPerformanceContext } from "../types";
import { cn } from "../lib/utils";
import { ReportFilterSelect } from "./ReportFilterSelect";

interface PromptLocaleFilterProps {
  locales: PromptLocale[];
  selectedKey: string;
  onChange: (key: string) => void;
  /** Hide when only Overall would be shown (no multi-locale config). */
  hideIfSingle?: boolean;
  ctx?: PromptPerformanceContext | null;
  /** Locale keys known failed from fan-out status. */
  failedLocaleKeys?: string[];
  className?: string;
  /** Optional short explanation shown under the select. */
  hint?: string;
}

function optionLabel(opt: LocaleViewOption): string {
  if (opt.key === OVERALL_LOCALE_KEY) return "Overall";
  if (opt.availability === "ready") return opt.label;
  if (opt.availability === "failed") return `${opt.label} (failed)`;
  return `${opt.label} (no data)`;
}

/**
 * Market / language view selector. Defaults to Overall; lists each configured
 * market:language. Missing/failed locales are selectable but show empty states
 * upstream (no silent fallback to default probe data).
 */
export function PromptLocaleFilter({
  locales,
  selectedKey,
  onChange,
  hideIfSingle = true,
  ctx,
  failedLocaleKeys = [],
  className,
  hint = "Limit scores and charts to one market/language, or Overall for every probed locale.",
}: PromptLocaleFilterProps) {
  const options = useMemo(() => {
    if (ctx) return buildLocaleViewOptions(ctx, failedLocaleKeys);
    const base: LocaleViewOption[] = [
      { key: OVERALL_LOCALE_KEY, label: "Overall", availability: "ready" },
      ...locales.map((loc) => ({
        key: loc.key,
        label: loc.label,
        country: loc.country,
        country_code: loc.country_code,
        language: loc.language,
        language_name: loc.language_name,
        availability: "ready" as const,
      })),
    ];
    return base;
  }, [ctx, locales, failedLocaleKeys]);

  if (!options.length) return null;
  if (hideIfSingle && options.length < 2) return null;

  const selected = options.find((o) => o.key === selectedKey) ?? options[0];

  return (
    <div className={cn("flex flex-wrap items-end gap-3", className)}>
      <ReportFilterSelect
        id="locale-view-select"
        label="Market / language"
        hint={hint}
        value={selected?.key ?? OVERALL_LOCALE_KEY}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((opt) => (
          <option key={opt.key} value={opt.key}>
            {optionLabel(opt)}
          </option>
        ))}
      </ReportFilterSelect>
      {selected && selected.key !== OVERALL_LOCALE_KEY && selected.availability !== "ready" ? (
        <span className="pb-6 text-xs text-amber-700">
          {selected.availability === "failed"
            ? "Probe failed for this market — re-run failed markets to fill it in."
            : "No probe data for this market yet."}
        </span>
      ) : null}
    </div>
  );
}

/**
 * Nested Market → Language dropdowns for the Prompts section.
 * Includes Overall as the default top-level option.
 */
export function PromptLocaleNestedFilter({
  locales,
  selectedKey,
  onChange,
  ctx,
  failedLocaleKeys = [],
  className,
}: Omit<PromptLocaleFilterProps, "hideIfSingle">) {
  const options = useMemo(() => {
    if (ctx) return buildLocaleViewOptions(ctx, failedLocaleKeys);
    return [
      { key: OVERALL_LOCALE_KEY, label: "Overall", availability: "ready" as const },
      ...locales.map((loc) => ({
        key: loc.key,
        label: loc.label,
        country: loc.country,
        country_code: loc.country_code,
        language: loc.language,
        language_name: loc.language_name,
        availability: "ready" as const,
      })),
    ];
  }, [ctx, locales, failedLocaleKeys]);

  const markets = useMemo(() => {
    const map = new Map<string, { code: string; name: string; languages: LocaleViewOption[] }>();
    for (const opt of options) {
      if (opt.key === OVERALL_LOCALE_KEY) continue;
      const code = (opt.country_code || "XX").toUpperCase();
      const name = opt.country || code;
      let entry = map.get(code);
      if (!entry) {
        entry = { code, name, languages: [] };
        map.set(code, entry);
      }
      entry.languages.push(opt);
    }
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [options]);

  const selected = options.find((o) => o.key === selectedKey) ?? options[0];
  const isOverall = !selected || selected.key === OVERALL_LOCALE_KEY;

  const [marketCode, setMarketCode] = useState<string>("");

  const activeMarketCode = isOverall
    ? ""
    : (selected?.country_code || marketCode || markets[0]?.code || "").toUpperCase();

  const languagesForMarket = markets.find((m) => m.code === activeMarketCode)?.languages ?? [];

  if (options.length < 2 && markets.length === 0) return null;

  return (
    <div className={cn("flex flex-wrap items-end gap-3 mb-4", className)}>
      <div className="flex flex-col gap-1">
        <label className="text-xs font-semibold uppercase tracking-wide text-gray-400" htmlFor="prompt-market-select">
          Market
        </label>
        <select
          id="prompt-market-select"
          value={isOverall ? OVERALL_LOCALE_KEY : activeMarketCode}
          onChange={(e) => {
            const value = e.target.value;
            if (value === OVERALL_LOCALE_KEY) {
              onChange(OVERALL_LOCALE_KEY);
              setMarketCode("");
              return;
            }
            setMarketCode(value);
            const langs = markets.find((m) => m.code === value)?.languages ?? [];
            const preferEn = langs.find((l) => l.language === "en") ?? langs[0];
            if (preferEn) onChange(preferEn.key);
          }}
          className="text-sm rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-[#0d0d0d] shadow-sm focus:outline-none focus:ring-2 focus:ring-[#0984e3]/30 min-w-[10rem]"
        >
          <option value={OVERALL_LOCALE_KEY}>Overall</option>
          {markets.map((m) => (
            <option key={m.code} value={m.code}>
              {m.name}
            </option>
          ))}
        </select>
      </div>

      {!isOverall && languagesForMarket.length > 0 ? (
        <div className="flex flex-col gap-1">
          <label className="text-xs font-semibold uppercase tracking-wide text-gray-400" htmlFor="prompt-language-select">
            Language
          </label>
          <select
            id="prompt-language-select"
            value={selected?.key ?? languagesForMarket[0]?.key}
            onChange={(e) => onChange(e.target.value)}
            className="text-sm rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-[#0d0d0d] shadow-sm focus:outline-none focus:ring-2 focus:ring-[#0984e3]/30 min-w-[10rem]"
          >
            {languagesForMarket.map((lang) => (
              <option key={lang.key} value={lang.key}>
                {lang.language_name || lang.language || lang.label}
                {lang.availability === "failed"
                  ? " (failed)"
                  : lang.availability === "missing"
                    ? " (no data)"
                    : ""}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      {!isOverall && selected && selected.availability !== "ready" ? (
        <span className="text-xs text-amber-700 pb-1.5">
          {selected.availability === "failed"
            ? "Probe failed for this market — re-run failed markets to fill it in."
            : "No probe data for this market yet."}
        </span>
      ) : null}
    </div>
  );
}

/** Resolve live_probe for the selected locale view (Overall merges; missing → null). */
export function liveProbeForLocale(
  ctx: PromptPerformanceContext | null | undefined,
  localeKey: string,
): LiveProbeResult | null {
  return liveProbeForLocaleView(ctx, localeKey || OVERALL_LOCALE_KEY);
}
