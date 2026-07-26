# CUI // SP-CTI
"""Document Intelligence Canvas — Flask Blueprint.

Routes:
  GET  /document-intelligence/              index (upload + canvas overview)
  GET  /document-intelligence/collections   collections management + team access
  GET  /document-intelligence/search        grounded search + document chat
  GET  /document-intelligence/review        HITL review queue (fragments + versions)
  GET  /document-intelligence/generate      AI-assisted document generation
  GET  /document-intelligence/docdrift      DocDrift drift→regen→NIST page (was /acoic)
  GET  /document-intelligence/templates     use-case templates page

  POST /document-intelligence/api/ingest                         multi-modal upload
  POST /document-intelligence/api/search                         grounded search JSON (opt-in: classify_intent, expand, keywords, explain_access)
  POST /document-intelligence/api/chat                           document chat JSON
  GET  /document-intelligence/api/collections                    list collections
  POST /document-intelligence/api/collections                    create collection
  GET  /document-intelligence/api/collections/<id>/team          list team members
  POST /document-intelligence/api/collections/<id>/team          add team member
  POST /document-intelligence/api/documents/<id>/re-enrich       re-run LLM metadata extraction on ingested doc
  POST /document-intelligence/api/review/<id>/approve            approve fragment/version
  POST /document-intelligence/api/review/<id>/reject             reject fragment/version
  POST /document-intelligence/api/generate                       AI draft generation
  POST /document-intelligence/api/iqe-query                      IQE natural-language query

  GET  /document-intelligence/api/suggestions                    list suggestions (dsyn-adapt-04)
  GET  /document-intelligence/api/suggestions/<id>               suggestion detail
  POST /document-intelligence/api/suggestions/<id>/accept        accept: apply content + history
  POST /document-intelligence/api/suggestions/<id>/reject        reject: mark decided + note

  GET  /document-intelligence/techwriter                          tech writer workspace (migration 230)
  PATCH /document-intelligence/api/documents/<id>/writeguard-mode update writeguard content mode
  POST /document-intelligence/api/techwriter/research            AI research + draft for a section
  POST /document-intelligence/api/techwriter/diagram             generate Mermaid diagram syntax
  POST /document-intelligence/api/import-from-docgen            import docgen session → new tech writer doc
"""
from __future__ import annotations

import hashlib
import json
import os
import queue as _queue
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    stream_with_context,
    url_for,
)

from tools.document_intelligence.collection_registry import ensure_collection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── In-memory SSE job queues ──────────────────────────────────────────────────
# Maps job_id → queue.Queue[dict | None]  (None = sentinel / stream closed)
_JOB_QUEUES: dict[str, _queue.Queue] = {}
# Maps job_id → {status, doc_id, chunks, errors} — in-memory result cache backing
# the SSE result endpoint.
#
# This used to say the cache existed "even when the dic_ingest_jobs DB INSERT
# fails (e.g. wrong SQL parameter style on PostgreSQL)". That INSERT uses %s now;
# the bug was fixed and the comment outlived it, advertising a workaround for a
# failure that can no longer happen.
_JOB_RESULTS: dict[str, dict] = {}
_JOB_LOCK = threading.Lock()

# Override to a positive int in tests to make presence SSE streams finite.
_STREAM_MAX_POLLS: int | None = None

dic_bp = Blueprint(
    "dic",
    __name__,
    url_prefix="/document-intelligence",
    template_folder="../../tools/dashboard/templates",
)

# ── Static seed data ──────────────────────────────────────────────────────────

_TEMPLATES = [
    # id stays "acoic": it is a data key behind /api/templates/<id>/instantiate,
    # exercised by features/dic_document_intelligence.feature and e2e_full.py.
    # Renaming it would break those contracts for a string nobody sees.
    {"id": "acoic", "name": "DocDrift", "description": "Drift → impacted-doc regeneration → RICOAS NIST bridge.", "flagship": True, "category": "compliance", "kind": "automation"},
    {"id": "freshness-audit", "name": "Document Freshness Audit", "description": "Scan a collection for stale documents and generate a remediation report.", "flagship": False, "category": "quality", "kind": "audit"},
    {"id": "airgap-ingest", "name": "Air-Gap Ingest Pipeline", "description": "Ingest documents from a local directory with zero cloud calls.", "flagship": False, "category": "ingest", "kind": "pipeline"},
    {"id": "hitl-review", "name": "HITL Review Queue", "description": "Surface AI-generated drafts for human review before publishing.", "flagship": False, "category": "governance", "kind": "workflow"},
    {"id": "sop-refresh", "name": "SOP Refresh", "description": "Keep standard operating procedures current against process changes.", "flagship": False, "category": "operations", "kind": "workflow"},
    {"id": "knowledge-handoff", "name": "Knowledge Handoff", "description": "Capture retiring-SME knowledge into a living collection via CoD-verified generation.", "flagship": False, "category": "knowledge", "kind": "workflow"},
    # Tech Writer templates (migration 230) — category="techwriter", status='approved' on instantiate
    {"id": "STANDARD_GUIDE", "name": "Standard Guide", "description": "Cloud-agnostic reference guide spanning multiple providers (AWS/Azure/GCP/Oracle).", "flagship": False, "category": "techwriter", "kind": "guide"},
    {"id": "SOP", "name": "SOP", "description": "Standard Operating Procedure with numbered steps, prerequisites, and rollback.", "flagship": False, "category": "techwriter", "kind": "sop"},
    {"id": "RUNBOOK", "name": "Runbook", "description": "Operational runbook with pre-flight checks, procedure, verification, and escalation path.", "flagship": False, "category": "techwriter", "kind": "runbook"},
    {"id": "ARCH_NETWORK", "name": "Network Architecture", "description": "Network topology, segmentation strategy, traffic flows, and security controls.", "flagship": False, "category": "techwriter", "kind": "architecture"},
    {"id": "ARCH_APPLICATION", "name": "Application Architecture", "description": "System context, component diagram, API contracts, data flow, and deployment architecture.", "flagship": False, "category": "techwriter", "kind": "architecture"},
    {"id": "ARCH_SYSTEM", "name": "System Architecture", "description": "End-to-end system boundary, stakeholders, interfaces, and quality attributes.", "flagship": False, "category": "techwriter", "kind": "architecture"},
]

_PAGES = [
    {"name": "Collections", "icon": "🗂️", "href": "/document-intelligence/collections", "desc": "Organize documents into collections and manage team access.", "ready": True, "task": "dic-collab-01"},
    {"name": "Search & Chat", "icon": "🔍", "href": "/document-intelligence/search", "desc": "Grounded no-LLM search with mandatory citations · Conversational AI.", "ready": True, "task": "dic-search-01"},
    {"name": "Analytics", "icon": "📊", "href": "/document-intelligence/analytics", "desc": "Entity frequency, co-occurrence, pattern detection, anomaly detection, and scenario runner.", "ready": True, "task": "dic-analytics-01"},
    {"name": "HITL Review", "icon": "👁️", "href": "/document-intelligence/review", "desc": "Human-in-the-loop oversight for AI-generated drafts and SSP fragments.", "ready": True, "task": "dic-collab-01"},
    {"name": "AI-Assist", "icon": "✨", "href": "/document-intelligence/generate", "desc": "Generate CoD-verified document drafts from your collections.", "ready": True, "task": "dic-generate-01"},
    {"name": "DocDrift", "icon": "🛰️", "href": "/document-intelligence/docdrift", "desc": "Is this document still true? Drift → impact → regen → NIST re-map.", "ready": True, "task": "dic-acoic-01"},
    {"name": "Templates", "icon": "📐", "href": "/document-intelligence/templates", "desc": "Pre-built document workflows. DocDrift is the flagship.", "ready": True, "task": "dic-templates-01"},
    {"name": "Freshness", "icon": "🌡️", "href": "/document-intelligence/freshness", "desc": "Corpus staleness heatmap and remediation queue.", "ready": True, "task": "dic-freshness-01"},
    {"name": "Explorer", "icon": "🔎", "href": "/document-intelligence/explorer", "desc": "KG buried-bodies explorer — orphans, tribal knowledge, contradictions.", "ready": True, "task": "dic-explore-01"},
    {"name": "Handoff", "icon": "🤝", "href": "/document-intelligence/handoff", "desc": "Knowledge handoff — capture retiring SME knowledge into a living collection.", "ready": True, "task": "dic-handoff-01"},
    {"name": "Notebook", "icon": "📓", "href": "/document-intelligence/notebook", "desc": "NotebookLM-style view — sources, chat, and AI outputs (study guide, FAQ, timeline, audio) in one screen.", "ready": True, "task": "dic-notebook-01"},
    {"name": "Tech Writer", "icon": "✍️", "href": "/document-intelligence/techwriter", "desc": "Author arch docs, SOPs, runbooks, and standard guides with inline WriteGuard and AI research.", "ready": True, "task": "dic-techwriter-01"},
]

# Workflow grouping for the canvas index. 14 undifferentiated sibling tiles give
# no clue what to do first; these order them by the sequence a user actually
# follows. Presentation only — no tile is removed, and _PAGES stays the flat
# source of truth for other consumers.
_PAGE_GROUPS: list[tuple[str, str, list[str]]] = [
    ("1 · Ingest & organize",
     "Get documents in and shape them into collections.",
     ["Collections", "Notebook", "Handoff"]),
    ("2 · Explore & search",
     "Ask questions of what you already have — grounded, with citations.",
     ["Search & Chat", "Explorer", "Analytics"]),
    ("3 · Author & generate",
     "Write new documents, or rebuild existing ones.",
     ["Tech Writer", "AI-Assist", "Templates"]),
    ("4 · Govern & review",
     "Approve AI output, track staleness, keep compliance in sync.",
     ["HITL Review", "Freshness", "DocDrift"]),
]


def _grouped_pages() -> list[dict]:
    """Partition _PAGES into the workflow groups above.

    Any tile missing from _PAGE_GROUPS is appended to a trailing group rather
    than silently dropped — a typo must never make a feature vanish from the UI.
    """
    by_name = {p["name"]: p for p in _PAGES}
    groups: list[dict] = []
    claimed: set[str] = set()
    for label, desc, names in _PAGE_GROUPS:
        pages = [by_name[n] for n in names if n in by_name]
        claimed.update(p["name"] for p in pages)
        if pages:
            groups.append({"label": label, "desc": desc, "pages": pages})
    leftover = [p for p in _PAGES if p["name"] not in claimed]
    if leftover:
        groups.append({"label": "More", "desc": "", "pages": leftover})
    return groups



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
    """Look up the user's role in a collection via dic_team_access.

    Falls back to 'admin' for the anonymous local-dev sentinel ('current_user')
    so that unauthenticated single-user dashboards are not blocked by RBAC.
    All other users without an explicit row default to 'viewer'.
    """
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT role FROM dic_team_access WHERE collection_id = %s AND user_id = %s LIMIT 1",
            (collection_id, user_id),
        ).fetchone()
        if row:
            return row[0] if hasattr(row, "__getitem__") else row["role"]
    except Exception:
        pass
    finally:
        conn.close()
    # Anonymous local-dev sentinel → full access so every DIC feature is usable
    # without requiring a team_access row to be seeded first.
    if user_id in ("current_user", "", None):
        return "admin"
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
        row = conn.execute("SELECT collection_id FROM dic_documents WHERE doc_id = %s LIMIT 1", (doc_id,)).fetchone()
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
            "SELECT d.collection_id FROM dic_versions v JOIN dic_documents d ON d.doc_id = v.doc_id WHERE v.version_id = %s LIMIT 1",
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
            "SELECT d.collection_id FROM dic_sections s JOIN dic_documents d ON d.doc_id = s.doc_id WHERE s.section_id = %s LIMIT 1",
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
    return render_template(
        "document_intelligence/index.html",
        pages=_PAGES,
        page_groups=_grouped_pages(),
    )


@dic_bp.route("/collections")
def collections():
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        cols = _safe_rows(conn, "SELECT * FROM dic_collections WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 100", (tenant_id,))
        for c in cols:
            try:
                doc_count_row = conn.execute(
                    "SELECT COUNT(*) FROM dic_documents WHERE collection_id = %s", (c["collection_id"],)
                ).fetchone()
                c["doc_count"] = doc_count_row[0] if doc_count_row else 0
            except Exception:
                c["doc_count"] = 0
            try:
                team_row = conn.execute(
                    "SELECT COUNT(*) FROM dic_team_access WHERE collection_id = %s", (c["collection_id"],)
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
        doc = _safe_rows(conn, "SELECT * FROM dic_documents WHERE doc_id = %s LIMIT 1", (doc_id,))
        doc = doc[0] if doc else {}
        versions = _safe_rows(
            conn,
            "SELECT version_id, version_no, origin, status, assigned_to, created_at, created_by "
            "FROM dic_versions WHERE doc_id = %s ORDER BY version_no DESC",
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
                "FROM dic_sections WHERE version_id = %s ORDER BY section_id",
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
            "SELECT user_id, role FROM dic_team_access WHERE collection_id = %s ORDER BY role DESC, user_id",
            (collection_id,),
        )
    finally:
        conn.close()

    current_user = _current_user()
    user_role = _user_role(collection_id, current_user)

    try:
        from tools.document_intelligence.analytics_engine import log_doc_view
        tenant_id, _ = _security_context()
        log_doc_view(doc_id, user_id=current_user or "anonymous",
                     collection_id=collection_id, tenant_id=tenant_id or "default")
    except Exception:
        pass

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
                    "SELECT user_id, role FROM dic_team_access WHERE collection_id = %s ORDER BY role DESC, user_id",
                    (cid,),
                )
        for pd in pending_docs:
            cid = pd.get("collection_id") or "default"
            if cid not in team_map:
                team_map[cid] = _safe_rows(
                    conn,
                    "SELECT user_id, role FROM dic_team_access WHERE collection_id = %s ORDER BY role DESC, user_id",
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
    "acoic": "DocDrift — drift → impacted document regeneration → NIST 800-53 re-map",
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


def _docdrift_topologies() -> list[dict]:
    """Topologies + whether each has a saved baseline, for the DocDrift controls.

    Network drift can only be detected against a baseline, so the picker must
    show which topologies are actually ready. Degrades to [] when the NDC
    database is unreachable — a disabled control beats a 500.
    """
    try:
        from tools.network.db.init_db import get_connection as ndc_conn
        conn = ndc_conn()
        try:
            rows = conn.execute(
                "SELECT t.id, t.name, "
                "  (SELECT COUNT(*) FROM nc_versions v WHERE v.topology_id = t.id) AS versions "
                "FROM topologies t ORDER BY t.name LIMIT 100"
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "id": d["id"],
                "name": d["name"],
                "has_baseline": bool(d.get("versions") or 0),
            }
            for d in (dict(r) for r in rows)
        ]
    except Exception as exc:
        logger.warning("dic: docdrift topology list unavailable: %s", exc)
        return []


@dic_bp.route("/docdrift")
def docdrift():
    # acoic owns these three tables, and acoic.get_acoic_page_context() exists to
    # bundle exactly the three lists this template renders — its docstring even
    # spells out this call. The route re-queried them inline anyway, so the
    # column list lived in two places and could drift from the module that owns
    # the schema.
    #
    # The helper is also strictly safer: its _rows() calls _ensure_schema first,
    # so a fresh database renders instead of relying on _safe_rows swallowing
    # "no such table" into an empty list — the failure mode that had /analytics
    # telling operators they had no documents. It returns supersets of what the
    # inline queries selected (regen adds item_id; fragments add fragment_id,
    # verified, ai_labeled), so the template gains fields and loses none.
    from tools.document_intelligence import acoic

    page = acoic.get_acoic_page_context()
    drift_events = page["drift_events"]
    regen_queue = page["regen_queue"]
    ssp_fragments = page["ssp_fragments"]
    topologies = _docdrift_topologies()
    return render_template(
        "document_intelligence/docdrift.html",
        drift_events=drift_events,
        regen_queue=regen_queue,
        ssp_fragments=ssp_fragments,
        topologies=topologies,
        baselines_saved=sum(1 for t in topologies if t["has_baseline"]),
    )


@dic_bp.route("/acoic")
def acoic_legacy_redirect():
    """The page was called ACOIC until it stopped being network-only.

    Kept as a permanent redirect rather than deleted: the old path is in
    bookmarks, kanban card descriptions (dic-acoic-01/02) and docs, and a 404
    would read as "the feature is gone" rather than "it was renamed".
    """
    return redirect(url_for("dic.docdrift"), code=301)


@dic_bp.route("/api/docdrift/drift-check", methods=["POST"])
def api_docdrift_drift_check():
    """Run the real NDC->DocDrift drift check on demand.

    Same code path as the ndc_topology_drift reflex — this is not a demo or
    seed button. It records nothing unless genuine drift is found, and reports
    baselines_missing when a topology has no saved version to diff against.
    """
    data = request.get_json(silent=True) or {}
    try:
        from tools.genesis.reflexes.ndc_topology_drift import run as _drift_run
        result = _drift_run({
            "dry_run": bool(data.get("dry_run", False)),
            "topology_ids": [t for t in (data.get("topology_ids") or []) if t],
        })
    except Exception as exc:
        logger.warning("dic: docdrift drift-check failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
    return jsonify(result)


@dic_bp.route("/templates")
def templates_page():
    return render_template("document_intelligence/templates.html", templates=_TEMPLATES)


# ── Tech Writer Workspace ─────────────────────────────────────────────────────

@dic_bp.route("/techwriter")
def techwriter():
    """Tech Writer Workspace — template picker + list of active drafts."""
    tenant_id, classification = _security_context()
    current_user = _current_user()
    conn = _conn()
    try:
        # docmod-ux-04: enrich with freshness state, latest-version review
        # status/assignee, last approval date, and pending-suggestion counts.
        active_docs = _safe_rows(
            conn,
            "SELECT d.doc_id, d.title, d.template_type, d.writeguard_mode, d.created_at, "
            "  f.state AS freshness_state, "
            "  (SELECT v.status FROM dic_versions v WHERE v.doc_id = d.doc_id "
            "     ORDER BY v.version_no DESC LIMIT 1) AS review_status, "
            "  (SELECT v.assigned_to FROM dic_versions v WHERE v.doc_id = d.doc_id "
            "     ORDER BY v.version_no DESC LIMIT 1) AS assigned_to, "
            "  (SELECT v.created_at FROM dic_versions v WHERE v.doc_id = d.doc_id "
            "     AND v.status = 'approved' ORDER BY v.version_no DESC LIMIT 1) AS last_approved, "
            "  (SELECT COUNT(*) FROM dic_suggestions sg WHERE sg.doc_id = d.doc_id "
            "     AND sg.status = 'pending') AS pending_suggestions "
            "FROM dic_documents d "
            "LEFT JOIN dic_doc_freshness f ON f.doc_id = d.doc_id "
            "WHERE d.tenant_id = %s AND d.template_type IS NOT NULL "
            "ORDER BY d.created_at DESC LIMIT 50",
            (tenant_id,),
        )
        if not active_docs:
            # Graceful degradation when the join tables don't exist yet.
            active_docs = _safe_rows(
                conn,
                "SELECT doc_id, title, template_type, writeguard_mode, created_at "
                "FROM dic_documents "
                "WHERE tenant_id = %s AND template_type IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 50",
                (tenant_id,),
            )
    finally:
        conn.close()
    from tools.document_intelligence.constants import (
        TEMPLATE_TYPES,
        TEMPLATE_TYPE_TO_WRITEGUARD_MODE,
    )
    tw_templates = [t for t in _TEMPLATES if t.get("category") == "techwriter"]
    return render_template(
        "document_intelligence/techwriter.html",
        active_docs=active_docs,
        template_types=TEMPLATE_TYPES,
        type_to_mode=TEMPLATE_TYPE_TO_WRITEGUARD_MODE,
        tw_templates=tw_templates,
        current_user=current_user,
        pages=_PAGES,
    )


@dic_bp.route("/api/documents/<doc_id>/writeguard-mode", methods=["PATCH"])
def api_set_writeguard_mode(doc_id: str):
    """PATCH — update writeguard_mode on a techwriter document."""
    body = request.get_json(silent=True) or {}
    mode = (body.get("mode") or "default").strip()
    from tools.document_intelligence.constants import WRITEGUARD_MODES
    if mode not in WRITEGUARD_MODES:
        return jsonify({"error": f"invalid mode: {mode}"}), 400
    conn = _conn()
    try:
        conn.execute(
            "UPDATE dic_documents SET writeguard_mode = %s WHERE doc_id = %s",
            (mode, doc_id),
        )
        conn.commit()
        return jsonify({"doc_id": doc_id, "writeguard_mode": mode})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/techwriter/research", methods=["POST"])
def api_techwriter_research():
    """POST — AI research + draft for a single section.

    Body: {query, section_heading, template_type, collection_id, web_urls}
    Returns: {draft_content, rag_chunks, kg_entities, web_sources, is_airgap, warnings, error}
    """
    body = request.get_json(silent=True) or {}
    query = (body.get("query") or "").strip()
    if not query:
        return jsonify({"error": "query required"}), 400
    tenant_id, classification = _security_context()
    try:
        from tools.document_intelligence.tech_writing_assist import research_and_draft
        result = research_and_draft(
            query=query,
            section_heading=body.get("section_heading", ""),
            template_type=body.get("template_type", ""),
            collection_id=body.get("collection_id", "default"),
            tenant_id=tenant_id,
            classification=classification,
            web_urls=body.get("web_urls") or [],
        )
        return jsonify({
            "draft_content": result.draft_content,
            "rag_chunks": result.rag_chunks[:5],
            "kg_entities": result.kg_entities[:10],
            "web_sources": result.web_sources,
            "is_airgap": result.is_airgap,
            "warnings": result.warnings,
            "error": result.error,
            # TRUST: the draft's [source: N] tags resolve against this register,
            # and citation_report says whether they actually do. Returned in full
            # (not truncated like rag_chunks) — a citation the caller cannot
            # resolve is not a citation.
            "sources": result.sources,
            "citation_report": result.citation_report,
        })
    except Exception as exc:
        logger.warning("dic: techwriter/research error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/techwriter/diagram", methods=["POST"])
def api_techwriter_diagram():
    """POST — generate Mermaid diagram syntax from a natural-language description.

    Body: {description, diagram_type, template_type}
    Returns: {syntax, diagram_type, description, error}
    """
    body = request.get_json(silent=True) or {}
    description = (body.get("description") or "").strip()
    if not description:
        return jsonify({"error": "description required"}), 400
    _, classification = _security_context()
    try:
        from tools.document_intelligence.tech_writing_assist import generate_diagram_syntax
        result = generate_diagram_syntax(
            description=description,
            diagram_type=body.get("diagram_type", "mermaid"),
            template_type=body.get("template_type", ""),
            classification=classification,
        )
        return jsonify({
            "syntax": result.syntax,
            "diagram_type": result.diagram_type,
            "description": result.description,
            "error": result.error,
        })
    except Exception as exc:
        logger.warning("dic: techwriter/diagram error: %s", exc)
        return jsonify({"error": str(exc)}), 500


def _split_generated_doc(final_doc_text: str, template_headings: list[str]) -> list[tuple[str, str]]:
    """Split docgen final_doc_text on the '## ' headings api_generate emits and
    map the pieces onto the target template's section headings.

    Returns ordered (heading, content) pairs: template headings first (fuzzy-
    matched content or empty stub), then any unmatched generated sections —
    content is never dropped in favour of template purity.
    """
    import difflib
    import re

    pieces: list[tuple[str, str]] = []
    current_heading, buf = None, []
    for line in (final_doc_text or "").splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current_heading is not None:
                pieces.append((current_heading, "\n".join(buf).strip()))
            current_heading, buf = m.group(1).strip(), []
        elif current_heading is not None:
            buf.append(line)
    if current_heading is not None:
        pieces.append((current_heading, "\n".join(buf).strip()))

    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()
    generated = {norm(h): (h, c) for h, c in pieces}
    consumed: set[str] = set()

    ordered: list[tuple[str, str]] = []
    for th in template_headings:
        match = difflib.get_close_matches(norm(th), list(generated), n=1, cutoff=0.6)
        if match and match[0] not in consumed:
            consumed.add(match[0])
            ordered.append((th, generated[match[0]][1]))
        else:
            ordered.append((th, ""))
    for key, (h, c) in generated.items():
        if key not in consumed:
            ordered.append((h, c))
    return ordered


@dic_bp.route("/api/import-from-docgen", methods=["POST"])
def api_import_from_docgen():
    """POST — open a docgen session's document in the Tech Writer.

    Body: {session_id, title?, template_type?, classification?}
    Returns: {doc_id, collection_id, template_type, writeguard_mode, path}

    Path A (preferred): the session already carries dic_doc_id — the document
    generation created. Tag it with the Tech Writer template and return it;
    sections, citations, origin and review status are already correct.

    Path B (legacy sessions): rebuild sections from final_doc_text, preserving
    the generated content and citations, as origin='ai_generated' /
    status='pending_review' so the doc enters the review queue.

    Fallback (no session text at all): empty human_authored scaffold from
    _TEMPLATE_SECTIONS (previous behaviour, minus the wrong 'approved' AI doc).
    """
    from tools.document_intelligence.constants import (
        DOCGEN_DEFAULT_TEMPLATE,
        DOCGEN_DOCTYPE_TO_TEMPLATE,
        TEMPLATE_TYPE_TO_WRITEGUARD_MODE,
        TEMPLATE_TYPES,
    )

    body = request.get_json(silent=True) or {}
    session_id = (body.get("session_id") or "").strip()
    title = (body.get("title") or "Untitled").strip()
    classification = (body.get("classification") or "CUI").strip()

    session = None
    if session_id:
        try:
            from tools.docgen import session_manager as _sm
            session = _sm.get_session(session_id)
        except Exception as exc:  # docgen unavailable — degrade to scaffold path
            logger.warning("dic: import-from-docgen session lookup failed: %s", exc)

    # Server-side template resolution (client map removed): explicit override
    # wins, else the session's doc_type maps through the shared constant.
    template_type = (body.get("template_type") or "").strip().upper()
    if not template_type and session:
        template_type = DOCGEN_DOCTYPE_TO_TEMPLATE.get(
            (session.get("doc_type") or "").lower(), DOCGEN_DEFAULT_TEMPLATE
        )
    template_type = template_type or DOCGEN_DEFAULT_TEMPLATE
    if template_type not in TEMPLATE_TYPES:
        return jsonify({"error": f"Invalid template_type: {template_type}"}), 400

    tenant_id, _ = _security_context()
    writeguard_mode = TEMPLATE_TYPE_TO_WRITEGUARD_MODE.get(template_type, "default")
    wg_result_id = (session or {}).get("wg_result_id")

    try:
        conn = _conn()
        import uuid as _uuid
        now = _now()

        # ── Path A: reuse the document generation already created ────────────
        dic_doc_id = (session or {}).get("dic_doc_id")
        if dic_doc_id:
            row = conn.execute(
                "SELECT doc_id, collection_id FROM dic_documents WHERE doc_id = %s",
                (dic_doc_id,),
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE dic_documents
                       SET template_type = %s, writeguard_mode = %s,
                           source_idr_session_id = %s, source_wg_result_id = %s
                       WHERE doc_id = %s""",
                    (template_type, writeguard_mode, session_id, wg_result_id, dic_doc_id),
                )
                conn.commit() if hasattr(conn, "commit") else None
                logger.info(
                    "dic: import-from-docgen path=A doc_id=%s template=%s", dic_doc_id, template_type
                )
                return jsonify({
                    "doc_id": dic_doc_id,
                    "collection_id": dict(row).get("collection_id"),
                    "template_type": template_type,
                    "writeguard_mode": writeguard_mode,
                    "path": "reused_generated_doc",
                })

        # ── Paths B / fallback: build a document ──────────────────────────────
        doc_id = str(_uuid.uuid4())
        collection_id = (session or {}).get("dic_collection_id") or (
            f"docgen-import-{session_id[:8]}" if session_id else str(_uuid.uuid4())[:8]
        )
        # This was the only get-or-create in the codebase; it is now the shared
        # helper so every ingestion path gets the same guarantee. The `except:
        # pass` it replaces was itself a latent PostgreSQL bug — a failed
        # statement poisons the transaction, so a swallowed error here would
        # resurface as an unrelated "transaction is aborted" on the INSERT below.
        ensure_collection(
            conn,
            collection_id,
            name=f"Docgen Import — {title[:60]}",
            tenant_id=tenant_id,
            classification=classification,
        )

        final_doc_text = (session or {}).get("final_doc_text") or ""
        ai_content = bool(final_doc_text.strip())
        origin = "ai_generated" if ai_content else "human_authored"
        status = "pending_review" if ai_content else "approved"

        conn.execute(
            """INSERT INTO dic_documents
               (doc_id, collection_id, title, filename, status, origin,
                classification, template_type, writeguard_mode,
                source_idr_session_id, source_wg_result_id, tenant_id, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                doc_id,
                collection_id,
                title,
                f"docgen-{session_id[:8]}.doc" if session_id else "imported.doc",
                status,
                origin,
                classification,
                template_type,
                writeguard_mode,
                session_id or None,
                wg_result_id,
                tenant_id,
                now,
            ),
        )

        # Every document needs a version row (dic_sections.version_id is NOT NULL —
        # the previous scaffold insert violated this).
        version_id = f"{doc_id}_v1"
        conn.execute(
            """INSERT INTO dic_versions
               (version_id, doc_id, version_no, origin, status, created_at, created_by,
                tenant_id, classification)
               VALUES (%s,%s,1,%s,%s,%s,%s,%s,%s)""",
            (version_id, doc_id, origin, status, now, "docgen_bridge", tenant_id, classification),
        )

        template_headings = _TEMPLATE_SECTIONS.get(template_type, ["Overview"])
        if ai_content:
            section_pairs = _split_generated_doc(final_doc_text, template_headings)
        else:
            section_pairs = [(h, "") for h in template_headings]

        for i, (heading, content) in enumerate(section_pairs):
            # section_id carries a sortable index — section listings ORDER BY section_id.
            s_id = f"{doc_id[:8]}-s{i:03d}-{_uuid.uuid4().hex[:8]}"
            conn.execute(
                """INSERT INTO dic_sections
                   (section_id, version_id, doc_id, heading, content,
                    status, origin, created_at, tenant_id, classification)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    s_id,
                    version_id,
                    doc_id,
                    heading,
                    content,
                    "pending_review" if (ai_content and content) else "draft",
                    origin if content else "human_authored",
                    now,
                    tenant_id,
                    classification,
                ),
            )

        conn.commit() if hasattr(conn, "commit") else None
        logger.info(
            "dic: import-from-docgen path=%s doc_id=%s template=%s sections=%d",
            "B" if ai_content else "scaffold", doc_id, template_type, len(section_pairs),
        )
        return jsonify({
            "doc_id": doc_id,
            "collection_id": collection_id,
            "template_type": template_type,
            "writeguard_mode": writeguard_mode,
            "path": "rebuilt_from_text" if ai_content else "empty_scaffold",
        })
    except Exception as exc:
        logger.warning("dic: import-from-docgen error: %s", exc)
        return jsonify({"error": str(exc)}), 500


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
    # GraphRAG themes: the community summaries, grouped by collection, for browsing
    # the thematic structure of the corpus alongside the buried-bodies findings.
    themes = []
    try:
        from tools.knowledge_graph.community_engine import themes_by_collection

        conn = _conn()
        try:
            themes = themes_by_collection(conn, tenant_id=tenant_id)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — themes are best-effort; findings still render
        logger.debug("dic: explorer themes unavailable: %s", exc)
    return render_template("document_intelligence/explorer.html", findings=findings, themes=themes)


@dic_bp.route("/handoff")
def handoff():
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        sessions = _safe_rows(
            conn,
            "SELECT session_id, departing_owner_id, successor_owner_id, dest_collection_id, title, status, "
            "agenda_count, answered_count, generated_count, orphan_count, created_at "
            "FROM dic_handoff_sessions WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 50",
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
            "VALUES (%s,%s,%s,%s,%s)",
            (job_id, filename, collection_id, "queued", tenant_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    def _run():
        outcome = None
        try:
            def _cb(stage: str, detail: str, pct: int, extra: dict | None = None) -> None:
                event = {"stage": stage, "detail": detail, "pct": pct}
                if extra:
                    event.update(extra)
                q.put(event)
                # Update DB status.
                try:
                    c = _conn()
                    c.execute(
                        "UPDATE dic_ingest_jobs SET status=%s, stage_detail=%s, updated_at=%s WHERE job_id=%s",
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
            # Cache result in-memory (survives DB INSERT failures on PG).
            with _JOB_LOCK:
                _JOB_RESULTS[job_id] = {
                    "status": "done",
                    "doc_id": outcome.doc_id,
                    "chunks": outcome.chunks,
                    "errors": outcome.errors,
                }
            # Preserve the uploaded filename — ingest_file only sees the temp
            # path, which otherwise lands as e.g. 'tmp9x41vmaz.txt'.
            try:
                c = _conn()
                c.execute(
                    "UPDATE dic_documents SET filename = %s, "
                    "title = COALESCE(NULLIF(title, ''), %s) WHERE doc_id = %s",
                    (filename, Path(filename).stem, outcome.doc_id),
                )
                c.commit()
                c.close()
            except Exception as exc:
                logger.warning("dic: filename restore failed: %s", exc)
            # Best-effort DB update (may fail if INSERT never succeeded).
            try:
                c = _conn()
                c.execute(
                    "UPDATE dic_ingest_jobs SET status='done', doc_id=%s, chunks_total=%s, "
                    "chunks_done=%s, errors_json=%s, updated_at=%s WHERE job_id=%s",
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
            with _JOB_LOCK:
                _JOB_RESULTS[job_id] = {"status": "error", "doc_id": None, "message": str(exc)}
            try:
                c = _conn()
                c.execute(
                    "UPDATE dic_ingest_jobs SET status='error', stage_detail=%s, updated_at=%s WHERE job_id=%s",
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
            row = _safe_rows(conn, "SELECT status, stage_detail, chunks_total, doc_id FROM dic_ingest_jobs WHERE job_id=%s", (job_id,))
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
                if event.get("stage") == "error":
                    break
                # Treat "done" as terminal only when it carries the outcome
                # metadata (chunks). Progress-style done events are not final.
                if event.get("stage") == "done" and "chunks" in event:
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
    """Return final ingest job outcome.

    Checks the in-memory result cache first (always populated, even when the
    DB INSERT failed due to SQL parameter-style mismatch on PostgreSQL), then
    falls back to the DB for results from previous server instances.
    """
    # Fast path: in-memory result populated by the ingest thread.
    with _JOB_LOCK:
        mem = _JOB_RESULTS.get(job_id)
    if mem is not None:
        return jsonify(mem)
    # DB fallback (previous server instances).
    conn = _conn()
    try:
        rows = _safe_rows(conn, "SELECT * FROM dic_ingest_jobs WHERE job_id=%s", (job_id,))
    finally:
        conn.close()
    if not rows:
        return jsonify({"status": "pending", "doc_id": None}), 202
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
    query_text = (data.get("query") or "").strip()
    label = (data.get("label") or query_text or "").strip()
    entity_type = data.get("entity_type")
    limit = min(int(data.get("limit", 50)), 200)
    collection_id = (data.get("collection_id") or "").strip() or None
    doc_id = (data.get("doc_id") or "").strip() or None
    conn = _conn()
    # KG tables (kg_nodes/kg_edges/kg_graphs) do not carry tenant_id, so the
    # global RLS predicate would add an UndefinedColumn clause.  Disable RLS for
    # this read-only KG endpoint; collection/doc scoping is enforced below via
    # dic_chunk_links / dic_documents.
    conn.set_security_context(None)  # rls-bypass: kg_nodes/kg_edges/kg_graphs have no tenant_id; scoped via dic_chunk_links + dic_documents instead

    def _collection_chunk_clause(alias: str = "n") -> tuple[str, list]:
        """Return a predicate and params that restrict a kg_nodes alias to
        chunks that belong to a DIC document in the requested collection."""
        sql = (
            f"{alias}.source_chunk_id IS NOT NULL AND "
            f"{alias}.source_chunk_id IN ("
            f"SELECT l.rag_chunk_id FROM dic_chunk_links l "
            f"JOIN dic_documents d ON d.doc_id = l.doc_id "
            f"WHERE d.collection_id = %s)"
        )
        return sql, [collection_id]

    def _chunk_ids_for_query(query: str, coll_id: str) -> list[str]:
        """Return chunk IDs whose text matches the query within the collection.

        This is a deterministic BM25-style fallback (no embeddings / no LLM) so
        the KG relevance path works air-gapped and does not depend on the
        embedding provider being configured.
        """
        try:
            from tools.document_intelligence.search_engine import _extract_terms

            terms = _extract_terms(query)
            if not terms or not coll_id:
                return []
            like = " OR ".join(["LOWER(rc.content) LIKE LOWER(%s)"] * len(terms))
            params = [coll_id] + [f"%{t}%" for t in terms]
            cur = conn.execute(
                "SELECT DISTINCT rc.id FROM rag_chunks rc "
                "JOIN dic_chunk_links l ON l.rag_chunk_id = rc.id "
                "JOIN dic_documents d ON d.doc_id = l.doc_id "
                f"WHERE d.collection_id = %s AND ({like}) "
                "LIMIT 50",
                tuple(params),
            )
            return [row[0] for row in cur.fetchall() if row[0]]
        except Exception as exc:
            logger.debug("dic kg-explore chunk fallback error: %s", exc)
            return []

    try:
        if mode == "entities":
            sql = (
                "SELECT n.id, n.label, n.entity_type, n.centrality, n.source_chunk_id "
                "FROM kg_nodes n"
            )
            params: list = []
            clauses = []
            if collection_id:
                cc_sql, cc_params = _collection_chunk_clause("n")
                clauses.append(cc_sql)
                params.extend(cc_params)
            if label:
                clauses.append("LOWER(n.label) LIKE LOWER(%s)")
                params.append(f"%{label}%")
            if entity_type:
                clauses.append("n.entity_type = %s")
                params.append(entity_type)
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY COALESCE(n.centrality, 0) DESC LIMIT %s"
            params.append(limit)
            rows = _safe_rows(conn, sql, tuple(params))
            if not rows and query_text and collection_id:
                chunk_ids = _chunk_ids_for_query(query_text, collection_id)[:50]
                if chunk_ids:
                    ph = ",".join("%s" * len(chunk_ids))
                    fallback_sql = (
                        "SELECT n.id, n.label, n.entity_type, n.centrality, n.source_chunk_id "
                        f"FROM kg_nodes n WHERE n.source_chunk_id IN ({ph}) "
                        "ORDER BY COALESCE(n.centrality, 0) DESC LIMIT %s"
                    )
                    rows = _safe_rows(
                        conn,
                        fallback_sql,
                        tuple(chunk_ids + [limit]),
                    )
            return jsonify({"entities": rows, "count": len(rows)})

        elif mode == "relations":
            if not label:
                return jsonify({"error": "label is required for relations mode"}), 400
            params = [f"%{label}%", f"%{label}%"]
            collection_clause = ""
            if collection_id:
                src_sql, src_params = _collection_chunk_clause("src")
                tgt_sql, tgt_params = _collection_chunk_clause("tgt")
                collection_clause = f" AND ({src_sql}) AND ({tgt_sql})"
                params.extend(src_params + tgt_params)
            params.append(limit)
            rows = _safe_rows(
                conn,
                "SELECT src.label AS source, tgt.label AS target, e.relationship, e.weight "
                "FROM kg_edges e "
                "JOIN kg_nodes src ON src.id = e.source_id "
                "JOIN kg_nodes tgt ON tgt.id = e.target_id "
                "WHERE (LOWER(src.label) LIKE LOWER(%s) OR LOWER(tgt.label) LIKE LOWER(%s)) "
                + collection_clause +
                "ORDER BY e.weight DESC LIMIT %s",
                tuple(params),
            )
            return jsonify({"relationships": rows, "count": len(rows)})

        elif mode == "graph":
            graph_query = (data.get("query") or "").strip()
            node_sql = (
                "SELECT n.id, n.label, n.entity_type, n.centrality "
                "FROM kg_nodes n "
            )
            node_params: list = []
            clauses = []
            if collection_id:
                cc_sql, cc_params = _collection_chunk_clause("n")
                clauses.append(cc_sql)
                node_params.extend(cc_params)
            if doc_id:
                clauses.append(
                    "n.source_chunk_id IN ("
                    "SELECT l.rag_chunk_id FROM dic_chunk_links l "
                    "WHERE l.doc_id = %s)"
                )
                node_params.append(doc_id)
            if clauses:
                node_sql += " WHERE " + " AND ".join(clauses)
            node_sql += " ORDER BY COALESCE(n.centrality, 0) DESC LIMIT %s"
            node_params.append(limit)
            nodes = _safe_rows(conn, node_sql, tuple(node_params))
            if not nodes and graph_query and collection_id and not doc_id:
                chunk_ids = _chunk_ids_for_query(graph_query, collection_id)[:50]
                if chunk_ids:
                    ph = ",".join("%s" * len(chunk_ids))
                    fallback_sql = (
                        "SELECT n.id, n.label, n.entity_type, n.centrality "
                        f"FROM kg_nodes n WHERE n.source_chunk_id IN ({ph}) "
                        "ORDER BY COALESCE(n.centrality, 0) DESC LIMIT %s"
                    )
                    nodes = _safe_rows(
                        conn,
                        fallback_sql,
                        tuple(chunk_ids + [limit]),
                    )
            node_ids = [n["id"] for n in nodes]
            if not node_ids:
                return jsonify({"nodes": [], "edges": [], "count": 0})
            ph = ",".join("%s" * len(node_ids))
            edges = _safe_rows(
                conn,
                f"SELECT e.source_id, e.target_id, e.relationship, e.weight "
                f"FROM kg_edges e "
                f"WHERE e.source_id IN ({ph}) AND e.target_id IN ({ph}) "
                f"ORDER BY e.weight DESC LIMIT %s",
                tuple(node_ids + node_ids + [min(limit, 80)]),
            )
            return jsonify({"nodes": nodes, "edges": edges, "count": len(nodes)})

        elif mode == "neighbors":
            if not label:
                return jsonify({"error": "label is required for neighbors mode"}), 400
            node_params = [f"%{label}%"]
            collection_clause = ""
            if collection_id:
                cc_sql, cc_params = _collection_chunk_clause("kg_nodes")
                collection_clause = " AND " + cc_sql
                node_params.extend(cc_params)
            node_rows = _safe_rows(
                conn,
                "SELECT id, label, entity_type FROM kg_nodes "
                "WHERE LOWER(label) LIKE LOWER(%s)" + collection_clause + " LIMIT 1",
                tuple(node_params),
            )
            if not node_rows:
                return jsonify({"neighbors": [], "relationships": [], "count": 0})
            node_id = node_rows[0]["id"]
            neighbor_params = [node_id, node_id, node_id]
            collection_clause = ""
            if collection_id:
                cc_sql, cc_params = _collection_chunk_clause("n")
                collection_clause = f" AND ({cc_sql})"
                neighbor_params.extend(cc_params)
            neighbor_params.append(limit)
            neighbors = _safe_rows(
                conn,
                "SELECT DISTINCT n.label, n.entity_type, e.relationship, e.weight "
                "FROM kg_edges e "
                "JOIN kg_nodes n ON (n.id = e.target_id OR n.id = e.source_id) "
                "WHERE (e.source_id = %s OR e.target_id = %s) AND n.id != %s "
                + collection_clause +
                "ORDER BY e.weight DESC LIMIT %s",
                tuple(neighbor_params),
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
    classify_intent = bool(data.get("classify_intent"))
    keywords = data.get("keywords")
    tenant_id, clearance = _security_context()

    try:
        from tools.document_intelligence.search_engine import DICSearchEngine
        engine = DICSearchEngine(tenant_id=tenant_id)
        # Opt-in: classify query intent (aiify-opp-28) BEFORE expansion/search so
        # the caller can inspect the recommended strategy alongside results.
        # Degrades gracefully — llm_used=False when model unavailable.
        intent = None
        if classify_intent:
            intent = engine.classify_query_intent(query)
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
        if intent is not None:
            payload["intent"] = intent.to_dict()
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
        # Personalise results if Second Brain is enabled
        try:
            import os as _os
            if _os.environ.get("ICDEV_SECOND_BRAIN_ENABLED", "false").lower() == "true":
                from flask import g as _g
                from tools.second_brain.dic_personaliser import personalise_dic_results
                _uid = getattr(_g, "user_id", "default")
                _tid = getattr(_g, "tenant_id", "default")
                payload["results"] = personalise_dic_results(payload["results"], _uid, _tid)
        except Exception:
            pass
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
            "SELECT content, content_hash, source_id, classification FROM rag_chunks WHERE id = %s",
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
            "SELECT doc_id, collection_id, page, section FROM dic_chunk_links WHERE rag_chunk_id = %s",
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

# Synthesis keywords — LLM is warranted for these query types.
# Simple "what is / what are" factual lookups are answered directly when the
# evidence contains the query terms, so they are NOT synthesis triggers.
_SYNTHESIS_KEYWORDS = frozenset([
    "summarize", "summary", "compare", "contrast", "explain", "describe",
    "how does", "why does", "what does", "what is the difference",
    "write", "draft", "generate", "create", "list all", "what are all",
])


_STOP_WORDS = frozenset({
    "what", "is", "are", "was", "were", "be", "been", "being", "the", "a", "an",
    "this", "that", "these", "those", "of", "in", "on", "at", "to", "for", "with",
    "by", "from", "and", "or", "as", "it", "its", "their", "they", "them", "his",
    "her", "he", "she", "we", "you", "i", "me", "my", "our", "us", "do", "does",
    "did", "has", "have", "had", "can", "could", "would", "should", "will", "shall",
    "may", "might", "must", "about", "against", "all", "any", "each", "every",
    "some", "many", "much", "more", "most", "other", "than", "then", "now", "here",
    "there", "where", "why", "how", "who", "which", "whom", "whose", "when", "am",
})


def _significant_terms(query: str) -> list[str]:
    """Return content-bearing terms from a query, dropping stop words."""
    terms = [t.lower() for t in re.findall(r"\b\w{3,}\b", query)]
    return [t for t in terms if t not in _STOP_WORDS and not t.isdigit()]


def _query_match_score(query: str, result) -> float:
    """Lexical confidence that `result` directly answers `query`.

    Rewards query-term and exact-phrase coverage inside the focused passage
    returned by :func:`_extract_passage`, plus definition-breadth signals
    (multi-sentence passage, repeated phrase). A chunk that only mentions the
    query topic in passing now scores lower than a chunk whose focused passage
    actually defines it. Air-gap safe: no LLM required.
    """
    terms = _significant_terms(query)
    if not terms:
        return 0.0
    content = (result.content or "").lower()
    if not content:
        return 0.0

    passage = _extract_passage(result.content or "", query, max_chars=900).lower()
    if not passage:
        return 0.0
    phrase = " ".join(terms)

    # Focused-passage coverage (primary signal).
    matched_passage = [t for t in terms if t in passage]
    coverage_passage = len(matched_passage) / len(terms)

    # Exact-phrase and repetition inside the focused passage.
    phrase_in_passage = 0.2 if phrase and phrase in passage else 0.0
    phrase_hits = passage.count(phrase) if phrase else 0
    phrase_repeat = min(0.45, phrase_hits * 0.15)

    # Definition breadth: multi-sentence focused passages are more likely to
    # define a concept than a single passing mention.
    sentences = [s for s in re.split(r"[.!?]+", passage) if s.strip()]
    breadth = min(0.45, max(0, len(sentences) - 1) * 0.15)

    # Full-content coverage (secondary confirmation).
    matched_full = [t for t in terms if t in content]
    coverage_full = len(matched_full) / len(terms)

    return min(
        1.0,
        coverage_passage * 0.35
        + phrase_in_passage
        + phrase_repeat
        + breadth
        + coverage_full * 0.1,
    )


def _extract_passage(content: str, query: str, max_chars: int = 800) -> str:
    """Return a focused passage from content that answers the query.

    Anchors on the exact phrase formed by the query's significant terms (or the
    first matching term), then expands only enough to capture the surrounding
    sentence(s).  This avoids dragging in unrelated paragraphs from large
    Wikipedia-style chunks.
    """
    if not content:
        return ""
    terms = _significant_terms(query)
    content_lower = content.lower()
    if not terms:
        return content[:max_chars].strip()

    phrase = " ".join(terms)
    anchor = -1
    if phrase:
        anchor = content_lower.find(phrase)
    if anchor == -1:
        positions = [content_lower.find(t) for t in terms if content_lower.find(t) != -1]
        if positions:
            anchor = min(positions)
    if anchor == -1:
        return content[:max_chars].strip()

    half = max_chars // 2
    # Start a little before the anchor but snap to a sentence/paragraph boundary.
    start = max(0, anchor - half)
    para_break = content.rfind("\n\n", 0, start)
    if para_break != -1 and start - para_break < 200:
        start = para_break + 2
    else:
        while start > 0 and content[start - 1] not in ".!?\n":
            start -= 1

    # End at the first sentence terminator after the anchor window so the
    # snippet stays focused on the matching phrase.
    min_end = min(len(content), anchor + max(40, half // 2))
    end = min_end
    while end < len(content) and content[end] not in ".!?\n":
        end += 1
    if end < len(content):
        end += 1  # include the terminator

    # Allow a second sentence only if it is short and keeps us within budget.
    next_end = end
    budget_left = max_chars - (end - start)
    if budget_left > 60:
        while next_end < len(content) and content[next_end] in " \n":
            next_end += 1
        sentence_end = next_end
        while sentence_end < len(content) and content[sentence_end] not in ".!?\n":
            sentence_end += 1
        if sentence_end < len(content) and sentence_end - end <= budget_left:
            end = sentence_end + 1

    return content[start:end].strip()


def _snippet_focus_score(query: str, result) -> float:
    """Tie-breaker that rewards chunks whose focused passage *defines* the query topic.

    Prefers higher query-term and exact-phrase density inside the passage
    returned by :func:`_extract_passage` (the passage actually surrounding
    the query terms), and boosts results that come from fully-enriched
    documents carrying a ``doc_summary``. This lets a chunk that defines
    "Bill of Rights" outrank a shorter chunk that only mentions it in passing.
    """
    content = (result.content or "").lower()
    if not content:
        return 0.0
    terms = _significant_terms(query)
    if not terms:
        return 0.0

    passage = _extract_passage(result.content or "", query, max_chars=900).lower()
    phrase = " ".join(terms)
    passage_len = max(1, len(passage))

    # Density of query terms inside the focused passage (defines vs. mentions).
    term_hits = sum(passage.count(t) for t in terms)
    density = min(1.0, term_hits * 8 / passage_len)

    # Exact phrase presence and repetition inside the focused passage.
    phrase_hits = passage.count(phrase) if phrase else 0
    phrase_anchor = 0.5 if phrase_hits else 0.0
    phrase_repeat = min(0.3, phrase_hits * 0.15)

    # Boost fully-enriched documents that produced a doc_summary at ingest time.
    summary_boost = 0.25 if (result.doc_summary or "").strip() else 0.0

    # Mild reward for a tight, focused passage over a sprawling one.
    focus_bonus = max(0.0, 1.0 - passage_len / 1000)

    # Small length preference: avoid giant Wikipedia-style dumps.
    length_penalty = max(0.0, 1.0 - len(content) / 6000)

    return density + phrase_anchor + phrase_repeat + summary_boost + focus_bonus + length_penalty


def _needs_synthesis(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _SYNTHESIS_KEYWORDS)


# Global/thematic questions are about the corpus AS A WHOLE ("what are the main
# themes", "what topics do these documents cover"). No single chunk answers them
# — the answer lives in the KG's community structure. These queries get the
# GraphRAG community summaries fed into synthesis alongside the retrieved chunks.
_GLOBAL_QUERY_KEYWORDS = frozenset([
    "main theme", "main topic", "key theme", "key topic", "overall", "overarching",
    "across all", "across the", "these documents", "this collection", "the corpus",
    "main points", "high level", "high-level", "big picture", "recurring",
    "what topics", "what themes", "common themes", "overview of",
])


def _is_global_query(query: str) -> bool:
    q = query.lower()
    return any(kw in q for kw in _GLOBAL_QUERY_KEYWORDS)


def _community_context(message: str, tenant_id: str, limit: int = 5, collection_id: str | None = None) -> list[str]:
    """GraphRAG community summaries relevant to a global/thematic question.

    Scoped to the active collection when one is given, so "the main themes" means
    the collection the user is in. Returns summary texts (whole-corpus themes) or
    [] if the engine/table is empty or unavailable — always graceful, never
    blocks the grounded answer.
    """
    try:
        from tools.knowledge_graph.community_engine import search_communities

        conn = _conn()
        try:
            rows = search_communities(conn, message, tenant_id=tenant_id, limit=limit, collection_id=collection_id)
        finally:
            conn.close()
        return [r["summary_text"] for r in rows if r.get("summary_text")]
    except Exception as exc:  # noqa: BLE001 — GraphRAG augmentation is best-effort
        logger.debug("dic: community context unavailable: %s", exc)
        return []


def _build_sources(results: list) -> list[dict]:
    """Build a numbered, human-readable source list for chat citations.

    Each source links to the original document detail page and includes the
    page/classification metadata needed for a clickable citation badge.
    """
    sources = []
    seen = set()
    for r in results:
        key = (r.doc_id, r.page)
        if key in seen:
            continue
        seen.add(key)
        title = r.doc_title or r.doc_id or "Document"
        sources.append({
            "num": len(sources) + 1,
            "doc_id": r.doc_id,
            "doc_title": title,
            "page": r.page,
            "section": r.section,
            "classification": getattr(r.citation, "classification", None) or "CUI",
            "score": round(getattr(r, "score", 0.0), 4),
            "archive_url": f"/document-intelligence/doc/{r.doc_id}" if r.doc_id else "#",
        })
    return sources


def _compile_grounded_answer(results: list, query: str) -> dict:
    """Build a grounded answer directly from RAG chunks — no LLM.

    Returns a dict with 'answer' (numbered citation markers [1], [2], …) and
    'sources' (human-readable links to the original documents).
    """
    if not results:
        return {"answer": "No relevant documents found.", "sources": []}
    cited_results = results[:4]
    sources = _build_sources(cited_results)
    source_map = {(s["doc_id"], s["page"]): s["num"] for s in sources}
    lines = []
    for r in cited_results:
        num = source_map.get((r.doc_id, r.page))
        citation = f"[{num}]" if num else f"[{r.doc_title or r.doc_id}]"
        passage = _extract_passage(r.content or "", query, max_chars=900)
        if passage:
            lines.append(f"{citation} {passage}")
    answer = "\n\n".join(lines) if lines else _extract_passage(results[0].content or "", query, max_chars=1200)
    return {"answer": answer, "sources": sources}


def _llm_synthesize(
    message: str, results: list, community_summaries: list[str] | None = None
) -> str | None:
    """Call LLM only when synthesis is warranted. Returns None on failure.

    Uses LLMRouter.invoke() with a system prompt instructing the model to answer
    only from the provided evidence — grounded, no hallucination. Cites sources
    with [N] markers that match the sources list returned by api_chat.

    For global/thematic questions, ``community_summaries`` carries the GraphRAG
    whole-corpus themes so the model can answer a question no single chunk covers.
    """
    evidence_results = results[:5]
    sources = _build_sources(evidence_results)
    source_map = {(s["doc_id"], s["page"]): s["num"] for s in sources}
    evidence_lines = []
    for r in evidence_results:
        num = source_map.get((r.doc_id, r.page), "?")
        passage = _extract_passage(r.content or "", message, max_chars=1000)
        evidence_lines.append(
            f"[{num}] {r.doc_title or r.doc_id or 'Document'} "
            f"(p.{r.page or '?'})\n{passage}"
        )
    evidence = "\n\n".join(evidence_lines)
    graph_overview = ""
    if community_summaries:
        overview_lines = "\n".join(f"- {s}" for s in community_summaries[:5])
        graph_overview = (
            "\n\nKnowledge-graph thematic overview (themes spanning the whole "
            "corpus, derived from the document graph — use these for high-level or "
            "thematic questions the individual passages above do not cover):\n"
            f"{overview_lines}"
        )
    prompt = (
        "You are a document assistant. Answer ONLY using the provided evidence — "
        "do not add information beyond what is cited. "
        "Cite supporting facts with [N] markers that match the evidence numbers. "
        "If the evidence is insufficient, say so explicitly.\n\n"
        f"Evidence:\n{evidence}{graph_overview}\n\n"
        f"Question: {message}"
    )
    # Adoption wave 2: route DIC chat synthesis through the GOVERNED cortex.complete
    # instead of a raw router.invoke. The raw path set skip_injection_scan=True on a
    # prompt containing the user's `message` — Cortex's gateway pre-check screens it,
    # and adds egress redaction, provenance, and an append-only audit row to this
    # user-facing document-QA surface. No-LLM / errors fall through to None (the
    # route degrades to the grounded RAG+KG evidence without synthesis).
    try:
        from tools.cortex import api as cortex_api
        from tools.cortex.schemas import CortexContext

        cx = cortex_api.complete(
            prompt,
            function="question_answering",
            ctx=CortexContext(
                classification="CUI", domain="document", agent_id="dic-chat"
            ),
            max_tokens=1024,
        )
        return (cx.text or "").strip() or None
    except Exception as exc:
        logger.warning("dic: chat LLM error: %s", exc)
    return None


@dic_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """DIC chat: RAG+KG first. LLM only for synthesis queries when available.

    Mode logic:
    1. Always retrieve from RAG+KG (grounded, air-gap safe).
    2. If top result confidence is high (≥0.7) AND query is a direct lookup:
       → return grounded answer directly, mode="grounded", NO LLM call.
    3. If query needs synthesis (summarize/compare/explain/…) AND LLM available:
       → synthesize from evidence, mode="ai_assisted", verify with CoD gate.
    4. If LLM unavailable or synthesis not needed:
       → compile grounded answer from top chunks, mode="grounded".

    Every response includes a numbered 'sources' array with human-readable
    titles and clickable archive_url links back to the original document.
    """
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400
    collection_id = data.get("collection_id")
    tenant_id, _cls = _security_context()

    # Conversational memory (chat_memory): when enabled, resolve a bare follow-up
    # ("and its retention period?") by prepending the prior turn's GROUNDED subject,
    # so the existing grounded retrieval resolves it. Off => fully stateless.
    from tools.document_intelligence import chat_memory as _cm
    session_id = (data.get("session_id") or "").strip()
    mem_on = _cm.memory_enabled(data)
    resolved_subject = ""
    search_message = message
    if mem_on and session_id:
        try:
            _res = _cm.resolve_followup(session_id, message, tenant_id=tenant_id)
            if _res.is_followup:
                search_message = _res.resolved_query
                resolved_subject = _res.subject
        except Exception as _mexc:  # noqa: BLE001
            logger.debug("dic chat memory resolve failed: %s", _mexc)

    def _mem(payload: dict, answer: str = "", record_results=None) -> dict:
        """Attach memory fields to a response payload and best-effort record the turn."""
        payload["memory"] = mem_on
        payload["resolved_subject"] = resolved_subject
        # Report WHY memory is off. record_turn swallows write failures, so
        # without this a broken table is indistinguishable from an idle one —
        # which is how dic_chat_memory sat at 0 rows unnoticed.
        if mem_on and session_id:
            try:
                payload["memory_health"] = _cm.memory_health()
            except Exception:  # noqa: BLE001
                payload["memory_health"] = {"available": False, "reason": "probe_failed"}
        elif mem_on and not session_id:
            payload["memory_health"] = {"available": False, "reason": "no_session_id"}
        else:
            payload["memory_health"] = {"available": False, "reason": "disabled"}
        if mem_on and session_id and record_results:
            try:
                _cm.record_turn(session_id, message, answer, record_results,
                                tenant_id=tenant_id, collection_id=collection_id or "")
            except Exception as _rexc:  # noqa: BLE001
                logger.warning("dic chat memory record failed: %s", _rexc)
        return payload

    try:
        from tools.document_intelligence.search_engine import DICSearchEngine
        engine = DICSearchEngine(tenant_id=tenant_id)
        results = engine.search(search_message, collection_id=collection_id, top_k=8)

        if not results:
            return jsonify(_mem({
                "answer": "No relevant documents found in this collection. Upload documents first.",
                "sources": [],
                "citations": [],
                "abstained": True,
                "mode": "grounded",
            }))

        citations = [r.citation.to_dict() for r in results[:5]]
        # Use a lexical match score that works in air-gap mode; raw vector/BM25
        # scores are not calibrated for a fixed threshold. Re-rank by lexical
        # match quality + focus on the passage actually surrounding query terms.
        scored_results = sorted(
            results,
            key=lambda r: (_query_match_score(message, r), _snippet_focus_score(message, r)),
            reverse=True,
        )
        best_result = scored_results[0] if scored_results else None
        top_match_score = _query_match_score(message, best_result) if best_result else 0.0

        # ── Path 1: High-confidence direct lookup — NO LLM ──────────────────
        if best_result and top_match_score >= 0.7 and not _needs_synthesis(message):
            grounded = _compile_grounded_answer([best_result], message)
            return jsonify(_mem({
                "answer": grounded["answer"],
                "sources": grounded["sources"],
                "citations": citations,
                "abstained": False,
                "mode": "grounded",
            }, answer=grounded["answer"], record_results=scored_results))

        # ── Path 2: Grounded answer from top chunks — NO LLM ────────────────
        grounded = _compile_grounded_answer(scored_results, message)
        answer = grounded["answer"]
        sources = grounded["sources"]

        # ── Path 3: LLM synthesis if query warrants it ───────────────────────
        mode = "grounded"
        abstained = False
        # Whether the verifier actually ran AND every cited claim held. The UI
        # badge is driven from this — it must never assert verification that did
        # not happen. The deterministic Path-2 answer is cited but unverified.
        verified = False

        if _needs_synthesis(message):
            # Global/thematic questions get the GraphRAG community summaries fed in
            # — the corpus-level answer no single chunk contains.
            community_summaries = _community_context(message, tenant_id, collection_id=collection_id) if _is_global_query(message) else []
            llm_answer = _llm_synthesize(message, scored_results, community_summaries=community_summaries)
            if llm_answer:
                # Verify LLM answer against evidence before returning. Community
                # summaries are part of the evidence for global questions.
                try:
                    from tools.document_intelligence.verifier import verify
                    vr = verify(llm_answer, [r.content for r in scored_results] + community_summaries)
                    if vr.abstained:
                        abstained = True
                        answer = grounded["answer"]  # fall back to grounded
                    else:
                        answer = vr.verified_text or llm_answer
                        mode = "graphrag" if community_summaries else "ai_assisted"
                    verified = vr.verified
                except Exception as verr:
                    # Never publish an unverified draft as if it had passed. A
                    # verifier failure means we do not know whether the answer is
                    # grounded, so fall back to the deterministic cited answer and
                    # say so. A bare `except` that returned `llm_answer` here is
                    # what kept this gate silently dead.
                    logger.warning("dic: verifier failed, falling back to grounded: %s", verr)
                    abstained = True
                    answer = grounded["answer"]
                    mode = "grounded"

        return jsonify(_mem({
            "answer": answer,
            "sources": sources,
            "citations": citations,
            "abstained": abstained,
            "verified": verified,
            "mode": mode,
        }, answer=answer, record_results=scored_results))
    except Exception as exc:
        logger.warning("dic: chat error: %s", exc)
        return jsonify({"answer": f"Error: {exc}", "sources": [], "citations": [], "abstained": True, "mode": "grounded"}), 500


# ── API: Collections ──────────────────────────────────────────────────────────

@dic_bp.route("/api/collections", methods=["GET"])
def api_collections_list():
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        rows = _safe_rows(conn, "SELECT * FROM dic_collections WHERE tenant_id = %s ORDER BY created_at DESC", (tenant_id,))
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
            "VALUES (%s,%s,%s,%s,%s)",
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
        members = _safe_rows(conn, "SELECT user_id, role, granted_by, created_at FROM dic_team_access WHERE collection_id = %s ORDER BY created_at DESC", (collection_id,))
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
    tenant_id, classification = _security_context()
    access_id = _hid("dic_access", collection_id, user_id, _now())
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO dic_team_access (access_id, collection_id, user_id, role, tenant_id, classification) VALUES (%s,%s,%s,%s,%s,%s)",
            (access_id, collection_id, user_id, role, tenant_id, classification or "CUI"),
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
            "FROM dic_documents WHERE collection_id = %s AND tenant_id = %s ORDER BY created_at DESC",
            (collection_id, tenant_id),
        )
        # Augment with latest version status and chunk count
        for r in rows:
            try:
                ver = conn.execute(
                    "SELECT version_id, status, origin, version_no FROM dic_versions "
                    "WHERE doc_id = %s ORDER BY version_no DESC LIMIT 1",
                    (r["doc_id"],),
                ).fetchone()
                if ver:
                    r["latest_version_id"] = ver[0] if hasattr(ver, "__getitem__") else ver["version_id"]
                    r["latest_status"] = ver[1] if hasattr(ver, "__getitem__") else ver["status"]
                    r["latest_origin"] = ver[2] if hasattr(ver, "__getitem__") else ver["origin"]
                    r["version_count"] = conn.execute(
                        "SELECT COUNT(*) FROM dic_versions WHERE doc_id = %s",
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
                    "SELECT COUNT(*) FROM dic_chunk_links WHERE doc_id = %s",
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
            "FROM dic_versions WHERE doc_id = %s ORDER BY version_no DESC",
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
            "VALUES (%s,%s,%s,%s,%s,%s)",
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
        if item_type == "version":
            table, pk = "dic_versions", "version_id"
        elif item_type == "section":
            table, pk = "dic_sections", "section_id"
        else:
            table, pk = "dic_ssp_fragments", "fragment_id"
        conn.execute(f"UPDATE {table} SET assigned_to = %s WHERE {pk} = %s", (assigned_to, item_id))  # nosec B608 — table/pk from ternary constants, not user input
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
                "UPDATE dic_versions SET status='needs_revision', review_notes=%s WHERE version_id=%s",
                (note, item_id),
            )
        else:
            try:
                from tools.document_intelligence.acoic import request_revision
                request_revision(item_id, reviewed_by=reviewer)
            except Exception:
                conn.execute(
                    "UPDATE dic_ssp_fragments SET status='needs_revision', reviewed_by=%s, reviewed_at=%s WHERE fragment_id=%s",
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
        resp = {"status": "approved", "item_id": item_id}
        force_note = ""
        if item_type == "version":
            # Publish gate (ground-dic-05): a version cannot move to approved
            # while any section still contains unresolved [PLACEHOLDER] tokens,
            # unless the reviewer explicitly forces the override.
            force = bool(data.get("force"))
            gate: dict = {}
            try:
                from tools.document_intelligence.consistency_checker import (
                    check_version_consistency,
                )
                gate = check_version_consistency(item_id)
            except Exception as exc:
                logger.warning("dic approve: consistency gate error: %s", exc)
            placeholder_hits = gate.get("placeholders") or []
            numeric_conflicts = gate.get("numeric_conflicts") or []
            if placeholder_hits and not force:
                return jsonify({
                    "error": "unresolved_placeholders",
                    "message": (
                        f"{len(placeholder_hits)} section(s) contain unresolved "
                        "placeholder tokens. Resolve them or resubmit with "
                        "force=true to override."
                    ),
                    "placeholders": placeholder_hits,
                    "numeric_conflicts": numeric_conflicts,
                    "item_id": item_id,
                }), 409
            conn.execute(
                "UPDATE dic_versions SET status='approved' WHERE version_id=%s", (item_id,)
            )
            resp["numeric_conflicts"] = numeric_conflicts
            if placeholder_hits and force:
                resp["forced"] = True
                summary = "; ".join(
                    f"{f['item_number']}: {', '.join(f['placeholders'][:4])}"
                    for f in placeholder_hits[:5]
                )
                force_note = (
                    f"FORCE-APPROVED with unresolved placeholders in "
                    f"{len(placeholder_hits)} section(s): {summary}"
                )
        else:
            # Try ACOIC approve helper first.
            try:
                from tools.document_intelligence.acoic import approve_fragment
                approve_fragment(item_id, reviewed_by=reviewer)
            except Exception:
                conn.execute(
                    "UPDATE dic_ssp_fragments SET status='approved', reviewed_by=%s, reviewed_at=%s WHERE fragment_id=%s",
                    (reviewer, _now(), item_id),
                )
        conn.commit()
        if force_note:
            _record_review_note(item_id, "version", force_note, reviewer)
        if note:
            _record_review_note(item_id, item_type, note, reviewer)
        # Cross-reference cascade (dmx-ref-01, best-effort): a version just moved
        # to approved — raise findings on documents whose inbound references point
        # at a section that changed. HITL-preserving (findings only, no edits) and
        # non-blocking (an approval must never fail because a cascade could not run).
        if item_type == "version":
            try:
                from tools.document_intelligence.cross_reference_tracker import (
                    cascade_on_version_approval,
                )

                casc = cascade_on_version_approval(item_id)
                resp["cross_reference_cascade"] = {
                    "cascaded": casc.get("cascaded", 0),
                    "inbound": casc.get("inbound", 0),
                }
            except Exception as exc:
                logger.warning("dic approve: cross-reference cascade error: %s", exc)
        return jsonify(resp)
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
                "UPDATE dic_versions SET status='rejected' WHERE version_id=%s", (item_id,)
            )
        else:
            try:
                from tools.document_intelligence.acoic import reject_fragment
                reject_fragment(item_id, reviewed_by=reviewer)
            except Exception:
                conn.execute(
                    "UPDATE dic_ssp_fragments SET status='rejected', reviewed_by=%s, reviewed_at=%s WHERE fragment_id=%s",
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


# ── Section Locking (pessimistic collaborative editing) ───────────────────────

@dic_bp.route("/api/sections/<section_id>/lock", methods=["GET"])
def api_section_lock_status(section_id: str):
    from tools.document_intelligence.lock_manager import get_lock
    lock = get_lock(section_id)
    if lock:
        return jsonify({"locked": True, **lock})
    return jsonify({"locked": False, "section_id": section_id})


@dic_bp.route("/api/sections/<section_id>/lock", methods=["POST"])
def api_section_lock_acquire(section_id: str):
    from tools.document_intelligence.lock_manager import acquire_lock, get_lock
    user = _current_user()
    _c = _conn()
    try:
        doc_row = _c.execute(
            "SELECT doc_id FROM dic_sections WHERE section_id = %s LIMIT 1", (section_id,)
        ).fetchone()
        doc_id = doc_row["doc_id"] if doc_row else ""
    finally:
        _c.close()
    lock = acquire_lock(section_id, user, doc_id=doc_id)
    if lock is None:
        current = get_lock(section_id) or {}
        return jsonify({
            "locked": True,
            "locked_by": current.get("locked_by", "another user"),
            "expires_at": current.get("expires_at", ""),
            "section_id": section_id,
        }), 409
    return jsonify(lock), 200


@dic_bp.route("/api/sections/<section_id>/lock", methods=["DELETE"])
def api_section_lock_release(section_id: str):
    from tools.document_intelligence.lock_manager import release_lock
    user = _current_user()
    released = release_lock(section_id, user)
    if not released:
        return jsonify({"error": "Lock not held by you or does not exist"}), 403
    return jsonify({"released": True, "section_id": section_id})


@dic_bp.route("/api/sections/<section_id>/lock/renew", methods=["PUT"])
def api_section_lock_renew(section_id: str):
    from tools.document_intelligence.lock_manager import renew_lock
    user = _current_user()
    renewed = renew_lock(section_id, user)
    if not renewed:
        return jsonify({"error": "Lock not held by you or does not exist"}), 403
    return jsonify({"renewed": True, "section_id": section_id})


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
        sql = "SELECT * FROM dic_section_annotations WHERE section_id = %s"
        params: list = [section_id]
        if status_filter:
            sql += " AND status = %s"
            params.append(status_filter)
        if category_filter:
            sql += " AND category = %s"
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
            "SELECT doc_id FROM dic_sections WHERE section_id = %s LIMIT 1", (section_id,)
        ).fetchone()
        doc_id = doc_row[0] if doc_row else ""
        ann_id = f"ann_{uuid.uuid4().hex[:16]}"
        now = _now()
        author = data.get("author") or _current_user()
        conn.execute(
            """INSERT INTO dic_section_annotations
               (ann_id, section_id, doc_id, selected_text, category, comment,
                author, status, classification, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (ann_id, section_id, doc_id,
             (data.get("selected_text") or "").strip(),
             category, comment, author, "open", "CUI", now, now),
        )
        conn.commit()
        row = dict(conn.execute(
            "SELECT * FROM dic_section_annotations WHERE ann_id = %s", (ann_id,)
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
        set_clause = ", ".join(f"{k} = %s" for k in updates)
        conn.execute(
            f"UPDATE dic_section_annotations SET {set_clause} WHERE ann_id = %s",
            [*updates.values(), ann_id],
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM dic_section_annotations WHERE ann_id = %s", (ann_id,)
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
        conn.execute("DELETE FROM dic_section_annotations WHERE ann_id = %s", (ann_id,))
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
            "UPDATE dic_sections SET status='approved', reviewed_by=%s, reviewed_at=%s WHERE section_id=%s",
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
            "UPDATE dic_sections SET status='rejected', reviewed_by=%s, reviewed_at=%s WHERE section_id=%s",
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
            "UPDATE dic_sections SET status='needs_revision', reviewed_by=%s, reviewed_at=%s WHERE section_id=%s",
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
    expected_hash = (data.get("expected_hash") or "").strip()
    force = bool(data.get("force", False))
    cid = _collection_id_from_section(section_id) or "default"
    if not _require_role(cid, "editor"):
        return _forbid("editor")
    conn = _conn()
    try:
        # Capture before-content for edit history + conflict detection
        before_row = conn.execute(
            "SELECT content FROM dic_sections WHERE section_id = %s LIMIT 1", (section_id,)
        ).fetchone()
        before_content = (dict(before_row).get("content") or "") if before_row else ""

        # Conflict check — skip when no expected_hash supplied or force=True
        if expected_hash and not force:
            from tools.document_intelligence.conflict_detector import check_conflict
            result = check_conflict(conn, section_id, expected_hash)
            if result["conflict"]:
                return jsonify({
                    "conflict": True,
                    "current_hash": result["current_hash"],
                    "current_content": result["current_content"],
                    "section_id": section_id,
                }), 409

        conn.execute(
            "UPDATE dic_sections SET content = %s, status = %s, origin = %s, created_at = %s WHERE section_id = %s",
            (content, "draft", "human_authored", _now(), section_id),
        )
        conn.commit()

        # Append-only history record (no-op if content unchanged)
        try:
            from tools.document_intelligence.history_recorder import record_edit
            record_edit(
                section_id=section_id,
                editor=_current_user(),
                content_before=before_content,
                content_after=content,
            )
        except Exception:
            pass  # history failure must never block a save

        from tools.document_intelligence.conflict_detector import compute_hash
        new_hash = compute_hash(content)
        return jsonify({"status": "updated", "section_id": section_id, "new_hash": new_hash})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/sections/<section_id>/hash", methods=["GET"])
def api_section_content_hash(section_id: str):
    """Return the CRC32 fingerprint of the section's current content."""
    from tools.document_intelligence.conflict_detector import get_section_state
    conn = _conn()
    try:
        state = get_section_state(conn, section_id)
        if state is None:
            return jsonify({"error": "section not found"}), 404
        return jsonify({"section_id": section_id, "hash": state["hash"]})
    finally:
        conn.close()


@dic_bp.route("/api/sections/<section_id>/history", methods=["GET"])
def api_section_edit_history(section_id: str):
    """Return edit history for a section, most-recent first.

    Query params:
      limit  — max entries to return (default 50, max 100)
      since  — ISO 8601 timestamp; restrict to entries at or after this time
    """
    from tools.document_intelligence.history_recorder import get_section_history
    limit = min(int(request.args.get("limit", 50)), 100)
    since = request.args.get("since") or None
    entries = get_section_history(section_id, limit=limit, since=since)
    return jsonify({"section_id": section_id, "history": entries, "count": len(entries)})


# ── API: Template Instantiation ─────────────────────────────────────────────

_TEMPLATE_SECTIONS: dict[str, list[str]] = {
    "acoic": ["Impact Assessment", "Affected Controls", "Regeneration Plan", "SSP Fragment", "Approval Gate"],
    "freshness-audit": ["Audit Scope", "Stale Document Inventory", "Remediation Plan", "Owner Assignments", "Timeline"],
    "airgap-ingest": ["Source Directory", "Ingest Pipeline Steps", "Verification Checklist", "Rollback Plan"],
    "hitl-review": ["Review Queue", "Reviewer Assignments", "Acceptance Criteria", "Escalation Path"],
    "sop-refresh": ["Current Procedure", "Change Summary", "Updated Steps", "Validation Criteria", "Rollback Plan"],
    "knowledge-handoff": ["SME Profile", "Knowledge Areas", "Interview Agenda", "Captured Artifacts", "Successor Onboarding"],
    # Tech Writer templates (migration 230)
    "STANDARD_GUIDE": [
        "Executive Summary", "Scope and Applicability", "Cloud Provider Overview",
        "Connectivity Patterns", "Security Controls", "Implementation Steps",
        "Operational Procedures", "Troubleshooting", "References",
    ],
    "SOP": [
        "Purpose", "Scope", "Responsibilities", "Prerequisites",
        "Procedure", "Verification", "Rollback", "References",
    ],
    "RUNBOOK": [
        "Overview", "Prerequisites", "Pre-flight Checks",
        "Procedure", "Verification Steps", "Rollback", "Escalation Path",
    ],
    "ARCH_NETWORK": [
        "Architecture Overview", "Network Topology", "Segmentation Strategy",
        "Traffic Flows", "Security Controls", "Diagrams", "Decision Log",
    ],
    "ARCH_APPLICATION": [
        "System Context", "Component Diagram", "API Contracts",
        "Data Flow", "Security Considerations", "Deployment Architecture", "Decision Log",
    ],
    "ARCH_SYSTEM": [
        "Mission and Goals", "Stakeholders", "System Boundary",
        "Key Components", "Interfaces", "Quality Attributes", "Decision Log",
    ],
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

    # Tech writer templates skip HITL — authors own approval.
    is_techwriter = template_meta.get("category") == "techwriter"
    from tools.document_intelligence.constants import TEMPLATE_TYPE_TO_WRITEGUARD_MODE
    wg_mode = TEMPLATE_TYPE_TO_WRITEGUARD_MODE.get(template_id, "default") if is_techwriter else "default"
    doc_origin = "human_authored" if is_techwriter else "template"
    doc_status = "approved" if is_techwriter else "draft"

    conn = _conn()
    try:
        cur = conn.cursor()
        # collection_id defaults to "default" above — a collection that has never
        # had a row. Without this the instantiated document is invisible.
        ensure_collection(conn, collection_id, tenant_id=tenant_id, classification=classification)
        cur.execute(
            """
            INSERT OR REPLACE INTO dic_documents
                (doc_id, collection_id, source_id, filename, filepath,
                 content_type, provider, title, byte_size, content_sha256,
                 page_count, created_at, tenant_id, classification,
                 template_type, writeguard_mode)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                doc_id, collection_id, doc_id, f"{template_id}-template.md", "",
                "text/markdown", "template", template_meta["name"], 0, "",
                len(sections), now, tenant_id, classification,
                template_id if is_techwriter else None, wg_mode,
            ),
        )
        cur.execute(
            """
            INSERT OR REPLACE INTO dic_versions
                (version_id, doc_id, version_no, origin, status,
                 content_sha256, created_at, created_by, tenant_id, classification)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                version_id, doc_id, 1, doc_origin, doc_status,
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    section_id, version_id, doc_id, heading, "",
                    "[]", doc_status, doc_origin, now, created_by, tenant_id, classification,
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
    """Regenerate a single section with targeted evidence retrieval.

    Body fields:
      version_id    : required
      heading       : required
      collection_id : optional (default 'default')
      patch_mode    : optional bool; when true, prompt produces minimal diff with [KEEP] markers
      change_context: optional str; canvas event payload summary prepended to evidence
    """
    data = request.get_json(silent=True) or {}
    version_id = (data.get("version_id") or "").strip()
    heading = (data.get("heading") or "").strip()
    collection_id = data.get("collection_id", "default")
    patch_mode = bool(data.get("patch_mode", False))
    change_context = (data.get("change_context") or "").strip()
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
            patch_mode=patch_mode, change_context=change_context,
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
            "FROM dic_sections WHERE version_id = %s ORDER BY section_id",
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


# ── API: Style Gate ───────────────────────────────────────────────────────────

@dic_bp.route("/api/sections/<section_id>/style-check", methods=["POST"])
def api_section_style_check(section_id: str):
    """Run the style gate against a single section's current content."""
    from tools.document_intelligence.style_engine import check_style
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT heading, content FROM dic_sections WHERE section_id = %s LIMIT 1",
            (section_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "section not found"}), 404
        content = (row["content"] or "").strip()
        if not content:
            return jsonify({"score": 100.0, "passed": True, "violations": [], "stats": {}}), 200
        result = check_style(content)
        return jsonify(result.to_dict())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


@dic_bp.route("/api/versions/<version_id>/style-check", methods=["POST"])
def api_version_style_check(version_id: str):
    """Run the style gate across all sections in a version."""
    from tools.document_intelligence.style_engine import check_sections
    conn = _conn()
    try:
        rows = _safe_rows(
            conn,
            "SELECT heading, content FROM dic_sections WHERE version_id = %s ORDER BY section_id",
            (version_id,),
        )
        result = check_sections([dict(r) for r in rows])
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()


# ── API: Version Diff ─────────────────────────────────────────────────────────

@dic_bp.route("/api/versions/<version_a>/diff/<version_b>", methods=["GET"])
def api_version_diff(version_a: str, version_b: str):
    """Compute a line-by-line diff between two versions, section by section.

    Returns JSON::

        {
          "version_a": "...", "version_b": "...",
          "sections": [
            {
              "heading": "...",
              "in_a": true, "in_b": true,
              "lines": [
                {"tag": "equal"|"insert"|"delete", "text": "..."},
                ...
              ],
              "added": 3, "removed": 2
            }
          ],
          "total_added": N, "total_removed": N
        }
    """
    import difflib

    conn = _conn()
    try:
        def _get_sections(vid):
            rows = _safe_rows(
                conn,
                "SELECT heading, content FROM dic_sections WHERE version_id = %s ORDER BY section_id",
                (vid,),
            )
            return {(r.get("heading") or "").strip(): (r.get("content") or "") for r in rows}

        secs_a = _get_sections(version_a)
        secs_b = _get_sections(version_b)
        all_headings = list(dict.fromkeys(list(secs_a.keys()) + list(secs_b.keys())))

        results = []
        total_added = total_removed = 0

        for heading in all_headings:
            text_a = secs_a.get(heading, "")
            text_b = secs_b.get(heading, "")
            lines_a = text_a.splitlines()
            lines_b = text_b.splitlines()

            diff_lines = []
            added = removed = 0
            matcher = difflib.SequenceMatcher(None, lines_a, lines_b, autojunk=False)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes():
                if tag == "equal":
                    for line in lines_a[i1:i2]:
                        diff_lines.append({"tag": "equal", "text": line})
                elif tag in ("replace", "delete"):
                    for line in lines_a[i1:i2]:
                        diff_lines.append({"tag": "delete", "text": line})
                        removed += 1
                if tag in ("replace", "insert"):
                    for line in lines_b[j1:j2]:
                        diff_lines.append({"tag": "insert", "text": line})
                        added += 1

            total_added += added
            total_removed += removed
            results.append({
                "heading": heading,
                "in_a": heading in secs_a,
                "in_b": heading in secs_b,
                "lines": diff_lines,
                "added": added,
                "removed": removed,
            })

        return jsonify({
            "version_a": version_a,
            "version_b": version_b,
            "sections": results,
            "total_added": total_added,
            "total_removed": total_removed,
        })
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
    # Omitted collection_id scans EVERY collection. The old literal 'default'
    # meant the Scan-now button silently never scored real collections, so
    # documents with findings stayed invisible on the freshness board.
    collection_id = (data.get("collection_id") or "").strip() or None
    tenant_id, classification = _security_context()
    try:
        from tools.document_intelligence.freshness_engine import scan_collection

        if collection_id is None:
            conn = _conn()
            try:
                cids = [dict(r)["collection_id"] for r in conn.execute(
                    "SELECT collection_id FROM dic_collections"
                ).fetchall()] or ["default"]
            finally:
                conn.close()
            totals = {"collections_scanned": 0, "docs_scanned": 0,
                      "stale_count": 0, "aging_count": 0, "fresh_count": 0}
            for cid in cids:
                try:
                    r = scan_collection(cid, tenant_id=tenant_id, classification=classification)
                    totals["collections_scanned"] += 1
                    totals["docs_scanned"] += getattr(r, "docs_scanned", 0) or 0
                    totals["stale_count"] += getattr(r, "stale_count", 0) or 0
                    totals["aging_count"] += getattr(r, "aging_count", 0) or 0
                    totals["fresh_count"] += getattr(r, "fresh_count", 0) or 0
                except Exception as exc:
                    logger.warning("dic freshness: collection %s scan failed: %s", cid, exc)
            return jsonify(totals)

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
            detect_ingest_anomalies, detect_patterns, run_full_analytics,
            detect_view_anomalies, detect_ingest_job_anomalies,
            detect_output_export_anomalies, detect_document_model_anomalies,
            detect_bulk_edit_anomalies, detect_ingest_throughput_anomaly,
        )
        if mode == "frequency":
            return jsonify(entity_frequency(limit=int(data.get("limit", 50))))
        elif mode == "cooccurrence":
            return jsonify(co_occurrence(limit=int(data.get("limit", 60))))
        elif mode == "anomalies":
            return jsonify(detect_anomalies())
        elif mode == "ingest_anomalies":
            return jsonify(detect_ingest_anomalies(
                collection_id=data.get("collection_id") or None
            ))
        elif mode == "view_anomalies":
            return jsonify(detect_view_anomalies(
                collection_id=data.get("collection_id") or None,
                window_days=int(data.get("window_days", 30)),
            ))
        elif mode == "job_anomalies":
            return jsonify(detect_ingest_job_anomalies(
                collection_id=data.get("collection_id") or None,
                stale_minutes=int(data.get("stale_minutes", 60)),
            ))
        elif mode == "output_export_anomalies":
            return jsonify(detect_output_export_anomalies(
                collection_id=data.get("collection_id") or None,
                stale_minutes=int(data.get("stale_minutes", 30)),
            ))
        elif mode == "model_anomalies":
            return jsonify(detect_document_model_anomalies(
                collection_id=data.get("collection_id") or None,
            ))
        elif mode == "bulk_edit_anomalies":
            return jsonify(detect_bulk_edit_anomalies(
                collection_id=data.get("collection_id") or None,
            ))
        elif mode == "throughput_anomaly":
            return jsonify(detect_ingest_throughput_anomaly(
                collection_id=data.get("collection_id") or None,
                lookback_days=int(data.get("lookback_days", 30)),
            ))
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

# ── Presence Registry (rted-pres-01/02) ─────────────────────────────────────

@dic_bp.route("/api/documents/<doc_id>/presence/join", methods=["POST"])
def api_presence_join(doc_id: str):
    from tools.document_intelligence.presence_registry import join_document
    data = request.get_json(silent=True) or {}
    user_id = (data.get("user_id") or _current_user()).strip() or "anonymous"
    _, classification = _security_context()
    tenant_id, _ = _security_context()
    session_key = join_document(doc_id, user_id, tenant_id=tenant_id)
    return jsonify({"session_key": session_key, "doc_id": doc_id, "user_id": user_id})


@dic_bp.route("/api/documents/<doc_id>/presence/heartbeat", methods=["POST"])
def api_presence_heartbeat(doc_id: str):
    from tools.document_intelligence.presence_registry import heartbeat
    data = request.get_json(silent=True) or {}
    session_key = (data.get("session_key") or "").strip()
    if not session_key:
        return jsonify({"error": "session_key required"}), 400
    found = heartbeat(session_key)
    return jsonify({"ok": found, "session_key": session_key})


@dic_bp.route("/api/documents/<doc_id>/presence/leave", methods=["DELETE"])
def api_presence_leave(doc_id: str):
    from tools.document_intelligence.presence_registry import leave_document
    data = request.get_json(silent=True) or {}
    session_key = (data.get("session_key") or "").strip()
    if not session_key:
        return jsonify({"error": "session_key required"}), 400
    left = leave_document(session_key)
    return jsonify({"left": left, "session_key": session_key})


@dic_bp.route("/api/documents/<doc_id>/presence", methods=["GET"])
def api_presence_list(doc_id: str):
    from tools.document_intelligence.presence_registry import get_present_users
    users = get_present_users(doc_id)
    return jsonify({"doc_id": doc_id, "users": users, "count": len(users)})


@dic_bp.route("/api/documents/<doc_id>/presence/stream", methods=["GET"])
def api_presence_stream(doc_id: str):
    """SSE stream — emits the current presence list every 12 s."""
    import time as _time
    from tools.document_intelligence.presence_registry import (
        get_present_users,
        _SSE_POLL_INTERVAL,
    )

    @stream_with_context
    def _generate():
        try:
            for _ in range(300):  # max ~1 hr (300 × 12 s)
                users = get_present_users(doc_id)
                payload = json.dumps({"doc_id": doc_id, "users": users, "count": len(users)})
                yield f"data: {payload}\n\n"
                _time.sleep(_SSE_POLL_INTERVAL)
        except GeneratorExit:
            pass

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@dic_bp.route("/api/suggestions", methods=["GET"])
def api_suggestions_list():
    """List suggestions, filtered by optional query params.

    Query params: collection_id, canvas_source, status (default: pending)
    """
    from tools.document_intelligence.suggestion_store import get_pending_suggestions
    collection_id = request.args.get("collection_id") or None
    canvas_source = request.args.get("canvas_source") or None
    status = request.args.get("status", "pending")
    suggestions = get_pending_suggestions(
        collection_id=collection_id,
        canvas_source=canvas_source,
        status=status,
    )
    return jsonify({"suggestions": suggestions, "count": len(suggestions)})


@dic_bp.route("/api/suggestions/<suggestion_id>", methods=["GET"])
def api_suggestion_detail(suggestion_id: str):
    """Return full detail for a single suggestion."""
    from tools.document_intelligence.suggestion_store import get_suggestion
    s = get_suggestion(suggestion_id)
    if s is None:
        return jsonify({"error": "suggestion not found"}), 404
    return jsonify(s)


@dic_bp.route("/api/suggestions/<suggestion_id>/accept", methods=["POST"])
def api_suggestion_accept(suggestion_id: str):
    """Accept a suggestion: apply suggested_content to the section + record history."""
    from tools.document_intelligence.suggestion_store import (
        get_suggestion, decide_suggestion,
    )
    s = get_suggestion(suggestion_id)
    if s is None:
        return jsonify({"error": "suggestion not found"}), 404

    cid = s.get("collection_id") or _collection_id_from_section(s.get("section_id", "")) or "default"
    if not _require_role(cid, "editor"):
        return _forbid("editor")

    if s.get("status") != "pending":
        return jsonify({"error": "suggestion already decided", "status": s["status"]}), 409

    section_id = s.get("section_id", "")
    suggested_content = s.get("suggested_content", "")
    user = _current_user()

    # Apply content to the section (reuse section update logic)
    conn = _conn()
    try:
        before_row = conn.execute(
            "SELECT content FROM dic_sections WHERE section_id = %s LIMIT 1", (section_id,)
        ).fetchone()
        before_content = (dict(before_row).get("content") or "") if before_row else ""

        conn.execute(
            "UPDATE dic_sections SET content = %s, status = %s, origin = %s, created_at = %s WHERE section_id = %s",
            (suggested_content, "draft", "ai_generated", _now(), section_id),
        )
        conn.commit()
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        conn.close()

    # Record edit history (best-effort)
    try:
        from tools.document_intelligence.history_recorder import record_edit
        record_edit(
            section_id=section_id,
            editor=user,
            content_before=before_content,
            content_after=suggested_content,
        )
    except Exception:
        pass

    # Record the accept decision (marks suggestion as accepted)
    data = request.get_json(silent=True) or {}
    note = data.get("note", "")
    _, classification = _security_context()
    tenant_id, _ = _security_context()
    decide_suggestion(
        suggestion_id, "accepted", user,
        note=note, tenant_id=tenant_id, classification=classification,
    )

    from tools.document_intelligence.conflict_detector import compute_hash
    return jsonify({
        "status": "accepted",
        "suggestion_id": suggestion_id,
        "section_id": section_id,
        "new_hash": compute_hash(suggested_content),
    })


@dic_bp.route("/api/suggestions/<suggestion_id>/reject", methods=["POST"])
def api_suggestion_reject(suggestion_id: str):
    """Reject a suggestion with an optional note. Requires editor or reviewer role."""
    from tools.document_intelligence.suggestion_store import (
        get_suggestion, decide_suggestion,
    )
    s = get_suggestion(suggestion_id)
    if s is None:
        return jsonify({"error": "suggestion not found"}), 404

    cid = s.get("collection_id") or _collection_id_from_section(s.get("section_id", "")) or "default"
    if not _require_role(cid, "editor"):
        return _forbid("editor")

    if s.get("status") != "pending":
        return jsonify({"error": "suggestion already decided", "status": s["status"]}), 409

    data = request.get_json(silent=True) or {}
    note = data.get("note", "")
    user = _current_user()
    tenant_id, classification = _security_context()

    result = decide_suggestion(
        suggestion_id, "rejected", user,
        note=note, tenant_id=tenant_id, classification=classification,
    )
    if not result:
        return jsonify({"error": "could not record rejection"}), 500

    return jsonify({"status": "rejected", "suggestion_id": suggestion_id})


@dic_bp.route("/api/sections/<section_id>/suggest", methods=["POST"])
def api_section_suggest(section_id: str):
    """dsyn-suggest-01: Any viewer-or-above user can submit a crowdsourced edit suggestion.

    Body: { proposed_content: str, rationale: str }
    Creates a dic_suggestions row with canvas_source='crowdsource'.
    """
    from tools.document_intelligence.suggestion_store import create_suggestion

    cid = _collection_id_from_section(section_id) or "default"
    if not _require_role(cid, "viewer"):
        return _forbid("viewer")

    data = request.get_json(silent=True) or {}
    proposed_content = (data.get("proposed_content") or "").strip()
    rationale = (data.get("rationale") or "").strip()

    if not proposed_content:
        return jsonify({"error": "proposed_content is required"}), 400

    user = _current_user()
    tenant_id, classification = _security_context()

    # Load current section content for context.
    current_content = ""
    try:
        conn = _conn()
        cur = conn.execute(
            "SELECT content FROM dic_sections WHERE section_id = %s LIMIT 1",
            (section_id,),
        )
        row = cur.fetchone()
        if row:
            current_content = row[0] if isinstance(row, (list, tuple)) else row["content"]
        conn.close()
    except Exception:
        pass

    suggestion_id = create_suggestion(
        section_id=section_id,
        collection_id=cid,
        canvas_source="crowdsource",
        suggested_content=proposed_content,
        current_content=current_content,
        rationale=rationale or f"User suggestion from {user}",
        tenant_id=tenant_id,
        classification=classification,
    )

    return jsonify({
        "suggestion_id": suggestion_id,
        "section_id": section_id,
        "canvas_source": "crowdsource",
        "status": "pending",
    }), 201


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


# ── API: Presence stream + ping ───────────────────────────────────────────────

@dic_bp.route("/api/doc/<doc_id>/presence/stream", methods=["GET"])
def api_doc_presence_stream(doc_id: str):
    """SSE stream: emit 'presence' event with active user list every 10 s."""
    from tools.document_intelligence.presence_registry import get_presence as _get_presence

    def _generate():
        polls = 0
        while True:
            users = _get_presence(doc_id)
            yield f"event: presence\ndata: {json.dumps(users)}\n\n"
            polls += 1
            if _STREAM_MAX_POLLS is not None and polls >= _STREAM_MAX_POLLS:
                return
            time.sleep(10)

    return Response(
        stream_with_context(_generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@dic_bp.route("/api/doc/<doc_id>/presence/ping", methods=["POST"])
def api_doc_presence_ping(doc_id: str):
    """Heartbeat: update active_section_id for current user. Returns 204."""
    from tools.document_intelligence.presence_registry import ping as _ping

    body = request.get_json(silent=True) or {}
    section_id = body.get("section_id", "")
    user_id = _current_user()
    _ping(doc_id, user_id, section_id)
    return ("", 204)


# ── Dual-mode detection ───────────────────────────────────────────────────────

def _llm_mode_info() -> dict:
    """Return current LLM mode: air-gap vs online, active provider.

    Provider priority (mirrors output_generators._try_llm):
      1. Cloud API  — ANTHROPIC_API_KEY / OPENAI_API_KEY present
      2. Claude CLI — auto-detected when no cloud keys (``claude`` on PATH)
      3. Ollama     — last resort
    """
    import os, shutil  # noqa: E401
    try:
        from tools.airgap import is_airgap
        air_gap = is_airgap()
    except Exception:
        air_gap = os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")

    has_cloud_keys = bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("AZURE_OPENAI_API_KEY")
    )
    cli_binary = os.environ.get("ICDEV_CLI_BRIDGE_BINARY") or "claude"
    cli_available = bool(shutil.which(cli_binary))

    provider = "air-gap (local Ollama)"
    llm_available = False
    try:
        from tools.llm.router import LLMRouter
        router = LLMRouter()
        llm_available = router.has_any_llm()

        if has_cloud_keys and not air_gap:
            p, m, _ = router.get_provider_for_function("document_qna")
            p_name = getattr(p, "provider_name", None) or getattr(p, "name", None) or type(p).__name__
            provider = f"{p_name} / {m}"
        elif cli_available and not air_gap:
            provider = f"claude-cli ({cli_binary})"
            llm_available = True
        elif llm_available:
            p, m, _ = router.get_provider_for_function("document_qna")
            p_name = getattr(p, "provider_name", None) or getattr(p, "name", None) or type(p).__name__
            provider = f"{p_name} / {m} (last resort)"
    except Exception:
        if cli_available and not air_gap:
            provider = f"claude-cli ({cli_binary})"
            llm_available = True

    return {
        "mode": "air-gap" if air_gap else "online",
        "llm_available": llm_available,
        "provider": provider,
        "cli_bridge_available": cli_available,
        "cloud_keys_present": has_cloud_keys,
        "capabilities": {
            "url_ingest": True,
            "video_ingest": True,  # yt-dlp works offline; YouTube transcript API is online-only
            "study_guide": True,
            "faq": True,
            "timeline": True,
            "audio_overview": True,
            "llm_synthesis": llm_available,
        },
    }


@dic_bp.route("/api/mode", methods=["GET"])
def api_mode():
    """GET /document-intelligence/api/mode — return LLM mode and capabilities."""
    return jsonify(_llm_mode_info())


# ── Notebook page (NotebookLM-style unified view) ─────────────────────────────

@dic_bp.route("/notebook")
@dic_bp.route("/notebook/<collection_id>")
def notebook(collection_id: str = "default"):
    """NotebookLM-style unified view: sources + chat + generated outputs."""
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        docs = _safe_rows(
            conn,
            "SELECT doc_id AS id, title, filename, provider AS source_type, created_at "
            "FROM dic_documents "
            "WHERE collection_id = %s AND tenant_id = %s ORDER BY created_at DESC LIMIT 50",
            (collection_id, tenant_id),
        )
        outputs = _safe_rows(
            conn,
            "SELECT id, output_type, provider, status, created_at FROM dic_generated_outputs "
            "WHERE collection_id = %s AND tenant_id = %s ORDER BY created_at DESC LIMIT 20",
            (collection_id, tenant_id),
        )
    finally:
        conn.close()

    mode_info = _llm_mode_info()
    return render_template(
        "document_intelligence/notebook.html",
        collection_id=collection_id,
        docs=docs,
        outputs=outputs,
        mode_info=mode_info,
    )


# ── URL + YouTube ingest ──────────────────────────────────────────────────────

@dic_bp.route("/api/ingest/url", methods=["POST"])
def api_ingest_url():
    """POST /document-intelligence/api/ingest/url — ingest from a web URL."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "url must start with http:// or https://"}), 400

    collection_id = (body.get("collection_id") or "default").strip()
    classification = (body.get("classification") or "CUI").strip()
    tenant_id, _ = _security_context()

    try:
        from tools.document_intelligence.extractors import extract_url
        extraction = extract_url(url)
    except Exception as exc:
        return jsonify({"error": f"extraction failed: {exc}"}), 500

    if not extraction.text:
        return jsonify({
            "warning": "No text extracted — site may block automated access or require auth.",
            "warnings": extraction.warnings,
            "url": url,
        }), 206

    # Persist as a text file through the ingest orchestrator
    try:
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as f:
            f.write(extraction.text)
            tmp_path = f.name

        from tools.document_intelligence.ingest_orchestrator import ingest_file
        outcome = ingest_file(
            tmp_path, collection_id,
            tenant_id=tenant_id, classification=classification,
            created_by="dic_url_ingest",
        )
        os.unlink(tmp_path)
        return jsonify({
            "doc_id": outcome.doc_id,
            "chunks": outcome.chunks,
            "title": extraction.title,
            "url": url,
            "warnings": extraction.warnings + outcome.errors,
        })
    except Exception as exc:
        return jsonify({"error": f"ingest failed: {exc}"}), 500


@dic_bp.route("/api/ingest/youtube", methods=["POST"])
def api_ingest_youtube():
    """POST /document-intelligence/api/ingest/youtube — ingest YouTube transcript."""
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    collection_id = (body.get("collection_id") or "default").strip()
    classification = (body.get("classification") or "CUI").strip()
    tenant_id, _ = _security_context()

    try:
        from tools.document_intelligence.extractors import extract_youtube
        extraction = extract_youtube(url)
    except Exception as exc:
        return jsonify({"error": f"extraction failed: {exc}"}), 500

    if not extraction.text:
        return jsonify({
            "warning": "No transcript extracted.",
            "warnings": extraction.warnings,
            "url": url,
            "hint": extraction.warnings[0] if extraction.warnings else "",
        }), 206

    try:
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as f:
            f.write(extraction.text)
            tmp_path = f.name

        from tools.document_intelligence.ingest_orchestrator import ingest_file
        outcome = ingest_file(
            tmp_path, collection_id,
            tenant_id=tenant_id, classification=classification,
            created_by="dic_youtube_ingest",
        )
        os.unlink(tmp_path)
        return jsonify({
            "doc_id": outcome.doc_id,
            "chunks": outcome.chunks,
            "title": extraction.title,
            "url": url,
            "warnings": extraction.warnings + outcome.errors,
        })
    except Exception as exc:
        return jsonify({"error": f"ingest failed: {exc}"}), 500


# ── Output generators ─────────────────────────────────────────────────────────

def _run_generator(generator_fn, collection_id: str, tenant_id: str, **kwargs) -> tuple:
    try:
        result = generator_fn(collection_id, tenant_id, **kwargs)
        if "error" in result:
            return jsonify(result), 400
        # Bridge study-guide key_terms into canvas_kg_nodes so the Ontology canvas
        # can surface DIC-extracted concepts alongside design-canvas entities.
        _maybe_bridge_to_kg(result, collection_id)
        return jsonify(result)
    except Exception as exc:
        logger.warning("dic: generator error: %s", exc)
        return jsonify({"error": str(exc)}), 500


def _maybe_bridge_to_kg(result: dict, collection_id: str) -> None:
    """Fire-and-forget: write key_terms/timeline events to canvas_kg_nodes."""
    key_terms = result.get("key_terms") or []
    events = result.get("events") or []
    output_id = result.get("output_id") or collection_id
    if not key_terms and not events:
        return
    try:
        from tools.canvas.kg_builder import upsert_from_dic
        entities: list[dict] = []
        if key_terms:
            for term in key_terms:
                entities.append({"id": str(term).lower().replace(" ", "_"), "label": str(term), "type": "concept"})
        if events:
            for ev in events:
                label = f"{ev.get('date','?')}: {ev.get('event','')}"[:100]
                entities.append({"id": label.lower().replace(" ", "_")[:60], "label": label, "type": "event"})
        # Co-occurrence edges: link consecutive concept pairs (shallow but deterministic)
        relationships: list[dict] = []
        for i in range(len(entities) - 1):
            relationships.append({
                "source": entities[i]["id"],
                "target": entities[i + 1]["id"],
                "type": "co_occurs",
            })
        upsert_from_dic(output_id, entities, relationships)
    except Exception as exc:
        logger.debug("dic: kg bridge skipped: %s", exc)


@dic_bp.route("/api/generate/study-guide", methods=["POST"])
def api_generate_study_guide():
    """POST /document-intelligence/api/generate/study-guide"""
    body = request.get_json(silent=True) or {}
    collection_id = (body.get("collection_id") or "default").strip()
    doc_id = (body.get("doc_id") or "").strip() or None
    tenant_id, _ = _security_context()
    from tools.document_intelligence.output_generators import generate_study_guide
    return _run_generator(generate_study_guide, collection_id, tenant_id, doc_id=doc_id)


@dic_bp.route("/api/generate/faq", methods=["POST"])
def api_generate_faq():
    """POST /document-intelligence/api/generate/faq"""
    body = request.get_json(silent=True) or {}
    collection_id = (body.get("collection_id") or "default").strip()
    doc_id = (body.get("doc_id") or "").strip() or None
    n = int(body.get("n", 10))
    tenant_id, _ = _security_context()
    from tools.document_intelligence.output_generators import generate_faq
    return _run_generator(generate_faq, collection_id, tenant_id, n=n, doc_id=doc_id)


@dic_bp.route("/api/generate/timeline", methods=["POST"])
def api_generate_timeline():
    """POST /document-intelligence/api/generate/timeline"""
    body = request.get_json(silent=True) or {}
    collection_id = (body.get("collection_id") or "default").strip()
    doc_id = (body.get("doc_id") or "").strip() or None
    tenant_id, _ = _security_context()
    from tools.document_intelligence.output_generators import generate_timeline
    return _run_generator(generate_timeline, collection_id, tenant_id, doc_id=doc_id)


@dic_bp.route("/api/generate/audio", methods=["POST"])
def api_generate_audio():
    """POST /document-intelligence/api/generate/audio"""
    body = request.get_json(silent=True) or {}
    collection_id = (body.get("collection_id") or "default").strip()
    doc_id = (body.get("doc_id") or "").strip() or None
    tenant_id, _ = _security_context()
    from tools.document_intelligence.output_generators import generate_audio_overview
    return _run_generator(generate_audio_overview, collection_id, tenant_id, doc_id=doc_id)


@dic_bp.route("/api/kg-from-chunks", methods=["POST"])
def api_kg_from_chunks():
    """Extract entities from ingested chunk text — always scoped to the DIC collection."""
    import re as _re
    import html as _html
    import collections as _col
    body = request.get_json(silent=True) or {}
    collection_id = (body.get("collection_id") or "default").strip()
    doc_id = (body.get("doc_id") or "").strip() or None
    limit = min(int(body.get("limit", 30)), 60)
    tenant_id, _ = _security_context()

    from tools.document_intelligence.output_generators import _get_chunks, _kg_entities_for_collection

    # Try KG entities first (populated by LLM extraction during enhancement)
    kg_ents = _kg_entities_for_collection(collection_id, tenant_id, doc_id, limit=limit)
    if kg_ents:
        # Convert to node format with IDs
        nodes = [{"id": i, "label": e, "entity_type": "CONCEPT", "centrality": 1 - i / len(kg_ents)}
                 for i, e in enumerate(kg_ents)]
        return jsonify({"nodes": nodes, "edges": [], "count": len(nodes), "source": "kg"})

    # Fall back: extract entities from chunk text using proper noun regex
    chunks = _get_chunks(collection_id, tenant_id, limit=150, doc_id=doc_id)
    if not chunks:
        return jsonify({"nodes": [], "edges": [], "count": 0, "source": "none",
                        "hint": "No documents ingested yet."})

    all_text = " ".join(_html.unescape(c.get("chunk_text", "")) for c in chunks)
    noise = {
        "The", "This", "That", "Section", "Article", "Amendment", "Such", "Any", "All",
        "Each", "Not", "Its", "Their", "His", "Her", "United", "States", "And", "But",
        "President", "Congress", "Senate", "House", "Court", "Constitution", "With",
        "From", "When", "Where", "Which", "What", "How", "Who", "Was", "Were", "Has",
        "Skip", "Navigation", "Markets", "Currencies", "Prediction",
    }
    freq: dict = _col.Counter()
    for m in _re.finditer(r"\b([A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,}){0,2})\b", all_text):
        term = m.group(1)
        if term not in noise:
            freq[term] += 1

    if not freq:
        return jsonify({"nodes": [], "edges": [], "count": 0, "source": "none",
                        "hint": "No named entities found in chunks."})

    _DATE = _re.compile(r"January|February|March|April|May|June|July|August|"
                        r"September|October|November|December|\b\d{4}\b", _re.I)
    _ORG_ENDS = {"Act", "Association", "Committee", "Congress", "Corporation",
                 "Department", "Foundation", "Institute", "League", "Party", "Agency"}
    _PERSON = _re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+$")

    max_f = max(freq.values())
    top_terms = [t for t, _ in freq.most_common(limit)]
    nodes = []
    label_to_id: dict = {}
    for i, term in enumerate(top_terms):
        count = freq[term]
        if _DATE.search(term):
            etype = "DATE"
        elif any(term.endswith(s) for s in _ORG_ENDS):
            etype = "ORG"
        elif _PERSON.match(term):
            etype = "PERSON"
        else:
            etype = "CONCEPT"
        nodes.append({
            "id": i, "label": term, "entity_type": etype,
            "centrality": round(count / max_f, 3), "count": count,
        })
        label_to_id[term.lower()] = i

    # Co-occurrence edges: entities that appear together in the same chunk
    edge_freq: dict = _col.Counter()
    for c in chunks:
        text = _html.unescape(c.get("chunk_text", "")).lower()
        present = [nid for lbl, nid in label_to_id.items() if lbl in text]
        for a in range(len(present)):
            for b in range(a + 1, len(present)):
                pair = (min(present[a], present[b]), max(present[a], present[b]))
                edge_freq[pair] += 1

    edges = [
        {"source_id": a, "target_id": b, "relationship": "co-occurs", "weight": w}
        for (a, b), w in edge_freq.most_common(60)
        if w >= 2  # only show pairs that co-occur in ≥2 chunks
    ]

    return jsonify({"nodes": nodes, "edges": edges, "count": len(nodes), "source": "chunks"})


@dic_bp.route("/api/generate/enhance", methods=["POST"])
def api_generate_enhance():
    """POST /document-intelligence/api/generate/enhance — layer LLM on a BM25+KG output.

    Body: {output_id, output_type, collection_id, doc_id (opt), n (opt)}
    Runs _try_llm with a 90s ceiling and updates the stored output in place.
    """
    body = request.get_json(silent=True) or {}
    output_id = (body.get("output_id") or "").strip()
    output_type = (body.get("output_type") or "study_guide").strip().replace("-", "_")
    collection_id = (body.get("collection_id") or "default").strip()
    doc_id = (body.get("doc_id") or "").strip() or None
    n = int(body.get("n", 10))
    tenant_id, _ = _security_context()
    if not output_id:
        return jsonify({"error": "output_id required"}), 400
    from tools.document_intelligence.output_generators import enhance_with_llm
    try:
        result = enhance_with_llm(output_id, collection_id, tenant_id,
                                  output_type, doc_id=doc_id, n=n)
        if "error" in result or "enhance_error" in result:
            return jsonify(result), 400
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/outputs", methods=["GET"])
def api_outputs_list():
    """GET /document-intelligence/api/outputs — list generated outputs for a collection."""
    collection_id = (request.args.get("collection_id") or "default").strip()
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        rows = _safe_rows(
            conn,
            "SELECT id, output_type, provider, status, created_at FROM dic_generated_outputs "
            "WHERE collection_id = %s AND tenant_id = %s ORDER BY created_at DESC LIMIT 50",
            (collection_id, tenant_id),
        )
    finally:
        conn.close()
    return jsonify({"outputs": rows, "collection_id": collection_id})


@dic_bp.route("/api/outputs/<output_id>", methods=["GET"])
def api_output_detail(output_id: str):
    """GET /document-intelligence/api/outputs/<id> — get generated output content."""
    tenant_id, _ = _security_context()
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, output_type, content_json, provider, status, created_at "
            "FROM dic_generated_outputs WHERE id = %s AND tenant_id = %s",
            (output_id, tenant_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "output not found"}), 404
    import json as _json
    d = dict(row) if hasattr(row, "keys") else {
        "id": row[0], "output_type": row[1], "content_json": row[2],
        "provider": row[3], "status": row[4], "created_at": row[5],
    }
    try:
        d["content"] = _json.loads(d.pop("content_json", "{}"))
    except Exception:
        d["content"] = {}
    return jsonify(d)


@dic_bp.route("/api/generate/tasks", methods=["POST"])
def api_generate_tasks():
    """POST /document-intelligence/api/generate/tasks — seed kanban tasks from a generated output.

    Body: {output_id, collection_id, doc_id (opt)}
    Extracts action items from study_guide (key_points) or faq (pairs) outputs and
    seeds them as backlog kanban tasks linked back to the source document.
    """
    import uuid as _uuid
    body = request.get_json(silent=True) or {}
    output_id = (body.get("output_id") or "").strip()
    collection_id = (body.get("collection_id") or "default").strip()
    doc_id = (body.get("doc_id") or "").strip() or None
    tenant_id, _ = _security_context()
    if not output_id:
        return jsonify({"error": "output_id required"}), 400

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT output_type, content_json FROM dic_generated_outputs "
            "WHERE id = %s AND tenant_id = %s",
            (output_id, tenant_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "output not found"}), 404

    output_type = row[0] if isinstance(row, (tuple, list)) else row["output_type"]
    raw_json = row[1] if isinstance(row, (tuple, list)) else row["content_json"]
    try:
        content = json.loads(raw_json or "{}")
    except Exception:
        content = {}

    prefix = f"dic-nb-{collection_id[:6]}"
    tag = _uuid.uuid4().hex[:6]
    task_specs: list[dict] = []

    if output_type == "study_guide":
        for i, point in enumerate(content.get("key_points", [])[:20]):
            task_specs.append({
                "id": f"{prefix}-sg-{tag}-{i:02d}",
                "title": point[:200],
                "description": (
                    f"Action item extracted from DIC Study Guide (collection: {collection_id}). "
                    f"Source output: {output_id}."
                    + (f" Source document: {doc_id}." if doc_id else "")
                ),
                "task_type": "build",
                "priority": "medium",
                "status": "backlog",
                "source_doc_id": doc_id,
                "source_collection_id": collection_id,
            })
    elif output_type == "faq":
        for i, pair in enumerate(content.get("pairs", [])[:20]):
            task_specs.append({
                "id": f"{prefix}-fq-{tag}-{i:02d}",
                "title": str(pair.get("q", ""))[:200],
                "description": (
                    str(pair.get("a", ""))[:1000]
                    + f"\n\nSource: DIC FAQ output {output_id}, collection {collection_id}."
                    + (f" Document: {doc_id}." if doc_id else "")
                ),
                "task_type": "build",
                "priority": "medium",
                "status": "backlog",
                "source_doc_id": doc_id,
                "source_collection_id": collection_id,
            })
    else:
        return jsonify({"error": f"task extraction not supported for output type '{output_type}'"}), 400

    if not task_specs:
        return jsonify({"task_ids": [], "count": 0, "message": "No action items found in output"})

    try:
        from tools.kanban.task_factory import create_tasks
        created = create_tasks(task_specs)
        return jsonify({"task_ids": created, "count": len(created),
                        "skipped": len(task_specs) - len(created)})
    except Exception as exc:
        logger.warning("dic: generate/tasks failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/generate/slides", methods=["POST"])
def api_generate_slides():
    """POST /document-intelligence/api/generate/slides — build a slide deck from a generated output.

    Body: {output_id, collection_id, title (opt), theme (opt)}
    Converts a study_guide or timeline output to slide dicts, persists to slides_decks,
    builds the .pptx, and returns {deck_id, url}.
    """
    body = request.get_json(silent=True) or {}
    output_id = (body.get("output_id") or "").strip()
    collection_id = (body.get("collection_id") or "default").strip()
    custom_title = (body.get("title") or "").strip() or None
    theme = (body.get("theme") or "midnight_executive").strip()
    tenant_id, _ = _security_context()
    if not output_id:
        return jsonify({"error": "output_id required"}), 400

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT output_type, content_json FROM dic_generated_outputs "
            "WHERE id = %s AND tenant_id = %s",
            (output_id, tenant_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "output not found"}), 404

    output_type = row[0] if isinstance(row, (tuple, list)) else row["output_type"]
    raw_json = row[1] if isinstance(row, (tuple, list)) else row["content_json"]
    try:
        content = json.loads(raw_json or "{}")
    except Exception:
        content = {}

    # Build slide dicts from DIC output
    slide_dicts: list[dict] = []
    deck_title = custom_title or f"DIC: {collection_id}"

    if output_type == "study_guide":
        deck_title = custom_title or f"Study Guide — {collection_id}"
        overview = content.get("overview", "")
        key_points = content.get("key_points", [])
        key_terms = content.get("key_terms", [])
        sources = content.get("sources", [])

        slide_dicts.append({
            "slide_type": "title",
            "title": deck_title,
            "bullets": sources[:3],
            "speaker_notes": overview,
        })
        for i in range(0, len(key_points), 4):
            chunk = key_points[i:i + 4]
            slide_dicts.append({
                "slide_type": "content",
                "title": f"Key Points ({i // 4 + 1})",
                "bullets": chunk,
            })
        if key_terms:
            slide_dicts.append({
                "slide_type": "content",
                "title": "Key Terms",
                "bullets": [f"• {t}" for t in key_terms[:10]],
            })
        slide_dicts.append({
            "slide_type": "outro",
            "title": "Sources",
            "bullets": sources[:6],
        })

    elif output_type == "timeline":
        deck_title = custom_title or f"Timeline — {collection_id}"
        events = content.get("events", [])

        slide_dicts.append({"slide_type": "title", "title": deck_title, "bullets": []})
        for i in range(0, len(events), 5):
            chunk = events[i:i + 5]
            slide_dicts.append({
                "slide_type": "content",
                "title": f"Timeline ({i // 5 + 1})",
                "bullets": [
                    f"{e.get('date', '?')}: {e.get('event', '')}"[:120]
                    for e in chunk
                ],
            })
        slide_dicts.append({"slide_type": "outro", "title": "End of Timeline", "bullets": []})
    else:
        return jsonify({"error": f"slides not supported for output type '{output_type}'"}), 400

    if not slide_dicts:
        return jsonify({"error": "No content to build slides from"}), 400

    try:
        from tools.slides.db.init_db import get_connection as _slides_conn, init_db as _slides_init
        from tools.slides import pptx_builder
        from datetime import datetime as _dt, timezone as _tz

        _slides_init()
        sconn = _slides_conn()
        try:
            cur = sconn.execute(
                "INSERT INTO slides_decks (title, deck_type, theme, status, source_types) "
                "VALUES (%s, %s, %s, 'running', %s) RETURNING deck_id",
                (deck_title, "executive_overview", theme, json.dumps(["dic"])),
            )
            row2 = cur.fetchone()
            sconn.commit()
            deck_id = int(row2[0]) if row2 else None
        except Exception:
            sconn.rollback()
            raise

        try:
            pptx_path = pptx_builder.build(slide_dicts, theme=theme, title=deck_title)
            now_iso = _dt.now(_tz.utc).isoformat()
            sconn.execute(
                "UPDATE slides_decks SET status='completed', slide_count=%s, pptx_path=%s, "
                "completed_at=%s WHERE deck_id=%s",
                (len(slide_dicts), pptx_path, now_iso, deck_id),
            )
            for i, sd in enumerate(slide_dicts):
                sconn.execute(
                    "INSERT INTO slides_slides "
                    "(deck_id, position, slide_type, title, bullets, speaker_notes) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (
                        deck_id, i + 1,
                        sd.get("slide_type", "content"),
                        sd.get("title", "")[:255],
                        json.dumps(sd.get("bullets", [])),
                        sd.get("speaker_notes", ""),
                    ),
                )
            sconn.commit()
        except Exception as exc:
            try:
                sconn.execute(
                    "UPDATE slides_decks SET status='failed', error_message=%s WHERE deck_id=%s",
                    (str(exc), deck_id),
                )
                sconn.commit()
            except Exception:
                pass
            raise
        finally:
            sconn.close()

        return jsonify({
            "deck_id": deck_id,
            "url": f"/slides/{deck_id}",
            "slide_count": len(slide_dicts),
            "title": deck_title,
        })
    except Exception as exc:
        logger.warning("dic: generate/slides failed: %s", exc)
        return jsonify({"error": str(exc)}), 500


@dic_bp.route("/api/generate/roadmap", methods=["POST"])
def api_generate_roadmap():
    """POST /document-intelligence/api/generate/roadmap — push timeline events to PMO milestones.

    Body: {output_id, contract_id, collection_id (opt)}
    Reads a timeline output and creates PMO milestones for each event.
    Returns {milestone_count, contract_id, milestone_ids}.
    """
    body = request.get_json(silent=True) or {}
    output_id = (body.get("output_id") or "").strip()
    contract_id = (body.get("contract_id") or "").strip()
    collection_id = (body.get("collection_id") or "default").strip()
    tenant_id, _ = _security_context()
    if not output_id:
        return jsonify({"error": "output_id required"}), 400
    if not contract_id:
        return jsonify({"error": "contract_id required — specify which PMO contract to attach milestones to"}), 400

    conn = _conn()
    try:
        row = conn.execute(
            "SELECT output_type, content_json FROM dic_generated_outputs "
            "WHERE id = %s AND tenant_id = %s",
            (output_id, tenant_id),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "output not found"}), 404

    output_type = row[0] if isinstance(row, (tuple, list)) else row["output_type"]
    raw_json = row[1] if isinstance(row, (tuple, list)) else row["content_json"]
    if output_type != "timeline":
        return jsonify({"error": f"roadmap generation requires a timeline output, got '{output_type}'"}), 400

    try:
        content = json.loads(raw_json or "{}")
    except Exception:
        content = {}

    events = content.get("events", [])
    if not events:
        return jsonify({"milestone_count": 0, "contract_id": contract_id, "milestone_ids": [],
                        "message": "No events found in timeline output"})

    try:
        from tools.govcon.milestone_manager import create_milestone
    except Exception as exc:
        return jsonify({"error": f"PMO milestone manager unavailable: {exc}"}), 500

    milestone_ids: list[str] = []
    errors: list[str] = []
    for ev in events[:50]:
        date_str = str(ev.get("date") or "").strip() or None
        title = str(ev.get("event") or "").strip()[:255]
        if not title:
            continue
        result = create_milestone({
            "contract_id": contract_id,
            "title": title,
            "description": f"Auto-generated from DIC timeline (collection: {collection_id}, output: {output_id}).",
            "baseline_date": date_str,
            "status": "pending",
            "notes": f"Source: DIC collection {collection_id}",
        })
        if result.get("status") == "ok":
            milestone_ids.append(result["milestone_id"])
        else:
            errors.append(result.get("message", "unknown error"))

    return jsonify({
        "milestone_count": len(milestone_ids),
        "contract_id": contract_id,
        "milestone_ids": milestone_ids,
        "errors": errors[:5],
    })


@dic_bp.route("/api/documents/<doc_id>/re-enrich", methods=["POST"])
def api_document_re_enrich(doc_id: str):
    """POST /document-intelligence/api/documents/<doc_id>/re-enrich

    Re-runs LLM metadata extraction on a previously ingested document without
    requiring the original file. Reconstructs text from stored rag_chunks and
    returns a HITL proposal dict — never silently writes to dic_documents.
    (aiify-opp-89: signals/handlers.py → re_enrich_metadata in DIC)

    Body (optional JSON): {"extract_identifiers": bool, "extract_correspondence": bool}
    Returns: {"doc_id", "filename", "proposals": {...}} or {"error": "..."}
    """
    body = request.get_json(silent=True) or {}
    extract_identifiers = bool(body.get("extract_identifiers", True))
    extract_correspondence = bool(body.get("extract_correspondence", True))

    try:
        from tools.document_intelligence.ingest_orchestrator import re_enrich_metadata
        result = re_enrich_metadata(
            doc_id,
            extract_identifiers=extract_identifiers,
            extract_correspondence=extract_correspondence,
        )
    except Exception as exc:
        logger.warning("dic: re_enrich_metadata raised: %s", exc)
        return jsonify({"error": str(exc)}), 500

    if result is None:
        return jsonify({"error": f"document {doc_id!r} not found"}), 404

    return jsonify(result)


@dic_bp.route("/api/collections/<collection_id>/attach-coworker", methods=["POST"])
def api_attach_coworker(collection_id):
    """POST /document-intelligence/api/collections/<id>/attach-coworker

    Stores a pending DIC→ACE context link in coworker_dic_contexts.
    Body: {} (collection_id from URL)
    Returns {collection_id, coworker_url} so the client can redirect to /coworker.
    """
    tenant_id, _ = _security_context()
    try:
        from tools.db.storage import get_canvas_connection
        import uuid as _uuid
        conn = get_canvas_connection("ICDEV_ACE_DB_URL")
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS coworker_dic_contexts (
                    id           TEXT PRIMARY KEY,
                    instance_id  TEXT,
                    collection_id TEXT NOT NULL,
                    attached_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            conn.execute(
                "INSERT INTO coworker_dic_contexts (id, instance_id, collection_id) VALUES (%s, NULL, %s)",
                (_uuid.uuid4().hex, collection_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.warning("dic: attach-coworker DB write failed: %s", exc)
    return jsonify({
        "collection_id": collection_id,
        "coworker_url": f"/coworker?dic_collection={collection_id}",
        "message": "Collection attached. Open Co-Worker and launch with DIC context pre-loaded.",
    })


# ── Modernization routes (docmod hitl-04..06, ux-02) — registered on dic_bp ──
# Kept in a separate module so this file stops growing; import has side effects
# (route registration) and must stay at the bottom after dic_bp is fully built.
from tools.document_intelligence import modernization_routes  # noqa: E402,F401


# ── Chunk inspect & repair (oss-hitl-01) ─────────────────────────────────────
#
# Reuses the section-review HITL shape (RBAC via _require_role, _conn, review
# notes) applied to rag_chunks. A repair is a reviewer-gated mutation, not an
# autonomous rewrite, and every repair re-baselines dic_chunk_links so the
# evidence baseline stays honest. The engine (tools/document_intelligence/
# chunk_repair.py) does the work; these routes are the reviewer-gated seam.


def _chunk_repair_engine():
    from tools.document_intelligence.chunk_repair import ChunkRepairEngine
    from tools.rag.vector_store_factory import VectorStoreFactory

    return ChunkRepairEngine(
        store=VectorStoreFactory.create(),
        conn_factory=_conn,
        actor=_current_user(),
    )


@dic_bp.route("/api/chunks/<collection_id>/repair", methods=["POST"])
def api_chunk_repair(collection_id):
    """Apply a HITL chunk repair. Reviewer role required.

    Body: {operation: merge|split|rechunk|reembed, chunk_ids|chunk_id, texts|text,
           offset?, template?}. The operator supplies the current text so the
           engine does not have to re-read it under the reviewer's lock.
    """
    from tools.document_intelligence.chunk_repair import (
        MERGE, RECHUNK, REEMBED, SPLIT,
    )

    if not _require_role(collection_id, "reviewer"):
        return _forbid("reviewer")

    data = request.get_json(silent=True) or {}
    op = (data.get("operation") or "").strip()
    engine = _chunk_repair_engine()
    try:
        if op == MERGE:
            result = engine.merge(data.get("chunk_ids") or [], data.get("texts") or [])
        elif op == SPLIT:
            result = engine.split(data.get("chunk_id", ""), data.get("text", ""),
                                   int(data.get("offset", 0)))
        elif op == RECHUNK:
            result = engine.rechunk(data.get("chunk_id", ""), data.get("text", ""),
                                    template=data.get("template", "general"))
        elif op == REEMBED:
            result = engine.reembed(data.get("chunk_id", ""), data.get("text", ""))
        else:
            return jsonify({"error": f"unknown operation {op!r}"}), 400
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc)}), 500

    status = 200 if result.ok else 422
    return jsonify(result.to_dict()), status
