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
