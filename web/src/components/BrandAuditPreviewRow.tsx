interface BrandAuditPreviewRowProps {
  faviconUrl: string;
  brandName: string;
  websiteUrl: string;
}

export function BrandAuditPreviewRow({
  faviconUrl,
  brandName,
  websiteUrl,
}: BrandAuditPreviewRowProps) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-gray-200 bg-gray-50/80 px-4 py-3">
      {faviconUrl ? (
        <img
          src={faviconUrl}
          alt=""
          width={28}
          height={28}
          className="rounded shrink-0"
          loading="lazy"
        />
      ) : (
        <span
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded bg-gray-200 text-xs text-gray-500"
          aria-hidden
        >
          ?
        </span>
      )}
      <strong className="text-brand-dark">{brandName}</strong>
      <span className="text-sm text-gray-600 select-all">{websiteUrl}</span>
    </div>
  );
}
