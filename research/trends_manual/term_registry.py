"""Resolve term slugs to publisher/advertiser hierarchy paths."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from trends_csv import term_slug

MANUAL_ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = MANUAL_ROOT / "term_registry.json"
DEFAULT_START_TAG = "20220601"


@lru_cache(maxsize=1)
def load_registry() -> dict[str, Any]:
    raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def start_tag() -> str:
    start = os.environ.get("START_DATE", "2022-06-01").strip()
    return start.replace("-", "") or DEFAULT_START_TAG


def slug_lookup() -> dict[str, tuple[str, str, str]]:
    """Map term slug → (kind_plural, entity_id, branded|non_branded)."""
    out: dict[str, tuple[str, str, str]] = {}
    entities = load_registry().get("entities") or {}
    for entity_id, spec in entities.items():
        if not isinstance(spec, dict):
            continue
        kind = str(spec.get("kind") or "publisher").strip().lower()
        kind_plural = "advertisers" if kind == "advertiser" else "publishers"
        for bucket in ("branded", "non_branded"):
            for term in spec.get(bucket) or []:
                slug = term_slug(str(term))
                out[slug] = (kind_plural, str(entity_id), bucket)
    return out


def lookup_slug(slug: str) -> tuple[str, str, str] | None:
    return slug_lookup().get(slug)


def data_dir_for_slug(slug: str, *, manual_root: Path | None = None) -> Path:
    """``publishers/good_food/branded/good_food_20220601`` (or advertisers/…)."""
    root = manual_root or MANUAL_ROOT
    tag = start_tag()
    folder = f"{slug}_{tag}"
    hit = lookup_slug(slug)
    if hit is None:
        return root / "data" / folder
    kind_plural, entity_id, bucket = hit
    return root / kind_plural / entity_id / bucket / folder


def list_all_data_dirs(manual_root: Path | None = None) -> list[Path]:
    """Every term folder containing ``reference_weekly.csv`` or ``trends_weekly.csv``."""
    root = manual_root or MANUAL_ROOT
    dirs: list[Path] = []
    for pattern in (
        "data/*_*",
        "publishers/*/*/*_*",
        "advertisers/*/*/*_*",
    ):
        for p in root.glob(pattern):
            if p.is_dir() and (
                (p / "reference_weekly.csv").is_file() or (p / "trends_weekly.csv").is_file()
            ):
                dirs.append(p)
    # Dedupe
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in sorted(dirs):
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            unique.append(p)
    return unique


def term_from_data_dir(data_dir: Path) -> str | None:
    ref = data_dir / "reference_weekly.csv"
    if not ref.is_file():
        return None
    from trends_csv import read_csv_grain_and_terms

    _, terms = read_csv_grain_and_terms(ref)
    return terms[0][0] if terms else None
