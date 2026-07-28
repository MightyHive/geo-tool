"""Persist-on-write + slim GET for prompt-performance metrics."""

from __future__ import annotations

import json
from pathlib import Path

import api.prompt_performance as pp
import api.prompt_performance_metrics as metrics


def _write_minimal_audit(audit_dir: Path) -> None:
    (audit_dir / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://acme.example"}),
        encoding="utf-8",
    )
    onboarding = {
        "brand_name_used": "Acme",
        "brand_website_used": "https://acme.example",
        "geo_market_country": "Belgium",
        "geo_market_country_code": "BE",
        "products_and_services_rows": [
            {"product_or_service": "Widget", "prompts": ["best widget brand"]},
        ],
        "prompt_locales": [
            {
                "country": "Belgium",
                "country_code": "BE",
                "language": "en",
                "language_name": "English",
                "key": "BE:en",
                "label": "Belgium: English",
            }
        ],
    }
    (audit_dir / "onboarding_context.json").write_text(json.dumps(onboarding), encoding="utf-8")
    (audit_dir / "products_and_services.json").write_text(
        json.dumps({"rows": onboarding["products_and_services_rows"]}),
        encoding="utf-8",
    )


def _sample_live_probe() -> dict:
    return {
        "per_prompt": [
            {
                "prompt": "best widget brand",
                "gemini_response": "Acme is the best widget brand recommended today.",
                "openai_response": "Consider Acme for widgets.",
                "mention_scores_gemini": {
                    "brand_signal": 1,
                    "competitor_detail": {"RivalCo": 1},
                },
                "mention_scores_openai": {
                    "brand_signal": 1,
                    "competitor_detail": {},
                },
                "citations_gemini": [{"domain": "review.example", "url": "https://review.example/a"}],
            }
        ],
        "brand_match_tokens": ["Acme"],
        "active_platforms": ["gemini", "openai", "claude", "google_aio"],
        "aggregate": {"gemini": {"brand_visibility": 100}},
    }


def test_slim_row_omits_reply_bodies_and_keeps_scores() -> None:
    row = _sample_live_probe()["per_prompt"][0]
    slim = metrics.slim_per_prompt_row(row, index=0, locale_key="BE:en", brand_tokens=["Acme"])
    assert slim["replies_omitted"] is True
    assert "gemini_response" not in slim
    assert "openai_response" not in slim
    assert slim["mention_scores_gemini"]["brand_signal"] == 1
    assert slim["has_response_gemini"] is True
    assert slim["list_metrics"]["visibility_pct"] > 0
    assert slim["prompt_id"].startswith("BE:en|")


def test_save_live_probe_persists_metrics_file(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    live = _sample_live_probe()

    monkeypatch.setattr(
        "api.probe_platforms.sanitize_live_probe",
        lambda probe: probe,
    )
    monkeypatch.setattr(
        "prompt_suggest.recompute_live_probe_mention_scores",
        lambda probe, path_candidates=None: probe,
    )
    monkeypatch.setattr(
        "prompt_suggest.aggregate_live_sov",
        lambda rows, excluded=None: {"gemini": {"brand_visibility": 100}},
    )

    pp._save_live_probe(
        tmp_path,
        live,
        highlight_brand="Acme",
        highlight_comp_urls=[],
        highlight_comp_brands=[],
        locale_probes={
            "BE:en": {
                "locale": {"key": "BE:en", "label": "Belgium: English"},
                "live_probe": live,
                "prompts_probed": ["best widget brand"],
            }
        },
        default_locale_key="BE:en",
        persist_metrics=True,
    )

    metrics_path = tmp_path / metrics.METRICS_FILE
    assert metrics_path.is_file()
    doc = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert doc["version"] == metrics.METRICS_VERSION
    assert "BE:en" in doc["locales"]
    locale_live = doc["locales"]["BE:en"]["live_probe"]
    assert locale_live["replies_omitted"] is True
    assert "gemini_response" not in locale_live["per_prompt"][0]
    assert doc["overall_metrics"] is not None


def test_slim_get_reads_persisted_metrics_without_full_recompute(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    live = _sample_live_probe()
    monkeypatch.setattr("api.probe_platforms.sanitize_live_probe", lambda probe: probe)
    monkeypatch.setattr(
        "prompt_suggest.recompute_live_probe_mention_scores",
        lambda probe, path_candidates=None: probe,
    )
    monkeypatch.setattr(
        "prompt_suggest.aggregate_live_sov",
        lambda rows, excluded=None: {},
    )

    pp._save_live_probe(
        tmp_path,
        live,
        highlight_brand="Acme",
        highlight_comp_urls=[],
        highlight_comp_brands=[],
        locale_probes={
            "BE:en": {
                "locale": {"key": "BE:en", "label": "Belgium: English"},
                "live_probe": live,
                "prompts_probed": ["best widget brand"],
            }
        },
        default_locale_key="BE:en",
        persist_metrics=True,
    )

    calls = {"full": 0}
    real_full = pp._build_full_context_response

    def counting_full(audit_dir):
        calls["full"] += 1
        return real_full(audit_dir)

    monkeypatch.setattr(pp, "_build_full_context_response", counting_full)

    ctx = pp._build_context_response(tmp_path, use_persisted_metrics=True)
    assert ctx["metrics_from_cache"] is True
    assert ctx["replies_omitted"] is True
    assert ctx["live_probe"]["per_prompt"][0].get("replies_omitted") is True
    assert "gemini_response" not in ctx["live_probe"]["per_prompt"][0]
    # Fresh metrics → no full rebuild on GET.
    assert calls["full"] == 0


def test_lazy_backfill_on_first_slim_get(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    live = _sample_live_probe()
    monkeypatch.setattr("api.probe_platforms.sanitize_live_probe", lambda probe: probe)
    monkeypatch.setattr(
        "prompt_suggest.recompute_live_probe_mention_scores",
        lambda probe, path_candidates=None: probe,
    )
    monkeypatch.setattr(
        "prompt_suggest.aggregate_live_sov",
        lambda rows, excluded=None: {},
    )

    # Write probe without metrics file (simulate legacy audit).
    pp._save_live_probe(
        tmp_path,
        live,
        highlight_brand="Acme",
        highlight_comp_urls=[],
        highlight_comp_brands=[],
        locale_probes={
            "BE:en": {
                "locale": {"key": "BE:en"},
                "live_probe": live,
                "prompts_probed": ["best widget brand"],
            }
        },
        default_locale_key="BE:en",
        persist_metrics=False,
    )
    assert not (tmp_path / metrics.METRICS_FILE).is_file()

    ctx = pp._build_context_response(tmp_path, use_persisted_metrics=True)
    assert (tmp_path / metrics.METRICS_FILE).is_file()
    assert ctx["metrics_from_cache"] is True
    assert ctx["live_probe"]["per_prompt"][0]["replies_omitted"] is True


def test_find_full_prompt_row_returns_replies(tmp_path: Path, monkeypatch) -> None:
    _write_minimal_audit(tmp_path)
    live = _sample_live_probe()
    monkeypatch.setattr("api.probe_platforms.sanitize_live_probe", lambda probe: probe)
    monkeypatch.setattr(
        "prompt_suggest.recompute_live_probe_mention_scores",
        lambda probe, path_candidates=None: probe,
    )
    monkeypatch.setattr(
        "prompt_suggest.aggregate_live_sov",
        lambda rows, excluded=None: {},
    )
    pp._save_live_probe(
        tmp_path,
        live,
        highlight_brand="Acme",
        highlight_comp_urls=[],
        highlight_comp_brands=[],
        locale_probes={
            "BE:en": {
                "locale": {"key": "BE:en"},
                "live_probe": live,
                "prompts_probed": ["best widget brand"],
            }
        },
        default_locale_key="BE:en",
        persist_metrics=True,
    )
    slim = metrics.slim_per_prompt_row(
        live["per_prompt"][0], index=0, locale_key="BE:en", brand_tokens=["Acme"]
    )
    found = metrics.find_full_prompt_row(
        tmp_path, prompt_id=slim["prompt_id"], locale_key="BE:en"
    )
    assert found is not None
    assert found["gemini_response"].startswith("Acme")
    # Persist should have written a per-prompt reply shard.
    shard = metrics.reply_shard_path(tmp_path, "BE:en", slim["prompt_id"])
    assert shard.is_file()


def test_find_full_prompt_row_prefers_shard_over_live_probe(tmp_path: Path) -> None:
    live = _sample_live_probe()
    slim = metrics.slim_per_prompt_row(
        live["per_prompt"][0], index=0, locale_key="BE:en", brand_tokens=["Acme"]
    )
    pid = slim["prompt_id"]
    full = {
        **live["per_prompt"][0],
        "prompt_id": pid,
        "_locale_key": "BE:en",
        "gemini_response": "Acme from shard only",
        "replies_omitted": False,
    }
    metrics._write_reply_shard(metrics.reply_shard_path(tmp_path, "BE:en", pid), full)
    # No live probe file — shard must be enough.
    found = metrics.find_full_prompt_row(tmp_path, prompt_id=pid, locale_key="BE:en")
    assert found is not None
    assert found["gemini_response"] == "Acme from shard only"


def test_find_full_prompt_row_locale_scoped_no_spill(tmp_path: Path, monkeypatch) -> None:
    """When a locale is requested, do not return a row from another market."""
    _write_minimal_audit(tmp_path)
    live_be = _sample_live_probe()
    live_nl = {
        "per_prompt": [
            {
                "prompt": "best widget brand",
                "gemini_response": "Dutch Acme reply should not leak.",
                "openai_response": "NL only",
                "mention_scores_gemini": {"brand_signal": 1},
                "citations_gemini": [{"domain": "nl.example", "url": "https://nl.example"}],
            }
        ],
        "brand_match_tokens": ["Acme"],
        "active_platforms": ["gemini", "openai"],
    }
    monkeypatch.setattr("api.probe_platforms.sanitize_live_probe", lambda probe: probe)
    monkeypatch.setattr(
        "prompt_suggest.recompute_live_probe_mention_scores",
        lambda probe, path_candidates=None: probe,
    )
    monkeypatch.setattr(
        "prompt_suggest.aggregate_live_sov",
        lambda rows, excluded=None: {},
    )
    # Skip metrics/shards so we exercise the merged live-probe path only.
    pp._save_live_probe(
        tmp_path,
        live_be,
        highlight_brand="Acme",
        highlight_comp_urls=[],
        highlight_comp_brands=[],
        locale_probes={
            "BE:en": {"locale": {"key": "BE:en"}, "live_probe": live_be},
            "NL:nl": {"locale": {"key": "NL:nl"}, "live_probe": live_nl},
        },
        default_locale_key="BE:en",
        persist_metrics=False,
    )
    nl_id = metrics.prompt_id_for(0, "best widget brand", "NL:nl")
    # Asking for BE must not return the NL row even if prompt text matches.
    missing = metrics.find_full_prompt_row(
        tmp_path, prompt_id=nl_id, locale_key="BE:en"
    )
    assert missing is None
    found_nl = metrics.find_full_prompt_row(
        tmp_path, prompt_id=nl_id, locale_key="NL:nl"
    )
    assert found_nl is not None
    assert "Dutch Acme" in found_nl["gemini_response"]


def test_find_full_prompt_row_uses_locale_artifact(tmp_path: Path) -> None:
    from api.prompt_jobs import locale_probe_artifact_path, _write_json

    live = _sample_live_probe()
    live["per_prompt"][0]["gemini_response"] = "Acme from locale artifact"
    _write_json(
        locale_probe_artifact_path(tmp_path, "BE:en"),
        {"locale": {"key": "BE:en"}, "live_probe": live},
    )
    pid = metrics.prompt_id_for(0, "best widget brand", "BE:en")
    found = metrics.find_full_prompt_row(tmp_path, prompt_id=pid, locale_key="BE:en")
    assert found is not None
    assert found["gemini_response"] == "Acme from locale artifact"
    # Cold hit should backfill a shard for the next open.
    assert metrics.reply_shard_path(tmp_path, "BE:en", pid).is_file()


def test_load_prompt_visibility_metrics_prefers_persisted(tmp_path: Path, monkeypatch) -> None:
    from api.geo_services import load_prompt_visibility_metrics

    _write_minimal_audit(tmp_path)
    live = _sample_live_probe()
    monkeypatch.setattr("api.probe_platforms.sanitize_live_probe", lambda probe: probe)
    monkeypatch.setattr(
        "prompt_suggest.recompute_live_probe_mention_scores",
        lambda probe, path_candidates=None: probe,
    )
    monkeypatch.setattr(
        "prompt_suggest.aggregate_live_sov",
        lambda rows, excluded=None: {},
    )
    pp._save_live_probe(
        tmp_path,
        live,
        highlight_brand="Acme",
        highlight_comp_urls=[],
        highlight_comp_brands=[],
        locale_probes={
            "BE:en": {
                "locale": {"key": "BE:en"},
                "live_probe": live,
                "prompts_probed": ["best widget brand"],
            }
        },
        default_locale_key="BE:en",
        persist_metrics=True,
    )
    result = load_prompt_visibility_metrics(tmp_path)
    assert result is not None
    assert "visibility_pct" in result
    assert result["prompt_count"] >= 1
