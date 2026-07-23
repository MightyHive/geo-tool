import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CheckSquare,
  ExternalLink,
  Globe,
  Loader2,
  Plus,
  Square,
  X,
} from "lucide-react";
import { discoverPages, fetchGa4TopPages } from "../api/client";

interface WizardPagesStepProps {
  brandWebsite: string;
  marketCountry: string;
  marketCountryCode: string;
  ga4PropertyId?: string;
  /** Current crawl URL selection (undefined = use default sitemap discovery) */
  crawlUrls: string[] | undefined;
  onCrawlUrlsChange: (urls: string[] | undefined) => void;
  onBack: () => void;
  onContinue: () => void;
}

function urlPath(url: string): string {
  try {
    const { pathname, search } = new URL(url);
    const p = pathname + search;
    return p === "/" || p === "" ? "/" : p;
  } catch {
    return url;
  }
}

function urlDisplayLabel(url: string): string {
  try {
    const { hostname, pathname, search } = new URL(url);
    const path = (pathname + search).replace(/\/$/, "") || "/";
    return hostname + path;
  } catch {
    return url;
  }
}

export function WizardPagesStep({
  brandWebsite,
  marketCountry,
  marketCountryCode,
  ga4PropertyId,
  crawlUrls,
  onCrawlUrlsChange,
  onBack,
  onContinue,
}: WizardPagesStepProps) {
  const [discovered, setDiscovered] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [extraUrls, setExtraUrls] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addInput, setAddInput] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [source, setSource] = useState<"ga4" | "sitemap" | null>(null);
  const [pageviewsByUrl, setPageviewsByUrl] = useState<Record<string, number>>({});
  // Whether the user has made any manual changes from the defaults
  const [touched, setTouched] = useState(false);
  const addInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!brandWebsite) return;
    let cancelled = false;
    setLoading(true);
    setError(null);

    async function loadPages() {
      let urls: string[] = [];
      let nextSource: "ga4" | "sitemap" = "sitemap";
      let nextPageviews: Record<string, number> = {};
      let nextTruncated = false;

      if (ga4PropertyId) {
        try {
          const ga4 = await fetchGa4TopPages(brandWebsite, 100);
          if (ga4.pages.length) {
            const rankedPages = [...ga4.pages].sort(
              (a, b) => b.total_pageviews - a.total_pageviews || a.url.localeCompare(b.url),
            );
            urls = rankedPages.map((page) => page.url);
            nextPageviews = Object.fromEntries(
              rankedPages.map((page) => [page.url, page.total_pageviews]),
            );
            nextSource = "ga4";
          } else {
            throw new Error(
              "GA4 returned no pageview rows matching this website hostname. Check that the selected property belongs to this site.",
            );
          }
        } catch (ga4Error) {
          const detail = ga4Error instanceof Error ? ga4Error.message : String(ga4Error);
          throw new Error(
            `Could not load GA4 Total Pageviews. Go back and reconnect or select the correct property. ${detail}`,
          );
        }
      }

      if (!urls.length) {
        const sitemap = await discoverPages({
          brand_website: brandWebsite,
          market_country: marketCountry,
          market_country_code: marketCountryCode,
        });
        urls = sitemap.urls;
        nextTruncated = sitemap.truncated;
      }

      if (cancelled) return;
      setDiscovered(urls);
      setSource(nextSource);
      setPageviewsByUrl(nextPageviews);
      setTruncated(nextTruncated);
      if (crawlUrls !== undefined && crawlUrls.length > 0) {
        setSelected(new Set(crawlUrls));
        setExtraUrls(crawlUrls.filter((url) => !urls.includes(url)));
        setTouched(true);
      } else {
        setSelected(new Set(urls));
        setExtraUrls([]);
        setTouched(false);
      }
    }

    loadPages()
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    brandWebsite,
    marketCountry,
    marketCountryCode,
    ga4PropertyId,
    crawlUrls,
  ]);

  function toggleUrl(url: string) {
    setTouched(true);
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  }

  function selectAll() {
    setTouched(true);
    setSelected(new Set([...discovered, ...extraUrls]));
  }

  function deselectAll() {
    setTouched(true);
    setSelected(new Set());
  }

  function addUrl() {
    const raw = addInput.trim();
    if (!raw) return;
    let normalized = raw;
    if (!/^https?:\/\//i.test(normalized)) normalized = "https://" + normalized;
    try {
      new URL(normalized);
    } catch {
      setAddError("Enter a valid URL (e.g. https://example.com/page)");
      return;
    }
    setAddError(null);
    if (!extraUrls.includes(normalized) && !discovered.includes(normalized)) {
      setExtraUrls((prev) => [...prev, normalized]);
    }
    setSelected((prev) => new Set([...prev, normalized]));
    setAddInput("");
    setTouched(true);
    addInputRef.current?.focus();
  }

  function removeExtra(url: string) {
    setExtraUrls((prev) => prev.filter((u) => u !== url));
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(url);
      return next;
    });
  }

  function handleContinue() {
    if (!touched && source !== "ga4") {
      // No changes — pass undefined so audit uses default sitemap discovery
      onCrawlUrlsChange(undefined);
    } else {
      const finalUrls = [...selected].filter(Boolean);
      onCrawlUrlsChange(finalUrls.length > 0 ? finalUrls : undefined);
    }
    onContinue();
  }

  function handleSkip() {
    onCrawlUrlsChange(undefined);
    onContinue();
  }

  const allUrls = [...discovered, ...extraUrls];
  const selectedCount = [...selected].filter(
    (u) => discovered.includes(u) || extraUrls.includes(u),
  ).length;
  const totalCount = allUrls.length;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-gray-900">Pages to crawl</h2>
        <p className="mt-1 text-sm text-gray-500">
          {ga4PropertyId
            ? "Choose from your highest-traffic pages in GA4, ranked by Total Pageviews over the last 90 days. "
            : "Choose which pages the audit crawls. "}
          All suggested pages are selected by default. Deselect any you want to exclude, or add
          extra URLs.
        </p>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-gray-500 py-6">
          <Loader2 className="h-4 w-4 animate-spin" />
          {ga4PropertyId ? "Loading top pages from GA4…" : "Discovering pages from sitemap…"}
        </div>
      )}

      {error && !loading && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-medium">
              {ga4PropertyId ? "Could not load GA4 top pages" : "Could not discover pages automatically"}
            </p>
            <p className="mt-0.5 text-amber-700">{error}</p>
            <p className="mt-1 text-amber-700">
              {ga4PropertyId
                ? "Go back to reconnect GA4 or choose another property. You can also add URLs manually."
                : "Add URLs manually below, or skip this step to use default sitemap discovery."}
            </p>
          </div>
        </div>
      )}

      {!loading && (
        <>
          {/* Toolbar */}
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600">
              <span className="font-semibold text-gray-900">{selectedCount}</span>
              {" of "}
              <span className="font-semibold text-gray-900">{totalCount}</span>
              {" pages selected"}
              {source === "ga4" && (
                <span className="ml-1 text-blue-600">(top 100 by Total Pageviews, descending)</span>
              )}
              {source === "sitemap" && truncated && (
                <span className="ml-1 text-amber-600">(capped at 100)</span>
              )}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={selectAll}
                className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
              >
                <CheckSquare className="h-3.5 w-3.5" />
                Select all
              </button>
              <button
                type="button"
                onClick={deselectAll}
                className="inline-flex items-center gap-1 rounded border border-gray-300 bg-white px-2.5 py-1 text-xs font-medium text-gray-700 hover:bg-gray-50"
              >
                <Square className="h-3.5 w-3.5" />
                Deselect all
              </button>
            </div>
          </div>

          {/* Page list */}
          {allUrls.length > 0 ? (
            <div className="max-h-72 overflow-y-auto rounded-lg border border-gray-200 divide-y divide-gray-100">
              {/* GA4 or sitemap suggestions preserve their ranked source order. */}
              {discovered.map((url) => (
                <label
                  key={url}
                  className="flex items-center gap-3 px-3 py-2 hover:bg-gray-50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(url)}
                    onChange={() => toggleUrl(url)}
                    className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  <span className="flex-1 min-w-0 text-sm text-gray-700 truncate font-mono text-xs" title={url}>
                    {urlPath(url)}
                  </span>
                  {source === "ga4" && (
                    <span className="shrink-0 text-xs tabular-nums text-gray-500">
                      Total Pageviews: {(pageviewsByUrl[url] ?? 0).toLocaleString()}
                    </span>
                  )}
                  <a
                    href={url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="shrink-0 text-gray-400 hover:text-blue-500"
                    title="Open in new tab"
                  >
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </label>
              ))}

              {/* Manually added or previously saved URLs follow ranked suggestions. */}
              {extraUrls.map((url) => (
                <label
                  key={url}
                  className="flex items-center gap-3 px-3 py-2 hover:bg-gray-50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={selected.has(url)}
                    onChange={() => toggleUrl(url)}
                    className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  <Globe className="h-3.5 w-3.5 shrink-0 text-blue-500" />
                  <span className="flex-1 min-w-0 text-sm text-gray-800 truncate" title={url}>
                    {urlDisplayLabel(url)}
                  </span>
                  <span className="text-xs text-blue-600 font-medium shrink-0">Added</span>
                  <button
                    type="button"
                    onClick={(e) => { e.preventDefault(); removeExtra(url); }}
                    className="ml-1 rounded p-0.5 text-gray-400 hover:text-red-500 hover:bg-red-50"
                    title="Remove"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </label>
              ))}
            </div>
          ) : (
            !error && (
              <p className="rounded-lg border border-dashed border-gray-200 py-8 text-center text-sm text-gray-400">
                No pages discovered yet. Add URLs manually below.
              </p>
            )
          )}

          {/* Add URL input */}
          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-gray-700">
              Add a URL
            </label>
            <div className="flex gap-2">
              <input
                ref={addInputRef}
                type="url"
                value={addInput}
                onChange={(e) => { setAddInput(e.target.value); setAddError(null); }}
                onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addUrl(); } }}
                placeholder="https://example.com/specific-page"
                className="flex-1 min-w-0 rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:ring-1 focus:ring-blue-500 outline-none"
              />
              <button
                type="button"
                onClick={addUrl}
                className="inline-flex items-center gap-1.5 rounded-lg bg-gray-100 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-200"
              >
                <Plus className="h-4 w-4" />
                Add
              </button>
            </div>
            {addError && (
              <p className="text-xs text-red-600">{addError}</p>
            )}
          </div>
        </>
      )}

      {/* Nav */}
      <div className="flex items-center justify-between pt-2 border-t border-gray-100">
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
        >
          Back
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleSkip}
            className="rounded-lg border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-500 hover:bg-gray-50"
          >
            Skip
          </button>
          <button
            type="button"
            onClick={handleContinue}
            disabled={loading}
            className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-5 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-50"
          >
            {loading && <Loader2 className="h-4 w-4 animate-spin" />}
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
