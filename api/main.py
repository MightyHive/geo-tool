"""FastAPI backend for the GEO audit web UI."""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
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
from api.recommendations import router as recommendations_router
from api.wizard import router as wizard_router
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
app.include_router(wizard_router)
app.include_router(prompt_performance_router)
app.include_router(executive_summary_router)
app.include_router(recommendations_router)


class WizardProductRow(BaseModel):
    product_or_service: str = ""
    prompts: list[str] = Field(default_factory=list)


class WizardCompetitorRow(BaseModel):
    competitor_brand: str = ""
    competitor_website: str = ""
    included: bool = True


class RunAuditRequest(BaseModel):
    brand_name: str
    brand_website: str
    industry: str = ""
    competitors: list[str] = Field(default_factory=list)
    max_urls: int = 40
    delay: float = 0.2
    out_base: str = "audit_output"
    ga4_property_id: str | None = None
    ga4_ai_channels: str | None = None
    wizard_market_country: str = ""
    wizard_market_country_code: str = ""
    wizard_products: list[WizardProductRow] = Field(default_factory=list)
    wizard_competitors: list[WizardCompetitorRow] = Field(default_factory=list)


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
def audits_local() -> list[dict[str, Any]]:
    return geo.list_primary_audits()


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
    html = geo.prepare_report_html_for_embed(html)
    return HTMLResponse(html, media_type="text/html")


@app.get("/api/audits/{audit_id}/report.pdf")
def audit_report_pdf(audit_id: str) -> Response:
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
    from api.pdf_service import generate_report_pdf, pdf_filename_for_audit

    try:
        pdf_bytes = generate_report_pdf(report_path)
    except Exception as exc:
        raise HTTPException(500, f"PDF generation failed: {exc}") from exc
    filename = pdf_filename_for_audit(audit_dir)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/audits/{audit_id}/report-all-pages.html", response_model=None)
def audit_report_all_pages_html(audit_id: str) -> Response:
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

    from api.html_service import generate_all_pages_html

    try:
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


@app.get("/api/audits/{audit_id}/run-status")
def audit_run_status(audit_id: str) -> dict[str, Any]:
    audit_dir = geo.resolve_audit_dir(audit_id)
    from api.audit_runner import read_run_status

    status = read_run_status(audit_dir)
    if status is None:
        raise HTTPException(404, "No audit run in progress for this folder.")
    return status


@app.get("/api/audits/{audit_id:path}")
def audit_detail(audit_id: str) -> dict[str, Any]:
    audit_dir = geo.resolve_audit_dir(audit_id)
    if not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    summary = geo.load_audit_summary(audit_dir)
    report_html = (audit_dir / "report.html").is_file()
    report_meta = geo.load_report_meta(audit_dir) if report_html else None
    return {
        "audit_dir": geo.audit_dir_api_rel(audit_dir),
        "summary": summary,
        "has_report_html": report_html,
        "report_meta": report_meta,
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

    competitors = [c.strip() for c in body.competitors if c.strip()][:12]
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
            geo.seed_audit_dir_from_wizard(
                adir,
                primary_url=primary,
                brand_name=body.brand_name.strip(),
                industry=body.industry.strip(),
                market_country=body.wizard_market_country.strip(),
                market_country_code=body.wizard_market_country_code.strip(),
                competitor_urls=competitors,
                products_rows=[p.model_dump() for p in body.wizard_products],
                competitors_detail=[c.model_dump() for c in body.wizard_competitors],
                ga4_property_id=ga4_prop or "",
                ga4_ai_channel_names=ga4_ch or "",
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
                ga4_property_id=ga4_prop,
                ga4_ai_channels=ga4_ch,
                ga4_oauth_credentials_path=ga4_cred_path,
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
            if stream_progress and progress_state is not None:
                from api.audit_progress import advance_to_step, complete_all_steps
                from api.prompt_performance import run_post_audit_prompt_insights

                progress_state = advance_to_step(
                    progress_state,
                    "prompt_probes",
                    "Running AI prompt probes for share of voice…",
                )
                yield f"data: {json.dumps({'type': 'progress', **progress_state.to_payload()})}\n\n"

                def _on_post_audit_step(step_id: str, detail: str) -> None:
                    nonlocal progress_state
                    progress_state = advance_to_step(progress_state, step_id, detail)

                run_post_audit_prompt_insights(
                    adir, report_mode=True, on_step=_on_post_audit_step
                )
                yield f"data: {json.dumps({'type': 'progress', **progress_state.to_payload()})}\n\n"

                progress_state = complete_all_steps(progress_state, detail="Audit complete")
                yield f"data: {json.dumps({'type': 'progress', **progress_state.to_payload()})}\n\n"
            else:
                from api.prompt_performance import run_post_audit_prompt_insights

                run_post_audit_prompt_insights(adir, report_mode=True)

            summary = geo.load_audit_summary(adir)
            overall = float(summary.get("overall_score") or 0)
            if overall <= 0:
                resolved = geo.resolve_overall_score_for_audit(adir)
                if resolved is not None:
                    overall = resolved
            geo.archive_add_run(
                primary_url=primary,
                audit_dir=adir,
                overall=overall,
                competitors=competitors,
                owner_email=owner_email,
                brand_name=body.brand_name or None,
            )
            payload = {
                "type": "done",
                "audit_dir": geo.audit_dir_api_rel(adir),
                "overall_score": overall if overall > 0 else summary.get("overall_score"),
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
