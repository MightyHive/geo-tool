# Skill: AI Visibility — Prompt Performance Score

Use this skill to calculate the **AI Visibility score** from live prompt probe data. This score measures how visible a brand actually is in AI-generated responses, not just how technically prepared the site is.

> **This score replaces the previous AI Visibility category**, which has been renamed **Technical GEO Setup** and now covers citability, platform readiness, crawler access, and indexability. The new AI Visibility score is entirely probe-driven.

---

## Purpose

Answer: *Is the brand being seen by real users of AI search tools?*

This is the definitive answer to whether the GEO work is paying off. A brand could have perfect technical setup and excellent content quality yet still not appear in AI-generated responses. This score surfaces that reality.

---

## Score formula

```
AI Visibility = 0.60 × Visibility%  +  0.40 × SOV%
```

Where:

- **Visibility %** = (number of prompts where the brand is mentioned in at least one platform response) ÷ (total prompts probed) × 100  
- **SOV %** (Share of Voice) = brand mention hits ÷ (brand mention hits + all competitor mention hits) × 100

Both components are aggregated across all active platforms (Gemini, OpenAI, Google AI Overviews).

Score is clamped to 0–100 and rounded to the nearest integer.

---

## Band interpretation

| Score | Label | Interpretation |
|-------|-------|---------------|
| 90–100 | Excellent | Brand dominates AI-generated answers; consistently cited across platforms |
| 75–89 | Good | Brand appears in most relevant AI responses; minor blind spots remain |
| 60–74 | OK | Brand visible in some responses but inconsistently; competitors gaining |
| 40–59 | Weak | Brand rarely mentioned by AI tools; significant visibility gaps |
| 0–39 | Poor | Brand essentially absent from AI-generated answers |

---

## Inputs required

1. `prompt_performance_live_probe.json` — output of the probe pipeline  
   - `per_prompt[].mention_scores_{platform}.brand_signal` — numeric hit count per prompt per platform  
   - `per_prompt[].{platform}_response` — raw text used for fallback keyword matching  
   - `brand_match_tokens` — normalised brand tokens for matching  

2. Active platforms: `gemini`, `openai`, `google_aio` (Claude excluded from primary visibility calculation due to lower consumer market share but can be added as a supplementary signal)

---

## Scope boundaries

- **In scope**: prompt-level brand mention detection, SOV against detected competitors, cross-platform aggregation  
- **Out of scope**: technical citability signals (those belong in Technical GEO Setup), content quality, domain authority  
- **No probes = null score**: a null score is displayed when no probe data is available; it does not default to zero to avoid misleading clients

---

## Per-platform breakdown

In addition to the combined score, per-platform visibility should be reported:

| Metric | Per-platform visibility % | Per-platform SOV % |
|--------|--------------------------|-------------------|
| Gemini | brand mentions / total Gemini probes | brand hits / all hits on Gemini |
| OpenAI | brand mentions / total OpenAI probes | brand hits / all hits on OpenAI |
| Google AIO | brand mentions / total AIO probes | brand hits / all hits on AIO |

---

## Reporting

The AI Visibility score should be displayed:
1. As a **pillar card** in the Summary section with a gauge, label, and tooltip explaining the formula  
2. In the **AI Visibility Overview** section alongside Sentiment, Position, and SOV scorecards  
3. In the **Platform Readiness** section as the primary probe input for each platform's composite score  

---

## Improvement levers

| Gap | Recommended action |
|-----|--------------------|
| Low Visibility % | Run prompts that match the brand's actual use cases; improve content topical authority |
| Low SOV % | Reduce competitor dominance by building brand entity signals; create differentiated answer-optimised pages |
| Platform-specific absence | Check robots.txt allows that platform's crawler; review per-platform action items |
| No probe data | Run the Prompts section to kick off probes |
