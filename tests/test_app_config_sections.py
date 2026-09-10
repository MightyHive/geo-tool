from api import geo_services


def test_single_page_audits_is_in_workshop_navigation() -> None:
    sections = geo_services.app_config()["report_sections"]
    page_audits = next(section for section in sections if section["id"] == "single-page-audits")
    assert page_audits == {
        "id": "single-page-audits",
        "label": "Single-page audit",
        "group": "Workshop",
    }


def test_recommendations_is_available_in_overview_navigation() -> None:
    sections = geo_services.app_config()["report_sections"]
    recommendations = next(
        section for section in sections if section["id"] == "recommendations"
    )

    assert recommendations == {
        "id": "recommendations",
        "label": "Recommendations",
        "group": "Overview",
    }
    ids = [section["id"] for section in sections]
    assert ids.index("config") < ids.index("recommendations")


def test_dashboard_builder_section_follows_feature_flag(monkeypatch) -> None:
    monkeypatch.setenv("WORKSHOP_DASHBOARD_BUILDER_ENABLED", "true")
    enabled = geo_services.app_config()
    assert enabled["features"]["workshop_dashboard_builder"] is True
    assert any(section["id"] == "dashboard" for section in enabled["report_sections"])

    monkeypatch.setenv("WORKSHOP_DASHBOARD_BUILDER_ENABLED", "false")
    disabled = geo_services.app_config()
    assert disabled["features"]["workshop_dashboard_builder"] is False
    assert all(section["id"] != "dashboard" for section in disabled["report_sections"])
