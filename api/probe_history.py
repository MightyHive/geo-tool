"""
probe_history.py — Daily probe re-run, probe history storage, and time-series API.

Storage layout (inside each audit_dir on GCS):
  prompt_performance_live_probe.json       ← always the latest run
  probe_history/
    YYYY-MM-DD.json                         ← full probe result for that day
  probe_history_index.json                 ← lightweight summary index

probe_history_index.json schema:
{
  "entries": [
    {
      "date": "2026-07-13",
      "created_at": "2026-07-13T10:00:00Z",
      "summary": {
        "gemini":      {"brand_visibility": 0.6, "avg_competitor_visibility": 0.4},
        "openai":      {"brand_visibility": 0.8, "avg_competitor_visibility": 0.3},
        "google_aio":  {"brand_visibility": 0.7, "avg_competitor_visibility": 0.35},
        "top_cited_domains": ["cerave.co.uk", "boots.com", "superdrug.com"]
      }
    }
  ]
}
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
import os

from api import geo_services as geo

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audits", tags=["probe-history"])

_PLATFORMS = ("gemini", "openai", "claude", "google_aio")
_HISTORY_DIR = "probe_history"
_INDEX_FILE = "probe_history_index.json"
_LIVE_FILE = "prompt_performance_live_probe.json"

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


def _response_sentiment(text: str, brand_tokens: list[str]) -> str:
    """Mirror the prompt UI's ±280-character brand-context classifier."""
    lower = text.lower()
    positions: list[int] = []
    for token in brand_tokens:
        start = lower.find(token)
        while start >= 0:
            positions.append(start)
            start = lower.find(token, start + 1)
    positive = negative = 0
    for position in positions:
        window = lower[max(0, position - 280):position + 280]
        positive += sum(word in window for word in _POSITIVE_WORDS)
        negative += sum(word in window for word in _NEGATIVE_WORDS)
    if positive > negative:
        return "positive"
    if negative > positive:
        return "negative"
    return "neutral"


# ── Storage helpers ───────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_index(audit_dir: Path) -> dict[str, Any]:
    return _read_json(audit_dir / _INDEX_FILE)


def _save_index(audit_dir: Path, index: dict[str, Any]) -> None:
    _write_json(audit_dir / _INDEX_FILE, index)


# ── Summary extraction from a full probe result ───────────────────────────────

def _build_daily_summary(
    live_probe: dict[str, Any],
    topic_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Extract lightweight per-platform visibility + top domains from a probe result."""
    data = live_probe.get("live_probe", live_probe) if isinstance(live_probe, dict) else {}
    saved_top_sites = data.get("top_cited_sites") or []
    try:
        from prompt_suggest import recompute_live_probe_mention_scores

        data = recompute_live_probe_mention_scores(data)
    except Exception as exc:
        log.warning("Could not refresh mention scores for probe history: %s", exc)
    per_prompt: list[dict[str, Any]] = data.get("per_prompt") or []

    summary: dict[str, Any] = {}
    brand_tokens = list(dict.fromkeys(
        str(token).lower()
        for token in [data.get("brand_name"), *(data.get("brand_match_tokens") or [])]
        if str(token or "").strip()
    ))

    for plat in _PLATFORMS:
        brand_hits = 0
        response_count = 0
        positive_brand_hits = 0
        comp_totals: dict[str, int] = {}
        for row in per_prompt:
            raw_runs = (row.get("runs") or {}).get(plat) or []
            completed_runs = [
                run for run in raw_runs
                if isinstance(run, dict) and run.get("response") and not run.get("error")
            ]
            response_rows = completed_runs or [{
                "response": row.get(f"{plat}_response"),
                "mention_scores": row.get(f"mention_scores_{plat}") or {},
            }]
            for run in response_rows:
                response = str(run.get("response") or "")
                if not response:
                    continue
                response_count += 1
                scores = run.get("mention_scores") or {}
                lower_response = response.lower()
                brand_mentioned = int(scores.get("brand_signal") or 0) > 0 or any(
                    token in lower_response for token in brand_tokens
                )
                if brand_mentioned:
                    brand_hits += 1
                    if _response_sentiment(response, brand_tokens) == "positive":
                        positive_brand_hits += 1
                for comp, cnt in (scores.get("competitor_detail") or {}).items():
                    if int(cnt or 0) > 0:
                        comp_totals[comp] = comp_totals.get(comp, 0) + 1

        if response_count > 0:
            brand_vis = round(brand_hits / response_count, 4)
            avg_comp_vis = (
                round(sum(comp_totals.values()) / (response_count * len(comp_totals)), 4)
                if comp_totals else 0.0
            )
        else:
            brand_vis = avg_comp_vis = 0.0

        summary[plat] = {
            "brand_visibility": brand_vis,
            "avg_competitor_visibility": avg_comp_vis,
            "competitor_detail": comp_totals,
            "competitor_visibility": {
                name: round(count / response_count, 4) if response_count else 0.0
                for name, count in comp_totals.items()
            },
            "response_count": response_count,
            "brand_mentioned_count": brand_hits,
            "positive_brand_mention_count": positive_brand_hits,
            "sentiment_score": (
                round(positive_brand_hits / brand_hits, 4) if brand_hits else None
            ),
        }

    # Top cited domains
    top_sites = saved_top_sites or data.get("top_cited_sites") or []
    summary["top_cited_domains"] = [
        {
            "domain": s.get("domain"),
            "frequency": s.get("frequency", s.get("count", 0)),
        }
        for s in top_sites[:20]
    ]

    if topic_map:
        topic_rows: dict[str, list[dict[str, Any]]] = {}
        for index, row in enumerate(per_prompt):
            topic = topic_map.get(str(row.get("prompt") or "").strip().lower())
            if not topic:
                topic = topic_map.get(f"__index_{index}")
            if topic:
                topic_rows.setdefault(topic, []).append(row)
        summary["topic_summaries"] = {
            topic: _build_daily_summary(
                {"live_probe": {**data, "per_prompt": rows}},
            )
            for topic, rows in topic_rows.items()
            if rows
        }

    return summary


def save_probe_to_history(
    audit_dir: Path,
    probe_result: dict[str, Any],
    prompt_context: dict[str, Any] | None = None,
) -> str:
    """Save a probe result to the history folder and update the index. Returns ISO date."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    created_at = datetime.now(UTC).isoformat()

    # Save full probe to dated file
    hist_dir = audit_dir / _HISTORY_DIR
    hist_dir.mkdir(parents=True, exist_ok=True)
    dated_path = hist_dir / f"{today}.json"
    _write_json(dated_path, probe_result)

    # Also update live probe file
    live_path = audit_dir / _LIVE_FILE
    _write_json(live_path, probe_result)

    try:
        from api.audit_json_cache import invalidate_audit

        invalidate_audit(audit_dir)
    except Exception:
        log.exception("Failed to invalidate audit JSON cache after history save")

    # Build lightweight summary. Topic metadata is resolved from the persisted
    # prompt configuration so historical charts can honour the Overview filter.
    data = probe_result.get("live_probe", probe_result)
    if prompt_context is None:
        try:
            from api.prompt_performance import _build_context_response

            prompt_context = _build_context_response(audit_dir)
        except Exception:
            prompt_context = None
    topic_map: dict[str, str] = {}
    if prompt_context:
        rows = prompt_context.get("probed_pss_rows") or prompt_context.get("pss_rows") or []
        positional_topics: list[str] = []
        for row in rows:
            topic = str(row.get("product_or_service") or "Other").strip() or "Other"
            for prompt in row.get("prompts") or []:
                key = str(prompt or "").strip().lower()
                if key:
                    topic_map[key] = topic
                positional_topics.append(topic)
        for index, topic in enumerate(positional_topics):
            topic_map[f"__index_{index}"] = topic
    summary = _build_daily_summary(data, topic_map or None)

    # Update index
    index = _load_index(audit_dir)
    entries: list[dict[str, Any]] = index.get("entries") or []

    # Replace or append entry for today
    entries = [e for e in entries if e.get("date") != today]
    entries.append({
        "date": today,
        "created_at": created_at,
        "summary": summary,
    })
    entries.sort(key=lambda e: e.get("date", ""), reverse=True)
    index["entries"] = entries
    _save_index(audit_dir, index)

    return today


# ── Re-run job (background) ───────────────────────────────────────────────────

def _do_rerun(
    audit_id: str,
    audit_dir: Path,
    *,
    all_prompts: bool = False,
    raise_errors: bool = False,
) -> None:
    """Execute the probe re-run in the background and save to history."""
    try:
        from api.prompt_performance import (
            _build_context_response,
            _all_prompts_for_api,
            _competitor_lists,
            _probe_prompts_for_api,
        )
        from prompt_suggest import run_live_prompt_probes

        ctx_resp = _build_context_response(audit_dir)
        brand = str(ctx_resp.get("brand_name") or "").strip()
        site = str(ctx_resp.get("brand_site_url") or "").strip()
        competitors = ctx_resp.get("competitors") or []
        comp_urls, comp_brands = _competitor_lists(competitors, report_mode=True)
        mcc = str((ctx_resp.get("primary_market") or {}).get("country") or "")
        mid = str((ctx_resp.get("primary_market") or {}).get("country_id") or "")

        # Get the prompts that were originally probed
        prompts = (
            _all_prompts_for_api(ctx_resp)
            if all_prompts
            else _probe_prompts_for_api(ctx_resp)
        )

        if not prompts:
            log.warning("Re-run for %s: no prompts found", audit_id)
            return

        result = run_live_prompt_probes(
            prompts,
            brand_name=brand,
            brand_site_url=site,
            competitor_urls=comp_urls,
            competitor_brands=comp_brands,
            num_runs=3,
            max_prompts=len(prompts),
            market_country=mcc,
            market_country_code=mid,
        )

        # Wrap in the standard live_probe envelope
        wrapped = {"live_probe": result}
        save_probe_to_history(audit_dir, wrapped)
        try:
            from api.score_history import save_score_snapshot

            save_score_snapshot(audit_dir, source="prompt_history")
        except Exception:
            log.exception("Failed to save score snapshot after history re-run for %s", audit_id)
        try:
            from api.export_precompute import precompute_exports_after_probes

            precompute_exports_after_probes(audit_dir)
        except Exception:
            log.exception("Export precompute after history re-run failed for %s", audit_id)
        log.info("Re-run complete for %s", audit_id)

    except Exception as e:
        log.error("Re-run failed for %s: %s", audit_id, e)
        if raise_errors:
            raise


# ── API endpoints ─────────────────────────────────────────────────────────────

def _audit_dir_or_404(audit_id: str) -> Path:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


@router.post("/{audit_id}/re-run")
def trigger_rerun(audit_id: str) -> dict[str, Any]:
    """Queue a durable Cloud Run Job probe re-run for this audit."""
    from api.prompt_jobs import enqueue_prompt_job

    audit_dir = _audit_dir_or_404(audit_id)
    return enqueue_prompt_job(audit_dir, mode="history", all_prompts=True)


# ── Scheduled bulk re-run (called by Cloud Scheduler) ────────────────────────

scheduled_router = APIRouter(prefix="/api/scheduled", tags=["scheduled"])


def _should_rerun_today(audit_dir: Path) -> bool:
    """Return True when this audit is eligible for automated daily prompt refresh."""
    from api.automated_refresh import is_automated_tracking_eligible

    return is_automated_tracking_eligible(audit_dir)


def parse_excluded_audits(*sources: str | None) -> set[str]:
    """Normalize comma-separated audit folder IDs / API paths into a match set.

    Accepts folder names (``www.example.com_abc``) and API-relative paths
    (``audit_output/www.example.com_abc``). Both forms are stored so callers
    can match either ``id`` or ``audit_dir`` from ``list_primary_audits``.
    """
    excluded: set[str] = set()
    for source in sources:
        if not source:
            continue
        for part in str(source).split(","):
            token = part.strip()
            if not token:
                continue
            excluded.add(token)
            if "/" in token:
                excluded.add(token.rsplit("/", 1)[-1])
    return excluded


def _audit_is_excluded(audit_info: dict[str, Any], excluded: set[str]) -> bool:
    if not excluded:
        return False
    folder_id = str(audit_info.get("id") or "").strip()
    audit_dir = str(audit_info.get("audit_dir") or "").strip()
    if folder_id and folder_id in excluded:
        return True
    if audit_dir and audit_dir in excluded:
        return True
    if audit_dir:
        basename = audit_dir.rsplit("/", 1)[-1]
        if basename in excluded:
            return True
    return False


def _resolve_excluded_audits(excluded_audits: str | None = None) -> set[str]:
    """Merge query/param exclusions with ``SCHEDULE_EXCLUDED_AUDITS`` / ``EXCLUDED_AUDITS``."""
    return parse_excluded_audits(
        excluded_audits,
        os.environ.get("SCHEDULE_EXCLUDED_AUDITS"),
        os.environ.get("EXCLUDED_AUDITS"),
    )


@scheduled_router.post("/daily-rerun")
def scheduled_daily_rerun(excluded_audits: str | None = None) -> dict[str, Any]:
    """Bulk daily prompt re-run for audits created on/after 2026-07-22.
    Called by Cloud Scheduler at 02:00 UTC daily.

    Optional query param `excluded_audits` (comma-separated) or env vars
    `SCHEDULE_EXCLUDED_AUDITS` / `EXCLUDED_AUDITS` list audit folder names to skip.
    """
    try:
        audits = geo.list_primary_audits()
    except Exception as e:
        log.error("scheduled_daily_rerun: failed to list audits: %s", e)
        return {"status": "error", "message": str(e)}

    queued: list[str] = []
    skipped: list[str] = []
    excluded_set = _resolve_excluded_audits(excluded_audits)
    from api.prompt_jobs import enqueue_prompt_job

    for audit_info in audits:
        audit_id = str(audit_info.get("audit_dir") or "")
        if not audit_id:
            continue
        try:
            if _audit_is_excluded(audit_info, excluded_set):
                skipped.append(audit_id)
                continue
            audit_dir = geo.resolve_audit_dir(audit_id)
            if _should_rerun_today(audit_dir):
                enqueue_prompt_job(audit_dir, mode="history", all_prompts=True)
                queued.append(audit_id)
            else:
                skipped.append(audit_id)
        except Exception as e:
            log.warning("scheduled_daily_rerun: skipping %s: %s", audit_id, e)
            skipped.append(audit_id)

    log.info("scheduled_daily_rerun: queued=%d, skipped=%d", len(queued), len(skipped))
    return {"status": "ok", "queued": len(queued), "skipped": len(skipped), "queued_ids": queued}


@scheduled_router.post("/monthly-crawl")
def scheduled_monthly_crawl(excluded_audits: str | None = None) -> dict[str, Any]:
    """Monthly brand crawl (+ competitor crawl when competitors are configured) for tracked audits.
    Called by Cloud Scheduler once per month (configure Cloud Scheduler accordingly).

    Optional query param `excluded_audits` (comma-separated) or env vars
    `SCHEDULE_EXCLUDED_AUDITS` / `EXCLUDED_AUDITS` list audit folder names to skip.
    """
    from api.automated_refresh import (
        is_automated_tracking_eligible,
        rebuild_full_audit_crawl_payload,
    )
    from api.crawl_jobs import crawl_jobs_enabled, enqueue_crawl_job

    if not crawl_jobs_enabled():
        return {"status": "error", "message": "AUDIT_CRAWL_JOB_NAME not configured"}

    try:
        audits = geo.list_primary_audits()
    except Exception as e:
        log.error("scheduled_monthly_crawl: failed to list audits: %s", e)
        return {"status": "error", "message": str(e)}

    queued: list[dict[str, Any]] = []
    skipped: list[str] = []
    excluded_set = _resolve_excluded_audits(excluded_audits)

    for audit_info in audits:
        audit_id = str(audit_info.get("audit_dir") or "")
        if not audit_id:
            continue
        try:
            if _audit_is_excluded(audit_info, excluded_set):
                skipped.append(audit_id)
                continue
            audit_dir = geo.resolve_audit_dir(audit_id)
            if not is_automated_tracking_eligible(audit_dir):
                skipped.append(audit_id)
                continue
            payload = rebuild_full_audit_crawl_payload(audit_dir)
            primary = str(payload.get("primary") or "").strip()
            if not primary:
                skipped.append(audit_id)
                continue
            result = enqueue_crawl_job(audit_dir, mode="full_audit", payload=payload)
            queued.append(
                {
                    "audit_id": audit_id,
                    "request_id": result.get("request_id"),
                    "follow_on_competitor_crawl": bool(payload.get("follow_on_competitor_crawl")),
                    "already_running": bool(result.get("already_running")),
                }
            )
        except Exception as e:
            log.warning("scheduled_monthly_crawl: skipping %s: %s", audit_id, e)
            skipped.append(audit_id)

    log.info("scheduled_monthly_crawl: queued=%d, skipped=%d", len(queued), len(skipped))
    return {"status": "ok", "queued": len(queued), "skipped": len(skipped), "queued_ids": queued}


def _summary_needs_repair(summary: dict[str, Any]) -> bool:
    """True when stored platform summaries lack response-level chart fields.

    Only inspect platforms that already have a summary dict — missing platforms
    (never probed that day) must not force a full daily-blob rebuild.
    """
    platform_summaries = [
        summary.get(platform)
        for platform in _PLATFORMS
        if isinstance(summary.get(platform), dict)
    ]
    if not platform_summaries:
        return False
    return any(
        "response_count" not in plat
        or "sentiment_score" not in plat
        or "competitor_visibility" not in plat
        for plat in platform_summaries
    )


def _citation_rows_need_repair(domain_rows: list[Any]) -> bool:
    if not domain_rows:
        return False
    return not any(int((row or {}).get("frequency") or 0) > 0 for row in domain_rows if isinstance(row, dict))


def _load_history_entries(audit_dir: Path, *, persist_repairs: bool = True) -> list[dict[str, Any]]:
    """Return index entries with lightweight summaries, repairing older rows once.

    Chart APIs only need metrics in ``summary`` — never full reply bodies. When
    an old index row is missing fields, rebuild from the dated probe JSON and
    write the repaired summary back so subsequent requests stay cheap.
    """
    index = _load_index(audit_dir)
    entries = list(index.get("entries") or [])

    if not entries:
        live_path = audit_dir / _LIVE_FILE
        if live_path.is_file():
            live = _read_json(live_path)
            data = live.get("live_probe", live)
            summary = _build_daily_summary(data)
            audit_summary = _read_json(audit_dir / "audit_summary.json")
            created = str(audit_summary.get("created_at") or "").split("T")[0]
            if not created:
                created = datetime.now(UTC).strftime("%Y-%m-%d")
            return [{
                "date": created,
                "created_at": str(audit_summary.get("created_at") or datetime.now(UTC).isoformat()),
                "summary": summary,
            }]
        return []

    repaired_entries: list[dict[str, Any]] = []
    did_repair = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
        date = str(entry.get("date") or "")
        needs_platform_repair = _summary_needs_repair(summary)
        domain_rows = summary.get("top_cited_domains") or []
        needs_citation_repair = _citation_rows_need_repair(
            domain_rows if isinstance(domain_rows, list) else []
        )
        if needs_platform_repair or needs_citation_repair:
            dated_probe = audit_dir / _HISTORY_DIR / f"{date}.json"
            if dated_probe.is_file():
                repaired_summary = _build_daily_summary(_read_json(dated_probe))
                entry = {**entry, "summary": repaired_summary}
                did_repair = True
        repaired_entries.append(entry)

    if persist_repairs and did_repair:
        index["entries"] = repaired_entries
        try:
            _save_index(audit_dir, index)
        except Exception:
            log.warning("Failed to persist repaired probe_history_index for %s", audit_dir, exc_info=True)

    return repaired_entries


@router.get("/{audit_id}/probe-history")
def get_probe_history(audit_id: str) -> dict[str, Any]:
    """Return the probe history index with per-day visibility summaries."""
    audit_dir = _audit_dir_or_404(audit_id)
    entries = _load_history_entries(audit_dir)
    return {"entries": entries, "total": len(entries)}


@router.get("/{audit_id}/citation-history")
def get_citation_history(audit_id: str) -> dict[str, Any]:
    """Return daily citation counts by domain for time-series chart."""
    audit_dir = _audit_dir_or_404(audit_id)
    entries = _load_history_entries(audit_dir)

    # Transform into {date, domain, frequency} rows for charting
    result: list[dict[str, Any]] = []
    for entry in entries:
        date = entry.get("date", "")
        domain_rows = (entry.get("summary") or {}).get("top_cited_domains") or []
        for dom_entry in domain_rows:
            result.append({
                "date": date,
                "domain": dom_entry.get("domain"),
                "frequency": dom_entry.get("frequency", 0),
            })

    return {"rows": result, "dates": sorted({r["date"] for r in result})}
