#!/usr/bin/env python3
"""Cloud Run Job: Search Console daily clicks/impressions/position for a site."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path


def main() -> None:
    run_id = os.environ.get("RUN_ID") or datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    site_url = (os.environ.get("GSC_SITE_URL") or "").strip()
    if not site_url:
        raise SystemExit("GSC_SITE_URL is required (Search Console property URI)")
    start = os.environ.get("START_DATE") or "2025-03-01"
    end = os.environ.get("END_DATE") or datetime.utcnow().strftime("%Y-%m-%d")
    out_uri = os.environ.get("GCS_OUTPUT_URI") or f"/tmp/ai_impact_runs/{run_id}/"

    print(f"[gsc-job] run_id={run_id} site={site_url} range={start}→{end}", flush=True)
    print(
        "[gsc-job] PLACEHOLDER: wire googleapiclient searchconsole searchanalytics.query",
        flush=True,
    )
    print(
        "[gsc-job] NOTE: impressions not comparable across 2025-09-01 method change",
        flush=True,
    )

    local_dir = Path(f"/tmp/ai_impact_runs/{run_id}")
    local_dir.mkdir(parents=True, exist_ok=True)
    local = local_dir / "gsc_daily.csv"
    local.write_text("date,clicks,impressions,ctr,position\n", encoding="utf-8")
    meta = {
        "run_id": run_id,
        "site_url": site_url,
        "start": start,
        "end": end,
        "impression_break": "2025-09-01",
        "status": "placeholder",
    }
    (local_dir / "gsc_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    if not out_uri.startswith("gs://"):
        dest = Path(out_uri)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "gsc_daily.csv").write_text(local.read_text(encoding="utf-8"), encoding="utf-8")
        (dest / "gsc_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[gsc-job] wrote {dest / 'gsc_daily.csv'}", flush=True)


if __name__ == "__main__":
    main()
