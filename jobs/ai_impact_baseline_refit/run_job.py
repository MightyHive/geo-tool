"""Run a full portfolio AI-impact baseline fit in Cloud Run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from google.api_core.exceptions import NotFound
from google.cloud import storage


def _split_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected gs:// URI, got {uri!r}")
    bucket, _, blob = uri[5:].partition("/")
    if not bucket or not blob:
        raise ValueError(f"Expected gs://bucket/object URI, got {uri!r}")
    return bucket, blob


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        for member in bundle.getmembers():
            target = (destination / member.name).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError(f"Unsafe archive member: {member.name}")
        bundle.extractall(destination)


def _upload_tree(client: storage.Client, source: Path, destination_uri: str) -> None:
    bucket_name, prefix = _split_gcs_uri(destination_uri.rstrip("/") + "/sentinel")
    prefix = prefix.rsplit("/", 1)[0]
    bucket = client.bucket(bucket_name)
    for path in sorted(source.rglob("*")):
        if path.is_file():
            relative = path.relative_to(source).as_posix()
            bucket.blob(f"{prefix}/{relative}").upload_from_filename(path)


def _update_current(
    client: storage.Client,
    current_uri: str,
    version: str,
) -> None:
    """Compare-and-swap CURRENT so concurrent monthly runs cannot race."""
    bucket_name, blob_name = _split_gcs_uri(current_uri)
    blob = client.bucket(bucket_name).blob(blob_name)
    try:
        blob.reload()
        generation = int(blob.generation)
    except NotFound:
        generation = 0
    blob.upload_from_string(
        version + "\n",
        content_type="text/plain",
        if_generation_match=generation,
    )


def main() -> int:
    input_uri = os.environ["AI_IMPACT_TRAINING_INPUT_URI"]
    output_uri = os.environ["AI_IMPACT_TRAINING_OUTPUT_URI"].rstrip("/")
    version = os.environ["AI_IMPACT_MODEL_VERSION"]
    client = storage.Client()
    started = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory(prefix="ai-impact-baseline-") as temporary:
        root = Path(temporary)
        archive = root / "inputs.tar.gz"
        bucket_name, blob_name = _split_gcs_uri(input_uri)
        client.bucket(bucket_name).blob(blob_name).download_to_filename(archive)
        _safe_extract(archive, root)

        modelling = root / "modelling"
        output_root = root / "artifacts"
        env = {
            **os.environ,
            "AI_IMPACT_MODEL_VERSION": version,
            "AI_IMPACT_PROPERTIES_PATH": str(modelling / "properties.csv"),
            "AI_IMPACT_SESSIONS_DIR": str(modelling / "sessions"),
            "AI_IMPACT_TRENDS_DIR": str(modelling / "google_trends"),
            "AI_IMPACT_PLOTS_DIR": str(root / "plots"),
            "PYTHONUNBUFFERED": "1",
        }
        command = [
            sys.executable,
            "/app/modelling/train_baseline_artifact.py",
            "--version",
            version,
            "--output-root",
            str(output_root),
        ]
        result = subprocess.run(command, env=env, check=False)
        status = {
            "version": version,
            "started_at": started.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "exit_code": result.returncode,
            "input_uri": input_uri,
        }
        status_path = root / "status.json"
        status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
        status_bucket, status_prefix = _split_gcs_uri(
            f"{output_uri}/runs/{version}/status.json"
        )
        client.bucket(status_bucket).blob(status_prefix).upload_from_filename(status_path)
        if result.returncode:
            return result.returncode

        _upload_tree(client, output_root / version, f"{output_uri}/versions/{version}")
        _update_current(client, f"{output_uri}/CURRENT", version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
