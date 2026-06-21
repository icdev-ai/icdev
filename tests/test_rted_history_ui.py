# CUI // SP-CTI
"""UI tests for the History button and timeline panel in doc_detail.html (rted-hist-03).

Verifies:
  - 🕒 History button is rendered in each section action row
  - History panel contains a Close button
  - JS contains _relTime helper for relative timestamps
  - JS renders 'No edit history yet.' for empty history
  - JS renders char_delta badge (green for +, red for -)
  - Timeline entries show editor name, relative time, char_delta, diff_summary
  - Clicking History shows panel; clicking again collapses (toggle logic present)
"""
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Template source path ──────────────────────────────────────────────────────

_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "tools" / "dashboard" / "templates" / "document_intelligence" / "doc_detail.html"
)


@pytest.fixture()
def template_src() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


# ── SQLite shim ───────────────────────────────────────────────────────────────

class _ShimConn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        return _ShimCursor(self._db.execute(sql.replace("%s", "?"), params))

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.commit()


class _ShimCursor:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS dic_edit_history (
    edit_id        TEXT PRIMARY KEY,
    section_id     TEXT NOT NULL,
    doc_id         TEXT,
    version_id     TEXT,
    editor         TEXT NOT NULL,
    content_before TEXT,
    content_after  TEXT,
    char_delta     INTEGER,
    diff_summary   TEXT,
    edited_at      TEXT NOT NULL,
    tenant_id      TEXT,
    classification TEXT DEFAULT 'CUI'
);
"""

_SEC_ID = "sec-ui-hist-001"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "hist_ui.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def app(db):
    flask_app = Flask(
        __name__,
        template_folder=str(
            Path(__file__).parent.parent / "tools" / "dashboard" / "templates"
        ),
    )
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"

    db_path = str(db)

    def _make_shim():
        return _ShimConn(sqlite3.connect(db_path))

    @contextmanager
    def _fake_get_connection():
        c = _make_shim()
        try:
            yield c
        finally:
            c.commit()

    patches = [
        patch("tools.document_intelligence.blueprint._conn", _make_shim),
        patch("tools.document_intelligence.history_recorder.get_connection", _fake_get_connection),
    ]
    for p in patches:
        p.start()

    from tools.document_intelligence.blueprint import dic_bp
    flask_app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
    yield flask_app

    for p in patches:
        p.stop()


@pytest.fixture()
def client(app):
    return app.test_client()


def _insert_history(db_path: str, section_id: str, entries: list) -> None:
    conn = sqlite3.connect(db_path)
    for e in entries:
        conn.execute(
            "INSERT INTO dic_edit_history "
            "(edit_id, section_id, editor, char_delta, diff_summary, edited_at) "
            "VALUES (?,?,?,?,?,?)",
            (e["edit_id"], section_id, e["editor"], e["char_delta"],
             e.get("diff_summary", ""), e["edited_at"]),
        )
    conn.commit()
    conn.close()


# ── Template source tests (read file directly — no Flask render needed) ───────

def test_history_button_rendered_with_clock_emoji(template_src):
    """Each section action row must contain a '🕒 History' button."""
    assert "🕒 History" in template_src


def test_history_button_calls_toggle_function(template_src):
    """The History button must invoke toggleHistoryPanel with the section id."""
    assert "toggleHistoryPanel(" in template_src
    assert "s.section_id" in template_src


def test_history_panel_rendered_hidden(template_src):
    """History panel div must be present but hidden (display:none) by default."""
    assert 'id="hist-panel-' in template_src
    # panel starts hidden
    panel_idx = template_src.find('id="hist-panel-')
    panel_snippet = template_src[panel_idx - 100:panel_idx + 200]
    assert "display:none" in panel_snippet


def test_history_panel_has_close_button(template_src):
    """History panel must contain a Close button that triggers toggleHistoryPanel."""
    panel_idx = template_src.find('id="hist-panel-')
    assert panel_idx != -1
    panel_snippet = template_src[panel_idx:panel_idx + 700]
    assert "Close" in panel_snippet
    assert "toggleHistoryPanel(" in panel_snippet


def test_history_list_container_rendered(template_src):
    """hist-list-<section_id> container must be present in the panel."""
    assert 'id="hist-list-' in template_src


def test_history_count_badge_rendered(template_src):
    """hist-count-<section_id> badge span must be rendered inside the button."""
    assert 'id="hist-count-' in template_src


# ── JavaScript logic tests (check JS source in template) ─────────────────────

def test_js_contains_relative_time_helper(template_src):
    """Template must define _relTime() for human-readable relative timestamps."""
    assert "function _relTime(" in template_src
    assert "ago" in template_src


def test_js_toggle_shows_panel_on_first_click(template_src):
    """toggleHistoryPanel must show panel when display===none."""
    assert "panel.style.display = 'block'" in template_src
    assert "loadSectionHistory(sid)" in template_src


def test_js_toggle_hides_panel_on_second_click(template_src):
    """toggleHistoryPanel must set display none when panel is visible."""
    assert "panel.style.display = 'none'" in template_src


def test_js_empty_history_message(template_src):
    """JS must show 'No edit history yet.' when no entries returned."""
    assert "No edit history yet." in template_src


def test_js_positive_delta_badge_green(template_src):
    """JS badge for positive char_delta must use green color (#4ade80)."""
    assert "#4ade80" in template_src


def test_js_negative_delta_badge_red(template_src):
    """JS badge for negative char_delta must use red color (#f87171)."""
    assert "#f87171" in template_src


def test_js_diff_summary_rendered_in_pre_block(template_src):
    """JS must render diff_summary inside a <pre> monospace block."""
    assert "e.diff_summary" in template_src
    js_section = template_src[template_src.find("loadSectionHistory"):]
    assert "<pre" in js_section


# ── API integration: history endpoint feeds the UI ────────────────────────────

def test_history_api_returns_3_entries_for_3_edits(client, db):
    """GIVEN a section with 3 recorded edits WHEN GET /api/sections/<id>/history
    THEN 3 entries are returned with editor, edited_at, char_delta, diff_summary."""
    base = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    _insert_history(str(db), _SEC_ID, [
        {"edit_id": "eh-ui-01", "editor": "alice", "char_delta": 42,
         "diff_summary": "+added line",
         "edited_at": (base + timedelta(minutes=0)).isoformat()},
        {"edit_id": "eh-ui-02", "editor": "bob", "char_delta": -10,
         "diff_summary": "-removed line",
         "edited_at": (base + timedelta(minutes=1)).isoformat()},
        {"edit_id": "eh-ui-03", "editor": "alice", "char_delta": 5,
         "diff_summary": "+minor fix",
         "edited_at": (base + timedelta(minutes=2)).isoformat()},
    ])

    r = client.get(f"/document-intelligence/api/sections/{_SEC_ID}/history")
    assert r.status_code == 200
    data = r.get_json()
    assert data["count"] == 3
    assert len(data["history"]) == 3

    entry_editors = {e["editor"] for e in data["history"]}
    assert "alice" in entry_editors
    assert "bob" in entry_editors

    for entry in data["history"]:
        assert "editor" in entry
        assert "edited_at" in entry
        assert "char_delta" in entry
        assert "diff_summary" in entry


def test_history_api_returns_empty_for_section_with_no_edits(client):
    """GIVEN a section with no edits WHEN GET history THEN empty list returned."""
    r = client.get("/document-intelligence/api/sections/sec-no-history-at-all/history")
    assert r.status_code == 200
    data = r.get_json()
    assert data["history"] == []
    assert data["count"] == 0


def test_history_entries_ordered_newest_first(client, db):
    """Timeline entries must be newest-first (descending edited_at)."""
    base = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    _insert_history(str(db), _SEC_ID, [
        {"edit_id": "eh-ord-01", "editor": "u1", "char_delta": 1,
         "edited_at": (base + timedelta(minutes=0)).isoformat()},
        {"edit_id": "eh-ord-02", "editor": "u2", "char_delta": 2,
         "edited_at": (base + timedelta(minutes=5)).isoformat()},
        {"edit_id": "eh-ord-03", "editor": "u3", "char_delta": 3,
         "edited_at": (base + timedelta(minutes=10)).isoformat()},
    ])

    r = client.get(f"/document-intelligence/api/sections/{_SEC_ID}/history")
    data = r.get_json()
    timestamps = [e["edited_at"] for e in data["history"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_history_entries_do_not_expose_raw_content(client, db):
    """content_before and content_after must never appear in API response entries."""
    _insert_history(str(db), _SEC_ID, [
        {"edit_id": "eh-priv-01", "editor": "u1", "char_delta": 10,
         "edited_at": datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()},
    ])
    r = client.get(f"/document-intelligence/api/sections/{_SEC_ID}/history")
    data = r.get_json()
    for entry in data["history"]:
        assert "content_before" not in entry
        assert "content_after" not in entry
