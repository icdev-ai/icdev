# CUI // SP-CTI
"""VIZ Studio H6 — PDF export + read-only share link."""
from __future__ import annotations

import sqlite3

import pytest

from tools.slides import blueprint as bp
from tools.slides.pdf_export import build_pdf


def test_build_pdf_freeform_and_simple():
    slides = [
        {"freeform": True, "elements": [
            {"type": "text", "x": 0.1, "y": 0.1, "w": 0.6, "h": 0.2, "z": 1,
             "payload": {"text": "Hello\nWorld"}, "style": {"fontSize": 28, "color": "#FFFFFF", "bold": True}},
            {"type": "shape", "x": 0.1, "y": 0.5, "w": 0.3, "h": 0.2, "z": 2,
             "payload": {"shape": "ellipse"}, "style": {"fill": "#C8A951", "opacity": 0.8}},
            {"type": "kpis", "x": 0.5, "y": 0.5, "w": 0.45, "h": 0.2, "z": 3,
             "payload": {"tiles": [{"value": "42", "label": "Widgets"}, {"value": "9", "label": "Teams"}]}},
        ]},
        {"title": "Plain Slide", "bullets": ["one", "two", "three"]},
    ]
    pdf = build_pdf(slides, title="Test", theme="midnight_executive")
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 800


@pytest.fixture
def client(tmp_path, monkeypatch):
    from flask import Flask
    db = tmp_path / "slides.db"
    c = sqlite3.connect(str(db))
    c.executescript(
        """CREATE TABLE slides_decks (deck_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
            deck_type TEXT, theme TEXT, status TEXT, slide_count INTEGER, pptx_path TEXT,
            error_message TEXT, tags TEXT DEFAULT '', share_token TEXT, source_types TEXT,
            created_at TEXT, completed_at TEXT);
        CREATE TABLE slides_slides (slide_id INTEGER PRIMARY KEY AUTOINCREMENT, deck_id INTEGER,
            position INTEGER, slide_type TEXT, title TEXT, bullets TEXT, speaker_notes TEXT,
            image_path TEXT, image_prompt TEXT, chart_json TEXT, table_json TEXT, diagram_json TEXT,
            kpis_json TEXT, dashboard_json TEXT, elements_json TEXT);
        INSERT INTO slides_decks (deck_id,title,deck_type,theme,status,slide_count,source_types)
            VALUES (1,'Sharable','custom','midnight_executive','completed',1,'[]');
        INSERT INTO slides_slides (deck_id,position,slide_type,title,bullets,speaker_notes)
            VALUES (1,1,'title','Sharable','[]','');""")
    c.commit(); c.close()
    monkeypatch.setattr(bp, "_ensure_init", lambda: None)
    monkeypatch.setattr(bp, "_conn", lambda: (lambda cc: (cc.__setattr__("row_factory", sqlite3.Row), cc)[1])(sqlite3.connect(str(db))))
    app = Flask(__name__); app.register_blueprint(bp.slides_bp)
    return app.test_client(), str(db)


def test_share_token_then_view(client):
    c, db = client
    j = c.post("/slides/api/1/share").get_json()
    assert j["ok"] and j["token"] and j["url"].startswith("/slides/share/")
    # idempotent: second call returns the same token
    assert c.post("/slides/api/1/share").get_json()["token"] == j["token"]
    # the token resolves to a read-only present view
    resp = c.get(j["url"])
    assert resp.status_code == 200
    # unknown token -> 404
    assert c.get("/slides/share/deadbeefdeadbeef").status_code == 404


def test_download_pdf_route(client):
    c, db = client
    resp = c.get("/slides/api/1/download.pdf")
    assert resp.status_code == 200
    assert resp.mimetype == "application/pdf"
    assert resp.data[:5] == b"%PDF-"
