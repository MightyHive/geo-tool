from api import geo_services


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
