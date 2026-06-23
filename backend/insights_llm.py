"""
Gemini-generated narrative insights for GA4 AI traffic and prompt reply sentiment.

Uses the same ``google.genai`` client as :mod:`geo_setup_llm` / :mod:`prompt_suggest``.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from geo_setup_llm import build_genai_client

GEMINI_MODEL = (os.environ.get("GEMINI_INSIGHTS_MODEL") or "gemini-3.5-flash").strip()
MAX_OUTPUT_TOKENS = 4096


class Ga4InsightsResponse(BaseModel):
    headline: str = Field(description="Short headline (max ~12 words) for the GA4 AI traffic section")
    summary: str = Field(description="2–4 sentences summarising the most important patterns in the data")
    key_insights: list[str] = Field(
        description="3–5 concise bullet points with specific numbers or trends where possible",
        min_length=2,
        max_length=6,
    )


class CategorySentimentRow(BaseModel):
    category: str = Field(description="Product or service category label")
    sentiment: str = Field(
        description="One of: Positive, Mixed, Neutral, Negative — tone toward the brand in AI replies"
    )
    summary: str = Field(description="1–2 sentences on how assistants talk about the brand in this category")


class PromptSentimentResponse(BaseModel):
    overall_sentiment: str = Field(
        description="One of: Positive, Mixed, Neutral, Negative — overall tone toward the brand"
    )
    overall_summary: str = Field(description="2–4 sentences on brand sentiment across all probed replies")
    by_category: list[CategorySentimentRow] = Field(
        description="One row per prompt category supplied",
        min_length=0,
        max_length=12,
    )


def _generation_config(*, response_schema: type[BaseModel]) -> Any:
    from google.genai import types

    return types.GenerateContentConfig(
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.4,
        top_p=0.9,
        response_mime_type="application/json",
        response_schema=response_schema,
    )


def _generate_structured(prompt: str, schema: type[BaseModel], *, model: str | None = None) -> BaseModel:
    client = build_genai_client()
    mid = (model or GEMINI_MODEL).strip()
    cfg = _generation_config(response_schema=schema)
    resp = client.models.generate_content(model=mid, contents=prompt, config=cfg)
    text = (resp.text or "").strip()
    if not text:
        raise ValueError("Empty Gemini response")
    data = json.loads(text)
    return schema.model_validate(data)


def ga4_digest_for_llm(ga4: dict[str, Any]) -> dict[str, Any]:
    """Compact GA4 export for the model (drops huge raw tables)."""
    gaps = ga4.get("source_medium_gaps") or ga4.get("ai_source_medium_gaps") or []
    if isinstance(gaps, list):
        gaps = sorted(
            [g for g in gaps if isinstance(g, dict)],
            key=lambda g: int(g.get("sessions") or g.get("session_count") or 0),
            reverse=True,
        )[:12]
    by_src = ga4.get("monthly_ai_sessions_by_source")
    by_src_trim: dict[str, Any] = {}
    if isinstance(by_src, dict):
        by_src_trim = {
            "mode": by_src.get("mode"),
            "source_order": (by_src.get("source_order") or [])[:14],
            "months": (by_src.get("months") or [])[-6:],
        }
    return {
        "has_ai_channel": ga4.get("has_ai_channel"),
        "ai_channel_names": ga4.get("ai_channel_names"),
        "weekly_channel_dimension": ga4.get("weekly_channel_dimension"),
        "monthly_sessions": (ga4.get("monthly_sessions") or ga4.get("weekly") or [])[-14:],
        "monthly_ai_revenue_pct": (ga4.get("monthly_ai_revenue_pct") or [])[-14:],
        "conversion_rate": ga4.get("conversion_rate"),
        "monthly_ai_sessions_by_source": by_src_trim,
        "source_medium_gaps": gaps,
        "notes": ga4.get("notes"),
    }


def generate_ga4_insights(
    ga4: dict[str, Any],
    *,
    brand_name: str,
    site_url: str,
    model: str | None = None,
) -> Ga4InsightsResponse:
    digest = ga4_digest_for_llm(ga4)
    payload = json.dumps(digest, ensure_ascii=False, indent=2)
    brand = (brand_name or "the brand").strip() or "the brand"
    site = (site_url or "").strip() or "the site"
    prompt = f"""
You are a digital analytics consultant summarising **AI-related traffic in Google Analytics 4** for a GEO audit report.

Brand: **{brand}**
Website: **{site}**

The JSON below is from ``ga4_traffic.json`` (sessions by month, optional AI revenue share, conversion rates, channel gaps, AI-by-source breakdown). 
Interpret it for a marketing lead — plain English, no jargon dumps. If an AI channel is configured, treat ``ai_sessions`` as traffic in that bucket; otherwise AI-like referrers may be partial.

Rules:
- Ground every claim in the JSON (cite trends, rough % or counts when useful).
- Note growth or decline in AI sessions vs total sessions where visible.
- Mention conversion rate comparison (all channels vs AI) if present.
- If ``source_medium_gaps`` lists sources, explain they may be mis-bucketed AI referrers (one sentence).
- Do not invent data not in the JSON.
- UK English spelling.

GA4 data:
{payload}
""".strip()
    return _generate_structured(prompt, Ga4InsightsResponse, model=model)  # type: ignore[return-value]


def _bundle_replies_for_sentiment(
    pss_rows: list[dict[str, Any]],
    per_prompt: list[dict[str, Any]],
) -> str:
    flat: list[str] = []
    meta: list[str] = []
    for r in pss_rows:
        pos = str(r.get("product_or_service") or "").strip() or "General"
        for p in r.get("prompts") or []:
            s = str(p).strip()
            if s:
                flat.append(s)
                meta.append(pos)
    chunks: list[str] = []
    for i, pq in enumerate(flat):
        if i >= len(per_prompt):
            break
        row = per_prompt[i]
        if not isinstance(row, dict):
            continue
        cat = meta[i]
        g = str(row.get("gemini_response") or "").strip()[:5000]
        o = str(row.get("openai_response") or "").strip()[:5000]
        chunks.append(
            f"=== CATEGORY: {cat} ===\nPROMPT: {pq[:600]}\n--- GEMINI ---\n{g}\n--- OPENAI ---\n{o}\n"
        )
    blob = "\n".join(chunks)
    return blob[:48000] if len(blob) > 48000 else blob


def _resolve_probed_rows(pss_rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = pss_rows or []
    if not rows:
        return []
    from api.prompt_selection import select_prompts_for_probing

    _, probed_rows = select_prompts_for_probing(rows)
    return probed_rows


def filter_sentiment_for_probed_rows(
    sentiment: PromptSentimentResponse,
    probed_rows: list[dict[str, Any]],
) -> PromptSentimentResponse:
    """Keep only categories that were actually probed (drops empty Custom prompts)."""
    from api.prompt_selection import probed_category_labels

    allowed = probed_category_labels(probed_rows)
    if not allowed:
        return sentiment
    filtered = [row for row in sentiment.by_category if row.category.strip() in allowed]
    return sentiment.model_copy(update={"by_category": filtered})


def generate_prompt_sentiment(
    live_probe: dict[str, Any],
    *,
    brand_name: str,
    site_url: str,
    pss_rows: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> PromptSentimentResponse:
    per = live_probe.get("per_prompt") or []
    if not isinstance(per, list) or not per:
        raise ValueError("No live probe replies to analyse")
    per_clean = [p for p in per if isinstance(p, dict)]
    probed_rows = _resolve_probed_rows(pss_rows)
    rows_for_bundle = probed_rows
    bundled = _bundle_replies_for_sentiment(rows_for_bundle, per_clean)
    from api.prompt_selection import probed_category_labels

    categories = sorted(probed_category_labels(probed_rows)) if probed_rows else []
    cat_line = ", ".join(categories) if categories else "(single bucket — no product categories)"
    brand = (brand_name or "the brand").strip() or "the brand"
    site = (site_url or "").strip() or "the site"
    prompt = f"""
You analyse **sentiment toward a brand** in AI assistant replies (Gemini and OpenAI) from a live GEO probe.

Brand to judge sentiment **toward**: **{brand}** (site: **{site}**)

Categories to cover in ``by_category`` (use these exact labels only): {cat_line}

For each category, read the bundled replies below. Judge how favourably, neutrally, or critically the assistants portray **{brand}** (recommendations, trust, warnings, omissions vs competitors).

Sentiment labels must be exactly one of: **Positive**, **Mixed**, **Neutral**, **Negative**.

Rules:
- Base judgment only on the reply text — not on idealised brand reputation.
- ``overall_sentiment`` synthesises all replies; ``by_category`` must include exactly the categories listed above — no extra rows.
- Do not invent categories (e.g. do not add "Custom prompts" unless it is listed above).
- Be specific (mention praise, caveats, competitor preference, or absence of brand).
- UK English.

Bundled prompts and replies:
{bundled}
""".strip()
    raw = _generate_structured(prompt, PromptSentimentResponse, model=model)  # type: ignore[assignment]
    return filter_sentiment_for_probed_rows(raw, probed_rows)


GA4_INSIGHTS_FILE = "ga4_ai_insights.json"
SENTIMENT_FILE = "prompt_performance_sentiment.json"


def _file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime if path.is_file() else None
    except OSError:
        return None


def load_cached_ga4_insights(audit_dir: Path, ga4_path: Path) -> dict[str, Any] | None:
    cache = audit_dir / GA4_INSIGHTS_FILE
    if not cache.is_file() or not ga4_path.is_file():
        return None
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    src_mtime = _file_mtime(ga4_path)
    if src_mtime is None:
        return None
    if float(raw.get("source_mtime") or 0) != src_mtime:
        return None
    return raw


def save_ga4_insights_cache(audit_dir: Path, ga4_path: Path, insights: Ga4InsightsResponse) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    out = audit_dir / GA4_INSIGHTS_FILE
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_mtime": _file_mtime(ga4_path),
        "insights": insights.model_dump(),
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def load_or_generate_ga4_insights(
    audit_dir: Path,
    ga4: dict[str, Any],
    *,
    brand_name: str,
    site_url: str,
) -> Ga4InsightsResponse | None:
    ga4_path = audit_dir / "ga4_traffic.json"
    cached = load_cached_ga4_insights(audit_dir, ga4_path)
    if cached and isinstance(cached.get("insights"), dict):
        try:
            return Ga4InsightsResponse.model_validate(cached["insights"])
        except Exception:
            pass
    try:
        insights = generate_ga4_insights(ga4, brand_name=brand_name, site_url=site_url)
        if ga4_path.is_file():
            save_ga4_insights_cache(audit_dir, ga4_path, insights)
        return insights
    except Exception:
        return None


def load_cached_sentiment(audit_dir: Path, probe_path: Path) -> dict[str, Any] | None:
    cache = audit_dir / SENTIMENT_FILE
    if not cache.is_file() or not probe_path.is_file():
        return None
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    src_mtime = _file_mtime(probe_path)
    if src_mtime is None or float(raw.get("source_mtime") or 0) != src_mtime:
        return None
    return raw


def save_sentiment_cache(audit_dir: Path, probe_path: Path, sentiment: PromptSentimentResponse) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    out = audit_dir / SENTIMENT_FILE
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_mtime": _file_mtime(probe_path),
        "sentiment": sentiment.model_dump(),
    }
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def load_or_generate_prompt_sentiment(
    audit_dir: Path,
    live_probe: dict[str, Any],
    *,
    brand_name: str,
    site_url: str,
    pss_rows: list[dict[str, Any]] | None = None,
) -> tuple[PromptSentimentResponse | None, str | None]:
    """Return (sentiment, error_message)."""
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    probed_rows = _resolve_probed_rows(pss_rows)
    cached = load_cached_sentiment(audit_dir, probe_path)
    if cached and isinstance(cached.get("sentiment"), dict):
        try:
            sent = PromptSentimentResponse.model_validate(cached["sentiment"])
            return filter_sentiment_for_probed_rows(sent, probed_rows), None
        except Exception:
            pass
    try:
        sentiment = generate_prompt_sentiment(
            live_probe,
            brand_name=brand_name,
            site_url=site_url,
            pss_rows=pss_rows,
        )
        if probe_path.is_file():
            save_sentiment_cache(audit_dir, probe_path, sentiment)
        return sentiment, None
    except Exception as exc:
        return None, str(exc)
