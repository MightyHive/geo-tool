#!/usr/bin/env python3
"""Copy a weekly multiTimeline CSV from inbox/ into the term data directory."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

MANUAL_ROOT = Path(__file__).resolve().parent
if str(MANUAL_ROOT) not in sys.path:
    sys.path.insert(0, str(MANUAL_ROOT))

from trends_csv import is_valid_trends_csv  # noqa: E402

RAW_CSV = "reference_weekly.csv"


def _data_dir() -> Path:
    return Path(os.environ.get("TRENDS_DATA_DIR", str(MANUAL_ROOT / "data"))).resolve()


def _pick_csv(source: Path) -> Path:
    candidates = sorted(
        [p for p in source.iterdir() if p.is_file() and p.suffix.lower() == ".csv"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    valid = [p for p in candidates if is_valid_trends_csv(p)]
    if not valid:
        names = ", ".join(p.name for p in candidates[:5]) or "(none)"
        raise SystemExit(f"No valid Trends CSV in {source}. Found: {names}")
    if len(valid) > 1:
        print(f"Multiple valid CSVs; using newest: {valid[0].name}", flush=True)
    return valid[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="from_dir", type=Path, default=None, help="Inbox folder.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--move", action="store_true", help="Move instead of copy.")
    args = ap.parse_args()

    data = _data_dir()
    slug = os.environ.get("TERM_SLUG", "").strip()
    source = args.from_dir or (MANUAL_ROOT / "inbox" / slug if slug else MANUAL_ROOT / "inbox")
    source = source.resolve()
    if not source.is_dir():
        raise SystemExit(f"Inbox not found: {source}")

    src = _pick_csv(source)
    dest = data / RAW_CSV
    data.mkdir(parents=True, exist_ok=True)

    print(f"Match: {src.name} → {dest}", flush=True)
    if args.dry_run:
        return 0
    if args.move:
        shutil.move(str(src), str(dest))
    else:
        shutil.copy2(src, dest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
