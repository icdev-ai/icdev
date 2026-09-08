# CUI // SP-CTI
"""DIC Annotation Store — threaded, anchored, resolvable review comments.

Table (created by migration 20260908065203; ``_ensure_tables`` carries the same
shape for a database created after it landed):

  dic_section_annotations  — one row per comment. A ROOT row carries the anchor
                             and the thread's lifecycle; a REPLY row carries
                             ``parent_ann_id`` and nothing else about position.

Public API:
  create_annotation(...)                    -> dict   the row as written
  get_annotation(ann_id)                    -> dict | None
  list_annotations(section_id, ...)         -> list[dict]   flat, anchor-checked
  list_threads(section_id, ...)             -> list[dict]   root + replies
  resolve_thread(ann_id, resolved_by=, ...) -> dict
  reopen_thread(ann_id)                     -> dict
  update_annotation(ann_id, **fields)       -> dict
  delete_thread(ann_id)                     -> dict
  anchor_state(section_text, row)           -> dict   the READ-TIME verdict

WHAT THIS TABLE WAS. Measured on the live PG board 2026-09-08: 14 columns, 0
rows, and NO MIGRATION — it was a runtime ``CREATE TABLE IF NOT EXISTS`` in
``blueprint.py``, so a deployment where that ``_ensure`` had run was
indistinguishable from one where it had not. It had ``selected_text``,
``category``, ``author`` and an open/resolved lifecycle, and it could not
express a reply, a resolved RANGE, or a tenant.

THE ANCHOR IS THE SUGGESTION'S ANCHOR. An anchored comment obeys the SAME rule
as an anchored change: ``resolve_anchor`` and ``validate_anchor`` are IMPORTED
from ``suggestion_store`` (dwr-anchor-03), never re-derived here. A second copy
of "does this span still say what it said" is how one surface starts disagreeing
with another about whether a document moved. The test suite reads this module's
AST to refuse a local re-implementation.

  section_id     the section of record, and the section the offsets index.
                 There is deliberately NO ``anchor_section_id``: the row already
                 names its section, and two spellings of one fact drift.
  anchor_start   chunk-local offsets into that section's ``content``.
  anchor_end
  anchor_text    the VERBATIM slice. ``content[start:end] == anchor_text`` is
                 the invariant, checked at write time against the caller's
                 ``section_content`` and RE-DERIVED on every read.
  anchor_basis   RECORDED by the writer, never inferred:
                   exact       the offsets came from the selection that made it
                   relocated   recovered by ``str.find`` — an honest guess, and
                               only when the text occurs ONCE
                   unanchored  a SECTION-LEVEL comment. Not a defect: plenty of
                               review comments are about the section, not a span.
  selected_text  DISPLAY ONLY, and the legacy free-typed field. On an anchored
                 write it is set to the verified slice so one value is rendered;
                 it is never what gets verified. ``anchor_text`` is.

ORPHANED IS A VERDICT, NOT A COLUMN. Nothing persists "this comment is
orphaned": a stored verdict about whether a span still matches goes stale the
moment the section is edited, which is the one event it exists to describe. It
is re-derived on every read by re-slicing the LIVE section:

  verified      content[start:end] == anchor_text
  orphaned      it does not. THE FINDING.
  unanchored    the comment never had a span. Not a defect.
  unverifiable  the section could not be read. NOT a clean bill of health, and
                never folded into ``verified`` — an unreadable section says
                nothing about whether the anchor still holds.

AND IT IS NEVER SILENTLY RE-POINTED. An orphaned comment keeps its stored
offsets. Where ``resolve_anchor`` can find the text exactly ONCE elsewhere, that
position is reported as ``relocation_candidate`` — advisory, for a human, and
never written back. An ambiguous match yields no candidate at all, because
picking one of several is the guess ``claim_extractor.anchor`` refuses to make.

THREADS ARE FLAT, AND THE ROOT OWNS THE LIFECYCLE. A reply names its parent and
carries NO anchor of its own — the span belongs to the thread, not to each
remark about it — and a reply to a reply is refused. Resolving is an act on the
THREAD: it writes ``status``/``resolved_by``/``resolved_at`` to the ROOT, and a
reply's own ``status`` is never a second lifecycle to keep in step.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection

# ONE anchor rule. Imported, never re-derived — see the module docstring.
from tools.document_intelligence.suggestion_store import (
    ANCHOR_BASES,
    resolve_anchor,
    validate_anchor,
)

TABLE = "dic_section_annotations"

CATEGORIES = ("question", "improvement", "compliance", "strength",
              "weakness", "risk", "editorial")

STATUSES = ("open", "resolved")

# The read-time verdict. `unverifiable` is never folded into another.
ANCHOR_STATES = ("verified", "orphaned", "unanchored", "unverifiable")

# Declaration order of the columns dwr-cmt-01 added. The migration that reaches
# an EXISTING table (20260908065203) carries the same tuple, pinned by test.
NEW_COLUMNS = (
    ("parent_ann_id", "TEXT"),
    ("anchor_start", "INTEGER"),
    ("anchor_end", "INTEGER"),
    ("anchor_text", "TEXT"),
    ("anchor_basis", "TEXT"),
    ("tenant_id", "TEXT"),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(row):
    if row is None:
        return None
    return dict(row)


# ── Schema ────────────────────────────────────────────────────────────────────

CREATE_FULL = """
CREATE TABLE IF NOT EXISTS dic_section_annotations (
    ann_id          TEXT    PRIMARY KEY,
    section_id      TEXT    NOT NULL,
    doc_id          TEXT    NOT NULL,
    selected_text   TEXT    NOT NULL DEFAULT '',
    category        TEXT    NOT NULL,
    comment         TEXT    NOT NULL,
    author          TEXT    NOT NULL DEFAULT 'reviewer',
    status          TEXT    NOT NULL DEFAULT 'open',
    resolution_note TEXT,
    resolved_by     TEXT,
    resolved_at     TEXT,
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT,
    parent_ann_id   TEXT,
    anchor_start    INTEGER,
    anchor_end      INTEGER,
    anchor_text     TEXT,
    anchor_basis    TEXT,
    tenant_id       TEXT
)
"""


def _ensure_tables(conn) -> None:
    conn.execute(CREATE_FULL)
    conn.commit()


# ── The read-time verdict ─────────────────────────────────────────────────────

def anchor_state(section_text: str | None, row: dict) -> dict:
    """Re-derive whether ``row``'s span still says what it said.

    ``section_text`` is the section's content of record, or ``None`` when it
    could not be read. Returns ``{"state", "reason", "relocation_candidate"}``;
    the candidate is ``None`` unless the text is findable exactly once, and it
    is ADVISORY — nothing here writes it back.

    Pure: reads no database, writes nothing.
    """
    basis = row.get("anchor_basis")
    start, end = row.get("anchor_start"), row.get("anchor_end")
    text = row.get("anchor_text")

    if basis is None and start is None and end is None:
        # Written before this migration, or a section-level comment from a
        # writer that did not say. Either way there is no span to check.
        return {"state": "unanchored", "reason": "no_anchor_recorded",
                "relocation_candidate": None}
    if basis == "unanchored" or start is None or end is None:
        return {"state": "unanchored", "reason": "section_level_comment",
                "relocation_candidate": None}
    if section_text is None:
        return {"state": "unverifiable", "reason": "section_unreadable",
                "relocation_candidate": None}

    verdict = resolve_anchor(section_text, text, anchor_start=start, anchor_end=end)
    if verdict["anchor_basis"] == "exact":
        return {"state": "verified", "reason": "offsets_verified",
                "relocation_candidate": None}

    candidate = None
    if verdict["anchor_basis"] == "relocated":
        # The text is still in the section, exactly once, somewhere else. That
        # is a suggestion for a HUMAN. The stored offsets stay as they are.
        candidate = {"anchor_start": verdict["anchor_start"],
                     "anchor_end": verdict["anchor_end"]}
    return {"state": "orphaned", "reason": verdict["reason"],
            "relocation_candidate": candidate}


_REPLY_ANCHOR = {"state": "unanchored", "reason": "reply_inherits_thread",
                 "relocation_candidate": None}


def anchor_from_selection(section_id: str, anchor_text: str | None, *,
                          anchor_start: int | None = None,
                          anchor_end: int | None = None,
                          conn=None) -> dict:
    """Work out what basis a CALLER'S SELECTION can honestly claim, and return
    the fields ``create_annotation`` wants plus the ``section_content`` they
    were resolved against.

    This is the door for a surface that has the selected TEXT and not offsets
    into the content of record — ``doc_detail`` renders a section through
    markdown, so a DOM offset is an offset into the rendered HTML and not into
    the stored content. ``resolve_anchor`` decides: found once is ``relocated``
    (an honest ``str.find`` guess), ambiguous or absent is ``unanchored``, and
    supplied offsets that verify are ``exact``.

    A section that cannot be read yields an ``unanchored`` comment rather than
    a refusal: the remark is still worth keeping, it simply has no span.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        content = _section_text(conn, section_id)
    finally:
        if own:
            conn.close()

    if content is None or not (anchor_text or "").strip():
        return {"anchor_basis": "unanchored", "anchor_start": None,
                "anchor_end": None, "anchor_text": anchor_text or None,
                "section_content": content,
                "reason": "section_unreadable" if content is None else "no_selection"}

    verdict = resolve_anchor(content, anchor_text,
                             anchor_start=anchor_start, anchor_end=anchor_end)
    if verdict["anchor_basis"] == "unanchored":
        # Ambiguous or absent. The comment is kept; the span is not invented.
        return {**verdict, "section_content": content,
                "anchor_start": None, "anchor_end": None}
    return {**verdict, "section_content": content}


# ── Reads ─────────────────────────────────────────────────────────────────────

def _section_text(conn, section_id: str) -> str | None:
    """The section's content of record, or None when it cannot be read.

    None is the ``unverifiable`` input — a missing section row and an
    unreadable one are the same fact for this purpose, and neither is an
    anchor that still holds.
    """
    try:
        row = conn.execute(
            "SELECT content FROM dic_sections WHERE section_id = %s LIMIT 1",
            (section_id,),
        ).fetchone()
    except Exception:
        return None
    if row is None:
        return None
    d = dict(row)
    content = d.get("content", next(iter(d.values()), None))
    return content if isinstance(content, str) else None


def _tenant_clause(tenant_id: str | None) -> tuple[str, list]:
    """Scope a read to one tenant, keeping rows written before tenant scoping.

    A NULL ``tenant_id`` is a row written before migration 20260908065203. There
    are provably zero of those on this deployment (0 rows lifetime, measured
    2026-09-08) — but dropping one silently would make an old comment vanish
    with no word, which is worse than showing it.
    """
    if not tenant_id:
        return "", []
    return " AND (tenant_id = %s OR tenant_id IS NULL)", [tenant_id]


def list_annotations(section_id: str, *, tenant_id: str | None = None,
                     status: str | None = None, category: str | None = None,
                     conn=None) -> list[dict]:
    """Every annotation on a section, flat, each carrying its anchor verdict.

    Filters apply to the ROW. ``list_threads`` is what a reviewer reads; this is
    the flat, back-compatible shape and what the count badge counts.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        _ensure_tables(conn)
        sql = f"SELECT * FROM {TABLE} WHERE section_id = %s"
        params: list = [section_id]
        clause, extra = _tenant_clause(tenant_id)
        sql += clause
        params += extra
        if status:
            sql += " AND status = %s"
            params.append(status)
        if category:
            sql += " AND category = %s"
            params.append(category)
        sql += " ORDER BY created_at ASC, ann_id ASC"
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        text = _section_text(conn, section_id)
    finally:
        if own:
            conn.close()

    for r in rows:
        # A reply has no span of its own; it inherits the thread's.
        r["anchor"] = dict(_REPLY_ANCHOR) if r.get("parent_ann_id") else anchor_state(text, r)
    return rows


def list_threads(section_id: str, *, tenant_id: str | None = None,
                 status: str | None = None, category: str | None = None,
                 conn=None) -> list[dict]:
    """Roots with their replies attached, oldest first.

    ``status``/``category`` filter the THREAD (they are the root's), so a
    resolved thread never comes back split from its replies. A reply whose root
    is GONE is returned as its own entry carrying ``thread_broken`` rather than
    being dropped — a dropped row is a comment that silently ceases to exist.
    """
    rows = list_annotations(section_id, tenant_id=tenant_id, conn=conn)
    known = {r["ann_id"] for r in rows}
    roots: list[dict] = []
    for r in rows:
        if r.get("parent_ann_id"):
            continue
        if status and r.get("status") != status:
            continue
        if category and r.get("category") != category:
            continue
        entry = dict(r)
        entry["replies"] = []
        roots.append(entry)
    index = {r["ann_id"]: r for r in roots}
    for r in rows:
        parent = r.get("parent_ann_id")
        if not parent:
            continue
        if parent in index:
            index[parent]["replies"].append(r)
        elif parent not in known:
            stray = dict(r)
            stray["replies"] = []
            stray["thread_broken"] = True
            roots.append(stray)
    for r in roots:
        r["reply_count"] = len(r["replies"])
        r["thread_status"] = r.get("status")
    roots.sort(key=lambda x: (x.get("created_at") or "", x["ann_id"]))
    return roots


def get_annotation(ann_id: str, *, conn=None) -> dict | None:
    own = conn is None
    conn = conn or get_connection()
    try:
        _ensure_tables(conn)
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE ann_id = %s", (ann_id,)
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        text = _section_text(conn, out.get("section_id") or "")
    finally:
        if own:
            conn.close()
    out["anchor"] = dict(_REPLY_ANCHOR) if out.get("parent_ann_id") else anchor_state(text, out)
    return out


# ── Writes ────────────────────────────────────────────────────────────────────

class AnnotationError(ValueError):
    """A refused write. Nothing is written on a refusal."""


def _validate_reply(conn, parent_ann_id: str, section_id: str,
                    anchor_basis: str, anchor_start, anchor_end) -> dict:
    parent = conn.execute(
        f"SELECT * FROM {TABLE} WHERE ann_id = %s", (parent_ann_id,)
    ).fetchone()
    if parent is None:
        raise AnnotationError(f"parent_ann_id {parent_ann_id!r} does not exist")
    parent = dict(parent)
    if parent.get("parent_ann_id"):
        raise AnnotationError(
            "threads are flat: reply to the thread's first comment, not to a reply")
    if parent.get("section_id") != section_id:
        raise AnnotationError(
            "a reply belongs to the same section as the comment it answers")
    if anchor_start is not None or anchor_end is not None or anchor_basis != "unanchored":
        raise AnnotationError(
            "a reply carries no anchor of its own — the span belongs to the thread")
    return parent


def create_annotation(
    *,
    section_id: str,
    doc_id: str = "",
    category: str,
    comment: str,
    author: str = "reviewer",
    selected_text: str = "",
    parent_ann_id: str | None = None,
    anchor_start: int | None = None,
    anchor_end: int | None = None,
    anchor_text: str | None = None,
    anchor_basis: str = "unanchored",
    section_content: str | None = None,
    tenant_id: str = "",
    classification: str = "CUI",
    conn=None,
) -> dict:
    """Insert one comment — a thread root, or a reply when ``parent_ann_id`` is set.

    ``section_content`` is the section's content of record, and an anchored
    write is verified against it by ``validate_anchor`` before anything is
    written. When it is not supplied, the section's live content is read.

    Raises ``AnnotationError`` on any inconsistency; nothing is written.
    """
    category = (category or "").strip()
    comment = (comment or "").strip()
    if category not in CATEGORIES:
        raise AnnotationError(f"category must be one of {sorted(CATEGORIES)}")
    if not comment:
        raise AnnotationError("comment is required")
    if not section_id:
        raise AnnotationError("section_id is required")
    if anchor_basis not in ANCHOR_BASES:
        raise AnnotationError(f"anchor_basis must be one of {ANCHOR_BASES}")

    own = conn is None
    conn = conn or get_connection()
    try:
        _ensure_tables(conn)
        if parent_ann_id:
            parent = _validate_reply(conn, parent_ann_id, section_id,
                                     anchor_basis, anchor_start, anchor_end)
            doc_id = doc_id or parent.get("doc_id") or ""
            tenant_id = tenant_id or parent.get("tenant_id") or ""
            # A reply's position is the thread's. Recording a text it was
            # "looking for" would put a second, unverified span on the thread.
            anchor_text = None

        if anchor_basis != "unanchored":
            content = section_content
            if content is None:
                content = _section_text(conn, section_id)
            if content is None:
                raise AnnotationError(
                    f"an {anchor_basis} anchor needs the section's content of record; "
                    f"section {section_id!r} could not be read")
            # THE ONE anchor rule — imported, not re-derived.
            try:
                validate_anchor(
                    anchor_basis=anchor_basis, anchor_section_id=section_id,
                    anchor_start=anchor_start, anchor_end=anchor_end,
                    anchor_text=anchor_text, current_content=content,
                )
            except ValueError as exc:
                raise AnnotationError(str(exc)) from exc
            # Display follows the verified slice, so the panel and the check
            # cannot render two different "selected texts".
            selected_text = anchor_text or ""
        elif anchor_start is not None or anchor_end is not None:
            raise AnnotationError(
                "an unanchored comment carries no offsets; record the basis "
                "the span actually has")

        ann_id = f"ann_{uuid.uuid4().hex[:16]}"
        now = _now()
        conn.execute(
            f"""INSERT INTO {TABLE}
                (ann_id, section_id, doc_id, selected_text, category, comment,
                 author, status, classification, created_at, updated_at,
                 parent_ann_id, anchor_start, anchor_end, anchor_text,
                 anchor_basis, tenant_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (ann_id, section_id, doc_id, selected_text or "", category, comment,
             author or "reviewer", classification, now, now,
             parent_ann_id or None, anchor_start, anchor_end,
             anchor_text, anchor_basis, tenant_id or None),
        )
        conn.commit()
        row = get_annotation(ann_id, conn=conn)
    finally:
        if own:
            conn.close()
    return row


def update_annotation(ann_id: str, *, conn=None, **fields) -> dict:
    """Edit a comment's text or category. NOT the lifecycle and NOT the anchor.

    ``resolve_thread``/``reopen_thread`` own ``status``; the anchor is set at
    write time and re-derived on read, never edited into agreement.
    """
    allowed = {"comment", "category"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        raise AnnotationError(f"no editable fields; allowed: {sorted(allowed)}")
    if "category" in updates and updates["category"] not in CATEGORIES:
        raise AnnotationError(f"category must be one of {sorted(CATEGORIES)}")
    if "comment" in updates and not str(updates["comment"]).strip():
        raise AnnotationError("comment cannot be emptied")
    updates["updated_at"] = _now()

    own = conn is None
    conn = conn or get_connection()
    try:
        _ensure_tables(conn)
        if get_annotation(ann_id, conn=conn) is None:
            raise AnnotationError(f"annotation {ann_id!r} does not exist")
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        conn.execute(f"UPDATE {TABLE} SET {set_clause} WHERE ann_id = %s",
                     [*updates.values(), ann_id])
        conn.commit()
        return get_annotation(ann_id, conn=conn)
    finally:
        if own:
            conn.close()


def _require_root(conn, ann_id: str) -> dict:
    row = get_annotation(ann_id, conn=conn)
    if row is None:
        raise AnnotationError(f"annotation {ann_id!r} does not exist")
    if row.get("parent_ann_id"):
        raise AnnotationError(
            "resolve the thread, not a reply: a reply has no lifecycle of its own")
    return row


def resolve_thread(ann_id: str, *, resolved_by: str, note: str = "",
                   conn=None) -> dict:
    """Resolve a THREAD. Writes the root only — replies have no second lifecycle."""
    if not (resolved_by or "").strip():
        raise AnnotationError("resolved_by is required")
    own = conn is None
    conn = conn or get_connection()
    try:
        _ensure_tables(conn)
        _require_root(conn, ann_id)
        now = _now()
        conn.execute(
            f"""UPDATE {TABLE} SET status = 'resolved', resolved_by = %s,
                resolved_at = %s, resolution_note = %s, updated_at = %s
                WHERE ann_id = %s""",
            (resolved_by, now, note or None, now, ann_id),
        )
        conn.commit()
        return get_annotation(ann_id, conn=conn)
    finally:
        if own:
            conn.close()


def reopen_thread(ann_id: str, *, conn=None) -> dict:
    """Reopen a resolved thread. The resolution that WAS recorded is cleared:
    a row reading ``open`` while naming who resolved it says two things."""
    own = conn is None
    conn = conn or get_connection()
    try:
        _ensure_tables(conn)
        _require_root(conn, ann_id)
        conn.execute(
            f"""UPDATE {TABLE} SET status = 'open', resolved_by = NULL,
                resolved_at = NULL, resolution_note = NULL, updated_at = %s
                WHERE ann_id = %s""",
            (_now(), ann_id),
        )
        conn.commit()
        return get_annotation(ann_id, conn=conn)
    finally:
        if own:
            conn.close()


def delete_thread(ann_id: str, *, conn=None) -> dict:
    """Delete a comment. Deleting a ROOT takes its replies with it.

    A reply whose root is gone is a remark answering nothing; leaving it behind
    is how a thread becomes a set of unattributed fragments.
    """
    own = conn is None
    conn = conn or get_connection()
    try:
        _ensure_tables(conn)
        row = get_annotation(ann_id, conn=conn)
        if row is None:
            raise AnnotationError(f"annotation {ann_id!r} does not exist")
        deleted_replies = 0
        if not row.get("parent_ann_id"):
            cur = conn.execute(f"SELECT ann_id FROM {TABLE} WHERE parent_ann_id = %s",
                               (ann_id,))
            deleted_replies = len(cur.fetchall())
            conn.execute(f"DELETE FROM {TABLE} WHERE parent_ann_id = %s", (ann_id,))
        conn.execute(f"DELETE FROM {TABLE} WHERE ann_id = %s", (ann_id,))
        conn.commit()
        return {"deleted": ann_id, "deleted_replies": deleted_replies,
                "was_reply": bool(row.get("parent_ann_id"))}
    finally:
        if own:
            conn.close()
