"""Page-level HTML builders that mirror React report sections for PDF/HTML export."""

from __future__ import annotations

import html as html_lib
import json
import math
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from api.report_score import format_report_score

ESC = html_lib.escape

PLATFORM_LABELS = {
    "gemini": "Gemini",
    "openai": "ChatGPT",
    "claude": "Claude",
    "google_aio": "Google AIO",
}
PLATFORM_COLORS = {
    "gemini": "#4285F4",
    "openai": "#10a37f",
    "claude": "#D97706",
    "google_aio": "#EA4335",
}
PRIMARY_PLATFORMS = ("gemini", "openai", "google_aio", "claude")

# Mirror web/src/lib/vendorDomains.ts — citations exclude retailer/vendor domains.
_VENDOR_DOMAINS = frozenset({
    "boots.com", "superdrug.com", "lookfantastic.com", "cultbeauty.co.uk",
    "spacenk.com", "beautybay.com", "feelunique.com", "allbeauty.com", "pharmaca.com",
    "johnlewis.com", "marksandspencer.com", "selfridges.com", "harrods.com",
    "libertylondon.com", "debenhams.com", "next.co.uk", "nextdirect.com",
    "tkmaxx.com", "tkmaxx.co.uk", "hmv.com",
    "tesco.com", "sainsburys.co.uk", "asda.com", "waitrose.com", "ocado.com",
    "morrisons.com", "aldi.co.uk", "lidl.co.uk", "iceland.co.uk",
    "asos.com", "asos.co.uk", "zalando.co.uk", "zalando.com", "farfetch.com",
    "net-a-porter.com", "matchesfashion.com", "notonthehighstreet.com", "etsy.com",
    "sephora.com", "ulta.com", "cvs.com", "walgreens.com", "target.com",
    "walmart.com", "costco.com",
    "amazon.com", "amazon.co.uk", "amazon.ca", "amazon.com.au", "amazon.de", "amazon.fr",
    "macys.com", "nordstrom.com", "bloomingdales.com", "kohls.com", "jcpenney.com",
    "ebay.com", "ebay.co.uk",
    "tripadvisor.com", "opentable.com", "bookatable.co.uk", "booking.com",
    "hotels.com", "airbnb.com", "expedia.com",
    "deliveroo.co.uk", "ubereats.com", "just-eat.co.uk", "doordash.com",
    "hollandandbarrett.com", "chemistdirect.co.uk", "pharmacy2u.co.uk", "nhs.uk",
    "diy.com", "screwfix.com", "homebase.co.uk", "wickes.co.uk", "dunelm.com",
    "argos.co.uk", "ikea.com",
})

_MULTI_PART_PUBLIC_SUFFIXES = frozenset({
    "co.uk", "org.uk", "gov.uk", "ac.uk",
    "com.au", "net.au", "org.au",
    "co.nz", "co.za", "co.in", "co.jp",
    "com.br", "com.sg", "com.hk", "com.mx",
})


def _shell(title: str, subtitle: str, body: str) -> str:
    from api.export_builders import _shell as shared_shell

    return shared_shell(title, subtitle, body)


def _score_tone_color(score: float) -> tuple[str, str]:
    from api.export_builders import _score_tone_color as shared

    return shared(score)


def _is_ok_or_below(score: float) -> bool:
    from api.export_builders import _is_ok_or_below as shared

    return shared(score)


def _prepare_recommendation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from api.export_builders import _prepare_recommendation_items as shared

    return shared(items)


def _good_score_min() -> int:
    from api.export_builders import GOOD_SCORE_MIN

    return GOOD_SCORE_MIN


def _svg_score_gauge(score: float, *, size: int = 96, decimals: int = 0) -> str:
    from api.export_builders import _svg_score_gauge as shared

    return shared(score, size=size, decimals=decimals)


def _pct_class(val: float) -> str:
    from api.export_builders import _pct_class as shared

    return shared(val)


def _load_scores(audit_dir: Path) -> dict[str, Any]:
    from api import geo_services as geo

    try:
        return geo.load_integrated_scores(audit_dir) or {}
    except Exception:
        return {}


def _load_probe_ctx(audit_dir: Path) -> dict[str, Any]:
    from api.prompt_performance import _build_context_response

    try:
        return _build_context_response(audit_dir) or {}
    except Exception:
        return {}


def _concise(text: str, max_length: int = 125) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip()).rstrip(".;:")
    if len(cleaned) <= max_length:
        return cleaned
    return f"{cleaned[: max_length - 1].rstrip()}…"


def _pillar_finding_summary(components: list[dict[str, Any]], fallback: str) -> str:
    if not components:
        return fallback
    ranked = sorted(components, key=lambda c: float(c.get("score") or 0))
    weakest = ranked[0]
    strongest = ranked[-1]
    strength = ""
    strengths = strongest.get("strengths") or []
    if strengths:
        strength = str(strengths[0])
    elif float(strongest.get("score") or 0) >= 60:
        strength = str(strongest.get("finding_summary") or "")
    improvement = ""
    improvements = weakest.get("improvements") or []
    if improvements:
        improvement = str(improvements[0])
    elif float(weakest.get("score") or 0) < 75:
        improvement = str(weakest.get("finding_summary") or "")
    parts: list[str] = []
    if strength:
        parts.append(f"{strongest.get('title')}: {_concise(strength)}")
    if improvement and (weakest.get("key") != strongest.get("key") or not strength):
        parts.append(f"{weakest.get('title')} needs work: {_concise(improvement)}")
    return f"{'. '.join(parts)}." if parts else fallback


def _finding_list(items: list[str], *, empty: str, positive: bool = True) -> str:
    if not items:
        return f"<p class='muted' style='font-style:italic'>{ESC(empty)}</p>"
    icon = "✓" if positive else "!"
    color = "#00b894" if positive else "#e17055"
    lis = "".join(
        f"<li style='display:flex;gap:8px;margin:0 0 8px;font-size:12px;line-height:1.45;color:#4b5563'>"
        f"<span style='color:{color};font-weight:700'>{icon}</span><span>{ESC(item)}</span></li>"
        for item in items
        if str(item).strip()
    )
    return f"<ul style='margin:0;padding:0;list-style:none'>{lis}</ul>"


def _subsection(title: str, body: str) -> str:
    return (
        f"<div style='padding:16px 24px 8px'>"
        f"<h3 style='margin:0 0 10px;font-size:14px;font-weight:700;color:#0d0d0d'>{ESC(title)}</h3>"
        f"{body}</div>"
    )


def _score_pill(score: float) -> str:
    _, color = _score_tone_color(score)
    return (
        f"<span style='display:inline-flex;min-width:64px;justify-content:center;"
        f"border-radius:999px;padding:4px 10px;font-size:13px;font-weight:700;"
        f"background:{color}18;color:{color}'>{format_report_score(score)}/100</span>"
    )


# ── Shared visibility / citation helpers ───────────────────────────────────────


def _website_host(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        host = urlparse(raw if "://" in raw else f"https://{raw}").hostname or ""
    except Exception:
        return ""
    host = host.lower()
    return host[4:] if host.startswith("www.") else host


def _registrable_domain(raw_domain: str) -> str:
    hostname = _website_host(raw_domain) or str(raw_domain or "").replace("https://", "").replace("http://", "")
    hostname = hostname.split("/")[0].lower().removeprefix("www.")
    parts = [p for p in hostname.split(".") if p]
    if len(parts) <= 2:
        return hostname
    suffix = ".".join(parts[-2:])
    return ".".join(parts[-3:]) if suffix in _MULTI_PART_PUBLIC_SUFFIXES else ".".join(parts[-2:])


def _has_page_path(raw_url: str) -> bool:
    try:
        url = urlparse(raw_url if "://" in raw_url else f"https://{raw_url}")
        return bool(url.path.strip("/"))
    except Exception:
        return False


def _is_vendor_domain(domain: str) -> bool:
    d = str(domain or "").lower().removeprefix("www.")
    if not d:
        return False
    if d in _VENDOR_DOMAINS:
        return True
    return any(d.endswith(f".{vendor}") for vendor in _VENDOR_DOMAINS)


def _citation_stem(value: str) -> str:
    from api.geo_services import _stem_brand_label

    return _stem_brand_label(value)


def _information_source_domains(
    audit_dir: Path,
    ctx: dict[str, Any],
) -> Any:
    """Return predicate matching web/src/lib/citationSource.ts information sources."""
    return _citation_domain_predicate(audit_dir, ctx, exclude_competitors=True)


def _citation_eligible_domains(
    audit_dir: Path,
    ctx: dict[str, Any],
) -> Any:
    """Return predicate matching web/src/lib/citationSource.ts citation list eligibility.

    Competitors are allowed when they appear as real cited sources (not force-injected).
    """
    return _citation_domain_predicate(audit_dir, ctx, exclude_competitors=False)


def _citation_domain_predicate(
    audit_dir: Path,
    ctx: dict[str, Any],
    *,
    exclude_competitors: bool,
) -> Any:
    live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else {}
    brand_domains: set[str] = set()
    brand_stems: set[str] = set()
    competitor_domains: set[str] = set()
    competitor_stems: set[str] = set()

    def add_entity(
        domains: set[str],
        stems: set[str],
        name: str = "",
        website: str = "",
    ) -> None:
        domain = _website_host(website)
        name_stem = _citation_stem(name)
        domain_stem = _citation_stem(domain)
        if domain:
            domains.add(domain)
        if name_stem:
            stems.add(name_stem)
        if domain_stem:
            stems.add(domain_stem)

    add_entity(
        brand_domains,
        brand_stems,
        str(ctx.get("brand_name") or live.get("brand_name") or ""),
        str(ctx.get("brand_site_url") or ""),
    )
    for row in ctx.get("competitors") or []:
        if isinstance(row, dict):
            add_entity(
                competitor_domains,
                competitor_stems,
                str(row.get("competitor_brand") or ""),
                str(row.get("competitor_website") or ""),
            )
    for row in live.get("reply_detected_brands") or []:
        if isinstance(row, dict):
            add_entity(
                competitor_domains,
                competitor_stems,
                str(row.get("brand_name") or ""),
                str(row.get("website_url") or ""),
            )
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            onboarding = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            onboarding = {}
        if isinstance(onboarding, dict):
            add_entity(
                brand_domains,
                brand_stems,
                str(onboarding.get("brand_name_used") or ""),
                str(
                    onboarding.get("brand_website_used")
                    or onboarding.get("brand_url")
                    or onboarding.get("brand_site_url")
                    or ""
                ),
            )
            for row in onboarding.get("competitors_detail") or onboarding.get("competitor_context") or []:
                if isinstance(row, dict):
                    add_entity(
                        competitor_domains,
                        competitor_stems,
                        str(row.get("competitor_brand") or ""),
                        str(row.get("competitor_website") or ""),
                    )

    def is_eligible(raw_domain: str) -> bool:
        domain = str(raw_domain or "").lower().removeprefix("www.")
        if not domain or _is_vendor_domain(domain):
            return False
        stem = _citation_stem(domain)
        if domain in brand_domains or stem in brand_stems:
            return False
        if exclude_competitors and (domain in competitor_domains or stem in competitor_stems):
            return False
        return True

    return is_eligible


def _aggregate_root_domains(sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for site in sites:
        domain = _registrable_domain(str(site.get("domain") or ""))
        if not domain:
            continue
        existing = grouped.get(domain)
        if not existing:
            grouped[domain] = {
                **site,
                "domain": domain,
                "platforms": list(site.get("platforms") or []),
                "competitor_names": list(site.get("competitor_names") or []),
            }
            continue
        existing["count"] = int(existing.get("count") or 0) + int(site.get("count") or site.get("citations") or 0)
        plats = list(existing.get("platforms") or [])
        for p in site.get("platforms") or []:
            if p not in plats:
                plats.append(p)
        existing["platforms"] = plats
        existing["brand_mentioned"] = bool(existing.get("brand_mentioned") or site.get("brand_mentioned"))
        names = list(existing.get("competitor_names") or [])
        for n in site.get("competitor_names") or []:
            if n not in names:
                names.append(n)
        existing["competitor_names"] = names
    return sorted(grouped.values(), key=lambda s: -int(s.get("count") or 0))


def _load_probe_history_entries(audit_dir: Path) -> list[dict[str, Any]]:
    """Local probe-history entries (same source as /probe-history API)."""
    from api.probe_history import _LIVE_FILE, _build_daily_summary, _load_index, _read_json

    index = _load_index(audit_dir)
    entries = [e for e in (index.get("entries") or []) if isinstance(e, dict)]
    if entries:
        return sorted(entries, key=lambda e: str(e.get("date") or ""))
    live_path = audit_dir / _LIVE_FILE
    if not live_path.is_file():
        return []
    live = _read_json(live_path)
    data = live.get("live_probe", live) if isinstance(live, dict) else {}
    summary = _build_daily_summary(data if isinstance(data, dict) else {})
    audit_summary = _read_json(audit_dir / "audit_summary.json")
    created = str(audit_summary.get("created_at") or "").split("T")[0] or "today"
    return [{"date": created, "summary": summary}]


def _load_citation_history_rows(audit_dir: Path) -> list[dict[str, Any]]:
    entries = _load_probe_history_entries(audit_dir)
    rows: list[dict[str, Any]] = []
    for entry in entries:
        date = str(entry.get("date") or "")
        for dom in (entry.get("summary") or {}).get("top_cited_domains") or []:
            if not isinstance(dom, dict):
                continue
            rows.append({
                "date": date,
                "domain": str(dom.get("domain") or ""),
                "frequency": int(dom.get("frequency") or dom.get("count") or 0),
            })
    return rows


def _load_cached_ai_sentiment(audit_dir: Path) -> dict[str, Any] | None:
    """Cached LLM sentiment only — never trigger generation during export."""
    try:
        from backend.insights_llm import load_cached_sentiment
    except Exception:
        return None
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    cached = load_cached_sentiment(audit_dir, probe_path)
    if not cached or not isinstance(cached.get("sentiment"), dict):
        return None
    return cached["sentiment"]


def _svg_line_chart(
    points: list[dict[str, Any]],
    series: list[tuple[str, str, str]],
    *,
    aria_label: str,
    y_is_pct: bool = True,
) -> str:
    """Static multi-series SVG line chart (same pattern as Estimated AI impact)."""
    usable = [p for p in points if isinstance(p, dict) and p.get("date")]
    if len(usable) < 1 or not series:
        return ""
    width, height = 720, 240
    pad_l, pad_r, pad_t, pad_b = 44, 16, 16, 40
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    values: list[float] = []
    for point in usable:
        for key, _color, _label in series:
            try:
                val = point.get(key)
                if val is None:
                    continue
                values.append(float(val))
            except (TypeError, ValueError):
                pass
    if not values:
        return ""
    vmin = 0.0 if y_is_pct else min(values)
    vmax = 100.0 if y_is_pct else max(values)
    if vmax <= vmin:
        vmax = vmin + 1

    def x_at(i: int) -> float:
        return pad_l + (plot_w * i / max(len(usable) - 1, 1))

    def y_at(v: float) -> float:
        return pad_t + plot_h * (1 - (v - vmin) / (vmax - vmin))

    paths: list[str] = []
    for key, color, _label in series:
        pts: list[str] = []
        for i, point in enumerate(usable):
            raw = point.get(key)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            pts.append(f"{x_at(i):.1f},{y_at(val):.1f}")
        if len(pts) >= 2:
            paths.append(
                f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{' '.join(pts)}'/>"
            )
        elif len(pts) == 1:
            x, y = pts[0].split(",")
            paths.append(f"<circle cx='{x}' cy='{y}' r='3.5' fill='{color}'/>")
    if not paths:
        return ""
    legend: list[str] = []
    lx = pad_l
    for _key, color, label in series:
        legend.append(
            f"<rect x='{lx}' y='{height - 16}' width='9' height='9' fill='{color}'/>"
            f"<text x='{lx + 12}' y='{height - 8}' font-size='10' fill='#4b5563'>{ESC(label)}</text>"
        )
        lx += 12 + len(label) * 6.0 + 14
    first = str(usable[0].get("date") or "")
    last = str(usable[-1].get("date") or "")
    note = ""
    if len(usable) < 2:
        note = (
            f"<text x='{pad_l}' y='{pad_t + 14}' font-size='10' fill='#d97706'>"
            "Single data point — daily re-runs will add trend points.</text>"
        )
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        f"xmlns='http://www.w3.org/2000/svg' role='img' aria-label='{ESC(aria_label)}'>"
        f"<rect x='{pad_l}' y='{pad_t}' width='{plot_w}' height='{plot_h}' fill='#fafafa' stroke='#e5e7eb'/>"
        + note
        + "".join(paths)
        + f"<text x='{pad_l}' y='{height - 22}' font-size='10' fill='#9ca3af'>{ESC(first)}</text>"
        f"<text x='{width - pad_r}' y='{height - 22}' font-size='10' fill='#9ca3af' text-anchor='end'>{ESC(last)}</text>"
        + "".join(legend)
        + "</svg>"
    )


def _time_series_table(points: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not points or not columns:
        return "<p class='muted'>No history points yet.</p>"
    head = "".join(f"<th style='text-align:right'>{ESC(label)}</th>" for _key, label in columns)
    body = []
    for point in points:
        cells = []
        for key, _label in columns:
            val = point.get(key)
            if val is None:
                cells.append("<td style='text-align:right' class='muted'>—</td>")
            else:
                cells.append(f"<td style='text-align:right'>{ESC(str(val))}%</td>")
        body.append(f"<tr><td>{ESC(str(point.get('date') or ''))}</td>{''.join(cells)}</tr>")
    return (
        "<table class='export-table'><thead><tr><th>Date</th>"
        + head
        + "</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table>"
    )


# ── Competitor visibility (Visibility / SOV) ───────────────────────────────────


def _brand_rows_from_probe(ctx: dict[str, Any], audit_dir: Path | None = None) -> list[dict[str, Any]]:
    """Brand/competitor rows matching UI website-backed raw SOV (brandVisibilityRows.ts)."""
    from api.geo_services import accumulate_brand_visibility_rows

    return accumulate_brand_visibility_rows(ctx, audit_dir)


def _competitor_visibility_display_rows(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    own = next((r for r in rows if r["is_own"]), None)
    competitors = [
        r for r in rows if not r["is_own"] and r["mentions"] > 0
    ]
    # Match BrandCompetitorVisibility topComparisonRows ranking.
    competitors.sort(key=lambda r: (-int(r["mentions"]), str(r["name"]).lower()))
    competitors = competitors[:limit]
    display = ([own] if own else []) + competitors
    display.sort(key=lambda r: (-int(r["mentions"]), str(r["name"]).lower()))
    return display


def _brand_competitor_over_time_chart(
    audit_dir: Path,
    display_rows: list[dict[str, Any]],
) -> str:
    entries = _load_probe_history_entries(audit_dir)
    if not entries or not display_rows:
        return ""
    colors = [
        "#047857", "#2563EB", "#7C3AED", "#C2410C", "#0E7490",
        "#A21CAF", "#4D7C0F", "#B45309", "#475569", "#BE123C", "#0369A1",
    ]
    series_defs: list[tuple[str, str, str]] = []
    points: list[dict[str, Any]] = []
    for i, row in enumerate(display_rows[:8]):
        series_defs.append((f"s{i}", colors[i % len(colors)], str(row["name"])))

    from api.geo_services import _stem_brand_label

    for entry in entries:
        point: dict[str, Any] = {"date": entry.get("date")}
        summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
        platform_rows = [
            summary[pk] for pk in PRIMARY_PLATFORMS
            if isinstance(summary.get(pk), dict) and int((summary.get(pk) or {}).get("response_count") or 0) > 0
        ]
        total_responses = sum(int(s.get("response_count") or 0) for s in platform_rows)
        for i, row in enumerate(display_rows[:8]):
            if total_responses <= 0:
                point[f"s{i}"] = 0
                continue
            weighted = 0.0
            target = _stem_brand_label(str(row["name"]))
            for plat_summary in platform_rows:
                responses = int(plat_summary.get("response_count") or 0)
                if row["is_own"]:
                    vis = float(plat_summary.get("brand_visibility") or 0)
                else:
                    vis_map = plat_summary.get("competitor_visibility") or {}
                    matches = [
                        float(v) for name, v in vis_map.items()
                        if _stem_brand_label(str(name)) == target
                    ]
                    vis = max(matches) if matches else 0.0
                weighted += vis * responses
            point[f"s{i}"] = round(100.0 * weighted / total_responses)
        points.append(point)

    chart = _svg_line_chart(points, series_defs, aria_label="Brand and competitor visibility over time")
    if chart:
        return chart
    # Structured substitute when SVG cannot be built.
    cols = [(f"s{i}", str(row["name"])) for i, row in enumerate(display_rows[:8])]
    return _time_series_table(points, cols)


def build_competitor_visibility_export(audit_dir: Path) -> str:
    ctx = _load_probe_ctx(audit_dir)
    rows = _brand_rows_from_probe(ctx, audit_dir)
    if not rows or not any(r["total_responses"] for r in rows):
        return _shell(
            "Competitor visibility",
            "Compare your brand with competitors found in AI responses.",
            "<div class='export-empty'>No probe data yet.</div>",
        )
    display = _competitor_visibility_display_rows(rows, limit=10)

    body_rows = []
    for row in display:
        you = " <span class='topic-count'>Your Brand</span>" if row["is_own"] else ""
        body_rows.append(
            "<tr>"
            f"<td>{ESC(str(row['name']))}{you}</td>"
            f"<td class='{_pct_class(row['visibility'])}' style='text-align:right'>{row['visibility']:.1f}%</td>"
            f"<td class='{_pct_class(row['sov'])}' style='text-align:right'>{row['sov']:.1f}%</td>"
            "</tr>"
        )
    table = (
        "<table class='export-table'><thead><tr>"
        "<th>Brand</th>"
        "<th style='text-align:right'>Visibility</th>"
        "<th style='text-align:right'>SOV</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
        "<p class='muted' style='padding:12px 24px 8px'>"
        "Visibility = responses mentioning the brand ÷ all analysed responses. "
        "SOV = brand signal hits ÷ total brand + website-backed competitor hits."
        "</p>"
    )
    over_time = _brand_competitor_over_time_chart(audit_dir, display)
    parts = [table]
    if over_time:
        parts.append(_subsection("Brand & competitor visibility over time", over_time))
    return _shell(
        "Competitor visibility",
        "Compare your brand with the 10 most visible competitors found in AI responses.",
        "".join(parts),
    )


# ── AI visibility overview ─────────────────────────────────────────────────────


def build_ai_visibility_overview_export(audit_dir: Path) -> str:
    from api.export_builders import _brand_mentioned
    from api.geo_services import load_prompt_visibility_metrics
    from api.probe_history import _response_sentiment as _windowed_sentiment

    scores = _load_scores(audit_dir)
    ctx = _load_probe_ctx(audit_dir)
    live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else {}
    metrics = load_prompt_visibility_metrics(audit_dir) or {}
    if not metrics:
        metrics = scores.get("prompt_metrics") if isinstance(scores.get("prompt_metrics"), dict) else {}
    per_platform = metrics.get("per_platform") if isinstance(metrics.get("per_platform"), dict) else {}
    per_prompt = [p for p in (live.get("per_prompt") or []) if isinstance(p, dict)]
    brand = str(ctx.get("brand_name") or live.get("brand_name") or "Brand")
    tokens = [str(t) for t in (live.get("brand_match_tokens") or []) if str(t).strip()]
    if brand and brand.lower() not in {t.lower() for t in tokens}:
        tokens = [brand, *tokens]

    brand_rows = _brand_rows_from_probe(ctx, audit_dir) if per_prompt else []
    own_row = next((r for r in brand_rows if r["is_own"]), None)

    visibility_pct = metrics.get("visibility_pct")
    sov_pct = metrics.get("sov_pct")
    if own_row is not None:
        visibility_pct = own_row["visibility"]
        sov_pct = own_row["sov"]

    ai = metrics.get("score")
    if ai is None:
        ai = scores.get("ai_visibility")

    parts: list[str] = []
    if ai is not None:
        try:
            ai_f = float(ai)
            tone, color = _score_tone_color(ai_f)
            vis_txt = f"{float(visibility_pct):.1f}%" if isinstance(visibility_pct, (int, float)) else "—"
            sov_txt = f"{float(sov_pct):.1f}%" if isinstance(sov_pct, (int, float)) else "—"
            parts.append(
                "<div class='summary-hero'>"
                f"<div class='summary-hero-score'>{_svg_score_gauge(ai_f, size=88)}"
                f"<div class='summary-hero-copy'>"
                f"<p class='eyebrow'>AI Visibility Score</p>"
                f"<p class='score-num'>{ai_f:.0f}<span> /100</span></p>"
                f"<p class='score-label' style='color:{color}'>{ESC(tone)}</p>"
                f"<p class='muted' style='margin:8px 0 0'>{ESC(brand)} appears in {ESC(vis_txt)} of analysed "
                f"platform responses and has {ESC(sov_txt)} raw share of voice.</p>"
                f"<p class='muted' style='margin:4px 0 0'>60% response-level visibility + 40% share-of-voice "
                f"performance against the 10 most-mentioned competitors.</p>"
                f"</div></div></div>"
            )
        except (TypeError, ValueError):
            pass

    # Position + keyword positive-rate (Overview scorecards; distinct from Gemini AI Sentiment)
    avg_pos = None
    pos_n = 0
    pos_sum = 0.0
    pos_count = 0
    neg_count = 0
    mentioned = 0
    token_list = tokens or [brand]
    lower_tokens = [str(t).lower() for t in token_list if t]

    for row in per_prompt:
        for pk in PRIMARY_PLATFORMS:
            runs = (row.get("runs") or {}).get(pk) if isinstance(row.get("runs"), dict) else None
            completed: list[dict[str, Any]] = []
            if isinstance(runs, list):
                completed = [
                    r for r in runs
                    if isinstance(r, dict) and r.get("response") and not r.get("error")
                ]
            if not completed:
                resp = str(row.get(f"{pk}_response") or "")
                err = str(row.get(f"error_{pk}") or "")
                if resp and not err:
                    completed = [{"response": resp}]
            for run in completed:
                resp = str(run.get("response") or "")
                if not resp or not lower_tokens:
                    continue
                lower = resp.lower()
                if not any(tok in lower for tok in lower_tokens):
                    continue
                mentioned += 1
                hit_idx = -1
                for tok in lower_tokens:
                    idx = lower.find(tok)
                    if idx >= 0:
                        hit_idx = idx
                        break
                if hit_idx >= 0:
                    pos_sum += (hit_idx / max(len(resp), 1)) * 10 + 1
                    pos_n += 1
                sent = _windowed_sentiment(resp, lower_tokens)
                if sent == "positive":
                    pos_count += 1
                elif sent == "negative":
                    neg_count += 1
    if pos_n:
        avg_pos = pos_sum / pos_n
    sentiment_pct = round(100.0 * pos_count / mentioned) if mentioned else None

    cards = []
    if isinstance(visibility_pct, (int, float)):
        cards.append(
            f"<div class='score-card'><div class='label'>Visibility</div>"
            f"<div class='value'>{float(visibility_pct):.1f}%</div>"
            f"<div class='sub'>Responses mentioning your brand</div></div>"
        )
    if isinstance(sov_pct, (int, float)):
        cards.append(
            f"<div class='score-card'><div class='label'>SOV</div>"
            f"<div class='value'>{float(sov_pct):.1f}%</div>"
            f"<div class='sub'>Brand hits ÷ brand + website-backed competitor hits</div></div>"
        )
    pos_value = f"{avg_pos:.1f}" if avg_pos is not None else "—"
    cards.append(
        f"<div class='score-card'><div class='label'>Position</div>"
        f"<div class='value'>{ESC(pos_value)}</div>"
        f"<div class='sub'>Avg mention position (lower = earlier)</div></div>"
    )
    if sentiment_pct is not None:
        cards.append(
            f"<div class='score-card'><div class='label'>Positive rate</div>"
            f"<div class='value'>{sentiment_pct}%</div>"
            f"<div class='sub'>{pos_count}/{mentioned} keyword-positive mentions</div></div>"
        )
    else:
        cards.append(
            "<div class='score-card'><div class='label'>Positive rate</div>"
            "<div class='value'>—</div>"
            "<div class='sub'>No brand mentions yet</div></div>"
        )
    parts.append(f"<div class='score-grid'>{''.join(cards)}</div>")

    # Brand visibility by platform
    bars = []
    for pk in PRIMARY_PLATFORMS:
        entry = per_platform.get(pk) if isinstance(per_platform.get(pk), dict) else {}
        if not entry or not entry.get("response_count"):
            continue
        vis = float(entry.get("visibility_pct") or 0)
        sov = float(entry.get("sov_pct") or 0)
        color = PLATFORM_COLORS.get(pk, "#4B5563")
        bars.append(
            f"<div class='bar-row'><div class='bar-label'>"
            f"<span>{ESC(PLATFORM_LABELS.get(pk, pk))}</span>"
            f"<span class='muted'>{vis:.0f}% visibility · {sov:.0f}% SOV</span></div>"
            f"<div class='bar-track'><div class='bar-fill' style='width:{min(vis,100)}%;background:{color}'></div></div>"
            f"</div>"
        )
    if bars:
        parts.append(_subsection("Brand visibility by platform", "".join(bars)))

    # Visibility over time
    history = _load_probe_history_entries(audit_dir)
    if history:
        vis_points: list[dict[str, Any]] = []
        vis_series: list[tuple[str, str, str]] = []
        for pk in PRIMARY_PLATFORMS:
            if any(isinstance((e.get("summary") or {}).get(pk), dict) for e in history):
                vis_series.append((f"{pk}_brand", PLATFORM_COLORS.get(pk, "#4B5563"), PLATFORM_LABELS.get(pk, pk)))
        for entry in history:
            point: dict[str, Any] = {"date": entry.get("date")}
            summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
            for pk in PRIMARY_PLATFORMS:
                plat = summary.get(pk) if isinstance(summary.get(pk), dict) else None
                if plat:
                    point[f"{pk}_brand"] = round(float(plat.get("brand_visibility") or 0) * 100)
            vis_points.append(point)
        chart = _svg_line_chart(vis_points, vis_series, aria_label="Visibility over time")
        body = chart or _time_series_table(vis_points, [(key, label) for key, _color, label in vis_series])
        if body:
            parts.append(_subsection("Visibility over time", body))

    # AI Sentiment (cached LLM summary)
    sentiment = _load_cached_ai_sentiment(audit_dir)
    if sentiment:
        overall = str(sentiment.get("overall_sentiment") or "Neutral")
        summary_txt = str(sentiment.get("overall_summary") or "")
        cat_rows = []
        for cat in sentiment.get("by_category") or []:
            if not isinstance(cat, dict):
                continue
            cat_rows.append(
                f"<tr><td>{ESC(str(cat.get('category') or ''))}</td>"
                f"<td>{ESC(str(cat.get('sentiment') or ''))}</td></tr>"
            )
        sent_body = (
            f"<p style='margin:0 0 8px'><strong>{ESC(overall)}</strong></p>"
            f"<p class='muted' style='margin:0 0 12px'>{ESC(summary_txt)}</p>"
        )
        if cat_rows:
            sent_body += (
                "<table class='export-table'><thead><tr><th>Category</th><th>Sentiment</th></tr></thead>"
                f"<tbody>{''.join(cat_rows)}</tbody></table>"
            )
        parts.append(_subsection("AI Sentiment", sent_body))

    # Sentiment over time
    if history:
        sent_points: list[dict[str, Any]] = []
        sent_series: list[tuple[str, str, str]] = []
        for pk in PRIMARY_PLATFORMS:
            if any(
                isinstance((e.get("summary") or {}).get(pk), dict)
                and (e.get("summary") or {}).get(pk, {}).get("sentiment_score") is not None
                for e in history
            ):
                sent_series.append((pk, PLATFORM_COLORS.get(pk, "#4B5563"), PLATFORM_LABELS.get(pk, pk)))
        for entry in history:
            point = {"date": entry.get("date")}
            summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
            for pk in PRIMARY_PLATFORMS:
                plat = summary.get(pk) if isinstance(summary.get(pk), dict) else None
                if plat and plat.get("sentiment_score") is not None:
                    point[pk] = round(float(plat["sentiment_score"]) * 100)
            sent_points.append(point)
        if sent_series:
            chart = _svg_line_chart(sent_points, sent_series, aria_label="Sentiment over time")
            body = chart or _time_series_table(
                sent_points, [(key, label) for key, _color, label in sent_series]
            )
            parts.append(_subsection("Sentiment over time", body))

    # Brand & competitor visibility table
    if brand_rows:
        display = _competitor_visibility_display_rows(brand_rows, limit=5)
        row_html = []
        for row in display:
            you = " <span class='topic-count'>Your Brand</span>" if row["is_own"] else ""
            row_html.append(
                "<tr>"
                f"<td>{ESC(str(row['name']))}{you}</td>"
                f"<td style='text-align:right' class='{_pct_class(row['visibility'])}'>{row['visibility']:.1f}%</td>"
                f"<td style='text-align:right' class='{_pct_class(row['sov'])}'>{row['sov']:.1f}%</td>"
                "</tr>"
            )
        parts.append(
            _subsection(
                "Brand & competitor visibility",
                "<table class='export-table'><thead><tr>"
                "<th>Brand</th>"
                "<th style='text-align:right'>Visibility</th><th style='text-align:right'>SOV</th>"
                "</tr></thead>"
                f"<tbody>{''.join(row_html)}</tbody></table>",
            )
        )
        over_time = _brand_competitor_over_time_chart(audit_dir, display)
        if over_time:
            parts.append(_subsection("Brand & competitor visibility over time", over_time))

    # Top cited domains (information sources only)
    is_source = _information_source_domains(audit_dir, ctx)
    sites = _aggregate_root_domains([
        s for s in (live.get("top_cited_sites") or [])
        if isinstance(s, dict) and is_source(str(s.get("domain") or ""))
    ])[:8]
    if sites:
        rows = []
        for site in sites:
            domain = str(site.get("domain") or "")
            count = site.get("count") or site.get("citations") or "—"
            plats = site.get("platforms") or []
            plat = ", ".join(PLATFORM_LABELS.get(str(p), str(p)) for p in plats) if isinstance(plats, list) else ""
            rows.append(
                f"<tr><td>{ESC(domain)}</td>"
                f"<td style='text-align:right'>{ESC(str(count))}</td>"
                f"<td class='muted'>{ESC(plat)}</td></tr>"
            )
        parts.append(
            _subsection(
                "Top cited domains",
                "<table class='export-table'><thead><tr>"
                "<th>Domain</th><th style='text-align:right'>Citations</th><th>Platforms</th>"
                "</tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table>",
            )
        )

    # Citation Frequency Over Time
    cite_rows = _load_citation_history_rows(audit_dir)
    if cite_rows and sites:
        allowed = {str(s.get("domain") or "") for s in sites}
        filtered = [r for r in cite_rows if r.get("domain") in allowed]
        totals: dict[str, int] = {}
        for r in filtered:
            totals[r["domain"]] = totals.get(r["domain"], 0) + int(r.get("frequency") or 0)
        top_domains = [d for d, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:8]]
        if top_domains:
            dates = sorted({r["date"] for r in filtered if r.get("date")})
            cite_points: list[dict[str, Any]] = []
            for date in dates:
                point: dict[str, Any] = {"date": date}
                for domain in top_domains:
                    point[domain] = sum(
                        int(r.get("frequency") or 0)
                        for r in filtered
                        if r.get("date") == date and r.get("domain") == domain
                    )
                cite_points.append(point)
            palette = ["#4285F4", "#EA4335", "#FBBC05", "#34A853", "#7B68EE", "#FF7043", "#00BCD4", "#E91E63"]
            cite_series = [
                (domain, palette[i % len(palette)], domain) for i, domain in enumerate(top_domains)
            ]
            chart = _svg_line_chart(cite_points, cite_series, aria_label="Citation frequency over time", y_is_pct=False)
            if chart:
                parts.append(_subsection("Citation Frequency Over Time", chart))
            else:
                # Structured substitute: latest frequencies
                table_rows = "".join(
                    f"<tr><td>{ESC(d)}</td><td style='text-align:right'>{totals.get(d, 0)}</td></tr>"
                    for d in top_domains
                )
                parts.append(
                    _subsection(
                        "Citation Frequency Over Time",
                        "<p class='muted'>Trend chart needs multiple history points; showing latest domain frequencies.</p>"
                        "<table class='export-table'><thead><tr><th>Domain</th>"
                        "<th style='text-align:right'>Frequency</th></tr></thead>"
                        f"<tbody>{table_rows}</tbody></table>",
                    )
                )

    # Prompt visibility breakdown (with sentiment)
    if per_prompt:
        agg = live.get("aggregate") if isinstance(live.get("aggregate"), dict) else {}
        active = [pk for pk in PRIMARY_PLATFORMS if agg.get(pk) is not None or any(
            str(r.get(f"{pk}_response") or "") for r in per_prompt[:10]
        )]
        head = (
            "<th style='text-align:center'>Sentiment</th>"
            + "".join(f"<th style='text-align:center'>{ESC(PLATFORM_LABELS.get(pk, pk))}</th>" for pk in active)
        )
        body_rows = []
        for row in per_prompt[:10]:
            # Prompt-level sentiment badge
            prompt_pos = prompt_neg = prompt_mentioned = 0
            for pk in PRIMARY_PLATFORMS:
                resp = str(row.get(f"{pk}_response") or "")
                if not resp or not lower_tokens:
                    continue
                if not any(tok in resp.lower() for tok in lower_tokens):
                    continue
                prompt_mentioned += 1
                sent = _windowed_sentiment(resp, lower_tokens)
                if sent == "positive":
                    prompt_pos += 1
                elif sent == "negative":
                    prompt_neg += 1
            if prompt_mentioned == 0:
                sent_badge = "—"
            elif prompt_pos > prompt_neg:
                sent_badge = "+"
            elif prompt_neg > prompt_pos:
                sent_badge = "−"
            else:
                sent_badge = "~"
            cells = [f"<td style='text-align:center'>{sent_badge}</td>"]
            for pk in active:
                mentioned_plat = False
                scores_row = row.get(f"mention_scores_{pk}") if isinstance(row.get(f"mention_scores_{pk}"), dict) else {}
                if float(scores_row.get("brand_signal") or 0) > 0:
                    mentioned_plat = True
                else:
                    resp = str(row.get(f"{pk}_response") or "")
                    if resp and _brand_mentioned(resp, brand, tokens or [brand]):
                        mentioned_plat = True
                cells.append(
                    f"<td style='text-align:center'>{'✓' if mentioned_plat else '—'}</td>"
                )
            body_rows.append(
                f"<tr><td style='font-size:12px'>{ESC(str(row.get('prompt') or '')[:160])}</td>"
                + "".join(cells)
                + "</tr>"
            )
        parts.append(
            _subsection(
                "Prompt visibility breakdown",
                "<table class='export-table'><thead><tr><th>Prompt</th>"
                + head
                + "</tr></thead>"
                f"<tbody>{''.join(body_rows)}</tbody></table>",
            )
        )

    body = "".join(parts)
    if not body.strip():
        body = "<div class='export-empty'>No AI visibility probe data yet.</div>"
    n_prompts = len(per_prompt)
    n_plats = sum(
        1 for pk in PRIMARY_PLATFORMS
        if isinstance(per_platform.get(pk), dict) and per_platform[pk].get("response_count")
    )
    return _shell(
        "AI visibility overview",
        f"Aggregated across {n_prompts} prompt{'s' if n_prompts != 1 else ''} and {n_plats} platform{'s' if n_plats != 1 else ''}.",
        body,
    )


# ── Citations ──────────────────────────────────────────────────────────────────


def build_citations_export(audit_dir: Path) -> str:
    ctx = _load_probe_ctx(audit_dir)
    live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else {}
    is_eligible = _citation_eligible_domains(audit_dir, ctx)
    raw_urls = [
        u for u in (live.get("top_cited_urls") or [])
        if isinstance(u, dict) and is_eligible(str(u.get("domain") or ""))
    ]
    urls = [u for u in raw_urls if _has_page_path(str(u.get("url") or ""))]
    evidenced_domains: set[str] = set()
    for u in urls:
        d = _registrable_domain(str(u.get("domain") or ""))
        if d:
            evidenced_domains.add(d)
    for u in raw_urls:
        if not str(u.get("title") or "").strip():
            continue
        d = _registrable_domain(str(u.get("domain") or ""))
        if d:
            evidenced_domains.add(d)
    sites = _aggregate_root_domains([
        s for s in (live.get("top_cited_sites") or [])
        if isinstance(s, dict)
        and is_eligible(str(s.get("domain") or ""))
        and _registrable_domain(str(s.get("domain") or "")) in evidenced_domains
    ])
    if not sites and not urls:
        return _shell(
            "Citations",
            "Domains and URLs most commonly referenced in AI answers.",
            "<div class='export-empty'>No citation data yet.</div>",
        )

    parts: list[str] = []
    if sites:
        max_count = max((int(s.get("count") or s.get("citations") or 0) for s in sites), default=1) or 1
        rows = []
        for site in sites[:30]:
            domain = str(site.get("domain") or "")
            count = int(site.get("count") or site.get("citations") or 0)
            brand = "Yes" if site.get("brand_mentioned") else "—"
            comps = site.get("competitor_names") or []
            ment = ", ".join(str(c) for c in comps[:4]) if isinstance(comps, list) and comps else "—"
            plats = site.get("platforms") or []
            plat = ", ".join(PLATFORM_LABELS.get(str(p), str(p)) for p in plats) if isinstance(plats, list) else ""
            bar = min(100, round(100 * count / max_count))
            rows.append(
                "<tr>"
                f"<td><div style='font-weight:600'>{ESC(domain)}</div>"
                f"<div class='bar-track' style='margin-top:6px;max-width:160px'>"
                f"<div class='bar-fill' style='width:{bar}%;background:#0984e3'></div></div></td>"
                f"<td style='text-align:center'>{count}</td>"
                f"<td style='text-align:center'>{ESC(brand)}</td>"
                f"<td class='muted'>{ESC(ment)}</td>"
                f"<td class='muted'>{ESC(plat)}</td>"
                "</tr>"
            )
        parts.append(
            "<div style='padding-top:4px'>"
            "<div class='export-section-header' style='border:0;padding-bottom:0'>"
            "<h2 style='font-size:14px;margin:0'>Top Domains</h2>"
            "<p>Websites most commonly referenced in AI-generated answers.</p></div>"
            "<table class='export-table'><thead><tr>"
            "<th>Domain</th><th style='text-align:center'>Frequency</th>"
            "<th style='text-align:center'>Brand mentioned</th>"
            "<th>Mentions</th><th>AI platforms</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )

    if urls:
        url_rows = []
        for item in urls[:30]:
            url = str(item.get("url") or "")
            title = str(item.get("title") or "")
            domain = str(item.get("domain") or "")
            freq = item.get("frequency") or item.get("count") or "—"
            content_type = str(item.get("content_type") or item.get("type") or "Web page")
            channel_type = str(item.get("channel_type") or item.get("channel") or "Website")
            brand = "Yes" if item.get("brand_mentioned") else "—"
            comps = item.get("competitor_names") or []
            ment = ", ".join(str(c) for c in comps[:4]) if isinstance(comps, list) and comps else "—"
            label = title or domain or url
            url_rows.append(
                "<tr>"
                f"<td><div style='font-weight:600'>{ESC(label[:100])}</div>"
                f"<div class='muted' style='word-break:break-all'>{ESC(url[:120])}</div></td>"
                f"<td class='muted'>{ESC(content_type)}</td>"
                f"<td class='muted'>{ESC(channel_type)}</td>"
                f"<td style='text-align:center'>{ESC(str(freq))}</td>"
                f"<td style='text-align:center'>{ESC(brand)}</td>"
                f"<td class='muted'>{ESC(ment)}</td>"
                "</tr>"
            )
        parts.append(
            "<div style='padding-top:12px'>"
            "<div class='export-section-header' style='border:0;padding-bottom:0'>"
            "<h2 style='font-size:14px;margin:0'>Top URLs</h2>"
            "<p>URLs most commonly referenced in AI-generated answers.</p></div>"
            "<table class='export-table'><thead><tr>"
            "<th>URL</th><th>Content type</th><th>Channel type</th>"
            "<th style='text-align:center'>Frequency</th>"
            "<th style='text-align:center'>Brand mentioned</th><th>Mentions</th>"
            "</tr></thead>"
            f"<tbody>{''.join(url_rows)}</tbody></table></div>"
        )

    return _shell(
        "Citations",
        "Domains and URLs most commonly referenced in AI-generated answers.",
        "".join(parts),
    )


# ── Reddit / YouTube ───────────────────────────────────────────────────────────


def _insights_brand_context(audit_dir: Path) -> tuple[str, list[str], list[str]]:
    brand_name = ""
    brand_site = ""
    competitor_urls: list[str] = []
    competitor_brands: list[str] = []
    try:
        ob = json.loads((audit_dir / "onboarding_context.json").read_text(encoding="utf-8", errors="replace"))
        brand_name = str(ob.get("brand_name_used") or "").strip()
        brand_site = str(
            ob.get("brand_website_used") or ob.get("brand_url") or ob.get("brand_site_url") or ""
        ).strip()
        for c in ob.get("competitors_detail") or ob.get("competitor_context") or []:
            if isinstance(c, dict):
                if c.get("competitor_website"):
                    competitor_urls.append(str(c["competitor_website"]))
                if c.get("competitor_brand"):
                    competitor_brands.append(str(c["competitor_brand"]))
    except Exception:
        pass
    return brand_name, competitor_urls, competitor_brands


def build_reddit_insights_export(audit_dir: Path) -> str:
    try:
        from api.reddit_insights import (
            _build_positional_topic_map,
            _enrich_from_url,
            _extract_reddit_posts,
        )
        from backend.citation_context import (
            build_brand_tokens,
            build_competitor_tokens,
            merge_brand_tokens,
        )
    except Exception:
        return _shell(
            "Reddit Citations",
            "Reddit posts cited by AI platforms.",
            "<div class='export-empty'>Reddit insights unavailable.</div>",
        )

    brand_name, competitor_urls, competitor_brands = _insights_brand_context(audit_dir)
    brand_site = ""
    try:
        ob = json.loads((audit_dir / "onboarding_context.json").read_text(encoding="utf-8", errors="replace"))
        brand_site = str(
            ob.get("brand_website_used") or ob.get("brand_url") or ob.get("brand_site_url") or ""
        ).strip()
    except Exception:
        pass

    brand_tokens = build_brand_tokens(brand_name, brand_site)
    comp_tokens = build_competitor_tokens(competitor_urls, competitor_brands)
    per_prompt_texts: list[str] = []
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    if probe_path.is_file():
        try:
            raw = json.loads(probe_path.read_text(encoding="utf-8", errors="replace"))
            live = raw.get("live_probe", raw) if isinstance(raw, dict) else {}
            if not brand_name:
                brand_name = str(live.get("brand_name") or "").strip()
            brand_tokens = merge_brand_tokens(
                build_brand_tokens(brand_name, brand_site),
                [str(t) for t in live.get("brand_match_tokens") or []],
            )
            per_prompt_texts = [str(r.get("prompt") or "") for r in live.get("per_prompt", [])]
        except Exception:
            pass

    topic_map = _build_positional_topic_map(audit_dir, per_prompt_texts)
    raw_posts = _extract_reddit_posts(audit_dir, topic_map, brand_tokens, comp_tokens)
    if not raw_posts:
        return _shell(
            "Reddit Citations",
            "Reddit posts cited by AI platforms.",
            "<div class='export-empty'>No Reddit citations found in probe responses.</div>",
        )

    posts = []
    for item in raw_posts[:40]:
        try:
            details = _enrich_from_url(item["url"])
        except Exception:
            details = {}
        posts.append({**item, **details})
    posts.sort(key=lambda p: p.get("citation_count") or 0, reverse=True)

    rows = []
    for post in posts:
        url = str(post.get("url") or "")
        title = str(post.get("reddit_title") or post.get("title") or url)
        sub = ""
        try:
            parts = urlparse(url).path.split("/")
            if "r" in parts:
                idx = parts.index("r")
                if idx + 1 < len(parts):
                    sub = parts[idx + 1]
        except Exception:
            pass
        plats = post.get("platforms") or []
        if isinstance(plats, set):
            plats = sorted(plats)
        plat = ", ".join(PLATFORM_LABELS.get(str(p), str(p)) for p in plats)
        brand = "Yes" if post.get("brand_mentioned_count") or post.get("brand_mentioned") else "—"
        count = post.get("citation_count") or 0
        topics = post.get("prompts_by_topic") or {}
        topic_label = ", ".join(list(topics.keys())[:3]) if isinstance(topics, dict) else ""
        rows.append(
            "<tr>"
            f"<td><div style='font-weight:600'>{ESC(title[:140])}</div>"
            f"<div class='muted'>r/{ESC(sub) if sub else '—'}</div></td>"
            f"<td style='text-align:center'>{count}</td>"
            f"<td style='text-align:center'>{ESC(brand)}</td>"
            f"<td class='muted'>{ESC(plat)}</td>"
            f"<td class='muted'>{ESC(topic_label or '—')}</td>"
            "</tr>"
        )
    table = (
        "<table class='export-table'><thead><tr>"
        "<th>Post</th><th style='text-align:center'>Citations</th>"
        "<th style='text-align:center'>Brand</th><th>Platforms</th><th>Topics</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return _shell(
        "Reddit Citations",
        f"{len(posts)} Reddit post{'s' if len(posts) != 1 else ''} cited in AI responses"
        + (f" for {brand_name}." if brand_name else "."),
        table,
    )


def build_youtube_insights_export(audit_dir: Path) -> str:
    try:
        from api.youtube_insights import (
            _build_positional_topic_map,
            _extract_youtube_urls,
            _fetch_video_details,
        )
        from backend.citation_context import (
            build_brand_tokens,
            build_competitor_tokens,
            infer_citation_brand_context,
            merge_brand_tokens,
        )
        import os
    except Exception:
        return _shell(
            "YouTube Citations",
            "YouTube videos cited by AI platforms.",
            "<div class='export-empty'>YouTube insights unavailable.</div>",
        )

    brand_name, competitor_urls, competitor_brands = _insights_brand_context(audit_dir)
    brand_site = ""
    try:
        ob = json.loads((audit_dir / "onboarding_context.json").read_text(encoding="utf-8", errors="replace"))
        brand_site = str(
            ob.get("brand_website_used") or ob.get("brand_url") or ob.get("brand_site_url") or ""
        ).strip()
    except Exception:
        pass

    brand_tokens = build_brand_tokens(brand_name, brand_site)
    comp_tokens = build_competitor_tokens(competitor_urls, competitor_brands)
    per_prompt_texts: list[str] = []
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    if probe_path.is_file():
        try:
            raw = json.loads(probe_path.read_text(encoding="utf-8", errors="replace"))
            live = raw.get("live_probe", raw) if isinstance(raw, dict) else {}
            if not brand_name:
                brand_name = str(live.get("brand_name") or "").strip()
            brand_tokens = merge_brand_tokens(
                build_brand_tokens(brand_name, brand_site),
                [str(t) for t in live.get("brand_match_tokens") or []],
            )
            per_prompt_texts = [str(r.get("prompt") or "") for r in live.get("per_prompt", [])]
        except Exception:
            pass

    topic_map = _build_positional_topic_map(audit_dir, per_prompt_texts)
    reviewed = len({p.strip() for p in per_prompt_texts if p.strip()})
    raw_urls = _extract_youtube_urls(audit_dir, topic_map, brand_tokens, comp_tokens)
    if not raw_urls:
        return _shell(
            "YouTube Citations",
            "YouTube videos cited by AI platforms.",
            "<div class='export-empty'>No YouTube citations found in probe responses.</div>",
        )

    api_key = os.environ.get("YOUTUBE_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
    video_ids = [item["video_id"] for item in raw_urls if item.get("video_id")]
    enriched_map: dict[str, dict[str, Any]] = {}
    if api_key and video_ids:
        try:
            # _fetch_video_details returns (results, api_error) — unpack like the API route.
            enriched_map, _api_error = _fetch_video_details(
                list(dict.fromkeys(video_ids)), brand_name, api_key
            )
            if not isinstance(enriched_map, dict):
                enriched_map = {}
        except Exception:
            enriched_map = {}

    videos = []
    for item in raw_urls[:40]:
        vid = item.get("video_id")
        details = enriched_map.get(vid, {}) if vid else {}
        title = str(details.get("yt_title") or item.get("title") or "")
        title_ctx = infer_citation_brand_context(
            "",
            str(item.get("url") or ""),
            str(item.get("domain") or ""),
            brand_tokens,
            comp_tokens,
            title,
        )
        videos.append({
            **item,
            **details,
            "brand_mentioned": bool(item.get("brand_mentioned")) or title_ctx["brand_cited"],
            "citation_percentage": (
                round(100 * int(item.get("citing_prompt_count") or 0) / reviewed, 1)
                if reviewed else None
            ),
        })
    videos.sort(key=lambda v: v.get("view_count") or v.get("views") or 0, reverse=True)

    rows = []
    for video in videos:
        title = str(video.get("yt_title") or video.get("title") or video.get("url") or "Video")
        channel = str(video.get("channel_title") or video.get("channel") or "—")
        views = video.get("view_count") or video.get("views")
        views_s = f"{int(views):,}" if isinstance(views, (int, float)) else "—"
        brand = "Yes" if video.get("brand_mentioned") else "—"
        plats = video.get("platforms") or []
        if isinstance(plats, set):
            plats = sorted(plats)
        plat = ", ".join(PLATFORM_LABELS.get(str(p), str(p)) for p in plats)
        pct = video.get("citation_percentage")
        pct_s = f"{pct}%" if isinstance(pct, (int, float)) else "—"
        rows.append(
            "<tr>"
            f"<td><div style='font-weight:600'>{ESC(title[:140])}</div>"
            f"<div class='muted'>{ESC(channel)}</div></td>"
            f"<td style='text-align:right'>{ESC(views_s)}</td>"
            f"<td style='text-align:center'>{ESC(brand)}</td>"
            f"<td style='text-align:center'>{ESC(pct_s)}</td>"
            f"<td class='muted'>{ESC(plat)}</td>"
            "</tr>"
        )
    table = (
        "<table class='export-table'><thead><tr>"
        "<th>Video</th><th style='text-align:right'>Views</th>"
        "<th style='text-align:center'>Brand</th>"
        "<th style='text-align:center'>% prompts</th><th>Platforms</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )
    return _shell(
        "YouTube Citations",
        f"{len(videos)} YouTube video{'s' if len(videos) != 1 else ''} cited in AI responses"
        + (f" for {brand_name}." if brand_name else "."),
        table,
    )


# ── Technical sections ─────────────────────────────────────────────────────────


def build_technical_overview_export(audit_dir: Path) -> str:
    scores = _load_scores(audit_dir)
    tech = scores.get("technical_setup")
    components = ((scores.get("details") or {}).get("technical_setup") or {}).get("components") or []
    by_key = {str(c.get("key")): c for c in components if isinstance(c, dict)}

    sub_areas = [
        ("Crawler access", ["ai_crawler_report"],
         "Whether major AI crawlers can access the site through robots.txt and related controls."),
        ("Citability", ["ai_citability", "ai_search_success", "query_coverage_footprint"],
         "How successfully the site supports AI extraction and citation across tested queries and content."),
        ("Platform readiness", ["platform_readiness"],
         "Technical and prompt-performance readiness for each supported AI platform."),
    ]

    hero = ""
    if tech is not None:
        try:
            tech_f = float(tech)
            tone, color = _score_tone_color(tech_f)
            hero = (
                "<div class='summary-hero'>"
                f"<div class='summary-hero-score'>{_svg_score_gauge(tech_f, size=96)}"
                f"<div class='summary-hero-copy'>"
                f"<p class='eyebrow'>Technical GEO Setup</p>"
                f"<p class='score-num'>{tech_f:.0f}<span> /100</span></p>"
                f"<p class='score-label' style='color:{color}'>{ESC(tone)}</p>"
                f"</div></div></div>"
            )
        except (TypeError, ValueError):
            pass

    rows = []
    for label, keys, desc in sub_areas:
        comps = [by_key[k] for k in keys if k in by_key]
        if not comps:
            score_html = "<span class='muted'>— /100</span>"
        else:
            total_w = sum(float(c.get("weight_pct") or 0) for c in comps)
            if total_w > 0:
                score = sum(float(c.get("score") or 0) * float(c.get("weight_pct") or 0) for c in comps) / total_w
            else:
                score = sum(float(c.get("score") or 0) for c in comps) / len(comps)
            score_html = _score_pill(score)
        rows.append(
            "<tr>"
            f"<td><div style='font-weight:700'>{ESC(label)}</div>"
            f"<div class='muted' style='margin-top:4px'>{ESC(desc)}</div></td>"
            f"<td style='text-align:right;white-space:nowrap'>{score_html}</td>"
            "</tr>"
        )
    table = (
        "<div style='padding:8px 24px 20px'>"
        "<h3 style='margin:0 0 12px;font-size:14px;font-weight:700'>What this score measures</h3>"
        "<table class='export-table'><thead><tr>"
        "<th>Criteria</th><th style='text-align:right'>Score</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "<p class='muted' style='margin:14px 0 0'>Technical Setup = 25% crawler access + 50% citability + 25% platform readiness.</p>"
        "</div>"
    )
    body = hero + table
    if not body.strip():
        body = "<div class='export-empty'>Technical setup scores not available.</div>"
    return _shell(
        "Technical setup overview",
        "How well AI tools can access and use the site.",
        body,
    )


def build_crawler_access_export(audit_dir: Path) -> str:
    scores = _load_scores(audit_dir)
    crawler = scores.get("crawler_access") if isinstance(scores.get("crawler_access"), dict) else {}
    rows = [r for r in (crawler.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        return _shell(
            "Crawler access",
            "Which search and AI crawlers can access the site under current robots.txt rules.",
            "<div class='export-empty'>No robots.txt crawler data is available for this audit.</div>",
        )
    body_rows = []
    for row in rows:
        can = "Can fetch" if row.get("can_fetch") else "Cannot fetch"
        can_color = "#00b894" if row.get("can_fetch") else "#e17055"
        aligned = row.get("aligned")
        if aligned is None:
            aligned_s = "—"
        elif aligned:
            aligned_s = "Yes"
        else:
            aligned_s = "No"
        rec = str(row.get("recommendation") or "")
        body_rows.append(
            "<tr>"
            f"<td style='font-family:ui-monospace,monospace;font-size:12px;font-weight:600'>{ESC(str(row.get('crawler') or ''))}</td>"
            f"<td class='muted'>{ESC(str(row.get('tier') or ''))}</td>"
            f"<td><span class='chip chip-neu'>{ESC(rec)}</span></td>"
            f"<td class='muted'>{ESC(str(row.get('reason') or ''))}</td>"
            f"<td style='color:{can_color};font-weight:600'>{ESC(can)}</td>"
            f"<td style='text-align:center'>{ESC(aligned_s)}</td>"
            "</tr>"
        )
    strengths = crawler.get("strengths") or []
    improvements = crawler.get("improvements") or []
    extras = ""
    if strengths or improvements:
        extras = (
            "<div class='score-grid' style='padding-top:8px'>"
            f"<div class='score-card'><div class='label'>Strengths</div>{_finding_list([str(s) for s in strengths], empty='None recorded.')}</div>"
            f"<div class='score-card'><div class='label'>Improvements</div>{_finding_list([str(s) for s in improvements], empty='None recorded.', positive=False)}</div>"
            "</div>"
        )
    table = (
        "<table class='export-table'><thead><tr>"
        "<th>Crawler</th><th>Tier</th><th>GEO recommendation</th>"
        "<th>Reason</th><th>Your robots</th><th style='text-align:center'>Aligned</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )
    score = crawler.get("score")
    subtitle = "Which search and AI crawlers can access the site under current robots.txt rules."
    if isinstance(score, (int, float)):
        subtitle = f"Crawler access score {float(score):.0f}/100. {subtitle}"
    return _shell("Crawler access", subtitle, table + extras)


def build_citability_export(audit_dir: Path) -> str:
    scores = _load_scores(audit_dir)
    components = ((scores.get("details") or {}).get("technical_setup") or {}).get("components") or []
    order = ["ai_citability", "ai_search_success", "query_coverage_footprint"]
    by_key = {str(c.get("key")): c for c in components if isinstance(c, dict)}
    rows_data = [by_key[k] for k in order if k in by_key]
    if not rows_data:
        return _shell(
            "Citability",
            "AI citability, AI Search Success criteria, and query coverage.",
            "<div class='export-empty'>Citability details not available.</div>",
        )

    body_rows = []
    for comp in rows_data:
        strengths: list[str] = []
        improvements: list[str] = []
        if comp.get("key") == "ai_search_success" and comp.get("criteria"):
            for criterion in comp.get("criteria") or []:
                if not isinstance(criterion, dict):
                    continue
                title = str(criterion.get("title") or "Criterion")
                sc = float(criterion.get("score") or 0)
                if sc >= 80:
                    msg = (criterion.get("strengths") or ["This criterion is performing well."])[0]
                    strengths.append(f"{title} ({sc:.0f}/100): {msg}")
                else:
                    msg = (criterion.get("improvements") or ["This criterion needs further work."])[0]
                    improvements.append(f"{title} ({sc:.0f}/100): {msg}")
        else:
            strengths = [str(s) for s in (comp.get("strengths") or [])]
            improvements = [str(s) for s in (comp.get("improvements") or [])]
        score = float(comp.get("score") or 0)
        body_rows.append(
            "<tr>"
            f"<td><div style='font-weight:700'>{ESC(str(comp.get('title') or ''))}</div>"
            f"<p class='muted' style='margin:6px 0 0'>{ESC(str(comp.get('detail') or ''))}</p></td>"
            f"<td>{_finding_list(strengths, empty='No positive finding recorded yet.')}</td>"
            f"<td>{_finding_list(improvements, empty='No material gap recorded.', positive=False)}</td>"
            f"<td style='text-align:center'>{_score_pill(score)}</td>"
            "</tr>"
        )
    table = (
        "<table class='export-table'><thead><tr>"
        "<th>Criteria</th><th>What is working well</th>"
        "<th>What needs work</th><th style='text-align:center'>Score</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )
    return _shell(
        "Citability",
        "AI citability, nine AI Search Success criteria, and query coverage.",
        table,
    )


def build_platform_readiness_export(audit_dir: Path) -> str:
    scores = _load_scores(audit_dir)
    platform_rows = [r for r in (scores.get("platform_readiness") or []) if isinstance(r, dict)]
    metrics = scores.get("prompt_metrics") if isinstance(scores.get("prompt_metrics"), dict) else {}
    per_platform = metrics.get("per_platform") if isinstance(metrics.get("per_platform"), dict) else {}

    config = [
        ("gemini", "gemini", "Gemini"),
        ("chatgpt", "openai", "ChatGPT / OpenAI"),
        ("aio", "google_aio", "Google AI Overviews"),
        ("claude", "claude", "Claude (Anthropic)"),
        ("perplexity", None, "Perplexity"),
        ("copilot", None, "Bing Copilot"),
    ]
    base_by_key = {str(r.get("key") or ""): r for r in platform_rows}

    cards = []
    for base_key, probe_key, label in config:
        base = base_by_key.get(base_key) or {}
        base_score = base.get("score")
        try:
            base_f = float(base_score) if base_score is not None else None
        except (TypeError, ValueError):
            base_f = None
        probe = per_platform.get(probe_key) if probe_key and isinstance(per_platform.get(probe_key), dict) else None
        if probe and probe.get("response_count"):
            vis = float(probe.get("visibility_pct") or 0)
            sov = float(probe.get("sov_pct") or 0)
            technical = base_f if base_f is not None else 50.0
            # Match PlatformReadinessSection: 55% visibility + 25% SOV + 20% technical baseline.
            blended = min(100, round(0.55 * vis + 0.25 * sov + 0.20 * technical))
            color = PLATFORM_COLORS.get(probe_key or "", "#4B5563")
            cards.append(
                f"<div class='score-card'>"
                f"<div class='label'>{ESC(label)}</div>"
                f"<div class='value' style='color:{color}'>{blended}</div>"
                f"<div class='sub'>{vis:.0f}% visibility · {sov:.0f}% SOV · "
                f"tech {technical:.0f}</div>"
                f"<p class='muted' style='margin:8px 0 0'>{ESC(str(base.get('gap') or ''))}</p>"
                f"</div>"
            )
        elif base_f is not None:
            _, color = _score_tone_color(base_f)
            cards.append(
                f"<div class='score-card'>"
                f"<div class='label'>{ESC(label)}</div>"
                f"<div class='value' style='color:{color}'>{base_f:.0f}</div>"
                f"<div class='sub'>Technical baseline</div>"
                f"<p class='muted' style='margin:8px 0 0'>{ESC(str(base.get('gap') or ''))}</p>"
                f"</div>"
            )

    if not cards:
        return _shell(
            "Platform readiness",
            "Technical and prompt-performance readiness by AI platform.",
            "<div class='export-empty'>Platform readiness data not available.</div>",
        )
    return _shell(
        "Platform readiness",
        "Technical and prompt-performance readiness by AI platform.",
        f"<div class='score-grid'>{''.join(cards)}</div>",
    )


# ── Content sections ───────────────────────────────────────────────────────────


def _content_details(audit_dir: Path) -> dict[str, Any]:
    scores = _load_scores(audit_dir)
    details = scores.get("content_quality_details")
    if isinstance(details, dict) and details:
        return details
    try:
        from api import geo_services as geo

        return geo.load_content_quality_details(audit_dir) or {}
    except Exception:
        return {}


def build_content_overview_export(audit_dir: Path) -> str:
    scores = _load_scores(audit_dir)
    details = _content_details(audit_dir)
    # Prefer integrated content_structure score (matches Summary / UI overview).
    score = scores.get("content_structure")
    if score is None:
        score = details.get("score")
    components = [c for c in (
        ((scores.get("details") or {}).get("content_structure") or {}).get("components")
        or details.get("components")
        or []
    ) if isinstance(c, dict)]
    by_key = {str(c.get("key")): c for c in components}

    sub_areas = [
        ("E-E-A-T Signals", ["eeat"],
         "Experience, Expertise, Authoritativeness, and Trustworthiness signals across key pages."),
        ("Content Structure & Answerability",
         ["original_information_gain", "passage_answerability", "content_formatting"],
         "Whether content contributes original information and is structured into clear, extractable passages."),
        ("Schema & Entity Markup", ["schema_entity_markup"],
         "JSON-LD structured data coverage and entity clarity."),
        ("Brand Visibility & Authority", ["brand_visibility_authority"],
         "Third-party presence signals used by AI systems to corroborate the brand."),
    ]

    hero = ""
    if score is not None:
        try:
            score_f = float(score)
            tone, color = _score_tone_color(score_f)
            hero = (
                "<div class='summary-hero'>"
                f"<div class='summary-hero-score'>{_svg_score_gauge(score_f, size=96)}"
                f"<div class='summary-hero-copy'>"
                f"<p class='eyebrow'>Content Quality Score</p>"
                f"<p class='score-num'>{score_f:.0f}<span> /100</span></p>"
                f"<p class='score-label' style='color:{color}'>{ESC(tone)}</p>"
                f"</div></div></div>"
            )
        except (TypeError, ValueError):
            pass

    rows = []
    for label, keys, desc in sub_areas:
        comps = [by_key[k] for k in keys if k in by_key]
        if not comps:
            score_html = "<span class='muted'>— /100</span>"
        else:
            total_w = sum(float(c.get("weight_pct") or 0) for c in comps)
            if total_w > 0:
                area_score = sum(
                    float(c.get("score") or 0) * float(c.get("weight_pct") or 0) for c in comps
                ) / total_w
            else:
                area_score = sum(float(c.get("score") or 0) for c in comps) / len(comps)
            score_html = _score_pill(area_score)
        rows.append(
            "<tr>"
            f"<td><div style='font-weight:700'>{ESC(label)}</div>"
            f"<div class='muted' style='margin-top:4px'>{ESC(desc)}</div></td>"
            f"<td style='text-align:right;white-space:nowrap'>{score_html}</td>"
            "</tr>"
        )
    table = (
        "<div style='padding:8px 24px 20px'>"
        "<h3 style='margin:0 0 12px;font-size:14px;font-weight:700'>What this score measures</h3>"
        "<table class='export-table'><thead><tr>"
        "<th>Criteria</th><th style='text-align:right'>Score</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )
    body = hero + table
    if not body.strip():
        body = "<div class='export-empty'>Content quality scores not available.</div>"
    return _shell(
        "Content quality overview",
        "How helpful, trustworthy, and structured your content is for AI citation.",
        body,
    )


def build_content_eeat_export(audit_dir: Path) -> str:
    details = _content_details(audit_dir)
    rows = [r for r in (details.get("eeat") or []) if isinstance(r, dict)]
    aggregate = next((c for c in (details.get("components") or []) if c.get("key") == "eeat"), None)
    parts: list[str] = []
    if isinstance(aggregate, dict):
        parts.append(
            f"<div class='summary-block' style='margin-top:18px'>"
            f"{_score_pill(float(aggregate.get('score') or 0))} "
            f"<strong style='margin-left:10px'>Combined E-E-A-T score</strong>"
            f"<p class='muted' style='margin:8px 0 0'>{ESC(str(aggregate.get('finding_summary') or ''))}</p>"
            f"</div>"
        )
    if rows:
        cards = []
        for row in rows:
            evidence = row.get("evidence") or []
            ev_html = ""
            if evidence:
                items = []
                for ex in evidence[:3]:
                    if not isinstance(ex, dict):
                        continue
                    items.append(
                        f"<blockquote style='margin:8px 0 0;padding-left:10px;border-left:2px solid #e5e7eb;"
                        f"font-size:12px;color:#4b5563'>{ESC(str(ex.get('snippet') or ''))}</blockquote>"
                    )
                ev_html = "".join(items)
            else:
                ev_html = f"<p class='muted'>{ESC(str(row.get('evidence_note') or 'No evidence captured.'))}</p>"
            cards.append(
                f"<div class='score-card'>"
                f"<div style='display:flex;justify-content:space-between;gap:12px;align-items:flex-start'>"
                f"<div><div class='label' style='margin:0'>{ESC(str(row.get('name') or ''))}</div>"
                f"<p class='muted' style='margin:4px 0 0'>{ESC(str(row.get('tagline') or ''))}</p></div>"
                f"{_score_pill(float(row.get('score') or 0))}</div>"
                f"<p style='font-size:12px;color:#4b5563;margin:10px 0 0'>{ESC(str(row.get('what_it_means') or ''))}</p>"
                f"{ev_html}</div>"
            )
        parts.append(f"<div class='score-grid'>{''.join(cards)}</div>")
    if not parts:
        parts.append("<div class='export-empty'>No E-E-A-T evidence is available for this audit.</div>")
    return _shell(
        "E-E-A-T Signals",
        "Direct 0–100 content-fit scores with examples from sampled site pages.",
        "".join(parts),
    )


def build_content_structure_export(audit_dir: Path) -> str:
    details = _content_details(audit_dir)
    rows = [r for r in (details.get("structure_answerability") or []) if isinstance(r, dict)]
    if not rows:
        return _shell(
            "Content Structure & Answerability",
            "How distinctive, answer-ready and extractable the sampled content is.",
            "<div class='export-empty'>No content structure data available.</div>",
        )
    body_rows = []
    for row in rows:
        examples = row.get("examples") or []
        if examples:
            ev = "".join(
                f"<div style='margin-bottom:8px'><div class='muted'>{ESC(str(ex.get('title') or ex.get('url') or ''))}</div>"
                f"<blockquote style='margin:4px 0 0;padding-left:10px;border-left:2px solid #e5e7eb;"
                f"font-size:12px;color:#4b5563'>{ESC(str(ex.get('snippet') or ''))}</blockquote></div>"
                for ex in examples[:3]
                if isinstance(ex, dict)
            )
        else:
            ev = f"<p class='muted' style='font-style:italic'>{ESC(str(row.get('empty_message') or '—'))}</p>"
        body_rows.append(
            "<tr>"
            f"<td style='font-weight:700'>{ESC(str(row.get('title') or ''))}</td>"
            f"<td style='text-align:center'>{_score_pill(float(row.get('score') or 0))}</td>"
            f"<td class='muted'>{ESC(str(row.get('description') or ''))}</td>"
            f"<td>{ev}</td>"
            "</tr>"
        )
    table = (
        "<table class='export-table'><thead><tr>"
        "<th>Criterion</th><th style='text-align:center'>Score</th>"
        "<th>What this measures</th><th>Example content</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
    )
    return _shell(
        "Content Structure & Answerability",
        "How distinctive, answer-ready and extractable the sampled content is for AI systems.",
        table,
    )


def build_content_schema_export(audit_dir: Path) -> str:
    details = _content_details(audit_dir)
    section = details.get("schema_entity") if isinstance(details.get("schema_entity"), dict) else None
    if not section:
        return _shell(
            "Schema & Entity Markup",
            "JSON-LD coverage, schema depth and entity links.",
            "<div class='export-empty'>No schema evidence is available for this audit.</div>",
        )
    parts = [
        f"<div class='summary-block' style='margin-top:18px'>"
        f"{_score_pill(float(section.get('score') or 0))} "
        f"<span style='margin-left:10px;font-size:13px;color:#4b5563'>"
        f"Combined JSON-LD coverage, schema depth and entity-linking score.</span>"
        f"<p class='muted' style='margin:10px 0 0'>{ESC(str(section.get('summary') or ''))}</p></div>",
        "<div class='score-grid'>"
        f"<div class='score-card'><div class='label'>What is working well</div>"
        f"{_finding_list([str(s) for s in (section.get('strengths') or [])], empty='No positive schema finding recorded.')}</div>"
        f"<div class='score-card'><div class='label'>What needs work</div>"
        f"{_finding_list([str(s) for s in (section.get('improvements') or [])], empty='No material schema gap recorded.', positive=False)}</div>"
        "</div>",
    ]
    evidence = [e for e in (section.get("evidence") or []) if isinstance(e, dict)]
    if evidence:
        rows = []
        for item in evidence:
            types = ", ".join(str(t) for t in (item.get("types") or [])) or "Unclassified JSON-LD"
            rows.append(
                "<tr>"
                f"<td>{ESC(str(item.get('title') or item.get('url') or ''))}</td>"
                f"<td class='muted'>{ESC(types)}</td>"
                f"<td style='text-align:center'>{ESC(str(item.get('blocks') or 0))}</td>"
                f"<td style='text-align:center'>{ESC(str(item.get('same_as_count') or 0))}</td>"
                "</tr>"
            )
        parts.append(
            "<table class='export-table'><thead><tr>"
            "<th>Page</th><th>Schema types</th>"
            "<th style='text-align:center'>Blocks</th>"
            "<th style='text-align:center'>sameAs</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    return _shell(
        "Schema & Entity Markup",
        "JSON-LD coverage, schema depth and entity links that help AI understand the site.",
        "".join(parts),
    )


def build_content_brand_visibility_export(audit_dir: Path) -> str:
    details = _content_details(audit_dir)
    section = details.get("brand_visibility_authority")
    if not isinstance(section, dict):
        return _shell(
            "Brand Visibility & Authority",
            "Third-party presence signals used by AI systems to corroborate the brand.",
            "<div class='export-empty'>No brand visibility scan is available for this audit.</div>",
        )
    query = str(section.get("brand_query") or "")
    parts = [
        f"<div class='summary-block' style='margin-top:18px'>"
        f"{_score_pill(float(section.get('score') or 0))} "
        f"<span style='margin-left:10px;font-size:13px;color:#4b5563'>"
        f"{ESC(f'Visibility signals for “{query}”.' if query else 'Off-site brand visibility score.')}</span></div>"
    ]
    rows = [r for r in (section.get("rows") or []) if isinstance(r, dict)]
    if rows:
        body_rows = []
        for row in rows:
            present = bool(row.get("present"))
            badge = (
                "<span class='chip chip-pos'>Likely yes</span>"
                if present
                else "<span class='chip chip-neg'>No / unclear</span>"
            )
            url = str(row.get("url") or "").strip()
            channel = ESC(str(row.get("status") or "—"))
            if url:
                channel += f" <a href='{ESC(url)}'>{ESC(url[:60])}</a>"
            body_rows.append(
                "<tr>"
                f"<td style='font-weight:600'>{ESC(str(row.get('platform') or '—'))}</td>"
                f"<td style='text-align:center'>{badge}</td>"
                f"<td class='muted'>{channel}</td>"
                f"<td class='muted'>{ESC(str(row.get('impact') or '—'))}</td>"
                "</tr>"
            )
        parts.append(
            "<table class='export-table'><thead><tr>"
            "<th>Platform</th><th style='text-align:center'>Presence</th>"
            "<th>Channel / page</th><th>Impact on AI visibility</th>"
            "</tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table>"
        )
    else:
        parts.append("<div class='export-empty'>No platform rows are available.</div>")
    return _shell(
        "Brand Visibility & Authority",
        "Third-party presence signals used by AI systems to corroborate the brand.",
        "".join(parts),
    )


# ── Recommendations (all three tabs sequentially) ──────────────────────────────


def _rec_item_html(index: int, title: str, detail: str, actions: list[str], priority: str, score: float | None = None) -> str:
    chip = "chip-neg" if priority == "High" else "chip-neu"
    actions_html = ""
    if actions:
        lis = "".join(
            f"<li style='display:flex;gap:8px;margin:0 0 6px;font-size:12px;line-height:1.45;color:#374151'>"
            f"<span style='color:#2563eb'>→</span><span>{ESC(action)}</span></li>"
            for action in actions if str(action).strip()
        )
        actions_html = f"<ul style='margin:10px 0 0;padding:0;list-style:none'>{lis}</ul>"
    if isinstance(score, (int, float)):
        _, color = _score_tone_color(float(score))
        score_html = (
            f"<span style='margin-left:8px;border-radius:999px;padding:3px 10px;font-size:12px;"
            f"font-weight:700;font-variant-numeric:tabular-nums;color:{color};"
            f"background:{color}1a'>{format_report_score(float(score))}/100</span>"
        )
    else:
        score_html = ""
    return (
        f"<div style='padding:14px 0;border-bottom:1px solid #f3f4f6;display:grid;"
        f"grid-template-columns:2rem minmax(0,1fr) auto;gap:12px'>"
        f"<div style='width:28px;height:28px;border-radius:999px;background:#f3f4f6;"
        f"display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#6b7280'>"
        f"{index}</div>"
        f"<div><div style='display:flex;flex-wrap:wrap;gap:8px;align-items:center'>"
        f"<div style='font-weight:700;font-size:13px'>{ESC(title)}</div>"
        f"<span class='chip {chip}'>{ESC(priority)}</span>{score_html}</div>"
        + (f"<p class='muted' style='margin:6px 0 0'>{ESC(detail)}</p>" if detail else "")
        + actions_html
        + "</div></div>"
    )


def _rec_group_html(title: str, description: str, items: list[dict[str, Any]], empty: str) -> str:
    body = (
        f"<p class='muted' style='font-style:italic;padding:4px 0 12px'>{ESC(empty)}</p>"
        if not items
        else "".join(
            _rec_item_html(
                i + 1,
                str(item.get("title") or ""),
                str(item.get("detail") or ""),
                [str(a) for a in (item.get("actions") or [])],
                str(item.get("priority") or "Medium"),
                item.get("score") if isinstance(item.get("score"), (int, float)) else None,
            )
            for i, item in enumerate(items[:20])
        )
    )
    return (
        f"<div style='padding:8px 0 18px'>"
        f"<h3 style='margin:0 0 4px;font-size:14px;font-weight:700'>{ESC(title)}</h3>"
        f"<p class='muted' style='margin:0 0 10px'>{ESC(description)}</p>"
        f"<div style='border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:4px 18px;background:#fff'>"
        f"{body}</div></div>"
    )


def build_recommendations_export(audit_dir: Path) -> str:
    """Mirror RecommendationsSection: AI / Technical / Content tabs with groups."""
    scores = _load_scores(audit_dir)
    ctx = _load_probe_ctx(audit_dir)
    details = scores.get("details") if isinstance(scores.get("details"), dict) else {}
    tech_comps = [c for c in (((details.get("technical_setup") or {}).get("components")) or []) if isinstance(c, dict)]
    content_details = scores.get("content_quality_details") if isinstance(scores.get("content_quality_details"), dict) else {}
    crawler = scores.get("crawler_access") if isinstance(scores.get("crawler_access"), dict) else {}
    platform_rows = [r for r in (scores.get("platform_readiness") or []) if isinstance(r, dict)]
    live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else {}
    good_min = _good_score_min()

    def priority_for(score: float) -> str:
        return "High" if score < 40 else "Medium"

    def unique_actions(values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            key = re.sub(r"[^\w]+", " ", value.lower()).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(value)
        return out

    # ── AI Visibility groups ──
    low_vis_chatbots: list[dict[str, Any]] = []
    low_vis_overviews: list[dict[str, Any]] = []
    try:
        from api.export_builders import _map_prompt_topics, _active_platforms, _brand_mentioned

        all_platforms = _active_platforms(live) or list(PRIMARY_PLATFORMS)
        chatbot_platforms = [pk for pk in all_platforms if pk in {"gemini", "openai", "claude"}]
        overview_platforms = [pk for pk in all_platforms if pk == "google_aio"]
        brand = str(ctx.get("brand_name") or live.get("brand_name") or "Brand")
        tokens = list(live.get("brand_match_tokens") or [brand])
        mapped = _map_prompt_topics(ctx, [p for p in live.get("per_prompt") or [] if isinstance(p, dict)])
        by_topic: dict[str, list[dict[str, Any]]] = {}
        for item in mapped:
            by_topic.setdefault(item["topic"], []).append(item["row"])

        def _low_vis_for(platforms: list[str]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for topic, rows in by_topic.items():
                total = hits = 0
                for row in rows:
                    for pk in platforms:
                        resp = str(row.get(f"{pk}_response") or "")
                        if not resp:
                            continue
                        total += 1
                        scores_row = row.get(f"mention_scores_{pk}") if isinstance(row.get(f"mention_scores_{pk}"), dict) else {}
                        if float(scores_row.get("brand_signal") or 0) > 0 or _brand_mentioned(resp, brand, tokens):
                            hits += 1
                vis = (100.0 * hits / total) if total else 0.0
                if total and _is_ok_or_below(vis):
                    out.append({
                        "title": topic,
                        "detail": (
                            f"Your brand appeared in {hits} of {total} analysed responses for this topic "
                            f"({vis:.0f}% visibility)."
                        ),
                        "actions": [
                            f'Create or strengthen content that directly answers the priority questions associated with “{topic}”.',
                            "Use clear question-led headings, concise answer passages, supporting evidence, and internal links to the most relevant commercial pages.",
                        ],
                        "priority": priority_for(vis),
                        "score": vis,
                    })
            return _prepare_recommendation_items(out)

        low_vis_chatbots = _low_vis_for(chatbot_platforms)
        low_vis_overviews = _low_vis_for(overview_platforms)
    except Exception:
        pass

    neg_sent: list[dict[str, Any]] = []
    try:
        from insights_llm import load_cached_sentiment

        probe_path = audit_dir / "prompt_performance_live_probe.json"
        cached = load_cached_sentiment(audit_dir, probe_path) if probe_path.is_file() else None
        sentiment = cached if isinstance(cached, dict) else {}
        by_cat = sentiment.get("by_category") or (sentiment.get("sentiment") or {}).get("by_category") or []
        for row in by_cat:
            if not isinstance(row, dict):
                continue
            label = str(row.get("sentiment") or "").lower()
            if "negative" not in label:
                continue
            category = str(row.get("category") or "Category")
            neg_sent.append({
                "title": category,
                "detail": str(row.get("summary") or "AI responses show negative sentiment for this category."),
                "actions": [
                    f'Review the recurring concerns associated with “{category}” and publish content that addresses them directly.',
                    "Support corrective claims with verifiable evidence, transparent limitations, and clear customer guidance.",
                ],
                "priority": "High",
            })
        neg_sent = _prepare_recommendation_items(neg_sent)
    except Exception:
        pass

    # ── Technical groups ──
    crawler_items: list[dict[str, Any]] = []
    for row in crawler.get("rows") or []:
        if not isinstance(row, dict) or row.get("aligned") is not False:
            continue
        rec = str(row.get("recommendation") or "ALLOW")
        crawler_name = str(row.get("crawler") or "crawler")
        crawler_items.append({
            "title": f"{rec} {crawler_name}",
            "detail": str(row.get("reason") or ""),
            "actions": [
                f"Update robots.txt so {crawler_name} "
                + ("can fetch the intended public pages." if rec == "ALLOW" else "is blocked in line with the recommended policy."),
                "Re-test the live robots.txt rules after deployment.",
            ],
            "priority": "High" if int(row.get("tier") or 99) <= 2 else "Medium",
        })
    policy = unique_actions([str(x) for x in (crawler.get("improvements") or []) if str(x).strip()])
    if policy:
        crawler_items.append({
            "title": "Crawler policy and discovery",
            "detail": "Additional access or discovery findings recorded by the crawler assessment.",
            "actions": policy,
            "priority": "High",
        })
    crawler_items = _prepare_recommendation_items(crawler_items)

    citability_items: list[dict[str, Any]] = []
    for comp in tech_comps:
        key = str(comp.get("key") or "")
        if key not in {"ai_citability", "ai_search_success", "query_coverage_footprint"}:
            continue
        if key == "ai_search_success" and comp.get("criteria"):
            for criterion in comp.get("criteria") or []:
                if not isinstance(criterion, dict):
                    continue
                sc = float(criterion.get("score") or 0)
                if not _is_ok_or_below(sc):
                    continue
                actions = unique_actions([str(a) for a in (criterion.get("improvements") or []) if str(a).strip()])
                if not actions:
                    continue
                citability_items.append({
                    "title": str(criterion.get("title") or "Criterion"),
                    "detail": f"{comp.get('title')} criterion, currently {sc:.0f}/100.",
                    "actions": actions,
                    "priority": priority_for(sc),
                    "score": sc,
                })
            continue
        actions = unique_actions([str(a) for a in (comp.get("improvements") or []) if str(a).strip()])
        if not actions:
            continue
        sc = float(comp.get("score") or 0)
        if not _is_ok_or_below(sc):
            continue
        citability_items.append({
            "title": str(comp.get("title") or key),
            "detail": str(comp.get("finding_summary") or comp.get("detail") or ""),
            "actions": actions,
            "priority": priority_for(sc),
            "score": sc,
        })
    citability_items = _prepare_recommendation_items(citability_items)

    platform_items: list[dict[str, Any]] = []
    per_platform = (scores.get("prompt_metrics") or {}).get("per_platform") if isinstance(scores.get("prompt_metrics"), dict) else {}
    if not isinstance(per_platform, dict):
        per_platform = {}
    platform_cfg = [
        ("gemini", "gemini", "Gemini", "chatbots"),
        ("openai", "chatgpt", "ChatGPT / OpenAI", "chatbots"),
        ("google_aio", "aio", "Google AI Overviews", "overviews"),
        ("claude", "claude", "Claude (Anthropic)", "chatbots"),
        ("perplexity", "perplexity", "Perplexity", "chatbots"),
        ("copilot", "copilot", "Microsoft Copilot", "chatbots"),
    ]
    base_by_key = {str(r.get("key") or ""): r for r in platform_rows}
    for probe_key, base_key, label, _surface in platform_cfg:
        base = base_by_key.get(base_key) or base_by_key.get(probe_key)
        probe = per_platform.get(probe_key) if isinstance(per_platform.get(probe_key), dict) else None
        has_probe = bool(probe and float(probe.get("response_count") or 0) > 0)
        if has_probe:
            vis = float(probe.get("visibility_pct") or 0)
            sov = float(probe.get("sov_pct") or 0)
            base_score = float((base or {}).get("score") or 50)
            sc = min(100.0, round(0.55 * vis + 0.25 * sov + 0.20 * base_score))
            detail = f"{vis:.0f}% response visibility and a combined readiness score of {sc}/100."
        elif base and base.get("score") is not None:
            sc = round(float(base.get("score") or 0))
            detail = f"{sc}/100 technical readiness. Prompt visibility data is not available for this platform."
        else:
            continue
        if not _is_ok_or_below(float(sc)):
            continue
        gap = str((base or {}).get("gap") or "").strip()
        platform_items.append({
            "title": label,
            "detail": detail,
            "actions": unique_actions([gap] if gap else ["Improve platform-specific technical and content readiness."]),
            "priority": priority_for(float(sc)),
            "score": float(sc),
            "surface": _surface,
        })
    platform_items = _prepare_recommendation_items(platform_items)
    platform_chatbots = [item for item in platform_items if item.get("surface") == "chatbots"]
    platform_overviews = [item for item in platform_items if item.get("surface") == "overviews"]

    # ── Content groups ──
    eeat_items: list[dict[str, Any]] = []
    for row in content_details.get("eeat") or []:
        if not isinstance(row, dict):
            continue
        sc = float(row.get("score") or 0)
        if not _is_ok_or_below(sc):
            continue
        name = str(row.get("name") or "E-E-A-T")
        eeat_items.append({
            "title": name,
            "detail": f"{row.get('what_it_means') or ''} Current score: {sc:.0f}/100.".strip(),
            "actions": [
                "Publish first-hand evidence, credentials, and transparent sources on priority pages.",
                "Keep claims specific, verifiable, and consistent across the site.",
            ],
            "priority": priority_for(sc),
            "score": sc,
        })
    eeat_items = _prepare_recommendation_items(eeat_items)

    structure_items: list[dict[str, Any]] = []
    structure_actions = {
        "original_information_gain": [
            "Add original research, proprietary data, expert analysis, comparisons, or tested examples that are not available elsewhere.",
            "State the new insight clearly near the top of each priority page.",
        ],
        "passage_answerability": [
            "Place concise, self-contained answers immediately below question-led headings.",
            "Define the subject, answer the question, and support the answer within the same passage.",
        ],
        "content_formatting": [
            "Use descriptive heading levels, short paragraphs, lists, and comparison tables where they improve comprehension.",
            "Keep each section focused on one intent so AI systems can extract it without surrounding context.",
        ],
    }
    for row in content_details.get("structure_answerability") or []:
        if not isinstance(row, dict):
            continue
        sc = float(row.get("score") or 0)
        if not _is_ok_or_below(sc):
            continue
        key = str(row.get("key") or "")
        structure_items.append({
            "title": str(row.get("title") or key),
            "detail": f"{row.get('description') or ''} Current score: {sc:.0f}/100.".strip(),
            "actions": structure_actions.get(key) or [str(row.get("empty_message") or "Improve this criterion across priority content templates.")],
            "priority": priority_for(sc),
            "score": sc,
        })
    structure_items = _prepare_recommendation_items(structure_items)

    schema_items: list[dict[str, Any]] = []
    schema = content_details.get("schema_entity") if isinstance(content_details.get("schema_entity"), dict) else {}
    schema_imps = unique_actions([str(x) for x in (schema.get("improvements") or []) if str(x).strip()])
    schema_score = float(schema.get("score") or 0) if isinstance(schema.get("score"), (int, float)) else None
    if schema_imps and (schema_score is None or _is_ok_or_below(schema_score)):
        schema_items.append({
            "title": "Schema and entity coverage",
            "detail": str(schema.get("summary") or ""),
            "actions": schema_imps,
            "priority": priority_for(schema_score if schema_score is not None else 50),
            "score": schema_score,
        })
    schema_items = _prepare_recommendation_items(schema_items)

    brand_items: list[dict[str, Any]] = []
    brand_auth = content_details.get("brand_visibility_authority") if isinstance(content_details.get("brand_visibility_authority"), dict) else {}
    if brand_auth:
        brand_score = float(brand_auth.get("score") or 0) if isinstance(brand_auth.get("score"), (int, float)) else None
        if brand_score is None or _is_ok_or_below(brand_score):
            actions = unique_actions([str(x) for x in (brand_auth.get("improvements") or []) if str(x).strip()])
            for row in brand_auth.get("rows") or []:
                if isinstance(row, dict) and not row.get("present") and row.get("platform"):
                    actions.append(f"Create or complete the official {row.get('platform')} presence and keep brand details consistent with the website.")
            actions = unique_actions(actions)
            if actions:
                brand_items.append({
                    "title": "Third-party brand authority",
                    "detail": (
                        f"Authority signals assessed for “{brand_auth.get('brand_query')}”."
                        if brand_auth.get("brand_query")
                        else "Third-party sources do not yet provide enough corroborating brand signals."
                    ),
                    "actions": actions,
                    "priority": priority_for(brand_score if brand_score is not None else 50),
                    "score": brand_score,
                })
    brand_items = _prepare_recommendation_items(brand_items)

    tabs = [
        ("AI Visibility", [
            ("Low visibility topics — Chatbots",
             f"Topics at OK or below ({good_min - 1}/100 or less) across Gemini, ChatGPT, and Claude, ordered from the largest gap.",
             low_vis_chatbots, "No tested chatbot topic is at OK or below."),
            ("Low visibility topics — AI Overviews",
             f"Topics at OK or below ({good_min - 1}/100 or less) in Google AI Overviews, ordered from the largest gap.",
             low_vis_overviews, "No tested AI Overview topic is at OK or below."),
            ("Low visibility platforms — Chatbots",
             f"Improvement actions for chatbot platforms scoring OK or below (below {good_min}/100).",
             platform_chatbots, "No chatbot platform is currently at OK or below."),
            ("Low visibility platforms — AI Overviews",
             f"Improvement actions for AI Overview platforms scoring OK or below (below {good_min}/100).",
             platform_overviews, "No AI Overview platform is currently at OK or below."),
            ("Negative sentiment categories", "Categories where AI responses contain concerns or unfavourable brand framing.", neg_sent,
             "No negative sentiment category was detected."),
        ]),
        ("Technical Setup", [
            ("Crawler access changes", "Robots.txt rules that do not match the recommended access policy.", crawler_items,
             "Current crawler access rules align with the recorded recommendations."),
            ("Citability improvements", "The current What needs work findings from the Citability report.", citability_items,
             "No material Citability improvement is recorded."),
        ]),
        ("Content Quality", [
            ("E-E-A-T content required", f"Content formats to strengthen E-E-A-T criteria scoring OK or below (below {good_min}/100).", eeat_items,
             "No E-E-A-T criterion is at OK or below."),
            ("Content structure and answerability", "Changes that make priority pages more original, extractable, and answer-ready.", structure_items,
             "No content structure criterion is at OK or below."),
            ("Schema and entity markup", "Structured data changes drawn from the current schema assessment.", schema_items,
             "No material schema or entity markup change is recorded."),
            ("Brand visibility and authority", "Actions that improve third-party corroboration of the brand entity.", brand_items,
             "No material brand authority action is recorded."),
        ]),
    ]

    parts: list[str] = []
    for tab_label, groups in tabs:
        parts.append(
            f"<div style='padding:20px 24px 4px;border-top:1px solid rgba(0,0,0,.06)'>"
            f"<div style='font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;"
            f"color:#2563eb;margin-bottom:8px'>{ESC(tab_label)}</div>"
        )
        for title, desc, items, empty in groups:
            parts.append(_rec_group_html(title, desc, items, empty))
        parts.append("</div>")

    return _shell(
        "Recommendations",
        "Prioritised actions generated from the latest visibility probes and audit scores.",
        "".join(parts),
    )


def enhance_summary_pillar_descriptions(scores: dict[str, Any], brand: str, visibility_pct: Any) -> tuple[str, str, str]:
    """Return (ai_desc, tech_desc, content_desc) matching React SummarySection."""
    metrics = scores.get("prompt_metrics") if isinstance(scores.get("prompt_metrics"), dict) else {}
    per_platform = metrics.get("per_platform") if isinstance(metrics.get("per_platform"), dict) else {}
    platform_count = sum(
        1
        for pk, entry in per_platform.items()
        if pk != "claude" and isinstance(entry, dict) and entry.get("response_count")
    )
    if isinstance(visibility_pct, (int, float)):
        if platform_count:
            ai_desc = (
                f"{brand} is mentioned in {float(visibility_pct):.0f}% of analysed responses "
                f"across {platform_count} platforms."
            )
        else:
            ai_desc = f"{brand} is mentioned in {float(visibility_pct):.0f}% of analysed responses."
    else:
        ai_desc = "Run probes to calculate AI Visibility score."

    tech_components = ((scores.get("details") or {}).get("technical_setup") or {}).get("components") or []
    content_components = ((scores.get("details") or {}).get("content_structure") or {}).get("components") or []
    if not content_components:
        cqd = scores.get("content_quality_details") if isinstance(scores.get("content_quality_details"), dict) else {}
        content_components = [c for c in (cqd.get("components") or []) if isinstance(c, dict)]
    tech_desc = _pillar_finding_summary(
        [c for c in tech_components if isinstance(c, dict)],
        "Technical findings are not available for this audit.",
    )
    content_desc = _pillar_finding_summary(
        content_components,
        "Content-quality findings are not available for this audit.",
    )
    return ai_desc, tech_desc, content_desc


# ── AI Traffic Dashboard (GA4 + Estimated AI impact chart) ─────────────────────


def _load_ai_impact_estimate(audit_dir: Path) -> dict[str, Any] | None:
    """Load only the estimate explicitly attached to this audit."""
    from api.ai_impact import load_ai_impact_estimate

    return load_ai_impact_estimate(audit_dir)


def _svg_ai_impact_chart(weekly: list[dict[str, Any]]) -> str:
    """Simple multi-series SVG line chart for Estimated AI impact."""
    if len(weekly) < 2:
        return ""
    width, height = 720, 260
    pad_l, pad_r, pad_t, pad_b = 48, 16, 20, 36
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    hierarchical = any(
        isinstance(point.get("seo_uncapped_counterfactual"), dict)
        for point in weekly
    )
    keys = (
        [
            ("seo_sessions", "#0f766e", "SEO actual"),
            ("seo_uncapped_counterfactual", "#14b8a6", "SEO counterfactual"),
            ("direct_sessions", "#1d4ed8", "Direct actual"),
            ("direct_uncapped_counterfactual", "#60a5fa", "Direct counterfactual"),
        ]
        if hierarchical
        else [
            ("total_sessions", "#6b7280", "Total sessions"),
            ("ai_sessions", "#2563eb", "Tracked AI"),
            ("estimated_ai_sessions", "#7c3aed", "Estimated AI"),
            ("counterfactual_sessions", "#d97706", "Counterfactual"),
        ]
    )

    def chart_value(point: dict[str, Any], key: str) -> float:
        value = point.get(key)
        if isinstance(value, dict):
            value = value.get("posterior_mean")
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
    values: list[float] = []
    for point in weekly:
        for key, _, _ in keys:
            values.append(chart_value(point, key))
    if not values:
        return ""
    vmin, vmax = min(values), max(values)
    if vmax <= vmin:
        vmax = vmin + 1

    def x_at(i: int) -> float:
        return pad_l + (plot_w * i / max(len(weekly) - 1, 1))

    def y_at(v: float) -> float:
        return pad_t + plot_h * (1 - (v - vmin) / (vmax - vmin))

    paths = []
    for key, color, _label in keys:
        pts = []
        for i, point in enumerate(weekly):
            val = chart_value(point, key)
            pts.append(f"{x_at(i):.1f},{y_at(val):.1f}")
        paths.append(
            f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{' '.join(pts)}'/>"
        )
    legend = []
    lx = pad_l
    for _key, color, label in keys:
        legend.append(
            f"<rect x='{lx}' y='{height - 18}' width='10' height='10' fill='{color}'/>"
            f"<text x='{lx + 14}' y='{height - 9}' font-size='10' fill='#4b5563'>{ESC(label)}</text>"
        )
        lx += 12 + len(label) * 6.2 + 16
    first = str(weekly[0].get("week") or "")
    last = str(weekly[-1].get("week") or "")
    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        f"xmlns='http://www.w3.org/2000/svg' role='img' aria-label='Estimated AI impact'>"
        f"<rect x='{pad_l}' y='{pad_t}' width='{plot_w}' height='{plot_h}' fill='#fafafa' stroke='#e5e7eb'/>"
        + "".join(paths)
        + f"<text x='{pad_l}' y='{height - 22}' font-size='10' fill='#9ca3af'>{ESC(first)}</text>"
        f"<text x='{width - pad_r}' y='{height - 22}' font-size='10' fill='#9ca3af' text-anchor='end'>{ESC(last)}</text>"
        + "".join(legend)
        + "</svg>"
    )


def _fmt_compact_estimate(n: float) -> str:
    """Compact figure with ~2 significant digits (mirrors web estimateRange helper)."""
    if not math.isfinite(n):
        return "—"
    abs_n = abs(float(n))
    sign = "-" if n < 0 else ""
    if abs_n >= 1_000_000_000:
        scaled, suffix = abs_n / 1_000_000_000, "B"
    elif abs_n >= 1_000_000:
        scaled, suffix = abs_n / 1_000_000, "M"
    elif abs_n >= 1_000:
        scaled, suffix = abs_n / 1_000, "k"
    else:
        if abs_n == 0:
            return "0"
        return f"{float(n):.2g}"
    return f"{sign}{scaled:.2g}{suffix}"


def _indirect_sessions_range_label(estimate: dict[str, Any]) -> str | None:
    """±10% range around central indirect sessions (sessions_overall_net − direct)."""
    overall = estimate.get("sessions_overall_net")
    if not isinstance(overall, dict):
        return None
    try:
        central_net = float(overall.get("central"))
        direct = float(estimate.get("direct_ai_sessions") or 0)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(central_net):
        return None
    central = central_net - direct
    if central == 0:
        return _fmt_compact_estimate(0)
    a, b = central * 0.9, central * 1.1
    low, high = min(a, b), max(a, b)
    return f"{_fmt_compact_estimate(low)} – {_fmt_compact_estimate(high)}"


def _probability_of_result_percent(p_value: Any) -> int | None:
    """(1 − p) × 100, rounded; None when p is missing/non-finite."""
    try:
        p = float(p_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(p):
        return None
    p = min(1.0, max(0.0, p))
    return int(round((1.0 - p) * 100))


def build_ga4_traffic_export(audit_dir: Path) -> str:
    """GA4 traffic panel plus Estimated AI impact chart when available."""
    from api.export_builders import _extract_legacy_panel

    parts: list[str] = []
    panel = _extract_legacy_panel(audit_dir, "ga4-traffic")
    if panel:
        parts.append(panel)
    estimate = _load_ai_impact_estimate(audit_dir)
    if estimate and isinstance(estimate.get("weekly_series"), list) and estimate["weekly_series"]:
        from api.ai_impact import _ensure_backend_path

        _ensure_backend_path()
        from ai_impact.panel import filter_completed_weeks

        weekly = filter_completed_weeks(
            [p for p in estimate["weekly_series"] if isinstance(p, dict)]
        )
        chart = _svg_ai_impact_chart(weekly)
        posterior_outcomes = estimate.get("posterior_outcomes")
        hierarchical = isinstance(posterior_outcomes, dict)
        quality = estimate.get("model_quality_score") or estimate.get("confidence_score")
        probability = _probability_of_result_percent(estimate.get("p_value"))
        window = ""
        if estimate.get("window_start") and estimate.get("window_end"):
            window = f"{estimate.get('window_start')} to {estimate.get('window_end')}"
        meta = []
        if window:
            meta.append(f"<span class='muted'>Window: {ESC(str(window))}</span>")
        if estimate.get("category"):
            meta.append(f"<span class='muted'>Category: {ESC(str(estimate['category']))}</span>")
        if estimate.get("estimate_mode"):
            meta.append(f"<span class='muted'>Mode: {ESC(str(estimate['estimate_mode']))}</span>")
        if estimate.get("model_artifact_version"):
            meta.append(
                f"<span class='muted'>Model: {ESC(str(estimate['model_artifact_version']))}</span>"
            )
        if not hierarchical and isinstance(quality, (int, float)):
            meta.append(f"<span class='muted'>Data &amp; model quality {float(quality):.0f}%</span>")
        if not hierarchical and probability is not None:
            meta.append(f"<span class='muted'>Probability of result {probability}%</span>")
        sessions_summary = ""
        if hierarchical:
            rows = []
            for channel in ("seo", "direct"):
                outcome = posterior_outcomes.get(channel)
                if not isinstance(outcome, dict):
                    continue
                primary = outcome.get("uncapped")
                capped = outcome.get("capped")
                if not isinstance(primary, dict) or not isinstance(capped, dict):
                    continue
                def interval_label(value: dict[str, Any]) -> str:
                    return (
                        f"{_fmt_compact_estimate(float(value.get('posterior_mean') or 0))} "
                        f"({_fmt_compact_estimate(float(value.get('lower_94') or 0))} to "
                        f"{_fmt_compact_estimate(float(value.get('upper_94') or 0))})"
                    )
                rows.append(
                    "<tr>"
                    f"<th style='text-align:left;padding:6px 10px;font-weight:600'>{channel.upper()}</th>"
                    f"<td style='padding:6px 10px'>{ESC(interval_label(primary))}</td>"
                    f"<td style='padding:6px 10px'>{ESC(interval_label(capped))}</td>"
                    "</tr>"
                )
            sessions_summary = (
                "<table style='margin:12px 0 0;border-collapse:collapse;font-size:13px'>"
                "<thead><tr><th style='padding:6px 10px'>Channel</th>"
                "<th style='padding:6px 10px'>Primary mean (94% CI)</th>"
                "<th style='padding:6px 10px'>Capped sensitivity (94% CI)</th>"
                f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
            )
        else:
            try:
                direct = float(estimate.get("direct_ai_sessions") or 0)
            except (TypeError, ValueError):
                direct = None
            indirect_label = _indirect_sessions_range_label(estimate)
            if direct is not None or indirect_label:
                rows = []
                if direct is not None:
                    rows.append(
                        "<tr><th style='padding:6px 10px'>Direct (tracked)</th>"
                        f"<td style='padding:6px 10px'>{ESC(f'{round(direct):,}')}</td></tr>"
                    )
                if indirect_label:
                    rows.append(
                        "<tr><th style='padding:6px 10px'>Indirect estimate</th>"
                        f"<td style='padding:6px 10px'>{ESC(indirect_label)}</td></tr>"
                    )
                sessions_summary = (
                    "<table style='margin:12px 0 0;border-collapse:collapse;font-size:13px'>"
                    f"<tbody>{''.join(rows)}</tbody></table>"
                )
        parts.append(
            "<div style='padding:18px 24px 24px;border-top:1px solid rgba(0,0,0,.06)'>"
            "<h3 style='margin:0 0 4px;font-size:15px;font-weight:700'>Estimated AI impact</h3>"
            "<p class='muted' style='margin:0 0 12px'>SEO and Direct actual sessions versus the "
            "posterior counterfactual with AI adoption frozen at baseline.</p>"
            + (f"<div style='display:flex;gap:16px;flex-wrap:wrap;margin-bottom:10px'>{''.join(meta)}</div>" if meta else "")
            + (f"<div style='overflow-x:auto'>{chart}</div>" if chart else
               "<p class='muted'>Weekly series is not available for this estimate.</p>")
            + sessions_summary
            + "</div>"
        )
    if not parts:
        return _shell(
            "AI Traffic Dashboard",
            "GA4 AI traffic and estimated impact.",
            "<div class='export-empty'>GA4 traffic export not available. Connect GA4 and run an estimate to include the chart.</div>",
        )
    return _shell(
        "AI Traffic Dashboard",
        "GA4 AI traffic and estimated impact.",
        "".join(parts),
    )
