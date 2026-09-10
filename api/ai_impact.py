"""AI Traffic Impact API — create runs, poll status, return estimates."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai-impact", tags=["ai-impact"])


def load_ai_impact_estimate(audit_dir: Path) -> dict[str, Any] | None:
    """Load the latest completed audit-local model for UI restore and exports."""
    return load_latest_completed_model_estimate(audit_dir)


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


def _category_from_audit(audit_id: str | None) -> tuple[str | None, dict[str, Any] | None]:
    if not audit_id:
        return None, None
    from api import geo_services as geo

    audit_dir = geo.resolve_audit_dir(audit_id)
    path = audit_dir / "onboarding_context.json"
    if not path.is_file():
        raise HTTPException(
            status_code=422,
            detail="This audit has no persisted site model category.",
        )
    try:
        onboarding = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="This audit's category metadata is unreadable.",
        ) from exc
    _ensure_backend_path()
    from ai_impact.model_artifact import validate_category

    try:
        category = validate_category(onboarding.get("model_category"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provenance = onboarding.get("model_category_provenance")
    return category, provenance if isinstance(provenance, dict) else None


def _brand_name_from_audit(audit_id: str | None) -> str | None:
    """Wizard brand name from onboarding_context.json (``brand_name_used``)."""
    if not audit_id:
        return None
    from api import geo_services as geo

    audit_dir = geo.resolve_audit_dir(audit_id)
    path = audit_dir / "onboarding_context.json"
    if not path.is_file():
        return None
    try:
        onboarding = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    brand = str(onboarding.get("brand_name_used") or "").strip()
    return brand or None


def _last_completed_saturday() -> str:
    """ISO date of the most recent Saturday (Europe/London); weeks end Saturday."""
    from datetime import timedelta

    try:
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("Europe/London")).date()
    except Exception:
        today = datetime.now(timezone.utc).date()
    days_since_sat = (today.weekday() - 5) % 7
    return (today - timedelta(days=days_since_sat)).isoformat()


def _google_trends_job_configured() -> bool:
    return bool((os.getenv("GOOGLE_TRENDS_JOB_NAME") or "").strip())


def _execute_google_trends_weekly_job(
    *,
    query_term: str,
    run_id: str,
    start_date: str = "2023-06-01",
    end_date: str | None = None,
    gcs_output_prefix: str | None = None,
) -> str:
    """Enqueue the weekly Trends Cloud Run Job and wait until it finishes.

    Returns the GCS object path for ``reference_weekly.csv``.
    """
    from google.cloud import run_v2

    name = (os.getenv("GOOGLE_TRENDS_JOB_NAME") or "").strip()
    if not name:
        raise RuntimeError("GOOGLE_TRENDS_JOB_NAME is not configured")
    project = (
        os.getenv("GOOGLE_TRENDS_JOB_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    ).strip()
    region = (
        os.getenv("GOOGLE_TRENDS_JOB_REGION")
        or os.getenv("CLOUD_RUN_REGION")
        or "europe-west1"
    ).strip()
    bucket = (
        os.getenv("GOOGLE_TRENDS_GCS_BUCKET")
        or os.getenv("GCS_BUCKET")
        or ""
    ).strip()
    if bucket.startswith("gs://"):
        bucket = bucket[5:]
    if not bucket:
        raise RuntimeError("GOOGLE_TRENDS_GCS_BUCKET (or GCS_BUCKET) is not configured")
    if not project:
        raise RuntimeError("GOOGLE_TRENDS_JOB_PROJECT is not configured")

    end = (end_date or "").strip() or _last_completed_saturday()
    prefix = (gcs_output_prefix or f"runs/{run_id}").strip().strip("/")
    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    env = {
        "QUERY_TERM": query_term,
        "START_DATE": start_date,
        "END_DATE": end,
        "TRENDS_WEEKLY_ONLY": "1",
        "TRENDS_RUN_MODE": "weekly",
        "TRENDS_GEO": (os.getenv("GOOGLE_TRENDS_GEO") or "GB").strip(),
        "TRENDS_HL": (os.getenv("GOOGLE_TRENDS_HL") or "en").strip(),
        "RUN_ID": run_id,
        "GCS_OUTPUT_BUCKET": bucket,
        "GCS_OUTPUT_PREFIX": prefix,
    }
    log.info(
        "Executing Google Trends weekly job %s term=%r window=%s…%s "
        "run_id=%s gcs=gs://%s/%s/",
        job_path,
        query_term,
        start_date,
        end,
        run_id,
        bucket,
        prefix,
    )
    client = run_v2.JobsClient()
    operation = client.run_job(
        request=run_v2.RunJobRequest(
            name=job_path,
            overrides={
                "container_overrides": [
                    {
                        "env": [
                            {"name": key, "value": value}
                            for key, value in env.items()
                        ]
                    }
                ],
                "task_count": 1,
            },
        )
    )
    timeout_raw = (os.getenv("GOOGLE_TRENDS_JOB_TIMEOUT_SEC") or "1200").strip()
    try:
        timeout_sec = max(60, int(timeout_raw))
    except ValueError:
        timeout_sec = 1200
    operation.result(timeout=timeout_sec)
    return f"{prefix}/reference_weekly.csv"


def _download_gcs_object(bucket_name: str, object_path: str) -> bytes:
    from google.cloud import storage

    bucket = bucket_name.strip()
    if bucket.startswith("gs://"):
        bucket = bucket[5:]
    client = storage.Client()
    blob = client.bucket(bucket).blob(object_path)
    if not blob.exists():
        raise FileNotFoundError(f"gs://{bucket}/{object_path} not found after Trends job")
    return blob.download_as_bytes()


def _audit_google_trends_dir(audit_id: str | None) -> Path | None:
    """``audit_output/<audit_id>/google_trends`` on the geo-data mount / local root."""
    if not audit_id:
        return None
    from api import geo_services as geo

    return geo.resolve_audit_dir(audit_id) / "google_trends"


def _fetch_brand_trends_via_job(
    *,
    brand: str,
    run_id: str,
    run_dir: Path,
    start_date: str = "2023-06-01",
    audit_id: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Run the weekly scraper job, normalize CSV, write under the audit + run dir."""
    _ensure_backend_path()
    from ai_impact.trends import TrendsUploadError, parse_google_trends_upload

    trends_dir = _audit_google_trends_dir(audit_id)
    # Prefer audit-attached path so outputs show up next to other audit artifacts.
    # ``audit_id`` may already be ``audit_output/<slug>`` (API rel) or just ``<slug>``.
    if audit_id:
        slug = str(audit_id).strip().strip("/")
        if slug.startswith("audit_output/"):
            gcs_prefix = f"{slug}/google_trends/runs/{run_id}"
        else:
            gcs_prefix = f"audit_output/{slug}/google_trends/runs/{run_id}"
    else:
        gcs_prefix = f"runs/{run_id}"
    object_path = _execute_google_trends_weekly_job(
        query_term=brand,
        run_id=run_id,
        start_date=start_date,
        gcs_output_prefix=gcs_prefix,
    )
    bucket = (
        os.getenv("GOOGLE_TRENDS_GCS_BUCKET")
        or os.getenv("GCS_BUCKET")
        or ""
    ).strip()
    try:
        raw = _download_gcs_object(bucket, object_path)
    except FileNotFoundError:
        # Older job images ignore GCS_OUTPUT_PREFIX and always write runs/<run_id>/.
        fallback = f"runs/{run_id}/reference_weekly.csv"
        log.warning(
            "Trends artifact not at %s; trying legacy path %s",
            object_path,
            fallback,
        )
        raw = _download_gcs_object(bucket, fallback)
        object_path = fallback
    end = _last_completed_saturday()
    try:
        parsed = parse_google_trends_upload(
            raw,
            expected_start=start_date,
            expected_end=end,
        )
    except TrendsUploadError as exc:
        raise RuntimeError("; ".join(exc.messages)) from exc

    metadata = {
        "source": "cloud_run_job",
        "filename": "reference_weekly.csv",
        "terms": parsed.terms,
        "start_date": parsed.start_date,
        "end_date": parsed.end_date,
        "week_count": parsed.week_count,
        "warnings": parsed.warnings,
        "query_term": brand,
        "gcs_object": object_path,
        "gcs_prefix": gcs_prefix,
        "audit_id": audit_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # AI Impact run working copy (panel builder reads this).
    trends_path = run_dir / "trends_weekly.csv"
    parsed.weekly.to_csv(trends_path, index=False)
    (run_dir / "reference_weekly.csv").write_bytes(raw)
    (run_dir / "trends_meta.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )

    # Persist on the audit under google_trends/ (same bucket via FUSE / local path).
    if trends_dir is not None:
        trends_dir.mkdir(parents=True, exist_ok=True)
        (trends_dir / "reference_weekly.csv").write_bytes(raw)
        parsed.weekly.to_csv(trends_dir / "trends_weekly.csv", index=False)
        (trends_dir / "meta.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        metadata["audit_path"] = str(trends_dir)

    return trends_path, metadata


def _brand_trends_column(
    trends_metadata: dict[str, Any] | None,
    requested_term: str | None,
) -> str:
    terms = list((trends_metadata or {}).get("terms") or ())
    if not terms:
        raise HTTPException(
            status_code=422,
            detail="A Google Trends brand term is required for hierarchical scoring.",
        )
    if requested_term:
        try:
            index = terms.index(requested_term)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Selected brand term {requested_term!r} is not in the upload.",
            ) from exc
        return f"trends_{index + 1}"
    if len(terms) == 1:
        return "trends_1"
    raise HTTPException(
        status_code=422,
        detail="Multiple Google Trends terms were uploaded; select the brand term.",
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


COMPLETED_MODEL_SCHEMA = "ai-impact-completed-model-v1"


def _read_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _completed_models_root(audit_dir: Path) -> Path:
    return audit_dir / "ai_impact" / "models"


def _completed_model_payload(
    meta: dict[str, Any],
    estimate: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": COMPLETED_MODEL_SCHEMA,
        "run_id": str(meta.get("run_id") or "legacy"),
        "audit_id": str(meta.get("audit_id") or ""),
        "status": "completed",
        "created_at": str(meta.get("created_at") or ""),
        "updated_at": _utc_now(),
        "estimate_mode": str(estimate.get("estimate_mode") or ""),
        "conversion_event_name": str(
            meta.get("conversion_event_name") or "purchase"
        ),
        "conversion_events": _conversion_events_from_meta(meta),
        "jobs": meta.get("jobs") if isinstance(meta.get("jobs"), dict) else {},
        "hierarchical_refit": (
            meta.get("hierarchical_refit")
            if isinstance(meta.get("hierarchical_refit"), dict)
            else None
        ),
        "artifact_versions": (
            meta.get("artifact_versions")
            if isinstance(meta.get("artifact_versions"), dict)
            else {}
        ),
        "category": meta.get("category") or estimate.get("category"),
        "category_provenance": (
            meta.get("category_provenance")
            if isinstance(meta.get("category_provenance"), dict)
            else None
        ),
        "refit_eligibility": (
            meta.get("refit_eligibility")
            if isinstance(meta.get("refit_eligibility"), dict)
            else None
        ),
        "trends_upload": (
            meta.get("trends_upload")
            if isinstance(meta.get("trends_upload"), dict)
            else None
        ),
        "estimate": estimate,
    }


def persist_completed_model(
    audit_dir: Path,
    *,
    meta: dict[str, Any],
    estimate: dict[str, Any],
) -> dict[str, Any]:
    """Persist a safe completed-model snapshot beside the audit's other files."""
    run_id = str(meta.get("run_id") or "legacy").strip() or "legacy"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise ValueError("Invalid AI-impact run ID")
    model = _completed_model_payload(meta, estimate)
    relative_model_path = f"models/{run_id}/model.json"
    _atomic_write_json(
        _completed_models_root(audit_dir) / run_id / "model.json",
        model,
    )

    pointer_path = audit_dir / "ai_impact" / "latest.json"
    current = _read_json_dict(pointer_path) or {}
    current_created = str(current.get("created_at") or "")
    candidate_created = str(model.get("created_at") or "")
    should_promote = (
        str(current.get("run_id") or "") == run_id
        or not current
        or candidate_created >= current_created
    )
    if should_promote:
        pointer = {
            "schema_version": COMPLETED_MODEL_SCHEMA,
            "run_id": run_id,
            "created_at": candidate_created,
            "updated_at": str(model["updated_at"]),
            "estimate_mode": str(model.get("estimate_mode") or ""),
            "model_path": relative_model_path,
        }
        _atomic_write_json(pointer_path, pointer)
        legacy = dict(estimate)
        legacy["_run_id"] = run_id
        _atomic_write_json(audit_dir / "ai_impact_estimate.json", legacy)
    return model


def _audit_matches_run(audit_dir: Path, audit_id: Any) -> bool:
    value = str(audit_id or "").strip()
    if not value:
        return False
    try:
        from api import geo_services as geo

        return geo.resolve_audit_dir(value).resolve() == audit_dir.resolve()
    except Exception:  # noqa: BLE001
        return False


def _refresh_audit_model_from_run(audit_dir: Path, run_id: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        return
    runs_root = _runs_root()
    if not runs_root.is_dir():
        return
    meta = _read_json_dict(runs_root / run_id / "meta.json")
    if not meta or not _audit_matches_run(audit_dir, meta.get("audit_id")):
        return
    try:
        get_run(run_id)
    except Exception:  # noqa: BLE001
        log.exception("Could not refresh AI-impact run %s", run_id)


def _load_pointed_model(audit_dir: Path) -> dict[str, Any] | None:
    pointer = _read_json_dict(audit_dir / "ai_impact" / "latest.json")
    models_root = _completed_models_root(audit_dir)
    candidates: list[dict[str, Any]] = []
    if models_root.is_dir():
        for run_dir in models_root.iterdir():
            if not run_dir.is_dir():
                continue
            model = _read_json_dict(run_dir / "model.json")
            if (
                model
                and model.get("schema_version") == COMPLETED_MODEL_SCHEMA
                and model.get("status") == "completed"
                and isinstance(model.get("estimate"), dict)
            ):
                candidates.append(model)
    if not candidates:
        return None
    model = max(
        candidates,
        key=lambda item: (
            str(item.get("created_at") or ""),
            str(item.get("updated_at") or ""),
        ),
    )
    run_id = str(model.get("run_id") or "").strip()
    if run_id:
        _refresh_audit_model_from_run(audit_dir, run_id)
        refreshed = _read_json_dict(models_root / run_id / "model.json")
        if refreshed and isinstance(refreshed.get("estimate"), dict):
            model = refreshed
    expected_pointer = {
        "schema_version": COMPLETED_MODEL_SCHEMA,
        "run_id": run_id,
        "created_at": str(model.get("created_at") or ""),
        "updated_at": str(model.get("updated_at") or ""),
        "estimate_mode": str(model.get("estimate_mode") or ""),
        "model_path": f"models/{run_id}/model.json",
    }
    if pointer != expected_pointer:
        _atomic_write_json(audit_dir / "ai_impact" / "latest.json", expected_pointer)
    return model


def _migrate_legacy_completed_model(audit_dir: Path) -> dict[str, Any] | None:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    migration_marker = audit_dir / "ai_impact" / "legacy_scan_complete.json"
    runs_root = _runs_root()
    if not migration_marker.is_file() and runs_root.is_dir():
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            meta = _read_json_dict(run_dir / "meta.json")
            estimate = _read_json_dict(run_dir / "estimate.json")
            if (
                not meta
                or not estimate
                or not _audit_matches_run(audit_dir, meta.get("audit_id"))
                or str(meta.get("status") or "") != "completed"
            ):
                continue
            candidates.append((str(meta.get("created_at") or ""), run_dir, meta))
    if candidates:
        _, run_dir, meta = max(candidates, key=lambda item: item[0])
        _refresh_refit_state(run_dir, meta)
        estimate = _read_json_dict(run_dir / "estimate.json")
        if estimate:
            _atomic_write_json(run_dir / "meta.json", meta)
            persist_completed_model(audit_dir, meta=meta, estimate=estimate)
            _atomic_write_json(
                migration_marker,
                {
                    "checked_at": _utc_now(),
                    "completed_run_count": len(candidates),
                },
            )
            return _load_pointed_model(audit_dir)
    if not migration_marker.is_file():
        _atomic_write_json(
            migration_marker,
            {"checked_at": _utc_now(), "completed_run_count": 0},
        )

    legacy_path = audit_dir / "ai_impact_estimate.json"
    legacy = _read_json_dict(legacy_path)
    if legacy:
        estimate = dict(legacy)
        run_id = str(estimate.pop("_run_id", "") or "legacy")
        meta = _read_json_dict(runs_root / run_id / "meta.json") or {
            "run_id": run_id,
            "audit_id": "",
            "created_at": datetime.fromtimestamp(
                legacy_path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "status": "completed",
            "jobs": {"estimate": "completed"},
            "category": estimate.get("category"),
        }
        if meta.get("audit_id") and not _audit_matches_run(
            audit_dir, meta.get("audit_id")
        ):
            return None
        persist_completed_model(audit_dir, meta=meta, estimate=estimate)
        return _load_pointed_model(audit_dir)
    return None


def load_latest_completed_model_estimate(
    audit_dir: Path,
) -> dict[str, Any] | None:
    model = _load_pointed_model(audit_dir) or _migrate_legacy_completed_model(
        audit_dir
    )
    if not model:
        return None
    estimate = dict(model["estimate"])
    estimate["_run_id"] = str(model.get("run_id") or "")
    return estimate


def _persist_run_model_for_audit(
    meta: dict[str, Any],
    estimate: dict[str, Any] | None,
) -> None:
    audit_id = str(meta.get("audit_id") or "").strip()
    if not audit_id or not isinstance(estimate, dict):
        return
    try:
        from api import geo_services as geo

        audit_dir = geo.resolve_audit_dir(audit_id)
        if not (audit_dir / "audit_summary.json").is_file():
            return
        persist_completed_model(audit_dir, meta=meta, estimate=estimate)
    except Exception:  # noqa: BLE001
        log.exception(
            "Could not persist completed AI-impact model for audit %s", audit_id
        )


def persist_completed_estimate_for_audit(
    audit_dir: Path,
    *,
    estimate: dict[str, Any],
    run_id: str | None = None,
) -> dict[str, Any]:
    """Compatibility writer used by the audit estimate PUT endpoint."""
    safe_run_id = (run_id or "legacy").strip() or "legacy"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", safe_run_id):
        raise ValueError("Invalid AI-impact run ID")
    meta = _read_json_dict(_runs_root() / safe_run_id / "meta.json")
    if meta and meta.get("audit_id") and not _audit_matches_run(
        audit_dir, meta.get("audit_id")
    ):
        raise ValueError("AI-impact run belongs to a different audit")
    if not meta:
        meta = {
            "run_id": safe_run_id,
            "audit_id": "",
            "created_at": _utc_now(),
            "status": "completed",
            "jobs": {"estimate": "completed"},
            "category": estimate.get("category"),
            "conversion_event_name": "purchase",
        }
    return persist_completed_model(audit_dir, meta=meta, estimate=estimate)


def _refit_artifact_versions() -> dict[str, str]:
    """Best-effort promoted artifact identity; absence keeps legacy runs usable."""
    try:
        _ensure_backend_path()
        from ai_impact.model_artifact import load_artifact

        artifact = load_artifact()
        return {
            "baseline_model": artifact.model_version,
            "baseline_signal": artifact.signal_version,
        }
    except Exception as exc:  # noqa: BLE001
        log.info("AI-impact refit artifact is unavailable: %s", exc)
        return {}


def _refit_eligibility(
    panel: Any,
    *,
    category: str | None,
    brand_trends_column: str | None,
    has_ga4: bool,
    has_brand_trends: bool,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    """Count completed, non-Christmas weeks with usable GA4 and brand Trends."""
    _ensure_backend_path()
    import pandas as pd

    from ai_impact.panel import is_completed_sunday_week, is_model_christmas_week

    reasons: list[str] = []
    if not has_ga4:
        reasons.append("ga4_required")
    if not has_brand_trends:
        reasons.append("brand_trends_required")
    if not category:
        reasons.append("category_required")

    eligible_weeks = 0
    if has_ga4 and has_brand_trends:
        frame = panel.copy()
        frame["week"] = pd.to_datetime(frame["week"], errors="coerce")
        trend_columns = (
            [brand_trends_column]
            if brand_trends_column and brand_trends_column in frame.columns
            else []
        )
        required = ["week", "seo_sessions", "direct_sessions", "ai_sessions", "total_sessions"]
        if trend_columns and all(column in frame.columns for column in required):
            complete = frame["week"].map(
                lambda value: bool(
                    pd.notna(value) and is_completed_sunday_week(value, as_of=as_of)
                )
            )
            non_christmas = ~is_model_christmas_week(frame["week"])
            numeric = frame[required[1:]].apply(pd.to_numeric, errors="coerce")
            trends_valid = frame[trend_columns].apply(
                pd.to_numeric, errors="coerce"
            ).notna().any(axis=1)
            usable = (
                complete
                & non_christmas
                & numeric.notna().all(axis=1)
                & (numeric["seo_sessions"] > 0)
                & (numeric["direct_sessions"] > 0)
                & (numeric["total_sessions"] > 0)
                & trends_valid
            )
            eligible_weeks = int(frame.loc[usable, "week"].nunique())
    if eligible_weeks < 8:
        reasons.append("minimum_8_completed_non_christmas_weeks")
    return {
        "eligible": not reasons,
        "eligible_weeks": eligible_weeks,
        "minimum_weeks": 8,
        "requires": ["ga4", "brand_trends", "category"],
        "reasons": reasons,
    }


def _refit_mode() -> str:
    configured = (os.getenv("AI_IMPACT_REFIT_MODE") or "").strip().lower()
    if configured:
        if configured not in {"cloud_run", "local", "noop"}:
            raise ValueError(
                "AI_IMPACT_REFIT_MODE must be cloud_run, local, or noop"
            )
        return configured
    return "cloud_run" if (os.getenv("AI_IMPACT_REFIT_JOB_NAME") or "").strip() else "noop"


def _stage_refit_gcs_inputs(run_dir: Path, run_id: str) -> str:
    root = (os.getenv("AI_IMPACT_REFIT_GCS_ROOT") or "").strip().rstrip("/")
    if not root:
        return str(run_dir)
    if not root.startswith("gs://"):
        raise ValueError("AI_IMPACT_REFIT_GCS_ROOT must be a gs:// URI")
    from google.cloud import storage

    _, _, remainder = root.partition("gs://")
    bucket_name, _, prefix = remainder.partition("/")
    run_prefix = f"{prefix.rstrip('/')}/{run_id}".strip("/")
    bucket = storage.Client().bucket(bucket_name)
    for filename in ("panel.csv", "cold_start_estimate.json"):
        path = run_dir / filename
        if path.is_file():
            bucket.blob(f"{run_prefix}/{filename}").upload_from_filename(str(path))
    return f"gs://{bucket_name}/{run_prefix}"


def _execute_refit_cloud_run(
    *,
    run_id: str,
    run_uri: str,
    category: str,
    brand_trends_column: str,
    window_weeks: int,
) -> str:
    from google.cloud import run_v2

    name = (os.getenv("AI_IMPACT_REFIT_JOB_NAME") or "").strip()
    if not name:
        raise RuntimeError("AI_IMPACT_REFIT_JOB_NAME is not configured")
    project = (
        os.getenv("AI_IMPACT_REFIT_JOB_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GCP_PROJECT")
        or ""
    ).strip()
    region = (
        os.getenv("AI_IMPACT_REFIT_JOB_REGION")
        or os.getenv("CLOUD_RUN_REGION")
        or "europe-west1"
    ).strip()
    if not project:
        raise RuntimeError("AI_IMPACT_REFIT_JOB_PROJECT is not configured")
    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    env = {
        "RUN_ID": run_id,
        "AI_IMPACT_REFIT_RUN_URI": run_uri,
        "AI_IMPACT_REFIT_SITE_ID": run_id,
        "AI_IMPACT_REFIT_CATEGORY": category,
        "AI_IMPACT_BRAND_TRENDS_COLUMN": brand_trends_column,
        "AI_IMPACT_REFIT_WINDOW_WEEKS": str(window_weeks),
    }
    operation = run_v2.JobsClient().run_job(
        request=run_v2.RunJobRequest(
            name=job_path,
            overrides={
                "container_overrides": [
                    {
                        "env": [
                            {"name": key, "value": value}
                            for key, value in env.items()
                        ]
                    }
                ],
                "task_count": 1,
            },
        )
    )
    metadata_name = str(getattr(getattr(operation, "metadata", None), "name", "") or "")
    raw_operation = getattr(operation, "operation", None)
    return (
        metadata_name
        or str(getattr(raw_operation, "name", "") or "")
        or f"{job_path}/operations/accepted-{run_id}"
    )


def _enqueue_hierarchical_refit(
    *,
    run_dir: Path,
    meta: dict[str, Any],
    panel: Any,
    has_ga4: bool,
    has_brand_trends: bool,
) -> None:
    eligibility = _refit_eligibility(
        panel,
        category=meta.get("category"),
        brand_trends_column=meta.get("brand_trends_column"),
        has_ga4=has_ga4,
        has_brand_trends=has_brand_trends,
    )
    meta["refit_eligibility"] = eligibility
    meta["artifact_versions"] = _refit_artifact_versions()
    if not eligibility["eligible"]:
        meta["hierarchical_refit"] = {
            "status": "ineligible",
            "updated_at": _utc_now(),
            "error": None,
        }
        meta["jobs"]["refit"] = "ineligible"
        return
    if not meta["artifact_versions"]:
        meta["hierarchical_refit"] = {
            "status": "unavailable",
            "updated_at": _utc_now(),
            "error": "No compatible promoted hierarchical artifact is available.",
        }
        meta["jobs"]["refit"] = "unavailable"
        return

    mode = _refit_mode()
    request = {
        "run_id": meta["run_id"],
        "category": meta["category"],
        "window_weeks": meta["window_weeks"],
        "created_at": _utc_now(),
        "mode": mode,
        "artifact_versions": meta["artifact_versions"],
    }
    _atomic_write_json(run_dir / "refit_request.json", request)
    if mode == "noop":
        meta["hierarchical_refit"] = {
            "status": "not_configured",
            "updated_at": _utc_now(),
            "execution": "noop",
            "error": None,
        }
        meta["jobs"]["refit"] = "not_configured"
        return

    try:
        run_uri = _stage_refit_gcs_inputs(run_dir, meta["run_id"])
        if mode == "cloud_run":
            execution = _execute_refit_cloud_run(
                run_id=meta["run_id"],
                run_uri=run_uri,
                category=meta["category"],
                brand_trends_column=meta["brand_trends_column"],
                window_weeks=int(meta["window_weeks"]),
            )
        else:
            repo = Path(__file__).resolve().parents[1]
            environment = os.environ.copy()
            environment.update(
                {
                    "RUN_ID": meta["run_id"],
                    "AI_IMPACT_REFIT_RUN_URI": run_uri,
                    "AI_IMPACT_REFIT_SITE_ID": meta["run_id"],
                    "AI_IMPACT_REFIT_CATEGORY": meta["category"],
                    "AI_IMPACT_BRAND_TRENDS_COLUMN": meta["brand_trends_column"],
                    "AI_IMPACT_REFIT_WINDOW_WEEKS": str(meta["window_weeks"]),
                }
            )
            log_handle = (run_dir / "refit_job.log").open("ab")
            try:
                process = subprocess.Popen(  # noqa: S603
                    [sys.executable, "-m", "jobs.ai_impact_refit.run_job"],
                    cwd=repo,
                    env=environment,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            finally:
                log_handle.close()
            execution = f"local-process:{process.pid}"
        meta["hierarchical_refit"] = {
            "status": "queued",
            "updated_at": _utc_now(),
            "execution": execution,
            "error": None,
        }
        meta["jobs"]["refit"] = "queued"
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to enqueue AI-impact hierarchical refit")
        meta["hierarchical_refit"] = {
            "status": "failed",
            "updated_at": _utc_now(),
            "error": str(exc),
        }
        meta["jobs"]["refit"] = "failed"


def _sync_gcs_refit_outputs(run_dir: Path, run_id: str) -> None:
    root = (os.getenv("AI_IMPACT_REFIT_GCS_ROOT") or "").strip().rstrip("/")
    if not root:
        return
    try:
        from google.cloud import storage

        _, _, remainder = root.partition("gs://")
        bucket_name, _, prefix = remainder.partition("/")
        run_prefix = f"{prefix.rstrip('/')}/{run_id}".strip("/")
        bucket = storage.Client().bucket(bucket_name)
        for filename in ("refit_status.json", "refit_diagnostics.json", "estimate.json"):
            blob = bucket.blob(f"{run_prefix}/{filename}")
            if blob.exists():
                destination = run_dir / filename
                try:
                    temporary = destination.parent / (
                        f".{destination.name}.{uuid.uuid4().hex}.download"
                    )
                    blob.download_to_filename(str(temporary))
                    if filename != "estimate.json":
                        temporary.replace(destination)
                        continue
                    try:
                        payload = json.loads(temporary.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        continue
                    if payload.get("estimate_mode") == "site_refit":
                        temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not poll GCS refit outputs for %s: %s", run_id, exc)


def _refresh_refit_state(run_dir: Path, meta: dict[str, Any]) -> bool:
    """Promote completed job output or expose a durable failure on polling."""
    _sync_gcs_refit_outputs(run_dir, str(meta.get("run_id") or run_dir.name))
    changed = False
    estimate_path = run_dir / "estimate.json"
    if estimate_path.is_file():
        try:
            estimate = json.loads(estimate_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            estimate = {}
        if estimate.get("estimate_mode") == "site_refit":
            current = meta.get("hierarchical_refit") or {}
            if current.get("status") != "completed":
                meta["hierarchical_refit"] = estimate.get("hierarchical_refit") or {
                    "status": "completed",
                    "updated_at": _utc_now(),
                }
                meta["hierarchical_refit"]["status"] = "completed"
                meta["jobs"]["refit"] = "completed"
                meta["artifact_versions"] = estimate.get("artifact_versions") or meta.get(
                    "artifact_versions"
                ) or {}
                changed = True
            return changed
    status_path = run_dir / "refit_status.json"
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            status = {}
        if status.get("status") == "failed":
            current = meta.get("hierarchical_refit") or {}
            if current.get("status") != "failed" or current.get("error") != status.get(
                "error"
            ):
                meta["hierarchical_refit"] = {
                    **current,
                    "status": "failed",
                    "updated_at": status.get("updated_at") or _utc_now(),
                    "error": status.get("error") or "Hierarchical refit failed.",
                }
                meta["jobs"]["refit"] = "failed"
                changed = True
    return changed


class CreateAiImpactRunRequest(BaseModel):
    ga4_property_id: str | None = None
    ga4_property_name: str | None = None
    gsc_site_url: str | None = None
    conversion_event_name: str = Field(default="purchase", min_length=1, max_length=500)
    trends_upload_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    start_date: str = "2023-01-01"
    end_date: str | None = None
    window_weeks: int = Field(default=13, ge=4, le=52)
    category: str | None = None
    audit_id: str | None = None
    brand_trends_term: str | None = None
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

    @field_validator("category")
    @classmethod
    def _validate_category(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        _ensure_backend_path()
        from ai_impact.model_artifact import validate_category

        return validate_category(value)


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
    cold_start_estimate: dict[str, Any] | None = None
    hierarchical_refit: dict[str, Any] | None = None
    artifact_versions: dict[str, str] = Field(default_factory=dict)
    category: str | None = None
    category_provenance: dict[str, Any] | None = None
    refit_eligibility: dict[str, Any] | None = None
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
    audit_category, category_provenance = _category_from_audit(body.audit_id)
    category = body.category or audit_category
    if not category:
        raise HTTPException(
            status_code=422,
            detail="A persisted audit site category or explicit category is required.",
        )

    trends_path: Path | None = None
    trends_job_error: str | None = None
    if uploaded_trends_path is not None:
        trends_path = run_dir / "trends_weekly.csv"
        shutil.copy2(uploaded_trends_path, trends_path)
        brand_trends_column = _brand_trends_column(
            trends_metadata, body.brand_trends_term
        )
        trends_job_status = "uploaded"
    else:
        brand = (body.brand_trends_term or "").strip() or _brand_name_from_audit(
            body.audit_id
        )
        if _google_trends_job_configured() and brand:
            try:
                trends_path, trends_metadata = _fetch_brand_trends_via_job(
                    brand=brand,
                    run_id=run_id,
                    run_dir=run_dir,
                    start_date="2023-06-01",
                    audit_id=body.audit_id,
                )
                brand_trends_column = _brand_trends_column(
                    trends_metadata, body.brand_trends_term
                )
                trends_job_status = "completed"
            except Exception as trends_exc:  # noqa: BLE001
                log.exception("Google Trends weekly job failed for brand=%r", brand)
                trends_job_error = str(trends_exc)
                trends_metadata = {
                    "source": "cloud_run_job",
                    "query_term": brand,
                    "error": trends_job_error,
                }
                brand_trends_column = None
                trends_job_status = "failed"
        elif _google_trends_job_configured() and not brand:
            trends_job_status = "skipped"
            brand_trends_column = None
            trends_metadata = {
                "source": "cloud_run_job",
                "error": "No brand name on the audit; set brand_name_used or upload a CSV.",
            }
        else:
            # No upload and job not configured — require an upload for brand column.
            brand_trends_column = _brand_trends_column(
                trends_metadata, body.brand_trends_term
            )
            trends_job_status = "skipped"

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
        "audit_id": body.audit_id,
        "category": category,
        "category_provenance": category_provenance,
        "brand_trends_term": body.brand_trends_term
        or (trends_metadata or {}).get("terms", [None])[0]
        or (trends_metadata or {}).get("query_term"),
        "brand_trends_column": brand_trends_column,
        "artifact_versions": {},
        "cold_start_estimate": None,
        "hierarchical_refit": {"status": "not_started"},
        "refit_eligibility": None,
        "jobs": {
            "ga4": "not_started",
            "gsc": "not_started",
            "trends": trends_job_status,
            "estimate": "not_started",
            "refit": "not_started",
        },
        "needs_ga4_reauth": False,
    }
    if trends_job_error:
        meta["trends_error"] = trends_job_error

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
            from ai_impact.panel import build_weekly_panel, select_brand_trends_column

            panel_path = Path(body.local_panel_path)
            if not panel_path.is_file():
                raise FileNotFoundError(str(panel_path))
            panel = build_weekly_panel(panel_path, trends=trends_path)
            if trends_path is not None:
                select_brand_trends_column(panel, brand_trends_column)
            panel.to_csv(run_dir / "panel.csv", index=False)
            result = estimate_ai_impact(
                panel,
                window_weeks=body.window_weeks,
                category=category,
            )
            estimate = result.as_dict()
            meta["cold_start_estimate"] = estimate
            meta["artifact_versions"] = {
                "baseline_model": estimate["model_artifact_version"],
                "baseline_signal": estimate["signal_artifact_version"],
            }
            _atomic_write_json(run_dir / "cold_start_estimate.json", estimate)
            _atomic_write_json(run_dir / "estimate.json", estimate)
            meta["status"] = "completed"
            meta["jobs"] = {k: "skipped_local_panel" for k in meta["jobs"]}
            meta["jobs"]["estimate"] = "completed"
            _enqueue_hierarchical_refit(
                run_dir=run_dir,
                meta=meta,
                panel=panel,
                has_ga4=True,
                has_brand_trends=trends_path is not None,
            )
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
                        from ai_impact.panel import (
                            build_weekly_panel,
                            select_brand_trends_column,
                        )

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
                        if trends_path is not None:
                            select_brand_trends_column(panel, brand_trends_column)
                        panel.to_csv(run_dir / "panel.csv", index=False)
                        result = estimate_ai_impact(
                            panel,
                            window_weeks=body.window_weeks,
                            category=category,
                        )
                        estimate = result.as_dict()
                        meta["cold_start_estimate"] = estimate
                        meta["artifact_versions"] = {
                            "baseline_model": estimate["model_artifact_version"],
                            "baseline_signal": estimate["signal_artifact_version"],
                        }
                        _atomic_write_json(
                            run_dir / "cold_start_estimate.json", estimate
                        )
                        _atomic_write_json(run_dir / "estimate.json", estimate)
                        meta["jobs"]["estimate"] = "completed"
                        meta["status"] = "completed"
                        _enqueue_hierarchical_refit(
                            run_dir=run_dir,
                            meta=meta,
                            panel=panel,
                            has_ga4=True,
                            has_brand_trends=trends_path is not None,
                        )
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
                "refit": "blocked",
            }
            meta["enqueue_note"] = (
                "Select a GA4 property (wizard OAuth) or pass local_panel_path."
            )

    _atomic_write_json(run_dir / "meta.json", meta)
    _persist_run_model_for_audit(meta, estimate)
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
        cold_start_estimate=meta.get("cold_start_estimate"),
        hierarchical_refit=meta.get("hierarchical_refit"),
        artifact_versions=meta.get("artifact_versions") or {},
        category=meta.get("category"),
        category_provenance=meta.get("category_provenance"),
        refit_eligibility=meta.get("refit_eligibility"),
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
    if _refresh_refit_state(run_dir, meta):
        _atomic_write_json(meta_path, meta)
    estimate = None
    est_path = run_dir / "estimate.json"
    if est_path.is_file():
        estimate = json.loads(est_path.read_text(encoding="utf-8"))
    _persist_run_model_for_audit(meta, estimate)
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
        cold_start_estimate=meta.get("cold_start_estimate"),
        hierarchical_refit=meta.get("hierarchical_refit"),
        artifact_versions=meta.get("artifact_versions") or {},
        category=meta.get("category"),
        category_provenance=meta.get("category_provenance"),
        refit_eligibility=meta.get("refit_eligibility"),
        error=meta.get("error"),
        trends_upload=meta.get("trends_upload"),
        needs_ga4_reauth=bool(meta.get("needs_ga4_reauth")),
    )


@router.get("/config")
def ai_impact_config() -> dict[str, Any]:
    """Frontend bootstrap: required scopes, limits, method notes."""
    auto_fetch = _google_trends_job_configured()
    return {
        "default_window_weeks": 13,
        "trends_manual_upload": True,
        "trends_auto_fetch": auto_fetch,
        "trends_required": True,
        "brand_term_required_when_multiple": True,
        "trends_expected_start": "2023-06-01",
        "trends_expected_end": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "trends_max_terms": 5,
        "trends_url": "https://trends.google.com/trends/explore",
        "ga4_oauth_path": "/api/ga4/login",
        "gsc_oauth_path": "/api/gsc/login",
        "gsc_scope": "https://www.googleapis.com/auth/webmasters.readonly",
        "impression_break": "2025-09-01",
        "holiday_mask": "week_start_dec_15_to_jan_07",
        "ga4_export": "research/ga4/ga4_channel_export.py",
        "quality_narratives": [
            "sessions_down_quality_up",
            "sessions_and_quality_down",
            "sessions_and_quality_up",
            "mixed_or_aligned",
        ],
    }