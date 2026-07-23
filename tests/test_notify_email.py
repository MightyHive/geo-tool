"""Tests for audit completion email notifications (Gmail API)."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import api.notify_email as notify_email
from api import audit_runner


def test_parse_from_with_display_name() -> None:
    assert notify_email._parse_from("GEO Audit <noreply@example.com>") == (
        "noreply@example.com",
        "GEO Audit",
    )


def test_parse_from_bare_email() -> None:
    assert notify_email._parse_from("noreply@example.com") == (
        "noreply@example.com",
        None,
    )


def test_report_url_uses_summary_path(monkeypatch) -> None:
    monkeypatch.setattr(notify_email, "REPORT_BASE_URL", "https://app.example.com")
    assert (
        notify_email._report_url("audit_output/example-com")
        == "https://app.example.com/report/audit_output/example-com/summary"
    )


def test_send_skipped_without_credentials(monkeypatch) -> None:
    monkeypatch.setattr(notify_email, "get_gmail_credentials", lambda **_kw: None)
    monkeypatch.setattr(notify_email, "_use_adc", lambda: False)
    ok = notify_email.send_audit_complete_email(
        to_email="user@example.com",
        brand_name="Acme",
        audit_dir="example-com",
    )
    assert ok is False


def test_send_via_gmail_builds_raw_and_calls_api(monkeypatch) -> None:
    monkeypatch.setattr(notify_email, "FROM_EMAIL", "GEO Audit <me@example.com>")
    monkeypatch.setattr(notify_email, "REPORT_BASE_URL", "https://app.example.com")

    send = MagicMock(return_value={"id": "msg-1"})
    service = MagicMock()
    service.users.return_value.messages.return_value.send.return_value.execute = send

    ok = notify_email.send_audit_complete_email(
        to_email="user@example.com",
        brand_name="Acme",
        audit_dir="example-com",
        service=service,
    )
    assert ok is True
    send.assert_called_once()
    kwargs = service.users.return_value.messages.return_value.send.call_args.kwargs
    assert kwargs["userId"] == "me"
    raw = kwargs["body"]["raw"]
    decoded = base64.urlsafe_b64decode(raw.encode()).decode()
    assert "To: user@example.com" in decoded
    assert "Subject: Acme GEO audit is ready" in decoded
    assert "https://app.example.com/report/example-com/summary" in decoded
    assert "From: GEO Audit <me@example.com>" in decoded


def test_finalize_sends_once_and_marks_status(monkeypatch, tmp_path: Path) -> None:
    audit_dir = tmp_path / "example-com"
    audit_dir.mkdir()
    (audit_dir / "audit_summary.json").write_text(
        json.dumps({"overall_score": 72, "base_url": "https://example.com"})
    )
    (audit_dir / "onboarding_context.json").write_text(
        json.dumps({"notification_email": "user@example.com", "brand_name_used": "Acme"})
    )

    monkeypatch.setattr(audit_runner.geo, "audit_dir_api_rel", lambda _p: "example-com")
    monkeypatch.setattr(audit_runner.geo, "load_audit_summary", lambda _p: {"overall_score": 72})
    monkeypatch.setattr(audit_runner.geo, "resolve_overall_score_for_audit", lambda _p: 72)
    monkeypatch.setattr(audit_runner.geo, "archive_add_run", lambda **_kw: None)

    send = MagicMock(return_value=True)
    monkeypatch.setattr(notify_email, "send_audit_complete_email", send)

    audit_runner.finalize_audit_run(
        audit_dir=audit_dir,
        primary="https://example.com",
        competitors=[],
        owner_email=None,
        notification_email="user@example.com",
        brand_name="Acme",
        progress_state=None,
    )
    assert send.call_count == 1
    status = json.loads((audit_dir / audit_runner.AUDIT_RUN_STATUS_FILE).read_text())
    assert status["status"] == "done"
    assert status["notification_email_sent"] is True
    assert status["notification_email"] == "user@example.com"

    audit_runner.finalize_audit_run(
        audit_dir=audit_dir,
        primary="https://example.com",
        competitors=[],
        owner_email=None,
        notification_email="user@example.com",
        brand_name="Acme",
        progress_state=None,
    )
    assert send.call_count == 1


def test_seed_persists_notification_email(tmp_path: Path) -> None:
    from api.geo_services import seed_audit_dir_from_wizard

    audit_dir = tmp_path / "example-com"
    seed_audit_dir_from_wizard(
        audit_dir,
        primary_url="https://example.com",
        brand_name="Acme",
        industry="SaaS",
        market_country="United Kingdom",
        market_country_code="GB",
        competitor_urls=[],
        products_rows=[],
        competitors_detail=[],
        notification_email="User@Example.com",
    )
    ob = json.loads((audit_dir / "onboarding_context.json").read_text())
    assert ob["notification_email"] == "user@example.com"
