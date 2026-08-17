# CUI // SP-CTI
"""A RAG retrieval persists provenance to ``rag_provenance_ledger`` (cef-fnd-05).

The defect these tests pin down
-------------------------------
``rag_provenance_ledger`` held **0 rows against 2,430 rows in
``rag_retrieval_log``** on the live board. It shipped with three READERS
(``dic/provenance_adapter``, ``genesis/reflexes/aidp_monitor``,
``quality/citation_grounding``) and **no writer anywhere in the tree**, so a
citation produced from a retrieved chunk could not be traced back to its source
record after the fact — the TRUST invariant's second half.

Three distinct failure modes are covered, because all three were live at once
and fixing only the first would have left the table empty and the suite green:

1. **The missing call.** ``RAGRetriever.search()`` step 7 was named "record
   provenance" and wrote to a DIFFERENT store (PROV-AGENT), which is why the gap
   was invisible on inspection.
2. **The vocabulary.** ``event_type``'s CHECK admitted only
   ``('ingest','chain_of_custody')``. Writing ``'retrieval'`` raised a CHECK
   violation — which the caller's ``except Exception: pass`` would have
   swallowed, leaving 0 rows and green tests. Migration 20260815002727 fixed
   this exact shape one table over. The parity tests here keep the Python
   constant and every DDL copy in step.
3. **The silent swallow.** ``_record_provenance`` ended in ``except Exception:
   pass``. A provenance write that fails must stay non-blocking but must be
   LOGGED.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools.rag.provenance_ledger import (
    PROVENANCE_EVENT_TYPES,
    ProvenanceWriteError,
    lineage_for_chunk,
    record_retrieval,
    source_uuid,
)
from tools.rag.vector_store_provider import SearchResult

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── harness ─────────────────────────────────────────────────────────────────


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    """SQLite DB carrying rag_provenance_ledger + rag_retrieval_log.

    Goes through ``storage.get_connection()`` rather than raw sqlite3 so the
    writer's own ``%s`` placeholders hit the same translation production does —
    a test that talks straight to sqlite3 can pass while the real INSERT is
    broken. Setting the env vars (rather than patching a module object) also
    keeps this correct for both the ``tools.`` and ``icdev.tools.`` aliases of
    ``storage``, which are distinct module objects.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "ledger_test.db"))

    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    conn = get_connection()
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.commit()
    try:
        yield conn
    finally:
        try:
            conn.commit()
        except Exception:
            pass
        conn.close()


def _result(chunk_id="chunk-1", source_type="doc", source_id="src-9", content="alpha"):
    return SearchResult(
        chunk_id=chunk_id,
        content=content,
        source_type=source_type,
        source_id=source_id,
        source_table="rag_chunks",
        # Both: search() re-seeds final_score from score before fusion, so a
        # stub that sets only final_score is filtered out by min_score_threshold.
        score=0.87,
        final_score=0.87,
        classification="CUI",
    )


def _new_retrieval_event(conn, query_hash="qh-1") -> int:
    cur = conn.execute(
        """INSERT INTO rag_retrieval_log (query_hash, results_count, retrieval_mode)
           VALUES (%s, %s, %s) RETURNING id""",
        (query_hash, 1, "rrf_hybrid"),
    )
    row = cur.fetchone()
    conn.commit()
    return int(row[0])


# ── 1. a retrieval writes a ledger row ──────────────────────────────────────


def test_retrieval_writes_a_provenance_row(ledger_db):
    """The headline: retrieval produces at least one rag_provenance_ledger row."""
    event_id = _new_retrieval_event(ledger_db)

    written = record_retrieval(
        [_result()],
        query="what is AC-2",
        retrieval_log_id=event_id,
        retrieval_mode="rrf_hybrid",
        conn=ledger_db,
    )

    assert written == 1
    rows = ledger_db.execute(
        "SELECT COUNT(*) FROM rag_provenance_ledger WHERE event_type = %s",
        ("retrieval",),
    ).fetchone()
    assert rows[0] == 1


def test_row_links_chunk_to_source_to_retrieval_event(ledger_db):
    """chunk -> source -> retrieval event, and queryable after the fact."""
    event_id = _new_retrieval_event(ledger_db, query_hash="qh-link")
    record_retrieval(
        [_result(chunk_id="chunk-42", source_type="policy", source_id="doc-7")],
        query="link me",
        retrieval_log_id=event_id,
        retrieval_mode="reranked",
        model_id="stub-embed",
        conn=ledger_db,
    )

    row = ledger_db.execute(
        """SELECT chunk_uuid, parent_doc_uuid, retrieval_log_id, sha256_hash,
                  model_id, hyperparams_json, event_type
           FROM   rag_provenance_ledger
           WHERE  chunk_uuid = %s""",
        ("chunk-42",),
    ).fetchone()

    assert row is not None, "no ledger row for the retrieved chunk"
    chunk_uuid, parent_doc, log_id, sha, model_id, hyper, event_type = row
    assert chunk_uuid == "chunk-42"                       # -> chunk
    assert parent_doc == source_uuid("policy", "doc-7")   # -> source
    assert log_id == event_id                             # -> retrieval event
    assert event_type == "retrieval"
    assert sha and len(sha) == 64
    assert model_id == "stub-embed"
    assert json.loads(hyper)["retrieval_mode"] == "reranked"


def test_the_link_joins_back_to_the_retrieval_log(ledger_db):
    """The FK is usable: a JOIN recovers the query that served the chunk."""
    event_id = _new_retrieval_event(ledger_db, query_hash="qh-join")
    record_retrieval([_result(chunk_id="c-join")], retrieval_log_id=event_id, conn=ledger_db)

    row = ledger_db.execute(
        """SELECT rrl.query_hash, rrl.retrieval_mode
           FROM   rag_provenance_ledger rpl
           JOIN   rag_retrieval_log rrl ON rrl.id = rpl.retrieval_log_id
           WHERE  rpl.chunk_uuid = %s""",
        ("c-join",),
    ).fetchone()
    assert row is not None, "provenance row does not join to its retrieval event"
    assert row[0] == "qh-join"


def test_lineage_is_queryable_after_the_fact(ledger_db):
    event_id = _new_retrieval_event(ledger_db)
    record_retrieval([_result(chunk_id="c-lin")], query="q", retrieval_log_id=event_id, conn=ledger_db)

    lineage = lineage_for_chunk("c-lin", conn=ledger_db)
    assert len(lineage) == 1
    assert lineage[0]["parent_doc_uuid"] == source_uuid("doc", "src-9")
    assert lineage[0]["retrieval_log_id"] == event_id


def test_one_row_per_retrieved_chunk(ledger_db):
    event_id = _new_retrieval_event(ledger_db)
    results = [_result(chunk_id=f"c-{i}") for i in range(3)]
    assert record_retrieval(results, retrieval_log_id=event_id, conn=ledger_db) == 3

    ranks = [
        json.loads(r[0])["rank"]
        for r in ledger_db.execute(
            "SELECT hyperparams_json FROM rag_provenance_ledger ORDER BY id"
        ).fetchall()
    ]
    assert ranks == [1, 2, 3], "rank must record the position the chunk was served at"


def test_query_text_is_never_stored(ledger_db):
    """D282: the query is hashed, never persisted in the clear."""
    event_id = _new_retrieval_event(ledger_db)
    secret = "classified program name"
    record_retrieval([_result()], query=secret, retrieval_log_id=event_id, conn=ledger_db)

    dumped = " ".join(
        str(v)
        for row in ledger_db.execute("SELECT * FROM rag_provenance_ledger").fetchall()
        for v in row
    )
    assert secret not in dumped


def test_prompt_sha256_matches_the_retrieval_log_query_hash(ledger_db):
    """The second path back to the event, for when retrieval_log_id is NULL.

    A ledger row written after the rag_retrieval_log INSERT failed is still
    attributable: prompt_sha256 is computed exactly as _log_retrieval computes
    query_hash.
    """
    import hashlib

    query = "AC-2 account management"
    expected = hashlib.sha256(query.encode("utf-8")).hexdigest()

    record_retrieval([_result()], query=query, retrieval_log_id=None, conn=ledger_db)
    row = ledger_db.execute(
        "SELECT prompt_sha256, retrieval_log_id FROM rag_provenance_ledger"
    ).fetchone()
    assert row[0] == expected
    assert row[1] is None, "an unlinked row must be honest about it, not invent an id"


def test_no_results_writes_nothing(ledger_db):
    assert record_retrieval([], query="q", conn=ledger_db) == 0


# ── 1b. end to end: search() itself writes it ───────────────────────────────


def test_a_full_search_writes_the_ledger_row(ledger_db, tmp_path, monkeypatch):
    """The acceptance criterion, driven through RAGRetriever.search().

    This is the test that fails against the pre-cef-fnd-05 tree for the reason
    that matters: step 7 was wired to PROV-AGENT only, so no amount of
    retrieving produced a rag_provenance_ledger row. Embedding and the vector
    store are stubbed — the assertion is about persistence, not about ranking.
    """
    from tools.rag import retriever as rt

    class _Provider:
        model_id = "stub-embed-model"

        def embed(self, _text):
            return [0.1, 0.2, 0.3]

    class _Store:
        def search(self, _emb, top_k=5, filters=None):
            return [_result(chunk_id="e2e-chunk", source_type="ssp", source_id="doc-e2e")]

    monkeypatch.setattr(rt, "_get_embedding_provider", lambda: _Provider())
    monkeypatch.setattr(rt.VectorStoreFactory, "create", staticmethod(lambda **_k: _Store()))
    # _log_retrieval short-circuits on a missing ICDEV_DB; point it at the
    # fixture database so the retrieval event is actually recorded.
    monkeypatch.setattr(rt, "ICDEV_DB", Path(str(tmp_path / "ledger_test.db")))

    retriever = rt.RAGRetriever(config={"rag": {"retrieval": {"fusion_method": "weighted_sum"}}})
    results = retriever.search("account management controls")

    assert results, "stubbed store returned a result; search() dropped it"

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT rpl.chunk_uuid, rpl.parent_doc_uuid, rpl.model_id,
                      rrl.query_hash
               FROM   rag_provenance_ledger rpl
               LEFT   JOIN rag_retrieval_log rrl ON rrl.id = rpl.retrieval_log_id
               WHERE  rpl.event_type = %s""",
            ("retrieval",),
        ).fetchone()
    finally:
        conn.close()

    assert row is not None, (
        "a completed RAG retrieval wrote no rag_provenance_ledger row — the "
        "citation it produced cannot be traced back to its source"
    )
    assert row[0] == "e2e-chunk"
    assert row[1] == source_uuid("ssp", "doc-e2e")
    assert row[2] == "stub-embed-model"
    assert row[3], "the ledger row is not joined to a retrieval event"


# ── 2. the vocabulary trap ──────────────────────────────────────────────────


def test_retrieval_is_an_admitted_event_type():
    assert "retrieval" in PROVENANCE_EVENT_TYPES


def test_check_constraint_admits_retrieval(ledger_db):
    """The trap: before cef-fnd-05 this INSERT raised a CHECK violation.

    The caller swallowed it, so the table stayed at 0 rows and nothing went red.
    """
    ledger_db.execute(
        "INSERT INTO rag_provenance_ledger (chunk_uuid, event_type) VALUES (%s, %s)",
        ("c-check", "retrieval"),
    )
    ledger_db.commit()
    assert ledger_db.execute(
        "SELECT COUNT(*) FROM rag_provenance_ledger WHERE event_type = 'retrieval'"
    ).fetchone()[0] == 1


def test_an_unregistered_event_type_is_still_refused(ledger_db):
    """Widening must not have removed the constraint. An unknown value RAISES."""
    with pytest.raises(Exception):
        ledger_db.execute(
            "INSERT INTO rag_provenance_ledger (chunk_uuid, event_type) VALUES (%s, %s)",
            ("c-bad", "not_a_real_event_type"),
        )
        ledger_db.commit()


def _event_types_in(text: str, table_pattern: str) -> set:
    """Pull the event_type CHECK vocabulary out of a DDL blob."""
    window = re.search(table_pattern, text, re.S | re.I)
    assert window, f"pattern {table_pattern!r} not found"
    check = re.search(r"event_type\s*(?:=\s*ANY\s*\(ARRAY|IN)\s*[\(\[](.*?)[\)\]]",
                      window.group(0), re.S | re.I)
    assert check, "no event_type CHECK found"
    return set(re.findall(r"'([a-z_]+)'", check.group(1)))


@pytest.mark.parametrize(
    "rel_path,pattern",
    [
        (
            "tools/db/init_icdev_db.py",
            r"CREATE TABLE IF NOT EXISTS rag_provenance_ledger.*?\n\);",
        ),
        (
            "tools/db/schema/pg_consolidated.sql",
            r"CREATE TABLE IF NOT EXISTS public\.rag_provenance_ledger.*?\n\);",
        ),
        (
            "tests/conftest.py",
            r"CREATE TABLE IF NOT EXISTS rag_provenance_ledger.*?\n\);",
        ),
        (
            "tools/db/migrations/20260817014511_rag_provenance_ledger_retrieval_events/up.sql",
            r"ADD CONSTRAINT rag_provenance_ledger_event_type_check.*?;",
        ),
    ],
)
def test_every_ddl_copy_agrees_with_the_python_constant(rel_path, pattern):
    """PROVENANCE_EVENT_TYPES is the single source; four DDL copies must match.

    CLAUDE.md: "SQL CHECK constraints: derive from Python constants, never
    hardcode". Adding a value to the tuple without widening a copy re-creates
    the silently-dropped-INSERT bug on whichever database that copy built.
    """
    text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert _event_types_in(text, pattern) == set(PROVENANCE_EVENT_TYPES)


# ── 3. nothing is silently swallowed ────────────────────────────────────────


def test_a_failed_write_raises_rather_than_returning_zero(tmp_path, monkeypatch):
    """The writer RAISES. Silence is the defect; the caller decides severity."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "no_table.db"))
    from tools.db.storage import get_connection

    conn = get_connection()  # a database with no rag_provenance_ledger at all
    try:
        with pytest.raises(ProvenanceWriteError) as exc:
            record_retrieval([_result()], query="q", conn=conn)
        assert "rag_provenance_ledger" in str(exc.value)
    finally:
        conn.close()


def test_retriever_logs_the_failure_instead_of_passing(ledger_db, monkeypatch):
    """_record_provenance must stay non-blocking but must never be silent.

    The pre-cef-fnd-05 body ended in `except Exception: pass`, so a ledger
    failure produced no row and no evidence that anything had been attempted.

    caplog is unusable here: get_logger() sets ``propagate = False`` on the
    ICDEV logger, so records never reach the root handler caplog installs. The
    handler is attached to the retriever's own logger instead, which also makes
    the assertion specific to THAT logger rather than to logging in general.
    """
    import logging

    from tools.rag import provenance_ledger as pl
    from tools.rag import retriever as rt

    def _boom(*_a, **_k):
        raise pl.ProvenanceWriteError("simulated ledger outage")

    monkeypatch.setattr(pl, "record_retrieval", _boom)

    captured: list = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Capture(level=logging.WARNING)
    rt.logger.addHandler(handler)
    try:
        retriever = rt.RAGRetriever(config={"rag": {}})
        # Must NOT raise: a retrieval that found results still returns them.
        retriever._record_provenance("q", [_result()], "", retrieval_log_id=7)
    finally:
        rt.logger.removeHandler(handler)

    assert any("rag_provenance_ledger write failed" in m for m in captured), (
        "a failed provenance write produced no warning — it was swallowed"
    )
    assert any("simulated ledger outage" in m for m in captured), (
        "the warning must carry the underlying exception, not just a generic message"
    )


def test_a_result_without_a_chunk_id_is_not_written_as_an_untraceable_row(ledger_db):
    """chunk_uuid is NOT NULL and a row with no chunk is untraceable by design.

    Skipping it must still be LOUD. caplog cannot see this either — get_logger()
    sets ``propagate = False`` — so the handler goes on the module's own logger.
    """
    import logging

    from tools.rag import provenance_ledger as pl

    captured: list = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = _Capture(level=logging.WARNING)
    pl.logger.addHandler(handler)
    try:
        written = record_retrieval([_result(chunk_id="")], query="q", conn=ledger_db)
    finally:
        pl.logger.removeHandler(handler)

    assert written == 0
    assert any("no chunk_id" in m for m in captured), (
        "a result that cannot be made traceable was skipped silently"
    )
