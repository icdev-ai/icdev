# CUI // SP-CTI
"""Workflow Forms Canvas — Flask Blueprint.

Routes:
  GET  /workflow-canvas/                     Index (stats + recent)
  GET  /workflow-canvas/forms                Form library
  GET  /workflow-canvas/forms/new            New form builder
  GET  /workflow-canvas/forms/<id>           Form detail
  GET  /workflow-canvas/forms/<id>/edit      Edit form
  GET  /workflow-canvas/workflows            Workflow library
  GET  /workflow-canvas/workflows/new        New workflow builder
  GET  /workflow-canvas/workflows/<id>       Workflow detail
  GET  /workflow-canvas/workflows/<id>/edit  Edit workflow
  GET  /workflow-canvas/templates            Template library

  POST /workflow-canvas/api/forms/<id>/export/<fmt>     Download form as pptx|pdf|docx
  POST /workflow-canvas/api/workflows/<id>/export/<fmt> Download workflow as pptx|pdf|docx
  GET  /workflow-canvas/api/branding/<type>/<id>        Get branding
  POST /workflow-canvas/api/branding/<type>/<id>        Save branding
  GET  /workflow-canvas/api/forms                       JSON list of forms
  GET  /workflow-canvas/api/workflows                   JSON list of workflows
  POST /workflow-canvas/api/iqe-query                   IQE natural-language query
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection, sql_placeholder
from tools.logging.icdev_logger import get_logger
from tools.workflow_canvas.constants import (
    EXPORT_FORMATS,
    INDUSTRY_CATEGORIES,
    DEFAULT_PRIMARY_COLOR,
    DEFAULT_SECONDARY_COLOR,
)
from tools.studio.form_builder import (
    FIELD_TYPES,
    FORM_TEMPLATES,
    list_forms,
    get_form,
    create_form,
    update_form,
    delete_form,
    list_submissions,
    submit_form,
)

logger = get_logger(__name__)

_INIT_DONE = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "wfc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


_WFC_MIGRATION_PG = """
CREATE TABLE IF NOT EXISTS wfc_branding (
    id               TEXT PRIMARY KEY,
    entity_type      TEXT NOT NULL CHECK(entity_type IN ('form','workflow')),
    entity_id        TEXT NOT NULL,
    org_name         TEXT,
    logo_data        TEXT,
    primary_color    TEXT DEFAULT '#1a365d',
    secondary_color  TEXT DEFAULT '#c8a951',
    header_html      TEXT,
    footer_html      TEXT,
    show_classification INTEGER DEFAULT 1,
    created_at       TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    updated_at       TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    UNIQUE(entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS wfc_workflow_form_nodes (
    id                   TEXT PRIMARY KEY,
    workflow_id          TEXT NOT NULL,
    node_key             TEXT NOT NULL,
    form_id              TEXT NOT NULL,
    node_label           TEXT,
    required_before_next INTEGER DEFAULT 1,
    created_at           TEXT DEFAULT to_char(NOW() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS"Z"')
);
CREATE INDEX IF NOT EXISTS idx_wfc_branding_entity ON wfc_branding(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_wfc_form_nodes_workflow ON wfc_workflow_form_nodes(workflow_id);
"""

_WFC_MIGRATION_SQLITE = """
CREATE TABLE IF NOT EXISTS wfc_branding (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL CHECK(entity_type IN ('form','workflow')),
    entity_id TEXT NOT NULL,
    org_name TEXT,
    logo_data TEXT,
    primary_color TEXT DEFAULT '#1a365d',
    secondary_color TEXT DEFAULT '#c8a951',
    header_html TEXT,
    footer_html TEXT,
    show_classification INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS wfc_workflow_form_nodes (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    node_key TEXT NOT NULL,
    form_id TEXT NOT NULL,
    node_label TEXT,
    required_before_next INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_wfc_branding_entity ON wfc_branding(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_wfc_form_nodes_workflow ON wfc_workflow_form_nodes(workflow_id);
"""


def _ensure_init() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        from tools.studio.init_db import init_db as studio_init
        studio_init()
    except Exception as exc:
        logger.warning("wfc: studio DB init error: %s", exc)
    try:
        conn = get_connection()
        is_pg = hasattr(conn, 'server_version') or 'psycopg2' in type(conn).__module__
        migration_sql = _WFC_MIGRATION_PG if is_pg else _WFC_MIGRATION_SQLITE
        # Execute each statement separately (executescript is SQLite-only)
        for stmt in migration_sql.strip().split(';'):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    pass  # Index may already exist — continue
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("wfc: migration error: %s", exc)
    _INIT_DONE = True


def create_wfc_blueprint() -> Blueprint:
    bp = Blueprint(
        "workflow_canvas",
        __name__,
        url_prefix="/workflow-canvas",
        template_folder="../../tools/dashboard/templates",
    )

    @bp.before_request
    def _init():
        _ensure_init()

    # ── Branding helpers ──────────────────────────────────────────────────

    def _ph(conn):
        return sql_placeholder(conn)

    def _get_branding(entity_type: str, entity_id: str) -> dict:
        conn = get_connection()
        try:
            ph = _ph(conn)
            row = conn.execute(
                f"SELECT * FROM wfc_branding WHERE entity_type={ph} AND entity_id={ph}",
                (entity_type, entity_id),
            ).fetchone()
            if row:
                return dict(row)
            return {
                "org_name": "",
                "logo_data": "",
                "primary_color": DEFAULT_PRIMARY_COLOR,
                "secondary_color": DEFAULT_SECONDARY_COLOR,
                "header_html": "",
                "footer_html": "",
                "show_classification": 1,
            }
        finally:
            conn.close()

    def _save_branding(entity_type: str, entity_id: str, data: dict) -> None:
        conn = get_connection()
        try:
            ph = _ph(conn)
            existing = conn.execute(
                f"SELECT id FROM wfc_branding WHERE entity_type={ph} AND entity_id={ph}",
                (entity_type, entity_id),
            ).fetchone()
            if existing:
                conn.execute(
                    f"""UPDATE wfc_branding SET
                       org_name={ph}, logo_data={ph}, primary_color={ph}, secondary_color={ph},
                       header_html={ph}, footer_html={ph}, show_classification={ph}, updated_at={ph}
                       WHERE entity_type={ph} AND entity_id={ph}""",
                    (
                        data.get("org_name", ""),
                        data.get("logo_data", ""),
                        data.get("primary_color", DEFAULT_PRIMARY_COLOR),
                        data.get("secondary_color", DEFAULT_SECONDARY_COLOR),
                        data.get("header_html", ""),
                        data.get("footer_html", ""),
                        int(data.get("show_classification", 1)),
                        _now_iso(),
                        entity_type,
                        entity_id,
                    ),
                )
            else:
                conn.execute(
                    f"""INSERT INTO wfc_branding
                       (id, entity_type, entity_id, org_name, logo_data,
                        primary_color, secondary_color, header_html, footer_html,
                        show_classification, created_at, updated_at)
                       VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                    (
                        _new_id("brd"),
                        entity_type,
                        entity_id,
                        data.get("org_name", ""),
                        data.get("logo_data", ""),
                        data.get("primary_color", DEFAULT_PRIMARY_COLOR),
                        data.get("secondary_color", DEFAULT_SECONDARY_COLOR),
                        data.get("header_html", ""),
                        data.get("footer_html", ""),
                        int(data.get("show_classification", 1)),
                        _now_iso(),
                        _now_iso(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    # ── Workflow helpers ──────────────────────────────────────────────────

    def _list_workflows() -> list[dict]:
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM studio_workflows ORDER BY updated_at DESC LIMIT 100"
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def _get_workflow(workflow_id: str) -> dict | None:
        conn = get_connection()
        try:
            ph = _ph(conn)
            row = conn.execute(
                f"SELECT * FROM studio_workflows WHERE workflow_id={ph}", (workflow_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _count_submissions_today() -> int:
        conn = get_connection()
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            ph = _ph(conn)
            row = conn.execute(
                f"SELECT COUNT(*) FROM studio_form_submissions WHERE submitted_at LIKE {ph}",
                (f"{today}%",),
            ).fetchone()
            return row[0] if row else 0
        except Exception:
            return 0
        finally:
            conn.close()

    # ── Page routes ───────────────────────────────────────────────────────

    @bp.route("/")
    def index():
        forms = list_forms()
        workflows = _list_workflows()
        return render_template(
            "workflow_canvas/page.html",
            forms=forms[:6],
            workflows=workflows[:6],
            stats={
                "total_forms": len(forms),
                "published_forms": sum(1 for f in forms if f.get("status") == "published"),
                "total_workflows": len(workflows),
                "submissions_today": _count_submissions_today(),
                "templates_available": len(FORM_TEMPLATES),
            },
        )

    @bp.route("/forms")
    def form_list():
        industry = request.args.get("industry", "All")
        forms = list_forms()
        return render_template(
            "workflow_canvas/form_list.html",
            forms=forms,
            industry=industry,
            industry_categories=INDUSTRY_CATEGORIES,
        )

    @bp.route("/forms/new")
    def form_new():
        return render_template(
            "workflow_canvas/form_builder.html",
            form=None,
            branding={
                "org_name": "",
                "logo_data": "",
                "primary_color": DEFAULT_PRIMARY_COLOR,
                "secondary_color": DEFAULT_SECONDARY_COLOR,
                "header_html": "",
                "footer_html": "",
                "show_classification": 1,
            },
            field_types=FIELD_TYPES,
            templates=FORM_TEMPLATES,
            industry_categories=INDUSTRY_CATEGORIES,
            export_formats=EXPORT_FORMATS,
            is_new=True,
        )

    @bp.route("/forms/<form_id>")
    def form_detail(form_id: str):
        form = get_form(form_id)
        if not form:
            return render_template("errors/404.html"), 404
        submissions = list_submissions(form_id)
        branding = _get_branding("form", form_id)
        schema = json.loads(form.get("schema_json", "{}"))
        fields = schema.get("_fields", [])
        return render_template(
            "workflow_canvas/form_detail.html",
            form=form,
            fields=fields,
            submissions=submissions,
            branding=branding,
            export_formats=EXPORT_FORMATS,
        )

    @bp.route("/forms/<form_id>/edit")
    def form_edit(form_id: str):
        form = get_form(form_id)
        if not form:
            return render_template("errors/404.html"), 404
        branding = _get_branding("form", form_id)
        schema = json.loads(form.get("schema_json", "{}"))
        form["_fields"] = schema.get("_fields", [])
        return render_template(
            "workflow_canvas/form_builder.html",
            form=form,
            branding=branding,
            field_types=FIELD_TYPES,
            templates=FORM_TEMPLATES,
            industry_categories=INDUSTRY_CATEGORIES,
            export_formats=EXPORT_FORMATS,
            is_new=False,
        )

    @bp.route("/workflows")
    def workflow_list():
        workflows = _list_workflows()
        return render_template(
            "workflow_canvas/workflow_list.html",
            workflows=workflows,
        )

    @bp.route("/workflows/new")
    def workflow_new():
        return render_template(
            "workflow_canvas/workflow_builder.html",
            workflow=None,
            branding={
                "org_name": "",
                "primary_color": DEFAULT_PRIMARY_COLOR,
                "secondary_color": DEFAULT_SECONDARY_COLOR,
                "header_html": "",
                "footer_html": "",
            },
            export_formats=EXPORT_FORMATS,
            forms=list_forms(status="published"),
            is_new=True,
        )

    @bp.route("/workflows/<workflow_id>")
    def workflow_detail(workflow_id: str):
        workflow = _get_workflow(workflow_id)
        if not workflow:
            return render_template("errors/404.html"), 404
        branding = _get_branding("workflow", workflow_id)
        conn = get_connection()
        try:
            ph = _ph(conn)
            runs = conn.execute(
                f"SELECT * FROM studio_workflow_runs WHERE workflow_id={ph} ORDER BY started_at DESC LIMIT 20",
                (workflow_id,),
            ).fetchall()
            run_list = [dict(r) for r in runs]
        except Exception:
            run_list = []
        finally:
            conn.close()
        return render_template(
            "workflow_canvas/workflow_detail.html",
            workflow=workflow,
            branding=branding,
            runs=run_list,
            export_formats=EXPORT_FORMATS,
        )

    @bp.route("/workflows/<workflow_id>/edit")
    def workflow_edit(workflow_id: str):
        workflow = _get_workflow(workflow_id)
        if not workflow:
            return render_template("errors/404.html"), 404
        branding = _get_branding("workflow", workflow_id)
        return render_template(
            "workflow_canvas/workflow_builder.html",
            workflow=workflow,
            branding=branding,
            export_formats=EXPORT_FORMATS,
            forms=list_forms(status="published"),
            is_new=False,
        )

    @bp.route("/templates")
    def template_library():
        industry = request.args.get("industry", "All")
        templates = FORM_TEMPLATES
        if industry and industry != "All":
            templates = [t for t in templates if t.get("industry") == industry
                         or t.get("category") == industry.lower().replace("/", "_")]
        return render_template(
            "workflow_canvas/template_library.html",
            templates=templates,
            industry=industry,
            industry_categories=INDUSTRY_CATEGORIES,
            all_templates=FORM_TEMPLATES,
        )

    # ── AI-assisted form generation ───────────────────────────────────────

    @bp.route("/api/forms/generate", methods=["POST"])
    def api_generate_form():
        """Generate form fields from a natural-language description using LLM."""
        data = request.get_json(force=True) or {}
        description = (data.get("description") or "").strip()
        industry = data.get("industry", "")
        if not description:
            return jsonify({"error": "description is required"}), 400

        prompt = f"""You are a form designer. Given the description below, generate a JSON list of form fields.
Each field must be a JSON object with these keys:
  - id: unique string like "f1", "f2" etc.
  - type: one of text, textarea, number, date, select, multiselect, checkbox, email, file, richtext
  - label: human-readable label
  - required: true or false
  - options: array of strings (only for select/multiselect types)
  - placeholder: optional hint text

Return ONLY a valid JSON array. No markdown, no explanation.

Industry context: {industry or 'General'}
Form description: {description}"""

        try:
            from tools.llm.router import LLMRouter
            router = LLMRouter()
            result = router.invoke("form_generation", prompt)
            raw = (result or {}).get("content") or (result or {}).get("text") or str(result or "")
            import re
            # Extract JSON array from the response
            match = re.search(r"\[[\s\S]*\]", raw)
            if not match:
                return jsonify({"error": "LLM did not return a valid JSON array", "raw": raw[:500]}), 500
            import json as _json
            fields = _json.loads(match.group())
            # Validate each field has minimum required keys
            valid_types = {"text","textarea","number","date","select","multiselect","checkbox","email","file","richtext"}
            cleaned = []
            for i, f in enumerate(fields):
                if not isinstance(f, dict):
                    continue
                cleaned.append({
                    "id": str(f.get("id") or f"f{i+1}"),
                    "type": str(f.get("type","text")) if f.get("type") in valid_types else "text",
                    "label": str(f.get("label") or f"Field {i+1}"),
                    "required": bool(f.get("required", False)),
                    "placeholder": str(f.get("placeholder","")) if f.get("placeholder") else "",
                    "options": [str(o) for o in f["options"]] if f.get("options") and isinstance(f["options"], list) else None,
                })
            return jsonify({"status": "ok", "fields": cleaned, "count": len(cleaned)})
        except Exception as exc:
            logger.error("wfc generate_form LLM error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    # ── API routes ────────────────────────────────────────────────────────

    @bp.route("/api/forms")
    def api_forms():
        forms = list_forms()
        return jsonify({"forms": forms})

    @bp.route("/api/workflows")
    def api_workflows():
        return jsonify({"workflows": _list_workflows()})

    @bp.route("/api/forms", methods=["POST"])
    def api_create_form():
        data = request.get_json(force=True) or {}
        result = create_form(
            name=data.get("name", "Untitled Form"),
            fields=data.get("fields", []),
            description=data.get("description", ""),
            created_by=data.get("created_by", "user"),
        )
        # Save branding if provided
        if data.get("branding"):
            _save_branding("form", result.get("form_id", ""), data["branding"])
        return jsonify(result)

    @bp.route("/api/forms/<form_id>", methods=["PATCH"])
    def api_update_form(form_id: str):
        data = request.get_json(force=True) or {}
        result = update_form(
            form_id,
            name=data.get("name"),
            fields=data.get("fields"),
            description=data.get("description"),
            status=data.get("status"),
        )
        if data.get("branding"):
            _save_branding("form", form_id, data["branding"])
        return jsonify(result)

    @bp.route("/api/forms/<form_id>", methods=["DELETE"])
    def api_delete_form(form_id: str):
        result = delete_form(form_id)
        return jsonify(result)

    @bp.route("/api/forms/<form_id>/submit", methods=["POST"])
    def api_submit_form(form_id: str):
        data = request.get_json(force=True) or {}
        result = submit_form(form_id, data.get("data", {}), submitted_by=data.get("submitted_by", "user"))
        return jsonify(result)

    @bp.route("/api/forms/<form_id>/export/<fmt>", methods=["POST"])
    def api_export_form(form_id: str, fmt: str):
        if fmt not in ("pptx", "pdf", "docx"):
            return jsonify({"error": "Unsupported format"}), 400
        try:
            from tools.workflow_canvas.export_engine import export_form
            conn = get_connection()
            try:
                data, filename = export_form(form_id, fmt, conn)
            finally:
                conn.close()
            mime_map = {
                "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "pdf": "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            from io import BytesIO
            return send_file(
                BytesIO(data),
                mimetype=mime_map[fmt],
                as_attachment=True,
                download_name=filename,
            )
        except Exception as exc:
            logger.error("wfc export_form error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/workflows/<workflow_id>/export/<fmt>", methods=["POST"])
    def api_export_workflow(workflow_id: str, fmt: str):
        if fmt not in ("pptx", "pdf", "docx"):
            return jsonify({"error": "Unsupported format"}), 400
        try:
            from tools.workflow_canvas.export_engine import export_workflow
            conn = get_connection()
            try:
                data, filename = export_workflow(workflow_id, fmt, conn)
            finally:
                conn.close()
            mime_map = {
                "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                "pdf": "application/pdf",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            }
            from io import BytesIO
            return send_file(
                BytesIO(data),
                mimetype=mime_map[fmt],
                as_attachment=True,
                download_name=filename,
            )
        except Exception as exc:
            logger.error("wfc export_workflow error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/branding/<entity_type>/<entity_id>")
    def api_get_branding(entity_type: str, entity_id: str):
        if entity_type not in ("form", "workflow"):
            return jsonify({"error": "Invalid entity_type"}), 400
        return jsonify(_get_branding(entity_type, entity_id))

    @bp.route("/api/branding/<entity_type>/<entity_id>", methods=["POST"])
    def api_save_branding(entity_type: str, entity_id: str):
        if entity_type not in ("form", "workflow"):
            return jsonify({"error": "Invalid entity_type"}), 400
        data = request.get_json(force=True) or {}
        _save_branding(entity_type, entity_id, data)
        return jsonify({"status": "ok"})

    @bp.route("/api/iqe-query", methods=["POST"])
    def api_iqe_query():
        question = (request.get_json(force=True) or {}).get("question", "")
        try:
            from tools.iqe.dispatcher import dispatch_query
            result = dispatch_query(question, canvas="wfc")
        except Exception as exc:
            result = {"answer": str(exc), "sources": []}
        return jsonify(result)

    return bp
