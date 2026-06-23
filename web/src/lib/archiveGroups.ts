import type { ArchiveRun } from "../types";

export interface SiteAuditGroup {
  siteKey: string;
  primaryUrl: string;
  brandName: string;
  latest: ArchiveRun;
  previous: ArchiveRun[];
}

export function displayArchiveScore(score: number | null | undefined): string {
  if (score == null || Number.isNaN(Number(score))) return "—";
  const n = Number(score);
  if (n <= 0) return "—";
  return n.toFixed(1);
}

export function scoreTone(score: number | null | undefined): string {
  const n = Number(score);
  if (!Number.isFinite(n) || n <= 0) return "text-gray-500";
  if (n >= 75) return "text-emerald-600";
  if (n >= 60) return "text-blue-600";
  if (n >= 40) return "text-amber-600";
  return "text-orange-600";
}

export function groupArchiveRunsBySite(runs: ArchiveRun[]): SiteAuditGroup[] {
  const bySite = new Map<string, ArchiveRun[]>();
  for (const run of runs) {
    const key = (run.site_key || run.primary_url || run.id).trim().toLowerCase();
    const list = bySite.get(key) ?? [];
    list.push(run);
    bySite.set(key, list);
  }

  const groups: SiteAuditGroup[] = [];
  for (const [siteKey, list] of bySite) {
    const sorted = [...list].sort((a, b) =>
      (b.created_at || "").localeCompare(a.created_at || ""),
    );
    const latest = sorted[0];
    groups.push({
      siteKey,
      primaryUrl: latest.primary_url,
      brandName: latest.brand_name?.trim() || siteKey,
      latest,
      previous: sorted.slice(1),
    });
  }

  groups.sort((a, b) =>
    (b.latest.created_at || "").localeCompare(a.latest.created_at || ""),
  );
  return groups;
}
