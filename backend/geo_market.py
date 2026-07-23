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


# Approximate capital / major-city coordinates for Google Search grounding latLng.
# Used so Google AIO probes retrieve locally relevant results for the configured market.
_MARKET_LAT_LNG: dict[str, tuple[float, float]] = {
    "GB": (51.5074, -0.1278),   # London
    "US": (40.7128, -74.0060),  # New York (broad US default)
    "IE": (53.3498, -6.2603),   # Dublin
    "AU": (-33.8688, 151.2093), # Sydney
    "NZ": (-36.8509, 174.7645), # Auckland
    "CA": (43.6532, -79.3832),  # Toronto
    "FR": (48.8566, 2.3522),    # Paris
    "BE": (50.8503, 4.3517),    # Brussels
    "CH": (47.3769, 8.5417),    # Zurich
    "DE": (52.5200, 13.4050),   # Berlin
    "AT": (48.2082, 16.3738),   # Vienna
    "ES": (40.4168, -3.7038),   # Madrid
    "MX": (19.4326, -99.1332),  # Mexico City
    "AR": (-34.6037, -58.3816), # Buenos Aires
    "CL": (-33.4489, -70.6693), # Santiago
    "CO": (4.7110, -74.0721),   # Bogotá
    "IT": (41.9028, 12.4964),   # Rome
    "NL": (52.3676, 4.9041),    # Amsterdam
    "PT": (38.7223, -9.1393),   # Lisbon
    "BR": (-23.5505, -46.6333), # São Paulo
    "PL": (52.2297, 21.0122),   # Warsaw
    "SE": (59.3293, 18.0686),   # Stockholm
    "DK": (55.6761, 12.5683),   # Copenhagen
    "NO": (59.9139, 10.7522),   # Oslo
    "FI": (60.1699, 24.9384),   # Helsinki
    "CZ": (50.0755, 14.4378),   # Prague
    "HU": (47.4979, 19.0402),   # Budapest
    "RO": (44.4268, 26.1025),   # Bucharest
    "GR": (37.9838, 23.7275),   # Athens
    "TR": (41.0082, 28.9784),   # Istanbul
    "SA": (24.7136, 46.6753),   # Riyadh
    "AE": (25.2048, 55.2708),   # Dubai
    "IL": (32.0853, 34.7818),   # Tel Aviv
    "JP": (35.6762, 139.6503),  # Tokyo
    "KR": (37.5665, 126.9780),  # Seoul
    "CN": (39.9042, 116.4074),  # Beijing
    "TW": (25.0330, 121.5654),  # Taipei
    "HK": (22.3193, 114.1694),  # Hong Kong
    "IN": (28.6139, 77.2090),   # New Delhi
    "SG": (1.3521, 103.8198),   # Singapore
    "PH": (14.5995, 120.9842),  # Manila
    "MY": (3.1390, 101.6869),   # Kuala Lumpur
    "TH": (13.7563, 100.5018),  # Bangkok
    "ID": (-6.2088, 106.8456),  # Jakarta
    "VN": (21.0278, 105.8342),  # Hanoi
    "ZA": (-26.2041, 28.0473),  # Johannesburg
    "LU": (49.6116, 6.1319),    # Luxembourg City
}


def lat_lng_for_market(
    country: str | None = "",
    country_code: str | None = "",
) -> dict[str, float] | None:
    """
    Return ``{"latitude": …, "longitude": …}`` for Google Search grounding, or ``None``.
    Uses ISO2 when available; otherwise returns None (prompt/system market context still applies).
    """
    _mc, mid = resolve_primary_market(country, country_code)
    if not mid:
        return None
    coords = _MARKET_LAT_LNG.get(mid.upper())
    if not coords:
        return None
    lat, lng = coords
    return {"latitude": float(lat), "longitude": float(lng)}
