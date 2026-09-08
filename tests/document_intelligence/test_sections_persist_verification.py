# CUI // SP-CTI
"""dwr-sect-02 — a generated section's verification is PERSISTED, not merely returned.

``doc_generator`` built a per-section ``citation_report`` and a ``verified``
flag, returned both in the API payload, and wrote neither: the ``dic_sections``
INSERT named heading, content, citations_json, status and origin and nothing
else. A review surface that shows a green "verified" badge must read a COLUMN;
``generate.html`` was re-deriving it from ``status == 'approved'``, a human
publishing decision wearing a verifier's name.

WHAT IS PINNED HERE
  * the migration is a Python migration that adds EXACTLY the four columns,
    idempotently, and leaves a pre-existing row NULL (not recorded, never 0);
  * a WHITEPAPER section generated end to end lands a row whose
    ``citation_report`` is the generator's own report and whose ``verified`` /
    ``abstained`` / ``confidence`` are the verifier's;
  * a verifier that DID NOT PRODUCE A VERDICT persists NULL, and a verifier
    that ran and said no persists 0 -- the two are asserted side by side
    because conflating them is the defect;
  * ``regenerate_section`` writes the same columns through its own UPDATE;
  * the blueprint decoder keeps None apart from False, and the page reads the
    column rather than ``status``.

The database is a per-test SQLite file reached through ``ICDEV_DB_PATH``, so
``doc_generator``'s bare ``get_connection()`` lands there and never on the
checkout's ``data/icdev.db``.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.document_intelligence import doc_generator

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = ROOT / "tools" / "db" / "migrations" / "20260908003513_dic_sections_verification"

_OFF = {"cortex": {"enabled": False}}

OLD_SHAPE = """
CREATE TABLE dic_sections (
    section_id      TEXT PRIMARY KEY,
    version_id      TEXT NOT NULL,
    doc_id          TEXT NOT NULL,
    heading         TEXT NOT NULL,
    content         TEXT,
    citations_json  TEXT,
    status          TEXT DEFAULT 'draft',
    origin          TEXT DEFAULT 'ai_generated',
    created_at      TEXT NOT NULL,
    created_by      TEXT,
    tenant_id       TEXT,
    classification  TEXT,
    assigned_to     TEXT,
    reviewed_by     TEXT,
    reviewed_at     TEXT
);
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_dic_sections_verification", MIGRATION_DIR / "up.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A fresh DIC schema in a throwaway SQLite file, reached by env."""
    from tools.db.storage import get_connection
    from tools.document_intelligence import ingest_orchestrator

    path = tmp_path / "dic.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))
    conn = get_connection(db_path=str(path))
    ingest_orchestrator._ensure_schema(conn)
    conn.close()
    return path


def _rows(db_path, version_id):
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in raw.execute(
            "SELECT heading, verified, citation_report, abstained, confidence "
            "FROM dic_sections WHERE version_id = ? ORDER BY section_id",
            (version_id,),
        )]
    finally:
        raw.close()


class _Citation:
    def __init__(self, chunk_id):
        self.chunk_id = chunk_id

    def to_dict(self):
        return {"chunk_id": self.chunk_id, "doc_id": "d1", "doc_title": "Runbook", "page": 1}


class _Result:
    def __init__(self, chunk_id, content):
        self.chunk_id = chunk_id
        self.content = content
        self.doc_title = "Runbook"
        self.doc_id = "d1"
        self.page = 1
        self.citation = _Citation(chunk_id)


def _verdict(text, *, supported=True, abstained=False):
    cited = "[source:" in (text or "")
    claims = [SimpleNamespace(method="cited", supported=supported, text=text)] if cited else []
    return SimpleNamespace(
        abstained=abstained,
        verified=(cited and supported and not abstained),
        verified_text=text,
        claims=claims,
    )


@pytest.fixture()
def harness(monkeypatch):
    """Legacy retrieval + LLM + verifier stubbed; ``holder`` steers each one."""
    holder = {
        "section": "TLS 1.3 secures every enclave endpoint [source: chunk c1].",
        "verify": lambda text, contents: _verdict(text),
    }

    class _Engine:
        def __init__(self, *a, **k):
            pass

        def search(self, query, collection_id=None, top_k=10):
            return [_Result("c1", "TLS 1.3 is the mandated transport for enclave traffic.")]

    def _llm(prompt, function="document_qna", max_tokens=2048):
        if "outline" in prompt.lower():
            return json.dumps({"title": "Whitepaper", "sections": [
                {"heading": "Overview", "summary": "s"}]})
        return holder["section"]

    for name in ("tools.document_intelligence.search_engine",
                 "icdev.tools.document_intelligence.search_engine"):
        try:
            monkeypatch.setattr(importlib.import_module(name), "DICSearchEngine", _Engine, raising=False)
        except Exception:  # pragma: no cover - one tree may be absent
            pass
    for name in ("tools.document_intelligence.verifier",
                 "icdev.tools.document_intelligence.verifier"):
        try:
            monkeypatch.setattr(
                importlib.import_module(name), "verify",
                lambda text, contents: holder["verify"](text, contents), raising=False,
            )
        except Exception:  # pragma: no cover
            pass
    monkeypatch.setattr(doc_generator, "_llm_generate", _llm)
    seam = doc_generator._evidence_module()
    if seam is not None:
        monkeypatch.setattr(seam, "load_config", lambda path=None: _OFF)
    return holder


def _generate(**kwargs):
    kwargs.setdefault("template_id", "WHITEPAPER")
    return doc_generator.generate_document(
        "network transport SOP", "default", tenant_id="t1", classification="CUI", **kwargs,
    )


# ── 1. The migration ─────────────────────────────────────────────────────────

class TestMigration:
    def test_is_a_python_migration_with_no_sql_twin(self):
        assert (MIGRATION_DIR / "up.py").exists()
        assert (MIGRATION_DIR / "down.py").exists()
        # A directory carrying BOTH is ambiguous to the runner (up.sql wins).
        assert not (MIGRATION_DIR / "up.sql").exists()
        assert not (MIGRATION_DIR / "down.sql").exists()

    def test_columns_match_the_generator_declaration(self):
        mig = _load_migration()
        assert tuple(n for n, _ in mig.NEW_COLUMNS) == doc_generator.VERIFICATION_COLUMNS
        assert mig.TABLE == "dic_sections"

    def test_old_shape_gains_exactly_the_four_columns_and_a_prior_row_reads_null(self, tmp_path):
        from tools.db.storage import get_connection
        db = tmp_path / "old.db"
        raw = sqlite3.connect(db)
        raw.executescript(OLD_SHAPE)
        raw.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, created_at) "
            "VALUES ('sec_old', 'v0', 'd0', 'Overview', 't0')"
        )
        raw.commit()
        raw.close()

        mig = _load_migration()
        with get_connection(db_path=str(db)) as conn:
            before = mig.existing_columns(conn)
            mig.up(conn)
            after = mig.existing_columns(conn)
            assert after - before == {n for n, _ in mig.NEW_COLUMNS}
            mig.up(conn)  # idempotent: adds nothing, raises nothing
            assert mig.existing_columns(conn) == after
            row = dict(conn.execute(
                "SELECT verified, citation_report, abstained, confidence FROM dic_sections "
                "WHERE section_id = 'sec_old'").fetchone())
        # NOT RECORDED, never a default. A 0 here would say "verified: false"
        # about a section nobody checked.
        assert row == {"verified": None, "citation_report": None, "abstained": None, "confidence": None}

    def test_absent_table_is_left_to_the_runtime_ddl(self, tmp_path):
        from tools.db.storage import get_connection
        mig = _load_migration()
        with get_connection(db_path=str(tmp_path / "fresh.db")) as conn:
            mig.up(conn)  # no table, nothing to alter, nothing raised
            assert not mig._table_present(conn, "sqlite")

    def test_runtime_ddl_already_carries_the_columns(self, db):
        """A fresh database needs no migration: ingest_orchestrator's DDL has them."""
        mig = _load_migration()
        raw = sqlite3.connect(db)
        cols = {r[1] for r in raw.execute("PRAGMA table_info(dic_sections)")}
        raw.close()
        assert {n for n, _ in mig.NEW_COLUMNS} <= cols

    def test_down_drops_exactly_the_added_columns(self, tmp_path):
        from tools.db.storage import get_connection
        db = tmp_path / "down.db"
        raw = sqlite3.connect(db)
        raw.executescript(OLD_SHAPE)
        raw.commit()
        raw.close()
        mig = _load_migration()
        with get_connection(db_path=str(db)) as conn:
            before = mig.existing_columns(conn)
            mig.up(conn)
            mig.down(conn)
            assert mig.existing_columns(conn) == before


# ── 2. The row carries its own verification ──────────────────────────────────

def test_a_whitepaper_section_row_carries_its_own_citation_report(db, harness):
    """THE DONE CRITERION. Generate a WHITEPAPER; read the rows back from the
    table, not the payload."""
    result = _generate()
    assert not result.error, result.error
    assert result.outline_source == "contract:WHITEPAPER"

    rows = _rows(db, result.version_id)
    assert len(rows) == len(result.sections) == 9  # the declared skeleton, whole

    payload_by_heading = {s.heading: s for s in result.sections}
    for row in rows:
        report = json.loads(row["citation_report"])
        assert report["evidence_path"] == "legacy"
        assert report["valid"] is True
        assert report["verification"] == {"ran": True, "error": None}
        assert row["verified"] == 1
        assert row["abstained"] == 0
        assert row["confidence"] == 1.0
        # The row and the response say the SAME thing -- one report, persisted.
        assert report == payload_by_heading[row["heading"]].citation_report


def test_verified_is_null_when_the_verifier_produced_no_verdict(db, harness):
    """A verifier that raised is not a pass and not a fail: NO verdict exists.

    Persisting 0 here would render the section beside one the verifier
    rejected, and the two need different repairs (an outage vs. a bad draft).
    The report says WHY it is NULL.
    """
    def _boom(text, contents):
        raise RuntimeError("verifier backend unavailable")
    harness["verify"] = _boom

    result = _generate()
    assert not result.error, result.error
    rows = _rows(db, result.version_id)
    assert rows
    for row in rows:
        assert row["verified"] is None
        assert row["confidence"] is None
        report = json.loads(row["citation_report"])
        assert report["verification"]["ran"] is False
        assert "verifier backend unavailable" in report["verification"]["error"]
    # The in-memory verdict agrees: None, not False.
    assert all(s.verified is None for s in result.sections)


def test_a_failed_verdict_is_zero_never_null(db, harness):
    """The discriminating half: the verifier RAN and a cited claim did not hold.

    This must land as 0 with a measured confidence, so that a NULL above can
    never be mistaken for it.
    """
    harness["verify"] = lambda text, contents: _verdict(text, supported=False)

    result = _generate()
    assert not result.error, result.error
    rows = _rows(db, result.version_id)
    assert rows
    for row in rows:
        assert row["verified"] == 0
        assert row["confidence"] == 0.0
        assert row["abstained"] == 1  # 0.0 is below the abstain floor
        assert json.loads(row["citation_report"])["verification"]["ran"] is True


def test_regenerate_section_writes_the_same_columns(db, harness):
    """The second writer. Its UPDATE names the columns, and the response carries
    the report the row does."""
    from tools.db.storage import get_connection

    doc_id, version_id = f"doc-{uuid.uuid4().hex[:8]}", f"ver-{uuid.uuid4().hex[:8]}"
    with get_connection(db_path=str(db)) as conn:
        conn.execute(
            "INSERT INTO dic_documents (doc_id, collection_id, source_id, filename, content_type, "
            "provider, title, byte_size, content_sha256, page_count, created_at, tenant_id, classification) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (doc_id, "default", doc_id, "a.md", "text/markdown", "t", "Doc", 1, "x", 1, "t0", "t1", "CUI"),
        )
        conn.execute(
            "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, content_sha256, "
            "created_at, created_by, tenant_id, classification) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (version_id, doc_id, 1, "ai_generated", "pending_review", "x", "t0", "a", "t1", "CUI"),
        )
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            ("sec-1", version_id, doc_id, "Overview", "old prose", "t0"),
        )
        conn.commit()

    out = doc_generator.regenerate_section(version_id, "Overview", "default", tenant_id="t1")
    assert "error" not in out, out
    assert out["verified"] is True
    assert out["confidence"] == 1.0

    (row,) = _rows(db, version_id)
    assert row["verified"] == 1
    assert row["abstained"] == 0
    assert row["confidence"] == 1.0
    report = json.loads(row["citation_report"])
    assert report["verification"] == {"ran": True, "error": None}
    assert report == out["citation_report"]


def test_a_currency_trip_downgrades_a_verdict_but_never_invents_one():
    """`_verification_values` is the one place the tuple is built."""
    vals = doc_generator._verification_values
    assert vals(None, {}, False, 1.0) == (None, "{}", 0, None)
    assert vals(True, {"k": 1}, False, 0.876543) == (1, '{"k": 1}', 0, 0.877)
    assert vals(False, {}, True, 0.0) == (0, "{}", 1, 0.0)


# ── 3. The reader keeps None apart from False ────────────────────────────────

def test_blueprint_decoder_keeps_not_checked_apart_from_failed():
    from tools.document_intelligence import blueprint as bp

    rows = [
        {"verified": None, "abstained": None, "citation_report": None},
        {"verified": 0, "abstained": 1, "citation_report": '{"valid": false}'},
        {"verified": 1, "abstained": 0, "citation_report": '{"valid": true}'},
        {"verified": 1, "abstained": 0, "citation_report": "not json"},
    ]
    bp._attach_section_verification(rows)
    assert [r["verified"] for r in rows] == [None, False, True, True]
    assert [r["abstained"] for r in rows] == [None, True, False, False]
    assert rows[0]["citation_report"] is None
    assert rows[1]["citation_report"] == {"valid": False}
    assert rows[3]["citation_report"] is None


def test_both_section_readers_select_the_columns_and_the_page_reads_them():
    """Structural: the two SELECTs ask for the columns through ONE spelling, and
    the page no longer manufactures `verified` from `status`."""
    src = (ROOT / "tools" / "document_intelligence" / "blueprint.py").read_text(encoding="utf-8")
    assert src.count("+ SECTION_VERIFICATION_SELECT") == 2
    html = (ROOT / "tools" / "dashboard" / "templates" / "document_intelligence" / "generate.html")
    page = html.read_text(encoding="utf-8")
    assert "verified: s.status === 'approved'" not in page
    assert "s.verified === true" in page and "s.verified === false" in page
