# CUI // SP-CTI
"""dwr-anchor-02 — docmod_findings persists the anchor.

dwr-anchor-01 made every pack keep the chunk-local span it computes;
``scanner._insert_finding`` then dropped it because the table had no column.
Now a finding row carries ``anchor_start`` / ``anchor_end`` / ``anchor_text``,
and the invariant a reviewer surface can rely on is asserted against the
PERSISTED row, not the in-memory entity:

    chunk_text[anchor_start:anchor_end] == anchor_text

Three ways a row is legitimately NOT anchored, each persisted as NULL and
never as ``0``: the entity carries no span (evidence_currency), the span does
not slice to ``raw_match`` (refused — a wrong highlight is worse than none),
and a superseding row written by the resolver (no entity at all).

The migration half is exercised on a throwaway SQLite database shaped by
migration 257: the columns are added, a second run is a no-op, and a database
with no table is left alone.
"""
from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = (
    REPO_ROOT / "tools" / "db" / "migrations" / "20260907221048_docmod_findings_anchor"
)
DDL_257 = REPO_ROOT / "tools" / "db" / "migrations" / "257_doc_modernization.sql"

ANCHOR_COLUMNS = ("anchor_start", "anchor_end", "anchor_text")

# ── fixtures (the test_core_engine shape, per-file SQLite via tests/docmod/conftest) ──

_DDL = [
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
]
_DOCMOD_DDL_KEYS = (
    "docmod_scan_runs", "docmod_findings", "docmod_doc_scan_state",
    "docmod_catalog_entries", "docmod_catalog_audit",
)


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
    conn.commit()
    conn.close()
    yield


def _seed_doc(text_sections: list[tuple[str, str]]) -> str:
    from tools.db.storage import get_connection

    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    version_id = f"{doc_id}_v1"
    conn = get_connection()
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, status, origin, created_at) "
        "VALUES (%s,'col-t','T','approved','human_authored','2026-01-01')",
        (doc_id,),
    )
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at) "
        "VALUES (%s,%s,1,'human_authored','approved','2026-01-01')",
        (version_id, doc_id),
    )
    for i, (heading, content) in enumerate(text_sections):
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, created_at) "
            "VALUES (%s,%s,%s,%s,%s,'2026-01-01')",
            (f"{doc_id}-s{i:03d}", version_id, doc_id, heading, content),
        )
    conn.commit()
    conn.close()
    return doc_id


def _finding_rows(doc_id: str) -> list[dict]:
    from tools.db.storage import get_connection

    conn = get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM docmod_findings WHERE doc_id = %s ORDER BY created_at, finding_id",
        (doc_id,),
    ).fetchall()]
    conn.close()
    return rows


class _TlsPack:
    """Deterministic pack flagging 'TLS 1.1'; ``span`` picks how it anchors."""

    PATTERN = re.compile(r"\s*TLS 1\.1\s*")  # admits whitespace: match_span must trim

    def __init__(self, span: str = "real", label: str = "TLS 1.1"):
        self.pack_id = "tls_anchor_stub"
        self.entity_types = ["protocol"]
        self.config = {}
        self._span = span
        self._label = label

    def extract(self, text, chunk_ref):
        from tools.doc_modernization.base_pack import CandidateEntity, match_span

        out = []
        for m in self.PATTERN.finditer(text):
            raw = m.group(0).strip()
            if self._span == "real":
                start, end = match_span(m)
            elif self._span == "none":
                start, end = None, None
            elif self._span == "wrong":
                start, end = 0, len(raw)          # claims the chunk STARTS with it
            else:  # pragma: no cover
                raise AssertionError(self._span)
            out.append(CandidateEntity(
                label=self._label, entity_type="protocol", pack_id=self.pack_id,
                chunk_ref=chunk_ref, raw_match=raw, span_start=start, span_end=end,
            ))
        return out

    def evaluate(self, entity, conn):
        from tools.doc_modernization.base_pack import Verdict

        return Verdict(
            currency_verdict="deprecated", finding_type="deprecated_tech",
            severity="high", rationale="TLS 1.1 is deprecated", confidence=1.0,
            evidence=[{"source": "rule:crypto-tls-01", "detail": "TLS <1.2", "date": ""}],
        )

    def recommend(self, entity, verdict, conn):
        return None

    def evidence_snapshot(self, conn):
        return "static-evidence-v1"


# ── the scan persists the anchor ─────────────────────────────────────────────

SECTION = "Legacy hosts still negotiate TLS 1.1 with the proxy tier."


def test_scan_persists_anchor_equal_to_chunk_slice(db):
    from tools.doc_modernization.scanner import scan_document

    doc_id = _seed_doc([("Transport", SECTION)])
    result = scan_document(doc_id, packs={"tls_anchor_stub": _TlsPack("real")})
    assert result["findings_new"] == 1

    rows = _finding_rows(doc_id)
    assert len(rows) == 1
    row = rows[0]
    for col in ANCHOR_COLUMNS:
        assert col in row, f"row lacks {col}: {sorted(row)}"

    # The DONE criterion, on the persisted row against the seeded chunk.
    assert row["anchor_text"] == "TLS 1.1"
    assert SECTION[row["anchor_start"]:row["anchor_end"]] == row["anchor_text"]
    assert row["anchor_start"] == SECTION.index("TLS 1.1")
    assert row["anchor_end"] == row["anchor_start"] + len("TLS 1.1")


def test_entity_with_no_span_persists_null_never_zero(db):
    from tools.doc_modernization.scanner import scan_document

    doc_id = _seed_doc([("Transport", SECTION)])
    result = scan_document(doc_id, packs={"tls_anchor_stub": _TlsPack("none")})
    assert result["findings_new"] == 1
    row = _finding_rows(doc_id)[0]
    assert (row["anchor_start"], row["anchor_end"], row["anchor_text"]) == (None, None, None)


def test_span_that_does_not_slice_to_raw_match_is_refused_not_persisted(db):
    """A wrong anchor is worse than none: the finding lands, the anchor does not."""
    from tools.doc_modernization.scanner import scan_document

    doc_id = _seed_doc([("Transport", SECTION)])
    result = scan_document(doc_id, packs={"tls_anchor_stub": _TlsPack("wrong")})
    assert result["findings_new"] == 1
    row = _finding_rows(doc_id)[0]
    assert row["entity_label"] == "TLS 1.1"
    assert (row["anchor_start"], row["anchor_end"], row["anchor_text"]) == (None, None, None)


def test_superseding_row_carries_no_anchor(db):
    """The resolver's supersede row has no entity and therefore no anchor;
    the row it supersedes keeps the one it was written with (append-only)."""
    from tools.doc_modernization.scanner import scan_document

    doc_id = _seed_doc([("Transport", SECTION)])
    pack = _TlsPack("real")
    scan_document(doc_id, packs={"tls_anchor_stub": pack})

    # A new approved version without the entity -> a superseding row.
    from tools.db.storage import get_connection

    conn = get_connection()
    v2 = f"{doc_id}_v2"
    conn.execute(
        "INSERT INTO dic_versions (version_id, doc_id, version_no, origin, status, created_at) "
        "VALUES (%s,%s,2,'human_authored','approved','2026-02-01')",
        (v2, doc_id),
    )
    conn.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, created_at) "
        "VALUES (%s,%s,%s,'Transport','Legacy hosts now negotiate TLS 1.3.','2026-02-01')",
        (f"{doc_id}-v2-s000", v2, doc_id),
    )
    conn.commit()
    conn.close()
    r2 = scan_document(doc_id, packs={"tls_anchor_stub": pack})
    assert r2["scanned"] is True
    assert r2["findings_resolved"] == 1

    rows = _finding_rows(doc_id)
    by_state = {r["state"]: r for r in rows}
    assert set(by_state) == {"open", "superseded"}
    assert by_state["open"]["anchor_text"] == "TLS 1.1"
    sup = by_state["superseded"]
    assert sup["supersedes_id"] == by_state["open"]["finding_id"]
    assert (sup["anchor_start"], sup["anchor_end"], sup["anchor_text"]) == (None, None, None)


# ── finding_anchor, the one derivation ───────────────────────────────────────

class _Entity:
    def __init__(self, raw="TLS 1.1", start=None, end=None):
        self.raw_match = raw
        self.span_start = start
        self.span_end = end
        self.pack_id = "unit"


@pytest.mark.parametrize("start,end", [
    (None, None),        # not anchored
    (None, 5),           # half a span is no span
    (-1, 6),             # before the text
    (30, 37),            # starts inside, but the slice is not raw_match
    (0, 0),              # empty
    (7, 2),              # inverted
    (0, 10_000),         # past the end
    (None, None),
])
def test_finding_anchor_refuses_every_untrustworthy_span(start, end):
    from tools.doc_modernization.scanner import finding_anchor

    assert finding_anchor(_Entity(start=start, end=end), SECTION) == (None, None, None)


def test_finding_anchor_returns_the_slice_of_the_text_it_was_given():
    from tools.doc_modernization.scanner import finding_anchor

    s = SECTION.index("TLS 1.1")
    assert finding_anchor(_Entity(start=s, end=s + 7), SECTION) == (s, s + 7, "TLS 1.1")
    # No text to anchor into -> not anchored, whatever the entity claims.
    assert finding_anchor(_Entity(start=s, end=s + 7), None) == (None, None, None)


# ── the migration ────────────────────────────────────────────────────────────

def _load_migration(name: str):
    path = MIGRATION_DIR / name
    spec = importlib.util.spec_from_file_location(f"docmod_anchor_{name[:-3]}", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def shaped_257(tmp_path):
    """A throwaway SQLite database carrying docmod_findings AS 257 DECLARED IT —
    the live-board shape, with no anchor columns."""
    import sqlite3

    db_path = tmp_path / "m.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(DDL_257.read_text(encoding="utf-8"))
    raw.commit()
    raw.close()

    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(db_path))
    yield conn
    conn.close()


def test_migration_is_a_python_migration_with_both_halves():
    assert (MIGRATION_DIR / "up.py").exists()
    assert (MIGRATION_DIR / "down.py").exists()
    # up.py runs ONLY when no up.sql shadows it (migration_runner.apply).
    assert not (MIGRATION_DIR / "up.sql").exists()
    assert not (MIGRATION_DIR / "down.sql").exists()
    assert (MIGRATION_DIR / "meta.json").exists()


def test_migration_adds_the_three_columns_and_is_idempotent(shaped_257):
    up = _load_migration("up.py")
    before = up.existing_columns(shaped_257)
    assert not set(ANCHOR_COLUMNS) & before, "257 must not already carry the columns"

    up.up(shaped_257)
    after = up.existing_columns(shaped_257)
    assert set(ANCHOR_COLUMNS) <= after
    assert after - before == set(ANCHOR_COLUMNS)

    up.up(shaped_257)  # second run: nothing to add, nothing raised
    assert up.existing_columns(shaped_257) == after

    # Nullable with no default: a row that names none of them reads NULL.
    shaped_257.execute("INSERT INTO docmod_scan_runs (run_id) VALUES ('run-1')")
    shaped_257.execute(
        "INSERT INTO docmod_findings (finding_id, run_id, doc_id, pack_id, entity_label, "
        "entity_type, finding_type, dedupe_key) "
        "VALUES ('f-1','run-1','d-1','p','TLS 1.1','protocol','deprecated_tech','k')"
    )
    row = dict(shaped_257.execute(
        "SELECT anchor_start, anchor_end, anchor_text FROM docmod_findings WHERE finding_id='f-1'"
    ).fetchone())
    assert row == {"anchor_start": None, "anchor_end": None, "anchor_text": None}


def test_migration_leaves_a_database_without_the_table_alone(tmp_path):
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(tmp_path / "empty.db"))
    try:
        up = _load_migration("up.py")
        up.up(conn)  # no raise, no table invented
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='docmod_findings'"
        ).fetchone() is None
    finally:
        conn.close()


def test_conftest_schema_declares_the_columns():
    """Fresh test databases come from MINIMAL_ICDEV_SCHEMA, not the migration."""
    from tests.conftest import MINIMAL_ICDEV_SCHEMA

    block = next(
        s for s in MINIMAL_ICDEV_SCHEMA.split(";")
        if "CREATE TABLE IF NOT EXISTS docmod_findings" in s
    )
    for col in ANCHOR_COLUMNS:
        assert col in block, col
