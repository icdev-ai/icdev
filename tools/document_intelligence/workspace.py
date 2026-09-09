# CUI // SP-CTI
r"""The two-pane workspace — the document beside its proposals (dwr-ws-02).

dwr-ws-01 made a proposal DIFFABLE (``word_diff``) and ASSEMBLED
(``change_set.build_change_set``, served at ``GET /api/change-set``) and then
nothing rendered it: the endpoint had no consumer, which is the
declared-but-unconsumed defect this platform ships most. This module is the
server half of the page that consumes it, and it does ONE job the change set
cannot do for itself — produce THE DOCUMENT the proposals are about.

## The left pane is three rendering modes, and which one you get is MEASURED

The three are not stylistic alternatives. Each is what the evidence on this
document supports, and the difference between them is whether a character
OFFSET means anything:

``positioned``  ``dic_sections`` rows rendered VERBATIM in a pre-wrap layer, so
                that ``content[start:end]`` on screen is the same span
                ``suggestion_store.verify_anchor`` checks and the accept door
                splices. An anchor can be marked EXACTLY. This is the default
                whenever sections exist, because it is the only mode in which a
                mark is truthful.
``reflow``      the same sections through ``safeMarkdown``. Offsets index the
                RENDERED HTML and not the content of record — the trap
                ``annotation_store.anchor_from_selection`` was written for — so
                marks are HIDDEN in this mode and the page says why. It is a
                reading view, not a review view.
``chunks``      NO ``dic_sections`` row exists for this document and its text
                survives only as ``rag_chunks``. A chunk is retrieval evidence,
                not the content of record: ``api_suggestion_accept`` writes
                ``dic_sections.content`` and nothing else, so a proposal can
                never be applied against a chunk. Rendered read-only and
                labelled NOT ADDRESSABLE.

MEASURED on the live PG board 2026-09-08, and this is why the third mode had to
exist rather than being a fallback nobody would reach:
``dic_doc_28e2ee4d984f3f35`` carries 47 of the board's 58 pending proposals and
ZERO ``dic_sections`` rows, while holding 2 ``rag_chunks``. A workspace that
rendered only sections would show an EMPTY document beside 47 proposals — the
most misleading screen this feature could produce.

## ``no_body`` and ``unmeasured`` are different answers and are never merged

``basis`` is ``positioned`` | ``chunks`` | ``no_body`` | ``unmeasured``.
``no_body`` means both stores were READ and neither holds anything for this
document. ``unmeasured`` means a read failed. Counts are ``None``, never ``0``,
whenever nothing was measured: an unreadable database reporting "0 sections"
reads as an empty document, which is the fabrication this card series exists to
refuse.

## Document-level findings, and why a span-only page lies

``placeholder_guard`` and ``citation_guard`` report at DOCUMENT level — they
anchor to no single claim. ``delta_review/page.html`` states the consequence and
this page reproduces it: a workspace showing only span-anchored findings renders
a BLOCKED draft as having nothing wrong with it. So the left pane carries a
document-level band fed by ``consistency_checker.check_version_consistency`` and
``check_version_citations`` — the same two functions the approve and export
gates run, imported and never re-derived.

Both take a ``version_id``. A document with no version has NOT passed those
gates; it has never been put to them, so each reports ``unmeasured``.
``clean`` is only ever written after a gate actually ran.

A THIRD document-level finding is structural and is this page's own: a proposal
that names no section at all. On this board that is 58 of 58, and it is exactly
what a span-anchored rail cannot draw.

Reads. Writes nothing, decides nothing, applies nothing — every act on this page
goes through a door that already existed (``/api/suggestions/<id>/accept``,
``/api/suggestions/<id>/reject``, ``/api/sections/<id>/annotations``).
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── Left-pane bases. Which one you get is measured, never chosen. ─────────────
BASIS_POSITIONED = "positioned"
BASIS_CHUNKS = "chunks"
BASIS_NONE = "no_body"
BASIS_UNMEASURED = "unmeasured"

#: Rendering modes offered for a ``positioned`` body. ``reflow`` is the SAME
#: rows through markdown; it is a reading view and it cannot carry a mark.
MODE_POSITIONED = "positioned"
MODE_REFLOW = "reflow"

#: A gate's three states. ``unmeasured`` is never folded into ``clean``.
GATE_CLEAN = "clean"
GATE_FINDINGS = "findings"
GATE_UNMEASURED = "unmeasured"

#: Versions a reader is considered to be "in". Mirrors the blueprint's
#: ``_OPEN_VERSION_STATUSES``; pinned against it by test.
OPEN_VERSION_STATUSES = ("draft", "pending_review", "needs_revision")

_MAX_SECTIONS = 400
_MAX_CHUNKS = 200


# ── The document body ─────────────────────────────────────────────────────────

def active_version(versions: list[dict]) -> str:
    """The version a reader of this document is looking at.

    Same rule as ``blueprint._active_version_id`` — newest OPEN version, else
    the newest of any status, else ``""``. Restated here rather than imported
    because importing the blueprint from a module the blueprint imports is a
    cycle; the rule is four lines and a test pins it against the blueprint's.

    ``versions`` must be ordered newest-first, as every caller reads it.
    """
    for v in versions or []:
        if (v.get("status") or "") in OPEN_VERSION_STATUSES:
            return v.get("version_id") or ""
    if versions:
        return versions[0].get("version_id") or ""
    return ""


def document_body(doc_id: str) -> dict:
    """The left pane's content for one document.

    Never raises. A read failure is ``basis: unmeasured`` with ``None`` counts —
    "we could not look" — and is never reported as an empty document.
    """
    if not doc_id:
        return _empty_body(BASIS_UNMEASURED, "no_doc_id")

    from tools.db.storage import get_connection

    versions: list[dict] = []
    version_id = ""
    try:
        with get_connection() as conn:
            versions = _rows(
                conn,
                "SELECT version_id, version_no, origin, status, created_at "
                "FROM dic_versions WHERE doc_id = %s ORDER BY version_no DESC",
                (doc_id,),
            )
            version_id = active_version(versions)
            sections: list[dict] = []
            if version_id:
                sections = _rows(
                    conn,
                    "SELECT section_id, heading, content, status, origin, created_at "
                    "FROM dic_sections WHERE version_id = %s ORDER BY section_id",
                    (version_id,),
                )[:_MAX_SECTIONS]
            if sections:
                return {
                    "basis": BASIS_POSITIONED,
                    "reason": None,
                    "version_id": version_id,
                    "version_count": len(versions),
                    "sections": sections,
                    "chunks": [],
                    "section_count": len(sections),
                    "chunk_count": None,
                    "addressable": True,
                    "modes": [MODE_POSITIONED, MODE_REFLOW],
                }
            chunks = _rows(
                conn,
                "SELECT id, content, chunk_index, total_chunks, created_at "
                "FROM rag_chunks WHERE source_id = %s ORDER BY chunk_index",
                (doc_id,),
            )[:_MAX_CHUNKS]
    except Exception as exc:  # noqa: BLE001 — an unreadable store is `unmeasured`
        logger.warning("workspace.document_body: unreadable for %s: %s", doc_id, exc)
        return _empty_body(BASIS_UNMEASURED, "read_failed")

    if chunks:
        return {
            "basis": BASIS_CHUNKS,
            # The reason a reader needs is not "no text" but "no text a
            # proposal could ever be applied to".
            "reason": "no_sections",
            "version_id": version_id,
            "version_count": len(versions),
            "sections": [],
            "chunks": chunks,
            "section_count": 0,
            "chunk_count": len(chunks),
            "addressable": False,
            "modes": [],
        }
    return {
        "basis": BASIS_NONE,
        "reason": "no_sections_no_chunks",
        "version_id": version_id,
        "version_count": len(versions),
        "sections": [],
        "chunks": [],
        "section_count": 0,
        "chunk_count": 0,
        "addressable": False,
        "modes": [],
    }


def _empty_body(basis: str, reason: str) -> dict:
    return {"basis": basis, "reason": reason, "version_id": "", "version_count": None,
            "sections": [], "chunks": [], "section_count": None, "chunk_count": None,
            "addressable": False, "modes": []}


# ── Document-level findings ───────────────────────────────────────────────────

def document_findings(version_id: str) -> dict:
    """The DOCUMENT-level publish gates, each with its own state.

    ``placeholder_guard`` and ``citation_guard`` anchor to no claim, so a page
    rendering only span-anchored findings shows a blocked draft as clean
    (``delta_review/page.html``, the precedent this page is modelled on).

    Each gate is ``clean`` | ``findings`` | ``unmeasured``, and a document with
    NO version is ``unmeasured`` for all of them — it has not passed these
    gates, it has never been put to them.

    A GATE OVER AN EMPTY DENOMINATOR IS ``unmeasured``, NEVER ``clean``. Both
    checkers return no findings when they were handed no sections, and the
    first live run of this module reported ``placeholder: clean`` and
    ``citation: clean`` for ``dic_doc_28e2ee4d984f3f35`` — a document with a
    version row, ZERO sections and 47 pending proposals. That is the
    ``args/perfect_score_gate.yaml`` defect wearing a gate's name: nothing was
    scanned, and the band would have drawn two green ticks. ``section_count``
    guards the placeholder and numeric gates; ``ai_section_count`` guards the
    citation gate, which by contract only ever inspects AI-authored sections,
    so a document with none of them has not been checked for citations — it has
    had no citation to check.
    """
    if not version_id:
        return {
            "placeholder": _gate(GATE_UNMEASURED, [], "no_version"),
            "citation": _gate(GATE_UNMEASURED, [], "no_version"),
            "numeric": _gate(GATE_UNMEASURED, [], "no_version"),
        }

    placeholder = _gate(GATE_UNMEASURED, [], "read_failed")
    numeric = _gate(GATE_UNMEASURED, [], "read_failed")
    citation = _gate(GATE_UNMEASURED, [], "read_failed")

    try:
        from tools.document_intelligence.consistency_checker import check_version_consistency
        report = check_version_consistency(version_id)
        if report.get("error"):
            placeholder = _gate(GATE_UNMEASURED, [], "gate_error")
            numeric = _gate(GATE_UNMEASURED, [], "gate_error")
        elif not report.get("section_count"):
            placeholder = _gate(GATE_UNMEASURED, [], "no_sections")
            numeric = _gate(GATE_UNMEASURED, [], "no_sections")
        else:
            hits = report.get("placeholders") or []
            placeholder = _gate(GATE_FINDINGS if hits else GATE_CLEAN, hits, None)
            nhits = report.get("numeric_conflicts") or []
            numeric = _gate(GATE_FINDINGS if nhits else GATE_CLEAN, nhits, None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace.document_findings: consistency gate failed: %s", exc)

    try:
        from tools.document_intelligence.consistency_checker import check_version_citations
        report = check_version_citations(version_id)
        if report.get("error"):
            citation = _gate(GATE_UNMEASURED, [], "gate_error")
        elif not report.get("ai_section_count"):
            citation = _gate(
                GATE_UNMEASURED, [],
                "no_sections" if not report.get("section_count") else "no_ai_sections")
        else:
            hits = report.get("findings") or []
            citation = _gate(GATE_FINDINGS if hits else GATE_CLEAN, hits, None)
        citation["ai_section_count"] = report.get("ai_section_count")
        citation["section_count"] = report.get("section_count")
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace.document_findings: citation gate failed: %s", exc)

    return {"placeholder": placeholder, "citation": citation, "numeric": numeric}


def _gate(state: str, findings: list, reason: str | None) -> dict:
    """One gate's verdict. ``count`` is ``None``, never ``0``, when unmeasured."""
    return {
        "state": state,
        "findings": list(findings) if state == GATE_FINDINGS else [],
        "count": len(findings) if state != GATE_UNMEASURED else None,
        "reason": reason,
    }


def unplaced_summary(changes: list[dict] | None, *, read_ok: bool = True) -> dict:
    """How many proposals name NO section — this page's own document-level finding.

    A proposal with no section is not a span finding and a span-anchored rail
    cannot draw it. On the live board 2026-09-08 that is 58 of 58, so folding it
    into "nothing to review" would empty the page. ``count`` is ``None`` when
    the change set could not be read.
    """
    if not read_ok or changes is None:
        return {"state": GATE_UNMEASURED, "count": None, "total": None,
                "reason": "unmeasured", "suggestion_ids": []}
    unplaced = [c for c in changes
                if not (c.get("anchor_section_id") or c.get("section_id"))]
    return {
        "state": GATE_FINDINGS if unplaced else GATE_CLEAN,
        "count": len(unplaced),
        "total": len(changes),
        "reason": None,
        "suggestion_ids": [c.get("suggestion_id") for c in unplaced][:50],
    }


# ── The page ──────────────────────────────────────────────────────────────────

def workspace_context(doc_id: str) -> dict:
    """Everything the workspace template renders server-side.

    The CHANGE SET is deliberately NOT assembled here: the page fetches
    ``GET /api/change-set?doc_id=...`` — dwr-ws-01's endpoint — so there is one
    assembler and the page cannot drift from what the API says. What is
    assembled here is the half the change set has no opinion about: the document
    body, the version, and the document-level gates.
    """
    # dwr-collab-01 -- THE POLL CURSOR IS STAMPED BEFORE THE PAGE IS READ, not
    # by the page's first poll. A cursor taken when the client's first poll
    # arrives leaves a window -- render, ship HTML, parse, fetch -- in which
    # another reviewer's decision lands and is never delivered, and the second
    # reviewer's rail is stale from the moment it draws. Stamped here, that
    # window is closed: everything this render saw is at or before the cursor,
    # and everything after it is a delta. The inclusive `>=` comparison means a
    # decision landing in the same microsecond is re-delivered rather than lost.
    from tools.document_intelligence.collab import now_cursor
    cursor = now_cursor()
    body = document_body(doc_id)
    findings = document_findings(body.get("version_id") or "")
    return {
        "doc_id": doc_id,
        "poll_cursor": cursor,
        "body": body,
        "document_findings": findings,
        "any_document_findings": any(
            g.get("state") == GATE_FINDINGS for g in findings.values()),
        "any_unmeasured_gate": any(
            g.get("state") == GATE_UNMEASURED for g in findings.values()),
    }


def workspace_candidates(limit: int = 100) -> dict:
    """Documents worth opening a workspace on: those carrying PENDING proposals.

    Backs the picker at ``/document-intelligence/workspace``. ``state`` is
    ``documents`` | ``none`` | ``unmeasured``; ``none`` means the store was read
    and no document has a pending proposal, and it is never what an unreadable
    store returns.
    """
    from tools.db.storage import get_connection

    try:
        with get_connection() as conn:
            rows = _rows(
                conn,
                "SELECT doc_id, COUNT(*) AS pending, MAX(created_at) AS newest "
                "FROM dic_suggestions WHERE status = 'pending' "
                "AND COALESCE(doc_id, '') <> '' "
                "GROUP BY doc_id ORDER BY COUNT(*) DESC",
                (),
            )[:max(limit, 0)]
            titles = _titles_for(conn, [r.get("doc_id") for r in rows])
    except Exception as exc:  # noqa: BLE001
        logger.warning("workspace.workspace_candidates: unreadable: %s", exc)
        return {"state": "unmeasured", "documents": [], "count": None}

    docs = []
    for row in rows:
        did = row.get("doc_id")
        docs.append({
            "doc_id": did,
            # A document id that resolves to no row renders AS THE ID. Nothing
            # here invents a title for a document the catalogue has lost.
            "title": titles.get(did),
            "pending": row.get("pending"),
            "newest": row.get("newest"),
        })
    return {"state": "documents" if docs else "none", "documents": docs, "count": len(docs)}


def _titles_for(conn, doc_ids: list) -> dict:
    ids = [d for d in doc_ids if d]
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    try:
        rows = _rows(
            conn,
            f"SELECT doc_id, title FROM dic_documents WHERE doc_id IN ({placeholders})",
            tuple(ids),
        )
    except Exception:  # noqa: BLE001 — a missing title renders as the id
        return {}
    return {r.get("doc_id"): r.get("title") for r in rows}


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
