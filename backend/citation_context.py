"""
citation_context.py — Citation-level brand context detection.

Determines whether a specific citation URL was used to support a mention of
the *brand* vs a *competitor*, by analysing which section of the AI response
text contains that citation.

This replaces the cruder row-level heuristic (was brand mentioned *anywhere*
in this response?) with a section-level check (was brand mentioned in the
same paragraph/section as this citation?).

Two-pass algorithm
──────────────────
Pass 1 – Inline match
  Split the response into sections (markdown headings, then blank lines).
  Find sections that contain the citation URL or domain name inline.
  Check whether the brand or any competitor appears in those sections.

Pass 2 – Domain-stem match (Gemini / AIO grounding citations)
  Gemini returns citations as opaque grounding metadata — the URL often does
  not appear verbatim in the response text.
  In that case, derive a "stem" from the citation domain
  (e.g. "theordinary.com" → "theordinary") and check whether that stem
  appears in any brand or competitor token, assigning context accordingly.

Fallback
  If neither pass produces a match, return brand_cited=False,
  competitor_cited=False ("unknown / general source").
"""

from __future__ import annotations

import re
from typing import Any


# ── Section splitting ─────────────────────────────────────────────────────────

def _split_sections(text: str) -> list[str]:
    """Split response text into logical sections.

    Tries markdown heading boundaries first (###/##/#).
    Falls back to double-newline paragraph splitting.
    """
    if not text:
        return []
    # Split on markdown headings — keep the heading with its following text
    parts = re.split(r"(?m)^#{1,3}\s+", text)
    if len(parts) > 1:
        return [p.strip() for p in parts if p.strip()]
    # Recommendation lists often use one item per line without blank paragraphs.
    parts = re.split(r"(?m)(?=^\s*(?:(?:\*{0,2})\d+[.)]|[-*•])\s+)", text)
    if len(parts) > 1:
        return [p.strip() for p in parts if p.strip()]
    # Fall back to blank-line paragraphs
    parts = re.split(r"\n{2,}", text)
    return [p.strip() for p in parts if p.strip()]


# ── Token helpers ─────────────────────────────────────────────────────────────

def _to_stem(s: str) -> str:
    """Reduce a brand/domain name to a searchable stem."""
    s = s.lower().strip()
    # Remove common TLD suffixes
    s = re.sub(r"\.(com|co\.uk|co\.nz|co\.au|org|net|io|ai|uk)$", "", s)
    # Remove non-alphanumeric characters
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def text_mentions_brand_tokens(text: str, tokens: list[str]) -> bool:
    """Match entity tokens with boundaries and flexible punctuation separators."""
    normalized_text = re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()
    if not normalized_text:
        return False
    for token in tokens:
        normalized_token = re.sub(r"[^a-z0-9]+", " ", (token or "").lower()).strip()
        if normalized_token and re.search(
            rf"(?<!\w){re.escape(normalized_token)}(?!\w)",
            normalized_text,
        ):
            return True
    return False


def merge_brand_tokens(*groups: list[str]) -> list[str]:
    """Merge token sources while preserving order and case-insensitive uniqueness."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            token = str(value or "").strip()
            key = token.casefold()
            if token and key not in seen:
                seen.add(key)
                merged.append(token)
    return merged


def build_brand_tokens(brand_name: str, brand_site_url: str = "") -> list[str]:
    """Return a list of lowercase string tokens for the brand."""
    tokens: list[str] = []
    b = (brand_name or "").strip()
    if b:
        tokens.append(b.lower())
        stem = _to_stem(b)
        if stem and stem not in tokens:
            tokens.append(stem)
    if brand_site_url:
        try:
            from urllib.parse import urlparse
            host = urlparse(brand_site_url if "//" in brand_site_url else f"https://{brand_site_url}").hostname or ""
            host = host.removeprefix("www.")
            stem = _to_stem(host)
            if stem and stem not in tokens:
                tokens.append(stem)
        except Exception:
            pass
    return [t for t in tokens if len(t) >= 3]


def build_competitor_tokens(
    competitor_urls: list[str] | None = None,
    competitor_brands: list[str] | None = None,
) -> list[str]:
    """Return a deduplicated list of lowercase competitor tokens."""
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(t: str) -> None:
        t = t.lower().strip()
        if len(t) >= 3 and t not in seen:
            seen.add(t)
            tokens.append(t)
            stem = _to_stem(t)
            if stem and stem not in seen:
                seen.add(stem)
                tokens.append(stem)

    for url in (competitor_urls or []):
        try:
            from urllib.parse import urlparse
            host = urlparse(url if "//" in url else f"https://{url}").hostname or ""
            host = host.removeprefix("www.")
            if host:
                _add(host)
                _add(_to_stem(host))
        except Exception:
            pass

    for brand in (competitor_brands or []):
        if brand:
            _add(brand)

    return tokens


# ── Core logic ────────────────────────────────────────────────────────────────

def infer_citation_brand_context(
    response_text: str,
    citation_url: str,
    citation_domain: str,
    brand_tokens: list[str],
    competitor_tokens: list[str],
    citation_title: str = "",
) -> dict[str, bool]:
    """Return {"brand_cited": bool, "competitor_cited": bool} for one citation.

    See module docstring for the two-pass algorithm.
    """
    title_lower = (citation_title or "").lower()
    title_brand_cited = False
    title_competitor_cited = False
    if title_lower:
        title_brand_cited = text_mentions_brand_tokens(citation_title, brand_tokens)
        title_competitor_cited = text_mentions_brand_tokens(citation_title, competitor_tokens)

    if not response_text:
        return {
            "brand_cited": title_brand_cited,
            "competitor_cited": title_competitor_cited,
        }

    url_lower = (citation_url or "").lower().rstrip("/")
    domain_lower = (citation_domain or "").lower().removeprefix("www.")

    sections = _split_sections(response_text)

    # ── Pass 1: inline URL / domain match ────────────────────────────────────
    matching: list[str] = []
    for sec in sections:
        sec_l = sec.lower()
        if url_lower and url_lower in sec_l:
            matching.append(sec)
        elif domain_lower and domain_lower in sec_l:
            matching.append(sec)

    if matching:
        combined = " ".join(matching).lower()
        brand_cited = text_mentions_brand_tokens(combined, brand_tokens)
        competitor_cited = text_mentions_brand_tokens(combined, competitor_tokens)
        return {
            "brand_cited": brand_cited or title_brand_cited,
            "competitor_cited": competitor_cited or title_competitor_cited,
        }

    # ── Pass 2: domain-stem matching (for grounding citations) ───────────────
    domain_stem = _to_stem(domain_lower)

    if domain_stem and len(domain_stem) >= 4:
        for bt in brand_tokens:
            bt_stem = _to_stem(bt)
            if bt_stem and len(bt_stem) >= 4:
                if bt_stem in domain_stem or domain_stem in bt_stem:
                    return {
                        "brand_cited": True,
                        "competitor_cited": title_competitor_cited,
                    }

        for ct in competitor_tokens:
            ct_stem = _to_stem(ct)
            if ct_stem and len(ct_stem) >= 4:
                if ct_stem in domain_stem or domain_stem in ct_stem:
                    return {
                        "brand_cited": title_brand_cited,
                        "competitor_cited": True,
                    }

    # ── Fallback: cannot determine context ───────────────────────────────────
    return {
        "brand_cited": title_brand_cited,
        "competitor_cited": title_competitor_cited,
    }


def enrich_citations_with_context(
    citations: list[dict[str, Any]],
    response_text: str,
    brand_tokens: list[str],
    competitor_tokens: list[str],
) -> list[dict[str, Any]]:
    """Add brand_cited / competitor_cited fields to each citation in-place.

    Idempotent — skips citations that already have the fields set.
    """
    for c in citations:
        if "brand_cited" not in c:
            ctx = infer_citation_brand_context(
                response_text,
                c.get("url", ""),
                c.get("domain", ""),
                brand_tokens,
                competitor_tokens,
                str(c.get("title") or ""),
            )
            c["brand_cited"] = ctx["brand_cited"]
            c["competitor_cited"] = ctx["competitor_cited"]
    return citations
