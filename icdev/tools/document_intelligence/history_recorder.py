# CUI // SP-CTI
"""DIC Section Edit History Recorder — append-only audit trail of content changes.

Records every meaningful edit (before != after) to dic_edit_history, a NIST AU
append-only table.  Produces a unified-diff summary (stdlib difflib only) and
char_delta so reviewers can see the scope of each change at a glance.

Usage::

    from tools.document_intelligence.history_recorder import record_edit

    record_edit("sec_abc123", "alice", old_content, new_content)
"""
from __future__ import annotations

import difflib
import uuid
from datetime import datetime, timezone

from tools.db.storage import get_canvas_connection, get_connection


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dic_edit_history (
            edit_id        TEXT PRIMARY KEY,
            section_id     TEXT NOT NULL,
            doc_id         TEXT,
            version_id     TEXT,
            editor         TEXT NOT NULL,
            content_before TEXT,
            content_after  TEXT,
            char_delta     INTEGER,
            diff_summary   TEXT,
            edited_at      TEXT NOT NULL,
            tenant_id      TEXT,
            classification TEXT DEFAULT 'CUI'
        )
    """)
    conn.commit()


def _unified_diff_summary(before: str, after: str, max_lines: int = 40) -> str:
    """Return a truncated unified diff string for storage."""
    diff_lines = list(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile="before",
        tofile="after",
        n=2,
    ))
    if not diff_lines:
        return ""
    summary = "".join(diff_lines[:max_lines])
    if len(diff_lines) > max_lines:
        summary += f"\n... ({len(diff_lines) - max_lines} more lines)"
    return summary


def record_edit(
    section_id: str,
    editor: str,
    content_before: str,
    content_after: str,
    doc_id: str = "",
    version_id: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> str | None:
    """Record a section edit.

    Returns the new edit_id, or None if before == after (no-op).
    """
    before = content_before or ""
    after = content_after or ""
    if before == after:
        return None

    edit_id = f"eh_{uuid.uuid4().hex[:16]}"
    char_delta = len(after) - len(before)
    diff_summary = _unified_diff_summary(before, after)
    now = _now_iso()

    with get_connection() as conn:
        _ensure_table(conn)
        conn.execute(
            """INSERT INTO dic_edit_history
               (edit_id, section_id, doc_id, version_id, editor,
                content_before, content_after, char_delta, diff_summary,
                edited_at, tenant_id, classification)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                edit_id, section_id, doc_id or "", version_id or "",
                editor, before, after, char_delta, diff_summary,
                now, tenant_id or "", classification,
            ),
        )
        conn.commit()

    # dsyn-consist-02: propagate consistency flags to related docs when change is substantial
    _propagate_consistency_flags(
        section_id=section_id,
        doc_id=doc_id or "",
        content_before=before,
        content_after=after,
        char_delta=char_delta,
        now=now,
        tenant_id=tenant_id or "",
        classification=classification,
    )

    return edit_id


def _propagate_consistency_flags(
    section_id: str,
    doc_id: str,
    content_before: str,
    content_after: str,
    char_delta: int,
    now: str,
    tenant_id: str,
    classification: str,
) -> None:
    """Best-effort: emit dic.consistency_flag canvas events to related docs.

    Only fires when |char_delta| > 50 — minor typo fixes don't trigger cross-doc flags.
    All exceptions are swallowed; consistency propagation must never block a section save.
    """
    if abs(char_delta) <= 50:
        return
    try:
        import json as _json
        import uuid as _uuid
        from tools.document_intelligence.consistency_checker import (
            extract_changed_concepts, find_related_docs,
        )
        changed = extract_changed_concepts(content_before, content_after)
        if not changed:
            return
        related = find_related_docs(doc_id, changed, tenant_id=tenant_id)
        if not related:
            return
        change_summary = f"Section {section_id} changed by {char_delta:+d} chars"
        # canvas_events (migration 039) has no tenant_id/classification columns;
        # carry the security context inside payload_json and use the RLS-disabled
        # canvas connection (mirrors tools/canvas/event_bus.py, PR #720).
        with get_canvas_connection() as conn:
            for rdoc in related:
                event_id = f"evt_{_uuid.uuid4().hex[:16]}"
                payload = _json.dumps({
                    "source_doc_id": doc_id,
                    "source_section_id": section_id,
                    "related_doc_id": rdoc["doc_id"],
                    "matching_concepts": rdoc["matching_concepts"],
                    "change_summary": change_summary,
                    "_security_context": {
                        "tenant_id": tenant_id, "clearance": classification},
                })
                conn.execute(
                    "INSERT INTO canvas_events "
                    "(id, source_canvas, target_canvas, event_type, payload_json, "
                    " created_at, consumed_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, NULL)",
                    (event_id, "dic", "dic", "dic.consistency_flag", payload,
                     now),
                )
            conn.commit()
    except Exception as exc:
        from tools.logging.icdev_logger import get_logger as _gl
        _gl(__name__).debug("history_recorder: consistency propagation error: %s", exc)


def get_section_history(
    section_id: str,
    limit: int = 50,
    since: str | None = None,
) -> list[dict]:
    """Return edit history for a section, most-recent first.

    since: optional ISO 8601 timestamp; only entries at or after this time are returned.
    """
    with get_connection() as conn:
        _ensure_table(conn)
        if since:
            rows = conn.execute(
                """SELECT edit_id, section_id, doc_id, version_id, editor,
                          char_delta, diff_summary, edited_at, classification
                   FROM dic_edit_history
                   WHERE section_id = %s AND edited_at >= %s
                   ORDER BY edited_at DESC
                   LIMIT %s""",
                (section_id, since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT edit_id, section_id, doc_id, version_id, editor,
                          char_delta, diff_summary, edited_at, classification
                   FROM dic_edit_history
                   WHERE section_id = %s
                   ORDER BY edited_at DESC
                   LIMIT %s""",
                (section_id, limit),
            ).fetchall()
    return [dict(r) for r in rows]
