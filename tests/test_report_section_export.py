from __future__ import annotations

from api.export_builders import FULL_REPORT_SECTIONS, SECTION_BUILDERS
from api.html_service import resolve_export_section


def test_resolve_export_section_maps_ui_aliases() -> None:
    assert resolve_export_section("competitor-comparison") == "competitor-comparison"
    assert resolve_export_section("competitors") == "competitor-comparison"
    assert resolve_export_section("ai-visibility-overview") == "ai-visibility-overview"
    assert resolve_export_section("eeat-signals") == "eeat-signals"
    assert resolve_export_section("content-eeat") == "eeat-signals"
    assert resolve_export_section("platform-readiness") == "platform-readiness"
    assert resolve_export_section("reddit-citations") == "reddit-citations"
    assert resolve_export_section("reddit-insights") == "reddit-citations"
    assert resolve_export_section("youtube-citations") == "youtube-citations"
    assert resolve_export_section("youtube-insights") == "youtube-citations"
    assert resolve_export_section("prompts") == "prompts"
    assert resolve_export_section("prompt_performance") == "prompts"
    assert resolve_export_section("crawler-access") == "crawler-access"
    assert resolve_export_section("technical") == "crawler-access"
    assert resolve_export_section("citability") == "citability"
    assert resolve_export_section("ai-visibility") == "citability"
    assert resolve_export_section("ai-traffic-dashboard") == "ai-traffic-dashboard"
    assert resolve_export_section("ga4-traffic") == "ai-traffic-dashboard"
    assert resolve_export_section("config") is None
    assert resolve_export_section("samples") is None
    assert resolve_export_section("sample-scripts") is None
    assert resolve_export_section("") is None


def test_full_report_excludes_workshop_includes_citation_insights() -> None:
    ids = [section_id for section_id, _ in FULL_REPORT_SECTIONS]
    assert "samples" not in ids
    assert "sample-scripts" not in ids
    assert "content-outline" not in ids
    assert "content-outline-generator" not in ids
    assert "reddit-citations" in ids
    assert "youtube-citations" in ids
    assert "platform-readiness" in ids
    assert "crawler-access" in ids
    # Reddit/YouTube sit with AI visibility sections, before technical
    assert ids.index("citations") < ids.index("reddit-citations") < ids.index("technical-overview")


def test_section_builders_cover_full_report_react_pages() -> None:
    for section_id, _ in FULL_REPORT_SECTIONS:
        if section_id == "ai-traffic-dashboard":
            continue
        assert section_id in SECTION_BUILDERS, f"missing builder for {section_id}"
