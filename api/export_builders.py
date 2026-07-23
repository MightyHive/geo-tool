"""HTML builders for downloads that mirror the React report UI (not legacy report.html tabs)."""

from __future__ import annotations

import html as html_lib
import json
import re
from pathlib import Path
from typing import Any

from api.report_score import (
    GOOD_SCORE_MIN,
    format_report_score,
    is_ok_or_below as _is_ok_or_below,
    score_label,
)

ESC = html_lib.escape


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


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

POSITIVE_WORDS = (
    "best", "top", "recommend", "recommended", "leading", "excellent", "great",
    "trusted", "award", "premium", "outstanding", "popular", "preferred",
    "favourite", "favorite", "praised", "market leader", "highly rated",
)
NEGATIVE_WORDS = (
    "worst", "avoid", "poor", "bad", "inferior", "terrible", "disappointing",
    "overpriced", "complaint", "unreliable", "ineffective", "not recommended",
)

# Section order for full-report downloads (matches sidebar groups; Workshop excluded).
FULL_REPORT_SECTIONS: list[tuple[str, str]] = [
    ("summary", "Summary"),
    ("recommendations", "Recommendations"),
    ("ai-traffic-dashboard", "AI Traffic Dashboard"),
    ("competitor-comparison", "Competitor comparison"),
    ("ai-visibility-overview", "AI visibility overview"),
    ("prompts", "Prompts"),
    ("competitor-visibility", "Competitor visibility"),
    ("citations", "Citations"),
    ("reddit-citations", "Reddit Citations"),
    ("youtube-citations", "YouTube Citations"),
    ("technical-overview", "Technical setup overview"),
    ("crawler-access", "Crawler access"),
    ("citability", "Citability"),
    ("platform-readiness", "Platform readiness"),
    ("content-overview", "Content quality overview"),
    ("eeat-signals", "E-E-A-T Signals"),
    ("content-structure-answerability", "Content Structure & Answerability"),
    ("schema-entity-markup", "Schema & Entity Markup"),
    ("brand-visibility-authority", "Brand Visibility & Authority"),
]


def _shell(title: str, subtitle: str, body: str) -> str:
    return (
        f"<section class='export-section report-block'>"
        f"<header class='export-section-header'>"
        f"<h2 class='section-title'>{ESC(title)}</h2>"
        f"<p class='section-lead'>{ESC(subtitle)}</p>"
        f"</header>"
        f"<div class='export-section-body'>{body}</div>"
        f"</section>"
    )


def _load_report_styles_css() -> str:
    """Inline the original GEO report stylesheet when available."""
    candidates: list[Path] = []
    try:
        from geo_app_env import ASSETS_ROOT

        candidates.append(ASSETS_ROOT / "design" / "report-styles.css")
    except Exception:
        pass
    candidates.append(
        Path(__file__).resolve().parents[1] / "assets" / "design" / "report-styles.css"
    )
    for path in candidates:
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return ""


def export_page_css() -> str:
    report_css = _load_report_styles_css()
    return f"""
<style>
{_report_css_safe(report_css)}

/* Export layout — keep original report look, add print page breaks */
body.geo-report.geo-export {{
  margin: 0;
  padding: 32px 0 56px;
  background: var(--bg-light, #e8e5e0);
  color: var(--text-primary, #2d2d2d);
  font-family: var(--font-sans, "Google Sans", "Inter", system-ui, sans-serif);
  line-height: 1.55;
  letter-spacing: -0.011em;
  -webkit-font-smoothing: antialiased;
}}
.export-wrap {{
  max-width: 1120px;
  margin: 0 auto;
  padding: 0 24px;
}}
.export-page {{
  break-after: page;
  page-break-after: always;
  margin-bottom: 28px;
}}
.export-page:last-child {{
  break-after: auto;
  page-break-after: auto;
  margin-bottom: 0;
}}
.export-section.report-block {{
  background: var(--bg-white, #fff);
  border: 1px solid var(--border, rgba(0,0,0,.08));
  border-radius: var(--radius, 12px);
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,.04));
  overflow: hidden;
}}
.export-section-header {{
  padding: 20px 24px 14px;
  border-bottom: 1px solid var(--border, rgba(0,0,0,.08));
  background: rgba(255,255,255,.92);
}}
.export-section-header .section-title {{
  margin: 0;
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text-primary, #2d2d2d);
  letter-spacing: -0.02em;
}}
.export-section-header .section-lead {{
  margin: 6px 0 0;
  font-size: 14px;
  line-height: 1.55;
  color: var(--text-secondary, #5a5a5a);
}}
.export-section-body {{ padding: 0; }}
.export-empty {{
  padding: 36px 24px;
  text-align: center;
  color: var(--text-muted, #737373);
  font-size: 14px;
}}
.export-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}}
.export-table th {{
  text-align: left;
  padding: 11px 18px;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: .06em;
  text-transform: uppercase;
  color: var(--text-muted, #737373);
  background: rgba(232, 229, 224, 0.45);
  border-bottom: 1px solid var(--border, rgba(0,0,0,.08));
}}
.export-table td {{
  padding: 13px 18px;
  border-bottom: 1px solid var(--border, rgba(0,0,0,.06));
  vertical-align: top;
  color: var(--text-primary, #2d2d2d);
}}
.export-table tr:last-child td {{ border-bottom: none; }}
.topic-row td {{
  background: linear-gradient(to right, rgba(99, 102, 241, 0.08), rgba(99, 102, 241, 0.02));
  border-bottom-color: rgba(99, 102, 241, 0.18);
}}
.topic-label {{ font-weight: 700; color: var(--text-primary, #2d2d2d); }}
.topic-count {{
  display: inline-block;
  margin-left: 8px;
  font-size: 11px;
  font-weight: 650;
  color: var(--brand-accent, #6366f1);
  background: rgba(99, 102, 241, 0.12);
  border-radius: 999px;
  padding: 2px 8px;
}}
.prompt-text {{ font-weight: 500; color: var(--text-primary, #2d2d2d); }}
.muted {{ color: var(--text-muted, #737373); font-size: 12px; }}
.pct {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
.pct-good {{ color: var(--score-green, #00b894); }}
.pct-mid {{ color: var(--score-yellow, #fdcb6e); }}
.pct-bad {{ color: var(--text-muted, #737373); }}
.chip {{
  display: inline-flex; align-items: center; gap: 4px;
  border-radius: 999px; padding: 2px 10px; font-size: 11px; font-weight: 700;
}}
.chip-pos {{ background: var(--insight-positive-bg, #f0f9f5); color: var(--insight-positive-label, #1a6b52); }}
.chip-neg {{ background: var(--insight-attention-bg, #fdf5f4); color: var(--insight-attention-label, #8f4a3d); }}
.chip-neu {{ background: #f0f0f0; color: #636e72; }}
.pill {{
  display: inline-block; border-radius: 8px; padding: 2px 8px;
  font-size: 11px; font-weight: 700; margin: 1px 3px 1px 0;
}}
.score-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 14px;
  padding: 20px 24px 24px;
}}
.score-card {{
  border: 1px solid var(--border, rgba(0,0,0,.08));
  border-radius: var(--radius, 12px);
  padding: 16px 18px;
  background: var(--bg-card, rgba(255,255,255,.88));
  box-shadow: var(--shadow-sm, 0 1px 2px rgba(0,0,0,.04));
}}
.score-card .label {{
  font-size: 11px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text-muted, #737373); margin-bottom: 8px;
}}
.score-card .value {{
  font-size: 30px; font-weight: 750; line-height: 1;
  color: var(--text-primary, #2d2d2d);
}}
.score-card .sub {{ font-size: 12px; color: var(--text-muted, #737373); margin-top: 8px; }}
.bar-row {{ padding: 8px 24px 14px; }}
.bar-label {{
  display: flex; justify-content: space-between; font-size: 13px;
  color: var(--text-primary, #2d2d2d); margin-bottom: 6px;
}}
.bar-track {{
  height: 8px; background: rgba(0,0,0,.06);
  border-radius: 999px; overflow: hidden;
}}
.bar-fill {{ height: 100%; border-radius: 999px; }}
.summary-hero {{
  display: flex; flex-wrap: wrap; gap: 24px; align-items: center;
  padding: 24px; border-bottom: 1px solid var(--border, rgba(0,0,0,.08));
}}
.summary-hero-score {{ display: flex; align-items: center; gap: 18px; flex-shrink: 0; }}
.summary-hero-copy {{ flex: 1; min-width: 240px; }}
.summary-hero-copy .eyebrow {{
  font-size: 10px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text-muted, #737373); margin: 0 0 6px;
}}
.summary-hero-copy .score-num {{
  font-size: 30px; font-weight: 750; line-height: 1; color: #0d0d0d; margin: 0 0 4px;
}}
.summary-hero-copy .score-num span {{ font-size: 15px; font-weight: 500; color: #9ca3af; }}
.summary-hero-copy .score-label {{ font-size: 14px; font-weight: 650; margin: 0 0 8px; }}
.summary-exec {{
  border-left: 1px solid var(--border, rgba(0,0,0,.08));
  padding-left: 22px; color: #4b5563; font-size: 14px; line-height: 1.6;
}}
.summary-exec .eyebrow {{
  font-size: 10px; font-weight: 700; letter-spacing: .08em;
  text-transform: uppercase; color: var(--text-muted, #737373); margin: 0 0 8px;
}}
.pillar-grid {{
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 14px;
  padding: 20px 24px;
}}
@media (max-width: 900px) {{
  .pillar-grid {{ grid-template-columns: 1fr; }}
  .summary-exec {{ border-left: 0; padding-left: 0; border-top: 1px solid var(--border, rgba(0,0,0,.08)); padding-top: 16px; }}
}}
.pillar-card {{
  border: 1px solid var(--border, rgba(0,0,0,.08));
  border-radius: 16px; padding: 18px; background: #fff;
}}
.pillar-card .pillar-head {{
  display: flex; align-items: center; gap: 8px; margin-bottom: 12px;
}}
.pillar-card .pillar-head .label {{
  font-size: 11px; font-weight: 700; letter-spacing: .06em;
  text-transform: uppercase; color: #9ca3af;
}}
.pillar-card .pillar-head .weight {{
  margin-left: auto; font-size: 10px; color: #d1d5db;
}}
.pillar-card .pillar-body {{ display: flex; align-items: center; gap: 14px; margin-bottom: 10px; }}
.pillar-card .pillar-score {{ font-size: 26px; font-weight: 750; line-height: 1; color: #0d0d0d; margin: 0; }}
.pillar-card .pillar-tone {{ font-size: 12px; font-weight: 650; margin: 4px 0 0; }}
.pillar-card .pillar-desc {{ font-size: 12px; color: #9ca3af; line-height: 1.45; margin: 0 0 12px; }}
.pillar-card .pillar-bar {{
  height: 6px; background: #f3f4f6; border-radius: 999px; overflow: hidden;
}}
.pillar-card .pillar-bar > span {{ display: block; height: 100%; border-radius: 999px; }}
.summary-block {{
  margin: 0 24px 24px; border: 1px solid var(--border, rgba(0,0,0,.08));
  border-radius: 16px; padding: 18px 20px; background: #fff;
}}
.summary-block h3 {{
  margin: 0 0 14px; font-size: 14px; font-weight: 700; color: #0d0d0d;
}}
.platform-mini {{
  display: flex; align-items: center; gap: 12px; margin-bottom: 12px;
}}
.platform-mini:last-child {{ margin-bottom: 0; }}
.platform-mini .plat-name {{
  width: 120px; flex-shrink: 0; font-size: 12px; font-weight: 600; color: #0d0d0d;
}}
.platform-mini .plat-track {{
  flex: 1; height: 8px; background: #f3f4f6; border-radius: 999px; overflow: hidden;
}}
.platform-mini .plat-track > span {{ display: block; height: 100%; border-radius: 999px; }}
.platform-mini .plat-pct {{
  width: 48px; text-align: right; font-size: 12px; font-weight: 700;
  font-variant-numeric: tabular-nums;
}}
.findings-list {{ margin: 0; padding: 0; list-style: none; }}
.findings-list li {{
  display: flex; gap: 10px; align-items: flex-start;
  font-size: 14px; color: #4b5563; line-height: 1.5; margin-bottom: 8px;
}}
.findings-list li:last-child {{ margin-bottom: 0; }}
.findings-list .dot {{
  width: 6px; height: 6px; border-radius: 999px; background: #6366f1;
  margin-top: 7px; flex-shrink: 0;
}}
.summary-footnote {{
  text-align: center; font-size: 11px; color: #d1d5db;
  padding: 0 24px 20px; margin: 0;
}}
.divider-label {{
  display: none; /* section headers already carry titles in cards */
}}
@media print {{
  body.geo-report.geo-export {{
    background: #fff;
    padding: 0;
  }}
  .export-wrap {{ max-width: none; padding: 0; }}
  .export-page {{
    break-after: page;
    page-break-after: always;
    margin: 0;
  }}
  .export-page:last-child {{
    break-after: auto;
    page-break-after: auto;
  }}
  .export-section.report-block {{
    box-shadow: none;
  }}
}}
</style>
"""


def _report_css_safe(css: str) -> str:
    # Avoid breaking the surrounding <style> if the file ever contains </style>.
    return (css or "").replace("</style>", "<\\/style>")


def wrap_export_document(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{ESC(title)} — GEO Audit</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,400;0,14..32,500;0,14..32,600;0,14..32,700;1,14..32,400&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;600;700&amp;display=swap" rel="stylesheet"/>
{export_page_css()}
</head>
<body class="geo-report geo-export">
<div class="export-wrap container">
{body}
</div>
</body>
</html>"""


def _pct_class(val: float) -> str:
    if val >= 75:
        return "pct pct-good"
    if val >= 50:
        return "pct pct-mid"
    return "pct pct-bad"


def _sentiment_chip(label: str | None) -> str:
    if not label:
        return "<span class='muted'>—</span>"
    key = label.lower()
    cls = "chip-neu"
    if key.startswith("pos"):
        cls = "chip-pos"
    elif key.startswith("neg"):
        cls = "chip-neg"
    return f"<span class='chip {cls}'>{ESC(label.capitalize())}</span>"


def _brand_mentioned(text: str, brand: str, tokens: list[str]) -> bool:
    blob = (text or "").lower()
    if not blob:
        return False
    for tok in tokens:
        t = (tok or "").strip().lower()
        if t and t in blob:
            return True
    b = (brand or "").strip().lower()
    return bool(b and b in blob)


def _response_sentiment(text: str, tokens: list[str]) -> str | None:
    if not _brand_mentioned(text, "", tokens) and not any(
        t.lower() in (text or "").lower() for t in tokens if t
    ):
        # still try brand tokens only
        lower = (text or "").lower()
        if not any((t or "").lower() in lower for t in tokens if t):
            return None
    lower = (text or "").lower()
    pos = sum(1 for w in POSITIVE_WORDS if w in lower)
    neg = sum(1 for w in NEGATIVE_WORDS if w in lower)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _active_platforms(probe: dict[str, Any]) -> list[str]:
    active = probe.get("active_platforms")
    if isinstance(active, list) and active:
        return [str(p) for p in active]
    found: list[str] = []
    for pk in ("gemini", "openai", "claude", "google_aio"):
        for row in probe.get("per_prompt") or []:
            if isinstance(row, dict) and row.get(f"{pk}_response"):
                found.append(pk)
                break
    return found or ["gemini", "openai", "claude"]


def _map_prompt_topics(ctx: dict[str, Any], per_prompt: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping_rows = ctx.get("probed_pss_rows") or ctx.get("pss_rows") or []
    metadata: dict[str, str] = {}
    positional: list[str] = []
    if ctx.get("use_pss") and isinstance(mapping_rows, list):
        for row in mapping_rows:
            if not isinstance(row, dict):
                continue
            topic = str(row.get("product_or_service") or "").strip() or "Other"
            for prompt in row.get("prompts") or []:
                key = str(prompt).strip().lower()
                positional.append(topic)
                if key and key not in metadata:
                    metadata[key] = topic
    fallback = (ctx.get("category_labels") or ["Other"])
    fallback_topic = str(fallback[0]).strip() if fallback else "Other"
    out: list[dict[str, Any]] = []
    for i, row in enumerate(per_prompt):
        if not isinstance(row, dict):
            continue
        key = str(row.get("prompt") or "").strip().lower()
        topic = metadata.get(key) or (positional[i] if i < len(positional) else fallback_topic)
        out.append({"row": row, "topic": topic or "Other"})
    return out


def build_prompts_export(audit_dir: Path) -> str:
    """Collapsed-style Prompts table matching AI Visibility → Prompts."""
    from api.prompt_performance import _build_context_response

    try:
        ctx = _build_context_response(audit_dir)
    except Exception:
        ctx = {}
    live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else None
    if not live:
        raw = _load_json(audit_dir / "prompt_performance_live_probe.json") or {}
        live = raw.get("live_probe", raw) if isinstance(raw, dict) else {}
    if not isinstance(live, dict) or not live.get("per_prompt"):
        return _shell(
            "Prompts",
            "AI probe results for each tracked prompt.",
            "<div class='export-empty'>No probe results yet.</div>",
        )

    try:
        from api.probe_platforms import sanitize_live_probe

        live = sanitize_live_probe(live)
    except Exception:
        pass

    brand = str(ctx.get("brand_name") or live.get("brand_name") or "Brand")
    tokens = list(live.get("brand_match_tokens") or [])
    if brand and brand not in tokens:
        tokens = [brand, *tokens]
    platforms = _active_platforms(live)
    mapped = _map_prompt_topics(ctx, [p for p in live.get("per_prompt") or [] if isinstance(p, dict)])

    by_topic: dict[str, list[dict[str, Any]]] = {}
    for item in mapped:
        by_topic.setdefault(item["topic"], []).append(item)

    rows_html: list[str] = []
    for topic, entries in by_topic.items():
        total_resp = 0
        ment_resp = 0
        sentiments: list[str] = []
        comps: set[str] = set()
        cits: set[str] = set()
        topic_platforms: set[str] = set()
        for item in entries:
            row = item["row"]
            for pk in platforms:
                resp = str(row.get(f"{pk}_response") or "")
                if not resp:
                    continue
                topic_platforms.add(pk)
                total_resp += 1
                scores = row.get(f"mention_scores_{pk}") if isinstance(row.get(f"mention_scores_{pk}"), dict) else {}
                brand_hit = float(scores.get("brand_signal") or 0) > 0 or _brand_mentioned(resp, brand, tokens)
                if brand_hit:
                    ment_resp += 1
                    s = _response_sentiment(resp, tokens)
                    if s:
                        sentiments.append(s)
                detail = scores.get("competitor_detail") if isinstance(scores.get("competitor_detail"), dict) else {}
                for name, hits in detail.items():
                    if float(hits or 0) > 0:
                        comps.add(str(name))
                for cit in row.get(f"citations_{pk}") or []:
                    if isinstance(cit, dict) and cit.get("domain") and not cit.get("competitor_cited"):
                        cits.add(str(cit["domain"]))
        vis = round(100.0 * ment_resp / total_resp) if total_resp else 0
        if sentiments.count("positive") >= sentiments.count("negative") and sentiments:
            dom_sent = "positive"
        elif sentiments.count("negative") > sentiments.count("positive"):
            dom_sent = "negative"
        elif sentiments:
            dom_sent = "neutral"
        else:
            dom_sent = None
        plat_html = "".join(
            f"<span class='pill' style='background:{PLATFORM_COLORS.get(pk, '#666')}18;color:{PLATFORM_COLORS.get(pk, '#666')}'>"
            f"{ESC(PLATFORM_LABELS.get(pk, pk))}</span>"
            for pk in platforms
            if pk in topic_platforms
        )
        comp_html = ", ".join(ESC(c) for c in sorted(comps)[:8]) or "<span class='muted'>—</span>"
        cit_html = ", ".join(ESC(c) for c in sorted(cits)[:8]) or "<span class='muted'>—</span>"
        plat_cell = plat_html or "<span class='muted'>—</span>"
        rows_html.append(
            "<tr class='topic-row'>"
            f"<td><span class='topic-label'>{ESC(topic)}</span>"
            f"<span class='topic-count'>{len(entries)} tested</span></td>"
            f"<td class='{_pct_class(vis)}' style='text-align:center'>{vis}%</td>"
            f"<td style='text-align:center'>{_sentiment_chip(dom_sent)}</td>"
            f"<td>{plat_cell}</td>"
            f"<td class='muted'>{comp_html}</td>"
            f"<td class='muted'>{cit_html}</td>"
            "</tr>"
        )
        for item in entries:
            row = item["row"]
            prompt = str(row.get("prompt") or "")
            # per-prompt visibility
            p_total = 0
            p_hit = 0
            p_sentiments: list[str] = []
            p_plats: list[str] = []
            p_comps: set[str] = set()
            p_cits: set[str] = set()
            for pk in platforms:
                resp = str(row.get(f"{pk}_response") or "")
                if not resp:
                    continue
                p_plats.append(pk)
                p_total += 1
                scores = row.get(f"mention_scores_{pk}") if isinstance(row.get(f"mention_scores_{pk}"), dict) else {}
                brand_hit = float(scores.get("brand_signal") or 0) > 0 or _brand_mentioned(resp, brand, tokens)
                if brand_hit:
                    p_hit += 1
                    s = _response_sentiment(resp, tokens)
                    if s:
                        p_sentiments.append(s)
                detail = scores.get("competitor_detail") if isinstance(scores.get("competitor_detail"), dict) else {}
                for name, hits in detail.items():
                    if float(hits or 0) > 0:
                        p_comps.add(str(name))
                for cit in row.get(f"citations_{pk}") or []:
                    if isinstance(cit, dict) and cit.get("domain") and not cit.get("competitor_cited"):
                        p_cits.add(str(cit["domain"]))
            p_vis = round(100.0 * p_hit / p_total) if p_total else 0
            if p_sentiments.count("positive") >= p_sentiments.count("negative") and p_sentiments:
                p_dom = "positive"
            elif p_sentiments.count("negative") > p_sentiments.count("positive"):
                p_dom = "negative"
            elif p_sentiments:
                p_dom = "neutral"
            else:
                p_dom = None
            plat_html = "".join(
                f"<span class='pill' style='background:{PLATFORM_COLORS.get(pk, '#666')}18;color:{PLATFORM_COLORS.get(pk, '#666')}'>"
                f"{ESC(PLATFORM_LABELS.get(pk, pk))}</span>"
                for pk in p_plats
            )
            plat_cell = plat_html or "<span class='muted'>—</span>"
            comp_cell = ", ".join(ESC(c) for c in sorted(p_comps)[:6]) or "—"
            cit_cell = ", ".join(ESC(c) for c in sorted(p_cits)[:6]) or "—"
            rows_html.append(
                "<tr>"
                f"<td><div class='prompt-text'>{ESC(prompt)}</div></td>"
                f"<td class='{_pct_class(p_vis)}' style='text-align:center'>{p_vis}%</td>"
                f"<td style='text-align:center'>{_sentiment_chip(p_dom)}</td>"
                f"<td>{plat_cell}</td>"
                f"<td class='muted'>{comp_cell}</td>"
                f"<td class='muted'>{cit_cell}</td>"
                "</tr>"
            )

    table = (
        "<table class='export-table'>"
        "<thead><tr>"
        "<th>Topic / Prompt</th><th style='text-align:center'>Visibility</th>"
        "<th style='text-align:center'>Sentiment</th><th>Platforms</th>"
        "<th>Competitors</th><th>Citations</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
    )
    return _shell(
        "Prompts",
        f"AI probe results for {brand} — topic summary rows with prompts underneath.",
        table,
    )


def build_competitors_export(audit_dir: Path) -> str:
    from api import geo_services as geo

    try:
        data = geo.load_competitive_comparison(audit_dir)
    except Exception:
        data = {"rows": [], "has_comparison": False}
    rows = [r for r in (data.get("rows") or []) if isinstance(r, dict)]
    if not rows:
        msg = (
            "Competitor comparison appears after the audit finishes crawling configured competitor sites."
            if not data.get("has_comparison")
            else "Comparison data is incomplete."
        )
        return _shell(
            "Competitor comparison",
            "Pillar scores from competitor site crawls.",
            f"<div class='export-empty'>{ESC(msg)}</div>",
        )

    brand_name = next(
        (str(r.get("name") or "Your Brand") for r in rows if r.get("is_primary")),
        "Your Brand",
    )

    def overall_of(row: dict[str, Any]) -> float:
        if isinstance(row.get("overall"), (int, float)):
            return float(row["overall"])
        try:
            return round(
                0.4 * float(row.get("ai_visibility") or 0)
                + 0.3 * float(row.get("technical_setup") or 0)
                + 0.3 * float(row.get("content_quality") or 0),
                1,
            )
        except (TypeError, ValueError):
            return 0.0

    rows = sorted(rows, key=lambda r: (-overall_of(r), str(r.get("name") or "").lower()))

    def rationale_cell(value: float, rationale: Any, competitor: str) -> str:
        components: list[dict[str, Any]] = []
        brand_components: list[dict[str, Any]] = []
        if isinstance(rationale, dict):
            components = [
                item for item in (rationale.get("components") or []) if isinstance(item, dict)
            ]
            brand_components = [
                item for item in (rationale.get("brand_components") or []) if isinstance(item, dict)
            ]
        _, color = _score_tone_color(value)
        parts = [
            f"<div style='text-align:right;font-weight:700;font-size:12px;color:{color};margin-bottom:6px'>{format_report_score(value)}</div>",
            f"<div class='bar-track' style='margin-bottom:8px'><div class='bar-fill' style='width:{min(max(value,0),100)}%;background:{color}'></div></div>",
        ]
        by_key: dict[str, dict[str, Any]] = {}
        order: list[str] = []

        def upsert(item: dict[str, Any], side: str) -> None:
            key = str(item.get("key") or item.get("title") or "")
            if not key:
                return
            if key not in by_key:
                by_key[key] = {"title": str(item.get("title") or key)}
                order.append(key)
            by_key[key][side] = item

        for item in brand_components:
            upsert(item, "brand")
        for item in components:
            upsert(item, "competitor")

        has_verified = (
            bool(rationale.get("has_verified_findings"))
            if isinstance(rationale, dict) and "has_verified_findings" in rationale
            else any(
                str(c.get("finding_summary") or "").strip() or str(c.get("evidence_example") or "").strip()
                for c in components
                if c.get("verified") is not False
            )
        )

        if not order:
            parts.append(
                "<p class='muted' style='font-size:11px'>Criterion findings are not available for this crawl.</p>"
            )
        elif not has_verified:
            for key in order:
                pair = by_key[key]
                title = ESC(str(pair.get("title") or key))
                comp = pair.get("competitor") if isinstance(pair.get("competitor"), dict) else {}
                score = format_report_score(float((comp or {}).get("score") or value))
                parts.append(
                    f"<div style='margin:0 0 10px;padding-top:8px;border-top:1px solid #f3f4f6'>"
                    f"<p style='margin:0 0 6px;font-size:11px;font-weight:700;color:#0d0d0d'>{title}</p>"
                    f"<div style='background:#f5f3ff;border-radius:8px;padding:8px'>"
                    f"<p style='margin:0;font-size:9px;font-weight:700;letter-spacing:.06em;"
                    f"text-transform:uppercase;color:#6d28d9'>{ESC(competitor)}</p>"
                    f"<p style='margin:4px 0 0;font-size:11px;font-weight:700;color:#374151'>{score}</p>"
                    f"</div></div>"
                )
        else:
            for key in order:
                pair = by_key[key]
                title = ESC(str(pair.get("title") or key))
                comp = pair.get("competitor") if isinstance(pair.get("competitor"), dict) else {}
                brand = pair.get("brand") if isinstance(pair.get("brand"), dict) else {}
                comp_verified = bool(
                    (str(comp.get("finding_summary") or "").strip()
                     or str(comp.get("evidence_example") or "").strip())
                    and comp.get("verified") is not False
                )
                if comp_verified:
                    comp_finding = ESC(str(comp.get("finding_summary") or "Finding not available."))
                    parts.append(
                        f"<div style='margin:0 0 10px;padding-top:8px;border-top:1px solid #f3f4f6'>"
                        f"<p style='margin:0 0 6px;font-size:11px;font-weight:700;color:#0d0d0d'>{title}</p>"
                        f"<div style='background:#f5f3ff;border-radius:8px;padding:8px;margin:0 0 6px'>"
                        f"<p style='margin:0;font-size:9px;font-weight:700;letter-spacing:.06em;"
                        f"text-transform:uppercase;color:#6d28d9'>{ESC(competitor)}</p>"
                        f"<p style='margin:4px 0 0;font-size:9px;font-weight:700;letter-spacing:.06em;"
                        f"text-transform:uppercase;color:#7c3aed'>What we found</p>"
                        f"<p style='margin:4px 0 0;font-size:11px;color:#374151;line-height:1.4'>{comp_finding}</p>"
                        f"</div>"
                    )
                else:
                    score = format_report_score(float((comp or {}).get("score") or value))
                    parts.append(
                        f"<div style='margin:0 0 10px;padding-top:8px;border-top:1px solid #f3f4f6'>"
                        f"<p style='margin:0 0 6px;font-size:11px;font-weight:700;color:#0d0d0d'>{title}</p>"
                        f"<div style='background:#f5f3ff;border-radius:8px;padding:8px;margin:0 0 6px'>"
                        f"<p style='margin:0;font-size:9px;font-weight:700;letter-spacing:.06em;"
                        f"text-transform:uppercase;color:#6d28d9'>{ESC(competitor)}</p>"
                        f"<p style='margin:4px 0 0;font-size:11px;font-weight:700;color:#374151'>{score}</p>"
                        f"</div>"
                    )
                brand_finding = ESC(str(brand.get("finding_summary") or "").strip())
                if brand_finding:
                    parts.append(
                        f"<div style='background:#f5f3ff;border-radius:8px;padding:8px'>"
                        f"<p style='margin:0;font-size:9px;font-weight:700;letter-spacing:.06em;"
                        f"text-transform:uppercase;color:#6d28d9'>{ESC(brand_name)}</p>"
                        f"<p style='margin:4px 0 0;font-size:9px;font-weight:700;letter-spacing:.06em;"
                        f"text-transform:uppercase;color:#7c3aed'>What we found</p>"
                        f"<p style='margin:4px 0 0;font-size:11px;color:#374151;line-height:1.4'>{brand_finding}</p>"
                        f"</div></div>"
                    )
                else:
                    parts.append("</div>")
        return f"<td style='vertical-align:top;min-width:180px'>{''.join(parts)}</td>"

    body_rows = []
    for row in rows:
        name = str(row.get("name") or "—")
        you = " <span class='topic-count'>Your Brand</span>" if row.get("is_primary") else ""
        overall = overall_of(row)
        try:
            ai_v = float(row.get("ai_visibility"))
            tech_v = float(row.get("technical_setup"))
            content_v = float(row.get("content_quality"))
        except (TypeError, ValueError):
            ai_v = tech_v = content_v = 0.0

        score_row = (
            "<tr>"
            f"<td>{ESC(name)}{you}</td>"
            f"<td class='{_pct_class(overall)}' style='text-align:right;font-weight:700'>{format_report_score(overall)}</td>"
            f"<td class='{_pct_class(ai_v)}' style='text-align:right'>{format_report_score(ai_v)}</td>"
            f"<td class='{_pct_class(tech_v)}' style='text-align:right'>{format_report_score(tech_v)}</td>"
            f"<td class='{_pct_class(content_v)}' style='text-align:right'>{format_report_score(content_v)}</td>"
            "</tr>"
        )
        body_rows.append(score_row)

        # Expanded detail for competitors only (brand is benchmarking reference).
        if row.get("is_primary"):
            continue
        detail = (
            "<tr style='background:rgba(249,250,251,.9)'>"
            f"<td style='vertical-align:top'><span class='muted' style='font-size:11px'>{ESC(name)}</span></td>"
            f"<td></td>"
            + rationale_cell(ai_v, row.get("ai_visibility_rationale"), name)
            + rationale_cell(tech_v, row.get("technical_setup_rationale"), name)
            + rationale_cell(content_v, row.get("content_quality_rationale"), name)
            + "</tr>"
        )
        body_rows.append(detail)

    table = (
        "<table class='export-table'><thead><tr>"
        "<th>Brand</th>"
        "<th style='text-align:right'>Overall</th>"
        "<th style='text-align:right'>AI visibility</th>"
        "<th style='text-align:right'>Technical setup</th>"
        "<th style='text-align:right'>Content quality</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody></table>"
        "<p class='muted' style='padding:12px 24px 20px'>"
        "Overall = 40% AI Visibility + 30% Technical + 30% Content. "
        "Your brand row uses integrated Summary scores. Competitor AI Visibility uses prompt "
        "visibility and SOV when the competitor URL matches probe data; otherwise crawl signals. "
        "Expanded rows show Summary-style criterion findings (What we found) for competitor and brand."
        "</p>"
    )
    return _shell(
        "Competitor comparison",
        "Pillar scores with criterion-level findings expanded for each competitor.",
        table,
    )


def _prepare_recommendation_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop Good/Excellent scored items; sort lowest score → highest (unscored last)."""
    kept: list[dict[str, Any]] = []
    for item in items:
        score = item.get("score")
        if isinstance(score, (int, float)) and not _is_ok_or_below(float(score)):
            continue
        kept.append(item)
    kept.sort(
        key=lambda item: (
            float(item["score"]) if isinstance(item.get("score"), (int, float)) else float("inf"),
            str(item.get("title") or ""),
        )
    )
    return kept


def _score_tone_color(score: float) -> tuple[str, str]:
    """Return (label, hex color) matching the React report score scale (banded on rounded int)."""
    label = score_label(score)
    colors = {
        "Excellent": "#00b894",
        "Good": "#00b894",
        "OK": "#0984e3",
        "Weak": "#fdcb6e",
        "Poor": "#e17055",
    }
    return label, colors[label]


def _svg_score_gauge(score: float, *, size: int = 96, decimals: int = 0) -> str:
    r = (size - 12) / 2
    circ = 2 * 3.141592653589793 * r
    clamped = max(0.0, min(100.0, float(score)))
    dash = (clamped / 100.0) * circ
    _, color = _score_tone_color(clamped)
    # decimals kept for call-site compat; display is always nearest integer.
    _ = decimals
    label = format_report_score(clamped)
    return (
        f"<svg width='{size}' height='{size}' viewBox='0 0 {size} {size}' "
        f"xmlns='http://www.w3.org/2000/svg' aria-hidden='true'>"
        f"<circle cx='{size/2}' cy='{size/2}' r='{r}' fill='none' stroke='#e5e7eb' stroke-width='10'/>"
        f"<circle cx='{size/2}' cy='{size/2}' r='{r}' fill='none' stroke='{color}' stroke-width='10' "
        f"stroke-dasharray='{dash:.3f} {max(0.0, circ - dash):.3f}' stroke-linecap='round' "
        f"transform='rotate(-90 {size/2} {size/2})'/>"
        f"<text x='50%' y='50%' dominant-baseline='middle' text-anchor='middle' "
        f"font-size='{size * 0.22:.1f}' font-weight='700' fill='#0d0d0d'>{ESC(label)}</text>"
        f"</svg>"
    )


def _platform_bar_color(pct: float) -> str:
    if pct >= 75:
        return "#00b894"
    if pct >= 50:
        return "#0984e3"
    if pct >= 25:
        return "#6366f1"
    return "#a5b4fc"


def build_summary_export(audit_dir: Path) -> str:
    from api import geo_services as geo

    try:
        scores = geo.load_integrated_scores(audit_dir)
    except Exception:
        scores = {}
    summary: dict[str, Any] = {}
    try:
        summary = geo.load_audit_summary(audit_dir)
    except Exception:
        pass

    brand = str(summary.get("brand_name") or scores.get("brand_name") or "Brand")
    overall = scores.get("overall")
    if overall is None:
        overall = summary.get("overall_score")
    try:
        overall_f = float(overall) if overall is not None else None
    except (TypeError, ValueError):
        overall_f = None

    ai = scores.get("ai_visibility")
    technical = scores.get("technical_setup")
    content = scores.get("content_structure")

    exec_doc: dict[str, Any] = {}
    try:
        from executive_summary_llm import load_cached_executive_summary

        cached = load_cached_executive_summary(audit_dir)
    except Exception:
        cached = None
    if isinstance(cached, dict):
        exec_doc = cached
    paragraph_html = str(exec_doc.get("paragraph_html") or "").strip()
    key_findings = [str(f).strip() for f in (exec_doc.get("key_findings") or []) if str(f).strip()]

    prompt_metrics = scores.get("prompt_metrics") if isinstance(scores.get("prompt_metrics"), dict) else {}
    visibility_pct = prompt_metrics.get("visibility_pct")
    platform_rows: list[tuple[str, float]] = []
    per_platform = prompt_metrics.get("per_platform") if isinstance(prompt_metrics.get("per_platform"), dict) else {}
    if per_platform:
        for pk in ("gemini", "openai", "google_aio", "claude"):
            entry = per_platform.get(pk)
            if not isinstance(entry, dict):
                continue
            try:
                pct = float(entry.get("visibility_pct") or entry.get("brand_visibility_pct") or 0)
            except (TypeError, ValueError):
                continue
            if pct <= 0 and not entry.get("response_count"):
                continue
            if pk == "claude":
                continue
            platform_rows.append((pk, pct))
    if not platform_rows:
        try:
            from api.prompt_performance import _build_context_response

            ctx = _build_context_response(audit_dir)
            live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else {}
            agg = live.get("aggregate") if isinstance(live.get("aggregate"), dict) else {}
            for pk in _active_platforms(live if isinstance(live, dict) else {}):
                if pk == "claude":
                    continue
                plat = agg.get(pk) if isinstance(agg.get(pk), dict) else {}
                try:
                    pct = float(
                        plat.get("brand_visibility_pct")
                        or plat.get("brand_mention_pct")
                        or plat.get("brand_share_pct")
                        or 0
                    )
                except (TypeError, ValueError):
                    pct = 0.0
                platform_rows.append((pk, pct))
        except Exception:
            pass

    if overall_f is None and ai is None and technical is None and content is None:
        return _shell(
            "Summary",
            f"Score overview for {brand}.",
            "<div class='export-empty'>Score breakdown not available.</div>",
        )

    hero = ""
    if overall_f is not None:
        tone_label, tone_color = _score_tone_color(overall_f)
        exec_block = ""
        if paragraph_html:
            # Keep score in sync with integrated overall when present in copy.
            synced = re.sub(
                r"(\d{1,3}(?:\.\d+)?)\s*(?:/\s*100|out of 100)",
                f"{format_report_score(overall_f)}/100",
                paragraph_html,
                count=1,
                flags=re.IGNORECASE,
            )
            exec_block = (
                f"<div class='summary-exec'><p class='eyebrow'>Executive summary</p>"
                f"<div>{synced}</div></div>"
            )
        hero = (
            "<div class='summary-hero'>"
            f"<div class='summary-hero-score'>{_svg_score_gauge(overall_f, size=100)}"
            f"<div class='summary-hero-copy'>"
            f"<p class='eyebrow'>Overall GEO Score</p>"
            f"<p class='score-num'>{format_report_score(overall_f)}<span> /100</span></p>"
            f"<p class='score-label' style='color:{tone_color}'>{ESC(tone_label)}</p>"
            f"</div></div>{exec_block}</div>"
        )

    def _pillar(label: str, weight: int, raw: Any, description: str) -> str:
        try:
            score = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            score = None
        if score is None:
            return (
                f"<div class='pillar-card'><div class='pillar-head'>"
                f"<span class='label'>{ESC(label)}</span>"
                f"<span class='weight'>Weight: {weight}%</span></div>"
                f"<p class='pillar-desc'>Not yet available</p>"
                f"<p class='pillar-desc'>{ESC(description)}</p></div>"
            )
        tone_label, tone_color = _score_tone_color(score)
        return (
            f"<div class='pillar-card'><div class='pillar-head'>"
            f"<span class='label'>{ESC(label)}</span>"
            f"<span class='weight'>Weight: {weight}%</span></div>"
            f"<div class='pillar-body'>{_svg_score_gauge(score, size=72)}"
            f"<div><p class='pillar-score'>{format_report_score(score)}</p>"
            f"<p class='pillar-tone' style='color:{tone_color}'>{ESC(tone_label)}</p></div></div>"
            f"<p class='pillar-desc'>{ESC(description)}</p>"
            f"<div class='pillar-bar'><span style='width:{max(0, min(100, score)):.1f}%;background:{tone_color}'></span></div>"
            f"</div>"
        )

    from api.export_page_builders import enhance_summary_pillar_descriptions

    ai_desc, tech_desc, content_desc = enhance_summary_pillar_descriptions(
        scores, brand, visibility_pct
    )

    pillars = (
        "<div class='pillar-grid'>"
        + _pillar("AI Visibility", 40, ai, ai_desc)
        + _pillar("Technical Setup", 30, technical, tech_desc[:220] or "Technical readiness from the GEO audit engine.")
        + _pillar("Content Quality", 30, content, content_desc[:220] or "Content quality from the GEO audit engine.")
        + "</div>"
    )

    platforms_html = ""
    if platform_rows:
        rows = []
        for pk, pct in platform_rows:
            color = _platform_bar_color(pct)
            rows.append(
                f"<div class='platform-mini'>"
                f"<span class='plat-name'>{ESC(PLATFORM_LABELS.get(pk, pk))}</span>"
                f"<div class='plat-track'><span style='width:{max(0, min(100, pct)):.1f}%;background:{color}'></span></div>"
                f"<span class='plat-pct' style='color:{color}'>{pct:.0f}%</span>"
                f"</div>"
            )
        platforms_html = (
            "<div class='summary-block'><h3>Brand visibility by AI platform</h3>"
            + "".join(rows)
            + "</div>"
        )

    findings_html = ""
    if key_findings:
        items = "".join(
            f"<li><span class='dot'></span><span>{ESC(item)}</span></li>" for item in key_findings
        )
        findings_html = (
            "<div class='summary-block'><h3>Key Findings</h3>"
            f"<ul class='findings-list'>{items}</ul></div>"
        )

    footnote = (
        "<p class='summary-footnote'>"
        "AI Visibility is calculated from live probe data (60% brand visibility + 40% competitor-relative SOV). "
        "Overall = 40% AI Visibility + 30% Technical + 30% Content."
        "</p>"
    )

    body = hero + pillars + platforms_html + findings_html + footnote
    return _shell("Summary", f"Score overview for {brand}.", body)


def build_ai_visibility_overview_export(audit_dir: Path) -> str:
    from api.export_page_builders import build_ai_visibility_overview_export as _build

    return _build(audit_dir)


def build_competitor_visibility_export(audit_dir: Path) -> str:
    from api.export_page_builders import build_competitor_visibility_export as _build

    return _build(audit_dir)


def build_citations_export(audit_dir: Path) -> str:
    from api.export_page_builders import build_citations_export as _build

    return _build(audit_dir)


def build_recommendations_export(audit_dir: Path) -> str:
    from api.export_page_builders import build_recommendations_export as _build

    return _build(audit_dir)


def build_generic_score_export(audit_dir: Path, section: str, title: str) -> str:
    """Fallback for unknown sections — prefer dedicated builders via SECTION_BUILDERS."""
    panel = _extract_legacy_panel(audit_dir, section)
    if panel:
        return panel
    return _shell(
        title,
        "Exported from the current audit scores.",
        "<div class='export-empty'>Detailed view is available in the app; export builder not found for this section.</div>",
    )


def _extract_legacy_panel(audit_dir: Path, section: str) -> str | None:
    """Last-resort extract from report.html for panels that still exist there (e.g. samples)."""
    panel_id = {
        "sample-scripts": "samples",
        "samples": "samples",
        "ai-traffic-dashboard": "ga4-traffic",
        "ga4-traffic": "ga4-traffic",
        "summary": "summary",
        "recommendations": "recommendations",
        "competitor-comparison": "competitors",
        "competitors": "competitors",
        "crawler-access": "technical",
        "technical": "technical",
        "content": "content",
        "citability": "ai-visibility",
        "ai-visibility": "ai-visibility",
    }.get(section, section)
    path = audit_dir / "report.html"
    if not path.is_file():
        return None
    full = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        rf'<div[^>]*data-tab-panel=["\']{re.escape(panel_id)}["\'][^>]*>[\s\S]*?'
        r'(?=<div[^>]*report-tab-panel|</main>)',
        full,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    panel = re.sub(r"\s+hidden(?=[\s>])", "", match.group(0), count=1, flags=re.IGNORECASE)
    return f"<div class='export-section' style='padding:12px'>{panel}</div>"


def _page_builder(name: str):
    def _call(audit_dir: Path) -> str:
        from api import export_page_builders as pages

        return getattr(pages, name)(audit_dir)

    return _call


SECTION_BUILDERS: dict[str, Any] = {
    "summary": lambda d: build_summary_export(d),
    "recommendations": lambda d: build_recommendations_export(d),
    "competitor-comparison": lambda d: build_competitors_export(d),
    "competitors": lambda d: build_competitors_export(d),  # legacy alias
    "ai-traffic-dashboard": _page_builder("build_ga4_traffic_export"),
    "ga4-traffic": _page_builder("build_ga4_traffic_export"),  # legacy alias
    "ai-visibility-overview": lambda d: build_ai_visibility_overview_export(d),
    "prompts": lambda d: build_prompts_export(d),
    "prompt_performance": lambda d: build_prompts_export(d),  # legacy alias
    "competitor-visibility": lambda d: build_competitor_visibility_export(d),
    "competitor-performance": lambda d: build_competitor_visibility_export(d),  # legacy alias
    "citations": lambda d: build_citations_export(d),
    "reddit-citations": _page_builder("build_reddit_insights_export"),
    "reddit-insights": _page_builder("build_reddit_insights_export"),  # legacy alias
    "youtube-citations": _page_builder("build_youtube_insights_export"),
    "youtube-insights": _page_builder("build_youtube_insights_export"),  # legacy alias
    "technical-overview": _page_builder("build_technical_overview_export"),
    "crawler-access": _page_builder("build_crawler_access_export"),
    "technical": _page_builder("build_crawler_access_export"),  # legacy alias
    "citability": _page_builder("build_citability_export"),
    "ai-visibility": _page_builder("build_citability_export"),  # legacy alias
    "platform-readiness": _page_builder("build_platform_readiness_export"),
    "content-overview": _page_builder("build_content_overview_export"),
    "eeat-signals": _page_builder("build_content_eeat_export"),
    "content-eeat": _page_builder("build_content_eeat_export"),  # legacy alias
    "content-structure-answerability": _page_builder("build_content_structure_export"),
    "content-structure": _page_builder("build_content_structure_export"),  # legacy alias
    "schema-entity-markup": _page_builder("build_content_schema_export"),
    "content-schema": _page_builder("build_content_schema_export"),  # legacy alias
    "brand-visibility-authority": _page_builder("build_content_brand_visibility_export"),
    "content-brand-visibility": _page_builder("build_content_brand_visibility_export"),  # legacy alias
}


def build_section_body(audit_dir: Path, section: str) -> tuple[str, str]:
    """Return (label, inner HTML body) for a UI section id."""
    labels = {sid: label for sid, label in FULL_REPORT_SECTIONS}
    label = labels.get(section, section.replace("-", " ").replace("_", " ").title())
    builder = SECTION_BUILDERS.get(section)
    if builder:
        return label, builder(audit_dir)
    return label, build_generic_score_export(audit_dir, section, label)


def build_all_pages_export(audit_dir: Path) -> str:
    """Full HTML report: each section is its own printable page."""
    parts: list[str] = []
    for section_id, label in FULL_REPORT_SECTIONS:
        try:
            _lbl, body = build_section_body(audit_dir, section_id)
        except Exception as exc:
            body = _shell(
                label,
                "Export failed for this section.",
                f"<div class='export-empty'>{ESC(str(exc))}</div>",
            )
        parts.append(f"<div class='export-page' data-export-section='{ESC(section_id)}'>{body}</div>")
    return wrap_export_document("Full GEO report", "\n".join(parts))


def iter_section_export_html(audit_dir: Path) -> list[tuple[str, str, str]]:
    """
    Return [(section_id, label, standalone_html), ...] for PDF page assembly.
    Each item is a full HTML document for one section.
    """
    out: list[tuple[str, str, str]] = []
    for section_id, label in FULL_REPORT_SECTIONS:
        try:
            _lbl, body = build_section_body(audit_dir, section_id)
        except Exception as exc:
            body = _shell(
                label,
                "Export failed for this section.",
                f"<div class='export-empty'>{ESC(str(exc))}</div>",
            )
        # Skip empty-ish technical/content stubs that only say score not found? Keep them —
        # user asked for sections on their own pages, including available ones.
        html = wrap_export_document(label, f"<div class='export-page'>{body}</div>")
        out.append((section_id, label, html))
    return out
