# Weekly Google Trends — manual workflow

Manual browser downloads for the fixed research window **2022-06-01 → 2026-05-31**, with **weekly** aggregation (one CSV per term — no daily chunk stitching).

Modeled on `google-trends-browser-fetch/manual_run`, but scoped for `research/` panel work.

## Why weekly?

Google Trends returns weekly resolution for ranges ≥ ~90 days. For the full four-year research window, a **single weekly download** is enough — no multi-chunk daily stitch step.

## Quick start (one term)

```bash
cd research/trends_manual
cp config.env.example config.env
# edit QUERY_TERM in config.env

chmod +x run_manual_trends.sh run_organize.sh run_batch_terms.sh

./run_manual_trends.sh --config config.env --print-checklist
# Open the printed URL in Chrome, download CSV from "Interest over time"
# Save as data/<term_slug>_20220601/reference_weekly.csv
#   OR drop in inbox/<term_slug>/ and run run_organize.sh

./run_manual_trends.sh --config config.env --skip-bq
```

Outputs in `data/<term_slug>_20220601/`:

| File | Purpose |
|------|---------|
| `reference_weekly.csv` | Raw Google export |
| `trends_weekly.csv` | Normalized long format (`week_start`, `term`, `google_trends_value`) |
| `provenance.json` | Run metadata |

## Folder hierarchy

Terms are filed under **publishers** or **advertisers** using `term_registry.json`:

```
publishers/good_food/branded/bbc_good_food_20220601/
publishers/good_food/non_branded/pancake_recipe_20220601/
advertisers/euro_car_parts/branded/ecp_20220601/
advertisers/wickes/non_branded/decking_20220601/
```

After sorting unnamed CSVs, move flat `data/` folders into the hierarchy:

```bash
./run_resort.sh
```

## Sort unnamed CSV dumps

Drop ``multiTimeline*.csv`` files into ``inbox/unsorted/`` (or leave them in ``~/Downloads``):

```bash
./run_sort_downloads.sh --from ~/Downloads          # copy into data/<slug>_20220601/
./run_sort_downloads.sh --from inbox/unsorted --move  # move after sorting
./run_sort_downloads.sh --from ~/Downloads --overwrite  # replace existing terms
```

Each file is read for its query term (from the ``Week,<term>: (Region)`` header). Multi-term
exports are split into one file per term. Then finalize:

```bash
./run_batch_terms.sh --finalize --skip-bq
```


```bash
cp terms.example.txt terms.txt
# edit terms.txt

./run_batch_terms.sh --print-checklists   # URLs for every term
# Download each CSV into inbox/<term_slug>/

./run_batch_terms.sh --organize           # copy inbox → data/
./run_batch_terms.sh --finalize --skip-bq # normalize all terms
```

## BigQuery (optional)

Set `BQ_TRENDS_WEEKLY_TABLE` or use defaults from `shared.env` (`geo-tool-emea-ds.geo_tool.google_trends_weekly_{slug}`).

Requires `google-cloud-bigquery` and Application Default Credentials.

Omit `--skip-bq` on finalize to load.

## Notes

- Use **one term per URL** (single column in the CSV).
- Export from the **Interest over time** card only.
- Week labels in Google's CSV are **week-start dates** (often Sunday for UK).
- For Cloudflare-heavy sites, this workflow is unrelated — it only handles Trends ingestion.
