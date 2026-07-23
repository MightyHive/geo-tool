#!/usr/bin/env python3
"""
Cloud Run Job / local runner: GA4 weekly channels for AI-impact estimates.

Uses ``research/ga4/ga4_channel_export.py`` (same classification + Data API pull).

Auth (first match):
  1. GOOGLE_APPLICATION_CREDENTIALS — user OAuth ADC JSON from the API/wizard session
  2. GA4_OAUTH_TOKEN_PATH — CLI token JSON (research/.ga4_oauth_token.json)
  3. Interactive CLI login when FORCE_GA4_LOGIN=1 (local only)

Env:
  RUN_ID, GA4_PROPERTY_ID, GA4_PROPERTY_NAME, START_DATE, END_DATE, GCS_OUTPUT_URI
  GA4_OAUTH_TOKEN_PATH, FORCE_GA4_LOGIN
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"
_RESEARCH = _REPO / "research"
_GA4_SCRIPTS = _RESEARCH / "ga4"
for p in (_BACKEND, _RESEARCH, _GA4_SCRIPTS, _REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def _upload_or_copy(local: Path, out_uri: str, name: str) -> None:
    if out_uri.startswith("gs://"):
        from google.cloud import storage  # type: ignore

        _, _, rest = out_uri.partition("gs://")
        bucket_name, _, prefix = rest.partition("/")
        blob_path = f"{prefix.rstrip('/')}/{name}"
        storage.Client().bucket(bucket_name).blob(blob_path).upload_from_filename(str(local))
        print(f"[ga4-job] uploaded gs://{bucket_name}/{blob_path}", flush=True)
        return
    dest_dir = Path(out_uri)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    shutil.copy2(local, dest)
    print(f"[ga4-job] wrote {dest}", flush=True)


def main() -> None:
    from geo_app_env import load_app_environment

    load_app_environment()

    run_id = os.environ.get("RUN_ID") or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    property_id = (os.environ.get("GA4_PROPERTY_ID") or "").strip()
    if not property_id:
        raise SystemExit("GA4_PROPERTY_ID is required")
    property_name = (os.environ.get("GA4_PROPERTY_NAME") or property_id).strip()
    start = os.environ.get("START_DATE") or "2022-06-01"
    end = os.environ.get("END_DATE") or datetime.utcnow().strftime("%Y-%m-%d")
    out_uri = os.environ.get("GCS_OUTPUT_URI") or f"/tmp/ai_impact_runs/{run_id}/"
    force_login = (os.environ.get("FORCE_GA4_LOGIN") or "").strip() in {"1", "true", "yes"}
    conversion_event_name = (os.environ.get("GA4_CONVERSION_EVENT_NAME") or "purchase").strip()
    # Prefer wizard-session ADC from the API (GOOGLE_APPLICATION_CREDENTIALS),
    # then explicit GA4_OAUTH_TOKEN_PATH, then default research CLI cache.
    adc_env = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    token_path = (os.environ.get("GA4_OAUTH_TOKEN_PATH") or "").strip()
    token_file: Path | None = None
    if adc_env and Path(adc_env).expanduser().is_file():
        token_file = Path(adc_env).expanduser()
    elif token_path:
        token_file = Path(token_path).expanduser()

    print(
        f"[ga4-job] run_id={run_id} property={property_id} range={start}→{end} "
        f"token={token_file} force_login={force_login}",
        flush=True,
    )

    from ga4_channel_export import run_export  # noqa: E402

    local_dir = Path(f"/tmp/ai_impact_runs/{run_id}")
    local_dir.mkdir(parents=True, exist_ok=True)

    try:
        _long, _wide, weekly_path = run_export(
            property_id,
            property_name=property_name,
            start_date=start,
            end_date=end,
            output_dir=local_dir,
            token_path=token_file,
            force_login=force_login,
            conversion_event_name=conversion_event_name,
        )
    except Exception as exc:  # noqa: BLE001
        # Surface re-auth hint for the API / UI
        meta = {
            "run_id": run_id,
            "status": "failed",
            "error": str(exc),
            "needs_ga4_reauth": True,
            "reauth_path": "/api/ga4/login",
        }
        (local_dir / "ga4_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        _upload_or_copy(local_dir / "ga4_meta.json", out_uri, "ga4_meta.json")
        raise SystemExit(f"GA4 export failed (re-auth via wizard OAuth if needed): {exc}") from exc

    # Canonical name for the estimate step
    canonical = local_dir / "ga4_weekly.csv"
    shutil.copy2(weekly_path, canonical)

    meta = {
        "run_id": run_id,
        "status": "ok",
        "property_id": property_id,
        "property_name": property_name,
        "start": start,
        "end": end,
        "weekly_source": str(weekly_path.name),
        "output": "ga4_weekly.csv",
        "needs_ga4_reauth": False,
    }
    meta_path = local_dir / "ga4_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    _upload_or_copy(canonical, out_uri, "ga4_weekly.csv")
    _upload_or_copy(meta_path, out_uri, "ga4_meta.json")
    print(f"[ga4-job] done → ga4_weekly.csv ({weekly_path})", flush=True)


if __name__ == "__main__":
    main()
