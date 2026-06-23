# Skill: AI crawler access report

## Purpose

This skill forms part of the **Technical Setup** category in a GEO audit.

For a site to appear in AI search results, AI-generated answers, or citation surfaces, the relevant crawlers must be able to **discover, fetch, and process** the site’s important pages.

Use this skill to assess whether major AI-related crawlers are **effectively allowed or blocked**, primarily through `robots.txt`, and secondarily through page-level directives, HTTP headers, discovery files, sitemap access, and rendering constraints.

**Primary output:** a concise markdown table showing each crawler, operator, tier, effective status, evidence, and recommendation.

**Default GEO stance:** if the site wants AI search visibility, explicitly allow major **AI search / retrieval crawlers**. Treat training-only crawlers as a separate policy decision.

---

## What this skill does and does not measure

This skill measures:

- Whether AI-related crawlers are allowed by `robots.txt`
- Whether page-level `meta robots` or `X-Robots-Tag` directives restrict AI/search use
- Whether broad AI opt-out signals are present
- Whether important discovery files exist, especially `llms.txt` and sitemaps
- Whether JavaScript rendering may prevent non-rendering AI crawlers from seeing meaningful content
- Whether draft `Content-Signal` preferences are present in `robots.txt`

This skill does **not** measure:

- Whether pages are good enough to be cited
- Whether pages rank in Google or Bing
- Whether AI platforms currently cite the site
- Whether the brand is visible in external knowledge sources
- Whether the content demonstrates E-E-A-T

Pair with:

| Skill | Role |
|---|---|
| `ai-citability.md` | Whether page body content is extractable and citation-worthy |
| `platform-readiness.md` | Platform-specific selection and citation patterns |
| `technical-audit.md` | Indexability, rendering, raw HTML, canonicalisation, performance |
| `llms-txt.md` | Validity and quality of `llms.txt` |
| `ai-search-success.md` | Google AI Overviews / AI Mode readiness |

---

## Inputs

Preferred inputs:

1. Live `robots.txt` fetched from:

   ```text
   https://{domain}/robots.txt
   ```

2. Audit artifacts, if available:

   | Artifact | Use |
   |---|---|
   | `{audit}/robots_fetched.txt` | Preferred ground truth for live production behaviour |
   | `{audit}/robots.txt` | May be merged or template-enhanced; do not assume it is live |
   | `{audit}/llms_fetched.txt` | Live `llms.txt`, if fetched |
   | `{audit}/llms.txt` | Generated or recommended `llms.txt`, if present |
   | Crawl HTML / headers | Used for meta robots and `X-Robots-Tag` checks |
   | Sitemap output | Used for sitemap reachability and discovery checks |

3. Sample key URLs:
   - Homepage
   - Top service/product pages
   - Flagship guides
   - Comparison pages
   - Pricing pages
   - Important support pages
   - Other pages intended to appear in AI answers

4. Optional:
   - Client AI policy
   - Training-data policy
   - Legal/publisher restrictions
   - Known bot abuse concerns
   - Staging/admin path rules

---

## Output

The standard output is:

1. Short summary
2. Main crawler access table
3. Optional composite score
4. Meta robots / HTTP header findings
5. AI-specific files and discovery findings
6. Rendering risk note
7. `Content-Signal` note
8. Prioritised recommendations

The main crawler table should include:

| Column | Meaning |
|---|---|
| User-agent | Crawler token |
| Operator | Company or project |
| Tier | Importance for GEO visibility |
| Status | Effective access status |
| Evidence | Relevant `robots.txt` group/rule and page-level overrides |
| Recommendation | What to change, if anything |

---

# Client-friendly recommendation wording

Crawler findings can mention `robots.txt` and crawler names, but the recommendation must explain the purpose in plain English.

| Technical finding | Client-friendly recommendation |
|---|---|
| `GPTBot` blocked | Allow OpenAI’s crawler (`GPTBot`) in `robots.txt` if ChatGPT visibility is a priority |
| `OAI-SearchBot` blocked | Allow ChatGPT Search (`OAI-SearchBot`) in `robots.txt` so ChatGPT can discover public pages |
| `PerplexityBot` blocked | Allow Perplexity’s crawler (`PerplexityBot`) in `robots.txt` so Perplexity can access pages that should be cited |
| `User-agent: * Disallow: /` | Remove the broad crawler block in `robots.txt` from public pages that should appear in search or AI answers |
| `Bytespider` allowed | Decide whether to allow ByteDance’s AI training crawler (`Bytespider`); this is a policy choice, not a core AI search visibility fix |
| `noindex` on key pages | Remove accidental `noindex` settings from important pages so search engines can list them |

---

## Priority logic for action plans

Prioritise crawler actions in this order:

1. Googlebot and Bingbot access
2. AI search/retrieval crawlers such as `OAI-SearchBot`, `ChatGPT-User`, `PerplexityBot`, and `ClaudeBot`
3. Page-level `noindex` or snippet restrictions
4. Training crawler policy decisions such as `Bytespider`, `CCBot`, and `cohere-ai`

Training crawler decisions should usually be policy notes, not top Quick Wins, unless the client has a specific AI training policy.

---

# When to use

Use this skill for:

- A quick GEO technical gate: “Are we accidentally blocking AI crawlers?”
- A pre-launch or migration audit
- A before/after check following `robots.txt` changes
- A client appendix showing AI crawler access at a glance
- Diagnosing why AI systems may not retrieve or cite a site
- Separating **AI search access** from **AI training permission**

---

# Important distinction: search/retrieval vs training

Do not treat all AI crawlers as equivalent.

Some crawlers are associated with **live search, browsing, retrieval, or user-requested access**. Blocking these can directly reduce AI visibility.

Other crawlers are primarily associated with **training-data collection**. Blocking these may have little or no immediate effect on live AI search visibility.

Use this distinction in recommendations.

| Category | Typical GEO recommendation |
|---|---|
| AI search / retrieval / user-agent crawlers | Usually allow if AI visibility is desired |
| Traditional search crawlers powering AI answers | Usually allow |
| AI training-only crawlers | Allow or block based on client policy |
| Unknown or aggressive crawlers | Investigate before recommending blanket allow |

---

# Full audit workflow

## Step 1: Fetch and identify the live `robots.txt`

Fetch:

```text
https://{domain}/robots.txt
```

Use `robots_fetched.txt` when available.

Record:

- Fetch URL
- Date/time
- HTTP status
- Redirect chain, if any
- Final URL
- Content type
- Whether the file is empty
- Whether it appears generated, cached, or environment-specific
- Whether live `robots_fetched.txt` differs from a merged/template `robots.txt`

### Interpretation

| Condition | Interpretation |
|---|---|
| `200` with valid-looking file | Use as ground truth |
| `404` / missing | Usually means all crawlers are allowed unless restricted elsewhere |
| `403` / blocked fetch | Status unknown; site may block robots fetches or user agents |
| `5xx` | Unknown; retest |
| Empty file | Usually all crawlers allowed unless page-level restrictions exist |
| Redirected file | Use final fetched content but note redirect |
| Different live vs merged file | Prefer live file and flag mismatch |

If `robots.txt` is unreachable or invalid, do not overstate “allowed”. Mark crawler rows as **Unknown** or **Assumed allowed from missing robots.txt**, depending on the fetch result.

---

## Step 2: Parse `robots.txt`

Parse `robots.txt` into user-agent groups.

A group consists of one or more consecutive `User-agent:` lines followed by associated directives until the next group starts.

Recognise at minimum:

- `User-agent`
- `Allow`
- `Disallow`
- `Sitemap`
- `Crawl-delay`
- `Clean-param`, where relevant
- `Content-Signal`, if present

### Matching rules

For each crawler:

1. Look for the most specific matching `User-agent` token.
2. If no specific group exists, apply `User-agent: *`.
3. Evaluate the rules for a representative URL, normally:
   - `/`
   - one key content URL
   - one commercial URL
   - one article/guide URL, where available

### Path rule logic

Use standard robots interpretation:

- Matching is path-prefix based.
- The most specific matching rule generally wins.
- If `Allow` and `Disallow` rules are equally specific, `Allow` is usually treated as winning by major search crawlers.
- `Disallow:` with an empty value means allow all.
- `Disallow: /` means block all paths unless a more specific `Allow` applies.
- `$` and `*` wildcards may be supported by major crawlers but not uniformly by every bot; flag complex patterns.

### Record path nuance

Avoid reducing complex path rules to a misleading all-site status.

Use statuses such as:

- **Allowed**
- **Blocked**
- **Partially blocked**
- **Not listed — inherits `*`**
- **Inherited — blocked by `*`**
- **Ambiguous**
- **Unknown**

---

## Step 3: Check sitemaps declared in `robots.txt`

Record all `Sitemap:` directives.

For each sitemap URL, check:

- HTTP status
- Final URL after redirects
- Whether it is XML or sitemap index
- Whether it lists key pages
- Whether it is blocked by robots rules for representative Tier 1 crawlers
- Whether `lastmod` is present and plausible, if available

Sitemaps matter because AI and search systems use them for discovery and freshness.

---

## Step 4: Check meta robots tags on key pages

For 5–10 key pages, fetch HTML and inspect:

```html
<meta name="robots" content="...">
```

Also check bot-specific variants, such as:

```html
<meta name="GPTBot" content="noindex">
<meta name="OAI-SearchBot" content="noindex">
<meta name="Googlebot" content="noindex">
```

### Patterns to record

| Pattern | Effect |
|---|---|
| `noindex` | Prevents indexing by compliant bots |
| `nofollow` | Discourages following links from that page |
| `none` | Equivalent to `noindex, nofollow` |
| `nosnippet` | Can prevent excerpts/snippets; may reduce AI answer usefulness |
| `max-snippet:0` | Blocks text snippets; may reduce extractability in search surfaces |
| `noarchive` | Archive/cache restriction; usually not a direct AI visibility blocker |
| `noimageindex` | Restricts image indexing |
| `noai` | Emerging/non-standard AI-use restriction; record as policy signal |
| `noimageai` | Emerging/non-standard image AI restriction; record as policy signal |
| Bot-specific directive | May override generic behaviour for that bot |

### Important note

`robots.txt` controls crawling. Meta robots and `X-Robots-Tag` typically control indexing, snippet use, and page-level processing after fetch.

A page can be crawl-allowed but still unsuitable for AI/search visibility if it is `noindex`, `nosnippet`, or contains broad AI opt-out signals.

---

## Step 5: Check HTTP `X-Robots-Tag` headers

For the same sample pages, inspect response headers.

Record:

| Header pattern | Notes |
|---|---|
| `X-Robots-Tag: noindex` | HTTP equivalent of meta `noindex`; applies to the response |
| `X-Robots-Tag: nofollow` | Discourages link following |
| `X-Robots-Tag: none` | Equivalent to `noindex, nofollow` |
| `X-Robots-Tag: nosnippet` | Can reduce preview/extraction in search surfaces |
| `X-Robots-Tag: max-snippet:0` | Blocks snippets |
| `X-Robots-Tag: noai` | Emerging AI-use restriction; record |
| `X-Robots-Tag: noimageai` | Emerging image AI restriction; record |
| Bot-specific header | Applies only to named bot where supported |

Example:

```http
X-Robots-Tag: noindex
X-Robots-Tag: GPTBot: noindex
X-Robots-Tag: Googlebot: nosnippet
```

### Precedence

HTTP headers can apply to HTML and non-HTML assets, including PDFs.

If `X-Robots-Tag` conflicts with meta robots, record the conflict and treat the header as material.

---

## Step 6: Check AI-specific files and discovery signals

Check these paths:

| Path | Purpose |
|---|---|
| `/llms.txt` | Emerging AI-readable site guidance |
| `/.well-known/llms.txt` | Alternative/well-known location |
| `/.well-known/ai-plugin.json` | OpenAI plugin manifest; legacy but still worth recording |
| `/ai.txt` | Proposed/non-standard AI preference file; record only |
| `/sitemap.xml` | Common sitemap location |
| Sitemap URLs from `robots.txt` | Primary discovery signals |

For `llms.txt`, record:

- Present or absent
- HTTP status
- Whether it is markdown/text
- Whether it has a clear H1
- Whether it links to important canonical pages
- Whether links are fetchable
- Whether content appears generated, stale, or useful

Use `llms-txt.md` for a full review.

---

## Step 7: Assess JavaScript rendering risk

Many AI crawlers have limited or inconsistent JavaScript rendering compared with Googlebot.

Check whether meaningful page content appears in:

- Raw HTML
- Rendered DOM
- Reader-mode text
- Crawl text extraction

Flag as a GEO risk if:

- Main content is JS-only
- Product/service descriptions are loaded after user interaction
- FAQs are hidden in non-serialised accordions
- Pricing/comparison data is rendered only client-side
- Schema or entity signals appear only after JavaScript
- Pages are empty shells without rendering

### Rendering risk summary

| Risk level | Meaning |
|---|---|
| Low | Core content is present in initial HTML or stable server-rendered output |
| Medium | Core content is rendered but some important blocks require JS or interaction |
| High | Key content is absent from raw HTML and depends heavily on client-side rendering |
| Unknown | Rendering was not tested |

Recommended mitigations:

- Server-side rendering
- Static generation
- Hydrated HTML with core content present before JS
- Plain HTML copies of key facts/tables
- Accessible accordions/tabs
- XML sitemaps and canonical links in raw HTML

---

## Step 8: Parse `Content-Signal` directives

Using the same `robots.txt` text from Step 1, scan for `Content-Signal:` lines.

The directive is draft/emerging. Treat it as a policy signal, not as universally enforced crawler behaviour.

### Parsing method

1. Scan every line for a line starting with `Content-Signal:` after optional leading whitespace.
2. Treat directive name as case-insensitive.
3. Parse key-value pairs after the colon.
4. Split pairs on commas.
5. Split each pair on the first `=`.
6. Trim whitespace.
7. Normalise values to lowercase.

Known keys:

| Key | Meaning |
|---|---|
| `ai-train` | Preference for use in AI training |
| `search` | Preference for search use |
| `ai-personalization` | Preference for personalisation use |
| `ai-retrieval` | Preference for AI retrieval / answer use |

Valid values:

- `yes`
- `no`

Unknown keys or invalid values should produce a warning.

### Output

| Condition | Output |
|---|---|
| Valid lines present | Summarise each key in plain English |
| No lines present | “No `Content-Signal` directives found; site has not declared preferences via this draft mechanism.” |
| Invalid syntax | Warning with exact line |
| Conflicts across lines | Warning and summarise conflict |

Reference: IETF draft `draft-romm-aipref-contentsignals`. Verify the latest draft name and URL before citing externally.

---

# Baseline crawler list

Verify current user-agent tokens against vendor documentation before making legal, compliance, or contractual recommendations.

## Tier 1 — Critical for AI search visibility

These crawlers are most directly associated with AI search, browsing, retrieval, citations, or user-requested access.

Default GEO recommendation: **allow**, unless the client has a specific policy reason to block.

| User-agent token | Operator | Function | Default stance |
|---|---|---|---|
| `OAI-SearchBot` | OpenAI | ChatGPT Search / search retrieval | Recommend allow |
| `ChatGPT-User` | OpenAI | User-requested browsing/fetching | Recommend allow |
| `GPTBot` | OpenAI | OpenAI crawler; may support AI systems and model improvement depending on vendor policy | Recommend allow if client accepts OpenAI usage |
| `ClaudeBot` | Anthropic | Claude web access / retrieval | Recommend allow |
| `PerplexityBot` | Perplexity | Perplexity search and citations | Recommend allow |

## Tier 2 — Important for broader AI/search ecosystem

These crawlers support major search, assistant, or AI ecosystems.

Default GEO recommendation: usually **allow** if visibility is the priority.

| User-agent token | Operator | Function | Default stance |
|---|---|---|---|
| `Googlebot` | Google | Google Search indexing; foundational for Google AI Overviews | Strongly recommend allow |
| `Google-Extended` | Google | Controls use for Gemini apps / Vertex AI and related AI improvement; not normal Search indexing | Policy-dependent, often allow |
| `GoogleOther` | Google | Google non-search product/research crawls | Usually allow |
| `Bingbot` | Microsoft | Bing index; important for Copilot and Microsoft search surfaces | Strongly recommend allow |
| `Applebot` | Apple | Siri / Spotlight / Apple search features | Usually allow |
| `Applebot-Extended` | Apple | Apple AI training/use extension | Policy-dependent, often allow |
| `Amazonbot` | Amazon | Alexa / Amazon AI discovery | Usually allow |
| `FacebookBot` | Meta | Meta AI-related crawling | Usually allow |

## Tier 3 — Training or dataset crawlers

These are primarily training, dataset, or ecosystem crawlers. They are useful to report but should usually be scored separately from AI search visibility.

| User-agent token | Operator | Function | Default stance |
|---|---|---|---|
| `CCBot` | Common Crawl | Public web dataset used by many AI organisations | Policy-dependent |
| `anthropic-ai` | Anthropic | Anthropic training/research crawler, distinct from `ClaudeBot` | Policy-dependent |
| `cohere-ai` | Cohere | Cohere model training | Policy-dependent |
| `Bytespider` | ByteDance | ByteDance AI/search products | Market-dependent |
| `meta-externalagent` | Meta | Meta external AI data collection | Policy-dependent |
| `omgili` / `omgilibot` | Webz.io / data crawling | Dataset crawling | Usually investigate |

---

# Status definitions

Use these statuses consistently.

| Status | Definition |
|---|---|
| **Allowed** | Specific group or wildcard allows representative key URLs |
| **Explicitly allowed** | Dedicated crawler group exists and allows key URLs |
| **Not listed — inherits `*`** | No specific group; wildcard rules allow key URLs |
| **Blocked** | Specific crawler group blocks representative key URLs |
| **Inherited — blocked by `*`** | No specific group; wildcard blocks key URLs |
| **Partially blocked** | Some key paths allowed, others blocked |
| **Blocked by page-level directive** | `robots.txt` allows crawling but meta/header restricts indexing/snippets/AI use |
| **Ambiguous** | Conflicting or complex rules require path-level testing |
| **Unknown** | Could not fetch or parse required evidence |

---

# How to determine effective access

For each crawler:

1. Find the crawler-specific `User-agent` group.
2. If absent, apply `User-agent: *`.
3. Test representative paths:
   - `/`
   - at least one key commercial URL
   - at least one guide/article URL, if applicable
   - sitemap URL, if relevant
4. Check whether page-level meta/header directives materially restrict usage.
5. Assign status.

### Effective status logic

| Robots result | Meta/header result | Effective status |
|---|---|---|
| Allowed | No restrictive directives | Allowed |
| Allowed | `noindex` on key pages | Blocked by page-level directive |
| Allowed | `nosnippet` / `max-snippet:0` | Allowed but preview/extraction restricted |
| Allowed | `noai` | Allowed but AI-use restricted / policy conflict |
| Blocked | Any | Blocked |
| Not listed and wildcard allows | No restrictive directives | Not listed — inherits `*` |
| Not listed and wildcard blocks | Any | Inherited — blocked by `*` |
| Mixed path results | Any | Partially blocked |
| Cannot fetch | Unknown | Unknown |

---

# Recommendation defaults

Use concise, consistent recommendations.

| Status | Default recommendation |
|---|---|
| Explicitly allowed | No change required; maintain explicit allow if AI visibility is desired |
| Allowed via wildcard | Add explicit allow group for Tier 1/Tier 2 crawlers to reduce policy ambiguity |
| Not listed — inherits `*` | Add explicit `User-agent: {token}` with `Allow: /` for search/retrieval crawlers |
| Blocked | Whitelist if AI visibility is desired; retain narrow disallows for admin, cart, checkout, login, staging, search results |
| Inherited — blocked by `*` | Add crawler-specific allow groups or narrow the wildcard block |
| Partially blocked | Verify whether blocked paths include important content; allow key content paths |
| Blocked by page-level directive | Remove `noindex`, `nosnippet`, `max-snippet:0`, or AI opt-out where visibility is desired |
| Ambiguous | Test specific URLs with a robots parser and simplify rules |
| Unknown | Fix fetch/parse issue and retest |

---

# Suggested robots.txt allow-list pattern

Use only when aligned with the client’s AI policy.

```txt
# AI search / retrieval crawlers
User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: PerplexityBot
Allow: /

# Optional: OpenAI broader crawler
User-agent: GPTBot
Allow: /

# Search engines that influence AI answers
User-agent: Googlebot
Allow: /

User-agent: Bingbot
Allow: /

# Broader AI ecosystem, policy-dependent
User-agent: Google-Extended
Allow: /

User-agent: Applebot
Allow: /

User-agent: Applebot-Extended
Allow: /

User-agent: GoogleOther
Allow: /

User-agent: Amazonbot
Allow: /

User-agent: FacebookBot
Allow: /

# Keep sensitive paths restricted
User-agent: *
Disallow: /admin/
Disallow: /cart/
Disallow: /checkout/
Disallow: /login/
Disallow: /internal-search/
```

Do not blindly add `Allow: /` if the site has legal, privacy, paywall, licensing, or abuse concerns.

---

# Composite score model

Use this only when a numeric sub-score is needed for dashboards or reports.

Score out of **100**.

| Component | Weight | Scoring |
|---|---:|---|
| Tier 1 AI search/retrieval access | 45 | Percentage of Tier 1 crawlers effectively allowed on key URLs |
| Foundational search crawlers for AI surfaces | 20 | Googlebot and Bingbot allowed and not blocked by page-level directives |
| Tier 2 broader AI ecosystem access | 15 | Percentage of selected Tier 2 crawlers effectively allowed |
| No blanket AI/search blocks | 10 | No sitewide wildcard block, `noindex`, `noai`, or snippet block affecting key pages |
| Discovery files and sitemap access | 10 | `llms.txt` useful, sitemap reachable, key pages discoverable |

### Formula

```text
AI crawler access score =
Tier1_access_component
+ Foundational_search_component
+ Tier2_access_component
+ No_blanket_blocks_component
+ Discovery_component
```

### Component details

#### 1. Tier 1 AI search/retrieval access — 45 points

Tier 1 set:

- `OAI-SearchBot`
- `ChatGPT-User`
- `GPTBot`
- `ClaudeBot`
- `PerplexityBot`

```text
Tier1 component = (effectively allowed Tier 1 crawlers / 5) × 45
```

#### 2. Foundational search crawlers — 20 points

These are essential because Google and Bing indexes influence AI answer surfaces.

| Crawler | Points |
|---|---:|
| `Googlebot` effectively allowed | 10 |
| `Bingbot` effectively allowed | 10 |

#### 3. Tier 2 broader AI ecosystem access — 15 points

Recommended selected Tier 2 set:

- `Google-Extended`
- `GoogleOther`
- `Applebot`
- `Applebot-Extended`
- `Amazonbot`
- `FacebookBot`

```text
Tier2 component = (effectively allowed selected Tier 2 crawlers / 6) × 15
```

If a client intentionally blocks training-related Tier 2 bots, note this as policy-driven and consider scoring a separate “AI training openness” metric.

#### 4. No blanket AI/search blocks — 10 points

| Condition | Points |
|---|---:|
| No broad wildcard block, no sampled `noindex`, no `noai`, no severe snippet restrictions | 10 |
| Minor restrictions affect non-critical pages only | 7 |
| Some key pages restricted | 3 |
| Broad block across key pages/site | 0 |

#### 5. Discovery files and sitemap access — 10 points

| Signal | Points |
|---|---:|
| Useful live `llms.txt` or well-known equivalent | 4 |
| Sitemap declared in `robots.txt` and reachable | 3 |
| Sitemap includes key pages with plausible canonical URLs | 2 |
| Freshness signals present, e.g. plausible `lastmod` | 1 |

---

# Training-data policy score

Optional. Use this only if the client wants a separate measure of how open the site is to model training crawlers.

Do **not** mix this into the main AI crawler access score unless the client explicitly wants training openness to affect the GEO readiness score.

| Component | Suggested scoring |
|---|---|
| Training crawlers allowed | Percentage of selected training crawlers allowed |
| Policy clarity | Explicit allow/block groups rather than accidental inheritance |
| Content licensing alignment | Robots policy matches legal/editorial stance |
| Abuse controls | Sensitive paths remain blocked |

Training crawler set may include:

- `CCBot`
- `anthropic-ai`
- `cohere-ai`
- `Bytespider`
- `meta-externalagent`
- Other client-relevant bots

---

# Deliverable template

```markdown
## AI crawler access — {domain}

**Source:** `{source}`  
**Fetched:** {date}  
**Robots status:** {HTTP status / notes}  
**Scope:** {homepage + number of sampled key pages}  

### Summary

{One to three sentences. Mention Tier 1 gaps first. Mention any broad blocking, noindex, nosnippet, noai, sitemap, or rendering concerns.}

### Crawler access table

| User-agent | Operator | Tier | Status | Evidence | Recommendation |
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

### Composite score

**AI crawler access score:** {score}/100

| Component | Score |
|---|---:|
| Tier 1 AI search/retrieval access | /45 |
| Foundational search crawlers | /20 |
| Tier 2 broader AI ecosystem | /15 |
| No blanket AI/search blocks | /10 |
| Discovery files and sitemap access | /10 |
| **Total** | **/100** |

### Sampled page directives

| URL | Meta robots | X-Robots-Tag | Effective issue |
|---|---|---|---|
| | | | |

### Discovery files

| File / signal | Status | Notes |
|---|---|---|
| `/llms.txt` | | |
| `/.well-known/llms.txt` | | |
| `/.well-known/ai-plugin.json` | | |
| `/ai.txt` | | |
| Sitemap from `robots.txt` | | |

### Rendering risk

**Risk level:** Low / Medium / High / Unknown

{Short note on whether core content is present in raw HTML or requires JavaScript.}

### Content-Signal

{Parsed result, absence note, or warnings.}

### Priority recommendations

1. {Highest-impact fix}
2. {Second fix}
3. {Third fix}
```

---

# Example evidence wording

Use concise evidence in the table.

| Scenario | Evidence example |
|---|---|
| Dedicated allow | `User-agent: OAI-SearchBot` with `Allow: /` |
| Dedicated block | `User-agent: GPTBot` with `Disallow: /` |
| Wildcard allow | No specific group; inherits `User-agent: *` with no blocking disallow |
| Wildcard block | No specific group; inherits `User-agent: * Disallow: /` |
| Partial block | Allows `/blog/`, blocks `/products/`; key commercial pages affected |
| Meta noindex | Robots allows, but sampled URL has `<meta name="robots" content="noindex">` |
| Header noindex | Robots allows, but `X-Robots-Tag: noindex` found |
| Snippet restriction | `max-snippet:0` or `nosnippet` found on key pages |
| AI opt-out | `noai` found in meta/header or `Content-Signal: ai-retrieval=no` |
| Unknown | `robots.txt` returned 403 / timeout / parse failed |

---

# Crawler reference notes

Use these notes for recommendations, but verify current vendor documentation before quoting exact details externally.

## OpenAI

| Token | Notes |
|---|---|
| `OAI-SearchBot` | Search-focused crawler for ChatGPT Search-style retrieval. High GEO priority. |
| `ChatGPT-User` | User-initiated requests where a ChatGPT user asks to fetch or inspect a page. High GEO priority. |
| `GPTBot` | OpenAI crawler. May be associated with model improvement/training and broader AI systems depending on OpenAI’s current policy. Policy-sensitive but often allowed for maximum visibility. |

## Anthropic

| Token | Notes |
|---|---|
| `ClaudeBot` | Claude web/retrieval crawler. High GEO priority. |
| `anthropic-ai` | Training/research crawler. Treat separately from Claude search/retrieval access. |

## Perplexity

| Token | Notes |
|---|---|
| `PerplexityBot` | Powers Perplexity discovery and citation-style AI search. High GEO priority. |

## Google

| Token | Notes |
|---|---|
| `Googlebot` | Critical for Google Search indexing and therefore Google AI Overviews visibility. Do not omit from AI crawler audits. |
| `Google-Extended` | Controls use for Gemini apps / Vertex AI and related AI use; does not control normal Google Search indexing. Policy-sensitive. |
| `GoogleOther` | Used for various Google product/research crawls. Usually allow unless policy says otherwise. |

## Microsoft

| Token | Notes |
|---|---|
| `Bingbot` | Critical for Bing indexing and Microsoft Copilot/search surfaces. Do not omit from AI crawler audits. |

## Apple

| Token | Notes |
|---|---|
| `Applebot` | Used for Siri, Spotlight, and Apple search features. |
| `Applebot-Extended` | Extension related to Apple AI training/use. Policy-sensitive. |

## Meta

| Token | Notes |
|---|---|
| `FacebookBot` | Meta AI-related crawling. Link previews may use separate crawlers. |
| `meta-externalagent` | Meta external AI data collection. Policy-sensitive. |

## Common Crawl and other training/data crawlers

| Token | Notes |
|---|---|
| `CCBot` | Common Crawl dataset crawler; frequently used in AI training datasets. |
| `cohere-ai` | Cohere training crawler. |
| `Bytespider` | ByteDance crawler; market and abuse considerations may affect recommendation. |

---

# Common findings and recommended language

## Finding: Tier 1 bots blocked

```markdown
Several Tier 1 AI search/retrieval crawlers are blocked by `robots.txt`. This is a direct GEO visibility risk because these agents may be unable to fetch key pages for AI answers or user-requested browsing. If AI visibility is a goal, add explicit allow groups for the affected crawlers while keeping sensitive paths blocked.
```

## Finding: Bots only allowed through wildcard

```markdown
The crawler is not explicitly listed and currently inherits the wildcard group. This may be acceptable if `User-agent: *` remains open, but it is fragile. For GEO visibility, add an explicit allow group so future wildcard changes do not accidentally block the crawler.
```

## Finding: Training crawlers blocked but search crawlers allowed

```markdown
The site blocks training-oriented crawlers while allowing search/retrieval crawlers. This is a coherent policy if the client wants AI search visibility without broad training-data participation. Maintain this distinction explicitly in `robots.txt`.
```

## Finding: Googlebot or Bingbot blocked

```markdown
Googlebot or Bingbot is restricted on key pages. This is a severe search and GEO risk because Google and Bing indexes influence AI answer surfaces such as Google AI Overviews and Microsoft Copilot. Resolve before prioritising AI-specific crawler changes.
```

## Finding: Noindex on key pages

```markdown
`robots.txt` allows crawling, but sampled key pages include `noindex` directives. This can prevent search and AI systems from using the pages even when crawler access appears open. Remove `noindex` from pages intended to rank or be cited.
```

## Finding: Nosnippet or max-snippet:0

```markdown
Sampled key pages restrict snippets using `nosnippet` or `max-snippet:0`. This may reduce the ability of search and AI answer systems to show previews or extract passages. If citation visibility is desired, remove or relax snippet restrictions on important pages.
```

## Finding: JS-only content

```markdown
Crawler access appears open, but important content is only available after client-side JavaScript rendering. Many AI crawlers have limited rendering compared with Googlebot. Move critical answer text, entity details, pricing, FAQs, and comparison data into server-rendered HTML.
```

## Finding: Missing sitemap

```markdown
No reachable sitemap was found or declared in `robots.txt`. This weakens discovery and freshness signalling. Add a valid XML sitemap, declare it in `robots.txt`, and ensure it includes canonical key pages.
```

## Finding: Missing llms.txt

```markdown
No live `llms.txt` was found. This is not currently a universal requirement, but it is a useful GEO guidance file. Add a concise `llms.txt` linking to canonical source-of-truth pages, documentation, policies, and high-value guides.
```

---

# Limitations

State these where relevant:

- `robots.txt` is advisory; crawler compliance varies.
- Some AI platforms use search indexes, licensed datasets, APIs, or third-party sources rather than direct crawling.
- Allowing a crawler does not guarantee indexing, ranking, retrieval, citation, or referral traffic.
- Blocking a training crawler may not remove content from previously collected datasets.
- Bot tokens and policies change; verify vendor documentation for current user-agent names.
- Page-level directives may be interpreted differently across platforms.
- `noai`, `noimageai`, `/ai.txt`, and `Content-Signal` are emerging/non-universal mechanisms.
- JavaScript rendering capability varies widely across AI crawlers.

---

# Integration with repo artifacts

| Artifact | Use |
|---|---|
| `{audit}/robots_fetched.txt` | Preferred source for live `robots.txt` |
| `{audit}/robots.txt` | May include merged template suggestions; compare but do not treat as live unless confirmed |
| `samples/robots.txt` | Reference allow/block patterns |
| `{audit}/llms_fetched.txt` | Live `llms.txt` evidence |
| `{audit}/llms.txt` | Generated/recommended `llms.txt` |
| Crawl HTML/header files | Meta robots and `X-Robots-Tag` checks |
| `json-ld.txt` / `jsonld/*.json` | Not part of crawler access, but useful for downstream structured data review |

`create-report.py` implements the **composite 0–100 model** above with automation limits: Tier 1 (45) + Googlebot/Bingbot (20) + Tier-2 ecosystem set (15) from `robots.txt` and hero-page `noindex`; blanket `*` / sampled `noai` (10); discovery (10) from live `llms.txt`, reachable `Sitemap:` (3), and crawl-via-sitemap proxy (+2). It does not parse `Content-Signal:` or full per-path robots nuance—use this skill for the complete workflow.

---

# Related skills

| Skill | Role |
|---|---|
| `technical-audit.md` | Indexability, rendering, raw HTML, canonicals, performance |
| `llms-txt.md` | Review and improve `llms.txt` |
| `ai-citability.md` | Score whether accessible content is cite-worthy |
| `platform-readiness.md` | Platform-specific AI visibility scoring |
| `ai-search-success.md` | Google AI Overviews / AI Mode readiness |
| `json-ld.md` | Structured data and entity markup |
