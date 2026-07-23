#!/usr/bin/env python3
"""CLI: run AI impact estimate on a local combined panel."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "backend") not in sys.path:
    sys.path.insert(0, str(_REPO / "backend"))

from ai_impact.estimate import estimate_ai_impact  # noqa: E402
from ai_impact.panel import build_weekly_panel  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="AI impact estimate (local panel)")
    p.add_argument("--panel", type=Path, required=True, help="GA4 weekly combined CSV")
    p.add_argument("--gsc", type=Path, default=None)
    p.add_argument("--window-weeks", type=int, default=13)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()

    panel = build_weekly_panel(args.panel, gsc=args.gsc)
    result = estimate_ai_impact(panel, window_weeks=args.window_weeks)
    text = json.dumps(result.as_dict(), indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    print(text)


if __name__ == "__main__":
    main()
