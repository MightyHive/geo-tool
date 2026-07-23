/**
 * Prefer a single locale over Overall when an audit is large enough that the
 * Overall Topic→Market→Prompt tree would be expensive to render.
 */
import type { PromptPerformanceContext } from "../types";
import { OVERALL_LOCALE_KEY, configuredPromptLocales } from "./localeProbeView";

/** ≥ this many configured locales → prefer a single default locale. */
export const HUGE_AUDIT_LOCALE_COUNT = 3;
/** ≥ this many prompts (per locale) → prefer a single default locale. */
export const HUGE_AUDIT_PROMPT_COUNT = 40;
/** Overall row estimate (prompts × locales) above this → prefer a single locale. */
export const HUGE_AUDIT_OVERALL_ROW_ESTIMATE = 80;

export function isHugeMultiLocaleAudit(
  ctx: PromptPerformanceContext | null | undefined,
): boolean {
  if (!ctx) return false;
  const locales = configuredPromptLocales(ctx);
  if (locales.length < 2) return false;
  const promptCount = Number(ctx.prompt_count ?? ctx.stored_prompt_count ?? 0) || 0;
  const overallEstimate = promptCount * locales.length;
  return (
    locales.length >= HUGE_AUDIT_LOCALE_COUNT
    || promptCount >= HUGE_AUDIT_PROMPT_COUNT
    || overallEstimate >= HUGE_AUDIT_OVERALL_ROW_ESTIMATE
  );
}

/**
 * Initial locale filter for Prompts / Citations / related AI Visibility views.
 * Huge multi-locale audits default to the audit's primary locale; otherwise Overall.
 * User can always switch back to Overall in the filter.
 */
export function preferredInitialLocaleKey(
  ctx: PromptPerformanceContext | null | undefined,
): string {
  if (!ctx) return OVERALL_LOCALE_KEY;
  if (!isHugeMultiLocaleAudit(ctx)) return OVERALL_LOCALE_KEY;
  const locales = configuredPromptLocales(ctx);
  const preferred = String(ctx.default_locale_key || "").trim();
  if (preferred && locales.some((loc) => loc.key === preferred)) {
    return preferred;
  }
  return locales[0]?.key || OVERALL_LOCALE_KEY;
}
