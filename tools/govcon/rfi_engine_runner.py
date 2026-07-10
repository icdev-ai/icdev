"""RFI Engine Runner — Phase B weighted context assembly.

Pulls from already-processed engine outputs (never runs full pipelines):
  - 1st-party session uploads (past performance, capability statements)
  - Existing KG past-performance entities
  - RAG general knowledge chunks
  - Innovation engine approved signals
  - Creative engine published specs / trends
  - Research engine dossier summaries

Each source has an enabled flag and a weight (0–100). Weights are
normalized among enabled sources before blending. The assembled context
string is injected into the LLM prompt in generate_section_content().
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.rfi_engine_runner")

_DEFAULT_WEIGHTS: dict = {
    "uploads": {"enabled": True, "weight": 40},
    "kg_past_performance": {"enabled": True, "weight": 30},
    "prior_submissions": {"enabled": True, "weight": 25},
    "rag_general": {"enabled": True, "weight": 20},
    "innovation_engine": {"enabled": False, "weight": 10},
    "creative_engine": {"enabled": False, "weight": 10},
    "research_engine": {"enabled": False, "weight": 10},
}

# RAG source_types that make up the reusable-evidence corpus: approved RFI
# sections, approved proposal drafts, and uploaded prior submissions.
_EVIDENCE_SOURCE_TYPES = [
    "rfi_approved_sections",
    "proposal_approved_drafts",
    "prior_submissions",
]

# 'primary' evidence is a document we actually submitted or were graded on.
# Everything else is 'derived' — prose this system generated that a human approved.
# The tier follows from the source_type rather than a chunk metadata field, because
# metadata_cols must map to real table columns for the batch ingest path.
_PRIMARY_SOURCE_TYPES = frozenset({"prior_submissions"})

# Outcomes whose prose must never be reused as supporting evidence.
_NON_CITABLE_OUTCOMES = frozenset({"lost"})


def evidence_tier(source_type: str) -> str:
    return "primary" if source_type in _PRIMARY_SOURCE_TYPES else "derived"

# Max characters contributed per source (before weight-scaling)
_MAX_CHARS_PER_SOURCE = 1200


def _uid() -> str:
    import uuid
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_db():
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection("ICDEV_DB_URL")


# ── Weight CRUD ───────────────────────────────────────────────────────────────

def get_session_engine_weights(session_id: str) -> dict:
    """Return effective engine weights for a session (merged with defaults)."""
    try:
        db = _get_db()
        row = db.execute(
            "SELECT engine_weights FROM rfi_workbench_sessions WHERE id=%s",
            (session_id,),
        ).fetchone()
        if not row:
            return dict(_DEFAULT_WEIGHTS)
        raw = list(row)[0] if not hasattr(row, "keys") else row["engine_weights"]
        if not raw:
            return dict(_DEFAULT_WEIGHTS)
        saved = json.loads(raw) if isinstance(raw, str) else raw
        merged = {}
        for key, defaults in _DEFAULT_WEIGHTS.items():
            merged[key] = {**defaults, **(saved.get(key) or {})}
        return merged
    except Exception as exc:
        logger.warning("Could not load engine weights for %s: %s", session_id, exc)
        return dict(_DEFAULT_WEIGHTS)


def set_session_engine_weights(session_id: str, weights: dict) -> None:
    db = _get_db()
    db.execute(
        "UPDATE rfi_workbench_sessions SET engine_weights=%s, updated_at=%s WHERE id=%s",
        (json.dumps(weights), _now(), session_id),
    )
    db.commit()


def get_section_engine_weights(section_id: str) -> dict | None:
    """Return section-level override, or None if not set (use session weights)."""
    try:
        db = _get_db()
        row = db.execute(
            "SELECT engine_weights FROM rfi_workbench_sections WHERE id=%s",
            (section_id,),
        ).fetchone()
        if not row:
            return None
        raw = list(row)[0] if not hasattr(row, "keys") else row["engine_weights"]
        if not raw:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        logger.warning("Could not load section engine weights for %s: %s", section_id, exc)
        return None


def set_section_engine_weights(section_id: str, weights: dict) -> None:
    db = _get_db()
    db.execute(
        "UPDATE rfi_workbench_sections SET engine_weights=%s, updated_at=%s WHERE id=%s",
        (json.dumps(weights), _now(), section_id),
    )
    db.commit()


def get_effective_weights(session_id: str, section_id: str) -> dict:
    """Section override > session > defaults."""
    section_weights = get_section_engine_weights(section_id)
    if section_weights:
        merged = {}
        for key, defaults in _DEFAULT_WEIGHTS.items():
            merged[key] = {**defaults, **(section_weights.get(key) or {})}
        return merged
    return get_session_engine_weights(session_id)


# ── Upload management ─────────────────────────────────────────────────────────

def save_upload(
    session_id: str,
    filename: str,
    file_path: str,
    upload_type: str = "past_performance",
) -> dict:
    """Persist an upload record and extract its text."""
    uid = _uid()
    extracted = _extract_text(file_path)
    size = Path(file_path).stat().st_size if Path(file_path).exists() else 0
    db = _get_db()
    db.execute(
        "INSERT INTO rfi_session_uploads (id, session_id, filename, file_path, upload_type, extracted_text, file_size, created_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (uid, session_id, filename, file_path, upload_type, extracted, size, _now()),
    )
    db.commit()
    return {
        "id": uid,
        "session_id": session_id,
        "filename": filename,
        "upload_type": upload_type,
        "file_size": size,
        "extracted_chars": len(extracted or ""),
        "created_at": _now(),
    }


def get_uploads(session_id: str) -> list[dict]:
    try:
        db = _get_db()
        rows = db.execute(
            "SELECT id, session_id, filename, upload_type, file_size, created_at "
            "FROM rfi_session_uploads WHERE session_id=%s ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("Could not list uploads for %s: %s", session_id, exc)
        return []


def delete_upload(upload_id: str) -> bool:
    try:
        db = _get_db()
        row = db.execute(
            "SELECT file_path FROM rfi_session_uploads WHERE id=%s", (upload_id,)
        ).fetchone()
        if not row:
            return False
        fp = list(row)[0] if not hasattr(row, "keys") else row["file_path"]
        db.execute("DELETE FROM rfi_session_uploads WHERE id=%s", (upload_id,))
        db.commit()
        try:
            Path(fp).unlink(missing_ok=True)
        except Exception:
            pass
        return True
    except Exception as exc:
        logger.warning("Could not delete upload %s: %s", upload_id, exc)
        return False


def _extract_text(file_path: str) -> str:
    """Extract plain text from PDF, DOCX, or TXT. Returns empty string on failure."""
    path = Path(file_path)
    if not path.exists():
        return ""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            try:
                from pdfminer.high_level import extract_text as pdf_extract
                return pdf_extract(str(path)) or ""
            except ImportError:
                pass
            try:
                import PyPDF2
                reader = PyPDF2.PdfReader(str(path))
                return "\n".join(p.extract_text() or "" for p in reader.pages)
            except ImportError:
                pass
            return ""
        if suffix in (".docx", ".doc"):
            try:
                import docx
                doc = docx.Document(str(path))
                return "\n".join(p.text for p in doc.paragraphs)
            except ImportError:
                return ""
        # Plain text fallback
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.warning("Text extraction failed for %s: %s", file_path, exc)
        return ""


# ── Source gatherers ──────────────────────────────────────────────────────────

def _gather_uploads(session_id: str, topic: str, max_chars: int) -> str:
    """Return relevant snippets from session's past-performance uploads."""
    try:
        db = _get_db()
        rows = db.execute(
            "SELECT filename, extracted_text FROM rfi_session_uploads "
            "WHERE session_id=%s AND extracted_text IS NOT NULL AND extracted_text != '' "
            "ORDER BY created_at DESC LIMIT 5",
            (session_id,),
        ).fetchall()
        if not rows:
            return ""
        parts = []
        budget = max_chars
        keywords = set(re.sub(r"[^\w\s]", " ", topic.lower()).split())
        for row in rows:
            d = dict(row)
            text = d.get("extracted_text", "") or ""
            fname = d.get("filename", "upload")
            # Score paragraphs by keyword overlap
            paras = [p.strip() for p in text.split("\n") if len(p.strip()) > 40]
            scored = sorted(
                paras,
                key=lambda p: sum(1 for w in keywords if w in p.lower()),
                reverse=True,
            )
            snippet = " … ".join(scored[:3])[:budget]
            if snippet:
                parts.append(f"[{fname}]: {snippet}")
                budget -= len(snippet)
                if budget <= 0:
                    break
        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning("Upload gather failed: %s", exc)
        return ""


def _gather_kg_past_performance(topic: str, max_chars: int) -> str:
    """Retrieve past-performance knowledge blocks relevant to the topic.

    Historically this queried a `kg_entities` table via `tools.knowledge.kg_engine`.
    Neither exists — the import raised ImportError and the fallback query hit a
    non-existent table, so this source silently contributed nothing despite
    carrying 30% of the default evidence weight.

    The live past-performance store is `proposal_knowledge_base`, already used by
    `response_drafter.draft_response()`. Blocks are emitted with their id so the
    `[source: <id>]` citation gate can validate any claim drawn from them.
    """
    from tools.govcon.knowledge_base import search_blocks

    result = search_blocks(topic, category="past_performance", top_k=5)
    snippets = []
    for block in result.get("results", []):
        content = (block.get("content") or "").strip()
        if not content:
            continue
        snippets.append(
            f"- [source: {block.get('id', '?')}] {block.get('title', 'block')}: {content[:200]}"
        )
    return "\n".join(snippets)[:max_chars]


def _rag_search(topic: str, top_k: int, source_types: "list | None" = None) -> list:
    """Thin wrapper over the real RAG retriever.

    The retriever lives at tools.rag.get_retriever(...).search(...); the previously
    imported `tools.rag.rag_engine` module has never existed.
    """
    from tools.rag import get_retriever

    return get_retriever().search(topic, top_k=top_k, source_types=source_types)


def _gather_rag_general(topic: str, max_chars: int) -> str:
    """Retrieve relevant RAG chunks for the topic."""
    chunks = []
    for result in _rag_search(topic, top_k=4):
        text = (result.content or "").strip()
        if text:
            chunks.append(text[:300])
    return "\n\n".join(chunks)[:max_chars]


def _gather_prior_submissions(topic: str, max_chars: int) -> str:
    """Retrieve evidence from approved RFI/proposal content and uploaded prior submissions.

    Chunks carry an `evidence_tier` of 'primary' (a document we actually submitted)
    or 'derived' (prose this system generated and a human approved). Primary
    evidence is listed first, and the tier is stated inline so the model — and the
    reviewer reading the Sources panel — can tell ground truth from our own words.
    """
    results = _rag_search(topic, top_k=8, source_types=_EVIDENCE_SOURCE_TYPES)

    # A lost proposal's prose is not persuasive evidence. Its lessons belong in the
    # capture strategy (pg_win_loss_records.lessons_learned), not in the next draft.
    results = [r for r in results if (r.metadata or {}).get("outcome") not in _NON_CITABLE_OUTCOMES]

    # Primary evidence outranks derived, then fall back to the retriever's score.
    results.sort(key=lambda r: (evidence_tier(r.source_type) != "primary", -r.final_score))
    results = results[:6]

    parts = []
    for result in results:
        text = (result.content or "").strip()
        if not text:
            continue
        tier = evidence_tier(result.source_type)
        parts.append(f"- [source: {result.source_id}] ({tier}) {text[:280]}")

    if not parts:
        return ""

    header = (
        "Evidence marked (primary) comes from a document we submitted or were graded on. "
        "Evidence marked (derived) is our own previously-approved prose: reuse its framing, "
        "but do not treat it as proof of a number, a metric, or a customer outcome."
    )
    return (header + "\n" + "\n".join(parts))[:max_chars]


def _gather_innovation_signals(topic: str, max_chars: int) -> str:
    """Pull top-ranked innovation signals relevant to the topic."""
    try:
        from tools.db.storage import get_canvas_connection
        db = get_canvas_connection("ICDEV_DB_URL")
        keywords = topic[:40]
        rows = db.execute(
            "SELECT title, description, gotcha_layer FROM innovation_signals "
            "WHERE status IN ('approved','triaged') "
            "AND (title ILIKE %s OR description ILIKE %s) "
            "ORDER BY fitness_score DESC NULLS LAST LIMIT 4",
            (f"%{keywords}%", f"%{keywords}%"),
        ).fetchall()
        if not rows:
            # Broader fallback: just grab top-approved
            rows = db.execute(
                "SELECT title, description, gotcha_layer FROM innovation_signals "
                "WHERE status='approved' ORDER BY fitness_score DESC NULLS LAST LIMIT 3"
            ).fetchall()
        snippets = [
            f"- [{dict(r).get('gotcha_layer','innovation')}] {dict(r).get('title','')}: {(dict(r).get('description') or '')[:200]}"
            for r in rows
        ]
        return "\n".join(snippets)[:max_chars]
    except Exception as exc:
        logger.debug("Innovation signal gather failed: %s", exc)
        return ""


def _gather_creative_specs(topic: str, max_chars: int) -> str:
    """Pull published creative specs / trend insights relevant to the topic."""
    try:
        from tools.db.storage import get_canvas_connection
        db = get_canvas_connection("ICDEV_DB_URL")
        keywords = topic[:40]
        rows = db.execute(
            "SELECT title, executive_summary FROM creative_specs "
            "WHERE status='published' "
            "AND (title ILIKE %s OR executive_summary ILIKE %s) "
            "ORDER BY pain_score DESC NULLS LAST LIMIT 4",
            (f"%{keywords}%", f"%{keywords}%"),
        ).fetchall()
        if not rows:
            rows = db.execute(
                "SELECT title, executive_summary FROM creative_specs "
                "WHERE status='published' ORDER BY pain_score DESC NULLS LAST LIMIT 3"
            ).fetchall()
        snippets = [
            f"- {dict(r).get('title','')}: {(dict(r).get('executive_summary') or '')[:200]}"
            for r in rows
        ]
        return "\n".join(snippets)[:max_chars]
    except Exception as exc:
        logger.debug("Creative spec gather failed: %s", exc)
        return ""


def _gather_research_dossier(topic: str, max_chars: int) -> str:
    """Pull research dossier summaries relevant to the topic."""
    try:
        from tools.db.storage import get_canvas_connection
        db = get_canvas_connection("ICDEV_DB_URL")
        keywords = topic[:40]
        rows = db.execute(
            "SELECT vertical_name, executive_summary FROM research_dossiers "
            "WHERE (vertical_name ILIKE %s OR executive_summary ILIKE %s) "
            "ORDER BY created_at DESC LIMIT 3",
            (f"%{keywords}%", f"%{keywords}%"),
        ).fetchall()
        if not rows:
            rows = db.execute(
                "SELECT vertical_name, executive_summary FROM research_dossiers "
                "ORDER BY created_at DESC LIMIT 2"
            ).fetchall()
        snippets = [
            f"- [{dict(r).get('vertical_name','research')}]: {(dict(r).get('executive_summary') or '')[:250]}"
            for r in rows
        ]
        return "\n".join(snippets)[:max_chars]
    except Exception as exc:
        logger.debug("Research dossier gather failed: %s", exc)
        return ""


# ── Main assembler ────────────────────────────────────────────────────────────

_GATHERERS = {
    "uploads": _gather_uploads,
    "kg_past_performance": _gather_kg_past_performance,
    "prior_submissions": _gather_prior_submissions,
    "rag_general": _gather_rag_general,
    "innovation_engine": _gather_innovation_signals,
    "creative_engine": _gather_creative_specs,
    "research_engine": _gather_research_dossier,
}

_SOURCE_LABELS = {
    "uploads": "Past Performance / Uploads",
    "kg_past_performance": "Past Performance Knowledge Base",
    "prior_submissions": "Prior RFIs & Proposals",
    "rag_general": "RAG Knowledge Base",
    "innovation_engine": "Innovation Engine Signals",
    "creative_engine": "Creative Engine Specs",
    "research_engine": "Research Dossier",
}


def assemble_weighted_prompt_context(
    session_id: str,
    section_id: str,
    topic: str,
) -> dict:
    """Build weighted source context for injection into the generation prompt.

    Returns:
        {
            "context": str,          — assembled text block (may be empty)
            "sources_used": list,    — source keys that contributed content
            "weights_applied": dict, — normalized weight per source
            "total_chars": int,
        }
    """
    weights = get_effective_weights(session_id, section_id)

    # Compute normalized weights for enabled sources
    enabled = {k: v for k, v in weights.items() if v.get("enabled") and v.get("weight", 0) > 0}
    if not enabled:
        return {"context": "", "sources_used": [], "weights_applied": {}, "total_chars": 0}

    total_weight = sum(v["weight"] for v in enabled.values())
    total_budget = _MAX_CHARS_PER_SOURCE * len(enabled)  # 1200 chars per source max

    sources_used = []
    weights_applied = {}
    parts = []

    for source_key, cfg in enabled.items():
        norm_weight = cfg["weight"] / total_weight
        char_budget = max(200, int(total_budget * norm_weight))
        gatherer = _GATHERERS.get(source_key)
        if not gatherer:
            continue

        try:
            if source_key == "uploads":
                text = gatherer(session_id, topic, char_budget)
            else:
                text = gatherer(topic, char_budget)
        except Exception as exc:
            logger.warning("Gatherer %s failed: %s", source_key, exc)
            text = ""

        if text and text.strip():
            label = _SOURCE_LABELS.get(source_key, source_key)
            parts.append(f"## {label} (weight {norm_weight:.0%})\n{text.strip()}")
            sources_used.append(source_key)
            weights_applied[source_key] = round(norm_weight, 3)

    context = "\n\n".join(parts)
    return {
        "context": context,
        "sources_used": sources_used,
        "weights_applied": weights_applied,
        "total_chars": len(context),
    }


# ── Source availability check (Item 2) ───────────────────────────────────────

# Backing table per source, used to report "is there anything indexed for this source?"
# kg_past_performance previously pointed at `kg_entities`, which has never existed, so
# the panel reported it unavailable forever while its gatherer silently returned "".
_SOURCE_TABLES = {
    "uploads": ("rfi_session_uploads", "session_id"),
    "kg_past_performance": ("proposal_knowledge_base", None),
    "prior_submissions": ("rag_chunks", None),
    "rag_general": ("rag_chunks", None),
    "innovation_engine": ("innovation_signals", None),
    "creative_engine": ("creative_specs", None),
    "research_engine": ("research_dossiers", None),
}


def check_source_availability(session_id: str) -> dict:
    """Return {source_key: {available: bool, row_count: int}} for all 6 sources."""
    db = _get_db()
    result = {}
    for key, (table, session_col) in _SOURCE_TABLES.items():
        try:
            if session_col:
                row = db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {session_col}=%s",
                    (session_id,),
                ).fetchone()
            else:
                row = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            count = list(row)[0] if row else 0
            result[key] = {"available": count > 0, "row_count": int(count)}
        except Exception:
            result[key] = {"available": False, "row_count": 0}
    return result


# ── Phase C: on-demand engine seed (Item 6) ──────────────────────────────────

def trigger_engine_seed(session_id: str, source_key: str, topic: str) -> dict:
    """Seed the target engine's table with RFI-relevant stub content.

    Runs in a background thread. For each source, creates minimal records so
    the gatherers can return useful context on the next generate call.
    """
    try:
        if source_key == "innovation_engine":
            return _seed_innovation(topic)
        if source_key == "creative_engine":
            return _seed_creative(topic)
        if source_key == "research_engine":
            return _seed_research(session_id, topic)
        logger.warning("trigger_engine_seed: unknown source '%s'", source_key)
        return {"ok": False, "error": f"Unknown source: {source_key}"}
    except Exception as exc:
        logger.warning("Engine seed failed for %s/%s: %s", source_key, session_id, exc)
        return {"ok": False, "error": str(exc)}


def _seed_innovation(topic: str) -> dict:
    db = _get_db()
    uid = _uid()
    db.execute(
        "INSERT INTO innovation_signals (id, title, description, status, gotcha_layer, fitness_score, created_at) "
        "VALUES (%s,%s,%s,'approved','govcon',0.75,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (uid, f"RFI Signal: {topic[:80]}", f"Innovation signal seeded from RFI topic: {topic[:400]}", _now()),
    )
    db.commit()
    return {"ok": True, "seeded": uid}


def _seed_creative(topic: str) -> dict:
    db = _get_db()
    uid = _uid()
    db.execute(
        "INSERT INTO creative_specs (id, title, executive_summary, status, pain_score, created_at) "
        "VALUES (%s,%s,%s,'published',0.7,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (uid, f"GovCon Spec: {topic[:80]}", f"Creative spec seeded from RFI topic: {topic[:400]}", _now()),
    )
    db.commit()
    return {"ok": True, "seeded": uid}


def _seed_research(session_id: str, topic: str) -> dict:
    db = _get_db()
    uid = _uid()
    db.execute(
        "INSERT INTO research_dossiers (id, vertical_name, executive_summary, created_at) "
        "VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (id) DO NOTHING",
        (uid, f"GovCon: {topic[:60]}", f"Research dossier seeded from RFI session {session_id}: {topic[:400]}", _now()),
    )
    db.commit()
    return {"ok": True, "seeded": uid}
