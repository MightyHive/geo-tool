import { describe, expect, it } from "vitest";
import {
  DEFAULT_PROBE_PLATFORM_COUNT,
  DEFAULT_PROBE_RUNS,
  SECONDS_PER_PLATFORM_CALL,
  derivePlatformCallsPerMarket,
  estimateProbeRunMinutes,
  estimateProbeRunSeconds,
  formatProbeRunEta,
} from "./probeRunEta";

describe("probeRunEta", () => {
  it("uses 7 seconds per platform call", () => {
    expect(SECONDS_PER_PLATFORM_CALL).toBe(7);
  });

  it("estimates minutes with ceil((calls * 7) / 60) — markets not multiplied", () => {
    // 100 platform calls for 4 markets → still ~12 minutes (parallel markets)
    expect(estimateProbeRunMinutes(100)).toBe(12);
    expect(estimateProbeRunSeconds(100)).toBe(700);
  });

  it("returns 0 for empty / zero calls", () => {
    expect(estimateProbeRunMinutes(0)).toBe(0);
    expect(estimateProbeRunMinutes(-3)).toBe(0);
  });

  it("ceils partial minutes to at least 1", () => {
    expect(estimateProbeRunMinutes(1)).toBe(1); // 7s → 1 min
    expect(estimateProbeRunMinutes(9)).toBe(2); // 63s → 2 min
  });

  it("derives platform calls from prompts × platforms × runs", () => {
    expect(
      derivePlatformCallsPerMarket({
        promptCount: 10,
        platformCount: 4,
        runsPerPrompt: 3,
      }),
    ).toBe(120);
    expect(derivePlatformCallsPerMarket({ promptCount: 10 })).toBe(
      10 * DEFAULT_PROBE_PLATFORM_COUNT * DEFAULT_PROBE_RUNS,
    );
  });

  it("formats remaining vs total copy", () => {
    expect(formatProbeRunEta({ totalCalls: 100 })).toBe("Estimated time: ~12 minutes");
    expect(formatProbeRunEta({ remainingCalls: 100, totalCalls: 100 })).toBe(
      "Estimated time: ~12 minutes",
    );
    expect(formatProbeRunEta({ remainingCalls: 50, totalCalls: 100 })).toBe(
      "About 6 minutes remaining",
    );
    expect(formatProbeRunEta({ remainingCalls: 0, totalCalls: 100 })).toBeNull();
    expect(formatProbeRunEta({})).toBeNull();
  });
});