"""Precompute HTML (and optionally PDF) export artifacts after probes complete."""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SECTIONS_DIR = "exports/sections"
MANIFEST_NAME = "manifest.json"
FULL_REPORT_NAME = "full-report.html"


def _truthy(name: str, default: str = "1") -> bool:
    raw = (os.getenv(name) or default).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def export_precompute_html_enabled() -> bool:
    return _truthy("EXPORT_PRECOMPUTE_HTML", "1")


def export_precompute_pdf_enabled() -> bool:
    return _truthy("EXPORT_PRECOMPUTE_PDF", "0")


def sections_dir(audit_dir: Path) -> Path:
    return audit_dir / SECTIONS_DIR


def section_html_path(audit_dir: Path, section_id: str) -> Path:
    safe = re.sub(r"[^\w\-]+", "-", (section_id or "").strip()).strip("-") or "section"
    return sections_dir(audit_dir) / f"{safe}.html"


def full_report_html_path(audit_dir: Path) -> Path:
    return sections_dir(audit_dir) / FULL_REPORT_NAME


def manifest_path(audit_dir: Path) -> Path:
    return sections_dir(audit_dir) / MANIFEST_NAME


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime if path.is_file() else None
    except OSError:
        return None


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _source_mtimes(audit_dir: Path) -> dict[str, float | None]:
    return {
        "audit_summary": _mtime(audit_dir / "audit_summary.json"),
        "live_probe": _mtime(audit_dir / "prompt_performance_live_probe.json"),
        "aio_probe": _mtime(audit_dir / "prompt_performance_aio.json"),
    }


def precomputed_html_is_fresh(audit_dir: Path, *, section: str | None = None) -> bool:
    """True when a precomputed HTML artifact exists and matches current probe/summary mtimes."""
    import json

    man = manifest_path(audit_dir)
    if not man.is_file():
        return False
    try:
        payload = json.loads(man.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    current = _source_mtimes(audit_dir)
    for key, cur in current.items():
        stored = sources.get(key)
        if cur is None and stored is None:
            continue
        try:
            if stored is None or abs(float(stored) - float(cur or 0)) > 0.001:
                return False
        except (TypeError, ValueError):
            return False
    target = full_report_html_path(audit_dir) if not section else section_html_path(audit_dir, section)
    return target.is_file()


def read_precomputed_section_html(audit_dir: Path, section: str) -> str | None:
    if not precomputed_html_is_fresh(audit_dir, section=section):
        return None
    path = section_html_path(audit_dir, section)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_precomputed_full_html(audit_dir: Path) -> str | None:
    if not precomputed_html_is_fresh(audit_dir):
        return None
    path = full_report_html_path(audit_dir)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def write_precomputed_section_html(audit_dir: Path) -> list[Path]:
    """Build and write standalone HTML for each full-report section + all-pages doc."""
    from api.export_builders import (
        FULL_REPORT_SECTIONS,
        build_all_pages_export,
        build_section_body,
        wrap_export_document,
    )

    written: list[Path] = []
    out_dir = sections_dir(audit_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    section_ids: list[str] = []
    for section_id, label in FULL_REPORT_SECTIONS:
        try:
            _lbl, body = build_section_body(audit_dir, section_id)
            html = wrap_export_document(label, body)
        except Exception as exc:
            log.warning("Precompute HTML failed for %s/%s: %s", audit_dir.name, section_id, exc)
            continue
        path = section_html_path(audit_dir, section_id)
        _write_text_atomic(path, html)
        written.append(path)
        section_ids.append(section_id)

    try:
        full_html = build_all_pages_export(audit_dir)
        full_path = full_report_html_path(audit_dir)
        _write_text_atomic(full_path, full_html)
        written.append(full_path)
    except Exception as exc:
        log.warning("Precompute full HTML failed for %s: %s", audit_dir.name, exc)

    _write_json_atomic(
        manifest_path(audit_dir),
        {
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "sources": _source_mtimes(audit_dir),
            "sections": section_ids,
            "full_report": FULL_REPORT_NAME,
        },
    )
    return written


def precompute_exports_after_probes(audit_dir: Path) -> dict[str, Any]:
    """
    Invalidate JSON caches, write HTML artifacts, optionally enqueue PDF.

    Safe to call from the prompt-probe job after sentiment/exec summary.
    """
    from api.audit_json_cache import invalidate_audit

    audit_dir = audit_dir.resolve()
    outcome: dict[str, Any] = {"html": "skipped", "pdf": "skipped", "paths": []}
    invalidate_audit(audit_dir)

    if export_precompute_html_enabled():
        try:
            paths = write_precomputed_section_html(audit_dir)
            outcome["html"] = "done"
            outcome["paths"] = [str(p.name) for p in paths]
            log.info(
                "Precomputed %d HTML export artifact(s) for %s",
                len(paths),
                audit_dir.name,
            )
        except Exception as exc:
            log.exception("HTML export precompute failed for %s: %s", audit_dir, exc)
            outcome["html"] = f"error: {exc}"

    if export_precompute_pdf_enabled():
        try:
            from api.export_jobs import enqueue_pdf_export

            status = enqueue_pdf_export(audit_dir, section=None)
            outcome["pdf"] = "queued"
            outcome["pdf_status"] = status
        except Exception as exc:
            log.exception("PDF export enqueue failed for %s: %s", audit_dir, exc)
            outcome["pdf"] = f"error: {exc}"

    return outcome
