# CUI // SP-CTI
"""VIZ Epic F2 — dataset ingestion + auto data-storytelling."""
from __future__ import annotations

from tools.viz import dataset as ds
from tools.viz.story_builder import build_dataset_slides

_CSV = """region,product,units,revenue
West,Widget,10,100
West,Gadget,5,250
East,Widget,8,80
East,Gadget,12,600
North,Widget,3,30
"""

_JSON_RECORDS = '[{"team":"A","score":10},{"team":"B","score":40},{"team":"A","score":20}]'


def test_parse_csv_infers_types():
    d = ds.parse_dataset(text=_CSV, name="Sales")
    assert d["columns"] == ["region", "product", "units", "revenue"]
    assert len(d["rows"]) == 5
    # units/revenue are numeric measures; region/product categorical dimensions
    assert "revenue" in d["measures"] and "units" in d["measures"]
    assert "region" in d["dimensions"] and "product" in d["dimensions"]


def test_parse_json_records():
    d = ds.parse_dataset(text=_JSON_RECORDS, name="Teams")
    assert d["columns"] == ["team", "score"]
    assert len(d["rows"]) == 3
    assert "score" in d["measures"]
    assert "team" in d["dimensions"]


def test_parse_rejects_non_tabular():
    assert ds.parse_dataset(text="") is None
    # single prose line → 1 column, not a useful dataset for the >=2col guard upstream
    d = ds.parse_dataset(text="just some prose here")
    assert d is None or len(d["columns"]) < 2


def _xlsx_bytes(rows):
    import io
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_parse_xlsx_infers_types():
    raw = _xlsx_bytes([["region", "sales"], ["East", 100], ["West", 200], ["East", 50]])
    d = ds.parse_dataset(file_bytes=raw, filename="sales.xlsx", name="Sales")
    assert d["columns"] == ["region", "sales"]
    assert len(d["rows"]) == 3
    assert d["measures"] == ["sales"]
    assert d["dimensions"] == ["region"]
    cats, vals = ds.aggregate(d["rows"], d["columns"], "region", "sales", "sum")
    assert dict(zip(cats, vals)) == {"East": 150.0, "West": 200.0}


def test_parse_xlsm_rejected():
    raw = _xlsx_bytes([["a", "b"], [1, 2]])
    assert ds.parse_dataset(file_bytes=raw, filename="macro.xlsm", name="bad") is None


def test_parse_xlsx_empty_rejected():
    raw = _xlsx_bytes([["a", "b"]])
    assert ds.parse_dataset(file_bytes=raw, filename="empty.xlsx", name="empty") is None


def test_aggregate_sum_and_count():
    d = ds.parse_dataset(text=_CSV, name="Sales")
    cats, vals = ds.aggregate(d["rows"], d["columns"], "region", "revenue", "sum")
    by = dict(zip(cats, vals))
    assert by["West"] == 350.0   # 100 + 250
    assert by["East"] == 680.0   # 80 + 600
    cats2, vals2 = ds.aggregate(d["rows"], d["columns"], "region", None, "count")
    assert dict(zip(cats2, vals2))["West"] == 2


def test_build_dataset_slides_dashboard_and_story():
    d = ds.parse_dataset(text=_CSV, name="Sales")
    slides = build_dataset_slides(d)
    assert slides, "should produce a dashboard + story slides"
    # dashboard slide embeds the dataset + filterable datasetChart tiles
    dash = next(s for s in slides if s.get("dashboard"))["dashboard"]
    assert dash["dataset"]["columns"] == d["columns"]
    assert dash["filters"]                      # at least one filter dimension
    assert any("datasetChart" in t for t in dash["tiles"])
    # a focused chart slide with a computed insight
    chart_slide = next(s for s in slides if s.get("chart"))
    assert "leads with" in chart_slide["insight"]
    # every slide carries an insight (Story Point)
    assert all(s.get("insight") for s in slides)


def test_build_empty_safe():
    assert build_dataset_slides({}) == []
    assert build_dataset_slides({"columns": [], "rows": []}) == []
