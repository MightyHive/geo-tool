import glob
import gc
import importlib.util
import os
from pathlib import Path
import re

# if importlib.util.find_spec("numpyro") is None:
#   !pip install -q numpyro arviz

# Run four independent fits in parallel so we can check they agree. This must be
# set before JAX starts up, so keep it at the very top of the module.
os.environ["XLA_FLAGS"] = "--xla_force_host_platform_device_count=4"

import arviz as az
import jax
import jax.numpy as jnp

# Non-interactive backend: this is a batch script, not a notebook, and the
# macOS "MacOSX" GUI backend hangs interpreter shutdown in an infinite loop of
# "SystemError: NULL object passed to Py_BuildValue" once the last figure is
# closed (a long-standing Cocoa event-loop bug). Save PNGs instead of showing
# windows so the process actually exits.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import numpyro
import numpyro.distributions as dist
import pandas as pd
from numpyro.infer import MCMC, NUTS, Predictive

numpyro.set_host_device_count(4)
pd.set_option("display.width", 120)

print("numpyro", numpyro.__version__)
print("devices:", jax.devices())

AI_CHANNEL = "AI Chatbots"
MODELLING_ROOT = Path(
    os.environ.get("AI_IMPACT_MODELLING_ROOT", Path(__file__).resolve().parent)
).expanduser().resolve()
PROPERTIES_PATH = Path(
    os.environ.get("AI_IMPACT_PROPERTIES_PATH", MODELLING_ROOT / "properties.csv")
).expanduser().resolve()
SESSIONS_DIR = Path(
    os.environ.get("AI_IMPACT_SESSIONS_DIR", MODELLING_ROOT / "sessions")
).expanduser().resolve()
GOOGLE_TRENDS_DIR = Path(
    os.environ.get("AI_IMPACT_TRENDS_DIR", MODELLING_ROOT / "google_trends")
).expanduser().resolve()
PLOTS_DIR = Path(
    os.environ.get("AI_IMPACT_PLOTS_DIR", MODELLING_ROOT / "plots")
).expanduser().resolve()
AI_SCALE_REFERENCE_END = pd.Timestamp("2026-06-01")
AI_TAIL_CAVEAT_START = pd.Timestamp("2026-06-01")
RUN_AI_SENSITIVITY = os.environ.get("RUN_AI_SENSITIVITY", "1") == "1"
SENSITIVITY_WARMUP = int(os.environ.get("SENSITIVITY_WARMUP", "1000"))
SENSITIVITY_SAMPLES = int(os.environ.get("SENSITIVITY_SAMPLES", "1000"))
AI_SENSITIVITY_SPECS = {
    value.strip()
    for value in os.environ.get("AI_SENSITIVITY_SPECS", "").split(",")
    if value.strip()
}
# Week-start dates in this window (15 Dec – 7 Jan) are dropped from the fit
# and from the AI-influence evaluation: Christmas/NY retail spikes swamp the
# smoother AI-adoption and brand-Trends signals we'd otherwise be estimating.
CHRISTMAS_START_MD = 1215  # month*100 + day
CHRISTMAS_END_MD = 107

def save_fig(fig, filename):
    os.makedirs(PLOTS_DIR, exist_ok=True)
    path = os.path.join(PLOTS_DIR, filename)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"  saved {path}")

def count_files(website_type, vertical, sessions_root=SESSIONS_DIR):
    dir_path = Path(sessions_root) / str(website_type) / str(vertical)
    if not dir_path.is_dir():
        return None
    count = 0
    for path in dir_path.iterdir():
        if path.is_file():
            count += 1
    print('File count:', count)
    return count

def aggregate_site_data(
    properties_path=PROPERTIES_PATH,
    sessions_root=SESSIONS_DIR,
    *,
    expected_site_count=61,
    require_exact_totals=True,
):
    """
    Builds the per-site weekly SEO/Direct panel (as before), plus a portfolio-wide
    weekly AI-adoption index (AI Chatbots' share of total sessions across every
    property, every channel). The AI index is computed from every site's raw data
    before the "positive SEO & Direct" row filter below, since it needs to reflect
    the whole portfolio's behaviour, not just the rows that survive that filter.
    """
    property_df = pd.read_csv(properties_path)
    property_df = property_df[
        property_df["website_type"].astype(str).str.lower() != "app"
    ].copy()
    active_site_ids = set(property_df["name"].astype(str))
    if expected_site_count is not None and len(active_site_ids) != expected_site_count:
        raise ValueError(
            f"Expected {expected_site_count} eligible web properties, found "
            f"{len(active_site_ids)} in {properties_path}"
        )
    vertical_list = property_df['vertical'].unique()
    count_df = pd.DataFrame(columns=['website_type', 'vertical', 'count'])
    frames, meta = [], []
    seen_site_ids = set()
    portfolio_ai = pd.Series(dtype=float)
    portfolio_total = pd.Series(dtype=float)

    for website_type in ['advertiser', 'publisher']:
        for vertical in vertical_list:
            count = count_files(website_type, vertical, sessions_root)
            if count is None:
                continue
            count_df.loc[len(count_df)] = [website_type, vertical, count]
            dir_path = Path(sessions_root) / str(website_type) / str(vertical)
            for e in os.scandir(dir_path):
                if not e.is_file():
                    continue
                site_df = pd.read_csv(e.path)
                if site_df.empty:
                    continue
                site_id = str(site_df["name"].iloc[0])
                if site_id not in active_site_ids:
                    print(f"  skipping excluded app property {site_id}")
                    continue
                if site_id in seen_site_ids:
                    raise ValueError(f"Duplicate session export for {site_id}")
                seen_site_ids.add(site_id)
                site_df["date"] = pd.to_datetime(site_df["date"])
                site_df["week_start"] = site_df["date"].dt.to_period("W-SAT").dt.start_time
                # The GA4 pull starts mid-week and ends mid-week, so the first and
                # last week of each site would otherwise look like a traffic collapse.
                days_per_week = site_df.groupby("week_start")["date"].nunique()
                complete_weeks = days_per_week[days_per_week == 7].index
                site_df = site_df[site_df["week_start"].isin(complete_weeks)]
                if site_df.empty:
                    continue

                # Portfolio-wide AI-adoption denominator. New exports carry the
                # exact undimensioned GA4 total because Sessions is not additive
                # across channel dimensions. Retain the channel-sum fallback for
                # historical exports created before "All Sessions" was added.
                exact_total_rows = site_df[
                    site_df["custom_channel_grouping"] == "All Sessions"
                ]
                if exact_total_rows.empty:
                    if require_exact_totals:
                        raise ValueError(
                            f"{site_id} has no exact weekly All Sessions rows"
                        )
                    site_week_total = site_df.groupby("week_start")["sessions"].sum()
                else:
                    site_week_total = exact_total_rows.groupby("week_start")[
                        "sessions"
                    ].sum()
                site_week_ai = (
                    site_df[site_df["custom_channel_grouping"] == AI_CHANNEL]
                    .groupby("week_start")["sessions"]
                    .sum()
                )
                portfolio_total = portfolio_total.add(site_week_total, fill_value=0.0)
                portfolio_ai = portfolio_ai.add(site_week_ai, fill_value=0.0)

                weekly = (
                    site_df.groupby(
                        ["name", "website_type", "vertical", "week_start", "custom_channel_grouping"],
                        as_index=False,
                    )["sessions"]
                    .sum()
                )
                wide = (
                    weekly.pivot_table(
                        index=["name", "website_type", "vertical", "week_start"],
                        columns="custom_channel_grouping",
                        values="sessions",
                        aggfunc="sum",
                        fill_value=0,
                    )
                    .reset_index()
                )
                for col in ("SEO", "Direct"):
                    if col not in wide.columns:
                        wide[col] = 0
                frame_rows = wide.rename(columns={
                    "name": "site",
                    "vertical": "category",
                    "week_start": "date",
                    "SEO": "seo_sessions",
                    "Direct": "direct_sessions",
                })[["site", "category", "date", "seo_sessions", "direct_sessions"]]
                # properties.csv is the source of truth for the vertical, so trust
                # the folder over the copy embedded in the session file.
                file_vertical = site_df["vertical"].iloc[0]
                if file_vertical != vertical:
                    print(f"  vertical mismatch in {e.name}: file says "
                          f"{file_vertical!r}, folder says {vertical!r}")
                # Production scoring uses three stable model categories:
                # advertisers retain retail/services, while all publishers pool
                # together regardless of their legacy automotive/lifestyle folder.
                model_category = (
                    "publisher"
                    if website_type == "publisher"
                    else (
                        vertical
                        if str(vertical).startswith("advertiser-")
                        else f"advertiser-{vertical}"
                    )
                )
                frame_rows["category"] = model_category
                frames.append(frame_rows)
                meta.append({
                    "site": site_df["name"].iloc[0],
                    "website_type": website_type,
                    "category": model_category,
                    "n_weeks": frame_rows["date"].nunique(),
                })

    missing_site_ids = sorted(active_site_ids - seen_site_ids)
    if missing_site_ids:
        raise ValueError(
            f"Missing session exports for {len(missing_site_ids)} eligible properties: "
            f"{missing_site_ids}"
        )
    if not frames:
        raise ValueError(f"No eligible session exports found under {sessions_root}")
    panel = pd.concat(frames, ignore_index=True)

    ai_index = (
        pd.DataFrame({
            "week_start": portfolio_total.index,
            "ai_sessions_total": portfolio_ai.reindex(portfolio_total.index, fill_value=0.0).to_numpy(),
            "all_sessions_total": portfolio_total.to_numpy(),
        })
        .sort_values("week_start")
        .reset_index(drop=True)
    )
    ai_index["ai_share"] = ai_index["ai_sessions_total"] / ai_index["all_sessions_total"]

    # The model works in logs, so a week with no SEO or no direct traffic cannot
    # be used. These are rare enough to drop outright.
    positive = (panel["seo_sessions"] > 0) & (panel["direct_sessions"] > 0)
    dropped = int((~positive).sum())
    panel = panel[positive]

    week_map = {d: i for i, d in enumerate(sorted(panel["date"].unique()))}
    panel["week"] = panel["date"].map(week_map)
    panel = panel[["site", "category", "week", "date", "seo_sessions", "direct_sessions"]].copy()

    sites = (
        pd.DataFrame(meta)
        .merge(panel.groupby("site", as_index=False)["week"].nunique(), on="site", how="inner")
        .drop(columns="n_weeks")
        .rename(columns={"week": "n_weeks"})
    )
    categories = sorted(sites["category"].unique())

    print(f"{len(sites)} sites, {len(panel):,} site-weeks, {dropped} zero-traffic weeks dropped")
    print(f"AI-adoption index: {len(ai_index)} portfolio-weeks, "
          f"ai_share {ai_index['ai_share'].iloc[0]:.5f} -> {ai_index['ai_share'].iloc[-1]:.5f}")
    print(sites.head())
    print(panel.head())
    return panel, sites, categories, ai_index

def build_ai_adoption_index(ai_index):
    """
    Builds a compressed, frozen-scale AI-adoption predictor.

    Raw ``ai_share`` is tiny and extremely right-skewed. Z-scoring it directly
    made the final week 7.7 SD above the historical mean, allowing a handful of
    late weeks to dominate the linear coefficient. ``log1p(ai_share)`` alone
    would not help because log1p(x) ~= x at this scale, so first divide by the
    median positive share in a fixed pre-June-2026 reference window.

    The transformed value is then standardised using mean/std from that same
    fixed window. These constants must not move when new weeks arrive: otherwise
    every future data refresh would silently rescale all historical predictors.

    ``ai_signal_capped`` is a sensitivity-only variant. Positive transformed
    values are winsorised at the active reference window's P3/P97; true zero
    weeks remain exactly zero before standardisation so the no-adoption baseline
    is retained.
    """
    idx = ai_index.sort_values("week_start").reset_index(drop=True).copy()
    idx["week"] = np.arange(len(idx))
    reference = idx[idx["week_start"] < AI_SCALE_REFERENCE_END]
    active_reference = reference[reference["ai_share"] > 0]
    if active_reference.empty:
        raise ValueError("No positive AI-adoption weeks in the scale reference window")

    share_scale = float(active_reference["ai_share"].median())
    idx["ai_log_scaled"] = np.log1p(idx["ai_share"] / share_scale)
    reference_transformed = idx.loc[reference.index, "ai_log_scaled"]
    transform_mean = float(reference_transformed.mean())
    transform_std = float(reference_transformed.std(ddof=0)) or 1.0
    idx["ai_signal"] = (idx["ai_log_scaled"] - transform_mean) / transform_std

    active_transformed = idx.loc[active_reference.index, "ai_log_scaled"]
    cap_low, cap_high = np.percentile(active_transformed, [3, 97])
    capped = idx["ai_log_scaled"].copy()
    positive = idx["ai_share"] > 0
    capped.loc[positive] = capped.loc[positive].clip(cap_low, cap_high)
    capped.loc[~positive] = 0.0
    idx["ai_log_scaled_capped"] = capped
    idx["ai_signal_capped"] = (capped - transform_mean) / transform_std

    metadata = {
        "share_scale": share_scale,
        "transform_mean": transform_mean,
        "transform_std": transform_std,
        "cap_low": float(cap_low),
        "cap_high": float(cap_high),
        "reference_end": AI_SCALE_REFERENCE_END,
    }
    print(
        "AI predictor: log1p(ai_share / reference median positive share), "
        f"scale={share_scale:.8f}, reference < {AI_SCALE_REFERENCE_END.date()}, "
        f"active transformed P3/P97={cap_low:.3f}/{cap_high:.3f}"
    )
    return idx, metadata

def detect_ai_ramp_start(ai_index, baseline_weeks=26, ramp_multiple=3.0):
    """
    Heuristic split point between "pre-AI baseline" and "AI proliferation" weeks:
    the first week where ai_share exceeds ``ramp_multiple`` times the average of
    the first ``baseline_weeks`` weeks. Falls back to the last week if the ramp
    has not (yet) cleared that bar.
    """
    idx = ai_index.sort_values("week_start").reset_index(drop=True)
    baseline_mean = max(float(idx["ai_share"].iloc[:baseline_weeks].mean()), 1e-9)
    ramp_rows = idx.index[idx["ai_share"] > ramp_multiple * baseline_mean]
    if len(ramp_rows) == 0:
        return idx["week_start"].iloc[-1]
    return idx.loc[ramp_rows[0], "week_start"]

def ai_baseline_value(ai_index, ramp_start, signal_col="ai_signal"):
    """Mean transformed AI signal over pre-ramp no-adoption weeks."""
    baseline = ai_index[ai_index["week_start"] < ramp_start]
    if baseline.empty:
        baseline = ai_index.iloc[: max(len(ai_index) // 4, 1)]
    return float(baseline[signal_col].mean())

def _parse_trends_file(path):
    """Parses a Google Trends 'Interest over time' weekly CSV export."""
    with open(path, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if line.strip().lower().startswith(("week,", "day,"))),
        None,
    )
    if header_idx is None:
        raise ValueError(f"{path}: could not find a 'Week,...' header row")
    df = pd.read_csv(path, skiprows=header_idx)
    date_col, term_col = df.columns[0], df.columns[1]
    df = df.rename(columns={date_col: "week_start", term_col: "brand_interest"})
    df["week_start"] = pd.to_datetime(df["week_start"])
    # Google Trends prints values below 1 as the literal string "<1".
    df["brand_interest"] = (
        df["brand_interest"].astype(str).str.strip().str.replace("<1", "0.5", regex=False)
    )
    df["brand_interest"] = pd.to_numeric(df["brand_interest"], errors="coerce")
    return df[["week_start", "brand_interest"]]

def load_brand_trends(sites, trends_dir=GOOGLE_TRENDS_DIR):
    """
    Loads whichever ``google_trends_property_*.csv`` files currently exist.

    More of these land over time (one per property); the pipeline works with
    whatever subset is present today and needs no code change as more arrive.
    """
    site_names = set(sites["site"])
    pattern = os.path.join(trends_dir, "google_trends_property_*.csv")
    frames = []
    covered_sites = set()
    for path in sorted(glob.glob(pattern)):
        match = re.search(r"(property_\d+)", os.path.basename(path))
        if not match:
            continue
        site = match.group(1)
        if site not in site_names:
            continue
        df = _parse_trends_file(path)
        df["site"] = site
        frames.append(df)
        covered_sites.add(site)

    n_sites = len(site_names)
    print(f"Google Trends coverage: {len(covered_sites)} of {n_sites} sites have a Trends file "
          f"({sorted(covered_sites) if covered_sites else 'none'})")
    if not frames:
        return pd.DataFrame(columns=["site", "week_start", "brand_interest"])
    return pd.concat(frames, ignore_index=True)

def fourier_terms(weeks):
    return np.column_stack([
        np.sin(2 * np.pi * weeks / 52), np.cos(2 * np.pi * weeks / 52),
        np.sin(4 * np.pi * weeks / 52), np.cos(4 * np.pi * weeks / 52),
])

def is_christmas_week(dates):
    """True for week-starts that fall in the Christmas/NY trading lull."""
    d = pd.to_datetime(dates)
    md = d.dt.month * 100 + d.dt.day
    return (md >= CHRISTMAS_START_MD) | (md <= CHRISTMAS_END_MD)

def prepare_model_data(panel, sites, ai_index, trends, vertical_list):
    """
    Builds the NumPyro-ready model_data/outcomes for the two parallel models.

    Sites without a Google Trends file are dropped from the fitted panel (inner
    join) - documented rather than silently ignored, since today that is most of
    the portfolio. ``vertical_list`` is kept at its full size (not restricted to
    covered sites) so the category dimension does not need to change shape as more
    Trends files are added later.
    """
    if trends.empty:
        raise ValueError(
            "No Google Trends files found under "
            f"'{GOOGLE_TRENDS_DIR}/'; add at least one google_trends_property_*.csv"
        )

    n_sites_before = panel["site"].nunique()
    panel = panel.merge(
        trends, left_on=["site", "date"], right_on=["site", "week_start"], how="inner"
    )
    panel = panel.drop(columns="week_start")
    n_sites_after = panel["site"].nunique()
    print(f"Trends-covered panel: {n_sites_after} of {n_sites_before} sites retained "
          f"({len(panel):,} site-weeks)")

    panel = panel.merge(
        ai_index[["week_start", "ai_signal", "ai_signal_capped", "ai_share"]],
        left_on="date",
        right_on="week_start",
        how="left",
    )
    panel = panel.drop(columns="week_start")
    missing_ai = int(panel["ai_signal"].isna().sum())
    if missing_ai:
        print(f"  {missing_ai} site-weeks fall outside the AI index's date range; dropping them")
        panel = panel[panel["ai_signal"].notna()].copy()

    christmas = is_christmas_week(panel["date"])
    n_christmas = int(christmas.sum())
    n_christmas_weeks = int(panel.loc[christmas, "date"].nunique())
    if n_christmas:
        print(f"  masking Christmas/NY period: dropping {n_christmas:,} site-weeks "
              f"across {n_christmas_weeks} distinct weeks "
              f"(week-start {CHRISTMAS_START_MD // 100:02d}-{CHRISTMAS_START_MD % 100:02d} "
              f"through {CHRISTMAS_END_MD // 100:02d}-{CHRISTMAS_END_MD % 100:02d})")
        panel = panel.loc[~christmas].copy()

    sites = sites[sites["site"].isin(panel["site"].unique())].reset_index(drop=True)
    # n_weeks is used in plot titles; refresh after the Christmas drop.
    sites = sites.drop(columns="n_weeks").merge(
        panel.groupby("site", as_index=False)["week"].nunique().rename(columns={"week": "n_weeks"}),
        on="site",
        how="inner",
    )
    site_names = sites["site"].tolist()
    site_idx = pd.Categorical(panel["site"], categories=site_names).codes
    cat_of_site = pd.Categorical(sites["category"], categories=vertical_list).codes
    # A -1 code would silently index from the end of the category arrays.
    assert site_idx.min() >= 0, "panel contains sites missing from the sites table"
    assert cat_of_site.min() >= 0, "sites contains categories missing from vertical_list"

    panel["log_seo"] = np.log(panel["seo_sessions"])
    panel["log_direct"] = np.log(panel["direct_sessions"])
    # Google Trends can genuinely print 0, not just "<1"; floor it so log() is finite.
    panel["log_trends"] = np.log(panel["brand_interest"].clip(lower=0.5))
    panel["log_trends_c"] = panel.groupby("site")["log_trends"].transform(lambda s: s - s.mean())
    panel["trend"] = panel.groupby("site")["week"].transform(lambda s: (s - s.mean()) / 52.0)

    model_data = dict(
        site_idx=jnp.asarray(site_idx),
        cat_of_site=jnp.asarray(cat_of_site),
        trend=jnp.asarray(panel["trend"].to_numpy()),
        fourier=jnp.asarray(fourier_terms(panel["week"].to_numpy())),
        ai_signal=jnp.asarray(panel["ai_signal"].to_numpy()),
        log_trends_c=jnp.asarray(panel["log_trends_c"].to_numpy()),
        n_sites=len(site_names),
        n_categories=len(vertical_list),
    )
    outcomes = {
        "log_seo": jnp.asarray(panel["log_seo"].to_numpy()),
        "log_direct": jnp.asarray(panel["log_direct"].to_numpy()),
    }

    print("site_idx maps each of the", len(panel), "rows to one of", len(site_names), "sites")
    print("cat_of_site maps each of the", len(site_names), "sites to one of",
        len(vertical_list), "categories")
    return panel, sites, site_names, model_data, outcomes

def plot_seo_direct(panel, sites, site_names=None):
    if site_names is None:
        site_names = sites.sort_values("n_weeks", ascending=False)["site"].head(3).tolist()
    fig, axes = plt.subplots(1, len(site_names), figsize=(14, 3.4))
    for ax, site in zip(np.atleast_1d(axes), site_names):
        d = panel[panel["site"] == site]
        row = sites[sites["site"] == site].iloc[0]
        ax.plot(d["week"], d["log_trends_c"], lw=1.2, label="brand search interest")
        ax.plot(d["week"], d["log_seo"] - d["log_seo"].mean(), lw=1.2, label="SEO sessions")
        ax.set_title(f"{site} - {row['category']}, {row['n_weeks']} weeks", fontsize=10)
        ax.set_xlabel("week")
    axes[0].set_ylabel("log, centred on each site's average")
    axes[0].legend(fontsize=8)
    fig.suptitle("Brand search interest and SEO sessions", y=1.04)
    save_fig(fig, "seo_direct_examples.png")

def plot_ai_adoption_index(ai_index, ramp_start):
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot(ai_index["week_start"], ai_index["ai_share"] * 100, lw=1.5)
    ax.axvline(pd.Timestamp(ramp_start), color="grey", ls="--", lw=1,
               label=f"detected AI ramp start ({pd.Timestamp(ramp_start).date()})")
    ax.set_ylabel("AI Chatbots' share of all sessions (%)")
    ax.set_xlabel("week")
    ax.set_title("Portfolio-wide AI-adoption index")
    ax.legend(fontsize=8)
    save_fig(fig, "ai_adoption_index.png")

def single_regression(panel, sites, site_names):
    """
    Naive per-site OLS baseline for beta_ai / beta_trends - "every site for
    itself", with no pooling across sites or categories. Used only as a point of
    comparison against the hierarchical posterior; real data has no known true
    coefficient to check against (unlike the notebook's simulated portfolio).
    """
    rows = []
    for site in site_names:
        d = panel[panel["site"] == site]
        X = np.column_stack([
            np.ones(len(d)), d["ai_signal"], d["log_trends_c"], d["trend"],
            fourier_terms(d["week"].to_numpy()),
        ])
        for outcome_col, label in (("log_seo", "seo"), ("log_direct", "direct")):
            y = d[outcome_col].to_numpy()
            coef, *_ = np.linalg.lstsq(X, y, rcond=None)
            residuals = y - X @ coef
            dof = max(len(d) - X.shape[1], 1)
            covariance = (residuals @ residuals / dof) * np.linalg.pinv(X.T @ X)
            rows.append({
                "site": site,
                "outcome": label,
                "n_weeks": len(d),
                "beta_ai_ols": coef[1],
                "se_ai_ols": np.sqrt(max(covariance[1, 1], 0.0)),
                "beta_trends_ols": coef[2],
                "se_trends_ols": np.sqrt(max(covariance[2, 2], 0.0)),
            })
    return pd.DataFrame(rows)

def outcome_model(site_idx, cat_of_site, trend, fourier, ai_signal, log_trends_c,
                   n_sites, n_categories, y=None):
    """
    Same three-level hierarchy as the notebook's ``seo_model`` (all websites ->
    category -> site), now with two regressors of interest - AI adoption and
    brand search interest - each pooled the same way, instead of using the other
    session channel as a predictor. Fit once for log(SEO), once for log(Direct).

    ``ai_signal`` is portfolio-wide: every site sees the identical value in a
    given week, so there is no cross-sectional variation to identify a
    site-specific slope from - only two levels (global -> category), not three.
    Giving it a site layer anyway (as an earlier version did) added ~n_sites
    free parameters with nothing but each site's own noise/trend to fit them
    to, which is exactly what overfit: divergences spiked and the counterfactual
    credible intervals blew up and flipped sign between runs. ``log_trends_c``
    genuinely varies per site (each site's own Trends series), so it keeps the
    full three-level treatment below.

    Every ``Normal(parent, tau)`` draw below - i.e. every place a hierarchy's
    location or scale is itself a sampled value, not a fixed literal - is
    written non-centred: sample a unit normal ``*_z`` and scale it, rather than
    sampling the target distribution directly. Same prior, far kinder geometry
    for NUTS once ``tau`` gets small, and cheap enough that there is no reason
    to wait for divergence warnings before using it everywhere it applies. Only
    the top-level priors (``mu_ai``, ``mu_alpha``, ``mu_season``, ...), whose
    parameters are fixed literals rather than samples, are left centred - there
    is no funnel to avoid when nothing upstream is unknown.
    """

    # ---- two-level joint prior on the AI-adoption coefficient (global -> category) --
    mu_ai = numpyro.sample("mu_ai", dist.Normal(0.0, 0.5))
    tau_cat_ai = numpyro.sample("tau_cat_ai", dist.HalfNormal(0.3))

    with numpyro.plate("category_ai", n_categories):
        beta_ai_cat_z = numpyro.sample("beta_ai_cat_z", dist.Normal(0.0, 1.0))
    beta_ai_cat = numpyro.deterministic("beta_ai_cat", mu_ai + tau_cat_ai * beta_ai_cat_z)

    # ---- three-level joint prior on the brand-search-interest coefficient ----
    mu_trends = numpyro.sample("mu_trends", dist.Normal(0.3, 0.3))
    tau_cat_trends = numpyro.sample("tau_cat_trends", dist.HalfNormal(0.25))
    tau_site_trends = numpyro.sample("tau_site_trends", dist.HalfNormal(0.2))

    with numpyro.plate("category_trends", n_categories):
        beta_trends_cat_z = numpyro.sample("beta_trends_cat_z", dist.Normal(0.0, 1.0))
    beta_trends_cat = numpyro.deterministic(
        "beta_trends_cat", mu_trends + tau_cat_trends * beta_trends_cat_z)

    with numpyro.plate("site_trends", n_sites):
        beta_trends_site_z = numpyro.sample("beta_trends_site_z", dist.Normal(0.0, 1.0))
    beta_trends_site = numpyro.deterministic(
        "beta_trends_site", beta_trends_cat[cat_of_site] + tau_site_trends * beta_trends_site_z)

    # ---- everything else we need to control for -----------------------------
    # site size, site trend and site noise all get a shared prior too, using the
    # same idea one level shallower: all sites, then this site.
    mu_alpha = numpyro.sample("mu_alpha", dist.Normal(9.0, 3.0))
    tau_alpha = numpyro.sample("tau_alpha", dist.HalfNormal(3.0))
    mu_trend_coef = numpyro.sample("mu_trend_coef", dist.Normal(0.0, 0.1))
    tau_trend_coef = numpyro.sample("tau_trend_coef", dist.HalfNormal(0.1))
    mu_log_sigma = numpyro.sample("mu_log_sigma", dist.Normal(-2.0, 1.0))
    tau_log_sigma = numpyro.sample("tau_log_sigma", dist.HalfNormal(0.5))

    with numpyro.plate("site_controls", n_sites):
        alpha_z = numpyro.sample("alpha_z", dist.Normal(0.0, 1.0))
        alpha = numpyro.deterministic("alpha", mu_alpha + tau_alpha * alpha_z)
        beta_trend_z = numpyro.sample("beta_trend_z", dist.Normal(0.0, 1.0))
        beta_trend = numpyro.deterministic(
            "beta_trend", mu_trend_coef + tau_trend_coef * beta_trend_z)
        log_sigma_z = numpyro.sample("log_sigma_z", dist.Normal(0.0, 1.0))
        log_sigma = numpyro.deterministic("log_sigma", mu_log_sigma + tau_log_sigma * log_sigma_z)
    sigma = numpyro.deterministic("sigma", jnp.exp(log_sigma))

    # seasonality is shared across the sites in a category
    mu_season = numpyro.sample(
        "mu_season", dist.Normal(0.0, 0.2).expand([fourier.shape[1]]).to_event(1))
    tau_season = numpyro.sample("tau_season", dist.HalfNormal(0.2))
    with numpyro.plate("fourier", fourier.shape[1], dim=-1):
        with numpyro.plate("category_season", n_categories, dim=-2):
            beta_season_z = numpyro.sample("beta_season_z", dist.Normal(0.0, 1.0))
    beta_season = numpyro.deterministic("beta_season", mu_season + tau_season * beta_season_z)

    # ---- put it together and compare with what we observed ------------------
    expected_log_y = (
        alpha[site_idx]
        + beta_trend[site_idx] * trend
        + beta_ai_cat[cat_of_site[site_idx]] * ai_signal
        + beta_trends_site[site_idx] * log_trends_c
        + jnp.sum(fourier * beta_season[cat_of_site[site_idx]], axis=-1)
    )
    numpyro.deterministic("expected_log_y", expected_log_y)
    with numpyro.plate("observations", len(ai_signal)):
        numpyro.sample("y", dist.Normal(expected_log_y, sigma[site_idx]), obs=y)

def fit_outcome_model(model_data, y, num_warmup=1000, num_samples=1000, num_chains=4, seed=0):
    mcmc = MCMC(
        NUTS(outcome_model, target_accept_prob=0.95),
        num_warmup=num_warmup,
        num_samples=num_samples,
        num_chains=num_chains,
        progress_bar=False,
    )
    mcmc.run(jax.random.PRNGKey(seed), y=y, extra_fields=("diverging",), **model_data)
    n_divergences = int(np.sum(np.asarray(mcmc.get_extra_fields()["diverging"])))
    print("divergences:", n_divergences)
    return mcmc

def _summarize_draws(values):
    values = np.asarray(values, dtype=float)
    return {
        "mean": float(values.mean()),
        "low": float(np.percentile(values, 3)),
        "high": float(np.percentile(values, 97)),
    }

def compute_ai_influence(mcmc, model_data, panel, baseline_ai_signal, eval_mask, session_col):
    """
    Counterfactual-freeze estimate of "sessions influenced by AI".

    For every posterior draw, computes the model's expected sessions under the
    actual AI-index trajectory and again with the AI index held at its pre-ramp
    baseline, holding every other term (site intercept, trend, seasonality, brand
    Trends) fixed. ``exp(actual) - exp(counterfactual)``, summed over the
    evaluation window, is the "sessions influenced" figure - pushed through all
    4,000 posterior draws so the uncertainty comes along for the ride.
    """
    posterior = mcmc.get_samples()
    site_idx = np.asarray(model_data["site_idx"])
    cat_of_site = np.asarray(model_data["cat_of_site"])
    trend = np.asarray(model_data["trend"])
    fourier = np.asarray(model_data["fourier"])
    log_trends_c = np.asarray(model_data["log_trends_c"])
    ai_signal = np.asarray(model_data["ai_signal"])
    row_cat = cat_of_site[site_idx]

    alpha = np.asarray(posterior["alpha"])[:, site_idx]
    beta_trend = np.asarray(posterior["beta_trend"])[:, site_idx]
    beta_ai_cat = np.asarray(posterior["beta_ai_cat"])[:, row_cat]
    beta_trends_site = np.asarray(posterior["beta_trends_site"])[:, site_idx]
    beta_season = np.asarray(posterior["beta_season"])  # (draws, n_categories, n_fourier)
    season_term = (beta_season[:, row_cat, :] * fourier[None, :, :]).sum(axis=-1)

    def expected_log_y(ai_values):
        return (
            alpha
            + beta_trend * trend[None, :]
            + beta_ai_cat * ai_values[None, :]
            + beta_trends_site * log_trends_c[None, :]
            + season_term
        )

    actual = expected_log_y(ai_signal)
    counterfactual = expected_log_y(np.full_like(ai_signal, baseline_ai_signal))
    influenced = np.exp(actual) - np.exp(counterfactual)  # (draws, n_rows)

    influenced_eval = influenced[:, eval_mask]
    portfolio = _summarize_draws(influenced_eval.sum(axis=1))
    actual_sessions = float(panel.loc[eval_mask, session_col].sum())
    portfolio["pct_of_actual"] = portfolio["mean"] / actual_sessions if actual_sessions else float("nan")
    portfolio["actual_sessions"] = actual_sessions

    site_codes_eval = site_idx[eval_mask]
    n_sites = model_data["n_sites"]
    per_site_rows = []
    for s in range(n_sites):
        mask_s = site_codes_eval == s
        if not mask_s.any():
            continue
        stats = _summarize_draws(influenced_eval[:, mask_s].sum(axis=1))
        per_site_rows.append({"site_code": s, **stats})
    per_site = pd.DataFrame(per_site_rows)

    return {
        "portfolio": portfolio,
        "per_site": per_site,
        "influenced_draws": influenced,      # (draws, n_rows), for plotting
        "eval_mask": eval_mask,
    }

def plot_actual_vs_counterfactual(panel, influence, outcome_label, session_col):
    weekly_actual = panel.groupby("week")[session_col].sum()
    influenced_draws = influence["influenced_draws"]  # (draws, n_rows)
    weekly_influenced_draws = (
        pd.DataFrame(influenced_draws.T, index=panel.index)
        .groupby(panel["week"])
        .sum()
        .reindex(weekly_actual.index, fill_value=0.0)
    )
    weekly_influenced = weekly_influenced_draws.mean(axis=1)
    weekly_influenced_low = weekly_influenced_draws.quantile(0.03, axis=1)
    weekly_influenced_high = weekly_influenced_draws.quantile(0.97, axis=1)
    weekly_counterfactual = weekly_actual - weekly_influenced
    weekly_counterfactual_low = weekly_actual - weekly_influenced_high
    weekly_counterfactual_high = weekly_actual - weekly_influenced_low

    week_dates = panel.groupby("week")["date"].min()
    out = pd.DataFrame({
        "week": weekly_actual.index,
        "week_start": week_dates.reindex(weekly_actual.index).to_numpy(),
        "actual_sessions": weekly_actual.to_numpy(),
        "counterfactual_sessions": weekly_counterfactual.to_numpy(),
        "counterfactual_sessions_low": weekly_counterfactual_low.to_numpy(),
        "counterfactual_sessions_high": weekly_counterfactual_high.to_numpy(),
        "ai_influenced_sessions": weekly_influenced.to_numpy(),
        "ai_influenced_sessions_low": weekly_influenced_low.to_numpy(),
        "ai_influenced_sessions_high": weekly_influenced_high.to_numpy(),
    })
    csv_path = os.path.join(PLOTS_DIR, f"actual_vs_counterfactual_{outcome_label}.csv")
    os.makedirs(PLOTS_DIR, exist_ok=True)
    out.to_csv(csv_path, index=False)
    print(f"  saved {csv_path} ({len(out)} weeks)")

    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot(weekly_actual.index, weekly_actual.to_numpy(), lw=1.4, label="actual")
    ax.plot(weekly_counterfactual.index, weekly_counterfactual.to_numpy(), lw=1.4, ls="--",
            label="counterfactual (AI held at pre-ramp baseline)")
    ax.set_xlabel("week")
    ax.set_ylabel(f"{outcome_label} sessions (Trends-covered sites)")
    ax.set_title(f"Actual vs counterfactual {outcome_label} sessions")
    ax.legend(fontsize=8)
    save_fig(fig, f"actual_vs_counterfactual_{outcome_label}.png")
    return out

def posterior_predictive_check(mcmc, model_data, panel, y_obs, site_names, outcome_label,
                                session_col, thin=8, seed=2, n_example_sites=3):
    """
    Simulates ``y`` from the fitted posterior (mirroring the notebook's final
    section) and checks the model against the data it was fit to:

    - **Coverage**: the fraction of observed log-sessions falling inside the
      posterior predictive's 94% band. Close to 0.94 is the target; well below
      means the model is overconfident, well above means it is vaguer than it
      needs to be.
    - **A few example sites**: actual sessions plotted against the simulated
      band, so the coverage number has a picture behind it.
    """
    posterior = mcmc.get_samples()
    thinned = {k: v[::thin] for k, v in posterior.items()}
    predicted = np.asarray(
        Predictive(outcome_model, posterior_samples=thinned, return_sites=["y"])(
            jax.random.PRNGKey(seed), **model_data)["y"]
    )
    observed = np.asarray(y_obs)
    lower, upper = np.percentile(predicted, [3, 97], axis=0)
    coverage = float(((observed >= lower) & (observed <= upper)).mean())
    print(f"  posterior predictive coverage ({outcome_label}): {coverage:.3f} of observations "
          f"fall inside the model's 94% range (target ~0.94)")

    idxs = np.linspace(0, len(site_names) - 1, min(n_example_sites, len(site_names))).astype(int)
    example_sites = [site_names[i] for i in idxs]
    fig, axes = plt.subplots(1, len(example_sites), figsize=(14, 3.4))
    for ax, site in zip(np.atleast_1d(axes), example_sites):
        m = (panel["site"] == site).to_numpy()
        weeks = panel.loc[m, "week"]
        lo, hi = np.percentile(predicted[:, m], [3, 97], axis=0)
        ax.fill_between(weeks, np.exp(lo), np.exp(hi), alpha=0.3, label="94% range")
        ax.plot(weeks, np.exp(predicted[:, m].mean(axis=0)), lw=1.2, label="model")
        ax.plot(weeks, panel.loc[m, session_col], "k.", ms=3, label="actual")
        ax.set_title(site, fontsize=10)
        ax.set_xlabel("week")
    axes[0].set_ylabel(f"{outcome_label} sessions")
    axes[0].legend(fontsize=8)
    fig.suptitle(f"Posterior predictive check: simulated vs actual {outcome_label} sessions",
                 y=1.04)
    save_fig(fig, f"posterior_predictive_{outcome_label}.png")
    return coverage

def plot_shrinkage(ols, sites, posterior_site_values, coefficient_col_ols, outcome_label,
                    coefficient_label, filename):
    """
    Scatter of the naive per-site OLS estimate (x) against the hierarchical
    posterior mean (y), coloured by category, with a y=x reference line.
    Points sitting on the line kept their own estimate; points pulled toward a
    shared value show the hierarchy borrowing strength across sites. For the
    AI-adoption coefficient, every site in a category collapses onto the same
    y-value by construction (there is no site-level term to pool from at all -
    see ``outcome_model``'s docstring), which is itself the pooling story worth
    seeing, not a bug in the plot.
    """
    df = (
        ols[ols["outcome"] == outcome_label][["site", coefficient_col_ols]]
        .merge(sites[["site", "category"]], on="site")
    )
    df["posterior_mean"] = df["site"].map(posterior_site_values)

    fig, ax = plt.subplots(figsize=(6, 6))
    cmap = plt.get_cmap("tab10")
    for i, cat in enumerate(sorted(df["category"].unique())):
        d = df[df["category"] == cat]
        ax.scatter(d[coefficient_col_ols], d["posterior_mean"], label=cat, s=28,
                   color=cmap(i), alpha=0.85, edgecolors="none")
    lo = float(min(df[coefficient_col_ols].min(), df["posterior_mean"].min()))
    hi = float(max(df[coefficient_col_ols].max(), df["posterior_mean"].max()))
    pad = 0.05 * (hi - lo or 1.0)
    ax.plot([lo - pad, hi + pad], [lo - pad, hi + pad], color="grey", ls="--", lw=1,
            label="no shrinkage (y = x)")
    ax.set_xlabel(f"per-site OLS estimate ({coefficient_label})")
    ax.set_ylabel(f"hierarchical posterior mean ({coefficient_label})")
    ax.set_title(f"Shrinkage: OLS vs hierarchical, {coefficient_label} on {outcome_label}")
    ax.legend(fontsize=8)
    save_fig(fig, filename)

def report_pooling(mcmc, ols, sites, outcome_label):
    """
    Explicitly answers "how hard does a site get pulled?" for the two
    regressors, since they are pooled differently by design:

    - **AI adoption** is only ever pooled at global -> category (see
      ``outcome_model``'s docstring for why a site level was dropped), so every
      site's answer is 100% its category's value, 0% its own data - always,
      regardless of how much history that site has.
    - **Brand Trends** keeps the full global -> category -> site hierarchy, so
      the weight a site's own data gets is a real, estimated quantity: ``w =
      tau_site^2 / (tau_site^2 + se_ols^2)`` (the notebook's formula), which
      grows toward 1 for sites with a long, precise OLS history and shrinks
      toward 0 for short or noisy ones.
    """
    posterior = mcmc.get_samples()
    tau_cat_ai = np.asarray(posterior["tau_cat_ai"])
    tau_cat_trends = np.asarray(posterior["tau_cat_trends"])
    tau_site_trends = np.asarray(posterior["tau_site_trends"])

    def fmt(values):
        return (f"{values.mean():.3f} (94% CI {np.percentile(values, 3):.3f} to "
                f"{np.percentile(values, 97):.3f})")

    print(f"\n  Pooling summary ({outcome_label}):")
    print(f"    tau_cat_ai      = {fmt(tau_cat_ai)}  <- AI effect, spread *between* categories")
    print(f"    tau_cat_trends  = {fmt(tau_cat_trends)}  <- Trends effect, spread *between* categories")
    print(f"    tau_site_trends = {fmt(tau_site_trends)}  <- Trends effect, spread *within* a "
          "category, site to site")
    print("    (no tau_site_ai: a single portfolio-wide AI series gives every site in a "
          "category the identical AI effect - full pooling by construction, not fitted)")

    tau_site_trends_est = float(tau_site_trends.mean())
    trends_ols = (
        ols[ols["outcome"] == outcome_label][["site", "se_trends_ols"]]
        .merge(sites[["site", "category"]], on="site")
    )
    trends_ols["weight_on_own_data"] = (
        tau_site_trends_est ** 2
        / (tau_site_trends_est ** 2 + trends_ols["se_trends_ols"] ** 2)
    )
    by_cat = trends_ols.groupby("category")["weight_on_own_data"].mean().sort_index()
    print("    average weight each site's own data gets for brand-Trends, by category "
          "(0 = fully pooled onto the category, 1 = own data trusted outright):")
    for cat, w in by_cat.items():
        print(f"      {cat:<14s} {w:.3f}")

    fig, ax = plt.subplots(figsize=(7, 3.6))
    x = np.arange(len(by_cat))
    ax.bar(x - 0.18, np.zeros(len(by_cat)), width=0.35, label="AI adoption (fully pooled, by design)")
    ax.bar(x + 0.18, by_cat.to_numpy(), width=0.35, label="brand Trends (partially pooled)")
    ax.set_xticks(x)
    ax.set_xticklabels(by_cat.index, rotation=15, ha="right")
    ax.set_ylim(0, 1)
    ax.set_ylabel("weight on own site's data")
    ax.set_title(f"How much pooling: AI adoption vs brand Trends ({outcome_label})")
    ax.legend(fontsize=8)
    save_fig(fig, f"pooling_weights_{outcome_label}.png")
    return by_cat

def model_data_with_ai_signal(model_data, panel, signal_col):
    """Copy model inputs while swapping only the row-aligned AI predictor."""
    result = dict(model_data)
    result["ai_signal"] = jnp.asarray(panel[signal_col].to_numpy())
    return result

def subset_model_rows(model_data, row_mask):
    """Drop whole panel rows while retaining site/category lookup arrays."""
    mask = np.asarray(row_mask, dtype=bool)
    row_keys = {"site_idx", "trend", "fourier", "ai_signal", "log_trends_c"}
    result = {}
    for key, value in model_data.items():
        result[key] = jnp.asarray(np.asarray(value)[mask]) if key in row_keys else value
    return result


def _arviz_summary(idata, *, var_names, interval_prob=0.94):
    """Call ``az.summary`` with whichever interval kwarg this ArviZ version accepts.

    Classic ``arviz.summary`` uses ``hdi_prob``. Newer ``arviz_stats.summary``
    (sometimes re-exported) uses ``ci_prob``. Passing the wrong one crashes the
    Cloud Run refit job after MCMC has already finished.
    """
    import inspect

    kwargs = {"var_names": var_names, "round_to": None}
    try:
        params = inspect.signature(az.summary).parameters
    except (TypeError, ValueError):
        params = {}
    if "hdi_prob" in params:
        kwargs["hdi_prob"] = interval_prob
    elif "ci_prob" in params:
        kwargs["ci_prob"] = interval_prob
    return az.summary(idata, **kwargs)

def summarize_ai_parameters(mcmc, categories, outcome_label, specification):
    """Return 94% intervals plus ESS/R-hat for mu_ai and beta_ai_cat."""
    posterior = mcmc.get_samples()
    idata = az.from_numpyro(
        mcmc,
        coords={"category": list(categories)},
        dims={"beta_ai_cat": ["category"]},
    )
    diagnostics = _arviz_summary(
        idata,
        var_names=["mu_ai", "beta_ai_cat"],
        interval_prob=0.94,
    )

    rows = []
    parameter_draws = [("mu_ai", "portfolio", np.asarray(posterior["mu_ai"]))]
    parameter_draws.extend(
        ("beta_ai_cat", str(category), np.asarray(posterior["beta_ai_cat"])[:, i])
        for i, category in enumerate(categories)
    )
    for parameter, category, draws in parameter_draws:
        diagnostic_name = (
            "mu_ai" if parameter == "mu_ai" else f"beta_ai_cat[{category}]"
        )
        diagnostic = diagnostics.loc[diagnostic_name]
        rows.append({
            "outcome": outcome_label,
            "specification": specification,
            "parameter": parameter,
            "category": category,
            **_summarize_draws(draws),
            "ess_bulk": float(diagnostic["ess_bulk"]),
            "ess_tail": float(diagnostic["ess_tail"]),
            "r_hat": float(diagnostic["r_hat"]),
        })
    return pd.DataFrame(rows)

def weekly_influence_summary(panel, influence, outcome_label, specification):
    """Summarise posterior AI-attributed sessions by calendar week."""
    draws = influence["influenced_draws"]
    weekly_draws = (
        pd.DataFrame(draws.T, index=panel.index)
        .groupby(panel["week"])
        .sum()
        .sort_index()
    )
    week_meta = (
        panel.groupby("week", as_index=True)
        .agg(week_start=("date", "min"), ai_share=("ai_share", "first"))
        .reindex(weekly_draws.index)
    )
    return pd.DataFrame({
        "outcome": outcome_label,
        "specification": specification,
        "week": weekly_draws.index,
        "week_start": week_meta["week_start"].to_numpy(),
        "ai_share": week_meta["ai_share"].to_numpy(),
        "ai_influenced_sessions_mean": weekly_draws.mean(axis=1).to_numpy(),
        "ai_influenced_sessions_low": weekly_draws.quantile(0.03, axis=1).to_numpy(),
        "ai_influenced_sessions_high": weekly_draws.quantile(0.97, axis=1).to_numpy(),
    })

def run_ai_sensitivity_analysis(
    primary_mcmc,
    primary_model_data,
    y,
    panel,
    categories,
    outcome_label,
    session_col,
    ramp_start,
    baseline_primary,
    baseline_capped,
):
    """
    Exact grouped-week refits that quantify late-week leverage.

    The leave-tail fits omit complete calendar weeks across every site, then use
    those posteriors to evaluate the full observed series. This is deliberately
    not row-level PSIS-LOO: the AI predictor is shared by all sites in a week, so
    one calendar week is the correct influence unit.
    """
    specs = [
        {
            "name": "primary_scaled_log",
            "signal_col": "ai_signal",
            "fit_mask": np.ones(len(panel), dtype=bool),
            "excluded": "",
            "reuse_primary": True,
        },
        {
            "name": "capped_pre_june_p03_p97",
            "signal_col": "ai_signal_capped",
            "fit_mask": np.ones(len(panel), dtype=bool),
            "excluded": "",
        },
        {
            "name": "positive_ai_weeks_only",
            "signal_col": "ai_signal",
            "fit_mask": (panel["ai_share"] > 0).to_numpy(),
            "excluded": "all zero-share weeks",
        },
        {
            "name": "leave_week_163_out",
            "signal_col": "ai_signal",
            "fit_mask": (panel["week"] != 163).to_numpy(),
            "excluded": "163",
        },
        {
            "name": "leave_weeks_160_163_out",
            "signal_col": "ai_signal",
            "fit_mask": (~panel["week"].between(160, 163)).to_numpy(),
            "excluded": "160-163",
        },
        {
            "name": "leave_weeks_157_163_out",
            "signal_col": "ai_signal",
            "fit_mask": (~panel["week"].between(157, 163)).to_numpy(),
            "excluded": "157-163",
        },
    ]
    if AI_SENSITIVITY_SPECS:
        specs = [spec for spec in specs if spec["name"] in AI_SENSITIVITY_SPECS]
        unknown = AI_SENSITIVITY_SPECS - {spec["name"] for spec in specs}
        if unknown:
            raise ValueError(f"Unknown AI sensitivity specifications: {sorted(unknown)}")
    if not specs or not any(spec.get("reuse_primary") for spec in specs):
        raise ValueError("AI sensitivity specifications must include primary_scaled_log")
    eval_mask = (panel["date"] >= pd.Timestamp(ramp_start)).to_numpy()
    parameter_frames, weekly_frames, summary_rows = [], [], []

    print(
        f"\nRunning {len(specs) - 1} exact AI-leverage sensitivity refits for "
        f"{outcome_label} ({SENSITIVITY_WARMUP} warmup + "
        f"{SENSITIVITY_SAMPLES} samples x 4 chains each)..."
    )
    for spec_index, spec in enumerate(specs):
        print(f"\n  sensitivity specification: {spec['name']}")
        full_data = model_data_with_ai_signal(
            primary_model_data, panel, spec["signal_col"]
        )
        fit_mask = spec["fit_mask"]
        if spec.get("reuse_primary"):
            mcmc = primary_mcmc
        else:
            fit_data = subset_model_rows(full_data, fit_mask)
            mcmc = fit_outcome_model(
                fit_data,
                jnp.asarray(np.asarray(y)[fit_mask]),
                num_warmup=SENSITIVITY_WARMUP,
                num_samples=SENSITIVITY_SAMPLES,
                seed=100 + spec_index,
            )

        baseline = (
            baseline_capped
            if spec["signal_col"] == "ai_signal_capped"
            else baseline_primary
        )
        influence = compute_ai_influence(
            mcmc, full_data, panel, baseline, eval_mask, session_col
        )
        weekly = weekly_influence_summary(
            panel, influence, outcome_label, spec["name"]
        )
        weekly_frames.append(weekly[weekly["week_start"] >= AI_TAIL_CAVEAT_START])
        parameter_frames.append(
            summarize_ai_parameters(mcmc, categories, outcome_label, spec["name"])
        )

        portfolio = influence["portfolio"]
        tail_draws = (
            pd.DataFrame(influence["influenced_draws"].T, index=panel.index)
            .loc[panel["date"] >= AI_TAIL_CAVEAT_START]
            .sum(axis=0)
            .to_numpy()
        )
        tail = _summarize_draws(tail_draws)
        last_week = weekly.iloc[-1]
        n_fit_weeks = int(panel.loc[fit_mask, "week"].nunique())
        divergences = int(
            np.asarray(mcmc.get_extra_fields()["diverging"]).sum()
        )
        summary_rows.append({
            "outcome": outcome_label,
            "specification": spec["name"],
            "signal_column": spec["signal_col"],
            "excluded_weeks": spec["excluded"],
            "n_fit_rows": int(fit_mask.sum()),
            "n_fit_weeks": n_fit_weeks,
            "divergences": divergences,
            "total_influenced_mean": portfolio["mean"],
            "total_influenced_low": portfolio["low"],
            "total_influenced_high": portfolio["high"],
            "june_july_influenced_mean": tail["mean"],
            "june_july_influenced_low": tail["low"],
            "june_july_influenced_high": tail["high"],
            "last_week": int(last_week["week"]),
            "last_week_start": last_week["week_start"],
            "last_week_influenced_mean": last_week["ai_influenced_sessions_mean"],
            "last_week_influenced_low": last_week["ai_influenced_sessions_low"],
            "last_week_influenced_high": last_week["ai_influenced_sessions_high"],
            "tail_caveat": (
                "June 2026 onward is high-leverage: a few shared-covariate "
                "weeks materially influence the fitted AI coefficient."
            ),
        })
        print(
            f"    total={portfolio['mean']:,.0f} "
            f"({portfolio['low']:,.0f}, {portfolio['high']:,.0f}); "
            f"last week={last_week['ai_influenced_sessions_mean']:,.0f}"
        )

        if not spec.get("reuse_primary"):
            del mcmc
        del influence, full_data
        gc.collect()

    summary = pd.DataFrame(summary_rows)
    parameters = pd.concat(parameter_frames, ignore_index=True)
    weekly_tail = pd.concat(weekly_frames, ignore_index=True)
    os.makedirs(PLOTS_DIR, exist_ok=True)
    summary_path = os.path.join(PLOTS_DIR, f"ai_sensitivity_summary_{outcome_label}.csv")
    parameters_path = os.path.join(
        PLOTS_DIR, f"ai_sensitivity_parameters_{outcome_label}.csv"
    )
    weekly_path = os.path.join(
        PLOTS_DIR, f"ai_sensitivity_weekly_june_july_{outcome_label}.csv"
    )
    summary.to_csv(summary_path, index=False)
    parameters.to_csv(parameters_path, index=False)
    weekly_tail.to_csv(weekly_path, index=False)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.2))
    y_pos = np.arange(len(summary))
    for ax, prefix, title in (
        (axes[0], "total_influenced", "Total post-ramp AI-influenced sessions"),
        (axes[1], "last_week_influenced", "Latest-week AI-influenced sessions"),
    ):
        mean = summary[f"{prefix}_mean"].to_numpy()
        low = summary[f"{prefix}_low"].to_numpy()
        high = summary[f"{prefix}_high"].to_numpy()
        ax.errorbar(
            mean,
            y_pos,
            xerr=np.vstack([mean - low, high - mean]),
            fmt="o",
            capsize=3,
        )
        ax.axvline(0, color="grey", lw=1, ls="--")
        ax.set_title(title)
        ax.set_xlabel("sessions (posterior mean and 94% interval)")
        ax.set_yticks(y_pos)
    axes[0].set_yticklabels(summary["specification"])
    axes[1].set_yticklabels([])
    fig.suptitle(
        f"AI tail-leverage sensitivity: {outcome_label}",
        y=1.02,
    )
    save_fig(fig, f"ai_sensitivity_effects_{outcome_label}.png")

    print(f"  saved {summary_path}")
    print(f"  saved {parameters_path}")
    print(f"  saved {weekly_path}")
    return summary, parameters, weekly_tail

def main():
    panel, sites, vertical_list, ai_index_raw = aggregate_site_data()
    ai_index, ai_transform = build_ai_adoption_index(ai_index_raw)
    ramp_start = detect_ai_ramp_start(ai_index)
    baseline_ai_signal = ai_baseline_value(ai_index, ramp_start, "ai_signal")
    baseline_ai_signal_capped = ai_baseline_value(
        ai_index, ramp_start, "ai_signal_capped"
    )
    print(f"\nAI ramp detected starting {pd.Timestamp(ramp_start).date()}; "
          f"baseline scaled-log ai_signal = {baseline_ai_signal:.3f}")

    os.makedirs(PLOTS_DIR, exist_ok=True)
    ai_index_path = os.path.join(PLOTS_DIR, "ai_index_by_week.csv")
    ai_index[[
        "week", "week_start", "ai_sessions_total", "all_sessions_total", "ai_share",
        "ai_log_scaled", "ai_signal", "ai_log_scaled_capped", "ai_signal_capped",
    ]].to_csv(ai_index_path, index=False)
    transform_path = os.path.join(PLOTS_DIR, "ai_transform_metadata.csv")
    pd.DataFrame([ai_transform]).to_csv(transform_path, index=False)
    print(f"  saved {ai_index_path}")
    print(f"  saved {transform_path}")

    trends = load_brand_trends(sites)
    if trends.empty:
        print(f"No Google Trends files found under '{GOOGLE_TRENDS_DIR}/' yet - "
              "add google_trends_property_*.csv files and re-run.")
        return

    panel, sites, site_names, model_data, outcomes = prepare_model_data(
        panel, sites, ai_index, trends, vertical_list)

    ols = single_regression(panel, sites, site_names)
    print("\nPer-site OLS baseline (no pooling):")
    print(ols.round(3))

    plot_ai_adoption_index(ai_index, ramp_start)
    plot_seo_direct(panel, sites, site_names=site_names[: min(3, len(site_names))])

    eval_mask = (panel["date"] >= pd.Timestamp(ramp_start)).to_numpy()
    session_cols = {"log_seo": "seo_sessions", "log_direct": "direct_sessions"}

    for outcome_name, y in outcomes.items():
        label = outcome_name.replace("log_", "")
        print(f"\nFitting hierarchical model for {label} sessions...")
        mcmc = fit_outcome_model(model_data, y)

        coords = {"site": site_names, "category": vertical_list,
                  "fourier": ["sin52", "cos52", "sin26", "cos26"]}
        dims = {
            "alpha": ["site"], "alpha_z": ["site"],
            "beta_trend": ["site"], "beta_trend_z": ["site"],
            "log_sigma": ["site"], "log_sigma_z": ["site"], "sigma": ["site"],
            "beta_trends_site": ["site"], "beta_trends_site_z": ["site"],
            "beta_ai_cat": ["category"], "beta_ai_cat_z": ["category"],
            "beta_trends_cat": ["category"], "beta_trends_cat_z": ["category"],
            "beta_season": ["category", "fourier"], "beta_season_z": ["category", "fourier"],
            "mu_season": ["fourier"],
        }
        idata = az.from_numpyro(mcmc, coords=coords, dims=dims)

        headline_vars = [
            "mu_ai", "tau_cat_ai", "mu_trends", "tau_cat_trends", "tau_site_trends",
        ]
        print(az.summary(idata, var_names=headline_vars, round_to=3))

        # Full parameter table: every site's alpha/beta_trend/sigma, every
        # category's beta_season, on top of the headline hyperparameters above.
        # This is long (n_sites x 3 + n_categories x n_fourier rows), so it goes
        # to a CSV rather than flooding the console; the console gets the
        # r_hat/ess health check across the whole thing instead.
        full_vars = headline_vars + [
            "alpha", "beta_trend", "sigma", "beta_season", "beta_ai_cat", "beta_trends_cat",
        ]
        full_summary = az.summary(idata, var_names=full_vars, round_to=3)
        worst_rhat = full_summary["r_hat"].max()
        worst_ess = full_summary["ess_bulk"].min()
        print(f"  full parameter table: {len(full_summary)} rows, "
              f"worst r_hat {worst_rhat:.3f}, worst ess_bulk {worst_ess:.0f} "
              f"(want r_hat<=1.01, ess_bulk in the hundreds+)")
        summary_path = f"az_summary_{label}.csv"
        full_summary.to_csv(summary_path)
        print(f"  saved {summary_path}")

        posterior_predictive_check(
            mcmc, model_data, panel, np.asarray(y), site_names, label, session_cols[outcome_name])

        posterior = mcmc.get_samples()
        cat_of_site_arr = np.asarray(model_data["cat_of_site"])
        beta_ai_cat_mean = np.asarray(posterior["beta_ai_cat"]).mean(axis=0)
        beta_trends_site_mean = np.asarray(posterior["beta_trends_site"]).mean(axis=0)
        ai_site_values = {
            site: float(beta_ai_cat_mean[cat_of_site_arr[i]]) for i, site in enumerate(site_names)
        }
        trends_site_values = {
            site: float(beta_trends_site_mean[i]) for i, site in enumerate(site_names)
        }
        plot_shrinkage(ols, sites, ai_site_values, "beta_ai_ols", label,
                       "AI adoption", f"shrinkage_ai_{label}.png")
        plot_shrinkage(ols, sites, trends_site_values, "beta_trends_ols", label,
                       "brand Trends", f"shrinkage_trends_{label}.png")

        report_pooling(mcmc, ols, sites, label)

        influence = compute_ai_influence(
            mcmc, model_data, panel, baseline_ai_signal, eval_mask, session_cols[outcome_name])
        p = influence["portfolio"]
        print(f"\n{label} sessions influenced by AI since {pd.Timestamp(ramp_start).date()}: "
              f"{p['mean']:.0f}  (94% credible interval {p['low']:.0f} to {p['high']:.0f}), "
              f"{p['pct_of_actual'] * 100:.1f}% of the {p['actual_sessions']:.0f} actual "
              f"{label} sessions in that window")

        plot_actual_vs_counterfactual(panel, influence, label, session_cols[outcome_name])

        print(
            f"\nCAUTION: results from {AI_TAIL_CAVEAT_START.date()} onward are "
            "high-leverage in-sample estimates. A few late weeks shared by all "
            "sites do much of the work in identifying beta_ai; use the grouped "
            "leave-tail sensitivity CSVs before presenting weekly tail figures."
        )
        if RUN_AI_SENSITIVITY:
            run_ai_sensitivity_analysis(
                primary_mcmc=mcmc,
                primary_model_data=model_data,
                y=y,
                panel=panel,
                categories=vertical_list,
                outcome_label=label,
                session_col=session_cols[outcome_name],
                ramp_start=ramp_start,
                baseline_primary=baseline_ai_signal,
                baseline_capped=baseline_ai_signal_capped,
            )
        else:
            print("  AI sensitivity refits skipped (RUN_AI_SENSITIVITY=0)")

if __name__ == "__main__":
    main()
