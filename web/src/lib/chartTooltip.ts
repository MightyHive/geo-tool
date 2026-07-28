/**
 * Shared helpers so multi-series Recharts tooltips list rows high → low by metric value.
 */

export type TooltipSortableItem = {
  value?: unknown;
};

/** Coerce a tooltip payload value to a finite number, or null if missing/non-numeric. */
export function tooltipItemNumericValue(value: unknown): number | null {
  if (value == null) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string" && value.trim() !== "") {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }
  if (Array.isArray(value) && value.length > 0) {
    return tooltipItemNumericValue(value[0]);
  }
  return null;
}

/**
 * Sort key for Recharts `Tooltip` `itemSorter` (lodash-style ascending `sortBy`).
 * Negated value → descending display order; null/undefined → +Infinity (last).
 */
export function tooltipItemSortKey(item: TooltipSortableItem): number {
  const n = tooltipItemNumericValue(item.value);
  if (n == null) return Number.POSITIVE_INFINITY;
  return -n;
}

/** Recharts `itemSorter` for default tooltip content: value descending, nulls last. */
export const tooltipItemSorterByValueDesc = tooltipItemSortKey;

/**
 * Sort a custom-tooltip payload by numeric value descending; null/undefined last.
 * Preserves item identity (colors, names, dataKeys) — only reorders rows.
 */
export function sortTooltipItemsByValueDesc<T extends TooltipSortableItem>(
  items: readonly T[],
): T[] {
  return [...items].sort((a, b) => {
    const av = tooltipItemNumericValue(a.value);
    const bv = tooltipItemNumericValue(b.value);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  });
}
