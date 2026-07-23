import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { CountryCombobox } from "./CountryCombobox";
import { countryCodeForName } from "../lib/countries";
import {
  MAX_PROMPT_LOCALES,
  SUPPORTED_LANGUAGES,
  defaultLanguageForCountry,
  makeLocale,
  normalizePromptLocales,
  type PromptLocale,
} from "../lib/promptLocales";

interface PromptLocaleEditorProps {
  marketCountry: string;
  marketCountryCode: string;
  locales: PromptLocale[];
  onChange: (locales: PromptLocale[]) => void;
  /** Compact layout for Config / Prompts sections */
  compact?: boolean;
}

export function PromptLocaleEditor({
  marketCountry,
  marketCountryCode,
  locales,
  onChange,
  compact = false,
}: PromptLocaleEditorProps) {
  const [draftCountry, setDraftCountry] = useState("");
  const [draftCountryCode, setDraftCountryCode] = useState("");
  const [draftLanguage, setDraftLanguage] = useState("en");
  const [error, setError] = useState<string | null>(null);

  const normalized = normalizePromptLocales(locales, marketCountry, marketCountryCode);
  const extras = normalized.slice(1);

  const sync = (nextExtras: PromptLocale[]) => {
    onChange(normalizePromptLocales(nextExtras, marketCountry, marketCountryCode));
  };

  const addLocale = () => {
    setError(null);
    const country = draftCountry.trim();
    const code = (draftCountryCode || countryCodeForName(country)).trim().toUpperCase();
    if (!country || !code) {
      setError("Choose a market country first.");
      return;
    }
    if (normalized.length >= MAX_PROMPT_LOCALES) {
      setError(`You can configure up to ${MAX_PROMPT_LOCALES} market/language combinations.`);
      return;
    }
    const langMeta =
      SUPPORTED_LANGUAGES.find((l) => l.code === draftLanguage) ??
      defaultLanguageForCountry(code);
    const next = makeLocale({
      country,
      country_code: code,
      language: langMeta.code,
      language_name: langMeta.name,
    });
    if (normalized.some((l) => l.key === next.key)) {
      setError(`${next.label} is already configured.`);
      return;
    }
    sync([...extras, next]);
    setDraftCountry("");
    setDraftCountryCode("");
    setDraftLanguage(defaultLanguageForCountry(code).code);
  };

  const removeAt = (key: string) => {
    sync(extras.filter((l) => l.key !== key));
  };

  const updateLanguage = (key: string, language: string) => {
    const meta = SUPPORTED_LANGUAGES.find((l) => l.code === language);
    sync(
      extras.map((l) =>
        l.key === key
          ? makeLocale({
              country: l.country,
              country_code: l.country_code,
              language,
              language_name: meta?.name,
            })
          : l,
      ),
    );
  };

  return (
    <div className={compact ? "space-y-3" : "space-y-4"}>
      <div>
        <h4 className="text-sm font-semibold text-[#0d0d0d] mb-1">
          Prompt markets &amp; languages
        </h4>
        <p className="text-xs text-gray-500 leading-relaxed">
          Default is your primary market with English. Add more market + language pairs before
          generating prompts — extra markets get market-specific prompt adaptations when probes run.
        </p>
      </div>

      <ul className="space-y-2">
        <li className="flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
          <div className="min-w-0">
            <p className="text-sm font-medium text-[#0d0d0d] truncate">
              {normalized[0]?.label || "Primary market: English"}
            </p>
            <p className="text-[11px] text-gray-400">Default · always included</p>
          </div>
          <span className="text-[11px] font-medium uppercase tracking-wide text-gray-400 shrink-0">
            Default
          </span>
        </li>
        {extras.map((loc) => (
          <li
            key={loc.key}
            className="flex flex-wrap items-center gap-2 rounded-lg border border-gray-200 px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-[#0d0d0d] truncate">{loc.country}</p>
              <p className="text-[11px] text-gray-400">{loc.country_code}</p>
            </div>
            <select
              className="text-sm border border-gray-200 rounded-md px-2 py-1.5 bg-white"
              value={loc.language}
              onChange={(e) => updateLanguage(loc.key, e.target.value)}
              aria-label={`Language for ${loc.country}`}
            >
              {SUPPORTED_LANGUAGES.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="p-1.5 text-gray-400 hover:text-red-600"
              onClick={() => removeAt(loc.key)}
              aria-label={`Remove ${loc.label}`}
            >
              <Trash2 className="w-4 h-4" />
            </button>
          </li>
        ))}
      </ul>

      {normalized.length < MAX_PROMPT_LOCALES && (
        <div className="rounded-lg border border-dashed border-gray-300 p-3 space-y-3">
          <p className="text-xs font-semibold uppercase tracking-wide text-gray-400">
            Add market + language
          </p>
          <CountryCombobox
            id="prompt-locale-market"
            label="Market"
            value={draftCountry}
            countryCode={draftCountryCode}
            onChange={(name, code) => {
              setDraftCountry(name);
              setDraftCountryCode(code);
              if (code) {
                setDraftLanguage(defaultLanguageForCountry(code).code);
              }
            }}
            help="Search country / region…"
          />
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex-1 min-w-[10rem]">
              <span className="block text-xs text-gray-500 mb-1">Language</span>
              <select
                className="w-full text-sm border border-gray-200 rounded-md px-2 py-2 bg-white"
                value={draftLanguage}
                onChange={(e) => setDraftLanguage(e.target.value)}
              >
                {SUPPORTED_LANGUAGES.map((l) => (
                  <option key={l.code} value={l.code}>
                    {l.name}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" className="btn-secondary inline-flex items-center gap-1.5" onClick={addLocale}>
              <Plus className="w-4 h-4" />
              Add
            </button>
          </div>
          {error && <p className="text-xs text-red-600">{error}</p>}
        </div>
      )}
    </div>
  );
}
