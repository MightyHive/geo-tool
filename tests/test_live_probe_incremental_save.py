"""Multi-locale live probes must persist after each locale (not only at the end)."""

from __future__ import annotations

import json
from pathlib import Path

import api.prompt_performance as pp


def test_run_live_probe_job_saves_after_each_locale(tmp_path: Path, monkeypatch) -> None:
    onboarding = {
        "brand_name_used": "Acme",
        "brand_website_used": "https://www.acme.example/",
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
            },
            {
                "country": "Belgium",
                "country_code": "BE",
                "language": "fr",
                "language_name": "French",
                "key": "BE:fr",
                "label": "Belgium: French",
            },
        ],
    }
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps(onboarding), encoding="utf-8"
    )
    (tmp_path / "products_and_services.json").write_text(
        json.dumps({"rows": onboarding["products_and_services_rows"]}),
        encoding="utf-8",
    )

    save_calls: list[list[str]] = []

    def fake_execute(audit_dir, **kwargs):
        lang = str(kwargs.get("language") or "en")
        return {
            "per_prompt": [
                {
                    "prompt": f"prompt-{lang}",
                    "platforms": {},
                    "brand_mentioned": True,
                }
            ],
            "brand_match_tokens": ["Acme"],
        }

    real_save = pp._save_live_probe

    def tracking_save(audit_dir, live, **kwargs):
        keys = sorted((kwargs.get("locale_probes") or {}).keys())
        save_calls.append(keys)
        return real_save(audit_dir, live, **kwargs)

    monkeypatch.setattr(pp, "_execute_live_probe", fake_execute)
    monkeypatch.setattr(pp, "_save_live_probe", tracking_save)
    monkeypatch.setattr(
        "prompt_locales.regenerate_prompts_for_language",
        lambda prompts, **kwargs: [f"{p} ({kwargs.get('target_language')})" for p in prompts],
    )
    monkeypatch.setattr(
        "api.probe_history.save_probe_to_history",
        lambda *a, **k: None,
    )

    pp.run_live_probe_job(tmp_path, report_mode=True)

    # One persist per locale + one final persist after the loop.
    assert save_calls == [["BE:en"], ["BE:en", "BE:fr"], ["BE:en", "BE:fr"]]
    payload = json.loads(
        (tmp_path / "prompt_performance_live_probe.json").read_text(encoding="utf-8")
    )
    assert payload["default_locale_key"] == "BE:en"
    assert set(payload["locale_probes"]) == {"BE:en", "BE:fr"}
    assert payload["live_probe"]["per_prompt"][0]["prompt"] == "prompt-en"
