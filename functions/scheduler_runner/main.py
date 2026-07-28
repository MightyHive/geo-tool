import base64
import json
import logging
import os
from typing import Any

import functions_framework
from cloudevents.http import CloudEvent
from google.cloud import run_v2

log = logging.getLogger("scheduler_job_runner")


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


def _run_scheduler_job(action: str, payload: dict[str, Any]) -> str:
    project = payload.get("SCHEDULER_JOB_PROJECT") or os.environ.get("SCHEDULER_JOB_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    region = payload.get("SCHEDULER_JOB_REGION") or os.environ.get("SCHEDULER_JOB_REGION") or os.environ.get("REGION")
    name = payload.get("SCHEDULER_JOB_NAME") or os.environ.get("SCHEDULER_JOB_NAME")
    if not project or not region or not name:
        raise RuntimeError("SCHEDULER_JOB_PROJECT, SCHEDULER_JOB_REGION, and SCHEDULER_JOB_NAME are required")

    job_path = f"projects/{project}/locations/{region}/jobs/{name}"
    env_vars = [{"name": "SCHEDULER_RUNNER_ACTION", "value": action}]

    for key in [
        "APP_ENV",
        "BUCKET",
        "GEO_DATA_ROOT",
        "PROMPT_PROBE_JOB_NAME",
        "PROMPT_PROBE_JOB_REGION",
        "PROMPT_PROBE_JOB_PROJECT",
        "AUDIT_CRAWL_JOB_NAME",
        "AUDIT_CRAWL_JOB_REGION",
        "AUDIT_CRAWL_JOB_PROJECT",
        "PDF_EXPORT_JOB_NAME",
        "PDF_EXPORT_JOB_REGION",
        "PDF_EXPORT_JOB_PROJECT",
        "PROMPT_SENTIMENT_JOB_NAME",
        "PROMPT_SENTIMENT_JOB_REGION",
        "PROMPT_SENTIMENT_JOB_PROJECT",
        "CONTENT_QUALITY_JOB_NAME",
        "CONTENT_QUALITY_JOB_REGION",
        "CONTENT_QUALITY_JOB_PROJECT",
    ]:
        value = payload.get(key) or os.environ.get(key)
        if value:
            env_vars.append({"name": key, "value": value})

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
    if action not in {"daily-rerun", "weekly-crawl"}:
        log.error("Unsupported or missing action: %r", action)
        return

    try:
        operation_name = _run_scheduler_job(action, data)
    except Exception as exc:
        log.exception("Failed to launch scheduler job for action=%s", action)
        raise

    log.info("Launched scheduler job for action=%s: %s", action, operation_name)
