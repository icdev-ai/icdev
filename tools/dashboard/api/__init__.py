# CUI // SP-CTI
"""
tools/dashboard/api/__init__.py
================================
Centralises all Flask Blueprint registrations for the ICDEV™ dashboard.

Canonical URL namespace : /api/v1/<resource>
Legacy alias            : /api/<resource>   (kept for 1 release; deprecate next cycle)

Usage (from create_app):
    from tools.dashboard.api import register_api_blueprints
    register_api_blueprints(app)
"""
from __future__ import annotations

import os


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _v1_prefix(prefix: str) -> str:
    """Convert /api/<resource>  →  /api/v1/<resource>.

    Blueprints whose url_prefix already starts with /api/v or that don't
    start with /api/ are returned unchanged.
    """
    if prefix.startswith("/api/") and not prefix.startswith("/api/v"):
        return "/api/v1/" + prefix[5:]
    return prefix


def _dual(app, bp) -> None:
    """Register *bp* at its canonical /api/v1/… path AND at the legacy /api/… alias.

    The legacy registration uses a ``_legacy`` suffix on the Blueprint name to
    prevent Flask endpoint-name collisions while both namespaces are live.
    """
    orig: str = bp.url_prefix or ""
    v1 = _v1_prefix(orig)
    # Canonical ─ /api/v1/...
    app.register_blueprint(bp, url_prefix=v1)
    # Legacy alias ─ /api/... (different blueprint name avoids endpoint conflicts)
    app.register_blueprint(bp, url_prefix=orig, name=bp.name + "_legacy")
    app.logger.debug("API  %-40s  canonical=%s  legacy=%s", bp.name, v1, orig)


def _single(app, bp) -> None:
    """Register *bp* at its existing url_prefix with no /api/v1/ remapping.

    Used for:
    - Blueprints whose routes hard-code /api/... (no url_prefix set)
    - Non-API blueprints (e.g. /admin)
    """
    app.register_blueprint(bp)
    app.logger.debug("API  %-40s  prefix=%s", bp.name, bp.url_prefix or "(none)")


# ---------------------------------------------------------------------------
# Public registration entry-point
# ---------------------------------------------------------------------------

def register_api_blueprints(app) -> None:  # noqa: ANN001
    """Mount all dashboard API blueprints onto *app*.

    Blueprints that carry an explicit ``url_prefix="/api/<resource>"`` are
    registered twice:

    * ``/api/v1/<resource>``  — new canonical path
    * ``/api/<resource>``     — backward-compatible alias (1-release grace period)

    Blueprints whose routes hard-code the full ``/api/…`` path (no url_prefix)
    are registered once at their existing paths; they cannot be trivially
    remounted without modifying every route decorator.

    Conditional/optional blueprints are wrapped in try/except or gated by
    environment flags, mirroring the original app.py logic.
    """
    _airgap = os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")
    _govcon_enabled = (
        os.environ.get("ICDEV_GOVCON_ENABLED", "false").lower() == "true"
        and not _airgap
    )

    # ------------------------------------------------------------------
    # 1. Core blueprints — have url_prefix="/api/..." → dual-register
    # ------------------------------------------------------------------
    from tools.dashboard.api.projects import projects_api
    from tools.dashboard.api.kanban import kanban_api
    from tools.dashboard.api.agents import agents_api
    from tools.dashboard.api.compliance import compliance_api
    from tools.dashboard.api.poam import poam_api
    from tools.dashboard.api.audit import audit_api
    from tools.dashboard.api.metrics import metrics_api
    from tools.dashboard.api.batch import batch_api
    from tools.dashboard.api.diagrams import diagrams_api
    from tools.dashboard.api.activity import activity_api
    from tools.dashboard.api.usage import usage_api
    from tools.dashboard.api.traces import traces_api, provenance_api, xai_api
    from tools.dashboard.api.oscal import oscal_api
    from tools.dashboard.api.prod_audit import prod_audit_api
    from tools.dashboard.api.ai_transparency import ai_transparency_api
    from tools.dashboard.api.ai_accountability import ai_accountability_api
    from tools.dashboard.api.code_quality import code_quality_api
    from tools.dashboard.api.fedramp_20x import fedramp_20x_api
    from tools.dashboard.api.evidence import evidence_api
    from tools.dashboard.api.lineage import lineage_api
    from tools.dashboard.api.filesync import filesync_api
    from tools.dashboard.api.security_scan import security_scan_api
    from tools.dashboard.api.migration import migration_api
    from tools.dashboard.api.sbd import sbd_api
    from tools.dashboard.api.pr_intel import pr_intel_api
    from tools.dashboard.api.iac import iac_api
    from tools.dashboard.api.cato import cato_api
    from tools.dashboard.api.control_inheritance import control_inheritance_api
    from tools.dashboard.api.migration_cost import migration_cost_api
    from tools.dashboard.api.compliance_debt import compliance_debt_api
    from tools.dashboard.api.stig_manager import stig_manager_api
    from tools.dashboard.api.ato_package import ato_package_api
    from tools.dashboard.api.ndc_labs import ndc_labs_api
    from tools.dashboard.api.ndc_sops import ndc_sops_api
    from tools.dashboard.api.writeguard import writeguard_api
    from tools.dashboard.api.orchestration import orchestration_api
    from tools.dashboard.api.studio import studio_api

    _core_dual = [
        projects_api,
        kanban_api,
        agents_api,
        compliance_api,
        poam_api,
        audit_api,
        metrics_api,
        batch_api,
        diagrams_api,
        activity_api,
        usage_api,
        traces_api,
        provenance_api,
        xai_api,
        oscal_api,
        prod_audit_api,
        ai_transparency_api,
        ai_accountability_api,
        code_quality_api,
        fedramp_20x_api,
        evidence_api,
        lineage_api,
        filesync_api,
        security_scan_api,
        migration_api,
        sbd_api,
        pr_intel_api,
        iac_api,
        cato_api,
        control_inheritance_api,
        migration_cost_api,
        compliance_debt_api,
        stig_manager_api,
        ato_package_api,
        ndc_labs_api,
        ndc_sops_api,
        writeguard_api,
        orchestration_api,
        studio_api,
    ]

    for bp in _core_dual:
        _dual(app, bp)

    # ------------------------------------------------------------------
    # 2. Blueprints whose routes hard-code /api/... (no url_prefix set)
    #    → single-register at existing paths; /api/v1/ alias not possible
    #      without modifying the route decorators.
    # ------------------------------------------------------------------
    from tools.dashboard.api.events import events_bp
    from tools.dashboard.api.nlq import nlq_bp
    from tools.dashboard.api.kanban_plan import kanban_plan_api
    from tools.dashboard.api.cicd import cicd_api
    from tools.dashboard.api.intake import intake_api
    from tools.dashboard.api.analytics import analytics_api
    from tools.dashboard.api.oracle import oracle_api
    from tools.dashboard.api.canvas_projects import canvas_projects_api
    from tools.dashboard.api.sandbox import sandbox_api

    _core_single = [
        events_bp,
        nlq_bp,
        kanban_plan_api,
        cicd_api,
        intake_api,
        analytics_api,
        oracle_api,
        canvas_projects_api,
        sandbox_api,
    ]

    for bp in _core_single:
        _single(app, bp)

    # ------------------------------------------------------------------
    # 3. Non-API blueprint (url_prefix="/admin")
    # ------------------------------------------------------------------
    from tools.dashboard.api.admin import admin_api
    _single(app, admin_api)

    # ------------------------------------------------------------------
    # 4. Optional blueprints — dual-register when available
    # ------------------------------------------------------------------
    try:
        from tools.dashboard.api.finetune import finetune_api
        _dual(app, finetune_api)
    except ImportError:
        app.logger.debug("finetune_api not available — skipping")

    try:
        from tools.dashboard.api.chat import chat_api
        _dual(app, chat_api)
    except ImportError:
        app.logger.debug("chat_api not available — skipping")

    # ------------------------------------------------------------------
    # 5. GovCon suite — conditional on ICDEV_GOVCON_ENABLED + not air-gap
    # ------------------------------------------------------------------
    if _govcon_enabled:
        try:
            from tools.dashboard.api.proposals import proposals_api
            from tools.dashboard.api.govcon import govcon_api
            from tools.dashboard.api.cpmp import cpmp_api
            _dual(app, proposals_api)
            _dual(app, govcon_api)
            _dual(app, cpmp_api)
        except ImportError as exc:
            app.logger.warning("GovCon API import failed: %s", exc)

        try:
            from tools.dashboard.api.proposal_genesis import proposal_genesis_api
            _dual(app, proposal_genesis_api)
        except ImportError as exc:
            app.logger.debug("proposal_genesis_api not available: %s", exc)

    # ------------------------------------------------------------------
    # 6. Optional blueprints whose routes hard-code /api/... paths
    # ------------------------------------------------------------------
    try:
        from tools.dashboard.api.rag_eval import rag_eval_api
        _single(app, rag_eval_api)
    except ImportError:
        app.logger.debug("rag_eval_api not available — skipping")

    # ------------------------------------------------------------------
    # 7. SRE API — optional, may not be present in all deployments
    # ------------------------------------------------------------------
    try:
        from tools.dashboard.api.sre import sre_api
        _dual(app, sre_api)
        app.logger.info("SRE API registered at /api/v1/sre/ (alias /api/sre/)")
    except ImportError as exc:
        app.logger.warning("SRE API failed to register: %s", exc)
