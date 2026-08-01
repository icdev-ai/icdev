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
    CREATE TABLE IF NOT EXISTS idr_analyses (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, upload_id TEXT NOT NULL,
        analysis_type TEXT NOT NULL, result_ref_id TEXT NOT NULL, status TEXT DEFAULT 'done',
        error_msg TEXT, tenant_id TEXT, result_json TEXT, confidence_score REAL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS idr_conflicts (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, node_label TEXT NOT NULL,
        conflict_type TEXT NOT NULL, source_a TEXT NOT NULL, source_a_value TEXT,
        source_b TEXT NOT NULL, source_b_value TEXT, resolved_by TEXT, resolution TEXT,
        resolution_notes TEXT, resolved_at TEXT, tenant_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
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


# ═══════════════ cnr-doc-02: publish-gate bypass + fail-closed WG ══════════════

def test_publish_ignores_client_doc_text(tmp_path):
    """A client-supplied doc_text can NOT override the validated final_doc_text."""
    client = _client(tmp_path)
    s = _session_ready("Server body cited [source: kb1].")
    captured = {}

    def _fake_publish(session_id, doc_text, title, **kw):
        captured["doc_text"] = doc_text
        return [{"format": "html"}]

    with patch("tools.docgen.workflow.stage8_publish", side_effect=_fake_publish):
        resp = client.post(
            f"/docgen/api/sessions/{s['id']}/publish",
            json={"doc_text": "HOSTILE arbitrary bytes with no gate [source: x]."},
        )
    assert resp.status_code == 201
    # The published bytes are the server-side validated text, not the client payload.
    assert captured["doc_text"] == "Server body cited [source: kb1]."


def test_stage6_writeguard_fails_closed_on_import_error():
    import sys
    from tools.docgen.session_manager import create_session
    from tools.docgen.workflow import stage6_writeguard
    s = create_session(title="WG", domain="network")
    with patch.dict(sys.modules, {"tools.pulse.writeguard": None}):
        res = stage6_writeguard(s["id"], "Some text.", "network")
    assert res["passed"] is False
    assert res["blocked"] is True
    assert res.get("writeguard_unavailable") is True


def test_api_writeguard_route_fails_closed_on_import_error(tmp_path):
    import sys
    from tools.docgen.session_manager import create_session, advance_stage
    client = _client(tmp_path)
    s = create_session(title="WG route", domain="network")
    advance_stage(s["id"], 6, "writeguard")
    with patch.dict(sys.modules, {"tools.pulse.writeguard": None}):
        resp = client.post(
            f"/docgen/api/sessions/{s['id']}/writeguard",
            json={"doc_text": "Some doc text to check."},
        )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["passed"] is False
    assert body["writeguard_unavailable"] is True


# ═══════════ cnr-doc-03: upload allowlist/size, tenant IDOR, XSS ═══════════════

def test_upload_rejects_disallowed_extension(tmp_path):
    from io import BytesIO
    from tools.docgen.session_manager import create_session
    client = _client(tmp_path)
    s = create_session(title="Up", domain="network")
    resp = client.post(
        f"/docgen/api/sessions/{s['id']}/uploads",
        data={"file": (BytesIO(b"MZ evil"), "malware.exe"), "upload_type": "doc"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "not permitted" in resp.get_json()["error"]


def test_upload_rejects_oversize_file(tmp_path, monkeypatch):
    from io import BytesIO
    from tools.docgen.session_manager import create_session
    monkeypatch.setenv("DOCGEN_MAX_UPLOAD_BYTES", "10")
    client = _client(tmp_path)
    s = create_session(title="Up", domain="network")
    resp = client.post(
        f"/docgen/api/sessions/{s['id']}/uploads",
        data={"file": (BytesIO(b"x" * 500), "notes.txt"), "upload_type": "doc"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "upload limit" in resp.get_json()["error"]


def test_get_session_cross_tenant_is_404(tmp_path):
    from tools.docgen.session_manager import create_session, set_field
    client = _client(tmp_path)
    s = create_session(title="TenantA doc", domain="network", tenant_id="tenant-a")
    set_field(s["id"], tenant_id="tenant-a")
    with patch("tools.docgen.blueprint._request_tenant_id", return_value="tenant-b"):
        resp = client.get(f"/docgen/api/sessions/{s['id']}")
    assert resp.status_code == 404


def test_get_session_same_tenant_ok(tmp_path):
    from tools.docgen.session_manager import create_session, set_field
    client = _client(tmp_path)
    s = create_session(title="TenantA doc", domain="network", tenant_id="tenant-a")
    set_field(s["id"], tenant_id="tenant-a")
    with patch("tools.docgen.blueprint._request_tenant_id", return_value="tenant-a"):
        resp = client.get(f"/docgen/api/sessions/{s['id']}")
    assert resp.status_code == 200


def test_download_artifact_cross_tenant_is_404(tmp_path):
    from tools.docgen.session_manager import create_session, add_artifact, set_field
    client = _client(tmp_path)
    s = create_session(title="TenantA", domain="network", tenant_id="tenant-a")
    set_field(s["id"], tenant_id="tenant-a")
    art = add_artifact(s["id"], "html", file_path=str(tmp_path / "doc.html"), tenant_id="tenant-a")
    with patch("tools.docgen.blueprint._request_tenant_id", return_value="tenant-b"):
        resp = client.get(f"/docgen/api/sessions/{s['id']}/artifacts/{art['id']}/download")
    assert resp.status_code == 404


def test_export_html_escapes_hostile_title_and_body(tmp_path):
    from tools.docgen.workflow import _try_export_html
    from tools.docgen.session_manager import create_session
    s = create_session(title="X", domain="network")
    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir, exist_ok=True)
    artifacts = []
    hostile_title = "<script>alert('xss')</script>Report"
    hostile_body = "Intro\n\n<script>steal()</script>\n\n<img src=x onerror=alert(1)>\n\n[click](javascript:alert(2))"
    _try_export_html(s["id"], hostile_body, hostile_title, out_dir, "CUI", artifacts)
    content = Path(artifacts[0]["file_path"]).read_text(encoding="utf-8")
    assert "<script>" not in content
    assert "onerror=" not in content
    assert "javascript:alert" not in content
    # Hostile title is HTML-escaped, not rendered as a live tag.
    assert "&lt;script&gt;" in content


def test_sanitize_html_keeps_safe_formatting():
    from tools.docgen.workflow import _sanitize_html
    out = _sanitize_html("<h1>Title</h1><p>ok <strong>bold</strong></p>")
    assert "<h1>" in out and "<strong>" in out


# ═══════════ cnr-doc-04: schema converge, landing, email, pdf label, perf ══════

def test_add_upload_matches_schema_all_backends():
    """add_upload uses only real idr_uploads columns (no upload_id/collection_id/filepath)."""
    from tools.docgen.session_manager import create_session, add_upload, get_upload
    s = create_session(title="U", domain="network")
    up = add_upload(s["id"], filename="topo.png", upload_type="diagram",
                    file_path="/x/topo.png", tenant_id="t1")
    assert up["id"] and up["filename"] == "topo.png"
    assert get_upload(up["id"])["upload_type"] == "diagram"


def test_add_upload_accepts_email_type():
    """'email' upload_type (workflow.py:553) is a valid type end-to-end."""
    from tools.docgen.session_manager import create_session, add_upload
    s = create_session(title="U", domain="network")
    up = add_upload(s["id"], filename="msg.eml", upload_type="email", file_path="/x/msg.eml")
    assert up["upload_type"] == "email"


def test_sessions_with_freshness_degrades_when_tables_absent():
    """cnr-doc-04(b): landing helper returns [] instead of raising when idr tables absent."""
    from tools.docgen import blueprint as bp
    with patch("tools.docgen.session_manager.list_sessions",
               side_effect=Exception("no such table: idr_sessions")):
        assert bp._sessions_with_freshness() == []


def test_check_freshness_stored_hash_skips_get_session():
    """cnr-doc-04(e): passing stored_hash avoids the per-session get_session round-trip."""
    from tools.docgen.workflow import check_freshness
    with patch("tools.docgen.session_manager.get_session",
               side_effect=AssertionError("get_session must not be called")):
        res = check_freshness("sess-x", [], stored_hash="abc123")
    assert res["stored_hash"] == "abc123"


def test_compute_source_hash_cache_and_change_detection(tmp_path):
    from tools.docgen.workflow import compute_source_hash
    f = tmp_path / "src.txt"
    f.write_text("original", encoding="utf-8")
    h1 = compute_source_hash([str(f)])
    assert h1 and compute_source_hash([str(f)]) == h1  # cached / stable
    import os as _os
    import time as _time
    _time.sleep(0.01)
    f.write_text("changed content", encoding="utf-8")
    _os.utime(str(f), None)
    assert compute_source_hash([str(f)]) != h1  # stat signature changed -> re-hashed


def test_try_export_pdf_no_fpdf_records_no_pdf_artifact(tmp_path):
    """cnr-doc-04(d): when fpdf2 is absent, no (mislabelled) 'pdf' artifact is recorded."""
    from tools.docgen.workflow import _try_export_pdf
    artifacts = []
    with patch("importlib.util.find_spec", return_value=None):
        _try_export_pdf("sess-x", "body", "Title", str(tmp_path), "CUI", artifacts)
    assert artifacts == []
