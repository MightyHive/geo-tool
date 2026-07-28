/**
 * Bootstrap prompt-performance payloads for report sections.
 * Huge multi-locale audits load one preferred locale first so Citations / Prompts
 * are not blocked on an all-locales slim GET.
 */
import {
  fetchPromptPerformanceContext,
  fetchPromptPerformanceLocale,
  fetchPromptPerformanceSummary,
} from "../api/client";
import type { PromptPerformanceContext } from "../types";
import { isHugeMultiLocaleAudit, preferredInitialLocaleKey } from "./defaultLocaleView";
import { OVERALL_LOCALE_KEY } from "./localeProbeView";

export async function loadPromptPerformanceBootstrap(
  auditDirOrSlug: string,
  opts?: { allLocales?: boolean; locale?: string },
): Promise<PromptPerformanceContext> {
  if (opts?.locale) {
    if (opts.locale === OVERALL_LOCALE_KEY) {
      return fetchPromptPerformanceLocale(auditDirOrSlug, OVERALL_LOCALE_KEY);
    }
    return fetchPromptPerformanceLocale(auditDirOrSlug, opts.locale);
  }

  if (opts?.allLocales) {
    try {
      return await fetchPromptPerformanceContext(auditDirOrSlug);
    } catch (err) {
      try {
        const summary = await fetchPromptPerformanceSummary(auditDirOrSlug);
        const mini = summary as unknown as PromptPerformanceContext;
        const preferred = preferredInitialLocaleKey(mini);
        if (preferred && preferred !== OVERALL_LOCALE_KEY) {
          return fetchPromptPerformanceLocale(auditDirOrSlug, preferred);
        }
      } catch {
        /* rethrow original */
      }
      throw err;
    }
  }

  let preferred = OVERALL_LOCALE_KEY;
  let huge = false;
  try {
    const summary = await fetchPromptPerformanceSummary(auditDirOrSlug);
    const mini = summary as unknown as PromptPerformanceContext;
    huge = isHugeMultiLocaleAudit(mini);
    preferred = preferredInitialLocaleKey(mini);
  } catch {
    /* summary optional */
  }

  if (huge && preferred !== OVERALL_LOCALE_KEY) {
    return fetchPromptPerformanceLocale(auditDirOrSlug, preferred);
  }

  try {
    return await fetchPromptPerformanceContext(auditDirOrSlug);
  } catch (err) {
    if (preferred !== OVERALL_LOCALE_KEY) {
      return fetchPromptPerformanceLocale(auditDirOrSlug, preferred);
    }
    throw err;
  }
}
