"""Public-site URL hints (Tranco-derived hostname list + favicon thumbnails)."""

from __future__ import annotations

import bisect
import functools
import re
import urllib.parse
from pathlib import Path

from geo_app_env import ASSETS_ROOT

_DEFAULT_DOMAIN_FILE = ASSETS_ROOT / "data" / "public_domains_tranco_head.txt"


@functools.lru_cache(maxsize=1)
def public_domains_sorted() -> tuple[str, ...]:
    """Sorted unique hostnames (lowercase) for prefix search."""
    path = _DEFAULT_DOMAIN_FILE
    if not path.is_file():
        return tuple()
    hosts: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        h = line.strip().lower()
        if h and "." in h and not h.startswith("#"):
            hosts.append(h)
    return tuple(sorted(set(hosts)))


def prefix_hint_for_suggest(raw: str) -> str:
    """Hostname fragment the user is typing, lowercased (for prefix search)."""
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if "://" in s or s.startswith("//"):
        p = urllib.parse.urlparse(s if "://" in s else "https:" + s)
        host = (p.hostname or "").strip().lower()
        if not host and p.path:
            host = p.path.split("/")[0].strip().lower()
    else:
        host = s.split("/")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


_MIN_SUGGEST_HINT_LEN = 3


def suggest_public_domains(hint: str, *, limit: int = 8) -> list[str]:
    if len(hint) < _MIN_SUGGEST_HINT_LEN:
        return []
    domains = public_domains_sorted()
    if not domains:
        return []
    i = bisect.bisect_left(domains, hint)
    out: list[str] = []
    while i < len(domains) and len(out) < limit:
        d = domains[i]
        if not d.startswith(hint):
            break
        out.append(d)
        i += 1
    return out


def public_site_favicon_url(hostname: str) -> str:
    """Google-hosted favicon lookup (no extra Python deps); ``hostname`` should be a registrable host."""
    h = (hostname or "").strip().lower()
    if not h:
        return ""
    return "https://www.google.com/s2/favicons?domain=" + urllib.parse.quote(h, safe="") + "&sz=32"


def _hostname_from_any_url(raw: str) -> str:
    u = (raw or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u
    try:
        return (urllib.parse.urlparse(u).hostname or "").lower().replace("www.", "")
    except Exception:
        return ""


def hostname_for_display_url(raw: str) -> str:
    """Hostname for favicons and read-only labels."""
    return _hostname_from_any_url(raw)


def domain_search_tuple_options(searchterm: str, *, limit: int = 12) -> list[tuple[str, str]]:
    """Return (label, normalized https URL) rows for domain autocomplete."""
    from geo_urls import normalize_competitor_url

    t = (searchterm or "").strip()
    hint = prefix_hint_for_suggest(t)
    if len(hint) < _MIN_SUGGEST_HINT_LEN:
        doms = []
    else:
        doms = suggest_public_domains(hint, limit=limit)
    return [(f"🌐 {d}", normalize_competitor_url(f"https://{d}")) for d in doms]
