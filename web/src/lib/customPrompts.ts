import type { ProductServiceRow } from "../types";

/** Reserved product/service label for user-submitted prompts in the wizard and dashboard. */
export const CUSTOM_PROMPTS_LABEL = "Custom prompts";

export function isCustomPromptsCategory(label: string): boolean {
  return label.trim().toLowerCase() === CUSTOM_PROMPTS_LABEL.toLowerCase();
}

export function mergeCustomPromptsRow(
  rows: ProductServiceRow[],
  prompts: string[],
): ProductServiceRow[] {
  const rest = rows.filter((r) => !isCustomPromptsCategory(r.product_or_service));
  const cleaned = prompts.map((p) => p.trim()).filter(Boolean);
  if (!cleaned.length) return rest;
  return [...rest, { product_or_service: CUSTOM_PROMPTS_LABEL, prompts: cleaned }];
}

export function withoutCustomPromptsRow(rows: ProductServiceRow[]): ProductServiceRow[] {
  return rows.filter((r) => !isCustomPromptsCategory(r.product_or_service));
}

/** Drop Custom prompts from sentiment cards when none were probed. */
export function filterSentimentCategories<T extends { category: string }>(
  categories: T[],
  probedRows: ProductServiceRow[] | undefined,
): T[] {
  const rows = probedRows ?? [];
  const hasCustom = rows.some(
    (r) => isCustomPromptsCategory(r.product_or_service) && (r.prompts?.length ?? 0) > 0,
  );
  if (hasCustom) return categories;
  return categories.filter((c) => !isCustomPromptsCategory(c.category));
}
