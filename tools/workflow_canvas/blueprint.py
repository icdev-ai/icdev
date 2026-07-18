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

  GET  /workflow-canvas/processify                       Process-Ify page
  POST /workflow-canvas/api/workflows/processify         Extract + AI-structure a process document
  POST /workflow-canvas/api/workflows/save-processify    Save processified workflow (creates forms + DAG)
  POST /workflow-canvas/api/forms/<id>/export/<fmt>      Download form as pptx|pdf|docx
  POST /workflow-canvas/api/workflows/<id>/export/<fmt>  Download workflow as pptx|pdf|docx
  GET  /workflow-canvas/api/branding/<type>/<id>        Get branding
  POST /workflow-canvas/api/branding/<type>/<id>        Save branding
  GET  /workflow-canvas/api/forms                       JSON list of forms
  GET  /workflow-canvas/api/workflows                   JSON list of workflows
  POST /workflow-canvas/api/iqe-query                   IQE natural-language query

  # pif-* Process-Ify Enhancement Suite
  GET  /workflow-canvas/api/workflows/<id>/bpmn              BPMN 2.0 XML (pif-bpmn-01)
  POST /workflow-canvas/api/workflows/diff                   Version diff (pif-diff-01)
  GET  /workflow-canvas/api/chains/<id>/roi                  ROI calc (pif-roi-01)
  GET  /workflow-canvas/api/chains/<id>/simulate             Timeline sim (pif-sim-01)
  GET  /workflow-canvas/api/templates                        Template list (pif-lib-01)
  POST /workflow-canvas/api/templates                        Save template (pif-lib-01)
  GET  /workflow-canvas/api/templates/<id>                   Get template (pif-lib-01)
  POST /workflow-canvas/api/workflows/synthesize             Multi-source synth (pif-synth-01)
  POST /workflow-canvas/api/chains/<cid>/phases/<pid>/handoff  Handoff brief (pif-handoff-01)
  POST /workflow-canvas/api/workflows/<id>/reverse-regen     Reverse regen (pif-rev-01)
  GET  /workflow-canvas/api/chains/<id>/dependencies         Dep graph (pif-dep-01)
  POST /workflow-canvas/api/chains/<id>/dependencies         Add dep (pif-dep-01)
  GET  /workflow-canvas/api/chains/<id>/export-yaml          Export YAML (pif-code-01)
  POST /workflow-canvas/api/chains/import-yaml               Import YAML (pif-code-01)
  GET  /workflow-canvas/api/workflows/<id>/conformance       Conformance (pif-conf-01)
  GET  /workflow-canvas/api/workflows/<id>/sla-status        SLA gate (pif-sla-01)
  GET  /workflow-canvas/api/workflows/<id>/steps/<n>/coach   AI coach (pif-coach-01)
  GET  /workflow-canvas/my-tasks                             My-tasks portal (pif-tasks-01)
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection, get_canvas_connection, sql_placeholder, is_pg
from tools.logging.icdev_logger import get_logger
from tools.workflow_canvas.constants import (
    EXPORT_FORMATS,
    INDUSTRY_CATEGORIES,
    DEFAULT_PRIMARY_COLOR,
    DEFAULT_SECONDARY_COLOR,
    MAX_UPLOAD_BYTES,
    MAX_EXTRACT_CHARS,
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

_INIT_DONE = False  # reset to re-run chain table migration (added handoff_brief column)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "wfc") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _stream_size(file_storage) -> int:
    """Return the byte size of an uploaded Werkzeug FileStorage without reading
    it into memory. Seeks to end, records position, then rewinds so the stream
    is still fully readable by downstream extractors."""
    try:
        stream = file_storage.stream
        pos = stream.tell()
        stream.seek(0, 2)  # SEEK_END
        size = stream.tell()
        stream.seek(pos)
        return int(size)
    except Exception:
        # If the stream is not seekable, fall back to content_length (may be 0).
        return int(getattr(file_storage, "content_length", 0) or 0)


def _cancel_prior_processify_tasks(workflow_name: str) -> int:
    """Cancel non-terminal processify tasks for *workflow_name*. Returns count."""
    from tools.db.storage import get_connection, sql_placeholder
    try:
        conn = get_connection()
        ph = sql_placeholder(conn)
        title_prefix = f"{workflow_name}:%"
        cur = conn.execute(
            f"""UPDATE kanban_tasks
                SET status = 'done', updated_at = {ph}
                WHERE dispatch_source = 'processify'
                  AND status NOT IN ('done', 'cancelled')
                  AND title LIKE {ph}""",
            (_now_iso(), title_prefix),
        )
        n = cur.rowcount if cur else 0
        conn.commit()
        conn.close()
        if n:
            logger.info("wfc: cancelled %d prior digitize task(s) for '%s'", n, workflow_name)
        return n
    except Exception as exc:
        logger.warning("wfc: could not cancel prior digitize tasks: %s", exc)
        return 0


_WFC_MIGRATION_PG = """
CREATE TABLE IF NOT EXISTS wfc_process_chains (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    industry TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','completed')),
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wfc_chain_phases (
    id TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL,
    phase_number INTEGER NOT NULL,
    name TEXT NOT NULL,
    team_name TEXT,
    team_role TEXT,
    workflow_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','in_progress','complete')),
    unlock_threshold INTEGER NOT NULL DEFAULT 100,
    handoff_checklist TEXT NOT NULL DEFAULT '[]',
    handoff_brief TEXT,
    style_fingerprint TEXT,
    regen_artifact_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chain_id, phase_number)
);
ALTER TABLE wfc_chain_phases ADD COLUMN IF NOT EXISTS handoff_brief TEXT;
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
CREATE INDEX IF NOT EXISTS idx_wfc_branding_entity ON wfc_branding(entity_type, entity_id);
"""

_WFC_MIGRATION_SQLITE = """
CREATE TABLE IF NOT EXISTS wfc_process_chains (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    industry TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','active','completed')),
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS wfc_chain_phases (
    id TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL,
    phase_number INTEGER NOT NULL,
    name TEXT NOT NULL,
    team_name TEXT,
    team_role TEXT,
    workflow_ids TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','active','in_progress','complete')),
    unlock_threshold INTEGER NOT NULL DEFAULT 100,
    handoff_checklist TEXT NOT NULL DEFAULT '[]',
    handoff_brief TEXT,
    style_fingerprint TEXT,
    regen_artifact_path TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(chain_id, phase_number)
);
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
CREATE INDEX IF NOT EXISTS idx_wfc_branding_entity ON wfc_branding(entity_type, entity_id);
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
        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
        migration_sql = _WFC_MIGRATION_PG if is_pg(conn) else _WFC_MIGRATION_SQLITE
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
        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
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
        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
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
            # Half-open range on the ISO-8601 submitted_at so an index on
            # submitted_at can be used (a leading-wildcard-free LIKE would work
            # too, but a range is unambiguously sargable). (cnr-wfc-04)
            now = datetime.now(timezone.utc)
            start = now.strftime("%Y-%m-%dT00:00:00")
            end = (now + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00")
            ph = _ph(conn)
            row = conn.execute(
                f"SELECT COUNT(*) FROM studio_form_submissions "
                f"WHERE submitted_at >= {ph} AND submitted_at < {ph}",
                (start, end),
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

    # ── Process-Ify page ──────────────────────────────────────────────────

    @bp.route("/processify")
    def processify():
        return render_template(
            "workflow_canvas/processify.html",
            industry_categories=INDUSTRY_CATEGORIES,
        )

    # ── AI-assisted form generation helpers ──────────────────────────────

    _VALID_FIELD_TYPES = {"text","textarea","number","date","select","multiselect","checkbox","email","file","richtext"}

    def _validate_fields(raw_fields: list) -> list:
        """Coerce and validate a list of raw field dicts from LLM output."""
        cleaned = []
        for i, f in enumerate(raw_fields):
            if not isinstance(f, dict):
                continue
            cleaned.append({
                "id": str(f.get("id") or f"f{i+1}"),
                "type": str(f.get("type", "text")) if f.get("type") in _VALID_FIELD_TYPES else "text",
                "label": str(f.get("label") or f"Field {i+1}"),
                "required": bool(f.get("required", False)),
                "placeholder": str(f.get("placeholder", "")) if f.get("placeholder") else "",
                "options": [str(o) for o in f["options"]] if f.get("options") and isinstance(f["options"], list) else None,
            })
        return cleaned

    def _generate_fields_from_text(text: str, mode: str = "describe") -> list:
        """Call LLM to extract form fields from text.

        mode='describe' — text is a natural-language description
        mode='import'   — text was extracted from an uploaded form document
        """
        import re
        import json as _json
        from tools.llm.router import LLMRouter, LLMRequest

        if mode == "import":
            prompt = f"""You are a form designer. The text below was extracted from an existing form template document.
Reconstruct it as a JSON list of form fields that faithfully represents every input, checkbox, dropdown, and text area in the original.

Each field must be a JSON object with these keys:
  - id: unique string like "f1", "f2" etc.
  - type: one of text, textarea, number, date, select, multiselect, checkbox, email, file, richtext
  - label: the field label or question text from the document
  - required: true if the field appears mandatory, otherwise false
  - options: array of strings (only for select/multiselect types — use values from the document)
  - placeholder: optional hint text

Return ONLY a valid JSON array. No markdown, no explanation.

Extracted form text:
{text[:6000]}"""
        else:
            prompt = f"""You are a form designer. Given the description below, generate a JSON list of form fields.
Each field must be a JSON object with these keys:
  - id: unique string like "f1", "f2" etc.
  - type: one of text, textarea, number, date, select, multiselect, checkbox, email, file, richtext
  - label: human-readable label
  - required: true or false
  - options: array of strings (only for select/multiselect types)
  - placeholder: optional hint text

Return ONLY a valid JSON array. No markdown, no explanation.

Form description: {text}"""

        router = LLMRouter()
        req = LLMRequest(function_id="form_generation", prompt=prompt)
        result = router.invoke("form_generation", req)
        if hasattr(result, "content"):
            raw = result.content or ""
        elif isinstance(result, dict):
            raw = result.get("content") or result.get("text") or result.get("response") or str(result)
        else:
            raw = str(result or "")

        match = re.search(r"\[[\s\S]*\]", raw)
        if not match:
            raise ValueError(f"LLM did not return a valid JSON array. Raw: {raw[:300]}")
        return _validate_fields(_json.loads(match.group()))

    # ── AI-assisted form generation ───────────────────────────────────────

    @bp.route("/api/forms/generate", methods=["POST"])
    def api_generate_form():
        """Generate form fields from a natural-language description using LLM."""
        data = request.get_json(force=True) or {}
        description = (data.get("description") or "").strip()
        industry = (data.get("industry") or "General").strip()
        if not description:
            return jsonify({"error": "description is required"}), 400
        try:
            text = f"Industry context: {industry}\n{description}"
            fields = _generate_fields_from_text(text, mode="describe")
            return jsonify({"status": "ok", "fields": fields, "count": len(fields)})
        except Exception as exc:
            logger.error("wfc generate_form LLM error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/forms/import", methods=["POST"])
    def api_import_form():
        """Extract text from an uploaded file and generate form fields via LLM."""
        import tempfile
        import pathlib

        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "No file uploaded"}), 400

        ext = pathlib.Path(file.filename).suffix.lower()
        allowed = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}
        if ext not in allowed:
            return jsonify({"error": f"Unsupported format '{ext}'. Use: PDF, DOCX, PNG, or JPG."}), 400

        # Save to temp so extractors can open by path
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                file.save(tmp)
                tmp_path = tmp.name

            # Try DIC extractors first (handles pdf/docx/image with OCR fallback)
            text = ""
            try:
                from tools.document_intelligence.extractors import extract_file
                extraction = extract_file(tmp_path)
                text = (extraction.text or "").strip()
            except Exception as e1:
                logger.warning("wfc import: extractors failed (%s), trying markitdown", e1)

            # Fallback: markitdown (high-fidelity DOCX/PPTX structure)
            if not text:
                try:
                    from tools.document_intelligence.converters.markitdown_adapter import convert as md_convert
                    extraction = md_convert(pathlib.Path(tmp_path))
                    text = (extraction.text or "").strip()
                except Exception as e2:
                    logger.warning("wfc import: markitdown failed (%s)", e2)

            if not text:
                return jsonify({"error": "Could not extract text from the uploaded file. The file may be image-only — try a clearer scan or a DOCX version."}), 422

            fields = _generate_fields_from_text(text, mode="import")
            source_name = pathlib.Path(file.filename).stem
            return jsonify({"status": "ok", "fields": fields, "count": len(fields), "source_file": source_name})

        except Exception as exc:
            logger.error("wfc import_form error: %s", exc)
            return jsonify({"error": str(exc)}), 500
        finally:
            if tmp_path:
                try:
                    pathlib.Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    # ── Process Digitization API ──────────────────────────────────────────

    _ACE_ROLE_HINTS = [
        ("review", "doc_reviewer"), ("document", "document_reviewer"),
        ("compliance", "compliance_manager"), ("security", "security_analyst"),
        ("test", "qa_manager"), ("quality", "qa_manager"),
        ("requirement", "requirements_engineer"), ("submit", "document_contributor"),
        ("approve", "compliance_manager"), ("verify", "adversarial_verifier"),
        ("deploy", "devops_engineer"), ("analyze", "data_analyst"),
        ("research", "researcher"), ("write", "technical_writer"),
        ("report", "technical_writer"),
    ]

    def _suggest_ace_role(title: str, description: str) -> str:
        text = (title + " " + description).lower()
        for keyword, role in _ACE_ROLE_HINTS:
            if keyword in text:
                return role
        return "doc_reviewer"

    @bp.route("/api/workflows/processify", methods=["POST"])
    def api_processify_workflow():
        """Upload a process document and AI-convert it into a structured workflow definition."""
        import re
        import tempfile
        import pathlib
        import json as _json

        file = request.files.get("file")
        manual_text = (request.form.get("text") or "").strip()
        if not file and not manual_text:
            return jsonify({"error": "Provide a file or paste text"}), 400

        industry = (request.form.get("industry") or "General").strip()
        source_name = "Process Document"
        text = ""

        if file and file.filename:
            ext = pathlib.Path(file.filename).suffix.lower()
            if ext not in {".pdf", ".docx", ".png", ".jpg", ".jpeg", ".txt"}:
                return jsonify({"error": f"Unsupported format '{ext}'. Use: PDF, DOCX, PNG, JPG, or TXT."}), 400
            # Reject oversize uploads before reading into memory (cnr-wfc-02).
            upload_size = _stream_size(file)
            if upload_size > MAX_UPLOAD_BYTES:
                return jsonify({
                    "error": f"File too large ({upload_size} bytes). Limit is {MAX_UPLOAD_BYTES} bytes."
                }), 413
            source_name = pathlib.Path(file.filename).stem
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
                    file.save(tmp)
                    tmp_path = tmp.name
                try:
                    from tools.document_intelligence.extractors import extract_file
                    extraction = extract_file(tmp_path)
                    text = (extraction.text or "").strip()
                except Exception as e1:
                    logger.warning("digitize: extractors failed (%s)", e1)
                if not text:
                    try:
                        from tools.document_intelligence.converters.markitdown_adapter import convert as md_convert
                        extraction = md_convert(pathlib.Path(tmp_path))
                        text = (extraction.text or "").strip()
                    except Exception as e2:
                        logger.warning("digitize: markitdown failed (%s)", e2)
            finally:
                if tmp_path:
                    try:
                        pathlib.Path(tmp_path).unlink(missing_ok=True)
                    except Exception:
                        pass
        else:
            text = manual_text

        if not text:
            return jsonify({"error": "Could not extract text. Try a clearer document or paste text directly."}), 422

        # Cap extracted text kept in memory / passed downstream (cnr-wfc-02).
        if len(text) > MAX_EXTRACT_CHARS:
            logger.info("digitize: truncating extracted text %d -> %d chars", len(text), MAX_EXTRACT_CHARS)
            text = text[:MAX_EXTRACT_CHARS]

        prompt = f"""You are a business process analyst. Analyze the following process document and convert it into a structured digital workflow.

Industry: {industry}
Document: {source_name}

Return a JSON object with this exact structure:
{{
  "workflow_name": "concise workflow name",
  "workflow_description": "one sentence describing the process",
  "industry": "{industry}",
  "estimated_duration_days": 30,
  "steps": [
    {{
      "id": "step-1",
      "title": "Step title",
      "phase": "Phase name (Initiation|Review|Approval|Completion etc.)",
      "description": "What happens in this step",
      "checklist_items": ["Item from document 1", "Item from document 2"],
      "form_fields": [
        {{"label": "Field label", "type": "text", "required": true, "options": null}}
      ],
      "assignee_role": "Who performs this step",
      "reviewer_role": "Who reviews (or empty string)",
      "approver_role": "Who approves (or empty string)",
      "sla_days": 5,
      "dependencies": [],
      "ace_role": "snake_case_ai_coworker_role_specific_to_this_step"
    }}
  ]
}}

Rules:
- Extract ALL steps/tasks/requirements found in the document
- checklist_items should be concrete, verbatim items from the document
- form_fields: REQUIRED — provide 2-5 meaningful data fields that the assignee must fill in to complete this step. Never leave form_fields empty.
- assignee_role/reviewer_role/approver_role are human job titles (e.g. "Provider", "Network Director")
- dependencies lists step ids that must complete first (first step has empty [])
- Aim for 5-15 steps covering the full process
- field type must be one of: text textarea number date select multiselect checkbox email file
- ace_role: suggest a specific, domain-appropriate AI coworker role in snake_case (e.g. credentialing_specialist, dhcs_compliance_auditor, loan_underwriter, permit_reviewer, safety_inspector) — be specific to the industry, not generic
- Return ONLY valid JSON. No markdown fences, no explanation.

Process Document:
{text[:8000]}"""

        try:
            # Route through the governed Cortex facade (input injection screen,
            # output redaction, provenance, budget). The uploaded document text
            # is UNTRUSTED, so trusted_content stays False (the default) — the
            # injection screen runs. This mirrors docgen's cortex_api.complete
            # usage but without the trusted_content=True flag. (cnr-wfc-02)
            from tools.cortex import api as cortex_api
            from tools.cortex.schemas import CortexContext
            cx = cortex_api.complete(
                prompt,
                function="process_digitization",
                ctx=CortexContext(domain="workflow", agent_id="wfc-processify",
                                  trusted_content=False),
                max_tokens=4096,
                temperature=0.2,
            )
            raw = (cx.text or "") if cx is not None else ""

            match = re.search(r'\{[\s\S]*\}', raw)
            if not match:
                raise ValueError(f"LLM did not return valid JSON. Raw: {raw[:300]}")
            workflow_def = _json.loads(match.group())

            steps = workflow_def.get("steps", [])
            for i, step in enumerate(steps):
                if "id" not in step:
                    step["id"] = f"step-{i + 1}"
                # Use LLM-suggested role first; keyword-hint map is the fallback
                if not step.get("ace_role"):
                    step["ace_role"] = _suggest_ace_role(
                        step.get("title", ""), step.get("description", "")
                    )
                step["form_fields"] = _validate_fields(step.get("form_fields") or [])
                step.setdefault("checklist_items", [])
                step.setdefault("dependencies", [])
            workflow_def["steps"] = steps
            workflow_def["source_name"] = source_name
            return jsonify({"status": "ok", "workflow": workflow_def, "source_doc_text": text})
        except Exception as exc:
            logger.error("wfc processify error: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/workflows/save-processify", methods=["POST"])
    def api_save_processify_workflow():
        """Persist a Process-Ify workflow: creates per-step WFC forms + workflow record."""
        try:
            return _do_save_processify()
        except Exception as exc:
            logger.error("wfc save-processify error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    def _do_save_processify():
        import yaml as _yaml
        data = request.get_json(force=True) or {}
        workflow_def = data.get("workflow") or {}
        if not workflow_def:
            return jsonify({"error": "workflow data required"}), 400

        steps = workflow_def.get("steps", [])
        workflow_name = (workflow_def.get("workflow_name") or "Digitized Workflow").strip()
        workflow_desc = (workflow_def.get("workflow_description") or "").strip()
        industry = (workflow_def.get("industry") or "General").strip()

        _DEFAULT_STEP_FIELDS = [
            {"label": "Submission Notes", "type": "textarea", "required": False},
            {"label": "Completed By", "type": "text", "required": True},
            {"label": "Completion Date", "type": "date", "required": True},
        ]

        # Create a WFC form for each step (always — use defaults if LLM omitted fields)
        step_form_ids: dict[str, str] = {}
        for step in steps:
            raw_fields = _validate_fields(step.get("form_fields") or [])
            if not raw_fields:
                raw_fields = list(_DEFAULT_STEP_FIELDS)
            form_result = create_form(
                name=f"{workflow_name} — {step.get('title') or step['id']}",
                fields=raw_fields,
                description=step.get("description", ""),
                created_by="processify",
                status="draft",
            )
            if form_result.get("form_id"):
                step_form_ids[step["id"]] = form_result["form_id"]

        # Build YAML DAG
        yaml_steps = []
        for step in steps:
            yaml_steps.append({
                "id": step["id"],
                "title": step.get("title", ""),
                "phase": step.get("phase", ""),
                "description": step.get("description", ""),
                "assignee_role": step.get("assignee_role", ""),
                "reviewer_role": step.get("reviewer_role", ""),
                "approver_role": step.get("approver_role", ""),
                "sla_days": step.get("sla_days"),
                "ace_role": step.get("ace_role", ""),
                "checklist": step.get("checklist_items", []),
                "form_id": step_form_ids.get(step["id"]),
                "dependencies": step.get("dependencies", []),
            })

        workflow_yaml = _yaml.dump({
            "name": workflow_name,
            "description": workflow_desc,
            "industry": industry,
            "estimated_duration_days": workflow_def.get("estimated_duration_days"),
            "source": workflow_def.get("source_name", ""),
            "steps": yaml_steps,
        }, default_flow_style=False, allow_unicode=True)

        source_doc_text = (data.get("source_doc_text") or "")[:50000]  # cap at 50k chars

        workflow_id = _new_id("wfl")
        conn = get_connection()
        try:
            ph = _ph(conn)
            conn.execute(
                f"""INSERT INTO studio_workflows
                    (workflow_id, name, description, template_yaml, category,
                     created_by, created_at, updated_at, version, shared,
                     source_doc_text)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                (workflow_id, workflow_name, workflow_desc, workflow_yaml,
                 industry, "processify", _now_iso(), _now_iso(), 1, 0,
                 source_doc_text or None),
            )
            conn.commit()
        finally:
            conn.close()

        # NOTE (cnr-wfc-03): the per-step form_id linkage is persisted in the
        # workflow's template_yaml above (yaml_steps[].form_id). The former
        # wfc_workflow_form_nodes write was dead — nothing ever read those rows
        # and no executor enforced the advertised form-intake HITL gate — so it
        # was removed along with the form_node.py module.

        # Optionally seed kanban tasks
        kanban_ids: list = []
        conflict_mode = data.get("conflict_mode", "replace")  # replace | append
        if data.get("create_kanban_tasks"):
            try:
                from tools.kanban.task_factory import create_tasks
                wfl_prefix = workflow_id[:8]

                # "replace": cancel prior process_digitizer tasks for the same workflow name
                if conflict_mode == "replace":
                    _cancel_prior_processify_tasks(workflow_name)

                task_specs = []
                for step in steps:
                    deps = step.get("dependencies", [])
                    task_specs.append({
                        "id": f"processify-{wfl_prefix}-{step['id']}",
                        "title": f"{workflow_name}: {step.get('title') or step['id']}",
                        "description": step.get("description", ""),
                        "task_type": "build",
                        "priority": "medium",
                        "status": "backlog",
                        "depends_on_task_id": f"processify-{wfl_prefix}-{deps[0]}" if deps else None,
                        "dispatch_source": "processify",
                    })
                kanban_ids = create_tasks(task_specs)
            except Exception as exc:
                logger.warning("wfc: kanban seeding failed: %s", exc)

        logger.info(
            "wfc: saved processify workflow %s — %d steps, %d forms, %d kanban tasks",
            workflow_id, len(steps), len(step_form_ids), len(kanban_ids),
        )
        return jsonify({
            "status": "ok",
            "workflow_id": workflow_id,
            "forms_created": len(step_form_ids),
            "kanban_tasks": len(kanban_ids),
        })

    @bp.route("/api/workflows/<workflow_id>/task-status")
    def api_workflow_task_status(workflow_id):
        """Return kanban task statuses for a workflow (matched by processify ID prefix)."""
        from tools.db.storage import get_connection, sql_placeholder
        conn = get_connection()
        ph = sql_placeholder(conn)
        prefix = f"processify-{workflow_id[:8]}-%"
        rows = conn.execute(
            f"""SELECT id, title, status, updated_at
                FROM kanban_tasks
                WHERE id LIKE {ph}
                ORDER BY id""",
            (prefix,),
        ).fetchall()
        conn.close()
        tasks = [{"id": r[0], "title": r[1], "status": r[2], "updated_at": str(r[3])} for r in rows]
        total = len(tasks)
        done = sum(1 for t in tasks if t["status"] in ("done", "failed"))
        return jsonify({"tasks": tasks, "total": total, "done": done,
                        "complete": total > 0 and done == total})

    @bp.route("/api/workflows/cleanup-processify", methods=["POST"])
    def api_cleanup_processify_tasks():
        """Cancel processify kanban tasks, optionally filtered by workflow_name.

        Body (all optional):
          workflow_name  — only cancel tasks for this workflow (title LIKE "name:%")
          status_filter  — list of statuses to cancel; default ["backlog", "in_progress"]
        Returns: {"cancelled": N}
        """
        data = request.get_json(force=True) or {}
        wf_name = (data.get("workflow_name") or "").strip()
        status_filter = data.get("status_filter") or ["backlog", "in_progress"]

        from tools.db.storage import get_connection, sql_placeholder
        try:
            conn = get_connection()
            ph = sql_placeholder(conn)
            placeholders = ",".join([ph] * len(status_filter))
            base_params: list = [_now_iso()] + list(status_filter)
            if wf_name:
                cur = conn.execute(
                    f"""UPDATE kanban_tasks
                        SET status = 'done', updated_at = {ph}
                        WHERE dispatch_source = 'processify'
                          AND status IN ({placeholders})
                          AND title LIKE {ph}""",
                    base_params + [f"{wf_name}:%"],
                )
            else:
                cur = conn.execute(
                    f"""UPDATE kanban_tasks
                        SET status = 'done', updated_at = {ph}
                        WHERE dispatch_source = 'processify'
                          AND status IN ({placeholders})""",
                    base_params,
                )
            n = cur.rowcount if cur else 0
            conn.commit()
            conn.close()
            logger.info("wfc: cleanup-processify cancelled %d task(s) (filter=%r)", n, wf_name or "*")
            return jsonify({"cancelled": n})
        except Exception as exc:
            logger.error("wfc: cleanup-processify error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/workflows/ensure-roles", methods=["POST"])
    def api_ensure_roles():
        """Ensure ACE coworker roles exist — auto-generates any missing ones via profile_generator."""
        data = request.get_json(force=True) or {}
        role_requests = data.get("roles", [])  # [{role_id, description}]
        if not role_requests:
            return jsonify({"existing": [], "generated": [], "errors": []})

        try:
            from icdev.tools.ace.profile_generator import list_profiles, generate_profile
            existing_ids = {p["role_id"] for p in list_profiles()}
        except Exception as exc:
            logger.warning("ensure_roles: could not load profiles: %s", exc)
            return jsonify({"existing": [], "generated": [], "errors": [str(exc)]})

        existing, generated, errors = [], [], []
        for role_req in role_requests:
            role_id = (role_req.get("role_id") or "").strip()
            description = (role_req.get("description") or "").strip()
            if not role_id:
                continue
            if role_id in existing_ids:
                existing.append(role_id)
                continue
            try:
                display_name = role_id.replace("_", " ").title()
                role_desc = description or f"AI co-worker for {role_id} tasks"
                minimal_spec = {
                    "role_id": role_id,
                    "display_name": display_name,
                    "description": role_desc,
                    "version": "1.0",
                    "source": "generated",
                    "trust_tier": "yellow",
                    "default_count": 1,
                    "max_instances": 2,
                    "steps": ["analyze", "execute", "report"],
                    "llm_function": "task_decomposition",
                    "tool_permissions": ["Read", "Grep", "Glob"],
                    "genesis_reflex": "maintain",
                    "communication": {
                        "protocol": "a2a",
                        "listen_topics": [],
                        "emit_topics": ["task.completed"],
                    },
                    "personality": {"domain": display_name, "supported_verticals": ["enterprise"]},
                }
                result = generate_profile(
                    name=display_name,
                    description=role_desc,
                    spec_override=minimal_spec,
                )
                generated.append(result["role_id"])
                logger.info("ensure_roles: generated profile for %s", role_id)
            except Exception as exc:
                logger.warning("ensure_roles: failed to generate %s: %s", role_id, exc)
                errors.append({"role_id": role_id, "error": str(exc)})

        return jsonify({"existing": existing, "generated": generated, "errors": errors})

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
            status=data.get("status", "draft"),
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
        if result.get("status") == "error":
            # Missing form -> 404; schema validation failure -> 400.
            code = 404 if result.get("error") == "Form not found" else 400
            return jsonify(result), code
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

    # ── Doc Regen routes ──────────────────────────────────────────────────────

    @bp.route("/api/workflows/<workflow_id>/regen-doc", methods=["POST"])
    def api_regen_workflow_doc(workflow_id: str):
        """Start background doc regeneration for a single processified workflow."""
        data = request.get_json(force=True) or {}
        fmt = data.get("format", "all")
        try:
            from tools.workflow_canvas.doc_regenerator import start_regen_workflow
            job_id = start_regen_workflow(workflow_id, output_format=fmt)
            return jsonify({"status": "ok", "job_id": job_id})
        except Exception as exc:
            logger.error("regen-doc error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/regen-jobs/<job_id>/status")
    def api_regen_job_status(job_id: str):
        """Poll regen job status and retrieve download info when done."""
        from tools.workflow_canvas.doc_regenerator import get_job_status
        job = get_job_status(job_id)
        if not job:
            return jsonify({"error": "job not found"}), 404
        return jsonify(job)

    @bp.route("/api/regen-jobs/<job_id>/download/<filename>")
    def api_regen_download(job_id: str, filename: str):
        """Download a regenerated artifact file.

        Path-traversal hardened: the requested filename is resolved under the
        job's artifact_dir and the resolved path is asserted to stay within it.
        Flask's <filename> converter permits backslashes and absolute paths, so
        without this check a request like '..%5C..%5C<secret>' (Windows) or an
        absolute path would escape artifact_dir. Any violation → 404.
        """
        from flask import send_file
        from tools.workflow_canvas.doc_regenerator import get_job_status
        job = get_job_status(job_id)
        if not job or job.get("status") != "done":
            return jsonify({"error": "job not ready"}), 404
        artifact_dir = job.get("artifact_dir", "")
        if not artifact_dir:
            return jsonify({"error": "file not found"}), 404
        base = Path(artifact_dir).resolve()
        candidate = (base / filename).resolve()
        # Containment check: candidate must be base itself or live under base.
        if candidate != base and base not in candidate.parents:
            logger.warning("wfc: rejected traversal download filename=%r job=%s", filename, job_id)
            return jsonify({"error": "file not found"}), 404
        if not candidate.is_file():
            return jsonify({"error": "file not found"}), 404
        return send_file(str(candidate), as_attachment=True, download_name=candidate.name)

    # ── Process Chain CRUD ────────────────────────────────────────────────────

    @bp.route("/api/workflows/chains", methods=["POST"])
    def api_create_chain():
        """Create a new process chain."""
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        chain_id = _new_id("chn")
        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
        try:
            ph = _ph(conn)
            conn.execute(
                f"""INSERT INTO wfc_process_chains
                    (id, name, description, industry, status, created_by, created_at, updated_at)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                (chain_id, name, data.get("description", ""), data.get("industry", "General"),
                 "draft", "processify", _now_iso(), _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"status": "ok", "chain_id": chain_id, "name": name})

    @bp.route("/api/workflows/chains", methods=["GET"])
    def api_list_chains():
        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
        try:
            rows = conn.execute(
                "SELECT id, name, description, industry, status, created_at FROM wfc_process_chains ORDER BY created_at DESC"
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"chains": [dict(r) for r in rows]})

    @bp.route("/api/workflows/chains/<chain_id>", methods=["GET"])
    def api_get_chain(chain_id: str):
        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
        try:
            ph = _ph(conn)
            chain = conn.execute(
                f"SELECT * FROM wfc_process_chains WHERE id = {ph}", (chain_id,)
            ).fetchone()
            if not chain:
                return jsonify({"error": "chain not found"}), 404
            phases = conn.execute(
                f"SELECT * FROM wfc_chain_phases WHERE chain_id = {ph} ORDER BY phase_number",
                (chain_id,),
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"chain": dict(chain), "phases": [dict(p) for p in phases]})

    @bp.route("/api/workflows/chains/<chain_id>/phases", methods=["POST"])
    def api_add_chain_phase(chain_id: str):
        """Add a phase to a process chain."""
        data = request.get_json(force=True) or {}
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
        try:
            ph = _ph(conn)
            # Auto-number: find max phase_number for this chain
            row = conn.execute(
                f"SELECT MAX(phase_number) FROM wfc_chain_phases WHERE chain_id = {ph}", (chain_id,)
            ).fetchone()
            next_num = (row[0] or 0) + 1
            phase_id = _new_id("phs")
            conn.execute(
                f"""INSERT INTO wfc_chain_phases
                    (id, chain_id, phase_number, name, team_name, team_role,
                     workflow_ids, status, unlock_threshold, handoff_checklist, created_at, updated_at)
                    VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                (phase_id, chain_id, next_num, name,
                 data.get("team_name", ""), data.get("team_role", ""),
                 "[]", "pending", data.get("unlock_threshold", 100), "[]",
                 _now_iso(), _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"status": "ok", "phase_id": phase_id, "phase_number": next_num})

    @bp.route("/api/workflows/chains/<chain_id>/phases/<phase_id>/processify", methods=["POST"])
    def api_processify_chain_phase(chain_id: str, phase_id: str):
        """Processify a document for a specific chain phase and link the workflow."""
        data = request.get_json(force=True) or {}
        workflow_id = (data.get("workflow_id") or "").strip()
        if not workflow_id:
            return jsonify({"error": "workflow_id required — run processify first and pass the result"}), 400

        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
        try:
            ph = _ph(conn)
            row = conn.execute(
                f"SELECT workflow_ids FROM wfc_chain_phases WHERE id = {ph} AND chain_id = {ph}",
                (phase_id, chain_id),
            ).fetchone()
            if not row:
                return jsonify({"error": "phase not found"}), 404
            import json as _json
            wf_ids = _json.loads(row[0] or "[]")
            if workflow_id not in wf_ids:
                wf_ids.append(workflow_id)
            conn.execute(
                f"UPDATE wfc_chain_phases SET workflow_ids = {ph}, status = 'in_progress', "
                f"updated_at = {ph} WHERE id = {ph}",
                (_json.dumps(wf_ids), _now_iso(), phase_id),
            )
            # Activate chain if still draft
            conn.execute(
                f"UPDATE wfc_process_chains SET status = 'active', updated_at = {ph} "
                f"WHERE id = {ph} AND status = 'draft'",
                (_now_iso(), chain_id),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"status": "ok", "phase_id": phase_id, "workflow_ids": wf_ids})

    @bp.route("/api/workflows/chains/<chain_id>/progress", methods=["GET"])
    def api_chain_progress(chain_id: str):
        """Return progress across all phases of a chain."""
        conn = get_canvas_connection("ICDEV_WFC_ENABLED")
        try:
            ph = _ph(conn)
            chain = conn.execute(
                f"SELECT * FROM wfc_process_chains WHERE id = {ph}", (chain_id,)
            ).fetchone()
            if not chain:
                return jsonify({"error": "chain not found"}), 404
            phases = [
                dict(r) for r in conn.execute(
                    f"SELECT * FROM wfc_chain_phases WHERE chain_id = {ph} ORDER BY phase_number",
                    (chain_id,),
                ).fetchall()
            ]
        finally:
            conn.close()

        import json as _json
        phase_progress = []
        for p in phases:
            wf_ids = _json.loads(p.get("workflow_ids") or "[]")
            # Check kanban task completion for these workflows
            total_tasks = 0
            done_tasks = 0
            for wf_id in wf_ids:
                prefix = f"processify-{wf_id[:8]}-%"
                conn2 = get_connection()
                try:
                    t_ph = _ph(conn2)
                    t_rows = conn2.execute(
                        f"SELECT status FROM kanban_tasks WHERE id LIKE {t_ph}", (prefix,)
                    ).fetchall()
                finally:
                    conn2.close()
                total_tasks += len(t_rows)
                done_tasks += sum(1 for r in t_rows if r[0] in ("done", "failed"))
            pct = round(done_tasks / total_tasks * 100) if total_tasks > 0 else 0
            phase_progress.append({
                "phase_id": p["id"],
                "phase_number": p["phase_number"],
                "name": p["name"],
                "team_name": p.get("team_name", ""),
                "status": p["status"],
                "workflow_count": len(wf_ids),
                "total_tasks": total_tasks,
                "done_tasks": done_tasks,
                "completion_pct": pct,
                "can_regen": pct > 0,
            })

        total_phases = len(phases)
        complete_phases = sum(1 for p in phase_progress if p["completion_pct"] == 100)
        return jsonify({
            "chain": dict(chain),
            "phases": phase_progress,
            "total_phases": total_phases,
            "complete_phases": complete_phases,
            "overall_pct": round(complete_phases / total_phases * 100) if total_phases else 0,
        })

    @bp.route("/api/chains/<chain_id>/regen-doc", methods=["POST"])
    def api_regen_chain_doc(chain_id: str):
        """Start background doc regen for a chain (full e2e) or a single phase."""
        data = request.get_json(force=True) or {}
        phase_id = data.get("phase_id")  # None → full chain
        fmt = data.get("format", "all")
        try:
            from tools.workflow_canvas.doc_regenerator import start_regen_chain
            job_id = start_regen_chain(chain_id, phase_id=phase_id, output_format=fmt)
            return jsonify({"status": "ok", "job_id": job_id})
        except Exception as exc:
            logger.error("regen-chain-doc error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-bpmn-01: BPMN 2.0 Export ──────────────────────────────────────────
    @bp.route("/api/workflows/<workflow_id>/bpmn")
    def api_workflow_bpmn(workflow_id: str):
        import yaml
        try:
            conn = get_connection()
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT * FROM studio_workflows WHERE workflow_id = {ph}", (workflow_id,)
            ).fetchone()
            conn.close()
            if not row:
                return jsonify({"error": "not found"}), 404
            row_dict = dict(row) if hasattr(row, "keys") else {}
            wf = yaml.safe_load(row_dict.get("template_yaml") or "{}") or {}
            from tools.workflow_canvas.bpmn_export import workflow_to_bpmn
            xml_str = workflow_to_bpmn(wf)
            from flask import Response
            return Response(
                xml_str, mimetype="application/xml",
                headers={"Content-Disposition": f'attachment; filename="workflow_{workflow_id[:8]}.bpmn"'},
            )
        except Exception as exc:
            logger.error("bpmn export error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-diff-01: Process Version Diff ─────────────────────────────────────
    @bp.route("/api/workflows/diff", methods=["POST"])
    def api_workflow_diff():
        data = request.get_json(force=True) or {}
        id_a = data.get("workflow_id_a")
        id_b = data.get("workflow_id_b")
        if not id_a or not id_b:
            return jsonify({"error": "workflow_id_a and workflow_id_b required"}), 400
        try:
            from tools.workflow_canvas.workflow_diff import diff_workflow_ids
            result = diff_workflow_ids(id_a, id_b)
            return jsonify(result)
        except Exception as exc:
            logger.error("workflow diff error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-roi-01: Process ROI Calculator ────────────────────────────────────
    @bp.route("/api/chains/<chain_id>/roi")
    def api_chain_roi(chain_id: str):
        try:
            from tools.workflow_canvas.roi_calculator import chain_roi_from_db
            result = chain_roi_from_db(chain_id)
            return jsonify(result)
        except Exception as exc:
            logger.error("chain roi error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-sim-01: Live Chain Simulation ────────────────────────────────────
    @bp.route("/api/chains/<chain_id>/simulate")
    def api_chain_simulate(chain_id: str):
        try:
            from tools.workflow_canvas.chain_simulator import simulate_chain_from_db
            result = simulate_chain_from_db(chain_id)
            return jsonify(result)
        except Exception as exc:
            logger.error("chain simulate error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-lib-01: Process Template Library ─────────────────────────────────
    @bp.route("/api/templates")
    def api_templates_list():
        data = request.args
        try:
            from tools.workflow_canvas.template_library import search_templates, ensure_table
            cc = get_canvas_connection("ICDEV_WFC_ENABLED")
            ensure_table(cc)
            rows = search_templates(
                cc,
                query=data.get("q", ""),
                industry=data.get("industry", ""),
                doc_type=data.get("doc_type", ""),
            )
            cc.close()
            return jsonify({"templates": rows})
        except Exception as exc:
            logger.error("templates list error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/templates", methods=["POST"])
    def api_templates_save():
        data = request.get_json(force=True) or {}
        workflow_id = data.get("workflow_id")
        name = data.get("name") or ""
        if not workflow_id or not name:
            return jsonify({"error": "workflow_id and name required"}), 400
        try:
            import yaml
            conn = get_connection()
            ph = sql_placeholder(conn)
            row = conn.execute(
                f"SELECT * FROM studio_workflows WHERE workflow_id = {ph}", (workflow_id,)
            ).fetchone()
            conn.close()
            if not row:
                return jsonify({"error": "workflow not found"}), 404
            row_dict = dict(row) if hasattr(row, "keys") else {}
            wf = yaml.safe_load(row_dict.get("template_yaml") or "{}") or {}
            from tools.workflow_canvas.template_library import save_template, ensure_table
            cc = get_canvas_connection("ICDEV_WFC_ENABLED")
            ensure_table(cc)
            tid = save_template(
                cc, name=name, workflow=wf,
                industry=data.get("industry", ""),
                doc_type=data.get("doc_type", ""),
            )
            cc.close()
            return jsonify({"status": "ok", "template_id": tid})
        except Exception as exc:
            logger.error("save template error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/templates/<template_id>")
    def api_template_get(template_id: str):
        try:
            from tools.workflow_canvas.template_library import ensure_table, increment_usage
            cc = get_canvas_connection("ICDEV_WFC_ENABLED")
            ensure_table(cc)
            ph = sql_placeholder(cc)
            row = cc.execute(
                f"SELECT * FROM wfc_template_library WHERE id = {ph}", (template_id,)
            ).fetchone()
            if not row:
                cc.close()
                return jsonify({"error": "not found"}), 404
            increment_usage(cc, template_id)
            cc.close()
            row_dict = dict(row) if hasattr(row, "keys") else {}
            return jsonify(row_dict)
        except Exception as exc:
            logger.error("get template error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-synth-01: Multi-Source Synthesis ──────────────────────────────────
    @bp.route("/api/workflows/synthesize", methods=["POST"])
    def api_workflows_synthesize():
        data = request.get_json(force=True) or {}
        texts = data.get("texts") or []
        if not texts or len(texts) < 2:
            return jsonify({"error": "provide at least 2 texts to synthesize"}), 400
        try:
            from tools.workflow_canvas.multi_source_synth import synthesize_workflows
            llm_router = None
            try:
                from tools.llm.router import LLMRouter
                llm_router = LLMRouter()
            except Exception:
                pass
            result = synthesize_workflows(texts, llm_router=llm_router)
            return jsonify(result)
        except Exception as exc:
            logger.error("synthesize error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-handoff-01: Handoff Ceremony Wizard ───────────────────────────────
    @bp.route("/api/chains/<chain_id>/phases/<phase_id>/handoff", methods=["POST"])
    def api_phase_handoff(chain_id: str, phase_id: str):
        try:
            cc = get_canvas_connection("ICDEV_WFC_ENABLED")
            cc_ph = sql_placeholder(cc)
            chain_row = cc.execute(
                f"SELECT * FROM wfc_process_chains WHERE id = {cc_ph}", (chain_id,)
            ).fetchone()
            phase_row = cc.execute(
                f"SELECT * FROM wfc_chain_phases WHERE id = {cc_ph}", (phase_id,)
            ).fetchone()
            # Look for next phase
            phase_num = (dict(phase_row) if hasattr(phase_row, "keys") else {}).get("phase_number") or 0
            next_phase_row = cc.execute(
                f"SELECT * FROM wfc_chain_phases WHERE chain_id = {cc_ph} AND phase_number = {cc_ph}",
                (chain_id, phase_num + 1),
            ).fetchone()
            cc.close()
            if not chain_row or not phase_row:
                return jsonify({"error": "chain or phase not found"}), 404

            conn = get_connection()
            ph = sql_placeholder(conn)
            tasks = conn.execute(
                f"SELECT * FROM kanban_tasks WHERE status = {ph} AND description LIKE {ph}",
                ("done", f"%{phase_id}%"),
            ).fetchall()
            conn.close()

            from tools.workflow_canvas.handoff_wizard import generate_handoff_brief, save_handoff_brief
            llm_router = None
            try:
                from tools.llm.router import LLMRouter
                llm_router = LLMRouter()
            except Exception:
                pass
            brief = generate_handoff_brief(
                chain=dict(chain_row) if hasattr(chain_row, "keys") else {},
                phase=dict(phase_row) if hasattr(phase_row, "keys") else {},
                completed_tasks=[dict(t) if hasattr(t, "keys") else {} for t in tasks],
                next_phase=dict(next_phase_row) if (next_phase_row and hasattr(next_phase_row, "keys")) else None,
                llm_router=llm_router,
            )
            save_handoff_brief(None, phase_id, brief)
            return jsonify({"status": "ok", "brief": brief})
        except Exception as exc:
            logger.error("handoff brief error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-rev-01: Reverse Process-Ify ───────────────────────────────────────
    @bp.route("/api/workflows/<workflow_id>/reverse-regen", methods=["POST"])
    def api_workflow_reverse_regen(workflow_id: str):
        try:
            from tools.workflow_canvas.reverse_regen import reverse_regen_from_db
            doc = reverse_regen_from_db(workflow_id)
            return jsonify({"status": "ok", "document": doc})
        except Exception as exc:
            logger.error("reverse regen error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-dep-01: Cross-Chain Dependency Graph ───────────────────────────────
    @bp.route("/api/chains/<chain_id>/dependencies")
    def api_chain_deps_get(chain_id: str):
        try:
            from tools.workflow_canvas.chain_deps import get_dependencies, check_blockers, ensure_table
            cc = get_canvas_connection("ICDEV_WFC_ENABLED")
            ensure_table(cc)
            deps = get_dependencies(cc, chain_id)
            blockers = check_blockers(cc, chain_id)
            cc.close()
            return jsonify({"dependencies": deps, "blockers": blockers})
        except Exception as exc:
            logger.error("chain deps get error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/chains/<chain_id>/dependencies", methods=["POST"])
    def api_chain_deps_add(chain_id: str):
        data = request.get_json(force=True) or {}
        upstream_chain = data.get("depends_on_chain_id")
        if not upstream_chain:
            return jsonify({"error": "depends_on_chain_id required"}), 400
        try:
            from tools.workflow_canvas.chain_deps import add_dependency, ensure_table
            cc = get_canvas_connection("ICDEV_WFC_ENABLED")
            ensure_table(cc)
            dep_id = add_dependency(
                cc, chain_id=chain_id,
                phase_number=data.get("phase_number"),
                depends_on_chain_id=upstream_chain,
                depends_on_phase=data.get("depends_on_phase"),
                note=data.get("note", ""),
            )
            cc.close()
            return jsonify({"status": "ok", "dep_id": dep_id})
        except Exception as exc:
            logger.error("chain deps add error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-code-01: Process as Code (YAML export/import) ─────────────────────
    @bp.route("/api/chains/<chain_id>/export-yaml")
    def api_chain_export_yaml(chain_id: str):
        try:
            from tools.workflow_canvas.process_code import export_chain_yaml_from_db
            yaml_str = export_chain_yaml_from_db(chain_id)
            from flask import Response
            return Response(
                yaml_str, mimetype="text/yaml",
                headers={"Content-Disposition": f'attachment; filename="chain_{chain_id[:8]}.yaml"'},
            )
        except Exception as exc:
            logger.error("export yaml error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/chains/import-yaml", methods=["POST"])
    def api_chain_import_yaml():
        try:
            raw = request.get_data(as_text=True)
            if not raw:
                data = request.get_json(force=True) or {}
                raw = data.get("yaml") or ""
            if not raw:
                return jsonify({"error": "yaml body required"}), 400
            from tools.workflow_canvas.process_code import import_chain_yaml
            conn = get_connection()
            chain_id = import_chain_yaml(raw, conn)
            return jsonify({"status": "ok", "chain_id": chain_id})
        except Exception as exc:
            logger.error("import yaml error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-conf-01: Process Conformance Checking ─────────────────────────────
    @bp.route("/api/workflows/<workflow_id>/conformance")
    def api_workflow_conformance(workflow_id: str):
        try:
            from tools.workflow_canvas.conformance_checker import check_conformance_from_db
            result = check_conformance_from_db(workflow_id)
            return jsonify(result)
        except Exception as exc:
            logger.error("conformance error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-sla-01: SLA Gate Enforcement ─────────────────────────────────────
    @bp.route("/api/workflows/<workflow_id>/sla-status")
    def api_workflow_sla(workflow_id: str):
        try:
            from tools.workflow_canvas.sla_checker import check_sla_from_db
            result = check_sla_from_db(workflow_id)
            return jsonify(result)
        except Exception as exc:
            logger.error("sla check error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-coach-01: AI Process Coach ───────────────────────────────────────
    @bp.route("/api/workflows/<workflow_id>/steps/<int:step_n>/coach")
    def api_step_coach(workflow_id: str, step_n: int):
        try:
            from tools.workflow_canvas.process_coach import coach_from_db
            result = coach_from_db(workflow_id, step_n)
            return jsonify(result)
        except Exception as exc:
            logger.error("coach error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    # ── pif-tasks-01: Role-Based My-Tasks Portal ──────────────────────────────
    @bp.route("/api/my-tasks")
    def api_my_tasks():
        task_type = request.args.get("task_type", "")
        try:
            conn = get_connection()
            ph = sql_placeholder(conn)
            if task_type:
                tasks = conn.execute(
                    f"SELECT id, title, status, priority, task_type, updated_at FROM kanban_tasks WHERE id LIKE {ph} AND task_type = {ph} AND status != {ph} ORDER BY updated_at DESC LIMIT 200",
                    ("processify-%", task_type, "done"),
                ).fetchall()
            else:
                tasks = conn.execute(
                    f"SELECT id, title, status, priority, task_type, updated_at FROM kanban_tasks WHERE id LIKE {ph} AND status != {ph} ORDER BY updated_at DESC LIMIT 200",
                    ("processify-%", "done"),
                ).fetchall()
            conn.close()
            return jsonify({"tasks": [dict(t) if hasattr(t, "keys") else {} for t in tasks]})
        except Exception as exc:
            logger.error("api-my-tasks error: %s", exc, exc_info=True)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/my-tasks")
    def page_my_tasks():
        task_type = request.args.get("task_type", "")
        try:
            conn = get_connection()
            ph = sql_placeholder(conn)
            if task_type:
                tasks = conn.execute(
                    f"SELECT * FROM kanban_tasks WHERE id LIKE {ph} AND task_type = {ph} AND status != {ph} ORDER BY updated_at DESC LIMIT 200",
                    ("processify-%", task_type, "done"),
                ).fetchall()
            else:
                tasks = conn.execute(
                    f"SELECT * FROM kanban_tasks WHERE id LIKE {ph} AND status != {ph} ORDER BY updated_at DESC LIMIT 200",
                    ("processify-%", "done"),
                ).fetchall()
            types_rows = conn.execute(
                f"SELECT DISTINCT task_type FROM kanban_tasks WHERE id LIKE {ph} AND task_type IS NOT NULL AND task_type != '' ORDER BY task_type",
                ("processify-%",),
            ).fetchall()
            conn.close()
            tasks_list = [dict(t) if hasattr(t, "keys") else {} for t in tasks]
            types_list = [r[0] for r in types_rows]
        except Exception as exc:
            logger.error("my-tasks error: %s", exc, exc_info=True)
            tasks_list, types_list = [], []
        return render_template(
            "workflow_canvas/my_tasks.html",
            tasks=tasks_list,
            roles=types_list,
            current_role=task_type,
        )


    return bp
