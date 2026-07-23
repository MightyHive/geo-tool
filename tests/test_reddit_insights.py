"""Tests for metadata derived from Reddit citation URLs."""

from api.reddit_insights import _citation_text, _parse_reddit_url


def test_parse_reddit_url_extracts_subreddit_name() -> None:
    metadata = _parse_reddit_url(
        "https://www.reddit.com/r/SkincareAddictionUK/comments/bccun4/"
        "what_are_the_good_superdrug_products/"
    )

    assert metadata["subreddit"] == "SkincareAddictionUK"


def test_parse_reddit_url_without_subreddit_leaves_it_empty() -> None:
    metadata = _parse_reddit_url("https://redd.it/bccun4")

    assert metadata["subreddit"] is None


def test_citation_text_returns_inline_passage() -> None:
    text, is_exact = _citation_text(
        "First paragraph.\n\nReddit discussion: https://www.reddit.com/r/SEO/comments/abc123/post/",
        "https://www.reddit.com/r/SEO/comments/abc123/post/",
        "reddit.com",
    )

    assert text == "Reddit discussion: https://www.reddit.com/r/SEO/comments/abc123/post/"
    assert is_exact is True


def test_citation_text_marks_structured_grounding_as_non_exact() -> None:
    text, is_exact = _citation_text(
        "The response recommends comparing community experiences.",
        "https://www.reddit.com/r/SEO/comments/abc123/post/",
        "reddit.com",
    )

    assert text == "The response recommends comparing community experiences."
    assert is_exact is False
