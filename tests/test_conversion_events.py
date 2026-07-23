"""Tests for multi-conversion-event parsing and GA4 fetch aggregation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.ai_impact import CreateAiImpactRunRequest
from api.conversion_events import (
    ConversionEvent,
    ConversionEventParseError,
    build_conversion_event_spec,
    event_names_input,
    format_conversion_heading,
    format_conversion_method_note,
    is_only_default_purchase,
    normalize_conversion_event_spec,
    parse_conversion_events,
    serialize_conversion_events,
    series_label_from_events,
)
from api.ga4 import Ga4PropertyBody
from api.main import RunAuditRequest


def test_parse_single_event_backward_compat() -> None:
    events = parse_conversion_events("purchase")
    assert events == [ConversionEvent(event="purchase")]
    assert serialize_conversion_events(events) == "purchase"
    assert format_conversion_heading(events) == "Purchases"
    assert format_conversion_method_note(events) == ""


def test_parse_comma_separated_events() -> None:
    events = parse_conversion_events("purchase, generate_lead")
    assert [e.event for e in events] == ["purchase", "generate_lead"]
    assert serialize_conversion_events(events) == "purchase,generate_lead"
    # Multi-event with no series label → neutral fallback (UI should prompt).
    assert format_conversion_heading(events) == "Conversions"
    assert format_conversion_method_note(events) == " (conversions = purchase, generate_lead)"


def test_parse_legacy_per_event_labels_collapse_to_series() -> None:
    events = parse_conversion_events("purchase:Purchases, generate_lead=Leads")
    assert [e.event for e in events] == ["purchase", "generate_lead"]
    assert series_label_from_events(events) == "Purchases + Leads"
    assert serialize_conversion_events(events) == "purchase:Purchases + Leads,generate_lead"
    assert format_conversion_heading(events) == "Purchases + Leads"
    assert format_conversion_method_note(events) == " (conversions = Purchases + Leads)"


def test_series_label_on_single_and_multi() -> None:
    single = parse_conversion_events("generate_lead:Sign-ups")
    assert format_conversion_heading(single) == "Sign-ups"
    assert event_names_input(single) == "generate_lead"

    multi = parse_conversion_events(
        build_conversion_event_spec("purchase,generate_lead", "Purchases + leads")
    )
    assert [e.event for e in multi] == ["purchase", "generate_lead"]
    assert format_conversion_heading(multi) == "Purchases + leads"
    assert serialize_conversion_events(multi) == "purchase:Purchases + leads,generate_lead"


def test_single_non_purchase_without_label_uses_event_id() -> None:
    events = parse_conversion_events("generate_lead")
    assert format_conversion_heading(events) == "generate_lead"
    assert format_conversion_method_note(events) == " (conversions = generate_lead)"


def test_parse_dedupes_and_defaults_empty() -> None:
    events = parse_conversion_events("purchase, purchase:Buys")
    assert events == [ConversionEvent(event="purchase", label=None)]
    assert parse_conversion_events("") == [ConversionEvent(event="purchase")]
    assert parse_conversion_events(None) == [ConversionEvent(event="purchase")]


def test_parse_rejects_invalid_event_tokens() -> None:
    with pytest.raises(ConversionEventParseError):
        parse_conversion_events("invalid event")
    with pytest.raises(ConversionEventParseError):
        parse_conversion_events("1bad")
    with pytest.raises(ConversionEventParseError):
        parse_conversion_events("ok: " + ("x" * 80))


def test_parse_accepts_hyphenated_event_names() -> None:
    events = parse_conversion_events(
        "appointment-confirmation-replace,appointment-confirmation-repair"
    )
    assert [e.event for e in events] == [
        "appointment-confirmation-replace",
        "appointment-confirmation-repair",
    ]
    assert (
        build_conversion_event_spec(
            "appointment-confirmation-replace,appointment-confirmation-repair",
            "appointment_confirmation",
        )
        == "appointment-confirmation-replace:appointment_confirmation,appointment-confirmation-repair"
    )
    body = Ga4PropertyBody(
        property_id="123",
        conversion_event_name=(
            "appointment-confirmation-replace:appointment_confirmation,"
            "appointment-confirmation-repair"
        ),
    )
    assert body.conversion_event_name == (
        "appointment-confirmation-replace:appointment_confirmation,"
        "appointment-confirmation-repair"
    )


def test_normalize_and_api_validators_accept_multi() -> None:
    assert (
        normalize_conversion_event_spec("purchase,generate_lead:Leads")
        == "purchase:Leads,generate_lead"
    )
    body = Ga4PropertyBody(
        property_id="123",
        conversion_event_name="purchase, generate_lead:Leads",
    )
    assert body.conversion_event_name == "purchase:Leads,generate_lead"
    run = CreateAiImpactRunRequest(
        conversion_event_name="sign_up=Sign-ups, purchase",
    )
    assert run.conversion_event_name == "sign_up:Sign-ups,purchase"
    audit = RunAuditRequest(
        brand_name="Acme",
        brand_website="https://example.com",
        ga4_conversion_event_name="purchase,generate_lead",
    )
    assert audit.ga4_conversion_event_name == "purchase,generate_lead"
    with pytest.raises(ValidationError):
        Ga4PropertyBody(property_id="123", conversion_event_name="bad event")


def test_is_only_default_purchase() -> None:
    assert is_only_default_purchase(parse_conversion_events("purchase"))
    assert not is_only_default_purchase(parse_conversion_events("generate_lead"))
    assert not is_only_default_purchase(parse_conversion_events("purchase,generate_lead"))


def test_fetch_sums_multiple_custom_conversion_events(monkeypatch) -> None:
    from tests.test_ga4_custom_conversion import _load_export_module, _row

    module = _load_export_module(monkeypatch)
    requests = []

    def fake_paginate(_client, request_factory, *, label):
        request = request_factory(0)
        requests.append((label, request))
        if "daily_conversion_generate_lead" in label:
            return [
                _row(
                    ["20260102", "chatgpt.com", "referral", "Referral", "generate_lead"],
                    ["3"],
                )
            ]
        if "daily_conversion_sign_up" in label:
            return [
                _row(
                    ["20260102", "chatgpt.com", "referral", "Referral", "sign_up"],
                    ["2"],
                )
            ]
        return [_row(["20260102", "chatgpt.com", "referral", "Referral"], ["10"])]

    monkeypatch.setattr(module, "_paginate_run_report", fake_paginate)

    rows = module.fetch_daily_traffic_rows(
        "123",
        "2026-01-02",
        "2026-01-02",
        conversion_event_name="generate_lead:Leads,sign_up:Sign-ups",
    )

    assert rows == [("2026-01-02", "AI", 10, 5)]
    conversion_labels = [label for label, _ in requests if label.startswith("daily_conversion_")]
    assert len(conversion_labels) == 2


def test_parse_conversion_event_names_strips_labels(monkeypatch) -> None:
    from tests.test_ga4_custom_conversion import _load_export_module

    module = _load_export_module(monkeypatch)
    assert module.parse_conversion_event_names("purchase:Buys, generate_lead") == [
        "purchase",
        "generate_lead",
    ]
    assert module.parse_conversion_event_names("") == ["purchase"]
