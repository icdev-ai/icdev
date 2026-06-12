# CUI // SP-CTI
"""Document Intelligence Canvas — Flask Blueprint.

Routes:
  GET  /document-intelligence/              index (upload + canvas overview)
  GET  /document-intelligence/collections   collections management + team access
  GET  /document-intelligence/search        grounded search + document chat
  GET  /document-intelligence/review        HITL review queue (fragments + versions)
  GET  /document-intelligence/generate      AI-assisted document generation
  GET  /document-intelligence/acoic         ACOIC drift→regen→NIST page
  GET  /document-intelligence/finetune      air-gap fine-tuning page
  GET  /document-intelligence/snippets      reusable snippets page
  GET  /document-intelligence/templates     use-case templates page

  POST /document-intelligence/api/ingest                         multi-modal upload
  POST /document-intelligence/api/search                         grounded search JSON
  POST /document-intelligence/api/chat                           document chat JSON
  GET  /document-intelligence/api/collections                    list collections
  POST /document-intelligence/api/collections                    create collection
  GET  /document-intelligence/api/collections/<id>/team          list team members
  POST /document-intelligence/api/collections/<id>/team          add team member
  POST /document-intelligence/api/review/<id>/approve            approve fragment/version
  POST /document-intelligence/api/review/<id>/reject             reject fragment/version
  POST /document-intelligence/api/generate                       AI draft generation
  POST /document-intelligence/api/iqe-query                      IQE natural-language query
"""
from __future__ import annotations

import hashlib
import json
import os
import queue as _queue
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── In-memory SSE job queues ──────────────────────────────────────────────────
# Maps job_id → queue.Queue[dict | None]  (None = sentinel / stream closed)
_JOB_QUEUES: dict[str, _queue.Queue] = {}
_JOB_LOCK = threading.Lock()

dic_bp = Blueprint(
    "dic",
    __name__,
    url_prefix="/document-intelligence",
    template_folder="../../tools/dashboard/templates",
)

# ── Static seed data ──────────────────────────────────────────────────────────

_TEMPLATES = [
    {"id": "acoic", "name": "ACOIC", "description": "Infra-drift → impacted-doc regeneration → RICOAS NIST bridge.", "flagship": True, "category": "compliance", "kind": "automation"},
    {"id": "freshness-audit", "name": "Document Freshness Audit", "description": "Scan a collection for stale documents and generate a remediation report.", "flagship": False, "category": "quality", "kind": "audit"},
    {"id": "airgap-ingest", "name": "Air-Gap Ingest Pipeline", "description": "Ingest documents from a local directory with zero cloud calls.", "flagship": False, "category": "ingest", "kind": "pipeline"},
    {"id": "hitl-review", "name": "HITL Review Queue", "description": "Surface AI-generated drafts for human review before publishing.", "flagship": False, "category": "governance", "kind": "workflow"},
    {"id": "sop-refresh", "name": "SOP Refresh", "description": "Keep standard operating procedures current against process changes.", "flagship": False, "category": "operations", "kind": "workflow"},
    {"id": "knowledge-handoff", "name": "Knowledge Handoff", "description": "Capture retiring-SME knowledge into a living collection via CoD-verified generation.", "flagship": False, "category": "knowledge", "kind": "workflow"},
]

_SNIPPETS = [
    {"id": "dic-citation-badge", "name": "Citation Badge", "description": "Inline citation chip linking a claim to its source document, chunk ID, and page.", "category": "search", "tags": ["citation", "grounded", "no-llm"]},
    {"id": "dic-freshness-indicator", "name": "Freshness Indicator", "description": "Color-coded badge (fresh / aging / stale) derived from document TTL.", "category": "quality", "tags": ["freshness", "ttl", "badge"]},
    {"id": "dic-ai-label", "name": "AI-Label Chip", "description": "Displays the HITL/AI classification label and confidence score on a document card.", "category": "governance", "tags": ["hitl", "label", "confidence"]},
    {"id": "dic-drift-trigger", "name": "Drift Trigger Button", "description": "Manual button to fire a drift event on a document or collection for ACOIC pipeline testing.", "category": "acoic", "tags": ["drift", "acoic", "debug"]},
    {"id": "dic-rag-search-bar", "name": "Grounded Search Bar", "description": "No-LLM keyword+vector search input that returns cited chunks.", "category": "search", "tags": ["rag", "no-llm", "citations"]},
]

_PAGES = [
    {"name": "Collections", "icon": "🗂️", "href": "/document-intelligence/collections", "desc": "Organize documents into collections and manage team access.", "ready": True, "task": "dic-collab-01"},
    {"name": "Search & Chat", "icon": "🔍", "href": "/document-intelligence/search", "desc": "Grounded no-LLM search with mandatory citations · Conversational AI.", "ready": True, "task": "dic-search-01"},
    {"name": "Analytics", "icon": "📊", "href": "/document-intelligence/analytics", "desc": "Entity frequency, co-occurrence, pattern detection, anomaly detection, and scenario runner.", "ready": True, "task": "dic-analytics-01"},
    {"name": "HITL Review", "icon": "👁️", "href": "/document-intelligence/review", "desc": "Human-in-the-loop oversight for AI-generated drafts and SSP fragments.", "ready": True, "task": "dic-collab-01"},
    {"name": "AI-Assist", "icon": "✨", "href": "/document-intelligence/generate", "desc": "Generate CoD-verified document drafts from your collections.", "ready": True, "task": "dic-generate-01"},
    {"name": "ACOIC", "icon": "🛰️", "href": "/document-intelligence/acoic", "desc": "Flagship bridge: drift → document impact → regen → NIST re-map.", "ready": True, "task": "dic-acoic-01"},
    {"name": "Air-Gap Fine-Tuning", "icon": "🧪", "href": "/document-intelligence/finetune", "desc": "Train a local model on a collection's chunks/KG (GPU optional).", "ready": True, "task": "dic-finetune-01"},
    {"name": "Snippets", "icon": "🧩", "href": "/document-intelligence/snippets", "desc": "Reusable UI building blocks for document workflows.", "ready": True, "task": "dic-snippets-01"},
    {"name": "Templates", "icon": "📐", "href": "/document-intelligence/templates", "desc": "Pre-built document workflows. ACOIC is the flagship.", "ready": True, "task": "dic-templates-01"},
    {"name": "Freshness", "icon": "🌡️", "href": "/document-intelligence/freshness", "desc": "Corpus staleness heatmap and remediation queue.", "ready": True, "task": "dic-freshness-01"},
    {"name": "Explorer", "icon": "🔎", "href": "/document-intelligence/explorer", "desc": "KG buried-bodies explorer — orphans, tribal knowledge, contradictions.", "ready": True, "task": "dic-explore-01"},
    {"name": "Handoff", "icon": "🤝", "href": "/document-intelligence/handoff", "desc": "Knowledge handoff — capture retiring SME knowledge into a living collection.", "ready": True, "task": "dic-handoff-01"},
]

_LOCAL_PROVIDERS = ["ollama", "llamacpp", "huggingface-local"]

# ── Init ──────────────────────────────────────────────────────────────────────

_INIT_DONE = False


def _ensure_init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        from tools.document_intelligence.db.init_db import init_db
        init_db()
    except Exception as exc:
        logger.warning("dic: DB init error: %s", exc)
    # Ensure review-notes schema (lives in ingest_orchestrator).
    try:
        from tools.document_intelligence.ingest_orchestrator import _ensure_schema as _ingest_ensure
        c = _conn()
        try:
            _ingest_ensure(c)
        finally:
            c.close()
    except Exception as exc:
        logger.warning("dic: review schema init error: %s", exc)
    _INIT_DONE = True


@dic_bp.before_request
def _init():
    _ensure_init()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _safe_rows(conn, sql: str, params: tuple = ()) -> list[dict]:
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("dic: query error: %s", exc)
        return []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hid(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _security_context() -> tuple[str, str]:
    try:
        from flask import g
        ctx = getattr(g, "security_context", None) or {}
        return ctx.get("tenant_id", "default"), ctx.get("classification", "CUI")
    except Exception:
        return "default", "CUI"


# Role hierarchy for collaboration workflow.
_ROLE_LEVEL = {"viewer": 0, "editor": 1, "reviewer": 2, "admin": 3}


def _current_user() -> str:
    """Return the best-effort current user id."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            ctx = getattr(g, "security_context", None) or {}
            user = ctx.get("user_id") or ctx.get("username")
            if user:
                return user
    except Exception:
        pass
    return "current_user"


def _user_role(collection_id: str, user_id: str) -> str:
    """Look up the user's role in a collection via dic_team_access."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT role FROM dic_team_access WHERE collection_id = ? AND user_id = ? LIMIT 1",
            (collection_id, user_id),
        ).fetchone()
        if row:
            return row[0] if hasattr(row, "__getitem__") else row["role"]
    except Exception:
        pass
    finally:
        conn.close()
    return "viewer"


def _require_role(collection_id: str, min_role: str) -> bool:
    """Return True if current user meets or exceeds min_role."""
    user_id = _current_user()
    role = _user_role(collection_id, user_id)
    return _ROLE_LEVEL.get(role, 0) >= _ROLE_LEVEL.get(min_role, 99)


def _role_badge(role: str) -> str:
    return {
        "viewer": "🔎 Viewer",
        "editor": "✏️ Editor",
        "reviewer": "👁️ Reviewer",
        "admin": "🛡️ Admin",
    }.get(role, role)


def _collection_id_from_doc(doc_id: str) -> str | None:
    conn = _conn()
    try:
        row = conn.execute("SELECT collection_id FROM dic_documents WHERE doc_id = ? LIMIT 1", (doc_id,)).fetchone()
        if row:
            return row[0] if hasattr(row, "__getitem__") else row["collection_id"]
    except Exception:
        pass
    finally:
        conn.close()
    return None


def _collection_id_from_version(version_id: str) -> str | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT d.collection_id FROM dic_versions v JOIN dic_documents d ON d.doc_id = v.doc_id WHERE v.version_id = ? LIMIT 1",
            (version_id,),
        ).fetchone()
        if row:
            return row[0] if hasattr(row, "__getitem__") else row["collection_id"]
    except Exception:
        pass
    finally:
        conn.close()
    return None


def _collection_id_from_section(section_id: str) -> str | None:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT d.collection_id FROM dic_sections s JOIN dic_documents d ON d.doc_id = s.doc_id WHERE s.section_id = ? LIMIT 1",
            (section_id,),
        ).fetchone()
        if row:
            return row[0] if hasattr(row, "__getitem__") else row["collection_id"]
    except Exception:
        pass
    finally:
        conn.close()
    return None


def _forbid(role: str, msg: str = "Insufficient permissions") -> tuple:
    return jsonify({"error": msg}), 403


# ── Page Routes ───────────────────────────────────────────────────────────────

@dic_bp.route("/")
def index():
    return render_template("document_intelligence/index.html", pages=_PAGES)


@dic_bp.route("/collections")
def collections():
    conn = _conn()
    try:
        cols = _safe_rows(conn, "SELECT * FROM dic_collections ORDER BY created_at DESC LIMIT 100")
        for c in cols:
            try:
                doc_count_row = conn.execute(
                    "SELECT COUNT(*) FROM dic_documents WHERE collection_id = ?", (c["collection_id"],)
                ).fetchone()
                c["doc_count"] = doc_count_row[0] if doc_count_row else 0
            except Exception:
                c["doc_count"] = 0
            try:
                team_row = conn.execute(
                    "SELECT COUNT(*) FROM dic_team_access WHERE collection_id = ?", (c["collection_id"],)
                ).fetchone()
                c["team_size"] = team_row[0] if team_row else 0
            except Exception:
                c["team_size"] = 0
    finally:
        conn.close()
    return render_template("document_intelligence/collections.html", collections=cols)


@dic_bp.route("/search")
def search():
    collection_id = request.args.get("collection", "")
    return render_template("document_intelligence/search.html", collection_id=collection_id)


# ── Document Detail Page ──────────────────────────────────────────────────────

@dic_bp.route("/doc/<doc_id>")
def doc_detail(doc_id: str):
    conn = _conn()
    try:
        doc = _safe_rows(conn, "SELECT * FROM dic_documents WHERE doc_id = ? LIMIT 1", (doc_id,))
        doc = doc[0] if doc else {}
        versions = _safe_rows(
            conn,
            "SELECT version_id, version_no, origin, status, assigned_to, created_at, created_by "
            "FROM dic_versions WHERE doc_id = ? ORDER BY version_no DESC",
            (doc_id,),
        )
        # Load sections for the latest pending or latest version
        active_version_id = ""
        for v in versions:
            if v["status"] in ("pending_review", "needs_revision", "draft"):
                active_version_id = v["version_id"]
                break
        if not active_version_id and versions:
            active_version_id = versions[0]["version_id"]
        sections = []
        if active_version_id:
            sections = _safe_rows(
                conn,
                "SELECT section_id, heading, content, citations_json, status, origin, assigned_to "
                "FROM dic_sections WHERE version_id = ? ORDER BY rowid",
                (active_version_id,),
            )
            for s in sections:
                try:
                    s["citations"] = json.loads(s.get("citations_json") or "[]")
                except Exception:
                    s["citations"] = []
        # Team members for assignment dropdown
        collection_id = doc.get("collection_id") or "default"
        team = _safe_rows(
            conn,
            "SELECT user_id, role FROM dic_team_access WHERE collection_id = ? ORDER BY role DESC, user_id",
            (collection_id,),
        )
    finally:
        conn.close()

    current_user = _current_user()
    user_role = _user_role(collection_id, current_user)
    return render_template(
        "document_intelligence/doc_detail.html",
        doc=doc,
        versions=versions,
        sections=sections,
        active_version_id=active_version_id,
        team=team,
        current_user=current_user,
        user_role=user_role,
        role_badge=_role_badge,
        role_levels=_ROLE_LEVEL,
    )


@dic_bp.route("/review")
def review():
    conn = _conn()
    try:
        pending_fragments = _safe_rows(
            conn,
            "SELECT fragment_id, document_id, control_id, fragment_text, cod_verdict_json as cod_verdict, "
            "status, assigned_to, created_at, reviewed_by FROM dic_ssp_fragments "
            "WHERE status IN ('pending_review', 'needs_revision') "
            "ORDER BY created_at DESC LIMIT 50",
        )
        pending_versions = _safe_rows(
            conn,
            "SELECT v.version_id, v.doc_id, v.version_no, v.origin, v.status, "
            "v.assigned_to, v.created_at, v.created_by, d.title as doc_title, d.collection_id "
            "FROM dic_versions v LEFT JOIN dic_documents d ON d.doc_id = v.doc_id "
            "WHERE v.status IN ('pending_review', 'needs_revision') ORDER BY v.created_at DESC LIMIT 50",
        )
        # Gather team members per collection for assignment dropdowns.
        team_map: dict[str, list[dict]] = {}
        for v in pending_versions:
            cid = v.get("collection_id") or "default"
            if cid not in team_map:
                team_map[cid] = _safe_rows(
                    conn,
                    "SELECT user_id, role FROM dic_team_access WHERE collection_id = ? ORDER BY role DESC, user_id",
                    (cid,),
                )
        # Documents with pending sections (for the Documents tab).
        pending_docs = _safe_rows(
            conn,
            "SELECT DISTINCT d.doc_id, d.title, d.collection_id, d.filename, d.classification, "
            "(SELECT COUNT(*) FROM dic_sections s2 WHERE s2.doc_id = d.doc_id AND s2.status IN ('pending_review','needs_revision','draft')) AS pending_section_count "
            "FROM dic_documents d JOIN dic_sections s ON s.doc_id = d.doc_id "
            "WHERE s.status IN ('pending_review', 'needs_revision', 'draft') "
            "ORDER BY pending_section_count DESC LIMIT 50",
        )
        # Gather team members per collection for assignment dropdowns.
        team_map: dict[str, list[dict]] = {}
        for v in pending_versions:
            cid = v.get("collection_id") or "default"
            if cid not in team_map:
                team_map[cid] = _safe_rows(
                    conn,
                    "SELECT user_id, role FROM dic_team_access WHERE collection_id = ? ORDER BY role DESC, user_id",
                    (cid,),
                )
        for pd in pending_docs:
            cid = pd.get("collection_id") or "default"
            if cid not in team_map:
                team_map[cid] = _safe_rows(
                    conn,
                    "SELECT user_id, role FROM dic_team_access WHERE collection_id = ? ORDER BY role DESC, user_id",
                    (cid,),
                )
        # Load latest review note per item.
        notes_map: dict[str, str] = {}
        for rows in (
            _safe_rows(conn, "SELECT item_id, note_text FROM dic_review_notes WHERE item_type='version' ORDER BY created_at DESC LIMIT 200"),
            _safe_rows(conn, "SELECT item_id, note_text FROM dic_review_notes WHERE item_type='fragment' ORDER BY created_at DESC LIMIT 200"),
        ):
            for r in rows:
                if r["item_id"] not in notes_map:
                    notes_map[r["item_id"]] = r["note_text"] or ""
    finally:
        conn.close()

    # Augment versions/fragments/docs with latest note and current-user role.
    current_user = _current_user()
    for v in pending_versions:
        v["latest_note"] = notes_map.get(v["version_id"], "")
        v["user_role"] = _user_role(v.get("collection_id") or "default", current_user)
    for f in pending_fragments:
        f["latest_note"] = notes_map.get(f["fragment_id"], "")
        f["user_role"] = _user_role("default", current_user)
    for pd in pending_docs:
        pd["user_role"] = _user_role(pd.get("collection_id") or "default", current_user)

    return render_template(
        "document_intelligence/review.html",
        pending_fragments=pending_fragments,
        pending_versions=pending_versions,
        pending_docs=pending_docs,
        team_map=team_map,
        current_user=current_user,
        role_badge=_role_badge,
        role_levels=_ROLE_LEVEL,
    )


# Template defaults for query prefill when arriving from /templates.
_TEMPLATE_DEFAULTS = {
    "acoic": "ACOIC drift → impacted document regeneration → NIST 800-53 re-map",
    "freshness-audit": "Document freshness audit — identify stale documents and remediation plan",
    "airgap-ingest": "Air-gap ingest pipeline — ingest local documents with zero cloud calls",
    "hitl-review": "HITL review queue — surface AI-generated drafts for human review",
    "sop-refresh": "SOP refresh — keep standard operating procedures current against process changes",
    "knowledge-handoff": "Knowledge handoff — capture retiring SME knowledge into a living collection",
}


@dic_bp.route("/generate")
def generate():
    preselected = request.args.get("template", "").strip()
    default_query = _TEMPLATE_DEFAULTS.get(preselected, "")
    return render_template(
        "document_intelligence/generate.html",
        templates=_TEMPLATES,
        preselected_template=preselected,
        default_query=default_query,
    )


@dic_bp.route("/acoic")
def acoic():
    conn = _conn()
    try:
        drift_events = _safe_rows(conn, "SELECT source, entity, severity, detected_at FROM dic_drift_events ORDER BY detected_at DESC LIMIT 50")
        regen_queue = _safe_rows(conn, "SELECT document_id, impact_level, state, queued_at FROM dic_acoic_regen_queue ORDER BY queued_at DESC LIMIT 50")
        ssp_fragments = _safe_rows(conn, "SELECT control_id, document_id, status FROM dic_ssp_fragments ORDER BY created_at DESC LIMIT 50")
    finally:
        conn.close()
    return render_template("document_intelligence/acoic.html", drift_events=drift_events, regen_queue=regen_queue, ssp_fragments=ssp_fragments)


@dic_bp.route("/finetune")
def finetune():
    return render_template("document_intelligence/finetune.html", local_providers=_LOCAL_PROVIDERS)


@dic_bp.route("/snippets")
def snippets():
    return render_template("document_intelligence/snippets.html", snippets=_SNIPPETS)


@dic_bp.route("/templates")
def templates_page():
    return render_template("document_intelligence/templates.html", templates=_TEMPLATES)


@dic_bp.route("/freshness")
def freshness():
    tenant_id, _ = _security_context()
    try:
        from tools.document_intelligence.freshness_engine import corpus_heatmap
        heatmap = corpus_heatmap(tenant_id=tenant_id, limit=200)
    except Exception as exc:
        logger.warning("dic: freshness heatmap error: %s", exc)
        heatmap = []
    # Group by collection for the UI.
    by_collection: dict[str, list[dict]] = {}
    for row in heatmap:
        cid = row.get("collection_id") or "default"
        by_collection.setdefault(cid, []).append(row)
    return render_template("document_intelligence/freshness.html", heatmap=heatmap, by_collection=by_collection)


@dic_bp.route("/explorer")
def explorer():
    tenant_id, _ = _security_context()
    try:
        from tools.document_intelligence.explorer import run_explorer
        findings = run_explorer(tenant_id=tenant_id, limit=100)
    except Exception as exc:
        logger.warning("dic: explorer error: %s", exc)
        findings = []
    return render_template("document_intelligence/explorer.html", findings=findings)


@dic_bp.route("/handoff")
def handoff():
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        sessions = _safe_rows(
            conn,
            "SELECT session_id, departing_owner_id, successor_owner_id, dest_collection_id, title, status, "
            "agenda_count, answered_count, generated_count, orphan_count, created_at "
            "FROM dic_handoff_sessions WHERE tenant_id = ? ORDER BY created_at DESC LIMIT 50",
            (tenant_id,),
        )
    except Exception as exc:
        logger.warning("dic: handoff query error: %s", exc)
        sessions = []
    finally:
        conn.close()
    return render_template("document_intelligence/handoff.html", sessions=sessions)


# ── API: Upload / Ingest ──────────────────────────────────────────────────────

@dic_bp.route("/api/ingest", methods=["POST"])
def api_ingest():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "no file provided"}), 400

    collection_id = (request.form.get("collection_id") or "default").strip()
    classification = (request.form.get("classification") or "CUI").strip()
    tenant_id, _ = _security_context()
    filename = file.filename or "upload"

    # Save file to temp immediately (before thread starts).
    suffix = Path(filename).suffix.lower()
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        file.save(tmp)
        tmp.close()
        tmp_path = tmp.name
    except Exception as exc:
        return jsonify({"error": f"file save failed: {exc}"}), 500

    # Create job + SSE queue.
    job_id = uuid.uuid4().hex
    q: _queue.Queue = _queue.Queue()
    with _JOB_LOCK:
        _JOB_QUEUES[job_id] = q

    # Persist job record (best-effort).
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO dic_ingest_jobs (job_id, filename, collection_id, status, tenant_id) "
            "VALUES (?,?,?,?,?)",
            (job_id, filename, collection_id, "queued", tenant_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    def _run():
        outcome = None
        try:
            def _cb(stage: str, detail: str, pct: int) -> None:
                q.put({"stage": stage, "detail": detail, "pct": pct})
                # Update DB status.
                try:
                    c = _conn()
                    c.execute(
                        "UPDATE dic_ingest_jobs SET status=?, stage_detail=?, updated_at=? WHERE job_id=?",
                        (stage, detail, _now(), job_id),
                    )
                    c.commit()
                    c.close()
                except Exception:
                    pass

            from tools.document_intelligence.ingest_orchestrator import ingest_file
            outcome = ingest_file(
                tmp_path, collection_id,
                tenant_id=tenant_id, classification=classification,
                created_by="dashboard_upload", progress_cb=_cb,
            )
            q.put({
                "stage": "done",
                "doc_id": outcome.doc_id,
                "chunks": outcome.chunks,
                "chunks_embedded": outcome.chunks_embedded,
                "kg_entities": outcome.kg_entities,
                "errors": outcome.errors,
                "pct": 100,
            })
            # Update DB to done.
            try:
                c = _conn()
                c.execute(
                    "UPDATE dic_ingest_jobs SET status='done', doc_id=?, chunks_total=?, "
                    "chunks_done=?, errors_json=?, updated_at=? WHERE job_id=?",
                    (outcome.doc_id, outcome.chunks, outcome.chunks_embedded,
                     json.dumps(outcome.errors), _now(), job_id),
                )
                c.commit()
                c.close()
            except Exception:
                pass
        except Exception as exc:
            logger.warning("dic: ingest thread error: %s", exc)
            q.put({"stage": "error", "message": str(exc), "pct": 0})
            try:
                c = _conn()
                c.execute(
                    "UPDATE dic_ingest_jobs SET status='error', stage_detail=?, updated_at=? WHERE job_id=?",
                    (str(exc), _now(), job_id),
                )
                c.commit()
                c.close()
            except Exception:
                pass
        finally:
            q.put(None)  # Sentinel — SSE stream can close.
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({
        "job_id": job_id,
        "filename": filename,
        "stream_url": f"/document-intelligence/api/ingest/{job_id}/stream",
        "result_url": f"/document-intelligence/api/ingest/{job_id}/result",
        "status": "queued",
    }), 202


@dic_bp.route("/api/ingest/<job_id>/stream", methods=["GET"])
def api_ingest_stream(job_id: str):
    """SSE stream for ingest job progress. Closes when done/error sentinel received."""
    with _JOB_LOCK:
        q = _JOB_QUEUES.get(job_id)
    if q is None:
        # Job may have already completed — check DB.
        conn = _conn()
        try:
            row = _safe_rows(conn, "SELECT status, stage_detail, chunks_total, doc_id FROM dic_ingest_jobs WHERE job_id=?", (job_id,))
        finally:
            conn.close()
        if row:
            r = row[0]
            data = json.dumps({"stage": r["status"], "detail": r["stage_detail"], "chunks": r["chunks_total"], "doc_id": r["doc_id"], "pct": 100})
            return Response(f"data: {data}\n\n", mimetype="text/event-stream")
        return jsonify({"error": "job not found"}), 404

    def _generate():
        try:
            while True:
                try:
                    event = q.get(timeout=30)
                except _queue.Empty:
                    yield "data: {\"stage\": \"heartbeat\"}\n\n"
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("stage") in ("done", "error"):
                    break
        finally:
            with _JOB_LOCK:
                _JOB_QUEUES.pop(job_id, None)

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@dic_bp.route("/api/ingest/<job_id>/result", methods=["GET"])
def api_ingest_result(job_id: str):
    """Return final ingest job outcome."""
    conn = _conn()
    try:
        rows = _safe_rows(conn, "SELECT * FROM dic_ingest_jobs WHERE job_id=?", (job_id,))
    finally:
        conn.close()
    if not rows:
        return jsonify({"error": "job not found"}), 404
    return jsonify(rows[0])


# ── API: KG Explorer ─────────────────────────────────────────────────────────

@dic_bp.route("/api/kg-explore", methods=["POST"])
def api_kg_explore():
    """Entity and relationship search over the KG extracted from DIC documents.

    Modes:
      entities  — return all entities matching an optional label filter
      relations — return relationships involving a specific entity label
      neighbors — return all entities directly connected to a given entity
    """
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "entities")
    label = (data.get("label") or "").strip()
    entity_type = data.get("entity_type")
    limit = min(int(data.get("limit", 50)), 200)
    conn = _conn()
    try:
        if mode == "entities":
            sql = (
                "SELECT n.id, n.label, n.entity_type, n.centrality, n.source_chunk_id, "
                "g.source_doc_id FROM kg_nodes n LEFT JOIN kg_graphs g ON g.id = n.graph_id"
            )
            params: list = []
            clauses = []
            if label:
                clauses.append("LOWER(n.label) LIKE LOWER(?)")
                params.append(f"%{label}%")
            if entity_type:
                clauses.append("n.entity_type = ?")
                params.append(entity_type)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY n.centrality DESC LIMIT ?"
            params.append(limit)
            rows = _safe_rows(conn, sql, tuple(params))
            return jsonify({"entities": rows, "count": len(rows)})

        elif mode == "relations":
            if not label:
                return jsonify({"error": "label is required for relations mode"}), 400
            rows = _safe_rows(
                conn,
                "SELECT src.label AS source, tgt.label AS target, e.relationship, e.weight "
                "FROM kg_edges e "
                "JOIN kg_nodes src ON src.id = e.source_id "
                "JOIN kg_nodes tgt ON tgt.id = e.target_id "
                "WHERE LOWER(src.label) LIKE LOWER(?) OR LOWER(tgt.label) LIKE LOWER(?) "
                "ORDER BY e.weight DESC LIMIT ?",
                (f"%{label}%", f"%{label}%", limit),
            )
            return jsonify({"relationships": rows, "count": len(rows)})

        elif mode == "neighbors":
            if not label:
                return jsonify({"error": "label is required for neighbors mode"}), 400
            # Find the node id first
            node_rows = _safe_rows(
                conn,
                "SELECT id, label, entity_type FROM kg_nodes WHERE LOWER(label) LIKE LOWER(?) LIMIT 1",
                (f"%{label}%",),
            )
            if not node_rows:
                return jsonify({"neighbors": [], "relationships": [], "count": 0})
            node_id = node_rows[0]["id"]
            neighbors = _safe_rows(
                conn,
                "SELECT DISTINCT n.label, n.entity_type, e.relationship, e.weight "
                "FROM kg_edges e "
                "JOIN kg_nodes n ON (n.id = e.target_id OR n.id = e.source_id) "
                "WHERE (e.source_id = ? OR e.target_id = ?) AND n.id != ? "
                "ORDER BY e.weight DESC LIMIT ?",
                (node_id, node_id, node_id, limit),
            )
            return jsonify({
                "entity": node_rows[0],
                "neighbors": neighbors,
                "count": len(neighbors),
            })

        return jsonify({"error": f"unknown mode: {mode}"}), 400
    except Exception as exc:
        logger.warning("dic: kg-explore error: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── API: Search ───────────────────────────────────────────────────────────────

@dic_bp.route("/api/search", methods=["POST"])
def api_search():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    collection_id = data.get("collection_id")
    mode = data.get("mode", "grounded")
    top_k = min(int(data.get("top_k", 10)), 50)
    explain_access = bool(data.get("explain_access"))
    expand = bool(data.get("expand"))
    keywords = data.get("keywords")
    tenant_id, clearance = _security_context()

    try:
        from tools.document_intelligence.search_engine import DICSearchEngine
        engine = DICSearchEngine(tenant_id=tenant_id)
        # Opt-in: broaden the query with LLM-suggested synonyms for better recall.
        # Degrades to the original query when the LLM is unavailable.
        expansion = None
        effective_query = query
        if expand:
            expansion = engine.expand_query(query)
            effective_query = expansion.expanded_query or query
        # Enforce the caller's clearance: results above it are never returned.
        results = engine.search(
            effective_query, collection_id=collection_id, top_k=top_k, mode=mode, clearance=clearance,
        )
        # Results are ordered through the attribution lens (strongly-supporting
        # evidence first); citation_quality is the per-answer sufficiency score.
        from tools.document_intelligence.search_engine import _citation_quality
        payload = {
            "results": [r.to_dict() for r in results],
            "count": len(results),
            "citation_quality": round(_citation_quality(results), 4),
        }
        if expansion is not None:
            payload["expansion"] = expansion.to_dict()
        # Opt-in: embedding-based search over a literal keyword list. Semantic
        # matches surface even when a document lacks the literal term; degrades
        # to literal keyword matching when no embedding provider is available.
        if isinstance(keywords, list) and keywords:
            kw_result = engine.keyword_search(
                keywords, collection_id=collection_id, top_k=top_k, clearance=clearance,
            )
            payload["keyword_search"] = kw_result.to_dict()
        # Opt-in: explain (without leaking) what was withheld above clearance.
        if explain_access:
            expl = engine.access_explanation(
                query, clearance=clearance, collection_id=collection_id, top_k=top_k, mode=mode,
            )
            payload["access"] = expl.to_dict()
        return jsonify(payload)
    except Exception as exc:
        logger.warning("dic: search error: %s", exc)
        return jsonify({"results": [], "error": str(exc)}), 500


# ── API: Provenance ────────────────────────────────────────────────────────────

@dic_bp.route("/api/provenance/<chunk_id>", methods=["GET"])
def api_provenance(chunk_id: str):
    """Return AIDP attribution provenance for a chunk: SHA-256, classification, attribution score."""
    import hashlib
    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        # Look up chunk — PG uses 'id' (text), SQLite may use 'chunk_id'
        cur = conn.execute(
            "SELECT content, content_hash, source_id, classification FROM rag_chunks WHERE id = ?",
            (chunk_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "chunk not found"}), 404

        content = row["content"] if hasattr(row, "__getitem__") else row[0]
        content_hash = row["content_hash"] if hasattr(row, "__getitem__") else row[1]
        source_id = row["source_id"] if hasattr(row, "__getitem__") else row[2]
        classification = row["classification"] if hasattr(row, "__getitem__") else row[3]
        sha256 = content_hash or hashlib.sha256((content or "").encode()).hexdigest()

        # Look up doc linkage
        cur2 = conn.execute(
            "SELECT doc_id, collection_id, page, section FROM dic_chunk_links WHERE rag_chunk_id = ?",
            (chunk_id,),
        )
        link = cur2.fetchone()
        if link and hasattr(link, "__getitem__") and "doc_id" in (link.keys() if hasattr(link, "keys") else []):
            doc_id = link["doc_id"] or source_id or ""
            collection_id = link["collection_id"] or ""
            page = link["page"] or 0
            section = link["section"] or ""
        elif link:
            doc_id = link[0] or source_id or ""
            collection_id = link[1] or ""
            page = link[2] or 0
            section = link[3] or ""
        else:
            doc_id = source_id or ""
            collection_id = ""
            page = 0
            section = ""

        # Attribution score: cosine-similarity proxy via content length heuristic
        content_len = len(content or "")
        attribution_pct = min(100, max(40, int((content_len / 500) * 80)))

        # Archive link points to DIC doc detail page
        archive_url = f"/document-intelligence/doc/{doc_id}" if doc_id else "#"

        return jsonify({
            "chunk_id": chunk_id,
            "sha256": sha256,
            "classification": classification or "CUI",
            "attribution_pct": attribution_pct,
            "archive_url": archive_url,
            "doc_id": doc_id,
            "collection_id": collection_id,
            "page": page,
            "section": section,
        })
    except Exception as exc:
        logger.warning("dic: provenance error: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── API: Chat ─────────────────────────────────────────────────────────────────

# Synthesis keywords — LLM is warranted only for these query types.
_SYNTHESIS_KEYWORDS = frozenset([
    "summarize", "summary", "compare", "contrast", "explain", "describe",
    "how does", "why does", "what does", "what is the difference",
    "write", "draft", "generate", "create", "list all", "what are all",
])


def _needs_synthesis(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _SYNTHESIS_KEYWORDS)


def _compile_grounded_answer(results: list, query: str) -> str:
    """Build a grounded answer directly from RAG chunks — no LLM."""
    if not results:
        return "No relevant documents found."
    lines = []
    for r in results[:4]:
        citation = f"[{r.doc_title or r.doc_id} · p.{r.page}]" if r.page else f"[{r.doc_title or r.doc_id}]"
        snippet = r.content[:300].strip()
        if snippet:
            lines.append(f"{citation} {snippet}")
    return "\n\n".join(lines) if lines else results[0].content[:400]


def _llm_synthesize(message: str, results: list, evidence: str) -> str | None:
    """Call LLM only when synthesis is warranted. Returns None on failure."""
    try:
        for ns in ("icdev.tools.llm.router", "tools.llm.router"):
            try:
                import importlib
                mod = importlib.import_module(ns)
                router = mod.LLMRouter()
                prompt = (
                    "You are a document assistant. Answer ONLY using the provided evidence — "
                    "do not add information beyond what is cited. Cite sources inline as "
                    "[chunk <id>]. If the evidence is insufficient, say so explicitly.\n\n"
                    f"Evidence:\n{evidence}\n\n"
                    f"Question: {message}\n\nAnswer:"
                )
                for meth in ("generate", "complete", "chat", "route", "call"):
                    fn = getattr(router, meth, None)
                    if callable(fn):
                        result = fn(prompt)
                        if isinstance(result, str):
                            return result
                        if isinstance(result, dict):
                            return result.get("text") or result.get("content")
            except ImportError:
                continue
    except Exception as exc:
        logger.warning("dic: chat LLM error: %s", exc)
    return None


@dic_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """DIC chat: RAG+KG first. LLM only for synthesis queries when available.

    Mode logic:
    1. Always retrieve from RAG+KG (grounded, air-gap safe).
    2. If top result confidence is high (≥0.4) AND query is a direct lookup:
       → return grounded answer directly, mode="grounded", NO LLM call.
    3. If query needs synthesis (summarize/compare/explain/…) AND LLM available:
       → synthesize from evidence, mode="ai_assisted", verify with CoD gate.
    4. If LLM unavailable or synthesis not needed:
       → compile grounded answer from top chunks, mode="grounded".
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    collection_id = data.get("collection_id")
    tenant_id, _cls = _security_context()

    try:
        from tools.document_intelligence.search_engine import DICSearchEngine
        engine = DICSearchEngine(tenant_id=tenant_id)
        results = engine.search(message, collection_id=collection_id, top_k=8)

        if not results:
            return jsonify({
                "answer": "No relevant documents found in this collection. Upload documents first.",
                "citations": [],
                "abstained": True,
                "mode": "grounded",
            })

        citations = [r.citation.to_dict() for r in results[:5]]
        top_score = results[0].score if results else 0.0

        # ── Path 1: High-confidence direct lookup — NO LLM ──────────────────
        if top_score >= 0.4 and not _needs_synthesis(message):
            answer = _compile_grounded_answer(results, message)
            return jsonify({
                "answer": answer,
                "citations": citations,
                "abstained": False,
                "mode": "grounded",
            })

        # ── Path 2: Grounded answer from top chunks — NO LLM ────────────────
        grounded_answer = _compile_grounded_answer(results, message)

        # ── Path 3: LLM synthesis if query warrants it ───────────────────────
        answer = grounded_answer
        mode = "grounded"
        abstained = False

        if _needs_synthesis(message):
            evidence = "\n\n".join(
                f"[chunk {r.chunk_id}] {r.content[:400]}" for r in results
            )
            llm_answer = _llm_synthesize(message, results, evidence)
            if llm_answer:
                # Verify LLM answer against evidence before returning.
                try:
                    from tools.document_intelligence.verifier import verify
                    vr = verify(llm_answer, [r.content for r in results])
                    if vr.abstained:
                        abstained = True
                        answer = grounded_answer  # fall back to grounded
                    else:
                        answer = vr.verified_text or llm_answer
                        mode = "ai_assisted"
                except Exception:
                    answer = llm_answer
                    mode = "ai_assisted"

        return jsonify({
            "answer": answer,
            "citations": citations,
            "abstained": abstained,
            "mode": mode,
        })
    except Exception as exc:
        logger.warning("dic: chat error: %s", exc)
        return jsonify({"answer": f"Error: {exc}", "citations": [], "abstained": True, "mode": "grounded"}), 500


# ── API: Collections ──────────────────────────────────────────────────────────

@dic_bp.route("/api/collections", methods=["GET"])
def api_collections_list():
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        rows = _safe_rows(conn, "SELECT * FROM dic_collections WHERE tenant_id = ? ORDER BY created_at DESC", (tenant_id,))
        return jsonify({"collections": rows})
    finally:
        conn.close()


@dic_bp.route("/api/collections", methods=["POST"])
def api_collections_create():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    # Collection creation requires admin role in the tenant (use default collection as proxy).
    if not _require_role("default", "admin"):
        return _forbid("admin")
    tenant_id, classification = _security_context()
    collection_id = _hid("dic_col", name, tenant_id, _now())
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO dic_collections (collection_id, name, description, classification, tenant_id) "
            "VALUES (?,?,?,?,?)",
            (collection_id, name, data.get("description", ""), data.get("classification", classification), tenant_id),
        )
        conn.commit()
        return jsonify({"collection_id": collection_id, "name": name})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/collections/<collection_id>/team", methods=["GET"])
def api_team_list(collection_id):
    conn = _conn()
    try:
        members = _safe_rows(conn, "SELECT user_id, role, granted_by, created_at FROM dic_team_access WHERE collection_id = ? ORDER BY created_at DESC", (collection_id,))
        return jsonify({"members": members})
    finally:
        conn.close()


@dic_bp.route("/api/collections/<collection_id>/team", methods=["POST"])
def api_team_add(collection_id):
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or "").strip()
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400
    if not _require_role(collection_id, "admin"):
        return _forbid("admin")
    role = data.get("role", "viewer")
    tenant_id, _ = _security_context()
    access_id = _hid("dic_access", collection_id, user_id, _now())
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO dic_team_access (access_id, collection_id, user_id, role, tenant_id) VALUES (?,?,?,?,?)",
            (access_id, collection_id, user_id, role, tenant_id),
        )
        conn.commit()
        return jsonify({"access_id": access_id, "user_id": user_id, "role": role})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── API: Documents ───────────────────────────────────────────────────────────

@dic_bp.route("/api/collections/<collection_id>/documents", methods=["GET"])
def api_collection_documents(collection_id):
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        rows = _safe_rows(
            conn,
            "SELECT doc_id, collection_id, filename, title, content_type, provider, "
            "page_count, content_sha256, created_at, classification "
            "FROM dic_documents WHERE collection_id = ? AND tenant_id = ? ORDER BY created_at DESC",
            (collection_id, tenant_id),
        )
        # Augment with latest version status and chunk count
        for r in rows:
            try:
                ver = conn.execute(
                    "SELECT version_id, status, origin, version_no FROM dic_versions "
                    "WHERE doc_id = ? ORDER BY version_no DESC LIMIT 1",
                    (r["doc_id"],),
                ).fetchone()
                if ver:
                    r["latest_version_id"] = ver[0] if hasattr(ver, "__getitem__") else ver["version_id"]
                    r["latest_status"] = ver[1] if hasattr(ver, "__getitem__") else ver["status"]
                    r["latest_origin"] = ver[2] if hasattr(ver, "__getitem__") else ver["origin"]
                    r["version_count"] = conn.execute(
                        "SELECT COUNT(*) FROM dic_versions WHERE doc_id = ?",
                        (r["doc_id"],),
                    ).fetchone()[0]
                else:
                    r["latest_version_id"] = ""
                    r["latest_status"] = ""
                    r["latest_origin"] = ""
                    r["version_count"] = 0
            except Exception:
                r["latest_version_id"] = ""
                r["latest_status"] = ""
                r["latest_origin"] = ""
                r["version_count"] = 0
            try:
                chunk_row = conn.execute(
                    "SELECT COUNT(*) FROM dic_chunk_links WHERE doc_id = ?",
                    (r["doc_id"],),
                ).fetchone()
                r["chunk_count"] = chunk_row[0] if chunk_row else 0
            except Exception:
                r["chunk_count"] = 0
        return jsonify({"documents": rows, "collection_id": collection_id})
    finally:
        conn.close()


@dic_bp.route("/api/documents/<doc_id>/versions", methods=["GET"])
def api_document_versions(doc_id):
    conn = _conn()
    try:
        versions = _safe_rows(
            conn,
            "SELECT version_id, version_no, origin, status, assigned_to, "
            "created_at, created_by, content_sha256 "
            "FROM dic_versions WHERE doc_id = ? ORDER BY version_no DESC",
            (doc_id,),
        )
        return jsonify({"doc_id": doc_id, "versions": versions})
    finally:
        conn.close()


# ── API: Review (HITL) ────────────────────────────────────────────────────────

def _record_review_note(item_id: str, item_type: str, note_text: str, reviewer_id: str) -> None:
    """Persist a review note (best-effort)."""
    try:
        conn = _conn()
        note_id = f"note_{hashlib.sha256(f'{item_id}:{_now()}'.encode()).hexdigest()[:16]}"
        conn.execute(
            "INSERT INTO dic_review_notes (note_id, item_id, item_type, note_text, reviewer_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (note_id, item_id, item_type, note_text, reviewer_id, _now()),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


@dic_bp.route("/api/review/<item_id>/assign", methods=["POST"])
def api_review_assign(item_id):
    data = request.get_json(silent=True) or {}
    assigned_to = (data.get("assigned_to") or "").strip()
    item_type = data.get("type", "version")
    if not assigned_to:
        return jsonify({"error": "assigned_to is required"}), 400
    # Resolve collection for role check.
    if item_type == "version":
        cid = _collection_id_from_version(item_id) or "default"
    elif item_type == "section":
        cid = _collection_id_from_section(item_id) or "default"
    else:
        cid = "default"
    if not _require_role(cid, "editor"):
        return _forbid("editor")
    conn = _conn()
    try:
        table = "dic_versions" if item_type == "version" else "dic_ssp_fragments"
        pk = "version_id" if item_type == "version" else "fragment_id"
        conn.execute(f"UPDATE {table} SET assigned_to = ? WHERE {pk} = ?", (assigned_to, item_id))  # nosec B608 — table/pk from ternary constants, not user input
        conn.commit()
        return jsonify({"status": "assigned", "item_id": item_id, "assigned_to": assigned_to})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/review/<item_id>/revise", methods=["POST"])
def api_review_revise(item_id):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer", _current_user())
    item_type = data.get("type", "fragment")
    note = (data.get("note") or "").strip()
    if item_type == "version":
        cid = _collection_id_from_version(item_id) or "default"
    elif item_type == "section":
        cid = _collection_id_from_section(item_id) or "default"
    else:
        cid = "default"
    if not _require_role(cid, "reviewer"):
        return _forbid("reviewer")
    conn = _conn()
    try:
        if item_type == "version":
            conn.execute(
                "UPDATE dic_versions SET status='needs_revision', review_notes=? WHERE version_id=?",
                (note, item_id),
            )
        else:
            try:
                from tools.document_intelligence.acoic import request_revision
                request_revision(item_id, reviewed_by=reviewer)
            except Exception:
                conn.execute(
                    "UPDATE dic_ssp_fragments SET status='needs_revision', reviewed_by=?, reviewed_at=? WHERE fragment_id=?",
                    (reviewer, _now(), item_id),
                )
        conn.commit()
        if note:
            _record_review_note(item_id, item_type, note, reviewer)
        return jsonify({"status": "needs_revision", "item_id": item_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/review/<item_id>/approve", methods=["POST"])
def api_review_approve(item_id):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer", _current_user())
    item_type = data.get("type", "fragment")
    note = (data.get("note") or "").strip()
    if item_type == "version":
        cid = _collection_id_from_version(item_id) or "default"
    elif item_type == "section":
        cid = _collection_id_from_section(item_id) or "default"
    else:
        cid = "default"
    if not _require_role(cid, "reviewer"):
        return _forbid("reviewer")
    conn = _conn()
    try:
        if item_type == "version":
            conn.execute(
                "UPDATE dic_versions SET status='approved' WHERE version_id=?", (item_id,)
            )
        else:
            # Try ACOIC approve helper first.
            try:
                from tools.document_intelligence.acoic import approve_fragment
                approve_fragment(item_id, reviewed_by=reviewer)
            except Exception:
                conn.execute(
                    "UPDATE dic_ssp_fragments SET status='approved', reviewed_by=?, reviewed_at=? WHERE fragment_id=?",
                    (reviewer, _now(), item_id),
                )
        conn.commit()
        if note:
            _record_review_note(item_id, item_type, note, reviewer)
        return jsonify({"status": "approved", "item_id": item_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/review/<item_id>/reject", methods=["POST"])
def api_review_reject(item_id):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer", _current_user())
    item_type = data.get("type", "fragment")
    note = (data.get("note") or "").strip()
    if item_type == "version":
        cid = _collection_id_from_version(item_id) or "default"
    elif item_type == "section":
        cid = _collection_id_from_section(item_id) or "default"
    else:
        cid = "default"
    if not _require_role(cid, "reviewer"):
        return _forbid("reviewer")
    conn = _conn()
    try:
        if item_type == "version":
            conn.execute(
                "UPDATE dic_versions SET status='rejected' WHERE version_id=?", (item_id,)
            )
        else:
            try:
                from tools.document_intelligence.acoic import reject_fragment
                reject_fragment(item_id, reviewed_by=reviewer)
            except Exception:
                conn.execute(
                    "UPDATE dic_ssp_fragments SET status='rejected', reviewed_by=?, reviewed_at=? WHERE fragment_id=?",
                    (reviewer, _now(), item_id),
                )
        conn.commit()
        if note:
            _record_review_note(item_id, item_type, note, reviewer)
        return jsonify({"status": "rejected", "item_id": item_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── Section Annotations (threaded comments anchored to text / sections) ──────

_ANN_CATEGORIES = {"question", "improvement", "compliance", "strength", "weakness", "risk", "editorial"}


def _ensure_dic_annotations(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dic_section_annotations (
            ann_id TEXT PRIMARY KEY,
            section_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            selected_text TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL,
            comment TEXT NOT NULL,
            author TEXT NOT NULL DEFAULT 'reviewer',
            status TEXT NOT NULL DEFAULT 'open',
            resolution_note TEXT,
            resolved_by TEXT,
            resolved_at TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    conn.commit()


@dic_bp.route("/api/sections/<section_id>/annotations", methods=["GET"])
def api_section_annotations_list(section_id: str):
    conn = _conn()
    try:
        _ensure_dic_annotations(conn)
        status_filter = request.args.get("status")
        category_filter = request.args.get("category")
        sql = "SELECT * FROM dic_section_annotations WHERE section_id = ?"
        params: list = [section_id]
        if status_filter:
            sql += " AND status = ?"
            params.append(status_filter)
        if category_filter:
            sql += " AND category = ?"
            params.append(category_filter)
        sql += " ORDER BY created_at ASC"
        rows = _safe_rows(conn, sql, params)
        return jsonify({"annotations": [dict(r) for r in rows]})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/sections/<section_id>/annotations", methods=["POST"])
def api_section_annotations_create(section_id: str):
    data = request.get_json(silent=True) or {}
    category = (data.get("category") or "").strip()
    comment = (data.get("comment") or "").strip()
    if category not in _ANN_CATEGORIES:
        return jsonify({"error": f"category must be one of {sorted(_ANN_CATEGORIES)}"}), 400
    if not comment:
        return jsonify({"error": "comment is required"}), 400
    conn = _conn()
    try:
        _ensure_dic_annotations(conn)
        doc_row = conn.execute(
            "SELECT doc_id FROM dic_sections WHERE section_id = ? LIMIT 1", (section_id,)
        ).fetchone()
        doc_id = doc_row[0] if doc_row else ""
        ann_id = f"ann_{uuid.uuid4().hex[:16]}"
        now = _now()
        author = data.get("author") or _current_user()
        conn.execute(
            """INSERT INTO dic_section_annotations
               (ann_id, section_id, doc_id, selected_text, category, comment,
                author, status, classification, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (ann_id, section_id, doc_id,
             (data.get("selected_text") or "").strip(),
             category, comment, author, "open", "CUI", now, now),
        )
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM dic_section_annotations WHERE ann_id = ?", (ann_id,)
        ).fetchone())
        return jsonify(row), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/annotations/<ann_id>", methods=["PUT"])
def api_annotation_update(ann_id: str):
    data = request.get_json(silent=True) or {}
    allowed = {"comment", "category", "status", "resolution_note", "resolved_by"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "no valid fields"}), 400
    now = _now()
    updates["updated_at"] = now
    if updates.get("status") == "resolved":
        updates["resolved_by"] = data.get("resolved_by") or data.get("author") or _current_user()
        updates["resolved_at"] = now
    conn = _conn()
    try:
        _ensure_dic_annotations(conn)
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE dic_section_annotations SET {set_clause} WHERE ann_id = ?",
            [*updates.values(), ann_id],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM dic_section_annotations WHERE ann_id = ?", (ann_id,)
        ).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        return jsonify(dict(row))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/annotations/<ann_id>", methods=["DELETE"])
def api_annotation_delete(ann_id: str):
    conn = _conn()
    try:
        _ensure_dic_annotations(conn)
        conn.execute("DELETE FROM dic_section_annotations WHERE ann_id = ?", (ann_id,))
        conn.commit()
        return jsonify({"deleted": ann_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── API: Section Review (per-section accept/reject/revise) ─────────────────

@dic_bp.route("/api/sections/<section_id>/approve", methods=["POST"])
def api_section_approve(section_id):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer", _current_user())
    note = (data.get("note") or "").strip()
    cid = _collection_id_from_section(section_id) or "default"
    if not _require_role(cid, "reviewer"):
        return _forbid("reviewer")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE dic_sections SET status='approved', reviewed_by=?, reviewed_at=? WHERE section_id=?",
            (reviewer, _now(), section_id),
        )
        conn.commit()
        if note:
            _record_review_note(section_id, "section", note, reviewer)
        return jsonify({"status": "approved", "section_id": section_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/sections/<section_id>/reject", methods=["POST"])
def api_section_reject(section_id):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer", _current_user())
    note = (data.get("note") or "").strip()
    cid = _collection_id_from_section(section_id) or "default"
    if not _require_role(cid, "reviewer"):
        return _forbid("reviewer")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE dic_sections SET status='rejected', reviewed_by=?, reviewed_at=? WHERE section_id=?",
            (reviewer, _now(), section_id),
        )
        conn.commit()
        if note:
            _record_review_note(section_id, "section", note, reviewer)
        return jsonify({"status": "rejected", "section_id": section_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/sections/<section_id>/revise", methods=["POST"])
def api_section_revise(section_id):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer", _current_user())
    note = (data.get("note") or "").strip()
    cid = _collection_id_from_section(section_id) or "default"
    if not _require_role(cid, "reviewer"):
        return _forbid("reviewer")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE dic_sections SET status='needs_revision', reviewed_by=?, reviewed_at=? WHERE section_id=?",
            (reviewer, _now(), section_id),
        )
        conn.commit()
        if note:
            _record_review_note(section_id, "section", note, reviewer)
        return jsonify({"status": "needs_revision", "section_id": section_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/sections/<section_id>/content", methods=["POST"])
def api_section_update_content(section_id):
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    cid = _collection_id_from_section(section_id) or "default"
    if not _require_role(cid, "editor"):
        return _forbid("editor")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE dic_sections SET content = ?, status = ?, origin = ?, created_at = ? WHERE section_id = ?",
            (content, "draft", "human_authored", _now(), section_id),
        )
        conn.commit()
        return jsonify({"status": "updated", "section_id": section_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── API: Template Instantiation ─────────────────────────────────────────────

_TEMPLATE_SECTIONS: dict[str, list[str]] = {
    "acoic": ["Impact Assessment", "Affected Controls", "Regeneration Plan", "SSP Fragment", "Approval Gate"],
    "freshness-audit": ["Audit Scope", "Stale Document Inventory", "Remediation Plan", "Owner Assignments", "Timeline"],
    "airgap-ingest": ["Source Directory", "Ingest Pipeline Steps", "Verification Checklist", "Rollback Plan"],
    "hitl-review": ["Review Queue", "Reviewer Assignments", "Acceptance Criteria", "Escalation Path"],
    "sop-refresh": ["Current Procedure", "Change Summary", "Updated Steps", "Validation Criteria", "Rollback Plan"],
    "knowledge-handoff": ["SME Profile", "Knowledge Areas", "Interview Agenda", "Captured Artifacts", "Successor Onboarding"],
}


@dic_bp.route("/api/templates/<template_id>/instantiate", methods=["POST"])
def api_template_instantiate(template_id):
    data = request.get_json(silent=True) or {}
    collection_id = data.get("collection_id", "default")
    if not _require_role(collection_id, "editor"):
        return _forbid("editor")
    tenant_id, classification = _security_context()
    created_by = _current_user()

    template_meta = next((t for t in _TEMPLATES if t["id"] == template_id), None)
    if not template_meta:
        return jsonify({"error": "template not found"}), 404

    sections = _TEMPLATE_SECTIONS.get(template_id, ["Overview"])
    now = _now()
    doc_id = _hid("dic_tpl", template_id, collection_id, now)
    version_id = f"{doc_id}_v1"

    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO dic_documents
                (doc_id, collection_id, source_id, filename, filepath,
                 content_type, provider, title, byte_size, content_sha256,
                 page_count, created_at, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id, collection_id, doc_id, f"{template_id}-template.md", "",
                "text/markdown", "template", template_meta["name"], 0, "",
                len(sections), now, tenant_id, classification,
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO dic_versions
                (version_id, doc_id, version_no, origin, status,
                 content_sha256, created_at, created_by, tenant_id, classification)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                version_id, doc_id, 1, "template", "draft",
                "", now, created_by, tenant_id, classification,
            ),
        )
        for i, heading in enumerate(sections):
            section_id = f"{version_id}_sec_{i}"
            cur.execute(
                """
                INSERT OR REPLACE INTO dic_sections
                    (section_id, version_id, doc_id, heading, content,
                     citations_json, status, origin, created_at, created_by, tenant_id, classification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    section_id, version_id, doc_id, heading, "",
                    "[]", "draft", "template", now, created_by, tenant_id, classification,
                ),
            )
        conn.commit()
        return jsonify({
            "doc_id": doc_id,
            "version_id": version_id,
            "template_id": template_id,
            "title": template_meta["name"],
            "sections": sections,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── API: Generate ─────────────────────────────────────────────────────────────

@dic_bp.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    collection_id = data.get("collection_id", "default")
    if not _require_role(collection_id, "editor"):
        return _forbid("editor")
    template_id = data.get("template_id")
    tenant_id, classification = _security_context()

    try:
        from tools.document_intelligence.doc_generator import generate_document
        result = generate_document(
            query,
            collection_id,
            template_id=template_id,
            tenant_id=tenant_id,
            classification=classification,
        )
        return jsonify(result.to_dict())
    except Exception as exc:
        logger.warning("dic: generate error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/generate/section", methods=["POST"])
def api_generate_section():
    """Regenerate a single section with targeted evidence retrieval."""
    data = request.get_json(silent=True) or {}
    version_id = (data.get("version_id") or "").strip()
    heading = (data.get("heading") or "").strip()
    collection_id = data.get("collection_id", "default")
    if not _require_role(collection_id, "editor"):
        return _forbid("editor")
    if not version_id or not heading:
        return jsonify({"error": "version_id and heading are required"}), 400
    tenant_id, classification = _security_context()
    try:
        from tools.document_intelligence.doc_generator import regenerate_section
        result = regenerate_section(
            version_id, heading, collection_id,
            tenant_id=tenant_id, classification=classification,
        )
        return jsonify(result)
    except Exception as exc:
        logger.warning("dic: generate-section error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/versions/<version_id>/sections", methods=["GET"])
def api_version_sections(version_id):
    """Return sections for a document version."""
    conn = _conn()
    try:
        rows = _safe_rows(
            conn,
            "SELECT section_id, heading, content, citations_json, status, origin "
            "FROM dic_sections WHERE version_id = ? ORDER BY rowid",
            (version_id,),
        )
        for r in rows:
            try:
                r["citations"] = json.loads(r.get("citations_json") or "[]")
            except Exception:
                r["citations"] = []
        return jsonify({"sections": rows})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── API: Handoff ──────────────────────────────────────────────────────────────

@dic_bp.route("/api/handoff/start", methods=["POST"])
def api_handoff_start():
    data = request.get_json(silent=True) or {}
    departing = (data.get("departing_owner_id") or "").strip()
    successor = (data.get("successor_owner_id") or "").strip()
    dest = data.get("dest_collection_id", "default")
    if not departing or not successor:
        return jsonify({"error": "departing_owner_id and successor_owner_id are required"}), 400
    if not _require_role(dest, "admin"):
        return _forbid("admin")
    tenant_id, classification = _security_context()
    created_by = _current_user()
    try:
        from tools.document_intelligence.handoff import start_session
        session = start_session(
            departing, successor, dest,
            tenant_id=tenant_id, classification=classification, created_by=created_by,
        )
        return jsonify({
            "session_id": session.session_id,
            "title": session.title,
            "agenda_count": session.agenda_count,
            "orphan_count": session.orphan_count,
        })
    except Exception as exc:
        logger.warning("dic: handoff start error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/handoff/<session_id>/close", methods=["POST"])
def api_handoff_close(session_id):
    try:
        from tools.document_intelligence.handoff import close_session
        result = close_session(session_id, closed_by=_current_user())
        return jsonify(result)
    except Exception as exc:
        logger.warning("dic: handoff close error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/handoff/<item_id>/answer", methods=["POST"])
def api_handoff_answer(item_id):
    data = request.get_json(silent=True) or {}
    answer = (data.get("answer_text") or "").strip()
    if not answer:
        return jsonify({"error": "answer_text is required"}), 400
    try:
        from tools.document_intelligence.handoff import answer_item
        result = answer_item(item_id, answer, reviewed_by=_current_user())
        return jsonify(result)
    except Exception as exc:
        logger.warning("dic: handoff answer error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── API: Freshness ───────────────────────────────────────────────────────────

@dic_bp.route("/api/freshness/scan", methods=["POST"])
def api_freshness_scan():
    data = request.get_json(silent=True) or {}
    collection_id = data.get("collection_id", "default")
    tenant_id, classification = _security_context()
    try:
        from tools.document_intelligence.freshness_engine import scan_collection
        result = scan_collection(collection_id, tenant_id=tenant_id, classification=classification)
        return jsonify({
            "scan_id": result.scan_id,
            "collection_id": result.collection_id,
            "stale_count": result.stale_count,
            "aging_count": result.aging_count,
            "fresh_count": result.fresh_count,
            "regen_priority": result.regen_priority,
            "docs_scanned": len(result.docs),
        })
    except Exception as exc:
        logger.warning("dic: freshness scan error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/freshness/heatmap", methods=["GET"])
def api_freshness_heatmap():
    tenant_id, _ = _security_context()
    try:
        from tools.document_intelligence.freshness_engine import corpus_heatmap
        rows = corpus_heatmap(tenant_id=tenant_id, limit=200)
        return jsonify({"heatmap": rows, "count": len(rows)})
    except Exception as exc:
        logger.warning("dic: freshness heatmap error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── API: Explorer ────────────────────────────────────────────────────────────

@dic_bp.route("/api/explorer/refresh", methods=["POST"])
def api_explorer_refresh():
    tenant_id, _ = _security_context()
    try:
        from tools.document_intelligence.explorer import run_explorer
        findings = run_explorer(tenant_id=tenant_id, limit=100)
        return jsonify({"findings": findings, "count": len(findings)})
    except Exception as exc:
        logger.warning("dic: explorer refresh error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── Analytics Page + API ─────────────────────────────────────────────────────

@dic_bp.route("/analytics")
def analytics():
    return render_template("document_intelligence/analytics.html")


@dic_bp.route("/api/analytics", methods=["POST"])
def api_analytics():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "full")
    try:
        from tools.document_intelligence.analytics_engine import (
            entity_frequency, co_occurrence, detect_anomalies,
            detect_patterns, run_full_analytics,
        )
        if mode == "frequency":
            return jsonify(entity_frequency(limit=int(data.get("limit", 50))))
        elif mode == "cooccurrence":
            return jsonify(co_occurrence(limit=int(data.get("limit", 60))))
        elif mode == "anomalies":
            return jsonify(detect_anomalies())
        elif mode == "patterns":
            return jsonify(detect_patterns())
        else:
            return jsonify(run_full_analytics())
    except Exception as exc:
        logger.warning("dic: analytics error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/scenarios", methods=["POST"])
def api_scenarios():
    data = request.get_json(silent=True) or {}
    scenario_type = (data.get("scenario_type") or "remove_entity").strip()
    entity_label = (data.get("entity_label") or "").strip()
    params = data.get("params", {})
    try:
        from tools.document_intelligence.analytics_engine import run_scenario
        result = run_scenario(scenario_type, entity_label=entity_label, params=params)
        return jsonify(result)
    except Exception as exc:
        logger.warning("dic: scenario error: %s", exc)
        return jsonify({"error": str(exc)}), 500


# ── API: IQE ──────────────────────────────────────────────────────────────────

@dic_bp.route("/api/iqe-query", methods=["POST"])
def iqe_query():
    try:
        from tools.iqe.adapters import dic as _  # noqa: F401  registers collections
        from tools.iqe.executor import Executor

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400

        executor = Executor()
        result = executor.execute(question)
        return jsonify(result)
    except Exception as exc:
        logger.warning("dic: iqe-query error: %s", exc)
        return jsonify({"error": str(exc)}), 500
