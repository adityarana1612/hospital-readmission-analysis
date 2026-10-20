# Oracle Database Setup and SQL Execution

**Status:** executed and verified on 2026-10-21 against **Oracle AI Database 26ai Free, Release 23.26.3**
(Windows 11, pluggable database `FREEPDB1`, schema `HRRA`). All 43 statements ran with 0 errors, and the
Oracle integration tests (`tests/test_sql.py`) pass.

The scripts use standard Oracle SQL and PL/SQL and avoid 23ai-only syntax (such as `DROP TABLE IF EXISTS`),
so they should also run on **Oracle Database 19c or later**. `VALIDATE_CONVERSION` and
`TO_NUMBER ... DEFAULT ... ON CONVERSION ERROR` need 12.2+. Only 26ai Free was actually tested.

## 1. Install Oracle Database Free (Windows)

1. Download `oracle-ai-database-free-26ai-23.26.3.windows.x64.zip` from
   https://www.oracle.com/database/free/get-started/ and verify its SHA-256 against the value on that page:
   ```powershell
   Get-FileHash .\oracle-ai-database-free-26ai-23.26.3.windows.x64.zip -Algorithm SHA256
   ```
2. Extract it, for example to `D:\oracle-installer\setup`. Keep it **outside** the repository.
3. In an **elevated** PowerShell (Run as Administrator):
   ```powershell
   powershell -ExecutionPolicy Bypass -File D:\hospital-readmission-analysis\scripts\install_oracle_free.ps1
   ```
   The script:
   * prompts for the admin (SYS/SYSTEM) password and the project-schema password without echoing them;
   * writes Oracle's response file to `%TEMP%`, runs the silent install, and **deletes the response file**
     (it contains the admin password);
   * creates schema user `HRRA` in `FREEPDB1` with only the privileges it needs (`CREATE SESSION/TABLE/VIEW/SEQUENCE/PROCEDURE`);
   * sets the non-secret user environment variables `ORACLE_USER=HRRA` and `ORACLE_DSN=localhost:1521/FREEPDB1`;
   * stores the `HRRA` password in **Windows Credential Manager** (service `hrra_oracle`) via `keyring`.

## 2. How credentials are resolved

`src/oracle_loader.py → connect()`:
1. `ORACLE_USER` and `ORACLE_DSN` from the environment (falls back to the persisted user-level value on Windows).
2. Password from `ORACLE_PASSWORD` **only if** it is set. Otherwise from Credential Manager.
3. Connects with `python-oracledb` in **thin mode** (no Oracle Instant Client needed).

To change the stored password: `.venv\Scripts\python -m keyring set hrra_oracle HRRA`.
Never put passwords in `.env` files that could be committed. `.env` is git-ignored, but Credential Manager is preferred.

## 3. Run everything from Python

```powershell
.venv\Scripts\python -m src.oracle_loader --check   # prints version, container and user
.venv\Scripts\python -m src.oracle_loader --all     # create tables, load, run QC + analysis SQL
```
Results go to `reports/sql_results/<script>__<query_name>.csv`, with an execution log in `_execution_log.csv`.

Load order and row counts (verified):

| Table | Rows |
|---|---|
| `STG_HRRP_HOSPITAL` (raw text, as published) | 18,330 |
| `DIM_MEASURE` | 6 |
| `DIM_HOSPITAL` | 3,055 |
| `FACT_HOSPITAL_PAYMENT` | 2,937 |
| `FACT_READMISSION_MEASURE` | 18,330 |

All rows passed the primary key, foreign key and CHECK constraints on insert.

## 4. Run in Oracle SQL Developer (manual)

1. **New Connection:** Name `HRRA@FREEPDB1`, Username `HRRA`, Password (tick *Save Password* only on a
   personal machine), Hostname `localhost`, Port `1521`, **Service name** `FREEPDB1`. Click *Test*, then *Connect*.
2. Open `sql/create_tables.sql` and run it as a script (**F5**). It is re-runnable because it drops project objects first.
3. Load data with `python -m src.oracle_loader --all`. SQL Developer's *Import Data* wizard also works with
   `data/processed/*.csv`, but the Python loader handles types, `Y/N` flags and dates consistently.
4. Open `sql/data_quality_checks.sql`, `sql/readmission_analysis.sql` and `sql/hospital_rankings.sql`.
   Run a whole file with **F5**, or put the cursor in one query and press **Ctrl+Enter**.

## 5. Manual validation checklist

| Check | Query (`-- @name`) | Expected (verified result) |
|---|---|---|
| Staging = fact row count | `stage_to_fact_reconciliation` | difference 0; 11,720 rows with ERR on both sides |
| No malformed / duplicate CCN rows | `stg_invalid_ccn_violations`, `stg_duplicate_key_violations` | 0 rows |
| Only documented text tokens | `stg_unparseable_value_violations` | 0 rows (`N/A`, `Too Few to Report` are handled) |
| ERR unchanged by cleaning | `err_value_mismatch_violations` | 0 |
| ERR = predicted ÷ expected | `err_definition_violations` | 0 rows |
| Penalty flag follows CMS rule | `penalty_rule_consistency` | only 4 `N/Y` rows, all exact ties at 4 decimals |
| Factor vs reduction % | `payment_factor_pct_violations` | 0 rows |
| Payment formula reproduces official value | `payment_reconstruction_check` | 2,937 / 2,937 within 0.01 pp (max 0.0063) |

## 6. Issues found and resolved during execution

* **Literal `N/A` text in the raw file.** The staging table keeps CMS values exactly as published. The first
  version of `err_value_mismatch_violations` failed with ORA-01722 (`N/A` cannot be converted to a number).
  Fixed with `TO_NUMBER(... DEFAULT NULL ON CONVERSION ERROR)`, and the missing-value counts now treat `N/A` as missing.
* **Thousands separators in the Supplemental file.** `penalty_rule_consistency` showed 93 rows where CMS
  had flagged a penalty but eligible discharges were NULL. The cause was 193 values written as `"1,218"`,
  which the Python cleaner had turned into missing values. The cleaner was fixed, a regression test added,
  and the Python penalty-rule test was tightened so that only exact ties are tolerated.

## 7. Removing the database (optional)

Windows Settings → Apps → *Oracle AI Database 26ai Free* → Uninstall, then delete `D:\app\oracle` and
`D:\oracle-installer`. Remove the stored credential with `.venv\Scripts\python -m keyring del hrra_oracle HRRA`.
