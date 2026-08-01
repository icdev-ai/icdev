# CUI // SP-CTI
"""IDR — Intelligent Documentation Regeneration — Flask Blueprint.

Routes:
  GET  /docgen/                             index (session list + new button)
  GET  /docgen/new                          create session wizard (domain picker)
  GET  /docgen/<session_id>                 session stepper UI (stages 0-8)
  GET  /docgen/<session_id>/conflicts       HITL conflict resolution (stage 3)
  GET  /docgen/<session_id>/review          HITL document review (stage 7)

  POST /docgen/api/sessions                 create session
  GET  /docgen/api/sessions                 list sessions
  GET  /docgen/api/sessions/<id>            session detail
  POST /docgen/api/sessions/<id>/advance    advance to next stage
  POST /docgen/api/sessions/<id>/uploads    add upload record
  GET  /docgen/api/sessions/<id>/uploads    list uploads for session
  POST /docgen/api/sessions/<id>/analyze    trigger Stage 2 analysis for one upload
  POST /docgen/api/sessions/<id>/reconcile  Stage 3: reconcile diagrams → conflicts
  GET  /docgen/api/sessions/<id>/conflicts  list conflicts
  POST /docgen/api/conflicts/<id>/resolve   resolve one conflict (HITL)
  POST /docgen/api/sessions/<id>/generate   Stage 5: trigger AI doc generation
  POST /docgen/api/sessions/<id>/writeguard Stage 6 hard gate: run WriteGuard quality check
  GET  /docgen/api/sessions/<id>/artifacts  list published artifacts
  POST /docgen/api/iqe-query                IQE natural-language query
"""
from __future__ import annotations

import json
import os
import pathlib
import uuid

from flask import Blueprint, jsonify, render_template, request

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

docgen_bp = Blueprint(
    "docgen",
    __name__,
    url_prefix="/docgen",
    template_folder="../../tools/dashboard/templates",
)


# ─── Tenant scoping (cnr-doc-03: cross-tenant IDOR guard) ────────────────────

def _request_tenant_id():
    """Current request's tenant id from the security context, or None (system/CLI)."""
    try:
        from flask import g
        ctx = getattr(g, "security_context", None)
        return getattr(ctx, "tenant_id", None) if ctx else None
    except Exception:
        return None


def _tenant_visible(row) -> bool:
    """False when *row* belongs to a DIFFERENT tenant than the request's.

    Rows with no tenant (shared/default) and requests with no tenant context
    (system/CLI/single-tenant) are always visible — this blocks cross-tenant IDOR
    without breaking the default single-tenant deployment.
    """
    req_tenant = _request_tenant_id()
    if not req_tenant:
        return True
    if isinstance(row, dict):
        row_tenant = row.get("tenant_id")
    else:
        row_tenant = getattr(row, "tenant_id", None)
    return row_tenant is None or row_tenant == req_tenant


# ─── Page routes ─────────────────────────────────────────────────────────────

def _sessions_with_freshness(limit: int = 20) -> list:
    """List sessions annotated with freshness_stale for the UI badge.

    Shared by ``/`` and ``/new`` so both render the same session list.
    Only checks published sessions with a stored source hash (avoids DB churn
    for drafts).
    """
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import check_freshness

    # cnr-doc-04(b): the /docgen landing must not 500 when the idr_* tables are
    # absent (e.g. a squash-bootstrapped PG DB where migration 211 was marked
    # applied but never ran). Degrade to an empty board instead.
    try:
        sessions = sm.list_sessions(limit=limit)
    except Exception as exc:
        logger.warning("docgen: session list unavailable (tables not initialized?): %s", exc)
        return []
    for s in sessions:
        if s.get("last_source_hash") and s.get("status") in ("published", "reviewing"):
            try:
                uploads = sm.list_uploads(s["id"])
                paths = [u["file_path"] for u in uploads if u.get("file_path")]
                fresh = check_freshness(s["id"], paths, stored_hash=s.get("last_source_hash"))
                s["freshness_stale"] = fresh["stale"]
            except Exception:
                s["freshness_stale"] = False
        else:
            s["freshness_stale"] = False
    return sessions


@docgen_bp.route("/")
def index():
    from tools.docgen.domain_profiles import list_profiles

    sessions = _sessions_with_freshness()
    profiles = list_profiles()

    return render_template(
        "docgen/index.html",
        session=None,
        sessions=sessions,
        profiles=profiles,
        page_title="Doc Regeneration",
    )


@docgen_bp.route("/new")
def new_session_page():
    from tools.docgen.domain_profiles import list_profiles

    domain = request.args.get("domain", "network")
    from_topo = request.args.get("from_topo")
    profiles = list_profiles()

    # docmod-regen-01: 'Regenerate in DocGen' from a stale DIC document —
    # prefill title/classification/doc_type from the source doc so generation
    # appends a new pending_review version on the SAME document.
    source_doc = None
    source_doc_id = (request.args.get("source_doc_id") or "").strip()
    if source_doc_id:
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT doc_id, title, collection_id, classification, template_type "
                    "FROM dic_documents WHERE doc_id = %s",
                    (source_doc_id,),
                ).fetchone()
            finally:
                conn.close()
            if row:
                source_doc = dict(row)
                from tools.document_intelligence.constants import (
                    DOCGEN_DOCTYPE_TO_TEMPLATE,
                )
                # first doc_type per template wins — the map lists canonical
                # doc_types before aliases (standard_guide before baseline, ...)
                reverse_map: dict = {}
                for dt, tpl in DOCGEN_DOCTYPE_TO_TEMPLATE.items():
                    reverse_map.setdefault(tpl, dt)
                source_doc["doc_type"] = reverse_map.get(
                    source_doc.get("template_type") or "", "standard_guide"
                )
        except Exception as exc:
            logger.warning("docgen: source_doc prefill failed: %s", exc)

    return render_template(
        "docgen/index.html",
        session=None,
        # Show the real session list — /new is the same page with the wizard
        # open, not an empty board. Previously hardcoded [], which made the
        # page claim "No regeneration sessions yet" even when sessions existed.
        sessions=_sessions_with_freshness(),
        profiles=profiles,
        preselect_domain=domain,
        from_topo=from_topo,
        source_doc=source_doc,
        # Auto-open the wizard: /new is advertised as the "DocGen Wizard"
        # entry point, so arriving here must show the form, not hide it
        # behind a button.
        open_wizard=True,
        page_title="New Doc Regeneration",
    )


@docgen_bp.route("/<session_id>")
def session_detail(session_id: str):
    from tools.docgen import session_manager as sm
    from tools.docgen.domain_profiles import get_profile

    session = sm.get_session(session_id)
    if not session or not _tenant_visible(session):  # cnr-doc-03: cross-tenant IDOR guard
        return render_template("errors/404.html"), 404
    uploads = sm.list_uploads(session_id)
    analyses = sm.list_analyses(session_id)
    artifacts = sm.list_artifacts(session_id)
    conflicts = sm.list_conflicts(session_id)
    pending_conflicts = [c for c in conflicts if not c.get("resolved_at")]
    try:
        profile = get_profile(session["domain"])
    except KeyError:
        profile = {}
    return render_template(
        "docgen/index.html",
        session=session,
        uploads=uploads,
        analyses=analyses,
        artifacts=artifacts,
        conflicts=conflicts,
        pending_conflicts=pending_conflicts,
        profile=profile,
        page_title=f"IDR — {session.get('title', session_id)}",
    )


@docgen_bp.route("/<session_id>/conflicts")
def conflicts_page(session_id: str):
    from tools.docgen import session_manager as sm

    session = sm.get_session(session_id)
    if not session:
        return render_template("errors/404.html"), 404
    conflicts = sm.list_conflicts(session_id, pending_only=False)
    pending = [c for c in conflicts if not c.get("resolved_at")]
    resolved = [c for c in conflicts if c.get("resolved_at")]
    return render_template(
        "docgen/conflicts.html",
        session=session,
        conflicts=conflicts,
        pending_conflicts=pending,
        resolved_conflicts=resolved,
        page_title=f"IDR Conflicts — {session.get('title', session_id)}",
    )


@docgen_bp.route("/<session_id>/review")
def review_page(session_id: str):
    from tools.docgen import session_manager as sm

    session = sm.get_session(session_id)
    if not session:
        return render_template("errors/404.html"), 404
    artifacts = sm.list_artifacts(session_id)
    analyses = sm.list_analyses(session_id)

    # Extract remediation diagrams from diagram_analysis result_json
    remediation_diagrams: list[str] = []
    for a in analyses:
        if a.get("analysis_type") != "diagram_analysis":
            continue
        raw_json = a.get("result_json")
        if raw_json:
            try:
                stored = json.loads(raw_json)
                rdiag = stored.get("remediation_diagram", "")
                if rdiag and rdiag.strip():
                    remediation_diagrams.append(rdiag.strip())
            except Exception:
                pass

    return render_template(
        "docgen/review.html",
        session=session,
        artifacts=artifacts,
        analyses=analyses,
        remediation_diagrams=remediation_diagrams,
        page_title=f"IDR Review — {session.get('title', session_id)}",
    )


# ─── API — Sessions ──────────────────────────────────────────────────────────

@docgen_bp.route("/api/sessions", methods=["POST"])
def api_create_session():
    from tools.docgen import session_manager as sm

    data = request.get_json(force=True, silent=True) or {}
    title = (data.get("title") or "").strip()
    domain = (data.get("domain") or "network").strip()
    doc_type = (data.get("doc_type") or "runbook").strip()
    template_id = data.get("template_id")
    created_by = data.get("created_by") or "dashboard"
    tenant_id = data.get("tenant_id")
    classification = data.get("classification", "CUI")

    if not title:
        return jsonify({"error": "title is required"}), 400

    try:
        from tools.docgen.domain_profiles import get_profile
        get_profile(domain)
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 400

    session = sm.create_session(
        title=title,
        domain=domain,
        doc_type=doc_type,
        template_id=template_id,
        created_by=created_by,
        tenant_id=tenant_id,
        classification=classification,
    )

    # docmod-regen-01: sessions started from a DIC document regenerate THAT
    # document — persist the source link and reuse its collection as evidence.
    source_dic_doc_id = (data.get("source_dic_doc_id") or "").strip()
    if source_dic_doc_id:
        fields = {"source_dic_doc_id": source_dic_doc_id}
        try:
            from tools.db.storage import get_connection
            conn = get_connection()
            try:
                row = conn.execute(
                    "SELECT collection_id FROM dic_documents WHERE doc_id = %s",
                    (source_dic_doc_id,),
                ).fetchone()
            finally:
                conn.close()
            if row and dict(row).get("collection_id"):
                fields["dic_collection_id"] = dict(row)["collection_id"]
        except Exception as exc:
            logger.warning("docgen: source doc collection lookup failed: %s", exc)
        sm.set_field(session["id"], **fields)
        session = sm.get_session(session["id"]) or session

    return jsonify(session), 201


@docgen_bp.route("/api/sessions", methods=["GET"])
def api_list_sessions():
    from tools.docgen import session_manager as sm

    domain = request.args.get("domain")
    limit = min(int(request.args.get("limit", 50)), 200)
    sessions = sm.list_sessions(domain=domain, limit=limit)
    return jsonify(sessions)


@docgen_bp.route("/api/sessions/<session_id>/refresh", methods=["POST"])
def api_refresh_session(session_id: str):
    """One-click 'Re-run with updated sources' (docmod-ux-03): clone a stale
    session — same title/domain/doc_type/uploads by file reference — re-hash
    the sources, and start the clone at stage 0 for a fresh generation run."""
    from tools.docgen import session_manager as sm

    old = sm.get_session(session_id)
    if not old:
        return jsonify({"error": "not found"}), 404

    clone = sm.create_session(
        title=f"{old['title']} (refreshed)",
        domain=old.get("domain") or "network",
        doc_type=old.get("doc_type") or "runbook",
        template_id=old.get("template_id"),
        created_by=old.get("created_by") or "dashboard",
        tenant_id=old.get("tenant_id"),
        classification=old.get("classification") or "CUI",
    )
    paths: list[str] = []
    for up in sm.list_uploads(session_id):
        sm.add_upload(
            clone["id"],
            filename=up.get("filename") or "upload",
            upload_type=up.get("upload_type") or "doc",
            file_path=up.get("file_path"),
            file_hash=up.get("file_hash"),
            tenant_id=up.get("tenant_id"),
        )
        if up.get("file_path"):
            paths.append(up["file_path"])
    try:
        from tools.docgen.workflow import record_source_hash
        record_source_hash(clone["id"], paths)
    except Exception as exc:
        logger.warning("docgen refresh: source hash snapshot failed: %s", exc)
    if old.get("source_dic_doc_id") or old.get("dic_collection_id"):
        sm.set_field(
            clone["id"],
            source_dic_doc_id=old.get("source_dic_doc_id"),
            dic_collection_id=old.get("dic_collection_id"),
        )
    logger.info("docgen: session %s refreshed -> %s (%d uploads)",
                session_id, clone["id"], len(paths))
    return jsonify({"session_id": clone["id"], "uploads": len(paths)}), 201


@docgen_bp.route("/api/sessions/<session_id>", methods=["GET"])
def api_get_session(session_id: str):
    from tools.docgen import session_manager as sm

    session = sm.get_session(session_id)
    if not session or not _tenant_visible(session):  # cnr-doc-03: cross-tenant IDOR guard
        return jsonify({"error": "not found"}), 404
    return jsonify(session)


@docgen_bp.route("/api/sessions/<session_id>/advance", methods=["POST"])
def api_advance_session(session_id: str):
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import advance, stage3_check_gate

    data = request.get_json(force=True, silent=True) or {}
    to_stage = data.get("stage")
    if to_stage is None:
        session = sm.get_session(session_id)
        if not session:
            return jsonify({"error": "not found"}), 404
        to_stage = session["stage"] + 1

    # Gate check for stage 3 → 4 transition
    if to_stage == 4:
        if not stage3_check_gate(session_id):
            pending = sm.pending_conflict_count(session_id)
            return jsonify({
                "error": f"Cannot advance: {pending} conflicts pending HITL resolution.",
                "pending_conflicts": pending,
            }), 409

    # Hard blocking gate: stage 6 (writeguard) → 7 (reviewing)
    if to_stage == 7:
        from tools.docgen.workflow import stage6_check_gate
        if not stage6_check_gate(session_id):
            return jsonify({
                "error": (
                    "Cannot advance to review: WriteGuard quality gate has not passed. "
                    "Run POST /docgen/api/sessions/<id>/writeguard with the document text first."
                ),
                "gate": "writeguard",
            }), 409

    try:
        updated = advance(session_id, int(to_stage))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(updated)


# ─── API — Uploads ────────────────────────────────────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/uploads", methods=["POST"])
def api_add_upload(session_id: str):
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import stage1_ingest_upload, advance

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    # Handle multipart file upload or JSON metadata
    if request.files:
        from tools.docgen.constants import ALLOWED_UPLOAD_EXTENSIONS, max_upload_bytes

        file = next(iter(request.files.values()))
        upload_type = (request.form.get("upload_type") or "doc").strip()
        safe_name = pathlib.Path(file.filename or "upload").name  # traversal-safe

        # cnr-doc-03: extension allowlist — reject executables/scripts and anything
        # outside the documented analyzer input set.
        ext = pathlib.Path(safe_name).suffix.lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            return jsonify({
                "error": f"File type '{ext or '(none)'}' is not permitted.",
                "allowed_extensions": sorted(ALLOWED_UPLOAD_EXTENSIONS),
            }), 400

        # cnr-doc-03: per-file size cap (coordinates with cnr-plat-02 global cap via env).
        cap = max_upload_bytes()
        try:
            file.stream.seek(0, os.SEEK_END)
            size = file.stream.tell()
            file.stream.seek(0)
        except (OSError, ValueError):
            size = request.content_length or 0
        if size > cap:
            return jsonify({
                "error": f"File exceeds the {cap // (1024 * 1024)} MiB upload limit.",
                "max_bytes": cap,
                "size": size,
            }), 400

        save_dir = pathlib.Path("data") / "docgen" / "uploads" / session_id
        save_dir.mkdir(parents=True, exist_ok=True)
        file_path = str(save_dir / safe_name)
        file.save(file_path)
        filename = safe_name
    else:
        data = request.get_json(force=True, silent=True) or {}
        filename = data.get("filename", "")
        upload_type = data.get("upload_type", "doc")
        file_path = data.get("file_path")

    upload = sm.add_upload(
        session_id=session_id,
        filename=filename,
        upload_type=upload_type,
        file_path=file_path,
    )

    # Automatically ingest into DIC
    if file_path and pathlib.Path(file_path).exists():
        upload = stage1_ingest_upload(session_id, upload["id"], file_path) or upload

    # Advance to ingesting if still at setup
    if session.get("stage", 0) == 0:
        advance(session_id, 1)

    return jsonify(upload), 201


@docgen_bp.route("/api/sessions/<session_id>/uploads", methods=["GET"])
def api_list_uploads(session_id: str):
    from tools.docgen import session_manager as sm

    session = sm.get_session(session_id)
    if not session or not _tenant_visible(session):  # cnr-doc-03: cross-tenant IDOR guard
        return jsonify({"error": "not found"}), 404
    uploads = sm.list_uploads(session_id)
    return jsonify(uploads)


# ─── API — Analysis ───────────────────────────────────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/analyze", methods=["POST"])
def api_analyze(session_id: str):
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import stage2_analyze_upload, advance

    data = request.get_json(force=True, silent=True) or {}
    upload_id = data.get("upload_id")

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404
    if upload_id:
        upload = sm.get_upload(upload_id)
        if not upload:
            return jsonify({"error": "upload not found"}), 404
        created = stage2_analyze_upload(session_id, upload_id, upload, session)
    else:
        # Analyze all pending uploads
        created = []
        for upload in sm.list_uploads(session_id):
            if upload.get("status") in ("pending", "ingested"):
                created.extend(
                    stage2_analyze_upload(session_id, upload["id"], upload, session)
                )

    # Advance to analyzing if still at ingesting
    if session.get("stage", 0) <= 1:
        advance(session_id, 2)

    return jsonify({"analyses_created": len(created), "analyses": created})


# ─── API — Reconcile ──────────────────────────────────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/reconcile", methods=["POST"])
def api_reconcile(session_id: str):
    from tools.docgen import session_manager as sm
    from tools.docgen.reconciler import reconcile
    from tools.docgen.workflow import advance

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    # Gather diagram analyses with their result graphs
    analyses = sm.list_analyses(session_id)
    diagram_analyses = [a for a in analyses if a["analysis_type"] == "diagram_analysis"]

    named_graphs: list[tuple[str, dict]] = []
    for analysis in diagram_analyses:
        upload = sm.get_upload(analysis["upload_id"])
        source_name = upload["filename"] if upload else analysis["upload_id"]
        # Try to load the graph from the NDC analysis result
        graph = _load_diagram_graph(analysis.get("result_ref_id"))
        if graph:
            named_graphs.append((source_name, graph))

    merged = reconcile(
        session_id=session_id,
        named_graphs=named_graphs,
        tenant_id=session.get("tenant_id"),
    )

    advance(session_id, 3)
    pending = sm.pending_conflict_count(session_id)
    return jsonify({
        "merged": {
            "node_count": len(merged.get("nodes", [])),
            "edge_count": len(merged.get("edges", [])),
            "stitched_hosts": merged.get("_stitched_hosts", []),
        },
        "conflicts_recorded": merged.get("_stats", {}).get("conflicts_recorded", 0),
        "pending_conflicts": pending,
    })


def _load_diagram_graph(result_ref_id: str | None) -> dict | None:
    """Try to load a diagram analysis graph from NDC nc_diagram_analyses."""
    if not result_ref_id:
        return None
    try:
        from tools.db.storage import get_connection
        with get_connection() as conn:
            row = conn.execute(
                "SELECT graph_json FROM nc_diagram_analyses WHERE id=%s", (result_ref_id,)
            ).fetchone()
        if row and row["graph_json"]:
            return json.loads(row["graph_json"])
    except Exception:
        pass
    return None


# ─── API — Conflicts ──────────────────────────────────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/conflicts", methods=["GET"])
def api_list_conflicts(session_id: str):
    from tools.docgen import session_manager as sm

    pending_only = request.args.get("pending_only", "false").lower() == "true"
    conflicts = sm.list_conflicts(session_id, pending_only=pending_only)
    return jsonify(conflicts)


@docgen_bp.route("/api/conflicts/<conflict_id>/resolve", methods=["POST"])
def api_resolve_conflict(conflict_id: str):
    from tools.docgen import session_manager as sm

    data = request.get_json(force=True, silent=True) or {}
    resolution = data.get("resolution")
    resolved_by = data.get("resolved_by", "dashboard")
    notes = data.get("notes")

    if resolution not in ("a", "b", "manual"):
        return jsonify({"error": "resolution must be 'a', 'b', or 'manual'"}), 400

    ok = sm.resolve_conflict(conflict_id, resolution, resolved_by, notes)
    if not ok:
        return jsonify({"error": "conflict not found or already resolved"}), 404
    return jsonify({"resolved": True, "conflict_id": conflict_id})


# ─── API — Generate ───────────────────────────────────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/generate", methods=["POST"])
def api_generate(session_id: str):
    """Stage 4-5: synthesize context then generate document.

    Request JSON (all optional):
        use_ace (bool, default false): launch ACE multi-coworker generation
            in addition to (or instead of) DIC DocGenerator.
        role_ids (list[str]): ACE roles to spawn (default: technical_writer, network_engineer).
        supplemental_text (str): extra context to inject into the query.
    """
    from tools.docgen import session_manager as sm
    from tools.docgen.context_builder import build_context
    from tools.docgen.workflow import stage3_check_gate, stage5_ace_generate, advance

    data = request.get_json(force=True, silent=True) or {}

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    # Enforce stage 3 gate
    if not stage3_check_gate(session_id):
        pending = sm.pending_conflict_count(session_id)
        return jsonify({
            "error": "Cannot generate: unresolved conflicts.",
            "pending_conflicts": pending,
        }), 409

    uploads = sm.list_uploads(session_id)
    analyses = sm.list_analyses(session_id)
    supplemental = data.get("supplemental_text", "")
    use_ace = bool(data.get("use_ace", False))
    role_ids = data.get("role_ids") or None

    context = build_context(
        session=session,
        uploads=uploads,
        analyses=analyses,
        supplemental_text=supplemental,
    )

    # Enrich context with title/domain for ACE problem text.
    context.setdefault("title", session.get("title", "Document"))
    context.setdefault("domain", session.get("domain", "network"))
    context.setdefault("doc_type", session.get("doc_type", "runbook"))
    context.setdefault("classification", session.get("classification", "CUI"))

    advance(session_id, 4)

    ace_result: dict = {}
    if use_ace:
        ace_result = stage5_ace_generate(session_id, context, role_ids=role_ids)

    # Attempt DIC generation (fallback / complement to ACE).
    doc_id = None
    try:
        from tools.document_intelligence.doc_generator import generate_document as _dic_gen

        # docmod-regen-01: sessions started from a DIC doc rebuild THAT doc —
        # old approved text + open modernization findings become mandatory
        # OPERATOR-tier context, and generation appends version N+1 on the
        # same document (target_doc_id) instead of minting a new one.
        supplemental = context.get("supplemental_text", "")
        target_doc_id = (session.get("source_dic_doc_id") or "").strip() or None
        if target_doc_id:
            try:
                from tools.doc_modernization.regen_orchestrator import _approved_text

                from tools.db.storage import get_connection
                _conn = get_connection()
                try:
                    _, old_text = _approved_text(_conn, target_doc_id)
                    try:
                        from tools.doc_modernization import get_findings
                        findings = get_findings(doc_id=target_doc_id, state="open", conn=_conn)
                    except Exception:
                        findings = []  # engine tables absent — old text still injects
                finally:
                    _conn.close()
                change_lines = [
                    f"- '{f['entity_label']}' is {f['currency_verdict']}"
                    + (f" -> replace with '{f['recommended_replacement']}'"
                       if f.get("recommended_replacement") else "")
                    for f in findings
                ]
                regen_ctx = (
                    "CURRENT APPROVED DOCUMENT (modernize its content, keep its "
                    "purpose and structure):\n" + old_text
                )
                if change_lines:
                    regen_ctx += (
                        "\n\nMANDATORY MODERNIZATION CHANGES (deterministic "
                        "findings — apply each):\n" + "\n".join(change_lines)
                    )
                supplemental = f"{supplemental}\n\n{regen_ctx}".strip()
            except Exception as _exc:
                logger.warning("IDR regen context assembly failed: %s", _exc)

        result = _dic_gen(
            query=context["query_string"],
            collection_id=None,  # full DIC KB search; falls back to session-scoped internally
            classification=context.get("classification", "CUI"),
            created_by="idr_pipeline",
            supplemental_text=supplemental,
            kg_chunks=context.get("kg_chunks", []),
            target_doc_id=target_doc_id,
        )
        doc_id = result.doc_id if result else None
        # Persist assembled text so WriteGuard / HITL review can read it
        if result and result.sections:
            final_text = "\n\n".join(
                f"## {s.heading}\n{s.content}" for s in result.sections if s.content
            )
            sm.set_field(session_id, final_doc_text=final_text)
        advance(session_id, 5)
    except ImportError:
        logger.warning("DIC DocGenerator not available — placeholder generation")
        advance(session_id, 5)
    except Exception:
        logger.exception("IDR generation failed: session=%s", session_id)
        sm.fail_session(session_id)
        return jsonify({"error": "Generation failed — check logs"}), 500

    if doc_id:
        # Persist the DIC document generation created so the Tech Writer bridge
        # can reuse it (Path A) instead of rebuilding an empty scaffold.
        sm.set_field(
            session_id,
            dic_collection_id=context["session_id"],
            dic_doc_id=doc_id,
        )

    from tools.docgen.domain_profiles import get_ato_doc_type as _get_ato
    ato_cfg = _get_ato(context.get("doc_type"))

    return jsonify({
        "status": "generating",
        "context_summary": {
            "query_length": len(context["query_string"]),
            "ace_roles": context["ace_roles"],
            "topology_nodes": context["topology_summary"].get("node_count", 0),
            "config_findings": len(context["config_findings"]),
        },
        "doc_id": doc_id,
        "ace_instance_id": ace_result.get("instance_id"),
        "ace_status": ace_result.get("status") if use_ace else None,
        "doc_type_config": ato_cfg,
    })


# ─── API — Item 10: Semantic conflict detection ──────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/detect-conflicts", methods=["POST"])
def api_detect_conflicts(session_id: str):
    """Detect semantic cross-section conflicts in document text.

    Request JSON: {"doc_text": str}
    Response: {"conflicts": list, "semantic_count": int, "total_count": int}
    """
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import detect_semantic_conflicts

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    doc_text = (data.get("doc_text") or "").strip()
    if not doc_text:
        return jsonify({"error": "doc_text is required"}), 400

    conflicts = detect_semantic_conflicts(doc_text)
    semantic_count = len([c for c in conflicts if c.get("severity") == "error"])
    return jsonify({
        "conflicts": conflicts,
        "semantic_count": semantic_count,
        "total_count": len(conflicts),
    })


# ─── API — Item 12: SSE generation progress stream ────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/progress")
def api_session_progress(session_id: str):
    """SSE stream of generation progress for a session."""
    from flask import Response, stream_with_context
    import json as _json
    import time as _time

    def generate():
        for _ in range(60):  # max 60 polls (60s at 1s interval)
            from tools.docgen import session_manager as _sm
            session = _sm.get_session(session_id)
            if not session:
                yield f"data: {_json.dumps({'error': 'session not found'})}\n\n"
                return

            stage = session.get("stage", 0)
            status = session.get("status", "unknown")
            ace_id = session.get("ace_instance_id")
            ace_state = None

            if ace_id:
                try:
                    from tools.db.storage import get_canvas_connection
                    conn = get_canvas_connection("ACE_STORAGE_BACKEND")
                    row = conn.execute(
                        "SELECT state FROM ace_instances WHERE id = %s", (ace_id,)
                    ).fetchone()
                    ace_state = dict(row)["state"] if row else None
                    conn.close()
                except Exception:
                    pass

            pct = min(100, stage * 12)  # 8 stages → ~12% each
            payload = {
                "stage": stage,
                "status": status,
                "pct": pct,
                "ace_state": ace_state,
                "done": status in ("published", "failed"),
            }
            yield f"data: {_json.dumps(payload)}\n\n"

            if payload["done"]:
                return
            _time.sleep(1)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ─── API — Item 13: Template gallery ──────────────────────────────────────────

@docgen_bp.route("/api/templates", methods=["GET"])
def api_list_templates():
    """Return the pre-built document template gallery."""
    from tools.docgen.domain_profiles import get_template_gallery
    return jsonify({"templates": get_template_gallery()})


@docgen_bp.route("/api/sessions/<session_id>/apply-template", methods=["POST"])
def api_apply_template(session_id: str):
    """Apply a template's fields to a session (pre-populates Stage 1 context).

    Request JSON: {"template_id": str}
    Response: updated session dict
    """
    from tools.docgen import session_manager as sm
    from tools.docgen.domain_profiles import get_template

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    template_id = (data.get("template_id") or "").strip()
    if not template_id:
        return jsonify({"error": "template_id is required"}), 400

    tpl = get_template(template_id)
    if not tpl:
        return jsonify({"error": f"template '{template_id}' not found"}), 404

    sm.set_field(
        session_id,
        doc_type=tpl["doc_type"],
        template_id=template_id,
    )
    updated = sm.get_session(session_id)
    return jsonify(updated)


# ─── API — Stage 0: LLM-first document ingestion (Item 7) ───────────────────

@docgen_bp.route("/api/sessions/<session_id>/ingest-upload", methods=["POST"])
def api_ingest_upload(session_id: str):
    """Stage 0: Submit raw document text for LLM extraction.

    Request JSON: {"doc_text": str}   (plain text content of the uploaded file)
    Response: {
        "entities": [...], "topology": [...], "key_findings": [...],
        "document_type": str, "classification_hint": str,
        "extracted": bool, "session_id": str
    }
    """
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import stage0_ingest_document

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    doc_text = (data.get("doc_text") or "").strip()
    if not doc_text:
        return jsonify({"error": "doc_text is required"}), 400

    result = stage0_ingest_document(session_id, doc_text)
    return jsonify(result)


# ─── API — AI classification suggestion (Items 4 & 9) ────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/suggest-classification", methods=["POST"])
def api_suggest_classification(session_id: str):
    """Suggest classification level from a document text sample.

    Request JSON: {"text_sample": str}
    Response: {"classification": str, "confidence": float, "rationale": str, "requires_confirmation": bool}
    """
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import stage2_suggest_classification

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    data = request.get_json(force=True, silent=True) or {}
    text_sample = (data.get("text_sample") or "").strip()
    if not text_sample:
        return jsonify({"error": "text_sample is required"}), 400

    result = stage2_suggest_classification(session_id, text_sample)
    result["requires_confirmation"] = result.get("confidence", 0.0) < 0.85
    return jsonify(result)


# ─── API — WriteGuard gate (Stage 6) ─────────────────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/writeguard", methods=["POST"])
def api_writeguard(session_id: str):
    """Stage 6 hard blocking gate: run WriteGuard with a server-side auto-fix loop.

    The server runs up to _WG_MAX_RETRIES rewrite-and-recheck cycles internally.
    If all attempts fail, ACE regeneration is triggered (session rewound to stage 5).

    Request JSON:
        {"doc_text": str}   generated document text to quality-check

    Responses:
        200  {"passed": true, "score": ..., "fixed_text": ..., "wg_result_id": ...,
              "attempts": int}
        409  {"passed": false, "score": ..., "fixed_text": ..., "blocked": bool,
              "ace_regen_triggered": bool, "attempts": int, "message": str}
        400  validation error
        404  session not found
    """
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import stage6_writeguard, stage6_trigger_ace_regen, advance

    data = request.get_json(force=True, silent=True) or {}
    doc_text = data.get("doc_text", "")

    if not doc_text.strip():
        return jsonify({"error": "doc_text is required"}), 400

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    gate = stage6_writeguard(
        session_id=session_id,
        doc_text=doc_text,
        domain=session.get("domain", "network"),
    )

    if gate["passed"]:
        wg_result_id = str(uuid.uuid4())
        sm.set_field(
            session_id,
            wg_result_id=wg_result_id,
            final_doc_text=gate["fixed_text"],
        )
        advance(session_id, 6)
        logger.info(
            "IDR WriteGuard gate PASSED: session=%s score=%.1f attempts=%d",
            session_id, gate["score"], gate.get("attempts", 0),
        )
        return jsonify({
            "passed": True,
            "score": gate["score"],
            "fixed_text": gate["fixed_text"],
            "attempts": gate.get("attempts", 0),
            "blocked": False,
            "ace_regen_triggered": False,
            "wg_result_id": wg_result_id,
            # TRUST (cnr-doc-01): surface citation/placeholder defects so reviewers
            # resolve them before the publish gate blocks export.
            "citation_findings": gate.get("citation_findings", []),
            "placeholder_findings": gate.get("placeholder_findings", []),
        })

    # Gate failed after all auto-fix attempts.
    ace_regen_triggered = bool(gate.get("ace_regen_needed"))
    if ace_regen_triggered:
        stage6_trigger_ace_regen(session_id)

    logger.warning(
        "IDR WriteGuard gate FAILED: session=%s score=%.1f attempts=%d ace_regen=%s",
        session_id, gate["score"], gate.get("attempts", 0), ace_regen_triggered,
    )
    return jsonify({
        "passed": False,
        "score": gate["score"],
        "fixed_text": gate.get("fixed_text", doc_text),
        "attempts": gate.get("attempts", 0),
        "blocked": gate.get("blocked", False),
        "ace_regen_triggered": ace_regen_triggered,
        "message": (
            "WriteGuard engine unavailable — quality gate failed closed (cnr-doc-02). "
            "Publishing is blocked until the engine is restored."
            if gate.get("writeguard_unavailable") else
            "WriteGuard blocked after maximum auto-fix attempts — ACE regeneration triggered."
            if ace_regen_triggered else
            f"Quality gate failed (score {gate['score']:.1f} < 70)."
        ),
        "writeguard_unavailable": bool(gate.get("writeguard_unavailable")),
    }), 409


# ─── API — Publish (Stage 8) ─────────────────────────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/publish", methods=["POST"])
def api_publish(session_id: str):
    """Stage 8: export approved document to HTML + PDF (+ DOCX if available).

    Request JSON (all optional):
        {"doc_text": str, "title": str, "classification": str}

    If doc_text is omitted, the session title is used as a placeholder.
    Returns list of idr_artifacts rows.
    """
    from tools.docgen import session_manager as sm
    from tools.docgen.workflow import stage8_publish, stage6_check_gate, citation_publish_gate

    session = sm.get_session(session_id)
    if not session:
        return jsonify({"error": "not found"}), 404

    # Require WriteGuard to have passed (wg_result_id set) before publishing.
    if not stage6_check_gate(session_id):
        return jsonify({
            "error": (
                "Cannot publish: WriteGuard quality gate has not passed. "
                "Run POST /docgen/api/sessions/<id>/writeguard first."
            ),
            "gate": "writeguard",
        }), 409

    data = request.get_json(force=True, silent=True) or {}
    # cnr-doc-02: publish ONLY the server-side validated document. A client-supplied
    # doc_text is IGNORED — otherwise a caller could pass clean text through the
    # WriteGuard gate, then publish arbitrary unvalidated bytes (publish-gate bypass).
    doc_text = session.get("final_doc_text") or session.get("title", "Document")
    if data.get("doc_text") and data.get("doc_text") != doc_text:
        logger.warning(
            "IDR publish: ignoring client-supplied doc_text (session=%s) — publishing "
            "server-side validated final_doc_text only", session_id,
        )
    title = data.get("title") or session.get("title", "Document")
    classification = data.get("classification") or session.get("classification", "CUI")

    # ── TRUST publish gate (cnr-doc-01) ──────────────────────────────────────
    # Block export on citation / placeholder defects. A HITL force_* override
    # publishes past the defect but writes an append-only audit row.
    force_citations = bool(data.get("force_citations", False))
    force_placeholders = bool(data.get("force_placeholders", False))
    trust = citation_publish_gate(
        doc_text,
        force_citations=force_citations,
        force_placeholders=force_placeholders,
    )
    if trust["blocked"]:
        return jsonify({
            "error": (
                "Cannot publish: document has "
                + ("unresolved [PLACEHOLDER] tokens"
                   if trust["gate"] == "placeholder_guard"
                   else "citation defects (missing/hallucinated [source: …] tags)")
                + f" — resolve them or pass force_{trust['gate'].split('_')[0]}s=True after review."
            ),
            "gate": trust["gate"],
            "citation_findings": trust["citation_findings"],
            "placeholder_findings": trust["placeholder_findings"],
        }), 409
    reviewer = data.get("reviewer") or session.get("created_by") or "dashboard"
    for gate_name, key in (("placeholder_guard", "placeholder_guard_override"),
                           ("citation_guard", "citation_guard_override")):
        if trust["overrides"].get(key):
            sm.record_publish_audit(
                session_id, gate_name, reviewer,
                trust["overrides"][key], tenant_id=session.get("tenant_id"),
            )
            logger.warning(
                "IDR publish %s OVERRIDE: session=%s reviewer=%s defects=%d",
                gate_name, session_id, reviewer, len(trust["overrides"][key]),
            )

    try:
        artifacts = stage8_publish(
            session_id=session_id,
            doc_text=doc_text,
            title=title,
            classification=classification,
        )
    except Exception:
        logger.exception("IDR publish failed: session=%s", session_id)
        return jsonify({"error": "Publish failed — check logs"}), 500

    logger.info(
        "IDR publish complete: session=%s artifacts=%d", session_id, len(artifacts)
    )
    return jsonify({"published": True, "artifacts": artifacts}), 201


# ─── API — Artifacts ──────────────────────────────────────────────────────────

@docgen_bp.route("/api/sessions/<session_id>/artifacts", methods=["GET"])
def api_list_artifacts(session_id: str):
    from tools.docgen import session_manager as sm

    session = sm.get_session(session_id)
    if not session or not _tenant_visible(session):  # cnr-doc-03: cross-tenant IDOR guard
        return jsonify({"error": "not found"}), 404
    artifacts = sm.list_artifacts(session_id)
    return jsonify(artifacts)


@docgen_bp.route("/api/sessions/<session_id>/artifacts/<artifact_id>/download", methods=["GET"])
def api_download_artifact(session_id: str, artifact_id: str):
    """Stream a published artifact file to the browser."""
    import mimetypes
    from flask import send_file, abort
    from tools.docgen import session_manager as sm

    artifact = sm.get_artifact(artifact_id)
    if (not artifact or artifact.get("session_id") != session_id
            or not _tenant_visible(artifact)):  # cnr-doc-03: cross-tenant IDOR guard
        abort(404)

    file_path = artifact.get("file_path")
    if not file_path or not pathlib.Path(file_path).is_file():
        return jsonify({"error": "artifact file not found on disk"}), 404

    mime, _ = mimetypes.guess_type(file_path)
    mime = mime or "application/octet-stream"
    return send_file(
        pathlib.Path(file_path).resolve(),
        mimetype=mime,
        as_attachment=True,
        download_name=pathlib.Path(file_path).name,
    )


# ─── IQE ─────────────────────────────────────────────────────────────────────

@docgen_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    from tools.iqe.adapters.docgen import handle_iqe_query

    data = request.get_json(force=True, silent=True) or {}
    query = data.get("query", "")
    try:
        result = handle_iqe_query(query)
        return jsonify(result)
    except Exception:
        logger.exception("IDR IQE query failed")
        return jsonify({"error": "IQE query failed"}), 500
