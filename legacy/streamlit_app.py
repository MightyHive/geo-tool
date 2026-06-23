#!/usr/bin/env python3
"""
Streamlit UI for GEO crawl + scored report.

  APP_ENV=development ./scripts/run_streamlit_dev.sh
  APP_ENV=staging ./scripts/run_streamlit_staging.sh
  APP_ENV=production ./scripts/run_streamlit_prod.sh

  Or: ``PYTHONPATH=backend streamlit run legacy/streamlit_app.py`` (defaults to **development**; see ``geo_app_env.py``).

Landing: choose New audit or Existing audits. Main view: embedded report.html (static export layout).
Optional Google SSO (Streamlit auth): audits run while signed in are listed under Existing audits.
"""

from __future__ import annotations

import base64
import hashlib
import html
import hmac
import importlib.util
import json
import re
import time
from collections.abc import Iterator, Mapping
import os
import secrets as py_secrets
import subprocess
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from geo_app_env import (
    app_env_display_label,
    current_app_env,
    default_streamlit_public_origin,
    load_app_environment,
)

import streamlit as st
from streamlit.errors import StreamlitAuthError

from competitor_suggest import ensure_llm_env_from_streamlit_secrets
from domain_suggest import hostname_for_display_url, public_site_favicon_url, render_url_searchbox

load_app_environment()

from geo_app_env import ASSETS_ROOT, BACKEND_ROOT, REPO_ROOT

# OAuth redirects (Streamlit auth + GA4) must match Google Cloud “Authorized redirect URIs”.
# Set ``STREAMLIT_PUBLIC_ORIGIN`` in ``env/.env.<APP_ENV>`` or ``.env`` (see ``geo_app_env.py``).
DEFAULT_STREAMLIT_PUBLIC_ORIGIN = default_streamlit_public_origin()
DEFAULT_GA4_OAUTH_REDIRECT_URI = f"{DEFAULT_STREAMLIT_PUBLIC_ORIGIN}/"

ARCHIVE_PATH = REPO_ROOT / "audit_archive" / "index.json"
DEFAULT_OUT_BASE = "audit_output"
MAX_COMPETITORS = 12
WIZARD_GEMINI_COMP_INITIAL = 3
# First option in Brand industry selectbox — user must pick a real industry (never auto-filled from GA4).
WIZ_INDUSTRY_PLACEHOLDER = "— Select an industry —"


def _effective_na_industry() -> str:
    """Industry for API calls; empty when the user has not picked a real list value."""
    v = str(st.session_state.get("na_industry") or "").strip()
    if not v or v == WIZ_INDUSTRY_PLACEHOLDER:
        return ""
    return v


# Optional on-disk sample for "Quick open" / wizard shortcuts (first match wins).
SAMPLE_AUDIT_IDS: tuple[str, ...] = ("starbucks.co.uk_d6a4f5ac1a37",)
SAMPLE_AUDIT_RELS: tuple[Path, ...] = tuple(
    Path("audit_output") / audit_id for audit_id in SAMPLE_AUDIT_IDS
)


def _resolved_sample_audit_dir() -> Path | None:
    for rel in SAMPLE_AUDIT_RELS:
        p = (REPO_ROOT / rel).resolve()
        if (p / "audit_summary.json").is_file():
            return p
    return None


def _canonical_site_hostname(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u
    try:
        return (urllib.parse.urlparse(u).hostname or "").lower().replace("www.", "")
    except Exception:
        return ""


def _ga4_top_pages_match_audit_site(top_pages: Any, base_url: str) -> bool:
    """True if GA4 page rows look like they belong to the audited site (hostname match)."""
    if not isinstance(top_pages, list) or not top_pages:
        return False
    ah = _canonical_site_hostname(base_url)
    if not ah:
        return False
    for p in top_pages[:30]:
        if not isinstance(p, dict):
            continue
        ph = str(p.get("host") or "").strip().lower().replace("www.", "")
        if not ph:
            continue
        if ph == ah or ph.endswith("." + ah):
            return True
    return False


def _clear_stale_ga4_session_keys() -> None:
    """Drop GA4-derived UI keys so opening another audit cannot inherit a prior session's GA4 state."""
    for k in (
        "ga4_top_pages",
        "ga4_primary_market",
        "onboarding_suggestions",
        "suggested_brand_name",
        "suggested_brand_website",
        "suggested_industry_hint",
        "suggestions_loaded",
    ):
        st.session_state.pop(k, None)


def _clear_wizard_site_derived_state() -> None:
    """Remove GA4 + Gemini wizard data when the crawl target site changes or a fresh audit starts."""
    for k in (
        "ga4_top_pages",
        "ga4_primary_market",
        "onboarding_suggestions",
        "accepted_categories",
        "accepted_products",
        "accepted_competitors",
        "suggested_brand_website",
        "suggested_brand_name",
        "suggested_industry_hint",
        "suggestions_loaded",
        "geo_pss_rows",
        "wiz_pss_selected",
        "geo_competitors_detail",
        "wiz_comp_urls_sel",
        "pp_prompts",
        "pp_last_cat_labels",
        "pp_live_probe",
        "pp_live_probe_highlight_brand",
        "pp_live_probe_highlight_comps",
        "pp_live_probe_highlight_comp_brands",
        "pp_content_actions",
        "pp_sov",
    ):
        st.session_state.pop(k, None)
    for i in range(1, MAX_COMPETITORS + 1):
        st.session_state.pop(f"na_c{i}", None)
    for i in range(MAX_COMPETITORS):
        st.session_state.pop(f"pp_cmp_u{i}", None)
        st.session_state.pop(f"pp_cmp_b{i}", None)


def _reset_new_audit_wizard_state() -> None:
    """Blank slate for **New audit** (avoids leaking another site's GA4 / Gemini rows into the wizard)."""
    _clear_wizard_site_derived_state()
    st.session_state.pop("_wiz_bound_site_host", None)
    st.session_state.pop("_wiz_gemini_pss_site", None)
    st.session_state.pop("_wiz_primary_url", None)
    st.session_state.pop("_wiz_primary_brand", None)
    st.session_state["na_brand"] = ""
    st.session_state["na_site"] = ""
    st.session_state["wiz_comp_manual_url"] = ""
    st.session_state.pop("wiz_add_comp_msg", None)
    st.session_state.pop("wiz_add_comp_url", None)
    st.session_state.pop("_ga4_onboarding_pull_committed", None)
    st.session_state.pop("wiz_s3_site_preview_unlocked", None)
    st.session_state.pop("_wiz_s3_brand_locked", None)
    st.session_state.pop("_wiz_s3_na_site_normalized_pending", None)
    st.session_state.pop("_wiz_s3_locked_canonical_url", None)
    st.session_state["na_industry"] = WIZ_INDUSTRY_PLACEHOLDER


def _wizard_brand_name() -> str:
    """Brand from **Brand & website**; survives steps where ``na_brand`` widget is not mounted."""
    w = str(st.session_state.get("_wiz_primary_brand") or "").strip()
    if w:
        return w
    lb = str(st.session_state.get("_wiz_s3_brand_locked") or "").strip()
    if lb:
        return lb
    return str(st.session_state.get("na_brand") or "").strip()


def _wiz_s3_resolved_brand_after_preview() -> str:
    """Brand string after site preview is locked (widget ``na_brand`` may be cleared on rerun)."""
    return str(
        st.session_state.get("_wiz_s3_brand_locked")
        or st.session_state.get("na_brand")
        or ""
    ).strip()


def _wizard_brand_website_url() -> str:
    """Crawl target URL from **Brand & website**; survives steps where ``na_site`` widget is not mounted."""
    w = str(st.session_state.get("_wiz_primary_url") or "").strip()
    if w:
        return w
    lk = str(st.session_state.get("_wiz_s3_locked_canonical_url") or "").strip()
    if lk:
        return lk
    return str(st.session_state.get("na_site") or "").strip()


def _wizard_bind_site_after_step3_continue() -> None:
    """Drop GA4 / Gemini wizard leftovers whenever the canonical site host changes (incl. first bind vs "")."""
    from geo_setup_llm import normalize_competitor_url

    raw = str(
        st.session_state.get("_wiz_s3_locked_canonical_url")
        or st.session_state.get("na_site")
        or ""
    ).strip()
    url = normalize_competitor_url(raw) if raw else ""
    cur = _canonical_site_hostname(url)
    if not cur:
        return
    prev = str(st.session_state.get("_wiz_bound_site_host") or "").strip()
    if prev != cur:
        _clear_wizard_site_derived_state()
    st.session_state["_wiz_bound_site_host"] = cur
    st.session_state["_wiz_primary_url"] = url

# ``data-tab-target`` values in ``report.html`` (create-report.render_html) + in-app Prompt performance.
REPORT_GEO_TAB_IDS: frozenset[str] = frozenset(
    {
        "summary",
        "recommendations",
        "competitors",
        "ai-visibility",
        "technical",
        "content",
        "ga4-traffic",
        "samples",
    }
)
REPORT_SECTION_PROMPTS = "prompt_performance"
REPORT_SIDEBAR_GEO_SECTIONS: tuple[tuple[str, str], ...] = (
    ("summary", "Summary"),
    ("ga4-traffic", "AI traffic (GA4)"),
    ("recommendations", "Recommendations"),
    ("competitors", "Competitor comparison"),
    ("ai-visibility", "AI visibility"),
    ("technical", "Technical setup"),
    ("content", "Content quality"),
)
REPORT_SIDEBAR_SAMPLES_SECTION: tuple[str, str] = ("samples", "Sample scripts")


def _render_readable_prompt_rows(prompts: list[Any], *, heading: str | None = None) -> None:
    """One scrollable box; each prompt is a full-width row with wrapped text."""
    rows: list[str] = [
        '<div style="border:1px solid #e5e7eb;border-radius:10px;background:var(--bg-card,#fff);'
        'max-height:28rem;overflow-y:auto;">'
    ]
    for i, raw in enumerate(prompts, start=1):
        esc = html.escape(str(raw).strip())
        rows.append(
            '<div style="border-bottom:1px solid #eee;padding:12px 14px;white-space:pre-wrap;'
            "word-break:break-word;line-height:1.5;font-size:0.95rem;\">"
            f'<span style="color:#6b7280;font-weight:600;margin-right:0.5rem">{i}.</span>{esc}</div>'
        )
    rows.append("</div>")
    if heading:
        st.markdown(f"**{heading}**")
    st.markdown("".join(rows), unsafe_allow_html=True)


def _slug_widget(s: str, *, max_len: int = 48) -> str:
    x = re.sub(r"[^a-zA-Z0-9]+", "_", str(s or "").strip())[:max_len].strip("_").lower()
    return x or "x"


def _session_pss_rows_normalized() -> list[dict[str, Any]]:
    """``geo_pss_rows`` from wizard / onboarding: product_or_service + prompts[]."""
    rows = st.session_state.get("geo_pss_rows")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        label = str(r.get("product_or_service") or "").strip()
        if not label:
            continue
        prs = r.get("prompts")
        if not isinstance(prs, list):
            continue
        ps = [str(p).strip() for p in prs if str(p).strip()]
        if ps:
            out.append({"product_or_service": label, "prompts": ps})
    return out


def _flatten_pss_with_meta(rows: list[dict[str, Any]]) -> tuple[list[str], list[dict[str, str]]]:
    """Stable order for live probes; ``meta[i]`` matches ``flat[i]``."""
    flat: list[str] = []
    meta: list[dict[str, str]] = []
    for r in rows:
        pos = str(r.get("product_or_service") or "").strip()
        for p in r.get("prompts") or []:
            s = str(p).strip()
            if not s:
                continue
            flat.append(s)
            meta.append({"product_or_service": pos})
    return flat, meta


def _split_live_rows_by_product(
    rows: list[dict[str, Any]], live: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    flat, meta = _flatten_pss_with_meta(rows)
    per = [r for r in (live.get("per_prompt") or []) if isinstance(r, dict)]
    out: dict[str, list[dict[str, Any]]] = {}
    for i, pr in enumerate(flat):
        if i >= len(per):
            break
        prod = meta[i]["product_or_service"]
        out.setdefault(prod, []).append(per[i])
    return out


def _mean_brand_visibility(live_rows: list[dict[str, Any]]) -> float:
    vals: list[float] = []
    for r in live_rows:
        g = float(r.get("gemini_brand_mention_pct") or 0)
        o = float(r.get("openai_brand_mention_pct") or 0)
        vals.append((g + o) / 2.0)
    return sum(vals) / len(vals) if vals else 0.0


def _mean_competitor_visibility(live_rows: list[dict[str, Any]]) -> float:
    vals: list[float] = []
    for r in live_rows:
        g = float(r.get("gemini_competitor_mention_pct") or 0)
        o = float(r.get("openai_competitor_mention_pct") or 0)
        vals.append((g + o) / 2.0)
    return sum(vals) / len(vals) if vals else 0.0


def _prompts_scanned_label(live_rows: list[dict[str, Any]], expected: int) -> str:
    ok = 0
    for r in live_rows:
        ge = str(r.get("error_gemini") or "").strip()
        oe = str(r.get("error_openai") or "").strip()
        if not ge or not oe:
            ok += 1
    return f"{ok} / {max(expected, 0)}"


def _render_prompt_performance_pss_grouped(
    pss_rows: list[dict[str, Any]],
    live: dict[str, Any],
    *,
    for_report_tab: bool,
) -> None:
    """Per product/service: summary metric selectbox + per-prompt reply selectbox with visibility in label."""
    by_prod = _split_live_rows_by_product(pss_rows, live)
    hb = str(
        st.session_state.get("pp_live_probe_highlight_brand")
        or _wizard_brand_for_probes()
        or st.session_state.get("pp_brand")
        or ""
    )
    hc = st.session_state.get("pp_live_probe_highlight_comps")
    if not isinstance(hc, list):
        hc = []
    hcb = st.session_state.get("pp_live_probe_highlight_comp_brands")
    if not isinstance(hcb, list):
        hcb = []
    h_reply_raw = live.get("reply_detected_brand_names")
    h_reply = (
        [str(x).strip() for x in h_reply_raw if str(x).strip()]
        if isinstance(h_reply_raw, list)
        else []
    )
    from prompt_suggest import highlight_response_html

    _ppx = "r" if for_report_tab else "n"
    st.markdown("##### By product or service")
    for gi, row in enumerate(pss_rows):
        label = str(row.get("product_or_service") or "").strip()
        if not label:
            continue
        exp_n = len([p for p in (row.get("prompts") or []) if str(p).strip()])
        lrows = by_prod.get(label, [])
        slug = _slug_widget(f"{gi}_{label}")
        with st.expander(f"{label} — {exp_n} prompt(s)", expanded=(gi == 0)):
            metric = st.selectbox(
                "Summary",
                ("Brand visibility %", "Avg competitor visibility %", "Prompts scanned"),
                key=f"pp_pss_metric_{_ppx}_{slug}",
                help="Pick which summary figure to highlight for this product or service.",
            )
            if metric == "Brand visibility %":
                st.metric("Brand visibility (Gemini + OpenAI average)", f"{_mean_brand_visibility(lrows):.1f}%")
            elif metric == "Avg competitor visibility %":
                st.metric("Average competitor visibility (combined models)", f"{_mean_competitor_visibility(lrows):.1f}%")
            else:
                st.metric("Prompts scanned (replies without full failure)", _prompts_scanned_label(lrows, exp_n))

            flat_p = [str(p).strip() for p in (row.get("prompts") or []) if str(p).strip()]
            for pi, pq in enumerate(flat_p):
                lr = lrows[pi] if pi < len(lrows) else None
                st.divider()
                st.markdown(f"**Prompt:** {html.escape(pq)}", unsafe_allow_html=True)
                if lr is None:
                    st.caption("No live probe row for this prompt—re-run probes after changing products or services.")
                    continue
                g_bp = float(lr.get("gemini_brand_mention_pct") or 0)
                g_cp = float(lr.get("gemini_competitor_mention_pct") or 0)
                o_bp = float(lr.get("openai_brand_mention_pct") or 0)
                o_cp = float(lr.get("openai_competitor_mention_pct") or 0)
                hdr = (
                    f"Assistant reply — brand visibility **Gemini {g_bp:.1f}%** · **OpenAI {o_bp:.1f}%** "
                    f"(competitors **Gemini {g_cp:.1f}%** · **OpenAI {o_cp:.1f}%**)"
                )
                st.caption(hdr)
                which = st.selectbox(
                    "Model",
                    ("Gemini", "OpenAI"),
                    key=f"pp_pss_sel_{_ppx}_{slug}_{pi}",
                    help="Show the full assistant response for the selected model.",
                )
                mg = lr.get("mention_scores_gemini") or {}
                mo = lr.get("mention_scores_openai") or {}
                if which == "Gemini":
                    if lr.get("error_gemini"):
                        st.error(str(lr["error_gemini"]))
                    else:
                        st.markdown(
                            highlight_response_html(
                                str(lr.get("gemini_response") or ""),
                                hb,
                                hc,
                                hcb,
                                reply_detected_brands=h_reply if h_reply else None,
                            ),
                            unsafe_allow_html=True,
                        )
                    gd = mg.get("competitor_detail") if isinstance(mg, dict) else None
                    if isinstance(gd, dict) and gd:
                        st.caption("Competitor hit counts: " + ", ".join(f"{k}: {v}" for k, v in sorted(gd.items())[:10]))
                else:
                    if lr.get("error_openai"):
                        st.error(str(lr["error_openai"]))
                    else:
                        st.markdown(
                            highlight_response_html(
                                str(lr.get("openai_response") or ""),
                                hb,
                                hc,
                                hcb,
                                reply_detected_brands=h_reply if h_reply else None,
                            ),
                            unsafe_allow_html=True,
                        )
                    od = mo.get("competitor_detail") if isinstance(mo, dict) else None
                    if isinstance(od, dict) and od:
                        st.caption("Competitor hit counts: " + ", ".join(f"{k}: {v}" for k, v in sorted(od.items())[:10]))


def _streamlit_report_root_css() -> str:
    """Inject :root design tokens from design/report-styles.css (same as design-sample / report.html)."""
    path = ASSETS_ROOT / "design" / "report-styles.css"
    try:
        txt = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    start = txt.find(":root")
    if start == -1:
        return ""
    brace = txt.find("{", start)
    if brace == -1:
        return ""
    depth = 0
    j = brace
    while j < len(txt):
        if txt[j] == "{":
            depth += 1
        elif txt[j] == "}":
            depth -= 1
            if depth == 0:
                j += 1
                break
        j += 1
    block = txt[start:j].strip()
    return f"<style>\n{block}\n</style>\n"


# App chrome matches design/report-styles.css (monks.com-inspired GEO report).
_STREAMLIT_THEME_CSS = """
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,400;0,14..32,500;0,14..32,600;0,14..32,700;1,14..32,400&display=swap" rel="stylesheet"/>
<style>
  html, body, .stApp, [data-testid="stAppViewContainer"] {
    font-family: var(--font-sans, "Inter", "Helvetica Neue", Helvetica, Arial, sans-serif);
    color: var(--text-primary);
    letter-spacing: -0.011em;
  }
  .stApp {
    background: var(--bg-light);
  }
  [data-testid="stHeader"] {
    background: var(--bg-light);
    border-bottom: 1px solid var(--border);
  }
  [data-testid="stSidebar"] h1 {
    font-weight: 600;
    letter-spacing: -0.03em;
    color: var(--brand-dark);
  }
  [data-testid="stSidebar"] .stCaption,
  [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    color: var(--text-secondary);
    line-height: 1.45;
  }
  h1, h2, h3 {
    font-weight: 600;
    letter-spacing: -0.03em;
    color: var(--brand-dark);
  }
  div[data-testid="stExpander"] {
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg-card);
    box-shadow: none;
  }
  section.main .stButton > button[kind="primary"] {
    border-radius: 6px;
    font-weight: 600;
    border: 1px solid var(--border);
    background: var(--brand-dark);
    color: #fff;
    box-shadow: none;
  }
  section.main .stButton > button[kind="primary"]:hover {
    border-color: #000;
    background: #1a1a1a;
    box-shadow: none;
  }
  [data-testid="stCheckbox"] label {
    font-size: 0.9rem;
    color: var(--text-primary);
  }
  .stMarkdown small, .stCaption {
    color: var(--text-muted);
  }
  /* Clear borders on typed fields + select so inputs read as editable */
  [data-testid="stTextInput"] input,
  [data-testid="stNumberInput"] input {
    border: 1px solid var(--border, #e8e8e8) !important;
    border-radius: 6px;
    background: var(--bg-card, #fff);
  }
  [data-testid="stTextInput"] input:focus,
  [data-testid="stNumberInput"] input:focus {
    border-color: var(--brand-dark, #0d0d0d) !important;
    box-shadow: 0 0 0 1px var(--brand-dark, #0d0d0d);
    outline: none;
  }
  [data-testid="stSelectbox"] [data-baseweb="select"] > div {
    border: 1px solid var(--border, #e8e8e8) !important;
    border-radius: 6px;
    background: var(--bg-card, #fff);
  }
  .geo-landing-hero {
    max-width: 720px;
    margin: 2rem auto 1rem;
    padding: 2rem 1.5rem;
    border: 1px solid var(--border);
    border-radius: 12px;
    background: var(--bg-card);
    text-align: center;
  }
  .geo-landing-hero h1 {
    margin-bottom: 0.35rem;
  }
  .geo-landing-sub {
    color: var(--text-secondary);
    font-size: 0.95rem;
    margin-bottom: 1.5rem;
    line-height: 1.5;
  }
  /* —— App shell (mirrors perception-flow: static header + vertical sidebar) —— */
  .geo-app-header {
    background: #2d2d2d;
    color: #fff;
    padding: 1rem 1.5rem;
    margin: -1rem -1rem 1.25rem -1rem;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 0.75rem 1rem;
  }
  .geo-app-header-inner {
    flex: 1;
    min-width: min(100%, 36rem);
  }
  .geo-app-header h1 {
    font-size: 1.5rem;
    font-weight: 600;
    margin: 0;
    letter-spacing: -0.02em;
    color: #fff !important;
  }
  .geo-app-header-desc {
    margin: 0.4rem 0 0 0;
    font-size: 0.875rem;
    font-weight: 400;
    line-height: 1.5;
    color: rgba(255, 255, 255, 0.88) !important;
    max-width: 52rem;
  }
  .geo-app-header-desc strong {
    color: #fff !important;
    font-weight: 600;
  }
  .geo-app-header-actions {
    display: flex;
    gap: 0.75rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .geo-app-header .geo-header-link-btn {
    background: rgba(255, 255, 255, 0.1);
    color: #fff !important;
    border: 1.5px solid rgba(255, 255, 255, 0.3);
    padding: 0.5rem 1rem;
    border-radius: 8px;
    font-size: 0.9375rem;
    font-weight: 500;
    text-decoration: none;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  .geo-app-header .geo-header-link-btn:hover {
    background: rgba(255, 255, 255, 0.2);
    border-color: rgba(255, 255, 255, 0.5);
  }
  /* Sidebar rail: perception .sidebar + .analysis-nav */
  [data-testid="stSidebar"] {
    background: #f8f9fa !important;
    border-right: 1px solid #e9ecef !important;
  }
  [data-testid="stSidebar"] > div:first-child {
    padding-top: 1.25rem;
    position: sticky;
    top: 0.75rem;
    align-self: flex-start;
    max-height: calc(100vh - 1.5rem);
    overflow-y: auto;
  }
  .geo-nav-section-title {
    font-weight: 600;
    color: #2d2d2d;
    margin: 0 0 1rem 0;
    font-size: 1.05rem;
    text-align: center;
    border-bottom: 2px solid #e9ecef;
    padding-bottom: 0.5rem;
  }
  /* Nav buttons in left rail (perception .nav-item / .nav-item-active) */
  [data-testid="stSidebar"] .stButton > button {
    width: 100%;
    justify-content: flex-start;
    text-align: left;
    border-radius: 8px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
    padding: 0.75rem 0.85rem !important;
    min-height: 2.75rem;
    box-shadow: none !important;
    transition: all 0.2s ease !important;
  }
  [data-testid="stSidebar"] .stButton > button[kind="secondary"] {
    background: transparent !important;
    color: #2d2d2d !important;
    border: 1px solid transparent !important;
  }
  [data-testid="stSidebar"] .stButton > button[kind="secondary"]:hover {
    background: rgba(45, 45, 45, 0.06) !important;
    border-color: rgba(45, 45, 45, 0.12) !important;
  }
  [data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: #2d2d2d !important;
    color: #fff !important;
    border: 1px solid #2d2d2d !important;
    box-shadow: 0 2px 8px rgba(45, 45, 45, 0.2) !important;
    -webkit-text-fill-color: #fff !important;
  }
  [data-testid="stSidebar"] .stButton > button[kind="primary"] p,
  [data-testid="stSidebar"] .stButton > button[kind="primary"] span,
  [data-testid="stSidebar"] .stButton > button[kind="primary"] div,
  [data-testid="stSidebar"] .stButton > button[kind="primary"] * {
    color: #fff !important;
    -webkit-text-fill-color: #fff !important;
  }
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover,
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:focus {
    color: #fff !important;
    -webkit-text-fill-color: #fff !important;
  }
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover p,
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover span,
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover div,
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:focus p,
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:focus span,
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:focus div {
    color: #fff !important;
    -webkit-text-fill-color: #fff !important;
  }
  [data-testid="stSidebar"] .stButton > button:disabled {
    opacity: 0.45;
  }
  [data-testid="stSidebar"] [data-testid="stVerticalBorderBlockContainer"] {
    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.07) !important;
    border-radius: 12px !important;
    border-color: #e9ecef !important;
  }
  /* Main workspace surface (perception .dashboard-area) */
  section.main > div.block-container {
    padding-top: 1rem;
    padding-bottom: 0.75rem;
    max-width: 100%;
  }
  section.main .stAppViewBlockContainer {
    background: #f5f5f5;
  }
  /* Sign-in card (classes used in HTML from ``_render_sso_signin_screen``) */
  .geo-signin-card {
    max-width: 640px;
    margin: 0 auto 1.5rem auto;
    padding: 2.5rem 2.25rem;
    background: #fff;
    border-radius: 16px;
    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.28);
    text-align: center;
    border: 1px solid rgba(255, 255, 255, 0.35);
  }
  .geo-signin-icon {
    width: 4.5rem;
    height: 4.5rem;
    margin: 0 auto 1.25rem auto;
    border-radius: 16px;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    color: #fff;
    font-size: 2rem;
    line-height: 4.5rem;
    font-weight: 600;
  }
  .geo-signin-card h2 {
    font-size: 1.85rem;
    font-weight: 700;
    color: #1e293b !important;
    margin: 0 0 0.5rem 0;
    letter-spacing: -0.03em;
  }
  .geo-signin-lead {
    font-size: 1.05rem;
    color: #64748b !important;
    line-height: 1.6;
    margin: 0 0 1.25rem 0;
  }
  .geo-signin-bullets {
    text-align: left;
    margin: 0 0 1.25rem 0;
    padding: 0 0 0 1.25rem;
    color: #475569 !important;
    font-size: 0.95rem;
    line-height: 1.65;
  }
  .geo-signin-bullets li {
    margin-bottom: 0.35rem;
  }
  .geo-signin-foot {
    font-size: 0.82rem;
    color: #94a3b8 !important;
    line-height: 1.5;
    margin: 0;
  }
  .geo-signin-btn-row {
    max-width: 640px;
    margin: 0 auto;
  }
</style>
"""


def _is_under_audit_output(p: Path, out_base: str = DEFAULT_OUT_BASE) -> bool:
    try:
        p.resolve().relative_to((REPO_ROOT / out_base).resolve())
        return True
    except ValueError:
        return False


def find_latest_primary_audit(out_root: Path | None = None) -> Path | None:
    """Newest primary crawl folder under audit_output/ (by audit_summary.json mtime)."""
    root = (out_root or (REPO_ROOT / DEFAULT_OUT_BASE)).resolve()
    if not root.is_dir():
        return None
    best: Path | None = None
    best_mtime = 0.0
    for d in root.iterdir():
        if not d.is_dir():
            continue
        summ = d / "audit_summary.json"
        if not summ.is_file():
            continue
        try:
            data = json.loads(summ.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("audit_label") != "primary":
            continue
        try:
            m = summ.stat().st_mtime
        except OSError:
            continue
        if m > best_mtime:
            best_mtime = m
            best = d.resolve()
    return best


def list_primary_audit_dirs(out_root: Path | None = None) -> list[tuple[Path, str, float]]:
    """Primary crawl folders under ``audit_output/``, newest ``audit_summary.json`` first."""
    root = (out_root or (REPO_ROOT / DEFAULT_OUT_BASE)).resolve()
    if not root.is_dir():
        return []
    rows: list[tuple[Path, str, float]] = []
    for d in root.iterdir():
        if not d.is_dir():
            continue
        summ = d / "audit_summary.json"
        if not summ.is_file():
            continue
        try:
            data = json.loads(summ.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("audit_label") != "primary":
            continue
        try:
            m = summ.stat().st_mtime
        except OSError:
            continue
        base = str(data.get("base_url") or d.name).strip() or d.name
        rows.append((d.resolve(), base, m))
    rows.sort(key=lambda r: r[2], reverse=True)
    return rows


def _open_audit_in_report_view(
    cr: Any,
    audit_path: Path,
    *,
    archive_competitors: list[str] | None = None,
    follow_latest: bool | None = None,
) -> None:
    """Load a primary audit folder into session and switch to the report view."""
    if not (audit_path / "audit_summary.json").is_file():
        st.error(f"No audit_summary.json in {audit_path}")
        return
    try:
        apply_audit_to_session(audit_path, cr, archive_competitors or [])
    except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
        st.error(str(e))
        return
    st.session_state.pop("onboarding_step", None)
    st.session_state["landing_step"] = 1
    st.session_state["ui_view"] = "report"
    if follow_latest is not None:
        st.session_state["follow_latest_audit"] = follow_latest
    st.rerun()


def _load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@st.cache_resource
def get_create_report():
    return _load_module("geo_create_report", BACKEND_ROOT / "create-report.py")


@st.cache_resource
def get_crawl_site():
    return _load_module("geo_crawl_site", BACKEND_ROOT / "crawl-site.py")


def site_key_from_url(url: str) -> str:
    cr = get_crawl_site()
    base = cr.normalize_base(url.strip())
    return urllib.parse.urlparse(base + "/").netloc.lower()


def audit_dir_for_run(out_base: str, primary_url: str) -> Path:
    cr = get_crawl_site()
    base = cr.normalize_base(primary_url.strip())
    return (REPO_ROOT / out_base / cr.safe_dir_name(base)).resolve()


def _out_base_from_audit_dir(audit_dir: Path) -> str:
    try:
        rel = audit_dir.resolve().relative_to(REPO_ROOT)
        if rel.parts:
            return rel.parts[0]
    except ValueError:
        pass
    return DEFAULT_OUT_BASE


def load_audit_summary(audit_dir: Path) -> dict[str, Any]:
    p = audit_dir / "audit_summary.json"
    if not p.is_file():
        raise FileNotFoundError(p)
    return json.loads(p.read_text(encoding="utf-8", errors="replace"))


def load_archive() -> dict[str, Any]:
    if not ARCHIVE_PATH.is_file():
        return {"runs": []}
    return json.loads(ARCHIVE_PATH.read_text(encoding="utf-8", errors="replace"))


def save_archive(data: dict[str, Any]) -> None:
    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    rel = str(audit_dir.resolve().relative_to(REPO_ROOT))
    sk = site_key_from_url(primary_url)
    entry: dict[str, Any] = {
        "id": datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "_" + sk.replace(".", "_"),
        "primary_url": get_crawl_site().normalize_base(primary_url.strip()),
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
    data.setdefault("runs", []).append(entry)
    save_archive(data)


def runs_for_site(site_key: str) -> list[dict[str, Any]]:
    data = load_archive()
    runs = [r for r in data.get("runs", []) if r.get("site_key") == site_key]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return runs


def current_user_email() -> str | None:
    """Google / OIDC email when Streamlit auth is enabled and the user is signed in."""
    user = getattr(st, "user", None)
    if user is None:
        return None
    try:
        if not getattr(user, "is_logged_in", False):
            return None
    except Exception:
        return None
    email = getattr(user, "email", None)
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    return None


def runs_for_user(owner_email: str) -> list[dict[str, Any]]:
    want = owner_email.strip().lower()
    data = load_archive()
    runs = [
        r
        for r in data.get("runs", [])
        if (r.get("owner_email") or "").strip().lower() == want
    ]
    runs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return runs


def resolve_audit_dir(rel_or_abs: str) -> Path:
    p = Path(rel_or_abs)
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    return p


def _secrets_section(name: str) -> dict[str, Any]:
    try:
        sec = st.secrets[name]
    except (FileNotFoundError, KeyError, TypeError):
        return {}
    # Streamlit wraps TOML tables in AttrDict (Mapping, not dict).
    if isinstance(sec, Mapping):
        return dict(sec)
    return {}


def _qp_first(name: str) -> str | None:
    try:
        v = st.query_params.get(name)
    except Exception:
        return None
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return str(v[0]) if v else None
    return str(v)


def _ga4_oauth_sign_state(
    secret: str,
    *,
    return_view: str = "new_audit",
    onboarding_step: int | None = None,
    wiz_ga4_after_yes: bool = False,
    ttl_sec: int = 900,
) -> str:
    """
    Signed OAuth ``state`` so the GA4 callback still validates after Google redirects.
    Optionally embeds ``onboarding_step`` and wizard GA4 phase so the UI returns to the right screen.
    """
    payload: dict[str, Any] = {"e": int(time.time()) + ttl_sec, "v": str(return_view)}
    if onboarding_step is not None:
        payload["s"] = int(onboarding_step)
    if wiz_ga4_after_yes:
        payload["w"] = 1
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"g4.{body}.{sig}"


def _ga4_parse_signed_oauth_state(state: str, secret: str) -> dict[str, Any] | None:
    """Return decoded payload if signature and expiry are valid; else ``None``."""
    if not state.startswith("g4."):
        return None
    try:
        _, body, sig = state.split(".", 2)
        expect = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expect, sig):
            return None
        pad = "=" * (-len(body) % 4)
        data = json.loads(base64.urlsafe_b64decode((body + pad).encode("ascii")))
        if int(data.get("e", 0)) < int(time.time()):
            return None
        if data.get("v") != "new_audit":
            return None
        return data
    except Exception:
        return None


def _ga4_oauth_verify_signed_state(state: str, secret: str) -> bool:
    return _ga4_parse_signed_oauth_state(state, secret) is not None


def _ga4_oauth_callback_state_valid(state: str) -> bool:
    auth = _secrets_section("auth")
    cookie_secret = (auth.get("cookie_secret") or "").strip()
    if cookie_secret and state.startswith("g4."):
        return _ga4_parse_signed_oauth_state(state, cookie_secret) is not None
    expected = st.session_state.get("ga4_oauth_state")
    return bool(expected) and py_secrets.compare_digest(str(state), str(expected))


def ga4_oauth_client_config() -> tuple[str, str, str] | None:
    """
    Return (client_id, client_secret, redirect_uri) for GA4 user OAuth.

    Prefer ``[ga4_oauth]`` in ``.streamlit/secrets.toml``; else reuse ``[auth]`` web client
    with ``redirect_uri`` from ``[ga4_oauth]``, ``GA4_OAUTH_REDIRECT_URI``, or
    ``DEFAULT_GA4_OAUTH_REDIRECT_URI`` (see ``STREAMLIT_PUBLIC_ORIGIN`` / ngrok defaults).
    """
    g = _secrets_section("ga4_oauth")
    cid = (g.get("client_id") or os.environ.get("GA4_OAUTH_CLIENT_ID") or "").strip()
    csec = (g.get("client_secret") or os.environ.get("GA4_OAUTH_CLIENT_SECRET") or "").strip()
    ruri = (g.get("redirect_uri") or os.environ.get("GA4_OAUTH_REDIRECT_URI") or "").strip()
    if cid and csec and ruri:
        # Must match Google Cloud "Authorized redirect URIs" exactly (including trailing slash).
        return cid, csec, ruri

    auth = _secrets_section("auth")
    cid2 = (auth.get("client_id") or "").strip()
    csec2 = (auth.get("client_secret") or "").strip()
    ruri2 = (
        (g.get("redirect_uri") or os.environ.get("GA4_OAUTH_REDIRECT_URI") or "").strip()
        or DEFAULT_GA4_OAUTH_REDIRECT_URI
    )
    if cid2 and csec2:
        return cid2, csec2, ruri2.strip()
    return None


def ga4_oauth_try_callback() -> None:
    """If the URL contains OAuth ``code`` + ``state``, exchange and store GA4 credentials."""
    cfg = ga4_oauth_client_config()
    if not cfg:
        return
    cid, csec, ruri = cfg
    code = _qp_first("code")
    state = _qp_first("state")
    if not code or not state:
        return
    if not _ga4_oauth_callback_state_valid(state):
        return
    try:
        import ga4_oauth as g4o

        creds = g4o.exchange_code(cid, csec, ruri, code)
        st.session_state["ga4_user_creds_dict"] = g4o.credentials_to_dict(creds)
        st.session_state.pop("ga4_oauth_state", None)
        st.session_state.pop("ga4_property_options", None)
        auth_sec = _secrets_section("auth")
        cookie_secret = (auth_sec.get("cookie_secret") or "").strip()
        payload = (
            _ga4_parse_signed_oauth_state(state, cookie_secret)
            if (cookie_secret and str(state).startswith("g4."))
            else None
        )
        if payload is not None:
            if "s" in payload:
                st.session_state["onboarding_step"] = int(payload["s"])
            if payload.get("w"):
                st.session_state["wiz_ga4_after_yes"] = True
                st.session_state["setup_want_ga4"] = True
                st.session_state["setup_ga4_choice"] = "yes"
        for k in ("code", "state", "scope"):
            try:
                if k in st.query_params:
                    del st.query_params[k]
            except Exception:
                pass
        st.session_state["ui_view"] = "new_audit"
        st.session_state["ga4_oauth_just_connected"] = True
        st.rerun()
    except Exception as e:
        st.session_state["ga4_oauth_error"] = str(e)
        try:
            if "code" in st.query_params:
                del st.query_params["code"]
        except Exception:
            pass


_GA4_ONBOARDING_KEYS: tuple[str, ...] = (
    "ga4_top_pages",
    "ga4_primary_market",
    "onboarding_suggestions",
    "accepted_categories",
    "accepted_products",
    "accepted_competitors",
    "suggested_brand_name",
    "suggested_brand_website",
    "suggested_industry_hint",
    "suggestions_loaded",
)


def _sync_na_competitors_from_multiselect() -> None:
    sel = list(st.session_state.get("accepted_competitors") or [])
    for i in range(MAX_COMPETITORS):
        st.session_state[f"na_c{i + 1}"] = sel[i] if i < len(sel) else ""


def _session_primary_market() -> tuple[str, str]:
    """Effective primary market for AI prompts: **manual wizard** → **GA4** → **env** (``GEO_PRIMARY_MARKET_*``)."""
    from geo_market import default_primary_market_from_env, resolve_primary_market

    oc = str(st.session_state.get("geo_market_country") or "").strip()
    oid = str(st.session_state.get("geo_market_country_code") or "").strip()
    if oc or oid:
        return resolve_primary_market(oc, oid)
    m = st.session_state.get("ga4_primary_market")
    if isinstance(m, dict):
        gc = str(m.get("country") or "").strip()
        gid = str(m.get("country_id") or "").strip()
        if gc or gid:
            return resolve_primary_market(gc, gid)
    return default_primary_market_from_env()


def _competitors_detail_from_wizard_session() -> list[dict[str, Any]]:
    """Structured competitor rows for JSON export; falls back to ``na_c*`` URL fields."""
    det = st.session_state.get("geo_competitors_detail")
    if isinstance(det, list) and det:
        return [x for x in det if isinstance(x, dict)]
    out: list[dict[str, Any]] = []
    for i in range(1, MAX_COMPETITORS + 1):
        u = str(st.session_state.get(f"na_c{i}") or "").strip()
        if not u:
            continue
        raw = u if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u) else "https://" + u
        try:
            p = urllib.parse.urlparse(raw)
            host = (p.netloc or "").lower().replace("www.", "") or u
        except Exception:
            host = u
        brand = host.split(".")[0].replace("-", " ").title() if host else f"Competitor {i}"
        out.append({"competitor_brand": brand, "competitor_website": u})
    return out


def _apply_competitors_detail_to_session(rows: list[dict[str, Any]]) -> None:
    """Sync ``na_c*``, ``accepted_competitors``, and ``pp_comp`` from structured rows."""
    st.session_state["geo_competitors_detail"] = [r for r in rows if isinstance(r, dict)]
    urls: list[str] = []
    for r in rows[:MAX_COMPETITORS]:
        if not isinstance(r, dict):
            continue
        u = str(r.get("competitor_website") or "").strip()
        if u:
            urls.append(u)
    for j in range(MAX_COMPETITORS):
        st.session_state[f"na_c{j + 1}"] = urls[j] if j < len(urls) else ""
    st.session_state["accepted_competitors"] = list(urls)
    st.session_state["pp_comp"] = "\n".join(urls)


def _fallback_brand_label_from_url(url: str) -> str:
    """Short display label from homepage URL (hostname segment, title-cased)."""
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", u):
        u = "https://" + u
    try:
        host = (urllib.parse.urlparse(u).hostname or "").lower().replace("www.", "")
    except Exception:
        return ""
    if not host:
        return ""
    return host.split(".")[0].replace("-", " ").strip().title() or host


def _wiz_comp_checkbox_key(normalized_url: str) -> str:
    """Stable Streamlit checkbox key per competitor URL (survives appends / reorder)."""
    u = (normalized_url or "").strip().lower()
    h = hashlib.sha256(u.encode("utf-8")).hexdigest()[:16]
    return f"wiz_comp_inc__{h}"


def _wiz_write_na_slots_from_urls(urls: list[str]) -> None:
    """Fill ``na_c*`` slots and mirror ``accepted_competitors`` / ``pp_comp`` (no widgets may use those keys this run)."""
    for j in range(MAX_COMPETITORS):
        st.session_state[f"na_c{j + 1}"] = urls[j] if j < len(urls) else ""
    st.session_state["accepted_competitors"] = list(urls)
    st.session_state["pp_comp"] = "\n".join(urls)


def _wiz_add_competitor_on_click() -> None:
    """Append one competitor from ``wiz_comp_manual_url`` (runs in button callback before widgets bind)."""
    from geo_setup_llm import normalize_competitor_url

    raw = str(st.session_state.get("wiz_comp_manual_url") or "").strip()
    nu = normalize_competitor_url(raw) if raw else ""
    if not nu:
        st.session_state["wiz_add_comp_msg"] = (
            "error",
            "Enter a valid URL in the field above, or pick a site from **Look up popular sites**.",
        )
        return
    det_cur = [x for x in (st.session_state.get("geo_competitors_detail") or []) if isinstance(x, dict)]
    exist = {normalize_competitor_url(str(x.get("competitor_website") or "")) for x in det_cur}
    exist.discard("")
    if nu in exist:
        st.session_state["wiz_add_comp_msg"] = ("error", "That site is already in the table.")
        return
    n_rows = sum(1 for x in det_cur if normalize_competitor_url(str(x.get("competitor_website") or "")))
    if n_rows >= MAX_COMPETITORS:
        st.session_state["wiz_add_comp_msg"] = (
            "error",
            f"You can list at most **{MAX_COMPETITORS}** competitor rows. Run **Suggest competitors with Gemini** again to replace with a fresh batch (up to {WIZARD_GEMINI_COMP_INITIAL}), use **Suggest more**, or start a **New audit**.",
        )
        return
    brand = _fallback_brand_label_from_url(nu)
    det_cur.append({"competitor_brand": brand, "competitor_website": nu})
    st.session_state["geo_competitors_detail"] = det_cur
    st.session_state[_wiz_comp_checkbox_key(nu)] = True
    st.session_state["wiz_comp_manual_url"] = ""
    st.session_state.pop("wiz_add_comp_msg", None)


_PP_SCORE_GREEN = "#00b894"
_PP_SCORE_BLUE = "#0984e3"


def _wizard_brand_for_probes() -> str:
    return str(st.session_state.get("na_brand") or st.session_state.get("pp_brand") or "").strip()


def _wizard_site_for_probes() -> str:
    return str(st.session_state.get("na_site") or st.session_state.get("pp_site") or "").strip()


def _pp_html_metric_card(*, tone: str, label: str, value: str, sub: str = "") -> str:
    top = _PP_SCORE_GREEN if tone == "green" else _PP_SCORE_BLUE
    sub_html = (
        f'<div style="font-size:13px;color:#5c5c5c;margin-top:6px;">{html.escape(sub)}</div>' if sub else ""
    )
    return (
        '<div style="position:relative;background:#fff;border:1px solid #e8e8e8;border-radius:6px;'
        'padding:20px 22px;overflow:hidden;">'
        f'<div style="position:absolute;top:0;left:0;right:0;height:3px;background:{top};"></div>'
        f'<div style="font-size:11px;font-weight:600;color:#737373;text-transform:uppercase;'
        f'letter-spacing:0.04em;margin-bottom:8px;">{html.escape(label)}</div>'
        f'<div style="font-size:28px;font-weight:800;color:{top};line-height:1.1;">{value}</div>'
        f"{sub_html}</div>"
    )


def _pp_html_sov_bar(brand_pct: float, comp_pct: float) -> str:
    b = max(0.0, min(100.0, float(brand_pct)))
    c = max(0.0, min(100.0, float(comp_pct)))
    t = b + c + 1e-9
    bw = 100.0 * b / t
    return (
        f'<div style="display:flex;border-radius:8px;overflow:hidden;height:14px;border:1px solid #e8e8e8;'
        f'margin-top:10px;" title="Brand vs competitor mention share (substring hits in replies).">'
        f'<div style="width:{bw:.2f}%;min-width:2px;background:{_PP_SCORE_GREEN};"></div>'
        f'<div style="flex:1;background:{_PP_SCORE_BLUE};"></div></div>'
        f'<div style="display:flex;justify-content:space-between;font-size:12px;color:#737373;margin-top:6px;">'
        f"<span>Brand {b:.1f}%</span><span>Competitors {c:.1f}%</span></div>"
    )


def _ensure_pp_cmp_widget_keys_seeded() -> None:
    for i in range(MAX_COMPETITORS):
        ku, kb = f"pp_cmp_u{i}", f"pp_cmp_b{i}"
        if ku not in st.session_state:
            det = [x for x in (st.session_state.get("geo_competitors_detail") or []) if isinstance(x, dict)]
            r = det[i] if i < len(det) else None
            if isinstance(r, dict):
                st.session_state[ku] = str(r.get("competitor_website") or "")
                st.session_state[kb] = str(r.get("competitor_brand") or "")
            else:
                st.session_state[ku] = str(st.session_state.get(f"na_c{i + 1}") or "")
                st.session_state[kb] = ""
        elif kb not in st.session_state:
            st.session_state[kb] = ""


def _flush_pp_cmp_widgets_to_geo_detail() -> None:
    rows: list[dict[str, Any]] = []
    for i in range(MAX_COMPETITORS):
        ku, kb = f"pp_cmp_u{i}", f"pp_cmp_b{i}"
        u = str(st.session_state.get(ku) or "").strip()
        if not u:
            continue
        b = str(st.session_state.get(kb) or "").strip()
        rows.append(
            {
                "competitor_website": u,
                "competitor_brand": b or _fallback_brand_label_from_url(u),
            }
        )
    _apply_competitors_detail_to_session(rows)


def _pp_competitor_url_brand_lists() -> tuple[list[str], list[str]]:
    det = [x for x in (st.session_state.get("geo_competitors_detail") or []) if isinstance(x, dict)]
    urls: list[str] = []
    brands: list[str] = []
    for d in det[:MAX_COMPETITORS]:
        u = str(d.get("competitor_website") or "").strip()
        if not u:
            continue
        urls.append(u)
        brands.append(str(d.get("competitor_brand") or "").strip())
    return urls, brands


def _pp_report_reveal_sov_on_click() -> None:
    st.session_state["pp_report_show_sov_analysis"] = True


def _pp_track_competitor_from_sov(url: str, brand: str) -> None:
    """Persist a SOV-detected peer into session + onboarding files (used by competitor crawl / report)."""
    from geo_setup_llm import normalize_competitor_url

    nu = normalize_competitor_url(str(url or "").strip())
    if not nu:
        return
    bn = (str(brand or "").strip() or _fallback_brand_label_from_url(nu)).strip()
    det = [x for x in (st.session_state.get("geo_competitors_detail") or []) if isinstance(x, dict)]
    exist = {normalize_competitor_url(str(x.get("competitor_website") or "")) for x in det}
    exist.discard("")
    if nu in exist:
        return
    if len([x for x in det if normalize_competitor_url(str(x.get("competitor_website") or ""))]) >= MAX_COMPETITORS:
        return
    det.append({"competitor_brand": bn, "competitor_website": nu})
    st.session_state["geo_competitors_detail"] = det
    _apply_competitors_detail_to_session(det[:MAX_COMPETITORS])
    ad = st.session_state.get("audit_dir")
    if isinstance(ad, Path):
        write_onboarding_context(ad)


def _render_reply_detected_competitors_track_table(live: dict[str, Any]) -> None:
    """Favicon + brand + URL + **Track competitor** for Gemini-detected peers (report SOV section)."""
    from geo_setup_llm import normalize_competitor_url

    rd = live.get("reply_detected_brands")
    if not isinstance(rd, list) or not rd:
        return
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for x in rd:
        if not isinstance(x, dict):
            continue
        bn = str(x.get("brand_name") or "").strip()
        u = normalize_competitor_url(str(x.get("website_url") or "").strip())
        if not bn and not u:
            continue
        key = (u or bn).lower()
        if key in seen:
            continue
        seen.add(key)
        rows.append({"brand_name": bn or "—", "website_url": u})
    if not rows:
        return
    st.markdown("##### Competitors found from SOV analysis")
    st.caption(
        "Brands **Gemini inferred** from assistant reply excerpts (substring SOV). "
        "**Track competitor** saves the row to this audit’s competitor list and `competitors.json`—use **Competitor comparison** → "
        "**Crawl competitors & refresh report** to update the embedded report tab."
    )
    for i, r in enumerate(rows):
        bn = r["brand_name"]
        u = r["website_url"]
        host = hostname_for_display_url(u) if u else ""
        hkey = hashlib.sha256((u or bn).lower().encode("utf-8")).hexdigest()[:14]
        c0, c1, c2, c3 = st.columns((1.1, 3.0, 5.0, 2.2), gap="small")
        with c0:
            if host:
                st.image(public_site_favicon_url(host), width=28)
        with c1:
            st.markdown(f"**{html.escape(bn)}**", unsafe_allow_html=True)
        with c2:
            if u:
                st.markdown(
                    f'<p style="margin:0;font-size:0.88rem;color:#4b5563;">{html.escape(u)}</p>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("No homepage URL — track disabled until you add a URL in **New audit**.")
        with c3:
            if u:
                st.button(
                    "Track competitor",
                    key=f"pp_sov_trk_{hkey}_{i}",
                    on_click=_pp_track_competitor_from_sov,
                    args=(u, bn),
                )


def _render_pp_brand_and_competitors_panel(*, for_report_tab: bool = False) -> None:
    """Wizard brand (read-only scorecards) + competitor URL/brand rows used for live SoV scan."""
    _ensure_pp_cmp_widget_keys_seeded()
    b = _wizard_brand_for_probes()
    s = _wizard_site_for_probes()
    esc_b = html.escape(b or "—")
    esc_s = html.escape(s or "—")
    st.markdown(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:8px;">'
        + _pp_html_metric_card(
            tone="green",
            label="Your brand (wizard)",
            value=esc_b,
            sub="Used for live probes and mention highlighting",
        )
        + _pp_html_metric_card(
            tone="blue",
            label="Your website",
            value=esc_s,
            sub="Hostname counts toward brand signal in replies",
        )
        + "</div>",
        unsafe_allow_html=True,
    )
    if for_report_tab:
        st.caption(
            "Competitor URLs are not edited here. After **live probes**, use **Show SOV analysis** to see peers inferred "
            "from replies, then **Track competitor** to save them to this audit."
        )
        return
    st.markdown("##### Competitors for this scan")
    st.caption(
        "Confirm each **website** and **brand name** (from setup or add below). Both are used to detect competitor mentions."
    )
    h0, h1, h2 = st.columns((1, 3, 3))
    with h0:
        st.caption("**#**")
    with h1:
        st.caption("**Website**")
    with h2:
        st.caption("**Brand name**")
    for i in range(MAX_COMPETITORS):
        c0, c1, c2 = st.columns((1, 3, 3))
        with c0:
            st.markdown(f"`{i + 1}`")
        with c1:
            render_url_searchbox(
                session_key=f"pp_cmp_u{i}",
                label="Competitor URL",
                help="Search or paste a competitor homepage URL.",
                placeholder="Type to search…",
            )
        with c2:
            st.text_input("Competitor brand", key=f"pp_cmp_b{i}", label_visibility="collapsed")
    _flush_pp_cmp_widgets_to_geo_detail()


def write_onboarding_context(audit_dir: Path) -> None:
    """Persist GA4-derived onboarding choices next to the audit for future report use."""
    ga4_ok = bool(st.session_state.get("_ga4_onboarding_pull_committed"))
    tp_raw = st.session_state.get("ga4_top_pages") or []
    tp = tp_raw if ga4_ok and isinstance(tp_raw, list) else []
    if isinstance(tp, list) and len(tp) > 100:
        tp = tp[:100]
    cats = list(st.session_state.get("accepted_categories") or [])
    pp = list(st.session_state.get("pp_prompts") or [])
    comp_det = _competitors_detail_from_wizard_session()
    site_u = (_wizard_brand_website_url() or str(st.session_state.get("na_site") or "").strip()).strip()
    pss_rows = st.session_state.get("geo_pss_rows")
    payload: dict[str, Any] = {
        "brand_name_used": st.session_state.get("na_brand"),
        "brand_website_used": _wizard_brand_website_url() or st.session_state.get("na_site"),
        "industry_used": _effective_na_industry(),
        "ga4_suggested_brand_name": st.session_state.get("suggested_brand_name"),
        "ga4_suggested_site_url": st.session_state.get("suggested_brand_website"),
        "ga4_suggested_industry": st.session_state.get("suggested_industry_hint"),
        "accepted_categories": list(st.session_state.get("accepted_categories") or []),
        "accepted_products": list(st.session_state.get("accepted_products") or []),
        "accepted_competitors": list(st.session_state.get("accepted_competitors") or []),
        "ga4_onboarding_pull": ga4_ok,
        "ga4_top_pages": tp,
        "suggested_prompts": pp,
        "product_service_prompts": pp,
        "products_and_services": cats,
        "competitors_detail": comp_det,
        "products_and_services_rows": pss_rows if isinstance(pss_rows, list) else [],
        "prompt_category_labels": list(st.session_state.get("pp_last_cat_labels") or []),
        "ga4_primary_market": (st.session_state.get("ga4_primary_market") if ga4_ok else None),
        "geo_market_country": str(st.session_state.get("geo_market_country") or "").strip(),
        "geo_market_country_code": str(st.session_state.get("geo_market_country_code") or "").strip(),
    }
    try:
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / "onboarding_context.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        pss_file: dict[str, Any] = {"website_url": site_u, "products_and_services": cats}
        if isinstance(pss_rows, list) and pss_rows:
            pss_file["rows"] = pss_rows
        (audit_dir / "products_and_services.json").write_text(
            json.dumps(pss_file, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (audit_dir / "competitors.json").write_text(
            json.dumps({"website_url": site_u, "competitors": comp_det}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def load_onboarding_context_to_session(audit_dir: Path) -> None:
    """Restore onboarding + prompt-performance inputs from disk when opening a report."""
    _clear_stale_ga4_session_keys()
    merged: dict[str, Any] = {}
    path = audit_dir / "onboarding_context.json"
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                merged = dict(raw)
        except (OSError, json.JSONDecodeError):
            pass
    pjp = audit_dir / "products_and_services.json"
    if pjp.is_file():
        try:
            pj = json.loads(pjp.read_text(encoding="utf-8"))
            if isinstance(pj, dict):
                psv = pj.get("products_and_services")
                if isinstance(psv, list) and psv:
                    merged.setdefault(
                        "products_and_services",
                        [str(x).strip() for x in psv if str(x).strip()],
                    )
                rows = pj.get("rows")
                if isinstance(rows, list) and rows:
                    merged.setdefault("products_and_services_rows", rows)
        except (OSError, json.JSONDecodeError):
            pass
    cjp = audit_dir / "competitors.json"
    if cjp.is_file():
        try:
            cj = json.loads(cjp.read_text(encoding="utf-8"))
            if isinstance(cj, dict):
                cd = cj.get("competitors")
                if isinstance(cd, list) and cd:
                    merged.setdefault("competitors_detail", [x for x in cd if isinstance(x, dict)])
        except (OSError, json.JSONDecodeError):
            pass
    if not merged:
        return
    data = merged
    if data.get("brand_name_used"):
        bn = str(data["brand_name_used"])
        st.session_state["na_brand"] = bn
        st.session_state["pp_brand"] = bn
    if data.get("brand_website_used"):
        su = str(data["brand_website_used"])
        st.session_state["na_site"] = su
        st.session_state["pp_site"] = su
    if not str(st.session_state.get("pp_brand") or "").strip():
        bw = str(st.session_state.get("pp_site") or data.get("brand_website_used") or "").strip()
        if bw:
            g = _fallback_brand_label_from_url(bw)
            if g:
                st.session_state["pp_brand"] = g
    cdet = data.get("competitors_detail")
    if isinstance(cdet, list) and cdet:
        clean_d = [x for x in cdet if isinstance(x, dict)]
        _apply_competitors_detail_to_session(clean_d)
        try:
            from geo_setup_llm import normalize_competitor_url

            st.session_state["wiz_comp_urls_sel"] = [
                normalize_competitor_url(str(d.get("competitor_website") or ""))
                for d in clean_d
                if str(d.get("competitor_website") or "").strip()
            ][:MAX_COMPETITORS]
        except Exception:
            pass
    else:
        comps = data.get("accepted_competitors")
        if isinstance(comps, list) and comps:
            urls2 = [str(c).strip() for c in comps if str(c).strip()][:MAX_COMPETITORS]
            st.session_state["pp_comp"] = "\n".join(urls2)
            st.session_state["accepted_competitors"] = list(urls2)
            for j in range(MAX_COMPETITORS):
                st.session_state[f"na_c{j + 1}"] = urls2[j] if j < len(urls2) else ""
    psp = data.get("product_service_prompts")
    sp = data.get("suggested_prompts")
    plist: list[str] = []
    if isinstance(psp, list) and psp:
        plist = [str(p).strip() for p in psp if str(p).strip()]
    elif isinstance(sp, list) and sp:
        plist = [str(p).strip() for p in sp if str(p).strip()]
    if plist:
        st.session_state["pp_prompts"] = plist
    pcl = data.get("prompt_category_labels")
    if isinstance(pcl, list) and pcl:
        st.session_state["pp_last_cat_labels"] = [str(x).strip() for x in pcl if str(x).strip()]
    base_for_ga4 = str(data.get("brand_website_used") or "").strip()
    audit_obj = st.session_state.get("audit")
    if isinstance(audit_obj, dict):
        base_for_ga4 = base_for_ga4 or str(audit_obj.get("base_url") or "").strip()
    tp = data.get("ga4_top_pages")
    ga4_pull = data.get("ga4_onboarding_pull") is True
    allow_ga4 = ga4_pull or (
        isinstance(tp, list)
        and bool(tp)
        and bool(base_for_ga4)
        and _ga4_top_pages_match_audit_site(tp, base_for_ga4)
    )
    if allow_ga4 and isinstance(tp, list) and tp:
        st.session_state["ga4_top_pages"] = tp
    pm = data.get("ga4_primary_market")
    if allow_ga4 and isinstance(pm, dict) and (str(pm.get("country") or "").strip() or str(pm.get("country_id") or "").strip()):
        st.session_state["ga4_primary_market"] = pm
    else:
        st.session_state.pop("ga4_primary_market", None)
    if "geo_market_country" in data:
        st.session_state["geo_market_country"] = str(data.get("geo_market_country") or "")
    if "geo_market_country_code" in data:
        st.session_state["geo_market_country_code"] = str(data.get("geo_market_country_code") or "")
    pss_names = data.get("products_and_services")
    if isinstance(pss_names, list) and pss_names:
        st.session_state["accepted_categories"] = [str(x).strip() for x in pss_names if str(x).strip()]
    elif data.get("accepted_categories"):
        st.session_state["accepted_categories"] = list(data["accepted_categories"])
    if data.get("accepted_products"):
        st.session_state["accepted_products"] = list(data["accepted_products"])
    pss_rows = data.get("products_and_services_rows")
    if isinstance(pss_rows, list) and pss_rows:
        st.session_state["geo_pss_rows"] = pss_rows
    for _i in range(MAX_COMPETITORS):
        st.session_state.pop(f"pp_cmp_u{_i}", None)
        st.session_state.pop(f"pp_cmp_b{_i}", None)


def render_ga4_onboarding_suggestions(
    _industries: list[str],
    *,
    sections: frozenset[str] | None = None,
) -> None:
    """Review / trim GA4 + Gemini suggestions before running the audit."""
    suggestions = st.session_state.get("onboarding_suggestions")
    if not suggestions:
        return

    want = sections or frozenset({"categories", "products", "competitors"})

    st.markdown("### Suggested setup from GA4")
    st.caption(
        "Based on the **top 100 pages by pageviews** in the **last 90 days**, plus **Gemini** for competitor sites. "
        "Remove anything that is not accurate; competitor picks sync to the audit form (up to five)."
    )
    pm = st.session_state.get("ga4_primary_market")
    if isinstance(pm, dict) and (pm.get("country") or pm.get("country_id")):
        cc = str(pm.get("country") or "—")
        cid = str(pm.get("country_id") or "").strip()
        nu = pm.get("active_users")
        try:
            nu_i = int(nu) if nu is not None else None
        except (TypeError, ValueError):
            nu_i = None
        extra = f" (`{cid}`)" if cid else ""
        cnt = f"{nu_i:,} active users" if nu_i is not None else "active users (n/a)"
        st.info(
            f"**Primary market (GA4, last 90 days):** {cc}{extra} — {cnt}. "
            "AI prompts use the **effective** market (step 3 override → this GA4 row → env)—see Prompt performance."
        )

    hint = (st.session_state.get("suggested_industry_hint") or "").strip()
    if hint and hint in _industries:
        cur_ind = str(st.session_state.get("na_industry") or "")
        if cur_ind != hint:
            if st.button(f"Use suggested industry: {hint}", key="ga4_apply_industry_btn"):
                st.session_state["na_industry"] = hint
                st.rerun()

    st.caption(
        "**Brand name** and **Brand website** above were prefilled from GA4 when you ran suggestions—adjust them there if needed."
    )

    cats = [c["label"] for c in suggestions.get("categories", [])]
    if "categories" in want:
        if cats:
            if "accepted_categories" in st.session_state:
                prev = st.session_state["accepted_categories"]
                if isinstance(prev, list):
                    st.session_state["accepted_categories"] = [x for x in prev if x in cats] or list(cats)
            else:
                st.session_state["accepted_categories"] = list(cats)
            st.multiselect(
                "Suggested categories / product areas",
                options=cats,
                key="accepted_categories",
                help="Deselect rows that do not reflect your business.",
            )
        else:
            st.caption(
                "No strong category patterns from URL/title heuristics—refine **Brand industry** or enter context manually."
            )

    products = [p["label"] for p in suggestions.get("products", [])]
    if "products" in want:
        if products:
            if "accepted_products" in st.session_state:
                prev_p = st.session_state["accepted_products"]
                if isinstance(prev_p, list):
                    st.session_state["accepted_products"] = [x for x in prev_p if x in products] or list(products)
            else:
                st.session_state["accepted_products"] = list(products)
            st.multiselect(
                "Suggested popular products (from high-traffic product-like URLs)",
                options=products,
                key="accepted_products",
                help="Optional context for your records; stored in onboarding_context.json.",
            )

    comp_opts = suggestions.get("competitors") or []
    comp_urls = [c["url"] for c in comp_opts if isinstance(c, dict) and c.get("url")]
    if "competitors" in want:
        if comp_urls:
            comp_labels = {
                c["url"]: f"{c.get('name', '')} — {c['url']}"
                for c in comp_opts
                if isinstance(c, dict) and c.get("url")
            }
            if "accepted_competitors" in st.session_state:
                prev_c = st.session_state["accepted_competitors"]
                if isinstance(prev_c, list):
                    st.session_state["accepted_competitors"] = [x for x in prev_c if x in comp_urls] or list(
                        comp_urls[:MAX_COMPETITORS]
                    )
            else:
                st.session_state["accepted_competitors"] = list(comp_urls[:MAX_COMPETITORS])
            st.multiselect(
                "Suggested competitors",
                options=comp_urls,
                format_func=lambda u: comp_labels.get(u, str(u)),
                key="accepted_competitors",
                max_selections=MAX_COMPETITORS,
                on_change=_sync_na_competitors_from_multiselect,
                help=f"Up to {MAX_COMPETITORS} URLs for the crawl. Deselect any that are not true peers.",
            )
        else:
            st.caption(
                "No competitor URLs from Gemini (check **GEMINI_API_KEY** / Vertex settings) or use **Search for my competitors** below."
            )

    with st.expander("Top GA4 pages used for suggestions"):
        for p in (suggestions.get("top_pages") or [])[:25]:
            pv = int(p.get("pageviews") or 0)
            st.write(f"{pv:,} views — `{p.get('path')}` — {p.get('title')}")


def render_prompt_performance_page(*, for_report_tab: bool = False) -> None:
    """Likely AI-search prompts plus **live** Gemini + OpenAI probes and mention-based share (report tab or legacy)."""
    ensure_llm_env_from_streamlit_secrets()
    if for_report_tab:
        ad0 = st.session_state.get("audit_dir")
        if isinstance(ad0, Path) and not _audit_dir_has_ga4_traffic_json(ad0):
            st.session_state.pop("ga4_primary_market", None)
            st.session_state.pop("ga4_top_pages", None)
            st.session_state.pop("onboarding_suggestions", None)
    pss_rows = _session_pss_rows_normalized()
    use_pss = bool(pss_rows)
    flat_from_pss, _meta_fp = _flatten_pss_with_meta(pss_rows) if use_pss else ([], [])
    if use_pss and flat_from_pss:
        st.session_state["pp_prompts"] = list(flat_from_pss)

    top_pages: list[Any] = list(st.session_state.get("ga4_top_pages") or [])
    if not top_pages and not for_report_tab and not use_pss:
        st.warning(
            "No **GA4 top pages** in session—type **manual categories** below, or connect GA4 during **New audit** setup."
        )

    if for_report_tab:
        st.markdown("### Prompt performance")
        st.caption(
            "Prompts are grouped by **product or service** from setup (Gemini: five shopper questions per line). "
            "Run **live Gemini + OpenAI probes**, then **Show SOV analysis** for mention share, replies, and inferred competitors. "
            "Share of voice uses **real probe replies** only."
        )
    else:
        st.subheader("Prompt performance")
        st.caption(
            "When your audit used **products & services** from setup, prompts are grouped that way here. "
            "Otherwise generate category-based prompts below, then run probes."
        )
    st.markdown("#### Prompt recommendations")
    if use_pss:
        if for_report_tab:
            st.caption(
                "1. **Live probes** — Each prompt is answered by **Gemini** and **OpenAI**. "
                "After probes finish, open **Show SOV analysis** to see mention-based share and competitors inferred from replies; "
                "you can **Track competitor** to save peers to this audit.  \n"
                "2. **By product or service** — Expand a group after SOV is visible and use **Summary** / **Model**."
            )
        else:
            st.caption(
                "1. **Live probes** — Each prompt is answered by **Gemini** and **OpenAI**; competitor mention % uses substring hits "
                "on your wizard **brand name** + site host vs wizard competitors **plus** brands **Gemini extracts** from reply excerpts "
                "(when that step succeeds).  \n"
                "2. **By product or service** — Expand a group and pick **Summary**, then each prompt’s **Model** dropdown "
                "(header shows brand visibility per model)."
            )
    else:
        if for_report_tab:
            st.caption(
                "1. **Regenerate prompts** (optional) — Gemini can refresh up to **10** queries from setup context.  \n"
                "2. **Live probes** — Same shopper query is answered by **Gemini** and **OpenAI**. "
                "SOV and inferred competitors stay behind **Show SOV analysis** until you choose to open them."
            )
        else:
            st.caption(
                "1. **Generate prompts** — Optional **Gemini** pass for up to **10** queries from **brand**, **industry**, and "
                "**categories** (GA4 / manual).  \n"
                "2. **Live probes** — Each query is run through **Gemini** (`GEMINI_API_KEY` / Vertex) and **OpenAI** "
                "Chat Completions (`OPENAI_API_KEY`, ChatGPT-style behaviour) with the same shopper-facing brief.  \n"
                "3. **Inspect answers** — Under each prompt, use the **dropdown** to switch between the Gemini and OpenAI replies.  \n"
                "4. **Mention %** — Counts substring hits for your **wizard brand** (and site host) vs each competitor’s "
                "**website tokens and brand names** from the table above. "
                "When GA4 is connected, we use the **top country by active users (last 90 days)** unless you override it "
                "in **New audit → step 3** or via **GEO_PRIMARY_MARKET_COUNTRY** / **GEO_PRIMARY_MARKET_COUNTRY_ID** in the environment."
            )

    def _default_comp_lines() -> str:
        lines: list[str] = []
        for k in (f"na_c{i}" for i in range(1, MAX_COMPETITORS + 1)):
            v = (st.session_state.get(k) or "").strip()
            if v:
                lines.append(v)
        ac = st.session_state.get("accepted_competitors")
        if isinstance(ac, list) and ac:
            lines = [str(x).strip() for x in ac if str(x).strip()] or lines
        return "\n".join(lines)

    if not str(st.session_state.get("pp_brand") or "").strip():
        st.session_state["pp_brand"] = str(st.session_state.get("na_brand") or "")
    if not str(st.session_state.get("pp_site") or "").strip():
        st.session_state["pp_site"] = str(st.session_state.get("na_site") or "")
    if "pp_comp" not in st.session_state:
        st.session_state["pp_comp"] = _default_comp_lines()

    if not str(st.session_state.get("pp_brand") or "").strip():
        site_try = str(st.session_state.get("pp_site") or st.session_state.get("na_site") or "").strip()
        if not site_try:
            au = st.session_state.get("audit")
            if isinstance(au, dict):
                site_try = str(au.get("base_url") or "").strip()
        g = _fallback_brand_label_from_url(site_try)
        if g:
            st.session_state["pp_brand"] = g
    if not str(st.session_state.get("na_brand") or "").strip() and str(st.session_state.get("pp_brand") or "").strip():
        st.session_state["na_brand"] = str(st.session_state.get("pp_brand") or "")
    if not str(st.session_state.get("na_site") or "").strip() and str(st.session_state.get("pp_site") or "").strip():
        st.session_state["na_site"] = str(st.session_state.get("pp_site") or "")

    if for_report_tab:
        st.markdown("#### Brand & site (wizard)")
    else:
        st.markdown("#### Brand & competitors for this scan")
    if for_report_tab:
        _mcc, _mid = _session_primary_market()
        if _mcc or _mid:
            mid_part = f" (`{html.escape(_mid)}`)" if _mid else ""
            st.caption(
                "Primary market for Gemini / OpenAI (wizard override → GA4 → env "
                "`GEO_PRIMARY_MARKET_COUNTRY` / `GEO_PRIMARY_MARKET_COUNTRY_ID`): **"
                + html.escape(_mcc or "—")
                + "**"
                + mid_part
                + "."
            )
    _render_pp_brand_and_competitors_panel(for_report_tab=for_report_tab)

    if not for_report_tab and not use_pss:
        st.text_area(
            "Manual categories / offerings (optional, one per line)",
            key="pp_manual_categories",
            height=100,
            help="Merged with categories inferred from GA4 top pages when those exist.",
        )

    if not use_pss:
        gen_btn_label = (
            "1. Generate up to 10 prompt recommendations (Gemini)"
            if not for_report_tab
            else "Regenerate up to 10 prompt recommendations (Gemini)"
        )
        if st.button(
            gen_btn_label,
            type="primary",
            key="pp_run_btn" if not for_report_tab else "pp_run_btn_report",
        ):
            try:
                from prompt_suggest import infer_category_labels_from_top_pages, suggest_ai_platform_prompts

                bname = _wizard_brand_for_probes()
                bsite = _wizard_site_for_probes()
                manual_cats = (
                    []
                    if for_report_tab
                    else [
                        ln.strip()
                        for ln in str(st.session_state.get("pp_manual_categories") or "").splitlines()
                        if ln.strip()
                    ]
                )
                stored_cats = [str(c).strip() for c in (st.session_state.get("pp_last_cat_labels") or []) if str(c).strip()]
                inferred = infer_category_labels_from_top_pages(
                    top_pages,
                    selected_industry=_effective_na_industry(),
                ) if top_pages else []
                cat_labels = list(dict.fromkeys([c for c in stored_cats + inferred + manual_cats if c.strip()]))
                if not cat_labels:
                    st.error(
                        "Add category context (setup step 5) or **manual categories** in this form."
                        if not for_report_tab
                        else "No category labels on file—re-run setup or use **Regenerate** after adding GA4 categories."
                    )
                elif not bname:
                    st.error("Set **Brand** in **New audit** (step 3) before generating prompts.")
                else:
                    ind = _effective_na_industry()
                    mcc, mid = _session_primary_market()
                    with st.spinner("Generating likely AI prompts with Gemini…"):
                        prompts_gen = suggest_ai_platform_prompts(
                            cat_labels,
                            brand_name=bname,
                            site_url=bsite,
                            industry=ind,
                            max_prompts=10,
                            market_country=mcc,
                            market_country_code=mid,
                        )
                    st.session_state["pp_prompts"] = prompts_gen
                    st.session_state["pp_last_cat_labels"] = cat_labels

                    st.session_state.pop("pp_sov", None)
                    st.session_state.pop("pp_live_probe", None)
                    st.session_state.pop("pp_content_actions", None)
                    st.session_state.pop("pp_report_show_sov_analysis", None)
                    st.success(f"Prompt recommendations ready ({len(prompts_gen)} prompts). Run **live probes** below.")
                    st.rerun()
            except Exception as e:
                st.error(str(e))

    probe_list = flat_from_pss if use_pss else (st.session_state.get("pp_prompts") or [])
    if not isinstance(probe_list, list):
        probe_list = []
    n_pr = len(probe_list)
    prompts = probe_list

    if not use_pss and probe_list:
        st.markdown(f"##### Generated prompts ({n_pr})")
        _render_readable_prompt_rows(probe_list)
    elif use_pss and pss_rows:
        st.caption(
            f"**{len(pss_rows)}** product or service line(s), **{n_pr}** prompts — expand groups below after live probes."
        )

    if probe_list:
        st.markdown("#### Live probe (Gemini + OpenAI)")
        if for_report_tab:
            st.caption(
                "Each prompt is answered independently by **Gemini** and by **OpenAI** (Chat Completions API). "
                "Wizard **competitor URLs are not** sent from this tab—after probes, open **Show SOV analysis** to view "
                "mention-based share, full replies, and **Gemini-inferred** peers from the combined reply text."
            )
        else:
            st.caption(
                "Each prompt is answered independently by **Gemini** and by **OpenAI** (Chat Completions API). "
                "Share of voice below is from **substring hits** in those replies (wizard brand + site vs wizard competitor URLs "
                "and names, **plus** brands inferred by **Gemini** from the combined reply text when available)."
            )
        probe_btn_label = (
            "1. Run live probes (Gemini + OpenAI for each prompt)"
            if use_pss
            else "2. Run live probes (Gemini + OpenAI for each prompt)"
        )
        if st.button(
            probe_btn_label,
            type="secondary",
            key="pp_live_probe_btn_report" if for_report_tab else "pp_live_probe_btn",
        ):
            try:
                from prompt_suggest import run_live_prompt_probes

                bname = _wizard_brand_for_probes()
                bsite = _wizard_site_for_probes()
                if for_report_tab:
                    st.session_state.pop("pp_report_show_sov_analysis", None)
                    comp_lines, comp_brands = [], []
                else:
                    comp_lines, comp_brands = _pp_competitor_url_brand_lists()
                if not bname:
                    st.error("Set **Brand** in **New audit** (step 3) before running probes.")
                else:
                    mcc, mid = _session_primary_market()
                    probe_max = min(80, max(1, n_pr)) if n_pr else 10
                    with st.spinner("Calling Gemini and OpenAI for each prompt (may take a minute)…"):
                        live = run_live_prompt_probes(
                            probe_list,
                            brand_name=bname,
                            brand_site_url=bsite,
                            competitor_urls=comp_lines,
                            competitor_brands=comp_brands,
                            max_prompts=probe_max,
                            market_country=mcc,
                            market_country_code=mid,
                        )
                    st.session_state["pp_live_probe"] = live
                    st.session_state["pp_live_probe_highlight_brand"] = bname
                    st.session_state["pp_live_probe_highlight_comps"] = comp_lines
                    st.session_state["pp_live_probe_highlight_comp_brands"] = comp_brands
                    st.success("Live probes complete—see results below.")
                    st.rerun()
            except Exception as e:
                st.error(str(e))

        live = st.session_state.get("pp_live_probe")
        if isinstance(live, dict) and live.get("per_prompt"):
            show_sov = (not for_report_tab) or bool(st.session_state.get("pp_report_show_sov_analysis"))
            if for_report_tab and not show_sov:
                st.markdown("##### Share of voice (SOV)")
                st.caption(
                    "Probes are done. Open SOV to load mention bars, per-prompt replies, and the **competitors** table "
                    "(with **Track competitor**)."
                )
                st.button(
                    "Show SOV analysis & detected competitors",
                    type="primary",
                    key="pp_report_sov_reveal_btn",
                    on_click=_pp_report_reveal_sov_on_click,
                )
            else:
                agg = live.get("aggregate") or {}
                gm = agg.get("gemini") or {}
                oa = agg.get("openai") or {}
                g_bp = float(gm.get("brand_share_pct") or 0)
                g_cp = float(gm.get("competitor_share_pct") or 0)
                o_bp = float(oa.get("brand_share_pct") or 0)
                o_cp = float(oa.get("competitor_share_pct") or 0)
                st.markdown("##### Share of voice from live replies (all prompts combined)")
                if live.get("disclaimer"):
                    st.caption(str(live["disclaimer"]))
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown(
                        _pp_html_metric_card(
                            tone="green",
                            label="Gemini — brand share",
                            value=f"{g_bp:.1f}%",
                            sub="Green vs blue matches Summary scorecard styling",
                        )
                        + _pp_html_sov_bar(g_bp, g_cp),
                        unsafe_allow_html=True,
                    )
                with c2:
                    st.markdown(
                        _pp_html_metric_card(
                            tone="blue",
                            label="OpenAI — brand share",
                            value=f"{o_bp:.1f}%",
                            sub="Stacked bar: brand (green) vs named competitors (blue)",
                        )
                        + _pp_html_sov_bar(o_bp, o_cp),
                        unsafe_allow_html=True,
                    )

                rd = live.get("reply_detected_brands")
                rde = live.get("reply_detected_brands_error")
                if (not for_report_tab) and isinstance(rd, list) and rd:
                    with st.expander("Brands named from reply excerpts (Gemini)", expanded=False):
                        st.caption(
                            "Used as **extra competitor needles** for mention % and highlighting (in addition to the "
                            "**Brand & competitors** table)."
                        )
                        st.dataframe(rd, use_container_width=True, hide_index=True)
                elif (not for_report_tab) and rde:
                    st.caption(f"Reply brand detection: {str(rde)[:220]}")

                if not use_pss:
                    sum_rows: list[dict[str, Any]] = []
                    for row in live["per_prompt"]:
                        if not isinstance(row, dict):
                            continue
                        sum_rows.append(
                            {
                                "#": row.get("index"),
                                "Prompt (trunc.)": (str(row.get("prompt") or "")[:64] + "…")
                                if len(str(row.get("prompt") or "")) > 64
                                else str(row.get("prompt") or ""),
                                "Brand % (G)": round(float(row.get("gemini_brand_mention_pct") or 0), 1),
                                "Comp % (G)": round(float(row.get("gemini_competitor_mention_pct") or 0), 1),
                                "Brand % (O)": round(float(row.get("openai_brand_mention_pct") or 0), 1),
                                "Comp % (O)": round(float(row.get("openai_competitor_mention_pct") or 0), 1),
                                "G err": (row.get("error_gemini") or "")[:32],
                                "O err": (row.get("error_openai") or "")[:32],
                            }
                        )
                    st.dataframe(sum_rows, use_container_width=True, hide_index=True)

                if use_pss:
                    _render_prompt_performance_pss_grouped(pss_rows, live, for_report_tab=for_report_tab)
                else:
                    hb = str(st.session_state.get("pp_live_probe_highlight_brand") or _wizard_brand_for_probes() or "")
                    hc = st.session_state.get("pp_live_probe_highlight_comps")
                    if not isinstance(hc, list):
                        hc = []
                    hcb = st.session_state.get("pp_live_probe_highlight_comp_brands")
                    if not isinstance(hcb, list):
                        hcb = []
                    h_reply_raw = live.get("reply_detected_brand_names")
                    h_reply_flat = (
                        [str(x).strip() for x in h_reply_raw if str(x).strip()]
                        if isinstance(h_reply_raw, list)
                        else []
                    )
                    from prompt_suggest import highlight_response_html

                    st.markdown("##### Per-prompt answers & mention split")
                    _ppx = "r" if for_report_tab else "n"
                    for row in live["per_prompt"]:
                        if not isinstance(row, dict):
                            continue
                        idx = int(row.get("index") or 0)
                        pq = str(row.get("prompt") or "")
                        st.divider()
                        st.markdown(f"**{idx}.** {pq}")
                        g_bp = float(row.get("gemini_brand_mention_pct") or 0)
                        g_cp = float(row.get("gemini_competitor_mention_pct") or 0)
                        o_bp = float(row.get("openai_brand_mention_pct") or 0)
                        o_cp = float(row.get("openai_competitor_mention_pct") or 0)
                        which = st.selectbox(
                            "Show assistant reply from",
                            ("Gemini", "OpenAI"),
                            key=f"pp_sel_{_ppx}_{idx}",
                            help="Switch models to compare how each assistant answered the same user query.",
                        )
                        mg = row.get("mention_scores_gemini") or {}
                        mo = row.get("mention_scores_openai") or {}
                        if which == "Gemini":
                            c1, c2 = st.columns(2)
                            with c1:
                                st.metric("Brand mention % (this reply)", f"{g_bp:.1f}%")
                            with c2:
                                st.metric("Competitors mention % (combined)", f"{g_cp:.1f}%")
                            gd = mg.get("competitor_detail") if isinstance(mg, dict) else None
                            if isinstance(gd, dict) and gd:
                                st.caption(
                                    "Competitor hit counts: " + ", ".join(f"{k}: {v}" for k, v in sorted(gd.items())[:10])
                                )
                            if row.get("error_gemini"):
                                st.error(str(row["error_gemini"]))
                            else:
                                st.markdown(
                                    highlight_response_html(
                                        str(row.get("gemini_response") or ""),
                                        hb,
                                        hc,
                                        hcb,
                                        reply_detected_brands=h_reply_flat if h_reply_flat else None,
                                    ),
                                    unsafe_allow_html=True,
                                )
                        else:
                            c1, c2 = st.columns(2)
                            with c1:
                                st.metric("Brand mention % (this reply)", f"{o_bp:.1f}%")
                            with c2:
                                st.metric("Competitors mention % (combined)", f"{o_cp:.1f}%")
                            od = mo.get("competitor_detail") if isinstance(mo, dict) else None
                            if isinstance(od, dict) and od:
                                st.caption(
                                    "Competitor hit counts: " + ", ".join(f"{k}: {v}" for k, v in sorted(od.items())[:10])
                                )
                            if row.get("error_openai"):
                                st.error(str(row["error_openai"]))
                            else:
                                st.markdown(
                                    highlight_response_html(
                                        str(row.get("openai_response") or ""),
                                        hb,
                                        hc,
                                        hcb,
                                        reply_detected_brands=h_reply_flat if h_reply_flat else None,
                                    ),
                                    unsafe_allow_html=True,
                                )

                if for_report_tab and show_sov:
                    if rde and not (isinstance(rd, list) and rd):
                        st.caption(f"Reply brand detection: {str(rde)[:220]}")
                    _render_reply_detected_competitors_track_table(live)

    live_probe_state = st.session_state.get("pp_live_probe")
    show_sov_for_weak = (not for_report_tab) or bool(st.session_state.get("pp_report_show_sov_analysis"))
    if (
        show_sov_for_weak
        and isinstance(live_probe_state, dict)
        and isinstance(live_probe_state.get("aggregate"), dict)
        and isinstance(prompts, list)
        and prompts
    ):
        st.markdown("##### Weak-prompt content actions (Gemini)")
        st.caption(
            "Uses a **second Gemini** pass on your prompts plus **live probe** mention totals to flag intents where "
            "the primary site may trail competitors, then proposes **publishable content** (not final copy—edit for brand and compliance)."
        )
        if st.button(
            "Generate content suggestions for weak prompts",
            type="secondary",
            key="pp_content_suggest_btn_report" if for_report_tab else "pp_content_suggest_btn",
        ):
            try:
                from prompt_suggest import suggest_content_for_weak_prompts

                bname = _wizard_brand_for_probes()
                bsite = _wizard_site_for_probes()
                comp_lines, _cb = _pp_competitor_url_brand_lists()
                cat_ctx = list(st.session_state.get("pp_last_cat_labels") or [])
                mcc, mid = _session_primary_market()
                with st.spinner("Reviewing weak coverage and drafting content actions…"):
                    content_pkg = suggest_content_for_weak_prompts(
                        prompts,
                        live_aggregate=live_probe_state["aggregate"],
                        brand_name=bname,
                        brand_site_url=bsite,
                        competitor_urls=comp_lines,
                        category_labels=cat_ctx or None,
                        max_weak_prompts=5,
                        market_country=mcc,
                        market_country_code=mid,
                    )
                st.session_state["pp_content_actions"] = content_pkg
                st.success("Content suggestions ready below.")
                st.rerun()
            except Exception as e:
                st.error(str(e))

    content_pkg = st.session_state.get("pp_content_actions")
    if isinstance(content_pkg, dict) and content_pkg.get("weak_prompts"):
        st.markdown("---")
        st.markdown("#### Suggested on-site content")
        if content_pkg.get("disclaimer"):
            st.caption(str(content_pkg["disclaimer"]))
        if content_pkg.get("priority_summary"):
            st.markdown("**Priority summary**")
            st.markdown(str(content_pkg["priority_summary"]))

        for wp in content_pkg.get("weak_prompts") or []:
            if not isinstance(wp, dict):
                continue
            title = str(wp.get("prompt") or "Prompt")[:120]
            with st.expander(title, expanded=False):
                st.markdown(f"**Prompt:** {wp.get('prompt', '')}")
                gap = wp.get("why_primary_underperforms") or wp.get("performance_gap")
                if gap:
                    st.markdown("**Why the site may trail competitors (from live probe context)**")
                    st.write(gap)
                for j, act in enumerate(wp.get("content_actions") or [], start=1):
                    if not isinstance(act, dict):
                        continue
                    st.markdown(f"**Action {j}: {act.get('action_title', 'Untitled')}**")
                    if act.get("content_format"):
                        st.caption(f"Format: {act['content_format']}")
                    if act.get("what_to_publish"):
                        st.write(act["what_to_publish"])
                    if act.get("differentiation_angle"):
                        st.markdown("*Brand angle:* " + str(act["differentiation_angle"]))
                    ob = act.get("outline_bullets")
                    if isinstance(ob, list) and ob:
                        st.markdown("Outline ideas")
                        for b in ob:
                            st.markdown(f"- {b}")
                    sn = act.get("snippet_or_title_suggestions")
                    if isinstance(sn, list) and sn:
                        st.markdown("Title / snippet ideas")
                        for s in sn:
                            st.markdown(f"- {s}")


def execute_ga4_suggestions_pull() -> tuple[bool, str | None]:
    """
    Fetch GA4 top pages and build onboarding suggestions into session state.

    Returns ``(ok, error_message)``. On failure, ``ok`` is False and ``error_message`` is set.
    """
    try:
        import ga4_oauth as g4o
        from onboarding_suggestions import build_onboarding_suggestions

        credsx = g4o.credentials_from_dict(st.session_state["ga4_user_creds_dict"])
        prop_id = str(st.session_state.get("ga4_selected_property_id") or "").strip()
        industry_sel = _effective_na_industry()
        with st.spinner("Reading GA4 top pages, primary market (country), and building suggestions…"):
            top_pages = g4o.fetch_top_pages_last_90_days(credsx, prop_id, limit=100)
            try:
                st.session_state["ga4_primary_market"] = g4o.fetch_top_country_by_users_last_90_days(
                    credsx, prop_id
                )
            except Exception:
                st.session_state["ga4_primary_market"] = None
            if not top_pages:
                return False, "GA4 returned no page rows for this property in the last 90 days."
            mkc, midc = _session_primary_market()
            suggestions = build_onboarding_suggestions(
                top_pages,
                selected_industry=industry_sel,
                market_country=mkc,
                market_country_code=midc,
            )

        st.session_state["ga4_top_pages"] = top_pages
        st.session_state["onboarding_suggestions"] = suggestions
        st.session_state["suggested_brand_website"] = suggestions.get("suggested_site_url") or ""
        st.session_state["suggested_brand_name"] = suggestions.get("suggested_brand_name") or ""
        st.session_state["suggested_industry_hint"] = suggestions.get("suggested_industry") or ""
        cat_labels = [c["label"] for c in suggestions.get("categories", [])]
        prod_labels = [p["label"] for p in suggestions.get("products", [])]
        if cat_labels:
            st.session_state["accepted_categories"] = cat_labels
        else:
            st.session_state.pop("accepted_categories", None)
        if prod_labels:
            st.session_state["accepted_products"] = prod_labels
        else:
            st.session_state.pop("accepted_products", None)
        comp_urls = [c["url"] for c in (suggestions.get("competitors") or []) if isinstance(c, dict) and c.get("url")]
        if comp_urls:
            st.session_state["accepted_competitors"] = comp_urls[:MAX_COMPETITORS]
        else:
            st.session_state.pop("accepted_competitors", None)

        sbn = (suggestions.get("suggested_brand_name") or "").strip()
        if sbn:
            st.session_state["na_brand"] = sbn
            st.session_state["_wiz_primary_brand"] = sbn
        ssu = (suggestions.get("suggested_site_url") or "").strip()
        if ssu:
            st.session_state["na_site"] = ssu
            st.session_state["_wiz_primary_url"] = ssu

        for i in range(MAX_COMPETITORS):
            st.session_state[f"na_c{i + 1}"] = comp_urls[i] if i < len(comp_urls) else ""

        st.session_state["suggestions_loaded"] = True
        st.session_state["_ga4_onboarding_pull_committed"] = True
        return True, None
    except Exception as e:
        return False, str(e)


def _audit_dir_has_ga4_traffic_json(audit_dir: Any) -> bool:
    return isinstance(audit_dir, Path) and (audit_dir / "ga4_traffic.json").is_file()


def _render_report_ga4_traffic_insights_cta(audit_dir: Path) -> None:
    """Offer Google OAuth when the opened audit has no GA4 export—never imply traffic data exists."""
    if _audit_dir_has_ga4_traffic_json(audit_dir):
        return
    with st.container(border=True):
        st.markdown("##### AI traffic insights")
        st.caption(
            "This audit has **no GA4 AI traffic export** (`ga4_traffic.json` is missing). "
            "Connect Google Analytics, then run **New audit** and choose **Yes** on the GA4 step so the crawl can pull traffic."
        )
        if st.session_state.get("ga4_user_creds_dict"):
            st.info(
                "You are already signed in to Google Analytics. During **New audit**, pick **Yes** for GA4 (step 4), "
                "choose a property, and re-run this site’s audit to embed traffic."
            )
            return
        cfg = ga4_oauth_client_config()
        if not cfg:
            st.info(
                "To enable **Connect GA4 to view AI traffic insights**, add **`[ga4_oauth]`** or **`[auth]`** "
                "to `.streamlit/secrets.toml` (see `.streamlit/secrets.toml.example`) with your Google OAuth client."
            )
            return
        cid, csec, ruri = cfg
        if not st.session_state.get("ga4_oauth_authorize_url"):
            try:
                import ga4_oauth as g4o

                auth_sec = _secrets_section("auth")
                cookie_secret = (auth_sec.get("cookie_secret") or "").strip()
                if cookie_secret:
                    step_raw = st.session_state.get("onboarding_step")
                    step_int = int(step_raw) if step_raw is not None else None
                    oauth_state = _ga4_oauth_sign_state(
                        cookie_secret,
                        onboarding_step=step_int,
                        wiz_ga4_after_yes=bool(st.session_state.get("wiz_ga4_after_yes")),
                    )
                else:
                    oauth_state = g4o.new_oauth_state()
                    st.session_state["ga4_oauth_state"] = oauth_state
                flow = g4o.build_flow(cid, csec, ruri)
                url = g4o.authorization_url(flow, state=oauth_state)
                st.session_state["ga4_oauth_authorize_url"] = url
            except Exception as e:
                st.error(f"Could not start Google OAuth: {e}")
                return
        url = st.session_state.get("ga4_oauth_authorize_url")
        if url:
            st.link_button(
                "Connect GA4 to view AI traffic insights",
                str(url),
                type="primary",
                key="rep_ga4_traffic_insights_lb",
                help="Opens Google’s consent screen for read-only Analytics access.",
            )


def render_ga4_oauth_new_audit_block(
    *,
    show_suggest_from_ga4_button: bool = True,
    wizard_compact: bool = False,
) -> None:
    """Connect Google (GA4 scopes), pick property, optional AI channel labels — outside the audit form."""
    if st.session_state.pop("ga4_oauth_just_connected", False):
        if show_suggest_from_ga4_button:
            st.success(
                "**Google Analytics is connected.** Pick your GA4 property, then run **Suggest website, categories, "
                "and competitors from GA4** (or fill the form manually)."
            )
        else:
            st.success(
                "**Google Analytics is connected.** Pick your GA4 property, set **AI channel labels** if needed, "
                "then continue to pull categories and products."
            )

    err_key = "ga4_oauth_error"
    if err_key in st.session_state and st.session_state[err_key]:
        st.warning(st.session_state.pop(err_key))

    cfg = ga4_oauth_client_config()
    if not cfg:
        st.info(
            "To pull **GA4** data with your Google account, add a **`[ga4_oauth]`** block to "
            "`.streamlit/secrets.toml` (see `.streamlit/secrets.toml.example`) with **client_id**, "
            "**client_secret**, and **redirect_uri**, or set **GA4_OAUTH_CLIENT_ID** / "
            "**GA4_OAUTH_CLIENT_SECRET** / **GA4_OAUTH_REDIRECT_URI** in the environment. "
            "A **`[auth]`** block alone is enough if it already has client id/secret (GA4 reuses them). "
            "On **Streamlit Community Cloud**, paste the same TOML under app **Secrets**. "
            "Enable **Google Analytics Admin API** and **Google Analytics Data API** for that OAuth client’s project."
        )
        return

    cid, csec, ruri = cfg
    if not wizard_compact:
        st.markdown("##### Google Analytics (GA4)")
        st.caption(
            "Optional: connect read-only Analytics access, choose a property, then run the audit. "
            "Requests Analytics read-only plus OpenID scopes when reusing the same OAuth client as "
            "Streamlit sign-in (matches Google’s combined token scopes)."
        )
    else:
        st.caption(
            "Read-only Analytics access lets this app list your GA4 properties and pull top-page data. "
            "Uses the same OAuth client as Streamlit sign-in when applicable."
        )

    cred_dict = st.session_state.get("ga4_user_creds_dict")
    if cred_dict:
        if st.button("Disconnect GA4", key="ga4_disconnect"):
            st.session_state.pop("ga4_user_creds_dict", None)
            st.session_state.pop("ga4_property_options", None)
            st.session_state.pop("ga4_selected_property_id", None)
            st.session_state.pop("ga4_oauth_authorize_url", None)
            for k in _GA4_ONBOARDING_KEYS:
                st.session_state.pop(k, None)
            st.rerun()
    else:
        if wizard_compact:
            if not st.session_state.get("ga4_oauth_authorize_url"):
                try:
                    import ga4_oauth as g4o

                    auth_sec = _secrets_section("auth")
                    cookie_secret = (auth_sec.get("cookie_secret") or "").strip()
                    if cookie_secret:
                        step_raw = st.session_state.get("onboarding_step")
                        step_int = int(step_raw) if step_raw is not None else None
                        oauth_state = _ga4_oauth_sign_state(
                            cookie_secret,
                            onboarding_step=step_int,
                            wiz_ga4_after_yes=bool(st.session_state.get("wiz_ga4_after_yes")),
                        )
                    else:
                        oauth_state = g4o.new_oauth_state()
                        st.session_state["ga4_oauth_state"] = oauth_state
                    flow = g4o.build_flow(cid, csec, ruri)
                    url = g4o.authorization_url(flow, state=oauth_state)
                    st.session_state["ga4_oauth_authorize_url"] = url
                except Exception as e:
                    st.error(f"Could not start OAuth: {e}")
            url = st.session_state.get("ga4_oauth_authorize_url")
            if url:
                st.link_button(
                    "Sign in with Google for Analytics",
                    url,
                    type="primary",
                    help="Opens Google consent for read-only Analytics access.",
                )
        else:
            if st.button("Connect Google for GA4", key="ga4_connect", type="secondary"):
                try:
                    import ga4_oauth as g4o

                    auth_sec = _secrets_section("auth")
                    cookie_secret = (auth_sec.get("cookie_secret") or "").strip()
                    if cookie_secret:
                        step_raw = st.session_state.get("onboarding_step")
                        step_int = int(step_raw) if step_raw is not None else None
                        oauth_state = _ga4_oauth_sign_state(
                            cookie_secret,
                            onboarding_step=step_int,
                            wiz_ga4_after_yes=bool(st.session_state.get("wiz_ga4_after_yes")),
                        )
                    else:
                        oauth_state = g4o.new_oauth_state()
                        st.session_state["ga4_oauth_state"] = oauth_state
                    flow = g4o.build_flow(cid, csec, ruri)
                    url = g4o.authorization_url(flow, state=oauth_state)
                    st.session_state["ga4_oauth_authorize_url"] = url
                except Exception as e:
                    st.error(f"Could not start OAuth: {e}")
            url = st.session_state.get("ga4_oauth_authorize_url")
            if url:
                st.link_button("Open Google consent…", url, type="primary", help="Sign in and approve Analytics read access.")

    if not st.session_state.get("ga4_user_creds_dict"):
        return

    try:
        import ga4_oauth as g4o

        creds = g4o.credentials_from_dict(st.session_state["ga4_user_creds_dict"])
        if st.session_state.get("ga4_property_options") is None:
            with st.spinner("Loading GA4 properties you can access…"):
                st.session_state["ga4_property_options"] = g4o.list_ga4_properties(creds)
    except Exception as e:
        st.error(f"Could not list GA4 properties: {e}")
        return

    props: list[dict[str, str]] = st.session_state["ga4_property_options"] or []
    if not props:
        st.warning("No GA4 properties returned for this account (check Admin API is enabled and you have access).")
        return

    labels = [f"{p['name']} — {p['id']} ({p.get('account', '')})" for p in props]
    ids = [p["id"] for p in props]
    default_ix = 0
    cur = st.session_state.get("ga4_selected_property_id")
    if cur and cur in ids:
        default_ix = ids.index(cur)
    ix = st.selectbox(
        "GA4 property for this audit",
        range(len(labels)),
        index=default_ix,
        format_func=lambda i: labels[int(i)],
        key="ga4_property_select_index",
    )
    st.session_state["ga4_selected_property_id"] = ids[int(ix)]

    if show_suggest_from_ga4_button:
        if st.button(
            "Suggest website, categories, and competitors from GA4",
            key="ga4_suggest_from_pages",
            help="Top 100 pages by pageviews (last 90 days), heuristics for categories/products, Gemini for competitor URLs.",
        ):
            ok, err = execute_ga4_suggestions_pull()
            if not ok:
                if err and err.startswith("GA4 returned"):
                    st.warning(err)
                elif err:
                    st.error(f"Could not generate suggestions from GA4: {err}")
            else:
                st.success("GA4 suggestions are ready—review the section below, then run the audit.")
            st.rerun()

    st.text_input(
        "AI channel bucket labels (comma-separated, optional)",
        value=os.environ.get("GA4_AI_CHANNEL_NAMES", ""),
        key="ga4_ai_channels_input",
        help=(
            "When set, these labels must match **your GA4 Admin custom channel group** bucket names; the export "
            "uses the Metadata API to resolve `sessionCustomChannelGroup:<id>` for this property. "
            "When **left blank**, the export uses **`sessionDefaultChannelGroup`** for session bucketing and "
            "labels the stacked “AI by source” chart from **known AI referrer hosts** only (not custom buckets)."
        ),
    )


def _create_report_cmd_env(
    primary: str,
    competitors: list[str],
    out_base: str,
    max_sitemap_urls: int,
    delay: float,
    *,
    brand_name: str = "",
    industry: str = "",
    ga4_property_id: str | None = None,
    ga4_ai_channels: str | None = None,
    ga4_oauth_credentials_path: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Argv + env for ``create-report.py`` (``-u`` for line-buffered child logs in Streamlit)."""
    cmd: list[str] = [
        sys.executable,
        "-u",
        str(BACKEND_ROOT / "create-report.py"),
        primary.strip(),
        "--out",
        out_base,
        "--max-sitemap-urls",
        str(max_sitemap_urls),
        "--max-sitemaps",
        "40",
        "--delay",
        str(delay),
        "--sample-robots",
        str(ASSETS_ROOT / "samples" / "robots.txt"),
        "--sample-llms",
        str(ASSETS_ROOT / "samples" / "llms-txt-skeleton.txt"),
    ]
    if brand_name.strip():
        cmd.extend(["--brand", brand_name.strip()])
    if industry.strip():
        cmd.extend(["--industry", industry.strip()])
    mcc, mid = _session_primary_market()
    if mcc.strip():
        cmd.extend(["--market-country", mcc.strip()])
    if mid.strip():
        cmd.extend(["--market-country-code", mid.strip()])
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
    env = os.environ.copy()
    if ga4_oauth_credentials_path:
        env["GOOGLE_APPLICATION_CREDENTIALS"] = ga4_oauth_credentials_path
    return cmd, env


def iter_run_full_pipeline_logs(
    primary: str,
    competitors: list[str],
    out_base: str,
    max_sitemap_urls: int,
    delay: float,
    *,
    brand_name: str = "",
    industry: str = "",
    ga4_property_id: str | None = None,
    ga4_ai_channels: str | None = None,
    ga4_oauth_credentials_path: str | None = None,
) -> Iterator[str]:
    """Yield merged stdout/stderr lines from ``create-report.py``; raise if non-zero exit."""
    cmd, env = _create_report_cmd_env(
        primary,
        competitors,
        out_base,
        max_sitemap_urls,
        delay,
        brand_name=brand_name,
        industry=industry,
        ga4_property_id=ga4_property_id,
        ga4_ai_channels=ga4_ai_channels,
        ga4_oauth_credentials_path=ga4_oauth_credentials_path,
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


def run_full_pipeline(
    primary: str,
    competitors: list[str],
    out_base: str,
    max_sitemap_urls: int,
    delay: float,
    *,
    brand_name: str = "",
    industry: str = "",
    ga4_property_id: str | None = None,
    ga4_ai_channels: str | None = None,
    ga4_oauth_credentials_path: str | None = None,
) -> Path:
    cmd, env = _create_report_cmd_env(
        primary,
        competitors,
        out_base,
        max_sitemap_urls,
        delay,
        brand_name=brand_name,
        industry=industry,
        ga4_property_id=ga4_property_id,
        ga4_ai_channels=ga4_ai_channels,
        ga4_oauth_credentials_path=ga4_oauth_credentials_path,
    )
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "create-report failed")
    return audit_dir_for_run(out_base, primary)


def ensure_report_html(audit_dir: Path, cr: Any) -> None:
    """Regenerate report.html / slides if missing (e.g. old folder)."""
    if (audit_dir / "report.html").is_file():
        return
    try:
        cr.generate_reports(audit_dir, None)
    except Exception:
        pass


def apply_audit_to_session(audit_dir: Path, cr: Any, archive_competitors: list[str] | None = None) -> None:
    audit = load_audit_summary(audit_dir)
    ensure_report_html(audit_dir, cr)
    weights = dict(cr.DEFAULT_WEIGHTS)
    wsum = sum(weights.values())
    if abs(wsum - 100.0) > 0.01:
        weights = {k: v * 100.0 / wsum for k, v in weights.items()}
    overall, categories = cr.score_audit(audit, weights)
    st.session_state["audit_dir"] = audit_dir
    st.session_state["audit"] = audit
    st.session_state["overall"] = overall
    st.session_state["categories"] = categories
    st.session_state["archive_competitors"] = archive_competitors or []
    load_onboarding_context_to_session(audit_dir)
    st.session_state.pop("_ga4_onboarding_pull_committed", None)
    ai = audit.get("audit_inputs") or {}
    brand_audit = str(ai.get("brand") or "").strip()
    if brand_audit and not str(st.session_state.get("pp_brand") or "").strip():
        st.session_state["pp_brand"] = brand_audit
    base_u = str(audit.get("base_url") or "").strip()
    if base_u and not str(st.session_state.get("pp_site") or "").strip():
        st.session_state["pp_site"] = base_u
    if not str(st.session_state.get("pp_brand") or "").strip():
        guess = _fallback_brand_label_from_url(str(st.session_state.get("pp_site") or base_u or ""))
        if guess:
            st.session_state["pp_brand"] = guess
    ac = archive_competitors or []
    if ac and not str(st.session_state.get("pp_comp") or "").strip():
        st.session_state["pp_comp"] = "\n".join(str(x).strip() for x in ac if str(x).strip())
    st.session_state["report_section"] = "summary"
    st.session_state.pop("pp_report_show_sov_analysis", None)


def _go_home() -> None:
    st.session_state["ui_view"] = "landing"
    st.session_state["landing_step"] = 1
    st.session_state.pop("onboarding_step", None)
    st.session_state.pop("wiz_ga4_after_yes", None)
    st.session_state.pop("ga4_oauth_authorize_url", None)
    for k in ("audit_dir", "audit", "overall", "categories", "archive_competitors"):
        st.session_state.pop(k, None)
    st.session_state.pop("report_section", None)


def _coerce_streamlit_auth_table_inplace(table: dict[str, Any]) -> dict[str, Any]:
    """Ensure Streamlit sees ``secrets['auth']``. Hoist known OAuth keys from the file root."""
    if not table:
        return table
    auth = table.get("auth")
    if isinstance(auth, Mapping) and any(
        str(auth.get(k) or "").strip() for k in ("redirect_uri", "cookie_secret", "client_id")
        ):
        return table
    hoist_keys = (
        "redirect_uri",
        "cookie_secret",
        "client_id",
        "client_secret",
        "server_metadata_url",
        "client_kwargs",
        "expose_tokens",
    )
    nested: dict[str, Any] = {}
    rest = dict(table)
    for k in hoist_keys:
        if k in rest:
            nested[k] = rest.pop(k)
    if nested:
        rest["auth"] = nested
    return rest


def _bootstrap_streamlit_secrets_from_repo() -> None:
    """Merge this repo's ``.streamlit/secrets.toml`` into Streamlit's secret store.

    ``st.login`` validates via ``secrets_singleton.load_if_toml_exists()``, which returns
    false when no TOML was parsed. That happens if no file exists on Streamlit's default
    paths, or if an *earlier* path (e.g. ``~/.streamlit/secrets.toml``) exists but has a
    TOML syntax error—then Streamlit never reaches the copy next to ``streamlit_app.py``.

    Shallow-merging ``{**already_loaded, **repo_file}`` lets the repo file supply ``[auth]``
    and API keys for typical local runs while preserving keys only present elsewhere.
    """
    path = REPO_ROOT / ".streamlit" / "secrets.toml"
    if not path.is_file():
        return
    try:
        import tomllib
    except ImportError:  # pragma: no cover - py310
        import tomli as tomllib  # type: ignore[no-redef,import-not-found]

    try:
        repo_table = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        try:
            if not st.session_state.get("_geo_secrets_bootstrap_error_shown"):
                st.session_state["_geo_secrets_bootstrap_error_shown"] = True
                st.warning(
                    f"Could not parse `.streamlit/secrets.toml` ({type(e).__name__}). "
                    "Use valid TOML (quote values with `:` or `/`), and put Google OAuth settings under `[auth]` "
                    "(see `.streamlit/secrets.toml.example`)."
                )
        except Exception:
            pass
        return
    if not repo_table:
        return
    repo_table = _coerce_streamlit_auth_table_inplace(repo_table)
    from streamlit.runtime.secrets import secrets_singleton

    had_any = secrets_singleton.load_if_toml_exists()
    if had_any:
        combined: dict[str, Any] = {**secrets_singleton.to_dict(), **repo_table}
    else:
        combined = dict(repo_table)
    try:
        secrets_singleton.merge_programmatic_secrets(combined)
    except TypeError:
        return


def _render_streamlit_auth_troubleshooting(*, expanded: bool = False) -> None:
    """Hints when ``st.login`` raises (missing secrets vs invalid TOML vs incomplete ``[auth]``)."""
    secrets_path = REPO_ROOT / ".streamlit" / "secrets.toml"
    with st.expander("Why Google sign-in fails (secrets & paths)", expanded=expanded):
        st.markdown(
            f"- **This repo's file:** `{secrets_path}` — must be valid TOML with an `[auth]` table "
            "including `redirect_uri`, `cookie_secret`, `client_id`, `client_secret`, and "
            "`server_metadata_url` (see `.streamlit/secrets.toml.example`).\n"
            "- **Streamlit's search order:** `~/.streamlit/secrets.toml`, then `$PWD/.streamlit/`, "
            "then `.streamlit/` next to `streamlit_app.py`. A **syntax error** in an earlier file "
            "stops parsing entirely; fix or rename that file.\n"
            "- **Redirect URI** must match how you open the app (scheme, host, port) plus `/oauth2callback`.\n"
            "- **Dependencies:** `pip install 'Authlib>=1.3.2'` (or `streamlit[auth]`) and restart Streamlit."
        )


def _try_landing_quick_open_audit(cr: Any, *, demo: bool) -> None:
    """Load sample or latest primary audit into session and switch to report view."""
    if demo:
        demo_path = _resolved_sample_audit_dir()
        if demo_path is None:
            st.error(
                "No bundled sample audit found. Run at least one audit so **audit_output/** contains a report, "
                "or add a sample folder under **SAMPLE_AUDIT_RELS** in streamlit_app.py."
            )
            return
        _open_audit_in_report_view(cr, demo_path, follow_latest=False)
    else:
        lp = find_latest_primary_audit()
        if lp and (lp / "audit_summary.json").is_file():
            _open_audit_in_report_view(cr, lp, follow_latest=True)
        else:
            st.warning("No primary audit found under audit_output/.")


def _render_landing_sidebar_hide() -> None:
    """Perception-style shell uses a left rail; landing is full-width like SetupPage."""
    st.sidebar.empty()
    st.markdown(
        "<style>"
        "section[data-testid='stSidebar']{display:none !important;}"
        "div[data-testid='collapsedControl']{display:none !important;}"
        "section.main{max-width:100% !important;}"
        "</style>",
        unsafe_allow_html=True,
    )


def _render_geo_page_header() -> None:
    """Slim top chrome (title only) for setup flows; report view omits this entirely."""
    bar = (
        '<div class="geo-app-header"><div class="geo-app-header-inner">'
        "<h1>GEO Audit</h1>"
        "</div></div>"
    )
    st.markdown(bar, unsafe_allow_html=True)


def _sidebar_home_button(*, key_suffix: str) -> None:
    """First control in the left rail (above section / app nav)."""
    if st.button(
        "Home",
        key=f"sidebar_nav_home_{key_suffix}",
        use_container_width=True,
        help="Start a new audit, load an existing run, or open quick shortcuts.",
    ):
        _go_home()
        st.rerun()


def _render_sso_signin_screen(login_fn: Any) -> None:
    """Full-width gradient + card (Perception.Flow ``SetupPage`` energy) when SSO is required."""
    st.markdown(
        "<style>"
        "section.main { background: linear-gradient(135deg, #5b67d4 0%, #6b3d96 48%, #764ba2 100%) "
        "!important; min-height: calc(100vh - 3rem); }"
        "section.main .stAppViewBlockContainer { background: transparent !important; }"
        "</style>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="geo-signin-card">'
        '<div class="geo-signin-icon">◆</div>'
        "<h2>Welcome to GEO Audit</h2>"
        '<p class="geo-signin-lead">Crawl your site, score GEO readiness, and open a full report in '
        "one place. Sign in with Google to reopen audits you ran while authenticated—your data still "
        "runs on this app; sign-in only links saved runs to your account.</p>"
        '<ul class="geo-signin-bullets">'
        "<li><strong>GEO scoring</strong> — structured checks across citability, technical signals, "
        "schema, and AI-facing readiness.</li>"
        "<li><strong>Deep report</strong> — export-style HTML you can scroll, share, or archive.</li>"
        "<li><strong>Your history</strong> — after sign-in, pick any past run from the list without "
        "re-entering URLs.</li>"
        "</ul>"
        '<p class="geo-signin-foot">Requires <code>[auth]</code> in <code>.streamlit/secrets.toml</code> '
        "with Google OIDC. See Streamlit’s Google Auth Platform guide if this button is disabled.</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    _, col_mid, _ = st.columns([1, 2, 1])
    with col_mid:
        if login_fn is not None:
            if st.button("Sign in with Google", type="primary", use_container_width=True, key="sso_google"):
                try:
                    login_fn()
                except StreamlitAuthError as e:
                    st.error(str(e))
                    _render_streamlit_auth_troubleshooting(
                        expanded="configure credentials" in str(e).lower()
                    )
                    st.caption(
                        "Typical fixes: `pip install 'Authlib>=1.3.2'` then restart Streamlit; "
                        "add `[auth]` in `.streamlit/secrets.toml` (see `.streamlit/secrets.toml.example`)."
                    )
        else:
            st.caption(
                "This Streamlit build has no `st.login`. Upgrade to streamlit>=1.42 and install "
                "auth dependencies (`pip install streamlit[auth]` or `authlib>=1.3.2`) per Streamlit docs."
            )


SETUP_WIZARD_TOTAL = 9


def _render_setup_progress(display_step: int, *, title: str | None = None) -> None:
    display_step = max(1, min(SETUP_WIZARD_TOTAL, int(display_step)))
    st.progress(display_step / float(SETUP_WIZARD_TOTAL))
    cap = f"Step **{display_step}** of **{SETUP_WIZARD_TOTAL}**"
    if title:
        cap += f" — {title}"
    st.caption(cap)


def _execute_geo_audit_from_wizard(cr: Any, progress_slot: Any = None) -> None:
    """Run crawl + scoring from wizard session keys; raises on failure."""
    brand_website = _wizard_brand_website_url()
    brand_name = _wizard_brand_name()
    industry = _effective_na_industry()
    comps = [str(st.session_state.get(f"na_c{i}") or "").strip() for i in range(1, MAX_COMPETITORS + 1)]
    comps = [c for c in comps if c][:MAX_COMPETITORS]
    crawl_comps: list[str] = []
    max_urls = int(st.session_state.get("wiz_max_urls") or 40)
    delay = float(st.session_state.get("wiz_delay") or 0.2)
    out_base = str(st.session_state.get("wiz_out_base") or DEFAULT_OUT_BASE).strip() or DEFAULT_OUT_BASE
    owner = current_user_email()
    ga4_tmp: Path | None = None
    want_ga4 = bool(st.session_state.get("setup_want_ga4"))
    cred_dict = st.session_state.get("ga4_user_creds_dict") if want_ga4 else None
    prop_id = (st.session_state.get("ga4_selected_property_id") or "").strip() if want_ga4 else ""
    ch_raw = st.session_state.get("ga4_ai_channels_input") if want_ga4 else None
    ga4_ch_arg: str | None = None if ch_raw is None else (str(ch_raw).strip() or None)
    try:
        if cred_dict and prop_id:
            import ga4_oauth as g4o

            creds = g4o.credentials_from_dict(cred_dict)
            ga4_tmp = g4o.write_temp_application_default_user_json(creds)
        pipe_kw = dict(
            brand_name=brand_name,
            industry=industry,
            ga4_property_id=prop_id if prop_id else None,
            ga4_ai_channels=ga4_ch_arg,
            ga4_oauth_credentials_path=str(ga4_tmp) if ga4_tmp else None,
        )
        if progress_slot is not None:
            progress_slot.empty()
            with progress_slot.container(border=True):
                st.markdown("**Crawl & report progress**")
                st.caption(
                    "Live terminal-style output from create-report (crawl, GA4 pulls, scoring, HTML). "
                    "The last line is usually the current phase."
                )
                st.write_stream(
                    iter_run_full_pipeline_logs(
                        brand_website,
                        crawl_comps,
                        out_base,
                        max_urls,
                        delay,
                        **pipe_kw,
                    )
                )
        else:
            run_full_pipeline(
                brand_website,
                crawl_comps,
                out_base,
                max_urls,
                delay,
                **pipe_kw,
            )
        adir = audit_dir_for_run(out_base, brand_website)
        apply_audit_to_session(adir, cr, comps)
        write_onboarding_context(adir)
        archive_add_run(
            primary_url=brand_website,
            audit_dir=adir,
            overall=float(st.session_state["overall"]),
            competitors=comps,
            owner_email=owner,
            brand_name=brand_name or None,
        )
    finally:
        if ga4_tmp is not None:
            try:
                ga4_tmp.unlink(missing_ok=True)
            except OSError:
                pass


def render_new_audit_setup_wizard(
    cr: Any, _industries: list[str], progress_below_header: Any = None
) -> None:
    """Multi-step new audit: brand → GA4 → products or services → competitors → prompts → run."""
    st.session_state.setdefault("onboarding_step", 3)
    if "na_site" not in st.session_state:
        st.session_state["na_site"] = ""
    st.session_state.setdefault("wiz_max_urls", 40)
    st.session_state.setdefault("wiz_delay", 0.2)
    st.session_state.setdefault("wiz_out_base", DEFAULT_OUT_BASE)
    st.session_state.setdefault("wiz_comp_manual_url", "")
    step = int(st.session_state.get("onboarding_step") or 3)
    # Steps 4+ do not mount ``na_site``; Streamlit may clear it on rerun. Restore from step-3 lock.
    if step > 3:
        _lk_restore = str(st.session_state.get("_wiz_s3_locked_canonical_url") or "").strip()
        if _lk_restore and not str(st.session_state.get("na_site") or "").strip():
            st.session_state["na_site"] = _lk_restore
    titles = {
        3: "Brand & website",
        4: "Google Analytics (optional)",
        5: "Products or services",
        6: "Competitors",
        7: "Suggested AI prompts",
        8: "Run audit",
    }
    _render_setup_progress(step, title=titles.get(step))

    # —— Step 3 ——
    if step == 3:
        from geo_market import default_primary_market_from_env
        from geo_setup_llm import normalize_competitor_url

        st.session_state.setdefault("wiz_s3_site_preview_unlocked", False)

        _pending_site = st.session_state.pop("_wiz_s3_na_site_normalized_pending", None)
        if isinstance(_pending_site, str) and _pending_site.strip():
            nu_ap = _pending_site.strip()
            st.session_state["na_site"] = nu_ap
            st.session_state["_wiz_s3_locked_canonical_url"] = nu_ap

        if bool(st.session_state.get("wiz_s3_site_preview_unlocked")):
            lk = str(st.session_state.get("_wiz_s3_locked_canonical_url") or "").strip()
            if lk:
                st.session_state["na_site"] = lk

        if not str(st.session_state.get("na_site") or "").strip():
            _cached = str(st.session_state.get("_wiz_primary_url") or "").strip()
            if _cached:
                st.session_state["na_site"] = _cached
        else:
            _wpu = str(st.session_state.get("_wiz_primary_url") or "").strip()
            if _wpu and normalize_competitor_url(str(st.session_state.get("na_site") or "").strip()) == _wpu:
                st.session_state["na_site"] = _wpu
        if not str(st.session_state.get("na_brand") or "").strip():
            _cb = str(st.session_state.get("_wiz_primary_brand") or "").strip()
            if _cb:
                st.session_state["na_brand"] = _cb

        if "geo_market_country" not in st.session_state:
            _dc, _did = default_primary_market_from_env()
            st.session_state["geo_market_country"] = _dc
            st.session_state["geo_market_country_code"] = _did

        st.markdown("#### Brand & website")
        st.caption("Primary crawl target and how the report labels your brand.")

        preview_unlocked = bool(st.session_state.get("wiz_s3_site_preview_unlocked"))
        if preview_unlocked and not str(st.session_state.get("_wiz_s3_brand_locked") or "").strip():
            legacy_b = str(st.session_state.get("na_brand") or "").strip()
            if legacy_b:
                st.session_state["_wiz_s3_brand_locked"] = legacy_b

        if not preview_unlocked:
            bn_preview = _wizard_brand_name()
            if not bn_preview:
                st.warning(
                    "Enter your **brand name** in the form below. It is **required** before you can continue—used for "
                    "visibility scans and AI competitor suggestions (there is no separate brand field later)."
                )
            with st.form("wiz_s3_lock_brand_site"):
                st.text_input(
                    "Brand name (required)",
                    key="na_brand",
                    placeholder="e.g. the name customers use in search and reviews",
                )
                st.text_input(
                    "Brand website (required)",
                    key="na_site",
                    help="Primary site URL passed to create-report.py as the crawl target. Paste your homepage (https recommended).",
                    placeholder="https://www.example.com",
                )
                st.caption(
                    "When the URL is correct, press **Enter** or click **Show audit preview**—that locks the site, "
                    "shows **How this will appear in the audit**, and reveals industry and **Continue**."
                )
                submitted = st.form_submit_button("Show audit preview", type="primary")
            if submitted:
                b_raw = str(st.session_state.get("na_brand") or "").strip()
                s_raw = str(st.session_state.get("na_site") or "").strip()
                if not b_raw:
                    st.error("Enter your **brand name** first.")
                elif not s_raw:
                    st.error("Enter your **brand website** URL.")
                else:
                    nu = normalize_competitor_url(s_raw)
                    if not nu:
                        st.error("That URL could not be normalized—add **https://** and a valid hostname, then try again.")
                    else:
                        st.session_state["_wiz_s3_brand_locked"] = b_raw.strip()
                        st.session_state["_wiz_s3_na_site_normalized_pending"] = nu
                        st.session_state["_wiz_s3_locked_canonical_url"] = nu
                        st.session_state["wiz_s3_site_preview_unlocked"] = True
                        st.rerun()
            b1, _ = st.columns(2)
            with b1:
                if st.button("← Back", key="wiz_s3_back"):
                    st.session_state.pop("wiz_s3_site_preview_unlocked", None)
                    st.session_state.pop("_wiz_s3_locked_canonical_url", None)
                    st.session_state.pop("_wiz_s3_brand_locked", None)
                    st.session_state["ui_view"] = "landing"
                    st.session_state["landing_step"] = 2
                    st.rerun()
            return

        site_raw = str(
            st.session_state.get("_wiz_s3_locked_canonical_url")
            or st.session_state.get("na_site")
            or ""
        ).strip()
        nu = normalize_competitor_url(site_raw) if site_raw else ""
        if not nu:
            st.error("Brand website is missing or invalid—use **Change website** and enter a full URL.")
            if st.button("Change website", key="wiz_s3_unlock_bad"):
                st.session_state["wiz_s3_site_preview_unlocked"] = False
                st.session_state.pop("_wiz_s3_locked_canonical_url", None)
                st.session_state.pop("_wiz_s3_brand_locked", None)
                st.rerun()
            return

        brand_disp = _wiz_s3_resolved_brand_after_preview() or _fallback_brand_label_from_url(nu)
        host = hostname_for_display_url(nu)
        st.markdown("##### How this will appear in the audit")
        st.caption("Brand name, site URL, and favicon (read-only preview).")
        c0, c1, c2 = st.columns((1.1, 3.2, 5.5), gap="small")
        with c0:
            if host:
                st.image(public_site_favicon_url(host), width=28)
        with c1:
            st.markdown(f"**{html.escape(brand_disp)}**", unsafe_allow_html=True)
        with c2:
            st.markdown(
                f'<p style="margin:0;font-size:0.88rem;color:#4b5563;user-select:none;">{html.escape(nu)}</p>',
                unsafe_allow_html=True,
            )
        if st.button("← Change website or brand", key="wiz_s3_unlock"):
            st.session_state["wiz_s3_site_preview_unlocked"] = False
            st.session_state.pop("_wiz_s3_locked_canonical_url", None)
            st.session_state.pop("_wiz_s3_brand_locked", None)
            st.rerun()

        industry_opts = [WIZ_INDUSTRY_PLACEHOLDER] + list(_industries)
        cur_ind = str(st.session_state.get("na_industry") or "").strip()
        if cur_ind in industry_opts:
            ind_idx = industry_opts.index(cur_ind)
        else:
            ind_idx = 0
        st.selectbox(
            "Brand industry (required — choose one)",
            options=industry_opts,
            index=ind_idx,
            key="na_industry",
            help="GA4 suggestions will **not** pick this for you—you must select an industry that best fits the brand.",
        )
        st.markdown("##### Primary market (for AI prompts)")
        st.caption(
            "Used for **prompt wording**, **competitor suggestions**, and **live probes**. "
            "If you connect GA4 later, its top country still wins **unless** you fill these fields "
            "(or set `GEO_PRIMARY_MARKET_COUNTRY` / `GEO_PRIMARY_MARKET_COUNTRY_ID` in the environment)."
        )
        c_m1, c_m2 = st.columns(2)
        with c_m1:
            st.text_input(
                "Country / region (optional)",
                key="geo_market_country",
                placeholder="e.g. United Kingdom",
            )
        with c_m2:
            st.text_input(
                "ISO country code (optional)",
                key="geo_market_country_code",
                placeholder="e.g. GB",
                max_chars=2,
                help="Two-letter ISO 3166-1 alpha-2 (e.g. GB, DE, AU). Leave blank if unsure.",
            )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("← Back", key="wiz_s3_back_unlocked"):
                st.session_state.pop("wiz_s3_site_preview_unlocked", None)
                st.session_state.pop("_wiz_s3_locked_canonical_url", None)
                st.session_state.pop("_wiz_s3_brand_locked", None)
                st.session_state["ui_view"] = "landing"
                st.session_state["landing_step"] = 2
                st.rerun()
        with b2:
            if st.button("Continue", type="primary", key="wiz_s3_next"):
                brand_ok = _wiz_s3_resolved_brand_after_preview()
                if not brand_ok:
                    st.error("Brand name is required. Enter the name your customers know you by, then try again.")
                elif not str(
                    st.session_state.get("_wiz_s3_locked_canonical_url")
                    or st.session_state.get("na_site")
                    or ""
                ).strip():
                    st.error("Brand website is required.")
                else:
                    ind_sel = str(st.session_state.get("na_industry") or "").strip()
                    if not ind_sel or ind_sel == WIZ_INDUSTRY_PLACEHOLDER or ind_sel not in _industries:
                        st.error("Select a **Brand industry** from the dropdown (not the placeholder row).")
                    else:
                        st.session_state["na_brand"] = brand_ok
                        st.session_state["_wiz_primary_brand"] = brand_ok
                        _lu = str(st.session_state.get("_wiz_s3_locked_canonical_url") or "").strip()
                        if _lu:
                            st.session_state["na_site"] = _lu
                        _wizard_bind_site_after_step3_continue()
                        st.session_state["onboarding_step"] = 4
                        st.rerun()
        return

    # —— Step 4 ——
    if step == 4:
        st.markdown("#### Google Analytics (optional)")
        if st.session_state.get("wiz_ga4_after_yes") and st.session_state.get("setup_want_ga4"):
            st.caption("Use the link below to sign in, then choose your GA4 property.")
            render_ga4_oauth_new_audit_block(
                show_suggest_from_ga4_button=False,
                wizard_compact=True,
            )
            b1, b2 = st.columns(2)
            with b1:
                if st.button("← Back", key="wiz_s4_ga_back"):
                    st.session_state["wiz_ga4_after_yes"] = False
                    st.session_state.pop("ga4_oauth_authorize_url", None)
                    st.rerun()
            with b2:
                if st.button("Continue to products or services", type="primary", key="wiz_s4_ga_next"):
                    if not st.session_state.get("ga4_user_creds_dict"):
                        st.error("Complete **Sign in with Google for Analytics** first.")
                    elif not str(st.session_state.get("ga4_selected_property_id") or "").strip():
                        st.error("Select a **GA4 property** from the list above.")
                    else:
                        st.session_state["onboarding_step"] = 5
                        st.rerun()
        else:
            st.radio(
                "Do you want to connect GA4 for traffic context (optional)?",
                ["yes", "skip"],
                horizontal=True,
                key="setup_ga4_choice",
                format_func=lambda x: "Yes" if x == "yes" else "Skip",
            )
            if st.session_state.get("setup_ga4_choice") == "skip":
                st.info("Next you will define **products or services** with **Gemini** from your website.")
            b1, b2 = st.columns(2)
            with b1:
                if st.button("← Back", key="wiz_s4_back"):
                    st.session_state["onboarding_step"] = 3
                    st.rerun()
            with b2:
                if st.button("Next", type="primary", key="wiz_s4_next"):
                    want = st.session_state.get("setup_ga4_choice") == "yes"
                    st.session_state["setup_want_ga4"] = want
                    if want:
                        st.session_state["wiz_ga4_after_yes"] = True
                        st.session_state.pop("ga4_oauth_authorize_url", None)
                        st.rerun()
                    st.session_state["wiz_ga4_after_yes"] = False
                    st.session_state["_ga4_onboarding_pull_committed"] = False
                    st.session_state["onboarding_step"] = 5
                    st.rerun()
        return

    # —— Step 5 ——
    if step == 5:
        st.markdown("#### Products or services")
        site_g5 = _wizard_brand_website_url()
        site_h5 = _canonical_site_hostname(site_g5)
        gem_h5 = str(st.session_state.get("_wiz_gemini_pss_site") or "").strip()
        if site_h5 and gem_h5 and site_h5 != gem_h5:
            st.session_state.pop("geo_pss_rows", None)
            st.session_state.pop("wiz_pss_selected", None)
            st.session_state.pop("_wiz_gemini_pss_site", None)
        st.caption(
            "Only **Gemini** suggests lines here (same structured flow as ``prompt_dev/geo_prompt_tuning.py``). "
            "By default you get **five** product or service lines and **five** shopper-style prompts each, "
            "using the **primary market** from **Brand & website** when set. "
            "You choose which lines to keep, then approve individual prompts on the next step."
        )
        if st.session_state.get("setup_want_ga4") and st.session_state.get("ga4_user_creds_dict"):
            with st.expander("Optional: pull GA4 top pages for reference (not used to build this list)", expanded=False):
                if st.button("Refresh GA4 top pages", type="secondary", key="wiz_s5_ga4_ref"):
                    ok, err = execute_ga4_suggestions_pull()
                    if not ok:
                        if err and err.startswith("GA4 returned"):
                            st.warning(err)
                        elif err:
                            st.error(err)
                    else:
                        st.success("GA4 context refreshed.")
                    st.rerun()
                if st.session_state.get("onboarding_suggestions"):
                    render_ga4_onboarding_suggestions(
                        _industries,
                        sections=frozenset({"products"}),
                    )

        if not site_g5:
            st.warning(
                "Add your **brand website** (primary crawl URL) on **Brand & website** before Gemini can suggest lines. "
                "Use **← Back** if that field is empty here—your URL is saved when you choose **Continue** there."
            )
        elif st.button("Suggest products or services from website (Gemini)", type="primary", key="wiz_s5_gemini_pss"):
            try:
                from geo_setup_llm import suggest_products_and_services

                _mcc_pss, _mid_pss = _session_primary_market()
                with st.spinner("Asking Gemini for products or services and prompts…"):
                    rows_g = suggest_products_and_services(
                        site_g5,
                        market_country=_mcc_pss,
                        market_country_code=_mid_pss,
                    )
                st.session_state["geo_pss_rows"] = rows_g
                st.session_state["_wiz_gemini_pss_site"] = _canonical_site_hostname(site_g5)
                names_g = [
                    str(r.get("product_or_service") or "").strip()
                    for r in rows_g
                    if isinstance(r, dict) and str(r.get("product_or_service") or "").strip()
                ]
                st.session_state["wiz_pss_selected"] = names_g
                st.success(f"Received **{len(names_g)}** lines—adjust the multiselect, then **Continue**.")
                st.rerun()
            except Exception as e:
                st.error(str(e))
        pss_rows_ui = st.session_state.get("geo_pss_rows") or []
        if isinstance(pss_rows_ui, list) and pss_rows_ui:
            names_all = [
                str(r.get("product_or_service") or "").strip()
                for r in pss_rows_ui
                if isinstance(r, dict) and str(r.get("product_or_service") or "").strip()
            ]
            st.multiselect(
                "Select products or services to keep",
                options=names_all,
                key="wiz_pss_selected",
                help="These labels are stored for the audit and seed **Prompt performance**.",
            )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("← Back", key="wiz_s5_back"):
                st.session_state["onboarding_step"] = 4
                if st.session_state.get("setup_want_ga4"):
                    st.session_state["wiz_ga4_after_yes"] = True
                st.rerun()
        with b2:
            if st.button("Continue", type="primary", key="wiz_s5_next"):
                from geo_setup_llm import flatten_product_prompts

                rows_s = st.session_state.get("geo_pss_rows") or []
                sel_s = list(st.session_state.get("wiz_pss_selected") or [])
                if not isinstance(rows_s, list) or not rows_s:
                    st.error("Use **Suggest products or services from website** and keep at least one line.")
                else:
                    by_name = {
                        str(r.get("product_or_service") or "").strip(): r
                        for r in rows_s
                        if isinstance(r, dict) and str(r.get("product_or_service") or "").strip()
                    }
                    picked_rows = [by_name[n] for n in sel_s if n in by_name]
                    if not picked_rows:
                        st.error("Select at least one product or service from the list.")
                    else:
                        st.session_state["geo_pss_rows"] = picked_rows
                        st.session_state["accepted_categories"] = list(sel_s)
                        st.session_state["pp_prompts"] = flatten_product_prompts(picked_rows)
                        st.session_state["pp_last_cat_labels"] = list(sel_s)
                        st.session_state["onboarding_step"] = 6
                        st.rerun()
        return

    # —— Step 6 ——
    if step == 6:
        st.markdown("#### Competitors")
        st.caption(
            f"Optional: up to **{MAX_COMPETITORS}** peer site URLs for **Prompt performance** and for an optional **competitor crawl** "
            "after the report opens (the first audit run does **not** crawl peers by default)."
        )
        brand_for_msg = _wizard_brand_name()
        if brand_for_msg:
            st.caption(f"Brand context: **{html.escape(brand_for_msg)}**")

        top_pages = list(st.session_state.get("ga4_top_pages") or [])
        if top_pages:
            with st.expander("Reference: top GA4 pages (optional context)", expanded=False):
                labels = []
                for p in top_pages[:50]:
                    if not isinstance(p, dict):
                        continue
                    path = str(p.get("path") or "").strip()
                    title = str(p.get("title") or "").strip()[:60]
                    pv = int(p.get("pageviews") or 0)
                    labels.append(f"{path} — {title} ({pv:,} views)")
                if labels:
                    st.selectbox("Browse pulled pages", options=labels, key="wiz_s6_top_page_ref")

        comp_mode = st.radio(
            "How do you want to choose competitor websites?",
            ["manual", "ai"],
            index=0,
            key="na_comp_mode",
            horizontal=True,
            format_func=lambda m: (
                "I will enter URLs myself" if m == "manual" else "Suggest competitors with Gemini (AI)"
            ),
        )
        if comp_mode == "ai":
            st.info(
                "**Gemini** suggests competitor brands and homepages from your **site URL**, the **products or services** "
                "you confirmed in step 5, and the **primary market** from **Brand & website** (same structured flow as "
                "``geo_setup_llm`` / prompt tuning). The first run fills **up to three** rows; use **Suggest more** to merge "
                "additional Gemini results (deduped by URL), up to the row limit."
            )
            b_ai1, b_ai2 = st.columns(2)
            with b_ai1:
                run_initial = st.button(
                    "Suggest competitors with Gemini",
                    type="secondary",
                    key="na_ai_comp_search_btn",
                    help=f"Fills the table with up to **{WIZARD_GEMINI_COMP_INITIAL}** suggestions first.",
                )
            with b_ai2:
                n_exist = len(
                    [
                        d
                        for d in (st.session_state.get("geo_competitors_detail") or [])
                        if isinstance(d, dict) and str(d.get("competitor_website") or "").strip()
                    ]
                )
                run_more = st.button(
                    "Suggest more competitors (Gemini)",
                    type="secondary",
                    key="na_ai_comp_more_btn",
                    disabled=n_exist == 0 or n_exist >= MAX_COMPETITORS,
                    help="Calls Gemini again and **merges** new peers (deduped by URL) until the row limit.",
                )

            if run_initial or run_more:
                from geo_setup_llm import normalize_competitor_url, suggest_competitors

                site_g = _wizard_brand_website_url()
                cats_g = [str(c).strip() for c in (st.session_state.get("accepted_categories") or []) if str(c).strip()]
                if not site_g:
                    st.error("Brand website missing—go back to **Brand & website** and choose **Continue**.")
                elif not cats_g:
                    st.error("Select products or services in step 5 first.")
                else:
                    try:
                        _mcc_c, _mid_c = _session_primary_market()
                        with st.spinner("Asking Gemini for competitors…"):
                            found = suggest_competitors(
                                site_g,
                                cats_g,
                                market_country=_mcc_c,
                                market_country_code=_mid_c,
                            )
                        if not isinstance(found, list):
                            found = []

                        def _rows_from_found(src: list[Any], *, limit: int | None) -> list[dict[str, Any]]:
                            out: list[dict[str, Any]] = []
                            for r in src:
                                if limit is not None and len(out) >= limit:
                                    break
                                if not isinstance(r, dict):
                                    continue
                                nu = normalize_competitor_url(str(r.get("competitor_website") or ""))
                                if not nu:
                                    continue
                                out.append(
                                    {
                                        "competitor_brand": str(r.get("competitor_brand") or "").strip(),
                                        "competitor_website": nu,
                                    }
                                )
                            return out

                        if run_initial:
                            det = _rows_from_found(found, limit=WIZARD_GEMINI_COMP_INITIAL)
                            st.session_state["geo_competitors_detail"] = det
                            st.session_state["wiz_comp_detail_gen"] = (
                                int(st.session_state.get("wiz_comp_detail_gen") or 0) + 1
                            )
                            pre = det[:MAX_COMPETITORS]
                            urls5 = [str(d.get("competitor_website") or "") for d in pre]
                            st.session_state["wiz_comp_urls_sel"] = urls5
                            _apply_competitors_detail_to_session(pre)
                            st.success(
                                f"Suggested **{len(det)}** competitor(s) (first batch, up to {WIZARD_GEMINI_COMP_INITIAL}). "
                                "Use **Suggest more** to merge additional Gemini results, or add a site manually."
                            )
                        else:
                            prev = [
                                x
                                for x in (st.session_state.get("geo_competitors_detail") or [])
                                if isinstance(x, dict)
                            ]
                            exist_u = {
                                normalize_competitor_url(str(x.get("competitor_website") or ""))
                                for x in prev
                            }
                            exist_u.discard("")
                            merged = list(prev)
                            added = 0
                            for r in found:
                                if not isinstance(r, dict):
                                    continue
                                nu = normalize_competitor_url(str(r.get("competitor_website") or ""))
                                if not nu or nu in exist_u:
                                    continue
                                if len(merged) >= MAX_COMPETITORS:
                                    break
                                merged.append(
                                    {
                                        "competitor_brand": str(r.get("competitor_brand") or "").strip(),
                                        "competitor_website": nu,
                                    }
                                )
                                exist_u.add(nu)
                                added += 1
                            st.session_state["geo_competitors_detail"] = merged
                            st.session_state["wiz_comp_detail_gen"] = (
                                int(st.session_state.get("wiz_comp_detail_gen") or 0) + 1
                            )
                            pre = merged[:MAX_COMPETITORS]
                            urls5 = [str(d.get("competitor_website") or "") for d in pre]
                            st.session_state["wiz_comp_urls_sel"] = urls5
                            _apply_competitors_detail_to_session(pre)
                            if added:
                                st.success(
                                    f"Merged **{added}** new competitor row(s) (**{len(merged)}** total). "
                                    "Duplicates by URL were skipped."
                                )
                            else:
                                st.info("No new competitor URLs in this Gemini pass (all were duplicates or empty).")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                    except Exception as e:
                        st.error(f"Competitor search failed: {e}")
        det_m = st.session_state.get("geo_competitors_detail") or []
        st.session_state.pop("wiz_add_comp_url", None)
        from geo_setup_llm import normalize_competitor_url

        picked_urls: list[str] = []
        if isinstance(det_m, list) and det_m:
            st.markdown("##### Competitors")
            st.caption(
                "Favicon, brand, and site per row. Toggle **Include** for the audit; add more sites with the search box below."
            )
            for d in det_m:
                if not isinstance(d, dict):
                    continue
                u = normalize_competitor_url(str(d.get("competitor_website") or ""))
                if not u:
                    continue
                brand = str(d.get("competitor_brand") or "").strip() or "—"
                host = hostname_for_display_url(u)
                ck = _wiz_comp_checkbox_key(u)
                if ck not in st.session_state:
                    st.session_state[ck] = True
                c0, c1, c2, c3 = st.columns((1.1, 3.2, 5.5, 1.8), gap="small")
                with c0:
                    if host:
                        st.image(public_site_favicon_url(host), width=28)
                with c1:
                    st.markdown(f"**{html.escape(brand)}**", unsafe_allow_html=True)
                with c2:
                    st.markdown(
                        f'<p style="margin:0;font-size:0.88rem;color:#4b5563;user-select:none;">{html.escape(u)}</p>',
                        unsafe_allow_html=True,
                    )
                with c3:
                    st.checkbox("Include", key=ck)
            for d in det_m:
                if not isinstance(d, dict):
                    continue
                u = normalize_competitor_url(str(d.get("competitor_website") or ""))
                if not u:
                    continue
                if st.session_state.get(_wiz_comp_checkbox_key(u), False):
                    picked_urls.append(u)
            st.session_state["wiz_comp_urls_sel"] = picked_urls[:MAX_COMPETITORS]
        else:
            st.session_state["wiz_comp_urls_sel"] = []
            st.caption("No competitors in the list yet—use **Suggest competitors with Gemini** or add a site below.")

        sel_final = list(st.session_state.get("wiz_comp_urls_sel") or [])
        st.session_state["accepted_competitors"] = sel_final
        st.session_state["pp_comp"] = "\n".join(sel_final)

        n_rows = sum(
            1
            for d in (det_m if isinstance(det_m, list) else [])
            if isinstance(d, dict) and normalize_competitor_url(str(d.get("competitor_website") or ""))
        )
        st.markdown("##### Add a competitor")
        if n_rows >= MAX_COMPETITORS:
            st.session_state.pop("wiz_add_comp_msg", None)
            st.caption(
                f"You have **{MAX_COMPETITORS}** competitor rows (the limit). To change the list, run **Suggest competitors with Gemini** again (replaces with a fresh batch of up to {WIZARD_GEMINI_COMP_INITIAL}), use **Suggest more** until full, or start a **New audit**."
            )
        else:
            msg = st.session_state.pop("wiz_add_comp_msg", None)
            if isinstance(msg, tuple) and len(msg) == 2 and msg[0] == "error":
                st.error(msg[1])
            render_url_searchbox(
                session_key="wiz_comp_manual_url",
                label="Search or paste one competitor homepage",
                help="Adds one site to the table above. Duplicates are skipped.",
                rerun_on_update=False,
            )
            st.button(
                "Add to table",
                type="secondary",
                key="wiz_add_comp_submit",
                on_click=_wiz_add_competitor_on_click,
            )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("← Back", key="wiz_s6_back"):
                st.session_state["onboarding_step"] = 5
                st.rerun()
        with b2:
            if st.button("Continue", type="primary", key="wiz_s6_next"):
                pu = [
                    normalize_competitor_url(str(u))
                    for u in (st.session_state.get("wiz_comp_urls_sel") or [])
                    if str(u).strip()
                ]
                pu = [u for u in pu if u][:MAX_COMPETITORS]
                _wiz_write_na_slots_from_urls(pu)
                st.session_state["onboarding_step"] = 7
                st.rerun()
        return

    # —— Step 7 ——
    if step == 7:
        st.markdown("#### Review AI prompts")
        st.caption(
            "Up to **five** products or services × **five** prompts each (defaults from Gemini in step 5). "
            "Uncheck any prompt you do not want in the audit or in **Prompt performance**, then **Continue**."
        )
        rows7 = st.session_state.get("geo_pss_rows") or []
        if not isinstance(rows7, list) or not rows7:
            st.error("Go back to **step 5** and generate products or services with Gemini.")
            b1, _ = st.columns(2)
            with b1:
                if st.button("← Back", key="wiz_s7_back_err"):
                    st.session_state["onboarding_step"] = 6
                    st.rerun()
            return
        for gi, r in enumerate(rows7):
            if not isinstance(r, dict):
                continue
            name = str(r.get("product_or_service") or "").strip()
            prompts_r = [str(p).strip() for p in (r.get("prompts") or []) if str(p).strip()]
            if not name or not prompts_r:
                continue
            with st.expander(f"{html.escape(name)} ({len(prompts_r)} prompts)", expanded=(gi == 0)):
                for pi, pr in enumerate(prompts_r):
                    c1, c2 = st.columns([0.055, 0.945])
                    with c1:
                        st.checkbox(
                            "include",
                            value=True,
                            key=f"wiz_pss_inc_{gi}_{pi}",
                            label_visibility="collapsed",
                        )
                    with c2:
                        esc = html.escape(pr)
                        st.markdown(
                            "<div style=\"border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;margin-bottom:10px;"
                            "white-space:pre-wrap;word-break:break-word;line-height:1.5;font-size:0.95rem;\">"
                            f"{esc}</div>",
                            unsafe_allow_html=True,
                        )
        b1, b2 = st.columns(2)
        with b1:
            if st.button("← Back", key="wiz_s7_back"):
                st.session_state["onboarding_step"] = 6
                st.rerun()
        with b2:
            if st.button("Continue", type="primary", key="wiz_s7_next"):
                from geo_setup_llm import flatten_product_prompts

                new_rows: list[dict[str, Any]] = []
                for gi, r in enumerate(rows7):
                    if not isinstance(r, dict):
                        continue
                    name = str(r.get("product_or_service") or "").strip()
                    prs = [str(p).strip() for p in (r.get("prompts") or []) if str(p).strip()]
                    kept = [prs[pi] for pi in range(len(prs)) if st.session_state.get(f"wiz_pss_inc_{gi}_{pi}", True)]
                    if kept:
                        new_rows.append({"product_or_service": name, "prompts": kept})
                if not new_rows:
                    st.error("Keep at least one prompt selected.")
                else:
                    st.session_state["geo_pss_rows"] = new_rows
                    st.session_state["pp_prompts"] = flatten_product_prompts(new_rows)
                    st.session_state["accepted_categories"] = [str(x.get("product_or_service") or "").strip() for x in new_rows]
                    st.session_state["pp_last_cat_labels"] = list(st.session_state["accepted_categories"])
                    st.session_state.pop("pp_sov", None)
                    st.session_state.pop("pp_live_probe", None)
                    st.session_state.pop("pp_content_actions", None)
                    st.session_state["onboarding_step"] = 8
                    st.rerun()
        return

    # —— Step 8 ——
    if step == 8:
        st.markdown("#### Review & run audit")
        st.write("**Brand:**", _wizard_brand_name() or "—")
        st.write("**Site:**", _wizard_brand_website_url() or "—")
        emc, emid = _session_primary_market()
        if emc or emid:
            st.write("**Primary market (effective):**", f"{emc or '—'}" + (f" (`{emid}`)" if emid else ""))
        st.write("**Prompts:**", len(list(st.session_state.get("pp_prompts") or [])))
        comps = [str(st.session_state.get(f"na_c{i}") or "").strip() for i in range(1, MAX_COMPETITORS + 1)]
        comps = [c for c in comps if c]
        st.write("**Competitors (saved for probes / optional crawl):**", ", ".join(comps) if comps else "(none)")
        st.info(
            "Competitor URLs are **not** crawled on this first run. After the report opens, go to **Competitor comparison** "
            "and use **Crawl competitors & refresh report** when you want peer scores in the HTML."
        )
        st.number_input(
            "Max sitemap URLs",
            min_value=5,
            max_value=120,
            value=int(st.session_state.get("wiz_max_urls") or 40),
            step=1,
            key="wiz_max_urls",
            help="Maps to create-report.py --max-sitemap-urls.",
        )
        st.slider(
            "Request delay (s)",
            0.05,
            1.0,
            value=float(st.session_state.get("wiz_delay") or 0.2),
            key="wiz_delay",
        )
        st.text_input(
            "Output folder (--out)",
            value=str(st.session_state.get("wiz_out_base") or DEFAULT_OUT_BASE),
            key="wiz_out_base",
        )
        with st.expander("Shortcuts (no new crawl)"):
            dcol, lcol = st.columns(2)
            with dcol:
                if st.button("Open bundled sample report", key="wiz_demo"):
                    demo_path = _resolved_sample_audit_dir()
                    if demo_path is None:
                        st.error("No sample audit on disk—see **SAMPLE_AUDIT_RELS** in streamlit_app.py.")
                    else:
                        apply_audit_to_session(demo_path, cr, [])
                        st.session_state.pop("onboarding_step", None)
                        st.session_state["ui_view"] = "report"
                        st.rerun()
            with lcol:
                if st.button("Load latest local audit", key="wiz_latest"):
                    lp = find_latest_primary_audit()
                    if lp and (lp / "audit_summary.json").is_file():
                        try:
                            apply_audit_to_session(lp, cr, [])
                            st.session_state.pop("onboarding_step", None)
                            st.session_state["ui_view"] = "report"
                            st.rerun()
                        except (FileNotFoundError, OSError, json.JSONDecodeError) as e:
                            st.error(str(e))
                    else:
                        st.warning("No primary audit found under audit_output/.")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("← Back", key="wiz_s8_back"):
                st.session_state["onboarding_step"] = 7
                st.rerun()
        with b2:
            if st.button("Run audit", type="primary", key="wiz_s8_run"):
                comps_chk = [str(st.session_state.get(f"na_c{i}") or "").strip() for i in range(1, MAX_COMPETITORS + 1)]
                comps_chk = [c for c in comps_chk if c]
                if len(comps_chk) > MAX_COMPETITORS:
                    st.error(f"At most {MAX_COMPETITORS} competitors.")
                else:
                    prog = st.progress(0.02, text="Starting…")
                    try:
                        with st.status("Running crawl, scoring, and HTML report…", expanded=False):
                            prog.progress(0.12, text="Launching create-report pipeline (this may take several minutes)…")
                            _execute_geo_audit_from_wizard(cr, progress_below_header)
                            prog.progress(1.0, text="Complete")
                        st.session_state.pop("onboarding_step", None)
                        st.session_state["ui_view"] = "report"
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
        return


def _urls_for_competitor_crawl_from_session() -> list[str]:
    lines = [ln.strip() for ln in str(st.session_state.get("pp_comp") or "").splitlines() if ln.strip()]
    if lines:
        return lines[:MAX_COMPETITORS]
    out: list[str] = []
    for i in range(1, MAX_COMPETITORS + 1):
        u = str(st.session_state.get(f"na_c{i}") or "").strip()
        if u:
            out.append(u)
    return out[:MAX_COMPETITORS]


def _render_competitor_crawl_optional_panel(cr: Any, audit_dir: Path) -> None:
    """Optional full re-crawl including peer sites (initial wizard run skips competitor crawls)."""
    has_cmp = (audit_dir / "comparison.json").is_file()
    if has_cmp:
        st.success(
            "**Competitor comparison** data is on disk—you can review it in the embedded report tab below."
        )
    else:
        st.info(
            "The default audit run **does not crawl competitor sites** (faster). Crawl peers here when you want "
            "scores and notes in **Competitor comparison**."
        )
    urls = _urls_for_competitor_crawl_from_session()
    if not urls:
        st.warning(
            "No competitor URLs on file. Add them under **Prompt performance** (one URL per line) or re-run "
            "**New audit** and enter peers in setup."
        )
        return
    st.caption("URLs to crawl: " + ", ".join(urls))
    if st.button(
        "Crawl competitors & refresh report (full crawl, several minutes)",
        type="primary",
        key="report_comp_crawl_btn",
    ):
        try:
            audit_sum = load_audit_summary(audit_dir)
            primary = str(audit_sum.get("base_url") or "").strip()
            if not primary:
                st.error("Could not read primary **base_url** from audit_summary.json.")
                return
            out_base = _out_base_from_audit_dir(audit_dir)
            max_urls = int(st.session_state.get("wiz_max_urls") or 40)
            delay = float(st.session_state.get("wiz_delay") or 0.2)
            ai = audit_sum.get("audit_inputs") or {}
            brand_name = str(ai.get("brand") or st.session_state.get("pp_brand") or "").strip()
            industry = str(ai.get("industry") or st.session_state.get("na_industry") or "").strip()
            prop_id = (st.session_state.get("ga4_selected_property_id") or "").strip()
            ch_raw = st.session_state.get("ga4_ai_channels_input")
            ga4_ch_arg: str | None = None if ch_raw is None else (str(ch_raw).strip() or None)
            ga4_tmp: Path | None = None
            cred_dict = st.session_state.get("ga4_user_creds_dict")
            try:
                if cred_dict and prop_id:
                    import ga4_oauth as g4o

                    creds = g4o.credentials_from_dict(cred_dict)
                    ga4_tmp = g4o.write_temp_application_default_user_json(creds)
                with st.status("Running create-report with competitors…", expanded=True):
                    adir = run_full_pipeline(
                        primary,
                        urls,
                        out_base,
                        max_urls,
                        delay,
                        brand_name=brand_name,
                        industry=industry,
                        ga4_property_id=prop_id if prop_id else None,
                        ga4_ai_channels=ga4_ch_arg,
                        ga4_oauth_credentials_path=str(ga4_tmp) if ga4_tmp else None,
                    )
                apply_audit_to_session(adir, cr, urls)
                write_onboarding_context(adir)
                st.success("Competitor crawl finished—report reloaded.")
                st.rerun()
            finally:
                if ga4_tmp is not None:
                    try:
                        ga4_tmp.unlink(missing_ok=True)
                    except OSError:
                        pass
        except Exception as e:
            st.error(str(e))


def _prepare_embedded_report_html(html_doc: str, active_tab: str) -> str:
    """Hide in-frame horizontal tabs and open ``data-tab-panel`` matching ``active_tab`` (sidebar-driven)."""
    tab = active_tab if active_tab in REPORT_GEO_TAB_IDS else "summary"
    tab_js = json.dumps(tab)
    hide = (
        '<style id="geo-app-hide-report-tabs">.report-tabs-wrap{display:none!important;}'
        "body.geo-report{min-height:0!important;}html{height:auto!important;}"
        "</style>"
        '<style id="geo-app-action-columns-start">'
        "body.geo-report .action-columns{align-items:start!important;}"
        "</style>"
    )
    script = (
        "<script>(function(){var target="
        + tab_js
        + ";var tabs=document.querySelectorAll('.report-tab[data-tab-target]');"
        "var panels=document.querySelectorAll('[data-tab-panel]');"
        "if(!tabs.length||!panels.length)return;"
        "function show(t){tabs.forEach(function(b){var on=b.getAttribute('data-tab-target')===t;"
        "b.setAttribute('aria-selected',on?'true':'false');});"
        "panels.forEach(function(p){var on=p.getAttribute('data-tab-panel')===t;"
        "p.toggleAttribute('hidden',!on);p.classList.toggle('is-active',on);});"
        "try{if(history.replaceState)history.replaceState(null,'','#'+t);}catch(e){}}"
        "show(target);})();</script>"
    )
    out, n = re.subn(re.compile(r"(</body>)", re.I), hide + script + r"\1", html_doc, count=1)
    if n:
        return out
    return hide + html_doc + script


def _render_report_section_sidebar() -> None:
    """Left rail: GEO report panels (same as embedded ``report.html`` tabs) + Prompt performance."""
    st.session_state.setdefault("report_section", "summary")
    current = str(st.session_state.get("report_section") or "summary")
    with st.sidebar:
        _sidebar_home_button(key_suffix="report")
        with st.container(border=True):
            for sec_id, label in REPORT_SIDEBAR_GEO_SECTIONS:
                active = current == sec_id
                if st.button(
                    label,
                    key=f"repsec_{sec_id}",
                    type="primary" if active else "secondary",
                    use_container_width=True,
                ):
                    if current != sec_id:
                        st.session_state["report_section"] = sec_id
                        st.rerun()
            active_p = current == REPORT_SECTION_PROMPTS
            if st.button(
                "Prompt performance",
                key="repsec_prompts",
                type="primary" if active_p else "secondary",
                use_container_width=True,
            ):
                if current != REPORT_SECTION_PROMPTS:
                    st.session_state["report_section"] = REPORT_SECTION_PROMPTS
                    st.rerun()
            sid_samples, lab_samples = REPORT_SIDEBAR_SAMPLES_SECTION
            active_samples = current == sid_samples
            if st.button(
                lab_samples,
                key=f"repsec_{sid_samples}",
                type="primary" if active_samples else "secondary",
                use_container_width=True,
            ):
                if current != sid_samples:
                    st.session_state["report_section"] = sid_samples
                    st.rerun()
        if current_app_env() != "production":
            st.caption(f"Environment: {app_env_display_label()}")


def _render_sidebar_navigation(current: str) -> None:
    """App-level nav when not on the report view; report view uses :func:`_render_report_section_sidebar`."""
    has_report = bool(st.session_state.get("audit_dir") and st.session_state.get("audit"))
    if current == "report":
        _render_report_section_sidebar()
        return

    with st.sidebar:
        _sidebar_home_button(key_suffix="app")
        with st.container(border=True):
            nav_items: list[tuple[str, str, str]] = [
                ("new_audit", "New audit", "＋"),
                ("existing", "Existing audits", "▤"),
                ("report", "Report", "📄"),
            ]
            for vid, label, icon in nav_items:
                active = current == vid
                if vid == "report" and not has_report:
                    st.button(
                        f"{icon}  {label}",
                        key=f"nav_{vid}",
                        disabled=True,
                        use_container_width=True,
                        help="Run an audit or load demo / latest output to open the report.",
                    )
                    continue
                if st.button(
                    f"{icon}  {label}",
                    key=f"nav_{vid}",
                    type="primary" if active else "secondary",
                    use_container_width=True,
                ):
                    if vid == current:
                        continue
                    st.session_state["ui_view"] = vid
                    if vid == "new_audit":
                        st.session_state["onboarding_step"] = 3
                        st.session_state.pop("wiz_ga4_after_yes", None)
                        st.session_state.pop("ga4_oauth_authorize_url", None)
                        _reset_new_audit_wizard_state()
                    st.rerun()
        if current_app_env() != "production":
            st.caption(f"Environment: {app_env_display_label()}")


def main() -> None:
    st.set_page_config(
        page_title="GEO Audit",
        page_icon="◆",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _bootstrap_streamlit_secrets_from_repo()
    st.markdown(_streamlit_report_root_css() + _STREAMLIT_THEME_CSS, unsafe_allow_html=True)
    ensure_llm_env_from_streamlit_secrets()
    cr = get_create_report()
    ga4_oauth_try_callback()

    if "follow_latest_audit" not in st.session_state:
        st.session_state["follow_latest_audit"] = True
    st.session_state.setdefault("report_section", "summary")
    st.session_state.setdefault("ui_view", "landing")
    st.session_state.setdefault("landing_step", 1)

    _industries: list[str] = list(getattr(cr, "COMMON_INDUSTRIES", ()))
    if not _industries:
        _industries = ["Auto & Vehicles", "Shopping", "Other Business Activity"]

    ui_view = st.session_state["ui_view"]
    audit_dir = st.session_state.get("audit_dir")
    audit = st.session_state.get("audit")

    if ui_view == "report" and (not audit_dir or not audit):
        _go_home()
        st.rerun()

    # —— Landing —— steps 1–2: account, then new vs existing audit
    if ui_view == "landing":
        _render_landing_sidebar_hide()
        st.session_state.setdefault("landing_step", 1)
        lstep = int(st.session_state.get("landing_step") or 1)
        _render_setup_progress(min(lstep, 2), title="Account & audit choice")

        _, cx, _ = st.columns([1, 2, 1])
        with cx:
            st.markdown(
                '<div class="geo-landing-hero"><h1>GEO Audit</h1>'
                '<p class="geo-landing-sub">Guided setup, then a full crawl and scored report—including a '
                "**Prompt performance** section in the report.</p></div>",
                unsafe_allow_html=True,
            )
            st.markdown("#### Quick open")
            st.caption("Open a report without running a new crawl.")
            q1, q2 = st.columns(2)
            with q1:
                if st.button("Load latest local audit", use_container_width=True, key="land_q_latest"):
                    _try_landing_quick_open_audit(cr, demo=False)
            with q2:
                if st.button("Open bundled sample report", use_container_width=True, key="land_q_demo"):
                    _try_landing_quick_open_audit(cr, demo=True)
            local_audits = list_primary_audit_dirs()
            if local_audits:
                with st.expander("Browse audits on this machine", expanded=False):
                    st.caption(
                        "Newest first. **Load latest** opens whichever primary folder was modified most recently; "
                        "use a row below to open a specific site (e.g. Euro Car Parts)."
                    )
                    for path, base_url, mtime in local_audits:
                        when = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                        if st.button(
                            f"{base_url} — {when}",
                            key=f"land_audit_{path.name}",
                            use_container_width=True,
                        ):
                            _open_audit_in_report_view(cr, path, follow_latest=False)
            login_fn = getattr(st, "login", None)
            email = current_user_email()
            if lstep == 1:
                st.markdown("#### Step 1 — Google account")
                st.caption(
                    "Sign in with Google to attach new audits to your identity and reopen them from **Existing audits**."
                )
                if email:
                    st.success(f"Signed in as **{email}**")
                elif login_fn is not None:
                    if st.button("Sign in with Google", type="primary", use_container_width=True, key="land_signin"):
                        try:
                            login_fn()
                        except StreamlitAuthError as e:
                            st.error(str(e))
                            _render_streamlit_auth_troubleshooting(
                                expanded="configure credentials" in str(e).lower()
                            )
                else:
                    st.caption(
                        "Google sign-in is not configured (`[auth]` in secrets). You can still run audits locally."
                    )
                if st.button("Continue", type="primary", use_container_width=True, key="land_step1_go"):
                    st.session_state["landing_step"] = 2
                    st.rerun()
            else:
                st.markdown("#### Step 2 — Choose an audit path")
                if email:
                    st.caption(f"Signed in as **{email}**")
                b1, b2 = st.columns(2)
                with b1:
                    if st.button("New audit", use_container_width=True, type="primary", key="land_new"):
                        st.session_state["ui_view"] = "new_audit"
                        st.session_state["onboarding_step"] = 3
                        st.session_state.pop("wiz_ga4_after_yes", None)
                        st.session_state.pop("ga4_oauth_authorize_url", None)
                        _reset_new_audit_wizard_state()
                        st.rerun()
                with b2:
                    if st.button("Existing audits", use_container_width=True, key="land_exist"):
                        st.session_state["ui_view"] = "existing"
                        st.rerun()
                if st.button("← Back", key="land_back2"):
                    st.session_state["landing_step"] = 1
                    st.rerun()
        return

    # —— New audit wizard ——
    if ui_view == "new_audit":
        _render_sidebar_navigation(ui_view)
        _render_geo_page_header()
        below_header_pipeline = st.empty()
        st.subheader("New audit setup")
        render_new_audit_setup_wizard(cr, _industries, below_header_pipeline)
        return

    # —— Existing audits (Google SSO) ——
    if ui_view == "existing":
        _render_sidebar_navigation(ui_view)
        _render_geo_page_header()
        email = current_user_email()
        login_fn = getattr(st, "login", None)

        if not email:
            _render_sso_signin_screen(login_fn)
            return

        st.subheader("Your audits")

        st.caption(f"Signed in as **{email}**")
        lo = getattr(st, "logout", None)
        if lo is not None:
            st.button("Sign out", key="geo_sign_out", on_click=lo)

        user_runs = runs_for_user(email)
        if not user_runs:
            st.info(
                "No audits are saved for this account yet. Run **New audit** while signed in "
                "to attach runs to your Google identity."
            )
        else:
            for i, r in enumerate(user_runs):
                brand = r.get("brand_name") or r.get("site_key", "—")
                when = (r.get("created_at") or "")[:19].replace("T", " ")
                score = r.get("overall_score", "—")
                primary_u = r.get("primary_url", "")
                label = f"{score} / 100 — {brand} — {primary_u} — {when}"
                if st.button(label, key=f"user_run_{r.get('id', i)}", use_container_width=True):
                    p = resolve_audit_dir(r["audit_dir"])
                    if (p / "audit_summary.json").is_file():
                        apply_audit_to_session(p, cr, r.get("competitors") or [])
                        st.session_state["ui_view"] = "report"
                        st.rerun()
                    else:
                        st.error("That audit folder is missing on disk.")
        return

    # —— Report view ——
    if ui_view != "report":
        st.session_state["ui_view"] = "landing"
        st.rerun()

    _render_sidebar_navigation(ui_view)

    st.session_state["follow_latest_audit"] = st.checkbox(
        "Follow newest crawl in audit_output/",
        value=st.session_state.get("follow_latest_audit", True),
        help="When on, switch to a newer primary audit under audit_output/ after a local run.",
    )

    if (
        st.session_state.get("follow_latest_audit")
        and audit_dir
        and audit
        and _is_under_audit_output(audit_dir)
    ):
        latest = find_latest_primary_audit()
        if latest and latest.resolve() != audit_dir.resolve():
            try:
                lm = (latest / "audit_summary.json").stat().st_mtime
                cm = (audit_dir / "audit_summary.json").stat().st_mtime
                if lm > cm:
                    apply_audit_to_session(latest, cr, [])
                    audit_dir = st.session_state.get("audit_dir")
                    audit = st.session_state.get("audit")
            except OSError:
                pass

    rs = str(st.session_state.get("report_section") or "summary")
    if rs not in REPORT_GEO_TAB_IDS and rs != REPORT_SECTION_PROMPTS:
        st.session_state["report_section"] = "summary"
        rs = "summary"

    if rs == REPORT_SECTION_PROMPTS:
        render_prompt_performance_page(for_report_tab=True)
    else:
        if isinstance(audit_dir, Path) and rs == "ga4-traffic":
            _render_report_ga4_traffic_insights_cta(audit_dir)
        ensure_report_html(audit_dir, cr)
        report_path = audit_dir / "report.html"
        if not report_path.is_file():
            st.error("report.html not found and could not be generated.")
        else:
            raw_html = report_path.read_text(encoding="utf-8", errors="replace")
            if rs == "competitors":
                _render_competitor_crawl_optional_panel(cr, audit_dir)
            html_doc = _prepare_embedded_report_html(raw_html, rs)
            st.iframe(html_doc, height="content", width="stretch")

        slides_path = audit_dir / "report_slides.html"
        if slides_path.is_file():
            st.caption(
                f"Slide deck on disk: `{slides_path.relative_to(REPO_ROOT)}` — open in a browser if needed."
            )


if __name__ == "__main__":
    main()
