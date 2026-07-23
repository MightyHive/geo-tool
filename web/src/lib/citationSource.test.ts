import { describe, expect, it } from "vitest";
import {
  aggregateCitationSitesFromPrompts,
  aggregateCitationUrlsFromPrompts,
  buildCitationEligibleDomainPredicate,
  buildInformationSourceDomainPredicate,
  collectPromptCitationSources,
  hasPagePath,
  isPromptCitationSource,
  isPromptVendorCitation,
  promptCitationDomainSet,
  registrableDomain,
} from "./citationSource";
import { buildCompetitorCitationMatcher } from "./localeProbeView";
import type { PromptPerformanceContext } from "../types";

function makeCtx(overrides: Partial<PromptPerformanceContext> = {}): PromptPerformanceContext {
  return {
    brand_name: "Acme",
    brand_site_url: "https://acme.com",
    use_pss: false,
    pss_rows: [],
    flat_prompts: [],
    prompt_count: 0,
    competitors: [
      { competitor_brand: "Rival", competitor_website: "https://rival.com" },
      { competitor_brand: "OtherCo", competitor_website: "https://otherco.com" },
    ],
    primary_market: { country: "UK", country_id: "GB" },
    category_labels: [],
    industry: "",
    live_probe: {
      per_prompt: [],
      top_cited_sites: [
        { domain: "nytimes.com", count: 3, platforms: ["chatgpt"] },
        { domain: "rival.com", count: 2, platforms: ["chatgpt"] },
        { domain: "boots.com", count: 4, platforms: ["chatgpt"] },
        { domain: "acme.com", count: 1, platforms: ["chatgpt"] },
      ],
    },
    highlight: { brand: "Acme", competitor_urls: [], competitor_brands: [] },
    ...overrides,
  };
}

describe("citationSource predicates", () => {
  it("allows cited competitors in the Citations list but not brand/vendors", () => {
    const eligible = buildCitationEligibleDomainPredicate(makeCtx());
    expect(eligible("nytimes.com")).toBe(true);
    expect(eligible("rival.com")).toBe(true);
    expect(eligible("acme.com")).toBe(false);
    expect(eligible("boots.com")).toBe(false);
  });

  it("information-source summary still excludes competitors", () => {
    const info = buildInformationSourceDomainPredicate(makeCtx());
    expect(info("nytimes.com")).toBe(true);
    expect(info("rival.com")).toBe(false);
    expect(info("boots.com")).toBe(false);
  });

  it("isPromptCitationSource matches eligibility (vendors/brand out, competitors in)", () => {
    const ctx = makeCtx();
    expect(isPromptCitationSource({ domain: "nytimes.com" }, ctx)).toBe(true);
    expect(isPromptCitationSource({ domain: "rival.com", competitor_cited: true }, ctx)).toBe(true);
    expect(isPromptCitationSource({ domain: "boots.com" }, ctx)).toBe(false);
    expect(isPromptCitationSource({ domain: "acme.com" }, ctx)).toBe(false);
    expect(isPromptVendorCitation({ domain: "boots.com" })).toBe(true);
  });
});

describe("prompt Citations ↔ Citations page alignment", () => {
  const perPrompt = [
    {
      prompt: "Best moisturiser?",
      citations_openai: [
        {
          url: "https://www.nytimes.com/wirecutter/skin",
          domain: "nytimes.com",
          title: "Wirecutter",
        },
        {
          url: "https://www.rival.com/research/study",
          domain: "rival.com",
          title: "Rival research",
          competitor_cited: true,
        },
        {
          url: "https://www.boots.com/skin",
          domain: "boots.com",
          title: "Boots",
        },
        {
          url: "https://acme.com/blog",
          domain: "acme.com",
          title: "Acme blog",
          brand_cited: true,
        },
      ],
      citations_gemini: [
        {
          url: "https://www.dermnetnz.org/topics/eczema",
          domain: "dermnetnz.org",
        },
      ],
      mention_scores_openai: {
        brand_signal: 1,
        competitors_combined_hits: 1,
        competitor_detail: { Rival: 1 },
      },
      mention_scores_gemini: { brand_signal: 0, competitors_combined_hits: 0, competitor_detail: {} },
    },
    {
      prompt: "Where to buy?",
      // Recommended-only competitor is NOT in citations_* (backend merge drops bare domains).
      openai_response: "Also try otherco.com for beginners.",
      citations_openai: [],
    },
  ];

  it("prompt citation domains ⊆ Citations page aggregate set", () => {
    const ctx = makeCtx();
    const promptDomains = promptCitationDomainSet(perPrompt, ctx);
    const pageDomains = new Set(
      aggregateCitationSitesFromPrompts(perPrompt, ctx).map((s) => registrableDomain(s.domain)),
    );

    expect(promptDomains.size).toBeGreaterThan(0);
    for (const domain of promptDomains) {
      expect(pageDomains.has(domain)).toBe(true);
    }
    expect(pageDomains).toEqual(promptDomains);
  });

  it("does not include recommended-only domains that never appear in citations_*", () => {
    const ctx = makeCtx();
    const domains = promptCitationDomainSet(perPrompt, ctx);
    expect(domains.has("otherco.com")).toBe(false);
    expect(
      collectPromptCitationSources(perPrompt, ctx).some((c) => c.domain === "otherco.com"),
    ).toBe(false);
  });

  it("includes a competitor only when cited in prompts; labels via matcher", () => {
    const ctx = makeCtx();
    const match = buildCompetitorCitationMatcher(ctx);
    const sites = aggregateCitationSitesFromPrompts(perPrompt, ctx);
    const domains = sites.map((s) => s.domain);

    expect(domains).toContain("rival.com");
    expect(domains).toContain("nytimes.com");
    expect(domains).toContain("dermnetnz.org");
    expect(domains).not.toContain("otherco.com"); // configured competitor, never cited
    expect(domains).not.toContain("boots.com"); // vendor
    expect(domains).not.toContain("acme.com"); // brand

    expect(match("rival.com").isCompetitor).toBe(true);
    expect(match("nytimes.com").isCompetitor).toBe(false);
  });

  it("aggregates URLs from the same prompt citation list", () => {
    const ctx = makeCtx();
    const urls = aggregateCitationUrlsFromPrompts(perPrompt, ctx).map((u) => u.url);
    expect(urls).toContain("https://www.nytimes.com/wirecutter/skin");
    expect(urls).toContain("https://www.rival.com/research/study");
    expect(urls).toContain("https://www.dermnetnz.org/topics/eczema");
    expect(urls.some((u) => u.includes("otherco.com"))).toBe(false);
    expect(urls.some((u) => u.includes("boots.com"))).toBe(false);
  });

  it("Top URLs keep only paths — exclude bare domains and homepages", () => {
    const ctx = makeCtx();
    const mixed = [
      {
        prompt: "Sources?",
        citations_openai: [
          { url: "https://example.com/guides/foo", domain: "example.com" },
          { url: "https://example.com", domain: "example.com" },
          { url: "https://example.com/", domain: "example.com" },
          { url: "https://news.site/article/1", domain: "news.site" },
          { url: "rival.com", domain: "rival.com" },
        ],
      },
    ];
    const urls = aggregateCitationUrlsFromPrompts(mixed, ctx).map((u) => u.url);
    expect(urls).toContain("https://example.com/guides/foo");
    expect(urls).toContain("https://news.site/article/1");
    expect(urls).not.toContain("https://example.com");
    expect(urls).not.toContain("https://example.com/");
    expect(urls.some((u) => u === "rival.com" || u === "https://rival.com")).toBe(false);

    // Domains table still includes domain-level aggregates from path citations.
    const sites = aggregateCitationSitesFromPrompts(mixed, ctx).map((s) => s.domain);
    expect(sites).toContain("example.com");
    expect(sites).toContain("news.site");
  });

  it("hasPagePath distinguishes page paths from homepages", () => {
    expect(hasPagePath("https://example.com/guides/foo")).toBe(true);
    expect(hasPagePath("https://example.com")).toBe(false);
    expect(hasPagePath("https://example.com/")).toBe(false);
    expect(hasPagePath("example.com/path")).toBe(true);
    expect(hasPagePath("example.com")).toBe(false);
  });

  it("Overall merge: locale per_prompt rows union into one Citations set", () => {
    const ctx = makeCtx();
    const localeA = [
      {
        prompt: "A",
        _locale_key: "uk:en",
        citations_openai: [{ url: "https://bbc.co.uk/news", domain: "bbc.co.uk" }],
      },
    ];
    const localeB = [
      {
        prompt: "B",
        _locale_key: "us:en",
        citations_openai: [
          { url: "https://www.rival.com/us", domain: "rival.com", competitor_cited: true },
        ],
      },
    ];
    const overall = [...localeA, ...localeB];
    const domains = promptCitationDomainSet(overall, ctx);
    expect(domains.has("bbc.co.uk")).toBe(true);
    expect(domains.has("rival.com")).toBe(true);
    expect(aggregateCitationSitesFromPrompts(overall, ctx).map((s) => s.domain).sort()).toEqual(
      Array.from(domains).sort(),
    );
  });
});
