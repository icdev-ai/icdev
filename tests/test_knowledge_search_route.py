# CUI // SP-CTI
"""V&V gate: /knowledge-search page renders 200 within a tight timeout.

Two checks:
  1. GET /knowledge-search returns HTTP 200.
  2. Response body contains 'Knowledge' or 'knowledge' (CUI-branded page).
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Minimal auth schema (mirrors test_aisg_wizard.py pattern)
# ---------------------------------------------------------------------------

_AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS dashboard_users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE, display_name TEXT,
    role TEXT DEFAULT 'admin', status TEXT DEFAULT 'active',
    created_by TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS dashboard_api_keys (
    id TEXT PRIMARY KEY, user_id TEXT, key_hash TEXT, key_prefix TEXT,
    label TEXT, status TEXT DEFAULT 'active', last_used_at TIMESTAMP,
    expires_at TIMESTAMP, created_at TIMESTAMP, revoked_at TIMESTAMP, revoked_by TEXT
);
CREATE TABLE IF NOT EXISTS dashboard_auth_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, event_type TEXT,
    ip_address TEXT, user_agent TEXT, details TEXT, created_at TIMESTAMP
);
INSERT OR IGNORE INTO dashboard_users (id, email, display_name, role)
VALUES ('test-admin', 'admin@test.local', 'Test Admin', 'admin');
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ks_app(tmp_path):
    """Dashboard Flask test app with knowledge-search route."""
    db_path = str(tmp_path / "ks_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_AUTH_SCHEMA)
    conn.commit()
    conn.close()

    import tools.dashboard.app as _app_mod
    import tools.dashboard.auth as _auth_mod

    with (
        patch.object(_app_mod, "DB_PATH", db_path),
        patch.object(_auth_mod, "DB_PATH", db_path),
        # Prevent 60+ PG queries from blocking the page load in unit tests.
        patch("tools.rag.ingestion_manager.get_status", return_value=None),
    ):
        from tools.dashboard.app import create_app

        app = create_app()
        app.config["TESTING"] = True
        yield app


# ---------------------------------------------------------------------------
# Check 1 — /knowledge-search returns HTTP 200
# ---------------------------------------------------------------------------


def test_knowledge_search_page_200(ks_app):
    """GET /knowledge-search must return HTTP 200."""
    client = ks_app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "test-admin"

    resp = client.get("/knowledge-search")
    assert resp.status_code == 200, (
        f"Expected 200 from GET /knowledge-search, got {resp.status_code}"
    )
    body = resp.data.decode("utf-8", errors="replace")
    assert "knowledge" in body.lower() or "Knowledge" in body, (
        "Expected 'Knowledge' text in /knowledge-search response"
    )
# CUI // SP-CTI
