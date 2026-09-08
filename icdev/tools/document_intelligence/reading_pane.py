# CUI // SP-CTI
"""What can this document ACTUALLY render, and why not more (dwr-fid-03).

THE DEFECT. ``doc_detail`` renders ``dic_sections``. A document with no section
row rendered one line — *"No sections yet. Generate a draft or instantiate a
template."* — for a document whose full text was sitting in ``rag_chunks`` the
whole time, and a document whose text is GONE rendered exactly the same line.
So the page could not tell "nothing has been drafted yet" from "the text is
here and this page will not show it" from "the text no longer exists anywhere",
and a thin render was indistinguishable from a complete one.

MEASURED on the live PG board 2026-09-08, and the numbers are why this module
does not have two states:

    55 documents
    16  carry a dic_sections row            -> the FULL render, untouched
    39  carry none, and they are NOT one population:
        19  text recoverable from rag_chunks -> the degraded reading pane
         9  chunk LINKS exist and EVERY ONE dangles: the rag_chunks rows the
            links name are gone (a reindex, a retention sweep, delete_source).
            The document was chunked and the text has since been dropped.
        11  no links and no chunks at all. It was never chunked.

Serving the first 19 and printing "no text" for the other 20 would be the same
conflation one layer down: `chunks_missing` and `no_text` send a reader to
different fixes — re-ingest from the original vs. the text never existed here —
and neither is "this document is short".

NINETEEN OF THE NINETEEN ARE RECOVERED BY SOURCE ID, NOT BY LINK. ``dic_chunk_links``
is the document's own record of its chunks and is the FIRST derivation asked;
on this board it resolves for TWO documents. ``rag_chunks`` also carries
``source_type='dic_document'`` / ``source_id=<doc_id>`` written by the same
ingest, and that is the SECOND. Which one answered is RECORDED as ``text_source``
and rendered — a pane that silently fell back would hide that the link table has
gone stale, which is a real finding about the ingest and not a rendering detail.

WORKING ANCHORS, AND THE ONE THIS PANE CANNOT GIVE YOU. Every rendered chunk
carries a stable ``anchor_id`` derived from the rag chunk id, so ``#chunk-<id>``
is deep-linkable, highlightable and survives a re-render. That is the pane's own
coordinate space. It is NOT the review coordinate space: ``suggestion_store`` and
``annotation_store`` anchor offsets into ``dic_sections.content``, and a document
with no section has no such row for an anchor to index. THE PANE SAYS THAT, in
words, rather than rendering a comment box that would drop what a reviewer typed.

THE PANE STATES ITS OWN LIMITS. ``limits`` is a list of typed findings, each
with the fix, and it is derived — never a fixed sentence. The two the card names
are separate entries because they are separate absences with separate costs:

    original_*   dwr-fid-01's verdict, IMPORTED from ``originals``. Never
                 re-derived here: two spellings of "is the upload still there"
                 is how one surface starts disagreeing with another.
    no_sections  there is no editing/anchoring coordinate space.

AN UNREADABLE DATABASE IS ``unmeasurable``, NEVER ``no_text``. ``_safe_rows`` in
``blueprint.py`` turns a SELECT naming a missing column into an EMPTY LIST, which
is exactly how a broken query reads as a document with no chunks. Every read here
reports its own failure instead, and a basis of ``unmeasurable`` is not a clean
bill of health.

A library and a CLI::

    python -m tools.document_intelligence.reading_pane --survey [--json]
    python -m tools.document_intelligence.reading_pane --doc <doc_id> [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: The document has ``dic_sections`` rows. The full render owns it; this module
#: declines and renders nothing.
BASIS_SECTIONS = "sections"
#: No sections, and the text was recovered from ``rag_chunks``. The degraded pane.
BASIS_CHUNKS = "chunks"
#: No sections, chunk LINKS exist, and not one of them resolves to a live
#: ``rag_chunks`` row. The document WAS chunked; the chunks are gone. THE FINDING.
BASIS_CHUNKS_MISSING = "chunks_missing"
#: No sections, no links, no chunks. Nothing ever chunked this document.
BASIS_NO_TEXT = "no_text"
#: A read failed. NOT a clean bill of health, and never folded into the others.
BASIS_UNMEASURABLE = "unmeasurable"

#: Every basis this module can return.
RENDER_BASES: tuple[str, ...] = (
    BASIS_SECTIONS,
    BASIS_CHUNKS,
    BASIS_CHUNKS_MISSING,
    BASIS_NO_TEXT,
    BASIS_UNMEASURABLE,
)

#: The bases that render prose in the degraded pane.
RENDERING_BASES: frozenset[str] = frozenset({BASIS_CHUNKS})

#: How the chunk text was obtained. RECORDED, never inferred at read time.
TEXT_SOURCE_LINKS = "chunk_links"      # the document's own link rows resolved
TEXT_SOURCE_RAG_SOURCE_ID = "rag_source_id"  # recovered by source id; links stale/absent
TEXT_SOURCES: tuple[str, ...] = (TEXT_SOURCE_LINKS, TEXT_SOURCE_RAG_SOURCE_ID)

#: ``rag_chunks`` rows written by DIC ingest carry these. Both are asked for, so
#: a row of another kind that happens to share an id can never be served as this
#: document's prose.
RAG_SOURCE_TYPE = "dic_document"
RAG_SOURCE_TABLE = "dic_documents"

#: A cap on how much prose one pane renders. A 78-chunk document is ~65k chars,
#: which is fine; a pathological one is not, and a truncated pane that does not
#: SAY it truncated is the defect this module exists for. Deferred chunks are
#: COUNTED and reported as ``chunks_truncated``.
MAX_PANE_CHUNKS = 400


@dataclass
class ReadingChunk:
    """One renderable unit of the degraded pane."""

    anchor_id: str
    """Stable, deep-linkable id for this chunk. ``#<anchor_id>`` in the page."""
    chunk_id: str
    """The ``rag_chunks.id`` this text came from."""
    index: int
    """Order within the document. From the link row when there is one, else the
    ``rag_chunks.chunk_index``."""
    content: str
    page: Optional[int] = None
    section: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Limit:
    """One stated limitation of this render, with the fix.

    ``code`` is machine-readable and stable; ``label`` and ``detail`` are what a
    human reads. ``severity`` orders the band, it does not decide anything.
    """

    code: str
    label: str
    detail: str
    severity: str = "info"  # info | warn | finding

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReadingPane:
    """Everything the page needs to render honestly, and nothing it can infer."""

    doc_id: str
    basis: str
    text_source: Optional[str] = None
    chunks: list[ReadingChunk] = field(default_factory=list)
    limits: list[Limit] = field(default_factory=list)
    section_count: int = 0
    links_total: int = 0
    links_resolved: int = 0
    chunks_truncated: int = 0
    original: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def degraded(self) -> bool:
        """True when this render is NOT the full sections render."""
        return self.basis != BASIS_SECTIONS

    @property
    def renders_text(self) -> bool:
        return self.basis in RENDERING_BASES and bool(self.chunks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "basis": self.basis,
            "text_source": self.text_source,
            "degraded": self.degraded,
            "renders_text": self.renders_text,
            "chunks": [c.to_dict() for c in self.chunks],
            "chunk_count": len(self.chunks),
            "chunks_truncated": self.chunks_truncated,
            "limits": [x.to_dict() for x in self.limits],
            "section_count": self.section_count,
            "links_total": self.links_total,
            "links_resolved": self.links_resolved,
            "original": self.original,
            "errors": self.errors,
        }


# ── primary reads ─────────────────────────────────────────────────────────────
#
# Each returns (value, error). An error is REPORTED, never turned into a zero:
# `_safe_rows` returning [] for a broken query is precisely how "the column is
# missing" reads as "this document has no chunks".

def _rows(conn, sql: str, params: tuple) -> tuple[list[dict], Optional[str]]:
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()], None
    except Exception as exc:  # noqa: BLE001 — reported to the caller, never swallowed
        logger.warning("dic reading_pane: query failed: %s", exc)
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001 — a rollback on a healthy conn is a no-op
            pass
        return [], str(exc)


def section_count(conn, doc_id: str) -> tuple[Optional[int], Optional[str]]:
    """How many sections this document has. ``None`` means UNREADABLE."""
    rows, err = _rows(conn, "SELECT COUNT(*) AS n FROM dic_sections WHERE doc_id = %s", (doc_id,))
    if err:
        return None, err
    return (int(rows[0]["n"]) if rows else 0), None


def linked_chunks(conn, doc_id: str) -> tuple[list[dict], int, Optional[str]]:
    """The document's OWN chunk links, resolved against ``rag_chunks``.

    Returns ``(resolved_rows, links_total, error)``. ``links_total`` counts the
    link rows regardless of whether they resolve — the gap between the two is
    what makes a dangling-link document say so instead of reading as empty.
    """
    total_rows, err = _rows(
        conn, "SELECT COUNT(*) AS n FROM dic_chunk_links WHERE doc_id = %s", (doc_id,)
    )
    if err:
        return [], 0, err
    total = int(total_rows[0]["n"]) if total_rows else 0
    if not total:
        return [], 0, None
    rows, err = _rows(
        conn,
        "SELECT l.rag_chunk_id AS chunk_id, l.chunk_index, l.page, l.section, rc.content "
        "FROM dic_chunk_links l JOIN rag_chunks rc ON rc.id = l.rag_chunk_id "
        "WHERE l.doc_id = %s ORDER BY l.chunk_index",
        (doc_id,),
    )
    if err:
        return [], total, err
    return rows, total, None


def source_chunks(conn, doc_id: str) -> tuple[list[dict], Optional[str]]:
    """``rag_chunks`` written for this document, found by source id.

    The SECOND derivation, and only ever a fallback: the ingest writes both the
    link row and the chunk, so a document whose links do not resolve while its
    chunks do is a document whose LINK TABLE has gone stale. Both
    ``source_type`` and ``source_table`` are required so a row of another kind
    can never be rendered as this document's prose.
    """
    return _rows(
        conn,
        "SELECT id AS chunk_id, chunk_index, content FROM rag_chunks "
        "WHERE source_type = %s AND source_table = %s AND source_id = %s "
        "ORDER BY chunk_index",
        (RAG_SOURCE_TYPE, RAG_SOURCE_TABLE, doc_id),
    )


def _anchor_id(chunk_id: str, index: int) -> str:
    """A stable, URL-safe anchor. The chunk id is already opaque and unique; the
    index is a readable fallback for a row that somehow carries none.

    Live rag ids are already spelled ``chunk-<hex>``, so the prefix is not
    doubled — ``#chunk-chunk-6e63…`` is a URL somebody has to paste.
    """
    cid = "".join(ch for ch in str(chunk_id or "") if ch.isalnum() or ch in "-_")
    if cid.startswith("chunk-"):
        cid = cid[len("chunk-"):]
    return f"chunk-{cid}" if cid else f"chunk-i{index}"


def _to_chunks(rows: list[dict]) -> tuple[list[ReadingChunk], int]:
    """Rows -> renderable chunks, capped. Returns ``(chunks, truncated_count)``."""
    out: list[ReadingChunk] = []
    for i, r in enumerate(rows[:MAX_PANE_CHUNKS]):
        content = r.get("content")
        if content is None:
            continue
        idx = r.get("chunk_index")
        idx = int(idx) if isinstance(idx, int) else i
        cid = str(r.get("chunk_id") or "")
        page = r.get("page")
        out.append(
            ReadingChunk(
                anchor_id=_anchor_id(cid, idx),
                chunk_id=cid,
                index=idx,
                content=str(content),
                page=int(page) if isinstance(page, int) else None,
                section=(str(r["section"]) if r.get("section") else None),
            )
        )
    return out, max(0, len(rows) - MAX_PANE_CHUNKS)


# ── limits ────────────────────────────────────────────────────────────────────

def original_limit(verdict: dict[str, Any], *, columns_present: bool) -> Optional[Limit]:
    """The ORIGINAL half of "which of the two is missing".

    The verdict itself comes from :mod:`tools.document_intelligence.originals`;
    this only decides how to SAY it. A board that has not run dwr-fid-01's
    migration cannot know, and says ``unmeasurable`` rather than ``absent`` —
    reporting 43 documents as having lost their upload when the column simply
    does not exist would be a fabricated finding.
    """
    if not columns_present:
        return Limit(
            code="original_unmeasurable",
            label="Original upload: not measurable on this deployment",
            detail=(
                "dic_documents does not carry the retention columns "
                "(original_path / original_sha256 / original_retained_at), so this "
                "page cannot tell whether the uploaded file was kept. That is not "
                "the same as knowing it was lost. Run `python tools/db/migrate.py --up`."
            ),
            severity="warn",
        )
    status = str(verdict.get("status") or "")
    if status == "retained":
        return None
    if status == "source_on_disk":
        return Limit(
            code="original_source_on_disk",
            label="Original upload: readable, but not retained",
            detail=(
                "The file this document was ingested from still exists at its "
                "original path, so it can be re-read today — but nothing has "
                "copied it into the retention store, so it disappears the moment "
                "that path does."
            ),
            severity="info",
        )
    if status == "no_source":
        return Limit(
            code="original_no_source",
            label="No original: this document was generated in the canvas",
            detail=(
                "There was never an upload to retain — this document was drafted "
                "here rather than ingested from a file. Not a defect."
            ),
            severity="info",
        )
    if status == "retained_missing":
        return Limit(
            code="original_retained_missing",
            label="Original upload: recorded, but the file is gone",
            detail=(
                "A retained original is recorded for this document and the file is "
                "not at that path — the retention root was moved, pruned or is not "
                "mounted here. Page geometry and re-extraction are unavailable "
                "until it is restored."
            ),
            severity="finding",
        )
    if status == "retained_mismatch":
        return Limit(
            code="original_retained_mismatch",
            label="Original upload: on disk, but its hash does not match",
            detail=(
                "The retained file's sha256 is not the one recorded at ingest. The "
                "store has been corrupted or tampered with; do not treat this file "
                "as the document's original."
            ),
            severity="finding",
        )
    if status == "absent":
        return Limit(
            code="original_absent",
            label="No original: the uploaded file is gone",
            detail=(
                "This document was ingested from an upload that was deleted and "
                "never retained, so its extracted text is all that survives. The "
                "extraction cannot be re-run, page geometry cannot be recovered, "
                "and fidelity cannot be checked against the source. Re-upload the "
                "file to restore either."
            ),
            severity="finding",
        )
    return Limit(
        code="original_unknown",
        label=f"Original upload: unrecognised state ({status or 'none'})",
        detail="The retention verdict for this document could not be interpreted.",
        severity="warn",
    )


def _basis_limits(pane: ReadingPane) -> list[Limit]:
    """The SECTIONS half, and what each basis costs the reader."""
    out: list[Limit] = []
    if pane.basis == BASIS_SECTIONS:
        return out

    out.append(
        Limit(
            code="no_sections",
            label="No sections: this is a reading view, not the document workspace",
            detail=(
                "Nothing has derived this document's outline, so there is no "
                "section to edit, assign, review, suggest against or anchor a "
                "comment to. Anchored review is unavailable here — comments and "
                "suggestions index offsets into a section's text, and this "
                "document has none."
            ),
            severity="warn",
        )
    )

    if pane.basis == BASIS_CHUNKS:
        if pane.text_source == TEXT_SOURCE_RAG_SOURCE_ID:
            detail = (
                "The text below is reassembled from the retrieval chunks, found by "
                "source id because this document's own chunk links "
                f"({pane.links_resolved} of {pane.links_total} resolve) could not "
                "supply it. Chunks are sized for embedding and overlap, so a "
                "sentence may repeat at a boundary and the original layout — "
                "pages, columns, tables, figures — is not preserved."
            ) if pane.links_total else (
                "The text below is reassembled from the retrieval chunks, found by "
                "source id: this document has no chunk-link rows at all. Chunks are "
                "sized for embedding and overlap, so a sentence may repeat at a "
                "boundary and the original layout — pages, columns, tables, "
                "figures — is not preserved."
            )
        else:
            detail = (
                "The text below is reassembled from this document's own retrieval "
                "chunks. Chunks are sized for embedding and overlap, so a sentence "
                "may repeat at a boundary and the original layout — pages, columns, "
                "tables, figures — is not preserved."
            )
        out.append(
            Limit(
                code="text_from_chunks",
                label="Text reassembled from retrieval chunks — not the document",
                detail=detail,
                severity="warn",
            )
        )
        if pane.chunks_truncated:
            out.append(
                Limit(
                    code="chunks_truncated",
                    label=f"Showing the first {MAX_PANE_CHUNKS} chunks — "
                          f"{pane.chunks_truncated} more are not rendered",
                    detail=(
                        "This pane caps how much prose it reassembles. The rest of "
                        "the document exists and is searchable; it is not on screen."
                    ),
                    severity="warn",
                )
            )

    elif pane.basis == BASIS_CHUNKS_MISSING:
        out.append(
            Limit(
                code="chunks_missing",
                label="The text is gone: every retrieval chunk this document names is missing",
                detail=(
                    f"{pane.links_total} chunk link(s) are recorded for this "
                    "document and not one resolves to a live rag_chunks row, and no "
                    "chunk carries this document's source id either. It WAS chunked "
                    "and the chunks have since been removed — a reindex, a retention "
                    "sweep or a source deletion. This is not an empty document. "
                    "Re-ingest it from its original to restore the text."
                ),
                severity="finding",
            )
        )
    elif pane.basis == BASIS_NO_TEXT:
        out.append(
            Limit(
                code="no_text",
                label="No text: this document has never been chunked",
                detail=(
                    "There are no chunk links and no retrieval chunks for this "
                    "document, so there is no text anywhere for this page to show. "
                    "Nothing here says the document is empty — only that nothing "
                    "has extracted it."
                ),
                severity="finding",
            )
        )
    elif pane.basis == BASIS_UNMEASURABLE:
        out.append(
            Limit(
                code="unmeasurable",
                label="This page could not measure what it can render",
                detail=(
                    "A read against the sections, chunk-link or retrieval tables "
                    "failed, so the absence of text below is a statement about this "
                    "query and not about the document. This is not a clean bill of "
                    "health."
                ),
                severity="finding",
            )
        )
    return out


# ── the pane ──────────────────────────────────────────────────────────────────

def build_pane(conn, doc_id: str, doc_row: Any = None) -> ReadingPane:
    """Everything ``doc_detail`` needs to render this document honestly.

    ``doc_row`` is the ``dic_documents`` row when the caller already has it (the
    page does); it is re-read otherwise. Only the ORIGINAL verdict uses it, and
    that verdict is :func:`originals.original_verdict`'s — never re-derived.
    """
    pane = ReadingPane(doc_id=doc_id, basis=BASIS_UNMEASURABLE)

    from tools.document_intelligence import originals as _originals

    # ── the original ──────────────────────────────────────────────────────────
    columns_present = False
    try:
        columns_present = _originals.original_columns_present(conn)
    except Exception as exc:  # noqa: BLE001 — an unreadable catalogue is unmeasurable, and said so
        pane.errors.append(f"original_columns: {exc}")
    if doc_row is None:
        extra, columns_present = _originals.select_columns(conn)
        rows, err = _rows(
            conn, f"SELECT doc_id{extra} FROM dic_documents WHERE doc_id = %s", (doc_id,)
        )
        if err:
            pane.errors.append(f"document: {err}")
        doc_row = rows[0] if rows else None
    verdict = _originals.original_verdict(doc_row) if doc_row is not None else {
        "status": "no_source", "path": None, "sha256": None
    }
    pane.original = dict(verdict)
    pane.original["columns_present"] = columns_present

    # ── sections: the full render wins outright ───────────────────────────────
    n_sections, err = section_count(conn, doc_id)
    if err:
        pane.errors.append(f"sections: {err}")
        pane.limits = _basis_limits(pane) + [x for x in (
            original_limit(verdict, columns_present=columns_present),) if x]
        return pane
    pane.section_count = int(n_sections or 0)
    if pane.section_count:
        pane.basis = BASIS_SECTIONS
        lim = original_limit(verdict, columns_present=columns_present)
        pane.limits = ([lim] if lim else [])
        return pane

    # ── the document's own links first ────────────────────────────────────────
    linked, links_total, err = linked_chunks(conn, doc_id)
    if err:
        pane.errors.append(f"chunk_links: {err}")
    pane.links_total = links_total
    pane.links_resolved = len(linked)

    rows: list[dict] = []
    if linked:
        rows = linked
        pane.text_source = TEXT_SOURCE_LINKS
    else:
        # ── recovered by source id ────────────────────────────────────────────
        src, err2 = source_chunks(conn, doc_id)
        if err2:
            pane.errors.append(f"rag_chunks: {err2}")
        if src:
            rows = src
            pane.text_source = TEXT_SOURCE_RAG_SOURCE_ID

    if rows:
        pane.chunks, pane.chunks_truncated = _to_chunks(rows)

    if pane.chunks:
        pane.basis = BASIS_CHUNKS
    elif pane.errors:
        pane.basis = BASIS_UNMEASURABLE
    elif links_total:
        pane.basis = BASIS_CHUNKS_MISSING
    else:
        pane.basis = BASIS_NO_TEXT

    limits = _basis_limits(pane)
    lim = original_limit(verdict, columns_present=columns_present)
    if lim:
        limits.append(lim)
    pane.limits = limits
    return pane


# ── survey ────────────────────────────────────────────────────────────────────

def survey(conn=None, *, limit_examples: int = 5) -> dict[str, Any]:
    """Every document's render basis, counted. UNMEASURABLE over an empty board."""
    close = False
    if conn is None:
        from tools.db.storage import get_connection

        conn = get_connection()
        close = True
    try:
        docs, err = _rows(conn, "SELECT doc_id FROM dic_documents ORDER BY created_at", ())
        if err:
            return {"state": "unmeasurable", "reason": f"documents unreadable: {err}",
                    "documents": None, "by_basis": {}}
        if not docs:
            return {"state": "unmeasurable", "reason": "no documents on this board",
                    "documents": 0, "by_basis": {}}
        by_basis: dict[str, int] = {b: 0 for b in RENDER_BASES}
        by_source: dict[str, int] = {s: 0 for s in TEXT_SOURCES}
        examples: dict[str, list[str]] = {b: [] for b in RENDER_BASES}
        for d in docs:
            doc_id = d["doc_id"]
            pane = build_pane(conn, doc_id)
            by_basis[pane.basis] = by_basis.get(pane.basis, 0) + 1
            if pane.text_source:
                by_source[pane.text_source] = by_source.get(pane.text_source, 0) + 1
            if len(examples.setdefault(pane.basis, [])) < limit_examples:
                examples[pane.basis].append(doc_id)
        return {
            "state": "measured",
            "documents": len(docs),
            "by_basis": by_basis,
            "by_text_source": by_source,
            "degraded": len(docs) - by_basis.get(BASIS_SECTIONS, 0),
            "examples": examples,
        }
    finally:
        if close:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def _human(report: dict[str, Any]) -> str:
    if report.get("state") != "measured":
        return f"UNMEASURABLE — {report.get('reason', 'unknown')}"
    lines = [f"{report['documents']} document(s); {report['degraded']} render degraded", ""]
    for basis in RENDER_BASES:
        n = report["by_basis"].get(basis, 0)
        if not n:
            continue
        ex = ", ".join(report["examples"].get(basis, [])[:3])
        lines.append(f"  {basis:<16} {n:>4}   {ex}")
    src = report.get("by_text_source") or {}
    if any(src.values()):
        lines.append("")
        lines.append("  text recovered by: " + ", ".join(f"{k}={v}" for k, v in src.items() if v))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="DIC reading pane — what a document can render, and why not more")
    ap.add_argument("--survey", action="store_true", help="every document's render basis, counted")
    ap.add_argument("--doc", help="one document's pane")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.doc:
        from tools.db.storage import get_connection

        conn = get_connection()
        try:
            pane = build_pane(conn, args.doc)
        finally:
            conn.close()
        out = pane.to_dict()
        if args.json:
            print(json.dumps(out, indent=2, default=str))
        else:
            print(f"{args.doc}: basis={pane.basis} text_source={pane.text_source} "
                  f"chunks={len(pane.chunks)} sections={pane.section_count}")
            for lim in pane.limits:
                print(f"  [{lim.severity}] {lim.code}: {lim.label}")
        return 0

    report = survey()
    print(json.dumps(report, indent=2, default=str) if args.json else _human(report))
    return 0 if report.get("state") == "measured" else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
