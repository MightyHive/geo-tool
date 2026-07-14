import pandas as pd
import numpy as np
import statsmodels.formula.api as smf
import matplotlib.pyplot as plt

# ====================================================
# 1. Load data
# ====================================================

df = pd.read_csv("./outputs/segment_model_inputs/S4_advertisers_branded_site_long.csv")

df["week"] = pd.to_datetime(df["week"])
df = df.sort_values(["site", "week"]).reset_index(drop=True)

# Optional but useful if present
if "site_type" not in df.columns:
    df["site_type"] = "advertiser"

# Map export column names → names used by this notebook-style script.
# Site-long panels use *_trends; older BQ exports used *_index.
_COL_ALIASES = {
    "brand_index": ["brand_index", "brand_trends", "brand_trends_norm"],
    "nonbrand_info_index": [
        "nonbrand_info_index",
        "nonbrand_info_trends",
        "nonbrand_info_trends_norm",
    ],
    "nonbrand_commercial_index": [
        "nonbrand_commercial_index",
        "nonbrand_commercial_trends",
        "nonbrand_commercial_trends_norm",
    ],
}
for canonical, candidates in _COL_ALIASES.items():
    if canonical in df.columns:
        continue
    for alt in candidates:
        if alt in df.columns:
            df[canonical] = df[alt]
            break

print("\nLoaded columns:", list(df.columns))
print("Sites:", sorted(df["site"].dropna().unique().tolist()))
print("Week range:", df["week"].min().date(), "→", df["week"].max().date())

# ====================================================
# 2. Check weekly aggregation
# ====================================================

row_check = (
    df.groupby(["site", "week"])
      .size()
      .reset_index(name="rows")
)

duplicates = row_check[row_check["rows"] > 1]

print("\nRows per site-week check:")
print("Total site-weeks:", len(row_check))
print("Duplicate site-weeks:", len(duplicates))

if len(duplicates) > 0:
    print("\nWARNING: Multiple rows per site-week found. Aggregating to weekly.")

    # Edit these if you have more session columns
    possible_sum_cols = [
        "SEO_sessions",
        "SEO_brand_sessions",
        "SEO_nonbrand_sessions",
        "SEO_nonbrand_info_sessions",
        "SEO_nonbrand_commercial_sessions",
    ]

    possible_mean_cols = [
        "brand_index",
        "nonbrand_index",
        "nonbrand_info_index",
        "nonbrand_commercial_index",
    ]

    agg_dict = {}

    for col in possible_sum_cols:
        if col in df.columns:
            agg_dict[col] = "sum"

    for col in possible_mean_cols:
        if col in df.columns:
            agg_dict[col] = "mean"

    if "site_type" in df.columns:
        agg_dict["site_type"] = "first"

    df = (
        df.groupby(["site", "week"], as_index=False)
          .agg(agg_dict)
    )

    df = df.sort_values(["site", "week"]).reset_index(drop=True)

else:
    print("Data appears to be one row per site-week.")

# ====================================================
# 3. Segment configuration
# ====================================================

# Edit this to match your actual columns.
# outcome_col = SEO session column
# trends_col = Google Trends column for that segment

SEGMENTS = {
    "total_SEO_from_brand_trends": {
        "outcome_col": "SEO_sessions",
        "trends_col": "brand_index",
    },
    "total_SEO_from_nonbrand_info_trends": {
        "outcome_col": "SEO_sessions",
        "trends_col": "nonbrand_info_index",
    },
    "total_SEO_from_nonbrand_commercial_trends": {
        "outcome_col": "SEO_sessions",
        "trends_col": "nonbrand_commercial_index",
    },
}

# Keep only segments where columns exist
available_segments = {}

for segment, cfg in SEGMENTS.items():
    outcome_col = cfg["outcome_col"]
    trends_col = cfg["trends_col"]

    if outcome_col in df.columns and trends_col in df.columns:
        available_segments[segment] = cfg
    else:
        print(
            f"Skipping segment '{segment}' because required columns are missing: "
            f"{outcome_col}, {trends_col}"
        )

if len(available_segments) == 0:
    raise ValueError("No configured SEO segments were found in the dataset.")

print("\nAvailable segments:")
print(available_segments)

# ====================================================
# 4. Numeric conversion
# ====================================================

numeric_cols = set()

for cfg in available_segments.values():
    numeric_cols.add(cfg["outcome_col"])
    numeric_cols.add(cfg["trends_col"])

for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# ====================================================
# 5. Helper functions
# ====================================================

def safe_log1p(x):
    x = pd.to_numeric(x, errors="coerce")
    x = x.replace([np.inf, -np.inf], np.nan)
    x = x.clip(lower=0)
    return np.log1p(x)

def safe_log_positive(x):
    x = pd.to_numeric(x, errors="coerce")
    x = x.replace([np.inf, -np.inf], np.nan)
    x = x.where(x > 0)
    return np.log(x)

# ====================================================
# 6. Create segment-specific log variables
# ====================================================

for segment, cfg in available_segments.items():
    outcome_col = cfg["outcome_col"]
    trends_col = cfg["trends_col"]

    df[f"log_{outcome_col}"] = safe_log1p(df[outcome_col])
    df[f"log_{trends_col}"] = safe_log_positive(df[trends_col])

# ====================================================
# 7. Time controls
# ====================================================

df["trend"] = (df["week"] - df["week"].min()).dt.days / 7

df["sin_annual"] = np.sin(
    2 * np.pi * df["week"].dt.dayofyear / 365.25
)

df["cos_annual"] = np.cos(
    2 * np.pi * df["week"].dt.dayofyear / 365.25
)

# ====================================================
# 8. Period definitions
# ====================================================

baseline_start = pd.to_datetime("2022-06-01")
baseline_end = pd.to_datetime("2025-03-31")

transition_start = pd.to_datetime("2025-04-01")
transition_end = pd.to_datetime("2025-05-25")

eval_start = pd.to_datetime("2025-05-26")
eval_end = df["week"].max()

df["period"] = "other"

df.loc[
    (df["week"] >= baseline_start) & (df["week"] <= baseline_end),
    "period"
] = "baseline"

df.loc[
    (df["week"] >= transition_start) & (df["week"] <= transition_end),
    "period"
] = "transition"

df.loc[
    (df["week"] >= eval_start) & (df["week"] <= eval_end),
    "period"
] = "evaluation"

print("\nPeriod counts:")
print(df.groupby("period")["week"].count())

# ====================================================
# 9. Site-specific SEO segment counterfactual
# ====================================================

def run_site_segment_counterfactual(data, site, segment, cfg):
    site_df = data[data["site"] == site].copy()

    outcome_col = cfg["outcome_col"]
    trends_col = cfg["trends_col"]

    outcome_log = f"log_{outcome_col}"
    trends_log = f"log_{trends_col}"

    baseline_df = site_df[site_df["period"] == "baseline"].copy()
    eval_df = site_df[site_df["period"] == "evaluation"].copy()

    model_cols = [
        outcome_log,
        trends_log,
        "sin_annual",
        "cos_annual",
        "trend",
    ]

    baseline_df = (
        baseline_df
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=model_cols)
        .copy()
    )

    eval_df = (
        eval_df
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=model_cols)
        .copy()
    )

    if len(baseline_df) < 30 or len(eval_df) == 0:
        return None, None

    formula = f"""
    {outcome_log} ~
        {trends_log}
        + sin_annual
        + cos_annual
        + trend
    """

    model = smf.ols(formula, data=baseline_df).fit(cov_type="HC3")

    expected_col = f"expected_{outcome_col}"
    gap_col = f"{outcome_col}_gap"
    gap_pct_col = f"{outcome_col}_gap_pct"

    eval_df[expected_col] = np.expm1(model.predict(eval_df))
    eval_df[expected_col] = eval_df[expected_col].clip(lower=0)

    eval_df[gap_col] = eval_df[outcome_col] - eval_df[expected_col]

    eval_df[gap_pct_col] = (
        eval_df[gap_col] / eval_df[expected_col].replace(0, np.nan)
    )

    eval_df["segment"] = segment
    eval_df["model_type"] = "site_specific"
    eval_df["outcome"] = outcome_col
    eval_df["trends_col"] = trends_col

    summary = {
        "model_type": "site_specific",
        "site": site,
        "site_type": site_df["site_type"].iloc[0] if "site_type" in site_df.columns else "unknown",
        "segment": segment,
        "outcome": outcome_col,
        "trends_col": trends_col,
        "baseline_weeks": len(baseline_df),
        "evaluation_weeks": len(eval_df),
        "actual_sessions": eval_df[outcome_col].sum(),
        "expected_sessions": eval_df[expected_col].sum(),
        "gap": eval_df[gap_col].sum(),
        "gap_pct": (
            eval_df[gap_col].sum()
            / eval_df[expected_col].sum()
            if eval_df[expected_col].sum() != 0
            else np.nan
        ),
        "model_r2": model.rsquared,
        "trends_coef": model.params.get(trends_log, np.nan),
        "trends_p": model.pvalues.get(trends_log, np.nan),
    }

    return eval_df, summary

site_predictions = []
site_summaries = []

for site in df["site"].dropna().unique():
    for segment, cfg in available_segments.items():
        pred_df, summary = run_site_segment_counterfactual(
            data=df,
            site=site,
            segment=segment,
            cfg=cfg
        )

        if pred_df is not None:
            site_predictions.append(pred_df)
            site_summaries.append(summary)

if len(site_predictions) == 0:
    raise ValueError("No site-specific segment predictions were generated.")

site_predictions = pd.concat(site_predictions, ignore_index=True)
site_summary = pd.DataFrame(site_summaries)

print("\nSite-specific SEO segment summary:")
print(site_summary)

site_summary.to_csv("seo_segment_site_counterfactual_summary.csv", index=False)
site_predictions.to_csv("seo_segment_site_counterfactual_predictions.csv", index=False)

# ====================================================
# 10. Aggregate site-specific results
# ====================================================

overall_site_specific = (
    site_summary.groupby(["segment", "outcome", "trends_col"])
    .agg(
        sites=("site", "nunique"),
        actual_sessions=("actual_sessions", "sum"),
        expected_sessions=("expected_sessions", "sum"),
        total_gap=("gap", "sum"),
        median_gap_pct=("gap_pct", "median"),
        mean_gap_pct=("gap_pct", "mean"),
        sites_above_expected=("gap", lambda x: (x > 0).sum()),
        sites_below_expected=("gap", lambda x: (x < 0).sum()),
    )
    .reset_index()
)

overall_site_specific["total_gap_pct"] = (
    overall_site_specific["total_gap"]
    / overall_site_specific["expected_sessions"].replace(0, np.nan)
)

print("\nOverall site-specific SEO segment counterfactual:")
print(overall_site_specific)

overall_site_specific.to_csv(
    "seo_segment_site_counterfactual_overall.csv",
    index=False
)

# ====================================================
# 11. Panel SEO segment counterfactual
# ====================================================

def run_panel_segment_counterfactual(data, segment, cfg):
    outcome_col = cfg["outcome_col"]
    trends_col = cfg["trends_col"]

    outcome_log = f"log_{outcome_col}"
    trends_log = f"log_{trends_col}"

    model_cols = [
        outcome_log,
        trends_log,
        "site",
        "sin_annual",
        "cos_annual",
        "trend",
    ]

    baseline_df = (
        data[data["period"] == "baseline"]
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=model_cols)
        .copy()
    )

    eval_df = (
        data[data["period"] == "evaluation"]
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=model_cols)
        .copy()
    )

    if len(baseline_df) < 30 or len(eval_df) == 0:
        return None, None, None

    formula = f"""
    {outcome_log} ~
        {trends_log}
        + C(site)
        + sin_annual
        + cos_annual
        + trend
    """

    model = smf.ols(formula, data=baseline_df).fit(
        cov_type="cluster",
        cov_kwds={"groups": baseline_df["site"]}
    )

    expected_col = f"expected_{outcome_col}"
    gap_col = f"{outcome_col}_gap"
    gap_pct_col = f"{outcome_col}_gap_pct"

    eval_df[expected_col] = np.expm1(model.predict(eval_df))
    eval_df[expected_col] = eval_df[expected_col].clip(lower=0)

    eval_df[gap_col] = eval_df[outcome_col] - eval_df[expected_col]

    eval_df[gap_pct_col] = (
        eval_df[gap_col] / eval_df[expected_col].replace(0, np.nan)
    )

    eval_df["segment"] = segment
    eval_df["model_type"] = "panel"
    eval_df["outcome"] = outcome_col
    eval_df["trends_col"] = trends_col

    result = {
        "model_type": "panel",
        "segment": segment,
        "outcome": outcome_col,
        "trends_col": trends_col,
        "baseline_rows": len(baseline_df),
        "evaluation_rows": len(eval_df),
        "actual_sessions": eval_df[outcome_col].sum(),
        "expected_sessions": eval_df[expected_col].sum(),
        "gap": eval_df[gap_col].sum(),
        "gap_pct": (
            eval_df[gap_col].sum()
            / eval_df[expected_col].sum()
            if eval_df[expected_col].sum() != 0
            else np.nan
        ),
        "model_r2": model.rsquared,
        "trends_coef": model.params.get(trends_log, np.nan),
        "trends_p": model.pvalues.get(trends_log, np.nan),
    }

    print("\n==============================")
    print(f"PANEL MODEL: {segment}")
    print("==============================")
    print(model.summary())
    print(result)

    return model, eval_df, result

panel_results = []
panel_predictions = []

for segment, cfg in available_segments.items():
    model, pred_df, result = run_panel_segment_counterfactual(
        data=df,
        segment=segment,
        cfg=cfg
    )

    if pred_df is not None:
        panel_predictions.append(pred_df)
        panel_results.append(result)

panel_results = pd.DataFrame(panel_results)
panel_predictions = pd.concat(panel_predictions, ignore_index=True)

print("\nPanel SEO segment counterfactual results:")
print(panel_results)

panel_results.to_csv("seo_segment_panel_counterfactual_results.csv", index=False)
panel_predictions.to_csv("seo_segment_panel_counterfactual_predictions.csv", index=False)

# ====================================================
# 12. Net and gross impact
# ====================================================

impact_summary = panel_results.copy()

impact_summary["absolute_gap"] = impact_summary["gap"].abs()

net_impact = impact_summary["gap"].sum()
gross_impact = impact_summary["absolute_gap"].sum()

print("\nSEO segment net/gross impact:")
print("Net impact:", net_impact)
print("Gross absolute impact:", gross_impact)

impact_summary.to_csv("seo_segment_net_gross_impact.csv", index=False)

# ====================================================
# 13. Visualisations
# ====================================================

# A. Gap pct by site and segment, site-specific
for segment in site_summary["segment"].unique():
    temp = site_summary[site_summary["segment"] == segment].copy()
    temp = temp.sort_values("gap_pct")

    plt.figure(figsize=(10, 5))

    plt.barh(
        temp["site"],
        temp["gap_pct"],
        color=np.where(temp["gap_pct"] >= 0, "tab:green", "tab:red")
    )

    plt.axvline(0, color="black")
    plt.title(f"SEO sessions gap % by site — {segment}")
    plt.xlabel("Actual vs expected gap %")
    plt.tight_layout()
    plt.show()

# B. Actual vs expected over time by site and segment
for segment in available_segments.keys():
    cfg = available_segments[segment]
    outcome_col = cfg["outcome_col"]
    expected_col = f"expected_{outcome_col}"

    temp = site_predictions[
        (site_predictions["segment"] == segment)
        & (site_predictions[expected_col].notna())
    ].copy()

    for site in temp["site"].unique():
        site_df = temp[temp["site"] == site].copy()

        plt.figure(figsize=(12, 5))

        plt.plot(
            site_df["week"],
            site_df[outcome_col],
            label=f"Actual {outcome_col}",
            color="tab:blue"
        )

        plt.plot(
            site_df["week"],
            site_df[expected_col],
            label=f"Expected {outcome_col}",
            color="tab:red",
            linestyle="--"
        )

        plt.axvline(pd.to_datetime("2025-05-26"), color="black", linestyle="--")

        plt.title(f"{site}: Actual vs Expected SEO Sessions — {segment}")
        plt.ylabel("Sessions")
        plt.legend()
        plt.tight_layout()
        plt.show()

# C. Panel result summary
panel_plot = panel_results.sort_values("gap_pct").copy()

plt.figure(figsize=(10, 5))

plt.barh(
    panel_plot["segment"],
    panel_plot["gap_pct"],
    color=np.where(panel_plot["gap_pct"] >= 0, "tab:green", "tab:red")
)

plt.axvline(0, color="black")
plt.title("Panel SEO session impact by segment")
plt.xlabel("Actual vs expected gap %")
plt.tight_layout()
plt.show()

# ====================================================
# 14. Final console summary
# ====================================================

print("\n==============================")
print("FINAL SEO SEGMENT COUNTERFACTUAL SUMMARY")
print("==============================")

print("\nSite-specific overall:")
print(overall_site_specific[[
    "segment",
    "outcome",
    "sites",
    "actual_sessions",
    "expected_sessions",
    "total_gap",
    "total_gap_pct",
    "median_gap_pct",
    "sites_above_expected",
    "sites_below_expected"
]])

print("\nPanel:")
print(panel_results[[
    "segment",
    "outcome",
    "actual_sessions",
    "expected_sessions",
    "gap",
    "gap_pct",
    "model_r2",
    "trends_coef",
    "trends_p"
]])

print("\nNet SEO session impact:", net_impact)
print("Gross absolute SEO session impact:", gross_impact)