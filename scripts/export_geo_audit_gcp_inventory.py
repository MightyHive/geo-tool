#!/usr/bin/env python3
"""Export a secret-free GCP inventory for GEO Audit migration verification."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud import storage


def gcloud_json(*args: str, allow_missing: bool = False) -> Any:
    command = ["gcloud", *args, "--format=json"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        if allow_missing:
            return {"error": result.stderr.strip(), "command": command}
        raise RuntimeError(result.stderr.strip() or f"Failed: {command}")
    return json.loads(result.stdout or "null")


def bucket_objects(bucket_name: str, project: str) -> list[dict[str, Any]]:
    client = storage.Client(project=project)
    rows = []
    for blob in client.list_blobs(bucket_name):
        rows.append(
            {
                "name": blob.name,
                "size": blob.size,
                "generation": blob.generation,
                "crc32c": blob.crc32c,
                "md5_hash": blob.md5_hash,
                "content_type": blob.content_type,
                "updated": blob.updated.isoformat() if blob.updated else None,
                "metadata": blob.metadata or {},
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="europe-west1")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--bucket", action="append", default=[])
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": gcloud_json("projects", "describe", args.project),
        "enabled_services": gcloud_json(
            "services", "list", "--enabled", "--project", args.project
        ),
        "run_services": gcloud_json(
            "run",
            "services",
            "list",
            "--project",
            args.project,
            "--region",
            args.region,
            allow_missing=True,
        ),
        "run_jobs": gcloud_json(
            "run",
            "jobs",
            "list",
            "--project",
            args.project,
            "--region",
            args.region,
            allow_missing=True,
        ),
        "service_accounts": gcloud_json(
            "iam", "service-accounts", "list", "--project", args.project
        ),
        "project_iam": gcloud_json(
            "projects", "get-iam-policy", args.project
        ),
        "secrets": gcloud_json(
            "secrets", "list", "--project", args.project, allow_missing=True
        ),
        "scheduler": gcloud_json(
            "scheduler",
            "jobs",
            "list",
            "--project",
            args.project,
            "--location",
            args.region,
            allow_missing=True,
        ),
        "functions": gcloud_json(
            "functions",
            "list",
            "--project",
            args.project,
            "--regions",
            args.region,
            allow_missing=True,
        ),
        "pubsub_topics": gcloud_json(
            "pubsub", "topics", "list", "--project", args.project, allow_missing=True
        ),
        "buckets": {},
    }
    for bucket_name in args.bucket:
        metadata = gcloud_json(
            "storage",
            "buckets",
            "describe",
            f"gs://{bucket_name}",
            "--project",
            args.project,
        )
        objects = bucket_objects(bucket_name, args.project)
        inventory["buckets"][bucket_name] = {
            "metadata": metadata,
            "object_count": len(objects),
            "total_bytes": sum(int(row["size"] or 0) for row in objects),
        }
        (args.output / f"{bucket_name}.objects.json").write_text(
            json.dumps(objects, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    (args.output / "inventory.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
