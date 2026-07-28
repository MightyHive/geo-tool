"""Persist and serve pillar score history for Overview time-series charts."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from api import geo_services as geo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/audits", tags=["score-history"])

_HISTORY_DIR = "score_history"
_INDEX_FILE = "score_history_index.json"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_index(audit_dir: Path) -> dict[str, Any]:
    return _read_json(audit_dir / _INDEX_FILE) or {"schema_version": 1, "entries": []}


def _save_index(audit_dir: Path, index: dict[str, Any]) -> None:
    _write_json(audit_dir / _INDEX_FILE, index)


def _competitor_score_rows(audit_dir: Path) -> list[dict[str, Any]]:
    try:
        comparison = geo.load_competitive_comparison(audit_dir)
    except Exception:
        log.exception("score_history: competitive comparison failed for %s", audit_dir)
        return []
    rows = comparison.get("rows") if isinstance(comparison, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("is_primary"):
            continue
        name = str(row.get("name") or row.get("brand") or "").strip()
        website = str(row.get("website") or row.get("url") or "").strip()
        if not name and not website:
            continue
        out.append(
            {
                "name": name or website,
                "website": website,
                "overall": row.get("overall"),
                "ai_visibility": row.get("ai_visibility"),
                "technical_setup": row.get("technical_setup"),
                "content_structure": row.get("content_quality")
                if row.get("content_quality") is not None
                else row.get("content_structure"),
            }
        )
    return out


def save_score_snapshot(audit_dir: Path, *, source: str = "manual") -> str:
    """Snapshot current integrated (+ competitor) scores for today. Returns ISO date."""
    today = _today()
    created_at = _utc_now()
    try:
        scores = geo.load_integrated_scores(audit_dir)
    except Exception:
        log.exception("score_history: load_integrated_scores failed for %s", audit_dir)
        scores = {}

    entry = {
        "date": today,
        "created_at": created_at,
        "source": source,
        "overall": scores.get("overall"),
        "ai_visibility": scores.get("ai_visibility"),
        "technical_setup": scores.get("technical_setup"),
        "content_structure": scores.get("content_structure"),
        "competitors": _competitor_score_rows(audit_dir),
    }

    hist_dir = audit_dir / _HISTORY_DIR
    hist_dir.mkdir(parents=True, exist_ok=True)
    _write_json(hist_dir / f"{today}.json", entry)

    index = _load_index(audit_dir)
    entries: list[dict[str, Any]] = [
        e for e in (index.get("entries") or []) if isinstance(e, dict) and e.get("date") != today
    ]
    # Keep index lean (chart needs series, not full competitor rationale).
    entries.append(
        {
            "date": today,
            "created_at": created_at,
            "source": source,
            "overall": entry["overall"],
            "ai_visibility": entry["ai_visibility"],
            "technical_setup": entry["technical_setup"],
            "content_structure": entry["content_structure"],
            "competitors": entry["competitors"],
        }
    )
    entries.sort(key=lambda e: str(e.get("date") or ""), reverse=True)
    index["schema_version"] = 1
    index["entries"] = entries
    _save_index(audit_dir, index)
    return today


def _load_history_entries(audit_dir: Path, *, persist_repairs: bool = True) -> list[dict[str, Any]]:
    index = _load_index(audit_dir)
    entries = [e for e in (index.get("entries") or []) if isinstance(e, dict)]
    if entries:
        return sorted(entries, key=lambda e: str(e.get("date") or ""))

    hist_dir = audit_dir / _HISTORY_DIR
    if not hist_dir.is_dir():
        return []

    repaired_entries: list[dict[str, Any]] = []
    for path in sorted(hist_dir.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entry, dict):
            continue
        date = str(entry.get("date") or path.stem).strip()
        if not date:
            continue
        entry = {**entry, "date": date}
        if "created_at" not in entry:
            entry["created_at"] = _utc_now()
        repaired_entries.append(entry)

    if not repaired_entries:
        return []

    repaired_entries.sort(key=lambda e: str(e.get("date") or ""))
    if persist_repairs:
        index["schema_version"] = 1
        index["entries"] = repaired_entries
        _save_index(audit_dir, index)
    return repaired_entries


def get_score_history_entries(audit_dir: Path) -> list[dict[str, Any]]:
    entries = _load_history_entries(audit_dir)
    if entries:
        return entries

    # Synthesize a single point from current scores so charts aren't empty.
    try:
        scores = geo.load_integrated_scores(audit_dir)
    except Exception:
        return []
    if not any(scores.get(k) is not None for k in ("overall", "ai_visibility", "technical_setup", "content_structure")):
        return []
    return [
        {
            "date": _today(),
            "created_at": _utc_now(),
            "source": "live",
            "overall": scores.get("overall"),
            "ai_visibility": scores.get("ai_visibility"),
            "technical_setup": scores.get("technical_setup"),
            "content_structure": scores.get("content_structure"),
            "competitors": _competitor_score_rows(audit_dir),
        }
    ]


@router.get("/{audit_id}/score-history")
def get_score_history(audit_id: str) -> dict[str, Any]:
    try:
        audit_dir = geo.resolve_audit_dir(audit_id)
    except Exception as exc:
        raise HTTPException(404, str(exc)) from exc
    entries = get_score_history_entries(audit_dir)
    return {"entries": entries, "count": len(entries)}
