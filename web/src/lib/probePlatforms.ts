import type { LiveProbeResult } from "../types";

export type ProbePlatform = "gemini" | "openai" | "claude";

export const ALL_PROBE_PLATFORMS: ProbePlatform[] = ["gemini", "openai", "claude"];

const PLATFORM_LABELS: Record<ProbePlatform, string> = {
  gemini: "Gemini",
  openai: "OpenAI",
  claude: "Claude",
};

export function activeProbePlatforms(live: LiveProbeResult | null | undefined): ProbePlatform[] {
  if (live?.active_platforms?.length) {
    return live.active_platforms.filter((p): p is ProbePlatform =>
      ALL_PROBE_PLATFORMS.includes(p as ProbePlatform),
    );
  }
  const excluded = new Set((live?.excluded_platforms ?? []).map((p) => p.toLowerCase()));
  return ALL_PROBE_PLATFORMS.filter((p) => !excluded.has(p));
}

export function isProbePlatformActive(
  platform: ProbePlatform,
  live: LiveProbeResult | null | undefined,
): boolean {
  return activeProbePlatforms(live).includes(platform);
}

export function probePlatformsLabel(platforms: ProbePlatform[]): string {
  return platforms.map((p) => PLATFORM_LABELS[p]).join(" + ");
}

export function rowHasPlatformData(
  row: Record<string, unknown>,
  platform: ProbePlatform,
  live: LiveProbeResult | null | undefined,
): boolean {
  if (!isProbePlatformActive(platform, live)) return false;
  const resp = row[`${platform}_response`];
  const err = row[`error_${platform}`];
  return Boolean(resp || err);
}
