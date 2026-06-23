"""Shared helpers for Gemini-generated GEO report copy."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from geo_app_env import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "skills"


def truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def load_skill_chunk(
    path: Path,
    start_marker: str,
    *,
    end_marker: str | None = None,
    max_chars: int = 14_000,
) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    start = text.find(start_marker)
    if start < 0:
        return text[:max_chars]
    chunk = text[start:]
    if end_marker:
        end = chunk.find(end_marker, len(start_marker))
        if end > 0:
            chunk = chunk[:end]
    return chunk.strip()[:max_chars]


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def sanitize_limited_html(raw: str, *, allowed_tags: tuple[str, ...] = ("strong",)) -> str:
    """Strip wrapper <p> and disallow tags except those listed (e.g. strong)."""
    import re

    s = (raw or "").strip()
    if not s:
        return ""
    s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = re.sub(r"^<p[^>]*>\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*</p>\s*$", "", s, flags=re.IGNORECASE)
    allowed = {t.lower() for t in allowed_tags}

    parts: list[str] = []
    pos = 0
    for m in re.finditer(r"<[^>]+>", s):
        parts.append(s[pos : m.start()])
        tag = m.group(0)
        name = re.sub(r"^</?|>$", "", tag, flags=re.IGNORECASE).split()[0].lower()
        if name in allowed:
            parts.append(tag)
        pos = m.end()
    parts.append(s[pos:])
    return " ".join("".join(parts).split())
