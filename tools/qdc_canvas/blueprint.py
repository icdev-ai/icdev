# CUI // SP-CTI
"""Quality Design Canvas -- Flask Blueprint with all routes and API endpoints.

Provides the visual QA/QC canvas for designing quality gate topologies,
computing Unified Quality Scores (UQS), mapping gates to NIST SA-11
enhancements, and emitting OSCAL evidence artifacts.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml as _yaml
except ImportError:
    _yaml = None  # type: ignore[assignment]

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from tools.qdc_canvas.constants import (
    QDC_COMPLIANCE_RULES,
    QDC_NODE_DESCS,
    QDC_OBJECTS,
    QDC_SA11_MAP,
)
from tools.qdc_canvas.qdc_engine import (
    aggregate_cross_canvas_quality,
    assess_quality_design,
    compute_uqs,
    compute_uqs_trend,
    detect_quality_gaps,
    emit_oscal_evidence,
    map_gate_to_sa11,
)

logger = get_logger(__name__)

try:
    from tools.canvas.ai_trace_mixin import record_canvas_decision as _record_decision
except Exception:
    def _record_decision(**_kw): pass  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_CFG_PATH = Path(__file__).resolve().parents[2] / "args" / "qdc_canvas_config.yaml"
_QDC_CONFIG: dict = {}
if _yaml and _CFG_PATH.exists():
    try:
        with open(_CFG_PATH, "r", encoding="utf-8") as _f:
            _QDC_CONFIG = _yaml.safe_load(_f) or {}
    except Exception:
        pass
elif _CFG_PATH.exists():
    # Fallback: parse simple YAML manually
    try:
        _QDC_CONFIG = {"app": {"name": "Quality Design Canvas", "version": "1.0.0"}}
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
qdc_bp = Blueprint(
    "qdc_canvas",
    __name__,
    url_prefix="/quality",
    template_folder="../../tools/dashboard/templates",
)

# Initialize the QDC schema + seeds at blueprint import time (best-effort). This
# is the module-level-blueprint equivalent of BI Studio's init_db() at
# blueprint-factory creation (tools/bi_dashboard/blueprint.py:47-51). Guarded so
# an unavailable DB never blocks dashboard startup; page routes also degrade
# gracefully to empty lists if a table is still absent (cnr-qdc-04).
try:
    from tools.qdc_canvas.db.init_db import init_db as _qdc_init_db

    _qdc_init_db()
except Exception as _exc:  # pragma: no cover - defensive startup guard
    logger.warning("QDC DB init at blueprint import failed: %s", _exc)


# ---------------------------------------------------------------------------
# Authentication (cnr-qdc-01)
# ---------------------------------------------------------------------------


@qdc_bp.before_request
def _require_auth():
    """Enforce authentication on every QDC route.

    Mirrors BI Studio's ``_login_required`` behaviour but applied blueprint-wide
    via ``before_request`` so no route (including destructive endpoints such as
    ``DELETE /quality/api/designs``) can be reached unauthenticated. Page (GET)
    routes redirect to /login; API routes and any mutating method return a JSON
    401. ``ICDEV_AUTH_BYPASS`` keeps CI/E2E parity with other canvases, and a
    presented ``ICDEV_DASHBOARD_API_KEY`` authenticates headless callers.
    """
    if session.get("user_id"):
        return None

    # ICDEV_AUTH_BYPASS: explicit test-only opt-in — bypass unchanged.
    if os.environ.get("ICDEV_AUTH_BYPASS", "").lower() in ("1", "true", "yes"):
        session["user_id"] = "e2e-bypass"
        return None

    # ICDEV_DASHBOARD_API_KEY: presented-key semantics (constant-time compare).
    # Merely having the var set does NOT auto-authenticate.
    api_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
    if api_key:
        presented = request.headers.get("X-ICDEV-API-Key", "")
        if not presented:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                presented = auth_header[len("Bearer "):].strip()
        if presented and hmac.compare_digest(presented, api_key):
            session["user_id"] = "api-key"
            return None

    # API calls and any mutating method get JSON 401; page GETs redirect.
    if (
        request.is_json
        or request.path.startswith("/quality/api/")
        or request.method in ("DELETE", "POST", "PUT")
    ):
        return jsonify({"error": "Authentication required"}), 401
    return redirect("/login")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _gen_id() -> str:
    """Generate a unique ID with ``qdc-`` prefix."""
    return f"qdc-{uuid.uuid4().hex[:10]}"


def _get_conn():
    """Get a QDC database connection."""
    from tools.qdc_canvas.db.init_db import get_connection

    return get_connection()


def _row_to_dict(row) -> dict:
    """Convert a sqlite3.Row (or tuple) to a plain dict."""
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    return {}


def _rows_to_list(rows) -> list[dict]:
    """Convert a list of rows to list of dicts."""
    return [_row_to_dict(r) for r in rows]


# --- Graceful fetch + pagination (cnr-qdc-04) -----------------------------

_DEFAULT_PAGE_SIZE = 200
_MAX_PAGE_SIZE = 500


def _safe_rows(conn, sql: str, params=()) -> list[dict]:
    """Fetch rows as dicts, degrading to [] if the table is absent or query fails.

    Fresh-DB installs (before migrations/seeds run) must render the page rather
    than 500. On PostgreSQL a failed statement aborts the transaction, so roll
    back before returning so later queries on the same connection still work.
    """
    try:
        return _rows_to_list(conn.execute(sql, params).fetchall())
    except Exception as exc:
        logger.debug("QDC list query degraded to empty: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def _page_bounds() -> tuple[int, int]:
    """Return (limit, offset) from ?page / ?per_page query args, safely bounded."""
    try:
        per_page = min(max(int(request.args.get("per_page", _DEFAULT_PAGE_SIZE)), 1), _MAX_PAGE_SIZE)
    except (TypeError, ValueError):
        per_page = _DEFAULT_PAGE_SIZE
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    return per_page, (page - 1) * per_page


def _audit(conn, design_id: str, action: str, detail: str = "", user: str = "system") -> None:
    """Write an append-only audit entry."""
    conn.execute(
        "INSERT INTO qdc_audit (id, design_id, user, action, detail, created_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (_gen_id(), design_id, user, action, detail, _utcnow()),
    )


# ===================================================================
# Page Routes
# ===================================================================


@qdc_bp.route("/")
def index():
    """List all designs and templates."""
    limit, offset = _page_bounds()
    conn = _get_conn()
    try:
        designs = _safe_rows(
            conn,
            "SELECT id, name, description, classification, created_at, updated_at "
            "FROM qdc_designs ORDER BY updated_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
        templates = _safe_rows(
            conn,
            "SELECT id, name, category, description, compliance_target, tags "
            "FROM qdc_templates ORDER BY name LIMIT %s OFFSET %s",
            (limit, offset),
        )
    finally:
        conn.close()
    return render_template(
        "qdc_canvas/index.html",
        designs=designs,
        templates=templates,
        config=_QDC_CONFIG,
    )


@qdc_bp.route("/canvas/new")
def new_canvas():
    """Create a new design, optionally from a template."""
    template_id = request.args.get("template")
    project_id = request.args.get("project_id")
    graph_json = '{"nodes":[],"edges":[]}'
    name = "Untitled Quality Design"

    if template_id:
        conn = _get_conn()
        try:
            ex = conn.execute(
                "SELECT id FROM qdc_designs WHERE template_id=%s LIMIT 1",
                (template_id,),
            ).fetchone()
            if ex:
                return redirect(url_for("qdc_canvas.canvas_editor", design_id=ex["id"]))
            row = conn.execute(
                "SELECT name, graph_json FROM qdc_templates WHERE id = %s",
                (template_id,),
            ).fetchone()
            if row:
                name = f"{row[0]} (copy)"
                graph_json = row[1] or graph_json
        finally:
            conn.close()

    design_id = _gen_id()
    now = _utcnow()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO qdc_designs "
            "(id, name, description, graph_json, template_id, project_id, classification, "
            "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (design_id, name, "", graph_json, template_id, project_id, "CUI", now, now),
        )
        conn.commit()
        _audit(conn, design_id, "create", f"Created from template={template_id}")
        conn.commit()
    finally:
        conn.close()
    return redirect(url_for("qdc_canvas.canvas_editor", design_id=design_id))


@qdc_bp.route("/canvas/<design_id>")
def canvas_editor(design_id: str):
    """Canvas editor page for a specific design."""
    limit, offset = _page_bounds()
    conn = _get_conn()
    try:
        try:
            row = conn.execute("SELECT * FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
        except Exception as exc:
            logger.debug("QDC canvas_editor design lookup degraded: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            row = None
        if not row:
            return render_template(
                "qdc_canvas/index.html", designs=[], templates=[], config=_QDC_CONFIG, error="Design not found"
            ), 404
        design_dict = _row_to_dict(row)

        snippets_list = _safe_rows(
            conn,
            "SELECT id, name, category, description, graph_json, node_count, tags "
            "FROM qdc_snippets ORDER BY name LIMIT %s OFFSET %s",
            (limit, offset),
        )
        runbooks_list = _safe_rows(
            conn,
            "SELECT id, name, trigger_gate, auto_executable, confidence_threshold, "
            "run_count, last_run FROM qdc_runbooks ORDER BY name LIMIT %s OFFSET %s",
            (limit, offset),
        )
    finally:
        conn.close()

    return render_template(
        "qdc_canvas/canvas.html",
        design=design_dict,
        objects=QDC_OBJECTS.get("categories", QDC_OBJECTS),
        rules=QDC_COMPLIANCE_RULES,
        sa11_map=QDC_SA11_MAP,
        node_descs=QDC_NODE_DESCS,
        snippets=snippets_list,
        runbooks=runbooks_list,
        config=_QDC_CONFIG,
    )


@qdc_bp.route("/templates")
def templates_page():
    """Template gallery page."""
    limit, offset = _page_bounds()
    conn = _get_conn()
    try:
        templates = _safe_rows(
            conn,
            "SELECT id, name, category, description, compliance_target, tags "
            "FROM qdc_templates ORDER BY name LIMIT %s OFFSET %s",
            (limit, offset),
        )
    finally:
        conn.close()
    return render_template(
        "qdc_canvas/index.html",
        designs=[],
        templates=templates,
        config=_QDC_CONFIG,
        active_tab="templates",
    )


@qdc_bp.route("/snippets")
def snippets_page():
    """Snippet library page."""
    limit, offset = _page_bounds()
    conn = _get_conn()
    try:
        snippets = _safe_rows(
            conn,
            "SELECT id, name, category, description, graph_json, node_count, tags "
            "FROM qdc_snippets ORDER BY name LIMIT %s OFFSET %s",
            (limit, offset),
        )
    finally:
        conn.close()
    return render_template(
        "qdc_canvas/index.html",
        designs=[],
        templates=[],
        snippets=snippets,
        config=_QDC_CONFIG,
        active_tab="snippets",
    )


@qdc_bp.route("/assessments")
def assessments_page():
    """Assessment history with UQS trends."""
    limit, offset = _page_bounds()
    conn = _get_conn()
    try:
        assessments = _safe_rows(
            conn,
            "SELECT a.id, a.design_id, a.score, a.uqs_score, a.created_at, "
            "d.name AS design_name "
            "FROM qdc_assessments a "
            "LEFT JOIN qdc_designs d ON d.id = a.design_id "
            "ORDER BY a.created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        )
    finally:
        conn.close()
    return render_template(
        "qdc_canvas/index.html",
        designs=[],
        templates=[],
        assessments=assessments,
        config=_QDC_CONFIG,
        active_tab="assessments",
    )


@qdc_bp.route("/runbooks")
def runbooks_page():
    """Runbook library page."""
    limit, offset = _page_bounds()
    conn = _get_conn()
    try:
        runbooks = _safe_rows(
            conn,
            "SELECT id, name, trigger_gate, body_markdown, auto_executable, "
            "confidence_threshold, run_count, last_run, created_at "
            "FROM qdc_runbooks ORDER BY name LIMIT %s OFFSET %s",
            (limit, offset),
        )
    finally:
        conn.close()
    return render_template(
        "qdc_canvas/index.html",
        designs=[],
        templates=[],
        runbooks=runbooks,
        config=_QDC_CONFIG,
        active_tab="runbooks",
    )


@qdc_bp.route("/sops")
def sops_page():
    """SOP library page."""
    limit, offset = _page_bounds()
    conn = _get_conn()
    try:
        sops = _safe_rows(
            conn,
            "SELECT id, sop_number, title, version, frequency, audience, "
            "approval_status, classification, created_at, updated_at "
            "FROM qdc_sops ORDER BY sop_number LIMIT %s OFFSET %s",
            (limit, offset),
        )
    finally:
        conn.close()
    return render_template(
        "qdc_canvas/index.html",
        designs=[],
        templates=[],
        sops=sops,
        config=_QDC_CONFIG,
        active_tab="sops",
    )


@qdc_bp.route("/remediation/<design_id>")
def remediation_page(design_id: str):
    """Gap analysis / remediation view for a design."""
    limit, offset = _page_bounds()
    conn = _get_conn()
    try:
        try:
            row = conn.execute("SELECT * FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
        except Exception as exc:
            logger.debug("QDC remediation design lookup degraded: %s", exc)
            try:
                conn.rollback()
            except Exception:
                pass
            row = None
        if not row:
            return render_template(
                "qdc_canvas/index.html", designs=[], templates=[], config=_QDC_CONFIG, error="Design not found"
            ), 404
        design_dict = _row_to_dict(row)

        # Get latest assessment
        assess_rows = _safe_rows(
            conn,
            "SELECT findings_json, score, uqs_score, uqs_breakdown "
            "FROM qdc_assessments WHERE design_id = %s ORDER BY created_at DESC LIMIT 1",
            (design_id,),
        )
        assessment = assess_rows[0] if assess_rows else {}

        # Get runbooks for remediation suggestions
        runbooks = _safe_rows(
            conn,
            "SELECT id, name, trigger_gate, body_markdown FROM qdc_runbooks "
            "ORDER BY name LIMIT %s OFFSET %s",
            (limit, offset),
        )
    finally:
        conn.close()

    # Compute gaps from assessment findings
    findings_raw = assessment.get("findings_json", "[]")
    try:
        findings = json.loads(findings_raw) if isinstance(findings_raw, str) else findings_raw
    except (json.JSONDecodeError, TypeError):
        findings = []
    gaps = detect_quality_gaps({"findings": findings})

    return render_template(
        "qdc_canvas/canvas.html",
        design=design_dict,
        objects=QDC_OBJECTS.get("categories", QDC_OBJECTS),
        rules=QDC_COMPLIANCE_RULES,
        sa11_map=QDC_SA11_MAP,
        node_descs=QDC_NODE_DESCS,
        snippets=[],
        runbooks=runbooks,
        config=_QDC_CONFIG,
        gaps=gaps,
        assessment=assessment,
        active_tab="remediation",
    )


# ===================================================================
# API Endpoints — Designs
# ===================================================================


@qdc_bp.route("/api/designs/<design_id>", methods=["GET"])
def api_get_design(design_id: str):
    """Get a design as JSON."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "Design not found"}), 404
    return jsonify(_row_to_dict(row))


@qdc_bp.route("/api/designs/<design_id>", methods=["PUT"])
def api_save_design(design_id: str):
    """Save (update) a design — graph_json, name, updated_at."""
    data = request.get_json(silent=True) or {}
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT id FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
        if not existing:
            conn.close()
            return jsonify({"error": "Design not found"}), 404

        now = _utcnow()
        graph_json = data.get("graph_json")
        name = data.get("name")
        project_id = data.get("project_id")

        updates = ["updated_at = ?"]
        params: list = [now]
        if graph_json is not None:
            gj = json.dumps(graph_json) if isinstance(graph_json, dict) else graph_json
            updates.append("graph_json = ?")
            params.append(gj)
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        # cnr-qdc-05: project_id (schema column) is now writable so a design can
        # be associated with an owning project rather than being permanently null.
        if project_id is not None:
            updates.append("project_id = ?")
            params.append(project_id)

        params.append(design_id)
        conn.execute(
            f"UPDATE qdc_designs SET {', '.join(updates)} WHERE id = %s",  # noqa: S608
            params,
        )
        conn.commit()

        # Audit
        _audit(conn, design_id, "save", f"Updated fields: {', '.join(updates)}")
        conn.commit()

        # Trigger cross-canvas KG rebuild (best-effort)
        try:
            from tools.qdc_canvas.agent import _on_canvas_saved

            _on_canvas_saved("qdc", design_id)
        except Exception as exc:
            logger.debug("Cross-canvas KG rebuild skipped: %s", exc)

    finally:
        conn.close()
    return jsonify({"ok": True, "design_id": design_id, "updated_at": now})


@qdc_bp.route("/api/designs/<design_id>", methods=["DELETE"])
def api_delete_design(design_id: str):
    """Delete a design."""
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT id FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
        if not existing:
            conn.close()
            return jsonify({"error": "Design not found"}), 404

        _audit(conn, design_id, "delete", "Design deleted")
        conn.execute("DELETE FROM qdc_designs WHERE id = %s", (design_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "deleted": design_id})


@qdc_bp.route("/api/designs", methods=["DELETE"])
def api_delete_all_designs():
    """Delete all quality designs."""
    conn = _get_conn()
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM qdc_designs").fetchall()]
        conn.execute("DELETE FROM qdc_designs")
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "deleted": len(ids)})


# ===================================================================
# API Endpoints — Assessments
# ===================================================================


@qdc_bp.route("/api/designs/<design_id>/assess", methods=["POST"])
def api_assess_design(design_id: str):
    """Run quality assessment on a design."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT graph_json FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Design not found"}), 404

        graph_raw = row[0] if not hasattr(row, "keys") else row["graph_json"]
        try:
            graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
        except (json.JSONDecodeError, TypeError):
            graph_data = {"nodes": [], "edges": []}

        # Run assessment
        result = assess_quality_design(graph_data)

        # Compute UQS from gate results if available
        gate_results_rows = _rows_to_list(
            conn.execute(
                "SELECT gate_id, status, evidence_json FROM qdc_gate_results "
                "WHERE design_id = %s ORDER BY executed_at DESC",
                (design_id,),
            ).fetchall()
        )
        uqs_result = {"uqs_score": 0.0, "dimensions": {}, "grade": "F"}
        if gate_results_rows:
            gate_inputs = []
            for gr in gate_results_rows:
                try:
                    ev = json.loads(gr.get("evidence_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    ev = {}
                gate_inputs.append(
                    {
                        "gate_id": gr.get("gate_id", ""),
                        "status": gr.get("status", "skip"),
                        "dimension": ev.get("dimension", "code_quality"),
                        "score": ev.get("score", 0.0),
                    }
                )
            uqs_result = compute_uqs(gate_inputs)

        # Map gates to SA-11
        sa11_mapping = {}
        node_types = [n.get("type", "") for n in graph_data.get("nodes", [])]
        for nt in node_types:
            gate_key = nt.replace("gate-", "") if nt.startswith("gate-") else None
            if gate_key:
                sa11_mapping[gate_key] = map_gate_to_sa11(gate_key)

        # Store assessment
        assess_id = _gen_id()
        now = _utcnow()
        conn.execute(
            "INSERT INTO qdc_assessments "
            "(id, design_id, assessment_type, findings_json, score, uqs_score, "
            "uqs_breakdown, sa11_mapping, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                assess_id,
                design_id,
                "quality",
                json.dumps(result.get("findings", [])),
                result.get("score", 0.0),
                uqs_result.get("uqs_score", 0.0),
                json.dumps(uqs_result.get("dimensions", {})),
                json.dumps(sa11_mapping),
                now,
            ),
        )

        # Store UQS history point
        conn.execute(
            "INSERT INTO qdc_uqs_history (id, design_id, uqs_score, dimension_scores, computed_at) VALUES (%s,%s,%s,%s,%s)",
            (
                _gen_id(),
                design_id,
                uqs_result.get("uqs_score", 0.0),
                json.dumps(uqs_result.get("dimensions", {})),
                now,
            ),
        )
        conn.commit()

        # Audit
        _audit(conn, design_id, "assess", f"Score={result.get('score', 0)}, UQS={uqs_result.get('uqs_score', 0)}")
        conn.commit()

    finally:
        conn.close()

    _record_decision(
        canvas_type="qdc",
        record_id=design_id,
        decision_type="quality_assessment",
        decision=f"UQS {uqs_result.get('uqs_score', 0):.2f} — Score {result.get('score', 0)}, Grade {uqs_result.get('grade', '?')}",
        rationale=f"SA-11 mappings: {len(sa11_mapping)}, findings: {len(result.get('findings', []))}",
        model_used=None,
        confidence=uqs_result.get("uqs_score"),
    )

    return jsonify(
        {
            "status": "ok",
            "assessment_id": assess_id,
            "design_id": design_id,
            "assessment": result,
            "result": result,
            "uqs": uqs_result,
            "sa11_mapping": sa11_mapping,
        }
    )


@qdc_bp.route("/api/designs/<design_id>/uqs", methods=["GET"])
def api_get_uqs(design_id: str):
    """Get latest UQS score for a design."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT uqs_score, uqs_breakdown, created_at "
            "FROM qdc_assessments WHERE design_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (design_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"uqs_score": 0.0, "dimensions": {}, "grade": "F"})
    d = _row_to_dict(row)
    try:
        dims = json.loads(d.get("uqs_breakdown", "{}"))
    except (json.JSONDecodeError, TypeError):
        dims = {}
    score = d.get("uqs_score", 0.0)
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
    return jsonify({"status": "ok", "uqs_score": score, "dimensions": dims, "grade": grade, "assessed_at": d.get("created_at")})


@qdc_bp.route("/api/designs/<design_id>/uqs/history", methods=["GET"])
def api_get_uqs_history(design_id: str):
    """Get UQS trend from qdc_uqs_history."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT uqs_score, dimension_scores, computed_at "
                "FROM qdc_uqs_history WHERE design_id = %s "
                "ORDER BY computed_at ASC",
                (design_id,),
            ).fetchall()
        )
    finally:
        conn.close()
    trend = compute_uqs_trend(rows)
    return jsonify({"history": rows, "trend": trend})


@qdc_bp.route("/api/designs/<design_id>/gates", methods=["GET"])
def api_get_gates(design_id: str):
    """Get gate results for a design."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT id, gate_id, sa11_control, status, evidence_json, "
                "oscal_artifact, executed_at "
                "FROM qdc_gate_results WHERE design_id = %s "
                "ORDER BY executed_at DESC",
                (design_id,),
            ).fetchall()
        )
    finally:
        conn.close()
    return jsonify({"gates": rows, "count": len(rows)})


@qdc_bp.route("/api/designs/<design_id>/gaps", methods=["GET"])
def api_get_gaps(design_id: str):
    """Get quality gaps for a design based on latest assessment."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT graph_json FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
        if not row:
            return jsonify({"error": "Design not found"}), 404
        graph_raw = row[0] if not hasattr(row, "keys") else row["graph_json"]
        try:
            graph_data = json.loads(graph_raw) if isinstance(graph_raw, str) else graph_raw
        except (json.JSONDecodeError, TypeError):
            graph_data = {"nodes": [], "edges": []}

        result = assess_quality_design(graph_data)
        gaps = detect_quality_gaps(result)
    finally:
        conn.close()
    return jsonify({"status": "ok", "gaps": gaps, "count": len(gaps)})


@qdc_bp.route("/api/designs/<design_id>/evidence", methods=["GET"])
def api_get_evidence(design_id: str):
    """Get OSCAL evidence artifacts for a design."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT gate_id, sa11_control, oscal_artifact, executed_at "
                "FROM qdc_gate_results WHERE design_id = %s "
                "AND oscal_artifact IS NOT NULL AND oscal_artifact != '{}' "
                "ORDER BY executed_at DESC",
                (design_id,),
            ).fetchall()
        )
    finally:
        conn.close()
    evidence = []
    for r in rows:
        try:
            artifact = json.loads(r.get("oscal_artifact", "{}"))
        except (json.JSONDecodeError, TypeError):
            artifact = {}
        if artifact:
            evidence.append(artifact)
    return jsonify({"evidence": evidence, "count": len(evidence)})


# ===================================================================
# API Endpoints — Templates, Snippets, Runbooks, SOPs
# ===================================================================


@qdc_bp.route("/api/templates", methods=["GET"])
def api_list_templates():
    """List all templates as JSON."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT id, name, category, description, graph_json, "
                "compliance_target, tags FROM qdc_templates ORDER BY name"
            ).fetchall()
        )
    finally:
        conn.close()
    return jsonify({"templates": rows, "count": len(rows)})


@qdc_bp.route("/api/snippets", methods=["GET"])
def api_list_snippets():
    """List all snippets as JSON."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT id, name, category, description, graph_json, node_count, tags FROM qdc_snippets ORDER BY name"
            ).fetchall()
        )
    finally:
        conn.close()
    return jsonify({"snippets": rows, "count": len(rows)})


@qdc_bp.route("/api/snippets/<snippet_id>", methods=["GET"])
def api_get_snippet(snippet_id):
    """Get a single snippet by ID."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM qdc_snippets WHERE id = %s", (snippet_id,)).fetchone()
        if not row:
            return jsonify({"error": "Snippet not found"}), 404
        snippet = dict(row) if hasattr(row, "keys") else {}
        return jsonify({"status": "ok", "snippet": snippet})
    finally:
        conn.close()


@qdc_bp.route("/api/runbooks", methods=["GET"])
def api_list_runbooks():
    """List all runbooks as JSON."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT id, name, trigger_gate, steps_json, auto_executable, "
                "confidence_threshold, run_count, last_run, created_at "
                "FROM qdc_runbooks ORDER BY name"
            ).fetchall()
        )
    finally:
        conn.close()
    return jsonify({"runbooks": rows, "count": len(rows)})


@qdc_bp.route("/api/runbooks/<runbook_id>/execute", methods=["POST"])
def api_execute_runbook(runbook_id: str):
    """Execute a runbook — update last_run and increment run_count."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT id, name, steps_json, auto_executable, confidence_threshold FROM qdc_runbooks WHERE id = %s",
            (runbook_id,),
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Runbook not found"}), 404

        rb = _row_to_dict(row)
        now = _utcnow()
        conn.execute(
            "UPDATE qdc_runbooks SET last_run = %s, run_count = run_count + 1, updated_at = %s WHERE id = %s",
            (now, now, runbook_id),
        )
        conn.commit()
    finally:
        conn.close()

    try:
        steps = json.loads(rb.get("steps_json", "[]"))
    except (json.JSONDecodeError, TypeError):
        steps = []

    return jsonify(
        {
            "status": "ok",
            "runbook_id": runbook_id,
            "name": rb.get("name", ""),
            "result": {"steps": steps, "executed_at": now},
        }
    )


@qdc_bp.route("/api/sops", methods=["GET"])
def api_list_sops():
    """List all SOPs as JSON."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT id, sop_number, title, version, frequency, audience, "
                "body_markdown, approval_status, classification, created_at, updated_at "
                "FROM qdc_sops ORDER BY sop_number"
            ).fetchall()
        )
    finally:
        conn.close()
    return jsonify({"sops": rows, "count": len(rows)})


@qdc_bp.route("/api/sops/<sop_id>", methods=["PUT"])
def api_update_sop(sop_id: str):
    """Update a SOP — body_markdown, approval_status."""
    data = request.get_json(silent=True) or {}
    conn = _get_conn()
    try:
        existing = conn.execute("SELECT id FROM qdc_sops WHERE id = %s", (sop_id,)).fetchone()
        if not existing:
            conn.close()
            return jsonify({"error": "SOP not found"}), 404

        now = _utcnow()
        updates = ["updated_at = ?"]
        params: list = [now]

        if "body_markdown" in data:
            updates.append("body_markdown = ?")
            params.append(data["body_markdown"])
        if "approval_status" in data:
            updates.append("approval_status = ?")
            params.append(data["approval_status"])
            if data["approval_status"] == "approved":
                updates.append("approved_at = ?")
                params.append(now)
                updates.append("approved_by = ?")
                params.append(data.get("approved_by", "system"))

        params.append(sop_id)
        conn.execute(
            f"UPDATE qdc_sops SET {', '.join(updates)} WHERE id = %s",  # noqa: S608
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "sop_id": sop_id, "updated_at": now})


# ===================================================================
# API Endpoints — Cross-Canvas
# ===================================================================


@qdc_bp.route("/api/cross-canvas", methods=["GET"])
def api_cross_canvas():
    """Aggregate quality scores from cross-canvas links."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT source_canvas, quality_score, last_synced FROM qdc_cross_canvas_links ORDER BY last_synced DESC"
            ).fetchall()
        )
    finally:
        conn.close()

    canvas_scores: dict[str, float] = {}
    for r in rows:
        canvas_key = r.get("source_canvas", "")
        if canvas_key and canvas_key not in canvas_scores:
            canvas_scores[canvas_key] = r.get("quality_score", 0.0)

    aggregate = aggregate_cross_canvas_quality(canvas_scores)
    return jsonify({"links": rows, "aggregate": aggregate})


# ===================================================================
# API Endpoints — Export
# ===================================================================


@qdc_bp.route("/api/export/<design_id>/json", methods=["POST"])
def api_export_json(design_id: str):
    """Export design as JSON download."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "Design not found"}), 404

    design = _row_to_dict(row)
    response = jsonify(design)
    response.headers["Content-Disposition"] = f'attachment; filename="qdc-{design_id}.json"'
    return response


@qdc_bp.route("/api/export/<design_id>/markdown", methods=["POST"])
def api_export_markdown(design_id: str):
    """Export design as markdown."""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Design not found"}), 404
        design = _row_to_dict(row)

        assess_row = conn.execute(
            "SELECT score, uqs_score, findings_json, sa11_mapping "
            "FROM qdc_assessments WHERE design_id = %s ORDER BY created_at DESC LIMIT 1",
            (design_id,),
        ).fetchone()
        assessment = _row_to_dict(assess_row) if assess_row else {}
    finally:
        conn.close()

    try:
        graph = json.loads(design.get("graph_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        graph = {"nodes": [], "edges": []}

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    lines = [
        f"# Quality Design Canvas: {design.get('name', 'Untitled')}",
        f"\n**Classification:** {design.get('classification', 'CUI')}",
        f"**Created:** {design.get('created_at', '')}",
        f"**Updated:** {design.get('updated_at', '')}",
        f"\n## Nodes ({len(nodes)})\n",
    ]
    for n in nodes:
        lines.append(f"- **{n.get('label', n.get('type', ''))}** ({n.get('type', '')})")
    lines.append(f"\n## Edges ({len(edges)})\n")
    for e in edges:
        lines.append(f"- {e.get('source', '')} -> {e.get('target', '')} {e.get('label', '')}")

    if assessment:
        lines.append("\n## Assessment\n")
        lines.append(f"- **Score:** {assessment.get('score', 0)}")
        lines.append(f"- **UQS:** {assessment.get('uqs_score', 0)}")

    md = "\n".join(lines)
    return jsonify({"markdown": md, "design_id": design_id})


@qdc_bp.route("/api/export/<design_id>/oscal", methods=["POST"])
def api_export_oscal(design_id: str):
    """Export OSCAL evidence pack for a design."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT gate_id, status, evidence_json "
                "FROM qdc_gate_results WHERE design_id = %s "
                "ORDER BY executed_at DESC",
                (design_id,),
            ).fetchall()
        )
    finally:
        conn.close()

    evidence_pack = []
    for r in rows:
        evidence = emit_oscal_evidence(
            {
                "gate_id": r.get("gate_id", "unknown"),
                "status": r.get("status", "fail"),
            }
        )
        evidence_pack.append(evidence)

    return jsonify(
        {
            "design_id": design_id,
            "evidence_count": len(evidence_pack),
            "findings": evidence_pack,
            "exported_at": _utcnow(),
        }
    )


# ===================================================================
# API Endpoints — Versions
# ===================================================================


@qdc_bp.route("/api/versions/<design_id>", methods=["GET"])
def api_list_versions(design_id: str):
    """List version history for a design."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT id, version_number, change_summary, user_id, created_at "
                "FROM qdc_versions WHERE design_id = %s "
                "ORDER BY version_number DESC",
                (design_id,),
            ).fetchall()
        )
    finally:
        conn.close()
    return jsonify({"versions": rows, "count": len(rows)})


@qdc_bp.route("/api/versions/<design_id>", methods=["POST"])
def api_create_version(design_id: str):
    """Create a version snapshot of the current design."""
    data = request.get_json(silent=True) or {}
    conn = _get_conn()
    try:
        row = conn.execute("SELECT graph_json FROM qdc_designs WHERE id = %s", (design_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Design not found"}), 404

        graph_json = row[0] if not hasattr(row, "keys") else row["graph_json"]

        # Get next version number
        max_row = conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) FROM qdc_versions WHERE design_id = %s",
            (design_id,),
        ).fetchone()
        next_ver = (max_row[0] if max_row else 0) + 1

        vid = _gen_id()
        conn.execute(
            "INSERT INTO qdc_versions "
            "(id, design_id, version_number, graph_json, change_summary, "
            "user_id, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                vid,
                design_id,
                next_ver,
                graph_json,
                data.get("change_summary", ""),
                data.get("user_id", "system"),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "version_id": vid, "version_number": next_ver})


@qdc_bp.route("/api/versions/<design_id>/restore/<version_id>", methods=["POST"])
def api_restore_version(design_id: str, version_id: str):
    """Restore a design from a version snapshot."""
    conn = _get_conn()
    try:
        ver_row = conn.execute(
            "SELECT graph_json, version_number FROM qdc_versions WHERE id = %s AND design_id = %s",
            (version_id, design_id),
        ).fetchone()
        if not ver_row:
            conn.close()
            return jsonify({"error": "Version not found"}), 404

        graph_json = ver_row[0] if not hasattr(ver_row, "keys") else ver_row["graph_json"]
        ver_num = ver_row[1] if not hasattr(ver_row, "keys") else ver_row["version_number"]

        now = _utcnow()
        conn.execute(
            "UPDATE qdc_designs SET graph_json = %s, updated_at = %s WHERE id = %s",
            (graph_json, now, design_id),
        )
        conn.commit()

        _audit(conn, design_id, "restore", f"Restored from version {ver_num} ({version_id})")
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "design_id": design_id, "restored_version": version_id})


# ===================================================================
# API Endpoints — Collaboration
# ===================================================================

_COLLAB_COLORS = [
    "#2196f3",
    "#4caf50",
    "#ff9800",
    "#e91e63",
    "#9c27b0",
    "#00bcd4",
    "#ff5722",
    "#607d8b",
]


@qdc_bp.route("/api/collab/<design_id>/join", methods=["POST"])
def api_collab_join(design_id: str):
    """Join a collaboration session for a design."""
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id", f"anon-{uuid.uuid4().hex[:6]}")
    user_name = data.get("user_name", user_id)

    conn = _get_conn()
    try:
        # Pick a color not yet taken
        taken = conn.execute(
            "SELECT color FROM qdc_collab_sessions WHERE design_id = %s AND is_active = 1",
            (design_id,),
        ).fetchall()
        taken_colors = {r[0] for r in taken}
        color = "#2196f3"
        for c in _COLLAB_COLORS:
            if c not in taken_colors:
                color = c
                break

        sid = _gen_id()
        now = _utcnow()
        conn.execute(
            "INSERT INTO qdc_collab_sessions "
            "(id, design_id, user_id, user_name, color, joined_at, last_seen, is_active) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,1)",
            (sid, design_id, user_id, user_name, color, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "session_id": sid, "color": color, "user_id": user_id})


@qdc_bp.route("/api/collab/<design_id>/leave", methods=["POST"])
def api_collab_leave(design_id: str):
    """Leave a collaboration session."""
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")

    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE qdc_collab_sessions SET is_active = 0 WHERE id = %s AND design_id = %s",
            (session_id, design_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True, "left": session_id})


@qdc_bp.route("/api/collab/<design_id>/push", methods=["POST"])
def api_collab_push(design_id: str):
    """Push an operation to the collaboration session and persist it.

    cnr-qdc-05: previously this endpoint reported ``{"ok": True}`` but dropped the
    operation on the floor — poll then returned only presence, so no operation
    sync ever occurred. Now the op is appended to ``qdc_collab_ops`` with a
    monotonic per-design ``seq`` (same MAX+1 pattern as version snapshots) so
    peers can replay it via poll's ``?since=`` cursor.
    """
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id", "")
    user_id = data.get("user_id", "")
    operation = data.get("operation", {})

    conn = _get_conn()
    try:
        now = _utcnow()
        max_row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM qdc_collab_ops WHERE design_id = %s",
            (design_id,),
        ).fetchone()
        next_seq = (max_row[0] if max_row else 0) + 1
        conn.execute(
            "INSERT INTO qdc_collab_ops "
            "(id, design_id, seq, session_id, user_id, operation, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (_gen_id(), design_id, next_seq, session_id, user_id, json.dumps(operation), now),
        )
        conn.execute(
            "UPDATE qdc_collab_sessions SET last_seen = %s WHERE id = %s",
            (now, session_id),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"ok": True, "seq": next_seq, "received_at": now, "operation": operation})


@qdc_bp.route("/api/collab/<design_id>/poll", methods=["GET"])
def api_collab_poll(design_id: str):
    """Poll for collaboration updates — participants plus operations since a cursor.

    cnr-qdc-05: replays operations pushed by peers. Pass ``?since=<seq>`` (the
    ``cursor`` from the previous poll, default 0) to receive only newer ops; the
    returned ``cursor`` is the highest seq seen so the client can advance.
    """
    try:
        since = int(request.args.get("since", 0))
    except (TypeError, ValueError):
        since = 0

    conn = _get_conn()
    try:
        participants = _safe_rows(
            conn,
            "SELECT id, user_id, user_name, color, last_seen "
            "FROM qdc_collab_sessions "
            "WHERE design_id = %s AND is_active = 1 "
            "ORDER BY joined_at",
            (design_id,),
        )
        operations = _safe_rows(
            conn,
            "SELECT seq, session_id, user_id, operation, created_at "
            "FROM qdc_collab_ops WHERE design_id = %s AND seq > %s ORDER BY seq ASC",
            (design_id, since),
        )
        row = conn.execute(
            "SELECT graph_json, updated_at FROM qdc_designs WHERE id = %s",
            (design_id,),
        ).fetchone()
        design_state = _row_to_dict(row) if row else {}
    finally:
        conn.close()

    for op in operations:
        raw = op.get("operation", "{}")
        try:
            op["operation"] = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            op["operation"] = {}

    cursor = operations[-1]["seq"] if operations else since
    return jsonify(
        {
            "participants": participants,
            "operations": operations,
            "cursor": cursor,
            "design_updated_at": design_state.get("updated_at", ""),
        }
    )


@qdc_bp.route("/api/collab/<design_id>/participants", methods=["GET"])
def api_collab_participants(design_id: str):
    """List active participants in a collaboration session."""
    conn = _get_conn()
    try:
        rows = _rows_to_list(
            conn.execute(
                "SELECT id, user_id, user_name, color, joined_at, last_seen "
                "FROM qdc_collab_sessions "
                "WHERE design_id = %s AND is_active = 1 "
                "ORDER BY joined_at",
                (design_id,),
            ).fetchall()
        )
    finally:
        conn.close()
    return jsonify({"participants": rows, "count": len(rows)})


# ===================================================================
# API Endpoints — IQE natural-language query (cnr-qdc-03)
# ===================================================================


@qdc_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    """Answer a plain-English question over QDC collections via IQE.

    Mirrors BI Studio's /api/iqe-query. Importing the adapter registers the
    qdc.* collections on the module-level IQE Executor.
    """
    import importlib

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    execute = data.get("execute", True)
    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        importlib.import_module("tools.iqe.adapters.qdc")
    except Exception:
        pass

    iqe_str = ""
    try:
        from tools.iqe.executor import execute_query
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import parse as _parse

        collections = [
            "qdc.designs",
            "qdc.assessments",
            "qdc.gate_results",
            "qdc.ai_decisions",
        ]
        translation = nl_to_iqe(question, collections=collections)
        iqe_str = translation["iqe"]
        ast = _parse(iqe_str)
        results = execute_query(ast, None) if execute else []
        return jsonify({"iqe": iqe_str, "results": results, "row_count": len(results)})
    except Exception as exc:
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500


# ── Gate Execution API ─────────────────────────────────────────────────────


@qdc_bp.route("/api/gates/execute/<gate_id>", methods=["POST"])
def api_execute_gate(gate_id):
    """Execute a single quality gate and return results."""
    try:
        from tools.qdc_canvas.gate_executor import execute_gate

        data = request.get_json(force=True, silent=True) or {}
        project_dir = data.get("project_dir")
        result = execute_gate(gate_id, project_dir)
        try:
            from tools.canvas.event_bus import publish as _eb_publish
            _eb_publish("qdc", "qdc.gate.executed", {
                "gate_id": gate_id,
                "design_id": data.get("design_id", ""),
                "status": result.get("status", "unknown"),
                "passed": result.get("status") == "pass",
                "score": result.get("score", 0),
            }, target_canvas="pdc")
        except Exception:
            pass
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc), "gate_id": gate_id}), 500


@qdc_bp.route("/api/gates/execute-all", methods=["POST"])
def api_execute_all_gates():
    """Execute all registered quality gates."""
    try:
        from tools.qdc_canvas.gate_executor import execute_all_gates

        data = request.get_json(force=True, silent=True) or {}
        project_dir = data.get("project_dir")
        results = execute_all_gates(project_dir)
        return jsonify({"gates": results, "count": len(results)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@qdc_bp.route("/api/gates/tools", methods=["GET"])
def api_gate_tools():
    """Check which QA/QC tools are installed and available."""
    try:
        from tools.qdc_canvas.gate_executor import check_tool_availability

        return jsonify(check_tool_availability())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@qdc_bp.route("/api/gates/auto-fix/<gate_id>", methods=["POST"])
def api_auto_fix_gate(gate_id):
    """Attempt auto-fix for a specific gate's findings."""
    try:
        from tools.qdc_canvas.gate_executor import auto_fix, execute_gate

        result = execute_gate(gate_id)
        fix_result = auto_fix(result)
        return jsonify(fix_result)
    except Exception as exc:
        return jsonify({"error": str(exc), "gate_id": gate_id}), 500


@qdc_bp.route("/api/oracle/quality", methods=["GET"])
def api_oracle_quality():
    """Run Oracle Quality Lens predictions."""
    try:
        from tools.oracle.lenses.lens_quality import QualityLens

        lens = QualityLens()
        predictions = lens.run()
        return jsonify({"predictions": [p.to_dict() for p in predictions], "count": len(predictions)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@qdc_bp.route("/api/genesis/quality", methods=["POST"])
def api_genesis_quality():
    """Run Genesis Quality Reflex (scan + detect + remediate + GKP)."""
    try:
        from tools.genesis.reflexes.quality import run_reflex

        result = run_reflex()
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _compute_qdc_governance(graph_data: dict) -> dict:
    """Quality governance check — DAMA DMBOK / ISO 25010 / SA-11 / TMMi aligned."""
    nodes = graph_data.get("nodes", [])
    types = [n.get("type", "").lower() for n in nodes]
    labels = [str(n.get("label", "")).lower() for n in nodes]

    def _any(*pfx): return any(any(t.startswith(p) for p in pfx) for t in types)
    def _lbl(*kws): return any(kw in l for l in labels for kw in kws)

    CHECKS = [
        ("Quality Gate Defined",           "Quality Gates",      "CAT1", _any("gate-") or _lbl("quality gate","qg","pass/fail","threshold gate")),
        ("Automated Testing Nodes",        "Test Coverage",      "CAT1", _any("test-","tst-") or _lbl("unit test","integration test","automated test","test suite","pytest","jest","junit")),
        ("Code Coverage Measurement",      "Test Coverage",      "CAT2", _lbl("coverage","jacoco","istanbul","lcov","coverage threshold","80%","90%")),
        ("Static Analysis (SAST)",         "Test Coverage",      "CAT2", _any("sast-","lint-") or _lbl("sast","sonar","lint","bandit","semgrep","static analysis")),
        ("Performance Benchmarks",         "Test Coverage",      "CAT2", _lbl("benchmark","performance test","load test","k6","locust","jmeter","gatling")),
        ("Data Quality Profiling",         "Data Quality",       "CAT1", _any("dq-","profile-") or _lbl("data profile","profil","null check","row count","completeness")),
        ("Anomaly / Outlier Detection",    "Data Quality",       "CAT2", _lbl("anomaly","outlier","drift","schema change","data drift")),
        ("Quality Metrics Dashboard",      "Observability",      "CAT2", _lbl("dashboard","scorecard","kpi","metric","reporting","quality report")),
        ("Defect Tracking Integration",    "Observability",      "CAT2", _lbl("defect","bug","issue","jira","servicenow","ticket","remediation")),
        ("SLA / SLO Defined",             "SLO Governance",     "CAT1", _lbl("sla","slo","service level","error budget","availability","uptime")),
        ("SA-11 / NIST Compliance",        "Compliance",         "CAT1", _any("comp-","nist-") or _lbl("sa-11","nist","800-53","control","compliance","fedramp","rmf")),
        ("TMMi / CMMI Process Level",      "Compliance",         "CAT2", _lbl("tmmi","cmmi","process level","maturity","capability")),
        ("Audit Logging of Quality Ops",   "Compliance",         "CAT2", _lbl("audit","log quality","change log","quality trail","immutable")),
        ("Remediation Tracking",           "Observability",      "CAT3", _any("rem-") or _lbl("remediat","fix","action item","corrective")),
        ("Cross-Canvas Quality Targets",   "SLO Governance",     "CAT3", _lbl("cross-canvas","data mesh","target","contract","expected quality")),
    ]

    PILLARS = ["Quality Gates", "Test Coverage", "Data Quality", "Observability", "SLO Governance", "Compliance"]
    WEIGHTS = {"CAT1": 3, "CAT2": 2, "CAT3": 1}
    MATURITY = [
        (0,  "L1 — Initial",    "No formal quality processes defined."),
        (30, "L2 — Developing", "Basic testing and quality gates present."),
        (55, "L3 — Defined",    "Systematic quality with metrics and SLOs."),
        (70, "L4 — Managed",    "Measured quality with automated enforcement."),
        (85, "L5 — Optimised",  "Continuous improvement with predictive quality analytics."),
    ]

    check_results, total_w, passed_w = [], 0, 0
    cats = {p: {"passed": 0, "total": 0, "pct": 0} for p in PILLARS}
    for title, pillar, sev, passed in CHECKS:
        w = WEIGHTS[sev]; total_w += w
        status = "pass" if passed else "fail"
        if passed: passed_w += w
        cats.setdefault(pillar, {"passed": 0, "total": 0, "pct": 0})
        cats[pillar]["total"] += 1
        if passed: cats[pillar]["passed"] += 1
        check_results.append({"title": title, "pillar": pillar, "severity": sev,
                               "status": status, "weight": w, "detail": ""})
    for c in cats.values():
        c["pct"] = round(c["passed"] / c["total"] * 100) if c["total"] else 0

    score = round(passed_w / total_w * 100) if total_w else 0
    grade = "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70 else "D" if score >= 60 else "F"
    mat_level = sum(1 for t, *_ in MATURITY if score >= t)
    mat_label, mat_desc = MATURITY[mat_level - 1][1], MATURITY[mat_level - 1][2]

    recs = [{"title": c["title"], "pillar": c["pillar"], "priority": c["severity"]}
            for c in check_results if c["status"] == "fail"]
    recs.sort(key=lambda r: {"CAT1": 0, "CAT2": 1, "CAT3": 2}[r["priority"]])
    return {
        "score": score, "grade": grade,
        "maturity": {"level": mat_level, "label": mat_label.split(" — ")[1], "description": mat_desc},
        "checks": check_results, "categories": cats, "recommendations": recs,
        "total_checks": len(CHECKS), "passed_checks": sum(1 for c in check_results if c["status"] == "pass"),
        "assessed_at": _utcnow(),
    }


@qdc_bp.route("/api/designs/<design_id>/governance", methods=["POST"])
def qdc_api_governance(design_id):
    """Run quality governance framework check."""
    conn = _get_conn()
    row = conn.execute("SELECT graph_json FROM qdc_designs WHERE id=%s", (design_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "Not found"}), 404
    try:
        graph_data = json.loads(row["graph_json"]) if isinstance(row["graph_json"], str) else row["graph_json"]
    except (json.JSONDecodeError, TypeError):
        conn.close()
        return jsonify({"error": "Invalid graph data"}), 400

    result = _compute_qdc_governance(graph_data)

    assess_id = _gen_id()
    conn.execute(
        "INSERT INTO qdc_assessments "
        "(id, design_id, assessment_type, findings_json, score, uqs_score, "
        "uqs_breakdown, sa11_mapping, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (assess_id, design_id, "governance",
         json.dumps([{"title": c["title"], "severity": c["severity"], "status": c["status"]}
                     for c in result["checks"]]),
         result["score"], 0.0, json.dumps(result["categories"]), "{}", _utcnow()),
    )
    conn.commit()
    conn.close()
    return jsonify(result)
