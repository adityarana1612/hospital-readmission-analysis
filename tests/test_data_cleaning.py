"""Unit tests for src/data_cleaning.py.

The small DataFrames below are hand-built test fixtures that exercise edge cases;
they are not CMS data.
"""
import numpy as np
import pandas as pd
import pytest

from src import data_cleaning as dc


def _hrrp_raw(rows):
    cols = ["Facility Name", "Facility ID", "State", "Measure Name", "Number of Discharges", "Footnote",
            "Excess Readmission Ratio", "Predicted Readmission Rate", "Expected Readmission Rate",
            "Number of Readmissions", "Start Date", "End Date"]
    return pd.DataFrame(rows, columns=cols, dtype="string")


GOOD = ["H1", "010001", "AL", "READM-30-HF-HRRP", "500", None, "1.0500", "21.0", "20.0", "105",
        "07/01/2021", "06/30/2024"]


# ---------------------------------------------------------------- helpers
def test_standardize_column_name():
    assert dc.standardize_column_name(" Excess Readmission Ratio ") == "EXCESS_READMISSION_RATIO"
    assert dc.standardize_column_name("City/Town") == "CITY_TOWN"


def test_to_numeric_handles_cms_tokens():
    s = pd.Series(["1.5", ".", "N/A", "Not Available", "0.12%", None, "abc"])
    out = dc.to_numeric(s)
    assert out.iloc[0] == 1.5 and out.iloc[4] == 0.12
    assert out.iloc[[1, 2, 3, 5, 6]].isna().all()
    assert out.dtype == float


@pytest.mark.parametrize("ccn,valid", [("010001", True), ("05T001", True), ("01014F", True),
                                       ("10001", False), ("0100011", False), ("", False), (None, False),
                                       ("ABCDEF", False)])
def test_ccn_validation(ccn, valid):
    s = dc.normalize_ccn(pd.Series([ccn], dtype="string"))
    assert bool(dc.is_valid_ccn(s).iloc[0]) is valid


def test_normalize_ccn_does_not_pad_lost_leading_zero():
    # "10001" might be "010001" with the zero lost by Excel, but we must not guess.
    assert dc.normalize_ccn(pd.Series(["10001"])).iloc[0] == "10001"


# ---------------------------------------------------------------- HRRP cleaning
def test_clean_hrrp_types_and_status():
    suppressed = GOOD.copy(); suppressed[3] = "READM-30-AMI-HRRP"; suppressed[6:10] = [None, None, None, "Too Few to Report"]
    df, review = dc.clean_hrrp(_hrrp_raw([GOOD, suppressed]), dc.CleaningLog())
    assert review.empty
    assert df["EXCESS_READMISSION_RATIO"].dtype == float
    assert pd.api.types.is_datetime64_any_dtype(df["START_DATE"])
    by = df.set_index("MEASURE_CODE")
    assert by.loc["HF", "RECORD_STATUS"] == "ANALYZABLE"
    assert by.loc["AMI", "RECORD_STATUS"] == "EXCLUDED_NO_ERR"
    assert bool(by.loc["AMI", "READMISSIONS_SUPPRESSED"]) is True
    assert np.isnan(by.loc["AMI", "NUMBER_OF_READMISSIONS"])  # suppressed is missing, not zero


def test_missing_ids_go_to_review_not_invented():
    missing = GOOD.copy(); missing[1] = None
    bad = GOOD.copy(); bad[1] = "10001"
    df, review = dc.clean_hrrp(_hrrp_raw([GOOD, missing, bad]), dc.CleaningLog())
    assert len(df) == 1
    assert sorted(review["REVIEW_REASON"]) == ["INVALID_ID_FORMAT", "MISSING_ID"]


def test_duplicate_provider_measure_period_detected():
    df, review = dc.clean_hrrp(_hrrp_raw([GOOD, GOOD]), dc.CleaningLog())
    assert df.empty
    assert (review["REVIEW_REASON"] == "DUPLICATE_KEY").sum() == 2


def test_out_of_range_values_flagged_for_review():
    bad = GOOD.copy(); bad[6] = "5.0"          # ERR far outside plausible range
    over = GOOD.copy(); over[3] = "READM-30-PN-HRRP"; over[9] = "900"  # readmissions > discharges
    df, _ = dc.clean_hrrp(_hrrp_raw([bad, over]), dc.CleaningLog())
    assert (df["RECORD_STATUS"] == "REVIEW").all()


def test_find_duplicates_and_range_violations():
    df = pd.DataFrame({"K": [1, 1, 2], "DUAL_PROPORTION": [0.5, 1.2, np.nan]})
    assert len(dc.find_duplicates(df, ["K"])) == 2
    v = dc.range_violations(df)
    assert v["column"].tolist() == ["DUAL_PROPORTION"] and v["value"].tolist() == [1.2]


# ---------------------------------------------------------------- imputation
def test_imputation_flags_and_audit_columns():
    df = pd.DataFrame({"STATE": ["AL", "AK", "AK"], "MEASURE_CODE": ["HF", "HF", "AMI"],
                       "PERIOD": ["P1", "P1", "P1"], "RATE": [20.0, np.nan, np.nan]})
    ref = pd.DataFrame({"MEASURE_CODE": ["HF"], "PERIOD": ["P1"], "NAT": [21.3]})
    out = dc.impute_from_reference(df, "RATE", ref, "NAT", ["MEASURE_CODE", "PERIOD"], "national mean, same measure+period")
    assert out["IMPUTED_FLAG"].tolist() == [0, 1, 0]
    assert out.loc[1, "RATE"] == 21.3 and np.isnan(out.loc[1, "RATE_ORIGINAL"])
    assert out.loc[1, "IMPUTATION_SOURCE_VALUE"] == 21.3
    assert out.loc[0, "RATE"] == 20.0 and pd.isna(out.loc[0, "IMPUTATION_METHOD"])
    assert np.isnan(out.loc[2, "RATE"])  # no comparable reference -> stays missing


def test_imputation_refuses_mismatched_period():
    df = pd.DataFrame({"MEASURE_CODE": ["HF"], "PERIOD": ["2021-2024"], "RATE": [np.nan]})
    ref = pd.DataFrame({"MEASURE_CODE": ["HF"], "PERIOD": ["2023-2025"], "NAT": [21.3]})
    out = dc.impute_from_reference(df, "RATE", ref, "NAT", ["MEASURE_CODE", "PERIOD"], "m")
    assert out["IMPUTED_FLAG"].sum() == 0 and out["RATE"].isna().all()


def test_imputation_rejects_ambiguous_reference():
    df = pd.DataFrame({"M": ["HF"], "RATE": [np.nan]})
    ref = pd.DataFrame({"M": ["HF", "HF"], "NAT": [1.0, 2.0]})
    with pytest.raises(ValueError):
        dc.impute_from_reference(df, "RATE", ref, "NAT", ["M"], "m")


# ---------------------------------------------------------------- joins
def test_join_report_counts():
    a = pd.DataFrame({"K": ["1", "2", "3"]}); b = pd.DataFrame({"K": ["2", "3", "4", "5"]})
    r = dc.join_report(a, b, "K", "a", "b")
    assert (r["matched"], r["left_only"], r["right_only"]) == (2, 1, 2)
