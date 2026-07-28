from __future__ import annotations

from api import geo_services


def test_ga4_section_contains_only_requested_direct_ai_modules(monkeypatch) -> None:
    create_report = geo_services.load_create_report()
    monkeypatch.setattr(create_report, "_ga4_apply_display_policy", lambda value: value)
    ga4 = {
        "has_ai_channel": False,
        "conversion_rate": {
            "all_channels": {"rate_pct": 3.6, "sessions": 1000, "purchases": 36},
            "ai": {
                "rate_pct": 4.2,
                "sessions": 100,
                "purchases": 4,
                "mode": "known_ai_sources",
            },
        },
        "monthly_sessions": [{
            "year_month": "202601",
            "total_sessions": 1000,
            "ai_sessions": 100,
        }],
        "monthly_ai_sessions_by_source": {
            "mode": "known_ai_sources",
            "source_order": ["chatgpt.com"],
            "months": [{
                "year_month": "202601",
                "by_source": {"chatgpt.com": 100},
            }],
        },
        "monthly_ai_revenue_pct": [{
            "year_month": "202601",
            "ai_pct_of_revenue": 2.0,
        }],
        "source_medium_gaps": [{
            "session_source": "example",
            "sessions": 20,
        }],
    }

    rendered = create_report._ga4_section_html(ga4)

    assert "Conversion rate (average)" in rendered
    assert "Conversion rate for AI traffic" in rendered
    assert "All channels ·" not in rendered
    assert "Purchases ÷ sessions" not in rendered
    assert 'id="ga4Chart"' in rendered
    assert 'id="ga4AiSessionsBySourceChart"' in rendered
    assert 'id="ga4MonthlyRevenueChart"' not in rendered
    assert "Possible channel bucket gaps" not in rendered


def test_embed_renames_existing_ga4_heading() -> None:
    source = (
        '<body class="geo-report"><main class="container report-main-with-tabs">'
        '<div class="report-tab-panel" id="tab-panel-ga4-traffic">'
        '<header class="section-head" id="ga4-traffic-heading">'
        '<h2 class="section-title">GA4 — AI traffic</h2></header>'
        '<p class="section-lead">Commentary</p>'
        '<div class="ga4-conv-card"><p class="table-note">All channels · details</p></div>'
        '<table class="data-table" aria-label="Possible channel bucket gaps">'
        '<tbody><tr><td>unallocated.example</td><td>referral</td></tr></tbody></table>'
        '<div class="chart-panel"><canvas id="ga4Chart"></canvas></div>'
        '<div class="chart-panel"><canvas id="ga4AiSessionsBySourceChart"></canvas></div>'
        "</div><div class=\"report-tab-panel\" id=\"next\"></div></main></body>"
    )

    rendered = geo_services.prepare_report_html_for_embed(source)

    assert '<h2 class="section-title">Direct AI Traffic</h2>' in rendered
    assert "Sessions by AI platform over time" in rendered
    assert "Commentary" not in rendered
    assert "All channels · details" not in rendered
    assert "unallocated.example" not in rendered
    assert 'class="chart-panel ga4-chart-sessions"' in rendered
    assert 'class="chart-panel ga4-chart-by-source"' in rendered
