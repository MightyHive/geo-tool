"""AI Traffic Impact API — create runs, poll status, return estimates."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-impact", tags=["ai-impact"])


def _runs_root() -> Path:
    from api.geo_services import audit_output_base

    root = audit_output_base().parent / "ai_impact_runs"
    geo_root = (os.environ.get("GEO_DATA_ROOT") or "").strip()
    if geo_root:
        root = Path(geo_root) / "ai_impact_runs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _trends_uploads_root() -> Path:
    root = _runs_root().parent / "ai_impact_trends_uploads"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_backend_path() -> Path:
    backend = Path(__file__).resolve().parents[1] / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    return backend


class CreateAiImpactRunRequest(BaseModel):
    ga4_property_id: str | None = None
    ga4_property_name: str | None = None
    gsc_site_url: str | None = None
    conversion_event_name: str = Field(default="purchase", min_length=1, max_length=500)
    trends_upload_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    start_date: str = "2023-01-01"
    end_date: str | None = None
    window_weeks: int = Field(default=13, ge=4, le=52)
    # Phase 0 escape hatch: estimate from an existing panel CSV on disk
    local_panel_path: str | None = None

    @field_validator("conversion_event_name")
    @classmethod
    def _validate_conversion_event_name(cls, value: str) -> str:
        from api.conversion_events import (
            ConversionEventParseError,
            normalize_conversion_event_spec,
        )

        try:
            return normalize_conversion_event_spec(value)
        except ConversionEventParseError as exc:
            raise ValueError(str(exc)) from exc


class AiImpactRunStatus(BaseModel):
    run_id: str
    status: str
    created_at: str
    ga4_property_id: str | None = None
    gsc_site_url: str | None = None
    conversion_event_name: str = "purchase"
    conversion_events: list[dict[str, str]] = Field(default_factory=list)
    jobs: dict[str, str] = Field(default_factory=dict)
    estimate: dict[str, Any] | None = None
    error: str | None = None
    trends_upload: dict[str, Any] | None = None
    needs_ga4_reauth: bool = False
    ga4_login_path: str = "/api/ga4/login"


class TrendsUploadStatus(BaseModel):
    upload_id: str
    filename: str
    terms: list[str]
    start_date: str
    end_date: str
    week_count: int
    warnings: list[str] = Field(default_factory=list)


def _resolve_trends_upload(upload_id: str | None) -> tuple[Path | None, dict[str, Any] | None]:
    if not upload_id:
        return None, None
    root = _trends_uploads_root()
    csv_path = root / f"{upload_id}.csv"
    meta_path = root / f"{upload_id}.json"
    if not csv_path.is_file() or not meta_path.is_file():
        raise HTTPException(
            status_code=400,
            detail="The Google Trends upload is no longer available. Upload the CSV again.",
        )
    return csv_path, json.loads(meta_path.read_text(encoding="utf-8"))


def _conversion_events_from_meta(meta: dict[str, Any]) -> list[dict[str, str]]:
    from api.conversion_events import conversion_events_as_dicts, parse_conversion_events

    stored = meta.get("conversion_events")
    if isinstance(stored, list) and stored:
        out: list[dict[str, str]] = []
        for row in stored:
            if not isinstance(row, dict):
                continue
            event = str(row.get("event") or "").strip()
            if not event:
                continue
            label = str(row.get("label") or event).strip() or event
            out.append({"event": event, "label": label})
        if out:
            return out
    raw = str(meta.get("conversion_event_name") or "purchase")
    try:
        return conversion_events_as_dicts(parse_conversion_events(raw))
    except Exception:  # noqa: BLE001
        return [{"event": "purchase", "label": "purchase"}]


@router.post("/trends-upload", response_model=TrendsUploadStatus)
async def upload_trends_csv(
    file: UploadFile = File(...),
    start_date: str = Query(default="2023-01-01"),
    end_date: str | None = Query(default=None),
) -> TrendsUploadStatus:
    filename = (file.filename or "google-trends.csv").strip()
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status_code=422, detail=["Upload the CSV file downloaded from Google Trends."])
    content = await file.read(2 * 1024 * 1024 + 1)
    _ensure_backend_path()
    from ai_impact.trends import TrendsUploadError, default_expected_end, parse_google_trends_upload

    try:
        parsed = parse_google_trends_upload(
            content,
            expected_start=start_date,
            expected_end=end_date or default_expected_end(),
        )
    except TrendsUploadError as exc:
        raise HTTPException(status_code=422, detail=exc.messages) from exc

    upload_id = uuid.uuid4().hex
    root = _trends_uploads_root()
    csv_path = root / f"{upload_id}.csv"
    meta_path = root / f"{upload_id}.json"
    parsed.weekly.to_csv(csv_path, index=False)
    metadata = {
        "upload_id": upload_id,
        "filename": filename,
        "terms": parsed.terms,
        "start_date": parsed.start_date,
        "end_date": parsed.end_date,
        "week_count": parsed.week_count,
        "warnings": parsed.warnings,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return TrendsUploadStatus(**metadata)


def _write_session_adc(run_dir: Path, creds_dict: dict[str, Any]) -> Path | None:
    try:
        _ensure_backend_path()
        import ga4_oauth as g4o

        creds = g4o.credentials_from_dict(creds_dict)
        creds = g4o.ensure_fresh_credentials(creds)
        path = run_dir / "ga4_adc.json"
        g4o.save_adc_credentials(creds, path)
        return path
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not write GA4 ADC for AI-impact run: %s", exc)
        return None


def _run_ga4_channel_export(
    *,
    property_id: str,
    property_name: str,
    start_date: str,
    end_date: str,
    run_dir: Path,
    adc_path: Path | None,
    conversion_event_name: str = "purchase",
) -> Path:
    """Pull weekly channels via research ``ga4_channel_export.run_export``."""
    repo = Path(__file__).resolve().parents[1]
    research_ga4 = repo / "research" / "ga4"
    research = repo / "research"
    for p in (research_ga4, research, repo / "backend"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))

    previous_adc = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if adc_path is not None:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(adc_path)

    from ga4_channel_export import run_export

    try:
        _long, _wide, weekly_path = run_export(
            property_id,
            property_name=property_name,
            start_date=start_date,
            end_date=end_date,
            output_dir=run_dir,
            token_path=adc_path,
            force_login=False,
            conversion_event_name=conversion_event_name,
        )
    finally:
        # ``run_export`` installs the user's GA4-only ADC process-wide. Restore
        # Cloud Run's service-account ADC before invoking other Google APIs.
        if previous_adc is None:
            os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
        else:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = previous_adc
    canonical = run_dir / "ga4_weekly.csv"
    shutil.copy2(weekly_path, canonical)
    return canonical


@router.post("/runs", response_model=AiImpactRunStatus)
def create_run(body: CreateAiImpactRunRequest, request: Request) -> AiImpactRunStatus:
    """
    Create an AI-impact run.

    - ``local_panel_path``: estimate immediately from an on-disk panel CSV.
    - Otherwise: require wizard GA4 OAuth when a property is set; pull via
      ``ga4_channel_export`` (same auth as setup wizard).
    """
    if body.local_panel_path:
        from api.conversion_events import is_only_default_purchase, parse_conversion_events

        events = parse_conversion_events(body.conversion_event_name)
        if not is_only_default_purchase(events):
            raise HTTPException(
                status_code=400,
                detail="Custom conversion events require a live GA4 export; local panels contain unverified purchase columns.",
            )

    from api.conversion_events import conversion_events_as_dicts, parse_conversion_events

    conversion_events = conversion_events_as_dicts(
        parse_conversion_events(body.conversion_event_name)
    )

    run_id = uuid.uuid4().hex[:16]
    created = datetime.now(timezone.utc).isoformat()
    run_dir = _runs_root() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    end_date = body.end_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    uploaded_trends_path, trends_metadata = _resolve_trends_upload(body.trends_upload_id)
    trends_path: Path | None = None
    if uploaded_trends_path is not None:
        trends_path = run_dir / "trends_weekly.csv"
        shutil.copy2(uploaded_trends_path, trends_path)
    meta: dict[str, Any] = {
        "run_id": run_id,
        "status": "pending",
        "created_at": created,
        "ga4_property_id": body.ga4_property_id,
        "ga4_property_name": body.ga4_property_name,
        "gsc_site_url": body.gsc_site_url,
        "conversion_event_name": body.conversion_event_name,
        "conversion_events": conversion_events,
        "trends_upload": trends_metadata,
        "start_date": body.start_date,
        "end_date": end_date,
        "window_weeks": body.window_weeks,
        "jobs": {
            "ga4": "not_started",
            "gsc": "not_started",
            "trends": "uploaded" if trends_path is not None else "skipped",
            "estimate": "not_started",
        },
        "needs_ga4_reauth": False,
    }

    ga4_creds = request.session.get("ga4_user_creds_dict")
    gsc_creds = request.session.get("gsc_user_creds_dict")
    ga4_connected = bool(isinstance(ga4_creds, dict) and ga4_creds.get("refresh_token"))
    meta["ga4_connected"] = ga4_connected
    meta["gsc_connected"] = bool(isinstance(gsc_creds, dict) and gsc_creds.get("refresh_token"))

    # Prefer request body, then wizard session selection
    property_id = (body.ga4_property_id or "").strip() or str(
        request.session.get("ga4_selected_property_id") or ""
    ).strip()
    property_name = (body.ga4_property_name or property_id or "property").strip()
    if property_id:
        meta["ga4_property_id"] = property_id
    # The request is authoritative so sending null explicitly omits Search
    # Console even when this browser session has a previously selected site.
    gsc_site_url = (body.gsc_site_url or "").strip()
    if gsc_site_url:
        meta["gsc_site_url"] = gsc_site_url

    estimate: dict[str, Any] | None = None
    error: str | None = None
    needs_ga4_reauth = False

    if body.local_panel_path:
        try:
            _ensure_backend_path()
            from ai_impact.estimate import estimate_ai_impact
            from ai_impact.panel import build_weekly_panel

            panel_path = Path(body.local_panel_path)
            if not panel_path.is_file():
                raise FileNotFoundError(str(panel_path))
            panel = build_weekly_panel(panel_path, trends=trends_path)
            result = estimate_ai_impact(panel, window_weeks=body.window_weeks)
            estimate = result.as_dict()
            (run_dir / "estimate.json").write_text(
                json.dumps(estimate, indent=2), encoding="utf-8"
            )
            meta["status"] = "completed"
            meta["jobs"] = {k: "skipped_local_panel" for k in meta["jobs"]}
            meta["jobs"]["estimate"] = "completed"
        except Exception as exc:  # noqa: BLE001
            log.exception("AI impact local estimate failed")
            meta["status"] = "failed"
            error = str(exc)
            meta["error"] = error
    else:
        adc_path: Path | None = None
        if property_id:
            if not ga4_connected:
                needs_ga4_reauth = True
                meta["needs_ga4_reauth"] = True
                meta["status"] = "needs_ga4_reauth"
                meta["jobs"]["ga4"] = "blocked_reauth"
                meta["error"] = (
                    "Google Analytics is not connected or the session expired. "
                    "Reconnect with the same wizard OAuth flow."
                )
                error = meta["error"]
            else:
                assert isinstance(ga4_creds, dict)
                adc_path = _write_session_adc(run_dir, ga4_creds)
                if adc_path is None:
                    needs_ga4_reauth = True
                    meta["needs_ga4_reauth"] = True
                    meta["status"] = "needs_ga4_reauth"
                    meta["jobs"]["ga4"] = "blocked_reauth"
                    error = "Could not refresh GA4 credentials. Reconnect Google Analytics."
                    meta["error"] = error
                else:
                    ga4_job_env = {
                        "RUN_ID": run_id,
                        "GCS_OUTPUT_URI": str(run_dir) + "/",
                        "GA4_PROPERTY_ID": property_id,
                        "GA4_PROPERTY_NAME": property_name,
                        "START_DATE": body.start_date,
                        "END_DATE": end_date,
                        "GOOGLE_APPLICATION_CREDENTIALS": str(adc_path),
                        "GA4_OAUTH_TOKEN_PATH": str(adc_path),
                        "GA4_CONVERSION_EVENT_NAME": body.conversion_event_name,
                    }
                    (run_dir / "ga4_job.json").write_text(
                        json.dumps(ga4_job_env, indent=2), encoding="utf-8"
                    )
                    try:
                        weekly = _run_ga4_channel_export(
                            property_id=property_id,
                            property_name=property_name,
                            start_date=body.start_date,
                            end_date=end_date,
                            run_dir=run_dir,
                            adc_path=adc_path,
                            conversion_event_name=body.conversion_event_name,
                        )
                        meta["jobs"]["ga4"] = "completed"
                        _ensure_backend_path()
                        from ai_impact.estimate import estimate_ai_impact
                        from ai_impact.panel import build_weekly_panel

                        gsc_path: Path | None = None
                        if gsc_site_url:
                            try:
                                if not meta["gsc_connected"]:
                                    raise RuntimeError(
                                        "Search Console is not connected. Reconnect it and retry."
                                    )
                                from api.gsc import export_search_analytics_daily

                                gsc_path = export_search_analytics_daily(
                                    request,
                                    site_url=gsc_site_url,
                                    start_date=body.start_date,
                                    end_date=end_date,
                                    output_path=run_dir / "gsc_daily.csv",
                                )
                                meta["jobs"]["gsc"] = "completed"
                            except Exception as gsc_exc:  # noqa: BLE001
                                log.exception("Search Console export failed")
                                meta["jobs"]["gsc"] = "failed"
                                meta["gsc_error"] = str(gsc_exc)
                        else:
                            meta["jobs"]["gsc"] = "skipped"

                        panel = build_weekly_panel(weekly, trends=trends_path, gsc=gsc_path)
                        panel.to_csv(run_dir / "panel.csv", index=False)
                        result = estimate_ai_impact(panel, window_weeks=body.window_weeks)
                        estimate = result.as_dict()
                        (run_dir / "estimate.json").write_text(
                            json.dumps(estimate, indent=2), encoding="utf-8"
                        )
                        meta["jobs"]["estimate"] = "completed"
                        meta["status"] = "completed"
                        meta["enqueue_note"] = (
                            "GA4 weekly channels pulled via ga4_channel_export."
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.exception("AI impact GA4 export / estimate failed")
                        err_s = str(exc).lower()
                        reauth = any(
                            x in err_s
                            for x in (
                                "invalid_grant",
                                "refresh",
                                "unauthorized",
                                "credentials",
                                "oauth",
                                "token",
                                "login",
                            )
                        )
                        needs_ga4_reauth = reauth or not ga4_connected
                        meta["needs_ga4_reauth"] = needs_ga4_reauth
                        meta["status"] = "needs_ga4_reauth" if needs_ga4_reauth else "failed"
                        meta["jobs"]["ga4"] = "failed"
                        error = str(exc)
                        meta["error"] = error
        else:
            meta["status"] = "awaiting_extracts"
            meta["jobs"] = {
                "ga4": "skipped_no_property",
                "gsc": "blocked" if gsc_site_url else "skipped",
                "estimate": "blocked",
            }
            meta["enqueue_note"] = (
                "Select a GA4 property (wizard OAuth) or pass local_panel_path."
            )

    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return AiImpactRunStatus(
        run_id=run_id,
        status=meta["status"],
        created_at=created,
        ga4_property_id=meta.get("ga4_property_id"),
        gsc_site_url=gsc_site_url or None,
        conversion_event_name=body.conversion_event_name,
        conversion_events=conversion_events,
        jobs=meta["jobs"],
        estimate=estimate,
        error=error,
        trends_upload=trends_metadata,
        needs_ga4_reauth=needs_ga4_reauth,
    )


@router.get("/runs/{run_id}", response_model=AiImpactRunStatus)
def get_run(run_id: str) -> AiImpactRunStatus:
    run_dir = _runs_root() / run_id
    meta_path = run_dir / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(status_code=404, detail="Run not found")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    estimate = None
    est_path = run_dir / "estimate.json"
    if est_path.is_file():
        estimate = json.loads(est_path.read_text(encoding="utf-8"))
    return AiImpactRunStatus(
        run_id=run_id,
        status=meta.get("status", "unknown"),
        created_at=meta.get("created_at", ""),
        ga4_property_id=meta.get("ga4_property_id"),
        gsc_site_url=meta.get("gsc_site_url"),
        conversion_event_name=str(meta.get("conversion_event_name") or "purchase"),
        conversion_events=_conversion_events_from_meta(meta),
        jobs=meta.get("jobs") or {},
        estimate=estimate,
        error=meta.get("error"),
        trends_upload=meta.get("trends_upload"),
        needs_ga4_reauth=bool(meta.get("needs_ga4_reauth")),
    )


@router.get("/config")
def ai_impact_config() -> dict[str, Any]:
    """Frontend bootstrap: required scopes, limits, method notes."""
    return {
        "default_window_weeks": 13,
        "trends_manual_upload": True,
        "trends_expected_start": "2023-01-01",
        "trends_expected_end": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "trends_max_terms": 5,
        "trends_url": "https://trends.google.com/trends/explore",
        "ga4_oauth_path": "/api/ga4/login",
        "gsc_oauth_path": "/api/gsc/login",
        "gsc_scope": "https://www.googleapis.com/auth/webmasters.readonly",
        "impression_break": "2025-09-01",
        "holiday_mask": "black_friday_to_twelfth_night",
        "ga4_export": "research/ga4/ga4_channel_export.py",
        "quality_narratives": [
            "sessions_down_quality_up",
            "sessions_and_quality_down",
            "sessions_and_quality_up",
            "mixed_or_aligned",
        ],
    }