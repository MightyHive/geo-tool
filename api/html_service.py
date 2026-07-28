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
    """Return HTML matching the React Prompts table (topic rows + prompts)."""
    from api.export_builders import build_prompts_export

    return build_prompts_export(audit_dir)


def generate_all_pages_html(audit_dir: Path) -> str:
    """Full report download assembled from current React-section exporters."""
    from api.export_builders import build_all_pages_export

    return build_all_pages_export(audit_dir)


# UI section ids accepted for HTML/PDF section downloads.
# Values are canonical sidebar-matching ids; keys include legacy aliases.
SECTION_EXPORT_MAP: dict[str, str] = {
    "summary": "summary",
    "recommendations": "recommendations",
    # AI Traffic Dashboard
    "ai-traffic-dashboard": "ai-traffic-dashboard",
    "ga4-traffic": "ai-traffic-dashboard",
    "ai-impact": "ai-traffic-dashboard",
    # Competitor comparison
    "competitor-comparison": "competitor-comparison",
    "competitors": "competitor-comparison",
    # Citability (Technical setup)
    "citability": "citability",
    "ai-visibility": "citability",
    "ai-visibility-overview": "ai-visibility-overview",
    # Crawler access
    "crawler-access": "crawler-access",
    "technical": "crawler-access",
    "technical-overview": "technical-overview",
    # Content quality
    "content": "content-overview",
    "content-overview": "content-overview",
    "eeat-signals": "eeat-signals",
    "content-eeat": "eeat-signals",
    "content-structure-answerability": "content-structure-answerability",
    "content-structure": "content-structure-answerability",
    "schema-entity-markup": "schema-entity-markup",
    "content-schema": "schema-entity-markup",
    "brand-visibility-authority": "brand-visibility-authority",
    "content-brand-visibility": "brand-visibility-authority",
    # AI visibility
    "prompts": "prompts",
    "prompt_performance": "prompts",
    "competitor-visibility": "competitor-visibility",
    "competitor-performance": "competitor-visibility",
    "citations": "citations",
    "reddit-citations": "reddit-citations",
    "reddit-insights": "reddit-citations",
    "youtube-citations": "youtube-citations",
    "youtube-insights": "youtube-citations",
    "platform-readiness": "platform-readiness",
}


def resolve_export_section(section: str) -> str | None:
    """Return the canonical UI section id for export, or None if not downloadable."""
    key = (section or "").strip()
    if not key:
        return None
    return SECTION_EXPORT_MAP.get(key)


def generate_section_html(audit_dir: Path, section: str) -> tuple[str, str]:
    """
    Return (filename_slug, standalone HTML) for one report section.
    Prefers React-matching builders over legacy report.html panels.
    """
    target = resolve_export_section(section)
    if not target:
        raise ValueError(f"Section is not available for download: {section}")

    from api.export_builders import build_section_body, wrap_export_document

    label, body = build_section_body(audit_dir, target)
    html = wrap_export_document(label, body)
    slug = re.sub(r"[^\w\-]+", "-", target).strip("-") or "section"
    return slug, html
