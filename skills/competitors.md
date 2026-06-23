# Skill: Competitor crawl & comparison

Use this skill to audit a **primary site** against up to **three user-specified competitors** using the same crawl settings, then produce a side-by-side GEO readiness comparison.

The purpose is not to determine who ranks highest. The purpose is to identify whether competitors have stronger **AI/discoverability hygiene**, **technical access**, **structured data**, **entity signals**, **content patterns**, and **AI-ready publishing infrastructure** than the primary site.

**Implementation:** use `create-report.py` for crawl + HTML reports, or `crawl-site.py` for crawl-only runs.

---

## Purpose

Use this skill to answer:

- How does the primary site compare with named competitors on GEO foundations?
- Are competitors easier for crawlers and AI systems to understand?
- Do competitors publish better structured data, social/entity links, or AI guidance files?
- Are competitors more consistent with `sameAs`, `og:image`, sitemap, or JSON-LD usage?
- Do competitors have stronger citation-worthy content templates?
- Which gaps should the primary site close first?

---

## Output readability rules (`report.html` / `comparison.md`)

Competitor findings must read clearly for non-technical stakeholders.

Each **automated** peer advantage in the HTML report is structured as:

1. **Area** — AI visibility, Technical setup, or Content quality & structure (plus optional notes for structured data, previews, `llms.txt`, `sameAs`).
2. **Takeaway** — what appears stronger in the crawl sample (directional, score-supported).
3. **Why** — which sub-metrics drove the gap in this run.
4. **Confidence** — only when needed (listing/category skew, SSR listing caveat, JSON-LD vs narrative content).
5. **Recommended action** — a short next step for the primary site.

Do **not** concatenate caveats, methodology, and scores into one long sentence. Keep confidence and actions in their own lines (the renderer enforces this).

---

## Evidence URL quality rules

Do not show competitor example URLs unless they are defensible evidence. The `create-report.py` renderer filters pages using crawl fields:

- HTTP **200** on the stored fetch.
- **Not** a soft 404: suspicious `page_title` markers (e.g. “404”, “not found”, “page not found”, common “sorry” variants).
- Path not shaped like an error page (`/404`, `/error`, `/not-found`, …).
- **Not** `noindex` when `x-robots-tag` or robots meta values are present on the page record.
- **Not** a cross-host redirect (requested URL host vs `final_url` host).
- **Not** a deep URL that resolves only to the origin path in `final_url` when `final_url` is available (weak evidence).
- **Deduped** in the picker.

If nothing qualifies, the UI shows: *“No reliable example URL was available from the crawl sample; verify manually before acting on this comparison.”*

`crawl-site.py` records `final_url` on successful page fetches so the report can apply redirect checks.

---

## Page-type and automation confidence

| Situation | Typical confidence / wording |
|-----------|------------------------------|
| Like-for-like templates with valid 200 examples | Medium–high (still automated) |
| Category / listing pages driving AI-style gaps | **Low–medium**; citability is directional |
| High SSR / listing technical scores | **Medium**; extractability ≠ quotable answer content |
| Broader JSON-LD on peer sample | **Medium**; compare equivalent templates before claiming full content-quality wins |
| No valid example URLs after filtering | State explicitly; never show broken or irrelevant links |

Prefer **“appears stronger”**, **“scores higher in the automated sample”**, and **“directional”** over absolute language.

---

## Renderer alignment

`create-report.py` implements:

- Structured **Takeaway / Why / Confidence / Recommended action** blocks for category gaps.
- The column title **“Where this competitor appears stronger”** (and **“Where your site appears ahead”** for the inverse column).
- Evidence link label **“Evidence examples:”** with human-readable link text (not raw URLs alone).
- Softer red lines (“Your site scores higher…”) instead of “Weaker…”.

When you hand-write `comparison.md`, mirror the same tone and structure.

---

## When to use

Use this skill for:

- GEO competitor benchmarking
- Sales pitches and QBRs
- Migration before/after comparisons
- Technical discovery audits
- Content strategy benchmarking
- Schema and entity footprint comparisons
- AI crawler / `llms.txt` gap analysis
- Identifying “table-stakes” signals competitors already have

Do not use this skill as a standalone ranking, traffic, backlink, or market-share analysis.

---

## Inputs

Required:

| Input | Description |
|---|---|
| Primary site URL | The audited client/domain |
| 1–3 competitor URLs | User-specified competitor origins |

Recommended:

| Input | Use |
|---|---|
| Brand name for primary and competitors | Improves brand/entity checks |
| Target country/market | Avoids comparing against wrong regional sites |
| Industry/category | Helps interpret schema/content expectations |
| Priority URL types | Ensures fair page-template comparison |
| Target queries/topics | Supports content and citability comparisons |
| Crawl limits | Keep crawl scope consistent |
| Known exclusions | Avoid staging, support portals, app subdomains, etc. |

---

## Competitor selection guidance

Use competitors supplied by the user, but sanity-check whether each is comparable.

Good competitors are usually:

- Same market/category
- Similar product/service
- Similar target audience
- Same or comparable country/region
- Similar business model
- Sites users would plausibly compare during selection

Flag weak comparisons when:

- One site is a marketplace and another is a local provider
- One site is a publisher and another is a SaaS vendor
- One site targets a different country/language
- One site is a parent company rather than a product site
- One site is a support/app subdomain rather than the marketing site
- Competitor blocks crawlers or fails fetches, making comparison incomplete

---

## Structured data (JSON-LD) comparisons

- **Do not infer sitewide schema winners** from a single opaque score or one lucky URL in the crawl sample.
- **Compare equivalent templates** where possible: homepage vs homepage, product PDP vs product PDP, category hub vs category hub, article/guide vs article/guide.
- **Organization vs WebSite:** one site may lead on `Organization` + `sameAs` (entity links); another on `WebSite` + `SearchAction` (sitewide search). Report those as *different strengths*, not as “has schema” vs “does not”.
- **Unequal samples:** if the sitemap/GA4 merge surfaces different mixes of URLs, call that out—directional language is appropriate.
- Prefer evidence from **`jsonld/*.json`** plus manual checks over crawl-only booleans when stakes are high.

---

## AI citability & AI visibility comparisons

- **Compare like-for-like templates** when you infer citability or “query footprint” from crawls: homepage vs homepage, PDP vs PDP, category hub vs category hub, guide vs guide, support article vs support article.
- **Category and listing URLs** can score well on technical proxies (SSR/raw HTML, depth, previews) without being highly **citable**—call competitor advantages **directional** unless manual passage review confirms direct answers, buying guidance, or FAQs.
- When sample pages look like **product grids** with little editorial prose, label citability conclusions **low–medium confidence** and recommend spot-checking 2–3 passages on each URL.
- **Raw HTML completeness** for catalogue pages benefits **Technical setup** and crawl access; it should not be read as proof of **answer-style** content quality for AI citations.

---

# How to run

Recommended:

```bash
python3 create-report.py "https://yoursite.com" \
  --competitor "https://competitor-a.com" \
  --competitor "https://competitor-b.com" \
  --competitor "https://competitor-c.com" \
  --out audit_output
```

Crawl only:

```bash
python3 crawl-site.py "https://yoursite.com" \
  --competitor "https://competitor-a.com" \
  --competitor "https://competitor-b.com" \
  --out audit_output
```

Notes:

- Primary URL is the positional argument.
- Add `--competitor URL` up to three times.
- Duplicates of the primary origin are skipped.
- All sites use the same crawl settings:
  - `--max-sitemap-urls`
  - `--max-sitemaps`
  - `--delay`
  - TLS options
  - sample/template paths
  - robots and `llms.txt` templates

---

# Recommended crawl settings

Use consistent crawl settings across all sites.

Example for a lightweight comparison:

```bash
python3 create-report.py "https://primary.com" \
  --competitor "https://competitor-a.com" \
  --competitor "https://competitor-b.com" \
  --competitor "https://competitor-c.com" \
  --max-sitemap-urls 50 \
  --max-sitemaps 3 \
  --delay 0.5 \
  --out audit_output
```

For a deeper comparison:

```bash
python3 create-report.py "https://primary.com" \
  --competitor "https://competitor-a.com" \
  --competitor "https://competitor-b.com" \
  --competitor "https://competitor-c.com" \
  --max-sitemap-urls 200 \
  --max-sitemaps 5 \
  --delay 1 \
  --out audit_output
```

---

# Output layout

Everything is stored under the primary audit folder.

```text
audit_output/{primary_host}_{hash}/
  audit_summary.json
  report.html
  report_slides.html
  robots.txt
  robots_fetched.txt
  llms.txt
  llms_fetched.txt
  json-ld.txt
  jsonld/
  og_images/
  comparison.md
  comparison.json
  competitors/
    {competitor_host}_{hash}/
      audit_summary.json
      report.html
      robots.txt
      robots_fetched.txt
      llms.txt
      llms_fetched.txt
      json-ld.txt
      jsonld/
      og_images/
```

Each competitor gets a full audit with the same artifact set as the primary site.

---

# What the automated comparison contains

`comparison.md` and `comparison.json` summarise per site:

| Metric | Meaning |
|---|---|
| Robots fetched | Live `/robots.txt` returned usable content |
| `llms.txt` live | `/llms.txt` or `/.well-known/llms.txt` returned usable content |
| Pages crawled | URLs in crawl batch |
| HTTP 200 | Pages returning success |
| Pages with JSON-LD | Count of pages with at least one JSON-LD block |
| Pages with `og:image` | Count of pages with Open Graph image meta |
| Any JSON-LD | Site-level flag |
| Any `og:image` | Site-level flag |
| `sameAs` count | Distinct `sameAs` URLs found across scanned pages |
| Robots template groups added | Suggested groups merged from sample/template robots file |
| Brand visibility, if enabled | Four-platform visibility summary where available |

These are **coverage and hygiene signals**, not ranking proof.

---

# Additional comparison dimensions

The automated comparison is useful but should be extended manually for a fuller GEO benchmark.

## 1. AI crawler access

Compare:

- `Googlebot`
- `Bingbot`
- `OAI-SearchBot`
- `ChatGPT-User`
- `GPTBot`
- `ClaudeBot`
- `PerplexityBot`
- `Google-Extended`
- `Applebot`
- `CCBot` and other training crawlers if relevant

Output:

| Site | Tier 1 AI crawlers allowed | Googlebot/Bingbot allowed | Blanket blocks? | Notes |
|---|---:|---:|---|---|

Use `ai-crawler-report.md` for the detailed method.

---

## 2. `llms.txt` quality

Compare:

- Present or absent
- Correct markdown structure
- Links to important pages
- Freshness
- Useful summaries
- Canonical URLs
- Whether file is live or only generated by audit

Output:

| Site | `llms.txt` status | Quality | Main gap |
|---|---|---|---|

Use `llms-txt.md` for detailed scoring.

---

## 3. Structured data and entity markup

Compare:

- Organization schema
- WebSite schema
- Breadcrumb schema
- Article schema
- Product/Offer schema
- LocalBusiness schema
- FAQPage where appropriate
- VideoObject where appropriate
- `sameAs` coverage
- Validation errors
- Schema consistency with visible content

Output:

| Site | JSON-LD coverage | Key schema types | `sameAs` quality | Issues |
|---|---|---|---|---|

Use `json-ld.md` for deeper review.

---

## 4. Brand/entity visibility

Compare:

- Wikipedia/Wikidata presence
- YouTube channel
- LinkedIn company page
- Reddit discussion or official presence
- Review platforms
- Industry directories
- Press/news
- Partner pages

Output:

| Site | Entity clarity | Strongest third-party source | Weakest gap | Risk |
|---|---|---|---|---|

Use `brand-visbility.md`.

---

## 5. Content citability

Compare representative page types, not random pages.

Recommended page types:

- Homepage
- Core product/service page
- Pricing page
- Comparison page
- Flagship guide
- Support/help article
- Local page, if relevant
- Product category page, if ecommerce

Output:

| Site | Best answer block | Citability score | Main content advantage | Main weakness |
|---|---|---:|---|---|

Use `ai-citability.md`.

---

## 6. Google AI Search readiness

Compare:

- Unique content
- Googlebot access
- Preview controls
- Structured data
- Page experience
- Multimodal readiness
- Visit-quality measurement

Output:

| Site | Google AI Search readiness | Key advantage | Key blocker |
|---|---:|---|---|

Use `ai-search-success.md`.

---

## 7. Multimodal and preview signals

Compare:

- `og:image`
- High-quality images
- Alt text
- Video embeds
- Video schema
- Transcripts
- Product images
- YouTube integration

Output:

| Site | `og:image` coverage | Video readiness | Image/alt quality | Notes |
|---|---:|---|---|---|

---

# Recommended competitor scorecard

Use this scorecard when producing a benchmark.

| Category | Weight |
|---|---:|
| Technical discoverability | 25 |
| AI crawler and search access | 20 |
| Structured data and entity signals | 20 |
| Content citability | 20 |
| Brand/third-party visibility | 10 |
| Multimodal and preview readiness | 5 |
| **Total** | **100** |

## Category definitions

### Technical discoverability — 25

Includes:

- HTTP success
- Sitemap availability
- Canonicals
- Indexability
- Raw HTML content availability
- `robots.txt` availability
- Page/template crawl health

### AI crawler and search access — 20

Includes:

- Tier 1 AI crawlers
- Googlebot/Bingbot
- No blanket AI/search blocking
- Meta robots and `X-Robots-Tag`
- Preview restrictions

### Structured data and entity signals — 20

Includes:

- JSON-LD coverage
- Relevant schema types
- `sameAs`
- Organization/LocalBusiness/Product/Article schema
- Schema consistency with visible content

### Content citability — 20

Includes:

- Direct answer blocks
- Self-contained passages
- Evidence/data points
- Query-aligned headings
- Originality/uniqueness

### Brand/third-party visibility — 10

Includes:

- Wikipedia/Wikidata
- YouTube
- LinkedIn
- Reddit
- Reviews
- Industry sources
- Press/partner corroboration

### Multimodal and preview readiness — 5

Includes:

- `og:image`
- Image quality
- Alt text
- Video metadata
- Transcripts
- Rich media consistency

---

# Competitor comparison workflow

## Step 1: Confirm inputs

Record:

| Field | Primary | Competitor 1 | Competitor 2 | Competitor 3 |
|---|---|---|---|---|
| Domain | | | | |
| Brand name | | | | |
| Market/country | | | | |
| Business model | | | | |
| Notes | | | | |

Flag any comparability concerns.

---

## Step 2: Run crawl with identical settings

Use `create-report.py` unless crawl-only output is sufficient.

Check whether each site produced:

- `audit_summary.json`
- `robots_fetched.txt`
- `llms_fetched.txt`, if live
- `json-ld.txt`
- `jsonld/`
- `og_images/`
- `comparison.md`
- `comparison.json`

---

## Step 3: Read `comparison.md`

Use it for the first-pass table.

Look for:

- Which sites have live `llms.txt`
- Which sites expose JSON-LD broadly
- Which sites use `sameAs`
- Which sites include `og:image`
- Which sites have robots gaps
- Which sites had crawl failures

---

## Step 4: Normalise metrics

Raw counts can mislead when crawl sizes differ.

Use both raw counts and percentages.

Recommended derived metrics:

```text
HTTP 200 rate = HTTP 200 pages / pages crawled

JSON-LD coverage = pages with JSON-LD / HTTP 200 pages

og:image coverage = pages with og:image / HTTP 200 pages

sameAs density = distinct sameAs URLs / pages crawled
```

Report percentages where possible.

---

## Step 5: Check critical blockers

For each site, identify whether:

- Googlebot is blocked
- Bingbot is blocked
- AI search crawlers are blocked
- Key pages are `noindex`
- Main content is JS-only
- Sitemap is missing/unreachable
- Canonicals are broken
- No structured data exists
- `llms.txt` is missing
- Preview controls suppress snippets
- Crawl returned many errors

These blockers should override superficial wins.

---

## Step 6: Compare equivalent page types

Do not compare a competitor’s guide page against the primary site’s homepage.

Use equivalent templates:

| Template | Primary URL | Competitor A | Competitor B | Competitor C |
|---|---|---|---|---|
| Homepage | | | | |
| Product/service | | | | |
| Pricing | | | | |
| Comparison | | | | |
| Guide/article | | | | |
| Support/help | | | | |
| Local/location | | | | |

For each template, compare:

- Main content depth
- Direct answer blocks
- Schema
- Internal links
- Media
- Trust cues
- Preview controls

---

## Step 7: Produce benchmark findings

Write findings in relative terms.

Good:

```markdown
Competitor A has stronger structured data coverage: 82% of successful crawled pages include JSON-LD versus 24% on the primary site.
```

Avoid:

```markdown
Competitor A is better for AI.
```

Tie conclusions to evidence and explain limitations.

---

# Deliverable template

```markdown
## Competitor GEO comparison

**Primary site:** {primary}  
**Competitors:** {competitor 1}, {competitor 2}, {competitor 3}  
**Crawl date:** {date}  
**Crawl settings:** {max sitemap URLs, max sitemaps, delay}  

### Executive summary

{2–4 sentences. State where the primary leads, where competitors lead, and the highest-priority gap to close.}

### Comparability notes

| Site | Comparable? | Notes |
|---|---|---|
| Primary | Yes | |
| Competitor A | Yes / Partial / No | |
| Competitor B | Yes / Partial / No | |
| Competitor C | Yes / Partial / No | |

### Automated crawl comparison

| Site | Pages | HTTP 200 rate | JSON-LD coverage | og:image coverage | live llms.txt | sameAs URLs | Robots notes |
|---|---:|---:|---:|---:|---|---:|---|
| Primary | | | | | | | |
| Competitor A | | | | | | | |
| Competitor B | | | | | | | |
| Competitor C | | | | | | | |

### GEO benchmark scorecard

| Site | Technical discoverability /25 | AI access /20 | Structured data/entity /20 | Content citability /20 | Brand visibility /10 | Multimodal /5 | Total /100 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Primary | | | | | | | |
| Competitor A | | | | | | | |
| Competitor B | | | | | | | |
| Competitor C | | | | | | | |

### Key gaps vs competitors

| Gap | Competitor advantage | Evidence | Priority |
|---|---|---|---|
| | | | High / Medium / Low |

### Where the primary leads

| Strength | Evidence | How to defend/extend |
|---|---|---|

### Priority actions for the primary site

1. {Action}
2. {Action}
3. {Action}
```

---

# JSON output recommendation

If producing a machine-readable result, use this shape.

`create-report.py` augments the crawl-written `comparison.json` **rows** with `geo_benchmark_scorecard` when the HTML report is built (same numeric buckets as below).

```json
{
  "primary": "https://primary.com",
  "competitors": [
    "https://competitor-a.com",
    "https://competitor-b.com"
  ],
  "crawl_date": "YYYY-MM-DD",
  "metrics": [
    {
      "site": "primary",
      "domain": "primary.com",
      "pages_crawled": 50,
      "http_200_rate": 0.96,
      "jsonld_coverage": 0.72,
      "og_image_coverage": 0.88,
      "llms_txt_live": true,
      "sameas_count": 6,
      "robots_notes": "Tier 1 AI crawlers mostly allowed",
      "scores": {
        "technical_discoverability": 20,
        "ai_access": 18,
        "structured_data_entity": 16,
        "content_citability": 14,
        "brand_visibility": 7,
        "multimodal": 4,
        "total": 79
      },
      "top_gaps": [
        "No PerplexityBot-specific robots group",
        "Product pages lack Product schema"
      ]
    }
  ]
}
```

**Implemented keys** (on each `comparison.json` row after report render): `geo_benchmark_scorecard` with `technical_discoverability`, `ai_access`, `structured_data_entity`, `content_citability`, `brand_third_party`, `multimodal_preview`, `total`.

---

# Interpretation guidance

## Strong competitor advantage

Use when:

- Competitor has a signal and primary does not
- Competitor has materially higher coverage
- Competitor avoids a blocker present on primary
- Competitor has stronger equivalent page templates
- Competitor has better off-site corroboration

Example:

```markdown
Competitor B has a strong entity advantage: it uses Organization schema with verified `sameAs` links to LinkedIn, YouTube, Wikidata, and Crunchbase, while the primary site has no `sameAs` markup.
```

## Weak or inconclusive advantage

Use when:

- Crawl samples differ heavily
- Signals are present but not clearly better
- Data is blocked by login or bot challenge
- Competitor has more pages crawled but lower percentage coverage
- Difference is small

Example:

```markdown
Competitor A shows slightly higher `og:image` coverage, but the difference is small and crawl samples differ, so this is a low-confidence advantage.
```

## Primary leads

Use when the primary site outperforms competitors on:

- AI crawler access
- JSON-LD coverage
- `llms.txt`
- `sameAs`
- Original content depth
- Better structured guides
- Stronger third-party corroboration

Always recommend how to defend that advantage.

---

# Common findings

## Competitor has live `llms.txt`

```markdown
Competitor A publishes a live `llms.txt`, while the primary site does not. Although `llms.txt` is emerging and not a universal ranking factor, it provides a clear AI-readable map of important pages. Add a concise `llms.txt` linking to canonical guides, product/service pages, policies, and company information.
```

## Competitor has stronger JSON-LD coverage

```markdown
Competitor B has broader JSON-LD coverage across crawled pages. The primary site should prioritise Organization, WebSite, BreadcrumbList, Article/Product/LocalBusiness schema where relevant, and ensure markup matches visible content.
```

## Competitor has better `sameAs`

```markdown
Competitor C exposes more verified `sameAs` links, making its entity easier to connect across the web. Add verified official profiles to Organization schema after manual confirmation.
```

## Competitor has stronger answer blocks

```markdown
Competitor A’s service pages answer common buyer questions directly near the top of each page. The primary site has similar topics but buries the answer below generic marketing copy. Add answer-first sections and self-contained summaries to priority templates.
```

## Primary has better AI crawler access

```markdown
The primary site allows more Tier 1 AI search crawlers than competitors. Maintain this advantage with explicit robots groups and monitor future policy changes.
```

---

# Limits

- Maximum three competitors in the current CLI.
- Results are only as representative as the crawl sample.
- Shallow crawls may miss deeper template patterns.
- Different sitemap structures can skew page counts.
- No headless rendering means JS-only content may be under-counted.
- Automated checks do not prove rankings, AI Overview inclusion, or traffic.
- Some competitors may block crawlers, causing incomplete evidence.
- Brand visibility and sentiment checks require manual verification.
- Comparing different business models can produce misleading conclusions.

---

# Related skills

| Skill | Role |
|---|---|
| `create-report.md` | Runs crawl and generates report artifacts |
| `ai-crawler-report.md` | Detailed AI/search crawler access comparison |
| `llms-txt.md` | Live/generated `llms.txt` comparison |
| `json-ld.md` | Structured data comparison and recommendations |
| `brand-visbility.md` | Off-site entity and corroboration comparison |
| `ai-citability.md` | Page/template-level content citability comparison |
| `ai-search-success.md` | Google AI Search readiness comparison |
| `technical-audit.md` | Technical/indexability comparison |
| `eeat.md` | People-first content and trust comparison |
