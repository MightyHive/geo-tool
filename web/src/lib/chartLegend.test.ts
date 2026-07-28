import { describe, expect, it } from "vitest";
import {
  latestSeriesValue,
  legendItemSortKeyByLatestValue,
  legendItemSorterByLatestValueDesc,
  sortByLatestSeriesValueDesc,
} from "./chartLegend";

const rows = [
  { date: "2026-01-01", brand: 40, rival: 80, missing: null },
  { date: "2026-01-02", brand: 70, rival: 50, missing: null },
  { date: "2026-01-03", brand: 60, rival: null, missing: null },
];

describe("latestSeriesValue", () => {
  it("returns the latest non-null numeric value for a dataKey", () => {
    expect(latestSeriesValue(rows, "brand")).toBe(60);
    expect(latestSeriesValue(rows, "rival")).toBe(50);
  });

  it("skips trailing nulls and uses the prior finite value", () => {
    expect(
      latestSeriesValue(
        [
          { a: 1 },
          { a: null },
          { a: undefined },
        ],
        "a",
      ),
    ).toBe(1);
  });

  it("returns null when every point is missing", () => {
    expect(latestSeriesValue(rows, "missing")).toBeNull();
    expect(latestSeriesValue(rows, "unknown")).toBeNull();
    expect(latestSeriesValue([], "brand")).toBeNull();
  });
});

describe("legendItemSortKeyByLatestValue", () => {
  it("orders higher latest values first via ascending sortBy (negated keys)", () => {
    const items = [
      { dataKey: "brand", value: "Brand" },
      { dataKey: "rival", value: "Rival" },
    ];
    const sorted = [...items].sort(
      (a, b) =>
        legendItemSortKeyByLatestValue(rows, a) -
        legendItemSortKeyByLatestValue(rows, b),
    );
    expect(sorted.map((i) => i.dataKey)).toEqual(["brand", "rival"]);
  });

  it("puts missing dataKeys / all-null series last", () => {
    const items = [
      { dataKey: "missing" },
      { dataKey: "brand" },
      { dataKey: undefined },
    ];
    const sorted = [...items].sort(
      (a, b) =>
        legendItemSortKeyByLatestValue(rows, a) -
        legendItemSortKeyByLatestValue(rows, b),
    );
    expect(sorted[0]?.dataKey).toBe("brand");
  });
});

describe("legendItemSorterByLatestValueDesc", () => {
  it("returns a Recharts-compatible itemSorter bound to chart rows", () => {
    const sorter = legendItemSorterByLatestValueDesc(rows);
    const items = [{ dataKey: "rival" }, { dataKey: "brand" }];
    const sorted = [...items].sort((a, b) => sorter(a) - sorter(b));
    expect(sorted.map((i) => i.dataKey)).toEqual(["brand", "rival"]);
  });
});

describe("sortByLatestSeriesValueDesc", () => {
  it("sorts by latest value descending and keeps item identity", () => {
    const items = [
      { dataKey: "rival", name: "Rival", color: "#r" },
      { dataKey: "brand", name: "Brand", color: "#b" },
      { dataKey: "missing", name: "Gone", color: "#g" },
    ];
    const sorted = sortByLatestSeriesValueDesc(items, rows);
    expect(sorted.map((i) => i.name)).toEqual(["Brand", "Rival", "Gone"]);
    expect(sorted[0]).toEqual({ dataKey: "brand", name: "Brand", color: "#b" });
  });
});
