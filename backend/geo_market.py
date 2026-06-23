"""
Primary **market / geography** for all Gemini and OpenAI generation in this repo.

Precedence when resolving from call sites (e.g. wizard session or CLI arguments):

1. Explicit session/UI or CLI arguments (country name and/or ISO-3166-1 alpha-2 code)
2. GA4-derived primary country (when connected)
3. Environment defaults (below)

Environment variables (first non-empty wins per field):

- **GEO_PRIMARY_MARKET_COUNTRY** or **PRIMARY_MARKET_COUNTRY** or **GA4_PRIMARY_MARKET_COUNTRY** —
  human-readable country/region (e.g. ``United Kingdom``).
- **GEO_PRIMARY_MARKET_COUNTRY_ID** or **PRIMARY_MARKET_COUNTRY_ID** or **GA4_PRIMARY_MARKET_COUNTRY_ID** —
  ISO-3166-1 alpha-2 code (e.g. ``GB``).

Set these in ``.env``, ``env/.env.<APP_ENV>``, or process environment so CLI runs and Streamlit
both pick up the same defaults without GA4.
"""

from __future__ import annotations

import os


def _env_first(*keys: str) -> str:
    for k in keys:
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    return ""


def default_primary_market_from_env() -> tuple[str, str]:
    """``(country_name, iso2_or_empty)`` from env aliases; ISO uppercased to two letters when present."""
    country = _env_first(
        "GEO_PRIMARY_MARKET_COUNTRY",
        "PRIMARY_MARKET_COUNTRY",
        "GA4_PRIMARY_MARKET_COUNTRY",
    )
    raw_id = _env_first(
        "GEO_PRIMARY_MARKET_COUNTRY_ID",
        "PRIMARY_MARKET_COUNTRY_ID",
        "GA4_PRIMARY_MARKET_COUNTRY_ID",
    )
    cid = raw_id.upper()[:2] if len(raw_id.strip()) >= 2 else ""
    return (country, cid)


def resolve_primary_market(country: str | None, country_code: str | None) -> tuple[str, str]:
    """
    Normalise caller-supplied market; if **both** are empty, fall back to :func:`default_primary_market_from_env`.
    """
    c = (country or "").strip()
    raw = (country_code or "").strip()
    cid = raw.upper()[:2] if len(raw) >= 2 else ""
    if c or cid:
        return (c, cid)
    return default_primary_market_from_env()
