"""Cleaning, validation and reshaping of the CMS HRRP source files.

Design rules
------------
* Never invent identifiers or values. Rows with missing/invalid CCNs go to a
  review table; suppressed values stay missing and are flagged.
* Every stage records its row count in a CleaningLog.
* Column names are standardised to UPPER_SNAKE_CASE so they map 1:1 to the
  Oracle tables in sql/create_tables.sql.

Run as a script to build data/processed/ from data/raw/:

    python -m src.data_cleaning
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from src import data_ingestion as ing

PROCESSED_DIR = ing.PROJECT_ROOT / "data" / "processed"

# CCN: 2-digit state code + 4 characters. Short-term acute hospitals are all
# digits; the 3rd position can be a letter for some provider categories.
# Federal (VA / DoD) facilities in Hospital General Information use 5 digits + "F".
CCN_PATTERN = re.compile(r"^(\d{2}[0-9A-Z]\d{3}|\d{5}F)$")

# Canonical measure codes used everywhere in the project.
HRRP_MEASURE_MAP = {
    "READM-30-AMI-HRRP": "AMI",
    "READM-30-CABG-HRRP": "CABG",
    "READM-30-COPD-HRRP": "COPD",
    "READM-30-HF-HRRP": "HF",
    "READM-30-HIP-KNEE-HRRP": "HIP_KNEE",
    "READM-30-PN-HRRP": "PN",
}
SUPPLEMENTAL_MEASURE_MAP = {"AMI": "AMI", "COPD": "COPD", "HF": "HF",
                            "pneumonia": "PN", "CABG": "CABG", "THA/TKA": "HIP_KNEE"}
MEASURE_LABELS = {"AMI": "Heart attack (AMI)", "CABG": "Coronary artery bypass graft (CABG)",
                  "COPD": "Chronic obstructive pulmonary disease (COPD)", "HF": "Heart failure (HF)",
                  "HIP_KNEE": "Elective hip/knee replacement (THA/TKA)", "PN": "Pneumonia"}
# Census divisions as coded in the IPPS Impact File variable description.
REGION_MAP = {"1": "New England", "2": "Middle Atlantic", "3": "East North Central",
              "4": "West North Central", "5": "South Atlantic", "6": "East South Central",
              "7": "West South Central", "8": "Mountain", "9": "Pacific", "40": "Puerto Rico"}
URGEO_MAP = {"LURBAN": "Large urban", "OURBAN": "Other urban", "RURAL": "Rural"}
OWNERSHIP_CONTROL_MAP = {"G": "Government", "P": "Proprietary", "V": "Voluntary", "X": "Unknown"}

# Plausible ranges for validation (domain limits, not statistical cut-offs).
VALID_RANGES = {
    "EXCESS_READMISSION_RATIO": (0.0, 3.0),
    "PREDICTED_READMISSION_RATE": (0.0, 100.0),
    "EXPECTED_READMISSION_RATE": (0.0, 100.0),
    "PAYMENT_ADJUSTMENT_FACTOR": (0.97, 1.0),
    "PAYMENT_REDUCTION_PCT": (0.0, 3.0),
    "DUAL_PROPORTION": (0.0, 1.0),
}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
@dataclass
class CleaningLog:
    """Ordered record of (dataset, stage, rows, note) for every transformation."""
    entries: list[dict] = field(default_factory=list)

    def record(self, dataset: str, stage: str, df: pd.DataFrame, note: str = "") -> None:
        self.entries.append({"dataset": dataset, "stage": stage, "rows": len(df), "note": note})
        log.info("%-22s %-38s rows=%6d %s", dataset, stage, len(df), note)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.entries)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def standardize_column_name(name: str) -> str:
    """'Excess Readmission Ratio ' -> 'EXCESS_READMISSION_RATIO'."""
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip())
    return name.strip("_").upper()


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [standardize_column_name(c) for c in out.columns]
    return out


def to_numeric(series: pd.Series, missing_tokens: tuple[str, ...] = (".", "N/A", "Not Available",
                                                                    "Not Applicable")) -> pd.Series:
    """Convert to float. Known CMS missing tokens become NaN; anything else that
    fails to parse also becomes NaN (callers flag such rows separately)."""
    s = series.astype("string").str.strip().str.rstrip("%")
    s = s.mask(s.isin(missing_tokens))
    return pd.to_numeric(s, errors="coerce").astype(float)


def normalize_ccn(series: pd.Series) -> pd.Series:
    """Trim whitespace and upper-case. Does NOT pad or alter digits: a CCN that
    lost its leading zero is reported invalid rather than guessed."""
    return series.astype("string").str.strip().str.upper().replace("", pd.NA)


def is_valid_ccn(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.match(CCN_PATTERN)


def find_duplicates(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Return every row whose key combination occurs more than once."""
    return df[df.duplicated(keys, keep=False)].sort_values(keys)


def range_violations(df: pd.DataFrame, ranges: dict[str, tuple[float, float]] = VALID_RANGES) -> pd.DataFrame:
    """Rows where a non-null numeric value falls outside its documented range."""
    problems = []
    for col, (lo, hi) in ranges.items():
        if col in df.columns:
            bad = df[df[col].notna() & ~df[col].between(lo, hi)]
            problems.extend({"row": i, "column": col, "value": v} for i, v in bad[col].items())
    return pd.DataFrame(problems, columns=["row", "column", "value"])


def split_invalid_ids(df: pd.DataFrame, id_col: str, dataset: str,
                      clog: CleaningLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate rows whose identifier is missing or malformed into a review table."""
    df = df.copy()
    df[id_col] = normalize_ccn(df[id_col])
    valid = is_valid_ccn(df[id_col])
    review = df[~valid].assign(REVIEW_REASON=np.where(df.loc[~valid, id_col].isna(),
                                                      "MISSING_ID", "INVALID_ID_FORMAT"))
    clog.record(dataset, "invalid/missing ID -> review", review)
    return df[valid].copy(), review


# ---------------------------------------------------------------------------
# Dataset-specific cleaning
# ---------------------------------------------------------------------------
def clean_hrrp(raw: pd.DataFrame, clog: CleaningLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Provider-level HRRP file -> one typed row per hospital x measure.

    Adds RECORD_STATUS:
      ANALYZABLE      ERR present and all values in range
      EXCLUDED_NO_ERR CMS did not publish an ERR (footnoted) - not imputed
      REVIEW          values present but failing a validation rule
    """
    ds = "hrrp_hospital"
    clog.record(ds, "raw load", raw)
    df = standardize_columns(raw)
    df, review = split_invalid_ids(df, "FACILITY_ID", ds, clog)

    df["MEASURE_CODE"] = df["MEASURE_NAME"].map(HRRP_MEASURE_MAP)
    unknown = df["MEASURE_CODE"].isna()
    if unknown.any():
        review = pd.concat([review, df[unknown].assign(REVIEW_REASON="UNKNOWN_MEASURE")])
        df = df[~unknown]
    clog.record(ds, "known measures only", df, f"{int(unknown.sum())} unknown measure rows")

    df["READMISSIONS_SUPPRESSED"] = df["NUMBER_OF_READMISSIONS"].eq("Too Few to Report")
    for col in ["NUMBER_OF_DISCHARGES", "EXCESS_READMISSION_RATIO", "PREDICTED_READMISSION_RATE",
                "EXPECTED_READMISSION_RATE", "NUMBER_OF_READMISSIONS"]:
        df[col] = to_numeric(df[col].replace("Too Few to Report", pd.NA))
    df["START_DATE"] = pd.to_datetime(df["START_DATE"], format="%m/%d/%Y")
    df["END_DATE"] = pd.to_datetime(df["END_DATE"], format="%m/%d/%Y")
    df["FOOTNOTE"] = df["FOOTNOTE"].astype("string")

    keys = ["FACILITY_ID", "MEASURE_CODE", "START_DATE", "END_DATE"]
    dups = find_duplicates(df, keys)
    if not dups.empty:
        review = pd.concat([review, dups.assign(REVIEW_REASON="DUPLICATE_KEY")])
        df = df.drop_duplicates(keys, keep=False)
    clog.record(ds, "duplicate key check", df, f"{len(dups)} rows in duplicate groups removed to review")

    out_of_range = range_violations(df)
    readm_gt_dis = df["NUMBER_OF_READMISSIONS"] > df["NUMBER_OF_DISCHARGES"]
    bad_rows = set(out_of_range["row"]) | set(df.index[readm_gt_dis.fillna(False)])

    df["RECORD_STATUS"] = np.select(
        [df.index.isin(list(bad_rows)), df["EXCESS_READMISSION_RATIO"].isna()],
        ["REVIEW", "EXCLUDED_NO_ERR"], default="ANALYZABLE")
    for status, n in df["RECORD_STATUS"].value_counts().items():
        clog.record(ds, f"status={status}", df[df["RECORD_STATUS"] == status])

    cols = ["FACILITY_ID", "FACILITY_NAME", "STATE", "MEASURE_CODE", "MEASURE_NAME",
            "NUMBER_OF_DISCHARGES", "FOOTNOTE", "EXCESS_READMISSION_RATIO",
            "PREDICTED_READMISSION_RATE", "EXPECTED_READMISSION_RATE", "NUMBER_OF_READMISSIONS",
            "READMISSIONS_SUPPRESSED", "START_DATE", "END_DATE", "RECORD_STATUS"]
    return df[cols].reset_index(drop=True), review


def clean_supplemental(raw: pd.DataFrame, clog: CleaningLog) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """HRRP Supplemental File -> (hospital-level payment table, long measure table, review rows)."""
    ds = "hrrp_supplemental"
    clog.record(ds, "raw load", raw)
    df = raw[~raw["Hospital CCN"].astype(str).str.contains("End of worksheet", na=False)].copy()
    clog.record(ds, "drop footer row", df)
    df = df.rename(columns={"Hospital CCN": "FACILITY_ID"})
    df, review = split_invalid_ids(df, "FACILITY_ID", ds, clog)

    hosp = pd.DataFrame({
        "FACILITY_ID": df["FACILITY_ID"],
        "PAYMENT_ADJUSTMENT_FACTOR": to_numeric(df["Payment adjustment factor"]),
        "PAYMENT_REDUCTION_PCT": to_numeric(df["Payment reduction percentage"]),
        "DUAL_PROPORTION": to_numeric(df["Dual proportion"]),
        "PEER_GROUP": to_numeric(df["Peer group assignment"]).astype("Int64"),
        "NEUTRALITY_MODIFIER": to_numeric(df["Neutrality modifier"]),
    })
    # Consistency check: reduction % should equal (1 - factor) * 100.
    implied = (1 - hosp["PAYMENT_ADJUSTMENT_FACTOR"]) * 100
    hosp["FACTOR_PCT_CONSISTENT"] = (implied - hosp["PAYMENT_REDUCTION_PCT"]).abs() < 0.006
    hosp["IS_PENALIZED"] = hosp["PAYMENT_ADJUSTMENT_FACTOR"] < 1.0  # derived: any payment reduction
    dups = find_duplicates(hosp, ["FACILITY_ID"])
    if not dups.empty:
        review = pd.concat([review, dups.assign(REVIEW_REASON="DUPLICATE_KEY")])
        hosp = hosp.drop_duplicates("FACILITY_ID", keep=False)
    clog.record(ds, "hospital payment table", hosp, f"{len(dups)} duplicate rows to review")

    parts = []
    for label, code in SUPPLEMENTAL_MEASURE_MAP.items():
        parts.append(pd.DataFrame({
            "FACILITY_ID": df["FACILITY_ID"].values,
            "MEASURE_CODE": code,
            "ELIGIBLE_DISCHARGES": to_numeric(df[f"Number of eligible discharges for {label}"]).values,
            "ERR": to_numeric(df[f"ERR for {label}"]).values,
            "PEER_GROUP_MEDIAN_ERR": to_numeric(df[f"Peer group median ERR for {label}"]).values,
            "PENALTY_INDICATOR": df[f"Penalty indicator for {label}"].str.strip().values,
            "DRG_PAYMENT_RATIO": to_numeric(df[f"DRG payment ratio for {label}"]).values,
        }))
    measures = pd.concat(parts, ignore_index=True)
    clog.record(ds, "measure table (wide->long)", measures)
    return hosp.reset_index(drop=True), measures, review


def clean_general_info(raw: pd.DataFrame, clog: CleaningLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    ds = "hospital_general_info"
    clog.record(ds, "raw load", raw)
    df = standardize_columns(raw)
    df, review = split_invalid_ids(df, "FACILITY_ID", ds, clog)
    out = pd.DataFrame({
        "FACILITY_ID": df["FACILITY_ID"],
        "FACILITY_NAME": df["FACILITY_NAME"],
        "CITY": df["CITY_TOWN"],
        "STATE": df["STATE"],
        "COUNTY": df["COUNTY_PARISH"],
        "HOSPITAL_TYPE": df["HOSPITAL_TYPE"],
        "HOSPITAL_OWNERSHIP": df["HOSPITAL_OWNERSHIP"],
        "EMERGENCY_SERVICES": df["EMERGENCY_SERVICES"],
        "OVERALL_RATING": to_numeric(df["HOSPITAL_OVERALL_RATING"]).astype("Int64"),
    })
    dups = find_duplicates(out, ["FACILITY_ID"])
    out = out.drop_duplicates("FACILITY_ID", keep=False)
    clog.record(ds, "typed + deduplicated", out, f"{len(dups)} duplicate rows removed")
    return out.reset_index(drop=True), review


def clean_impact(raw: pd.DataFrame, clog: CleaningLog) -> tuple[pd.DataFrame, pd.DataFrame]:
    ds = "ipps_impact"
    clog.record(ds, "raw load", raw)
    df = raw.rename(columns={"Provider Number": "FACILITY_ID"})
    df, review = split_invalid_ids(df, "FACILITY_ID", ds, clog)
    rtb = to_numeric(df["Resident to Bed Ratio"])
    out = pd.DataFrame({
        "FACILITY_ID": df["FACILITY_ID"],
        "CENSUS_REGION": df["Region"].str.strip().map(REGION_MAP),
        "URBAN_RURAL": df["URGEO"].str.strip().map(URGEO_MAP),
        "RESIDENT_TO_BED_RATIO": rtb,
        # Derived proxy: any residents => teaching. Documented in docs/data_cleaning_decisions.md.
        "TEACHING_STATUS": np.select([rtb > 0, rtb == 0], ["Teaching", "Non-teaching"], default=None),
        "BEDS": to_numeric(df["Beds"]),
        "DSH_PATIENT_PCT": to_numeric(df["DSHPCT"]),
        "OWNERSHIP_CONTROL": df["Ownership Control Type"].str.strip().map(OWNERSHIP_CONTROL_MAP),
    })
    out["BED_SIZE_GROUP"] = pd.cut(out["BEDS"], bins=[-np.inf, 99, 299, np.inf],
                                   labels=["<100 beds", "100-299 beds", "300+ beds"]).astype("string")
    out = out.drop_duplicates("FACILITY_ID", keep=False)
    clog.record(ds, "typed", out)
    return out.reset_index(drop=True), review


# ---------------------------------------------------------------------------
# Joins
# ---------------------------------------------------------------------------
def join_report(left: pd.DataFrame, right: pd.DataFrame, key: str, left_name: str, right_name: str) -> dict:
    """Match statistics for a key join, used to validate merges before relying on them."""
    lk, rk = set(left[key].dropna()), set(right[key].dropna())
    return {"left": left_name, "right": right_name, "left_keys": len(lk), "right_keys": len(rk),
            "matched": len(lk & rk), "left_only": len(lk - rk), "right_only": len(rk - lk),
            "left_match_pct": round(100 * len(lk & rk) / len(lk), 2) if lk else np.nan}


def build_hospital_dim(supp_hosp: pd.DataFrame, general: pd.DataFrame, impact: pd.DataFrame,
                       hrrp: pd.DataFrame) -> pd.DataFrame:
    """One row per HRRP hospital with attributes from all sources (left joins, no rows dropped)."""
    base = hrrp[["FACILITY_ID", "FACILITY_NAME", "STATE"]].drop_duplicates("FACILITY_ID")
    dim = (base.merge(general.drop(columns=["FACILITY_NAME", "STATE"]), on="FACILITY_ID", how="left")
               .merge(impact, on="FACILITY_ID", how="left")
               .merge(supp_hosp, on="FACILITY_ID", how="left"))
    dim["IN_GENERAL_INFO"] = dim["FACILITY_ID"].isin(general["FACILITY_ID"])
    dim["IN_IMPACT_FILE"] = dim["FACILITY_ID"].isin(impact["FACILITY_ID"])
    dim["IN_SUPPLEMENTAL"] = dim["FACILITY_ID"].isin(supp_hosp["FACILITY_ID"])
    return dim


def reconcile_err(hrrp: pd.DataFrame, supp_measures: pd.DataFrame, tol: float = 1e-4) -> pd.DataFrame:
    """Compare ERR between the Provider Data file and the Supplemental file (same period)."""
    m = hrrp.merge(supp_measures, on=["FACILITY_ID", "MEASURE_CODE"], how="inner")
    both = m["EXCESS_READMISSION_RATIO"].notna() & m["ERR"].notna()
    m["ERR_MATCH"] = np.where(both, (m["EXCESS_READMISSION_RATIO"] - m["ERR"]).abs() <= tol, pd.NA)
    return m


# ---------------------------------------------------------------------------
# Imputation (guarded; see docs/data_sources.md for why it is not applied to ERR)
# ---------------------------------------------------------------------------
def impute_from_reference(df: pd.DataFrame, value_col: str, reference: pd.DataFrame,
                          ref_value_col: str, keys: list[str], method: str) -> pd.DataFrame:
    """Fill missing df[value_col] from reference rows that match on *all* keys.

    The keys must include the measure and the reporting period so a value is
    only ever filled from a comparable source. Adds audit columns:
      <value_col>_ORIGINAL   the value before imputation (NaN if missing)
      IMPUTED_FLAG           1 if this row was filled, else 0
      IMPUTATION_METHOD      text description (only where filled)
      IMPUTATION_SOURCE_VALUE the reference value used (only where filled)
    Rows with no matching reference stay missing.
    """
    missing_keys = [k for k in keys if k not in reference.columns or k not in df.columns]
    if missing_keys:
        raise ValueError(f"Keys missing from inputs: {missing_keys}")
    if reference.duplicated(keys).any():
        raise ValueError("Reference has duplicate key rows; imputation would be ambiguous.")

    out = df.copy()
    ref = reference[keys + [ref_value_col]].rename(columns={ref_value_col: "_REF"})
    out = out.merge(ref, on=keys, how="left", validate="many_to_one")
    out[f"{value_col}_ORIGINAL"] = out[value_col]
    fill = out[value_col].isna() & out["_REF"].notna()
    out.loc[fill, value_col] = out.loc[fill, "_REF"]
    out["IMPUTED_FLAG"] = fill.astype(int)
    out["IMPUTATION_METHOD"] = np.where(fill, method, None)
    out["IMPUTATION_SOURCE_VALUE"] = out["_REF"].where(fill)
    return out.drop(columns="_REF")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(raw_dir: Path = ing.RAW_DIR, out_dir: Path = PROCESSED_DIR) -> dict[str, pd.DataFrame]:
    clog = CleaningLog()
    hrrp, r1 = clean_hrrp(ing.load_csv("hrrp_hospital", raw_dir), clog)
    supp_hosp, supp_meas, r2 = clean_supplemental(ing.load_hrrp_supplemental(raw_dir), clog)
    general, r3 = clean_general_info(ing.load_csv("hospital_general_info", raw_dir), clog)
    impact, r4 = clean_impact(ing.load_ipps_impact(raw_dir), clog)

    joins = pd.DataFrame([
        join_report(hrrp, general, "FACILITY_ID", "hrrp_hospital", "hospital_general_info"),
        join_report(hrrp, impact, "FACILITY_ID", "hrrp_hospital", "ipps_impact"),
        join_report(hrrp, supp_hosp, "FACILITY_ID", "hrrp_hospital", "hrrp_supplemental"),
    ])
    dim = build_hospital_dim(supp_hosp, general, impact, hrrp)
    clog.record("hospital_dim", "left-joined attributes", dim)

    recon = reconcile_err(hrrp, supp_meas)
    measure_fact = hrrp.merge(
        supp_meas[["FACILITY_ID", "MEASURE_CODE", "ELIGIBLE_DISCHARGES", "PEER_GROUP_MEDIAN_ERR",
                   "PENALTY_INDICATOR", "DRG_PAYMENT_RATIO"]],
        on=["FACILITY_ID", "MEASURE_CODE"], how="left", validate="one_to_one")
    # Derived estimate, NOT a CMS-published count:
    #   eligible discharges x (predicted rate - expected rate) / 100
    # Positive = more readmissions than an average hospital with the same case mix
    # would be expected to have; negative = fewer. Eligible discharges come from the
    # Supplemental file (same 2021-2024 period) because the Provider Data file
    # suppresses discharge counts for many rows.
    measure_fact["EST_EXCESS_READMISSIONS"] = np.where(
        measure_fact["RECORD_STATUS"].eq("ANALYZABLE"),
        measure_fact["ELIGIBLE_DISCHARGES"]
        * (measure_fact["PREDICTED_READMISSION_RATE"] - measure_fact["EXPECTED_READMISSION_RATE"]) / 100,
        np.nan)
    clog.record("measure_fact", "HRRP + supplemental measures", measure_fact)

    review = pd.concat([r.assign(SOURCE=s) for r, s in
                        [(r1, "hrrp_hospital"), (r2, "hrrp_supplemental"),
                         (r3, "hospital_general_info"), (r4, "ipps_impact")] if not r.empty],
                       ignore_index=True) if any(not r.empty for r in (r1, r2, r3, r4)) else pd.DataFrame()

    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {"measure_fact": measure_fact, "hospital_dim": dim, "join_report": joins,
              "err_reconciliation": recon[["FACILITY_ID", "MEASURE_CODE", "EXCESS_READMISSION_RATIO", "ERR", "ERR_MATCH"]],
              "review_records": review, "cleaning_log": clog.to_frame()}
    for name, t in tables.items():
        t.to_csv(out_dir / f"{name}.csv", index=False, date_format="%Y-%m-%d")
    return tables


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    t = run_pipeline()
    print("\nJoin validation:\n", t["join_report"].to_string(index=False))
    rec = t["err_reconciliation"]["ERR_MATCH"].dropna()
    print(f"\nERR reconciliation (Provider Data vs Supplemental): {int(rec.sum())}/{len(rec)} match")
    print(f"Review records: {len(t['review_records'])}")
