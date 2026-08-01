"""ICDEV™ Studio — API Blueprint.

Self-contained Flask Blueprint for all Studio endpoints.
Registration in app.py is a single line:
    app.register_blueprint(studio_api)
"""

from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, Response, jsonify, request, send_file, stream_with_context

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.dashboard.auth import require_role  # noqa: E402
from tools.studio.workflow_editor import (  # noqa: E402
    create_workflow,
    delete_workflow,
    get_builtin_template,
    get_tool_catalog,
    get_workflow,
    list_builtin_templates,
    list_workflows,
    save_workflow,
    update_workflow,
    workflow_to_composer_format,
)

studio_api = Blueprint("studio_api", __name__, url_prefix="/api/studio")

# Roles allowed to install/publish marketplace (SkillHub) assets. nav-intel-01:
# the install endpoint was previously unauthenticated — any caller could pull
# and install arbitrary marketplace assets into the environment.
_MARKETPLACE_MUTATION_ROLES = ("admin", "pm")

#: Binding an event source to a workflow, and firing a simulate that starts a
#: real run, are workflow-authoring actions. ``admin`` is listed explicitly:
#: ``require_role`` is an exact membership test with no hierarchy, so a bare
#: ("developer",) locks the administrator out of their own instance.
_TRIGGER_MUTATION_ROLES = ("admin", "developer")


# ── Workflow CRUD ──────────────────────────────────────────


@studio_api.route("/workflows", methods=["GET"])
def api_list_workflows():
    shared = request.args.get("shared") == "1"
    return jsonify({"workflows": list_workflows(shared_only=shared)})


@studio_api.route("/workflows/templates", methods=["GET"])
def list_workflow_templates():
    """List workflow templates from context/workflow_templates/*.yaml."""
    import yaml  # noqa: PLC0415 — deferred to avoid startup cost

    tpl_dir = _ROOT / "context" / "workflow_templates"
    if not tpl_dir.is_dir():
        return jsonify({"templates": [], "total": 0, "builtin": 0, "community": 0, "categories": {}})

    templates = []
    for path in sorted(tpl_dir.glob("*.yaml")):
        raw = path.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError:
            data = {}
        steps = data.get("steps", [])
        templates.append({
            "id": path.stem,
            "name": data.get("name", path.stem.replace("-", " ").replace("_", " ").title()),
            "description": data.get("description", ""),
            "category": data.get("category", "general"),
            "tags": data.get("tags", []),
            "author": data.get("author", "builtin"),
            "steps_count": len(steps) if isinstance(steps, list) else 0,
            "yaml": raw,
        })

    categories: dict[str, int] = {}
    for t in templates:
        cat = t["category"]
        categories[cat] = categories.get(cat, 0) + 1

    return jsonify({
        "templates": templates,
        "total": len(templates),
        "builtin": len(templates),
        "community": 0,
        "categories": categories,
    })


@studio_api.route("/workflows/<workflow_id>", methods=["GET"])
def api_get_workflow(workflow_id: str):
    wf = get_workflow(workflow_id)
    if not wf:
        return jsonify({"error": "Workflow not found"}), 404
    return jsonify(wf)


@studio_api.route("/workflows", methods=["POST"])
def api_create_workflow():
    import yaml as _yaml
    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    template_yaml = data.get("template_yaml", "").strip()
    # Accept {steps:[]} format (e.g. from E2E tests) when template_yaml is absent
    if not template_yaml and "steps" in data:
        template_yaml = _yaml.dump({
            "name": name,
            "description": data.get("description", ""),
            "steps": data.get("steps", []),
        })
    if not name or not template_yaml:
        return jsonify({"error": "name and template_yaml are required"}), 400
    result = create_workflow(
        name=name,
        template_yaml=template_yaml,
        description=data.get("description", ""),
        category=data.get("category", "custom"),
    )
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


@studio_api.route("/workflows/<workflow_id>", methods=["PATCH"])
def api_update_workflow(workflow_id: str):
    data = request.get_json(silent=True) or {}
    result = update_workflow(
        workflow_id,
        name=data.get("name"),
        description=data.get("description"),
        template_yaml=data.get("template_yaml"),
        category=data.get("category"),
        shared=data.get("shared"),
    )
    if result.get("status") == "error":
        code = 404 if "not found" in result.get("error", "") else 400
        return jsonify(result), code
    return jsonify(result)


@studio_api.route("/workflows/<workflow_id>", methods=["DELETE"])
def api_delete_workflow(workflow_id: str):
    result = delete_workflow(workflow_id)
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/workflows/<workflow_id>/composer", methods=["GET"])
def api_workflow_composer_format(workflow_id: str):
    """Return workflow in format compatible with workflow_composer.py."""
    data = workflow_to_composer_format(workflow_id)
    if not data:
        return jsonify({"error": "Workflow not found or invalid YAML"}), 404
    return jsonify(data)


@studio_api.route("/workflows/from-canvas", methods=["POST"])
def create_workflow_from_canvas():
    """Create a pre-filled workflow draft from a canvas template."""
    try:
        from tools.studio.canvas_bridge import get_canvas_workflow_template, list_canvas_ids
    except ImportError as exc:
        return jsonify({"error": f"canvas_bridge unavailable: {exc}"}), 503

    import uuid
    import yaml

    data = request.get_json(silent=True) or {}
    canvas_id = (data.get("canvas_id") or "").strip().lower()
    context_label = (data.get("context_label") or "").strip()
    design_id = (data.get("design_id") or "").strip()

    if not canvas_id or canvas_id not in list_canvas_ids():
        return jsonify({"error": f"Unknown canvas_id: {canvas_id!r}"}), 400

    template = dict(get_canvas_workflow_template(canvas_id))
    if context_label:
        base_desc = template.get("description", "")
        template["description"] = f"{base_desc} — {context_label}".strip(" —")
    if design_id:
        template["project_id"] = design_id

    wf_id = str(uuid.uuid4())
    wf_name = f"{canvas_id.upper()} Workflow" + (f" — {context_label}" if context_label else "")
    yaml_str = yaml.dump(template, default_flow_style=False, allow_unicode=True)

    save_workflow(wf_id, wf_name, yaml_str, category=template.get("category", "general"))

    return jsonify(
        {"workflow_id": wf_id, "name": wf_name, "redirect": f"/studio/workflows?load={wf_id}"}
    ), 201


# ── Tool Catalog ───────────────────────────────────────────


@studio_api.route("/tools/catalog", methods=["GET"])
def api_tool_catalog():
    return jsonify(get_tool_catalog())


# ── Roles (for human-gate node config dropdowns) ──────────


_STUDIO_ROLES = [
    {"id": "stakeholder", "name": "Stakeholder"},
    {"id": "program_manager", "name": "Program Manager"},
    {"id": "isso", "name": "ISSO / Security Officer"},
    {"id": "contracting_officer", "name": "Contracting Officer"},
    {"id": "developer", "name": "Developer / Architect"},
    {"id": "reviewer", "name": "Reviewer"},
    {"id": "approver", "name": "Approver"},
]


@studio_api.route("/roles", methods=["GET"])
def api_studio_roles():
    return jsonify({"roles": _STUDIO_ROLES})


@studio_api.route("/doc-templates", methods=["GET"])
def api_doc_templates():
    return jsonify({"doc_templates": []})


# ── Built-in Templates ─────────────────────────────────────


@studio_api.route("/templates", methods=["GET"])
def api_builtin_templates():
    return jsonify({"templates": list_builtin_templates()})


@studio_api.route("/templates/<template_id>", methods=["GET"])
def api_get_builtin_template(template_id: str):
    tpl = get_builtin_template(template_id)
    if not tpl:
        return jsonify({"error": "Template not found"}), 404
    return jsonify(tpl)


# ── Marketplace (read-only wrapper) ───────────────────────


@studio_api.route("/marketplace/assets", methods=["GET"])
def api_marketplace_assets():
    """List marketplace assets for storefront UI."""
    try:
        from tools.marketplace.catalog_manager import list_assets

        query = request.args.get("q", "")
        category = request.args.get("category", "")
        # nav-intel-01: list_assets() returns {"assets": [...], "total": ...};
        # unwrap to the list before filtering (previously iterated the dict keys
        # and 500'd whenever q/category filters were supplied).
        _res = list_assets()
        assets = _res.get("assets", []) if isinstance(_res, dict) else (_res or [])
        # Client-side filtering if params provided
        if query:
            q_lower = query.lower()
            assets = [
                a for a in assets if q_lower in a.get("name", "").lower() or q_lower in a.get("description", "").lower()
            ]
        if category:
            assets = [a for a in assets if a.get("category", "").lower() == category.lower()]
        return jsonify({"assets": assets, "total": len(assets)})
    except ImportError:
        return jsonify({"assets": [], "total": 0, "note": "Marketplace module not available"})


@studio_api.route("/marketplace/categories", methods=["GET"])
def api_marketplace_categories():
    """Return distinct asset categories."""
    try:
        from tools.marketplace.catalog_manager import list_assets

        _res = list_assets()
        assets = _res.get("assets", []) if isinstance(_res, dict) else (_res or [])
        cats = sorted({a.get("category", "uncategorized") for a in assets})
        return jsonify({"categories": cats})
    except ImportError:
        return jsonify({"categories": []})


@studio_api.route("/marketplace/assets/<asset_id>", methods=["GET"])
def api_marketplace_asset_detail(asset_id: str):
    """Get detailed info for a single asset."""
    try:
        from tools.marketplace.catalog_manager import get_asset

        asset = get_asset(asset_id)
        if not asset:
            return jsonify({"error": "Asset not found"}), 404
        return jsonify(asset)
    except ImportError:
        return jsonify({"error": "Marketplace module not available"}), 503


@studio_api.route("/marketplace/assets/<asset_id>/install", methods=["POST"])
@require_role(*_MARKETPLACE_MUTATION_ROLES)
def api_marketplace_install(asset_id: str):
    """Install a marketplace asset."""
    try:
        from tools.marketplace.install_manager import install_asset

        result = install_asset(asset_id)
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "Marketplace module not available"}), 503
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Form Builder (Phase 72c) ──────────────────────────────


@studio_api.route("/forms/field-types", methods=["GET"])
def api_form_field_types():
    from tools.studio.form_builder import get_field_types

    return jsonify({"field_types": get_field_types()})


@studio_api.route("/forms/templates", methods=["GET"])
def api_form_templates():
    from tools.studio.form_builder import get_form_templates

    return jsonify({"templates": get_form_templates()})


@studio_api.route("/forms", methods=["GET"])
def api_list_forms():
    from tools.studio.form_builder import list_forms

    status = request.args.get("status")
    return jsonify({"forms": list_forms(status=status)})


@studio_api.route("/forms", methods=["POST"])
def api_create_form():
    from tools.studio.form_builder import create_form

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    fields = data.get("fields", [])
    if not name or not fields:
        return jsonify({"error": "name and fields are required"}), 400
    result = create_form(name, fields, description=data.get("description", ""))
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


@studio_api.route("/forms/<form_id>", methods=["GET"])
def api_get_form(form_id: str):
    from tools.studio.form_builder import get_form

    form = get_form(form_id)
    if not form:
        return jsonify({"error": "Form not found"}), 404
    return jsonify(form)


@studio_api.route("/forms/<form_id>", methods=["PATCH"])
def api_update_form(form_id: str):
    from tools.studio.form_builder import update_form

    data = request.get_json(silent=True) or {}
    result = update_form(
        form_id,
        name=data.get("name"),
        fields=data.get("fields"),
        description=data.get("description"),
        status=data.get("status"),
    )
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/forms/<form_id>", methods=["DELETE"])
def api_delete_form(form_id: str):
    from tools.studio.form_builder import delete_form

    result = delete_form(form_id)
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/forms/<form_id>/submissions", methods=["GET"])
def api_list_submissions(form_id: str):
    from tools.studio.form_builder import list_submissions

    return jsonify({"submissions": list_submissions(form_id)})


@studio_api.route("/forms/<form_id>/submissions", methods=["POST"])
def api_submit_form(form_id: str):
    from tools.studio.form_builder import submit_form

    data = request.get_json(silent=True) or {}
    result = submit_form(form_id, data.get("data", {}))
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


# ── Case Management (Phase 72c) ───────────────────────────


@studio_api.route("/cases/templates", methods=["GET"])
def api_case_lifecycle_templates():
    from tools.studio.case_manager import get_lifecycle_templates

    return jsonify({"templates": get_lifecycle_templates()})


@studio_api.route("/cases/types", methods=["GET"])
def api_list_case_types():
    from tools.studio.case_manager import list_case_types

    return jsonify({"types": list_case_types()})


@studio_api.route("/cases/types", methods=["POST"])
def api_create_case_type():
    from tools.studio.case_manager import create_case_type

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    lifecycle = data.get("lifecycle", {})
    if not name or not lifecycle:
        return jsonify({"error": "name and lifecycle are required"}), 400
    result = create_case_type(name, lifecycle, description=data.get("description", ""))
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


@studio_api.route("/cases/types/<type_id>", methods=["GET"])
def api_get_case_type(type_id: str):
    from tools.studio.case_manager import get_case_type

    ct = get_case_type(type_id)
    if not ct:
        return jsonify({"error": "Case type not found"}), 404
    return jsonify(ct)


@studio_api.route("/cases/types/<type_id>/board", methods=["GET"])
def api_case_board(type_id: str):
    from tools.studio.case_manager import get_case_board

    result = get_case_board(type_id)
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/cases", methods=["GET"])
def api_list_cases():
    from tools.studio.case_manager import list_cases

    return jsonify(
        {
            "cases": list_cases(
                type_id=request.args.get("type_id"),
                state=request.args.get("state"),
                priority=request.args.get("priority"),
            )
        }
    )


@studio_api.route("/cases", methods=["POST"])
def api_create_case():
    from tools.studio.case_manager import create_case

    data = request.get_json(silent=True) or {}
    result = create_case(
        data.get("type_id", ""),
        data.get("title", "").strip(),
        description=data.get("description", ""),
        priority=data.get("priority", "medium"),
        assigned_to=data.get("assigned_to"),
        form_submission_id=data.get("form_submission_id"),
    )
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


@studio_api.route("/cases/<case_id>", methods=["GET"])
def api_get_case(case_id: str):
    from tools.studio.case_manager import get_case

    case = get_case(case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    return jsonify(case)


@studio_api.route("/cases/<case_id>/transition", methods=["POST"])
def api_transition_case(case_id: str):
    from tools.studio.case_manager import transition_case

    data = request.get_json(silent=True) or {}
    to_state = data.get("to_state", "").strip()
    if not to_state:
        return jsonify({"error": "to_state is required"}), 400
    result = transition_case(case_id, to_state, comment=data.get("comment", ""))
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result)


# ── Dashboard Builder (Phase 72e) ──────────────────────────


@studio_api.route("/dashboards/widgets", methods=["GET"])
def api_widget_types():
    from tools.studio.dashboard_builder import get_widget_types

    return jsonify({"widgets": get_widget_types()})


@studio_api.route("/dashboards/role-defaults", methods=["GET"])
def api_role_defaults():
    from tools.studio.dashboard_builder import get_role_defaults

    return jsonify(get_role_defaults())


@studio_api.route("/dashboards", methods=["GET"])
def api_list_dashboards():
    from tools.studio.dashboard_builder import list_dashboards

    return jsonify({"dashboards": list_dashboards(role=request.args.get("role"))})


@studio_api.route("/dashboards", methods=["POST"])
def api_create_dashboard():
    from tools.studio.dashboard_builder import create_dashboard, create_from_role_default

    data = request.get_json(silent=True) or {}
    if data.get("from_role"):
        result = create_from_role_default(data["from_role"])
    else:
        name = data.get("name", "").strip()
        layout = data.get("layout", [])
        if not name:
            return jsonify({"error": "name is required"}), 400
        result = create_dashboard(name, layout, shared=data.get("shared", False))
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


@studio_api.route("/dashboards/<dash_id>", methods=["GET"])
def api_get_dashboard(dash_id: str):
    from tools.studio.dashboard_builder import get_dashboard

    dash = get_dashboard(dash_id)
    if not dash:
        return jsonify({"error": "Dashboard not found"}), 404
    return jsonify(dash)


@studio_api.route("/dashboards/<dash_id>", methods=["PATCH"])
def api_update_dashboard(dash_id: str):
    from tools.studio.dashboard_builder import update_dashboard

    data = request.get_json(silent=True) or {}
    result = update_dashboard(dash_id, name=data.get("name"), layout=data.get("layout"), shared=data.get("shared"))
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/dashboards/<dash_id>", methods=["DELETE"])
def api_delete_dashboard(dash_id: str):
    from tools.studio.dashboard_builder import delete_dashboard

    result = delete_dashboard(dash_id)
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


# ── Citizen Automations (Phase 72d) ────────────────────────


@studio_api.route("/automations/triggers", methods=["GET"])
def api_automation_triggers():
    from tools.studio.automation_builder import get_trigger_types

    return jsonify({"triggers": get_trigger_types()})


@studio_api.route("/automations/operators", methods=["GET"])
def api_automation_operators():
    from tools.studio.automation_builder import get_condition_operators

    return jsonify({"operators": get_condition_operators()})


@studio_api.route("/automations/actions", methods=["GET"])
def api_automation_actions():
    from tools.studio.automation_builder import get_action_types

    return jsonify({"actions": get_action_types()})


@studio_api.route("/automations/templates", methods=["GET"])
def api_automation_templates():
    from tools.studio.automation_builder import get_automation_templates

    return jsonify({"templates": get_automation_templates()})


@studio_api.route("/automations", methods=["GET"])
def api_list_automations():
    from tools.studio.automation_builder import list_automations

    return jsonify({"automations": list_automations()})


@studio_api.route("/automations", methods=["POST"])
def api_create_automation():
    from tools.studio.automation_builder import create_automation

    data = request.get_json(silent=True) or {}
    name = data.get("name", "").strip()
    trigger = data.get("trigger", {})
    actions = data.get("actions", [])
    if not name or not trigger or not actions:
        return jsonify({"error": "name, trigger, and actions are required"}), 400
    result = create_automation(
        name,
        trigger,
        actions,
        description=data.get("description", ""),
        conditions=data.get("conditions"),
    )
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


@studio_api.route("/automations/<auto_id>", methods=["GET"])
def api_get_automation(auto_id: str):
    from tools.studio.automation_builder import get_automation

    auto = get_automation(auto_id)
    if not auto:
        return jsonify({"error": "Automation not found"}), 404
    return jsonify(auto)


@studio_api.route("/automations/<auto_id>/toggle", methods=["POST"])
def api_toggle_automation(auto_id: str):
    from tools.studio.automation_builder import toggle_automation

    data = request.get_json(silent=True) or {}
    return jsonify(toggle_automation(auto_id, data.get("enabled", True)))


@studio_api.route("/automations/<auto_id>", methods=["DELETE"])
def api_delete_automation(auto_id: str):
    from tools.studio.automation_builder import delete_automation

    result = delete_automation(auto_id)
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/automations/<auto_id>/simulate", methods=["POST"])
def api_simulate_automation(auto_id: str):
    from tools.studio.automation_builder import simulate_automation

    data = request.get_json(silent=True) or {}
    return jsonify(simulate_automation(auto_id, data.get("test_event")))


@studio_api.route("/automations/<auto_id>/trigger", methods=["POST"])
def api_trigger_automation(auto_id: str):
    """Fire an automation for a real event — actions execute for real."""
    from tools.studio.automation_builder import trigger_automation

    data = request.get_json(silent=True) or {}
    result = trigger_automation(auto_id, data.get("event"))
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/automations/runs", methods=["GET"])
def api_automation_runs():
    from tools.studio.automation_builder import list_automation_runs

    auto_id = request.args.get("automation_id")
    return jsonify({"runs": list_automation_runs(automation_id=auto_id)})


# ── NL App Builder (Phase 72b) ────────────────────────────


@studio_api.route("/app-builder/extract", methods=["POST"])
def api_app_builder_extract():
    """Extract capabilities from a natural language description."""
    from tools.studio.nl_app_builder import extract_from_description

    data = request.get_json(silent=True) or {}
    desc = data.get("description", "").strip()
    if not desc:
        return jsonify({"error": "description is required"}), 400
    return jsonify(extract_from_description(desc))


@studio_api.route("/app-builder/sessions", methods=["POST"])
def api_app_builder_create():
    """Create a new NL app builder session."""
    from tools.studio.nl_app_builder import create_builder_session

    data = request.get_json(silent=True) or {}
    desc = data.get("description", "").strip()
    if not desc:
        return jsonify({"error": "description is required"}), 400
    result = create_builder_session(desc, app_name=data.get("app_name"))
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result), 201


@studio_api.route("/app-builder/sessions/<session_id>/refine", methods=["PATCH"])
def api_app_builder_refine(session_id: str):
    """Refine an existing builder session."""
    from tools.studio.nl_app_builder import refine_session

    data = request.get_json(silent=True) or {}
    result = refine_session(
        session_id,
        app_name=data.get("app_name"),
        toggle_capabilities=data.get("toggle_capabilities"),
        classification=data.get("classification"),
    )
    if result.get("status") == "error":
        return jsonify(result), 404
    return jsonify(result)


@studio_api.route("/app-builder/sessions/<session_id>/build", methods=["POST"])
def api_app_builder_build(session_id: str):
    """Execute the build from a confirmed session."""
    from tools.studio.nl_app_builder import execute_build

    data = request.get_json(silent=True) or {}
    result = execute_build(session_id, output_dir=data.get("output_dir"))
    if result.get("status") == "error":
        return jsonify(result), 400
    return jsonify(result)


@studio_api.route("/app-builder/from-prd/<intake_session_id>", methods=["POST"])
def api_app_builder_from_prd(intake_session_id: str):
    """Bootstrap an App Builder session from a completed intake PRD."""
    import re as _re

    try:
        from tools.requirements.prd_generator import generate_prd as _gen_prd
    except ImportError:
        return jsonify({"error": "PRD generator not available"}), 503

    try:
        from tools.studio.nl_app_builder import create_builder_session
    except ImportError:
        return jsonify({"error": "App builder not available"}), 503

    # Load PRD markdown
    try:
        prd_result = _gen_prd(intake_session_id)
    except Exception as exc:
        return jsonify({"error": f"PRD generation failed: {exc}"}), 500

    if prd_result.get("status") != "ok":
        return jsonify({"error": prd_result.get("error", "PRD not found")}), 404

    prd_markdown = prd_result.get("prd_markdown") or prd_result.get("prd") or ""

    # Extract app name from first H1 heading in the PRD
    title_match = _re.search(r"^#\s+(.+)$", prd_markdown, _re.MULTILINE)
    app_name = title_match.group(1).strip() if title_match else f"App {intake_session_id[:8]}"

    # Load requirement types to enrich the description with capability hints
    _REQ_TYPE_CAPABILITY_MAP = {
        "security": "security scanning and role-based access control",
        "compliance": "FedRAMP/CMMC compliance tracking",
        "data": "data pipeline and storage management",
        "performance": "performance monitoring and SLO dashboards",
        "interface": "REST API with OpenAPI documentation",
        "operational": "operational runbooks and alerting",
    }
    capability_hints: list[str] = []
    try:
        from tools.db.storage import get_connection
        from pathlib import Path as _Path
        _DB_PATH = _Path(__file__).resolve().parents[3] / "data" / "icdev.db"
        import os as _os
        if _os.environ.get("ICDEV_STORAGE_BACKEND", "").lower() == "postgresql":
            conn = get_connection()
        else:
            conn = get_connection(db_path=str(_DB_PATH))
        try:
            conn.set_security_context(None)  # rls-bypass: studio reads intake_requirements by session_id, not user tenant
        except Exception:
            pass
        rows = conn.execute(
            "SELECT DISTINCT requirement_type FROM intake_requirements WHERE session_id = %s",
            (intake_session_id,),
        ).fetchall()
        for row in rows:
            rt = (row[0] or "").lower()
            if rt in _REQ_TYPE_CAPABILITY_MAP:
                capability_hints.append(_REQ_TYPE_CAPABILITY_MAP[rt])
    except Exception:
        pass

    # Build composite description: first 2000 chars of PRD + capability hints
    description = prd_markdown[:2000]
    if capability_hints:
        description += "\n\nKey capabilities required: " + ", ".join(capability_hints) + "."

    # Create builder session
    try:
        result = create_builder_session(description, app_name=app_name)
    except Exception as exc:
        return jsonify({"error": f"Builder session creation failed: {exc}"}), 500

    if result.get("status") == "error":
        return jsonify(result), 400

    builder_session_id = result.get("session_id", "")
    return jsonify({
        "status": "ok",
        "builder_session_id": builder_session_id,
        "app_name": app_name,
        "preview_url": f"/studio/app-builder?session_id={builder_session_id}",
        "blueprint_preview": result.get("blueprint_preview"),
    })


# ── Workflow Execution (wex Phase 1-3) ─────────────────────


@studio_api.route("/workflows/<workflow_id>/run", methods=["POST"])
def api_run_workflow(workflow_id: str):
    """Start an async workflow execution. Returns run_id for SSE streaming."""
    from tools.studio.workflow_runner import start_run

    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id", "default")
    try:
        run_id = start_run(workflow_id, project_id=project_id)
        return jsonify({"status": "ok", "run_id": run_id}), 202
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@studio_api.route("/workflows/runs/<run_id>/stream", methods=["GET"])
def api_stream_run(run_id: str):
    """Per-run SSE stream. Each event is: data: <json>\n\n"""
    from tools.studio.workflow_runner import stream_run

    def generate():
        yield from stream_run(run_id)

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@studio_api.route("/artifacts/<path:filepath>", methods=["GET"])
def api_download_artifact(filepath: str):
    """Download a generated workflow artifact file."""
    import mimetypes
    filepath = filepath.replace("\\", "/")
    safe_path = (_ROOT / Path(filepath)).resolve()
    # Security: must stay inside _ROOT/data/studio_artifacts
    artifacts_root = (_ROOT / "data" / "studio_artifacts").resolve()
    if not str(safe_path).startswith(str(artifacts_root)):
        return jsonify({"error": "Access denied"}), 403
    if not safe_path.exists():
        return jsonify({"error": "Artifact not found"}), 404
    mime, _ = mimetypes.guess_type(str(safe_path))
    as_attachment = request.args.get("download", "0") == "1"
    return send_file(safe_path, mimetype=mime or "text/plain", as_attachment=as_attachment,
                     download_name=safe_path.name)


@studio_api.route("/workflows/runs", methods=["GET"])
def api_list_runs():
    """List workflow runs. Optional ?workflow_id= filter."""
    from tools.studio.workflow_runner import list_runs

    workflow_id = request.args.get("workflow_id")
    limit = int(request.args.get("limit", 50))
    return jsonify({"runs": list_runs(workflow_id=workflow_id, limit=limit)})


@studio_api.route("/workflows/runs/<run_id>", methods=["GET"])
def api_get_run(run_id: str):
    from tools.studio.workflow_runner import get_run, get_run_steps

    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    run["steps"] = get_run_steps(run_id)

    # dwo-evt-04-d5: what started this run.
    #
    # The `trigger_event` key is not a new contract — workflow-studio-exec.js
    # has rendered `_triggerBadge(run.trigger_event)` since dwo-vv-03-d3, but
    # nothing on the server ever set it, so the badge was unreachable code that
    # silently resolved to '' on every run. This is the missing half.
    #
    # Absent (not null) for a manual run: the badge renders on presence. Never
    # fatal — a run detail that 500s because its provenance lookup failed is a
    # worse outcome than a run detail with no badge.
    try:
        from tools.studio.event_sources import trigger_event_for_run

        trigger_event = trigger_event_for_run(run_id)
        if trigger_event:
            run["trigger_event"] = trigger_event
    except Exception:
        pass

    return jsonify(run)


# ── Workflow triggers panel (dwo-evt-04-d5) ────────────────


@studio_api.route("/workflows/<workflow_id>/triggers", methods=["GET"])
def api_workflow_triggers(workflow_id: str):
    """Sources + bound triggers for the editor's Triggers tab."""
    from tools.studio.workflow_editor import build_triggers_panel

    return jsonify(build_triggers_panel(workflow_id))


@studio_api.route("/workflows/<workflow_id>/triggers", methods=["POST"])
@require_role(*_TRIGGER_MUTATION_ROLES)
def api_create_workflow_trigger(workflow_id: str):
    """Bind an event source to this workflow.

    ``event_filter`` and ``input_mapping`` are accepted as already-parsed JSON:
    the panel parses the textareas client-side so a malformed filter is caught
    while the user is still looking at it, rather than becoming a trigger that
    silently never matches.
    """
    from tools.studio.event_sources import create_workflow_trigger

    data = request.get_json(silent=True) or {}
    source_id = (data.get("source_id") or "").strip()
    if not source_id:
        return jsonify({"error": "source_id is required"}), 400

    event_filter = data.get("event_filter")
    if event_filter not in (None, "") and not isinstance(event_filter, list):
        return jsonify({"error": "event_filter must be a list of conditions"}), 400
    input_mapping = data.get("input_mapping")
    if input_mapping not in (None, "") and not isinstance(input_mapping, dict):
        return jsonify({"error": "input_mapping must be an object"}), 400

    try:
        trigger = create_workflow_trigger(
            workflow_id,
            source_id,
            event_type=(data.get("event_type") or "").strip(),
            event_filter=event_filter or None,
            input_mapping=input_mapping or None,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"status": "ok", "trigger": trigger}), 201


@studio_api.route("/workflows/<workflow_id>/triggers/simulate", methods=["POST"])
@require_role(*_TRIGGER_MUTATION_ROLES)
def api_simulate_workflow_trigger(workflow_id: str):
    """Feed a test payload through the real dispatch path.

    Deliberately calls ``dispatch_event`` rather than starting a run directly.
    A simulate button that reimplemented matching would answer a question about
    itself instead of about the trigger: filter evaluation, input mapping, the
    audit row and the run all have to be the production code path, or a green
    simulate proves nothing about what a live webhook will do.
    """
    from tools.studio.event_sources import dispatch_event

    data = request.get_json(silent=True) or {}
    source_id = (data.get("source_id") or "").strip()
    if not source_id:
        return jsonify({"error": "source_id is required"}), 400
    payload = data.get("payload")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return jsonify({"error": "payload must be an object"}), 400

    try:
        result = dispatch_event(
            source_id,
            (data.get("event_type") or "").strip(),
            payload,
            project_id=(data.get("project_id") or "default"),
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    # The dispatch fans out to every trigger on the source; the panel only cares
    # about the ones pointing at the workflow being edited.
    result["workflow_id"] = workflow_id
    return jsonify(result), 200


@studio_api.route("/workflows/runs/<run_id>/steps/<step_run_id>/approve", methods=["POST"])
def api_approve_step(run_id: str, step_run_id: str):
    from tools.studio.workflow_runner import approve_step
    data = request.get_json(silent=True) or {}
    actor = data.get("actor", "approver")
    ok = approve_step(step_run_id, actor=actor)
    if not ok:
        return jsonify({"error": "No pending approval for this step — it may have already been resolved or timed out"}), 404
    return jsonify({"status": "approved", "step_run_id": step_run_id})


@studio_api.route("/workflows/runs/<run_id>/steps/<step_run_id>/reject", methods=["POST"])
def api_reject_step(run_id: str, step_run_id: str):
    from tools.studio.workflow_runner import reject_step
    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "")
    actor = data.get("actor", "approver")
    ok = reject_step(step_run_id, reason=reason, actor=actor)
    if not ok:
        return jsonify({"error": "No pending approval for this step — it may have already been resolved or timed out"}), 404
    return jsonify({"status": "rejected", "step_run_id": step_run_id})


@studio_api.route("/runs/<run_id>/resume", methods=["POST"])
@studio_api.route("/workflows/runs/<run_id>/resume", methods=["POST"])
def api_resume_run(run_id: str):
    """Continue a run left mid-flight (dwo-dur-03).

    Steps already recorded success/approved/skipped are replayed, not
    re-executed, and the run continues in place rather than forking a new row.
    """
    from tools.studio.workflow_runner import (
        RESUMABLE_RUN_STATUSES,
        RESUME_MODE,
        get_run,
        resume_run,
    )

    run = get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    status = run.get("status")
    if status not in RESUMABLE_RUN_STATUSES:
        return jsonify({
            "error": f"Run is '{status}' — only {', '.join(RESUMABLE_RUN_STATUSES)} runs can be resumed"
        }), 409
    if not resume_run(run_id):
        return jsonify({
            "error": "Run could not be resumed — a live worker may already own it, "
                     "or its workflow no longer exists"
        }), 409
    return jsonify({"status": "resuming", "run_id": run_id, "mode": RESUME_MODE}), 202


@studio_api.route("/workflows/runs/<run_id>", methods=["DELETE"])
def api_delete_run(run_id: str):
    from tools.studio.workflow_runner import delete_run

    deleted = delete_run(run_id)
    if not deleted:
        return jsonify({"error": "Run not found"}), 404
    return jsonify({"status": "deleted", "run_id": run_id})


@studio_api.route("/workflows/runs", methods=["DELETE"])
def api_delete_all_runs():
    from tools.studio.workflow_runner import delete_all_runs

    workflow_id = request.args.get("workflow_id")
    count = delete_all_runs(workflow_id=workflow_id or None)
    return jsonify({"status": "deleted", "count": count})


@studio_api.route("/workflows/<workflow_id>/generate-code", methods=["POST"])
def api_generate_code(workflow_id: str):
    """Generate a standalone Python script for this workflow."""
    from tools.studio.workflow_runner import generate_python_script

    try:
        script = generate_python_script(workflow_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    wf_name = workflow_id.replace("-", "_")
    return Response(
        script,
        mimetype="text/x-python",
        headers={"Content-Disposition": f'attachment; filename="workflow_{wf_name}.py"'},
    )


# ── Chat-to-Workflow (wex Phase 4-5) ───────────────────────


@studio_api.route("/chat/generate-workflow", methods=["POST"])
def api_chat_generate_workflow():
    """Generate workflow YAML from a natural language message via Ollama/Qwen3."""
    import concurrent.futures  # noqa: PLC0415

    from tools.studio.workflow_chat import generate_workflow_yaml  # noqa: PLC0415

    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    if not message:
        return jsonify({"error": "message is required"}), 400

    history = data.get("history", [])
    _TIMEOUT = 25  # seconds — prevents dashboard hang when LLM is slow
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _ex:
            _fut = _ex.submit(generate_workflow_yaml, message, history or None)
            result = _fut.result(timeout=_TIMEOUT)
    except concurrent.futures.TimeoutError:
        return jsonify({"status": "error", "error": "LLM did not respond within 25 seconds. Try again or check Ollama status.", "raw": ""}), 504
    if result.get("status") == "error":
        return jsonify(result), 422
    return jsonify(result)
