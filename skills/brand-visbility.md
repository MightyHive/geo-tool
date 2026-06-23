# Skill: Brand visibility

Use this skill to assess how visible, verifiable, and consistently represented a brand is across major third-party surfaces.

This skill supports GEO because generative answer systems often rely on **entity clarity** and **third-party corroboration** when deciding whether a brand is real, notable, trustworthy, and relevant.

The automated crawl covers:

- Wikipedia
- YouTube
- Reddit
- LinkedIn

The full skill also includes manual checks for:

- Entity clarity
- Wikidata
- Reviews
- News/press
- Industry directories
- Partner pages
- Product/local profiles
- Other trusted corroborating sources

**Primary output:** a structured **Brand visibility report** with presence, URLs, confidence, entity clarity, quantitative notes where observable, risks, and prioritised actions.

---

## Purpose

Use this skill to answer:

- Can AI systems confidently identify the brand as a real entity?
- Can they connect the website to the official brand?
- Are official profiles discoverable and consistent?
- Is the brand corroborated by independent sources?
- Are there trusted third-party references that support claims about the brand?
- Are there confusing duplicates, rebrands, similarly named entities, or fake profiles?
- Is the brand discussed by users or communities on platforms such as Reddit and YouTube?

---

## Inputs

Minimum inputs:

| Input | Use |
|---|---|
| Canonical brand name | Primary search and matching term |
| Brand spelling variants | Avoid false negatives |
| Legal entity name, if different | Entity disambiguation |
| Primary domain | Verify official links |
| Country/market | Disambiguate similar brands |
| Industry/category | Confirm correct entity |
| Founder / notable person names, optional | Wikipedia/Wikidata disambiguation |
| Known official social/profile URLs, optional | Verification and `sameAs` recommendations |

Recommended optional inputs:

| Input | Use |
|---|---|
| Rebrand history | Avoid missing old mentions |
| Former names | Search and entity matching |
| Parent/subsidiary names | Ownership/entity clarity |
| Product names | Find product-led mentions |
| Local branch/location names | Local visibility checks |
| Competitor list | Relative visibility comparison |
| Existing Organization JSON-LD | Check `sameAs` accuracy |
| Google Business Profile / Merchant Center URLs | Local/commerce corroboration |

---

## Output

Produce:

1. Executive summary
2. Four-platform automated visibility table
3. Entity clarity assessment
4. Third-party corroboration assessment
5. Risks and ambiguity notes
6. Recommended `sameAs` updates
7. Prioritised actions

Use evidence-backed language. Include URLs, dates, and method notes.

---

# Status definitions

Use consistent labels.

| Status | Meaning |
|---|---|
| **Confirmed** | Strong evidence that the result is the official or correct brand entity |
| **Likely** | Good match, but one verification signal is missing |
| **Possible** | Similar name or partial evidence; manual verification needed |
| **Not found** | No credible match found through the method used |
| **Ambiguous** | Multiple plausible entities or conflicting evidence |
| **Blocked / unavailable** | Platform prevented verification |
| **Not applicable** | Platform is not relevant to the brand/category |

---

# Confidence scoring

For each platform, assign a confidence level.

| Confidence | Meaning |
|---|---|
| **High** | URL/profile clearly belongs to the brand and links to or from the official domain |
| **Medium** | Name, logo, content, or context strongly matches, but official-domain linkage is missing |
| **Low** | Name match only, weak evidence, or possible duplicate/copycat |
| **Unknown** | Verification blocked or unavailable |

Recommended evidence signals:

- Official website link on the profile
- Link from the audited domain to the profile
- Matching logo/brand assets
- Matching company description
- Matching location/HQ
- Matching legal name
- Matching product/service descriptions
- Verified badge or official status
- Consistent posting history
- External references confirming the relationship

---

# Optional scoring model

Use this if the audit needs a numeric **Brand visibility score**.

| Component | Weight |
|---|---:|
| Entity clarity on owned site | 25 |
| Official profile presence and verification | 25 |
| Independent third-party corroboration | 25 |
| Audience/community visibility | 15 |
| Consistency and risk management | 10 |
| **Total** | **100** |

## Component details

### 1. Entity clarity on owned site — 25 points

| Check | Points |
|---|---:|
| Brand name, legal name, and domain relationship clear | 5 |
| Products/services/category clear | 5 |
| Location, market, or service area clear | 4 |
| About/contact/company details credible | 4 |
| Organization/LocalBusiness JSON-LD present and accurate | 4 |
| Official profiles listed or linked | 3 |

### 2. Official profile presence and verification — 25 points

Score verified official presence across major platforms.

| Surface | Points |
|---|---:|
| Wikipedia / Wikidata where applicable | 5 |
| YouTube | 5 |
| LinkedIn | 5 |
| Reddit official presence, if relevant | 3 |
| Other official profiles relevant to category | 4 |
| Profiles cross-link to domain or are linked from site | 3 |

If Wikipedia is not realistically applicable for a small or local brand, reallocate those 5 points to industry directories, Google Business Profile, review platforms, or local citations.

### 3. Independent third-party corroboration — 25 points

| Source type | Points |
|---|---:|
| News/press coverage or authoritative mentions | 5 |
| Review platforms, e.g. Trustpilot, G2, Capterra, Google reviews | 5 |
| Industry directories / trade bodies | 5 |
| Partner/customer/integration pages | 4 |
| Government, academic, regulatory, or standards references where relevant | 3 |
| Consistent facts across sources | 3 |

### 4. Audience/community visibility — 15 points

| Check | Points |
|---|---:|
| YouTube activity or third-party videos | 4 |
| Reddit discussion volume and quality | 4 |
| LinkedIn activity and follower/employee signals | 3 |
| Other community/social platforms relevant to market | 2 |
| Positive or balanced sentiment / useful discussion | 2 |

### 5. Consistency and risk management — 10 points

| Check | Points |
|---|---:|
| No major duplicate/copycat/confusing profiles | 2 |
| No major naming inconsistencies | 2 |
| No contradictory company facts | 2 |
| Rebrand/ownership relationships are clear | 2 |
| Reputation risks identified and actioned | 2 |

---

# Automated pipeline notes

`crawl-site.py` / `create-report.py` can call:

```text
brand_visibility_scan.scan_brand_platforms
```

This writes `brand_visibility` into `audit_summary.json`.

The HTML report renders a four-platform table.

## Automated scan behaviour

| Platform | Approach |
|---|---|
| Wikipedia | Uses `en.wikipedia.org` MediaWiki search API across brand/host variants; applies title-matching logic |
| YouTube | Tests likely `@handle` URLs and may inspect search-result HTML for `@...` links |
| Reddit | Uses Reddit search JSON where available and checks likely subreddit slugs |
| LinkedIn | Tests likely `/company/{slug}` URLs with retry/backoff for rate limits |

## Important limitations

- Automated URLs are **likely matches**, not always confirmed matches.
- Bot challenges, rate limits, login walls, and dynamic UI can produce false negatives.
- The automated table does **not** read on-site JSON-LD `sameAs`.
- Manual verification is required for ambiguous or high-stakes conclusions.
- Use `--brand "Canonical Name"` if the hostname-derived brand is wrong.
- Use `--no-brand-scan` to skip live probes.

---

# Naming and scope

Document the brand variant used for each check.

| Input | Use |
|---|---|
| Canonical brand name | Primary match string |
| Legal name | Company/entity verification |
| Domain | Official website confirmation |
| Compact variant | e.g. `brandname` |
| Hyphenated variant | e.g. `brand-name` |
| Former name | Rebrand discovery |
| Parent/subsidiary | Ownership clarity |
| Founder/person names | Wikipedia/Wikidata disambiguation |
| Product names | Product-led mentions |

## Disambiguation checks

Flag ambiguity when:

- Several companies share the same name
- Brand name is a generic word
- There is a former brand or acquired company
- A product has stronger visibility than the company
- Social handles belong to unrelated entities
- Wikipedia article is about a different entity
- The domain uses a different brand name than official profiles

---

# Platform checks

## 1. Wikipedia

Wikipedia is a strong entity-corroboration signal, but absence of a page is common and does not automatically mean poor brand visibility.

**Important:** web search alone is not reliable for Wikipedia presence. Always use the MediaWiki API first.

### Method

1. Search using the MediaWiki API:

```text
https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={query}&format=json
```

Use a descriptive User-Agent and modest request rates.

2. Try multiple queries:
   - Canonical brand name
   - Legal name
   - Compact hostname variant
   - Spaced brand variant
   - Founder/person name + brand
   - Brand + industry/category

3. Review top results manually if automatic matching fails.

4. Verify any candidate URL:

```text
https://en.wikipedia.org/wiki/{Title_with_underscores}
```

5. Confirm:
   - Real article, not only search results
   - Not just a disambiguation page
   - Correct entity
   - Official website matches audited domain, if present
   - Infobox details are consistent
   - References are credible
   - Article is not a stub with weak sourcing

### Optional Wikidata check

Use Wikidata when entity-level confidence matters.

Check:

- Q-id
- Description
- Official website
- Legal name
- Parent organisation
- Founding date
- Headquarters
- Industry
- Social profile identifiers
- SameAs-style external IDs

Wikidata is not included in the four-platform automated table unless separately implemented.

### Report notes

| Check | Record |
|---|---|
| Article exists | Yes / No / Ambiguous |
| Title and URL | Exact page |
| Quality | Stub / moderate / strong |
| References | Weak / adequate / strong |
| Infobox | Present and accurate? |
| Official website | Matches audited domain? |
| Risks | Disambiguation, outdated, deletion tags, weak sourcing |
| Wikidata | Q-id and key statements, if checked |

---

## 2. YouTube

YouTube supports brand/entity visibility, video search, product education, trust, and multimodal AI understanding.

### Method

Search:

```text
[brand name] site:youtube.com
"[brand name]" site:youtube.com
[brand name] official YouTube
```

Check likely channels:

```text
https://www.youtube.com/@{handle}
https://www.youtube.com/c/{slug}
https://www.youtube.com/user/{slug}
```

Try variants:

- `brandname`
- `brand-name`
- `brandofficial`
- `brandOfficial`
- Legal name
- Product names

### Verification

Confirm the channel is official using:

- Link to audited domain in channel links/about section
- Link from audited domain to YouTube
- Matching logo and brand assets
- Matching products/services
- Consistent description
- Verified badge, where visible
- Active posting history

### Report notes

| Check | Record |
|---|---|
| Official channel | URL or none found |
| Confidence | High / Medium / Low |
| Subscribers | If visible |
| Video count | If visible |
| Latest upload | Date |
| Upload cadence | Active / occasional / inactive |
| Content type | Product demos, education, webinars, support, ads |
| Third-party mentions | Notable videos or approximate volume |
| Risks | Copycats, inactive official channel, outdated branding |

### Action examples

- Link official channel from the website footer or About page.
- Add website link on YouTube channel.
- Improve channel description with canonical brand/entity facts.
- Publish educational videos that answer high-intent questions.
- Add transcripts and `VideoObject` markup on embedded videos.

---

## 3. Reddit

Reddit can reveal community awareness, sentiment, recommendations, complaints, and real user language. It is a corroboration and reputation surface, not necessarily an official brand channel.

### Method

Search:

```text
[brand name] site:reddit.com
"[brand name]" site:reddit.com
[brand name] reddit
```

Check:

```text
https://www.reddit.com/r/{slug}/
https://www.reddit.com/user/{handle}/
```

Review:

- Dedicated subreddit
- Official user account
- Mentions in industry/community subreddits
- Recent discussions
- High-upvote threads
- Complaint/support patterns
- Recommendation frequency

### Sentiment guidance

Classify sentiment as:

- Positive
- Negative
- Mixed
- Neutral
- Not enough data

State sample size.

Example:

```markdown
Sentiment: Mixed, based on 12 recent threads from r/{x}, r/{y}, and r/{z}.
```

This is heuristic, not formal NLP.

### Report notes

| Check | Record |
|---|---|
| Official subreddit | URL or none |
| Official user | URL or none |
| Dominant subreddits | Top 3–5 |
| Discussion volume | Low / moderate / high |
| Sentiment | Positive / negative / mixed / neutral |
| Common themes | Praise, complaints, support, pricing, alternatives |
| Recommendation frequency | Often recommended / occasional / rare |
| Risks | Unanswered complaints, misinformation, fake communities |

### Action examples

- Monitor priority subreddits.
- Create a response policy before participating.
- Fix recurring product/support issues surfaced by Reddit.
- Do not astroturf or manipulate discussions.
- Consider an official account only if the brand can support it responsibly.

---

## 4. LinkedIn

LinkedIn is a strong corroboration surface for organisations, B2B brands, employers, founders, and leadership.

### Method

Search:

```text
[brand name] site:linkedin.com/company
"[brand name]" "LinkedIn"
```

Check likely company URLs:

```text
https://www.linkedin.com/company/{slug}/
```

Try variants:

- `brand-name`
- `brandname`
- `brand-name-ltd`
- `brand-name-ltd-`
- `brand-name-limited`
- Legal entity name
- Parent/subsidiary name

Expect 429 rate limits or login walls. Use browser verification when needed.

### Verification

Confirm:

- Associated website matches audited domain
- Logo/name match
- Industry/category match
- Company size plausible
- Locations match
- Employee profiles point to the company
- Posting history is consistent
- Description matches site/about page

### Report notes

| Check | Record |
|---|---|
| Company page | URL or none found |
| Confidence | High / Medium / Low |
| Followers | If visible |
| Employee count | If visible |
| Website | Matches audited domain? |
| Activity | Latest post and cadence |
| Engagement | Qualitative |
| Risks | Duplicate pages, outdated name, wrong domain, inactive page |

### Action examples

- Claim/merge duplicate pages.
- Add audited domain to company profile.
- Align company description with site and schema.
- Post regularly about expertise, products, case studies, and people.
- Encourage accurate employee association where appropriate.

---

# Entity clarity assessment

Entity clarity checks whether AI systems can confidently connect the website to the real-world brand.

Review:

- Homepage
- About page
- Contact page
- Footer
- Terms/privacy/company details
- Press/media page
- Organization JSON-LD
- LocalBusiness JSON-LD, if relevant
- Social profile links
- Google Business Profile, if relevant

## Checks

| Check | What to verify |
|---|---|
| Brand name | Public-facing name is clear and consistent |
| Legal name | Legal entity name shown where appropriate |
| Domain relationship | Site clearly belongs to the brand |
| Category | Industry/product/service category is explicit |
| Products/services | What the brand sells or does is clear |
| Locations | HQ, service areas, or local footprint are clear |
| Leadership/founders | Present where relevant |
| Contact details | Real-world contact/support details exist |
| About page | Provides credible entity facts |
| Official profiles | Social/knowledge profiles are linked |
| Structured data | Organization/LocalBusiness schema is accurate |
| Rebrand/ownership | Parent/subsidiary/former names explained |

## Entity clarity scoring

| Score | Meaning |
|---:|---|
| 21–25 | Entity is clear, consistent, and well supported across site and profiles |
| 15–20 | Mostly clear with minor gaps or missing structured data |
| 8–14 | Basic entity information exists but is incomplete or inconsistent |
| 0–7 | Brand identity is unclear, generic, contradictory, or hard to verify |

---

# Third-party corroboration

Automated checks cover only four platforms. For stronger GEO analysis, add corroboration from trusted independent sources.

## Sources to consider

| Source | What to check |
|---|---|
| Wikidata | Q-id, official website, entity facts |
| Crunchbase | Funding, founders, HQ, industry |
| Companies House / business registers | Legal name, status, incorporation |
| Google Business Profile | Local facts, reviews, categories |
| Merchant Center / shopping surfaces | Product consistency |
| G2 / Capterra / Gartner / TrustRadius | B2B software reviews |
| Trustpilot / Feefo / Reviews.io | Consumer/service reviews |
| App Store / Google Play | App presence and reviews |
| GitHub / package registries | Developer/product credibility |
| Industry directories | Trade bodies, memberships, certifications |
| Partner pages | Integrations, reseller lists, customer stories |
| Press/news | Independent coverage |
| Academic/government sources | High-trust citations where relevant |
| Podcasts/webinars/events | Expert visibility |
| Awards/certifications | Third-party validation |

## Corroboration scoring

| Score | Meaning |
|---:|---|
| 21–25 | Multiple independent, trusted sources confirm consistent brand facts |
| 15–20 | Several credible sources exist, with minor gaps |
| 8–14 | Some corroboration exists, but it is thin, niche, or inconsistent |
| 0–7 | Little independent corroboration beyond owned profiles |

---

# `sameAs` and structured data recommendations

The automated scan does not read on-site JSON-LD `sameAs`, but this skill should recommend updates based on verified official URLs.

## Recommended rule

Only add URLs to `sameAs` when they are verified official profiles.

Good `sameAs` candidates:

- Wikipedia article
- Wikidata entity URL
- LinkedIn company page
- YouTube official channel
- Official X/Twitter, Instagram, Facebook, TikTok, GitHub profiles
- Crunchbase profile, where relevant
- Google Knowledge Graph-relevant profiles

Avoid:

- Unofficial Reddit threads
- Review pages unless clearly official/accepted in the entity strategy
- Duplicate or unclaimed profiles
- Search result URLs
- Profile URLs with weak confidence
- Country/location pages unless they represent the same entity

## Example Organization JSON-LD

```json
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "Brand Name",
  "legalName": "Brand Name Ltd",
  "url": "https://www.example.com/",
  "logo": "https://www.example.com/logo.png",
  "sameAs": [
    "https://www.linkedin.com/company/example/",
    "https://www.youtube.com/@example",
    "https://www.wikidata.org/wiki/Q123456"
  ]
}
```

---

# Risks and ambiguity

Always flag material risks.

| Risk | Why it matters |
|---|---|
| Similar-name entities | AI systems may confuse brands |
| Duplicate social profiles | Dilutes entity confidence |
| Inconsistent legal name | Weakens corroboration |
| Old rebrand references | Causes conflicting facts |
| Mismatched domains | Raises trust issues |
| Weak Wikipedia sourcing | Article may be unstable or less trusted |
| Negative Reddit sentiment | May affect perceived reputation |
| Unclaimed profiles | Can be outdated or inaccurate |
| Fake/copycat channels | May confuse users and models |
| Conflicting addresses/phone numbers | Local/entity trust issue |
| Review manipulation concerns | Trust risk |
| Thin off-site footprint | Harder for AI systems to corroborate claims |

---

# Workflow

1. Confirm canonical brand name, legal name, domain, country, and category.
2. List variants:
   - Spaced
   - Compact
   - Hyphenated
   - Former names
   - Product names
   - Legal name
3. Run the automated four-platform scan, if available.
4. Verify automated matches manually.
5. Use MediaWiki API for Wikipedia.
6. Optionally check Wikidata.
7. Search YouTube, Reddit, and LinkedIn manually where automation is uncertain.
8. Review owned-site entity clarity.
9. Check existing JSON-LD `sameAs`, if available.
10. Add third-party corroboration sources relevant to the brand.
11. Identify ambiguity, duplicates, and inconsistency.
12. Produce report and prioritised actions.

---

# Brand visibility report template

```markdown
## Brand visibility — {brand} ({domain})

**Audit date:** {date}  
**Market / country:** {market}  
**Methods:** Automated four-platform probes, Wikipedia API, URL verification, web search, manual review where needed.  
**Confidence note:** {limitations, login walls, rate limits, ambiguous matches}

### Executive summary

{2–4 sentences. State strongest visibility signal, biggest entity gap, corroboration quality, and top priority action.}

### Score

**Brand visibility score:** {score}/100

| Component | Score | Notes |
|---|---:|---|
| Entity clarity on owned site | /25 | |
| Official profile presence and verification | /25 | |
| Independent third-party corroboration | /25 | |
| Audience/community visibility | /15 | |
| Consistency and risk management | /10 | |
| **Total** | **/100** | |

### Platform visibility

| Platform | Status | URL | Confidence | Quantitative notes | Issues / actions |
|---|---|---|---|---|---|
| Wikipedia | Confirmed / Likely / Possible / Not found / Ambiguous | | High / Medium / Low / Unknown | Article quality, refs, Wikidata | |
| YouTube | Confirmed / Likely / Possible / Not found / Ambiguous | | High / Medium / Low / Unknown | Subscribers, videos, latest upload | |
| Reddit | Confirmed / Likely / Possible / Not found / Ambiguous / N/A | | High / Medium / Low / Unknown | Volume, dominant subs, sentiment | |
| LinkedIn | Confirmed / Likely / Possible / Not found / Ambiguous | | High / Medium / Low / Unknown | Followers, employees, activity | |

### Entity clarity

| Check | Status | Evidence | Action |
|---|---|---|---|
| Brand/legal name clarity | | | |
| Product/service category | | | |
| Location/service area | | | |
| About/contact trust cues | | | |
| Official profiles linked | | | |
| Organization/LocalBusiness schema | | | |
| Rebrand/ownership clarity | | | |

### Third-party corroboration

| Source | Presence | Evidence | Notes |
|---|---|---|---|
| Wikidata | | | |
| Reviews | | | |
| Industry directories | | | |
| Press/news | | | |
| Partner/customer pages | | | |
| Government/academic/regulatory | | | |
| Other | | | |

### Existing or recommended `sameAs`

| URL | Include in `sameAs`? | Confidence | Notes |
|---|---|---|---|
| | Yes / No / Verify | | |

### Risks

- {Risk}
- {Risk}
- {Risk}

### Recommended actions

1. {Highest-priority action}
2. {Second action}
3. {Third action}
```

---

# Summary report version

Use this shorter version for executive reports.

```markdown
## Brand visibility

{Brand} has {strong/moderate/weak} off-site entity visibility. The strongest corroboration is {source/platform}. The main gap is {gap}. Priority action: {action}.

| Surface | Finding | Action |
|---|---|---|
| Wikipedia/Wikidata | | |
| YouTube | | |
| Reddit | | |
| LinkedIn | | |
| Other corroboration | | |
| Entity consistency | | |
```

---

# Ethics, limits, and accuracy

- Respect robots, platform terms, APIs, and rate limits.
- Prefer official APIs where available.
- Use MediaWiki API for Wikipedia.
- Do not scrape aggressively.
- Treat automated matches as provisional until verified.
- Sentiment is approximate unless supported by a formal tool or dataset.
- Do not claim a profile is official without evidence.
- Do not recommend creating or editing Wikipedia pages unless the brand meets notability and conflict-of-interest guidelines.
- Do not encourage fake reviews, astroturfing, or manipulative community activity.
- Record uncertainty clearly.

---

# Integration with repo artifacts

| Artifact | Use |
|---|---|
| `audit_summary.json` → `brand_visibility` | Automated four-platform scan results |
| `json-ld.txt` / `jsonld/*.json` | Existing `sameAs`, Organization, LocalBusiness, Person checks |
| `report.html` | Renders automated visibility summary |
| `create-report.py` → `score_brand_visibility` | Builds the **0–100 Brand visibility** sub-score from the five weighted components (automation limits corroboration/audience/consistency to crawl-derived proxies; manual skill steps still apply for news, reviews, GBP, etc.) |
| `robots_fetched.txt` | Not central, but useful if platform/profile links are blocked from crawlers |
| `llms.txt` | Optional: can include official URLs and source-of-truth pages |

---

# Related skills

| Skill | Role |
|---|---|
| `json-ld.md` | Add verified `sameAs` and entity markup |
| `eeat.md` | Assess trust, authorship, reputation, people-first signals |
| `platform-readiness.md` | Connect entity visibility to AI platform citation readiness |
| `ai-citability.md` | Assess whether owned content is quote-worthy |
| `competitors.md` | Compare brand/entity footprint against competitors |
| `create-report.md` | Crawl/report pipeline that may include automated four-platform visibility |
