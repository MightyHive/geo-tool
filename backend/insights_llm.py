"""
Gemini-generated narrative insights for GA4 AI traffic and prompt reply sentiment.

Uses the same ``google.genai`` client as :mod:`geo_setup_llm` / :mod:`prompt_suggest``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from geo_setup_llm import build_genai_client

log = logging.getLogger(__name__)

GEMINI_MODEL = (os.environ.get("GEMINI_INSIGHTS_MODEL") or "gemini-3.5-flash").strip()
# Used when the primary model rejects the request (schema / availability).
GEMINI_FALLBACK_MODEL = (os.environ.get("GEMINI_INSIGHTS_FALLBACK_MODEL") or "gemini-2.5-flash").strip()
MAX_OUTPUT_TOKENS = 8192
# Cap per-platform reply snippets so multi-locale bundles stay within context.
_REPLY_SNIPPET_CHARS = 1500
_BUNDLE_MAX_CHARS = 48_000
# Overview must stay small: stuffing all replies (e.g. 280 prompts / ~50k chars)
# yields truncated invalid JSON even on HTTP 200.
_OVERVIEW_SAMPLE_SIZE = 24
_OVERVIEW_BUNDLE_MAX_CHARS = 16_000
# Gemini constrained decoding rejects nested array maxItems that are too large
# ("constraint that has too many states"). Keep chunks small; merge locally.
_BY_PROMPT_CHUNK_SIZE = 30
# Soft ceiling used only for request-building validation / dry-run tests.
_MAX_SERVABLE_BY_PROMPT_MAX_ITEMS = 40
# Retries per model for empty / invalid JSON (same model, then fallback).
_STRUCTURED_PARSE_ATTEMPTS = 2
_BAD_PAYLOAD_LOG_CHARS = 400


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


class PerPromptSentimentRow(BaseModel):
    prompt_id: str = Field(description="Exact PROMPT_ID from the bundled replies")
    sentiment: str = Field(
        description="One of: Positive, Mixed, Neutral, Negative — tone toward the brand for this prompt"
    )
    summary: str = Field(
        description="One short sentence (≤20 words) on how assistants portray the brand for this prompt"
    )


class PromptSentimentOverviewResponse(BaseModel):
    """Overview-only schema (no by_prompt) — keeps structured output small for large audits."""

    overall_sentiment: str = Field(
        description="One of: Positive, Mixed, Neutral, Negative — overall tone toward the brand"
    )
    overall_summary: str = Field(description="2–4 sentences on brand sentiment across all probed replies")
    by_category: list[CategorySentimentRow] = Field(
        description="One row per prompt category supplied",
        min_length=0,
        max_length=12,
    )


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
    # Do NOT set a large max_length here: Gemini returns INVALID_ARGUMENT when nested
    # array maxItems is too high (e.g. 120). Completeness is enforced in Python via
    # ensure_by_prompt_coverage after the call.
    by_prompt: list[PerPromptSentimentRow] = Field(
        description="One row per PROMPT_ID supplied in the bundled replies",
        min_length=0,
        default_factory=list,
    )


class ByPromptBatchResponse(BaseModel):
    """Schema for chunked by_prompt calls (multi-locale / large probes)."""

    by_prompt: list[PerPromptSentimentRow] = Field(
        description="One row per PROMPT_ID supplied in this chunk",
        min_length=0,
        default_factory=list,
    )


def sentiment_schema_by_prompt_max_items() -> int | None:
    """Return JSON-schema maxItems for by_prompt, or None if unbounded."""
    schema = PromptSentimentResponse.model_json_schema()
    props = schema.get("properties") or {}
    by_prompt = props.get("by_prompt") if isinstance(props, dict) else None
    if not isinstance(by_prompt, dict):
        return None
    raw = by_prompt.get("maxItems")
    return int(raw) if raw is not None else None


def assert_sentiment_schema_servable() -> None:
    """Raise if the Gemini response schema is likely to trigger INVALID_ARGUMENT."""
    max_items = sentiment_schema_by_prompt_max_items()
    if max_items is not None and max_items > _MAX_SERVABLE_BY_PROMPT_MAX_ITEMS:
        raise ValueError(
            f"PromptSentimentResponse.by_prompt maxItems={max_items} exceeds Gemini "
            f"servable limit (~{_MAX_SERVABLE_BY_PROMPT_MAX_ITEMS}); remove max_length "
            "or chunk by_prompt instead"
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


def _strip_markdown_json_fence(raw: str) -> str:
    """Remove optional ``` / ```json fences around model output."""
    t = (raw or "").strip()
    if not t.startswith("```"):
        return t
    t = re.sub(r"^```[a-zA-Z0-9_+-]*\s*", "", t)
    t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _extract_json_text(raw: str) -> str:
    """Best-effort extract of a JSON object/array from mixed model text."""
    t = _strip_markdown_json_fence(raw)
    if not t:
        return t
    if t[0] in "{[":
        return t
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start < 0:
            continue
        end = t.rfind(closer)
        if end > start:
            return t[start : end + 1]
    return t


def _loads_json_lenient(raw: str) -> Any:
    """Parse JSON after fence-strip / object extraction. Raises JSONDecodeError."""
    text = _extract_json_text(raw)
    if not (text or "").strip():
        raise ValueError("Empty JSON payload after extraction")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Trailing prose after a complete object/array.
        stripped = text.strip()
        if stripped and stripped[0] in "{[":
            closer = "}" if stripped[0] == "{" else "]"
            end = stripped.rfind(closer)
            if end > 0:
                try:
                    return json.loads(stripped[: end + 1])
                except json.JSONDecodeError:
                    pass
        raise


def _snippet_for_log(raw: str, *, limit: int = _BAD_PAYLOAD_LOG_CHARS) -> str:
    t = (raw or "").replace("\n", "\\n")
    if len(t) <= limit:
        return t
    head = limit // 2
    tail = limit - head
    return f"{t[:head]}…({len(t)} chars)…{t[-tail:]}"


def _response_finish_reason(resp: Any) -> str:
    try:
        candidates = getattr(resp, "candidates", None) or []
        if not candidates:
            return ""
        fr = getattr(candidates[0], "finish_reason", None)
        return str(fr) if fr is not None else ""
    except Exception:
        return ""


def _coerce_parsed_to_schema(parsed: Any, schema: type[BaseModel]) -> BaseModel | None:
    """Use SDK structured ``resp.parsed`` when present."""
    if parsed is None:
        return None
    if isinstance(parsed, schema):
        return parsed
    if isinstance(parsed, BaseModel):
        try:
            return schema.model_validate(parsed.model_dump())
        except Exception:
            return None
    if isinstance(parsed, dict):
        try:
            return schema.model_validate(parsed)
        except Exception:
            return None
    return None


def _is_retryable_gemini_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    markers = (
        "invalid_argument",
        "not_found",
        "is no longer available",
        "too many states",
        "404",
        "400",
    )
    return any(m in text for m in markers)


def _is_retryable_parse_error(exc: BaseException) -> bool:
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        msg = str(exc).lower()
        return (
            isinstance(exc, json.JSONDecodeError)
            or "empty gemini response" in msg
            or "empty json payload" in msg
            or "expecting value" in msg
        )
    return False


def _model_candidates(model: str | None = None) -> list[str]:
    primary = (model or GEMINI_MODEL).strip()
    fallback = GEMINI_FALLBACK_MODEL.strip()
    out: list[str] = []
    for mid in (primary, fallback):
        if mid and mid not in out:
            out.append(mid)
    return out or [GEMINI_MODEL]


def _generate_structured(prompt: str, schema: type[BaseModel], *, model: str | None = None) -> BaseModel:
    if not (prompt or "").strip():
        raise ValueError("Empty Gemini prompt contents")
    client = build_genai_client()
    cfg = _generation_config(response_schema=schema)
    last_exc: BaseException | None = None
    schema_name = getattr(schema, "__name__", str(schema))
    for mid in _model_candidates(model):
        for attempt in range(1, _STRUCTURED_PARSE_ATTEMPTS + 1):
            try:
                log.info(
                    "Gemini structured call model=%s schema=%s prompt_chars=%s attempt=%s",
                    mid,
                    schema_name,
                    len(prompt),
                    attempt,
                )
                resp = client.models.generate_content(model=mid, contents=prompt, config=cfg)
                coerced = _coerce_parsed_to_schema(getattr(resp, "parsed", None), schema)
                if coerced is not None:
                    return coerced
                text = (resp.text or "").strip()
                if not text:
                    raise ValueError(f"Empty Gemini response from model={mid}")
                try:
                    data = _loads_json_lenient(text)
                except (json.JSONDecodeError, ValueError) as parse_exc:
                    fr = _response_finish_reason(resp)
                    log.warning(
                        "Gemini structured JSON parse failed model=%s schema=%s finish_reason=%s "
                        "payload_snippet=%s err=%s",
                        mid,
                        schema_name,
                        fr or "unknown",
                        _snippet_for_log(text),
                        parse_exc,
                    )
                    raise
                return schema.model_validate(data)
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "Gemini structured call failed model=%s schema=%s attempt=%s: %s",
                    mid,
                    schema_name,
                    attempt,
                    exc,
                )
                if _is_retryable_parse_error(exc) or _is_retryable_gemini_error(exc):
                    continue
                raise
    assert last_exc is not None
    raise last_exc


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


def _prompt_id_for_row(row: dict[str, Any], index: int) -> str:
    from api.prompt_performance_metrics import prompt_id_for

    existing = str(row.get("prompt_id") or "").strip()
    if existing:
        return existing
    locale_key = str(row.get("_locale_key") or "").strip()
    return prompt_id_for(int(row.get("index") if row.get("index") is not None else index), str(row.get("prompt") or ""), locale_key)


def _category_for_prompt_text(
    prompt_text: str,
    pss_rows: list[dict[str, Any]],
) -> str:
    needle = prompt_text.strip().lower()
    if not needle:
        return "General"
    for r in pss_rows:
        pos = str(r.get("product_or_service") or "").strip() or "General"
        for p in r.get("prompts") or []:
            if str(p).strip().lower() == needle:
                return pos
    return "General"


def _normalize_per_prompt_rows(per_prompt: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, row in enumerate(per_prompt):
        if not isinstance(row, dict):
            continue
        cleaned = dict(row)
        cleaned["prompt_id"] = _prompt_id_for_row(cleaned, i)
        if cleaned.get("index") is None:
            cleaned["index"] = i
        out.append(cleaned)
    return out


def collect_per_prompt_from_probe_file(saved: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Flatten per-prompt rows across locale probes (preferred) or default live_probe."""
    if not isinstance(saved, dict):
        return []
    locales = saved.get("locale_probes")
    collected: list[dict[str, Any]] = []
    if isinstance(locales, dict) and locales:
        for key, entry in locales.items():
            if not isinstance(entry, dict):
                continue
            live = entry.get("live_probe")
            if not isinstance(live, dict):
                continue
            for i, row in enumerate(live.get("per_prompt") or []):
                if not isinstance(row, dict):
                    continue
                item = dict(row)
                item.setdefault("_locale_key", str(key))
                item["prompt_id"] = _prompt_id_for_row(item, int(item.get("index") if item.get("index") is not None else i))
                collected.append(item)
        if collected:
            return collected
    live = saved.get("live_probe")
    if isinstance(live, dict):
        return _normalize_per_prompt_rows([p for p in (live.get("per_prompt") or []) if isinstance(p, dict)])
    return []


def _bundle_replies_for_sentiment(
    pss_rows: list[dict[str, Any]],
    per_prompt: list[dict[str, Any]],
    *,
    index_offset: int = 0,
    max_chars: int | None = None,
) -> str:
    cap = _BUNDLE_MAX_CHARS if max_chars is None else max(1, int(max_chars))
    chunks: list[str] = []
    for i, row in enumerate(per_prompt):
        if not isinstance(row, dict):
            continue
        pq = str(row.get("prompt") or "").strip()
        cat = _category_for_prompt_text(pq, pss_rows) if pss_rows else "General"
        abs_i = index_offset + i
        pid = _prompt_id_for_row(row, abs_i)
        g = str(row.get("gemini_response") or "").strip()[:_REPLY_SNIPPET_CHARS]
        o = str(row.get("openai_response") or "").strip()[:_REPLY_SNIPPET_CHARS]
        c = str(row.get("claude_response") or "").strip()[:_REPLY_SNIPPET_CHARS]
        chunks.append(
            f"=== PROMPT_ID: {pid} | INDEX: {abs_i} | CATEGORY: {cat} ===\n"
            f"PROMPT: {pq[:600]}\n--- GEMINI ---\n{g}\n--- OPENAI ---\n{o}\n--- CLAUDE ---\n{c}\n"
        )
    blob = "\n".join(chunks)
    return blob[:cap] if len(blob) > cap else blob


def _chunk_rows(rows: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    size = max(1, int(chunk_size))
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _sample_rows_for_overview(
    rows: list[dict[str, Any]],
    *,
    sample_size: int = _OVERVIEW_SAMPLE_SIZE,
) -> list[dict[str, Any]]:
    """Evenly spaced sample so large audits do not stuff every reply into overview."""
    if not rows:
        return []
    n = max(1, int(sample_size))
    if len(rows) <= n:
        return list(rows)
    # Inclusive endpoints via linspace-style indices.
    if n == 1:
        return [rows[0]]
    step = (len(rows) - 1) / (n - 1)
    indices = sorted({int(round(i * step)) for i in range(n)})
    return [rows[i] for i in indices]


def estimate_sentiment_request_chars(
    pss_rows: list[dict[str, Any]],
    per_prompt: list[dict[str, Any]],
) -> dict[str, int]:
    """Dry-run sizing for sentiment request building (no API call)."""
    sample = _sample_rows_for_overview(per_prompt)
    overview_bundled = _bundle_replies_for_sentiment(
        pss_rows, sample, max_chars=_OVERVIEW_BUNDLE_MAX_CHARS
    )
    bundled = _bundle_replies_for_sentiment(pss_rows, per_prompt)
    return {
        "prompt_count": len(per_prompt),
        "bundle_chars": len(bundled),
        "bundle_max_chars": _BUNDLE_MAX_CHARS,
        "overview_sample_size": len(sample),
        "overview_bundle_chars": len(overview_bundled),
        "overview_bundle_max_chars": _OVERVIEW_BUNDLE_MAX_CHARS,
        "by_prompt_chunk_size": _BY_PROMPT_CHUNK_SIZE,
        "chunk_count": max(1, (len(per_prompt) + _BY_PROMPT_CHUNK_SIZE - 1) // _BY_PROMPT_CHUNK_SIZE)
        if per_prompt
        else 0,
    }


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


def _row_brand_hits(row: dict[str, Any]) -> int:
    total = 0
    for platform in ("gemini", "openai", "claude", "google_aio"):
        scores = row.get(f"mention_scores_{platform}") or {}
        if isinstance(scores, dict):
            total += int(scores.get("brand_signal") or 0)
    return total


def _category_brand_hits(
    probed_rows: list[dict[str, Any]],
    per_prompt: list[dict[str, Any]],
) -> dict[str, int]:
    """Total brand_signal per probed category across all platforms."""
    from api.prompt_selection import probed_category_labels

    labels = probed_category_labels(probed_rows)
    if not labels:
        return {}
    hits = {label: 0 for label in labels}
    for row in per_prompt:
        if not isinstance(row, dict):
            continue
        cat = _category_for_prompt_text(str(row.get("prompt") or ""), probed_rows)
        if cat not in hits:
            continue
        hits[cat] = hits.get(cat, 0) + _row_brand_hits(row)
    return hits


def ensure_by_prompt_coverage(
    sentiment: PromptSentimentResponse,
    per_prompt: list[dict[str, Any]],
) -> PromptSentimentResponse:
    """Guarantee one by_prompt row per probe prompt_id (fill Neutral gaps)."""
    by_id = {row.prompt_id.strip(): row for row in sentiment.by_prompt if row.prompt_id.strip()}
    filled: list[PerPromptSentimentRow] = []
    seen: set[str] = set()
    for i, row in enumerate(per_prompt):
        if not isinstance(row, dict):
            continue
        pid = _prompt_id_for_row(row, i)
        if pid in seen:
            continue
        seen.add(pid)
        existing = by_id.get(pid)
        if existing:
            filled.append(existing)
        else:
            filled.append(
                PerPromptSentimentRow(
                    prompt_id=pid,
                    sentiment="Neutral",
                    summary="No qualitative sentiment returned for this prompt.",
                )
            )
    return sentiment.model_copy(update={"by_prompt": filled})


def ground_sentiment_on_mentions(
    sentiment: PromptSentimentResponse,
    *,
    probed_rows: list[dict[str, Any]],
    per_prompt: list[dict[str, Any]],
) -> PromptSentimentResponse:
    """Align LLM sentiment with mention-based brand visibility."""
    per_clean = _normalize_per_prompt_rows([p for p in per_prompt if isinstance(p, dict)])
    category_hits = _category_brand_hits(probed_rows, per_clean)
    total_hits = sum(category_hits.values())
    if total_hits <= 0:
        note = (
            "No brand mentions were detected in live probe replies using flexible brand-name matching "
            "(including hyphen/spacing variants and product-line aliases)."
        )
        by_category = [
            row.model_copy(
                update={
                    "sentiment": "Neutral",
                    "summary": (
                        f"{note} {row.summary}"
                        if row.sentiment != "Neutral"
                        else (row.summary or note)
                    ),
                }
            )
            for row in sentiment.by_category
        ]
        by_prompt = [
            PerPromptSentimentRow(
                prompt_id=_prompt_id_for_row(row, i),
                sentiment="Neutral",
                summary=note,
            )
            for i, row in enumerate(per_clean)
        ]
        return sentiment.model_copy(
            update={
                "overall_sentiment": "Neutral",
                "overall_summary": (
                    f"{note} Assistant replies may discuss the category without naming the brand."
                ),
                "by_category": by_category,
                "by_prompt": by_prompt,
            }
        )

    updated_rows: list[CategorySentimentRow] = []
    for row in sentiment.by_category:
        hits = int(category_hits.get(row.category.strip(), 0))
        if hits > 0:
            updated_rows.append(row)
            continue
        prefix = "Brand not mentioned in probe replies for this category (mention-based check). "
        sentiment_label = row.sentiment
        if sentiment_label in ("Positive", "Mixed"):
            sentiment_label = "Neutral"
        updated_rows.append(
            row.model_copy(
                update={
                    "sentiment": sentiment_label,
                    "summary": prefix + (row.summary or ""),
                }
            )
        )

    by_id = {row.prompt_id.strip(): row for row in sentiment.by_prompt if row.prompt_id.strip()}
    updated_prompts: list[PerPromptSentimentRow] = []
    for i, row in enumerate(per_clean):
        pid = _prompt_id_for_row(row, i)
        existing = by_id.get(pid)
        hits = _row_brand_hits(row)
        if hits <= 0:
            updated_prompts.append(
                PerPromptSentimentRow(
                    prompt_id=pid,
                    sentiment="Neutral",
                    summary=(
                        "Brand not mentioned in probe replies for this prompt (mention-based check)."
                        + (f" {existing.summary}" if existing and existing.summary else "")
                    ).strip(),
                )
            )
            continue
        if existing:
            label = existing.sentiment
            if label not in ("Positive", "Mixed", "Neutral", "Negative"):
                label = "Neutral"
            updated_prompts.append(existing.model_copy(update={"sentiment": label}))
        else:
            updated_prompts.append(
                PerPromptSentimentRow(
                    prompt_id=pid,
                    sentiment="Neutral",
                    summary="Brand mentioned; qualitative label unavailable.",
                )
            )
    return sentiment.model_copy(update={"by_category": updated_rows, "by_prompt": updated_prompts})


def _majority_sentiment(labels: list[str]) -> str:
    allowed = ("Positive", "Mixed", "Neutral", "Negative")
    counts: dict[str, int] = {k: 0 for k in allowed}
    for lab in labels:
        if lab in counts:
            counts[lab] += 1
    if not any(counts.values()):
        return "Neutral"
    # Prefer Mixed when Positive and Negative both present with similar weight.
    if counts["Positive"] and counts["Negative"] and counts["Mixed"] == 0:
        if abs(counts["Positive"] - counts["Negative"]) <= 1:
            return "Mixed"
    return max(allowed, key=lambda k: (counts[k], k == "Mixed"))


def _synthesize_overview_from_by_prompt(
    by_prompt_rows: list[PerPromptSentimentRow],
    *,
    categories: list[str],
    brand: str,
) -> PromptSentimentOverviewResponse:
    """Build a usable overview when the dedicated overview Gemini call fails."""
    labels = [r.sentiment for r in by_prompt_rows if r.sentiment]
    overall = _majority_sentiment(labels)
    n = len(by_prompt_rows)
    brand_s = (brand or "the brand").strip() or "the brand"
    summary = (
        f"Overview synthesised locally from {n} per-prompt sentiment label(s) after the "
        f"Gemini overview call failed or returned invalid JSON. Overall tone toward {brand_s} "
        f"is {overall} based on majority vote across prompt-level labels."
    )
    by_category = [
        CategorySentimentRow(
            category=cat,
            sentiment=overall,
            summary=f"Derived from per-prompt labels (overview call unavailable); overall {overall}.",
        )
        for cat in categories
    ]
    return PromptSentimentOverviewResponse(
        overall_sentiment=overall,
        overall_summary=summary,
        by_category=by_category,
    )


def generate_prompt_sentiment(
    live_probe: dict[str, Any],
    *,
    brand_name: str,
    site_url: str,
    pss_rows: list[dict[str, Any]] | None = None,
    per_prompt_override: list[dict[str, Any]] | None = None,
    model: str | None = None,
) -> PromptSentimentResponse:
    if per_prompt_override is not None:
        per_clean = _normalize_per_prompt_rows([p for p in per_prompt_override if isinstance(p, dict)])
    else:
        per = live_probe.get("per_prompt") or []
        if not isinstance(per, list) or not per:
            raise ValueError("No live probe replies to analyse")
        per_clean = _normalize_per_prompt_rows([p for p in per if isinstance(p, dict)])
    if not per_clean:
        raise ValueError("No live probe replies to analyse")
    assert_sentiment_schema_servable()
    probed_rows = _resolve_probed_rows(pss_rows)
    from api.prompt_selection import probed_category_labels

    categories = sorted(probed_category_labels(probed_rows)) if probed_rows else []
    cat_line = ", ".join(categories) if categories else "(single bucket — no product categories)"
    brand = (brand_name or "the brand").strip() or "the brand"
    site = (site_url or "").strip() or "the site"
    category_hits = _category_brand_hits(probed_rows, per_clean)
    hit_lines = ", ".join(f"{k}: {v} mention hit(s)" for k, v in sorted(category_hits.items())) or "none"

    chunks = _chunk_rows(per_clean, _BY_PROMPT_CHUNK_SIZE)
    sizing = estimate_sentiment_request_chars(probed_rows, per_clean)
    log.info(
        "Prompt sentiment generate brand=%s prompts=%s chunks=%s overview_sample=%s "
        "overview_bundle_chars=%s",
        brand,
        sizing["prompt_count"],
        sizing["chunk_count"],
        sizing["overview_sample_size"],
        sizing["overview_bundle_chars"],
    )

    # 1) Chunked by_prompt first so large audits still persist labels if overview fails.
    by_prompt_rows: list[PerPromptSentimentRow] = []
    chunk_failures: list[str] = []
    offset = 0
    for chunk_i, chunk in enumerate(chunks):
        bundled = _bundle_replies_for_sentiment(probed_rows, chunk, index_offset=offset)
        prompt_ids = [_prompt_id_for_row(row, offset + i) for i, row in enumerate(chunk)]
        offset += len(chunk)
        if not bundled.strip():
            chunk_failures.append(f"chunk {chunk_i}: empty bundle")
            continue
        batch_prompt = f"""
You analyse **sentiment toward a brand** in AI assistant replies for a subset of GEO probe prompts.

Brand to judge sentiment **toward**: **{brand}** (site: **{site}**)

PROMPT_IDs to cover in ``by_prompt`` (use these exact ids only): {", ".join(prompt_ids)}

Sentiment labels must be exactly one of: **Positive**, **Mixed**, **Neutral**, **Negative**.

Rules:
- Base judgment only on the reply text.
- If a prompt's replies do not mention the brand, that prompt's sentiment must be **Neutral**.
- ``by_prompt`` must include exactly one row per PROMPT_ID listed above.
- Keep each summary to one short sentence. UK English.

Bundled prompts and replies:
{bundled}
""".strip()
        try:
            batch = _generate_structured(batch_prompt, ByPromptBatchResponse, model=model)  # type: ignore[assignment]
            by_prompt_rows.extend(batch.by_prompt)
        except Exception as exc:
            chunk_failures.append(f"chunk {chunk_i}: {exc}")
            log.warning(
                "Prompt sentiment by_prompt chunk %s/%s failed (%s prompts): %s",
                chunk_i + 1,
                len(chunks),
                len(chunk),
                exc,
            )

    if len(chunks) > 1 or chunk_failures:
        log.info(
            "Prompt sentiment by_prompt merged=%s across %s chunks (failures=%s)",
            len(by_prompt_rows),
            len(chunks),
            len(chunk_failures),
        )

    # 2) Compact overview from a sample — never stuff all ~280 prompts into one call.
    sample_rows = _sample_rows_for_overview(per_clean)
    overview_bundle = _bundle_replies_for_sentiment(
        probed_rows,
        sample_rows,
        max_chars=_OVERVIEW_BUNDLE_MAX_CHARS,
    )
    overview: PromptSentimentOverviewResponse | None = None
    overview_error: str | None = None
    if overview_bundle.strip():
        overview_prompt = f"""
You analyse **sentiment toward a brand** in AI assistant replies (Gemini, OpenAI, Claude) from a live GEO probe.

Brand to judge sentiment **toward**: **{brand}** (site: **{site}**)

Categories to cover in ``by_category`` (use these exact labels only): {cat_line}

This bundle is a **representative sample** of {len(sample_rows)} of {len(per_clean)} probed prompts
(not the full set). Synthesise overall and per-category tone from the sample plus mention hits.

Mention-based brand hits already counted across **all** replies (flexible spelling + product-line aliases): {hit_lines}

Sentiment labels must be exactly one of: **Positive**, **Mixed**, **Neutral**, **Negative**.

Rules:
- Base judgment only on the reply text — not on idealised brand reputation.
- If mention hits for a category are **0**, that category sentiment must be **Neutral** and the summary must state the brand was not mentioned.
- ``overall_sentiment`` synthesises the sample; ``by_category`` must include exactly the categories listed above — no extra rows.
- Do not invent categories (e.g. do not add "Custom prompts" unless it is listed above).
- Do **not** return per-prompt rows — overall and by_category only.
- Be specific (mention praise, caveats, competitor preference, or absence of brand).
- UK English.

Sampled prompts and replies:
{overview_bundle}
""".strip()
        try:
            overview = _generate_structured(  # type: ignore[assignment]
                overview_prompt,
                PromptSentimentOverviewResponse,
                model=model,
            )
        except Exception as exc:
            overview_error = str(exc)
            log.warning("Prompt sentiment overview call failed: %s", exc)

    if overview is None:
        if by_prompt_rows:
            overview = _synthesize_overview_from_by_prompt(
                by_prompt_rows,
                categories=categories,
                brand=brand,
            )
            log.warning(
                "Using synthesised overview from %s by_prompt rows (overview_error=%s)",
                len(by_prompt_rows),
                overview_error or "empty_sample",
            )
        else:
            detail = overview_error or "; ".join(chunk_failures) or "no usable Gemini output"
            raise ValueError(
                f"Prompt sentiment failed for {len(per_clean)} prompts: {detail}"
            )

    raw = PromptSentimentResponse(
        overall_sentiment=overview.overall_sentiment,
        overall_summary=overview.overall_summary,
        by_category=overview.by_category,
        by_prompt=by_prompt_rows,
    )
    covered = ensure_by_prompt_coverage(raw, per_clean)
    grounded = ground_sentiment_on_mentions(
        covered,
        probed_rows=probed_rows,
        per_prompt=per_clean,
    )
    return filter_sentiment_for_probed_rows(grounded, probed_rows)


GA4_INSIGHTS_FILE = "ga4_ai_insights.json"
SENTIMENT_FILE = "prompt_performance_sentiment.json"
MENTION_RULES_VERSION = 2
# Schema v2 adds qualitative by_prompt rows (Gemini labels per prompt_id).
SENTIMENT_SCHEMA_VERSION = 2


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


def _cached_sentiment_is_current(raw: dict[str, Any], probe_path: Path) -> bool:
    src_mtime = _file_mtime(probe_path)
    if src_mtime is None or float(raw.get("source_mtime") or 0) != src_mtime:
        return False
    if int(raw.get("mention_rules_version") or 0) != MENTION_RULES_VERSION:
        return False
    if int(raw.get("schema_version") or 0) < SENTIMENT_SCHEMA_VERSION:
        return False
    sentiment = raw.get("sentiment")
    if not isinstance(sentiment, dict):
        return False
    # Require per-prompt qualitative rows (legacy caches only had overall/by_category).
    by_prompt = sentiment.get("by_prompt")
    return isinstance(by_prompt, list)


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
    if not _cached_sentiment_is_current(raw, probe_path):
        return None
    return raw


def save_sentiment_cache(audit_dir: Path, probe_path: Path, sentiment: PromptSentimentResponse) -> Path:
    audit_dir.mkdir(parents=True, exist_ok=True)
    out = audit_dir / SENTIMENT_FILE
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "source_mtime": _file_mtime(probe_path),
        "mention_rules_version": MENTION_RULES_VERSION,
        "schema_version": SENTIMENT_SCHEMA_VERSION,
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
    per_prompt_override: list[dict[str, Any]] | None = None,
    force: bool = False,
) -> tuple[PromptSentimentResponse | None, str | None]:
    """Return (sentiment, error_message)."""
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    probed_rows = _resolve_probed_rows(pss_rows)
    if per_prompt_override is not None:
        per_clean = _normalize_per_prompt_rows([p for p in per_prompt_override if isinstance(p, dict)])
    else:
        # Prefer all locale rows from the probe artifact when available.
        try:
            saved = json.loads(probe_path.read_text(encoding="utf-8")) if probe_path.is_file() else None
        except (OSError, json.JSONDecodeError):
            saved = None
        collected = collect_per_prompt_from_probe_file(saved if isinstance(saved, dict) else None)
        if collected:
            per_clean = collected
        else:
            per = live_probe.get("per_prompt") or []
            per_clean = _normalize_per_prompt_rows([p for p in per if isinstance(p, dict)])

    if not force:
        cached = load_cached_sentiment(audit_dir, probe_path)
        if cached and isinstance(cached.get("sentiment"), dict):
            try:
                sent = PromptSentimentResponse.model_validate(cached["sentiment"])
                sent = ground_sentiment_on_mentions(
                    filter_sentiment_for_probed_rows(sent, probed_rows),
                    probed_rows=probed_rows,
                    per_prompt=per_clean,
                )
                sent = ensure_by_prompt_coverage(sent, per_clean)
                return sent, None
            except Exception:
                pass
    try:
        sentiment = generate_prompt_sentiment(
            live_probe,
            brand_name=brand_name,
            site_url=site_url,
            pss_rows=pss_rows,
            per_prompt_override=per_clean,
        )
        if probe_path.is_file():
            save_sentiment_cache(audit_dir, probe_path, sentiment)
        return sentiment, None
    except Exception as exc:
        log.exception(
            "Prompt sentiment generation failed for %s (prompts=%s)",
            audit_dir.name,
            len(per_clean),
        )
        return None, str(exc)
