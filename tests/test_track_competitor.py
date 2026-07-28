from __future__ import annotations

import json

from api import geo_services


def test_track_competitor_adds_detected_brand_to_config(tmp_path) -> None:
    config_path = tmp_path / "onboarding_context.json"
    config_path.write_text(json.dumps({
        "competitors_detail": [{
            "competitor_brand": "Existing",
            "competitor_website": "https://existing.example",
        }],
        "accepted_competitors": ["https://existing.example"],
    }))
    result = geo_services.track_competitor_config(
        tmp_path,
        name="Detected Brand",
        website="https://detected.example",
    )

    saved = json.loads(config_path.read_text())
    assert result["tracked"] is True
    assert saved["competitors_detail"][-1]["competitor_brand"] == "Detected Brand"
    assert saved["accepted_competitors"][-1] == "https://detected.example"


def test_track_competitor_is_idempotent(tmp_path) -> None:
    config_path = tmp_path / "onboarding_context.json"
    config_path.write_text(json.dumps({
        "competitors_detail": [{
            "competitor_brand": "Detected Brand",
            "competitor_website": "",
        }],
    }))
    result = geo_services.track_competitor_config(
        tmp_path,
        name="detected brand",
    )

    assert result["already_tracked"] is True
    assert len(json.loads(config_path.read_text())["competitors_detail"]) == 1
