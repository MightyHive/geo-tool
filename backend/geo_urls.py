"""URL normalization helpers (no LLM / google-genai dependencies)."""

from __future__ import annotations

import re


def normalize_competitor_url(raw: str) -> str:
    """Ensure a crawl/search URL has an explicit https scheme."""
    s = (raw or "").strip()
    if not s:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", s):
        s = "https://" + s
    return s
