import type {
  LiveProbeResult,
  PageAuditDetail,
  PromptPerformanceContext,
} from "../types";

export const PAGE_PROMPT_LOCALE_KEY = "page";

export function pageLiveProbe(audit: PageAuditDetail): LiveProbeResult | null {
  const probe = audit.page_probe;
  if (!probe || typeof probe !== "object") return null;
  const nested = (probe as { live_probe?: unknown }).live_probe;
  const live = nested && typeof nested === "object" ? nested : probe;
  const rows = (live as LiveProbeResult).per_prompt;
  if (!Array.isArray(rows) || rows.length === 0) return null;
  return live as LiveProbeResult;
}

export function pageAuditPromptContext(audit: PageAuditDetail): {
  ctx: PromptPerformanceContext;
  live: LiveProbeResult | null;
  topicLabel: string;
} {
  const live = pageLiveProbe(audit);
  const topicLabel = (audit.title || "").trim() || "This page";
  const brand = (audit.parent_brand_name || "").trim();
  const site = (audit.parent_base_url || "").trim();
  const competitors = (audit.parent_competitors ?? []).filter(
    (row) => row.competitor_brand || row.competitor_website,
  );
  const prompts = (audit.page_prompts?.prompts ?? []).filter(Boolean);
  const ctx: PromptPerformanceContext = {
    brand_name: brand,
    brand_site_url: site,
    use_pss: false,
    pss_rows: [],
    flat_prompts: prompts,
    prompt_count: prompts.length || live?.per_prompt?.length || 0,
    competitors,
    primary_market: { country: "", country_id: "" },
    category_labels: [topicLabel],
    industry: "",
    live_probe: live,
    highlight: {
      brand,
      competitor_urls: competitors.map((row) => row.competitor_website).filter(Boolean),
      competitor_brands: competitors.map((row) => row.competitor_brand).filter(Boolean),
      brand_match_tokens: live?.brand_match_tokens ?? [],
    },
  };
  return { ctx, live, topicLabel };
}
