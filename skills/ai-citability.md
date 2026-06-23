# Skill: AI citability

Use this skill to analyse the **actual body content** of a site’s **key web pages** and estimate how likely major **AI answer systems** are to **extract, reuse, and cite** those pages.

This skill evaluates **passage extractability**: whether an AI system can lift a contiguous passage, treat it as a direct answer, and attribute it without distorting meaning.

It does **not** measure only crawlability, ranking, backlinks, or traditional SEO performance.

**Target surfaces:** ChatGPT / ChatGPT Search, Perplexity, Gemini / Google AI Overviews, Claude, Bing Copilot.

**Primary output:** A **citability score 0–100** per page, query, or template roll-up, with evidence-backed findings and concrete passage-level rewrite recommendations.

**Pair with:**

| Skill | Use |
|---|---|
| `platform-readiness.md` | Platform-specific citation patterns and scoring |
| `eeat.md` | Trust, expertise, people-first depth, source transparency |
| `ai-crawler-report.md` | Whether AI crawlers can access the content |
| `technical-audit.md` | Indexability, rendering, raw HTML availability |
| `ai-search-success.md` | Google AI Overviews / AI Mode readiness |

---

## Why this is not traditional SEO copywriting

Traditional SEO often optimises for **keyword relevance**, **internal linking**, **rankability**, and **engagement**.

GEO citability optimises for **extractability**:

- Can an AI answer system identify a passage as answering a user question?
- Can it quote or paraphrase the passage without losing meaning?
- Is the passage self-contained enough to stand outside the original page?
- Are claims specific, verifiable, and safe to cite?
- Does the page offer something worth citing over generic or more authoritative sources?

A page can rank well in search but still be weak for AI citation if its content is vague, promotional, fragmented, or dependent on visual context.

---

## Research context

Early GEO research, including work associated with researchers from Princeton, Georgia Tech, IIT Delhi, and others, has reported that content adapted for generative answer systems can achieve higher visibility in AI-generated responses in controlled settings.

Treat reported uplift figures as **context-dependent**, not universal benchmarks.

A consistent theme in this research and in observed AI answer behaviour is that models often prefer passages that are:

- **Self-contained**
- **Answer-led**
- **Fact-rich**
- **Well structured**
- **Supported by named sources or clear evidence**
- **Specific about dates, units, entities, and scope**
- Easy to extract as a coherent block

A useful design target for longer answer blocks is roughly **134–167 words**, but this is not a rule. Shorter passages may be better for definitions, FAQs, and factual answers. Quality, clarity, truthfulness, and source support dominate.

---

## When to use

Use this skill for audits where the client asks:

- “Will ChatGPT quote us?”
- “Why does Perplexity cite competitors but not us?”
- “Could this guide appear in Google AI Overviews?”
- “Are our product/service pages answerable enough for AI search?”
- “What should we rewrite to become a better source?”

Use it especially after publishing or auditing:

- Flagship guides
- Product education pages
- Service pages
- Comparison pages
- Alternatives pages
- Pricing pages
- Support articles
- FAQs
- Research reports
- Definition or glossary pages
- Local service pages
- “Source of truth” pages

---

## Scope of this skill

This skill scores **on-page passage citability**.

It does not directly score:

- Whether crawlers are allowed to fetch the page
- Whether the page is indexed
- Whether the brand is authoritative off-site
- Whether Google or Bing ranks the page
- Whether live AI systems currently cite the page
- Whether the page has enough backlinks
- Whether the page passes Core Web Vitals

However, these factors can affect real-world AI visibility. Cross-check with the related skills where needed.

---

# Scoring levels

This skill can be applied at three levels.

## 1. Page-level citability

Measures how extractable and cite-worthy the page is overall.

Use when auditing a page without a defined target query set.

## 2. Query-level citability

Measures how well the page answers a specific question or prompt type.

Use when the stakeholder provides target questions, or when the audit identifies likely AI-search query patterns.

A page can have a strong page-level score but a weak query-level score if it is well written but does not answer the questions AI systems are likely to retrieve for.

## 3. Template-level citability

Measures recurring content patterns across many similar pages.

Use for:

- Product pages
- Location pages
- Category pages
- Glossary pages
- Support article templates
- Comparison page templates
- Blog templates

Score 3–5 representative URLs where possible and report whether issues are **template-level** or **page-specific**.

---

# Inputs

## Minimum inputs

1. **URL list** of key pages, usually 5–15 URLs:
   - Homepage
   - Core product/service pages
   - Flagship guides
   - Comparison/alternatives pages
   - Pricing pages
   - Support or definitive articles
   - Pages intended to be “source of truth” content

2. **Rendered main content** for each page:
   - HTML
   - Reader-mode text
   - Extracted main content from a crawler
   - Rendered DOM text where JavaScript affects content

3. Optional but recommended:
   - Target questions the page should answer
   - Search Console queries
   - Stakeholder-priority queries
   - Competitor pages
   - Brand/entity notes
   - Publication/update dates
   - Author/reviewer information
   - JSON-LD extracts

## Preferred content source

Prefer content as a bot or crawler would see it.

If available, compare:

- Raw HTML
- Rendered DOM
- Reader-mode extraction
- Visible browser page

If important answer content is only present after JavaScript rendering, in tabs, accordions, carousels, or interactive widgets, flag this as an extractability risk and cross-check `technical-audit.md`.

---

# Query coverage / answer demand

Citability depends on whether the site has pages that match the **types of questions** AI systems answer.

Before scoring passages, do a quick coverage check.

Look for pages or sections that map to:

- **Informational queries**: “what is…”, “how does…”
- **Definition queries**: terms, acronyms, concepts
- **Comparison queries**: “X vs Y”, “alternatives to…”
- **Best-of queries**: “best X for Y”
- **Troubleshooting queries**: “why does…”, “how to fix…”
- **Pricing queries**: ranges, plans, cost drivers
- **Local intent queries**: areas served, locations, eligibility
- **Product/service selection queries**: “which option fits which use case?”
- **Safety / risk queries**: “is X safe?”, “is X compliant?”
- **Process queries**: “how to do X”, “steps to…”
- **Eligibility queries**: “who qualifies for…”
- **Regulatory queries**: “what does [law/standard] require?”

If the site lacks pages for the highest-demand query shapes in its market, call that out as a **citability limiter**, even if existing pages are well written.

---

## Query fit check

When target questions are available, complete this table.

| Target question | Best matching passage? | Direct answer in first 1–2 sentences? | Missing information | Query fit score 0–10 |
|---|---|---:|---|---:|
| | Yes / Partial / No | Yes / Partial / No | | |
| | Yes / Partial / No | Yes / Partial / No | | |

### Query fit scoring

| Score | Meaning |
|---:|---|
| 9–10 | The page has a direct, self-contained answer that fits the query closely. |
| 7–8 | The page answers the query, but the answer needs clearer framing, source support, or scope. |
| 4–6 | The page partially answers the query but requires synthesis across sections. |
| 1–3 | The page mentions the topic but does not provide a usable answer. |
| 0 | No relevant answer found. |

Query fit does not replace the page citability score. Use it to explain why a page may or may not be selected for specific AI answers.

---

# Candidate passage inventory

Before scoring, identify **2–5 candidate answer blocks** per page.

A candidate answer block can be:

- A paragraph
- A short list
- A table
- An FAQ answer
- A definition box
- A comparison section
- A procedural step sequence
- A data/statistics block
- A quoted expert explanation

Record the candidates before scoring.

| Passage ID | Section / heading | User question answered | Passage type | Approx. words | Initial assessment |
|---|---|---|---|---:|---|
| P1 | | | Definition / comparison / process / data / recommendation / FAQ | | Strong / Partial / Weak |
| P2 | | | Definition / comparison / process / data / recommendation / FAQ | | Strong / Partial / Weak |
| P3 | | | Definition / comparison / process / data / recommendation / FAQ | | Strong / Partial / Weak |

Use these candidate passages as the evidence base for the score.

---

# Passage types to look for

Strong citability usually comes from one or more of these passage types.

| Passage type | Best for | What good looks like |
|---|---|---|
| Definition block | “What is X?” | 40–80 words, direct definition, scope, example |
| Explainer block | “How does X work?” | Answer-first paragraph followed by mechanism, steps, or evidence |
| Comparison block | “X vs Y” | Clear criteria, table, differences, explicit recommendation by use case |
| Decision block | “Which X should I choose?” | Advice segmented by audience, constraints, budget, or scenario |
| Data block | “How much / how many / how often?” | Numbers, units, date, source, methodology |
| Procedure block | “How do I do X?” | Ordered steps, prerequisites, outcome, warnings |
| Troubleshooting block | “Why is X happening?” | Symptoms, likely causes, fixes, escalation path |
| Evidence block | “Is X true / safe / effective?” | Claim, source, caveats, date, limits |
| Local/service block | “Who provides X near me?” | Location, service area, eligibility, operating constraints |
| Pricing block | “How much does X cost?” | Price ranges, cost drivers, inclusions/exclusions, date |
| Regulatory block | “What does X require?” | Jurisdiction, authority, current date, requirements, caveats |

---

# Core scoring rubric

Score each page out of **100**.

| Criterion | Max score |
|---|---:|
| 1. Quality of answer blocks | 25 |
| 2. Self-containment of passages | 20 |
| 3. Structural readability & extractability | 15 |
| 4. Data points & verifiability | 25 |
| 5. Uniqueness & citation worthiness | 15 |
| **Total** | **100** |

Document short excerpts as evidence. Keep excerpts brief in summary reports unless the client specifically requests full passage review.

---

## Product listings vs citation-worthy content

**Technical extractability** (crawlable HTML, headings, previews, structured data) is **not** the same as **content citability** (clear, self-contained passages an AI could cite).

- **Category and product-listing pages** can look “text heavy” because of product names, prices, filters, sort controls, and repeated cards. That can inflate naive word-count or structure proxies.
- **Do not score listing grids like expert guides** unless the page also contains substantive explanatory content: direct answers, selection criteria, comparisons, troubleshooting, definitions, or source-backed guidance.

### Suggested caps when automation cannot read passages in depth

When you only have crawl proxies (no manual passage review):

| Situation | Max citability (guidance) |
|---|---:|
| Category / listing page, mostly product grid, little editorial prose | **45** |
| Category page with limited guidance (thin FAQs or short explainer) | **60** |
| Category page with strong buying guide / comparison / FAQ | **85+** (then score normally against rubric) |
| Standard product PDP without editorial blocks | **70** (commerce pages can be useful but are often not broadly citable) |
| Flagship guide / definitive support article | **100** ceiling |

Always state when scores are **proxy-based** and require manual validation.

---

## Criterion 1 — Quality of answer blocks, 0–25

**Question:** Does the page contain clear, quotable passages an AI could reuse verbatim or near-verbatim as an answer?

| Score | What you see |
|---:|---|
| 21–25 | Multiple direct answer blocks with crisp claims, supporting detail, and clean boundaries. Key questions are answered upfront. |
| 15–20 | Some strong answer blocks, but key questions are not consistently answered in the first 1–2 sentences. |
| 8–14 | Useful information exists but is buried, promotional, fragmented, list-like, or missing a clear answer. |
| 0–7 | Few or no passages could be quoted without substantial rewriting. |

Look for:

- Definitional sentences: “X is…”
- Direct answers after headings
- “In short…” or “The key difference is…”
- Clear procedural summaries
- Comparison summaries
- Recommendation blocks
- Tables with meaningful headers
- Key takeaways
- FAQ answers with substance

Penalise:

- Long intros before the answer
- Marketing-heavy copy
- Generic claims such as “powerful”, “seamless”, “industry-leading”
- Answers spread across many disconnected sections
- CTA interruptions inside explanatory content

---

## Criterion 2 — Self-containment of passages, 0–20

**Question:** If you copy only the best paragraph or short section, does it still make sense without the surrounding page?

| Score | What you see |
|---:|---|
| 16–20 | Passages define entities, scope, audience, units, date, region, version, and conditions clearly. |
| 10–15 | Mostly understandable, but relies on some pronouns, implied context, prior sections, or page-specific jargon. |
| 4–9 | Frequent missing antecedents, unclear scope, layout dependence, or claims that require images/charts to interpret. |
| 0–3 | Core passages are incomprehensible, misleading, or unusable in isolation. |

Self-containment checks:

- Does the passage name the product, service, brand, concept, or entity?
- Does it specify who the statement applies to?
- Does it include dates where time matters?
- Does it include geography or jurisdiction where relevant?
- Does it define acronyms or specialist terms?
- Does it avoid “it”, “this”, “our solution”, “the above”, “as mentioned earlier”?
- Does it include units for numbers?
- Can it stand outside the page without becoming misleading?

Test: paste the passage into a blank document. If a reader asks “what is this referring to?”, the passage is not self-contained.

---

## Criterion 3 — Structural readability & extractability, 0–15

**Question:** Does the page structure help an AI system segment, retrieve, and extract the right passage?

| Score | What you see |
|---:|---|
| 13–15 | Clean H1 → H2 → H3 hierarchy, query-aligned headings, short paragraphs, accessible lists/tables, and minimal boilerplate interference. |
| 9–12 | Mostly clear structure with some generic headings, dense sections, weak table labels, or minor layout issues. |
| 4–8 | Poor hierarchy, very long paragraphs, important facts hidden in tabs/carousels, repetitive boilerplate, or weak section boundaries. |
| 0–3 | Structure actively prevents extraction or hides the main content. |

Look for:

- One clear H1
- Descriptive H2/H3 headings
- Question-shaped headings where appropriate
- Short paragraphs
- Meaningful bullets and ordered lists
- Tables with column headers, units, and row labels
- Summary boxes
- Key takeaways
- FAQs based on real questions
- Content present in HTML/rendered text

Penalise:

- Generic headings like “Overview”, “Solutions”, “Learn more”
- Long walls of text
- Hero copy with no substantive explanation
- Facts embedded only in images
- Important content hidden in UI widgets
- Repeated CTAs between every short section
- Duplicated template blocks
- Infinite scroll or content shells that do not serialize cleanly

### Platform nuance

- **Gemini / Google AI Overviews** and **Bing Copilot** favour snippet-shaped summaries and pages that map cleanly to indexed search results.
- **Perplexity** rewards scannable fact clusters, clear sourcing, tables, and concise answer passages.
- **ChatGPT Search** benefits from direct, well-scoped answer blocks and clear entity references.
- **Claude** can handle longer coherent sections if hierarchy is honest and context is explicit.

Use this nuance only to explain likely behaviour. Do not replace the core score with platform-specific scoring; use `platform-readiness.md` for that.

---

## Criterion 4 — Data points & verifiability, 0–25

**Question:** Are claims specific, current, checkable, and safe to cite?

| Score | What you see |
|---:|---|
| 21–25 | Multiple specific facts with sources, dates, units, named entities, methods, and appropriate caveats. |
| 15–20 | Some useful specifics, but important claims need better sourcing, dates, units, or scope. |
| 8–14 | Claims are mostly generic, unsourced, outdated, vague, or difficult to verify. |
| 0–7 | Little checkable substance; high risk of misleading or unsupported citation. |

Look for:

- Named entities
- Dates and “as of” statements
- Prices, ranges, counts, rates, or percentages
- Jurisdiction or geography
- Product model/version
- Dataset year or sample size
- Standards, laws, regulations, or official references
- Named studies, authorities, or source documents
- Methodology notes
- Expert quotes or reviewed-by statements
- Caveats and exclusions

Penalise:

- “Many”, “fast”, “affordable”, “best”, “leading” without proof
- Unsourced statistics
- Old facts with no update date
- No units
- No region or jurisdiction
- Medical, financial, legal, or safety claims without credible support
- Unsupported claims of superiority
- Contradictions within the page

---

## Criterion 5 — Uniqueness & citation worthiness, 0–15

**Question:** Would an AI need this site, or is the same answer available from Wikipedia, official docs, manufacturer specs, or a thousand similar pages?

| Score | What you see |
|---:|---|
| 13–15 | Original data, first-hand experience, expert synthesis, proprietary method, unique examples, or distinctive decision framework. |
| 9–12 | Useful synthesis with some original interpretation, examples, cases, or practical guidance. |
| 4–8 | Competent but largely duplicative of common web sources. |
| 0–3 | Thin, scraped, templated, affiliate-style, or catalogue content with little unique value. |

Look for:

- Original research
- First-party benchmarks
- Case studies
- Expert commentary
- Proprietary frameworks
- Worked examples
- Internal data
- Original images, diagrams, or tables with explanatory text
- First-hand testing
- Clear methodology
- Practical interpretation of complex rules or standards

Penalise:

- Reworded competitor content
- Manufacturer specs with no added interpretation
- Generic affiliate summaries
- Pages that exist mainly to capture search traffic
- Thin location or product templates
- AI-generated copy with no original contribution

---

# Citation safety review

After scoring the five core criteria, review whether the page is safe for an AI system to cite.

Do not reward a passage simply because it is confident, long, or fact-dense. Check whether it could be cited without misleading the user.

## Common citation risks

| Risk | Examples | Impact |
|---|---|---|
| Unsupported claims | “Best”, “most trusted”, “clinically proven” with no source | Reduce Criteria 4 and 5 |
| Outdated claims | Old prices, old regulations, old product versions, no update date | Reduce Criteria 2 and 4 |
| YMYL sensitivity | Medical, legal, financial, insurance, safety advice | Require stronger sourcing, credentials, caveats |
| Overclaiming | Guarantees, universal claims, exaggerated outcomes | Reduce Criteria 1 and 4 |
| Ambiguous scope | No country, date, audience, product version, or conditions | Reduce Criteria 2 and 4 |
| Contradiction | Page conflicts with itself or with cited official sources | Reduce Criterion 4 heavily |
| Sales bias | Passage reads like an ad rather than an answer | Reduce Criteria 1 and 5 |
| Missing caveats | Risky advice without limitations or exceptions | Reduce Criteria 2 and 4 |
| Hidden affiliation | Reviews or comparisons without disclosure | Reduce Criteria 4 and 5 |

## Citability caps

Apply these caps after scoring if relevant.

| Condition | Maximum score |
|---|---:|
| Main content is unavailable in extracted text | 50 |
| Page is mostly sales copy with no direct answers | 55 |
| No candidate answer block answers a target question | 60 |
| Major claims are unsourced in a YMYL topic | 60 |
| Page has no clear topic or query intent | 65 |
| Content is heavily duplicated from internal or external sources | 60 |
| Important time-sensitive claims appear outdated | 70 |
| Page contradicts itself or contains likely factual errors | 40 |
| Page is misleading, deceptive, or materially unsafe | 30 |

State when a cap is applied.

Example:

```markdown
Raw score: 74/100  
Cap applied: 60/100 because the page contains major unsourced financial advice.  
Final citability score: 60/100
```

## Structured element checks

When the page uses tables, FAQs, accordions, tabs, carousels, calculators, or comparison widgets, check:

- Is the content present in HTML text or rendered DOM text?
- Is important information available without user interaction?
- Are table headers meaningful?
- Are units included in column headings or cells?
- Can a table row be understood without the full page context?
- Are FAQ answers specific and substantive?
- Are accordions expanded or accessible in source/rendered DOM?
- Is widget output repeated in prose for systems that do not preserve UI state?
- Are images, charts, and diagrams explained in surrounding text?
- Are transcripts provided for videos or audio?
- Are captions and alt text meaningful?

Poorly labelled or inaccessible structured elements should not receive full structure credit.

## Competitive citation context

Where time allows, compare the page against 2–3 likely citation competitors for the same target question.

Potential competitors include:

- Wikipedia / Wikidata
- Official documentation
- Government or regulatory sources
- Academic or medical institutions
- Manufacturer pages
- Major publishers
- Review platforms
- Industry directories
- High-ranking competitors
- Reddit, Stack Overflow, YouTube, or community sources where relevant

Ask:

- Does this page answer the question more clearly?
- Does it provide fresher or more specific information?
- Does it provide original data or first-hand experience?
- Is it better sourced?
- Is it easier to quote?
- Is it more specific to the user’s likely context?
- Does it provide a better table, framework, or summary?

If competitors have stronger answer blocks, note the page’s relative citation disadvantage.

## Passage design targets

When recommending rewrites, aim for one or more primary answer blocks per major section.

| Target | Guidance |
|--------|----------|
| Length | Around 134–167 words for a substantial answer block; shorter for definitions and FAQs. |
| Opening | 1–2 sentences should directly answer the implied question. |
| Scope | Include audience, region, date, product version, conditions, or use case where needed. |
| Density | Prefer named entities, dates, counts, percentages, units, jurisdiction, version. |
| Boundary | Start and end on semantic breaks; avoid starting mid-list or mid-step. |
| Attribution | Add source, method, or “as of [date]” for non-obvious claims. |
| Caveat | Include limitations or exceptions when the topic is sensitive or conditional. |
| Format | Use prose for explanations, tables for comparisons, ordered lists for procedures. |

Design targets are not magic numbers. Do not pad content to hit a word count. Clarity and accuracy matter more.

## Model answer block format

When drafting a model answer block, use this structure:

- **Direct answer sentence** — Answer the implied query immediately.
- **Scope sentence** — Define audience, region, date, product version, or use case.
- **Evidence sentence** — Add source, data, method, or example.
- **Nuance sentence** — Include limitation, caveat, or exception.
- **Action sentence** — Explain what the reader should do next, if appropriate.

Avoid inventing facts.

If a fact is missing, use placeholders such as:

- `[insert current price as of Month YYYY]`
- `[cite source]`
- `[insert internal benchmark]`
- `[insert sample size or methodology]`
- `[name expert/reviewer]`
- `[confirm jurisdiction]`

## Rewrite patterns

| Problem | Fix |
|---------|-----|
| Buried lede | Move answer-first sentences to the top of the section, then evidence, then nuance. |
| Pronoun soup | Replace “it/they/this product” with named entities on first mention in each block. |
| Timeless vagueness | Add as-of date, region, version, dataset year, or eligibility scope. |
| Facts in UI-only widgets | Mirror critical facts in plain HTML text or visible tables. |
| No extractable definition | Add a 40–80 word “What is X?” block plus a deeper answer block. |
| Duplicate of SERP | Add original measurement, worked example, first-party data, or decision framework. |
| Thin comparison | Add criteria, table, who-should-choose-what guidance, and evidence. |
| Unsupported claim | Add source, methodology, date, caveat, or remove the claim. |
| Generic FAQ | Replace with real user questions and direct, specific answers. |
| Sales-led copy | Add explanatory passages before CTAs. |
| Missing caveats | Add limitations, exceptions, eligibility, or risk notes. |
| Image-only facts | Add surrounding prose, alt text, caption, or data table. |
| Long dense section | Split into question-led subsections with summary blocks. |

## Passage-level answerability checklist

When writing recommendations, check whether key pages include:

- Concise definitions
- Clear subheadings that mirror questions
- Direct answers immediately after headings
- Tables for comparisons
- Step-by-step ordered processes
- Pros and cons / trade-offs
- FAQs based on real questions
- Comparison summaries
- “Which should you choose?” guidance
- Key takeaways
- Statistics with named sources
- Dates for time-sensitive claims
- Author/reviewer or expert context where needed
- Examples or use cases
- Caveats and exclusions
- Plain-language summaries for complex topics

If most of these are missing on key templates, recommend adding 2–3 high-impact patterns first rather than rewriting everything.

## Per-page workflow

### 1. Fetch and isolate main content

Strip nav, footer, related widgets, cookie banners, and repeated CTAs where possible.

Use rendered main content if JavaScript affects the page.

Note if critical content is absent from extracted text.

### 2. Identify target query shapes

Use stakeholder target questions if available.

Otherwise infer likely questions from headings, page type, and topic.

### 3. Create candidate passage inventory

Identify 2–5 likely answer blocks.

Classify each by passage type.

### 4. Run query fit check

For each target question, identify whether a candidate passage answers it.

Score query fit 0–10 where useful.

### 5. Score the five core criteria

Use evidence snippets.

Explain major strengths and weaknesses.

### 6. Run citation safety review

Check unsupported claims, YMYL risk, outdated facts, contradictions, and overclaiming.

Apply caps where necessary.

### 7. Compare competitive context where possible

Check whether obvious competitor or authority sources have better answer blocks.

### 8. Produce rewrite recommendations

Give passage-level instructions.

Draft model answer blocks only when helpful.

Use placeholders for missing facts rather than inventing data.

### 9. Summarise platform angle

Give 2–3 concise bullets on how major AI surfaces may treat the page.

## Roll-up scoring

When scoring multiple pages, report both:

- Average citability score across audited pages.
- Priority-weighted citability score based on business/search importance.

Suggested default page weights:

| Page type | Default weight |
|-----------|---------------:|
| Flagship guide / definitive article | 3 |
| Core product or service page | 3 |
| Comparison / alternatives page | 2 |
| Pricing / plan page | 2 |
| High-demand support article | 2 |
| Homepage | 1 |
| Blog/news article | 1 |
| Standard support article | 1 |
| Low-priority archive/tag page | 0.5 |

**Formula:**

`Priority-weighted citability = sum(page score × page weight) / sum(page weights)`

Do not let many low-importance pages dilute issues on commercially or strategically critical pages.

## Template scoring

For template-based sites, score:

- 3–5 representative URLs per template where possible
- Best example
- Median example
- Worst example
- Whether weakness is template-level or page-specific

Template-level issues include:

- Generic headings repeated across all pages
- No page-specific answer block
- Thin location modifiers
- Missing self-contained descriptions
- Product specs without interpretation
- FAQs duplicated across every page
- Important data only in widgets
- No unique examples or proof

**Template roll-up format:**

| Template | URLs reviewed | Best | Median | Worst | Main template issue | Priority |
|----------|---------------|------|--------|-------|---------------------|----------|
| Product page | | | | | | High / Medium / Low |
| Location page | | | | | | High / Medium / Low |

(Add rows per template you audit.)

## Deliverable format

### A) Executive line

One sentence:

```markdown
Citability for [URL] is **[score]/100** — strongest on [criterion], weakest on [criterion]. The main limiter is [one-sentence issue].
```

If a cap is applied:

```markdown
Raw citability for [URL] is **[raw score]/100**, capped at **[final score]/100** because [reason].
```

### B) Candidate passage inventory

| ID | Section / heading | Query answered | Type | Approx. words | Assessment |
|----|-------------------|----------------|------|---------------|------------|
| P1 | | | Definition / comparison / process / data / decision / FAQ | | Strong / Partial / Weak |
| P2 | | | Definition / comparison / process / data / decision / FAQ | | Strong / Partial / Weak |

### C) Score table

| Criterion | Score | Evidence / notes |
|-----------|------:|------------------|
| 1. Answer blocks | /25 | |
| 2. Self-containment | /20 | |
| 3. Structure | /15 | |
| 4. Data/verifiability | /25 | |
| 5. Uniqueness | /15 | |
| Raw total | /100 | |
| Cap / adjustment | | |
| Final score | /100 | |

### D) Query fit

| Target question | Fit | Best passage | Gap |
|-----------------|-----|--------------|-----|
| | Strong / Partial / Weak / Missing | | Strong / Partial / Weak / Missing |

### E) Citation safety notes

- Unsupported claims:
- Outdated or time-sensitive claims:
- YMYL concerns:
- Scope/caveat issues:
- Contradictions:
- Cap applied: Yes / No — reason

### F) Rewrite recommendations

For each high-impact gap, provide:

`Recommendation [number]: [short title]`

- **Issue:**
- **Criterion affected:**
- **Current pattern:**
- **Rewrite instruction:**
- **Model answer block:** Optional, with placeholders where facts are missing.

### G) Platform angle

Provide 2–3 concise bullets.

- ChatGPT / ChatGPT Search:
- Perplexity:
- Gemini / Google AI Overviews:
- Claude:
- Bing Copilot:

Tie to `platform-readiness.md` if platform readiness has already been scored.

### Example rewrite recommendation format

```markdown
### Recommendation 1: Add an answer-first pricing block

- **Issue:** The page discusses pricing across several sections but never gives a direct answer to “How much does [service] cost?”
- **Criterion affected:** Answer blocks, self-containment, data/verifiability.
- **Current pattern:** Pricing is described as “flexible” and “tailored” without ranges or cost drivers.
- **Rewrite instruction:** Add a 120–160 word answer block under an H2 such as “How much does [service] cost?” Include starting price/range, what affects cost, what is included, date, and caveat.
- **Model answer block:**  
  As of [Month YYYY], [service] typically costs [insert range] for [audience/use case] in [region]. The final price depends on [cost driver 1], [cost driver 2], and [cost driver 3]. [Company] includes [inclusion 1] and [inclusion 2], while [exclusion] may be billed separately. This range is based on [source/methodology]. For organisations with [special condition], pricing may differ because [reason].
```

## Limitations

State these explicitly when relevant:

- This skill scores on-page extractability, not rank, personalisation, or live retrieval behaviour.
- A high citability score does not guarantee citation by ChatGPT, Perplexity, Gemini, Claude, or Copilot.
- Paywalled, geo-blocked, noindexed, or robotically restricted content may never enter an AI/search corpus.
- Cross-check access restrictions with `ai-crawler-report.md`.
- Cross-check raw HTML/rendering risks with `technical-audit.md`.
- Cross-check trust, author credentials, and people-first depth with `eeat.md`.
- Do not recommend adding unsourced statistics.
- Do not invent facts in model answer blocks.
- For YMYL topics, require stronger sourcing, author/reviewer evidence, and caveats.

## Related skills

| Skill | Role |
|-------|------|
| `platform-readiness.md` | Platform-specific citation patterns and readiness scoring |
| `eeat.md` | Trust, expertise, source transparency, people-first depth |
| `ai-search-success.md` | Google AI features, previews, structured data, page experience |
| `ai-crawler-report.md` | Robots.txt and AI crawler access |
| `technical-audit.md` | Indexability, rendering, raw HTML, performance, mobile, security |
| `json-ld.md` | Structured data and entity markup review |
| `llms-txt.md` | llms.txt presence, validity, and improvement |
| `create-report.md` | Audit pipeline/report generation, if available |