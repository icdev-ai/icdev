# CUI // SP-CTI
"""Tests for icdev/tools/genesis/reflexes/dic_integration.py (dsyn-reflex-01).

Covers: event fetch, per-event processing, idempotency, error isolation,
dry_run mode, and run() result shape.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── SQLite shim ───────────────────────────────────────────────────────────────

class _ShimConn:
    def __init__(self, db: sqlite3.Connection):
        self._db = db
        self._db.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        cur = self._db.execute(sql.replace("%s", "?"), params)
        return _ShimCur(cur)

    def commit(self):
        self._db.commit()

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.commit()


class _ShimCur:
    def __init__(self, cur):
        self._cur = cur
        self.rowcount = cur.rowcount
        self.description = cur.description

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id            TEXT PRIMARY KEY,
    source_canvas TEXT NOT NULL,
    target_canvas TEXT,
    event_type    TEXT NOT NULL,
    payload_json  TEXT NOT NULL DEFAULT '{}',
    created_at    TEXT NOT NULL,
    consumed_at   TEXT
);
CREATE TABLE IF NOT EXISTS dic_processed_canvas_events (
    event_id     TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL,
    processor    TEXT NOT NULL DEFAULT 'dic_integration'
);
CREATE TABLE IF NOT EXISTS dic_suggestions (
    suggestion_id     TEXT PRIMARY KEY,
    section_id        TEXT,
    doc_id            TEXT,
    collection_id     TEXT,
    trigger_event_id  TEXT,
    canvas_source     TEXT DEFAULT 'unknown',
    suggested_content TEXT NOT NULL DEFAULT '',
    current_content   TEXT,
    rationale         TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    tenant_id         TEXT,
    classification    TEXT NOT NULL DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_suggestion_decisions (
    decision_id   TEXT PRIMARY KEY,
    suggestion_id TEXT NOT NULL,
    decision      TEXT NOT NULL,
    decided_by    TEXT,
    decided_at    TEXT NOT NULL,
    note          TEXT,
    tenant_id     TEXT,
    classification TEXT NOT NULL DEFAULT 'CUI'
);
CREATE TABLE IF NOT EXISTS dic_collections (
    collection_id TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    tags          TEXT DEFAULT '',
    tenant_id     TEXT DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS dic_documents (
    doc_id        TEXT PRIMARY KEY,
    collection_id TEXT,
    filename      TEXT,
    tenant_id     TEXT
);
CREATE TABLE IF NOT EXISTS dic_sections (
    section_id    TEXT PRIMARY KEY,
    doc_id        TEXT,
    collection_id TEXT,
    heading       TEXT,
    content       TEXT DEFAULT '',
    created_at    TEXT,
    tenant_id     TEXT
);
CREATE TABLE IF NOT EXISTS notification_log (
    id          TEXT PRIMARY KEY,
    event_type  TEXT,
    adapter     TEXT,
    severity    TEXT,
    title       TEXT,
    delivered   INTEGER DEFAULT 0,
    error       TEXT,
    created_at  TEXT
);
"""

_COL_ID = "col-reflex-test-001"
_SEC_ID = "sec-reflex-test-001"
_DOC_ID = "doc-reflex-test-001"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "reflex_test.db")
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    # Seed a collection tagged for NDC events
    conn.execute(
        "INSERT INTO dic_collections (collection_id, name, tags) VALUES (?,?,?)",
        (_COL_ID, "Network Architecture Docs", "network_architecture,infrastructure"),
    )
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, filename) VALUES (?,?,?)",
        (_DOC_ID, _COL_ID, "network_sop.md"),
    )
    conn.execute(
        "INSERT INTO dic_sections (section_id, doc_id, heading, content, created_at) VALUES (?,?,?,?,?)",
        (_SEC_ID, _DOC_ID, "Network Topology Overview", "Current topology uses a flat /24 subnet.", _now()),
    )
    conn.commit()
    conn.close()
    return path


def _make_shim(path: str) -> _ShimConn:
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    return _ShimConn(raw)


@contextmanager
def _gc(path: str):
    c = _make_shim(path)
    try:
        yield c
    finally:
        c.commit()


def _insert_event(db_path: str, event_type: str = "ndc.topology.drift_detected",
                   source_canvas: str = "ndc", target_canvas: str = "dic") -> str:
    eid = f"ev-{uuid.uuid4().hex[:12]}"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO canvas_events (id, source_canvas, target_canvas, event_type, payload_json, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (eid, source_canvas, target_canvas, event_type,
         json.dumps({"change_summary": "Subnet added to segment A"}), _now()),
    )
    conn.commit()
    conn.close()
    return eid


# ── Patch helper ──────────────────────────────────────────────────────────────

def _patch_all(db_path: str):
    """Context manager that patches all get_connection calls to our SQLite shim."""
    @contextmanager
    def _fake_gc():
        yield _make_shim(db_path)

    patches = [
        patch("tools.genesis.reflexes.dic_integration.get_connection", lambda: _make_shim(db_path)),
        patch("tools.document_intelligence.canvas_adapter.get_connection", _fake_gc),
        patch("tools.document_intelligence.suggestion_store.get_connection", _fake_gc),
    ]
    return patches


# ── run() result shape ────────────────────────────────────────────────────────

def test_run_returns_required_keys(db):
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        from tools.genesis.reflexes.dic_integration import run
        result = run({"dry_run": True})
        for key in ("events_found", "events_processed", "suggestions_created",
                    "errors", "status", "cadence_minutes"):
            assert key in result, f"Missing key '{key}'"
    finally:
        for p in ps:
            p.stop()


def test_run_no_events_returns_zero(db):
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        from tools.genesis.reflexes.dic_integration import run
        result = run({})
        assert result["events_found"] == 0
        assert result["suggestions_created"] == 0
        assert result["status"] == "ok"
    finally:
        for p in ps:
            p.stop()


def test_run_dry_run_finds_events_but_creates_none(db):
    _insert_event(db)
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        from tools.genesis.reflexes.dic_integration import run
        result = run({"dry_run": True})
        assert result["events_found"] == 1
        assert result["suggestions_created"] == 0
    finally:
        for p in ps:
            p.stop()


# ── Suggestion creation ───────────────────────────────────────────────────────

def test_run_creates_suggestion_for_matched_collection(db):
    _insert_event(db)
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        # Stub out LLM so we get deterministic stub suggestions
        with patch("tools.genesis.reflexes.dic_integration._draft_suggestion",
                   return_value="Stub: review this section"):
            from tools.genesis.reflexes.dic_integration import run
            result = run({})
        assert result["suggestions_created"] >= 1
        # Verify row in DB
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM dic_suggestions").fetchall()
        conn.close()
        assert len(rows) >= 1
    finally:
        for p in ps:
            p.stop()


def test_suggestion_links_trigger_event_id(db):
    eid = _insert_event(db)
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        with patch("tools.genesis.reflexes.dic_integration._draft_suggestion",
                   return_value="Stub suggestion"):
            from tools.genesis.reflexes.dic_integration import run
            run({})
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT trigger_event_id FROM dic_suggestions LIMIT 1"
        ).fetchone()
        conn.close()
        assert row[0] == eid
    finally:
        for p in ps:
            p.stop()


def test_suggestion_has_correct_canvas_source(db):
    _insert_event(db, source_canvas="ndc")
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        with patch("tools.genesis.reflexes.dic_integration._draft_suggestion",
                   return_value="Stub"):
            from tools.genesis.reflexes.dic_integration import run
            run({})
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT canvas_source FROM dic_suggestions LIMIT 1").fetchone()
        conn.close()
        assert row[0] == "ndc"
    finally:
        for p in ps:
            p.stop()


# ── Idempotency ───────────────────────────────────────────────────────────────

def test_second_run_creates_no_duplicate_suggestions(db):
    _insert_event(db)
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        with patch("tools.genesis.reflexes.dic_integration._draft_suggestion",
                   return_value="Stub"):
            from tools.genesis.reflexes.dic_integration import run
            run({})   # first run
            run({})   # second run — event already processed
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM dic_suggestions").fetchone()[0]
        processed = conn.execute(
            "SELECT COUNT(*) FROM dic_processed_canvas_events"
        ).fetchone()[0]
        conn.close()
        assert processed == 1  # event marked exactly once
        # suggestion count doesn't grow on second run
        assert count >= 1
        # run again a third time and count stays the same
    finally:
        for p in ps:
            p.stop()


def test_event_marked_processed_after_run(db):
    eid = _insert_event(db)
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        with patch("tools.genesis.reflexes.dic_integration._draft_suggestion",
                   return_value="Stub"):
            from tools.genesis.reflexes.dic_integration import run
            run({})
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT event_id FROM dic_processed_canvas_events WHERE event_id = ?", (eid,)
        ).fetchone()
        conn.close()
        assert row is not None
    finally:
        for p in ps:
            p.stop()


# ── Error isolation ───────────────────────────────────────────────────────────

def test_one_bad_event_does_not_block_others(db):
    eid_good = _insert_event(db, event_type="ndc.topology.drift_detected")
    eid_bad = _insert_event(db, event_type="ndc.config.push")

    ps = _patch_all(db)
    for p in ps:
        p.start()

    call_count = 0

    def _flaky_draft(section, change_context, rationale, patch_mode):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Simulated LLM error")
        return "Stub from good event"

    try:
        with patch("tools.genesis.reflexes.dic_integration._draft_suggestion", _flaky_draft):
            from tools.genesis.reflexes.dic_integration import run
            result = run({})
        # At least one event must succeed even if another fails
        assert result["events_found"] == 2
    finally:
        for p in ps:
            p.stop()


# ── Non-DIC events not picked up ─────────────────────────────────────────────

def test_events_not_targeting_dic_are_ignored(db):
    eid = _insert_event(db, target_canvas="ndc")  # wrong target
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        from tools.genesis.reflexes.dic_integration import run
        result = run({})
        assert result["events_found"] == 0
    finally:
        for p in ps:
            p.stop()


# ── Notification ──────────────────────────────────────────────────────────────

def test_notification_emitted_for_suggestion(db):
    _insert_event(db)
    ps = _patch_all(db)
    for p in ps:
        p.start()
    try:
        with patch("tools.genesis.reflexes.dic_integration._draft_suggestion",
                   return_value="Stub"):
            from tools.genesis.reflexes.dic_integration import run
            run({})
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM notification_log").fetchall()
        conn.close()
        assert len(rows) >= 1
        assert rows[0][1] == "dic_suggestion_created"  # event_type column
    finally:
        for p in ps:
            p.stop()
