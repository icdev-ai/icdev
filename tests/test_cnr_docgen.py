# CUI // SP-CTI
"""CNR Canvas (DocGen/IDR) production-readiness tests — cnr-doc-01..04.

Covers the TRUST citation/placeholder publish gate, the publish-payload bypass
fix + WriteGuard fail-closed, upload allowlist/size caps, tenant IDOR scoping,
exported-HTML XSS escaping, PG schema convergence, the honest PDF label, and the
freshness hash cache. Self-bootstrapping SQLite harness (mirrors test_docgen.py).
"""
from __future__ import annotations

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

_REPO = Path(__file__).parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

os.environ.setdefault("ICDEV_STORAGE_BACKEND", "sqlite")
os.environ.setdefault("FLASK_ENV", "testing")


@pytest.fixture(autouse=True)
def _sqlite_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db = str(tmp_path / "icdev_test.db")
    monkeypatch.setenv("ICDEV_DB_PATH", db)
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS idr_sessions (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, domain TEXT NOT NULL DEFAULT 'network',
        doc_type TEXT NOT NULL DEFAULT 'runbook', template_id TEXT, stage INTEGER DEFAULT 0,
        status TEXT DEFAULT 'setup', dic_collection_id TEXT, ace_instance_id TEXT,
        topology_id TEXT, wg_result_id TEXT, created_by TEXT, tenant_id TEXT,
        classification TEXT DEFAULT 'CUI', final_doc_text TEXT,
        last_source_hash TEXT, source_hash_checked_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idr_uploads (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, filename TEXT NOT NULL,
        upload_type TEXT NOT NULL DEFAULT 'doc', file_path TEXT, file_hash TEXT,
        dic_doc_id TEXT, extracted_from_doc_id TEXT, status TEXT DEFAULT 'pending',
        error_msg TEXT, tenant_id TEXT, uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idr_artifacts (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, dic_doc_id TEXT,
        dic_version_id TEXT, format TEXT NOT NULL, file_path TEXT, wg_result_id TEXT,
        published_at TEXT, tenant_id TEXT, flagged_sections TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idr_publish_audit (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, gate TEXT NOT NULL,
        reviewer TEXT, findings TEXT, tenant_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()

    import sqlite3 as _sqlite3
    from tools.db.storage import StorageConnection

    def _get_conn():
        c = _sqlite3.connect(db)
        c.row_factory = _sqlite3.Row
        return StorageConnection(c, "sqlite")

    with patch("tools.db.storage.get_connection", side_effect=lambda: _get_conn()):
        yield


def _client(tmp_path):
    from tools.docgen.blueprint import docgen_bp
    from flask import Flask
    app = Flask(__name__, template_folder=str(tmp_path / "templates"))
    app.register_blueprint(docgen_bp)
    app.config["TESTING"] = True
    return app.test_client()


def _session_ready(final_doc_text, **fields):
    """Create a session that has passed WriteGuard, with a given final_doc_text."""
    from tools.docgen.session_manager import create_session, set_field, advance_stage
    s = create_session(title="CNR Test", domain="network", **fields)
    advance_stage(s["id"], 6, "writeguard")
    set_field(s["id"], wg_result_id="wg-ready", final_doc_text=final_doc_text)
    return s


# ═══════════════════ cnr-doc-01: TRUST citation/placeholder gate ═══════════════

def test_citation_publish_gate_blocks_uncited_text():
    from tools.docgen.workflow import citation_publish_gate
    res = citation_publish_gate("A network runbook body with no sources at all.")
    assert res["blocked"] is True
    assert res["gate"] == "citation_guard"
    assert res["citation_findings"]


def test_citation_publish_gate_passes_cited_text():
    from tools.docgen.workflow import citation_publish_gate
    res = citation_publish_gate("Firewalls are configured [source: kb1].")
    assert res["blocked"] is False
    assert res["gate"] is None


def test_citation_publish_gate_blocks_placeholders_first():
    from tools.docgen.workflow import citation_publish_gate
    res = citation_publish_gate("Body [source: kb1] with a [PLACEHOLDER] left in.")
    assert res["blocked"] is True
    assert res["gate"] == "placeholder_guard"


def test_citation_publish_gate_force_records_override():
    from tools.docgen.workflow import citation_publish_gate
    res = citation_publish_gate("Uncited body text.", force_citations=True)
    assert res["blocked"] is False
    assert res["overrides"].get("citation_guard_override")


def test_publish_route_blocks_uncited_document(tmp_path):
    """POST /publish is blocked (409) when the validated doc has no citations."""
    client = _client(tmp_path)
    s = _session_ready("A runbook with no citations whatsoever.")
    resp = client.post(f"/docgen/api/sessions/{s['id']}/publish", json={})
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["gate"] == "citation_guard"
    assert body["citation_findings"]


def test_publish_route_force_citations_writes_audit_row(tmp_path):
    """force_citations publishes past the gate AND persists an append-only audit row."""
    from tools.docgen.session_manager import list_publish_audit
    client = _client(tmp_path)
    s = _session_ready("A runbook with no citations whatsoever.")
    with patch("tools.docgen.workflow.stage8_publish", return_value=[{"format": "html"}]):
        resp = client.post(
            f"/docgen/api/sessions/{s['id']}/publish",
            json={"force_citations": True, "reviewer": "alice"},
        )
    assert resp.status_code == 201
    rows = list_publish_audit(s["id"])
    assert len(rows) == 1
    assert rows[0]["gate"] == "citation_guard"
    assert rows[0]["reviewer"] == "alice"


def test_publish_route_allows_cited_document(tmp_path):
    client = _client(tmp_path)
    s = _session_ready("Configured per policy [source: kb1].")
    with patch("tools.docgen.workflow.stage8_publish", return_value=[{"format": "html"}]):
        resp = client.post(f"/docgen/api/sessions/{s['id']}/publish", json={})
    assert resp.status_code == 201
