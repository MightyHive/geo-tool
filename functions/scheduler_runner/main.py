"""Pub/Sub Cloud Function: launch environment-isolated scheduler runner Jobs.

Trusted routing only. Downstream job names and bucket mounts live on the
dev/staging Cloud Run Jobs themselves — Pub/Sub payloads must not override them.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any

import functions_framework
from cloudevents.http import CloudEvent
from google.cloud import run_v2

log = logging.getLogger("scheduler_job_runner")

_SUPPORTED_ACTIONS = frozenset({"daily-rerun", "monthly-crawl"})
_SUPPORTED_ENVIRONMENTS = frozenset({"dev", "staging"})


def _decode_payload(message: dict[str, Any]) -> dict[str, Any]:
    if not message.get("data"):
        return {}
    decoded = base64.b64decode(message["data"]).decode("utf-8")
    try:
        parsed = json.loads(decoded)
    except json.JSONDecodeError:
        return {"message": decoded}
    return parsed if isinstance(parsed, dict) else {"message": parsed}


def _job_client() -> run_v2.JobsClient:
    return run_v2.JobsClient()


def _runner_job_name(environment: str) -> str:
    if environment == "staging":
        return (
            os.environ.get("STAGING_SCHEDULER_JOB_NAME")
            or "geo-audit-scheduler-runner-staging"
        )
    return os.environ.get("DEV_SCHEDULER_JOB_NAME") or "geo-audit-scheduler-runner-dev"


def _run_scheduler_job(action: str, payload: dict[str, Any]) -> str:
    environment = str(payload.get("environment") or "").strip().lower()
    if environment not in _SUPPORTED_ENVIRONMENTS:
        raise RuntimeError(f"Unsupported or missing environment: {environment!r}")

    project = (
        os.environ.get("SCHEDULER_JOB_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCP_PROJECT")
    )
    region = (
        os.environ.get("SCHEDULER_JOB_REGION")
        or os.environ.get("REGION")
        or "europe-west1"
    )
    name = _runner_job_name(environment)
    if not project or not region or not name:
        raise RuntimeError(
            "SCHEDULER_JOB_PROJECT, SCHEDULER_JOB_REGION, and runner job name are required"
        )

    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    excluded_val = payload.get("excluded_audits")
    if excluded_val is None:
        excluded_val = os.environ.get("SCHEDULE_EXCLUDED_AUDITS") or ""
    env_vars = [
        {"name": "SCHEDULER_RUNNER_ACTION", "value": action},
        {"name": "EXCLUDED_AUDITS", "value": str(excluded_val)},
    ]

    overrides = {
        "container_overrides": [
            {
                "env": env_vars,
            }
        ],
        "task_count": 1,
    }
    client = _job_client()
    operation = client.run_job(request=run_v2.RunJobRequest(name=job_path, overrides=overrides))
    accepted_name = getattr(getattr(operation, "metadata", None), "name", None) or getattr(
        getattr(operation, "operation", None), "name", ""
    )
    return accepted_name or f"{job_path}/operations/accepted-{action}"


@functions_framework.cloud_event
def pubsub_handler(cloud_event: CloudEvent) -> None:
    message = (cloud_event.data or {}).get("message") or {}
    data = _decode_payload(message)
    action = str(data.get("action") or "").strip()
    if action not in _SUPPORTED_ACTIONS:
        log.error("Unsupported or missing action: %r", action)
        return

    try:
        operation_name = _run_scheduler_job(action, data)
    except Exception:
        log.exception("Failed to launch scheduler job for action=%s", action)
        raise

    log.info(
        "Launched scheduler job for action=%s environment=%s: %s",
        action,
        data.get("environment"),
        operation_name,
    )
