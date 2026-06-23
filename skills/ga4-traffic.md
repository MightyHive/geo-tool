# Skill: GA4 session traffic (monthly trend, by channel) + AI agent referrer gaps

Use this skill to pull **session-level traffic from GA4** via the **Analytics MCP** (`user-analytics-mcp` / `analytics-mcp`), using the **already-configured GCP project** and credentials in Cursor (`GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_PROJECT_ID` in `~/.cursor/mcp.json`).

Deliver:

1. **Weekly session totals** (ISO weeks: **Monday–Sunday**) for the **past 12 months**, broken out by **all default channel groups**.
2. A **gap analysis**: **session `source` / `medium` (or combined) pairs** that look **AI-related** but whose **`sessionDefaultChannelGroup` is not** your property’s **AI channel** (e.g. still `Referral`, `Organic Search`, or `Direct`).

---

## When to use

- GEO / SEO reporting that needs **real traffic context** (how much volume, which channels, trend by week).
- Explaining **under-counted AI traffic**: referrers and UTM patterns that **do not** roll into the channel group you treat as “AI” (custom **Organic AI** rules, order vs Referral, AI Overviews counted under **Organic Search**, etc.).

---

## Before any MCP call (required)

1. **Confirm the MCP server is connected** (no `spawn … ENOENT`; `pipx` or command path resolves from Cursor).
2. **List and read the tool JSON schema** for this server under your Cursor MCP descriptors (parameters names differ by wrapper). Map the logical requests below to the MCP tool’s fields (`property_id`, `date_ranges`, `dimensions`, `metrics`, `limit`, `offset`, etc.).
3. You need the GA4 **property numeric ID** (Admin → Property settings). The Data API expects resource names like `properties/123456789`; if the MCP takes only the number, pass what the schema requires.

---

## Date range

- **Rolling last ~12 months** aligned to GA4 relative dates, e.g.  
  - `startDate`: **`365daysAgo`** (GA4 Data API does **not** accept `12monthsAgo`; only `NdaysAgo`, `yesterday`, `today`, or `YYYY-MM-DD`.)  
  - `endDate`: `yesterday`  
  - In this repo, `GA4_START_DATE` / `GA4_END_DATE` are normalized where possible (e.g. `12monthsAgo` → `365daysAgo`).  
- Do **not** mix calendar months with ISO weeks without labeling; prefer **one** definition and state it in the output.

---

## Report A — Sessions by ISO week × default channel group

**Purpose:** Aggregated **weekly** traffic and **totals for every channel grouping** the property returns.

| Role | API dimension / metric (GA4 Data API v1) |
|------|------------------------------------------|
| Time (Mon–Sun weeks) | `isoYearIsoWeek` |
| Channel | `sessionDefaultChannelGroup` |
| Volume | `sessions` (add `engagedSessions` or `activeUsers` only if the MCP/schema allows and the user wants them) |

**Behavior:**

- One row per **(week × channel)**; sum `sessions` to get **weekly totals** and **channel mix**.
- **ISO weeks** (`isoYearIsoWeek`) use **ISO 8601** boundaries (**week starts Monday**), which matches “Monday to Sunday” weekly aggregation.
- **Edge weeks:** The first and last ISO week in the 12-month window can be **partial**; note that in the narrative if totals for those weeks look low vs neighbors.

**Pagination:** If the MCP returns row caps, page with `offset` / `limit` until all weeks × channels are retrieved.

---

## Report B — AI-like source/medium **outside** the AI channel

**Purpose:** Find **session source/medium** combinations that should often be thought of as **AI referrals or AI-assisted discovery** but are **classified** into a **non-AI** `sessionDefaultChannelGroup`.

**Dimensions (minimum):**

- `sessionSource`
- `sessionMedium`  
  *or* `sessionSourceMedium` if you need a single combined dimension (matches how analysts read “source / medium”).
- `sessionDefaultChannelGroup`

**Metric:**

- `sessions`

**Date range:** Same as Report A.

**Post-process (after pulling rows):**

1. **Define the AI channel label(s)** for this property. Examples (property-specific—**read Admin → Channel groups** or ask the user):
   - Custom group: **Organic AI**, **AI Referral**, etc.
   - If the property has **no** dedicated AI channel, treat the target as *empty* and frame the section as “AI-shaped traffic currently attributed to: Referral / Organic Search / …”.
2. **Flag rows** where:
   - `sessionDefaultChannelGroup` **is not** in the agreed AI channel set (or is not `Organic AI` / your named channel), **and**
   - Source/medium matches **AI heuristics** (below).

**Heuristic patterns** (extend per client; tune regex in Admin rules to match):

| Signal | Examples |
|--------|----------|
| Known AI / LLM hosts (referral) | `chatgpt.com`, `chat.openai.com`, `openai.com`, `claude.ai`, `anthropic.com`, `perplexity.ai`, `gemini.google.com`, `bard.google.com`, `copilot.microsoft.com`, `bing.com` (when medium is `referral` and path/context suggests Copilot), `you.com`, `poe.com`, `meta.ai`, `character.ai`, `mistral.ai`, `deepseek.com` |
| Medium / UTM hints | `medium` containing `ai`, `llm`, `copilot`, `perplexity`; `source` containing `chatgpt`, `claude`, `perplexity`, `copilot` |
| Organic Search caveat | Rows with `Organic Search` may still include **AI Overviews / AI Mode** clicks; flag separately as “possibly AI-assisted search” if the user cares, since **source/medium** may look like `google / organic` rather than a distinct AI channel. |

**Output table (suggested columns):**

- `sessionSource`, `sessionMedium` (or `sessionSourceMedium`)
- `sessionDefaultChannelGroup`
- `sessions`
- `notes` (e.g. “Referrer is LLM domain”, “Organic—possible AI Overview”)

Sort by **sessions descending** so the biggest mis-buckets appear first.

---

## Optional Report C — Totals sanity check

Run a **single-metric** request for the same date range with **no channel dimension** (only `sessions`) or with only `sessionDefaultChannelGroup` **without** week, and confirm totals reconcile with Report A after summing (within rounding / sampling notes).

---

## Deliverable format

1. **Summary:** Date range definition, property ID (redacted if client-facing), any sampling warnings from the API.
2. **Weekly channel chart/table:** From Report A (week × channel, sessions).
3. **AI gap table:** From Report B—**source/medium rows not in the AI channel** that match heuristics, with session counts.
4. **Recommendations:** Point to **Channel group rule order**, missing referrers, or **UTM hygiene** if gaps are large.

### Automated fetch in `create-report.py` (CLI / Streamlit)

The HTML pipeline can call the **same Google Analytics Data API** as this MCP (not the MCP itself — MCP runs only inside Cursor). Requirements:

1. Service account JSON with access to the property; set **`GOOGLE_APPLICATION_CREDENTIALS`** to that file (same as for the Analytics MCP).
2. Install **`google-analytics-data`** (`pip install -r requirements.txt` in the repo).

Then either:

- **CLI:** after crawl, or with `--only-report AUDIT_DIR`:

  ```bash
  python3 create-report.py https://example.com --ga4-property 123456789 --ga4-ai-channels "Organic AI"
  python3 create-report.py --only-report audit_output/example.com_abc123 --ga4-property 123456789
  ```

- **Environment (e.g. Streamlit):** set **`GA4_PROPERTY_ID`** and optionally **`GA4_AI_CHANNEL_NAMES`** (comma-separated). The Streamlit “Run full pipeline” command passes these through to `create-report.py`.

This writes **`ga4_traffic.json`** next to **`audit_summary.json`** before **`report.html`** is generated.

When **`GA4_PROPERTY_ID`** or **`--ga4-property`** is set on a **full crawl**, `create-report.py` also passes flags into **`crawl-site.py`** so the **primary** audit prepends the **top 100 URLs by `screenPageViews`** (last 90 days by default) ahead of sitemap sampling. Those URLs are saved as **`ga4_top_pages.json`** in the audit folder. Competitor crawls do not use your GA4 property for their hosts.

### Optional: `ga4_traffic.json` for `report.html`

Save next to `audit_summary.json` so `create-report.py` can render the GA4 appendix:

- `has_ai_channel` (bool), `ai_channel_names` (list of strings)
- `monthly_sessions`: list of `{ "year_month", "label", "total_sessions", "ai_sessions" }` (calendar `yearMonth` from GA4, e.g. label `Jan 2025`; legacy `weekly` / `iso_week` exports still render)
- `source_medium_gaps`: rows with `session_source`, `session_medium`, channel bucket, `sessions` — only **known AI agent / product hostnames** (see repo `samples/robots.txt`); brand hosts (Facebook, Amazon, Apple) require `bot` in `session_source` (e.g. FacebookBot), not generic social domains; Bytespider/CCBot excluded. Rows are referrers attributed **outside** your configured AI channel bucket. In `report.html` this block is titled **Possible channel bucket gaps** with a short reader note before the table.

See **`create-report.md`** for the full pipeline.

---

## Troubleshooting

| Issue | What to check |
|--------|----------------|
| MCP won’t start | Command/`pipx` path, env vars, restart Cursor |
| `PERMISSION_DENIED` | Service account needs **Viewer** (or **Analyst**) on the GA4 property |
| Empty `yearMonth` rows | Confirm stream type and that sessions exist in the chosen date range |
| Row limits | Paginate; reduce dimensions only if necessary |

---

## Related skills

| Skill | Role |
|-------|------|
| `create-report.md` | Crawl + HTML deck (combine narrative with this traffic appendix) |
| `ai-search-success.md` | On-site AI/search surface checks (complements traffic attribution) |
