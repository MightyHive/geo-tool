#!/usr/bin/env python3
"""
Full GEO audit pipeline: run crawl-site.py (primary + competitors when configured), then
synthesize report.html and report_slides.html.

  python3 create-report.py https://example.com --competitor https://peer.com

  python3 create-report.py https://example.com --brand "Acme" --industry "Shopping" \\
      --accept-ai-defaults

  When ``--accept-ai-defaults`` is set and you omit ``--competitor``, Gemini suggests up to five
  competitor URLs. After reports are built, Gemini also writes ``onboarding_context.json`` (prompts +
  category labels) for the Streamlit Prompt performance tab. Requires ``GEMINI_API_KEY`` / Vertex
  env (same as ``competitor_suggest``). Use ``--ga4-property`` for GA4-backed category heuristics.
  Primary market for those Gemini calls follows ``GEO_PRIMARY_MARKET_COUNTRY`` /
  ``GEO_PRIMARY_MARKET_COUNTRY_ID`` (or ``PRIMARY_MARKET_*`` / ``GA4_PRIMARY_MARKET_*`` aliases) when set.

  python3 create-report.py --only-report audit_output/example.com_abc123

Optional GA4 appendix: pass --ga4-property (or env GA4_PROPERTY_ID) to pull traffic JSON
before rendering and to **prepend the top 100 viewed URLs** to the primary crawl (requires
google-analytics-data + credentials). The file includes monthly session trend by channel bucket, optional
monthly AI % of revenue and misallocated sources (custom channel groups via ga4_data_api),
and source/medium gaps. Or place ga4_traffic.json manually beside audit_summary.json.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import os
import html
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.robotparser import RobotFileParser

from report_copy import (
    client_friendly_finding,
    client_friendly_text,
    is_manual_caveat,
    is_policy_only_item,
    prepare_report_priorities,
    priorities_for_executive,
)

from geo_market import resolve_primary_market

from geo_app_env import ASSETS_ROOT, BACKEND_ROOT, REPO_ROOT
from report_score import format_report_score, score_label as _score_label, score_tone as _tone_class

CRAWL_SCRIPT = BACKEND_ROOT / "crawl-site.py"


def meaningful_value(value: Any) -> bool:
    """True if a JSON-LD field should count as present (excludes null-like sentinels)."""
    if value is None:
        return False
    if value == "" or value == [] or value == {}:
        return False
    if isinstance(value, str) and value.strip().lower() in {"null", "none", "n/a", "undefined"}:
        return False
    return True


def iter_schema_nodes(data: Any) -> Any:
    """Yield dict nodes from JSON-LD (top-level array, @graph, or single object)."""
    if isinstance(data, list):
        for item in data:
            yield from iter_schema_nodes(item)
    elif isinstance(data, dict):
        g = data.get("@graph")
        if isinstance(g, list) and g:
            for item in g:
                yield from iter_schema_nodes(item)
        else:
            yield data


def get_schema_types_from_data(data: Any) -> list[str]:
    types: list[str] = []
    for node in iter_schema_nodes(data):
        if not isinstance(node, dict):
            continue
        t = node.get("@type")
        if isinstance(t, str):
            types.append(t)
        elif isinstance(t, list):
            for x in t:
                if isinstance(x, str):
                    types.append(x)
    return types


def is_schema_context(value: Any) -> bool:
    """True if @context resolves to schema.org (http or https)."""
    if isinstance(value, str):
        return value.strip().rstrip("/") in {"http://schema.org", "https://schema.org"}
    if isinstance(value, dict):
        return any(is_schema_context(v) for v in value.values())
    if isinstance(value, list):
        return any(is_schema_context(v) for v in value)
    return False


def classify_page_type(url: str, title: str = "", schema_types: list[str] | None = None) -> str:
    """URL template hint (kept in sync with crawl-site crawl_template_hint)."""
    del title, schema_types  # reserved for future signals
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


def _page_template_hint(page: dict[str, Any]) -> str:
    return str(page.get("template_hint") or "").strip() or classify_page_type(str(page.get("url") or ""))


def _aggregate_crawl_content_signals(pages: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up per-page content_signals for caps and confidence labels."""
    ok = [p for p in pages if p.get("http_status") == 200]
    sigs = [p.get("content_signals") for p in ok if isinstance(p.get("content_signals"), dict)]
    if not sigs:
        return {
            "has_body_signals": False,
            "listing_fraction": 0.0,
            "editorial_fraction": 0.0,
            "grid_without_editorial": 0.0,
            "confidence": "low",
            "n_pages": len(ok),
        }
    n = len(sigs)
    lf = sum(1 for s in sigs if s.get("is_product_grid")) / n
    ed = sum(1 for s in sigs if s.get("has_editorial_content")) / n
    gwo = sum(1 for s in sigs if s.get("is_product_grid") and not s.get("has_editorial_content")) / n
    if lf > 0.55 and ed < 0.18:
        conf = "low"
    elif ed > 0.32 and lf < 0.35:
        conf = "high"
    else:
        conf = "medium"
    return {
        "has_body_signals": True,
        "listing_fraction": lf,
        "editorial_fraction": ed,
        "grid_without_editorial": gwo,
        "confidence": conf,
        "n_pages": len(ok),
    }


def _page_passage_citability_score(page: dict[str, Any]) -> float:
    """0–100 passage / editorial proxy for one page (listing pages capped when grid-heavy)."""
    sig = page.get("content_signals")
    hint = _page_template_hint(page)
    if not isinstance(sig, dict):
        return 44.0 if hint not in ("article", "support") else 52.0

    ms = int(sig.get("meaningful_sentence_n") or 0)
    em = int(sig.get("explanatory_markers") or 0)
    qq = int(sig.get("question_like_n") or 0)
    raw = 20.0 + min(40.0, 2.0 * float(ms)) + min(24.0, 2.2 * float(em)) + min(14.0, 3.5 * float(qq))
    raw = min(100.0, raw)
    grid = bool(sig.get("is_product_grid"))
    ed = bool(sig.get("has_editorial_content"))

    if hint in ("category",) or (hint == "other" and grid):
        if grid and not ed:
            raw = min(raw, 45.0)
        elif grid and ms < 3:
            raw = min(raw, 60.0)
        elif ed:
            raw = min(raw, 88.0)
    if hint == "product":
        if not ed:
            raw = min(raw, 70.0)
    if hint in ("article", "support"):
        raw = min(100.0, raw + 7.0)
    if hint == "homepage":
        raw = min(100.0, raw + 3.0)
    return float(raw)


def _passage_citability_proxy_for_audit(pages: list[dict[str, Any]], agg: dict[str, Any]) -> float:
    ok = [p for p in pages if p.get("http_status") == 200]
    if not ok:
        return 38.0
    scores = [_page_passage_citability_score(p) for p in ok]
    return float(sum(scores) / len(scores))


def _query_coverage_passage_proxy_for_audit(
    pages: list[dict[str, Any]], agg: dict[str, Any]
) -> float:
    """
    Query coverage from URL shape is weak alone; require editorial / answer proxies when body signals exist.
    """
    ok = [p for p in pages if p.get("http_status") == 200]
    if not ok:
        return 36.0
    depths = [_peer_url_path_segments(str(p.get("url") or "")) for p in ok]
    slug_part = min(100.0, 10.0 + 7.5 * (sum(depths) / len(depths)))
    if agg.get("has_body_signals"):
        slug_part *= max(0.35, 1.0 - 0.55 * float(agg.get("listing_fraction") or 0.0))
    body_part = _passage_citability_proxy_for_audit(pages, agg)
    if not agg.get("has_body_signals"):
        body_part = min(body_part, 52.0)
    return _clamp100(0.22 * slug_part + 0.78 * body_part)


def _audit_url_is_home(url: str, base: str) -> bool:
    a = urllib.parse.urldefrag(url.strip())[0]
    b = urllib.parse.urldefrag(base.rstrip("/") + "/")[0]
    pa, pb = urllib.parse.urlparse(a), urllib.parse.urlparse(b)
    path_a = (pa.path or "/").rstrip("/")
    path_b = (pb.path or "/").rstrip("/")
    return (
        pa.scheme.lower() == pb.scheme.lower()
        and pa.netloc.lower() == pb.netloc.lower()
        and path_a == path_b
    )


def _ld_nested_has_type(obj: Any, want: str) -> bool:
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


def _enrich_audit_json_ld_signals(audit: dict[str, Any]) -> None:
    """Merge homepage JSON-LD signals from jsonld/*.json when summary flags are absent or false."""
    sm = audit.setdefault("summary", {})
    base = (audit.get("base_url") or "").strip().rstrip("/")
    if not base:
        return
    for p in audit.get("pages") or []:
        if p.get("http_status") != 200:
            continue
        jpath = p.get("json_ld_saved")
        if not jpath:
            continue
        path = Path(jpath)
        if not path.is_file():
            continue
        url = str(p.get("url") or "")
        if not _audit_url_is_home(url, base):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError):
            continue
        tl = {t.lower() for t in get_schema_types_from_data(raw)}
        if "organization" in tl:
            sm["json_ld_home_organization"] = True
        if "website" in tl:
            sm["json_ld_home_website"] = True
        if _ld_nested_has_type(raw, "SearchAction"):
            sm["json_ld_home_search_action"] = True
        if isinstance(raw, dict):
            c = raw.get("@context")
            if isinstance(c, str) and c.strip().rstrip("/").lower() == "http://schema.org":
                sm["json_ld_any_http_context"] = True


def _load_geo_report_css(audit_dir: Path) -> str:
    """Prefer per-audit design/report-styles.css, then repo design/report-styles.css."""
    audit_css = audit_dir / "design" / "report-styles.css"
    repo_css = ASSETS_ROOT / "design" / "report-styles.css"
    if audit_css.is_file():
        return audit_css.read_text(encoding="utf-8")
    if repo_css.is_file():
        return repo_css.read_text(encoding="utf-8")
    return (
        "/* Missing design/report-styles.css — restore from repo. */\n"
        "body{font-family:system-ui,sans-serif;margin:1rem;line-height:1.5;}"
    )

# AI crawlers benchmarked in report UI.
# policy: allow = GEO recommends homepage fetch; block = recommend blocking; context = team decision (not scored).
@dataclass(frozen=True)
class AICrawlerSpec:
    token: str
    tier: int
    rec_label: str
    reason: str
    policy: Literal["allow", "block", "context"]


AI_CRAWLER_SPECS: tuple[AICrawlerSpec, ...] = (
    # Tier 1 — AI search / retrieval
    AICrawlerSpec("OAI-SearchBot", 1, "ALLOW", "ChatGPT Search / search retrieval", "allow"),
    AICrawlerSpec("ChatGPT-User", 1, "ALLOW", "User-initiated browsing", "allow"),
    AICrawlerSpec("GPTBot", 1, "ALLOW", "OpenAI crawler; policy-sensitive visibility", "allow"),
    AICrawlerSpec("ClaudeBot", 1, "ALLOW", "Claude web access / retrieval", "allow"),
    AICrawlerSpec("PerplexityBot", 1, "ALLOW", "Perplexity search and citations", "allow"),
    # Foundational search crawlers (10 pts each in composite)
    AICrawlerSpec("Googlebot", 2, "ALLOW", "Google Search; foundational for AI Overviews", "allow"),
    AICrawlerSpec("Bingbot", 2, "ALLOW", "Bing index; Copilot / Microsoft search", "allow"),
    # Tier 2 — broader AI / assistant ecosystem (15 pts shared across 6)
    AICrawlerSpec("Google-Extended", 2, "ALLOW", "Gemini apps / Vertex-style use; not normal Search indexing", "allow"),
    AICrawlerSpec("GoogleOther", 2, "ALLOW", "Google non-search product/research crawls", "allow"),
    AICrawlerSpec("Applebot", 2, "ALLOW", "Siri / Spotlight / Apple search features", "allow"),
    AICrawlerSpec("Applebot-Extended", 2, "ALLOW", "Apple Intelligence extension; policy-sensitive", "allow"),
    AICrawlerSpec("Amazonbot", 2, "ALLOW", "Alexa / Amazon AI discovery", "allow"),
    AICrawlerSpec("FacebookBot", 2, "ALLOW", "Meta AI-related crawling", "allow"),
    # Tier 3 — training / dataset (informational in GEO stance column)
    AICrawlerSpec("CCBot", 3, "Context", "Common Crawl / training datasets", "context"),
    AICrawlerSpec("anthropic-ai", 3, "Context", "Anthropic training/research (not ClaudeBot)", "context"),
    AICrawlerSpec("cohere-ai", 3, "Context", "Cohere model training", "context"),
    AICrawlerSpec("Bytespider", 3, "BLOCK", "ByteDance; market/abuse considerations", "block"),
    AICrawlerSpec("meta-externalagent", 3, "Context", "Meta external AI data collection", "context"),
)

# Composite score buckets: Tier 1 (45) + Googlebot/Bingbot (20) + selected Tier 2 eco (15).
CRAWLER_TIER1_TOKENS: frozenset[str] = frozenset(
    {"OAI-SearchBot", "ChatGPT-User", "GPTBot", "ClaudeBot", "PerplexityBot"}
)
CRAWLER_FOUNDATIONAL_TOKENS: frozenset[str] = frozenset({"Googlebot", "Bingbot"})
CRAWLER_TIER2_ECO_TOKENS: frozenset[str] = frozenset(
    {
        "Google-Extended",
        "GoogleOther",
        "Applebot",
        "Applebot-Extended",
        "Amazonbot",
        "FacebookBot",
    }
)

AI_CRAWLER_TOKENS: tuple[str, ...] = tuple(s.token for s in AI_CRAWLER_SPECS)

SOCIAL_VISIBILITY_HOSTS: tuple[str, ...] = (
    "youtube.com",
    "youtu.be",
    "linkedin.com",
    "reddit.com",
    "wikipedia.org",
    "wikidata.org",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
)

# sameAs hosts counted toward "other official profiles" in brand visibility scoring (not the four scan platforms).
_BRAND_OTHER_PROFILE_HOSTS: frozenset[str] = frozenset(
    {
        "wikidata.org",
        "twitter.com",
        "x.com",
        "facebook.com",
        "instagram.com",
        "github.com",
        "crunchbase.com",
    }
)

# Three agent categories (must sum to 100).
DEFAULT_WEIGHTS: dict[str, float] = {
    "ai_visibility": 40.0,
    "technical_setup": 30.0,
    "content_structure": 30.0,
}

# Sidebar / CLI industry list (Streamlit dropdown); also accepted via --industry on crawl.
# Aligned with Google Ads vertical-style categories used in the UI.
COMMON_INDUSTRIES: tuple[str, ...] = (
    "Arts & Entertainment",
    "Auto & Vehicles",
    "Beauty & Fitness",
    "Books & Literature",
    "Business & Industrial",
    "Computers & Electronics",
    "Finance",
    "Food & Drink",
    "Games",
    "Health",
    "Home & Garden",
    "Internet & Telecom",
    "Jobs & Education",
    "Law & Government",
    "News",
    "Online Communities",
    "People & Society",
    "Pets & Animals",
    "Real Estate",
    "Reference",
    "Science",
    "Shopping",
    "Sports",
    "Travel",
    "Other Business Activity",
)


def _default_sample_robots() -> Path:
    return ASSETS_ROOT / "reference" / "robots.txt"


def _default_sample_llms() -> Path:
    return ASSETS_ROOT / "reference" / "llms-txt-skeleton.txt"


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


def _homepage_from_audit(audit: dict[str, Any]) -> str:
    raw = (audit.get("base_url") or "").strip()
    if not raw:
        return "https://example.org/"
    try:
        b = normalize_base(raw)
    except ValueError:
        return "https://example.org/"
    return b + "/" if not b.endswith("/") else b


def _robots_home_and_fetch_url(homepage: str) -> tuple[str, str]:
    """Homepage URL with trailing slash, and absolute robots.txt URL for RobotFileParser.set_url."""
    home = homepage if homepage.endswith("/") else homepage + "/"
    robots_u = urllib.parse.urljoin(home, "robots.txt")
    return home, robots_u


def build_crawl_argv(args: argparse.Namespace) -> list[str]:
    max_pages = int(getattr(args, "max_sitemap_urls", 80))
    cmd: list[str] = [
        str(args.url),
        "--out",
        str(args.out),
        "--max-sitemap-urls",
        str(max_pages),
        "--max-sitemaps",
        str(args.max_sitemaps),
        "--delay",
        str(args.delay),
        "--sample-robots",
        str(args.sample_robots),
        "--sample-llms",
        str(args.sample_llms),
        "--mimic-urls-per-bot",
        str(max(1, min(6, int(getattr(args, "mimic_urls_per_bot", 4) or 4)))),
        "--mimic-crawl-max-pages",
        str(max(2, min(40, int(getattr(args, "mimic_crawl_max_pages", 12) or 12)))),
        "--mimic-crawl-depth",
        str(max(0, min(4, int(getattr(args, "mimic_crawl_depth", 2) or 2)))),
        "--mimic-crawl-links-per-page",
        str(max(2, min(60, int(getattr(args, "mimic_crawl_links_per_page", 20) or 20)))),
    ]
    if args.insecure:
        cmd.append("--insecure")
    if args.no_certifi:
        cmd.append("--no-certifi")
    if getattr(args, "brand", None):
        cmd.extend(["--brand", str(args.brand)])
    if getattr(args, "industry", None):
        ind = str(args.industry).strip()
        if ind:
            cmd.extend(["--industry", ind])
    if getattr(args, "no_brand_scan", False):
        cmd.append("--no-brand-scan")
    mcc = str(getattr(args, "market_country", "") or "").strip()
    mid = str(getattr(args, "market_country_code", "") or "").strip()
    if mcc:
        cmd.extend(["--market-country", mcc])
    if mid:
        cmd.extend(["--market-country-code", mid])
    extra = getattr(args, "extra_markets_json", "")
    if str(extra or "").strip():
        cmd.extend(["--extra-markets-json", str(extra)])
    for c in args.competitors:
        cmd.extend(["--competitor", c])
    # Pass user-selected crawl URLs from wizard (loaded from onboarding_context.json)
    include_urls = getattr(args, "include_urls", None) or []
    if include_urls:
        cmd.extend(["--include-urls", json.dumps(include_urls)])
    return cmd


def _apply_market_from_onboarding(args: argparse.Namespace, audit_dir: Path) -> None:
    """Fill market CLI args from wizard onboarding when not passed explicitly."""
    if str(getattr(args, "market_country", "") or "").strip() or str(
        getattr(args, "market_country_code", "") or ""
    ).strip():
        return
    ob_path = audit_dir / "onboarding_context.json"
    if not ob_path.is_file():
        return
    try:
        ob = json.loads(ob_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(ob, dict):
        return
    mcc = str(ob.get("geo_market_country") or "").strip()
    mid = str(ob.get("geo_market_country_code") or "").strip()
    if not mcc and not mid:
        pm = ob.get("ga4_primary_market")
        if isinstance(pm, dict):
            mcc = str(pm.get("country") or "").strip()
            mid = str(pm.get("country_id") or "").strip()
    if mcc:
        args.market_country = mcc
    if mid:
        args.market_country_code = mid


def _guess_brand_from_url_cli(url: str) -> str:
    base = normalize_base(url)
    host = (urllib.parse.urlparse(base).hostname or "").lower().replace("www.", "")
    if not host:
        return ""
    return host.split(".")[0].replace("-", " ").strip().title() or host


def _cli_apply_accept_ai_defaults(args: argparse.Namespace) -> None:
    """If ``--accept-ai-defaults`` and no ``--competitor``, append Gemini-suggested competitor URLs."""
    if not getattr(args, "accept_ai_defaults", False):
        return
    if args.competitors:
        return
    brand = (getattr(args, "brand", None) or "").strip() or _guess_brand_from_url_cli(str(args.url or ""))
    ind = (getattr(args, "industry", None) or "").strip()
    mcc, mid = resolve_primary_market("", "")
    try:
        from competitor_suggest import suggest_competitor_urls

        urls = suggest_competitor_urls(
            brand,
            primary_url=str(args.url or "").strip(),
            industry=ind,
            max_suggestions=5,
            market_country=mcc,
            market_country_code=mid,
        )
    except Exception as e:
        print(f"Warning: --accept-ai-defaults could not fetch Gemini competitors ({e})", file=sys.stderr)
        return
    args.competitors.extend(urls)
    print(f"--accept-ai-defaults: added {len(urls)} Gemini competitor URL(s).", file=sys.stderr)


def _cli_write_onboarding_context(audit_dir: Path, args: argparse.Namespace) -> None:
    """Write ``onboarding_context.json`` using Gemini prompts (Streamlit-compatible shape)."""
    if not getattr(args, "accept_ai_defaults", False):
        return
    brand = (getattr(args, "brand", None) or "").strip() or _guess_brand_from_url_cli(str(args.url or ""))
    ind = (getattr(args, "industry", None) or "").strip()
    cat_labels = [ind] if ind else [f"{_guess_brand_from_url_cli(str(args.url or ''))} catalog".strip()]
    cat_labels = [str(c).strip() for c in cat_labels if str(c).strip()]
    if not cat_labels:
        cat_labels = ["General product discovery"]
    mcc, mid = resolve_primary_market("", "")
    try:
        from prompt_suggest import suggest_ai_platform_prompts

        prompts = suggest_ai_platform_prompts(
            cat_labels,
            brand_name=brand,
            site_url=str(args.url or "").strip(),
            industry=ind,
            max_prompts=10,
            market_country=mcc,
            market_country_code=mid,
        )
    except Exception as e:
        print(f"Warning: --accept-ai-defaults could not generate Gemini prompts ({e})", file=sys.stderr)
        prompts = []
    comp_urls = [str(c).strip() for c in (args.competitors or []) if str(c).strip()][:10]
    comp_detail: list[dict[str, str]] = []
    for u in comp_urls:
        raw = u if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u) else "https://" + u
        try:
            p = urllib.parse.urlparse(raw)
            host = (p.netloc or "").lower().replace("www.", "") or u
        except Exception:
            host = u
        brand_guess = host.split(".")[0].replace("-", " ").title() if host else u
        comp_detail.append({"competitor_brand": brand_guess, "competitor_website": u})
    site_u = str(args.url or "").strip()
    payload: dict[str, Any] = {
        "brand_name_used": brand,
        "brand_website_used": site_u,
        "industry_used": ind,
        "ga4_suggested_brand_name": None,
        "ga4_suggested_site_url": None,
        "ga4_suggested_industry": None,
        "accepted_categories": list(cat_labels),
        "accepted_products": [],
        "accepted_competitors": list(comp_urls),
        "ga4_top_pages": [],
        "suggested_prompts": prompts,
        "product_service_prompts": list(prompts),
        "products_and_services": list(cat_labels),
        "competitors_detail": comp_detail,
        "products_and_services_rows": [],
        "prompt_category_labels": list(cat_labels),
        "ga4_primary_market": (
            {"country": mcc, "country_id": mid, "active_users": None}
            if (mcc or mid)
            else None
        ),
    }
    try:
        (audit_dir / "onboarding_context.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (audit_dir / "products_and_services.json").write_text(
            json.dumps({"website_url": site_u, "products_and_services": list(cat_labels)}, indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        (audit_dir / "competitors.json").write_text(
            json.dumps({"website_url": site_u, "competitors": comp_detail}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {audit_dir / 'onboarding_context.json'}", file=sys.stderr)
    except OSError as e:
        print(f"Warning: could not write onboarding_context.json ({e})", file=sys.stderr)


@dataclass
class AgentSubResult:
    """One skill-backed sub-score inside an agent category."""

    key: str
    title: str
    skill_md: str
    score: float
    detail: str
    strengths: list[str]
    improvements: list[str]


@dataclass
class AgentCategoryResult:
    """Report category owned by one agent (e.g. AI Visibility 55%)."""

    key: str
    title: str
    weight: float
    score: float  # 0–100 (mean of subs)
    detail: str
    subs: list[AgentSubResult]
    strengths: list[str]
    improvements: list[str]
    scorecard_subtitle: str = ""


def _category_scorecard_definitions() -> dict[str, str]:
    """Single-line “Measures whether…” copy for top metric cards (skills/create-report.md)."""
    return {
        "ai_visibility": (
            "Measures whether AI search tools can recognise the brand and use the site's pages "
            "as clear, cite-worthy answers."
        ),
        "technical_setup": (
            "Measures whether search engines and AI tools can access the site, find the right pages, "
            "and read the important content reliably."
        ),
        "content_structure": (
            "Measures whether the site's content is helpful, trustworthy, well organised, "
            "and easy for AI systems to understand."
        ),
    }


def category_card_tagline(category_key: str) -> str:
    """Short scorecard blurb only (no score-band sentence)—for the three summary metric cards."""
    return _category_scorecard_definitions().get(category_key, "").strip()


def category_card_description(category_key: str, score: float) -> str:
    """Plain-English blurb for category section intros: tagline + score-band interpretation."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        s = 0.0
    definitions = _category_scorecard_definitions()
    interpretations: dict[str, list[tuple[float, str]]] = {
        "ai_visibility": [
            (
                90,
                "AI search tools have a strong basis to recognise the brand and cite key pages, "
                "with only minor improvements needed.",
            ),
            (
                75,
                "The site has a strong AI visibility foundation, but selected pages or platforms "
                "still need refinement.",
            ),
            (
                60,
                "The site has some AI visibility strengths, but gaps in answer clarity, brand signals, "
                "or platform readiness are limiting citation potential.",
            ),
            (
                40,
                "AI systems may struggle to recognise the brand or find clear, cite-worthy answers "
                "on important pages.",
            ),
            (
                0,
                "Major visibility gaps make it difficult for AI search tools to understand, trust, "
                "or cite the content.",
            ),
        ],
        "technical_setup": [
            (
                90,
                "Search engines and AI tools should be able to access and read the site reliably, "
                "with only minor technical improvements needed.",
            ),
            (
                75,
                "The technical foundation is solid, but a few access, discovery, performance, "
                "or crawler-readability issues still need attention.",
            ),
            (
                60,
                "Most technical basics are in place, but some issues may make it harder for crawlers "
                "to find or read important pages.",
            ),
            (
                40,
                "Technical issues are likely limiting how reliably search engines and AI tools can "
                "access or understand the site.",
            ),
            (
                0,
                "Major technical blockers are preventing the site from being reliably crawled, "
                "indexed, or used by AI systems.",
            ),
        ],
        "content_structure": [
            (
                90,
                "The content is strong, trustworthy, well organised, and gives AI systems clear "
                "evidence to work with.",
            ),
            (
                75,
                "The content is generally helpful and well structured, with opportunities to improve "
                "evidence, originality, or structured data.",
            ),
            (
                60,
                "The content provides some useful information, but needs clearer answers, stronger "
                "trust signals, or better structure to support AI citation.",
            ),
            (
                40,
                "Important content is not yet clear, trusted, or structured enough for strong AI "
                "citation performance.",
            ),
            (
                0,
                "The site needs major content improvements before AI systems are likely to treat "
                "it as a reliable source.",
            ),
        ],
    }
    definition = definitions.get(category_key, "")
    interpretation = ""
    for threshold, text in interpretations.get(category_key, []):
        if s >= threshold:
            interpretation = text
            break
    parts = [definition, interpretation]
    return " ".join(p for p in parts if p).strip()


def _sub_score(agents: list[AgentCategoryResult], cat_key: str, sub_key: str) -> float:
    for a in agents:
        if a.key == cat_key:
            for s in a.subs:
                if s.key == sub_key:
                    return s.score
    return 0.0


def _rollup_subs_to_category(subs: list[AgentSubResult]) -> tuple[list[str], list[str]]:
    st: list[str] = []
    im: list[str] = []
    for s in subs:
        for x in s.strengths:
            st.append(f"{s.title}: {x}")
        for x in s.improvements:
            im.append(f"{s.title}: {x}")
    return _unique_preserve(st), _unique_preserve(im)


def _mean_scores(scores: list[float]) -> float:
    return sum(scores) / len(scores) if scores else 0.0


def _weighted_scores(pairs: list[tuple[float, float]]) -> float:
    """Weighted mean for (score, weight) pairs. Weights can be percent or fractions."""
    if not pairs:
        return 0.0
    wsum = sum(w for _, w in pairs)
    if wsum <= 0:
        return 0.0
    return sum(float(s) * float(w) for s, w in pairs) / float(wsum)


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _clamp100(x: float) -> float:
    return max(0.0, min(100.0, float(x)))


def _ssr_html_completeness_proxy(audit: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    """
    Proxy score for 'Server-side rendering / raw HTML completeness'.
    We do not run a JS renderer; we infer risk from what the crawl could parse from HTML.
    """
    pages = [p for p in (audit.get("pages") or []) if p.get("http_status") == 200]
    ok = len(pages)
    summary = audit.get("summary") or {}
    any_jld = bool(summary.get("any_json_ld"))
    any_og = bool(summary.get("any_og_image"))
    name_src = str((audit.get("json_ld_txt") or {}).get("name_source") or "")

    st: list[str] = []
    im: list[str] = []

    if ok == 0:
        return 0.0, [], ["No successful HTML pages in the sample—cannot judge raw HTML completeness."]

    # If we can read a usable title on the homepage, raw HTML likely contains meaningful head elements.
    head_ok = name_src == "homepage_title"
    # If we can parse OG/JSON-LD on multiple pages, raw HTML likely contains machine-readable signals.
    signal_ok = (any_jld or any_og)

    s = 35.0
    if head_ok:
        s += 35.0
        st.append("Homepage metadata was readable in raw HTML (good sign for SSR/HTML completeness).")
    else:
        im.append("Homepage title signal was weak—raw HTML may be thin or heavily JS-dependent.")
    if signal_ok:
        s += 30.0
        st.append("Crawl could extract some structured/preview signals from HTML.")
    else:
        im.append("Few machine-readable signals were extractable—check if key content is JS-only.")

    s = _clamp100(s)
    return s, st, im


def _noindex_risk_from_sample(audit: dict[str, Any]) -> bool:
    """True if any sampled 200 page carries a noindex signal (meta or X-Robots-Tag)."""
    for p in (audit.get("pages") or []):
        if p.get("http_status") != 200:
            continue
        gen = str(p.get("meta_robots_generic") or "")
        if "noindex" in gen.lower():
            return True
        xrt = str(p.get("x_robots_tag") or "")
        if "noindex" in xrt.lower():
            return True
        named = p.get("meta_robots_named")
        if isinstance(named, dict):
            for v in named.values():
                if isinstance(v, str) and "noindex" in v.lower():
                    return True
    return False


def _subscore_canonical_duplicate_control(
    audit: dict[str, Any], *, http_ratio: float
) -> tuple[float, list[str], list[str]]:
    """
    Theme 2 proxy (skills/technical-audit.md): accidental noindex and HTTP success as weak
    canonical/consolidation signals—validate redirects, host policy, and tags in Search Console.
    """
    st: list[str] = []
    im: list[str] = []
    if _noindex_risk_from_sample(audit):
        im.append(
            "Sampled 200 responses include noindex (meta or X-Robots-Tag)—confirm intent; unintentional noindex breaks consolidation."
        )
        s = 36.0
    else:
        st.append("No noindex on sampled 200 pages in meta/X-Robots-Tag (still verify key templates sitewide).")
        s = 86.0
    if http_ratio < 0.72:
        s = min(s, 52.0)
        im.append("Many sampled URLs are non-200—check redirect chains and canonical targets.")
    elif http_ratio < 0.85:
        s = min(s, s - 12.0)
        im.append("Several non-200s in sample—spot-check redirects and soft errors.")
    elif http_ratio >= 0.92:
        st.append("High share of HTTP 200 in sample—good baseline for URL resolution checks.")
    return _clamp100(s), st, im


def _googlebot_blocked(robots_text: str | None, homepage_url: str) -> bool:
    if not robots_text or not robots_text.strip():
        return False
    home, robots_u = _robots_home_and_fetch_url(homepage_url)
    rp = RobotFileParser()
    rp.set_url(robots_u)
    try:
        rp.parse(robots_text.splitlines())
    except Exception:
        return False
    try:
        return not bool(rp.can_fetch("Googlebot", home))
    except Exception:
        return False


def _apply_overall_gating_caps(
    *,
    overall: float,
    audit: dict[str, Any],
    robots_text: str | None,
    homepage_url: str,
    ai_crawler_breakdown: dict[str, Any] | None,
    ssr_score: float,
    crawl_http_ratio: float,
) -> tuple[float, list[str]]:
    """
    Suggested gating rules (cap overall score when a major access/visibility blocker exists).
    Returns (capped_overall, notes).
    """
    notes: list[str] = []
    cap: float | None = None

    def apply(new_cap: float, why: str) -> None:
        nonlocal cap
        if cap is None or new_cap < cap:
            cap = float(new_cap)
        notes.append(f"Score cap: {int(new_cap)} — {why}")

    # 1) Site blocks Googlebot or major search crawlers → cap 50
    if _googlebot_blocked(robots_text, homepage_url):
        apply(50.0, "Site appears to block Googlebot in robots.txt (limits organic visibility, which gates AI Overviews/Gemini).")

    # 2) Site blocks most major crawlers (Tier 1 + Google/Bing + Tier-2 eco) → cap 60
    bd = ai_crawler_breakdown or {}
    major_raw = bd.get("major_allowed")
    if isinstance(major_raw, (int, float)):
        major_ok = int(major_raw)
    else:

        def _parse_allow_frac(s: Any) -> int:
            try:
                t = str(s)
                return int(t.split("/", 1)[0]) if "/" in t else 0
            except Exception:
                return 0

        t1_ok = _parse_allow_frac(bd.get("tier1_allowed"))
        f_ok = _parse_allow_frac(bd.get("foundational_allowed"))
        eco_ok = _parse_allow_frac(bd.get("tier2_eco_allowed"))
        if not f_ok and not eco_ok:
            eco_ok = _parse_allow_frac(bd.get("tier2_allowed"))
        major_ok = t1_ok + f_ok + eco_ok

    # Fewer than 6 of Tier1(5) + foundational(2) + eco(6) effectively allowed.
    if major_ok < 6:
        apply(
            60.0,
            "Most major crawlers (Tier 1 AI retrieval + Googlebot/Bingbot + ecosystem bots) are blocked or noindexed on sampled URLs.",
        )

    # 3) Key content unavailable in raw HTML → cap 65
    if ssr_score <= 40.0:
        apply(65.0, "Key pages may require JavaScript to show meaningful content (many AI crawlers do not render JS).")

    # 4) Key pages are noindex or canonicalized elsewhere → cap 55 (we only detect noindex here)
    if _noindex_risk_from_sample(audit):
        apply(55.0, "Some sampled pages appear to be marked noindex (blocks indexing/citation).")

    # 5) Widespread 4xx/5xx errors on key pages → cap 60
    if crawl_http_ratio and crawl_http_ratio < 0.70:
        apply(60.0, "Many sampled pages did not return a successful response (limits crawl and quoting).")

    # 6) No meaningful body content on priority pages → cap 50 (proxy: weak title + no extractable signals)
    summ = audit.get("summary") or {}
    if not bool(summ.get("any_json_ld")) and not bool(summ.get("any_og_image")):
        ns = str((audit.get("json_ld_txt") or {}).get("name_source") or "")
        if ns != "homepage_title":
            apply(50.0, "Priority pages look thin in raw HTML (few extractable signals were detected).")

    if cap is None:
        return overall, []
    return min(float(overall), float(cap)), notes


def _safe_ratio(num: int, den: int) -> float:
    return float(num) / float(den) if den else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _normalized_finding_sig(s: str) -> str:
    t = (s or "").lower()
    t = re.sub(r"[^a-z0-9\s]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _finding_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, _normalized_finding_sig(a), _normalized_finding_sig(b)).ratio()


def _dedupe_similar_strings(items: list[str], *, ratio: float = 0.68) -> list[str]:
    out: list[str] = []
    for x in items:
        x = (x or "").strip()
        if not x:
            continue
        if any(_finding_similarity(x, y) >= ratio for y in out):
            continue
        sig = _normalized_finding_sig(x)
        if sig and any(_normalized_finding_sig(y) == sig for y in out):
            continue
        out.append(x)
    return out


_SAMEAS_TOPIC_RE = re.compile(
    r"(?i)same\s*as|sameas|no sameas|organization linkage|"
    r"structured data.*\b(same|entity|profile|linkage)\b|\b(same|entity)\b.*structured data"
)
_OG_PREVIEW_TOPIC_RE = re.compile(
    r"(?i)og:image|og\s*image|open graph|social preview|twitter card|ai visual preview"
)
_JSONLD_TOPIC_RE = re.compile(
    r"(?i)json-ld|json ld|schema\.org|"
    r"(no|zero|missing|without)\s+json|"
    r"(no|zero|missing|lack|lacks)\s+structured\s+data"
)

_CANON_OG_FINDING = (
    "Use consistent preview titles and images on key pages so AI and social surfaces represent your brand clearly."
)
_CANON_ENTITY_STRUCTURED_FINDING = (
    "Improve Organization structured data: add verified profile links (`sameAs`) where confirmed "
    "and extend JSON-LD on priority templates so AI can recognize and cite your business."
)


def _collapse_topic_findings(items: list[str], pattern: re.Pattern[str], canonical: str) -> list[str]:
    kept: list[str] = []
    hit = False
    for x in items:
        if pattern.search(x):
            hit = True
        else:
            kept.append(x)
    if hit:
        if canonical not in kept:
            kept.insert(0, canonical)
    return kept


def _collapse_entity_structured_findings(items: list[str]) -> list[str]:
    """One bullet for sameAs + JSON-LD / schema gaps instead of two near-duplicates."""
    kept: list[str] = []
    hit = False
    for x in items:
        if _SAMEAS_TOPIC_RE.search(x) or _JSONLD_TOPIC_RE.search(x):
            hit = True
        else:
            kept.append(x)
    if hit and _CANON_ENTITY_STRUCTURED_FINDING not in kept:
        kept.insert(0, _CANON_ENTITY_STRUCTURED_FINDING)
    return kept


def _consolidate_improvement_lines(items: list[str]) -> list[str]:
    """Merge overlapping topic bullets (entity markup, OG) and drop near-duplicate lines."""
    u = _unique_preserve([(x or "").strip() for x in items if (x or "").strip()])
    u = _collapse_entity_structured_findings(u)
    u = _collapse_topic_findings(u, _OG_PREVIEW_TOPIC_RE, _CANON_OG_FINDING)
    return _dedupe_similar_strings(u, ratio=0.68)


def _consolidate_strength_lines(items: list[str]) -> list[str]:
    return _dedupe_similar_strings(_unique_preserve([(x or "").strip() for x in items if (x or "").strip()]))


def _peer_sub_score_gaps(
    peer_cat: AgentCategoryResult,
    primary_cat: AgentCategoryResult,
    *,
    peer_ahead: bool,
    min_diff: float = 5.0,
) -> list[tuple[float, str, str]]:
    """(score_delta, sub_key, sub_title) for subs where peer is ahead (or behind) by at least min_diff."""
    by_p = {s.key: s for s in primary_cat.subs}
    out: list[tuple[float, str, str]] = []
    for s in peer_cat.subs:
        sp = by_p.get(s.key)
        if sp is None:
            continue
        diff = s.score - sp.score if peer_ahead else sp.score - s.score
        if diff >= min_diff:
            out.append((diff, s.key, s.title))
    out.sort(key=lambda z: z[0], reverse=True)
    return out


def _peer_page_richness_score(page: dict[str, Any]) -> float:
    """Heuristic: richer machine-readable + preview signals ≈ stronger AI/content surface."""
    s = 0.0
    s += 4.0 * float(page.get("json_ld_blocks") or 0)
    if page.get("has_json_ld"):
        s += 3.0
    s += 1.5 * float(len(page.get("og_image_urls") or []))
    s += 1.2 * float(len(page.get("og_images_saved") or []))
    s += 1.0 * float(len(page.get("same_as") or []))
    return s


def _peer_url_path_segments(url: str) -> int:
    """Count path segments (rough proxy for topic depth / coverage pages)."""
    try:
        p = urllib.parse.urlparse(url).path.strip("/")
        return len([x for x in p.split("/") if x])
    except (TypeError, ValueError, AttributeError):
        return 0


def _peer_page_score_for_subdrivers(page: dict[str, Any], sub_keys: list[str]) -> float:
    """Rank peer pages for sample links when sub-areas drive the category gap."""
    if not sub_keys:
        return _peer_page_richness_score(page)
    u = (page.get("url") or "").strip()
    depth = _peer_url_path_segments(u)
    bl = int(page.get("json_ld_blocks") or 0)
    has_j = bool(page.get("has_json_ld") or bl > 0)
    og = float(len(page.get("og_image_urls") or []))
    same_n = float(len(page.get("same_as") or []))
    sk = set(sub_keys)
    score = 0.0
    if "ai_citability" in sk:
        score += 3.2 * float(bl) + (5.5 if has_j else 0.0) + 1.4 * og
    if "passage_answerability" in sk:
        score += 2.6 * float(bl) + 1.1 * float(min(depth, 8)) + 0.6 * og
    if "query_coverage_footprint" in sk:
        score += 2.4 * float(min(depth, 8)) + 0.9 * float(bl)
    if "original_information_gain" in sk:
        score += 2.0 * float(min(depth, 6)) + 1.4 * float(bl)
    if "brand_entity_visibility" in sk or "brand_visibility" in sk:
        score += 2.0 * same_n + 1.1 * og + 0.6 * float(bl)
    if "platform_readiness" in sk or "ai_search_success" in sk:
        score += 1.8 * og + 0.7 * float(bl)
    if "eeat" in sk or "json_ld" in sk or "schema_entity_markup" in sk:
        score += 2.2 * float(bl) + 1.3 * same_n
    if "source_transparency_governance" in sk:
        score += 1.4 * float(bl) + 0.5 * same_n
    if "indexability_crawl_health" in sk:
        score += 0.6 * float(min(depth, 5)) + 0.35 * float(bl)
    if "ai_crawler_report" in sk:
        score += 0.9 * (1.0 if has_j else 0.0) + 0.45 * float(min(depth, 4))
    if "ssr_html_completeness" in sk:
        score += 1.2 * float(bl) + 0.45 * float(min(depth, 5))
    if "discovery_signals" in sk:
        score += 0.45 * float(min(depth, 4))
    if "performance_page_experience" in sk:
        score += 0.35 * og
    score += 0.12 * _peer_page_richness_score(page)
    sig = page.get("content_signals")
    if isinstance(sig, dict):
        grid = bool(sig.get("is_product_grid"))
        ed = bool(sig.get("has_editorial_content"))
        if ("query_coverage_footprint" in sk or "ai_citability" in sk) and grid and not ed:
            score *= 0.55
        elif "query_coverage_footprint" in sk and grid:
            score *= 0.78
    return score


def _peer_label_page_for_subdrivers(page: dict[str, Any], drivers: list[str]) -> str:
    """Human-readable link text aligned to the gap drivers (not the raw URL)."""
    bl = int(page.get("json_ld_blocks") or 0)
    has_j = bool(page.get("has_json_ld") or bl > 0)
    og = len(page.get("og_image_urls") or [])
    same_n = len(page.get("same_as") or [])
    depth = _peer_url_path_segments((page.get("url") or ""))
    sig = page.get("content_signals")
    grid = isinstance(sig, dict) and bool(sig.get("is_product_grid"))
    ed_ok = isinstance(sig, dict) and bool(sig.get("has_editorial_content"))
    for d in drivers:
        if d == "ai_citability" and (has_j or bl or og):
            return "AI citability example"
        if d == "query_coverage_footprint" and depth >= 2:
            if grid and not ed_ok:
                return "Category / listing sample (verify passages)"
            return "Topic / query-depth example"
        if d == "query_coverage_footprint":
            if grid and not ed_ok:
                return "Category / listing sample (verify passages)"
            return "On-site content example"
        if d == "passage_answerability" and (has_j or bl):
            return "Passage-friendly example"
        if d == "original_information_gain" and (depth >= 3 or bl):
            return "Content depth example"
        if d == "brand_entity_visibility" and (same_n or has_j):
            return "Entity / corroboration example"
        if d == "brand_visibility" and og:
            return "Brand surface example"
        if d in ("json_ld", "schema_entity_markup") and has_j:
            return "Structured data example"
        if d == "eeat" and has_j:
            return "Trust / markup example"
        if d == "source_transparency_governance":
            return "Governance signal example"
        if d == "ai_crawler_report":
            return "Crawl-accessible page example"
        if d == "indexability_crawl_health":
            return "Indexable URL example"
        if d == "ssr_html_completeness" and bl:
            return "Machine-readable HTML example"
        if d == "platform_readiness" and og:
            return "Preview / platform example"
        if d == "ai_search_success" and has_j:
            return "AI search readiness example"
    return "High-signal sample page"


def _http_int_status(page: dict[str, Any]) -> int | None:
    try:
        return int(page.get("http_status"))
    except (TypeError, ValueError):
        return None


def _page_title_suggests_soft_404(title: str | None) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return False
    markers = (
        "404",
        "not found",
        "page not found",
        "we can't find",
        "we can’t find",
        "cannot find",
        "can't find",
        "sorry, we",
        "sorry this",
        "oops",
        "not available",
        "no longer available",
        "access denied",
    )
    return any(m in t for m in markers)


def _url_path_suggests_error(url: str) -> bool:
    try:
        path = urllib.parse.urlparse(url).path.lower()
    except (TypeError, ValueError, AttributeError):
        return True
    needles = ("/404", "/error", "/not-found", "/not_found", "not-found", "page-not-found", "/gone")
    return any(n in path for n in needles)


def _page_has_noindex_signal(page: dict[str, Any]) -> bool:
    x = (page.get("x_robots_tag") or "").lower()
    if "noindex" in x:
        return True
    for key in ("meta_robots_generic", "meta_robots_named"):
        v = page.get(key)
        if isinstance(v, str) and "noindex" in v.lower():
            return True
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and "noindex" in item.lower():
                    return True
    return False


def _cross_host_redirect(page: dict[str, Any]) -> bool:
    fu = (page.get("final_url") or "").strip()
    ou = (page.get("url") or "").strip()
    if not fu or not ou:
        return False
    try:
        o = urllib.parse.urlparse(ou)
        f = urllib.parse.urlparse(fu)
    except (TypeError, ValueError, AttributeError):
        return False
    on = (o.netloc or "").lower()
    fn = (f.netloc or "").lower()
    if not on or not fn:
        return False
    return on != fn


def _redirect_collapsed_deep_link_to_home(peer_base: str, page: dict[str, Any]) -> bool:
    """True when a non-home requested URL resolves to only the origin path (weak evidence)."""
    fu = (page.get("final_url") or "").strip()
    ou = (page.get("url") or "").strip()
    if not fu or not ou:
        return False
    try:
        o = urllib.parse.urlparse(ou)
        f = urllib.parse.urlparse(fu)
    except (TypeError, ValueError, AttributeError):
        return False
    if (o.netloc or "").lower() != (f.netloc or "").lower():
        return False
    o_path = (o.path or "/").rstrip("/") or "/"
    f_path = (f.path or "/").rstrip("/") or "/"
    if o_path in ("/", ""):
        return False
    if f_path in ("/", ""):
        return True
    return False


def _peer_page_ok_for_evidence(peer_audit: dict[str, Any], page: dict[str, Any]) -> bool:
    """
    Competitor example URLs must look like valid 200 pages: no soft-404 titles, no noindex,
    no error paths, and no suspicious redirects (cross-host or deep-link collapsing to /).
    """
    if _http_int_status(page) != 200:
        return False
    u = (page.get("url") or "").strip()
    if not u.startswith("http"):
        return False
    if _url_path_suggests_error(u):
        return False
    if _page_title_suggests_soft_404(page.get("page_title")):
        return False
    if _page_has_noindex_signal(page):
        return False
    if _cross_host_redirect(page):
        return False
    base = (peer_audit.get("base_url") or "").strip().rstrip("/")
    if base and _redirect_collapsed_deep_link_to_home(base, page):
        return False
    return True


def _peer_evidence_pages(peer_audit: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in (peer_audit.get("pages") or []) if _peer_page_ok_for_evidence(peer_audit, p)]


def _peer_category_driver_labels(
    peer_cat: AgentCategoryResult,
    primary_cat: AgentCategoryResult,
    *,
    peer_ahead: bool,
    limit: int = 3,
) -> str:
    deltas = _peer_sub_score_gaps(peer_cat, primary_cat, peer_ahead=peer_ahead)
    if not deltas:
        return ""
    labels = [t for _, __, t in deltas[:limit]]
    return ", ".join(labels)


def _cmp_peer_advantage_html(
    area: str,
    takeaway: str,
    why: str,
    confidence: str | None,
    action: str | None,
) -> str:
    """Trusted HTML: escape all copy after client_friendly_text normalization."""
    def esc(s: str) -> str:
        return html.escape(client_friendly_text((s or "").strip()))

    parts = [
        '<div class="cmp-peer-adv">',
        f'<div class="cmp-peer-adv-area"><strong>{esc(area)}</strong></div>',
        f'<div class="cmp-peer-adv-line"><strong>Takeaway:</strong> {esc(takeaway)}</div>',
        f'<div class="cmp-peer-adv-line"><strong>Why:</strong> {esc(why)}</div>',
    ]
    if confidence:
        parts.append(
            '<div class="cmp-peer-adv-line cmp-peer-adv-confidence">'
            f"<strong>Confidence:</strong> {esc(confidence)}</div>"
        )
    if action:
        parts.append(
            '<div class="cmp-peer-adv-line cmp-peer-adv-action">'
            f"<strong>Recommended action:</strong> {esc(action)}</div>"
        )
    parts.append("</div>")
    return "".join(parts)


def _cmp_peer_note_html(lead: str, body: str) -> str:
    """Single paragraph note (lead bold) for non-category comparison bullets."""
    return (
        f"<p class='cmp-peer-note'><strong>{html.escape(lead.strip())}</strong> "
        f"{html.escape(client_friendly_text((body or '').strip()))}</p>"
    )


def _peer_pick_labeled_sample_urls(
    peer_audit: dict[str, Any],
    sub_drivers: list[str],
    *,
    max_urls: int = 2,
) -> list[tuple[str, str]]:
    """(url, label) pairs from the peer crawl, chosen to reflect the sub-areas driving the gap."""
    pages = [p for p in _peer_evidence_pages(peer_audit) if (p.get("url") or "").strip()]
    if not pages:
        return []
    drivers = list(sub_drivers)[:6]
    ranked = sorted(
        pages,
        key=lambda p: (
            -_peer_page_score_for_subdrivers(p, drivers),
            -len((p.get("url") or "")),
            (p.get("url") or ""),
        ),
    )
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for p in ranked:
        u = (p.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append((u, _peer_label_page_for_subdrivers(p, drivers)))
        if len(out) >= max_urls:
            break
    return out


def _peer_format_labeled_sample_links_html(labeled: list[tuple[str, str]]) -> str:
    """Trusted HTML: URLs and labels come only from crawl-derived tuples."""
    if not labeled:
        return (
            '<p class="cmp-peer-samples cmp-peer-samples--empty" role="note">'
            "No reliable example URL was available from the crawl sample; verify manually before "
            "acting on this comparison."
            "</p>"
        )
    links: list[str] = []
    for u, lab in labeled:
        links.append(
            f'<a class="cmp-peer-sample-link" href="{html.escape(u)}" target="_blank" rel="noopener noreferrer">'
            f"{html.escape(lab)}</a>"
        )
    joined = '<span class="cmp-peer-sample-sep"> · </span>'.join(links)
    return (
        '<div class="cmp-peer-samples" role="note">'
        '<span class="cmp-peer-samples-label">Evidence examples:</span> '
        f"{joined}"
        "</div>"
    )


def _peer_pick_sample_page_urls(peer_audit: dict[str, Any], *, max_urls: int = 2) -> list[str]:
    """Best-effort URLs from the peer crawl to illustrate on-site examples (validated 200 pages)."""
    pages = [p for p in _peer_evidence_pages(peer_audit) if (p.get("url") or "").strip()]
    if not pages:
        return []
    base = (peer_audit.get("base_url") or "").strip().rstrip("/")
    scored = sorted(
        pages,
        key=lambda p: (-_peer_page_richness_score(p), (p.get("url") or "")),
    )
    out: list[str] = []
    seen: set[str] = set()
    for p in scored:
        u = (p.get("url") or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
        if len(out) >= max_urls:
            break
    if len(out) < max_urls and base:
        bn = base.rstrip("/").lower()
        for p in pages:
            u = (p.get("url") or "").strip()
            if not u:
                continue
            if u.rstrip("/").lower() == bn and u not in seen:
                seen.add(u)
                out.insert(0, u)
                break
    return out[:max_urls]


def _peer_first_page_with_json_ld(peer_audit: dict[str, Any]) -> str | None:
    for p in _peer_evidence_pages(peer_audit):
        if p.get("has_json_ld") or int(p.get("json_ld_blocks") or 0) > 0:
            u = (p.get("url") or "").strip()
            if u:
                return u
    return None


def _dedupe_peer_green_entries(lines: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """Drop near-duplicate competitor notes; ``lines`` items are (dedupe_key, body_html, links_html)."""
    blob = "\n".join(re.sub(r"<[^>]+>", " ", h).lower() for _, h, __ in lines if h)
    out: list[tuple[str, str, str]] = []
    for key, body, links in lines:
        gl = re.sub(r"<[^>]+>", " ", body).lower()
        if "json-ld detected" in gl and "json-ld / structured data" in blob:
            continue
        if "og:image" in gl and "platform readiness" in blob:
            continue
        if "llms.txt" in gl and "llms.txt" in blob and "technical setup" in blob:
            continue
        out.append((key, body, links))
    return out


def _executive_priority_bucket(text: str) -> str:
    """Coarse theme for deduping the executive summary priority list."""
    low = (text or "").lower()
    if re.search(
        r"structured data|sameas|same as|entity signals|machine-readable|json-ld|json ld",
        low,
    ):
        return "structured"
    if re.search(r"preview|open graph|social surfaces|sharing elements|images on key pages", low):
        return "previews"
    if re.search(r"crawler|robots\.txt|gptbot|perplexity", low):
        return "crawlers"
    if re.search(r"brand profile|off-site|listing|automated checks did not match", low):
        return "brand_surface"
    if re.search(r"llms\.txt|\bai-facing guide\b", low):
        return "llms"
    return "other"


def _is_bytespider_deprioritized(text: str) -> bool:
    """Tier-3 training crawler tuning — useful but not lead executive priority."""
    low = (text or "").lower()
    return "bytespider" in low or ("bytedance" in low and "training" in low)


def _executive_plain_finding(text: str) -> str:
    """Business-readable wording for the executive summary (avoid raw crawl jargon)."""
    raw = (text or "").strip()
    low = raw.lower()
    if re.search(r"og:image|open graph|twitter card|social preview|meta.*preview", low):
        return (
            "Some important preview and sharing elements are missing, which can weaken how the site "
            "appears in AI-driven answers and when links are shared."
        )
    if _SAMEAS_TOPIC_RE.search(raw):
        return (
            "Official brand profiles are not yet wired into structured data in a way AI systems can rely on."
        )
    if re.search(r"json-ld|json ld|schema\.org", low) and "same" not in low:
        return (
            "Machine-readable structured data is underused, so AI has fewer trustworthy hooks when citing you."
        )
    if "llms.txt" in low or re.search(r"\bllms\.txt\b", low):
        return (
            "The site does not yet publish a concise AI-facing guide to what should be trusted on this domain."
        )
    if re.search(r"robots\.txt|gptbot|perplexitybot|crawler|disallow|user-agent", low):
        return (
            "Crawler access rules may be limiting how much trusted AI services can read your public pages."
        )
    if re.search(r"\bh1\b|missing h1|no h1", low):
        return "Key pages lack a clear primary heading, which weakens topic clarity for both people and machines."
    if "e-e-a-t" in low or "eeat" in low or ("author" in low and "byline" in low):
        return "Signals of expertise and trust on pages are thinner than competitors typically show."
    if "word" in low and "homepage" in low:
        return "Important landing pages are light on substantive copy, which limits what AI can quote or summarize."
    if "json-ld" in low or "structured data" in low:
        return "Structured data and entity signals need attention so AI can recognize and cite the business confidently."
    if re.search(r"\bsample\b|\bcrawl\b", low):
        return "There are technical and content gaps in this audit sample that are holding back AI visibility."
    if "off-site" in low or "brand-visbility" in low or (
        "brand visibility" in low and "manual" in low
    ):
        return (
            "Automated checks did not match every expected brand profile—confirm key listings "
            "so the picture is complete."
        )
    return raw


def _executive_plain_chain(
    priorities: list[str], impactful_plain: str
) -> list[tuple[str, str]]:
    """
    Up to three (plain finding, bucket) tuples for the executive narrative:
    primary gap then distinct follow-on themes. Bytespider lines are skipped for lead slots.
    """
    priorities = priorities_for_executive(list(priorities))
    ib0 = _executive_priority_bucket(impactful_plain)
    out: list[tuple[str, str]] = [(impactful_plain, ib0)]
    seen_buckets: set[str] = {ib0}

    filt = [x for x in priorities if not _is_bytespider_deprioritized(x)]
    walk = filt if filt else list(priorities)

    for x in walk:
        t = _plain_detail_for_executive(x) if ": " in x else x
        t = client_friendly_text(t)
        plain = _executive_plain_finding(t).strip().rstrip(".")
        if not plain:
            continue
        if plain.lower() == impactful_plain.lower():
            continue
        if _finding_similarity(plain, impactful_plain) >= 0.55:
            continue
        bb = _executive_priority_bucket(plain)
        if bb in seen_buckets:
            continue
        out.append((plain, bb))
        seen_buckets.add(bb)
        if len(out) >= 3:
            break
    return out


def _executive_gap_sentence_html(plain: str, bucket: str) -> str:
    """Primary gap — one readable sentence, minimal strong emphasis."""
    low = plain.lower()
    if bucket == "structured":
        if "official brand" in low or "wired into structured" in low:
            return (
                "The biggest gap is in <strong>structured data</strong> on the site, as "
                "official brand profiles are not yet included in a way that AI systems can rely on."
            )
        if "underused" in low or "trustworthy hooks" in low:
            return (
                "The biggest gap is in <strong>structured data</strong> on the site, as "
                "machine-readable labels are still thin, so AI has fewer trustworthy hooks when citing you."
            )
        inner = plain.strip().rstrip(".")
        return (
            "The biggest gap is in <strong>structured data</strong> on the site—"
            f"{html.escape(inner)}."
        )
    if bucket == "previews":
        return (
            "The biggest gap is inconsistent <strong>link previews</strong>—titles and images "
            "should represent your brand reliably when pages appear in AI answers and social feeds."
        )
    if bucket == "llms":
        return (
            "The biggest gap is the lack of a concise <strong>AI-facing guide</strong> "
            "that tells assistants what to trust on this domain."
        )
    if bucket == "crawlers":
        return (
            "The biggest gap is <strong>crawler access</strong>: your rules may be limiting "
            "how much trusted AI services can read from public pages."
        )
    if bucket == "brand_surface":
        return (
            "The biggest gap is <strong>brand footprint</strong>: external listings and "
            "your site should tell the same story so AI can corroborate facts."
        )
    return f"The biggest gap to close first is this: {html.escape(plain)}."


def _executive_follow_sentence_html(plain: str, bucket: str, position: int) -> str:
    """Second or third sentence—full clauses, not comma-chained fragments."""
    # Prefer canonical friendly sentences over raw crawl text.
    if bucket == "previews":
        return (
            "An early priority is to use <strong>consistent preview titles and images</strong> "
            "on key pages so AI represents your brand clearly."
        )
    if bucket == "llms":
        return (
            "The site should publish a concise <strong>AI-facing guide</strong> "
            "as to what should be trusted on this domain."
        )
    if bucket == "structured":
        if position == 2:
            return (
                "Another focus is strengthening <strong>structured data</strong> "
                "so organization and page types are explicit for search and AI systems."
            )
        return (
            "Continue tightening <strong>structured data</strong> until key templates "
            "fully describe who you are and what each page is for."
        )
    if bucket == "crawlers":
        return (
            "Review <strong>robots and crawler rules</strong> so helpful AI bots can reach "
            "content you intend to be public."
        )
    if bucket == "brand_surface":
        return (
            "Confirm official <strong>brand profiles</strong> on major platforms and align them with your site."
        )
    frag = client_friendly_text((plain or "").strip().rstrip("."))
    if not frag:
        return "See the prioritized action plan for concrete next steps."
    return f"Another priority is this: {html.escape(frag)}."


def _score_band(score: float) -> str:
    """Qualitative band for a 0–100 score: good | medium | bad."""
    if score >= 70:
        return "good"
    if score >= 45:
        return "medium"
    return "bad"


def _td_score(score: float, *, strong: bool = False) -> str:
    b = _score_band(score)
    inner = format_report_score(score)
    if strong:
        inner = f"<strong>{inner}</strong>"
    # Legacy helper (used by competitor table); redesigned markup uses score pills.
    return f'<td class="score-cell band-{b}">{inner}</td>'


def _td_score_pill(score: float, *, strong: bool = False) -> str:
    """Table cell with score pill (matches Summary table styling)."""
    pill = _pill_class(score)
    inner = f'<span class="score-pill {pill}">{format_report_score(score)}</span>'
    if strong:
        inner = f"<strong>{inner}</strong>"
    return f"<td>{inner}</td>"


def _competitor_key_observations(cats: list[AgentCategoryResult], *, limit: int = 6) -> list[str]:
    """Short bullet list for expandable row (no duplicate skill-style prefixes)."""
    strengths: list[str] = []
    gaps: list[str] = []
    for c in cats:
        for x in c.strengths:
            line = _plain_rollup_line(x) if ": " in x else x
            strengths.append(line)
        for x in c.improvements:
            line = _plain_rollup_line(x) if ": " in x else x
            gaps.append(line)
    merged = _unique_preserve(strengths + gaps)
    return [client_friendly_text(x) for x in merged[:limit]]


def _audit_quick_signals(audit: dict[str, Any]) -> dict[str, Any]:
    _enrich_audit_json_ld_signals(audit)
    sm = audit.get("summary") or {}
    rt = audit.get("robots_txt") or {}
    ll = audit.get("llms_txt") or {}
    ok_pages = [p for p in (audit.get("pages") or []) if p.get("http_status") == 200]
    jld_hits = sum(1 for p in ok_pages if p.get("has_json_ld"))
    json_ld_effective = bool(
        sm.get("any_json_ld")
        or sm.get("json_ld_home_organization")
        or sm.get("json_ld_home_website")
        or sm.get("json_ld_home_search_action")
    )
    return {
        "json_ld": bool(sm.get("any_json_ld")),
        "json_ld_effective": json_ld_effective,
        "json_ld_page_hits": jld_hits,
        "home_org": bool(sm.get("json_ld_home_organization")),
        "home_website": bool(sm.get("json_ld_home_website")),
        "home_search_action": bool(sm.get("json_ld_home_search_action")),
        "json_ld_http_context": bool(sm.get("json_ld_any_http_context")),
        "og": bool(sm.get("any_og_image")),
        "same_as_n": len(sm.get("unique_same_as_urls") or []),
        "llms_live": bool(ll.get("exists")),
        "robots_fetched": bool(rt.get("exists")),
    }


def _peer_diff_bullets(
    primary_audit: dict[str, Any],
    peer_audit: dict[str, Any],
    primary_cats: list[AgentCategoryResult],
    peer_cats: list[AgentCategoryResult],
    *,
    delta: float = 4.0,
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Return peer-ahead detail rows followed by primary-brand-ahead summaries."""
    green: list[tuple[str, str, str]] = []
    red: list[str] = []
    pk = {c.key: c for c in primary_cats}
    qk = {c.key: c for c in peer_cats}
    titles = {
        "ai_visibility": "AI visibility",
        "technical_setup": "Technical setup",
        "content_structure": "Content quality & structure",
    }
    for key, title in titles.items():
        ps = pk[key].score
        qs = qk[key].score
        if qs - ps >= delta:
            drivers_txt = _peer_category_driver_labels(qk[key], pk[key], peer_ahead=True)
            takeaway = (
                f"The competitor appears stronger on {title.lower()} in this automated crawl sample "
                f"({qs:.0f} vs your {ps:.0f})."
            )
            if drivers_txt:
                why = f"The largest automated gaps vs your sample sit on: {drivers_txt}."
            else:
                why = "The difference is spread across several sub-scores in this crawl sample."
            conf: str | None = None
            act: str | None = None
            if key == "ai_visibility":
                meta = peer_audit.get("_ai_visibility_meta") or {}
                if meta.get("citability_confidence") == "low" or meta.get("listing_weighted_sample"):
                    conf = (
                        "Low–medium: the crawl leans on category or listing URLs. Treat AI citability as directional "
                        "until someone spot-checks passages for direct answers, buying guidance, or FAQs—not only "
                        "product grids."
                    )
                    act = (
                        "Improve priority templates with answer blocks and FAQs; confirm passages on listing URLs "
                        "before mapping those templates into an AI channel bucket."
                    )
            if key == "technical_setup":
                tmeta = peer_audit.get("_technical_meta") or {}
                if tmeta.get("ssr_listing_caveat"):
                    conf = (
                        "Medium: stronger raw HTML on catalogue-style URLs is mainly an extractability signal—it "
                        "does not automatically mean stronger quotable answer content for AI citations."
                    )
                    act = (
                        "Keep hardening crawler-readable HTML, stable 200 responses, and discovery paths on real "
                        "shopper journeys."
                    )
            if key == "content_structure":
                drivers_keys = [sk for _, sk, __ in _peer_sub_score_gaps(qk[key], pk[key], peer_ahead=True)][:5]
                if any(d in ("json_ld", "schema_entity_markup", "eeat") for d in drivers_keys):
                    conf = (
                        "Medium: broader JSON-LD on the peer sample is a template signal. Compare like-for-like page "
                        "types (homepage, PDP, category) before treating it as a full content-quality advantage."
                    )
                    act = (
                        "Prioritise schema where the type matches the real page; strengthen E-E-A-T and clear "
                        "answer copy wherever you need AI citations—not only markup breadth."
                    )
            body = _cmp_peer_advantage_html(title, takeaway, why, conf, act)
            links_html = ""
            if key in ("ai_visibility", "content_structure", "technical_setup"):
                drivers = [sk for _, sk, __ in _peer_sub_score_gaps(qk[key], pk[key], peer_ahead=True)][:5]
                pairs = _peer_pick_labeled_sample_urls(peer_audit, drivers, max_urls=2)
                links_html = _peer_format_labeled_sample_links_html(pairs)
            green.append((f"cat:{key}", body, links_html))
        elif ps - qs >= delta:
            drivers_txt = _peer_category_driver_labels(qk[key], pk[key], peer_ahead=False)
            if drivers_txt:
                detail = f"Your crawl sample scores higher on: {drivers_txt}."
            else:
                detail = "Your crawl sample leads on several sub-areas in this run."
            red.append(
                f"{title}: Your site scores higher in this automated sample ({ps:.0f} vs their {qs:.0f}). "
                f"{client_friendly_text(detail)}"
            )
    psig = _audit_quick_signals(primary_audit)
    qsig = _audit_quick_signals(peer_audit)
    pe, qe = psig["json_ld_effective"], qsig["json_ld_effective"]
    pj, qj = psig["json_ld"], qsig["json_ld"]
    if qe and not pe:
        ju = _peer_first_page_with_json_ld(peer_audit)
        gh = (
            _peer_format_labeled_sample_links_html([(ju, "Category or template page with JSON-LD")])
            if ju
            else _peer_format_labeled_sample_links_html([])
        )
        body = _cmp_peer_note_html(
            "Structured data:",
            "The peer crawl sample shows broader JSON-LD signals on sampled URLs than yours. "
            "Compare equivalent templates before treating this as decisive.",
        )
        green.append(("note:jsonld-peer-stronger", body, gh))
    elif qj and not pj and pe:
        ju = _peer_first_page_with_json_ld(peer_audit)
        gh = (
            _peer_format_labeled_sample_links_html([(ju, "Peer page with JSON-LD blocks")])
            if ju
            else _peer_format_labeled_sample_links_html([])
        )
        body = _cmp_peer_note_html(
            "Structured data:",
            "Parsed JSON-LD blocks appeared on fewer sampled URLs on your site than on the peer, "
            "but your homepage still shows Organization or WebSite-style signals in jsonld/*.json. "
            "Broaden coverage to key templates, not only the homepage.",
        )
        green.append(("note:jsonld-mixed", body, gh))
    if pe and qe:
        if qsig["home_website"] and psig["home_org"] and not psig["home_website"]:
            body = _cmp_peer_note_html(
                "Structured data:",
                "Both sites expose JSON-LD in this crawl. The peer sample stresses WebSite-style markup more; "
                "your homepage sample leans on Organization. Add WebSite plus SearchAction where appropriate while "
                "keeping sameAs.",
            )
            green.append(("note:jsonld-website", body, ""))
        elif qsig["home_search_action"] and psig["home_org"] and not psig["home_search_action"]:
            body = _cmp_peer_note_html(
                "Structured data:",
                "Both sites expose JSON-LD; the peer homepage sample includes SearchAction-style site search markup "
                "that yours lacks. Add it if you have sitewide search.",
            )
            green.append(("note:jsonld-searchaction", body, ""))
        elif psig["home_website"] and qsig["home_org"] and not qsig["home_website"]:
            body = _cmp_peer_note_html(
                "Structured data:",
                "Both sites expose JSON-LD; your homepage sample includes WebSite-style structured data while the "
                "peer sample stresses Organization and sameAs. Keep your sitewide schema and tighten entity links.",
            )
            green.append(("note:jsonld-org-peer", body, ""))
        p_avg = (primary_audit.get("summary") or {}).get("json_ld_avg_product_proxy")
        q_avg = (peer_audit.get("summary") or {}).get("json_ld_avg_product_proxy")
        if (
            isinstance(p_avg, (int, float))
            and isinstance(q_avg, (int, float))
            and abs(float(p_avg) - float(q_avg)) < 5.0
            and (p_avg or q_avg)
        ):
            body = _cmp_peer_note_html(
                "Structured data:",
                "Sampled Product JSON-LD richness is broadly comparable. Next gains are likely template coverage, "
                "stable @id values, and graph linking rather than a single headline score.",
            )
            green.append(("note:jsonld-product-comparable", body, ""))
        if pj != qj:
            body = _cmp_peer_note_html(
                "Directional note:",
                "Crawl samples can mix different templates. Compare homepage, product, and category URLs "
                "like-for-like before treating one schema rollup as decisive.",
            )
            green.append(("note:jsonld-templates", body, ""))
    if pe and not qe:
        red.append(
            "Structured data: the peer sample shows weaker JSON-LD / homepage entity signals than yours in this crawl."
        )
    if qsig["og"] and not psig["og"]:
        su = _peer_pick_sample_page_urls(peer_audit, max_urls=1)
        gh = _peer_format_labeled_sample_links_html([(u, "Page with Open Graph preview metadata") for u in su])
        body = _cmp_peer_note_html(
            "Preview metadata:",
            "The peer crawl sample includes og:image on at least one sampled URL where yours did not in this run.",
        )
        green.append(("note:og-peer", body, gh))
    if psig["og"] and not qsig["og"]:
        red.append("Preview metadata: the peer sample lacks og:image where yours has it on this crawl.")
    if qsig["llms_live"] and not psig["llms_live"]:
        green.append(
            (
                "note:llms-peer",
                _cmp_peer_note_html(
                    "AI-facing guide:",
                    "The peer origin serves a live llms.txt; yours does not in this crawl.",
                ),
                "",
            )
        )
    if psig["llms_live"] and not qsig["llms_live"]:
        red.append("AI-facing guide: the peer has no live llms.txt where you do.")
    if qsig["same_as_n"] >= 2 and psig["same_as_n"] == 0:
        su = _peer_pick_sample_page_urls(peer_audit, max_urls=1)
        gh = _peer_format_labeled_sample_links_html([(u, "Page referencing sameAs in JSON-LD") for u in su])
        body = _cmp_peer_note_html(
            "Entity corroboration:",
            f"The peer sample surfaces more JSON-LD sameAs links ({qsig['same_as_n']} vs {psig['same_as_n']}).",
        )
        green.append(("note:sameas-peer", body, gh))
    if psig["same_as_n"] >= 2 and qsig["same_as_n"] == 0:
        red.append(
            f"Entity corroboration: fewer sameAs links on the peer sample than on yours ({qsig['same_as_n']} vs {psig['same_as_n']})."
        )

    green = _dedupe_peer_green_entries(green)
    green_u: list[tuple[str, str, str]] = []
    seen_g: set[str] = set()
    for _key, body, links in green:
        sig = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip().lower()[:320]
        if sig in seen_g:
            continue
        seen_g.add(sig)
        green_u.append((_key, body, links))
    green_u = green_u[:8]
    red = _unique_preserve([client_friendly_text(x) for x in red])[:8]
    return green_u, red


def _finding_severity(text: str) -> str:
    """Map an improvement line to a design-system severity band."""
    t = text.lower()
    if "bytespider" in t or ("bytedance" in t and "crawler" in t):
        return "low"
    if any(
        k in t
        for k in (
            "no json-ld",
            "no json ld",
            "no structured data",
            "blocked",
            "disallow",
            "cannot fetch",
            "critical",
            "no sameas",
            "no same as",
        )
    ):
        return "critical"
    if any(
        k in t
        for k in (
            "no og:image",
            "og:image",
            "llms.txt",
            "llms ",
            "crawler",
            "robots",
            "gptbot",
            "perplexity",
            "e-e-a-t",
            "eeat",
            "title signal",
            "weak title",
            "brand visibility",
            "sameas",
        )
    ):
        return "high"
    if any(
        k in t
        for k in (
            "heading",
            "metadata",
            "faq",
            "template",
            "content",
            "cluster",
            "definition",
        )
    ):
        return "medium"
    return "low"


def _key_finding_cluster_order(text: str) -> int:
    """
    Thematic sort key so related key findings appear together (within the same severity band).
    Lower numbers surface first. Order follows common remediation groupings: robots/crawlers,
    rendering, brand checks, schema, discovery files, performance, then content and governance.
    """
    t = (text or "").lower()

    if "robots.txt" in t or ("robots" in t and "crawler" in t):
        return 0
    if "llms.txt" in t or "llms " in t or "ai guide file" in t:
        return 1
    if "javascript" in t or " without relying on js" in t or "raw html" in t:
        return 2
    if (
        "chatgpt" in t
        or "perplexity" in t
        or "copilot" in t
        or "ai overviews" in t
        or "google ai" in t
    ):
        return 3
    if (
        "sameas" in t
        or "same as" in t
        or "json-ld" in t
        or "json ld" in t
        or "organization structured" in t
        or ("structured data" in t and "visible" not in t and "javascript" not in t)
    ):
        return 4
    if "sitemap" in t:
        return 5
    if "speed" in t or "vitals" in t or "usability" in t or "loading speed" in t or "ttfb" in t:
        return 6
    if "template" in t or "canonical" in t or "listable" in t or "main url" in t:
        return 7
    if (
        ("query" in t and "shape" in t)
        or "definitions" in t
        or "comparisons" in t
        or "troubleshooting" in t
        or "pricing" in t
        or "selection" in t
    ):
        return 8
    if (
        "passage" in t
        or "faq" in t
        or "takeaways" in t
        or "direct answers under headings" in t
    ):
        return 9
    if "transparency" in t or "governance" in t or "disclosures" in t or "corrections" in t:
        return 10
    if "update date" in t or "freshness" in t:
        return 11
    if "original information" in t or "first-party" in t or "first party" in t or "case stud" in t:
        return 12
    if "crawler" in t or "gptbot" in t or "disallow" in t or "allow" in t:
        return 0
    return 99


def _report_section_head(title: str, elem_id: str = "", *, suffix_html: str = "") -> str:
    id_attr = f' id="{html.escape(elem_id)}"' if elem_id else ""
    return (
        f'<header class="section-head"{id_attr}>'
        f'<h2 class="section-title">{html.escape(title)}{suffix_html}</h2>'
        f"</header>"
    )


def _report_subhead(title: str, elem_id: str = "") -> str:
    id_attr = f' id="{html.escape(elem_id)}"' if elem_id else ""
    return f'<h3 class="score-breakdown-subheading"{id_attr}>{html.escape(title)}</h3>'


def _key_findings_section_html(
    categories: list[AgentCategoryResult],
    priorities: list[str],
    *,
    industry: str = "",
    limit: int = 14,
) -> str:
    """Key Findings block (design/use_this_design.html) ranked critical → low."""
    raw: list[str] = []
    for c in categories:
        for x in c.improvements:
            line = _plain_rollup_line(x) if ": " in x else x
            if line.strip():
                raw.append(client_friendly_text(line.strip()))
    for x in priorities:
        line = _plain_rollup_line(x) if ": " in x else x
        if line.strip():
            raw.append(client_friendly_text(line.strip()))
    items = _consolidate_improvement_lines(raw)
    tagged: list[tuple[str, str, int, int, int]] = []
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for t in items:
        sev = _finding_severity(t)
        tagged.append(
            (
                sev,
                t,
                1 if is_manual_caveat(t) else 0,
                1 if is_policy_only_item(t) else 0,
                order.get(sev, 9),
            )
        )
    tagged.sort(
        key=lambda z: (
            z[2],
            z[3],
            z[4],
            _key_finding_cluster_order(z[1]),
            z[1].lower(),
        )
    )
    tagged = tagged[:limit]

    counts: dict[str, int] = {}
    for sev, _, __, ___, ____ in tagged:
        counts[sev] = counts.get(sev, 0) + 1
    n = len(tagged)
    summary_bits = [
        f"{counts.get('critical', 0)} critical",
        f"{counts.get('high', 0)} high",
        f"{counts.get('medium', 0)} medium",
        f"{counts.get('low', 0)} low",
    ]
    summary_txt = ", ".join(summary_bits)
    ind_note = ""
    if industry.strip():
        ind_note = f" Industry context: <strong>{html.escape(industry.strip())}</strong>."

    cards = []
    for sev, text, _cave, _pol, _ord in tagged:
        cards.append(
            f"""<div class="finding-card">
        <span class="finding-badge {sev}">{html.escape(sev)}</span>
        <span class="finding-text">{html.escape(text)}</span>
      </div>"""
        )
    cards_html = "\n      ".join(cards) if cards else '<p class="table-note">No gaps surfaced in this run—see category sections for detail.</p>'

    return f"""<section class="report-block" aria-labelledby="findings-heading">
      {_report_section_head("Key findings", "findings-heading")}
      <p class="section-lead">{n} finding(s) ranked by severity — {summary_txt}. Related themes are listed together.{ind_note}</p>
      <div class="findings-list">
      {cards_html}
      </div>
    </section>"""


def _pill_class(score: float) -> str:
    t = _tone_class(score)
    return {"green": "pill-green", "blue": "pill-blue", "yellow": "pill-yellow", "red": "pill-red"}[t]


def _sov_head_score_html(score: float) -> str:
    """Score overview / pillar rows: large score + text-coloured band (no pill)."""
    tone = _tone_class(score)
    band = _score_label(score)
    return (
        f'<span class="sov-head-metrics">'
        f'<span class="sov-head-score score-tone-{tone}">{format_report_score(score)}</span>'
        f'<span class="sov-head-band score-tone-{tone}">{html.escape(band)}</span>'
        f"</span>"
    )


def _category_section_head_suffix(score: float, weight: float) -> str:
    """Agent category h2: large score + text-coloured rating band; weight de-emphasised."""
    tone = _tone_class(score)
    band = _score_label(score)
    return (
        f'<span class="category-head-meta">'
        f'<span class="category-head-score score-tone-{tone}">{format_report_score(score)}</span>'
        f'<span class="category-head-band score-tone-{tone}">{html.escape(band)}</span>'
        f'<span class="category-head-weight">({weight:.0f}%)</span>'
        f"</span>"
    )


def _weights_from_categories(categories: list[AgentCategoryResult]) -> dict[str, float]:
    return {c.key: float(c.weight) for c in categories}


def _collect_primary_score_history(
    audit_dir: Path,
    audit: dict[str, Any],
    weights: dict[str, float],
    *,
    max_dirs_scan: int = 400,
) -> list[dict[str, Any]]:
    """Scores over time for the same primary URL as sibling folders under ``audit_dir.parent``."""
    rows_out: list[dict[str, Any]] = []
    try:
        target_norm = normalize_base(str(audit.get("base_url") or ""))
    except ValueError:
        return rows_out

    parent = audit_dir.resolve().parent
    audit_root = audit_dir.resolve()
    if not parent.is_dir():
        return rows_out

    hits: list[tuple[float, Path, dict[str, float]]] = []
    scanned = 0
    for child in parent.iterdir():
        if scanned >= max_dirs_scan:
            break
        if not child.is_dir():
            continue
        summary_path = child / "audit_summary.json"
        if not summary_path.is_file():
            continue
        scanned += 1
        try:
            other = _read_json(summary_path)
        except (OSError, json.JSONDecodeError):
            continue
        bu = str(other.get("base_url") or "").strip()
        try:
            if normalize_base(bu) != target_norm:
                continue
        except ValueError:
            continue
        ensure_brand_visibility_on_audit(other)
        try:
            ov, cats = score_audit(other, weights)
        except Exception:
            continue
        by_key = {c.key: float(c.score) for c in cats}
        mtime = summary_path.stat().st_mtime
        scores: dict[str, float] = {"overall": float(ov), **by_key}
        hits.append((mtime, child.resolve(), scores))

    hits.sort(key=lambda x: x[0])
    for mtime, path, scores in hits:
        rows_out.append(
            {
                "date": datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d"),
                "datetime": datetime.fromtimestamp(mtime, tz=UTC).strftime("%Y-%m-%d %H:%M UTC"),
                "audit_dir": str(path),
                "is_current": path == audit_root,
                **scores,
            }
        )
    return rows_out


def _format_timeseries_date(iso_date: str) -> str:
    s = (iso_date or "").strip()[:10]
    if len(s) == 10:
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%d %b")
        except ValueError:
            pass
    return s or "—"


def _score_timeseries_svg(
    history: list[dict[str, Any]],
    metric_key: str,
    *,
    stroke: str = "#0984e3",
    w: int = 220,
    h: int = 112,
) -> str:
    """Time series with Y axis (0–100) and date labels from sibling audit history."""
    pad_l, pad_r, pad_t, pad_b = 30, 10, 10, 28
    inner_w = w - pad_l - pad_r
    inner_h = h - pad_t - pad_b
    esc = html.escape(stroke, quote=True)

    points: list[tuple[str, float]] = []
    for row in history:
        if not isinstance(row, dict):
            continue
        v = row.get(metric_key)
        if not isinstance(v, (int, float)):
            continue
        date_lbl = str(row.get("date") or "")[:10]
        points.append((date_lbl, max(0.0, min(100.0, float(v)))))

    if not points:
        return (
            f'<svg class="score-timeseries score-timeseries--empty" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" aria-label="Score over time">'
            f'<text x="{pad_l}" y="{h // 2 + 4}" font-size="11" fill="#737373">No prior runs</text></svg>'
        )

    def y_for(v: float) -> float:
        return pad_t + inner_h - (v / 100.0) * inner_h

    y_axis = ""
    for tick in (0, 50, 100):
        y = y_for(float(tick))
        y_axis += (
            f'<line x1="{pad_l:.1f}" y1="{y:.1f}" x2="{w - pad_r:.1f}" y2="{y:.1f}" '
            'stroke="#1a1a1a" stroke-opacity="0.06" stroke-width="1"/>'
            f'<text x="{pad_l - 4:.1f}" y="{y + 3.5:.1f}" text-anchor="end" font-size="9" fill="#888">{tick}</text>'
        )

    n = len(points)
    coords: list[tuple[float, float, str]] = []
    for i, (date_lbl, val) in enumerate(points):
        x = pad_l + (i / (n - 1) * inner_w if n > 1 else inner_w / 2)
        coords.append((x, y_for(val), date_lbl))

    line_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in coords)
    x0, y0 = coords[0][0], coords[0][1]
    xn, yn = coords[-1][0], coords[-1][1]
    area_pts = (
        f"{x0:.1f},{pad_t + inner_h:.1f} "
        + " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in coords)
        + f" {xn:.1f},{pad_t + inner_h:.1f}"
    )

    if n == 1:
        tick_idx = {0}
    elif n == 2:
        tick_idx = {0, 1}
    elif n <= 5:
        tick_idx = set(range(n))
    else:
        tick_idx = {0, n // 2, n - 1}

    x_labels = ""
    for i in tick_idx:
        x, _, date_lbl = coords[i]
        x_labels += (
            f'<text x="{x:.1f}" y="{h - 6:.1f}" text-anchor="middle" font-size="9" fill="#666">'
            f"{html.escape(_format_timeseries_date(date_lbl))}</text>"
        )

    circles = ""
    for i, (x, y, _) in enumerate(coords):
        if n > 10 and i not in tick_idx and i not in {0, n - 1}:
            continue
        fill = esc if i == n - 1 else "#fff"
        r = 3.5 if i == n - 1 else 3
        sw = 2 if i == n - 1 else 1.75
        circles += f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" stroke="{esc}" stroke-width="{sw}"/>'

    return (
        f'<svg class="score-timeseries" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="Score over time">'
        f"{y_axis}"
        f'<polygon class="score-timeseries-area" points="{area_pts}" fill="{esc}" fill-opacity="0.1"/>'
        f'<polyline fill="none" stroke="{esc}" stroke-width="2.25" stroke-linecap="round" '
        f'stroke-linejoin="round" points="{line_pts}"/>'
        f"{circles}{x_labels}</svg>"
    )


def _sparkline_svg(values: list[float], *, w: int = 168, h: int = 52, stroke: str = "#0984e3") -> str:
    """Legacy sparkline (prefer _score_timeseries_svg when history rows are available)."""
    pad_x = 4
    pad_y = 5
    inner_w = w - pad_x * 2
    inner_h = h - pad_y * 2
    esc = html.escape(stroke, quote=True)
    if not values:
        return (
            f'<svg class="score-sparkline score-sparkline--empty" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<text x="{pad_x}" y="{h // 2 + 4}" font-size="11" fill="#737373">No prior runs</text></svg>'
        )

    def y_for(v: float) -> float:
        clamped = max(0.0, min(100.0, float(v)))
        return pad_y + inner_h - (clamped / 100.0) * inner_h

    grid = ""
    for frac in (0.25, 0.5, 0.75):
        gy = pad_y + inner_h * (1.0 - frac)
        grid += (
            f'<line x1="{pad_x:.1f}" y1="{gy:.1f}" x2="{w - pad_x:.1f}" y2="{gy:.1f}" '
            'stroke="#1a1a1a" stroke-opacity="0.06" stroke-width="1"/>'
        )

    if len(values) == 1:
        v = float(values[0])
        y = y_for(v)
        x0 = float(pad_x)
        x1 = float(pad_x + inner_w)
        area = (
            f'<polygon class="score-sparkline-area" points="{x0:.1f},{pad_y + inner_h:.1f} {x1:.1f},{pad_y + inner_h:.1f} '
            f'{x1:.1f},{y:.1f} {x0:.1f},{y:.1f}" fill="{esc}" fill-opacity="0.14"/>'
        )
        return (
            f'<svg class="score-sparkline" width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f"{grid}{area}"
            f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" stroke="{esc}" '
            'stroke-width="2.25" stroke-linecap="round"/>'
            f'<circle cx="{x0:.1f}" cy="{y:.1f}" r="3.5" fill="#fff" stroke="{esc}" stroke-width="2"/>'
            f'<circle cx="{x1:.1f}" cy="{y:.1f}" r="3.5" fill="{esc}"/></svg>'
        )

    pts: list[tuple[float, float]] = []
    n = len(values)
    for i, val in enumerate(values):
        x = pad_x + (i / (n - 1)) * inner_w
        y = y_for(val)
        pts.append((x, y))
    points_attr = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    x0, y0 = pts[0]
    xn, yn = pts[-1]
    area_pts = (
        f"{x0:.1f},{pad_y + inner_h:.1f} " + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        + f" {xn:.1f},{pad_y + inner_h:.1f}"
    )
    circles = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#fff" stroke="{esc}" stroke-width="1.75"/>'
        for x, y in pts
    )
    if len(pts) > 12:
        circles = (
            f'<circle cx="{pts[0][0]:.1f}" cy="{pts[0][1]:.1f}" r="3" fill="#fff" stroke="{esc}" stroke-width="1.75"/>'
            f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="3.5" fill="{esc}"/>'
        )
    return (
        f'<svg class="score-sparkline" width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
        f"{grid}"
        f'<polygon class="score-sparkline-area" points="{area_pts}" fill="{esc}" fill-opacity="0.12"/>'
        f'<polyline fill="none" stroke="{esc}" stroke-width="2.25" stroke-linecap="round" '
        f'stroke-linejoin="round" points="{points_attr}"/>'
        f"{circles}</svg>"
    )


def _score_sparkline_stroke(score: float) -> str:
    tone = _tone_class(score)
    return {
        "green": "#00b894",
        "blue": "#0984e3",
        "yellow": "#d4a017",
        "red": "#e17055",
    }[tone]


def _vertical_score_overview_html(
    overall: float,
    categories: list[AgentCategoryResult],
    history: list[dict[str, Any]],
) -> str:
    """Overall score hero + pillar breakdown rows with bar + sparkline."""

    pillar_tab_hash = {
        "ai_visibility": "ai-visibility",
        "technical_setup": "technical",
        "content_structure": "content",
    }

    def row_html(
        key: str,
        title: str,
        subtitle: str,
        score: float,
        *,
        row_class: str = "",
    ) -> str:
        spark = _score_timeseries_svg(
            history,
            key,
            stroke=_score_sparkline_stroke(score),
        )
        tone = _tone_class(score)
        fill_color = {
            "green": "var(--score-green)",
            "blue": "var(--score-blue)",
            "yellow": "var(--score-yellow)",
            "red": "var(--score-red)",
        }[tone]
        sub_esc = html.escape(subtitle.strip() or "—")
        cls = "sov-row"
        if row_class.strip():
            cls += " " + row_class.strip()
        frag = pillar_tab_hash.get(key)
        cta = (
            f'<a class="report-pillar-cta" href="#{frag}">Detailed report</a>'
            if frag
            else ""
        )
        score_html = _sov_head_score_html(score)
        return (
            f"""<div class="{cls}" data-score-key="{html.escape(key, quote=True)}">
  <div class="sov-main">
    <div class="sov-head">
      <div class="sov-head-text">
        <span class="sov-title">{html.escape(title)}</span>
        {cta}
      </div>
      {score_html}
    </div>
    <p class="sov-sub">{sub_esc}</p>
    <div class="sov-bar-track sov-bar-track--modern" role="img" aria-label="{html.escape(title)} score {score:.0f} out of 100">
      <div class="sov-bar-fill sov-bar-fill--modern tone-{tone}" style="width:{max(0.0, min(100.0, score)):.1f}%;background:{fill_color}"></div>
    </div>
  </div>
  <div class="sov-spark" aria-label="{html.escape('Score history for ' + title, quote=True)}">
    <div class="sov-spark-label">Over time</div>
    {spark}
  </div>
</div>"""
        )

    overall_block = row_html(
        "overall",
        "Overall GEO score",
        "Weighted blend of AI visibility, technical setup, and content quality.",
        overall,
        row_class="sov-row--overall",
    )
    cat_parts: list[str] = []
    for c in categories:
        card_sub = (c.scorecard_subtitle or c.detail).strip()
        cat_parts.append(row_html(c.key, c.title, card_sub, c.score, row_class="sov-row--pillar"))

    hist_note = ""
    if len(history) < 2:
        hist_note = (
            '<p class="sov-history-note">Re-run audits for this site (saved under the same output folder) '
            "to build a trend line.</p>"
        )
    else:
        hist_note = (
            f"<p class=\"sov-history-note\">Based on <strong>{len(history)}</strong> saved audit(s) for this URL "
            "in the same parent folder (e.g. <code>audit_output/</code>), ordered by report file date.</p>"
        )

    pillars = (
        f'<div class="sov-pillar-stack" role="region" aria-label="Breakdown by pillar">'
        f'<div class="sov-pillar-heading">Breakdown by pillar</div>'
        f'{"".join(cat_parts)}'
        f"</div>"
    )

    return f"""<div class="score-overview-vertical">
  {overall_block}
  {pillars}
  {hist_note}
</div>"""


def _ga4_has_displayable_data(ga4: dict[str, Any] | None) -> bool:
    if not ga4:
        return False
    sessions = ga4.get("monthly_sessions") or ga4.get("weekly") or []
    monthly_rev = ga4.get("monthly_ai_revenue_pct") or []
    gaps = ga4.get("source_medium_gaps") or ga4.get("ai_source_medium_gaps") or []
    misalloc = ga4.get("misallocated_ai_sources") or []
    return bool(sessions or monthly_rev or gaps or misalloc)


def _ga4_insights_lead_html(
    ga4: dict[str, Any] | None,
    audit_dir: Path,
    audit: dict[str, Any],
) -> str:
    """Gemini summary of GA4 AI traffic, or static fallback."""
    fallback = (
        "Traffic pulled from GA4. If you have specified an AI channel in the setup wizard, "
        "this has been used to identify AI traffic. Otherwise, we use the source / medium dimension."
    )
    if not ga4:
        return f'<p class="section-lead">{html.escape(fallback)}</p>'

    brand = _brand_display_name(audit)
    site = str(audit.get("base_url") or "").strip()

    try:
        from insights_llm import load_or_generate_ga4_insights

        applied = _ga4_apply_display_policy(dict(ga4))
        insights = load_or_generate_ga4_insights(
            audit_dir.resolve(),
            applied,
            brand_name=brand,
            site_url=site,
        )
    except Exception:
        insights = None

    if not insights:
        return f'<p class="section-lead">{html.escape(fallback)}</p>'

    bullets = "".join(f"<li>{html.escape(str(b))}</li>" for b in (insights.key_insights or [])[:6])
    return (
        '<div class="ga4-insights-callout callout" role="region" aria-label="GA4 AI traffic insights">'
        f'<p class="ga4-insights-headline"><strong>{html.escape(insights.headline)}</strong></p>'
        f'<p class="ga4-insights-summary">{html.escape(insights.summary)}</p>'
        f'<ul class="ga4-insights-list">{bullets}</ul>'
        '<p class="table-note ga4-insights-note">AI-generated summary from <code>ga4_traffic.json</code> '
        "(Gemini). Charts and tables below show the underlying export.</p>"
        "</div>"
    )


def _ga4_connect_prompt_html() -> str:
    return """<div class="ga4-connect-callout" role="status">
  <div class="ga4-connect-callout__title">Connect GA4 for AI traffic data</div>
  <p class="ga4-connect-callout__text">When you run a crawl with <code>--ga4-property</code> (or set <code>GA4_PROPERTY_ID</code>),
  this section shows AI channel sessions, revenue share, and source/medium gaps beside your audit.</p>
</div>"""


def _ga4_session_row_label_py(row: dict[str, Any]) -> str:
    if row.get("label"):
        return str(row["label"]).strip()
    ym = str(row.get("year_month") or "")
    if len(ym) == 6 and ym.isdigit():
        y, m = ym[:4], int(ym[4:6], 10)
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        if 1 <= m <= 12:
            return f"{names[m - 1]} {y}"
    return str(row.get("iso_week") or row.get("week") or ym or "—")


def _ga4_channel_dim_label(ga4: dict[str, Any]) -> str:
    return str(
        ga4.get("weekly_channel_dimension")
        or ga4.get("custom_channel_dimension")
        or "sessionDefaultChannelGroup"
    )


def _ga4_sessions_trend_chart_html(
    ga4: dict[str, Any] | None,
    *,
    canvas_id: str = "ga4Chart",
    data_el_id: str = "ga4-sessions-trend-data",
) -> str:
    """Chart.js monthly sessions trend (same chart as GA4 — AI traffic appendix)."""
    if not ga4:
        return ""
    g = _ga4_apply_display_policy(dict(ga4))
    sessions_trend = g.get("monthly_sessions") or g.get("weekly") or []
    if not sessions_trend:
        return ""
    dim_esc = html.escape(_ga4_channel_dim_label(g))
    safe_sessions_trend = json.dumps(sessions_trend, ensure_ascii=False)
    return f"""
<p class="table-note"><strong>AI sessions over time</strong></p>
<div class="chart-panel" style="min-height:280px"><canvas id="{html.escape(canvas_id, quote=True)}" aria-label="AI channel sessions and AI percent of all sessions"></canvas></div>
<script type="application/json" id="{html.escape(data_el_id, quote=True)}">{safe_sessions_trend}</script>
"""


def _ga4_sessions_trend_chart_script(
    *,
    canvas_id: str = "ga4Chart",
    data_el_id: str = "ga4-sessions-trend-data",
) -> str:
    cid = json.dumps(canvas_id)
    did = json.dumps(data_el_id)
    return f"""
<script>
(function() {{
  if (!window.Chart) return;
  function ga4SessionTrendLabel(row) {{
    if (!row) return '';
    if (row.label) return String(row.label);
    const ym = String(row.year_month || '');
    if (ym.length === 6 && /^\\d{{6}}$/.test(ym)) {{
      const y = ym.slice(0, 4);
      const m = parseInt(ym.slice(4, 6), 10);
      const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      if (m >= 1 && m <= 12) return names[m - 1] + ' ' + y;
    }}
    return String(row.iso_week || row.week || '');
  }}
  const sessionsTrendEl = document.getElementById({did});
  if (!sessionsTrendEl) return;
  try {{
    const sessionsTrend = JSON.parse(sessionsTrendEl.textContent);
    const ctx = document.getElementById({cid});
    if (ctx && sessionsTrend.length) {{
      const labels = sessionsTrend.map(ga4SessionTrendLabel);
      const ai = sessionsTrend.map(function(w) {{ return Number(w.ai_sessions || w.ai || 0); }});
      const tot = sessionsTrend.map(function(w) {{ return Number(w.total_sessions || w.total || 0); }});
      const pct = tot.map(function(t, i) {{ return t > 0 ? (100 * ai[i] / t) : 0; }});
      new Chart(ctx, {{
        type: 'line',
        data: {{
          labels: labels,
          datasets: [
            {{ label: 'AI channel sessions', data: ai, borderColor: '#9a4b2f', backgroundColor: 'rgba(154,75,47,0.08)', yAxisID: 'y', tension: 0.2 }},
            {{ label: 'AI % of all', data: pct, borderColor: '#5a6b4a', yAxisID: 'y1', tension: 0.2 }}
          ]
        }},
        options: {{
          responsive: true,
          maintainAspectRatio: false,
          interaction: {{ mode: 'index', intersect: false }},
          stacked: false,
          scales: {{
            y: {{ type: 'linear', position: 'left', title: {{ display: true, text: 'Sessions' }} }},
            y1: {{ type: 'linear', position: 'right', grid: {{ drawOnChartArea: false }}, title: {{ display: true, text: '% of total' }}, min: 0 }}
          }}
        }}
      }});
    }}
  }} catch (e) {{}}
}})();
</script>
"""


def _ga4_summary_sessions_preview_html(ga4: dict[str, Any] | None) -> str:
    """Summary tab: same Chart.js sessions trend as the GA4 appendix."""
    block = _ga4_sessions_trend_chart_html(
        ga4, canvas_id="ga4ChartSummary", data_el_id="ga4-sessions-trend-data-summary"
    )
    if not block:
        return ""
    init = _ga4_sessions_trend_chart_script(
        canvas_id="ga4ChartSummary", data_el_id="ga4-sessions-trend-data-summary"
    )
    return (
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>\n'
        + block
        + init
    )


def _ga4_conversion_rate_cards_html(ga4: dict[str, Any]) -> str:
    cr = ga4.get("conversion_rate")
    if not isinstance(cr, dict):
        return ""
    if cr.get("error"):
        return (
            f'<p class="table-note"><em>Conversion rate could not be loaded:</em> '
            f"{html.escape(str(cr.get('error')))}</p>"
        )

    def _fmt_rate(bucket: dict[str, Any]) -> str:
        r = bucket.get("rate_pct")
        if r is None:
            return "—"
        try:
            return f"{float(r):.2f}%"
        except (TypeError, ValueError):
            return "—"

    all_c = cr.get("all_channels") if isinstance(cr.get("all_channels"), dict) else {}
    ai_c = cr.get("ai") if isinstance(cr.get("ai"), dict) else {}
    ai_title = "Conversion rate for AI traffic"

    all_rate = _fmt_rate(all_c)
    ai_rate = _fmt_rate(ai_c)
    return f"""
<div class="ga4-conv-rate-row" style="display:flex;flex-wrap:wrap;gap:16px;margin:8px 0 20px">
  <div class="ga4-conv-card" style="flex:1 1 220px;padding:14px 16px;border:1px solid #e4e2de;border-radius:10px;background:#faf9f7">
    <div style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;color:#666">Conversion rate (average)</div>
    <div style="font-size:28px;font-weight:700;color:#0d0d0d;margin:6px 0 4px">{html.escape(all_rate)}</div>
  </div>
  <div class="ga4-conv-card" style="flex:1 1 220px;padding:14px 16px;border:1px solid #e4e2de;border-radius:10px;background:#faf9f7">
    <div style="font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;color:#666">{html.escape(ai_title)}</div>
    <div style="font-size:28px;font-weight:700;color:#0d0d0d;margin:6px 0 4px">{html.escape(ai_rate)}</div>
  </div>
</div>
"""


def _ga4_monthly_sessions_inline_chart_html(ga4: dict[str, Any] | None) -> str:
    """Deprecated: use _ga4_summary_sessions_preview_html."""
    return _ga4_summary_sessions_preview_html(ga4)


def _geo_readiness_tier_display(score: float) -> str:
    """Same verbal scale as _score_label (reserved for exec copy / future use)."""
    return _score_label(score)


def _brand_display_name(audit: dict[str, Any]) -> str:
    ai = audit.get("audit_inputs") or {}
    if (ai.get("brand") or "").strip():
        return str(ai.get("brand")).strip()
    raw = (audit.get("base_url") or "").strip()
    if not raw:
        return "Your brand"
    p = urllib.parse.urlparse(raw if "://" in raw else f"https://{raw}")
    host = (p.hostname or "").strip()
    if not host:
        return raw[:48]
    if host.startswith("www."):
        host = host[4:]
    segment = host.split(".")[0].replace("-", " ")
    return segment.title() if segment else host


def _most_impactful_finding_line(
    priorities: list[str],
    working: list[str],
    overall: float,
    categories: list[AgentCategoryResult],
) -> str:
    priorities = priorities_for_executive(list(priorities))
    prio_f = [x for x in priorities if not _is_bytespider_deprioritized(x)]
    walk_prio = prio_f if prio_f else priorities
    if overall >= 66 and working:
        t = working[0]
        t = _plain_detail_for_executive(t) if ": " in t else t
        return client_friendly_text(t)
    if walk_prio:
        t = walk_prio[0]
        t = _plain_detail_for_executive(t) if ": " in t else t
        return client_friendly_text(t)
    if working:
        t = working[0]
        t = _plain_detail_for_executive(t) if ": " in t else t
        return client_friendly_text(t)
    if categories:
        ordered = sorted(categories, key=lambda x: x.score)
        return f"{ordered[0].title} is the weakest pillar in this audit and should lead the first wave of fixes."
    return (
        "AI visibility, crawler access, and content trust signals all need coordinated upgrades—"
        "the category scores below show where to start."
    )


def _resolve_recommendations_for_report(
    audit_dir: Path,
    audit: dict[str, Any],
    overall: float,
    categories: list[AgentCategoryResult],
    priorities: list[str],
    working: list[str],
    fallback_phases: tuple[list[str], list[str], list[str], list[str]],
    projected_narrative: str | None,
) -> tuple[tuple[list[str], list[str], list[str], list[str]], str | None]:
    """Gemini action plan + projected narrative when configured; else deterministic fallback."""
    quick, medium, strategic, plan_policy = fallback_phases
    try:
        from recommendations_llm import resolve_recommendations_for_report

        phases, narrative = resolve_recommendations_for_report(
            audit_dir,
            audit,
            overall,
            categories,
            priorities,
            working,
            fallback_phases,
        )
        return phases, narrative or projected_narrative
    except Exception:
        return (quick, medium, strategic, plan_policy), projected_narrative


def _resolve_executive_summary_html(
    audit_dir: Path,
    audit: dict[str, Any],
    categories: list[AgentCategoryResult],
    overall: float,
    priorities: list[str],
    working: list[str],
) -> str:
    """Gemini executive summary when configured; deterministic template as fallback."""
    try:
        from executive_summary_llm import paragraph_for_report

        return paragraph_for_report(
            audit_dir,
            audit,
            overall,
            categories,
            priorities,
            working,
        )
    except Exception:
        return _executive_summary_paragraph_html(
            audit,
            categories,
            overall,
            priorities,
            working,
        )


def _executive_summary_paragraph_html(
    audit: dict[str, Any],
    categories: list[AgentCategoryResult],
    overall: float,
    priorities: list[str],
    working: list[str],
) -> str:
    """Executive paragraph: readable sentences, minimal bold; score/tier/domain in header only."""

    def _cap_suffix() -> str:
        notes = audit.get("_overall_score_cap_notes") or []
        if not isinstance(notes, list) or not notes:
            return ""
        primary_raw = client_friendly_text(str(notes[0]).strip()).strip().rstrip(".")
        primary = html.escape(primary_raw)
        extra = " More cap detail is listed under Technical setup." if len(notes) > 1 else ""
        return f" <strong>Overall score cap applied:</strong> {primary}.{extra}"

    pages = audit.get("pages") or []
    n = len(pages)
    page_noun = "page" if n == 1 else "pages"

    del priorities, working, overall

    fe = ""
    if isinstance(pages, list):
        for pg in pages:
            if isinstance(pg, dict) and str(pg.get("fetch_error") or "").strip():
                fe = str(pg.get("fetch_error") or "").strip()
                break
    rt = audit.get("robots_txt") if isinstance(audit.get("robots_txt"), dict) else {}
    rterr = str(rt.get("error") or "").strip() if isinstance(rt, dict) else ""
    err_blob = fe or rterr
    el = err_blob.lower()
    tls_fail = "certificate_verify_failed" in el or "certificate verify failed" in el

    if n == 0:
        opening = (
            "This run recorded <strong>no</strong> sampled pages the crawler could use—"
            "fix the start URL, TLS, or blocking, then re-run."
        )
    elif n == 1 and err_blob and tls_fail:
        opening = (
            "This report reflects <strong>1</strong> sampled page. The crawl did not widen past the seed URL because "
            "<strong>HTTPS certificate verification failed</strong> (common causes: incomplete certificate chain, "
            "corporate TLS inspection, or a proxy). Headline scores are <strong>provisional</strong>—fix public TLS "
            "so standard trust stores accept the host, then re-run for a real page sample."
        )
    elif n == 1 and err_blob:
        opening = (
            "This report reflects <strong>1</strong> sampled page. The crawl stopped before branching out because "
            "the first fetch failed. Headline scores are <strong>provisional</strong>—see Technical setup for detail, "
            "then re-crawl once the site responds reliably."
        )
    elif n <= 2 and err_blob:
        opening = (
            f"This report reflects <strong>{n}</strong> sampled {page_noun} with fetch issues—"
            "not a full pass, so treat headline scores as <strong>provisional</strong> until more URLs load cleanly."
        )
    elif n <= 2:
        opening = (
            f"This report reflects <strong>{n}</strong> sampled {page_noun} from your crawl—"
            "not a full pass, so treat headline scores as <strong>provisional</strong> until coverage grows."
        )
    else:
        opening = (
            f"This report reflects <strong>{n}</strong> sampled {page_noun} from your crawl—"
            "not every URL on the site, but enough to surface meaningful patterns."
        )

    if n == 1 and not err_blob:
        opening += (
            " <strong>Note:</strong> check sitemap discovery, crawl limits, and whether the start URL returned HTTP 200, "
            "then re-run if you expected more pages."
        )

    if not categories:
        return opening + _cap_suffix()

    ordered = sorted(categories, key=lambda category: category.score)
    weakest = ordered[0]
    strongest = ordered[-1]
    finding = (
        f"The strongest assessed area is <strong>{html.escape(strongest.title)}</strong> "
        f"at {strongest.score:.0f}/100, while <strong>{html.escape(weakest.title)}</strong> "
        f"at {weakest.score:.0f}/100 is the largest current readiness gap."
    )
    return f"{opening} {finding}" + _cap_suffix()


def _brand_visibility_platform_hits(audit: dict[str, Any] | None) -> dict[str, bool]:
    """Wikipedia / YouTube / Reddit / LinkedIn presence flags from brand_visibility_scan."""
    keys = ("wikipedia", "youtube", "reddit", "linkedin")
    empty = {k: False for k in keys}
    if not audit:
        return empty.copy()
    bv = audit.get("brand_visibility") or {}
    if bv.get("skipped"):
        return empty.copy()
    hits = empty.copy()
    for row in bv.get("platforms") or []:
        if not row.get("present"):
            continue
        pl = str(row.get("platform") or "").lower()
        if "wikipedia" in pl:
            hits["wikipedia"] = True
        elif "youtube" in pl:
            hits["youtube"] = True
        elif "reddit" in pl:
            hits["reddit"] = True
        elif "linkedin" in pl:
            hits["linkedin"] = True
    return hits


def _platform_readiness_gap_line(
    platform_key: str,
    *,
    hits: dict[str, bool],
    cit: float,
    plat: float,
    ai_srch: float,
    jld: float,
    tech_aud: float,
    crawler_rep: float,
    discovery_s: float,
    brand: float,
    eeat: float,
) -> str:
    """Rubric-first recommendation line (skills/platform-readiness.md)."""
    if platform_key == "aio":
        if cit < 56.0:
            return (
                "Add question-style H2/H3s and direct 1–2 sentence answers—AIO favors clarity beyond raw rank "
                "(confirm top-10 SEO in Search Console)."
            )
        if plat < 56.0:
            return (
                "Improve titles, meta descriptions, and on-page preview signals aligned to target queries "
                "(AIO / featured-snippet overlap)."
            )
        if ai_srch < 56.0:
            return (
                "Strengthen JSON-LD and snippet-friendly structure on key templates for Google AI surfaces."
            )
        return (
            "Validate organic top-10 for money keywords; add tables, lists, cited statistics, and author bylines."
        )
    if platform_key == "chatgpt":
        if not hits["wikipedia"]:
            return (
                "Prioritize Wikipedia + Wikidata entity work—ChatGPT web search leans on canonical entity sources."
            )
        if not hits["reddit"] and not hits["youtube"]:
            return "Grow Reddit and YouTube footprint—high citation share for ChatGPT-style web answers."
        if brand < 58.0:
            return "Raise off-site brand corroboration; align About copy with Wikipedia and social entity facts."
        return (
            "Target comprehensive pillars, Bing index health, and clear attributions for Bing-backed ChatGPT search."
        )
    if platform_key == "perplexity":
        if not hits["reddit"]:
            return (
                "Earn authentic Reddit and forum visibility—Perplexity weights community validation heavily."
            )
        if cit < 56.0:
            return (
                "Add quotable paragraphs and multi-source citations—Perplexity often stacks many sources per answer."
            )
        if ai_srch < 56.0:
            return "Improve freshness and structured date signals—Perplexity deprioritizes stale pages."
        return (
            "Publish original data and discussion-worthy angles; refresh key pages within the last 6–12 months."
        )
    if platform_key == "gemini":
        if jld < 58.0:
            return "Expand Schema.org + sameAs—Gemini uses structured data aggressively for entity understanding."
        if not hits["youtube"]:
            return (
                "Add topic-relevant YouTube with chapters/captions—Gemini over-weights video vs classic search."
            )
        if tech_aud < 56.0:
            return "Tighten crawl/index and Google surface hygiene (GBP, Knowledge Panel) for multi-modal answers."
        return (
            "Strengthen E-E-A-T pages, image alt text, and Maps/News ecosystem touchpoints where relevant."
        )
    if platform_key == "claude":
        if crawler_rep < 55.0:
            return (
                "Allow ClaudeBot and keep key pages fetchable—Claude rubric weights crawler access and SSR-friendly HTML."
            )
        if cit < 55.0:
            return (
                "Improve summaries, headings, and self-contained passages so long-form answers extract cleanly."
            )
        if eeat < 55.0:
            return (
                "Strengthen sourcing, caveats, and publisher/author clarity—the Claude rubric stresses trustworthy attribution."
            )
        if discovery_s < 45.0:
            return (
                "Curate llms.txt toward documentation and source-of-truth URLs (optional signal in the Claude rubric)."
            )
        return (
            "Add nuanced expert synthesis, original examples, and consistent entity facts across About and schema."
        )
    # copilot
    if crawler_rep < 62.0:
        return (
            "Fix crawl/access and Bing-facing meta descriptions—Copilot shares Bing’s index with ChatGPT web search."
        )
    if discovery_s < 48.0:
        return (
            "Improve discovery signals (live llms.txt + sitemap reachability)—Copilot/Bing ecosystem hygiene."
        )
    if not hits["linkedin"]:
        return "Complete the LinkedIn company page—Microsoft ecosystem signal for Copilot."
    if tech_aud < 58.0:
        return (
            "Verify Bing Webmaster Tools, sitemaps, IndexNow, and performance targets."
        )
    return (
        "Layer IndexNow, compelling meta descriptions, and natural exact-match headings for Bing literal retrieval."
    )


def _platform_readiness_scores(
    *,
    overall: float,
    agents: list[AgentCategoryResult],
    ai_crawler_score: float,
    audit: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Heuristic 0–100 readiness for six surfaces (skills/platform-readiness.md): Google AIO, ChatGPT,
    Perplexity, Gemini, Bing Copilot, and optional Claude. Blends AI visibility / content / technical
    subs + brand_visibility scan; discovery uses `discovery_signals` (llms.txt + sitemap). Manual
    rubric items (GSC top-10, Bing WMT, IndexNow, live citations) still need verification.
    """
    cit = _sub_score(agents, "ai_visibility", "ai_citability")
    plat = _sub_score(agents, "ai_visibility", "platform_readiness")
    ai_srch = _sub_score(agents, "ai_visibility", "ai_search_success")
    brand = _sub_score(agents, "ai_visibility", "brand_visibility")
    tech_aud = next((a.score for a in agents if a.key == "technical_setup"), 0.0)
    crawler_rep = _sub_score(agents, "technical_setup", "ai_crawler_report")
    discovery_s = _sub_score(agents, "technical_setup", "discovery_signals")
    eeat = _sub_score(agents, "content_structure", "eeat")
    jld = _sub_score(agents, "content_structure", "json_ld")

    hits = _brand_visibility_platform_hits(audit)

    def clamp(x: float) -> float:
        return max(0.0, min(100.0, x))

    entity_mix = min(
        100.0,
        (38.0 if hits["wikipedia"] else 6.0)
        + (27.0 if hits["youtube"] else 9.0)
        + (22.0 if hits["reddit"] else 9.0)
        + (13.0 if hits["linkedin"] else 7.0),
    )
    comm = min(
        100.0,
        15.0
        + (38.0 if hits["reddit"] else 10.0)
        + (28.0 if hits["youtube"] else 12.0)
        + (24.0 if hits["wikipedia"] else 10.0)
        + (18.0 if hits["linkedin"] else 8.0),
    )

    # Weights tuned to rubric emphasis in platform-readiness.md (automated proxies only).
    aio_score = clamp(
        0.26 * cit
        + 0.20 * plat
        + 0.18 * ai_srch
        + 0.15 * jld
        + 0.09 * tech_aud
        + 0.06 * crawler_rep
        + 0.06 * overall
    )
    gpt_score = clamp(
        0.18 * brand
        + 0.15 * cit
        + 0.14 * ai_srch
        + 0.12 * jld
        + 0.10 * tech_aud
        + 0.07 * overall
        + 0.14 * entity_mix
        + 0.10 * crawler_rep
    )
    perp_score = clamp(
        0.26 * cit
        + 0.20 * ai_srch
        + 0.17 * brand
        + 0.14 * jld
        + 0.13 * comm
        + 0.10 * plat
    )
    gem_score = clamp(
        0.34 * jld
        + 0.22 * ai_srch
        + 0.16 * tech_aud
        + 0.11 * cit
        + 0.09 * plat
        + 0.08 * eeat
        + (11.0 if hits["youtube"] else 0.0)
    )
    cop_score = clamp(
        0.20 * brand
        + 0.18 * crawler_rep
        + 0.17 * tech_aud
        + 0.15 * cit
        + 0.11 * plat
        + 0.15 * discovery_s
        + 0.04 * jld
        + (7.0 if hits["linkedin"] else 0.0)
    )
    claude_score = clamp(
        0.22 * crawler_rep
        + 0.18 * cit
        + 0.16 * eeat
        + 0.14 * jld
        + 0.12 * plat
        + 0.10 * brand
        + 0.08 * discovery_s
    )

    _gap = lambda pk: _platform_readiness_gap_line(
        pk,
        hits=hits,
        cit=cit,
        plat=plat,
        ai_srch=ai_srch,
        jld=jld,
        tech_aud=tech_aud,
        crawler_rep=crawler_rep,
        discovery_s=discovery_s,
        brand=brand,
        eeat=eeat,
    )

    scores = [
        {
            "key": "aio",
            "name": "Google AI Overviews",
            "icon": "📰",
            "score": aio_score,
            "gap": _gap("aio"),
        },
        {
            "key": "chatgpt",
            "name": "ChatGPT",
            "icon": "🤖",
            "score": gpt_score,
            "gap": _gap("chatgpt"),
        },
        {
            "key": "perplexity",
            "name": "Perplexity",
            "icon": "🔎",
            "score": perp_score,
            "gap": _gap("perplexity"),
        },
        {
            "key": "gemini",
            "name": "Google Gemini",
            "icon": "✨",
            "score": gem_score,
            "gap": _gap("gemini"),
        },
        {
            "key": "copilot",
            "name": "Microsoft Copilot",
            "icon": "🧭",
            "score": cop_score,
            "gap": _gap("copilot"),
        },
        {
            "key": "claude",
            "name": "Claude",
            "icon": "📚",
            "score": claude_score,
            "gap": _gap("claude"),
        },
    ]

    access_penalty = 0.0
    if ai_crawler_score < 50:
        access_penalty = 10.0
    elif ai_crawler_score < 70:
        access_penalty = 5.0

    for s in scores:
        s["score"] = clamp(float(s["score"]) - access_penalty)
    return scores


def _eeat_breakdown(
    *,
    audit: dict[str, Any],
    agents: list[AgentCategoryResult],
    ai_crawler_score: float,
) -> list[dict[str, Any]]:
    """Score each E-E-A-T criterion directly from sampled website content."""
    del agents, ai_crawler_score  # E-E-A-T cards no longer blend unrelated category scores.
    pages = [
        page for page in (audit.get("pages") or [])
        if isinstance(page, dict) and int(page.get("http_status") or 0) == 200
    ]
    page_count = len(pages)

    legacy_patterns: dict[str, tuple[str, ...]] = {
        "experience": (r"\b(?:we|our team|i)\b.{0,100}\b(?:tested|used|built|found|learned|implemented)\b", r"\bcase study\b"),
        "expertise": (r"\b(?:expert|qualified|certified|research|study|methodology|according to)\b",),
        "authoritativeness": (r"\b(?:award|recognised|recognized|featured in|trusted by|accredited by|member of)\b",),
        "trust": (r"\b(?:privacy policy|terms and conditions|contact us|reviewed by|last updated|sources?|references?)\b",),
    }

    def page_evidence(page: dict[str, Any], criterion: str) -> list[str]:
        signals = page.get("content_signals") if isinstance(page.get("content_signals"), dict) else {}
        evidence = signals.get("eeat_evidence") if isinstance(signals.get("eeat_evidence"), dict) else {}
        snippets = [
            str(snippet).strip()
            for snippet in (evidence.get(criterion) or [])
            if str(snippet).strip()
        ]
        if snippets:
            return snippets
        # Compatibility for audits created before criterion-specific capture.
        excerpt = str(signals.get("representative_excerpt") or "").strip()
        if excerpt and any(re.search(pattern, excerpt, flags=re.IGNORECASE) for pattern in legacy_patterns[criterion]):
            return [excerpt]
        return []

    def signal_ratio(key: str) -> float:
        if not page_count:
            return 0.0
        matched = 0
        for page in pages:
            content = page.get("content_signals") if isinstance(page.get("content_signals"), dict) else {}
            signals = content.get("eeat_signals") if isinstance(content.get("eeat_signals"), dict) else {}
            if signals.get(key):
                matched += 1
        return matched / page_count

    def evidence_metrics(criterion: str) -> tuple[float, float, list[dict[str, str]]]:
        if not page_count:
            return 0.0, 0.0, []
        evidence_pages = 0
        total_snippets = 0
        examples: list[dict[str, str]] = []
        for page in pages:
            snippets = page_evidence(page, criterion)
            if not snippets:
                continue
            evidence_pages += 1
            total_snippets += len(snippets)
            for snippet in snippets:
                examples.append({
                    "url": str(page.get("final_url") or page.get("url") or "").strip(),
                    "title": str(page.get("page_title") or page.get("final_url") or page.get("url") or "Sampled page").strip(),
                    "snippet": snippet[:420],
                })
        coverage = evidence_pages / page_count
        density = min(1.0, total_snippets / max(1.0, page_count * 1.5))
        return coverage, density, examples[:3]

    editorial_ratio = (
        sum(
            1 for page in pages
            if isinstance(page.get("content_signals"), dict)
            and page["content_signals"].get("has_editorial_content")
        ) / page_count
        if page_count else 0.0
    )

    exp_cov, exp_density, exp_examples = evidence_metrics("experience")
    expertise_cov, expertise_density, expertise_examples = evidence_metrics("expertise")
    authority_cov, authority_density, authority_examples = evidence_metrics("authoritativeness")
    trust_cov, trust_density, trust_examples = evidence_metrics("trust")

    experience = _clamp100(
        45.0 * exp_cov
        + 25.0 * exp_density
        + 20.0 * signal_ratio("process")
        + 10.0 * (editorial_ratio if exp_examples else 0.0)
    )
    expertise = _clamp100(
        35.0 * expertise_cov
        + 20.0 * expertise_density
        + 15.0 * signal_ratio("author")
        + 15.0 * signal_ratio("credentials")
        + 15.0 * signal_ratio("sources")
    )
    authoritativeness = _clamp100(
        55.0 * authority_cov
        + 25.0 * authority_density
        + 15.0 * signal_ratio("recognition")
        + 5.0 * (editorial_ratio if authority_examples else 0.0)
    )
    trust = _clamp100(
        35.0 * trust_cov
        + 15.0 * trust_density
        + 15.0 * signal_ratio("policy")
        + 10.0 * signal_ratio("contact")
        + 10.0 * signal_ratio("sources")
        + 10.0 * signal_ratio("review_date")
        + 5.0 * signal_ratio("author")
    )

    def result(
        *,
        name: str,
        criterion: str,
        tagline: str,
        meaning: str,
        score: float,
        examples: list[dict[str, str]],
    ) -> dict[str, Any]:
        if examples:
            evidence_note = (
                f"{len(examples)} example snippet{'s' if len(examples) != 1 else ''} "
                f"from {page_count} sampled page{'s' if page_count != 1 else ''} supported this score."
            )
        else:
            evidence_note = (
                f"No matching {criterion} content was found in the "
                f"{page_count} sampled page{'s' if page_count != 1 else ''}."
                if page_count
                else "No successfully crawled pages were available for content evidence."
            )
        return {
            "name": name,
            "tagline": tagline,
            "what_it_means": meaning,
            "how_scored": (
                f"A direct 0–100 assessment of how strongly and consistently the sampled website content "
                f"demonstrates {criterion}. Unrelated technical or category scores are not included."
            ),
            "score": round(score, 1),
            "evidence": examples,
            "evidence_note": evidence_note,
        }

    return [
        result(
            name="Experience",
            criterion="first-hand experience",
            tagline="Does the content show real use, testing or practice?",
            meaning="Looks for first-hand observations, testing, case studies, methods, processes and specific outcomes.",
            score=experience,
            examples=exp_examples,
        ),
        result(
            name="Expertise",
            criterion="subject-matter expertise",
            tagline="Does the content demonstrate credible subject knowledge?",
            meaning="Looks for named authors or reviewers, relevant credentials, research, sources, methodology and detailed explanation.",
            score=expertise,
            examples=expertise_examples,
        ),
        result(
            name="Authoritativeness",
            criterion="authoritativeness",
            tagline="Does the content establish recognised authority?",
            meaning="Looks for awards, accreditation, trusted partnerships, external recognition and clear evidence of standing in the field.",
            score=authoritativeness,
            examples=authority_examples,
        ),
        result(
            name="Trust",
            criterion="trust and transparency",
            tagline="Does the content make the publisher accountable and reliable?",
            meaning="Looks for policies, contact details, sourcing, disclosures, named review and visible publication or update information.",
            score=trust,
            examples=trust_examples,
        ),
    ]


_STRATEGIC_HORIZON_RE = re.compile(
    r"(?i)\b(reddit|wikipedia|content\s+clusters?|cluster(s)?\s+for|long[\s-]term|"
    r"original\s+content|based\s+on\s+research|community\s+building|"
    r"thought\s+leadership|editorial\s+(program|calendar|governance)|90\s*\+|"
    r"brand\s+campaigns?|partnerships?|marketing\s+efforts|digital\s+pr|"
    r"internationali[sz]ation|hreflang|youtube\s+program|ssr\s+migrat|"
    r"performance\s+refactor)\b"
)
_MEDIUM_HORIZON_RE = re.compile(
    r"(?i)\b(30[\s–-]90|sitewide|site[\s-]wide|all\s+pages|every\s+page|"
    r"templates?|bulk\s+|\bprogramme\b|\bmigration\b|"
    r"overall\s+site\s+schema|metadata\s+.+\s+(across|site|sites)|"
    r"product\s+schema\s+on|adjust(ing)?\s+site\s+content|"
    r"ai[- ]friendly\s+content|off[- ]site|footprint|internal\s+link|"
    r"sitemap\s+clean|indexnow|author\s+bio|content\s+refresh|"
    r"schema\s+at\s+scale)\b"
)
_QUICK_HORIZON_RE = re.compile(
    r"(?i)\b(robots\.txt|llms\.txt|json-ld|json\s+ld|"
    r"schema\.org|og:image|open\s+graph|sameas|whitelist|user-agent|"
    r"\bcrawler\b|\ballow\b|disallow|published\s+llms|generated\s+llms|"
    r"homepage\s+title|html\s+<title>|fix\s+syntax|tls|https|certifi|"
    r"parse\s+robots|noindex|nosnippet|max-snippet|pilot|starter|"
    r"priority\s+pages?|top\s+\d+\s+pages?)\b"
)


def _priority_horizon(text: str) -> str:
    """Classify one line into quick | medium | strategic (internal heuristics)."""
    if _STRATEGIC_HORIZON_RE.search(text):
        return "strategic"
    if _MEDIUM_HORIZON_RE.search(text):
        return "medium"
    if _QUICK_HORIZON_RE.search(text):
        return "quick"
    return "medium"


def _priorities_spaced(
    priorities: list[str], *, max_per: int = 5
) -> tuple[list[str], list[str], list[str]]:
    """
    At most max_per items per column. Walks the global priority order; overflow
    from 'quick' defers to medium, then strategic (skills/action-plan.md).
    """
    fq: list[str] = []
    fm: list[str] = []
    fs: list[str] = []
    for t in priorities:
        h = _priority_horizon(t)
        if h == "quick":
            if len(fq) < max_per:
                fq.append(t)
            elif len(fm) < max_per:
                fm.append(t)
            elif len(fs) < max_per:
                fs.append(t)
        elif h == "medium":
            if len(fm) < max_per:
                fm.append(t)
            elif len(fs) < max_per:
                fs.append(t)
        else:
            if len(fs) < max_per:
                fs.append(t)
    return fq, fm, fs


def _plain_rollup_line(text: str) -> str:
    """Drop 'Sub-area: detail' prefix for shorter executive copy."""
    if ": " in text:
        return text.split(": ", 1)[1].strip()
    return text


def _plain_detail_for_executive(text: str) -> str:
    """Like rollup, but keep the full line when the detail half is too short to stand alone."""
    text = (text or "").strip()
    if ": " not in text:
        return text
    detail = text.split(": ", 1)[1].strip()
    if len(detail) < 16:
        return text
    return detail


def _normalize_insight_line(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _insight_dedup_key(text: str, *, strengths: bool) -> str:
    """
    Key for cross-sub deduplication. Improvements use the same normalized signature as
    _finding_similarity so consolidated / near-identical bullets collapse to one owner.
    Strengths stay exact-match (whitespace-normalized) to avoid over-merging positives.
    """
    if strengths:
        return _normalize_insight_line(text)
    sig = _normalized_finding_sig(text)
    return sig if sig else _normalize_insight_line(text)


# Sub-keys → percent weight within category (for deduping repeated bullets: keep under highest-weight sub).
_INSIGHT_DEDUP_WEIGHTS: dict[str, dict[str, float]] = {
    "ai_visibility": {
        "ai_citability": 30.0,
        "platform_readiness": 25.0,
        "ai_search_success": 15.0,
        "brand_entity_visibility": 15.0,
        "query_coverage_footprint": 15.0,
        "brand_visibility": 5.0,
    },
    "technical_setup": {
        "indexability_crawl_health": 25.0,
        "ai_crawler_report": 25.0,
        "ssr_html_completeness": 20.0,
        "performance_page_experience": 15.0,
        "discovery_signals": 15.0,
    },
    "content_structure": {
        "eeat": 35.0,
        "original_information_gain": 20.0,
        "passage_answerability": 20.0,
        "schema_entity_markup": 15.0,
        "json_ld": 12.0,
        "source_transparency_governance": 10.0,
    },
}


def _dedupe_insight_lines_across_subs(
    subs: list[AgentSubResult],
    agent_key: str,
    *,
    strengths: bool,
) -> list[tuple[str, list[str]]]:
    """
    Each distinct recommendation appears once, under the sub with the highest category weight.
    Ties use original sub order (earlier wins).
    """
    weights = _INSIGHT_DEDUP_WEIGHTS.get(agent_key, {})
    order = {s.key: i for i, s in enumerate(subs)}

    per_sub_lines: list[tuple[AgentSubResult, list[str]]] = []
    for s in subs:
        raw = list(s.strengths) if strengths else list(s.improvements)
        lines = _consolidate_strength_lines(raw) if strengths else _consolidate_improvement_lines(raw)
        if lines:
            per_sub_lines.append((s, lines))

    # dedup key -> (weight, order_idx, sub_key, sub_title, display text for this key)
    best: dict[str, tuple[float, int, str, str, str]] = {}
    for s, lines in per_sub_lines:
        w = float(weights.get(s.key, 0.0))
        oidx = order[s.key]
        for line in lines:
            dk = _insight_dedup_key(line, strengths=strengths)
            if not dk:
                continue
            cur = best.get(dk)
            cand = (w, oidx, s.key, s.title, line)
            if cur is None or cand[0] > cur[0] or (cand[0] == cur[0] and cand[1] < cur[1]):
                best[dk] = cand

    out: list[tuple[str, list[str]]] = []
    for s in subs:
        raw = list(s.strengths) if strengths else list(s.improvements)
        lines = _consolidate_strength_lines(raw) if strengths else _consolidate_improvement_lines(raw)
        if not lines:
            continue
        picked: list[str] = []
        seen_key: set[str] = set()
        for line in lines:
            dk = _insight_dedup_key(line, strengths=strengths)
            if not dk:
                continue
            b = best.get(dk)
            if not b or b[2] != s.key:
                continue
            if dk in seen_key:
                continue
            seen_key.add(dk)
            picked.append(b[4])
        if picked:
            out.append((s.title, picked))
    return out


def _insight_groups_from_category(
    agent: AgentCategoryResult,
    audit: dict[str, Any] | None,
) -> tuple[list[tuple[str, list[str]]], list[tuple[str, list[str]]]]:
    """Pair of (sub-area title, bullet lines) for strengths and improvements."""
    good = _dedupe_insight_lines_across_subs(agent.subs, agent.key, strengths=True)
    bad = _dedupe_insight_lines_across_subs(agent.subs, agent.key, strengths=False)
    if agent.key == "technical_setup" and audit:
        notes = audit.get("_overall_score_cap_notes") or []
        cap_bullets = [str(n).strip() for n in notes if str(n).strip()]
        if cap_bullets:
            bad.append(("Overall score cap", cap_bullets))
    return good, bad


def _insight_grouped_body_html(
    groups: list[tuple[str, list[str]]],
    *,
    empty_message: str,
) -> str:
    if not groups:
        return f'<p class="insight-box__empty">{html.escape(empty_message)}</p>'
    blocks: list[str] = []
    for title, items in groups:
        lis = "".join(
            f"<li>{html.escape(client_friendly_finding(t, 'insight'))}</li>" for t in items
        )
        blocks.append(
            f'<div class="insight-group">'
            f'<div class="insight-group__title">{html.escape(title)}:</div>'
            f"<ul class='insight-list insight-list--nested'>{lis}</ul>"
            f"</div>"
        )
    return f"<div class='insight-grouped'>{''.join(blocks)}</div>"


def _insight_pair_grouped_html(
    good_groups: list[tuple[str, list[str]]],
    bad_groups: list[tuple[str, list[str]]],
    *,
    empty_good: str = "Nothing highlighted yet.",
    empty_bad: str = "No gaps recorded yet.",
) -> str:
    return f"""<div class="insight-pair">
  <div class="insight-box insight-box--positive">
    <div class="insight-box__label">What is working well</div>
    <div class="insight-box__body">{_insight_grouped_body_html(good_groups, empty_message=empty_good)}</div>
  </div>
  <div class="insight-box insight-box--attention">
    <div class="insight-box__label">What needs work</div>
    <div class="insight-box__body">{_insight_grouped_body_html(bad_groups, empty_message=empty_bad)}</div>
  </div>
</div>"""


def _priority_table_html(items: list[str], *, empty_message: str = "None noted.") -> str:
    if not items:
        return (
            "<table class='data-table'><tbody>"
            f"<tr><td><em>{html.escape(empty_message)}</em></td></tr>"
            "</tbody></table>"
        )
    rows = []
    for i, t in enumerate(items, 1):
        rows.append(f"<tr><td><strong>{i}.</strong> {html.escape(t)}</td></tr>")
    return (
        "<table class='data-table'><thead><tr>"
        "<th>Observation</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def ensure_brand_visibility_on_audit(audit: dict[str, Any]) -> None:
    """Populate audit['brand_visibility'] with live probes when missing (not if crawl skipped scan)."""
    raw = audit.get("brand_visibility")
    if isinstance(raw, dict) and raw.get("skipped"):
        return
    if isinstance(raw, dict) and raw.get("platforms"):
        return
    try:
        import brand_visibility_scan as bvs
    except ImportError:
        audit["brand_visibility"] = {
            "brand_query": "",
            "brand_source": "unavailable",
            "base_url": audit.get("base_url") or "",
            "platforms": [],
            "method_note": "Could not import brand_visibility_scan (run from repo root).",
        }
        return
    base = audit.get("base_url") or ""
    if not base:
        return
    bq = ""
    if isinstance(raw, dict):
        bq = (raw.get("brand_query") or "").strip()
    if not bq:
        bq = bvs.derive_brand_from_base(base)
    audit["brand_visibility"] = bvs.scan_brand_platforms(
        bq, base, delay=0.15, brand_source="report_fallback"
    )


def _brand_visibility_reddit_status_display(status: str, *, present: bool) -> str:
    """Hide Reddit post titles in the plain row; thread titles appear only inside the details dropdown."""
    s = (status or "").strip()
    if not present:
        return s
    low = s.lower()
    if "discussion found" in low or "relevant discussion" in low:
        return "Relevant discussion surfaced on Reddit—use Open to review the thread."
    return s


def _brand_visibility_reddit_channel_cell(row: dict[str, Any]) -> str:
    """HTML for Reddit row: summary + expandable top threads with ML sentiment."""
    threads = list(row.get("reddit_threads") or [])
    if not threads:
        return ""
    summary = html.escape(str(row.get("reddit_sentiment_summary") or "").strip())
    model = html.escape(str(row.get("reddit_sentiment_model") or "").strip())
    err = (row.get("reddit_sentiment_error") or "").strip()
    err_html = (
        f'<p class="table-note reddit-sentiment-err">{html.escape(err)}</p>' if err else ""
    )
    items: list[str] = []
    for th in threads:
        title = html.escape(str(th.get("title") or "Thread"))
        turl = html.escape(str(th.get("url") or "").strip())
        sub = html.escape(str(th.get("subreddit") or "").strip())
        lab = str(th.get("sentiment_label") or "mixed").lower()
        pill = {
            "positive": "pill-green",
            "negative": "pill-red",
            "mixed": "pill-yellow",
        }.get(lab, "pill-yellow")
        pos = float(th.get("sentiment_positive") or 0.5)
        neg = float(th.get("sentiment_negative") or 0.5)
        ncom = int(th.get("top_comment_sample_n") or 0)
        prev = html.escape(str(th.get("comment_preview") or ""))
        snote = html.escape(str(th.get("sentiment_note") or ""))
        sub_note = f"r/{sub}" if sub else ""
        sub_html = f' <span class="reddit-sub">{sub_note}</span>' if sub_note else ""
        items.append(
            "<li class='reddit-thread-snippet'>"
            f"<div class='reddit-thread-title-row'><strong>{title}</strong> "
            f"<span class='score-pill {pill}'>{html.escape(lab)}</span>{sub_html}"
            "</div>"
            f"<div class='reddit-sentiment-bar'>Positive {pos:.0%} · Negative {neg:.0%} · "
            f"Comments scored: {ncom}</div>"
            f"<p class='reddit-comment-preview'>{prev}</p>"
            f"<p class='table-note'>{snote}</p>"
            f"<a href=\"{turl}\" target=\"_blank\" rel=\"noopener noreferrer\">Open thread</a>"
            "</li>"
        )
    list_html = "<ol class='reddit-thread-list'>" + "".join(items) + "</ol>"
    u0 = (threads[0].get("url") or "").strip()
    open_first = ""
    if u0:
        open_first = (
            f' <a href="{html.escape(u0)}" target="_blank" rel="noopener noreferrer">Open first thread</a>'
        )
    return (
        '<div class="reddit-channel-wrap">'
        f'<p class="reddit-summary-line"><strong>{summary}</strong>{open_first}</p>'
        f"{err_html}"
        '<details class="reddit-threads-details">'
        f'<summary>Top {len(threads)} Reddit threads & sentiment ({model})</summary>'
        "<p class=\"table-note\">Each line uses DistilBERT (SST-2) on the thread title plus the highest-scoring "
        "comments returned by Reddit's JSON API. This is a directional read—confirm tone in the live thread.</p>"
        f"{list_html}"
        "</details></div>"
    )


def _brand_visibility_subsection_html(audit: dict[str, Any]) -> str:
    bv = audit.get("brand_visibility") or {}
    heading = f"""<div class="score-breakdown-sub score-breakdown-nested" aria-labelledby="brand-vis-heading">
  {_report_subhead("Brand visibility", "brand-vis-heading")}"""

    if bv.get("skipped"):
        inner = """<p class="score-breakdown-subdesc">Off-site checks for major platforms.</p>
  <p class="table-note">Scan was skipped during crawl (<code>--no-brand-scan</code>). Re-run without that flag to populate this table.</p>"""
        return heading + inner + "\n</div>"

    platforms = bv.get("platforms") or []
    if not platforms:
        inner = """<p class="score-breakdown-subdesc">Off-site checks for major platforms.</p>
  <p class="table-note">No platform rows in audit. Re-run crawl or open this report from the repo so the scan module can run.</p>"""
        return heading + inner + "\n</div>"

    bq = html.escape(str(bv.get("brand_query") or ""))
    desc = f"""<p class="score-breakdown-subdesc">This section checks whether the brand <strong>{bq}</strong> is visible on major third-party platforms that AI systems often use for verification. Automated matches should be confirmed manually before treating a URL as an official profile or adding it to structured data.</p>"""
    note = (bv.get("method_note") or "").strip()
    note_html = (
        f'<p class="table-note">{html.escape(note)}</p>' if note else ""
    )

    rows_html: list[str] = []
    for row in platforms:
        platform = html.escape(str(row.get("platform") or "—"))
        present = bool(row.get("present"))
        is_reddit = str(row.get("platform") or "").strip().lower() == "reddit"
        pill = (
            '<span class="score-pill pill-green">Likely yes</span>'
            if present
            else '<span class="score-pill pill-red">No / unclear</span>'
        )
        reddit_threads = list(row.get("reddit_threads") or []) if is_reddit else []
        if is_reddit and reddit_threads:
            channel_cell = _brand_visibility_reddit_channel_cell(row)
        else:
            st_raw = str(row.get("status") or "—")
            if is_reddit:
                st_raw = _brand_visibility_reddit_status_display(st_raw, present=present)
            status_esc = html.escape(st_raw)
            url = row.get("url")
            if url and isinstance(url, str):
                link = (
                    f' <a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">Open</a>'
                )
            else:
                link = ""
            channel_cell = f"{status_esc}{link}"
        impact = html.escape(str(row.get("impact") or "—"))
        rows_html.append(
            "<tr>"
            f"<td><strong>{platform}</strong></td>"
            f"<td>{pill}</td>"
            f"<td>{channel_cell}</td>"
            f"<td>{impact}</td>"
            "</tr>"
        )
    table = (
        "<table class='data-table' aria-label='Brand visibility off-site scan'>"
        "<thead><tr>"
        "<th>Platform</th><th>Presence</th><th>Channel / page</th>"
        "<th>Impact on AI visibility</th>"
        "</tr></thead><tbody>"
        + "".join(rows_html)
        + "</tbody></table>"
    )
    return f"""{heading}
  {desc}
  {note_html}
  {table}
</div>"""


def _geo_readiness_lift_hint(text: str) -> str:
    """Illustrative composite GEO lift from keyword heuristics; bands follow skills/action-plan.md (not additive)."""
    t = text.lower()
    if any(x in t for x in ("noindex", "nosnippet", "max-snippet")):
        return "Est. +4–10 pts overall"
    if re.search(
        r"(?i)\b(javascript|client-rendered|js-only|react|vue|angular)\b", text
    ) or "client rendered" in t or "js only" in t:
        return "Est. +5–12 pts overall"
    if any(
        x in t
        for x in (
            "answer-first",
            "answer first",
            "direct answer",
            "quotable",
            "passage",
        )
    ):
        return "Est. +3–8 pts overall"
    if any(
        x in t
        for x in (
            "robots",
            "crawler",
            "disallow",
            "whitelist",
            "user-agent",
            "gptbot",
            "perplexity",
            "oai-search",
            "claudebot",
        )
    ):
        return "Est. +4–9 pts overall"
    if "llms.txt" in t or "llms " in t:
        return "Est. +1–3 pts overall"
    if any(
        x in t
        for x in (
            "json-ld",
            "json ld",
            "structured data",
            "schema.org",
            "schema ",
        )
    ):
        if "homepage" in t or "pilot" in t or "starter" in t:
            return "Est. +1–4 pts overall"
        if any(x in t for x in ("roll out", "rollout", "templates", "sitewide", "all pages")):
            return "Est. +3–8 pts overall"
        return "Est. +3–7 pts overall"
    if any(
        x in t
        for x in ("open graph", "og:", "og:image", "twitter card", "social preview")
    ):
        return "Est. +2–6 pts overall"
    if any(
        x in t
        for x in (
            "sameas",
            "same as",
            "wikipedia",
            "linkedin",
            "wikidata",
            "trustpilot",
            "youtube channel",
            "brand footprint",
        )
    ):
        return "Est. +2–6 pts overall"
    if any(
        x in t
        for x in ("sitewide", "all pages", "template", "cms ", "roll out", "rollout")
    ):
        return "Est. +5–12 pts overall"
    if any(
        x in t
        for x in ("tls", "https", "certificate", "parse error", "canonical", "redirect")
    ):
        return "Est. +2–6 pts overall"
    if any(
        x in t
        for x in (
            "reddit",
            "cluster",
            "pillar",
            "research",
            "editorial",
            "calendar",
            "community",
        )
    ):
        return "Est. +4–10 pts overall"
    if any(
        x in t
        for x in (
            "research-led content",
            "citability as a roadmap",
            "brand corroboration",
            "editorial governance",
            "originality push",
        )
    ):
        return "Est. +5–12 pts overall"
    if any(x in t for x in ("title", "description", "meta ", "heading", "h1")):
        return "Est. +1–3 pts overall"
    return "Est. +2–6 pts overall"


def _parse_lift_points_from_hint(hint: str) -> tuple[float, float]:
    """Extract (low, high) from strings like 'Est. +4–9 pts overall' or 'Est. +6 pts'."""
    m = re.search(r"\+(\d+)\s*[–\-]\s*(\d+)", hint)
    if m:
        return float(m.group(1)), float(m.group(2))
    m2 = re.search(r"\+(\d+)", hint)
    if m2:
        v = float(m2.group(1))
        return v, v
    return 2.0, 6.0


def _phase_expected_lift(
    items: list[str], *, realized_factor: float, cap: float
) -> float:
    """Sum midpoint lifts from per-item hints, dampened (actions overlap in practice)."""
    if not items:
        return 0.0
    total_mid = 0.0
    for raw in items:
        hint = _geo_readiness_lift_hint(raw)
        lo, hi = _parse_lift_points_from_hint(hint)
        total_mid += (lo + hi) / 2.0
    return min(cap, total_mid * realized_factor)


def _projected_performance_html(
    overall: float,
    quick: list[str],
    medium: list[str],
    strategic: list[str],
    *,
    narrative_override: str | None = None,
) -> str:
    """
    Horizontal milestone timeline (design/use_this_design.html).
    Uses illustrative lift ranges from action-plan hints; not a guarantee.
    """
    cur_f = max(0.0, min(100.0, float(overall)))
    cur = int(round(cur_f))
    gap = 100 - cur

    use_llm_desc = bool(narrative_override and str(narrative_override).strip())

    if gap <= 0:
        steps = [
            (cur, "Current", "Today", "var(--score-green)"),
            (cur, "Quick wins", "~30 days", "var(--score-green)"),
            (cur, "Medium-term", "~90 days", "var(--score-green)"),
            (cur, "Strategic", "6+ months", "var(--score-green)"),
        ]
        desc = (
            "Composite score is already at the top of this model’s range—focus on sustaining signals "
            "and validating with analytics rather than chasing numeric lift."
        )
    else:
        g_quick = _phase_expected_lift(
            quick, realized_factor=0.42, cap=max(10.0, gap * 0.52)
        )
        g_med = _phase_expected_lift(
            medium, realized_factor=0.38, cap=max(8.0, gap * 0.42)
        )
        g_strat = _phase_expected_lift(
            strategic, realized_factor=0.35, cap=max(7.0, gap * 0.38)
        )

        if g_quick + g_med + g_strat < 1.5:
            g_quick = min(28.0, gap * 0.30)
            g_med = min(24.0, max(0.0, gap * 0.28))
            g_strat = min(22.0, max(0.0, gap * 0.22))

        s30 = int(round(min(100.0, cur_f + g_quick)))
        s90 = int(round(min(100.0, float(s30) + g_med)))
        s6 = int(round(min(100.0, float(s90) + g_strat)))

        s30 = max(cur, min(100, s30))
        s90 = max(s30, min(100, s90))
        s6 = max(s90, min(100, s6))

        if s30 <= cur:
            s30 = min(100, cur + max(4, int(round(gap * 0.12))))
        if s90 <= s30:
            s90 = min(100, s30 + max(3, int(round(gap * 0.10))))
        if s6 <= s90:
            s6 = min(100, max(s90 + 3, int(round(cur_f + min(float(gap) * 0.82, 88.0)))))
        # Long-term dot should reflect most of the remaining gap if phases execute (design baseline).
        if gap >= 10:
            floor_s6 = int(round(cur_f + gap * 0.58))
            s6 = max(s6, min(100, floor_s6))
        s6 = max(s90, min(100, s6))

        span = s6 - cur
        hi = min(100, s6 + max(0, int(round((100 - s6) * 0.12))))
        if hi > s6 and span >= 8:
            desc = (
                f"<strong>Directional only:</strong> if higher-priority fixes are implemented well, the composite score "
                f"might move from about <strong>{cur}</strong> toward roughly "
                f"<strong>{s30}</strong> (~30 days), <strong>{s90}</strong> (~90 days), and "
                f"<strong>{s6}–{hi}</strong> over 6+ months—actual results depend on what ships, how completely, "
                f"and what a follow-up crawl confirms."
            )
        else:
            desc = (
                f"<strong>Directional only:</strong> the markers below are illustrative, not forecasts. "
                f"Improvement depends on which actions you implement and what the next audit shows; "
                f"rough bands from the current score might trend toward about "
                f"<strong>{s30}</strong> (~30 days), <strong>{s90}</strong> (~90 days), and "
                f"<strong>{s6}</strong> (6+ months) when execution is strong."
            )

        cur_dot = (
            "var(--score-red)"
            if cur < 45
            else ("var(--score-yellow)" if cur < 68 else "var(--score-blue)")
        )
        final_dot = (
            "var(--score-green)"
            if (s6 - cur) >= 10 and s6 >= 62
            else "var(--score-blue)"
        )
        steps = [
            (cur, "Current", "Today", cur_dot),
            (s30, "Quick wins", "~30 days", "var(--score-blue)"),
            (s90, "Medium-term", "~90 days", "var(--score-blue)"),
            (s6, "Strategic", "6+ months", final_dot),
        ]

    if use_llm_desc:
        desc = str(narrative_override).strip()

    parts = [
        '<div class="projection-track" role="img" aria-label="Projected GEO score over time">',
        '<div class="projection-line" aria-hidden="true"></div>',
    ]
    for val, label, sub, bg in steps:
        parts.append(
            '<div class="projection-step">'
            f'<div class="projection-dot" style="background:{bg};">{int(val)}</div>'
            f'<div class="projection-label">{html.escape(label)}</div>'
            f'<div class="projection-sub">{html.escape(sub)}</div>'
            "</div>"
        )
    parts.append("</div>")
    inner = "".join(parts)
    return (
        f'<p class="section-desc">{desc}</p>'
        f'<div class="projection-wrap">{inner}</div>'
    )


def _action_plan_columns_html(
    quick: list[str],
    medium: list[str],
    strategic: list[str],
    *,
    policy_notes: list[str] | None = None,
) -> str:
    def col(
        title: str,
        phase_key: str,
        phase_sub: str,
        items: list[str],
    ) -> str:
        header_cls = {"quick": "quick", "medium": "medium", "strategic": "strategic"}[
            phase_key
        ]
        parts: list[str] = [
            '<div class="action-column">',
            f'<div class="action-column-header {header_cls}">{html.escape(title)}'
            f'<span class="phase-label">{html.escape(phase_sub)}</span></div>',
        ]
        if not items:
            parts.append(
                '<div class="action-item action-item--empty">'
                '<span class="action-num">—</span>'
                '<div><span class="small muted">No items in this phase.</span></div>'
                "</div>"
            )
        else:
            for i, raw in enumerate(items, 1):
                line = client_friendly_text(_plain_rollup_line(raw) if ": " in raw else raw)
                lift = _geo_readiness_lift_hint(line)
                parts.append(
                    '<div class="action-item">'
                    f'<span class="action-num">{i}</span>'
                    "<div>"
                    f"{html.escape(line)}"
                    f'<br><span class="action-impact">{html.escape(lift)}</span>'
                    "</div>"
                    "</div>"
                )
        parts.append("</div>")
        return "".join(parts)

    policy_notes = policy_notes or []
    policy_block = ""
    if policy_notes:
        lis = "".join(
            f"<li>{html.escape(client_friendly_text(p))}</li>" for p in policy_notes
        )
        policy_block = (
            '<div class="action-policy-notes" role="region" aria-label="Policy notes">'
            '<div class="action-policy-notes__title">Policy notes</div>'
            f"<p class=\"table-note\">Separate decisions about training or dataset crawlers—not the same as AI search visibility fixes.</p>"
            f"<ul class=\"action-policy-list\">{lis}</ul>"
            "</div>"
        )

    return (
        '<div class="action-columns">'
        + col("Quick wins", "quick", "0–30 days · highest return", quick)
        + col("Medium-term", "medium", "30–90 days · high impact", medium)
        + col(
            "Strategic",
            "strategic",
            "90+ days · content, brand authority & citability programmes",
            strategic,
        )
        + "</div>"
        + policy_block
    )


def _snippet_file(path: Path | None, max_chars: int = 4000) -> str | None:
    if not path or not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(raw) > max_chars:
        raw = raw[: max_chars - 20] + "\n… [truncated]"
    return raw


def _robots_text_for_ai(audit: dict[str, Any]) -> str | None:
    rt = audit.get("robots_txt") or {}
    for key in ("fetched_path", "merged_path", "saved_path"):
        p = rt.get(key)
        if p:
            path = Path(p)
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace")
    return None


def _parse_robots_sitemap_urls(robots_text: str) -> list[str]:
    urls: list[str] = []
    for line in robots_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"(?i)^sitemap:\s*(.+)$", line)
        if m:
            urls.append(m.group(1).strip())
    return urls


def _robots_user_agent_star_disallows_root(robots_text: str) -> bool:
    """True if a User-agent: * group contains Disallow: / (site-wide block for default rules)."""
    current_ua: str | None = None
    for raw in robots_text.splitlines():
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        m = re.match(r"(?i)^User-agent:\s*(.+)$", s)
        if m:
            current_ua = m.group(1).strip()
            continue
        if current_ua == "*" and re.match(r"(?i)^Disallow:\s*/\s*$", s):
            return True
    return False


def _split_x_robots_clause(val: str) -> list[tuple[str | None, str]]:
    """Split X-Robots-Tag-style comma lists; (bot lower or None for global, remainder string)."""
    out: list[tuple[str | None, str]] = []
    for part in val.split(","):
        p = part.strip()
        if not p:
            continue
        if ":" in p:
            a, b = p.split(":", 1)
            al = a.strip().lower()
            br = b.strip()
            out.append((al, br))
        else:
            out.append((None, p))
    return out


def _directive_list_contains(token: str, haystack: str) -> bool:
    """token is a whole directive (e.g. noindex, noai) in comma/space-separated lists."""
    tl = token.lower()
    for chunk in re.split(r"[\s,]+", haystack.lower()):
        if chunk == tl:
            return True
    return False


def _x_robots_has_global(val: str | None, directive: str) -> bool:
    if not val:
        return False
    for bot, rest in _split_x_robots_clause(val):
        if bot is None and _directive_list_contains(directive, rest):
            return True
    return False


def _x_robots_has_for_bot(val: str | None, bot_token: str, directive: str) -> bool:
    if not val:
        return False
    bl = bot_token.lower()
    for bot, rest in _split_x_robots_clause(val):
        if bot == bl and _directive_list_contains(directive, rest):
            return True
    return False


def _meta_content_has_directive(content: str | None, directive: str) -> bool:
    if not content:
        return False
    return _directive_list_contains(directive, content.replace(",", " "))


def _page_blocks_bot_on_signals(page: dict[str, Any], bot_token: str) -> bool:
    xrt = page.get("x_robots_tag")
    if isinstance(xrt, str) and xrt.strip():
        if _x_robots_has_global(xrt, "noindex"):
            return True
        if _x_robots_has_for_bot(xrt, bot_token, "noindex"):
            return True
    gen = page.get("meta_robots_generic")
    if isinstance(gen, str) and _meta_content_has_directive(gen, "noindex"):
        return True
    named = page.get("meta_robots_named")
    if isinstance(named, dict):
        raw = named.get(bot_token.lower())
        if isinstance(raw, str) and _meta_content_has_directive(raw, "noindex"):
            return True
    return False


def _page_has_noai_signal(page: dict[str, Any]) -> bool:
    xrt = page.get("x_robots_tag")
    if isinstance(xrt, str) and _x_robots_has_global(xrt, "noai"):
        return True
    gen = page.get("meta_robots_generic")
    if isinstance(gen, str) and _meta_content_has_directive(gen, "noai"):
        return True
    named = page.get("meta_robots_named")
    if isinstance(named, dict):
        for v in named.values():
            if isinstance(v, str) and _meta_content_has_directive(v, "noai"):
                return True
    return False


def _key_signal_pages(audit: dict[str, Any], homepage_url: str, max_n: int = 10) -> list[dict[str, Any]]:
    pages = [p for p in (audit.get("pages") or []) if p.get("http_status") == 200]
    if not pages:
        return []
    hn = homepage_url.rstrip("/")

    def sort_key(p: dict[str, Any]) -> tuple[int, str]:
        u = (p.get("url") or "").rstrip("/")
        return (0 if u == hn else 1, u)

    pages = sorted(pages, key=sort_key)
    return pages[:max_n]


def _primary_hero_page(pages: list[dict[str, Any]], homepage_url: str) -> dict[str, Any] | None:
    if not pages:
        return None
    hn = homepage_url.rstrip("/")
    for p in pages:
        if (p.get("url") or "").rstrip("/") == hn:
            return p
    return pages[0]


def _fetch_http_status(url: str, timeout: float = 22.0) -> int | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SEOGECrawlBot/1.0 (+https://example.local; audit)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200))
    except urllib.error.HTTPError as e:
        return int(e.code)
    except Exception:
        return None


def _sitemap_access_points(
    robots_text: str | None, homepage_url: str, rp: RobotFileParser | None
) -> tuple[float, list[str]]:
    """
    Up to 3 points (skills/ai-crawler-report.md discovery rubric): Sitemap: in robots returns HTTP 200
    and is allowed for a Tier-1 check agent per rp.
    """
    notes: list[str] = []
    if not robots_text or not robots_text.strip():
        notes.append("No robots.txt—cannot score sitemap discovery from Sitemap: directives.")
        return 0.0, notes
    urls = _parse_robots_sitemap_urls(robots_text)
    if not urls:
        notes.append("No Sitemap: lines in robots.txt—add a sitemap URL for discovery.")
        return 0.0, notes
    home_p = urllib.parse.urlparse(homepage_url)
    for sm in urls[:5]:
        status = _fetch_http_status(sm)
        if status != 200:
            notes.append(f"Sitemap not HTTP 200 ({status}): {sm[:120]}")
            continue
        smp = urllib.parse.urlparse(sm)
        same_host = smp.netloc.lower() == home_p.netloc.lower()
        if same_host:
            if rp is not None:
                try:
                    allowed = bool(rp.can_fetch("GPTBot", sm))
                except Exception:
                    allowed = False
            else:
                allowed = False
        else:
            allowed = True
        if allowed:
            notes.append(f"Sitemap reachable and allowed for Tier-1 check: {sm[:120]}")
            return 3.0, notes
        notes.append(f"Sitemap returned 200 but blocked for GPTBot in robots: {sm[:120]}")
    return 0.0, notes


def _ai_mimic_results_by_token(audit: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    root = audit.get("ai_bot_mimic")
    if not isinstance(root, dict):
        return out
    bots = root.get("bots")
    if not isinstance(bots, list):
        return out
    for row in bots:
        if not isinstance(row, dict):
            continue
        token = str(row.get("robots_token") or "").strip()
        if not token:
            continue
        summary = row.get("summary")
        if isinstance(summary, dict):
            out[token] = summary
    return out


def _regional_access_summary(audit: dict[str, Any]) -> dict[str, Any]:
    rows = audit.get("regional_crawls")
    if not isinstance(rows, list) or not rows:
        return {
            "regions": 0,
            "with_data": 0,
            "avg_access": 0.0,
            "modifier": 0.0,
        }
    access_scores: list[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        http_ratio = float(row.get("pages_http_200_ratio") or 0.0)
        mimic = row.get("mimic_fetch") or {}
        mimic_ratios: list[float] = []
        if isinstance(mimic, dict):
            for token in ("chatgpt", "gemini"):
                sm = mimic.get(token)
                if not isinstance(sm, dict):
                    continue
                attempted = int(sm.get("crawl_pages_fetched") or sm.get("attempted_fetches") or 0)
                if attempted <= 0:
                    continue
                ratio = float(sm.get("crawl_http_200_ratio") or sm.get("http_200_ratio") or 0.0)
                mimic_ratios.append(ratio)
        if mimic_ratios:
            access = 0.6 * (sum(mimic_ratios) / len(mimic_ratios)) + 0.4 * http_ratio
        else:
            access = http_ratio
        access_scores.append(_clamp01(access))
    if not access_scores:
        return {
            "regions": len(rows),
            "with_data": 0,
            "avg_access": 0.0,
            "modifier": 0.0,
        }
    avg_access = sum(access_scores) / len(access_scores)
    # Apply a bounded technical modifier: -5 to +5 around midpoint 0.5.
    modifier = max(-5.0, min(5.0, (avg_access - 0.5) * 10.0))
    return {
        "regions": len(rows),
        "with_data": len(access_scores),
        "avg_access": round(avg_access, 3),
        "modifier": round(modifier, 2),
    }


def score_ai_crawler_robots(
    robots_text: str | None,
    *,
    homepage_url: str = "https://example.org/",
    audit: dict[str, Any] | None = None,
) -> tuple[float, list[str], list[str], dict[str, Any]]:
    """
    0–100 composite (skills/ai-crawler-report.md): Tier 1 AI retrieval 45, Googlebot+Bingbot 20,
    selected Tier-2 ecosystem 15, no blanket AI/search blocks 10, discovery (llms.txt + sitemap) 10.
    Fourth return is a breakdown dict for UI.
    """
    audit = audit or {}
    home, robots_u = _robots_home_and_fetch_url(homepage_url)
    hero_pages = _key_signal_pages(audit, home, max_n=10)
    hero = _primary_hero_page(hero_pages, home)

    tier1_specs = [s for s in AI_CRAWLER_SPECS if s.token in CRAWLER_TIER1_TOKENS]
    foundational_specs = [s for s in AI_CRAWLER_SPECS if s.token in CRAWLER_FOUNDATIONAL_TOKENS]
    eco_specs = [s for s in AI_CRAWLER_SPECS if s.token in CRAWLER_TIER2_ECO_TOKENS]

    rp: RobotFileParser | None = None
    parse_err: str | None = None
    if robots_text and robots_text.strip():
        rp = RobotFileParser()
        rp.set_url(robots_u)
        try:
            rp.parse(robots_text.splitlines())
        except Exception:
            rp = None
            parse_err = "robots.txt failed to parse; fix syntax and re-run."

    def tier_allowed(specs: list[AICrawlerSpec]) -> tuple[int, list[str]]:
        allowed_n = 0
        blocked: list[str] = []
        for s in specs:
            if rp is None:
                blocked.append(s.token)
                continue
            try:
                rc = bool(rp.can_fetch(s.token, home))
            except Exception:
                rc = False
            if not rc:
                blocked.append(s.token)
                continue
            if hero and _page_blocks_bot_on_signals(hero, s.token):
                blocked.append(s.token)
                continue
            allowed_n += 1
        return allowed_n, blocked

    t1_ok, t1_bad = tier_allowed(tier1_specs)
    f_ok, f_bad = tier_allowed(foundational_specs)
    eco_ok, eco_bad = tier_allowed(eco_specs)
    n1 = len(tier1_specs)
    n_f = len(foundational_specs)
    n_eco = len(eco_specs)

    tier1_pts = (t1_ok / n1 * 45.0) if n1 else 0.0
    foundational_pts = (f_ok / n_f * 20.0) if n_f else 0.0
    eco_pts = (eco_ok / n_eco * 15.0) if n_eco else 0.0

    blanket_pts = 0.0
    blanket_notes: list[str] = []
    star_blocks = bool(robots_text and _robots_user_agent_star_disallows_root(robots_text))
    if star_blocks:
        blanket_notes.append("`User-agent: *` includes `Disallow: /`—blanket crawl block.")
    noai_hit = any(_page_has_noai_signal(p) for p in hero_pages)
    if noai_hit:
        blanket_notes.append("`noai` present in sampled pages (meta or X-Robots-Tag).")
    if not star_blocks and not noai_hit:
        blanket_pts = 10.0

    files_pts = 0.0
    file_notes: list[str] = []
    llms_live = bool(((audit.get("llms_txt") or {}).get("exists")))
    llms_pts = 4.0 if llms_live else 0.0
    files_pts += llms_pts
    if llms_live:
        file_notes.append("Live llms.txt present (+4).")
    else:
        file_notes.append("No live llms.txt at origin (+0 of 4).")

    sm_pts, sm_notes = _sitemap_access_points(robots_text, home, rp)
    files_pts += sm_pts
    file_notes.extend(sm_notes)

    key_page_pts = 0.0
    sm_scan_n = int(audit.get("sitemap_pages_scanned") or 0)
    if sm_scan_n > 0:
        key_page_pts = 2.0
        file_notes.append(f"Sitemap drove this crawl ({sm_scan_n} pages scanned) — key URLs discoverable (+2).")

    files_pts += key_page_pts
    files_pts = min(10.0, files_pts)

    regional = _regional_access_summary(audit)
    regional_mod = float(regional.get("modifier") or 0.0)

    score = tier1_pts + foundational_pts + eco_pts + blanket_pts + files_pts
    score += regional_mod
    score = max(0.0, min(100.0, round(score, 1)))

    major_ok = t1_ok + f_ok + eco_ok
    major_total = n1 + n_f + n_eco
    mimic = _ai_mimic_results_by_token(audit)

    breakdown: dict[str, Any] = {
        "total": score,
        "tier1_points": round(tier1_pts, 2),
        "foundational_points": round(foundational_pts, 2),
        "tier2_eco_points": round(eco_pts, 2),
        "tier2_points": round(eco_pts, 2),  # legacy key for older readers
        "blanket_points": round(blanket_pts, 2),
        "files_points": round(files_pts, 2),
        "tier1_allowed": f"{t1_ok}/{n1}",
        "foundational_allowed": f"{f_ok}/{n_f}",
        "tier2_eco_allowed": f"{eco_ok}/{n_eco}",
        "tier2_allowed": f"{eco_ok}/{n_eco}",  # legacy: was “tier 2” eco set
        "blanket_ok": blanket_pts >= 10.0,
        "llms_ok": llms_live,
        "llms_points": round(llms_pts, 2),
        "sitemap_points": round(sm_pts, 2),
        "discovery_key_pages_points": round(key_page_pts, 2),
        "major_allowed": major_ok,
        "major_total": major_total,
        "regional_access": regional,
        "mimic_fetch": {
            "chatgpt": mimic.get("ChatGPT-User"),
            "gemini": mimic.get("GoogleOther"),
        },
    }

    strengths: list[str] = []
    improvements: list[str] = []

    if parse_err:
        improvements.append(parse_err)
    if not robots_text or not robots_text.strip():
        improvements.append(
            "No robots.txt text available—cannot verify crawler rules; crawler subscores are 0."
        )

    if tier1_pts >= 44.0 and n1:
        strengths.append(f"Tier 1 AI search/retrieval crawlers effectively allowed on hero URL ({t1_ok}/{n1}).")
    elif t1_bad:
        improvements.append(
            "Tier 1 gaps (robots or hero noindex): "
            + ", ".join(t1_bad[:8])
            + ("…" if len(t1_bad) > 8 else "")
        )

    if foundational_pts >= 19.0 and n_f:
        strengths.append(f"Googlebot and Bingbot effectively allowed ({f_ok}/{n_f}).")
    elif f_bad:
        improvements.append(
            "Foundational search crawler gaps: " + ", ".join(f_bad[:8]) + ("…" if len(f_bad) > 8 else "")
        )

    if eco_pts >= 14.0 and n_eco:
        strengths.append(f"Tier 2 ecosystem crawlers effectively allowed ({eco_ok}/{n_eco}).")
    elif eco_bad:
        improvements.append(
            "Tier 2 ecosystem gaps: " + ", ".join(eco_bad[:8]) + ("…" if len(eco_bad) > 8 else "")
        )

    if blanket_pts >= 10.0:
        strengths.append("No blanket `*` site block and no sampled `noai` signals (+10).")
    elif blanket_notes:
        improvements.extend(blanket_notes)

    if files_pts >= 8.0:
        strengths.append("Strong discovery signals (llms.txt, sitemap reachability, and/or crawl via sitemap).")
    elif files_pts < 5.0:
        improvements.append(
            "Improve discovery: live llms.txt, reachable Sitemap: in robots.txt, and key URLs in sitemap (+10 max)."
        )

    for token, label in (("ChatGPT-User", "ChatGPT"), ("GoogleOther", "Gemini")):
        info = mimic.get(token) or {}
        attempted = int(info.get("crawl_pages_fetched") or info.get("attempted_fetches") or 0)
        ok = int(info.get("crawl_http_200") or info.get("http_200") or 0)
        blocked = int(info.get("crawl_blocked_in_robots") or 0) + int(info.get("blocked_in_robots") or 0)
        ratio = float(info.get("crawl_http_200_ratio") or info.get("http_200_ratio") or 0.0)
        if attempted <= 0:
            if blocked > 0:
                improvements.append(
                    f"{label} simulated crawl was blocked by robots on sampled URLs; verify intended {token} policy."
                )
            continue
        if ratio >= 0.85 and ok >= 2:
            strengths.append(f"{label} simulated crawl mostly returned HTTP 200 ({ok}/{attempted}).")
        elif ratio < 0.6:
            improvements.append(
                f"{label} simulated crawl was weak ({ok}/{attempted} HTTP 200). Review redirects, bot handling, and bot-targeted headers."
            )

    if int(regional.get("regions") or 0) > 0:
        avg = float(regional.get("avg_access") or 0.0)
        used = int(regional.get("with_data") or 0)
        if avg >= 0.75:
            strengths.append(
                f"Regional access looks strong across additional markets ({used}/{int(regional.get('regions') or 0)} regions with data; avg access {avg:.2f})."
            )
        elif avg < 0.5:
            improvements.append(
                f"Regional crawl access is weak in additional markets (avg access {avg:.2f}); check geo-dependent bot controls, redirects, and edge policies."
            )

    # GEO policy nudges (Bytespider, etc.) — informational vs numeric score
    if rp is not None:
        try:
            bs_ok = not bool(rp.can_fetch("Bytespider", home))
        except Exception:
            bs_ok = False
        if not bs_ok:
            improvements.append(
                "Consider blocking Bytespider (Tier 3) unless you intentionally allow ByteDance training crawls."
            )

    if not strengths and not improvements:
        improvements.append("Review robots.txt, hero meta/X-Robots-Tag, llms.txt, and sitemap discovery.")

    return score, strengths, improvements, breakdown


def _ai_crawler_access_table_html(robots_text: str | None, audit: dict[str, Any]) -> str:
    """Full crawler matrix for report UI: tier, GEO stance, live fetch, aligned yes/no."""
    if not robots_text or not robots_text.strip():
        return "<p class='table-note'>No robots.txt text in this audit—cannot evaluate crawlers.</p>"
    home, robots_u = _robots_home_and_fetch_url(_homepage_from_audit(audit))
    rp = RobotFileParser()
    rp.set_url(robots_u)
    try:
        rp.parse(robots_text.splitlines())
    except Exception:
        return "<p class='table-note'>robots.txt could not be parsed for crawler checks.</p>"
    mimic = _ai_mimic_results_by_token(audit)
    rows: list[str] = []
    rec_class = {"ALLOW": "rec-allow", "BLOCK": "rec-block", "Context": "rec-context"}
    for s in AI_CRAWLER_SPECS:
        try:
            cf = rp.can_fetch(s.token, home)
        except Exception:
            cf = False
        live = "Can fetch" if cf else "Cannot fetch"
        live_cls = "crawler-live-yes" if cf else "crawler-live-no"
        rc = rec_class.get(s.rec_label, "rec-context")
        mimic_cell = "—"
        if s.token in mimic:
            mm = mimic[s.token]
            attempted = int(mm.get("crawl_pages_fetched") or mm.get("attempted_fetches") or 0)
            ok = int(mm.get("crawl_http_200") or mm.get("http_200") or 0)
            blocked = int(mm.get("crawl_blocked_in_robots") or 0) + int(mm.get("blocked_in_robots") or 0)
            if attempted > 0:
                mode = "crawl" if int(mm.get("crawl_pages_fetched") or 0) > 0 else "probe"
                mimic_cell = f"{mode}: {ok}/{attempted} HTTP 200"
            elif blocked > 0:
                mimic_cell = "Blocked by robots"
            else:
                mimic_cell = "No fetch data"
        if s.policy == "context":
            ok_cell = '<span class="crawler-match-na">—</span>'
        else:
            good = (s.policy == "allow" and cf) or (s.policy == "block" and not cf)
            ok_cell = (
                '<span class="crawler-match-yes">Yes</span>'
                if good
                else '<span class="crawler-match-no">No</span>'
            )
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(s.token)}</code></td>"
            f"<td>{s.tier}</td>"
            f"<td><span class=\"crawler-rec {rc}\">{html.escape(s.rec_label)}</span></td>"
            f"<td>{html.escape(s.reason)}</td>"
            f"<td class=\"{live_cls}\">{html.escape(live)}</td>"
            f"<td>{html.escape(mimic_cell)}</td>"
            f"<td>{ok_cell}</td>"
            "</tr>"
        )
    thead = (
        "<thead><tr>"
        "<th>Crawler</th><th>Tier</th><th>GEO recommendation</th><th>Reason</th>"
        "<th>Your robots (homepage)</th><th>Mimic fetch (sample)</th><th>Aligned</th>"
        "</tr></thead>"
    )
    return (
        '<div class="crawler-table-wrap">'
        "<p class='table-note'>Mimic results now include a small simulated crawl for ChatGPT and Gemini-like user agents (robots-aware, link-following), with HTTP outcomes summarized here.</p>"
        '<table class="data-table crawler-access-table" aria-label="AI crawler robots alignment">'
        f"{thead}<tbody>{''.join(rows)}</tbody></table>"
        "</div>"
    )


def _subscore_crawl_infra(audit: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    pages = audit.get("pages") or []
    n = len(pages)
    ok = sum(1 for p in pages if p.get("http_status") == 200)
    rt = audit.get("robots_txt") or {}
    robots_ok = bool(rt.get("exists"))
    merged_ok = bool(rt.get("merged_path"))
    http_ratio = _safe_ratio(ok, n) if n else 0.0
    s = 0.0
    s += 45.0 if robots_ok else 0.0
    s += 35.0 * http_ratio
    if robots_ok and http_ratio >= 0.85:
        s += 20.0
    s = min(100.0, s)
    st: list[str] = []
    im: list[str] = []
    if robots_ok:
        st.append("robots.txt fetched successfully.")
    else:
        im.append("robots.txt missing or failed fetch.")
    if http_ratio >= 0.9 and n:
        st.append(f"Most sampled pages return HTTP 200 ({ok}/{n}).")
    elif n:
        im.append(f"Some sampled pages non-200 ({ok}/{n} with 200).")
    if merged_ok:
        im.append(
            "Merged robots.txt suggestion is in this audit folder—review it and apply appropriate "
            "rules to the live site’s robots.txt (the suggestion itself is not a live-site attribute)."
        )
    return s, st, im


def _llms_txt_audit_body(audit: dict[str, Any]) -> tuple[str, str]:
    """Return (text, source) with source in fetched|generated|''."""
    lt = audit.get("llms_txt") or {}
    for label, key in (("fetched", "fetched_path"), ("generated", "generated_path")):
        raw = lt.get(key)
        if not raw:
            continue
        p = Path(str(raw))
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8", errors="replace"), label
            except OSError:
                continue
    return "", ""


def _llms_txt_looks_like_html_response(text: str) -> bool:
    s = text.lstrip()[:800].lower()
    return "<!doctype html" in s or (s.startswith("<html") and "<body" in s)


def _subscore_llm(audit: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    """
    0–100 aligned with skills/llms-txt.md optional model (six themes): discoverability 20,
    proposal structure 20, link quality 20, curation 25, maintenance 10, policy alignment 5.
    Uses live flag + verbatim/generated file text when paths resolve; apply manual validation
    for policy, link health, and crawl/noindex checks.
    """
    lt = audit.get("llms_txt") or {}
    live = bool(lt.get("exists"))
    gen_path = lt.get("generated_path")
    gen_flag = bool(gen_path)
    gen_readable = gen_flag and Path(str(gen_path)).is_file()
    body, body_src = _llms_txt_audit_body(audit)
    robots_ok = bool((audit.get("robots_txt") or {}).get("exists"))

    st: list[str] = []
    im: list[str] = []

    if live:
        st.append("Published llms.txt is reachable on the origin.")
    elif gen_flag:
        im.append(
            "No live llms.txt at origin—a draft was written to this audit output; review it and publish "
            "(or merge) a concise guide on the live site for AI systems."
        )
    else:
        im.append(
            "No live llms.txt and no draft in this audit output—publish at origin or re-run the crawl "
            "to generate a draft."
        )

    # Theme 1: discoverability & fetchability
    if live:
        t_disc = 100.0
    elif gen_flag:
        t_disc = 32.0
    else:
        t_disc = 0.0

    n_links = 0
    https_links = 0
    t_struct = 0.0
    t_links = 0.0
    t_cur = 0.0
    t_maint = 25.0
    h2_count = 0
    has_optional = False
    has_h1 = False
    has_bq = False

    if body.strip():
        head = "\n".join(body.splitlines()[:45])
        has_h1 = bool(re.search(r"(?m)^#\s+.+$", head))
        has_bq = bool(re.search(r"(?m)^>\s*\S", body[:12000]))
        h2_count = len(re.findall(r"(?m)^##\s+.+$", body))
        has_optional = bool(re.search(r"(?m)^##\s+optional\s*$", body, re.IGNORECASE))
        links = re.findall(r"\[[^\]]*\]\((https?://[^)\s]+)\)", body)
        n_links = len(links)
        https_links = sum(1 for u in links if u.startswith("https:"))

        t_struct = 0.0
        if has_h1:
            t_struct += 34.0
        if has_bq:
            t_struct += 33.0
        if h2_count >= 1:
            t_struct += 18.0
        if h2_count >= 2:
            t_struct += 10.0
        if n_links:
            t_struct += 5.0
        t_struct = _clamp100(t_struct)

        if n_links == 0:
            t_links = 18.0
        else:
            https_ratio = https_links / float(n_links)
            t_links = _clamp100(
                48.0 * https_ratio
                + 32.0 * min(1.0, n_links / 18.0)
                + 20.0 * min(1.0, n_links / 6.0)
            )

        ann_n = len(re.findall(r"(?m)^-\s*\[[^\]]+\]\([^)]+\):\s*.+", body))
        dump_penalty = 1.0
        if n_links > 85:
            dump_penalty = max(0.25, 1.0 - (n_links - 85) / 180.0)
        cur_core = 0.0
        if h2_count >= 2:
            cur_core += 38.0
        elif h2_count == 1:
            cur_core += 24.0
        if 4 <= n_links <= 50:
            cur_core += 34.0
        elif n_links < 4:
            cur_core += 12.0
        else:
            cur_core += max(14.0, 40.0 - (n_links - 50) * 0.35)
        if has_optional:
            cur_core += 12.0
        cur_core += min(16.0, ann_n * 2.8)
        t_cur = _clamp100(cur_core * dump_penalty)

        if live:
            t_maint = 88.0
        elif gen_readable:
            t_maint = 58.0
        else:
            t_maint = 28.0
        if body_src == "fetched":
            t_maint = min(100.0, t_maint + 4.0)
    else:
        if live:
            st.append(
                "Live llms.txt was fetched during crawl, but the verbatim file is not in this folder—"
                "structure checks skipped."
            )
            t_struct = 62.0
            t_links = 58.0
            t_cur = 55.0
            t_maint = 82.0
        elif gen_flag:
            t_struct = 28.0
            t_links = 22.0
            t_cur = 20.0
            t_maint = 45.0
        else:
            t_struct = 0.0
            t_links = 0.0
            t_cur = 0.0
            t_maint = 20.0

    t_policy = _clamp100(52.0 + (28.0 if robots_ok else 0.0) + (12.0 if live else 0.0))

    score = _clamp100(
        (
            20.0 * t_disc
            + 20.0 * t_struct
            + 20.0 * t_links
            + 25.0 * t_cur
            + 10.0 * t_maint
            + 5.0 * t_policy
        )
        / 100.0
    )

    if not live:
        score = min(score, 40.0)
    if live and body.strip():
        if _llms_txt_looks_like_html_response(body):
            score = min(score, 35.0)
            im.append("llms.txt response looks like HTML—confirm the live file is markdown/text, not an error page.")
        elif n_links == 0 and len(body.strip()) > 40:
            score = min(score, 50.0)
            im.append("Live llms.txt has little or no markdown link inventory—add curated absolute links.")

    if body.strip() and has_h1 and has_bq and h2_count >= 2 and n_links >= 4:
        st.append("llms.txt follows basic proposal shape (H1, blockquote, sections, links).")
    elif body.strip() and live:
        im.append(
            "Tighten llms.txt structure: one H1, blockquote summary, H2 sections, markdown links with https URLs."
        )

    if body.strip() and n_links and https_links < n_links:
        im.append("Prefer https:// URLs in llms.txt link lists for reliable resolution.")

    return score, st, im


def _subscore_structured(audit: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    """
    0–100 composite aligned with skills/json-ld.md (type-aware model, total 100):
    parseability 10, template coverage 15, homepage entity graph 20, page-type schema 25,
    entity linkage / sameAs 15, graph connectivity 10, visible-content proxy 5.
    Uses crawl proxies + summary flags; validate with Schema.org / Rich Results tests.
    """
    _enrich_audit_json_ld_signals(audit)
    pages = audit.get("pages") or []
    ok_pages = [p for p in pages if p.get("http_status") == 200]
    ok = len(ok_pages)
    jld_n = sum(1 for p in ok_pages if p.get("has_json_ld"))
    summary = audit.get("summary") or {}
    any_jld = bool(summary.get("any_json_ld"))
    jld_ratio = _safe_ratio(jld_n, ok) if ok else 0.0
    jld_txt = bool((audit.get("json_ld_txt") or {}).get("path"))
    same_urls = list(summary.get("unique_same_as_urls") or [])
    sa_n = len(same_urls)
    ho = bool(summary.get("json_ld_home_organization"))
    hw = bool(summary.get("json_ld_home_website"))
    hsa = bool(summary.get("json_ld_home_search_action"))
    gshape = bool(summary.get("json_ld_has_graph_structure"))
    http_ctx = bool(summary.get("json_ld_any_http_context"))
    eff = bool(any_jld or ho or hw or hsa)

    blocks_counts = [max(0, int(p.get("json_ld_blocks") or 0)) for p in ok_pages]
    max_blocks = max(blocks_counts) if blocks_counts else 0
    avg_blocks = sum(blocks_counts) / len(blocks_counts) if blocks_counts else 0.0

    jld_og_n = sum(
        1 for p in ok_pages if p.get("has_json_ld") and (p.get("og_image_urls") or [])
    )
    align_ratio = _safe_ratio(jld_og_n, jld_n) if jld_n else 0.0

    # --- Sub-scores 0–100 (weighted below) ---
    if eff:
        t_parse = min(
            100.0,
            40.0
            + 34.0 * jld_ratio
            + 16.0 * min(1.0, max_blocks / 4.0)
            + 10.0 * (1.0 if (any_jld and avg_blocks >= 1.2) else 0.6),
        )
    else:
        t_parse = 0.0

    base = str(audit.get("base_url") or "").strip().rstrip("/")
    priority_hits = 0
    for p in ok_pages:
        if not p.get("has_json_ld"):
            continue
        u = str(p.get("url") or "")
        k = classify_page_type(u)
        if k in ("homepage", "product", "category", "article", "local", "support"):
            priority_hits += 1
    prio_ratio = _safe_ratio(priority_hits, ok) if ok else 0.0
    home_mark = float(int(ho or hw))
    t_cov = _clamp100(100.0 * (0.5 * home_mark + 0.5 * max(jld_ratio, prio_ratio)))

    t_home = min(
        100.0,
        36.0 * float(ho)
        + 34.0 * float(hw)
        + 18.0 * float(hsa)
        + 12.0 * (1.0 if gshape else (0.55 if (max_blocks > 1) else 0.25)),
    )

    avg_p = summary.get("json_ld_avg_product_proxy")
    n_prod_pages = int(summary.get("json_ld_pages_with_product_schema") or 0)
    if isinstance(avg_p, (int, float)) and float(avg_p) > 0:
        t_pt = min(100.0, float(avg_p) * 4.0)
    elif any_jld:
        t_pt = min(100.0, 32.0 + 28.0 * min(1.0, float(n_prod_pages) / 2.0) + 18.0 * jld_ratio)
    else:
        t_pt = min(55.0, 22.0 * float(ho or hw))

    t_el = _clamp100(min(100.0, 12.0 + float(sa_n) * 22.0 + (10.0 if sa_n else 0.0)))
    t_gc = _clamp100(
        100.0
        * (
            0.55 * (1.0 if gshape else min(1.0, max_blocks / 3.0))
            + 0.45 * min(1.0, avg_blocks / 2.5 if any_jld else 0.0)
        )
    )
    t_vis = _clamp100(100.0 * align_ratio) if any_jld else (18.0 if eff else 0.0)

    s = _clamp100(
        (
            10.0 * t_parse
            + 15.0 * t_cov
            + 20.0 * t_home
            + 25.0 * t_pt
            + 15.0 * t_el
            + 10.0 * t_gc
            + 5.0 * t_vis
        )
        / 100.0
    )
    if not eff:
        s = min(s, 40.0)

    st: list[str] = []
    im: list[str] = []
    if any_jld:
        st.append("JSON-LD present on at least one URL (successfully parsed in crawl).")
    elif ho or hw:
        st.append("Homepage JSON-LD export shows Organization/WebSite-style nodes—expand to more templates.")
    else:
        im.append("No JSON-LD in sample—add structured data on key templates.")
    if ho and not hw:
        im.append(
            "Homepage sample shows Organization-style JSON-LD; add WebSite (and SearchAction if you have search) "
            "without removing sameAs."
        )
    elif hw and not ho:
        im.append("Homepage sample shows WebSite JSON-LD; strengthen Organization + sameAs for entity clarity.")
    if ok and jld_ratio > 0.3:
        st.append(f"JSON-LD on multiple sampled pages ({jld_n}/{ok}).")
    elif ok and any_jld:
        im.append("JSON-LD coverage across templates is thin.")
    if http_ctx:
        im.append("@context uses http://schema.org—valid; prefer https://schema.org for modern defaults.")
    if any_jld and sa_n:
        st.append(f"{sa_n} distinct sameAs URL(s) surfaced from JSON-LD.")
    elif any_jld:
        im.append("Add verified Organization sameAs links where profiles are confirmed.")
    if jld_txt:
        im.append(
            "A json-ld.txt export is in this audit folder for review—ensure live pages actually "
            "serve JSON-LD; the export is not itself a live-site signal."
        )
    return s, st, im


def _subscore_og(audit: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    pages = audit.get("pages") or []
    ok = sum(1 for p in pages if p.get("http_status") == 200)
    og_n = sum(1 for p in pages if p.get("og_image_urls"))
    summary = audit.get("summary") or {}
    any_og = bool(summary.get("any_og_image"))
    og_ratio = _safe_ratio(og_n, ok) if ok else 0.0
    s = (40.0 if any_og else 0.0) + 60.0 * og_ratio
    s = min(100.0, s)
    st: list[str] = []
    im: list[str] = []
    if any_og:
        st.append("Open Graph / preview images on at least one URL.")
    else:
        im.append("No og:image in sample—hurts previews and AI surface extracts.")
    if ok and og_ratio >= 0.25:
        st.append(f"Several pages carry OG image meta ({og_n}/{ok}).")
    return s, st, im


def _subscore_entity(audit: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    same_urls = (audit.get("summary") or {}).get("unique_same_as_urls") or []
    sa_n = len(same_urls)
    s = min(100.0, 15.0 + sa_n * 22.0)
    st: list[str] = []
    im: list[str] = []
    if sa_n:
        st.append(f"{sa_n} distinct sameAs URL(s) in JSON-LD.")
    else:
        im.append("No sameAs URLs—strengthen organization linkage in structured data.")
    return s, st, im


def _subscore_meta(audit: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    summary = audit.get("summary") or {}
    any_jld = bool(summary.get("any_json_ld"))
    any_og = bool(summary.get("any_og_image"))
    ns = (audit.get("json_ld_txt") or {}).get("name_source")
    s = 55.0
    st: list[str] = []
    im: list[str] = []
    if ns == "homepage_title":
        s += 30.0
        st.append("Homepage HTML title is present and usable for basic metadata checks.")
    else:
        s += 15.0
        im.append("Homepage title signal weak—check HTML <title>.")
    if any_jld or any_og:
        s += 15.0
    s = min(100.0, s)
    if not st:
        st.append("Basic homepage metadata evaluated.")
    return s, st, im


def score_brand_visibility(
    audit: dict[str, Any],
    *,
    owned_site_entity_proxy: float | None = None,
) -> tuple[float, list[str], list[str]]:
    """
    0–100 aligned with skills/brand-visbility.md optional model:
    entity clarity on owned site (25), official profiles (25), third-party corroboration (25),
    audience/community (15), consistency/risk (10).
    Automated scan covers four platforms; corroboration/audience/risk use crawl proxies.
    """
    same_urls = list((audit.get("summary") or {}).get("unique_same_as_urls") or [])
    bv = audit.get("brand_visibility") or {}
    platforms = bv.get("platforms") or []
    skipped = bool(bv.get("skipped"))
    st: list[str] = []
    im: list[str] = []

    def _owned_entity_25() -> float:
        if owned_site_entity_proxy is not None:
            return round((_clamp100(float(owned_site_entity_proxy)) / 100.0) * 25.0, 2)
        e, _, _ = _subscore_entity(audit)
        m, _, _ = _subscore_meta(audit)
        s, _, _ = _subscore_structured(audit)
        return round((_clamp100(0.28 * e + 0.30 * m + 0.42 * s) / 100.0) * 25.0, 2)

    if skipped:
        oe = _owned_entity_25()
        score = _clamp100(oe + 28.0)
        return (
            score,
            [],
            [
                "Brand visibility scan was skipped—re-run crawl without --no-brand-scan for platform signals.",
                "After manual verification, add confirmed profile URLs to Organization JSON-LD sameAs.",
            ],
        )

    if not platforms:
        if not same_urls:
            return (
                _clamp100(_owned_entity_25() + 12.0),
                [],
                [
                    "No off-site scan data and no sameAs in crawl—run a full crawl or verify profiles manually.",
                    "Add Organization sameAs entries that match verified external profiles.",
                ],
            )
        hits = [u for u in same_urls if any(h in u.lower() for h in SOCIAL_VISIBILITY_HOSTS)]
        hit_n = len(hits)
        oe = _owned_entity_25()
        has_wp = any("wikipedia.org" in u.lower() for u in same_urls)
        has_yt = any("youtube.com" in u.lower() or "youtu.be" in u.lower() for u in same_urls)
        has_li = any("linkedin.com" in u.lower() for u in same_urls)
        has_rd = any("reddit.com" in u.lower() for u in same_urls)
        official_est = (
            (5.0 if has_wp else 0.0)
            + (5.0 if has_yt else 0.0)
            + (5.0 if has_li else 0.0)
            + (3.0 if has_rd else 0.0)
        )
        extra_same = sum(
            1
            for u in same_urls
            if any(h in u.lower() for h in _BRAND_OTHER_PROFILE_HOSTS)
        )
        official_est += min(4.0, float(extra_same) * 2.0)
        if same_urls and official_est > 0:
            official_est += 3.0
        official_est = min(25.0, official_est)
        scan_hits = int(has_wp) + int(has_yt) + int(has_li) + int(has_rd)
        corro = min(25.0, 7.0 + 3.8 * float(scan_hits) + (4.0 if has_wp else 0.0))
        aud = 0.0
        if has_yt:
            aud += 4.0
        if has_rd:
            aud += 4.0
        if has_li:
            aud += 3.0
        cn = int(has_yt) + int(has_rd) + int(has_li)
        if cn >= 2:
            aud += 2.0
        if cn >= 1:
            aud += 2.0
        aud = min(15.0, aud)
        risk = min(10.0, 5.0 + (3.0 if scan_hits >= 2 else 0.0) + (2.0 if len(same_urls) >= 2 else 0.0))
        score = _clamp100(oe + official_est + corro + aud + risk)
        if hits:
            st.append(f"JSON-LD lists {hit_n} social/knowledge URL(s); off-site table unavailable.")
        im.append(
            "Re-run crawl from repo root (or without --no-brand-scan) for automated platform presence checks."
        )
        im.append("Keep sameAs aligned with verified profile URLs.")
        return score, st, im

    by_name = {str(r.get("platform") or ""): r for r in platforms}
    wp = bool((by_name.get("Wikipedia") or {}).get("present"))
    yt = bool((by_name.get("YouTube") or {}).get("present"))
    rd = bool((by_name.get("Reddit") or {}).get("present"))
    li = bool((by_name.get("LinkedIn") or {}).get("present"))
    hit_n = int(wp) + int(yt) + int(rd) + int(li)

    official = 0.0
    if wp:
        official += 5.0
    if yt:
        official += 5.0
    if li:
        official += 5.0
    if rd:
        official += 3.0
    extra_same = 0
    scan_hosts = ("wikipedia.org", "youtube.com", "youtu.be", "reddit.com", "linkedin.com")
    for u in same_urls:
        ul = u.lower()
        if any(h in ul for h in scan_hosts):
            continue
        if any(h in ul for h in _BRAND_OTHER_PROFILE_HOSTS):
            extra_same += 1
    official += min(4.0, float(extra_same) * 2.0)
    if same_urls and hit_n > 0:
        official += 3.0
    official = min(25.0, official)

    corroboration = min(25.0, 7.0 + 3.8 * float(hit_n) + (4.0 if wp else 0.0))

    audience = 0.0
    if yt:
        audience += 4.0
    if rd:
        audience += 4.0
    if li:
        audience += 3.0
    comm_n = int(yt) + int(rd) + int(li)
    if comm_n >= 2:
        audience += 2.0
    if comm_n >= 1:
        audience += 2.0
    audience = min(15.0, audience)

    consistency = 5.0
    if hit_n >= 3:
        consistency += 3.0
    elif hit_n >= 2:
        consistency += 2.0
    elif hit_n >= 1:
        consistency += 1.0
    if len(same_urls) >= 2:
        consistency += 2.0
    consistency = min(10.0, consistency)

    entity_25 = _owned_entity_25()
    score = _clamp100(entity_25 + official + corroboration + audience + consistency)
    bq = str(bv.get("brand_query") or "Brand")

    if hit_n:
        st.append(
            f"Automated scan found {hit_n} likely presence signal(s) for “{bq}” across Wikipedia/YouTube/Reddit/LinkedIn."
        )
    else:
        im.append(
            f"Off-site scan found no confident matches for “{bq}”—verify manually."
        )

    im.append(
        "Add or update Organization JSON-LD sameAs so on-site structured data matches verified profile URLs."
    )
    if same_urls:
        st.append(f"{len(same_urls)} sameAs URL(s) in crawl—dedupe and align after manual verification.")
    else:
        im.append("No sameAs URLs in crawl yet—publish them once profiles are confirmed.")

    if hit_n < 4:
        im.append(
            "Strengthen authoritative footprint (Wikipedia when eligible, key socials) where it fits brand strategy."
        )
    im.append(
        "News, reviews, directories, and Google Business Profile corroboration are not fully automated—validate manually."
    )
    return score, st, _unique_preserve(im)


def _page_snippet_restrictive_for_google(p: dict[str, Any]) -> bool:
    """True if page-level robots hints likely restrict snippets in Google results."""
    gen = p.get("meta_robots_generic")
    if isinstance(gen, str) and gen.strip():
        gl = gen.lower()
        if _directive_list_contains("nosnippet", gl.replace(",", " ")):
            return True
        if re.search(r"max-snippet\s*:\s*0\b", gl):
            return True
    xrt = p.get("x_robots_tag")
    if isinstance(xrt, str) and xrt.strip():
        xl = xrt.lower()
        if "nosnippet" in xl:
            return True
        if re.search(r"max-snippet\s*:\s*0\b", xl):
            return True
    return False


def _google_ai_search_snippet_eligibility_proxy(
    audit: dict[str, Any],
) -> tuple[float, list[str], list[str]]:
    pages = [p for p in (audit.get("pages") or []) if p.get("http_status") == 200]
    if not pages:
        return 55.0, [], ["No HTTP 200 pages in sample—cannot evaluate preview/snippet controls."]
    sample = pages[:20]
    n = len(sample)
    bad = sum(1 for p in sample if _page_snippet_restrictive_for_google(p))
    score = _clamp100(100.0 * (1.0 - (bad / n if n else 0.0)))
    st: list[str] = []
    im: list[str] = []
    if bad == 0:
        st.append("Sampled pages show no nosnippet / max-snippet:0 on meta or X-Robots-Tag.")
    elif bad >= n:
        im.append("Snippet restrictions are widespread on sampled pages—review preview and snippet controls.")
    else:
        im.append(f"{bad}/{n} sampled pages have snippet-restrictive robots signals.")
    return score, st, im


def _google_ai_search_visit_quality_proxy(
    audit: dict[str, Any],
) -> tuple[float, list[str], list[str]]:
    od = (audit.get("output_dir") or "").strip()
    if od:
        ga4p = Path(od) / "ga4_traffic.json"
        if ga4p.is_file():
            try:
                data = json.loads(ga4p.read_text(encoding="utf-8", errors="replace"))
                if (
                    data.get("monthly_sessions")
                    or data.get("weekly")
                    or data.get("has_ai_channel")
                    or data.get("monthly_ai_revenue_pct")
                ):
                    return (
                        74.0,
                        [
                            "Found ga4_traffic.json—tie sessions to landing pages and GSC queries.",
                        ],
                        [],
                    )
            except Exception:
                pass
    return (
        54.0,
        [],
        [
            "Visit quality is mostly manual: conversions, engaged sessions, GSC query→page mapping.",
        ],
    )


def _google_ai_search_freshness_proxy(audit: dict[str, Any]) -> tuple[float, list[str], list[str]]:
    jt = audit.get("json_ld_txt") or {}
    path = jt.get("path")
    if isinstance(path, str) and path.strip():
        try:
            txt = Path(path).read_text(encoding="utf-8", errors="replace").lower()
            if "datemodified" in txt or "datepublished" in txt:
                return (
                    70.0,
                    ["JSON-LD export includes date fields—keep live schema dates accurate."],
                    [],
                )
        except OSError:
            pass
    summ = audit.get("summary") or {}
    if summ.get("any_json_ld"):
        return (
            56.0,
            [],
            ["Add or verify dateModified / datePublished on time-sensitive templates."],
        )
    return (
        48.0,
        [],
        ["No schema freshness signals detected in exports—update stale facts and schema dates where relevant."],
    )


def score_google_ai_search_success(
    audit: dict[str, Any],
    *,
    meta: float,
    structured: float,
    og: float,
    ent: float,
    brand: float,
    crawl_i: float,
    http_ratio: float,
    foundational_points: float,
    ssr_score: float,
    perf_score: float,
) -> tuple[float, list[str], list[str]]:
    """
    Weighted 0–100 composite for Google AI Search readiness (nine internal themes).
    Uses crawl proxies where manual evidence (GSC, CrUX, Merchant Center) is unavailable.
    """
    snip, sn_s, sn_i = _google_ai_search_snippet_eligibility_proxy(audit)
    vis, vis_s, vis_i = _google_ai_search_visit_quality_proxy(audit)
    fr, fr_s, fr_i = _google_ai_search_freshness_proxy(audit)

    gbot_proxy = _clamp100((foundational_points / 20.0) * 100.0) if foundational_points >= 0 else 0.0
    crawl_theme = _clamp100(0.52 * crawl_i + 0.28 * (100.0 * http_ratio) + 0.20 * gbot_proxy)
    content_theme = _clamp100(0.50 * meta + 0.30 * structured + 0.20 * ssr_score)
    page_exp_theme = _clamp100(0.52 * perf_score + 0.48 * ssr_score)
    entity_eco_theme = _clamp100(0.58 * ent + 0.42 * brand)

    themes = [
        ("content_quality", "Content quality", 22.0, content_theme,
         "Titles, structured signals and server-rendered content give AI systems usable page context.",
         "Improve page titles, explanatory content and server-rendered page context."),
        ("crawl_index", "Crawl and index eligibility", 18.0, crawl_theme,
         "The sampled site is accessible to search crawlers and key pages return successfully.",
         "Resolve crawler access, indexing or non-200 response gaps on important pages."),
        ("structured_data", "Structured data", 14.0, structured,
         "Structured data helps Google identify page types and entities.",
         "Add or validate relevant Schema.org markup on key templates."),
        ("snippet_eligibility", "Snippet eligibility", 10.0, snip,
         "Pages retain the signals needed to be eligible for search snippets.",
         "Remove snippet restrictions and strengthen concise extractable answers."),
        ("page_experience", "Page experience", 10.0, page_exp_theme,
         "HTTPS, successful responses and raw HTML provide a sound page-experience baseline.",
         "Validate Core Web Vitals and improve slow, unstable or client-rendered templates."),
        ("multimodal", "Multimodal readiness", 7.0, og,
         "Social preview metadata supplies reusable image and page context.",
         "Improve Open Graph image, title and description coverage for important pages."),
        ("entity_ecosystem", "Entity ecosystem", 7.0, entity_eco_theme,
         "Brand and entity signals connect the site with its wider web presence.",
         "Strengthen organisation identity, sameAs links and consistent third-party brand references."),
        ("visit_quality", "Visit quality", 6.0, vis,
         "The crawl found signals that support useful post-click visits.",
         "Improve answer depth, navigation and landing-page usefulness for AI-referred visitors."),
        ("freshness", "Freshness", 6.0, fr,
         "The site exposes useful freshness or update signals.",
         "Add accurate publication and modification dates, and keep priority content current."),
    ]
    score = sum(weight * theme_score for _, _, weight, theme_score, _, _ in themes) / 100.0
    score = _clamp100(score)

    criteria: list[dict[str, Any]] = []
    for key, title, weight, theme_score, positive, improvement in themes:
        criteria.append(
            {
                "key": key,
                "title": title,
                "weight_pct": weight,
                "score": round(_clamp100(theme_score), 1),
                "strengths": [positive] if theme_score >= 60 else [],
                "improvements": [improvement] if theme_score < 80 else [],
            }
        )
    audit["_ai_search_success_criteria"] = criteria

    st = _unique_preserve(sn_s + vis_s + fr_s)
    im = _unique_preserve(sn_i + vis_i + fr_i)
    return score, st, im


def score_audit(
    audit: dict[str, Any], weights: dict[str, float] | None = None
) -> tuple[float, list[AgentCategoryResult]]:
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        for k in DEFAULT_WEIGHTS:
            if k in weights:
                w[k] = float(weights[k])
    ws = sum(w.values())
    if abs(ws - 100.0) > 0.01:
        w = {k: v * 100.0 / ws for k, v in w.items()}

    robots_body = _robots_text_for_ai(audit)
    ai_robots, ar_s, ar_i, ai_crawler_br = score_ai_crawler_robots(
        robots_body, homepage_url=_homepage_from_audit(audit), audit=audit
    )
    audit["_ai_crawler_score_breakdown"] = ai_crawler_br

    og, og_s, og_i = _subscore_og(audit)
    ent, en_s, en_i = _subscore_entity(audit)
    meta, me_s, me_i = _subscore_meta(audit)
    structured, st_st, st_im = _subscore_structured(audit)

    crawl_i, cr_st, cr_im = _subscore_crawl_infra(audit)
    pages = audit.get("pages") or []
    content_agg = _aggregate_crawl_content_signals(pages)
    n_pages = len(pages)
    ok_200 = sum(1 for p in pages if p.get("http_status") == 200)
    http_ratio = _safe_ratio(ok_200, n_pages) if n_pages else 0.0

    tls_mode = ((audit.get("tls") or {}).get("mode") or "").lower()
    if tls_mode in ("certifi", "stdlib_default"):
        tls_score = 90.0
        tls_st = ["TLS verification mode suitable for reliable crawl."]
        tls_im: list[str] = []
    elif tls_mode == "insecure":
        tls_score = 55.0
        tls_st = []
        tls_im = ["TLS verification disabled—insecure crawl mode; use verified HTTPS for production checks."]
    else:
        tls_score = 72.0
        tls_st = []
        tls_im = ["TLS/certificate configuration unclear—confirm HTTPS trust chain."]

    ssr_score, ssr_st, ssr_im = _ssr_html_completeness_proxy(audit)
    perf_score = _clamp100(0.65 * tls_score + 0.35 * (100.0 * http_ratio))

    platform = min(100.0, (og + ent + meta) / 3.0)
    pl_st = _unique_preserve(og_s + en_s + me_s)
    pl_im = _unique_preserve(og_i + en_i + me_i)
    pl_detail = (
        "Preview clarity: titles, Open Graph, and entity hooks that support cross-platform surfaces; "
        "per-surface cards blend more subs below."
    )

    tech_extract = min(100.0, 0.38 * meta + 0.38 * og + 0.24 * structured)
    passage_cit = _passage_citability_proxy_for_audit(pages, content_agg)
    cit_im_extra: list[str] = []
    if not content_agg.get("has_body_signals"):
        passage_cit = min(passage_cit, tech_extract * 0.74)
        cit_im_extra.append(
            "Body-text heuristics were not found on this audit snapshot—re-run crawl for listing vs editorial detection."
        )
    elif (content_agg.get("listing_fraction") or 0) > 0.5 and (content_agg.get("editorial_fraction") or 0) < 0.2:
        cit_im_extra.append(
            "Sample leans toward product listing / category templates—citability is capped unless pages add buying guides, "
            "FAQs, comparisons, or other direct-answer passages."
        )
    if content_agg.get("has_body_signals"):
        ai_cit = _clamp100(0.50 * passage_cit + 0.50 * tech_extract)
    else:
        ai_cit = _clamp100(0.44 * passage_cit + 0.56 * tech_extract)
    cit_st = _unique_preserve(me_s + og_s + st_st)
    cit_im = _unique_preserve(me_i + og_i + st_im + cit_im_extra)
    cit_detail = (
        "Blends technical extractability (titles, previews, structured data) with passage-style heuristics from crawl "
        "body text (editorial vs product-grid listings)—not a substitute for manual passage review."
    )

    owned_brand_entity_proxy = _clamp100(0.28 * ent + 0.30 * meta + 0.42 * structured)
    brand, br_s, br_i = score_brand_visibility(
        audit, owned_site_entity_proxy=owned_brand_entity_proxy
    )
    br_detail = (
        "Five-component score blending on-site entity signals with "
        "four-platform scan and sameAs; full corroboration still needs manual checks."
    )

    foundational_pts = float((ai_crawler_br.get("foundational_points") or 0.0))
    ai_search, ass_x_st, ass_x_im = score_google_ai_search_success(
        audit,
        meta=meta,
        structured=structured,
        og=og,
        ent=ent,
        brand=brand,
        crawl_i=crawl_i,
        http_ratio=http_ratio,
        foundational_points=foundational_pts,
        ssr_score=ssr_score,
        perf_score=perf_score,
    )
    ass_st = _unique_preserve(st_st + og_s + me_s + ass_x_st)
    ass_im = _unique_preserve(st_im + og_i + me_i + ass_x_im)
    ass_detail = (
        "Google AI Search readiness proxy: nine-theme weighting "
        "(content, crawl/index, structured data, snippet eligibility, page experience, multimodal, "
        "entity ecosystem, visit quality, freshness)—automated where possible."
    )

    # AI visibility: new structure (skills/create-report.md)
    entity_clarity = _clamp100(0.55 * ent + 0.45 * structured)
    entity_st = _unique_preserve(en_s + st_st)
    entity_im = _unique_preserve(en_i + st_im)
    entity_detail = "Whether the site clearly links the brand, the website, and official profiles (entity clarity)."

    brand_entity = _clamp100(0.70 * brand + 0.30 * entity_clarity)
    be_st = _unique_preserve(br_s + entity_st)
    be_im = _unique_preserve(br_i + entity_im)
    be_detail = "Third‑party presence plus on-site entity clarity (sameAs and consistent brand facts)."

    qc_passage = _query_coverage_passage_proxy_for_audit(pages, content_agg)
    query_cov = _clamp100(
        0.26 * ai_cit + 0.26 * qc_passage + 0.22 * platform + 0.13 * ai_search + 0.13 * brand_entity
    )
    qc_st = _unique_preserve(cit_st + pl_st)
    qc_im = _unique_preserve(
        cit_im
        + [
            "Add pages that match common AI query shapes (definitions, comparisons, troubleshooting, pricing, selection).",
            "Run citation footprint checks (Perplexity / ChatGPT / AI Overviews / Copilot) for target queries to set a baseline.",
        ]
    )
    qc_detail = (
        "Query coverage blends URL/title signals with passage heuristics—keyword-like paths alone do not earn full credit "
        "without explanatory body content (manual validation still required)."
    )

    audit["_citability_breakdown"] = {
        "technical_extractability": round(tech_extract, 1),
        "content_citability": round(passage_cit, 1),
        "query_coverage_passage_proxy": round(qc_passage, 1),
        "query_coverage_composite": round(query_cov, 1),
        "listing_heavy_sample": bool((content_agg.get("listing_fraction") or 0) > 0.48),
        "citability_confidence": str(content_agg.get("confidence") or "medium"),
    }
    audit["_ai_visibility_meta"] = {
        "citability_confidence": str(content_agg.get("confidence") or "medium"),
        "listing_weighted_sample": bool((content_agg.get("listing_fraction") or 0) > 0.42),
        "grid_without_editorial": bool((content_agg.get("grid_without_editorial") or 0) > 0.38),
    }
    audit["_technical_meta"] = {
        "ssr_listing_caveat": bool(ssr_score >= 76 and (content_agg.get("listing_fraction") or 0) > 0.45),
    }

    subs_ai = [
        AgentSubResult(
            "ai_citability",
            "AI citability",
            "ai-citability.md",
            ai_cit,
            cit_detail,
            cit_st,
            cit_im,
        ),
        AgentSubResult(
            "brand_visibility",
            "Brand visibility",
            "brand-visbility.md",
            brand,
            br_detail,
            br_s,
            br_i,
        ),
        AgentSubResult(
            "platform_readiness",
            "Platform readiness",
            "platform-readiness.md",
            platform,
            pl_detail,
            pl_st,
            pl_im,
        ),
        AgentSubResult(
            "ai_search_success",
            "AI search success",
            "ai-search-success.md",
            ai_search,
            ass_detail,
            ass_st,
            ass_im,
        ),
        AgentSubResult(
            "brand_entity_visibility",
            "Brand/entity visibility",
            "brand-visbility.md",
            brand_entity,
            be_detail,
            be_st,
            be_im,
        ),
        AgentSubResult(
            "query_coverage_footprint",
            "Query coverage & citation footprint",
            "platform-readiness.md",
            query_cov,
            qc_detail,
            qc_st,
            qc_im,
        ),
    ]
    for s in subs_ai:
        s.strengths = _consolidate_strength_lines(s.strengths)
        s.improvements = _consolidate_improvement_lines(s.improvements)
    sc_ai = _weighted_scores(
        [
            (ai_cit, 0.30),
            (platform, 0.25),
            (ai_search, 0.15),
            (brand_entity, 0.15),
            (query_cov, 0.15),
        ]
    )
    st_ai, im_ai = _rollup_subs_to_category(subs_ai)
    det_ai = category_card_description("ai_visibility", sc_ai)

    llm, ll_st, ll_im = _subscore_llm(audit)

    # Technical setup: new structure (skills/create-report.md + technical-audit.md optional model)
    indexability = _clamp100(0.75 * crawl_i + 0.25 * (100.0 * http_ratio))
    ix_st = _unique_preserve(cr_st)
    ix_im = _unique_preserve(cr_im + ["Validate canonical and noindex behavior on key templates (manual spot-check)."])
    ix_detail = "Crawl/index eligibility from robots fetch and HTTP 200 ratio in the sample."

    canonical_dup, cd_st, cd_im = _subscore_canonical_duplicate_control(audit, http_ratio=http_ratio)

    pf_st = _unique_preserve(tls_st + (["Most sampled pages returned normally (good baseline for crawl)."] if http_ratio >= 0.85 and n_pages else []))
    pf_im = _unique_preserve(
        tls_im
        + (["Many sampled pages were non-200—fix errors before performance tuning."] if n_pages and http_ratio < 0.85 else [])
        + ["Core Web Vitals and TTFB are not measured in this automated crawl—validate with a speed tool."]
    )
    pf_detail = (
        "Performance, mobile usability, and HTTPS proxy: TLS trust and HTTP success in sample; "
        "Core Web Vitals, INP/CLS, and real mobile layout require CrUX/Lighthouse."
    )

    # Discovery: llms.txt + sitemap + freshness signals (limited automation)
    _br_disc = audit.get("_ai_crawler_score_breakdown") or {}
    disc_pts = float(_br_disc.get("files_points") or 0.0)
    sm_pts_ds = float(_br_disc.get("sitemap_points") or 0.0)
    sitemap_score = _clamp100((_clamp01(disc_pts / 10.0)) * 100.0)
    discovery = _clamp100(0.55 * llm + 0.45 * sitemap_score)
    ds_st = _unique_preserve(ll_st + (["Sitemap reachable and allowed for Tier-1 check."] if sm_pts_ds >= 3.0 else []))
    ds_im = _unique_preserve(
        ll_im
        + (["Ensure robots.txt declares a live sitemap URL for discovery."] if sm_pts_ds <= 0.0 else [])
        + ["Freshness signals (lastmod, feeds, IndexNow, Last-Modified) are manual checks in this version."]
    )
    ds_detail = (
        "Discovery proxy: 55% llms.txt signals + 45% sitemap reachability from robots; "
        "lastmod, IndexNow, and feeds are manual checks in this pipeline."
    )

    crawl_ix_canon = _clamp100(0.50 * indexability + 0.50 * canonical_dup)
    ixcan_st = _unique_preserve(ix_st + cd_st)
    ixcan_im = _unique_preserve(ix_im + cd_im)
    ixcan_detail = (
        "Indexability, canonicalisation, and crawl health (25% of technical score): "
        "automation covers themes 1–2; full host/redirect/canonical maps need manual verification."
    )

    subs_tech = [
        AgentSubResult(
            "indexability_crawl_health",
            "Indexability, canonicalisation & crawl health",
            "technical-audit.md",
            crawl_ix_canon,
            ixcan_detail,
            ixcan_st,
            ixcan_im,
        ),
        AgentSubResult(
            "ai_crawler_report",
            "AI crawler access / robots",
            "ai-crawler-report.md",
            ai_robots,
            "Composite 0–100: Tier 1 (45) + Googlebot/Bingbot (20) + Tier-2 eco (15) + blanket block check (10) + discovery (10), then regional-access modifier (-5..+5) from extra market crawl outcomes when configured.",
            ar_s,
            ar_i,
        ),
        AgentSubResult(
            "ssr_html_completeness",
            "SSR & raw HTML completeness",
            "technical-audit.md",
            ssr_score,
            "Theme 3 (rendering / raw HTML): whether titles, head signals, and machine-readable tags appear without relying on JS execution.",
            ssr_st,
            ssr_im,
        ),
        AgentSubResult(
            "performance_page_experience",
            "Performance, mobile, HTTPS",
            "technical-audit.md",
            perf_score,
            pf_detail,
            pf_st,
            pf_im,
        ),
        AgentSubResult(
            "discovery_signals",
            "llms.txt, sitemaps, freshness",
            "llms-txt.md",
            discovery,
            ds_detail,
            ds_st,
            ds_im,
        ),
    ]
    for s in subs_tech:
        s.strengths = _consolidate_strength_lines(s.strengths)
        s.improvements = _consolidate_improvement_lines(s.improvements)
    sc_tech = _weighted_scores(
        [
            (crawl_ix_canon, 0.25),
            (ai_robots, 0.25),
            (ssr_score, 0.20),
            (perf_score, 0.15),
            (discovery, 0.15),
        ]
    )
    st_tech, im_tech = _rollup_subs_to_category(subs_tech)
    det_tech = category_card_description("technical_setup", sc_tech)

    http_s, http_st, http_im = _subscore_crawl_infra(audit)
    # Base page-quality proxy (meta + OG + HTTP/crawl reliability); used inside themes without circular weighting.
    base_signal = _clamp100(0.35 * meta + 0.35 * og + 0.30 * http_s)
    t_people_first = _clamp100(0.45 * meta + 0.55 * ai_cit)
    t_content_quality = _clamp100(0.40 * ai_cit + 0.35 * meta + 0.25 * og)
    t_original_info = _clamp100(0.55 * base_signal + 0.45 * ai_search)
    t_exp_expert = _clamp100(0.55 * structured + 0.45 * ent)
    t_trust_sourcing = _clamp100(0.30 * meta + 0.25 * og + 0.25 * http_s + 0.20 * ai_robots)
    t_authority = _clamp100(0.65 * brand_entity + 0.35 * ent)
    llms_live_ct = bool(((audit.get("llms_txt") or {}).get("exists")))
    any_jl_ct = bool((audit.get("summary") or {}).get("any_json_ld"))
    t_governance = _clamp100(
        0.45 * base_signal
        + 0.30 * (88.0 if llms_live_ct else 62.0)
        + 0.25 * (82.0 if any_jl_ct else 52.0)
    )
    eeat_score = _clamp100(
        (
            15.0 * t_people_first
            + 20.0 * t_content_quality
            + 15.0 * t_original_info
            + 15.0 * t_exp_expert
            + 20.0 * t_trust_sourcing
            + 10.0 * t_authority
            + 5.0 * t_governance
        )
        / 100.0
    )
    eeat_st = _unique_preserve(me_s + og_s + http_st)
    http_im_for_eeat = [
        x
        for x in http_im
        if "merged robots" not in (x or "").lower()
    ]
    eeat_im = _unique_preserve(me_i + og_i + http_im_for_eeat)
    eeat_detail = (
        "E-E-A-T proxy (15/20/15/15/20/10/5): people-first, content quality, "
        "original information gain, experience/expertise, trust/sourcing, authoritativeness, governance—crawl-only; validate editorially."
    )

    jl_detail = (
        "JSON-LD proxy (10/15/20/25/15/10/5): parseability, template coverage, homepage entity graph "
        "(Organization vs WebSite scored separately), page-type/Product richness, sameAs / entity linkage, "
        "graph connectivity (@id, publisher, isPartOf), visible-content proxy (OG + JSON-LD co-presence)—"
        "validate with Schema Validator / Rich Results Test."
    )

    # Content quality & structure: new structure (skills/create-report.md)
    orig_gain = t_original_info
    og_st2 = _unique_preserve(eeat_st)
    og_im2 = _unique_preserve(
        eeat_im
        + [
            "Add original information gain (first‑party data, case studies, expert commentary, unique examples).",
        ]
    )
    og_detail2 = "Original information gain beyond what is already in top search results (proxy; validate editorially)."

    passage_ans = _clamp100(0.65 * ai_cit + 0.35 * platform)
    pa_st = _unique_preserve(cit_st + pl_st)
    pa_im = _unique_preserve(
        cit_im
        + [
            "Add passage patterns AI can lift: definitions, direct answers under headings, tables, steps, FAQs, key takeaways.",
        ]
    )
    pa_detail = "Whether pages contain extractable answer passages (definitions, tables, steps, FAQs)."

    schema_entity = _clamp100(0.75 * structured + 0.25 * ent)
    se_st = _unique_preserve(st_st + en_s)
    se_im = _unique_preserve(st_im + en_i)
    se_detail = "JSON‑LD/schema coverage plus entity markup (sameAs and org clarity)."

    governance = _clamp100(0.50 * t_governance + 0.30 * t_trust_sourcing + 0.20 * brand)
    gv_st = _unique_preserve(eeat_st)
    gv_im = _unique_preserve(
        eeat_im
        + [
            "Improve source transparency (named sources, dates, methods) and content governance (review process, corrections, disclosures).",
        ]
    )
    gv_detail = "Source transparency and content governance signals (proxy; validate on-page)."

    subs_content = [
        AgentSubResult(
            "eeat",
            "E-E-A-T & helpful content",
            "eeat.md",
            eeat_score,
            eeat_detail,
            eeat_st,
            eeat_im,
        ),
        AgentSubResult(
            "original_information_gain",
            "Original information gain",
            "eeat.md",
            orig_gain,
            og_detail2,
            og_st2,
            og_im2,
        ),
        AgentSubResult(
            "passage_answerability",
            "Passage-level answerability",
            "ai-citability.md",
            passage_ans,
            pa_detail,
            pa_st,
            pa_im,
        ),
        AgentSubResult(
            "json_ld",
            "JSON-LD / schema / entity markup",
            "json-ld.md",
            structured,
            jl_detail,
            list(st_st),
            list(st_im),
        ),
        AgentSubResult(
            "schema_entity_markup",
            "Schema & entity markup depth",
            "json-ld.md",
            schema_entity,
            se_detail,
            se_st,
            se_im,
        ),
        AgentSubResult(
            "source_transparency_governance",
            "Source transparency & governance",
            "eeat.md",
            governance,
            gv_detail,
            gv_st,
            gv_im,
        ),
    ]
    for s in subs_content:
        s.strengths = _consolidate_strength_lines(s.strengths)
        s.improvements = _consolidate_improvement_lines(s.improvements)
    sc_ct = _weighted_scores(
        [
            (eeat_score, 0.35),
            (orig_gain, 0.20),
            (passage_ans, 0.20),
            (schema_entity, 0.15),
            (governance, 0.10),
        ]
    )
    st_ct, im_ct = _rollup_subs_to_category(subs_content)
    det_ct = category_card_description("content_structure", sc_ct)

    st_ai = _consolidate_strength_lines(st_ai)
    im_ai = _consolidate_improvement_lines(im_ai)
    st_tech = _consolidate_strength_lines(st_tech)
    im_tech = _consolidate_improvement_lines(im_tech)
    st_ct = _consolidate_strength_lines(st_ct)
    im_ct = _consolidate_improvement_lines(im_ct)

    agents: list[AgentCategoryResult] = [
        AgentCategoryResult(
            "ai_visibility",
            "AI visibility",
            w["ai_visibility"],
            sc_ai,
            det_ai,
            subs_ai,
            st_ai,
            im_ai,
            category_card_tagline("ai_visibility"),
        ),
        AgentCategoryResult(
            "technical_setup",
            "Technical setup",
            w["technical_setup"],
            sc_tech,
            det_tech,
            subs_tech,
            st_tech,
            im_tech,
            category_card_tagline("technical_setup"),
        ),
        AgentCategoryResult(
            "content_structure",
            "Content quality & structure",
            w["content_structure"],
            sc_ct,
            det_ct,
            subs_content,
            st_ct,
            im_ct,
            category_card_tagline("content_structure"),
        ),
    ]

    overall = sum(a.score * (a.weight / 100.0) for a in agents)

    # Apply gating rules (caps) for major access/visibility blockers.
    capped, cap_notes = _apply_overall_gating_caps(
        overall=overall,
        audit=audit,
        robots_text=robots_body,
        homepage_url=_homepage_from_audit(audit),
        ai_crawler_breakdown=(audit.get("_ai_crawler_score_breakdown") or {}),
        ssr_score=ssr_score,
        crawl_http_ratio=http_ratio,
    )
    if cap_notes:
        # Surface in Technical setup “needs work” so non-technical readers see the constraint.
        for c in agents:
            if c.key == "technical_setup":
                c.improvements = _unique_preserve(c.improvements + cap_notes)
    overall = capped
    audit["_overall_score_cap_notes"] = cap_notes
    return overall, agents


def _audit_from_comparison_row(row: dict[str, Any]) -> dict[str, Any]:
    """Minimal audit when only comparison.json row exists (no full JSON on disk)."""
    n = int(row.get("pages_scanned") or 0)
    ok_n = int(row.get("pages_http_200") or 0)
    j = int(row.get("pages_with_json_ld") or 0)
    o = int(row.get("pages_with_og_meta") or 0)
    pages: list[dict[str, Any]] = []
    for i in range(max(n, 1)):
        is_ok = i < ok_n
        has_j = i < j and is_ok
        pages.append(
            {
                "http_status": 200 if is_ok else 404,
                "has_json_ld": has_j,
                "json_ld_blocks": 1 if has_j else 0,
                "og_image_urls": ["_"] if (i < o and is_ok) else [],
            }
        )
    sa_count = int(row.get("same_as_count") or 0)
    same_as = [f"https://placeholder.example/profile/{k}" for k in range(sa_count)]
    return {
        "base_url": row.get("base_url"),
        "robots_txt": {
            "exists": row.get("robots_fetched"),
            "merged_path": None,
            "fetched_path": None,
        },
        "llms_txt": {"exists": row.get("llms_live"), "generated_path": None},
        "pages": pages,
        "summary": {
            "any_json_ld": row.get("any_json_ld"),
            "any_og_image": row.get("any_og_image"),
            "unique_same_as_urls": same_as,
        },
        "json_ld_txt": {"path": None, "name_source": "homepage_title"},
    }


def _load_audit_for_comparison_row(row: dict[str, Any]) -> dict[str, Any]:
    od = row.get("output_dir")
    if od:
        p = Path(od)
        summary = p / "audit_summary.json"
        if summary.is_file():
            return _read_json(summary)
    return _audit_from_comparison_row(row)


def _comparison_url_key(value: str) -> str:
    try:
        return normalize_base(value).rstrip("/").lower()
    except ValueError:
        return str(value or "").strip().rstrip("/").lower()


def _comparison_brand_names(comp_path: Path) -> tuple[str, dict[str, str]]:
    """Load user-confirmed primary and competitor labels keyed by website."""
    config_path = comp_path.parent / "onboarding_context.json"
    if not config_path.is_file():
        return "", {}
    try:
        config = _read_json(config_path)
    except (OSError, json.JSONDecodeError):
        return "", {}
    primary_name = str(
        config.get("brand_name_used")
        or config.get("brand_name")
        or config.get("brand")
        or ""
    ).strip()
    by_url: dict[str, str] = {}
    for competitor in config.get("competitors_detail") or []:
        if not isinstance(competitor, dict):
            continue
        name = str(competitor.get("competitor_brand") or "").strip()
        website = str(competitor.get("competitor_website") or "").strip()
        if name and website:
            by_url[_comparison_url_key(website)] = name
    return primary_name, by_url


def _comparison_fallback_name(url: str) -> str:
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = [part for part in host.split(".") if part]
    if (
        len(parts) >= 3
        and len(parts[-1]) == 2
        and parts[-2] in {"co", "com", "net", "org"}
    ):
        label = parts[-3]
    else:
        label = parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")
    label = label.replace("-", " ").strip()
    return label.title() if label else (host or url)


def _comparison_display_name(url: str, configured_name: str) -> str:
    """Prefer configured brand names, correcting labels copied from a subdomain."""
    fallback = _comparison_fallback_name(url)
    configured = configured_name.strip()
    if not configured:
        return fallback
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    first_label = host.split(".")[0]
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    if normalize(configured) == normalize(first_label) and normalize(configured) != normalize(fallback):
        return fallback
    return configured


def _comparison_favicon_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return ""
    return (
        "https://www.google.com/s2/favicons?domain="
        + urllib.parse.quote(host, safe="")
        + "&sz=32"
    )


def _sub_strengths_improvements(sub: AgentSubResult) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    improvements: list[str] = []
    for raw in list(sub.strengths or []):
        line = client_friendly_finding(str(raw), "insight").strip()
        if line:
            strengths.append(line)
    for raw in list(sub.improvements or []):
        line = client_friendly_finding(str(raw), "insight").strip()
        if line:
            improvements.append(line)
    return strengths, improvements


def _sub_finding_summary(sub: AgentSubResult) -> str:
    """Summary-style finding: prefer strength + improvement, else either side."""
    strengths, improvements = _sub_strengths_improvements(sub)
    strength = strengths[0] if strengths else ""
    improvement = improvements[0] if improvements else ""
    if strength and improvement:
        return f"{strength} Needs work: {improvement}."
    if strength:
        return strength if strength.endswith(".") else f"{strength}."
    if improvement:
        return improvement if improvement.endswith(".") else f"{improvement}."
    detail = client_friendly_text(str(sub.detail or "")).strip()
    return detail or "No specific finding was recorded."


def _concise_finding_text(text: str, max_length: int = 160) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip()).rstrip(".;:")
    if len(cleaned) <= max_length:
        return cleaned
    return f"{cleaned[: max_length - 1].rstrip()}…"


# Overview criteria — keep aligned with web/src/lib/technicalSetupAreas.ts
# and web/src/lib/contentQualityAreas.ts (Summary / pillar Overview).
TECHNICAL_SETUP_OVERVIEW_AREAS: list[dict[str, Any]] = [
    {
        "key": "crawler_access",
        "label": "Crawler access",
        "description": (
            "Whether major AI crawlers are permitted to access the site through "
            "robots.txt and related controls."
        ),
        "component_keys": ("ai_crawler_report",),
    },
    {
        "key": "citability",
        "label": "Citability",
        "description": (
            "How successfully the site supports AI extraction and citation across "
            "tested queries and content."
        ),
        "component_keys": (
            "ai_citability",
            "ai_search_success",
            "query_coverage_footprint",
        ),
    },
    {
        "key": "platform_readiness",
        "label": "Platform readiness",
        "description": (
            "Technical and prompt-performance readiness for each supported AI platform."
        ),
        "component_keys": ("platform_readiness",),
    },
]

CONTENT_QUALITY_OVERVIEW_AREAS: list[dict[str, Any]] = [
    {
        "key": "eeat",
        "label": "E-E-A-T Signals",
        "description": (
            "Experience, Expertise, Authoritativeness, and Trustworthiness signals "
            "across key pages."
        ),
        "component_keys": ("eeat",),
    },
    {
        "key": "structure_answerability",
        "label": "Content Structure & Answerability",
        "description": (
            "Whether content contributes genuinely original information and is "
            "structured into clear, self-contained passages AI can extract."
        ),
        "component_keys": (
            "original_information_gain",
            "passage_answerability",
            "content_formatting",
        ),
    },
    {
        "key": "schema_entity_markup",
        "label": "Schema & Entity Markup",
        "description": (
            "JSON-LD structured data coverage and entity clarity."
        ),
        # Prefer schema_entity_markup; json_ld is a crawl fallback alias.
        "component_keys": ("schema_entity_markup", "json_ld"),
    },
    {
        "key": "brand_visibility_authority",
        "label": "Brand Visibility & Authority",
        "description": (
            "How prominently the brand appears across third-party surfaces that AI "
            "models use to verify entities."
        ),
        "component_keys": (
            "brand_visibility_authority",
            "brand_visibility",
            "brand_entity_visibility",
            "source_transparency_governance",
        ),
    },
]


def _area_finding_summary(parts: list[dict[str, Any]]) -> str:
    """Mirror Summary / Overview areaFindingSummary for grouped criteria."""
    if not parts:
        return ""
    if len(parts) == 1:
        return str(parts[0].get("finding_summary") or "").strip()
    ranked = sorted(parts, key=lambda item: float(item.get("score") or 0.0))
    weakest = ranked[0]
    strongest = ranked[-1]
    strength = ""
    strongest_strengths = strongest.get("strengths") or []
    if strongest_strengths:
        strength = str(strongest_strengths[0])
    elif float(strongest.get("score") or 0.0) >= 60:
        strength = str(strongest.get("finding_summary") or "")
    improvement = ""
    weakest_improvements = weakest.get("improvements") or []
    if weakest_improvements:
        improvement = str(weakest_improvements[0])
    elif float(weakest.get("score") or 0.0) < 75:
        improvement = str(weakest.get("finding_summary") or "")
    chunks: list[str] = []
    if strength:
        chunks.append(
            f"{strongest.get('title')}: {_concise_finding_text(strength)}"
        )
    if improvement and (weakest.get("key") != strongest.get("key") or not strength):
        chunks.append(
            f"{weakest.get('title')} needs work: {_concise_finding_text(improvement)}"
        )
    if chunks:
        return f"{'. '.join(chunks)}."
    return str(
        weakest.get("finding_summary")
        or strongest.get("finding_summary")
        or ""
    ).strip()


def _group_overview_components(
    components: list[dict[str, Any]] | None,
    areas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Collapse leaf score components into Summary / Overview criteria.

    Idempotent when components are already overview-keyed.
    """
    raw = [item for item in (components or []) if isinstance(item, dict) and item.get("key")]
    if not raw:
        return []

    by_key = {str(item.get("key") or ""): item for item in raw}
    overview_keys = {str(area["key"]) for area in areas}
    if by_key.keys() & overview_keys and not any(
        key in by_key
        for area in areas
        for key in area["component_keys"]
        if key not in overview_keys
    ):
        # Already overview-shaped: keep stable area order and titles.
        ordered: list[dict[str, Any]] = []
        for area in areas:
            item = by_key.get(str(area["key"]))
            if not item:
                continue
            next_item = dict(item)
            next_item["title"] = str(area["label"])
            if not str(next_item.get("detail") or "").strip():
                next_item["detail"] = str(area.get("description") or "")
            ordered.append(next_item)
        return ordered

    grouped: list[dict[str, Any]] = []
    for area in areas:
        parts: list[dict[str, Any]] = []
        seen: set[str] = set()
        for leaf_key in area["component_keys"]:
            item = by_key.get(leaf_key)
            if not item or leaf_key in seen:
                continue
            # For schema/brand aliases, prefer the first matching key only.
            if area["key"] in {"schema_entity_markup", "brand_visibility_authority"} and parts:
                break
            parts.append(item)
            seen.add(leaf_key)
        if not parts:
            continue
        total_weight = sum(float(part.get("weight_pct") or 0.0) for part in parts)
        if total_weight > 0:
            score = sum(
                float(part.get("score") or 0.0) * float(part.get("weight_pct") or 0.0)
                for part in parts
            ) / total_weight
        else:
            score = sum(float(part.get("score") or 0.0) for part in parts) / len(parts)
        strengths = [
            line
            for part in parts
            for line in (part.get("strengths") or [])
            if str(line).strip()
        ]
        improvements = [
            line
            for part in parts
            for line in (part.get("improvements") or [])
            if str(line).strip()
        ]
        evidence = next(
            (
                str(part.get("evidence_example") or "").strip()
                for part in parts
                if str(part.get("evidence_example") or "").strip()
            ),
            "",
        )
        grouped.append(
            {
                "key": str(area["key"]),
                "title": str(area["label"]),
                "score": round(score, 1),
                "weight_pct": total_weight,
                "detail": str(area.get("description") or ""),
                "finding_summary": _area_finding_summary(parts),
                "evidence_example": evidence,
                "strengths": strengths or None,
                "improvements": improvements or None,
            }
        )
    return grouped


def group_technical_setup_components(
    components: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Collapse Technical Setup leafs into Overview criteria (Summary-aligned)."""
    return _group_overview_components(components, TECHNICAL_SETUP_OVERVIEW_AREAS)


def group_content_quality_components(
    components: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Collapse Content Quality leafs into Overview criteria (Summary-aligned)."""
    return _group_overview_components(components, CONTENT_QUALITY_OVERVIEW_AREAS)


def _component_from_sub(
    sub: AgentSubResult,
    *,
    weight_pct: float,
    title: str | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    strengths, improvements = _sub_strengths_improvements(sub)
    detail = client_friendly_text(str(sub.detail or "")).strip()
    return {
        "key": key or sub.key,
        "title": title or sub.title,
        "score": round(float(sub.score), 1),
        "weight_pct": float(weight_pct),
        "detail": detail,
        "finding_summary": _sub_finding_summary(sub),
        "evidence_example": detail,
        "strengths": strengths or None,
        "improvements": improvements or None,
    }


def _pillar_components_from_subs(
    cat: AgentCategoryResult,
    *,
    weight_keys: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Summary-style criterion rows (key/title/score/weight/finding_summary)."""
    weights = weight_keys if weight_keys is not None else _INSIGHT_DEDUP_WEIGHTS.get(cat.key, {})
    components: list[dict[str, Any]] = []
    for sub in list(cat.subs or []):
        if weights and sub.key not in weights:
            continue
        components.append(
            _component_from_sub(sub, weight_pct=float(weights.get(sub.key, 0.0)))
        )
    return components


def _integrated_technical_leaf_components(
    by_key: dict[str, AgentCategoryResult],
) -> list[dict[str, Any]]:
    """Technical leaf criteria matching Summary integrated scoring."""
    weights = {
        "ai_crawler_report": 25.0,
        "ai_citability": 25.0,
        "ai_search_success": 12.5,
        "query_coverage_footprint": 12.5,
        "platform_readiness": 25.0,
    }
    titles = {
        "ai_crawler_report": "AI crawler access",
        "ai_citability": "AI citability",
        "ai_search_success": "AI search success",
        "query_coverage_footprint": "Query coverage & citation footprint",
        "platform_readiness": "Platform readiness",
    }
    ai = by_key.get("ai_visibility")
    tech = by_key.get("technical_setup")
    sub_by_key: dict[str, AgentSubResult] = {}
    if ai:
        for sub in ai.subs:
            sub_by_key[sub.key] = sub
    if tech:
        for sub in tech.subs:
            sub_by_key[sub.key] = sub
    components: list[dict[str, Any]] = []
    for key, weight in weights.items():
        sub = sub_by_key.get(key)
        if not sub:
            continue
        components.append(
            _component_from_sub(
                sub,
                weight_pct=weight,
                title=titles.get(key, sub.title),
            )
        )
    return components


def _integrated_technical_components(
    by_key: dict[str, AgentCategoryResult],
) -> list[dict[str, Any]]:
    """Technical Overview criteria matching Summary (grouped)."""
    return group_technical_setup_components(_integrated_technical_leaf_components(by_key))


def _content_quality_components_for_comparison(
    by_key: dict[str, AgentCategoryResult],
) -> list[dict[str, Any]]:
    """Content Overview criteria for competitor comparison (Summary-aligned)."""
    content = by_key.get("content_structure")
    if not content:
        return []
    # Leaf weights mirror Summary content_structure components where present.
    weight_keys = {
        "eeat": 35.0,
        "original_information_gain": 15.0,
        "passage_answerability": 15.0,
        "content_formatting": 10.0,
        "schema_entity_markup": 15.0,
    }
    leafs = _pillar_components_from_subs(content, weight_keys=weight_keys)
    by_leaf = {str(item.get("key") or ""): item for item in leafs}
    # Crawl may expose schema under json_ld only.
    if "schema_entity_markup" not in by_leaf:
        for sub in list(content.subs or []):
            if sub.key == "json_ld":
                leafs.append(
                    _component_from_sub(
                        sub,
                        weight_pct=15.0,
                        key="schema_entity_markup",
                        title="Schema & entity markup",
                    )
                )
                break
    # Brand Visibility & Authority comes from integrated content scores; crawl
    # comparisons map AI brand-visibility signals into that Overview criterion.
    if "brand_visibility_authority" not in by_leaf:
        ai = by_key.get("ai_visibility")
        brand_sub = None
        if ai:
            sub_map = {sub.key: sub for sub in ai.subs}
            brand_sub = sub_map.get("brand_visibility") or sub_map.get("brand_entity_visibility")
        if brand_sub:
            leafs.append(
                _component_from_sub(
                    brand_sub,
                    weight_pct=10.0,
                    key="brand_visibility_authority",
                    title="Brand Visibility & Authority",
                )
            )
        else:
            for sub in list(content.subs or []):
                if sub.key == "source_transparency_governance":
                    leafs.append(
                        _component_from_sub(
                            sub,
                            weight_pct=10.0,
                            key="brand_visibility_authority",
                            title="Brand Visibility & Authority",
                        )
                    )
                    break
    return group_content_quality_components(leafs)


def _integrated_technical_score(components: list[dict[str, Any]]) -> float | None:
    available = sum(float(c.get("weight_pct") or 0.0) for c in components)
    if available <= 0:
        return None
    return round(
        sum(
            float(c.get("score") or 0.0) * float(c.get("weight_pct") or 0.0)
            for c in components
        )
        / available,
        1,
    )


def _pillar_score_rationale(
    cat: AgentCategoryResult,
    *,
    components: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Criterion breakdown for competitor comparison expand rows."""
    resolved = components if components is not None else _pillar_components_from_subs(cat)
    strengths: list[str] = []
    improvements: list[str] = []
    for raw in list(cat.strengths or []):
        line = client_friendly_finding(str(raw), "insight").strip()
        if line:
            strengths.append(line)
    for raw in list(cat.improvements or []):
        line = client_friendly_finding(str(raw), "insight").strip()
        if line:
            improvements.append(line)
    if not strengths and not improvements:
        for sub in list(cat.subs or []):
            for raw in list(sub.strengths or [])[:1]:
                line = client_friendly_finding(str(raw), "insight").strip()
                if line:
                    strengths.append(line)
            for raw in list(sub.improvements or [])[:1]:
                line = client_friendly_finding(str(raw), "insight").strip()
                if line:
                    improvements.append(line)
            if strengths or improvements:
                break
    strengths = _unique_preserve(strengths)[:2]
    improvements = _unique_preserve(improvements)[:2]
    summary = category_card_description(cat.key, float(cat.score)).strip()
    if not summary:
        summary = client_friendly_text(str(cat.detail or "")).strip()
    return {
        "summary": summary,
        "strengths": strengths,
        "improvements": improvements,
        "components": resolved,
    }


def competitive_comparison_rows(
    comp_path: Path | None,
    primary_url: str,
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    """Score each comparison.json site for the dashboard table.

    All rows are scored from crawl audits (no live brand probes on this path).
    The React API overlays the primary brand's pillars with Summary-integrated
    scores (probe AI Visibility + integrated Technical/Content) so the brand
    row matches the rest of the product; competitor AI Visibility remains
    crawl-relative and is not directly comparable to probe-based AI Visibility.
    """
    if not comp_path or not comp_path.is_file():
        return []
    data = _read_json(comp_path)
    rows = data.get("rows") or []
    if len(rows) < 2:
        return []

    primary_brand_name, competitor_brand_names = _comparison_brand_names(comp_path)
    try:
        primary_norm = normalize_base(primary_url)
    except ValueError:
        primary_norm = primary_url

    out: list[dict[str, Any]] = []
    for r in rows:
        audit = _load_audit_for_comparison_row(r)
        # Avoid live Playwright/brand probes on the API request thread.
        raw_bv = audit.get("brand_visibility")
        if not (isinstance(raw_bv, dict) and (raw_bv.get("platforms") or raw_bv.get("skipped"))):
            audit["brand_visibility"] = {
                "skipped": True,
                "brand_query": "",
                "brand_source": "comparison_no_live_probe",
                "base_url": audit.get("base_url") or "",
                "platforms": [],
                "method_note": "Live brand visibility scan skipped during competitor comparison render.",
            }
        _overall, cats = score_audit(audit, weights)
        by_key = {c.key: c for c in cats}
        bu = str(r.get("base_url") or audit.get("base_url") or "").strip()
        try:
            bu_norm = normalize_base(bu)
        except ValueError:
            bu_norm = bu
        is_primary = r.get("audit_label") == "primary" or bu_norm == primary_norm
        configured_name = (
            primary_brand_name
            if is_primary and primary_brand_name
            else competitor_brand_names.get(_comparison_url_key(bu)) or ""
        )
        ai_visibility = round(float(by_key["ai_visibility"].score), 1)
        tech_leafs = _integrated_technical_leaf_components(by_key)
        tech_components = group_technical_setup_components(tech_leafs)
        integrated_tech = _integrated_technical_score(tech_leafs or tech_components)
        # Prefer Summary-integrated Technical criteria when available; else crawl foundation.
        technical_setup = (
            float(integrated_tech)
            if integrated_tech is not None
            else round(float(by_key["technical_setup"].score), 1)
        )
        content_components = _content_quality_components_for_comparison(by_key)
        content_quality = round(float(by_key["content_structure"].score), 1)
        # Match Summary pillar blend: 40% AI Visibility + 30% Technical + 30% Content.
        overall = round(
            0.40 * ai_visibility + 0.30 * technical_setup + 0.30 * content_quality,
            1,
        )
        out.append(
            {
                "name": _comparison_display_name(bu, configured_name),
                "url": bu,
                "is_primary": bool(is_primary),
                "overall": overall,
                "ai_visibility": ai_visibility,
                "technical_setup": technical_setup,
                "content_quality": content_quality,
                "favicon_url": _comparison_favicon_url(bu),
                "ai_visibility_rationale": _pillar_score_rationale(by_key["ai_visibility"]),
                "technical_setup_rationale": _pillar_score_rationale(
                    by_key["technical_setup"],
                    components=tech_components
                    or group_technical_setup_components(
                        _pillar_components_from_subs(by_key["technical_setup"])
                    ),
                ),
                "content_quality_rationale": _pillar_score_rationale(
                    by_key["content_structure"],
                    components=content_components
                    or group_content_quality_components(
                        _pillar_components_from_subs(by_key["content_structure"])
                    ),
                ),
            }
        )

    out.sort(key=lambda row: float(row["overall"]), reverse=True)
    return out


def build_competitive_section(
    comp_path: Path | None,
    primary_url: str,
    weights: dict[str, float],
) -> tuple[list[str], list[str], str, str]:
    """Peer notes and scores-only comparison table HTML."""
    scored = competitive_comparison_rows(comp_path, primary_url, weights)
    if not scored:
        if not comp_path or not comp_path.is_file():
            return (
                [],
                ["No comparison.json—run crawl with --competitor for peer scores."],
                "<p><em>No competitor crawl data.</em></p>",
                "",
            )
        return (
            [],
            ["Only one site in comparison—add competitors."],
            "<p><em>Insufficient rows for peer comparison.</em></p>",
            "",
        )

    primary = next((row for row in scored if row["is_primary"]), scored[0])
    primary_score = float(primary["overall"])
    others = [row for row in scored if not row["is_primary"]]
    peer_avgs = [float(row["overall"]) for row in others]
    avg_peer = sum(peer_avgs) / len(peer_avgs) if peer_avgs else primary_score

    strengths: list[str] = []
    improve: list[str] = []
    if primary_score >= avg_peer + 5:
        strengths.append(
            f"Overall score ahead of peer average ({format_report_score(primary_score)} vs ~{format_report_score(avg_peer)})."
        )
    elif primary_score <= avg_peer - 5:
        improve.append(
            f"Overall score trails peer average ({format_report_score(primary_score)} vs ~{format_report_score(avg_peer)})."
        )
    else:
        strengths.append("Roughly in line with peers on composite score—see agent category gaps.")
    rank = 1 + sum(1 for avg in peer_avgs if avg > primary_score + 0.001)
    strengths.append(f"Peer rank by overall score: #{rank} of {len(scored)}.")

    lines = [
        '<div class="cmp-card">',
        '<div class="cmp-card-head">',
        "<h3>Site crawl comparison</h3>",
        "<p>Pillar scores from competitor site crawls.</p>",
        "</div>",
        '<div class="cmp-card-body">',
        '<table class="cmp-table" aria-label="Competitor comparison">',
        "<thead><tr>",
        '<th scope="col" class="cmp-col-brand">Brand</th>',
        '<th scope="col" class="cmp-col-score">Overall</th>',
        '<th scope="col" class="cmp-col-score">AI visibility</th>',
        '<th scope="col" class="cmp-col-score">Technical setup</th>',
        '<th scope="col" class="cmp-col-score">Content quality</th>',
        "</tr></thead><tbody>",
    ]
    for row in scored:
        name = html.escape(str(row["name"]))
        url = str(row["url"] or "")
        fav = ""
        if row.get("favicon_url"):
            fav = (
                f"<img class='cmp-favicon' src='{html.escape(str(row['favicon_url']), quote=True)}' "
                f"alt='' width='17' height='17' loading='lazy'>"
            )
        if row["is_primary"]:
            brand = (
                f"<div class='cmp-brand-cell'>{fav}"
                f"<a class='cmp-brand-link cmp-brand-link--own' href='{html.escape(url, quote=True)}' "
                f"target='_blank' rel='noopener noreferrer'>{name}</a></div>"
            )
            row_class = "cmp-row cmp-row--brand"
        else:
            brand = (
                f"<div class='cmp-brand-cell'>{fav}"
                f"<a class='cmp-brand-link' href='{html.escape(url, quote=True)}' "
                f"target='_blank' rel='noopener noreferrer'>"
                f"<span class='cmp-brand-name'>{name}</span></a></div>"
            )
            row_class = "cmp-row cmp-row--peer"
        lines.append(
            f"<tr class='{row_class}'>"
            f"<th scope='row'>{brand}</th>"
            f"<td class='cmp-score-cell'><span class='cmp-score-value score-tone-{_tone_class(float(row['overall']))}'>{format_report_score(float(row['overall']))}</span></td>"
            f"<td class='cmp-score-cell'><span class='cmp-score-value score-tone-{_tone_class(float(row['ai_visibility']))}'>{format_report_score(float(row['ai_visibility']))}</span></td>"
            f"<td class='cmp-score-cell'><span class='cmp-score-value score-tone-{_tone_class(float(row['technical_setup']))}'>{format_report_score(float(row['technical_setup']))}</span></td>"
            f"<td class='cmp-score-cell'><span class='cmp-score-value score-tone-{_tone_class(float(row['content_quality']))}'>{format_report_score(float(row['content_quality']))}</span></td>"
            "</tr>"
        )
    lines.extend(["</tbody></table>", "</div>", "</div>"])
    return strengths, improve, "\n".join(lines), ""


def load_ga4_traffic(
    audit_dir: Path,
    *,
    extra_search_dirs: tuple[Path, ...] | None = None,
) -> dict[str, Any] | None:
    """Load ``ga4_traffic.json`` from the audit folder, then optional extra dirs (e.g. ``--report-out``)."""
    dirs: list[Path] = [audit_dir.resolve()]
    if extra_search_dirs:
        for d in extra_search_dirs:
            if not d:
                continue
            r = Path(d).resolve()
            if r not in dirs:
                dirs.append(r)
    for base in dirs:
        p = base / "ga4_traffic.json"
        if not p.is_file():
            continue
        try:
            return _read_json(p)
        except (OSError, json.JSONDecodeError):
            continue
    return None


def maybe_fetch_ga4_traffic(
    audit_dir: Path,
    property_id: str | None,
    ai_channels_csv: str | None,
) -> None:
    """If property_id is set, call GA4 Data API and write audit_dir/ga4_traffic.json."""
    if not property_id or not str(property_id).strip():
        return
    try:
        import ga4_fetch
        from ga4_data_api import ga4_log
    except ImportError:
        print(
            "Warning: install google-analytics-data to use --ga4-property (pip install -r requirements.txt).",
            file=sys.stderr,
        )
        return
    ga4_log(
        f"maybe_fetch_ga4_traffic: start audit_dir={audit_dir.resolve()} "
        f"property={str(property_id).strip()!r}"
    )
    names = [x.strip() for x in (ai_channels_csv or "").split(",") if x.strip()]
    try:
        out = ga4_fetch.fetch_and_save(audit_dir.resolve(), property_id.strip(), names)
        ga4_log(f"maybe_fetch_ga4_traffic: success path={out}")
    except Exception as e:
        ga4_log(f"maybe_fetch_ga4_traffic: failed {e!r}")
        target = audit_dir.resolve() / "ga4_traffic.json"
        hint = "Check GOOGLE_APPLICATION_CREDENTIALS and GA4 property access."
        es = str(e)
        if "startDate" in es or "Invalid startDate" in es or "endDate" in es:
            hint = (
                "GA4 date strings must be YYYY-MM-DD, yesterday, today, or NdDaysAgo (e.g. 365daysAgo). "
                "Not 12monthsAgo—set GA4_START_DATE / GA4_END_DATE accordingly. "
                "Also verify credentials and property access."
            )
        print(
            f"Warning: GA4 fetch failed ({e}). Expected file: {target}. {hint}",
            file=sys.stderr,
        )


def _ga4_apply_display_policy(ga4: dict[str, Any]) -> dict[str, Any]:
    """Clip incomplete calendar month from series; apply 1% gap threshold when export includes denominator."""
    from ga4_fetch import last_complete_calendar_month_end

    cap_d = last_complete_calendar_month_end()
    cap_ym = f"{cap_d.year}{cap_d.month:02d}"
    out = dict(ga4)

    def _ym_ok(row: dict[str, Any]) -> bool:
        ym = str(row.get("year_month") or "").strip()
        return bool(ym) and ym <= cap_ym

    ms = out.get("monthly_sessions")
    if isinstance(ms, list):
        out["monthly_sessions"] = [r for r in ms if isinstance(r, dict) and _ym_ok(r)]
    mr = out.get("monthly_ai_revenue_pct")
    if isinstance(mr, list):
        out["monthly_ai_revenue_pct"] = [r for r in mr if isinstance(r, dict) and _ym_ok(r)]

    mbs = out.get("monthly_ai_sessions_by_source")
    if isinstance(mbs, dict):
        mo = mbs.get("months")
        if isinstance(mo, list):
            mbs = dict(mbs)
            mbs["months"] = [r for r in mo if isinstance(r, dict) and _ym_ok(r)]
            out["monthly_ai_sessions_by_source"] = mbs

    den = out.get("source_medium_gaps_ai_sessions_denominator_90d")
    gaps = out.get("source_medium_gaps") or out.get("ai_source_medium_gaps") or []
    if isinstance(gaps, list) and isinstance(den, (int, float)) and int(den) > 0:
        di = int(den)
        out["source_medium_gaps"] = [
            g for g in gaps if isinstance(g, dict) and int(g.get("sessions") or 0) * 100 >= di
        ]
    return out


def _ga4_section_html(ga4: dict[str, Any] | None) -> str:
    if not ga4:
        return """<p class="table-note"><em>No <code>ga4_traffic.json</code> in this audit folder. Run with <code>--ga4-property YOUR_NUMERIC_ID</code> (and optional <code>--ga4-ai-channels</code>), or set environment variable <code>GA4_PROPERTY_ID</code>, then re-run the full crawl or <code>--only-report &lt;audit_dir&gt;</code>. You can also copy an exported JSON beside <code>audit_summary.json</code>.</em></p>
<p class="table-note">Expected shape: <code>has_ai_channel</code>, <code>ai_channel_names</code>, <code>monthly_sessions</code> [{<code>year_month</code>, <code>label</code>, <code>total_sessions</code>, <code>ai_sessions</code>}] (legacy <code>weekly</code> / <code>iso_week</code> still supported), optional <code>monthly_ai_sessions_by_source</code> (<code>mode</code>, <code>months</code> with <code>by_source</code>, <code>source_order</code>), optional <code>monthly_ai_revenue_pct</code> and <code>misallocated_ai_sources</code> (custom channel bundle), <code>source_medium_gaps</code>.</p>"""

    ga4 = _ga4_apply_display_policy(ga4)

    has_ai = ga4.get("has_ai_channel")
    names = ga4.get("ai_channel_names") or []
    sessions_trend = ga4.get("monthly_sessions") or ga4.get("weekly") or []
    gaps = ga4.get("source_medium_gaps") or ga4.get("ai_source_medium_gaps") or []
    monthly = ga4.get("monthly_ai_revenue_pct") or []
    ccd = ga4.get("custom_channel_dimension")
    cce = ga4.get("custom_channel_bundle_error")
    weekly_ch_dim = ga4.get("weekly_channel_dimension") or ccd or "sessionDefaultChannelGroup"
    weekly_ch_dim_esc = html.escape(str(weekly_ch_dim))

    by_src_pack_raw = ga4.get("monthly_ai_sessions_by_source")
    by_src_pack: dict[str, Any] = by_src_pack_raw if isinstance(by_src_pack_raw, dict) else {}
    by_src_chart_ok = False
    _mos_bs = by_src_pack.get("months")
    if isinstance(_mos_bs, list):
        for _m in _mos_bs:
            if not isinstance(_m, dict):
                continue
            _bs = _m.get("by_source")
            if isinstance(_bs, dict) and sum(int(v or 0) for v in _bs.values()) > 0:
                by_src_chart_ok = True
                break

    if has_ai:
        intro = f"<p class=\"table-note\"><strong>AI channel configured:</strong> {html.escape(', '.join(str(x) for x in names) or 'yes')}.</p>"
    else:
        intro = '<p class="table-note"><strong>No dedicated AI channel</strong> detected in export—traffic may roll into Referral or Organic Search. Define a clear channel grouping in your analytics property.</p>'

    err_block = ""
    if cce:
        err_block = f"<p class=\"table-note\"><strong>Custom channel bundle:</strong> {html.escape(str(cce))}</p>"

    dim_note = ""
    if ccd and monthly:
        dim_note = (
            "<p class=\"table-note\">Monthly AI revenue share uses custom channel dimension "
            f"<code>{html.escape(str(ccd))}</code> (AI bucket names matched heuristically in the export pipeline).</p>"
        )

    gap_rows = ""
    for g in gaps[:40]:
        src = html.escape(str(g.get("session_source") or g.get("source") or ""))
        med = html.escape(str(g.get("session_medium") or g.get("medium") or ""))
        sess = g.get("sessions", g.get("session_count", ""))
        ch = html.escape(
            str(
                g.get("channel_bucket")
                or g.get("session_default_channel_group")
                or g.get("channel")
                or ""
            )
        )
        gap_rows += f"<tr><td>{src}</td><td>{med}</td><td>{ch}</td><td>{html.escape(str(sess))}</td></tr>"

    gaps_table = (
        "<p class=\"table-note\"><strong>Possible channel bucket gaps</strong> — "
        "These are traffic sources we identified that do not fall into your AI channel bucket, "
        "but may belong there. Review these suggestions and check the site before adding anything "
        "to the AI channel bucket. "
        "When the export includes a 90-day AI-channel session total, only sources with at least "
        "<strong>1%</strong> of that total in the same window are listed. "
        f"(Channel dimension: <code>{weekly_ch_dim_esc}</code>.)</p>"
        "<table class='data-table' aria-label='Possible channel bucket gaps'>"
        "<thead><tr><th>Source</th><th>Medium</th><th>Channel</th><th>Sessions</th></tr></thead><tbody>"
        + (gap_rows or "<tr><td colspan=\"4\"><em>None listed in export.</em></td></tr>")
        + "</tbody></table>"
    )

    need_charts = bool(monthly) or bool(sessions_trend) or by_src_chart_ok
    bundle_touched = any(
        k in ga4
        for k in (
            "monthly_ai_revenue_pct",
            "misallocated_ai_sources",
            "custom_channel_dimension",
            "custom_channel_bundle_error",
        )
    )
    conv_cards = _ga4_conversion_rate_cards_html(ga4)

    monthly_block = ""
    if monthly:
        safe_monthly = json.dumps(monthly, ensure_ascii=False)
        monthly_block = f"""
<p class="table-note"><strong>Monthly — AI revenue as % of total revenue</strong> (calendar <code>yearMonth</code>; requires ecommerce revenue in GA4). <strong>Partial current month is excluded.</strong></p>
<div class="chart-panel" style="min-height:280px"><canvas id="ga4MonthlyRevenueChart" aria-label="Monthly AI percent of revenue"></canvas></div>
<script type="application/json" id="ga4-monthly-data">{safe_monthly}</script>
"""
    elif bundle_touched:
        if not cce and not monthly:
            monthly_block = (
                "<p class=\"table-note\"><em>No monthly revenue rows in this export "
                "(no ecommerce revenue in the date range, or all values zero). "
                f"Sessions trend chart below still uses <code>{weekly_ch_dim_esc}</code>.</em></p>"
            )

    sessions_trend_block = _ga4_sessions_trend_chart_html(ga4) if sessions_trend else ""

    ai_by_source_block = ""
    if by_src_chart_ok:
        safe_by_src = json.dumps(by_src_pack, ensure_ascii=False)
        by_mode = by_src_pack.get("mode")
        if by_mode == "ai_channel":
            by_caption_html = '<p class="table-note"><strong>Sessions by AI platform over time</strong></p>'
        else:
            by_caption_html = '<p class="table-note"><strong>Sessions by AI platform over time</strong></p>'
        ai_by_source_block = (
            by_caption_html
            + '<div class="chart-panel" style="min-height:320px">'
            + '<canvas id="ga4AiSessionsBySourceChart" aria-label="Monthly AI sessions stacked by source"></canvas>'
            + "</div>\n"
            + f'<script type="application/json" id="ga4-ai-sessions-by-source-data">{safe_by_src}</script>\n'
        )

    chart_script = ""
    if need_charts:
        chart_script = """
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script>
(function() {
  if (!window.Chart) return;
  function ymLabel(ym) {
    const s = String(ym || '');
    if (s.length === 6 && /^\\d{6}$/.test(s)) return s.slice(0,4) + '-' + s.slice(4);
    return s;
  }
  const monthlyEl = document.getElementById('ga4-monthly-data');
  if (monthlyEl) {
    try {
      const monthly = JSON.parse(monthlyEl.textContent);
      const canvas = document.getElementById('ga4MonthlyRevenueChart');
      if (canvas && monthly.length) {
        const labels = monthly.map(function(m) { return ymLabel(m.year_month); });
        const pct = monthly.map(function(m) { return Number(m.ai_pct_of_revenue || 0); });
        new Chart(canvas, {
          type: 'line',
          data: {
            labels: labels,
            datasets: [{
              label: 'AI % of total revenue',
              data: pct,
              borderColor: '#5a6b4a',
              backgroundColor: 'rgba(90,107,74,0.12)',
              tension: 0.25,
              fill: true
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
              y: {
                beginAtZero: true,
                title: { display: true, text: '% of revenue' }
              }
            },
            plugins: {
              tooltip: {
                callbacks: {
                  label: function(ctx) {
                    const i = ctx.dataIndex;
                    const row = monthly[i] || {};
                    return ' ' + (ctx.dataset.label || '') + ': ' + Number(ctx.raw).toFixed(2) + '%';
                  },
                  afterBody: function(items) {
                    if (!items || !items.length) return '';
                    const i = items[0].dataIndex;
                    const row = monthly[i] || {};
                    const tr = row.total_revenue != null ? row.total_revenue : '';
                    const ar = row.ai_revenue != null ? row.ai_revenue : '';
                    return 'Total revenue: ' + tr + '\\nAI revenue: ' + ar;
                  }
                }
              }
            }
          }
        });
      }
    } catch (e) {}
  }
  function ga4SessionTrendLabel(row) {
    if (!row) return '';
    if (row.label) return String(row.label);
    const ym = String(row.year_month || '');
    if (ym.length === 6 && /^\\d{6}$/.test(ym)) {
      const y = ym.slice(0, 4);
      const m = parseInt(ym.slice(4, 6), 10);
      const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
      if (m >= 1 && m <= 12) return names[m - 1] + ' ' + y;
    }
    return String(row.iso_week || row.week || '');
  }
  const bySrcEl = document.getElementById('ga4-ai-sessions-by-source-data');
  if (bySrcEl) {
    try {
      const pack = JSON.parse(bySrcEl.textContent);
      const months = pack.months || [];
      let order = pack.source_order || [];
      if (months.length) {
        if (!order.length) {
          const s = new Set();
          months.forEach(function(m) {
            Object.keys(m.by_source || {}).forEach(function(k) { s.add(k); });
          });
          order = Array.from(s);
        }
        const canvasSrc = document.getElementById('ga4AiSessionsBySourceChart');
        if (canvasSrc && order.length) {
          const labels = months.map(ga4SessionTrendLabel);
          const palette = [
            '#9a4b2f', '#5a6b4a', '#7c6f64', '#b08968', '#6d8356', '#4a6670', '#8b5a6b', '#5c4d7a',
            '#7a6e4a', '#4d6b8b', '#6b5648', '#567d6b', '#7a5648', '#5a5246', '#6b6570', '#8b6f4a'
          ];
          const datasets = order.map(function(src, i) {
            return {
              label: src,
              data: months.map(function(m) {
                return Number((m.by_source || {})[src] || 0);
              }),
              backgroundColor: palette[i % palette.length],
              stack: 'aiSrc'
            };
          });
          new Chart(canvasSrc, {
            type: 'bar',
            data: { labels: labels, datasets: datasets },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              interaction: { mode: 'index', intersect: false },
              scales: {
                x: { stacked: true },
                y: { stacked: true, beginAtZero: true, title: { display: true, text: 'Sessions' } }
              },
              plugins: {
                legend: { position: 'bottom' },
                tooltip: {
                  callbacks: {
                    footer: function(items) {
                      var t = 0;
                      items.forEach(function(it) { t += Number(it.raw || 0); });
                      return 'Month total: ' + t;
                    }
                  }
                }
              }
            }
          });
        }
      }
    } catch (e) {}
  }
})();
</script>
"""

    notes = ga4.get("notes")
    note_p = f"<p class='table-note'>{html.escape(str(notes))}</p>" if notes else ""

    return (
        conv_cards
        + sessions_trend_block
        + ai_by_source_block
        + chart_script
        + (_ga4_sessions_trend_chart_script() if sessions_trend_block else "")
    )


_AGENT_SECTION_ICONS: dict[str, str] = {
    "ai_visibility": "🤖",
    "technical_setup": "⚙️",
    "content_structure": "✍️",
}


def _reader_help_block_ai_visibility(audit: dict[str, Any]) -> str:
    """Plain-language context for JSON-LD and llms.txt (non-technical readers)."""
    summ = audit.get("summary") or {}
    any_jld = bool(summ.get("any_json_ld"))
    pages = [p for p in (audit.get("pages") or []) if p.get("http_status") == 200]
    ok_n = len(pages)
    jld_n = sum(1 for p in pages if p.get("has_json_ld"))
    jld_ratio = _safe_ratio(jld_n, ok_n) if ok_n else 0.0
    lt = audit.get("llms_txt") or {}
    llms_live = bool(lt.get("exists"))

    if any_jld and jld_ratio >= 0.35:
        jld_para = (
            "<strong>JSON-LD</strong> is hidden, structured labeling in your page code (for example your business name, "
            "logo, or article type). Search and AI tools read it to understand what each page is about. "
            "Your sample already includes JSON-LD on many pages—next, keep it accurate, cover any missing templates, "
            "and align it with what people actually see on the page."
        )
    elif any_jld:
        jld_para = (
            "<strong>JSON-LD</strong> is structured data in your page code that tells search and AI systems who you are "
            "and what each page represents. You have some JSON-LD, but it looks thin across the pages we sampled—spreading "
            "complete, accurate labels to more key templates usually lifts how confidently you get cited."
        )
    else:
        jld_para = (
            "<strong>JSON-LD</strong> is a machine-readable “fact sheet” embedded in your HTML (organization, products, "
            "articles, and so on). Without it, AI and search have to infer meaning from text alone, which hurts quoting and "
            "visibility—adding honest, up-to-date structured data is one of the most important fixes in this category."
        )

    if llms_live:
        llms_para = (
            "<strong>llms.txt</strong> is a small public file on your site that points AI assistants to the pages and topics "
            "you want emphasized. Yours appears to be published—still review it after navigation or content overhauls, trim "
            "stale links, and make priorities obvious so tools don’t rely on guesswork."
        )
    else:
        llms_para = (
            "<strong>llms.txt</strong> is an optional but increasingly common helper file: a short list of important URLs "
            "and notes so AI tools know where to look first. It doesn’t replace good content, but it reduces confusion and "
            "helps assistants surface the right pages when people ask about your brand."
        )

    return f"<p>{jld_para}</p><p>{llms_para}</p>"


def _reader_help_block_technical_setup(audit: dict[str, Any]) -> str:
    """Plain-language context for robots.txt and what this audit’s technical score reflects."""
    rt = audit.get("robots_txt") or {}
    robots_ok = bool(rt.get("exists"))

    if robots_ok:
        robots_para = (
            "<strong>robots.txt</strong> is a simple instruction file at the root of your site that tells crawlers "
            "(including many AI crawlers) which URLs they may fetch. We could retrieve yours—still audit it whenever you "
            "launch new sections, and avoid overly broad “block everything” rules that can hide you from AI search results."
        )
    else:
        robots_para = (
            "<strong>robots.txt</strong> is the standard place crawlers look for permission rules before loading your pages. "
            "If it’s missing or unreachable, automated systems may not know your preferences—publish a clear file and revisit "
            "it after major site changes."
        )

    metrics_para = (
        "The <strong>technical checks in this report</strong> look at whether <strong>robots.txt</strong> was available, "
        "whether sampled pages returned a normal <strong>successful response</strong> (we report this as an HTTP status such as "
        "200—meaning the page answered OK), and whether our crawl used a <strong>trusted HTTPS</strong> connection. "
        "<strong>Core Web Vitals</strong> (real-user loading and layout stability) and <strong>time to first byte</strong> "
        "(how quickly the server starts responding) matter for visitors and search quality, but they are "
        "<em>not measured in this automated run</em>—use your analytics toolkit or a dedicated speed test for those."
    )

    return f"<p>{robots_para}</p><p>{metrics_para}</p>"


def _agent_category_section_html(
    agent: AgentCategoryResult,
    *,
    extra_below: str = "",
    reader_help_html: str = "",
    audit: dict[str, Any] | None = None,
) -> str:
    """One score-breakdown section: headline score, plain summary, insight pair, optional appendix."""
    below = f"\n{extra_below}" if extra_below else ""
    rh = ""
    if reader_help_html.strip():
        rh = (
            '<div class="section-reader-help" aria-label="What this section means in plain language">'
            f"{reader_help_html}"
            "</div>\n  "
        )
    g_good, g_bad = _insight_groups_from_category(agent, audit)
    insights = _insight_pair_grouped_html(
        g_good,
        g_bad,
        empty_good="Nothing highlighted in this area yet.",
        empty_bad="No gaps recorded in this area yet.",
    )
    title_suffix = _category_section_head_suffix(agent.score, agent.weight)
    return f"""<section class="report-block agent-category-block score-breakdown-major" aria-labelledby="agent-{html.escape(agent.key, quote=True)}">
  {_report_section_head(agent.title, f"agent-{html.escape(agent.key, quote=True)}", suffix_html=title_suffix)}
  {rh}<p class="section-lead">{html.escape(agent.detail)}</p>
  {insights}
{below}
</section>"""


def render_html(
    audit: dict[str, Any],
    overall: float,
    categories: list[AgentCategoryResult],
    working: list[str],
    priorities: list[str],
    comp_table: str,
    comp_detail: str,
    samples: dict[str, str | None],
    ga4_html: str,
    ga4_data: dict[str, Any] | None,
    audit_dir: Path,
    out_path: Path,
    *,
    action_plan_phases: tuple[list[str], list[str], list[str], list[str]] | None = None,
) -> None:
    base = html.escape(audit.get("base_url") or "")
    outd = html.escape(audit.get("output_dir") or "")
    generated = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    report_css = _load_geo_report_css(audit_dir.resolve())
    # AI crawler access (used in its own section + as an input to readiness/E-E-A-T)
    robots_text = _robots_text_for_ai(audit)
    br = audit.get("_ai_crawler_score_breakdown")
    if isinstance(br, dict) and isinstance(br.get("total"), (int, float)):
        ai_crawler_score = float(br["total"])
    else:
        ai_crawler_score, _, _, br = score_ai_crawler_robots(
            robots_text, homepage_url=_homepage_from_audit(audit), audit=audit
        )
        audit["_ai_crawler_score_breakdown"] = br

    def bullets(items: list[str]) -> str:
        if not items:
            return "<li><em>None noted.</em></li>"
        return "".join(f"<li>{html.escape(t)}</li>" for t in items)

    rows_summary: list[str] = []
    cats_by_key = {c.key: c for c in categories}
    for c in categories:
        contrib = c.score * (c.weight / 100.0)
        pill = _pill_class(c.score)
        rows_summary.append(
            "<tr>"
            f"<td>{html.escape(c.title)}</td>"
            f"<td>{c.weight:.0f}%</td>"
            f"<td><span class='score-pill {pill}'>{format_report_score(c.score)}</span></td>"
            f"<td>{contrib:.1f}</td>"
            "</tr>"
        )

    overall_tone = _tone_class(overall)
    overall_deg = max(0.0, min(360.0, overall * 3.6))
    overall_pill = _pill_class(overall)
    overall_label = _score_label(overall)
    gauge_color = {
        "green": "var(--score-green)",
        "blue": "var(--score-blue)",
        "yellow": "var(--score-yellow)",
        "red": "var(--score-red)",
    }[overall_tone]
    rows_summary.append(
        "<tr>"
        "<td><strong>Overall (weighted)</strong></td>"
        "<td>100%</td>"
        f"<td><span class='score-pill {overall_pill}'>{format_report_score(overall)}</span></td>"
        f"<td>{format_report_score(overall)}</td>"
        "</tr>"
    )
    sample_blocks = []
    for label, content in samples.items():
        sid = html.escape(label, quote=True)
        if content:
            sample_blocks.append(
                f"""<details class="sample" id="sample-{sid}">
  <summary><span>{html.escape(label)}</span><span class="hint">Expand · scroll inside · copy</span></summary>
  <pre tabindex="0">{html.escape(content)}</pre>
</details>"""
            )
        else:
            sample_blocks.append(
                f"""<details class="sample" id="sample-{sid}"><summary><span>{html.escape(label)}</span><span class="hint">Not available</span></summary></details>"""
            )

    _report_head = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="color-scheme" content="light"/>
  <title>GEO audit report — {base}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,400;0,14..32,500;0,14..32,600;0,14..32,700;1,14..32,400&amp;display=swap" rel="stylesheet"/>
  <link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;600;700&amp;display=swap" rel="stylesheet"/>
  <style>
"""
    _inputs = audit.get("audit_inputs") or {}
    _industry = str(_inputs.get("industry") or "").strip()
    exec_summary = _resolve_executive_summary_html(
        audit_dir,
        audit,
        categories,
        overall,
        priorities,
        working,
    )
    key_findings_html = _key_findings_section_html(
        categories, priorities, industry=_industry
    )
    _brand_meta = str(_inputs.get("brand") or "").strip()
    _meta_line_parts: list[str] = []
    if _brand_meta:
        _meta_line_parts.append(f"Brand: {html.escape(_brand_meta)}")
    if _industry:
        _meta_line_parts.append(f"Industry: {html.escape(_industry)}")
    header_meta_line = (
        f'<div class="header-meta">{" · ".join(_meta_line_parts)}</div>'
        if _meta_line_parts
        else ""
    )

    # Platform readiness cards
    platform_scores = _platform_readiness_scores(
        overall=overall,
        agents=categories,
        ai_crawler_score=ai_crawler_score,
        audit=audit,
    )
    platform_cards = []
    for p in platform_scores:
        ps = float(p["score"])
        tone = _tone_class(ps)
        fill_color = {
            "green": "var(--score-green)",
            "blue": "var(--score-blue)",
            "yellow": "var(--score-yellow)",
            "red": "var(--score-red)",
        }[tone]
        platform_cards.append(
            f"""<div class="platform-card">
  <div class="platform-name">{html.escape(str(p['icon']))} {html.escape(str(p['name']))}</div>
  <div class="platform-confidence">Confidence: medium — automated crawl and signal proxies; confirm in platform-specific tools.</div>
  <div class="platform-score" aria-label="Readiness score out of 100">
    <span class="platform-score-num tone-{tone}">{ps:.0f}</span><span class="platform-score-denom tone-{tone}">/100</span>
  </div>
  <div class="platform-bar">
    <div class="progress-bar-container"><div class="progress-bar-fill" style="width:{ps:.1f}%; background:{fill_color}"></div></div>
    <span class="platform-bar-label tone-{tone}">{ps:.0f}</span>
  </div>
  <div class="platform-gap">{html.escape(str(p['gap']))}</div>
</div>"""
        )

    crawler_grid_html = _ai_crawler_access_table_html(robots_text, audit)

    # EEAT breakdown bars
    eeat = _eeat_breakdown(audit=audit, agents=categories, ai_crawler_score=ai_crawler_score)
    eeat_cards = []
    for e in eeat:
        es = float(e["score"])
        pill = _pill_class(es)
        fill_color = {
            "pill-green": "var(--score-green)",
            "pill-blue": "var(--score-blue)",
            "pill-yellow": "var(--score-yellow)",
            "pill-red": "var(--score-red)",
        }[pill]
        evidence_rows: list[str] = []
        for item in e.get("evidence") or []:
            url = html.escape(str(item.get("url") or ""), quote=True)
            title = html.escape(str(item.get("title") or "Sampled page"))
            snippet = html.escape(str(item.get("snippet") or ""))
            source = (
                f'<a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>'
                if url else title
            )
            evidence_rows.append(
                f'<li><div class="eeat-evidence-source">{source}</div>'
                f'<blockquote>{snippet}</blockquote></li>'
            )
        evidence_note = html.escape(str(e.get("evidence_note") or ""))
        evidence_html = (
            f'<div class="eeat-evidence"><div class="eeat-evidence-title">Example content behind this score</div>'
            f'<p class="eeat-evidence-note">{evidence_note}</p>'
            f'<ul>{"".join(evidence_rows)}</ul></div>'
            if evidence_rows
            else f'<div class="eeat-evidence eeat-evidence-empty">'
            f'<div class="eeat-evidence-title">Example content behind this score</div>'
            f'<p class="eeat-evidence-note">{evidence_note}</p></div>'
        )
        eeat_cards.append(
            f"""<div class="eeat-item">
  <div class="eeat-label">
    <div class="eeat-name-block">
      <span class="eeat-name">{html.escape(str(e["name"]))}</span>
      <span class="eeat-tagline">{html.escape(str(e["tagline"]))}</span>
    </div>
    <span class="eeat-score">{es:.0f}/100</span>
  </div>
  <p class="eeat-meaning">{html.escape(str(e["what_it_means"]))}</p>
  <div class="progress-bar-container"><div class="progress-bar-fill" style="width:{es:.1f}%; background:{fill_color}"></div></div>
  <p class="eeat-how-scored"><span class="eeat-how-label">How we calculate this number:</span> {html.escape(str(e["how_scored"]))}</p>
  {evidence_html}
</div>"""
        )

    if action_plan_phases is not None:
        quick, medium, strategic, plan_policy = action_plan_phases
        projected_narrative: str | None = None
    else:
        quick, medium, strategic = _priorities_spaced(priorities, max_per=5)
        plan_policy = []
        projected_narrative = None

    (quick, medium, strategic, plan_policy), projected_narrative = _resolve_recommendations_for_report(
        audit_dir,
        audit,
        overall,
        categories,
        priorities,
        working,
        (quick, medium, strategic, plan_policy),
        projected_narrative,
    )

    action_plan_html = _action_plan_columns_html(
        quick, medium, strategic, policy_notes=plan_policy
    )
    projected_performance_html = _projected_performance_html(
        overall, quick, medium, strategic, narrative_override=projected_narrative
    )

    _bd = br if isinstance(br, dict) else {}
    _t1p = float(_bd.get("tier1_points") or 0)
    _fp = float(_bd.get("foundational_points") or 0)
    _t2p = float(_bd.get("tier2_eco_points") or _bd.get("tier2_points") or 0)
    _blp = float(_bd.get("blanket_points") or 0)
    _fip = float(_bd.get("files_points") or 0)
    _reg = _bd.get("regional_access") if isinstance(_bd.get("regional_access"), dict) else {}
    _reg_regions = int(_reg.get("regions") or 0)
    _reg_with_data = int(_reg.get("with_data") or 0)
    _reg_avg = float(_reg.get("avg_access") or 0.0)
    _reg_mod = float(_reg.get("modifier") or 0.0)
    _reg_pct = max(0.0, min(100.0, _reg_avg * 100.0))
    _reg_score_5 = max(0.0, min(5.0, _reg_avg * 5.0))
    _reg_pct_label = f"{_reg_pct:.1f}".rstrip("0").rstrip(".")
    _reg_score_5_label = f"{_reg_score_5:.1f}".rstrip("0").rstrip(".")
    _t1a = html.escape(str(_bd.get("tier1_allowed") or "?"))
    _fa = html.escape(str(_bd.get("foundational_allowed") or "?"))
    _t2a = html.escape(str(_bd.get("tier2_eco_allowed") or _bd.get("tier2_allowed") or "?"))
    _regional_rows = audit.get("regional_crawls")
    _region_names: list[str] = []
    if isinstance(_regional_rows, list):
        for _row in _regional_rows:
            if not isinstance(_row, dict):
                continue
            _country = str(_row.get("country") or "").strip()
            _code = str(_row.get("country_code") or "").strip().upper()
            if _country and _code:
                _label = f"{_country} ({_code})"
            else:
                _label = _country or _code
            if _label:
                _region_names.append(_label)
    _region_names = list(dict.fromkeys(_region_names))
    _region_names_preview = ", ".join(_region_names[:3])
    if len(_region_names) > 3:
        _region_names_preview += f", +{len(_region_names) - 3} more"
    _region_names_html = html.escape(_region_names_preview) if _region_names_preview else ""
    _reg_summary_line = (
        f" Regional coverage summary: crawled {_reg_regions} additional region(s), "
        f"coverage {_reg_pct_label}%, score {_reg_score_5_label}/5 "
        f"(technical modifier {_reg_mod:+.1f})."
        if _reg_regions > 0
        else ""
    )
    crawler_composite_note = (
        "<p class=\"table-note crawler-composite\">"
        "<strong>Composite (0–100):</strong> "
        f"Tier 1 AI retrieval {_t1p:.1f}/45 ({_t1a} allowed), "
        f"Googlebot+Bingbot {_fp:.1f}/20 ({_fa} allowed), "
        f"Tier-2 ecosystem {_t2p:.1f}/15 ({_t2a} allowed), "
        f"no blanket AI/search blocks {_blp:.1f}/10, "
        f"discovery (llms.txt + sitemap) {_fip:.1f}/10. "
        "Crawler rows use robots.txt plus hero-page <code>meta robots</code> / "
        "<code>X-Robots-Tag</code> (noindex). Blanket uses <code>User-agent: *</code> "
        "<code>Disallow: /</code> and sampled <code>noai</code>."
        f"{html.escape(_reg_summary_line)}"
        "</p>"
    )
    regional_crawler_summary_html = ""
    if _reg_regions > 0:
        _regions_clause = f" in {_region_names_html}" if _region_names_html else ""
        regional_crawler_summary_html = (
            '<p class="table-note">'
            f"<strong>Regional crawl summary:</strong> "
            f"crawled {_reg_regions} additional region(s){_regions_clause} with "
            f"<strong>{_reg_pct_label}% coverage</strong>. "
            f"Regional coverage score: <strong>{_reg_score_5_label}/5</strong> "
            f"({_reg_with_data}/{_reg_regions} regions produced usable data; technical modifier {_reg_mod:+.1f})."
            "</p>"
        )
    crawler_block_for_tech = f"""<div class="score-breakdown-sub score-breakdown-nested" aria-labelledby="crawler-heading">
  {_report_subhead("AI crawler access", "crawler-heading")}
  <p class="score-breakdown-subdesc">This table shows which search and AI crawlers can access the site’s public pages under the current rules. For AI visibility, the practical goal is to let answer-oriented crawlers read the pages you want cited, while deciding separately whether training-only crawlers fit your data-use policy.</p>
  {regional_crawler_summary_html}
  <details class="crawler-composite-details"><summary class="table-note">Technical scoring detail (how the composite is built)</summary>
  {crawler_composite_note}
  </details>
  {crawler_grid_html}
</div>"""

    eeat_explainer = (
        "<p class=\"eeat-explainer\"><strong>E-E-A-T</strong> stands for "
        "<strong>E</strong>xperience, <strong>E</strong>xpertise, <strong>A</strong>uthoritativeness, "
        "and <strong>T</strong>rustworthiness—how well your content demonstrates real-world experience, "
        "subject-matter depth, credible sourcing, and reliability. Search and AI systems use these signals "
        "to decide what to surface and cite. The scores below are <em>approximate indicators</em> from "
        "this crawl; they complement—not replace—a full editorial and fact-check review.</p>"
    )

    eeat_block_for_content = f"""<div class="score-breakdown-sub score-breakdown-nested" aria-labelledby="eeat-heading">
  {_report_subhead("Trust signals from your crawl (E-E-A-T view)", "eeat-heading")}
  <p class="score-breakdown-subdesc">Four cards score how strongly sampled website content demonstrates Experience, Expertise, Authoritativeness, and Trust. Each score is supported by content examples where matching evidence was found.</p>
  {eeat_explainer}
  <div class="eeat-grid">
    {"".join(eeat_cards)}
  </div>
</div>"""

    brand_visibility_block = _brand_visibility_subsection_html(audit)

    platform_block_for_ai = f"""<div class="score-breakdown-sub score-breakdown-nested" aria-labelledby="platform-readiness-heading">
  {_report_subhead("AI platform readiness", "platform-readiness-heading")}
  <p class="score-breakdown-subdesc">These scores estimate how ready the site is for each major AI search experience in this model. They combine crawl signals, brand visibility checks, structured data, content quality, and discovery signals such as llms.txt and sitemap reachability. Treat them as a guide—confirm with live searches and platform tools where it matters.</p>
  <div class="platform-grid">
    {"".join(platform_cards)}
  </div>
</div>"""

    agent_ai_html = _agent_category_section_html(
        cats_by_key["ai_visibility"],
        extra_below=platform_block_for_ai,
        reader_help_html=_reader_help_block_ai_visibility(audit),
        audit=audit,
    )
    agent_tech_html = _agent_category_section_html(
        cats_by_key["technical_setup"],
        extra_below=crawler_block_for_tech,
        reader_help_html=_reader_help_block_technical_setup(audit),
        audit=audit,
    )
    agent_content_html = _agent_category_section_html(
        cats_by_key["content_structure"],
        extra_below=eeat_block_for_content + brand_visibility_block,
        audit=audit,
    )

    weights_hist = _weights_from_categories(categories)
    score_history = _collect_primary_score_history(audit_dir, audit, weights_hist)
    vertical_score_overview = _vertical_score_overview_html(overall, categories, score_history)
    ga4_insights_lead = ""
    ga4_summary_block = (
        ga4_html
        if (ga4_data and _ga4_has_displayable_data(_ga4_apply_display_policy(dict(ga4_data))))
        else _ga4_connect_prompt_html()
    )

    ga4_applied = _ga4_apply_display_policy(dict(ga4_data)) if ga4_data else None
    ga4_preview_suffix = (
        ' <span class="section-head-actions"><a class="report-pillar-cta" href="#ga4-traffic">'
        "GA4 — AI traffic (full appendix)</a></span>"
    )
    ga4_inline_preview = _ga4_summary_sessions_preview_html(ga4_data) if ga4_data else ""
    if ga4_inline_preview:
        ga4_preview_inner = ga4_inline_preview
    elif ga4_applied and _ga4_has_displayable_data(ga4_applied):
        ga4_preview_inner = (
            '<p class="section-lead">This export includes other GA4 views but no monthly session time series. '
            'Open the <a class="report-pillar-cta" href="#ga4-traffic">GA4 — AI traffic</a> appendix for charts and tables.</p>'
        )
    else:
        ga4_preview_inner = _ga4_connect_prompt_html()
    ga4_preview_section = f"""    <section class="report-block" aria-labelledby="ga4-preview-heading">
      {_report_section_head("GA4 — Monthly AI sessions", "ga4-preview-heading", suffix_html=ga4_preview_suffix)}
      {ga4_preview_inner}
    </section>
"""

    weighted_table_details = f"""<details class="weighted-breakdown-details">
  <summary class="weighted-breakdown-summary">Weighted breakdown (table)</summary>
  <p class="section-lead">Weights reflect how much each area contributes to the overall score. Together they total 100%.</p>
  <table class="data-table" aria-label="Category summary table">
    <thead><tr><th>Area</th><th>Weight</th><th>Score</th><th>Weighted points</th></tr></thead>
    <tbody>{"".join(rows_summary)}</tbody>
  </table>
  <div class="table-note">Output folder: <code>{outd}</code></div>
</details>"""

    _report_footer_strip = """    <section class="report-block report-block--footer" aria-label="Footer">
      <div class="table-note">Produced by <code>create-report.py</code>. Scores are heuristic; validate with live checks and analytics.</div>
    </section>"""

    _report_body = f"""  </style>
</head>
<body class="geo-report">
  <header class="header">
    <div class="container">
      <div class="header-content">
        <div class="header-text">
          <h1>GEO Audit Report</h1>
          <div class="subtitle">{base}</div>
          {header_meta_line}
          <div>
            <span class="badge badge-type">Full audit</span>
            <span class="badge badge-date">{generated}</span>
          </div>
        </div>
        <div class="score-gauge" aria-label="Overall score gauge">
          <div class="gauge-ring" style="background: conic-gradient({gauge_color} 0deg, {gauge_color} {overall_deg:.1f}deg, rgba(255,255,255,0.12) {overall_deg:.1f}deg, rgba(255,255,255,0.12) 360deg);">
            <div class="gauge-inner">
              <div class="score-number">{format_report_score(overall)}</div>
              <div class="score-label" style="color:{gauge_color}">{overall_label}</div>
              <div class="score-max">/ 100</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </header>

  <main class="container report-main-with-tabs">
    <div class="report-tab-panel is-active" role="tabpanel" id="tab-panel-summary" data-tab-panel="summary" aria-labelledby="tab-btn-summary">
    <section class="report-block report-block--first" aria-labelledby="exec-heading">
      {_report_section_head("Executive summary", "exec-heading")}
      <div class="exec-summary callout">{exec_summary}</div>
    </section>

    <section class="report-block" aria-labelledby="overview-heading">
      {_report_section_head("Score overview", "overview-heading")}
      <p class="section-lead">Each row shows the current score on a 0–100 scale and a sparkline of past audits for this URL in the same output folder (e.g. sibling folders under <code>audit_output/</code>).</p>
      {vertical_score_overview}
      {weighted_table_details}
    </section>

    {ga4_preview_section}

    {key_findings_html}
    {_report_footer_strip}
    </div>

    <div class="report-tab-panel" role="tabpanel" id="tab-panel-ga4-traffic" data-tab-panel="ga4-traffic" aria-labelledby="tab-btn-ga4-traffic" hidden>
    <section class="report-block report-block--first" aria-labelledby="ga4-traffic-heading">
      {_report_section_head("Direct AI Traffic", "ga4-traffic-heading")}
      {ga4_insights_lead}
      {ga4_summary_block}
    </section>
    {_report_footer_strip}
    </div>

    <div class="report-tab-panel" role="tabpanel" id="tab-panel-recommendations" data-tab-panel="recommendations" aria-labelledby="tab-btn-recommendations" hidden>
    <section class="report-block report-block--first" aria-labelledby="plan-heading">
      {_report_section_head("Prioritized action plan", "plan-heading")}
      <p class="section-lead">At most <strong>five</strong> items per horizon (<strong>15</strong> total cap). Overflow from Quick wins rolls into Medium-term, then Strategic. The <strong>Strategic</strong> column is oriented to long-cycle <strong>content development</strong>, <strong>brand authority</strong>, and <strong>citability</strong> (research-led publishing, governance, corroboration)—not quick technical patches. <strong>Estimated lift</strong> lines use conservative bands for the composite GEO score (category weights 40% AI visibility / 30% Technical / 30% Content)—they are <strong>not</strong> additive; validate with a follow-up crawl.</p>
      {action_plan_html}
    </section>

    <section class="report-block" aria-labelledby="projection-heading">
      {_report_section_head("Projected performance", "projection-heading")}
      {projected_performance_html}
    </section>
    {_report_footer_strip}
    </div>

    <div class="report-tab-panel" role="tabpanel" id="tab-panel-competitors" data-tab-panel="competitors" aria-labelledby="tab-btn-competitors" hidden>
    <section class="report-block report-block--first" aria-labelledby="comp-heading">
      {_report_section_head("Competitor comparison", "comp-heading")}
      <p class="section-lead">Compare AI visibility, technical setup, and content quality scores from competitor site crawls.</p>
      {comp_table}
    </section>
    {_report_footer_strip}
    </div>

    <div class="report-tab-panel" role="tabpanel" id="tab-panel-ai" data-tab-panel="ai-visibility" aria-labelledby="tab-btn-ai" hidden>
    {agent_ai_html}
    {_report_footer_strip}
    </div>

    <div class="report-tab-panel" role="tabpanel" id="tab-panel-technical" data-tab-panel="technical" aria-labelledby="tab-btn-technical" hidden>
    {agent_tech_html}
    {_report_footer_strip}
    </div>

    <div class="report-tab-panel" role="tabpanel" id="tab-panel-content" data-tab-panel="content" aria-labelledby="tab-btn-content" hidden>
    {agent_content_html}
    {_report_footer_strip}
    </div>

    <div class="report-tab-panel" role="tabpanel" id="tab-panel-samples" data-tab-panel="samples" aria-labelledby="tab-btn-samples" hidden>
    <section class="report-block report-block--first" aria-labelledby="samples-heading">
      {_report_section_head("Sample scripts", "samples-heading")}
      <p class="section-lead">These are example versions of key files you can implement on the site to improve AI crawler access and citability: a merged <code>robots.txt</code>, a WebSite <code>json-ld</code> sample, and a generated <code>llms.txt</code> skeleton. Expand each block to scroll and copy, then adapt hosts and policy before publishing.</p>
      {"".join(sample_blocks)}
    </section>
    {_report_footer_strip}
    </div>

  </main>
  <script>
(function () {{
  var panels = document.querySelectorAll("[data-tab-panel]");
  if (!panels.length) return;
  function show(target) {{
    panels.forEach(function (panel) {{
      var on = panel.getAttribute("data-tab-panel") === target;
      panel.toggleAttribute("hidden", !on);
      panel.classList.toggle("is-active", on);
    }});
    try {{
      if (history.replaceState) history.replaceState(null, "", "#" + target);
    }} catch (e) {{}}
  }}
  function syncFromHash() {{
    var h = (location.hash || "").replace(/^#/, "");
    if (h && document.querySelector('[data-tab-panel="' + h + '"]')) show(h);
    else show("summary");
  }}
  syncFromHash();
  window.addEventListener("hashchange", syncFromHash);
}})();
  </script>
</body>
</html>"""
    doc = _report_head + report_css + _report_body
    out_path.write_text(doc, encoding="utf-8")


def render_slides(
    audit: dict[str, Any],
    overall: float,
    categories: list[AgentCategoryResult],
    comp_table: str,
    ga4_has_data: bool,
    out_path: Path,
) -> None:
    base = html.escape(audit.get("base_url") or "")
    slides: list[str] = []

    def _slide_grouped_insights(*, improvements: bool) -> str:
        chunks: list[str] = []
        for c in categories:
            g_good, g_bad = _insight_groups_from_category(c, audit)
            groups = g_bad if improvements else g_good
            if not groups:
                continue
            body = _insight_grouped_body_html(groups, empty_message="—")
            chunks.append(
                f'<div class="slide-insight-category"><h3>{html.escape(c.title)}</h3>{body}</div>'
            )
        if not chunks:
            return '<p class="slide-empty">—</p>'
        return f'<div class="slide-insights-inner">{"".join(chunks)}</div>'

    slides.append(
        f'<section class="slide active"><h1>GEO audit</h1><p class="big">{base}</p>'
        f'<p class="score">{format_report_score(overall)} / 100</p><p class="hint">← → or swipe</p></section>'
    )

    slides.append(
        '<section class="slide"><h2>Agent category scores</h2><ul>'
        + "".join(
            f"<li><strong>{html.escape(c.title)}</strong> ({c.weight:.0f}%): "
            f"{format_report_score(c.score)}/100</li>"
            for c in categories
        )
        + "</ul></section>"
    )

    sub_lines: list[str] = []
    for c in categories:
        for s in c.subs:
            sub_lines.append(
                f"<li>{html.escape(c.title)} · {html.escape(s.title)}: {s.score:.0f}</li>"
            )
    slides.append(
        '<section class="slide"><h2>Sub-areas</h2><ul>'
        + "".join(sub_lines or ["<li>—</li>"])
        + "</ul></section>"
    )

    slides.append(
        '<section class="slide"><h2>Strengths</h2><div class="slide-insights">'
        + _slide_grouped_insights(improvements=False)
        + "</div></section>"
    )
    slides.append(
        '<section class="slide"><h2>Improvements</h2><div class="slide-insights">'
        + _slide_grouped_insights(improvements=True)
        + "</div></section>"
    )

    slides.append(f'<section class="slide"><h2>Competitors</h2>{comp_table}</section>')

    ga4_hint = "GA4 appendix present in report.html." if ga4_has_data else "Add ga4_traffic.json for AI traffic charts."
    slides.append(
        f'<section class="slide"><h2>GA4</h2><p class="big">{html.escape(ga4_hint)}</p></section>'
    )

    slides.append(
        '<section class="slide"><h2>Next steps</h2><ul>'
        "<li>Whitelist AI crawlers; refine llms.txt + JSON-LD</li>"
        "<li>Strengthen off-site brand profiles and corroboration</li>"
        "<li>Re-run crawl after fixes</li>"
        "</ul></section>"
    )

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="color-scheme" content="light"/>
  <title>Slides — {base}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
  <link href="https://fonts.googleapis.com/css2?family=Literata:ital,opsz,wght@0,7..72,400;0,7..72,600;0,7..72,700;1,7..72,400&amp;family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&amp;display=swap" rel="stylesheet"/>
  <style>
    * {{ box-sizing: border-box; }}
    :root {{
      --ink: oklch(0.22 0.02 50);
      --ink-muted: oklch(0.45 0.02 50);
      --surface: oklch(0.99 0.004 85);
      --surface-2: oklch(0.96 0.008 80);
      --line: oklch(0.88 0.012 75);
      --accent: oklch(0.48 0.14 35);
      --accent-soft: oklch(0.94 0.03 55);
      font-family: "Source Sans 3", "Segoe UI", system-ui, sans-serif;
      font-size: 106%;
      line-height: 1.55;
      color: var(--ink);
      background: var(--surface);
    }}
    html, body {{ margin: 0; height: 100%; background: var(--surface); color: var(--ink); }}
    #deck {{ height: 100vh; overflow: hidden; position: relative; }}
    .slide {{
      display: none; padding: 4vh 6vw; height: 100vh; flex-direction: column; justify-content: center;
      max-width: 960px; margin: 0 auto;
    }}
    .slide.active {{ display: flex; }}
    h1 {{
      font-family: Literata, "Georgia", serif;
      font-size: clamp(1.65rem, 4vw, 2.15rem);
      margin: 0 0 1rem;
      font-weight: 700;
      letter-spacing: -0.02em;
      line-height: 1.2;
    }}
    h2 {{
      font-family: Literata, "Georgia", serif;
      font-size: clamp(1.2rem, 3vw, 1.65rem);
      color: var(--accent);
      font-weight: 600;
      margin: 0 0 0.75rem;
      letter-spacing: -0.015em;
    }}
    .big {{ font-size: clamp(1rem, 2.2vw, 1.25rem); color: var(--ink-muted); line-height: 1.5; }}
    .score {{
      font-family: Literata, "Georgia", serif;
      font-size: clamp(2.5rem, 8vw, 3.5rem);
      font-weight: 700;
      color: var(--accent);
      margin: 1rem 0;
      letter-spacing: -0.03em;
    }}
    .hint {{ color: var(--ink-muted); font-size: 0.88rem; margin-top: 1.5rem; }}
    .slide-insights-inner {{ overflow-y: auto; max-height: 72vh; text-align: left; width: 100%; }}
    .slide-insight-category h3 {{
      font-size: clamp(0.95rem, 2vw, 1.12rem);
      margin: 0.85rem 0 0.35rem;
      font-weight: 600;
      color: var(--accent);
    }}
    .slide-insight-category:first-child h3 {{ margin-top: 0; }}
    .slide-insights .insight-grouped {{ display: flex; flex-direction: column; gap: 0.5rem; }}
    .slide-insights .insight-group__title {{ font-weight: 600; font-size: 0.88rem; margin: 0.35rem 0 0.1rem; }}
    .slide-insights .insight-list--nested {{ margin: 0.1rem 0 0.35rem 1rem; font-size: 0.9rem; line-height: 1.45; }}
    .slide-empty {{ color: var(--ink-muted); margin: 0; }}
    ul {{ font-size: clamp(0.95rem, 2vw, 1.08rem); line-height: 1.55; margin: 0.35rem 0 0 1.15rem; }}
    table {{ width: 100%; font-size: 0.78rem; border-collapse: collapse; margin-top: 1rem; }}
    th, td {{ border-bottom: 1px solid var(--line); padding: 0.45rem 0.55rem; text-align: left; }}
    th {{
      background: var(--surface-2);
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      font-size: 0.68rem;
      color: var(--ink-muted);
    }}
    tbody tr:nth-child(even) {{ background: oklch(0.985 0.005 85); }}
    tr.primary {{ background: var(--accent-soft); }}
    nav {{ position: fixed; bottom: 1.25rem; right: 1.25rem; z-index: 10; }}
    nav button {{
      background: var(--accent-soft);
      color: var(--ink);
      border: 1px solid oklch(0.82 0.04 35);
      padding: 0.5rem 1rem;
      border-radius: 8px;
      cursor: pointer;
      margin-left: 0.45rem;
      font-family: inherit;
      font-size: 0.95rem;
      font-weight: 600;
    }}
    nav button:hover {{ border-color: oklch(0.55 0.12 35); background: oklch(0.92 0.035 50); }}
  </style>
</head>
<body class="geo-report">
  <div id="deck">{"".join(slides)}</div>
  <nav><button type="button" id="prev">←</button><button type="button" id="next">→</button></nav>
  <script>
    const slides = [...document.querySelectorAll('.slide')];
    let i = 0;
    function show(n) {{
      i = (n + slides.length) % slides.length;
      slides.forEach((s, j) => s.classList.toggle('active', j === i));
    }}
    document.getElementById('prev').onclick = () => show(i - 1);
    document.getElementById('next').onclick = () => show(i + 1);
    document.addEventListener('keydown', (e) => {{
      if (e.key === 'ArrowRight' || e.key === ' ') {{ e.preventDefault(); show(i + 1); }}
      if (e.key === 'ArrowLeft') {{ e.preventDefault(); show(i - 1); }}
    }});
  </script>
</body>
</html>"""
    out_path.write_text(doc, encoding="utf-8")


def generate_reports(
    audit_dir: Path,
    report_out: Path | None = None,
    *,
    industry: str = "",
    brand: str = "",
    ga4_property: str | None = None,
    ga4_ai_channels: str | None = None,
) -> int:
    """Build report.html and report_slides.html from a primary audit folder."""
    audit_dir = audit_dir.resolve()
    out_root = (report_out or audit_dir).resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    summary_path = audit_dir / "audit_summary.json"
    if not summary_path.is_file():
        print(f"Missing {summary_path}", file=sys.stderr)
        return 2

    gp = ((ga4_property or _ga4_property_from_env()) or "").strip() or None
    gc = ga4_ai_channels if ga4_ai_channels is not None else _ga4_channels_from_env()
    ga4_json = audit_dir / "ga4_traffic.json"
    if gp and not ga4_json.is_file():
        maybe_fetch_ga4_traffic(audit_dir, gp, gc)

    audit = _read_json(summary_path)
    if (industry or brand).strip():
        merged_in = dict(audit.get("audit_inputs") or {})
        if str(industry).strip():
            merged_in["industry"] = str(industry).strip()
        if str(brand).strip():
            merged_in["brand"] = str(brand).strip()
        audit = {**audit, "audit_inputs": merged_in}
        if str(brand).strip():
            bv = dict(audit.get("brand_visibility") or {})
            if not bv.get("skipped"):
                bv["brand_query"] = str(brand).strip()
                bv["platforms"] = []
                audit["brand_visibility"] = bv
    weights = dict(DEFAULT_WEIGHTS)
    wsum = sum(weights.values())
    if abs(wsum - 100.0) > 0.01:
        print("Warning: DEFAULT_WEIGHTS should sum to 100; normalizing.", file=sys.stderr)
        weights = {k: v * 100.0 / wsum for k, v in weights.items()}

    ensure_brand_visibility_on_audit(audit)
    overall, categories = score_audit(audit, weights)

    comp_path = audit_dir / "comparison.json"
    if not comp_path.is_file():
        comp_path = None
    cs, ci, ctable, cdetail = build_competitive_section(comp_path, audit.get("base_url") or "", weights)

    working = _consolidate_strength_lines(
        _unique_preserve([x for c in categories for x in c.strengths] + cs)
    )
    priorities_raw = _consolidate_improvement_lines(
        _unique_preserve([x for c in categories for x in c.improvements] + ci)
    )
    plan_quick, plan_medium, plan_strategic, plan_policy, priorities = prepare_report_priorities(
        priorities_raw
    )

    extra_dirs: tuple[Path, ...] = (out_root,) if out_root != audit_dir else ()
    ga4 = load_ga4_traffic(audit_dir, extra_search_dirs=extra_dirs or None)
    ga4_html = _ga4_section_html(ga4)

    rt = audit.get("robots_txt") or {}
    merged_p = Path(rt["merged_path"]) if rt.get("merged_path") else None
    jld_p = Path((audit.get("json_ld_txt") or {}).get("path") or "")
    llm_p = Path((audit.get("llms_txt") or {}).get("generated_path") or "")

    samples = {
        "robots.txt (merged suggestion)": _snippet_file(merged_p, 4500),
        "json-ld.txt (WebSite sample)": _snippet_file(jld_p if jld_p.is_file() else None, 3500),
        "llms.txt (generated)": _snippet_file(llm_p if llm_p.is_file() else None, 4500),
    }

    render_html(
        audit,
        overall,
        categories,
        working,
        priorities,
        ctable,
        cdetail,
        samples,
        ga4_html,
        ga4,
        audit_dir,
        out_root / "report.html",
        action_plan_phases=(plan_quick, plan_medium, plan_strategic, plan_policy),
    )
    render_slides(
        audit,
        overall,
        categories,
        ctable,
        bool(ga4 and _ga4_has_displayable_data(_ga4_apply_display_policy(dict(ga4)))),
        out_root / "report_slides.html",
    )

    print(f"Wrote {out_root / 'report.html'}")
    print(f"Wrote {out_root / 'report_slides.html'}")
    return 0


def _ga4_property_from_env() -> str | None:
    v = os.environ.get("GA4_PROPERTY_ID", "").strip()
    return v or None


def _ga4_channels_from_env() -> str | None:
    v = os.environ.get("GA4_AI_CHANNEL_NAMES", "").strip()
    return v or None


def _add_crawl_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--out",
        default="audit_output",
        help="Base directory for crawl output (default: audit_output)",
    )
    p.add_argument("--max-sitemap-urls", type=int, default=80, help="Max page URLs from sitemaps (default: 80)")
    p.add_argument(
        "--include-urls",
        default="",
        dest="include_urls_json",
        metavar="JSON",
        help="JSON array of page URLs to crawl instead of sitemap discovery. Passed to crawl-site.py.",
    )
    p.add_argument("--max-sitemaps", type=int, default=40, help="Max sitemap files when following indexes (default: 40)")
    p.add_argument("--delay", type=float, default=0.25, help="Seconds between HTTP requests (default: 0.25)")
    p.add_argument(
        "--mimic-urls-per-bot",
        type=int,
        default=4,
        help="URLs per bot for AI mimic fetch checks (ChatGPT + Gemini simulation, default: 4, max 6).",
    )
    p.add_argument(
        "--mimic-crawl-max-pages",
        type=int,
        default=12,
        help="Max pages per bot for simulated AI-platform crawl pass (default: 12, max 40).",
    )
    p.add_argument(
        "--mimic-crawl-depth",
        type=int,
        default=2,
        help="Max link depth for simulated AI-platform crawl pass (default: 2, max 4).",
    )
    p.add_argument(
        "--mimic-crawl-links-per-page",
        type=int,
        default=20,
        help="Max internal links queued per page in mimic crawl (default: 20, max 60).",
    )
    p.add_argument("--insecure", action="store_true", help="Do not verify TLS certificates")
    p.add_argument("--no-certifi", action="store_true", help="Do not use certifi CA bundle")
    p.add_argument(
        "--sample-robots",
        type=Path,
        default=_default_sample_robots(),
        help="Reference robots.txt for merge (default: assets/reference/robots.txt)",
    )
    p.add_argument(
        "--sample-llms",
        type=Path,
        default=_default_sample_llms(),
        help="Reference llms skeleton (default: assets/reference/llms-txt-skeleton.txt)",
    )
    p.add_argument(
        "--competitor",
        action="append",
        default=[],
        dest="competitors",
        metavar="URL",
        help="Competitor URL (max 10). Repeat flag. Passed to crawl-site.py.",
    )
    p.add_argument(
        "--brand",
        default=None,
        metavar="NAME",
        help="Brand name for off-site visibility scan. Passed to crawl-site.py (default: hostname guess).",
    )
    p.add_argument(
        "--no-brand-scan",
        action="store_true",
        help="Skip brand visibility probes in crawl-site.py.",
    )
    p.add_argument(
        "--industry",
        default="",
        metavar="LABEL",
        help="Industry vertical for executive summary & key findings context (stored in audit JSON).",
    )
    p.add_argument(
        "--market-country",
        default="",
        metavar="NAME",
        help="Primary market country name (wizard). Guides regional sitemap prioritisation in crawl-site.py.",
    )
    p.add_argument(
        "--market-country-code",
        default="",
        metavar="ISO2",
        help="Primary market ISO-3166-1 alpha-2 code (wizard). Guides regional sitemap prioritisation.",
    )
    p.add_argument(
        "--extra-markets-json",
        default="",
        metavar="JSON",
        help="JSON array of additional regional crawls, e.g. [{\"country\":\"France\",\"country_code\":\"FR\"}].",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run full GEO audit: crawl-site.py then HTML reports (or --only-report to rebuild reports).",
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Primary website URL (required unless --only-report)",
    )
    parser.add_argument(
        "--only-report",
        type=Path,
        default=None,
        metavar="AUDIT_DIR",
        help="Skip crawl; build report.html / report_slides.html from this primary audit folder",
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        help="Write HTML reports here (default: primary audit directory)",
    )
    parser.add_argument(
        "--ga4-property",
        default=None,
        metavar="PROPERTY_ID",
        help="GA4 property numeric ID: fetch ga4_traffic.json before building reports (same API as Analytics MCP). Env: GA4_PROPERTY_ID.",
    )
    parser.add_argument(
        "--ga4-ai-channels",
        default=None,
        metavar="NAMES",
        help=(
            "Comma-separated bucket labels counted as AI sessions; must match your GA4 **Admin custom channel group** "
            "for this property (the export resolves `sessionCustomChannelGroup:<id>` via the Metadata API). "
            "If omitted, the export uses **`sessionDefaultChannelGroup`** for session bucketing and infers the stacked "
            "AI-by-source chart from known AI referrer hosts only. Env: GA4_AI_CHANNEL_NAMES."
        ),
    )
    parser.add_argument(
        "--accept-ai-defaults",
        action="store_true",
        help=(
            "With no --competitor flags, ask Gemini for up to five competitor URLs. After reports are built, "
            "write onboarding_context.json (Gemini prompts + category labels) for Streamlit. Requires "
            "GEMINI_API_KEY / GOOGLE_API_KEY or Vertex (see competitor_suggest). Use --ga4-property to "
            "fetch ga4_traffic.json for AI traffic charts in the report (not for crawl URL selection)."
        ),
    )
    _add_crawl_arguments(parser)
    args = parser.parse_args()

    ga4_prop = (args.ga4_property or _ga4_property_from_env()) or None
    ga4_ch = args.ga4_ai_channels if args.ga4_ai_channels is not None else _ga4_channels_from_env()

    if args.only_report is not None:
        maybe_fetch_ga4_traffic(args.only_report.resolve(), ga4_prop, ga4_ch)
        return generate_reports(
            args.only_report,
            args.report_out,
            industry=getattr(args, "industry", "") or "",
            brand=getattr(args, "brand", "") or "",
            ga4_property=ga4_prop,
            ga4_ai_channels=ga4_ch,
        )

    if not args.url:
        parser.error("Provide primary url or --only-report AUDIT_DIR")

    _cli_apply_accept_ai_defaults(args)

    if len(args.competitors) > 10:
        print("Error: at most 10 --competitor URLs allowed.", file=sys.stderr)
        return 2

    if not CRAWL_SCRIPT.is_file():
        print(f"Missing crawler script: {CRAWL_SCRIPT}", file=sys.stderr)
        return 2

    try:
        primary_base_for_dir = normalize_base(args.url)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    audit_dir_for_market = Path(args.out).resolve() / safe_dir_name(primary_base_for_dir)
    _apply_market_from_onboarding(args, audit_dir_for_market)
    # Load user-selected crawl URLs from wizard (stored in onboarding_context.json)
    # when not already provided via --include-urls CLI arg.
    if not (getattr(args, "include_urls_json", "") or "").strip():
        ob_path = audit_dir_for_market / "onboarding_context.json"
        if ob_path.is_file():
            try:
                ob = json.loads(ob_path.read_text(encoding="utf-8"))
                wizard_urls = ob.get("crawl_urls") if isinstance(ob, dict) else None
                if isinstance(wizard_urls, list) and wizard_urls:
                    args.include_urls = [str(u) for u in wizard_urls if str(u).strip()]
                else:
                    args.include_urls = []
            except (OSError, json.JSONDecodeError):
                args.include_urls = []
    else:
        try:
            args.include_urls = json.loads(args.include_urls_json)
        except (json.JSONDecodeError, ValueError):
            args.include_urls = []
    mcc, mid = resolve_primary_market(
        str(getattr(args, "market_country", "") or ""),
        str(getattr(args, "market_country_code", "") or ""),
    )
    if mcc or mid:
        print(
            f"Sitemap prioritisation: primary market {mcc or '—'} ({mid or '—'})",
            file=sys.stderr,
        )

    crawl_cmd = [sys.executable, "-u", str(CRAWL_SCRIPT), *build_crawl_argv(args)]
    print("Running:", " ".join(crawl_cmd), file=sys.stderr)
    proc = subprocess.run(crawl_cmd, cwd=str(REPO_ROOT))
    if proc.returncode != 0:
        return proc.returncode

    try:
        primary_base = normalize_base(args.url)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    audit_dir = Path(args.out).resolve() / safe_dir_name(primary_base)
    maybe_fetch_ga4_traffic(audit_dir, ga4_prop, ga4_ch)
    rc = generate_reports(
        audit_dir,
        args.report_out,
        industry=getattr(args, "industry", "") or "",
        brand=getattr(args, "brand", "") or "",
        ga4_property=ga4_prop,
        ga4_ai_channels=ga4_ch,
    )
    _cli_write_onboarding_context(audit_dir, args)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
