# CUI // SP-CTI
"""Evidence corpus — the approve -> index -> retrieve -> cite flywheel.

Approved RFI sections and proposal drafts become reusable evidence for the next
pursuit, alongside prior submissions uploaded from before this system existed.

Two properties make approved content safe to reuse: the RFI export gate and
`response_drafter.approve_draft()` both refuse content that still holds
[PLACEHOLDER]/[VERIFY] tokens or has citation defects. So anything reaching
'accepted'/'approved' is placeholder-free and citation-validated.

Two properties keep it honest:

*   Tiering. Uploaded submissions are `primary` evidence; our own approved prose is
    `derived`. Retrieval ranks primary first and tells the model not to treat derived
    text as proof of a number.
*   A depth cap. A section written *from* the corpus must not re-enter it, or a claim
    would recycle forever without ever resting on a submitted document. Enforced both
    here (`_is_corpus_derived`) and in the SOURCE_REGISTRY filter.

Force-overridden content is never promoted: a draft pushed past the citation or
placeholder gate by a human override is, by definition, not validated evidence.

Ingestion goes through `ingestion_manager.ingest_single_record`, which chunks,
dedups on content_hash, embeds, and calls `_kg_enrich_chunks` — so a single call
lands the content in both RAG and the knowledge graph.
"""
from __future__ import annotations

import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.evidence_corpus")

RFI_SOURCE_TYPE = "rfi_approved_sections"
PROPOSAL_SOURCE_TYPE = "proposal_approved_drafts"
PRIOR_SOURCE_TYPE = "prior_submissions"

# Statuses that mean "a human accepted this and the export gates passed".
RFI_APPROVED_STATUSES = ("hitl_approved", "accepted")

DOC_TYPES = ("rfi", "proposal", "award", "cpars")
OUTCOMES = ("won", "lost", "no_award", "cancelled", "unknown")

# A lost proposal's prose is not persuasive evidence. Its lessons belong in the
# capture strategy, not in the next draft's supporting context.
NON_CITABLE_OUTCOMES = frozenset({"lost"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    from tools.db.storage import get_connection

    return get_connection()


def _canvas_db():
    from tools.db.storage import get_canvas_connection

    return get_canvas_connection("ICDEV_DB_URL")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


# ── Depth cap ─────────────────────────────────────────────────────────────────

def _is_corpus_derived(sources_json: str | None) -> bool:
    """True when this section was written using the evidence corpus itself.

    Such a section is one generation removed from ground truth. Promoting it would
    let a claim cite prose that cited prose, with no submitted document underneath.
    """
    if not sources_json:
        return False
    try:
        used = json.loads(sources_json)
    except (json.JSONDecodeError, TypeError):
        return False
    if isinstance(used, dict):
        used = used.get("sources_used", [])
    return PRIOR_SOURCE_TYPE in (used or []) or RFI_SOURCE_TYPE in (used or [])


# ── Promotion on approval ─────────────────────────────────────────────────────

def promote_rfi_section(section_id: str, tenant_id: str = "") -> dict:
    """Index an approved RFI section as derived evidence. Idempotent.

    Returns {"status": "indexed"|"skipped", "reason": str, "chunks": int}.
    Never raises: promotion must not be able to fail a user's Accept click.
    """
    try:
        row = _canvas_db().execute(
            "SELECT id, title, content, session_id, part, item_number, status, sources_json "
            "FROM rfi_workbench_sections WHERE id = %s",
            (section_id,),
        ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "not_found", "chunks": 0}

        section = dict(row)
        if section.get("status") not in RFI_APPROVED_STATUSES:
            return {"status": "skipped", "reason": "not_approved", "chunks": 0}

        content = (section.get("content") or "").strip()
        if not content:
            return {"status": "skipped", "reason": "empty", "chunks": 0}

        # apply_hitl only *warns* about unresolved tokens; the export gate is what
        # blocks them. An approved section still holding [VERIFY] is an unproven
        # claim, so it must not become evidence for the next pursuit.
        from tools.govcon.rfi_grounding import find_placeholders

        tokens = find_placeholders(content)
        if tokens:
            logger.info("Section %s still holds %s; not indexing as evidence", section_id, tokens)
            return {"status": "skipped", "reason": "unresolved_placeholders", "chunks": 0}

        if _is_corpus_derived(section.get("sources_json")):
            logger.info("Section %s built from the corpus; not re-indexing (depth cap)", section_id)
            return {"status": "skipped", "reason": "derivation_depth", "chunks": 0}

        return _ingest(RFI_SOURCE_TYPE, section, tenant_id)
    except Exception as exc:
        logger.warning("Could not promote RFI section %s: %s", section_id, exc)
        return {"status": "skipped", "reason": f"error: {exc}"[:120], "chunks": 0}


def promote_proposal_draft(draft_id: str, tenant_id: str = "") -> dict:
    """Index an approved proposal draft as derived evidence. Idempotent, never raises."""
    try:
        row = _db().execute(
            "SELECT id, draft_content, opportunity_id, shall_statement_id, confidence_score, "
            "draft_method, status, metadata FROM proposal_section_drafts WHERE id = %s",
            (draft_id,),
        ).fetchone()
        if not row:
            return {"status": "skipped", "reason": "not_found", "chunks": 0}

        draft = dict(row)
        if draft.get("status") != "approved":
            return {"status": "skipped", "reason": "not_approved", "chunks": 0}
        if not (draft.get("draft_content") or "").strip():
            return {"status": "skipped", "reason": "empty", "chunks": 0}
        if _was_force_overridden(draft.get("metadata")):
            logger.info("Draft %s was force-approved; not indexing as evidence", draft_id)
            return {"status": "skipped", "reason": "force_override", "chunks": 0}

        return _ingest(PROPOSAL_SOURCE_TYPE, draft, tenant_id)
    except Exception as exc:
        logger.warning("Could not promote proposal draft %s: %s", draft_id, exc)
        return {"status": "skipped", "reason": f"error: {exc}"[:120], "chunks": 0}


# approve_draft() records a bypass under these keys when force_placeholders /
# force_citations was used. A draft pushed past a gate by a human is, by definition,
# not validated evidence — reusing it would launder an unproven claim into the corpus.
_OVERRIDE_KEYS = ("placeholder_guard_override", "citation_guard_override")


def _was_force_overridden(metadata) -> bool:
    if not metadata:
        return False
    try:
        meta = json.loads(metadata) if isinstance(metadata, str) else metadata
    except (json.JSONDecodeError, TypeError):
        return False
    return any(meta.get(key) for key in _OVERRIDE_KEYS)


def _ingest(source_type: str, record: dict, tenant_id: str) -> dict:
    from tools.rag.ingestion_manager import ingest_single_record

    result = ingest_single_record(source_type, record, tenant_id=tenant_id)
    if result.get("error"):
        logger.warning("Ingest of %s failed: %s", source_type, result["error"])
        return {"status": "skipped", "reason": result["error"][:120], "chunks": 0}
    ingested = int(result.get("ingested", 0))
    if not ingested:
        return {"status": "skipped", "reason": result.get("reason", "dedup"), "chunks": 0}
    return {"status": "indexed", "reason": "", "chunks": ingested}


# ── Prior submissions (upload path) ───────────────────────────────────────────

def find_by_hash(file_hash: str) -> dict | None:
    row = _db().execute(
        "SELECT id, title, file_name, status, created_at, chunk_count "
        "FROM govcon_prior_submissions WHERE file_hash = %s",
        (file_hash,),
    ).fetchone()
    return dict(row) if row else None


def register_prior_submission(
    file_path: str,
    title: str,
    doc_type: str = "proposal",
    outcome: str = "unknown",
    solicitation_number: str = "",
    uploaded_by: str = "",
    tenant_id: str = "",
) -> dict:
    """Register an uploaded prior submission, or report that we already have it.

    Answers the drag-and-drop question directly: {"status": "duplicate"} with the
    original's ingest date, or {"status": "registered"} with a row to extract.
    """
    if doc_type not in DOC_TYPES:
        return {"status": "error", "message": f"doc_type must be one of {DOC_TYPES}"}
    if outcome not in OUTCOMES:
        return {"status": "error", "message": f"outcome must be one of {OUTCOMES}"}

    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "message": f"File not found: {file_path}"}

    digest = file_sha256(path)
    existing = find_by_hash(digest)
    if existing:
        return {"status": "duplicate", "existing": existing, "file_hash": digest}

    row_id = str(uuid.uuid4())
    conn = _db()
    conn.execute(
        "INSERT INTO govcon_prior_submissions "
        "(id, title, doc_type, outcome, solicitation_number, file_name, file_path, file_hash, "
        " file_size, status, uploaded_by, created_at, updated_at, classification) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            row_id, title, doc_type, outcome, solicitation_number, path.name, str(path),
            digest, path.stat().st_size, "pending", uploaded_by, _now(), _now(), "CUI",
        ),
    )
    conn.commit()
    return {"status": "registered", "id": row_id, "file_hash": digest}


def extract_prior_submission(submission_id: str) -> dict:
    """Extract text with the DIC extractor (four-pass PDF + OCR), not the RFI parser.

    rfi_engine_runner._extract_text returns "" on any failure, which would silently
    register an empty document. rfi_document_parser extracts *fields*, not body text.
    """
    conn = _db()
    row = conn.execute(
        "SELECT id, file_path, status FROM govcon_prior_submissions WHERE id = %s",
        (submission_id,),
    ).fetchone()
    if not row:
        return {"status": "error", "message": "submission not found"}

    conn.execute(
        "UPDATE govcon_prior_submissions SET status = %s, updated_at = %s WHERE id = %s",
        ("extracting", _now(), submission_id),
    )
    conn.commit()

    try:
        from tools.document_intelligence.extractors import extract_file

        extraction = extract_file(dict(row)["file_path"])
        text = extraction.text or ""
        method = extraction.provider or ""
        if not text.strip():
            raise ValueError("extractor produced no text")
    except Exception as exc:
        logger.warning("Extraction failed for %s: %s", submission_id, exc)
        conn.execute(
            "UPDATE govcon_prior_submissions SET status = %s, updated_at = %s WHERE id = %s",
            ("failed", _now(), submission_id),
        )
        conn.commit()
        return {"status": "failed", "message": str(exc)[:200]}

    conn.execute(
        "UPDATE govcon_prior_submissions SET extracted_text = %s, extraction_method = %s, "
        "status = %s, updated_at = %s WHERE id = %s",
        (text, method, "extracted", _now(), submission_id),
    )
    conn.commit()
    return {"status": "extracted", "chars": len(text), "method": method}


def index_prior_submission(submission_id: str, tenant_id: str = "") -> dict:
    """Chunk + embed an extracted submission into RAG (and thereby the KG)."""
    conn = _db()
    row = conn.execute(
        "SELECT id, title, extracted_text, doc_type, outcome, file_hash, classification, status "
        "FROM govcon_prior_submissions WHERE id = %s",
        (submission_id,),
    ).fetchone()
    if not row:
        return {"status": "error", "message": "submission not found"}

    submission = dict(row)
    if submission["status"] not in ("extracted", "ingested"):
        return {"status": "skipped", "reason": f"status={submission['status']}", "chunks": 0}

    result = _ingest(PRIOR_SOURCE_TYPE, submission, tenant_id)
    if result["status"] == "indexed":
        conn.execute(
            "UPDATE govcon_prior_submissions SET status = %s, chunk_count = %s, updated_at = %s "
            "WHERE id = %s",
            ("ingested", result["chunks"], _now(), submission_id),
        )
        conn.commit()
    return result


def ingest_upload(file_path: str, title: str, **kwargs) -> dict:
    """register -> extract -> index, short-circuiting on a duplicate."""
    registered = register_prior_submission(file_path, title, **kwargs)
    if registered["status"] != "registered":
        return registered

    submission_id = registered["id"]
    extracted = extract_prior_submission(submission_id)
    if extracted["status"] != "extracted":
        return {"status": "failed", "id": submission_id, **extracted}

    indexed = index_prior_submission(submission_id, tenant_id=kwargs.get("tenant_id", ""))
    return {"status": "ingested", "id": submission_id, **indexed}


# ── Library view ──────────────────────────────────────────────────────────────

def list_corpus(limit: int = 100) -> list[dict]:
    """Rows for the Evidence Library table, newest first."""
    rows = _db().execute(
        "SELECT id, title, doc_type, outcome, file_name, chunk_count, status, "
        "classification, created_at FROM govcon_prior_submissions "
        "ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    items = []
    for raw in rows:
        item = dict(raw)
        item["evidence_tier"] = "primary"
        item["citable"] = item.get("outcome") not in NON_CITABLE_OUTCOMES
        items.append(item)
    return items


def corpus_stats() -> dict:
    """Chunk counts per evidence source — the weights panel renders these so a
    source with nothing indexed reads as unavailable rather than merely silent."""
    stats = {}
    try:
        conn = _db()
        for source_type in (RFI_SOURCE_TYPE, PROPOSAL_SOURCE_TYPE, PRIOR_SOURCE_TYPE):
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM rag_chunks WHERE source_type = %s", (source_type,)
            ).fetchone()
            count = int(dict(row)["cnt"]) if row else 0
            stats[source_type] = {"chunks": count, "available": count > 0}
    except Exception as exc:
        logger.warning("Could not read corpus stats: %s", exc)
    return stats
