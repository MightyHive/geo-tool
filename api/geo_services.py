"""Shared audit/archive helpers for the FastAPI layer."""

from __future__ import annotations

import importlib.util
import html as html_lib
import json
import logging
import os
import re
import subprocess
import sys
import urllib.parse
from urllib.robotparser import RobotFileParser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from geo_app_env import ASSETS_ROOT, BACKEND_ROOT, REPO_ROOT, load_app_environment
from api.report_score import format_report_score, score_label as _score_label
from api.report_score import score_tone as _score_tone

load_app_environment()

log = logging.getLogger(__name__)
DEFAULT_OUT_BASE = "audit_output"
# Folder names under audit_output (GCS mount on Cloud Run).
SAMPLE_AUDIT_IDS: tuple[str, ...] = ("starbucks.co.uk_d6a4f5ac1a37",)
SAMPLE_AUDIT_RELS: tuple[Path, ...] = tuple(
    Path("audit_output") / audit_id for audit_id in SAMPLE_AUDIT_IDS
)


def data_root() -> Path:
    """Writable root for audits/archive (GCS mount on Cloud Run, repo root locally)."""
    raw = (os.environ.get("GEO_DATA_ROOT") or "").strip()
    if raw:
        return Path(raw).resolve()
    return REPO_ROOT


def archive_path() -> Path:
    return data_root() / "audit_archive" / "index.json"


def audit_output_base(out_base: str | None = None) -> Path:
    base = (out_base or DEFAULT_OUT_BASE).strip() or DEFAULT_OUT_BASE
    return (data_root() / base).resolve()


def audit_dir_api_rel(path: Path) -> str:
    """Stable API path such as ``audit_output/www.example.com_abc``."""
    resolved = path.resolve()
    for root in (data_root(), REPO_ROOT):
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            continue
    return str(resolved)


def load_create_report() -> Any:
    path = BACKEND_ROOT / "create-report.py"
    spec = importlib.util.spec_from_file_location("geo_create_report", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["geo_create_report"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_crawl_site() -> Any:
    path = BACKEND_ROOT / "crawl-site.py"
    spec = importlib.util.spec_from_file_location("geo_crawl_site", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["geo_crawl_site"] = mod
    spec.loader.exec_module(mod)
    return mod


def _list_row_brand_and_favicon(audit_dir: Path, base_url: str) -> tuple[str, str]:
    """Lightweight list metadata — never reads report.html (that path is ~2s for a few audits)."""
    brand_name = ""
    favicon_url = ""
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            ob = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(ob, dict):
                brand_name = str(ob.get("brand_name_used") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass
    if base_url:
        try:
            from domain_suggest import hostname_for_display_url, public_site_favicon_url

            host = hostname_for_display_url(base_url)
            if host:
                favicon_url = public_site_favicon_url(host)
        except Exception:
            pass
    return brand_name, favicon_url


def list_primary_audits(
    out_root: Path | None = None,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """
    Metadata-only primary audit list for the UI (id, urls, brand, score, mtime).

    Does not parse report.html or compute integrated scores — those belong on the
    report detail path. Optional ``limit`` / ``offset`` support server-side paging.
    """
    root = (out_root or audit_output_base()).resolve()
    if not root.is_dir():
        return []
    # Sort by mtime first so a small ``limit`` can stop after enough primary rows.
    candidates: list[tuple[float, Path, Path]] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        summ = d / "audit_summary.json"
        if not summ.is_file():
            continue
        try:
            m = summ.stat().st_mtime
        except OSError:
            continue
        candidates.append((m, d, summ))
    candidates.sort(key=lambda t: t[0], reverse=True)

    if offset < 0:
        offset = 0
    rows: list[dict[str, Any]] = []
    skipped = 0
    for m, d, summ in candidates:
        try:
            data = json.loads(summ.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("audit_label") != "primary":
            continue
        if skipped < offset:
            skipped += 1
            continue
        base = str(data.get("base_url") or d.name).strip() or d.name
        brand_name, favicon_url = _list_row_brand_and_favicon(d, base)
        try:
            from api.audit_pipeline_status import get_audit_run_phase

            pipeline = get_audit_run_phase(d)
            still_running = bool(pipeline.get("still_running"))
            pipeline_phase = str(pipeline.get("phase") or "idle")
        except Exception:
            still_running = False
            pipeline_phase = "idle"
        rows.append(
            {
                "id": d.name,
                "audit_dir": audit_dir_api_rel(d),
                "base_url": base,
                "brand_name": brand_name,
                "favicon_url": favicon_url,
                "modified_at": datetime.fromtimestamp(m, tz=UTC).isoformat(),
                "overall_score": data.get("overall_score"),
                "still_running": still_running,
                "pipeline_phase": pipeline_phase,
            }
        )
        if limit is not None and limit >= 0 and len(rows) >= limit:
            break
    return rows


def resolve_audit_dir(rel_or_abs: str) -> Path:
    """
    Resolve an audit directory from a repo-relative path or a short folder id.

    The web UI routes use only the folder name (e.g. ``www.example.com_abc123``) because
    React Router ``:param`` cannot span slashes in ``audit_output/...`` paths.
    """
    raw = (rel_or_abs or "").strip().strip("/")
    if raw.endswith("/report.html"):
        raw = raw[: -len("/report.html")].strip("/")
    if not raw:
        return audit_output_base()

    if "/" not in raw and "\\" not in raw:
        candidate = (audit_output_base() / raw).resolve()
        if candidate.is_dir():
            return candidate

    p = Path(raw)
    if not p.is_absolute():
        parts = p.parts
        if parts and parts[0] in (DEFAULT_OUT_BASE, "audit_archive"):
            p = (data_root() / p).resolve()
        else:
            p = (REPO_ROOT / p).resolve()
    return p


_EMBED_NAV_SCRIPT = (
    "<script>"
    "(function(){"
    'function notifyParent(section){try{if(window.parent!==window)'
    'window.parent.postMessage({type:"geo-report-nav",section:section},"*");}catch(e){}}'
    'document.querySelectorAll("a.report-pillar-cta[href^=\'#\']").forEach(function(a){'
    'a.addEventListener("click",function(ev){var sec=(a.getAttribute("href")||"").replace(/^#/,"");'
    "if(!sec)return;if(window.parent!==window){ev.preventDefault();notifyParent(sec);}});});"
    # Only notify on user-driven hash changes — not on initial load.
    # Initial notify races with sibling iframes (e.g. a hidden ga4-traffic embed)
    # and can overwrite the React sidebar selection.
    'window.addEventListener("hashchange",function(){'
    'var h=(location.hash||"").replace(/^#/,"");if(h)notifyParent(h);});'
    "})();"
    "</script>"
)

_EMBED_CHROME_STYLE = (
    '<style id="geo-app-embed-chrome">'
    "body.geo-report.geo-report-app-embed .header,"
    "body.geo-report.geo-report-app-embed .report-tabs-wrap{display:none!important;}"
    "body.geo-report.geo-report-app-embed{background:#e8e5e0!important;}"
    "body.geo-report.geo-report-app-embed #tab-panel-competitors{background:#fff!important;}"
    "body.geo-report.geo-report-app-embed .report-main-with-tabs.container{"
    "max-width:none;padding:8px 0 32px;width:100%;}"
    "body.geo-report.geo-report-app-embed .report-block,"
    "body.geo-report.geo-report-app-embed .section{margin-bottom:16px;}"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .ga4-insights-callout,"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .section-lead,"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .table-note{display:none!important;}"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .report-block--footer,"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .data-table,"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .chart-panel{display:none!important;}"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .ga4-chart-sessions,"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .ga4-chart-by-source{"
    "display:block!important;margin-top:20px;padding-top:38px;position:relative;}"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .ga4-chart-sessions::before,"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .ga4-chart-by-source::before{"
    "color:#0d0d0d;font-size:15px;font-weight:700;left:0;position:absolute;top:12px;}"
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .ga4-chart-sessions::before{"
    'content:"AI sessions over time";}'
    "body.geo-report.geo-report-app-embed #tab-panel-ga4-traffic .ga4-chart-by-source::before{"
    'content:"Sessions by AI platform over time";}'
    "body.geo-report.geo-report-app-embed .cmp-card{"
    "background:#fff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden;}"
    "body.geo-report.geo-report-app-embed .cmp-card-head{border-bottom:1px solid #f3f4f6;padding:16px 20px;}"
    "body.geo-report.geo-report-app-embed .cmp-card-head h3{color:#0d0d0d;font-size:14px;font-weight:600;margin:0;}"
    "body.geo-report.geo-report-app-embed .cmp-card-head p{color:#9ca3af;font-size:11px;margin:4px 0 0;}"
    "body.geo-report.geo-report-app-embed .cmp-card-body{overflow-x:auto;}"
    "body.geo-report.geo-report-app-embed .cmp-table{border-collapse:collapse;min-width:560px;table-layout:fixed;width:100%;}"
    "body.geo-report.geo-report-app-embed .cmp-table thead th{"
    "background:#f9fafb;border-bottom:1px solid #f3f4f6;color:#9ca3af;font-size:10px;font-weight:600;"
    "letter-spacing:.04em;padding:12px 16px;text-align:left;text-transform:uppercase;}"
    "body.geo-report.geo-report-app-embed .cmp-table thead th.cmp-col-score{text-align:right;}"
    "body.geo-report.geo-report-app-embed .cmp-table tbody th,"
    "body.geo-report.geo-report-app-embed .cmp-table tbody td{"
    "border-bottom:1px solid #f9fafb;padding:12px 16px;vertical-align:middle;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-brand-cell,"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-peer-cell,"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-toggle-label{"
    "align-items:center;display:flex;gap:8px;min-width:0;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-favicon{border-radius:3px;flex:0 0 auto;object-fit:contain;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-brand-link,"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-brand-name{"
    "font-size:14px;font-weight:600;overflow:hidden;text-decoration:none;text-overflow:ellipsis;white-space:nowrap;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-brand-link--own{color:#047857;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-brand-name{color:#1f2937;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-row--brand th,"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-row--brand td{background:rgba(236,253,245,.4);}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-score-cell{text-align:right;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-score-value{"
    "font-size:14px;font-variant-numeric:tabular-nums;font-weight:600;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-score-value.score-tone-green{color:var(--score-green);}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-score-value.score-tone-blue{color:var(--score-blue);}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-score-value.score-tone-yellow{color:var(--score-yellow);}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-score-value.score-tone-red{color:var(--score-red);}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-toggle{background:transparent;border:0;flex:1 1 auto;margin:0;min-width:0;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-toggle summary{"
    "align-items:center;border-radius:6px;cursor:pointer;display:flex;gap:8px;list-style:none;margin:-4px -6px;padding:4px 6px;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-toggle summary::-webkit-details-marker{display:none;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-toggle summary::before{"
    "border-color:transparent transparent transparent #9ca3af;border-style:solid;border-width:4px 0 4px 6px;"
    "content:'';flex:0 0 auto;height:0;transition:transform .15s ease;width:0;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-toggle[open] summary::before{transform:rotate(90deg);}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-peer-group:not(:has(.cmp-toggle[open])) .cmp-row--detail{display:none;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-row--detail th,"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-row--detail td{"
    "background:rgba(249,250,251,.6);border-bottom:1px solid #f3f4f6;vertical-align:top;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-detail-kicker{"
    "color:#6b7280;display:block;font-size:12px;font-weight:600;margin-bottom:8px;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-visit{"
    "color:#1d4ed8;display:inline-block;font-size:12px;font-weight:600;text-decoration:none;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-insight-stack{display:grid;gap:16px;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-side-title{font-size:12px;font-weight:700;margin:0 0 8px;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-side-title--brand{color:#047857;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-side-title--peer{color:#374151;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-topic-list{list-style:none;margin:0;padding:0;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-topic-list>li+li{margin-top:8px;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-topic-card{"
    "background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-side-title--brand+.cmp-topic-list .cmp-topic-card{border-color:#d1fae5;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-topic-title{"
    "color:#1f2937;font-size:12px;font-weight:600;line-height:1.4;margin:0;}"
    "body.geo-report.geo-report-app-embed .cmp-table .cmp-empty{color:#9ca3af;font-size:12px;font-style:italic;margin:0;}"
    "html,body.geo-report.geo-report-app-embed{min-height:0!important;height:auto!important;}"
    "</style>"
)

_REPORT_HEADER_RE = re.compile(r'<header class="header">.*?</header>\s*', re.DOTALL | re.IGNORECASE)


def load_fresh_report_html(audit_dir: Path) -> str:
    """
    Load report.html with live enrichments applied (competitor comparison scores,
    brand-visibility citation backfill). Used for PDF/HTML downloads so exports
    match the current app view rather than a stale on-disk snapshot alone.
    """
    report_path = audit_dir / "report.html"
    if not report_path.is_file():
        raise FileNotFoundError(f"report.html not found in {audit_dir}")
    html = report_path.read_text(encoding="utf-8", errors="replace")
    html = enrich_report_brand_visibility_from_citations(html, audit_dir)
    html = refresh_competitor_comparison_for_embed(html, audit_dir)
    return html


def refresh_competitor_comparison_for_embed(html: str, audit_dir: Path) -> str:
    """Rebuild the comparison table with the current scoring model for saved reports."""
    comp_path = audit_dir / "comparison.json"
    summary_path = audit_dir / "audit_summary.json"
    if not comp_path.is_file() or not summary_path.is_file():
        return html
    try:
        create_report = load_create_report()
        audit = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
        weights = dict(create_report.DEFAULT_WEIGHTS)
        total = sum(weights.values())
        if total and abs(total - 100.0) > 0.01:
            weights = {key: value * 100.0 / total for key, value in weights.items()}
        _strengths, _improvements, table_html, _detail = (
            create_report.build_competitive_section(
                comp_path,
                str(audit.get("base_url") or ""),
                weights,
            )
        )
    except Exception as exc:
        # Keep the saved table if live rebuild fails (e.g. greenlet/thread conflicts).
        log.warning("refresh_competitor_comparison_for_embed failed: %s", exc)
        return html

    def replace_panel(match: re.Match[str]) -> str:
        panel = match.group(0)
        panel = re.sub(
            r'<p class="section-lead"[^>]*>[\s\S]*?</p>',
            (
                '<p class="section-lead">Compare AI visibility, technical setup, and '
                "content quality scores from competitor site crawls.</p>"
            ),
            panel,
            count=1,
            flags=re.IGNORECASE,
        )
        return re.sub(
            r'(?:<div class="cmp-card"[\s\S]*?</div>\s*</div>|<table class="(?:data-table )?cmp-table"[^>]*>[\s\S]*?</table>)',
            table_html,
            panel,
            count=1,
            flags=re.IGNORECASE,
        )

    return re.sub(
        r'<div class="report-tab-panel"[^>]*id="tab-panel-competitors"[\s\S]*?'
        r'(?=<div class="report-tab-panel"|</main>)',
        replace_panel,
        html,
        count=1,
        flags=re.IGNORECASE,
    )


def _slim_score_components(components: list[Any] | None) -> list[dict[str, Any]]:
    """Keep criterion fields needed by the comparison expand UI (Summary-aligned)."""
    slim: list[dict[str, Any]] = []
    for raw in components or []:
        if not isinstance(raw, dict) or not raw.get("key"):
            continue
        finding = str(raw.get("finding_summary") or "").strip()
        if not finding:
            finding = str(raw.get("detail") or "").strip() or "No specific finding was recorded."
        strengths = [
            str(item).strip()
            for item in (raw.get("strengths") or [])
            if str(item).strip()
        ]
        improvements = [
            str(item).strip()
            for item in (raw.get("improvements") or [])
            if str(item).strip()
        ]
        evidence = str(raw.get("evidence_example") or "").strip()
        detail = str(raw.get("detail") or "").strip()
        item = {
            "key": str(raw.get("key") or ""),
            "title": str(raw.get("title") or raw.get("key") or ""),
            "score": round(float(raw.get("score") or 0.0), 1),
            "weight_pct": float(raw.get("weight_pct") or 0.0),
            "finding_summary": finding,
            "detail": detail,
            "evidence_example": evidence or detail,
        }
        if strengths:
            item["strengths"] = strengths
        if improvements:
            item["improvements"] = improvements
        slim.append(item)
    return slim


def _overview_components_for_pillar(
    create_report: Any,
    pillar: str,
    components: list[Any] | None,
) -> list[dict[str, Any]]:
    """Group leaf score components into Summary / Overview criteria."""
    slim = _slim_score_components(components)
    if pillar == "technical_setup":
        return create_report.group_technical_setup_components(slim)
    if pillar == "content_quality":
        return create_report.group_content_quality_components(slim)
    return slim


def _ai_visibility_components_from_probe_metrics(
    name: str,
    metrics: dict[str, Any],
) -> list[dict[str, Any]]:
    """Summary-style Brand visibility + SOV components from probe metrics."""
    brand_name = str(name or "Brand").strip() or "Brand"
    visibility_pct = float(metrics.get("visibility_pct") or 0.0)
    visible = int(metrics.get("visible_prompt_count") or metrics.get("visible_response_count") or 0)
    total = int(metrics.get("prompt_count") or metrics.get("response_count") or 0)
    sov_score = float(metrics.get("sov_performance_score") or 0.0)
    sov_rank = metrics.get("sov_rank")
    competitor_count = int(metrics.get("competitor_count") or 0)
    sov_pct = float(metrics.get("sov_pct") or 0.0)
    top_competitor_sov = float(metrics.get("top_competitor_sov_pct") or 0.0)
    avg_competitor_sov = float(metrics.get("average_competitor_sov_pct") or 0.0)
    if sov_rank is None:
        sov_finding = (
            f"{brand_name} and peers had no measurable share of voice in the tested responses."
        )
    else:
        sov_finding = (
            f"{brand_name} ranks #{int(sov_rank)} against the {competitor_count} "
            f"most-mentioned competitors, producing a {format_report_score(sov_score)}/100 relative SOV score."
        )
    return [
        {
            "key": "brand_visibility",
            "title": "Brand visibility",
            "score": round(visibility_pct, 1),
            "weight_pct": 60.0,
            "detail": (
                "Platform responses mentioning the brand divided by all platform "
                "responses analysed."
            ),
            "finding_summary": (
                f"{brand_name} appeared in {visible} of {total} analysed platform responses."
            ),
            "evidence_example": (
                f"{visibility_pct:.1f}% response-level visibility across the tested AI platforms."
            ),
        },
        {
            "key": "share_of_voice",
            "title": "Share of voice performance",
            "score": round(sov_score, 1),
            "weight_pct": 40.0,
            "detail": (
                "Relative rank against the most-mentioned website-backed competitors."
            ),
            "finding_summary": sov_finding,
            "evidence_example": (
                f"{brand_name}: {sov_pct:.1f}% raw SOV. Top competitor: "
                f"{top_competitor_sov:.1f}%; average competitor: {avg_competitor_sov:.1f}%."
            ),
        },
    ]


def _relative_sov_performance(
    focal_hits: float,
    peer_hits_by_name: dict[str, float],
    *,
    sov_competitor_limit: int = 10,
) -> tuple[float, int | None, int]:
    """Mirror Summary relative SOV: focal entity vs top-N peers."""
    peer_hits = sorted(
        (float(value) for value in peer_hits_by_name.values() if float(value) > 0),
        reverse=True,
    )[:sov_competitor_limit]
    if focal_hits <= 0:
        rank = len(peer_hits) + 1 if peer_hits else None
        return 0.0, rank, len(peer_hits)
    if not peer_hits:
        return 100.0, 1, 0
    below = sum(1 for value in peer_hits if value < focal_hits)
    tied = sum(1 for value in peer_hits if abs(value - focal_hits) <= 1e-9)
    ahead = sum(1 for value in peer_hits if value > focal_hits)
    score = 100.0 * (below + 0.5 * tied) / len(peer_hits)
    return score, ahead + 1, len(peer_hits)


def load_probe_entity_visibility_metrics(audit_dir: Path) -> dict[str, Any] | None:
    """
    Per website-backed entity visibility + relative SOV from live probes.

    Used to score competitor AI Visibility in the comparison table when the
    competitor URL/domain matches a probe-surfaced (or wizard-configured) entity.
    Keys are website entity keys (same as brandVisibilityRows / prompt metrics).
    """
    from api.audit_json_cache import load_json_cached

    sov_competitor_limit = 10
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    if not probe_path.is_file():
        return None
    raw = load_json_cached(probe_path)
    if not isinstance(raw, dict):
        return None
    live = raw.get("live_probe", raw) if isinstance(raw, dict) else {}
    if not isinstance(live, dict):
        return None
    rows = live.get("per_prompt")
    if not isinstance(rows, list) or not rows:
        return None

    platforms = ("gemini", "openai", "google_aio", "claude")
    brand_tokens = [
        str(token).lower()
        for token in [live.get("brand_name"), *(live.get("brand_match_tokens") or [])]
        if str(token or "").strip()
    ]
    brand_name = str(live.get("brand_name") or "").strip() or "Brand"
    alias_to_key = _load_website_backed_competitor_aliases(audit_dir, live)
    key_to_website: dict[str, str] = {}
    key_to_name: dict[str, str] = {}

    # Prefer wizard / onboarding websites for entity → URL mapping.
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            onboarding = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            onboarding = {}
        if isinstance(onboarding, dict):
            if not brand_name:
                brand_name = str(onboarding.get("brand_name_used") or "").strip() or brand_name
            for row in onboarding.get("competitors_detail") or onboarding.get("competitor_context") or []:
                if not isinstance(row, dict):
                    continue
                website = str(row.get("competitor_website") or "").strip()
                name = str(row.get("competitor_brand") or "").strip()
                key = _website_entity_key(website)
                if key:
                    key_to_website[key] = website
                    if name:
                        key_to_name[key] = name

    def accept_competitor(name: str) -> str | None:
        normalized = str(name).strip().lower()
        if not normalized:
            return None
        stem = _stem_brand_label(normalized)
        if normalized in alias_to_key:
            return alias_to_key[normalized]
        if stem and stem in alias_to_key:
            return alias_to_key[stem]
        if _is_domain_string(normalized):
            return _website_entity_key(normalized) or stem or normalized
        return None

    brand_hits = 0.0
    brand_visible = 0
    response_count = 0
    competitor_hits_by_key: dict[str, float] = {}
    competitor_visible_by_key: dict[str, int] = {}

    for row in rows:
        if not isinstance(row, dict):
            continue
        for platform in platforms:
            raw_runs = (row.get("runs") or {}).get(platform) or []
            completed_runs = [
                run
                for run in raw_runs
                if isinstance(run, dict) and run.get("response") and not run.get("error")
            ]
            response_rows = completed_runs or [
                {
                    "response": row.get(f"{platform}_response"),
                    "mention_scores": row.get(f"mention_scores_{platform}") or {},
                }
            ]
            for run in response_rows:
                response = str(run.get("response") or "").lower()
                if not response:
                    continue
                scores = run.get("mention_scores") or {}
                stored_signal = float(scores.get("brand_signal") or 0) if isinstance(scores, dict) else 0.0
                visible = stored_signal > 0 or any(token in response for token in brand_tokens)
                response_count += 1
                if visible:
                    brand_visible += 1
                signal = stored_signal if stored_signal > 0 else (1.0 if visible else 0.0)
                brand_hits += signal
                detail = scores.get("competitor_detail") or {} if isinstance(scores, dict) else {}
                mentioned_keys: set[str] = set()
                if isinstance(detail, dict):
                    for name, value in detail.items():
                        canonical = accept_competitor(str(name))
                        if not canonical:
                            continue
                        competitor_hits_by_key[canonical] = (
                            competitor_hits_by_key.get(canonical, 0.0) + float(value or 0)
                        )
                        if float(value or 0) > 0:
                            mentioned_keys.add(canonical)
                        if _is_domain_string(str(name)) and canonical not in key_to_website:
                            key_to_website[canonical] = str(name)
                        if canonical not in key_to_name:
                            key_to_name[canonical] = str(name)
                for key in mentioned_keys:
                    competitor_visible_by_key[key] = competitor_visible_by_key.get(key, 0) + 1

    if response_count <= 0:
        return None

    brand_visibility_pct = 100.0 * brand_visible / response_count
    brand_sov_score, brand_sov_rank, brand_peer_count = _relative_sov_performance(
        brand_hits,
        competitor_hits_by_key,
        sov_competitor_limit=sov_competitor_limit,
    )
    brand_score = min(100.0, 0.60 * brand_visibility_pct + 0.40 * brand_sov_score)
    entities: dict[str, dict[str, Any]] = {
        "__brand__": {
            "entity_key": "__brand__",
            "is_primary": True,
            "name": brand_name,
            "website": "",
            "visibility_pct": round(brand_visibility_pct, 1),
            "visible_prompt_count": brand_visible,
            "prompt_count": response_count,
            "sov_performance_score": round(brand_sov_score, 1),
            "sov_rank": brand_sov_rank,
            "competitor_count": brand_peer_count,
            "score": round(brand_score, 1),
            "hits": round(brand_hits, 1),
        }
    }

    for key, hits in competitor_hits_by_key.items():
        peers = {peer: value for peer, value in competitor_hits_by_key.items() if peer != key}
        if brand_hits > 0:
            peers["__brand__"] = brand_hits
        visibility_pct = 100.0 * competitor_visible_by_key.get(key, 0) / response_count
        sov_score, sov_rank, peer_count = _relative_sov_performance(
            float(hits),
            peers,
            sov_competitor_limit=sov_competitor_limit,
        )
        score = min(100.0, 0.60 * visibility_pct + 0.40 * sov_score)
        entities[key] = {
            "entity_key": key,
            "is_primary": False,
            "name": key_to_name.get(key) or key,
            "website": key_to_website.get(key) or "",
            "visibility_pct": round(visibility_pct, 1),
            "visible_prompt_count": int(competitor_visible_by_key.get(key, 0)),
            "prompt_count": response_count,
            "sov_performance_score": round(sov_score, 1),
            "sov_rank": sov_rank,
            "competitor_count": peer_count,
            "score": round(score, 1),
            "hits": round(float(hits), 1),
        }

    return {
        "response_count": response_count,
        "alias_to_key": alias_to_key,
        "entities": entities,
    }


def _match_probe_entity_for_url(
    url: str,
    probe_entities: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Match a comparison-row URL/domain to a probe website-backed competitor entity."""
    if not probe_entities or not isinstance(probe_entities, dict):
        return None
    entities = probe_entities.get("entities") or {}
    if not isinstance(entities, dict):
        return None
    alias_to_key = probe_entities.get("alias_to_key") or {}
    host = _website_host(url)
    key = _website_entity_key(url)
    candidates = [
        key,
        _stem_brand_label(host),
        host,
        host.split(".")[0] if host else "",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        resolved = alias_to_key.get(candidate) or alias_to_key.get(_stem_brand_label(candidate))
        entity_key = resolved or candidate
        entity = entities.get(entity_key)
        if isinstance(entity, dict) and not entity.get("is_primary"):
            return entity
    # Direct website field match as last resort.
    target_host = host
    for entity in entities.values():
        if not isinstance(entity, dict) or entity.get("is_primary"):
            continue
        if target_host and _website_host(str(entity.get("website") or "")) == target_host:
            return entity
    return None


def _normalize_finding_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _text_mentions_name(text: str, name: str) -> bool:
    token = re.sub(r"[^a-z0-9]+", "", (name or "").lower())
    if len(token) < 3:
        return False
    hay = re.sub(r"[^a-z0-9]+", "", (text or "").lower())
    return token in hay


def _evidence_host(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" in raw or raw.startswith("www."):
        return _website_host(raw)
    # Snippet may embed a URL — pull the first http(s) occurrence.
    match = re.search(r"https?://[^\s)\"']+", raw)
    if match:
        return _website_host(match.group(0))
    return ""


def _competitor_dir_for_url(audit_dir: Path, url: str) -> Path | None:
    """Resolve ``competitors/<safe_host>/`` for a comparison row URL when present."""
    root = audit_dir / "competitors"
    if not root.is_dir():
        return None
    try:
        crawl = load_crawl_site()
        candidate = root / crawl.safe_dir_name(crawl.normalize_base(url))
        if (candidate / "audit_summary.json").is_file():
            return candidate
    except Exception:
        pass
    host = _website_host(url)
    if not host:
        return None
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if host in child.name.lower() and (child / "audit_summary.json").is_file():
            return child
    return None


def _component_has_verified_finding(component: dict[str, Any]) -> bool:
    finding = str(component.get("finding_summary") or "").strip()
    evidence = str(component.get("evidence_example") or "").strip()
    if not finding and not evidence:
        return False
    placeholders = {
        "no specific finding was recorded.",
        "finding not available.",
        "criterion findings are not available for this crawl.",
    }
    if _normalize_finding_text(finding) in placeholders and not evidence:
        return False
    return True


def filter_competitor_verified_components(
    components: list[dict[str, Any]] | None,
    *,
    brand_name: str,
    competitor_name: str,
    brand_components: list[dict[str, Any]] | None = None,
    primary_host: str = "",
    competitor_host: str = "",
) -> list[dict[str, Any]]:
    """Keep score rows; strip finding blurbs that belong to the primary brand.

    Misattribution signals:
    - Finding/evidence text equals the brand-side finding for the same criterion key
    - Finding mentions the primary brand and not the competitor
    - Evidence URL host matches the primary site (and not the competitor)
    """
    brand_by_key = {
        str(item.get("key") or ""): item
        for item in (brand_components or [])
        if isinstance(item, dict) and item.get("key")
    }
    out: list[dict[str, Any]] = []
    for raw in components or []:
        if not isinstance(raw, dict) or not raw.get("key"):
            continue
        component = dict(raw)
        key = str(component.get("key") or "")
        finding = str(component.get("finding_summary") or "").strip()
        evidence = str(component.get("evidence_example") or "").strip()
        brand_row = brand_by_key.get(key) if isinstance(brand_by_key.get(key), dict) else {}
        brand_finding = str((brand_row or {}).get("finding_summary") or "").strip()
        brand_evidence = str((brand_row or {}).get("evidence_example") or "").strip()

        attributed_to_brand = False
        if finding and brand_finding and _normalize_finding_text(finding) == _normalize_finding_text(
            brand_finding
        ):
            attributed_to_brand = True
        if evidence and brand_evidence and _normalize_finding_text(evidence) == _normalize_finding_text(
            brand_evidence
        ):
            attributed_to_brand = True
        if (
            brand_name
            and finding
            and _text_mentions_name(finding, brand_name)
            and not _text_mentions_name(finding, competitor_name)
        ):
            attributed_to_brand = True
        if (
            brand_name
            and evidence
            and _text_mentions_name(evidence, brand_name)
            and not _text_mentions_name(evidence, competitor_name)
        ):
            attributed_to_brand = True

        ev_host = _evidence_host(evidence)
        if (
            primary_host
            and ev_host
            and ev_host == primary_host
            and (not competitor_host or ev_host != competitor_host)
        ):
            attributed_to_brand = True

        if attributed_to_brand:
            component["finding_summary"] = ""
            component["evidence_example"] = ""
            component["strengths"] = []
            component["improvements"] = []
            component["verified"] = False
        else:
            component["verified"] = _component_has_verified_finding(component)
            if not component["verified"]:
                # Score-only: drop placeholder blurbs so the UI shows score alone.
                component["finding_summary"] = ""
                component["evidence_example"] = ""
        out.append(component)
    return out


def _apply_competitor_gemini_content_overlay(
    audit_dir: Path,
    row: dict[str, Any],
) -> None:
    """Merge per-competitor ``content_quality_gemini.json`` into comparison CQ scores."""
    try:
        from content_quality_llm import (
            apply_gemini_to_content_components,
            load_cached_content_quality_gemini,
        )
    except Exception:
        return

    comp_dir = _competitor_dir_for_url(audit_dir, str(row.get("url") or ""))
    if comp_dir is None:
        return
    gemini = load_cached_content_quality_gemini(comp_dir)
    if not gemini:
        return
    rationale = dict(row.get("content_quality_rationale") or {})
    components = list(rationale.get("components") or [])
    merged, pillar_score = apply_gemini_to_content_components(components, gemini)
    if merged:
        rationale["components"] = merged
        rationale["gemini_overlay"] = {
            "available": True,
            "pages_analyzed": int(gemini.get("pages_analyzed") or 0),
            "merge_rule": str(gemini.get("merge_rule") or "replace_eeat_and_answerability"),
        }
        row["content_quality_rationale"] = rationale
    if pillar_score is not None:
        row["content_quality"] = float(pillar_score)
        row["overall"] = round(
            0.40 * float(row.get("ai_visibility") or 0)
            + 0.30 * float(row.get("technical_setup") or 0)
            + 0.30 * float(row["content_quality"]),
            1,
        )


def load_competitive_comparison(audit_dir: Path) -> dict[str, Any]:
    """JSON rows for the React competitor comparison table."""
    create_report = load_create_report()
    summary_path = audit_dir / "audit_summary.json"
    primary_url = ""
    brand_name = ""
    if summary_path.is_file():
        try:
            audit = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
            primary_url = str(audit.get("base_url") or "")
        except (OSError, json.JSONDecodeError):
            primary_url = ""
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            onboarding = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(onboarding, dict):
                brand_name = str(onboarding.get("brand_name_used") or "").strip()
        except (OSError, json.JSONDecodeError):
            brand_name = ""
    weights = dict(create_report.DEFAULT_WEIGHTS)
    total = sum(weights.values())
    if total and abs(total - 100.0) > 0.01:
        weights = {key: value * 100.0 / total for key, value in weights.items()}
    rows = create_report.competitive_comparison_rows(
        audit_dir / "comparison.json",
        primary_url,
        weights,
    )
    # Primary brand pillars must match Summary / AI Visibility Overview.
    # Competitor AI Visibility prefers probe visibility+SOV when the competitor
    # URL matches a website-backed entity from wizard input / prompt replies.
    integrated = load_integrated_scores(audit_dir)
    prompt_metrics = integrated.get("prompt_metrics") or load_prompt_visibility_metrics(audit_dir)
    probe_entities = load_probe_entity_visibility_metrics(audit_dir)
    ai = integrated.get("ai_visibility")
    technical = integrated.get("technical_setup")
    content = integrated.get("content_structure")
    primary_host = _website_host(primary_url)

    brand_ai_components = (
        _ai_visibility_components_from_probe_metrics(
            brand_name
            or next((str(row.get("name") or "") for row in rows if row.get("is_primary")), "Your brand"),
            prompt_metrics if isinstance(prompt_metrics, dict) else {},
        )
        if isinstance(prompt_metrics, dict) and prompt_metrics
        else []
    )
    brand_tech_components = _overview_components_for_pillar(
        create_report,
        "technical_setup",
        ((integrated.get("details") or {}).get("technical_setup") or {}).get("components"),
    )
    brand_content_components = _overview_components_for_pillar(
        create_report,
        "content_quality",
        ((integrated.get("details") or {}).get("content_structure") or {}).get("components"),
    )

    for row in rows:
        # Ensure crawl-built tech/content criteria stay Overview-shaped even if
        # an older comparison payload still carries leaf keys.
        for pillar_key, pillar in (
            ("technical_setup_rationale", "technical_setup"),
            ("content_quality_rationale", "content_quality"),
        ):
            rationale = dict(row.get(pillar_key) or {})
            if rationale.get("components"):
                rationale["components"] = _overview_components_for_pillar(
                    create_report, pillar, rationale.get("components")
                )
                row[pillar_key] = rationale

        if row.get("is_primary"):
            if isinstance(ai, (int, float)):
                row["ai_visibility"] = float(ai)
            if isinstance(technical, (int, float)):
                row["technical_setup"] = float(technical)
            if isinstance(content, (int, float)):
                row["content_quality"] = float(content)
            row["overall"] = round(
                0.40 * float(row["ai_visibility"])
                + 0.30 * float(row["technical_setup"])
                + 0.30 * float(row["content_quality"]),
                1,
            )
            if brand_ai_components:
                rationale = dict(row.get("ai_visibility_rationale") or {})
                rationale["components"] = brand_ai_components
                rationale["source"] = "prompt_visibility"
                row["ai_visibility_rationale"] = rationale
            if brand_tech_components:
                rationale = dict(row.get("technical_setup_rationale") or {})
                rationale["components"] = brand_tech_components
                row["technical_setup_rationale"] = rationale
            if brand_content_components:
                rationale = dict(row.get("content_quality_rationale") or {})
                rationale["components"] = brand_content_components
                row["content_quality_rationale"] = rationale
            continue

        # Competitor Gemini CQ overlay (per-site cache under competitors/<host>/).
        _apply_competitor_gemini_content_overlay(audit_dir, row)

        # Competitor: attach brand side-by-side criterion findings for reference,
        # then keep only verified competitor-owned blurbs on the competitor side.
        competitor_name = str(row.get("name") or "Competitor").strip() or "Competitor"
        competitor_host = _website_host(str(row.get("url") or ""))
        for pillar_key, brand_components in (
            ("ai_visibility_rationale", brand_ai_components),
            ("technical_setup_rationale", brand_tech_components),
            ("content_quality_rationale", brand_content_components),
        ):
            rationale = dict(row.get(pillar_key) or {})
            if brand_components:
                rationale["brand_components"] = brand_components
            filtered = filter_competitor_verified_components(
                rationale.get("components"),
                brand_name=brand_name,
                competitor_name=competitor_name,
                brand_components=brand_components,
                primary_host=primary_host,
                competitor_host=competitor_host,
            )
            rationale["components"] = filtered
            rationale["has_verified_findings"] = any(
                bool(c.get("verified")) for c in filtered if isinstance(c, dict)
            )
            row[pillar_key] = rationale

        matched = _match_probe_entity_for_url(str(row.get("url") or ""), probe_entities)
        ai_rationale = dict(row.get("ai_visibility_rationale") or {})
        if matched:
            row["ai_visibility"] = float(matched["score"])
            ai_rationale["components"] = filter_competitor_verified_components(
                _ai_visibility_components_from_probe_metrics(
                    competitor_name or str(matched.get("name") or "Competitor"),
                    matched,
                ),
                brand_name=brand_name,
                competitor_name=competitor_name,
                brand_components=brand_ai_components,
                primary_host=primary_host,
                competitor_host=competitor_host,
            )
            ai_rationale["source"] = "prompt_visibility"
            ai_rationale["matched_entity_key"] = matched.get("entity_key")
            ai_rationale["has_verified_findings"] = any(
                bool(c.get("verified")) for c in (ai_rationale.get("components") or [])
            )
            row["overall"] = round(
                0.40 * float(row["ai_visibility"])
                + 0.30 * float(row["technical_setup"])
                + 0.30 * float(row["content_quality"]),
                1,
            )
        else:
            # Fallback: keep crawl-scored AI Visibility components from score_audit.
            ai_rationale["source"] = "crawl_fallback"
            if not ai_rationale.get("components"):
                ai_rationale["components"] = []
            # Ensure Brand visibility / SOV rows exist so the UI stays Summary-shaped.
            existing_keys = {
                str(component.get("key") or "")
                for component in (ai_rationale.get("components") or [])
                if isinstance(component, dict)
            }
            if "brand_visibility" not in existing_keys or "share_of_voice" not in existing_keys:
                crawl_components = list(ai_rationale.get("components") or [])
                brand_vis = next(
                    (
                        component
                        for component in crawl_components
                        if isinstance(component, dict)
                        and component.get("key") in {
                            "brand_visibility",
                            "brand_entity_visibility",
                            "brand_visibility_authority",
                        }
                    ),
                    None,
                )
                brand_finding = ""
                if isinstance(brand_vis, dict):
                    strengths = [
                        str(item).strip()
                        for item in (brand_vis.get("strengths") or [])
                        if str(item).strip()
                    ]
                    improvements = [
                        str(item).strip()
                        for item in (brand_vis.get("improvements") or [])
                        if str(item).strip()
                    ]
                    if strengths and improvements:
                        brand_finding = f"{strengths[0]} Needs work: {improvements[0]}."
                    else:
                        brand_finding = str(
                            brand_vis.get("finding_summary")
                            or (strengths[0] if strengths else "")
                            or (improvements[0] if improvements else "")
                            or brand_vis.get("detail")
                            or ""
                        ).strip()
                if not brand_finding:
                    brand_finding = (
                        "No prompt-visibility match for this competitor URL; "
                        "showing crawl brand-visibility signals instead."
                    )
                evidence = ""
                if isinstance(brand_vis, dict):
                    evidence = str(
                        brand_vis.get("evidence_example")
                        or brand_vis.get("detail")
                        or ""
                    ).strip()
                summary_shaped = [
                    {
                        "key": "brand_visibility",
                        "title": "Brand visibility",
                        "score": float(
                            (brand_vis or {}).get("score")
                            or row.get("ai_visibility")
                            or 0.0
                        ),
                        "weight_pct": 60.0,
                        "detail": (
                            "Crawl-derived brand/entity visibility signals used when "
                            "this competitor URL is not matched in live probes."
                        ),
                        "finding_summary": brand_finding,
                        "evidence_example": evidence or brand_finding,
                        "strengths": (brand_vis or {}).get("strengths"),
                        "improvements": (brand_vis or {}).get("improvements"),
                    },
                    {
                        "key": "share_of_voice",
                        "title": "Share of voice performance",
                        "score": 0.0,
                        "weight_pct": 40.0,
                        "detail": (
                            "Relative rank against website-backed competitors from live probes."
                        ),
                        "finding_summary": (
                            "Share of voice is measured from live probes. This competitor URL "
                            "was not matched in prompt visibility data, so SOV is unavailable."
                        ),
                        "evidence_example": (
                            "SOV requires a website-backed match in analysed platform responses."
                        ),
                    },
                ]
                ai_rationale["components"] = filter_competitor_verified_components(
                    summary_shaped,
                    brand_name=brand_name,
                    competitor_name=competitor_name,
                    brand_components=brand_ai_components,
                    primary_host=primary_host,
                    competitor_host=competitor_host,
                )
                ai_rationale["crawl_components"] = crawl_components
            else:
                ai_rationale["components"] = filter_competitor_verified_components(
                    ai_rationale.get("components"),
                    brand_name=brand_name,
                    competitor_name=competitor_name,
                    brand_components=brand_ai_components,
                    primary_host=primary_host,
                    competitor_host=competitor_host,
                )
            ai_rationale["has_verified_findings"] = any(
                bool(c.get("verified")) for c in (ai_rationale.get("components") or [])
            )
        row["ai_visibility_rationale"] = ai_rationale

    rows.sort(key=lambda row: float(row["overall"]), reverse=True)
    return {
        "rows": rows,
        "has_comparison": (audit_dir / "comparison.json").is_file(),
    }


def prepare_report_html_for_embed(html: str) -> str:
    """
    Strip duplicate report header / horizontal tabs for the React app iframe.
    Sidebar + site header in React drive section via ``#hash``.
    """
    if 'class="geo-report"' in html and "geo-report-app-embed" not in html:
        html = html.replace(
            'class="geo-report"',
            'class="geo-report geo-report-app-embed"',
            1,
        )
    html = _REPORT_HEADER_RE.sub("", html, count=1)
    html = re.sub(
        r'<div class="report-tabs-wrap"[^>]*>[\s\S]*?(?=\s*<main\b)',
        "",
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    def clean_ga4_panel(match: re.Match[str]) -> str:
        panel = match.group(0)
        panel = re.sub(
            r'(<header class="section-head" id="ga4-traffic-heading">\s*'
            r'<h2 class="section-title">).*?(</h2>)',
            r"\1Direct AI Traffic\2",
            panel,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        panel = re.sub(
            r'<div class="ga4-insights-callout[^"]*"[^>]*>[\s\S]*?</div>',
            "",
            panel,
            count=1,
            flags=re.IGNORECASE,
        )
        panel = re.sub(
            r'<p class="(?:section-lead|table-note)[^"]*"[^>]*>[\s\S]*?</p>',
            "",
            panel,
            flags=re.IGNORECASE,
        )
        panel = re.sub(
            r"<table[^>]*aria-label=['\"]Possible channel bucket gaps['\"][^>]*>"
            r"[\s\S]*?</table>",
            "",
            panel,
            flags=re.IGNORECASE,
        )
        panel = re.sub(
            r'<div class="chart-panel"([^>]*)><canvas id="ga4Chart"',
            r'<div class="chart-panel ga4-chart-sessions"\1><canvas id="ga4Chart"',
            panel,
            count=1,
            flags=re.IGNORECASE,
        )
        panel = re.sub(
            r'<div class="chart-panel"([^>]*)><canvas id="ga4AiSessionsBySourceChart"',
            r'<div class="chart-panel ga4-chart-by-source"\1><canvas id="ga4AiSessionsBySourceChart"',
            panel,
            count=1,
            flags=re.IGNORECASE,
        )
        return panel

    html = re.sub(
        r'<div class="report-tab-panel"[^>]*id="tab-panel-ga4-traffic"[\s\S]*?'
        r'(?=<div class="report-tab-panel"|</main>)',
        clean_ga4_panel,
        html,
        count=1,
        flags=re.IGNORECASE,
    )
    inject = _EMBED_CHROME_STYLE + _EMBED_NAV_SCRIPT
    if "</body>" in html.lower():
        html = re.sub(r"</body>", inject + "</body>", html, count=1, flags=re.IGNORECASE)
    else:
        html = html + inject
    return html


def enrich_report_brand_visibility_from_citations(html: str, audit_dir: Path) -> str:
    """Use brand-associated Reddit/YouTube citations when the off-site scan is empty."""
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    if not probe_path.is_file():
        return html
    try:
        raw = json.loads(probe_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return html
    live = raw.get("live_probe", raw) if isinstance(raw, dict) else {}
    if not isinstance(live, dict):
        return html

    from citation_context import (
        build_brand_tokens,
        build_competitor_tokens,
        infer_citation_brand_context,
    )

    brand_tokens = build_brand_tokens(
        str(live.get("brand_name") or ""),
        str(live.get("brand_site_url") or ""),
    )
    competitor_tokens = build_competitor_tokens(
        [str(value) for value in live.get("competitor_urls") or []],
        [str(value) for value in live.get("competitor_brands") or []]
        + [str(value) for value in live.get("reply_detected_brand_names") or []],
    )
    evidence: dict[str, dict[str, Any]] = {
        "Reddit": {"count": 0, "brand_count": 0, "url": ""},
        "YouTube": {"count": 0, "brand_count": 0, "url": ""},
    }

    for row in live.get("per_prompt") or []:
        if not isinstance(row, dict):
            continue
        for platform in ("gemini", "openai", "claude", "google_aio"):
            response_text = str(row.get(f"{platform}_response") or "")
            for citation in row.get(f"citations_{platform}") or []:
                if not isinstance(citation, dict):
                    continue
                domain = str(citation.get("domain") or "").lower().removeprefix("www.")
                label = (
                    "Reddit"
                    if domain == "redd.it" or domain == "reddit.com" or domain.endswith(".reddit.com")
                    else "YouTube"
                    if domain == "youtu.be" or domain == "youtube.com" or domain.endswith(".youtube.com")
                    else ""
                )
                if not label:
                    continue
                url = str(citation.get("url") or "")
                evidence[label]["count"] += 1
                if url and not evidence[label]["url"]:
                    evidence[label]["url"] = url
                inferred = infer_citation_brand_context(
                    response_text,
                    url,
                    domain,
                    brand_tokens,
                    competitor_tokens,
                    str(citation.get("title") or ""),
                )["brand_cited"]
                brand_cited = bool(citation.get("brand_cited")) or inferred
                if brand_cited:
                    evidence[label]["brand_count"] += 1

    # Older probe artifacts can retain aggregate URLs after prompt-level rows
    # have been compacted. Count those as presence evidence too.
    aggregate_urls = list(live.get("top_cited_urls") or [])
    aggregate = live.get("aggregate")
    if isinstance(aggregate, dict):
        aggregate_urls.extend(aggregate.get("top_cited_urls") or [])
    for citation in aggregate_urls:
        if not isinstance(citation, dict):
            continue
        domain = str(citation.get("domain") or "").lower().removeprefix("www.")
        label = (
            "Reddit"
            if domain == "redd.it" or domain == "reddit.com" or domain.endswith(".reddit.com")
            else "YouTube"
            if domain == "youtu.be" or domain == "youtube.com" or domain.endswith(".youtube.com")
            else ""
        )
        if not label:
            continue
        url = str(citation.get("url") or "")
        if url and not evidence[label]["url"]:
            evidence[label]["url"] = url
        try:
            frequency = max(1, int(float(citation.get("frequency") or 1)))
        except (TypeError, ValueError):
            frequency = 1
        evidence[label]["count"] += frequency
        if infer_citation_brand_context(
            "",
            url,
            domain,
            brand_tokens,
            competitor_tokens,
            str(citation.get("title") or ""),
        )["brand_cited"]:
            evidence[label]["brand_count"] += frequency

    for label, item in evidence.items():
        if not item["count"]:
            continue
        row_pattern = re.compile(
            rf"(<tr><td><strong>{label}</strong></td>)"
            r"(<td>.*?</td>)(<td>.*?</td>)(<td>.*?</td></tr>)",
            re.DOTALL | re.IGNORECASE,
        )
        match = row_pattern.search(html)
        if not match or "pill-green" in match.group(2):
            continue
        url = html_lib.escape(str(item["url"]), quote=True)
        link = (
            f' <a href="{url}" target="_blank" rel="noopener noreferrer">Open citation</a>'
            if url
            else ""
        )
        count = int(item["count"])
        brand_count = int(item["brand_count"])
        presence_cell = (
            '<td><span class="score-pill pill-green">Likely yes</span></td>'
            if brand_count
            else '<td><span class="score-pill pill-yellow">Citation signal</span></td>'
        )
        evidence_text = (
            f"{brand_count} brand-associated AI citation{'s' if brand_count != 1 else ''} found."
            if brand_count
            else f"{count} AI citation{'s' if count != 1 else ''} found; source-level brand mention not verified."
        )
        replacement = (
            match.group(1)
            + presence_cell
            + f"<td>{evidence_text}{link}</td>"
            + match.group(4)
        )
        html = html[:match.start()] + replacement + html[match.end():]
    return html


def load_audit_summary(audit_dir: Path) -> dict[str, Any]:
    from api.audit_json_cache import load_json_cached

    p = audit_dir / "audit_summary.json"
    if not p.is_file():
        raise FileNotFoundError(p)

    def _loader(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))

    data = load_json_cached(p, loader=_loader)
    if not isinstance(data, dict):
        raise FileNotFoundError(p)
    return data


def load_category_scores(audit_dir: Path) -> dict[str, float | None]:
    """
    Parse the three pillar scores (ai_visibility, technical_setup, content_structure)
    from report.html using data-score-key attributes.
    Falls back to None if report.html is absent or a score cannot be found.
    """
    rp = audit_dir / "report.html"
    if not rp.is_file():
        return {"ai_visibility": None, "technical_setup": None, "content_structure": None}
    raw = rp.read_text(encoding="utf-8", errors="replace")
    results: dict[str, float | None] = {}
    for key in ("ai_visibility", "technical_setup", "content_structure"):
        pattern = (
            r'data-score-key="' + re.escape(key) + r'"'
            r'.*?class="sov-head-score[^"]*">([\d.]+)<'
        )
        m = re.search(pattern, raw, re.DOTALL)
        if m:
            try:
                results[key] = round(float(m.group(1)), 1)
            except ValueError:
                results[key] = None
        else:
            results[key] = None
    return results


def load_category_score_details(audit_dir: Path) -> dict[str, Any]:
    """Return score-engine component scores and weights for calculation overlays."""
    summary_path = audit_dir / "audit_summary.json"
    if not summary_path.is_file():
        return {}
    try:
        audit = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
        cr = load_create_report()
        _, categories = cr.score_audit(audit)
    except Exception:
        return {}

    component_weights: dict[str, dict[str, float]] = {
        "ai_visibility": {
            "ai_citability": 30,
            "platform_readiness": 25,
            "ai_search_success": 15,
            "brand_entity_visibility": 15,
            "query_coverage_footprint": 15,
        },
        "technical_setup": {
            "indexability_crawl_health": 25,
            "ai_crawler_report": 25,
            "ssr_html_completeness": 20,
            "performance_page_experience": 15,
            "discovery_signals": 15,
        },
        "content_structure": {
            "eeat": 35,
            "original_information_gain": 20,
            "passage_answerability": 20,
            "schema_entity_markup": 15,
            "source_transparency_governance": 10,
        },
    }
    report_sections = {
        "ai_citability": "citability",
        "platform_readiness": "platform-readiness",
        "ai_search_success": "citability",
        "brand_entity_visibility": "brand-visibility-authority",
        "query_coverage_footprint": "citability",
        "indexability_crawl_health": "technical-overview",
        "ai_crawler_report": "crawler-access",
        "ssr_html_completeness": "technical-overview",
        "performance_page_experience": "technical-overview",
        "discovery_signals": "technical-overview",
        "eeat": "eeat-signals",
        "original_information_gain": "content-structure-answerability",
        "passage_answerability": "content-structure-answerability",
        "schema_entity_markup": "schema-entity-markup",
        "source_transparency_governance": "eeat-signals",
    }

    def successful_page(page: Any) -> bool:
        if not isinstance(page, dict):
            return False
        try:
            return int(page.get("http_status") or 0) == 200
        except (TypeError, ValueError):
            return False

    pages = [page for page in (audit.get("pages") or []) if successful_page(page)]

    def site_examples(component_key: str) -> list[dict[str, str]]:
        if component_key not in {"eeat", "original_information_gain", "passage_answerability"}:
            return []

        def rank(page: dict[str, Any]) -> float:
            signals = page.get("content_signals") if isinstance(page.get("content_signals"), dict) else {}
            editorial = 20.0 if signals.get("has_editorial_content") else 0.0
            sentences = min(20.0, float(signals.get("meaningful_sentence_n") or 0) * 2.0)
            explanatory = min(20.0, float(signals.get("explanatory_markers") or 0) * 3.0)
            questions = min(15.0, float(signals.get("question_like_n") or 0) * 4.0)
            schema = min(15.0, float(page.get("json_ld_blocks") or 0) * 3.0)
            entity = min(10.0, float(len(page.get("same_as") or [])) * 2.0)
            if component_key == "eeat":
                return editorial + sentences + schema + entity
            if component_key == "original_information_gain":
                return editorial + sentences + explanatory + 0.5 * schema
            return editorial + sentences + explanatory + questions

        contexts = {
            "eeat": "Sampled page contributing experience, expertise, authority or trust signals.",
            "original_information_gain": "Sampled page with the strongest proxy signals for distinctive explanatory content.",
            "passage_answerability": "Sampled passage with the strongest direct-answer and extractability signals.",
        }
        examples: list[dict[str, str]] = []
        for page in sorted(pages, key=rank, reverse=True)[:2]:
            signals = page.get("content_signals") if isinstance(page.get("content_signals"), dict) else {}
            title = str(page.get("page_title") or "").strip()
            excerpt = str(signals.get("representative_excerpt") or "").strip()
            if not title and not excerpt:
                continue
            examples.append({
                "url": str(page.get("final_url") or page.get("url") or "").strip(),
                "title": title or "Sampled site page",
                "excerpt": excerpt,
                "context": (
                    contexts[component_key]
                    if excerpt
                    else contexts[component_key]
                    + " This audit predates quoted-passage capture; rerun it to include the exact excerpt."
                ),
            })
        return examples

    details: dict[str, Any] = {}
    for category in categories:
        weights = component_weights.get(category.key, {})
        components = [
            {
                "key": sub.key,
                "title": sub.title,
                "score": round(float(sub.score), 1),
                "weight_pct": weights[sub.key],
                "detail": sub.detail,
                "finding_summary": (
                    sub.strengths[0]
                    if sub.strengths
                    else (sub.improvements[0] if sub.improvements else "No specific finding was recorded.")
                ),
                "evidence_example": (
                    sub.strengths[1]
                    if len(sub.strengths) > 1
                    else ""
                ),
                "strengths": [str(item) for item in sub.strengths],
                "improvements": [str(item) for item in sub.improvements],
                "report_section": report_sections.get(sub.key, ""),
                "site_examples": site_examples(sub.key),
                **(
                    {"criteria": list(audit.get("_ai_search_success_criteria") or [])}
                    if sub.key == "ai_search_success"
                    else {}
                ),
            }
            for sub in category.subs
            if sub.key in weights
        ]
        details[category.key] = {
            "score": round(float(category.score), 1),
            "components": components,
        }
    return details


def _stem_brand_label(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\.(com|co\.uk|co|org|net|io|uk|au|ca|de|fr|es|it)(\.[a-z]{2})?$", "", text)
    text = re.sub(r"^www\.", "", text)
    return re.sub(r"[\s\-_'.]+", "", text)


def _is_domain_string(value: str) -> bool:
    return bool(re.search(r"\.[a-z]{2,6}$", str(value or "").strip(), flags=re.IGNORECASE))


# Mirror web/src/lib/vendorDomains.ts — retailers are not SOV competitors.
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
_VENDOR_STEMS = frozenset(_stem_brand_label(domain) for domain in _VENDOR_DOMAINS)


def _is_vendor_domain(domain: str) -> bool:
    d = str(domain or "").lower().removeprefix("www.")
    if not d:
        return False
    if d in _VENDOR_DOMAINS:
        return True
    return any(d.endswith(f".{vendor}") for vendor in _VENDOR_DOMAINS)


def _is_vendor_brand(name: str) -> bool:
    """True when a competitor name/domain is a known retailer (UI isVendorBrand)."""
    raw = str(name or "").strip()
    if not raw:
        return False
    if _is_vendor_domain(raw):
        return True
    return _stem_brand_label(raw) in _VENDOR_STEMS


def _website_host(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if "://" not in raw:
            raw = f"https://{raw}"
        host = (urllib.parse.urlparse(raw).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _website_entity_key(value: str) -> str:
    parts = [part for part in _website_host(value).split(".") if part]
    if len(parts) < 2:
        return _stem_brand_label(parts[0] if parts else "")
    if (
        len(parts) >= 3
        and len(parts[-1]) == 2
        and parts[-2] in {"co", "com", "net", "org"}
    ):
        return _stem_brand_label(parts[-3])
    return _stem_brand_label(parts[-2])


def _domain_to_label(domain: str) -> str:
    text = str(domain or "").strip()
    text = re.sub(r"^www\.", "", text, flags=re.IGNORECASE)
    return re.sub(
        r"\.(com|co\.uk|co|org|net|io|uk|au|ca|de|fr|es|it)(\.[a-z]{2})?$",
        "",
        text,
        flags=re.IGNORECASE,
    )


def _load_website_backed_competitor_entities(
    audit_dir: Path | None,
    live: dict[str, Any],
    *,
    competitors: list[dict[str, Any]] | None = None,
    brand_site_url: str = "",
) -> dict[str, dict[str, Any]]:
    """
    Website-backed competitor entities keyed by entity key.

    Mirrors web/src/lib/brandVisibilityRows.ts buildCompetitorEntities —
    onboarding / tracked competitors, reply-detected brands with websites,
    and domain-form keys from competitor_detail. Vendors and the own brand
    are excluded.
    """
    brand_site = str(brand_site_url or live.get("brand_site_url") or "").strip()
    own_key = _website_entity_key(brand_site) if brand_site else ""
    entities: dict[str, dict[str, Any]] = {}

    def add_entity(name: str, website: str, priority: int) -> None:
        host = _website_host(website)
        key = _website_entity_key(website)
        clean_name = str(name or "").strip()
        if (
            not host
            or not key
            or key == own_key
            or _is_vendor_brand(host)
            or _is_vendor_brand(clean_name)
        ):
            return
        existing = entities.get(key)
        aliases = set(existing["aliases"]) if existing else set()
        for alias in (clean_name, host, f"www.{host}", _domain_to_label(host)):
            stem = _stem_brand_label(alias)
            if stem:
                aliases.add(stem)
        display = clean_name or _domain_to_label(host) or host.split(".")[0]
        website_url = website if "://" in website else f"https://{host}"
        if not existing or priority > int(existing["priority"]):
            entities[key] = {
                "key": key,
                "name": display,
                "website": website_url,
                "priority": priority,
                "aliases": aliases,
            }
        else:
            existing["aliases"] = aliases

    if competitors:
        for row in competitors:
            if not isinstance(row, dict):
                continue
            add_entity(
                str(row.get("competitor_brand") or ""),
                str(row.get("competitor_website") or ""),
                3,
            )

    if audit_dir is not None:
        ob_path = audit_dir / "onboarding_context.json"
        if ob_path.is_file():
            try:
                onboarding = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                onboarding = {}
            if isinstance(onboarding, dict):
                if not brand_site:
                    brand_site = str(
                        onboarding.get("brand_website_used")
                        or onboarding.get("brand_url")
                        or onboarding.get("brand_site_url")
                        or ""
                    ).strip()
                    own_key = _website_entity_key(brand_site) if brand_site else ""
                if not competitors:
                    for row in onboarding.get("competitors_detail") or onboarding.get("competitor_context") or []:
                        if not isinstance(row, dict):
                            continue
                        add_entity(
                            str(row.get("competitor_brand") or ""),
                            str(row.get("competitor_website") or ""),
                            3,
                        )
        comp_path = audit_dir / "competitors.json"
        if comp_path.is_file() and not competitors:
            try:
                comp_payload = json.loads(comp_path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError):
                comp_payload = {}
            if isinstance(comp_payload, dict):
                for row in comp_payload.get("competitors") or []:
                    if not isinstance(row, dict):
                        continue
                    add_entity(
                        str(row.get("competitor_brand") or ""),
                        str(row.get("competitor_website") or ""),
                        3,
                    )

    for row in live.get("reply_detected_brands") or []:
        if not isinstance(row, dict):
            continue
        website = str(row.get("website_url") or "").strip()
        if website:
            add_entity(str(row.get("brand_name") or ""), website, 2)

    raw_names: set[str] = set()
    for row in live.get("per_prompt") or []:
        if not isinstance(row, dict):
            continue
        for platform in ("gemini", "openai", "google_aio", "claude"):
            runs = (row.get("runs") or {}).get(platform) or []
            completed = [
                run for run in runs
                if isinstance(run, dict) and run.get("response") and not run.get("error")
            ]
            response_rows = completed or [{
                "mention_scores": row.get(f"mention_scores_{platform}") or {},
            }]
            for run in response_rows:
                scores = run.get("mention_scores") if isinstance(run, dict) else {}
                detail = scores.get("competitor_detail") if isinstance(scores, dict) else {}
                if isinstance(detail, dict):
                    raw_names.update(str(name) for name in detail.keys())

    for raw in raw_names:
        if _is_domain_string(raw):
            add_entity(_domain_to_label(raw) or raw, raw, 1)

    # Prefer human brand labels when a domain-only entity later sees a text key.
    def entity_for(name: str) -> dict[str, Any] | None:
        stem = _stem_brand_label(name)
        for entity in entities.values():
            if entity["key"] == stem or stem in entity["aliases"]:
                return entity
        return None

    for raw in raw_names:
        if _is_domain_string(raw):
            continue
        entity = entity_for(raw)
        if entity and int(entity["priority"]) == 1 and _stem_brand_label(raw) == entity["key"]:
            entity["name"] = raw
            entity["aliases"].add(_stem_brand_label(raw))

    return entities


def _alias_map_from_entities(entities: dict[str, dict[str, Any]]) -> dict[str, str]:
    alias_to_key: dict[str, str] = {}
    for entity in entities.values():
        alias_to_key[str(entity["key"])] = str(entity["key"])
        for alias in entity["aliases"]:
            alias_to_key[str(alias)] = str(entity["key"])
        alias_to_key[_stem_brand_label(entity["name"])] = str(entity["key"])
    return alias_to_key


def _load_website_backed_competitor_aliases(
    audit_dir: Path,
    live: dict[str, Any],
    *,
    competitors: list[dict[str, Any]] | None = None,
    brand_site_url: str = "",
) -> dict[str, str]:
    """
    Map competitor detail keys / aliases → canonical entity key.

    Mirrors web/src/lib/brandVisibilityRows.ts so backend sov_pct matches the
    Brand & competitor visibility table (website-backed competitors only).
    """
    entities = _load_website_backed_competitor_entities(
        audit_dir,
        live,
        competitors=competitors,
        brand_site_url=brand_site_url,
    )
    return _alias_map_from_entities(entities)


def _accept_website_backed_competitor(
    name: str,
    alias_to_key: dict[str, str],
) -> str | None:
    """Return canonical entity key only when the name maps to a website-backed entity."""
    normalized = str(name or "").strip().lower()
    if not normalized:
        return None
    stem = _stem_brand_label(normalized)
    if normalized in alias_to_key:
        return alias_to_key[normalized]
    if stem and stem in alias_to_key:
        return alias_to_key[stem]
    # No domain bypass — vendors / own-brand domains rejected during entity build
    # must stay excluded (matches UI isValidCompetitor / entityForName).
    return None


def accumulate_brand_visibility_rows(
    ctx: dict[str, Any],
    audit_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """
    Brand + competitor Visibility % / SOV % rows.

    Mirrors web/src/lib/brandVisibilityRows.ts accumulateBrandVisibilityHits
    (website-backed competitors only, vendors excluded, raw signal-hit SOV).
    """
    from backend.prompt_suggest import text_mentions_brand

    live = ctx.get("live_probe") if isinstance(ctx.get("live_probe"), dict) else {}
    per_prompt = [p for p in (live.get("per_prompt") or []) if isinstance(p, dict)]
    if not per_prompt:
        return []

    brand = str(ctx.get("brand_name") or live.get("brand_name") or "Brand")
    brand_site = str(ctx.get("brand_site_url") or live.get("brand_site_url") or "")
    tokens = [str(t) for t in (live.get("brand_match_tokens") or []) if str(t).strip()]
    competitors = [
        row for row in (ctx.get("competitors") or [])
        if isinstance(row, dict)
    ]
    platforms = ("gemini", "openai", "google_aio", "claude")
    entities = _load_website_backed_competitor_entities(
        audit_dir,
        live,
        competitors=competitors or None,
        brand_site_url=brand_site,
    )
    alias_to_key = _alias_map_from_entities(entities)

    total_responses = 0
    mention_counts: dict[str, int] = {brand: 0}
    signal_hits: dict[str, float] = {brand: 0.0}
    key_hits: dict[str, float] = {}
    key_mentions: dict[str, int] = {}

    for row in per_prompt:
        for pk in platforms:
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
                if not resp or err:
                    continue
                completed = [{
                    "response": resp,
                    "mention_scores": row.get(f"mention_scores_{pk}") or {},
                }]
            for run in completed:
                resp = str(run.get("response") or "")
                if not resp:
                    continue
                total_responses += 1
                scores = run.get("mention_scores") if isinstance(run.get("mention_scores"), dict) else {}
                stored = float(scores.get("brand_signal") or 0)
                text_visible = text_mentions_brand(resp, brand, tokens)
                brand_sig = stored if stored > 0 else (1.0 if text_visible else 0.0)
                if brand_sig > 0:
                    mention_counts[brand] = mention_counts.get(brand, 0) + 1
                signal_hits[brand] = signal_hits.get(brand, 0.0) + brand_sig
                detail = scores.get("competitor_detail") if isinstance(scores.get("competitor_detail"), dict) else {}
                seen_keys: set[str] = set()
                for name, hits in detail.items():
                    val = float(hits or 0)
                    key = _accept_website_backed_competitor(str(name), alias_to_key)
                    if not key:
                        continue
                    key_hits[key] = key_hits.get(key, 0.0) + val
                    if val > 0:
                        seen_keys.add(key)
                for key in seen_keys:
                    key_mentions[key] = key_mentions.get(key, 0) + 1

    total_hits = signal_hits.get(brand, 0.0) + sum(key_hits.values())
    rows: list[dict[str, Any]] = [{
        "name": brand,
        "is_own": True,
        "visibility": (100.0 * mention_counts.get(brand, 0) / total_responses) if total_responses else 0.0,
        "sov": (100.0 * signal_hits.get(brand, 0.0) / total_hits) if total_hits else 0.0,
        "mentions": mention_counts.get(brand, 0),
        "total_responses": total_responses,
        "hits": signal_hits.get(brand, 0.0),
        "website": brand_site,
    }]
    for key, entity in entities.items():
        hits = key_hits.get(key, 0.0)
        mentions = key_mentions.get(key, 0)
        rows.append({
            "name": str(entity.get("name") or key),
            "is_own": False,
            "visibility": (100.0 * mentions / total_responses) if total_responses else 0.0,
            "sov": (100.0 * hits / total_hits) if total_hits else 0.0,
            "mentions": mentions,
            "total_responses": total_responses,
            "hits": hits,
            "website": str(entity.get("website") or ""),
            "entity_key": key,
        })
    # Match BrandCompetitorVisibility topComparisonRows: visibility (mentions) desc, name asc.
    rows.sort(key=lambda r: (-int(r["mentions"]), str(r["name"]).lower()))
    return rows


def load_prompt_visibility_metrics(
    audit_dir: Path,
    *,
    prefer_persisted: bool = True,
) -> dict[str, Any] | None:
    """Canonical probe-driven AI Visibility metrics used across the product."""
    from api.audit_json_cache import load_json_cached

    # Prefer fresh persisted metrics when available (avoids re-parsing multi-MB probe JSON).
    # Do not lazy-backfill here — backfill belongs to prompt-performance GET so SOV
    # stays on the canonical probe-parsing path until metrics are written at probe time.
    if prefer_persisted:
        try:
            from api.prompt_performance_metrics import metrics_are_fresh, read_metrics_file

            metrics_doc = read_metrics_file(audit_dir)
            if metrics_are_fresh(audit_dir, metrics_doc) and isinstance(metrics_doc, dict):
                overall = metrics_doc.get("overall_metrics")
                if isinstance(overall, dict) and overall:
                    return overall
                locales = metrics_doc.get("locales") if isinstance(metrics_doc.get("locales"), dict) else {}
                default_key = str(metrics_doc.get("default_locale_key") or "")
                entry = locales.get(default_key) if default_key else None
                if isinstance(entry, dict) and isinstance(entry.get("metrics"), dict) and entry["metrics"]:
                    return entry["metrics"]
        except Exception:
            pass

    sov_competitor_limit = 10
    probe_path = audit_dir / "prompt_performance_live_probe.json"
    if not probe_path.is_file():
        return None
    raw = load_json_cached(probe_path)
    if not isinstance(raw, dict):
        return None
    live = raw.get("live_probe", raw) if isinstance(raw, dict) else {}
    if not isinstance(live, dict):
        return None
    rows = live.get("per_prompt")
    if not isinstance(rows, list) or not rows:
        return None

    platforms = ("gemini", "openai", "google_aio", "claude")
    brand_tokens = [
        str(token).lower()
        for token in [live.get("brand_name"), *(live.get("brand_match_tokens") or [])]
        if str(token or "").strip()
    ]
    alias_to_key = _load_website_backed_competitor_aliases(audit_dir, live)
    visible_responses = 0
    response_count = 0
    brand_hits = 0.0
    competitor_hits_by_name: dict[str, float] = {}
    per_platform_acc: dict[str, dict[str, Any]] = {
        platform: {
            "response_count": 0,
            "visible_response_count": 0,
            "brand_hits": 0.0,
            "competitor_hits_by_name": {},
        }
        for platform in platforms
    }

    for row in rows:
        if not isinstance(row, dict):
            continue
        for platform in platforms:
            raw_runs = (row.get("runs") or {}).get(platform) or []
            completed_runs = [
                run for run in raw_runs
                if isinstance(run, dict) and run.get("response") and not run.get("error")
            ]
            response_rows = completed_runs or [{
                "response": row.get(f"{platform}_response"),
                "mention_scores": row.get(f"mention_scores_{platform}") or {},
            }]
            plat_acc = per_platform_acc[platform]
            for run in response_rows:
                response = str(run.get("response") or "").lower()
                if not response:
                    continue
                scores = run.get("mention_scores") or {}
                stored_signal = float(scores.get("brand_signal") or 0) if isinstance(scores, dict) else 0.0
                visible = stored_signal > 0 or any(token in response for token in brand_tokens)
                response_count += 1
                plat_acc["response_count"] += 1
                if visible:
                    visible_responses += 1
                    plat_acc["visible_response_count"] += 1
                signal = stored_signal if stored_signal > 0 else (1.0 if visible else 0.0)
                brand_hits += signal
                plat_acc["brand_hits"] += signal
                detail = scores.get("competitor_detail") or {} if isinstance(scores, dict) else {}
                if isinstance(detail, dict):
                    for name, value in detail.items():
                        canonical = _accept_website_backed_competitor(str(name), alias_to_key)
                        if not canonical:
                            continue
                        competitor_hits_by_name[canonical] = (
                            competitor_hits_by_name.get(canonical, 0.0) + float(value or 0)
                        )
                        plat_comp = plat_acc["competitor_hits_by_name"]
                        plat_comp[canonical] = plat_comp.get(canonical, 0.0) + float(value or 0)
    ranked_competitors = sorted(
        (
            (name, value)
            for name, value in competitor_hits_by_name.items()
            if value > 0
        ),
        key=lambda item: (-item[1], item[0]),
    )
    # Relative SOV performance uses the top-N website-backed competitors.
    top_competitors = ranked_competitors[:sov_competitor_limit]
    positive_competitor_hits = [value for _, value in top_competitors]
    competitor_hits = sum(positive_competitor_hits)
    # Raw SOV matches the brand visibility table: all website-backed competitors.
    all_competitor_hits = sum(value for _, value in ranked_competitors)
    visibility_pct = 100.0 * visible_responses / response_count if response_count else 0.0
    total_hits_raw = brand_hits + all_competitor_hits
    total_hits_rel = brand_hits + competitor_hits
    sov_pct = 100.0 * brand_hits / total_hits_raw if total_hits_raw else 0.0
    if brand_hits <= 0:
        sov_performance_score = 0.0
        sov_rank = len(positive_competitor_hits) + 1 if positive_competitor_hits else None
    elif not positive_competitor_hits:
        sov_performance_score = 100.0
        sov_rank = 1
    else:
        below = sum(1 for value in positive_competitor_hits if value < brand_hits)
        tied = sum(1 for value in positive_competitor_hits if abs(value - brand_hits) <= 1e-9)
        ahead = sum(1 for value in positive_competitor_hits if value > brand_hits)
        sov_performance_score = 100.0 * (below + 0.5 * tied) / len(positive_competitor_hits)
        sov_rank = ahead + 1
    top_competitor_sov = (
        100.0 * max(positive_competitor_hits) / total_hits_rel
        if positive_competitor_hits and total_hits_rel else 0.0
    )
    average_competitor_sov = (
        100.0 * (sum(positive_competitor_hits) / len(positive_competitor_hits)) / total_hits_rel
        if positive_competitor_hits and total_hits_rel else 0.0
    )
    score = min(100.0, 0.60 * visibility_pct + 0.40 * sov_performance_score)
    per_platform: dict[str, dict[str, Any]] = {}
    for platform, plat_acc in per_platform_acc.items():
        plat_resp = int(plat_acc["response_count"])
        plat_vis = int(plat_acc["visible_response_count"])
        plat_brand = float(plat_acc["brand_hits"])
        # Per-platform raw SOV uses all website-backed competitors on that platform.
        plat_comp_hits = sum(
            float(value)
            for value in plat_acc["competitor_hits_by_name"].values()
            if float(value) > 0
        )
        plat_total = plat_brand + plat_comp_hits
        per_platform[platform] = {
            "response_count": plat_resp,
            "visible_response_count": plat_vis,
            "visibility_pct": round(100.0 * plat_vis / plat_resp, 1) if plat_resp else 0.0,
            "brand_hits": round(plat_brand, 1),
            "competitor_hits": round(plat_comp_hits, 1),
            "sov_pct": round(100.0 * plat_brand / plat_total, 1) if plat_total else 0.0,
        }
    return {
        "score": round(score, 1),
        "visibility_pct": round(visibility_pct, 1),
        "sov_pct": round(sov_pct, 1),
        "sov_performance_score": round(sov_performance_score, 1),
        "sov_rank": sov_rank,
        "competitor_count": len(positive_competitor_hits),
        "detected_competitor_count": len(ranked_competitors),
        "sov_competitor_limit": sov_competitor_limit,
        "top_competitor_sov_pct": round(top_competitor_sov, 1),
        "average_competitor_sov_pct": round(average_competitor_sov, 1),
        "visible_prompt_count": visible_responses,
        "prompt_count": response_count,
        "visible_response_count": visible_responses,
        "response_count": response_count,
        "tested_prompt_count": len(rows),
        "brand_hits": round(brand_hits, 1),
        "competitor_hits": round(competitor_hits, 1),
        "per_platform": per_platform,
    }


def load_technical_display_data(audit_dir: Path) -> dict[str, Any]:
    """Return the legacy platform-readiness scores and crawler matrix as data."""
    try:
        audit = load_audit_summary(audit_dir)
        cr = load_create_report()
        overall, categories = cr.score_audit(audit)
        robots_text = cr._robots_text_for_ai(audit)
        crawler_score, strengths, improvements, _ = cr.score_ai_crawler_robots(
            robots_text,
            homepage_url=cr._homepage_from_audit(audit),
            audit=audit,
        )
        platform_rows = cr._platform_readiness_scores(
            overall=overall,
            agents=categories,
            ai_crawler_score=crawler_score,
            audit=audit,
        )
    except Exception:
        return {"platform_readiness": [], "crawler_access": {"rows": []}}

    crawler_rows: list[dict[str, Any]] = []
    if robots_text and str(robots_text).strip():
        home, robots_url = cr._robots_home_and_fetch_url(cr._homepage_from_audit(audit))
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.parse(str(robots_text).splitlines())
            for spec in cr.AI_CRAWLER_SPECS:
                can_fetch = bool(parser.can_fetch(spec.token, home))
                aligned = (
                    None
                    if spec.policy == "context"
                    else (spec.policy == "allow" and can_fetch)
                    or (spec.policy == "block" and not can_fetch)
                )
                crawler_rows.append({
                    "crawler": spec.token,
                    "tier": spec.tier,
                    "recommendation": spec.rec_label,
                    "reason": spec.reason,
                    "can_fetch": can_fetch,
                    "aligned": aligned,
                })
        except Exception:
            crawler_rows = []

    return {
        "platform_readiness": [
            {
                "key": str(row.get("key") or ""),
                "name": str(row.get("name") or ""),
                "score": round(float(row.get("score") or 0), 1),
                "gap": str(row.get("gap") or ""),
            }
            for row in platform_rows
        ],
        "crawler_access": {
            "score": round(float(crawler_score), 1),
            "strengths": [str(item) for item in strengths],
            "improvements": [str(item) for item in improvements],
            "rows": crawler_rows,
        },
    }


def load_content_quality_details(audit_dir: Path) -> dict[str, Any]:
    """Build native Content Quality section data from stored crawl artifacts.

    When ``content_quality_gemini.json`` is present, overlays Gemini E-E-A-T /
    answerability scores and evidence (schema / formatting / brand stay crawl).
    """
    try:
        audit = load_audit_summary(audit_dir)
        cr = load_create_report()
        crawl = load_crawl_site()
        _, categories = cr.score_audit(audit)
    except Exception:
        return {}

    pages = [
        page for page in (audit.get("pages") or [])
        if isinstance(page, dict) and int(page.get("http_status") or 0) == 200
    ]
    eeat = cr._eeat_breakdown(audit=audit, agents=categories, ai_crawler_score=0)

    def page_identity(page: dict[str, Any]) -> dict[str, str]:
        return {
            "url": str(page.get("final_url") or page.get("url") or "").strip(),
            "title": str(page.get("page_title") or page.get("final_url") or page.get("url") or "Sampled page").strip(),
        }

    def bounded_examples(values: list[dict[str, str]], limit: int = 3) -> list[dict[str, str]]:
        seen: set[tuple[str, str]] = set()
        output: list[dict[str, str]] = []
        for item in values:
            key = (item.get("url", ""), item.get("snippet", ""))
            if not item.get("snippet") or key in seen:
                continue
            seen.add(key)
            output.append(item)
            if len(output) >= limit:
                break
        return output

    original_examples: list[dict[str, str]] = []
    passage_examples: list[dict[str, str]] = []
    formatting_examples: list[dict[str, str]] = []
    formatting_scores: list[float] = []
    originality_scores: list[float] = []
    original_pages = 0
    for page in pages:
        identity = page_identity(page)
        signals = page.get("content_signals") if isinstance(page.get("content_signals"), dict) else {}
        eeat_evidence = signals.get("eeat_evidence") if isinstance(signals.get("eeat_evidence"), dict) else {}
        if "originality_evidence" in signals:
            original_snippets = list(signals.get("originality_evidence") or [])
            originality_types = list(signals.get("originality_signal_types") or [])
        else:
            # Older audits did not store originality separately. Reclassify
            # bounded legacy snippets using the dedicated originality patterns;
            # generic Experience evidence is not accepted automatically.
            legacy_snippets = [
                str(snippet)
                for criterion_examples in eeat_evidence.values()
                if isinstance(criterion_examples, list)
                for snippet in criterion_examples
                if str(snippet).strip()
            ]
            classified = crawl.extract_originality_evidence(" ".join(legacy_snippets))
            original_snippets = classified["evidence"]
            originality_types = classified["signal_types"]
        if original_snippets:
            original_pages += 1
        originality_scores.append(
            cr._clamp100(
                55.0
                + 15.0 * max(0, len(set(originality_types)) - 1)
                + 10.0 * max(0, len(original_snippets) - 1)
            )
            if original_snippets else 0.0
        )
        for snippet in original_snippets:
            original_examples.append({**identity, "snippet": str(snippet)[:420]})

        representative = str(signals.get("representative_excerpt") or "").strip()
        if not representative:
            representative = next(
                (
                    str(snippet).strip()
                    for criterion_examples in eeat_evidence.values()
                    if isinstance(criterion_examples, list)
                    for snippet in criterion_examples
                    if str(snippet).strip()
                ),
                "",
            )
        if representative:
            passage_examples.append({**identity, "snippet": representative[:420]})
        headings = int(signals.get("heading_h2_h3_n") or 0)
        direct = int(signals.get("direct_answer_n") or signals.get("explanatory_markers") or 0)
        faqs = int(signals.get("faq_question_n") or signals.get("question_like_n") or 0)
        editorial = bool(signals.get("has_editorial_content"))
        formatting_snippets = list(signals.get("formatting_evidence") or [])
        if not formatting_snippets and representative:
            # Compatibility for audits created before formatting-specific
            # evidence was persisted by the crawler.
            formatting_snippets = [f"Structured passage: {representative}"]
        for snippet in formatting_snippets:
            formatting_examples.append({**identity, "snippet": str(snippet)[:420]})
        formatting_scores.append(cr._clamp100(
            35.0 * min(1.0, headings / 3.0)
            + 30.0 * min(1.0, direct / 2.0)
            + 20.0 * min(1.0, faqs / 2.0)
            + 15.0 * float(editorial)
        ))

    original_score = (
        round(sum(originality_scores) / len(pages), 1) if pages else 0.0
    )
    passage_score = (
        round(sum(cr._page_passage_citability_score(page) for page in pages) / len(pages), 1)
        if pages else 0.0
    )
    formatting_score = (
        round(sum(formatting_scores) / len(formatting_scores), 1)
        if formatting_scores else 0.0
    )

    structured_score, schema_strengths, schema_improvements = cr._subscore_structured(audit)
    entity_score, entity_strengths, entity_improvements = cr._subscore_entity(audit)
    schema_score = cr._clamp100(0.75 * structured_score + 0.25 * entity_score)
    schema_evidence: list[dict[str, Any]] = []
    for page in pages:
        if not page.get("has_json_ld"):
            continue
        schema_evidence.append({
            **page_identity(page),
            "types": [str(value) for value in (page.get("json_ld_types") or [])],
            "blocks": int(page.get("json_ld_blocks") or 0),
            "same_as_count": len(page.get("same_as") or []),
        })

    brand_visibility = audit.get("brand_visibility") if isinstance(audit.get("brand_visibility"), dict) else {}
    brand_rows = [dict(row) for row in (brand_visibility.get("platforms") or []) if isinstance(row, dict)]

    # A Reddit citation is itself evidence that the brand/query surfaced on
    # Reddit. Presence does not require proving that the cited post is official.
    reddit_citation_url = ""
    live_path = audit_dir / "prompt_performance_live_probe.json"
    if live_path.is_file():
        try:
            raw_probe = json.loads(live_path.read_text(encoding="utf-8", errors="replace"))
            live = raw_probe.get("live_probe", raw_probe)
            for prompt_row in live.get("per_prompt") or []:
                if not isinstance(prompt_row, dict):
                    continue
                for platform in ("gemini", "openai", "google_aio", "claude"):
                    for citation in prompt_row.get(f"citations_{platform}") or []:
                        if not isinstance(citation, dict):
                            continue
                        domain = str(citation.get("domain") or "").lower().removeprefix("www.")
                        if domain == "redd.it" or domain == "reddit.com" or domain.endswith(".reddit.com"):
                            reddit_citation_url = str(citation.get("url") or "")
                            break
                    if reddit_citation_url:
                        break
                if reddit_citation_url:
                    break
            if not reddit_citation_url:
                aggregate_urls = list(live.get("top_cited_urls") or [])
                aggregate = live.get("aggregate")
                if isinstance(aggregate, dict):
                    aggregate_urls.extend(aggregate.get("top_cited_urls") or [])
                for citation in aggregate_urls:
                    if not isinstance(citation, dict):
                        continue
                    domain = str(citation.get("domain") or "").lower().removeprefix("www.")
                    if domain == "redd.it" or domain == "reddit.com" or domain.endswith(".reddit.com"):
                        reddit_citation_url = str(citation.get("url") or "")
                        break
        except (OSError, json.JSONDecodeError):
            pass

    reddit_row = next(
        (row for row in brand_rows if str(row.get("platform") or "").strip().lower() == "reddit"),
        None,
    )
    if reddit_citation_url:
        if reddit_row is None:
            reddit_row = {"platform": "Reddit"}
            brand_rows.append(reddit_row)
        reddit_row["present"] = True
        reddit_row["status"] = "Likely yes — Reddit citation found in an AI response."
        reddit_row["url"] = reddit_row.get("url") or reddit_citation_url
        reddit_row["citation_confirmed"] = True

    audit_for_brand_score = {
        **audit,
        "brand_visibility": {**brand_visibility, "platforms": brand_rows},
    }
    brand_score, brand_strengths, brand_improvements = cr.score_brand_visibility(audit_for_brand_score)
    eeat_score = (
        sum(float(item.get("score") or 0) for item in eeat) / len(eeat)
        if eeat else 0.0
    )
    content_components = [
        {
            "key": "eeat",
            "title": "E-E-A-T Signals",
            "score": round(eeat_score, 1),
            "weight_pct": 35,
            "detail": "Direct site-content evidence for Experience, Expertise, Authoritativeness and Trust.",
            "finding_summary": f"E-E-A-T evidence averages {format_report_score(eeat_score)}/100 across the four criteria.",
            "evidence_example": next(
                (
                    str(example.get("snippet") or "")
                    for item in eeat
                    for example in (item.get("evidence") or [])
                    if str(example.get("snippet") or "").strip()
                ),
                next((str(item.get("evidence_note") or "") for item in eeat), ""),
            ),
            "report_section": "eeat-signals",
            "site_examples": [
                {
                    "url": str(example.get("url") or ""),
                    "title": str(example.get("title") or "Sampled site page"),
                    "excerpt": str(example.get("snippet") or ""),
                    "context": f"Matching {item.get('name', 'E-E-A-T')} evidence from this site.",
                }
                for item in eeat
                for example in (item.get("evidence") or [])
            ][:3],
        },
        {
            "key": "original_information_gain",
            "title": "Original information gain",
            "score": original_score,
            "weight_pct": 15,
            "detail": "Novel first-party research, quantified findings, datasets, benchmarks, or proprietary frameworks.",
            "finding_summary": (
                f"Dedicated originality evidence was found on {original_pages} of {len(pages)} sampled pages."
            ),
            "evidence_example": (
                original_examples[0]["snippet"]
                if original_examples
                else "No dedicated evidence of original data, findings, analysis, benchmarks, or proprietary frameworks was found."
            ),
            "report_section": "content-structure-answerability",
            "site_examples": [
                {**item, "excerpt": item["snippet"], "context": "Evidence of a distinct original contribution."}
                for item in bounded_examples(original_examples)
            ],
        },
        {
            "key": "passage_answerability",
            "title": "Passage-level answerability",
            "score": passage_score,
            "weight_pct": 15,
            "detail": "Self-contained explanatory passages that can answer a question when quoted independently.",
            "finding_summary": f"Answerability across sampled pages scores {format_report_score(passage_score)}/100.",
            "evidence_example": (
                passage_examples[0]["snippet"]
                if passage_examples
                else "No sufficiently complete answer passage was captured in the sampled pages."
            ),
            "report_section": "content-structure-answerability",
            "site_examples": [
                {**item, "excerpt": item["snippet"], "context": "Representative answer-ready passage."}
                for item in bounded_examples(passage_examples)
            ],
        },
        {
            "key": "content_formatting",
            "title": "Content formatting",
            "score": formatting_score,
            "weight_pct": 10,
            "detail": "Clear headings, direct-answer prose, FAQs and structured sections that AI can extract.",
            "finding_summary": f"Content formatting across sampled pages scores {format_report_score(formatting_score)}/100.",
            "evidence_example": (
                formatting_examples[0]["snippet"]
                if formatting_examples
                else "No matching heading, FAQ or direct-answer examples were captured."
            ),
            "report_section": "content-structure-answerability",
            "site_examples": [
                {**item, "excerpt": item["snippet"], "context": "Formatting or direct-answer evidence."}
                for item in bounded_examples(formatting_examples)
            ],
        },
        {
            "key": "schema_entity_markup",
            "title": "Schema & entity markup",
            "score": round(schema_score, 1),
            "weight_pct": 15,
            "detail": "JSON-LD coverage, schema depth and entity links that help AI understand the site.",
            "finding_summary": (
                schema_strengths[0]
                if schema_strengths
                else (schema_improvements[0] if schema_improvements else "No schema finding was recorded.")
            ),
            "evidence_example": (
                f"{len(schema_evidence)} sampled page(s) contain JSON-LD."
                if schema_evidence
                else "No JSON-LD was found on sampled pages."
            ),
            "report_section": "schema-entity-markup",
            "site_examples": [
                {
                    "url": item["url"],
                    "title": item["title"],
                    "excerpt": (
                        f"Detected schema types: {', '.join(item['types']) or 'unclassified JSON-LD'}; "
                        f"{item['blocks']} block(s), {item['same_as_count']} sameAs link(s)."
                    ),
                    "context": "Structured-data evidence from this page.",
                }
                for item in schema_evidence[:3]
            ],
        },
        {
            "key": "brand_visibility_authority",
            "title": "Brand Visibility & Authority",
            "score": round(float(brand_score), 1),
            "weight_pct": 10,
            "detail": "Owned entity clarity and third-party presence across major corroboration platforms.",
            "finding_summary": (
                brand_strengths[0]
                if brand_strengths
                else (brand_improvements[0] if brand_improvements else "No brand-authority finding was recorded.")
            ),
            "evidence_example": "; ".join(
                f"{row.get('platform', 'Platform')}: {'Likely yes' if row.get('present') else 'No / unclear'}"
                for row in brand_rows
            ),
            "report_section": "brand-visibility-authority",
            "site_examples": [],
        },
    ]
    content_score = sum(
        float(component["score"]) * float(component["weight_pct"]) / 100.0
        for component in content_components
    )
    cap_notes = [str(note) for note in (audit.get("_overall_score_cap_notes") or [])]
    cap_values = [
        float(match.group(1))
        for note in cap_notes
        if (match := re.search(r"Score cap:\s*(\d+(?:\.\d+)?)", note))
    ]

    details = {
        "score": round(content_score, 1),
        "components": content_components,
        "overall_cap": min(cap_values) if cap_values else None,
        "overall_cap_notes": cap_notes,
        "eeat": eeat,
        "structure_answerability": [
            {
                "key": "original_information_gain",
                "title": "Original information gain",
                "score": original_score,
                "description": "Novel first-party research, quantified findings, datasets, benchmarks, analysis or proprietary frameworks.",
                "examples": bounded_examples(original_examples),
                "empty_message": "No dedicated evidence of an original contribution was found in the sampled pages.",
            },
            {
                "key": "passage_answerability",
                "title": "Passage-level answerability",
                "score": passage_score,
                "description": "Self-contained explanatory passages that can answer a question when quoted independently.",
                "examples": bounded_examples(passage_examples),
                "empty_message": "No sufficiently complete answer passage was captured in the sampled pages.",
            },
            {
                "key": "content_formatting",
                "title": "Content formatting",
                "score": formatting_score,
                "description": "Clear headings, direct-answer prose, FAQs and structured sections that AI can extract.",
                "examples": bounded_examples(formatting_examples),
                "empty_message": "No matching heading, FAQ or direct-answer examples were captured in the sampled pages.",
            },
        ],
        "schema_entity": {
            "score": round(schema_score, 1),
            "summary": (
                "JSON-LD is machine-readable Schema.org markup embedded in a page. It identifies entities such as "
                "the organisation, website, authors, products and FAQs, and links them to verified profiles."
            ),
            "strengths": [str(value) for value in cr._unique_preserve(schema_strengths + entity_strengths)],
            "improvements": [str(value) for value in cr._unique_preserve(schema_improvements + entity_improvements)],
            "evidence": schema_evidence[:10],
        },
        "brand_visibility_authority": {
            "score": round(float(brand_score), 1),
            "brand_query": str(brand_visibility.get("brand_query") or ""),
            "method_note": str(brand_visibility.get("method_note") or ""),
            "strengths": [str(value) for value in brand_strengths],
            "improvements": [str(value) for value in brand_improvements],
            "rows": brand_rows,
        },
    }
    try:
        from content_quality_llm import (
            load_cached_content_quality_gemini,
            merge_gemini_into_content_quality,
        )

        gemini = load_cached_content_quality_gemini(audit_dir)
        return merge_gemini_into_content_quality(details, gemini)
    except Exception:
        details["gemini_overlay"] = {"available": False, "status": "error"}
        return details


def load_integrated_scores(audit_dir: Path) -> dict[str, Any]:
    """Return the single score model used by header, Summary, archive, and LLM copy."""
    details = load_category_score_details(audit_dir)
    prompt_metrics = load_prompt_visibility_metrics(audit_dir)
    technical_display = load_technical_display_data(audit_dir)
    content_quality_details = load_content_quality_details(audit_dir)
    readiness_detail = details.get("ai_visibility") or {}
    foundation_detail = details.get("technical_setup") or {}
    readiness_components = {
        component.get("key"): dict(component)
        for component in readiness_detail.get("components", [])
        if isinstance(component, dict) and component.get("key")
    }
    foundation_components = {
        component.get("key"): dict(component)
        for component in foundation_detail.get("components", [])
        if isinstance(component, dict) and component.get("key")
    }
    technical_component_weights = {
        "ai_crawler_report": 25.0,
        "ai_citability": 25.0,
        "ai_search_success": 12.5,
        "query_coverage_footprint": 12.5,
        "platform_readiness": 25.0,
    }
    technical_components: list[dict[str, Any]] = []
    for key, weight in technical_component_weights.items():
        source = (
            foundation_components.get(key)
            if key == "ai_crawler_report"
            else readiness_components.get(key)
        )
        if not source:
            continue
        source["weight_pct"] = weight
        technical_components.append(source)
    available_weight = sum(
        technical_component_weights.get(component["key"], 0.0)
        for component in technical_components
    )
    technical = (
        round(
            sum(
                float(component.get("score") or 0.0)
                * technical_component_weights.get(component["key"], 0.0)
                for component in technical_components
            )
            / available_weight,
            1,
        )
        if available_weight > 0
        else readiness_detail.get("score")
    )
    technical_detail = {
        "score": technical,
        "components": technical_components or readiness_detail.get("components", []),
    }
    content = (
        content_quality_details.get("score")
        if content_quality_details.get("score") is not None
        else (details.get("content_structure") or {}).get("score")
    )
    ai_visibility = prompt_metrics.get("score") if prompt_metrics else None

    overall = None
    if ai_visibility is not None and technical is not None and content is not None:
        overall = round(0.40 * ai_visibility + 0.30 * technical + 0.30 * content, 1)
        cap = content_quality_details.get("overall_cap")
        if isinstance(cap, (int, float)):
            overall = min(overall, float(cap))

    return {
        "overall": overall,
        "ai_visibility": ai_visibility,
        "technical_setup": technical,
        "content_structure": content,
        "prompt_metrics": prompt_metrics,
        **technical_display,
        "content_quality_details": content_quality_details,
        "details": {
            "technical_setup": technical_detail,
            "technical_foundation": foundation_detail,
            "content_structure": {
                "score": content,
                "components": (
                    content_quality_details.get("components")
                    or (details.get("content_structure") or {}).get("components", [])
                ),
            },
        },
    }


def load_report_meta(audit_dir: Path) -> dict[str, Any]:
    """Header fields for the React report shell (parsed from report.html when present)."""
    import html as html_mod
    import re

    meta: dict[str, Any] = {
        "base_url": "",
        "brand_name": "",
        "industry": "",
        "favicon_url": "",
        "overall_score": None,
        "overall_label": "",
        "score_tone": "yellow",
        "generated_at": "",
    }
    try:
        summ = load_audit_summary(audit_dir)
        meta["base_url"] = str(summ.get("base_url") or "").strip()
    except (OSError, json.JSONDecodeError, FileNotFoundError):
        pass
    base_for_favicon = meta["base_url"]
    if base_for_favicon:
        try:
            from domain_suggest import hostname_for_display_url, public_site_favicon_url

            host = hostname_for_display_url(base_for_favicon)
            if host:
                meta["favicon_url"] = public_site_favicon_url(host)
        except Exception:
            pass
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            ob = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(ob, dict):
                meta["brand_name"] = str(ob.get("brand_name_used") or "").strip()
                meta["industry"] = str(ob.get("industry_used") or "").strip()
        except (OSError, json.JSONDecodeError):
            pass
    rp = audit_dir / "report.html"
    if rp.is_file():
        raw = rp.read_text(encoding="utf-8", errors="replace")
        sub = re.search(r'class="subtitle">([^<]*)</div>', raw)
        if sub:
            meta["base_url"] = html_mod.unescape(sub.group(1).strip()) or meta["base_url"]
        hm = re.search(r'class="header-meta">([^<]*)</div>', raw)
        if hm:
            line = html_mod.unescape(hm.group(1).strip())
            bm = re.search(r"Brand:\s*([^·]+)", line)
            im = re.search(r"Industry:\s*(.+)", line)
            if bm:
                meta["brand_name"] = bm.group(1).strip()
            if im:
                meta["industry"] = im.group(1).strip()
        sn = re.search(r'class="score-number">([^<]+)<', raw)
        sl = re.search(r'class="score-label"[^>]*>([^<]+)<', raw)
        bd = re.search(r'class="badge badge-date">([^<]+)<', raw)
        if sn:
            try:
                score = float(sn.group(1).strip())
                meta["overall_score"] = score
                meta["overall_label"] = (
                    html_mod.unescape(sl.group(1).strip()) if sl else _score_label(score)
                )
                meta["score_tone"] = _score_tone(score)
            except ValueError:
                pass
        if bd:
            meta["generated_at"] = html_mod.unescape(bd.group(1).strip())
    if meta["overall_score"] is None:
        for run in load_archive().get("runs", []):
            rel = str(run.get("audit_dir") or "").strip()
            if rel and audit_dir.resolve() == (REPO_ROOT / rel).resolve():
                if run.get("overall_score") is not None:
                    score = float(run["overall_score"])
                    meta["overall_score"] = score
                    meta["overall_label"] = _score_label(score)
                    meta["score_tone"] = _score_tone(score)
                if not meta["brand_name"] and run.get("brand_name"):
                    meta["brand_name"] = str(run["brand_name"]).strip()
                break
    integrated = load_integrated_scores(audit_dir)
    if integrated.get("overall") is not None:
        score = float(integrated["overall"])
        meta["overall_score"] = score
        meta["overall_label"] = _score_label(score)
        meta["score_tone"] = _score_tone(score)
    return meta


def load_archive() -> dict[str, Any]:
    path = archive_path()
    if not path.is_file():
        return {"runs": []}
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def site_key_from_url(url: str) -> str:
    cr = load_crawl_site()
    base = cr.normalize_base(url.strip())
    return urllib.parse.urlparse(base + "/").netloc.lower()


def save_archive(data: dict[str, Any]) -> None:
    path = archive_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def resolve_overall_score_for_audit(audit_dir: Path) -> float | None:
    """Best available GEO score, preferring the canonical integrated model."""
    integrated = load_integrated_scores(audit_dir)
    if integrated.get("overall") is not None:
        return round(float(integrated["overall"]), 1)
    try:
        summ = load_audit_summary(audit_dir)
        raw = summ.get("overall_score")
        if raw is not None:
            score = float(raw)
            if score > 0:
                return round(score, 1)
    except (OSError, json.JSONDecodeError, FileNotFoundError, TypeError, ValueError):
        pass
    meta = load_report_meta(audit_dir)
    raw = meta.get("overall_score")
    if raw is not None:
        try:
            return round(float(raw), 1)
        except (TypeError, ValueError):
            pass
    return None


def enrich_archive_run(run: dict[str, Any]) -> dict[str, Any]:
    """Refresh overall_score from the canonical integrated score model."""
    out = dict(run)
    rel = str(out.get("audit_dir") or "").strip()
    if not rel:
        return out
    try:
        adir = resolve_audit_dir(rel)
    except Exception:
        return out
    if not adir.is_dir():
        return out
    resolved = resolve_overall_score_for_audit(adir)
    if resolved is not None:
        out["overall_score"] = resolved
    return out


def archive_add_run(
    *,
    primary_url: str,
    audit_dir: Path,
    overall: float,
    competitors: list[str],
    owner_email: str | None = None,
    brand_name: str | None = None,
) -> None:
    data = load_archive()
    rel = audit_dir_api_rel(audit_dir)
    sk = site_key_from_url(primary_url)
    entry: dict[str, Any] = {
        "id": datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + sk.replace(".", "_"),
        "primary_url": load_crawl_site().normalize_base(primary_url.strip()),
        "site_key": sk,
        "audit_dir": rel,
        "created_at": datetime.now(UTC).isoformat(),
        "overall_score": round(overall, 1),
        "competitors": competitors,
    }
    if owner_email and owner_email.strip():
        entry["owner_email"] = owner_email.strip().lower()
    if brand_name and str(brand_name).strip():
        entry["brand_name"] = str(brand_name).strip()
    if float(entry.get("overall_score") or 0) <= 0:
        resolved = resolve_overall_score_for_audit(audit_dir)
        if resolved is not None:
            entry["overall_score"] = resolved
    data.setdefault("runs", []).append(entry)
    save_archive(data)


def runs_for_user(owner_email: str) -> list[dict[str, Any]]:
    want = owner_email.strip().lower()
    data = load_archive()
    runs = [
        enrich_archive_run(r)
        for r in data.get("runs", [])
        if (r.get("owner_email") or "").strip().lower() == want
    ]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return runs


def sample_audit_dir() -> Path | None:
    base = audit_output_base()
    for audit_id in SAMPLE_AUDIT_IDS:
        p = (base / audit_id).resolve()
        if (p / "audit_summary.json").is_file():
            return p
    for rel in SAMPLE_AUDIT_RELS:
        p = (REPO_ROOT / rel).resolve()
        if (p / "audit_summary.json").is_file():
            return p
    return None


def latest_audit_dir() -> Path | None:
    audits = list_primary_audits(limit=1)
    if not audits:
        return None
    return resolve_audit_dir(audits[0]["audit_dir"])


def audit_dir_for_run(out_base: str, primary_url: str) -> Path:
    cr = load_crawl_site()
    base = cr.normalize_base(primary_url.strip())
    return (audit_output_base(out_base) / cr.safe_dir_name(base)).resolve()


def seed_audit_dir_from_wizard(
    audit_dir: Path,
    *,
    primary_url: str,
    brand_name: str,
    industry: str,
    market_country: str,
    market_country_code: str,
    additional_markets: list[dict[str, Any]] | None,
    competitor_urls: list[str],
    products_rows: list[dict[str, Any]],
    competitors_detail: list[dict[str, Any]],
    ga4_property_id: str = "",
    ga4_ai_channel_names: str = "",
    ga4_conversion_event_name: str = "purchase",
    crawl_urls: list[str] | None = None,
    preserve_prompt_data: bool = False,
    prompt_locales: list[dict[str, Any]] | None = None,
    notification_email: str | None = None,
) -> None:
    """
    Create the audit folder early (before crawl) so the report UI can load, and persist
    wizard products/prompts + competitors for prompt performance and onboarding merge.
    Clears prior live-probe artifacts unless this is a config-only rerun.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    # Drop prior run outputs so the UI does not show a stale report while re-crawling.
    stale_files = [
        "report.html",
        "report_slides.html",
        "ga4_traffic.json",
        "ga4_top_pages.json",
        "ga4_ai_insights.json",
        "comparison.json",
        "comparison.md",
    ]
    if not preserve_prompt_data:
        stale_files.extend([
            "prompt_performance_live_probe.json",
            "prompt_performance_probe_pending.json",
            "prompt_probe_progress.jsonl",
            "prompt_performance_sentiment.json",
        ])
    for fn in stale_files:
        (audit_dir / fn).unlink(missing_ok=True)

    rel_out = audit_dir_api_rel(audit_dir)
    existing_created_at = None
    existing_summary_path = audit_dir / "audit_summary.json"
    if existing_summary_path.is_file():
        try:
            existing_summary = json.loads(
                existing_summary_path.read_text(encoding="utf-8", errors="replace")
            )
            if isinstance(existing_summary, dict):
                existing_created_at = existing_summary.get("created_at")
        except (OSError, json.JSONDecodeError):
            existing_created_at = None
    stub = {
        "audit_label": "primary",
        "base_url": primary_url.strip(),
        "output_dir": rel_out,
        "overall_score": None,
        "created_at": existing_created_at or datetime.now(UTC).isoformat(),
    }
    (audit_dir / "audit_summary.json").write_text(
        json.dumps(stub, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    site_u = primary_url.strip()
    existing_ob: dict[str, Any] = {}
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            raw = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                existing_ob = raw
        except (OSError, json.JSONDecodeError):
            pass
    onboarding = {**existing_ob}
    onboarding["brand_name_used"] = brand_name.strip()
    onboarding["brand_website_used"] = site_u
    onboarding["industry_used"] = industry.strip()
    onboarding["geo_market_country"] = market_country.strip()
    onboarding["geo_market_country_code"] = market_country_code.strip()
    try:
        from prompt_locales import normalize_prompt_locales

        onboarding["prompt_locales"] = normalize_prompt_locales(
            prompt_locales,
            market_country=market_country.strip(),
            market_country_code=market_country_code.strip(),
        )
    except Exception:
        onboarding["prompt_locales"] = [
            {
                "country": market_country.strip(),
                "country_code": market_country_code.strip(),
                "language": "en",
                "language_name": "English",
                "key": f"{(market_country_code or 'XX').strip().upper() or 'XX'}:en",
                "label": f"{market_country.strip() or 'Market'}: English",
            }
        ]
    extra_markets: list[dict[str, str]] = []
    for row in additional_markets or []:
        if not isinstance(row, dict):
            continue
        c = str(row.get("country") or "").strip()
        cc = str(row.get("country_code") or "").strip().upper()
        if not c or not cc:
            continue
        extra_markets.append({"country": c, "country_code": cc})
    onboarding["additional_crawl_markets"] = extra_markets
    onboarding["accepted_competitors"] = list(competitor_urls)
    if crawl_urls:
        onboarding["crawl_urls"] = [u for u in crawl_urls if str(u).strip()]
    else:
        onboarding.pop("crawl_urls", None)
    ga4_prop = ga4_property_id.strip()
    if ga4_prop:
        onboarding["ga4_property_id"] = ga4_prop
    ga4_ch = ga4_ai_channel_names.strip()
    if ga4_ch:
        onboarding["ga4_ai_channel_names"] = ga4_ch
    onboarding["ga4_conversion_event_name"] = (
        ga4_conversion_event_name.strip() or "purchase"
    )
    try:
        from api.conversion_events import conversion_events_as_dicts, parse_conversion_events

        onboarding["ga4_conversion_events"] = conversion_events_as_dicts(
            parse_conversion_events(onboarding["ga4_conversion_event_name"])
        )
    except Exception:  # noqa: BLE001
        onboarding["ga4_conversion_events"] = [
            {"event": "purchase", "label": "purchase"}
        ]
    notify = (notification_email or "").strip()
    if notify and "@" in notify:
        onboarding["notification_email"] = notify.lower()
    else:
        onboarding.pop("notification_email", None)

    if not products_rows:
        (audit_dir / "onboarding_context.json").write_text(
            json.dumps(onboarding, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return

    cleaned_rows: list[dict[str, Any]] = []
    names: list[str] = []
    for r in products_rows:
        label = str(r.get("product_or_service") or "").strip()
        if not label:
            continue
        raw_prs = r.get("prompts") if isinstance(r.get("prompts"), list) else []
        prs = [str(p).strip() for p in raw_prs if str(p).strip()]
        if not prs:
            continue
        prompt_set = set(prs)
        raw_tags = r.get("prompt_tags") if isinstance(r.get("prompt_tags"), dict) else {}
        prompt_tags = {
            str(prompt): [str(tag).strip() for tag in tags if str(tag).strip()]
            for prompt, tags in raw_tags.items()
            if str(prompt) in prompt_set and isinstance(tags, list)
        }
        custom_prompts = [
            str(prompt).strip()
            for prompt in (r.get("custom_prompts") or [])
            if str(prompt).strip() in prompt_set
        ]
        cleaned_rows.append(
            {
                "product_or_service": label,
                "prompts": prs,
                "prompt_tags": prompt_tags,
                "custom_prompts": custom_prompts,
                "is_custom_topic": bool(r.get("is_custom_topic")),
            }
        )
        names.append(label)

    if not cleaned_rows:
        (audit_dir / "onboarding_context.json").write_text(
            json.dumps(onboarding, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return

    (audit_dir / "products_and_services.json").write_text(
        json.dumps(
            {
                "website_url": site_u,
                "products_and_services": names,
                "rows": cleaned_rows,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    comp_detail: list[dict[str, str]] = []
    for d in competitors_detail:
        if not isinstance(d, dict):
            continue
        if d.get("included") is False:
            continue
        u = str(d.get("competitor_website") or "").strip()
        if not u:
            continue
        comp_detail.append(
            {
                "competitor_website": u,
                "competitor_brand": str(d.get("competitor_brand") or "").strip(),
            }
        )

    onboarding["competitors_detail"] = comp_detail
    onboarding["products_and_services"] = names
    onboarding["products_and_services_rows"] = cleaned_rows
    (audit_dir / "onboarding_context.json").write_text(
        json.dumps(onboarding, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def create_report_cmd_env(
    primary: str,
    competitors: list[str],
    out_base: str,
    max_sitemap_urls: int,
    delay: float,
    *,
    brand_name: str = "",
    industry: str = "",
    market_country: str = "",
    market_country_code: str = "",
    additional_markets: list[dict[str, Any]] | None = None,
    ga4_property_id: str | None = None,
    ga4_ai_channels: str | None = None,
    ga4_oauth_credentials_path: str | None = None,
    crawl_urls: list[str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    out_dir = audit_output_base(out_base)
    cmd: list[str] = [
        sys.executable,
        "-u",
        str(BACKEND_ROOT / "create-report.py"),
        primary.strip(),
        "--out",
        str(out_dir),
        "--max-sitemap-urls",
        str(max_sitemap_urls),
        "--max-sitemaps",
        "40",
        "--delay",
        str(delay),
        "--sample-robots",
        str(ASSETS_ROOT / "reference" / "robots.txt"),
        "--sample-llms",
        str(ASSETS_ROOT / "reference" / "llms-txt-skeleton.txt"),
    ]
    if brand_name.strip():
        cmd.extend(["--brand", brand_name.strip()])
    if industry.strip():
        cmd.extend(["--industry", industry.strip()])
    if market_country.strip():
        cmd.extend(["--market-country", market_country.strip()])
    if market_country_code.strip():
        cmd.extend(["--market-country-code", market_country_code.strip()])
    if additional_markets:
        cmd.extend(["--extra-markets-json", json.dumps(additional_markets, ensure_ascii=False)])
    for c in competitors:
        c = c.strip()
        if c:
            cmd.extend(["--competitor", c])
    ga4_prop = (ga4_property_id or os.environ.get("GA4_PROPERTY_ID", "") or "").strip()
    if ga4_ai_channels is None:
        ga4_ch = os.environ.get("GA4_AI_CHANNEL_NAMES", "").strip()
    else:
        ga4_ch = str(ga4_ai_channels).strip()
    if ga4_prop:
        cmd.extend(["--ga4-property", ga4_prop])
    if ga4_ch:
        cmd.extend(["--ga4-ai-channels", ga4_ch])
    if crawl_urls:
        clean = [u for u in crawl_urls if str(u).strip()]
        if clean:
            cmd.extend(["--include-urls", json.dumps(clean)])
    env = os.environ.copy()
    if ga4_oauth_credentials_path:
        env["GOOGLE_APPLICATION_CREDENTIALS"] = ga4_oauth_credentials_path
    return cmd, env


def iter_pipeline_logs(
    primary: str,
    competitors: list[str],
    out_base: str,
    max_sitemap_urls: int,
    delay: float,
    **kwargs: Any,
) -> Iterator[str]:
    cmd, env = create_report_cmd_env(
        primary, competitors, out_base, max_sitemap_urls, delay, **kwargs
    )
    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    if proc.stdout is None:
        raise RuntimeError("Could not capture create-report output")
    buf: list[str] = []
    for line in proc.stdout:
        buf.append(line)
        yield line
    rc = proc.wait()
    if rc != 0:
        tail = "".join(buf[-120:]).strip()
        raise RuntimeError(tail or f"create-report failed (exit {rc})")


def suggest_domains(query: str, *, limit: int = 12) -> list[dict[str, str]]:
    from domain_suggest import domain_search_tuple_options

    return [
        {"label": label, "url": url}
        for label, url in domain_search_tuple_options(query, limit=limit)
    ]


def get_industries() -> list[str]:
    cr = load_create_report()
    industries = list(getattr(cr, "COMMON_INDUSTRIES", ()))
    if not industries:
        industries = ["Auto & Vehicles", "Shopping", "Other Business Activity"]
    return industries


def track_competitor_config(
    audit_dir: Path,
    *,
    name: str,
    website: str = "",
) -> dict[str, Any]:
    """Persist a detected visibility competitor in an audit's editable config."""
    config_path = audit_dir / "onboarding_context.json"
    if not config_path.is_file():
        raise FileNotFoundError("Audit configuration not found")
    config = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(config, dict):
        raise ValueError("Audit configuration is invalid")

    clean_name = name.strip()
    clean_website = website.strip()
    rows = [
        row for row in (config.get("competitors_detail") or [])
        if isinstance(row, dict)
    ]
    name_key = clean_name.casefold()
    website_key = clean_website.lower().rstrip("/")
    for row in rows:
        same_name = str(row.get("competitor_brand") or "").strip().casefold() == name_key
        same_site = bool(website_key) and (
            str(row.get("competitor_website") or "").strip().lower().rstrip("/") == website_key
        )
        if same_name or same_site:
            return {"tracked": True, "competitor": row, "already_tracked": True}
    if len(rows) >= 10:
        raise ValueError("Config already contains the maximum of 10 competitors")

    competitor = {
        "competitor_brand": clean_name,
        "competitor_website": clean_website,
    }
    rows.append(competitor)
    config["competitors_detail"] = rows
    if clean_website:
        accepted = [
            str(value).strip() for value in (config.get("accepted_competitors") or [])
            if str(value).strip()
        ]
        if website_key not in {value.lower().rstrip("/") for value in accepted}:
            accepted.append(clean_website)
        config["accepted_competitors"] = accepted
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return {"tracked": True, "competitor": competitor, "already_tracked": False}


def competitor_urls_for_crawl(audit_dir: Path) -> list[str]:
    """Return configured competitor website URLs for an in-place crawl (max 10)."""
    from geo_urls import normalize_competitor_url

    config_path = audit_dir / "onboarding_context.json"
    urls: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        cleaned = normalize_competitor_url(str(raw or "").strip())
        if not cleaned:
            return
        key = cleaned.lower().rstrip("/")
        if key in seen:
            return
        seen.add(key)
        urls.append(cleaned)

    if config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            config = {}
        if isinstance(config, dict):
            for row in config.get("competitors_detail") or []:
                if isinstance(row, dict):
                    _add(str(row.get("competitor_website") or ""))
            for value in config.get("accepted_competitors") or []:
                _add(str(value or ""))
    return urls[:10]


COMPETITOR_CRAWL_STATUS_FILE = "competitor_crawl_status.json"
COMPETITOR_CRAWLS_DIR = "competitor_crawls"
# Competitor crawl Cloud Run Job timeout is 2h; treat older "running" as orphaned.
STALE_COMPETITOR_CRAWL_SECONDS = 3 * 60 * 60
# If the Job pending file is gone and updates stopped, orphan sooner.
STALE_COMPETITOR_ORPHAN_SECONDS = 45 * 60


def competitor_crawl_status_path(audit_dir: Path) -> Path:
    return audit_dir / COMPETITOR_CRAWL_STATUS_FILE


def parse_status_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def running_status_age_seconds(payload: dict[str, Any]) -> float | None:
    for key in ("updated_at", "started_at", "created_at"):
        dt = parse_status_timestamp(payload.get(key))
        if dt is not None:
            return max(0.0, (datetime.now(UTC) - dt).total_seconds())
    return None


def running_status_is_stale(
    payload: dict[str, Any],
    *,
    max_age_seconds: float,
    orphan_after_seconds: float | None = None,
    pending_path: Path | None = None,
) -> bool:
    if str(payload.get("status") or "") not in {"running", "starting", "queued"}:
        return False
    age = running_status_age_seconds(payload)
    if age is None:
        return True
    if age >= max_age_seconds:
        return True
    if (
        orphan_after_seconds is not None
        and pending_path is not None
        and age >= orphan_after_seconds
        and not pending_path.is_file()
    ):
        return True
    return False


def read_competitor_crawl_status(audit_dir: Path) -> dict[str, Any] | None:
    path = competitor_crawl_status_path(audit_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def reconcile_competitor_crawl_status(audit_dir: Path) -> dict[str, Any] | None:
    """
    Mark orphaned in-process / killed Job crawls as errored so the UI can retry.
    Legacy daemon-thread crawls left ``status=running`` forever when the service recycled.
    """
    status = read_competitor_crawl_status(audit_dir)
    if not status:
        return None
    pending = audit_dir / "competitor_crawl_pending.json"
    if not running_status_is_stale(
        status,
        max_age_seconds=STALE_COMPETITOR_CRAWL_SECONDS,
        orphan_after_seconds=STALE_COMPETITOR_ORPHAN_SECONDS,
        pending_path=pending,
    ):
        return status

    age = running_status_age_seconds(status)
    age_label = f"{int(age // 3600)}h" if age is not None and age >= 3600 else (
        f"{int((age or 0) // 60)}m" if age is not None else "unknown"
    )
    log.warning(
        "Marking stale competitor crawl as error for %s (age=%s)",
        audit_dir_api_rel(audit_dir),
        age_label,
    )
    pending.unlink(missing_ok=True)
    return write_competitor_crawl_status(
        audit_dir,
        {
            **status,
            "status": "error",
            "finished_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "percent": int(status.get("percent") or 0),
            "error": "Competitor crawl timed out or the worker stopped unexpectedly.",
            "detail": (
                "Previous competitor crawl was interrupted (often after a service restart). "
                f"Last update age: {age_label}. Start a new crawl to retry."
            ),
            "current_step": "error",
            "seen": False,
            "stale": True,
        },
    )


def write_competitor_crawl_status(audit_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    audit_dir.mkdir(parents=True, exist_ok=True)
    body = {
        **payload,
        "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "audit_dir": audit_dir_api_rel(audit_dir),
        "job_type": "competitor_crawl",
    }
    competitor_crawl_status_path(audit_dir).write_text(
        json.dumps(body, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return body


def mark_competitor_crawl_seen(audit_dir: Path) -> dict[str, Any]:
    current = read_competitor_crawl_status(audit_dir) or {}
    if not current:
        return {"status": "idle", "seen": True, "audit_dir": audit_dir_api_rel(audit_dir)}
    current["seen"] = True
    return write_competitor_crawl_status(audit_dir, current)


def archive_current_competitor_crawl(audit_dir: Path) -> str | None:
    """
    Snapshot the current comparison (+ competitor crawl dirs) before overwriting.
    Returns the archive id, or None when there was nothing to archive.
    """
    import shutil

    comparison = audit_dir / "comparison.json"
    competitors_dir = audit_dir / "competitors"
    if not comparison.is_file() and not competitors_dir.is_dir():
        return None

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = audit_dir / COMPETITOR_CRAWLS_DIR / stamp
    dest.mkdir(parents=True, exist_ok=True)
    if comparison.is_file():
        shutil.copy2(comparison, dest / "comparison.json")
    comparison_md = audit_dir / "comparison.md"
    if comparison_md.is_file():
        shutil.copy2(comparison_md, dest / "comparison.md")
    if competitors_dir.is_dir():
        shutil.copytree(competitors_dir, dest / "competitors", dirs_exist_ok=True)

    previous = read_competitor_crawl_status(audit_dir) or {}
    manifest = {
        "archive_id": stamp,
        "archived_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "from_status": {
            "status": previous.get("status"),
            "finished_at": previous.get("finished_at"),
            "started_at": previous.get("started_at"),
            "crawled": previous.get("crawled") or [],
            "competitor_count": previous.get("competitor_count"),
        },
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return stamp


def list_competitor_crawl_archives(audit_dir: Path) -> list[dict[str, Any]]:
    root = audit_dir / COMPETITOR_CRAWLS_DIR
    if not root.is_dir():
        return []
    archives: list[dict[str, Any]] = []
    for child in sorted(root.iterdir(), reverse=True):
        if not child.is_dir():
            continue
        entry: dict[str, Any] = {"archive_id": child.name}
        manifest_path = child / "manifest.json"
        if manifest_path.is_file():
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(raw, dict):
                    entry.update(raw)
            except (OSError, json.JSONDecodeError):
                pass
        archives.append(entry)
    return archives


def crawl_competitors_in_place(
    audit_dir: Path,
    *,
    max_sitemap_urls: int = 40,
    delay: float = 0.2,
    on_progress: Any | None = None,
) -> dict[str, Any]:
    """
    Crawl configured competitor sites into ``audit_dir/competitors/`` without
    re-crawling the primary site, then rebuild comparison + report.html.

    Archives any existing comparison into ``competitor_crawls/<id>/`` first so
    prior crawls remain available; live ``comparison.json`` always reflects the
    most recent successful crawl.
    """
    import types

    summary_path = audit_dir / "audit_summary.json"
    if not summary_path.is_file():
        raise FileNotFoundError("Primary audit summary not found")

    competitor_urls = competitor_urls_for_crawl(audit_dir)
    if not competitor_urls:
        raise ValueError("No competitor websites configured for this audit")

    if on_progress is not None:
        on_progress("Archiving previous competitor crawl…", 0, len(competitor_urls))
    archived_id = archive_current_competitor_crawl(audit_dir)

    primary_report = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(primary_report, dict):
        raise ValueError("Primary audit summary is invalid")

    crawl = load_crawl_site()
    primary_base = str(primary_report.get("base_url") or "").strip()
    if not primary_base:
        raise ValueError("Primary audit is missing base_url")

    config: dict[str, Any] = {}
    config_path = audit_dir / "onboarding_context.json"
    if config_path.is_file():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                config = raw
        except (OSError, json.JSONDecodeError):
            config = {}

    args = types.SimpleNamespace(
        delay=float(delay),
        sample_robots=ASSETS_ROOT / "reference" / "robots.txt",
        sample_llms=ASSETS_ROOT / "reference" / "llms-txt-skeleton.txt",
        max_sitemaps=40,
        max_sitemap_urls=int(max_sitemap_urls),
        include_urls="",
        brand=str(config.get("brand_name_used") or "").strip() or None,
        industry=str(config.get("industry_used") or "").strip(),
        market_country=str(config.get("geo_market_country") or "").strip(),
        market_country_code=str(config.get("geo_market_country_code") or "").strip(),
        no_brand_scan=False,
        insecure=False,
        no_certifi=False,
    )
    tls_info = crawl.configure_tls(insecure=False, no_certifi=False)

    competitor_bundle: list[tuple[str, dict[str, Any]]] = []
    crawled: list[str] = []
    skipped: list[dict[str, str]] = []

    # Map competitor URL → configured brand label for brand-visibility scans.
    competitor_brand_by_url: dict[str, str] = {}
    for row in config.get("competitors_detail") or []:
        if not isinstance(row, dict):
            continue
        website = str(row.get("competitor_website") or "").strip()
        name = str(row.get("competitor_brand") or "").strip()
        if website and name:
            try:
                competitor_brand_by_url[crawl.normalize_base(website).rstrip("/").lower()] = name
            except ValueError:
                competitor_brand_by_url[website.rstrip("/").lower()] = name

    try:
        from browser_fetch import close_browser_session
    except ImportError:
        close_browser_session = None  # type: ignore[assignment,misc]

    try:
        for index, comp_url in enumerate(competitor_urls, start=1):
            if on_progress is not None:
                on_progress(
                    f"Crawling competitor {index}/{len(competitor_urls)}: {comp_url}",
                    index - 1,
                    len(competitor_urls),
                )
            try:
                comp_base = crawl.normalize_base(comp_url)
            except ValueError as exc:
                skipped.append({"url": comp_url, "reason": str(exc)})
                continue
            if comp_base.rstrip("/") == primary_base.rstrip("/"):
                skipped.append({"url": comp_url, "reason": "same origin as primary"})
                continue
            comp_out = str(audit_dir / "competitors" / crawl.safe_dir_name(comp_base))
            label = f"Competitor {index}"
            # Use this competitor's brand for off-site visibility — never the primary brand.
            comp_brand = competitor_brand_by_url.get(comp_base.rstrip("/").lower()) or None
            if not comp_brand:
                try:
                    from brand_visibility_scan import derive_brand_from_base

                    comp_brand = derive_brand_from_base(comp_base)
                except Exception:
                    comp_brand = None
            comp_args = types.SimpleNamespace(**{**vars(args), "brand": comp_brand})
            report = crawl.run_site_audit(
                comp_args,
                comp_base,
                comp_out,
                audit_label=f"competitor_{index}",
                tls_info=tls_info,
            )
            competitor_bundle.append((label, report))
            crawled.append(comp_base)
    finally:
        if close_browser_session is not None:
            close_browser_session()

    if not competitor_bundle:
        raise RuntimeError(
            "Competitor crawl produced no usable sites"
            + (f": {skipped[0]['reason']}" if skipped else "")
        )

    if on_progress is not None:
        on_progress("Writing comparison and rebuilding report…", len(competitor_urls), len(competitor_urls))

    comparison_json, comparison_md = crawl.write_comparison_files(
        str(audit_dir),
        primary_report,
        competitor_bundle,
    )
    primary_report["comparison"] = {
        "markdown_path": comparison_md,
        "json_path": comparison_json,
        "competitors": [rep.get("base_url") for _, rep in competitor_bundle],
    }
    summary_path.write_text(
        json.dumps(primary_report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    if config_path.is_file() and crawled:
        accepted = [
            str(value).strip() for value in (config.get("accepted_competitors") or []) if str(value).strip()
        ]
        accepted_keys = {value.lower().rstrip("/") for value in accepted}
        for url in crawled:
            key = url.lower().rstrip("/")
            if key not in accepted_keys:
                accepted.append(url)
                accepted_keys.add(key)
        config["accepted_competitors"] = accepted
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    report_rc = 0
    try:
        create_report = load_create_report()
        report_rc = int(
            create_report.generate_reports(
                audit_dir,
                None,
                industry=str(config.get("industry_used") or ""),
                brand=str(config.get("brand_name_used") or ""),
            )
        )
    except Exception as exc:
        # comparison.json is already written; embed refresh can still render it
        report_rc = -1
        if on_progress is not None:
            on_progress(f"Report rebuild warning: {exc}", len(competitor_urls), len(competitor_urls))

    return {
        "crawled": crawled,
        "skipped": skipped,
        "competitor_count": len(crawled),
        "comparison_json": comparison_json,
        "report_rc": report_rc,
        "archived_id": archived_id,
        "audit_dir": audit_dir_api_rel(audit_dir),
    }


def app_config() -> dict[str, Any]:
    from geo_app_env import app_env_display_label, current_app_env

    return {
        "app_env": current_app_env(),
        "app_env_label": app_env_display_label(),
        "report_sections": [
            {"id": "summary", "label": "Summary", "group": "Overview"},
            {"id": "config", "label": "Config", "group": "Overview"},
            {"id": "recommendations", "label": "Recommendations", "group": "Overview"},
            {"id": "ai-traffic-dashboard", "label": "AI Traffic Dashboard", "group": "Overview"},
            {"id": "competitor-comparison", "label": "Competitor comparison", "group": "Overview"},
            {"id": "ai-visibility-overview", "label": "Overview", "group": "AI visibility"},
            {"id": "prompts", "label": "Prompts", "group": "AI visibility"},
            {"id": "competitor-visibility", "label": "Competitor visibility", "group": "AI visibility"},
            {"id": "citations", "label": "Citations", "group": "AI visibility"},
            {"id": "reddit-citations", "label": "Reddit Citations", "group": "AI visibility"},
            {"id": "youtube-citations", "label": "YouTube Citations", "group": "AI visibility"},
            {"id": "technical-overview", "label": "Overview", "group": "Technical setup"},
            {"id": "crawler-access", "label": "Crawler access", "group": "Technical setup"},
            {"id": "citability", "label": "Citability", "group": "Technical setup"},
            {"id": "platform-readiness", "label": "Platform readiness", "group": "Technical setup"},
            {"id": "content-overview", "label": "Overview", "group": "Content quality"},
            {"id": "eeat-signals", "label": "E-E-A-T Signals", "group": "Content quality"},
            {"id": "content-structure-answerability", "label": "Content Structure & Answerability", "group": "Content quality"},
            {"id": "schema-entity-markup", "label": "Schema & Entity Markup", "group": "Content quality"},
            {"id": "brand-visibility-authority", "label": "Brand Visibility & Authority", "group": "Content quality"},
            {"id": "sample-scripts", "label": "Sample scripts", "group": "Workshop"},
            {"id": "content-outline-generator", "label": "Content outline generator", "group": "Workshop"},
        ],
    }
