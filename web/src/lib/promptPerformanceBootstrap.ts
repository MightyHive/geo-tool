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

  // One request: the server decides between the primary locale (huge audits)
  // and all locales, instead of a /summary round trip before the real fetch.
  try {
    return await fetchPromptPerformanceContext(auditDirOrSlug, { bootstrap: true });
  } catch (err) {
    // A timeout means the server is struggling; don't queue two more heavy requests.
    if (err instanceof Error && err.message.startsWith("Timed out")) throw err;
    /* otherwise fall back to the summary-first path below (older API) */
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
