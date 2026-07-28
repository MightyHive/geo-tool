/**
 * Shared helpers so multi-series Recharts legends list rows high → low by metric value,
 * matching tooltip order (see chartTooltip.ts). For time series, the sort key is the
 * latest non-null point for each series dataKey.
 */

import { tooltipItemNumericValue } from "./chartTooltip";

/** Compatible with Recharts `LegendPayload` / `DataKey` (string | number | accessor). */
export type LegendSortableItem = {
  dataKey?: string | number | ((obj: unknown) => unknown);
};

function seriesDataKey(dataKey: LegendSortableItem["dataKey"]): string | null {
  if (typeof dataKey === "string" || typeof dataKey === "number") {
    return String(dataKey);
  }
  return null;
}

/**
 * Walk rows from the end (latest date first) and return the first finite numeric
 * value for `dataKey`, or null if none.
 */
export function latestSeriesValue(
  rows: readonly Record<string, unknown>[],
  dataKey: string,
): number | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    const n = tooltipItemNumericValue(rows[i]?.[dataKey]);
    if (n != null) return n;
  }
  return null;
}

/**
 * Sort key for Recharts `Legend` `itemSorter` (lodash-style ascending `sortBy`).
 * Negated latest value → descending display order; null/undefined → +Infinity (last).
 */
export function legendItemSortKeyByLatestValue(
  rows: readonly Record<string, unknown>[],
  item: LegendSortableItem,
): number {
  const key = seriesDataKey(item.dataKey);
  if (!key) return Number.POSITIVE_INFINITY;
  const n = latestSeriesValue(rows, key);
  if (n == null) return Number.POSITIVE_INFINITY;
  return -n;
}

/**
 * Factory for Recharts `Legend` `itemSorter`: latest metric value descending, nulls last.
 *
 * @example
 *   <Legend itemSorter={legendItemSorterByLatestValueDesc(chartRows)} />
 */
export function legendItemSorterByLatestValueDesc(
  rows: readonly Record<string, unknown>[],
): (item: LegendSortableItem) => number {
  return (item) => legendItemSortKeyByLatestValue(rows, item);
}

/**
 * Sort items that expose a `dataKey` by latest chart-row value descending.
 * Useful for custom (non-Recharts) legends.
 */
export function sortByLatestSeriesValueDesc<T extends { dataKey: string }>(
  items: readonly T[],
  rows: readonly Record<string, unknown>[],
): T[] {
  return [...items].sort((a, b) => {
    const av = latestSeriesValue(rows, a.dataKey);
    const bv = latestSeriesValue(rows, b.dataKey);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av;
  });
}
