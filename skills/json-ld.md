# Skill: JSON-LD review, creation, and improvement

Use this skill to review, improve, or create **JSON-LD structured data** for a site using crawl outputs such as `json-ld.txt`, `jsonld/*.json`, extracted HTML, rendered page text, and visible page content.

Structured data helps search engines and AI systems understand **entities**, **page types**, **authors**, **products**, **locations**, **breadcrumbs**, **media**, and **relationships**. It can support rich results, entity clarity, E-E-A-T signals, and GEO readiness, but it must be accurate, visible-content-aligned, and policy-compliant.

**Primary output:** a structured JSON-LD audit with inventory, issue severity, recommended fixes, validation notes, and optional starter/corrected JSON-LD templates.

---

## Primary references

- [Introduction to structured data markup in Google Search](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data)
- [General structured data guidelines](https://developers.google.com/search/docs/appearance/structured-data/sd-policies)
- [Google Search Gallery](https://developers.google.com/search/docs/appearance/structured-data/search-gallery)
- [Schema.org](https://schema.org/) vocabulary
- [Schema Markup Validator](https://validator.schema.org/)
- [Google Rich Results Test](https://search.google.com/test/rich-results)

---

## Purpose

Use this skill to answer:

- Does the site publish valid JSON-LD?
- Does the markup match visible page content?
- Are the right Schema.org types used for each page/template?
- Is the brand/entity clearly represented?
- Are `Organization`, `WebSite`, `WebPage`, `BreadcrumbList`, and page-specific schemas linked properly?
- Are authors, products, local details, videos, FAQs, or articles marked up where appropriate?
- Are there misleading, stale, duplicate, or conflicting structured data blocks?
- Is the markup eligible for Google rich results where relevant?
- Does JSON-LD support GEO goals such as entity clarity, source transparency, and content attribution?

---

## What this skill does and does not do

This skill evaluates:

- JSON syntax validity
- Schema.org type appropriateness
- Google structured data guideline alignment
- Visible-content consistency
- Entity graph quality
- `sameAs` quality
- Page/template schema coverage
- Rich result eligibility risks

This skill does **not** guarantee:

- Rich results
- Higher rankings
- AI Overview inclusion
- AI citation
- Knowledge panel generation

Pair with:

| Skill | Role |
|---|---|
| `ai-search-success.md` | Google AI Search readiness and preview controls |
| `eeat.md` | Authorship, expertise, trust, source transparency |
| `brand-visbility.md` | Verified official profiles for `sameAs` |
| `ai-citability.md` | Whether page content is extractable and cite-worthy |
| `technical-audit.md` | Rendering, indexability, raw HTML availability |

---

# Inputs

## Minimum inputs

| Input | Use |
|---|---|
| `jsonld/*.json` | Raw JSON-LD blocks extracted per crawled URL |
| `json-ld.txt` | Audit-generated starter/sample JSON-LD |
| Page HTML or rendered text | Visible-content comparison |
| URL list / templates | Page-type mapping |
| Homepage HTML | Organization/WebSite baseline |

## Recommended inputs

| Input | Use |
|---|---|
| `same_as_urls.txt` | Candidate `sameAs` URLs |
| Brand visibility report | Verify official profiles before adding `sameAs` |
| Author pages | Validate `Person`, `author`, `reviewedBy` |
| Product/pricing pages | Validate `Product`, `Offer`, prices, availability |
| Local pages / GBP data | Validate `LocalBusiness`, address, opening hours |
| Video pages / YouTube URLs | Validate `VideoObject` |
| Breadcrumb HTML | Validate `BreadcrumbList` |
| Search Console / Rich Results reports | Identify structured data errors at scale |
| Merchant Center feed | Product data consistency |
| CMS templates | Implement schema at template level |

---

# Output

Produce:

1. JSON-LD inventory
2. Page/template coverage summary
3. Issue list with severity
4. Recommended fixes
5. Starter or corrected JSON-LD where needed
6. Validation status
7. `sameAs` recommendations
8. Implementation notes

---

# Client-facing wording

Structured data terms can be used if they are explained.

| Technical wording | Client-facing wording |
|---|---|
| Add JSON-LD | Add structured page information using JSON-LD so search engines and AI systems can understand the page |
| Add Organization schema | Add structured brand information to the homepage so AI systems can identify the business |
| Add WebSite schema | Add structured site information so search engines understand the website name and publisher |
| Add `sameAs` | Add verified official profile links (`sameAs`) so AI systems can connect the website to the right brand |
| Add BreadcrumbList | Add structured breadcrumbs so search engines understand where the page sits in the site |
| Product schema missing | Add structured product details that match what users can see on the product page |

---

# Status definitions

| Status | Meaning |
|---|---|
| **Pass** | Valid, appropriate, visible-content-aligned, and useful |
| **Partial** | Present but incomplete, inconsistent, or missing recommended fields |
| **Fail** | Invalid, misleading, wrong type, or absent where important |
| **Not applicable** | Schema type not relevant to the page/template |
| **Manual check** | Requires live validation or business data not available in crawl |

---

# Optional scoring model

Use this when the audit needs a numeric JSON-LD / structured data score. Prefer **type-aware** scoring over a single “schema present” boolean.

## Overall JSON-LD score (0–100)

| Component | Weight | What it measures |
|---|---:|---|
| JSON-LD detection and parseability | 10 | Valid JSON-LD extracted from raw HTML; supports `@graph`, arrays, compact one-line JSON; `@context` may be `http://` or `https://` schema.org |
| Template coverage | 15 | Share of important sampled templates (homepage, product, category, article, etc.) with parsed JSON-LD |
| Homepage entity graph | 20 | **Organization** vs **WebSite** are scored separately; strong Organization does **not** zero out a missing WebSite |
| Page-type schema quality | 25 | Product/Offer richness on product URLs; other templates vs expected types |
| Entity linkage and `sameAs` | 15 | Official profiles, Wikipedia/Wikidata, plausibility |
| Graph connectivity and `@id` | 10 | Stable IDs, `publisher` / `isPartOf` / `mainEntity`, coherent `@graph` |
| Visible-content consistency | 5 | Core fields non-null; prices/availability plausible vs page; no misleading hidden markup |

**Total:** 100

### Detection and parseability (10)

Treat as **valid** when:

- At least one `application/ld+json` block parses as JSON.
- Root may be an **object**, an **array** of objects, or an object with **`@graph`**.
- `@context` may be `http://schema.org`, `https://schema.org`, or `https://schema.org/` (normalise trailing slash). **Do not fail** `http://`; recommend upgrading to HTTPS in guidance only.

### Homepage: Organization vs WebSite (within the 20-point homepage band)

| Role | Purpose |
|---|---|
| **Organization** | Who is the brand? (`name`, `url`, `logo`, `sameAs`, knowledge/social URLs) |
| **WebSite** | What is the site and search entry point? (`name`, `url`, `potentialAction` / **SearchAction** when search exists) |

Scoring narrative should allow: *“Primary: strong Organization / `sameAs`; peer: stronger WebSite / SearchAction”* — not *“peer has schema; primary does not”* when both have different strengths.

### Meaningful values (global rule)

Do not award points for placeholder or empty values. Treat as **not meaningful**:

- `null`, missing field, `""`, `[]`, `{}`
- Strings that trim to `null`, `none`, `n/a`, `undefined` (case-insensitive)

Use this rule for descriptions, prices, ratings, images, and `sameAs` entries.

### Competitor comparison caution

When comparing audits:

- Samples may include **different page types**; a peer may show JSON-LD on a product URL while the primary sample never hit a product template.
- Prefer **like-for-like** tables: homepage vs homepage, product vs product, category vs category.
- Read `jsonld/*.json` for the homepage even when `any_json_ld` is thin on long-tail samples.

#### Example interpretation table (client-facing)

| Template | Primary | Competitor | Interpretation |
|---|---|---|---|
| Homepage | Organization + `sameAs` | WebSite + SearchAction | Both useful; combine patterns |
| Product | Product + Offer + image + brand | Product + Offer + seller + `@id` | Often comparable; improve `@id`, seller, description |
| Category | Unknown / thin | Unknown / thin | Needs deliberate template sampling |

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

| Condition | Maximum score |
|---|---:|
| JSON-LD is not parseable sitewide | 40 |
| Markup contains materially false or misleading claims | 35 |
| Product/review/ratings markup is fake, hidden, or inconsistent | 40 |
| Markup claims content not visible to users | 55 |
| Wrong schema type is used across major templates | 60 |
| Entity graph conflicts with visible brand/legal identity | 65 |
| Key commercial/informational templates have no structured data | 70 |
| Major Google structured data policy violation | 50 |

State caps clearly.

---

# Core principles

## 1. Mark up what users can see

Structured data should describe visible or clearly accessible page content.

Do not add:

- Fake reviews
- Invisible FAQs
- Hidden offers
- Fabricated ratings
- Incorrect prices
- Outdated availability
- Nonexistent authors
- Misleading organisation details
- Unsupported awards/accreditations

## 2. Use the most specific appropriate type

Use the type that matches the page and content.

| Page | Likely schema |
|---|---|
| Homepage | `WebSite`, `Organization`, `WebPage` |
| About page | `AboutPage`, `Organization` |
| Contact page | `ContactPage`, `Organization` |
| Article/blog | `Article`, `BlogPosting`, `NewsArticle` |
| Product page | `Product`, `Offer`, optional `AggregateRating` |
| Category page | `CollectionPage`, `ItemList`, `BreadcrumbList` |
| Local branch page | `LocalBusiness` or subtype |
| FAQ page/section | `FAQPage`, only if visible and eligible |
| How-to guide | `HowTo`, where relevant and visible |
| Video page | `VideoObject` |
| Review page | `Review`, where policy-compliant |
| Course/training | `Course`, `CourseInstance` |
| Event page | `Event` |
| Job page | `JobPosting` |
| Recipe page | `Recipe` |
| Software/app | `SoftwareApplication` |
| Service page | `Service` or `ProfessionalService`, where appropriate |

## 3. Link entities with stable `@id` values

Use stable IDs so crawlers understand relationships.

Recommended IDs:

```text
https://www.example.com/#organization
https://www.example.com/#website
https://www.example.com/page-url/#webpage
https://www.example.com/page-url/#article
https://www.example.com/page-url/#breadcrumb
https://www.example.com/product/product-name/#product
```

## 4. Prefer one coherent graph

Use `@graph` to connect entities.

Good relationships:

- `WebSite` → `publisher` → `Organization`
- `WebPage` → `isPartOf` → `WebSite`
- `WebPage` → `about` / `mainEntity`
- `Article` → `author`, `publisher`, `mainEntityOfPage`
- `Product` → `brand`, `offers`
- `BreadcrumbList` → `itemListElement`
- `VideoObject` → `embedUrl`, `thumbnailUrl`, `uploadDate`

## 5. Validate before publishing

Every recommendation should be validated with:

- JSON parser
- Schema Markup Validator
- Google Rich Results Test where the type is eligible
- Manual visible-content spot check

---

# Discovery workflow

## Step 1: Inventory existing JSON-LD

Open `jsonld/*.json` for priority URLs.

Record:

| URL | File | JSON valid? | `@type` values | Main issues |
|---|---|---|---|---|

Check:

- Is JSON parseable?
- Is `@context` present?
- Are `@type` values appropriate?
- Are there duplicate blocks?
- Are nodes linked with `@id`?
- Are values consistent with visible content?

---

## Step 2: Map schema to templates

Create a template map.

| Template | Example URL | Expected schema | Current schema | Gap |
|---|---|---|---|---|
| Homepage | | `WebSite`, `Organization`, `WebPage` | | |
| Article | | `Article`, `BreadcrumbList` | | |
| Product | | `Product`, `Offer`, `BreadcrumbList` | | |
| Local page | | `LocalBusiness`, `BreadcrumbList` | | |
| Video page | | `VideoObject`, `WebPage` | | |

Start with one page type per template. Expand after validation.

---

## Step 3: Compare markup with visible content

For each priority page, compare structured data against what users see.

| Property | Compare against |
|---|---|
| `name` / `headline` | H1, title, visible heading |
| `description` | Meta description, intro, visible summary |
| `image` | Visible image, `og:image`, crawlable image URL |
| `author` | Visible byline and author page |
| `datePublished` | Visible publication date |
| `dateModified` | Visible update date or reliable CMS date |
| `price` | Visible price |
| `priceCurrency` | Visible currency |
| `availability` | Visible availability |
| `aggregateRating` | Visible reviews/ratings |
| `review` | Visible review content |
| `address` | Visible address and GBP/business records |
| `openingHours` | Visible hours / Google Business Profile |
| `sameAs` | Verified official profiles |
| `faq` | Visible question/answer pairs |
| `breadcrumb` | Visible breadcrumb or logical site hierarchy |

---

## Step 4: Validate against Google requirements

For each rich-result eligible type, check:

- Required properties
- Recommended properties
- Google-specific policy requirements
- Eligibility limitations
- Whether the markup appears on the right page type

Use:

- Rich Results Test
- Search Console Enhancements report
- Google Search Gallery documentation
- Schema Markup Validator

---

## Step 5: Consolidate and improve

Common improvements:

| Issue | What to do |
|---|---|
| Multiple disconnected nodes | Use one `@graph` or linked `@id` references |
| Missing `Organization` | Add verified `Organization` node |
| Missing `publisher` | Link page/article to `Organization` |
| Missing `WebSite` | Add homepage-level `WebSite` node |
| Missing `WebPage` | Add page-level `WebPage` node |
| Scattered `sameAs` | Move verified official URLs to `Organization` |
| Wrong `@type` | Use the type that matches visible content |
| Generic descriptions | Align with visible summary/meta description |
| Duplicate conflicting blocks | Deduplicate or consolidate |
| Missing breadcrumbs | Add `BreadcrumbList` where page hierarchy is clear |
| Missing author | Add `author` only where visible/true |
| Missing dates | Add accurate publish/update dates |
| Invalid product offers | Align price/availability with visible UI and feeds |
| Invisible FAQs | Remove or make FAQ content visible |
| Stale logo/image | Use current crawlable image URL |

---

# Starter JSON-LD templates

Use these as starting points only. Replace placeholders with verified facts.

## Homepage graph: `WebSite` + `Organization` + `WebPage`

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://www.example.com/#organization",
      "name": "Example Brand",
      "legalName": "Example Brand Ltd",
      "url": "https://www.example.com/",
      "logo": {
        "@type": "ImageObject",
        "url": "https://www.example.com/logo.png"
      },
      "sameAs": [
        "https://www.linkedin.com/company/example/",
        "https://www.youtube.com/@example"
      ]
    },
    {
      "@type": "WebSite",
      "@id": "https://www.example.com/#website",
      "url": "https://www.example.com/",
      "name": "Example Brand",
      "description": "Short description aligned with visible site positioning.",
      "publisher": {
        "@id": "https://www.example.com/#organization"
      }
    },
    {
      "@type": "WebPage",
      "@id": "https://www.example.com/#webpage",
      "url": "https://www.example.com/",
      "name": "Homepage title or H1",
      "isPartOf": {
        "@id": "https://www.example.com/#website"
      },
      "about": {
        "@id": "https://www.example.com/#organization"
      }
    }
  ]
}
```

---

## Article / blog post

```json
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Article",
      "@id": "https://www.example.com/blog/article-slug/#article",
      "headline": "Visible article headline",
      "description": "Visible or meta description summary.",
      "image": [
        "https://www.example.com/images/article-image.jpg"
      ],
      "datePublished": "2025-01-15",
      "dateModified": "2025-02-02",
      "author": {
        "@type": "Person",
        "@id": "https://www.example.com/authors/jane-smith/#person",
        "name": "Jane Smith",
        "url": "https://www.example.com/authors/jane-smith/"
      },
      "publisher": {
        "@id": "https://www.example.com/#organization"
      },
      "mainEntityOfPage": {
        "@id": "https://www.example.com/blog/article-slug/#webpage"
      }
    }
  ]
}
```

---

## Product page

```json
{
  "@context": "https://schema.org",
  "@type": "Product",
  "@id": "https://www.example.com/products/product-name/#product",
  "name": "Visible Product Name",
  "description": "Visible product description.",
  "image": [
    "https://www.example.com/images/product.jpg"
  ],
  "brand": {
    "@type": "Brand",
    "name": "Example Brand"
  },
  "sku": "VISIBLE-SKU-123",
  "offers": {
    "@type": "Offer",
    "url": "https://www.example.com/products/product-name/",
    "priceCurrency": "GBP",
    "price": "99.00",
    "availability": "https://schema.org/InStock"
  }
}
```

Only include `aggregateRating` or `review` if ratings/reviews are visible and policy-compliant.

---

## Breadcrumbs

```json
{
  "@context": "https://schema.org",
  "@type": "BreadcrumbList",
  "@id": "https://www.example.com/category/page/#breadcrumb",
  "itemListElement": [
    {
      "@type": "ListItem",
      "position": 1,
      "name": "Home",
      "item": "https://www.example.com/"
    },
    {
      "@type": "ListItem",
      "position": 2,
      "name": "Category",
      "item": "https://www.example.com/category/"
    },
    {
      "@type": "ListItem",
      "position": 3,
      "name": "Page name",
      "item": "https://www.example.com/category/page/"
    }
  ]
}
```

---

## Local business

```json
{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "@id": "https://www.example.com/locations/city/#localbusiness",
  "name": "Example Brand City",
  "url": "https://www.example.com/locations/city/",
  "telephone": "+44-0000-000000",
  "address": {
    "@type": "PostalAddress",
    "streetAddress": "1 Example Street",
    "addressLocality": "City",
    "postalCode": "AB1 2CD",
    "addressCountry": "GB"
  },
  "openingHoursSpecification": [
    {
      "@type": "OpeningHoursSpecification",
      "dayOfWeek": [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday"
      ],
      "opens": "09:00",
      "closes": "17:00"
    }
  ],
  "parentOrganization": {
    "@id": "https://www.example.com/#organization"
  }
}
```

---

## FAQPage

```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "@id": "https://www.example.com/page/#faq",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Visible question?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Visible answer text."
      }
    }
  ]
}
```

Only use FAQ markup where the question and answer are visible on the page and comply with current Google guidance.

---

## VideoObject

```json
{
  "@context": "https://schema.org",
  "@type": "VideoObject",
  "@id": "https://www.example.com/videos/video-page/#video",
  "name": "Visible video title",
  "description": "Visible video description.",
  "thumbnailUrl": [
    "https://www.example.com/images/video-thumbnail.jpg"
  ],
  "uploadDate": "2025-01-15",
  "embedUrl": "https://www.youtube.com/embed/VIDEO_ID",
  "transcript": "Optional transcript text if available on the page."
}
```

---

# `sameAs` guidance

Use `sameAs` to connect the official brand/entity to verified external profiles.

## Good `sameAs` candidates

- Wikipedia article
- Wikidata entity
- LinkedIn company page
- YouTube official channel
- Official X/Twitter, Instagram, Facebook, TikTok
- GitHub organisation
- Crunchbase profile, where relevant
- Official app store profiles
- Other verified official profiles

## Avoid adding

- Unofficial Reddit discussions
- Search result pages
- Review pages unless part of entity strategy
- Duplicate or unclaimed profiles
- Country/location pages that are not the same entity
- Low-confidence social profiles
- Competitor or reseller pages

Verify `sameAs` using `brand-visbility.md`.

---

# Format: audit artifact vs production script

| Format | Use case |
|---|---|
| `json-ld.txt` | Audit artifact, human review, copy/paste draft, diff across audits |
| `jsonld/*.json` | Extracted on-page JSON-LD from crawl |
| Inline `<script type="application/ld+json">` | Production implementation in HTML |
| CMS field/template | Preferred scalable implementation for repeated templates |
| Tag manager injection | Possible, but less ideal; ensure Google can render reliably |

The crawler’s `json-ld.txt` is a sample document. It is not a substitute for embedding JSON-LD in production HTML.

---

# Validation workflow

1. **Parse JSON**
   - Use a linter or:

   ```bash
   python -m json.tool file.json
   ```

2. **Validate schema**
   - Use [Schema Markup Validator](https://validator.schema.org/).

3. **Check Google eligibility**
   - Use [Rich Results Test](https://search.google.com/test/rich-results) for eligible types.

4. **Compare against visible content**
   - Open the live page.
   - Confirm headline, image, author, price, dates, FAQ, ratings, and address match.

5. **Check URLs**
   - Image/logo URLs resolve.
   - Canonical URLs match.
   - `sameAs` URLs are official.
   - `@id` URLs are stable.

6. **Check rendered output**
   - Confirm JSON-LD is present in rendered/live HTML.
   - If injected by JavaScript, verify Google can render it.

---

# Issue severity

Use this severity model.

| Severity | Meaning |
|---|---|
| **Critical** | Policy violation, fake/misleading markup, invalid JSON sitewide, or conflicts with visible content |
| **High** | Wrong schema on key templates, missing required fields, product/review inconsistencies |
| **Medium** | Missing recommended fields, disconnected graph, weak `sameAs`, missing breadcrumbs |
| **Low** | Formatting, minor field improvements, optional enrichments |

---

# Audit deliverable template

```markdown
## JSON-LD structured data — {host}

**Scope:** {number} URLs/templates reviewed  
**Inputs:** `jsonld/*.json`, `json-ld.txt`, HTML, visible page checks  
**Score:** {score}/100, if scored  

### Executive summary

{2–4 sentences. State whether JSON-LD is present, whether it is valid, biggest risks, and top priority fix.}

### Inventory

| URL | Template | File | JSON valid? | `@type` values | Notes |
|---|---|---|---|---|---|
| | | `jsonld/...json` | Yes / No | | |

### Template coverage

| Template | Expected schema | Current schema | Status | Action |
|---|---|---|---|---|
| Homepage | `WebSite`, `Organization`, `WebPage` | | Pass / Partial / Fail | |
| Article | `Article`, `BreadcrumbList` | | Pass / Partial / Fail | |
| Product | `Product`, `Offer`, `BreadcrumbList` | | Pass / Partial / Fail | |
| Local page | `LocalBusiness`, `BreadcrumbList` | | Pass / Partial / Fail | |

### Issues

| Issue | Severity | Evidence | Fix |
|---|---|---|---|
| | Critical / High / Medium / Low | URL/file/property | |

### Visible-content consistency

| URL | Property | JSON-LD value | Visible value | Status |
|---|---|---|---|---|
| | `price` | | | Match / Mismatch / Missing |

### `sameAs` recommendations

| URL | Include? | Confidence | Notes |
|---|---|---|---|
| | Yes / No / Verify | High / Medium / Low | |

### Suggested JSON-LD

~~~json
{}
~~~

### Validation

| Tool | Result | Notes |
|---|---|---|
| JSON parser | Pass / Fail | |
| Schema Markup Validator | Pass / Fail / Not tested | |
| Rich Results Test | Pass / Fail / Not eligible / Not tested | |
| Visible-content check | Pass / Partial / Fail | |

### Priority actions

1. {Action}
2. {Action}
3. {Action}
```

---

# Integration with crawl output

When using `crawl-site.py` / `create-report.py` audit folders:

| Artifact | Role |
|---|---|
| `json-ld.txt` | Baseline WebSite-style sample; extend with Organization, sameAs, image as verified |
| `jsonld/*.json` | Raw extracted JSON-LD for review |
| `same_as_urls.txt` | Candidate `sameAs` list; verify before use |
| Homepage HTML | Ground truth for title, description, hero, logo |
| Page HTML / extracted text | Visible-content consistency checks |
| `og_images/` | Candidate image/preview assets |
| `audit_summary.json` | Site-level structured data summary |
| `brand_visibility` | Verify official profiles before adding `sameAs` |
| `create-report.py` | **JSON-LD / structured data** sub-score uses seven crawl-backed theme proxies aligned with §Optional scoring model (15/15/20/20/15/10/5); directional only |

---

# Common recommendations

## Add a coherent homepage graph

```markdown
Add a connected homepage JSON-LD graph with `Organization`, `WebSite`, and `WebPage` nodes linked by stable `@id` values.
```

## Fix disconnected schema nodes

```markdown
Consolidate separate JSON-LD blocks into a linked `@graph` so Google can connect the page, site, publisher, and main entity.
```

## Verify and improve `sameAs`

```markdown
Move verified official profiles into the `Organization.sameAs` array. Exclude unverified, duplicate, or unofficial profiles.
```

## Align Product schema with visible content

```markdown
Ensure `Product`, `Offer`, price, currency, availability, SKU, and ratings match the visible product page and Merchant Center feed.
```

## Add Article authorship

```markdown
For article templates, add `Article` or `BlogPosting` markup with visible headline, author, publisher, image, `datePublished`, and `dateModified`.
```

## Remove misleading FAQ markup

```markdown
Remove FAQ markup for questions/answers that are not visible on the page or are reused in a misleading way across templates.
```

---

# Limitations

- Structured data helps understanding but does not guarantee rich results or AI visibility.
- Google may ignore valid markup if content quality, policy, or eligibility requirements are not met.
- Markup injected only client-side may be less reliable than server-rendered HTML.
- Schema.org allows many properties that Google does not use for rich results.
- Google requirements vary by rich result type and change over time.
- `json-ld.txt` is an audit artifact, not production implementation.