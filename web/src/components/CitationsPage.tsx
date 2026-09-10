import { useEffect, useMemo, useState } from "react";
import { ExternalLink, X } from "lucide-react";
import { PageLoading } from "./PageLoading";
import { ensurePromptPerformance } from "../lib/promptPerformanceStore";
import { fetchCitationsView, type CitationsViewPayload } from "../api/client";
import type {
  PromptPerformanceContext,
  TopCitedSite,
  TopCitedUrl,
} from "../types";
import { Card, CardDescription, CardTitle } from "./ui/Card";
import CitationOverTime from "./CitationOverTime";
import { CompetitorFavicon, PlatformLogoRow } from "./PlatformLogo";
import { platformScoreColor } from "../lib/platformScoreColor";
import { normalizePromptLocales, type PromptLocale } from "../lib/promptLocales";
import {
  aggregateCitationSitesFromPrompts,
  aggregateCitationUrlsFromPrompts,
  isPromptCitationSource,
  PROBE_CITATION_PLATFORMS,
  registrableDomain,
} from "../lib/citationSource";
import { buildCompetitorCitationMatcher, OVERALL_LOCALE_KEY } from "../lib/localeProbeView";
import { preferredInitialLocaleKey } from "../lib/defaultLocaleView";
import { filterContextByTopic, listProbeTopics } from "../lib/promptCategoryGrouping";
import {
  platformsForSurface,
  type SurfaceFilter,
} from "../lib/visibilityMetrics";
import { PromptLocaleFilter, liveProbeForLocale } from "./PromptLocaleFilter";
import { ReportFilterSelect } from "./ReportFilterSelect";
import { VirtualScrollTable } from "./VirtualScrollTable";
import { ViewportOverlay } from "./ViewportOverlay";
import type { ReactNode } from "react";

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

/** Match server caps on `/prompt-performance/citations` (show full capped lists). */
const CITATIONS_TOP_DOMAINS = 15;
const CITATIONS_TOP_URLS = 30;
/** Initial visible rows; Load more only matters if a list somehow exceeds the cap. */
const CITATIONS_INITIAL_PAGE_DOMAINS = CITATIONS_TOP_DOMAINS;
const CITATIONS_INITIAL_PAGE_URLS = CITATIONS_TOP_URLS;
const CITATIONS_PAGE_SIZE = 10;
/** When the full list is at least this long, virtualize instead of paginating. */
const CITATIONS_VIRTUALIZE_THRESHOLD = 40;

function filterCitedSitesByPlatforms(
  sites: TopCitedSite[],
  platforms: readonly string[],
): TopCitedSite[] {
  const allowed = new Set(platforms);
  return sites
    .map((site) => ({
      ...site,
      platforms: (site.platforms ?? []).filter((platform) => allowed.has(platform)),
    }))
    .filter((site) => site.platforms.length > 0)
    .sort((left, right) => Number(right.count ?? 0) - Number(left.count ?? 0));
}

function filterCitedUrlsByPlatforms(
  urls: TopCitedUrl[],
  platforms: readonly string[],
): TopCitedUrl[] {
  const allowed = new Set(platforms);
  return urls
    .map((url) => ({
      ...url,
      probe_platforms: (url.probe_platforms ?? []).filter((platform) => allowed.has(platform)),
    }))
    .filter((url) => {
      if (url.probe_platforms.length > 0) return true;
      return Boolean(url.platform && allowed.has(url.platform));
    })
    .sort((left, right) => Number(right.frequency ?? 0) - Number(left.frequency ?? 0));
}

function LoadMoreFooter({
  visible,
  total,
  onLoadMore,
  noun,
  initialPage,
}: {
  visible: number;
  total: number;
  onLoadMore: () => void;
  noun: string;
  initialPage: number;
}) {
  if (total <= initialPage) return null;
  const remaining = Math.max(0, total - visible);
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-3 border-t border-gray-100 bg-gray-50/80">
      <p className="text-xs text-gray-500">
        Showing {Math.min(visible, total)} of {total} {noun}
      </p>
      {remaining > 0 ? (
        <button
          type="button"
          onClick={onLoadMore}
          className="text-xs font-semibold text-blue-700 hover:text-blue-900 underline underline-offset-2"
        >
          Load {Math.min(CITATIONS_PAGE_SIZE, remaining)} more
        </button>
      ) : (
        <button
          type="button"
          onClick={() => onLoadMore()}
          className="text-xs font-semibold text-gray-500 hover:text-gray-700 underline underline-offset-2"
        >
          Show top {initialPage}
        </button>
      )}
    </div>
  );
}


function formatViews(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

// ── Competitor names with favicon ─────────────────────────────────────────────

function CompetitorNameList({
  names,
  competitorMap,
}: {
  names: string[];
  competitorMap: Map<string, string>;
}) {
  if (!names.length) return <span className="text-gray-300">—</span>;
  return (
    <div className="flex flex-col gap-1">
      {names.slice(0, 4).map((name) => {
        const website = competitorMap.get(name.toLowerCase());
        return (
          <div key={name} className="flex items-center gap-1.5">
            <CompetitorFavicon name={name} website={website} size={14} />
            {website ? (
              <a
                href={website}
                target="_blank"
                rel="noopener noreferrer"
                className="max-w-[130px] truncate rounded-sm text-xs text-[#0d0d0d] hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                {name}
              </a>
            ) : (
              <span className="max-w-[130px] truncate text-xs text-[#0d0d0d]">{name}</span>
            )}
          </div>
        );
      })}
      {names.length > 4 && (
        <span className="text-[10px] text-gray-400">+{names.length - 4} more</span>
      )}
    </div>
  );
}

// ── URL detail overlay ────────────────────────────────────────────────────────

interface UrlContext {
  url: string;
  domain: string;
  title?: string;
  topics: string[];
  prompts: { prompt: string; topic: string; platforms: string[] }[];
}

const PROBE_PLATFORMS_ALL = ["gemini", "openai", "claude", "google_aio"];

function buildUrlContext(url: string, ctx: PromptPerformanceContext): UrlContext {
  const live = ctx.live_probe;
  const perPrompt = (live?.per_prompt ?? []) as Array<Record<string, unknown>>;

  let domain = url;
  try { domain = new URL(url).hostname.replace(/^www\./, ""); } catch { /* ignore */ }

  const urlMeta = live?.top_cited_urls?.find((u) => u.url === url);
  const title = urlMeta?.title;

  const mappingRows = (ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows) ?? [];
  const promptTopicMap = new Map<string, string>();
  if (ctx.use_pss && mappingRows.length) {
    let idx = 0;
    for (const pssRow of mappingRows) {
      for (const p of (pssRow.prompts ?? [])) {
        if (!p?.trim()) continue;
        if (idx >= perPrompt.length) break;
        promptTopicMap.set(String((perPrompt[idx] as Record<string, unknown>).prompt ?? p).trim(), pssRow.product_or_service ?? "");
        idx++;
      }
    }
  }

  const normalizedUrl = url.toLowerCase().replace(/\/$/, "");
  const topicSet = new Set<string>();
  const citedPrompts: UrlContext["prompts"] = [];

  for (const row of perPrompt) {
    const rowPrompt = String(row.prompt ?? "").trim();
    const topic = promptTopicMap.get(rowPrompt) ?? "";
    const platforms: string[] = [];
    for (const plat of PROBE_PLATFORMS_ALL) {
      const cits = (row[`citations_${plat}`] ?? []) as Array<{ url: string; domain?: string }>;
      if (cits.some((c) =>
        isPromptCitationSource(c, ctx)
        && c.url.toLowerCase().replace(/\/$/, "") === normalizedUrl
      )) {
        platforms.push(plat);
      }
    }
    if (platforms.length > 0) {
      if (topic) topicSet.add(topic);
      citedPrompts.push({ prompt: rowPrompt, topic, platforms });
    }
  }

  return { url, domain, title, topics: Array.from(topicSet), prompts: citedPrompts };
}

const PLAT_LABEL: Record<string, string> = {
  gemini: "Gemini", openai: "OpenAI", claude: "Claude", google_aio: "Google AI",
};

function UrlDetailOverlay({
  urlCtx,
  onClose,
}: {
  urlCtx: UrlContext;
  onClose: () => void;
}) {
  return (
    <ViewportOverlay onClose={onClose}>
      <div className="bg-white rounded-2xl w-full max-w-2xl max-h-[min(88vh,900px)] flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-100 bg-gray-50 sticky top-0 z-10">
          <FaviconImg domain={urlCtx.domain} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-bold text-[#0d0d0d] truncate">{urlCtx.title || urlCtx.domain}</p>
            <a
              href={urlCtx.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[11px] text-blue-500 hover:underline truncate block max-w-full"
            >
              {urlCtx.url.length > 80 ? urlCtx.url.slice(0, 80) + "…" : urlCtx.url}
              <ExternalLink className="inline w-2.5 h-2.5 ml-1 align-middle" />
            </a>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-5">
          {/* Topics */}
          {urlCtx.topics.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">Topics</p>
              <div className="flex flex-wrap gap-2">
                {urlCtx.topics.map((t) => (
                  <span key={t} className="text-xs font-semibold px-2.5 py-1 rounded-full bg-blue-50 text-blue-700">{t}</span>
                ))}
              </div>
            </div>
          )}

          {/* Prompts */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">
              Prompts that cited this URL ({urlCtx.prompts.length})
            </p>
            <div className="space-y-2">
              {urlCtx.prompts.map((p, i) => (
                <div key={i} className="rounded-lg border border-gray-100 bg-gray-50 px-3.5 py-2.5">
                  {p.topic && (
                    <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-stone-100 text-stone-500 mb-1 inline-block">
                      {p.topic}
                    </span>
                  )}
                  <p className="text-sm text-[#0d0d0d] leading-snug mb-1.5">{p.prompt}</p>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {p.platforms.map((plat) => (
                      <span
                        key={plat}
                        className="inline-flex items-center gap-1 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-gray-100 text-gray-500"
                      >
                        {PLAT_LABEL[plat] ?? plat}
                      </span>
                    ))}
                  </div>
                </div>
              ))}
              {urlCtx.prompts.length === 0 && (
                <p className="text-xs text-gray-400 italic">No matching prompts found in probe data.</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </ViewportOverlay>
  );
}

// ── Domain detail overlay ─────────────────────────────────────────────────────

interface DomainContext {
  domain: string;
  topics: string[];
  prompts: { prompt: string; topic: string; urls: string[] }[];
  urls: { url: string; title?: string }[];
}

function buildDomainContext(
  domain: string,
  ctx: PromptPerformanceContext,
): DomainContext {
  const live = ctx.live_probe;
  const perPrompt = (live?.per_prompt ?? []) as Array<Record<string, unknown>>;
  const PROBE_PLATFORMS = ["gemini", "openai", "claude", "google_aio"];

  // Build prompt → topic mapping
  const mappingRows = (ctx.probed_pss_rows?.length ? ctx.probed_pss_rows : ctx.pss_rows) ?? [];
  const promptTopicMap = new Map<string, string>();
  if (ctx.use_pss && mappingRows.length) {
    let idx = 0;
    for (const pssRow of mappingRows) {
      for (const p of (pssRow.prompts ?? [])) {
        if (!p?.trim()) continue;
        if (idx >= perPrompt.length) break;
        const row = perPrompt[idx];
        const prompt = String(row.prompt ?? p).trim();
        promptTopicMap.set(prompt, pssRow.product_or_service ?? "");
        idx++;
      }
    }
  }

  const topicSet = new Set<string>();
  const urlSet = new Set<string>();
  const citedPrompts: DomainContext["prompts"] = [];

  for (const row of perPrompt) {
    const rowPrompt = String(row.prompt ?? "").trim();
    const topic = promptTopicMap.get(rowPrompt) ?? "";
    const rowUrls: string[] = [];

    for (const plat of PROBE_PLATFORMS) {
      const cits = (row[`citations_${plat}`] ?? []) as Array<{ domain: string; url: string; title?: string }>;
      for (const c of cits) {
        if (!isPromptCitationSource(c, ctx)) continue;
        if (registrableDomain(c.domain ?? "") === registrableDomain(domain)) {
          rowUrls.push(c.url);
          urlSet.add(c.url);
        }
      }
    }

    if (rowUrls.length > 0) {
      if (topic) topicSet.add(topic);
      citedPrompts.push({ prompt: rowPrompt, topic, urls: [...new Set(rowUrls)] });
    }
  }

  // Also include AIO citations
  for (const row of ((ctx.aio_probe?.per_prompt ?? []) as unknown[]) as Array<Record<string, unknown>>) {
    const rowPrompt = String(row.prompt ?? "").trim();
    const topic = promptTopicMap.get(rowPrompt) ?? "";
    const cits = (row.citations ?? []) as Array<{ domain: string; url: string; title?: string }>;
    const rowUrls = cits
      .filter((citation) =>
        isPromptCitationSource(citation, ctx)
        && registrableDomain(citation.domain ?? "") === registrableDomain(domain)
      )
      .map((citation) => citation.url);
    if (rowUrls.length > 0) {
      if (topic) topicSet.add(topic);
      rowUrls.forEach((u) => urlSet.add(u));
      if (!citedPrompts.find((p) => p.prompt === rowPrompt)) {
        citedPrompts.push({ prompt: rowPrompt, topic, urls: [...new Set(rowUrls)] });
      }
    }
  }

  // Build unique URLs with titles from top_cited_urls
  const urlTitleMap = new Map<string, string>();
  for (const u of (live?.top_cited_urls ?? [])) {
    if (registrableDomain(u.domain ?? "") === registrableDomain(domain)) {
      urlTitleMap.set(u.url, u.title ?? "");
    }
  }
  const urls = Array.from(urlSet).map((url) => ({ url, title: urlTitleMap.get(url) }));

  return {
    domain,
    topics: Array.from(topicSet),
    prompts: citedPrompts,
    urls,
  };
}

function DomainDetailOverlay({
  domainCtx,
  onClose,
  auditId,
}: {
  domainCtx: DomainContext;
  onClose: () => void;
  auditId?: string;
}) {
  return (
    <ViewportOverlay onClose={onClose}>
      <div className="bg-white rounded-2xl w-full max-w-2xl max-h-[min(88vh,900px)] flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-100 bg-gray-50 sticky top-0 z-10">
          <FaviconImg domain={domainCtx.domain} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-bold text-[#0d0d0d] truncate">{domainCtx.domain}</p>
            <p className="text-[11px] text-gray-400">{domainCtx.prompts.length} prompt{domainCtx.prompts.length !== 1 ? "s" : ""} cited this domain</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="shrink-0 w-8 h-8 flex items-center justify-center rounded-full text-gray-400 hover:text-gray-700 hover:bg-gray-100 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-6">
          {/* Topics */}
          {domainCtx.topics.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">Topics</p>
              <div className="flex flex-wrap gap-2">
                {domainCtx.topics.map((t) => (
                  <span key={t} className="text-xs font-semibold px-2.5 py-1 rounded-full bg-blue-50 text-blue-700">
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Prompts */}
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">
              Prompts that cited this domain ({domainCtx.prompts.length})
            </p>
            <div className="space-y-2">
              {domainCtx.prompts.map((p, i) => (
                <div key={i} className="rounded-lg border border-gray-100 bg-gray-50 px-3.5 py-2.5">
                  {p.topic && (
                    <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-stone-100 text-stone-500 mb-1 inline-block">
                      {p.topic}
                    </span>
                  )}
                  <p className="text-sm text-[#0d0d0d] leading-snug">{p.prompt}</p>
                  {p.urls.length > 0 && (
                    <div className="mt-1.5 flex flex-col gap-1">
                      {p.urls.map((url, j) => (
                        <a
                          key={j}
                          href={url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[11px] text-blue-600 hover:underline truncate flex items-center gap-1"
                        >
                          {url}
                          <ExternalLink className="w-2.5 h-2.5 shrink-0" />
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* All cited URLs */}
          {domainCtx.urls.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 mb-2">
                URLs cited from this domain ({domainCtx.urls.length})
              </p>
              <ul className="space-y-1.5">
                {domainCtx.urls.map((u, i) => (
                  <li key={i} className="flex items-start gap-2">
                    <a
                      href={u.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-blue-600 hover:underline break-all flex items-center gap-1"
                    >
                      {u.title ? (
                        <span>
                          <span className="font-medium text-[#0d0d0d]">{u.title}</span>
                          <span className="ml-1 text-gray-400">— {u.url}</span>
                        </span>
                      ) : u.url}
                      <ExternalLink className="w-2.5 h-2.5 shrink-0" />
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Citation over time for this domain */}
          {auditId && (
            <CitationOverTime auditId={auditId} selectedDomain={domainCtx.domain} />
          )}
        </div>
      </div>
    </ViewportOverlay>
  );
}

// ── Domain-level table ────────────────────────────────────────────────────────

function TopDomainsTable({
  sites,
  competitorMap,
  competitorDomainMatch,
  onDomainClick,
}: {
  sites: TopCitedSite[];
  competitorMap: Map<string, string>;
  competitorDomainMatch: (domain: string) => { isCompetitor: boolean; name?: string };
  onDomainClick: (domain: string) => void;
}) {
  const [visibleCount, setVisibleCount] = useState(CITATIONS_INITIAL_PAGE_DOMAINS);
  useEffect(() => {
    setVisibleCount(CITATIONS_INITIAL_PAGE_DOMAINS);
  }, [sites]);

  if (!sites.length) return null;
  const useVirtual = sites.length >= CITATIONS_VIRTUALIZE_THRESHOLD;
  const list = useVirtual ? sites : sites.slice(0, visibleCount);
  const maxCount = Math.max(...sites.map((s) => s.count), 1);

  const rows: ReactNode[] = list.map((site, i) => {
    const barPct = Math.round((site.count / maxCount) * 100);
    const competitor = competitorDomainMatch(site.domain);
    return (
      <tr
        key={`${site.domain}-${i}`}
        className="hover:bg-blue-50/30 transition-colors cursor-pointer border-b border-gray-50"
        onClick={() => onDomainClick(site.domain)}
      >
        <td className="px-4 py-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <FaviconImg domain={site.domain} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 min-w-0">
                <a
                  href={site.example_url ?? (isRedirectUrl(site.example_url) ? site.example_url : `https://${site.domain}`)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-semibold text-brand-dark hover:underline truncate"
                  title={site.title}
                  onClick={(e) => e.stopPropagation()}
                >
                  {citationDisplayDomain(site.domain, site.title)}
                </a>
                {competitor.isCompetitor ? (
                  <span
                    className="shrink-0 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-100 text-amber-800"
                    title={competitor.name ? `Competitor: ${competitor.name}` : "Competitor"}
                  >
                    Competitor
                  </span>
                ) : null}
              </div>
              {site.unresolved_redirect && (
                <span className="text-[10px] text-amber-600">via Google search</span>
              )}
              <div className="mt-1 h-1 bg-gray-100 rounded-full overflow-hidden" style={{ maxWidth: 180 }}>
                <div className="h-full rounded-full" style={{ width: `${barPct}%`, background: platformScoreColor(barPct) }} />
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
        <td className="px-4 py-3 hidden md:table-cell">
          {site.competitor_mentioned
            ? <CompetitorNameList names={site.competitor_names ?? []} competitorMap={competitorMap} />
            : <span className="text-gray-300 text-sm">—</span>}
        </td>
        <td className="px-4 py-3 hidden lg:table-cell">
          <PlatformLogoRow platforms={site.platforms} size={18} />
        </td>
      </tr>
    );
  });

  return (
    <div className="mb-10">
      <h3 className="text-sm font-bold text-brand-dark mb-0.5">Top Domains</h3>
      <p className="text-xs text-gray-500 mb-4">Websites most commonly referenced in AI-generated answers.</p>
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm">
        <VirtualScrollTable
          colSpan={5}
          threshold={CITATIONS_VIRTUALIZE_THRESHOLD}
          estimateSize={64}
          tableClassName="w-full"
          head={(
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5">Domain</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">Frequency</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">Brand mentioned</th>
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden md:table-cell">Mentions</th>
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden lg:table-cell">AI platforms</th>
            </tr>
          )}
          rows={rows}
        />
        {!useVirtual ? (
          <LoadMoreFooter
            visible={visibleCount}
            total={sites.length}
            noun="domains"
            initialPage={CITATIONS_INITIAL_PAGE_DOMAINS}
            onLoadMore={() =>
              setVisibleCount((current) =>
                current >= sites.length
                  ? CITATIONS_INITIAL_PAGE_DOMAINS
                  : Math.min(sites.length, current + CITATIONS_PAGE_SIZE),
              )
            }
          />
        ) : null}
      </div>
    </div>
  );
}

// ── URL-level table ───────────────────────────────────────────────────────────

function TopUrlsTable({
  urls,
  competitorMap,
  competitorDomainMatch,
  onUrlClick,
}: {
  urls: TopCitedUrl[];
  competitorMap: Map<string, string>;
  competitorDomainMatch: (domain: string) => { isCompetitor: boolean; name?: string };
  onUrlClick: (url: string) => void;
}) {
  const [visibleCount, setVisibleCount] = useState(CITATIONS_INITIAL_PAGE_URLS);
  useEffect(() => {
    setVisibleCount(CITATIONS_INITIAL_PAGE_URLS);
  }, [urls]);

  if (!urls.length) return null;
  const useVirtual = urls.length >= CITATIONS_VIRTUALIZE_THRESHOLD;
  const list = useVirtual ? urls : urls.slice(0, visibleCount);

  const rows: ReactNode[] = list.map((row, i) => {
    const competitor = competitorDomainMatch(row.domain);
    return (
      <tr
        key={`${row.url}-${i}`}
        className="hover:bg-blue-50/30 transition-colors cursor-pointer border-b border-gray-50"
        onClick={() => onUrlClick(row.url)}
      >
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
              <div className="flex items-center gap-2 min-w-0">
                <p className="text-xs font-semibold text-brand-dark truncate">
                  {citationDisplayDomain(row.domain, row.title)}
                </p>
                {competitor.isCompetitor ? (
                  <span
                    className="shrink-0 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-100 text-amber-800"
                    title={competitor.name ? `Competitor: ${competitor.name}` : "Competitor"}
                  >
                    Competitor
                  </span>
                ) : null}
              </div>
              <a
                href={row.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[11px] text-blue-600 hover:underline truncate block max-w-[240px]"
                title={isRedirectUrl(row.url) ? "Opens via Google redirect → actual page" : row.url}
                onClick={(e) => e.stopPropagation()}
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
        <td className="px-4 py-3 hidden md:table-cell">
          <span className="text-xs text-gray-600">{row.content_type ?? "Web page"}</span>
        </td>
        <td className="px-4 py-3 hidden md:table-cell">
          <span className="text-xs text-gray-600">{row.channel_type ?? "Website"}</span>
        </td>
        <td className="px-4 py-3 text-center">
          <span className="inline-block font-bold text-sm text-brand-dark">{row.frequency}</span>
        </td>
        <td className="px-4 py-3 text-center text-sm">
          <YesNo value={row.brand_mentioned} />
        </td>
        <td className="px-4 py-3 hidden sm:table-cell">
          {row.competitor_mentioned
            ? <CompetitorNameList names={row.competitor_names ?? []} competitorMap={competitorMap} />
            : <span className="text-gray-300 text-sm">—</span>}
        </td>
      </tr>
    );
  });

  return (
    <div className="mb-6">
      <h3 className="text-sm font-bold text-brand-dark mb-0.5">Top URLs</h3>
      <p className="text-xs text-gray-500 mb-4">URLs most commonly referenced in AI-generated answers. Click a row to see which prompts cited it.</p>
      <div className="rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm">
        <VirtualScrollTable
          colSpan={6}
          threshold={CITATIONS_VIRTUALIZE_THRESHOLD}
          estimateSize={72}
          tableClassName="w-full"
          head={(
            <tr className="border-b border-gray-100 bg-gray-50">
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5">URL</th>
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden md:table-cell">Content type</th>
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden md:table-cell">Channel type</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">Frequency</th>
              <th className="text-center text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap">Brand mentioned</th>
              <th className="text-left text-[11px] font-semibold text-gray-500 uppercase tracking-wide px-4 py-2.5 whitespace-nowrap hidden sm:table-cell">Mentions</th>
            </tr>
          )}
          rows={rows}
        />
        {!useVirtual ? (
          <LoadMoreFooter
            visible={visibleCount}
            total={urls.length}
            noun="URLs"
            initialPage={CITATIONS_INITIAL_PAGE_URLS}
            onLoadMore={() =>
              setVisibleCount((current) =>
                current >= urls.length
                  ? CITATIONS_INITIAL_PAGE_URLS
                  : Math.min(urls.length, current + CITATIONS_PAGE_SIZE),
              )
            }
          />
        ) : null}
      </div>
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

type TabId = "domains" | "urls" | "aio";

type CitationsViewState = {
  payload: CitationsViewPayload;
  competitorDomainMatch: (domain: string) => { isCompetitor: boolean; name?: string; website?: string };
  competitorMap: Map<string, string>;
};

function miniCtxFromCitations(payload: CitationsViewPayload): PromptPerformanceContext {
  return {
    brand_name: payload.brand_name || "",
    brand_site_url: payload.brand_site_url || "",
    competitors: payload.competitors || [],
    primary_market: payload.primary_market || { country: "", country_id: "" },
    prompt_locales: payload.prompt_locales || [],
    default_locale_key: payload.default_locale_key || "",
    locale_spread: payload.locale_spread || [],
    prompt_count: payload.prompt_count || 0,
    use_pss: false,
    pss_rows: [],
    flat_prompts: [],
    category_labels: [],
    industry: "",
    live_probe: null,
    highlight: { brand: payload.brand_name || "", competitor_urls: [], competitor_brands: [] },
  } as unknown as PromptPerformanceContext;
}

export function CitationsPage({ auditDirOrSlug }: { auditDirOrSlug: string }) {
  const [tab, setTab] = useState<TabId>("domains");
  const [selectedDomain, setSelectedDomain] = useState<string | null>(null);
  const [selectedUrl, setSelectedUrl] = useState<string | null>(null);
  const [selectedLocaleKey, setSelectedLocaleKey] = useState<string | null>(null);
  const [selectedTopic, setSelectedTopic] = useState("All topics");
  const [surfaceFilter, setSurfaceFilter] = useState<SurfaceFilter>("all");
  const [view, setView] = useState<CitationsViewState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** Full probe context — for topic list, filtered re-aggregation, and drill-down overlays. */
  const [probeCtx, setProbeCtx] = useState<PromptPerformanceContext | null>(null);
  const [overlayCtx, setOverlayCtx] = useState<PromptPerformanceContext | null>(null);
  const [overlayLoading, setOverlayLoading] = useState(false);

  const applyPayload = (payload: CitationsViewPayload) => {
    const mini = miniCtxFromCitations(payload);
    const competitorMap = new Map<string, string>();
    for (const c of payload.competitors ?? []) {
      if (c.competitor_brand) {
        competitorMap.set(c.competitor_brand.toLowerCase(), c.competitor_website);
      }
    }
    setView({
      payload,
      competitorDomainMatch: buildCompetitorCitationMatcher(mini),
      competitorMap,
    });
    setSelectedLocaleKey(payload.locale_key || preferredInitialLocaleKey(mini));
    setLoading(false);
    setError(null);
  };

  useEffect(() => {
    setSelectedLocaleKey(null);
    setSelectedTopic("All topics");
    setSurfaceFilter("all");
    setView(null);
    setError(null);
    setProbeCtx(null);
    setOverlayCtx(null);
    setLoading(true);

    let cancelled = false;
    const ac = new AbortController();
    void fetchCitationsView(auditDirOrSlug, { signal: ac.signal })
      .then((payload) => {
        if (cancelled) return;
        applyPayload(payload);
      })
      .catch((err) => {
        // Unmount/remount only — never leave an infinite spinner on timeout/network errors.
        if (cancelled) return;
        setLoading(false);
        setView(null);
        setError(err instanceof Error ? err.message : "Failed to load citations");
      });

    return () => {
      cancelled = true;
      ac.abort();
    };
  }, [auditDirOrSlug]);

  const localeKey = selectedLocaleKey || OVERALL_LOCALE_KEY;

  // Load slim probe for topic filtering (and reuse for overlays).
  useEffect(() => {
    if (!view) return;
    let cancelled = false;
    void ensurePromptPerformance(
      auditDirOrSlug,
      localeKey === OVERALL_LOCALE_KEY ? { allLocales: true } : { locale: localeKey },
    )
      .then((ctx) => {
        if (!cancelled) {
          setProbeCtx(ctx);
          setOverlayCtx(ctx);
        }
      })
      .catch(() => {
        if (!cancelled) setProbeCtx(null);
      });
    return () => {
      cancelled = true;
    };
  }, [auditDirOrSlug, localeKey, view]);

  // Lazy-load full slim probe only for drill-down overlays when not already loaded.
  useEffect(() => {
    if (!selectedDomain && !selectedUrl) return;
    if (overlayCtx) return;
    let cancelled = false;
    setOverlayLoading(true);
    void ensurePromptPerformance(
      auditDirOrSlug,
      localeKey === OVERALL_LOCALE_KEY ? { allLocales: true } : { locale: localeKey },
    )
      .then((ctx) => {
        if (!cancelled) {
          setOverlayCtx(ctx);
          setProbeCtx(ctx);
          setOverlayLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setOverlayLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [auditDirOrSlug, selectedDomain, selectedUrl, localeKey, overlayCtx]);

  const onLocaleChange = (key: string) => {
    if (key === selectedLocaleKey) return;
    setSelectedLocaleKey(key);
    setSelectedTopic("All topics");
    setProbeCtx(null);
    setOverlayCtx(null);
    setLoading(true);
    setError(null);
    void fetchCitationsView(auditDirOrSlug, { locale: key })
      .then((payload) => {
        applyPayload(payload);
      })
      .catch((err) => {
        setLoading(false);
        setError(err instanceof Error ? err.message : "Failed to load citations");
      });
  };

  const filterLocales = useMemo(
    () =>
      normalizePromptLocales(
        view?.payload.prompt_locales as PromptLocale[] | undefined,
        view?.payload.primary_market?.country ?? "",
        view?.payload.primary_market?.country_id ?? "",
      ),
    [view],
  );

  const localeProbeCtx = useMemo(() => {
    if (!probeCtx) return null;
    const live = liveProbeForLocale(probeCtx, localeKey);
    return { ...probeCtx, live_probe: live };
  }, [probeCtx, localeKey]);

  const topics = useMemo(() => listProbeTopics(localeProbeCtx), [localeProbeCtx]);

  const surfacePlatforms = useMemo(
    () => platformsForSurface(surfaceFilter, PROBE_CITATION_PLATFORMS),
    [surfaceFilter],
  );

  const filteredLists = useMemo(() => {
    const payload = view?.payload;
    if (!payload) {
      return { topSites: [] as TopCitedSite[], topUrls: [] as TopCitedUrl[], aioUrls: [] as TopCitedUrl[] };
    }
    const topicActive = selectedTopic !== "All topics";
    const surfaceActive = surfaceFilter !== "all";

    // Topic filter requires probe rows; keep slim payload until they load.
    if (topicActive && !localeProbeCtx) {
      return {
        topSites: [] as TopCitedSite[],
        topUrls: [] as TopCitedUrl[],
        aioUrls: [] as TopCitedUrl[],
      };
    }

    if (localeProbeCtx && (topicActive || surfaceActive)) {
      const topicCtx = filterContextByTopic(localeProbeCtx, selectedTopic);
      const rows = topicCtx?.live_probe?.per_prompt as Array<Record<string, unknown>> | undefined;
      return {
        topSites: aggregateCitationSitesFromPrompts(rows, topicCtx, surfacePlatforms).slice(
          0,
          CITATIONS_TOP_DOMAINS,
        ),
        topUrls: aggregateCitationUrlsFromPrompts(rows, topicCtx, surfacePlatforms).slice(
          0,
          CITATIONS_TOP_URLS,
        ),
        aioUrls:
          surfaceFilter === "chatbots"
            ? []
            : (payload.aio_cited_urls ?? []).slice(0, CITATIONS_TOP_URLS),
      };
    }

    const sites = payload.top_cited_sites ?? [];
    const urls = payload.top_cited_urls ?? [];
    const aio = payload.aio_cited_urls ?? [];
    if (!surfaceActive) {
      return { topSites: sites, topUrls: urls, aioUrls: aio };
    }
    return {
      topSites: filterCitedSitesByPlatforms(sites, surfacePlatforms),
      topUrls: filterCitedUrlsByPlatforms(urls, surfacePlatforms),
      aioUrls: surfaceFilter === "chatbots" ? [] : aio,
    };
  }, [view, localeProbeCtx, selectedTopic, surfaceFilter, surfacePlatforms]);

  if (loading && !view && !error) {
    return <PageLoading label="Loading citations…" />;
  }
  if (error && !view) {
    return <div className="alert-error">{error}</div>;
  }
  if (!view) return null;

  const { payload, competitorDomainMatch, competitorMap } = view;
  const { topSites, topUrls, aioUrls } = filteredLists;
  const hasLive = topSites.length > 0 || topUrls.length > 0;
  const hasAio = aioUrls.length > 0;
  const hasAnything = hasLive || hasAio;
  const hasProbe = Boolean(payload.has_probe_data);
  const topicFilterPending = selectedTopic !== "All topics" && !localeProbeCtx;

  const allTabs: { id: TabId; label: string; count: number }[] = [
    { id: "domains", label: "Top Domains", count: topSites.length },
    { id: "urls", label: "Top URLs", count: topUrls.length },
    ...(hasAio ? [{ id: "aio" as TabId, label: "Google AIO", count: aioUrls.length }] : []),
  ];
  const tabs = allTabs.filter((t) => t.count > 0);
  const activeTab = tabs.some((t) => t.id === tab) ? tab : (tabs[0]?.id ?? "domains");

  return (
    <Card className="!mb-0">
      <CardTitle>Citations</CardTitle>
      <CardDescription>
        Domains and URLs the AI platforms referenced as sources — not sites merely recommended in the answer.
      </CardDescription>

      <div className="flex flex-wrap items-end gap-4">
        <PromptLocaleFilter
          locales={filterLocales}
          selectedKey={localeKey}
          onChange={onLocaleChange}
          hideIfSingle={false}
          ctx={miniCtxFromCitations(payload)}
          className="mb-0"
        />
        <ReportFilterSelect
          id="citations-topic-select"
          label="Topic"
          hint="Limit citations to prompts tagged with one product or service topic."
          value={selectedTopic}
          onChange={(event) => setSelectedTopic(event.target.value)}
        >
          <option>All topics</option>
          {topics.map((topic) => (
            <option key={topic}>{topic}</option>
          ))}
        </ReportFilterSelect>
        <ReportFilterSelect
          id="citations-surface-select"
          label="Surface"
          hint="Chatbots (Gemini, ChatGPT, Claude) or Google AI Overviews."
          value={surfaceFilter}
          onChange={(event) => setSurfaceFilter(event.target.value as SurfaceFilter)}
        >
          <option value="all">All surfaces</option>
          <option value="chatbots">Chatbots</option>
          <option value="overviews">AI Overviews</option>
        </ReportFilterSelect>
      </div>
      {loading ? (
        <div className="mt-2 mb-2">
          <PageLoading label="Loading citations…" />
        </div>
      ) : null}
      {error ? <div className="alert-error mt-2 mb-2">{error}</div> : null}

      {localeKey !== OVERALL_LOCALE_KEY && !hasProbe ? (
        <div className="alert-info mt-4">
          No probe data for this market/language yet. Re-run failed markets from the Prompts section to fill it in.
        </div>
      ) : topicFilterPending ? (
        <div className="mt-4">
          <PageLoading label="Applying topic filter…" />
        </div>
      ) : !hasAnything ? (
        <div className="alert-info mt-4">
          No citations found yet. Run live probes or Google AIO probes from the{" "}
          <strong>Prompt performance</strong> section first.
        </div>
      ) : (
        <>
          {tabs.length > 1 && (
            <div className="flex gap-1 mt-4 mb-6 border-b border-gray-200">
              {tabs.map((t) => (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => setTab(t.id)}
                  className="flex items-center gap-2 px-4 py-2.5 text-sm font-semibold -mb-px border-b-2 transition-colors"
                  style={
                    activeTab === t.id
                      ? { borderColor: "#1a1a1a", color: "#1a1a1a" }
                      : { borderColor: "transparent", color: "#9ca3af" }
                  }
                >
                  {t.label}
                  <span
                    className="text-[10px] font-bold px-1.5 py-0.5 rounded-full"
                    style={
                      activeTab === t.id
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

          <div className="mt-4">
            {activeTab === "domains" && (
              <>
                {topSites.length > 0
                  ? (
                    <TopDomainsTable
                      sites={topSites}
                      competitorMap={competitorMap}
                      competitorDomainMatch={competitorDomainMatch}
                      onDomainClick={setSelectedDomain}
                    />
                  )
                  : <p className="text-sm text-gray-500">No domain data — run live probes first.</p>}
              </>
            )}
            {activeTab === "urls" && (
              <>
                {topUrls.length > 0
                  ? (
                    <TopUrlsTable
                      urls={topUrls}
                      competitorMap={competitorMap}
                      competitorDomainMatch={competitorDomainMatch}
                      onUrlClick={setSelectedUrl}
                    />
                  )
                  : <p className="text-sm text-gray-500">No URL data — run live probes first.</p>}
              </>
            )}
            {activeTab === "aio" && (
              <>
                {aioUrls.length > 0
                  ? (
                    <TopUrlsTable
                      urls={aioUrls}
                      competitorMap={competitorMap}
                      competitorDomainMatch={competitorDomainMatch}
                      onUrlClick={setSelectedUrl}
                    />
                  )
                  : <p className="text-sm text-gray-500">No Google AIO citations — run AIO probes from Prompt Performance.</p>}
              </>
            )}
          </div>
        </>
      )}

      {selectedDomain && (
        overlayLoading || !overlayCtx ? (
          <ViewportOverlay onClose={() => setSelectedDomain(null)}>
            <div className="bg-white rounded-2xl p-8 shadow-2xl">
              <PageLoading label="Loading domain details…" />
            </div>
          </ViewportOverlay>
        ) : (
          <DomainDetailOverlay
            domainCtx={buildDomainContext(selectedDomain, overlayCtx)}
            onClose={() => setSelectedDomain(null)}
            auditId={auditDirOrSlug}
          />
        )
      )}

      {selectedUrl && (
        overlayLoading || !overlayCtx ? (
          <ViewportOverlay onClose={() => setSelectedUrl(null)}>
            <div className="bg-white rounded-2xl p-8 shadow-2xl">
              <PageLoading label="Loading URL details…" />
            </div>
          </ViewportOverlay>
        ) : (
          <UrlDetailOverlay
            urlCtx={buildUrlContext(selectedUrl, overlayCtx)}
            onClose={() => setSelectedUrl(null)}
          />
        )
      )}
    </Card>
  );
}
