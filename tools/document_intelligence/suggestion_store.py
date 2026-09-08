# CUI // SP-CTI
"""DIC Suggestion Store — lifecycle management for canvas-triggered doc update suggestions.

Tables (lazy-init via _ensure_tables):
  dic_suggestions            — mutable; one row per AI-drafted or crowdsourced suggestion
  dic_suggestion_decisions   — append-only, NIST AU; one row per accept/reject decision

Public API:
  create_suggestion(...)  -> str  (suggestion_id)
  get_pending_suggestions(collection_id=None, canvas_source=None) -> list[dict]
  get_suggestion(suggestion_id) -> dict | None
  decide_suggestion(suggestion_id, decision, decided_by, note='') -> bool
  record_application(suggestion_id, applied_text, applied_by) -> bool
  resolve_anchor(section_text, anchor_text, anchor_start=, anchor_end=) -> dict
  whole_section_anchor(section_id, content) -> dict
  verify_anchor(suggestion, section_content) -> dict      (dwr-anchor-05, pure)
  supersede_suggestion(suggestion_id, reason, ...) -> bool (dwr-anchor-05)

THE ANCHOR (dwr-anchor-03). A suggestion is an ADDRESSABLE change: it names the
section it lives in and the verbatim span it replaces, and it says HOW it knows.

  anchor_section_id  the section of record. NOT NULL for anything appliable.
  anchor_start/end   offsets into that section's content (chunk-local, the
                     claim_lifecycle convention).
  anchor_text        the VERBATIM slice. ``content[start:end] == anchor_text``
                     is the invariant; ``create_suggestion`` checks it against
                     ``current_content`` at write time and the accept path
                     re-derives it against the live section (dwr-anchor-05).
  anchor_basis       RECORDED by the writer, never inferred as ``exact``:
                       exact       the span came from the match that found it
                       relocated   recovered post-hoc by ``str.find`` — a guess,
                                   and only admitted when the text occurs ONCE
                       unanchored  no span, or an ambiguous one. Never applied.
                     This mirrors ``doc_modernization.claim_extractor.anchor``,
                     which REJECTS a non-verbatim candidate instead of guessing.
  origin_kind        docmod_redline | section_draft | crowdsource | human_edit.
                     NULL = the writer did not say.
  applied_text/by    what was ACTUALLY written on edit-then-accept. The AI draft
                     (``suggested_content``) passed the TRUST gates; a human
                     rewrite did not, so the two are stored apart and provenance
                     says which shipped.

CONTRACT FOR AN ANCHORED WRITE: when ``anchor_basis`` is ``exact`` or
``relocated``, ``current_content`` MUST be the section's content of record —
that is the text the offsets index — not a fragment of it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tools.db.storage import get_connection

_VALID_DECISIONS = ("accepted", "rejected")
_VALID_STATUSES = ("pending", "accepted", "rejected", "superseded")

#: dwr-anchor-05. A suggestion whose anchor no longer resolves against the live
#: section is SUPERSEDED, the ``claim_lifecycle.verify_claim_anchors`` verdict.
#: It is not a human decision -- ``decide_suggestion`` refuses it -- but it is
#: recorded on the same append-only ``dic_suggestion_decisions`` chain so a
#: reader can tell "nobody decided" from "the anchor drifted before anyone could".
SUPERSEDED_DECISION = "superseded"

#: The bases the accept path may apply. ``unanchored`` is never in this tuple.
APPLIABLE_BASES = ("exact", "relocated")

ANCHOR_BASES = ("exact", "relocated", "unanchored")

#: Every ``reason`` ``verify_anchor`` can return when it refuses. Declared here
#: so a reader that has to TRANSLATE those reasons (dwr-cmt-02's review rail
#: maps them onto the comment vocabulary) can be pinned against this tuple
#: rather than against a hand-copied list that silently goes out of date.
VERIFY_REASONS = ("section_missing", "unanchored", "no_offsets", "anchor_stale")
ORIGIN_KINDS = ("docmod_redline", "section_draft", "crowdsource", "human_edit")

# Declaration order of the columns dwr-anchor-03 added. The migration that
# reaches an EXISTING table (20260907213944) carries the same tuple, pinned by
# test; `_ensure_tables` below carries them for a table created after it landed.
ANCHOR_COLUMNS = (
    ("anchor_section_id", "TEXT"),
    ("anchor_start", "INTEGER"),
    ("anchor_end", "INTEGER"),
    ("anchor_text", "TEXT"),
    ("anchor_basis", "TEXT"),
    ("origin_kind", "TEXT"),
    ("applied_text", "TEXT"),
    ("applied_by", "TEXT"),
)


# ── Schema ────────────────────────────────────────────────────────────────────

def _ensure_tables(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dic_suggestions (
            suggestion_id       TEXT    PRIMARY KEY,
            section_id          TEXT,
            doc_id              TEXT,
            collection_id       TEXT,
            trigger_event_id    TEXT,
            canvas_source       TEXT    NOT NULL DEFAULT 'unknown',
            suggested_content   TEXT    NOT NULL DEFAULT '',
            current_content     TEXT,
            rationale           TEXT,
            status              TEXT    NOT NULL DEFAULT 'pending',
            created_at          TEXT    NOT NULL,
            updated_at          TEXT,
            tenant_id           TEXT,
            classification      TEXT    NOT NULL DEFAULT 'CUI',
            anchor_section_id   TEXT,
            anchor_start        INTEGER,
            anchor_end          INTEGER,
            anchor_text         TEXT,
            anchor_basis        TEXT,
            origin_kind         TEXT,
            applied_text        TEXT,
            applied_by          TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dic_suggestion_decisions (
            decision_id     TEXT    PRIMARY KEY,
            suggestion_id   TEXT    NOT NULL,
            decision        TEXT    NOT NULL,
            decided_by      TEXT,
            decided_at      TEXT    NOT NULL,
            note            TEXT,
            tenant_id       TEXT,
            classification  TEXT    NOT NULL DEFAULT 'CUI'
        )
        """
    )
    conn.commit()


# ── Anchor resolution ─────────────────────────────────────────────────────────

def resolve_anchor(
    section_text: str,
    anchor_text: str,
    *,
    anchor_start: int | None = None,
    anchor_end: int | None = None,
) -> dict:
    """Decide what basis a span can honestly claim against ``section_text``.

    Returns ``{"anchor_basis", "anchor_start", "anchor_end", "anchor_text",
    "reason"}``.

      * offsets supplied AND ``section_text[start:end] == anchor_text``
                                                    -> ``exact``
      * otherwise, ``anchor_text`` occurs exactly ONCE -> ``relocated`` at the
        offsets ``str.find`` recovered. Honest about being a guess.
      * not found, found MORE THAN ONCE, or nothing to search for
                                                    -> ``unanchored``, offsets None.
        An ambiguous match is never resolved by picking one; that is the
        ``claim_extractor.anchor`` rule (reject rather than guess).

    Pure — reads nothing, writes nothing.
    """
    section_text = section_text or ""
    anchor_text = "" if anchor_text is None else anchor_text

    if anchor_start is not None and anchor_end is not None:
        try:
            s, e = int(anchor_start), int(anchor_end)
        except (TypeError, ValueError):
            s = e = -1
        if 0 <= s <= e <= len(section_text) and section_text[s:e] == anchor_text:
            return {"anchor_basis": "exact", "anchor_start": s, "anchor_end": e,
                    "anchor_text": anchor_text, "reason": "offsets_verified"}

    if not anchor_text or not section_text:
        return {"anchor_basis": "unanchored", "anchor_start": None,
                "anchor_end": None, "anchor_text": anchor_text or None,
                "reason": "nothing_to_search"}

    occurrences = section_text.count(anchor_text)
    if occurrences == 1:
        idx = section_text.find(anchor_text)
        return {"anchor_basis": "relocated", "anchor_start": idx,
                "anchor_end": idx + len(anchor_text), "anchor_text": anchor_text,
                "reason": "found_once"}
    if occurrences == 0:
        reason = "not_found"
    else:
        reason = f"ambiguous:{occurrences}"
    return {"anchor_basis": "unanchored", "anchor_start": None,
            "anchor_end": None, "anchor_text": anchor_text, "reason": reason}


def whole_section_anchor(section_id: str, content: str) -> dict:
    """An ``exact`` anchor over a whole section — the shape a full-section
    replacement (crowdsource proposal, canvas-triggered section draft) writes.
    ``content`` must be the section's content of record."""
    content = content or ""
    return {"anchor_section_id": section_id, "anchor_start": 0,
            "anchor_end": len(content), "anchor_text": content,
            "anchor_basis": "exact"}


def section_of_record(suggestion: dict) -> str:
    """Which section a suggestion is ABOUT — ONE statement of the rule.

    The anchor's section outranks the legacy ``section_id`` column when both
    are set; the legacy column is what a pre-anchor row carries. The accept
    door and dwr-cmt-02's review rail both ask HERE, because a second copy is
    how a rail renders a proposal beside one section while accept splices it
    into another.

    Returns ``""`` for a suggestion that names NO section. That is a real state
    on this deployment, not an edge case: measured 2026-09-08, all 58 rows in
    ``dic_suggestions`` carry a real ``doc_id``, ``section_id = ''`` and
    ``anchor_section_id`` NULL. An empty string must never be resolved to
    "the first section" by a caller — it means the proposal cannot be placed.
    """
    return (suggestion.get("anchor_section_id")
            or suggestion.get("section_id") or "")


def verify_anchor(suggestion: dict, section_content: str | None) -> dict:
    """Re-derive, at ACCEPT time, whether a suggestion's anchor still holds
    against the section as it is NOW (dwr-anchor-05).

    This is the ``claim_lifecycle.verify_claim_anchors`` discipline applied to
    a suggestion: the anchor is valid iff
    ``section_content[anchor_start:anchor_end] == anchor_text``. What was
    verified when the row was WRITTEN says nothing about the section today --
    a human edit, a regenerated draft or an earlier accepted suggestion can
    all have moved the text -- so the accept path asks again and trusts only
    this answer.

    Returns ``{"ok", "reason", "anchor_basis", "anchor_start", "anchor_end",
    "anchor_text", "found_text"}``. ``reason`` when ``ok`` is False:

      section_missing  ``section_content`` is None -- no row to splice into
      unanchored       basis is not in ``APPLIABLE_BASES`` (never applied)
      no_offsets       an appliable basis with no integer offsets -- a row the
                       store's own validation would have refused; treated as
                       stale rather than guessed at
      anchor_stale     the offsets no longer index ``anchor_text``; ``found_text``
                       is what the slice holds now, for the reader

    Pure -- reads nothing, writes nothing.
    """
    basis = suggestion.get("anchor_basis")
    start = suggestion.get("anchor_start")
    end = suggestion.get("anchor_end")
    text = suggestion.get("anchor_text")
    out = {"ok": False, "reason": None, "anchor_basis": basis,
           "anchor_start": start, "anchor_end": end, "anchor_text": text,
           "found_text": None}
    if section_content is None:
        out["reason"] = "section_missing"
        return out
    if basis not in APPLIABLE_BASES:
        out["reason"] = "unanchored"
        return out
    if not isinstance(start, int) or not isinstance(end, int)             or isinstance(start, bool) or isinstance(end, bool) or not isinstance(text, str):
        out["reason"] = "no_offsets"
        return out
    if 0 <= start <= end <= len(section_content) and section_content[start:end] == text:
        out["ok"] = True
        out["found_text"] = text
        return out
    out["reason"] = "anchor_stale"
    if 0 <= start <= end:
        out["found_text"] = section_content[start:end]
    return out


def validate_anchor(
    *,
    anchor_basis: str,
    anchor_section_id: str | None,
    anchor_start: int | None,
    anchor_end: int | None,
    anchor_text: str | None,
    current_content: str | None,
) -> None:
    """Raise ``ValueError`` unless the anchor fields are mutually consistent.

    ``unanchored`` carries NO offsets — a writer holding a span has an anchor
    and must say which basis it has. ``anchor_text`` may still be recorded on
    an unanchored row: it is the text the writer was looking for.

    ``exact`` / ``relocated`` need a section, integer offsets with
    ``0 <= start <= end``, a text whose length is the span's, and — the
    invariant — ``current_content[start:end] == anchor_text``. ``relocated``
    additionally needs a non-empty text: ``str.find("")`` is 0 and means nothing.

    ``current_content`` here is THE STRING THE OFFSETS INDEX INTO — the
    section's content of record. For a whole-section proposal that is also the
    row's ``current_content`` column, which is why the two were one parameter
    until dwr-anchor-04. A PASSAGE-level change records the passage as its
    before-text and the section content here, and the caller keeps them apart
    (``create_suggestion(anchor_content=...)``): verifying the span against the
    passage would prove only that a string contains itself.
    """
    if anchor_basis not in ANCHOR_BASES:
        raise ValueError(f"anchor_basis must be one of {ANCHOR_BASES}, got {anchor_basis!r}")

    if anchor_basis == "unanchored":
        if anchor_start is not None or anchor_end is not None:
            raise ValueError("an unanchored suggestion carries no offsets; "
                             "record the basis the span actually has")
        return

    if not anchor_section_id:
        raise ValueError(f"a {anchor_basis} anchor needs anchor_section_id")
    if not isinstance(anchor_start, int) or not isinstance(anchor_end, int) \
            or isinstance(anchor_start, bool) or isinstance(anchor_end, bool):
        raise ValueError(f"a {anchor_basis} anchor needs integer anchor_start/anchor_end")
    if anchor_start < 0 or anchor_end < anchor_start:
        raise ValueError(f"anchor offsets must satisfy 0 <= start <= end, got {anchor_start}:{anchor_end}")
    if not isinstance(anchor_text, str):
        raise ValueError(f"a {anchor_basis} anchor needs anchor_text")
    if anchor_basis == "relocated" and not anchor_text:
        raise ValueError("a relocated anchor needs a non-empty anchor_text")
    if len(anchor_text) != anchor_end - anchor_start:
        raise ValueError(f"anchor_text length {len(anchor_text)} does not match span "
                         f"{anchor_start}:{anchor_end}")
    if current_content is None:
        raise ValueError(f"a {anchor_basis} anchor needs current_content (the section's "
                         "content of record) to verify against")
    if anchor_end > len(current_content) or current_content[anchor_start:anchor_end] != anchor_text:
        raise ValueError("anchor_text is not the verbatim slice "
                         f"current_content[{anchor_start}:{anchor_end}]")


def _validate_origin_kind(origin_kind: str | None) -> None:
    if origin_kind is not None and origin_kind not in ORIGIN_KINDS:
        raise ValueError(f"origin_kind must be one of {ORIGIN_KINDS} or None, got {origin_kind!r}")


def _validate_applied(applied_text: str | None, applied_by: str | None) -> None:
    # Half an application record is a claim with no author, or an author with
    # no claim. Both halves or neither.
    if (applied_text is None) != (applied_by is None or applied_by == ""):
        raise ValueError("applied_text and applied_by are recorded together or not at all")


# ── Public API ────────────────────────────────────────────────────────────────

def create_suggestion(
    *,
    section_id: str = "",
    doc_id: str = "",
    collection_id: str = "",
    trigger_event_id: str | None = None,
    canvas_source: str = "unknown",
    suggested_content: str,
    current_content: str = "",
    rationale: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
    anchor_section_id: str | None = None,
    anchor_start: int | None = None,
    anchor_end: int | None = None,
    anchor_text: str | None = None,
    anchor_basis: str = "unanchored",
    anchor_content: str | None = None,
    origin_kind: str | None = None,
    applied_text: str | None = None,
    applied_by: str | None = None,
) -> str:
    """Insert a new pending suggestion and return its suggestion_id.

    ``anchor_content`` is the section's content of record — the string
    ``anchor_start``/``anchor_end`` index into — and defaults to
    ``current_content``. Pass it only when the two differ: a passage-level
    change (``docmod_redline``) stores the PASSAGE it replaces in
    ``current_content``, so the exact-slice invariant has to be checked against
    the section, not against the before-text. It is used for validation ONLY
    and is never persisted; the row already names its section.

    Raises ``ValueError`` when the anchor fields are inconsistent (see
    ``validate_anchor``), ``origin_kind`` is not a declared kind, or an
    application record is half-supplied. Nothing is written on a refusal.
    """
    validate_anchor(
        anchor_basis=anchor_basis, anchor_section_id=anchor_section_id,
        anchor_start=anchor_start, anchor_end=anchor_end, anchor_text=anchor_text,
        current_content=current_content if anchor_content is None else anchor_content,
    )
    _validate_origin_kind(origin_kind)
    _validate_applied(applied_text, applied_by)

    suggestion_id = f"sug_{uuid.uuid4().hex[:16]}"
    now = _now()
    with get_connection() as conn:
        _ensure_tables(conn)
        conn.execute(
            """
            INSERT INTO dic_suggestions
                (suggestion_id, section_id, doc_id, collection_id,
                 trigger_event_id, canvas_source, suggested_content,
                 current_content, rationale, status,
                 created_at, updated_at, tenant_id, classification,
                 anchor_section_id, anchor_start, anchor_end, anchor_text,
                 anchor_basis, origin_kind, applied_text, applied_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                suggestion_id, section_id, doc_id, collection_id,
                trigger_event_id, canvas_source, suggested_content,
                current_content, rationale,
                now, now, tenant_id, classification,
                anchor_section_id or None, anchor_start, anchor_end, anchor_text,
                anchor_basis, origin_kind, applied_text, applied_by or None,
            ),
        )
        conn.commit()
        # dsyn-patch-02: best-effort notification to editors/reviewers of this collection
        _notify_collection_members(conn, suggestion_id, collection_id, canvas_source, rationale, now)
    return suggestion_id


def _notify_collection_members(conn, suggestion_id: str, collection_id: str,
                                canvas_source: str, rationale: str, now: str) -> None:
    """Insert notification_log rows for editors/reviewers of this collection.

    Best-effort: any exception is swallowed — notification failure never blocks creation.
    """
    try:
        import zlib
        members = conn.execute(
            """
            SELECT user_id FROM dic_team_access
            WHERE collection_id = %s AND role IN ('editor', 'reviewer')
            """,
            (collection_id,),
        ).fetchall()
        for member in members:
            uid = _col(member, "user_id", 0) or ""
            if not uid:
                continue
            log_id = f"nlog-{format(zlib.crc32(f'{now}{suggestion_id}{uid}'.encode()) & 0xFFFFFFFF, '08x')}"
            conn.execute(
                """
                INSERT INTO notification_log
                    (id, event_type, adapter, severity, title, delivered, error, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    log_id, "dic_suggestion_created", "dic_suggestion_store",
                    "info",
                    f"DIC suggestion from {canvas_source}: {rationale[:80]}",
                    False, None, now,
                ),
            )
        conn.commit()
    except Exception as exc:
        from tools.logging.icdev_logger import get_logger as _gl
        _gl(__name__).debug("suggestion_store: notification emit error: %s", exc)


def get_pending_suggestions(
    collection_id: str | None = None,
    canvas_source: str | None = None,
    status: str = "pending",
) -> list[dict]:
    """Return suggestions filtered by optional collection_id, canvas_source, and status."""
    clauses = ["status = %s"]
    params: list = [status]

    if collection_id:
        clauses.append("collection_id = %s")
        params.append(collection_id)
    if canvas_source:
        clauses.append("canvas_source = %s")
        params.append(canvas_source)

    sql = f"SELECT * FROM dic_suggestions WHERE {' AND '.join(clauses)} ORDER BY created_at DESC"

    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute(sql, params).fetchall()

    return [_row_to_dict(r) for r in rows]


def get_suggestion(suggestion_id: str) -> dict | None:
    """Return a single suggestion by ID, or None if not found."""
    with get_connection() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT * FROM dic_suggestions WHERE suggestion_id = %s",
            (suggestion_id,),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


def decide_suggestion(
    suggestion_id: str,
    decision: str,
    decided_by: str,
    note: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Accept or reject a pending suggestion.

    Returns True on success, False if the suggestion doesn't exist or
    was already decided.  Raises ValueError for an invalid decision value.
    """
    if decision not in _VALID_DECISIONS:
        raise ValueError(f"decision must be one of {_VALID_DECISIONS}, got '{decision}'")

    now = _now()
    decision_id = f"dec_{uuid.uuid4().hex[:16]}"

    with get_connection() as conn:
        _ensure_tables(conn)

        row = conn.execute(
            "SELECT status FROM dic_suggestions WHERE suggestion_id = %s",
            (suggestion_id,),
        ).fetchone()

        if row is None:
            return False

        current_status = _col(row, "status", 0)
        if current_status != "pending":
            return False

        conn.execute(
            "UPDATE dic_suggestions SET status=%s, updated_at=%s WHERE suggestion_id=%s",
            (decision, now, suggestion_id),
        )
        conn.execute(
            """
            INSERT INTO dic_suggestion_decisions
                (decision_id, suggestion_id, decision, decided_by,
                 decided_at, note, tenant_id, classification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                decision_id, suggestion_id, decision, decided_by,
                now, note, tenant_id, classification,
            ),
        )
        conn.commit()

    return True


def record_application(suggestion_id: str, applied_text: str, applied_by: str) -> bool:
    """Record what was ACTUALLY written to the document for an ACCEPTED suggestion.

    Called by the accept path AFTER the decision row and its audit row stand and
    the splice has been made — never before, so the ordering invariant
    (decision -> audit -> apply) is untouched. ``applied_text`` is stored beside
    ``suggested_content``, never over it: the AI draft passed the TRUST gates
    and a human rewrite did not, and provenance must say which one shipped.

    Returns False when the row is missing or is not ``accepted``; raises
    ``ValueError`` on a half-supplied record.
    """
    _validate_applied(applied_text, applied_by)
    if applied_text is None:
        raise ValueError("record_application needs applied_text and applied_by")

    with get_connection() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT status FROM dic_suggestions WHERE suggestion_id = %s",
            (suggestion_id,),
        ).fetchone()
        if row is None or _col(row, "status", 0) != "accepted":
            return False
        conn.execute(
            "UPDATE dic_suggestions SET applied_text=%s, applied_by=%s, updated_at=%s "
            "WHERE suggestion_id=%s",
            (applied_text, applied_by, _now(), suggestion_id),
        )
        conn.commit()
    return True


def supersede_suggestion(
    suggestion_id: str,
    reason: str,
    *,
    superseded_by: str = "system:anchor_verify",
    note: str = "",
    tenant_id: str = "",
    classification: str = "CUI",
) -> bool:
    """Move a PENDING suggestion to ``superseded`` and record why (dwr-anchor-05).

    The accept path calls this when the anchor no longer resolves against the
    live section -- the document moved on underneath the proposal, so what the
    reviewer would be accepting is no longer what the drafter proposed. The
    document is NOT touched. The reason (``anchor_stale`` and what the slice
    holds now) rides on an append-only ``dic_suggestion_decisions`` row with
    ``decision = 'superseded'``; ``decided_by`` names the mechanism, never a
    human, so the row can never be read as a person's verdict.

    Returns False when the row is missing or no longer pending -- a concurrent
    human decision wins and is not overwritten.
    """
    if not reason:
        raise ValueError("supersede_suggestion needs a reason")
    now = _now()
    decision_id = f"dec_{uuid.uuid4().hex[:16]}"
    with get_connection() as conn:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT status FROM dic_suggestions WHERE suggestion_id = %s",
            (suggestion_id,),
        ).fetchone()
        if row is None or _col(row, "status", 0) != "pending":
            return False
        conn.execute(
            "UPDATE dic_suggestions SET status=%s, updated_at=%s WHERE suggestion_id=%s",
            ("superseded", now, suggestion_id),
        )
        conn.execute(
            """
            INSERT INTO dic_suggestion_decisions
                (decision_id, suggestion_id, decision, decided_by,
                 decided_at, note, tenant_id, classification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                decision_id, suggestion_id, SUPERSEDED_DECISION, superseded_by,
                now, f"{reason}: {note}" if note else reason, tenant_id, classification,
            ),
        )
        conn.commit()
    return True


def get_decisions_for_suggestion(suggestion_id: str) -> list[dict]:
    """Return all decision rows for a suggestion (audit trail)."""
    with get_connection() as conn:
        _ensure_tables(conn)
        rows = conn.execute(
            "SELECT * FROM dic_suggestion_decisions WHERE suggestion_id = %s ORDER BY decided_at",
            (suggestion_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _col(row, name: str, index: int):
    """Access a row column by name (sqlite3.Row) or positional index (tuple)."""
    if isinstance(row, (list, tuple)):
        return row[index]
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return row
    try:
        return dict(row)
    except Exception:
        return {}
