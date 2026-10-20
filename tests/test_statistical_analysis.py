"""Tests for src/statistical_analysis.py (hand-built fixtures + checks on real output)."""
import numpy as np
import pandas as pd
import pytest
from scipy import stats

from src import statistical_analysis as sa


def test_welch_ci_matches_scipy():
    rng = np.random.default_rng(0)
    a, b = rng.normal(1.0, 0.05, 300), rng.normal(1.01, 0.08, 200)
    d, lo, hi = sa.welch_ci(a, b)
    ci = stats.ttest_ind(a, b, equal_var=False).confidence_interval()
    assert d == pytest.approx(a.mean() - b.mean())
    assert (lo, hi) == pytest.approx((ci.low, ci.high))


def test_effect_sizes():
    assert sa.rank_biserial(u=50, na=10, nb=10) == pytest.approx(0.0)   # U = na*nb/2 -> no effect
    assert sa.rank_biserial(u=100, na=10, nb=10) == pytest.approx(1.0)
    assert sa.epsilon_squared(h=1.0, n=100, k=3) == 0.0                  # floored at zero
    t = pd.DataFrame([[50, 0], [0, 50]])
    assert sa.cramers_v(t) == pytest.approx(1.0)


def test_compare_two_groups_detects_known_shift():
    df = pd.DataFrame({"g": ["a"] * 200 + ["b"] * 200,
                       "v": np.r_[np.full(200, 1.0) + np.linspace(-.01, .01, 200),
                                  np.full(200, 1.1) + np.linspace(-.01, .01, 200)]})
    r = sa.compare_two_groups(df, "v", "g", "a", "b")
    assert r["mean_diff"] == pytest.approx(-0.1)
    assert r["welch_p"] < 1e-10 and r["ci95_high"] < 0


def test_by_measure_applies_holm():
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"MEASURE_CODE": np.repeat(["X", "Y", "Z"], 100),
                       "g": np.tile(["a", "b"], 150), "v": rng.normal(size=300)})
    out = sa.by_measure(df, sa.compare_two_groups, value="v", group="g", a="a", b="b")
    assert (out["welch_p_holm"] >= out["welch_p"]).all()


def test_pareto_table():
    df = pd.DataFrame({"cat": list("ABCDE"), "v": [50, 30, 10, 6, 4]})
    t = sa.pareto_table(df, "cat", "v")
    assert t["cat"].tolist() == list("ABCDE")
    assert t["CUMULATIVE_PCT"].iloc[-1] == pytest.approx(100)
    assert t["VITAL_FEW"].tolist() == [True, True, False, False, False]  # 50+30 reaches 80%


def test_payment_contribution_formula_and_cap():
    mf = pd.DataFrame({"FACILITY_ID": ["010001"] * 2 + ["010002"] * 2, "STATE": ["AL"] * 4,
                       "MEASURE_CODE": ["HF", "PN"] * 2, "PENALTY_INDICATOR": ["Y", "N", "Y", "Y"],
                       "DRG_PAYMENT_RATIO": [0.04, 0.03, 0.5, 0.5],
                       "EXCESS_READMISSION_RATIO": [1.05, 0.98, 1.1, 1.1],
                       "PEER_GROUP_MEDIAN_ERR": [1.0, 1.0, 1.0, 1.0]})
    dim = pd.DataFrame({"FACILITY_ID": ["010001", "010002"], "STATE": ["AL", "AL"],
                        "NEUTRALITY_MODIFIER": [1.0, 1.0], "PAYMENT_REDUCTION_PCT": [0.2, 3.0]})
    c = sa.payment_contributions(mf, dim).set_index(["FACILITY_ID", "MEASURE_CODE"])["CONTRIB_PCT_POINTS"]
    assert c[("010001", "HF")] == pytest.approx(0.2)      # 0.04 * 0.05 * 100
    assert c[("010001", "PN")] == 0                       # penalty N contributes nothing
    assert c.loc["010002"].sum() == pytest.approx(3.0)    # 10 points capped at 3%


def test_reconstructed_reduction_matches_official(processed_dir):
    mf = pd.read_csv(processed_dir / "measure_fact.csv", dtype={"FACILITY_ID": str})
    dim = pd.read_csv(processed_dir / "hospital_dim.csv", dtype={"FACILITY_ID": str})
    diff = sa.reconstruction_check(sa.payment_contributions(mf, dim))
    assert len(diff) > 2900
    assert diff.max() < 0.01   # official values are rounded to 0.01 percentage points
