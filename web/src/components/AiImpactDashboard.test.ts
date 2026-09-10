import { describe, expect, it } from "vitest";

import {
  aiImpactConfigPresentation,
  aiImpactResultLabel,
  aiImpactRefitStatus,
  estimateWindowDayCount,
  estimatedImpactShareOfTotal,
  estimatedSessionsPerDay,
  hydrateAiImpactRunFromSaved,
  isRobustSensitivity,
  shouldShowAwaitingSignalWeeks,
  stripSavedEstimateRunId,
} from "./AiImpactDashboard";


describe("AI impact hierarchical presentation", () => {
  it("distinguishes category scoring from a site refit", () => {
    expect(aiImpactResultLabel("category_posterior")).toContain("Category-level");
    expect(aiImpactResultLabel("site_refit")).toContain("Site-inclusive");
  });

  it("only calls capped and uncapped results robust when intervals agree", () => {
    expect(
      isRobustSensitivity({
        uncapped: { posterior_mean: 100, lower_94: 20, upper_94: 180 },
        capped: { posterior_mean: 80, lower_94: 10, upper_94: 150 },
      }),
    ).toBe(true);
    expect(
      isRobustSensitivity({
        uncapped: { posterior_mean: 100, lower_94: 20, upper_94: 180 },
        capped: { posterior_mean: 10, lower_94: -40, upper_94: 60 },
      }),
    ).toBe(false);
  });

  it("surfaces asynchronous refit status", () => {
    expect(
      aiImpactRefitStatus({
        run_id: "run",
        status: "completed",
        created_at: "2026-08-11",
        jobs: { refit: "queued" },
        hierarchical_refit: { status: "running" },
      }),
    ).toBe("running");
  });

  it("hides portfolio-refresh lag after a completed site refit", () => {
    expect(shouldShowAwaitingSignalWeeks(3, "category_posterior", "running")).toBe(true);
    expect(shouldShowAwaitingSignalWeeks(3, "category_posterior", "completed")).toBe(false);
    expect(shouldShowAwaitingSignalWeeks(3, "site_refit", "completed")).toBe(false);
    expect(shouldShowAwaitingSignalWeeks(0, "category_posterior", "queued")).toBe(false);
  });
});

describe("AI impact saved estimate hydrate", () => {
  const saved = {
    window_start: "2026-01-01",
    window_end: "2026-04-01",
    category: "publisher",
    estimate_mode: "category_posterior",
    model_artifact_version: "model-v1",
    signal_artifact_version: "signal-v1",
    direct_ai_sessions: 10,
    direct_ai_purchases: 1,
    total_sessions: 100,
    total_purchases: 5,
    site_cvr: 0.05,
    ai_cvr: 0.1,
    _run_id: "run-9",
  };

  it("strips the stored run id from the estimate payload", () => {
    expect(stripSavedEstimateRunId(saved)).toEqual({
      estimate: {
        window_start: "2026-01-01",
        window_end: "2026-04-01",
        category: "publisher",
        estimate_mode: "category_posterior",
        model_artifact_version: "model-v1",
        signal_artifact_version: "signal-v1",
        direct_ai_sessions: 10,
        direct_ai_purchases: 1,
        total_sessions: 100,
        total_purchases: 5,
        site_cvr: 0.05,
        ai_cvr: 0.1,
      },
      runId: "run-9",
    });
  });

  it("prefers a live run that still has an estimate", () => {
    const live = {
      run_id: "run-9",
      status: "completed",
      created_at: "2026-08-01",
      jobs: { trends: "completed" },
      estimate: {
        window_start: "2026-02-01",
        window_end: "2026-05-01",
        direct_ai_sessions: 20,
        direct_ai_purchases: 2,
        total_sessions: 200,
        total_purchases: 10,
        site_cvr: 0.05,
        ai_cvr: 0.1,
      },
    };
    expect(hydrateAiImpactRunFromSaved(saved, live)).toBe(live);
  });

  it("builds a synthetic run when the stored run is gone", () => {
    const hydrated = hydrateAiImpactRunFromSaved(saved, null);
    expect(hydrated.run_id).toBe("run-9");
    expect(hydrated.status).toBe("completed");
    expect(hydrated.estimate).toMatchObject({
      window_start: "2026-01-01",
      category: "publisher",
    });
    expect(hydrated.estimate).not.toHaveProperty("_run_id");
    expect(hydrated.category).toBe("publisher");
    expect(hydrated.estimate_mode).toBe("category_posterior");
  });
});

describe("AI impact Config visibility", () => {
  it("does not flash Config while completed models hydrate", () => {
    expect(aiImpactConfigPresentation(false)).toBe("loading");
  });

  it("keeps Config available collapsed after hydrate, including when an estimate exists", () => {
    expect(aiImpactConfigPresentation(true)).toBe("collapsed");
  });
});

describe("AI impact per-day and share helpers", () => {
  it("counts window days from weekly series when present", () => {
    expect(
      estimateWindowDayCount({
        window_start: "2026-04-26",
        window_end: "2026-07-19",
        weekly_series: [
          { week: "2026-04-26", total_sessions: 1, ai_sessions: 0, seo_sessions: 1 },
          { week: "2026-05-03", total_sessions: 1, ai_sessions: 0, seo_sessions: 1 },
        ],
      }),
    ).toBe(14);
  });

  it("falls back to Sunday week-start span when weekly series is missing", () => {
    expect(
      estimateWindowDayCount({
        window_start: "2026-04-26",
        window_end: "2026-07-19",
      }),
    ).toBe(91);
  });

  it("converts window impact into average sessions per day", () => {
    expect(estimatedSessionsPerDay(910, 91)).toBe(10);
    expect(estimatedSessionsPerDay(-910, 91)).toBe(-10);
    expect(estimatedSessionsPerDay(100, 0)).toBeNaN();
  });

  it("expresses impact as a share of total sessions", () => {
    expect(estimatedImpactShareOfTotal(250, 1000)).toBeCloseTo(0.25);
    expect(estimatedImpactShareOfTotal(-50, 1000)).toBeCloseTo(-0.05);
    expect(estimatedImpactShareOfTotal(10, 0)).toBeNaN();
  });
});
