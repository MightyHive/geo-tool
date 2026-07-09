import { useCallback, useEffect, useMemo, useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { fetchPromptPerformanceContext } from "../api/client";
import type {
  AioProbeResult,
  PromptPerformanceContext,
  TopCitedSite,
  TopCitedUrl,
} from "../types";
import { Card, CardDescription, CardTitle } from "./ui/Card";

// ── Constants ─────────────────────────────────────────────────────────────────

const PLATFORM_GEMINI = "#4285F4";
const PLATFORM_OPENAI = "#10a37f";
const PLATFORM_CLAUDE = "#D97706";
const PLATFORM_AIO = "#EA4335";

const PLATFORM_META: Record<string, { label: string; color: string }> = {
  gemini: { label: "Gemini", color: PLATFORM_GEMINI },
  openai: { label: "OpenAI", color: PLATFORM_OPENAI },
  claude: { label: "Claude", color: PLATFORM_CLAUDE },
  google_aio: { label: "Google AIO", color: PLATFORM_AIO },
};

// ── Small helpers ─────────────────────────────────────────────────────────────

const REDIRECT_HOST = "vertexaisearch.cloud.google.com";

function isRedirectUrl(url: string | undefined): boolean {
  return !!(url && url.includes(REDIRECT_HOST));
}

function FaviconImg({ domain }: { domain: string }) {
  const [failed, setFailed] = useState(false);
  if (domain.includes(REDIRECT_HOST) || failed)
    return <span className="w-4 h-4 rounded-sm bg-gray-200 inline-block shrink-0" />;
  return (
    <img
      src={`https://www.google.com/s2/favicons?domain=${domain}&sz=16`}
      alt=""
      width={16}
      height={16}
      className="rounded-sm shrink-0"
      onError={() => setFailed(true)}
    />
  );
}

/** Display label for a citation — prefers title over raw redirect domain. */
function citationDisplayDomain(domain: string, title?: string): string {
  if (domain.includes(REDIRECT_HOST)) {
    if (title) {
      // "Page Title - Site Name" → "Site Name"
      for (const sep of [" - ", " | ", " – ", " — "]) {
        if (title.includes(sep)) return title.split(sep).pop()!.trim();
      }
      return title.length > 40 ? title.slice(0, 40) + "…" : title;
    }
    return "Google-grounded source";
  }
  return domain;
}

function YesNo({ value, trueLabel = "Yes" }: { value: boolean; trueLabel?: string }) {
  return value
    ? <span className="font-semibold text-emerald-600">{trueLabel}</span>
    : <span className="text-gray-300">—</span>;
}

function PlatformChips({ platforms }: { platforms: string[] }) {
  return (
    <div className="flex flex-wrap gap-1">
      {platforms.map((p) => {
        const m = PLATFORM_META[p] ?? { label: p, color: "#6b7280" };
        return (
          <span
            key={p}
            className="inline-block text-[10px] font-semibold px-1.5 py-0.5 rounded-full"
            style={{ background: `${m.color}18`, color: m.color }}
          >
            {m.label}
          </span>
        );
      })}
    </div>
  );
}

function formatViews(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

// ── Domain-level table ────────────────────────────────────────────────────────

function TopDomainsTable({ sites }: { sites: TopCitedSite[] }) {
  if (!sites.length) return null;
  const maxCount = Math.max(...sites.map((s) => s.count), 1);

  return (
    <div className="mb-10">
      <h3 className="text-sm font-bold text-brand-dark mb-0.5">Top Domains</h3>
      <p className="text-xs text-gray-500 mb-4">Websites most commonly referenced in AI-generated answers.</p>
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5">Domain</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">Frequency</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">Brand mentioned</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden md:table-cell">Competitors mentioned</th>
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden lg:table-cell">AI platforms</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {sites.map((site, i) => {
              const barPct = Math.round((site.count / maxCount) * 100);
              return (
                <tr key={i} className="hover:bg-gray-50/60 transition-colors">
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2.5 min-w-0">
                      <FaviconImg domain={site.domain} />
                      <div className="min-w-0 flex-1">
                        <a
                          href={site.example_url ?? (isRedirectUrl(site.example_url) ? site.example_url : `https://${site.domain}`)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm font-semibold text-brand-dark hover:underline truncate block"
                          title={site.title}
                        >
                          {citationDisplayDomain(site.domain, site.title)}
                        </a>
                        {site.unresolved_redirect && (
                          <span className="text-[10px] text-amber-600">via Google search</span>
                        )}
                        <div className="mt-1 h-1 bg-gray-100 rounded-full overflow-hidden" style={{ maxWidth: 180 }}>
                          <div className="h-full rounded-full" style={{ width: `${barPct}%`, background: PLATFORM_GEMINI }} />
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="inline-block font-bold text-sm text-brand-dark">{site.count}</span>
                  </td>
                  <td className="px-4 py-3 text-center text-sm">
                    <YesNo value={site.brand_mentioned ?? false} />
                  </td>
                  <td className="px-4 py-3 text-center text-sm hidden md:table-cell">
                    {site.competitor_mentioned
                      ? (
                        <div>
                          <span className="font-semibold text-blue-600">Yes</span>
                          {site.competitor_names?.length ? (
                            <p className="text-[10px] text-gray-400 mt-0.5 truncate max-w-[140px]">
                              {site.competitor_names.slice(0, 3).join(", ")}
                            </p>
                          ) : null}
                        </div>
                      )
                      : <span className="text-gray-300">—</span>}
                  </td>
                  <td className="px-4 py-3 hidden lg:table-cell">
                    <PlatformChips platforms={site.platforms} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── URL-level table ───────────────────────────────────────────────────────────

function TopUrlsTable({ urls }: { urls: TopCitedUrl[] }) {
  if (!urls.length) return null;

  return (
    <div className="mb-6">
      <h3 className="text-sm font-bold text-brand-dark mb-0.5">Top URLs</h3>
      <p className="text-xs text-gray-500 mb-4">URLs most commonly referenced in AI-generated answers.</p>
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm">
        <table className="w-full">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5">URL</th>
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden md:table-cell">Content type</th>
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden md:table-cell">Channel type</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">Frequency</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">Brand mentioned</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden sm:table-cell">Competitors mentioned</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {urls.map((row, i) => (
              <tr key={i} className="hover:bg-gray-50/60 transition-colors">
                {/* URL cell */}
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2.5 min-w-0">
                    {row.thumbnail_url ? (
                      <div className="w-10 h-7 rounded overflow-hidden bg-gray-100 shrink-0">
                        <img
                          src={row.thumbnail_url}
                          alt=""
                          className="w-full h-full object-cover"
                          onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }}
                        />
                      </div>
                    ) : (
                      <FaviconImg domain={row.domain} />
                    )}
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-brand-dark truncate">
                        {citationDisplayDomain(row.domain, row.title)}
                      </p>
                      <a
                        href={row.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[11px] text-blue-600 hover:underline truncate block max-w-[240px]"
                        title={isRedirectUrl(row.url) ? "Opens via Google redirect → actual page" : row.url}
                      >
                        {isRedirectUrl(row.url) ? (row.title ?? row.url) : row.url}
                        <ExternalLink className="inline w-2.5 h-2.5 ml-1 align-middle" />
                      </a>
                      {row.unresolved_redirect && (
                        <span className="text-[10px] text-amber-600">via Google search</span>
                      )}
                      {!row.unresolved_redirect && row.title && (
                        <p className="text-[10px] text-gray-400 truncate max-w-[240px]">{row.title}</p>
                      )}
                      {row.views != null && (
                        <p className="text-[10px] text-gray-400">{formatViews(row.views)} views</p>
                      )}
                    </div>
                  </div>
                </td>
                {/* Content type */}
                <td className="px-4 py-3 hidden md:table-cell">
                  <span className="text-xs text-gray-600">{row.content_type ?? "Web page"}</span>
                </td>
                {/* Channel type */}
                <td className="px-4 py-3 hidden md:table-cell">
                  <span className="text-xs text-gray-600">{row.channel_type ?? "Website"}</span>
                </td>
                {/* Frequency */}
                <td className="px-4 py-3 text-center">
                  <span className="inline-block font-bold text-sm text-brand-dark">{row.frequency}</span>
                </td>
                {/* Brand mentioned */}
                <td className="px-4 py-3 text-center text-sm">
                  <YesNo value={row.brand_mentioned} />
                </td>
                {/* Competitors mentioned */}
                <td className="px-4 py-3 text-center text-sm hidden sm:table-cell">
                  {row.competitor_mentioned
                    ? (
                      <div>
                        <span className="font-semibold text-blue-600">Yes</span>
                        {row.competitor_names?.length ? (
                          <p className="text-[10px] text-gray-400 mt-0.5 truncate max-w-[120px]">
                            {row.competitor_names.slice(0, 3).join(", ")}
                          </p>
                        ) : null}
                      </div>
                    )
                    : <span className="text-gray-300">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Unified citation builder (from AIO probe) ─────────────────────────────────

function buildAioUrls(aio: AioProbeResult | null | undefined): TopCitedUrl[] {
  if (!aio?.per_prompt?.length) return [];
  const map = new Map<string, TopCitedUrl>();
  for (const row of aio.per_prompt) {
    for (const c of row.citations ?? []) {
      const key = c.url.toLowerCase().replace(/\/$/, "");
      if (!map.has(key)) {
        map.set(key, {
          url: c.url,
          domain: c.domain,
          title: c.title,
          thumbnail_url: c.thumbnail_url,
          views: c.views,
          platform: c.platform,
          content_type: undefined,
          channel_type: "Google AIO",
          frequency: 0,
          probe_platforms: ["google_aio"],
          brand_mentioned: false,
          competitor_mentioned: false,
          competitor_names: [],
          unresolved_redirect: c.unresolved_redirect,
        });
      }
      const entry = map.get(key)!;
      entry.frequency += 1;
    }
  }
  return Array.from(map.values()).sort((a, b) => b.frequency - a.frequency);
}

// ── Main export ───────────────────────────────────────────────────────────────

type TabId = "domains" | "urls" | "aio";

export function CitationsPage({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const [ctx, setCtx] = useState<PromptPerformanceContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("domains");

  const load = useCallback(() => {
    setLoading(true);
    fetchPromptPerformanceContext(auditDirOrSlug)
      .then(setCtx)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => setLoading(false));
  }, [auditDirOrSlug]);

  useEffect(() => { load(); }, [load]);

  const topSites: TopCitedSite[] = ctx?.live_probe?.top_cited_sites ?? [];
  const topUrls: TopCitedUrl[] = ctx?.live_probe?.top_cited_urls ?? [];
  const aioUrls: TopCitedUrl[] = useMemo(() => buildAioUrls(ctx?.aio_probe), [ctx?.aio_probe]);
  const hasLive = topSites.length > 0 || topUrls.length > 0;
  const hasAio = aioUrls.length > 0;
  const hasAnything = hasLive || hasAio;

  const allTabs: { id: TabId; label: string; count: number }[] = [
    { id: "domains", label: "Top Domains", count: topSites.length },
    { id: "urls", label: "Top URLs", count: topUrls.length },
    ...(hasAio ? [{ id: "aio" as TabId, label: "Google AIO", count: aioUrls.length }] : []),
  ];
  const tabs = allTabs.filter((t) => t.count > 0);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="w-8 h-8 animate-spin text-brand-accent" />
      </div>
    );
  }
  if (error) return <div className="alert-error">{error}</div>;
  if (!ctx) return null;

  return (
    <Card className="!mb-0">
      <CardTitle>Citations</CardTitle>
      <CardDescription>
        Websites and URLs most commonly referenced in AI-generated answers across all probed platforms.
      </CardDescription>

      {!hasAnything ? (
        <div className="alert-info mt-4">
          No citations found yet. Run live probes or Google AIO probes from the{" "}
          <strong>Prompt performance</strong> section first.
        </div>
      ) : (
        <>
          {/* Tab bar */}
          {tabs.length > 1 && (
            <div className="flex gap-1 mt-4 mb-6 border-b border-gray-200">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className="flex items-center gap-2 px-4 py-2.5 text-sm font-semibold -mb-px border-b-2 transition-colors"
                  style={
                    tab === t.id
                      ? { borderColor: "#1a1a1a", color: "#1a1a1a" }
                      : { borderColor: "transparent", color: "#9ca3af" }
                  }
                >
                  {t.label}
                  <span
                    className="text-[10px] font-bold px-1.5 py-0.5 rounded-full"
                    style={
                      tab === t.id
                        ? { background: "#1a1a1a", color: "#fff" }
                        : { background: "#f3f4f6", color: "#6b7280" }
                    }
                  >
                    {t.count}
                  </span>
                </button>
              ))}
            </div>
          )}

          {/* Content */}
          <div className="mt-4">
            {(tab === "domains" || tabs.length === 1) && (
              <>
                {topSites.length > 0
                  ? <TopDomainsTable sites={topSites} />
                  : <p className="text-sm text-gray-500">No domain data — run live probes first.</p>}
              </>
            )}
            {tab === "urls" && (
              <>
                {topUrls.length > 0
                  ? <TopUrlsTable urls={topUrls} />
                  : <p className="text-sm text-gray-500">No URL data — run live probes first.</p>}
              </>
            )}
            {tab === "aio" && (
              <>
                {aioUrls.length > 0
                  ? <TopUrlsTable urls={aioUrls} />
                  : <p className="text-sm text-gray-500">No Google AIO citations — run AIO probes from Prompt Performance.</p>}
              </>
            )}
          </div>
        </>
      )}
    </Card>
  );
}
