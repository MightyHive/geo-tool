import pandas as pd
import numpy as np
import statsmodels.formula.api as smf
import matplotlib.pyplot as plt
from scipy.stats import binomtest, wilcoxon

df = pd.read_csv("trend_bq_export.csv")

df["week"] = pd.to_datetime(df["week"])
df = df.sort_values(["site", "week"])

# Clean
numeric_cols = [
    "brand_index",
    "SEO_sessions", "SEO_purchases",
    "PPC_sessions", "PPC_purchases"
]

for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors="coerce")

# Avoid divide by zero
df["brand_index"] = df["brand_index"].replace(0, np.nan)

# Ratios
df["SEO_sessions_per_trend"] = df["SEO_sessions"] / df["brand_index"]
df["SEO_purchases_per_trend"] = df["SEO_purchases"] / df["brand_index"]
df["PPC_sessions_per_trend"] = df["PPC_sessions"] / df["brand_index"]
df["PPC_purchases_per_trend"] = df["PPC_purchases"] / df["brand_index"]

df["Search_sessions"] = df["SEO_sessions"] + df["PPC_sessions"]
df["Search_purchases"] = df["SEO_purchases"] + df["PPC_purchases"]

df["Search_sessions_per_trend"] = df["Search_sessions"] / df["brand_index"]
df["Search_purchases_per_trend"] = df["Search_purchases"] / df["brand_index"]

df["SEO_CVR"] = df["SEO_purchases"] / df["SEO_sessions"].replace(0, np.nan)
df["PPC_CVR"] = df["PPC_purchases"] / df["PPC_sessions"].replace(0, np.nan)
df["Search_CVR"] = df["Search_purchases"] / df["Search_sessions"].replace(0, np.nan)

# Logs
for col in [
    "brand_index",
    "SEO_sessions", "SEO_purchases",
    "PPC_sessions", "PPC_purchases",
    "Search_sessions", "Search_purchases"
]:
    df[f"log_{col}"] = np.log1p(df[col].clip(lower=0))

# Dates/periods
df["transition"] = (
    (df["week"] >= pd.to_datetime("2025-04-01"))
    & (df["week"] <= pd.to_datetime("2025-05-25"))
).astype(int)

df["post_AI"] = (
    df["week"] >= pd.to_datetime("2025-05-26")
).astype(int)

df["trend"] = (
    df["week"] - df["week"].min()
).dt.days / 7

df["sin_annual"] = np.sin(2 * np.pi * df["week"].dt.dayofyear / 365.25)
df["cos_annual"] = np.cos(2 * np.pi * df["week"].dt.dayofyear / 365.25)

print(df.head())

def assign_period(date):
    if date < pd.to_datetime("2024-07-01"):
        return "pre_proliferation"
    elif date < pd.to_datetime("2025-04-01"):
        return "ai_proliferation"
    elif date < pd.to_datetime("2025-05-26"):
        return "aio_transition"
    else:
        return "ai_growth"

df["period"] = df["week"].apply(assign_period)

metrics = [
    "brand_index",
    "SEO_sessions_per_trend",
    "SEO_purchases_per_trend",
    "PPC_sessions_per_trend",
    "PPC_purchases_per_trend",
    "Search_sessions_per_trend",
    "Search_purchases_per_trend",
    "SEO_CVR",
    "PPC_CVR",
    "Search_CVR"
]

period_summary = (
    df.groupby(["site", "period"])[metrics]
      .mean()
      .reset_index()
)

print(period_summary)
period_summary.to_csv("site_period_summary.csv", index=False)

baseline = period_summary[
    period_summary["period"] == "pre_proliferation"
].copy()

baseline = baseline[["site"] + metrics].rename(
    columns={m: f"{m}_baseline" for m in metrics}
)

period_changes = period_summary.merge(baseline, on="site", how="left")

for m in metrics:
    period_changes[f"{m}_change_vs_baseline"] = (
        period_changes[m] / period_changes[f"{m}_baseline"] - 1
    )

period_changes.to_csv("site_period_changes_vs_baseline.csv", index=False)

print(period_changes)

for m in [
    "SEO_sessions_per_trend",
    "SEO_purchases_per_trend",
    "PPC_sessions_per_trend",
    "PPC_purchases_per_trend",
    "Search_purchases_per_trend"
]:
    col = f"{m}_change_vs_baseline"

    ai_growth = period_changes[
        period_changes["period"] == "ai_growth"
    ]

    print("\n", m)
    print("Median change:", ai_growth[col].median())
    print("Mean change:", ai_growth[col].mean())
    print("Sites increasing:", (ai_growth[col] > 0).sum(), "of", len(ai_growth))

def run_panel_model(outcome):
    formula = f"""
    {outcome} ~
        log_brand_index
        + transition
        + post_AI
        + C(site)
        + sin_annual
        + cos_annual
        + trend
    """

    model_df = df.replace([np.inf, -np.inf], np.nan).dropna(
        subset=[
            outcome,
            "log_brand_index",
            "transition",
            "post_AI",
            "site",
            "sin_annual",
            "cos_annual",
            "trend"
        ]
    ).copy()

    model = smf.ols(formula, data=model_df).fit(
        cov_type="cluster",
        cov_kwds={"groups": model_df["site"]}
    )

    print("\n==============================")
    print(f"Outcome: {outcome}")
    print("==============================")
    print(model.summary())

    coef = model.params.get("post_AI", np.nan)
    pct = np.exp(coef) - 1

    print("Post-AI coefficient:", coef)
    print("Approx post-AI % shift:", pct)

    return model

models = {}

for outcome in [
    "log_SEO_sessions",
    "log_SEO_purchases",
    "log_PPC_sessions",
    "log_PPC_purchases",
    "log_Search_sessions",
    "log_Search_purchases"
]:
    models[outcome] = run_panel_model(outcome)

baseline_period = (
    (df["week"] >= pd.to_datetime("2022-06-01"))
    & (df["week"] <= pd.to_datetime("2025-03-31"))
)

for m in [
    "SEO_sessions_per_trend",
    "SEO_purchases_per_trend",
    "PPC_sessions_per_trend",
    "PPC_purchases_per_trend",
    "Search_purchases_per_trend"
]:
    col = f"{m}_change_vs_baseline"

    ai_growth = period_changes[
        period_changes["period"] == "ai_growth"
    ].replace([np.inf, -np.inf], np.nan)

    valid = ai_growth[col].dropna()
    successes = (valid > 0).sum()
    n = len(valid)

    test = binomtest(successes, n, p=0.5, alternative="two-sided")

    print("\n", m)
    print(f"Sites increasing: {successes} of {n}")
    print("Sign-test p-value:", test.pvalue)

for m in [
    "SEO_sessions_per_trend",
    "SEO_purchases_per_trend",
    "PPC_sessions_per_trend",
    "PPC_purchases_per_trend",
    "Search_purchases_per_trend"
]:
    col = f"{m}_change_vs_baseline"

    ai_growth = period_changes[
        period_changes["period"] == "ai_growth"
    ].replace([np.inf, -np.inf], np.nan)

    valid = ai_growth[col].dropna()

    if len(valid) >= 5:
        stat, p = wilcoxon(valid)
        print("\n", m)
        print("Wilcoxon p-value:", p)
        print("Median change:", valid.median())

for outcome in [
    "log_SEO_sessions",
    "log_SEO_purchases",
    "log_PPC_sessions",
    "log_PPC_purchases"
]:
    print("\n", outcome)

    for site, site_df in df.groupby("site"):
        model_df = site_df.replace([np.inf, -np.inf], np.nan).dropna(
            subset=[
                outcome,
                "log_brand_index",
                "post_AI",
                "sin_annual",
                "cos_annual",
                "trend"
            ]
        )

        if len(model_df) < 30:
            continue

        formula = f"""
        {outcome} ~
            log_brand_index
            + post_AI
            + sin_annual
            + cos_annual
            + trend
        """

        m = smf.ols(formula, data=model_df).fit(cov_type="HC3")

        coef = m.params.get("post_AI", np.nan)
        pct = np.exp(coef) - 1
        pval = m.pvalues.get("post_AI", np.nan)

        print(site, "post_AI %:", pct, "p:", pval)

for metric in [
    "SEO_sessions_per_trend",
    "SEO_purchases_per_trend",
    "PPC_sessions_per_trend",
    "PPC_purchases_per_trend"
]:
    baseline_avg = (
        df[baseline_period]
        .groupby("site")[metric]
        .mean()
        .rename("baseline_avg")
    )

    temp = df.merge(baseline_avg, on="site", how="left")
    temp[f"{metric}_index"] = temp[metric] / temp["baseline_avg"]

    avg_trend = (
        temp.groupby("week")[f"{metric}_index"]
            .median()
            .reset_index()
    )

    plt.figure(figsize=(14, 5))

    for site, site_df in temp.groupby("site"):
        plt.plot(
            site_df["week"],
            site_df[f"{metric}_index"],
            alpha=0.25,
            linewidth=1
        )

    plt.plot(
        avg_trend["week"],
        avg_trend[f"{metric}_index"],
        color="black",
        linewidth=3,
        label="Median across sites"
    )

    plt.axhline(1, color="grey", linestyle="--")
    plt.axvline(pd.to_datetime("2025-04-01"), color="grey", linestyle="--")
    plt.axvline(pd.to_datetime("2025-05-26"), color="red", linestyle="--")

    plt.title(f"Indexed {metric} Across Sites")
    plt.ylabel("Index vs baseline")
    plt.legend()
    plt.tight_layout()
    plt.show()