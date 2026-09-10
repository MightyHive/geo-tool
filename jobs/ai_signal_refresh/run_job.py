#!/usr/bin/env python3
"""Refresh and publish the model artifact's weekly portfolio AI signal.

This job only refreshes the model input signal. It deliberately does not fit or
modify posterior artifacts.

Environment:
  AI_SIGNAL_INPUT_PATH   Session CSV directory (default: research/modelling/sessions)
  AI_SIGNAL_OUTPUT_URI   Local directory or gs://bucket/prefix
  AI_SIGNAL_VERSION      Optional immutable publication version
  AI_SIGNAL_AS_OF        Optional ISO date/time used to determine completed weeks
  AI_IMPACT_MODEL_*      Current frozen model artifact selection (see model_artifact)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from backend.ai_impact import model_artifact  # noqa: E402

SIGNAL_SCHEMA_VERSION = "ai-impact-portfolio-signal-v1"
AI_CHANNEL = "AI Chatbots"
DEFAULT_INPUT = _REPO / "research" / "modelling" / "sessions"
DEFAULT_OUTPUT = Path("/tmp/ai_impact_signal_artifacts")
REQUIRED_COLUMNS = {"date", "name", "custom_channel_grouping", "sessions"}


def _as_of_timestamp(value: str | datetime | pd.Timestamp | None) -> pd.Timestamp:
    if value is None or value == "":
        stamp = pd.Timestamp.now(tz="UTC")
    else:
        stamp = pd.Timestamp(value)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize("UTC")
        else:
            stamp = stamp.tz_convert("UTC")
    return stamp


def _session_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"AI signal input does not exist: {input_path}")
    files = sorted(input_path.rglob("sessions_*.csv"))
    if not files:
        raise FileNotFoundError(f"No sessions_*.csv files found under {input_path}")
    return files


def _files_for_sites(files: Iterable[Path], expected_sites: Iterable[str]) -> dict[str, Path]:
    expected = tuple(str(site) for site in expected_sites)
    expected_set = set(expected)
    selected: dict[str, Path] = {}
    unresolved: list[Path] = []

    for path in files:
        filename_site = path.stem.removeprefix("sessions_")
        if filename_site in expected_set:
            if filename_site in selected:
                raise ValueError(f"Multiple session files found for {filename_site}")
            selected[filename_site] = path
        else:
            unresolved.append(path)

    # A configured consolidated file or non-standard filenames are supported by
    # inspecting the name column. One file must still represent one site.
    missing = expected_set - set(selected)
    for path in unresolved:
        if not missing:
            break
        names = pd.read_csv(path, usecols=["name"])["name"].dropna().astype(str).unique()
        matches = expected_set.intersection(names)
        if len(matches) > 1:
            raise ValueError(f"Session file must contain one portfolio site: {path}")
        if matches:
            site = next(iter(matches))
            if site in selected:
                raise ValueError(f"Multiple session files found for {site}")
            selected[site] = path
            missing.discard(site)

    missing = expected_set - set(selected)
    if missing:
        preview = ", ".join(sorted(missing)[:8])
        raise ValueError(f"Missing session data for {len(missing)} portfolio sites: {preview}")
    return {site: selected[site] for site in expected}


def compute_weekly_signal(
    input_path: Path | str,
    artifact: model_artifact.HierarchicalArtifact,
    *,
    as_of: str | datetime | pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Aggregate complete Sunday-start weeks and apply the frozen transform."""
    expected_sites = tuple(str(site) for site in artifact.manifest.get("site_order") or ())
    if not expected_sites or len(set(expected_sites)) != len(expected_sites):
        raise model_artifact.ArtifactCompatibilityError(
            "Current model artifact must identify a non-empty set of unique training sites"
        )

    selected = _files_for_sites(_session_files(Path(input_path)), expected_sites)
    cutoff = _as_of_timestamp(as_of).normalize().tz_localize(None)
    portfolio_ai = pd.Series(dtype=float)
    portfolio_total = pd.Series(dtype=float)

    for site, path in selected.items():
        frame = pd.read_csv(path)
        missing_columns = REQUIRED_COLUMNS - set(frame.columns)
        if missing_columns:
            raise ValueError(f"{path} is missing columns: {sorted(missing_columns)}")
        names = set(frame["name"].dropna().astype(str).unique())
        if names != {site}:
            raise ValueError(f"{path} contains site names {sorted(names)}, expected {site}")

        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.tz_localize(None)
        frame["sessions"] = pd.to_numeric(frame["sessions"], errors="raise")
        if frame["sessions"].isna().any() or (frame["sessions"] < 0).any():
            raise ValueError(f"{path} contains missing or negative session totals")
        frame["week_start"] = frame["date"].dt.to_period("W-SAT").dt.start_time

        # Require all seven calendar dates and a Saturday before the as-of date.
        days = frame.groupby("week_start")["date"].nunique()
        complete = days[days == 7].index
        complete = complete[(complete + pd.Timedelta(days=6)) < cutoff]
        frame = frame[frame["week_start"].isin(complete)]
        if frame.empty:
            raise ValueError(f"{path} has no completed Sunday-start weeks")

        exact_total_rows = frame[
            frame["custom_channel_grouping"] == "All Sessions"
        ]
        if exact_total_rows.empty:
            weekly_total = frame.groupby("week_start")["sessions"].sum()
        else:
            weekly_total = exact_total_rows.groupby("week_start")["sessions"].sum()
        weekly_ai = (
            frame.loc[frame["custom_channel_grouping"] == AI_CHANNEL]
            .groupby("week_start")["sessions"]
            .sum()
        )
        portfolio_total = portfolio_total.add(weekly_total, fill_value=0.0)
        portfolio_ai = portfolio_ai.add(weekly_ai, fill_value=0.0)

    signal = pd.DataFrame(
        {
            "week_start": portfolio_total.index,
            "ai_sessions_total": portfolio_ai.reindex(
                portfolio_total.index, fill_value=0.0
            ).to_numpy(),
            "all_sessions_total": portfolio_total.to_numpy(),
        }
    ).sort_values("week_start", ignore_index=True)
    if signal.empty or (signal["all_sessions_total"] <= 0).any():
        raise ValueError("Completed portfolio weeks must have positive total sessions")

    signal["ai_share"] = signal["ai_sessions_total"] / signal["all_sessions_total"]
    transform = artifact.manifest.get("transform")
    if not isinstance(transform, dict):
        raise model_artifact.ArtifactCompatibilityError(
            "Current model artifact has no frozen signal transform"
        )
    signal["ai_signal"] = model_artifact.transform_ai_share(
        signal["ai_share"], transform, capped=False
    )
    signal["ai_signal_capped"] = model_artifact.transform_ai_share(
        signal["ai_share"], transform, capped=True
    )

    latest = pd.Timestamp(signal["week_start"].max())
    if latest + pd.Timedelta(days=6) >= cutoff:
        raise AssertionError("Refusing to publish an incomplete portfolio week")
    details = {
        "site_count": len(selected),
        "source_file_count": len(selected),
        "latest_completed_week": latest.date().isoformat(),
        "as_of": _as_of_timestamp(as_of).isoformat(),
    }
    return signal, details


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _csv_gzip_bytes(signal: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    signal.to_csv(buffer, index=False, compression={"method": "gzip", "mtime": 0})
    return buffer.getvalue()


def _publish_local(
    output_root: Path, version: str, files: dict[str, bytes], pointer: dict[str, Any]
) -> str:
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / version
    if destination.exists():
        raise FileExistsError(f"Signal artifact version already exists: {destination}")
    staging = Path(tempfile.mkdtemp(prefix=f".{version}.", dir=output_root))
    try:
        for name, content in files.items():
            (staging / name).write_bytes(content)
        os.replace(staging, destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    temp_pointer = output_root / f".CURRENT.{os.getpid()}.tmp"
    temp_pointer.write_text(version + "\n", encoding="utf-8")
    os.replace(temp_pointer, output_root / "CURRENT")
    (output_root / "latest.json").write_bytes(_json_bytes(pointer))
    return str(destination)


def _publish_gcs(
    output_uri: str, version: str, files: dict[str, bytes], pointer: dict[str, Any]
) -> str:
    from google.cloud import storage  # type: ignore

    rest = output_uri.removeprefix("gs://")
    bucket_name, separator, prefix = rest.partition("/")
    if not bucket_name:
        raise ValueError(f"Invalid GCS output URI: {output_uri}")
    base = prefix.strip("/")
    version_prefix = "/".join(part for part in (base, version) if part)
    bucket = storage.Client().bucket(bucket_name)
    for name, content in files.items():
        blob = bucket.blob(f"{version_prefix}/{name}")
        blob.upload_from_string(content, if_generation_match=0)
    current_name = "/".join(part for part in (base, "CURRENT") if part)
    latest_name = "/".join(part for part in (base, "latest.json") if part)
    bucket.blob(current_name).upload_from_string(version + "\n")
    bucket.blob(latest_name).upload_from_string(
        _json_bytes(pointer), content_type="application/json"
    )
    return f"gs://{bucket_name}/{version_prefix}"


def refresh_and_publish(
    *,
    input_path: Path | str,
    output_uri: Path | str,
    artifact: model_artifact.HierarchicalArtifact,
    as_of: str | datetime | pd.Timestamp | None = None,
    version: str | None = None,
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    signal, details = compute_weekly_signal(input_path, artifact, as_of=as_of)
    signal_version = version or (
        f"portfolio-ai-signal-{details['latest_completed_week']}-"
        f"{generated_at.strftime('%Y%m%dT%H%M%SZ')}"
    )
    if Path(signal_version).name != signal_version:
        raise ValueError("AI signal version must be a single path component")

    manifest = {
        "schema_version": SIGNAL_SCHEMA_VERSION,
        "signal_version": signal_version,
        "generated_at": generated_at.isoformat(),
        "job_type": "portfolio_signal_refresh",
        "posterior_retrained": False,
        "model_version": artifact.model_version,
        "model_schema_version": artifact.manifest.get("schema_version"),
        "transform": artifact.manifest.get("transform"),
        "training_panel_version": artifact.manifest.get("training_panel_version"),
        "input": str(input_path),
        "site_count": details["site_count"],
        "source_file_count": details["source_file_count"],
        "week_count": len(signal),
        "latest_completed_week": details["latest_completed_week"],
        "latest_completed_portfolio_week": details["latest_completed_week"],
        "as_of": details["as_of"],
        "files": {"portfolio_signal": "portfolio_signal.csv.gz"},
        "columns": list(signal.columns),
    }
    pointer = {
        "schema_version": SIGNAL_SCHEMA_VERSION,
        "signal_version": signal_version,
        "model_version": artifact.model_version,
        "latest_completed_week": details["latest_completed_week"],
        "manifest": f"{signal_version}/manifest.json",
    }
    files = {
        "portfolio_signal.csv.gz": _csv_gzip_bytes(signal),
        "manifest.json": _json_bytes(manifest),
    }
    destination = str(output_uri)
    if destination.startswith("gs://"):
        published_uri = _publish_gcs(destination, signal_version, files, pointer)
    else:
        published_uri = _publish_local(Path(destination), signal_version, files, pointer)
    return {**pointer, "published_uri": published_uri, "week_count": len(signal)}


def main() -> int:
    artifact = model_artifact.load_artifact()
    result = refresh_and_publish(
        input_path=os.environ.get("AI_SIGNAL_INPUT_PATH") or DEFAULT_INPUT,
        output_uri=os.environ.get("AI_SIGNAL_OUTPUT_URI") or DEFAULT_OUTPUT,
        artifact=artifact,
        as_of=os.environ.get("AI_SIGNAL_AS_OF"),
        version=os.environ.get("AI_SIGNAL_VERSION") or None,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
