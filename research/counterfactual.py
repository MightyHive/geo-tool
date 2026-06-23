import pandas as pd
import numpy as np
import statsmodels.formula.api as smf
import matplotlib.pyplot as plt


df = pd.read_csv("trend_bq_export.csv")

df["week"] = pd.to_datetime(df["week"])
df = df.sort_values(["site", "week"])

numeric_cols = [
    "brand_index",
    "SEO_sessions",
    "PPC_sessions"
]

for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# Avoid invalid logs
df["brand_index"] = df["brand_index"].replace(0, np.nan)

df["log_brand_index"] = np.log(df["brand_index"])
df["log_SEO_sessions"] = np.log1p(df["SEO_sessions"])
df["log_PPC_sessions"] = np.log1p(df["PPC_sessions"])

# Combined search
df["Search_sessions"] = df["SEO_sessions"] + df["PPC_sessions"]
df["log_Search_sessions"] = np.log1p(df["Search_sessions"])

# Time controls
df["trend"] = (df["week"] - df["week"].min()).dt.days / 7

df["sin_annual"] = np.sin(2 * np.pi * df["week"].dt.dayofyear / 365.25)
df["cos_annual"] = np.cos(2 * np.pi * df["week"].dt.dayofyear / 365.25)

# Periods
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

def run_site_counterfactual(data, site, outcome_log, outcome_raw):
    site_df = data[data["site"] == site].copy()

    baseline_df = site_df[site_df["period"] == "baseline"].copy()
    eval_df = site_df[site_df["period"] == "evaluation"].copy()

    model_cols = [
        outcome_log,
        "log_brand_index",
        "sin_annual",
        "cos_annual",
        "trend"
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
        log_brand_index
        + sin_annual
        + cos_annual
        + trend
    """

    model = smf.ols(formula, data=baseline_df).fit(cov_type="HC3")

    eval_df[f"expected_{outcome_raw}"] = np.expm1(model.predict(eval_df))

    eval_df[f"{outcome_raw}_gap"] = (
        eval_df[outcome_raw] - eval_df[f"expected_{outcome_raw}"]
    )

    eval_df[f"{outcome_raw}_gap_pct"] = (
        eval_df[f"{outcome_raw}_gap"]
        / eval_df[f"expected_{outcome_raw}"].replace(0, np.nan)
    )

    summary = {
        "site": site,
        "outcome": outcome_raw,
        "baseline_weeks": len(baseline_df),
        "evaluation_weeks": len(eval_df),
        "actual_sessions": eval_df[outcome_raw].sum(),
        "expected_sessions": eval_df[f"expected_{outcome_raw}"].sum(),
        "gap": eval_df[f"{outcome_raw}_gap"].sum(),
        "gap_pct": (
            eval_df[f"{outcome_raw}_gap"].sum()
            / eval_df[f"expected_{outcome_raw}"].sum()
        ),
        "model_r2": model.rsquared,
        "brand_index_coef": model.params.get("log_brand_index", np.nan),
        "brand_index_p": model.pvalues.get("log_brand_index", np.nan),
    }

    eval_df["model_type"] = "panel"
    eval_df["outcome"] = outcome_raw
    return eval_df, summary

all_predictions = []
all_summaries = []

outcomes = [
    ("log_SEO_sessions", "SEO_sessions"),
    ("log_PPC_sessions", "PPC_sessions"),
    ("log_Search_sessions", "Search_sessions")
]

for site in df["site"].unique():
    for outcome_log, outcome_raw in outcomes:
        pred_df, summary = run_site_counterfactual(
            data=df,
            site=site,
            outcome_log=outcome_log,
            outcome_raw=outcome_raw
        )

        if pred_df is not None:
            all_predictions.append(pred_df)
            all_summaries.append(summary)

predictions = pd.concat(all_predictions, ignore_index=True)
summary = pd.DataFrame(all_summaries)

print(summary)

summary.to_csv("seo_ppc_sessions_counterfactual_summary.csv", index=False)
predictions.to_csv("seo_ppc_sessions_counterfactual_predictions.csv", index=False)

overall = (
    summary.groupby("outcome")
    .agg(
        sites=("site", "nunique"),
        actual_sessions=("actual_sessions", "sum"),
        expected_sessions=("expected_sessions", "sum"),
        total_gap=("gap", "sum"),
        median_gap_pct=("gap_pct", "median"),
        mean_gap_pct=("gap_pct", "mean"),
        sites_above_expected=("gap", lambda x: (x > 0).sum()),
        sites_below_expected=("gap", lambda x: (x < 0).sum())
    )
    .reset_index()
)

overall["total_gap_pct"] = (
    overall["total_gap"] / overall["expected_sessions"]
)

print(overall)
overall.to_csv("seo_ppc_sessions_counterfactual_overall.csv", index=False)

def run_panel_counterfactual(outcome_log, outcome_raw):
    model_cols = [
        outcome_log,
        "log_brand_index",
        "site",
        "sin_annual",
        "cos_annual",
        "trend"
    ]

    baseline_df = (
        df[df["period"] == "baseline"]
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=model_cols)
        .copy()
    )

    eval_df = (
        df[df["period"] == "evaluation"]
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=model_cols)
        .copy()
    )

    formula = f"""
    {outcome_log} ~
        log_brand_index
        + C(site)
        + sin_annual
        + cos_annual
        + trend
    """

    model = smf.ols(formula, data=baseline_df).fit(
        cov_type="cluster",
        cov_kwds={"groups": baseline_df["site"]}
    )

    eval_df[f"expected_{outcome_raw}"] = np.expm1(model.predict(eval_df))
    eval_df[f"{outcome_raw}_gap"] = (
        eval_df[outcome_raw] - eval_df[f"expected_{outcome_raw}"]
    )

    result = {
        "outcome": outcome_raw,
        "actual_sessions": eval_df[outcome_raw].sum(),
        "expected_sessions": eval_df[f"expected_{outcome_raw}"].sum(),
        "gap": eval_df[f"{outcome_raw}_gap"].sum(),
        "gap_pct": (
            eval_df[f"{outcome_raw}_gap"].sum()
            / eval_df[f"expected_{outcome_raw}"].sum()
        ),
        "model_r2": model.rsquared,
        "brand_index_coef": model.params.get("log_brand_index", np.nan),
        "brand_index_p": model.pvalues.get("log_brand_index", np.nan),
    }

    print("\n==============================")
    print(outcome_raw)
    print("==============================")
    print(model.summary())
    print(result)

    return model, eval_df, result

panel_results_list = []
panel_predictions_list = []

for outcome_log, outcome_raw in outcomes:
    m, p, r = run_panel_counterfactual(outcome_log, outcome_raw)

    panel_results_list.append(r)
    panel_predictions_list.append(p)

panel_results = pd.DataFrame(panel_results_list)
panel_predictions = pd.concat(panel_predictions_list, ignore_index=True)

print("\nPanel counterfactual results:")
print(panel_results)

panel_results.to_csv("panel_counterfactual_results.csv", index=False)
panel_predictions.to_csv("panel_counterfactual_predictions.csv", index=False)


for outcome_raw in ["SEO_sessions", "PPC_sessions", "Search_sessions"]:
    expected_col = f"expected_{outcome_raw}"

    if expected_col not in predictions.columns:
        continue

    temp = predictions[
        (predictions["period"] == "evaluation")
        & (predictions[expected_col].notna())
    ].copy()

    sites = temp["site"].unique()

    for site in sites:
        site_df = temp[temp["site"] == site].copy()

        plt.figure(figsize=(12, 5))

        plt.plot(
            site_df["week"],
            site_df[outcome_raw],
            label=f"Actual {outcome_raw}",
            color="tab:blue"
        )

        plt.plot(
            site_df["week"],
            site_df[expected_col],
            label=f"Expected {outcome_raw}",
            color="tab:red",
            linestyle="--"
        )

        plt.axvline(pd.to_datetime("2025-05-26"), color="black", linestyle="--")

        plt.title(f"{site}: Actual vs Expected {outcome_raw}")
        plt.ylabel("Sessions")
        plt.legend()
        plt.tight_layout()
        plt.show()

for outcome_raw in ["SEO_sessions", "PPC_sessions", "Search_sessions"]:
    temp = summary[summary["outcome"] == outcome_raw].copy()
    temp = temp.sort_values("gap_pct")

    plt.figure(figsize=(10, 5))

    plt.barh(
        temp["site"],
        temp["gap_pct"],
        color=np.where(temp["gap_pct"] >= 0, "tab:green", "tab:red")
    )

    plt.axvline(0, color="black")
    plt.title(f"{outcome_raw}: Actual vs Expected Gap %")
    plt.xlabel("Gap vs expected")
    plt.tight_layout()
    plt.show()

print("\n==============================")
print("COUNTERFACTUAL SUMMARY")
print("==============================")

print("\nSite-specific models:")
print(overall[[
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

print("\nPanel model:")
print(panel_results[[
    "outcome",
    "actual_sessions",
    "expected_sessions",
    "gap",
    "gap_pct",
    "model_r2",
    "brand_index_coef",
    "brand_index_p"
]])