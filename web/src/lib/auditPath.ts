/** Folder name under ``audit_output/`` (safe for React Router single-segment paths). */
export function auditSlug(auditDirOrId: string): string {
  const normalized = auditDirOrId.trim().replace(/\\/g, "/");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? normalized;
}

/** Bundled demo report shipped on dev/staging (GCS audit_output). */
export const BUNDLED_SAMPLE_AUDIT_SLUG = "starbucks.co.uk_d6a4f5ac1a37";
