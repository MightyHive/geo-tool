"""Prompt performance (live probes, SOV) for the TypeScript report UI."""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api import geo_services as geo
from api.prompt_selection import select_flat_prompts_for_probing, select_prompts_for_probing

router = APIRouter(prefix="/api/audits", tags=["prompt-performance"])

MAX_COMPETITORS = 12
LIVE_PROBE_FILE = "prompt_performance_live_probe.json"
PROBE_PENDING_FILE = "prompt_performance_probe_pending.json"


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
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


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
            out.append({"product_or_service": label, "prompts": ps})
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
    return merged


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
    if report_mode:
        return [], []
    urls: list[str] = []
    brands: list[str] = []
    for c in competitors:
        u = str(c.get("competitor_website") or "").strip()
        if not u:
            continue
        urls.append(u)
        brands.append(str(c.get("competitor_brand") or "").strip())
    return urls, brands


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
) -> None:
    from api.probe_platforms import sanitize_live_probe

    live = sanitize_live_probe(live)
    payload = {
        "live_probe": live,
        "highlight_brand": highlight_brand,
        "highlight_comp_urls": highlight_comp_urls,
        "highlight_comp_brands": highlight_comp_brands,
    }
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / LIVE_PROBE_FILE).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _probed_pss_rows_from_context(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    probed = ctx.get("probed_pss_rows")
    if isinstance(probed, list):
        return probed
    rows = ctx.get("pss_rows")
    if ctx.get("use_pss") and isinstance(rows, list) and rows:
        _, probed_rows = select_prompts_for_probing(rows)
        return probed_rows
    return []


def _build_context_response(audit_dir: Path) -> dict[str, Any]:
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
    saved = _read_json(audit_dir / LIVE_PROBE_FILE)
    live_probe = saved.get("live_probe") if isinstance(saved, dict) else None
    if isinstance(live_probe, dict):
        from api.probe_platforms import sanitize_live_probe

        live_probe = sanitize_live_probe(live_probe)
    highlight = {
        "brand": str((saved or {}).get("highlight_brand") or brand),
        "competitor_urls": (saved or {}).get("highlight_comp_urls") or [],
        "competitor_brands": (saved or {}).get("highlight_comp_brands") or [],
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
    return {
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
        "category_labels": [
            str(x).strip()
            for x in (ctx.get("prompt_category_labels") or ctx.get("accepted_categories") or [])
            if str(x).strip()
        ],
        "industry": str(ctx.get("industry_used") or "").strip(),
        "live_probe": live_probe if isinstance(live_probe, dict) else None,
        "live_probe_in_progress": live_probe_is_pending(audit_dir),
        "highlight": highlight,
        "sov_history": sov_history,
        "sov_history_by_product": sov_history_by_product,
    }


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


def _live_probe_pending_path(audit_dir: Path) -> Path:
    return audit_dir / PROBE_PENDING_FILE


def live_probe_is_pending(audit_dir: Path) -> bool:
    if not _live_probe_pending_path(audit_dir).is_file():
        return False
    saved = _load_saved_live_probe(audit_dir)
    if saved and isinstance(saved.get("per_prompt"), list) and len(saved["per_prompt"]) > 0:
        return False
    return True


def _execute_live_probe(
    audit_dir: Path, *, report_mode: bool, prompts: list[str]
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
    mcc = str((ctx_resp.get("primary_market") or {}).get("country") or "")
    mid = str((ctx_resp.get("primary_market") or {}).get("country_id") or "")
    return run_live_prompt_probes(
        prompts,
        brand_name=brand,
        brand_site_url=site,
        competitor_urls=comp_urls,
        competitor_brands=comp_brands,
        max_prompts=len(prompts),
        market_country=mcc,
        market_country_code=mid,
    )


def run_live_probe_job(audit_dir: Path, *, report_mode: bool = True) -> None:
    """Run live probes and save; used by background job and manual POST."""
    ctx_resp = _build_context_response(audit_dir)
    prompts = _probe_prompts_for_api(ctx_resp)
    if not prompts:
        return
    brand = str(ctx_resp.get("brand_name") or "").strip()
    if not brand:
        return
    live = _execute_live_probe(audit_dir, report_mode=report_mode, prompts=prompts)
    competitors = ctx_resp.get("competitors") or []
    comp_urls, comp_brands = _competitor_lists(competitors, report_mode=report_mode)
    _save_live_probe(
        audit_dir,
        live,
        highlight_brand=brand,
        highlight_comp_urls=comp_urls,
        highlight_comp_brands=comp_brands,
    )


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
    outcome: dict[str, str] = {"probes": "skipped", "sentiment": "skipped"}

    try:
        ctx = _build_context_response(audit_dir)
    except Exception as exc:
        log.warning("Post-audit insights skipped (context): %s", exc)
        return outcome

    prompts = _probe_prompts_for_api(ctx)
    brand = str(ctx.get("brand_name") or "").strip()
    if not prompts or not brand:
        return outcome

    try:
        run_live_probe_job(audit_dir, report_mode=report_mode)
        outcome["probes"] = "done"
    except Exception as exc:
        log.exception("Live probe job failed for %s: %s", audit_dir, exc)
        outcome["probes"] = f"error: {exc}"
        return outcome

    if on_step:
        on_step("sentiment", "Analysing brand sentiment in AI assistant replies…")

    try:
        ctx2 = _build_context_response(audit_dir)
        live = ctx2.get("live_probe")
        if not isinstance(live, dict) or not (live.get("per_prompt") or []):
            return outcome
        from insights_llm import load_or_generate_prompt_sentiment

        pss_rows = _probed_pss_rows_from_context(ctx2)
        sent, err = load_or_generate_prompt_sentiment(
            audit_dir,
            live,
            brand_name=brand,
            site_url=str(ctx2.get("brand_site_url") or ""),
            pss_rows=pss_rows if isinstance(pss_rows, list) else None,
        )
        if sent:
            outcome["sentiment"] = "done"
        else:
            outcome["sentiment"] = f"error: {err or 'unknown'}"
    except Exception as exc:
        log.exception("Sentiment analysis failed for %s: %s", audit_dir, exc)
        outcome["sentiment"] = f"error: {exc}"

    return outcome


def start_background_live_probe(audit_rel: str, *, report_mode: bool = True) -> None:
    """Start live probes in a daemon thread (non-blocking)."""
    import logging
    import threading

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
    pending = _live_probe_pending_path(ad)
    if pending.is_file():
        return
    pending.write_text('{"status":"running"}\n', encoding="utf-8")

    def _thread_main() -> None:
        try:
            run_live_probe_job(ad, report_mode=report_mode)
        except Exception as exc:
            log.exception("Background live probe failed for %s: %s", audit_rel, exc)
        finally:
            pending.unlink(missing_ok=True)

    threading.Thread(target=_thread_main, daemon=True).start()


def _audit_dir_or_404(audit_id: str) -> Path:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


@router.get("/{audit_id}/prompt-performance")
def get_prompt_performance_context(audit_id: str) -> dict[str, Any]:
    return _build_context_response(_audit_dir_or_404(audit_id))


@router.get("/{audit_id}/prompt-performance/sentiment")
def get_prompt_sentiment(audit_id: str) -> dict[str, Any]:
    """Gemini sentiment analysis of live probe replies (cached per probe file)."""
    audit_dir = _audit_dir_or_404(audit_id)
    ctx = _build_context_response(audit_dir)
    live = ctx.get("live_probe")
    if not isinstance(live, dict) or not (live.get("per_prompt") or []):
        return {"available": False, "sentiment": None, "error": "Run live probes first to analyse reply sentiment."}

    from insights_llm import (
        PromptSentimentResponse,
        filter_sentiment_for_probed_rows,
        load_cached_sentiment,
        load_or_generate_prompt_sentiment,
    )

    probe_path = audit_dir / LIVE_PROBE_FILE
    probed_rows = _probed_pss_rows_from_context(ctx)
    cached = load_cached_sentiment(audit_dir, probe_path)
    if cached and isinstance(cached.get("sentiment"), dict):
        try:
            sent = filter_sentiment_for_probed_rows(
                PromptSentimentResponse.model_validate(cached["sentiment"]),
                probed_rows,
            )
            return {
                "available": True,
                "sentiment": sent.model_dump(),
                "error": None,
                "cached": True,
            }
        except Exception:
            pass

    pss_rows = probed_rows
    sent, err = load_or_generate_prompt_sentiment(
        audit_dir,
        live,
        brand_name=str(ctx.get("brand_name") or ""),
        site_url=str(ctx.get("brand_site_url") or ""),
        pss_rows=pss_rows if isinstance(pss_rows, list) else None,
    )
    if sent:
        return {"available": True, "sentiment": sent.model_dump(), "error": None, "cached": False}
    return {"available": False, "sentiment": None, "error": err or "Sentiment analysis failed."}


class RunProbesBody(BaseModel):
    report_mode: bool = True


@router.post("/{audit_id}/prompt-performance/run-probes")
def run_prompt_probes(audit_id: str, body: RunProbesBody | None = None) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    report_mode = body.report_mode if body is not None else True
    ctx_resp = _build_context_response(audit_dir)
    prompts = _probe_prompts_for_api(ctx_resp)
    if not prompts:
        raise HTTPException(400, "No prompts on file for this audit—complete setup with products and prompts first.")
    brand = str(ctx_resp.get("brand_name") or "").strip()
    if not brand:
        raise HTTPException(400, "Brand name is required—re-run setup or add brand_name_used to onboarding_context.json.")
    competitors = ctx_resp.get("competitors") or []
    comp_urls, comp_brands = _competitor_lists(competitors, report_mode=report_mode)
    try:
        live = _execute_live_probe(audit_dir, report_mode=report_mode, prompts=prompts)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    _save_live_probe(
        audit_dir,
        live,
        highlight_brand=brand,
        highlight_comp_urls=comp_urls,
        highlight_comp_brands=comp_brands,
    )
    _live_probe_pending_path(audit_dir).unlink(missing_ok=True)
    # Drop stale sentiment cache so the next context load regenerates from new replies.
    from insights_llm import SENTIMENT_FILE

    (audit_dir / SENTIMENT_FILE).unlink(missing_ok=True)
    return {"live_probe": live, "highlight": {"brand": brand, "competitor_urls": comp_urls, "competitor_brands": comp_brands}}


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
    if isinstance(live, dict):
        raw = live.get("reply_detected_brand_names")
        if isinstance(raw, list):
            h_reply = [str(x).strip() for x in raw if str(x).strip()]
    html_out = highlight_response_html(
        body.text,
        hb,
        [str(x) for x in hc],
        [str(x) for x in hcb],
        reply_detected_brands=h_reply,
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
