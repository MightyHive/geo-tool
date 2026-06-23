"""Market-aware sitemap URL prioritisation for multi-region sites."""

from __future__ import annotations

import re
import urllib.parse

from geo_market import resolve_primary_market

# ISO-3166-1 alpha-2 → common first path segments on global brand sites.
ISO_TO_PATH_SEGMENTS: dict[str, list[str]] = {
    "AE": ["ae", "ae_ar"],
    "AR": ["ar"],
    "AT": ["at"],
    "AU": ["au"],
    "BE": ["be", "be_fr"],
    "BG": ["bg"],
    "BR": ["br"],
    "CA": ["ca", "ca_fr"],
    "CH": ["ch", "ch_fr"],
    "CL": ["cl"],
    "CN": ["cn", "cn_zh"],
    "CO": ["co"],
    "CZ": ["cz"],
    "DE": ["de"],
    "DK": ["dk"],
    "EE": ["ee"],
    "EG": ["eg"],
    "ES": ["es"],
    "FI": ["fi"],
    "FR": ["fr"],
    "GB": ["uk", "gb"],
    "GR": ["gr"],
    "HK": ["hk", "hk_en"],
    "HR": ["hr"],
    "HU": ["hu"],
    "ID": ["id"],
    "IE": ["ie"],
    "IL": ["il"],
    "IN": ["in"],
    "IT": ["it"],
    "JP": ["jp"],
    "KR": ["kr"],
    "KZ": ["kz"],
    "LT": ["lt"],
    "LU": ["lu"],
    "LV": ["lv"],
    "MX": ["mx"],
    "MY": ["my"],
    "NL": ["nl"],
    "NO": ["no"],
    "NZ": ["nz"],
    "PE": ["pe"],
    "PH": ["ph"],
    "PL": ["pl"],
    "PT": ["pt"],
    "RO": ["ro"],
    "RS": ["rs"],
    "RU": ["ru"],
    "SA": ["sa"],
    "SE": ["se"],
    "SG": ["sg"],
    "SI": ["si"],
    "SK": ["sk"],
    "TH": ["th"],
    "TR": ["tr"],
    "TW": ["tw"],
    "UA": ["ua"],
    "US": ["us", "en-us"],
    "VN": ["vn"],
    "ZA": ["za"],
}

# Substrings that often indicate leaf urlsets (pages) vs nested indexes.
_LEAF_SITEMAP_HINTS = (
    "top_sitemap",
    "top-sitemap",
    "product-sitemap",
    "products-sitemap",
    "pages-sitemap",
    "page-sitemap",
    "content-sitemap",
    "sitemap-pages",
)

_INDEX_SITEMAP_HINTS = (
    "b2c-sitemap",
    "sitemap-index",
    "sitemap_index",
    "category-sitemap",
)


def market_path_hints(market_country_code: str = "", market_country: str = "") -> list[str]:
    """Return ordered path segment hints for the primary market (e.g. GB → uk, gb)."""
    _, iso2 = resolve_primary_market(market_country, market_country_code)
    hints: list[str] = []
    if iso2:
        for seg in ISO_TO_PATH_SEGMENTS.get(iso2.upper(), []):
            if seg not in hints:
                hints.append(seg)
        low = iso2.lower()
        if low not in hints:
            hints.append(low)
    name = (market_country or "").strip().lower()
    if name:
        slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")
        for candidate in (slug, slug.replace("-", "_")):
            if candidate and candidate not in hints:
                hints.append(candidate)
    return hints


def _path_segments(path: str) -> list[str]:
    return [s for s in path.lower().split("/") if s]


def sitemap_url_market_score(url: str, hints: list[str]) -> int:
    """Higher score = fetch earlier when exploring nested sitemap indexes."""
    if not hints:
        return 0
    parsed = urllib.parse.urlparse(url)
    path = (parsed.path or "").lower()
    segments = _path_segments(path)
    score = 0
    for hint in hints:
        h = hint.lower()
        if f"/{h}/" in path or path.startswith(f"/{h}/") or f"/{h}_" in path or f"_{h}/" in path:
            score += 20
        if h in segments:
            score += 25
    for hint in _LEAF_SITEMAP_HINTS:
        if hint in path:
            score += 8
    for hint in _INDEX_SITEMAP_HINTS:
        if hint in path:
            score -= 4
    return score


def prioritize_sitemap_urls(urls: list[str], hints: list[str]) -> list[str]:
    """Stable sort: market-relevant and likely leaf sitemaps first."""
    if not urls:
        return []
    if not hints:
        return list(urls)
    indexed = list(enumerate(urls))
    indexed.sort(
        key=lambda item: (-sitemap_url_market_score(item[1], hints), item[0]),
    )
    return [u for _, u in indexed]


def market_seed_sitemap_urls(base: str, hints: list[str]) -> list[str]:
    """Candidate regional sitemap URLs to try before the global index."""
    if not hints:
        return []
    base = base.rstrip("/")
    parsed = urllib.parse.urlparse(base if "://" in base else "https://" + base)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or parsed.path.split("/")[0]
    hosts = [host]
    if host.startswith("www."):
        hosts.append(host[4:])
    else:
        hosts.append("www." + host)
    out: list[str] = []
    seen: set[str] = set()
    for h in hosts:
        origin = f"{scheme}://{h}"
        for hint in hints:
            for suffix in (f"/{hint}/sitemap.xml", f"/{hint}/sitemap_index.xml"):
                u = urllib.parse.urldefrag(origin + suffix)[0]
                if u not in seen:
                    seen.add(u)
                    out.append(u)
    return out


def market_homepage_url(base: str, hints: list[str]) -> str | None:
    """Regional homepage for the primary market, if hints are available."""
    if not hints:
        return None
    base = base.rstrip("/")
    parsed = urllib.parse.urlparse(base if "://" in base else "https://" + base)
    scheme = parsed.scheme or "https"
    host = parsed.netloc or parsed.path.split("/")[0]
    if not host:
        return None
    return urllib.parse.urljoin(f"{scheme}://{host}/", hints[0].strip("/") + "/")


def extend_sitemap_pending(
    pending: list[str],
    seen: set[str],
    new_urls: list[str],
    hints: list[str],
) -> None:
    """Insert market-relevant nested sitemaps at the front of the queue."""
    candidates = [u for u in new_urls if u not in seen and u not in pending]
    if not candidates:
        return
    ordered = prioritize_sitemap_urls(candidates, hints)
    front: list[str] = []
    back: list[str] = []
    for u in ordered:
        if hints and sitemap_url_market_score(u, hints) > 0:
            front.append(u)
        else:
            back.append(u)
    pending[:0] = front
    pending.extend(back)
