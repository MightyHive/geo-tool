import { describe, expect, it } from "vitest";
import {
  formatReportScore,
  GOOD_SCORE_MIN,
  isOkOrBelow,
  roundReportScore,
  scoreLabel,
  scoreTone,
} from "./reportScore";

describe("roundReportScore / formatReportScore", () => {
  it("rounds to nearest integer (half away from zero for positive scores)", () => {
    expect(roundReportScore(60.0)).toBe(60);
    expect(roundReportScore(60.9)).toBe(61);
    expect(roundReportScore(60.5)).toBe(61);
    expect(roundReportScore(74.4)).toBe(74);
    expect(roundReportScore(74.6)).toBe(75);
  });

  it("formats as integer strings", () => {
    expect(formatReportScore(60.0)).toBe("60");
    expect(formatReportScore(60.9)).toBe("61");
    expect(formatReportScore(72.5)).toBe("73");
  });

  it("returns an em dash for non-finite values", () => {
    expect(formatReportScore(Number.NaN)).toBe("—");
    expect(formatReportScore(Number.POSITIVE_INFINITY)).toBe("—");
  });
});

describe("score banding on rounded display integer", () => {
  it("bands on the rounded value so 74.6 is Good", () => {
    expect(scoreLabel(74.6)).toBe("Good");
    expect(scoreTone(74.6)).toBe("green");
    expect(isOkOrBelow(74.6)).toBe(false);
  });

  it("keeps 74.4 as OK", () => {
    expect(scoreLabel(74.4)).toBe("OK");
    expect(scoreTone(74.4)).toBe("blue");
    expect(isOkOrBelow(74.4)).toBe(true);
  });

  it("maps band boundaries after rounding", () => {
    expect(scoreLabel(89.5)).toBe("Excellent");
    expect(scoreLabel(74.5)).toBe("Good");
    expect(scoreLabel(59.5)).toBe("OK");
    expect(scoreLabel(39.5)).toBe("Weak");
    expect(scoreLabel(39.4)).toBe("Poor");
  });

  it("treats exact GOOD_SCORE_MIN as not OK-or-below", () => {
    expect(isOkOrBelow(GOOD_SCORE_MIN - 1)).toBe(true);
    expect(isOkOrBelow(GOOD_SCORE_MIN)).toBe(false);
  });
});
