"""Load processed data into Oracle and run the SQL scripts.

Credentials (never stored in the repository):
  ORACLE_USER, ORACLE_DSN  environment variables (set by scripts/install_oracle_free.ps1)
  password                 ORACLE_PASSWORD env var if set, otherwise Windows Credential
                           Manager via keyring (service "hrra_oracle")

Usage:
    python -m src.oracle_loader --check          # test the connection only
    python -m src.oracle_loader --all            # create tables, load, run QC + analysis SQL
"""
from __future__ import annotations

import argparse
import logging
import os
import re
from pathlib import Path

import numpy as np
import oracledb
import pandas as pd

from src.data_cleaning import MEASURE_LABELS, PROCESSED_DIR
from src.data_ingestion import PROJECT_ROOT, load_csv

SQL_DIR = PROJECT_ROOT / "sql"
OUT_DIR = PROJECT_ROOT / "reports" / "sql_results"
KEYRING_SERVICE = "hrra_oracle"
log = logging.getLogger(__name__)


def _env(name: str) -> str | None:
    """Read an env var; on Windows fall back to the persisted user-level value
    (a terminal opened before the installer ran will not see it in os.environ)."""
    val = os.environ.get(name)
    if val is None and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                val = winreg.QueryValueEx(k, name)[0]
        except OSError:
            pass
    return val


def connect() -> oracledb.Connection:
    user, dsn = _env("ORACLE_USER"), _env("ORACLE_DSN")
    if not user or not dsn:
        raise RuntimeError("Set ORACLE_USER and ORACLE_DSN (see docs/oracle_setup.md).")
    password = os.environ.get("ORACLE_PASSWORD")
    if not password:
        import keyring
        password = keyring.get_password(KEYRING_SERVICE, user)
    if not password:
        raise RuntimeError(f"No password found. Run: python -m keyring set {KEYRING_SERVICE} {user}")
    return oracledb.connect(user=user, password=password, dsn=dsn)  # thin mode: no Oracle client needed


# ---------------------------------------------------------------------------
# SQL script runner
# ---------------------------------------------------------------------------
LABEL_RE = re.compile(r"^\s*--\s*@name\s+(\w+)\s*$", re.M)


def split_sql(script: str) -> list[str]:
    """Split an Oracle script into statements.

    * Lines that are only '/' end PL/SQL blocks.
    * Otherwise statements end with ';' at end of line.
    * SQL*Plus/SQL Developer client commands (PROMPT, SET, SPOOL, COLUMN) are skipped.
    """
    statements, buf, in_plsql = [], [], False
    for line in script.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if not buf and (not stripped or (stripped.startswith("--") and not LABEL_RE.match(stripped))):
            continue
        if not buf and re.match(r"^(PROMPT|SET |SPOOL|COLUMN|REM )", upper):
            continue
        if not buf and re.match(r"^(BEGIN|DECLARE|CREATE (OR REPLACE )?(PROCEDURE|FUNCTION|TRIGGER|PACKAGE))", upper):
            in_plsql = True
        if in_plsql:
            if stripped == "/":
                statements.append("\n".join(buf)); buf, in_plsql = [], False
            else:
                buf.append(line)
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).rstrip().rstrip(";")
            statements.append(stmt); buf = []
    if buf and "".join(buf).strip():
        statements.append("\n".join(buf).rstrip().rstrip(";"))
    return statements


def _label(stmt: str) -> str | None:
    m = LABEL_RE.search(stmt)
    return m.group(1) if m else None


def run_script(conn, path: Path, save_results: bool = True) -> list[dict]:
    """Execute each statement; save SELECT results to reports/sql_results/<script>__<name>.csv."""
    results = []
    with conn.cursor() as cur:
        for i, stmt in enumerate(split_sql(path.read_text(encoding="utf-8")), 1):
            name = _label(stmt) or f"stmt{i:02d}"
            body = re.sub(r"^\s*--.*$", "", stmt, flags=re.M).strip()
            kind = body.split(None, 1)[0].upper() if body else ""
            try:
                cur.execute(stmt)
                if kind in ("SELECT", "WITH"):
                    cols = [d[0] for d in cur.description]
                    df = pd.DataFrame(cur.fetchall(), columns=cols)
                    if save_results:
                        OUT_DIR.mkdir(parents=True, exist_ok=True)
                        df.to_csv(OUT_DIR / f"{path.stem}__{name}.csv", index=False)
                    results.append({"script": path.name, "name": name, "status": "ok", "rows": len(df)})
                else:
                    results.append({"script": path.name, "name": name, "status": "ok", "rows": cur.rowcount})
            except oracledb.DatabaseError as e:
                err = e.args[0]
                # Tolerate "table/index does not exist" on DROP so scripts are re-runnable.
                if kind == "DROP" and getattr(err, "code", None) in (942, 1418, 2289, 4043):
                    results.append({"script": path.name, "name": name, "status": "skipped (not found)", "rows": 0})
                    continue
                results.append({"script": path.name, "name": name, "status": f"ERROR {err.message}", "rows": None})
                log.error("%s %s failed: %s", path.name, name, err.message)
        conn.commit()
    return results


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
STAGE_HRRP_COLS = ["Facility Name", "Facility ID", "State", "Measure Name", "Number of Discharges", "Footnote",
                   "Excess Readmission Ratio", "Predicted Readmission Rate", "Expected Readmission Rate",
                   "Number of Readmissions", "Start Date", "End Date"]


def _rows(df: pd.DataFrame) -> list[tuple]:
    """DataFrame -> list of tuples with NaN/NA converted to None for Oracle NULL."""
    clean = df.astype(object).where(pd.notna(df), None)
    out = []
    for row in clean.itertuples(index=False, name=None):
        out.append(tuple(v.item() if isinstance(v, np.generic) else v for v in row))
    return out


def _insert(conn, table: str, df: pd.DataFrame) -> int:
    cols = list(df.columns)
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(':' + str(i + 1) for i in range(len(cols)))})"
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM {table}")
        cur.executemany(sql, _rows(df), batcherrors=False)
    conn.commit()
    return len(df)


def load_all(conn) -> dict:
    counts = {}
    # 1. Raw staging table: CMS provider file exactly as published (all text).
    raw = load_csv("hrrp_hospital")[STAGE_HRRP_COLS]
    raw.columns = ["FACILITY_NAME", "FACILITY_ID", "STATE", "MEASURE_NAME", "NUMBER_OF_DISCHARGES", "FOOTNOTE",
                   "EXCESS_READMISSION_RATIO", "PREDICTED_READMISSION_RATE", "EXPECTED_READMISSION_RATE",
                   "NUMBER_OF_READMISSIONS", "START_DATE", "END_DATE"]
    counts["STG_HRRP_HOSPITAL"] = _insert(conn, "STG_HRRP_HOSPITAL", raw)

    # 2. Dimensions.
    m = pd.DataFrame({"MEASURE_CODE": list(MEASURE_LABELS), "MEASURE_LABEL": list(MEASURE_LABELS.values())})
    counts["DIM_MEASURE"] = _insert(conn, "DIM_MEASURE", m)

    dim = pd.read_csv(PROCESSED_DIR / "hospital_dim.csv", dtype={"FACILITY_ID": str})
    hosp_cols = ["FACILITY_ID", "FACILITY_NAME", "CITY", "STATE", "COUNTY", "HOSPITAL_TYPE", "HOSPITAL_OWNERSHIP",
                 "EMERGENCY_SERVICES", "OVERALL_RATING", "CENSUS_REGION", "URBAN_RURAL", "RESIDENT_TO_BED_RATIO",
                 "TEACHING_STATUS", "BEDS", "BED_SIZE_GROUP", "DSH_PATIENT_PCT", "OWNERSHIP_CONTROL",
                 "IN_GENERAL_INFO", "IN_IMPACT_FILE", "IN_SUPPLEMENTAL"]
    h = dim[hosp_cols].copy()
    for c in ["IN_GENERAL_INFO", "IN_IMPACT_FILE", "IN_SUPPLEMENTAL"]:
        h[c] = h[c].map({True: "Y", False: "N"})
    counts["DIM_HOSPITAL"] = _insert(conn, "DIM_HOSPITAL", h)

    pay = dim[dim["IN_SUPPLEMENTAL"]][["FACILITY_ID", "PAYMENT_ADJUSTMENT_FACTOR", "PAYMENT_REDUCTION_PCT",
                                       "DUAL_PROPORTION", "PEER_GROUP", "NEUTRALITY_MODIFIER"]].copy()
    pay["PEER_GROUP"] = pay["PEER_GROUP"].astype(int)
    pay["PROGRAM_YEAR"] = "FY2026"
    counts["FACT_HOSPITAL_PAYMENT"] = _insert(conn, "FACT_HOSPITAL_PAYMENT", pay)

    # 3. Measure fact.
    mf = pd.read_csv(PROCESSED_DIR / "measure_fact.csv", dtype={"FACILITY_ID": str, "FOOTNOTE": str},
                     parse_dates=["START_DATE", "END_DATE"])
    f = mf[["FACILITY_ID", "MEASURE_CODE", "START_DATE", "END_DATE", "NUMBER_OF_DISCHARGES", "ELIGIBLE_DISCHARGES",
            "FOOTNOTE", "EXCESS_READMISSION_RATIO", "PREDICTED_READMISSION_RATE", "EXPECTED_READMISSION_RATE",
            "NUMBER_OF_READMISSIONS", "READMISSIONS_SUPPRESSED", "PEER_GROUP_MEDIAN_ERR", "PENALTY_INDICATOR",
            "DRG_PAYMENT_RATIO", "EST_EXCESS_READMISSIONS", "RECORD_STATUS"]].copy()
    f["READMISSIONS_SUPPRESSED"] = f["READMISSIONS_SUPPRESSED"].map({True: "Y", False: "N"})
    f["START_DATE"] = f["START_DATE"].dt.to_pydatetime()
    f["END_DATE"] = f["END_DATE"].dt.to_pydatetime()
    counts["FACT_READMISSION_MEASURE"] = _insert(conn, "FACT_READMISSION_MEASURE", f)
    return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="test the connection")
    ap.add_argument("--all", action="store_true", help="create tables, load data, run all SQL scripts")
    args = ap.parse_args()

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT banner_full FROM v$version")
            print("Connected:", cur.fetchone()[0].splitlines()[0])
            cur.execute("SELECT sys_context('USERENV','CON_NAME'), user FROM dual")
            print("Container / user:", cur.fetchone())
        if not args.all:
            return
        report = run_script(conn, SQL_DIR / "create_tables.sql", save_results=False)
        print("Loaded:", load_all(conn))
        for script in ["data_quality_checks.sql", "readmission_analysis.sql", "hospital_rankings.sql"]:
            report += run_script(conn, SQL_DIR / script)
        rep = pd.DataFrame(report)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        rep.to_csv(OUT_DIR / "_execution_log.csv", index=False)
        print(rep.to_string(index=False))
        errors = rep["status"].str.startswith("ERROR").sum()
        print(f"\n{len(rep)} statements, {errors} errors")
        if errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
