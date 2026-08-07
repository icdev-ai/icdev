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
from tools.logging.icdev_logger import get_logger

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask

logger = get_logger("icdev.dashboard.api")

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

    import sys as _di_sys
    def _dbg_bp(msg): print(f"[BP-REG] {msg}", file=_di_sys.stderr, flush=True)

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
    _dbg_bp("meta_api")
    try:
        from tools.dashboard.api.meta import meta_api
        app.register_blueprint(meta_api, url_prefix="/api/v1")
        logger.info("meta_api registered at /api/v1/openapi.json + /api/v1/docs")
    except Exception as exc:
        logger.warning("Failed to mount meta_api: %s", exc)

    _dbg_bp("auth_api")
    try:
        from tools.dashboard.api.auth import (
            auth_api,
            install_api_v1_auth_middleware,
            install_csrf_cookie_middleware,
        )
        app.register_blueprint(auth_api, url_prefix="/api/v1")
        install_csrf_cookie_middleware(app)
        install_api_v1_auth_middleware(app)
        logger.info(
            "auth_api registered at /api/v1/auth/token + /api/v1/auth/refresh; "
            "csrf cookie middleware + /api/v1/* JWT enforcement installed"
        )
    except Exception as exc:
        logger.warning("Failed to mount auth_api: %s", exc)

    _dbg_bp("core blueprints")
    _dbg_bp("projects_api")
    from tools.dashboard.api.projects import projects_api
    _mount(projects_api, v1_prefix="/api/v1/projects")

    _dbg_bp("kanban_api")
    from tools.dashboard.api.kanban import kanban_api
    _mount(kanban_api, v1_prefix="/api/v1/kanban")

    _dbg_bp("kanban_plan_api")
    from tools.dashboard.api.kanban_plan import kanban_plan_api
    _mount_inline(kanban_plan_api)   # inline routes: /api/kanban/plans

    _dbg_bp("agents_api")
    from tools.dashboard.api.agents import agents_api
    _mount(agents_api, v1_prefix="/api/v1/agents")

    _dbg_bp("compliance_api")
    from tools.dashboard.api.compliance import compliance_api
    _mount(compliance_api, v1_prefix="/api/v1/compliance")

    _dbg_bp("poam_api")
    from tools.dashboard.api.poam import poam_api
    _mount(poam_api, v1_prefix="/api/v1/poam")

    _dbg_bp("audit_api")
    from tools.dashboard.api.audit import audit_api
    _mount(audit_api, v1_prefix="/api/v1/audit")

    _dbg_bp("metrics_api")
    from tools.dashboard.api.metrics import metrics_api
    _mount(metrics_api, v1_prefix="/api/v1/metrics")

    _dbg_bp("events_bp")
    from tools.dashboard.api.events import events_bp
    _mount_inline(events_bp)

    _dbg_bp("nlq_bp")
    from tools.dashboard.api.nlq import nlq_bp
    _mount_inline(nlq_bp)

    _dbg_bp("batch_api")
    from tools.dashboard.api.batch import batch_api
    _mount(batch_api, v1_prefix="/api/v1/batch")

    _dbg_bp("diagrams_api")
    from tools.dashboard.api.diagrams import diagrams_api
    _mount(diagrams_api, v1_prefix="/api/v1/diagrams")

    _dbg_bp("cicd_api")
    from tools.dashboard.api.cicd import cicd_api
    _mount_inline(cicd_api)

    _dbg_bp("intake_api: import")
    from tools.dashboard.api.intake import intake_api
    _dbg_bp("intake_api: mount")
    _mount_inline(intake_api)
    _dbg_bp("intake_api: done")

    _dbg_bp("admin_api")
    from tools.dashboard.api.admin import admin_api
    _mount(admin_api, v1_prefix="/api/v1/admin", legacy_prefix="/admin")

    _dbg_bp("activity_api")
    from tools.dashboard.api.activity import activity_api
    _mount(activity_api, v1_prefix="/api/v1/activity")

    _dbg_bp("usage_api")
    from tools.dashboard.api.usage import usage_api
    _mount(usage_api, v1_prefix="/api/v1/usage")

    _dbg_bp("traces_api")
    from tools.dashboard.api.traces import traces_api, provenance_api, xai_api
    _mount(traces_api, v1_prefix="/api/v1/traces")
    _mount(provenance_api, v1_prefix="/api/v1/provenance")
    _mount(xai_api, v1_prefix="/api/v1/xai")

    # Runtime Performance — the runtime_invocations rollup behind the
    # /monitoring panel. Kept out of traces_api because it reads a different
    # table with a different fail-safe story (telemetry, not OTel spans).
    _dbg_bp("runtime_invocations_api")
    from tools.dashboard.api.runtime_invocations import runtime_invocations_api
    _mount(runtime_invocations_api, v1_prefix="/api/v1/runtime-invocations")

    # GovChain / blockchain provenance verification API — mounted at
    # /api/govchain-provenance so it no longer shares /api/provenance (and the
    # blueprint name) with the W3C PROV-AGENT provenance_api above.
    try:
        from tools.dashboard.pages.provenance import govchain_provenance_api
        _mount_inline(govchain_provenance_api)
        logger.info("govchain_provenance_api registered at /api/govchain-provenance/*")
    except Exception as exc:
        logger.warning("govchain_provenance_api skipped: %s", exc)

    from tools.dashboard.api.oscal import oscal_api
    _mount(oscal_api, v1_prefix="/api/v1/oscal")

    from tools.dashboard.api.prod_audit import prod_audit_api
    _mount(prod_audit_api, v1_prefix="/api/v1/prod-audit")

    _dbg_bp("ai_transparency_api")
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

    from tools.dashboard.api.ato_compliance import ato_compliance_api
    _mount(ato_compliance_api, v1_prefix="/api/v1/ato-compliance")

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

    from tools.dashboard.api.iqe import iqe_api
    _mount_inline(iqe_api)   # inline routes: /iqe, /iqe/run

    from tools.dashboard.api.canvas_projects import canvas_projects_api
    _mount_inline(canvas_projects_api)   # inline routes: /api/canvas-projects/*

    from tools.dashboard.api.il5 import il5_api
    _mount(il5_api, v1_prefix="/api/v1/il5", legacy_prefix="/api/il5")

    from tools.dashboard.api.writeguard import writeguard_api
    _mount(writeguard_api, v1_prefix="/api/v1/writeguard")

    from tools.dashboard.api.orchestration import orchestration_api
    _mount(orchestration_api, v1_prefix="/api/v1/orchestration")

    from tools.dashboard.api.genesis import genesis_api
    _mount(genesis_api, v1_prefix="/api/v1/genesis")

    from tools.dashboard.api.studio import studio_api
    _mount(studio_api, v1_prefix="/api/v1/studio")

    # Wire canvas_bus event sources onto the cross-canvas bus (dwo-evt-01-d5)
    try:
        from tools.studio.bus_subscriber import register as _register_studio_bus

        _register_studio_bus()
    except Exception as exc:
        logger.warning("studio bus subscriber registration skipped: %s", exc)

    try:
        from tools.dashboard.api.news import news_api
        _mount_inline(news_api)   # inline routes: /api/news/*
    except Exception as exc:
        logger.warning("news_api skipped: %s", exc)

    # Extracted from app.py inline routes (nav-misc-03) — paths unchanged.
    try:
        from tools.dashboard.api.pulse import pulse_api
        _mount_inline(pulse_api)   # inline routes: /pulse, /api/pulse/*
    except Exception as exc:
        logger.warning("pulse_api skipped: %s", exc)

    try:
        from tools.dashboard.api.research import research_api
        _mount_inline(research_api)   # inline routes: /api/research/*
    except Exception as exc:
        logger.warning("research_api skipped: %s", exc)

    try:
        from tools.dashboard.api.clawhub import clawhub_api
        _mount_inline(clawhub_api)   # inline routes: /api/clawhub/*
    except Exception as exc:
        logger.warning("clawhub_api skipped: %s", exc)

    try:
        from tools.fathomdesk.blueprint import fathomdesk_api
        _mount_inline(fathomdesk_api)   # inline routes: /fathomdesk/api/*
    except Exception as exc:
        logger.warning("fathomdesk_api skipped: %s", exc)

    try:
        from tools.dashboard.api.options import options_api
        _mount_inline(options_api)   # inline routes: /api/options/*
    except Exception as exc:
        logger.warning("options_api skipped: %s", exc)

    try:
        from tools.dashboard.api.quality_scores import quality_scores_api
        _mount_inline(quality_scores_api)   # inline routes: /api/quality-scores/*
    except Exception as exc:
        logger.warning("quality_scores_api skipped: %s", exc)

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
        from tools.knowledge_graph.blueprint import rag_kg_api
        _mount_inline(rag_kg_api)
    except ImportError as exc:
        logger.debug("rag_kg_api skipped: %s", exc)

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
        _os.environ.get("ICDEV_GOVCON_ENABLED", "false").lower() in ("true", "1", "yes")
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

    # ACE Co-Worker Engine API — /api/ace/* routes (ace_api_bp not in component registry)
    try:
        from tools.ace.blueprint import ace_api_bp
        _mount_inline(ace_api_bp)
        logger.info("ace_api_bp registered at /api/ace/")
    except Exception as exc:
        logger.warning("ace_api_bp skipped: %s", exc)

    # DataBridge feeds — /api/databridge/v1/<connector>/<table> (ctx-expose-05).
    # Cortex service-key (icdev_ctx_) auth is resolved by the central dashboard
    # auth hook; the blueprint enforces connector allowlist + scopes.
    try:
        from tools.dashboard.api.databridge_feeds import databridge_feeds_bp
        _mount_inline(databridge_feeds_bp)   # inline routes: /api/databridge/v1/*
        logger.info("databridge_feeds_bp registered at /api/databridge/v1/")
    except Exception as exc:
        logger.warning("databridge_feeds_bp skipped: %s", exc)

    # AISG blueprint is registered by the canvas loop in create_app() (_CANVAS_DEFS).
    # Registering it here too caused Flask 3.x "already registered for this blueprint"
    # errors — same object re-registered on the same Flask app with a different url_prefix.
    # The /api/explain/* routes live in the AISG blueprint and are served from the canvas
    # registration without needing a separate api/__init__.py mount.

    # HITL Workflow — opt-in via ICDEV_HITL_ENABLED=true
    try:
        from tools.workflow_hitl.blueprint import create_wf_blueprint
        wf_bp = create_wf_blueprint()
        _mount(wf_bp, v1_prefix="/api/v1/wf")
        logger.info("HITL Workflow API registered at /api/v1/wf/")
    except Exception as exc:
        logger.warning("HITL Workflow API skipped: %s", exc)

    # Safety Monitor — circuit breaker API at /safety/circuit-breaker
    try:
        from tools.dashboard.api.safety_monitor import safety_monitor_api
        _mount(safety_monitor_api, v1_prefix="/safety")
        logger.info("Safety Monitor API registered at /safety/")
    except Exception as exc:
        logger.warning("Safety Monitor API skipped: %s", exc)

    # JISE Portal feed — /api/v1/jise/{status,requirements,intelligence,compliance}
    try:
        from tools.dashboard.api.jise import jise_api
        _mount(jise_api, v1_prefix="/api/v1/jise")
        logger.info("JISE portal API registered at /api/v1/jise/")
    except Exception as exc:
        logger.warning("JISE portal API skipped: %s", exc)

    # Cross-Agency Transfer Audit API — NIST AU-2/AU-9 (append-only logging)
    try:
        from tools.dashboard.api.cross_agency_transfer import cross_agency_transfer_api
        _mount(cross_agency_transfer_api, v1_prefix="/api/v1/cross-agency-transfer")
        logger.info("cross_agency_transfer_api registered at /api/v1/cross-agency-transfer/")
    except Exception as exc:
        logger.warning("cross_agency_transfer_api skipped: %s", exc)

    _dbg_bp("ALL BLUEPRINTS MOUNTED")
    logger.info("register_api_blueprints: all API blueprints mounted.")

    # km-autoclose: sweep decomposed parents stuck before the auto-close hook
    try:
        from tools.kanban.state_machine import backfill_auto_close_parents
        from tools.db.storage import get_connection as _gc
        _conn = _gc()
        try:
            closed = backfill_auto_close_parents(_conn, actor="startup_backfill")
            if closed:
                logger.info("startup backfill auto-closed %d parent tasks: %s", len(closed), closed)
        finally:
            _conn.close()
    except Exception as _exc:
        logger.debug("startup auto-close backfill skipped: %s", _exc)


# ---------------------------------------------------------------------------
# ALL_BLUEPRINTS — flat list of (blueprint_name, v1_prefix) for tooling
# ---------------------------------------------------------------------------

ALL_BLUEPRINTS = [
    # (blueprint_name, v1_prefix, is_optional)
    ("meta_api", "/api/v1", False),                       # openapi.json + /docs
    ("auth_api", "/api/v1", False),                       # /auth/token + /auth/refresh
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
    ("runtime_invocations_api", "/api/v1/runtime-invocations", False),
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
    ("genesis_api", "/api/v1/genesis", False),
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
    ("jise_api", "/api/v1/jise", False),
    ("cross_agency_transfer_api", "/api/v1/cross-agency-transfer", False),
]
