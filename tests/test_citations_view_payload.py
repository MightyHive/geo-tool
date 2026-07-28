"""Citations slim endpoint aggregates without shipping per_prompt."""

from __future__ import annotations

from pathlib import Path

from api.prompt_performance_metrics import (
    CITATIONS_TOP_SITES,
    CITATIONS_TOP_URLS,
    _strip_runs,
    build_citations_view_payload,
    sanitize_slim_runs_inplace,
)


def test_strip_runs_omits_citations() -> None:
    slim = _strip_runs(
        {
            "gemini": [
                {
                    "run_index": 0,
                    "citations": [{"url": "https://news.example/a", "domain": "news.example"}],
                    "mention_scores": {"brand_signal": 1},
                    "response": "hello",
                }
            ]
        }
    )
    assert slim is not None
    assert "citations" not in slim["gemini"][0]
    assert slim["gemini"][0]["has_response"] is True


def test_sanitize_drops_persisted_run_citations() -> None:
    live = {
        "per_prompt": [
            {
                "runs": {
                    "openai": [{"citations": [{"url": "https://x.com"}], "mention_scores": {}}]
                }
            }
        ]
    }
    sanitize_slim_runs_inplace(live)
    assert "citations" not in live["per_prompt"][0]["runs"]["openai"][0]


def test_citations_view_filters_brand_and_vendors() -> None:
    metrics = {
        "brand_name": "Acme",
        "brand_site_url": "https://acme.example",
        "prompt_count": 2,
        "default_locale_key": "GB:en",
        "prompt_locales": [
            {
                "key": "GB:en",
                "label": "UK",
                "country": "United Kingdom",
                "country_code": "GB",
                "language": "en",
            }
        ],
        "competitors": [],
        "primary_market": {"country": "United Kingdom", "country_id": "GB"},
        "locale_spread": [{"key": "GB:en", "prompt_count": 1}],
        "locales": {
            "GB:en": {
                "live_probe": {
                    "per_prompt": [
                        {
                            "prompt": "best widget",
                            "citations_gemini": [
                                {
                                    "domain": "review.example",
                                    "url": "https://review.example/guide",
                                    "title": "Guide",
                                },
                                {
                                    "domain": "acme.example",
                                    "url": "https://acme.example/about",
                                },
                                {
                                    "domain": "amazon.co.uk",
                                    "url": "https://amazon.co.uk/dp/1",
                                },
                                {
                                    "domain": "news.example",
                                    "url": "https://news.example/",
                                },
                            ],
                            "mention_scores_gemini": {"brand_signal": 1, "competitor_detail": {}},
                        }
                    ]
                }
            }
        },
    }
    payload = build_citations_view_payload(metrics, locale_key="GB:en")
    domains = {s["domain"] for s in payload["top_cited_sites"]}
    urls = {u["url"] for u in payload["top_cited_urls"]}
    assert "review.example" in domains
    assert "acme.example" not in domains
    assert "amazon.co.uk" not in domains
    assert "https://review.example/guide" in urls
    # Homepage-only URL excluded from Top URLs
    assert "https://news.example/" not in urls
    assert "per_prompt" not in payload


def test_citations_view_caps_top_sites_and_urls() -> None:
    citations = []
    for i in range(40):
        citations.append(
            {
                "domain": f"site{i}.example",
                "url": f"https://site{i}.example/article/{i}",
            }
        )
    # Heavier counts for lower indices so sort order is deterministic.
    rows = []
    for weight in range(5, 0, -1):
        rows.append(
            {
                "prompt": f"prompt-{weight}",
                "citations_gemini": citations[: weight * 8],
                "mention_scores_gemini": {"brand_signal": 0, "competitor_detail": {}},
            }
        )
    metrics = {
        "brand_name": "Acme",
        "brand_site_url": "https://acme.example",
        "prompt_count": len(rows),
        "default_locale_key": "GB:en",
        "prompt_locales": [{"key": "GB:en", "label": "UK", "country_code": "GB", "language": "en"}],
        "competitors": [],
        "primary_market": {"country": "United Kingdom", "country_id": "GB"},
        "locale_spread": [{"key": "GB:en", "prompt_count": len(rows)}],
        "locales": {"GB:en": {"live_probe": {"per_prompt": rows}}},
        "aio_probe": {
            "per_prompt": [
                {
                    "prompt": "aio",
                    "citations": [
                        {"domain": f"aio{i}.example", "url": f"https://aio{i}.example/p/{i}"}
                        for i in range(45)
                    ],
                }
            ]
        },
    }
    payload = build_citations_view_payload(metrics, locale_key="GB:en")
    assert CITATIONS_TOP_SITES == 15
    assert CITATIONS_TOP_URLS == 30
    assert len(payload["top_cited_sites"]) == CITATIONS_TOP_SITES
    assert len(payload["top_cited_urls"]) == CITATIONS_TOP_URLS
    assert len(payload["aio_cited_urls"]) == CITATIONS_TOP_URLS
    # Highest-frequency domains first
    assert payload["top_cited_sites"][0]["domain"] == "site0.example"
    assert payload["top_cited_urls"][0]["url"] == "https://site0.example/article/0"

    overall = build_citations_view_payload(metrics, locale_key="__overall__")
    assert len(overall["top_cited_sites"]) <= CITATIONS_TOP_SITES
    assert len(overall["top_cited_urls"]) <= CITATIONS_TOP_URLS


def test_citations_view_cache_serves_without_rescanning(tmp_path: Path) -> None:
    from api.prompt_performance_metrics import (
        citations_view_path,
        get_or_build_citations_view_payload,
        serve_citations_view_if_fresh,
        write_citations_view_file,
        write_metrics_file,
    )

    metrics = {
        "version": 1,
        "probe_mtime": 100.0,
        "brand_name": "Acme",
        "brand_site_url": "https://acme.example",
        "prompt_count": 1,
        "default_locale_key": "GB:en",
        "prompt_locales": [{"key": "GB:en", "label": "UK", "country_code": "GB", "language": "en"}],
        "competitors": [],
        "primary_market": {"country": "United Kingdom", "country_id": "GB"},
        "locale_spread": [{"key": "GB:en", "prompt_count": 1}],
        "locales": {
            "GB:en": {
                "live_probe": {
                    "per_prompt": [
                        {
                            "prompt": "best widget",
                            "citations_gemini": [
                                {
                                    "domain": "review.example",
                                    "url": "https://review.example/guide",
                                    "title": "Guide",
                                }
                            ],
                            "mention_scores_gemini": {"brand_signal": 0, "competitor_detail": {}},
                        }
                    ]
                }
            }
        },
    }
    write_metrics_file(tmp_path, metrics)
    assert citations_view_path(tmp_path).is_file()

    hot = serve_citations_view_if_fresh(tmp_path, locale_key=None)
    assert hot is not None
    assert hot["locale_key"] in {"GB:en", "__overall__"}
    assert any(s["domain"] == "review.example" for s in hot["top_cited_sites"])

    again = get_or_build_citations_view_payload(tmp_path, metrics, locale_key="GB:en")
    assert again["locale_key"] == "GB:en"
    assert len(again["top_cited_sites"]) >= 1

    # Explicit rewrite still produces a readable hot-path file.
    write_citations_view_file(tmp_path, metrics)
    assert serve_citations_view_if_fresh(tmp_path, locale_key="GB:en") is not None
