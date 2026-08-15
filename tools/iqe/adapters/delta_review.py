# CUI // SP-CTI
"""IQE Delta Review collection adapters (trust-hitl-02).

Registers four read-only collections on the module-level Executor:

  delta_review.deltas       one row per recorded delta, counts derived
  delta_review.settlements  each delta joined to its approval item's outcome
  delta_review.spans        every aligned claim span, flattened across deltas
  delta_review.decisions     the permanent agent_approval_log entries

The split is what makes the interesting questions answerable. "Which claims
still carry a finding after revision" is a question about SPANS, and asking it
of whole deltas can only ever answer "this one has some" — which is what the
panel already shows. Flattening the spans turns the panel's central insight into
something an operator can query in bulk.

WHY SETTLEMENTS AND DECISIONS ARE TWO COLLECTIONS. Because the fact lives in two
tables, deliberately. ``approval_items`` holds the mutable outcome
(``state``/``resolution``/``resolved_by``) and is joined to a delta by
``approval_item_id``. The reviewer's RATIONALE is not in that table at all —
``approval_inbox._settle`` passes it to ``record_decision``, which appends it to
``agent_approval_log.reason``. So "was anything approved without a stated
reason" is a question about ``decisions``, and "what is the outcome of delta X"
is a question about ``settlements``. Collapsing them would require inventing a
join key that does not exist: ``agent_approval_log`` carries no ``item_id``, and
its link back is ``rule = 'hitl_delta'`` plus the rendered ``detail`` line, which
identifies the artifact and stage but not the individual delta. That limit is
named here rather than papered over with a fuzzy match.

Three properties these adapters hold, two of them learned from ctx-trust-03/04:

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
from every SELECT below; the JSON ``findings_*`` columns are read only to COUNT
them and are never emitted, because ``claim_gate`` puts the first 120 characters
of the offending claim into ``detail``; and the ``spans`` collection emits claim
INDICES and verdicts, never the claim strings. IQE results are rendered into
answers and summaries that travel further than the panel does — an ``analyst``
answer, an AI brief, a chat reply — and the drafted artifact is exactly the CUI
the panel keeps behind auth.

That costs the spans collection something, and it is worth naming rather than
pretending otherwise: ``where s.finding_verdict == "persisting"`` returns which
spans of which deltas still carry a defect, not what they say. That is a bulk
TRIAGE answer, and reading the claim itself is a one-delta act performed in the
panel by someone who opened it. Widening this to carry claim text would make the
collection marginally more convenient and would put drafted CUI into every
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


def _json_len(value: Any) -> int:
    """Length of a JSON list column, parsed in PYTHON.

    Never ``json_array_length``: that is SQLite dialect, it would work in a
    fresh worktree and fail on the PostgreSQL primary, and it is the exact shape
    of bug the CLAUDE.md rule exists to prevent.
    """
    if isinstance(value, list):
        return len(value)
    if not value:
        return 0
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return 0
    return len(parsed) if isinstance(parsed, list) else 0


def _drop(row: dict, *keys: str) -> dict:
    for key in keys:
        row.pop(key, None)
    return row


def deltas_adapter(conn: Any, limit: int = _ROW_CAP) -> list[dict]:
    """One row per recorded delta.

    ``findings_before_n`` / ``findings_after_n`` / ``net_findings`` are DERIVED
    here from the stored JSON, matching the panel exactly — the counts are not
    columns and must not become ones.
    """
    rows = _fetch(
        "delta_review.deltas",
        "SELECT delta_id, artifact_id, stage, before_hash, after_hash, "
        "findings_before, findings_after, spans, actor, rationale, "
        "approval_item_id, supersedes_delta_id, session_id, classification, "
        "created_at FROM trust_deltas ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )
    for row in rows:
        before_n = _json_len(row.get("findings_before"))
        after_n = _json_len(row.get("findings_after"))
        row["findings_before_n"] = before_n
        row["findings_after_n"] = after_n
        row["net_findings"] = after_n - before_n
        row["span_count"] = _json_len(row.get("spans"))
        row["is_no_op"] = bool(row.get("before_hash")) and (
            row.get("before_hash") == row.get("after_hash")
        )
        row["has_rationale"] = bool(str(row.get("rationale") or "").strip())
        _drop(row, "findings_before", "findings_after", "spans")
    return rows


def settlements_adapter(conn: Any, limit: int = _ROW_CAP) -> list[dict]:
    """Each delta joined to the outcome of its approval item.

    The join runs in PYTHON, not SQL, for the reason ``pending_deltas``
    documents: the two tables are RLS-eligible independently, and a cross-table
    join under two predicates is how a row silently vanishes from a review
    queue. Here the failure would be quieter still — an inner join would drop
    every delta whose ask never queued, which is precisely the population an
    operator auditing HITL coverage is looking for.

    ``review_state`` is the same derivation the panel renders, so a query and
    the page cannot disagree about whether something was approved.
    """
    from tools.delta_review.constants import (  # noqa: PLC0415
        RESOLUTION_APPROVED,
        RESOLUTION_DENIED,
        REVIEW_APPROVED,
        REVIEW_DENIED,
        REVIEW_LAPSED,
        REVIEW_PENDING,
        REVIEW_SUPERSEDED,
        STATE_PENDING,
        STATE_RESOLVED,
    )

    deltas = _fetch(
        "delta_review.settlements",
        "SELECT delta_id, artifact_id, stage, actor, approval_item_id, "
        "supersedes_delta_id, classification, created_at "
        "FROM trust_deltas ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )
    superseded = {
        d["supersedes_delta_id"] for d in deltas if d.get("supersedes_delta_id")
    }

    items: dict[str, dict] = {}
    wanted = {d["approval_item_id"] for d in deltas if d.get("approval_item_id")}
    if wanted:
        try:
            for item in _fetch(
                "delta_review.settlements",
                "SELECT item_id, state, resolution, resolved_by, resolved_at, "
                "expires_at, inbox, origin FROM approval_items "
                "WHERE origin = 'workflow_hitl' ORDER BY created_at DESC LIMIT %s",
                conn,
                limit,
            ):
                items[item["item_id"]] = item
        except Exception:
            # A missing or unreadable approval_items must not empty this
            # collection: every delta then reports `pending`, which is the same
            # answer review_state gives for an absent item and is the truthful
            # one — nobody has been shown to have answered.
            logger.warning(
                "delta_review.settlements: approval_items unreadable — every "
                "delta reports pending", exc_info=True,
            )

    out: list[dict] = []
    for row in deltas:
        item = items.get(row.get("approval_item_id") or "")
        if row["delta_id"] in superseded:
            review = REVIEW_SUPERSEDED
        elif item is None or item.get("state") == STATE_PENDING:
            review = REVIEW_PENDING
        elif item.get("state") == STATE_RESOLVED:
            review = (
                REVIEW_APPROVED if item.get("resolution") == RESOLUTION_APPROVED
                else REVIEW_DENIED if item.get("resolution") == RESOLUTION_DENIED
                else REVIEW_LAPSED
            )
        else:
            review = REVIEW_LAPSED
        row.update({
            "review_state": review,
            "item_state": (item or {}).get("state", ""),
            "resolution": (item or {}).get("resolution", ""),
            "resolved_by": (item or {}).get("resolved_by", ""),
            "resolved_at": (item or {}).get("resolved_at", ""),
            "has_ask": item is not None,
        })
        out.append(row)
    return out


def spans_adapter(conn: Any, limit: int = _SPAN_DELTA_CAP) -> list[dict]:
    """Every aligned claim span, flattened across deltas.

    ``spans`` is a JSON column, decoded in PYTHON rather than with ``json_each``
    — the CLAUDE.md rule.

    The cap counts DELTAS, not spans: a delta yields one row per sentence, so
    capping the output rows would truncate a delta mid-way and produce a span
    list that silently describes only part of a document.
    """
    from tools.delta_review.review import build_span_rows  # noqa: PLC0415
    from tools.iqe.executor import incompleteness_of  # noqa: PLC0415
    from tools.quality.hitl_delta import Delta  # noqa: PLC0415

    rows = _fetch(
        "delta_review.spans",
        "SELECT delta_id, artifact_id, stage, findings_before, findings_after, "
        "spans, created_at FROM trust_deltas ORDER BY created_at DESC LIMIT %s",
        conn,
        limit,
    )

    def _load(value: Any, delta_id: Any) -> list:
        if isinstance(value, list):
            return value
        try:
            parsed = json.loads(value or "[]")
        except (TypeError, ValueError):
            logger.warning("delta_review.spans: delta %s has unparseable JSON", delta_id)
            return []
        return parsed if isinstance(parsed, list) else []

    out: list[dict] = []
    for row in rows:
        delta = Delta(
            delta_id=row.get("delta_id") or "",
            artifact_id=row.get("artifact_id") or "",
            stage=row.get("stage") or "",
            findings_before=_load(row.get("findings_before"), row.get("delta_id")),
            findings_after=_load(row.get("findings_after"), row.get("delta_id")),
            spans=_load(row.get("spans"), row.get("delta_id")),
        )
        for index, span in enumerate(build_span_rows(delta), start=1):
            out.append({
                "delta_id": delta.delta_id,
                "artifact_id": delta.artifact_id,
                "stage": delta.stage,
                "span_number": index,
                "op": span.get("op"),
                "finding_verdict": span.get("finding_verdict"),
                "before_index": span.get("before_index", -1),
                "after_index": span.get("after_index", -1),
                "before_verdict": span.get("before_verdict", ""),
                "after_verdict": span.get("after_verdict", ""),
                "findings_before_n": span.get("findings_before_n", 0),
                "findings_after_n": span.get("findings_after_n", 0),
                "notable": span.get("notable", False),
                "created_at": row.get("created_at"),
            })

    # The delta cap is what may have bitten, so carry its report onto the
    # flattened rows — otherwise a truncated span scan reads as complete.
    if incompleteness_of(rows):
        return capped_rows(out, "delta_review.spans", int(limit))
    return out


def decisions_adapter(conn: Any, limit: int = _ROW_CAP) -> list[dict]:
    """The permanent decision records these settlements wrote.

    ``agent_approval_log`` filtered to ``rule = 'hitl_delta'`` — the rule
    ``hitl_delta._raise_ask`` stamps on every ask it queues. ``reason`` carries
    the reviewer's rationale, which is why "were any of these approved without a
    stated reason" is answerable at all.

    ``detail`` is the rendered ask title (``[stage] TRUST delta on <artifact>``),
    so it identifies the artifact and stage but NOT the individual delta —
    ``agent_approval_log`` has no ``item_id`` column. ``artifact_id`` below is
    parsed from it on a best-effort basis and is empty when the title does not
    match; it is not a foreign key and must not be treated as one.
    """
    rows = _fetch(
        "delta_review.decisions",
        "SELECT id, decided_at, session_id, actor, tool_name, tier, rule, "
        "decision, reason, mode, detail, classification, created_at "
        "FROM agent_approval_log WHERE rule = 'hitl_delta' "
        "ORDER BY decided_at DESC LIMIT %s",
        conn,
        limit,
    )
    for row in rows:
        reason = str(row.get("reason") or "").strip()
        row["has_rationale"] = bool(reason)
        # `settle_delta` substitutes "delta <id> approved|rejected" when a caller
        # supplies none. It is well-formed and says nothing, so it is reported
        # apart from a real rationale rather than counted as one.
        row["is_default_reason"] = reason.startswith("delta ") and (
            reason.endswith(" approved") or reason.endswith(" rejected")
        )
        detail = str(row.get("detail") or "")
        marker = " TRUST delta on "
        row["artifact_id"] = detail.split(marker, 1)[1].strip() if marker in detail else ""
        row["stage"] = (
            detail[1:].split("]", 1)[0] if detail.startswith("[") and "]" in detail else ""
        )
    return rows


register_collection("delta_review.deltas", deltas_adapter)
register_collection("delta_review.settlements", settlements_adapter)
register_collection("delta_review.spans", spans_adapter)
register_collection("delta_review.decisions", decisions_adapter)
