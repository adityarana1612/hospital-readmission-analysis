"""SQL script tests.

Unit tests (always run): script splitting/labelling and static checks that the
scripts use Oracle syntax and match the loader's table names.
Integration tests (run only when an Oracle connection is configured): re-check
data-quality invariants directly in the database.
"""
import re
from pathlib import Path

import pandas as pd
import pytest

from src import oracle_loader as ol

SQL_DIR = Path(__file__).resolve().parents[1] / "sql"
SCRIPTS = ["create_tables.sql", "data_quality_checks.sql", "readmission_analysis.sql", "hospital_rankings.sql"]


def test_split_sql_handles_plsql_labels_and_client_commands():
    script = """
-- header comment mentioning @name <id> must not become a label
PROMPT loading
BEGIN
  NULL;
END;
/
-- @name first_query
SELECT 1 FROM dual;
CREATE TABLE t (a NUMBER);
"""
    stmts = ol.split_sql(script)
    assert len(stmts) == 3
    assert stmts[0].strip().startswith("BEGIN") and stmts[0].strip().endswith("END;")
    assert ol._label(stmts[1]) == "first_query"
    assert ol._label(stmts[2]) is None and not stmts[2].endswith(";")


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_parse_and_labels_are_unique(name):
    stmts = ol.split_sql((SQL_DIR / name).read_text(encoding="utf-8"))
    assert stmts, f"{name} produced no statements"
    labels = [ol._label(s) for s in stmts if ol._label(s)]
    assert len(labels) == len(set(labels)), "duplicate @name labels"
    if name != "create_tables.sql":
        assert len(labels) == len(stmts), "every analytical query should carry an @name label"


def test_scripts_avoid_non_oracle_syntax():
    banned = [r"\bLIMIT\s+\d", r"\bSERIAL\b", r"\bAUTO_INCREMENT\b", r"::\w", r"\bILIKE\b", r"\bBOOLEAN\b",
              r"IF\s+EXISTS"]  # IF EXISTS is 23ai-only; scripts target 19c+
    for name in SCRIPTS:
        text = (SQL_DIR / name).read_text(encoding="utf-8")
        for pat in banned:
            assert not re.search(pat, text, re.I), f"{name} contains non-Oracle/19c syntax: {pat}"


def test_required_analytical_features_present():
    text = " ".join((SQL_DIR / n).read_text(encoding="utf-8") for n in SCRIPTS).upper()
    for feature in ["WITH ", "RANK()", "DENSE_RANK()", "CASE", "OVER (PARTITION BY", "RATIO_TO_REPORT"]:
        assert feature in text, feature


def test_loader_columns_exist_in_ddl():
    ddl = (SQL_DIR / "create_tables.sql").read_text(encoding="utf-8").upper()
    for col in ol.STAGE_HRRP_COLS:
        assert re.sub(r"[^A-Z]+", "_", col.upper()).strip("_") in ddl


# ---------------------------------------------------------------- integration
@pytest.fixture(scope="module")
def conn():
    try:
        c = ol.connect()
    except Exception as e:  # no DB configured / not running
        pytest.skip(f"Oracle not available: {e}")
    with c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM user_tables WHERE table_name = 'FACT_READMISSION_MEASURE'")
        if cur.fetchone()[0] == 0:
            pytest.skip("tables not created - run python -m src.oracle_loader --all")
    yield c
    c.close()


def _scalar(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def test_oracle_row_counts_match_processed_files(conn, processed_dir):
    mf = pd.read_csv(processed_dir / "measure_fact.csv")
    dim = pd.read_csv(processed_dir / "hospital_dim.csv")
    assert _scalar(conn, "SELECT COUNT(*) FROM fact_readmission_measure") == len(mf)
    assert _scalar(conn, "SELECT COUNT(*) FROM dim_hospital") == len(dim)
    assert _scalar(conn, "SELECT COUNT(*) FROM fact_readmission_measure WHERE record_status = 'ANALYZABLE'") \
        == (mf.RECORD_STATUS == "ANALYZABLE").sum()


def test_oracle_violation_checks_return_nothing(conn):
    text = (SQL_DIR / "data_quality_checks.sql").read_text(encoding="utf-8")
    for stmt in ol.split_sql(text):
        name = ol._label(stmt)
        if name and name.endswith("_violations"):
            with conn.cursor() as cur:
                cur.execute(stmt)
                rows = cur.fetchall()
            bad = rows if len(rows) != 1 or len(rows[0]) != 1 else ([] if rows[0][0] == 0 else rows)
            assert not bad, f"{name} returned {rows[:3]}"


def test_oracle_mean_err_matches_pandas(conn, processed_dir):
    mf = pd.read_csv(processed_dir / "measure_fact.csv")
    expected = mf[mf.RECORD_STATUS == "ANALYZABLE"].groupby("MEASURE_CODE")["EXCESS_READMISSION_RATIO"].mean()
    with conn.cursor() as cur:
        cur.execute("SELECT measure_code, AVG(excess_readmission_ratio) FROM fact_readmission_measure "
                    "WHERE record_status = 'ANALYZABLE' GROUP BY measure_code")
        got = dict(cur.fetchall())
    for code, val in expected.items():
        assert float(got[code]) == pytest.approx(val, abs=1e-9)
