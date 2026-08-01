# CUI // SP-CTI
"""Live-PG regressions + flow fixes: chunk-link column name, transaction
hygiene, the one-click modernization scan, and collection deletion."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import flask
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_DDL = [
    """CREATE TABLE IF NOT EXISTS dic_collections (collection_id TEXT PRIMARY KEY,
        name TEXT, tenant_id TEXT, classification TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, title TEXT, filename TEXT,
        status TEXT, origin TEXT, classification TEXT, template_type TEXT,
        writeguard_mode TEXT, source_idr_session_id TEXT, source_wg_result_id TEXT,
        tenant_id TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_versions (
        version_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL,
        version_no INTEGER NOT NULL DEFAULT 1, origin TEXT, status TEXT,
        assigned_to TEXT, review_notes TEXT, content_sha256 TEXT,
        created_at TEXT, created_by TEXT, tenant_id TEXT, classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_sections (
        section_id TEXT PRIMARY KEY, version_id TEXT NOT NULL,
        doc_id TEXT NOT NULL, heading TEXT NOT NULL, content TEXT,
        citations_json TEXT, status TEXT DEFAULT 'draft',
        origin TEXT DEFAULT 'ai_generated', assigned_to TEXT, reviewed_by TEXT,
        reviewed_at TEXT, created_at TEXT, created_by TEXT, tenant_id TEXT,
        classification TEXT)""",
    # PG-shaped dic_chunk_links: the column is link_id, NOT id — the scanner
    # selecting dcl.id poisoned the live PG transaction (regression).
    """CREATE TABLE IF NOT EXISTS dic_chunk_links (
        link_id TEXT PRIMARY KEY, doc_id TEXT, version_id TEXT,
        rag_chunk_id TEXT, collection_id TEXT, chunk_index INTEGER,
        page INTEGER, section TEXT, created_at TEXT, tenant_id TEXT,
        classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS rag_chunks (
        id TEXT PRIMARY KEY, content TEXT, content_hash TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_suggestions (
        suggestion_id TEXT PRIMARY KEY, section_id TEXT, doc_id TEXT,
        collection_id TEXT, canvas_source TEXT, suggested_content TEXT,
        current_content TEXT, rationale TEXT, status TEXT,
        created_at TEXT, updated_at TEXT, tenant_id TEXT, classification TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_doc_freshness (doc_id TEXT PRIMARY KEY,
        collection_id TEXT, state TEXT, score REAL, reason TEXT, updated_at TEXT)""",
]

_DOCMOD_DDL_KEYS = ("docmod_findings", "docmod_scan_runs", "docmod_doc_scan_state")


@pytest.fixture()
def db():
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    for ddl in _DDL:
        conn.execute(ddl)
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if any(k in stmt for k in _DOCMOD_DDL_KEYS) and "CREATE TABLE" in stmt:
            conn.execute(stmt)
    for t in ("docmod_findings", "dic_chunk_links", "rag_chunks"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    yield


@pytest.fixture()
def client():
    import tools.document_intelligence.blueprint as bp_mod
    import tools.document_intelligence.modernization_routes  # noqa: F401

    app = flask.Flask(__name__)
    app.register_blueprint(bp_mod.dic_bp, url_prefix="/document-intelligence")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _seed_chunked_doc(collection_id="col-pg", content="Use TLS 1.1 everywhere.") -> str:
    from tools.db.storage import get_connection

    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    conn = get_connection()
    conn.execute(
        "INSERT INTO dic_collections (collection_id, name, created_at) "
        "VALUES (%s,%s,'2026-01-01') ON CONFLICT (collection_id) DO NOTHING",
        (collection_id, collection_id),
    )
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, filename, created_at) "
        "VALUES (%s,%s,'PG Doc','pg.txt','2026-01-01')", (doc_id, collection_id),
    )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at) "
        "VALUES (%s,%s,1,'human_authored','approved','2026-01-01')",
        (f"{doc_id}_v1", doc_id),
    )
    chunk_id = f"rc-{uuid.uuid4().hex[:8]}"
    # rag_chunks.content_hash is NOT NULL in the real/shared schema; the test's
    # private CREATE (content_hash nullable) loses to it via IF NOT EXISTS, so
    # supply the hash explicitly rather than relying on the drifted local DDL.
    chunk_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO rag_chunks (id, content, content_hash) VALUES (%s,%s,%s)",
        (chunk_id, content, chunk_hash),
    )
    conn.execute(
        "INSERT INTO dic_chunk_links (link_id, doc_id, version_id, rag_chunk_id, "
        "collection_id, chunk_index, page, section, created_at) "
        "VALUES (%s,%s,%s,%s,%s,0,1,'Security','2026-01-01')",
        (f"lnk-{uuid.uuid4().hex[:8]}", doc_id, f"{doc_id}_v1", chunk_id, collection_id),
    )
    conn.commit()
    conn.close()
    return doc_id


# ── PG regression: chunk-link column name ────────────────────────────────────

def test_scanner_reads_chunks_via_link_id_column(db):
    """The live-PG defect: dic_chunk_links.link_id (not .id). The chunk path —
    not the sections fallback — must produce the finding with page/section."""
    from tools.doc_modernization import get_findings
    from tools.doc_modernization.packs.crypto_protocols import CryptoProtocolsPack
    from tools.doc_modernization.scanner import scan_document

    doc_id = _seed_chunked_doc()
    result = scan_document(
        doc_id, packs={"crypto_protocols": CryptoProtocolsPack(config={"pack_id": "crypto_protocols"})},
        force=True,
    )
    assert result["scanned"] is True and result["findings_new"] >= 1, result
    f = get_findings(doc_id=doc_id, state="open")[0]
    assert f["section_heading"] == "Security"      # came from the chunk link
    assert f["chunk_link_id"]                      # link_id threaded through
    assert f["page"] == 1


def test_scanner_source_has_no_dcl_id_reference():
    src = (REPO_ROOT / "tools" / "doc_modernization" / "scanner.py").read_text(encoding="utf-8")
    assert "dcl.id" not in src
    assert "dcl.link_id" in src
    # every evidence-read failure path must roll back (PG transaction poisoning)
    assert src.count("rollback()") >= 4


# ── one-click modernization scan endpoint ────────────────────────────────────

def test_modernization_scan_endpoint_collection_scope(client, db, monkeypatch):
    import tools.doc_modernization.scanner as scanner

    calls = {}
    monkeypatch.setattr(
        scanner, "scan_collection",
        lambda collection_id=None, trigger="manual", force=False:
            calls.update(cid=collection_id, trigger=trigger) or {"docs_scanned": 1, "findings_new": 2},
    )
    resp = client.post("/document-intelligence/api/modernization/scan",
                       json={"collection_id": "col-x"})
    assert resp.status_code == 200
    assert resp.get_json()["findings_new"] == 2
    assert calls == {"cid": "col-x", "trigger": "api"}

    resp = client.post("/document-intelligence/api/modernization/scan", json={})
    assert resp.status_code == 200
    assert calls["cid"] is None  # corpus-wide


# ── collection delete ─────────────────────────────────────────────────────────

def test_delete_collection_removes_docs_keeps_versions(client, db, monkeypatch):
    import tools.document_intelligence.blueprint as bp_mod

    monkeypatch.setattr(bp_mod, "_require_role", lambda cid, role: True)
    doc_id = _seed_chunked_doc(collection_id="col-del")

    resp = client.delete("/document-intelligence/api/collections/col-del")
    assert resp.status_code == 200, resp.get_json()
    data = resp.get_json()
    assert data["deleted"] == "col-del" and data["documents"] == 1

    from tools.db.storage import get_connection
    conn = get_connection()
    assert conn.execute("SELECT COUNT(*) FROM dic_documents WHERE collection_id='col-del'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM dic_chunk_links WHERE collection_id='col-del'").fetchone()[0] == 0
    # append-only version history retained
    assert conn.execute("SELECT COUNT(*) FROM dic_versions WHERE doc_id=%s", (doc_id,)).fetchone()[0] == 1
    conn.close()


def test_delete_collection_requires_admin(client, db, monkeypatch):
    import tools.document_intelligence.blueprint as bp_mod

    monkeypatch.setattr(bp_mod, "_require_role", lambda cid, role: False)
    assert client.delete("/document-intelligence/api/collections/col-nope").status_code == 403


# ── flow markup ───────────────────────────────────────────────────────────────

def test_flow_templates_wired():
    base = REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
    idx = (base / "index.html").read_text(encoding="utf-8")
    assert "post-upload-scan" in idx
    assert "/api/modernization/scan" in idx

    cols = (base / "collections.html").read_text(encoding="utf-8")
    assert "deleteCollection" in cols
    assert 'methods=["DELETE"]' not in cols  # JS only; route lives server-side

    fresh = (base / "freshness.html").read_text(encoding="utf-8")
    assert fresh.count("/api/modernization/scan") >= 1  # unified Scan now


def test_evidence_reads_use_rls_free_connection():
    """Flask-RLS regression: evidence tables lack tenant columns, so evidence
    must be read on get_canvas_connection (security_context=None) — never on
    the write connection, whose rollback would discard the scan-run row and
    trip the findings FK."""
    src = (REPO_ROOT / "tools" / "doc_modernization" / "scanner.py").read_text(encoding="utf-8")
    assert "def _evidence_connect" in src
    assert "get_canvas_connection" in src
    # both entry points hash evidence on the isolated connection
    assert "combined_evidence_hash(packs, ev_conn)" in src
    assert "combined_evidence_hash(packs, ev)" in src
    assert "combined_evidence_hash(packs, conn)" not in src


def test_findings_list_endpoint_and_visibility(client, db):
    """User feedback: '11 open findings, I can't see them' — the corpus-wide
    findings API + the clickable tile panel + findings-only table rows."""
    doc_id = _seed_chunked_doc(collection_id="col-vis")
    from tools.doc_modernization.packs.crypto_protocols import CryptoProtocolsPack
    from tools.doc_modernization.scanner import scan_document

    scan_document(doc_id, packs={"crypto_protocols": CryptoProtocolsPack(
        config={"pack_id": "crypto_protocols"})}, force=True)

    rows = client.get(
        "/document-intelligence/api/modernization/findings?state=open"
    ).get_json()
    mine = [r for r in rows if r["doc_id"] == doc_id]
    assert mine, "seeded finding missing from the corpus findings list"
    f = mine[0]
    assert f["doc_title"] == "PG Doc"          # titles joined in
    assert f["entity_label"].startswith("TLS")
    assert f["recommended_replacement"]
    assert f["evidence"]                        # citation-shaped evidence exposed

    # type filter works
    filtered = client.get(
        "/document-intelligence/api/modernization/findings?finding_type=eol_hardware"
    ).get_json()
    assert all(r["finding_type"] == "eol_hardware" for r in filtered)


def test_freshness_scan_all_collections_by_default(client, db, monkeypatch):
    """Scan-now regression: omitted collection_id must scan EVERY collection,
    not the literal 'default' one."""
    import tools.document_intelligence.freshness_engine as fe

    scanned = []

    class _R:
        scan_id = "s"; collection_id = "c"; stale_count = 1
        aging_count = 0; fresh_count = 2; regen_priority = 0.0; docs_scanned = 3

    monkeypatch.setattr(fe, "scan_collection",
                        lambda cid, **kw: scanned.append(cid) or _R())
    _seed_chunked_doc(collection_id="col-a")
    _seed_chunked_doc(collection_id="col-b")

    resp = client.post("/document-intelligence/api/freshness/scan", json={})
    assert resp.status_code == 200, resp.get_json()
    assert {"col-a", "col-b"} <= set(scanned)
    assert resp.get_json()["collections_scanned"] >= 2

    # explicit collection still scans just that one
    scanned.clear()
    client.post("/document-intelligence/api/freshness/scan",
                json={"collection_id": "col-a"})
    assert scanned == ["col-a"]


def test_freshness_template_findings_visibility_markup():
    tpl = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
           / "freshness.html").read_text(encoding="utf-8")
    assert "toggleFindingsPanel" in tpl          # 🧭 tile is clickable
    assert "findings-panel-body" in tpl
    assert "/api/modernization/findings" in tpl
    assert "not scored" in tpl                   # findings-only docs render as rows


def test_awaiting_review_includes_drafted_redlines(client, db):
    """User-visible regression: the sweep drafted redlines and 9 findings
    vanished from every surface — redline_drafted is still awaiting review."""
    import tools.document_intelligence.blueprint  # noqa: F401
    from tools.db.storage import get_connection

    doc_id = _seed_chunked_doc(collection_id="col-await")
    from tools.doc_modernization.packs.crypto_protocols import CryptoProtocolsPack
    from tools.doc_modernization.scanner import scan_document

    scan_document(doc_id, packs={"crypto_protocols": CryptoProtocolsPack(
        config={"pack_id": "crypto_protocols"})}, force=True)

    # simulate the sweep: append a redline_drafted state row for the finding.
    # docmod_findings is append-only and get_findings keeps the NEWEST row per
    # dedupe_key, so the redline row must be timestamped AFTER the scan-created
    # open finding to supersede it. Compute now()+1d rather than a hardcoded
    # literal (a fixed date silently rots into the past and lets the stale open
    # row win once the wall clock passes it).
    from tools.doc_modernization import get_findings
    f = get_findings(doc_id=doc_id, state="open")[0]
    newer_ts = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    conn = get_connection()
    conn.execute(
        """INSERT INTO docmod_findings
           (finding_id, run_id, doc_id, version_id, pack_id, entity_label, entity_type,
            finding_type, currency_verdict, severity, state, supersedes_id,
            redline_suggestion_id, dedupe_key, created_at)
           SELECT 'fnd-drafted-x', run_id, doc_id, version_id, pack_id, entity_label,
                  entity_type, finding_type, currency_verdict, severity,
                  'redline_drafted', finding_id, 'sug_x', dedupe_key,
                  %s
           FROM docmod_findings WHERE finding_id = %s""",
        (newer_ts, f["finding_id"]),
    )
    conn.commit()
    conn.close()

    # default findings list still shows it, flagged with the redline pointer
    rows = client.get(
        "/document-intelligence/api/modernization/findings"
    ).get_json()
    mine = [r for r in rows if r["doc_id"] == doc_id]
    assert mine and mine[0]["state"] == "redline_drafted"
    assert mine[0]["redline_suggestion_id"] == "sug_x"

    # summary counts it too
    summary = client.get("/document-intelligence/api/modernization/summary").get_json()
    assert any(d["doc_id"] == doc_id for d in summary["documents"])

    # and the kanban card bridge keeps the card open
    from tools.doc_modernization.card_bridge import _AWAITING_STATES
    assert "redline_drafted" in _AWAITING_STATES


def test_collections_page_js_is_valid_and_actionable():
    """User regression: the injected deleteCollection had a literal newline in
    a JS string, breaking ALL page JS — the collection name stopped toggling.
    Guard: the script must never contain raw newlines inside quoted strings,
    and the row must expose scan/findings/toggle actions."""
    import re

    tpl = (REPO_ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
           / "collections.html").read_text(encoding="utf-8")
    script = re.search(r"<script>([\s\S]*?)</script>", tpl).group(1)
    # crude but effective: any line with an odd number of single quotes that
    # opens a string and never closes it indicates a broken multi-line literal
    for i, line in enumerate(script.splitlines(), 1):
        stripped = re.sub(r"\'", "", line)
        assert stripped.count("'") % 2 == 0, f"unterminated JS string at script line {i}: {line[:70]}"
    assert "function scanCollection" in tpl
    assert "toggleDocs" in tpl and "deleteCollection" in tpl
    assert "String.fromCharCode(10" in tpl  # newline built safely, not literal
