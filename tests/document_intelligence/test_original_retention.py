# CUI // SP-CTI
"""dwr-fid-01 -- the uploaded ORIGINAL is retained, content-addressed, and the
board says which documents have none.

THE DEFECT. ``POST /document-intelligence/api/ingest`` saved the upload to a
NamedTemporaryFile and ``os.unlink()``'d it in the ingest thread's ``finally``
while ``dic_documents.filepath`` kept pointing at the deleted path. Measured
2026-09-07 on the live board: 55 documents, 44 with no original on disk.

What these tests pin:

  * ``retain_original`` is content-addressed: the same bytes land at ONE path
    and the second call reports ``deduplicated``; the file's sha256 IS the
    recorded one; no ``.partial-`` residue is left behind;
  * the upload route retains the original BEFORE ``ingest_file`` runs (the
    temp file still exists when ingest is called), records ``original_sha256``
    on the document row, and STILL deletes the temp file afterwards;
  * with ``ICDEV_DIC_RETAIN_ORIGINALS=0`` nothing is retained and the result
    SAYS so -- the ingest never reads like a retained upload;
  * a board that has NOT run the migration still lists its documents, each
    reported ``absent`` / ``source_on_disk`` / ``no_source`` from ``filepath``,
    with ``columns_present: False`` stated beside the verdict;
  * the survey counts every verdict, keeps ``absent`` (the finding) apart from
    ``no_source`` (generated in-canvas, nothing to retain), reports whether the
    columns exist, and is ``unmeasurable`` -- never clean -- over no documents;
  * ``--verify`` catches a retained file whose bytes changed;
  * the migration's column list, the module's and the ingest DDL agree.

Isolation: this module gets its own SQLite file (the test_export pattern) and
its own originals root under tmp, so nothing is written under data/.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import types
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = REPO_ROOT / "tools" / "db" / "migrations" / "20260908003311_dic_original_retention"
BLUEPRINT = REPO_ROOT / "tools" / "document_intelligence" / "blueprint.py"

# A minimal, well-formed one-page PDF. The stubbed ingest never parses it;
# what matters is that the bytes are what the route was handed.
PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
    b"trailer << /Root 1 0 R >>\n%%EOF\n"
)

OLD_SHAPE_DDL = """CREATE TABLE dic_documents (
    doc_id TEXT PRIMARY KEY, collection_id TEXT NOT NULL, source_id TEXT,
    filename TEXT, filepath TEXT, content_type TEXT, provider TEXT, title TEXT,
    byte_size INTEGER, content_sha256 TEXT, page_count INTEGER DEFAULT 1,
    created_at TEXT NOT NULL, tenant_id TEXT, classification TEXT)"""


def _load_migration():
    import importlib.util

    spec = importlib.util.spec_from_file_location("dwr_fid_01_up", str(MIGRATION_DIR / "up.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── isolation ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="module")
def _isolated_db(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("dic_originals_db") / "icdev.db"
    root = tmp_path_factory.mktemp("dic_originals_root")
    prev = {k: os.environ.get(k) for k in ("ICDEV_DB_PATH", "ICDEV_DIC_ORIGINALS_DIR", "ICDEV_DIC_RETAIN_ORIGINALS")}
    os.environ["ICDEV_DB_PATH"] = str(db_path)
    os.environ["ICDEV_DIC_ORIGINALS_DIR"] = str(root)
    os.environ.pop("ICDEV_DIC_RETAIN_ORIGINALS", None)
    import tools.db.storage as storage

    prev_mod = storage.DB_PATH
    storage.DB_PATH = str(db_path)

    # The OLD shape first, then the migration: the route tests then run over a
    # table the migration reached, which is the live board's population.
    raw = sqlite3.connect(db_path)
    raw.execute(OLD_SHAPE_DDL)
    raw.commit()
    raw.close()
    from tests._sql_compat import connect

    c = connect(str(db_path))
    _load_migration().up(c)
    c.close()
    try:
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        storage.DB_PATH = prev_mod


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


def _wait_result(client, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/document-intelligence/api/ingest/{job_id}/result")
        body = r.get_json()
        if r.status_code == 200 and body.get("status") in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"ingest job {job_id} did not finish")


@pytest.fixture()
def stub_ingest(monkeypatch):
    """A stand-in for ingest_file that writes the document row the way the
    real one does (INSERT OR REPLACE with ``filepath`` = the temp path) and
    records whether the temp file still existed when it was called."""
    from tools.document_intelligence import ingest_orchestrator as io

    seen: dict = {}

    def fake_ingest_file(path, collection_id, *, tenant_id=None, classification=None,
                         created_by=None, progress_cb=None, **_):
        seen["path"] = path
        seen["existed_at_ingest"] = os.path.exists(path)
        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        conn = _raw()
        conn.execute(
            "INSERT OR REPLACE INTO dic_documents (doc_id, collection_id, source_id, filename, filepath, "
            "content_type, provider, title, byte_size, content_sha256, page_count, created_at, "
            "tenant_id, classification) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, collection_id, "src", Path(path).name, str(path), "application/pdf", "pypdf",
             "t", os.path.getsize(path), "x", 1, "2026-09-07T00:00:00+00:00",
             tenant_id or "default", classification or "CUI"),
        )
        conn.commit()
        conn.close()
        seen["doc_id"] = doc_id
        return io.IngestOutcome(
            doc_id=doc_id, version_id=f"{doc_id}_v1", collection_id=collection_id,
            source_id="src", provider="pypdf", chunks=0, chunks_embedded=0,
            kg_entities=0, kg_relationships=0, tenant_id=tenant_id or "default",
            classification=classification or "CUI",
        )

    monkeypatch.setattr(io, "ingest_file", fake_ingest_file)
    return seen


# ── retain_original ───────────────────────────────────────────────────────────

def test_retain_is_content_addressed_and_the_sha_is_the_files(tmp_path):
    from tools.document_intelligence import originals

    src = tmp_path / "upload.PDF"
    src.write_bytes(PDF_BYTES)
    expected = hashlib.sha256(PDF_BYTES).hexdigest()
    root = tmp_path / "root"

    first = originals.retain_original(src, "Plan.PDF", root=root)
    assert first.sha256 == expected
    assert first.deduplicated is False
    assert first.byte_size == len(PDF_BYTES)
    assert Path(first.path) == root / expected[:2] / f"{expected}.pdf"  # suffix lower-cased
    assert Path(first.path).read_bytes() == PDF_BYTES
    assert originals.sha256_of(first.path) == expected

    # Same bytes again, different filename: ONE file, and the second call says so.
    src2 = tmp_path / "again.pdf"
    src2.write_bytes(PDF_BYTES)
    second = originals.retain_original(src2, "again.pdf", root=root)
    assert second.path == first.path
    assert second.deduplicated is True

    # Different bytes cost a second file; nothing partial is left behind.
    src3 = tmp_path / "other.pdf"
    src3.write_bytes(PDF_BYTES + b"\n% trailing comment\n")
    third = originals.retain_original(src3, "other.pdf", root=root)
    assert third.path != first.path
    assert [p for p in root.rglob(".partial-*")] == []
    assert len([p for p in root.rglob("*") if p.is_file()]) == 2


# ── the upload route ──────────────────────────────────────────────────────────

def test_upload_retains_before_ingest_records_sha_and_still_deletes_the_temp(client, stub_ingest):
    from tools.document_intelligence import originals

    r = client.post(
        "/document-intelligence/api/ingest",
        data={"file": (__import__("io").BytesIO(PDF_BYTES), "plan.pdf"), "collection_id": "default"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 202, r.get_json()
    result = _wait_result(client, r.get_json()["job_id"])
    assert result["status"] == "done", result

    # retained BEFORE ingest: the temp file was still there when ingest ran ...
    assert stub_ingest["existed_at_ingest"] is True
    # ... and the deletion is KEPT: it is gone now.
    time.sleep(0.1)
    assert not os.path.exists(stub_ingest["path"])

    original = result["original"]
    assert original["retained"] is True and original["recorded"] is True, original
    expected = hashlib.sha256(PDF_BYTES).hexdigest()
    assert original["sha256"] == expected
    retained_file = Path(original["path"])
    assert retained_file.exists()
    assert retained_file.parent.parent == Path(os.environ["ICDEV_DIC_ORIGINALS_DIR"])
    # the retained FILE's sha256 is the RECORDED one, re-derived from the bytes
    assert originals.sha256_of(retained_file) == expected

    conn = _raw()
    row = conn.execute("SELECT * FROM dic_documents WHERE doc_id = ?", (stub_ingest["doc_id"],)).fetchone()
    conn.close()
    assert row["original_sha256"] == expected
    assert row["original_path"] == str(retained_file)
    assert row["original_retained_at"]
    assert row["filepath"] == stub_ingest["path"]  # the read path is unchanged; the KEPT path is new
    assert originals.original_verdict(row)["status"] == "retained"
    assert not [e for e in result["errors"] if "original" in e]

    # and the listing says so
    listing = client.get("/document-intelligence/api/collections/default/documents").get_json()
    mine = [d for d in listing["documents"] if d["doc_id"] == stub_ingest["doc_id"]]
    assert mine and mine[0]["original_status"] == "retained"
    assert mine[0]["original_detail"]["columns_present"] is True


def test_upload_with_retention_off_says_so(client, stub_ingest, monkeypatch):
    monkeypatch.setenv("ICDEV_DIC_RETAIN_ORIGINALS", "0")
    r = client.post(
        "/document-intelligence/api/ingest",
        data={"file": (__import__("io").BytesIO(PDF_BYTES), "plan.pdf")},
        content_type="multipart/form-data",
    )
    result = _wait_result(client, r.get_json()["job_id"])
    assert result["status"] == "done"
    assert result["original"] == {"retained": False, "reason": "disabled_by_env"}
    assert any(e.startswith("original not retained: disabled_by_env") for e in result["errors"])
    conn = _raw()
    row = conn.execute("SELECT original_path, original_sha256 FROM dic_documents WHERE doc_id = ?",
                       (stub_ingest["doc_id"],)).fetchone()
    conn.close()
    assert row["original_path"] is None and row["original_sha256"] is None


def test_route_source_retains_before_ingest_and_keeps_the_unlink():
    src = BLUEPRINT.read_text(encoding="utf-8")
    start = src.index("def api_ingest():")
    end = src.index("def api_ingest_stream(")
    body = src[start:end]
    assert body.index("retain_original(") < body.index("outcome = ingest_file("), \
        "the original must be retained BEFORE ingest_file runs"
    assert re.search(r"finally:\s*\n\s*q\.put\(None\)[^\n]*\n\s*try:\s*\n\s*os\.unlink\(tmp_path\)", body), \
        "the temp file's deletion in `finally` must be kept -- the copy is the fix, not keeping the temp"


# ── an un-migrated board ──────────────────────────────────────────────────────

@pytest.fixture()
def old_shape_db(tmp_path, monkeypatch):
    """A database the migration has NOT reached, seeded with the three shapes
    the live board carries, swapped in for one test."""
    import tools.db.storage as storage

    db = tmp_path / "old.db"
    raw = sqlite3.connect(db)
    raw.execute(OLD_SHAPE_DDL)
    raw.execute("CREATE TABLE dic_versions (version_id TEXT PRIMARY KEY, doc_id TEXT, version_no INTEGER, "
                "origin TEXT, status TEXT, created_at TEXT)")
    raw.execute("CREATE TABLE dic_chunk_links (link_id TEXT PRIMARY KEY, doc_id TEXT, version_id TEXT)")
    on_disk = tmp_path / "cli_ingested.pdf"
    on_disk.write_bytes(PDF_BYTES)
    rows = [
        ("d-absent", "gone.pdf", str(tmp_path / "Temp" / "tmpabc.pdf")),   # the 29
        ("d-source", "cli.pdf", str(on_disk)),                              # the 11
        ("d-generated", "draft.md", None),                                  # the 15
    ]
    for doc_id, fn, fp in rows:
        raw.execute(
            "INSERT INTO dic_documents (doc_id, collection_id, source_id, filename, filepath, created_at, "
            "tenant_id, classification) VALUES (?,?,?,?,?,?,?,?)",
            (doc_id, "default", "s", fn, fp, "2026-09-07T00:00:00+00:00", "default", "CUI"),
        )
    raw.commit()
    raw.close()
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    monkeypatch.setattr(storage, "DB_PATH", str(db))
    return db


def test_unmigrated_board_still_lists_and_reports_each_absence_by_kind(client, old_shape_db):
    listing = client.get("/document-intelligence/api/collections/default/documents").get_json()
    by_id = {d["doc_id"]: d for d in listing["documents"]}
    assert set(by_id) == {"d-absent", "d-source", "d-generated"}, "the SELECT must not name a missing column"
    assert by_id["d-absent"]["original_status"] == "absent"
    assert by_id["d-source"]["original_status"] == "source_on_disk"
    assert by_id["d-generated"]["original_status"] == "no_source"
    for d in by_id.values():
        assert d["original_detail"]["columns_present"] is False


def test_survey_on_unmigrated_board_states_the_columns_are_absent(old_shape_db):
    from tests._sql_compat import connect
    from tools.document_intelligence import originals

    c = connect(str(old_shape_db))
    try:
        rep = originals.survey(c)
    finally:
        c.close()
    assert rep["schema"]["original_columns_present"] is False
    assert rep["state"] == "findings"
    assert rep["counts"]["absent"] == 1 and rep["counts"]["source_on_disk"] == 1 and rep["counts"]["no_source"] == 1
    assert rep["counts"]["retained"] == 0
    assert rep["findings"] == 1 and rep["readable"] == 1


# ── the survey over a migrated board ─────────────────────────────────────────

def test_survey_counts_every_verdict_and_verify_catches_a_changed_file(tmp_path):
    from tests._sql_compat import connect
    from tools.document_intelligence import originals

    db = tmp_path / "survey.db"
    root = tmp_path / "root"
    raw = sqlite3.connect(db)
    raw.execute(OLD_SHAPE_DDL)
    raw.commit()
    raw.close()
    c = connect(str(db))
    _load_migration().up(c)

    src = tmp_path / "u.pdf"
    src.write_bytes(PDF_BYTES)
    kept = originals.retain_original(src, "u.pdf", root=root)
    src2 = tmp_path / "v.pdf"
    src2.write_bytes(PDF_BYTES + b"v")
    tampered = originals.retain_original(src2, "v.pdf", root=root)
    Path(tampered.path).write_bytes(b"not the upload")
    on_disk = tmp_path / "cli.pdf"
    on_disk.write_bytes(PDF_BYTES)

    def ins(doc_id, filepath, original_path=None, original_sha=None):
        c.execute(
            "INSERT INTO dic_documents (doc_id, collection_id, source_id, filename, filepath, created_at, "
            "tenant_id, classification, original_path, original_sha256, original_retained_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (doc_id, "default", "s", doc_id, filepath, "2026-09-07T00:00:00+00:00", "default", "CUI",
             original_path, original_sha, "2026-09-07T00:00:00+00:00" if original_path else None),
        )

    ins("retained", str(tmp_path / "gone1"), kept.path, kept.sha256)
    ins("mismatch", str(tmp_path / "gone2"), tampered.path, tampered.sha256)
    ins("missing", str(tmp_path / "gone3"), str(root / "zz" / "zz.pdf"), "zz")
    ins("source", str(on_disk))
    ins("absent", str(tmp_path / "Temp" / "tmpxyz.pdf"))
    ins("generated", None)
    c.commit()

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ICDEV_DIC_ORIGINALS_DIR", str(root))
        plain = originals.survey(c)
        verified = originals.survey(c, verify=True)
    c.close()

    assert plain["schema"]["original_columns_present"] is True
    assert plain["total_documents"] == 6
    # without --verify the tampered file reads retained: the sha was not re-derived
    assert plain["counts"] == {"retained": 2, "retained_missing": 1, "retained_mismatch": 0,
                               "source_on_disk": 1, "absent": 1, "no_source": 1}
    assert plain["verified_sha256"] is False
    assert verified["counts"]["retained"] == 1 and verified["counts"]["retained_mismatch"] == 1
    assert verified["findings"] == 3 and verified["state"] == "findings"
    assert plain["retention"]["root"] == str(root)
    assert plain["retention"]["files_retained"] == 2
    assert plain["retention"]["bytes_retained"] == len(PDF_BYTES) + len(b"not the upload")
    assert [e["doc_id"] for e in verified["examples"]["absent"]] == ["absent"]
    assert [e["doc_id"] for e in verified["examples"]["retained_mismatch"]] == ["mismatch"]
    # the human rendering names the findings
    text = originals._human(verified)
    assert "absent: absent" in text and "retained_mismatch: mismatch" in text
    json.dumps(verified, default=str)


def test_survey_over_no_documents_is_unmeasurable_not_clean(tmp_path):
    from tests._sql_compat import connect
    from tools.document_intelligence import originals

    db = tmp_path / "empty.db"
    c = connect(str(db))
    _load_migration().up(c)   # creates the full shape on a database with no table
    rep = originals.survey(c)
    c.close()
    assert rep["state"] == "unmeasurable"
    assert rep["findings"] is None and rep["readable"] is None
    assert rep["total_documents"] == 0


# ── one column list ───────────────────────────────────────────────────────────

def test_migration_module_and_ingest_ddl_declare_the_same_columns():
    from tools.document_intelligence import originals
    from tools.document_intelligence.ingest_orchestrator import _SCHEMA

    mig = _load_migration()
    assert tuple(mig.NEW_COLUMNS) == originals.ORIGINAL_COLUMNS
    assert mig.TABLE == originals.TABLE == "dic_documents"
    ddl = next(s for s in _SCHEMA if "CREATE TABLE IF NOT EXISTS dic_documents" in s)
    for name, _ in originals.ORIGINAL_COLUMNS:
        assert re.search(rf"^\s+{name}\s+TEXT", ddl, re.M), f"{name} missing from the ingest DDL"
    assert (MIGRATION_DIR / "down.py").exists()
    assert not (MIGRATION_DIR / "up.sql").exists(), "a python migration must not also carry an up.sql"


def test_retention_env_switch_vocabulary(monkeypatch):
    from tools.document_intelligence import originals

    for off in ("0", "false", "NO", " off "):
        monkeypatch.setenv("ICDEV_DIC_RETAIN_ORIGINALS", off)
        assert originals.retention_enabled() is False, off
    for on in ("1", "true", ""):
        monkeypatch.setenv("ICDEV_DIC_RETAIN_ORIGINALS", on)
        assert originals.retention_enabled() is True, on
    monkeypatch.delenv("ICDEV_DIC_RETAIN_ORIGINALS")
    assert originals.retention_enabled() is True


def test_original_verdict_never_reads_a_missing_column_as_absent():
    """A row object that lacks the retention columns entirely (an un-migrated
    SELECT) falls back to ``filepath`` rather than raising or guessing."""
    from tools.document_intelligence import originals

    row = types.SimpleNamespace(filepath=None)
    assert originals.original_verdict(row)["status"] == "no_source"
    assert originals.original_verdict({"filepath": ""})["status"] == "no_source"


# ══════════════════════════════════════════════════════════════════════════
# The uploaded name, not the temp file's (2026-09-08)
#
# `ingest_file` only ever sees the temp path, so it titles the document after
# it. The restore below it used COALESCE(NULLIF(title,''), stem), which only
# writes when the title is EMPTY -- and by then it is the temp stem, which is
# not empty. Measured on the live board: documents titled `tmpqsnpbru9`.
# ══════════════════════════════════════════════════════════════════════════
def _ingest_stub_titling(monkeypatch, title_from):
    """A stub that titles the row the way the REAL ingester does.

    `title_from` takes the temp path and returns the title to write, so a test
    can reproduce the temp-stem accident or a genuinely extracted title.
    """
    from tools.document_intelligence import ingest_orchestrator as io

    seen: dict = {}

    def fake_ingest_file(path, collection_id, *, tenant_id=None, classification=None,
                         created_by=None, progress_cb=None, **_):
        doc_id = f"doc-{uuid.uuid4().hex[:8]}"
        conn = _raw()
        conn.execute(
            "INSERT OR REPLACE INTO dic_documents (doc_id, collection_id, source_id, "
            "filename, filepath, content_type, provider, title, byte_size, "
            "content_sha256, page_count, created_at, tenant_id, classification) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, collection_id, "src", Path(path).name, str(path),
             "application/pdf", "pypdf", title_from(path), os.path.getsize(path),
             "x", 1, "2026-09-07T00:00:00+00:00", tenant_id or "default",
             classification or "CUI"),
        )
        conn.commit()
        conn.close()
        seen["doc_id"] = doc_id
        seen["tmp_stem"] = Path(path).stem
        return io.IngestOutcome(
            doc_id=doc_id, version_id=f"{doc_id}_v1", collection_id=collection_id,
            source_id="src", provider="pypdf", chunks=0, chunks_embedded=0,
            kg_entities=0, kg_relationships=0, tenant_id=tenant_id or "default",
            classification=classification or "CUI",
        )

    monkeypatch.setattr(io, "ingest_file", fake_ingest_file)
    return seen


def _title_of(doc_id):
    conn = _raw()
    row = conn.execute("SELECT title, filename FROM dic_documents WHERE doc_id = ?",
                       (doc_id,)).fetchone()
    conn.close()
    return (row[0], row[1])


def test_a_temp_stem_title_is_replaced_by_the_uploaded_name(client, monkeypatch):
    seen = _ingest_stub_titling(monkeypatch, lambda path: Path(path).stem)

    r = client.post(
        "/document-intelligence/api/ingest",
        data={"file": (__import__("io").BytesIO(PDF_BYTES), "peering-policy-update.pdf"),
              "collection_id": "default"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 202, r.get_json()
    assert _wait_result(client, r.get_json()["job_id"])["status"] == "done"

    title, filename = _title_of(seen["doc_id"])
    assert title == "peering-policy-update", f"still the temp stem: {title!r}"
    assert not title.startswith("tmp")
    assert title != seen["tmp_stem"]
    assert filename == "peering-policy-update.pdf"


def test_a_real_extracted_title_is_kept(client, monkeypatch):
    """Only the accident is overwritten. A title the extractor genuinely derived
    -- PDF metadata, a leading heading -- is the better answer and survives."""
    seen = _ingest_stub_titling(monkeypatch, lambda _p: "Peering Policy, Q3 Revision")

    r = client.post(
        "/document-intelligence/api/ingest",
        data={"file": (__import__("io").BytesIO(PDF_BYTES), "upload-2.pdf"),
              "collection_id": "default"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 202
    assert _wait_result(client, r.get_json()["job_id"])["status"] == "done"

    title, filename = _title_of(seen["doc_id"])
    assert title == "Peering Policy, Q3 Revision"
    assert filename == "upload-2.pdf", "the filename is still restored either way"


def test_an_empty_title_is_filled_from_the_upload(client, monkeypatch):
    seen = _ingest_stub_titling(monkeypatch, lambda _p: "")

    r = client.post(
        "/document-intelligence/api/ingest",
        data={"file": (__import__("io").BytesIO(PDF_BYTES), "no-title-here.pdf"),
              "collection_id": "default"},
        content_type="multipart/form-data",
    )
    assert r.status_code == 202
    assert _wait_result(client, r.get_json()["job_id"])["status"] == "done"
    assert _title_of(seen["doc_id"])[0] == "no-title-here"

