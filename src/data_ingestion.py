"""Download official CMS files and load them as raw (all-string) DataFrames.

Run as a script to fetch everything into data/raw/:

    python -m src.data_ingestion

Each download is recorded in data/raw/manifest.json (URL, timestamp, SHA-256,
size) so the analysis can always be traced back to the exact source file.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"

PROVIDER_API = "https://data.cms.gov/provider-data/api/1/metastore/schemas/dataset/items/{id}"

# CMS Provider Data Catalog datasets (URLs resolved from the API at run time).
PROVIDER_DATASETS = {
    "hrrp_hospital": "9n3s-kdb3",
    "hospital_general_info": "xubh-q36u",
    "unplanned_visits_state": "4gkm-5ypv",
    "unplanned_visits_national": "cvcs-xecj",
}

# FY 2026 IPPS Final Rule ZIP files: (url, member file inside the zip).
IPPS_FILES = {
    "hrrp_supplemental": (
        "https://www.cms.gov/files/zip/fy2026-hospital-readmissions-reduction-program-supplemental-data-file.zip",
        "FR FY 2026.txt",
    ),
    "ipps_impact": (
        "https://www.cms.gov/files/zip/fy2026-ipps-fr-impact-file.zip",
        "FY 2026 IPPS Final Rule Impact File.txt",
    ),
}

HEADERS = {"User-Agent": "Mozilla/5.0 (hospital-readmission-analysis; educational project)"}

log = logging.getLogger(__name__)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_provider_dataset(dataset_id: str, timeout: int = 60) -> dict:
    """Return title, modified date and CSV download URL for a Provider Data dataset."""
    resp = requests.get(PROVIDER_API.format(id=dataset_id), headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    meta = resp.json()
    urls = [d["downloadURL"] for d in meta.get("distribution", []) if d.get("downloadURL", "").endswith(".csv")]
    if not urls:
        raise RuntimeError(f"No CSV distribution listed for dataset {dataset_id}")
    return {"title": meta.get("title"), "modified": meta.get("modified"),
            "released": meta.get("released"), "url": urls[0]}


def _download(url: str, timeout: int = 300) -> bytes:
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def download_all(raw_dir: Path = RAW_DIR, force: bool = False) -> dict:
    """Download every source file to raw_dir and write manifest.json.

    Existing files are kept unless force=True, so re-running is cheap and the
    analysis stays pinned to the files you already inspected.
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    for name, dataset_id in PROVIDER_DATASETS.items():
        target = raw_dir / f"{name}.csv"
        if target.exists() and not force:
            log.info("skip %s (exists)", target.name)
            continue
        meta = resolve_provider_dataset(dataset_id)
        content = _download(meta["url"])
        target.write_bytes(content)
        manifest[name] = {**meta, "dataset_id": dataset_id, "file": target.name,
                          "sha256": _sha256(content), "bytes": len(content),
                          "downloaded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        log.info("downloaded %s (%d bytes)", target.name, len(content))

    for name, (url, member) in IPPS_FILES.items():
        target = raw_dir / f"{name}.txt"
        if target.exists() and not force:
            log.info("skip %s (exists)", target.name)
            continue
        archive = _download(url)
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            content = zf.read(member)  # read the one member we need; never extract-all untrusted zips
        target.write_bytes(content)
        manifest[name] = {"url": url, "zip_member": member, "file": target.name,
                          "zip_sha256": _sha256(archive), "sha256": _sha256(content), "bytes": len(content),
                          "downloaded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        log.info("downloaded %s from %s", target.name, url)

    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


# ---------------------------------------------------------------------------
# Loaders: every column is read as string so that CCNs keep leading zeros and
# text codes such as "Too Few to Report" are not silently turned into NaN.
# Type conversion happens explicitly in data_cleaning.py.
# ---------------------------------------------------------------------------

def load_csv(name: str, raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Load a Provider Data CSV saved by download_all()."""
    return pd.read_csv(raw_dir / f"{name}.csv", dtype=str, keep_default_na=False, na_values=[""])


def _load_cms_txt(path: Path, encoding: str = "cp1252") -> pd.DataFrame:
    """Load a CMS tab-delimited file whose first line is a title, not a header."""
    df = pd.read_csv(path, sep="\t", skiprows=1, dtype=str, encoding=encoding,
                     keep_default_na=False, na_values=[""])
    df.columns = [c.strip() for c in df.columns]
    df = df.loc[:, ~df.columns.str.startswith("Unnamed")]  # trailing tabs create empty columns
    return df.dropna(how="all")


def load_hrrp_supplemental(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    return _load_cms_txt(raw_dir / "hrrp_supplemental.txt")


def load_ipps_impact(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    return _load_cms_txt(raw_dir / "ipps_impact.txt")


def schema_report(df: pd.DataFrame) -> pd.DataFrame:
    """One row per column: dtype, non-null count, null count, distinct values, example."""
    return pd.DataFrame({
        "dtype": df.dtypes.astype(str),
        "non_null": df.notna().sum(),
        "nulls": df.isna().sum(),
        "distinct": df.nunique(dropna=True),
        "example": df.apply(lambda s: s.dropna().iloc[0] if s.notna().any() else None),
    })


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Download official CMS HRRP source files.")
    parser.add_argument("--force", action="store_true", help="re-download even if files exist")
    manifest = download_all(force=parser.parse_args().force)
    for key, item in manifest.items():
        print(f"{key:28s} {item['bytes']:>12,d} bytes  sha256={item['sha256'][:12]}...")
