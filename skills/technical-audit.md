# Skill: Technical audit

Use this skill to assess technical setup for both **traditional SEO** and **GEO**.

The audit checks whether important pages are:

- Crawlable
- Indexable
- Canonicalised correctly
- Fast enough
- Secure
- Mobile usable
- Discoverable
- Accessible to non-rendering AI crawlers
- Supported by clean metadata, sitemaps, and freshness signals

For GEO, the most important technical question is:

> Can search engines and AI crawlers fetch meaningful, citation-ready content from the initial HTML or reliably rendered page?

**Primary output:** a structured checklist with **Pass / Partial / Fail / Not applicable**, evidence, severity, and prioritised fixes.

---

## Purpose

Use this skill to answer:

- Can Google, Bing, and AI crawlers access important pages?
- Are pages eligible to be indexed?
- Do canonical and redirect signals consolidate correctly?
- Is meaningful content visible without JavaScript?
- Are pages mobile usable and fast enough?
- Are sitemaps, freshness signals, and discovery paths healthy?
- Are there technical blockers that would prevent AI citation even if content quality is strong?

---

## Pair with

| Skill | Role |
|---|---|
| `ai-crawler-report.md` | Robots access for AI and search crawlers |
| `json-ld.md` | Structured data and entity markup |
| `llms-txt.md` | `llms.txt` discovery and validation |
| `ai-citability.md` | Passage-level citation readiness |
| `ai-search-success.md` | Google AI Search readiness |
| `platform-readiness.md` | Platform-specific citation readiness |
| `create-report.md` | Crawl + HTML report pipeline |
| `create-report.py` | Technical setup category weights subs to §Optional scoring model (eight themes, crawl proxies); performance row merges CWV + mobile + HTTPS where lab/field data unavailable |

---

# Inputs

## Minimum inputs

| Input | Use |
|---|---|
| Base URL | Canonical domain and host checks |
| 5–15 priority URLs | Template/page-level checks |
| HTTP responses | Status, redirects, headers |
| Raw HTML source | No-JS content, metadata, canonical, robots |
| `robots.txt` / `robots_fetched.txt` | Crawl access |
| Sitemap URLs | Discovery and freshness |
| Crawl output | Status codes, page extracts, JSON-LD, OG tags |

## Recommended inputs

| Input | Use |
|---|---|
| Rendered DOM | JS/rendering comparison |
| Google Search Console | Indexing, canonical, coverage, CWV |
| Bing Webmaster Tools | Bing index and crawl status |
| Server logs | Crawler behaviour and errors |
| CrUX / PageSpeed Insights | Field and lab performance |
| Lighthouse | Debugging lab issues |
| Mobile screenshots | Layout and overflow |
| CDN/cache config | TTFB, redirects, headers |
| CMS/template access | Fix planning |

---

# Output

Produce:

1. Executive summary
2. Technical checklist
3. Optional technical score
4. Critical blockers
5. Redirect/canonical map
6. SSR / AI crawler risk summary
7. Core Web Vitals snapshot
8. Discovery/freshness findings
9. Prioritised backlog

---

# Plain-English recommendation map

Use these translations in final reports.

| Technical finding | Client-friendly action |
|---|---|
| Core Web Vitals not measured | Run a Core Web Vitals check to confirm page speed and usability on key templates |
| TTFB slow or unknown | Check server response speed (TTFB) to make sure pages load quickly for users and crawlers |
| Raw HTML missing main content | Make key page content visible in the initial HTML so AI crawlers can read it without relying on JavaScript |
| SPA shell detected | Make sure the main text is available before JavaScript runs, especially on pages you want cited |
| Canonical conflict | Fix canonical signals so search engines know which version of each page is the main one |
| Sitemap missing | Publish a clean sitemap (`sitemap.xml`) listing the important pages you want crawlers to find |
| `lastmod` missing/stale | Add realistic update dates (`lastmod`) to the sitemap where content changes matter |
| Mixed HTTP/HTTPS | Redirect all traffic to the secure HTTPS version of the site |
| Horizontal scroll | Fix mobile layout issues that make pages hard to read |
| `noindex` | Remove accidental `noindex` settings from important pages |

---

## Measurement tasks should not outrank known blockers

If the audit only says a metric was not measured, phrase it as a validation task and do not rank it above known crawl, indexing, or content blockers.

Example:

- **Run a Core Web Vitals check.** Confirm whether page speed and usability issues are affecting users or crawlers before scheduling performance work.

This should usually be Medium-term unless there is already evidence of poor performance.

---

# Status definitions

| Status | Meaning |
|---|---|
| **Pass** | Healthy across sampled priority pages |
| **Partial** | Works in some cases but has gaps or template inconsistency |
| **Fail** | Major issue affecting important pages |
| **Not applicable** | Not relevant to this site |
| **Manual check** | Evidence unavailable from crawl |

---

# Optional scoring model

Use this when the audit needs a numeric technical score.

| Theme | Weight |
|---|---:|
| Crawl and index eligibility | 20 |
| Canonicalisation and duplicate control | 15 |
| Rendering / raw HTML / AI crawler access | 20 |
| Performance and Core Web Vitals | 15 |
| Mobile usability and page experience | 10 |
| Security and HTTPS | 10 |
| Discovery, sitemaps, and freshness | 5 |
| Internationalisation and media accessibility | 5 |
| **Total** | **100** |

## Scoring conversion

| Status | Points awarded |
|---|---:|
| Pass | 100% of theme weight |
| Partial | 50% of theme weight |
| Fail | 0% of theme weight |
| Not applicable | Remove from denominator and rescale |
| Manual check | Do not score unless evidence is available |

---

# Critical caps

Apply these caps after scoring.

| Condition | Maximum technical score |
|---|---:|
| Key pages blocked from Googlebot or Bingbot | 45 |
| Key pages blocked from major AI crawlers | 60 |
| Key pages are `noindex` unintentionally | 50 |
| Main content absent from raw HTML and not reliably renderable | 60 |
| Sitewide canonical points to wrong domain or homepage | 55 |
| Widespread 4xx/5xx on priority URLs | 60 |
| HTTPS invalid or broken | 65 |
| Severe mobile unusability on key templates | 70 |
| Sitemap/discovery missing and internal linking weak | 75 |

State any cap clearly.

---

# Why raw HTML matters for GEO

Many AI crawlers behave more like simple HTTP clients than full browsers. They may fetch a URL and parse the response body without executing JavaScript.

If primary content is injected only after React, Vue, Angular, or another client-side framework runs, a crawler may see only:

```html
<div id="root"></div>
<script src="/app.js"></script>
```

That creates risk for:

- AI citation
- Entity extraction
- Content summarisation
- Structured data discovery
- Link discovery
- Faster indexing

Googlebot can render JavaScript, but rendering is more expensive and may be deferred. Server-rendered or statically generated HTML is safer for both SEO and GEO.

---

# Review workflow

## Step 1: Select URLs

Use a representative set:

| Page type | Include |
|---|---|
| Homepage | Always |
| Core product/service | Yes |
| Pricing | If available |
| Flagship guide | Yes |
| Article/blog template | Yes |
| Category/listing | If ecommerce/publisher |
| Product page | If ecommerce |
| Location page | If local/multi-location |
| Support/help article | If important |
| Faceted/search page | If crawl bloat risk |
| International URL | If multilingual/multiregion |

## Step 2: Fetch raw and rendered content

For each URL, collect:

- Final URL
- Status code
- Redirect chain
- Response headers
- Raw HTML
- Rendered text, if available
- Canonical
- Meta robots
- H1/title/meta description
- JSON-LD
- Main content snippet

## Step 3: Check crawl/index rules

Use `robots.txt`, meta robots, and headers.

## Step 4: Check canonical and duplicate signals

Review host/scheme, canonical tags, redirects, parameters.

## Step 5: Check rendering and raw HTML

Compare raw HTML to rendered page.

## Step 6: Check performance, mobile, security

Use CrUX/PSI/Lighthouse where available.

## Step 7: Check discovery and freshness

Review sitemaps, feeds, `lastmod`, internal links, IndexNow where relevant.

## Step 8: Produce actions

Prioritise blockers first.

---

# Theme 1: Crawl and index eligibility

## Intent

Important pages must be fetchable and eligible for indexing.

## Checks

| Check | Pass | Fail / investigate |
|---|---|---|
| HTTP status | 200 for canonical pages | 4xx, 5xx, soft 404s |
| Redirects | Intentional redirects to canonical URL | Loops, chains, wrong destination |
| Robots.txt | Important paths not blocked | `Disallow` blocks key content |
| Meta robots | No accidental `noindex` | `noindex` on pages intended to rank/cite |
| X-Robots-Tag | No accidental `noindex` headers | Header-level `noindex` on HTML/PDF |
| Auth/login walls | Public content accessible | Key content requires login |
| Soft 404s | Empty pages not indexed | Thin “no results” pages return 200 |
| Internal links | Key pages linked in crawlable HTML | Pages only reachable via search/forms/JS |

## Evidence to capture

| Evidence | Example |
|---|---|
| Status code | `200 OK` / `404` / `500` |
| Robots rule | `User-agent: * Disallow: /blog/` |
| Meta robots | `<meta name="robots" content="noindex">` |
| Header | `X-Robots-Tag: noindex` |
| URL Inspection | Google-selected canonical, indexing status |

---

# Theme 2: Canonicalisation and duplicate control

## Intent

Each logical page should have one preferred URL so signals consolidate cleanly.

## Checks

| Check | Pass | Fail / investigate |
|---|---|---|
| Canonical tag | Self-referencing or correct consolidation | Missing, wrong, unrelated |
| Host consistency | Chosen host used everywhere | `www` and apex both 200 |
| Scheme consistency | HTTP redirects to HTTPS | HTTP remains indexable |
| Slash/case policy | Consistent canonical/redirect | Mixed 200s for variants |
| Parameters | Tracking/facet URLs controlled | Self-canonical parameter duplicates |
| Pagination/facets | Intentional index/noindex/canonical logic | Crawl traps or duplicate indexation |
| hreflang/canonicals | Compatible | Hreflang points to non-canonical URLs |

## Quick host/scheme probes

Test:

```text
http://example.com/
https://example.com/
http://www.example.com/
https://www.example.com/
```

Record:

| Variant | Status | Final URL | Notes |
|---|---:|---|---|

## Common fixes

- 301 alternate hosts to preferred host.
- Use HTTPS as canonical.
- Make canonicals absolute and self-referencing where appropriate.
- Remove self-canonicals from parameter duplicates.
- Normalise trailing slash and case.
- Avoid canonicalising many distinct pages to homepage.

---

# Theme 3: Rendering, raw HTML, and AI crawler access

## Intent

Critical content should exist in initial HTML or be reliably renderable.

## No-JS test

```bash
curl -sL -A "Mozilla/5.0 (compatible; TechnicalAudit/1.0)" "https://example.com/path" | head -n 120
```

Also test text extraction:

```bash
curl -sL "https://example.com/path" | grep -i "expected phrase"
```

## Evaluate

| Check | Pass | Fail |
|---|---|---|
| `<title>` | Present in initial HTML | Missing until JS |
| Meta description | Present | Missing/generic |
| Canonical | Present in initial HTML | Injected later only |
| Meta robots | Present if used | Injected later only |
| H1 | Present in raw HTML | JS-only |
| Main content | Substantive text in raw HTML | Empty shell |
| Links | Crawlable anchors in HTML | JS-only navigation |
| JSON-LD | Present in HTML if used | Injected late/unreliably |
| Images/alt | Key images and alt present | JS-only media |
| FAQ/pricing/specs | Present in HTML | Widget-only |

## Risk levels

| Risk | Meaning |
|---|---|
| Low | Core content present in raw HTML or stable SSR/SSG |
| Medium | Some secondary content JS-only; main content available |
| High | Main content absent from raw HTML |
| Critical | Empty shell plus blocked/failed rendering or no fallback |

## Recommended fixes

- Server-side rendering
- Static site generation
- Hybrid rendering with critical content in HTML
- Pre-render important pages where appropriate
- Render schema and canonical in initial HTML
- Make navigation links real anchors
- Mirror widget content in accessible HTML
- Avoid cloaked bot-only content; keep content consistent for users and crawlers

---

# Theme 4: Performance and Core Web Vitals

## Intent

Fast pages improve user satisfaction and crawl efficiency.

## Metrics

| Metric | Good target | Meaning |
|---|---:|---|
| LCP | ≤2.5s | Loading performance |
| INP | ≤200ms | Responsiveness |
| CLS | ≤0.1 | Visual stability |
| TTFB | Ideally <800ms | Server/CDN responsiveness |

Use field data where possible.

## Data sources

| Source | Use |
|---|---|
| CrUX | Real-user field data |
| PageSpeed Insights | Field + lab snapshot |
| Lighthouse | Lab debugging |
| WebPageTest | Waterfall and geography |
| Server logs/CDN | Backend latency and cache |

## TTFB command

```bash
curl -w "TTFB: %{time_starttransfer}s\nTotal: %{time_total}s\n" -o /dev/null -sL https://example.com/
```

Run multiple times and record region/tool caveats.

## Common fixes

- Improve caching and CDN coverage.
- Optimise server rendering and database queries.
- Preload hero image and critical fonts.
- Compress/resize images.
- Reduce render-blocking JS/CSS.
- Defer non-critical scripts.
- Reserve dimensions for images/ad slots.
- Reduce third-party script weight.

---

# Theme 5: Mobile usability and page experience

## Intent

Users should be able to consume the main content comfortably on mobile.

## Checks

| Check | Pass | Fail |
|---|---|---|
| Viewport meta | Correct viewport in HTML head | Missing/fixed-width/user-scalable disabled |
| Responsive layout | Content fits narrow widths | Horizontal scroll on body |
| Tap targets | Links/buttons usable | Tiny or overlapping controls |
| Font/readability | Text readable | Tiny text, poor contrast |
| Intrusive interstitials | No blocking popups | Popups obscure content |
| Content parity | Mobile has same core content | Important content desktop-only |
| Navigation | Usable and crawlable | JS-only or inaccessible nav |
| Tables/media | Responsively handled | Wide tables break layout |

## Viewport example

```html
<meta name="viewport" content="width=device-width, initial-scale=1">
```

Flag `user-scalable=no` unless there is a justified accessibility review.

---

# Theme 6: Security and HTTPS

## Intent

The public site should be secure and trustworthy.

## Checks

| Check | Pass | Fail |
|---|---|---|
| HTTPS | Valid certificate | Expired/wrong cert |
| HTTP redirect | HTTP → HTTPS | HTTP 200 indexable |
| HSTS | Present where appropriate | Missing on mature sites |
| Mixed content | No HTTP subresources | Active/passive mixed content |
| Security headers | Sensible baseline | Missing where risk is high |
| Malware/safe browsing | No warnings | Browser/security warnings |

Security headers to note:

- `Strict-Transport-Security`
- `Content-Security-Policy`
- `X-Content-Type-Options`
- `Referrer-Policy`
- `Permissions-Policy`

Do not over-prioritise advanced headers over crawl/index blockers unless security risk is material.

---

# Theme 7: Discovery, sitemaps, and freshness

## Intent

Crawlers should find important pages and understand what changed.

## Checks

| Check | Pass | Fail / investigate |
|---|---|---|
| XML sitemap | Exists and reachable | Missing/broken |
| Sitemap content | Includes canonical key pages | Junk, redirects, noindex URLs |
| Sitemap index | Organised for large sites | Incomplete/invalid |
| `lastmod` | Present and plausible | All identical/stale/fake |
| Robots sitemap directive | Sitemap listed in robots.txt | Missing, if discovery weak |
| Internal links | Key pages crawlable | Orphaned pages |
| Feeds | RSS/Atom for publishers | Missing where useful |
| IndexNow | Implemented where Bing/Copilot freshness matters | Missing for fast-changing sites |
| Last-Modified/ETag | Present where useful | Not required but helpful |
| Stale content | Old claims flagged | Outdated prices/laws/stats |

Technical freshness signals belong here. Editorial accuracy belongs in `eeat.md`.

---

# Theme 8: Index bloat and crawl efficiency

## Intent

Search and AI systems should spend crawl resources on useful, unique pages.

## Checks

| Signal | Healthy | Risk |
|---|---|---|
| Indexed vs sitemap | Roughly aligned | Huge index count vs useful pages |
| Facets/sorts | Controlled | Parameter crawl traps |
| Internal search pages | Noindexed/blocked appropriately | Indexed search result pages |
| Tag/archive pages | Useful or noindexed | Thin duplicate archives |
| Pagination | Clear and crawlable | Infinite crawl loops |
| Soft 404s | Not indexed | Empty pages return 200 |
| Duplicate templates | Consolidated | Many near-identical pages |

Use Search Console and logs where possible.

---

# Theme 9: Internationalisation and localisation

Use if the site targets multiple regions/languages.

## Checks

| Check | Pass | Fail |
|---|---|---|
| `hreflang` | Correct language/region pairs | Missing/invalid/return errors |
| Canonical compatibility | Canonicals point to same-language version | Canonical conflicts with hreflang |
| x-default | Used where appropriate | Missing for global selector |
| Localised content | Truly localised | Machine-swapped placeholders |
| Currency/units | Region appropriate | Mixed currency/units |
| Local schema | Correct local business details | Wrong NAP/service area |
| Language consistency | Page language coherent | Mixed templates |

---

# Theme 10: Media accessibility and multimodal readiness

## Intent

Images, video, and media should be accessible, crawlable, and understandable.

## Checks

| Check | Pass | Fail |
|---|---|---|
| Image alt | Meaningful for informative images | Missing/keyword-stuffed |
| Image dimensions | Width/height set | Layout shifts |
| Filenames | Descriptive where useful | Opaque filenames only |
| Image crawlability | Important images accessible | Blocked/hotlink protected |
| Video metadata | Title/description available | Generic embeds |
| Captions/transcripts | Present where video is important | Missing |
| Charts/diagrams | Data also in text/table | Image-only facts |
| Open Graph image | Present and representative | Missing/generic |
| Structured media | `VideoObject` where relevant | Missing on video pages |

---

# Deliverable template

```markdown
## Technical audit — {domain}

**Scope:** {number} URLs/templates reviewed  
**Inputs:** {crawl output, robots, raw HTML, rendered DOM, GSC, PSI, CrUX}  
**Score:** {score}/100, if scored  

### Executive summary

{2–4 sentences. State major blockers, SSR/AI crawler risk, and top fixes.}

### Checklist

| Theme | Status | Severity | Evidence | Fix |
|---|---|---|---|---|
| Crawl and index eligibility | Pass / Partial / Fail | P0–P3 | | |
| Canonicalisation and duplicate control | Pass / Partial / Fail | P0–P3 | | |
| Rendering / raw HTML / AI crawler access | Pass / Partial / Fail | P0–P3 | | |
| Performance and Core Web Vitals | Pass / Partial / Fail | P0–P3 | | |
| Mobile usability and page experience | Pass / Partial / Fail | P0–P3 | | |
| Security and HTTPS | Pass / Partial / Fail | P0–P3 | | |
| Discovery, sitemaps, and freshness | Pass / Partial / Fail | P0–P3 | | |
| Index bloat and crawl efficiency | Pass / Partial / Fail | P0–P3 | | |
| Internationalisation/localisation | Pass / Partial / Fail / N/A | P0–P3 | | |
| Media accessibility/multimodal | Pass / Partial / Fail / N/A | P0–P3 | | |

### Redirect and canonical map

| Test URL | Status | Final URL | Canonical | Notes |
|---|---:|---|---|---|
| `http://example.com/` | | | | |
| `https://example.com/` | | | | |
| `http://www.example.com/` | | | | |
| `https://www.example.com/` | | | | |

### SSR / AI crawler risk

**Risk level:** Low / Medium / High / Critical

Evidence:

~~~html
{raw HTML snippet or extracted text showing presence/absence of main content}
~~~

### Core Web Vitals snapshot

| Metric | Field result | Lab result | Notes |
|---|---:|---:|---|
| LCP | | | |
| INP | | | |
| CLS | | | |
| TTFB | | | |

### Priority backlog

| Priority | Issue | Impact | Recommended fix | Owner |
|---|---|---|---|---|
| P0 | | | | |
| P1 | | | | |
| P2 | | | | |
| P3 | | | | |
```

---

# Severity definitions

| Severity | Meaning |
|---|---|
| **P0** | Blocks indexing/crawling or creates severe GEO visibility risk |
| **P1** | Major issue affecting key templates or many important URLs |
| **P2** | Moderate issue affecting performance, usability, or consistency |
| **P3** | Low-risk improvement or hygiene fix |

---

# Common recommendations

## JS-only content

```markdown
Move critical page content into server-rendered or statically generated HTML. At minimum, ensure the H1, core answer text, canonical, meta robots, internal links, and JSON-LD are present in the initial HTML response.
```

## Accidental noindex

```markdown
Remove `noindex` from pages intended to rank or be cited. Confirm both meta robots and `X-Robots-Tag` headers.
```

## Canonical conflict

```markdown
Align redirects, canonicals, sitemap URLs, internal links, and hreflang so they all reference the same preferred URL.
```

## Slow TTFB

```markdown
Investigate CDN cache misses, origin response time, database queries, and dynamic rendering bottlenecks. Add caching for public pages where possible.
```

## Missing sitemap freshness

```markdown
Publish a clean XML sitemap with canonical priority URLs and plausible `lastmod` values. Declare it in `robots.txt`.
```

## Mobile horizontal scroll

```markdown
Fix fixed-width elements, wide tables, and oversized images. Use responsive CSS and bounded horizontal scrolling only for tables where necessary.
```

---

# Limitations

- `curl` approximates non-rendering crawlers but does not exactly match any one AI crawler.
- Googlebot can render JavaScript, but rendering may be delayed and resource-dependent.
- Index bloat analysis needs Search Console or log data for confidence.
- Core Web Vitals should use field data where possible.
- Mobile layout issues require real-device or browser testing.
- Some issues are template-specific and require broader crawl validation.