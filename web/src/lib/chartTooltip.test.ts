import { describe, expect, it } from "vitest";
import {
  sortTooltipItemsByValueDesc,
  tooltipItemNumericValue,
  tooltipItemSortKey,
} from "./chartTooltip";

describe("tooltipItemNumericValue", () => {
  it("returns finite numbers and coerces numeric strings", () => {
    expect(tooltipItemNumericValue(42)).toBe(42);
    expect(tooltipItemNumericValue("12.5")).toBe(12.5);
    expect(tooltipItemNumericValue([7, 1])).toBe(7);
  });

  it("returns null for missing or non-numeric values", () => {
    expect(tooltipItemNumericValue(null)).toBeNull();
    expect(tooltipItemNumericValue(undefined)).toBeNull();
    expect(tooltipItemNumericValue("")).toBeNull();
    expect(tooltipItemNumericValue("abc")).toBeNull();
    expect(tooltipItemNumericValue(Number.NaN)).toBeNull();
    expect(tooltipItemNumericValue([])).toBeNull();
  });
});

describe("tooltipItemSortKey", () => {
  it("orders higher values first via ascending sortBy (negated keys)", () => {
    const items = [{ value: 10 }, { value: 50 }, { value: 20 }];
    const sorted = [...items].sort(
      (a, b) => tooltipItemSortKey(a) - tooltipItemSortKey(b),
    );
    expect(sorted.map((i) => i.value)).toEqual([50, 20, 10]);
  });

  it("puts null/undefined last", () => {
    const items = [{ value: undefined }, { value: 5 }, { value: null }];
    const sorted = [...items].sort(
      (a, b) => tooltipItemSortKey(a) - tooltipItemSortKey(b),
    );
    expect(sorted.map((i) => i.value)).toEqual([5, undefined, null]);
  });
});

describe("sortTooltipItemsByValueDesc", () => {
  it("sorts by value descending and keeps colors/names", () => {
    const items = [
      { name: "B", value: 30, color: "#bbb" },
      { name: "A", value: 90, color: "#aaa" },
      { name: "C", value: 10, color: "#ccc" },
    ];
    const sorted = sortTooltipItemsByValueDesc(items);
    expect(sorted.map((i) => i.name)).toEqual(["A", "B", "C"]);
    expect(sorted[0]).toEqual({ name: "A", value: 90, color: "#aaa" });
  });

  it("places nullish values last", () => {
    const sorted = sortTooltipItemsByValueDesc([
      { name: "missing", value: undefined },
      { name: "low", value: 2 },
      { name: "nullish", value: null },
      { name: "high", value: 9 },
    ]);
    expect(sorted.map((i) => i.name)).toEqual([
      "high",
      "low",
      "missing",
      "nullish",
    ]);
  });
});
