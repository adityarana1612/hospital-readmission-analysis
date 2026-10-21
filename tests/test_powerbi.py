"""Tests for the Power BI export and the generated PBIP project."""
import json
import re
from pathlib import Path

import pandas as pd
import pytest

from src import powerbi_export as pbi
from src import statistical_analysis as sa

DASH = Path(__file__).resolve().parents[1] / "dashboard"
SM = DASH / "HRRP_Readmissions.SemanticModel" / "definition"
RPT = DASH / "HRRP_Readmissions.Report" / "definition"


@pytest.fixture(scope="module")
def tables(processed_dir):
    mf = pd.read_csv(processed_dir / "measure_fact.csv", dtype={"FACILITY_ID": str})
    dim = pd.read_csv(processed_dir / "hospital_dim.csv", dtype={"FACILITY_ID": str})
    return mf, dim, pbi.build_tables(mf, dim)


def test_export_keys_and_grain(tables):
    mf, dim, t = tables
    assert t["hospital"]["Facility ID"].is_unique and len(t["hospital"]) == len(dim)
    assert len(t["readmission_fact"]) == len(mf)
    assert t["readmission_fact"]["Facility ID"].isin(t["hospital"]["Facility ID"]).all()
    assert set(t["readmission_fact"]["Measure Code"]) == set(t["measure"]["Measure Code"])


def test_export_totals_reconcile(tables):
    mf, dim, t = tables
    contrib = sa.payment_contributions(mf, dim)
    assert t["readmission_fact"]["Payment Reduction Points"].sum() == pytest.approx(contrib["CONTRIB_PCT_POINTS"].sum())
    assert (t["hospital"]["Payment Reduced"] == "Yes").sum() == int(dim["IS_PENALIZED"].eq(True).sum())
    # Est. excess readmissions in Power BI are positive-only by design.
    assert (t["readmission_fact"]["Est Excess Readmissions"].dropna() >= 0).all()


def _model_fields() -> dict[str, set[str]]:
    fields = {}
    for f in (SM / "tables").glob("*.tmdl"):
        text = f.read_text(encoding="utf-8")
        table = re.search(r"^table '?([^'\n]+)'?", text, re.M).group(1)
        names = re.findall(r"^\t(?:measure|column) (?:'([^']+)'|(\S+))", text, re.M)
        fields[table] = {a or b for a, b in names}
    return fields


def _refs(obj):
    if isinstance(obj, dict):
        for kind in ("Column", "Measure"):
            if kind in obj and "Property" in obj[kind]:
                yield obj[kind]["Expression"]["SourceRef"]["Entity"], obj[kind]["Property"]
        for v in obj.values():
            yield from _refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _refs(v)


@pytest.mark.skipif(not RPT.exists(), reason="PBIP not generated - run scripts/build_powerbi_project.py")
def test_every_visual_field_exists_in_model():
    fields = _model_fields()
    missing = set()
    visuals = list(RPT.rglob("visual.json"))
    assert visuals
    for v in visuals:
        for entity, prop in _refs(json.loads(v.read_text(encoding="utf-8"))):
            if prop not in fields.get(entity, set()):
                missing.add(f"{v.parent.name}: {entity}[{prop}]")
    assert not missing, sorted(missing)


@pytest.mark.skipif(not RPT.exists(), reason="PBIP not generated")
def test_pages_metadata_matches_page_folders():
    meta = json.loads((RPT / "pages" / "pages.json").read_text(encoding="utf-8"))
    folders = {p.name for p in (RPT / "pages").iterdir() if p.is_dir()}
    assert set(meta["pageOrder"]) == folders
