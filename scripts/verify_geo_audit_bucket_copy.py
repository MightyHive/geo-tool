#!/usr/bin/env python3
"""Compare exported source and target GCS object manifests."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load(path: Path) -> dict[str, dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {row["name"]: row for row in rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument(
        "--exclude-suffix",
        action="append",
        default=["/.runtime/ga4_adc.json"],
    )
    args = parser.parse_args()
    source = load(args.source)
    target = load(args.target)
    for name in list(source):
        if name.endswith("/") or any(
            name.endswith(suffix) for suffix in args.exclude_suffix
        ):
            source.pop(name)
    for name in list(target):
        if name.endswith("/"):
            target.pop(name)

    missing = sorted(set(source) - set(target))
    extra = sorted(set(target) - set(source))
    mismatched = sorted(
        name
        for name in set(source) & set(target)
        if (
            source[name].get("size"),
            source[name].get("crc32c"),
            source[name].get("md5_hash"),
        )
        != (
            target[name].get("size"),
            target[name].get("crc32c"),
            target[name].get("md5_hash"),
        )
    )
    summary = {
        "source_objects": len(source),
        "target_objects": len(target),
        "missing": missing,
        "extra": extra,
        "checksum_mismatches": mismatched,
    }
    print(json.dumps(summary, indent=2))
    if missing or extra or mismatched:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
