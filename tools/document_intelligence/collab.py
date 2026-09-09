# CUI // SP-CTI
r"""Two reviewers in one document, without a lost write (dwr-collab-01).

dwr-ws-03 made a decision apply IN PLACE: accept a change and the card, the
section and the counts move without a reload. It moves them for THE REVIEWER
WHO CLICKED, and for nobody else. A second reviewer with the same document open
keeps a rail drawn at page-load: a change another human accepted ten minutes ago
still offers Accept / Reject, and the document beside it still shows the text
that change replaced.

This module is the feed that closes that gap, and it is a POLL. Following D103,
polling is this platform's primary transport -- proxy/firewall friendly for DoD
networks, and it works with Flask's synchronous WSGI, which an SSE stream ties a
worker up for. The presence SSE stream this canvas already carries
(``/api/documents/<id>/presence/stream``) is exactly that cost, and this module
deliberately does not add a second one. The CLIENT pattern is reused from
``static/js/live.js`` -- cursor, 3 s interval, exponential backoff, status dot --
and its ENDPOINT is not: ``/api/events/poll`` is bound to ``hook_events``, a
platform-wide tool-call feed that has never known what a document is.

## The cursor is a TIMESTAMP, compared as TEXT, and that is sound for one reason

``dic_suggestions.updated_at`` and ``dic_edit_history.edited_at`` are written by
``datetime.now(timezone.utc).isoformat()`` at every writer in this canvas
(``suggestion_store._now``, ``history_recorder._now_iso``, ``blueprint._now``).
Every stamp therefore carries the SAME ``+00:00`` offset, and for a fixed offset
ISO-8601 orders lexicographically -- which is what makes ``updated_at >= %s`` in
SQL a correct time comparison on PostgreSQL AND on SQLite, with no dialect
branch and no per-row parsing. It would NOT be sound against a writer stamping a
local offset: ``...T09:00:00+05:00`` sorts after ``...T09:00:00+00:00`` while
being four hours EARLIER. So a cursor that is not a UTC stamp is refused
(``unmeasured``) rather than compared, and ``tests/dic/test_collab_poll.py``
pins the writers.

## ``>=``, NOT ``>``, and the client dedupes

The cursor is INCLUSIVE. A strict ``>`` loses any row written in the same
microsecond as the cursor -- and on a board where one call cascades several
writes, that is not hypothetical. Re-delivering a row the client already holds
costs a redraw it suppresses by comparing ``(id, updated_at)``; missing one
costs a reviewer a decision they never learn about. The two are not symmetric,
so the direction is chosen, not defaulted.

## THE CURSOR NEVER ADVANCES ON A READ THAT FAILED

An ``unmeasured`` poll echoes the caller's cursor back UNCHANGED. Advancing it
would skip the window the failed read covered -- permanently, and silently: the
next poll returns "no changes" for a window nobody ever looked at, and the page
reports itself live while it is stale. That is the fabrication this card series
refuses, wearing a poll's clothes.

## FOUR STATES, AND ``baseline`` HAD TO EXIST

  ``baseline``    no cursor was supplied. The caller is ESTABLISHING one and is
                  told so; no delta is returned and none is implied. A page
                  seeds its cursor from its own render (``workspace_context``),
                  so this state is normally reached only by a caller that did
                  not -- but reporting a first poll as ``no_changes`` asserts
                  something about a window that was never defined.
  ``no_changes``  a window WAS defined and read, and nothing moved in it. The
                  measurement.
  ``changes``     rows moved.
  ``unmeasured``  a read failed. NOT a clean bill of health, and never folded
                  into ``no_changes``.

## TWO FEEDS, TWO VERDICTS, NEVER MERGED

A document's change-state moves in two independent ways, and each is read from
the store that records it:

  ``changes``   ``dic_suggestions`` -- a proposal was accepted, rejected,
                superseded or redrafted. Its own ``updated_at``.
  ``sections``  ``dic_edit_history`` -- the APPEND-ONLY content log both content
                writers already write (``api_section_update_content``, and the
                splice in ``api_suggestion_accept``, each through
                ``history_recorder.record_edit``). Its own ``edited_at``.

``dic_sections`` is deliberately NOT the second feed: it carries no
``updated_at`` column at all, and both writers stamp ``created_at`` when they
change content -- a column whose name says the row was CREATED then. Polling on
it would work today and would read, to anyone maintaining it, as a creation
feed. The append-only log says what it means.

The residual is named rather than implied: a section UPDATE that changes STATUS
and not content (approve, reject, revise) writes no history row and is not
reported here, because ``record_edit`` is a no-op when before == after. This
feed answers "did the text move", which is the question a stale rail needs
answered.

Each feed carries its OWN state, so an unreadable edit log cannot report itself
as a document whose text has not moved.

## WHAT THIS MODULE DOES NOT DO

It writes nothing, decides nothing, applies nothing, and it does not heartbeat.
Presence rows are READ (``presence_registry.get_present_users``, imported, never
a private SELECT) and the client keeps beating on the presence route that
already exists. A poll that also wrote could not be retried, cached or reasoned
about, and a reviewer's presence would then depend on a feed whose failure mode
is a backoff.

THE REFUSAL OF A STALE DECISION IS NOT HERE EITHER, and that separation is the
card's load-bearing one. It belongs at the door that writes --
``suggestion_store.decide_suggestion``, whose UPDATE is conditional on the row
still being ``pending`` -- because a poll can only ever make a stale decision
LESS LIKELY, never impossible. Two reviewers can always click inside one poll
interval. This feed shortens the window; the conditional write closes it.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: The whole-poll verdict. ``unmeasured`` is never folded into ``no_changes``.
STATE_BASELINE = "baseline"
STATE_NO_CHANGES = "no_changes"
STATE_CHANGES = "changes"
STATE_UNMEASURED = "unmeasured"

#: A per-feed verdict. The same question asked of one store.
FEED_OK = "ok"
FEED_UNMEASURED = "unmeasured"

#: Presence. ``alone`` is MEASURED -- the rows were read and nobody else is
#: there. ``unmeasured`` is a read that failed. A page drawing the second as the
#: first tells a reviewer they are the only one deciding.
PRESENCE_OTHERS = "others"
PRESENCE_ALONE = "alone"
PRESENCE_UNMEASURED = "unmeasured"

#: A status a proposal can no longer be decided from. Mirrors the doors: both
#: accept and reject refuse anything that is not ``pending``.
SETTLED_STATUSES = ("accepted", "rejected", "superseded")

#: Bounds. A hit bound is REPORTED and the cursor is set to the LAST DELIVERED
#: row, never to now -- see ``_cursor_after``. A truncated page therefore drains
#: over successive polls rather than dropping rows.
#:
#: THE ONE WAY THAT DRAIN CAN STALL, named rather than left to be discovered:
#: the cursor advances only if the delivered page ends on a NEWER stamp than it
#: started with, so ``MAX_DELTAS`` rows sharing one microsecond would re-deliver
#: the same page for ever. It needs 200 writes inside a single microsecond
#: against one document, which no writer on this canvas can produce -- each
#: decision is its own transaction with its own ``datetime.now`` -- so it is
#: accepted rather than defended against with a compound (stamp, id) cursor that
#: would cost every poll a second sort key for a case nothing can reach. Revisit
#: it if a bulk decider ever lands.
MAX_DELTAS = 200
MAX_SECTIONS = 200

#: A cursor must be an ISO-8601 stamp in UTC: the only shape the lexicographic
#: SQL comparison is sound for (see the module docstring).
_UTC_CURSOR = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?\+00:00$")


def now_cursor() -> str:
    """A cursor meaning "from this instant on", in the writers' own format."""
    return datetime.now(timezone.utc).isoformat()


def is_valid_cursor(cursor: str) -> bool:
    """True when *cursor* is a UTC ISO-8601 stamp this module may compare as text."""
    return bool(_UTC_CURSOR.match(cursor or ""))


# -- The poll -----------------------------------------------------------------

def poll_document(doc_id: str, cursor: str = "", *, me: str = "") -> dict:
    """Change-state deltas for ONE document since *cursor*, plus who else is here.

    Never raises. Every failure is a state, and no failure advances the cursor.
    """
    if not doc_id:
        return _unmeasured(cursor, "no_doc_id")

    presence = others_present(doc_id, me)

    if not cursor:
        # Establishing a cursor. Stamped BEFORE any read, so a write committing
        # during this call lands at or after it and is delivered by the next
        # poll rather than falling into the gap between the two.
        return {
            "state": STATE_BASELINE,
            "doc_id": doc_id,
            "cursor": now_cursor(),
            "reason": "no_cursor",
            "changes": [], "change_count": 0, "changes_state": FEED_OK,
            "sections": [], "section_count": 0, "sections_state": FEED_OK,
            "truncated": False, "deferred": 0,
            "presence": presence,
        }
    if not is_valid_cursor(cursor):
        return _unmeasured(cursor, "cursor_not_utc_iso8601", presence=presence)

    # Stamped before the reads, for the same reason as the baseline stamp.
    fresh = now_cursor()

    from tools.db.storage import get_connection

    changes: list[dict] = []
    sections: list[dict] = []
    changes_state = FEED_UNMEASURED
    sections_state = FEED_UNMEASURED
    truncated = False
    try:
        with get_connection() as conn:
            try:
                changes = _change_deltas(conn, doc_id, cursor)
                changes_state = FEED_OK
                if len(changes) > MAX_DELTAS:
                    changes, truncated = changes[:MAX_DELTAS], True
                if changes:
                    _attach_decisions(conn, changes)
            except Exception as exc:  # noqa: BLE001 -- one feed's failure is its own
                logger.warning("collab.poll_document: change feed unreadable for %s: %s",
                               doc_id, exc)
            try:
                sections = _section_deltas(conn, doc_id, cursor)[:MAX_SECTIONS]
                sections_state = FEED_OK
            except Exception as exc:  # noqa: BLE001
                logger.warning("collab.poll_document: section feed unreadable for %s: %s",
                               doc_id, exc)
    except Exception as exc:  # noqa: BLE001 -- no connection at all: BOTH unmeasured
        logger.warning("collab.poll_document: unreadable for %s: %s", doc_id, exc)
        return _unmeasured(cursor, "read_failed", presence=presence)

    if changes_state == FEED_UNMEASURED and sections_state == FEED_UNMEASURED:
        return _unmeasured(cursor, "both_feeds_unreadable", presence=presence)

    complete = changes_state == FEED_OK and sections_state == FEED_OK
    return {
        # A window in which ONE feed could not be read is not a window in which
        # nothing happened, whatever the other feed returned.
        "state": (STATE_CHANGES if (changes or sections)
                  else (STATE_NO_CHANGES if complete else STATE_UNMEASURED)),
        "doc_id": doc_id,
        "cursor": _cursor_after(cursor, fresh, changes, sections,
                                truncated=truncated, complete=complete),
        "reason": None,
        "changes": changes,
        "change_count": len(changes) if changes_state == FEED_OK else None,
        "changes_state": changes_state,
        "sections": sections,
        "section_count": len(sections) if sections_state == FEED_OK else None,
        "sections_state": sections_state,
        "truncated": truncated,
        # How many were held back is NOT known -- the query fetches one past the
        # bound and stops. ``None`` says "more, count unknown"; a 0 here would
        # read as "nothing deferred" on the one poll where something was.
        "deferred": None if truncated else 0,
        "presence": presence,
    }


def _unmeasured(cursor: str, reason: str, presence: dict | None = None) -> dict:
    """A poll that could not measure. THE CURSOR IS ECHOED BACK UNCHANGED.

    Counts are ``None``, never ``0``: "we could not look" and "nothing moved"
    justify opposite actions by the reader.
    """
    return {
        "state": STATE_UNMEASURED,
        "doc_id": None,
        "cursor": cursor,
        "reason": reason,
        "changes": [], "change_count": None, "changes_state": FEED_UNMEASURED,
        "sections": [], "section_count": None, "sections_state": FEED_UNMEASURED,
        "truncated": False, "deferred": None,
        "presence": presence or {"state": PRESENCE_UNMEASURED, "others": [],
                                 "count": None, "reason": "not_read"},
    }


def _cursor_after(previous: str, fresh: str, changes: list[dict],
                  sections: list[dict], *, truncated: bool, complete: bool) -> str:
    """Where the next poll should start.

    On a TRUNCATED or PARTIAL read the cursor is the NEWEST ROW ACTUALLY
    DELIVERED, never ``fresh``: advancing past rows this call did not return
    drops them for good. With the inclusive ``>=`` comparison that row is
    re-delivered once, which the client suppresses -- the cheap direction.

    It never goes BACKWARDS. A clock that stepped back, or a stored stamp newer
    than the wall clock, would otherwise re-deliver the same window forever.
    """
    if truncated or not complete:
        stamps = [c.get("updated_at") for c in changes if c.get("updated_at")]
        stamps += [s.get("edited_at") for s in sections if s.get("edited_at")]
        if stamps:
            return max(max(stamps), previous)
        return previous
    return max(fresh, previous)


# -- Feed 1: the proposals ----------------------------------------------------

def _change_deltas(conn, doc_id: str, cursor: str) -> list[dict]:
    """``dic_suggestions`` rows for *doc_id* touched at or after *cursor*.

    ``MAX_DELTAS + 1`` rows are taken so the caller can tell a full page from a
    truncated one without a second COUNT.
    """
    rows = _rows(
        conn,
        "SELECT suggestion_id, doc_id, section_id, anchor_section_id, anchor_basis, "
        "       status, updated_at, created_at, applied_by, successor_suggestion_id "
        "FROM dic_suggestions "
        "WHERE doc_id = %s AND updated_at >= %s "
        "ORDER BY updated_at ASC",
        (doc_id, cursor),
    )[: MAX_DELTAS + 1]
    for r in rows:
        r["settled"] = (r.get("status") or "") in SETTLED_STATUSES
        # The same rule ``suggestion_store.section_of_record`` states: the
        # anchor's section outranks the legacy column. Restated rather than
        # imported only because this is a projection of a row and not a
        # decision about one; pinned against that function by test.
        r["section_of_record"] = r.get("anchor_section_id") or r.get("section_id") or None
    return rows


def _attach_decisions(conn, changes: list[dict]) -> None:
    """Attach the NEWEST decision row to each settled change, in place.

    This is what makes a refusal legible. "Already decided" names nobody;
    "accepted by alice at 21:59" tells the second reviewer whose decision stands
    and lets them go and argue with a person. A settled change with no decision
    row keeps ``decision: None`` -- absent, never invented, which is the honest
    reading for a status written by a mechanism (``superseded``) rather than a
    human.
    """
    ids = [c["suggestion_id"] for c in changes if c.get("settled") and c.get("suggestion_id")]
    if not ids:
        return
    placeholders = ",".join(["%s"] * len(ids))
    rows = _rows(
        conn,
        "SELECT suggestion_id, decision, decided_by, decided_at, note "
        f"FROM dic_suggestion_decisions WHERE suggestion_id IN ({placeholders}) "  # nosec B608 -- placeholders are generated %s, ids are bound
        "ORDER BY decided_at ASC",
        tuple(ids),
    )
    newest: dict[str, dict] = {}
    for row in rows:  # ASC, so the last row per id wins
        newest[row.get("suggestion_id")] = row
    for c in changes:
        c["decision"] = newest.get(c.get("suggestion_id"))


# -- Feed 2: the document body ------------------------------------------------

def _section_deltas(conn, doc_id: str, cursor: str) -> list[dict]:
    """Sections of *doc_id* whose CONTENT moved at or after *cursor*.

    From ``dic_edit_history``, the append-only log both content writers already
    write. One row per section -- the newest edit -- because a client re-reads a
    section whole and does not care that it moved three times.

    The section set is scoped through ``dic_sections`` rather than through the
    history row's own ``doc_id``, which is supplied by the caller and is not
    always written: a history row naming another document's section must not
    make this document's rail re-read it.

    Only ``MAX()`` is selected beside the grouping key. A non-aggregated column
    beside a ``GROUP BY`` is another row's value on SQLite and an error on
    PostgreSQL, so "who edited it" is deliberately absent here -- the client
    re-reads the section, which carries its own provenance.
    """
    rows = _rows(
        conn,
        "SELECT h.section_id AS section_id, MAX(h.edited_at) AS edited_at, "
        "       COUNT(*) AS edits "
        "FROM dic_edit_history h "
        "JOIN dic_sections s ON s.section_id = h.section_id "
        "WHERE s.doc_id = %s AND h.edited_at >= %s "
        "GROUP BY h.section_id "
        "ORDER BY MAX(h.edited_at) ASC",
        (doc_id, cursor),
    )
    return [{"section_id": r.get("section_id"),
             "edited_at": r.get("edited_at"),
             "edits": r.get("edits")} for r in rows]


# -- Presence -----------------------------------------------------------------

def others_present(doc_id: str, me: str = "") -> dict:
    """Who ELSE is in this document, from the presence rows.

    ``presence_registry.get_present_users`` is IMPORTED. It owns the TTL, the
    expiry purge and the shape of a session row, and a second SELECT here would
    be a second opinion about who counts as present.

    ``alone`` is a MEASURED verdict; ``unmeasured`` is a failed read.

    PRESENCE IS PER PERSON, NOT PER SESSION, and that has a consequence worth
    knowing before reading the strip. ``join_document`` renews an existing row
    for a repeated ``(doc_id, user_id)`` pair rather than adding one, so one
    reviewer with three tabs open is ONE presence row -- which is right, because
    "3 other reviewers are here" for one person at one desk is a fabrication in
    the direction that stops somebody deciding.

    The cost is real and is named rather than left to be discovered: on a
    deployment with DEV AUTO-LOGIN every browser session is the same user, so
    two people at two machines both read "Only you are in this document". That
    is a true statement about the identities the deployment can distinguish and
    a misleading one about the room. Measured while building the E2E for this
    card: two independent browser contexts against a dev-auto-login dashboard
    produced ONE presence row. The repair is real per-user auth, not a
    session-keyed presence row.

    None of this weakens the write side: the refusal that stops a lost write is
    ``decide_outcome``'s conditional UPDATE, which knows nothing about presence
    and holds whether or not the strip could name anybody.
    """
    if not doc_id:
        return {"state": PRESENCE_UNMEASURED, "others": [], "count": None,
                "reason": "no_doc_id"}
    try:
        from tools.document_intelligence.presence_registry import get_present_users
        users = get_present_users(doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("collab.others_present: presence unreadable for %s: %s", doc_id, exc)
        return {"state": PRESENCE_UNMEASURED, "others": [], "count": None,
                "reason": "read_failed"}
    others = [
        {"user_id": u.get("user_id"), "last_seen": u.get("last_seen"),
         "joined_at": u.get("joined_at")}
        for u in users
        if (u.get("user_id") or "") != (me or "")
    ]
    return {
        "state": PRESENCE_OTHERS if others else PRESENCE_ALONE,
        "others": others,
        "count": len(others),
        "reason": None,
    }


# -- Rows ---------------------------------------------------------------------

def _rows(conn, sql: str, params: tuple) -> list[dict]:
    """``fetchall`` as a list of dicts, whatever the driver's row type is."""
    cur = conn.execute(sql, params)
    fetched = cur.fetchall()
    out: list[dict] = []
    for raw in fetched:
        if isinstance(raw, dict):
            out.append(dict(raw))
            continue
        try:
            out.append(dict(raw))
        except (TypeError, ValueError):
            cols = [d[0] for d in cur.description]
            out.append(dict(zip(cols, raw)))
    return out
