# Skill: Google AI Search success audit

Use this skill when auditing a site for **Google Search AI experiences**, including **AI Overviews**, **AI Mode**, multimodal search, and AI-assisted search journeys.

This skill translates Google Search guidance into practical checks that can be run against crawled pages, `robots.txt`, structured data, preview controls, multimodal assets, and analytics/process evidence.

**Primary question:** Is the site eligible, accessible, useful, and measurable enough to benefit from Google’s AI-powered search experiences?

**Primary output:** A structured checklist with **Pass / Partial / Fail / Not applicable** per theme, evidence tied to specific URLs/files, priority actions, and an optional **0–100 Google AI Search success score**.

---

## Scope

This skill evaluates readiness for Google Search AI experiences.

It covers:

- Unique, helpful content
- Page experience
- Crawl and index access
- Preview controls
- Structured data
- Multimodal support
- Entity and commerce/local signals
- Visit quality measurement
- Freshness and content evolution

It does **not** guarantee inclusion in AI Overviews or AI Mode.

Google decides eligibility and selection using many systems, including ranking, indexing, content quality, query interpretation, user context, and policy constraints.

---

## Target surfaces

Use this skill for readiness across:

- Google AI Overviews
- Google AI Mode
- Google Search snippets and rich results
- Google Lens / multimodal search where relevant
- Product, local, news, image, and video search features that can feed AI-style search experiences

---

## Inputs

Minimum inputs:

| Input | Use |
|---|---|
| HTML from crawled URLs | Content, meta robots, structure, images, links |
| `robots.txt` / `robots_fetched.txt` | Googlebot and Google-Extended access |
| HTTP status codes | Indexability and crawl health |
| HTTP headers | `X-Robots-Tag`, caching, content type |
| JSON-LD extracts | Structured data review |
| Sitemap URLs | Discovery and freshness |
| Key URL list | Priority page sampling |

Recommended optional inputs:

| Input | Use |
|---|---|
| Rendered DOM text | Check client-rendered content |
| Raw HTML text | Check what is available before JavaScript |
| Search Console data | Queries, impressions, CTR, indexing, page experience |
| Analytics data | Engagement and conversion quality |
| CrUX / Lighthouse | Page experience and performance |
| Merchant Center | Product feed consistency |
| Google Business Profile | Local/entity consistency |
| YouTube/video metadata | Video/multimodal readiness |
| `llms.txt` | Curated important-page discovery signal, not a Google ranking requirement |
| `jsonld/*.json`, `json-ld.txt` | Structured data extracts from crawler |

---

## Output

Produce:

1. Executive summary
2. Theme checklist
3. Optional weighted score
4. Evidence by URL/template
5. Key blockers
6. Prioritised actions

Use **Pass / Partial / Fail / Not applicable** for each theme.

---

# Status definitions

| Status | Meaning |
|---|---|
| **Pass** | Meets the requirement across key sampled pages with only minor issues |
| **Partial** | Some evidence of readiness, but important gaps or inconsistent templates exist |
| **Fail** | Major blocker or the theme is mostly absent |
| **Not applicable** | Theme does not apply to the site or page type |
| **Manual check** | Required evidence is unavailable in crawl data |

---

# Optional scoring model

Use this when the audit needs a numeric Google AI Search success score.

| Theme | Weight |
|---|---:|
| 1. Unique, valuable content | 22 |
| 2. Crawl, index, and rendering access | 18 |
| 3. Structured data and entity consistency | 14 |
| 4. Preview controls and snippet eligibility | 10 |
| 5. Page experience | 10 |
| 6. Multimodal readiness | 7 |
| 7. Entity, product, and local ecosystem | 5 |
| 8. Visit quality measurement | 7 |
| 9. Freshness and ongoing evolution | 7 |
| **Total** | **100** |

### Suggested scoring

| Status | Points awarded |
|---|---:|
| Pass | 100% of theme weight |
| Partial | 50% of theme weight |
| Fail | 0% of theme weight |
| Not applicable | Remove from denominator and rescale |
| Manual check | Mark separately; do not score unless evidence exists |

### Formula

```text
Google AI Search success score =
sum(theme_score_points) / sum(applicable_theme_weights) × 100
```

---

# Critical blockers and caps

Apply these caps after scoring.

| Condition | Maximum score |
|---|---:|
| Key pages blocked from Googlebot | 40 |
| Key pages are `noindex` | 45 |
| Important content unavailable in rendered or crawlable HTML | 55 |
| Sitewide `nosnippet` or `max-snippet:0` on key content | 60 |
| Severe structured data spam or misleading markup | 65 |
| Thin/duplicative content across key templates | 65 |
| Major mobile usability or page experience issue blocking use | 75 |

State any cap clearly.

Example:

```markdown
Raw score: 78/100  
Cap applied: 60/100 because key guide pages use `max-snippet:0`, limiting snippet and AI Overview eligibility.  
Final score: 60/100
```

---

# Theme 1: Unique, valuable content for people

## Intent

Google’s AI Search experiences are more likely to surface content that is useful, original, specific, and satisfying for real users.

Content should not exist only to capture search traffic. It should help users complete a task, understand a topic, compare options, or make a decision.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| Clear primary purpose | Each important URL has a focused topic and user need | H1, intro, and body all support one clear intent |
| Direct answer value | Page answers likely AI-style questions clearly | Direct answers near relevant headings |
| Depth vs thin content | Meaningful explanation, examples, steps, or details | Not only category shells or generic service copy |
| Originality | Adds information not easily found elsewhere | First-party data, examples, expert POV, methodology, case studies |
| People-first usefulness | Content satisfies the reader, not only a keyword pattern | Specific, practical, complete enough for task |
| Trust cues | Shows who created/reviewed it and why they are credible | Author, organisation, reviewer, sources, update date |
| Source support | Important claims have credible references | Named sources, dates, methodology |
| Query coverage | Covers the questions users actually ask | FAQ, comparison, troubleshooting, pricing, local intent |
| Content clarity | Uses plain language and clean structure | Headings, summaries, tables, steps |
| AI-generated content policy | If AI-assisted, quality and accuracy are human-reviewed | No scaled low-value auto-content |

## Automation-friendly signals

Weak signals only:

- Word count in main content
- Presence of `<main>` or `<article>`
- H1 alignment with title
- Duplicate title/H1 patterns
- Author/date fields in JSON-LD
- `datePublished` / `dateModified`
- FAQ or how-to style headings
- Internal duplication across templates

## Evidence examples

| Finding | Evidence |
|---|---|
| Strong original value | Page includes first-party benchmark data and methodology |
| Weak value | 80% of location pages use near-identical copy |
| Missing trust cue | No author, date, reviewer, or source references |
| Poor query fit | Page targets “pricing” but gives no range or cost drivers |

## Recommended actions

- Add answer-first sections for priority questions.
- Add original examples, first-party data, expert commentary, or case studies.
- Add dates, sources, and methodology for factual claims.
- Consolidate or improve thin/duplicative templates.
- Add clear “who, how, why” signals in line with helpful content guidance.

---

# Theme 2: Crawl, index, and rendering access

## Intent

Google must be able to discover, crawl, render, index, and understand the important content.

This theme is a critical gate for Google AI Search readiness.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| HTTP success | Priority URLs return `200` | No 4xx, 5xx, redirect loops, or soft 404s |
| Googlebot access | `robots.txt` does not block key paths | `Googlebot` allowed for important pages |
| Meta robots | Key pages are not `noindex` | No accidental `noindex` on rankable pages |
| X-Robots-Tag | Headers do not block indexing | No `X-Robots-Tag: noindex` on key HTML/PDF assets |
| Canonicals | Canonical points to the intended URL | Self-referencing or correctly consolidated |
| Indexable content | Main content is available in crawlable/rendered HTML | Not an empty JS shell |
| Internal links | Important pages are discoverable through crawlable links | Not only search forms or JS-only navigation |
| Sitemap discovery | XML sitemap lists canonical priority URLs | Sitemap is reachable and current |
| Status consistency | Final URLs match canonical/indexable versions | No mixed http/https, slash, parameter confusion |
| Rendering parity | Rendered content and raw HTML are not materially different for important text | Critical facts not JS-only |

## Google-specific crawler notes

| Crawler / control | Relevance |
|---|---|
| `Googlebot` | Controls Google Search crawling and indexing. Critical for AI Overviews eligibility. |
| `Google-Extended` | Controls whether content can be used for Gemini apps and Vertex AI grounding/training-related uses. It does not control normal Google Search indexing. |
| `GoogleOther` | Used for various Google product/research crawls. Not a replacement for Googlebot. |

## Pass criteria

A page generally passes when:

- It returns `200`
- It is not blocked by `robots.txt`
- It is not `noindex`
- Canonical is correct
- Main content is available to Google after rendering
- It is internally linked or in a sitemap

## Recommended actions

- Unblock Googlebot for key content paths.
- Remove accidental `noindex`.
- Fix canonical conflicts.
- Ensure important content appears in server-rendered or reliably rendered HTML.
- Add or update XML sitemaps.
- Make important pages reachable through crawlable links.

---

# Theme 3: Structured data and entity consistency

## Intent

Structured data helps Google understand entities, page types, products, organisations, authors, reviews, videos, breadcrumbs, and other relationships.

Markup must match visible content and follow Google’s structured data policies.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| Relevant schema types | Uses appropriate Schema.org types | `Organization`, `WebSite`, `Article`, `Product`, `FAQPage`, `BreadcrumbList`, `VideoObject`, `LocalBusiness`, etc. |
| Visible-content match | Markup reflects what users can see | Names, prices, ratings, dates match page content |
| Entity consistency | Brand, logo, sameAs, address, identifiers consistent | Same details across site, GBP, Merchant Center, social profiles |
| Author/reviewer markup | Authors and reviewers marked up where relevant | `Person`, `author`, `reviewedBy` where appropriate |
| Product markup | Product pages include accurate offers, availability, price, reviews where visible | Matches Merchant Center/feed where applicable |
| Article markup | Articles include headline, date, author, image | Accurate and visible |
| Breadcrumb markup | Breadcrumbs reflect visible navigation | No fake hierarchy |
| Validation | Rich Results Test / Schema validator | No critical errors |
| No spam | Avoid misleading, hidden, or fake markup | No fake reviews, invisible FAQs, fabricated ratings |

## Google-specific caution

Structured data can improve understanding and rich result eligibility, but it does not guarantee inclusion in AI Overviews or AI Mode.

Invalid or misleading structured data can create trust and policy issues.

## Recommended actions

- Add or fix JSON-LD on key templates.
- Align schema fields with visible page content.
- Add `sameAs` for official profiles.
- Add `dateModified` where content freshness matters.
- Validate with Google Rich Results Test and Schema.org validator.
- Remove hidden or misleading markup.

---

# Theme 4: Preview controls and snippet eligibility

## Intent

Preview controls affect how Google can display content in classic results and AI experiences.

Restrictive directives can reduce eligibility for snippets, rich previews, and AI-generated summaries.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| Document-level robots | Meta robots values on key pages | No accidental `nosnippet`, `noindex`, `max-snippet:0` |
| Header-level robots | `X-Robots-Tag` directives | No restrictive headers on key content |
| Inline snippet controls | `data-nosnippet` usage | Used only for legally sensitive or intentionally hidden text |
| Image preview controls | `max-image-preview` and `noimageindex` | Large previews allowed where image visibility matters |
| Snippet length | `max-snippet` not overly restrictive | No blanket `max-snippet:0` on content pages |
| Trade-off awareness | Stakeholders understand effect | Restrictions are intentional and documented |

## Directives to flag

| Directive | AI/Search impact |
|---|---|
| `noindex` | Prevents indexing; severe blocker |
| `nosnippet` | Prevents text snippets and may limit AI result use |
| `max-snippet:0` | Equivalent to no text snippet |
| `max-snippet:[low number]` | May restrict useful summaries |
| `data-nosnippet` | Prevents specific elements from appearing in snippets |
| `noimageindex` | Restricts image indexing |
| `max-image-preview:none` | Reduces image preview eligibility |
| `unavailable_after` | Can remove content after a date |
| `noarchive` | Usually less relevant to AI visibility but record |
| `noai` / `noimageai` | Non-standard/emerging; record as policy signal, not universal Google control |

## Recommended actions

- Remove accidental `nosnippet` or `max-snippet:0` from pages intended for AI/search visibility.
- Use `data-nosnippet` narrowly for sensitive text.
- Allow large image previews where image visibility is important:

```html
<meta name="robots" content="max-image-preview:large">
```

- Document deliberate restrictions for legal/compliance reasons.

---

# Theme 5: Page experience

## Intent

Users who click from Google AI experiences should land on fast, usable, trustworthy pages where the promised information is easy to find.

Page experience is not a replacement for content quality, but poor usability can reduce satisfaction and conversions.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| Mobile usability | Works on narrow screens | Valid viewport, no broken layouts |
| Main content prominence | Key content is easy to find | Not buried below ads, interstitials, or excessive hero sections |
| Layout stability | No disruptive shifts | Acceptable CLS where measured |
| Loading performance | Reasonable LCP and load time | CrUX/Lighthouse acceptable |
| Interactivity | Page responds quickly | INP acceptable where measured |
| Intrusive interstitials | No blocking popups before content | Cookie/newsletter overlays not obstructive |
| Accessibility basics | Headings, labels, contrast, alt where needed | Usable with assistive technology |
| Content-to-chrome ratio | Main content not overwhelmed by nav/ads/related links | Clear `<main>` region and section hierarchy |

## Automation-friendly signals

- `<meta name="viewport">`
- `<main>` or `role="main"`
- H1 count
- Image dimensions
- Lazy-loading patterns
- Excessive script count/weight
- Lighthouse performance/accessibility
- CrUX field data

## Recommended actions

- Improve mobile layouts.
- Move direct answer content higher on the page.
- Reduce intrusive popups.
- Improve LCP/INP/CLS.
- Use semantic landmarks.
- Reduce ad/CTA clutter around answer sections.

---

# Theme 6: Multimodal readiness

## Intent

Google’s AI Search experiences are increasingly multimodal. Strong images, video, audio, and product/local data can support search understanding and richer answers.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| Images | Relevant, high-quality images support the text | Not purely decorative on important pages |
| Alt text | Meaningful alt text where image conveys information | Descriptive but not keyword-stuffed |
| Image previews | Google can show useful image previews | `max-image-preview:large` where appropriate |
| Open Graph image | Representative page preview image | `og:image` present on key templates |
| Video | Video has title, description, transcript/captions | `VideoObject` where appropriate |
| Product media | Product images match offers and feeds | Consistent with Merchant Center |
| Local media | Location photos and GBP media are accurate | Useful for local search |
| Transcripts | Audio/video content has text equivalent | Crawlable transcript or summary |
| File accessibility | Media not blocked by robots or hotlink controls | Googlebot can fetch important assets |

## Recommended actions

- Add descriptive alt text for meaningful images.
- Add transcripts for videos and podcasts.
- Add `VideoObject` markup where video is central.
- Use high-quality `og:image` and image dimensions.
- Allow large image previews where appropriate.
- Ensure media URLs are crawlable.

---

# Theme 7: Entity, product, and local ecosystem consistency

## Intent

Google AI Search experiences may draw on the broader Google ecosystem, not just the webpage.

For brands, products, and local businesses, consistency across external Google-managed systems can influence understanding and user trust.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| Organization identity | Name, logo, URL, sameAs are consistent | Matches site, schema, social profiles |
| Google Business Profile | Local details accurate | Name, address, phone, hours, categories match site |
| Merchant Center | Product feed aligns with site | Prices, availability, shipping, returns match page |
| YouTube | Videos have useful titles, descriptions, links | Supports topical/entity visibility |
| Knowledge Panel inputs | Entity facts are consistent across web | About page, schema, Wikidata/Wikipedia if relevant |
| Contact and policy pages | Trust pages are easy to find | About, contact, returns, privacy, editorial policy |

## Status guidance

This theme may be **Not applicable** for purely informational sites with no local, product, or organisation-level entity goals.

For businesses, ecommerce, publishers, healthcare, finance, or local sites, this theme should usually be included.

## Recommended actions

- Align Organization/LocalBusiness schema with public profiles.
- Fix mismatches between product pages and Merchant Center.
- Update Google Business Profile details.
- Strengthen About, Contact, Editorial, Returns, and Support pages.
- Add `sameAs` links to official profiles.

---

# Theme 8: Visit quality measurement

## Intent

AI-powered search may change click behaviour. Users may click less often, but those who do click may have more specific intent.

Measure value beyond raw clicks.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| Conversion tracking | Primary conversions are tracked | Leads, purchases, signups, bookings |
| Micro-conversions | Engagement actions are tracked | Downloads, scroll, video plays, pricing views |
| Landing page engagement | Quality of visits measured | Engaged sessions, time, return visits, assisted conversions |
| Query/page mapping | Search Console queries tied to landing pages | Understand which questions drive visits |
| AI/referral tracking | AI referrers monitored where possible | ChatGPT, Perplexity, Gemini, Copilot patterns |
| Snippet promise match | Landing page satisfies likely AI result promise | Users do not bounce due to mismatch |
| Reporting cadence | Trends reviewed regularly | Monthly or quarterly review |

## Recommended actions

- Define success events for informational and commercial pages.
- Segment organic visits to AI-targeted pages.
- Monitor AI assistant referrers where visible.
- Track assisted conversions and engagement quality.
- Compare landing-page satisfaction before/after content rewrites.

---

# Theme 9: Freshness and ongoing evolution

## Intent

Google AI Search experiences evolve. Content and information architecture should be maintained as user questions, SERP features, and AI answer patterns change.

## Checks

| Check | What to look for | Pass hint |
|---|---|---|
| Update dates | Important pages show credible freshness | `dateModified` visible or in schema where relevant |
| Stale content | Old facts, prices, screenshots, laws, and stats updated | No outdated claims on key pages |
| Content pruning | Weak or obsolete pages improved, merged, or removed | No large stale content footprint |
| Query evolution | New questions reflected in content plan | Search Console and sales/support inputs used |
| Competitor monitoring | AI/organic competitors reviewed | Gaps tracked |
| Sitemap freshness | `lastmod` values are plausible | Not all identical or stale |
| Editorial process | Ownership and review cadence exist | Content owner, reviewer, schedule |

## Recommended actions

- Update pages with time-sensitive facts.
- Add or correct `dateModified`.
- Build content around emerging user questions.
- Merge thin overlapping articles.
- Review AI Overview/AI Mode patterns manually for priority queries.
- Maintain a quarterly GEO/search content backlog.

---

# Google AI Search checklist deliverable

Use this in audit reports.

```markdown
## Google AI Search success — {domain}

**Scope:** {number} URLs reviewed  
**Inputs:** {crawl artifacts, robots, JSON-LD, headers, Search Console if available}  
**Score:** {score}/100, if scored  

### Summary

{2–4 sentences summarising readiness, blockers, and most important actions.}

### Checklist

| Theme | Status | Evidence | Actions |
|---|---|---|---|
| Unique, valuable content | Pass / Partial / Fail / N/A | | |
| Crawl, index, and rendering access | Pass / Partial / Fail / N/A | | |
| Structured data and entity consistency | Pass / Partial / Fail / N/A | | |
| Preview controls and snippet eligibility | Pass / Partial / Fail / N/A | | |
| Page experience | Pass / Partial / Fail / N/A | | |
| Multimodal readiness | Pass / Partial / Fail / N/A | | |
| Entity/product/local ecosystem | Pass / Partial / Fail / N/A | | |
| Visit quality measurement | Pass / Partial / Fail / N/A / Manual check | | |
| Freshness and ongoing evolution | Pass / Partial / Fail / N/A | | |

### Key blockers

1. {Blocker}
2. {Blocker}
3. {Blocker}

### Priority actions

1. {Action}
2. {Action}
3. {Action}
```

---

# URL-level evidence template

Use this when deeper evidence is required.

```markdown
### URL: {url}

| Theme | Status | Evidence | Action |
|---|---|---|---|
| Content value | | | |
| Crawl/index/rendering | | | |
| Preview controls | | | |
| Structured data | | | |
| Page experience | | | |
| Multimodal | | | |
| Freshness | | | |

**Notes:**  
{Short notes tied to this URL.}
```

---

# Integration with `crawl-site.py` / `create-report.py`

Map crawler artifacts as follows.

| Artifact | Themes supported |
|---|---|
| `robots_fetched.txt` | Crawl/index access, Googlebot and Google-Extended checks |
| `robots.txt` | Merged or recommended policy context |
| HTML files / extracted text | Content value, preview controls, page structure, multimodal |
| Response headers | `X-Robots-Tag`, status codes, content type |
| `jsonld/*.json` | Structured data review |
| `json-ld.txt` | Structured data summary |
| `og` tags / `og_images/` | Multimodal and preview checks |
| Sitemap extracts | Discovery and freshness |
| `llms_fetched.txt` | Optional AI guidance context |
| `llms.txt` | Generated/recommended AI guidance context |
| Crawl URL status table | HTTP success, redirects, errors |
| Rendered/raw text comparison | JavaScript and rendering risk |

`create-report.py` computes a **0–100 Google AI Search success proxy** from the same crawl bundle using the **nine-theme weights** in §Optional scoring model: content (22), crawl/index (18), structured data (14), snippet eligibility (10), page experience (10), multimodal via OG coverage (7), entity ecosystem (7), visit quality (7, boosted when `ga4_traffic.json` exists), freshness (7). Caps in §Critical blockers are not auto-applied in code—apply manually when auditing.

---

# References

Use these as source guidance when writing audit notes.

- [AI features and your site](https://developers.google.com/search/docs/appearance/ai-features)
- [Creating helpful, reliable, people-first content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)
- [Using generative AI content on your website](https://developers.google.com/search/docs/fundamentals/using-generative-ai-content)
- [Understanding page experience in Google Search results](https://developers.google.com/search/docs/appearance/page-experience)
- [Robots meta tag, data-nosnippet, and X-Robots-Tag specifications](https://developers.google.com/search/docs/crawling-indexing/robots-meta-tag)
- [Block Search indexing with noindex](https://developers.google.com/search/docs/crawling-indexing/block-indexing)
- [Introduction to structured data markup in Google Search](https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data)
- [General structured data guidelines](https://developers.google.com/search/docs/appearance/structured-data/sd-policies)

---

# Limitations

- This skill estimates readiness; it does not guarantee AI Overview or AI Mode inclusion.
- Google does not expose a complete rule set for AI-generated search experiences.
- Some checks require manual validation in Search Console, Merchant Center, Google Business Profile, or analytics.
- `llms.txt` is not a Google requirement but may be useful in a broader GEO audit.
- AI referrer data may be incomplete or inconsistent.
- Page-level findings should be validated on representative templates before sitewide conclusions.