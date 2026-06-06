# CUI // SP-CTI
"""Tests for VIZ Epic C — web-native presenter route + image-serve bug fix."""
from __future__ import annotations

import sqlite3

import pytest

from tools.slides import blueprint as bp
from tools.slides import pptx_builder

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)


@pytest.fixture
def seeded_db(tmp_path):
    """A temp sqlite slides DB with one completed deck (chart + content slides)."""
    db = tmp_path / "slides.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE slides_decks (deck_id INTEGER PRIMARY KEY, title TEXT, deck_type TEXT,
            theme TEXT, status TEXT, slide_count INTEGER, pptx_path TEXT, error_message TEXT,
            created_at TEXT, completed_at TEXT, source_types TEXT);
        CREATE TABLE slides_slides (slide_id INTEGER PRIMARY KEY, deck_id INTEGER, position INTEGER,
            slide_type TEXT, title TEXT, bullets TEXT, speaker_notes TEXT, image_path TEXT,
            image_prompt TEXT, chart_json TEXT, table_json TEXT, diagram_json TEXT, kpis_json TEXT);
        INSERT INTO slides_decks VALUES (1,'VIZ Demo','executive_overview','midnight_executive',
            'completed',3,'/x.pptx',NULL,'now','now','[]');
        INSERT INTO slides_slides VALUES (1,1,1,'title','VIZ Demo','[]','',NULL,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO slides_slides VALUES (2,1,2,'data','Completion','[]','notes here',NULL,NULL,
            '{"kind":"chart","title":"Completion","chart_type":"bar","categories":["A","B"],"series":[{"name":"pct","values":[80,40]}],"unit":"%","max_value":null}',
            NULL,NULL,NULL);
        INSERT INTO slides_slides VALUES (3,1,3,'content','Summary','["Point one","Point two"]','',NULL,NULL,NULL,NULL,NULL,NULL);
        """
    )
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture
def client(seeded_db, monkeypatch):
    from flask import Flask

    monkeypatch.setattr(bp, "_ensure_init", lambda: None)

    def _fresh_conn():
        c = sqlite3.connect(seeded_db)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(bp, "_conn", _fresh_conn)

    app = Flask(__name__)
    app.register_blueprint(bp.slides_bp)
    return app.test_client()


def test_present_renders_chart_and_content(client):
    resp = client.get("/slides/1/present")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "VIZ Demo" in html
    # Interactive presenter: deck model embedded as JSON, rendered client-side.
    assert "window.__DECK" in html
    assert "/static/js/charts.js" in html      # interactive chart lib
    assert "/static/js/viz_story.js" in html   # storytelling runtime
    assert "Completion" in html                # chart spec serialized in __DECK
    assert "Point one" in html                 # content bullets serialized in __DECK
    assert "SPEAKER NOTES" in html


def test_present_deck_model_has_chart_and_insight(client):
    """The embedded deck model carries the chart spec the runtime will render."""
    import json as _json
    import re
    html = client.get("/slides/1/present").get_data(as_text=True)
    m = re.search(r"window\.__DECK = (\{.*?\});", html, re.S)
    assert m, "deck model JSON must be embedded"
    deck = _json.loads(m.group(1).replace("<\\/", "</"))
    types = [s["type"] for s in deck["slides"]]
    assert "chart" in types
    chart_slide = next(s for s in deck["slides"] if s["type"] == "chart")
    assert chart_slide["chart"]["chart_type"] == "bar"
    assert deck["colors"]["accent"].startswith("#")


def test_image_route_rejects_traversal(client, monkeypatch, tmp_path):
    monkeypatch.setattr(pptx_builder, "_OUTPUT_DIR", tmp_path)
    resp = client.get("/slides/api/image", query_string={"path": "/etc/passwd"})
    assert resp.status_code == 403


def test_image_route_serves_png(client, monkeypatch, tmp_path):
    monkeypatch.setattr(pptx_builder, "_OUTPUT_DIR", tmp_path)
    img = tmp_path / "images"
    img.mkdir()
    f = img / "viz_test.png"
    f.write_bytes(_PNG)
    resp = client.get("/slides/api/image", query_string={"path": str(f)})
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"


def test_image_route_missing_file(client, monkeypatch, tmp_path):
    monkeypatch.setattr(pptx_builder, "_OUTPUT_DIR", tmp_path)
    resp = client.get("/slides/api/image", query_string={"path": str(tmp_path / "nope.png")})
    assert resp.status_code == 404


def test_image_route_requires_path(client):
    assert client.get("/slides/api/image").status_code == 400
