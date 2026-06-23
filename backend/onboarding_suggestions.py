"""
Heuristic onboarding suggestions from GA4 top pages (paths + titles) plus optional Gemini competitors.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlparse

from geo_market import resolve_primary_market

CATEGORY_HINTS: dict[str, str] = {
    "battery": "Car batteries",
    "batteries": "Car batteries",
    "brake": "Brake parts",
    "brakes": "Brake parts",
    "wiper": "Wiper blades",
    "oil": "Engine oil and lubricants",
    "filter": "Car filters",
    "exhaust": "Exhaust parts",
    "bulb": "Car bulbs",
    "tyre": "Tyres",
    "tire": "Tyres",
    "tools": "Tools and workshop equipment",
    "accessories": "Car accessories",
    "paint": "Car paint and body repair",
    "clutch": "Clutch parts",
    "suspension": "Suspension parts",
}


def slug_to_label(slug: str) -> str:
    words = re.sub(r"[-_]+", " ", slug).strip()
    return " ".join(w.capitalize() for w in words.split()) if words else ""


def clean_product_title(title: str) -> str:
    if not title:
        return ""
    t = title.strip()
    for sep in ["|", "–", "—"]:
        if sep in t:
            t = t.split(sep, 1)[0]
    return t.strip()


def is_product_page(path: str) -> bool:
    path = (path or "").lower()
    return (
        "/p/" in path
        or "/product/" in path
        or bool(re.search(r"/[a-z0-9-]+-\d{5,}", path))
    )


def _hint_matches_haystack(key: str, haystack: str) -> bool:
    """Avoid ``oil`` matching ``foil`` / ``spoil``; substring match is fine for longer tokens."""
    if len(key) <= 4:
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", haystack, flags=re.IGNORECASE))
    return key in haystack


def infer_categories(
    top_pages: list[dict[str, Any]],
    max_items: int = 12,
    *,
    selected_industry: str = "",
) -> list[dict[str, Any]]:
    scores: Counter[str] = Counter()
    evidence: dict[str, list[str]] = defaultdict(list)
    ind_l = (selected_industry or "").lower()
    use_auto_hints = bool((selected_industry or "").strip()) and any(
        x in ind_l for x in ("auto", "vehicle", "motor")
    )

    for p in top_pages:
        path = (p.get("path") or "").lower()
        title = (p.get("title") or "").lower()
        views = int(p.get("pageviews") or 0)
        haystack = f"{path} {title}"

        if use_auto_hints:
            for key, label in CATEGORY_HINTS.items():
                if _hint_matches_haystack(key, haystack):
                    scores[label] += views or 1
                    if len(evidence[label]) < 3:
                        evidence[label].append(p.get("path") or "")

        parts = [x for x in path.split("/") if x]
        for part in parts[:2]:
            if part in {"p", "product", "products", "search", "basket", "checkout", "account", "cart"}:
                continue
            if len(part) < 3:
                continue
            label = slug_to_label(part)
            if not label:
                continue
            scores[label] += max(1, views // 5)
            if len(evidence[label]) < 3:
                evidence[label].append(p.get("path") or "")

    results: list[dict[str, Any]] = []
    for label, score in scores.most_common(max_items):
        results.append(
            {
                "label": label,
                "score": score,
                "evidence": evidence[label],
                "confidence": "high" if score > 100 else "medium",
            }
        )
    return results


def infer_products(top_pages: list[dict[str, Any]], max_items: int = 10) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    ordered = sorted(top_pages, key=lambda p: -int(p.get("pageviews") or 0))

    for p in ordered:
        path = p.get("path") or ""
        title = p.get("title") or ""
        if not is_product_page(path):
            continue

        label = clean_product_title(str(title))
        if not label or label.lower() == "(not set)":
            tail = path.rstrip("/").split("/")[-1]
            label = slug_to_label(tail) if tail else ""

        key = label.lower()
        if not key or key in seen:
            continue
        seen.add(key)

        products.append(
            {
                "label": label,
                "url_path": path,
                "pageviews": int(p.get("pageviews") or 0),
                "confidence": "medium",
            }
        )
        if len(products) >= max_items:
            break

    return products


def infer_primary_host(top_pages: list[dict[str, Any]]) -> str:
    if not top_pages:
        return ""
    by_host: Counter[str] = Counter()
    for p in top_pages:
        h = (p.get("host") or "").strip().lower()
        if not h or h in ("(not set)", "not set"):
            continue
        by_host[h] += int(p.get("pageviews") or 0)
    if not by_host:
        return ""
    host, _ = by_host.most_common(1)[0]
    return f"https://{host}/"


def infer_brand_name_from_pages(top_pages: list[dict[str, Any]], site_url: str) -> str:
    netloc = (urlparse(site_url).hostname or "").lower().replace("www.", "")
    if not netloc:
        return ""
    candidates: list[tuple[int, str]] = []
    for p in top_pages:
        h = (p.get("host") or "").strip().lower().replace("www.", "")
        if h != netloc:
            continue
        path = (p.get("path") or "").split("?")[0].rstrip("/") or "/"
        if path not in ("/", "/index.html", "/index.php", "/home"):
            continue
        t = clean_product_title(str(p.get("title") or ""))
        if t and t.lower() not in ("(not set)", "home", "welcome"):
            candidates.append((int(p.get("pageviews") or 0), t))
    if candidates:
        candidates.sort(key=lambda x: -x[0])
        return candidates[0][1]
    return slug_to_label(netloc.split(".")[0].replace("-", " "))


def infer_industry(categories: list[dict[str, Any]]) -> str:
    labels = " ".join(c.get("label", "").lower() for c in categories)
    auto_terms = ["car", "brake", "battery", "wiper", "engine", "vehicle", "tyre", "tire", "motor", "automotive"]
    if any(t in labels for t in auto_terms):
        return "Auto & Vehicles"
    return ""


def suggest_competitors_gemini(
    *,
    brand_name: str,
    site_url: str,
    selected_industry: str,
    market_country: str = "",
    market_country_code: str = "",
) -> list[dict[str, str]]:
    """Return up to five ``{"name", "url"}`` via :mod:`competitor_suggest` (Gemini)."""
    brand = (brand_name or "").strip()
    if not brand:
        brand = (urlparse(site_url).hostname or "").replace("www.", "") or "the business"
    try:
        from competitor_suggest import suggest_competitor_urls

        mk, mi = resolve_primary_market(market_country, market_country_code)
        urls = suggest_competitor_urls(
            brand,
            primary_url=site_url or "",
            industry=(selected_industry or "").strip(),
            max_suggestions=5,
            market_country=mk,
            market_country_code=mi,
        )
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for u in urls:
        host = (urlparse(u).hostname or "").replace("www.", "") or u
        out.append({"name": host or u, "url": u})
    return out


def build_onboarding_suggestions(
    top_pages: list[dict[str, Any]],
    *,
    selected_industry: str = "",
    skip_gemini_competitors: bool = False,
    market_country: str = "",
    market_country_code: str = "",
) -> dict[str, Any]:
    site_url = infer_primary_host(top_pages)
    brand_name = infer_brand_name_from_pages(top_pages, site_url)
    ind_sel = (selected_industry or "").strip()
    categories = infer_categories(top_pages, selected_industry=ind_sel)
    products = infer_products(top_pages)

    suggested_industry = ind_sel or infer_industry(categories)

    competitors: list[dict[str, str]] = []
    if not skip_gemini_competitors and site_url:
        competitors = suggest_competitors_gemini(
            brand_name=brand_name,
            site_url=site_url,
            selected_industry=suggested_industry,
            market_country=market_country,
            market_country_code=market_country_code,
        )

    return {
        "suggested_site_url": site_url,
        "suggested_brand_name": brand_name,
        "suggested_industry": suggested_industry,
        "categories": categories,
        "products": products,
        "competitors": competitors,
        "top_pages": top_pages,
    }
