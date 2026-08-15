# CUI // SP-CTI
"""IQE Delta Review collection adapters (trust-hitl-02).

Registers three read-only collections on the module-level Executor:

  delta_review.deltas       reviewable deltas, settlement rows excluded
  delta_review.settlements  the human dispositions, with their rationale
  delta_review.spans        every aligned claim span, flattened across deltas

The split is what makes the interesting questions answerable. "Which claims
still carry a finding after revision" is a question about SPANS, and asking it
against whole deltas can only ever return "this delta has some" — which is what
the panel already shows. Flattening the spans is what turns the panel's central
insight into something an operator can query in bulk.

Two properties these adapters hold, both learned from ctx-trust-03/04:

**They do not own the caller's connection.** ``Executor._fetch_union`` /
``_fetch_join`` fetch several collections IN PARALLEL over ONE connection the
caller opened. Closing it in ``finally`` closes it out from under a sibling
fetch still running on another thread; that sibling raises, a bare
``except: return []`` swallows it, and a union silently returns half its rows.

**A capped scan is not a certainty.** The executor applies a query's ``where``
clauses in PYTHON, AFTER this cap. So each fetch asks for one row more than the
cap and reports ``capped_rows`` when the extra row comes back, which reaches
``analyst`` and stops a truncated count being labelled ``confidence 1.0``.

**No draft text leaves this seam.** ``before_text`` / ``after_text`` are absent
from every SELECT below, and the ``spans`` collection emits claim INDICES and
verdicts but never the claim strings. IQE results are rendered into answers and
summaries that travel further than the panel does — an ``analyst`` answer, an
AI brief, a chat reply — and the drafted artifact is exactly the CUI the panel
keeps behind auth.

This does cost the spans collection something, and it is worth naming rather
than pretending otherwise: ``where s.finding_verdict == "persisting"`` returns
which spans of which deltas still carry a defect, not what they say. That is a
bulk-TRIAGE answer, and reading the claim itself is a one-delta act performed in
the panel by someone who opened it. Widening this to carry claim text would make
the collection marginally more convenient and would put drafted CUI into every
downstream surface that renders an IQE result.
"""
from __future__ import annotations

import json
from typing import Any

from tools.iqe.executor import capped_rows, register_collection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: Rows fetched per collection before the executor filters in Python.
#: Overridable per query via the IQE call form, e.g. ``delta_review.deltas(20000)``.
_ROW_CAP = 10000

#: Spans are ~1 row per sentence per delta, so the same delta cap would be met
#: far sooner here. Sized so a few hundred deltas' worth of spans still fit.
_SPAN_DELTA_CAP = 500


def _conn(conn: Any) -> tuple[Any, bool]:
    """Return ``(connection, owned)``.

    ``owned`` is True only when this adapter opened the connection itself, and
    it is the sole thing that may close it. A caller-supplied connection is
    shared with sibling fetches running concurrently on other threads.
    """
    if conn is None:
        from tools.db.storage import get_connection  # noqa: PLC0415

        return get_connection(), True
    return conn, False


def _fetch(collection: str, sql: str, conn: Any, limit: int) -> list[dict]:
    """Run *sql* for *collection*, honouring connection ownership.

    Fetches ``limit + 1`` rows and returns at most ``limit``. The extra row is
    what makes the cap *detectable*: ``len(rows) == limit`` is ambiguous — a
    collection holding exactly ``limit`` rows is complete — and reporting that
    as truncated would flag every honest answer.
    """
    limit = int(limit)
    c, owned = _conn(conn)
    try:
        cur = c.execute(sql, (limit + 1,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception:
        # Logged and re-raised, never swallowed into []: an empty list is
        # indistinguishable from "no rows", and these are the collections an
        # operator uses to audit whether HITL review is actually happening.
        logger.warning("iqe adapter %s failed", collection, exc_info=True)
        raise
    finally:
        if owned:
            c.close()

    if len(rows) > limit:
        logger.warning(
            "iqe adapter %s hit the %d-row cap — the executor filters in Python "
            "AFTER this cap, so any aggregate over a larger window is computed "
            "from the newest %d rows only",
            collection, limit, limit,
        )
        return capped_rows(rows[:limit], collection, limit)
    return rows


def deltas_adapter(conn: Any, limit: int = _ROW_CAP) -> list[dict]:
    """Reviewable deltas. Settlement successors are excluded — they are the
    disposition, not the thing disposed of, and counting both double-counts
    every reviewed change."""
    return _fetch(
        "delta_review.deltas",
        "SELECT delta_id, artifact_id, artifact_type, stage, gate, "
        "before_hash, after_hash, findings_before_n, findings_after_n, "
        "actor, disposition, approval_item_id, session_id, "
        "classification, tenant_id, created_at "
        "FROM trust_deltas WHERE stage <> 'settlement' "
        "ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )


def settlements_adapter(conn: Any, limit: int = _ROW_CAP) -> list[dict]:
    """Human dispositions. ``supersedes_delta_id`` is the delta each one settles,
    and ``rationale`` is included precisely so "were any approved without a
    stated reason" is answerable — which is the question the mandatory-rationale
    rule exists to make un-askable."""
    return _fetch(
        "delta_review.settlements",
        "SELECT delta_id, supersedes_delta_id, artifact_id, disposition, "
        "actor, rationale, gate, findings_before_n, findings_after_n, "
        "approval_item_id, session_id, classification, tenant_id, created_at "
        "FROM trust_deltas WHERE stage = 'settlement' "
        "ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )


def spans_adapter(conn: Any, limit: int = _SPAN_DELTA_CAP) -> list[dict]:
    """Every aligned claim span, flattened across deltas.

    ``spans`` is a JSON column, and this decodes it in PYTHON rather than with
    ``json_each`` — the CLAUDE.md rule. A SQLite-dialect JSON function here
    would work in a fresh worktree and fail on the PostgreSQL primary, which is
    the exact shape of the bug that rule exists to prevent.

    The cap counts DELTAS, not spans: a delta yields one row per sentence, so
    capping the output rows would truncate a delta mid-way and produce a span
    list that silently describes only part of a document.
    """
    from tools.delta_review.review import resolve_span_findings  # noqa: PLC0415

    rows = _fetch(
        "delta_review.spans",
        "SELECT delta_id, artifact_id, stage, gate, spans, created_at "
        "FROM trust_deltas WHERE stage <> 'settlement' "
        "ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )

    out: list[dict] = []
    for row in rows:
        raw = row.get("spans")
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw or "[]")
            except (TypeError, ValueError):
                logger.warning(
                    "delta_review.spans: delta %s has unparseable spans",
                    row.get("delta_id"),
                )
                continue
        else:
            parsed = raw or []
        if not isinstance(parsed, list):
            continue
        for index, span in enumerate(parsed, start=1):
            if not isinstance(span, dict):
                continue
            annotated = resolve_span_findings(span)
            out.append({
                "delta_id": row.get("delta_id"),
                "artifact_id": row.get("artifact_id"),
                "stage": row.get("stage"),
                "gate": row.get("gate"),
                "span_number": index,
                "kind": annotated.get("kind"),
                "finding_verdict": annotated.get("finding_verdict"),
                "before_index": annotated.get("before_index"),
                "after_index": annotated.get("after_index"),
                "findings_before_n": annotated.get("findings_before_n"),
                "findings_after_n": annotated.get("findings_after_n"),
                "created_at": row.get("created_at"),
            })

    # The delta cap is what may have bitten, so carry its report onto the
    # flattened rows — otherwise a truncated span scan reads as complete.
    from tools.iqe.executor import incompleteness_of  # noqa: PLC0415

    if incompleteness_of(rows):
        return capped_rows(out, "delta_review.spans", int(limit))
    return out


register_collection("delta_review.deltas", deltas_adapter)
register_collection("delta_review.settlements", settlements_adapter)
register_collection("delta_review.spans", spans_adapter)
