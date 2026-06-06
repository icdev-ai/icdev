# CUI // SP-CTI
"""VIZ Studio H5 — deck management: rename/tags, duplicate, delete."""
from __future__ import annotations

import sqlite3

import pytest

from tools.slides import blueprint as bp


@pytest.fixture
def client(tmp_path, monkeypatch):
    from flask import Flask
    db = tmp_path / "slides.db"
    c = sqlite3.connect(str(db))
    c.executescript(
        """CREATE TABLE slides_decks (deck_id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,
            deck_type TEXT, theme TEXT, status TEXT, slide_count INTEGER, pptx_path TEXT,
            error_message TEXT, tags TEXT DEFAULT '', source_types TEXT, created_at TEXT, completed_at TEXT);
        CREATE TABLE slides_slides (slide_id INTEGER PRIMARY KEY AUTOINCREMENT, deck_id INTEGER,
            position INTEGER, slide_type TEXT, title TEXT, bullets TEXT, speaker_notes TEXT,
            image_path TEXT, image_prompt TEXT, chart_json TEXT, table_json TEXT, diagram_json TEXT,
            kpis_json TEXT, dashboard_json TEXT, elements_json TEXT);
        INSERT INTO slides_decks (deck_id,title,deck_type,theme,status,slide_count,source_types,tags)
            VALUES (1,'Original','custom','midnight_executive','completed',2,'[]','');
        INSERT INTO slides_slides (deck_id,position,slide_type,title,bullets,speaker_notes)
            VALUES (1,1,'title','Original','[]','');
        INSERT INTO slides_slides (deck_id,position,slide_type,title,bullets,speaker_notes,elements_json)
            VALUES (1,2,'content','Body','[\"x\"]','','[{\"type\":\"text\",\"x\":0.1,\"y\":0.1,\"w\":0.5,\"h\":0.1,\"payload\":{\"text\":\"hi\"}}]');""")
    c.commit(); c.close()
    monkeypatch.setattr(bp, "_ensure_init", lambda: None)
    monkeypatch.setattr(bp, "_conn", lambda: (lambda cc: (cc.__setattr__("row_factory", sqlite3.Row), cc)[1])(sqlite3.connect(str(db))))
    app = Flask(__name__); app.register_blueprint(bp.slides_bp)
    return app.test_client(), str(db)


def test_rename_and_tags(client):
    c, db = client
    assert c.post("/slides/api/1/rename", json={"title": "Renamed", "tags": "q3, exec"}).get_json()["ok"]
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT title, tags FROM slides_decks WHERE deck_id=1").fetchone()
    conn.close()
    assert row[0] == "Renamed" and row[1] == "q3, exec"


def test_duplicate_deck(client):
    c, db = client
    j = c.post("/slides/api/1/duplicate-deck").get_json()
    assert j["ok"] and j["deck_id"] != 1
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM slides_slides WHERE deck_id=?", (j["deck_id"],)).fetchone()[0]
    title = conn.execute("SELECT title FROM slides_decks WHERE deck_id=?", (j["deck_id"],)).fetchone()[0]
    # slide content (elements) copied
    el = conn.execute("SELECT elements_json FROM slides_slides WHERE deck_id=? AND position=2", (j["deck_id"],)).fetchone()[0]
    conn.close()
    assert n == 2 and "(copy)" in title and el and "hi" in el


def test_delete_deck(client):
    c, db = client
    assert c.delete("/slides/api/1").get_json()["ok"]
    conn = sqlite3.connect(db)
    decks = conn.execute("SELECT COUNT(*) FROM slides_decks").fetchone()[0]
    slides = conn.execute("SELECT COUNT(*) FROM slides_slides WHERE deck_id=1").fetchone()[0]
    conn.close()
    assert decks == 0 and slides == 0
