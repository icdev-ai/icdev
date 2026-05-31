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

    # Augment versions/fragments with latest note and current-user role.
    current_user = _current_user()
    for v in pending_versions:
        v["latest_note"] = notes_map.get(v["version_id"], "")
        v["user_role"] = _user_role(v.get("collection_id") or "default", current_user)
    for f in pending_fragments:
        f["latest_note"] = notes_map.get(f["fragment_id"], "")
        f["user_role"] = _user_role("default", current_user)

    return render_template(
        "document_intelligence/review.html",
        pending_fragments=pending_fragments,
        pending_versions=pending_versions,
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
    tenant_id, _ = _security_context()

    try:
        from tools.document_intelligence.search_engine import DICSearchEngine
        engine = DICSearchEngine(tenant_id=tenant_id)
        results = engine.search(query, collection_id=collection_id, top_k=top_k, mode=mode)
        return jsonify({"results": [r.to_dict() for r in results], "count": len(results)})
    except Exception as exc:
        logger.warning("dic: search error: %s", exc)
        return jsonify({"results": [], "error": str(exc)}), 500


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
    conn = _conn()
    try:
        table = "dic_versions" if item_type == "version" else "dic_ssp_fragments"
        pk = "version_id" if item_type == "version" else "fragment_id"
        conn.execute(f"UPDATE {table} SET assigned_to = ? WHERE {pk} = ?", (assigned_to, item_id))
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


# ── API: Generate ─────────────────────────────────────────────────────────────

@dic_bp.route("/api/generate", methods=["POST"])
def api_generate():
    data = request.get_json(silent=True) or {}
    query = (data.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query is required"}), 400
    collection_id = data.get("collection_id", "default")
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
