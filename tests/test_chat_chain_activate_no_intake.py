# CUI // SP-CTI
"""Unit test: POST /api/chat/chains/<chain_id>/activate returns 503 when _HAS_INTAKE is False.

Verifies that the activate endpoint short-circuits with HTTP 503 and an explanatory
error body whenever the intake engine is unavailable, without needing a live engine.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask
from tools.dashboard.api.chat import chat_api

_CHAIN_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS use_case_chains (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    use_case_ids TEXT NOT NULL,
    merged_requirements TEXT,
    status TEXT DEFAULT 'draft',
    linked_session_id TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT DEFAULT 'dashboard-user',
    updated_at TEXT
)
"""

_CHAIN_ID = "chain_test-activate-503"


@pytest.fixture()
def chain_db(tmp_path):
    """Isolated SQLite DB with a pre-created chain row."""
    db_file = tmp_path / "test_chains.db"
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.execute(_CHAIN_TABLE_SQL)
    conn.execute(
        """INSERT INTO use_case_chains
           (id, tenant_id, name, use_case_ids, merged_requirements, status, created_at)
           VALUES (?, '', 'Test Chain', '[]', '[]', 'draft', '2026-01-01T00:00:00+00:00')""",
        (_CHAIN_ID,),
    )
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture()
def client(chain_db):
    """Flask test client wired to the isolated DB with _HAS_INTAKE patched to False."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(chat_api)

    def _make_conn(db_path=None, **kw):
        conn = sqlite3.connect(str(chain_db))
        conn.row_factory = sqlite3.Row
        return conn

    with (
        patch("tools.db.storage.get_connection", side_effect=_make_conn),
        patch("tools.dashboard.api.chat._HAS_INTAKE", False),
        patch("tools.dashboard.api.chat._uc_load_yaml", return_value=[]),
    ):
        with app.test_client() as c:
            yield c


class TestChainActivateNoIntake:
    def test_returns_503(self, client):
        resp = client.post(f"/api/chat/chains/{_CHAIN_ID}/activate")
        assert resp.status_code == 503

    def test_body_contains_error_key(self, client):
        resp = client.post(f"/api/chat/chains/{_CHAIN_ID}/activate")
        data = resp.get_json()
        assert data is not None
        assert "error" in data or "message" in data

    def test_error_message_mentions_intake(self, client):
        resp = client.post(f"/api/chat/chains/{_CHAIN_ID}/activate")
        data = resp.get_json()
        body_str = (data.get("error") or data.get("message") or "").lower()
        assert "intake" in body_str

    def test_unknown_chain_still_returns_503_not_404(self, client):
        """_HAS_INTAKE guard fires before DB lookup, so unknown IDs also get 503."""
        resp = client.post("/api/chat/chains/nonexistent-chain/activate")
        assert resp.status_code == 503
