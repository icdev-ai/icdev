# CUI // SP-CTI
"""Unit test: POST /api/chat/use-cases/import with overwrite=false skips duplicate IDs.

Verifies that when a bundle contains an ID already present in use_case_overrides,
the row is added to 'skipped' and the original DB label is preserved.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from flask import Flask
from tools.dashboard.api.chat import chat_api

_UC_ID = "test_import_uc"
_ORIGINAL_LABEL = "Original Label"
_NEW_LABEL = "New Label From Bundle"

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS use_case_overrides (
    id TEXT PRIMARY KEY,
    label TEXT, description TEXT, icon TEXT, badge TEXT,
    agent_model TEXT, ricoas INTEGER, boost_threshold INTEGER,
    system_prompt TEXT, seed_message TEXT,
    canvas_wiring TEXT, quick_actions TEXT,
    updated_at TEXT, updated_by TEXT,
    classification TEXT DEFAULT NULL,
    fast_track INTEGER DEFAULT 0,
    skip_requirement_types TEXT,
    user_config TEXT,
    is_user_created INTEGER DEFAULT 1,
    created_by TEXT DEFAULT NULL,
    created_at TEXT DEFAULT NULL,
    template_requirements TEXT DEFAULT NULL,
    category TEXT DEFAULT NULL,
    tenant_id TEXT DEFAULT '',
    canvas_seeds TEXT DEFAULT NULL,
    workflow_steps TEXT DEFAULT NULL
)
"""


@pytest.fixture()
def icdev_db(tmp_path):
    """Isolated SQLite DB pre-seeded with a user-created use case row."""
    db_file = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(_CREATE_TABLE_SQL)
    conn.execute(
        "INSERT INTO use_case_overrides (id, label, is_user_created, tenant_id) VALUES (?, ?, 1, '')",
        (_UC_ID, _ORIGINAL_LABEL),
    )
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture()
def client(icdev_db):
    """Flask test client wired to the isolated DB via mocked get_connection."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(chat_api)

    def _make_conn(db_path=None, **kw):
        # Translating wrapper — the chat API authors %s for PostgreSQL.
        from _sql_compat import connect as _tconnect

        return _tconnect(icdev_db, row_factory=False)

    with (
        patch("tools.db.storage.get_connection", side_effect=_make_conn),
        patch("tools.dashboard.api.chat._uc_load_yaml", return_value=[]),
    ):
        with app.test_client() as c:
            yield c


def _bundle_yaml(uc_id: str, label: str) -> str:
    return yaml.dump({
        "icdev_uc_bundle": True,
        "use_cases": [{"id": uc_id, "label": label}],
    })


class TestImportOverwriteFalse:
    def test_duplicate_id_goes_to_skipped(self, client):
        payload = {"yaml_content": _bundle_yaml(_UC_ID, _NEW_LABEL)}
        resp = client.post("/api/chat/use-cases/import", json=payload)
        assert resp.status_code == 200
        data = resp.get_json()
        skipped_ids = [s["id"] for s in data["skipped"]]
        assert _UC_ID in skipped_ids

    def test_duplicate_id_not_in_imported(self, client):
        payload = {"yaml_content": _bundle_yaml(_UC_ID, _NEW_LABEL)}
        resp = client.post("/api/chat/use-cases/import", json=payload)
        assert resp.status_code == 200
        data = resp.get_json()
        assert _UC_ID not in data["imported"]

    def test_original_label_preserved_in_db(self, client, icdev_db):
        payload = {"yaml_content": _bundle_yaml(_UC_ID, _NEW_LABEL)}
        client.post("/api/chat/use-cases/import", json=payload)
        conn = sqlite3.connect(str(icdev_db))
        row = conn.execute(
            "SELECT label FROM use_case_overrides WHERE id=?", (_UC_ID,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == _ORIGINAL_LABEL

    def test_overwrite_false_is_default(self, client):
        """Sending no overwrite key should behave identically to overwrite=false."""
        payload = {"yaml_content": _bundle_yaml(_UC_ID, _NEW_LABEL)}
        resp = client.post("/api/chat/use-cases/import", json=payload)
        data = resp.get_json()
        skipped_ids = [s["id"] for s in data["skipped"]]
        assert _UC_ID in skipped_ids
        assert _UC_ID not in data["imported"]
