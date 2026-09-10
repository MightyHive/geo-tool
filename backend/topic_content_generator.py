"""Evidence-backed content outlines for low-visibility prompt topics."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal
import uuid
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

ARTIFACT_FILE = "topic_content_samples.v1.json"
ARTIFACT_VERSION = 1
PROBE_FILE = "prompt_performance_live_probe.json"
METRICS_FILE = "prompt_performance_metrics.json"
AIO_FILE = "prompt_performance_aio.json"
SAMPLES_DIR = "topic_content_samples"
PLATFORMS = ("gemini", "openai", "claude", "google_aio")
DEFAULT_MODEL = "gemini-3.5-flash"
FALLBACK_MODEL = "gemini-2.5-flash"
# Match web/src/lib/reportScore.ts GOOD_SCORE_MIN / isOkOrBelow.
GOOD_SCORE_MIN = 75


def _is_ok_or_below(visibility: float) -> bool:
    """True when rounded visibility is below Good — same banding as Recommendations."""
    return round(float(visibility)) < GOOD_SCORE_MIN


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime if path.is_file() else None
    except OSError:
        return None


class OutlineSection(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    heading: str = Field(min_length=2, max_length=240)
    level: Literal["H2", "H3"]
    instructions: str = Field(min_length=5)
    sample_copy: str = Field(min_length=1)
    evidence_citation_opportunities: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        clean = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-")
        if not clean:
            raise ValueError("section id must contain letters or numbers")
        return clean


class FAQItem(BaseModel):
    question: str = Field(min_length=3)
    answer_guidance: str = Field(min_length=3)
    evidence_citation_opportunities: list[str] = Field(default_factory=list, max_length=5)


class InternalLinkItem(BaseModel):
    anchor_text: str = Field(min_length=1)
    target_url_or_path: str = Field(min_length=1)
    rationale: str = Field(min_length=3)


class TopicContentOutline(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    meta_description: str = Field(min_length=20, max_length=320)
    audience: str = Field(min_length=2)
    intent: str = Field(min_length=2)
    sections: list[OutlineSection] = Field(min_length=1, max_length=20)
    faqs: list[FAQItem] = Field(default_factory=list, max_length=12)
    internal_links: list[InternalLinkItem] = Field(default_factory=list, max_length=12)

    @field_validator("sections")
    @classmethod
    def unique_section_ids(cls, value: list[OutlineSection]) -> list[OutlineSection]:
        ids = [section.id for section in value]
        if len(ids) != len(set(ids)):
            raise ValueError("section ids must be unique")
        return value


# Gemini Developer API constrained decoding rejects schemas with lots of
# minLength/maxLength/array bounds. Keep the wire schema minimal and validate
# into TopicContentOutline after the response.
class _GeminiOutlineSection(BaseModel):
    id: str
    heading: str
    level: Literal["H2", "H3"]
    instructions: str
    sample_copy: str
    evidence_citation_opportunities: list[str]


class _GeminiFAQItem(BaseModel):
    question: str
    answer_guidance: str
    evidence_citation_opportunities: list[str] = Field(default_factory=list)


class _GeminiInternalLinkItem(BaseModel):
    anchor_text: str
    target_url_or_path: str
    rationale: str


class _GeminiTopicContentOutline(BaseModel):
    title: str
    meta_description: str
    audience: str
    intent: str
    sections: list[_GeminiOutlineSection]
    faqs: list[_GeminiFAQItem] = Field(default_factory=list)
    internal_links: list[_GeminiInternalLinkItem] = Field(default_factory=list)


def _model_candidates(model: str | None = None) -> list[str]:
    primary = (model or os.getenv("GEMINI_TOPIC_CONTENT_MODEL") or DEFAULT_MODEL).strip()
    fallback = (os.getenv("GEMINI_TOPIC_CONTENT_FALLBACK_MODEL") or FALLBACK_MODEL).strip()
    out: list[str] = []
    for mid in (primary, fallback):
        if mid and mid not in out:
            out.append(mid)
    return out or [DEFAULT_MODEL]


class EditableSection(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    heading: str = Field(min_length=2, max_length=240)
    level: Literal["H2", "H3"]
    instructions: str = Field(default="", max_length=4000)
    evidence_citation_opportunities: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("id")
    @classmethod
    def normalize_id(cls, value: str) -> str:
        clean = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower()).strip("-")
        if not clean:
            raise ValueError("section id must contain letters or numbers")
        return clean


def parse_structure(value: Any) -> list[dict[str, Any]] | None:
    """Validate editable section specs accepted by regeneration requests."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("structure must be valid JSON") from exc
    if isinstance(value, dict):
        value = value.get("sections")
    if not isinstance(value, list) or not value:
        raise ValueError("structure must contain a non-empty sections list")
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError("each structure section must be an object")
        row = dict(item)
        heading = str(row.get("heading") or "").strip()
        row["id"] = row.get("id") or f"{index + 1}-{heading}"
        raw_level = str(row.get("level") or "H2").upper()
        row["level"] = "H3" if raw_level in {"3", "H3"} and index > 0 else "H2"
        normalized.append(row)
    parsed = [EditableSection.model_validate(item) for item in normalized]
    ids = [item.id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError("structure section ids must be unique")
    return [item.model_dump() for item in parsed]


def _norm(value: Any) -> str:
    return str(value or "").strip().casefold()


def _mapping_rows(context: dict[str, Any]) -> list[dict[str, Any]]:
    rows = context.get("probed_pss_rows")
    if not isinstance(rows, list) or not rows:
        rows = context.get("pss_rows")
    return [row for row in (rows or []) if isinstance(row, dict)]


def _merge_context_metadata(base: dict[str, Any], *extras: dict[str, Any] | None) -> dict[str, Any]:
    """Fill missing brand/PSS/market fields from metrics or onboarding context."""
    merged = dict(base)
    for extra in extras:
        if not isinstance(extra, dict):
            continue
        for key in (
            "brand_name",
            "brand_site_url",
            "use_pss",
            "pss_rows",
            "probed_pss_rows",
            "category_labels",
            "competitors",
            "primary_market",
            "prompt_locales",
            "default_locale_key",
            "highlight",
            "industry",
        ):
            current = merged.get(key)
            candidate = extra.get(key)
            empty_list = isinstance(current, list) and not current
            empty_dict = isinstance(current, dict) and not current
            if current in (None, "", []) or empty_list or empty_dict:
                if candidate not in (None, "", []) and candidate != {}:
                    merged[key] = candidate
        for key in ("probed_pss_rows", "pss_rows"):
            current = merged.get(key)
            candidate = extra.get(key)
            if isinstance(candidate, list) and candidate and (
                not isinstance(current, list) or not current
            ):
                merged[key] = candidate

    if not _mapping_rows(merged):
        onboarding = next((extra for extra in extras if isinstance(extra, dict)), None) or {}
        # Prefer the last extra that looks like onboarding when provided.
        for extra in reversed([item for item in extras if isinstance(item, dict)]):
            if "products_and_services_rows" in extra or "products_and_services" in extra:
                onboarding = extra
                break
        rows = onboarding.get("products_and_services_rows")
        if isinstance(rows, list) and rows:
            onboarding_rows = [row for row in rows if isinstance(row, dict)]
        else:
            products = onboarding.get("products_and_services") or []
            onboarding_rows = [
                {"product_or_service": str(item), "prompts": []}
                for item in products
                if str(item).strip()
            ] if isinstance(products, list) else []
        if onboarding_rows:
            merged["pss_rows"] = onboarding_rows
            merged["probed_pss_rows"] = onboarding_rows
    return merged


def _prompt_lookup_keys(prompt: str) -> list[str]:
    """Exact and geo-stripped keys so UK/market suffixes still map to PSS topics."""
    text = str(prompt or "").strip()
    keys: list[str] = []
    if text:
        keys.append(_norm(text))
    stripped = re.sub(
        r"\s+in\s+the\s+[a-z][a-z\s.'-]{1,40}$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip(" ?")
    if stripped and _norm(stripped) not in keys:
        keys.append(_norm(stripped))
    stripped2 = re.sub(r"\s+\([^)]{2,40}\)$", "", text).strip()
    if stripped2 and _norm(stripped2) not in keys:
        keys.append(_norm(stripped2))
    return keys


def _topic_metadata(context: dict[str, Any]) -> tuple[dict[str, str], list[tuple[str, str]]]:
    by_prompt: dict[str, str] = {}
    positional: list[tuple[str, str]] = []
    for row in _mapping_rows(context):
        topic = str(row.get("product_or_service") or "").strip() or "Other"
        for prompt in row.get("prompts") or []:
            text = str(prompt or "").strip()
            positional.append((topic, text or topic))
            for key in _prompt_lookup_keys(text):
                by_prompt.setdefault(key, topic)
    return by_prompt, positional


def _topic_for_row(
    row: dict[str, Any],
    *,
    locale_key: str,
    locale_index: int,
    context: dict[str, Any],
    by_prompt: dict[str, str],
    positional: list[tuple[str, str]],
) -> tuple[str, str]:
    prompt = str(row.get("prompt") or "").strip()
    for key in _prompt_lookup_keys(prompt):
        direct = by_prompt.get(key)
        if direct:
            return direct, prompt
    locales = context.get("locale_probes")
    if not isinstance(locales, dict):
        locales = context.get("locales")
    entry = locales.get(locale_key) if isinstance(locales, dict) else None
    if isinstance(entry, dict):
        probed = [str(item or "") for item in entry.get("prompts_probed") or []]
        sources = [str(item or "") for item in entry.get("source_prompts") or []]
        mapped = next((i for i, item in enumerate(probed) if _norm(item) == _norm(prompt)), -1)
        if mapped < 0 and locale_index < len(sources):
            mapped = locale_index
        if 0 <= mapped < len(sources):
            source = sources[mapped].strip()
            for key in _prompt_lookup_keys(source):
                topic = by_prompt.get(key)
                if topic:
                    return topic, source or prompt
            if mapped < len(positional):
                return positional[mapped][0], source or positional[mapped][1]
    if locale_index < len(positional):
        return positional[locale_index]
    labels = context.get("category_labels") or []
    fallback = str(labels[0]).strip() if isinstance(labels, list) and labels else "Other"
    return fallback or "Other", prompt or fallback or "Other"


def _completed_runs(
    row: dict[str, Any], platform: str
) -> list[tuple[str, dict[str, Any], list[dict[str, Any]]]]:
    runs = (row.get("runs") or {}).get(platform) if isinstance(row.get("runs"), dict) else None
    completed: list[tuple[str, dict[str, Any], list[dict[str, Any]]]] = []
    if isinstance(runs, list):
        for run in runs:
            if not isinstance(run, dict) or run.get("error"):
                continue
            response = str(run.get("response") or "")
            if response.strip() or bool(run.get("has_response")):
                scores = run.get("mention_scores")
                citations = run.get("citations")
                completed.append(
                    (
                        response,
                        scores if isinstance(scores, dict) else {},
                        [item for item in (citations or []) if isinstance(item, dict)],
                    )
                )
    if completed:
        return completed
    response = str(row.get(f"{platform}_response") or "")
    error = str(row.get(f"error_{platform}") or "")
    scores = row.get(f"mention_scores_{platform}")
    citations = row.get(f"citations_{platform}")
    clean_citations = [item for item in (citations or []) if isinstance(item, dict)]
    if response.strip() and not error:
        return [(response, scores if isinstance(scores, dict) else {}, clean_citations)]
    listed = platform in ((row.get("list_metrics") or {}).get("platforms_responded") or [])
    if (row.get(f"has_response_{platform}") or listed) and not error:
        return [("", scores if isinstance(scores, dict) else {}, clean_citations)]
    return []


def _text_mentions_brand(text: str, brand_name: str, tokens: list[str]) -> bool:
    if not text.strip():
        return False
    lower = text.casefold()
    brand = brand_name.strip()
    if brand:
        words = [part for part in re.split(r"[\s-]+", brand) if part]
        if len(words) >= 2:
            pattern = r"[\s-]+".join(re.escape(word) for word in words)
            if re.search(pattern, text, re.IGNORECASE):
                return True
        elif brand.casefold() in lower:
            return True
    return any(len(token.strip()) >= 2 and token.strip().casefold() in lower for token in tokens)


def _source_context(audit_dir: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    probe_path = audit_dir / PROBE_FILE
    metrics_path = audit_dir / METRICS_FILE
    onboarding_path = audit_dir / "onboarding_context.json"
    probe = _read_json(probe_path) if probe_path.is_file() else None
    metrics = _read_json(metrics_path) if metrics_path.is_file() else None
    onboarding = _read_json(onboarding_path) if onboarding_path.is_file() else None

    # Prefer live-probe responses when present, but always merge PSS / brand
    # metadata from metrics + onboarding. Older audits store responses in
    # ``prompt_performance_live_probe.json`` (top-level or locale_probes)
    # without product/service rows.
    empty_provenance = {
        "primary_file": None,
        "primary_mtime": None,
        "metrics_file": None,
        "metrics_mtime": None,
        "aio_file": AIO_FILE if (audit_dir / AIO_FILE).is_file() else None,
        "aio_mtime": _mtime(audit_dir / AIO_FILE),
    }
    if probe:
        context = _merge_context_metadata(probe, metrics, onboarding)
        source_name = PROBE_FILE
        source_mtime = _mtime(probe_path)
    elif metrics:
        context = _merge_context_metadata(metrics, onboarding)
        source_name = METRICS_FILE
        source_mtime = _mtime(metrics_path)
    else:
        return None, [], empty_provenance

    provenance = {
        "primary_file": source_name,
        "primary_mtime": source_mtime,
        "metrics_file": METRICS_FILE if metrics_path.is_file() else None,
        "metrics_mtime": _mtime(metrics_path),
        "aio_file": AIO_FILE if (audit_dir / AIO_FILE).is_file() else None,
        "aio_mtime": _mtime(audit_dir / AIO_FILE),
    }

    locale_entries = context.get("locale_probes")
    if not isinstance(locale_entries, dict):
        locale_entries = context.get("locales")
    sources: list[dict[str, Any]] = []
    if isinstance(locale_entries, dict) and locale_entries:
        for key, entry in locale_entries.items():
            if not isinstance(entry, dict):
                continue
            live = entry.get("live_probe")
            if isinstance(live, dict):
                sources.append({"locale_key": str(key), "entry": entry, "live": live})
    else:
        live = context.get("live_probe")
        if isinstance(live, dict):
            sources.append(
                {
                    "locale_key": str(context.get("default_locale_key") or "default"),
                    "entry": context,
                    "live": live,
                }
            )
    return context, sources, provenance


def compute_topic_visibility(audit_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compute response-weighted visibility using the report's completed-run semantics."""
    context, sources, provenance = _source_context(audit_dir)
    if not context:
        return [], provenance
    by_prompt, positional = _topic_metadata(context)
    brand = str(context.get("brand_name") or "").strip()
    topics: dict[str, dict[str, Any]] = {}
    has_live_aio = False

    def consume(row: dict[str, Any], locale_key: str, locale_index: int, platforms: tuple[str, ...]) -> None:
        nonlocal has_live_aio
        topic, source_prompt = _topic_for_row(
            row,
            locale_key=locale_key,
            locale_index=locale_index,
            context=context,
            by_prompt=by_prompt,
            positional=positional,
        )
        result = topics.setdefault(
            topic,
            {
                "topic": topic,
                "responses": 0,
                "brand_mentions": 0,
                "related_prompts": [],
                "evidence_examples": [],
                "locales": {},
                "platforms": {platform: {"responses": 0, "brand_mentions": 0} for platform in PLATFORMS},
            },
        )
        for prompt_text in (source_prompt, str(row.get("prompt") or "").strip()):
            if prompt_text and prompt_text not in result["related_prompts"]:
                result["related_prompts"].append(prompt_text)
        tokens = [
            str(token).strip()
            for token in (
                (row.get("brand_match_tokens") or [])
                or ((next((source["live"] for source in sources if source["locale_key"] == locale_key), {})).get("brand_match_tokens") or [])
                or ((context.get("highlight") or {}).get("brand_match_tokens") or [])
            )
            if str(token).strip()
        ]
        for platform in platforms:
            completed = _completed_runs(row, platform)
            if platform == "google_aio" and completed:
                has_live_aio = True
            for response, scores, citations in completed:
                visible = float(scores.get("brand_signal") or 0) > 0 or _text_mentions_brand(
                    response, brand, tokens
                )
                result["responses"] += 1
                result["brand_mentions"] += int(visible)
                platform_totals = result["platforms"][platform]
                platform_totals["responses"] += 1
                platform_totals["brand_mentions"] += int(visible)
                locale_totals = result["locales"].setdefault(
                    locale_key, {"responses": 0, "brand_mentions": 0}
                )
                locale_totals["responses"] += 1
                locale_totals["brand_mentions"] += int(visible)
                if len(result["evidence_examples"]) < 12 and (response.strip() or citations):
                    result["evidence_examples"].append(
                        {
                            "platform": platform,
                            "prompt": str(row.get("prompt") or source_prompt).strip(),
                            "response_excerpt": response.strip()[:1200],
                            "citations": citations[:8],
                            "brand_visible": visible,
                        }
                    )

    for source in sources:
        rows = source["live"].get("per_prompt") or []
        for index, row in enumerate(rows):
            if isinstance(row, dict):
                consume(row, source["locale_key"], index, PLATFORMS)

    # Legacy AIO was persisted separately. Include it only when the canonical probe
    # did not already contain completed Google AIO responses.
    if not has_live_aio:
        aio = _read_json(audit_dir / AIO_FILE)
        if not aio and isinstance(context.get("aio_probe"), dict):
            aio = context["aio_probe"]
        if aio:
            for index, row in enumerate(aio.get("per_prompt") or []):
                if not isinstance(row, dict):
                    continue
                normalized = dict(row)
                if "google_aio_response" not in normalized and row.get("response"):
                    normalized["google_aio_response"] = row.get("response")
                if "error_google_aio" not in normalized and row.get("error"):
                    normalized["error_google_aio"] = row.get("error")
                if "mention_scores_google_aio" not in normalized and row.get("mention_scores"):
                    normalized["mention_scores_google_aio"] = row.get("mention_scores")
                consume(normalized, "google_aio", index, ("google_aio",))

    ranked = [value for value in topics.values() if int(value["responses"]) > 0]
    for value in ranked:
        value["visibility"] = round(
            100.0 * int(value["brand_mentions"]) / int(value["responses"]), 1
        )
        value["suggested"] = _is_ok_or_below(float(value["visibility"]))
        value["related_prompts"] = list(dict.fromkeys(value["related_prompts"]))
    # Low-visibility (Recommendations) topics first, then lowest visibility.
    ranked.sort(
        key=lambda value: (
            0 if value.get("suggested") else 1,
            float(value["visibility"]),
            _norm(value["topic"]),
        )
    )
    previous: float | None = None
    current_rank = 0
    for index, value in enumerate(ranked, start=1):
        score = float(value["visibility"])
        if previous is None or score != previous:
            current_rank = index
            previous = score
        value["rank"] = current_rank
    return ranked, provenance


def artifact_path(audit_dir: Path) -> Path:
    return audit_dir / ARTIFACT_FILE


def topic_sample_path(audit_dir: Path, topic: str) -> Path:
    digest = hashlib.sha256(topic.casefold().encode("utf-8")).hexdigest()
    return audit_dir / SAMPLES_DIR / f"{digest}.json"


def load_topic_sample(audit_dir: Path, topic: str) -> dict[str, Any] | None:
    sample = _read_json(topic_sample_path(audit_dir, topic))
    if sample and str(sample.get("topic") or "").casefold() == topic.casefold():
        return sample
    return None


def load_artifact(audit_dir: Path) -> dict[str, Any] | None:
    value = _read_json(artifact_path(audit_dir))
    if not value or int(value.get("version") or 0) != ARTIFACT_VERSION:
        return None
    topics = value.get("topics") if isinstance(value.get("topics"), dict) else {}
    for topic, entry in topics.items():
        if isinstance(entry, dict):
            persisted = load_topic_sample(audit_dir, topic)
            if persisted is not None:
                entry["sample"] = persisted
    return value


def refresh_evidence(audit_dir: Path) -> dict[str, Any]:
    """Refresh evidence while retaining each surviving topic's current sample."""
    evidence, provenance = compute_topic_visibility(audit_dir)
    previous = load_artifact(audit_dir) or {}
    old_topics = previous.get("topics") if isinstance(previous.get("topics"), dict) else {}
    topics: dict[str, Any] = {}
    for row in evidence:
        topic = str(row["topic"])
        old = old_topics.get(topic) if isinstance(old_topics.get(topic), dict) else {}
        topics[topic] = {
            "evidence": row,
            "sample": load_topic_sample(audit_dir, topic) or old.get("sample"),
        }
    for removed_topic in set(old_topics) - set(topics):
        try:
            topic_sample_path(audit_dir, removed_topic).unlink(missing_ok=True)
        except OSError:
            pass
    payload = {
        "version": ARTIFACT_VERSION,
        "updated_at": _now(),
        "source_probe": provenance,
        "topics": topics,
    }
    _write_json(artifact_path(audit_dir), payload)
    return payload


def ensure_evidence(audit_dir: Path, *, refresh: bool = False) -> dict[str, Any]:
    cached = load_artifact(audit_dir)
    if refresh or not cached:
        return refresh_evidence(audit_dir)
    source = cached.get("source_probe") if isinstance(cached.get("source_probe"), dict) else {}
    _, _, current = _source_context(audit_dir)
    if (
        source.get("primary_mtime") != current.get("primary_mtime")
        or source.get("metrics_mtime") != current.get("metrics_mtime")
        or source.get("aio_mtime") != current.get("aio_mtime")
    ):
        return refresh_evidence(audit_dir)
    return cached


def _request_outline(prompt: str, model_name: str | None = None) -> tuple[TopicContentOutline, str]:
    from geo_setup_llm import build_genai_client
    from google.genai import types

    # Keep an explicit Client reference for the whole call. google-genai closes
    # its httpx transport in ``Client.__del__``, so chaining
    # ``build_genai_client().models.generate_content(...)`` can race GC and fail
    # with "Cannot send a request, as the client has been closed."
    last_error: Exception | None = None
    for mid in _model_candidates(model_name):
        client = build_genai_client()
        try:
            response = client.models.generate_content(
                model=mid,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.25,
                    top_p=0.9,
                    max_output_tokens=8192,
                    response_mime_type="application/json",
                    response_schema=_GeminiTopicContentOutline,
                ),
            )
            text = str(response.text or "").strip()
            if not text:
                raise ValueError(f"Empty Gemini response from model={mid}")
            draft = _GeminiTopicContentOutline.model_validate(json.loads(text))
            return TopicContentOutline.model_validate(draft.model_dump()), mid
        except Exception as exc:
            last_error = exc
            message = str(exc).lower()
            # Try the fallback model for schema/availability issues only.
            if "invalid_argument" in message or "not found" in message or "empty gemini" in message:
                continue
            raise
        finally:
            try:
                client.close()
            except Exception:
                pass
    assert last_error is not None
    raise last_error


def _compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Shrink evidence for the Gemini prompt without losing intent signals."""
    examples = []
    for example in (evidence.get("evidence_examples") or [])[:6]:
        if not isinstance(example, dict):
            continue
        citations = [
            {"url": item.get("url") or item.get("link"), "title": item.get("title")}
            for item in (example.get("citations") or [])[:3]
            if isinstance(item, dict)
        ]
        examples.append(
            {
                "platform": example.get("platform"),
                "prompt": example.get("prompt"),
                "brand_visible": example.get("brand_visible"),
                "response_excerpt": str(example.get("response_excerpt") or "")[:500],
                "citations": [row for row in citations if row.get("url")],
            }
        )
    return {
        "topic": evidence.get("topic"),
        "visibility": evidence.get("visibility"),
        "responses": evidence.get("responses"),
        "brand_mentions": evidence.get("brand_mentions"),
        "related_prompts": list(evidence.get("related_prompts") or [])[:8],
        "evidence_examples": examples,
    }


def generate_topic_outline(
    audit_dir: Path,
    topic: str,
    *,
    structure: Any = None,
    model: str | None = None,
    commit_guard: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    artifact = ensure_evidence(audit_dir)
    topic_entry = (artifact.get("topics") or {}).get(topic)
    if not isinstance(topic_entry, dict):
        raise ValueError(f"Unknown or response-free topic: {topic}")
    sections = parse_structure(structure)
    context, _, _ = _source_context(audit_dir)
    context = context or {}
    evidence = topic_entry.get("evidence") or {}
    brand = str(context.get("brand_name") or "").strip() or "the brand"
    site = str(context.get("brand_site_url") or "").strip() or "the audited site"
    competitors = context.get("competitors") or []
    market = context.get("primary_market") or {}
    locales = context.get("prompt_locales") or []
    default_locale_key = str(context.get("default_locale_key") or "")
    default_locale = next(
        (
            locale
            for locale in locales
            if isinstance(locale, dict)
            and str(locale.get("key") or "") == default_locale_key
        ),
        {},
    )
    output_language = str(
        (default_locale or {}).get("language_name")
        or (default_locale or {}).get("language")
        or ""
    ).strip()
    model_name = (model or os.getenv("GEMINI_TOPIC_CONTENT_MODEL") or DEFAULT_MODEL).strip()
    structure_instruction = (
        "Use this exact section order and IDs, filling instructions, sample_copy, and evidence opportunities:\n"
        + json.dumps(sections, ensure_ascii=False, indent=2)
        if sections
        else "Design an ordered H2/H3 structure that directly answers the related prompts."
    )
    compact_evidence = _compact_evidence(evidence if isinstance(evidence, dict) else {})
    prompt = f"""
Create one publication-ready content outline for the topic {topic!r}.

Brand/site: {brand} — {site}
Competitors: {json.dumps(competitors, ensure_ascii=False)}
Primary market: {json.dumps(market, ensure_ascii=False)}
Market/language locales: {json.dumps(locales, ensure_ascii=False)}
Required output language: {output_language or "the language used by the default-locale prompts"}
Response-weighted topic evidence: {json.dumps(compact_evidence, ensure_ascii=False)}

{structure_instruction}

Rules:
- Return the configured JSON schema only.
- Use the related source and translated prompts as the intent evidence.
- Write concise illustrative sample_copy for every section.
- Identify places where the publisher should add first-party data, expert review,
  primary sources, citations, comparisons, or transparent limitations.
- Suggest internal links only to the audited site; use a plausible relative path
  when no verified URL is present and label it as a suggested target in rationale.
- Never invent facts, awards, statistics, customers, product capabilities,
  certifications, test results, prices, policies, or claims about {brand}.
- When a brand-specific fact is not present in the supplied evidence, write a
  visible [VERIFY] placeholder/instruction to verify it before publication,
  not a factual claim.
- Every section must include at least one concrete evidence or citation
  opportunity, even when the sample copy uses only [VERIFY] placeholders.
- Do not state that a competitor lacks or possesses a feature without evidence.
""".strip()
    outline, model_name = _request_outline(prompt, model_name)
    # Guarantee citation opportunities even when the model omits them.
    padded_sections: list[OutlineSection] = []
    for section in outline.sections:
        opportunities = list(section.evidence_citation_opportunities or [])
        if not opportunities:
            opportunities = [
                "Add a first-party source, expert review, or [VERIFY] claim before publishing."
            ]
        padded_sections.append(
            section.model_copy(update={"evidence_citation_opportunities": opportunities[:8]})
        )
    outline = outline.model_copy(update={"sections": padded_sections})
    if sections:
        generated_by_id = {section.id: section for section in outline.sections}
        exact_sections: list[OutlineSection] = []
        for index, spec in enumerate(sections):
            generated = generated_by_id.get(str(spec["id"]))
            if generated is None and index < len(outline.sections):
                generated = outline.sections[index]
            if generated is None:
                raise ValueError("Gemini omitted a requested outline section")
            exact_sections.append(
                OutlineSection(
                    id=str(spec["id"]),
                    heading=str(spec["heading"]),
                    level=spec["level"],
                    instructions=str(spec.get("instructions") or generated.instructions),
                    sample_copy=generated.sample_copy,
                    evidence_citation_opportunities=generated.evidence_citation_opportunities,
                )
            )
        outline = outline.model_copy(update={"sections": exact_sections})

    audited_host = (urlparse(site).hostname or "").lower().removeprefix("www.")
    safe_links: list[InternalLinkItem] = []
    for link in outline.internal_links:
        target = link.target_url_or_path.strip()
        if target.startswith("/") and not target.startswith("//"):
            safe_links.append(link)
            continue
        target_host = (urlparse(target).hostname or "").lower().removeprefix("www.")
        if audited_host and target_host == audited_host:
            safe_links.append(link)
    outline = outline.model_copy(update={"internal_links": safe_links})
    sample = {
        "topic": topic,
        "generated_at": _now(),
        "model": model_name,
        "structure_supplied": bool(sections),
        "source_probe": artifact.get("source_probe") or {},
        "evidence_snapshot": evidence,
        "outline": outline.model_dump(),
    }
    if commit_guard is not None and not commit_guard():
        raise RuntimeError("Topic generation was superseded by a newer request")
    # Samples use one file per topic, avoiding cross-topic read/modify/write races
    # on the GCS FUSE mount. Atomic replacement preserves one current sample.
    _write_json(topic_sample_path(audit_dir, topic), sample)
    return sample
