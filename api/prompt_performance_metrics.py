"""Persisted slim prompt-performance metrics (no reply bodies).

Write-time sanitization/scoring is stored so GET paths can serve small JSON
without re-reading and reprocessing the multi-locale live probe (~MBs).
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

METRICS_FILE = "prompt_performance_metrics.json"
# Precomputed Citations-page payloads (capped domains/URLs) — avoids reading the
# multi‑MB metrics blob on every Citations GET (GCS FUSE cold reads time out).
CITATIONS_VIEW_FILE = "prompt_performance_citations.json"
CITATIONS_VIEW_VERSION = 1
# Slim runs omit per-run citations (row-level citations_* is enough). Older v1
# blobs are sanitized at response time via sanitize_context_runs_inplace.
# Bump whenever the persisted slim shape changes. This invalidates Redis/GCS
# slim caches and forces a one-time rebuild from the stored full probe.
METRICS_VERSION = 2
LIVE_PROBE_FILE = "prompt_performance_live_probe.json"
# Per-prompt full reply bodies (one small JSON per prompt×locale) for fast overlay GETs.
REPLIES_DIR = "prompt_performance_replies"

_PLATFORM_KEYS = ("gemini", "openai", "claude", "google_aio")
_RESPONSE_KEYS = tuple(f"{pk}_response" for pk in _PLATFORM_KEYS)

_POSITIVE_WORDS = (
    "best", "top", "recommend", "recommended", "leading", "excellent", "great",
    "trusted", "award", "premium", "renowned", "outstanding", "popular",
    "highly rated", "highly regarded", "well-known", "preferred", "regarded",
    "favorite", "favourite", "praised", "featured", "celebrated", "notable",
    "widely used", "market leader", "well-regarded", "stand out", "go-to",
    "first choice", "gold standard", "dermatologist-recommended", "widely recommended",
)
_NEGATIVE_WORDS = (
    "worst", "avoid", "poor", "bad", "inferior", "terrible", "disappointing",
    "overpriced", "controversial", "concern", "issue", "problem", "complaint",
    "unreliable", "ineffective", "inadequate", "subpar", "recall", "lawsuit",
    "banned", "not recommended", "side effect",
)


def metrics_path(audit_dir: Path) -> Path:
    return audit_dir / METRICS_FILE


def citations_view_path(audit_dir: Path) -> Path:
    return audit_dir / CITATIONS_VIEW_FILE


def probe_mtime(audit_dir: Path) -> float | None:
    path = audit_dir / LIVE_PROBE_FILE
    try:
        return path.stat().st_mtime if path.is_file() else None
    except OSError:
        return None


def _response_sentiment(text: str, brand_tokens: list[str]) -> str:
    lower = text.lower()
    positions: list[int] = []
    for token in brand_tokens:
        tok = token.lower()
        if not tok:
            continue
        start = lower.find(tok)
        while start >= 0:
            positions.append(start)
            start = lower.find(tok, start + 1)
    if not positions:
        return "neutral"
    positive = negative = 0
    for position in positions:
        window = lower[max(0, position - 280) : position + 280]
        positive += sum(word in window for word in _POSITIVE_WORDS)
        negative += sum(word in window for word in _NEGATIVE_WORDS)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


def _platform_responses(row: dict[str, Any], platform: str) -> list[str]:
    runs = (row.get("runs") or {}).get(platform) or []
    completed = [
        str(run.get("response") or "")
        for run in runs
        if isinstance(run, dict) and run.get("response") and not run.get("error")
    ]
    if completed:
        return completed
    response = str(row.get(f"{platform}_response") or "")
    error = str(row.get(f"error_{platform}") or "")
    return [response] if response and not error else []


def _strip_runs(runs: Any) -> dict[str, Any] | None:
    """Keep per-run scores/errors; omit citations (duplicated on row-level citations_*)."""
    if not isinstance(runs, dict):
        return None
    out: dict[str, Any] = {}
    for platform, entries in runs.items():
        if not isinstance(entries, list):
            continue
        slim_entries: list[dict[str, Any]] = []
        for run in entries:
            if not isinstance(run, dict):
                continue
            slim_entries.append(
                {
                    "run_index": run.get("run_index"),
                    "mention_scores": run.get("mention_scores") or {},
                    "error": run.get("error"),
                    "has_response": bool(str(run.get("response") or "").strip()) and not run.get("error"),
                }
            )
        if slim_entries:
            out[str(platform)] = slim_entries
    return out or None


def sanitize_slim_runs_inplace(live: dict[str, Any] | None) -> None:
    """Drop duplicated run-level citations from already-persisted slim metrics (v1 blobs)."""
    if not isinstance(live, dict):
        return
    for row in live.get("per_prompt") or []:
        if not isinstance(row, dict):
            continue
        runs = row.get("runs")
        if not isinstance(runs, dict):
            continue
        for _platform, entries in runs.items():
            if not isinstance(entries, list):
                continue
            for run in entries:
                if isinstance(run, dict):
                    run.pop("citations", None)


def sanitize_context_runs_inplace(ctx: dict[str, Any] | None) -> None:
    """Sanitize live_probe + locale_probes runs before returning a slim GET payload."""
    if not isinstance(ctx, dict):
        return
    live = ctx.get("live_probe")
    if isinstance(live, dict):
        sanitize_slim_runs_inplace(live)
    locales = ctx.get("locale_probes")
    if isinstance(locales, dict):
        for entry in locales.values():
            if not isinstance(entry, dict):
                continue
            entry_live = entry.get("live_probe")
            if isinstance(entry_live, dict):
                sanitize_slim_runs_inplace(entry_live)


def prompt_id_for(index: int, prompt: str, locale_key: str = "") -> str:
    """Stable-enough id for expand/detail fetches (index + short prompt slug)."""
    slug = re.sub(r"[^a-z0-9]+", "-", (prompt or "").lower()).strip("-")[:48] or "prompt"
    base = f"{index}:{slug}"
    return f"{locale_key}|{base}" if locale_key else base


def compute_row_list_metrics(
    row: dict[str, Any],
    *,
    brand_tokens: list[str],
    platforms: tuple[str, ...] = _PLATFORM_KEYS,
) -> dict[str, Any]:
    """Precompute list-view fields that would otherwise need reply bodies."""
    tokens = [t for t in brand_tokens if t]
    token_lower = [t.lower() for t in tokens]
    platforms_responded: list[str] = []
    mention_count = 0
    response_count = 0
    sentiment_votes: dict[str, int] = {"positive": 0, "negative": 0, "neutral": 0}
    avg_position_acc = 0.0
    avg_position_n = 0
    competitors: set[str] = set()
    citation_domains: list[str] = []
    seen_domains: set[str] = set()
    platform_metrics: dict[str, dict[str, Any]] = {}

    for platform in platforms:
        responses = _platform_responses(row, platform)
        scores = row.get(f"mention_scores_{platform}") or {}
        if not isinstance(scores, dict):
            scores = {}
        platform_votes: dict[str, int] = {"positive": 0, "negative": 0, "neutral": 0}
        platform_mentions = 0
        platform_position_acc = 0.0
        platform_position_n = 0
        if responses:
            platforms_responded.append(platform)
        for resp in responses:
            response_count += 1
            lower = resp.lower()
            brand_mentioned = float(scores.get("brand_signal") or 0) > 0 or any(
                tok in lower for tok in token_lower
            )
            if brand_mentioned:
                mention_count += 1
                platform_mentions += 1
                label = _response_sentiment(resp, tokens)
                sentiment_votes[label] = sentiment_votes.get(label, 0) + 1
                platform_votes[label] = platform_votes.get(label, 0) + 1
            if tokens:
                for tok in token_lower:
                    idx = lower.find(tok)
                    if idx >= 0 and len(resp) > 0:
                        position = (idx / len(resp)) * 10 + 1
                        avg_position_acc += position
                        avg_position_n += 1
                        platform_position_acc += position
                        platform_position_n += 1
                        break
        for name, hits in (scores.get("competitor_detail") or {}).items():
            if float(hits or 0) > 0 and str(name).strip():
                competitors.add(str(name).strip())
        for cit in row.get(f"citations_{platform}") or []:
            if not isinstance(cit, dict):
                continue
            domain = str(cit.get("domain") or "").strip().lower().removeprefix("www.")
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                citation_domains.append(domain)
        platform_metrics[platform] = {
            "response_count": len(responses),
            "brand_mention_count": platform_mentions,
            "sentiment_votes": platform_votes,
            "avg_position": round(platform_position_acc / platform_position_n, 2) if platform_position_n else None,
            "position_count": platform_position_n,
        }

    if sentiment_votes["positive"] >= sentiment_votes["negative"] and (
        sentiment_votes["positive"] > 0 or sentiment_votes["neutral"] > 0
    ):
        dominant = (
            "positive"
            if sentiment_votes["positive"] > sentiment_votes["negative"]
            else ("negative" if sentiment_votes["negative"] > sentiment_votes["positive"] else "neutral")
        )
    elif sentiment_votes["negative"] > sentiment_votes["positive"]:
        dominant = "negative"
    else:
        dominant = "neutral"

    return {
        "platforms_responded": platforms_responded,
        "visibility_pct": round(100.0 * mention_count / response_count, 1) if response_count else 0.0,
        "sentiment": dominant,
        "sentiment_votes": sentiment_votes,
        "avg_position": round(avg_position_acc / avg_position_n, 2) if avg_position_n else None,
        "competitors_mentioned": sorted(competitors),
        "citation_domains": citation_domains[:20],
        "response_count": response_count,
        "brand_mention_count": mention_count,
        "platform_metrics": platform_metrics,
    }


def compute_keyword_sentiment_aggregate(
    per_prompt: list[dict[str, Any]],
    *,
    brand_tokens: list[str],
    platforms: tuple[str, ...] = _PLATFORM_KEYS,
) -> dict[str, Any]:
    tokens = [t for t in brand_tokens if t]
    token_lower = [t.lower() for t in tokens]
    mentioned = positive = negative = 0
    for row in per_prompt:
        if not isinstance(row, dict):
            continue
        for platform in platforms:
            for resp in _platform_responses(row, platform):
                lower = resp.lower()
                if not any(tok in lower for tok in token_lower):
                    continue
                mentioned += 1
                label = _response_sentiment(resp, tokens)
                if label == "positive":
                    positive += 1
                elif label == "negative":
                    negative += 1
    score = round(100.0 * positive / mentioned, 1) if mentioned else None
    if score is None:
        label = "neutral"
    elif score >= 60:
        label = "positive"
    elif score <= 40:
        label = "negative"
    else:
        label = "neutral"
    return {
        "mentioned_count": mentioned,
        "positive_count": positive,
        "negative_count": negative,
        "score_percent": score,
        "label": label,
    }


def slim_per_prompt_row(
    row: dict[str, Any],
    *,
    index: int,
    locale_key: str = "",
    brand_tokens: list[str] | None = None,
) -> dict[str, Any]:
    """Drop reply bodies; keep scores/citations/errors + precomputed list metrics."""
    prompt = str(row.get("prompt") or "").strip()
    tokens = brand_tokens or []
    list_metrics = compute_row_list_metrics(row, brand_tokens=tokens)
    slim: dict[str, Any] = {
        "index": row.get("index", index),
        "prompt": prompt,
        "prompt_id": prompt_id_for(index, prompt, locale_key),
        "list_metrics": list_metrics,
        "replies_omitted": True,
    }
    for key in (
        "run_index",
        "gemini_brand_mention_pct",
        "gemini_competitor_mention_pct",
        "openai_brand_mention_pct",
        "openai_competitor_mention_pct",
        "claude_brand_mention_pct",
        "claude_competitor_mention_pct",
        "google_aio_brand_mention_pct",
        "google_aio_competitor_mention_pct",
    ):
        if key in row:
            slim[key] = row[key]
    for platform in _PLATFORM_KEYS:
        for suffix in ("mention_scores", "citations", "error"):
            if suffix == "error":
                key = f"error_{platform}"
            elif suffix == "mention_scores":
                key = f"mention_scores_{platform}"
            else:
                key = f"citations_{platform}"
            if key in row:
                slim[key] = row[key]
        # Preserve presence without shipping reply text.
        has_resp = bool(_platform_responses(row, platform))
        slim[f"has_response_{platform}"] = has_resp
    runs = _strip_runs(row.get("runs"))
    if runs:
        slim["runs"] = runs
    if row.get("_locale_key"):
        slim["_locale_key"] = row.get("_locale_key")
    return slim


def slim_live_probe(
    live: dict[str, Any] | None,
    *,
    locale_key: str = "",
) -> dict[str, Any] | None:
    if not isinstance(live, dict):
        return None
    brand_tokens = [
        str(t).strip()
        for t in (live.get("brand_match_tokens") or [])
        if str(t).strip()
    ]
    per_raw = live.get("per_prompt") if isinstance(live.get("per_prompt"), list) else []
    per_prompt = [
        slim_per_prompt_row(
            row,
            index=i,
            locale_key=locale_key,
            brand_tokens=brand_tokens,
        )
        for i, row in enumerate(per_raw)
        if isinstance(row, dict)
    ]
    keyword_sentiment = compute_keyword_sentiment_aggregate(
        [r for r in per_raw if isinstance(r, dict)],
        brand_tokens=brand_tokens,
    )
    out = {
        "per_prompt": per_prompt,
        "aggregate": live.get("aggregate"),
        "disclaimer": live.get("disclaimer"),
        "reply_detected_brands": live.get("reply_detected_brands") or [],
        "reply_detected_brand_names": live.get("reply_detected_brand_names") or [],
        "reply_detected_brands_error": live.get("reply_detected_brands_error"),
        "brand_match_tokens": live.get("brand_match_tokens") or [],
        "brand_detected_spellings": live.get("brand_detected_spellings") or [],
        "product_line_aliases": live.get("product_line_aliases") or [],
        "excluded_platforms": live.get("excluded_platforms") or [],
        "active_platforms": live.get("active_platforms") or [],
        "top_cited_sites": live.get("top_cited_sites") or [],
        "top_cited_urls": live.get("top_cited_urls") or [],
        "competitor_urls": live.get("competitor_urls") or [],
        "competitor_brands": live.get("competitor_brands") or [],
        "keyword_sentiment": keyword_sentiment,
        "replies_omitted": True,
        "prompt_count": len(per_prompt),
    }
    return out


def visibility_metrics_from_slim_live(live: dict[str, Any] | None) -> dict[str, Any] | None:
    """Best-effort visibility metrics from a (full or slim) live probe via geo_services helpers."""
    if not isinstance(live, dict) or not (live.get("per_prompt") or []):
        return None
    # Prefer recomputing from mention_scores when reply bodies are present or brand_signal stored.
    platforms = _PLATFORM_KEYS
    rows = [r for r in (live.get("per_prompt") or []) if isinstance(r, dict)]
    visible_responses = 0
    response_count = 0
    brand_hits = 0.0
    competitor_hits_by_name: dict[str, float] = {}
    per_platform_acc: dict[str, dict[str, Any]] = {
        platform: {
            "response_count": 0,
            "visible_response_count": 0,
            "brand_hits": 0.0,
            "competitor_hits": 0.0,
        }
        for platform in platforms
    }

    for row in rows:
        list_m = row.get("list_metrics") if isinstance(row.get("list_metrics"), dict) else None
        for platform in platforms:
            scores = row.get(f"mention_scores_{platform}") or {}
            if not isinstance(scores, dict):
                scores = {}
            has_response = bool(row.get(f"has_response_{platform}"))
            if not has_response:
                # Full probe shape
                has_response = bool(_platform_responses(row, platform))
            if not has_response and list_m:
                # Fall back to list metrics presence
                has_response = platform in (list_m.get("platforms_responded") or [])
            if not has_response:
                continue
            response_count += 1
            plat = per_platform_acc[platform]
            plat["response_count"] += 1
            signal = float(scores.get("brand_signal") or 0)
            visible = signal > 0
            if visible:
                visible_responses += 1
                plat["visible_response_count"] += 1
            brand_hits += signal if signal > 0 else (1.0 if visible else 0.0)
            plat["brand_hits"] += signal if signal > 0 else (1.0 if visible else 0.0)
            detail = scores.get("competitor_detail") or {}
            if isinstance(detail, dict):
                for name, value in detail.items():
                    key = str(name).strip()
                    if not key:
                        continue
                    competitor_hits_by_name[key] = competitor_hits_by_name.get(key, 0.0) + float(
                        value or 0
                    )
                    plat["competitor_hits"] += float(value or 0)

    if response_count == 0 and rows:
        # Use precomputed list metrics when available.
        for row in rows:
            lm = row.get("list_metrics") if isinstance(row.get("list_metrics"), dict) else None
            if not lm:
                continue
            response_count += int(lm.get("response_count") or 0)
            visible_responses += int(lm.get("brand_mention_count") or 0)

    sov_competitor_limit = 10
    ranked = sorted(
        ((n, v) for n, v in competitor_hits_by_name.items() if v > 0),
        key=lambda item: (-item[1], item[0]),
    )
    top = ranked[:sov_competitor_limit]
    positive_comp = [v for _, v in top]
    competitor_hits = sum(positive_comp)
    all_comp = sum(v for _, v in ranked)
    visibility_pct = 100.0 * visible_responses / response_count if response_count else 0.0
    total_raw = brand_hits + all_comp
    total_rel = brand_hits + competitor_hits
    sov_pct = 100.0 * brand_hits / total_raw if total_raw else 0.0
    if brand_hits <= 0:
        sov_performance_score = 0.0
        sov_rank = len(positive_comp) + 1 if positive_comp else None
    elif not positive_comp:
        sov_performance_score = 100.0
        sov_rank = 1
    else:
        below = sum(1 for v in positive_comp if v < brand_hits)
        tied = sum(1 for v in positive_comp if abs(v - brand_hits) <= 1e-9)
        ahead = sum(1 for v in positive_comp if v > brand_hits)
        sov_performance_score = 100.0 * (below + 0.5 * tied) / len(positive_comp)
        sov_rank = ahead + 1
    top_competitor_sov = (
        100.0 * max(positive_comp) / total_rel if positive_comp and total_rel else 0.0
    )
    average_competitor_sov = (
        100.0 * (sum(positive_comp) / len(positive_comp)) / total_rel
        if positive_comp and total_rel
        else 0.0
    )
    score = min(100.0, 0.60 * visibility_pct + 0.40 * sov_performance_score)
    per_platform: dict[str, Any] = {}
    for platform, plat in per_platform_acc.items():
        pr = int(plat["response_count"])
        pv = int(plat["visible_response_count"])
        pb = float(plat["brand_hits"])
        pc = float(plat["competitor_hits"])
        pt = pb + pc
        per_platform[platform] = {
            "response_count": pr,
            "visible_response_count": pv,
            "visibility_pct": round(100.0 * pv / pr, 1) if pr else 0.0,
            "brand_hits": round(pb, 1),
            "competitor_hits": round(pc, 1),
            "sov_pct": round(100.0 * pb / pt, 1) if pt else 0.0,
        }
    return {
        "score": round(score, 1),
        "visibility_pct": round(visibility_pct, 1),
        "sov_pct": round(sov_pct, 1),
        "sov_performance_score": round(sov_performance_score, 1),
        "sov_rank": sov_rank,
        "competitor_count": len(positive_comp),
        "detected_competitor_count": len(ranked),
        "sov_competitor_limit": sov_competitor_limit,
        "top_competitor_sov_pct": round(top_competitor_sov, 1),
        "average_competitor_sov_pct": round(average_competitor_sov, 1),
        "visible_prompt_count": visible_responses,
        "prompt_count": response_count,
        "visible_response_count": visible_responses,
        "response_count": response_count,
        "tested_prompt_count": len(rows),
        "brand_hits": round(brand_hits, 1),
        "competitor_hits": round(competitor_hits, 1),
        "per_platform": per_platform,
    }


def build_metrics_payload_from_context(ctx: dict[str, Any], *, probe_mtime_value: float | None) -> dict[str, Any]:
    """Build the persisted metrics document from a (sanitized) full context response."""
    default_key = str(ctx.get("default_locale_key") or "")
    locale_probes_raw = ctx.get("locale_probes") if isinstance(ctx.get("locale_probes"), dict) else {}
    locales: dict[str, Any] = {}
    for key, entry in locale_probes_raw.items():
        if not isinstance(entry, dict):
            continue
        live = entry.get("live_probe") if isinstance(entry.get("live_probe"), dict) else None
        slim = slim_live_probe(live, locale_key=str(key))
        locales[str(key)] = {
            "locale": entry.get("locale"),
            "live_probe": slim,
            "prompts_probed": entry.get("prompts_probed") or [],
            "source_prompts": entry.get("source_prompts") or [],
            "metrics": visibility_metrics_from_slim_live(slim),
        }

    default_live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else None
    if default_key and default_key in locales:
        slim_default = locales[default_key].get("live_probe")
    else:
        slim_default = slim_live_probe(default_live, locale_key=default_key)

    overall_metrics = None
    # Merge overall from all locale slim probes (metrics only).
    if locales:
        merged_rows: list[dict[str, Any]] = []
        brand_tokens: list[str] = []
        active: list[str] = []
        top_sites: list[Any] = []
        top_urls: list[Any] = []
        reply_brands: list[Any] = []
        for key, entry in locales.items():
            live = entry.get("live_probe") if isinstance(entry.get("live_probe"), dict) else None
            if not live:
                continue
            if not brand_tokens:
                brand_tokens = list(live.get("brand_match_tokens") or [])
            if not active:
                active = list(live.get("active_platforms") or [])
            for row in live.get("per_prompt") or []:
                if isinstance(row, dict):
                    merged_rows.append({**row, "_locale_key": key})
            top_sites.extend(live.get("top_cited_sites") or [])
            top_urls.extend(live.get("top_cited_urls") or [])
            reply_brands.extend(live.get("reply_detected_brands") or [])
        overall_live = {
            "per_prompt": merged_rows,
            "brand_match_tokens": brand_tokens,
            "active_platforms": active,
            "top_cited_sites": top_sites,
            "top_cited_urls": top_urls,
            "reply_detected_brands": reply_brands,
            "keyword_sentiment": _keyword_sentiment_from_list_metrics(merged_rows),
        }
        overall_metrics = visibility_metrics_from_slim_live(overall_live)
    else:
        overall_live = slim_default
        overall_metrics = visibility_metrics_from_slim_live(slim_default)

    return {
        "version": METRICS_VERSION,
        "updated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "probe_mtime": probe_mtime_value,
        "brand_name": ctx.get("brand_name"),
        "brand_site_url": ctx.get("brand_site_url"),
        "use_pss": ctx.get("use_pss"),
        "pss_rows": ctx.get("pss_rows") or [],
        "probed_pss_rows": ctx.get("probed_pss_rows") or [],
        "flat_prompts": ctx.get("flat_prompts") or [],
        "prompt_count": ctx.get("prompt_count"),
        "stored_prompt_count": ctx.get("stored_prompt_count"),
        "competitors": ctx.get("competitors") or [],
        "primary_market": ctx.get("primary_market"),
        "prompt_locales": ctx.get("prompt_locales") or [],
        "default_locale_key": default_key,
        "locale_spread": ctx.get("locale_spread") or [],
        "category_labels": ctx.get("category_labels") or [],
        "industry": ctx.get("industry"),
        "highlight": ctx.get("highlight") or {},
        "sov_history": ctx.get("sov_history") or [],
        "sov_history_by_product": ctx.get("sov_history_by_product") or {},
        "aio_probe": ctx.get("aio_probe"),
        "aio_probe_in_progress": bool(ctx.get("aio_probe_in_progress")),
        "live_probe_in_progress": bool(ctx.get("live_probe_in_progress")),
        "overall_metrics": overall_metrics,
        "live_probe": slim_default,
        "locales": locales,
    }


def _keyword_sentiment_from_list_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mentioned = positive = negative = 0
    for row in rows:
        lm = row.get("list_metrics") if isinstance(row.get("list_metrics"), dict) else None
        if not lm:
            continue
        votes = lm.get("sentiment_votes") if isinstance(lm.get("sentiment_votes"), dict) else {}
        pos = int(votes.get("positive") or 0)
        neg = int(votes.get("negative") or 0)
        neu = int(votes.get("neutral") or 0)
        mentioned += pos + neg + neu
        positive += pos
        negative += neg
    score = round(100.0 * positive / mentioned, 1) if mentioned else None
    if score is None:
        label = "neutral"
    elif score >= 60:
        label = "positive"
    elif score <= 40:
        label = "negative"
    else:
        label = "neutral"
    return {
        "mentioned_count": mentioned,
        "positive_count": positive,
        "negative_count": negative,
        "score_percent": score,
        "label": label,
    }


def write_metrics_file(audit_dir: Path, payload: dict[str, Any]) -> Path:
    path = metrics_path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        from api.audit_json_cache import invalidate_path

        invalidate_path(path)
    except Exception:
        pass
    # Probe finalize / metrics rewrite → drop Redis + optional GCS slim blobs.
    try:
        invalidate_slim_response_caches(audit_dir)
    except Exception:
        log.debug("slim response cache invalidate failed for %s", audit_dir, exc_info=True)
    try:
        maybe_write_gcs_slim_locale_blobs(audit_dir, payload)
    except Exception:
        log.debug("GCS slim locale blob write failed for %s", audit_dir, exc_info=True)
    try:
        write_citations_view_file(audit_dir, payload)
    except Exception:
        log.exception("Failed to write citations view cache for %s", audit_dir)
    return path


def slim_response_cache_key(
    audit_id: str,
    *,
    locale_key: str | None,
    include_all_locales: bool,
    version: int,
    probe_mtime_value: float | None,
) -> str:
    """Cache key: audit + locale scope + metrics version + probe mtime."""
    if include_all_locales or locale_key in (None, "", "__all__"):
        scope = "__all__"
    else:
        scope = str(locale_key)
    mt = f"{float(probe_mtime_value):.3f}" if probe_mtime_value is not None else "none"
    return f"pp-slim:{audit_id}:{scope}:v{version}:{mt}"


def invalidate_slim_response_caches(audit_dir: Path) -> None:
    """Invalidate Redis keys and optional per-locale GCS slim JSON for an audit."""
    audit_id = audit_dir.name
    try:
        from api.cache import cache_delete_prefix

        cache_delete_prefix(f"pp-slim:{audit_id}:")
    except Exception:
        log.debug("Redis slim cache invalidate failed for %s", audit_id, exc_info=True)
    for path in audit_dir.glob("prompt_performance_slim_*.json"):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _locale_slug(locale_key: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", locale_key or "default")[:120] or "default"


def gcs_slim_locale_path(audit_dir: Path, locale_key: str) -> Path:
    return audit_dir / f"prompt_performance_slim_{_locale_slug(locale_key)}.json"


def maybe_write_gcs_slim_locale_blobs(audit_dir: Path, metrics: dict[str, Any]) -> None:
    """Optionally precompute per-locale slim GET payloads next to the audit (GCS mount)."""
    try:
        from api.cache import gcs_slim_cache_enabled
    except Exception:
        return
    if not gcs_slim_cache_enabled():
        return
    locales = metrics.get("locales") if isinstance(metrics.get("locales"), dict) else {}
    keys = list(locales.keys()) if locales else []
    default_key = str(metrics.get("default_locale_key") or "")
    if default_key and default_key not in keys:
        keys.append(default_key)
    keys.append("__overall__")
    for key in keys:
        try:
            payload = context_from_metrics(
                metrics,
                locale_key=key,
                include_all_locales=False,
            )
            path = gcs_slim_locale_path(audit_dir, key)
            path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        except Exception:
            log.debug("Failed writing GCS slim blob for %s / %s", audit_dir, key, exc_info=True)


def read_gcs_slim_locale_blob(audit_dir: Path, locale_key: str) -> dict[str, Any] | None:
    path = gcs_slim_locale_path(audit_dir, locale_key)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def attach_cdn_url(resp: dict[str, Any], audit_dir: Path, locale_key: str) -> dict[str, Any]:
    try:
        from api.cache import gcs_slim_cache_enabled, slim_metrics_cdn_base_url
    except Exception:
        return resp
    base = slim_metrics_cdn_base_url()
    if not base or not gcs_slim_cache_enabled():
        return resp
    path = gcs_slim_locale_path(audit_dir, locale_key)
    if not path.is_file():
        return resp
    # Relative path under GEO_DATA_ROOT when present; else filename only.
    try:
        rel = path.name
        geo_root = (os.getenv("GEO_DATA_ROOT") or "").strip()
        if geo_root:
            try:
                rel = str(path.resolve().relative_to(Path(geo_root).resolve()))
            except ValueError:
                rel = path.name
        resp = {**resp, "metrics_cdn_url": f"{base}/{rel.lstrip('/')}"}
    except Exception:
        pass
    return resp


def get_cached_slim_context(
    audit_dir: Path,
    *,
    locale_key: str | None,
    include_all_locales: bool,
) -> dict[str, Any] | None:
    """Redis → optional GCS slim blob for a locale-scoped response."""
    audit_id = audit_dir.name
    mtime = probe_mtime(audit_dir)
    key = slim_response_cache_key(
        audit_id,
        locale_key=locale_key,
        include_all_locales=include_all_locales,
        version=METRICS_VERSION,
        probe_mtime_value=mtime,
    )
    try:
        from api.cache import cache_get

        hit = cache_get(key)
        if isinstance(hit, dict):
            hit = {**hit, "metrics_from_redis": True}
            return hit
    except Exception:
        log.debug("Redis slim GET failed", exc_info=True)

    if not include_all_locales and locale_key:
        blob = read_gcs_slim_locale_blob(audit_dir, str(locale_key))
        if isinstance(blob, dict):
            return {**blob, "metrics_from_gcs_slim": True}
    return None


def put_cached_slim_context(
    audit_dir: Path,
    resp: dict[str, Any],
    *,
    locale_key: str | None,
    include_all_locales: bool,
) -> None:
    audit_id = audit_dir.name
    mtime = probe_mtime(audit_dir)
    key = slim_response_cache_key(
        audit_id,
        locale_key=locale_key,
        include_all_locales=include_all_locales,
        version=METRICS_VERSION,
        probe_mtime_value=mtime,
    )
    try:
        from api.cache import cache_set

        cache_set(key, resp)
    except Exception:
        log.debug("Redis slim SET failed", exc_info=True)


def read_metrics_file(audit_dir: Path) -> dict[str, Any] | None:
    from api.audit_json_cache import load_json_cached

    raw = load_json_cached(metrics_path(audit_dir))
    return raw if isinstance(raw, dict) else None


def metrics_are_fresh(audit_dir: Path, metrics: dict[str, Any] | None) -> bool:
    if not isinstance(metrics, dict) or int(metrics.get("version") or 0) != METRICS_VERSION:
        return False
    current = probe_mtime(audit_dir)
    stored = metrics.get("probe_mtime")
    if current is None:
        return True  # no probe file — metrics metadata still usable
    try:
        return stored is not None and abs(float(stored) - float(current)) < 0.001
    except (TypeError, ValueError):
        return False


def persist_metrics_from_full_context(audit_dir: Path, ctx: dict[str, Any]) -> dict[str, Any]:
    """Persist slim metrics after a probe write / sanitize pass."""
    payload = build_metrics_payload_from_context(ctx, probe_mtime_value=probe_mtime(audit_dir))
    # Prefer canonical SOV / visibility rules from the probe file (website-backed filters).
    try:
        from api.geo_services import load_prompt_visibility_metrics

        canonical = load_prompt_visibility_metrics(audit_dir, prefer_persisted=False)
        if isinstance(canonical, dict) and canonical:
            payload["overall_metrics"] = canonical
    except Exception:
        log.exception("Could not attach canonical overall_metrics for %s", audit_dir)
    write_metrics_file(audit_dir, payload)
    try:
        persist_reply_shards_from_context(audit_dir, ctx)
    except Exception:
        log.exception("Failed to persist prompt reply shards for %s", audit_dir)
    return payload


def ensure_persisted_metrics(
    audit_dir: Path,
    *,
    build_full_context: Any,
) -> dict[str, Any] | None:
    """Return fresh metrics; lazily backfill by building full context once if stale/missing."""
    existing = read_metrics_file(audit_dir)
    if metrics_are_fresh(audit_dir, existing):
        return existing
    probe = audit_dir / LIVE_PROBE_FILE
    if not probe.is_file():
        return existing
    try:
        ctx = build_full_context(audit_dir)
    except Exception:
        log.exception("Failed to backfill prompt performance metrics for %s", audit_dir)
        return existing
    if not isinstance(ctx, dict):
        return existing
    try:
        return persist_metrics_from_full_context(audit_dir, ctx)
    except Exception:
        log.exception("Failed to write prompt performance metrics for %s", audit_dir)
        return existing


def context_from_metrics(
    metrics: dict[str, Any],
    *,
    locale_key: str | None = None,
    include_all_locales: bool = False,
) -> dict[str, Any]:
    """Assemble a PromptPerformanceContext-shaped response from persisted metrics.

    By default only one locale's slim ``live_probe`` / ``locale_probes`` entry is
    included (default locale, or ``locale_key``). Pass ``include_all_locales`` for
    Overall merge on the client, or ``locale_key='__overall__'`` to ship a merged
    overall live_probe without per-locale reply blobs.
    """
    default_key = str(metrics.get("default_locale_key") or "")
    locales = metrics.get("locales") if isinstance(metrics.get("locales"), dict) else {}
    requested = locale_key if locale_key is not None else default_key

    locale_probes: dict[str, Any] = {}
    live_probe = metrics.get("live_probe")

    if include_all_locales or requested in (None, "", "__all__"):
        for key, entry in locales.items():
            if isinstance(entry, dict):
                locale_probes[str(key)] = {
                    "locale": entry.get("locale"),
                    "live_probe": entry.get("live_probe"),
                    "prompts_probed": entry.get("prompts_probed") or [],
                    "source_prompts": entry.get("source_prompts") or [],
                }
        if default_key and default_key in locale_probes:
            live_probe = locale_probes[default_key].get("live_probe")
    elif requested == "__overall__":
        # Small merge of slim per-prompt rows across locales (no reply bodies).
        merged_rows: list[dict[str, Any]] = []
        brand_tokens: list[str] = []
        active: list[str] = []
        top_sites: list[Any] = []
        top_urls: list[Any] = []
        reply_brands: list[Any] = []
        keyword = None
        for key, entry in locales.items():
            if not isinstance(entry, dict):
                continue
            live = entry.get("live_probe") if isinstance(entry.get("live_probe"), dict) else None
            if not live:
                continue
            if not brand_tokens:
                brand_tokens = list(live.get("brand_match_tokens") or [])
            if not active:
                active = list(live.get("active_platforms") or [])
            if keyword is None:
                keyword = live.get("keyword_sentiment")
            for row in live.get("per_prompt") or []:
                if isinstance(row, dict):
                    merged_rows.append({**row, "_locale_key": key})
            top_sites.extend(live.get("top_cited_sites") or [])
            top_urls.extend(live.get("top_cited_urls") or [])
            reply_brands.extend(live.get("reply_detected_brands") or [])
            locale_probes[str(key)] = {
                "locale": entry.get("locale"),
                "live_probe": {
                    "per_prompt": [],
                    "prompt_count": len(live.get("per_prompt") or []),
                    "replies_omitted": True,
                    "metrics_only": True,
                },
                "prompts_probed": entry.get("prompts_probed") or [],
                "source_prompts": entry.get("source_prompts") or [],
            }
        live_probe = {
            "per_prompt": merged_rows,
            "brand_match_tokens": brand_tokens,
            "active_platforms": active,
            "top_cited_sites": top_sites[:50],
            "top_cited_urls": top_urls[:50],
            "reply_detected_brands": reply_brands[:100],
            "keyword_sentiment": keyword or _keyword_sentiment_from_list_metrics(merged_rows),
            "replies_omitted": True,
            "disclaimer": "Overall combines successful market/language probe runs.",
            "prompt_count": len(merged_rows),
        }
    else:
        key = str(requested or default_key)
        entry = locales.get(key) if key in locales else None
        if isinstance(entry, dict):
            locale_probes[key] = {
                "locale": entry.get("locale"),
                "live_probe": entry.get("live_probe"),
                "prompts_probed": entry.get("prompts_probed") or [],
                "source_prompts": entry.get("source_prompts") or [],
            }
            live_probe = entry.get("live_probe")
        elif isinstance(live_probe, dict):
            pass
        # Always include stub entries so locale filter knows markets exist.
        for other_key, other in locales.items():
            if other_key in locale_probes or not isinstance(other, dict):
                continue
            live = other.get("live_probe") if isinstance(other.get("live_probe"), dict) else {}
            locale_probes[str(other_key)] = {
                "locale": other.get("locale"),
                "live_probe": {
                    "per_prompt": [],
                    "prompt_count": int(live.get("prompt_count") or len(live.get("per_prompt") or [])),
                    "replies_omitted": True,
                    "metrics_only": True,
                    "brand_match_tokens": live.get("brand_match_tokens") or [],
                    "active_platforms": live.get("active_platforms") or [],
                },
                "prompts_probed": other.get("prompts_probed") or [],
                "source_prompts": other.get("source_prompts") or [],
            }

    resp = {
        "brand_name": metrics.get("brand_name") or "",
        "brand_site_url": metrics.get("brand_site_url") or "",
        "use_pss": bool(metrics.get("use_pss")),
        "pss_rows": metrics.get("pss_rows") or [],
        "probed_pss_rows": metrics.get("probed_pss_rows") or [],
        "flat_prompts": metrics.get("flat_prompts") or [],
        "prompt_count": metrics.get("prompt_count") or 0,
        "stored_prompt_count": metrics.get("stored_prompt_count"),
        "competitors": metrics.get("competitors") or [],
        "primary_market": metrics.get("primary_market") or {"country": "", "country_id": ""},
        "prompt_locales": metrics.get("prompt_locales") or [],
        "default_locale_key": default_key,
        "locale_probes": locale_probes,
        "locale_spread": metrics.get("locale_spread") or [],
        "category_labels": metrics.get("category_labels") or [],
        "industry": metrics.get("industry") or "",
        "live_probe": live_probe if isinstance(live_probe, dict) else None,
        "live_probe_in_progress": bool(metrics.get("live_probe_in_progress")),
        "aio_probe": metrics.get("aio_probe"),
        "aio_probe_in_progress": bool(metrics.get("aio_probe_in_progress")),
        "highlight": metrics.get("highlight") or {},
        "sov_history": metrics.get("sov_history") or [],
        "sov_history_by_product": metrics.get("sov_history_by_product") or {},
        "overall_metrics": metrics.get("overall_metrics"),
        "metrics_from_cache": True,
        "replies_omitted": True,
    }
    sanitize_context_runs_inplace(resp)
    return resp


def reply_shard_filename(prompt_id: str) -> str:
    """Filesystem-safe filename for a prompt_id (keeps enough uniqueness)."""
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (prompt_id or "").strip())
    return (safe[:180] or "prompt") + ".json"


def replies_locale_dir(audit_dir: Path, locale_key: str) -> Path:
    from api.prompt_jobs import locale_key_filename

    return audit_dir / REPLIES_DIR / locale_key_filename(locale_key or "unknown")


def reply_shard_path(audit_dir: Path, locale_key: str, prompt_id: str) -> Path:
    return replies_locale_dir(audit_dir, locale_key) / reply_shard_filename(prompt_id)


def _enrich_prompt_row(row: dict[str, Any], *, index: int, locale_key: str, prompt_id: str) -> dict[str, Any]:
    return {
        **row,
        "index": row.get("index", index),
        "prompt_id": prompt_id,
        "_locale_key": locale_key,
        "replies_omitted": False,
    }


def _write_reply_shard(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from api.prompt_jobs import _write_json

    _write_json(path, row)


def persist_reply_shards_from_context(audit_dir: Path, ctx: dict[str, Any]) -> int:
    """Write one small JSON file per prompt×locale with full reply bodies.

    Called when slim metrics are persisted so overlay detail GETs can avoid
    reading the multi-locale live probe blob from GCS FUSE.
    """
    import shutil

    locale_probes = ctx.get("locale_probes") if isinstance(ctx.get("locale_probes"), dict) else {}
    written = 0
    root = audit_dir / REPLIES_DIR
    # Drop stale shards from previous probe runs before rewriting.
    if root.is_dir():
        try:
            shutil.rmtree(root)
        except OSError:
            log.warning("Could not clear reply shards under %s", root, exc_info=True)

    sources: list[tuple[str, dict[str, Any]]] = []
    for key, entry in locale_probes.items():
        if not isinstance(entry, dict):
            continue
        live = entry.get("live_probe") if isinstance(entry.get("live_probe"), dict) else None
        if live:
            sources.append((str(key), live))
    if not sources:
        default_key = str(ctx.get("default_locale_key") or "")
        default_live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else None
        if default_live:
            sources.append((default_key or "default", default_live))

    for loc, live in sources:
        per = live.get("per_prompt") if isinstance(live.get("per_prompt"), list) else []
        for i, row in enumerate(per):
            if not isinstance(row, dict):
                continue
            # Skip already-slim rows (no reply bodies to shard).
            if row.get("replies_omitted") and not any(
                str(row.get(f"{pk}_response") or "").strip() for pk in _PLATFORM_KEYS
            ):
                runs = row.get("runs") if isinstance(row.get("runs"), dict) else {}
                has_run_body = any(
                    isinstance(r, dict) and str(r.get("response") or "").strip()
                    for entries in runs.values()
                    if isinstance(entries, list)
                    for r in entries
                )
                if not has_run_body:
                    continue
            prompt = str(row.get("prompt") or "").strip()
            pid = str(row.get("prompt_id") or "").strip() or prompt_id_for(i, prompt, loc)
            payload = _enrich_prompt_row(row, index=i, locale_key=loc, prompt_id=pid)
            try:
                _write_reply_shard(reply_shard_path(audit_dir, loc, pid), payload)
                written += 1
            except Exception:
                log.exception("Failed writing reply shard for %s / %s", loc, pid)
    return written


def read_reply_shard(
    audit_dir: Path,
    *,
    locale_key: str,
    prompt_id: str,
) -> dict[str, Any] | None:
    if not locale_key or not prompt_id:
        return None
    candidates = [prompt_id]
    if "|" not in prompt_id:
        candidates.append(f"{locale_key}|{prompt_id}")
    else:
        bare = prompt_id.split("|", 1)[-1]
        if bare:
            candidates.append(f"{locale_key}|{bare}")
    path = None
    for pid in candidates:
        candidate = reply_shard_path(audit_dir, locale_key, pid)
        if candidate.is_file():
            path = candidate
            break
    if path is None:
        return None
    from api.audit_json_cache import load_json_cached

    raw = load_json_cached(path)
    return raw if isinstance(raw, dict) else None


def _search_live_for_prompt(
    live: dict[str, Any],
    loc: str,
    *,
    prompt_id: str | None = None,
    prompt_index: int | None = None,
    prompt_text: str = "",
    locale_key: str = "",
) -> dict[str, Any] | None:
    per = live.get("per_prompt") if isinstance(live.get("per_prompt"), list) else []
    for i, row in enumerate(per):
        if not isinstance(row, dict):
            continue
        prompt = str(row.get("prompt") or "").strip()
        pid = prompt_id_for(i, prompt, loc)
        bare = pid.split("|", 1)[-1]
        if prompt_id:
            if prompt_id == pid:
                return _enrich_prompt_row(row, index=i, locale_key=loc, prompt_id=pid)
            if locale_key:
                # Market selected: only bare id or this locale's prefixed id (no cross-locale).
                if prompt_id == bare or prompt_id == f"{loc}|{bare}":
                    return _enrich_prompt_row(row, index=i, locale_key=loc, prompt_id=pid)
            elif (
                prompt_id.endswith("|" + bare)
                or prompt_id == bare
                or prompt_id == prompt_id_for(i, prompt, "")
            ):
                return _enrich_prompt_row(row, index=i, locale_key=loc, prompt_id=pid)
        if prompt_index is not None and int(prompt_index) == i and (not locale_key or locale_key == loc):
            return _enrich_prompt_row(row, index=i, locale_key=loc, prompt_id=pid)
        if prompt_text and prompt.lower() == prompt_text.strip().lower() and (
            not locale_key or locale_key == loc
        ):
            return _enrich_prompt_row(row, index=i, locale_key=loc, prompt_id=pid)
    return None


def _load_locale_probe_artifact(audit_dir: Path, locale_key: str) -> dict[str, Any] | None:
    """Load a single-locale probe artifact (much smaller than the merged multi-locale file)."""
    if not locale_key:
        return None
    try:
        from api.prompt_jobs import locale_probe_artifact_path
    except Exception:
        return None
    path = locale_probe_artifact_path(audit_dir, locale_key)
    if not path.is_file():
        return None
    from api.audit_json_cache import load_json_cached

    raw = load_json_cached(path)
    if not isinstance(raw, dict):
        return None
    live = raw.get("live_probe") if isinstance(raw.get("live_probe"), dict) else None
    return live if isinstance(live, dict) else None


def _maybe_backfill_reply_shard(audit_dir: Path, row: dict[str, Any]) -> None:
    """Best-effort write of a shard after a cold hit so the next overlay open is fast."""
    loc = str(row.get("_locale_key") or "").strip()
    pid = str(row.get("prompt_id") or "").strip()
    if not loc or not pid:
        return
    path = reply_shard_path(audit_dir, loc, pid)
    if path.is_file():
        return
    try:
        _write_reply_shard(path, {**row, "replies_omitted": False})
    except Exception:
        log.debug("Reply shard backfill failed for %s / %s", loc, pid, exc_info=True)


def find_full_prompt_row(
    audit_dir: Path,
    *,
    prompt_id: str | None = None,
    locale_key: str = "",
    prompt_index: int | None = None,
    prompt_text: str = "",
) -> dict[str, Any] | None:
    """Load the full (with replies) per_prompt row.

    Prefer order (fast → slow):
    1. Per-prompt reply shard (tiny GCS read)
    2. Per-locale probe artifact (single market, not multi-locale merge)
    3. Canonical multi-locale ``prompt_performance_live_probe.json`` (last resort)
    """
    loc = (locale_key or "").strip()
    # Infer locale from prompt_id when the client omitted it (Overall table).
    if not loc and prompt_id and "|" in prompt_id:
        loc = prompt_id.split("|", 1)[0].strip()

    if loc and prompt_id:
        shard = read_reply_shard(audit_dir, locale_key=loc, prompt_id=prompt_id)
        if shard:
            return shard

    if loc:
        live = _load_locale_probe_artifact(audit_dir, loc)
        if isinstance(live, dict):
            found = _search_live_for_prompt(
                live,
                loc,
                prompt_id=prompt_id,
                prompt_index=prompt_index,
                prompt_text=prompt_text,
                locale_key=loc,
            )
            if found:
                _maybe_backfill_reply_shard(audit_dir, found)
                return found

    from api.audit_json_cache import load_json_cached

    raw = load_json_cached(audit_dir / LIVE_PROBE_FILE)
    if not isinstance(raw, dict):
        return None

    locale_probes = raw.get("locale_probes") if isinstance(raw.get("locale_probes"), dict) else {}

    # When a market is selected, only search that locale — never parse/merge others.
    if loc and loc in locale_probes:
        entry = locale_probes[loc]
        live = entry.get("live_probe") if isinstance(entry, dict) else None
        if isinstance(live, dict):
            found = _search_live_for_prompt(
                live,
                loc,
                prompt_id=prompt_id,
                prompt_index=prompt_index,
                prompt_text=prompt_text,
                locale_key=loc,
            )
            if found:
                _maybe_backfill_reply_shard(audit_dir, found)
                return found
        # Locale requested but row missing — do not spill into other markets.
        return None

    if loc:
        # Locale key set but not present in merged file (artifact already tried).
        return None

    # No locale: search all locales then default live_probe (Overall / legacy).
    for key, entry in locale_probes.items():
        if not isinstance(entry, dict):
            continue
        live = entry.get("live_probe")
        if isinstance(live, dict):
            found = _search_live_for_prompt(
                live,
                str(key),
                prompt_id=prompt_id,
                prompt_index=prompt_index,
                prompt_text=prompt_text,
                locale_key="",
            )
            if found:
                _maybe_backfill_reply_shard(audit_dir, found)
                return found

    live = raw.get("live_probe")
    if isinstance(live, dict):
        found = _search_live_for_prompt(
            live,
            str(raw.get("default_locale_key") or ""),
            prompt_id=prompt_id,
            prompt_index=prompt_index,
            prompt_text=prompt_text,
            locale_key="",
        )
        if found:
            _maybe_backfill_reply_shard(audit_dir, found)
        return found
    return None


# ── Citations page slim payload ───────────────────────────────────────────────

_MULTI_PART_PUBLIC_SUFFIXES = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk",
    "com.au", "net.au", "org.au",
    "co.nz", "co.za", "co.in", "co.jp",
    "com.br", "com.sg", "com.hk", "com.mx",
})


def _website_domain(website: str | None) -> str:
    raw = str(website or "").strip()
    if not raw:
        return ""
    try:
        from urllib.parse import urlparse

        host = urlparse(raw if "://" in raw else f"https://{raw}").hostname or ""
        return host.lower().removeprefix("www.")
    except Exception:
        return ""


def registrable_domain(raw_domain: str) -> str:
    hostname = _website_domain(raw_domain) or str(raw_domain or "").lower().removeprefix("www.")
    hostname = hostname.split("/")[0].split("?")[0]
    parts = [p for p in hostname.split(".") if p]
    if len(parts) <= 2:
        return hostname
    suffix = ".".join(parts[-2:])
    if suffix in _MULTI_PART_PUBLIC_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _has_page_path(raw_url: str) -> bool:
    try:
        from urllib.parse import urlparse

        url = raw_url if "://" in raw_url else f"https://{raw_url}"
        path = (urlparse(url).path or "").strip("/")
        return bool(path)
    except Exception:
        return False


def _citation_eligible(
    domain: str,
    *,
    brand_domains: set[str],
    brand_stems: set[str],
) -> bool:
    d = str(domain or "").lower().removeprefix("www.")
    if not d or _is_vendor_domain_local(d):
        return False
    if d in brand_domains:
        return False
    if _stem_brand_label_local(d) in brand_stems:
        return False
    return True


def _mention_summary(row: dict[str, Any], platform: str) -> tuple[bool, bool, list[str]]:
    scores = row.get(f"mention_scores_{platform}") or {}
    if not isinstance(scores, dict):
        return False, False, []
    detail = scores.get("competitor_detail") or {}
    names = [k for k, v in detail.items() if int(v or 0) > 0] if isinstance(detail, dict) else []
    brand = int(scores.get("brand_signal") or 0) > 0
    competitor = bool(names) or int(scores.get("competitors_combined_hits") or 0) > 0
    return brand, competitor, names


def _aggregate_citation_sites_urls(
    per_prompt: list[dict[str, Any]],
    *,
    brand_domains: set[str],
    brand_stems: set[str],
    platforms: tuple[str, ...] = _PLATFORM_KEYS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sites: dict[str, dict[str, Any]] = {}
    urls: dict[str, dict[str, Any]] = {}

    for row in per_prompt:
        if not isinstance(row, dict):
            continue
        for platform in platforms:
            citations = row.get(f"citations_{platform}") or []
            if not isinstance(citations, list):
                continue
            brand_m, comp_m, comp_names = _mention_summary(row, platform)
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                domain_raw = str(citation.get("domain") or "").lower().removeprefix("www.")
                if not _citation_eligible(
                    domain_raw, brand_domains=brand_domains, brand_stems=brand_stems
                ):
                    continue
                brand_hit = bool(citation.get("brand_cited")) or brand_m
                comp_hit = bool(citation.get("competitor_cited")) or comp_m
                root = registrable_domain(domain_raw)
                if root:
                    existing = sites.get(root)
                    if not existing:
                        sites[root] = {
                            "domain": root,
                            "count": 1,
                            "platforms": [platform],
                            "example_url": citation.get("url") or f"https://{root}",
                            "title": citation.get("title"),
                            "thumbnail_url": citation.get("thumbnail_url"),
                            "views": citation.get("views"),
                            "platform": platform,
                            "brand_mentioned": brand_hit,
                            "competitor_mentioned": comp_hit,
                            "competitor_names": list(comp_names),
                            "unresolved_redirect": citation.get("unresolved_redirect"),
                        }
                    else:
                        existing["count"] = int(existing.get("count") or 0) + 1
                        plats = existing.setdefault("platforms", [])
                        if platform not in plats:
                            plats.append(platform)
                        existing["brand_mentioned"] = bool(
                            existing.get("brand_mentioned") or brand_hit
                        )
                        existing["competitor_mentioned"] = bool(
                            existing.get("competitor_mentioned") or comp_hit
                        )
                        names = list(existing.get("competitor_names") or [])
                        for name in comp_names:
                            if name not in names:
                                names.append(name)
                        existing["competitor_names"] = names

                url = str(citation.get("url") or "").strip()
                if not url or not _has_page_path(url):
                    continue
                key = url.lower().rstrip("/")
                existing_url = urls.get(key)
                if not existing_url:
                    urls[key] = {
                        "url": url,
                        "domain": domain_raw or registrable_domain(url),
                        "title": citation.get("title"),
                        "thumbnail_url": citation.get("thumbnail_url"),
                        "views": citation.get("views"),
                        "platform": platform,
                        "frequency": 1,
                        "probe_platforms": [platform],
                        "brand_mentioned": brand_hit,
                        "competitor_mentioned": comp_hit,
                        "competitor_names": list(comp_names),
                        "unresolved_redirect": citation.get("unresolved_redirect"),
                    }
                else:
                    existing_url["frequency"] = int(existing_url.get("frequency") or 0) + 1
                    plats = existing_url.setdefault("probe_platforms", [])
                    if platform not in plats:
                        plats.append(platform)
                    existing_url["brand_mentioned"] = bool(
                        existing_url.get("brand_mentioned") or brand_hit
                    )
                    existing_url["competitor_mentioned"] = bool(
                        existing_url.get("competitor_mentioned") or comp_hit
                    )
                    names = list(existing_url.get("competitor_names") or [])
                    for name in comp_names:
                        if name not in names:
                            names.append(name)
                    existing_url["competitor_names"] = names

    top_sites = sorted(sites.values(), key=lambda x: -int(x.get("count") or 0))
    top_urls = sorted(urls.values(), key=lambda x: -int(x.get("frequency") or 0))
    return top_sites, top_urls


def _aggregate_aio_urls(
    aio: dict[str, Any] | None,
    *,
    brand_domains: set[str],
    brand_stems: set[str],
) -> list[dict[str, Any]]:
    if not isinstance(aio, dict):
        return []
    rows = aio.get("per_prompt") or []
    if not isinstance(rows, list):
        return []
    urls: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for citation in row.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            domain_raw = str(citation.get("domain") or "").lower().removeprefix("www.")
            if not _citation_eligible(
                domain_raw, brand_domains=brand_domains, brand_stems=brand_stems
            ):
                continue
            url = str(citation.get("url") or "").strip()
            if not url or not _has_page_path(url):
                continue
            key = url.lower().rstrip("/")
            existing = urls.get(key)
            if not existing:
                urls[key] = {
                    "url": url,
                    "domain": domain_raw,
                    "title": citation.get("title"),
                    "thumbnail_url": citation.get("thumbnail_url"),
                    "views": citation.get("views"),
                    "platform": citation.get("platform"),
                    "channel_type": "Google AIO",
                    "frequency": 1,
                    "probe_platforms": ["google_aio"],
                    "brand_mentioned": False,
                    "competitor_mentioned": False,
                    "competitor_names": [],
                    "unresolved_redirect": citation.get("unresolved_redirect"),
                }
            else:
                existing["frequency"] = int(existing.get("frequency") or 0) + 1
    return sorted(urls.values(), key=lambda x: -int(x.get("frequency") or 0))


def _brand_entity_sets(metrics: dict[str, Any]) -> tuple[set[str], set[str]]:
    brand_domains: set[str] = set()
    brand_stems: set[str] = set()
    site = _website_domain(str(metrics.get("brand_site_url") or ""))
    name = str(metrics.get("brand_name") or "").strip()
    if site:
        brand_domains.add(site)
        stem = _stem_brand_label_local(site)
        if stem:
            brand_stems.add(stem)
    if name:
        stem = _stem_brand_label_local(name)
        if stem:
            brand_stems.add(stem)
    return brand_domains, brand_stems


def _preferred_citations_locale(metrics: dict[str, Any]) -> str:
    """Mirror web preferredInitialLocaleKey for huge multi-locale audits."""
    locales = metrics.get("prompt_locales") if isinstance(metrics.get("prompt_locales"), list) else []
    locale_keys = [
        str(loc.get("key") or "").strip()
        for loc in locales
        if isinstance(loc, dict) and str(loc.get("key") or "").strip()
    ]
    prompt_count = int(metrics.get("prompt_count") or metrics.get("stored_prompt_count") or 0)
    n_locales = len(locale_keys)
    huge = (
        n_locales >= 2
        and (
            n_locales >= 3
            or prompt_count >= 40
            or (prompt_count * n_locales) >= 80
        )
    )
    if not huge:
        return "__overall__"
    default_key = str(metrics.get("default_locale_key") or "").strip()
    if default_key and default_key in locale_keys:
        return default_key
    return locale_keys[0] if locale_keys else "__overall__"


def _live_rows_for_locale(
    metrics: dict[str, Any],
    locale_key: str,
) -> tuple[list[dict[str, Any]], str]:
    locales = metrics.get("locales") if isinstance(metrics.get("locales"), dict) else {}
    default_key = str(metrics.get("default_locale_key") or "")
    requested = locale_key or default_key or "__overall__"

    if requested in ("__overall__", "overall"):
        rows: list[dict[str, Any]] = []
        for key, entry in locales.items():
            if not isinstance(entry, dict):
                continue
            live = entry.get("live_probe") if isinstance(entry.get("live_probe"), dict) else None
            if not live:
                continue
            for row in live.get("per_prompt") or []:
                if isinstance(row, dict):
                    rows.append({**row, "_locale_key": key})
        if rows:
            return rows, "__overall__"
        live = metrics.get("live_probe") if isinstance(metrics.get("live_probe"), dict) else {}
        return [r for r in (live.get("per_prompt") or []) if isinstance(r, dict)], "__overall__"

    entry = locales.get(requested) if requested in locales else None
    if isinstance(entry, dict):
        live = entry.get("live_probe") if isinstance(entry.get("live_probe"), dict) else {}
        rows = [r for r in (live.get("per_prompt") or []) if isinstance(r, dict)]
        return rows, requested

    live = metrics.get("live_probe") if isinstance(metrics.get("live_probe"), dict) else {}
    return [r for r in (live.get("per_prompt") or []) if isinstance(r, dict)], requested


# Citations page / slim endpoint caps (Overall merges use the same limits).
CITATIONS_TOP_SITES = 15
CITATIONS_TOP_URLS = 30

# Mirror api.geo_services vendor list (keep Citations GET free of that heavy import).
_CITATION_VENDOR_DOMAINS = frozenset({
    "boots.com", "superdrug.com", "lookfantastic.com", "cultbeauty.co.uk",
    "spacenk.com", "beautybay.com", "feelunique.com", "allbeauty.com", "pharmaca.com",
    "johnlewis.com", "marksandspencer.com", "selfridges.com", "harrods.com",
    "libertylondon.com", "debenhams.com", "next.co.uk", "nextdirect.com",
    "tkmaxx.com", "tkmaxx.co.uk", "hmv.com",
    "tesco.com", "sainsburys.co.uk", "asda.com", "waitrose.com", "ocado.com",
    "morrisons.com", "aldi.co.uk", "lidl.co.uk", "iceland.co.uk",
    "asos.com", "asos.co.uk", "zalando.co.uk", "zalando.com", "farfetch.com",
    "net-a-porter.com", "matchesfashion.com", "notonthehighstreet.com", "etsy.com",
    "sephora.com", "ulta.com", "cvs.com", "walgreens.com", "target.com",
    "walmart.com", "costco.com",
    "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au", "amazon.de", "amazon.fr",
    "macys.com", "nordstrom.com", "bloomingdales.com", "kohls.com", "jcpenney.com",
    "ebay.com", "ebay.co.uk",
    "tripadvisor.com", "opentable.com", "bookatable.co.uk", "booking.com",
    "hotels.com", "airbnb.com", "expedia.com",
    "deliveroo.co.uk", "ubereats.com", "just-eat.co.uk", "doordash.com",
    "hollandandbarrett.com", "chemistdirect.co.uk", "pharmacy2u.co.uk", "nhs.uk",
    "diy.com", "screwfix.com", "homebase.co.uk", "wickes.co.uk", "dunelm.com",
    "argos.co.uk", "ikea.com",
})


def _stem_brand_label_local(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\.(com|co\.uk|co|org|net|io|uk|au|ca|de|fr|es|it)(\.[a-z]{2})?$", "", text)
    text = re.sub(r"^www\.", "", text)
    return re.sub(r"[\s\-_'.]+", "", text)


def _is_vendor_domain_local(domain: str) -> bool:
    d = str(domain or "").lower().removeprefix("www.")
    if not d:
        return False
    if d in _CITATION_VENDOR_DOMAINS:
        return True
    return any(d.endswith(f".{vendor}") for vendor in _CITATION_VENDOR_DOMAINS)


def build_citations_view_payload(
    metrics: dict[str, Any],
    *,
    locale_key: str | None = None,
) -> dict[str, Any]:
    """Tiny Citations-page payload: aggregated domains/URLs only (no per_prompt).

    Response is capped at ``CITATIONS_TOP_SITES`` domains and
    ``CITATIONS_TOP_URLS`` URLs (including Google AIO URLs).
    """
    brand_domains, brand_stems = _brand_entity_sets(metrics)
    requested = (locale_key or "").strip() or _preferred_citations_locale(metrics)
    rows, resolved = _live_rows_for_locale(metrics, requested)
    top_sites, top_urls = _aggregate_citation_sites_urls(
        rows,
        brand_domains=brand_domains,
        brand_stems=brand_stems,
    )
    aio_urls = _aggregate_aio_urls(
        metrics.get("aio_probe") if isinstance(metrics.get("aio_probe"), dict) else None,
        brand_domains=brand_domains,
        brand_stems=brand_stems,
    )
    locale_spread = metrics.get("locale_spread") or []
    return {
        "brand_name": metrics.get("brand_name") or "",
        "brand_site_url": metrics.get("brand_site_url") or "",
        "competitors": metrics.get("competitors") or [],
        "primary_market": metrics.get("primary_market") or {"country": "", "country_id": ""},
        "prompt_locales": metrics.get("prompt_locales") or [],
        "default_locale_key": metrics.get("default_locale_key") or "",
        "locale_spread": locale_spread,
        "locale_key": resolved,
        "prompt_count": len(rows),
        "has_probe_data": bool(rows),
        "top_cited_sites": top_sites[:CITATIONS_TOP_SITES],
        "top_cited_urls": top_urls[:CITATIONS_TOP_URLS],
        "aio_cited_urls": aio_urls[:CITATIONS_TOP_URLS],
        "metrics_from_cache": True,
    }


def build_citations_view_bundle(metrics: dict[str, Any]) -> dict[str, Any]:
    """Precompute capped Citations payloads for Overall + each configured locale."""
    preferred = _preferred_citations_locale(metrics)
    locales = metrics.get("prompt_locales") if isinstance(metrics.get("prompt_locales"), list) else []
    locale_keys = [
        str(loc.get("key") or "").strip()
        for loc in locales
        if isinstance(loc, dict) and str(loc.get("key") or "").strip()
    ]
    keys = ["__overall__", *locale_keys]
    # Prefer first so cold GETs without ?locale= hit a warm key.
    if preferred and preferred not in keys:
        keys.insert(0, preferred)
    payloads: dict[str, Any] = {}
    for key in keys:
        payloads[key] = build_citations_view_payload(metrics, locale_key=key)
    return {
        "version": CITATIONS_VIEW_VERSION,
        "probe_mtime": metrics.get("probe_mtime"),
        "metrics_version": int(metrics.get("version") or 0),
        "preferred_locale_key": preferred,
        "payloads": payloads,
    }


def write_citations_view_file(audit_dir: Path, metrics: dict[str, Any]) -> Path:
    """Persist the tiny Citations bundle next to metrics (GCS-friendly)."""
    path = citations_view_path(audit_dir)
    audit_dir.mkdir(parents=True, exist_ok=True)
    bundle = build_citations_view_bundle(metrics)
    path.write_text(json.dumps(bundle, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        from api.audit_json_cache import invalidate_path

        invalidate_path(path)
    except Exception:
        pass
    return path


def read_citations_view_bundle(audit_dir: Path) -> dict[str, Any] | None:
    from api.audit_json_cache import load_json_cached

    raw = load_json_cached(citations_view_path(audit_dir))
    if not isinstance(raw, dict):
        return None
    if int(raw.get("version") or 0) != CITATIONS_VIEW_VERSION:
        return None
    payloads = raw.get("payloads")
    if not isinstance(payloads, dict) or not payloads:
        return None
    return raw


def citations_view_bundle_matches_metrics(
    bundle: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
) -> bool:
    if not isinstance(bundle, dict) or not isinstance(metrics, dict):
        return False
    try:
        if int(bundle.get("metrics_version") or 0) != int(metrics.get("version") or 0):
            return False
    except (TypeError, ValueError):
        return False
    b_mtime, m_mtime = bundle.get("probe_mtime"), metrics.get("probe_mtime")
    if b_mtime is None or m_mtime is None:
        return b_mtime is None and m_mtime is None
    try:
        return abs(float(b_mtime) - float(m_mtime)) < 0.001
    except (TypeError, ValueError):
        return False


def payload_from_citations_bundle(
    bundle: dict[str, Any],
    *,
    locale_key: str | None = None,
) -> dict[str, Any] | None:
    payloads = bundle.get("payloads") if isinstance(bundle.get("payloads"), dict) else {}
    preferred = str(bundle.get("preferred_locale_key") or "").strip() or "__overall__"
    requested = (locale_key or "").strip() or preferred
    if requested in ("overall",):
        requested = "__overall__"
    hit = payloads.get(requested)
    if isinstance(hit, dict):
        return hit
    # Fall back to preferred then Overall.
    for key in (preferred, "__overall__"):
        alt = payloads.get(key)
        if isinstance(alt, dict):
            return alt
    return None


def citations_view_file_is_fresh(audit_dir: Path) -> bool:
    """True when the slim citations file exists and is not older than metrics."""
    cpath = citations_view_path(audit_dir)
    if not cpath.is_file():
        return False
    mpath = metrics_path(audit_dir)
    if not mpath.is_file():
        return True
    try:
        return cpath.stat().st_mtime >= (mpath.stat().st_mtime - 1.0)
    except OSError:
        return False


def serve_citations_view_if_fresh(
    audit_dir: Path,
    *,
    locale_key: str | None = None,
) -> dict[str, Any] | None:
    """Hot path: serve Citations without opening the multi‑MB metrics JSON."""
    if not citations_view_file_is_fresh(audit_dir):
        return None
    bundle = read_citations_view_bundle(audit_dir)
    if bundle is None:
        return None
    return payload_from_citations_bundle(bundle, locale_key=locale_key)


def get_or_build_citations_view_payload(
    audit_dir: Path,
    metrics: dict[str, Any],
    *,
    locale_key: str | None = None,
) -> dict[str, Any]:
    """Serve Citations from the slim cache file; build+persist on miss/stale."""
    bundle = read_citations_view_bundle(audit_dir)
    if citations_view_bundle_matches_metrics(bundle, metrics) and bundle is not None:
        hit = payload_from_citations_bundle(bundle, locale_key=locale_key)
        if hit is not None:
            return hit

    # Cold path: scan metrics once, persist slim bundle for subsequent GETs.
    try:
        write_citations_view_file(audit_dir, metrics)
        bundle = read_citations_view_bundle(audit_dir)
        if bundle is not None:
            hit = payload_from_citations_bundle(bundle, locale_key=locale_key)
            if hit is not None:
                return hit
    except Exception:
        log.exception("Citations view cache write failed for %s", audit_dir)

    return build_citations_view_payload(metrics, locale_key=locale_key)
