# CUI // SP-CTI
"""VIZ Studio H4 — templates, theme switch, and prompt-driven generation plumbing."""
from __future__ import annotations

import sqlite3

import pytest

from tools.slides import templates as tpl
from tools.slides import blueprint as bp


def test_list_and_build_templates():
    keys = {t["key"] for t in tpl.list_deck_templates()}
    assert {"blank", "pitch", "status", "comparison", "briefing"} <= keys
    pitch = tpl.build_from_template("pitch")
    assert len(pitch) == 6
    assert pitch[0]["slide_type"] == "title"
    assert pitch[-1]["slide_type"] == "outro"
    assert tpl.build_from_template("nope") == []


def test_orchestrator_brief_path():
    # With a brief and no LLM, plan_outline still returns a (static) outline; with
    # a brief the general prompt is selected (no exception, returns list).
    from tools.slides import orchestrator
    out = orchestrator.plan_outline(raw_content={}, deck_title="X", brief="A deck about coffee brewing")
    assert isinstance(out, list) and len(out) >= 2


def test_content_agent_brief_arg():
    from tools.slides import content_agent
    slides = content_agent.generate_all(["Cover", "Body", "Close"], {}, brief="coffee brewing")
    assert len(slides) == 3
    # title/outro use generic (non-ICDEV) notes when a brief is present
    assert "ICDEV" not in slides[0].get("speaker_notes", "")


@pytest.fixture
def client(tmp_path, monkeypatch):
    from flask import Flask
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
            VALUES (1,'D','custom','midnight_executive','completed',1,'[]');""")
    c.commit(); c.close()
    monkeypatch.setattr(bp, "_ensure_init", lambda: None)
    monkeypatch.setattr(bp, "_conn", lambda: (lambda cc: (cc.__setattr__("row_factory", sqlite3.Row), cc)[1])(sqlite3.connect(str(db))))
    app = Flask(__name__); app.register_blueprint(bp.slides_bp)
    return app.test_client(), str(db)


def test_template_new_route(client):
    c, db = client
    resp = c.post("/slides/api/templates/new", json={"template": "pitch", "theme": "govcon_proposal"})
    j = resp.get_json()
    assert resp.status_code == 200 and j["ok"] and j["deck_id"]
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM slides_slides WHERE deck_id=?", (j["deck_id"],)).fetchone()[0]
    theme = conn.execute("SELECT theme FROM slides_decks WHERE deck_id=?", (j["deck_id"],)).fetchone()[0]
    conn.close()
    assert n == 6 and theme == "govcon_proposal"


def test_theme_switch_route(client):
    c, db = client
    assert c.post("/slides/api/1/theme", json={"theme": "compliance_briefing"}).get_json()["ok"]
    conn = sqlite3.connect(db)
    theme = conn.execute("SELECT theme FROM slides_decks WHERE deck_id=1").fetchone()[0]
    conn.close()
    assert theme == "compliance_briefing"
    assert c.post("/slides/api/1/theme", json={"theme": "bogus"}).status_code == 400