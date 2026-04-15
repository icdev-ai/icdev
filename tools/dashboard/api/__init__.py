# CUI // SP-CTI
"""tools/dashboard/api — Centralized API Blueprint Registry.

P1.1: register_api_blueprints(app) mounts all 55+ blueprints under /api/v1/
and keeps /api/* aliases for one release cycle.

NIST 800-53: SA-11 (Developer Security Testing), CM-3 (Configuration Change Control)

Usage in app.py::create_app():
    from tools.dashboard.api import register_api_blueprints
    register_api_blueprints(app)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

logger = logging.getLogger("icdev.dashboard.api")

# ---------------------------------------------------------------------------
# Lazy imports — each blueprint is imported inside the function so that a
# missing optional module never breaks the whole registration sequence.
# ---------------------------------------------------------------------------


def register_api_blueprints(app: "Flask") -> None:  # noqa: C901
    """Register all ICDEV™ API blueprints on *app*.

    Mounting strategy
    -----------------
    * Blueprints that carry an explicit ``url_prefix`` (e.g. ``/api/projects``)
      are re-registered under ``/api/v1/<name>`` **and** kept at ``/api/<name>``
      as a backward-compat alias for one release cycle.
    * Blueprints whose routes are hardcoded with the full ``/api/…`` path
      (no ``url_prefix`` on the Blueprint object) are registered as-is now;
      they will be migrated to ``/api/v1/`` in the next iteration.
    * Optional blueprints (GovCon, FineTune, RAG-eval, SRE, Chat) are wrapped
      in try/except so missing dependencies never break startup.
    """
    # ------------------------------------------------------------------ #
    #  Helper — re-register a blueprint at /api/v1/<path> + /api/<path>   #
    # ------------------------------------------------------------------ #
    def _mount(bp, *, v1_prefix: str, legacy_prefix: str | None = None) -> None:
        """Register *bp* under /api/v1/<path> and keep the /api/<path> alias."""
        try:
            app.register_blueprint(bp, url_prefix=v1_prefix, name=f"{bp.name}_v1")
        except Exception as exc:
            logger.warning("Failed to mount %s at %s: %s", bp.name, v1_prefix, exc)
            return
        # Legacy alias — keep for one release
        alias = legacy_prefix or v1_prefix.replace("/api/v1/", "/api/", 1)
        try:
            app.register_blueprint(bp, url_prefix=alias, name=f"{bp.name}_legacy")
        except Exception as exc:
            logger.warning("Failed to mount legacy alias %s for %s: %s", alias, bp.name, exc)

    def _mount_inline(bp) -> None:
        """Register a blueprint whose routes are hardcoded with /api/… paths."""
        try:
            app.register_blueprint(bp)
        except Exception as exc:
            logger.warning("Failed to mount inline-route blueprint %s: %s", bp.name, exc)

    # ------------------------------------------------------------------ #
    #  Meta blueprint (OpenAPI spec + Swagger UI) \u2014 P1.2 / B4             #
    #  No /api/ legacy alias: this is a v1-only surface.                  #
    # ------------------------------------------------------------------ #
    try:
        from tools.dashboard.api.meta import meta_api
        app.register_blueprint(meta_api, url_prefix="/api/v1")
        logger.info("meta_api registered at /api/v1/openapi.json + /api/v1/docs")
    except Exception as exc:
        logger.warning("Failed to mount meta_api: %s", exc)

    # ------------------------------------------------------------------ #
    #  Core blueprints (explicit url_prefix)                              #
    # ------------------------------------------------------------------ #
    from tools.dashboard.api.projects import projects_api
    _mount(projects_api, v1_prefix="/api/v1/projects")

    from tools.dashboard.api.kanban import kanban_api
    _mount(kanban_api, v1_prefix="/api/v1/kanban")

    from tools.dashboard.api.kanban_plan import kanban_plan_api
    _mount_inline(kanban_plan_api)   # inline routes: /api/kanban/plans

    from tools.dashboard.api.agents import agents_api
    _mount(agents_api, v1_prefix="/api/v1/agents")

    from tools.dashboard.api.compliance import compliance_api
    _mount(compliance_api, v1_prefix="/api/v1/compliance")

    from tools.dashboard.api.poam import poam_api
    _mount(poam_api, v1_prefix="/api/v1/poam")

    from tools.dashboard.api.audit import audit_api
    _mount(audit_api, v1_prefix="/api/v1/audit")

    from tools.dashboard.api.metrics import metrics_api
    _mount(metrics_api, v1_prefix="/api/v1/metrics")

    from tools.dashboard.api.events import events_bp
    _mount_inline(events_bp)   # inline routes: /api/events/*

    from tools.dashboard.api.nlq import nlq_bp
    _mount_inline(nlq_bp)   # inline routes: /api/nlq/*

    from tools.dashboard.api.batch import batch_api
    _mount(batch_api, v1_prefix="/api/v1/batch")

    from tools.dashboard.api.diagrams import diagrams_api
    _mount(diagrams_api, v1_prefix="/api/v1/diagrams")

    from tools.dashboard.api.cicd import cicd_api
    _mount_inline(cicd_api)   # inline routes: /api/cicd/*

    from tools.dashboard.api.intake import intake_api
    _mount_inline(intake_api)   # inline routes: /api/intake/*

    from tools.dashboard.api.admin import admin_api
    _mount(admin_api, v1_prefix="/api/v1/admin", legacy_prefix="/admin")

    from tools.dashboard.api.activity import activity_api
    _mount(activity_api, v1_prefix="/api/v1/activity")

    from tools.dashboard.api.usage import usage_api
    _mount(usage_api, v1_prefix="/api/v1/usage")

    from tools.dashboard.api.traces import traces_api, provenance_api, xai_api
    _mount(traces_api, v1_prefix="/api/v1/traces")
    _mount(provenance_api, v1_prefix="/api/v1/provenance")
    _mount(xai_api, v1_prefix="/api/v1/xai")

    from tools.dashboard.api.oscal import oscal_api
    _mount(oscal_api, v1_prefix="/api/v1/oscal")

    from tools.dashboard.api.prod_audit import prod_audit_api
    _mount(prod_audit_api, v1_prefix="/api/v1/prod-audit")

    from tools.dashboard.api.ai_transparency import ai_transparency_api
    _mount(ai_transparency_api, v1_prefix="/api/v1/ai-transparency")

    from tools.dashboard.api.ai_accountability import ai_accountability_api
    _mount(ai_accountability_api, v1_prefix="/api/v1/ai-accountability")

    from tools.dashboard.api.code_quality import code_quality_api
    _mount(code_quality_api, v1_prefix="/api/v1/code-quality")

    from tools.dashboard.api.fedramp_20x import fedramp_20x_api
    _mount(fedramp_20x_api, v1_prefix="/api/v1/fedramp-20x")

    from tools.dashboard.api.evidence import evidence_api
    _mount(evidence_api, v1_prefix="/api/v1/evidence")

    from tools.dashboard.api.lineage import lineage_api
    _mount(lineage_api, v1_prefix="/api/v1/lineage")

    from tools.dashboard.api.filesync import filesync_api
    _mount(filesync_api, v1_prefix="/api/v1/filesync")

    from tools.dashboard.api.security_scan import security_scan_api
    _mount(security_scan_api, v1_prefix="/api/v1/security-scan")

    from tools.dashboard.api.migration import migration_api
    _mount(migration_api, v1_prefix="/api/v1/migration")

    from tools.dashboard.api.sbd import sbd_api
    _mount(sbd_api, v1_prefix="/api/v1/sbd")

    from tools.dashboard.api.pr_intel import pr_intel_api
    _mount(pr_intel_api, v1_prefix="/api/v1/pr-intel")

    from tools.dashboard.api.iac import iac_api
    _mount(iac_api, v1_prefix="/api/v1/iac")

    from tools.dashboard.api.cato import cato_api
    _mount(cato_api, v1_prefix="/api/v1/cato")

    from tools.dashboard.api.control_inheritance import control_inheritance_api
    _mount(control_inheritance_api, v1_prefix="/api/v1/control-inheritance")

    from tools.dashboard.api.migration_cost import migration_cost_api
    _mount(migration_cost_api, v1_prefix="/api/v1/migration-cost")

    from tools.dashboard.api.compliance_debt import compliance_debt_api
    _mount(compliance_debt_api, v1_prefix="/api/v1/compliance-debt")

    from tools.dashboard.api.stig_manager import stig_manager_api
    _mount(stig_manager_api, v1_prefix="/api/v1/stig-manager")

    from tools.dashboard.api.ato_package import ato_package_api
    _mount(ato_package_api, v1_prefix="/api/v1/ato-package")

    from tools.dashboard.api.oracle import oracle_api
    _mount_inline(oracle_api)   # inline routes: /api/oracle/*

    from tools.dashboard.api.sandbox import sandbox_api
    _mount_inline(sandbox_api)   # inline routes: /api/sandbox/*

    from tools.dashboard.api.analytics import analytics_api
    _mount_inline(analytics_api)   # inline routes: /api/analytics/*

    from tools.dashboard.api.ndc_labs import ndc_labs_api
    _mount(ndc_labs_api, v1_prefix="/api/v1/ndc/labs")

    from tools.dashboard.api.ndc_sops import ndc_sops_api
    _mount(ndc_sops_api, v1_prefix="/api/v1/ndc/sops")

    from tools.dashboard.api.canvas_projects import canvas_projects_api
    _mount_inline(canvas_projects_api)   # inline routes: /api/canvas-projects/*

    from tools.dashboard.api.writeguard import writeguard_api
    _mount(writeguard_api, v1_prefix="/api/v1/writeguard")

    from tools.dashboard.api.orchestration import orchestration_api
    _mount(orchestration_api, v1_prefix="/api/v1/orchestration")

    from tools.dashboard.api.studio import studio_api
    _mount(studio_api, v1_prefix="/api/v1/studio")

    # ------------------------------------------------------------------ #
    #  Optional blueprints — graceful skip on ImportError                 #
    # ------------------------------------------------------------------ #
    try:
        from tools.dashboard.api.finetune import finetune_api
        _mount(finetune_api, v1_prefix="/api/v1/finetune")
    except ImportError as exc:
        logger.debug("finetune_api skipped: %s", exc)

    try:
        from tools.dashboard.api.rag_eval import rag_eval_api
        _mount_inline(rag_eval_api)
    except ImportError as exc:
        logger.debug("rag_eval_api skipped: %s", exc)

    try:
        from tools.dashboard.api.sre import sre_api
        _mount(sre_api, v1_prefix="/api/v1/sre")
        logger.info("SRE API registered at /api/v1/sre/")
    except ImportError as exc:
        logger.warning("SRE API failed to register: %s", exc)

    try:
        from tools.dashboard.api.chat import chat_api
        _mount(chat_api, v1_prefix="/api/v1/chat")
    except ImportError as exc:
        logger.debug("chat_api skipped: %s", exc)

    # GovCon suite — opt-in via ICDEV_GOVCON_ENABLED=true
    import os as _os  # noqa: PLC0415
    _airgap = _os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")
    _govcon_enabled = (
        _os.environ.get("ICDEV_GOVCON_ENABLED", "false").lower() == "true"
        and not _airgap
    )
    if _govcon_enabled:
        try:
            from tools.dashboard.api.proposals import proposals_api
            from tools.dashboard.api.govcon import govcon_api
            from tools.dashboard.api.cpmp import cpmp_api
            _mount(proposals_api, v1_prefix="/api/v1/proposals")
            _mount(govcon_api, v1_prefix="/api/v1/govcon")
            _mount(cpmp_api, v1_prefix="/api/v1/cpmp")
        except ImportError as exc:
            logger.warning("GovCon APIs skipped: %s", exc)

        try:
            from tools.dashboard.api.proposal_genesis import proposal_genesis_api
            _mount(proposal_genesis_api, v1_prefix="/api/v1/proposal-genesis")
        except ImportError as exc:
            logger.debug("proposal_genesis_api skipped: %s", exc)

    logger.info("register_api_blueprints: all API blueprints mounted.")


# ---------------------------------------------------------------------------
# ALL_BLUEPRINTS — flat list of (blueprint_name, v1_prefix) for tooling
# ---------------------------------------------------------------------------

ALL_BLUEPRINTS = [
    # (blueprint_name, v1_prefix, is_optional)
    ("meta_api", "/api/v1", False),                       # openapi.json + /docs
    ("projects_api", "/api/v1/projects", False),
    ("kanban_api", "/api/v1/kanban", False),
    ("kanban_plan_api", "/api/v1/kanban/plans", False),   # inline routes
    ("agents_api", "/api/v1/agents", False),
    ("compliance_api", "/api/v1/compliance", False),
    ("poam_api", "/api/v1/poam", False),
    ("audit_api", "/api/v1/audit", False),
    ("metrics_api", "/api/v1/metrics", False),
    ("events_bp", "/api/v1/events", False),               # inline routes
    ("nlq_bp", "/api/v1/nlq", False),                     # inline routes
    ("batch_api", "/api/v1/batch", False),
    ("diagrams_api", "/api/v1/diagrams", False),
    ("cicd_api", "/api/v1/cicd", False),                  # inline routes
    ("intake_api", "/api/v1/intake", False),               # inline routes
    ("admin_api", "/api/v1/admin", False),
    ("activity_api", "/api/v1/activity", False),
    ("usage_api", "/api/v1/usage", False),
    ("traces_api", "/api/v1/traces", False),
    ("provenance_api", "/api/v1/provenance", False),
    ("xai_api", "/api/v1/xai", False),
    ("oscal_api", "/api/v1/oscal", False),
    ("prod_audit_api", "/api/v1/prod-audit", False),
    ("ai_transparency_api", "/api/v1/ai-transparency", False),
    ("ai_accountability_api", "/api/v1/ai-accountability", False),
    ("code_quality_api", "/api/v1/code-quality", False),
    ("fedramp_20x_api", "/api/v1/fedramp-20x", False),
    ("evidence_api", "/api/v1/evidence", False),
    ("lineage_api", "/api/v1/lineage", False),
    ("filesync_api", "/api/v1/filesync", False),
    ("security_scan_api", "/api/v1/security-scan", False),
    ("migration_api", "/api/v1/migration", False),
    ("sbd_api", "/api/v1/sbd", False),
    ("pr_intel_api", "/api/v1/pr-intel", False),
    ("iac_api", "/api/v1/iac", False),
    ("cato_api", "/api/v1/cato", False),
    ("control_inheritance_api", "/api/v1/control-inheritance", False),
    ("migration_cost_api", "/api/v1/migration-cost", False),
    ("compliance_debt_api", "/api/v1/compliance-debt", False),
    ("stig_manager_api", "/api/v1/stig-manager", False),
    ("ato_package_api", "/api/v1/ato-package", False),
    ("oracle_api", "/api/v1/oracle", False),              # inline routes
    ("sandbox_api", "/api/v1/sandbox", False),            # inline routes
    ("analytics_api", "/api/v1/analytics", False),        # inline routes
    ("ndc_labs_api", "/api/v1/ndc/labs", False),
    ("ndc_sops_api", "/api/v1/ndc/sops", False),
    ("canvas_projects_api", "/api/v1/canvas-projects", False),   # inline routes
    ("writeguard_api", "/api/v1/writeguard", False),
    ("orchestration_api", "/api/v1/orchestration", False),
    ("studio_api", "/api/v1/studio", False),
    # Optional
    ("finetune_api", "/api/v1/finetune", True),
    ("rag_eval_api", "/api/v1/rag-eval", True),           # inline routes
    ("sre_api", "/api/v1/sre", True),
    ("chat_api", "/api/v1/chat", True),
    # GovCon (opt-in)
    ("proposals_api", "/api/v1/proposals", True),
    ("govcon_api", "/api/v1/govcon", True),
    ("cpmp_api", "/api/v1/cpmp", True),
    ("proposal_genesis_api", "/api/v1/proposal-genesis", True),
]
