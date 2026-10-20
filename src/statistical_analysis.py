"""Statistical tests, correlation, attribution and Pareto helpers.

Test choice is pre-specified (not chosen after seeing p-values):
  * Two groups  -> Welch's t-test (primary; ERR is near-symmetric, n is large,
                   variances may differ) + Mann-Whitney U (robustness).
  * k groups    -> Welch's ANOVA (primary) + Kruskal-Wallis (robustness).
  * Proportions -> chi-square test of independence + Cramer's V.
  * Families of tests (e.g. one per clinical measure) are Holm-adjusted.

Each ERR comparison is run *within one measure*, so every hospital contributes
at most one observation per test (no within-hospital clustering inside a test).
"""
from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.oneway import anova_oneway
import statsmodels.formula.api as smf

RNG_SEED = 20261020


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------
def analysis_frame(measure_fact: pd.DataFrame, hospital_dim: pd.DataFrame,
                   min_discharges: float | None = None) -> pd.DataFrame:
    """ANALYZABLE measure rows joined to hospital attributes.

    min_discharges optionally restricts to rows with at least that many eligible
    discharges (sensitivity analysis: CMS shrinks small-hospital ERRs toward 1).
    """
    df = measure_fact[measure_fact["RECORD_STATUS"] == "ANALYZABLE"].merge(
        hospital_dim.drop(columns=["FACILITY_NAME", "STATE"]), on="FACILITY_ID", how="left")
    if min_discharges is not None:
        df = df[df["ELIGIBLE_DISCHARGES"] >= min_discharges]
    return df


def payment_contributions(measure_fact: pd.DataFrame, hospital_dim: pd.DataFrame) -> pd.DataFrame:
    """Attribute each hospital's official HRRP payment reduction to measures.

    CMS formula (FY 2026 IPPS final rule):
        reduction = min(3%, NM x sum_m [DRG ratio_m x max(0, ERR_m - peer median ERR_m)])
    where only measures with penalty indicator 'Y' contribute.
    Returns one row per hospital x measure with CONTRIB_PCT_POINTS (percentage
    points of payment reduction). This is NOT a dollar amount.
    """
    m = measure_fact.merge(hospital_dim[["FACILITY_ID", "STATE", "NEUTRALITY_MODIFIER",
                                         "PAYMENT_REDUCTION_PCT"]], on=["FACILITY_ID", "STATE"], how="inner")
    m = m[m["PAYMENT_REDUCTION_PCT"].notna()].copy()
    raw = np.where(m["PENALTY_INDICATOR"].eq("Y"),
                   m["DRG_PAYMENT_RATIO"] * (m["EXCESS_READMISSION_RATIO"] - m["PEER_GROUP_MEDIAN_ERR"])
                   * m["NEUTRALITY_MODIFIER"] * 100, 0.0)
    m["CONTRIB_PCT_POINTS"] = np.nan_to_num(raw, nan=0.0)
    # Apply the 3% cap proportionally so measure contributions sum to the official value.
    tot = m.groupby("FACILITY_ID")["CONTRIB_PCT_POINTS"].transform("sum")
    scale = np.where(tot > 3.0, 3.0 / tot, 1.0)
    m["CONTRIB_PCT_POINTS"] = m["CONTRIB_PCT_POINTS"] * scale
    return m[["FACILITY_ID", "STATE", "MEASURE_CODE", "PENALTY_INDICATOR", "CONTRIB_PCT_POINTS",
              "PAYMENT_REDUCTION_PCT"]]


def reconstruction_check(contrib: pd.DataFrame) -> pd.Series:
    """Absolute difference between reconstructed and official reduction per hospital."""
    h = contrib.groupby("FACILITY_ID").agg(recon=("CONTRIB_PCT_POINTS", "sum"),
                                           official=("PAYMENT_REDUCTION_PCT", "first"))
    return (h["recon"] - h["official"]).abs()


# ---------------------------------------------------------------------------
# Effect sizes and intervals
# ---------------------------------------------------------------------------
def welch_ci(a: np.ndarray, b: np.ndarray, alpha: float = 0.05) -> tuple[float, float, float]:
    """Mean difference (a - b) with Welch-Satterthwaite confidence interval."""
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    se = np.sqrt(va + vb)
    dof = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    t = stats.t.ppf(1 - alpha / 2, dof)
    d = a.mean() - b.mean()
    return d, d - t * se, d + t * se


def hedges_g(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = len(a), len(b)
    sp = np.sqrt(((na - 1) * a.var(ddof=1) + (nb - 1) * b.var(ddof=1)) / (na + nb - 2))
    j = 1 - 3 / (4 * (na + nb) - 9)
    return j * (a.mean() - b.mean()) / sp


def rank_biserial(u: float, na: int, nb: int) -> float:
    """Rank-biserial correlation from Mann-Whitney U (U of group a). Range -1..1."""
    return 2 * u / (na * nb) - 1


def epsilon_squared(h: float, n: int, k: int) -> float:
    """Kruskal-Wallis effect size; ~0.01 small, ~0.08 medium, ~0.26 large."""
    return max(0.0, (h - k + 1) / (n - k))


def cramers_v(table: pd.DataFrame) -> float:
    chi2 = stats.chi2_contingency(table, correction=False)[0]
    n = table.to_numpy().sum()
    return float(np.sqrt(chi2 / (n * (min(table.shape) - 1))))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def compare_two_groups(df: pd.DataFrame, value: str, group: str, a: str, b: str) -> dict:
    x = df.loc[df[group] == a, value].dropna().to_numpy()
    y = df.loc[df[group] == b, value].dropna().to_numpy()
    t = stats.ttest_ind(x, y, equal_var=False)
    u = stats.mannwhitneyu(x, y, alternative="two-sided")
    diff, lo, hi = welch_ci(x, y)
    lev = stats.levene(x, y, center="median")
    return {"group_a": a, "group_b": b, "n_a": len(x), "n_b": len(y),
            "mean_a": x.mean(), "mean_b": y.mean(), "median_a": np.median(x), "median_b": np.median(y),
            "mean_diff": diff, "ci95_low": lo, "ci95_high": hi,
            "welch_t": t.statistic, "welch_p": t.pvalue, "hedges_g": hedges_g(x, y),
            "mwu_U": u.statistic, "mwu_p": u.pvalue, "rank_biserial": rank_biserial(u.statistic, len(x), len(y)),
            "levene_p": lev.pvalue, "skew_a": stats.skew(x), "skew_b": stats.skew(y)}


def compare_k_groups(df: pd.DataFrame, value: str, group: str) -> dict:
    groups = {g: s[value].dropna().to_numpy() for g, s in df.groupby(group) if s[value].notna().any()}
    arrays = list(groups.values())
    n, k = sum(map(len, arrays)), len(arrays)
    welch = anova_oneway(arrays, use_var="unequal")
    kw = stats.kruskal(*arrays)
    return {"k": k, "n": n, "group_n": {g: len(v) for g, v in groups.items()},
            "group_mean": {g: float(v.mean()) for g, v in groups.items()},
            "welch_F": float(welch.statistic), "welch_p": float(welch.pvalue),
            "kruskal_H": kw.statistic, "kruskal_p": kw.pvalue,
            "epsilon_sq": epsilon_squared(kw.statistic, n, k),
            "levene_p": stats.levene(*arrays, center="median").pvalue}


def pairwise_posthoc(df: pd.DataFrame, value: str, group: str) -> pd.DataFrame:
    """All pairwise Welch t-tests, Holm-adjusted within the family."""
    levels = sorted(df[group].dropna().unique())
    rows = [compare_two_groups(df, value, group, a, b) for a, b in combinations(levels, 2)]
    out = pd.DataFrame(rows)
    out["welch_p_holm"] = multipletests(out["welch_p"], method="holm")[1]
    return out


def by_measure(df: pd.DataFrame, fn, **kwargs) -> pd.DataFrame:
    """Run fn on each clinical measure separately and Holm-adjust p-values across measures."""
    rows = []
    for code, sub in df.groupby("MEASURE_CODE"):
        r = fn(sub, **kwargs)
        rows.append({"MEASURE_CODE": code, **r})
    out = pd.DataFrame(rows)
    for col in [c for c in out.columns if c.endswith("_p")]:
        out[f"{col}_holm"] = multipletests(out[col], method="holm")[1]
    return out


def chi_square(df: pd.DataFrame, row: str, col: str) -> dict:
    table = pd.crosstab(df[row], df[col])
    chi2, p, dof, expected = stats.chi2_contingency(table, correction=False)
    return {"table": table, "chi2": chi2, "p": p, "dof": dof, "cramers_v": cramers_v(table),
            "min_expected": float(expected.min()), "n": int(table.to_numpy().sum())}


def proportion_ci(successes: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval for a proportion."""
    ci = stats.binomtest(successes, n).proportion_ci(confidence_level=1 - alpha, method="wilson")
    return ci.low, ci.high


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------
def spearman_matrix(df: pd.DataFrame, cols: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Pairwise-complete Spearman rho, p-values and n for each pair."""
    rho = pd.DataFrame(np.nan, index=cols, columns=cols)
    p = rho.copy(); n = rho.copy()
    for i, a in enumerate(cols):
        for b in cols[i:]:
            sub = df[[a, b]].dropna()
            if a == b:
                r, pv = 1.0, 0.0
            else:
                r, pv = stats.spearmanr(sub[a], sub[b])
            rho.loc[a, b] = rho.loc[b, a] = r
            p.loc[a, b] = p.loc[b, a] = pv
            n.loc[a, b] = n.loc[b, a] = len(sub)
    return rho, p, n.astype(int)


# ---------------------------------------------------------------------------
# Multivariable model (adjusts for observed confounders)
# ---------------------------------------------------------------------------
def adjusted_model(df: pd.DataFrame, outcome: str, numeric: list[str], categorical: dict[str, str],
                   cluster: str = "STATE"):
    """OLS with standard errors clustered by state (hospitals in a state share
    market and policy context, so their residuals are not independent).

    categorical maps column -> reference level, e.g. {"TEACHING_STATUS": "Non-teaching"}.
    Complete-case analysis: rows missing any model variable are dropped.
    """
    terms = numeric + [f'C({c}, Treatment(reference="{ref}"))' for c, ref in categorical.items()]
    data = df[[outcome, cluster, *numeric, *categorical]].dropna()
    groups = pd.factorize(data[cluster])[0]
    return smf.ols(f"{outcome} ~ {' + '.join(terms)}", data=data).fit(
        cov_type="cluster", cov_kwds={"groups": groups})


# ---------------------------------------------------------------------------
# Pareto
# ---------------------------------------------------------------------------
def pareto_table(df: pd.DataFrame, category: str, metric: str, threshold: float = 80.0) -> pd.DataFrame:
    """Aggregate metric by category, sort descending, add share and cumulative share.

    VITAL_FEW marks the smallest set of top categories whose cumulative share
    first reaches the threshold.
    """
    t = (df.groupby(category, dropna=False)[metric].sum().sort_values(ascending=False)
           .rename("VALUE").reset_index())
    total = t["VALUE"].sum()
    t["SHARE_PCT"] = 100 * t["VALUE"] / total
    t["CUMULATIVE_PCT"] = t["SHARE_PCT"].cumsum()
    prev = t["CUMULATIVE_PCT"].shift(fill_value=0)
    t["VITAL_FEW"] = prev < threshold
    return t
