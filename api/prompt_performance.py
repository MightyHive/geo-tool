"""Prompt performance (live probes, SOV) for the TypeScript report UI."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api import geo_services as geo
from api.prompt_selection import select_flat_prompts_for_probing, select_prompts_for_probing

router = APIRouter(prefix="/api/audits", tags=["prompt-performance"])
log = logging.getLogger(__name__)

MAX_COMPETITORS = 10
LIVE_PROBE_FILE = "prompt_performance_live_probe.json"
PROBE_PENDING_FILE = "prompt_performance_probe_pending.json"
PROBE_PROGRESS_FILE = "prompt_probe_progress.jsonl"
AIO_PROBE_FILE = "prompt_performance_aio.json"
AIO_PENDING_FILE = "prompt_performance_aio_pending.json"


def _fallback_brand_label_from_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u
    try:
        host = (urllib.parse.urlparse(u).hostname or "").lower().replace("www.", "")
    except Exception:
        return ""
    if not host:
        return ""
    return host.split(".")[0].replace("-", " ").strip().title() or host


def _read_json(path: Path) -> dict[str, Any] | None:
    from api.audit_json_cache import load_json_cached

    raw = load_json_cached(path)
    return raw if isinstance(raw, dict) else None


def _invalidate_audit_json_cache(audit_dir: Path) -> None:
    try:
        from api.audit_json_cache import invalidate_audit

        invalidate_audit(audit_dir)
    except Exception:
        log.exception("Failed to invalidate audit JSON cache for %s", audit_dir)


def _normalize_pss_rows(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        label = str(r.get("product_or_service") or "").strip()
        if not label:
            continue
        prs = r.get("prompts")
        if not isinstance(prs, list):
            continue
        ps = [str(p).strip() for p in prs if str(p).strip()]
        if ps:
            prompt_set = set(ps)
            raw_tags = r.get("prompt_tags") if isinstance(r.get("prompt_tags"), dict) else {}
            out.append(
                {
                    "product_or_service": label,
                    "prompts": ps,
                    "prompt_tags": {
                        str(prompt): [str(tag).strip() for tag in tags if str(tag).strip()]
                        for prompt, tags in raw_tags.items()
                        if str(prompt) in prompt_set and isinstance(tags, list)
                    },
                    "custom_prompts": [
                        str(prompt).strip()
                        for prompt in (r.get("custom_prompts") or [])
                        if str(prompt).strip() in prompt_set
                    ],
                    "is_custom_topic": bool(r.get("is_custom_topic")),
                }
            )
    return out


def _flatten_pss(rows: list[dict[str, Any]]) -> list[str]:
    flat: list[str] = []
    for r in rows:
        for p in r.get("prompts") or []:
            s = str(p).strip()
            if s:
                flat.append(s)
    return flat


def _load_audit_onboarding(audit_dir: Path) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    ob = _read_json(audit_dir / "onboarding_context.json")
    if ob:
        merged.update(ob)
    pss = _read_json(audit_dir / "products_and_services.json")
    if pss:
        names = pss.get("products_and_services")
        if isinstance(names, list) and names:
            merged.setdefault(
                "products_and_services",
                [str(x).strip() for x in names if str(x).strip()],
            )
        rows = pss.get("rows")
        if isinstance(rows, list) and rows:
            merged.setdefault("products_and_services_rows", rows)
    comp = _read_json(audit_dir / "competitors.json")
    if comp:
        cd = comp.get("competitors")
        if isinstance(cd, list) and cd:
            merged.setdefault("competitors_detail", [x for x in cd if isinstance(x, dict)])
    summ = _read_json(audit_dir / "audit_summary.json")
    if summ:
        merged.setdefault("audit_base_url", str(summ.get("base_url") or "").strip())
    ga4_pages = _read_json(audit_dir / "ga4_top_pages.json")
    if isinstance(ga4_pages, dict):
        pages = ga4_pages.get("pages") or ga4_pages.get("top_pages")
        if isinstance(pages, list) and pages:
            merged.setdefault("ga4_top_pages", pages)
    return merged


def _path_candidates_from_context(ctx: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("ga4_top_pages", "top_pages"):
        raw = ctx.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, dict):
                path = str(item.get("path") or item.get("url_path") or "").strip()
                if path:
                    paths.append(path)
            elif isinstance(item, str) and item.strip():
                paths.append(item.strip())
    return paths


def _primary_market_from_context(ctx: dict[str, Any]) -> tuple[str, str]:
    from geo_market import default_primary_market_from_env, resolve_primary_market

    oc = str(ctx.get("geo_market_country") or "").strip()
    oid = str(ctx.get("geo_market_country_code") or "").strip()
    if oc or oid:
        return resolve_primary_market(oc, oid)
    pm = ctx.get("ga4_primary_market")
    if isinstance(pm, dict):
        gc = str(pm.get("country") or "").strip()
        gid = str(pm.get("country_id") or "").strip()
        if gc or gid:
            return resolve_primary_market(gc, gid)
    return default_primary_market_from_env()


def _brand_and_site(ctx: dict[str, Any]) -> tuple[str, str]:
    brand = str(ctx.get("brand_name_used") or "").strip()
    site = str(ctx.get("brand_website_used") or ctx.get("audit_base_url") or "").strip()
    if not brand and site:
        brand = _fallback_brand_label_from_url(site)
    return brand, site


def _competitors_detail(ctx: dict[str, Any]) -> list[dict[str, str]]:
    det = ctx.get("competitors_detail")
    if isinstance(det, list) and det:
        out: list[dict[str, str]] = []
        for d in det[:MAX_COMPETITORS]:
            if not isinstance(d, dict):
                continue
            u = str(d.get("competitor_website") or "").strip()
            if not u:
                continue
            b = str(d.get("competitor_brand") or "").strip() or _fallback_brand_label_from_url(u)
            out.append({"competitor_website": u, "competitor_brand": b})
        return out
    urls = [str(c).strip() for c in (ctx.get("accepted_competitors") or []) if str(c).strip()]
    return [
        {
            "competitor_website": u,
            "competitor_brand": _fallback_brand_label_from_url(u),
        }
        for u in urls[:MAX_COMPETITORS]
    ]


def _competitor_lists(
    competitors: list[dict[str, str]], *, report_mode: bool
) -> tuple[list[str], list[str]]:
    # report_mode controls whether competitor sites are crawled, not whether
    # configured competitor entities are scored in prompt responses. SOV needs
    # these names even when the full competitor-site audit is disabled.
    _ = report_mode
    urls: list[str] = []
    brands: list[str] = []
    for c in competitors:
        u = str(c.get("competitor_website") or "").strip()
        if not u:
            continue
        urls.append(u)
        brands.append(str(c.get("competitor_brand") or "").strip())
    return urls, brands


def _load_saved_aio_probe(audit_dir: Path) -> dict[str, Any] | None:
    data = _read_json(audit_dir / AIO_PROBE_FILE)
    return data if isinstance(data, dict) else None


def aio_probe_is_pending(audit_dir: Path) -> bool:
    return (audit_dir / AIO_PENDING_FILE).is_file()


def _save_aio_probe(audit_dir: Path, aio: dict[str, Any]) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / AIO_PROBE_FILE).write_text(
        json.dumps(aio, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _invalidate_audit_json_cache(audit_dir)


def _load_saved_live_probe(audit_dir: Path) -> dict[str, Any] | None:
    data = _read_json(audit_dir / LIVE_PROBE_FILE)
    if not data:
        return None
    live = data.get("live_probe")
    return live if isinstance(live, dict) else None


def _save_live_probe(
    audit_dir: Path,
    live: dict[str, Any],
    *,
    highlight_brand: str,
    highlight_comp_urls: list[str],
    highlight_comp_brands: list[str],
    locale_probes: dict[str, Any] | None = None,
    default_locale_key: str = "",
    prompt_locales: list[dict[str, Any]] | None = None,
    persist_metrics: bool = True,
) -> None:
    from api.probe_platforms import sanitize_live_probe

    live = sanitize_live_probe(live)
    payload: dict[str, Any] = {
        "live_probe": live,
        "highlight_brand": highlight_brand,
        "highlight_comp_urls": highlight_comp_urls,
        "highlight_comp_brands": highlight_comp_brands,
    }
    if default_locale_key:
        payload["default_locale_key"] = default_locale_key
    if prompt_locales is not None:
        payload["prompt_locales"] = prompt_locales
    if locale_probes is not None:
        cleaned: dict[str, Any] = {}
        for key, entry in locale_probes.items():
            if not isinstance(entry, dict):
                continue
            item = dict(entry)
            if isinstance(item.get("live_probe"), dict):
                item["live_probe"] = sanitize_live_probe(item["live_probe"])
            cleaned[str(key)] = item
        payload["locale_probes"] = cleaned
    audit_dir.mkdir(parents=True, exist_ok=True)
    from api.prompt_jobs import _write_json

    _write_json(audit_dir / LIVE_PROBE_FILE, payload)
    _invalidate_audit_json_cache(audit_dir)
    # Persist slim metrics so subsequent GETs avoid re-sanitizing the full probe.
    if persist_metrics:
        try:
            _persist_prompt_metrics(audit_dir)
        except Exception:
            log.exception("Failed to persist prompt performance metrics for %s", audit_dir)


def _probed_pss_rows_from_context(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    probed = ctx.get("probed_pss_rows")
    if isinstance(probed, list):
        return probed
    rows = ctx.get("pss_rows")
    if ctx.get("use_pss") and isinstance(rows, list) and rows:
        _, probed_rows = select_prompts_for_probing(rows)
        return probed_rows
    return []


def _sanitize_locale_live(
    live_probe: dict[str, Any] | None,
    *,
    competitors: list[dict[str, Any]],
    path_candidates: list[str],
) -> dict[str, Any] | None:
    if not isinstance(live_probe, dict):
        return None
    from api.probe_platforms import sanitize_live_probe
    from prompt_suggest import recompute_live_probe_mention_scores

    live_probe = sanitize_live_probe(live_probe)
    configured_urls, configured_brands = _competitor_lists(competitors, report_mode=False)
    if not live_probe.get("competitor_urls"):
        live_probe["competitor_urls"] = configured_urls
    if not live_probe.get("competitor_brands"):
        live_probe["competitor_brands"] = configured_brands
    return recompute_live_probe_mention_scores(
        live_probe,
        path_candidates=path_candidates,
    )


def _persist_prompt_metrics(audit_dir: Path) -> dict[str, Any] | None:
    """Build full sanitized context once and write ``prompt_performance_metrics.json``."""
    from api.prompt_performance_metrics import persist_metrics_from_full_context

    ctx = _build_context_response(audit_dir, use_persisted_metrics=False)
    return persist_metrics_from_full_context(audit_dir, ctx)


def _build_context_response(
    audit_dir: Path,
    *,
    use_persisted_metrics: bool = False,
    locale_key: str | None = None,
    include_all_locales: bool = True,
) -> dict[str, Any]:
    """Load prompt-performance context.

    Internal callers default to the full sanitized probe (needed for sentiment,
    exports, and probe execution). GET endpoints pass ``use_persisted_metrics=True``
    to serve slim metrics from ``prompt_performance_metrics.json`` (lazy-backfill
    if missing/stale) without reprocessing reply bodies.
    """
    if use_persisted_metrics:
        from api.prompt_performance_metrics import (
            attach_cdn_url,
            context_from_metrics,
            ensure_persisted_metrics,
            get_cached_slim_context,
            put_cached_slim_context,
        )

        # Prefer Redis / optional GCS slim blob before reading metrics file.
        cached = get_cached_slim_context(
            audit_dir,
            locale_key=locale_key,
            include_all_locales=include_all_locales if locale_key is None else False,
        )
        if cached:
            pending = live_probe_is_pending(audit_dir)
            aio_pending = aio_probe_is_pending(audit_dir)
            cached["live_probe_in_progress"] = pending
            cached["aio_probe_in_progress"] = aio_pending
            if pending:
                try:
                    cached["probe_progress"] = read_latest_probe_progress(audit_dir)
                except Exception:
                    cached["probe_progress"] = None
            from api.prompt_performance_metrics import sanitize_context_runs_inplace

            sanitize_context_runs_inplace(cached)
            scope = locale_key or ("__all__" if include_all_locales else "")
            return attach_cdn_url(cached, audit_dir, str(scope or "__overall__"))

        metrics = ensure_persisted_metrics(
            audit_dir,
            build_full_context=lambda d: _build_full_context_response(d),
        )
        if metrics:
            pending = live_probe_is_pending(audit_dir)
            aio_pending = aio_probe_is_pending(audit_dir)
            all_locales = include_all_locales if locale_key is None else False
            resp = context_from_metrics(
                metrics,
                locale_key=locale_key,
                include_all_locales=all_locales,
            )
            resp["live_probe_in_progress"] = pending
            resp["aio_probe_in_progress"] = aio_pending
            if pending:
                try:
                    resp["probe_progress"] = read_latest_probe_progress(audit_dir)
                except Exception:
                    resp["probe_progress"] = None
            put_cached_slim_context(
                audit_dir,
                resp,
                locale_key=locale_key,
                include_all_locales=all_locales,
            )
            scope = locale_key or ("__all__" if all_locales else "__overall__")
            return attach_cdn_url(resp, audit_dir, str(scope))

    return _build_full_context_response(audit_dir)


def _build_full_context_response(audit_dir: Path) -> dict[str, Any]:
    from prompt_locales import (
        build_locale_spread,
        locales_from_onboarding,
    )

    ctx = _load_audit_onboarding(audit_dir)
    pss_rows = _normalize_pss_rows(ctx.get("products_and_services_rows"))
    use_pss = bool(pss_rows)
    flat = _flatten_pss(pss_rows) if use_pss else []
    if not flat:
        for key in ("product_service_prompts", "suggested_prompts"):
            raw = ctx.get(key)
            if isinstance(raw, list) and raw:
                flat = [str(p).strip() for p in raw if str(p).strip()]
                break
    brand, site = _brand_and_site(ctx)
    mcc, mid = _primary_market_from_context(ctx)
    competitors = _competitors_detail(ctx)
    prompt_locales = locales_from_onboarding(ctx)
    default_locale = prompt_locales[0] if prompt_locales else None
    default_locale_key = str((default_locale or {}).get("key") or "")

    saved = _read_json(audit_dir / LIVE_PROBE_FILE)
    path_candidates = _path_candidates_from_context(ctx)
    live_probe = saved.get("live_probe") if isinstance(saved, dict) else None
    live_probe = _sanitize_locale_live(
        live_probe if isinstance(live_probe, dict) else None,
        competitors=competitors,
        path_candidates=path_candidates,
    )

    locale_probes_raw = saved.get("locale_probes") if isinstance(saved, dict) else None
    locale_probes: dict[str, Any] = {}
    if isinstance(locale_probes_raw, dict) and locale_probes_raw:
        for key, entry in locale_probes_raw.items():
            if not isinstance(entry, dict):
                continue
            entry_live = _sanitize_locale_live(
                entry.get("live_probe") if isinstance(entry.get("live_probe"), dict) else None,
                competitors=competitors,
                path_candidates=path_candidates,
            )
            locale_probes[str(key)] = {
                **entry,
                "live_probe": entry_live,
            }
    elif live_probe is not None and default_locale_key:
        # Historical: single probe file → treat as primary market + English.
        locale_probes[default_locale_key] = {
            "locale": default_locale,
            "live_probe": live_probe,
            "prompts_probed": [
                str(p.get("prompt") or "").strip()
                for p in (live_probe.get("per_prompt") or [])
                if isinstance(p, dict) and str(p.get("prompt") or "").strip()
            ],
        }

    saved_default_key = str((saved or {}).get("default_locale_key") or "") if isinstance(saved, dict) else ""
    if saved_default_key and saved_default_key in locale_probes:
        default_locale_key = saved_default_key
    elif default_locale_key not in locale_probes and locale_probes:
        default_locale_key = next(iter(locale_probes.keys()))

    if default_locale_key and default_locale_key in locale_probes:
        default_live = locale_probes[default_locale_key].get("live_probe")
        if isinstance(default_live, dict):
            live_probe = default_live

    highlight = {
        "brand": str((saved or {}).get("highlight_brand") or brand),
        "competitor_urls": (saved or {}).get("highlight_comp_urls") or [],
        "competitor_brands": (saved or {}).get("highlight_comp_brands") or [],
        "brand_match_tokens": (
            live_probe.get("brand_match_tokens") if isinstance(live_probe, dict) else []
        )
        or [],
        "brand_detected_spellings": (
            live_probe.get("brand_detected_spellings") if isinstance(live_probe, dict) else []
        )
        or [],
        "product_line_aliases": (
            live_probe.get("product_line_aliases") if isinstance(live_probe, dict) else []
        )
        or [],
    }
    probed_flat: list[str] = []
    probed_pss_rows: list[dict[str, Any]] = []
    if use_pss and pss_rows:
        probed_flat, probed_pss_rows = select_prompts_for_probing(pss_rows)
    elif flat:
        probed_flat = select_flat_prompts_for_probing(flat)

    product_labels = [
        str(r.get("product_or_service") or "").strip()
        for r in (probed_pss_rows if probed_pss_rows else pss_rows)
        if str(r.get("product_or_service") or "").strip()
    ]
    from api.sov_metrics import collect_sov_history

    sov_history, sov_history_by_product = collect_sov_history(
        audit_dir,
        site or str(ctx.get("audit_base_url") or ""),
        competitors,
        product_labels=product_labels if use_pss else None,
    )
    aio_probe = _load_saved_aio_probe(audit_dir)
    pending = live_probe_is_pending(audit_dir)
    result: dict[str, Any] = {
        "brand_name": brand,
        "brand_site_url": site,
        "use_pss": use_pss,
        "pss_rows": pss_rows,
        "probed_pss_rows": probed_pss_rows,
        "flat_prompts": flat,
        "prompt_count": len(probed_flat) if probed_flat else len(flat),
        "stored_prompt_count": len(flat),
        "competitors": competitors,
        "primary_market": {"country": mcc, "country_id": mid},
        "prompt_locales": prompt_locales,
        "default_locale_key": default_locale_key,
        "locale_probes": locale_probes,
        "locale_spread": build_locale_spread(locale_probes),
        "category_labels": [
            str(x).strip()
            for x in (ctx.get("prompt_category_labels") or ctx.get("accepted_categories") or [])
            if str(x).strip()
        ],
        "industry": str(ctx.get("industry_used") or "").strip(),
        "live_probe": live_probe if isinstance(live_probe, dict) else None,
        "live_probe_in_progress": pending,
        "aio_probe": aio_probe,
        "aio_probe_in_progress": aio_probe_is_pending(audit_dir),
        "highlight": highlight,
        "sov_history": sov_history,
        "sov_history_by_product": sov_history_by_product,
    }
    if pending:
        try:
            result["probe_progress"] = read_latest_probe_progress(audit_dir)
        except Exception:
            result["probe_progress"] = None
    return result


def _probe_prompts_for_api(ctx_resp: dict[str, Any]) -> list[str]:
    """Prompts selected for live probes (custom prompts always included)."""
    probed = ctx_resp.get("probed_pss_rows")
    if isinstance(probed, list) and probed:
        flat: list[str] = []
        for row in probed:
            if not isinstance(row, dict):
                continue
            for prompt in row.get("prompts") or []:
                s = str(prompt).strip()
                if s:
                    flat.append(s)
        if flat:
            return flat
    rows = ctx_resp.get("pss_rows") if isinstance(ctx_resp.get("pss_rows"), list) else []
    if rows:
        flat, _ = select_prompts_for_probing(rows)
        return flat
    flat_src = ctx_resp.get("flat_prompts") if isinstance(ctx_resp.get("flat_prompts"), list) else []
    return select_flat_prompts_for_probing([str(p) for p in flat_src])


def _all_prompts_for_api(ctx_resp: dict[str, Any]) -> list[str]:
    """Every stored prompt, deduplicated in configured order."""
    prompts: list[str] = []
    rows = ctx_resp.get("pss_rows") if isinstance(ctx_resp.get("pss_rows"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        prompts.extend(str(prompt).strip() for prompt in (row.get("prompts") or []))
    if not prompts:
        flat_src = ctx_resp.get("flat_prompts") if isinstance(ctx_resp.get("flat_prompts"), list) else []
        prompts.extend(str(prompt).strip() for prompt in flat_src)
    return list(dict.fromkeys(prompt for prompt in prompts if prompt))


def _live_probe_pending_path(audit_dir: Path) -> Path:
    return audit_dir / PROBE_PENDING_FILE


def live_probe_is_pending(audit_dir: Path) -> bool:
    """True while a prompt probe job is still in flight.

    Reconciles stale ``prompt_performance_probe_pending.json`` against shard-merged
    fan-out status so the UI does not stay on Running after every locale finished
    (or finalize already completed / failed) but the pending flag was left behind.
    Also recovers orphaned ``running`` shards that already have locale artifacts.
    """
    pending_path = _live_probe_pending_path(audit_dir)
    pending = _read_json(pending_path)
    if not isinstance(pending, dict):
        return False
    status = str(pending.get("status") or "")
    if status not in {"queued", "starting", "running"}:
        return False

    try:
        from api.prompt_jobs import (
            fanout_batch_is_idle,
            load_fanout_status,
            reconcile_orphaned_fanout_locales,
        )

        fanout = load_fanout_status(audit_dir)
        if fanout:
            fanout = reconcile_orphaned_fanout_locales(audit_dir, fanout)
    except Exception:
        return True

    if not fanout:
        # Fan-out pending without a status file: keep reporting in progress unless
        # this is a non-fanout legacy pending (no fanout flag) — still pending.
        return True

    pending_batch = str(pending.get("batch_id") or pending.get("request_id") or "")
    fanout_batch = str(fanout.get("batch_id") or "")
    # Different batch id means this pending may still be the active request for a
    # newer run that has not written fan-out yet — keep reporting in progress.
    if pending_batch and fanout_batch and pending_batch != fanout_batch:
        return True

    if not fanout_batch_is_idle(fanout):
        return True

    # All locale jobs are terminal (or finalize already settled). Drop the stale
    # running flag so subsequent GETs stay idle.
    try:
        pending_path.unlink(missing_ok=True)
    except OSError:
        pass
    return False


def _execute_live_probe(
    audit_dir: Path,
    *,
    report_mode: bool = True,
    prompts: list[str],
    progress_callback: Any | None = None,
    market_country: str = "",
    market_country_code: str = "",
    language: str = "en",
    language_name: str = "English",
) -> dict[str, Any]:
    from prompt_suggest import run_live_prompt_probes

    if not prompts:
        raise ValueError("No prompts to probe")
    ctx_resp = _build_context_response(audit_dir)
    brand = str(ctx_resp.get("brand_name") or "").strip()
    if not brand:
        raise ValueError("Brand name is required")
    site = str(ctx_resp.get("brand_site_url") or "").strip()
    competitors = ctx_resp.get("competitors") or []
    comp_urls, comp_brands = _competitor_lists(competitors, report_mode=report_mode)
    mcc = market_country or str((ctx_resp.get("primary_market") or {}).get("country") or "")
    mid = market_country_code or str((ctx_resp.get("primary_market") or {}).get("country_id") or "")
    return run_live_prompt_probes(
        prompts,
        brand_name=brand,
        brand_site_url=site,
        competitor_urls=comp_urls,
        competitor_brands=comp_brands,
        max_prompts=len(prompts),
        market_country=mcc,
        market_country_code=mid,
        language=language,
        language_name=language_name,
        progress_callback=progress_callback,
    )


def summarize_probe_progress_event(event: dict[str, Any]) -> dict[str, Any]:
    """Build a UI-friendly probe progress summary from a raw progress event.

    Markets run in parallel, so planned/completed stay per-market (do not multiply
    by ``locale_total``). ETA uses a fixed ~7s per platform call.
    """
    from api.probe_eta import estimate_probe_run_seconds

    locale_index = max(1, int(event.get("locale_index") or 1))
    locale_total = max(1, int(event.get("locale_total") or 1))
    completed = max(0, int(event.get("completed_calls") or 0))
    planned = max(0, int(event.get("planned_calls") or 0))
    prompt_index = max(0, int(event.get("prompt_index") or 0))
    prompt_total = max(0, int(event.get("prompt_total") or 0))

    # Parallel markets: wall-clock work is one market's call count (not × locale_total).
    overall_planned = planned
    overall_completed = min(planned, completed) if planned > 0 else completed
    remaining = max(0, overall_planned - overall_completed) if overall_planned > 0 else 0

    job_elapsed = event.get("job_elapsed_seconds")
    locale_elapsed = event.get("total_elapsed_seconds")
    elapsed = None
    for candidate in (job_elapsed, locale_elapsed):
        if isinstance(candidate, (int, float)) and candidate > 0:
            elapsed = float(candidate)
            break

    eta_seconds: int | None = None
    eta_total_seconds: int | None = None
    if overall_planned > 0:
        eta_total_seconds = estimate_probe_run_seconds(overall_planned)
    if remaining > 0:
        eta_seconds = estimate_probe_run_seconds(remaining)
    elif overall_planned > 0 and overall_completed <= 0:
        eta_seconds = eta_total_seconds

    return {
        "status": str(event.get("status") or ""),
        "market_count": locale_total,
        "locale_index": locale_index,
        "locale_label": str(
            event.get("locale_label") or event.get("locale_market") or ""
        ),
        "completed_calls": overall_completed,
        "planned_calls": overall_planned,
        "locale_completed_calls": completed,
        "locale_planned_calls": planned,
        "prompt_index": prompt_index,
        "prompt_total": prompt_total,
        "elapsed_seconds": round(elapsed, 1) if elapsed is not None else None,
        "eta_seconds": eta_seconds,
        "eta_total_seconds": eta_total_seconds,
    }


def read_latest_probe_progress(audit_dir: Path) -> dict[str, Any] | None:
    """Return the latest probe progress (fan-out aggregate preferred, else jsonl)."""
    try:
        from api.prompt_jobs import (
            aggregate_fanout_progress,
            load_fanout_status,
            reconcile_orphaned_fanout_locales,
        )

        fanout = load_fanout_status(audit_dir)
        if fanout and isinstance(fanout.get("locales"), dict) and fanout["locales"]:
            fanout = reconcile_orphaned_fanout_locales(audit_dir, fanout)
            summary = aggregate_fanout_progress(fanout)
            # Prefer wall-clock elapsed from the slowest (max) locale when present.
            elapsed = None
            for entry in fanout["locales"].values():
                if isinstance(entry, dict) and entry.get("elapsed_seconds"):
                    try:
                        elapsed = max(float(elapsed or 0), float(entry["elapsed_seconds"]))
                    except (TypeError, ValueError):
                        pass
            if elapsed:
                summary["elapsed_seconds"] = round(elapsed, 1)
            return summary
    except Exception:
        pass

    path = audit_dir / PROBE_PROGRESS_FILE
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    latest: dict[str, Any] | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            latest = parsed
    if not latest:
        return None
    return summarize_probe_progress_event(latest)


def run_live_probe_job(
    audit_dir: Path,
    *,
    report_mode: bool = True,
    on_progress: Any | None = None,
) -> None:
    """Run live probes for every configured market+language locale and save."""
    import time

    from prompt_locales import locales_from_onboarding, regenerate_prompts_for_language

    ctx_resp = _build_context_response(audit_dir)
    prompts = _probe_prompts_for_api(ctx_resp)
    if not prompts:
        return
    brand = str(ctx_resp.get("brand_name") or "").strip()
    if not brand:
        return

    onboarding = _load_audit_onboarding(audit_dir)
    locales = locales_from_onboarding(onboarding)
    if not locales:
        locales = [
            {
                "country": str((ctx_resp.get("primary_market") or {}).get("country") or ""),
                "country_code": str((ctx_resp.get("primary_market") or {}).get("country_id") or ""),
                "language": "en",
                "language_name": "English",
                "key": "XX:en",
                "label": "English",
            }
        ]
    source_language = str(
        onboarding.get("prompt_source_language") or "en"
    ).strip().lower() or "en"
    source_language_name = str(
        onboarding.get("prompt_source_language_name") or "English"
    ).strip() or "English"

    progress_path = audit_dir / PROBE_PROGRESS_FILE
    progress_events: list[dict[str, Any]] = []
    progress_path.unlink(missing_ok=True)
    job_started = time.monotonic()
    locale_total = len(locales)

    def _record_progress(event: dict[str, Any]) -> None:
        from api.prompt_jobs import _write_text

        enriched = {
            **event,
            "locale_total": int(event.get("locale_total") or locale_total),
            "job_elapsed_seconds": round(time.monotonic() - job_started, 2),
        }
        recorded = {
            "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            **enriched,
        }
        progress_events.append(recorded)
        completed = int(enriched.get("completed_calls") or 0)
        if (
            enriched.get("status") in {"started", "error", "responses_complete", "locale_started", "complete"}
            or (completed > 0 and completed % 5 == 0)
        ):
            try:
                _write_text(
                    progress_path,
                    "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in progress_events),
                )
            except OSError:
                pass
        if on_progress:
            on_progress(enriched)

    locale_probes: dict[str, Any] = {}
    default_locale = locales[0]
    default_key = str(default_locale.get("key") or "")
    default_live: dict[str, Any] | None = None
    competitors = ctx_resp.get("competitors") or []
    comp_urls, comp_brands = _competitor_lists(competitors, report_mode=report_mode)

    def _persist_partial(*, finalize_metrics: bool = False) -> None:
        """Write live probe after each locale so reports survive mid-job timeout/kill."""
        if default_live is None:
            return
        _save_live_probe(
            audit_dir,
            default_live,
            highlight_brand=brand,
            highlight_comp_urls=comp_urls,
            highlight_comp_brands=comp_brands,
            locale_probes=locale_probes,
            default_locale_key=default_key,
            prompt_locales=locales,
            persist_metrics=finalize_metrics,
        )

    for loc_index, locale in enumerate(locales, start=1):
        key = str(locale.get("key") or f"locale_{loc_index}")
        lang = str(locale.get("language") or "en").strip().lower() or "en"
        lang_name = str(locale.get("language_name") or "English")
        country = str(locale.get("country") or "")
        code = str(locale.get("country_code") or "")
        _record_progress(
            {
                "status": "locale_started",
                "locale_key": key,
                "locale_label": str(locale.get("label") or key),
                "locale_index": loc_index,
                "locale_total": locale_total,
                "completed_calls": 0,
                "planned_calls": 0,
            }
        )

        default_country = str(default_locale.get("country") or "")
        default_code = str(default_locale.get("country_code") or "")
        # Adapt whenever market or language differs from the stored source prompts.
        needs_adapt = lang != source_language or (
            (code or country)
            and (
                (code and default_code and code.upper() != default_code.upper())
                or (
                    not code
                    and country
                    and default_country
                    and country.strip().lower() != default_country.strip().lower()
                )
            )
        )
        if not needs_adapt:
            locale_prompts = list(prompts)
        else:
            locale_prompts = regenerate_prompts_for_language(
                prompts,
                target_language=lang,
                target_language_name=lang_name,
                market_country=country,
                market_country_code=code,
                source_market_country=default_country,
                source_market_country_code=default_code,
                source_language=source_language,
                source_language_name=source_language_name,
            )

        def _locale_progress(
            event: dict[str, Any],
            _key: str = key,
            _label: str = str(locale.get("label") or key),
            _index: int = loc_index,
        ) -> None:
            _record_progress(
                {
                    **event,
                    "locale_key": _key,
                    "locale_label": _label,
                    "locale_index": _index,
                    "locale_total": locale_total,
                }
            )

        live = _execute_live_probe(
            audit_dir,
            report_mode=report_mode,
            prompts=locale_prompts,
            progress_callback=_locale_progress,
            market_country=country,
            market_country_code=code,
            language=lang,
            language_name=lang_name,
        )
        locale_probes[key] = {
            "locale": locale,
            "live_probe": live,
            "source_prompts": prompts,
            "prompts_probed": locale_prompts,
        }
        if key == default_key or default_live is None:
            default_live = live
            default_key = key
        # Persist after every locale so multi-locale jobs still surface results if the
        # Cloud Run task later times out (default 4h) before finishing all markets.
        _persist_partial(finalize_metrics=False)

    try:
        from api.prompt_jobs import _write_text

        _write_text(
            progress_path,
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in progress_events),
        )
    except OSError:
        pass
    if default_live is None:
        return
    _persist_partial(finalize_metrics=True)
    # Save to probe history for time-series tracking (default locale)
    try:
        from api.probe_history import save_probe_to_history
        wrapped = {"live_probe": default_live, "locale_probes": locale_probes, "default_locale_key": default_key}
        save_probe_to_history(audit_dir, wrapped)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("probe_history save failed: %s", e)


def _default_locale_fallback(ctx_resp: dict[str, Any]) -> dict[str, Any]:
    return {
        "country": str((ctx_resp.get("primary_market") or {}).get("country") or ""),
        "country_code": str((ctx_resp.get("primary_market") or {}).get("country_id") or ""),
        "language": "en",
        "language_name": "English",
        "key": "XX:en",
        "label": "English",
    }


def run_live_probe_for_locale(
    audit_dir: Path,
    *,
    locale: dict[str, Any],
    locale_index: int = 1,
    locale_total: int = 1,
    report_mode: bool = True,
    on_progress: Any | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Run live probes for a single locale and persist a per-locale artifact.

    Platforms stay together inside this call. Used by locale-fan-out Cloud Run jobs.
    """
    import time

    from api.prompt_jobs import locale_probe_artifact_path, update_fanout_locale_progress
    from prompt_locales import locales_from_onboarding, regenerate_prompts_for_language

    ctx_resp = _build_context_response(audit_dir)
    prompts = _probe_prompts_for_api(ctx_resp)
    if not prompts:
        raise ValueError("No prompts on file for this audit")
    brand = str(ctx_resp.get("brand_name") or "").strip()
    if not brand:
        raise ValueError("Brand name is required")

    onboarding = _load_audit_onboarding(audit_dir)
    all_locales = locales_from_onboarding(onboarding) or [_default_locale_fallback(ctx_resp)]
    default_locale = all_locales[0]
    source_language = str(
        onboarding.get("prompt_source_language") or "en"
    ).strip().lower() or "en"
    source_language_name = str(
        onboarding.get("prompt_source_language_name") or "English"
    ).strip() or "English"
    key = str(locale.get("key") or f"locale_{locale_index}")
    lang = str(locale.get("language") or "en").strip().lower() or "en"
    lang_name = str(locale.get("language_name") or "English")
    country = str(locale.get("country") or "")
    code = str(locale.get("country_code") or "")
    label = str(locale.get("label") or key)

    job_started = time.monotonic()
    progress_path = audit_dir / f"prompt_probe_progress_{key.replace(':', '_')}.jsonl"
    progress_events: list[dict[str, Any]] = []

    def _persist_progress_jsonl() -> None:
        from api.prompt_jobs import _write_text

        try:
            _write_text(
                progress_path,
                "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in progress_events),
            )
        except OSError:
            pass

    def _record_progress(event: dict[str, Any]) -> None:
        enriched = {
            **event,
            "locale_key": key,
            "locale_label": label,
            "locale_index": locale_index,
            "locale_total": locale_total,
            "job_elapsed_seconds": round(time.monotonic() - job_started, 2),
        }
        recorded = {
            "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            **enriched,
        }
        progress_events.append(recorded)
        completed = int(enriched.get("completed_calls") or 0)
        planned = int(enriched.get("planned_calls") or 0)
        if (
            enriched.get("status")
            in {"started", "error", "responses_complete", "locale_started", "complete"}
            or (completed > 0 and completed % 5 == 0)
        ):
            _persist_progress_jsonl()
            try:
                update_fanout_locale_progress(
                    audit_dir,
                    locale_key=key,
                    status="running",
                    completed_calls=completed,
                    planned_calls=planned,
                    request_id=request_id,
                )
            except Exception:
                log.exception("Failed to update fan-out progress for %s", key)
        if on_progress:
            on_progress(enriched)

    _record_progress(
        {
            "status": "locale_started",
            "completed_calls": 0,
            "planned_calls": 0,
        }
    )
    try:
        update_fanout_locale_progress(
            audit_dir,
            locale_key=key,
            status="running",
            request_id=request_id,
        )
    except Exception:
        pass

    default_country = str(default_locale.get("country") or "")
    default_code = str(default_locale.get("country_code") or "")
    needs_adapt = lang != source_language or (
        (code or country)
        and (
            (code and default_code and code.upper() != default_code.upper())
            or (
                not code
                and country
                and default_country
                and country.strip().lower() != default_country.strip().lower()
            )
        )
    )
    if not needs_adapt:
        locale_prompts = list(prompts)
    else:
        locale_prompts = regenerate_prompts_for_language(
            prompts,
            target_language=lang,
            target_language_name=lang_name,
            market_country=country,
            market_country_code=code,
            source_market_country=default_country,
            source_market_country_code=default_code,
            source_language=source_language,
            source_language_name=source_language_name,
        )

    live = _execute_live_probe(
        audit_dir,
        report_mode=report_mode,
        prompts=locale_prompts,
        progress_callback=_record_progress,
        market_country=country,
        market_country_code=code,
        language=lang,
        language_name=lang_name,
    )
    competitors = ctx_resp.get("competitors") or []
    comp_urls, comp_brands = _competitor_lists(competitors, report_mode=report_mode)
    entry = {
        "locale": locale,
        "live_probe": live,
        "source_prompts": prompts,
        "prompts_probed": locale_prompts,
        "highlight_brand": brand,
        "highlight_comp_urls": comp_urls,
        "highlight_comp_brands": comp_brands,
    }
    artifact_path = locale_probe_artifact_path(audit_dir, key)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    from api.prompt_jobs import _write_json as _write_json_atomic

    _write_json_atomic(artifact_path, entry)
    _invalidate_audit_json_cache(audit_dir)

    # Soft-merge into canonical file so partial multi-locale progress is visible.
    try:
        merge_locale_probe_artifacts(audit_dir, require_all=False)
    except Exception:
        log.exception("Partial merge after locale %s failed (non-fatal)", key)

    _persist_progress_jsonl()

    return entry


def merge_locale_probe_artifacts(
    audit_dir: Path,
    *,
    require_all: bool = True,
) -> dict[str, Any] | None:
    """Combine per-locale probe artifacts into ``prompt_performance_live_probe.json``.

    When ``require_all`` is True, returns None (and does not overwrite the canonical
    file) unless every configured locale has a successful artifact.
    """
    from api.prompt_jobs import locale_probe_artifact_path
    from prompt_locales import locales_from_onboarding

    onboarding = _load_audit_onboarding(audit_dir)
    locales = locales_from_onboarding(onboarding)
    if not locales:
        ctx = _build_context_response(audit_dir)
        locales = [_default_locale_fallback(ctx)]

    locale_probes: dict[str, Any] = {}
    missing: list[str] = []
    brand = ""
    comp_urls: list[str] = []
    comp_brands: list[str] = []

    for loc in locales:
        key = str(loc.get("key") or "")
        if not key:
            continue
        path = locale_probe_artifact_path(audit_dir, key)
        raw = _read_json(path)
        if not raw or not isinstance(raw.get("live_probe"), dict):
            missing.append(key)
            continue
        locale_probes[key] = {
            "locale": raw.get("locale") or loc,
            "live_probe": raw["live_probe"],
            "source_prompts": raw.get("source_prompts") or [],
            "prompts_probed": raw.get("prompts_probed") or [],
        }
        if not brand:
            brand = str(raw.get("highlight_brand") or "")
            comp_urls = list(raw.get("highlight_comp_urls") or [])
            comp_brands = list(raw.get("highlight_comp_brands") or [])

    if require_all and missing:
        log.info(
            "Merge gated: missing locale artifacts for %s (%s)",
            geo.audit_dir_api_rel(audit_dir),
            ", ".join(missing),
        )
        return None
    if not locale_probes:
        return None

    default_key = str(locales[0].get("key") or next(iter(locale_probes)))
    if default_key not in locale_probes:
        default_key = next(iter(locale_probes))
    default_live = locale_probes[default_key]["live_probe"]
    if not brand:
        try:
            brand = str(_build_context_response(audit_dir).get("brand_name") or "")
        except Exception:
            brand = ""

    _save_live_probe(
        audit_dir,
        default_live,
        highlight_brand=brand,
        highlight_comp_urls=comp_urls,
        highlight_comp_brands=comp_brands,
        locale_probes=locale_probes,
        default_locale_key=default_key,
        prompt_locales=locales,
    )
    return {
        "live_probe": default_live,
        "locale_probes": locale_probes,
        "default_locale_key": default_key,
        "missing_locales": missing,
    }


def run_post_probe_side_effects(audit_dir: Path) -> dict[str, str]:
    """Sentiment (async job), executive summary, and export precompute after probes are merged."""
    outcome: dict[str, str] = {
        "sentiment": "skipped",
        "executive_summary": "skipped",
        "exports": "skipped",
    }
    try:
        ctx2 = _build_context_response(audit_dir)
    except Exception as exc:
        log.warning("Post-probe side effects skipped (context): %s", exc)
        return outcome

    brand = str(ctx2.get("brand_name") or "").strip()
    live = ctx2.get("live_probe")
    if not isinstance(live, dict) or not (live.get("per_prompt") or []):
        return outcome

    try:
        from api.sentiment_jobs import enqueue_sentiment_job

        queued = enqueue_sentiment_job(audit_dir)
        outcome["sentiment"] = str(queued.get("status") or "queued")
        if queued.get("request_id"):
            outcome["sentiment_request_id"] = str(queued["request_id"])
    except Exception as exc:
        log.exception("Sentiment job enqueue failed for %s: %s", audit_dir, exc)
        # Fallback: generate inline so reports still get overall sentiment.
        try:
            from insights_llm import load_or_generate_prompt_sentiment

            pss_rows = _probed_pss_rows_from_context(ctx2)
            sent, err = load_or_generate_prompt_sentiment(
                audit_dir,
                live,
                brand_name=brand,
                site_url=str(ctx2.get("brand_site_url") or ""),
                pss_rows=pss_rows if isinstance(pss_rows, list) else None,
                force=True,
            )
            if sent:
                outcome["sentiment"] = "done"
            else:
                outcome["sentiment"] = f"error: {err or 'unknown'}"
        except Exception as inner:
            log.exception("Inline sentiment fallback failed for %s: %s", audit_dir, inner)
            outcome["sentiment"] = f"error: {exc}"

    try:
        from executive_summary_llm import generate_and_cache_for_audit_dir

        generate_and_cache_for_audit_dir(audit_dir)
        outcome["executive_summary"] = "done"
    except Exception as exc:
        log.exception("Executive summary refresh failed for %s: %s", audit_dir, exc)
        outcome["executive_summary"] = f"error: {exc}"

    try:
        from api.export_precompute import precompute_exports_after_probes

        pre = precompute_exports_after_probes(audit_dir)
        outcome["exports"] = str(pre.get("html") or "skipped")
    except Exception as exc:
        log.exception("Export precompute failed for %s: %s", audit_dir, exc)
        outcome["exports"] = f"error: {exc}"

    return outcome


def maybe_complete_locale_fanout(
    audit_dir: Path,
    *,
    batch_id: str,
    completing_locale_key: str,
    request_id: str | None = None,
    locale_error: str | None = None,
) -> dict[str, Any]:
    """Mark a locale done/failed; merge + finalize only when every locale has finished.

    Successful locale artifacts are never deleted on partial failure.
    """
    import uuid

    from api.prompt_jobs import (
        PROMPT_PENDING_FILE,
        _read_json,
        _utc_now,
        _update_json,
        _write_json,
        claim_fanout_finalize,
        fanout_status_path,
        load_fanout_status,
        update_fanout_locale_progress,
    )

    result: dict[str, Any] = {
        "batch_id": batch_id,
        "locale_key": completing_locale_key,
        "action": "waiting",
    }

    if locale_error:
        update_fanout_locale_progress(
            audit_dir,
            locale_key=completing_locale_key,
            status="failed",
            error=locale_error,
            request_id=request_id,
        )
    else:
        update_fanout_locale_progress(
            audit_dir,
            locale_key=completing_locale_key,
            status="completed",
            request_id=request_id,
        )

    path = fanout_status_path(audit_dir)
    fanout = load_fanout_status(audit_dir)
    if not fanout or str(fanout.get("batch_id") or "") != batch_id:
        result["action"] = "stale_batch"
        return result

    locales = fanout.get("locales") if isinstance(fanout.get("locales"), dict) else {}
    if not locales:
        result["action"] = "no_locales"
        return result

    statuses = {
        key: str((entry or {}).get("status") or "")
        for key, entry in locales.items()
        if isinstance(entry, dict)
    }
    if any(st in {"queued", "starting", "running"} for st in statuses.values()):
        result["action"] = "waiting"
        result["statuses"] = statuses
        return result

    failed = [k for k, st in statuses.items() if st in {"failed", "launch_failed"}]
    if failed:
        result["action"] = "partial_failure"
        result["failed_locales"] = failed

        def mark_partial(data: dict[str, Any]) -> dict[str, Any] | None:
            if str(data.get("batch_id") or "") != batch_id:
                return None
            data["finalize_status"] = "blocked_partial_failure"
            data["failed_locales"] = failed
            data["updated_at"] = _utc_now()
            return data

        _update_json(path, mark_partial)
        pending = audit_dir / PROMPT_PENDING_FILE
        pending.unlink(missing_ok=True)
        # Soft-merge whatever succeeded so the report can show partial markets.
        try:
            merge_locale_probe_artifacts(audit_dir, require_all=False)
        except Exception:
            log.exception("Soft merge after partial failure failed for %s", audit_dir)
        try:
            from api.audit_runner import _write_run_status

            mode = str(fanout.get("mode") or "")
            if mode == "post_audit":
                _write_run_status(
                    audit_dir,
                    {
                        "status": "error",
                        "audit_dir": geo.audit_dir_api_rel(audit_dir),
                        "detail": (
                            "AI prompt probes incomplete: failed locales "
                            + ", ".join(failed)
                        ),
                        "error": f"Locale probe failures: {', '.join(failed)}",
                        "failed_locales": failed,
                        "market_count": int(fanout.get("locale_total") or len(locales)),
                    },
                )
        except Exception:
            log.exception("Failed to write partial-failure run status")
        return result

    # All locales completed — claim finalize under lock so only one job merges.
    claim = uuid.uuid4().hex
    fanout = claim_fanout_finalize(audit_dir, batch_id=batch_id, claim=claim)
    if not fanout:
        current = load_fanout_status(audit_dir) or {}
        if str(current.get("batch_id") or "") != batch_id:
            result["action"] = "stale_batch"
        elif str(current.get("finalize_status") or "") in {"finalizing", "finalized"}:
            result["action"] = "already_finalizing"
        else:
            result["action"] = "lost_finalize_race"
        return result

    mode = str(fanout.get("mode") or "live")
    report_mode = bool(fanout.get("report_mode", True))
    completion = fanout.get("completion") if isinstance(fanout.get("completion"), dict) else {}

    try:
        merged = merge_locale_probe_artifacts(audit_dir, require_all=True)
        if not merged:
            raise RuntimeError("Merge gated: not all locale artifacts present")
        try:
            from api.probe_history import save_probe_to_history

            save_probe_to_history(
                audit_dir,
                {
                    "live_probe": merged["live_probe"],
                    "locale_probes": merged["locale_probes"],
                    "default_locale_key": merged["default_locale_key"],
                },
            )
        except Exception as exc:
            log.warning("probe_history save failed after fan-out merge: %s", exc)

        side = run_post_probe_side_effects(audit_dir)
        result["side_effects"] = side
        try:
            from api.topic_content_jobs import enqueue_lowest_topic_content_job

            topic_content = enqueue_lowest_topic_content_job(audit_dir)
            if topic_content:
                result["topic_content"] = topic_content
        except Exception as exc:
            # Outline generation is an asynchronous workshop enhancement and
            # must never turn an otherwise successful probe run into a failure.
            log.warning("topic content enqueue failed after fan-out merge: %s", exc)

        if mode == "post_audit":
            from api.audit_progress import AuditProgressState, advance_to_step
            from api.audit_runner import finalize_audit_run

            state = AuditProgressState()
            state = advance_to_step(state, "sentiment", "Finishing sentiment and summary…")
            finalize_audit_run(
                audit_dir=audit_dir,
                primary=str(completion.get("primary") or ""),
                competitors=[
                    str(item).strip()
                    for item in (completion.get("competitors") or [])
                    if str(item).strip()
                ],
                owner_email=str(completion.get("owner_email") or "") or None,
                notification_email=str(completion.get("notification_email") or "") or None,
                brand_name=str(completion.get("brand_name") or ""),
                progress_state=state,
            )

        def mark_finalized(data: dict[str, Any]) -> dict[str, Any] | None:
            existing_claim = str(data.get("finalize_claim") or "")
            if existing_claim and existing_claim != claim:
                return None
            data["finalize_status"] = "finalized"
            data["finalized_at"] = _utc_now()
            data["updated_at"] = _utc_now()
            return data

        _update_json(path, mark_finalized)
        (audit_dir / PROMPT_PENDING_FILE).unlink(missing_ok=True)
        result["action"] = "finalized"
        result["report_mode"] = report_mode
        return result
    except Exception as exc:
        log.exception(
            "Fan-out merge/finalize failed for %s batch %s: %s",
            audit_dir,
            batch_id,
            exc,
        )
        try:

            def mark_failed(data: dict[str, Any]) -> dict[str, Any] | None:
                data["finalize_status"] = "finalize_failed"
                data["finalize_error"] = str(exc)
                data["updated_at"] = _utc_now()
                return data

            _update_json(path, mark_failed)
        except Exception:
            try:
                fanout_err = _read_json(path) or {}
                fanout_err["finalize_status"] = "finalize_failed"
                fanout_err["finalize_error"] = str(exc)
                fanout_err["updated_at"] = _utc_now()
                _write_json(path, fanout_err)
            except Exception:
                pass
        (audit_dir / PROMPT_PENDING_FILE).unlink(missing_ok=True)
        try:
            from api.audit_runner import _write_run_status

            if mode == "post_audit":
                _write_run_status(
                    audit_dir,
                    {
                        "status": "error",
                        "audit_dir": geo.audit_dir_api_rel(audit_dir),
                        "detail": f"AI prompt probe finalize failed: {exc}",
                        "error": str(exc),
                    },
                )
        except Exception:
            log.exception("Failed to write finalize-error run status")
        result["action"] = "finalize_failed"
        result["error"] = str(exc)
        return result


def run_post_audit_prompt_insights(
    audit_dir: Path,
    *,
    report_mode: bool = True,
    on_step: Any | None = None,
) -> dict[str, str]:
    """
    Blocking post-crawl work: live probes (SOV inputs) then Gemini sentiment cache.

    ``on_step(step_id, detail)`` is called before each phase (``prompt_probes``, ``sentiment``).
    """
    import logging

    log = logging.getLogger(__name__)
    audit_dir = audit_dir.resolve()
    outcome: dict[str, str] = {"probes": "skipped", "sentiment": "skipped", "executive_summary": "skipped"}

    try:
        ctx = _build_context_response(audit_dir)
    except Exception as exc:
        log.warning("Post-audit insights skipped (context): %s", exc)
        return outcome

    prompts = _probe_prompts_for_api(ctx)
    brand = str(ctx.get("brand_name") or "").strip()
    if not prompts or not brand:
        return outcome

    def _emit_step(step_id: str, detail: str, probe_progress: dict[str, Any] | None = None) -> None:
        if not on_step:
            return
        try:
            on_step(step_id, detail, probe_progress=probe_progress)
        except TypeError:
            on_step(step_id, detail)

    try:
        def _probe_progress(event: dict[str, Any]) -> None:
            status = str(event.get("status") or "")
            prompt_index = int(event.get("prompt_index") or 0)
            prompt_total = int(event.get("prompt_total") or 0)
            run_index = int(event.get("run_index") or 0)
            run_total = int(event.get("run_total") or 0)
            platform = str(event.get("platform") or "").replace("_", " ").title()
            completed = int(event.get("completed_calls") or 0)
            planned = int(event.get("planned_calls") or 0)
            elapsed = float(event.get("elapsed_seconds") or 0)
            summary = summarize_probe_progress_event(event)
            if status == "locale_started":
                locale_label = str(event.get("locale_label") or "market")
                locale_index = int(event.get("locale_index") or 1)
                locale_total = int(event.get("locale_total") or 1)
                detail = (
                    f"Starting prompts for {locale_label} "
                    f"(market {locale_index}/{locale_total})…"
                )
            elif status == "started":
                detail = (
                    f"Preparing {prompt_total} prompts × {run_total} runs "
                    f"({planned} platform calls planned)…"
                )
            elif status == "running":
                detail = (
                    f"Prompt {prompt_index}/{prompt_total} · run {run_index}/{run_total} · "
                    f"{platform} started ({completed}/{planned} calls complete)…"
                )
            elif status in {"complete", "error"}:
                outcome_label = "failed" if status == "error" else "finished"
                detail = (
                    f"Prompt {prompt_index}/{prompt_total} · run {run_index}/{run_total} · "
                    f"{platform} {outcome_label} in {elapsed:.1f}s ({completed}/{planned} calls complete)"
                )
            elif status == "responses_complete":
                detail = f"AI responses complete ({completed}/{planned} calls) · identifying mentioned brands…"
            elif status == "entity_detection_started":
                detail = "AI responses complete · identifying competitor entities in the replies…"
            elif status in {"entity_detection_complete", "entity_detection_error"}:
                outcome_label = "failed" if status.endswith("error") else "finished"
                detail = f"Competitor entity detection {outcome_label} in {elapsed:.1f}s"
            else:
                detail = f"AI responses complete ({completed}/{planned} platform calls)"
            _emit_step("prompt_probes", detail, summary)

        run_live_probe_job(
            audit_dir,
            report_mode=report_mode,
            on_progress=_probe_progress,
        )
        outcome["probes"] = "done"
    except Exception as exc:
        log.exception("Live probe job failed for %s: %s", audit_dir, exc)
        outcome["probes"] = f"error: {exc}"
        return outcome

    _emit_step("sentiment", "Analysing brand sentiment in AI assistant replies…")

    try:
        from api.sentiment_jobs import enqueue_sentiment_job, run_prompt_sentiment_analysis, sentiment_jobs_enabled

        if sentiment_jobs_enabled():
            queued = enqueue_sentiment_job(audit_dir)
            outcome["sentiment"] = str(queued.get("status") or "queued")
        else:
            # Local / no job: run inline so post-audit blocking path still completes.
            side = run_prompt_sentiment_analysis(audit_dir)
            outcome["sentiment"] = str(side.get("sentiment") or "skipped")
    except Exception as exc:
        log.exception("Sentiment analysis failed for %s: %s", audit_dir, exc)
        outcome["sentiment"] = f"error: {exc}"

    try:
        from executive_summary_llm import generate_and_cache_for_audit_dir

        generate_and_cache_for_audit_dir(audit_dir)
        outcome["executive_summary"] = "done"
    except Exception as exc:
        log.exception("Executive summary refresh failed for %s: %s", audit_dir, exc)
        outcome["executive_summary"] = f"error: {exc}"

    try:
        from api.export_precompute import precompute_exports_after_probes

        pre = precompute_exports_after_probes(audit_dir)
        outcome["exports"] = str(pre.get("html") or "skipped")
    except Exception as exc:
        log.exception("Export precompute failed for %s: %s", audit_dir, exc)
        outcome["exports"] = f"error: {exc}"

    return outcome


def start_background_live_probe(audit_rel: str, *, report_mode: bool = True) -> None:
    """Queue live probes in the durable Cloud Run Job."""
    import logging

    log = logging.getLogger("uvicorn.error")
    ad = geo.resolve_audit_dir(audit_rel)
    if not (ad / "audit_summary.json").is_file():
        return
    try:
        peek = _build_context_response(ad)
    except Exception as exc:
        log.warning("Skipping background live probe (could not load context): %s", exc)
        return
    prompts = _probe_prompts_for_api(peek)
    if not prompts or not str(peek.get("brand_name") or "").strip():
        return
    try:
        from api.prompt_jobs import enqueue_prompt_job

        enqueue_prompt_job(ad, mode="live", report_mode=report_mode)
    except Exception as exc:
        log.exception("Could not queue prompt probe Job for %s: %s", audit_rel, exc)


def _audit_dir_or_404(audit_id: str) -> Path:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


@router.get("/{audit_id}/prompt-performance")
def get_prompt_performance_context(
    audit_id: str,
    locale: str | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """Return prompt-performance context.

    By default returns **slim** persisted metrics (no reply bodies). Pass
    ``locale`` to scope ``live_probe`` / per-prompt rows to one market
    (``__overall__`` merges slim metrics across locales). Pass ``full=1`` only
    when a caller truly needs reply bodies in one blob (discouraged).
    """
    audit_dir = _audit_dir_or_404(audit_id)
    if full:
        return _build_full_context_response(audit_dir)
    return _build_context_response(
        audit_dir,
        use_persisted_metrics=True,
        locale_key=locale,
        include_all_locales=locale is None,
    )


@router.get("/{audit_id}/prompt-performance/summary")
def get_prompt_performance_summary(audit_id: str) -> dict[str, Any]:
    """Tiny payload for Summary / scorecards: metrics + locale spread, no per-prompt rows."""
    audit_dir = _audit_dir_or_404(audit_id)
    ctx = _build_context_response(
        audit_dir,
        use_persisted_metrics=True,
        locale_key="__overall__",
        include_all_locales=False,
    )
    live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else {}
    return {
        "brand_name": ctx.get("brand_name"),
        "brand_site_url": ctx.get("brand_site_url"),
        "prompt_count": ctx.get("prompt_count"),
        "competitors": ctx.get("competitors") or [],
        "primary_market": ctx.get("primary_market"),
        "prompt_locales": ctx.get("prompt_locales") or [],
        "default_locale_key": ctx.get("default_locale_key"),
        "locale_spread": ctx.get("locale_spread") or [],
        "overall_metrics": ctx.get("overall_metrics"),
        "keyword_sentiment": (live or {}).get("keyword_sentiment"),
        "highlight": {
            "brand": (ctx.get("highlight") or {}).get("brand"),
            "brand_match_tokens": (live or {}).get("brand_match_tokens")
            or (ctx.get("highlight") or {}).get("brand_match_tokens")
            or [],
        },
        "live_probe_in_progress": bool(ctx.get("live_probe_in_progress")),
        "has_probe_data": bool((live or {}).get("per_prompt") or ctx.get("overall_metrics")),
        "active_platforms": (live or {}).get("active_platforms") or [],
        "metrics_from_cache": bool(ctx.get("metrics_from_cache")),
    }


@router.get("/{audit_id}/prompt-performance/locales/{locale_key}")
def get_prompt_performance_locale(audit_id: str, locale_key: str) -> dict[str, Any]:
    """Locale-scoped slim metrics (no reply bodies)."""
    audit_dir = _audit_dir_or_404(audit_id)
    key = locale_key if locale_key != "overall" else "__overall__"
    return _build_context_response(
        audit_dir,
        use_persisted_metrics=True,
        locale_key=key,
        include_all_locales=False,
    )


@router.get("/{audit_id}/prompt-performance/citations")
def get_prompt_performance_citations(
    audit_id: str,
    locale: str | None = None,
) -> dict[str, Any]:
    """Citations-page aggregates only (domains/URLs) — no per_prompt / runs payload.

    Omit ``locale`` to use the same preferred-locale rule as the UI (single
    market for huge multi-locale audits, otherwise Overall).

    Hot path reads ``prompt_performance_citations.json`` only (tens of KB). The
    multi‑MB metrics blob is touched only on cache miss / stale.
    """
    from api.prompt_performance_metrics import (
        ensure_persisted_metrics,
        get_or_build_citations_view_payload,
        payload_from_citations_bundle,
        read_citations_view_bundle,
        read_metrics_file,
        serve_citations_view_if_fresh,
    )

    audit_dir = _audit_dir_or_404(audit_id)
    key = locale
    if key == "overall":
        key = "__overall__"

    cached = serve_citations_view_if_fresh(audit_dir, locale_key=key)
    if cached is not None:
        return cached

    metrics = read_metrics_file(audit_dir)
    if not metrics:
        metrics = ensure_persisted_metrics(
            audit_dir,
            build_full_context=lambda d: _build_full_context_response(d),
        )
    if not metrics:
        # Last resort: stale citations cache still better than 404.
        bundle = read_citations_view_bundle(audit_dir)
        if bundle is not None:
            hit = payload_from_citations_bundle(bundle, locale_key=key)
            if hit is not None:
                return hit
        raise HTTPException(404, "Prompt-performance metrics not available")
    return get_or_build_citations_view_payload(audit_dir, metrics, locale_key=key)


@router.get("/{audit_id}/prompt-performance/prompts/{prompt_id}")
def get_prompt_performance_detail(
    audit_id: str,
    prompt_id: str,
    locale: str = "",
) -> dict[str, Any]:
    """Full reply bodies for a single prompt (expand/detail)."""
    from api.prompt_performance_metrics import find_full_prompt_row

    audit_dir = _audit_dir_or_404(audit_id)
    row = find_full_prompt_row(audit_dir, prompt_id=prompt_id, locale_key=locale)
    if not row:
        raise HTTPException(404, "Prompt not found in probe data")
    return {"prompt": row, "locale_key": row.get("_locale_key") or locale}


@router.get("/{audit_id}/prompt-performance/sentiment")
def get_prompt_sentiment(
    audit_id: str,
    generate: bool = False,
) -> dict[str, Any]:
    """Gemini sentiment analysis of live probe replies (cached per probe file).

    Prefers persisted ``prompt_performance_sentiment.json`` (overall + by_category
    + by_prompt). Does not recompute on every page load: when the cache is missing
    or stale, enqueues the sentiment job (or generates inline when no job is
    configured / ``generate=true``).
    """
    audit_dir = _audit_dir_or_404(audit_id)
    probe_path = audit_dir / LIVE_PROBE_FILE
    saved = _read_json(probe_path)
    live = saved.get("live_probe") if isinstance(saved, dict) else None
    if not isinstance(live, dict) or not (live.get("per_prompt") or []):
        # Try default locale entry
        locales = saved.get("locale_probes") if isinstance(saved, dict) else None
        if isinstance(locales, dict):
            default_key = str((saved or {}).get("default_locale_key") or "")
            entry = locales.get(default_key) if default_key else None
            if not isinstance(entry, dict):
                entry = next((e for e in locales.values() if isinstance(e, dict)), None)
            if isinstance(entry, dict) and isinstance(entry.get("live_probe"), dict):
                live = entry["live_probe"]
    if not isinstance(live, dict) or not (live.get("per_prompt") or []):
        return {
            "available": False,
            "sentiment": None,
            "error": "Run live probes first to analyse reply sentiment.",
            "status": "idle",
        }

    from insights_llm import (
        PromptSentimentResponse,
        collect_per_prompt_from_probe_file,
        ensure_by_prompt_coverage,
        filter_sentiment_for_probed_rows,
        ground_sentiment_on_mentions,
        load_cached_sentiment,
        load_or_generate_prompt_sentiment,
    )
    from api.sentiment_jobs import (
        enqueue_sentiment_job,
        get_sentiment_job_status,
        sentiment_jobs_enabled,
    )

    # Lightweight onboarding read for brand + probed rows (no sanitize/recompute).
    onboarding = _load_audit_onboarding(audit_dir)
    brand, site = _brand_and_site(onboarding)
    pss_rows = _normalize_pss_rows(onboarding.get("products_and_services_rows"))
    probed_rows: list[dict[str, Any]] = []
    if pss_rows:
        _, probed_rows = select_prompts_for_probing(pss_rows)
    per_clean = collect_per_prompt_from_probe_file(saved if isinstance(saved, dict) else None)
    if not per_clean:
        per_clean = [p for p in (live.get("per_prompt") or []) if isinstance(p, dict)]

    cached = load_cached_sentiment(audit_dir, probe_path)
    if cached and isinstance(cached.get("sentiment"), dict):
        try:
            sent = filter_sentiment_for_probed_rows(
                PromptSentimentResponse.model_validate(cached["sentiment"]),
                probed_rows,
            )
            sent = ground_sentiment_on_mentions(
                sent,
                probed_rows=probed_rows,
                per_prompt=per_clean,
            )
            sent = ensure_by_prompt_coverage(sent, per_clean)
            return {
                "available": True,
                "sentiment": sent.model_dump(),
                "error": None,
                "cached": True,
                "status": "ready",
            }
        except Exception:
            pass

    job_status = get_sentiment_job_status(audit_dir)
    job_state = str(job_status.get("status") or "")

    # Prefer async job: enqueue once and let the UI poll the persisted cache.
    if sentiment_jobs_enabled() and not generate:
        if job_state not in {"queued", "starting", "running"}:
            try:
                enqueue_sentiment_job(audit_dir)
                job_status = get_sentiment_job_status(audit_dir)
                job_state = str(job_status.get("status") or "queued")
            except Exception as exc:
                log.exception("Could not enqueue sentiment job for %s", audit_id)
                return {
                    "available": False,
                    "sentiment": None,
                    "error": f"Could not start sentiment analysis: {exc}",
                    "status": "error",
                }
        return {
            "available": False,
            "sentiment": None,
            "error": None,
            "cached": False,
            "status": job_state or "queued",
            "job": job_status,
        }

    sent, err = load_or_generate_prompt_sentiment(
        audit_dir,
        live,
        brand_name=brand,
        site_url=site,
        pss_rows=probed_rows if probed_rows else None,
        per_prompt_override=per_clean,
        force=bool(generate),
    )
    if sent:
        return {
            "available": True,
            "sentiment": sent.model_dump(),
            "error": None,
            "cached": False,
            "status": "ready",
        }
    return {
        "available": False,
        "sentiment": None,
        "error": err or "Sentiment analysis failed.",
        "status": "error",
    }


class RunProbesBody(BaseModel):
    report_mode: bool = True
    failed_only: bool = False
    locale_keys: list[str] | None = None


@router.post("/{audit_id}/prompt-performance/run-probes")
def run_prompt_probes(audit_id: str, body: RunProbesBody | None = None) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    report_mode = body.report_mode if body is not None else True
    failed_only = bool(body.failed_only) if body is not None else False
    locale_keys = list(body.locale_keys) if body is not None and body.locale_keys else None
    ctx_resp = _build_context_response(audit_dir)
    prompts = _probe_prompts_for_api(ctx_resp)
    if not prompts:
        raise HTTPException(400, "No prompts on file for this audit—complete setup with products and prompts first.")
    brand = str(ctx_resp.get("brand_name") or "").strip()
    if not brand:
        raise HTTPException(400, "Brand name is required—re-run setup or add brand_name_used to onboarding_context.json.")
    try:
        from api.prompt_jobs import enqueue_prompt_job

        return enqueue_prompt_job(
            audit_dir,
            mode="live",
            report_mode=report_mode,
            failed_only=failed_only,
            locale_keys=locale_keys,
        )
    except Exception as exc:
        log.exception("Could not enqueue prompt probe Job for %s", audit_id)
        raise HTTPException(502, str(exc)) from exc


class RunAioProbesBody(BaseModel):
    max_prompts: int = 25


@router.post("/{audit_id}/prompt-performance/run-aio-probes")
def run_aio_probes_endpoint(audit_id: str, body: RunAioProbesBody | None = None) -> dict[str, Any]:
    """Queue Google AI Overview probes in the durable Cloud Run Job."""
    audit_dir = _audit_dir_or_404(audit_id)
    ctx_resp = _build_context_response(audit_dir)
    prompts = _probe_prompts_for_api(ctx_resp)
    if not prompts:
        raise HTTPException(400, "No prompts on file for this audit.")
    max_p = (body.max_prompts if body is not None else 25) or 25
    try:
        from api.prompt_jobs import enqueue_prompt_job

        return enqueue_prompt_job(audit_dir, mode="aio", max_prompts=max_p)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/{audit_id}/prompt-performance/aio-availability")
def aio_availability(audit_id: str) -> dict[str, Any]:
    """Check whether Gemini grounded search is configured."""
    from competitor_suggest import _gemini_api_key
    key = _gemini_api_key()
    return {"available": bool(key), "reason": None if key else "GEMINI_API_KEY not configured."}


class HighlightBody(BaseModel):
    text: str
    model: str = "gemini"


@router.post("/{audit_id}/prompt-performance/highlight")
def highlight_reply(audit_id: str, body: HighlightBody) -> dict[str, str]:
    from prompt_suggest import highlight_response_html

    audit_dir = _audit_dir_or_404(audit_id)
    saved = _read_json(audit_dir / LIVE_PROBE_FILE)
    ctx = _build_context_response(audit_dir)
    hb = str((saved or {}).get("highlight_brand") or ctx.get("brand_name") or "")
    hc = (saved or {}).get("highlight_comp_urls")
    hcb = (saved or {}).get("highlight_comp_brands")
    if not isinstance(hc, list):
        hc = []
    if not isinstance(hcb, list):
        hcb = []
    live = (saved or {}).get("live_probe") if isinstance(saved, dict) else None
    h_reply: list[str] | None = None
    brand_match_tokens_list: list[str] | None = None
    product_line_aliases: list[str] | None = None
    if isinstance(live, dict):
        raw = live.get("reply_detected_brand_names")
        if isinstance(raw, list):
            h_reply = [str(x).strip() for x in raw if str(x).strip()]
        raw_tokens = live.get("brand_match_tokens")
        if isinstance(raw_tokens, list):
            brand_match_tokens_list = [str(x).strip() for x in raw_tokens if str(x).strip()]
        raw_aliases = live.get("product_line_aliases")
        if isinstance(raw_aliases, list):
            product_line_aliases = [str(x).strip() for x in raw_aliases if str(x).strip()]
    html_out = highlight_response_html(
        body.text,
        hb,
        [str(x) for x in hc],
        [str(x) for x in hcb],
        reply_detected_brands=h_reply,
        brand_match_tokens_list=brand_match_tokens_list,
        product_line_aliases=product_line_aliases,
    )
    return {"html": html_out}


class TrackCompetitorBody(BaseModel):
    website_url: str
    brand_name: str = ""


@router.post("/{audit_id}/prompt-performance/track-competitor")
def track_competitor(audit_id: str, body: TrackCompetitorBody) -> dict[str, Any]:
    from geo_setup_llm import normalize_competitor_url

    audit_dir = _audit_dir_or_404(audit_id)
    nu = normalize_competitor_url(body.website_url.strip())
    if not nu:
        raise HTTPException(400, "Invalid competitor URL")
    bn = (body.brand_name.strip() or _fallback_brand_label_from_url(nu)).strip()
    ctx = _load_audit_onboarding(audit_dir)
    det = _competitors_detail(ctx)
    exist = {normalize_competitor_url(str(c.get("competitor_website") or "")) for c in det}
    exist.discard("")
    if nu in exist:
        return {"ok": True, "added": False, "competitors": det}
    if len(det) >= MAX_COMPETITORS:
        raise HTTPException(400, f"At most {MAX_COMPETITORS} competitors allowed")
    det.append({"competitor_website": nu, "competitor_brand": bn})
    site_u = str(ctx.get("brand_website_used") or ctx.get("audit_base_url") or "").strip()
    ob_path = audit_dir / "onboarding_context.json"
    ob = _read_json(ob_path) or {}
    ob["competitors_detail"] = det
    ob["accepted_competitors"] = [c["competitor_website"] for c in det]
    ob_path.write_text(json.dumps(ob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (audit_dir / "competitors.json").write_text(
        json.dumps({"website_url": site_u, "competitors": det}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"ok": True, "added": True, "competitors": det}


class RegeneratePromptsBody(BaseModel):
    manual_categories: list[str] = Field(default_factory=list)


@router.post("/{audit_id}/prompt-performance/regenerate-prompts")
def regenerate_prompts(audit_id: str, body: RegeneratePromptsBody) -> dict[str, Any]:
    from prompt_suggest import infer_category_labels_from_top_pages, suggest_ai_platform_prompts

    audit_dir = _audit_dir_or_404(audit_id)
    ctx = _load_audit_onboarding(audit_dir)
    pss_rows = _normalize_pss_rows(ctx.get("products_and_services_rows"))
    if pss_rows:
        raise HTTPException(400, "This audit uses products & services prompts—regenerate is not available.")
    brand, site = _brand_and_site(ctx)
    if not brand:
        raise HTTPException(400, "Brand name is required")
    stored = [str(c).strip() for c in (ctx.get("prompt_category_labels") or []) if str(c).strip()]
    manual = [str(c).strip() for c in body.manual_categories if str(c).strip()]
    top_pages = ctx.get("ga4_top_pages") if isinstance(ctx.get("ga4_top_pages"), list) else []
    industry = str(ctx.get("industry_used") or "").strip()
    inferred = (
        infer_category_labels_from_top_pages(top_pages, selected_industry=industry) if top_pages else []
    )
    cat_labels = list(dict.fromkeys([c for c in stored + inferred + manual if c.strip()]))
    if not cat_labels:
        raise HTTPException(400, "Add category context (setup or manual categories) before regenerating prompts.")
    mcc, mid = _primary_market_from_context(ctx)
    try:
        prompts_gen = suggest_ai_platform_prompts(
            cat_labels,
            brand_name=brand,
            site_url=site,
            industry=industry,
            max_prompts=10,
            market_country=mcc,
            market_country_code=mid,
        )
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    ob_path = audit_dir / "onboarding_context.json"
    ob = _read_json(ob_path) or {}
    ob["suggested_prompts"] = prompts_gen
    ob["product_service_prompts"] = prompts_gen
    ob["prompt_category_labels"] = cat_labels
    ob_path.write_text(json.dumps(ob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (audit_dir / LIVE_PROBE_FILE).unlink(missing_ok=True)
    return {"prompts": prompts_gen, "category_labels": cat_labels}


class AddPromptBody(BaseModel):
    prompt: str
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    run_probes: bool = False


@router.post("/{audit_id}/prompt-performance/add-prompt")
def add_prompt(audit_id: str, body: AddPromptBody) -> dict[str, Any]:
    """Add a new prompt to an existing audit and optionally re-run probes."""
    audit_dir = _audit_dir_or_404(audit_id)
    new_prompt = (body.prompt or "").strip()
    if not new_prompt:
        raise HTTPException(400, "Prompt text is required.")
    category = (body.category or "Custom prompts").strip() or "Custom prompts"

    ob_path = audit_dir / "onboarding_context.json"
    ob = _read_json(ob_path) or {}

    # Attach primary-market geo locator so custom prompts stay market-neutral in the UI
    # but probe correctly (and can be adapted for extra locales).
    try:
        from geo_market import resolve_primary_market
        from prompt_suggest import ensure_prompt_contains_geo_locator, geo_locator_phrase_for_market

        mcc = str(ob.get("geo_market_country") or "").strip()
        mid = str(ob.get("geo_market_country_code") or "").strip()
        mc, mid = resolve_primary_market(mcc, mid)
        phrase = geo_locator_phrase_for_market(mc, mid)
        if phrase:
            new_prompt = ensure_prompt_contains_geo_locator(new_prompt, phrase)
    except Exception:
        pass

    # Append to pss_rows (products_and_services_rows) so it appears in the next probe run
    pss_rows = list(ob.get("products_and_services_rows") or [])
    # Find existing category or create new
    matched = next((r for r in pss_rows if isinstance(r, dict) and r.get("product_or_service") == category), None)
    if matched is not None:
        existing = list(matched.get("prompts") or [])
        if new_prompt not in existing:
            existing.append(new_prompt)
        matched["prompts"] = existing
        custom_prompts = list(matched.get("custom_prompts") or [])
        if new_prompt not in custom_prompts:
            custom_prompts.append(new_prompt)
        matched["custom_prompts"] = custom_prompts
        prompt_tags = dict(matched.get("prompt_tags") or {})
        if body.tags:
            prompt_tags[new_prompt] = [str(tag).strip() for tag in body.tags if str(tag).strip()]
        matched["prompt_tags"] = prompt_tags
    else:
        pss_rows.append(
            {
                "product_or_service": category,
                "prompts": [new_prompt],
                "prompt_tags": {new_prompt: [str(tag).strip() for tag in body.tags if str(tag).strip()]},
                "custom_prompts": [new_prompt],
                "is_custom_topic": True,
            }
        )

    ob["products_and_services_rows"] = pss_rows
    ob_path.write_text(json.dumps(ob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    if body.run_probes:
        try:
            from api.prompt_jobs import enqueue_prompt_job

            queued = enqueue_prompt_job(audit_dir, mode="live", report_mode=True)
        except Exception as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"added": True, "run_probes": True, "prompt": new_prompt, **queued}

    return {"added": True, "run_probes": False, "prompt": new_prompt}


class PromptTagSelection(BaseModel):
    product_or_service: str
    prompt: str


class AddPromptTagsBody(BaseModel):
    selections: list[PromptTagSelection] = Field(default_factory=list, min_length=1)
    tags: list[str] = Field(default_factory=list, min_length=1)


@router.post("/{audit_id}/prompt-performance/add-tags")
def add_prompt_tags(audit_id: str, body: AddPromptTagsBody) -> dict[str, Any]:
    """Add one or more tags to selected prompts without re-running probes."""
    audit_dir = _audit_dir_or_404(audit_id)
    requested = {
        (
            selection.product_or_service.strip().lower(),
            selection.prompt.strip().lower(),
        )
        for selection in body.selections
        if selection.product_or_service.strip() and selection.prompt.strip()
    }
    tags = list(dict.fromkeys(str(tag).strip() for tag in body.tags if str(tag).strip()))
    if not requested:
        raise HTTPException(400, "Select at least one prompt.")
    if not tags:
        raise HTTPException(400, "Choose or add at least one tag.")

    ob_path = audit_dir / "onboarding_context.json"
    ob = _read_json(ob_path) or {}
    pss_rows = list(ob.get("products_and_services_rows") or [])
    available = {
        (
            str(row.get("product_or_service") or "").strip().lower(),
            str(prompt).strip().lower(),
        )
        for row in pss_rows
        if isinstance(row, dict)
        for prompt in row.get("prompts") or []
    }
    missing = requested - available
    if missing:
        raise HTTPException(404, "One or more selected prompts were not found in this audit.")

    for row in pss_rows:
        if not isinstance(row, dict):
            continue
        topic_key = str(row.get("product_or_service") or "").strip().lower()
        prompt_tags = dict(row.get("prompt_tags") or {})
        for prompt in row.get("prompts") or []:
            prompt_text = str(prompt).strip()
            if (topic_key, prompt_text.lower()) not in requested:
                continue
            existing = [str(tag).strip() for tag in prompt_tags.get(prompt_text, []) if str(tag).strip()]
            prompt_tags[prompt_text] = list(dict.fromkeys(existing + tags))
        row["prompt_tags"] = prompt_tags

    ob["products_and_services_rows"] = pss_rows
    ob_path.write_text(json.dumps(ob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    products_path = audit_dir / "products_and_services.json"
    products_data = _read_json(products_path) or {}
    products_data["rows"] = pss_rows
    products_data["products_and_services"] = [
        str(row.get("product_or_service") or "").strip()
        for row in pss_rows
        if isinstance(row, dict) and str(row.get("product_or_service") or "").strip()
    ]
    products_path.write_text(
        json.dumps(products_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {"updated": len(requested), "tags": tags}


class PromptLocalesBody(BaseModel):
    locales: list[dict[str, Any]] = Field(default_factory=list)


@router.put("/{audit_id}/prompt-performance/locales")
def update_prompt_locales(audit_id: str, body: PromptLocalesBody) -> dict[str, Any]:
    """Save market+language locale pairs used for multi-locale prompt probes."""
    from prompt_locales import locales_from_onboarding, normalize_prompt_locales

    audit_dir = _audit_dir_or_404(audit_id)
    ob_path = audit_dir / "onboarding_context.json"
    ob = _read_json(ob_path) or {}
    mcc = str(ob.get("geo_market_country") or "").strip()
    mid = str(ob.get("geo_market_country_code") or "").strip()
    normalized = normalize_prompt_locales(
        body.locales,
        market_country=mcc,
        market_country_code=mid,
    )
    ob["prompt_locales"] = normalized
    ob_path.write_text(json.dumps(ob, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "prompt_locales": locales_from_onboarding(ob),
    }
