# Hospital Readmission Driver Analysis (CMS HRRP, FY 2026)

> Work in progress. This README is completed in the final phase. Only verified results will be added.

Analysis of the CMS Hospital Readmissions Reduction Program (HRRP) to find which **available**
hospital characteristics, clinical measures and geographic factors are *associated with* higher
30-day excess readmission ratios and HRRP payment reductions.

The data is aggregated at hospital level, so findings describe associations between hospitals,
not patient-level causes.

- Data sources and availability: [docs/data_sources.md](docs/data_sources.md)
- Cleaning decisions and row counts: [docs/data_cleaning_decisions.md](docs/data_cleaning_decisions.md)

## Quick start (Windows)

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m src.data_ingestion     # download official CMS files -> data/raw
.venv\Scripts\python -m src.data_cleaning      # validate & build data/processed
.venv\Scripts\python -m pytest -q
```
