/** Rough wall-clock ETA for prompt probe runs.
 *
 * Markets run in parallel → do not multiply by market count.
 * Observed average ≈ 7 seconds per platform call.
 */

export const SECONDS_PER_PLATFORM_CALL = 7;

/** Default runs per prompt when deriving calls before progress events arrive. */
export const DEFAULT_PROBE_RUNS = 3;

/** Typical enabled platforms when live probe config is unknown. */
export const DEFAULT_PROBE_PLATFORM_COUNT = 4;

export function estimateProbeRunMinutes(platformCalls: number): number {
  const calls = Math.max(0, Math.floor(platformCalls || 0));
  if (calls <= 0) return 0;
  return Math.max(1, Math.ceil((calls * SECONDS_PER_PLATFORM_CALL) / 60));
}

export function estimateProbeRunSeconds(platformCalls: number): number {
  const calls = Math.max(0, Math.floor(platformCalls || 0));
  return calls * SECONDS_PER_PLATFORM_CALL;
}

/** Derive platform calls for one market when progress has not reported planned_calls yet. */
export function derivePlatformCallsPerMarket(opts: {
  promptCount: number;
  platformCount?: number;
  runsPerPrompt?: number;
}): number {
  const prompts = Math.max(0, Math.floor(opts.promptCount || 0));
  const platforms = Math.max(
    1,
    Math.floor(opts.platformCount ?? DEFAULT_PROBE_PLATFORM_COUNT),
  );
  const runs = Math.max(1, Math.floor(opts.runsPerPrompt ?? DEFAULT_PROBE_RUNS));
  return prompts * platforms * runs;
}

/**
 * Format ETA copy for progress UI.
 * Prefer remaining when progress is known; otherwise total estimate.
 */
export function formatProbeRunEta(opts: {
  remainingCalls?: number | null;
  totalCalls?: number | null;
}): string | null {
  const totalCalls = opts.totalCalls ?? 0;
  const remainingCalls = opts.remainingCalls;
  // Explicit zero remaining with a known total → finished; do not show ETA.
  if (remainingCalls != null && remainingCalls <= 0 && totalCalls > 0) {
    return null;
  }

  const remaining =
    remainingCalls != null && remainingCalls > 0
      ? estimateProbeRunMinutes(remainingCalls)
      : 0;
  const total =
    totalCalls > 0 ? estimateProbeRunMinutes(totalCalls) : 0;

  const done =
    totalCalls > 0 && remainingCalls != null && remainingCalls >= 0
      ? Math.max(0, totalCalls - remainingCalls)
      : 0;

  // Progress underway → remaining copy; otherwise total estimate.
  if (remaining > 0 && done > 0) {
    return `About ${remaining} minute${remaining === 1 ? "" : "s"} remaining`;
  }
  if (total > 0) {
    return `Estimated time: ~${total} minute${total === 1 ? "" : "s"}`;
  }
  if (remaining > 0) {
    return `About ${remaining} minute${remaining === 1 ? "" : "s"} remaining`;
  }
  return null;
}
