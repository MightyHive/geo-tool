"""Public-site URL hints for Streamlit (Tranco-derived hostname list + favicon thumbnails)."""

from __future__ import annotations

import bisect
import functools
import re
import urllib.parse
from pathlib import Path
from typing import Any

from geo_app_env import ASSETS_ROOT, REPO_ROOT

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


def default_domain_option_tuples(*, limit: int = 12) -> list[tuple[str, str]]:
    """First Tranco rows as (dropdown label, normalized https URL) for initial searchbox menu."""
    from geo_urls import normalize_competitor_url

    out: list[tuple[str, str]] = []
    for d in public_domains_sorted()[:limit]:
        url = normalize_competitor_url(f"https://{d}")
        out.append((f"🌐 {d}", url))
    return out


def domain_search_tuple_options(searchterm: str, *, limit: int = 12) -> list[tuple[str, str]]:
    """Search callback for ``streamlit_searchbox``: (label, url) rows."""
    from geo_urls import normalize_competitor_url

    t = (searchterm or "").strip()
    hint = prefix_hint_for_suggest(t)
    if len(hint) < _MIN_SUGGEST_HINT_LEN:
        doms = []
    else:
        doms = suggest_public_domains(hint, limit=limit)
    return [(f"🌐 {d}", normalize_competitor_url(f"https://{d}")) for d in doms]


def render_public_url_suggestions_below_input(
    *,
    session_key: str,
    limit: int = 8,
    caption: str = "Popular sites (Tranco top list)—click a row to fill the URL field:",
) -> None:
    """
    Fallback: favicon + hostname buttons that set ``session_key`` to a normalized https URL.

    Call **before** ``st.text_input(..., key=session_key)`` so button handlers do not mutate
    a widget-bound session key in the same run (Streamlit forbids that).
    """
    import streamlit as st

    from geo_urls import normalize_competitor_url

    raw = str(st.session_state.get(session_key) or "")
    hint = prefix_hint_for_suggest(raw)
    sugs = suggest_public_domains(hint, limit=limit)
    if not sugs:
        return
    st.caption(caption)
    safe_key = re.sub(r"[^0-9a-zA-Z_]+", "_", session_key) or "url"
    for i, domain in enumerate(sugs):
        ic, bc = st.columns((1, 12), gap="small")
        with ic:
            st.image(public_site_favicon_url(domain), width=22)
        with bc:
            if st.button(domain, key=f"domsug_{safe_key}_{i}", type="secondary"):
                st.session_state[session_key] = normalize_competitor_url(f"https://{domain}")
                st.rerun()


def render_url_searchbox(
    *,
    session_key: str,
    label: str,
    help: str | None = None,
    placeholder: str = "Type to search popular sites…",
    default_options_limit: int = 12,
    search_limit: int = 14,
    rerun_on_update: bool = True,
) -> None:
    """
    Optional Tranco ``streamlit-searchbox`` first (when installed), then ``st.text_input`` on
    ``session_key``. Picking a suggestion writes the normalized URL **before** the text field
    is instantiated so Streamlit does not reject the update.

    When ``streamlit-searchbox`` is not installed, Tranco suggestion buttons are rendered
    **before** the text field (same session-state rule).
    """
    import inspect
    import streamlit as st

    from geo_urls import normalize_competitor_url

    try:
        from streamlit_searchbox import st_searchbox
    except ImportError:
        render_public_url_suggestions_below_input(
            session_key=session_key,
            caption="Popular sites (Tranco)—click a row to fill the URL field below:",
        )
        st.text_input(
            label,
            key=session_key,
            help=help,
            placeholder="https://example.com",
        )
        host = _hostname_from_any_url(str(st.session_state.get(session_key) or ""))
        if host:
            ic, tx = st.columns((1, 14), gap="small")
            with ic:
                st.image(public_site_favicon_url(host), width=26)
            with tx:
                st.caption(f"Favicon preview · **{host}**")
        return

    sb_key = f"{session_key}_url_sb"

    def _search(term: str) -> list[tuple[str, str]]:
        return domain_search_tuple_options(term, limit=search_limit)

    _sb_kw: dict[str, Any] = dict(
        placeholder=placeholder,
        label="Look up popular sites (optional)",
        help=(help or "")
        + " Suggestions appear while you type. The URL field below is the canonical value for this row.",
        default_options=default_domain_option_tuples(limit=default_options_limit),
        clear_on_submit=False,
        edit_after_submit="option",
        key=sb_key,
        debounce=120,
    )
    try:
        if "rerun_on_update" in inspect.signature(st_searchbox).parameters:
            _sb_kw["rerun_on_update"] = rerun_on_update
    except (TypeError, ValueError, OSError):
        pass
    sel = st_searchbox(_search, **_sb_kw)
    if sel is not None:
        if isinstance(sel, tuple) and len(sel) >= 2:
            url = str(sel[1]).strip()
        else:
            url = str(sel).strip()
        if url:
            norm = normalize_competitor_url(url)
            if norm != str(st.session_state.get(session_key) or "").strip():
                st.session_state[session_key] = norm
                st.rerun()

    st.text_input(
        label,
        key=session_key,
        help=help,
        placeholder="https://example.com",
    )
    host = _hostname_from_any_url(str(st.session_state.get(session_key) or ""))
    if host:
        ic, tx = st.columns((1, 14), gap="small")
        with ic:
            st.image(public_site_favicon_url(host), width=26)
        with tx:
            st.caption(f"Favicon preview · **{host}**")