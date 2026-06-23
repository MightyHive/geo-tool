"""Shared audit/archive helpers for the FastAPI layer (mirrors streamlit_app.py)."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from geo_app_env import ASSETS_ROOT, BACKEND_ROOT, REPO_ROOT, load_app_environment

load_app_environment()

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


def list_primary_audits(out_root: Path | None = None) -> list[dict[str, Any]]:
    root = (out_root or audit_output_base()).resolve()
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
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
        overall = data.get("overall_score")
        meta = load_report_meta(d)
        brand_name = str(meta.get("brand_name") or "").strip()
        favicon_url = str(meta.get("favicon_url") or "").strip()
        rows.append(
            {
                "id": d.name,
                "audit_dir": audit_dir_api_rel(d),
                "base_url": base,
                "brand_name": brand_name,
                "favicon_url": favicon_url,
                "modified_at": datetime.fromtimestamp(m, tz=UTC).isoformat(),
                "overall_score": overall,
            }
        )
    rows.sort(key=lambda r: r["modified_at"], reverse=True)
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
    'function fromHash(){var h=(location.hash||"").replace(/^#/,"");if(h)notifyParent(h);}'
    'window.addEventListener("hashchange",fromHash);fromHash();'
    "})();"
    "</script>"
)

_EMBED_CHROME_STYLE = (
    '<style id="geo-app-embed-chrome">'
    "body.geo-report.geo-report-app-embed .header,"
    "body.geo-report.geo-report-app-embed .report-tabs-wrap{display:none!important;}"
    "body.geo-report.geo-report-app-embed{background:#e8e5e0!important;}"
    "body.geo-report.geo-report-app-embed .report-main-with-tabs.container{"
    "max-width:none;padding:8px 20px 32px;width:100%;}"
    "body.geo-report.geo-report-app-embed .report-block,"
    "body.geo-report.geo-report-app-embed .section{margin-bottom:16px;}"
    "html,body.geo-report.geo-report-app-embed{min-height:0!important;height:auto!important;}"
    "</style>"
)

_REPORT_HEADER_RE = re.compile(r'<header class="header">.*?</header>\s*', re.DOTALL | re.IGNORECASE)


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
    inject = _EMBED_CHROME_STYLE + _EMBED_NAV_SCRIPT
    if "</body>" in html.lower():
        html = re.sub(r"</body>", inject + "</body>", html, count=1, flags=re.IGNORECASE)
    else:
        html = html + inject
    return html

def load_audit_summary(audit_dir: Path) -> dict[str, Any]:
    p = audit_dir / "audit_summary.json"
    if not p.is_file():
        raise FileNotFoundError(p)
    return json.loads(p.read_text(encoding="utf-8", errors="replace"))


def _score_label(score: float) -> str:
    if score >= 90:
        return "Excellent"
    if score >= 75:
        return "Good"
    if score >= 60:
        return "Moderate"
    if score >= 40:
        return "Weak"
    return "Poor"


def _score_tone(score: float) -> str:
    if score >= 75:
        return "green"
    if score >= 60:
        return "blue"
    if score >= 40:
        return "yellow"
    return "red"


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
    """Best available GEO score for an audit directory (summary, then report.html)."""
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
    """Fill in overall_score from the audit folder when the archive entry is missing or zero."""
    out = dict(run)
    try:
        archived = float(out.get("overall_score") or 0)
    except (TypeError, ValueError):
        archived = 0.0
    if archived > 0:
        out["overall_score"] = round(archived, 1)
        return out
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
    audits = list_primary_audits()
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
    competitor_urls: list[str],
    products_rows: list[dict[str, Any]],
    competitors_detail: list[dict[str, Any]],
    ga4_property_id: str = "",
    ga4_ai_channel_names: str = "",
) -> None:
    """
    Create the audit folder early (before crawl) so the report UI can load, and persist
    wizard products/prompts + competitors for prompt performance and onboarding merge.
    Clears any prior live-probe artifacts for this folder.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)
    # Drop prior run outputs so the UI does not show a stale report while re-crawling.
    for fn in (
        "report.html",
        "report_slides.html",
        "prompt_performance_live_probe.json",
        "prompt_performance_probe_pending.json",
        "prompt_performance_sentiment.json",
        "ga4_traffic.json",
        "ga4_top_pages.json",
        "ga4_ai_insights.json",
        "comparison.json",
        "comparison.md",
    ):
        (audit_dir / fn).unlink(missing_ok=True)

    rel_out = audit_dir_api_rel(audit_dir)
    stub = {
        "audit_label": "primary",
        "base_url": primary_url.strip(),
        "output_dir": rel_out,
        "overall_score": None,
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
    onboarding["accepted_competitors"] = list(competitor_urls)
    ga4_prop = ga4_property_id.strip()
    if ga4_prop:
        onboarding["ga4_property_id"] = ga4_prop
    ga4_ch = ga4_ai_channel_names.strip()
    if ga4_ch:
        onboarding["ga4_ai_channel_names"] = ga4_ch

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
        cleaned_rows.append({"product_or_service": label, "prompts": prs})
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
    ga4_property_id: str | None = None,
    ga4_ai_channels: str | None = None,
    ga4_oauth_credentials_path: str | None = None,
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
        str(ASSETS_ROOT / "samples" / "robots.txt"),
        "--sample-llms",
        str(ASSETS_ROOT / "samples" / "llms-txt-skeleton.txt"),
    ]
    if brand_name.strip():
        cmd.extend(["--brand", brand_name.strip()])
    if industry.strip():
        cmd.extend(["--industry", industry.strip()])
    if market_country.strip():
        cmd.extend(["--market-country", market_country.strip()])
    if market_country_code.strip():
        cmd.extend(["--market-country-code", market_country_code.strip()])
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


def app_config() -> dict[str, Any]:
    from geo_app_env import app_env_display_label, current_app_env

    return {
        "app_env": current_app_env(),
        "app_env_label": app_env_display_label(),
        "report_sections": [
            {"id": "summary", "label": "Summary"},
            {"id": "ga4-traffic", "label": "AI traffic (GA4)"},
            {"id": "recommendations", "label": "Recommendations"},
            {"id": "competitors", "label": "Competitor comparison"},
            {"id": "ai-visibility", "label": "AI visibility"},
            {"id": "technical", "label": "Technical setup"},
            {"id": "content", "label": "Content quality"},
            {"id": "samples", "label": "Sample scripts"},
            {"id": "prompt_performance", "label": "Prompt performance"},
        ],
    }
