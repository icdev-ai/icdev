# CUI // SP-CTI
"""rmf-wp-02 -- DIC export: the gate, the artifact row, the routes.

DIC had NO export route; docgen had the right shape (TRUST gate -> WriteGuard
gate -> idr_artifacts). These tests pin that shape on DIC's own tables:

  * an uncited AI section is a 409 on citation_guard and NO file is written;
  * a [PLACEHOLDER] is a 409 on placeholder_guard;
  * a WriteGuard FAIL is a 409, an UNIMPORTABLE WriteGuard is a 409 that no
    force flag opens (never publish text no gate could inspect);
  * WriteGuard runs over the ASSEMBLED document, BEFORE any file exists;
  * a clean version exports to a real .docx, records a dic_artifacts row with
    the file's sha256, and the download route serves it;
  * a force without a reason is a 400; a force with a reason exports, marks
    the row forced, and writes the audit rows BEFORE the file;
  * the migration's format CHECK is rendered from EXPORT_FORMATS.

Isolation: this module gets its own SQLite file (docmod conftest pattern), its
own artifact directory, and a stub WriteGuard module -- the real one is slow
and its verdict on synthetic prose is not what is under test here.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import types
import uuid
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = REPO_ROOT / "tools" / "db" / "migrations" / "20260903194350_dic_artifacts" / "up.sql"


# Tables the route touches beside the DIC trio. audit_trail mirrors the LIVE
# PostgreSQL shape (tests/conftest.py); idr_publish_audit and dic_review_notes
# are the approve route's own substrate; dic_team_access backs the role lookup.
_SUPPORT_DDL = [
    """CREATE TABLE IF NOT EXISTS audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
        event_type TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
        details TEXT, affected_files TEXT, classification TEXT DEFAULT 'CUI',
        ip_address TEXT, session_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        hash TEXT, previous_hash TEXT, signature TEXT)""",
    """CREATE TABLE IF NOT EXISTS idr_publish_audit (
        id TEXT PRIMARY KEY, session_id TEXT NOT NULL, gate TEXT NOT NULL,
        reviewer TEXT, findings TEXT, tenant_id TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""",
    """CREATE TABLE IF NOT EXISTS dic_review_notes (
        note_id TEXT PRIMARY KEY, item_id TEXT, item_type TEXT,
        note_text TEXT, reviewer_id TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dic_team_access (
        access_id TEXT PRIMARY KEY, collection_id TEXT NOT NULL, user_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'viewer', granted_by TEXT DEFAULT '',
        tenant_id TEXT DEFAULT 'default', created_at TEXT)""",
]


# ── isolation ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _isolated_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("dic_export_db") / "icdev.db"
    art_dir = tmp_path_factory.mktemp("dic_export_artifacts")
    prev_env = os.environ.get("ICDEV_DB_PATH")
    prev_art = os.environ.get("ICDEV_DIC_ARTIFACT_DIR")
    os.environ["ICDEV_DB_PATH"] = str(db_path)
    os.environ["ICDEV_DIC_ARTIFACT_DIR"] = str(art_dir)
    import tools.db.storage as storage

    prev_mod = storage.DB_PATH
    storage.DB_PATH = str(db_path)

    from tools.document_intelligence.ingest_orchestrator import _SCHEMA as _INGEST_SCHEMA

    raw = sqlite3.connect(db_path)
    for ddl in _INGEST_SCHEMA:  # dic_documents / dic_versions / dic_sections, the real DDL
        raw.execute(ddl)
    for ddl in _SUPPORT_DDL:
        raw.execute(ddl)
    # The artifact table as the migration creates it in production; the
    # runtime CREATE in exporter._ensure_schema is the SQLite fallback, and a
    # parity test below pins the two column lists together.
    raw.executescript(MIGRATION.read_text(encoding="utf-8"))
    raw.commit()
    raw.close()
    try:
        yield
    finally:
        if prev_env is None:
            os.environ.pop("ICDEV_DB_PATH", None)
        else:
            os.environ["ICDEV_DB_PATH"] = prev_env
        if prev_art is None:
            os.environ.pop("ICDEV_DIC_ARTIFACT_DIR", None)
        else:
            os.environ["ICDEV_DIC_ARTIFACT_DIR"] = prev_art
        storage.DB_PATH = prev_mod


@pytest.fixture()
def writeguard(monkeypatch):
    """A stub `tools.pulse.writeguard` whose verdict the test controls, and a
    record of what it was asked to check."""
    calls: list[str] = []
    stub = types.ModuleType("tools.pulse.writeguard")
    stub.verdict = {"passed": True, "overall_score": 88.0, "issues": [], "composites": {}}

    def run_full_quality_check(text: str) -> dict:
        calls.append(text)
        return dict(stub.verdict)

    stub.run_full_quality_check = run_full_quality_check
    stub.calls = calls
    monkeypatch.setitem(sys.modules, "tools.pulse.writeguard", stub)
    return stub


@pytest.fixture()
def client():
    import flask

    from tools.document_intelligence.blueprint import dic_bp

    app = flask.Flask(__name__)
    app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _raw():
    conn = sqlite3.connect(os.environ["ICDEV_DB_PATH"])
    conn.row_factory = sqlite3.Row
    return conn


def _seed_version(sections: list[dict], *, title="Zero Trust Whitepaper", status="approved") -> str:
    """A document, a version and its sections. Each section dict: content,
    origin ('ai_generated' | 'human_authored'), citations (chunk ids)."""
    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    version_id = f"ver-{uuid.uuid4().hex[:8]}"
    conn = _raw()
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, source_id, filename, content_type, "
        "provider, title, byte_size, content_sha256, page_count, "
        "classification, tenant_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (doc_id, "default", "src", "wp.md", "text/markdown", "builtin", title, 0, "", 1,
         "CUI", "default", "2026-09-03T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at, "
        "tenant_id, classification) VALUES (?,?,?,?,?,?,?,?)",
        (version_id, doc_id, 2, "ai_generated", status, "2026-09-03T00:00:01+00:00",
         "default", "CUI"),
    )
    for i, sec in enumerate(sections):
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
            "citations_json, status, origin, created_at, tenant_id, classification) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"sec-{uuid.uuid4().hex[:8]}", version_id, doc_id,
             sec.get("heading", f"Section {i + 1}"), sec["content"],
             json.dumps([{"chunk_id": c} for c in sec.get("citations", [])]),
             "approved", sec.get("origin", "ai_generated"),
             f"2026-09-03T00:00:0{i + 2}+00:00", "default", "CUI"),
        )
    conn.commit()
    conn.close()
    return version_id


CITED = ("Zero trust architecture requires continuous verification of every "
         "session, as the reference architecture states [source: chunk c1].")
UNCITED = "Zero trust architecture requires continuous verification of every session."


def _artifact_rows(version_id: str) -> list[dict]:
    conn = _raw()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM dic_artifacts WHERE version_id = ?", (version_id,)).fetchall()]
    finally:
        conn.close()


def _files_under(version_id: str) -> list[Path]:
    root = Path(os.environ["ICDEV_DIC_ARTIFACT_DIR"]) / version_id
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


# ── the gates refuse, and write NOTHING ───────────────────────────────────────

def test_uncited_ai_section_is_409_on_citation_guard(client, writeguard):
    vid = _seed_version([{"content": UNCITED, "origin": "ai_generated"}])
    resp = client.get(f"/document-intelligence/api/versions/{vid}/export/docx")
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert body["gate"] == "citation_guard"
    assert body["citation_findings"], body
    assert body["unmeasured"] == []
    assert _artifact_rows(vid) == []
    assert _files_under(vid) == []


def test_placeholder_is_409_on_placeholder_guard_before_citations(client, writeguard):
    vid = _seed_version([{"content": "Contact [POC NAME] for access.", "origin": "ai_generated"}])
    resp = client.get(f"/document-intelligence/api/versions/{vid}/export/docx")
    assert resp.status_code == 409
    assert resp.get_json()["gate"] == "placeholder_guard"
    assert _artifact_rows(vid) == []


def test_writeguard_fail_is_409_and_runs_over_the_assembled_document(client, writeguard):
    writeguard.verdict = {"passed": False, "overall_score": 41.0, "issues": ["passive"], "composites": {}}
    vid = _seed_version([
        {"heading": "Background", "content": CITED, "citations": ["c1"]},
        {"heading": "Approach", "content": "Written by a human.", "origin": "human_authored"},
    ])
    resp = client.get(f"/document-intelligence/api/versions/{vid}/export/docx")
    assert resp.status_code == 409, resp.get_json()
    body = resp.get_json()
    assert body["gate"] == "writeguard"
    assert body["writeguard"]["measured"] is True
    assert body["writeguard"]["passed"] is False
    # WriteGuard saw the WHOLE assembled document, not one section.
    assert len(writeguard.calls) == 1
    checked = writeguard.calls[0]
    assert "## Background" in checked and "## Approach" in checked
    assert "Written by a human." in checked and "continuous verification" in checked
    assert _files_under(vid) == [], "a refused export must not leave a file behind"
    assert _artifact_rows(vid) == []


def test_unimportable_writeguard_is_409_that_no_force_opens(client, monkeypatch):
    # A None entry in sys.modules makes `import tools.pulse.writeguard` raise.
    monkeypatch.setitem(sys.modules, "tools.pulse.writeguard", None)
    vid = _seed_version([{"content": CITED, "citations": ["c1"]}])
    resp = client.get(
        f"/document-intelligence/api/versions/{vid}/export/docx"
        "?force_writeguard=1&force_reason=reviewed+offline"
    )
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["gate"] == "writeguard"
    assert body["unmeasured"] and body["unmeasured"][0]["gate"] == "writeguard"
    assert body["writeguard"]["measured"] is False
    assert _artifact_rows(vid) == []


# ── a clean version exports ───────────────────────────────────────────────────

def test_clean_version_exports_docx_records_row_and_downloads(client, writeguard):
    vid = _seed_version([
        {"heading": "Background", "content": CITED, "citations": ["c1"]},
        {"heading": "Approach", "content": "Written by a human.", "origin": "human_authored"},
    ])
    resp = client.get(f"/document-intelligence/api/versions/{vid}/export/docx")
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    art = body["artifact"]
    assert art["format"] == "docx"
    assert art["forced"] == 0
    assert art["version_status"] == "approved"
    assert art["wg_passed"] == 1 and art["wg_score"] == 88.0
    assert body["gate"]["blocked"] is False
    assert body["gate"]["citation_findings"] == []

    # The file is a real .docx (a zip with word/document.xml) carrying the prose
    # and the classification LABEL as the marking -- not the exporter's FOUO default.
    path = Path(art["file_path"])
    assert path.is_file() and path.suffix == ".docx"
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        assert "continuous verification" in xml
        assert "Zero Trust Whitepaper" in xml
        header = "".join(z.read(n).decode("utf-8") for n in z.namelist() if "header" in n)
        assert "CUI" in header and "FOUO" not in header

    rows = _artifact_rows(vid)
    assert len(rows) == 1
    row = rows[0]
    import hashlib

    assert row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert row["byte_size"] == path.stat().st_size
    report = json.loads(row["gate_report_json"])
    assert report["writeguard"]["passed"] is True

    dl = client.get(body["download_url"])
    assert dl.status_code == 200
    assert dl.data == path.read_bytes()
    assert "attachment" in dl.headers.get("Content-Disposition", "")

    listing = client.get(f"/document-intelligence/api/versions/{vid}/artifacts").get_json()
    assert [a["artifact_id"] for a in listing["artifacts"]] == [art["artifact_id"]]


def test_html_export_escapes_hostile_title(client, writeguard):
    vid = _seed_version([{"content": CITED, "citations": ["c1"]}],
                        title="<script>alert(1)</script>")
    resp = client.get(f"/document-intelligence/api/versions/{vid}/export/html")
    assert resp.status_code == 200, resp.get_json()
    html = Path(resp.get_json()["artifact"]["file_path"]).read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_md_export_carries_classification_marking(client, writeguard):
    vid = _seed_version([{"content": CITED, "citations": ["c1"]}])
    resp = client.get(f"/document-intelligence/api/versions/{vid}/export/md")
    assert resp.status_code == 200, resp.get_json()
    text = Path(resp.get_json()["artifact"]["file_path"]).read_text(encoding="utf-8")
    assert text.startswith("CUI\n") and text.rstrip().endswith("CUI")
    assert "[source: chunk c1]" in text


# ── the force contract ────────────────────────────────────────────────────────

def test_force_without_reason_is_400_and_writes_nothing(client, writeguard):
    vid = _seed_version([{"content": UNCITED}])
    resp = client.get(f"/document-intelligence/api/versions/{vid}/export/docx?force_citations=1")
    assert resp.status_code == 400
    assert "force_reason" in resp.get_json()["error"]
    assert _artifact_rows(vid) == []


def test_force_with_reason_exports_marks_forced_and_audits_first(client, writeguard):
    vid = _seed_version([{"content": UNCITED}])
    resp = client.get(
        f"/document-intelligence/api/versions/{vid}/export/docx"
        "?force_citations=1&force_reason=SME+confirmed+the+source+offline&reviewer=alice"
    )
    assert resp.status_code == 200, resp.get_json()
    art = resp.get_json()["artifact"]
    assert art["forced"] == 1
    assert art["force_reason"] == "SME confirmed the source offline"
    assert art["exported_by"] == "alice"

    conn = _raw()
    try:
        pub = conn.execute(
            "SELECT gate, reviewer, findings FROM idr_publish_audit WHERE session_id = ?", (vid,)
        ).fetchall()
        hitl = conn.execute(
            "SELECT action, details FROM audit_trail WHERE event_type = 'dic.hitl_decision'"
        ).fetchall()
    finally:
        conn.close()
    assert [r["gate"] for r in pub] == ["citation_guard"]
    assert pub[0]["reviewer"] == "alice"
    assert json.loads(pub[0]["findings"])
    actions = [r["action"] for r in hitl]
    assert "dic_version.export_forced" in actions
    details = json.loads([r["details"] for r in hitl if r["action"] == "dic_version.export_forced"][0])
    assert details["gates"] == ["citation_guard"]
    assert details["reason"] == "SME confirmed the source offline"


def test_a_writeguard_force_needs_a_reason_too(client, writeguard):
    writeguard.verdict = {"passed": False, "overall_score": 30.0, "issues": [], "composites": {}}
    vid = _seed_version([{"content": CITED, "citations": ["c1"]}])
    assert client.get(
        f"/document-intelligence/api/versions/{vid}/export/md?force_writeguard=1"
    ).status_code == 400
    ok = client.get(
        f"/document-intelligence/api/versions/{vid}/export/md?force_writeguard=1&force_reason=style+only"
    )
    assert ok.status_code == 200, ok.get_json()
    assert ok.get_json()["artifact"]["forced"] == 1
    assert ok.get_json()["gate"]["overrides"]["writeguard"]


# ── shape ─────────────────────────────────────────────────────────────────────

def test_unknown_version_is_404_and_bad_format_is_400(client, writeguard):
    assert client.get("/document-intelligence/api/versions/nope/export/docx").status_code == 404
    resp = client.get("/document-intelligence/api/versions/nope/export/exe")
    assert resp.status_code == 400
    assert resp.get_json()["formats"] == ["md", "html", "docx", "pdf"]


def test_unknown_artifact_download_is_404(client):
    assert client.get("/document-intelligence/api/artifacts/art_nope/download").status_code == 404


def test_migration_format_check_is_rendered_from_export_formats():
    from tools.document_intelligence.exporter import EXPORT_FORMATS

    sql = MIGRATION.read_text(encoding="utf-8")
    m = re.search(r"format\s+TEXT\s+NOT NULL\s+CHECK\s*\(format IN \(([^)]*)\)\)", sql)
    assert m, "the migration must constrain `format`"
    declared = tuple(v.strip().strip("'") for v in m.group(1).split(","))
    assert declared == EXPORT_FORMATS


def test_migration_and_runtime_ddl_declare_the_same_columns():
    """The runtime CREATE (exporter._SCHEMA) and the migration must agree, or
    the INSERT's column list is right on one database and wrong on the other."""
    from tools.document_intelligence.exporter import _SCHEMA

    def cols(ddl: str) -> list[str]:
        body = ddl.split("CREATE TABLE IF NOT EXISTS dic_artifacts", 1)[1]
        body = body[body.index("(") + 1:]
        out = []
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("--") or line.startswith(")"):
                continue
            if line.startswith("CREATE INDEX"):
                break
            out.append(line.split()[0])
        return out

    assert cols(_SCHEMA) == cols(MIGRATION.read_text(encoding="utf-8"))


def test_exporter_gate_reports_are_serialisable_and_writeguard_runs_last(writeguard):
    """Library-level: placeholder refuses BEFORE citations, and citations BEFORE
    WriteGuard -- an expensive check is never spent on a draft already refused."""
    from tools.document_intelligence import exporter

    vid = _seed_version([{"content": "Call [VERIFY] first.", "origin": "ai_generated"}])
    bundle = exporter.load_version(vid)
    report = exporter.export_gate(vid, exporter.assemble_markdown(bundle))
    assert report["blocked"] and report["gate"] == "placeholder_guard"
    assert writeguard.calls == []
    json.dumps(report)


def test_the_exporter_logs_through_the_icdev_logger_only():
    """mfx-own-01 (log_standard): `_log` was a raw logging.getLogger beside the
    ICDEV get_logger the module already imported, so its two warnings bypassed
    the platform log standard. One logger, the platform's."""
    import inspect

    from tools.document_intelligence import exporter

    src = inspect.getsource(exporter)
    assert "logging.getLogger" not in src
    assert exporter._log.name == exporter.logger.name
