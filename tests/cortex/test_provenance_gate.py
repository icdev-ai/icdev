# CUI // SP-CTI
"""The Cortex provenance gate must LAND a row, not merely run (cxo-trust-04).

Every existing test of gate 7 patches ``_gate_register_provenance`` with a fake
that returns a canned ``"scr-test123"`` (see the ``calls`` fixture in
``test_governance_pipeline.py``) and then asserts the gate was *invoked*. That
shape is what let cxo-trust-01 ship and sit undetected: Cortex passed
``citation_type="cortex"``, which was absent from ``CITATION_TYPES``, so
``register_citation`` raised ``ValueError`` before the INSERT, the gate caught it
and recorded ``provenance="warn"`` — and the tests, which never looked at the
database, stayed green. Measured 2026-08-02 against live PostgreSQL: 0 of 285
registry rows were type ``cortex`` and no Cortex operation had ever recorded a
clean pass. Same failure shape as
``tests/test_audit_trail_schema.py``'s stubbed-audit problem: a stub cannot
observe a write that never happens.

So these tests run gates 1-6 against fakes (offline, no gateway/Presidio) and
leave **gates 7a and 7b real**, pointed at a temp SQLite database. The
assertions are reads:

  a. ``source_citation_registry`` holds a row with ``citation_type='cortex'``
     whose ``source_hash`` is the sha256 of the governed output.
  b. the ``cortex_audit`` row for that same call reports
     ``gates_json.outcomes.provenance == 'pass'`` and carries the registry id in
     ``provenance_id`` — which is what joins (a) and (b) to one call.

``test_pre_fix_vocabulary_lands_nothing`` restores the pre-cxo-trust-01
vocabulary and asserts both reads go the other way, so the tests above are
proven to discriminate rather than merely to pass.
"""
from __future__ import annotations

import hashlib
import importlib
import json

import pytest

from tools.cortex import governance
from tools.cortex.governance import (
    GATE_PROVENANCE,
    OUTCOME_PASS,
    GovernancePipeline,
)
from tools.cortex.schemas import CortexContext, CortexResult

registry = importlib.import_module("tools.provenance.registry")
citation_types = importlib.import_module("tools.provenance.citation_types")
cortex_db = importlib.import_module("tools.cortex.db.init_db")

OUTPUT_TEXT = "Accounts are disabled after 90 days [source: 1]."


def _pre_ok():
    return {
        "allowed": True,
        "warnings": [],
        "blocked_reason": None,
        "injection_score": 0.0,
        "pii_labels": [],
        "request_id": "gw_test",
    }


@pytest.fixture
def provenance_db(tmp_path, monkeypatch):
    """A temp SQLite DB carrying both tables gate 7 writes to.

    ``ICDEV_DB_PATH`` steers ``get_connection()`` (used by ``record_audit``) and
    ``registry.DB_PATH`` steers ``register_citation``, whose signature the
    governance gate does not thread a path through. Both must name the SAME file
    or the provenance_id join in (b) has nothing to join against. Pointing them
    at one path also keeps ``get_connection`` off its dedicated-canvas-file
    branch, so the RLS-aware connection under test is the production one.

    The registry CHECK clause is rendered from ``sqlite_check_clause()`` rather
    than retyped, so the constraint this test inserts against is the one the
    migration ships.
    """
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    monkeypatch.setattr(registry, "DB_PATH", db_path)
    # Module-level latch: a previous test in this process may have set it True
    # against a different database, which would skip DDL on this one.
    monkeypatch.setattr(cortex_db, "_SCHEMA_ENSURED", False)

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS source_citation_registry ("
            " id TEXT PRIMARY KEY,"
            " citation_type TEXT NOT NULL "
            + citation_types.sqlite_check_clause()
            + ", source_table TEXT NOT NULL,"
            " source_record_id TEXT NOT NULL,"
            " source_doc TEXT,"
            " source_hash TEXT NOT NULL,"
            " anchor_hash TEXT,"
            " merkle_root TEXT,"
            " blockchain_tx_id TEXT,"
            " classification TEXT DEFAULT 'CUI',"
            " project_id TEXT,"
            " trust_score REAL DEFAULT 0.0,"
            " created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()

    cortex_db.init_db()
    return db_path


@pytest.fixture
def govern(monkeypatch, provenance_db):
    """Run one real governed Cortex call; return its GovernanceReport.

    Gates 1-6 are faked so the test needs no gateway config, no Presidio model
    and no network. Gates 7a/7b are deliberately NOT patched — they are the
    subject.
    """
    monkeypatch.setattr(governance, "_gate_check_text", lambda text: _pre_ok())
    monkeypatch.setattr(governance, "_gate_redact_input", lambda text, classification: (text, 0))
    monkeypatch.setattr(governance, "_gate_redact_output", lambda text: (text, []))

    def _run(text: str = OUTPUT_TEXT):
        pipeline = GovernancePipeline(operation="cortex.complete")
        ctx = CortexContext(tenant_id="tenant-cxo", classification="CUI")
        _result, report = pipeline.wrap(
            lambda prompt: CortexResult(text=text),
            ctx,
            prompt="when are accounts disabled?",
            context_sources=[{"source_id": "1", "content": text}],
            retrieval=True,
        )
        return report

    return _run


def _registry_rows(citation_type: str = "cortex"):
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, citation_type, source_table, source_doc, source_hash, "
            "classification, project_id FROM source_citation_registry "
            "WHERE citation_type = %s",
            (citation_type,),
        )
        cols = ["id", "citation_type", "source_table", "source_doc", "source_hash",
                "classification", "project_id"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _audit_rows():
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, function, tenant_id, gates_json, outcome, provenance_id "
            "FROM cortex_audit ORDER BY created_at"
        )
        cols = ["id", "function", "tenant_id", "gates_json", "outcome", "provenance_id"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (a) the registry row landed
# ---------------------------------------------------------------------------
def test_governed_call_writes_a_cortex_registry_row(govern):
    """The read cxo-trust-01 would have failed: SELECT, not "the gate ran"."""
    govern()

    rows = _registry_rows("cortex")
    assert len(rows) == 1, (
        "a governed Cortex call wrote no source_citation_registry row of type "
        "'cortex' — this is cxo-trust-01, where the gate reported warn and the "
        "table stayed empty for the subsystem's entire existence"
    )
    assert rows[0]["source_table"] == "cortex_governance"
    assert rows[0]["source_doc"] == "cortex.complete"


def test_the_registry_row_describes_the_actual_output(govern):
    """A row that landed but does not hash THIS output proves nothing."""
    govern()

    row = _registry_rows("cortex")[0]
    assert row["source_hash"] == hashlib.sha256(OUTPUT_TEXT.encode("utf-8")).hexdigest()
    assert row["classification"] == "CUI"
    assert row["project_id"] == "tenant-cxo"


# ---------------------------------------------------------------------------
# (b) the audit row says pass, and points at that registry row
# ---------------------------------------------------------------------------
def test_cortex_audit_records_provenance_pass(govern):
    """95 warn vs 14 pass was the live signal. A clean call must record pass."""
    report = govern()
    assert report.outcomes[GATE_PROVENANCE] == OUTCOME_PASS

    rows = _audit_rows()
    assert len(rows) == 1, "a governed call must persist exactly one cortex_audit row"
    outcomes = json.loads(rows[0]["gates_json"])["outcomes"]
    assert outcomes[GATE_PROVENANCE] == OUTCOME_PASS, (
        f"cortex_audit recorded provenance={outcomes.get(GATE_PROVENANCE)!r}; "
        "'warn' is the cxo-trust-01 signature and 'fail' is cxo-trust-03's "
        "misconfiguration signal — neither is a working provenance gate"
    )


def test_audit_row_and_registry_row_are_the_same_call(govern):
    """provenance_id is the join. Without it (a) and (b) are two coincidences."""
    govern()

    audit = _audit_rows()[0]
    registry_row = _registry_rows("cortex")[0]
    assert audit["provenance_id"] == registry_row["id"]
    assert audit["function"] == "cortex.complete"
    assert audit["tenant_id"] == "tenant-cxo"


# ---------------------------------------------------------------------------
# discrimination — the same reads must fail on the pre-fix tree
# ---------------------------------------------------------------------------
def test_pre_fix_vocabulary_lands_nothing(govern, monkeypatch):
    """Restore the pre-cxo-trust-01 vocabulary; both reads must flip.

    ``is_valid`` reads the module global at call time, so dropping 'cortex' from
    it reproduces the shipped bug exactly: ValueError before the INSERT, caught
    by the gate, no row, and an audit outcome that is not 'pass'. A regression
    test that cannot fail on the broken tree is not a regression test.
    """
    monkeypatch.setattr(
        citation_types,
        "CITATION_TYPES",
        tuple(t for t in citation_types.CITATION_TYPES if t != "cortex"),
    )

    report = govern()

    assert _registry_rows("cortex") == []
    assert report.outcomes[GATE_PROVENANCE] != OUTCOME_PASS
    outcomes = json.loads(_audit_rows()[0]["gates_json"])["outcomes"]
    assert outcomes[GATE_PROVENANCE] != OUTCOME_PASS
