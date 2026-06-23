```markdown
# Skill: Prioritized action plan

Use this skill to turn findings from a GEO audit into a clear, realistic, client-ready action plan.

The action plan should help a resource-constrained business understand:

- What to do first
- What can wait
- Why each action matters
- Who is likely to own it
- How much effort it may take
- What impact it may have on the GEO Readiness score

The plan must be readable by a **non-technical marketing lead**. Technical terms and file names may be used, but only when they are paired with a plain-English explanation.

---

## Purpose

Turn recommendations from all audit skills into one prioritised plan with three time horizons:

1. **Quick wins:** 0–30 days
2. **Medium-term:** 30–90 days
3. **Strategic:** 90+ days

The plan should balance:

- Expected impact
- Effort
- Dependencies
- Business value
- Team capacity
- Realistic sequencing

The final output should feel like a practical roadmap, not a technical issue log.

---

## Inputs

Use findings from:

| Source skill / process | Typical findings |
|---|---|
| `ai-crawler-report.md` | AI/search crawler access, `robots.txt`, `noindex`, `noai` |
| `technical-audit.md` | Indexability, canonical signals, rendering, speed, mobile, sitemap |
| `llms-txt.md` | Missing or weak `llms.txt` |
| `json-ld.md` | Structured data gaps, `sameAs`, entity schema |
| `ai-citability.md` | Weak answer blocks, missing sources, poor structure |
| `eeat.md` | Authorship, sourcing, trust, editorial process |
| `brand-visibility.md` | Weak entity or third-party corroboration |
| `ai-search-success.md` | Google AI Search readiness gaps |
| `platform-readiness.md` | Platform-specific issues |
| `competitors.md` | Gaps versus competitors |
| `create-report.py` | Aggregated scores, category findings, report cards; renders three-column plan with max five items per horizon |

---

## Output

Produce a single action plan with:

1. Short intro
2. Three time-horizon columns or sections
3. No more than five actions per horizon
4. Plain-English action titles
5. Short “why this matters” notes
6. Suggested owner
7. Effort level
8. Estimated GEO score lift
9. Dependencies or caveats where needed

---

# Client readability standard

The action plan is for a marketing, content, or business lead.

It should not read like:

- A developer ticket dump
- A crawl log
- A list of raw audit artifacts
- A scoring formula
- A collection of unexplained acronyms

It should read like:

- A practical roadmap
- A set of assignable tasks
- A clear explanation of why each task matters

---

# Client-friendly rewrite layer

Before publishing the action plan, rewrite every recommendation into plain business language.

Technical file names such as `robots.txt`, `llms.txt`, `sitemap.xml`, JSON-LD, `noindex`, or `sameAs` may be included, but each term must be explained by its purpose.

## Rewrite rules

1. Lead with the action, not the audit artifact.
2. Explain the business or visibility outcome.
3. Include the technical term only when it helps the team assign or implement the task.
4. Do not mention internal audit paths, generated files, crawl outputs, or tool implementation details unless needed.
5. Use action verbs: publish, update, fix, add, remove, review, decide, monitor, roll out.
6. Keep each item short enough for a non-technical stakeholder to understand.
7. Avoid score formulas or internal weighting logic.
8. Avoid making policy-only or measurement-only tasks look more urgent than known blockers.
9. If an action requires a developer, still explain the marketing/business reason.
10. Make the owner obvious where possible.

---

## Plain-English technical wording rule

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
- Add `sameAs` to Organization schema.
- Weighted 25/25/20/15/15 technical score.

---

# Audience and tone

Write for a non-technical marketing, content, or business lead.

## Good style

Use:

- Publish
- Add
- Fix
- Improve
- Create
- Update
- Roll out
- Review
- Measure
- Decide
- Monitor
- Ask your developer to

Avoid unexplained jargon such as:

- Hydration
- Canonicalisation
- X-Robots-Tag
- Schema graph
- SSR
- Crawl budget
- Entity reconciliation
- TTFB
- CWV
- Render queue

If you need a technical term, explain it.

Good:

```markdown
Make key page content visible in the initial HTML so AI crawlers can read it without relying on JavaScript.
```

Less useful:

```markdown
Implement SSR for client-rendered templates.
```

---

# Technical term translation table

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
| crawlability | Whether search engines and AI tools can access a page |
| indexability | Whether search engines are allowed to list a page |
| structured data | Extra page information that helps machines understand the page |
| entity signals | Clues that connect the website to the real-world brand |
| Open Graph / `og:image` | Preview information used when a page is shared or surfaced |

---

# Rewrite examples

| Raw audit wording | Client-friendly action |
|---|---|
| Merged `robots.txt` suggestion is in this audit folder | Ask your developer to update the site’s crawler access rules in `robots.txt` so AI search tools can read the public pages you want cited |
| No live `llms.txt` at origin | Publish a short AI guide file (`llms.txt`) that points AI tools to your most important pages |
| Improve discovery: live llms.txt, reachable Sitemap: in robots.txt, and key URLs in sitemap | Publish a short AI guide file (`llms.txt`) and make sure your sitemap lists the important pages you want search engines and AI tools to find |
| Consider blocking Bytespider (Tier 3) | Decide whether to allow ByteDance’s AI training crawler, Bytespider; this is a policy choice, not a core visibility fix |
| Core Web Vitals and TTFB are not measured | Run a page speed check on key templates to confirm whether loading speed is affecting users or crawlers |
| Add `sameAs` to Organization schema | Add verified official profile links (`sameAs`) to your structured brand information so AI systems can connect the site to the right brand |
| Add JSON-LD | Add structured page information using JSON-LD so search engines and AI systems can better understand the site |
| JS-only content detected | Make the main page text visible in the initial page load so AI crawlers can read it reliably |
| Weak answer blocks | Add clear answer summaries to priority pages so AI tools can quote them accurately |
| Missing citations | Add sources and dates to important claims so the content is easier to trust |
| Missing `og:image` | Add clear preview images so important pages look better when shared or surfaced |
| Bing WMT missing | Set up Bing Webmaster Tools so Microsoft search and Copilot can find updates faster |

---

# Capacity rule: maximum five actions per horizon

The report should show:

| Horizon | Timeframe | Maximum items |
|---|---:|---:|
| Quick wins | 0–30 days | 5 |
| Medium-term | 30–90 days | 5 |
| Strategic | 90+ days | 5 |

Maximum total: **15 actions**.

## Why

Resource-constrained teams need a focused plan. If everything is urgent, nothing is urgent.

Even if 12 items could theoretically be completed in 30 days, only the top five should appear under Quick wins. Remaining items should move into Medium-term or Strategic depending on effort and importance.

## Overflow rule

After prioritising globally:

1. Fill Quick wins with the five highest-priority actions that are genuinely achievable in 0–30 days.
2. Move extra quick but lower-priority actions into Medium-term.
3. Move extra medium-term actions into Strategic.
4. Keep the most important items visible.
5. Do not overload the plan with low-impact hygiene tasks.

---

# The three horizons

## 1. Quick wins: 0–30 days

### Definition

Actions that are:

- High impact
- Low to moderate effort
- Usually one file, one template, or a small set of key pages
- Achievable without a major redesign or long approval cycle
- Useful for unblocking crawlability, visibility, previews, or entity clarity

### Typical quick wins

| Area | Examples |
|---|---|
| Crawler access | Update `robots.txt` so key AI/search crawlers can access public content |
| Indexability | Remove accidental `noindex` settings from important pages |
| Snippet eligibility | Remove unintended `nosnippet` or overly strict preview limits |
| `llms.txt` | Publish a short AI guide file |
| Structured data pilot | Add Organization/WebSite JSON-LD to the homepage |
| Entity links | Add verified official profiles to `sameAs` |
| Metadata | Improve titles/descriptions on top pages |
| Open Graph | Add useful preview images to key templates |
| Content blocks | Add answer-first summaries to 3–5 priority pages |
| Trust basics | Add or improve About, Contact, author, or update-date visibility |
| Sitemap | Make sure the sitemap lists priority pages and is referenced from `robots.txt` |

### Not quick wins

Do not put these in Quick wins unless the team has already prepared the work:

- Full site redesign
- Sitewide schema rollout
- New content hub
- Major performance rebuild
- Community/PR campaigns
- Full migration to server-side rendering
- Hundreds of page rewrites
- Large editorial governance programme
- Full internationalisation strategy
- Long-term reputation or review programme

---

## 2. Medium-term: 30–90 days

### Definition

Actions that require:

- Template changes
- Several pages or sections
- CMS updates
- Content operations
- QA
- Coordination between marketing, content, development, and legal/compliance
- Measured rollout

### Typical medium-term actions

| Area | Examples |
|---|---|
| Structured data at scale | Roll out Product, Article, Breadcrumb, or LocalBusiness schema |
| Content citability | Rewrite priority templates with direct answers and evidence |
| Technical templates | Improve initial HTML content across key page types |
| Metadata systems | Build CMS fields for titles, descriptions, preview images |
| Internal linking | Improve paths to source-of-truth pages |
| Sitemap/freshness | Clean sitemap, add accurate `lastmod`, improve update signals |
| E-E-A-T | Add author bios, reviewer fields, sourcing standards |
| Brand consistency | Update official profiles and align naming/descriptions |
| Platform readiness | Set up Bing Webmaster Tools, IndexNow, Merchant Center, Google Business Profile fixes |
| Measurement | Run speed, Core Web Vitals, and AI traffic tracking checks |

---

## 3. Strategic: 90+ days

### Definition

Longer-term programmes that build authority, depth, trust, and durable AI visibility.

These usually need:

- Roadmap planning
- Ongoing ownership
- Editorial calendar
- PR or partnerships
- Product expertise
- Original research
- Community engagement
- Measurement and iteration

### Typical strategic actions

| Area | Examples |
|---|---|
| Original research | Publish surveys, benchmarks, industry reports |
| Content clusters | Build pillar pages and supporting guides by topic/category |
| Brand authority | Earn press, directory, partner, or expert mentions |
| Community visibility | Build ethical presence on Reddit/forums/YouTube |
| Video/multimodal | Create educational video series with transcripts |
| E-E-A-T programme | Ongoing editorial review and governance |
| Technical architecture | Major server-rendering, static-generation, or performance programme |
| Internationalisation | Full hreflang/localisation strategy |
| Review/reputation | Build review acquisition and response programme |

---

# Priority ordering guidance

## Put first

Prioritise actions that:

1. Fix crawler/indexing blockers
2. Remove accidental `noindex` or snippet restrictions
3. Make key content visible to AI/search crawlers
4. Improve source-of-truth pages
5. Improve structured data/entity clarity
6. Improve citability of commercial or high-traffic pages
7. Close a clear competitor gap
8. Help multiple platforms at once

## Put later

Deprioritise actions that:

- Affect only low-traffic pages
- Are nice-to-have metadata tweaks
- Require heavy engineering but have uncertain impact
- Are long-term brand campaigns
- Depend on unresolved strategy or legal decisions
- Improve a platform the client does not care about

---

# Measurement and policy task rule

Measurement-only tasks and policy-only tasks should not outrank known visibility blockers.

## Measurement-only examples

- Core Web Vitals not measured
- TTFB not measured
- AI referral traffic not tracked
- Bing index coverage not manually verified

These are useful, but they usually belong in **Medium-term** unless there is evidence of a real performance, tracking, or indexing problem.

Client-friendly wording:

```markdown
Run a page speed and usability check on key templates to confirm whether loading speed is affecting users or crawlers.
```

## Policy-only examples

- Decide whether to block Bytespider
- Decide whether to allow CCBot
- Decide whether to allow training-only crawlers

These should usually appear as policy notes or lower-priority actions unless the client has a stated AI training policy.

Client-friendly wording:

```markdown
Decide whether to allow ByteDance’s AI training crawler, Bytespider. This is a data-use policy choice, not a core AI search visibility fix.
```

---

# Prioritisation method

Use this sequence.

## Step 1: Collect all recommendations

Pull actions from all audit sections.

Examples:

- Add `llms.txt`
- Fix blocked AI crawlers
- Add Organization schema
- Improve answer blocks
- Add author bios
- Build content cluster
- Set up IndexNow
- Improve YouTube presence

## Step 2: Deduplicate

Merge similar recommendations.

Example:

Combine:

- Add Organization schema
- Add `sameAs` links
- Improve entity markup

Into:

```markdown
Add structured brand information using JSON-LD, including verified official profile links (`sameAs`).
```

## Step 3: Convert findings into actions

Findings describe a problem. Actions describe what to do.

| Finding | Better action |
|---|---|
| `llms.txt` missing | Publish a short AI guide file (`llms.txt`) that links to the site’s most important pages |
| Weak JSON-LD | Add structured brand and page information using JSON-LD |
| Content too vague | Add direct answer summaries to priority service pages |
| Reddit weak | Monitor relevant Reddit communities and create a responsible engagement plan |
| JS-only content | Make key page text available in the initial HTML |
| No sitemap reference | Make sure the sitemap is live and referenced in `robots.txt` |

## Step 4: Score impact and effort

Use simple labels.

### Impact

| Impact | Meaning |
|---|---|
| **High** | Removes a blocker or improves a major category score |
| **Medium** | Improves important signals across several pages |
| **Low** | Hygiene improvement or marginal gain |

### Effort

| Effort | Meaning |
|---|---|
| **Low** | One person or one small change |
| **Medium** | Several pages/templates or cross-team coordination |
| **High** | Programme-level, engineering-heavy, or ongoing |

## Step 5: Assign horizon

Use the horizon definitions.

## Step 6: Enforce capacity

Maximum five per horizon.

## Step 7: Order by business value

Within each horizon, order by:

1. Removes a blocker
2. Improves high-weight GEO category
3. Affects important pages
4. Helps multiple platforms
5. Is easy to implement
6. Supports revenue or conversion pages

---

# Classification rules

## Quick wins

Choose Quick wins when the action is:

- One configuration change
- One small file
- One or two templates
- A pilot on priority pages
- A metadata/schema/content fix on top pages
- A crawler/indexability blocker fix

Signals:

- `robots.txt`
- `noindex`
- `nosnippet`
- `llms.txt`
- homepage schema
- `sameAs`
- top 5 pages
- priority URLs
- metadata
- Open Graph
- starter
- pilot

## Medium-term

Choose Medium-term when the action is:

- Sitewide or template-wide
- Across many URLs
- Requires CMS fields
- Requires content rewriting
- Requires QA or stakeholder review
- Builds repeatable processes

Signals:

- all product pages
- all articles
- template
- CMS
- roll out
- content refresh
- author bios across
- schema at scale
- sitemap cleanup
- internal linking
- speed validation
- IndexNow setup

## Strategic

Choose Strategic when the action is:

- Ongoing
- Authority-building
- Research-led
- Community-led
- PR-led
- Requires quarterly planning
- Involves large technical architecture change

Signals:

- content cluster
- original research
- Reddit/community
- YouTube programme
- thought leadership
- digital PR
- partnerships
- editorial governance
- server-rendering migration
- internationalisation programme

---

# Split actions that span horizons

If an action is too broad, split it.

## Example 1

Too broad:

```markdown
Improve structured data across the site.
```

Better:

```markdown
Quick win: Add structured brand information using JSON-LD to the homepage.

Medium-term: Roll out Product, Article, and Breadcrumb structured data across priority templates.
```

## Example 2

Too broad:

```markdown
Improve content for AI citations.
```

Better:

```markdown
Quick win: Add answer-first summaries to the five highest-priority pages.

Medium-term: Rewrite service and guide templates with question-led headings, sourced facts, and comparison tables.

Strategic: Build original research and content clusters around the highest-value topics.
```

## Example 3

Too broad:

```markdown
Improve Reddit visibility.
```

Better:

```markdown
Medium-term: Monitor relevant Reddit discussions and identify recurring questions or concerns.

Strategic: Build an ethical community engagement programme with clear response guidelines.
```

---

# GEO score lift estimates

Each action should include:

```markdown
Est. +X–Y pts overall
```

This is an illustrative estimate of how the action could affect the overall GEO Readiness score if implemented well.

It is **not** a promise.

Do not sum all ranges linearly. Actions overlap.

## Recommended score weights

Use the score weights from the audit framework.

| Category | Weight |
|---|---:|
| AI Visibility | 40 |
| Technical Setup | 30 |
| Content Quality & Structure | 30 |

If the report uses a different weighting, follow that report.

## Lift estimate bands

Use conservative ranges.

| Action type | Typical overall lift |
|---|---:|
| Fix major crawl/index blocker | +6–12 |
| Unblock key AI/search crawlers | +4–9 |
| Remove accidental `noindex` / snippet suppression | +4–10 |
| Make JS-only main content available in HTML | +5–12 |
| Publish useful `llms.txt` | +1–3 |
| Add homepage Organization/WebSite schema | +1–4 |
| Roll out structured data across key templates | +3–8 |
| Add verified `sameAs` / entity links | +1–4 |
| Improve answer blocks on priority pages | +3–8 |
| Rewrite key templates for citability | +5–10 |
| Add authorship, sourcing, and trust signals | +3–7 |
| Improve brand visibility / official profiles | +2–6 |
| Build content clusters | +5–12 over time |
| Publish original research/data | +4–10 over time |
| Improve community/third-party corroboration | +3–8 over time |
| Major performance/mobile improvements | +2–6 |
| Major technical architecture / rendering programme | +6–12 |

Adjust down if the site is already strong in that area.

Adjust up only when the current issue is a severe blocker.

---

# Suggested owner labels

Use simple owner labels.

| Owner | Typical actions |
|---|---|
| Marketing | Brand profiles, messaging, priority pages, campaigns |
| Content | Page rewrites, FAQs, guides, sourcing, editorial calendar |
| SEO | Metadata, indexing, schema requirements, sitemaps, Search Console |
| Developer | `robots.txt`, rendering, structured data implementation, performance |
| Analytics | Tracking, dashboards, AI referral monitoring |
| PR / Comms | Press, partnerships, thought leadership |
| Legal / Compliance | Policies, claims, YMYL review, AI-use policy |
| Product | Product facts, documentation, pricing, feature accuracy |

Use multiple owners only when necessary.

---

# Action item format

Each action should be easy to scan.

## Full format

```markdown
### {Action title}

**What to do:** {Plain-English action}  
**Why it matters:** {Business/GEO reason}  
**Owner:** {Team}  
**Effort:** Low / Medium / High  
**Impact:** High / Medium / Low  
**Est. lift:** +X–Y pts overall  
**Dependencies:** {Optional}
```

## Compact format for report cards

```markdown
- **Publish an AI guide file (`llms.txt`).** Give AI tools a short guide to the site’s most important pages.  
  Owner: SEO/Developer · Effort: Low · Est. +1–3 pts overall
```

---

# Deliverable template

```markdown
## Prioritized action plan

Grouped by realistic effort horizon for a resource-constrained team. Score lift estimates are illustrative and should be validated with a follow-up crawl.

### Quick wins: 0–30 days

1. **{Action title}.** {Plain-English explanation.}  
   Owner: {owner} · Effort: Low / Medium · Impact: High / Medium · Est. +{x}–{y} pts overall

2. **{Action title}.** {Plain-English explanation.}  
   Owner: {owner} · Effort: Low / Medium · Impact: High / Medium · Est. +{x}–{y} pts overall

### Medium-term: 30–90 days

1. **{Action title}.** {Plain-English explanation.}  
   Owner: {owner} · Effort: Medium · Impact: High / Medium · Est. +{x}–{y} pts overall

### Strategic: 90+ days

1. **{Action title}.** {Plain-English explanation.}  
   Owner: {owner} · Effort: High / Ongoing · Impact: High / Medium · Est. +{x}–{y} pts overall
```

---

# HTML report output shape

For `report.html`, render three columns:

| Column | Time horizon | Max items |
|---|---:|---:|
| Quick wins | 0–30 days | 5 |
| Medium-term | 30–90 days | 5 |
| Strategic | 90+ days | 5 |

Optional intro:

```markdown
Grouped by typical effort horizon for a resource-constrained team. Adjust dates to your release process.
```

Each item should include:

- Action
- Plain-English benefit
- Estimated GEO score lift

Optional if space allows:

- Owner
- Effort

---

# Common action translations

## Technical

| Audit finding | Client-ready action |
|---|---|
| GPTBot blocked | Allow important AI/search crawlers to access public content through `robots.txt` |
| `noindex` found | Remove accidental “do not list this page” settings from important pages |
| JS-only content | Make the main page text visible in the initial page load |
| Missing sitemap | Publish a clean sitemap so crawlers can find important pages |
| Slow TTFB | Improve server response speed so pages load faster for users and crawlers |
| Missing HTTPS redirect | Make all versions of the site redirect to the secure version |
| Canonical conflict | Fix duplicate URL signals so search engines know which page to trust |

## Structured data

| Audit finding | Client-ready action |
|---|---|
| No Organization schema | Add structured brand information to the homepage |
| Missing `sameAs` | Connect the site to verified official profiles using `sameAs` |
| Product schema incomplete | Add accurate product details that match the visible page |
| Article schema missing author/date | Add structured author and update-date information on article templates |

## Content

| Audit finding | Client-ready action |
|---|---|
| Weak answer blocks | Add short direct answers near the top of priority pages |
| No sourced stats | Add sources and dates for factual claims |
| Thin templates | Rewrite thin pages with unique, useful information |
| No author bios | Add author or reviewer details where users expect expertise |
| No original information | Add examples, case studies, first-party data, or expert commentary |

## Brand

| Audit finding | Client-ready action |
|---|---|
| LinkedIn incomplete | Update the LinkedIn company profile to match the website |
| YouTube inactive | Refresh YouTube descriptions and link back to key pages |
| Weak third-party corroboration | Build presence on trusted industry directories and partner pages |
| Reddit sentiment unknown | Monitor relevant community discussions before engaging |

---

# Quality checks before publishing

Before finalising the plan, check:

- Are there no more than five items per horizon?
- Does each item start with an action verb?
- Would a non-technical reader understand it?
- Are technical terms explained when used?
- Is the timeframe realistic?
- Are technical dependencies stated?
- Are lift estimates conservative?
- Are long-term programmes not disguised as quick wins?
- Are duplicate recommendations merged?
- Are the most serious blockers first?
- Are measurement-only tasks demoted below known blockers?
- Are training-crawler policy decisions demoted below search visibility fixes?
- Is there a balance between technical, content, and brand work?
- Are actions tied to audit evidence?

---

# Example action plan

```markdown
## Prioritized action plan

Grouped by realistic effort horizon for a resource-constrained team. Score lift estimates are illustrative and should be validated with a follow-up crawl.

### Quick wins: 0–30 days

1. **Update crawler access rules in `robots.txt`.** Ask your developer to make sure search engines and AI tools can read the public pages you want cited.  
   Owner: Developer/SEO · Effort: Low · Impact: High · Est. +4–9 pts overall

2. **Publish an AI guide file (`llms.txt`).** Give AI tools a short guide to the site’s most important product, support, company, and policy pages.  
   Owner: SEO/Content · Effort: Low · Impact: Medium · Est. +1–3 pts overall

3. **Add structured brand information using JSON-LD.** Help search engines and AI systems identify the business, website, logo, and official profiles.  
   Owner: Developer/SEO · Effort: Low · Impact: Medium · Est. +1–4 pts overall

4. **Add direct answer summaries to the five highest-priority pages.** Start each important section with a clear answer before adding detail.  
   Owner: Content · Effort: Medium · Impact: High · Est. +3–8 pts overall

5. **Fix discovery through the sitemap.** Make sure the sitemap lists the key pages you want search engines and AI tools to find, and reference it from `robots.txt`.  
   Owner: SEO/Developer · Effort: Low · Impact: Medium · Est. +2–5 pts overall

### Medium-term: 30–90 days

1. **Roll out structured data across priority templates.** Add accurate Article, Product, Breadcrumb, or LocalBusiness information where relevant.  
   Owner: Developer/SEO · Effort: Medium · Impact: High · Est. +3–8 pts overall

2. **Rewrite priority templates for AI citation.** Use question-led headings, short answer blocks, comparison tables, sourced facts, and update dates.  
   Owner: Content/SEO · Effort: Medium · Impact: High · Est. +5–10 pts overall

3. **Improve authorship and trust signals.** Add author bios, reviewer details, source links, and update dates where expertise matters.  
   Owner: Content/Compliance · Effort: Medium · Impact: Medium · Est. +3–7 pts overall

4. **Run a page speed and usability check.** Use Core Web Vitals and server response speed checks to confirm whether loading issues affect users or crawlers.  
   Owner: Developer/Analytics · Effort: Medium · Impact: Medium · Est. +0–3 pts overall

5. **Align official brand profiles.** Update LinkedIn, YouTube, business profiles, and other official pages so names, descriptions, and website links match.  
   Owner: Marketing · Effort: Medium · Impact: Medium · Est. +2–6 pts overall

### Strategic: 90+ days

1. **Build content clusters around high-value topics.** Create pillar pages and supporting guides that answer the full set of buyer and research questions.  
   Owner: Content/SEO · Effort: High · Impact: High · Est. +5–12 pts overall

2. **Publish original research or first-party data.** Create reports, benchmarks, case studies, or expert analysis that give AI systems a reason to cite you.  
   Owner: Content/Product/PR · Effort: High · Impact: High · Est. +4–10 pts overall

3. **Develop ethical community visibility.** Monitor relevant Reddit, forum, and industry discussions, then participate only where the brand can be genuinely helpful.  
   Owner: Marketing/Comms · Effort: Ongoing · Impact: Medium · Est. +3–8 pts overall

4. **Create a video and transcript programme.** Publish educational videos with captions, transcripts, and links to source pages.  
   Owner: Marketing/Content · Effort: High · Impact: Medium · Est. +2–6 pts overall

5. **Establish ongoing content governance.** Create a review calendar, correction process, AI-use policy, and ownership for high-risk pages.  
   Owner: Content/Compliance · Effort: Ongoing · Impact: Medium · Est. +3–7 pts overall
```

---

# Limitations

- Lift estimates are illustrative, not guarantees.
- Actions overlap, so score ranges should not be added together.
- Some fixes require follow-up crawling or Search Console validation.
- Timelines assume a small but functioning marketing/development workflow.
- Regulated industries may need longer review cycles.
- A plan should be edited to match the client’s team, CMS, legal process, and release cadence.
```