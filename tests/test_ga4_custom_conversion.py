from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from api.ai_impact import CreateAiImpactRunRequest, create_run
from api.ga4 import Ga4PropertyBody


def _load_export_module(monkeypatch):
    class Proto:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class StringFilter(Proto):
        class MatchType:
            EXACT = "EXACT"

    class Filter(Proto):
        pass

    Filter.StringFilter = StringFilter

    types_module = ModuleType("google.analytics.data_v1beta.types")
    for name, value in {
        "DateRange": Proto,
        "Dimension": Proto,
        "Metric": Proto,
        "RunReportRequest": Proto,
        "Filter": Filter,
        "FilterExpression": Proto,
    }.items():
        setattr(types_module, name, value)
    monkeypatch.setitem(sys.modules, "google.analytics", ModuleType("google.analytics"))
    monkeypatch.setitem(
        sys.modules,
        "google.analytics.data_v1beta",
        ModuleType("google.analytics.data_v1beta"),
    )
    monkeypatch.setitem(sys.modules, "google.analytics.data_v1beta.types", types_module)
    monkeypatch.setitem(
        sys.modules,
        "ga4_data_api",
        SimpleNamespace(ga4_log=lambda *_args, **_kwargs: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "ga4_fetch",
        SimpleNamespace(
            _ga4_client_with_scopes=lambda: object(),
            _paginate_run_report=lambda *_args, **_kwargs: [],
            _row_metric_int=lambda row, index: int(row.metric_values[index].value),
            normalize_ga4_api_date=lambda value: value,
            normalize_property_id=lambda value: str(value).removeprefix("properties/"),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "ga4_oauth",
        SimpleNamespace(
            _DEFAULT_CLI_TOKEN_PATH=Path("/tmp/token.json"),
            acquire_cli_credentials=lambda **_kwargs: object(),
            install_oauth_application_default_credentials=lambda *_args, **_kwargs: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "geo_app_env",
        SimpleNamespace(load_app_environment=lambda: None),
    )
    monkeypatch.setitem(sys.modules, "paths", SimpleNamespace(GA4_DAILY=Path("/tmp")))

    module_path = (
        Path(__file__).resolve().parents[1] / "research" / "ga4" / "ga4_channel_export.py"
    )
    spec = importlib.util.spec_from_file_location("ga4_channel_export_under_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(dimensions: list[str], metrics: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        dimension_values=[SimpleNamespace(value=value) for value in dimensions],
        metric_values=[SimpleNamespace(value=value) for value in metrics],
    )


def test_custom_conversion_uses_separate_event_count_query(monkeypatch) -> None:
    module = _load_export_module(monkeypatch)
    requests = []

    def fake_paginate(_client, request_factory, *, label):
        request = request_factory(0)
        requests.append(request)
        if label.startswith("daily_conversion_"):
            return [
                _row(
                    ["20260102", "chatgpt.com", "referral", "Referral", "generate_lead"],
                    ["3"],
                )
            ]
        return [_row(["20260102", "chatgpt.com", "referral", "Referral"], ["10"])]

    monkeypatch.setattr(module, "_paginate_run_report", fake_paginate)

    rows = module.fetch_daily_traffic_rows(
        "123",
        "2026-01-02",
        "2026-01-02",
        conversion_event_name="generate_lead",
    )

    assert rows == [("2026-01-02", "AI", 10, 3)]
    assert [metric.name for metric in requests[0].metrics] == ["sessions"]
    assert [dimension.name for dimension in requests[1].dimensions][-1] == "eventName"
    assert [metric.name for metric in requests[1].metrics] == ["eventCount"]
    assert requests[1].dimension_filter.filter.string_filter.value == "generate_lead"


def test_purchase_plus_custom_sums_ecommerce_and_event_count(monkeypatch) -> None:
    module = _load_export_module(monkeypatch)
    requests = []

    def fake_paginate(_client, request_factory, *, label):
        request = request_factory(0)
        requests.append((label, request))
        if label.startswith("daily_conversion_"):
            return [
                _row(
                    ["20260102", "chatgpt.com", "referral", "Referral", "generate_lead"],
                    ["4"],
                )
            ]
        return [_row(["20260102", "chatgpt.com", "referral", "Referral"], ["10", "2"])]

    monkeypatch.setattr(module, "_paginate_run_report", fake_paginate)

    rows = module.fetch_daily_traffic_rows(
        "123",
        "2026-01-02",
        "2026-01-02",
        conversion_event_name="purchase,generate_lead:Leads",
    )

    assert rows == [("2026-01-02", "AI", 10, 6)]
    assert [metric.name for metric in requests[0][1].metrics] == [
        "sessions",
        "ecommercePurchases",
    ]


def test_only_exact_purchase_name_uses_ecommerce_purchase_metric(monkeypatch) -> None:
    module = _load_export_module(monkeypatch)
    requested_metrics = []

    def fake_paginate(_client, request_factory, *, label):
        request = request_factory(0)
        requested_metrics.append((label, [metric.name for metric in request.metrics]))
        return []

    monkeypatch.setattr(module, "_paginate_run_report", fake_paginate)

    module.fetch_daily_traffic_rows(
        "123",
        "2026-01-02",
        "2026-01-02",
        conversion_event_name="Purchase",
    )

    assert requested_metrics[0][1] == ["sessions"]
    assert requested_metrics[1][1] == ["eventCount"]


def test_ai_impact_request_allows_optional_gsc() -> None:
    request = CreateAiImpactRunRequest(
        ga4_property_id="123",
        conversion_event_name="generate_lead",
    )

    assert request.gsc_site_url is None
    assert request.trends_upload_id is None
    assert request.conversion_event_name == "generate_lead"


def test_ai_impact_request_accepts_valid_manual_trends_upload_id() -> None:
    request = CreateAiImpactRunRequest(trends_upload_id="a" * 32)

    assert request.trends_upload_id == "a" * 32


def test_ga4_selection_defaults_and_validates_conversion_event() -> None:
    default = Ga4PropertyBody(property_id="123")
    custom = Ga4PropertyBody(property_id="123", conversion_event_name="generate_lead")

    assert default.conversion_event_name is None
    assert custom.conversion_event_name == "generate_lead"
    with pytest.raises(ValidationError):
        Ga4PropertyBody(property_id="123", conversion_event_name="invalid event")


def test_custom_conversion_rejects_unverified_local_panel() -> None:
    body = CreateAiImpactRunRequest(
        local_panel_path="/tmp/panel.csv",
        conversion_event_name="generate_lead",
    )
    request = Request({"type": "http", "method": "POST", "path": "/"})

    with pytest.raises(HTTPException, match="Custom conversion events require"):
        create_run(body, request)
