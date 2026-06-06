# CUI // SP-CTI
"""VIZ Epic G2/G3 — freeform editor routes: edit page, save elements, image upload."""
from __future__ import annotations

import json
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
    db = tmp_path / "slides.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE slides_decks (deck_id INTEGER PRIMARY KEY, title TEXT, deck_type TEXT,
            theme TEXT, status TEXT, slide_count INTEGER, pptx_path TEXT, error_message TEXT,
            created_at TEXT, completed_at TEXT, source_types TEXT);
        CREATE TABLE slides_slides (slide_id INTEGER PRIMARY KEY, deck_id INTEGER, position INTEGER,
            slide_type TEXT, title TEXT, bullets TEXT, speaker_notes TEXT, image_path TEXT,
            image_prompt TEXT, chart_json TEXT, table_json TEXT, diagram_json TEXT, kpis_json TEXT,
            dashboard_json TEXT, elements_json TEXT);
        INSERT INTO slides_decks VALUES (1,'Edit Demo','executive_overview','midnight_executive',
            'completed',2,'/x.pptx',NULL,'now','now','[]');
        INSERT INTO slides_slides VALUES (1,1,1,'title','Edit Demo','[]','',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL);
        INSERT INTO slides_slides VALUES (2,1,2,'content','Points','["a","b"]','',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL);
        """
    )
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture
def client(seeded_db, monkeypatch):
    from flask import Flask
    monkeypatch.setattr(bp, "_ensure_init", lambda: None)

    def _fresh():
        c = sqlite3.connect(seeded_db)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(bp, "_conn", _fresh)
    app = Flask(__name__)
    app.register_blueprint(bp.slides_bp)
    return app.test_client(), seeded_db


def test_edit_page_renders(client):
    c, _ = client
    resp = c.get("/slides/1/edit")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "viz_editor.js" in html
    assert "window.__DECK" in html
    assert "Edit Demo" in html
    # auto-layout elements present in the model (title slide → text elements)
    assert '"type": "text"' in html or '"type":"text"' in html


def test_save_elements_persists(client):
    c, db = client
    els = [{"id": "t1", "type": "text", "x": 0.2, "y": 0.3, "w": 0.5, "h": 0.1,
            "z": 1, "payload": {"text": "Hello"}, "style": {"fontSize": 30}}]
    resp = c.post("/slides/api/1/elements", json={"slides": [{"position": 1, "elements": els}]})
    assert resp.status_code == 200 and resp.get_json()["ok"]
    # verify persisted
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT elements_json FROM slides_slides WHERE deck_id=1 AND position=1").fetchone()
    conn.close()
    saved = json.loads(row[0])
    assert saved[0]["payload"]["text"] == "Hello"
    assert saved[0]["x"] == 0.2


def test_upload_image_roundtrip(client, monkeypatch, tmp_path):
    c, _ = client
    monkeypatch.setattr(pptx_builder, "_OUTPUT_DIR", tmp_path)
    import io
    resp = c.post("/slides/api/1/upload-image",
                  data={"image": (io.BytesIO(_PNG), "logo.png")},
                  content_type="multipart/form-data")
    assert resp.status_code == 200
    url = resp.get_json()["url"]
    assert url.startswith("/slides/api/image?path=")
    # the served image is fetchable
    img = c.get(url)
    assert img.status_code == 200 and img.mimetype == "image/png"


def test_upload_rejects_non_image(client):
    c, _ = client
    import io
    resp = c.post("/slides/api/1/upload-image",
                  data={"image": (io.BytesIO(b"x"), "evil.exe")},
                  content_type="multipart/form-data")
    assert resp.status_code == 400


# ── H1: slide CRUD + notes ────────────────────────────────────────────────────

def _count(db):
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM slides_slides WHERE deck_id=1").fetchone()[0]
    conn.close()
    return n


def test_slide_add(client):
    c, db = client
    assert _count(db) == 2
    resp = c.post("/slides/api/1/slides/add", json={})
    j = resp.get_json()
    assert resp.status_code == 200 and j["ok"] and j["slide_id"]
    assert _count(db) == 3


def test_slide_duplicate(client):
    c, db = client
    resp = c.post("/slides/api/1/slides/1/duplicate", json={})
    j = resp.get_json()
    assert resp.status_code == 200 and j["ok"]
    assert _count(db) == 3
    conn = sqlite3.connect(db)
    title = conn.execute("SELECT title FROM slides_slides WHERE slide_id=?", (j["slide_id"],)).fetchone()[0]
    conn.close()
    assert "(copy)" in title


def test_slide_delete(client):
    c, db = client
    resp = c.delete("/slides/api/1/slides/2")
    assert resp.status_code == 200 and resp.get_json()["ok"]
    assert _count(db) == 1


def test_slide_reorder(client):
    c, db = client
    resp = c.post("/slides/api/1/slides/reorder", json={"slide_ids": [2, 1]})
    assert resp.status_code == 200 and resp.get_json()["count"] == 2
    conn = sqlite3.connect(db)
    pos = dict(conn.execute("SELECT slide_id, position FROM slides_slides WHERE deck_id=1").fetchall())
    conn.close()
    assert pos[2] == 1 and pos[1] == 2   # swapped


def test_save_elements_with_notes_by_slide_id(client):
    c, db = client
    els = [{"id": "t", "type": "text", "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.1, "payload": {"text": "hi"}}]
    resp = c.post("/slides/api/1/elements",
                  json={"slides": [{"slide_id": 2, "elements": els, "speaker_notes": "my notes"}]})
    assert resp.status_code == 200 and resp.get_json()["ok"]
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT elements_json, speaker_notes FROM slides_slides WHERE slide_id=2").fetchone()
    conn.close()
    assert json.loads(row[0])[0]["payload"]["text"] == "hi"
    assert row[1] == "my notes"
