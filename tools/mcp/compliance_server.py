#!/usr/bin/env python3
# CUI // SP-CTI
"""Compliance MCP server exposing NIST 800-53, SSP, POAM, STIG, SBOM, and CUI tools.

Tools:
    nist_lookup    - Look up NIST 800-53 Rev 5 controls by ID or family
    ssp_generate   - Generate a System Security Plan document
    poam_generate  - Generate a Plan of Action & Milestones document
    stig_check     - Run STIG compliance checks against a project
    sbom_generate  - Generate a Software Bill of Materials (CycloneDX)
    cui_mark       - Apply CUI markings to a file or content
    control_map    - Map project activities to NIST 800-53 controls

Runs as an MCP server over stdio with Content-Length framing.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

sys.path.insert(0, str(BASE_DIR))
from tools.mcp.base_server import MCPServer  # noqa: E402


# ---------------------------------------------------------------------------
# Lazy tool imports — tools may still be under construction
# ---------------------------------------------------------------------------


def _import_tool(module_path, func_name):
    """Dynamically import a function from a module. Returns None if unavailable."""
    try:
        import importlib

        mod = importlib.import_module(module_path)
        return getattr(mod, func_name, None)
    except (ImportError, ModuleNotFoundError, AttributeError):
        return None


def _available_stig_ids():
    """The STIG templates that actually ship, for the ``stig_check`` enum.

    Derived, never hand-listed. An enum is a promise the caller reads before
    it calls; listing a profile whose template is not on disk turns that
    promise into a FileNotFoundError.
    """
    lister = _import_tool("tools.compliance.stig_checker", "_list_stig_templates")
    ids = sorted(lister() or []) if lister else []
    return ids or ["webapp"]


def _control_mapper_statuses():
    """The implementation statuses ``create_mapping`` will accept.

    Read from the callee's own constant so the schema and the ValueError it
    would raise can never drift apart.
    """
    statuses = _import_tool("tools.compliance.control_mapper", "VALID_STATUSES")
    return list(statuses) if statuses else ["planned", "implemented", "not_applicable"]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def handle_nist_lookup(args: dict) -> dict:
    """Look up a NIST 800-53 control by ID or list controls by family."""
    lookup = _import_tool("tools.compliance.nist_lookup", "lookup")
    list_family = _import_tool("tools.compliance.nist_lookup", "list_family")
    list_all_families = _import_tool("tools.compliance.nist_lookup", "list_all_families")

    control_id = args.get("control_id")
    family = args.get("family")

    if control_id and lookup:
        result = lookup(control_id)
        if result:
            return {"control": result}
        return {"error": f"Control not found: {control_id}"}

    if family and list_family:
        controls = list_family(family)
        return {"family": family, "controls": controls, "count": len(controls)}

    if list_all_families:
        families = list_all_families()
        return {"families": families}

    return {"error": "nist_lookup module not available"}


def handle_ssp_generate(args: dict) -> dict:
    """Generate a System Security Plan for a project."""
    generate = _import_tool("tools.compliance.ssp_generator", "generate_ssp")
    if not generate:
        return {"error": "ssp_generator module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    return generate(project_id, output_dir=output_dir, db_path=str(DB_PATH))


def handle_poam_generate(args: dict) -> dict:
    """Generate a Plan of Action & Milestones document."""
    generate = _import_tool("tools.compliance.poam_generator", "generate_poam")
    if not generate:
        return {"error": "poam_generator module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    return generate(project_id, output_dir=output_dir, db_path=str(DB_PATH))


def handle_stig_check(args: dict) -> dict:
    """Run STIG compliance checks against a project.

    ``stig_checker`` has always exported ``run_stig_check``; this handler asked
    for ``check_project``, so ``_import_tool`` returned None and every call
    answered a "not available yet" stub for a module that shipped complete
    (rmf-inert-01). ``stig_profile`` is still accepted as an alias for the
    checker's own ``stig_id``.
    """
    check = _import_tool("tools.compliance.stig_checker", "run_stig_check")
    if not check:
        return {"error": "stig_checker module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    stig_id = args.get("stig_id") or args.get("stig_profile") or "webapp"
    # DB_PATH, not str(DB_PATH): stig_checker._get_connection calls
    # path.exists(), which a str does not have. Every other handler in this
    # file passes a str because its callee takes one; these three do not.
    return check(
        project_id,
        stig_id=stig_id,
        gate=bool(args.get("gate", False)),
        db_path=DB_PATH,
    )


def handle_sbom_generate(args: dict) -> dict:
    """Generate a Software Bill of Materials in CycloneDX or SPDX.

    ``generate_sbom`` takes a project id and reads that project's
    ``directory_path`` from the database; it has never taken a directory. The
    previous call passed ``project_dir`` positionally *and* ``project_id`` by
    keyword, which raised TypeError on every invocation. ``project_dir`` is
    still accepted so existing callers do not break, but it is the project row
    that decides which tree is scanned.
    """
    generate = _import_tool("tools.compliance.sbom_generator", "generate_sbom")
    if not generate:
        return {"error": "sbom_generator module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    sbom_format = args.get("format", "cyclonedx")
    path = generate(project_id, sbom_format=sbom_format, db_path=str(DB_PATH))
    return {"project_id": project_id, "format": sbom_format, "sbom_path": path}


def handle_cui_mark(args: dict) -> dict:
    """Apply CUI markings to a file.

    Found by the same sweep as the three the card named: this asked for
    ``mark_file`` and ``mark_content``, neither of which ``cui_marker`` has
    ever exported, so it always fell through to "cui_marker module not
    available". The real exports are ``mark_document`` (banner top and bottom,
    for the DOCUMENT_EXTENSIONS) and ``mark_code_file`` (a comment header, for
    the COMMENT_STYLES) — routed here exactly as ``mark_directory`` routes them.

    The ``content`` and ``marking`` parameters are gone from the schema rather
    than faked: marking a STRING is a capability this module does not have, and
    the banner text comes from ``args/cui_markings.yaml``, not from a caller.
    """
    mark_document = _import_tool("tools.compliance.cui_marker", "mark_document")
    mark_code_file = _import_tool("tools.compliance.cui_marker", "mark_code_file")
    if not mark_document or not mark_code_file:
        return {"error": "cui_marker module not available"}

    file_path = args.get("file_path")
    if not file_path:
        raise ValueError("'file_path' is required")

    from pathlib import Path as _Path

    from tools.compliance.cui_marker import COMMENT_STYLES, DOCUMENT_EXTENSIONS

    ext = _Path(file_path).suffix.lower()
    dry_run = bool(args.get("dry_run", False))

    if ext in DOCUMENT_EXTENSIONS:
        result = mark_document(file_path, dry_run=dry_run)
    elif ext in COMMENT_STYLES:
        result = mark_code_file(file_path, dry_run=dry_run)
    else:
        return {
            "file": file_path,
            "marked": False,
            "error": f"Unsupported extension '{ext}'. Supported: "
            + ", ".join(sorted(set(COMMENT_STYLES) | DOCUMENT_EXTENSIONS)),
        }

    # mark_document/mark_code_file return None when the file is ALREADY marked
    # — that is "nothing to do", not a failure, and the two must not read alike.
    return {
        "file": file_path,
        "marked": result is not None,
        "already_marked": result is None,
        "dry_run": dry_run,
        "path": str(result) if result else None,
    }


def handle_control_map(args: dict) -> dict:
    """Record a project's implementation of a NIST 800-53 control.

    This asked for ``map_activity`` — a function that has never existed in any
    module in the tree, so the tool answered "control_mapper module not
    available" for a mapper that shipped complete. The DECLARED capability was
    wrong too: ``control_mapper`` maps a project to a CONTROL, never an
    activity to a set of controls, so the schema moved with the handler rather
    than the handler inventing an activity crosswalk (rmf-inert-01).
    """
    create_mapping = _import_tool("tools.compliance.control_mapper", "create_mapping")
    if not create_mapping:
        return {"error": "control_mapper module not available"}

    project_id = args.get("project_id")
    control_id = args.get("control_id")
    if not project_id:
        raise ValueError("'project_id' is required")
    if not control_id:
        raise ValueError("'control_id' is required")

    row_id = create_mapping(
        project_id,
        control_id,
        implementation_status=args.get("implementation_status", "planned"),
        description=args.get("description"),
        responsible_role=args.get("responsible_role"),
        evidence_path=args.get("evidence_path"),
        # A Path, not a str — control_mapper._get_connection calls path.exists()
        # when tools.compat.db_utils is unavailable.
        db_path=DB_PATH,
    )
    return {
        "mapping_id": row_id,
        "project_id": project_id,
        "control_id": control_id.upper(),
        "implementation_status": args.get("implementation_status", "planned"),
    }


# ---------------------------------------------------------------------------
# CSSP tool handlers
# ---------------------------------------------------------------------------


def handle_cssp_assess(args: dict) -> dict:
    """Run CSSP assessment per DI 8530.01."""
    assess = _import_tool("tools.compliance.cssp_assessor", "run_cssp_assessment")
    if not assess:
        return {"error": "cssp_assessor module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    functional_area = args.get("functional_area", "all")
    # A Path, not a str — cssp_assessor._get_connection calls path.exists().
    return assess(
        project_id,
        functional_area=functional_area,
        gate=bool(args.get("gate", False)),
        db_path=DB_PATH,
    )


def handle_cssp_report(args: dict) -> dict:
    """Generate a CSSP certification report."""
    generate = _import_tool("tools.compliance.cssp_report_generator", "generate_cssp_report")
    if not generate:
        return {"error": "cssp_report_generator module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    return generate(project_id, output_dir=output_dir, db_path=str(DB_PATH))


def handle_cssp_ir_plan(args: dict) -> dict:
    """Generate an Incident Response Plan per CSSP SOC requirements."""
    generate = _import_tool("tools.compliance.incident_response_plan", "generate_ir_plan")
    if not generate:
        return {"error": "incident_response_plan module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    return generate(project_id, output_dir=output_dir, db_path=str(DB_PATH))


def handle_cssp_evidence(args: dict) -> dict:
    """Collect and index evidence artifacts for CSSP assessment."""
    collect = _import_tool("tools.compliance.cssp_evidence_collector", "collect_evidence")
    if not collect:
        return {"error": "cssp_evidence_collector module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    project_dir = args.get("project_dir")
    return collect(project_id, project_dir=project_dir, db_path=str(DB_PATH))


# ---------------------------------------------------------------------------
# Xacta integration handlers
# ---------------------------------------------------------------------------


def handle_xacta_sync(args: dict) -> dict:
    """Sync project compliance data to Xacta 360."""
    sync = _import_tool("tools.compliance.xacta.xacta_sync", "sync_to_xacta")
    if not sync:
        return {"error": "xacta_sync module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    mode = args.get("mode", "hybrid")
    return sync(project_id, mode=mode, db_path=str(DB_PATH))


def handle_xacta_export(args: dict) -> dict:
    """Generate Xacta 360-compatible export files."""
    export_all = _import_tool("tools.compliance.xacta.xacta_export", "export_all")
    if not export_all:
        return {"error": "xacta_export module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    export_format = args.get("format", "oscal")
    return export_all(project_id, export_format=export_format, db_path=str(DB_PATH))


# ---------------------------------------------------------------------------
# SbD (Secure by Design) handlers
# ---------------------------------------------------------------------------


def handle_sbd_assess(args: dict) -> dict:
    """Run Secure by Design assessment per CISA commitments and DoDI 5000.87."""
    assess = _import_tool("tools.compliance.sbd_assessor", "run_sbd_assessment")
    if not assess:
        return {"error": "sbd_assessor module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    domain = args.get("domain", "all")
    project_dir = args.get("project_dir")
    return assess(project_id, domain=domain, project_dir=project_dir, db_path=str(DB_PATH))


def handle_sbd_report(args: dict) -> dict:
    """Generate a Secure by Design assessment report."""
    generate = _import_tool("tools.compliance.sbd_report_generator", "generate_sbd_report")
    if not generate:
        return {"error": "sbd_report_generator module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    return generate(project_id, output_path=output_dir, db_path=str(DB_PATH))


# ---------------------------------------------------------------------------
# IV&V (Independent Verification & Validation) handlers
# ---------------------------------------------------------------------------


def handle_ivv_assess(args: dict) -> dict:
    """Run IV&V assessment per IEEE 1012."""
    assess = _import_tool("tools.compliance.ivv_assessor", "run_ivv_assessment")
    if not assess:
        return {"error": "ivv_assessor module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    process_area = args.get("process_area", "all")
    project_dir = args.get("project_dir")
    return assess(project_id, process_area=process_area, project_dir=project_dir, db_path=str(DB_PATH))


def handle_ivv_report(args: dict) -> dict:
    """Generate an IV&V certification report."""
    generate = _import_tool("tools.compliance.ivv_report_generator", "generate_ivv_report")
    if not generate:
        return {"error": "ivv_report_generator module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    output_dir = args.get("output_dir")
    return generate(project_id, output_path=output_dir, db_path=str(DB_PATH))


def handle_rtm_generate(args: dict) -> dict:
    """Generate a Requirements Traceability Matrix (RTM)."""
    generate = _import_tool("tools.compliance.traceability_matrix", "generate_rtm")
    if not generate:
        return {"error": "traceability_matrix module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    project_dir = args.get("project_dir")
    return generate(project_id, project_dir=project_dir, db_path=str(DB_PATH))


# ---------------------------------------------------------------------------
# Multi-Framework Compliance handlers (Phase 17)
# ---------------------------------------------------------------------------


def handle_crosswalk_query(args: dict) -> dict:
    """Query the control crosswalk engine for multi-framework mappings."""
    action = args.get("action", "frameworks_for_control")
    control_id = args.get("control_id")
    framework = args.get("framework")
    baseline = args.get("baseline")
    project_id = args.get("project_id")
    il_level = args.get("impact_level")

    if action == "frameworks_for_control":
        fn = _import_tool("tools.compliance.crosswalk_engine", "get_frameworks_for_control")
        if not fn:
            return {"error": "crosswalk_engine module not available yet", "status": "pending"}
        if not control_id:
            raise ValueError("'control_id' is required for frameworks_for_control")
        return {"control_id": control_id, "frameworks": fn(control_id)}

    elif action == "controls_for_framework":
        fn = _import_tool("tools.compliance.crosswalk_engine", "get_controls_for_framework")
        if not fn:
            return {"error": "crosswalk_engine module not available yet", "status": "pending"}
        if not framework:
            raise ValueError("'framework' is required for controls_for_framework")
        controls = fn(framework, baseline=baseline)
        return {"framework": framework, "baseline": baseline, "controls": controls, "count": len(controls)}

    elif action == "controls_for_impact_level":
        fn = _import_tool("tools.compliance.crosswalk_engine", "get_controls_for_impact_level")
        if not fn:
            return {"error": "crosswalk_engine module not available yet", "status": "pending"}
        if not il_level:
            raise ValueError("'impact_level' is required for controls_for_impact_level")
        controls = fn(il_level)
        return {"impact_level": il_level, "controls": controls, "count": len(controls)}

    elif action == "coverage":
        fn = _import_tool("tools.compliance.crosswalk_engine", "compute_crosswalk_coverage")
        if not fn:
            return {"error": "crosswalk_engine module not available yet", "status": "pending"}
        if not project_id:
            raise ValueError("'project_id' is required for coverage")
        return fn(project_id, db_path=str(DB_PATH))

    elif action == "gap_analysis":
        fn = _import_tool("tools.compliance.crosswalk_engine", "get_gap_analysis")
        if not fn:
            return {"error": "crosswalk_engine module not available yet", "status": "pending"}
        if not project_id or not framework:
            raise ValueError("'project_id' and 'framework' are required for gap_analysis")
        gaps = fn(project_id, framework, baseline=baseline, db_path=str(DB_PATH))
        return {"project_id": project_id, "framework": framework, "gaps": gaps, "gap_count": len(gaps)}

    return {"error": f"Unknown action: {action}"}


def handle_fedramp_assess(args: dict) -> dict:
    """Run FedRAMP assessment against a project."""
    assess = _import_tool("tools.compliance.fedramp_assessor", "run_fedramp_assessment")
    if not assess:
        return {"error": "fedramp_assessor module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    baseline = args.get("baseline", "moderate")
    project_dir = args.get("project_dir")
    return assess(project_id, baseline=baseline, project_dir=project_dir, db_path=str(DB_PATH))


def handle_fedramp_report(args: dict) -> dict:
    """Generate a FedRAMP assessment report."""
    generate = _import_tool("tools.compliance.fedramp_report_generator", "generate_fedramp_report")
    if not generate:
        return {"error": "fedramp_report_generator module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    baseline = args.get("baseline", "moderate")
    output_path = args.get("output_path")
    return generate(project_id, baseline=baseline, output_path=output_path, db_path=str(DB_PATH))


def handle_cmmc_assess(args: dict) -> dict:
    """Run CMMC assessment against a project."""
    assess = _import_tool("tools.compliance.cmmc_assessor", "run_cmmc_assessment")
    if not assess:
        return {"error": "cmmc_assessor module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    level = args.get("level", 2)
    project_dir = args.get("project_dir")
    return assess(project_id, level=level, project_dir=project_dir, db_path=str(DB_PATH))


def handle_cmmc_report(args: dict) -> dict:
    """Generate a CMMC assessment report."""
    generate = _import_tool("tools.compliance.cmmc_report_generator", "generate_cmmc_report")
    if not generate:
        return {"error": "cmmc_report_generator module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    level = args.get("level", 2)
    output_path = args.get("output_path")
    return generate(project_id, level=level, output_path=output_path, db_path=str(DB_PATH))


def handle_oscal_generate(args: dict) -> dict:
    """Generate OSCAL artifacts for a project."""
    artifact_type = args.get("artifact", "ssp")
    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    if artifact_type == "all":
        fn = _import_tool("tools.compliance.oscal_generator", "generate_all_oscal")
    else:
        fn_map = {
            "ssp": "generate_oscal_ssp",
            "poam": "generate_oscal_poam",
            "assessment_results": "generate_oscal_assessment_results",
            "assessment_plan": "generate_oscal_assessment_plan",
            "component_definition": "generate_oscal_component_definition",
        }
        func_name = fn_map.get(artifact_type, "generate_oscal_ssp")
        fn = _import_tool("tools.compliance.oscal_generator", func_name)

    if not fn:
        return {"error": "oscal_generator module not available yet", "status": "pending"}

    args.get("format", "json")
    return fn(project_id, db_path=str(DB_PATH))


def handle_emass_sync(args: dict) -> dict:
    """Sync project compliance data to eMASS."""
    sync = _import_tool("tools.compliance.emass.emass_sync", "sync_to_emass")
    if not sync:
        return {"error": "emass_sync module not available yet", "status": "pending"}

    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    mode = args.get("mode", "hybrid")
    return sync(project_id, mode=mode, db_path=str(DB_PATH))


def handle_cato_monitor(args: dict) -> dict:
    """Monitor cATO evidence freshness and readiness."""
    action = args.get("action", "readiness")
    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    if action == "check_freshness":
        fn = _import_tool("tools.compliance.cato_monitor", "check_evidence_freshness")
    elif action == "auto_reassess":
        fn = _import_tool("tools.compliance.cato_monitor", "auto_reassess")
    elif action == "dashboard":
        fn = _import_tool("tools.compliance.cato_monitor", "get_cato_dashboard_data")
    elif action == "expire":
        fn = _import_tool("tools.compliance.cato_monitor", "expire_old_evidence")
    else:
        fn = _import_tool("tools.compliance.cato_monitor", "compute_cato_readiness")

    if not fn:
        return {"error": "cato_monitor module not available yet", "status": "pending"}

    return fn(project_id, db_path=str(DB_PATH))


def handle_pi_compliance(args: dict) -> dict:
    """Track compliance across SAFe Program Increments."""
    action = args.get("action", "velocity")
    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")

    if action == "start":
        fn = _import_tool("tools.compliance.pi_compliance_tracker", "start_pi")
        if not fn:
            return {"error": "pi_compliance_tracker module not available yet", "status": "pending"}
        pi_number = args.get("pi_number")
        start_date = args.get("start_date")
        end_date = args.get("end_date")
        if not pi_number:
            raise ValueError("'pi_number' is required for start action")
        return fn(project_id, pi_number, start_date, end_date, db_path=str(DB_PATH))

    elif action == "record":
        fn = _import_tool("tools.compliance.pi_compliance_tracker", "record_pi_progress")
        if not fn:
            return {"error": "pi_compliance_tracker module not available yet", "status": "pending"}
        pi_number = args.get("pi_number")
        if not pi_number:
            raise ValueError("'pi_number' is required for record action")
        return fn(project_id, pi_number, db_path=str(DB_PATH))

    elif action == "close":
        fn = _import_tool("tools.compliance.pi_compliance_tracker", "close_pi")
        if not fn:
            return {"error": "pi_compliance_tracker module not available yet", "status": "pending"}
        pi_number = args.get("pi_number")
        if not pi_number:
            raise ValueError("'pi_number' is required for close action")
        return fn(project_id, pi_number, db_path=str(DB_PATH))

    elif action == "burndown":
        fn = _import_tool("tools.compliance.pi_compliance_tracker", "get_compliance_burndown")
        if not fn:
            return {"error": "pi_compliance_tracker module not available yet", "status": "pending"}
        return fn(project_id, db_path=str(DB_PATH))

    elif action == "report":
        fn = _import_tool("tools.compliance.pi_compliance_tracker", "generate_pi_compliance_report")
        if not fn:
            return {"error": "pi_compliance_tracker module not available yet", "status": "pending"}
        pi_number = args.get("pi_number")
        if not pi_number:
            raise ValueError("'pi_number' is required for report action")
        return fn(project_id, pi_number, db_path=str(DB_PATH))

    else:  # velocity
        fn = _import_tool("tools.compliance.pi_compliance_tracker", "get_pi_velocity")
        if not fn:
            return {"error": "pi_compliance_tracker module not available yet", "status": "pending"}
        return fn(project_id, db_path=str(DB_PATH))


def handle_classification_check(args: dict) -> dict:
    """Check and validate project classification markings."""
    action = args.get("action", "validate")
    project_id = args.get("project_id")
    il_level = args.get("impact_level")

    if action == "validate" and project_id:
        fn = _import_tool("tools.compliance.classification_manager", "validate_classification")
        if not fn:
            return {"error": "classification_manager module not available yet", "status": "pending"}
        return fn(project_id, db_path=str(DB_PATH))

    elif action == "banner":
        fn = _import_tool("tools.compliance.classification_manager", "get_marking_banner")
        if not fn:
            return {"error": "classification_manager module not available yet", "status": "pending"}
        classification = args.get("classification", "CUI")
        return {"banner": fn(classification)}

    elif action == "baseline" and il_level:
        fn = _import_tool("tools.compliance.classification_manager", "get_required_baseline")
        if not fn:
            return {"error": "classification_manager module not available yet", "status": "pending"}
        return fn(il_level)

    elif action == "profile" and il_level:
        fn = _import_tool("tools.compliance.classification_manager", "get_impact_level_profile")
        if not fn:
            return {"error": "classification_manager module not available yet", "status": "pending"}
        return fn(il_level)

    return {"error": "Provide 'project_id' for validate or 'impact_level' for baseline/profile"}


# ---------------------------------------------------------------------------
# FIPS 199/200 Security Categorization handlers (Phase 20)
# ---------------------------------------------------------------------------


def handle_fips199_categorize(args: dict) -> dict:
    """Run FIPS 199 security categorization."""
    action = args.get("action", "categorize")
    project_id = args.get("project_id")
    if not project_id and action != "list_catalog":
        raise ValueError("'project_id' is required")

    if action == "list_catalog":
        fn = _import_tool("tools.compliance.fips199_categorizer", "list_catalog")
        if not fn:
            return {"error": "fips199_categorizer not available"}
        return {"catalog": fn(category=args.get("category")), "status": "ok"}

    if action == "add_type":
        fn = _import_tool("tools.compliance.fips199_categorizer", "add_information_type")
        if not fn:
            return {"error": "fips199_categorizer not available"}
        return fn(
            project_id,
            args.get("type_id"),
            adjust_c=args.get("adjust_c"),
            adjust_i=args.get("adjust_i"),
            adjust_a=args.get("adjust_a"),
            adjustment_justification=args.get("justification"),
            db_path=str(DB_PATH),
        )

    if action == "remove_type":
        fn = _import_tool("tools.compliance.fips199_categorizer", "remove_information_type")
        if not fn:
            return {"error": "fips199_categorizer not available"}
        return fn(project_id, args.get("type_id"), db_path=str(DB_PATH))

    if action == "list_types":
        fn = _import_tool("tools.compliance.fips199_categorizer", "list_information_types")
        if not fn:
            return {"error": "fips199_categorizer not available"}
        return {"types": fn(project_id, db_path=str(DB_PATH))}

    if action == "get":
        fn = _import_tool("tools.compliance.fips199_categorizer", "get_categorization")
        if not fn:
            return {"error": "fips199_categorizer not available"}
        result = fn(project_id, db_path=str(DB_PATH))
        return result or {"project_id": project_id, "categorization": None}

    if action == "gate":
        fn = _import_tool("tools.compliance.fips199_categorizer", "evaluate_gate")
        if not fn:
            return {"error": "fips199_categorizer not available"}
        return fn(project_id, db_path=str(DB_PATH))

    # Default: categorize
    fn = _import_tool("tools.compliance.fips199_categorizer", "categorize_project")
    if not fn:
        return {"error": "fips199_categorizer not available"}
    return fn(
        project_id,
        method=args.get("method", "information_type"),
        manual_c=args.get("manual_c"),
        manual_i=args.get("manual_i"),
        manual_a=args.get("manual_a"),
        justification=args.get("justification"),
        db_path=str(DB_PATH),
    )


def handle_fips200_validate(args: dict) -> dict:
    """Validate FIPS 200 minimum security requirements."""
    fn = _import_tool("tools.compliance.fips200_validator", "validate_fips200")
    if not fn:
        return {"error": "fips200_validator not available"}
    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")
    return fn(project_id, project_dir=args.get("project_dir"), gate=args.get("gate", False), db_path=str(DB_PATH))


def handle_security_categorize(args: dict) -> dict:
    """Run full security categorization workflow (FIPS 199 + 200 + baseline)."""
    project_id = args.get("project_id")
    if not project_id:
        raise ValueError("'project_id' is required")
    cat_fn = _import_tool("tools.compliance.fips199_categorizer", "categorize_project")
    val_fn = _import_tool("tools.compliance.fips200_validator", "validate_fips200")
    result = {"project_id": project_id}
    if cat_fn:
        result["fips199"] = cat_fn(project_id, db_path=str(DB_PATH))
        result["baseline"] = result["fips199"].get("baseline")
    if val_fn:
        result["fips200"] = val_fn(project_id, db_path=str(DB_PATH))
    return result


# ---------------------------------------------------------------------------
# OSCAL Ecosystem handlers (D302-D306)
# ---------------------------------------------------------------------------


def handle_oscal_validate_deep(args: dict) -> dict:
    """Run 3-layer deep OSCAL validation (structural → pydantic → Metaschema)."""
    fn = _import_tool("tools.compliance.oscal_tools", "validate_oscal_deep")
    if not fn:
        return {"error": "oscal_tools module not available", "status": "pending"}
    file_path = args.get("file_path")
    if not file_path:
        raise ValueError("'file_path' is required")
    return fn(file_path, project_id=args.get("project_id"), db_path=str(DB_PATH))


def handle_oscal_convert(args: dict) -> dict:
    """Convert OSCAL artifact between JSON, XML, and YAML formats."""
    fn = _import_tool("tools.compliance.oscal_tools", "convert_oscal_format")
    if not fn:
        return {"error": "oscal_tools module not available", "status": "pending"}
    input_path = args.get("input_path")
    output_format = args.get("output_format", "xml")
    if not input_path:
        raise ValueError("'input_path' is required")
    return fn(input_path, output_format, output_path=args.get("output_path"))


def handle_oscal_resolve_profile(args: dict) -> dict:
    """Flatten an OSCAL Profile into a resolved Catalog via oscal-cli."""
    fn = _import_tool("tools.compliance.oscal_tools", "resolve_oscal_profile")
    if not fn:
        return {"error": "oscal_tools module not available", "status": "pending"}
    profile_path = args.get("profile_path")
    if not profile_path:
        raise ValueError("'profile_path' is required")
    return fn(profile_path, output_path=args.get("output_path"))


def handle_oscal_catalog_lookup(args: dict) -> dict:
    """Look up controls in the OSCAL catalog (official NIST or ICDEV™ fallback)."""
    control_id = args.get("control_id")
    family = args.get("family")

    if control_id:
        fn = _import_tool("tools.compliance.oscal_tools", "catalog_lookup")
        if not fn:
            return {"error": "oscal_tools module not available", "status": "pending"}
        result = fn(control_id)
        if result:
            return {"control": result}
        return {"error": f"Control '{control_id}' not found"}

    if family:
        fn = _import_tool("tools.compliance.oscal_tools", "catalog_list")
        if not fn:
            return {"error": "oscal_tools module not available", "status": "pending"}
        controls = fn(family=family)
        return {"family": family, "controls": controls, "count": len(controls)}

    fn = _import_tool("tools.compliance.oscal_tools", "catalog_stats")
    if not fn:
        return {"error": "oscal_tools module not available", "status": "pending"}
    return fn()


def handle_oscal_detect_tools(args: dict) -> dict:
    """Detect available OSCAL ecosystem tools (oscal-cli, oscal-pydantic, NIST catalog)."""
    fn = _import_tool("tools.compliance.oscal_tools", "detect_oscal_tools")
    if not fn:
        return {"error": "oscal_tools module not available", "status": "pending"}
    return fn()


# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------


def create_server() -> MCPServer:
    server = MCPServer(name="icdev-compliance", version="1.0.0")

    server.register_tool(
        name="nist_lookup",
        description="Look up NIST 800-53 Rev 5 security controls by control ID (e.g., AC-2) or list all controls in a family (e.g., AC, AU, SA).",
        input_schema={
            "type": "object",
            "properties": {
                "control_id": {"type": "string", "description": "Control ID to look up (e.g., AC-2, SA-11)"},
                "family": {
                    "type": "string",
                    "description": "Family code to list all controls (e.g., AC, AU, CM, IA, SA, SC)",
                },
            },
        },
        handler=handle_nist_lookup,
    )

    server.register_tool(
        name="ssp_generate",
        description="Generate a System Security Plan (SSP) document for a project with all 17 sections per NIST 800-53. Includes CUI markings.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory for SSP document (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_ssp_generate,
    )

    server.register_tool(
        name="poam_generate",
        description="Generate a Plan of Action & Milestones (POA&M) document from security scan findings with corrective actions and milestones.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory for POA&M (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_poam_generate,
    )

    server.register_tool(
        name="stig_check",
        description="Run STIG compliance checks against a project. Returns findings categorized as CAT1 (critical), CAT2 (high), CAT3 (medium).",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "stig_id": {
                    "type": "string",
                    "description": "STIG template to check against",
                    # Derived from the templates on disk, never hand-listed: the
                    # previous enum offered five profiles for one template, so
                    # four of the five raised FileNotFoundError (rmf-inert-01).
                    "enum": _available_stig_ids(),
                    "default": "webapp",
                },
                "gate": {
                    "type": "boolean",
                    "description": "Evaluate the security gate (0 CAT1 Open = pass)",
                    "default": False,
                },
            },
            "required": ["project_id"],
        },
        handler=handle_stig_check,
    )

    server.register_tool(
        name="sbom_generate",
        description="Generate a Software Bill of Materials (SBOM) in CycloneDX format. Lists all dependencies with versions and known vulnerabilities.",
        input_schema={
            "type": "object",
            "properties": {
                "project_dir": {"type": "string", "description": "Path to the project directory"},
                "project_id": {"type": "string", "description": "UUID of the project (optional, for DB recording)"},
            },
            "required": ["project_dir"],
        },
        handler=handle_sbom_generate,
    )

    server.register_tool(
        name="cui_mark",
        description=(
            "Apply CUI (Controlled Unclassified Information) markings to a file. "
            "Documents get top and bottom banners; source files get a comment header. "
            "The marking text comes from args/cui_markings.yaml, not from the caller."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to the file to mark"},
                "dry_run": {
                    "type": "boolean",
                    "description": "Report what would be marked without writing",
                    "default": False,
                },
            },
            "required": ["file_path"],
        },
        handler=handle_cui_mark,
    )

    server.register_tool(
        name="control_map",
        description=(
            "Record a project's implementation of a NIST 800-53 control "
            "(status, description, responsible role, evidence path). Upserts the "
            "mapping in project_controls."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "control_id": {
                    "type": "string",
                    "description": "NIST 800-53 control id (e.g. AC-2, AU-12)",
                },
                "implementation_status": {
                    "type": "string",
                    "description": "Implementation status of the control",
                    "enum": sorted(_control_mapper_statuses()),
                    "default": "planned",
                },
                "description": {
                    "type": "string",
                    "description": "How the control is implemented for this project",
                },
                "responsible_role": {
                    "type": "string",
                    "description": "Role accountable for the control",
                },
                "evidence_path": {"type": "string", "description": "Path to supporting evidence"},
            },
            "required": ["project_id", "control_id"],
        },
        handler=handle_control_map,
    )

    # --- CSSP Tools ---

    server.register_tool(
        name="cssp_assess",
        description="Run CSSP assessment per DoD Instruction 8530.01 against a project. Evaluates 5 functional areas: Identify, Protect, Detect, Respond, Sustain.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "functional_area": {
                    "type": "string",
                    "description": "Functional area to assess (default: all)",
                    "enum": ["all", "Identify", "Protect", "Detect", "Respond", "Sustain"],
                    "default": "all",
                },
                "gate": {
                    "type": "boolean",
                    "description": "Evaluate the CSSP gate (0 critical not_satisfied = pass)",
                    "default": False,
                },
            },
            "required": ["project_id"],
        },
        handler=handle_cssp_assess,
    )

    server.register_tool(
        name="cssp_report",
        description="Generate a CSSP certification report with functional area scores, evidence summary, and certification recommendation.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_cssp_report,
    )

    server.register_tool(
        name="cssp_ir_plan",
        description="Generate an Incident Response Plan per CSSP SOC requirements with escalation timelines and SOC coordination procedures.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_cssp_ir_plan,
    )

    server.register_tool(
        name="cssp_evidence",
        description="Collect and index evidence artifacts for CSSP assessment. Scans project for compliance artifacts and maps them to CSSP requirements.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "project_dir": {"type": "string", "description": "Project directory path (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_cssp_evidence,
    )

    # --- Xacta Integration ---

    server.register_tool(
        name="xacta_sync",
        description="Sync project compliance data to Xacta 360 (system of record for CSSP/ATO). Supports API, export, and hybrid modes.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "mode": {
                    "type": "string",
                    "description": "Sync mode",
                    "enum": ["api", "export", "hybrid"],
                    "default": "hybrid",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_xacta_sync,
    )

    server.register_tool(
        name="xacta_export",
        description="Generate Xacta 360-compatible export files (OSCAL JSON or CSV) for batch import.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "format": {
                    "type": "string",
                    "description": "Export format",
                    "enum": ["oscal", "csv", "all"],
                    "default": "oscal",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_xacta_export,
    )

    # --- SbD (Secure by Design) Tools ---

    server.register_tool(
        name="sbd_assess",
        description="Run Secure by Design assessment per CISA commitments and DoDI 5000.87 across 14 security domains.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project to assess"},
                "domain": {
                    "type": "string",
                    "enum": [
                        "all",
                        "Authentication",
                        "Memory Safety",
                        "Vulnerability Mgmt",
                        "Intrusion Evidence",
                        "Cryptography",
                        "Access Control",
                        "Input Handling",
                        "Error Handling",
                        "Supply Chain",
                        "Threat Modeling",
                        "Defense in Depth",
                        "Secure Defaults",
                        "CUI Compliance",
                        "DoD Software Assurance",
                    ],
                    "default": "all",
                    "description": "Domain to assess (default: all)",
                },
                "project_dir": {"type": "string", "description": "Project directory for file-based checks (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_sbd_assess,
    )

    server.register_tool(
        name="sbd_report",
        description="Generate a Secure by Design assessment report with domain scores and CISA commitment status.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_sbd_report,
    )

    # --- IV&V (Independent Verification & Validation) Tools ---

    server.register_tool(
        name="ivv_assess",
        description="Run IV&V assessment per IEEE 1012 across 9 process areas (verification + validation).",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project to assess"},
                "process_area": {
                    "type": "string",
                    "enum": [
                        "all",
                        "Requirements Verification",
                        "Design Verification",
                        "Code Verification",
                        "Test Verification",
                        "Integration Verification",
                        "Traceability Analysis",
                        "Security Verification",
                        "Build/Deploy Verification",
                        "Process Compliance",
                    ],
                    "default": "all",
                    "description": "Process area to assess (default: all)",
                },
                "project_dir": {"type": "string", "description": "Project directory for file-based checks (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_ivv_assess,
    )

    server.register_tool(
        name="ivv_report",
        description="Generate an IV&V certification report with verification/validation scores and certification recommendation.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "output_dir": {"type": "string", "description": "Output directory (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_ivv_report,
    )

    server.register_tool(
        name="rtm_generate",
        description="Generate a Requirements Traceability Matrix (RTM) linking requirements to design, code, and tests.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "project_dir": {"type": "string", "description": "Project directory to scan for artifacts"},
            },
            "required": ["project_id"],
        },
        handler=handle_rtm_generate,
    )

    # --- Multi-Framework Compliance Tools (Phase 17) ---

    server.register_tool(
        name="crosswalk_query",
        description="Query control crosswalk engine for multi-framework mappings (NIST 800-53 ↔ FedRAMP ↔ 800-171 ↔ CMMC). Actions: frameworks_for_control, controls_for_framework, controls_for_impact_level, coverage, gap_analysis.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "frameworks_for_control",
                        "controls_for_framework",
                        "controls_for_impact_level",
                        "coverage",
                        "gap_analysis",
                    ],
                    "default": "frameworks_for_control",
                    "description": "Query action to perform",
                },
                "control_id": {"type": "string", "description": "NIST 800-53 control ID (e.g., AC-2)"},
                "framework": {"type": "string", "description": "Target framework (fedramp, cmmc, 800-171)"},
                "baseline": {"type": "string", "description": "Framework baseline (moderate, high, l2, l3)"},
                "project_id": {"type": "string", "description": "UUID of the project (for coverage/gap_analysis)"},
                "impact_level": {"type": "string", "enum": ["IL4", "IL5", "IL6"], "description": "DoD Impact Level"},
            },
        },
        handler=handle_crosswalk_query,
    )

    server.register_tool(
        name="fedramp_assess",
        description="Run FedRAMP Moderate or High baseline security assessment against a project. Inherits NIST 800-53 implementations via crosswalk.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "baseline": {
                    "type": "string",
                    "enum": ["moderate", "high"],
                    "default": "moderate",
                    "description": "FedRAMP baseline to assess against",
                },
                "project_dir": {"type": "string", "description": "Project directory for auto-checks (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_fedramp_assess,
    )

    server.register_tool(
        name="fedramp_report",
        description="Generate a FedRAMP assessment report with control family scores, gap analysis, and readiness score.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "baseline": {"type": "string", "enum": ["moderate", "high"], "default": "moderate"},
                "output_path": {"type": "string", "description": "Output path for report (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_fedramp_report,
    )

    server.register_tool(
        name="cmmc_assess",
        description="Run CMMC Level 2 or Level 3 assessment against a project. Evaluates practices across 14 domains.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "level": {
                    "type": "integer",
                    "enum": [2, 3],
                    "default": 2,
                    "description": "CMMC level to assess",
                },
                "project_dir": {"type": "string", "description": "Project directory for auto-checks (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_cmmc_assess,
    )

    server.register_tool(
        name="cmmc_report",
        description="Generate a CMMC assessment report with domain scores, practice status, and NIST 800-171 cross-reference.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "level": {"type": "integer", "enum": [2, 3], "default": 2},
                "output_path": {"type": "string", "description": "Output path for report (optional)"},
            },
            "required": ["project_id"],
        },
        handler=handle_cmmc_report,
    )

    server.register_tool(
        name="oscal_generate",
        description="Generate NIST OSCAL 1.1.2 artifacts (SSP, POA&M, Assessment Results, Component Definition) in machine-readable format.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "artifact": {
                    "type": "string",
                    "enum": ["ssp", "poam", "assessment_results", "component_definition", "all"],
                    "default": "ssp",
                    "description": "OSCAL artifact type to generate",
                },
                "format": {
                    "type": "string",
                    "enum": ["json", "xml", "yaml"],
                    "default": "json",
                    "description": "Output format",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_oscal_generate,
    )

    server.register_tool(
        name="emass_sync",
        description="Sync project compliance data to eMASS (DoD system of record). Supports API, export, and hybrid modes.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "mode": {
                    "type": "string",
                    "enum": ["api", "export", "hybrid"],
                    "default": "hybrid",
                    "description": "Sync mode: api (REST), export (CSV/files), hybrid (try API, fall back to export)",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_emass_sync,
    )

    server.register_tool(
        name="cato_monitor",
        description="Monitor continuous ATO (cATO) evidence freshness and readiness. Actions: readiness, check_freshness, auto_reassess, dashboard, expire.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "action": {
                    "type": "string",
                    "enum": ["readiness", "check_freshness", "auto_reassess", "dashboard", "expire"],
                    "default": "readiness",
                    "description": "Monitoring action to perform",
                },
            },
            "required": ["project_id"],
        },
        handler=handle_cato_monitor,
    )

    server.register_tool(
        name="pi_compliance",
        description="Track compliance across SAFe Program Increments (PIs). Actions: start, record, close, velocity, burndown, report.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "UUID of the project"},
                "action": {
                    "type": "string",
                    "enum": ["start", "record", "close", "velocity", "burndown", "report"],
                    "default": "velocity",
                    "description": "PI tracking action",
                },
                "pi_number": {"type": "string", "description": "PI identifier (e.g., PI-24.1)"},
                "start_date": {"type": "string", "description": "PI start date (YYYY-MM-DD)"},
                "end_date": {"type": "string", "description": "PI end date (YYYY-MM-DD)"},
            },
            "required": ["project_id"],
        },
        handler=handle_pi_compliance,
    )

    server.register_tool(
        name="classification_check",
        description="Validate project classification markings, get marking banners, or query impact level baselines.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["validate", "banner", "baseline", "profile"],
                    "default": "validate",
                    "description": "Classification action",
                },
                "project_id": {"type": "string", "description": "UUID of the project (for validate)"},
                "impact_level": {
                    "type": "string",
                    "enum": ["IL4", "IL5", "IL6"],
                    "description": "Impact level (for baseline/profile)",
                },
                "classification": {"type": "string", "description": "Classification level (for banner)"},
            },
        },
        handler=handle_classification_check,
    )

    # --- FIPS 199/200 Security Categorization Tools (Phase 20) ---

    server.register_tool(
        name="fips199_categorize",
        description="FIPS 199 security categorization using NIST SP 800-60 information types. Actions: categorize, add_type, remove_type, list_types, list_catalog, get, gate.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "action": {
                    "type": "string",
                    "enum": ["categorize", "add_type", "remove_type", "list_types", "list_catalog", "get", "gate"],
                    "default": "categorize",
                },
                "type_id": {"type": "string", "description": "SP 800-60 info type ID (e.g., D.1.1.1)"},
                "method": {
                    "type": "string",
                    "enum": ["information_type", "manual", "cnssi_1253"],
                    "default": "information_type",
                },
                "manual_c": {"type": "string", "enum": ["Low", "Moderate", "High"]},
                "manual_i": {"type": "string", "enum": ["Low", "Moderate", "High"]},
                "manual_a": {"type": "string", "enum": ["Low", "Moderate", "High"]},
                "adjust_c": {"type": "string", "enum": ["Low", "Moderate", "High"]},
                "adjust_i": {"type": "string", "enum": ["Low", "Moderate", "High"]},
                "adjust_a": {"type": "string", "enum": ["Low", "Moderate", "High"]},
                "justification": {"type": "string"},
                "category": {"type": "string", "description": "Catalog filter (D.1, D.2, D.3)"},
            },
        },
        handler=handle_fips199_categorize,
    )

    server.register_tool(
        name="fips200_validate",
        description="Validate FIPS 200 minimum security requirements across all 17 areas.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
                "project_dir": {"type": "string"},
                "gate": {"type": "boolean", "default": False},
            },
            "required": ["project_id"],
        },
        handler=handle_fips200_validate,
    )

    server.register_tool(
        name="security_categorize",
        description="Full security categorization: FIPS 199 + FIPS 200 + baseline selection.",
        input_schema={
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Project UUID"},
            },
            "required": ["project_id"],
        },
        handler=handle_security_categorize,
    )

    # OSCAL Ecosystem tools (D302-D306)
    server.register_tool(
        name="oscal_validate_deep",
        description="Run 3-layer deep OSCAL validation: structural (built-in) → pydantic (if installed) → Metaschema (if oscal-cli + Java available). Each layer independently optional.",
        input_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to OSCAL JSON file to validate"},
                "project_id": {"type": "string", "description": "Project UUID (optional, for logging)"},
            },
            "required": ["file_path"],
        },
        handler=handle_oscal_validate_deep,
    )

    server.register_tool(
        name="oscal_convert",
        description="Convert OSCAL artifact between JSON, XML, and YAML formats using oscal-cli. Requires Java 11+ and oscal-cli JAR.",
        input_schema={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "Path to input OSCAL file"},
                "output_format": {
                    "type": "string",
                    "enum": ["json", "xml", "yaml"],
                    "default": "xml",
                    "description": "Target output format",
                },
                "output_path": {
                    "type": "string",
                    "description": "Output file path (optional, auto-generated if omitted)",
                },
            },
            "required": ["input_path"],
        },
        handler=handle_oscal_convert,
    )

    server.register_tool(
        name="oscal_resolve_profile",
        description="Flatten an OSCAL Profile into a resolved Catalog using oscal-cli profile resolution. Produces a full catalog of selected controls.",
        input_schema={
            "type": "object",
            "properties": {
                "profile_path": {"type": "string", "description": "Path to OSCAL Profile JSON file"},
                "output_path": {"type": "string", "description": "Output path for resolved catalog (optional)"},
            },
            "required": ["profile_path"],
        },
        handler=handle_oscal_resolve_profile,
    )

    server.register_tool(
        name="oscal_catalog_lookup",
        description="Look up NIST 800-53 controls from the OSCAL catalog. Uses official NIST OSCAL catalog (1000+ controls) with ICDEV™ fallback (39 controls).",
        input_schema={
            "type": "object",
            "properties": {
                "control_id": {"type": "string", "description": "Control ID to look up (e.g., AC-2, AC-2(1))"},
                "family": {"type": "string", "description": "Family code to list controls (e.g., AC, AU, SC)"},
            },
        },
        handler=handle_oscal_catalog_lookup,
    )

    server.register_tool(
        name="oscal_detect_tools",
        description="Detect available OSCAL ecosystem tools: oscal-cli (Java, Metaschema validation), oscal-pydantic (Python, type-safe models), NIST OSCAL catalog (1000+ controls).",
        input_schema={
            "type": "object",
            "properties": {},
        },
        handler=handle_oscal_detect_tools,
    )

    return server


if __name__ == "__main__":
    server = create_server()
    server.run()
