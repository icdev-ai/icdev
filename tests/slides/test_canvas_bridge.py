# CUI // SP-CTI
"""VIZ Epic G4 — canvas → presentation bridge (native pull + capture + aggregate)."""
from __future__ import annotations

import json
import sqlite3

import pytest

from tools.slides import canvas_bridge


def test_graph_to_diagram_slide():
    slide = canvas_bridge.graph_to_diagram_slide(
        {"nodes": [{"id": "a", "label": "Ingest"}, {"id": "b", "label": "Score"}],
         "edges": [{"source": "a", "target": "b", "label": "raw"}]}, "Pipeline")
    assert slide["diagram"]["nodes"][0]["label"] == "Ingest"
    assert slide["elements"]
    assert any(e["type"] == "diagram" for e in slide["elements"])


@pytest.fixture
def fake_source(monkeypatch, tmp_path):
    db = tmp_path / "canvas.db"
    c = sqlite3.connect(str(db))
    c.execute("CREATE TABLE t_designs (id INTEGER PRIMARY KEY, name TEXT, graph_json TEXT)")
    c.execute("INSERT INTO t_designs VALUES (1, 'Net A', ?)",
              (json.dumps({"nodes": [{"id": "n1", "label": "Router"}], "edges": []}),))
    c.execute("INSERT INTO t_designs VALUES (2, 'Net B', ?)",
              (json.dumps({"nodes": [], "edges": []}),))  # empty → skipped on slide build
    c.commit(); c.close()

    monkeypatch.setattr(canvas_bridge, "CANVAS_DESIGN_SOURCES",
                        {"test": {"module": "x", "table": "t_designs", "name": "Test Canvas"}})

    def _conn(key):
        cc = sqlite3.connect(str(db)); cc.row_factory = sqlite3.Row
        return cc
    monkeypatch.setattr(canvas_bridge, "_conn_for", _conn)


def test_list_designs(fake_source):
    designs = canvas_bridge.list_designs()
    names = {d["name"] for d in designs}
    assert names == {"Net A", "Net B"}
    assert all(d["canvas_key"] == "test" for d in designs)


def test_load_graph_and_design_to_slide(fake_source):
    graph = canvas_bridge.load_graph("test", 1)
    assert graph["nodes"][0]["label"] == "Router"
    slide = canvas_bridge.design_to_slide("test", 1)
    assert slide and slide["diagram"]["nodes"][0]["label"] == "Router"
    # empty design → None (no nodes)
    assert canvas_bridge.design_to_slide("test", 2) is None


def test_build_overview_slides(fake_source):
    slides = canvas_bridge.build_overview_slides()
    assert slides[0]["slide_type"] == "title"
    assert slides[-1]["slide_type"] == "outro"
    # one diagram slide for the non-empty design
    assert any(s.get("diagram") for s in slides)


# ── capture + aggregate endpoints ─────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    from flask import Flask
    from tools.slides import blueprint as bp

    db = tmp_path / "slides.db"
    c = sqlite3.connect(str(db))
    c.executescript(
        """CREATE TABLE slides_decks (deck_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
            deck_type TEXT, theme TEXT, status TEXT, slide_count INTEGER, pptx_path TEXT,
            error_message TEXT, created_at TEXT, completed_at TEXT, source_types TEXT);
        CREATE TABLE slides_slides (slide_id INTEGER PRIMARY KEY AUTOINCREMENT, deck_id INTEGER,
            position INTEGER, slide_type TEXT, title TEXT, bullets TEXT, speaker_notes TEXT,
            image_path TEXT, image_prompt TEXT, chart_json TEXT, table_json TEXT, diagram_json TEXT,
            kpis_json TEXT, dashboard_json TEXT, elements_json TEXT);
        INSERT INTO slides_decks (deck_id,title,deck_type,theme,status,slide_count,source_types)
            VALUES (1,'Deck','executive_overview','midnight_executive','completed',1,'[]');
        INSERT INTO slides_slides (deck_id,position,slide_type,title,bullets,speaker_notes)
            VALUES (1,1,'title','Deck','[]','');""")
    c.commit(); c.close()
    monkeypatch.setattr(bp, "_ensure_init", lambda: None)
    monkeypatch.setattr(bp, "_conn", lambda: (lambda cc: (cc.__setattr__("row_factory", sqlite3.Row), cc)[1])(sqlite3.connect(str(db))))
    app = Flask(__name__); app.register_blueprint(bp.slides_bp)
    return app.test_client(), str(db)


def test_capture_native_graph(client):
    c, db = client
    resp = c.post("/slides/api/1/capture", json={
        "graph_json": {"nodes": [{"id": "a", "label": "A"}], "edges": []}, "title": "Imported"})
    assert resp.status_code == 200 and resp.get_json()["ok"]
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT title, diagram_json, elements_json FROM slides_slides WHERE deck_id=1 AND position=2").fetchone()
    conn.close()
    assert row[0] == "Imported" and row[1] and row[2]


def test_capture_image_fallback(client, monkeypatch, tmp_path):
    from tools.slides import pptx_builder
    monkeypatch.setattr(pptx_builder, "_OUTPUT_DIR", tmp_path)
    c, db = client
    # 1x1 png data URL
    png_b64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
               "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
    resp = c.post("/slides/api/1/capture", json={
        "kind": "image", "image_data": "data:image/png;base64," + png_b64, "title": "Snapshot"})
    assert resp.status_code == 200 and resp.get_json()["ok"]
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT elements_json FROM slides_slides WHERE deck_id=1 AND position=2").fetchone()
    conn.close()
    els = json.loads(row[0])
    assert any(e["type"] == "image" for e in els)


def test_aggregate_canvases(client, fake_source):
    c, db = client
    resp = c.post("/slides/api/aggregate-canvases", json={"title": "Overview"})
    assert resp.status_code == 200
    j = resp.get_json()
    assert j["ok"] and j["deck_id"] and j["slides"] >= 2
