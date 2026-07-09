#!/usr/bin/env python3
"""Move flat ``data/<slug>_20220601/`` folders into publisher/advertiser hierarchy."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

MANUAL_ROOT = Path(__file__).resolve().parent
if str(MANUAL_ROOT) not in sys.path:
    sys.path.insert(0, str(MANUAL_ROOT))

from term_registry import data_dir_for_slug, lookup_slug, start_tag  # noqa: E402


def _flat_dirs(root: Path) -> list[Path]:
    tag = start_tag()
    return sorted(
        p
        for p in root.glob(f"data/*_{tag}")
        if p.is_dir()
    )


def resort(*, dry_run: bool) -> int:
    moved = 0
    unmapped: list[str] = []
    log: list[dict[str, str]] = []

    for src in _flat_dirs(MANUAL_ROOT):
        slug = src.name[: -(len(start_tag()) + 1)]
        hit = lookup_slug(slug)
        if hit is None:
            unmapped.append(slug)
            print(f"UNMAPPED  {src.name} (add to term_registry.json)", flush=True)
            continue

        dest = data_dir_for_slug(slug)
        if src.resolve() == dest.resolve():
            continue
        if dest.exists():
            print(f"SKIP      {src.name} → {dest} (destination exists)", flush=True)
            continue

        print(f"MOVE      {src} → {dest}", flush=True)
        log.append({"from": str(src), "to": str(dest), "slug": slug})
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        moved += 1

    if not dry_run:
        manifest = {
            "resorted_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "moved": moved,
            "unmapped": unmapped,
            "moves": log,
        }
        out = MANUAL_ROOT / "publishers" / "_resort_manifest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"\nMoved {moved} folder(s). Unmapped: {len(unmapped)}.", flush=True)
    if unmapped:
        print("Unmapped slugs:", ", ".join(unmapped), flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return resort(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
