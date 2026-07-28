from __future__ import annotations

import json

from api import reddit_insights, youtube_insights
from backend.citation_context import build_brand_tokens


def _write_probe(tmp_path, *, domain: str, url: str, response: str) -> None:
    (tmp_path / "prompt_performance_live_probe.json").write_text(
        json.dumps(
            {
                "live_probe": {
                    "per_prompt": [
                        {
                            "prompt": "example prompt",
                            "openai_response": response,
                            "citations_openai": [
                                {
                                    "url": url,
                                    "domain": domain,
                                    "title": domain,
                                    "brand_cited": False,
                                }
                            ],
                        }
                    ]
                }
            }
        )
    )


def test_youtube_extraction_repairs_stored_false_from_response_context(tmp_path) -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    _write_probe(
        tmp_path,
        domain="youtube.com",
        url=url,
        response=f"**Euro Car Parts** is shown in [this video]({url}).",
    )

    rows = youtube_insights._extract_youtube_urls(
        tmp_path,
        {},
        build_brand_tokens("Euro Car Parts"),
        [],
    )

    assert rows[0]["brand_mentioned"] is True
    assert rows[0]["citation_details"]
    assert rows[0]["citation_details"][0]["platform"] == "openai"
    assert "this video" in rows[0]["citation_details"][0]["citation_text"].lower() or url in rows[0]["citation_details"][0]["citation_text"]
    url = "https://www.reddit.com/r/cars/comments/abc/example/"
    _write_probe(
        tmp_path,
        domain="reddit.com",
        url=url,
        response=f"**Euro Car Parts** is discussed in [this thread]({url}).",
    )

    rows = reddit_insights._extract_reddit_posts(
        tmp_path,
        {},
        build_brand_tokens("Euro Car Parts"),
        [],
    )

    assert rows[0]["brand_mentioned"] is True


def test_reddit_extraction_uses_persisted_brand_aliases(tmp_path) -> None:
    url = "https://www.reddit.com/r/cars/comments/abc/example/"
    _write_probe(
        tmp_path,
        domain="reddit.com",
        url=url,
        response=f"**ECP** is discussed in [this thread]({url}).",
    )
    probe_path = tmp_path / "prompt_performance_live_probe.json"
    probe = json.loads(probe_path.read_text())
    probe["live_probe"]["brand_match_tokens"] = ["ECP"]
    probe_path.write_text(json.dumps(probe))

    rows = reddit_insights._extract_reddit_posts(
        tmp_path,
        {},
        build_brand_tokens("Euro Car Parts"),
        [],
    )

    assert rows[0]["brand_mentioned"] is True


def test_youtube_api_title_enrichment_sets_brand_mentioned(tmp_path, monkeypatch) -> None:
    url = "https://www.youtube.com/watch?v=abc123"
    (tmp_path / "audit_summary.json").write_text("{}")
    (tmp_path / "onboarding_context.json").write_text(
        json.dumps({"brand_name_used": "Euro Car Parts"})
    )
    _write_probe(
        tmp_path,
        domain="youtube.com",
        url=url,
        response=f"A useful [video]({url}).",
    )
    monkeypatch.setattr(youtube_insights.geo, "resolve_audit_dir", lambda _audit_id: tmp_path)
    monkeypatch.setattr(
        youtube_insights,
        "_fetch_video_details",
        lambda *_args: ({"abc123": {"yt_title": "Euro Car Parts battery fitting guide"}}, None),
    )
    monkeypatch.setenv("YOUTUBE_API_KEY", "test-key")

    result = youtube_insights.get_youtube_insights("audit")

    assert result["videos"][0]["brand_mentioned"] is True
    assert result["reviewed_prompt_count"] == 1
    assert result["videos"][0]["citing_prompt_count"] == 1
    assert result["videos"][0]["citation_details"]
    assert result["videos"][0]["citation_details"][0]["prompt"]
    assert result["videos"][0]["citation_details"][0]["platform"] == "openai"
    assert result["videos"][0]["citation_percentage"] == 100.0
