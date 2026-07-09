import type { OnboardingContext } from "../types";

interface ConfigSectionProps {
  config: OnboardingContext | null | undefined;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  if (!value) return null;
  return (
    <div className="grid grid-cols-[160px_1fr] gap-4 py-3 border-b border-gray-100 last:border-0">
      <dt className="text-xs font-semibold uppercase tracking-wide text-gray-400 pt-0.5">
        {label}
      </dt>
      <dd className="text-sm text-[#0d0d0d] leading-relaxed">{value}</dd>
    </div>
  );
}

export function ConfigSection({ config }: ConfigSectionProps) {
  if (!config) {
    return (
      <div className="max-w-2xl mx-auto py-12 text-center text-gray-400 text-sm">
        No configuration data found for this audit.
      </div>
    );
  }

  const products = config.products_and_services ?? [];
  const competitors = config.competitors_detail ?? [];
  const crawlUrls = config.crawl_urls ?? [];

  return (
    <div className="max-w-2xl mx-auto">
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-[#0d0d0d] mb-1">Audit configuration</h2>
        <p className="text-sm text-gray-500">
          Settings used when this audit was run. To change these, run a new audit.
        </p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 divide-y divide-gray-100 overflow-hidden mb-6">
        <div className="px-6 py-3 bg-gray-50">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">Brand</h3>
        </div>
        <dl className="px-6">
          <Row label="Brand name" value={config.brand_name_used} />
          <Row
            label="Website"
            value={
              config.brand_website_used ? (
                <a
                  href={config.brand_website_used}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-brand-accent hover:underline break-all"
                >
                  {config.brand_website_used}
                </a>
              ) : null
            }
          />
          <Row label="Industry" value={config.industry_used} />
          <Row
            label="Market"
            value={
              config.geo_market_country
                ? `${config.geo_market_country}${config.geo_market_country_code ? ` (${config.geo_market_country_code})` : ""}`
                : null
            }
          />
          {config.ga4_property_id && (
            <Row label="GA4 property" value={config.ga4_property_id} />
          )}
        </dl>
      </div>

      {products.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mb-6">
          <div className="px-6 py-3 bg-gray-50">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Products &amp; services ({products.length})
            </h3>
          </div>
          <ul className="divide-y divide-gray-100">
            {products.map((p, i) => (
              <li key={i} className="px-6 py-3 text-sm text-[#0d0d0d]">
                {p}
              </li>
            ))}
          </ul>
        </div>
      )}

      {competitors.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mb-6">
          <div className="px-6 py-3 bg-gray-50">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Competitors ({competitors.length})
            </h3>
          </div>
          <ul className="divide-y divide-gray-100">
            {competitors.map((c, i) => (
              <li key={i} className="px-6 py-3 flex items-center gap-3">
                <img
                  src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(c.competitor_website)}&sz=32`}
                  alt=""
                  width={20}
                  height={20}
                  className="rounded shrink-0"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = "none";
                  }}
                />
                <span className="text-sm font-medium text-[#0d0d0d] min-w-[8rem]">
                  {c.competitor_brand || "—"}
                </span>
                <a
                  href={c.competitor_website}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-gray-400 hover:text-brand-accent hover:underline break-all"
                >
                  {c.competitor_website}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {crawlUrls.length > 0 && (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mb-6">
          <div className="px-6 py-3 bg-gray-50">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-gray-500">
              Crawled pages ({crawlUrls.length})
            </h3>
          </div>
          <ul className="divide-y divide-gray-100 max-h-64 overflow-y-auto">
            {crawlUrls.map((u, i) => (
              <li key={i} className="px-6 py-2">
                <a
                  href={u}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-gray-500 hover:text-brand-accent hover:underline break-all"
                >
                  {u}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
