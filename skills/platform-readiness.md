# Skill: AI search platform readiness

Use this skill to assess how ready a site’s key pages are to be surfaced, cited, linked, or used by major AI search and answer platforms.

This skill compares readiness across:

- Google AI Overviews / AI Mode
- ChatGPT Search / ChatGPT web browsing
- Perplexity
- Google Gemini
- Bing Copilot
- Optional: Claude

**Primary output:** per-platform readiness scores from **0–100**, with confidence level, evidence, assumptions, gaps, and prioritised fixes.

This skill estimates **relative readiness**, not guaranteed citation.

---

## Purpose

Use this skill to answer:

- Which AI/search platforms is the site best prepared for?
- Which platforms are most at risk due to technical, entity, or content gaps?
- Do key pages have the structure and authority needed for citation?
- Are brand/entity signals strong enough across third-party surfaces?
- Are Google/Bing indexes and platform-specific ecosystems being supported?
- What fixes will improve readiness across multiple platforms?

---

## Scope

This skill evaluates platform-specific readiness using:

- Crawl/index access
- Page structure and answerability
- Structured data
- Entity clarity
- Brand visibility
- Third-party corroboration
- Community signals
- Freshness
- Multimodal signals
- Platform-specific discovery systems

### Query-shaped URLs are weak evidence on their own

A URL or title that *looks* like a query (e.g. a slug such as `car-key-battery-replacement`) is only a **weak** readiness signal unless the page body contains **answer-style passages** (definitions, steps, selection logic, comparisons, FAQs with substance). Do not award strong “query coverage” or citability credit from path keywords alone when the template is mainly a product grid or shallow category listing.

It does **not** guarantee:

- AI Overview inclusion
- ChatGPT citation
- Perplexity citation
- Copilot citation
- Gemini response inclusion
- Rankings or traffic

---

## Inputs

Minimum inputs:

| Input | Use |
|---|---|
| Priority URL list | Pages/templates to evaluate |
| HTML / rendered text | Structure, answer blocks, headings, dates |
| `robots.txt` / `robots_fetched.txt` | Googlebot, Bingbot, AI crawler access |
| `json-ld.txt` | Suggested/entity structured data |
| `jsonld/*.json` | Existing JSON-LD extracts |
| `llms.txt` / `llms_fetched.txt` | AI guidance file status |
| Open Graph signals | Preview/multimodal readiness |
| `brand_visibility` | Wikipedia, YouTube, Reddit, LinkedIn scan |
| Sitemap data | Discovery and freshness |

Recommended optional inputs:

| Input | Use |
|---|---|
| Google Search Console | Google rankings, queries, indexing |
| Bing Webmaster Tools | Bing index and crawl health |
| Rank tracking | Top 10 / top 20 status |
| IndexNow status | Bing/Copilot freshness |
| Wikidata | Entity corroboration |
| Google Business Profile | Gemini/local/entity readiness |
| Google Merchant Center | Ecommerce visibility |
| YouTube channel analytics | Gemini/multimodal readiness |
| Reddit/community evidence | Perplexity and ChatGPT corroboration |
| Backlink/PR data | Authority checks |
| CrUX/Lighthouse | Page experience |
| Live AI query tests | Observed citation footprint |

For rubric items the crawl cannot see, mark:

- **Manual check**
- **Assumption — verify**
- **Not available**
- **Not applicable**

---

## Output

Produce:

1. Platform score table
2. Confidence level per platform
3. Evidence and assumptions
4. Cross-platform strengths and gaps
5. Platform-specific fixes
6. Optional observed citation footprint

---

# Status and confidence definitions

## Readiness bands

| Score | Rating | Meaning |
|---:|---|---|
| 90–100 | Excellent | Strong readiness; only minor platform-specific improvements |
| 75–89 | Good | Solid foundation with meaningful optimisation opportunities |
| 60–74 | Moderate | Some strengths, but important gaps reduce citation likelihood |
| 40–59 | Weak | Significant platform-specific blockers or missing signals |
| 0–39 | Poor | Low readiness; major technical/content/entity issues |

## Confidence level

| Confidence | Use when |
|---|---|
| **High** | Crawl data plus platform-specific evidence is available |
| **Medium** | Crawl data is available but some manual evidence is missing |
| **Low** | Many criteria rely on assumptions or unverified signals |
| **Unknown** | Insufficient evidence to score responsibly |

---

# Cross-platform foundations

These signals help nearly all AI search and answer platforms.

| Signal | Why it matters |
|---|---|
| Crawl/index access | Platforms cannot cite what they cannot fetch or index |
| Clear answer blocks | AI systems need extractable passages |
| Self-contained passages | Reduces misquotation and ambiguity |
| Structured headings | Supports retrieval and passage segmentation |
| Tables/lists | Improve extraction for comparisons, steps, pricing, specs |
| Original information gain | Makes the page worth citing |
| Source transparency | Helps systems trust claims |
| Fresh dates | Supports time-sensitive answers |
| Author/publisher trust | Strengthens credibility |
| Schema.org | Helps entity and page-type understanding |
| `sameAs` | Connects the site to official profiles |
| Wikipedia/Wikidata | Strong entity corroboration where applicable |
| YouTube/video | Supports multimodal and Google ecosystem visibility |
| Reddit/community evidence | Supports real-world discussion and corroboration |
| LinkedIn | Supports organisation/entity trust, especially B2B |
| Fast, accessible pages | Improves usability and crawl/render reliability |
| `llms.txt` | Optional curated AI guidance signal |

---

# Platform priority summary

| Platform | Highest-priority signals |
|---|---|
| Google AI Overviews / AI Mode | Google index/rank, helpful content, answer-first structure, snippets, schema, page experience |
| ChatGPT Search | Bing/search visibility, entity clarity, authoritative sources, crawl access, comprehensive answers |
| Perplexity | Freshness, citations, original research, community corroboration, clean source passages |
| Google Gemini | Google ecosystem, Knowledge Graph, YouTube, schema, multimodal, GBP/Merchant Center where relevant |
| Bing Copilot | Bing index, Bing Webmaster Tools, IndexNow, LinkedIn/Microsoft ecosystem, meta/snippet clarity |
| Claude | Long-form clarity, trustworthy sourcing, nuanced explanations, accessible pages |

---

# Scoring approach

You can score in two modes.

## Manual scoring

Use for client deliverables and rigorous audits.

Steps:

1. Select 5–15 priority URLs.
2. Score each platform using the rubric.
3. Mark missing evidence as manual check or assumption.
4. Aggregate across URLs/templates.
5. Add confidence level.

## Automated proxy scoring

Use when interpreting `create-report.py` platform cards.

Automated scores are directional. They may use crawl artifacts such as:

- Robots access
- JSON-LD presence
- `llms.txt`
- `sameAs`
- Open Graph tags
- Brand visibility
- Basic content/technical scores

In **`create-report.py`**, the **AI platform readiness** cards use the same crawl-backed inputs as §Shared evidence map from crawl artifacts, blended into per-surface heuristics (plus an optional **Claude** card). They are **not** a full manual rubric pass.

Automated scores usually do **not** verify:

- Google top 10 rankings
- Bing Webmaster Tools setup
- IndexNow implementation
- Wikidata completeness
- Google Business Profile quality
- Merchant Center
- Backlinks/press
- Core Web Vitals field data
- Live AI citations
- Real community sentiment

State this limitation.

---

# Aggregation

When scoring multiple URLs, use a weighted roll-up.

Suggested page weights:

| Page type | Weight |
|---|---:|
| Flagship guide / source-of-truth article | 3 |
| Core product/service page | 3 |
| Pricing page | 2 |
| Comparison/alternatives page | 2 |
| Support/help article | 1–2 |
| Homepage | 1 |
| Blog/news article | 1 |
| Low-priority archive/tag page | 0.5 |

Formula:

```text
Platform score =
sum(URL platform score × URL weight) / sum(URL weights)
```

Also report the weakest critical template. A high average can hide a weak commercial or source-of-truth page.

---

# Gating rules and caps

Apply caps after scoring.

| Condition | Maximum platform score |
|---|---:|
| Key pages blocked from Googlebot | Google AIO/Gemini max 40 |
| Key pages blocked from Bingbot | ChatGPT/Copilot max 45 |
| Tier 1 AI retrieval crawlers blocked | ChatGPT/Perplexity/Claude max 60 |
| Key pages are `noindex` | All platforms max 50 |
| Main content unavailable without heavy JavaScript | Most platforms max 65 |
| Sitewide `nosnippet` or `max-snippet:0` | Google/Bing surfaces max 60 |
| No meaningful body content on priority pages | All platforms max 50 |
| Major YMYL claims lack sourcing/expertise | All platforms max 60 |
| Brand/entity is ambiguous or conflicts across sources | Entity-heavy platforms max 70 |

---

# Platform 1: Google AI Overviews / AI Mode

## How Google AI Search tends to select sources

Google AI Overviews and AI Mode are strongly connected to Google Search systems. Traditional search eligibility, indexing, and quality are prerequisites.

Readiness depends on:

- Googlebot access
- Indexability
- Organic relevance/ranking
- Helpful, people-first content
- Clear answer passages
- Snippet/preview eligibility
- Structured data
- Page experience
- Trust and source transparency

Industry studies often find high overlap between AI Overview citations and pages already visible in organic results. Treat exact percentages as directional, not universal.

## Optimisation checklist

1. Key pages are indexed and eligible in Google Search.
2. Priority queries have top 10 or top 20 organic visibility.
3. Question-led H2/H3 headings match real user questions.
4. Direct answer appears in the first 1–2 sentences after the heading.
5. Tables are used for comparisons, pricing, specs, or options.
6. Ordered lists are used for processes and steps.
7. Definitions are concise and self-contained.
8. Claims include sources, dates, and caveats where needed.
9. Publication or updated dates are visible where relevant.
10. Author/publisher trust is clear.
11. Structured data matches visible content.
12. Preview controls do not suppress snippets.
13. Page experience is strong enough for users.
14. Important content is present in crawlable/rendered HTML.

## Google AI Overviews rubric

| Criterion | Points | How to score |
|---|---:|---|
| Google indexing and organic visibility | 20 | 20 top 10 for priority queries; 10 top 20; 5 indexed but low; 0 not indexed/manual fail |
| Crawl/index eligibility | 15 | Googlebot allowed, 200 status, no `noindex`, canonical correct |
| Answer-first structure | 15 | Direct answers after query-aligned headings |
| Tables/lists/snippet-friendly formatting | 10 | Strong use of lists/tables/summary blocks |
| Helpful content and originality | 15 | Original, useful, satisfying, people-first |
| Structured data | 10 | Relevant schema, valid, visible-content-aligned |
| Preview controls | 5 | No `nosnippet`, `max-snippet:0`, accidental restrictions |
| Freshness/authorship/trust | 5 | Dates, authors, sources, publisher trust |
| Page experience | 5 | Mobile, speed, readability, low clutter |
| **Total** | **100** | |

---

# Platform 2: ChatGPT Search / web browsing

## How ChatGPT readiness works

ChatGPT web/search visibility may depend on a mix of:

- Search index visibility, especially Bing-linked discovery
- OpenAI crawler access
- User-requested browsing access
- Entity clarity
- Trusted third-party sources
- Comprehensive, well-structured content
- Canonical brand and source pages

Do not assume ChatGPT uses one source pipeline for every experience. Treat crawler access, Bing visibility, and entity corroboration as complementary signals.

## Optimisation checklist

1. `OAI-SearchBot` is allowed.
2. `ChatGPT-User` is allowed.
3. `GPTBot` policy is intentional.
4. Key pages are indexed in Bing or discoverable via search.
5. Bing Webmaster Tools is configured.
6. Brand has clear entity signals on-site.
7. Wikipedia/Wikidata/Crunchbase or equivalent corroboration exists where applicable.
8. About, Contact, leadership, and official profiles are clear.
9. Content is comprehensive and self-contained.
10. Pages include source-backed factual claims.
11. `sameAs` connects official profiles.
12. Reddit/YouTube/LinkedIn presence supports corroboration.
13. `llms.txt` provides a useful curated page map.

## ChatGPT rubric

| Criterion | Points | How to score |
|---|---:|---|
| OpenAI crawler access | 20 | `OAI-SearchBot`, `ChatGPT-User`, and policy for `GPTBot` allowed/intentional |
| Bing/search discoverability | 15 | Bing index coverage, sitemap, Bing WMT |
| Entity clarity and official profiles | 15 | Brand, legal name, About, Contact, `sameAs`, official profiles |
| Third-party corroboration | 15 | Wikipedia/Wikidata/press/reviews/directories as relevant |
| Comprehensive answer content | 15 | Self-contained, complete, useful pages |
| Source transparency and trust | 10 | Citations, dates, authors, policies |
| `llms.txt` and structured data | 5 | Useful `llms.txt`, Organization/WebSite schema |
| Community/multimodal support | 5 | Reddit/YouTube/LinkedIn where relevant |
| **Total** | **100** | |

---

# Platform 3: Perplexity

## How Perplexity readiness works

Perplexity is citation-forward and often rewards sources that are:

- Fresh
- Specific
- Well-sourced
- Easy to quote
- Corroborated by other sources
- Discussed in communities
- Supported by original research or data

It may cite mid-authority sources when they answer a question clearly and provide useful evidence.

## Optimisation checklist

1. `PerplexityBot` is allowed.
2. Key pages are accessible and indexable.
3. Pages contain concise, standalone, quotable paragraphs.
4. Claims are cited and dated.
5. Content includes original data, research, case studies, or expert analysis.
6. Updates are visible and recent for time-sensitive topics.
7. Reddit/community discussion exists where relevant.
8. YouTube/transcripts support topic coverage.
9. Wikipedia/Wikidata or trusted third-party corroboration exists where applicable.
10. Pages avoid vague marketing claims.
11. Comparisons include tables and clear criteria.
12. Support/community questions are answered directly.

## Perplexity rubric

| Criterion | Points | How to score |
|---|---:|---|
| PerplexityBot access | 15 | Allowed and not blocked by key page directives |
| Quotable answer passages | 15 | Standalone paragraphs, concise answers, clear evidence |
| Source transparency | 15 | Citations, dates, named sources, caveats |
| Freshness | 10 | Updated within relevant timeframe |
| Original research/data | 15 | First-party data, methodology, case studies, expert synthesis |
| Community corroboration | 10 | Reddit/forums/communities discuss or validate topic |
| Third-party authority | 10 | Wikipedia, press, reviews, directories, trusted sources |
| Structure and comparison formats | 5 | Tables/lists/headings support extraction |
| YouTube/multimodal support | 5 | Relevant video/transcripts where useful |
| **Total** | **100** | |

---

# Platform 4: Google Gemini

## How Gemini readiness works

Gemini readiness overlaps with Google Search readiness but also benefits from Google’s broader ecosystem:

- Google Search index
- Knowledge Graph
- YouTube
- Google Business Profile
- Merchant Center
- Maps
- Structured data
- Multimodal content

## Optimisation checklist

1. Googlebot can access and index key pages.
2. Google Search visibility exists for priority topics.
3. Entity information is clear and consistent.
4. Organization/LocalBusiness/Product/Article schema is valid.
5. Knowledge Panel or Knowledge Graph presence exists where applicable.
6. YouTube channel has relevant videos, descriptions, chapters, and captions.
7. Google Business Profile is complete for local businesses.
8. Merchant Center is accurate for ecommerce.
9. Images are high quality, crawlable, and well described.
10. Videos have transcripts and metadata.
11. E-E-A-T signals are strong.
12. Page experience is strong.

## Gemini rubric

| Criterion | Points | How to score |
|---|---:|---|
| Google crawl/index/search eligibility | 20 | Googlebot, indexing, organic visibility |
| Entity and Knowledge Graph readiness | 15 | Knowledge Panel, Wikidata, Organization schema, sameAs |
| YouTube readiness | 15 | Relevant channel/videos, captions, descriptions, chapters |
| Schema.org implementation | 15 | Valid and comprehensive structured data |
| Google ecosystem alignment | 10 | GBP, Merchant Center, Maps, News, Scholar as applicable |
| Multimodal readiness | 10 | Images, video, alt, transcripts, previews |
| E-E-A-T and source trust | 10 | Authors, policies, citations, reputation |
| Page experience | 5 | Mobile, speed, readability |
| **Total** | **100** | |

---

# Platform 5: Bing Copilot

## How Bing Copilot readiness works

Bing Copilot is tied to Microsoft/Bing discovery and ranking systems.

Readiness depends on:

- Bingbot access
- Bing index coverage
- Bing Webmaster Tools
- IndexNow
- Clear metadata
- Structured data
- Microsoft ecosystem signals
- LinkedIn for organisation credibility
- Fast, accessible pages

## Optimisation checklist

1. `Bingbot` is allowed.
2. Key URLs are indexed in Bing.
3. Bing Webmaster Tools is configured.
4. XML sitemap is submitted and clean.
5. IndexNow is implemented for new/updated URLs.
6. LinkedIn company page is complete and active.
7. Meta titles/descriptions are accurate and useful.
8. Structured data is valid.
9. Page performance is strong.
10. Microsoft ecosystem signals exist where relevant:
    - GitHub
    - Microsoft Learn
    - Bing Places
    - AppSource / Azure Marketplace
11. Exact-match language appears naturally in title/H1/body.
12. Content has concise, source-backed answer blocks.

## Bing Copilot rubric

| Criterion | Points | How to score |
|---|---:|---|
| Bingbot access and indexability | 20 | Bingbot allowed, 200, no noindex, canonical correct |
| Bing index / Webmaster Tools | 15 | Indexed, WMT verified, sitemap submitted |
| IndexNow implementation | 15 | Active and used on publish/update |
| Metadata and snippet clarity | 10 | Strong titles, descriptions, headings |
| Structured data | 10 | Valid schema for key templates |
| LinkedIn / Microsoft ecosystem | 10 | LinkedIn, GitHub, Bing Places, Microsoft surfaces as relevant |
| Content answerability | 10 | Direct, self-contained, source-backed passages |
| Performance/page experience | 5 | Fast and usable |
| Social/authority signals | 5 | Press, community, trusted external signals |
| **Total** | **100** | |

---

# Optional Platform 6: Claude

## How Claude readiness works

Claude-oriented retrieval benefits from:

- Accessible pages
- Strong long-form explanations
- Nuance and caveats
- Clear sourcing
- Trustworthy authorship
- Well-structured documents
- Low hallucination risk

Claude can handle longer coherent sections, but extraction still benefits from headings, summaries, and self-contained passages.

## Claude optimisation checklist

1. `ClaudeBot` is allowed.
2. Important pages are accessible without heavy JS.
3. Content is coherent, well organised, and complete.
4. Claims are sourced and caveated.
5. Authors, reviewers, and publisher are clear.
6. Long-form guides include summaries and section headings.
7. Sensitive topics avoid overclaiming.
8. `llms.txt` provides useful source-of-truth links.
9. Entity and brand details are consistent.
10. Content includes original insight or expert synthesis.

## Claude rubric

| Criterion | Points | How to score |
|---|---:|---|
| ClaudeBot access | 20 | Allowed and not blocked by key directives |
| Long-form coherence and completeness | 20 | Clear, useful, well structured |
| Source transparency and caveats | 15 | Claims sourced, limits explained |
| Trust and authorship | 15 | Author/publisher/reviewer clarity |
| Passage extractability | 10 | Summaries, headings, self-contained blocks |
| Entity clarity | 10 | Brand/about/schema/sameAs consistency |
| `llms.txt` / documentation support | 5 | Curated source-of-truth links |
| Original insight | 5 | Experience, examples, data |
| **Total** | **100** | |

---

# Shared evidence map from crawl artifacts

| Signal | Artifact/location | Platforms helped |
|---|---|---|
| Robots and bot access | `robots_fetched.txt` | All |
| HTTP 200 / redirects | Crawl summary | All |
| `noindex`, `nosnippet` | HTML/meta/headers | Google, Bing, ChatGPT |
| Raw/rendered content | HTML/extracted text | All |
| Heading hierarchy | HTML | Google, Gemini, Perplexity |
| Tables/lists | HTML | Google, Perplexity, Copilot |
| JSON-LD | `jsonld/*.json` | Google, Gemini, Bing, ChatGPT |
| `sameAs` | JSON-LD / `same_as_urls.txt` | ChatGPT, Gemini, Copilot |
| `llms.txt` | `llms_fetched.txt` | ChatGPT, Claude, general GEO |
| Open Graph | HTML/OG extracts | Gemini, Copilot, social previews |
| Brand visibility | `brand_visibility` | ChatGPT, Perplexity, Gemini |
| YouTube | Brand scan/manual | Gemini, Perplexity |
| Reddit | Brand scan/manual | Perplexity, ChatGPT |
| LinkedIn | Brand scan/manual | Copilot, ChatGPT |
| Sitemap | robots/crawl | Google, Bing, all discovery |
| Dates | HTML/JSON-LD | Google, Perplexity |
| Authors | HTML/JSON-LD | Google, Claude, ChatGPT |

---

# Citation footprint testing

This is an observed visibility layer. It is useful but not deterministic.

For each platform, test a small query set:

| Query type | Example |
|---|---|
| Branded | `{brand} reviews`, `{brand} pricing`, `what is {brand}` |
| Non-branded informational | `how to choose {category}` |
| Comparison | `{brand} vs {competitor}` |
| Best-of | `best {category} for {use case}` |
| Troubleshooting | `why does {problem} happen` |
| Local | `{service} near {location}` |
| Product/service selection | `which {product/service} is right for {scenario}` |

Record:

| Platform | Query | Date | Domain cited? | Competitors cited? | Notes |
|---|---|---|---|---|---|

Use this as context, not as the sole score.

---

# Deliverable template

```markdown
## AI platform readiness — {domain}

**URLs evaluated:** {list or count}  
**Date:** {date}  
**Data sources:** {crawl artifacts, GSC, Bing WMT, brand scan, manual checks}  

### Executive summary

{2–4 sentences. State strongest platform, weakest platform, cross-platform blockers, and top action.}

### Platform scores

| Platform | Score | Confidence | Top strengths | Top gaps | Priority actions |
|---|---:|---|---|---|---|
| Google AI Overviews / AI Mode | /100 | High / Medium / Low | | | |
| ChatGPT Search | /100 | High / Medium / Low | | | |
| Perplexity | /100 | High / Medium / Low | | | |
| Google Gemini | /100 | High / Medium / Low | | | |
| Bing Copilot | /100 | High / Medium / Low | | | |
| Claude, optional | /100 | High / Medium / Low | | | |

### Cross-platform findings

| Finding | Evidence | Impact | Priority |
|---|---|---|---|
| | | | High / Medium / Low |

### Assumptions and manual checks

| Item | Status | Notes |
|---|---|---|
| Google rankings | Verified / Assumption / Missing | |
| Bing index coverage | Verified / Assumption / Missing | |
| Bing Webmaster Tools | Verified / Assumption / Missing | |
| IndexNow | Verified / Assumption / Missing | |
| Wikidata | Verified / Assumption / Missing | |
| GBP / Merchant Center | Verified / Assumption / N/A | |
| Live AI citation tests | Completed / Not completed | |
```

---

# Common recommendations

## Cross-platform answerability

```markdown
Add answer-first blocks under query-aligned H2/H3 headings. Each block should answer the question directly in the first 1–2 sentences, then provide evidence, caveats, and examples.
```

## Google AIO/Gemini

```markdown
Prioritise Google index eligibility, helpful content depth, structured data, and snippet-friendly formatting. Remove accidental preview restrictions such as `nosnippet` or `max-snippet:0`.
```

## ChatGPT

```markdown
Strengthen entity clarity and Bing/search discoverability. Allow OpenAI search/user crawlers, verify Bing indexing, improve About/Contact pages, and add verified `sameAs` links.
```

## Perplexity

```markdown
Increase citation-worthy evidence: add fresh dates, source-backed claims, original data, and standalone paragraphs. Build authentic third-party corroboration in communities and trusted sources.
```

## Bing Copilot

```markdown
Verify Bing Webmaster Tools, submit clean sitemaps, implement IndexNow, improve metadata, and ensure LinkedIn/entity signals are accurate.
```

## Claude

```markdown
Improve long-form clarity, source transparency, caveats, and curated documentation links through `llms.txt`.
```

---

# Limitations

- Scores are readiness estimates, not predictions.
- Platform behaviour changes frequently.
- Many platform signals are not publicly documented.
- Live AI answers vary by user, location, time, and query phrasing.
- Some platforms use licensed data, search indexes, APIs, or third-party content rather than direct crawling.
- Paywalled or login-only content is usually less citable.
- Automated platform cards are directional and should be verified manually for high-stakes audits.
- Industry statistics about AI citation overlap should be treated as context-dependent.