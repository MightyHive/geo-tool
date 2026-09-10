from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from brand_visibility_scan import (  # noqa: E402
    _content_mentions_brand_variants,
    _entity_titles_from_html,
    _title_matches_brand_variants,
    _youtube_channel_looks_real,
    same_as_platform_url,
    same_as_urls_by_platform,
    scan_brand_platforms,
)


def test_wikipedia_title_rejects_unrelated_phrase_overlap() -> None:
    variants = ["The Ordinary", "Theordinary"]

    assert not _title_matches_brand_variants("The Magic of Ordinary Days", variants)
    assert not _title_matches_brand_variants("Ordinary People", variants)


def test_wikipedia_title_accepts_exact_or_brand_qualified_variant() -> None:
    variants = ["The Ordinary", "Theordinary"]

    assert _title_matches_brand_variants("The Ordinary", variants)
    assert _title_matches_brand_variants("The Ordinary (brand)", variants)


def test_youtube_channel_requires_matching_entity_title() -> None:
    unrelated = '<meta property="og:title" content="Ordinary Adventures"><div>1M subscribers</div>'
    matching = '<meta property="og:title" content="The Ordinary - YouTube"><div>1M subscribers</div>'

    assert not _youtube_channel_looks_real(unrelated, ["The Ordinary", "Theordinary"])
    assert _youtube_channel_looks_real(matching, ["The Ordinary", "Theordinary"])


def test_entity_title_parser_removes_platform_suffix() -> None:
    body = "<title>The Ordinary | LinkedIn</title>"

    assert _entity_titles_from_html(body) == ["The Ordinary"]


def test_discussion_matching_requires_contiguous_brand_phrase() -> None:
    variants = ["The Ordinary"]

    assert _content_mentions_brand_variants("My review of The Ordinary products", variants)
    assert not _content_mentions_brand_variants("The magic of ordinary days", variants)


def test_same_as_classifies_relevant_platform_profiles() -> None:
    assert same_as_platform_url("https://en.wikipedia.org/wiki/The_Ordinary") == (
        "wikipedia",
        "https://en.wikipedia.org/wiki/The_Ordinary",
    )
    assert same_as_platform_url("https://www.youtube.com/@theordinary")[0] == "youtube"
    assert same_as_platform_url("https://www.reddit.com/r/TheOrdinary/")[0] == "reddit"
    assert same_as_platform_url("https://www.linkedin.com/company/the-ordinary")[0] == "linkedin"
    assert same_as_platform_url("https://www.youtube.com/watch?v=abc") is None
    assert same_as_platform_url("https://en.wikipedia.org/wiki/Special:Search") is None
    assert same_as_platform_url("https://www.instagram.com/theordinary/") is None


def test_same_as_urls_keep_first_profile_per_platform() -> None:
    mapped = same_as_urls_by_platform(
        [
            "https://en.wikipedia.org/wiki/The_Ordinary",
            "https://en.wikipedia.org/wiki/Deciem",
            "https://www.instagram.com/theordinary/",
        ]
    )
    assert mapped == {"wikipedia": "https://en.wikipedia.org/wiki/The_Ordinary"}


def test_scan_prefers_same_as_and_falls_back_to_search(monkeypatch) -> None:
    import brand_visibility_scan as bvs

    called: list[str] = []
    monkeypatch.setattr(bvs, "_same_as_url_still_live", lambda _url: True)
    monkeypatch.setattr(
        bvs,
        "_wikipedia_probe",
        lambda *_a, **_k: called.append("wikipedia") or (False, "no wiki", None),
    )
    monkeypatch.setattr(
        bvs,
        "_youtube_probe",
        lambda *_a, **_k: called.append("youtube") or (False, "no yt", None),
    )
    monkeypatch.setattr(
        bvs,
        "_reddit_scan_search_threads_with_sentiment",
        lambda *_a, **_k: called.append("reddit") or {"reddit_threads": []},
    )
    monkeypatch.setattr(
        bvs,
        "_reddit_probe_legacy_subreddit_only",
        lambda *_a, **_k: called.append("reddit_legacy") or (False, "no rd", None),
    )
    monkeypatch.setattr(
        bvs,
        "_linkedin_probe",
        lambda *_a, **_k: called.append("linkedin") or (True, "company", "https://www.linkedin.com/company/example"),
    )

    result = scan_brand_platforms(
        "Example",
        "https://example.com",
        delay=0,
        same_as_urls=[
            "https://en.wikipedia.org/wiki/Example",
            "https://www.youtube.com/@example",
        ],
    )
    by_name = {row["platform"]: row for row in result["platforms"]}
    assert by_name["Wikipedia"]["present"] is True
    assert by_name["Wikipedia"]["source"] == "json_ld_same_as"
    assert by_name["YouTube"]["source"] == "json_ld_same_as"
    assert by_name["LinkedIn"]["source"] == "search"
    assert by_name["LinkedIn"]["present"] is True
    assert "wikipedia" not in called
    assert "youtube" not in called
    assert "linkedin" in called
    assert "reddit" in called
    assert result["same_as_used"] == ["Wikipedia", "YouTube"]
