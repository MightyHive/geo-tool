#!/usr/bin/env python3
"""Finalize a weekly Google Trends manual download (single CSV, no daily stitching).

Fixed research window defaults to 2022-06-01 → 2026-05-31 (see shared.env).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

MANUAL_ROOT = Path(__file__).resolve().parent
if str(MANUAL_ROOT) not in sys.path:
    sys.path.insert(0, str(MANUAL_ROOT))

from trends_csv import explore_url_range, is_valid_trends_csv, load_trends_csv  # noqa: E402

FIXED_START = "2022-06-01"
FIXED_END = "2026-05-31"
RAW_CSV = "reference_weekly.csv"
OUT_CSV = "trends_weekly.csv"
PROVENANCE_JSON = "provenance.json"


def _data_dir() -> Path:
    return Path(os.environ.get("TRENDS_DATA_DIR", str(MANUAL_ROOT / "data"))).resolve()


def _query_term() -> str:
    qt = os.environ.get("QUERY_TERM", "").strip()
    if not qt:
        raise SystemExit("Set QUERY_TERM in your config env.")
    return qt


def _date_range() -> tuple[str, str]:
    start = os.environ.get("START_DATE", FIXED_START).strip() or FIXED_START
    end = os.environ.get("END_DATE", FIXED_END).strip() or FIXED_END
    return start, end


def _weekly_header_hint(q: str) -> str:
    geo = os.environ.get("TRENDS_GEO", "GB").strip()
    region = "United Kingdom" if geo.upper() == "GB" else geo
    return f"Week,{q}: ({region})"


def _resolved_bq_table() -> str:
    single = os.environ.get("BQ_TRENDS_WEEKLY_TABLE", "").strip()
    if single and single.count(".") >= 2:
        return single
    project = os.environ.get("BQ_TRENDS_PROJECT", "").strip()
    dataset = os.environ.get("BQ_TRENDS_DATASET", "").strip()
    prefix = os.environ.get("BQ_TRENDS_TABLE_PREFIX", "google_trends_weekly_").strip()
    if not (project and dataset):
        return ""
    slug = os.environ.get("TERM_SLUG", "").strip()
    if not slug:
        slug = _query_term().lower()
        slug = "".join(c if c.isalnum() else "_" for c in slug).strip("_")
    return f"{project}.{dataset}.{prefix}{slug}"


def _validate_weekly_csv(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise SystemExit(f"Missing weekly CSV: {path}")
    if not is_valid_trends_csv(path):
        preview = path.read_text(encoding="utf-8-sig", errors="replace")[:400]
        raise SystemExit(
            f"Invalid Trends CSV: {path}\nPreview:\n{preview!r}\n"
            "Export from the **Interest over time** card only."
        )
    df = load_trends_csv(str(path))
    if len(df.columns) != 1:
        raise SystemExit(
            f"Expected one metric column (single-term URL); got {list(df.columns)}"
        )
    grain = "week"
    raw_head = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()[:6]
    for line in raw_head:
        if line.strip().lower().startswith("day,"):
            grain = "day"
            break
    if grain == "day":
        print(
            "WARN: CSV looks daily. For this workflow use one weekly download "
            f"covering {FIXED_START} → {FIXED_END}.",
            file=sys.stderr,
        )
    print(f"  OK {path.name}: {len(df)} rows, column={df.columns[0]!r}", flush=True)
    return df


def _to_long(df: pd.DataFrame, *, term: str) -> pd.DataFrame:
    col = df.columns[0]
    out = df[[col]].copy()
    out = out.reset_index().rename(columns={"date": "week_start", col: "google_trends_value"})
    out["week_start"] = pd.to_datetime(out["week_start"]).dt.date
    out["term"] = term
    out["google_trends_value"] = out["google_trends_value"].round().astype("Int64")
    return out[["week_start", "term", "google_trends_value"]].sort_values("week_start")


def print_checklist(data: Path) -> int:
    geo = os.environ.get("TRENDS_GEO", "GB").strip()
    hl = os.environ.get("TRENDS_HL", "en-GB").strip()
    q = _query_term()
    start, end = _date_range()
    url = explore_url_range(start, end, geo=geo, hl=hl, q_term=q)
    ref_path = data / RAW_CSV
    bq = _resolved_bq_table()

    print(f"Data directory: {data}")
    if bq:
        print(f"BigQuery table: {bq}")
    print(f"Query term: {q!r}")
    print(f"Date range:   {start} → {end} (weekly aggregation)\n")
    print("=" * 72)
    print("WEEKLY MANUAL DOWNLOAD (1 file)")
    print("=" * 72)
    print(
        "\n1. Open the URL below in Chrome (signed in to Google).\n"
        "2. Wait until **Interest over time** loads.\n"
        "3. Click the **CSV** icon on that card (not sub-region / related queries).\n"
        "4. Save the file as:\n"
        f"   {ref_path}\n"
        f"\nExpected header shape: {_weekly_header_hint(q)}\n"
    )
    print(f"URL:\n{url}\n")
    print("=" * 72)
    config = os.environ.get("MANUAL_RUN_CONFIG", "config.env")
    print(f"When saved, run:  ./run_manual_trends.sh --config {config}")
    print("=" * 72)
    return 0


def _write_provenance(data: Path, *, term: str, rows: int, start: str, end: str) -> None:
    payload = {
        "workflow": "weekly_manual",
        "term": term,
        "start_date": start,
        "end_date": end,
        "geo": os.environ.get("TRENDS_GEO", "GB"),
        "hl": os.environ.get("TRENDS_HL", "en-GB"),
        "rows": rows,
        "run_id": os.environ.get("RUN_ID", ""),
        "ingested_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_csv": RAW_CSV,
        "output_csv": OUT_CSV,
    }
    (data / PROVENANCE_JSON).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_bq(long_df: pd.DataFrame, table_id: str, run_id: str) -> None:
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise SystemExit(
            "google-cloud-bigquery is required for BQ load. "
            "Use --skip-bq or pip install google-cloud-bigquery."
        ) from exc

    client = bigquery.Client()
    ingested = datetime.now(timezone.utc).replace(microsecond=0)
    rows = [
        {
            "week_start": r.week_start.isoformat(),
            "term": r.term,
            "google_trends_value": int(r.google_trends_value) if pd.notna(r.google_trends_value) else None,
            "run_id": run_id,
            "ingested_at": ingested.isoformat(),
        }
        for r in long_df.itertuples(index=False)
    ]
    schema = [
        bigquery.SchemaField("week_start", "DATE"),
        bigquery.SchemaField("term", "STRING"),
        bigquery.SchemaField("google_trends_value", "INT64"),
        bigquery.SchemaField("run_id", "STRING"),
        bigquery.SchemaField("ingested_at", "TIMESTAMP"),
    ]
    temp_id = f"{table_id}_staging_{run_id.replace('-', '_').replace(':', '')[:40]}"
    job_config = bigquery.LoadJobConfig(schema=schema, write_disposition="WRITE_TRUNCATE")
    load_job = client.load_table_from_json(rows, temp_id, job_config=job_config)
    load_job.result()
    merge_sql = f"""
    MERGE `{table_id}` T
    USING `{temp_id}` S
    ON T.week_start = S.week_start AND T.term = S.term
    WHEN MATCHED THEN UPDATE SET
      google_trends_value = S.google_trends_value,
      run_id = S.run_id,
      ingested_at = S.ingested_at
    WHEN NOT MATCHED THEN INSERT ROW
    """
    try:
        client.get_table(table_id)
    except Exception:
        client.create_table(bigquery.Table(table_id, schema=schema))
    client.query(merge_sql).result()
    client.delete_table(temp_id, not_found_ok=True)
    print(f"Loaded {len(rows)} rows → `{table_id}`", flush=True)


def finalize(data: Path, *, skip_bq: bool) -> int:
    term = _query_term()
    start, end = _date_range()
    raw_path = data / RAW_CSV
    out_path = data / OUT_CSV

    print("Validating weekly CSV …", flush=True)
    wide = _validate_weekly_csv(raw_path)
    long_df = _to_long(wide, term=term)
    long_df.to_csv(out_path, index=False)
    _write_provenance(data, term=term, rows=len(long_df), start=start, end=end)

    print(f"Wrote {out_path} ({len(long_df)} weekly rows)", flush=True)
    print(f"  range: {long_df['week_start'].min()} → {long_df['week_start'].max()}", flush=True)

    if skip_bq:
        print("Skipping BigQuery (--skip-bq).", flush=True)
        return 0

    table = _resolved_bq_table()
    if not table:
        print("No BQ table configured (set BQ_TRENDS_WEEKLY_TABLE or BQ_TRENDS_PROJECT/DATASET).", flush=True)
        return 0

    run_id = os.environ.get("RUN_ID", "").strip() or f"weekly_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%SZ')}"
    _load_bq(long_df, table, run_id)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--print-checklist", action="store_true", help="Print Trends URL and save path.")
    ap.add_argument("--skip-bq", action="store_true", help="Write trends_weekly.csv only.")
    ap.add_argument("--data-dir", type=Path, default=None, help="Override TRENDS_DATA_DIR.")
    args = ap.parse_args()

    data = args.data_dir.resolve() if args.data_dir else _data_dir()
    data.mkdir(parents=True, exist_ok=True)

    if args.print_checklist:
        return print_checklist(data)
    return finalize(data, skip_bq=args.skip_bq)


if __name__ == "__main__":
    raise SystemExit(main())
