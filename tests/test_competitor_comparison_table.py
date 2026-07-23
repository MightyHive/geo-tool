from __future__ import annotations

import json

from api import geo_services


def test_comparison_brand_name_corrects_subdomain_labels() -> None:
    create_report = geo_services.load_create_report()

    assert (
        create_report._comparison_display_name("https://int.aestura.com", "Int")
        == "Aestura"
    )
    assert (
        create_report._comparison_display_name("https://www.cosrx.com", "COSRX")
        == "COSRX"
    )


def test_competitor_comparison_scores_only_table(tmp_path) -> None:
    create_report = geo_services.load_create_report()
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps(
            {
                "brand_name_used": "Example Brand",
                "competitors_detail": [
                    {
                        "competitor_brand": "Peer Brand",
                        "competitor_website": "https://peer.example",
                    }
                ],
            }
        )
    )
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "base_url": "https://example.com",
                        "audit_label": "primary",
                        "pages_scanned": 2,
                        "pages_http_200": 2,
                    },
                    {
                        "base_url": "https://peer.example",
                        "audit_label": "competitor_1",
                        "pages_scanned": 2,
                        "pages_http_200": 2,
                    },
                ]
            }
        )
    )

    rows = create_report.competitive_comparison_rows(
        comparison,
        "https://example.com",
        dict(create_report.DEFAULT_WEIGHTS),
    )
    assert len(rows) == 2
    primary = next(row for row in rows if row["is_primary"])
    peer = next(row for row in rows if not row["is_primary"])
    assert primary["name"] == "Example Brand"
    assert peer["name"] == "Peer Brand"
    for row in rows:
        for key in ("overall", "ai_visibility", "technical_setup", "content_quality"):
            assert isinstance(row[key], float)
        expected_overall = round(
            0.40 * float(row["ai_visibility"])
            + 0.30 * float(row["technical_setup"])
            + 0.30 * float(row["content_quality"]),
            1,
        )
        assert row["overall"] == expected_overall
    assert rows == sorted(rows, key=lambda row: float(row["overall"]), reverse=True)
    for key in (
        "ai_visibility_rationale",
        "technical_setup_rationale",
        "content_quality_rationale",
    ):
        rationale = primary[key]
        assert isinstance(rationale, dict)
        assert isinstance(rationale.get("summary"), str)
        assert isinstance(rationale.get("strengths"), list)
        assert isinstance(rationale.get("improvements"), list)
        assert isinstance(rationale.get("components"), list)
        assert rationale["components"], f"{key} should include criterion components"
        for component in rationale["components"]:
            assert component.get("key")
            assert component.get("title")
            assert "finding_summary" in component

    tech_keys = {c["key"] for c in peer["technical_setup_rationale"]["components"]}
    assert tech_keys == {"crawler_access", "citability", "platform_readiness"}
    content_keys = {c["key"] for c in peer["content_quality_rationale"]["components"]}
    assert content_keys == {
        "eeat",
        "structure_answerability",
        "schema_entity_markup",
        "brand_visibility_authority",
    }
    assert all(
        str(component.get("finding_summary") or "").strip()
        for component in peer["technical_setup_rationale"]["components"]
    )
    assert all(
        str(component.get("finding_summary") or "").strip()
        for component in peer["content_quality_rationale"]["components"]
    )

    _strengths, _improvements, rendered, _detail = (
        create_report.build_competitive_section(
            comparison,
            "https://example.com",
            dict(create_report.DEFAULT_WEIGHTS),
        )
    )

    assert 'class="cmp-card"' in rendered
    assert 'class="cmp-col-brand">Brand</th>' in rendered
    assert 'class="cmp-col-score">Overall</th>' in rendered
    assert 'class="cmp-col-score">AI visibility</th>' in rendered
    assert 'class="cmp-col-score">Technical setup</th>' in rendered
    assert 'class="cmp-col-score">Content quality</th>' in rendered
    assert rendered.count("cmp-score-value") == 8
    assert "Example Brand" in rendered
    assert "Peer Brand" in rendered
    assert "cmp-row--detail" not in rendered
    assert "cmp-toggle" not in rendered
    assert "performs better" not in rendered
    assert "cmp-insight-cell" not in rendered


def test_embed_refreshes_saved_competitor_table(monkeypatch, tmp_path) -> None:
    (tmp_path / "comparison.json").write_text("{}")
    (tmp_path / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://example.com"})
    )
    fake_report = type(
        "FakeReport",
        (),
        {
            "DEFAULT_WEIGHTS": {"ai_visibility": 40, "technical_setup": 30, "content_structure": 30},
            "build_competitive_section": staticmethod(
                lambda *_args: (
                    [],
                    [],
                    '<div class="cmp-card"><div class="cmp-card-body">'
                    '<table class="cmp-table"><tbody><tr><td>Fresh table</td></tr></tbody></table>'
                    "</div></div>",
                    "",
                )
            ),
        },
    )
    monkeypatch.setattr(geo_services, "load_create_report", lambda: fake_report)
    source = (
        '<div class="report-tab-panel" id="tab-panel-competitors">'
        '<p class="section-lead">Old copy</p>'
        '<table class="data-table cmp-table"><tbody><tr><td>Old table</td></tr></tbody></table>'
        '</div><div class="report-tab-panel" id="next"></div>'
    )

    rendered = geo_services.refresh_competitor_comparison_for_embed(source, tmp_path)

    assert "Fresh table" in rendered
    assert "Old table" not in rendered
    assert "competitor site crawls" in rendered


def test_load_competitive_comparison_api_shape(tmp_path) -> None:
    (tmp_path / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://example.com"})
    )
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps({"brand_name_used": "Example Brand", "competitors_detail": []})
    )
    (tmp_path / "comparison.json").write_text(
        json.dumps(
            {
                "rows": [
                    {"base_url": "https://example.com", "audit_label": "primary"},
                    {"base_url": "https://peer.example", "audit_label": "competitor_1"},
                ]
            }
        )
    )
    payload = geo_services.load_competitive_comparison(tmp_path)
    assert payload["has_comparison"] is True
    assert len(payload["rows"]) == 2
    assert any(row["is_primary"] is True for row in payload["rows"])
    assert all("overall" in row for row in payload["rows"])
    assert payload["rows"] == sorted(
        payload["rows"], key=lambda row: float(row["overall"]), reverse=True
    )
    peer = next(row for row in payload["rows"] if not row["is_primary"])
    assert peer["ai_visibility_rationale"].get("source") == "crawl_fallback"
    assert {c["key"] for c in peer["ai_visibility_rationale"]["components"]} >= {
        "brand_visibility",
        "share_of_voice",
    }


def test_load_competitive_comparison_primary_matches_integrated_scores(tmp_path, monkeypatch) -> None:
    (tmp_path / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://example.com"})
    )
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps({"brand_name_used": "Example Brand", "competitors_detail": []})
    )
    (tmp_path / "comparison.json").write_text(
        json.dumps(
            {
                "rows": [
                    {"base_url": "https://example.com", "audit_label": "primary"},
                    {"base_url": "https://peer.example", "audit_label": "competitor_1"},
                ]
            }
        )
    )
    monkeypatch.setattr(
        geo_services,
        "load_integrated_scores",
        lambda _audit_dir: {
            "ai_visibility": 61.5,
            "technical_setup": 72.0,
            "content_structure": 68.5,
            "prompt_metrics": None,
            "details": {"technical_setup": {"components": []}, "content_structure": {"components": []}},
        },
    )

    payload = geo_services.load_competitive_comparison(tmp_path)
    primary = next(row for row in payload["rows"] if row["is_primary"])
    assert primary["ai_visibility"] == 61.5
    assert primary["technical_setup"] == 72.0
    assert primary["content_quality"] == 68.5
    assert primary["overall"] == round(0.40 * 61.5 + 0.30 * 72.0 + 0.30 * 68.5, 1)


def test_competitor_ai_visibility_uses_matched_probe_metrics(tmp_path, monkeypatch) -> None:
    (tmp_path / "audit_summary.json").write_text(
        json.dumps({"base_url": "https://example.com"})
    )
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps(
            {
                "brand_name_used": "Example Brand",
                "competitors_detail": [
                    {
                        "competitor_brand": "Peer Brand",
                        "competitor_website": "https://peer.example",
                    }
                ],
            }
        )
    )
    (tmp_path / "comparison.json").write_text(
        json.dumps(
            {
                "rows": [
                    {"base_url": "https://example.com", "audit_label": "primary"},
                    {"base_url": "https://peer.example", "audit_label": "competitor_1"},
                ]
            }
        )
    )
    (tmp_path / "prompt_performance_live_probe.json").write_text(
        json.dumps(
            {
                "live_probe": {
                    "brand_name": "Example Brand",
                    "brand_match_tokens": ["example"],
                    "brand_site_url": "https://example.com",
                    "per_prompt": [
                        {
                            "prompt": "Best option?",
                            "gemini_response": "Example Brand and Peer Brand are options.",
                            "mention_scores_gemini": {
                                "brand_signal": 1,
                                "competitor_detail": {"peer.example": 2},
                            },
                            "openai_response": "Peer Brand leads here.",
                            "mention_scores_openai": {
                                "brand_signal": 0,
                                "competitor_detail": {"peer.example": 3},
                            },
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        geo_services,
        "load_integrated_scores",
        lambda _audit_dir: {
            "ai_visibility": 55.0,
            "technical_setup": 70.0,
            "content_structure": 65.0,
            "prompt_metrics": {
                "score": 55.0,
                "visibility_pct": 50.0,
                "visible_prompt_count": 1,
                "prompt_count": 2,
                "sov_performance_score": 0.0,
                "sov_rank": 2,
                "competitor_count": 1,
            },
            "details": {
                "technical_setup": {
                    "components": [
                        {
                            "key": "ai_crawler_report",
                            "title": "AI crawler access",
                            "score": 80,
                            "weight_pct": 25,
                            "finding_summary": "Brand crawlers look open.",
                        }
                    ]
                },
                "content_structure": {
                    "components": [
                        {
                            "key": "eeat",
                            "title": "E-E-A-T Signals",
                            "score": 70,
                            "weight_pct": 35,
                            "finding_summary": "Brand E-E-A-T is solid.",
                        }
                    ]
                },
            },
        },
    )

    payload = geo_services.load_competitive_comparison(tmp_path)
    peer = next(row for row in payload["rows"] if not row["is_primary"])
    primary = next(row for row in payload["rows"] if row["is_primary"])

    assert peer["ai_visibility_rationale"]["source"] == "prompt_visibility"
    assert peer["ai_visibility"] == round(0.60 * 100.0 + 0.40 * 100.0, 1)
    keys = {c["key"] for c in peer["ai_visibility_rationale"]["components"]}
    assert keys == {"brand_visibility", "share_of_voice"}
    brand_keys = {c["key"] for c in peer["ai_visibility_rationale"]["brand_components"]}
    assert brand_keys == {"brand_visibility", "share_of_voice"}
    tech_brand_keys = {c["key"] for c in peer["technical_setup_rationale"]["brand_components"]}
    assert tech_brand_keys == {"crawler_access"}
    assert peer["technical_setup_rationale"]["brand_components"][0]["finding_summary"] == (
        "Brand crawlers look open."
    )
    content_brand_keys = {c["key"] for c in peer["content_quality_rationale"]["brand_components"]}
    assert content_brand_keys == {"eeat"}
    assert peer["content_quality_rationale"]["brand_components"][0]["finding_summary"] == (
        "Brand E-E-A-T is solid."
    )
    assert primary["ai_visibility"] == 55.0
    assert payload["rows"] == sorted(
        payload["rows"], key=lambda row: float(row["overall"]), reverse=True
    )
