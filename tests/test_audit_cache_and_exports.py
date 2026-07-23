"""Tests for audit JSON TTL cache and HTML export precompute helpers."""

from __future__ import annotations

import json
from pathlib import Path

import api.audit_json_cache as cache
import api.export_precompute as precompute


def test_audit_json_cache_hit_and_invalidate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUDIT_JSON_CACHE_TTL_SEC", "60")
    cache.clear_all()

    path = tmp_path / "audit_summary.json"
    path.write_text(json.dumps({"score": 1}), encoding="utf-8")

    first = cache.load_json_cached(path)
    assert first == {"score": 1}

    path.write_text(json.dumps({"score": 2}), encoding="utf-8")
    # Still cached until invalidate
    assert cache.load_json_cached(path) == {"score": 1}

    cache.invalidate_audit(tmp_path)
    assert cache.load_json_cached(path) == {"score": 2}


def test_precomputed_html_freshness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EXPORT_PRECOMPUTE_HTML", "1")
    monkeypatch.setenv("EXPORT_PRECOMPUTE_PDF", "0")

    summary = tmp_path / "audit_summary.json"
    probe = tmp_path / "prompt_performance_live_probe.json"
    summary.write_text("{}", encoding="utf-8")
    probe.write_text(json.dumps({"live_probe": {"per_prompt": []}}), encoding="utf-8")

    section_path = precompute.section_html_path(tmp_path, "summary")
    section_path.parent.mkdir(parents=True, exist_ok=True)
    section_path.write_text("<html>summary</html>", encoding="utf-8")
    full_path = precompute.full_report_html_path(tmp_path)
    full_path.write_text("<html>full</html>", encoding="utf-8")

    sources = {
        "audit_summary": summary.stat().st_mtime,
        "live_probe": probe.stat().st_mtime,
        "aio_probe": None,
    }
    (section_path.parent / "manifest.json").write_text(
        json.dumps({"sources": sources, "sections": ["summary"]}),
        encoding="utf-8",
    )

    assert precompute.precomputed_html_is_fresh(tmp_path, section="summary")
    assert precompute.read_precomputed_section_html(tmp_path, "summary") == "<html>summary</html>"

    # Touch probe → stale
    probe.write_text(json.dumps({"live_probe": {"per_prompt": [{"x": 1}]}}), encoding="utf-8")
    assert not precompute.precomputed_html_is_fresh(tmp_path, section="summary")
    assert precompute.read_precomputed_section_html(tmp_path, "summary") is None
