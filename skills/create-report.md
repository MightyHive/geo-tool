```markdown
# Skill: Final synthesized GEO report

Use this skill last, or as the full pipeline entrypoint, to create the final client-ready GEO audit report.

This skill synthesises findings from all other GEO audit skills into a professional report for a **marketing lead or business stakeholder**. It translates technical, content, brand, and platform findings into clear scores, plain-English explanations, and a realistic action plan.

**Primary outputs:**

- `report.html`
- `report_slides.html`
- Optional Streamlit dashboard view
- Optional competitor comparison
- Optional GA4 AI traffic appendix

---

## Purpose

This skill creates a final GEO Readiness report for a submitted website.

The report scores the site across three categories:

1. **AI Visibility**
2. **Technical Setup**
3. **Content Quality & Structure**

It then produces:

- Overall GEO Readiness score out of 100
- Category scores and plain-English explanations
- Skill-level findings
- AI platform readiness cards
- AI crawler access summary
- Brand/entity visibility summary
- E-E-A-T and structured data notes
- Competitor comparison, if supplied
- Prioritised action plan for a resource-constrained team

The final report must be readable by a **non-technical stakeholder**. Technical findings should be translated into business impact and action-oriented recommendations.

---

## Technical approach

Preferred entrypoint:

```bash
python3 create-report.py "https://example.com" --out audit_output
```

With competitors:

```bash
python3 create-report.py "https://example.com" \
  --competitor "https://competitor-a.com" \
  --competitor "https://competitor-b.com" \
  --competitor "https://competitor-c.com" \
  --out audit_output
```

Implementation:

| Script | Role |
|---|---|
| `create-report.py` | Orchestrates crawl, scoring, synthesis, HTML reports |
| `crawl-site.py` | Crawl-only pipeline and artifact generation |
| `api/` + `web/` | React UI and FastAPI audit/archive API |

`create-report.py` runs the crawl, reads generated artifacts, calculates proxy scores, renders HTML, and writes report files into the primary audit directory.

---

# Report philosophy

The final report should:

- Be clear enough for a marketing lead
- Avoid unnecessary technical jargon
- Explain why each issue matters
- Separate urgent blockers from longer-term growth work
- Avoid overclaiming certainty about AI platform behaviour
- Use scores as prioritisation aids, not guarantees
- Make next steps realistic for a team with limited resources

Do not present GEO as a single trick. The report should show that AI visibility depends on:

- Search and AI crawler access
- Citation-worthy content
- Brand/entity clarity
- Structured data
- Third-party brand corroboration
- Platform-specific discovery signals
- Trust and source transparency

---

# Client readability standard

This section is mandatory for all report text rendered above the appendix.

The final report must be written for a non-technical stakeholder. It may include technical terms and filenames, but only when the plain-English meaning is also clear.

## Plain-English technical wording rule

Technical file names and terms may be included in client-facing recommendations, but they must be explained in plain English.

Use this pattern:

```text
Plain-English action first + technical term/file name + why it matters.
```

Good:

- Ask your developer to update the site’s crawler access rules in `robots.txt` so AI search tools can read the public pages you want cited.
- Publish a short AI guide file (`llms.txt`) that points AI tools to your most important product, service, support, company, and policy pages.
- Add structured brand information using JSON-LD so search engines and AI systems can identify the business, website, logo, and official profiles.
- Remove accidental “do not list this page” settings (`noindex`) from important pages.
- Make sure the sitemap lists the key pages you want search engines and AI tools to find.

Avoid:

- Merged `robots.txt` suggestion is in this audit folder.
- No live `llms.txt` at origin.
- Improve discovery: live llms.txt, reachable Sitemap: in robots.txt, and key URLs in sitemap.
- Core Web Vitals and TTFB are not measured in this automated crawl.
- Weighted 25/25/20/15/15 technical score.
- JSON-LD coverage proxy with sameAs density.

---

## Client-facing wording rules

1. Lead with the business action or business meaning.
2. Explain technical files/settings when mentioned.
3. Avoid internal audit phrasing such as “audit folder”, “merged suggestion”, “crawl artifact”, “generated output”, “proxy blend”, or “heuristic subtotal”.
4. Keep exact technical evidence in supporting tables or appendices.
5. Use technical filenames where useful, e.g. `robots.txt`, `llms.txt`, `sitemap.xml`, JSON-LD, but pair them with a plain-English explanation.
6. Do not let low-impact policy choices or measurement-only tasks outrank known blockers.
7. Do not show score formulas, sub-score weights, or internal scoring mechanics in top-level cards.
8. Keep top-level text short: one or two plain-English sentences per card or finding.
9. Replace “what the tool found” with “what this means for the business”.
10. Every recommendation should be assignable to a real owner.

---

## Technical term translation table

Use these translations in scorecards, summaries, and action items.

| Technical term | Plain-English explanation |
|---|---|
| `robots.txt` | The site file that tells crawlers which areas they can access |
| `llms.txt` | A short AI guide file that points AI tools to the site’s most important pages |
| `sitemap.xml` | A list of important pages for search engines and AI tools to discover |
| JSON-LD / schema | Structured page information that helps search engines and AI systems understand the content |
| `sameAs` | Verified links to official brand profiles |
| `noindex` | A setting that tells search engines not to list a page |
| `nosnippet` | A setting that stops search engines from showing a text preview |
| Core Web Vitals | Google’s page speed and usability checks |
| TTFB | Server response speed |
| raw HTML / SSR | Making the main page content visible without relying on JavaScript |
| canonical | A signal that tells search engines which version of a duplicate page is the main one |
| crawlability | Whether search engines and AI tools can access the page |
| indexability | Whether search engines are allowed to list the page |
| structured data | Extra page information that helps machines understand the page |
| entity signals | Clues that connect the website to the real-world brand |

---

## Recommendation normalisation map

Use this map before rendering the action plan or category recommendations.

| Finding keyword | Client-facing action title | Client-facing explanation |
|---|---|---|
| `robots.txt`, crawler blocked | Update crawler access rules in `robots.txt` | Let search engines and AI tools read the public pages you want cited |
| GPTBot / OAI-SearchBot / PerplexityBot blocked | Allow key AI search crawlers to access public content | Give major AI search tools access to pages that should appear in answers |
| Bytespider | Decide whether to allow ByteDance’s AI training crawler | Treat this as a policy choice, not a core visibility fix |
| `llms.txt` missing | Publish an AI guide file (`llms.txt`) | Give AI tools a curated map of the site’s most important pages |
| sitemap missing / unreachable | Fix the sitemap so crawlers can find important pages | Make sure search engines and AI tools can discover priority URLs |
| Core Web Vitals / TTFB | Run a page speed and usability check | Confirm whether slow loading is affecting users or crawlers |
| JSON-LD / schema missing | Add structured page information using JSON-LD | Help search engines and AI systems understand the page and brand |
| `sameAs` missing | Add verified official profile links (`sameAs`) | Connect the website to the correct official brand profiles |
| `noindex` | Remove accidental “do not list this page” settings | Let search engines list important pages |
| `nosnippet` | Allow useful search result previews | Let search engines show text previews from important pages |
| JS-only / SSR | Make key page content visible without relying on JavaScript | Help AI crawlers read the main content more reliably |
| weak answer blocks | Add clear answer summaries to priority pages | Give AI systems short, accurate passages they can cite |
| missing sources | Add sources and dates to important claims | Make factual claims easier to trust and verify |
| weak brand visibility | Strengthen official brand profiles | Make the brand easier for AI systems and users to verify |

---

# Skill map

## Category 1: AI Visibility

| Skill | Role |
|---|---|
| `ai-citability.md` | Page-level passage extractability and citation worthiness |
| `brand-visibility.md` | Brand/entity visibility and third-party corroboration |
| `platform-readiness.md` | Platform-specific readiness for AI/search surfaces |
| `ai-search-success.md` | Google AI Search / AI Overviews readiness |

## Category 2: Technical Setup

| Skill | Role |
|---|---|
| `ai-crawler-report.md` | AI and search crawler access |
| `llms-txt.md` | `llms.txt` discovery, validation, and sample generation |
| `technical-audit.md` | Indexability, rendering, canonicals, speed, mobile, discovery |

## Category 3: Content Quality & Structure

| Skill | Role |
|---|---|
| `eeat.md` | Helpful content, E-E-A-T, trust, source transparency |
| `json-ld.md` | Structured data, schema, entity graph, `sameAs` |

## Cross-cutting / optional

| Skill | Role |
|---|---|
| `competitors.md` | Competitor benchmarking |
| `action-plan.md` | Prioritised action plan |
| `ga4-traffic.md` | AI traffic monitoring |
| `create-report.md` | Final synthesis |

---

# Scoring model

The overall GEO Readiness score is out of 100.

Default category weights:

| Category | Weight |
|---|---:|
| AI Visibility | 40 |
| Technical Setup | 30 |
| Content Quality & Structure | 30 |
| **Total** | **100** |

Formula:

```text
Overall GEO score =
(AI Visibility × 0.40)
+ (Technical Setup × 0.30)
+ (Content Quality & Structure × 0.30)
```

`create-report.py` should use `DEFAULT_WEIGHTS` matching these defaults unless explicitly configured otherwise.

---

# Category scorecard copy

The three category scorecards near the top of the report must explain what each category measures in plain English.

Do **not** show sub-score formulas, weighted component lists, internal score mechanics, or proxy descriptions in these cards.

Those details belong in the scoring table, methodology notes, or appendix.

## Scorecard copy pattern

Each scorecard should contain:

1. **Definition sentence:** what the category measures.
2. **Interpretation sentence:** what the current score means.

Recommended format:

```text
{Definition sentence} {Score-band interpretation sentence}
```

Maximum length: **two short sentences**.

---

## Category definition sentences

| Category | Definition sentence |
|---|---|
| AI Visibility | Measures whether AI search tools can recognise the brand and use the site’s pages as clear, cite-worthy answers. |
| Technical Setup | Measures whether search engines and AI tools can access the site, find the right pages, and read the important content reliably. |
| Content Quality & Structure | Measures whether the site’s content is helpful, trustworthy, well organised, and easy for AI systems to understand. |

---

## Category score-band interpretation

### AI Visibility

| Score | Interpretation |
|---:|---|
| 90–100 | AI search tools have a strong basis to recognise the brand and cite key pages, with only minor improvements needed. |
| 75–89 | The site has a strong AI visibility foundation, but selected pages or platforms still need refinement. |
| 60–74 | The site has some AI visibility strengths, but gaps in answer clarity, brand signals, or platform readiness are limiting citation potential. |
| 40–59 | AI systems may struggle to recognise the brand or find clear, cite-worthy answers on important pages. |
| 0–39 | Major visibility gaps make it difficult for AI search tools to understand, trust, or cite the content. |

### Technical Setup

| Score | Interpretation |
|---:|---|
| 90–100 | Search engines and AI tools should be able to access and read the site reliably, with only minor technical improvements needed. |
| 75–89 | The technical foundation is solid, but a few access, discovery, performance, or crawler-readability issues still need attention. |
| 60–74 | Most technical basics are in place, but some issues may make it harder for crawlers to find or read important pages. |
| 40–59 | Technical issues are likely limiting how reliably search engines and AI tools can access or understand the site. |
| 0–39 | Major technical blockers are preventing the site from being reliably crawled, indexed, or used by AI systems. |

### Content Quality & Structure

| Score | Interpretation |
|---:|---|
| 90–100 | The content is strong, trustworthy, well organised, and gives AI systems clear evidence to work with. |
| 75–89 | The content is generally helpful and well structured, with opportunities to improve evidence, originality, or structured data. |
| 60–74 | The content provides some useful information, but needs clearer answers, stronger trust signals, or better structure to support AI citation. |
| 40–59 | Important content is not yet clear, trusted, or structured enough for strong AI citation performance. |
| 0–39 | The site needs major content improvements before AI systems are likely to treat it as a reliable source. |

---

## Example category scorecard output

Use this style:

```markdown
AI Visibility · weight 40%
41.1
Measures whether AI search tools can recognise the brand and use the site’s pages as clear, cite-worthy answers.
```

```markdown
Technical Setup · weight 30%
78.9
Measures whether search engines and AI tools can access the site, find the right pages, and read the important content reliably.
```

```markdown
Content Quality & Structure · weight 30%
42.4
Measures whether the site’s content is helpful, trustworthy, well organised, and easy for AI systems to understand.
```

Do not output:

```markdown
Weighted 25/25/20/15/15: indexability and canonical crawl health, AI crawler access, SSR/raw HTML, performance/mobile/HTTPS, discovery.
```

---

# Category sub-score models

These details are for methodology sections and internal scoring, not for the top category scorecards.

## 1. AI Visibility — 40% of overall score

Recommended sub-score weights:

| Subcriterion | Weight |
|---|---:|
| AI citability | 30 |
| Platform readiness | 25 |
| Google AI Search success | 15 |
| Brand/entity visibility | 15 |
| Query coverage and citation footprint | 15 |
| **Total** | **100** |

### Plain-English meaning

AI Visibility measures whether the site is likely to be surfaced, selected, cited, or referenced by AI answer systems.

It considers:

- Whether priority pages answer common questions clearly
- Whether the brand is easy to recognise and verify
- Whether major AI search platforms can use the site
- Whether the site has useful third-party corroboration
- Whether manual testing shows the brand already appears in AI answers

### Automation notes

Automated scoring may use proxies such as:

- Page titles, headings, and meta information
- JSON-LD presence
- Open Graph tags
- Brand visibility scan
- `sameAs`
- Crawl/index status
- `llms.txt` 
- AI crawler access

Manual review is needed for:

- Passage-level citability
- Live citation testing
- Search Console rankings
- Community sentiment
- Original information gain
- True query coverage

---

## 2. Technical Setup — 30% of overall score

Recommended sub-score weights:

| Subcriterion | Weight |
|---|---:|
| Indexability, canonicalisation, and crawl health | 25 |
| AI crawler access / robots rules | 25 |
| Server-side rendering and raw HTML completeness | 20 |
| Performance, mobile, HTTPS, and page experience | 15 |
| `llms.txt`, sitemaps, freshness, and discovery signals | 15 |
| **Total** | **100** |

### Plain-English meaning

Technical Setup measures whether search engines and AI systems can reliably access, understand, and discover the site.

It considers:

- Whether important pages load successfully
- Whether crawlers are allowed to access public content
- Whether search engines are allowed to list key pages
- Whether the site clearly identifies the main version of each page
- Whether important page content is visible to crawlers
- Whether the sitemap and `llms.txt` help discovery
- Whether the site is secure, mobile usable, and reasonably fast

### Automation notes

Automated scoring may use proxies from crawl artifacts. Some checks require manual or external validation:

- Core Web Vitals
- Server response speed
- Search Console indexing
- Bing Webmaster Tools
- IndexNow
- Server logs
- Full mobile layout testing

---

## 3. Content Quality & Structure — 30% of overall score

Recommended sub-score weights:

| Subcriterion | Weight |
|---|---:|
| E-E-A-T and helpful content | 35 |
| Original information gain | 20 |
| Passage-level answerability | 20 |
| JSON-LD / schema / entity markup | 15 |
| Source transparency and content governance | 10 |
| **Total** | **100** |

### Plain-English meaning

Content Quality & Structure measures whether the content is useful, trustworthy, original, structured, and understandable.

It considers:

- Whether the content is genuinely helpful to users
- Whether important pages give clear direct answers
- Whether claims are backed by sources and dates
- Whether authorship and expertise are clear
- Whether the site adds original insight, examples, or data
- Whether structured data helps search engines and AI systems understand the content

### Automation notes

Automated scoring may use proxies such as:

- JSON-LD coverage
- Author/date/schema fields
- Content depth
- Heading structure
- `sameAs`
- Policy page presence
- Brand visibility

Manual review is required for:

- True E-E-A-T
- Content usefulness
- Factual accuracy
- Source quality
- Author expertise
- Original information gain

---

# Overall score caps

Apply caps after calculating the raw score.

| Condition | Maximum overall score |
|---|---:|
| Site blocks Googlebot or Bingbot on key pages | 50 |
| Site blocks most major AI search/retrieval crawlers | 60 |
| Key content unavailable in raw HTML and not reliably renderable | 65 |
| Key pages are `noindex` or canonicalised elsewhere unintentionally | 55 |
| Widespread 4xx/5xx errors on priority pages | 60 |
| No meaningful body content on priority pages | 50 |
| Major unsupported YMYL claims | 65 |
| Materially false or misleading content | 40 |

State any cap clearly in the report.

Example:

```markdown
Raw score: 72/100  
Cap applied: 60/100 because most major AI search crawlers are blocked.  
Final score: 60/100
```

---

# Score labels

Use these labels in the report header and score cards.

| Score range | Rating | Meaning |
|---:|---|---|
| 90–100 | Excellent | Strong GEO readiness; likely crawlable, understandable, and citation-worthy |
| 75–89 | Good | Solid foundation with meaningful optimisation opportunities |
| 60–74 | Moderate | Some GEO strengths, but important gaps reduce AI visibility |
| 40–59 | Weak | Significant technical, content, or authority issues limit AI visibility |
| 0–39 | Poor | Major remediation needed before the site is likely to perform well in AI answer systems |

Keep naming consistent across all report sections.

---

# Full report layout

`report.html` should include these sections:

1. Header
2. Executive summary
3. Score overview
4. Scoring tables
5. AI platform readiness
6. Findings by category
7. Prioritised action plan
8. Competitor comparison, if available
9. GA4 AI traffic appendix, if available
10. Sample files and artifacts

`report_slides.html` should provide a condensed presentation-style version.

---

# Section 1: Header

Display:

- Site scanned
- Audit date/time
- Overall GEO Readiness score
- Rating label
- Optional: number of pages sampled
- Optional: competitor count

Header example:

```markdown
GEO Readiness Audit — example.com  
Score: 72/100 — Moderate  
Pages sampled: 50  
Audit date: 2026-05-08
```

---

# Section 2: Executive summary

Write exactly **one paragraph**.

Target length: **4–6 sentences**.

Tone:

- Confident
- Direct
- Professional
- Plain English
- No tool narration
- No unnecessary jargon

Use `<strong>...</strong>` in HTML for key priorities.

## Required content

The paragraph should include:

1. Scope: number of pages sampled.
2. Score: short mention if useful; do not repeat too much header metadata.
3. Single most important finding.
4. Top three priorities, bolded.
5. Business impact in plain language.

## Example

```html
<p>
We reviewed <strong>50 pages</strong> from example.com across AI visibility, technical setup, and content quality. The site has a <strong>Moderate GEO readiness score of 72/100</strong>, with a solid technical base but weaker citation signals on priority content. The most important gap is that key service pages explain the offer but do not yet provide clear, source-backed answer blocks that AI systems can quote. The top priorities are to <strong>add direct answer summaries to priority pages</strong>, <strong>roll out verified structured data</strong>, and <strong>strengthen brand/entity signals across official profiles</strong>. These changes will make the site easier for search engines and AI assistants to understand, trust, and cite.
</p>
```

---

# Section 3: Score overview

Show three metric cards:

| Card | Content |
|---|---|
| AI Visibility | Score, rating, plain-English category definition, score-band interpretation |
| Technical Setup | Score, rating, plain-English category definition, score-band interpretation |
| Content Quality & Structure | Score, rating, plain-English category definition, score-band interpretation |

## Mandatory wording rule

The score overview cards must use the copy from **Category scorecard copy** above.

Do not use:

- Sub-score weights
- Formula descriptions
- Internal component lists
- Technical-only terms
- Proxy-score language
- Raw skill names

Good:

```markdown
Measures whether search engines and AI tools can access the site, find the right pages, and read the important content reliably. The technical foundation is solid, but a few access, discovery, performance, or crawler-readability issues still need attention.
```

Bad:

```markdown
Weighted 25/25/20/15/15: indexability and canonical crawl health, AI crawler access, SSR/raw HTML, performance/mobile/HTTPS, discovery.
```

---

# Section 4: Scoring tables

Include two tables.

## 4.1 Category scoring table

| Category | Weight | Score | Weighted points | Rating |
|---|---:|---:|---:|---|
| AI Visibility | 40% | | | |
| Technical Setup | 30% | | | |
| Content Quality & Structure | 30% | | | |
| **Overall** | **100%** | | | |

## 4.2 Skill sub-score table

List all contributing skills.

| Category | Skill | Score | Rating | Plain-English notes |
|---|---|---:|---|---|
| AI Visibility | `ai-citability.md` | | | |
| AI Visibility | `brand-visibility.md` | | | |
| AI Visibility | `platform-readiness.md` | | | |
| AI Visibility | `ai-search-success.md` | | | |
| Technical Setup | `ai-crawler-report.md` | | | |
| Technical Setup | `llms-txt.md` | | | |
| Technical Setup | `technical-audit.md` | | | |
| Content Quality & Structure | `eeat.md` | | | |
| Content Quality & Structure | `json-ld.md` | | | |

Add manual-check labels where a score is proxy-only.

Keep notes short and plain English.

---

# Section 5: AI platform readiness

Render readiness cards for:

- Google AI Overviews / AI Mode
- ChatGPT Search
- Perplexity
- Google Gemini
- Bing Copilot
- Optional: Claude

Each card should include:

- Score
- Confidence
- Main strength
- Main gap
- Recommended action

## Client-facing platform wording

| Platform issue | Client-facing action |
|---|---|
| Google AIO weak answer structure | Add short, direct answers to priority pages so Google can summarise them more easily |
| ChatGPT weak entity clarity | Make the brand easier to verify across the website and official profiles |
| Perplexity weak sourcing | Add sources, dates, and original evidence to pages that should be cited |
| Gemini weak multimodal signals | Improve images, video, transcripts, and Google ecosystem profiles |
| Copilot weak Bing setup | Set up Bing discovery tools so Microsoft search and Copilot can find updates faster |
| Claude weak long-form trust | Improve source transparency, caveats, and long-form explanations |

## Table option

| Platform | Score | Confidence | Main strength | Main gap | Priority action |
|---|---:|---|---|---|---|
| Google AI Overviews / AI Mode | | High / Medium / Low | | | |
| ChatGPT Search | | High / Medium / Low | | | |
| Perplexity | | High / Medium / Low | | | |
| Google Gemini | | High / Medium / Low | | | |
| Bing Copilot | | High / Medium / Low | | | |
| Claude | | High / Medium / Low | | | |

Use `platform-readiness.md` for scoring logic, but simplify final wording.

---

# Section 6: Findings by category

For each category, include:

1. Category heading with score
2. Short plain-English summary
3. Three boxes:
   - What is working
   - What needs work
   - Recommendations
4. Skill-level details and relevant tables

---

## 6.1 AI Visibility findings

Include:

- AI citability summary
- Platform readiness summary
- Google AI Search success summary
- Brand visibility summary
- Query coverage / citation footprint notes, if available

### AI Visibility boxes

| Box | Include |
|---|---|
| What is working | Strong answer pages, brand presence, platform strengths, good snippets |
| What needs work | Weak answer blocks, unclear brand signals, missing query coverage, weak third-party corroboration |
| Recommendations | Add answer-first content, add citations/sources, fix brand profiles, improve platform-specific gaps |

### Brand visibility table

Populated from `brand_visibility` in `audit_summary.json`.

Platforms:

- Wikipedia
- YouTube
- Reddit
- LinkedIn

| Platform | Presence | URL / page | Confidence | Notes | Action |
|---|---|---|---|---|---|
| Wikipedia | Confirmed / Likely / Not found / Ambiguous | | High / Medium / Low | | |
| YouTube | Confirmed / Likely / Not found / Ambiguous | | High / Medium / Low | | |
| Reddit | Confirmed / Likely / Not found / Ambiguous / N/A | | High / Medium / Low | | |
| LinkedIn | Confirmed / Likely / Not found / Ambiguous | | High / Medium / Low | | |

Notes:

- Automated matches are likely, not always confirmed.
- Manual verification is required for ambiguous results.
- The brand visibility scan does not replace `sameAs` validation.
- Verified official URLs should feed JSON-LD recommendations.

---

## 6.2 Technical Setup findings

Include:

- Technical audit summary
- AI crawler access table
- `llms.txt` status
- Rendering/raw HTML risk
- Indexability/canonical notes
- Sitemap/discovery notes

### Technical Setup boxes

| Box | Include |
|---|---|
| What is working | Public pages are accessible, HTTPS works, sitemap exists, mobile basics are present, `llms.txt` exists if live |
| What needs work | Blocked crawlers, accidental `noindex`, key content hidden behind JavaScript, duplicate URL signals, speed or discovery gaps |
| Recommendations | Update `robots.txt`, remove accidental blocking settings, make key content crawler-readable, publish `llms.txt`, clean the sitemap |

### AI crawler access table

Include major crawlers from `ai-crawler-report.md`.

| Crawler | Operator | Tier | Status | Evidence | Recommendation |
|---|---|---:|---|---|---|
| OAI-SearchBot | OpenAI | 1 | | | |
| ChatGPT-User | OpenAI | 1 | | | |
| GPTBot | OpenAI | 1 | | | |
| ClaudeBot | Anthropic | 1 | | | |
| PerplexityBot | Perplexity | 1 | | | |
| Googlebot | Google | 2 | | | |
| Bingbot | Microsoft | 2 | | | |
| Google-Extended | Google | 2 | | | |
| GoogleOther | Google | 2 | | | |
| Applebot | Apple | 2 | | | |
| Applebot-Extended | Apple | 2 | | | |
| Amazonbot | Amazon | 2 | | | |
| FacebookBot | Meta | 2 | | | |
| CCBot | Common Crawl | 3 | | | |
| anthropic-ai | Anthropic | 3 | | | |
| cohere-ai | Cohere | 3 | | | |
| Bytespider | ByteDance | 3 | | | |
| meta-externalagent | Meta | 3 | | | |

Use plain language in recommendations.

Example:

```markdown
Allow this crawler on public content if AI search visibility is a priority.
```

For training-only crawlers:

```markdown
Decide whether this AI training crawler fits your data-use policy. This is not a core search visibility fix.
```

---

## 6.3 Content Quality & Structure findings

Include:

- E-E-A-T / helpful content summary
- Original information gain
- Passage-level answerability
- Source transparency
- Content governance
- JSON-LD / structured data summary

### Content Quality boxes

| Box | Include |
|---|---|
| What is working | Useful pages, clear expertise, structured data coverage, sources, strong guides |
| What needs work | Thin content, weak authorship, missing sources, little original evidence, weak structured data |
| Recommendations | Add authorship, sources, original examples, structured data, and a repeatable content review process |

### E-E-A-T proxy subsection

Show proxy scorecards or bars for:

- People-first purpose
- Content quality
- Original information gain
- Experience/expertise
- Trust/sourcing
- Authoritativeness
- Governance

State clearly:

```markdown
These are crawl-based signals. A full content quality review should also check authors, sources, factual accuracy, and editorial process.
```

### JSON-LD subsection

Show:

| Template / URL | Current schema | Status | Recommended fix |
|---|---|---|---|
| Homepage | | | |
| Article pages | | | |
| Product/service pages | | | |
| Local pages | | | |

---

# Section 7: Prioritised action plan

Use `action-plan.md`.

The action plan must be realistic for a resource-constrained business.

Show three columns:

| Horizon | Timeframe | Max items |
|---|---:|---:|
| Quick wins | 0–30 days | 5 |
| Medium-term | 30–90 days | 5 |
| Strategic | 90+ days | 5 |

Each action should include:

- Plain-English action
- Technical file/setting if useful
- Why it matters
- Owner, if space allows
- Effort
- Estimated GEO score lift

## Action item format

```markdown
- **Publish an AI guide file (`llms.txt`).** Give AI tools a short guide to the site’s most important pages.  
  Owner: SEO/Developer · Effort: Low · Est. +1–3 pts overall
```

Use `_priorities_spaced()` to enforce caps and `_geo_readiness_lift_hint()` for first-pass lift estimates. Human editors should tune action wording and lift ranges.

## Action plan priority rules

Known blockers should outrank measurement tasks and policy-only tasks.

Usually prioritise:

1. Fix crawler/indexing blockers
2. Allow AI/search crawler access where visibility is desired
3. Make key content visible to crawlers
4. Publish or improve `llms.txt` and sitemap discovery
5. Add structured brand/page information
6. Improve direct answer content on priority pages
7. Strengthen official brand profiles

Do not prioritise these above known blockers unless the audit shows they are the main risk:

- Core Web Vitals measurement only
- TTFB measurement only
- Bytespider / training-crawler policy decisions
- Low-impact metadata cleanup
- Nice-to-have social profile updates

---

# Section 8: Competitor comparison

Show this section only if competitors were supplied.

Use `competitors.md`.

## 8.1 Agent category comparison

| Site | AI Visibility | Technical Setup | Content Quality & Structure | Overall |
|---|---:|---:|---:|---:|
| Primary | | | | |
| Competitor A | | | | |
| Competitor B | | | | |
| Competitor C | | | | |

## 8.2 GEO benchmark scorecard

| Site | Technical discoverability /25 | AI access /20 | Structured data & entity /20 | Content citability /20 | Brand visibility /10 | Multimodal /5 | Total /100 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Primary | | | | | | | |
| Competitor A | | | | | | | |
| Competitor B | | | | | | | |
| Competitor C | | | | | | | |

## 8.3 Competitor narrative

Include:

- Where the primary site leads
- Where competitors lead
- Most important competitive gap
- Priority action to close the gap

Keep this narrative concise and plain-English.

---

# Section 9: GA4 AI traffic appendix

Show only if `ga4_traffic.json` is present or GA4 extraction was run.

Use `ga4-traffic.md`.

Include:

- AI traffic trend
- Total traffic vs AI traffic
- AI traffic as percentage of total
- Source/medium gaps
- Notes on tracking limitations

Chart guidance:

- X-axis: month-year
- Left Y-axis: sessions
- Show total sessions and AI sessions
- Label AI share percentage

Avoid overclaiming attribution. AI referral tracking is often incomplete.

---

# Section 10: Sample files and artifacts

Include snippets or links to:

| File | Purpose |
|---|---|
| `robots_fetched.txt` | Live crawler access evidence |
| `robots.txt` | Suggested crawler access rules |
| `llms_fetched.txt` | Live AI guide file, if found |
| `llms.txt` | Generated AI guide file draft |
| `json-ld.txt` | Suggested starter structured data |
| `jsonld/*.json` | Extracted structured data |
| `comparison.json` | Competitor metrics |
| `audit_summary.json` | Summary data |

Keep snippets short. This section can be more technical because it is an appendix/reference section.

---

# Full pipeline

## Standard report

```bash
python3 create-report.py "https://www.example.com" --out audit_output
```

## With competitors

```bash
python3 create-report.py "https://www.example.com" \
  --competitor "https://www.competitor-a.com" \
  --competitor "https://www.competitor-b.com" \
  --competitor "https://www.competitor-c.com" \
  --out audit_output
```

## Same command on one line

```bash
python3 create-report.py "https://www.example.com" --competitor "https://www.competitor-a.com" --competitor "https://www.competitor-b.com" --competitor "https://www.competitor-c.com" --out audit_output
```

## Reports only, no crawl

```bash
python3 create-report.py --only-report audit_output/example.com_abc123
```

## Crawl only

```bash
python3 crawl-site.py "https://www.example.com" --competitor "https://www.peer.com" --out audit_output
```

Then:

```bash
python3 create-report.py --only-report audit_output/example.com_abc123
```

---

# CLI notes

`create-report.py` should forward relevant crawl options to `crawl-site.py`, including:

- `--out`
- `--max-sitemap-urls`
- `--max-sitemaps`
- `--delay`
- `--insecure`
- `--no-certifi`
- `--sample-robots`
- `--sample-llms`
- `--brand`
- `--no-brand-scan`
- `--competitor`, max 3

## Shell line-continuation note

In `bash` or `zsh`, a line-ending backslash continues the command. Nothing may follow it on the same line.

Correct:

```bash
python3 create-report.py "https://example.com" \
  --competitor "https://competitor.com" \
  --out audit_output
```

Incorrect:

```bash
python3 create-report.py "https://example.com" \ --competitor "https://competitor.com"
```

---

# Optional GA4 appendix

## Automated GA4

Pass:

```bash
python3 create-report.py "https://example.com" \
  --ga4-property "123456789" \
  --ga4-ai-channels "Organic AI,AI Referral"
```

Or set environment variables:

```text
GA4_PROPERTY_ID
GA4_AI_CHANNEL_NAMES
GOOGLE_APPLICATION_CREDENTIALS
```

Requires `google-analytics-data`.

## Manual GA4

Save:

```text
{audit_dir}/ga4_traffic.json
```

Then run:

```bash
python3 create-report.py --only-report audit_output/example.com_abc123
```

Expected keys may include:

- `has_ai_channel`
- `ai_channel_names`
- `weekly`
- `monthly`
- `source_medium_gaps`
- `total_sessions`
- `ai_sessions`

---

# Web UI

Run:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./scripts/run_web_dev.sh
```

The React UI (`web/`) + FastAPI (`api/`) provide:

- Setup wizard (brand URL, market, competitors, prompts, GA4 connect)
- Audit run with live progress
- Archive of previous audits
- Embedded `report.html` and slide deck views
- Optional demo/sample audits when available

---

# Workflow for agents

1. Confirm primary URL, brand name, market, and up to three competitor URLs.
2. Run `create-report.py`.
3. Review `report.html` and `audit_summary.json`.
4. Run deeper manual skills where needed:
   - `ai-citability.md`
   - `eeat.md`
   - `brand-visibility.md`
   - `platform-readiness.md`
   - `technical-audit.md`
5. Add manual findings or adjust scores where proxy evidence is insufficient.
6. Add `ga4_traffic.json` if analytics data is available.
7. Re-run `create-report.py --only-report`.
8. Edit executive summary and action plan for client context.
9. Deliver `report.html` and `report_slides.html`.

---

# Quality checks before delivery

Before sending the final report, confirm:

- Overall score uses the correct category weights.
- Category weights sum to 100.
- Category scorecards use plain-English category definitions and score-band interpretations.
- Scorecard text does not expose sub-score formulas or internal weighting.
- Category sub-scores are explained in plain English.
- Any score caps are disclosed.
- Executive summary is one paragraph.
- Top three priorities are clear and bolded.
- Action plan has no more than five items per horizon.
- Action items explain technical filenames/settings when used.
- Technical jargon is explained or moved to appendix.
- Competitor comparisons are fair and caveated.
- Brand visibility matches are not overstated.
- Automated proxy scores are labelled where appropriate.
- Sample files are short and not overwhelming.
- Manual assumptions are listed.
- The report does not guarantee AI citations or rankings.

---

# Related skills

| Skill | Role | Category |
|---|---|---|
| `ai-citability.md` | Passage extractability and citation likelihood | AI Visibility |
| `brand-visibility.md` | Brand/entity visibility and corroboration | AI Visibility |
| `platform-readiness.md` | Per-platform AI/search readiness | AI Visibility |
| `ai-search-success.md` | Google AI Search success audit | AI Visibility |
| `ai-crawler-report.md` | AI/search crawler access | Technical Setup |
| `llms-txt.md` | `llms.txt` validation and generation | Technical Setup |
| `technical-audit.md` | Indexability, rendering, speed, mobile, discovery | Technical Setup |
| `eeat.md` | Helpful content, E-E-A-T, trust | Content Quality & Structure |
| `json-ld.md` | Structured data and entity graph | Content Quality & Structure |
| `competitors.md` | Competitor crawl and GEO benchmark | Cross-cutting |
| `action-plan.md` | Prioritised action plan | Cross-cutting |
| `ga4-traffic.md` | AI traffic monitoring | Cross-cutting |
```