#!/usr/bin/env python3
# CUI // SP-CTI
"""Writer for ``rag_provenance_ledger`` — the append-only AIA chain-of-custody
ledger (D-AIDP, NIST AU-3).

Why this module exists (cef-fnd-05)
-----------------------------------
The table shipped with three READERS and no writer:

* ``tools/dic/provenance_adapter.py``      — DIC footnote lineage
* ``tools/genesis/reflexes/aidp_monitor.py`` — integrity sweeps
* ``tools/quality/citation_grounding.py``  — ``from_ledger_row()``

so on the live board it held **0 rows against 2,430 retrievals** in
``rag_retrieval_log``. Every reader degraded to "no provenance found", which is
indistinguishable from "this chunk has clean provenance we simply did not
record" — and the retriever's step 7 wrote to a DIFFERENT store (PROV-AGENT via
``prov_recorder``), so the pipeline read as though provenance was being kept.

The TRUST invariant is two-part: an artifact carries inline ``[source: ...]``
citations AND a persisted provenance record. Only the first half existed.

The event-type vocabulary
-------------------------
``PROVENANCE_EVENT_TYPES`` is the SINGLE SOURCE for the ``event_type`` CHECK
constraint (CLAUDE.md: "SQL CHECK constraints: derive from Python constants,
never hardcode"). The DDL copies that must agree with it:

* ``tools/db/init_icdev_db.py``          (fresh database)
* ``tools/db/schema/pg_consolidated.sql`` (fresh PG bootstrap)
* ``tools/db/migrations/20260816...``    (existing database)
* ``tests/conftest.py`` MINIMAL_ICDEV_SCHEMA

``tests/rag/test_provenance_ledger_retrieval.py`` asserts they agree. This is
the same failure mode migration 20260815002727 fixed one table over: a value
the CHECK did not admit made the INSERT raise, the caller's ``except`` swallowed
it, and the row silently never appeared. Adding a value here without widening
every DDL copy re-creates exactly that bug.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.rag.provenance_ledger")

#: Every value that may be written to ``rag_provenance_ledger.event_type``.
#: The CHECK constraint in every DDL copy is derived from THIS tuple.
#:
#: * ``ingest``           — a chunk entered the corpus (D-AIDP DLT-002).
#: * ``chain_of_custody`` — an LLM consumed the chunk; carries prompt_sha256 +
#:   signature, which ``aidp_monitor`` check 2 requires to be non-empty.
#: * ``retrieval``        — the chunk was RETRIEVED and handed to a caller as
#:   citable evidence (cef-fnd-05). This is the row that makes a citation
#:   traceable back to its source record after the fact.
PROVENANCE_EVENT_TYPES = ("ingest", "chain_of_custody", "retrieval")

#: Separator used to compose ``parent_doc_uuid`` from a chunk's source columns.
#: ``rag_chunks`` identifies a source by the PAIR (source_type, source_id), so
#: neither alone is a document identity — ``aidp_monitor``'s classification-drift
#: check groups by ``parent_doc_uuid``, and collapsing two source types onto one
#: id would manufacture a drift violation that does not exist.
_SOURCE_UUID_SEP = ":"


class ProvenanceWriteError(RuntimeError):
    """A provenance row could not be persisted.

    Raised — never swallowed here. Callers on a latency-sensitive path may
    downgrade it to a logged warning (retrieval must not fail because the
    ledger did), but they must LOG it. A bare ``except Exception: pass`` around
    a provenance write is the defect this module was written to remove.
    """


def source_uuid(source_type: str, source_id: str) -> str:
    """Compose the ``parent_doc_uuid`` for a chunk's source document."""
    return f"{source_type or ''}{_SOURCE_UUID_SEP}{source_id or ''}"


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def record_retrieval(
    results: Iterable[Any],
    query: str = "",
    retrieval_log_id: Optional[int] = None,
    retrieval_mode: str = "",
    model_id: Optional[str] = None,
    tenant_id: str = "",
    project_id: str = "",
    conn: Optional[Any] = None,
) -> int:
    """Append one ``event_type='retrieval'`` row per retrieved chunk.

    Each row links **chunk -> source -> retrieval event**:

    * ``chunk_uuid``       = ``rag_chunks.id`` of the retrieved chunk
    * ``parent_doc_uuid``  = ``source_type:source_id`` of the chunk's source
    * ``retrieval_log_id`` = ``rag_retrieval_log.id`` of the event
    * ``prompt_sha256``    = SHA-256 of the query, which equals
      ``rag_retrieval_log.query_hash`` — a SECOND path back to the event, so a
      row is still attributable when the log INSERT itself failed and
      ``retrieval_log_id`` is NULL.

    ``sha256_hash`` is the hash of the content actually handed to the caller, so
    a later audit can prove the cited text is the chunk text.

    Args:
        results: Retrieved chunks. Any object exposing ``chunk_id``,
            ``content``, ``source_type``, ``source_id`` and ``final_score``.
        query: The query text. Only its SHA-256 is stored, never the text
            itself (D282 — the retrieval log hashes queries by default too).
        retrieval_log_id: ``rag_retrieval_log.id`` for this search, if known.
        retrieval_mode: The mode the search resolved to, recorded in
            ``hyperparams_json`` for post-hoc analysis.
        model_id: Embedding model that produced the ranking, read off the live
            provider — never a literal (CLAUDE.md: no hardcoded model ids).
        tenant_id: Tenant that issued the search.
        project_id: Project filter applied to the search.
        conn: Optional open connection. When omitted one is opened and closed
            here.

    Returns:
        Number of ledger rows written.

    Raises:
        ProvenanceWriteError: the rows could not be persisted. Callers decide
            whether that is fatal; it is never silent.
    """
    rows = list(results or [])
    if not rows:
        return 0

    query_hash = _sha256(query)
    params: List[tuple] = []
    for rank, r in enumerate(rows, start=1):
        chunk_uuid = str(getattr(r, "chunk_id", "") or "")
        if not chunk_uuid:
            # chunk_uuid is NOT NULL. A result with no chunk id cannot be made
            # traceable, so skip it loudly rather than writing an unusable row.
            logger.warning(
                "record_retrieval: result at rank %s has no chunk_id; "
                "no provenance row is traceable for it",
                rank,
            )
            continue
        content = getattr(r, "content", "") or ""
        hyperparams = {
            "rank": rank,
            "final_score": round(float(getattr(r, "final_score", 0.0) or 0.0), 6),
            "retrieval_mode": retrieval_mode,
            "source_table": getattr(r, "source_table", "") or "",
            "project_id": project_id,
            "tenant_id": tenant_id,
        }
        params.append(
            (
                chunk_uuid,
                source_uuid(getattr(r, "source_type", ""), getattr(r, "source_id", "")),
                _sha256(content),
                getattr(r, "classification", None),
                model_id,
                json.dumps(hyperparams, sort_keys=True),
                query_hash,
                "retrieval",
                retrieval_log_id,
            )
        )

    if not params:
        return 0

    sql = """INSERT INTO rag_provenance_ledger
             (chunk_uuid, parent_doc_uuid, sha256_hash, classification_label,
              model_id, hyperparams_json, prompt_sha256, event_type,
              retrieval_log_id)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""

    owns_conn = conn is None
    try:
        if owns_conn:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
        for p in params:
            conn.execute(sql, p)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 — re-raised as a typed error below
        raise ProvenanceWriteError(
            f"rag_provenance_ledger INSERT failed for {len(params)} retrieved "
            f"chunk(s) (retrieval_log_id={retrieval_log_id}): {exc}"
        ) from exc
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — close failure must not mask the write result
                logger.debug("record_retrieval: connection close failed", exc_info=True)

    logger.debug(
        "record_retrieval: wrote %s provenance row(s) for retrieval_log_id=%s",
        len(params),
        retrieval_log_id,
    )
    return len(params)


def lineage_for_chunk(chunk_uuid: str, conn: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Return every ledger row for *chunk_uuid*, newest first.

    The read side of the acceptance criterion "queryable after the fact": given
    a chunk cited in an artifact, this returns the source it came from and the
    retrieval events that served it.
    """
    owns_conn = conn is None
    try:
        if owns_conn:
            from tools.db.storage import get_connection  # noqa: PLC0415

            conn = get_connection()
        cur = conn.execute(
            """SELECT id, chunk_uuid, parent_doc_uuid, sha256_hash,
                      classification_label, model_id, hyperparams_json,
                      prompt_sha256, event_type, retrieval_log_id, created_at
               FROM   rag_provenance_ledger
               WHERE  chunk_uuid = %s
               ORDER  BY id DESC""",
            (chunk_uuid,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        raise ProvenanceWriteError(
            f"rag_provenance_ledger lineage query failed for {chunk_uuid}: {exc}"
        ) from exc
    finally:
        if owns_conn and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                logger.debug("lineage_for_chunk: connection close failed", exc_info=True)
