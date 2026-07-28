"""Summary export should mirror the React Summary section (gauges + pillars)."""

from __future__ import annotations

import json
from pathlib import Path

from api.export_builders import build_summary_export, _svg_score_gauge


def test_svg_score_gauge_contains_ring() -> None:
    html = _svg_score_gauge(72.5, size=100, decimals=1)
    assert "73" in html
    assert "72.5" not in html
    assert "stroke-dasharray" in html
    assert "#00b894" in html or "#0984e3" in html


def test_build_summary_export_includes_gauges_and_pillars(tmp_path: Path, monkeypatch) -> None:
    summary = {
        "brand_name": "Acme",
        "overall_score": 64.0,
        "base_url": "https://acme.example",
    }
    (tmp_path / "audit_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (tmp_path / "executive_summary.json").write_text(
        json.dumps({
            "schema_version": 4,
            "paragraph_html": "Acme scores <strong>64</strong> out of 100 overall.",
            "key_findings": ["Visibility is strongest on Gemini."],
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "api.geo_services.load_integrated_scores",
        lambda _audit_dir: {
            "overall": 64.2,
            "ai_visibility": 58.0,
            "technical_setup": 71.0,
            "content_structure": 66.0,
            "prompt_metrics": {"visibility_pct": 42.0},
            "details": {"technical_setup": {"components": []}, "content_structure": {"components": []}},
        },
    )
    monkeypatch.setattr(
        "api.geo_services.load_audit_summary",
        lambda _audit_dir: summary,
    )

    html = build_summary_export(tmp_path)
    assert "Overall GEO Score" in html
    assert "pillar-grid" in html
    assert "AI Visibility" in html
    assert "Technical Setup" in html
    assert "Content Quality" in html
    assert "Executive summary" in html
    assert "Key Findings" in html
    assert "stroke-dasharray" in html
