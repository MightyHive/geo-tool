from __future__ import annotations

import json

from api.geo_services import seed_audit_dir_from_wizard


def _seed(audit_dir, *, preserve_prompt_data: bool) -> None:
    seed_audit_dir_from_wizard(
        audit_dir,
        primary_url="https://example.com",
        brand_name="Example",
        industry="Software",
        market_country="United Kingdom",
        market_country_code="GB",
        competitor_urls=[],
        products_rows=[],
        competitors_detail=[],
        preserve_prompt_data=preserve_prompt_data,
    )


def test_config_rerun_preserves_prompt_artifacts(tmp_path) -> None:
    prompt_file = tmp_path / "prompt_performance_live_probe.json"
    report_file = tmp_path / "report.html"
    prompt_file.write_text('{"live_probe": {}}', encoding="utf-8")
    report_file.write_text("old report", encoding="utf-8")

    _seed(tmp_path, preserve_prompt_data=True)

    assert prompt_file.is_file()
    assert not report_file.exists()


def test_normal_audit_clears_prompt_artifacts(tmp_path) -> None:
    prompt_file = tmp_path / "prompt_performance_live_probe.json"
    prompt_file.write_text('{"live_probe": {}}', encoding="utf-8")

    _seed(tmp_path, preserve_prompt_data=False)

    assert not prompt_file.exists()


def test_seed_persists_primary_prompt_language(tmp_path) -> None:
    seed_audit_dir_from_wizard(
        tmp_path,
        primary_url="https://example.it",
        brand_name="Esempio",
        industry="Software",
        market_country="Italy",
        market_country_code="IT",
        additional_markets=[],
        competitor_urls=[],
        products_rows=[],
        competitors_detail=[],
        prompt_locales=[
            {
                "country": "Italy",
                "country_code": "IT",
                "language": "it",
                "language_name": "Italian",
            }
        ],
    )

    onboarding = json.loads(
        (tmp_path / "onboarding_context.json").read_text(encoding="utf-8")
    )
    assert onboarding["prompt_locales"][0]["key"] == "IT:it"
    assert onboarding["prompt_source_language"] == "it"
