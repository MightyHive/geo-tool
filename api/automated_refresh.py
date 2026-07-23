"""Eligibility helpers for automated daily/weekly audit refresh jobs."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from api import geo_services as geo

log = logging.getLogger(__name__)

# Audits created on or after this date are included in automated refresh.
AUTOMATED_TRACKING_SINCE = date(2026, 7, 22)


def _parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text.split("T", 1)[0])
    except ValueError:
        return None


def audit_created_date(audit_dir: Path) -> date | None:
    """Best-effort creation date for an audit directory."""
    summary_path = audit_dir / "audit_summary.json"
    if summary_path.is_file():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
            parsed = _parse_iso_date(summary.get("created_at") if isinstance(summary, dict) else None)
            if parsed:
                return parsed
        except (OSError, json.JSONDecodeError):
            pass

    status_path = audit_dir / "audit_run_status.json"
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(status, dict):
                for key in ("started_at", "created_at"):
                    parsed = _parse_iso_date(status.get(key))
                    if parsed:
                        return parsed
        except (OSError, json.JSONDecodeError):
            pass

    try:
        archive = geo.load_archive()
    except Exception:
        archive = {"runs": []}
    rel = geo.audit_dir_api_rel(audit_dir)
    folder = audit_dir.name
    earliest: date | None = None
    for run in archive.get("runs") or []:
        if not isinstance(run, dict):
            continue
        run_dir = str(run.get("audit_dir") or "")
        if folder not in run_dir and rel not in run_dir and audit_dir.name not in run_dir:
            continue
        parsed = _parse_iso_date(run.get("created_at"))
        if parsed and (earliest is None or parsed < earliest):
            earliest = parsed
    if earliest:
        return earliest

    # Last resort: directory mtime (may refresh on writes — only used if nothing else).
    try:
        return datetime.fromtimestamp(audit_dir.stat().st_mtime, tz=timezone.utc).date()
    except OSError:
        return None


def ensure_audit_created_at(audit_dir: Path) -> date | None:
    """Persist created_at onto audit_summary when missing and discoverable."""
    created = audit_created_date(audit_dir)
    if created is None:
        return None
    summary_path = audit_dir / "audit_summary.json"
    if not summary_path.is_file():
        return created
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(summary, dict):
            return created
        if summary.get("created_at"):
            return created
        summary["created_at"] = f"{created.isoformat()}T00:00:00+00:00"
        summary_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError, TypeError):
        log.exception("Failed to backfill created_at for %s", audit_dir)
    return created


def is_automated_tracking_eligible(audit_dir: Path) -> bool:
    """True when the audit should receive automated daily/weekly refreshes."""
    ensure_audit_created_at(audit_dir)
    created = audit_created_date(audit_dir)
    if created is None:
        return False
    return created >= AUTOMATED_TRACKING_SINCE


def rebuild_full_audit_crawl_payload(audit_dir: Path) -> dict[str, Any]:
    """Rebuild a full_audit Cloud Run Job payload from persisted wizard/onboarding files."""
    summary = geo.load_audit_summary(audit_dir) or {}
    ob: dict[str, Any] = {}
    ob_path = audit_dir / "onboarding_context.json"
    if ob_path.is_file():
        try:
            raw = json.loads(ob_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(raw, dict):
                ob = raw
        except (OSError, json.JSONDecodeError):
            ob = {}

    primary = str(summary.get("base_url") or ob.get("brand_website_used") or "").strip()
    brand_name = str(ob.get("brand_name_used") or "").strip()
    industry = str(ob.get("industry_used") or "").strip()
    market_country = str(ob.get("geo_market_country") or "").strip()
    market_code = str(ob.get("geo_market_country_code") or "").strip()

    competitors: list[str] = []
    detail = ob.get("competitors_detail") or []
    if isinstance(detail, list):
        for row in detail:
            if not isinstance(row, dict):
                continue
            url = str(row.get("competitor_website") or row.get("website") or "").strip()
            if url:
                competitors.append(url)
    if not competitors:
        for item in ob.get("competitors") or []:
            url = str(item).strip() if not isinstance(item, dict) else str(
                item.get("competitor_website") or item.get("website") or ""
            ).strip()
            if url:
                competitors.append(url)

    crawl_urls = ob.get("crawl_urls") if isinstance(ob.get("crawl_urls"), list) else None
    products = ob.get("pss_rows") or ob.get("products_and_services_rows") or []

    body_dict: dict[str, Any] = {
        "base_url": primary,
        "brand_name": brand_name,
        "industry": industry,
        "wizard_market_country": market_country,
        "wizard_market_country_code": market_code,
        "competitors": competitors,
        "crawl_urls": crawl_urls,
        "max_urls": int(ob.get("max_urls") or 40),
        "delay": float(ob.get("delay") or 0.2),
        "skip_prompt_probes": True,
        "products_and_services_rows": products if isinstance(products, list) else [],
        "wizard_prompt_locales": ob.get("prompt_locales") or [],
        "wizard_competitors_detail": detail if isinstance(detail, list) else [],
    }

    # Always follow on when competitors are configured (no-op path if empty).
    body_dict["follow_on_competitor_crawl"] = bool(competitors)

    return {
        "primary": primary,
        "competitors": competitors,
        "body_dict": body_dict,
        "ga4_prop": str(ob.get("ga4_property_id") or "") or None,
        "ga4_ch": str(ob.get("ga4_ai_channel_names") or "") or None,
        "owner_email": None,
        "notification_email": str(ob.get("notification_email") or "") or None,
        "follow_on_competitor_crawl": body_dict["follow_on_competitor_crawl"],
    }
