# Counterfactual research: AI impact on search traffic

## Hypothesis

AI proliferation will affect different brands and different types of traffic in different ways. When we estimate the impact of AI on traffic, we need to take these variations into account.

The test datasets are split by the following dimensions:

| Dimension | Categories | Explanation |
| --- | --- | --- |
| **Site type** | Publisher | More likely to attract users looking for information. Prioritise visits to generate revenue, not sales. |
| | Advertiser / retailer | More likely to attract users looking for products. Prioritise visits to make sales; sessions alone are not valuable without conversion. |
| **Query type** | Branded | Branded searches show existing awareness of the brand. |
| | Non-branded | Non-branded searches attract new users that may not have brand awareness. |
| **Intent** | Informational | Users searching for information, without an intent to buy. |
| | Commercial / shopping | Users looking to purchase a product. |
| **Channel** | SEO | Organic search. |
| | PPC | Paid search. |
| | Direct | Direct / typed-in traffic. |

---

## Segments and impact

### Site anonymisation

| Label | Group |
| --- | --- |
| Site 1, Site 2, Site 3 | Publishers |
| Site 4, Site 5, Site 6 | Advertisers |

Brand names and industry-identifying keywords are redacted throughout this document.

### Segment definitions

| Segment | Example queries (redacted) | Expected impact | Rationale |
| --- | --- | --- | --- |
| **1. Publishers, Non-Branded, Informational** | "[informational how-to / schedule / guide-style query]" | Most negative of all segments | AI chatbots answer these without citing sources; AI summaries provide information without a click. |
| **2. Publishers, Branded** | "[publisher brand name]" | Less impactful than non-branded; brand loyalty may retain some SEO and Direct. Requires testing. | Brand-aware users are likelier to keep visiting. Some decline possible if users switch to chatbots, but less than Segment 1. |
| **3. Advertisers, Non-Branded, Commercial** | "[best place to buy X]" / "[where is the best Y]" | Trends may fall as chatbot usage rises. Non-brand SEO/PPC may fall without referral; AI Overviews may offset. | Chatbots reference brands and link when intent is commercial. That may raise brand awareness and offset some PPC decline from less Google Search use. |
| **4. Advertisers, Branded** | "[advertiser brand]" / "[brand + product]" | Branded Trends may fall; Direct should be less affected; branded SEO less likely to be cannibalised by AI Overviews. | Users seeking a specific site are unlikely to change behaviour. Chatbots/overviews cannot fully substitute a commercial site visit. |

### Metrics to test

| Metric | Rationale |
| --- | --- |
| **Google Trends query volume** | Indicator of changing search behaviour since chatbots. Expect decreased Google usage as chatbots become preferred for information/advice. Note: Trends still includes searches with AI summaries — summaries should not cut query volume/impressions much, but will affect clicks. |
| **SEO clicks/sessions** | Also indicates chatbot substitution. If chatbots raise brand awareness, branded search may still appear as an indirect AI effect. GA4 does not separate AI-summary clicks from classic SERP clicks. |
| **Non-brand PPC sessions** | Indicator of Google Search used as a shopping or informational tool. |
| **Direct sessions** | Expect relative stability (brand-aware users). |
| **Conversion / purchase** | Session quality. Advertisers only. AI may raise quality by sending higher-intent users. |

### Expected direction of metrics

#### Segment 1: Publishers, Non-Branded, Informational

| Metric | Expected direction |
| --- | --- |
| Google Trends query volume | Down |
| SEO clicks/sessions | Down |
| Non-brand PPC sessions | Down or unchanged |
| Direct sessions | Little/no offset |
| Conversion/purchase | Irrelevant for publishers (for our purposes) |

#### Segment 2: Publishers, Branded

| Metric | Expected direction |
| --- | --- |
| Google Trends query volume | Down |
| SEO clicks/sessions | Down or stable |
| Branded PPC sessions | Insignificant for this segment |
| Direct sessions | Stable |
| Conversion/purchase | Irrelevant for publishers (for our purposes) |

#### Segment 3: Advertisers, Non-Branded, Commercial

| Metric | Expected direction |
| --- | --- |
| Google Trends query volume | Down |
| SEO clicks/sessions | Down or stable |
| Non-brand PPC sessions | Down (but control for spend) |
| Direct sessions | Stable / possibly up |
| Conversion/purchase | Stable / possibly up (higher intent sessions) |

#### Segment 4: Advertisers, Branded

| Metric | Expected direction |
| --- | --- |
| Google Trends query volume | Down |
| SEO clicks/sessions | Stable or slightly changed |
| Branded PPC sessions | Stable or slightly changed |
| Direct sessions | Stable / possibly up |
| Conversion/purchase | Stable / possibly up (higher intent sessions) |

---

## Approach to analysis

### 1. Create counterfactuals

Predict each metric, for each dataset, over the AI proliferation period — allowing for site effects, seasonality, market demand, and PPC intensity.

Counterfactuals estimate volume **had AI not been introduced**. With a decent training window we can model expected volume.

For all segments:

```
Expected volume = f(Google Trends demand, spend, seasonality, site effects, trend)
```

### 2. Calculate actual volume for each metric

Pull actual volume over the AI proliferation period. For channel mix:

```
Actual sessions = PPC sessions + SEO sessions + Direct sessions
```

Other channels are out of scope (less likely AI-affected).

### 3. Calculate difference per metric

```
AI-period impact = Actual − Expected
Gap % = (Actual − Expected) / Expected
```

---

## Analysis parameters

### Periods

**A. Broad AI proliferation**

- Baseline: 2022-06-01 to 2024-06-30
- Evaluation: 2024-07-01 onward

**B. Google AI Overviews / sustained AI traffic** *(used for Phase 2 results below)*

- Training: 2024-09-01 → 2025-03-31 (+ transition 2025-04-01 → 2025-05-25)
- Evaluation: 2025-05-26 onward
- Christmas (24 Dec–1 Jan) and Easter (Good Friday–Easter Monday) weeks **masked** from train and eval

### Spend proxy

Real £ spend is not available in current GA4 exports. Models that need spend use **lagged PPC sessions** (prior week, by site):

- `brand_PPC_spend_t` ≈ `brand_PPC_sessions_{t−1}`
- `nonbrand_PPC_spend_t` ≈ `nonbrand_PPC_sessions_{t−1}`
- `spend_t` (purchases) ≈ `total_PPC_sessions_{t−1}`

This avoids regressing PPC outcomes on contemporaneous sessions. The proxy is intensity/activity, not £ — coefficients are not true ROI.

### Model set (Phase 2)

#### Model 1: SEO sessions

```
log_SEO_sessions ~
  log_brand_trends
+ log_nonbrand_info_trends
+ log_nonbrand_commercial_trends
+ C(site)
+ seasonality
+ trend
```

#### Model 2a: Branded PPC sessions

```
log_brand_PPC_sessions ~
  log_brand_trends
+ log_brand_PPC_spend
+ C(site)
+ seasonality
+ trend
```

#### Model 2b: Non-branded PPC sessions

```
log_nonbrand_PPC_sessions ~
  log_nonbrand_trends
+ log_nonbrand_PPC_spend
+ C(site)
+ seasonality
+ trend
```

(`nonbrand_trends` = info trends for publishers; commercial trends for advertisers.)

#### Model 3: Direct sessions (advertisers only)

```
log_Direct_sessions ~
  log_brand_trends
+ C(site)
+ seasonality
+ trend
```

#### Model 4: Purchases (advertisers only)

```
log_purchases ~
  log_sessions
+ log_trends_volume
+ log_spend
+ C(site)
+ sin_annual
+ cos_annual
+ trend
```

### Additional models (planned / not yet in Phase 2 results)

**Brand / non-brand mix**

```
brand_share_of_search_sessions = brand_search_sessions / total_search_sessions
brand_share_of_search_sessions ~ post_AI + C(site) + seasonality + trend
```

**Difference-in-differences**

```
log_sessions ~ post_AI * C(segment) + C(site) + sin_annual + cos_annual + trend
```

Compare high-AI-exposure segments (e.g. Segment 1) to lower-exposure (e.g. Segment 4).

**AI intensity validation**

```
gap_pct ~ AI_visibility + C(site) + C(segment)
```

### Hypothesis set (summary)

| # | Segment | Claim |
| --- | --- | --- |
| H1 | Publishers, branded | Branded demand and branded SEO/PPC decline as users go to chatbots instead of searching for the publisher. |
| H2 | Publishers, non-brand informational | AI substitutes informational search clicks. |
| H3 | Advertisers, branded | AI may reduce Google brand searches but leave branded SEO/PPC/Direct stable or up via brand confidence. |
| H4 | Advertisers, non-brand commercial | AI may reduce non-brand discovery clicks; recommendations may offset via brand or Direct. |

---

## Model plan

### Segment sites (anonymised)

| Segment | Sites | Keywords |
| --- | --- | --- |
| **1 & 2: Publishers** | Site 1, Site 2, Site 3 | Brand terms and non-brand informational terms **[redacted]** |
| **3 & 4: Advertisers** | Site 4, Site 5, Site 6 | Brand terms and non-brand commercial terms **[redacted]** |

### Phase 1: Descriptive diagnostics

For each segment: Trends change, sessions change, sessions per Trends point, purchases/CVR if relevant. Report median/mean Δ and sites ↑/↓.

### Phase 2: Segment counterfactuals

Panel OLS (Actual − Expected) using the formulas above. S1/S2 share the same publisher panel; S3/S4 share the same advertiser panel.

### Phase 2b: Site-level + equal-weight overall

- **Per site:** site-specific OLS on raw levels (no `C(site)`; HC3 SEs).
- **Overall (normalised):** each metric ÷ site baseline mean (`2022-06-01` → `2025-03-31`; pre-eval mean if baseline mean ≈ 0), then panel with `C(site)` so large-volume sites do not dominate.

### Phase 3–5

Aggregate impact; DiD; AI intensity validation (as above).

---

# Results

## Phase 1: Descriptive diagnostics

### Summary table — eval vs baseline (% change)

| Segment | Metric | Median Δ | Mean Δ | Sites ↑ / ↓ |
| --- | --- | --- | --- | --- |
| **S1 Publishers · Non-Branded** | Google Trends | +8.8% | +19.1% | 2↑ / 1↓ |
| | Sessions | −19.7% | −18.0% | 0↑ / 3↓ |
| | Sessions / Trends pt | −19.0% | −25.5% | 0↑ / 3↓ |
| | Purchases | −68.7% | +205.7%* | 1↑ / 2↓ |
| | CVR | −53.3% | +354.4%* | 1↑ / 2↓ |
| **S2 Publishers · Branded** | Google Trends | −10.0% | +2.1% | 1↑ / 2↓ |
| | Sessions | −19.7% | −18.0% | 0↑ / 3↓ |
| | Sessions / Trends pt | −4.5% | −15.3% | 0↑ / 3↓ |
| | Purchases / CVR | (same session base as S1) | | |
| **S3 Advertisers · Non-Branded** | Google Trends | +8.0% | +19.3% | 3↑ / 0↓ |
| | Sessions | +10.6% | +5.6% | 2↑ / 1↓ |
| | Sessions / Trends pt | −13.5% | −11.1% | 1↑ / 2↓ |
| | Purchases | −8.0% | −27.5% | 1↑ / 2↓ |
| | CVR | +11.4% | −23.7% | 2↑ / 1↓ |
| **S4 Advertisers · Branded** | Google Trends | −9.7% | −7.0% | 1↑ / 2↓ |
| | Sessions | +10.6% | +5.6% | 2↑ / 1↓ |
| | Sessions / Trends pt | +17.3% | +12.0% | 2↑ / 1↓ |
| | Purchases | −8.0% | −27.5% | 1↑ / 2↓ |
| | CVR | +11.4% | −23.7% | 2↑ / 1↓ |

\*Means distorted by sparse / near-zero purchase series on some publisher sites.

### Headline patterns (Phase 1)

**Publishers (S1/S2):** Non-branded Trends up slightly; all three sites down ~14–20% on sessions. Sessions-per-trend also down (worse efficiency vs demand). Branded Trends flat/down; branded denominator makes sessions-per-trend look less bad (−4.5% median vs −19% non-branded).

**Advertisers (S3/S4):** Non-branded Trends up across all sites; sessions up (+11% median). Sessions-per-trend down on non-branded (−13.5% median) but up on branded (+17.3% median) — sessions grew faster than branded Trends fell. Purchases slightly down; CVR mixed (one advertiser up on CVR; another effectively ~0 purchases).

---

## Phase 2: Segment counterfactuals (volume-weighted panels)

**Method:** Panel OLS, Period B, holidays masked, lagged PPC sessions as spend proxy. Gap % = (actual − expected) / expected on non-holiday eval weeks.

S1 ≡ S2 (same publisher sites). S3 ≡ S4 (same advertiser sites).

| Model | Publishers (S1/S2) | Advertisers (S3/S4) |
| --- | ---: | ---: |
| **1. SEO sessions** | **+14.0%** (R² ≈ 1.00) | **−15.8%** (R² ≈ 0.98) |
| **2a. Branded PPC** | **+5.9%** (R² ≈ 0.96) | **−12.9%** (R² ≈ 1.00) |
| **2b. Non-branded PPC** | **−69.8%** (R² ≈ 0.94) | **−52.1%** (R² ≈ 0.99) |
| **3. Direct** | — | **−9.0%** (R² ≈ 0.99) |
| **4. Purchases** | — | **+15.0%** (R² ≈ 1.00) |

### Interpretation (Phase 2)

1. **SEO:** Volume-weighted publisher panel is **above** the demand/seasonality counterfactual (+14%). Advertiser SEO is **below** (−16%).
2. **PPC (with spend proxy):** Non-brand PPC is strongly **under** expected for both groups (−70% publishers, −52% advertisers). Brand PPC near flat/slightly up for publishers (+6%), soft for advertisers (−13%).
3. **Direct (advertisers):** Soft vs brand-trends baseline (−9%).
4. **Purchases (advertisers):** **Above** expected (+15%) despite softer SEO/Direct — conversion held up relative to sessions + demand + intensity.

**Caveat:** Panel gaps are volume-weighted; a large site can dominate. Phase 2b equal-weight / site-level views below can diverge (especially publisher SEO).

Outputs: `research/analysis/outputs/segment_counterfactuals/`

---

## Phase 2b: Site-level + overall (normalised)

**Anonymisation:** Site 1–3 = publishers; Site 4–6 = advertisers.

**Spend proxy:** same lagged PPC sessions as Phase 2.

**Overall:** baseline-indexed metrics + `C(site)` so each site contributes on a comparable scale.

### Overall (normalised, equal-weight)

| Model | Publishers (Sites 1–3) | Advertisers (Sites 4–6) |
| --- | ---: | ---: |
| **1. SEO sessions** | **−1.7%** (R² 0.61) | **−11.9%** (R² 0.62) |
| **2a. Branded PPC** | n/a (only 1 site scalable) | **−16.1%** (R² 0.99) |
| **2b. Non-branded PPC** | **−7.3%** (R² 0.66) | **−27.3%** (R² 0.92) |
| **3. Direct** | — | **−8.6%** (R² 0.61) |
| **4. Purchases** | — | **+3.6%** (R² 0.98) |

Equal-weight publisher SEO is near flat (−2%), vs +14% in the volume-weighted segment panel — large sites were driving the Phase 2 publisher SEO gap.

### By site — SEO sessions (raw, site-specific)

| Site | Gap % | R² |
| --- | ---: | ---: |
| Site 1 | −18.5% | 0.83 |
| Site 2 | −8.4% | 0.82 |
| Site 3 | −0.4% | 0.91 |
| Site 4 | −21.6% | 0.97 |
| Site 5 | −35.7% | 0.81 |
| Site 6 | −16.0% | 0.78 |

All six sites are at or below their own site-specific SEO counterfactual; advertiser Sites 4–5 are the weakest.

### By site — Direct (advertisers)

| Site | Gap % | R² |
| --- | ---: | ---: |
| Site 4 | −15.7% | 0.90 |
| Site 5 | −10.7% | 0.73 |
| Site 6 | **+15.1%** | 0.77 |

### By site — Purchases (advertisers)

| Site | Gap % | R² |
| --- | ---: | ---: |
| Site 4 | −11.3% | 0.97 |
| Site 5 | n/a * | n/a |
| Site 6 | **+23.0%** | 0.82 |

### By site — Branded PPC

| Site | Gap % | R² |
| --- | ---: | ---: |
| Site 1 | −97.5% | 0.73 |
| Site 2 | n/a * | n/a |
| Site 3 | n/a * | n/a |
| Site 4 | **+29.3%** | 0.89 |
| Site 5 | n/a * | n/a |
| Site 6 | −48.8% | 0.95 |

### By site — Non-branded PPC

| Site | Gap % | R² |
| --- | ---: | ---: |
| Site 1 | −87.1% | 0.70 |
| Site 2 | −5.7% | 0.61 |
| Site 3 | −22.8% | 0.20 |
| Site 4 | **+14.7%** | 0.92 |
| Site 5 | −96.8% | 0.71 |
| Site 6 | **+131.0%** | 0.81 |

\*Unreliable: thin series (expected ≈ 0 in eval / little pre-period history). Treat with caution. Publisher branded-PPC overall panel skipped for the same reason (only one site scalable).

### Interpretation (Phase 2b)

1. **Equal-weight overall** softens the publisher SEO story (near flat) and still shows advertiser SEO under-performing (~−12%).
2. **Purchases remain resilient** at overall level (+3.6% normalised; Site 6 strongly positive) even where Direct/SEO are soft.
3. **Non-brand PPC** is weak overall (−7% publishers, −27% advertisers) but highly site-heterogeneous (Site 4/6 up; Site 5 collapsed).
4. Site-specific models can diverge from pooled panels; use both views — volume-weighted for business impact, normalised/site for cross-site fairness.

Outputs: `research/analysis/outputs/site_counterfactuals/`

---

## Cross-cutting findings

| Theme | Evidence |
| --- | --- |
| Publisher SEO | Strong **above** expected in volume-weighted panel (+14%); **near flat / slightly down** when equal-weighted (−2%); all sites ≤ 0 on site-specific SEO. |
| Advertiser SEO | Consistently **below** expected (panel −16%; overall −12%; all three sites −16% to −36%). |
| Non-brand PPC | Broadly **under** demand + intensity baselines; large site dispersion. |
| Advertiser Direct | Soft overall (−9% / −9%); Site 6 is the exception (+15%). |
| Purchases | **Above** expected overall (+15% panel; +4% normalised) — quality/intent may be holding up. |
| Spend | £ spend still missing; lagged PPC sessions are a proxy only. |

---

## Reproduce

```bash
# Segment (volume-weighted) panels
python research/analysis/run_segment_counterfactuals.py

# Site-level + normalised overall (anonymised labels)
python research/analysis/run_site_counterfactuals.py
```
