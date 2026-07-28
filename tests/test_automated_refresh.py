"""Tests for automated refresh eligibility and score history snapshots."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from api import automated_refresh, score_history


def test_is_automated_tracking_eligible_uses_latest_file_activity(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    summary_path = audit / "audit_summary.json"
    summary_path.write_text(
        json.dumps({"audit_label": "primary", "created_at": "2026-07-21T10:00:00+00:00"})
    )
    mtime = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc).timestamp()
    os.utime(summary_path, (mtime, mtime))

    assert automated_refresh.is_automated_tracking_eligible(audit) is False


def test_is_automated_tracking_eligible_recent_file_mtime(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    summary_path = audit / "audit_summary.json"
    summary_path.write_text(
        json.dumps({"audit_label": "primary", "created_at": "2026-07-21T10:00:00+00:00"})
    )
    old_mtime = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc).timestamp()
    os.utime(summary_path, (old_mtime, old_mtime))

    target = audit / "probe_history" / "2026-07-24.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}")
    recent_mtime = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc).timestamp()
    os.utime(target, (recent_mtime, recent_mtime))

    assert automated_refresh.is_automated_tracking_eligible(audit) is True


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


def test_get_score_history_entries_falls_back_to_history_files(tmp_path: Path) -> None:
    audit = tmp_path / "audit"
    audit.mkdir()
    hist_dir = audit / "score_history"
    hist_dir.mkdir()
    entry = {
        "date": "2026-07-24",
        "created_at": "2026-07-24T12:00:00Z",
        "source": "manual",
        "overall": 68.5,
        "ai_visibility": 71.0,
        "technical_setup": 64.0,
        "content_structure": 69.0,
        "competitors": [
            {"name": "Rival", "overall": 54.0, "ai_visibility": 50.0, "technical_setup": 53.0, "content_structure": 52.0},
        ],
    }
    (hist_dir / "2026-07-24.json").write_text(json.dumps(entry), encoding="utf-8")

    entries = score_history.get_score_history_entries(audit)

    assert len(entries) == 1
    assert entries[0]["date"] == "2026-07-24"
    assert entries[0]["overall"] == 68.5
    assert (audit / "score_history_index.json").is_file()
