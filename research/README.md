# Research

Offline econometric work: GA4 exports, Google Trends manual pipeline, and counterfactual models.

## Layout

```
research/
├── README.md                 ← you are here
├── requirements.txt          pip install -r research/requirements.txt
├── paths.py                  shared export/output paths (import from scripts)
├── .ga4_oauth_token.json     GA4 OAuth cache (gitignored)
├── config/
│   └── ga4_ppc_properties.csv   batch property list (property_name, ga4_property_id)
├── ga4/
│   ├── ga4_channel_export.py        daily sessions/purchases by channel
│   ├── ga4_channel_weekly_export.py weekly SEO / Direct / purchases
│   ├── ga4_ppc_brand_export.py      weekly branded vs non-branded PPC
│   └── exports/
│       ├── daily/    ga4_channel_long_*, ga4_channel_wide_*
│       ├── weekly/   ga4_channel_weekly_*
│       └── ppc/      ga4_ppc_brand_weekly_*, ga4_ppc_brand_summary_*
├── analysis/
│   ├── segment_descriptive_diagnostics.py
│   ├── seo-sessions-counterfactual.py
│   ├── counterfactual.py, smf.py, trend_index.py   (legacy BQ-export notebooks)
│   └── outputs/
│       ├── segment_diagnostics/
│       ├── seo_counterfactual/
│       └── legacy/           older CSV outputs (trend_bq_export, panel counterfactual, …)
└── trends_manual/            Google Trends download → normalize → hierarchy
```

## Quick start

```bash
cd seo-geo-tool
source .venv/bin/activate
pip install -r research/requirements.txt

# GA4 weekly exports (all properties in config)
python research/ga4/ga4_channel_weekly_export.py \
  --properties-file research/config/ga4_ppc_properties.csv
python research/ga4/ga4_ppc_brand_export.py \
  --properties-file research/config/ga4_ppc_properties.csv

# Segment diagnostics
python research/analysis/segment_descriptive_diagnostics.py

# SEO sessions counterfactual (Euro Car Parts)
python research/analysis/seo-sessions-counterfactual.py --site euro_car_parts
```

First GA4 run may need `--login` on any export script (token saved to `research/.ga4_oauth_token.json`).

## Google Trends

See `trends_manual/README.md` for the manual weekly download workflow.

Term → folder mapping lives in `trends_manual/term_registry.json` under `publishers/` and `advertisers/`.
