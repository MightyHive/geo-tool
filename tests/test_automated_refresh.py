"""Tests for automated refresh eligibility and score history snapshots."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from api import automated_refresh, score_history


def test_is_automated_tracking_eligible_cutoff(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "audit_summary.json").write_text(
        json.dumps({"audit_label": "primary", "created_at": "2026-07-22T10:00:00+00:00"})
    )
    assert automated_refresh.is_automated_tracking_eligible(audit) is True

    (audit / "audit_summary.json").write_text(
        json.dumps({"audit_label": "primary", "created_at": "2026-07-21T10:00:00+00:00"})
    )
    assert automated_refresh.is_automated_tracking_eligible(audit) is False


def test_rebuild_payload_always_follow_on_when_competitors_configured(
    tmp_path: Path, monkeypatch
) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "audit_summary.json").write_text(
        json.dumps({"audit_label": "primary", "base_url": "https://acme.com", "created_at": "2026-07-22T00:00:00Z"})
    )
    (audit / "onboarding_context.json").write_text(
        json.dumps(
            {
                "brand_name_used": "Acme",
                "brand_website_used": "https://acme.com",
                "competitors_detail": [
                    {"competitor_brand": "Rival", "competitor_website": "https://rival.com"}
                ],
            }
        )
    )
    monkeypatch.setattr(automated_refresh.geo, "load_audit_summary", lambda _d: json.loads((audit / "audit_summary.json").read_text()))
    monkeypatch.setattr(automated_refresh.geo, "audit_dir_api_rel", lambda d: d.name)

    payload = automated_refresh.rebuild_full_audit_crawl_payload(audit)
    assert payload["primary"] == "https://acme.com"
    assert payload["follow_on_competitor_crawl"] is True
    assert payload["body_dict"]["skip_prompt_probes"] is True
    assert payload["body_dict"]["follow_on_competitor_crawl"] is True
    assert payload["competitors"] == ["https://rival.com"]


def test_rebuild_payload_no_follow_on_without_competitors(tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    (audit / "audit_summary.json").write_text(
        json.dumps({"audit_label": "primary", "base_url": "https://acme.com", "created_at": "2026-07-22T00:00:00Z"})
    )
    (audit / "onboarding_context.json").write_text(
        json.dumps(
            {
                "brand_name_used": "Acme",
                "brand_website_used": "https://acme.com",
                "competitors_detail": [],
            }
        )
    )
    monkeypatch.setattr(automated_refresh.geo, "load_audit_summary", lambda _d: json.loads((audit / "audit_summary.json").read_text()))
    monkeypatch.setattr(automated_refresh.geo, "audit_dir_api_rel", lambda d: d.name)

    payload = automated_refresh.rebuild_full_audit_crawl_payload(audit)
    assert payload["follow_on_competitor_crawl"] is False
    assert payload["body_dict"]["follow_on_competitor_crawl"] is False
    assert payload["competitors"] == []


def test_save_score_snapshot_writes_index(tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()

    monkeypatch.setattr(
        score_history.geo,
        "load_integrated_scores",
        lambda _d: {
            "overall": 55.0,
            "ai_visibility": 60.0,
            "technical_setup": 50.0,
            "content_structure": 52.0,
        },
    )
    monkeypatch.setattr(
        score_history.geo,
        "load_competitive_comparison",
        lambda _d: {
            "rows": [
                {"is_primary": True, "name": "Acme", "overall": 55},
                {
                    "is_primary": False,
                    "name": "Rival",
                    "website": "https://rival.com",
                    "overall": 40,
                    "ai_visibility": 35,
                    "technical_setup": 42,
                    "content_quality": 44,
                },
            ]
        },
    )

    day = score_history.save_score_snapshot(audit, source="test")
    assert day == date.today().isoformat()
    entries = score_history.get_score_history_entries(audit)
    assert len(entries) == 1
    assert entries[0]["overall"] == 55.0
    assert entries[0]["competitors"][0]["name"] == "Rival"
