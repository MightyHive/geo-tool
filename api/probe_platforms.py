"""Live probe platform availability (exclude on fatal API errors)."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PLATFORM_KEYS: tuple[str, ...] = ("gemini", "openai", "claude")

_ROW_FIELDS: dict[str, tuple[str, ...]] = {
    "gemini": (
        "gemini_response",
        "error_gemini",
        "mention_scores_gemini",
        "gemini_brand_mention_pct",
        "gemini_competitor_mention_pct",
    ),
    "openai": (
        "openai_response",
        "error_openai",
        "mention_scores_openai",
        "openai_brand_mention_pct",
        "openai_competitor_mention_pct",
    ),
    "claude": (
        "claude_response",
        "error_claude",
        "mention_scores_claude",
        "claude_brand_mention_pct",
        "claude_competitor_mention_pct",
    ),
}

_FATAL_HTTP_CODES = frozenset({400, 401, 402, 403, 429})
_FATAL_MESSAGE_HINTS = (
    "usage limit",
    "usage limits",
    "quota",
    "billing",
    "exceeded",
    "insufficient",
    "credit",
    "payment",
    "disabled",
    "invalid_request_error",
    "rate_limit",
    "rate limit",
)


def _data_root() -> Path:
    raw = (os.environ.get("GEO_DATA_ROOT") or "").strip()
    if raw:
        return Path(raw).resolve()
    return Path(__file__).resolve().parent.parent


def _state_path() -> Path:
    return _data_root() / "probe_excluded_platforms.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_excluded_platforms() -> set[str]:
    state = _load_state()
    excluded = state.get("excluded") or {}
    if not isinstance(excluded, dict):
        return set()
    return {str(k).strip().lower() for k in excluded if str(k).strip().lower() in PLATFORM_KEYS}


def exclude_platform(platform: str, reason: str) -> None:
    pk = (platform or "").strip().lower()
    if pk not in PLATFORM_KEYS:
        return
    state = _load_state()
    excluded = state.setdefault("excluded", {})
    if not isinstance(excluded, dict):
        excluded = {}
        state["excluded"] = excluded
    excluded[pk] = {
        "excluded_at": datetime.now(UTC).isoformat(),
        "reason": (reason or "")[:2000],
    }
    _save_state(state)


def is_fatal_platform_error(platform: str, err: str) -> bool:
    """True when the platform should be dropped for this service (quota, auth, billing, etc.)."""
    msg = (err or "").strip()
    if not msg:
        return False
    m = re.search(r"HTTP\s+(\d{3})", msg, flags=re.IGNORECASE)
    if m:
        code = int(m.group(1))
        if code in _FATAL_HTTP_CODES:
            return True
        if code == 400 and any(h in msg.lower() for h in _FATAL_MESSAGE_HINTS):
            return True
    lowered = msg.lower()
    if any(h in lowered for h in _FATAL_MESSAGE_HINTS):
        return True
    if "authentication" in lowered or "unauthorized" in lowered:
        return True
    return False


def active_platform_keys(excluded: set[str] | None = None) -> list[str]:
    blocked = excluded if excluded is not None else get_excluded_platforms()
    return [pk for pk in PLATFORM_KEYS if pk not in blocked]


def strip_platform_from_row(row: dict[str, Any], platform: str) -> None:
    for key in _ROW_FIELDS.get(platform, ()):
        row.pop(key, None)


def sanitize_live_probe(live: dict[str, Any]) -> dict[str, Any]:
    """Remove excluded platform data and attach ``active_platforms`` / ``excluded_platforms``."""
    if not isinstance(live, dict):
        return live
    excluded = set(get_excluded_platforms())
    saved = live.get("excluded_platforms")
    if isinstance(saved, list):
        excluded.update(str(x).strip().lower() for x in saved if str(x).strip())

    per = live.get("per_prompt")
    if isinstance(per, list):
        for row in per:
            if not isinstance(row, dict):
                continue
            for pk in PLATFORM_KEYS:
                err = str(row.get(f"error_{pk}") or "").strip()
                if err and is_fatal_platform_error(pk, err):
                    exclude_platform(pk, err)
                    excluded.add(pk)
            for pk in excluded:
                strip_platform_from_row(row, pk)

    from prompt_suggest import aggregate_live_sov

    if isinstance(per, list):
        live["aggregate"] = aggregate_live_sov([r for r in per if isinstance(r, dict)], excluded=excluded)
    elif isinstance(live.get("aggregate"), dict):
        agg = dict(live["aggregate"])
        for pk in excluded:
            agg.pop(pk, None)
        live["aggregate"] = agg

    active = [pk for pk in PLATFORM_KEYS if pk not in excluded]
    live["excluded_platforms"] = sorted(excluded)
    live["active_platforms"] = active
    return live
