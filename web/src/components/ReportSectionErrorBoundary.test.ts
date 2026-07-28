import { describe, expect, it } from "vitest";
import { ReportSectionErrorBoundary } from "./ReportSectionErrorBoundary";

describe("ReportSectionErrorBoundary", () => {
  it("captures error message for recovery UI", () => {
    const state = ReportSectionErrorBoundary.getDerivedStateFromError(
      new Error("citations boom"),
    );
    expect(state.hasError).toBe(true);
    expect(state.message).toContain("citations boom");
  });

  it("handles non-Error throws", () => {
    const state = ReportSectionErrorBoundary.getDerivedStateFromError("string fail");
    expect(state.hasError).toBe(true);
    expect(state.message).toBe("string fail");
  });
});
