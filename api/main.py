"""FastAPI backend for the GEO audit web UI."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

from api import geo_services as geo
from api.auth import (
    auth_enabled,
    auth_mode,
    cookie_secret,
    create_auth_router,
    current_user,
    require_user,
)
from api.auth_config import default_web_public_origin, load_auth_config
from api.ga4 import create_ga4_router
from api.iap_middleware import IAPMiddleware
from api.executive_summary import router as executive_summary_router
from api.prompt_performance import router as prompt_performance_router
from api.topic_content_samples import router as topic_content_samples_router
from api.recommendations import router as recommendations_router
from api.probe_history import router as probe_history_router, scheduled_router as probe_scheduled_router
from api.score_history import router as score_history_router
from api.reddit_insights import router as reddit_insights_router
from api.wizard import router as wizard_router
from api.youtube_insights import router as youtube_insights_router
from api.ai_impact import router as ai_impact_router
from api.gsc import router as gsc_router
from api.workshop_dashboards import router as workshop_dashboards_router
from api.page_audits import router as page_audits_router
from geo_app_env import current_app_env, load_app_environment

load_app_environment()

app = FastAPI(title="GEO Audit API", version="0.1.0")

_web_origin = default_web_public_origin()
_cors_origins = list(
    {
        _web_origin,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        os.environ.get("DEPLOY_PUBLIC_ORIGIN", "").rstrip("/"),
    }
)
_cors_origins = [o for o in _cors_origins if o]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
_session_https = current_app_env() in ("staging", "production")
app.add_middleware(
    SessionMiddleware,
    secret_key=cookie_secret(),
    https_only=_session_https,
)
app.add_middleware(IAPMiddleware)
app.include_router(create_auth_router())
app.include_router(create_ga4_router())
app.include_router(gsc_router)
app.include_router(ai_impact_router)
app.include_router(wizard_router)
app.include_router(prompt_performance_router)
app.include_router(topic_content_samples_router)
app.include_router(executive_summary_router)
app.include_router(recommendations_router)
app.include_router(probe_history_router)
app.include_router(probe_scheduled_router)
app.include_router(score_history_router)
app.include_router(reddit_insights_router)
app.include_router(youtube_insights_router)
app.include_router(workshop_dashboards_router)
app.include_router(page_audits_router)


class WizardProductRow(BaseModel):
    product_or_service: str = ""
    prompts: list[str] = Field(default_factory=list)
    prompt_tags: dict[str, list[str]] = Field(default_factory=dict)
    custom_prompts: list[str] = Field(default_factory=list)
    is_custom_topic: bool = False


class WizardCompetitorRow(BaseModel):
    competitor_brand: str = ""
    competitor_website: str = ""
    included: bool = True


class WizardAdditionalMarketRow(BaseModel):
    country: str = ""
    country_code: str = ""


class RunAuditRequest(BaseModel):
    brand_name: str
    brand_website: str
    industry: str = ""
    competitors: list[str] = Field(default_factory=list, max_length=10)
    max_urls: int = 40
    delay: float = 0.2
    out_base: str = "audit_output"
    ga4_property_id: str | None = None
    ga4_ai_channels: str | None = None
    ga4_conversion_event_name: str = Field(
        default="purchase",
        min_length=1,
        max_length=500,
    )
    wizard_market_country: str = ""
    wizard_market_country_code: str = ""
    wizard_prompt_locales: list[dict[str, Any]] = Field(default_factory=list)
    wizard_additional_markets: list[WizardAdditionalMarketRow] = Field(default_factory=list)
    wizard_products: list[WizardProductRow] = Field(default_factory=list)
    wizard_competitors: list[WizardCompetitorRow] = Field(default_factory=list, max_length=10)
    crawl_urls: list[str] | None = None
    notification_email: str | None = None
    skip_prompt_probes: bool = False
    # Always-on when competitors are configured; client values are overridden in the runner.
    follow_on_competitor_crawl: bool = True
    # From wizard suggest-products; validated again at run start. Empty → Gemini fallback classify.
    model_category: str | None = None

    @field_validator("ga4_conversion_event_name")
    @classmethod
    def _validate_ga4_conversion_event_name(cls, value: str) -> str:
        from api.conversion_events import (
            ConversionEventParseError,
            normalize_conversion_event_spec,
        )

        try:
            return normalize_conversion_event_spec(value)
        except ConversionEventParseError as exc:
            raise ValueError(str(exc)) from exc


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, Any]:
    cfg = load_auth_config()
    from api.iap import load_iap_config

    iap_cfg = load_iap_config()
    mode = auth_mode()
    base = geo.app_config()
    base["auth"] = {
        "mode": mode,
        "enabled": mode != "none",
        "login_url": "/api/auth/login" if mode == "oauth" else None,
        "logout_available": mode == "oauth",
        "iap_enforce": bool(iap_cfg.enforce) if iap_cfg else False,
        "redirect_uri": cfg.redirect_uri if cfg else None,
        "web_public_origin": cfg.web_public_origin if cfg else _web_origin,
    }
    return base


@app.get("/api/industries")
def industries() -> list[str]:
    return geo.get_industries()


@app.get("/api/domains/suggest")
def domain_suggest(q: str = "", limit: int = Query(12, ge=1, le=24)) -> list[dict[str, str]]:
    return geo.suggest_domains(q, limit=limit)


@app.get("/api/audits/local")
def audits_local(
    limit: int | None = Query(None, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[dict[str, Any]]:
    """Metadata-only primary audit list. Use limit/offset for server-side paging."""
    return geo.list_primary_audits(limit=limit, offset=offset)


@app.get("/api/audits/latest")
def audit_latest() -> dict[str, Any]:
    path = geo.latest_audit_dir()
    if path is None:
        raise HTTPException(404, "No local audits found")
    summary = geo.load_audit_summary(path)
    return {
        "audit_dir": geo.audit_dir_api_rel(path),
        "summary": summary,
    }


@app.get("/api/audits/sample")
def audit_sample() -> dict[str, Any]:
    path = geo.sample_audit_dir()
    if path is None:
        raise HTTPException(404, "Sample audit not found")
    summary = geo.load_audit_summary(path)
    return {
        "audit_dir": geo.audit_dir_api_rel(path),
        "summary": summary,
    }


@app.get("/api/audits/{audit_id}/report.html", response_model=None)
def audit_report_html(audit_id: str, embed: bool = False) -> Response:
    audit_dir = geo.resolve_audit_dir(audit_id)
    report_path = audit_dir / "report.html"
    if not report_path.is_file():
        cr = geo.load_create_report()
        try:
            cr.generate_reports(audit_dir, None)
        except Exception as exc:
            raise HTTPException(404, f"report.html not found: {exc}") from exc
    if not report_path.is_file():
        raise HTTPException(404, "report.html not found")
    if not embed:
        return FileResponse(report_path, media_type="text/html")
    html = report_path.read_text(encoding="utf-8", errors="replace")
    html = geo.enrich_report_brand_visibility_from_citations(html, audit_dir)
    html = geo.refresh_competitor_comparison_for_embed(html, audit_dir)
    html = geo.prepare_report_html_for_embed(html)
    return HTMLResponse(html, media_type="text/html")


@app.get("/api/audits/{audit_id}/report.pdf")
def audit_report_pdf(audit_id: str, section: str | None = None, sync: int = 0) -> Response:
    """
    Legacy sync PDF download. Prefer POST/GET ``/exports/pdf`` (async job).
    Pass ``sync=1`` to force on-request generation (slow; for debugging).
    """
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")

    from api.export_jobs import artifact_path, get_pdf_export_status
    from api.pdf_service import generate_audit_pdf, generate_section_pdf, pdf_filename_for_audit

    if not sync:
        status = get_pdf_export_status(audit_dir, section=section)
        artifact = artifact_path(audit_dir, section)
        if status.get("ready") and artifact.is_file():
            filename = pdf_filename_for_audit(
                audit_dir,
                section=section.strip() if section and section.strip() else None,
            )
            return Response(
                content=artifact.read_bytes(),
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    try:
        if section and section.strip():
            pdf_bytes = generate_section_pdf(audit_dir, section.strip())
            filename = pdf_filename_for_audit(audit_dir, section=section.strip())
        else:
            pdf_bytes = generate_audit_pdf(audit_dir)
            filename = pdf_filename_for_audit(audit_dir)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"PDF generation failed: {exc}") from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/audits/{audit_id}/exports/pdf")
def start_pdf_export(audit_id: str, section: str | None = None) -> dict:
    """Enqueue PDF generation (Cloud Run Job or local thread)."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    from api.export_jobs import enqueue_pdf_export

    try:
        return enqueue_pdf_export(audit_dir, section=section)
    except Exception as exc:
        raise HTTPException(502, f"Could not start PDF export: {exc}") from exc


@app.get("/api/audits/{audit_id}/exports/pdf")
def pdf_export_status(audit_id: str, section: str | None = None) -> dict:
    """Poll PDF export status."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    from api.export_jobs import get_pdf_export_status

    return get_pdf_export_status(audit_dir, section=section)


@app.get("/api/audits/{audit_id}/exports/pdf/file")
def pdf_export_file(audit_id: str, section: str | None = None) -> Response:
    """Download a completed PDF export artifact."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    from api.export_jobs import artifact_path, get_pdf_export_status
    from api.pdf_service import pdf_filename_for_audit

    status = get_pdf_export_status(audit_dir, section=section)
    artifact = artifact_path(audit_dir, section)
    if not status.get("ready") or not artifact.is_file():
        raise HTTPException(409, "PDF export is not ready yet")
    filename = pdf_filename_for_audit(
        audit_dir,
        section=section.strip() if section and section.strip() else None,
    )
    return Response(
        content=artifact.read_bytes(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/audits/{audit_id}/report-all-pages.html", response_model=None)
def audit_report_all_pages_html(audit_id: str) -> Response:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")

    from api.export_precompute import read_precomputed_full_html
    from api.html_service import generate_all_pages_html

    try:
        html = read_precomputed_full_html(audit_dir)
        if html is None:
            html = generate_all_pages_html(audit_dir)
    except Exception as exc:
        raise HTTPException(500, f"HTML generation failed: {exc}") from exc

    slug = audit_dir.name.replace("/", "-")
    filename = f"geo-report-{slug}-all-pages.html"
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/audits/{audit_id}/report-section.html", response_model=None)
def audit_report_section_html(audit_id: str, section: str) -> Response:
    """Download a single report section as standalone HTML (current React layout)."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    from api.export_precompute import read_precomputed_section_html
    from api.html_service import generate_section_html, resolve_export_section

    target = resolve_export_section(section)
    if not target:
        raise HTTPException(400, f"Section is not available for download: {section}")

    try:
        html = read_precomputed_section_html(audit_dir, target)
        if html is None:
            slug, html = generate_section_html(audit_dir, target)
        else:
            slug = re.sub(r"[^\w\-]+", "-", target).strip("-") or "section"
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"HTML generation failed: {exc}") from exc

    safe_audit = audit_dir.name.replace("/", "-")
    filename = f"geo-report-{safe_audit}-{slug}.html"
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/audits/{audit_id}/run-status")
def audit_run_status(audit_id: str) -> dict[str, Any]:
    audit_dir = geo.resolve_audit_dir(audit_id)
    from api.audit_runner import read_run_status

    status = read_run_status(audit_dir)
    if status is None:
        raise HTTPException(404, "No audit run in progress for this folder.")
    return status


@app.get("/api/audits/{audit_id}/score-breakdown")
def audit_score_breakdown(audit_id: str) -> dict[str, Any]:
    """Return individual pillar scores parsed from report.html."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return geo.load_integrated_scores(audit_dir)


class AiImpactEstimateBody(BaseModel):
    estimate: dict[str, Any]
    run_id: str | None = None


@app.get("/api/audits/{audit_id}/ai-impact-estimate")
def get_ai_impact_estimate(audit_id: str) -> dict[str, Any]:
    """Return the estimate persisted on this audit for dashboard restore and exports."""
    from api.ai_impact import load_ai_impact_estimate

    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    estimate = load_ai_impact_estimate(audit_dir)
    if estimate is None:
        raise HTTPException(404, "No saved AI impact estimate for this audit.")
    return estimate


@app.put("/api/audits/{audit_id}/ai-impact-estimate")
def save_ai_impact_estimate(audit_id: str, body: AiImpactEstimateBody) -> dict[str, Any]:
    """Persist the latest Estimated AI impact payload for PDF/HTML exports."""
    from api.ai_impact import persist_completed_estimate_for_audit

    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    try:
        model = persist_completed_estimate_for_audit(
            audit_dir,
            estimate=dict(body.estimate),
            run_id=body.run_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ok": True,
        "path": "ai_impact/latest.json",
        "run_id": model["run_id"],
    }


class ModelCategoryBody(BaseModel):
    """Set or classify the AI-impact site model category on an existing audit."""

    category: str | None = None  # advertiser-retail | advertiser-services | publisher
    classify: bool = False  # if true (and category omitted), run Gemini classifier


@app.post("/api/audits/{audit_id}/model-category")
def set_audit_model_category(audit_id: str, body: ModelCategoryBody) -> dict[str, Any]:
    """Backfill ``model_category`` on an existing audit so AI Impact can run.

    Pass an explicit ``category``, or set ``classify=true`` to ask Gemini.
    Does not re-run the full audit crawl.
    """
    from ai_impact.model_artifact import MODEL_CATEGORIES, validate_category
    from api.audit_runner import _persist_model_category_metadata
    from geo_setup_llm import classify_site_model_category

    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file() and not (
        audit_dir / "onboarding_context.json"
    ).is_file():
        raise HTTPException(404, "Audit not found")

    ob_path = audit_dir / "onboarding_context.json"
    onboarding: dict[str, Any] = {}
    if ob_path.is_file():
        try:
            raw = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                onboarding = raw
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"Could not read onboarding_context.json: {exc}") from exc

    explicit = (body.category or "").strip()
    if explicit:
        try:
            category = validate_category(explicit)
        except ValueError as exc:
            raise HTTPException(
                422,
                f"{exc}. Allowed: {', '.join(MODEL_CATEGORIES)}",
            ) from exc
        classification = {
            "category": category,
            "classifier": "manual",
            "provider": "operator",
            "model": "manual",
        }
    elif body.classify:
        website = str(
            onboarding.get("brand_website_used")
            or onboarding.get("brand_website")
            or ""
        ).strip()
        if not website:
            raise HTTPException(
                422,
                "No brand website on this audit — pass category explicitly "
                f"({', '.join(MODEL_CATEGORIES)}).",
            )
        products = onboarding.get("products_and_services") or []
        if not isinstance(products, list):
            products = []
        try:
            classification = classify_site_model_category(
                website,
                brand_name=str(onboarding.get("brand_name_used") or "").strip(),
                industry=str(onboarding.get("industry_used") or "").strip(),
                products_and_services=[str(p) for p in products if str(p).strip()],
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Gemini classification failed: {exc}") from exc
    else:
        raise HTTPException(
            422,
            "Provide category "
            f"({', '.join(MODEL_CATEGORIES)}) or set classify=true.",
        )

    try:
        result = _persist_model_category_metadata(audit_dir, classification)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"audit_dir": geo.audit_dir_api_rel(audit_dir), **result}


class TrackCompetitorBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    website: str = Field(default="", max_length=500)


@app.get("/api/audits/{audit_id}/competitors/comparison")
def competitor_comparison(audit_id: str) -> dict[str, Any]:
    """Return pillar scores for brand + competitors from comparison.json."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    try:
        return geo.load_competitive_comparison(audit_dir)
    except Exception as exc:
        raise HTTPException(500, f"Could not load competitor comparison: {exc}") from exc


@app.post("/api/audits/{audit_id}/competitors/track")
def track_audit_competitor(audit_id: str, body: TrackCompetitorBody) -> dict[str, Any]:
    """Add a detected visibility competitor to this audit's saved config."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    try:
        return geo.track_competitor_config(
            audit_dir,
            name=body.name,
            website=body.website,
        )
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"Could not read audit configuration: {exc}") from exc
    except ValueError as exc:
        status_code = 409 if "maximum" in str(exc).lower() else 400
        raise HTTPException(status_code, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, f"Could not save audit configuration: {exc}") from exc


@app.post("/api/audits/{audit_id}/competitors/crawl")
def crawl_audit_competitors(audit_id: str) -> dict[str, Any]:
    """Crawl configured competitor sites into this audit folder (no primary re-crawl)."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    from api.audit_runner import start_competitor_crawl

    try:
        return start_competitor_crawl(audit_dir)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/audits/{audit_id}/competitors/crawl-status")
def competitor_crawl_status(audit_id: str) -> dict[str, Any]:
    """Return the latest competitor-crawl job status for this audit."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    status = geo.reconcile_competitor_crawl_status(audit_dir)
    if status is None:
        has_comparison = (audit_dir / "comparison.json").is_file()
        return {
            "status": "idle",
            "audit_dir": geo.audit_dir_api_rel(audit_dir),
            "job_type": "competitor_crawl",
            "seen": True,
            "has_comparison": has_comparison,
            "archives": geo.list_competitor_crawl_archives(audit_dir),
        }
    return {
        **status,
        "has_comparison": (audit_dir / "comparison.json").is_file(),
        "archives": geo.list_competitor_crawl_archives(audit_dir),
    }


@app.post("/api/audits/{audit_id}/competitors/crawl-status/seen")
def competitor_crawl_mark_seen(audit_id: str) -> dict[str, Any]:
    """Mark the latest completed competitor crawl as viewed in the report UI."""
    audit_dir = geo.resolve_audit_dir(audit_id)
    return geo.mark_competitor_crawl_seen(audit_dir)

@app.get("/api/audits/{audit_id:path}")
def audit_detail(audit_id: str) -> dict[str, Any]:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    summary = geo.load_audit_summary(audit_dir)
    report_html = (audit_dir / "report.html").is_file()
    report_meta = geo.load_report_meta(audit_dir) if report_html else None
    onboarding: dict[str, Any] | None = None
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            import json as _json
            raw = _json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                onboarding = raw
        except Exception:
            pass
    return {
        "audit_dir": geo.audit_dir_api_rel(audit_dir),
        "summary": summary,
        "has_report_html": report_html,
        "report_meta": report_meta,
        "onboarding_context": onboarding,
    }


@app.get("/api/archive")
def archive_runs(
    request: Request,
    mine_only: bool = Query(True, description="When true, require sign-in and return only your runs"),
) -> dict[str, Any]:
    user = current_user(request)
    if mine_only:
        if user is None:
            return {
                "runs": [],
                "auth_required": True,
                "auth_enabled": auth_enabled(),
            }
        return {
            "runs": geo.runs_for_user(user["email"]),
            "auth_required": True,
            "auth_enabled": auth_enabled(),
            "user": user,
        }
    data = geo.load_archive()
    runs = [geo.enrich_archive_run(r) for r in data.get("runs", [])]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {
        "runs": runs,
        "auth_required": False,
        "auth_enabled": auth_enabled(),
        "user": user,
    }


@app.get("/api/archive/me")
def archive_me(user: dict[str, str] = Depends(require_user)) -> list[dict[str, Any]]:
    return geo.runs_for_user(user["email"])


@app.post("/api/audits/run")
def run_audit(body: RunAuditRequest, request: Request) -> StreamingResponse:
    from geo_setup_llm import normalize_competitor_url

    primary = normalize_competitor_url(body.brand_website.strip())
    if not primary:
        raise HTTPException(400, "Invalid brand website URL")

    competitors = [c.strip() for c in body.competitors if c.strip()][:10]
    user = current_user(request)
    owner_email = user["email"] if user else None

    def event_stream():
        from api.ga4 import resolve_ga4_for_audit_run

        ga4_cred_temp = None
        try:
            ga4_prop, ga4_ch, ga4_cred_temp = resolve_ga4_for_audit_run(
                request,
                ga4_property_id=body.ga4_property_id,
                ga4_ai_channels=body.ga4_ai_channels,
            )
            ga4_cred_path = str(ga4_cred_temp) if ga4_cred_temp is not None else None

            adir = geo.audit_dir_for_run(body.out_base, primary)
            notify = (body.notification_email or "").strip() or None
            geo.seed_audit_dir_from_wizard(
                adir,
                primary_url=primary,
                brand_name=body.brand_name.strip(),
                industry=body.industry.strip(),
                market_country=body.wizard_market_country.strip(),
                market_country_code=body.wizard_market_country_code.strip(),
                additional_markets=[m.model_dump() for m in body.wizard_additional_markets],
                competitor_urls=competitors,
                products_rows=[p.model_dump() for p in body.wizard_products],
                competitors_detail=[c.model_dump() for c in body.wizard_competitors],
                ga4_property_id=ga4_prop or "",
                ga4_ai_channel_names=ga4_ch or "",
                ga4_conversion_event_name=body.ga4_conversion_event_name,
                crawl_urls=body.crawl_urls or None,
                preserve_prompt_data=body.skip_prompt_probes,
                prompt_locales=list(body.wizard_prompt_locales or []),
                notification_email=notify,
            )
            rel = geo.audit_dir_api_rel(adir)
            yield f"data: {json.dumps({'type': 'started', 'audit_dir': rel})}\n\n"
            stream_progress = current_app_env() != "development"
            progress_state = None
            if stream_progress:
                from api.audit_progress import AuditProgressState, apply_log_line

                progress_state = AuditProgressState()
                yield f"data: {json.dumps({'type': 'progress', **progress_state.to_payload()})}\n\n"
            last_progress_json: str | None = None
            for line in geo.iter_pipeline_logs(
                primary,
                competitors,
                body.out_base,
                body.max_urls,
                body.delay,
                brand_name=body.brand_name,
                industry=body.industry,
                market_country=body.wizard_market_country.strip(),
                market_country_code=body.wizard_market_country_code.strip(),
                additional_markets=[m.model_dump() for m in body.wizard_additional_markets],
                ga4_property_id=ga4_prop,
                ga4_ai_channels=ga4_ch,
                ga4_oauth_credentials_path=ga4_cred_path,
                crawl_urls=body.crawl_urls or None,
            ):
                if stream_progress and progress_state is not None:
                    progress_state = apply_log_line(progress_state, line)
                    progress_payload = {"type": "progress", **progress_state.to_payload()}
                    progress_json = json.dumps(progress_payload)
                    if progress_json != last_progress_json:
                        last_progress_json = progress_json
                        yield f"data: {progress_json}\n\n"
                elif not stream_progress:
                    yield f"data: {json.dumps({'type': 'log', 'line': line})}\n\n"
            adir = geo.audit_dir_for_run(body.out_base, primary)
            prompt_job_queued = False
            if not body.skip_prompt_probes:
                from api.prompt_jobs import enqueue_prompt_job

                queued = enqueue_prompt_job(
                    adir,
                    mode="post_audit",
                    report_mode=True,
                    completion={
                        "primary": primary,
                        "competitors": competitors,
                        "owner_email": owner_email,
                        "notification_email": notify,
                        "brand_name": body.brand_name,
                    },
                )
                prompt_job_queued = True
                if stream_progress and progress_state is not None:
                    from api.audit_progress import advance_to_step

                    progress_state = advance_to_step(
                        progress_state,
                        "prompt_probes",
                        "AI prompt probe job queued…",
                    )
                    yield f"data: {json.dumps({'type': 'progress', **progress_state.to_payload()})}\n\n"
            elif stream_progress and progress_state is not None:
                from api.audit_progress import complete_all_steps

                progress_state = complete_all_steps(
                    progress_state,
                    detail="Audit complete; prompt results preserved",
                )
                yield f"data: {json.dumps({'type': 'progress', **progress_state.to_payload()})}\n\n"

            summary = geo.load_audit_summary(adir)
            overall = float(summary.get("overall_score") or 0)
            if overall <= 0:
                resolved = geo.resolve_overall_score_for_audit(adir)
                if resolved is not None:
                    overall = resolved
            if not prompt_job_queued:
                from api.audit_runner import finalize_audit_run

                finalize_audit_run(
                    audit_dir=adir,
                    primary=primary,
                    competitors=competitors,
                    owner_email=owner_email,
                    notification_email=notify,
                    brand_name=body.brand_name or "",
                    progress_state=progress_state,
                )
            if competitors:
                from api.audit_runner import _queue_follow_on_competitor_crawl

                _queue_follow_on_competitor_crawl(adir, competitors)
            payload = {
                "type": "done",
                "audit_dir": geo.audit_dir_api_rel(adir),
                "overall_score": overall if overall > 0 else summary.get("overall_score"),
                "prompt_job_queued": prompt_job_queued,
            }
            yield f"data: {json.dumps(payload)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            if ga4_cred_temp is not None:
                try:
                    ga4_cred_temp.unlink(missing_ok=True)
                except OSError:
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/audits/run-background")
def run_audit_background(body: RunAuditRequest, request: Request) -> dict[str, Any]:
    """Start audit pipeline in a background thread; poll ``GET …/run-status`` for progress."""
    from api.audit_runner import start_background_audit

    user = current_user(request)
    owner_email = user["email"] if user else None
    try:
        return start_background_audit(request, body, owner_email=owner_email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


_CONNECTOR_LOGO_DIR = geo.REPO_ROOT / "assets" / "logos"


@app.get("/assets/logos/{filename}")
def connector_logo(filename: str) -> FileResponse:
    candidate = (_CONNECTOR_LOGO_DIR / filename).resolve()
    try:
        candidate.relative_to(_CONNECTOR_LOGO_DIR.resolve())
    except ValueError:
        raise HTTPException(404) from None
    if not candidate.is_file():
        raise HTTPException(404)
    return FileResponse(candidate)


_STATIC_DIR = geo.REPO_ROOT / "web" / "dist"


def _mount_spa() -> None:
    if not _STATIC_DIR.is_dir():
        return

    @app.get("/")
    def spa_index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/{full_path:path}")
    def spa_files(full_path: str) -> Response:
        if full_path.startswith("api/") or full_path.startswith("api"):
            raise HTTPException(404)
        candidate = (_STATIC_DIR / full_path).resolve()
        try:
            candidate.relative_to(_STATIC_DIR.resolve())
        except ValueError:
            raise HTTPException(404) from None
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_STATIC_DIR / "index.html")


_mount_spa()
