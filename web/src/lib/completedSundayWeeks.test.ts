import { describe, expect, it } from "vitest";
import {
  filterCompletedWeeks,
  isCompletedSundayWeek,
  sundayWeekStart,
} from "./completedSundayWeeks";

describe("completedSundayWeeks", () => {
  it("labels weeks as Sunday starts", () => {
    expect(sundayWeekStart("2026-07-23")).toBe("2026-07-19"); // Thursday → prior Sunday
    expect(sundayWeekStart("2026-07-19")).toBe("2026-07-19");
    expect(sundayWeekStart("2026-07-25")).toBe("2026-07-19"); // Saturday
    expect(sundayWeekStart("2026-07-26")).toBe("2026-07-26"); // next Sunday
  });

  it("treats a week as complete only after its Saturday", () => {
    // Week of 2026-07-19 covers Sun Jul 19 – Sat Jul 25
    expect(isCompletedSundayWeek("2026-07-19", "2026-07-25")).toBe(false);
    expect(isCompletedSundayWeek("2026-07-19", "2026-07-26")).toBe(true);
    expect(isCompletedSundayWeek("2026-07-12", "2026-07-23")).toBe(true);
  });

  it("filters the in-progress current week from a series", () => {
    const series = [
      { week: "2026-07-05", total_sessions: 100 },
      { week: "2026-07-12", total_sessions: 110 },
      { week: "2026-07-19", total_sessions: 50 }, // in progress on Jul 23
    ];
    expect(filterCompletedWeeks(series, "2026-07-23").map((p) => p.week)).toEqual([
      "2026-07-05",
      "2026-07-12",
    ]);
  });
});
