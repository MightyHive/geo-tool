import pandas as pd
import numpy as np
import statsmodels.formula.api as smf
import matplotlib.pyplot as plt

# ====================================================
# 1. Load data
# ====================================================

df = pd.read_csv("bq_export.csv")

df["date"] = pd.to_datetime(df["date"])
df = df.sort_values("date").reset_index(drop=True)

# Make sure numeric columns are numeric
for col in df.columns:
    if col != "date":
        df[col] = pd.to_numeric(df[col], errors="coerce")

# Fill missing for core GA/channel metrics only.
# Do not fill spend, GSC, Position, brand_index with zero unless zero is genuinely true.
safe_zero_cols = [
    "Ecommerce_purchases", "Sessions",

    "PPC_purchases", "SEO_purchases", "Direct_purchases",
    "Unassigned_purchases", "Affiliates_purchases", "Social_purchases",
    "Email_purchases", "AI_purchases", "Referral_purchases", "Other_purchases",

    "PPC_sessions", "SEO_sessions", "Direct_sessions",
    "Unassigned_sessions", "Affiliates_sessions", "Social_sessions",
    "Email_sessions", "AI_sessions", "Referral_sessions", "Other_sessions",
]

for col in safe_zero_cols:
    if col in df.columns:
        df[col] = df[col].fillna(0)

# ====================================================
# 2. Optional holiday dates
# ====================================================

holiday_dates = [
    "2023-04-07",
    "2023-04-10",
    "2023-05-01",
    "2023-05-08",
    "2023-05-29",
    "2023-08-28",
    "2023-11-24",
    "2023-12-25",
    "2023-12-26",

    "2024-01-01",
    "2024-03-29",
    "2024-04-01",
    "2024-05-06",
    "2024-05-27",
    "2024-08-26",
    "2024-11-29",
    "2024-12-25",
    "2024-12-26",

    "2025-01-01",
    "2025-04-18",
    "2025-04-21",
    "2025-05-05",
    "2025-05-26",
    "2025-08-25",
    "2025-11-28",
    "2025-12-25",
    "2025-12-26",

    "2026-01-01",
    "2026-04-03",
    "2026-04-06",
    "2026-05-04",
    "2026-05-25",
    "2026-08-31",
    "2026-12-25",
    "2026-12-26",
]

holiday_dates = pd.to_datetime(holiday_dates)
df["holiday"] = df["date"].isin(holiday_dates).astype(int)

# ====================================================
# 3. Weekly aggregation
# ====================================================

# Add "Other" if those columns exist.
# If they do not exist, create them as zero columns.
required_optional_cols = [
    "Other_purchases",
    "Other_sessions",
    "brand_index",
    "spend"
]

for col in required_optional_cols:
    if col not in df.columns:
        df[col] = np.nan

if "Other_purchases" not in df.columns:
    df["Other_purchases"] = 0

if "Other_sessions" not in df.columns:
    df["Other_sessions"] = 0

if "brand_index" not in df.columns:
    df["brand_index"] = np.nan

if "spend" not in df.columns:
    df["spend"] = np.nan

sum_cols = [
    "Ecommerce_purchases", "Sessions",

    "PPC_purchases", "SEO_purchases", "Direct_purchases",
    "Unassigned_purchases", "Affiliates_purchases", "Social_purchases",
    "Email_purchases", "AI_purchases", "Referral_purchases",
    "Other_purchases",

    "PPC_sessions", "SEO_sessions", "Direct_sessions",
    "Unassigned_sessions", "Affiliates_sessions", "Social_sessions",
    "Email_sessions", "AI_sessions", "Referral_sessions",
    "Other_sessions",

    # "Clicks", "Impressions",

    # "Home_landing_sessions", "not_set_landing_sessions",

    "spend",
    "holiday"
]

# Only keep columns that exist
sum_cols = [c for c in sum_cols if c in df.columns]

weekly_sum = (
    df.set_index("date")
      .resample("W-MON")[sum_cols]
      .sum()
      .reset_index()
)

# ----------------------------------------------------
# Brand index weekly average
# ----------------------------------------------------
# Google Trends / brand_index should usually be averaged, not summed.
weekly_brand = (
    df.set_index("date")
      .resample("W-MON")["brand_index"]
      .mean()
      .reset_index()
      .rename(columns={"brand_index": "brand_index_weekly"})
)

weekly = weekly_sum.merge(weekly_brand, on="date", how="left")
weekly["brand_index"] = weekly["brand_index_weekly"]
weekly = weekly.drop(columns=["brand_index_weekly"])

# ----------------------------------------------------
# Weighted average Position
# ----------------------------------------------------
# df["Position_x_Impressions"] = df["Position"] * df["Impressions"]

# weekly_pos = (
#     df.set_index("date")
#       .resample("W-MON")[["Position_x_Impressions", "Impressions"]]
#       .sum()
#       .reset_index()
# )

# weekly["Position"] = (
#     weekly_pos["Position_x_Impressions"]
#     / weekly_pos["Impressions"].replace(0, np.nan)
# )

# # CTR
# weekly["CTR"] = weekly["Clicks"] / weekly["Impressions"].replace(0, np.nan)

# Holiday flag at weekly level:
# If any holiday occurred in that week, mark as holiday week.
weekly["holiday"] = (weekly["holiday"] > 0).astype(int)

# ====================================================
# 4. Define periods
# ====================================================

long_baseline_start = "2023-01-30"
long_baseline_end = "2025-03-31"

recent_baseline_start = "2024-09-01"
recent_baseline_end = "2025-03-31"

eval_start = "2025-05-26"
eval_end = "2026-06-14"

# Also daily versions for your descriptive summaries / channel CVR method
long_baseline = df[
    (df["date"] >= long_baseline_start)
    & (df["date"] <= long_baseline_end)
].copy()

recent_baseline = df[
    (df["date"] >= recent_baseline_start)
    & (df["date"] <= recent_baseline_end)
].copy()

evaluation = df[
    (df["date"] >= eval_start)
    & (df["date"] <= eval_end)
].copy()

transition = df[
    (df["date"] >= "2025-04-01")
    & (df["date"] <= "2025-05-25")
].copy()

print("\nTransition period daily sessions:", transition["Sessions"].mean())
print("Transition period daily purchases:", transition["Ecommerce_purchases"].mean())
print(
    "Transition period CVR:",
    transition["Ecommerce_purchases"].sum() / transition["Sessions"].sum()
)
print("Transition avg brand index:", transition["brand_index"].mean())

# ====================================================
# 5. Safe log helpers
# ====================================================

def safe_log1p(x):
    x = pd.to_numeric(x, errors="coerce")
    x = x.replace([np.inf, -np.inf], np.nan)
    x = x.clip(lower=0)
    return np.log1p(x)

def safe_divide(a, b):
    return a / b.replace(0, np.nan)

# ====================================================
# 6. Create weekly modeling variables
# ====================================================

weekly["non_AI_sessions"] = weekly["Sessions"] - weekly["AI_sessions"]
weekly["non_AI_purchases"] = weekly["Ecommerce_purchases"] - weekly["AI_purchases"]

weekly["non_AI_sessions"] = weekly["non_AI_sessions"].clip(lower=0)
weekly["non_AI_purchases"] = weekly["non_AI_purchases"].clip(lower=0)

weekly["brand_demand_purchases"] = (
    weekly["Direct_purchases"]
    + weekly["SEO_purchases"]
    + weekly["Unassigned_purchases"]
)

weekly["brand_demand_sessions"] = (
    weekly["Direct_sessions"]
    + weekly["SEO_sessions"]
    + weekly["Unassigned_sessions"]
)

weekly["CVR"] = safe_divide(weekly["Ecommerce_purchases"], weekly["Sessions"])
weekly["non_AI_CVR"] = safe_divide(weekly["non_AI_purchases"], weekly["non_AI_sessions"])

weekly["purchases_per_trends_point"] = safe_divide(
    weekly["Ecommerce_purchases"],
    weekly["brand_index"]
)

weekly["brand_demand_purchases_per_trends_point"] = safe_divide(
    weekly["brand_demand_purchases"],
    weekly["brand_index"]
)

# Log variables for regression
weekly["log_non_AI_purchases"] = safe_log1p(weekly["non_AI_purchases"])
weekly["log_non_AI_sessions"] = safe_log1p(weekly["non_AI_sessions"])
weekly["log_brand_trends"] = safe_log1p(weekly["brand_index"])
weekly["log_spend"] = safe_log1p(weekly["spend"])
# weekly["log_Clicks"] = safe_log1p(weekly["Clicks"])
# weekly["log_Impressions"] = safe_log1p(weekly["Impressions"])

weekly["log_PPC_sessions"] = safe_log1p(weekly["PPC_sessions"])
weekly["log_SEO_sessions"] = safe_log1p(weekly["SEO_sessions"])
weekly["log_Direct_sessions"] = safe_log1p(weekly["Direct_sessions"])
weekly["log_Unassigned_sessions"] = safe_log1p(weekly["Unassigned_sessions"])

# Time variables
weekly["trend"] = np.arange(len(weekly))

weekly["sin_annual"] = np.sin(
    2 * np.pi * weekly["date"].dt.dayofyear / 365.25
)

weekly["cos_annual"] = np.cos(
    2 * np.pi * weekly["date"].dt.dayofyear / 365.25
)

weekly["period"] = "other"

weekly.loc[
    (weekly["date"] >= long_baseline_start)
    & (weekly["date"] <= long_baseline_end),
    "period"
] = "long_baseline"

weekly.loc[
    (weekly["date"] >= recent_baseline_start)
    & (weekly["date"] <= recent_baseline_end),
    "recent_period_flag"
] = 1

weekly["recent_period_flag"] = weekly["recent_period_flag"].fillna(0).astype(int)

weekly.loc[
    (weekly["date"] >= eval_start)
    & (weekly["date"] <= eval_end),
    "period"
] = "evaluation"

long_baseline_weekly = weekly[
    (weekly["date"] >= long_baseline_start)
    & (weekly["date"] <= long_baseline_end)
].copy()

recent_baseline_weekly = weekly[
    (weekly["date"] >= recent_baseline_start)
    & (weekly["date"] <= recent_baseline_end)
].copy()

evaluation_weekly = weekly[
    (weekly["date"] >= eval_start)
    & (weekly["date"] <= eval_end)
].copy()

# Recreate baseline/evaluation after derived vars exist
long_baseline_weekly = weekly[
    (weekly["date"] >= pd.to_datetime(long_baseline_start))
    & (weekly["date"] <= pd.to_datetime(long_baseline_end))
].copy()

recent_baseline_weekly = weekly[
    (weekly["date"] >= pd.to_datetime(recent_baseline_start))
    & (weekly["date"] <= pd.to_datetime(recent_baseline_end))
].copy()

evaluation_weekly = weekly[
    (weekly["date"] >= pd.to_datetime(eval_start))
    & (weekly["date"] <= pd.to_datetime(eval_end))
].copy()

# ====================================================
# 7. Regression counterfactual
# ====================================================

formula_long = """
log_non_AI_purchases ~
    log_non_AI_sessions
    + log_brand_trends
    + sin_annual
    + cos_annual
    + trend
    + holiday
"""

formula_recent_spend = """
log_non_AI_purchases ~
    log_non_AI_sessions
    + log_brand_trends
    + log_spend
    + sin_annual
    + cos_annual
    + trend
    + holiday
"""

def run_counterfactual_model(
    model_name,
    formula,
    baseline_df,
    evaluation_df,
    model_cols
):
    baseline_model_df = (
        baseline_df
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=model_cols)
        .copy()
    )

    evaluation_model_df = (
        evaluation_df
        .replace([np.inf, -np.inf], np.nan)
        .dropna(subset=model_cols)
        .copy()
    )

    print(f"\n==============================")
    print(f"MODEL: {model_name}")
    print(f"==============================")
    print("Baseline weekly rows:", len(baseline_model_df))
    print("Evaluation weekly rows:", len(evaluation_model_df))

    model = smf.ols(formula, data=baseline_model_df).fit(cov_type="HC3")

    print(model.summary())

    evaluation_model_df["model_name"] = model_name
    evaluation_model_df["expected_log_non_AI_purchases"] = model.predict(evaluation_model_df)
    evaluation_model_df["expected_non_AI_purchases"] = np.expm1(
        evaluation_model_df["expected_log_non_AI_purchases"]
    )

    evaluation_model_df["purchase_protection"] = (
        evaluation_model_df["non_AI_purchases"]
        - evaluation_model_df["expected_non_AI_purchases"]
    )

    evaluation_model_df["purchase_protection_clipped"] = (
        evaluation_model_df["purchase_protection"].clip(lower=0)
    )

    actual = evaluation_model_df["non_AI_purchases"].sum()
    expected = evaluation_model_df["expected_non_AI_purchases"].sum()
    lift = evaluation_model_df["purchase_protection"].sum()
    lift_clipped = evaluation_model_df["purchase_protection_clipped"].sum()
    direct_ai_purchases = evaluation_model_df["AI_purchases"].sum()

    summary = {
        "model_name": model_name,
        "baseline_rows": len(baseline_model_df),
        "evaluation_rows": len(evaluation_model_df),
        "actual_non_AI_purchases": actual,
        "expected_non_AI_purchases": expected,
        "purchase_protection": lift,
        "purchase_protection_clipped": lift_clipped,
        "direct_AI_purchases": direct_ai_purchases,
        "total_AI_influenced_unclipped": direct_ai_purchases + lift,
        "total_AI_influenced_clipped": direct_ai_purchases + lift_clipped,
    }

    return model, evaluation_model_df, summary

model_cols_long = [
    "log_non_AI_purchases",
    "log_non_AI_sessions",
    "log_brand_trends",
    "sin_annual",
    "cos_annual",
    "trend",
    "holiday"
]

model_cols_recent_spend = [
    "log_non_AI_purchases",
    "log_non_AI_sessions",
    "log_brand_trends",
    "log_spend",
    "sin_annual",
    "cos_annual",
    "trend",
    "holiday"
]

weekly["post_sustained_AI"] = (
    weekly["date"] >= pd.to_datetime("2025-05-26")
).astype(int)

formula_post_ai = """
log_non_AI_purchases ~
    log_non_AI_sessions
    + log_brand_trends
    + post_sustained_AI
    + sin_annual
    + cos_annual
    + trend
    + holiday
"""

model_cols_post_ai = [
    "log_non_AI_purchases",
    "log_non_AI_sessions",
    "log_brand_trends",
    "post_sustained_AI",
    "sin_annual",
    "cos_annual",
    "trend",
    "holiday"
]

post_ai_df = (
    weekly[
        (weekly["date"] >= pd.to_datetime("2023-01-30"))
        & (weekly["date"] <= pd.to_datetime("2026-06-14"))
    ]
    .replace([np.inf, -np.inf], np.nan)
    .dropna(subset=model_cols_post_ai)
    .copy()
)

post_ai_model = smf.ols(
    formula_post_ai,
    data=post_ai_df
).fit(cov_type="HC3")

print(post_ai_model.summary())
post_coef = post_ai_model.params["post_sustained_AI"]
post_lift_pct = np.exp(post_coef) - 1

print("Post-AI purchase efficiency lift %:", post_lift_pct)
estimated_post_ai_lift = (
    evaluation["Ecommerce_purchases"].sum()
    * post_lift_pct
    / (1 + post_lift_pct)
)

print("Estimated post-AI lift purchases:", estimated_post_ai_lift)

model_long, eval_long, summary_long = run_counterfactual_model(
    model_name="long_baseline_no_spend",
    formula=formula_long,
    baseline_df=long_baseline_weekly,
    evaluation_df=evaluation_weekly,
    model_cols=model_cols_long
)

model_recent_spend, eval_recent_spend, summary_recent_spend = run_counterfactual_model(
    model_name="recent_baseline_with_spend",
    formula=formula_recent_spend,
    baseline_df=recent_baseline_weekly,
    evaluation_df=evaluation_weekly,
    model_cols=model_cols_recent_spend
)

summary_df = pd.DataFrame([summary_long, summary_recent_spend])
print("\nModel comparison:")
print(summary_df)

summary_df.to_csv("model_counterfactual_summary.csv", index=False)
eval_long.to_csv("evaluation_counterfactual_long_model.csv", index=False)
eval_recent_spend.to_csv("evaluation_counterfactual_recent_spend_model.csv", index=False)

# ====================================================
# 8. Channel-level CVR counterfactual
# ====================================================

channels = [
    "PPC",
    "SEO",
    "Direct",
    "Unassigned",
    "Affiliates",
    "Social",
    "Email",
    "Referral",
    "Other"
]

rows = []

for ch in channels:
    s_col = f"{ch}_sessions"
    p_col = f"{ch}_purchases"

    if s_col not in df.columns or p_col not in df.columns:
        continue

    long_baseline_sessions = long_baseline[s_col].sum()
    long_baseline_purchases = long_baseline[p_col].sum()

    long_baseline_cvr = (
        long_baseline_purchases / long_baseline_sessions
        if long_baseline_sessions > 0
        else np.nan
    )

    eval_sessions = evaluation[s_col].sum()
    eval_actual_purchases = evaluation[p_col].sum()

    eval_expected_purchases = eval_sessions * long_baseline_cvr

    lift = eval_actual_purchases - eval_expected_purchases

    rows.append({
        "channel": ch,
        "baseline_sessions": long_baseline_sessions,
        "baseline_purchases": long_baseline_purchases,
        "baseline_cvr": long_baseline_cvr,
        "eval_sessions": eval_sessions,
        "eval_actual_purchases": eval_actual_purchases,
        "eval_expected_purchases": eval_expected_purchases,
        "purchase_protection_lift": lift,
        "lift_pct_of_actual": (
            lift / eval_actual_purchases
            if eval_actual_purchases > 0
            else np.nan
        )
    })

channel_result = pd.DataFrame(rows)

direct_ai_sessions = evaluation["AI_sessions"].sum()
direct_ai_purchases = evaluation["AI_purchases"].sum()

indirect_actual = channel_result["eval_actual_purchases"].sum()
indirect_expected = channel_result["eval_expected_purchases"].sum()
indirect_lift = indirect_actual - indirect_expected

total_estimated_ai_influenced_purchases = (
    direct_ai_purchases + max(indirect_lift, 0)
)

print("\nChannel-level counterfactual:")
print(channel_result.sort_values("purchase_protection_lift", ascending=False))

print("\nChannel counterfactual totals:")
print("Indirect actual non-AI purchases:", indirect_actual)
print("Indirect expected non-AI purchases:", indirect_expected)
print("Estimated indirect purchase protection:", indirect_lift)
print("Direct AI sessions:", direct_ai_sessions)
print("Direct AI purchases:", direct_ai_purchases)
print("Total estimated AI-influenced purchases:")
print(total_estimated_ai_influenced_purchases)

channel_result.to_csv("channel_level_counterfactual.csv", index=False)

# ====================================================
# 9. Descriptive period summary
# ====================================================

long_baseline_daily_sessions = long_baseline["Sessions"].mean()
eval_daily_sessions = evaluation["Sessions"].mean()

session_change = eval_daily_sessions - long_baseline_daily_sessions
session_change_pct = session_change / long_baseline_daily_sessions

long_baseline_daily_purchases = long_baseline["Ecommerce_purchases"].mean()
eval_daily_purchases = evaluation["Ecommerce_purchases"].mean()

purchase_change = eval_daily_purchases - long_baseline_daily_purchases
purchase_change_pct = purchase_change / long_baseline_daily_purchases

long_baseline_cvr = long_baseline["Ecommerce_purchases"].sum() / long_baseline["Sessions"].sum()
eval_cvr = evaluation["Ecommerce_purchases"].sum() / evaluation["Sessions"].sum()

long_baseline_brand_index = long_baseline["brand_index"].mean()
eval_brand_index = evaluation["brand_index"].mean()

print("\nDescriptive period summary:")
print("Long baseline daily sessions:", long_baseline_daily_sessions)
print("Eval daily sessions:", eval_daily_sessions)
print("Session change %:", session_change_pct)

print("Long baseline daily purchases:", long_baseline_daily_purchases)
print("Eval daily purchases:", eval_daily_purchases)
print("Purchase change %:", purchase_change_pct)

print("Long baseline CVR:", long_baseline_cvr)
print("Eval CVR:", eval_cvr)
print("CVR change %:", (eval_cvr - long_baseline_cvr) / long_baseline_cvr)

print("Long baseline avg brand index:", long_baseline_brand_index)
print("Eval avg brand index:", eval_brand_index)
print(
    "Brand index change %:",
    (eval_brand_index - long_baseline_brand_index) / long_baseline_brand_index
)

# ====================================================
# 10. Optional: weekly summary export
# ====================================================

# weekly.to_csv("weekly_model_dataset.csv", index=False)


weekly["site_CVR"] = weekly["Ecommerce_purchases"] / weekly["Sessions"].replace(0, np.nan)

fig, ax1 = plt.subplots(figsize=(14, 6))

ax1.plot(
    weekly["date"],
    weekly["Sessions"],
    label="Sessions",
    color="tab:blue"
)

ax1.set_ylabel("Sessions", color="tab:blue")
ax1.tick_params(axis="y", labelcolor="tab:blue")

ax2 = ax1.twinx()

ax2.plot(
    weekly["date"],
    weekly["Ecommerce_purchases"],
    label="Purchases",
    color="tab:green"
)

ax2.set_ylabel("Purchases", color="tab:green")
ax2.tick_params(axis="y", labelcolor="tab:green")

plt.axvline(pd.to_datetime("2025-04-01"), color="grey", linestyle="--", label="AI Overviews live")
plt.axvline(pd.to_datetime("2025-05-25"), color="red", linestyle="--", label="Sustained AI growth")

fig.suptitle("Weekly Sessions and Purchases")
fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
plt.tight_layout()
plt.show()
# plt.savefig("plots/sessions_purchases.png", dpi=150)


# ----------------------------------------------------
# B. CVR over time
# ----------------------------------------------------

plt.figure(figsize=(14, 5))

plt.plot(
    weekly["date"],
    weekly["site_CVR"],
    label="Site CVR",
    color="tab:purple"
)

plt.axvline(pd.to_datetime("2025-04-01"), color="grey", linestyle="--", label="AI Overviews live")
plt.axvline(pd.to_datetime("2025-05-25"), color="red", linestyle="--", label="Sustained AI growth")

plt.title("Weekly Conversion Rate")
plt.ylabel("CVR")
plt.legend()
plt.tight_layout()
plt.show()
# plt.savefig("plots/cvr.png", dpi=150)


# ----------------------------------------------------
# C. Brand index vs purchases
# ----------------------------------------------------

fig, ax1 = plt.subplots(figsize=(14, 6))

ax1.plot(
    weekly["date"],
    weekly["brand_index"],
    label="Brand Trends Index",
    color="tab:orange"
)

ax1.set_ylabel("Brand Trends Index", color="tab:orange")
ax1.tick_params(axis="y", labelcolor="tab:orange")

ax2 = ax1.twinx()

ax2.plot(
    weekly["date"],
    weekly["Ecommerce_purchases"],
    label="Purchases",
    color="tab:green"
)

ax2.set_ylabel("Purchases", color="tab:green")
ax2.tick_params(axis="y", labelcolor="tab:green")

plt.axvline(pd.to_datetime("2025-04-01"), color="grey", linestyle="--")
plt.axvline(pd.to_datetime("2025-05-25"), color="red", linestyle="--")

fig.suptitle("Brand Search Interest vs Purchases")
fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
plt.tight_layout()
plt.show()
# plt.savefig("plots/brand_index_vs_purchases.png", dpi=150)


# ----------------------------------------------------
# D. Actual vs expected non-AI purchases
# ----------------------------------------------------

evaluation_model_df = eval_long.copy()
# evaluation_model_df = eval_recent_spend.copy()

plt.figure(figsize=(14, 6))

plt.plot(
    evaluation_model_df["date"],
    evaluation_model_df["non_AI_purchases"],
    label="Actual non-AI purchases",
    color="tab:green",
    linewidth=2
)

plt.plot(
    evaluation_model_df["date"],
    evaluation_model_df["expected_non_AI_purchases"],
    label="Expected non-AI purchases",
    color="tab:red",
    linestyle="--",
    linewidth=2
)

plt.axvline(pd.to_datetime("2025-05-25"), color="red", linestyle="--", label="Sustained AI growth")

plt.title("Actual vs Expected Non-AI Purchases During Evaluation Period")
plt.ylabel("Weekly purchases")
plt.legend()
plt.tight_layout()
plt.show()
# plt.savefig("plots/actual_vs_expected_purchases.png", dpi=150)


# ----------------------------------------------------
# E. Purchase protection over time
# ----------------------------------------------------

plt.figure(figsize=(14, 6))

plt.bar(
    evaluation_model_df["date"],
    evaluation_model_df["purchase_protection"],
    color=np.where(
        evaluation_model_df["purchase_protection"] >= 0,
        "tab:green",
        "tab:red"
    ),
    width=5
)

plt.axhline(0, color="black", linewidth=1)
plt.axvline(pd.to_datetime("2025-05-25"), color="red", linestyle="--")

plt.title("Weekly Purchase Protection: Actual - Expected Non-AI Purchases")
plt.ylabel("Purchase protection")
plt.tight_layout()
plt.show()
# plt.savefig("plots/purchase_protection.png", dpi=150)


# ----------------------------------------------------
# F. Cumulative purchase protection
# ----------------------------------------------------

evaluation_model_df["cumulative_purchase_protection"] = (
    evaluation_model_df["purchase_protection"].cumsum()
)

plt.figure(figsize=(14, 6))

plt.plot(
    evaluation_model_df["date"],
    evaluation_model_df["cumulative_purchase_protection"],
    label="Cumulative purchase protection",
    color="tab:blue",
    linewidth=2
)

plt.axhline(0, color="black", linewidth=1)
plt.axvline(pd.to_datetime("2025-05-25"), color="red", linestyle="--")

plt.title("Cumulative Purchase Protection During Evaluation Period")
plt.ylabel("Cumulative purchases above expected")
plt.legend()
plt.tight_layout()
plt.show()
# plt.savefig("plots/cumulative_purchase_protection.png", dpi=150)


# ----------------------------------------------------
# G. AI sessions/share over time
# ----------------------------------------------------

weekly["AI_session_share"] = weekly["AI_sessions"] / weekly["Sessions"].replace(0, np.nan)

fig, ax1 = plt.subplots(figsize=(14, 6))

ax1.plot(
    weekly["date"],
    weekly["AI_sessions"],
    label="AI sessions",
    color="tab:red"
)

ax1.set_ylabel("AI sessions", color="tab:red")
ax1.tick_params(axis="y", labelcolor="tab:red")

ax2 = ax1.twinx()

ax2.plot(
    weekly["date"],
    weekly["AI_session_share"],
    label="AI session share",
    color="tab:blue"
)

ax2.set_ylabel("AI session share", color="tab:blue")
ax2.tick_params(axis="y", labelcolor="tab:blue")

plt.axvline(pd.to_datetime("2025-04-01"), color="grey", linestyle="--")
plt.axvline(pd.to_datetime("2025-05-25"), color="red", linestyle="--")

fig.suptitle("AI Sessions and AI Session Share")
fig.legend(loc="upper left", bbox_to_anchor=(0.1, 0.9))
plt.tight_layout()
plt.show()
# plt.savefig("plots/ai_sessions_share.png", dpi=150)

eval_long = eval_long.copy()

plt.figure(figsize=(14, 6))
plt.bar(
    eval_long["date"],
    eval_long["purchase_protection"],
    color=np.where(eval_long["purchase_protection"] >= 0, "tab:green", "tab:red"),
    width=5
)

plt.axhline(0, color="black")
plt.axvline(pd.to_datetime("2025-05-26"), color="red", linestyle="--")
plt.title("Long Model Weekly Purchase Protection")
plt.ylabel("Actual - Expected non-AI purchases")
plt.tight_layout()
plt.show()