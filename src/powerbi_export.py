"""Export a Power BI-ready star schema from data/processed/.

    python -m src.powerbi_export

Writes to data/powerbi/ (git-ignored; regenerate any time):
  hospital.csv           one row per HRRP hospital (dimension + hospital-level payment facts)
  measure.csv            six HRRP clinical measures with labels and definitions
  readmission_fact.csv   one row per hospital x measure x performance period

Column names are human-readable because they appear directly in report visuals.
Yes/No text is used instead of booleans so slicers read naturally.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src import statistical_analysis as sa
from src.data_cleaning import MEASURE_LABELS, PROCESSED_DIR
from src.data_ingestion import PROJECT_ROOT

POWERBI_DIR = PROJECT_ROOT / "data" / "powerbi"

MEASURE_ORDER = ["HF", "PN", "AMI", "COPD", "CABG", "HIP_KNEE"]
MEASURE_SHORT = {"AMI": "AMI", "CABG": "CABG", "COPD": "COPD", "HF": "Heart failure",
                 "HIP_KNEE": "Hip/knee", "PN": "Pneumonia"}


def _yes_no(s: pd.Series) -> pd.Series:
    return s.map({True: "Yes", False: "No", "True": "Yes", "False": "No"})


def build_tables(measure_fact: pd.DataFrame, hospital_dim: pd.DataFrame) -> dict[str, pd.DataFrame]:
    contrib = sa.payment_contributions(measure_fact, hospital_dim)
    flags = measure_fact[measure_fact["PENALTY_INDICATOR"].eq("Y")].groupby("FACILITY_ID").size()

    hosp = pd.DataFrame({
        "Facility ID": hospital_dim["FACILITY_ID"],
        "Hospital": hospital_dim["FACILITY_NAME"],
        "State": hospital_dim["STATE"],
        "City": hospital_dim["CITY"],
        "Census Division": hospital_dim["CENSUS_REGION"],
        "Urban/Rural": hospital_dim["URBAN_RURAL"],
        "Teaching Status": hospital_dim["TEACHING_STATUS"],
        "Ownership": hospital_dim["OWNERSHIP_CONTROL"],
        "Hospital Type": hospital_dim["HOSPITAL_TYPE"],
        "Bed Size Group": hospital_dim["BED_SIZE_GROUP"],
        "Beds": hospital_dim["BEDS"],
        "Star Rating": hospital_dim["OVERALL_RATING"],
        "Has Payment Factor": _yes_no(hospital_dim["IN_SUPPLEMENTAL"]),
        "Payment Adjustment Factor": hospital_dim["PAYMENT_ADJUSTMENT_FACTOR"],
        "Payment Reduction %": hospital_dim["PAYMENT_REDUCTION_PCT"],
        "Payment Reduced": np.where(hospital_dim["IN_SUPPLEMENTAL"].astype(str) == "True",
                                    _yes_no(hospital_dim["IS_PENALIZED"]), None),
        "Dual Proportion": hospital_dim["DUAL_PROPORTION"],
        "Peer Group": hospital_dim["PEER_GROUP"].astype("Int64"),
    })
    hosp["Measures Flagged"] = hosp["Facility ID"].map(flags).fillna(0).astype(int)
    hosp.loc[hosp["Has Payment Factor"] != "Yes", "Measures Flagged"] = pd.NA
    for col in ["Census Division", "Urban/Rural", "Teaching Status", "Ownership", "Hospital Type", "Bed Size Group"]:
        hosp[col] = hosp[col].fillna("Unknown")

    measure = pd.DataFrame({
        "Measure Code": MEASURE_ORDER,
        "Measure": [MEASURE_SHORT[c] for c in MEASURE_ORDER],
        "Measure Full Name": [MEASURE_LABELS[c] for c in MEASURE_ORDER],
        "Measure Sort": range(1, len(MEASURE_ORDER) + 1),
    })

    fact = measure_fact.merge(contrib[["FACILITY_ID", "MEASURE_CODE", "CONTRIB_PCT_POINTS"]],
                              on=["FACILITY_ID", "MEASURE_CODE"], how="left")
    start = pd.to_datetime(fact["START_DATE"])
    end = pd.to_datetime(fact["END_DATE"])
    out = pd.DataFrame({
        "Facility ID": fact["FACILITY_ID"],
        "Measure Code": fact["MEASURE_CODE"],
        "Period Start": start.dt.strftime("%Y-%m-%d"),
        "Period End": end.dt.strftime("%Y-%m-%d"),
        "Reporting Period": start.dt.strftime("%b %Y") + " - " + end.dt.strftime("%b %Y"),
        "Record Status": fact["RECORD_STATUS"],
        "ERR": fact["EXCESS_READMISSION_RATIO"],
        "Predicted Rate": fact["PREDICTED_READMISSION_RATE"],
        "Expected Rate": fact["EXPECTED_READMISSION_RATE"],
        "Discharges": fact["NUMBER_OF_DISCHARGES"],
        "Eligible Discharges": fact["ELIGIBLE_DISCHARGES"],
        "Readmissions": fact["NUMBER_OF_READMISSIONS"],
        "Peer Median ERR": fact["PEER_GROUP_MEDIAN_ERR"],
        "Penalty Indicator": fact["PENALTY_INDICATOR"],
        "Payment Reduction Points": fact["CONTRIB_PCT_POINTS"],
        "Est Excess Readmissions": fact["EST_EXCESS_READMISSIONS"].clip(lower=0),
    })
    return {"hospital": hosp, "measure": measure, "readmission_fact": out}


def export(out_dir: Path = POWERBI_DIR, processed_dir: Path = PROCESSED_DIR) -> dict[str, pd.DataFrame]:
    mf = pd.read_csv(processed_dir / "measure_fact.csv", dtype={"FACILITY_ID": str})
    dim = pd.read_csv(processed_dir / "hospital_dim.csv", dtype={"FACILITY_ID": str})
    tables = build_tables(mf, dim)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, t in tables.items():
        # UTF-8 with BOM so Power BI and Excel detect the encoding of names like "SAINT JOSEPH'S".
        t.to_csv(out_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
    return tables


if __name__ == "__main__":
    for name, t in export().items():
        print(f"{name:18s} {t.shape[0]:>6,d} rows x {t.shape[1]} cols")
