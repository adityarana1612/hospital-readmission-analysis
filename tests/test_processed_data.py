"""Schema and integrity checks on the real pipeline output (skipped if not built)."""
import pandas as pd

from src import data_cleaning as dc

EXPECTED_MEASURE_FACT_COLS = {
    "FACILITY_ID", "STATE", "MEASURE_CODE", "EXCESS_READMISSION_RATIO", "PREDICTED_READMISSION_RATE",
    "EXPECTED_READMISSION_RATE", "RECORD_STATUS", "PENALTY_INDICATOR", "ELIGIBLE_DISCHARGES",
    "PEER_GROUP_MEDIAN_ERR", "EST_EXCESS_READMISSIONS", "START_DATE", "END_DATE"}


def _load(processed_dir, name):
    return pd.read_csv(processed_dir / f"{name}.csv", dtype={"FACILITY_ID": str})


def test_measure_fact_schema(processed_dir):
    mf = _load(processed_dir, "measure_fact")
    assert EXPECTED_MEASURE_FACT_COLS <= set(mf.columns)
    assert mf["FACILITY_ID"].str.len().eq(6).all()          # leading zeros preserved
    assert set(mf["MEASURE_CODE"]) == set(dc.HRRP_MEASURE_MAP.values())
    assert set(mf["RECORD_STATUS"]) <= {"ANALYZABLE", "EXCLUDED_NO_ERR", "REVIEW"}


def test_no_duplicate_keys(processed_dir):
    mf = _load(processed_dir, "measure_fact")
    assert not mf.duplicated(["FACILITY_ID", "MEASURE_CODE", "START_DATE", "END_DATE"]).any()
    dim = _load(processed_dir, "hospital_dim")
    assert dim["FACILITY_ID"].is_unique


def test_numeric_ranges(processed_dir):
    mf = _load(processed_dir, "measure_fact")
    dim = _load(processed_dir, "hospital_dim")
    assert dc.range_violations(mf).empty
    assert dc.range_violations(dim).empty


def test_analyzable_rows_have_err_and_excluded_do_not(processed_dir):
    mf = _load(processed_dir, "measure_fact")
    assert mf.loc[mf.RECORD_STATUS == "ANALYZABLE", "EXCESS_READMISSION_RATIO"].notna().all()
    assert mf.loc[mf.RECORD_STATUS == "EXCLUDED_NO_ERR", "EXCESS_READMISSION_RATIO"].isna().all()


def test_penalty_indicator_matches_cms_definition(processed_dir):
    """CMS: Y iff eligible discharges >= 25 and ERR > peer-group median ERR."""
    mf = _load(processed_dir, "measure_fact")
    m = mf[mf.PENALTY_INDICATOR.isin(["Y", "N"]) & mf.EXCESS_READMISSION_RATIO.notna()
           & mf.PEER_GROUP_MEDIAN_ERR.notna()]
    rule = (m.ELIGIBLE_DISCHARGES >= 25) & (m.EXCESS_READMISSION_RATIO > m.PEER_GROUP_MEDIAN_ERR)
    agreement = (rule == (m.PENALTY_INDICATOR == "Y")).mean()
    assert agreement > 0.99, f"penalty rule agreement only {agreement:.4f}"


def test_payment_factor_and_reduction_pct_consistent(processed_dir):
    dim = _load(processed_dir, "hospital_dim")
    d = dim[dim.IN_SUPPLEMENTAL]
    assert d["FACTOR_PCT_CONSISTENT"].all()


def test_err_reconciles_between_cms_files(processed_dir):
    rec = _load(processed_dir, "err_reconciliation")["ERR_MATCH"].dropna()
    assert len(rec) > 10_000 and rec.astype(bool).all()


def test_summary_metrics_reproducible(processed_dir):
    """Re-computing summary metrics from the same input gives identical results."""
    mf = _load(processed_dir, "measure_fact")
    a = mf[mf.RECORD_STATUS == "ANALYZABLE"].groupby("MEASURE_CODE")["EXCESS_READMISSION_RATIO"].agg(["count", "mean", "median"])
    b = _load(processed_dir, "measure_fact").query("RECORD_STATUS == 'ANALYZABLE'") \
        .groupby("MEASURE_CODE")["EXCESS_READMISSION_RATIO"].agg(["count", "mean", "median"])
    pd.testing.assert_frame_equal(a, b)
    assert int(a["count"].sum()) == (mf.RECORD_STATUS == "ANALYZABLE").sum()
