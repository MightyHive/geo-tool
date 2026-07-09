#!/usr/bin/env python3
"""Finalize ``trends_weekly.csv`` for every term folder in the hierarchy."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

MANUAL_ROOT = Path(__file__).resolve().parent
if str(MANUAL_ROOT) not in sys.path:
    sys.path.insert(0, str(MANUAL_ROOT))

from term_registry import list_all_data_dirs, term_from_data_dir  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-bq", action="store_true", default=True)
    args = ap.parse_args()

    ok = 0
    fail = 0
    for data_dir in list_all_data_dirs():
        term = term_from_data_dir(data_dir)
        if not term:
            print(f"SKIP {data_dir} (no reference_weekly.csv)", flush=True)
            fail += 1
            continue
        env = os.environ.copy()
        env["QUERY_TERM"] = term
        env["TRENDS_DATA_DIR"] = str(data_dir)
        cmd = [
            sys.executable,
            str(MANUAL_ROOT / "finalize_weekly.py"),
            *(["--skip-bq"] if args.skip_bq else []),
        ]
        r = subprocess.run(cmd, cwd=str(MANUAL_ROOT), env=env)
        if r.returncode == 0:
            ok += 1
        else:
            fail += 1
            print(f"FAIL {term} ({data_dir})", flush=True)

    print(f"\nFinalized {ok} term(s), {fail} failed.", flush=True)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
