#!/usr/bin/env python3
"""Sort unnamed Google Trends CSV dumps by reading the query term from each file.

Drop ``multiTimeline*.csv`` files into a folder (default: ``inbox/unsorted/``), then::

    ./run_sort_downloads.sh --from ~/Downloads
    ./run_sort_downloads.sh --from inbox/unsorted --move
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MANUAL_ROOT = Path(__file__).resolve().parent
if str(MANUAL_ROOT) not in sys.path:
    sys.path.insert(0, str(MANUAL_ROOT))

from trends_csv import (  # noqa: E402
    is_valid_trends_csv,
    read_csv_grain_and_terms,
    term_slug,
    write_single_term_csv,
)
from term_registry import data_dir_for_slug  # noqa: E402

RAW_CSV = "reference_weekly.csv"
DEFAULT_START_TAG = "20220601"


@dataclass
class SortResult:
    source: Path
    term: str
    slug: str
    dest: Path
    grain: str
    action: str  # copied | moved | skipped | split


def _start_tag() -> str:
    start = os.environ.get("START_DATE", "2022-06-01").strip()
    return start.replace("-", "") or DEFAULT_START_TAG


def _data_dir_for_slug(slug: str) -> Path:
    return data_dir_for_slug(slug)


def _inbox_dir_for_slug(slug: str) -> Path:
    return MANUAL_ROOT / "inbox" / slug


def _iter_source_csvs(source: Path, *, recursive: bool) -> list[Path]:
    if source.is_file() and source.suffix.lower() == ".csv":
        return [source]
    if recursive:
        candidates = source.rglob("multiTimeline*.csv")
    else:
        candidates = source.glob("multiTimeline*.csv")
    return sorted(p for p in candidates if p.is_file())


def sort_file(
    path: Path,
    *,
    dry_run: bool,
    move: bool,
    overwrite: bool,
) -> list[SortResult]:
    if not is_valid_trends_csv(path):
        print(f"SKIP (not a valid Trends CSV): {path}", flush=True)
        return []

    grain, terms = read_csv_grain_and_terms(path)
    results: list[SortResult] = []

    for term, region in terms:
        slug = term_slug(term)
        data_dest = _data_dir_for_slug(slug) / RAW_CSV
        inbox_dest = _inbox_dir_for_slug(slug) / path.name

        if data_dest.exists() and not overwrite:
            print(
                f"SKIP {path.name!r} → {slug} (already have {data_dest}; use --overwrite)",
                flush=True,
            )
            results.append(
                SortResult(path, term, slug, data_dest, grain, "skipped")
            )
            continue

        action = "moved" if move else "copied"
        if len(terms) > 1:
            action = f"split_{action}"

        print(
            f"{action.upper():12} {path.name!r}  term={term!r}  →  {data_dest}",
            flush=True,
        )

        if dry_run:
            results.append(SortResult(path, term, slug, data_dest, grain, action))
            continue

        data_dest.parent.mkdir(parents=True, exist_ok=True)
        inbox_dest.parent.mkdir(parents=True, exist_ok=True)

        if len(terms) == 1:
            shutil.copy2(path, data_dest)
            shutil.copy2(path, inbox_dest)
        else:
            write_single_term_csv(path, term=term, region=region, grain=grain, dest=data_dest)
            shutil.copy2(data_dest, inbox_dest)

        results.append(SortResult(path, term, slug, data_dest, grain, action))

    if move and not dry_run and path.is_file():
        try:
            path.unlink()
        except OSError:
            pass

    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--from",
        dest="from_dir",
        type=Path,
        default=MANUAL_ROOT / "inbox" / "unsorted",
        help="Folder of unnamed CSV dumps (default: inbox/unsorted/).",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--move", action="store_true", help="Move source files after sorting.")
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing reference_weekly.csv for a term.",
    )
    ap.add_argument(
        "--recursive",
        action="store_true",
        help="Search subfolders for multiTimeline*.csv (default: top level only).",
    )
    args = ap.parse_args()

    source = args.from_dir.expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Source not found: {source}")

    files = _iter_source_csvs(source, recursive=args.recursive)
    if not files:
        raise SystemExit(f"No CSV files under {source}")

    all_results: list[SortResult] = []
    for path in files:
        all_results.extend(
            sort_file(path, dry_run=args.dry_run, move=args.move, overwrite=args.overwrite)
        )

    summary = {
        "sorted_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source": str(source),
        "files_seen": len(files),
        "terms_written": sum(1 for r in all_results if r.action != "skipped"),
        "terms_skipped": sum(1 for r in all_results if r.action == "skipped"),
        "results": [
            {
                "source": str(r.source),
                "term": r.term,
                "slug": r.slug,
                "dest": str(r.dest),
                "grain": r.grain,
                "action": r.action,
            }
            for r in all_results
        ],
    }
    if not args.dry_run:
        out = MANUAL_ROOT / "publishers" / "_sort_manifest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote manifest: {out}", flush=True)

    print(
        f"\nDone: {summary['terms_written']} term(s) sorted, "
        f"{summary['terms_skipped']} skipped, from {summary['files_seen']} file(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
