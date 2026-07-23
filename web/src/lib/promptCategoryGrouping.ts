/**
 * Resolve prompt category for multi-locale probe rows and collapse Overall
 * view into one row per logical prompt with Market:Language variants.
 */
import type {
  LiveProbePerPrompt,
  PromptPerformanceContext,
  PromptPerformancePssRow,
} from "../types";
import { OVERALL_LOCALE_KEY } from "./localeProbeView";
import { formatLocaleKeyLabel, type PromptLocale } from "./promptLocales";

export type PromptRowMeta = {
  productLabel: string;
  tags: string[];
  /** Canonical source prompt text used for grouping across locales. */
  sourcePrompt: string;
};

export type LocalePromptVariant = {
  localeKey: string;
  row: LiveProbePerPrompt;
};

export type GroupedPromptEntry = {
  productLabel: string;
  tags: string[];
  sourcePrompt: string;
  /** All Market:Language variants for this logical prompt (Overall view). */
  variants: LocalePromptVariant[];
  /** Active row (selected market, or the only row in single-locale view). */
  row: LiveProbePerPrompt;
  selectedLocaleKey: string;
};

function norm(value: string): string {
  return value.trim().toLowerCase();
}

function buildMetadataFromPss(
  mappingRows: PromptPerformancePssRow[],
): {
  byPrompt: Map<string, PromptRowMeta>;
  positional: PromptRowMeta[];
} {
  const byPrompt = new Map<string, PromptRowMeta>();
  const positional: PromptRowMeta[] = [];
  for (const pssRow of mappingRows) {
    const productLabel = String(pssRow.product_or_service || "").trim() || "Other";
    for (const prompt of pssRow.prompts ?? []) {
      const sourcePrompt = String(prompt ?? "").trim();
      const meta: PromptRowMeta = {
        productLabel,
        tags: pssRow.prompt_tags?.[prompt] ?? pssRow.prompt_tags?.[sourcePrompt] ?? [],
        sourcePrompt: sourcePrompt || productLabel,
      };
      positional.push(meta);
      const key = norm(sourcePrompt);
      if (key && !byPrompt.has(key)) byPrompt.set(key, meta);
    }
  }
  return { byPrompt, positional };
}

/** Map a probed (possibly translated) prompt back to configured category metadata. */
export function resolvePromptRowMeta(
  row: LiveProbePerPrompt,
  opts: {
    byPrompt: Map<string, PromptRowMeta>;
    positional: PromptRowMeta[];
    localeIndex: number;
    ctx: PromptPerformanceContext;
    fallbackTopic: string;
  },
): PromptRowMeta {
  const { byPrompt, positional, localeIndex, ctx, fallbackTopic } = opts;
  const probed = String(row.prompt ?? "").trim();
  const probedKey = norm(probed);
  if (probedKey && byPrompt.has(probedKey)) {
    return byPrompt.get(probedKey)!;
  }

  const localeKey = String(row._locale_key || "").trim();
  if (localeKey) {
    const entry = ctx.locale_probes?.[localeKey];
    const probedList = (entry?.prompts_probed ?? []).map(String);
    const sourceList = (entry?.source_prompts ?? []).map(String);
    let mappedIndex = probedKey
      ? probedList.findIndex((p) => norm(p) === probedKey)
      : -1;
    if (mappedIndex < 0 && localeIndex >= 0 && localeIndex < sourceList.length) {
      mappedIndex = localeIndex;
    }
    if (mappedIndex >= 0 && mappedIndex < sourceList.length) {
      const source = sourceList[mappedIndex].trim();
      const sourceKey = norm(source);
      if (sourceKey && byPrompt.has(sourceKey)) {
        return { ...byPrompt.get(sourceKey)!, sourcePrompt: source || byPrompt.get(sourceKey)!.sourcePrompt };
      }
      if (mappedIndex < positional.length) {
        return {
          ...positional[mappedIndex],
          sourcePrompt: source || positional[mappedIndex].sourcePrompt,
        };
      }
    }
  }

  if (localeIndex >= 0 && localeIndex < positional.length) {
    return positional[localeIndex];
  }

  return {
    productLabel: fallbackTopic,
    tags: [],
    sourcePrompt: probed || "Other",
  };
}

/**
 * Annotate each live probe row with category metadata that is stable across locales.
 * Uses source_prompts ↔ prompts_probed when present so translated prompts keep category.
 */
export function annotateProbeRowsWithCategory(
  live: { per_prompt?: LiveProbePerPrompt[] | null } | null | undefined,
  ctx: PromptPerformanceContext,
): Array<{ row: LiveProbePerPrompt; meta: PromptRowMeta }> {
  const perPrompt = (live?.per_prompt ?? []).filter(Boolean) as LiveProbePerPrompt[];
  const mappingRows = ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows;
  const fallbackTopic = ctx.category_labels[0]?.trim() || "Other";

  if (!ctx.use_pss || !mappingRows.length) {
    return perPrompt.map((row) => ({
      row,
      meta: {
        productLabel: fallbackTopic,
        tags: [],
        sourcePrompt: String(row.prompt ?? "").trim() || fallbackTopic,
      },
    }));
  }

  const { byPrompt, positional } = buildMetadataFromPss(mappingRows);
  const localeCounters = new Map<string, number>();

  return perPrompt.map((row) => {
    const localeKey = String(row._locale_key || "").trim() || "__default__";
    const localeIndex = localeCounters.get(localeKey) ?? 0;
    localeCounters.set(localeKey, localeIndex + 1);
    const meta = resolvePromptRowMeta(row, {
      byPrompt,
      positional,
      localeIndex,
      ctx,
      fallbackTopic,
    });
    return { row, meta };
  });
}

export function marketLanguageLabel(
  localeKey: string,
  locales?: Array<Pick<PromptLocale, "key" | "country" | "country_code" | "language" | "language_name" | "label">> | null,
): string {
  return formatLocaleKeyLabel(localeKey, locales);
}

/**
 * Average brand visibility % across prompt rows.
 * Prefers each row's `list_metrics.visibility_pct`; falls back to provided per-row pcts.
 */
export function averageVisibilityAcrossPrompts(
  rows: Array<{ list_metrics?: { visibility_pct?: number | null } | null }>,
  fallbackPcts?: Array<number | null | undefined>,
): number {
  const values: number[] = [];
  for (let i = 0; i < rows.length; i++) {
    const fromMetrics = rows[i]?.list_metrics?.visibility_pct;
    if (fromMetrics != null && Number.isFinite(Number(fromMetrics))) {
      values.push(Number(fromMetrics));
      continue;
    }
    const fallback = fallbackPcts?.[i];
    if (fallback != null && Number.isFinite(Number(fallback))) {
      values.push(Number(fallback));
    }
  }
  if (!values.length) return 0;
  return Math.round(values.reduce((sum, v) => sum + v, 0) / values.length);
}

/**
 * Build display rows. In Overall view, collapse locale variants of the same
 * source prompt into one entry (legacy flat list with optional selected locale).
 *
 * Prefer {@link buildTopicMarketPromptTree} for the Topic → Market → Prompt UI.
 */
export function buildGroupedPromptEntries(
  live: { per_prompt?: LiveProbePerPrompt[] | null } | null | undefined,
  ctx: PromptPerformanceContext,
  options: {
    localeKey: string;
    selectedLocales?: Record<string, string>;
  },
): GroupedPromptEntry[] {
  const { localeKey, selectedLocales = {} } = options;
  const annotated = annotateProbeRowsWithCategory(live, ctx);
  const isOverall = !localeKey || localeKey === OVERALL_LOCALE_KEY;

  if (!isOverall) {
    return annotated.map(({ row, meta }) => ({
      productLabel: meta.productLabel,
      tags: meta.tags,
      sourcePrompt: meta.sourcePrompt,
      variants: [{ localeKey: row._locale_key || localeKey, row }],
      row,
      selectedLocaleKey: row._locale_key || localeKey,
    }));
  }

  const groups = new Map<string, GroupedPromptEntry>();
  for (const { row, meta } of annotated) {
    const groupKey = `${norm(meta.productLabel)}\u0000${norm(meta.sourcePrompt)}`;
    const variantKey = String(row._locale_key || ctx.default_locale_key || "default").trim();
    const existing = groups.get(groupKey);
    if (!existing) {
      const preferred =
        selectedLocales[groupKey]
        || (ctx.default_locale_key && variantKey === ctx.default_locale_key ? variantKey : "")
        || variantKey;
      groups.set(groupKey, {
        productLabel: meta.productLabel,
        tags: meta.tags,
        sourcePrompt: meta.sourcePrompt,
        variants: [{ localeKey: variantKey, row }],
        row,
        selectedLocaleKey: preferred,
      });
      continue;
    }
    if (!existing.variants.some((v) => v.localeKey === variantKey)) {
      existing.variants.push({ localeKey: variantKey, row });
    }
    // Prefer tags from the first non-empty set.
    if (!existing.tags.length && meta.tags.length) existing.tags = meta.tags;
  }

  return Array.from(groups.values()).map((entry) => {
    const preferredKey =
      selectedLocales[`${norm(entry.productLabel)}\u0000${norm(entry.sourcePrompt)}`]
      || entry.selectedLocaleKey;
    const selected =
      entry.variants.find((v) => v.localeKey === preferredKey)
      || entry.variants.find((v) => v.localeKey === ctx.default_locale_key)
      || entry.variants[0];
    return {
      ...entry,
      selectedLocaleKey: selected.localeKey,
      row: selected.row,
    };
  });
}

/** One prompt row under a Market:Language group (no per-prompt market dropdown). */
export type TopicMarketPrompt = {
  productLabel: string;
  tags: string[];
  sourcePrompt: string;
  localeKey: string;
  row: LiveProbePerPrompt;
};

export type TopicMarketGroup = {
  localeKey: string;
  prompts: TopicMarketPrompt[];
};

/**
 * Topic → Market:Language → Prompt tree for Overall (and single-locale) views.
 * Overall keeps every locale as its own market group so the UI can nest markets
 * under topics instead of rendering a Market dropdown on every prompt row.
 */
export type TopicMarketNode = {
  productLabel: string;
  markets: TopicMarketGroup[];
  /** All probe rows under this topic (for topic summary metrics). */
  allRows: LiveProbePerPrompt[];
  /** Distinct logical prompts (source text) under this topic. */
  promptCount: number;
};

function localeSortKey(
  localeKey: string,
  defaultLocaleKey: string | undefined,
): string {
  if (defaultLocaleKey && localeKey === defaultLocaleKey) return `\u0000${localeKey}`;
  return localeKey;
}

export function buildTopicMarketPromptTree(
  live: { per_prompt?: LiveProbePerPrompt[] | null } | null | undefined,
  ctx: PromptPerformanceContext,
  options: { localeKey: string },
): TopicMarketNode[] {
  const { localeKey } = options;
  const annotated = annotateProbeRowsWithCategory(live, ctx);
  const isOverall = !localeKey || localeKey === OVERALL_LOCALE_KEY;
  const byTopic = new Map<string, Array<{ row: LiveProbePerPrompt; meta: PromptRowMeta }>>();

  for (const item of annotated) {
    const topic = item.meta.productLabel.trim() || "Other";
    if (!byTopic.has(topic)) byTopic.set(topic, []);
    byTopic.get(topic)!.push(item);
  }

  const topicOrder = Array.from(
    new Set([
      ...ctx.category_labels.map((label) => label.trim()).filter(Boolean),
      ...byTopic.keys(),
    ]),
  );

  const nodes: TopicMarketNode[] = [];
  for (const topic of topicOrder) {
    const items = byTopic.get(topic);
    if (!items?.length) continue;

    const marketsMap = new Map<string, TopicMarketPrompt[]>();
    const sourcePrompts = new Set<string>();

    for (const { row, meta } of items) {
      const marketKey = isOverall
        ? String(row._locale_key || ctx.default_locale_key || "default").trim()
        : (row._locale_key || localeKey);
      sourcePrompts.add(norm(meta.sourcePrompt) || norm(String(row.prompt ?? "")));
      if (!marketsMap.has(marketKey)) marketsMap.set(marketKey, []);
      marketsMap.get(marketKey)!.push({
        productLabel: meta.productLabel,
        tags: meta.tags,
        sourcePrompt: meta.sourcePrompt,
        localeKey: marketKey,
        row,
      });
    }

    const markets = Array.from(marketsMap.entries())
      .sort(([a], [b]) =>
        localeSortKey(a, ctx.default_locale_key).localeCompare(
          localeSortKey(b, ctx.default_locale_key),
        ))
      .map(([key, prompts]) => ({ localeKey: key, prompts }));

    nodes.push({
      productLabel: topic,
      markets,
      allRows: items.map((item) => item.row),
      promptCount: sourcePrompts.size || items.length,
    });
  }

  return nodes;
}

export function promptGroupKey(productLabel: string, sourcePrompt: string): string {
  return `${norm(productLabel)}\u0000${norm(sourcePrompt)}`;
}

export function topicMarketKey(productLabel: string, localeKey: string): string {
  return `${norm(productLabel)}\u0000${localeKey.trim()}`;
}
