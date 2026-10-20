# Data Cleaning Decisions

Implementation: `src/data_cleaning.py`. The stage-by-stage row counts from every run are written to
`data/processed/cleaning_log.csv`. The results below are from the run on 2026-10-20.

## Row counts

| Dataset | Stage | Rows |
|---|---|---|
| HRRP hospital | raw | 18,330 |
| | invalid/missing CCN → review | 0 |
| | duplicate hospital × measure × period | 0 |
| | `ANALYZABLE` (ERR published, values in range) | 11,720 |
| | `EXCLUDED_NO_ERR` (CMS published no ERR; footnoted) | 6,610 |
| HRRP supplemental | raw (incl. "End of worksheet" footer) | 2,946 |
| | hospitals | 2,945 |
| | measure rows after wide → long | 17,670 |
| Hospital General Info | raw / kept | 5,419 / 5,419 |
| IPPS Impact File | raw / kept | 3,103 / 3,103 |
| `hospital_dim` | one row per HRRP hospital | 3,055 |

## Join validation

| Left | Right | Matched | Left-only | Match % |
|---|---|---|---|---|
| HRRP (3,055 CCNs) | General Info | 3,035 | 20 | 99.35 |
| HRRP | IPPS Impact | 3,014 | 41 | 98.66 |
| HRRP | Supplemental | 2,937 | 118 | 96.14 |

43 of the 118 hospitals missing from the Supplemental file are in Maryland. CMS excludes Maryland
from HRRP payment adjustment, and its variable description also excludes hospitals not open as of the
October 2025 list. All joins are **left joins on HRRP**, so unmatched hospitals are kept with null
attributes and an `IN_*` flag. They are never dropped silently.

**Cross-file validation:** for 11,523 hospital × measure pairs where both files publish an ERR, the
Provider Data ERR equals the Supplemental ERR in every case (tolerance 0.0001).

## Decisions

| Issue | Decision | Rationale |
|---|---|---|
| CCN type | Read as string; trim and upper-case only | Leading zeros (`010001`) are significant |
| Malformed or missing CCN | Moved to `review_records.csv` with a reason | Never pad, guess or generate identifiers |
| Federal IDs (`01014F`, VA/DoD) | Accepted as valid format | Legitimate CMS IDs; these facilities are not in HRRP anyway |
| `Too Few to Report` readmissions | Value set to missing + `READMISSIONS_SUPPRESSED = True` | Suppressed is not zero |
| Missing ERR (footnotes 1, 5, 7, 29) | `RECORD_STATUS = EXCLUDED_NO_ERR`; **not imputed** | ERR is hospital-specific and risk-adjusted; a national or state mean would fabricate performance |
| Out-of-range values or readmissions > discharges | `RECORD_STATUS = REVIEW` | None occurred in this release |
| Duplicate hospital × measure × period | All copies moved to review | None occurred in this release |
| CMS `.` / `N/A` / `Not Available` | Converted to missing | Documented CMS missing-value tokens |
| Supplemental footer row | Dropped | Not data |
| Teaching status | Derived: `Resident to Bed Ratio > 0` → Teaching | No official yes/no field exists. This is a proxy. |
| `IS_PENALIZED` (hospital level) | Derived: `Payment adjustment factor < 1.0` | Uses the official factor, not an invented flag |
| `PENALTY_INDICATOR` (measure level) | Taken directly from CMS Supplemental file | Official field |
| `EST_EXCESS_READMISSIONS` | Derived: eligible discharges × (predicted − expected) / 100 | An **estimate** for prioritisation, not a CMS count. Computed only for `ANALYZABLE` rows. |

## Imputation and sensitivity

The brief asked for state-level imputation using the national average for the same measure and period.
The state dataset has no readmission rate, and the national rates come from a different period
(07/2023–06/2025 vs 07/2021–06/2024; see `docs/data_sources.md`). **No values were imputed.**
Because nothing was imputed, the "with vs. without imputation" sensitivity comparison is
not applicable. Instead, the analyses compare *all ANALYZABLE rows* with the *subset with ≥25
eligible discharges* (CMS's own reliability threshold) to check that findings are not driven by small hospitals.

`impute_from_reference()` is still implemented and tested so it can be used if a comparable reference
becomes available. It only fills values when measure **and** period match exactly, and it records
`IMPUTED_FLAG`, `IMPUTATION_METHOD`, `IMPUTATION_SOURCE_VALUE` and the original value.
