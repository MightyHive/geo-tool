from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api import workshop_dashboards as dashboards


def _audit_dir(tmp_path: Path) -> Path:
    audit_dir = tmp_path / "audit_output" / "example"
    audit_dir.mkdir(parents=True)
    (audit_dir / "audit_summary.json").write_text('{"base_url":"https://example.com"}')
    return audit_dir


def test_widget_schema_rejects_unknown_fields_and_incompatible_options() -> None:
    with pytest.raises(ValidationError):
        dashboards.WidgetConfig(
            id="bad",
            title="Bad widget",
            dataset="scores",
            visualization="pie",
            dimension="prompt",
            metric="score",
        )

    with pytest.raises(ValidationError):
        dashboards.WidgetConfig(
            id="bad",
            title="Bad widget",
            dataset="scores",
            visualization="bar",
            dimension="pillar",
            metric="score",
            data_source="https://example.com/arbitrary.json",
        )


def test_dashboard_config_round_trip_is_atomic(tmp_path: Path) -> None:
    audit_dir = _audit_dir(tmp_path)
    config = dashboards.DashboardConfig(
        title="Client view",
        widgets=[
            dashboards.WidgetConfig(
                id="scores",
                title="Scores",
                dataset="scores",
                visualization="bar",
                dimension="pillar",
                metric="score",
            )
        ],
    )

    dashboards.write_dashboard_config(audit_dir, config)

    assert dashboards.read_dashboard_config(audit_dir) == config
    assert not list(audit_dir.glob(".*.tmp"))


def test_dashboard_config_rejects_duplicate_widget_ids() -> None:
    widget = {
        "id": "duplicate",
        "title": "Scores",
        "dataset": "scores",
        "visualization": "bar",
        "dimension": "pillar",
        "metric": "score",
    }
    with pytest.raises(ValidationError, match="Widget ids must be unique"):
        dashboards.DashboardConfig(widgets=[widget, widget])


def test_build_dashboard_data_uses_normalized_aggregates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _audit_dir(tmp_path)
    integrated = {
        "overall": 72.5,
        "ai_visibility": 60,
        "technical_setup": 80,
        "content_structure": 75,
        "details": {
            "technical_setup": {
                "components": [
                    {
                        "title": "Crawler access",
                        "score": 90,
                        "weight_pct": 25,
                        "finding_summary": "Most AI crawlers are allowed.",
                    }
                ]
            },
            "content_structure": {"components": []},
        },
    }
    monkeypatch.setattr(dashboards.geo, "load_integrated_scores", lambda _: integrated)
    monkeypatch.setattr(
        dashboards.geo,
        "load_competitive_comparison",
        lambda _: {
            "rows": [
                {
                    "name": "Example",
                    "overall": 72.5,
                    "ai_visibility": 60,
                    "technical_setup": 80,
                    "content_quality": 75,
                    "is_primary": True,
                }
            ]
        },
    )

    from api import prompt_performance_metrics

    monkeypatch.setattr(
        prompt_performance_metrics,
        "read_metrics_file",
        lambda _: {
            "default_locale_key": "uk-en",
            "pss_rows": [
                {"product_or_service": "Delivery", "prompts": ["Best delivery app"]},
            ],
            "locales": {
                "uk-en": {
                    "live_probe": {
                        "per_prompt": [
                            {
                                "prompt": "Best delivery app",
                                "has_response_gemini": True,
                                "has_response_google_aio": True,
                                "list_metrics": {
                                    "visibility_pct": 50,
                                    "response_count": 3,
                                    "sentiment": "positive",
                                    "platforms_responded": ["gemini", "google_aio"],
                                    "platform_metrics": {
                                        "gemini": {
                                            "response_count": 2,
                                            "brand_mention_count": 1,
                                            "avg_position": 2.0,
                                        },
                                        "google_aio": {
                                            "response_count": 1,
                                            "brand_mention_count": 0,
                                            "avg_position": None,
                                        },
                                    },
                                },
                            }
                        ]
                    }
                }
            },
        },
    )
    monkeypatch.setattr(
        prompt_performance_metrics,
        "build_citations_view_payload",
        lambda *_args, **_kwargs: {
            "top_cited_sites": [{"domain": "example.org", "frequency": 4}]
        },
    )

    payload = dashboards.build_dashboard_data(audit_dir)
    by_id = {dataset["id"]: dataset for dataset in payload["datasets"]}

    assert by_id["scores"]["rows"][0] == {"pillar": "Overall", "score": 72.5}
    assert by_id["findings"]["rows"][0]["title"] == "Crawler access"
    prompt_rows = by_id["prompts"]["rows"]
    assert len(prompt_rows) == 2
    assert prompt_rows[0]["topic"] == "Delivery"
    assert prompt_rows[0]["platform"] == "gemini"
    assert prompt_rows[0]["channel"] == "Chatbot"
    assert prompt_rows[0]["visibility_pct"] == 50.0
    assert prompt_rows[1]["platform"] == "google_aio"
    assert prompt_rows[1]["channel"] == "AI Overview"
    assert prompt_rows[1]["visibility_pct"] == 0.0
    assert by_id["prompts"]["filter_options"] == {
        "topics": ["Delivery"],
        "platforms": ["gemini", "google_aio"],
        "channels": ["chatbot", "ai_overview"],
    }
    assert "topic" in by_id["prompts"]["dimension_fields"]
    assert "platform" in by_id["prompts"]["dimension_fields"]
    assert by_id["citations"]["rows"] == [{"domain": "example.org", "citations": 4}]
    assert by_id["competitors"]["rows"][0]["name"] == "Example"
    assert not any(
        key.endswith("_response")
        for dataset in payload["datasets"]
        for row in dataset["rows"]
        for key in row
    )


def test_widget_accepts_prompt_filters_and_topic_platform_dimensions() -> None:
    widget = dashboards.WidgetConfig(
        id="by-topic",
        title="By topic",
        dataset="prompts",
        visualization="bar",
        dimension="topic",
        metric="visibility_pct",
        filter_topic="Delivery",
        filter_channel="chatbot",
        filter_platform="gemini",
    )
    assert widget.dimension == "topic"
    assert widget.filter_channel == "chatbot"

    with pytest.raises(ValidationError):
        dashboards.WidgetConfig(
            id="bad-platform",
            title="Bad platform",
            dataset="prompts",
            visualization="bar",
            dimension="platform",
            metric="visibility_pct",
            filter_platform="bing",
        )

    with pytest.raises(ValidationError):
        dashboards.WidgetConfig(
            id="bad-filter",
            title="Bad filter",
            dataset="scores",
            visualization="bar",
            dimension="pillar",
            metric="score",
            filter_topic="Delivery",
        )


def test_default_prompt_widget_still_validates_after_grain_change() -> None:
    config = dashboards._default_config()
    prompt_widget = next(widget for widget in config.widgets if widget.dataset == "prompts")
    assert prompt_widget.dimension == "prompt"
    assert prompt_widget.filter_topic == ""
    assert prompt_widget.filter_channel == ""
    assert prompt_widget.filter_platform == ""


def test_build_dashboard_data_handles_incomplete_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _audit_dir(tmp_path)
    monkeypatch.setattr(
        dashboards.geo,
        "load_integrated_scores",
        lambda _: {
            "overall": None,
            "ai_visibility": None,
            "technical_setup": 40,
            "content_structure": None,
            "details": {},
        },
    )
    monkeypatch.setattr(
        dashboards.geo,
        "load_competitive_comparison",
        lambda _: {"rows": [], "has_comparison": False},
    )
    from api import prompt_performance_metrics

    monkeypatch.setattr(prompt_performance_metrics, "read_metrics_file", lambda _: None)

    payload = dashboards.build_dashboard_data(audit_dir)
    by_id = {dataset["id"]: dataset for dataset in payload["datasets"]}

    assert by_id["scores"]["rows"] == [{"pillar": "Technical setup", "score": 40.0}]
    assert by_id["prompts"]["rows"] == []
    assert by_id["citations"]["rows"] == []
    assert by_id["competitors"]["rows"] == []
    assert by_id["findings"]["rows"] == []


def test_write_access_requires_owner_when_auth_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _audit_dir(tmp_path)
    request = object()
    monkeypatch.setattr(dashboards, "auth_enabled", lambda: True)
    monkeypatch.setattr(
        dashboards, "current_user", lambda _: {"email": "owner@example.com", "name": "Owner"}
    )
    monkeypatch.setattr(
        dashboards, "_audit_owner_emails", lambda _: {"someone-else@example.com"}
    )

    with pytest.raises(HTTPException) as exc:
        dashboards._require_write_access(request, audit_dir)  # type: ignore[arg-type]

    assert exc.value.status_code == 404


def test_write_access_allows_unowned_legacy_audit_in_development(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_dir = _audit_dir(tmp_path)
    request = object()
    monkeypatch.setattr(dashboards, "auth_enabled", lambda: True)
    monkeypatch.setattr(
        dashboards, "current_user", lambda _: {"email": "owner@example.com", "name": "Owner"}
    )
    monkeypatch.setattr(dashboards, "_audit_owner_emails", lambda _: set())
    monkeypatch.setenv("APP_ENV", "development")

    assert (
        dashboards._require_write_access(request, audit_dir)  # type: ignore[arg-type]
        == "owner@example.com"
    )


def test_dashboard_feature_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKSHOP_DASHBOARD_BUILDER_ENABLED", "false")
    assert dashboards.workshop_dashboard_enabled() is False
    monkeypatch.setenv("WORKSHOP_DASHBOARD_BUILDER_ENABLED", "true")
    assert dashboards.workshop_dashboard_enabled() is True
