# Data Sources

All data comes from official CMS publications. Nothing in this project is synthetic.
The download URLs below were verified on **2026-10-20** through the CMS Provider Data
Catalog metastore API (`https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/<id>`)
and the FY 2026 IPPS Final Rule home page. CMS replaces files on refresh, so URLs can change;
`src/data_ingestion.py` re-resolves Provider Data URLs from the API at run time.

## 1. Hospital Readmissions Reduction Program (provider-level)

| Item | Value |
|---|---|
| Publisher | CMS Provider Data Catalog |
| Dataset ID | `9n3s-kdb3` |
| Landing page | https://data.cms.gov/provider-data/dataset/9n3s-kdb3 |
| File | `FY_2026_Hospital_Readmissions_Reduction_Program_Hospital.csv` |
| Metadata modified / released | 2026-01-26 / 2026-08-13 |
| Performance period | 07/01/2021 – 06/30/2024 (single period, used for FY 2026 payment) |
| Grain | One row per hospital (CCN) × measure |
| Rows inspected | 18,330 (3,055 hospitals × 6 measures); 0 duplicate hospital–measure pairs |

Columns: `Facility Name`, `Facility ID`, `State`, `Measure Name`, `Number of Discharges`,
`Footnote`, `Excess Readmission Ratio`, `Predicted Readmission Rate`, `Expected Readmission Rate`,
`Number of Readmissions`, `Start Date`, `End Date`.

Measures: `READM-30-AMI-HRRP`, `READM-30-CABG-HRRP`, `READM-30-COPD-HRRP`, `READM-30-HF-HRRP`,
`READM-30-HIP-KNEE-HRRP`, `READM-30-PN-HRRP`.

Observed data issues:
- `Excess Readmission Ratio` (ERR), predicted and expected rates are `N/A` in 6,610 rows (footnotes 1, 5, 7; footnote 29 appears on rows that do have an ERR).
- `Number of Readmissions` is `Too Few to Report` in 3,683 rows and `N/A` in 6,610 (suppressed; not zero).
- `Number of Discharges` is `N/A` in 10,088 rows (CMS suppresses it more often than ERR).

## 2. FY 2026 HRRP Supplemental Data File (payment adjustment)

| Item | Value |
|---|---|
| Publisher | CMS, FY 2026 IPPS/LTCH PPS Final Rule |
| Landing page | https://www.cms.gov/medicare/payment/prospective-payment-systems/acute-inpatient-pps/fy-2026-ipps-final-rule-home-page |
| File | https://www.cms.gov/files/zip/fy2026-hospital-readmissions-reduction-program-supplemental-data-file.zip → `FR FY 2026.txt` (tab-delimited) |
| Performance period | 07/01/2021 – 06/30/2024 |
| Grain | One row per hospital (CCN), wide format with per-measure columns |
| Coverage | Open subsection (d) hospitals with ≥1 measure result; **Maryland excluded** |

Key fields (per CMS `Variable Description.txt`): `Hospital CCN`, `Payment adjustment factor`
(0.97–1.00), `Payment reduction percentage` (0–3%), `Dual proportion`, `Peer group assignment` (1–5),
`Neutrality modifier`, and for each of AMI/COPD/HF/pneumonia/CABG/THA-TKA: `Number of eligible
discharges`, `ERR`, `Peer group median ERR`, `Penalty indicator` (Y/N), `DRG payment ratio`.

The **penalty indicator** is an official CMS field. It is `Y` when the hospital has ≥25 eligible
discharges *and* ERR > peer-group median ERR for that measure. This project does not invent a penalty flag.

## 3. Hospital General Information

| Item | Value |
|---|---|
| Dataset ID | `xubh-q36u` — https://data.cms.gov/provider-data/dataset/xubh-q36u |
| Metadata modified / released | 2026-07-22 / 2026-08-13 |
| Grain | One row per hospital (CCN); 5,419 rows; 0 duplicate IDs |

Used fields: `Facility ID`, `State`, `County/Parish`, `Hospital Type`, `Hospital Ownership`,
`Emergency Services`, `Hospital overall rating`. 20 HRRP CCNs are absent from this file.
This is a *current* snapshot, not a 2021–2024 snapshot, so hospital attributes can have changed since the performance period.

## 4. FY 2026 IPPS Final Rule Impact File

| Item | Value |
|---|---|
| File | https://www.cms.gov/files/zip/fy2026-ipps-fr-impact-file.zip → `FY 2026 IPPS Final Rule Impact File.txt` |
| Grain | One row per IPPS hospital |

Used fields: `Provider Number`, `Region` (Census division code), `URGEO` (urban/rural),
`Resident to Bed Ratio` (teaching-intensity proxy), `Beds`, `DSHPCT`, `Ownership Control Type`.
**Teaching status is not published as a yes/no field.** This project derives it from
`Resident to Bed Ratio` > 0 and states that rule wherever it is used.

## 5. Unplanned Hospital Visits – State and National (context only)

| Dataset | ID | Period for READM measures |
|---|---|---|
| Unplanned Hospital Visits – State | `4gkm-5ypv` | 07/01/2023 – 06/30/2025 |
| Unplanned Hospital Visits – National | `cvcs-xecj` | 07/01/2023 – 06/30/2025 |

The **state file has no readmission rates**. It only counts hospitals rated Better / No Different /
Worse / Too Few for each measure. The national file has one rate per measure.

### Why state-level imputation is not performed

The brief suggested imputing a missing *state-level readmission rate* with the national average.
That is not possible here:
1. The state dataset contains no readmission rate, so there is nothing to impute.
2. The national rates cover 2023–2025. HRRP ERR values cover 2021–2024. These are different
   periods and different metrics (raw rate vs. risk-standardised ratio), so substituting one for the
   other would be invalid.

Missing hospital-level ERRs are **left missing** and flagged (see `docs/data_cleaning_decisions.md`).
The imputation helper in `src/data_cleaning.py` is kept and unit-tested. It only fills a
value when the period and the measure match exactly, and it records `IMPUTED_FLAG`, the method and
the source value.

## Availability matrix

| Concept requested | Available? | Where |
|---|---|---|
| Hospital type | Yes | General Information |
| Ownership | Yes | General Information; Impact File |
| Teaching status | Proxy only | Impact File `Resident to Bed Ratio` |
| Demographics | Proxy only | Supplemental `Dual proportion` (share of dual-eligible stays); Impact `DSHPCT` |
| Geography | Yes | State, Census region, urban/rural |
| Clinical conditions | Yes | 6 HRRP measures |
| Readmission rates | Yes (predicted/expected, risk-adjusted) | HRRP file |
| Excess readmission ratio | Yes | HRRP file; Supplemental |
| Payment reduction factor | Yes | Supplemental file |
| Actual financial cost | **No** | Not published at hospital level in these files. DRG payment ratio is a weight, not dollars. |
| Multiple reporting periods | **No** in current files | Prior fiscal years' files would have to be added from the CMS archive |

## Join keys

All hospital-level files share the 6-character CMS Certification Number (CCN):
`Facility ID` = `Hospital CCN` = `Provider Number`. CCNs must be read as **strings** so leading zeros
(e.g., `010001`) are preserved.
