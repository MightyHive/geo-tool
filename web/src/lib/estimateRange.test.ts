import { describe, expect, it } from "vitest";
import {
  formatCompactEstimate,
  formatIndirectSessionsRange,
  probabilityOfResultPercent,
} from "./estimateRange";

describe("formatIndirectSessionsRange", () => {
  it("formats ±10% around the central estimate with compact significant figures", () => {
    // 7.7k × 0.9 … 7.7k × 1.1 → 6.9k – 8.5k
    expect(formatIndirectSessionsRange(7700)).toBe("6.9k – 8.5k");
  });

  it("handles zero and non-finite values", () => {
    expect(formatIndirectSessionsRange(0)).toBe(formatCompactEstimate(0));
    expect(formatIndirectSessionsRange(Number.NaN)).toBe("—");
  });

  it("orders bounds correctly for negative centrals", () => {
    expect(formatIndirectSessionsRange(-7700)).toBe("-8.5k – -6.9k");
  });
});

describe("probabilityOfResultPercent", () => {
  it("maps p-value to (1 − p) × 100", () => {
    expect(probabilityOfResultPercent(0.3)).toBe(70);
    expect(probabilityOfResultPercent(0)).toBe(100);
    expect(probabilityOfResultPercent(1)).toBe(0);
  });

  it("returns null for missing values", () => {
    expect(probabilityOfResultPercent(null)).toBeNull();
    expect(probabilityOfResultPercent(undefined)).toBeNull();
    expect(probabilityOfResultPercent(Number.NaN)).toBeNull();
  });
});
