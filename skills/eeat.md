# Skill: Helpful, reliable, people-first content & E-E-A-T

Use this skill to evaluate whether a site’s content is **primarily created for people** and whether it demonstrates appropriate **Experience, Expertise, Authoritativeness, and Trustworthiness**.

This skill is part of the **Content Quality & Structure** category in a GEO audit. It is especially important because AI answer systems are more likely to extract, summarise, recommend, or cite content that is useful, specific, trustworthy, well-sourced, and clearly attributable.

**Primary reference:** [Creating helpful, reliable, people-first content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content), Google Search Central.

**Primary output:** a structured **Pass / Partial / Fail / Not applicable** assessment with evidence tied to URLs/templates, **Who / How / Why** notes, YMYL risk notes, and prioritised actions.

---

## Purpose

Use this skill to answer:

- Is the content genuinely useful for a defined audience?
- Does it satisfy the user’s task or question?
- Does it show first-hand experience, expertise, or original insight?
- Is it clear who created or reviewed the content?
- Are important claims accurate, current, and sourced?
- Is the publisher or brand trustworthy for this topic?
- Is the content substantially better than generic or copied alternatives?
- Are there search-engine-first or scaled-content warning signs?
- Is the level of E-E-A-T appropriate for the topic’s risk level?

---

## What this skill does and does not measure

This skill measures:

- People-first usefulness
- Content quality and completeness
- Original information gain
- First-hand experience
- Expertise and authorship
- Trust signals and source transparency
- Authoritativeness and reputation evidence
- Content governance
- YMYL sensitivity
- Search-engine-first warning signs

This skill does **not** directly measure:

- Rankings
- Backlinks
- AI citation frequency
- Technical crawlability
- AI crawler access
- Structured data correctness in full
- Core Web Vitals in full

Pair with:

| Skill | Role |
|---|---|
| `ai-citability.md` | Passage-level extractability and citation worthiness |
| `json-ld.md` | Structured data, author/entity markup, `sameAs` |
| `brand-visbility.md` | Off-site entity corroboration and reputation |
| `technical-audit.md` | Indexability, rendering, technical trust |
| `ai-search-success.md` | Google AI Search readiness |
| `platform-readiness.md` | Platform-specific AI visibility scoring |

---

# Inputs

## Minimum inputs

| Input | Use |
|---|---|
| Representative URLs | Page/template review |
| Page HTML or rendered text | Main content, bylines, links, sources |
| Homepage | Site purpose and audience |
| About page | Publisher identity and credibility |
| Contact/support pages | Accountability and trust |
| JSON-LD extracts | Author, Organization, Article, dates |
| Key page types/templates | Pattern-level assessment |

## Recommended inputs

| Input | Use |
|---|---|
| Author pages | Expertise and credentials |
| Reviewer pages | Specialist review evidence |
| Editorial policy | Governance and accountability |
| Corrections policy | Trust process |
| Disclosure policy | Affiliate/ads/sponsorship transparency |
| AI content policy | Automation transparency |
| Search Console data | Impacted pages, queries, intent gaps |
| Analytics data | Engagement/satisfaction proxies |
| Independent reviewer feedback | External quality check |
| Competitor pages | Relative helpfulness and originality |
| Brand visibility findings | Authoritativeness and corroboration |

---

# Output

Produce:

1. Executive summary
2. Optional score
3. Theme checklist
4. YMYL/topic-risk classification
5. Who / How / Why assessment
6. Search-engine-first warning signs
7. Page/template evidence
8. Prioritised actions

---

# Status definitions

| Status | Meaning |
|---|---|
| **Pass** | Strong evidence across sampled key pages/templates |
| **Partial** | Some evidence, but inconsistent or incomplete |
| **Fail** | Major gap, absent signal, or clear quality issue |
| **Not applicable** | Not relevant to the site or page type |
| **Manual check** | Evidence unavailable from crawl; human review required |

---

# Optional scoring model

Use this when a numeric E-E-A-T / people-first content score is needed.

| Theme | Weight |
|---|---:|
| 1. People-first purpose and audience fit | 15 |
| 2. Content quality and completeness | 20 |
| 3. Original information gain | 15 |
| 4. Experience and expertise | 15 |
| 5. Trust, sourcing, and factual accuracy | 20 |
| 6. Authoritativeness and reputation | 10 |
| 7. Content governance and transparency | 5 |
| **Total** | **100** |

## Scoring conversion

| Status | Points awarded |
|---|---:|
| Pass | 100% of theme weight |
| Partial | 50% of theme weight |
| Fail | 0% of theme weight |
| Not applicable | Remove from denominator and rescale |
| Manual check | Do not score unless evidence is available |

Formula:

```text
E-E-A-T score =
sum(applicable weighted theme points) / sum(applicable weights) × 100
```

---

# Critical caps

Apply these caps after scoring.

| Condition | Maximum score |
|---|---:|
| Materially false or misleading claims | 35 |
| Deceptive authorship, fake experts, fake reviews, or undisclosed conflicts | 40 |
| Important pages have little or no useful main content | 50 |
| YMYL content lacks expert review or credible sourcing | 55 |
| Large-scale low-value automated content is present | 55 |
| Content is mostly copied, rewritten, or commodity filler | 60 |
| Search-engine-first pattern dominates the site | 65 |
| No clear publisher, contact, or accountability signals | 65 |
| Time-sensitive content is materially outdated | 70 |

State caps clearly.

Example:

```markdown
Raw score: 74/100  
Cap applied: 55/100 because legal advice pages lack named expert review and credible sourcing.  
Final E-E-A-T score: 55/100
```

---

# Automated proxy E-E-A-T scoring in `report.html`

The HTML report may include E-E-A-T-style 0–100 scorecards. Treat these as **directional proxy scores**, not a full editorial assessment.

Proxy scores may be derived from crawlable artifacts such as:

| E-E-A-T area | Possible proxy signals |
|---|---|
| Experience | Content depth, media, examples, Open Graph, JSON-LD |
| Expertise | Content quality, author/schema signals, generated `llms.txt` presence |
| Authoritativeness | Brand visibility, `sameAs`, third-party profile links |
| Trust | HTTPS, technical setup, AI crawler access, indexability |

To turn the proxy into a true assessment, review:

- Actual page content
- Author and reviewer evidence
- Sources and citations
- Editorial process
- Reputation/corroboration
- Factual accuracy
- YMYL risk

---

# Review workflow

## Step 1: Select representative pages

Review a mix of high-value pages and templates.

| Page type | Why review it |
|---|---|
| Homepage | Purpose, brand clarity, trust |
| About page | Publisher identity and credibility |
| Contact/support | Accountability |
| Core product/service page | Commercial claims and buyer help |
| Pricing page | Transparency |
| Flagship guide | Informational depth and expertise |
| Comparison page | Fairness, evidence, usefulness |
| Review/recommendation page | Methodology and conflicts |
| Blog/article page | Authoring and content quality |
| Support/help page | Practical usefulness |
| Location page | Local relevance and uniqueness |
| YMYL page | Specialist expertise and sourcing |

## Step 2: Classify topic risk

| Risk level | Description | E-E-A-T expectation |
|---|---|---|
| Low | Entertainment, general interest, low-stakes content | Basic quality and clarity |
| Medium | Purchase decisions, B2B, technical, employment, education | Clear expertise, useful evidence |
| High / YMYL | Health, finance, legal, safety, insurance, civic, welfare | Strong sourcing, expert review, accountability |

## Step 3: Assess themes

Score the seven themes.

## Step 4: Ask Who / How / Why

Evaluate authorship, production method, and purpose.

## Step 5: Identify warning signs

Look for search-engine-first, scaled, copied, or unsupported content.

## Step 6: Prioritise actions

Prioritise by:

1. User risk
2. Business importance
3. Traffic/visibility importance
4. Template-level leverage
5. Ease of implementation

---

# Theme 1: People-first purpose and audience fit

## Intent

Content should primarily help a defined audience. It should not exist mainly to capture search traffic.

## Checks

| Check | What to look for |
|---|---|
| Clear audience | The intended reader/user is obvious |
| Clear purpose | Site and page have a coherent purpose |
| Intent match | Page matches the promise of title/H1/snippet |
| Direct usefulness | Helps user complete a task, learn, compare, decide, or act |
| Satisfying outcome | Reader would not immediately need to search again |
| Topic fit | Topic fits the brand’s real expertise or offering |
| Appropriate scope | Page does not overpromise or drift into unrelated topics |
| Direct audience value | Existing audience would find it useful even without search |

## Pass indicators

- Page answers the main user need clearly.
- Content is aligned with the site’s real purpose.
- Reader can make progress after reading.
- Page is not just a doorway or keyword-targeting asset.

## Fail indicators

- Page appears built only around keyword volume.
- Site covers unrelated topics without expertise.
- Content promises answers it does not provide.
- Reader must search elsewhere for basic information.
- Page is a near-duplicate template with minimal unique value.

---

# Theme 2: Content quality and completeness

## Intent

High-quality content should be accurate, complete enough for the task, clear, well produced, and better than generic summaries.

## Checks

| Check | What to look for |
|---|---|
| Completeness | Covers the topic sufficiently for the user’s goal |
| Accuracy | No obvious factual errors |
| Clarity | Clear structure, headings, examples, summaries |
| Specificity | Concrete details rather than vague claims |
| Helpful title/H1 | Descriptive, not clickbait or exaggerated |
| Production quality | Proofread, coherent, well formatted |
| Comparative value | Adds value compared with competing pages |
| No filler | Avoids padding, repetition, generic text |
| Main content quality | Important information is not buried or missing |
| Internal consistency | Page does not contradict itself |

## Google-aligned quality questions

Ask whether the content:

1. Provides original information, reporting, research, or analysis.
2. Provides a substantial, complete, or comprehensive description.
3. Provides insightful analysis or information beyond the obvious.
4. Adds substantial value when drawing on other sources.
5. Has a descriptive, helpful main heading or title.
6. Avoids exaggeration or shock value.
7. Is worth bookmarking, sharing, or recommending.
8. Meets a high editorial bar.
9. Provides substantial value compared with other search results.
10. Avoids spelling, style, or production issues.
11. Appears carefully produced.
12. Avoids mass-produced patterns where individual pages lack care.

---

# Theme 3: Original information gain

## Intent

For GEO and AI citation readiness, content is stronger when it contributes something not already available everywhere else.

## Checks

| Check | Examples |
|---|---|
| Original research | Surveys, benchmarks, experiments, datasets |
| First-party data | Usage data, internal metrics, anonymised customer insights |
| First-hand experience | Product testing, site visits, implementation notes |
| Expert commentary | Named experts with relevant credentials |
| Case studies | Real examples, outcomes, constraints |
| Proprietary frameworks | Decision models, calculators, evaluation tools |
| Original media | Diagrams, screenshots, photos, videos created by the brand |
| Practical interpretation | Explains what facts mean for a specific audience |
| Methodology | Shows how data or conclusions were produced |

## Status guidance

| Status | Meaning |
|---|---|
| Pass | Clear original contribution or strong expert synthesis |
| Partial | Some examples or POV, but mostly common information |
| Fail | Mostly restates competitors or public sources |

---

# Theme 4: Experience and expertise

## Intent

Users and AI systems need evidence that the content was created or reviewed by someone who understands the topic.

Experience and expertise can come from:

- Professional credentials
- First-hand use
- Lived experience
- Operational experience
- Research expertise
- Editorial expertise
- Practitioner knowledge

## Checks

| Check | What to look for |
|---|---|
| Author identified | Bylines where readers expect them |
| Author bio | Relevant expertise, role, credentials, topic focus |
| Reviewer identified | Specialist review where appropriate |
| First-hand evidence | Photos, screenshots, tests, examples, observations |
| Credentials | Qualifications, certifications, professional role |
| Topic fit | Author/site has reason to know the subject |
| Methodology | Tests/reviews explain how conclusions were reached |
| Appropriate caveats | Limits, uncertainty, conditions, exclusions |
| Expert language | Precise without being inaccessible |

## YMYL expectations

For YMYL content, require stronger evidence:

- Qualified author or reviewer
- Credible sources
- Current update date
- Clear disclaimers where appropriate
- No overconfident advice outside expertise
- Editorial review process

---

# Theme 5: Trust, sourcing, and factual accuracy

## Intent

Trust is the most important part of E-E-A-T. Experience, expertise, and authoritativeness support trust.

## Checks

| Check | What to look for |
|---|---|
| Source transparency | Important claims cite credible sources |
| Dates | Time-sensitive claims show dates |
| Methodology | Data/tests explain how results were obtained |
| Fact accuracy | No easily verified errors |
| Accountability | Contact, company details, support routes |
| Conflict disclosure | Affiliate, sponsorship, ads, partnerships disclosed |
| Review integrity | Reviews/ratings are real and visible |
| Security | HTTPS and safe user flows |
| Policy pages | Privacy, terms, returns, editorial/corrections as relevant |
| Corrections | Clear mechanism for fixing errors |
| No deception | No fake authors, fake reviews, hidden motives |

## Source quality guidance

Prefer:

- Official sources
- Government/regulatory sources
- Academic or medical institutions
- Standards bodies
- Primary documentation
- Named expert commentary
- First-party methodology

Be cautious with:

- Unsourced statistics
- Anonymous claims
- Circular citations
- Outdated reports
- Low-quality aggregators
- Affiliate-only sources

---

# Theme 6: Authoritativeness and reputation

## Intent

Authoritativeness asks whether the creator, site, or brand is recognised as a good source for the topic.

## Checks

| Check | What to look for |
|---|---|
| Brand reputation | Known in industry/category |
| Third-party mentions | Press, directories, reviews, citations |
| Expert recognition | Awards, qualifications, speaking, publications |
| Community reputation | Positive discussions or recommendations |
| Institutional credibility | Certifications, partnerships, memberships |
| External corroboration | Other trusted sources confirm key facts |
| Topical authority | Site has depth around the subject, not one-off pages |

Use `brand-visbility.md` for deeper off-site corroboration checks.

## Caution

Authority is topic-specific. A site can be authoritative in one area and weak in another.

---

# Theme 7: Content governance and transparency

## Intent

Trustworthy sites maintain content responsibly.

## Checks

| Check | What to look for |
|---|---|
| Editorial ownership | Someone owns content quality |
| Review process | Pages reviewed before publication |
| Specialist review | Used for YMYL or technical topics |
| Update process | Old content reviewed and refreshed |
| Date policy | Dates reflect real updates |
| Correction policy | Errors can be reported and corrected |
| Disclosure policy | Ads, affiliates, sponsorships disclosed |
| AI-use policy | AI-assisted content is reviewed and disclosed where appropriate |
| Pruning process | Outdated/thin pages improved, merged, or removed |

---

# Search-engine-first warning signs

If any answer is **yes**, treat it as a warning. If several are yes, treat it as a strategic content risk.

| # | Warning sign |
|---:|---|
| 1 | Content is primarily made to attract search visits rather than help users |
| 2 | The site publishes on many unrelated topics hoping some rank |
| 3 | Extensive automation produces large volumes of content with little review |
| 4 | Pages mainly summarise others without adding value |
| 5 | Topics are chosen only because they are trending |
| 6 | Readers likely need to search again for better information |
| 7 | Content is written to a target word count rather than user need |
| 8 | Site entered a niche without real expertise mainly for traffic |
| 9 | Pages promise answers that do not exist or are unknowable |
| 10 | Dates are changed to appear fresh without substantial updates |
| 11 | Content is added or removed mainly to look fresh to search engines |
| 12 | Pages are programmatic templates with minimal unique value |
| 13 | Content overuses keywords unnaturally |
| 14 | Near-duplicate location/product pages provide little unique value |
| 15 | AI-generated content is published at scale without expert review |

---

# Who / How / Why assessment

## Who created the content?

Check:

| Question | Evidence |
|---|---|
| Is it clear who authored the content? | Byline, author name, organisation |
| Are bylines present where expected? | Articles, guides, reviews, YMYL pages |
| Do bylines link to author bios? | Author page URL |
| Is the author qualified for the topic? | Credentials, role, experience |
| Is reviewer information shown where needed? | Medical/legal/financial/technical review |
| Is the publisher identity clear? | About, Contact, Organization schema |

## How was the content produced?

Check:

| Question | Evidence |
|---|---|
| Is methodology explained for reviews/tests/research? | Testing process, sample, tools, dates |
| Are sources cited? | Links, footnotes, references |
| Was AI or automation used substantially? | Disclosure or policy |
| Is human review evident? | Editor/reviewer, policy, QA |
| Are images/data original? | Photos, screenshots, charts, methodology |
| Are updates meaningful? | Change notes, updated sections |

## Why was the content created?

Classify:

| Alignment | Test |
|---|---|
| People-first | Primary purpose is to help a real audience |
| Mixed | Useful content exists, but SEO/traffic motives shape structure heavily |
| Search-first | Primary purpose appears to be attracting search visits or manipulating rankings |

---

# Page/template assessment table

Use this for detailed audits.

```markdown
| URL / template | Risk level | People-first fit | Quality | Info gain | Experience/expertise | Trust/sourcing | Authority | Governance | Who/How/Why notes | Priority |
|---|---|---|---|---|---|---|---|---|---|---|
| | Low / Medium / YMYL | Pass / Partial / Fail | Pass / Partial / Fail | Pass / Partial / Fail | Pass / Partial / Fail | Pass / Partial / Fail | Pass / Partial / Fail | Pass / Partial / Fail | | High / Medium / Low |
```

---

# Deliverable template

```markdown
## People-first content & E-E-A-T — {domain}

**Scope:** {number} URLs/templates reviewed  
**Inputs:** {HTML, author pages, JSON-LD, Search Console, analytics, manual review}  
**Score:** {score}/100, if scored  

### Summary

- **Primary audience and site purpose:** {summary}
- **Biggest strengths:** {summary}
- **Biggest risks:** {summary}
- **YMYL exposure:** Low / Medium / High
- **Overall assessment:** Pass / Partial / Fail

### Theme checklist

| Theme | Status | Evidence | Actions |
|---|---|---|---|
| People-first purpose and audience fit | Pass / Partial / Fail / N/A | | |
| Content quality and completeness | Pass / Partial / Fail / N/A | | |
| Original information gain | Pass / Partial / Fail / N/A | | |
| Experience and expertise | Pass / Partial / Fail / N/A | | |
| Trust, sourcing, and factual accuracy | Pass / Partial / Fail / N/A | | |
| Authoritativeness and reputation | Pass / Partial / Fail / N/A | | |
| Content governance and transparency | Pass / Partial / Fail / N/A | | |

### Who / How / Why

| URL / template | Who | How | Why | Notes |
|---|---|---|---|---|
| | | | People-first / Mixed / Search-first | |

### Search-engine-first warnings

| Warning | Found? | Evidence | Action |
|---|---|---|---|
| Scaled low-value content | Yes / No / Partial | | |
| Thin duplicate templates | Yes / No / Partial | | |
| Copied or rewritten content | Yes / No / Partial | | |
| Unhelpful date changes | Yes / No / Partial | | |
| Keyword-first content | Yes / No / Partial | | |
| AI-generated content without review | Yes / No / Partial | | |

### Priority actions

1. {Highest-priority action}
2. {Second action}
3. {Third action}
```

---

# Common recommendations

## Add or improve authorship

```markdown
Add bylines and author bios to guides, reviews, and YMYL pages. Each bio should explain the author’s role, topic expertise, credentials, and links to relevant work.
```

## Add expert review

```markdown
For YMYL or technical pages, add named expert review with reviewer credentials, review date, and scope of review.
```

## Improve source transparency

```markdown
Add citations for non-obvious factual claims, statistics, regulations, and health/financial/legal guidance. Include dates and source names.
```

## Add original information gain

```markdown
Add first-party data, case studies, test results, expert commentary, or a proprietary decision framework so the page contributes something beyond generic summaries.
```

## Reduce search-first content

```markdown
Consolidate or rewrite thin pages created mainly for keyword coverage. Prioritise fewer, stronger pages that fully satisfy user needs.
```

## Improve governance

```markdown
Create an editorial policy, correction process, disclosure policy, and review cadence for important content.
```

## Fix misleading freshness

```markdown
Only update visible dates when substantive changes are made. Add change notes or meaningful updates for time-sensitive pages.
```

---

# Integration with `crawl-site.py` / `create-report.py`

Crawl artifacts support, but do not replace, editorial review.

| Artifact | Maps to |
|---|---|
| `jsonld/*.json`, `json-ld.txt` | Organization, Person, Article, author, dates |
| On-page HTML | Bylines, About links, source links, headings, main content |
| Extracted text | Content depth, duplication patterns, answer quality |
| `llms.txt` | Stated purpose, curated source-of-truth pages |
| `brand_visibility` | Off-site authority and corroboration |
| Technical summary | HTTPS, indexability, rendering, access |
| `report.html` proxy E-E-A-T | Directional score for follow-up manual review (`create-report.py` blends seven theme proxies per §Optional scoring model) |

---

# References

- [Creating helpful, reliable, people-first content](https://developers.google.com/search/docs/fundamentals/creating-helpful-content)
- [Search Quality Rater Guidelines](https://guidelines.raterhub.com/searchqualityevaluatorguidelines.pdf)
- [Using generative AI content on your website](https://developers.google.com/search/docs/fundamentals/using-generative-ai-content)
- [Write high quality product reviews](https://developers.google.com/search/docs/specialty/ecommerce/write-high-quality-reviews)
- [Understanding page experience in Google Search results](https://developers.google.com/search/docs/appearance/page-experience)

---

# Limitations

- E-E-A-T is not a single ranking factor.
- Quality rater guidelines do not directly control rankings.
- Automated proxy scores are directional only.
- Some signals require manual review and external evidence.
- Different topics require different levels of expertise and trust.
- YMYL content requires stricter standards.
- A page can be technically optimised and still fail people-first quality.