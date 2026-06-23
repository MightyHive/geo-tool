import { Calendar, Eye } from "lucide-react";
import type { LocalAudit } from "../types";

function auditDisplayName(audit: LocalAudit): string {
  const brand = audit.brand_name?.trim();
  if (brand) return brand;
  const raw = audit.base_url?.trim();
  if (!raw) return audit.id;
  try {
    const url = raw.startsWith("http") ? raw : `https://${raw}`;
    const host = new URL(url).hostname.replace(/^www\./, "");
    const slug = host.split(".")[0] || host;
    return slug.replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  } catch {
    return raw;
  }
}

export function AuditListCard({
  audit,
  index,
  onOpen,
}: {
  audit: LocalAudit;
  index: number;
  onOpen: () => void;
}) {
  const label = auditDisplayName(audit);
  const favicon = audit.favicon_url?.trim();

  return (
    <button
      type="button"
      onClick={onOpen}
      className="card-surface p-5 text-left hover:scale-[1.01] transition-transform animate-slide-up w-full"
      style={{ animationDelay: `${index * 50}ms` }}
    >
      <div className="flex items-center gap-2 mb-1 min-w-0">
        <h3 className="font-semibold text-gray-900 truncate flex-1 min-w-0">{label}</h3>
        {favicon ? (
          <img
            src={favicon}
            alt=""
            width={22}
            height={22}
            className="rounded shrink-0"
            loading="lazy"
          />
        ) : (
          <span
            className="inline-flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded bg-gray-200 text-[10px] text-gray-500"
            aria-hidden
          >
            ?
          </span>
        )}
      </div>
      {audit.overall_score != null && (
        <p className="text-2xl font-bold text-brand-accent mb-2">
          {audit.overall_score}
          <span className="text-sm font-normal text-gray-500"> / 100</span>
        </p>
      )}
      <div className="flex items-center text-sm text-gray-500">
        <Calendar className="w-4 h-4 mr-2" />
        {audit.modified_at.slice(0, 10)}
      </div>
      <span className="mt-4 inline-flex items-center text-sm text-blue-600 font-medium">
        <Eye className="w-4 h-4 mr-1" />
        View report
      </span>
    </button>
  );
}
