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
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

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
            "status, created_at FROM dic_ssp_fragments WHERE status = 'pending_review' "
            "ORDER BY created_at DESC LIMIT 50",
        )
        pending_versions = _safe_rows(
            conn,
            "SELECT v.version_id, v.doc_id, v.version_no, v.origin, v.status, "
            "v.created_at, v.created_by, d.title as doc_title "
            "FROM dic_versions v LEFT JOIN dic_documents d ON d.doc_id = v.doc_id "
            "WHERE v.status = 'pending_review' ORDER BY v.created_at DESC LIMIT 50",
        )
    finally:
        conn.close()
    return render_template(
        "document_intelligence/review.html",
        pending_fragments=pending_fragments,
        pending_versions=pending_versions,
    )


@dic_bp.route("/generate")
def generate():
    return render_template("document_intelligence/generate.html", templates=_TEMPLATES)


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

    suffix = Path(file.filename).suffix.lower()
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            file.save(tmp)
            tmp_path = tmp.name

        from tools.document_intelligence.ingest_orchestrator import ingest_file
        outcome = ingest_file(
            tmp_path,
            collection_id,
            tenant_id=tenant_id,
            classification=classification,
            created_by="dashboard_upload",
        )
        return jsonify({
            "status": "ok",
            "doc_id": outcome.doc_id,
            "chunks": outcome.chunks,
            "collection_id": collection_id,
            "errors": outcome.errors,
        })
    except Exception as exc:
        logger.warning("dic: ingest error: %s", exc)
        return jsonify({"error": str(exc)}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


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

@dic_bp.route("/api/review/<item_id>/approve", methods=["POST"])
def api_review_approve(item_id):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer", "current_user")
    item_type = data.get("type", "fragment")
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
                approve_fragment(item_id, reviewer_id=reviewer, conn=conn)
            except Exception:
                conn.execute(
                    "UPDATE dic_ssp_fragments SET status='approved', reviewed_by=?, reviewed_at=? WHERE fragment_id=?",
                    (reviewer, _now(), item_id),
                )
        conn.commit()
        return jsonify({"status": "approved", "item_id": item_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/review/<item_id>/reject", methods=["POST"])
def api_review_reject(item_id):
    data = request.get_json(silent=True) or {}
    reviewer = data.get("reviewer", "current_user")
    item_type = data.get("type", "fragment")
    conn = _conn()
    try:
        if item_type == "version":
            conn.execute(
                "UPDATE dic_versions SET status='rejected' WHERE version_id=?", (item_id,)
            )
        else:
            try:
                from tools.document_intelligence.acoic import reject_fragment
                reject_fragment(item_id, reviewer_id=reviewer, conn=conn)
            except Exception:
                conn.execute(
                    "UPDATE dic_ssp_fragments SET status='rejected', reviewed_by=?, reviewed_at=? WHERE fragment_id=?",
                    (reviewer, _now(), item_id),
                )
        conn.commit()
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
