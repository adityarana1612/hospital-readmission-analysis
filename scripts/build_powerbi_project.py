"""Generate the Power BI Project (PBIP) in dashboard/ from code.

    python scripts/build_powerbi_project.py

Output (text-based, diff-friendly, opened directly by Power BI Desktop):
  dashboard/HRRP_Readmissions.pbip
  dashboard/HRRP_Readmissions.SemanticModel/   TMDL: tables, relationships, DAX measures
  dashboard/HRRP_Readmissions.Report/          PBIR: pages, visuals, theme

Data comes from data/powerbi/*.csv (python -m src.powerbi_export). The CSV folder
is a model parameter (DataFolder) that can be changed in Power BI Desktop via
Transform data > Edit parameters.

Schema versions are pinned to releases from early/mid 2026 so that Power BI
Desktop builds from that period onward can open the project.
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.powerbi_export import POWERBI_DIR  # noqa: E402

NAME = "HRRP_Readmissions"
DASH = ROOT / "dashboard"
SM, RPT = DASH / f"{NAME}.SemanticModel", DASH / f"{NAME}.Report"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric"
V_REPORT, V_PAGE, V_VISUAL, V_PAGES, V_VERSION = "3.1.0", "2.0.0", "2.7.0", "1.0.0", "1.0.0"
NS = uuid.UUID("6f1c0e52-3b8e-4a8c-9d0e-2b6a1c7e5f10")

# Validated categorical palette (see src/visualizations.py) + ink colours.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
INK, INK_2, MUTED, SURFACE, GRID = "#0b0b0b", "#52514e", "#898781", "#fcfcfb", "#e1e0d9"


def tag(*parts: str) -> str:
    """Deterministic lineage tag so regenerating the project produces no diff."""
    return str(uuid.uuid5(NS, "/".join(parts)))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def write_json(path: Path, obj) -> None:
    write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# =============================================================================
# Semantic model (TMDL)
# =============================================================================
# (column name, TMDL dataType, M type, summarizeBy, formatString or None)
HOSPITAL_COLS = [
    ("Facility ID", "string", "type text", "none", None),
    ("Hospital", "string", "type text", "none", None),
    ("State", "string", "type text", "none", None),
    ("City", "string", "type text", "none", None),
    ("Census Division", "string", "type text", "none", None),
    ("Urban/Rural", "string", "type text", "none", None),
    ("Teaching Status", "string", "type text", "none", None),
    ("Ownership", "string", "type text", "none", None),
    ("Hospital Type", "string", "type text", "none", None),
    ("Bed Size Group", "string", "type text", "none", None),
    ("Beds", "int64", "Int64.Type", "none", "#,0"),
    ("Star Rating", "int64", "Int64.Type", "none", "0"),
    ("Has Payment Factor", "string", "type text", "none", None),
    ("Payment Adjustment Factor", "double", "type number", "none", "0.0000"),
    ("Payment Reduction %", "double", "type number", "none", '0.00"%"'),
    ("Payment Reduced", "string", "type text", "none", None),
    ("Dual Proportion", "double", "type number", "none", "0.000"),
    ("Peer Group", "int64", "Int64.Type", "none", "0"),
    ("Measures Flagged", "int64", "Int64.Type", "none", "0"),
]
MEASURE_COLS = [
    ("Measure Code", "string", "type text", "none", None),
    ("Measure", "string", "type text", "none", None),
    ("Measure Full Name", "string", "type text", "none", None),
    ("Measure Sort", "int64", "Int64.Type", "none", "0"),
]
FACT_COLS = [
    ("Facility ID", "string", "type text", "none", None),
    ("Measure Code", "string", "type text", "none", None),
    ("Period Start", "dateTime", "type date", "none", "yyyy-mm-dd"),
    ("Period End", "dateTime", "type date", "none", "yyyy-mm-dd"),
    ("Reporting Period", "string", "type text", "none", None),
    ("Record Status", "string", "type text", "none", None),
    ("ERR", "double", "type number", "none", "0.0000"),
    ("Predicted Rate", "double", "type number", "none", "0.00"),
    ("Expected Rate", "double", "type number", "none", "0.00"),
    ("Discharges", "int64", "Int64.Type", "none", "#,0"),
    ("Eligible Discharges", "int64", "Int64.Type", "none", "#,0"),
    ("Readmissions", "int64", "Int64.Type", "none", "#,0"),
    ("Peer Median ERR", "double", "type number", "none", "0.0000"),
    ("Penalty Indicator", "string", "type text", "none", None),
    ("Payment Reduction Points", "double", "type number", "none", "0.000"),
    ("Est Excess Readmissions", "double", "type number", "none", "#,0.0"),
]

# (name, DAX, format, description) - descriptions show as tooltips in the Fields pane.
FACT_MEASURES = [
    ("Mean ERR", "AVERAGE ( ReadmissionFact[ERR] )", "0.000",
     "Average excess readmission ratio (predicted / expected). 1.000 = as expected for the hospital's case mix."),
    ("Median ERR", "MEDIAN ( ReadmissionFact[ERR] )", "0.000", "Median excess readmission ratio."),
    ("Hospital-Measures with ERR", "COUNT ( ReadmissionFact[ERR] )", "#,0",
     "Hospital x measure rows where CMS published an ERR."),
    ("% ERR Above 1",
     "DIVIDE ( CALCULATE ( COUNT ( ReadmissionFact[ERR] ), ReadmissionFact[ERR] > 1 ), [Hospital-Measures with ERR] )",
     "0.0%", "Share of hospital x measure results with more readmissions than expected."),
    ("Penalty Flags", 'CALCULATE ( COUNTROWS ( ReadmissionFact ), ReadmissionFact[Penalty Indicator] = "Y" )', "#,0",
     "Official CMS penalty indicator = Y (>= 25 eligible discharges and ERR above peer-group median)."),
    ("Eligible Hospital-Measures",
     "CALCULATE ( COUNTROWS ( ReadmissionFact ), ReadmissionFact[Eligible Discharges] >= 25, "
     "NOT ISBLANK ( ReadmissionFact[Penalty Indicator] ) )", "#,0",
     "Hospital x measure rows with >= 25 eligible discharges (can count toward a penalty)."),
    ("Penalty Flag Rate", "DIVIDE ( [Penalty Flags], [Eligible Hospital-Measures] )", "0.0%",
     "Penalty flags / eligible hospital-measures. About 50% by design (peer-median threshold)."),
    ("Total Reduction Points", "SUM ( ReadmissionFact[Payment Reduction Points] )", "#,0.0",
     "Sum of each hospital's payment reduction attributed to measures (CMS formula). Percentage points, NOT dollars."),
    ("Share of Reduction Points",
     "DIVIDE ( [Total Reduction Points], CALCULATE ( [Total Reduction Points], ALLSELECTED ( 'Measure' ) ) )",
     "0.0%", "Measure's share of selected payment-reduction points."),
    ("Cumulative Share of Reduction Points",
     "// ALLSELECTED on the whole table: Measure[Measure] is sorted by Measure Sort, so visuals also\n"
     "// group by Measure Sort, and clearing only Measure[Measure] would leave each row on its own.\n"
     "VAR cur = [Total Reduction Points]\n"
     "RETURN CALCULATE (\n"
     "    VAR tbl = ADDCOLUMNS ( VALUES ( 'Measure'[Measure] ), \"@p\", [Total Reduction Points] )\n"
     "    RETURN DIVIDE ( SUMX ( FILTER ( tbl, [@p] >= cur ), [@p] ), SUMX ( tbl, [@p] ) ),\n"
     "    ALLSELECTED ( 'Measure' ) )",
     "0.0%", "Pareto cumulative share: this measure plus all measures with larger contributions."),
    ("Total Est Excess Readmissions", "SUM ( ReadmissionFact[Est Excess Readmissions] )", "#,0",
     "Derived estimate: readmissions x (1 - 1/ERR) where ERR > 1. Not a CMS count; excludes suppressed counts."),
    ("Share of Est Excess Readmissions",
     "DIVIDE ( [Total Est Excess Readmissions], CALCULATE ( [Total Est Excess Readmissions], ALLSELECTED ( 'Measure' ) ) )",
     "0.0%", "Measure's share of estimated excess readmissions."),
    ("Reporting Periods", "DISTINCTCOUNT ( ReadmissionFact[Reporting Period] )", "0",
     "Number of performance periods in the data. 1 = no trend analysis possible."),
]
HOSPITAL_MEASURES = [
    ("Hospitals", "COUNTROWS ( Hospital )", "#,0", "HRRP hospitals in the current filter."),
    ("Hospitals with Payment Factor", 'CALCULATE ( COUNTROWS ( Hospital ), Hospital[Has Payment Factor] = "Yes" )',
     "#,0", "Hospitals in the FY 2026 HRRP Supplemental File (Maryland excluded by CMS)."),
    ("% Hospitals with Payment Reduction",
     'DIVIDE ( CALCULATE ( COUNTROWS ( Hospital ), Hospital[Payment Reduced] = "Yes" ), [Hospitals with Payment Factor] )',
     "0.0%", "Share of hospitals with an official payment adjustment factor below 1.0000."),
    ("Mean Payment Reduction %", "AVERAGE ( Hospital[Payment Reduction %] )", '0.00"%"',
     "Average FY 2026 HRRP reduction to base operating DRG payments (0-3%)."),
    ("Median Payment Reduction %", "MEDIAN ( Hospital[Payment Reduction %] )", '0.00"%"',
     "Median FY 2026 HRRP payment reduction."),
    ("Hospitals at 3% Cap", "CALCULATE ( COUNTROWS ( Hospital ), Hospital[Payment Reduction %] >= 3 )", "#,0",
     "Hospitals at the maximum 3% reduction."),
    ("Hospital Measures Flagged", "MAX ( Hospital[Measures Flagged] )", "0",
     "Number of the six measures with penalty indicator = Y (single hospital)."),
    ("Rank in State",
     "VAR st = SELECTEDVALUE ( Hospital[State] )\n"
     "VAR cur = MAX ( Hospital[Payment Reduction %] )\n"
     "RETURN IF ( HASONEVALUE ( Hospital[Facility ID] ) && NOT ISBLANK ( cur ),\n"
     "    RANKX ( FILTER ( ALL ( Hospital ), Hospital[State] = st && NOT ISBLANK ( Hospital[Payment Reduction %] ) ),\n"
     "            Hospital[Payment Reduction %], cur, DESC, DENSE ) )",
     "0", "Dense rank of the hospital's payment reduction within its state (1 = largest reduction)."),
]


def tmdl_table(name: str, csv: str, cols, measures, extra_col_props: dict | None = None) -> str:
    extra_col_props = extra_col_props or {}
    q = lambda n: f"'{n}'" if not n.replace("_", "").isalnum() else n  # noqa: E731
    lines = [f"table {q(name)}", f"\tlineageTag: {tag(name)}", ""]
    for mname, dax, fmt, desc in measures:
        lines.append(f"\t/// {desc}")
        if "\n" in dax:
            lines.append(f"\tmeasure {q(mname)} = ```")
            lines += [f"\t\t\t{l}" for l in dax.split("\n")]
            lines.append("\t\t\t```")
        else:
            lines.append(f"\tmeasure {q(mname)} = {dax}")
        lines += [f"\t\tformatString: {fmt}", f"\t\tlineageTag: {tag(name, 'm', mname)}", ""]
    for cname, dtype, _, summ, fmt in cols:
        lines += [f"\tcolumn {q(cname)}", f"\t\tdataType: {dtype}"]
        if fmt:
            lines.append(f"\t\tformatString: {fmt}")
        lines += [f"\t\tlineageTag: {tag(name, 'c', cname)}", f"\t\tsummarizeBy: {summ}", f"\t\tsourceColumn: {cname}"]
        lines += [f"\t\t{p}" for p in extra_col_props.get(cname, [])]
        lines += ["", "\t\tannotation SummarizationSetBy = Automatic", ""]
    types = ", ".join(f'{{"{c}", {m}}}' for c, _, m, _, _ in cols)
    m_lines = [
        "let",
        f'    Source = Csv.Document(File.Contents(DataFolder & "\\{csv}"), [Delimiter = ",", Columns = {len(cols)}, '
        'Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),',
        "    Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),",
        '    Blanks = Table.ReplaceValue(Promoted, "", null, Replacer.ReplaceValue, Table.ColumnNames(Promoted)),',
        f'    Typed = Table.TransformColumnTypes(Blanks, {{{types}}}, "en-US")',
        "in",
        "    Typed",
    ]
    lines += [f"\tpartition {q(name)} = m", "\t\tmode: import", "\t\tsource ="]
    lines += [f"\t\t\t\t{l}" for l in m_lines]
    lines += ["", "\tannotation PBI_ResultType = Table", ""]
    return "\n".join(lines)


def build_semantic_model() -> None:
    d = SM / "definition"
    write_json(SM / "definition.pbism", {
        "$schema": f"{SCHEMA}/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.2", "settings": {}})
    write(d / "database.tmdl", "database\n\tcompatibilityLevel: 1600\n")
    tables = ["Hospital", "Measure", "ReadmissionFact"]
    write(d / "model.tmdl", "\n".join([
        "model Model", "\tculture: en-US", "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tdiscourageImplicitMeasures", "\tsourceQueryCulture: en-US", "",
        "\tannotation PBI_QueryOrder = " + json.dumps(["DataFolder"] + tables), "",
        *[f"ref table {t if t != 'Measure' else chr(39) + t + chr(39)}" for t in tables], ""]))
    folder = str(POWERBI_DIR).replace('"', '""')
    write(d / "expressions.tmdl", "\n".join([
        "/// Folder containing hospital.csv, measure.csv and readmission_fact.csv "
        "(created by python -m src.powerbi_export).",
        f'expression DataFolder = "{folder}" meta [IsParameterQuery = true, Type = "Text", IsParameterQueryRequired = true]',
        f"\tlineageTag: {tag('DataFolder')}", "", "\tannotation PBI_ResultType = Text", ""]))
    write(d / "tables" / "Hospital.tmdl", tmdl_table("Hospital", "hospital.csv", HOSPITAL_COLS, HOSPITAL_MEASURES))
    write(d / "tables" / "Measure.tmdl", tmdl_table("Measure", "measure.csv", MEASURE_COLS, [],
                                                    {"Measure": ["sortByColumn: 'Measure Sort'"]}))
    write(d / "tables" / "ReadmissionFact.tmdl", tmdl_table("ReadmissionFact", "readmission_fact.csv",
                                                            FACT_COLS, FACT_MEASURES))
    write(d / "relationships.tmdl", "\n".join([
        f"relationship {tag('rel', 'fact-hospital')}",
        "\tfromColumn: ReadmissionFact.'Facility ID'", "\ttoColumn: Hospital.'Facility ID'", "",
        f"relationship {tag('rel', 'fact-measure')}",
        "\tfromColumn: ReadmissionFact.'Measure Code'", "\ttoColumn: 'Measure'.'Measure Code'", ""]))


# =============================================================================
# Report (PBIR)
# =============================================================================
def lit(v) -> dict:
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, (int, float)):
        s = f"{v}D"
    else:
        s = "'" + str(v).replace("'", "''") + "'"
    return {"expr": {"Literal": {"Value": s}}}


def col(entity: str, prop: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def mea(entity: str, prop: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Entity": entity}}, "Property": prop}}


def proj(field: dict, display: str | None = None) -> dict:
    kind = "Column" if "Column" in field else "Measure"
    e, p = field[kind]["Expression"]["SourceRef"]["Entity"], field[kind]["Property"]
    out = {"field": field, "queryRef": f"{e}.{p}", "nativeQueryRef": p}
    if display:
        out["displayName"] = display
    return out


class Page:
    def __init__(self, name: str, display: str):
        self.name, self.display, self.visuals = name, display, []

    def add(self, vname: str, visual: dict, x, y, w, h, title: str | None = None, subtitle: str | None = None):
        vco = {}
        if title is not None:
            vco["title"] = [{"properties": {"show": lit(True), "text": lit(title), "fontSize": lit(12),
                                            "fontColor": {"solid": {"color": lit(INK)}}}}]
        if subtitle is not None:
            vco["subTitle"] = [{"properties": {"show": lit(True), "text": lit(subtitle), "fontSize": lit(9),
                                               "fontColor": {"solid": {"color": lit(INK_2)}}}}]
        if vco:
            visual = {**visual, "visualContainerObjects": vco}
        self.visuals.append({
            "$schema": f"{SCHEMA}/item/report/definition/visualContainer/{V_VISUAL}/schema.json",
            "name": vname, "position": {"x": x, "y": y, "z": len(self.visuals) * 1000, "width": w, "height": h,
                                        "tabOrder": len(self.visuals) * 1000},
            "visual": visual})


def chart(vtype: str, roles: dict[str, list], sort: tuple[dict, str] | None = None, objects: dict | None = None) -> dict:
    v = {"visualType": vtype,
         "query": {"queryState": {r: {"projections": ps} for r, ps in roles.items()}},
         "drillFilterOtherVisuals": True}
    if sort:
        v["query"]["sortDefinition"] = {"sort": [{"field": sort[0], "direction": sort[1]}], "isDefaultSort": False}
    if objects:
        v["objects"] = objects
    return v


def textbox(paragraphs: list[list[tuple[str, dict]]]) -> dict:
    """paragraphs: list of paragraphs, each a list of (text, textStyle) runs."""
    return {"visualType": "textbox", "objects": {"general": [{"properties": {"paragraphs": [
        {"textRuns": [{"value": t, "textStyle": s} if s else {"value": t} for t, s in runs]}
        for runs in paragraphs]}}]}}


def slicer(field: dict, dropdown: bool = True) -> dict:
    objs = {"data": [{"properties": {"mode": lit("Dropdown" if dropdown else "Basic")}}]}
    return chart("slicer", {"Values": [proj(field)]}, objects=objs)


def card(measure: dict) -> dict:
    return chart("card", {"Values": [proj(measure)]},
                 objects={"labels": [{"properties": {"fontSize": lit(26), "color": {"solid": {"color": lit(INK)}},
                                                     "labelDisplayUnits": lit(1)}}],
                          "categoryLabels": [{"properties": {"show": lit(True), "fontSize": lit(10)}}]})


def bars(vtype: str, category: dict, value: dict, tooltips=(), sort_desc=True, color=PALETTE[0], labels=True) -> dict:
    roles = {"Category": [proj(category)], "Y": [proj(value)]}
    if tooltips:
        roles["Tooltips"] = [proj(t) for t in tooltips]
    objs = {"dataPoint": [{"properties": {"fill": {"solid": {"color": lit(color)}}}}],
            "labels": [{"properties": {"show": lit(labels), "fontSize": lit(9)}}]}
    return chart(vtype, roles, sort=(value, "Descending") if sort_desc else (category, "Ascending"), objects=objs)


H, F, R = "Hospital", "ReadmissionFact", "Measure"
TITLE = {"fontSize": "18pt", "fontWeight": "bold", "color": INK}
SUB = {"fontSize": "10pt", "color": INK_2}
SRC_NOTE = ("Source: CMS HRRP FY 2026 (discharges Jul 2021 - Jun 2024) and FY 2026 HRRP Supplemental File. "
            "Associations only; payment figures are percentages, not dollars.")


def header(p: Page, title: str, subtitle: str) -> None:
    p.add("header", textbox([[(title, TITLE)], [(subtitle, SUB)]]), 16, 8, 1248, 60)
    p.add("footer", textbox([[(SRC_NOTE, {"fontSize": "8pt", "color": MUTED})]]), 16, 692, 1248, 24)


def filters_row(p: Page, y: int = 72, fields=None) -> None:
    fields = fields or [col(H, "State"), col(R, "Measure"), col(H, "Teaching Status"), col(H, "Urban/Rural"),
                        col(H, "Ownership"), col(F, "Reporting Period")]
    w = (1248 - 8 * (len(fields) - 1)) / len(fields)
    for i, f in enumerate(fields):
        p.add(f"slicer_{i}", slicer(f), 16 + i * (w + 8), y, w, 52)


def build_pages() -> list[Page]:
    pages = []

    # ---------------------------------------------------------------- 1 Overview
    p = Page("overview", "Overview")
    header(p, "Hospital Readmissions & HRRP Payment Reductions",
           "FY 2026 program year. Clinical-measure filters apply to readmission metrics; "
           "hospital filters apply to everything.")
    filters_row(p)
    kpis = [(mea(H, "Hospitals"), "HRRP hospitals"),
            (mea(H, "% Hospitals with Payment Reduction"), "Hospitals with a payment reduction"),
            (mea(H, "Median Payment Reduction %"), "Median payment reduction"),
            (mea(F, "Mean ERR"), "Mean excess readmission ratio"),
            (mea(F, "Total Est Excess Readmissions"), "Est. excess readmissions (derived)")]
    w = (1248 - 8 * 4) / 5
    for i, (m, t) in enumerate(kpis):
        p.add(f"kpi_{i}", card(m), 16 + i * (w + 8), 132, w, 96, title=t)
    p.add("bar_points", bars("clusteredBarChart", col(R, "Measure"), mea(F, "Total Reduction Points"),
                             tooltips=[mea(F, "Share of Reduction Points"), mea(F, "Penalty Flags")]),
          16, 240, 616, 444, title="Payment-reduction points by clinical measure",
          subtitle="CMS formula applied per measure; percentage points summed across hospitals (not dollars)")
    p.add("bar_excess", bars("clusteredBarChart", col(R, "Measure"), mea(F, "Total Est Excess Readmissions"),
                             tooltips=[mea(F, "Share of Est Excess Readmissions"), mea(F, "Mean ERR")]),
          648, 240, 616, 444, title="Estimated excess readmissions by clinical measure",
          subtitle="Derived: readmissions x (1 - 1/ERR) where ERR > 1; suppressed counts excluded")
    pages.append(p)

    # ---------------------------------------------------------------- 2 States
    p = Page("states", "State Comparison")
    header(p, "State Comparison", "Mean HRRP payment reduction and readmission performance by state")
    filters_row(p, fields=[col(H, "Census Division"), col(R, "Measure"), col(H, "Teaching Status"),
                           col(H, "Urban/Rural")])
    p.add("col_state", bars("clusteredColumnChart", col(H, "State"), mea(H, "Mean Payment Reduction %"),
                            tooltips=[mea(H, "Hospitals with Payment Factor"),
                                      mea(H, "% Hospitals with Payment Reduction")], labels=False),
          16, 132, 1248, 300, title="Mean payment reduction by state",
          subtitle="States with few hospitals have unstable means - check the hospital count in the tooltip/table")
    p.add("tbl_state", chart("tableEx", {"Values": [
        proj(col(H, "State")), proj(mea(H, "Hospitals with Payment Factor")),
        proj(mea(H, "% Hospitals with Payment Reduction")), proj(mea(H, "Mean Payment Reduction %")),
        proj(mea(F, "Mean ERR")), proj(mea(F, "% ERR Above 1"))]},
        sort=(mea(H, "Mean Payment Reduction %"), "Descending")),
        16, 444, 1248, 240, title="State summary")
    pages.append(p)

    # ---------------------------------------------------------------- 3 Hospital rankings
    p = Page("rankings", "Hospital Rankings")
    header(p, "Hospital Performance Rankings",
           "Hospitals ranked by FY 2026 payment reduction within their state. Use for investigation, not judgement.")
    filters_row(p, fields=[col(H, "State"), col(H, "Teaching Status"), col(H, "Bed Size Group"),
                           col(H, "Payment Reduced")])
    p.add("tbl_hosp", chart("tableEx", {"Values": [
        proj(col(H, "Hospital")), proj(col(H, "State")), proj(mea(H, "Rank in State")),
        proj(mea(H, "Mean Payment Reduction %"), "Payment reduction %"),
        proj(mea(H, "Hospital Measures Flagged"), "Measures flagged"),
        proj(mea(F, "Mean ERR"), "Mean ERR (selected measures)"),
        proj(col(H, "Teaching Status")), proj(col(H, "Bed Size Group"))]},
        sort=(mea(H, "Mean Payment Reduction %"), "Descending"),
        objects={"total": [{"properties": {"totals": lit(False)}}]}),  # a total row of ranks/maxima is meaningless
        16, 132, 824, 552, title="Hospitals by payment reduction")
    p.add("txt_rank", textbox([
        [("How to read this page", {"fontSize": "12pt", "fontWeight": "bold", "color": INK})],
        [("Rank in State: 1 = largest payment reduction in the state (dense rank).", SUB)],
        [("Measures flagged: how many of the six HRRP measures have penalty indicator = Y.", SUB)],
        [("Mean ERR: average excess readmission ratio over the measures with a published ERR.", SUB)],
        [("A high rank flags a hospital for review. It does not prove worse care: small hospitals qualify on "
          "fewer measures, and CMS shrinks small-hospital ERRs toward 1.", SUB)],
    ]), 856, 132, 408, 300)
    p.add("card_cap", card(mea(H, "Hospitals at 3% Cap")), 856, 448, 200, 96, title="Hospitals at 3% cap")
    p.add("card_hpf", card(mea(H, "Hospitals with Payment Factor")), 1064, 448, 200, 96,
          title="Hospitals with a payment factor")
    pages.append(p)

    # ---------------------------------------------------------------- 4 Clinical conditions
    p = Page("conditions", "Clinical Conditions")
    header(p, "Clinical-Condition Comparison", "Readmission performance and penalty flags by HRRP measure")
    filters_row(p, fields=[col(H, "State"), col(H, "Census Division"), col(H, "Teaching Status"),
                           col(H, "Urban/Rural"), col(F, "Reporting Period")])
    p.add("col_err", bars("clusteredColumnChart", col(R, "Measure"), mea(F, "% ERR Above 1"),
                          tooltips=[mea(F, "Mean ERR"), mea(F, "Median ERR"), mea(F, "Hospital-Measures with ERR")],
                          sort_desc=False),
          16, 132, 408, 300, title="Share of hospitals with ERR > 1",
          subtitle="ERR is centred on 1 by design, so ~50% is expected")
    p.add("col_flag", bars("clusteredColumnChart", col(R, "Measure"), mea(F, "Penalty Flag Rate"),
                           tooltips=[mea(F, "Penalty Flags"), mea(F, "Eligible Hospital-Measures")], sort_desc=False),
          436, 132, 408, 300, title="Penalty flag rate (eligible hospitals)",
          subtitle="Flag = ERR above peer-group median with >= 25 eligible discharges")
    p.add("col_share", bars("clusteredColumnChart", col(R, "Measure"), mea(F, "Share of Est Excess Readmissions"),
                            tooltips=[mea(F, "Total Est Excess Readmissions")], sort_desc=False),
          856, 132, 408, 300, title="Share of estimated excess readmissions", subtitle="Derived estimate")
    p.add("mx_region", chart("pivotTable", {"Rows": [proj(col(H, "Census Division"))],
                                            "Columns": [proj(col(R, "Measure"))],
                                            "Values": [proj(mea(F, "Mean ERR"))]}),
          16, 444, 1248, 240, title="Mean ERR by Census division and measure")
    pages.append(p)

    # ---------------------------------------------------------------- 5 Payment reduction
    p = Page("payment", "Payment Reduction")
    header(p, "Payment Reduction Analysis (Pareto)",
           "Which measures and hospitals account for HRRP payment-reduction points")
    filters_row(p, fields=[col(H, "State"), col(H, "Teaching Status"), col(H, "Bed Size Group"),
                           col(H, "Urban/Rural")])
    p.add("pareto_share", bars("clusteredColumnChart", col(R, "Measure"), mea(F, "Share of Reduction Points"),
                               tooltips=[mea(F, "Total Reduction Points")]),
          16, 132, 616, 270, title="Pareto: share of payment-reduction points",
          subtitle="Sorted largest first")
    pareto_line = chart("lineChart", {"Category": [proj(col(R, "Measure"))],
                                      "Y": [proj(mea(F, "Cumulative Share of Reduction Points"))]},
                        # A sort field must be in the visual; cumulative share rises in Pareto order.
                        sort=(mea(F, "Cumulative Share of Reduction Points"), "Ascending"),
                        objects={"dataPoint": [{"properties": {"fill": {"solid": {"color": lit(PALETTE[0])}}}}],
                                 "labels": [{"properties": {"show": lit(True), "fontSize": lit(9)}}],
                                 "lineStyles": [{"properties": {"showMarker": lit(True)}}],
                                 "valueAxis": [{"properties": {"start": lit(0), "end": lit(1.05)}}],
                                 "y1AxisReferenceLine": [{"properties": {
                                     "show": lit(True), "value": lit(0.8), "displayName": lit("80%"),
                                     "lineColor": {"solid": {"color": lit(INK_2)}}, "style": lit("dashed"),
                                     "dataLabelShow": lit(True)}, "selector": {"id": "1"}}]})
    p.add("pareto_cum", pareto_line, 16, 410, 616, 274, title="Pareto: cumulative share",
          subtitle="Same order as above; 80% reference = Pareto threshold")
    p.add("col_flagged", bars("clusteredColumnChart", col(H, "Measures Flagged"), mea(H, "Mean Payment Reduction %"),
                              tooltips=[mea(H, "Hospitals with Payment Factor")], sort_desc=False),
          648, 132, 616, 270, title="Mean payment reduction by number of measures flagged",
          subtitle="Hospitals flagged on more measures carry most of the reduction")
    p.add("col_peer", bars("clusteredColumnChart", col(H, "Peer Group"), mea(H, "% Hospitals with Payment Reduction"),
                           tooltips=[mea(F, "Mean ERR"), mea(H, "Mean Payment Reduction %")], sort_desc=False),
          648, 410, 616, 274, title="Hospitals with a reduction, by dual-eligible peer group",
          subtitle="Peer group 1 = lowest dual-eligible share, 5 = highest")
    pages.append(p)

    # ---------------------------------------------------------------- 6 Notes
    p = Page("notes", "Definitions & Limitations")
    header(p, "Definitions, Sources & Limitations", "Read before interpreting any visual")
    h2 = {"fontSize": "12pt", "fontWeight": "bold", "color": INK}
    body = {"fontSize": "10pt", "color": INK_2}
    p.add("txt_defs", textbox([
        [("Metric definitions", h2)],
        [("ERR (excess readmission ratio): predicted / expected 30-day readmission rate, risk-adjusted by CMS. "
          ">1 = more readmissions than an average hospital with the same patients.", body)],
        [("Penalty flag: official CMS indicator; Y when eligible discharges >= 25 and ERR > peer-group median ERR.", body)],
        [("Payment reduction %: FY 2026 HRRP reduction to base operating DRG payments (official, 0-3%).", body)],
        [("Payment-reduction points: each hospital's official reduction split across measures with the CMS "
          "formula (DRG ratio x (ERR - peer median) x neutrality modifier). Percentage points, not dollars.", body)],
        [("Est. excess readmissions: readmissions x (1 - 1/ERR) where ERR > 1. A derived estimate, not a CMS "
          "count; rows with suppressed counts (mostly small hospitals) are excluded.", body)],
        [("Teaching status: proxy = resident-to-bed ratio > 0 (FY 2026 IPPS Impact File).", body)],
    ]), 16, 76, 616, 500)
    p.add("txt_limits", textbox([
        [("Sources", h2)],
        [("CMS Provider Data: Hospital Readmissions Reduction Program (9n3s-kdb3); Hospital General "
          "Information (xubh-q36u). CMS FY 2026 IPPS Final Rule: HRRP Supplemental File; Impact File.", body)],
        [("Limitations", h2)],
        [("Aggregate hospital-level data: no patient-level causes. All relationships are associations.", body)],
        [("One performance period (Jul 2021 - Jun 2024): trend analysis is not possible until prior-year "
          "files are added. The Reporting Period filter is included for when they are.", body)],
        [("No cost data: no dollar amounts or savings are shown.", body)],
        [("Hospital type: every matched HRRP hospital is an Acute Care Hospital, so hospital-type filtering "
          "uses teaching status, ownership, size and location instead.", body)],
        [("Maryland hospitals have no payment factor (exempt from HRRP payment adjustment).", body)],
        [("Measure filters do not change hospital-level payment KPIs, which are hospital-wide by definition.", body)],
    ]), 648, 76, 616, 500)
    p.add("card_periods", card(mea(F, "Reporting Periods")), 16, 588, 200, 96, title="Reporting periods in data")
    pages.append(p)
    return pages


def theme() -> dict:
    return {
        "name": "HRRA Readmissions", "dataColors": PALETTE, "foreground": INK, "foregroundNeutralSecondary": INK_2,
        "foregroundNeutralTertiary": MUTED, "background": SURFACE, "backgroundLight": "#f9f9f7",
        "tableAccent": PALETTE[0], "good": "#0ca30c", "neutral": "#fab219", "bad": "#d03b3b",
        "maximum": "#184f95", "center": "#f0efec", "minimum": "#b8302f",
        "textClasses": {"label": {"fontFace": "Segoe UI", "color": INK_2},
                        "title": {"fontFace": "Segoe UI Semibold", "color": INK}},
        "visualStyles": {"*": {"*": {
            "background": [{"color": {"solid": {"color": SURFACE}}}],
            "border": [{"show": False}],
            "valueAxis": [{"gridlineColor": {"solid": {"color": GRID}}}],
        }}},
    }


def build_report() -> None:
    write_json(RPT / "definition.pbir", {
        "$schema": f"{SCHEMA}/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    d = RPT / "definition"
    write_json(d / "version.json", {"$schema": f"{SCHEMA}/item/report/definition/versionMetadata/{V_VERSION}/schema.json",
                                    "version": "2.0.0"})
    write_json(RPT / "StaticResources" / "RegisteredResources" / "hrra_theme.json", theme())
    write_json(d / "report.json", {
        "$schema": f"{SCHEMA}/item/report/definition/report/{V_REPORT}/schema.json",
        "themeCollection": {"customTheme": {"name": "hrra_theme.json", "type": "RegisteredResources",
                                            "reportVersionAtImport": {"visual": V_VISUAL, "report": V_REPORT,
                                                                      "page": V_PAGE}}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": "hrra_theme.json", "path": "hrra_theme.json", "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "defaultDrillFilterOtherVisuals": True,
                     "allowChangeFilterTypes": True, "useEnhancedTooltips": True},
    })
    pages = build_pages()
    write_json(d / "pages" / "pages.json", {
        "$schema": f"{SCHEMA}/item/report/definition/pagesMetadata/{V_PAGES}/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[0].name})
    for p in pages:
        write_json(d / "pages" / p.name / "page.json", {
            "$schema": f"{SCHEMA}/item/report/definition/page/{V_PAGE}/schema.json",
            "name": p.name, "displayName": p.display, "displayOption": "FitToPage", "height": 720, "width": 1280})
        for v in p.visuals:
            write_json(d / "pages" / p.name / "visuals" / v["name"] / "visual.json", v)


def main() -> None:
    for folder in (SM, RPT):
        if folder.exists():  # regenerate cleanly but keep Desktop's cache/settings and item metadata
            for child in folder.iterdir():
                if child.name not in (".pbi", ".platform"):
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
    build_semantic_model()
    build_report()
    write_json(DASH / f"{NAME}.pbip", {
        "$schema": f"{SCHEMA}/pbip/pbipProperties/1.0.0/schema.json", "version": "1.0",
        "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}})
    n_vis = sum(1 for _ in (RPT / "definition" / "pages").rglob("visual.json"))
    print(f"wrote {DASH / (NAME + '.pbip')} ({n_vis} visuals)")


if __name__ == "__main__":
    main()
