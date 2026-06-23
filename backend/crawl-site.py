#!/usr/bin/env python3
"""
Crawl a site's discoverability artifacts: robots.txt, llms.txt (generated + optional live copy), sitemap URLs,
per-page JSON-LD (incl. sameAs), og:image, and an optional off-site brand_visibility probe (see brand_visibility_scan.py).
Saves found resources locally.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from email.message import Message
from html import unescape as html_unescape_json
from pathlib import Path
from typing import Any

USER_AGENT = "SEOGECrawlBot/1.0 (+https://example.local; audit)"
REQUEST_TIMEOUT = 25
SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

# HTTPS: set by configure_tls() before any _request(); None means omit urlopen(context=…).
_HTTPS_CONTEXT: ssl.SSLContext | None = None


def configure_tls(*, insecure: bool, no_certifi: bool) -> dict[str, Any]:
    """Pick TLS verification mode. Returns a small dict for the audit report."""
    global _HTTPS_CONTEXT
    if insecure:
        _HTTPS_CONTEXT = ssl._create_unverified_context()
        return {"mode": "insecure", "certifi": False, "note": "certificate verification disabled"}
    if not no_certifi:
        try:
            import certifi  # type: ignore[import-untyped]

            _HTTPS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
            return {"mode": "certifi", "certifi": True, "cafile": certifi.where()}
        except ImportError:
            pass
    _HTTPS_CONTEXT = None
    return {
        "mode": "stdlib_default",
        "certifi": False,
        "note": "install certifi (pip install certifi) if you see CERTIFICATE_VERIFY_FAILED on macOS",
    }

OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property\s*=\s*["\']og:image["\'][^>]*>',
    re.IGNORECASE,
)
OG_IMAGE_CONTENT_RE = re.compile(
    r'content\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)
META_DESC_RE = re.compile(
    r'<meta[^>]+name\s*=\s*["\']description["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
META_DESC_RE_ALT = re.compile(
    r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+name\s*=\s*["\']description["\']',
    re.IGNORECASE,
)


@dataclass
class FetchResult:
    url: str
    status: int | None
    error: str | None = None
    body: bytes = b""
    final_url: str | None = None
    headers: dict[str, str] | None = None


def _request(url: str) -> FetchResult:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        method="GET",
    )
    open_kw: dict[str, Any] = {"timeout": REQUEST_TIMEOUT}
    if _HTTPS_CONTEXT is not None and urllib.parse.urlparse(url).scheme == "https":
        open_kw["context"] = _HTTPS_CONTEXT
    try:
        with urllib.request.urlopen(req, **open_kw) as resp:
            body = resp.read()
            final = resp.geturl()
            status = getattr(resp, "status", 200)
            # Handle gzip if server sends raw gzip without urllib decoding
            headers = resp.headers
            hdict: dict[str, str] | None = None
            if isinstance(headers, Message):
                hdict = {k.lower(): v for k, v in headers.items()}
                enc = (headers.get("Content-Encoding") or "").lower()
                if enc == "gzip":
                    try:
                        body = gzip.decompress(body)
                    except OSError:
                        pass
            return FetchResult(url=url, status=status, body=body, final_url=final, headers=hdict)
    except urllib.error.HTTPError as e:
        hdict = None
        if e.headers:
            hdict = {k.lower(): v for k, v in e.headers.items()}
        return FetchResult(
            url=url, status=e.code, error=str(e), body=e.read() or b"", headers=hdict
        )
    except Exception as e:  # noqa: BLE001 — surface any network failure
        return FetchResult(url=url, status=None, error=str(e))


def normalize_base(url: str) -> str:
    url = url.strip()
    if not url:
        raise ValueError("URL is empty")
    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme}")
    netloc = parsed.netloc or parsed.path.split("/")[0]
    path = parsed.path if parsed.netloc else "/" + "/".join(parsed.path.split("/")[1:])
    if not path or path == "":
        path = "/"
    base = f"{parsed.scheme}://{netloc}"
    return base.rstrip("/")


def safe_dir_name(base: str) -> str:
    h = hashlib.sha256(base.encode()).hexdigest()[:12]
    host = urllib.parse.urlparse(base + "/").netloc.replace(":", "_")
    return f"{host}_{h}"


def ensure_dir(path: str) -> None:
    import os

    os.makedirs(path, exist_ok=True)


def write_bytes(out_dir: str, name: str, data: bytes) -> str:
    import os

    path = os.path.join(out_dir, name)
    with open(path, "wb") as f:
        f.write(data)
    return path


def write_text(out_dir: str, name: str, text: str) -> str:
    import os

    path = os.path.join(out_dir, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def parse_robots_sitemaps(robots_text: str) -> list[str]:
    urls: list[str] = []
    for line in robots_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(?i)^sitemap:\s*(.+)$", line)
        if m:
            urls.append(m.group(1).strip())
    return urls


RobotsGroup = dict[str, Any]  # comments: list[str], user_agents: list[str], rules: list[tuple[str, str]]


def _robots_group_signature(g: RobotsGroup) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    uas = tuple(sorted(ua.strip().lower() for ua in g["user_agents"]))
    rules = tuple((d.lower(), v) for d, v in g["rules"])
    return (uas, rules)


def parse_robots_structure(text: str) -> tuple[list[RobotsGroup], list[str]]:
    """Split robots.txt into UA rule groups and raw Sitemap lines (order preserved)."""
    groups: list[RobotsGroup] = []
    sitemaps: list[str] = []
    pending_header: list[str] = []
    current: RobotsGroup | None = None

    def flush_current() -> None:
        nonlocal current
        if current is not None and (current["user_agents"] or current["rules"]):
            groups.append(current)
        current = None

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            pending_header.append(raw)
            continue
        if stripped.startswith("#"):
            pending_header.append(raw)
            continue

        m = re.match(r"(?i)^User-agent:\s*(.+)$", stripped)
        if m:
            ua = m.group(1).strip()
            if current is not None and current["rules"]:
                flush_current()
            if current is None:
                current = {"comments": pending_header[:], "user_agents": [], "rules": []}
                pending_header = []
            current["user_agents"].append(ua)
            continue

        m = re.match(r"(?i)^Sitemap:\s*(.+)$", stripped)
        if m:
            flush_current()
            sitemaps.append(raw)
            pending_header = []
            continue

        m = re.match(r"(?i)^(Allow|Disallow|Crawl-delay):\s*(.*)$", stripped)
        if m:
            if current is None:
                current = {"comments": pending_header[:], "user_agents": [], "rules": []}
                pending_header = []
            current["rules"].append((m.group(1), m.group(2).strip()))
            continue

        pending_header.append(raw)

    flush_current()
    return groups, sitemaps


def render_robots_group(g: RobotsGroup) -> str:
    lines: list[str] = []
    lines.extend(g["comments"])
    for ua in g["user_agents"]:
        lines.append(f"User-agent: {ua}")
    for d, v in g["rules"]:
        lines.append(f"{d}: {v}")
    return "\n".join(lines)


def _sitemap_url_from_line(line: str) -> str | None:
    m = re.match(r"(?i)^\s*Sitemap:\s*(.+)$", line.strip())
    return m.group(1).strip() if m else None


def rewrite_template_sitemaps(text: str, canonical_sitemap_url: str) -> str:
    """Point template example.com sitemap lines at the audited site's sitemap URL."""
    out: list[str] = []
    for raw in text.splitlines():
        m = re.match(r"(?i)^(\s*Sitemap:\s*)(\S+)", raw)
        if m:
            u = m.group(2).strip()
            host = urllib.parse.urlparse(u).netloc.lower()
            if host == "example.com" or host == "www.example.com":
                out.append(f"{m.group(1)}{canonical_sitemap_url}")
            else:
                out.append(raw)
        else:
            out.append(raw)
    return "\n".join(out)


def merge_robots_with_sample(
    site_text: str,
    sample_text: str,
    canonical_sitemap_url: str,
) -> tuple[str, dict[str, Any]]:
    """Append sample groups and Sitemap lines that are not already on the site."""
    adapted = rewrite_template_sitemaps(sample_text, canonical_sitemap_url)
    site_groups, site_sitemaps = parse_robots_structure(site_text)
    sample_groups, sample_sitemaps = parse_robots_structure(adapted)

    site_sigs = {_robots_group_signature(g) for g in site_groups}
    site_sm_urls = {
        urllib.parse.urldefrag(u)[0].rstrip("/")
        for line in site_sitemaps
        if (u := _sitemap_url_from_line(line))
    }

    added_groups: list[RobotsGroup] = []
    for g in sample_groups:
        if _robots_group_signature(g) not in site_sigs:
            added_groups.append(g)
            site_sigs.add(_robots_group_signature(g))

    added_sitemap_lines: list[str] = []
    for line in sample_sitemaps:
        u = _sitemap_url_from_line(line)
        if not u:
            continue
        norm = urllib.parse.urldefrag(u)[0].rstrip("/")
        if norm not in site_sm_urls:
            added_sitemap_lines.append(f"Sitemap: {u}")
            site_sm_urls.add(norm)

    base_body = site_text.rstrip()
    # Dedupe identical Sitemap lines (template may repeat after rewrite)
    seen_sm_line: set[str] = set()
    deduped_sitemaps: list[str] = []
    for sl in added_sitemap_lines:
        if sl not in seen_sm_line:
            seen_sm_line.add(sl)
            deduped_sitemaps.append(sl)
    added_sitemap_lines = deduped_sitemaps

    meta: dict[str, Any] = {
        "template_groups_added": len(added_groups),
        "template_sitemap_lines_added": len(added_sitemap_lines),
    }

    if not added_groups and not added_sitemap_lines:
        return (base_body + ("\n" if base_body else ""), meta)

    lines_out: list[str] = []
    if base_body:
        lines_out.append(base_body)
        lines_out.append("")
    lines_out.append("# --- Added from reference template (samples/robots.txt) ---")
    lines_out.append("")
    if added_groups:
        lines_out.append("\n\n".join(render_robots_group(g) for g in added_groups))
    if added_sitemap_lines:
        if added_groups:
            lines_out.append("")
        lines_out.append("\n".join(added_sitemap_lines))

    merged = "\n".join(lines_out).rstrip() + "\n"
    return (merged, meta)


from geo_app_env import ASSETS_ROOT


def default_sample_robots_path() -> Path:
    return ASSETS_ROOT / "samples" / "robots.txt"


def parse_sitemap_xml(data: bytes) -> tuple[list[str], list[str]]:
    """Return (page_locs, nested_sitemap_locs)."""
    page_urls: list[str] = []
    sitemap_urls: list[str] = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return page_urls, sitemap_urls
    tag = root.tag
    if tag.endswith("sitemapindex"):
        for loc in root.findall(".//sm:sitemap/sm:loc", SITEMAP_NS):
            if loc.text:
                sitemap_urls.append(loc.text.strip())
        for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap/{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
            if loc.text and loc.text.strip() not in sitemap_urls:
                sitemap_urls.append(loc.text.strip())
    elif tag.endswith("urlset"):
        for loc in root.findall(".//sm:url/sm:loc", SITEMAP_NS):
            if loc.text:
                page_urls.append(loc.text.strip())
        for loc in root.findall(".//{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc"):
            if loc.text and loc.text.strip() not in page_urls:
                page_urls.append(loc.text.strip())
    return page_urls, sitemap_urls


def collect_sitemap_page_urls(
    base: str,
    robots_body: str | None,
    max_sitemaps: int,
    max_urls: int,
    *,
    market_country: str = "",
    market_country_code: str = "",
) -> list[str]:
    from sitemap_market import (
        extend_sitemap_pending,
        market_path_hints,
        market_seed_sitemap_urls,
        prioritize_sitemap_urls,
    )

    hints = market_path_hints(market_country_code, market_country)
    seen_sitemaps: set[str] = set()
    pending_sitemaps: list[str] = []

    for seed in market_seed_sitemap_urls(base, hints):
        if seed not in pending_sitemaps:
            pending_sitemaps.append(seed)

    if robots_body:
        extend_sitemap_pending(pending_sitemaps, seen_sitemaps, parse_robots_sitemaps(robots_body), hints)

    defaults = [
        urllib.parse.urljoin(base + "/", "sitemap.xml"),
        urllib.parse.urljoin(base + "/", "sitemap_index.xml"),
    ]
    extend_sitemap_pending(pending_sitemaps, seen_sitemaps, defaults, hints)

    if hints:
        pending_sitemaps[:] = prioritize_sitemap_urls(pending_sitemaps, hints)

    page_urls: list[str] = []
    while pending_sitemaps and len(seen_sitemaps) < max_sitemaps and len(page_urls) < max_urls:
        sm_url = pending_sitemaps.pop(0)
        if sm_url in seen_sitemaps:
            continue
        seen_sitemaps.add(sm_url)
        fr = _request(sm_url)
        if fr.status != 200 or not fr.body:
            continue
        pages, nested = parse_sitemap_xml(fr.body)
        for u in pages:
            if u not in page_urls:
                page_urls.append(u)
                if len(page_urls) >= max_urls:
                    break
        if len(page_urls) >= max_urls:
            break
        extend_sitemap_pending(pending_sitemaps, seen_sitemaps, nested, hints)
        if hints:
            pending_sitemaps[:] = prioritize_sitemap_urls(pending_sitemaps, hints)
    return page_urls


def merge_ga4_then_sitemap_urls(
    ga4_urls: list[str],
    sitemap_urls: list[str],
    max_urls: int,
) -> list[str]:
    """GA4 traffic-ordered URLs first, then sitemap URLs, deduped, capped at ``max_urls``."""

    def norm(u: str) -> str:
        return urllib.parse.urldefrag(u.strip())[0]

    seen: set[str] = set()
    ordered: list[str] = []

    for u in ga4_urls:
        nu = norm(u)
        if nu in seen:
            continue
        seen.add(nu)
        ordered.append(nu)
        if len(ordered) >= max_urls:
            return ordered

    for u in sitemap_urls:
        nu = norm(u)
        if nu in seen:
            continue
        seen.add(nu)
        ordered.append(nu)
        if len(ordered) >= max_urls:
            break
    return ordered


def slug_from_url(page_url: str) -> str:
    p = urllib.parse.urlparse(page_url)
    path = p.path.strip("/") or "index"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", path)[:120]
    if not slug:
        slug = "page"
    h = hashlib.sha256(page_url.encode()).hexdigest()[:8]
    return f"{slug}_{h}"


def extract_og_images(html: str) -> list[str]:
    found: list[str] = []
    for block in OG_IMAGE_RE.findall(html):
        m = OG_IMAGE_CONTENT_RE.search(block)
        if m:
            url = html_unescape_meta(m.group(1).strip())
            if url and url not in found:
                found.append(url)
    # Also try property after content (uncommon)
    alt_re = re.compile(
        r'<meta[^>]+content\s*=\s*["\']([^"\']+)["\'][^>]+property\s*=\s*["\']og:image["\']',
        re.IGNORECASE,
    )
    for m in alt_re.finditer(html):
        url = html_unescape_meta(m.group(1).strip())
        if url and url not in found:
            found.append(url)
    return found


def html_unescape_meta(s: str) -> str:
    return (
        s.replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )


def is_site_home_url(page_url: str, base: str) -> bool:
    """True if page_url is the crawl root (homepage), ignoring fragments and trailing slashes."""
    a = urllib.parse.urldefrag(page_url.strip())[0]
    b = urllib.parse.urldefrag(base.rstrip("/") + "/")[0]
    pa, pb = urllib.parse.urlparse(a), urllib.parse.urlparse(b)
    path_a = (pa.path or "/").rstrip("/")
    path_b = (pb.path or "/").rstrip("/")
    return (
        pa.scheme.lower() == pb.scheme.lower()
        and pa.netloc.lower() == pb.netloc.lower()
        and path_a == path_b
    )


def extract_html_title(html: str) -> str | None:
    m = TITLE_RE.search(html)
    if not m:
        return None
    t = html_unescape_meta(re.sub(r"\s+", " ", m.group(1)).strip())
    return t or None


def extract_meta_description(html: str) -> str | None:
    for rx in (META_DESC_RE, META_DESC_RE_ALT):
        m = rx.search(html)
        if m:
            d = html_unescape_meta(re.sub(r"\s+", " ", m.group(1)).strip())
            return d or None
    return None


# Meta `name` values that may carry per-bot or global robots directives (aligned with create-report AI table).
_CRAWLER_META_NAMES: frozenset[str] = frozenset(
    {
        "robots",
        "gptbot",
        "oai-searchbot",
        "chatgpt-user",
        "claudebot",
        "perplexitybot",
        "google-extended",
        "googleother",
        "applebot-extended",
        "amazonbot",
        "facebookbot",
        "ccbot",
        "anthropic-ai",
        "bytespider",
        "cohere-ai",
    }
)


def extract_robots_meta_for_page(html: str) -> tuple[str | None, dict[str, str]]:
    """Return (generic meta name=robots content, lowercased-name -> content for bot-specific metas)."""
    generic_parts: list[str] = []
    named: dict[str, str] = {}
    for m in re.finditer(r"<meta\s+[^>]+>", html, re.IGNORECASE):
        tag = m.group(0)
        if re.search(r"http-equiv", tag, re.IGNORECASE):
            continue
        nm = re.search(r'name\s*=\s*["\']([^"\']+)["\']', tag, re.IGNORECASE)
        cm = re.search(r'content\s*=\s*["\']([^"\']*)["\']', tag, re.IGNORECASE)
        if not nm or not cm:
            continue
        name_raw = html_unescape_meta(nm.group(1).strip())
        content_raw = html_unescape_meta(cm.group(1).strip())
        nl = name_raw.lower()
        if nl == "robots":
            if content_raw:
                generic_parts.append(content_raw)
        elif nl in _CRAWLER_META_NAMES:
            named[nl] = content_raw
    generic = ", ".join(generic_parts) if generic_parts else None
    return generic, named


def build_json_ld_txt(
    base: str,
    *,
    name: str | None,
    description: str | None,
    image: str | None,
    same_as: list[str],
) -> str:
    """Minimal JSON-LD document (pretty-printed JSON) for the audited site, WebSite-style."""
    site_url = base.rstrip("/") + "/"
    doc: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "@id": site_url + "#website",
        "url": site_url,
        "name": name or urllib.parse.urlparse(base).netloc,
    }
    if description:
        doc["description"] = description
    if image:
        doc["image"] = image
    if same_as:
        doc["sameAs"] = same_as
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def default_llms_skeleton_path() -> Path:
    return ASSETS_ROOT / "samples" / "llms-txt-skeleton.txt"


def _llms_link_label_for_page(url: str, base: str) -> str:
    if is_site_home_url(url, base):
        return "Homepage"
    path = urllib.parse.urldefrag(url)[0]
    parsed = urllib.parse.urlparse(path)
    segs = [s for s in parsed.path.strip("/").split("/") if s]
    if not segs:
        return parsed.netloc or "Page"
    raw = segs[-1].replace("-", " ").replace("_", " ")
    label = raw.title()
    if len(label) > 72:
        label = label[:69] + "…"
    return label


def _llms_same_as_label(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    if "wikipedia.org" in host:
        return "Wikipedia"
    if "twitter.com" in host or "x.com" in host:
        return "X / Twitter"
    if "facebook.com" in host:
        return "Facebook"
    if "instagram.com" in host:
        return "Instagram"
    if "linkedin.com" in host:
        return "LinkedIn"
    if "youtube.com" in host:
        return "YouTube"
    if "github.com" in host:
        return "GitHub"
    return host or "Related profile"


def _safe_md_link_text(s: str) -> str:
    return s.replace("[", "").replace("]", "")


def _llms_md_link_line(title: str, url: str, note: str | None = None) -> str:
    t = _safe_md_link_text(title)
    if note:
        return f"- [{t}]({url}): {note}"
    return f"- [{t}]({url})"


LLMS_EXCLUDE_FRAGMENTS: tuple[str, ...] = (
    "utm_",
    "gclid",
    "fbclid",
    "mc_eid",
    "/search",
    "sort=",
    "filter=",
    "page=",
    "/basket",
    "/cart",
    "/checkout",
    "/login",
    "/signin",
    "/account",
    "/wishlist",
    "/compare",
    "?q=",
    "query=",
)


def _llms_norm_key(url: str) -> str:
    u = urllib.parse.urldefrag(url.strip())[0]
    p = urllib.parse.urlparse(u)
    net = p.netloc.lower()
    path = p.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    q = p.query or ""
    return f"{p.scheme.lower()}://{net}{path}?{q}".rstrip("?")


def is_valid_llms_internal_url(url: str, base: str) -> bool:
    try:
        raw = urllib.parse.urldefrag(url.strip())[0]
        if not raw or any(c in raw for c in " \n\r\t"):
            return False
        p = urllib.parse.urlparse(raw)
        b = urllib.parse.urlparse(base.rstrip("/") + "/")
        if p.scheme not in ("http", "https") or not p.netloc:
            return False
        if p.scheme != b.scheme:
            return False
        if p.netloc.lower() != b.netloc.lower():
            return False
        low = raw.lower().rstrip("/")
        if low.endswith("(http") or (low.endswith("http") and len(raw) < 14):
            return False
        return True
    except Exception:
        return False


def is_valid_llms_external_https(url: str) -> bool:
    try:
        raw = urllib.parse.urldefrag(url.strip())[0]
        if not raw or any(c in raw for c in " \n\r\t"):
            return False
        p = urllib.parse.urlparse(raw)
        return p.scheme == "https" and bool(p.netloc)
    except Exception:
        return False


def _llms_query_excluded(path: str, query: str) -> bool:
    blob = f"{path}?{query}".lower()
    return any(x in blob for x in LLMS_EXCLUDE_FRAGMENTS)


def is_likely_product_path(path: str) -> bool:
    pl = (path or "").lower()
    if "/p/" in pl or "/product/" in pl:
        return True
    if re.search(r"/products/\d", pl):
        return True
    if re.search(r"/[^/]+-\d{5,}(?:/|$|\?)", pl):
        return True
    if re.search(r"/\d{7,}(?:/|$|\?)", pl):
        return True
    return False


def is_likely_category_path(path: str) -> bool:
    if is_likely_product_path(path):
        return False
    pl = (path or "").strip()
    if not pl or pl == "/":
        return False
    parts = [x for x in pl.strip("/").split("/") if x]
    if not parts:
        return False
    if len(parts) > 3:
        return False
    return True


def classify_llms_internal_path(path: str, query: str) -> str:
    if _llms_query_excluded(path, query):
        return "skip"
    pl = (path or "/").lower()
    pq = f"{path}?{query}".lower()
    if pl in ("", "/"):
        return "primary"
    primary_kw = (
        "about",
        "company",
        "contact",
        "help",
        "support",
        "faq",
        "customer-service",
        "customer_service",
        "store-locator",
        "store_locator",
        "stores",
        "locations",
        "find-a-store",
        "branch",
        "trade-account",
        "careers",
    )
    if any(k in pl or k in pq for k in primary_kw):
        return "primary"
    policy_kw = (
        "delivery",
        "returns",
        "refund",
        "warranty",
        "terms",
        "privacy",
        "cookies",
        "legal",
        "policies",
        "conditions",
    )
    if any(k in pl for k in policy_kw):
        return "policy"
    guides_kw = ("guide", "advice", "blog", "learn", "resources", "how-to", "articles", "news", "insights")
    if any(k in pl for k in guides_kw):
        return "guides"
    if is_likely_product_path(pl):
        return "product_optional"
    if is_likely_category_path(pl):
        return "category"
    return "optional"


def llms_internal_score(url: str, kind: str) -> int:
    p = urllib.parse.urlparse(urllib.parse.urldefrag(url)[0])
    path = (p.path or "/").lower()
    sc = 0
    if path in ("", "/"):
        sc += 200
    if kind == "primary":
        sc += 80
    elif kind == "policy":
        sc += 70
    elif kind == "category":
        sc += 55
    elif kind == "guides":
        sc += 45
        if "blog" in path or "news" in path:
            sc -= 18
    elif kind == "optional":
        sc += 12
    elif kind == "product_optional":
        sc -= 40
    for term in ("contact", "help", "support", "delivery", "returns", "privacy", "terms", "about"):
        if term in path:
            sc += 15
    return sc


def clean_llms_title(site_title: str | None, netloc: str, brand: str | None) -> str:
    if brand and brand.strip():
        return brand.strip()
    raw = (site_title or "").strip()
    if not raw:
        return netloc.split(":")[0]
    title = raw
    for sep in ("|", " – ", " — ", " - ", "–", "—", ":"):
        if sep in title:
            title = title.split(sep, 1)[0].strip()
            break
    return title.strip() or netloc.split(":")[0]


def _llms_blockquote_summary(text: str, max_len: int = 280) -> str:
    line = re.sub(r"\s+", " ", text).strip()
    if not line:
        return ""
    if len(line) <= max_len:
        s = line.rstrip(".")
        return (s + ".") if s else ""
    cut = line[:max_len].rstrip()
    if " " in cut:
        cut = cut[: cut.rfind(" ")].rstrip(",;:- ")
    if cut and not cut.endswith("."):
        cut += "."
    return cut


def clean_llms_summary(
    meta_description: str | None,
    clean_brand: str,
    industry: str | None,
) -> str:
    if meta_description and meta_description.strip():
        summary = re.sub(r"\s+", " ", meta_description.strip())
        summary = re.sub(
            r"\b(FREE|Free)\s+(UK|US|EU|WORLDWIDE)\s+(DELIVERY|SHIPPING)\b",
            lambda m: f"{m.group(2).lower()} {m.group(3).lower()}",
            summary,
        )
        return _llms_blockquote_summary(summary, 280)
    ind = (industry or "").strip().lower()
    bl = clean_brand.strip()
    if any(x in ind for x in ("auto", "vehicle")):
        return (
            f"{bl} is a car parts and automotive accessories retailer offering replacement parts, "
            "tools, and delivery or collection options for motorists and trade customers."
        )
    if any(x in ind for x in ("shop", "retail", "ecommerce", "shopping")):
        return f"{bl} is an online retailer offering products, ordering, delivery, and customer support."
    return (
        f"{bl} is a public website offering information, products, services, "
        "and support for its visitors and customers."
    )


def llms_link_note_for(url: str, kind: str, base: str) -> str:
    path = urllib.parse.urlparse(urllib.parse.urldefrag(url)[0]).path.lower()
    if is_site_home_url(url, base):
        return "Main entry point for products, services, and customer information."
    if "contact" in path:
        return "Official contact and customer support routes."
    if "about" in path or "company" in path or "careers" in path:
        return "Company and brand context for visitors."
    if "store" in path or "locator" in path or "branch" in path or "locations" in path:
        return "Physical locations, branch finder, or click-and-collect information."
    if "help" in path or "support" in path or "faq" in path:
        return "Help and support content for orders, accounts, and service questions."
    if kind == "policy":
        if "delivery" in path or "shipping" in path:
            return "Delivery and shipping policy, timings, and charges."
        if "return" in path or "refund" in path:
            return "Returns and refunds policy."
        if "privacy" in path or "cookie" in path:
            return "Privacy and data handling information."
        if "term" in path or "condition" in path:
            return "Terms of use and purchase conditions."
        return "Customer policy, legal, or trust information."
    if kind == "category":
        return "Category page grouping related products or services."
    if kind == "guides":
        return "Guides, advice, or editorial content for customers."
    if kind == "product_optional":
        return "Representative product detail page (examples only; prefer category pages above)."
    if kind == "primary":
        return "Important source-of-truth page for the site."
    return "Additional public page useful for site context."


def _llms_section_category_heading(industry: str | None, sample_paths: list[str]) -> str:
    ind = (industry or "").lower()
    blob = " ".join(sample_paths).lower()
    if any(x in ind for x in ("auto", "vehicle", "shop", "retail", "ecommerce", "shopping")):
        return "Product categories"
    if any(
        x in blob
        for x in (
            "car-parts",
            "car_parts",
            "battery",
            "brake",
            "engine-oil",
            "wiper",
            "accessories",
            "/tools",
            "catalog",
        )
    ):
        return "Product categories"
    return "Site sections"


def build_llms_txt_markdown(
    base: str,
    *,
    site_title: str | None,
    summary: str | None,
    page_urls: list[str],
    sitemap_urls: list[str],
    same_as: list[str],
    live_llms_fetched: bool = False,
    brand: str | None = None,
    industry: str | None = None,
    primary_cap: int = 8,
    category_cap: int = 12,
    guides_cap: int = 8,
    policy_cap: int = 8,
    optional_cap: int = 10,
    product_examples_cap: int = 3,
    same_as_cap: int = 8,
    sitemap_cap: int = 5,
    skeleton_reference: str | None = None,
) -> str:
    """Build a curated public llms.txt (https://llmstxt.org/); audit-only kwargs are ignored."""
    _ = live_llms_fetched, skeleton_reference
    parsed_base = urllib.parse.urlparse(base.rstrip("/") + "/")
    netloc = parsed_base.netloc
    h1 = clean_llms_title(site_title, netloc, brand)
    quote = clean_llms_summary(summary, h1, industry)

    intro = (
        f"This file highlights useful public pages for understanding {h1}, "
        "including key entry points, categories or major sections, support and policy information, "
        "and discovery helpers."
    )

    buckets: dict[str, list[tuple[str, int]]] = defaultdict(list)
    seen: set[str] = set()

    def consider(u: str) -> None:
        if not is_valid_llms_internal_url(u, base):
            return
        k = _llms_norm_key(u)
        if k in seen:
            return
        parsed = urllib.parse.urlparse(urllib.parse.urldefrag(u)[0])
        kind = classify_llms_internal_path(parsed.path or "/", parsed.query or "")
        if kind == "skip":
            return
        seen.add(k)
        score = llms_internal_score(u, kind)
        buckets[kind].append((u, score))

    for u in page_urls:
        consider(u)

    home = urllib.parse.urlunparse(
        (parsed_base.scheme, parsed_base.netloc, "/", "", "", "")
    )
    if _llms_norm_key(home) not in seen:
        consider(home)

    def pick(kind: str, cap: int) -> list[str]:
        rows = buckets.get(kind) or []
        rows.sort(key=lambda t: (-t[1], t[0]))
        out: list[str] = []
        for u, _ in rows:
            if u not in out:
                out.append(u)
            if len(out) >= cap:
                break
        return out

    primary_urls = pick("primary", primary_cap)
    if not primary_urls and buckets.get("optional"):
        primary_urls = pick("optional", min(4, primary_cap))

    category_urls = pick("category", category_cap)
    guides_urls = pick("guides", guides_cap)
    policy_urls = pick("policy", policy_cap)
    optional_urls = pick("optional", optional_cap)
    optional_urls = [u for u in optional_urls if u not in primary_urls]
    product_urls = pick("product_optional", product_examples_cap)

    chunks: list[str] = [f"# {h1}", "", f"> {quote}", "", intro, ""]

    def section_primary(urls: list[str]) -> None:
        if not urls:
            return
        chunks.extend(["", "## Primary pages", ""])
        for u in urls:
            chunks.append(
                _llms_md_link_line(
                    _llms_link_label_for_page(u, base),
                    u,
                    llms_link_note_for(u, "primary", base),
                )
            )

    section_primary(primary_urls)

    cat_heading = _llms_section_category_heading(
        industry, [urllib.parse.urlparse(u).path for u in category_urls]
    )
    if category_urls:
        chunks.extend(["", f"## {cat_heading}", ""])
        for u in category_urls:
            k = classify_llms_internal_path(
                urllib.parse.urlparse(u).path, urllib.parse.urlparse(u).query
            )
            chunks.append(
                _llms_md_link_line(
                    _llms_link_label_for_page(u, base),
                    u,
                    llms_link_note_for(u, k, base),
                )
            )

    if guides_urls:
        chunks.extend(["", "## Guides and support", ""])
        for u in guides_urls:
            chunks.append(
                _llms_md_link_line(
                    _llms_link_label_for_page(u, base),
                    u,
                    llms_link_note_for(u, "guides", base),
                )
            )

    if policy_urls:
        chunks.extend(["", "## Customer information", ""])
        for u in policy_urls:
            chunks.append(
                _llms_md_link_line(
                    _llms_link_label_for_page(u, base),
                    u,
                    llms_link_note_for(u, "policy", base),
                )
            )

    sm_lines: list[str] = []
    for su in sitemap_urls[:sitemap_cap]:
        if is_valid_llms_internal_url(su, base):
            label = f"Sitemap ({urllib.parse.urlparse(su).path.strip('/') or 'index'})"
            sm_lines.append(
                _llms_md_link_line(
                    label,
                    su,
                    "Machine-readable sitemap for broad URL discovery.",
                )
            )
    if sm_lines:
        chunks.extend(["", "## Sitemap & discovery", "", *sm_lines])

    if same_as:
        sa_urls = [u for u in same_as if is_valid_llms_external_https(u)][:same_as_cap]
        if sa_urls:
            chunks.extend(["", "## Entity profiles", ""])
            for u in sa_urls:
                chunks.append(
                    _llms_md_link_line(
                        _llms_same_as_label(u),
                        u,
                        "Official third-party profile or corroboration link for the brand.",
                    )
                )

    if product_urls:
        chunks.extend(["", "## Representative product pages", ""])
        for u in product_urls:
            chunks.append(
                _llms_md_link_line(
                    _llms_link_label_for_page(u, base),
                    u,
                    llms_link_note_for(u, "product_optional", base),
                )
            )

    if optional_urls:
        chunks.extend(["", "## Optional", ""])
        for u in optional_urls:
            k = classify_llms_internal_path(
                urllib.parse.urlparse(u).path, urllib.parse.urlparse(u).query
            )
            chunks.append(
                _llms_md_link_line(
                    _llms_link_label_for_page(u, base),
                    u,
                    llms_link_note_for(u, k, base),
                )
            )

    return "\n".join(chunks).rstrip() + "\n"


def collect_same_as_from_obj(obj: Any, out: list[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = k.lower() if isinstance(k, str) else k
            if lk == "sameas":
                if isinstance(v, str):
                    if v not in out:
                        out.append(v)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, str) and item not in out:
                            out.append(item)
            else:
                collect_same_as_from_obj(v, out)
    elif isinstance(obj, list):
        for item in obj:
            collect_same_as_from_obj(item, out)


def json_ld_script_inner_bodies(html: str) -> list[str]:
    """Extract inner HTML of script tags that declare application/ld+json (allows charset suffix)."""
    bodies: list[str] = []
    for m in re.finditer(r"<script([^>]*)>(.*?)</script>", html, re.IGNORECASE | re.DOTALL):
        tag = m.group(1)
        if not re.search(r"\btype\s*=\s*['\"]", tag, re.IGNORECASE):
            continue
        if not re.search(r"application/ld\+json", tag, re.IGNORECASE):
            continue
        bodies.append(m.group(2))
    return bodies


def flatten_ld_nodes(data: Any) -> list[dict[str, Any]]:
    """Expand JSON-LD roots into node dicts (handles top-level arrays and @graph)."""
    if isinstance(data, list):
        out: list[dict[str, Any]] = []
        for item in data:
            out.extend(flatten_ld_nodes(item))
        return out
    if isinstance(data, dict):
        g = data.get("@graph")
        if isinstance(g, list) and g:
            nodes = [x for x in g if isinstance(x, dict)]
            return nodes if nodes else [data]
        return [data]
    return []


def ld_context_uses_http(obj: Any) -> bool:
    """True if @context uses http://schema.org (valid but should be modernised to https)."""
    if isinstance(obj, dict):
        c = obj.get("@context")
        if isinstance(c, str) and c.strip().rstrip("/").lower() == "http://schema.org":
            return True
        if isinstance(c, list):
            for x in c:
                if isinstance(x, str) and x.strip().rstrip("/").lower() == "http://schema.org":
                    return True
        if isinstance(c, dict):
            for v in c.values():
                if isinstance(v, str) and v.strip().rstrip("/").lower() == "http://schema.org":
                    return True
                if ld_context_uses_http(v):
                    return True
        g = obj.get("@graph")
        if isinstance(g, list) and any(ld_context_uses_http(x) for x in g):
            return True
    if isinstance(obj, list):
        return any(ld_context_uses_http(x) for x in obj)
    return False


def ld_contains_type(obj: Any, want: str) -> bool:
    w = want.lower()

    def _walk(o: Any) -> bool:
        if isinstance(o, dict):
            t = o.get("@type")
            if isinstance(t, str) and t.lower() == w:
                return True
            if isinstance(t, list) and any(isinstance(x, str) and x.lower() == w for x in t):
                return True
            return any(_walk(v) for v in o.values())
        if isinstance(o, list):
            return any(_walk(x) for x in o)
        return False

    return _walk(obj)


def schema_types_from_nodes(nodes: list[dict[str, Any]]) -> list[str]:
    types: list[str] = []
    for node in nodes:
        t = node.get("@type")
        if isinstance(t, str):
            types.append(t)
        elif isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    types.append(x)
    return types


def _rough_visible_text(html: str, max_chars: int = 48000) -> str:
    """Strip scripts/styles/tags for lightweight listing vs editorial heuristics."""
    t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
    t = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html_unescape_json(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:max_chars]


def compute_page_content_signals(html: str) -> dict[str, Any]:
    """
    Heuristics for product-grid vs editorial prose (crawl-only; not manual passage review).
    Stored per page for citability / query-coverage caps in create-report.py.
    """
    text = _rough_visible_text(html)
    lower = text.lower()
    html_l = html.lower()
    add_n = lower.count("add to basket") + lower.count("add to cart") + lower.count("add to bag")
    price_n = len(re.findall(r"£\s*\d[\d,.]*|€\s*\d[\d,.]*|\$\s*\d[\d,.]*", text))
    fs = sum(
        lower.count(x)
        for x in (
            "sort by",
            "filter",
            "refine",
            "showing ",
            " products",
            " per page",
            "items per page",
            "results per page",
        )
    )
    product_kw = (
        html_l.count("data-product")
        + html_l.count("data-product-id")
        + html_l.count("itemtype=")
        + lower.count(" sku:")
    )
    is_grid = bool(
        add_n >= 3
        or price_n >= 8
        or fs >= 3
        or (product_kw >= 6 and price_n >= 4)
        or (add_n >= 2 and price_n >= 6)
    )

    sents = re.split(r"(?<=[.!?])\s+", text)
    meaningful_sents = [
        s
        for s in sents
        if len(s.strip()) > 80
        and not re.search(r"£\s*\d|€\s*\d|\$\s*\d", s)
        and "add to basket" not in s.lower()
        and "add to cart" not in s.lower()
    ]
    markers = sum(
        lower.count(x)
        for x in (
            "how to",
            "you should",
            "choose ",
            "depends on",
            "for example",
            "the difference",
            "recommended",
            " steps",
            " guide",
            "because ",
            "troubleshoot",
            "compared to",
        )
    )
    q_like = len(
        re.findall(
            r"\b(what|how|why|when|which|where|should i|can i|does my|do i need)\b[^.?!]{0,120}\?",
            lower,
        )
    )
    has_editorial = len(meaningful_sents) >= 3 or markers >= 3 or q_like >= 2
    return {
        "visible_words": len(text.split()),
        "add_to_cart_n": int(add_n),
        "price_token_n": int(price_n),
        "sort_filter_n": int(fs),
        "product_html_hits": int(product_kw),
        "meaningful_sentence_n": len(meaningful_sents),
        "explanatory_markers": int(markers),
        "question_like_n": int(q_like),
        "is_product_grid": bool(is_grid),
        "has_editorial_content": bool(has_editorial),
    }


def crawl_template_hint(url: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    segs = [s for s in path.split("/") if s]
    last = segs[-1] if segs else ""
    if path in ("", "/"):
        return "homepage"
    if re.search(r"/[a-z0-9][a-z0-9-]*-\d{5,}(?:\.html?)?$", path) or re.search(r"-\d{5,}\.html?$", last):
        return "product"
    if "/p/" in path or "/product/" in path or "/products/" in path:
        return "product"
    if "/category/" in path or "/categories/" in path:
        return "category"
    if any(x in path for x in ("blog", "guide", "advice", "learn", "how-to")):
        return "article"
    if any(x in path for x in ("store", "branch", "location")):
        return "local"
    if any(x in path for x in ("help", "faq", "support")):
        return "support"
    if len(segs) >= 2 and any(
        x in path
        for x in (
            "accessories",
            "car-parts",
            "car_parts",
            "brakes",
            "batteries",
            "maintenance",
            "lighting",
            "interior",
            "exterior",
            "electrical",
            "oils",
            "tools",
            "wipers",
            "engine",
        )
    ):
        if not re.search(r"-\d{5,}\.html?$", last):
            return "category"
    if len(segs) <= 2:
        return "category"
    return "other"


def _meaningful_ld_value(value: Any) -> bool:
    if value is None:
        return False
    if value == "" or value == [] or value == {}:
        return False
    if isinstance(value, str) and value.strip().lower() in {"null", "none", "n/a", "undefined"}:
        return False
    return True


def _has_schema_type(node: dict[str, Any], name: str) -> bool:
    nl = name.lower()
    t = node.get("@type")
    if isinstance(t, str):
        return t.lower() == nl
    if isinstance(t, list):
        return any(isinstance(x, str) and x.lower() == nl for x in t)
    return False


def _first_offer_dict(offers: Any) -> dict[str, Any] | None:
    if isinstance(offers, dict):
        return offers
    if isinstance(offers, list) and offers and isinstance(offers[0], dict):
        return offers[0]
    return None


def product_markup_proxy_25(nodes: list[dict[str, Any]]) -> float:
    """Rough 0–25 richness for Product + Offer on this page (crawl-time proxy)."""
    best = 0.0
    for n in nodes:
        if not _has_schema_type(n, "Product"):
            continue
        s = 0.0
        if _meaningful_ld_value(n.get("name")):
            s += 3.0
        if _meaningful_ld_value(n.get("sku")) or _meaningful_ld_value(n.get("mpn")):
            s += 3.0
        if _meaningful_ld_value(n.get("image")):
            s += 3.0
        if _meaningful_ld_value(n.get("description")):
            s += 3.0
        if _meaningful_ld_value(n.get("brand")):
            s += 3.0
        off = _first_offer_dict(n.get("offers") or n.get("offer"))
        if isinstance(off, dict):
            if _meaningful_ld_value(off.get("price")):
                s += 3.0
            if _meaningful_ld_value(off.get("priceCurrency")):
                s += 2.0
            if _meaningful_ld_value(off.get("availability")):
                s += 2.0
            if _meaningful_ld_value(off.get("url")):
                s += 1.0
            if _meaningful_ld_value(off.get("seller")):
                s += 1.0
        if _meaningful_ld_value(n.get("@id")):
            s += 2.0
        best = max(best, s)
    return min(25.0, best)


def graph_connectivity_proxy(nodes: list[dict[str, Any]]) -> bool:
    if len(nodes) > 1:
        return True
    for n in nodes:
        if _meaningful_ld_value(n.get("@id")):
            return True
        if n.get("publisher") or n.get("isPartOf") or n.get("mainEntity"):
            return True
    return False


def parse_json_ld_blocks(html: str) -> tuple[list[dict[str, Any]], list[str], bool]:
    """Return (flattened JSON-LD node dicts, parse errors, any_http_schema_org_context)."""
    parsed_nodes: list[dict[str, Any]] = []
    errors: list[str] = []
    any_http_ctx = False
    for raw in json_ld_script_inner_bodies(html):
        text = html_unescape_json(raw.strip())
        if text.startswith("\ufeff"):
            text = text.lstrip("\ufeff")
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            errors.append(str(e))
            continue
        if ld_context_uses_http(data):
            any_http_ctx = True
        parsed_nodes.extend(flatten_ld_nodes(data))
    return parsed_nodes, errors, any_http_ctx


def try_llms_locations(base: str) -> tuple[bool, str | None, bytes]:
    paths = ["/llms.txt", "/.well-known/llms.txt"]
    for path in paths:
        url = urllib.parse.urljoin(base + "/", path.lstrip("/"))
        fr = _request(url)
        if fr.status == 200 and fr.body:
            return True, url, fr.body
    return False, None, b""


def run_site_audit(
    args: argparse.Namespace,
    base: str,
    out_dir: str,
    *,
    audit_label: str,
    tls_info: dict[str, Any],
) -> dict[str, Any]:
    """Run full crawl audit for one origin; write artifacts under out_dir and return report dict."""
    import os

    ensure_dir(out_dir)
    ensure_dir(os.path.join(out_dir, "jsonld"))
    ensure_dir(os.path.join(out_dir, "og_images"))

    report: dict[str, Any] = {
        "audit_label": audit_label,
        "base_url": base,
        "output_dir": os.path.abspath(out_dir),
        "tls": tls_info,
        "robots_txt": {
            "exists": False,
            "fetched_path": None,
            "merged_path": None,
            "template_merge": None,
        },
        "llms_txt": {
            "exists": False,
            "url": None,
            "fetched_path": None,
            "generated_path": None,
        },
        "ga4_top_pages": {
            "enabled": False,
            "requested": 0,
            "fetched": 0,
            "error": None,
            "path": None,
            "lookback": None,
        },
        "sitemap_pages_scanned": 0,
        "pages": [],
        "summary": {
            "any_json_ld": False,
            "any_og_image": False,
            "any_same_as": False,
            "unique_same_as_urls": [],
            "json_ld_home_organization": False,
            "json_ld_home_website": False,
            "json_ld_home_search_action": False,
            "json_ld_any_http_context": False,
            "json_ld_has_graph_structure": False,
            "json_ld_product_richness_sum": 0.0,
            "json_ld_product_pages_scored": 0,
            "json_ld_pages_with_product_schema": 0,
        },
    }

    robots_url = urllib.parse.urljoin(base + "/", "robots.txt")
    time.sleep(args.delay)
    robots_fr = _request(robots_url)
    robots_text: str | None = None
    if robots_fr.status == 200 and robots_fr.body:
        report["robots_txt"]["exists"] = True
        fp = write_bytes(out_dir, "robots_fetched.txt", robots_fr.body)
        report["robots_txt"]["fetched_path"] = fp
        robots_text = robots_fr.body.decode("utf-8", errors="replace")
    else:
        report["robots_txt"]["status"] = robots_fr.status
        report["robots_txt"]["error"] = robots_fr.error

    discovered_sm = parse_robots_sitemaps(robots_text or "")
    if not discovered_sm:
        discovered_sm = [urllib.parse.urljoin(base + "/", "sitemap.xml")]
    canonical_sitemap = discovered_sm[0]

    sample_path: Path = args.sample_robots
    merged_text: str
    merge_meta: dict[str, Any] | None = None
    if sample_path.is_file():
        sample_raw = sample_path.read_text(encoding="utf-8", errors="replace")
        merged_text, merge_meta = merge_robots_with_sample(robots_text or "", sample_raw, canonical_sitemap)
        report["robots_txt"]["template_merge"] = merge_meta
    else:
        merged_text = (robots_text or "").rstrip() + ("\n" if (robots_text or "").strip() else "")
        if audit_label == "primary" and not sample_path.is_file():
            print(f"Reference robots not found ({sample_path}); skipped template merge.", file=sys.stderr)

    mp = write_text(out_dir, "robots.txt", merged_text)
    report["robots_txt"]["merged_path"] = mp

    time.sleep(args.delay)
    llms_ok, llms_final, llms_body = try_llms_locations(base)
    report["llms_txt"]["exists"] = llms_ok
    if llms_ok and llms_body:
        report["llms_txt"]["url"] = llms_final
        fp_llms = write_bytes(out_dir, "llms_fetched.txt", llms_body)
        report["llms_txt"]["fetched_path"] = fp_llms

    home = base + "/"
    market_country = str(getattr(args, "market_country", "") or "").strip()
    market_country_code = str(getattr(args, "market_country_code", "") or "").strip()
    from geo_market import resolve_primary_market
    from sitemap_market import market_homepage_url, market_path_hints

    market_country, market_country_code = resolve_primary_market(market_country, market_country_code)
    market_hints = market_path_hints(market_country_code, market_country)

    sm_urls = collect_sitemap_page_urls(
        base,
        robots_text,
        max_sitemaps=args.max_sitemaps,
        max_urls=args.max_sitemap_urls,
        market_country=market_country,
        market_country_code=market_country_code,
    )

    if market_country or market_country_code:
        report["primary_market"] = {
            "country": market_country,
            "country_id": market_country_code,
            "path_hints": market_hints,
        }

    report["ga4_top_pages"] = {
        "enabled": False,
        "requested": 0,
        "fetched": 0,
        "error": None,
        "path": None,
        "lookback": None,
        "note": "GA4 top-page URL merge removed; crawl uses sitemap discovery only. Business context uses Gemini in setup.",
    }

    page_urls = list(sm_urls)
    market_home = market_homepage_url(base, market_hints) if audit_label == "primary" else None
    if market_home and market_home not in page_urls:
        page_urls.insert(0, market_home)
    if not any(is_site_home_url(u, base) for u in page_urls):
        page_urls.insert(0, home)
    seen: set[str] = set()
    ordered: list[str] = []
    for u in page_urls:
        if u not in seen:
            seen.add(u)
            ordered.append(u)
    page_urls = ordered[: args.max_sitemap_urls]

    all_same_as: set[str] = set()
    home_title: str | None = None
    home_description: str | None = None
    home_image: str | None = None

    for page_url in page_urls:
        time.sleep(args.delay)
        fr = _request(page_url)
        entry: dict[str, Any] = {
            "url": page_url,
            "http_status": fr.status,
            "fetch_error": fr.error,
            "json_ld_saved": None,
            "og_images_saved": [],
        }
        if fr.status != 200 or not fr.body:
            report["pages"].append(entry)
            continue
        entry["final_url"] = (fr.final_url or page_url or "").strip()
        try:
            html = fr.body.decode("utf-8", errors="replace")
        except Exception:
            html = fr.body.decode("latin-1", errors="replace")

        entry["x_robots_tag"] = (fr.headers or {}).get("x-robots-tag")
        gen_rm, named_rm = extract_robots_meta_for_page(html)
        entry["meta_robots_generic"] = gen_rm
        entry["meta_robots_named"] = named_rm
        entry["page_title"] = extract_html_title(html)
        entry["content_signals"] = compute_page_content_signals(html)

        blocks, _, page_http_ctx = parse_json_ld_blocks(html)
        entry["has_json_ld"] = len(blocks) > 0
        entry["json_ld_blocks"] = len(blocks)
        entry["template_hint"] = crawl_template_hint(page_url)
        entry["json_ld_types"] = schema_types_from_nodes(blocks)

        same: list[str] = []
        for b in blocks:
            collect_same_as_from_obj(b, same)
        entry["same_as"] = same
        for s in same:
            all_same_as.add(s)

        sm = report["summary"]
        if page_http_ctx:
            sm["json_ld_any_http_context"] = True
        if blocks:
            tl = {t.lower() for t in entry["json_ld_types"]}
            if is_site_home_url(page_url, base):
                if "organization" in tl:
                    sm["json_ld_home_organization"] = True
                if "website" in tl:
                    sm["json_ld_home_website"] = True
                if any(ld_contains_type(b, "SearchAction") for b in blocks):
                    sm["json_ld_home_search_action"] = True
            if graph_connectivity_proxy(blocks):
                sm["json_ld_has_graph_structure"] = True
            if any(_has_schema_type(b, "Product") for b in blocks):
                sm["json_ld_pages_with_product_schema"] = (
                    int(sm.get("json_ld_pages_with_product_schema") or 0) + 1
                )
                pr = product_markup_proxy_25(blocks)
                sm["json_ld_product_richness_sum"] = float(
                    sm.get("json_ld_product_richness_sum") or 0.0
                ) + float(pr)
                sm["json_ld_product_pages_scored"] = int(
                    sm.get("json_ld_product_pages_scored") or 0
                ) + 1

        og_urls = extract_og_images(html)
        entry["og_image_urls"] = og_urls

        if is_site_home_url(page_url, base):
            if home_title is None:
                home_title = extract_html_title(html)
            if home_description is None:
                home_description = extract_meta_description(html)
            if home_image is None and og_urls:
                home_image = urllib.parse.urljoin(page_url, og_urls[0])

        if blocks:
            report["summary"]["any_json_ld"] = True
            slug = slug_from_url(page_url)
            combined = {"@context": "https://schema.org", "@graph": blocks} if len(blocks) > 1 else blocks[0]
            jpath = os.path.join(out_dir, "jsonld", f"{slug}.json")
            with open(jpath, "w", encoding="utf-8") as jf:
                json.dump(combined, jf, indent=2, ensure_ascii=False)
            entry["json_ld_saved"] = jpath

        if og_urls:
            report["summary"]["any_og_image"] = True
        slug = slug_from_url(page_url)
        for i, og_u in enumerate(og_urls[:5]):
            abs_og = urllib.parse.urljoin(page_url, og_u)
            time.sleep(args.delay)
            og_fr = _request(abs_og)
            if og_fr.status == 200 and og_fr.body:
                ext = ".bin"
                low = abs_og.lower()
                if ".png" in low:
                    ext = ".png"
                elif ".jpg" in low or ".jpeg" in low:
                    ext = ".jpg"
                elif ".webp" in low:
                    ext = ".webp"
                elif ".gif" in low:
                    ext = ".gif"
                fname = f"{slug}_og_{i}{ext}"
                op = write_bytes(out_dir, os.path.join("og_images", fname), og_fr.body)
                entry["og_images_saved"].append({"url": abs_og, "path": op})

        report["pages"].append(entry)

    report["sitemap_pages_scanned"] = len(report["pages"])
    same_as_sorted = sorted(all_same_as)
    if all_same_as:
        report["summary"]["any_same_as"] = True
        report["summary"]["unique_same_as_urls"] = same_as_sorted
        write_text(out_dir, "same_as_urls.txt", "\n".join(same_as_sorted) + "\n")

    n_ps = int(report["summary"].get("json_ld_product_pages_scored") or 0)
    if n_ps > 0:
        report["summary"]["json_ld_avg_product_proxy"] = round(
            float(report["summary"]["json_ld_product_richness_sum"]) / n_ps, 1
        )

    jld_body = build_json_ld_txt(
        base,
        name=home_title,
        description=home_description,
        image=home_image,
        same_as=same_as_sorted,
    )
    jld_path = write_text(out_dir, "json-ld.txt", jld_body)
    report["json_ld_txt"] = {
        "path": jld_path,
        "name_source": "homepage_title" if home_title else "hostname",
    }

    llms_md = build_llms_txt_markdown(
        base,
        site_title=home_title,
        summary=home_description,
        page_urls=page_urls,
        sitemap_urls=discovered_sm,
        same_as=same_as_sorted,
        live_llms_fetched=llms_ok,
        brand=(getattr(args, "brand", None) or "").strip() or None,
        industry=(getattr(args, "industry", None) or "").strip() or None,
    )
    llms_gen_path = write_text(out_dir, "llms.txt", llms_md)
    report["llms_txt"]["generated_path"] = llms_gen_path

    try:
        from brand_visibility_scan import derive_brand_from_base, scan_brand_platforms
    except ImportError:
        derive_brand_from_base = None  # type: ignore[assignment]
        scan_brand_platforms = None  # type: ignore[assignment]

    if getattr(args, "no_brand_scan", False):
        bq = (getattr(args, "brand", None) or "").strip()
        if derive_brand_from_base is not None:
            if not bq:
                bq = derive_brand_from_base(base)
            src = "cli" if (getattr(args, "brand", None) or "").strip() else "derived_hostname"
        else:
            src = "unavailable"
        report["brand_visibility"] = {
            "skipped": True,
            "brand_query": bq,
            "brand_source": src,
            "base_url": base,
            "platforms": [],
            "method_note": "Brand visibility scan skipped (--no-brand-scan).",
        }
    elif scan_brand_platforms is None or derive_brand_from_base is None:
        report["brand_visibility"] = {
            "brand_query": "",
            "brand_source": "unavailable",
            "base_url": base,
            "platforms": [],
            "method_note": "brand_visibility_scan.py not found; off-site table unavailable.",
        }
    else:
        bq = (getattr(args, "brand", None) or "").strip()
        src = "cli" if bq else "derived_hostname"
        if not bq:
            bq = derive_brand_from_base(base)
        time.sleep(args.delay)
        report["brand_visibility"] = scan_brand_platforms(
            bq, base, delay=args.delay, brand_source=src
        )

    report["audit_inputs"] = {
        "brand": (getattr(args, "brand", None) or "").strip(),
        "industry": (getattr(args, "industry", None) or "").strip(),
    }

    write_text(out_dir, "audit_summary.json", json.dumps(report, indent=2, ensure_ascii=False))
    return report


def comparison_metrics_row(site_label: str, r: dict[str, Any]) -> dict[str, Any]:
    pages = r.get("pages") or []
    ok_200 = sum(1 for p in pages if p.get("http_status") == 200)
    with_jld = sum(1 for p in pages if p.get("has_json_ld"))
    with_og = sum(1 for p in pages if p.get("og_image_urls"))
    sm = r.get("summary") or {}
    tm = (r.get("robots_txt") or {}).get("template_merge") or {}
    return {
        "site_label": site_label,
        "base_url": r.get("base_url"),
        "output_dir": r.get("output_dir"),
        "audit_label": r.get("audit_label"),
        "robots_fetched": (r.get("robots_txt") or {}).get("exists", False),
        "llms_live": (r.get("llms_txt") or {}).get("exists", False),
        "pages_scanned": len(pages),
        "pages_http_200": ok_200,
        "pages_with_json_ld": with_jld,
        "pages_with_og_meta": with_og,
        "any_json_ld": sm.get("any_json_ld", False),
        "any_og_image": sm.get("any_og_image", False),
        "same_as_count": len(sm.get("unique_same_as_urls") or []),
        "robots_template_groups_added": tm.get("template_groups_added", 0),
    }


def write_comparison_files(
    primary_out_dir: str,
    primary_report: dict[str, Any],
    competitor_reports: list[tuple[str, dict[str, Any]]],
) -> tuple[str, str]:
    """Write comparison.json and comparison.md next to primary audit. competitor_reports: (human_label, report)."""
    import os

    rows = [comparison_metrics_row("Primary (scanned)", primary_report)]
    for label, rep in competitor_reports:
        rows.append(comparison_metrics_row(label, rep))

    bundle: dict[str, Any] = {
        "primary_base_url": primary_report.get("base_url"),
        "primary_output_dir": primary_report.get("output_dir"),
        "rows": rows,
    }
    json_path = write_text(primary_out_dir, "comparison.json", json.dumps(bundle, indent=2, ensure_ascii=False))

    lines = [
        "# Audit comparison: primary vs competitors",
        "",
        f"Primary site: `{primary_report.get('base_url')}`",
        "",
        "Metrics are from the same crawl settings (sitemap depth, page cap, delay). "
        "Use full `audit_summary.json` under each folder for details.",
        "",
        "| Site | Base URL | robots fetched | llms live | pages | HTTP 200 | pages w/ JSON-LD | pages w/ og:image meta | any JSON-LD | any og:image | sameAs # | robots template groups added |",
        "|------|----------|----------------|-----------|-------|----------|------------------|------------------------|-------------|--------------|------------|------------------------------|",
    ]
    for row in rows:
        bu = (row.get("base_url") or "").replace("|", "\\|")
        lines.append(
            "| {site} | {bu} | {rf} | {ll} | {ps} | {ok} | {pj} | {po} | {aj} | {ao} | {sa} | {tg} |".format(
                site=row["site_label"].replace("|", "\\|"),
                bu=bu,
                rf="yes" if row["robots_fetched"] else "no",
                ll="yes" if row["llms_live"] else "no",
                ps=row["pages_scanned"],
                ok=row["pages_http_200"],
                pj=row["pages_with_json_ld"],
                po=row["pages_with_og_meta"],
                aj="yes" if row["any_json_ld"] else "no",
                ao="yes" if row["any_og_image"] else "no",
                sa=row["same_as_count"],
                tg=row["robots_template_groups_added"],
            )
        )
    lines.extend(
        [
            "",
            "## Output layout",
            "",
            f"- Primary: `{primary_out_dir}`",
        ]
    )
    for label, rep in competitor_reports:
        lines.append(f"- {label}: `{rep.get('output_dir')}`")

    md_path = write_text(primary_out_dir, "comparison.md", "\n".join(lines))
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit robots, llms.txt, sitemap pages for JSON-LD / og:image / sameAs.")
    parser.add_argument("url", help="Website URL (e.g. https://example.com)")
    parser.add_argument(
        "--out",
        default="audit_output",
        help="Base directory for saved files (default: audit_output)",
    )
    parser.add_argument(
        "--max-sitemap-urls",
        type=int,
        default=80,
        help="Max page URLs to collect from sitemaps (default: 80)",
    )
    parser.add_argument(
        "--max-sitemaps",
        type=int,
        default=40,
        help="Max sitemap files to fetch when following indexes (default: 40)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Seconds between HTTP requests (default: 0.25)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Do not verify TLS certificates (insecure; use only if you trust the target)",
    )
    parser.add_argument(
        "--no-certifi",
        action="store_true",
        help="Do not use the certifi CA bundle (use Python's default store only)",
    )
    parser.add_argument(
        "--sample-robots",
        type=Path,
        default=default_sample_robots_path(),
        help="Reference robots.txt used to suggest missing directives (default: samples/robots.txt)",
    )
    parser.add_argument(
        "--sample-llms",
        type=Path,
        default=default_llms_skeleton_path(),
        help="Optional reference file for authors (not embedded into generated llms.txt). Default: samples/llms-txt-skeleton.txt",
    )
    parser.add_argument(
        "--competitor",
        action="append",
        default=[],
        dest="competitors",
        metavar="URL",
        help="Competitor site to audit with the same settings (max 5). Reports go under primary output in competitors/. Repeat flag.",
    )
    parser.add_argument(
        "--brand",
        default=None,
        metavar="NAME",
        help="Brand name for off-site visibility checks (Wikipedia, social URL probes). "
        "When set, also used as the generated llms.txt H1 site name (cleaner than the homepage HTML title). "
        "Defaults to a hostname-based guess. Same value is used for primary and competitor crawls in this run.",
    )
    parser.add_argument(
        "--no-brand-scan",
        action="store_true",
        help="Skip live brand visibility API/HTTP probes (faster; table will be empty or note-only).",
    )
    parser.add_argument(
        "--industry",
        default="",
        metavar="LABEL",
        help="Industry vertical for report context (e.g. Auto & Vehicles). Stored in audit_summary.json.",
    )
    parser.add_argument(
        "--market-country",
        default="",
        metavar="NAME",
        help="Primary market country name from wizard (e.g. United Kingdom). Guides regional sitemap prioritisation.",
    )
    parser.add_argument(
        "--market-country-code",
        default="",
        metavar="ISO2",
        help="Primary market ISO-3166-1 alpha-2 code from wizard (e.g. GB). Guides regional sitemap prioritisation.",
    )
    args = parser.parse_args()

    if len(args.competitors) > 5:
        print("Error: at most 5 --competitor URLs allowed.", file=sys.stderr)
        return 2

    tls_info = configure_tls(insecure=args.insecure, no_certifi=args.no_certifi)
    if tls_info["mode"] == "stdlib_default" and not args.insecure:
        print(
            "TLS: using Python's default CA store. If HTTPS fails with "
            "CERTIFICATE_VERIFY_FAILED, run: pip install certifi",
            file=sys.stderr,
        )

    try:
        primary_base = normalize_base(args.url)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    import os

    out_root = args.out
    site_dir = safe_dir_name(primary_base)
    out_dir = os.path.join(out_root, site_dir)

    primary_report = run_site_audit(
        args,
        primary_base,
        out_dir,
        audit_label="primary",
        tls_info=tls_info,
    )

    competitor_bundle: list[tuple[str, dict[str, Any]]] = []
    for i, comp_raw in enumerate(args.competitors, start=1):
        try:
            comp_base = normalize_base(comp_raw)
        except ValueError as e:
            print(f"Skipping competitor ({comp_raw}): {e}", file=sys.stderr)
            continue
        if comp_base.rstrip("/") == primary_base.rstrip("/"):
            print(f"Skipping competitor {i}: same origin as primary.", file=sys.stderr)
            continue
        comp_out = os.path.join(out_dir, "competitors", safe_dir_name(comp_base))
        label = f"Competitor {i}"
        print(f"{label} ({comp_base}) …", file=sys.stderr)
        comp_report = run_site_audit(
            args,
            comp_base,
            comp_out,
            audit_label=f"competitor_{i}",
            tls_info=tls_info,
        )
        competitor_bundle.append((label, comp_report))

    if competitor_bundle:
        _cj, cmp_md = write_comparison_files(out_dir, primary_report, competitor_bundle)
        primary_report["comparison"] = {
            "markdown_path": cmp_md,
            "json_path": _cj,
            "competitors": [rep.get("base_url") for _, rep in competitor_bundle],
        }
        write_text(out_dir, "audit_summary.json", json.dumps(primary_report, indent=2, ensure_ascii=False))

    report = primary_report
    base = primary_base

    print(f"Base: {base}")
    print(f"Output: {report['output_dir']}")
    print(f"robots.txt: {'yes' if report['robots_txt']['exists'] else 'no'}")
    print(f"llms.txt (live): {'yes' if report['llms_txt']['exists'] else 'no'}")
    print(f"llms.txt (generated): {report['llms_txt']['generated_path']}")
    print(f"Pages scanned: {report['sitemap_pages_scanned']}")
    print(f"json-ld.txt: {report['json_ld_txt']['path']}")
    print(f"JSON-LD found on any page: {report['summary']['any_json_ld']}")
    print(f"og:image found on any page: {report['summary']['any_og_image']}")
    print(f"sameAs links found: {report['summary']['any_same_as']}")
    if report["summary"]["unique_same_as_urls"]:
        print("sameAs URLs:")
        for u in report["summary"]["unique_same_as_urls"]:
            print(f"  - {u}")
    if competitor_bundle:
        print(f"comparison.md: {report.get('comparison', {}).get('markdown_path', '')}")
        print(f"comparison.json: {report.get('comparison', {}).get('json_path', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
