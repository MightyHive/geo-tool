# Skill: llms.txt discovery, validation, and sample generation

Use this skill to audit whether a site publishes a useful **`llms.txt`** file, whether it follows the llms.txt proposal, and how to produce or improve a curated LLM-facing site guide.

`llms.txt` is an emerging convention. It is not a replacement for `robots.txt`, `sitemap.xml`, structured data, or good content. Its purpose is to give language models and AI tools a concise, human-readable map of the site’s most useful source-of-truth pages.

**Primary spec & background:** [The /llms.txt file](https://llmstxt.org/)

**Primary output:** a structured audit with live-file status, format validation, content-quality notes, broken/stale link checks, and a generated or improved sample.

---

## Purpose

Use this skill to answer:

- Does the site publish a live `llms.txt`?
- Is it available at the expected location?
- Does it follow the proposed markdown structure?
- Does it provide a useful curated map, or just dump links?
- Are links absolute, canonical, crawlable, and current?
- Does it point AI tools to the site’s best source-of-truth pages?
- Is `## Optional` used correctly for lower-priority material?
- Does the file align with the site’s brand, policies, and content strategy?
- What should the site publish if no live file exists?

---

## Relationship to other files

| File | Purpose |
|---|---|
| `robots.txt` | Crawling access rules |
| `sitemap.xml` | Bulk URL discovery |
| `llms.txt` | Curated LLM-oriented orientation and priority links |
| JSON-LD | Machine-readable structured entity/content data |
| `humans.txt` | Human/team attribution, if used |
| `ai.txt` / `Content-Signal` | Emerging AI preference/policy signals |

All can coexist.

---

## Inputs

Minimum inputs:

| Input | Use |
|---|---|
| `https://{host}/llms.txt` | Primary live-file check |
| `https://{host}/.well-known/llms.txt` | Alternative/well-known location |
| `llms_fetched.txt` | Saved live file from crawl |
| `llms.txt` | Generated sample from crawl |
| Homepage HTML/title/meta description | H1 and summary generation |
| Crawl URL list | Candidate priority links |
| `robots.txt` / sitemap URLs | Discovery section and crawlability checks |

Recommended inputs:

| Input | Use |
|---|---|
| Priority URL list | Better curation than raw crawl |
| `jsonld/*.json` / `sameAs` | Entity profile links |
| Brand visibility findings | Verified official profiles |
| Content audit findings | Source-of-truth pages |
| Product/service taxonomy | Section structure |
| Policy/legal pages | Trust and governance links |
| Markdown mirrors, if available | LLM-friendly alternative URLs |

---

## Outputs

Produce:

1. Live-file discovery result
2. Structure validation
3. Content-quality assessment
4. Link and crawlability checks
5. Optional score
6. Recommended fixes
7. Generated or improved `llms.txt`

---

# Client-facing wording

In client-facing reports, describe `llms.txt` as an **AI guide file**.

Use:

> Publish a short AI guide file (`llms.txt`) that points AI tools to the site’s most important product, service, support, company, and policy pages.

Avoid:

> No live `llms.txt` at origin.

Avoid:

> A draft was written to the audit output.

Preferred action wording:

- **Publish an AI guide file (`llms.txt`).** Use the draft prepared by the audit, then edit it so it points to your most important product, service, support, company, and policy pages.

---

## Priority guidance

Publishing `llms.txt` is useful, but it should not outrank severe crawl, indexing, or content blockers.

Typical priority:

- Quick win if the site can publish the file easily.
- Medium-term if legal, brand, or content review is needed first.

---

# Status definitions

| Status | Meaning |
|---|---|
| **Pass** | Live, valid, useful, curated, and mostly current |
| **Partial** | Present but structurally or editorially weak |
| **Fail** | Missing, invalid, empty, or unusable |
| **Not applicable** | Client intentionally does not want to publish one |
| **Manual check** | Requires stakeholder input or live verification |

---

# Optional scoring model

Use this when the audit needs a numeric `llms.txt` score.

| Theme | Weight |
|---|---:|
| Discoverability and fetchability | 20 |
| Proposal structure | 20 |
| Link quality and crawlability | 20 |
| Curation and usefulness | 25 |
| Maintenance, freshness, and governance | 10 |
| Policy alignment | 5 |
| **Total** | **100** |

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
| No live `llms.txt` at either checked path | 40 |
| Live file is HTML error page or empty | 35 |
| File exists but has no useful links | 50 |
| Most links are broken or non-canonical | 55 |
| File is a full sitemap dump with no curation | 65 |
| Critical pages listed are blocked/noindex | 65 |
| File contains misleading claims or wrong brand/entity | 50 |

---

# Step 1: Check for a live file

## URLs to request

| Location | URL |
|---|---|
| Root | `https://{host}/llms.txt` |
| Well-known | `https://{host}/.well-known/llms.txt` |

Record:

- Requested URL
- Final URL after redirects
- HTTP status
- Content-Type
- Byte size
- First 500 characters
- Whether response is text/markdown
- Whether response is an HTML error page
- Whether both locations exist
- Which location should be treated as canonical

## Pass / partial / fail guidance

| Result | Status |
|---|---|
| HTTP 200, non-empty markdown/text, useful content | Pass |
| Redirects to 200 on same canonical host | Pass or Partial |
| Redirects off-domain unexpectedly | Partial; verify intent |
| 404/410 at both paths | Fail |
| 200 but empty | Fail |
| 200 but HTML error page | Fail |
| 403/5xx/timeout | Unknown or Fail depending persistence |
| Both root and well-known exist with conflicting content | Partial; pick canonical and consolidate |

## Content-Type guidance

Preferred:

```text
text/plain
text/markdown
text/x-markdown
```

Acceptable if body is clearly markdown:

```text
application/octet-stream
```

Flag:

```text
text/html
application/json
```

unless intentionally served and clearly usable.

---

# Step 2: Validate proposal structure

The llms.txt proposal expects a markdown file in this general order:

1. H1 with project/site name
2. Blockquote summary immediately after H1
3. Optional body text
4. Zero or more H2 sections
5. Markdown link lists under H2 sections
6. Optional `## Optional` section for lower-priority links

## Structure checklist

| Rule | What to verify | Pass hint |
|---|---|---|
| Single H1 | One `# Site name` near the top | Clear brand/site name |
| Blockquote summary | `>` line directly after H1 | One concise orientation sentence |
| Optional body | Short explanatory paragraphs/lists | Helps AI understand scope |
| H2 sections | `## Section` headings | Logical groups of important links |
| Markdown links | `- [Label](https://...)` | Absolute canonical URLs |
| Link annotations | Optional `: note` after link | Explains why link matters |
| `## Optional` | Used only for lower-priority context | Critical pages are not optional |
| Markdown simplicity | Easy to parse | Avoid tables-heavy or complex HTML |

## Common failures

- No H1
- Multiple competing H1s
- Missing blockquote
- No H2 sections
- Bare URLs instead of markdown links
- Relative URLs
- Huge wall of links
- Entire sitemap pasted into the file
- Critical docs placed under `## Optional`
- Links to staging, tracking URLs, parameters, or PDFs without notes
- Stale, redirected, or broken links
- Misleading or over-promotional summary
- Conflicting brand name or domain

---

# Step 3: Assess content quality

A good `llms.txt` is curated, concise, and useful.

## Best-practice checks

| Practice | Why it matters |
|---|---|
| Concise summary | Helps models orient quickly |
| Clear scope | Explains what the site is and who it serves |
| Curated links | Prioritises source-of-truth content |
| Informative labels | Models understand destination before fetching |
| Link notes | Clarify why a page matters |
| Logical sections | Helps retrieval and context assembly |
| Absolute URLs | Avoid parser ambiguity |
| Canonical URLs | Avoid duplicate or parameterised versions |
| Fresh links | Reduces trust and retrieval failures |
| Markdown mirrors | Optional but useful for long docs |
| Policy/trust links | Helps entity and reliability understanding |

## Generated draft quality (automated crawl outputs)

When `crawl-site.py` writes `llms.txt` into an audit folder, treat it as a **starter draft** for humans to review — not a substitute for a hand-curated public file. The generator should **curate** from crawl/sitemap candidates, not replay crawl order.

### Primary page selection

`## Primary pages` must prioritise **source-of-truth** pages where they exist in the crawl set:

- Homepage
- About / company
- Contact
- Help, support, or FAQ
- Store locator / branches / locations (when relevant)
- Trade or account entry pages (when relevant)

**Avoid** using `## Primary pages` for:

- Random product / SKU detail URLs
- Long-tail variants
- Internal search, sort, filter, or pagination URLs
- Tracking or campaign-parameter URLs
- Duplicates or near-duplicates
- Malformed or truncated URLs

Individual product URLs — at most a few **representative** examples — belong under a clearly labelled section (for example `## Representative product pages`), not in Primary.

### Ecommerce guidance

For ecommerce properties, the file should emphasise:

1. Main category or department pages  
2. Help and support hubs  
3. Store / location pages (if applicable)  
4. Delivery, returns, warranty, terms, and privacy  
5. **Only then** a small number of representative product examples  

Avoid long lists of interchangeable product URLs unless the business explicitly wants SKU-level AI context.

### Public file wording

Do **not** ship audit-tool or pipeline jargon inside the customer-facing `llms.txt`. Avoid phrases such as:

- "HTML page surfaced by the crawl"
- "Additional URL from crawl"
- "generated by the audit"
- "layout guided by project skeleton"
- "crawl output"

Use short, factual **link notes** that explain what the visitor (or model) will learn from each destination.

### Link validity

- Use absolute URLs on the canonical site host (typically `https`).  
- Skip URLs that contain whitespace, obvious truncation, or scheme/host errors.  
- Prefer canonical paths without `utm_`, `gclid`, `sort=`, `filter=`, `page=`, basket/checkout/login noise.

## Good sections to include

Choose sections that match the site.

| Section | Use |
|---|---|
| `## Primary pages` | Core pages AI tools should read first |
| `## Product categories` (or `## Site sections`) | Department / taxonomy hubs — especially for ecommerce |
| `## Guides and support` | Help, advice, FAQ, and evergreen explainers |
| `## Customer information` | Delivery, returns, warranty, terms, privacy |
| `## Representative product pages` | A **small** set of example PDPs only |
| `## Products` | Broader product or service listings (use sparingly) |
| `## Documentation` | Docs, developer guides, API references |
| `## Guides` | Evergreen educational content |
| `## Support` | Help centre and FAQs |
| `## Policies` | Privacy, terms, returns, editorial policy |
| `## Company` | About, contact, leadership, press |
| `## Research` | Reports, whitepapers, data |
| `## Locations` | Local branches or service areas |
| `## Entity profiles` | Verified social/knowledge profiles |
| `## Sitemap & discovery` | Sitemaps and feeds |
| `## Optional` | Lower-priority background material |

---

# Step 4: Check link quality

For each important link, check:

| Check | Pass hint |
|---|---|
| Absolute URL | Starts with `https://` |
| Canonical host | Uses preferred host |
| HTTP status | Returns 200 or intentional redirect |
| Crawlability | Not blocked by robots for relevant crawlers |
| Indexability | Not `noindex` if intended for discovery |
| Content quality | Destination is useful and current |
| Format | HTML or markdown preferred; PDFs/docs noted |
| Duplicate handling | No repeated parameter/tracking variants |
| Section fit | Link belongs under the right H2 |
| Annotation quality | Note explains link purpose |

Suggested link sample size:

- Check all links if fewer than 30.
- Check top 30 priority links if file is larger.
- Always check links in non-Optional sections.

---

# Step 5: Check policy alignment

`llms.txt` should not conflict with the site’s AI, legal, or crawling policy.

Check consistency with:

- `robots.txt`
- AI crawler allow/block policy
- `Content-Signal`, if present
- `noai` / `noimageai`, if present
- Paywall/licensing rules
- Terms of use
- Privacy policy
- Content licensing
- Editorial policy

## Common conflict examples

| Conflict | Why it matters |
|---|---|
| `llms.txt` lists pages blocked by robots | AI tools may be pointed to inaccessible content |
| `llms.txt` invites AI use but headers say `noai` | Mixed policy signal |
| File lists private or gated content | Legal/access issue |
| File lists staging URLs | Quality and security risk |
| File lists pages with `noindex` | Discovery mismatch |
| File lists outdated docs | Reliability issue |

---

# Step 6: Generate a sample file

## When no live file exists

Create a starter `llms.txt` that follows the proposal.

Use:

- Brand name for H1
- One-sentence blockquote
- Curated priority links
- Sitemaps
- Verified entity profiles
- Optional section for lower-priority links

## When a live file exists

Compare it with the generated sample and recommend improvements:

- Add missing H1 or blockquote
- Replace bare URLs with markdown links
- Convert relative URLs to absolute URLs
- Group links into useful sections
- Remove crawl junk and tracking URLs
- Add notes after important links
- Move secondary material to `## Optional`
- Add source-of-truth pages
- Remove stale/broken links

---

# Automated generation from `crawl-site.py`

The crawler may write a generated `llms.txt` containing:

- H1 from homepage `<title>` or hostname
- Blockquote from meta description or default summary
- Short body context
- `## Primary pages`
- `## Sitemap & discovery`
- `## Entity profiles`
- `## Optional`
- Additional crawled URLs

Related files:

| File | Role |
|---|---|
| `llms_fetched.txt` | Verbatim live file, if found |
| `llms.txt` | Generated sample |
| `samples/llms-txt-skeleton.txt` | Layout reference |
| `robots.txt` / `robots_fetched.txt` | Discovery and access policy |
| `same_as_urls.txt` | Candidate entity profile links |
| `audit_summary.json` | Summary flags |
| `create-report.py` | Technical → **Discovery signals** blends `llms.txt` + sitemap; the llms portion follows this skill’s six-theme model (20/20/20/25/10/5) using live/draft file heuristics where the audit has text—policy uses a weak `robots.txt` proxy only |

## CLI note

`--sample-llms` can point to a skeleton file for consistent generated structure.

Example:

```bash
python3 create-report.py "https://example.com" --sample-llms samples/llms-txt-skeleton.txt
```

---

# Manual polish checklist

After generation:

1. Rewrite H1 to the canonical brand/site name.
2. Tighten the blockquote to one clear sentence.
3. Replace generic section names with site-specific IA.
4. Add high-value pages missing from the crawl.
5. Remove thin, duplicate, parameterised, or irrelevant URLs.
6. Add notes after important links.
7. Add `.md` mirrors where available.
8. Move secondary links to `## Optional`.
9. Add policies, About, Contact, and source-of-truth pages.
10. Verify all links.
11. Confirm robots and AI policy alignment.
12. Decide canonical location: root or well-known.

---

# Recommended starter template

```markdown
# {Brand or site name}

> {One-sentence summary of what the site provides and who it is for.}

This file highlights the most useful pages for understanding {brand/site}, its products, services, documentation, policies, and source-of-truth content. It is curated for AI assistants and other tools that need a concise map of the site.

## Primary pages

- [Homepage](https://www.example.com/): Overview of {brand/site}.
- [About](https://www.example.com/about/): Company background, mission, and contact context.
- [Contact](https://www.example.com/contact/): Official contact and support routes.

## Products and services

- [{Product or service name}](https://www.example.com/product/): Main product/service overview.
- [Pricing](https://www.example.com/pricing/): Plans, pricing, and commercial details.

## Guides and resources

- [{Guide title}](https://www.example.com/guides/example/): Practical guide to {topic}.
- [{FAQ or support hub}](https://www.example.com/help/): Common questions and support documentation.

## Policies

- [Privacy policy](https://www.example.com/privacy/): How user data is handled.
- [Terms](https://www.example.com/terms/): Terms of use.
- [Editorial policy](https://www.example.com/editorial-policy/): How content is created and reviewed.

## Entity profiles

- [LinkedIn](https://www.linkedin.com/company/example/): Official company profile.
- [YouTube](https://www.youtube.com/@example): Official video channel.

## Sitemap & discovery

- [XML sitemap](https://www.example.com/sitemap.xml): Full sitemap for URL discovery.

## Optional

- [{Secondary resource}](https://www.example.com/blog/example/): Useful background reading.
```

---

# Quality examples

## Strong link annotation

```markdown
- [API authentication guide](https://docs.example.com/api/authentication/): Explains authentication methods, token handling, and common errors.
```

## Weak link annotation

```markdown
- [Click here](https://example.com/page)
```

## Strong blockquote

```markdown
> ExampleCo provides cloud-based inventory software for UK retailers, with documentation, pricing, support, and implementation guides.
```

## Weak blockquote

```markdown
> Welcome to our website.
```

---

# Audit deliverable template

```markdown
## llms.txt audit — {host}

**Audit date:** {date}  
**Checked locations:** `/llms.txt`, `/.well-known/llms.txt`  
**Score:** {score}/100, if scored  

### Executive summary

{2–4 sentences. State whether a live file exists, whether it follows the proposal, biggest gap, and priority action.}

### Live file

| Check | Result |
|---|---|
| `/llms.txt` | {status / size / final URL} |
| `/.well-known/llms.txt` | {status / size / final URL} |
| Canonical location | {root / well-known / unclear} |
| Content-Type | {value} |
| Verbatim saved | `{path}` or N/A |

### Structure validation

| Rule | Status | Notes |
|---|---|---|
| Single H1 present | Pass / Partial / Fail | |
| Blockquote summary | Pass / Partial / Fail | |
| Optional body text | Pass / Partial / Fail / N/A | |
| H2 sections | Pass / Partial / Fail | |
| Markdown link lists | Pass / Partial / Fail | |
| Absolute URLs | Pass / Partial / Fail | |
| `## Optional` used appropriately | Pass / Partial / Fail / N/A | |

### Content quality

| Topic | Status | Notes |
|---|---|---|
| Concision and clarity | Pass / Partial / Fail | |
| Curated vs sitemap dump | Pass / Partial / Fail | |
| Useful link annotations | Pass / Partial / Fail | |
| Source-of-truth pages included | Pass / Partial / Fail | |
| Policies/trust pages included | Pass / Partial / Fail | |
| Entity profiles included | Pass / Partial / Fail / N/A | |

### Link checks

| Link | Status | Issue |
|---|---|---|
| | 200 / redirect / broken / blocked / noindex | |

### Policy alignment

| Signal | Finding |
|---|---|
| robots.txt | |
| noai / noimageai | |
| Content-Signal | |
| Paywall/licensing | |

### Generated or suggested file

- Path: `llms.txt`
- Recommended edits:
  1. …
  2. …
  3. …

### Priority actions

1. {Highest-priority action}
2. {Second action}
3. {Third action}
```

---

See **Generated draft quality (automated crawl outputs)** (under Step 3) for automated crawl / `crawl-site.py` curation rules, ecommerce guidance, and public wording constraints.

---

# Common recommendations

## Missing file

```markdown
Publish a curated `llms.txt` at `https://{host}/llms.txt`. Start with the generated sample, then manually edit it to include canonical product/service pages, source-of-truth guides, About/Contact pages, policies, sitemap, and verified official profiles.
```

## File exists but lacks structure

```markdown
Restructure the file to follow the llms.txt proposal: one H1, a concise blockquote summary, logical H2 sections, and markdown link lists with absolute URLs.
```

## File is a sitemap dump

```markdown
Reduce the file to a curated set of high-value pages. Keep full URL enumeration in `sitemap.xml`; use `llms.txt` to guide AI tools to the most authoritative pages.
```

## Links are relative

```markdown
Convert relative links to absolute canonical `https://` URLs so AI tools and parsers can resolve them reliably.
```

## Missing annotations

```markdown
Add short notes after important links explaining what each page contains and when an AI assistant should use it.
```

## Policy conflict

```markdown
Align `llms.txt` with robots and AI-use policy. Do not list pages as recommended context if they are blocked, noindexed, private, or subject to AI-use restrictions.
```

---

# Limitations

- `llms.txt` is an emerging proposal, not a universal standard.
- Major AI/search systems may ignore it or use it inconsistently.
- Publishing `llms.txt` does not guarantee AI citation or indexing.
- It does not override `robots.txt`, meta robots, paywalls, or legal restrictions.
- It should not include private, gated, or sensitive content.
- It requires editorial maintenance as site content changes.
- A generated file should be manually reviewed before publication.