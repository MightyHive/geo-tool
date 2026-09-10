"""Audit-scoped, allowlisted dashboard builder for the Workshop section."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from api import audit_json_cache
from api import geo_services as geo
from api.auth import auth_enabled, current_user

router = APIRouter(prefix="/api/audits", tags=["workshop-dashboard"])

SCHEMA_VERSION = 1
CONFIG_FILE = "workshop_dashboard.json"
DatasetId = Literal["scores", "prompts", "citations", "competitors", "findings"]
Visualization = Literal["table", "bar", "line", "pie"]
FilterChannel = Literal["", "chatbot", "ai_overview"]

CHATBOT_PLATFORMS = frozenset({"gemini", "openai", "claude"})
AI_OVERVIEW_PLATFORMS = frozenset({"google_aio"})
ALLOWED_PLATFORMS = frozenset({"gemini", "openai", "claude", "google_aio"})
PLATFORM_ORDER = ("gemini", "openai", "claude", "google_aio")
CHANNEL_CHATBOT = "Chatbot"
CHANNEL_AI_OVERVIEW = "AI Overview"


def workshop_dashboard_enabled() -> bool:
    value = os.environ.get("WORKSHOP_DASHBOARD_BUILDER_ENABLED", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


DATASET_DEFINITIONS: dict[str, dict[str, Any]] = {
    "scores": {
        "label": "Audit scores",
        "description": "Overall and pillar scores from the integrated audit score model.",
        "dimension_fields": ["pillar"],
        "metric_fields": ["score"],
        "default_dimension": "pillar",
        "default_metric": "score",
        "visualizations": ["table", "bar", "line", "pie"],
    },
    "prompts": {
        "label": "Prompt performance",
        "description": "Metrics-only prompt visibility across the configured markets.",
        "dimension_fields": ["prompt", "topic", "platform", "locale", "sentiment"],
        "metric_fields": ["visibility_pct", "avg_position", "response_count"],
        "default_dimension": "prompt",
        "default_metric": "visibility_pct",
        "visualizations": ["table", "bar", "line"],
    },
    "citations": {
        "label": "Top citations",
        "description": "Aggregated non-brand citation domains from prompt probes.",
        "dimension_fields": ["domain"],
        "metric_fields": ["citations"],
        "default_dimension": "domain",
        "default_metric": "citations",
        "visualizations": ["table", "bar", "line", "pie"],
    },
    "competitors": {
        "label": "Competitor visibility",
        "description": "Primary brand and competitor pillar scores.",
        "dimension_fields": ["name"],
        "metric_fields": [
            "overall",
            "ai_visibility",
            "technical_setup",
            "content_quality",
        ],
        "default_dimension": "name",
        "default_metric": "overall",
        "visualizations": ["table", "bar", "line"],
    },
    "findings": {
        "label": "Technical and content findings",
        "description": "Scored components from the technical and content pillars.",
        "dimension_fields": ["title", "pillar"],
        "metric_fields": ["score", "weight_pct"],
        "default_dimension": "title",
        "default_metric": "score",
        "visualizations": ["table", "bar", "line"],
    },
}


class WidgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=120)
    dataset: DatasetId
    visualization: Visualization
    dimension: str = Field(min_length=1, max_length=80)
    metric: str = Field(min_length=1, max_length=80)
    sort_direction: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=10, ge=1, le=100)
    filter_text: str = Field(default="", max_length=120)
    filter_topic: str = Field(default="", max_length=120)
    filter_channel: FilterChannel = ""
    filter_platform: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def validate_compatibility(self) -> "WidgetConfig":
        definition = DATASET_DEFINITIONS[self.dataset]
        if self.visualization not in definition["visualizations"]:
            raise ValueError(f"{self.visualization} is not supported for {self.dataset}")
        if self.dimension not in definition["dimension_fields"]:
            raise ValueError(f"{self.dimension} is not a dimension for {self.dataset}")
        if self.metric not in definition["metric_fields"]:
            raise ValueError(f"{self.metric} is not a metric for {self.dataset}")
        if self.filter_platform and self.filter_platform not in ALLOWED_PLATFORMS:
            raise ValueError(f"{self.filter_platform} is not an allowed platform filter")
        if self.dataset != "prompts":
            if self.filter_topic or self.filter_channel or self.filter_platform:
                raise ValueError("Topic, channel, and platform filters are only valid for prompts")
        return self


class DashboardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = SCHEMA_VERSION
    title: str = Field(default="Audit dashboard", min_length=1, max_length=120)
    widgets: list[WidgetConfig] = Field(default_factory=list, max_length=12)
    updated_at: str | None = None
    updated_by: str | None = None

    @model_validator(mode="after")
    def validate_unique_widget_ids(self) -> "DashboardConfig":
        ids = [widget.id for widget in self.widgets]
        if len(ids) != len(set(ids)):
            raise ValueError("Widget ids must be unique")
        return self


def _default_config() -> DashboardConfig:
    return DashboardConfig(
        widgets=[
            WidgetConfig(
                id="audit-scores",
                title="Audit scores",
                dataset="scores",
                visualization="bar",
                dimension="pillar",
                metric="score",
            ),
            WidgetConfig(
                id="prompt-visibility",
                title="Prompt visibility",
                dataset="prompts",
                visualization="table",
                dimension="prompt",
                metric="visibility_pct",
                limit=15,
            ),
            WidgetConfig(
                id="top-citations",
                title="Top cited domains",
                dataset="citations",
                visualization="bar",
                dimension="domain",
                metric="citations",
            ),
        ]
    )


def _audit_dir_or_404(audit_id: str) -> Path:
    if not workshop_dashboard_enabled():
        raise HTTPException(404, "Workshop dashboard builder is disabled")
    if "/" in audit_id or "\\" in audit_id or audit_id in {".", ".."}:
        raise HTTPException(404, "Audit not found")
    base = geo.audit_output_base().resolve()
    audit_dir = (base / audit_id).resolve()
    try:
        audit_dir.relative_to(base)
    except ValueError as exc:
        raise HTTPException(404, "Audit not found") from exc
    if not audit_dir.is_dir() or not (audit_dir / "audit_summary.json").is_file():
        raise HTTPException(404, "Audit not found")
    return audit_dir


def _audit_owner_emails(audit_dir: Path) -> set[str]:
    expected = audit_dir.resolve()
    owners: set[str] = set()
    for run in geo.load_archive().get("runs", []):
        if not isinstance(run, dict):
            continue
        rel = str(run.get("audit_dir") or "").strip()
        if not rel:
            continue
        try:
            if geo.resolve_audit_dir(rel).resolve() == expected:
                owner = str(run.get("owner_email") or "").strip().lower()
                if owner:
                    owners.add(owner)
        except (OSError, ValueError):
            continue
    return owners


def _require_write_access(request: Request, audit_dir: Path) -> str:
    if not auth_enabled():
        return "local"
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Sign in required")
    email = str(user.get("email") or "").strip().lower()
    owners = _audit_owner_emails(audit_dir)
    if email in owners:
        return email
    if not owners:
        from geo_app_env import current_app_env

        if current_app_env() == "development":
            return email
    if not email or email not in owners:
        # Avoid disclosing whether another user's audit exists.
        raise HTTPException(404, "Audit not found")
    return email


def _config_path(audit_dir: Path) -> Path:
    return audit_dir / CONFIG_FILE


def read_dashboard_config(audit_dir: Path) -> DashboardConfig:
    payload = audit_json_cache.load_json_cached(_config_path(audit_dir))
    if not isinstance(payload, dict):
        return _default_config()
    try:
        return DashboardConfig.model_validate(payload)
    except ValueError:
        return _default_config()


def write_dashboard_config(audit_dir: Path, config: DashboardConfig) -> None:
    path = _config_path(audit_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(
            json.dumps(config.model_dump(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
        audit_json_cache.invalidate_path(path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 2)
    return None


def _norm_prompt(value: str) -> str:
    return value.strip().lower()


def _channel_for_platform(platform: str) -> str:
    if platform in AI_OVERVIEW_PLATFORMS:
        return CHANNEL_AI_OVERVIEW
    return CHANNEL_CHATBOT


def _topic_lookup(metrics: dict[str, Any]) -> dict[str, str]:
    """Map normalized prompt text → topic label from PSS rows."""
    by_prompt: dict[str, str] = {}
    for key in ("probed_pss_rows", "pss_rows"):
        rows = metrics.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            topic = str(row.get("product_or_service") or "").strip() or "Other"
            for prompt in row.get("prompts") or []:
                text = str(prompt or "").strip()
                if not text:
                    continue
                norm = _norm_prompt(text)
                if norm and norm not in by_prompt:
                    by_prompt[norm] = topic
    return by_prompt


def _topic_for_prompt(
    prompt: str,
    *,
    by_prompt: dict[str, str],
    item: dict[str, Any],
    context: dict[str, Any],
) -> str:
    direct = by_prompt.get(_norm_prompt(prompt))
    if direct:
        return direct

    locale_key = str(item.get("_locale_key") or "").strip()
    locale_probes = context.get("locale_probes") if isinstance(context.get("locale_probes"), dict) else {}
    entry = locale_probes.get(locale_key) if locale_key else None
    if isinstance(entry, dict):
        probed = [str(p).strip() for p in (entry.get("prompts_probed") or [])]
        sources = [str(p).strip() for p in (entry.get("source_prompts") or [])]
        try:
            idx = probed.index(prompt)
        except ValueError:
            idx = -1
        if idx >= 0 and idx < len(sources):
            mapped = by_prompt.get(_norm_prompt(sources[idx]))
            if mapped:
                return mapped

    labels = context.get("category_labels")
    if isinstance(labels, list) and labels:
        first = str(labels[0] or "").strip()
        if first:
            return first
    return "Other"


def _platform_visibility(platform_metrics: dict[str, Any]) -> float | None:
    responses = int(platform_metrics.get("response_count") or 0)
    mentions = int(platform_metrics.get("brand_mention_count") or 0)
    if responses <= 0:
        return None
    return round(100.0 * mentions / responses, 1)


def _responding_platforms(item: dict[str, Any], list_metrics: dict[str, Any]) -> list[str]:
    responded = list_metrics.get("platforms_responded")
    if isinstance(responded, list) and responded:
        return [str(p) for p in responded if str(p) in ALLOWED_PLATFORMS]
    out: list[str] = []
    for platform in PLATFORM_ORDER:
        if item.get(f"has_response_{platform}"):
            out.append(platform)
            continue
        platform_metrics = list_metrics.get("platform_metrics")
        if isinstance(platform_metrics, dict):
            metrics = platform_metrics.get(platform)
            if isinstance(metrics, dict) and int(metrics.get("response_count") or 0) > 0:
                out.append(platform)
    return out


def _scores_and_findings(audit_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    integrated = geo.load_integrated_scores(audit_dir)
    scores = [
        {"pillar": "Overall", "score": _number(integrated.get("overall"))},
        {"pillar": "AI visibility", "score": _number(integrated.get("ai_visibility"))},
        {"pillar": "Technical setup", "score": _number(integrated.get("technical_setup"))},
        {"pillar": "Content quality", "score": _number(integrated.get("content_structure"))},
    ]
    scores = [row for row in scores if row["score"] is not None]

    findings: list[dict[str, Any]] = []
    details = integrated.get("details") if isinstance(integrated.get("details"), dict) else {}
    for key, label in (
        ("technical_setup", "Technical setup"),
        ("content_structure", "Content quality"),
    ):
        detail = details.get(key) if isinstance(details.get(key), dict) else {}
        for component in detail.get("components") or []:
            if not isinstance(component, dict):
                continue
            findings.append(
                {
                    "pillar": label,
                    "title": str(component.get("title") or component.get("key") or "Finding"),
                    "score": _number(component.get("score")),
                    "weight_pct": _number(component.get("weight_pct")),
                    "finding": str(
                        component.get("finding_summary")
                        or component.get("detail")
                        or ""
                    ),
                }
            )
    return scores, findings


def _prompt_rows(audit_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    from api.prompt_performance_metrics import context_from_metrics, read_metrics_file

    metrics = read_metrics_file(audit_dir)
    if not isinstance(metrics, dict):
        return [], None
    context = context_from_metrics(metrics, locale_key="__overall__")
    live = context.get("live_probe") if isinstance(context.get("live_probe"), dict) else {}
    by_prompt = _topic_lookup(metrics)
    rows: list[dict[str, Any]] = []
    for item in live.get("per_prompt") or []:
        if not isinstance(item, dict):
            continue
        list_metrics = item.get("list_metrics") if isinstance(item.get("list_metrics"), dict) else {}
        prompt = str(item.get("prompt") or "").strip()
        if not prompt:
            continue
        topic = _topic_for_prompt(prompt, by_prompt=by_prompt, item=item, context=context)
        locale = str(item.get("_locale_key") or context.get("default_locale_key") or "")
        sentiment = str(list_metrics.get("sentiment") or "unavailable")
        platform_metrics_map = (
            list_metrics.get("platform_metrics")
            if isinstance(list_metrics.get("platform_metrics"), dict)
            else {}
        )
        platforms = _responding_platforms(item, list_metrics)
        if not platforms:
            # Keep a prompt-level fallback row so incomplete audits still surface.
            rows.append(
                {
                    "prompt": prompt,
                    "topic": topic,
                    "platform": "",
                    "channel": "",
                    "locale": locale,
                    "visibility_pct": _number(list_metrics.get("visibility_pct")),
                    "avg_position": _number(list_metrics.get("avg_position")),
                    "response_count": int(list_metrics.get("response_count") or 0),
                    "sentiment": sentiment,
                }
            )
            continue
        for platform in platforms:
            plat_metrics = (
                platform_metrics_map.get(platform)
                if isinstance(platform_metrics_map.get(platform), dict)
                else {}
            )
            visibility = _platform_visibility(plat_metrics)
            if visibility is None:
                # Fall back to overall prompt visibility when platform metrics lack counts.
                visibility = _number(list_metrics.get("visibility_pct"))
            rows.append(
                {
                    "prompt": prompt,
                    "topic": topic,
                    "platform": platform,
                    "channel": _channel_for_platform(platform),
                    "locale": locale,
                    "visibility_pct": visibility,
                    "avg_position": _number(plat_metrics.get("avg_position"))
                    if plat_metrics.get("avg_position") is not None
                    else _number(list_metrics.get("avg_position")),
                    "response_count": int(plat_metrics.get("response_count") or 0)
                    or (1 if item.get(f"has_response_{platform}") else 0),
                    "sentiment": sentiment,
                }
            )
    return rows, metrics


def _prompt_filter_options(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    topics = sorted({str(row.get("topic") or "").strip() for row in rows if str(row.get("topic") or "").strip()})
    platforms = [p for p in PLATFORM_ORDER if any(str(row.get("platform") or "") == p for row in rows)]
    channels: list[str] = []
    if any(str(row.get("channel") or "") == CHANNEL_CHATBOT for row in rows):
        channels.append("chatbot")
    if any(str(row.get("channel") or "") == CHANNEL_AI_OVERVIEW for row in rows):
        channels.append("ai_overview")
    return {"topics": topics, "platforms": platforms, "channels": channels}


def _citation_rows(metrics: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not metrics:
        return []
    from api.prompt_performance_metrics import build_citations_view_payload

    payload = build_citations_view_payload(metrics, locale_key="__overall__")
    rows: list[dict[str, Any]] = []
    for item in payload.get("top_cited_sites") or []:
        if not isinstance(item, dict):
            continue
        domain = str(
            item.get("domain")
            or item.get("hostname")
            or item.get("site")
            or item.get("name")
            or ""
        ).strip()
        if not domain:
            continue
        count = item.get("frequency", item.get("count", item.get("citations", 0)))
        rows.append({"domain": domain, "citations": int(count or 0)})
    return rows


def _competitor_rows(audit_dir: Path) -> list[dict[str, Any]]:
    comparison = geo.load_competitive_comparison(audit_dir)
    rows: list[dict[str, Any]] = []
    for item in comparison.get("rows") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "name": str(item.get("name") or "Unknown"),
                "overall": _number(item.get("overall")),
                "ai_visibility": _number(item.get("ai_visibility")),
                "technical_setup": _number(item.get("technical_setup")),
                "content_quality": _number(item.get("content_quality")),
                "is_primary": bool(item.get("is_primary")),
            }
        )
    return rows


def build_dashboard_data(audit_dir: Path) -> dict[str, Any]:
    scores, findings = _scores_and_findings(audit_dir)
    prompts, metrics = _prompt_rows(audit_dir)
    rows_by_id = {
        "scores": scores,
        "prompts": prompts,
        "citations": _citation_rows(metrics),
        "competitors": _competitor_rows(audit_dir),
        "findings": findings,
    }
    datasets = []
    for dataset_id, definition in DATASET_DEFINITIONS.items():
        payload = {
            "id": dataset_id,
            **definition,
            "rows": rows_by_id[dataset_id],
            "row_count": len(rows_by_id[dataset_id]),
        }
        if dataset_id == "prompts":
            payload["filter_options"] = _prompt_filter_options(prompts)
        datasets.append(payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "provenance": {
            "audit_id": audit_dir.name,
            "source": "Persisted audit aggregates",
            "replies_omitted": True,
        },
        "datasets": datasets,
    }


@router.get("/{audit_id}/workshop/dashboard")
def get_dashboard(audit_id: str, request: Request) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    return read_dashboard_config(audit_dir).model_dump()


@router.put("/{audit_id}/workshop/dashboard")
def put_dashboard(
    audit_id: str,
    config: DashboardConfig,
    request: Request,
) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    updated_by = _require_write_access(request, audit_dir)
    saved = config.model_copy(
        update={
            "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "updated_by": updated_by,
        }
    )
    write_dashboard_config(audit_dir, saved)
    return saved.model_dump()


@router.get("/{audit_id}/workshop/dashboard-data")
def get_dashboard_data(audit_id: str, request: Request) -> dict[str, Any]:
    audit_dir = _audit_dir_or_404(audit_id)
    return build_dashboard_data(audit_dir)
