"""HTML export service - all-pages report with redesigned prompt performance section."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

SOV_GREEN = "#00b894"
SOV_BLUE = "#0984e3"
PLATFORM_COLORS = {"gemini": "#4285F4", "openai": "#10a37f", "claude": "#D97706"}
PLATFORM_LABELS = {"gemini": "Gemini", "openai": "OpenAI", "claude": "Claude"}
PANEL_LABELS = {
    "summary": "Summary",
    "ga4-traffic": "AI Traffic (GA4)",
    "recommendations": "Recommendations",
    "competitors": "Competitor Comparison",
    "ai-visibility": "AI Visibility",
    "technical": "Technical Setup",
    "content": "Content Quality",
    "samples": "Sample Scripts",
}

_TAB_JS = re.compile(
    r"<script[^>]*>\s*\(function\s*\(\)\s*\{"
    r"[^}]*var panels = document\.querySelectorAll"
    r".*?\}\)\(\);\s*</script>",
    re.DOTALL,
)
_HIDDEN_ATTR = re.compile(r"(<div[^>]*report-tab-panel[^>]*?)\s+hidden(\s*>)")
_TAB_PANEL_TAG = re.compile(r"<div[^>]*report-tab-panel[^>]*>")


def _load_json(path: Path) -> Optional[dict]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return None
    return None


def _section_divider(label: str) -> str:
    return (
        "<div style='display:flex;align-items:center;gap:14px;padding:32px 0 18px'>"
        "<hr style='flex:1;border:none;border-top:1px solid #ccc'>"
        "<span style='font-size:11px;font-weight:700;letter-spacing:.12em;"
        "text-transform:uppercase;color:#888'>" + label + "</span>"
        "<hr style='flex:1;border:none;border-top:1px solid #ccc'>"
        "</div>"
    )


def _add_divider(m: re.Match) -> str:  # type: ignore[type-arg]
    tag = m.group(0)
    val = re.search(r"data-tab-panel=['\"]([^'\"]+)['\"]", tag)
    label = PANEL_LABELS.get(val.group(1) if val else "", "") if val else ""
    return _section_divider(label) + tag


def _sentiment_chip(s: str) -> str:
    colors: dict[str, tuple[str, str]] = {
        "Positive": (SOV_GREEN, "#e8f8f5"),
        "Mixed": ("#e17055", "#fdf0ed"),
        "Negative": ("#d63031", "#ffeaea"),
        "Neutral": ("#636e72", "#f0f0f0"),
    }
    fg, bg = colors.get(s, ("#636e72", "#f0f0f0"))
    return (
        "<span style='background:" + bg + ";color:" + fg + ";padding:2px 10px;"
        "border-radius:10px;font-size:11px;font-weight:700'>" + s + "</span>"
    )


def _sov_bar(brand_pct: float, comp_pct: float) -> str:
    b = round(brand_pct or 0)
    c = round(comp_pct or 0)
    if b == 0 and c == 0:
        return "<span style='font-size:12px;color:#aaa'>No mentions detected</span>"
    return (
        "<div style='display:flex;align-items:center;gap:8px;font-size:12px'>"
        "<span style='color:" + SOV_GREEN + ";font-weight:600;min-width:36px'>" + str(b) + "%</span>"
        "<div style='display:flex;height:8px;border-radius:4px;overflow:hidden;width:180px;background:#e5e2de'>"
        "<div style='width:" + str(b) + "%;background:" + SOV_GREEN + "'></div>"
        "<div style='width:" + str(c) + "%;background:" + SOV_BLUE + "'></div>"
        "</div>"
        "<span style='color:" + SOV_BLUE + "'>" + str(c) + "% competitors</span>"
        "</div>"
    )


def build_prompt_performance_section(audit_dir: Path) -> str:
    """Return an HTML string for the fully redesigned prompt performance section."""
    probe_data = _load_json(audit_dir / "prompt_performance_live_probe.json")
    sent_data = _load_json(audit_dir / "prompt_performance_sentiment.json")

    if not probe_data:
        return "<p style='color:#888;padding:24px'>Prompt performance data not available.</p>"

    probe = probe_data.get("live_probe", {})
    try:
        from api.probe_platforms import sanitize_live_probe

        probe = sanitize_live_probe(probe if isinstance(probe, dict) else {})
        active = probe.get("active_platforms") or ["gemini", "openai", "claude"]
    except Exception:
        active = ["gemini", "openai", "claude"]
    per_prompt = probe.get("per_prompt", [])
    brand = probe.get("brand_name", probe_data.get("highlight_brand", "Brand"))
    site_url = probe.get("brand_site_url", "")
    aggregate = probe.get("aggregate", {})
    detected = probe.get("reply_detected_brands", [])
    sentiment = (sent_data or {}).get("sentiment", {})
    try:
        from api.prompt_performance import _build_context_response
        from insights_llm import PromptSentimentResponse, filter_sentiment_for_probed_rows

        ctx = _build_context_response(audit_dir)
        probed_rows = ctx.get("probed_pss_rows")
        if isinstance(probed_rows, list) and sentiment:
            sentiment = filter_sentiment_for_probed_rows(
                PromptSentimentResponse.model_validate(sentiment),
                probed_rows,
            ).model_dump()
    except Exception:
        pass

    # ── 1. Header: brand / website / tracked competitors ──────────────────
    comp_names = ", ".join(
        d.get("brand_name", "") for d in detected[:10] if d.get("brand_name")
    )
    hcells = (
        "<div style='padding:14px 20px;border-right:1px solid #e5e2de'>"
        "<div style='font-size:10px;font-weight:700;text-transform:uppercase;"
        "letter-spacing:.1em;color:#888'>Brand</div>"
        "<div style='font-weight:700;color:#1a1a1a;margin-top:3px'>" + brand + "</div></div>"
    )
    if site_url:
        hcells += (
            "<div style='padding:14px 20px;border-right:1px solid #e5e2de'>"
            "<div style='font-size:10px;font-weight:700;text-transform:uppercase;"
            "letter-spacing:.1em;color:#888'>Website</div>"
            "<div style='color:#0984e3;margin-top:3px'>" + site_url + "</div></div>"
        )
    if detected:
        hcells += (
            "<div style='padding:14px 20px;flex:1'>"
            "<div style='font-size:10px;font-weight:700;text-transform:uppercase;"
            "letter-spacing:.1em;color:#888'>Tracked competitors</div>"
            "<div style='color:#555;margin-top:3px'>" + (comp_names or "None detected") + "</div></div>"
        )
    header_html = (
        "<div style='display:flex;flex-wrap:wrap;gap:0;margin-bottom:20px;background:#fff;"
        "border-radius:10px;border:1px solid #e5e2de;overflow:hidden;font-size:13px'>"
        + hcells + "</div>"
    )

    # ── 2. Overall AI sentiment ───────────────────────────────────────────
    overall_s = sentiment.get("overall_sentiment", "")
    overall_summary = sentiment.get("overall_summary", "")
    oc = {"Positive": SOV_GREEN, "Mixed": "#e17055", "Negative": "#d63031"}.get(overall_s, "#636e72")
    sentiment_html = ""
    if overall_s:
        sentiment_html = (
            "<div style='background:#0d0d0d;border-radius:10px;padding:20px 24px;"
            "margin-bottom:20px;color:#fff'>"
            "<div style='display:flex;align-items:center;gap:10px;margin-bottom:10px'>"
            "<span style='font-size:14px;font-weight:700'>Overall AI Sentiment</span>"
            "<span style='background:" + oc + "40;color:" + oc + ";padding:2px 12px;"
            "border-radius:10px;font-size:12px;font-weight:700'>" + overall_s + "</span>"
            "</div>"
            "<p style='font-size:13px;color:#ccc;line-height:1.7;margin:0'>"
            + overall_summary + "</p></div>"
        )

    # ── 3. Sentiment by category ──────────────────────────────────────────
    cat_html = ""
    by_cat = sentiment.get("by_category", [])
    if by_cat:
        cards = ""
        for cat in by_cat:
            cards += (
                "<div style='background:#fff;border-radius:10px;padding:14px 16px;border:1px solid #e5e2de'>"
                "<div style='font-size:13px;font-weight:700;color:#1a1a1a;margin-bottom:6px'>"
                + cat.get("category", "") + "</div>"
                + _sentiment_chip(cat.get("sentiment", ""))
                + "<p style='font-size:12px;color:#555;margin-top:8px;line-height:1.6;margin-bottom:0'>"
                + cat.get("summary", "") + "</p></div>"
            )
        cat_html = (
            "<div style='margin-bottom:24px'>"
            "<div style='font-size:13px;font-weight:700;color:#1a1a1a;margin-bottom:12px'>"
            "Sentiment by Category</div>"
            "<div style='display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px'>"
            + cards + "</div></div>"
        )

    # ── 4. Share of voice (all prompts combined) ──────────────────────────
    sov_rows = ""
    for pk in ("gemini", "openai", "claude"):
        if pk not in active:
            continue
        agg = aggregate.get(pk, {})
        b_pct = float(agg.get("brand_share_pct") or 0)
        c_pct = float(agg.get("competitor_share_pct") or 0)
        if b_pct == 0 and c_pct == 0:
            continue
        col = PLATFORM_COLORS[pk]
        lbl = PLATFORM_LABELS[pk]
        sov_rows += (
            "<div style='display:flex;align-items:center;gap:12px;padding:10px 0;"
            "border-bottom:1px solid #f0ede9'>"
            "<span style='background:" + col + "18;color:" + col + ";padding:2px 10px;"
            "border-radius:8px;font-size:11px;font-weight:700;min-width:68px;text-align:center'>"
            + lbl + "</span>"
            "<div style='flex:1'>" + _sov_bar(b_pct, c_pct) + "</div></div>"
        )
    sov_html = ""
    if sov_rows:
        sov_html = (
            "<div style='background:#fff;border-radius:10px;padding:16px 20px;"
            "border:1px solid #e5e2de;margin-bottom:24px'>"
            "<div style='font-size:13px;font-weight:700;color:#1a1a1a;margin-bottom:12px'>"
            "Share of voice from live replies (all prompts combined)</div>"
            + sov_rows + "</div>"
        )

    # ── 5. Per-prompt responses (3 shown by default) ──────────────────────
    cards_html = ""
    for row in per_prompt:
        idx = str(row.get("index", ""))
        prompt_text = row.get("prompt", "")
        indicators = ""
        details_html = ""
        for pk in ("gemini", "openai", "claude"):
            if pk not in active:
                continue
            resp = row.get(pk + "_response") or ""
            if not resp:
                continue
            col = PLATFORM_COLORS[pk]
            lbl = PLATFORM_LABELS[pk]
            mentioned = brand.lower() in resp.lower()
            ic = SOV_GREEN if mentioned else "#e17055"
            b_pct = float(row.get(pk + "_brand_mention_pct") or 0)
            c_pct = float(row.get(pk + "_competitor_mention_pct") or 0)
            snip = re.sub(r"\*+", "", resp[:600]).strip()
            tick = "&#10003;" if mentioned else "&#10007;"
            indicators += (
                "<span style='display:inline-flex;align-items:center;gap:4px;"
                "padding:2px 10px;border-radius:8px;font-size:11px;font-weight:600;"
                "background:" + col + "15;color:" + col + "'>"
                + lbl + "&nbsp;<span style='color:" + ic + "'>" + tick + "</span></span>"
            )
            details_html += (
                "<div style='border-left:3px solid " + col + ";padding:10px 14px;"
                "background:#fafaf9;border-radius:0 8px 8px 0;margin-bottom:10px'>"
                "<div style='display:flex;align-items:center;"
                "justify-content:space-between;margin-bottom:6px'>"
                "<span style='background:" + col + "18;color:" + col + ";padding:1px 8px;"
                "border-radius:6px;font-size:11px;font-weight:700'>" + lbl + "</span>"
                "<span style='font-size:11px;color:" + ic + ";font-weight:600'>"
                + ("&#10003; " + brand + " mentioned" if mentioned else "&#10007; Not mentioned")
                + "</span></div>"
                + _sov_bar(b_pct, c_pct)
                + "<p style='font-size:12px;color:#444;line-height:1.65;"
                "margin-top:8px;margin-bottom:0'>"
                + snip + ("&hellip;" if len(resp) > 600 else "")
                + "</p></div>"
            )
        cards_html += (
            "<div class='pp-card' style='background:#f5f4f2;border-radius:10px;"
            "padding:16px;margin-bottom:12px'>"
            "<div style='display:flex;align-items:flex-start;gap:10px;margin-bottom:10px'>"
            "<span style='font-size:11px;font-weight:700;color:#888;"
            "white-space:nowrap;padding-top:1px'>Q" + idx + "</span>"
            "<span style='font-size:13px;font-weight:600;color:#1a1a1a;line-height:1.4'>"
            + prompt_text + "</span></div>"
            "<div style='display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px'>"
            + indicators + "</div>"
            "<details><summary style='font-size:12px;font-weight:600;color:#555;"
            "list-style:none;cursor:pointer;display:flex;align-items:center;"
            "gap:4px;user-select:none'>"
            "<span class='pp-arr' style='font-size:10px'>&#9654;</span>"
            "&nbsp;View AI responses</summary>"
            "<div style='margin-top:12px'>" + details_html + "</div>"
            "</details></div>"
        )

    n = len(per_prompt)
    expand_btn = ""
    if n > 3:
        expand_btn = (
            "<button onclick='ppShowAll()' id='pp-btn'"
            " style='display:block;margin:8px auto 0;padding:8px 24px;"
            "background:#0d0d0d;color:#fff;border:none;border-radius:8px;"
            "font-size:13px;font-weight:600;cursor:pointer'>"
            "Show all " + str(n) + " prompts</button>"
        )

    n_str = str(n)
    prompts_section = (
        "<div>"
        "<div style='display:flex;align-items:center;"
        "justify-content:space-between;margin-bottom:12px'>"
        "<span style='font-size:13px;font-weight:700;color:#1a1a1a'>"
        "Per-Prompt AI Responses</span>"
        "<span style='font-size:12px;color:#888' id='pp-count'>"
        "Showing 3 of " + n_str + "</span></div>"
        "<div id='pp-list'>" + cards_html + "</div>"
        + expand_btn
        + "<script>(function(){"
        "var c=document.querySelectorAll('.pp-card');"
        "for(var i=3;i<c.length;i++)c[i].style.display='none';"
        "document.querySelectorAll('details').forEach(function(d){"
        "d.addEventListener('toggle',function(){"
        "var a=d.querySelector('.pp-arr');"
        "if(a)a.textContent=d.open?'\u25BC':'\u25BA';"
        "});});"
        "})();"
        "function ppShowAll(){"
        "document.querySelectorAll('.pp-card').forEach(function(c){c.style.display='';});"
        "var b=document.getElementById('pp-btn');if(b)b.style.display='none';"
        "var ct=document.getElementById('pp-count');"
        "if(ct)ct.textContent='Showing all " + n_str + " prompts';}"
        "</script></div>"
    )

    return (
        "<div style='font-family:inherit;padding:0 0 48px'>"
        + header_html
        + sentiment_html
        + sov_html
        + cat_html
        + prompts_section
        + "</div>"
    )


def generate_all_pages_html(audit_dir: Path) -> str:
    """Return complete standalone HTML with all tab sections + prompt performance."""
    report_path = audit_dir / "report.html"
    if not report_path.is_file():
        raise FileNotFoundError(f"report.html not found in {audit_dir}")

    html = report_path.read_text(encoding="utf-8", errors="replace")

    # Remove tab-switching JS
    html = _TAB_JS.sub("", html)
    # Unhide all panels
    html = _HIDDEN_ATTR.sub(r"\1\2", html)
    # Inject section dividers before each panel
    html = _TAB_PANEL_TAG.sub(_add_divider, html)
    # Hide tab navigation
    html = html.replace(
        "</head>",
        "<style>.report-tab-nav,[role='tablist']{display:none!important}</style>\n</head>",
        1,
    )
    # Append prompt performance section
    pp_section = build_prompt_performance_section(audit_dir)
    html = html.replace(
        "</body>",
        _section_divider("Prompt Performance") + pp_section + "\n</body>",
        1,
    )
    return html
