#!/usr/bin/env python3
# CUI // SP-CTI
"""
ICDEV™ Web Dashboard - Flask Application
========================================
Provides a web interface for monitoring projects, agents, compliance,
and system health within the ICDEV™ framework.

Usage:
    python tools/dashboard/app.py [--port 5000] [--debug]
"""

import argparse
import json
import os  # noqa: F811 — needed directly (not just as _os)
import sys
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup  (so `tools.dashboard.config` is importable when run directly)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

from flask import Flask, render_template, jsonify, request as flask_request, g, session as flask_session, redirect, url_for  # noqa: E402

from tools.dashboard.config import (  # noqa: E402
    DB_PATH,
    CUI_BANNER_TOP,
    CUI_BANNER_BOTTOM,
    CUI_DESIGNATION,
    CUI_BANNER_ENABLED,
    DEFAULT_CLASSIFICATION,
    BYOK_ENABLED,
    PORT,
    DEBUG,
)
from tools.dashboard.auth import register_dashboard_auth, validate_api_key, log_auth_event  # noqa: E402
from tools.dashboard.websocket import init_socketio, get_socketio  # noqa: E402
from tools.dashboard.api.projects import projects_api  # noqa: E402
from tools.dashboard.api.kanban import kanban_api  # noqa: E402
from tools.dashboard.api.kanban_plan import kanban_plan_api  # noqa: E402
from tools.dashboard.api.agents import agents_api  # noqa: E402
from tools.dashboard.api.compliance import compliance_api  # noqa: E402
from tools.dashboard.api.audit import audit_api  # noqa: E402
from tools.dashboard.api.metrics import metrics_api  # noqa: E402
from tools.dashboard.api.events import events_bp  # noqa: E402
from tools.dashboard.api.nlq import nlq_bp  # noqa: E402
from tools.dashboard.api.batch import batch_api  # noqa: E402
from tools.dashboard.api.diagrams import diagrams_api  # noqa: E402
from tools.dashboard.api.cicd import cicd_api  # noqa: E402
from tools.dashboard.api.intake import intake_api  # noqa: E402
from tools.dashboard.api.admin import admin_api  # noqa: E402
from tools.dashboard.api.activity import activity_api  # noqa: E402
from tools.dashboard.api.usage import usage_api  # noqa: E402
from tools.dashboard.api.traces import traces_api, provenance_api, xai_api  # noqa: E402
from tools.dashboard.api.oscal import oscal_api  # noqa: E402
from tools.dashboard.api.prod_audit import prod_audit_api  # noqa: E402
from tools.dashboard.api.ai_transparency import ai_transparency_api  # noqa: E402
from tools.dashboard.api.ai_accountability import ai_accountability_api  # noqa: E402
from tools.dashboard.api.code_quality import code_quality_api  # noqa: E402
from tools.dashboard.api.fedramp_20x import fedramp_20x_api  # noqa: E402
from tools.dashboard.api.evidence import evidence_api  # noqa: E402
from tools.dashboard.api.lineage import lineage_api  # noqa: E402
from tools.dashboard.api.filesync import filesync_api  # noqa: E402
from tools.dashboard.api.security_scan import security_scan_api  # noqa: E402
from tools.dashboard.api.migration import migration_api  # noqa: E402
from tools.dashboard.api.sbd import sbd_api  # noqa: E402
from tools.dashboard.api.pr_intel import pr_intel_api  # noqa: E402
from tools.dashboard.api.iac import iac_api  # noqa: E402
from tools.dashboard.api.cato import cato_api  # noqa: E402
from tools.dashboard.api.control_inheritance import control_inheritance_api  # noqa: E402
from tools.dashboard.api.migration_cost import migration_cost_api  # noqa: E402
from tools.dashboard.api.compliance_debt import compliance_debt_api  # noqa: E402
from tools.dashboard.api.stig_manager import stig_manager_api  # noqa: E402
from tools.dashboard.api.ato_package import ato_package_api  # noqa: E402
from tools.dashboard.api.oracle import oracle_api  # noqa: E402
from tools.dashboard.api.analytics import analytics_api  # noqa: E402
from tools.dashboard.api.canvas_projects import canvas_projects_api  # noqa: E402
try:
    from tools.dashboard.api.finetune import finetune_api  # noqa: E402
    _HAS_FINETUNE_API = True
except ImportError:
    _HAS_FINETUNE_API = False
try:
    from tools.dashboard.api.rag_eval import rag_eval_api  # noqa: E402
    _HAS_RAG_EVAL_API = True
except ImportError:
    _HAS_RAG_EVAL_API = False
# Air-gap mode: hide cloud-dependent pages (Pulse, ClawHub, Genesis, GovCon, etc.)
_AIRGAP_MODE = os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")
# Pages disabled in air-gap mode (routes → friendly message instead of 404)
_AIRGAP_DISABLED_ROUTES = frozenset({
    "/pulse", "/clawhub", "/research", "/autoresearch",
    "/genesis", "/govcon", "/proposals", "/cpmp",
    "/proposal-genesis", "/leads", "/studio/marketplace",
    "/alphadesk",
})
# Network Design Canvas: feature-flagged, air-gap compatible
_NETWORK_ENABLED = os.environ.get("ICDEV_NETWORK_ENABLED", "true").lower() == "true"
_HAS_NETWORK = False
if _NETWORK_ENABLED:
    try:
        from tools.network.blueprint import create_network_blueprint
        _HAS_NETWORK = True
    except ImportError:
        _HAS_NETWORK = False
# Pipeline Design Canvas: feature-flagged, air-gap compatible
_PIPELINE_ENABLED = os.environ.get("ICDEV_PIPELINE_ENABLED", "true").lower() == "true"
_HAS_PIPELINE = False
if _PIPELINE_ENABLED:
    try:
        from tools.pipeline.blueprint import create_pipeline_blueprint
        _HAS_PIPELINE = True
    except ImportError:
        _HAS_PIPELINE = False
# Security Design Canvas: feature-flagged, air-gap compatible
_SECURITY_CANVAS_ENABLED = os.environ.get("ICDEV_SECURITY_ENABLED", "true").lower() in ("true", "1", "yes")
_HAS_SECURITY_CANVAS = False
if _SECURITY_CANVAS_ENABLED:
    try:
        from tools.security_canvas.blueprint import create_security_blueprint
        _HAS_SECURITY_CANVAS = True
    except ImportError:
        _HAS_SECURITY_CANVAS = False
# Infrastructure Design Canvas (IDC): feature-flagged
_INFRA_CANVAS_ENABLED = os.environ.get("ICDEV_INFRA_ENABLED", "true").lower() in ("true", "1", "yes")
_HAS_INFRA_CANVAS = False
if _INFRA_CANVAS_ENABLED:
    try:
        from tools.infra_canvas.blueprint import infra_bp  # noqa: E402
        _HAS_INFRA_CANVAS = True
    except ImportError:
        _HAS_INFRA_CANVAS = False
# Data Design Canvas (DDC): feature-flagged
_DATA_CANVAS_ENABLED = os.environ.get("ICDEV_DATA_CANVAS_ENABLED", "true").lower() in ("true", "1", "yes")
_HAS_DATA_CANVAS = False
if _DATA_CANVAS_ENABLED:
    try:
        from tools.data_canvas.blueprint import create_data_canvas_blueprint  # noqa: E402
        _HAS_DATA_CANVAS = True
    except ImportError:
        _HAS_DATA_CANVAS = False
# Boundary Design Canvas (BDC): feature-flagged
_BOUNDARY_CANVAS_ENABLED = os.environ.get("ICDEV_BOUNDARY_ENABLED", "true").lower() in ("true", "1", "yes")
_HAS_BOUNDARY_CANVAS = False
if _BOUNDARY_CANVAS_ENABLED:
    try:
        from tools.boundary_canvas.blueprint import create_boundary_blueprint  # noqa: E402
        _HAS_BOUNDARY_CANVAS = True
    except ImportError:
        _HAS_BOUNDARY_CANVAS = False
# Observability Design Canvas (ODC): feature-flagged
_OBSERVABILITY_CANVAS_ENABLED = os.environ.get("ICDEV_OBSERVABILITY_ENABLED", "true").lower() in ("true", "1", "yes")
_HAS_OBSERVABILITY_CANVAS = False
if _OBSERVABILITY_CANVAS_ENABLED:
    try:
        from tools.observability_canvas.blueprint import create_observability_blueprint  # noqa: E402
        _HAS_OBSERVABILITY_CANVAS = True
    except ImportError:
        _HAS_OBSERVABILITY_CANVAS = False
# D-CHILD-6: GovProposal/CPMP/GovCon conditionally loaded
_GOVCON_ENABLED = os.environ.get("ICDEV_GOVCON_ENABLED", "true").lower() == "true"
_HAS_GOVCON = False
if _GOVCON_ENABLED:
    try:
        from tools.dashboard.api.proposals import proposals_api  # noqa: E402
        from tools.dashboard.api.govcon import govcon_api  # noqa: E402
        from tools.dashboard.api.cpmp import cpmp_api  # noqa: E402
        _HAS_GOVCON = True
    except ImportError:
        _HAS_GOVCON = False
    try:
        from tools.dashboard.api.proposal_genesis import proposal_genesis_api  # noqa: E402
        _HAS_PROPOSAL_GENESIS = True
    except ImportError:
        _HAS_PROPOSAL_GENESIS = False
else:
    _HAS_PROPOSAL_GENESIS = False
from tools.dashboard.api.orchestration import orchestration_api  # noqa: E402
try:
    from tools.dashboard.api.chat import chat_api  # noqa: E402
    _HAS_CHAT_API = True
except ImportError:
    _HAS_CHAT_API = False
from tools.dashboard.ux_helpers import register_ux_filters  # noqa: E402
from tools.dashboard.api.studio import studio_api  # noqa: E402

# ---------------------------------------------------------------------------
# GovCon/CPMP/Proposals page registration (D-CHILD-6: isolated)
# ---------------------------------------------------------------------------


def _register_govcon_pages(app: "Flask", _get_db):
    """Register GovProposal/CPMP/GovCon SSR page routes on the Flask app.

    Called only when _HAS_GOVCON is True.  Extracted from create_app() so that
    child apps (and parent apps with ICDEV_GOVCON_ENABLED=false) never register
    these routes.
    """

    @app.route("/cpmp")
    def cpmp_portfolio_page():
        """CPMP Portfolio — contract performance overview, health scoring."""
        try:
            from tools.govcon.portfolio_manager import get_portfolio_summary
            portfolio_data = get_portfolio_summary()
            pf = portfolio_data.get("portfolio", {})
            contracts = pf.get("contracts", [])
            upcoming = pf.get("upcoming_deliverables", [])
            portfolio = {
                "total_contracts": pf.get("total_contracts", 0),
                "active_contracts": pf.get("active_contracts", 0),
                "total_value": pf.get("total_value", 0),
                "burn_rate": pf.get("burn_rate_pct", 0),
                "overdue_deliverables": pf.get("overdue_deliverables", 0),
                "at_risk": pf.get("at_risk_contracts", 0),
                "health_distribution": pf.get("health_distribution", {"green": 0, "yellow": 0, "red": 0}),
            }
            return render_template("cpmp/portfolio.html", portfolio=portfolio, contracts=contracts, upcoming_deliverables=upcoming)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return render_template("cpmp/portfolio.html", portfolio={"total_contracts": 0, "active_contracts": 0, "total_value": 0, "burn_rate": 0, "overdue_deliverables": 0, "health_distribution": {"green": 0, "yellow": 0, "red": 0}}, contracts=[], upcoming_deliverables=[], error=str(e))

    @app.route("/cpmp/<contract_id>")
    def cpmp_detail_page(contract_id):
        """CPMP Contract Detail — 7-tab view."""
        try:
            from tools.govcon.contract_manager import get_contract, list_clins, list_wbs, list_deliverables
            contract_result = get_contract(contract_id)
            if contract_result.get("status") == "error":
                return render_template("404.html", message="Contract not found"), 404
            contract = contract_result.get("contract", contract_result)
            clins = list_clins(contract_id).get("clins", [])
            wbs_elements = list_wbs(contract_id).get("wbs_elements", [])
            deliverables = list_deliverables(contract_id).get("deliverables", [])
            try:
                from tools.govcon.subcontractor_tracker import list_subcontractors
                subcontractors = list_subcontractors(contract_id).get("subcontractors", [])
            except Exception:
                subcontractors = []
            try:
                from tools.govcon.evm_engine import aggregate_contract_evm
                evm = aggregate_contract_evm(contract_id)
                if "indicators" in evm and isinstance(evm["indicators"], dict):
                    evm.update(evm["indicators"])
            except Exception:
                evm = {}
            try:
                from tools.govcon.cpars_predictor import predict_cpars, list_assessments
                cpars_prediction = predict_cpars(contract_id)
                if "dimension_scores" in cpars_prediction:
                    cpars_prediction["dimensions"] = {
                        k: round(v * 5, 2) for k, v in cpars_prediction["dimension_scores"].items()
                    }
                cpars_assessments = list_assessments(contract_id).get("assessments", [])
            except Exception:
                cpars_prediction = {}
                cpars_assessments = []
            return render_template("cpmp/detail.html",
                                   contract=contract, clins=clins, wbs_elements=wbs_elements,
                                   deliverables=deliverables, subcontractors=subcontractors,
                                   evm=evm, cpars_prediction=cpars_prediction,
                                   cpars_assessments=cpars_assessments)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return render_template("404.html", message=f"Error loading contract: {e}"), 500

    @app.route("/cpmp/<contract_id>/deliverables/<deliverable_id>")
    def cpmp_deliverable_detail_page(contract_id, deliverable_id):
        """CPMP Deliverable Detail — status pipeline, CDRL generation."""
        try:
            from tools.govcon.contract_manager import get_contract, get_deliverable
            contract_result = get_contract(contract_id)
            contract = contract_result.get("contract", contract_result) if contract_result.get("status") == "ok" else {}
            deliv_result = get_deliverable(deliverable_id)
            if deliv_result.get("status") == "error":
                return render_template("404.html", message="Deliverable not found"), 404
            deliverable = deliv_result.get("deliverable", deliv_result)
            generations = deliverable.get("generations", []) if isinstance(deliverable, dict) else []
            status_history = deliverable.get("status_history", []) if isinstance(deliverable, dict) else []
            return render_template("cpmp/deliverable_detail.html",
                                   contract=contract, deliverable=deliverable,
                                   generations=generations, status_history=status_history)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return render_template("404.html", message=f"Error loading deliverable: {e}"), 500

    @app.route("/cpmp/cor")
    def cpmp_cor_portal_page():
        """COR Portal — read-only government view of assigned contracts."""
        user = getattr(g, "current_user", None)
        cor_email = user.get("email", "") if user else ""
        conn = _get_db()
        try:
            if cor_email:
                rows = conn.execute(
                    "SELECT * FROM cpmp_contracts WHERE cor_email = ? ORDER BY created_at DESC",
                    (cor_email,),
                ).fetchall()
                contracts = [dict(r) for r in rows]
            else:
                contracts = []
            return render_template("cpmp/cor_portal.html", contracts=contracts, cor_email=cor_email)
        except Exception:
            import traceback
            traceback.print_exc()
            return render_template("cpmp/cor_portal.html", contracts=[], cor_email=cor_email)
        finally:
            conn.close()

    @app.route("/cpmp/cor/<contract_id>")
    def cpmp_cor_detail_page(contract_id):
        """COR Contract Detail — read-only, no internal cost data."""
        user = getattr(g, "current_user", None)
        cor_email = user.get("email", "") if user else ""
        conn = _get_db()
        try:
            from tools.govcon.contract_manager import get_contract, list_deliverables
            contract_result = get_contract(contract_id)
            if contract_result.get("status") == "error":
                return render_template("404.html", message="Contract not found"), 404
            contract = contract_result.get("contract", contract_result)
            deliverables = list_deliverables(contract_id).get("deliverables", [])
            try:
                from tools.govcon.evm_engine import aggregate_contract_evm
                evm = aggregate_contract_evm(contract_id)
                if "indicators" in evm and isinstance(evm["indicators"], dict):
                    evm.update(evm["indicators"])
                if "total_bac" in evm:
                    evm.setdefault("bac", evm["total_bac"])
                if "total_pv" in evm:
                    evm.setdefault("pv", evm["total_pv"])
                if "total_ev" in evm:
                    evm.setdefault("ev", evm["total_ev"])
                if "percent_complete" in evm:
                    evm.setdefault("percent_complete_schedule", evm["percent_complete"] / 100 if evm["percent_complete"] > 1 else evm["percent_complete"])
            except Exception:
                evm = {}
            try:
                from tools.govcon.cpars_predictor import list_assessments
                cpars_assessments = list_assessments(contract_id).get("assessments", [])
            except Exception:
                cpars_assessments = []
            try:
                conn.execute(
                    "INSERT INTO cpmp_cor_access_log (id, user_id, contract_id, action, accessed_at, classification) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(uuid.uuid4()), cor_email, contract_id, "view_contract", datetime.now(timezone.utc).isoformat(), DEFAULT_CLASSIFICATION),
                )
                conn.commit()
            except Exception:
                pass
            return render_template("cpmp/cor_detail.html",
                                   contract=contract, deliverables=deliverables,
                                   evm=evm, cpars_assessments=cpars_assessments, cor_email=cor_email)
        except Exception as e:
            return render_template("404.html", message=f"Error: {e}"), 500
        finally:
            conn.close()

    @app.route("/proposals")
    def proposals_list_page():
        """Proposal Opportunities — GovCon proposal writing lifecycle tracker."""
        conn = _get_db()
        try:
            rows = conn.execute("SELECT * FROM proposal_opportunities ORDER BY due_date ASC").fetchall()
            opportunities = [dict(r) for r in rows]
            from datetime import date
            today = date.today()
            nearest_deadline = None
            for opp in opportunities:
                if opp.get("due_date") and opp["status"] not in ("submitted", "won", "lost", "cancelled", "no_bid"):
                    try:
                        dd = date.fromisoformat(opp["due_date"])
                        days_left = (dd - today).days
                        opp["days_left"] = days_left
                        if nearest_deadline is None or days_left < nearest_deadline:
                            nearest_deadline = days_left
                    except (ValueError, TypeError):
                        opp["days_left"] = None
                else:
                    opp["days_left"] = None
            return render_template("proposals/list.html", opportunities=opportunities, nearest_deadline=nearest_deadline)
        finally:
            conn.close()

    @app.route("/proposals/<opp_id>")
    def proposals_detail_page(opp_id):
        """Proposal Opportunity Detail — 6-tab view with sections, compliance, reviews."""
        conn = _get_db()
        try:
            opp = conn.execute("SELECT * FROM proposal_opportunities WHERE id = ?", (opp_id,)).fetchone()
            if not opp:
                return render_template("404.html", message="Opportunity not found"), 404
            opp = dict(opp)
            sections = [dict(r) for r in conn.execute(
                """SELECT s.*, v.volume_number, v.title as volume_title
                   FROM proposal_sections s
                   LEFT JOIN proposal_volumes v ON s.volume_id = v.id
                   WHERE s.opportunity_id = ?
                   ORDER BY v.volume_number, s.section_number""", (opp_id,)
            ).fetchall()]
            from datetime import date
            today = date.today()
            for s in sections:
                s["overdue"] = False
                if s.get("due_date") and s["status"] not in ("final", "submitted"):
                    try:
                        s["overdue"] = date.fromisoformat(s["due_date"]) < today
                    except (ValueError, TypeError):
                        pass
            volumes = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_volumes WHERE opportunity_id = ? ORDER BY volume_number", (opp_id,)
            ).fetchall()]
            compliance_items = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_compliance_matrix WHERE opportunity_id = ?", (opp_id,)
            ).fetchall()]
            reviews = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_reviews WHERE opportunity_id = ? ORDER BY scheduled_date", (opp_id,)
            ).fetchall()]
            findings = [dict(r) for r in conn.execute(
                """SELECT f.*, r.review_type FROM proposal_review_findings f
                   JOIN proposal_reviews r ON f.review_id = r.id
                   WHERE r.opportunity_id = ?""", (opp_id,)
            ).fetchall()]
            total_sections = len(sections)
            completed_sections = len([s for s in sections if s["status"] in ("final", "submitted")])
            total_compliance = len(compliance_items)
            compliant_count = len([c for c in compliance_items if c.get("compliance_status") == "compliant"])
            coverage_pct = (compliant_count / total_compliance * 100) if total_compliance > 0 else 0
            open_findings = len([f for f in findings if f.get("status") in ("open", "in_progress")])
            critical_findings = len([f for f in findings if f.get("severity") == "critical" and f.get("status") in ("open", "in_progress")])
            section_status_dist = {}
            for s in sections:
                st = s.get("status", "not_started")
                section_status_dist[st] = section_status_dist.get(st, 0) + 1
            finding_severity_dist = {}
            for f in findings:
                if f.get("status") in ("open", "in_progress"):
                    sev = f.get("severity", "minor")
                    finding_severity_dist[sev] = finding_severity_dist.get(sev, 0) + 1
            cm_compliant = len([c for c in compliance_items if c.get("compliance_status") == "compliant"])
            cm_partial = len([c for c in compliance_items if c.get("compliance_status") == "partial"])
            cm_non_compliant = len([c for c in compliance_items if c.get("compliance_status") == "non_compliant"])
            cm_not_addressed = len([c for c in compliance_items if c.get("compliance_status") == "not_addressed"])
            cm_not_applicable = len([c for c in compliance_items if c.get("compliance_status") == "not_applicable"])
            cm_gap_pct = round(cm_not_addressed / total_compliance * 100) if total_compliance > 0 else 0
            compliance_stats = {
                "total": total_compliance, "compliant": cm_compliant, "partial": cm_partial,
                "non_compliant": cm_non_compliant, "not_addressed": cm_not_addressed,
                "not_applicable": cm_not_applicable, "gap_pct": cm_gap_pct,
            }
            findings_by_review = {}
            for f in findings:
                rid = f.get("review_id")
                if rid:
                    findings_by_review.setdefault(rid, []).append(f)
            reviews_data = []
            for rev in reviews:
                rd = dict(rev)
                rd["findings"] = findings_by_review.get(rev["id"], [])
                reviews_data.append(rd)
            days_left = None
            if opp.get("due_date"):
                try:
                    days_left = (date.fromisoformat(opp["due_date"]) - today).days
                except (ValueError, TypeError):
                    pass
            stats = {
                "sections_total": total_sections, "sections_complete": completed_sections,
                "compliance_coverage_pct": round(coverage_pct), "open_findings": open_findings,
                "critical_findings": critical_findings, "section_status_distribution": section_status_dist,
                "finding_severity_distribution": finding_severity_dist,
            }
            questions = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_questions WHERE opportunity_id = ? ORDER BY question_number ASC", (opp_id,),
            ).fetchall()]
            question_stats = {
                "total": len(questions),
                "high_priority": len([q for q in questions if q.get("priority") == "high"]),
                "draft": len([q for q in questions if q.get("status") == "draft"]),
                "approved": len([q for q in questions if q.get("status") == "approved"]),
                "submitted": len([q for q in questions if q.get("status") == "submitted"]),
                "answered": len([q for q in questions if q.get("status") == "answered"]),
            }
            questions_days_left = None
            if opp.get("questions_due_date"):
                try:
                    questions_days_left = (date.fromisoformat(opp["questions_due_date"]) - today).days
                except (ValueError, TypeError):
                    pass
            amendments = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_amendments WHERE opportunity_id = ? ORDER BY version_number ASC", (opp_id,),
            ).fetchall()]
            responses = {}
            for q in questions:
                if q.get("status") == "answered":
                    resp = conn.execute(
                        "SELECT * FROM proposal_question_responses WHERE question_id = ? ORDER BY created_at DESC LIMIT 1",
                        (q["id"],),
                    ).fetchone()
                    if resp:
                        responses[q["id"]] = dict(resp)
            return render_template("proposals/detail.html",
                opp=opp, sections=sections, volumes=volumes,
                compliance_items=compliance_items, reviews=reviews_data, findings=findings,
                stats=stats, compliance_stats=compliance_stats,
                reviews_data=reviews_data, days_left=days_left,
                questions=questions, question_stats=question_stats,
                questions_days_left=questions_days_left,
                amendments=amendments, responses=responses)
        finally:
            conn.close()

    @app.route("/proposals/<opp_id>/sections/<sec_id>")
    def proposals_section_detail_page(opp_id, sec_id):
        """Proposal Section Detail — status pipeline, notes, compliance, findings, history."""
        conn = _get_db()
        try:
            section = conn.execute(
                """SELECT s.*, v.volume_number, v.title as volume_title
                   FROM proposal_sections s
                   LEFT JOIN proposal_volumes v ON s.volume_id = v.id
                   WHERE s.id = ? AND s.opportunity_id = ?""",
                (sec_id, opp_id)).fetchone()
            if not section:
                return render_template("404.html", message="Section not found"), 404
            section = dict(section)
            opp = conn.execute("SELECT title FROM proposal_opportunities WHERE id = ?", (opp_id,)).fetchone()
            opp_title = opp["title"] if opp else "Unknown"
            from tools.dashboard.api.proposals import SECTION_TRANSITIONS
            section["valid_transitions"] = SECTION_TRANSITIONS.get(section["status"], [])
            from datetime import date
            section["overdue"] = False
            if section.get("due_date") and section["status"] not in ("final", "submitted"):
                try:
                    section["overdue"] = date.fromisoformat(section["due_date"]) < date.today()
                except (ValueError, TypeError):
                    pass
            section["compliance_items"] = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_compliance_matrix WHERE proposal_section_id = ?", (sec_id,)
            ).fetchall()]
            section["findings"] = [dict(r) for r in conn.execute(
                """SELECT f.*, r.review_type FROM proposal_review_findings f
                   JOIN proposal_reviews r ON f.review_id = r.id
                   WHERE f.section_id = ?""", (sec_id,)
            ).fetchall()]
            deps = conn.execute(
                """SELECT d.*, s.title as depends_on_title, s.status as depends_on_status
                   FROM proposal_section_dependencies d
                   JOIN proposal_sections s ON d.depends_on_section_id = s.id
                   WHERE d.section_id = ?""", (sec_id,)
            ).fetchall()
            dep_list = []
            for d in deps:
                d = dict(d)
                from tools.dashboard.api.proposals import SECTION_STATUS_ORDER
                req_idx = SECTION_STATUS_ORDER.index(d["required_status"]) if d["required_status"] in SECTION_STATUS_ORDER else 0
                cur_idx = SECTION_STATUS_ORDER.index(d["depends_on_status"]) if d["depends_on_status"] in SECTION_STATUS_ORDER else 0
                d["met"] = cur_idx >= req_idx
                dep_list.append(d)
            section["dependencies"] = dep_list
            section["history"] = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_status_history WHERE entity_id = ? ORDER BY created_at DESC", (sec_id,)
            ).fetchall()]
            return render_template("proposals/section_detail.html", section=section, opp_title=opp_title)
        finally:
            conn.close()

    @app.route("/govcon")
    def govcon_pipeline_page():
        """GovCon Intelligence — pipeline status, recent opportunities, domain distribution."""
        conn = _get_db()
        try:
            from tools.govcon.govcon_engine import get_status
            stats = get_status()
            try:
                opps = conn.execute("SELECT * FROM sam_gov_opportunities ORDER BY posted_date DESC LIMIT 25").fetchall()
                opportunities = [dict(r) for r in opps]
            except Exception:
                opportunities = []
            linked_opp_ids = set()
            try:
                linked = conn.execute("SELECT sam_gov_opportunity_id FROM proposal_opportunities WHERE sam_gov_opportunity_id IS NOT NULL").fetchall()
                linked_opp_ids = {r["sam_gov_opportunity_id"] for r in linked}
            except Exception:
                pass
            return render_template("govcon/pipeline.html", stats=stats, opportunities=opportunities, linked_opp_ids=linked_opp_ids)
        except Exception:
            stats = {"total_opportunities": 0, "total_requirements": 0, "total_patterns": 0,
                     "total_capability_maps": 0, "total_drafts": 0, "total_awards": 0,
                     "knowledge_blocks": 0, "linked_proposals": 0, "domain_distribution": {},
                     "last_pipeline_run": None}
            return render_template("govcon/pipeline.html", stats=stats, opportunities=[], linked_opp_ids=set())
        finally:
            conn.close()

    @app.route("/govcon/requirements")
    def govcon_requirements_page():
        """GovCon Requirements — pattern frequency, domain heatmap, statement types."""
        conn = _get_db()
        try:
            total_requirements = 0
            try:
                r = conn.execute("SELECT COUNT(*) as cnt FROM rfp_shall_statements").fetchone()
                total_requirements = r["cnt"] if r else 0
            except Exception:
                pass
            total_patterns = 0
            try:
                r = conn.execute("SELECT COUNT(*) as cnt FROM rfp_requirement_patterns").fetchone()
                total_patterns = r["cnt"] if r else 0
            except Exception:
                pass
            domain_stats = {}
            try:
                rows = conn.execute("SELECT domain_category, COUNT(*) as cnt FROM rfp_shall_statements GROUP BY domain_category ORDER BY cnt DESC").fetchall()
                domain_stats = {r["domain_category"]: {"count": r["cnt"]} for r in rows}
            except Exception:
                pass
            domain_count = len(domain_stats)
            patterns = []
            min_frequency = 3
            try:
                rows = conn.execute("SELECT * FROM rfp_requirement_patterns WHERE frequency >= ? ORDER BY frequency DESC LIMIT 30", (min_frequency,)).fetchall()
                patterns = [dict(r) for r in rows]
            except Exception:
                pass
            top_frequency = patterns[0]["frequency"] if patterns else 0
            type_stats = {}
            try:
                rows = conn.execute("SELECT statement_type, COUNT(*) as cnt FROM rfp_shall_statements GROUP BY statement_type ORDER BY cnt DESC").fetchall()
                type_stats = {r["statement_type"]: r["cnt"] for r in rows}
            except Exception:
                pass
            return render_template("govcon/requirements.html",
                total_requirements=total_requirements, total_patterns=total_patterns,
                domain_stats=domain_stats, domain_count=domain_count,
                patterns=patterns, top_frequency=top_frequency,
                type_stats=type_stats, min_frequency=min_frequency)
        finally:
            conn.close()

    @app.route("/govcon/capabilities")
    def govcon_capabilities_page():
        """GovCon Capabilities — coverage by domain, gap list, enhancement recommendations."""
        conn = _get_db()
        try:
            coverage = {"L": 0, "M": 0, "N": 0, "rate": 0}
            try:
                rows = conn.execute(
                    """SELECT
                        SUM(CASE WHEN m.coverage_score >= 0.80 THEN 1 ELSE 0 END) as L,
                        SUM(CASE WHEN m.coverage_score >= 0.40 AND m.coverage_score < 0.80 THEN 1 ELSE 0 END) as M,
                        SUM(CASE WHEN m.coverage_score < 0.40 OR m.coverage_score IS NULL THEN 1 ELSE 0 END) as N,
                        COUNT(*) as total
                    FROM rfp_shall_statements s
                    LEFT JOIN icdev_capability_map m ON s.id = m.pattern_id"""
                ).fetchone()
                if rows and rows["total"] > 0:
                    coverage["L"] = rows["L"] or 0
                    coverage["M"] = rows["M"] or 0
                    coverage["N"] = rows["N"] or 0
                    coverage["rate"] = round(coverage["L"] / rows["total"] * 100)
            except Exception:
                pass
            domain_coverage = []
            try:
                rows = conn.execute(
                    """SELECT s.domain_category as domain, COUNT(*) as total,
                        SUM(CASE WHEN m.coverage_score >= 0.80 THEN 1 ELSE 0 END) as L,
                        SUM(CASE WHEN m.coverage_score >= 0.40 AND m.coverage_score < 0.80 THEN 1 ELSE 0 END) as M,
                        SUM(CASE WHEN m.coverage_score < 0.40 OR m.coverage_score IS NULL THEN 1 ELSE 0 END) as N
                    FROM rfp_shall_statements s
                    LEFT JOIN icdev_capability_map m ON s.id = m.pattern_id
                    GROUP BY s.domain_category ORDER BY total DESC"""
                ).fetchall()
                domain_coverage = [dict(r) for r in rows]
            except Exception:
                pass
            gaps = []
            total_gaps = 0
            try:
                rows = conn.execute(
                    """SELECT p.pattern_name as requirement, p.domain_category as domain,
                        p.frequency, COALESCE(m.coverage_score, 0) as coverage,
                        p.frequency * (1 - COALESCE(m.coverage_score, 0)) as priority
                    FROM rfp_requirement_patterns p
                    LEFT JOIN icdev_capability_map m ON p.id = m.pattern_id
                    WHERE COALESCE(m.coverage_score, 0) < 0.40
                    ORDER BY priority DESC LIMIT 20"""
                ).fetchall()
                gaps = [dict(r) for r in rows]
                total_gaps = len(gaps)
            except Exception:
                pass
            recommendations = []
            try:
                from tools.govcon.gap_analyzer import generate_recommendations
                rec_result = generate_recommendations()
                recommendations = rec_result.get("recommendations", [])[:15]
            except Exception:
                pass
            return render_template("govcon/capabilities.html",
                coverage=coverage, domain_coverage=domain_coverage,
                gaps=gaps, total_gaps=total_gaps,
                recommendations=recommendations)
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _module_not_installed(slug: str):
    """Return a friendly error when a marketplace module is not installed."""
    return jsonify({"error": f"Module '{slug}' is not installed. Install via marketplace."}), 501


def require_installed(slug):
    """Route decorator — catches ImportError when module code is missing."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except ImportError as exc:
                if slug in str(exc) or f"tools.{slug}" in str(exc) or f"tools/{slug}" in str(exc):
                    return _module_not_installed(slug)
                raise
        return wrapper
    return decorator


# App factory
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )

    # Auto-reload templates on change (no server restart needed)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.jinja_env.auto_reload = True

    # Register UX filters (glossary, timestamps, error recovery, quick paths)
    register_ux_filters(app)

    # Register dashboard auth middleware (D169-D172)
    register_dashboard_auth(app)

    # Initialize WebSocket (D170 — optional, graceful fallback)
    init_socketio(app)

    # Correlation ID middleware (D149)
    try:
        from tools.resilience.correlation import register_correlation_middleware
        register_correlation_middleware(app)
    except ImportError:
        pass

    # Role-based view configuration
    ROLE_VIEWS = {
        "pm": {
            "label": "Program Manager",
            "show_tabs": ["overview", "compliance", "deployments", "audit"],
            "hide_columns": ["stig_id", "finding_id"],
        },
        "developer": {
            "label": "Developer / Architect",
            "show_tabs": ["overview", "security", "deployments", "audit"],
            "hide_columns": [],
        },
        "isso": {
            "label": "ISSO / Security Officer",
            "show_tabs": ["overview", "compliance", "security", "audit"],
            "hide_columns": [],
        },
        "co": {
            "label": "Contracting Officer",
            "show_tabs": ["overview", "compliance", "deployments"],
            "hide_columns": ["stig_id", "finding_id", "source"],
        },
        "analyst": {
            "label": "Analyst",
            "show_tabs": ["overview", "compliance", "security", "audit"],
            "hide_columns": [],
        },
        "solutions_architect": {
            "label": "Solutions Architect",
            "show_tabs": ["overview", "security", "deployments", "audit"],
            "hide_columns": [],
        },
        "sales_engineer": {
            "label": "Sales Engineer",
            "show_tabs": ["overview", "compliance", "deployments"],
            "hide_columns": ["stig_id", "finding_id"],
        },
        "innovator": {
            "label": "Innovator",
            "show_tabs": ["overview", "security", "deployments", "audit"],
            "hide_columns": [],
        },
        "biz_dev": {
            "label": "Business Development",
            "show_tabs": ["overview", "compliance", "deployments"],
            "hide_columns": ["stig_id", "finding_id", "source"],
        },
        "cor": {
            "label": "Contracting Officer Representative",
            "show_tabs": ["overview", "compliance"],
            "hide_columns": ["stig_id", "finding_id", "source", "internal_cost_details"],
        },
    }

    # Make CUI config, role, and user info available in all templates
    @app.context_processor
    def inject_cui():
        role = flask_request.args.get("role", "")
        role_config = ROLE_VIEWS.get(role, None)
        current_user = getattr(g, "current_user", None)
        # Route-to-module map for assistant widget auto-scoping (D-CA-4)
        try:
            from tools.dashboard.assistant_config import ROUTE_MODULE_MAP
            _route_map = ROUTE_MODULE_MAP
        except ImportError:
            _route_map = {}

        return {
            "cui_banner_top": CUI_BANNER_TOP,
            "cui_banner_bottom": CUI_BANNER_BOTTOM,
            "cui_banner_enabled": CUI_BANNER_ENABLED,
            "cui_designation": CUI_DESIGNATION,
            "current_role": role,
            "role_config": role_config,
            "ROLE_VIEWS": ROLE_VIEWS,
            "current_user": current_user,
            "byok_enabled": BYOK_ENABLED,
            "govcon_enabled": _HAS_GOVCON and not _AIRGAP_MODE,
            "network_enabled": _HAS_NETWORK,
            "pipeline_enabled": _HAS_PIPELINE,
            "security_canvas_enabled": _HAS_SECURITY_CANVAS,
            "infra_canvas_enabled": _HAS_INFRA_CANVAS,
            "data_canvas_enabled": _HAS_DATA_CANVAS,
            "boundary_canvas_enabled": _HAS_BOUNDARY_CANVAS,
            "observability_canvas_enabled": _HAS_OBSERVABILITY_CANVAS,
            "airgap_mode": _AIRGAP_MODE,
            "route_module_map": _route_map,
        }

    # ---- Air-gap route guard: friendly message for disabled pages ----
    if _AIRGAP_MODE:
        @app.before_request
        def _airgap_route_guard():
            path = flask_request.path.rstrip("/") or "/"
            # Check exact match or prefix match for nested routes
            for disabled in _AIRGAP_DISABLED_ROUTES:
                if path == disabled or path.startswith(disabled + "/"):
                    if flask_request.is_json or path.startswith("/api/"):
                        return jsonify({
                            "error": "unavailable",
                            "message": "This feature is not available in air-gap mode.",
                        }), 503
                    return render_template(
                        "airgap_unavailable.html",
                        feature_name=disabled.strip("/").replace("-", " ").title(),
                    ), 200

    # ---- Auto-register A2A agents from card files ----
    try:
        from tools.a2a.agent_registry import register_all_from_cards
        registered = register_all_from_cards()
        if registered:
            app.logger.info("Auto-registered %d agents from card files", len(registered))
    except Exception as exc:
        app.logger.debug("Agent auto-registration skipped: %s", exc)

    # ---- Register API blueprints ----
    app.register_blueprint(projects_api)
    app.register_blueprint(kanban_api)
    app.register_blueprint(kanban_plan_api)
    app.register_blueprint(agents_api)
    app.register_blueprint(compliance_api)
    app.register_blueprint(audit_api)
    app.register_blueprint(metrics_api)
    app.register_blueprint(events_bp)
    app.register_blueprint(nlq_bp)
    app.register_blueprint(batch_api)
    app.register_blueprint(diagrams_api)
    app.register_blueprint(cicd_api)
    app.register_blueprint(intake_api)
    app.register_blueprint(admin_api)
    app.register_blueprint(activity_api)
    app.register_blueprint(usage_api)
    app.register_blueprint(traces_api)
    app.register_blueprint(provenance_api)
    app.register_blueprint(xai_api)
    app.register_blueprint(oscal_api)
    app.register_blueprint(prod_audit_api)
    app.register_blueprint(ai_transparency_api)
    app.register_blueprint(ai_accountability_api)
    app.register_blueprint(code_quality_api)
    app.register_blueprint(fedramp_20x_api)
    app.register_blueprint(evidence_api)
    app.register_blueprint(lineage_api)
    app.register_blueprint(filesync_api)
    app.register_blueprint(security_scan_api)
    app.register_blueprint(migration_api)
    app.register_blueprint(sbd_api)
    app.register_blueprint(pr_intel_api)
    app.register_blueprint(iac_api)
    app.register_blueprint(cato_api)
    app.register_blueprint(control_inheritance_api)
    app.register_blueprint(migration_cost_api)
    app.register_blueprint(compliance_debt_api)
    app.register_blueprint(stig_manager_api)
    app.register_blueprint(ato_package_api)
    app.register_blueprint(oracle_api)
    app.register_blueprint(analytics_api)
    app.register_blueprint(canvas_projects_api)
    if _HAS_FINETUNE_API:
        app.register_blueprint(finetune_api)
    if _HAS_RAG_EVAL_API:
        app.register_blueprint(rag_eval_api)
    if _HAS_GOVCON:
        app.register_blueprint(proposals_api)
        app.register_blueprint(govcon_api)
        app.register_blueprint(cpmp_api)
    if _HAS_PROPOSAL_GENESIS:
        app.register_blueprint(proposal_genesis_api)
    app.register_blueprint(orchestration_api)
    if _HAS_CHAT_API:
        app.register_blueprint(chat_api)
    app.register_blueprint(studio_api)

    # ---- SRE API Blueprint ----
    try:
        from tools.dashboard.api.sre import sre_api
        app.register_blueprint(sre_api)
        app.logger.info("SRE API registered at /api/sre/")
    except ImportError as exc:
        app.logger.warning("SRE API failed to register: %s", exc)

    # ---- SRE Dashboard Page ----
    @app.route("/sre")
    def sre_dashboard_page():
        return render_template("sre/dashboard.html")

    # ---- Network Design Canvas Blueprint ----
    if _HAS_NETWORK:
        try:
            nc_bp = create_network_blueprint()
            if nc_bp:
                app.register_blueprint(nc_bp, url_prefix="/network")
                app.logger.info("Network Design Canvas registered at /network/")
        except Exception as exc:
            app.logger.warning("Network Design Canvas failed to register: %s", exc)

    # ---- Pipeline Design Canvas Blueprint ----
    if _HAS_PIPELINE:
        try:
            pc_bp = create_pipeline_blueprint()
            if pc_bp:
                app.register_blueprint(pc_bp, url_prefix="/devops")
                app.logger.info("Pipeline Design Canvas registered at /devops/")
        except Exception as exc:
            app.logger.warning("Pipeline Design Canvas failed to register: %s", exc)

    # ---- Security Design Canvas Blueprint ----
    if _HAS_SECURITY_CANVAS:
        try:
            sc_bp = create_security_blueprint()
            if sc_bp:
                app.register_blueprint(sc_bp, url_prefix="/security")
                app.logger.info("Security Design Canvas registered at /security/")
        except Exception as exc:
            app.logger.warning("Security Design Canvas failed to register: %s", exc)

    # ---- Infrastructure Design Canvas Blueprint ----
    if _HAS_INFRA_CANVAS:
        try:
            app.register_blueprint(infra_bp)
            app.logger.info("Infrastructure Design Canvas registered at /infra/")
        except Exception as exc:
            app.logger.warning("Infrastructure Design Canvas failed to register: %s", exc)

    # ---- Data Design Canvas Blueprint ----
    if _HAS_DATA_CANVAS:
        try:
            dd_bp = create_data_canvas_blueprint()
            if dd_bp:
                app.register_blueprint(dd_bp, url_prefix="/data")
                app.logger.info("Data Design Canvas registered at /data/")
        except Exception as exc:
            app.logger.warning("Data Design Canvas failed to register: %s", exc)

    # ---- Boundary Design Canvas Blueprint ----
    if _HAS_BOUNDARY_CANVAS:
        try:
            bd_bp = create_boundary_blueprint()
            if bd_bp:
                app.register_blueprint(bd_bp, url_prefix="/boundary")
                app.logger.info("Boundary Design Canvas registered at /boundary/")
        except Exception as exc:
            app.logger.warning("Boundary Design Canvas failed to register: %s", exc)

    # ---- Observability Design Canvas Blueprint ----
    if _HAS_OBSERVABILITY_CANVAS:
        try:
            od_bp = create_observability_blueprint()
            if od_bp:
                app.register_blueprint(od_bp, url_prefix="/observability")
                app.logger.info("Observability Design Canvas registered at /observability/")
        except Exception as exc:
            app.logger.warning("Observability Design Canvas failed to register: %s", exc)

    # ---- Unified Canvas Compliance Dashboard ----
    @app.route("/canvas-compliance")
    def canvas_compliance_page():
        """Unified compliance posture across all 7 design canvases."""
        return render_template("canvas_compliance.html")

    # ---- Convenience JSON routes that match the spec ----

    @app.route("/api/alerts", methods=["GET"])
    def api_alerts_shortcut():
        """Shortcut: GET /api/alerts -> delegates to metrics alerts."""
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            return jsonify({"alerts": [dict(r) for r in rows], "total": len(rows)})
        finally:
            conn.close()

    @app.route("/api/notifications", methods=["GET"])
    def api_notifications():
        """Return current notification-worthy items (firing alerts, overdue POAMs)."""
        conn = _get_db()
        try:
            notifications = []
            firing = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE status = 'firing'"
            ).fetchone()["cnt"]
            if firing > 0:
                notifications.append({
                    "type": "error",
                    "message": f"{firing} alert{'s' if firing > 1 else ''} currently firing",
                    "link": "/monitoring",
                })
            open_poam = conn.execute(
                "SELECT COUNT(*) as cnt FROM poam_items WHERE status = 'open'"
            ).fetchone()["cnt"]
            if open_poam > 5:
                notifications.append({
                    "type": "warning",
                    "message": f"{open_poam} open POA&M items need attention",
                    "link": "/projects",
                })
            inactive = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents WHERE status != 'active'"
            ).fetchone()["cnt"]
            if inactive > 0:
                notifications.append({
                    "type": "info",
                    "message": f"{inactive} agent{'s' if inactive > 1 else ''} inactive",
                    "link": "/agents",
                })
            return jsonify({"notifications": notifications})
        finally:
            conn.close()

    @app.route("/api/charts/overview", methods=["GET"])
    def api_charts_overview():
        """Aggregate chart data for the home dashboard."""
        import sqlite3 as _sqlite3

        conn = _get_db()
        try:
            # ----------------------------------------------------------------
            # 1. Task Board Status (donut) — replaces empty projects table
            # ----------------------------------------------------------------
            task_statuses = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM kanban_tasks GROUP BY status"
            ).fetchall()

            # ----------------------------------------------------------------
            # 2. Activity Trend: last 7 days (line chart) — from audit_trail
            # ----------------------------------------------------------------
            _is_pg = getattr(conn, "_backend", "sqlite") == "postgresql"
            if _is_pg:
                activity_trend = conn.execute(
                    "SELECT DATE(created_at) as day, COUNT(*) as cnt "
                    "FROM audit_trail WHERE created_at >= NOW() - INTERVAL '7 days' "
                    "GROUP BY DATE(created_at) ORDER BY day"
                ).fetchall()
            else:
                activity_trend = conn.execute(
                    "SELECT DATE(created_at) as day, COUNT(*) as cnt "
                    "FROM audit_trail WHERE created_at >= DATE('now', '-7 days') "
                    "GROUP BY DATE(created_at) ORDER BY day"
                ).fetchall()

            # ----------------------------------------------------------------
            # 3. Compliance Posture — aggregate across canvas assessment DBs
            # ----------------------------------------------------------------
            _CANVAS_DBS = [
                ("Security",      BASE_DIR / "data" / "security_canvas.db"),
                ("Network",       BASE_DIR / "data" / "network_canvas.db"),
                ("Pipeline",      BASE_DIR / "data" / "pipeline_canvas.db"),
                ("Infra",         BASE_DIR / "data" / "infra_canvas.db"),
                ("Data",          BASE_DIR / "data" / "data_canvas.db"),
                ("Boundary",      BASE_DIR / "data" / "boundary_canvas.db"),
                ("Observability", BASE_DIR / "data" / "observability_canvas.db"),
            ]

            canvas_compliance = []
            overall_scores = []

            for canvas_name, db_path in _CANVAS_DBS:
                if not db_path.exists():
                    continue
                try:
                    cconn = _sqlite3.connect(str(db_path))
                    cconn.row_factory = _sqlite3.Row
                    try:
                        if canvas_name == "Security":
                            row = cconn.execute(
                                "SELECT AVG(risk_score) as avg_score, "
                                "COUNT(*) as total_threats FROM sc_assessments"
                            ).fetchone()
                            score = round(max(0.0, 100.0 - float(row["avg_score"] or 0)), 1)
                            open_f = int(row["total_threats"] or 0)
                            closed_f = 0
                        elif canvas_name in ("Network", "Pipeline"):
                            tbl = "nc_compliance_findings" if canvas_name == "Network" else "pc_compliance_findings"
                            open_f = cconn.execute(
                                f"SELECT COUNT(*) as cnt FROM {tbl} WHERE status = 'open'"
                            ).fetchone()["cnt"]
                            closed_f = cconn.execute(
                                f"SELECT COUNT(*) as cnt FROM {tbl} WHERE status != 'open'"
                            ).fetchone()["cnt"]
                            total_f = open_f + closed_f
                            score = round((closed_f / total_f * 100) if total_f > 0 else 100.0, 1)
                        elif canvas_name in ("Infra", "Data"):
                            tbl = "idc_assessments" if canvas_name == "Infra" else "dd_assessments"
                            row = cconn.execute(
                                f"SELECT AVG(score) as avg_score FROM {tbl}"
                            ).fetchone()
                            score = round(float(row["avg_score"] or 0), 1)
                            open_f = 0
                            closed_f = 0
                        elif canvas_name == "Boundary":
                            row = cconn.execute(
                                "SELECT SUM(cat1_findings) as cat1, "
                                "SUM(cat2_findings) as cat2, "
                                "SUM(cat3_findings) as cat3, "
                                "AVG(score) as avg_score FROM bd_assessments"
                            ).fetchone()
                            open_f = int((row["cat1"] or 0) + (row["cat2"] or 0) + (row["cat3"] or 0))
                            closed_f = 0
                            score = round(float(row["avg_score"] or 0), 1)
                        elif canvas_name == "Observability":
                            row = cconn.execute(
                                "SELECT AVG(score) as avg_score FROM od_assessments"
                            ).fetchone()
                            score = round(float(row["avg_score"] or 0), 1)
                            open_f = 0
                            closed_f = 0
                        else:
                            continue

                        canvas_compliance.append({
                            "name": canvas_name,
                            "score": score,
                            "open_findings": open_f,
                            "closed_findings": closed_f,
                        })
                        if score > 0:
                            overall_scores.append(score)
                    finally:
                        cconn.close()
                except Exception:
                    pass  # Graceful if canvas DB has no data yet

            overall_score = round(sum(overall_scores) / len(overall_scores), 1) if overall_scores else 0.0

            # ----------------------------------------------------------------
            # 4. Agent health (gauge: % active) — unchanged
            # ----------------------------------------------------------------
            total_agents = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents"
            ).fetchone()["cnt"]
            active_agents = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents WHERE status = 'active'"
            ).fetchone()["cnt"]

            return jsonify({
                "task_statuses": [dict(r) for r in task_statuses],
                "activity_trend": [dict(r) for r in activity_trend],
                "compliance": {
                    "canvases": canvas_compliance,
                    "overall_score": overall_score,
                },
                "agent_health": {
                    "total": total_agents,
                    "active": active_agents,
                    "ratio": active_agents / total_agents if total_agents > 0 else 1.0,
                },
            })
        finally:
            conn.close()

    @app.route("/api/charts/project/<project_id>", methods=["GET"])
    def api_charts_project(project_id):
        """Chart data for a specific project detail page."""
        conn = _get_db()
        try:
            # STIG by severity (donut)
            stig_sev = conn.execute(
                "SELECT severity, status, COUNT(*) as cnt "
                "FROM stig_findings WHERE project_id = ? "
                "GROUP BY severity, status",
                (project_id,),
            ).fetchall()

            # POAM by severity (bar)
            poam_sev = conn.execute(
                "SELECT severity, status, COUNT(*) as cnt "
                "FROM poam_items WHERE project_id = ? "
                "GROUP BY severity, status",
                (project_id,),
            ).fetchall()

            # Deployment history (line — status over time)
            deploys = conn.execute(
                "SELECT DATE(created_at) as day, status, COUNT(*) as cnt "
                "FROM deployments WHERE project_id = ? "
                "GROUP BY DATE(created_at), status ORDER BY day",
                (project_id,),
            ).fetchall()

            # Alert trend for project
            alerts = conn.execute(
                "SELECT DATE(created_at) as day, severity, COUNT(*) as cnt "
                "FROM alerts WHERE project_id = ? "
                "GROUP BY DATE(created_at), severity ORDER BY day",
                (project_id,),
            ).fetchall()

            return jsonify({
                "stig_by_severity": [dict(r) for r in stig_sev],
                "poam_by_severity": [dict(r) for r in poam_sev],
                "deployment_history": [dict(r) for r in deploys],
                "alert_trend": [dict(r) for r in alerts],
            })
        finally:
            conn.close()

    # ---- HTML page routes ----

    @app.route("/")
    def index():
        """Dashboard home page with Kanban board."""
        conn = _get_db()
        try:
            # All projects for Kanban board
            projects = conn.execute(
                "SELECT id, name, type, status, classification "
                "FROM projects ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
            projects = [dict(r) for r in projects]

            # Agent counts (stat bar)
            total_agents = conn.execute("SELECT COUNT(*) as cnt FROM agents").fetchone()["cnt"]
            active_agents = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents WHERE status = 'active'"
            ).fetchone()["cnt"]
            inactive_agents = total_agents - active_agents

            # Recent audit entries (for existing audit trail section)
            recent_audit = conn.execute(
                "SELECT * FROM audit_trail ORDER BY created_at DESC LIMIT 10"
            ).fetchall()

            # --- Recent Activity & Findings: audit_trail + canvas CAT1 findings ---
            import sqlite3 as _sqlite3

            _audit_rows = conn.execute(
                "SELECT event_type, actor, action, project_id, created_at "
                "FROM audit_trail ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            _activity = []
            for _e in _audit_rows:
                _e = dict(_e)
                _activity.append({
                    "event_type": _e.get("event_type") or "AUDIT",
                    "source": "System",
                    "details": _e.get("action") or "",
                    "severity": "info",
                    "created_at": _e.get("created_at") or "",
                })

            # Canvas DBs: extract CAT1 findings from recent assessments
            _canvas_json_dbs = [
                ("security_canvas.db", "sc_assessments", "findings_json", "ran_at", "Security Canvas"),
                ("infra_canvas.db", "idc_assessments", "findings_json", "created_at", "Infra Canvas"),
                ("observability_canvas.db", "od_assessments", "findings_json", "created_at", "Observability Canvas"),
                ("boundary_canvas.db", "bd_assessments", "findings_json", "created_at", "Boundary Canvas"),
                ("data_canvas.db", "dd_assessments", "findings_json", "created_at", "Data Canvas"),
            ]
            cat1_count = 0
            open_canvas_count = 0
            for _db_name, _tbl, _fcol, _tcol, _label in _canvas_json_dbs:
                try:
                    _cc = _sqlite3.connect(str(BASE_DIR / "data" / _db_name))
                    _cc.row_factory = _sqlite3.Row
                    _rows = _cc.execute(
                        f"SELECT {_fcol}, {_tcol} FROM {_tbl} ORDER BY {_tcol} DESC LIMIT 3"
                    ).fetchall()
                    for _row in _rows:
                        _ts = _row[1] or ""
                        try:
                            _items = json.loads(_row[0] or "[]")
                        except (json.JSONDecodeError, TypeError):
                            _items = []
                        for _f in _items:
                            _sev = _f.get("severity", "")
                            open_canvas_count += 1
                            if _sev == "CAT1":
                                cat1_count += 1
                                _detail = _f.get("title", "")
                                _extra = _f.get("detail") or _f.get("affected_entity") or ""
                                if _extra:
                                    _detail = f"{_detail}: {_extra}"
                                _activity.append({
                                    "event_type": "Canvas Finding",
                                    "source": _label,
                                    "details": _detail,
                                    "severity": "CAT1",
                                    "created_at": _ts,
                                })
                    _cc.close()
                except Exception:
                    pass

            # Direct findings tables (network, pipeline canvas)
            _canvas_direct_dbs = [
                ("network_canvas.db", "nc_compliance_findings", "Network Canvas"),
                ("pipeline_canvas.db", "pc_compliance_findings", "Pipeline Canvas"),
            ]
            for _db_name, _tbl, _label in _canvas_direct_dbs:
                try:
                    _cc = _sqlite3.connect(str(BASE_DIR / "data" / _db_name))
                    _cc.row_factory = _sqlite3.Row
                    _rows = _cc.execute(
                        f"SELECT severity, title, description, status, created_at "
                        f"FROM {_tbl} ORDER BY created_at DESC LIMIT 10"
                    ).fetchall()
                    for _row in _rows:
                        _sev = _row["severity"] or ""
                        _status = _row["status"] or "open"
                        if _status != "remediated":
                            open_canvas_count += 1
                        if _sev == "CAT1":
                            cat1_count += 1
                            _activity.append({
                                "event_type": "Canvas Finding",
                                "source": _label,
                                "details": _row["title"] or "",
                                "severity": "CAT1",
                                "created_at": _row["created_at"] or "",
                            })
                    _cc.close()
                except Exception:
                    pass

            # Sort merged activity by created_at DESC, limit 10
            _activity.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
            recent_activity = _activity[:10]

            # Firing alert count = CAT1 canvas findings
            firing_alerts = cat1_count

            # Open POAM count = total open canvas findings
            open_poam = open_canvas_count

            # Group projects by status for Kanban columns
            kanban_columns = {
                "planning": [],
                "active": [],
                "completed": [],
                "inactive": [],
            }
            for p in projects:
                status = p.get("status", "inactive")
                if status in kanban_columns:
                    kanban_columns[status].append(p)
                else:
                    kanban_columns["inactive"].append(p)

            return render_template(
                "index.html",
                projects=projects,
                kanban_columns=kanban_columns,
                total_projects=len(projects),
                total_agents=total_agents,
                active_agents=active_agents,
                inactive_agents=inactive_agents,
                recent_audit=[dict(r) for r in recent_audit],
                recent_activity=recent_activity,
                firing_alerts=firing_alerts,
                open_poam=open_poam,
            )
        finally:
            conn.close()

    @app.route("/kanban")
    def kanban_page():
        """Task Board — Kanban view for scheduled and planned work."""
        return render_template("kanban.html")

    @app.route("/projects")
    def projects_list():
        """Project listing page."""
        conn = _get_db()
        try:
            projects = conn.execute(
                "SELECT id, name, type, status, classification, created_at "
                "FROM projects ORDER BY created_at DESC"
            ).fetchall()
            return render_template("projects/list.html", projects=[dict(r) for r in projects])
        finally:
            conn.close()

    @app.route("/projects/<project_id>")
    def project_detail(project_id):
        """Project detail page with tabs."""
        conn = _get_db()
        try:
            # Project info
            project = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not project:
                return render_template("404.html", message="Project not found"), 404
            project = dict(project)

            # SSP documents
            ssps = conn.execute(
                "SELECT * FROM ssp_documents WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()

            # POAM items
            poams = conn.execute(
                "SELECT * FROM poam_items WHERE project_id = ? ORDER BY severity, created_at DESC",
                (project_id,),
            ).fetchall()

            # STIG findings
            stigs = conn.execute(
                "SELECT * FROM stig_findings WHERE project_id = ? ORDER BY severity, created_at DESC",
                (project_id,),
            ).fetchall()

            # SBOM records
            sboms = conn.execute(
                "SELECT * FROM sbom_records WHERE project_id = ? ORDER BY generated_at DESC",
                (project_id,),
            ).fetchall()

            # Deployments
            deployments = conn.execute(
                "SELECT * FROM deployments WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()

            # Audit trail
            audit_entries = conn.execute(
                "SELECT * FROM audit_trail WHERE project_id = ? ORDER BY created_at DESC LIMIT 50",
                (project_id,),
            ).fetchall()

            # Alerts
            alerts = conn.execute(
                "SELECT * FROM alerts WHERE project_id = ? ORDER BY created_at DESC LIMIT 20",
                (project_id,),
            ).fetchall()

            # Summaries
            poam_open = sum(1 for p in poams if dict(p)["status"] == "open")
            stig_open = sum(1 for s in stigs if dict(s)["status"] == "Open")

            stig_by_severity = {}
            for s in stigs:
                sd = dict(s)
                sev = sd.get("severity", "unknown")
                if sev not in stig_by_severity:
                    stig_by_severity[sev] = {"open": 0, "closed": 0}
                if sd["status"] == "Open":
                    stig_by_severity[sev]["open"] += 1
                else:
                    stig_by_severity[sev]["closed"] += 1

            return render_template(
                "projects/detail.html",
                project=project,
                ssps=[dict(r) for r in ssps],
                poams=[dict(r) for r in poams],
                poam_open=poam_open,
                stigs=[dict(r) for r in stigs],
                stig_open=stig_open,
                stig_by_severity=stig_by_severity,
                sboms=[dict(r) for r in sboms],
                deployments=[dict(r) for r in deployments],
                audit_entries=[dict(r) for r in audit_entries],
                alerts=[dict(r) for r in alerts],
            )
        finally:
            conn.close()

    @app.route("/agents")
    def agents_list():
        """Agent status page."""
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM agents ORDER BY name"
            ).fetchall()
            agents = []
            for r in rows:
                agent = dict(r)
                tc = conn.execute(
                    "SELECT COUNT(*) as cnt FROM a2a_tasks "
                    "WHERE target_agent_id = ? AND status IN ('submitted', 'working')",
                    (agent["id"],),
                ).fetchone()
                agent["active_task_count"] = tc["cnt"] if tc else 0
                agents.append(agent)

            active = sum(1 for a in agents if a["status"] == "active")
            inactive = len(agents) - active

            return render_template(
                "agents/list.html",
                agents=agents,
                active_count=active,
                inactive_count=inactive,
            )
        finally:
            conn.close()

    @app.route("/monitoring")
    def monitoring_overview():
        """Monitoring overview page."""
        conn = _get_db()
        try:
            # Recent alerts
            alerts = conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 20"
            ).fetchall()

            # Self-healing events
            healing_events = conn.execute(
                "SELECT she.*, kp.description as pattern_description "
                "FROM self_healing_events she "
                "LEFT JOIN knowledge_patterns kp ON she.pattern_id = kp.id "
                "ORDER BY she.created_at DESC LIMIT 20"
            ).fetchall()

            # Health stats
            firing = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE status = 'firing'"
            ).fetchone()["cnt"]
            resolved = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE status = 'resolved'"
            ).fetchone()["cnt"]
            unresolved_failures = conn.execute(
                "SELECT COUNT(*) as cnt FROM failure_log WHERE resolved = 0"
            ).fetchone()["cnt"]

            health = "healthy"
            if firing > 0 or unresolved_failures > 5:
                health = "degraded"
            if firing > 5:
                health = "critical"

            return render_template(
                "monitoring/overview.html",
                alerts=[dict(r) for r in alerts],
                healing_events=[dict(r) for r in healing_events],
                firing_count=firing,
                resolved_count=resolved,
                unresolved_failures=unresolved_failures,
                health_status=health,
            )
        finally:
            conn.close()

    # ---- Events & NLQ page routes ----

    @app.route("/events")
    def events_page():
        """Real-time event timeline page (SSE-powered)."""
        conn = _get_db()
        try:
            recent_events = conn.execute(
                "SELECT * FROM hook_events ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            return render_template(
                "events/timeline.html",
                recent_events=[dict(r) for r in recent_events],
            )
        except Exception:
            return render_template("events/timeline.html", recent_events=[])
        finally:
            conn.close()

    @app.route("/oracle")
    def oracle_page():
        """Oracle — predictive intelligence dashboard."""
        return render_template("oracle.html")

    @app.route("/activity")
    def activity_page():
        """Activity feed — merged audit + hook events with real-time updates."""
        return render_template("activity.html")

    @app.route("/usage")
    def usage_page():
        """Usage tracking + cost dashboard."""
        return render_template("usage.html")

    @app.route("/wizard")
    def wizard_page():
        """Getting Started wizard — guides new users to the right workflow."""
        return render_template("wizard.html")

    @app.route("/chat")
    def chat_new():
        """Start a new requirements chat — wizard params set context."""
        goal = flask_request.args.get("goal", "build")
        role = flask_request.args.get("role", "developer")
        classification = flask_request.args.get("classification", "il4")
        frameworks = flask_request.args.get("frameworks", "")
        custom_role_name = flask_request.args.get("custom_role_name", "")
        custom_role_desc = flask_request.args.get("custom_role_desc", "")
        return render_template(
            "chat.html",
            session_id=None,
            messages=[],
            wizard_goal=goal,
            wizard_role=role,
            wizard_classification=classification,
            wizard_frameworks=frameworks,
            wizard_custom_role_name=custom_role_name,
            wizard_custom_role_desc=custom_role_desc,
        )

    @app.route("/chat/<session_id>")
    def chat_session(session_id):
        """Resume an existing requirements chat session."""
        conn = _get_db()
        try:
            try:
                session = conn.execute(
                    "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
                ).fetchone()
            except Exception:
                session = None
            if not session:
                return render_template("404.html", message="Session not found"), 404
            messages = conn.execute(
                "SELECT turn_number, role, content, content_type, created_at "
                "FROM intake_conversation WHERE session_id = ? ORDER BY turn_number",
                (session_id,),
            ).fetchall()
            # Extract context for sidebar display
            import json as _json
            session_dict = dict(session)
            ctx = {}
            try:
                ctx = _json.loads(session_dict.get("context_summary") or "{}")
            except (ValueError, TypeError):
                pass
            return render_template(
                "chat.html",
                session_id=session_id,
                session=session_dict,
                messages=[dict(m) for m in messages],
                wizard_goal=None,
                wizard_role=None,
                wizard_classification=None,
                wizard_frameworks=",".join(ctx.get("selected_frameworks", [])),
                wizard_custom_role_name="",
                wizard_custom_role_desc="",
                session_context=ctx,
            )
        finally:
            conn.close()

    @app.route("/quick-paths")
    def quick_paths_page():
        """Quick Path workflow templates — pre-built shortcuts for common tasks."""
        return render_template("quick_paths.html")

    @app.route("/batch")
    def batch_page():
        """Batch operations — run multi-tool workflows from the dashboard."""
        return render_template("batch.html")

    @app.route("/connector-forge")
    def connector_forge_page():
        """Connector Forge — generate API connectors from OpenAPI specs."""
        return render_template("connector_forge.html")

    @app.route("/api/connector-forge/list")
    def api_connector_forge_list():
        """List all generated/registered connectors."""
        try:
            from tools.databridge.registry import list_registered
            registered = list_registered()
            connectors = [{"name": k, "type": "registered", "status": "active"} for k in registered]
            return jsonify({"connectors": connectors, "total": len(connectors)})
        except Exception:
            return jsonify({"connectors": [], "total": 0})

    @app.route("/diagrams")
    def diagrams_page():
        """Interactive Mermaid diagrams — catalog, viewer, and editor."""
        return render_template("diagrams.html")

    @app.route("/cicd")
    def cicd_page():
        """CI/CD pipeline status, conversations, and connector health."""
        return render_template("cicd.html")

    @app.route("/gateway")
    def gateway_page():
        """Remote Command Gateway admin — bindings, command log, channel status."""
        import yaml as _yaml

        # Load gateway config
        gateway_config_path = BASE_DIR / "args" / "remote_gateway_config.yaml"
        gw_config = {}
        if gateway_config_path.exists():
            with open(gateway_config_path) as f:
                gw_config = _yaml.safe_load(f) or {}

        env_mode = gw_config.get("environment", {}).get("mode", "connected")
        channels = gw_config.get("channels", {})

        # Determine active channels
        active_channels = []
        for name, ch in channels.items():
            enabled = ch.get("enabled", False)
            req_internet = ch.get("requires_internet", False)
            available = enabled and not (env_mode == "air_gapped" and req_internet)
            active_channels.append({
                "name": name,
                "enabled": enabled,
                "available": available,
                "max_il": ch.get("max_il", "IL4"),
                "description": ch.get("description", ""),
            })

        # Load bindings and recent commands
        conn = _get_db()
        try:
            bindings = conn.execute(
                "SELECT * FROM remote_user_bindings ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            bindings = [dict(r) for r in bindings]
        except Exception:
            bindings = []

        try:
            commands = conn.execute(
                "SELECT * FROM remote_command_log ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            commands = [dict(r) for r in commands]
        except Exception:
            commands = []

        conn.close()

        return render_template(
            "gateway.html",
            environment_mode=env_mode,
            channels=active_channels,
            bindings=bindings,
            commands=commands,
            command_allowlist=gw_config.get("command_allowlist", []),
        )

    @app.route("/query")
    def query_page():
        """Natural language compliance query page."""
        conn = _get_db()
        try:
            recent_queries = conn.execute(
                "SELECT * FROM nlq_queries ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            return render_template(
                "query/nlq.html",
                recent_queries=[dict(r) for r in recent_queries],
            )
        except Exception:
            return render_template("query/nlq.html", recent_queries=[])
        finally:
            conn.close()

    # ---- Tour configuration ----

    @app.route("/api/tour/steps", methods=["GET"])
    def api_tour_steps():
        """Return tour step definitions for the onboarding walkthrough.

        Steps are served from DB (tour_config table) first, falling back
        to built-in defaults. tour.js fetches this endpoint on init and
        falls back to built-in defaults if the fetch fails (air-gap safe).
        """
        # Try DB first
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT selector, title, description FROM tour_config ORDER BY sort_order"
            ).fetchall()
            if rows:
                db_steps = [{"selector": r["selector"], "title": r["title"], "desc": r["description"]} for r in rows]
                return jsonify({"steps": db_steps, "version": 2, "source": "db", "classification": DEFAULT_CLASSIFICATION})
        except Exception:
            pass  # Table may not exist yet — fall through to defaults
        finally:
            conn.close()

        # Built-in defaults
        steps = [
            {
                "selector": ".navbar",
                "title": "Navigation Bar",
                "desc": (
                    "Navigate between pages: Home, Projects, Agents, "
                    "Monitoring, Quick Paths, Batch Operations, and "
                    "the Getting Started wizard."
                ),
            },
            {
                "selector": ".kanban-board",
                "title": "Project Kanban Board",
                "desc": (
                    "Projects organized by workflow stage: Planning, Active, "
                    "Completed, and Inactive. Click any card to view details."
                ),
            },
            {
                "selector": ".chart-grid",
                "title": "Visual Dashboards",
                "desc": (
                    "Visual dashboards: compliance posture, alert trends, "
                    "project status, and agent health charts."
                ),
            },
            {
                "selector": ".table-container",
                "title": "Data Tables",
                "desc": (
                    "Detailed data tables with search, sort, filter, "
                    "and CSV export capabilities."
                ),
            },
            {
                "selector": "#role-select",
                "title": "Role Selector",
                "desc": (
                    "Switch views: Program Manager, Developer, ISSO, or "
                    "Contracting Officer to see role-relevant information."
                ),
            },
            {
                "selector": "a[href*='quick-paths'], a[href*='/quick-paths']",
                "title": "Quick Paths",
                "desc": (
                    "Pre-built workflow shortcuts for common tasks like "
                    "ATO generation, project creation, and security scanning."
                ),
            },
            {
                "selector": "a[href*='/batch']",
                "title": "Batch Operations",
                "desc": (
                    "Run multi-step batch operations: Full ATO Package, "
                    "Security Scan Suite, Multi-Framework Check, or "
                    "Build & Validate from a single click."
                ),
            },
            {
                "selector": "a[href*='/events']",
                "title": "Live Events",
                "desc": (
                    "Real-time event timeline showing hook events, "
                    "agent activity, and system notifications with "
                    "severity filtering."
                ),
            },
        ]
        return jsonify({
            "steps": steps,
            "version": 2,
            "classification": DEFAULT_CLASSIFICATION,
        })

    # ---- Profile routes (D172, D175-D178) ----

    @app.route("/profile")
    def profile_page():
        """User profile page with BYOK key management."""
        return render_template("profile.html")

    @app.route("/profile/api/keys")
    def profile_api_keys():
        """List current user's dashboard API keys."""
        from tools.dashboard.auth import list_api_keys_for_user
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"keys": []})
        keys = list_api_keys_for_user(user["id"])
        return jsonify({"keys": keys})

    @app.route("/profile/api/llm-keys", methods=["GET"])
    def profile_llm_keys():
        """List current user's BYOK LLM keys."""
        from tools.dashboard.byok import list_llm_keys
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"keys": []})
        keys = list_llm_keys(user["id"])
        return jsonify({"keys": keys})

    @app.route("/profile/api/llm-keys", methods=["POST"])
    def profile_add_llm_key():
        """Store a new BYOK LLM key for the current user."""
        from tools.dashboard.byok import store_llm_key
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"error": "Not authenticated"}), 401
        data = flask_request.get_json(force=True)
        provider = data.get("provider", "").strip()
        api_key = data.get("api_key", "").strip()
        label = data.get("label", "").strip()
        if not provider or not api_key:
            return jsonify({"error": "provider and api_key required"}), 400
        result = store_llm_key(user["id"], provider, api_key, key_label=label)
        return jsonify(result), 201

    @app.route("/profile/api/llm-keys/<key_id>/revoke", methods=["POST"])
    def profile_revoke_llm_key(key_id):
        """Revoke a BYOK LLM key (ownership-scoped)."""
        from tools.dashboard.byok import revoke_llm_key
        user = getattr(g, "current_user", None)
        if not user:
            return jsonify({"error": "Not authenticated"}), 401
        success = revoke_llm_key(key_id, user_id=user["id"])
        if not success:
            return jsonify({"error": "Key not found"}), 404
        return jsonify({"status": "revoked"})

    # ---- Phase roadmap route ----

    @app.route("/phases")
    def phases_page():
        """Phase roadmap — all ICDEV™ phases with status, categories, and progress."""
        from tools.dashboard.phase_loader import (
            load_phases, load_categories, load_statuses, get_phase_summary,
        )
        phases = load_phases()
        categories = load_categories()
        statuses = load_statuses()
        summary = get_phase_summary(phases)

        # Optional category filter from query param
        cat_filter = flask_request.args.get("category", "")
        if cat_filter:
            phases = [p for p in phases if p.get("category") == cat_filter]

        return render_template(
            "phases.html",
            phases=phases,
            categories=categories,
            statuses=statuses,
            summary=summary,
            category_filter=cat_filter,
        )

    # ---- Dev profile routes (Phase 34, D183-D188) ----

    @app.route("/dev-profiles")
    def dev_profiles_page():
        """Dev profile management — list, create, view profiles."""
        return render_template("dev_profiles.html")

    # ---- Child application routes (Phase 19 + Evolutionary Intelligence) ----

    @app.route("/children")
    def children_page():
        """Child application registry — health, genome, capabilities, heartbeats."""
        conn = _get_db()
        try:
            # Fetch all registered child applications
            try:
                children_rows = conn.execute(
                    "SELECT * FROM child_app_registry ORDER BY created_at DESC"
                ).fetchall()
                children_rows = [dict(r) for r in children_rows]
            except Exception:
                children_rows = []

            # Fetch latest heartbeat per child from telemetry
            heartbeat_map = {}
            try:
                heartbeats = conn.execute(
                    "SELECT child_id, MAX(reported_at) as last_heartbeat "
                    "FROM child_telemetry GROUP BY child_id"
                ).fetchall()
                for hb in heartbeats:
                    hb_dict = dict(hb)
                    heartbeat_map[hb_dict["child_id"]] = hb_dict["last_heartbeat"]
            except Exception:
                pass

            # Fetch capability count per child
            capability_map = {}
            try:
                caps = conn.execute(
                    "SELECT child_id, COUNT(*) as cnt FROM child_capabilities GROUP BY child_id"
                ).fetchall()
                for c in caps:
                    c_dict = dict(c)
                    capability_map[c_dict["child_id"]] = c_dict["cnt"]
            except Exception:
                pass

            # Enrich children with heartbeat and capability data
            children = []
            for child in children_rows:
                child["last_heartbeat"] = heartbeat_map.get(child.get("id"), child.get("last_heartbeat"))
                child["capability_count"] = capability_map.get(child.get("id"), child.get("capability_count", 0))
                child["pending_upgrades"] = child.get("pending_upgrades", 0)
                child["genome_version"] = child.get("genome_version", None)
                child["health_status"] = child.get("health_status", "unhealthy")
                children.append(child)

            # Compute summary counts
            healthy_count = sum(1 for c in children if c["health_status"] == "healthy")
            degraded_count = sum(1 for c in children if c["health_status"] == "degraded")
            unhealthy_count = sum(1 for c in children if c["health_status"] not in ("healthy", "degraded"))

            return render_template(
                "children.html",
                children=children,
                total_count=len(children),
                healthy_count=healthy_count,
                degraded_count=degraded_count,
                unhealthy_count=unhealthy_count,
            )
        finally:
            conn.close()

    @app.route("/dev-profiles/api/list")
    def dev_profiles_api_list():
        """List all dev profiles (JSON)."""
        conn = _get_db()
        try:
            rows = conn.execute(
                """SELECT id, scope, scope_id, version, is_active, inherits_from,
                          created_by, created_at, change_summary
                   FROM dev_profiles WHERE is_active = 1
                   ORDER BY created_at DESC LIMIT 50"""
            ).fetchall()
            return jsonify({"profiles": [dict(r) for r in rows]})
        except Exception as e:
            return jsonify({"profiles": [], "error": str(e)})
        finally:
            conn.close()

    @app.route("/dev-profiles/api/resolve/<scope>/<scope_id>")
    def dev_profiles_api_resolve(scope, scope_id):
        """Resolve 5-layer cascade for a scope (JSON)."""
        try:
            from tools.builder.dev_profile_manager import resolve_profile
            result = resolve_profile(scope, scope_id)
            return jsonify(result)
        except (ImportError, Exception) as e:
            return jsonify({"error": str(e)})

    @app.route("/dev-profiles/api/templates")
    def dev_profiles_api_templates():
        """List available starter templates (JSON)."""
        templates = []
        templates_dir = Path(__file__).resolve().parent.parent.parent / "context" / "profiles"
        if templates_dir.exists():
            try:
                import yaml
                for f in sorted(templates_dir.glob("*.yaml")):
                    with open(f, "r", encoding="utf-8") as fh:
                        data = yaml.safe_load(fh)
                        templates.append({
                            "name": data.get("name", f.stem),
                            "file": f.name,
                            "description": data.get("description", ""),
                            "impact_levels": data.get("impact_levels", []),
                        })
            except Exception:
                pass
        return jsonify({"templates": templates})

    @app.route("/dev-profiles/api/create", methods=["POST"])
    def dev_profiles_api_create():
        """Create a dev profile from template or data (JSON)."""
        try:
            from tools.builder.dev_profile_manager import create_profile
            data = flask_request.get_json(silent=True) or {}
            result = create_profile(
                scope=data.get("scope", "project"),
                scope_id=data.get("scope_id", ""),
                template_name=data.get("template"),
                created_by=data.get("created_by", "dashboard"),
            )
            return jsonify(result), 201 if "error" not in result else 400
        except (ImportError, Exception) as e:
            return jsonify({"error": str(e)}), 500

    # ---- Auth routes (D169-D172) ----

    @app.route("/login", methods=["GET", "POST"])
    def login_page():
        """Login page — accepts API key via form or header."""
        # Auto-login when .env key is configured
        env_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
        if env_key:
            try:
                user = validate_api_key(env_key)
                if not user:
                    from tools.dashboard.auth import bootstrap_env_user
                    user = bootstrap_env_user(env_key)
                if user:
                    flask_session["user_id"] = user["id"]
                    return redirect(url_for("index"))
                else:
                    app.logger.warning("ICDEV_DASHBOARD_API_KEY set but validation failed")
            except Exception as exc:
                app.logger.error(f"Auto-login failed: {exc}")
        if flask_request.method == "POST":
            raw_key = flask_request.form.get("api_key", "").strip()
            user = validate_api_key(raw_key)
            # Fallback: accept ICDEV_DASHBOARD_API_KEY from .env
            if not user:
                env_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
                if env_key and raw_key == env_key:
                    from tools.dashboard.auth import bootstrap_env_user
                    user = bootstrap_env_user(env_key)
            if user:
                flask_session["user_id"] = user["id"]
                log_auth_event(
                    user["id"], "login_success",
                    ip_address=flask_request.remote_addr,
                    user_agent=flask_request.headers.get("User-Agent", "")[:256],
                    details="via_login_form",
                )
                return redirect(url_for("index"))
            else:
                log_auth_event(
                    None, "login_failed",
                    ip_address=flask_request.remote_addr,
                    user_agent=flask_request.headers.get("User-Agent", "")[:256],
                    details="via_login_form",
                )
                return render_template("login.html", error="Invalid API key. Please try again.")
        return render_template("login.html", error=None)

    @app.route("/logout")
    def logout():
        """Clear session and redirect to login."""
        user_id = flask_session.get("user_id")
        if user_id:
            log_auth_event(
                user_id, "logout",
                ip_address=flask_request.remote_addr,
            )
        flask_session.clear()
        return redirect(url_for("login_page"))

    # ---- Error handlers ----

    # ---- Cross-Language Translation routes (Phase 43) ----

    @app.route("/translations")
    def translations_page():
        """Translation jobs — list, status, validation scores."""
        conn = _get_db()
        try:
            try:
                jobs = conn.execute(
                    """SELECT id, project_id, source_language, target_language,
                              status, total_units, translated_units, mocked_units,
                              failed_units, gate_result, llm_model, llm_tokens_input,
                              llm_tokens_output, elapsed_seconds, created_at
                       FROM translation_jobs ORDER BY created_at DESC LIMIT 100"""
                ).fetchall()
                jobs = [dict(r) for r in jobs]
            except Exception:
                jobs = []

            # Summary stats
            total = len(jobs)
            completed = sum(1 for j in jobs if j.get("status") == "completed")
            in_progress = sum(1 for j in jobs if j.get("status") in ("pending", "extracting", "translating", "assembling", "validating"))
            failed = sum(1 for j in jobs if j.get("status") in ("failed", "partial"))

            # Average API surface score from validations
            avg_api_score = None
            try:
                row = conn.execute(
                    """SELECT AVG(score) as avg_score FROM translation_validations
                       WHERE check_type = 'api_surface' AND passed = 1"""
                ).fetchone()
                if row and row["avg_score"]:
                    avg_api_score = round(row["avg_score"] * 100, 1)
            except Exception:
                pass

            return render_template(
                "translations.html",
                jobs=jobs,
                total=total,
                completed=completed,
                in_progress=in_progress,
                failed=failed,
                avg_api_score=avg_api_score,
            )
        finally:
            conn.close()

    @app.route("/translations/<job_id>")
    def translation_detail_page(job_id):
        """Translation job detail — units, validations, dependencies."""
        conn = _get_db()
        try:
            # Fetch job
            try:
                job = conn.execute(
                    "SELECT * FROM translation_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                job = dict(job) if job else None
            except Exception:
                job = None

            if not job:
                return render_template("404.html", message="Translation job not found"), 404

            # Fetch units
            try:
                units = conn.execute(
                    """SELECT unit_name, unit_kind, source_file, status,
                              source_complexity, target_complexity,
                              repair_count, candidate_selected, created_at
                       FROM translation_units WHERE job_id = ?
                       ORDER BY created_at""", (job_id,)
                ).fetchall()
                units = [dict(u) for u in units]
            except Exception:
                units = []

            # Fetch validations
            try:
                validations = conn.execute(
                    """SELECT check_type, passed, score, findings, created_at
                       FROM translation_validations WHERE job_id = ?
                       ORDER BY created_at""", (job_id,)
                ).fetchall()
                validations = [dict(v) for v in validations]
            except Exception:
                validations = []

            # Fetch dependency mappings
            try:
                deps = conn.execute(
                    """SELECT source_import, target_import, mapping_source,
                              confidence, domain
                       FROM translation_dependency_mappings WHERE job_id = ?
                       ORDER BY domain, source_import""", (job_id,)
                ).fetchall()
                deps = [dict(d) for d in deps]
            except Exception:
                deps = []

            return render_template(
                "translation_detail.html",
                job=job,
                units=units,
                validations=validations,
                deps=deps,
            )
        finally:
            conn.close()

    @app.route("/api/charts/translations")
    def api_charts_translations():
        """Chart data for translations page."""
        conn = _get_db()
        try:
            # Status distribution
            status_dist = {}
            try:
                rows = conn.execute(
                    "SELECT status, COUNT(*) as cnt FROM translation_jobs GROUP BY status"
                ).fetchall()
                for r in rows:
                    r_dict = dict(r)
                    status_dist[r_dict["status"]] = r_dict["cnt"]
            except Exception:
                pass

            # Language pair frequency
            lang_pairs = {}
            try:
                rows = conn.execute(
                    """SELECT source_language || ' → ' || target_language as pair,
                              COUNT(*) as cnt
                       FROM translation_jobs GROUP BY pair ORDER BY cnt DESC LIMIT 10"""
                ).fetchall()
                for r in rows:
                    r_dict = dict(r)
                    lang_pairs[r_dict["pair"]] = r_dict["cnt"]
            except Exception:
                pass

            return jsonify({
                "status_distribution": status_dist,
                "language_pair_frequency": lang_pairs,
            })
        finally:
            conn.close()

    # ---- Phase 46: Observability pages ----

    @app.route("/traces")
    def traces_page():
        """Trace explorer — distributed tracing across MCP, A2A, LLM."""
        return render_template("traces.html")

    @app.route("/provenance")
    def provenance_page():
        """Provenance graph — W3C PROV-AGENT artifact lineage."""
        return render_template("provenance.html")

    @app.route("/xai")
    def xai_page():
        """XAI dashboard — explainability, SHAP attribution, compliance."""
        return render_template("xai.html")

    # ---- OSCAL & Production Audit pages ----

    @app.route("/oscal")
    def oscal_page():
        """OSCAL ecosystem — validation, catalog, format conversion (D302-D306)."""
        return render_template("oscal.html")

    @app.route("/prod-audit")
    def prod_audit_page():
        """Production readiness audit — 30 checks, 6 categories (D291-D300)."""
        return render_template("prod_audit.html")

    @app.route("/ai-transparency")
    def ai_transparency_page():
        """AI Transparency — OMB M-25-21, M-26-04, NIST AI 600-1, GAO-21-519SP (Phase 48, D307-D315)."""
        return render_template("ai_transparency.html")

    @app.route("/ai-accountability")
    def ai_accountability_page():
        """AI Accountability — oversight, appeals, CAIO, incidents, ethics (Phase 49, D316-D321)."""
        return render_template("ai_accountability.html")

    @app.route("/code-quality")
    def code_quality_page():
        """Code Quality Intelligence — AST analysis, smells, maintainability, runtime feedback (Phase 52, D331-D337)."""
        return render_template("code_quality.html")

    @app.route("/fedramp-20x")
    def fedramp_20x_page():
        """FedRAMP 20x KSI Dashboard — KSI evidence generation, maturity levels, authorization package (Phase 53, D338)."""
        return render_template("fedramp_20x.html")

    @app.route("/evidence")
    def evidence_page():
        """Evidence Collection — universal evidence auto-collection across all frameworks (Phase 56, D347)."""
        from tools.compliance.evidence_collector import FRAMEWORK_EVIDENCE_MAP, _get_connection, _table_exists
        stats = {"total_frameworks": len(FRAMEWORK_EVIDENCE_MAP), "required_frameworks": 0, "frameworks": []}
        try:
            conn = _get_connection()
            for fw_id, fw_config in FRAMEWORK_EVIDENCE_MAP.items():
                if fw_config["required"]:
                    stats["required_frameworks"] += 1
                total = 0
                for table_name in fw_config["tables"]:
                    if _table_exists(conn, table_name):
                        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()  # nosec B608 -- table/column names are internal constants, not user input
                        total += row[0]
                stats["frameworks"].append({
                    "id": fw_id,
                    "description": fw_config["description"],
                    "required": fw_config["required"],
                    "total_records": total,
                })
            conn.close()
        except Exception:
            pass
        return render_template("evidence.html", stats=stats)

    @app.route("/lineage")
    def lineage_page():
        """Artifact Lineage — unified DAG visualization of digital thread, provenance, audit trail, SBOM (Phase 56, D348)."""
        return render_template("lineage.html")

    # ---- Database helper ----
    def _get_db():
        conn = get_connection(db_path=str(DB_PATH))
        return conn

    # ---- CPMP / Proposals / GovCon Pages (D-CHILD-6: guarded) ----
    if _HAS_GOVCON:
        _register_govcon_pages(app, _get_db)

    # ---- Phase 61: Orchestration Dashboard ----

    @app.route("/orchestration")
    def orchestration_dashboard():
        """Real-time multi-agent orchestration dashboard — agent grid, DAG, mailbox (Phase 61)."""
        return render_template("orchestration/dashboard.html")

    # ---- Digital Program Twin — Simulation Dashboard ----

    @app.route("/simulation")
    def simulation_page():
        """Digital Program Twin — 6-dimension what-if simulation, Monte Carlo, COA analysis."""
        stats = {"total_scenarios": 0, "running": 0, "completed": 0, "monte_carlo_runs": 0, "coas_generated": 0}
        scenarios = []
        try:
            conn = _get_db()
            stats["total_scenarios"] = conn.execute("SELECT COUNT(*) FROM simulation_scenarios WHERE status != 'archived'").fetchone()[0]
            stats["running"] = conn.execute("SELECT COUNT(*) FROM simulation_scenarios WHERE status = 'running'").fetchone()[0]
            stats["completed"] = conn.execute("SELECT COUNT(*) FROM simulation_scenarios WHERE status = 'completed'").fetchone()[0]
            stats["monte_carlo_runs"] = conn.execute("SELECT COUNT(*) FROM monte_carlo_runs").fetchone()[0]
            stats["coas_generated"] = conn.execute("SELECT COUNT(*) FROM coa_definitions").fetchone()[0]
            scenarios = [dict(r) for r in conn.execute(
                "SELECT id, project_id, scenario_name, scenario_type, status, created_at, completed_at "
                "FROM simulation_scenarios WHERE status != 'archived' ORDER BY created_at DESC LIMIT 100"
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template("simulation.html", stats=stats, scenarios=scenarios)

    # ------------------------------------------------------------------
    # Phase 73: Cloud Migration Security Pages (5 new dashboard pages)
    # ------------------------------------------------------------------

    @app.route("/security-scan")
    def security_scan_page():
        """Security Scan Results — multi-layer scanning dashboard (SAST, dependency, secret, container)."""
        return render_template("security_scan.html")

    @app.route("/migration")
    def migration_page():
        """Application Migration Tracker — 7R strategy assessment with compliance impact scoring."""
        return render_template("migration.html")

    @app.route("/sbd")
    def sbd_page():
        """CISA Secure by Design Assessment — 8-pillar assessment with automated gating."""
        return render_template("sbd.html")

    @app.route("/pr-intel")
    def pr_intel_page():
        """PR Intelligence — compliance drift detection at the pull request level."""
        return render_template("pr_intel.html")

    @app.route("/iac")
    def iac_page():
        """IaC Gallery — STIG-hardened Infrastructure as Code for multi-cloud IL2-IL6."""
        return render_template("iac.html")

    @app.route("/cato")
    def cato_page():
        """Continuous ATO — real-time ATO health score and evidence freshness."""
        return render_template("cato.html")

    @app.route("/control-inheritance")
    def control_inheritance_page():
        """Control Inheritance Visualizer — CSP vs customer responsibility mapping."""
        return render_template("control_inheritance.html")

    @app.route("/mosa")
    def mosa_page():
        """MOSA Compliance — 10 U.S.C. §4401 modular open systems approach assessment."""
        return render_template("mosa.html")

    @app.route("/api/mosa/summary")
    def api_mosa_summary():
        """MOSA summary — module coupling, cohesion, circular dependency data."""
        try:
            from tools.compliance.mosa_assessor import get_latest_assessment
            data = get_latest_assessment()
            return jsonify(data)
        except Exception:
            return jsonify({
                "modules": [],
                "summary": {
                    "total_modules": 0,
                    "avg_coupling": 0,
                    "avg_cohesion": 0,
                    "circular_deps": 0,
                },
            })

    @app.route("/migration-cost")
    def migration_cost_page():
        """Migration Cost Estimator — 7R ROI calculator with compliance cost."""
        return render_template("migration_cost.html")

    @app.route("/compliance")
    def compliance_page():
        """Compliance Hub — unified posture across all compliance modules."""
        return render_template("compliance.html")

    @app.route("/api/compliance/posture")
    def api_compliance_posture():
        """Return aggregate compliance posture for the hub page."""
        conn = _get_db()
        result = {
            "controls_implemented": 0, "open_poams": 0,
            "cat1_findings": 0, "ato_status": "--", "frameworks": []
        }
        try:
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM ssp_controls WHERE implementation_status = 'implemented'"
                ).fetchone()
                result["controls_implemented"] = row["cnt"]
            except Exception:
                pass
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM poam_items WHERE status NOT IN ('completed', 'closed')"
                ).fetchone()
                result["open_poams"] = row["cnt"]
            except Exception:
                pass
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM stig_findings WHERE severity = 'CAT I' AND status = 'Open'"
                ).fetchone()
                result["cat1_findings"] = row["cnt"]
            except Exception:
                pass
            try:
                row = conn.execute(
                    "SELECT authorization_status FROM ato_packages ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                result["ato_status"] = row["authorization_status"] if row else "Not Started"
            except Exception:
                result["ato_status"] = "Not Started"
            # Framework summaries
            frameworks = [
                ("NIST 800-53", "ssp_controls", "implementation_status", "implemented"),
                ("FedRAMP", "ssp_controls", "implementation_status", "implemented"),
            ]
            for name, table, col, val in frameworks:
                try:
                    total = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()["cnt"]  # nosec B608 -- table/column names are internal constants, not user input
                    impl = conn.execute(
                        f"SELECT COUNT(*) as cnt FROM {table} WHERE {col} = ?", (val,)  # nosec B608 -- table/column names are internal constants, not user input
                    ).fetchone()["cnt"]
                    result["frameworks"].append({
                        "name": name, "total": total, "implemented": impl, "status": "Active"
                    })
                except Exception:
                    pass
        finally:
            conn.close()
        return jsonify(result)

    @app.route("/api/compliance/unified-posture")
    def api_compliance_unified_posture():
        """Unified compliance posture from PDC + NDC + SDC with NIST 800-53 heatmap."""
        import sqlite3 as _sqlite3

        NIST_FAMILIES = [
            ("AC", "Access Control"), ("AU", "Audit & Accountability"),
            ("AT", "Awareness & Training"), ("CA", "Assessment & Authorization"),
            ("CM", "Configuration Mgmt"), ("CP", "Contingency Planning"),
            ("IA", "ID & Authentication"), ("IR", "Incident Response"),
            ("MA", "Maintenance"), ("MP", "Media Protection"),
            ("PE", "Physical & Environmental"), ("PL", "Planning"),
            ("PM", "Program Management"), ("PS", "Personnel Security"),
            ("PT", "PII Processing"), ("RA", "Risk Assessment"),
            ("SA", "System & Services Acq"), ("SC", "System & Comms Protection"),
            ("SI", "System & Info Integrity"), ("SR", "Supply Chain Risk Mgmt"),
        ]

        result = {
            "sdc": {
                "available": False, "design_count": 0, "risk_score": None,
                "posture_grade": "--", "open_threats": 0, "controls_implemented": 0,
                "nist_coverage_pct": 0, "nist_families": {},
            },
            "ndc": {
                "available": False, "topology_count": 0, "cat1_open": 0,
                "cat2_open": 0, "cat3_open": 0, "total_findings": 0, "pass_rate": 0,
            },
            "pdc": {
                "available": False, "pipeline_count": 0, "slsa_level": "--",
                "ssdf_pct": 0, "owasp_pct": 0, "total_findings": 0,
            },
            "heatmap": [],
        }

        sdc_family_pcts: dict = {}
        main_family_pcts: dict = {}

        # --- SDC: security_canvas.db ---
        sdc_db = BASE_DIR / "data" / "security_canvas.db"
        if sdc_db.exists():
            try:
                with _sqlite3.connect(str(sdc_db)) as sc:
                    sc.row_factory = _sqlite3.Row
                    result["sdc"]["design_count"] = sc.execute(
                        "SELECT COUNT(*) FROM security_designs"
                    ).fetchone()[0]
                    result["sdc"]["available"] = result["sdc"]["design_count"] > 0
                    row = sc.execute(
                        "SELECT risk_score, posture_grade FROM sc_assessments ORDER BY ran_at DESC LIMIT 1"
                    ).fetchone()
                    if row:
                        result["sdc"]["risk_score"] = row["risk_score"]
                        result["sdc"]["posture_grade"] = row["posture_grade"]
                    result["sdc"]["open_threats"] = sc.execute(
                        "SELECT COUNT(*) FROM sc_threats WHERE status != 'mitigated'"
                    ).fetchone()[0]
                    for family, _ in NIST_FAMILIES:
                        total = sc.execute(
                            "SELECT COUNT(*) FROM sc_controls WHERE control_family = ?", (family,)
                        ).fetchone()[0]
                        impl = sc.execute(
                            "SELECT COUNT(*) FROM sc_controls WHERE control_family = ?"
                            " AND implementation_status IN ('implemented','tested')", (family,)
                        ).fetchone()[0]
                        if total > 0:
                            sdc_family_pcts[family] = round(impl / total * 100)
                            result["sdc"]["controls_implemented"] += impl
                    if sdc_family_pcts:
                        result["sdc"]["nist_coverage_pct"] = round(
                            sum(sdc_family_pcts.values()) / len(sdc_family_pcts)
                        )
                        result["sdc"]["nist_families"] = sdc_family_pcts
            except Exception:
                pass

        # --- NDC: network_canvas.db ---
        ndc_db = BASE_DIR / "data" / "network_canvas.db"
        if ndc_db.exists():
            try:
                with _sqlite3.connect(str(ndc_db)) as nc:
                    nc.row_factory = _sqlite3.Row
                    result["ndc"]["topology_count"] = nc.execute(
                        "SELECT COUNT(*) FROM topologies"
                    ).fetchone()[0]
                    result["ndc"]["available"] = result["ndc"]["topology_count"] > 0
                    result["ndc"]["cat1_open"] = nc.execute(
                        "SELECT COUNT(*) FROM nc_compliance_findings"
                        " WHERE severity = 'CAT1' AND status = 'open'"
                    ).fetchone()[0]
                    result["ndc"]["cat2_open"] = nc.execute(
                        "SELECT COUNT(*) FROM nc_compliance_findings"
                        " WHERE severity = 'CAT2' AND status = 'open'"
                    ).fetchone()[0]
                    result["ndc"]["cat3_open"] = nc.execute(
                        "SELECT COUNT(*) FROM nc_compliance_findings"
                        " WHERE severity = 'CAT3' AND status = 'open'"
                    ).fetchone()[0]
                    total_f = nc.execute(
                        "SELECT COUNT(*) FROM nc_compliance_findings"
                    ).fetchone()[0]
                    remediated_f = nc.execute(
                        "SELECT COUNT(*) FROM nc_compliance_findings WHERE status = 'remediated'"
                    ).fetchone()[0]
                    result["ndc"]["total_findings"] = total_f
                    result["ndc"]["pass_rate"] = (
                        round(remediated_f / total_f * 100) if total_f > 0 else 0
                    )
            except Exception:
                pass

        # --- PDC: pipeline_canvas.db ---
        pdc_db = BASE_DIR / "data" / "pipeline_canvas.db"
        if pdc_db.exists():
            try:
                with _sqlite3.connect(str(pdc_db)) as pc:
                    pc.row_factory = _sqlite3.Row
                    result["pdc"]["pipeline_count"] = pc.execute(
                        "SELECT COUNT(*) FROM pipelines"
                    ).fetchone()[0]
                    result["pdc"]["available"] = result["pdc"]["pipeline_count"] > 0
                    slsa_row = pc.execute(
                        "SELECT slsa_level FROM pc_snippets WHERE slsa_level IS NOT NULL"
                        " GROUP BY slsa_level ORDER BY COUNT(*) DESC LIMIT 1"
                    ).fetchone()
                    if slsa_row:
                        result["pdc"]["slsa_level"] = slsa_row["slsa_level"]
                    chk = pc.execute(
                        "SELECT findings_json FROM pc_compliance_checks ORDER BY ran_at DESC LIMIT 1"
                    ).fetchone()
                    if chk and chk["findings_json"]:
                        try:
                            findings = json.loads(chk["findings_json"])
                            result["pdc"]["total_findings"] = (
                                len(findings) if isinstance(findings, list) else 0
                            )
                        except Exception:
                            pass
                    # OWASP coverage — derive from node types in all pipelines
                    try:
                        from tools.pipeline.constants import compute_owasp_coverage
                        all_node_types: list = []
                        for g_row in pc.execute("SELECT graph_json FROM pipelines").fetchall():
                            try:
                                g = json.loads(g_row["graph_json"] or "{}")
                                for n in g.get("nodes", []):
                                    t = n.get("type") or n.get("data", {}).get("type", "")
                                    if t:
                                        all_node_types.append(t)
                            except Exception:
                                pass
                        if all_node_types:
                            owasp = compute_owasp_coverage(all_node_types)
                            result["pdc"]["owasp_pct"] = int(owasp.get("coverage_pct", 0))
                    except Exception:
                        pass
                    # SSDF coverage — remediation rate of SSDF framework findings
                    try:
                        tables_row = pc.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='pc_compliance_findings'"
                        ).fetchone()
                        if tables_row and tables_row[0] > 0:
                            ssdf_total = pc.execute(
                                "SELECT COUNT(*) FROM pc_compliance_findings WHERE framework LIKE 'SSDF%'"
                            ).fetchone()[0]
                            ssdf_rem = pc.execute(
                                "SELECT COUNT(*) FROM pc_compliance_findings"
                                " WHERE framework LIKE 'SSDF%' AND status = 'remediated'"
                            ).fetchone()[0]
                            if ssdf_total > 0:
                                result["pdc"]["ssdf_pct"] = round(ssdf_rem / ssdf_total * 100)
                            else:
                                # No findings means passing — treat as 100%
                                result["pdc"]["ssdf_pct"] = 100
                    except Exception:
                        pass
            except Exception:
                pass

        # --- NIST 800-53 heatmap from icdev.db project_controls ---
        try:
            with get_connection(db_path=str(DB_PATH)) as mc:
                for family, _ in NIST_FAMILIES:
                    total = mc.execute(
                        "SELECT COUNT(*) as cnt FROM project_controls WHERE control_id LIKE ?",
                        (f"{family}-%",),
                    ).fetchone()["cnt"]
                    impl = mc.execute(
                        "SELECT COUNT(*) as cnt FROM project_controls"
                        " WHERE control_id LIKE ? AND implementation_status = 'implemented'",
                        (f"{family}-%",),
                    ).fetchone()["cnt"]
                    if total > 0:
                        main_family_pcts[family] = round(impl / total * 100)
        except Exception:
            pass

        for family, name in NIST_FAMILIES:
            sdc_pct = sdc_family_pcts.get(family)
            main_pct = main_family_pcts.get(family)
            vals = [v for v in [sdc_pct, main_pct] if v is not None]
            avg_pct = round(sum(vals) / len(vals)) if vals else 0
            result["heatmap"].append({
                "family": family,
                "name": name,
                "sdc_pct": sdc_pct,
                "main_pct": main_pct,
                "avg_pct": avg_pct,
            })

        return jsonify(result)

    @app.route("/api/compliance/evidence-chain")
    def api_compliance_evidence_chain():
        """Evidence chain summary — PDC/NDC/SDC audit trail mapped to NIST 800-53."""
        since_hours = float(flask_request.args.get("since_hours", 168))  # 7 days default
        project_id = flask_request.args.get("project_id") or None
        try:
            from tools.compliance.evidence_chain import build_evidence_chain
            chain = build_evidence_chain(
                project_id=project_id,
                since_hours=since_hours,
            )
            # Return lightweight summary (drop full event list for dashboard perf)
            summary = {
                "chain_id": chain["chain_id"],
                "built_at": chain["built_at"],
                "since_hours": since_hours,
                "total_events": chain["timeline"]["total_events"],
                "first_event": chain["timeline"]["first_event"],
                "last_event": chain["timeline"]["last_event"],
                "sources": chain["sources"],
                "coverage": chain["coverage"],
                "evidence_types": chain["evidence_types"],
                "gate": chain["gate"],
                "recent_events": chain["timeline"]["events"][-10:],
            }
            return jsonify(summary)
        except Exception as exc:
            return jsonify({"error": str(exc), "total_events": 0, "sources": {}}), 200

    @app.route("/compliance-debt")
    def compliance_debt_page():
        """Compliance Debt Burndown — POAM, control, and STIG debt tracking."""
        return render_template("compliance_debt.html")

    @app.route("/stig-manager")
    def stig_manager_page():
        """STIG Benchmark Manager — import, track, and assess DISA STIG findings."""
        return render_template("stig_manager.html")

    @app.route("/ato-package")
    def ato_package_page():
        """ATO Package Builder — wizard to assemble SSP/SAR/POAM/SBOM package."""
        return render_template("ato_package.html")

    @app.route("/analytics")
    def analytics_page():
        """Compliance Funnel Analytics — ATO pipeline funnel, time-series, child app telemetry."""
        return render_template("analytics.html")

    @app.route("/api/simulation/scenarios", methods=["POST"])
    def api_simulation_create():
        """Create a new simulation scenario."""
        data = flask_request.get_json(silent=True) or {}
        try:
            from tools.simulation.simulation_engine import create_scenario
            result = create_scenario(
                project_id=data.get("project_id", ""),
                scenario_name=data.get("scenario_name", ""),
                scenario_type=data.get("scenario_type", "what_if"),
                modifications=data.get("modifications", {}),
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/scenarios/<scenario_id>/run", methods=["POST"])
    def api_simulation_run(scenario_id):
        """Run simulation across all 6 dimensions."""
        try:
            from tools.simulation.simulation_engine import run_simulation
            result = run_simulation(scenario_id)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/scenarios/<scenario_id>/summary")
    def api_simulation_summary(scenario_id):
        """Get scenario summary with results and MC runs."""
        try:
            from tools.simulation.scenario_manager import get_scenario_summary
            result = get_scenario_summary(scenario_id)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/scenarios/<scenario_id>/monte-carlo", methods=["POST"])
    def api_simulation_monte_carlo(scenario_id):
        """Run Monte Carlo estimation for a dimension."""
        data = flask_request.get_json(silent=True) or {}
        try:
            from tools.simulation.monte_carlo import run_monte_carlo
            result = run_monte_carlo(
                scenario_id=scenario_id,
                dimension=data.get("dimension", "schedule"),
                iterations=data.get("iterations", 10000),
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/scenarios/<scenario_id>/fork", methods=["POST"])
    def api_simulation_fork(scenario_id):
        """Fork a scenario with optional modifications."""
        data = flask_request.get_json(silent=True) or {}
        try:
            from tools.simulation.scenario_manager import fork_scenario
            result = fork_scenario(
                scenario_id=scenario_id,
                new_name=data.get("new_name", "Forked scenario"),
                additional_modifications=data.get("modifications"),
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/scenarios/<scenario_id>/coas")
    def api_simulation_coas(scenario_id):
        """Get COAs linked to a scenario."""
        try:
            conn = _get_db()
            coas = [dict(r) for r in conn.execute(
                "SELECT * FROM coa_definitions WHERE simulation_scenario_id = ? ORDER BY coa_type",
                (scenario_id,),
            ).fetchall()]
            conn.close()
            return jsonify({"coas": coas})
        except Exception as exc:
            return jsonify({"coas": [], "error": str(exc)})

    @app.route("/api/simulation/coas/<coa_id>/select", methods=["POST"])
    def api_simulation_coa_select(coa_id):
        """Select a COA."""
        try:
            conn = _get_db()
            conn.execute("UPDATE coa_definitions SET status = 'selected', selected_at = datetime('now') WHERE id = ?", (coa_id,))
            conn.commit()
            conn.close()
            return jsonify({"status": "selected", "coa_id": coa_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/coas/<coa_id>/reject", methods=["POST"])
    def api_simulation_coa_reject(coa_id):
        """Reject a COA."""
        try:
            conn = _get_db()
            conn.execute("UPDATE coa_definitions SET status = 'rejected' WHERE id = ?", (coa_id,))
            conn.commit()
            conn.close()
            return jsonify({"status": "rejected", "coa_id": coa_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/nlq", methods=["POST"])
    def api_simulation_nlq():
        """Parse a natural language query into simulation modifications."""
        data = flask_request.get_json(silent=True) or {}
        query = data.get("query", "")
        if not query:
            return jsonify({"error": "query is required"}), 400
        try:
            from tools.simulation.query_parser import parse_query
            result = parse_query(query)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/nlq/run", methods=["POST"])
    def api_simulation_nlq_run():
        """Parse NLQ, create scenario, and run simulation in one call."""
        data = flask_request.get_json(silent=True) or {}
        query = data.get("query", "")
        project_id = data.get("project_id", "")
        if not query or not project_id:
            return jsonify({"error": "query and project_id are required"}), 400
        try:
            from tools.simulation.query_parser import parse_query
            from tools.simulation.simulation_engine import create_scenario, run_simulation
            parsed = parse_query(query)
            scenario = create_scenario(
                project_id=project_id,
                scenario_name=parsed["scenario_name"],
                scenario_type=parsed["scenario_type"],
                modifications=parsed["modifications"],
            )
            sim_result = None
            try:
                sim_result = run_simulation(scenario["scenario_id"])
            except Exception:
                pass  # simulation may fail if no SysML data; return parsed+scenario anyway
            return jsonify({
                "parsed": parsed,
                "scenario": scenario,
                "simulation": sim_result,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/cascade", methods=["POST"])
    def api_simulation_cascade():
        """Run cascade analysis through the simulation KG."""
        data = flask_request.get_json(silent=True) or {}
        project_id = data.get("project_id", "")
        trigger = data.get("trigger", "")
        node_id = data.get("node_id")
        max_depth = data.get("max_depth", 7)
        max_width = data.get("max_width", 10)
        if not project_id:
            return jsonify({"error": "project_id is required"}), 400
        try:
            from tools.simulation.cascade_bridge import run_cascade
            start_ids = [node_id] if node_id else None
            result = run_cascade(
                project_id=project_id,
                start_node_ids=start_ids,
                trigger_text=trigger or None,
                max_depth=max_depth,
                max_width=max_width,
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/risk/composite", methods=["POST"])
    def api_simulation_risk_composite():
        """Calculate composite program risk score."""
        data = flask_request.get_json(silent=True) or {}
        project_id = data.get("project_id", "")
        if not project_id:
            return jsonify({"error": "project_id is required"}), 400
        try:
            from tools.simulation.risk_monitor import calculate_composite_risk
            result = calculate_composite_risk(project_id)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/simulation/risk/cpars", methods=["POST"])
    def api_simulation_risk_cpars():
        """Calculate CPARS risk score for a contract."""
        data = flask_request.get_json(silent=True) or {}
        contract_id = data.get("contract_id", "")
        if not contract_id:
            return jsonify({"error": "contract_id is required"}), 400
        try:
            from tools.simulation.risk_monitor import calculate_cpars_risk
            result = calculate_cpars_risk(contract_id)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    # ---- Phase 63: Industry Research Engine ----

    @app.route("/research")
    def research_page():
        """Industry Research Engine — vertical research sessions, scored dossiers (Phase 63, D-RES-1 through D-RES-13)."""
        stats = {"total_sessions": 0, "active_sessions": 0, "verticals_loaded": 0, "dossiers_generated": 0}
        sessions = []
        verticals = []
        try:
            conn = _get_db()
            stats["total_sessions"] = conn.execute("SELECT COUNT(*) FROM research_sessions").fetchone()[0]
            stats["active_sessions"] = conn.execute(
                "SELECT COUNT(*) FROM research_sessions WHERE status NOT IN ('archived', 'child_app_triggered')"
            ).fetchone()[0]
            stats["verticals_loaded"] = conn.execute("SELECT COUNT(*) FROM research_verticals").fetchone()[0]
            stats["dossiers_generated"] = conn.execute("SELECT COUNT(*) FROM research_dossiers").fetchone()[0]
            sessions = [dict(r) for r in conn.execute(
                """SELECT s.*, (SELECT COUNT(*) FROM research_challenges c WHERE c.session_id = s.id) as challenge_count
                   FROM research_sessions s ORDER BY s.created_at DESC LIMIT 50"""
            ).fetchall()]
            verticals = [dict(r) for r in conn.execute(
                "SELECT * FROM research_verticals ORDER BY name"
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template("research.html", stats=stats, sessions=sessions, verticals=verticals)

    @app.route("/api/research/sessions", methods=["POST"])
    def api_research_create_session():
        """Create a new research session."""
        data = flask_request.get_json(silent=True) or {}
        try:
            from tools.research.session_manager import create_session
            result = create_session(
                name=data.get("name", ""),
                vertical_slug=data.get("vertical", ""),
                focus_areas=data.get("focus_areas", []),
            )
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/sessions")
    def api_research_list_sessions():
        """List research sessions."""
        try:
            from tools.research.session_manager import list_sessions
            status = flask_request.args.get("status")
            return jsonify(list_sessions(status=status))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/sessions/<session_id>/run", methods=["POST"])
    def api_research_run_pipeline(session_id):
        """Run research pipeline for a session."""
        try:
            from tools.research.research_engine import run_pipeline
            result = run_pipeline(session_id=session_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/sessions/<session_id>/status")
    def api_research_session_status(session_id):
        """Get session status."""
        try:
            from tools.research.research_engine import get_status
            return jsonify(get_status(session_id=session_id))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/sessions/<session_id>/dossier")
    def api_research_session_dossier(session_id):
        """Get dossier by session ID."""
        try:
            from tools.research.dossier_generator import get_dossier
            return jsonify(get_dossier(session_id=session_id))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/sessions/<session_id>/run-stage", methods=["POST"])
    def api_research_run_stage(session_id):
        """Run a single pipeline stage for a session."""
        data = flask_request.get_json(silent=True) or {}
        stage = data.get("stage", "").upper()
        if not stage:
            return jsonify({"ok": False, "error": "Missing 'stage' parameter"}), 400
        try:
            from tools.research.research_engine import run_stage
            result = run_stage(session_id=session_id, stage=stage)
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/sessions/<session_id>/regulatory")
    def api_research_regulatory_landscape(session_id):
        """Get regulatory landscape for a session."""
        try:
            from tools.research.regulatory_mapper import get_regulatory_landscape
            return jsonify(get_regulatory_landscape(session_id=session_id))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/sessions/<session_id>/retry", methods=["POST"])
    def api_research_retry_session(session_id):
        """Retry a failed research session pipeline."""
        try:
            import threading
            from tools.research.research_engine import run_pipeline
            t = threading.Thread(target=run_pipeline, kwargs={"session_id": session_id}, daemon=True)
            t.start()
            return jsonify({"ok": True, "message": "Pipeline retry started"})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/dossiers/<dossier_id>")
    def api_research_get_dossier(dossier_id):
        """Get a dossier by dossier ID."""
        try:
            from tools.research.dossier_generator import get_dossier
            return jsonify(get_dossier(dossier_id=dossier_id))
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/dossiers/<dossier_id>/review", methods=["POST"])
    def api_research_review_dossier(dossier_id):
        """Review a dossier."""
        data = flask_request.get_json(silent=True) or {}
        try:
            from tools.research.dossier_generator import review_dossier
            result = review_dossier(
                dossier_id=dossier_id,
                reviewer=data.get("reviewer", "dashboard"),
                status=data.get("decision", "approved"),
                review_notes=data.get("notes", ""),
            )
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/verticals")
    def api_research_list_verticals():
        """List available verticals."""
        try:
            from tools.research.vertical_loader import list_verticals
            return jsonify(list_verticals())
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/research/verticals/load", methods=["POST"])
    def api_research_load_verticals():
        """Load verticals from config files into DB."""
        try:
            from tools.research.vertical_loader import load_verticals_to_db
            result = load_verticals_to_db()
            return jsonify(result)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ---- Phase 64: RAG Knowledge Search ----

    @app.route("/knowledge-search")
    def knowledge_search_page():
        """RAG Knowledge Search — natural language search across all ICDEV™ knowledge (Phase 64, D-RAG-1)."""
        status = None
        recent_searches = []
        source_types = []
        try:
            from tools.rag.ingestion_manager import get_status as rag_get_status
            from tools.rag.source_registry import SOURCE_REGISTRY
            status = rag_get_status()
            source_types = sorted(SOURCE_REGISTRY.keys())
        except Exception:
            pass
        try:
            conn = _get_db()
            recent_searches = [dict(r) for r in conn.execute(
                "SELECT * FROM rag_retrieval_log ORDER BY created_at DESC LIMIT 20"
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template(
            "rag/knowledge_search.html",
            status=status,
            recent_searches=recent_searches,
            source_types=source_types,
        )

    @app.route("/api/rag/search", methods=["POST"])
    def api_rag_search():
        """RAG search API endpoint."""
        data = flask_request.get_json(silent=True) or {}
        query = data.get("query", "")
        if not query:
            return jsonify({"error": "query is required", "results": []}), 400
        try:
            from tools.rag.retriever import RAGRetriever
            retriever = RAGRetriever()
            top_k = data.get("top_k", 5)
            source_types = None
            if data.get("source_type"):
                source_types = [data["source_type"]]
            results = retriever.search(
                query=query,
                top_k=top_k,
                source_types=source_types,
            )
            return jsonify({
                "classification": DEFAULT_CLASSIFICATION,
                "query": query,
                "results_count": len(results),
                "results": [r.to_dict() for r in results],
            })
        except ImportError:
            return jsonify({"error": "RAG subsystem not available", "results": []}), 503
        except Exception as e:
            return jsonify({"error": str(e), "results": []}), 500

    @app.route("/api/rag/status")
    def api_rag_status():
        """RAG status API endpoint."""
        try:
            from tools.rag.ingestion_manager import get_status as rag_get_status
            return jsonify(rag_get_status())
        except ImportError:
            return jsonify({"error": "RAG subsystem not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ---- Knowledge Graph Dashboard (D-KARL-1 through D-KARL-4) ----

    @app.route("/knowledge-graph")
    def knowledge_graph_page():
        """Knowledge Graph — entity extraction, GraphRAG retrieval, insights."""
        stats = None
        graphs = []
        recent_queries = []
        try:
            conn = _get_db()
            # Stats
            row = conn.execute(
                "SELECT COUNT(*) as cnt, COALESCE(SUM(entity_count),0) as nodes, "
                "COALESCE(SUM(edge_count),0) as edges FROM kg_graphs"
            ).fetchone()
            query_count = 0
            try:
                query_count = conn.execute("SELECT COUNT(*) FROM kg_retrieval_log").fetchone()[0]
            except Exception:
                pass
            stats = {
                "graph_count": row[0] if row else 0,
                "total_nodes": row[1] if row else 0,
                "total_edges": row[2] if row else 0,
                "recent_queries": query_count,
            }
            # Graph list
            rows = conn.execute(
                "SELECT id, project_id, name, entity_count, edge_count, created_at "
                "FROM kg_graphs ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            graphs = [dict(r) for r in rows]
            # Recent retrieval log
            try:
                qrows = conn.execute(
                    "SELECT query_hash, profile, node_count, top_score, duration_ms, created_at "
                    "FROM kg_retrieval_log ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
                recent_queries = [dict(r) for r in qrows]
            except Exception:
                pass
            conn.close()
        except Exception:
            pass
        return render_template(
            "knowledge_graph.html",
            stats=stats,
            graphs=graphs,
            recent_queries=recent_queries,
        )

    @app.route("/api/knowledge-graph/search", methods=["POST"])
    def api_knowledge_graph_search():
        """GraphRAG search API endpoint."""
        data = flask_request.get_json(silent=True) or {}
        query = data.get("query", "")
        if not query:
            return jsonify({"error": "query is required"}), 400
        try:
            from tools.knowledge_graph.graph_rag import retrieve
            result = retrieve(
                query=query,
                profile=data.get("profile"),
                top_k=data.get("top_k", 10),
            )
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "Knowledge graph module not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/knowledge-graph/graph/<graph_id>")
    def api_knowledge_graph_detail(graph_id):
        """Get graph detail with nodes and edges."""
        try:
            from tools.knowledge_graph.text_network import get_graph
            result = get_graph(graph_id)
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "Knowledge graph module not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/knowledge-graph/insights/<graph_id>")
    def api_knowledge_graph_insights(graph_id):
        """Get graph insights (summary, orphans, components)."""
        try:
            from tools.knowledge_graph.insight_generator import graph_summary
            result = graph_summary(graph_id)
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "Insight generator not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/knowledge-graph/bridge-gaps/<graph_id>")
    def api_knowledge_graph_bridge_gaps(graph_id):
        """Get bridge gaps between disconnected clusters."""
        try:
            from tools.knowledge_graph.insight_generator import find_bridge_gaps
            result = find_bridge_gaps(graph_id)
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "Insight generator not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/knowledge-graph/questions/<graph_id>")
    def api_knowledge_graph_questions(graph_id):
        """Generate research questions from graph structure."""
        try:
            from tools.knowledge_graph.insight_generator import generate_questions
            result = generate_questions(graph_id, use_llm=False)
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "Insight generator not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/knowledge-graph/orphans/<graph_id>")
    def api_knowledge_graph_orphans(graph_id):
        """Find orphan nodes with zero edges."""
        try:
            from tools.knowledge_graph.insight_generator import find_orphan_nodes
            result = find_orphan_nodes(graph_id)
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "Insight generator not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/knowledge-graph/compliance-build", methods=["POST"])
    def api_knowledge_graph_compliance_build():
        """Build compliance crosswalk knowledge graph."""
        data = flask_request.get_json(silent=True) or {}
        try:
            from tools.knowledge_graph.compliance_graph import build_compliance_graph
            result = build_compliance_graph(project_id=data.get("project_id"))
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "Compliance graph module not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/knowledge-graph/ingest", methods=["POST"])
    def api_knowledge_graph_ingest():
        """Ingest a document or table into the knowledge graph."""
        data = flask_request.get_json(silent=True) or {}
        source_table = data.get("source_table")
        project_id = data.get("project_id", "")
        if not source_table:
            return jsonify({"error": "source_table is required"}), 400
        try:
            from tools.knowledge_graph.ingester import ingest_from_table
            result = ingest_from_table(source_table, project_id=project_id)
            return jsonify(result)
        except ImportError:
            return jsonify({"error": "Ingester module not available"}), 503
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ---- Phase 64 Extension: Fine-Tuning Dashboard ----

    @app.route("/finetune")
    def finetune_overview_page():
        """Fine-Tuning overview — stats, GPU status, recent jobs, active overrides (D-FT-1 through D-FT-22)."""
        stats = {"datasets": 0, "total_jobs": 0, "active_jobs": 0, "model_versions": 0,
                 "promoted_models": 0, "active_overrides": 0, "evaluations": 0}
        recent_jobs = []
        active_overrides = []
        promotions = []
        try:
            conn = _get_db()
            stats["datasets"] = conn.execute("SELECT COUNT(*) FROM ft_datasets").fetchone()[0]
            stats["total_jobs"] = conn.execute("SELECT COUNT(*) FROM ft_training_jobs").fetchone()[0]
            stats["active_jobs"] = conn.execute(
                "SELECT COUNT(*) FROM ft_training_jobs WHERE status IN ('pending','preparing','training','exporting','evaluating')"
            ).fetchone()[0]
            stats["model_versions"] = conn.execute("SELECT COUNT(*) FROM ft_model_versions").fetchone()[0]
            stats["promoted_models"] = conn.execute(
                "SELECT COUNT(*) FROM ft_model_versions WHERE status = 'promoted'"
            ).fetchone()[0]
            stats["active_overrides"] = conn.execute(
                "SELECT COUNT(*) FROM ft_active_models WHERE deactivated_at IS NULL"
            ).fetchone()[0]
            stats["evaluations"] = conn.execute("SELECT COUNT(*) FROM ft_evaluations").fetchone()[0]
            recent_jobs = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_training_jobs ORDER BY created_at DESC LIMIT 10"
            ).fetchall()]
            active_overrides = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_active_models WHERE deactivated_at IS NULL ORDER BY activated_at DESC"
            ).fetchall()]
            promotions = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_promotion_log ORDER BY created_at DESC LIMIT 10"
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template("finetune/index.html", stats=stats, recent_jobs=recent_jobs,
                               active_overrides=active_overrides, promotions=promotions)

    @app.route("/finetune/datasets")
    def finetune_datasets_page():
        """Fine-Tuning datasets — versioned training data collections."""
        datasets = []
        try:
            conn = _get_db()
            datasets = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_datasets ORDER BY updated_at DESC"
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template("finetune/datasets.html", datasets=datasets)

    @app.route("/finetune/datasets/<dataset_id>")
    def finetune_dataset_detail_page(dataset_id):
        """Fine-Tuning dataset detail — examples with labeling controls."""
        dataset = None
        examples = []
        try:
            conn = _get_db()
            row = conn.execute("SELECT * FROM ft_datasets WHERE id = ?", (dataset_id,)).fetchone()
            if row:
                dataset = dict(row)
            examples = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_dataset_examples WHERE dataset_id = ? ORDER BY id DESC LIMIT 200",
                (dataset_id,),
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        if not dataset:
            return render_template("404.html", message="Dataset not found"), 404
        return render_template("finetune/dataset_detail.html", dataset=dataset, examples=examples)

    @app.route("/finetune/label")
    def finetune_label_page():
        """Fine-Tuning bulk labeling — multi-dimensional scoring, batch approve/reject (D-FT-12)."""
        datasets = []
        examples = []
        selected_dataset_id = flask_request.args.get("dataset_id", "")
        try:
            conn = _get_db()
            datasets = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_datasets ORDER BY updated_at DESC"
            ).fetchall()]
            if selected_dataset_id:
                examples = [dict(r) for r in conn.execute(
                    "SELECT * FROM ft_dataset_examples WHERE dataset_id = ? ORDER BY id DESC LIMIT 200",
                    (selected_dataset_id,),
                ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template("finetune/label.html", datasets=datasets, examples=examples,
                               selected_dataset_id=selected_dataset_id)

    @app.route("/finetune/jobs")
    def finetune_jobs_page():
        """Fine-Tuning training jobs — status tracking, loss curves."""
        jobs = []
        try:
            conn = _get_db()
            jobs = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_training_jobs ORDER BY created_at DESC"
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template("finetune/jobs.html", jobs=jobs)

    @app.route("/finetune/jobs/<job_id>")
    def finetune_job_detail_page(job_id):
        """Fine-Tuning job detail — loss curve, hyperparams, events."""
        import json as _json
        job = None
        events = []
        loss_history = []
        try:
            conn = _get_db()
            row = conn.execute("SELECT * FROM ft_training_jobs WHERE id = ?", (job_id,)).fetchone()
            if row:
                job = dict(row)
                try:
                    loss_history = _json.loads(job.get("loss_history", "[]") or "[]")
                except (ValueError, TypeError):
                    loss_history = []
            events = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_training_job_events WHERE job_id = ? ORDER BY created_at DESC",
                (job_id,),
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        if not job:
            return render_template("404.html", message="Training job not found"), 404
        return render_template("finetune/job_detail.html", job=job, events=events, loss_history=loss_history)

    @app.route("/finetune/models")
    def finetune_models_page():
        """Fine-Tuning model versions — eval scores, promotion status."""
        models = []
        try:
            conn = _get_db()
            models = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_model_versions ORDER BY created_at DESC"
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template("finetune/models.html", models=models)

    @app.route("/finetune/models/<model_id>")
    def finetune_model_detail_page(model_id):
        """Fine-Tuning model detail — evaluation history, promotion log."""
        model = None
        evaluations = []
        promotions = []
        try:
            conn = _get_db()
            row = conn.execute("SELECT * FROM ft_model_versions WHERE id = ?", (model_id,)).fetchone()
            if row:
                model = dict(row)
            evaluations = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_evaluations WHERE model_version_id = ? ORDER BY evaluated_at DESC",
                (model_id,),
            ).fetchall()]
            promotions = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_promotion_log WHERE model_version_id = ? ORDER BY created_at DESC",
                (model_id,),
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        if not model:
            return render_template("404.html", message="Model version not found"), 404
        return render_template("finetune/model_detail.html", model=model, evaluations=evaluations, promotions=promotions)

    @app.route("/finetune/evaluate")
    def finetune_evaluate_page():
        """Fine-Tuning evaluations — BLEU, ROUGE-L, perplexity scoring (D-FT-14, D-FT-15)."""
        evaluations = []
        try:
            conn = _get_db()
            evaluations = [dict(r) for r in conn.execute(
                "SELECT * FROM ft_evaluations ORDER BY evaluated_at DESC"
            ).fetchall()]
            conn.close()
        except Exception:
            pass
        return render_template("finetune/evaluate.html", evaluations=evaluations)

    # ── ICDEV™ Pulse — Blog Engine ─────────────────────────────────────


    @app.route("/pulse")
    @require_installed("pulse")
    def pulse():
        """ICDEV™ Pulse — AI-powered blog engine dashboard."""
        try:
            from tools.pulse.db import init_db, query_rows
            init_db()
            posts = query_rows("posts", limit=500)
            by_status = {}
            for p in posts:
                s = p.get("status", "unknown")
                by_status[s] = by_status.get(s, 0) + 1
            stats = {
                "total_posts": len(posts),
                "by_status": by_status,
            }
            # Get recent posts for the table (include quality stats)
            with _get_db() as conn:
                recent = conn.execute(
                    "SELECT id, title, slug, status, word_count, readability_score, "
                    "grammar_score, plagiarism_score, ai_detection_score, tone_score, "
                    "writeguard_passed, capabilities_referenced, "
                    "hero_image_path, generated_video_path, generated_video_method, "
                    "judge_color, judge_composite, judge_combined, "
                    "created_at, updated_at, published_at "
                    "FROM pulse_posts ORDER BY updated_at DESC LIMIT 50"
                ).fetchall()
                recent_posts = [dict(r) for r in recent]
                research_count = conn.execute("SELECT COUNT(*) FROM pulse_research_cache").fetchone()[0]
                cluster_count = conn.execute("SELECT COUNT(*) FROM pulse_topic_clusters").fetchone()[0]
                run_count = conn.execute("SELECT COUNT(*) FROM pulse_schedule_log").fetchone()[0]
            stats["research_entries"] = research_count
            stats["clusters"] = cluster_count
            stats["pipeline_runs"] = run_count
            # Capability catalog stats
            try:
                from tools.pulse.engine.capability_scanner import load_all_capabilities
                stats["capabilities"] = len(load_all_capabilities())
            except Exception:
                stats["capabilities"] = 0
        except Exception:
            recent_posts = []
            stats = {"total_posts": 0, "by_status": {}, "research_entries": 0,
                     "clusters": 0, "pipeline_runs": 0}
        return render_template("pulse.html", posts=recent_posts, stats=stats)


    @app.route("/pulse/post/<post_id>")
    @require_installed("pulse")
    def pulse_post_detail(post_id):
        """ICDEV™ Pulse — Single post detail view."""
        try:
            from tools.pulse.db import get_row
            post = get_row("posts", post_id)
        except Exception:
            post = None
        if not post:
            return render_template("pulse.html", posts=[], stats={},
                                   error=f"Post not found: {post_id}"), 404
        # Render markdown to HTML if body_html is missing
        if post.get("body_markdown") and not post.get("body_html"):
            import re
            md = post["body_markdown"]
            # Convert markdown to basic HTML
            lines = md.split("\n")
            html_parts = []
            in_list = False
            in_code = False
            for line in lines:
                stripped = line.strip()
                # Code blocks
                if stripped.startswith("```"):
                    if in_code:
                        html_parts.append("</code></pre>")
                        in_code = False
                    else:
                        lang = stripped[3:].strip()
                        html_parts.append(f'<pre><code class="language-{lang}">' if lang else "<pre><code>")
                        in_code = True
                    continue
                if in_code:
                    html_parts.append(line.replace("<", "&lt;").replace(">", "&gt;") + "\n")
                    continue
                # Headers
                if stripped.startswith("######"):
                    html_parts.append(f"<h6>{stripped[6:].strip()}</h6>")
                elif stripped.startswith("#####"):
                    html_parts.append(f"<h5>{stripped[5:].strip()}</h5>")
                elif stripped.startswith("####"):
                    html_parts.append(f"<h4>{stripped[4:].strip()}</h4>")
                elif stripped.startswith("###"):
                    html_parts.append(f"<h3>{stripped[3:].strip()}</h3>")
                elif stripped.startswith("##"):
                    html_parts.append(f"<h2>{stripped[2:].strip()}</h2>")
                elif stripped.startswith("# "):
                    html_parts.append(f"<h1>{stripped[1:].strip()}</h1>")
                # List items
                elif stripped.startswith("- ") or stripped.startswith("* "):
                    if not in_list:
                        html_parts.append("<ul>")
                        in_list = True
                    content = stripped[2:]
                    content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
                    content = re.sub(r'\*(.+?)\*', r'<em>\1</em>', content)
                    html_parts.append(f"<li>{content}</li>")
                elif re.match(r'^\d+\.\s', stripped):
                    if not in_list:
                        html_parts.append("<ol>")
                        in_list = True
                    content = re.sub(r'^\d+\.\s', '', stripped)
                    content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
                    content = re.sub(r'\*(.+?)\*', r'<em>\1</em>', content)
                    html_parts.append(f"<li>{content}</li>")
                # Empty line
                elif not stripped:
                    if in_list:
                        html_parts.append("</ul>" if html_parts[-5:] and "<ul>" in "".join(html_parts[-5:]) else "</ol>")
                        in_list = False
                    html_parts.append("")
                # Paragraph
                else:
                    if in_list:
                        html_parts.append("</ul>" if "<ul>" in "".join(html_parts[-10:]) else "</ol>")
                        in_list = False
                    content = stripped
                    content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
                    content = re.sub(r'\*(.+?)\*', r'<em>\1</em>', content)
                    content = re.sub(r'`(.+?)`', r'<code>\1</code>', content)
                    content = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2" style="color:var(--primary);">\1</a>', content)
                    html_parts.append(f"<p>{content}</p>")
            if in_list:
                html_parts.append("</ul>")
            if in_code:
                html_parts.append("</code></pre>")
            post["body_html"] = "\n".join(html_parts)
        return render_template("pulse_post.html", post=post)


    # ── Pulse API Endpoints ──────────────────────────────────────────


    @app.route("/api/pulse/posts")
    @require_installed("pulse")
    def api_pulse_list_posts():
        """List all Pulse posts."""
        try:
            from tools.pulse.db import init_db, query_rows
            init_db()
            status = flask_request.args.get("status")
            if status:
                rows = query_rows("posts", where="status = ?", params=(status,), limit=500)
            else:
                rows = query_rows("posts", limit=500)
            return jsonify(rows)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>")
    @require_installed("pulse")
    def api_pulse_get_post(post_id):
        """Get a single Pulse post."""
        try:
            from tools.pulse.db import get_row
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            return jsonify(post)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>", methods=["PUT"])
    @require_installed("pulse")
    def api_pulse_update_post(post_id):
        """Update a Pulse post."""
        try:
            from tools.pulse.db import get_row, update_row
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            body = flask_request.get_json(silent=True) or {}
            updates = {}
            for field in ("title", "body_markdown", "tldr", "seo_title",
                           "seo_description", "seo_keywords", "status"):
                if field in body:
                    updates[field] = body[field]
            if "title" in updates:
                from slugify import slugify as _slugify
                updates["slug"] = _slugify(updates["title"], max_length=80)
            if not updates:
                return jsonify({"error": "No valid fields to update"}), 400
            update_row("posts", post_id, updates)
            return jsonify({"post_id": post_id, "updated": list(updates.keys())})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/approve", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_approve(post_id):
        """Approve a Pulse post."""
        try:
            from tools.pulse.db import get_row, update_row, insert_row
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            now = datetime.now(timezone.utc).isoformat()
            update_row("posts", post_id, {"status": "approved"})
            insert_row("post_reviews", {
                "id": f"rev-{uuid.uuid4().hex[:12]}",
                "post_id": post_id,
                "action": "approved",
                "notes": "",
                "created_at": now,
            })
            return jsonify({"status": "approved", "post_id": post_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/reject", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_reject(post_id):
        """Reject a Pulse post."""
        try:
            from tools.pulse.db import get_row, update_row, insert_row
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            body = flask_request.get_json(silent=True) or {}
            notes = body.get("notes", "")
            now = datetime.now(timezone.utc).isoformat()
            update_row("posts", post_id, {"status": "rejected", "review_notes": notes})
            insert_row("post_reviews", {
                "id": f"rev-{uuid.uuid4().hex[:12]}",
                "post_id": post_id,
                "action": "rejected",
                "notes": notes,
                "created_at": now,
            })
            return jsonify({"status": "rejected", "post_id": post_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/judge", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_judge_post(post_id):
        """Run LLM Judge (Prometheus-2) on a Pulse post."""
        import threading
        try:
            conn = _get_db()
            row = conn.execute(
                "SELECT id, body_markdown, readability_score FROM pulse_posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            conn.close()
            if not row:
                return jsonify({"error": "Post not found"}), 404

            def _judge(pid, body, wg_score):
                try:
                    from tools.writing.llm_judge import evaluate_and_store, init_judge_db
                    init_judge_db()
                    result = evaluate_and_store(
                        text=body, content_type="blog",
                        writeguard_score=wg_score or 0, post_id=pid,
                    )
                    if result.get("status") == "evaluated":
                        conn2 = _get_db()
                        conn2.execute(
                            "UPDATE pulse_posts SET judge_color = ?, judge_composite = ?, "
                            "judge_combined = ? WHERE id = ?",
                            (result["color_rating"]["color"],
                             result["composite_score"],
                             result.get("combined_score", 0), pid),
                        )
                        conn2.commit()
                        conn2.close()
                except Exception:
                    pass

            threading.Thread(
                target=_judge,
                args=(post_id, row["body_markdown"], row["readability_score"]),
                daemon=True,
            ).start()
            return jsonify({"status": "judging", "post_id": post_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/pulse/posts/<post_id>/undo-reject", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_undo_reject(post_id):
        """Undo rejection — revert post to draft status."""
        try:
            from tools.pulse.db import get_row, update_row, insert_row
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            if post.get("status") != "rejected":
                return jsonify({"error": f"Post is {post.get('status')}, not rejected"}), 400
            now = datetime.now(timezone.utc).isoformat()
            update_row("posts", post_id, {"status": "draft", "review_notes": ""})
            insert_row("post_reviews", {
                "id": f"rev-{uuid.uuid4().hex[:12]}",
                "post_id": post_id,
                "action": "undo_reject",
                "notes": "Reverted to draft",
                "created_at": now,
            })
            return jsonify({"status": "draft", "post_id": post_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/pulse/posts/<post_id>/publish", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_publish(post_id):
        """Publish a Pulse post, export, and optionally push to Hostinger."""
        try:
            from tools.pulse.db import get_row, update_row
            from tools.pulse.engine.exporter import export_both
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            now = datetime.now(timezone.utc).isoformat()
            update_row("posts", post_id, {"status": "published", "published_at": now})
            exports = export_both(post_id)

            # Auto-push to WordPress (icdev.ai)
            wp_result = None
            auto_push = flask_request.json.get("auto_push", True) if flask_request.is_json else True
            if auto_push:
                try:
                    from tools.pulse.engine.wordpress_publisher import publish_post as wp_publish
                    wp_result = wp_publish(post_id)
                except Exception as we:
                    wp_result = {"status": "error", "message": str(we)}

            return jsonify({
                "status": "published",
                "post_id": post_id,
                "exports": exports,
                "hostinger": wp_result,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/unpublish", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_unpublish(post_id):
        """Unpublish a post: revert to draft locally and set WP post to draft."""
        try:
            from tools.pulse.db import get_row, update_row
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            update_row("posts", post_id, {
                "status": "draft",
                "published_at": None,
            })

            # Set WordPress post to draft if it was published there
            wp_result = None
            wp_post_id = post.get("wp_post_id")
            if wp_post_id:
                try:
                    from tools.pulse.engine.wordpress_publisher import (
                        _get_client, WP_BLOG_ID, WP_USERNAME, WP_PASSWORD,
                    )
                    if WP_PASSWORD:
                        wp = _get_client()
                        wp.wp.editPost(
                            WP_BLOG_ID, WP_USERNAME, WP_PASSWORD,
                            wp_post_id, {"post_status": "draft"},
                        )
                        wp_result = {"status": "ok", "wp_post_id": wp_post_id, "wp_status": "draft"}
                except Exception as we:
                    wp_result = {"status": "error", "message": str(we)}

            return jsonify({
                "status": "unpublished",
                "post_id": post_id,
                "wordpress": wp_result,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/push-hostinger", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_push_hostinger(post_id):
        """Push a published post to WordPress (icdev.ai)."""
        try:
            from tools.pulse.db import get_row
            from tools.pulse.engine.wordpress_publisher import publish_post as wp_publish
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            if post.get("status") != "published":
                return jsonify({"error": f"Post must be published first (current: {post.get('status')})"}), 400
            result = wp_publish(post_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/hostinger/session")
    @require_installed("pulse")
    def api_pulse_hostinger_session():
        """Check WordPress connection status."""
        try:
            from tools.pulse.engine.wordpress_publisher import test_connection
            result = test_connection()
            return jsonify({
                "session": result,
                "key_rotation": {"status": "ok", "message": "N/A — WordPress uses password auth"},
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/export", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_export(post_id):
        """Export a Pulse post as MDX + HTML."""
        try:
            from tools.pulse.db import get_row
            from tools.pulse.engine.exporter import export_both
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            exports = export_both(post_id)
            return jsonify({"post_id": post_id, "exports": exports})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>", methods=["DELETE"])
    @require_installed("pulse")
    def api_pulse_archive(post_id):
        """Archive or permanently delete a Pulse post."""
        try:
            from tools.pulse.db import get_row, update_row
            post = get_row("posts", post_id)
            if not post:
                return jsonify({"error": "Post not found"}), 404
            permanent = flask_request.args.get("permanent", "false").lower() == "true"
            if permanent:
                with _get_db() as conn:
                    conn.execute("DELETE FROM pulse_posts WHERE id = ?", (post_id,))
                    conn.commit()
                return jsonify({"status": "deleted", "post_id": post_id, "permanent": True})
            now = datetime.now(timezone.utc).isoformat()
            update_row("posts", post_id, {"status": "archived", "archived_at": now})
            return jsonify({"status": "archived", "post_id": post_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/research")
    @require_installed("pulse")
    def api_pulse_research():
        """List Pulse research cache entries."""
        try:
            from tools.pulse.db import query_rows
            limit = flask_request.args.get("limit", 50, type=int)
            rows = query_rows("research_cache", limit=limit)
            return jsonify(rows)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/clusters")
    @require_installed("pulse")
    def api_pulse_clusters():
        """List Pulse topic clusters."""
        try:
            with _get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM pulse_topic_clusters ORDER BY priority_score DESC LIMIT 100"
                ).fetchall()
                return jsonify([dict(r) for r in rows])
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    _pulse_pipeline_runs: dict = {}


    @app.route("/api/pulse/pipeline/run", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_pipeline_run():
        """Trigger a Pulse content pipeline run.

        Two modes:
        - With body_markdown + topic: Process pre-written draft (Claude Code orchestrated)
        - With topic only: Run research + cluster phase (returns context for Claude Code)
        - No params: Run research + cluster for all configured topics
        """
        import threading
        try:
            from tools.pulse.db import init_db
            init_db()
            body = flask_request.get_json(silent=True) or {}
            topic = body.get("topic")
            body_markdown = body.get("body_markdown")
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            _pulse_pipeline_runs[run_id] = {"run_id": run_id, "status": "running"}

            def _run_bg(rid, t, bm):
                try:
                    if bm and t:
                        # Claude Code wrote the article — run post-processing
                        from tools.pulse.engine.scheduler import run_pipeline_from_draft
                        result = run_pipeline_from_draft(t, bm, [])
                    else:
                        # Research + cluster only — returns context for Claude Code
                        from tools.pulse.engine.scheduler import research_phase
                        result = research_phase(topic_override=t)
                    _pulse_pipeline_runs[rid] = result
                except Exception as exc:
                    _pulse_pipeline_runs[rid] = {"run_id": rid, "status": "failed", "error": str(exc)}

            threading.Thread(target=_run_bg, args=(run_id, topic, body_markdown), daemon=True).start()
            return jsonify({"run_id": run_id, "status": "started"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/rewrite", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_rewrite_post(post_id):
        """Update a post with rewritten content from Claude Code."""
        try:
            from tools.pulse.engine.scheduler import update_post_content
            body = flask_request.get_json(silent=True) or {}
            body_markdown = body.get("body_markdown")
            if not body_markdown:
                return jsonify({"error": "body_markdown is required"}), 400
            result = update_post_content(post_id, body_markdown)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/rewrite-llm", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_rewrite_llm(post_id):
        """Trigger Claude Sonnet rewrite for a post via LLM router.

        Reads the post, runs WriteGuard to get findings, then rewrites
        via the LLM router (pulse_rewrite → Claude Sonnet planner tier).
        """
        try:
            from tools.pulse.engine.scheduler import rewrite_post_via_llm
            result = rewrite_post_via_llm(post_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/<post_id>/enrich-capabilities", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_enrich_capabilities(post_id):
        """Rewrite a post with ICDEV™ capability context injected.

        Matches capabilities based on title/topic, injects into rewrite prompt,
        triggers Claude Sonnet rewrite with capability references.
        """
        try:
            from tools.pulse.engine.scheduler import enrich_post_with_capabilities
            result = enrich_post_with_capabilities(post_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/posts/enrich-all", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_enrich_all():
        """Enrich all published posts with ICDEV™ capabilities (batch)."""
        import threading
        try:
            from tools.pulse.db import init_db
            init_db()
            with _get_db() as conn:
                posts = conn.execute(
                    "SELECT id, title FROM pulse_posts WHERE status = 'published'"
                ).fetchall()

            run_id = f"enrich-{__import__('uuid').uuid4().hex[:8]}"
            post_ids = [p["id"] for p in posts]

            def _run_batch():
                from tools.pulse.engine.scheduler import enrich_post_with_capabilities
                results = []
                for pid in post_ids:
                    try:
                        r = enrich_post_with_capabilities(pid)
                        results.append({"post_id": pid, "status": r.get("status", "unknown")})
                    except Exception as e:
                        results.append({"post_id": pid, "status": "error", "error": str(e)})
                # Store results in pipeline runs table
                try:
                    from tools.pulse.db import insert_row
                    insert_row("pipeline_runs", {
                        "id": run_id,
                        "status": "completed",
                        "stage": "enrich_capabilities",
                        "config_json": __import__("json").dumps({"post_ids": post_ids}),
                        "result_json": __import__("json").dumps(results),
                    })
                except Exception:
                    pass

            t = threading.Thread(target=_run_batch, daemon=True)
            t.start()
            return jsonify({
                "status": "started",
                "run_id": run_id,
                "posts_queued": len(post_ids),
                "post_ids": post_ids,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/pipeline/run-full", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_pipeline_run_full():
        """Run the full automated pipeline: research → draft → quality → rewrite.

        Uses LLM router: qwen3.5 for research/draft, Claude Sonnet for rewrite.

        Body params:
            topic (str, optional): Topic override.
            template_type (str): 'challenge_solution' or 'feature_spotlight'.
            auto_rewrite (bool): Whether to auto-rewrite via Sonnet (default true).
        """
        import threading
        try:
            from tools.pulse.db import init_db
            init_db()
            body = flask_request.get_json(silent=True) or {}
            topic = body.get("topic")
            template_type = body.get("template_type", "challenge_solution")
            auto_rewrite = body.get("auto_rewrite", True)
            run_id = f"run-{uuid.uuid4().hex[:12]}"
            _pulse_pipeline_runs[run_id] = {"run_id": run_id, "status": "running", "stage": "research"}

            _PULSE_STAGES = ["research", "quality_check", "rewrite", "publish"]

            def _run_bg(rid, t, tmpl, ar):
                def _on_stage(stage):
                    _pulse_pipeline_runs[rid] = {
                        "run_id": rid, "status": "running", "stage": stage,
                    }
                    # SSE progress broadcast
                    try:
                        from tools.dashboard.sse_manager import emit_progress
                        idx = _PULSE_STAGES.index(stage) if stage in _PULSE_STAGES else 0
                        emit_progress(
                            rid, "pulse_pipeline", stage,
                            idx + 1, len(_PULSE_STAGES),
                            detail=f"Pulse pipeline: {stage}",
                        )
                    except Exception:
                        pass
                try:
                    from tools.pulse.engine.scheduler import run_full_pipeline
                    result = run_full_pipeline(
                        topic_override=t,
                        template_type=tmpl,
                        auto_rewrite=ar,
                        progress_callback=_on_stage,
                    )
                    _pulse_pipeline_runs[rid] = result
                except Exception as exc:
                    _pulse_pipeline_runs[rid] = {
                        "run_id": rid, "status": "failed",
                        "stage": "error", "error": str(exc),
                    }

            threading.Thread(
                target=_run_bg,
                args=(run_id, topic, template_type, auto_rewrite),
                daemon=True,
            ).start()
            return jsonify({"run_id": run_id, "status": "started", "stage": "research"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/pipeline/status/<run_id>")
    @require_installed("pulse")
    def api_pulse_pipeline_status(run_id):
        """Get Pulse pipeline run status."""
        if run_id in _pulse_pipeline_runs:
            return jsonify(_pulse_pipeline_runs[run_id])
        try:
            from tools.pulse.db import get_row
            entry = get_row("schedule_log", run_id)
            if not entry:
                return jsonify({"error": "Run not found"}), 404
            return jsonify(entry)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/pipeline/history")
    @require_installed("pulse")
    def api_pulse_pipeline_history():
        """Get Pulse pipeline run history."""
        try:
            with _get_db() as conn:
                rows = conn.execute(
                    "SELECT * FROM pulse_schedule_log ORDER BY started_at DESC LIMIT 50"
                ).fetchall()
                return jsonify([dict(r) for r in rows])
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/authors")
    @require_installed("pulse")
    def api_pulse_authors():
        """List Pulse authors."""
        try:
            from tools.pulse.db import query_rows
            rows = query_rows("authors", limit=100)
            return jsonify(rows)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/authors", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_create_author():
        """Create a Pulse author."""
        try:
            from tools.pulse.db import insert_row
            body = flask_request.get_json(silent=True) or {}
            name = body.get("name")
            if not name:
                return jsonify({"error": "name is required"}), 400
            author_id = f"author-{uuid.uuid4().hex[:12]}"
            now = datetime.now(timezone.utc).isoformat()
            data = {
                "id": author_id,
                "name": name,
                "email": body.get("email", ""),
                "bio": body.get("bio", ""),
                "role": body.get("role", "contributor"),
                "created_at": now,
            }
            insert_row("authors", data)
            return jsonify(data), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/stats")
    @require_installed("pulse")
    def api_pulse_stats():
        """Get Pulse pipeline statistics."""
        try:
            from tools.pulse.db import init_db
            init_db()
            with _get_db() as conn:
                total = conn.execute("SELECT COUNT(*) FROM pulse_posts").fetchone()[0]
                status_rows = conn.execute(
                    "SELECT status, COUNT(*) as count FROM pulse_posts GROUP BY status"
                ).fetchall()
                by_status = {row["status"]: row["count"] for row in status_rows}
                research_count = conn.execute("SELECT COUNT(*) FROM pulse_research_cache").fetchone()[0]
                cluster_count = conn.execute("SELECT COUNT(*) FROM pulse_topic_clusters").fetchone()[0]
                run_count = conn.execute("SELECT COUNT(*) FROM pulse_schedule_log").fetchone()[0]
            return jsonify({
                "total_posts": total,
                "by_status": by_status,
                "research_entries": research_count,
                "clusters": cluster_count,
                "pipeline_runs": run_count,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/llm/dual-model", methods=["GET"])
    def api_llm_dual_model_status():
        """Get current dual-model mode status."""
        try:
            from tools.llm.router import LLMRouter
            active = LLMRouter.get_dual_model()
            return jsonify({
                "dual_model": active,
                "mode": "speed" if active else "quality",
                "description": "1.7B text-only + Gemma3 (both VRAM-resident)" if active
                    else "9B multimodal (single model, higher quality)",
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/llm/dual-model", methods=["POST"])
    def api_llm_dual_model_toggle():
        """Toggle dual-model mode. Body: {"enabled": true/false}."""
        try:
            from tools.llm.router import LLMRouter
            data = flask_request.get_json(silent=True) or {}
            enabled = data.get("enabled")
            if enabled is None:
                # Toggle current state
                enabled = not LLMRouter.get_dual_model()
            LLMRouter.set_dual_model(bool(enabled))
            return jsonify({
                "dual_model": LLMRouter.get_dual_model(),
                "mode": "speed" if enabled else "quality",
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/pulse/analytics/<post_id>")
    @require_installed("pulse")
    def api_pulse_analytics(post_id):
        """Get analytics for a Pulse post."""
        try:
            from tools.pulse.db import query_rows
            rows = query_rows("post_analytics", where="post_id = ?", params=(post_id,), limit=100)
            return jsonify(rows)
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    # ── Pulse SAM Bridge ──────────────────────────────────────────────

    _sam_bridge_runs: dict[str, dict] = {}


    @app.route("/api/pulse/sam-bridge/run", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_sam_bridge_run():
        """Run SAM-to-Pulse bridge (extracts pain points from SAM.gov, generates articles).

        Body params:
            dry_run (bool): If true, extract topics without generating articles.
            max_articles (int): Max articles to generate (default 5).
        """
        import threading
        try:
            from tools.pulse.db import init_db
            init_db()
            body = flask_request.get_json(silent=True) or {}
            dry_run = body.get("dry_run", False)
            max_articles = body.get("max_articles", 5)
            run_id = f"sam-{uuid.uuid4().hex[:12]}"
            _sam_bridge_runs[run_id] = {
                "run_id": run_id, "status": "running",
                "stage": "scanning", "dry_run": dry_run,
            }

            def _run_bg(rid, dr, ma):
                try:
                    _sam_bridge_runs[rid]["stage"] = "extracting"
                    from tools.pulse.engine.sam_bridge import run_sam_to_pulse
                    result = run_sam_to_pulse(dry_run=dr, max_articles=ma)
                    result["run_id"] = rid
                    result["status"] = "completed"
                    _sam_bridge_runs[rid] = result
                except Exception as exc:
                    _sam_bridge_runs[rid] = {
                        "run_id": rid, "status": "failed",
                        "stage": "error", "error": str(exc),
                    }

            threading.Thread(
                target=_run_bg, args=(run_id, dry_run, max_articles),
                daemon=True,
            ).start()
            return jsonify({"run_id": run_id, "status": "started", "dry_run": dry_run})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/sam-bridge/status/<run_id>")
    @require_installed("pulse")
    def api_pulse_sam_bridge_status(run_id):
        """Get SAM bridge run status."""
        if run_id in _sam_bridge_runs:
            return jsonify(_sam_bridge_runs[run_id])
        return jsonify({"error": "Run not found"}), 404


    @app.route("/api/pulse/sam-bridge/stats")
    @require_installed("pulse")
    def api_pulse_sam_bridge_stats():
        """Get SAM bridge pipeline statistics."""
        try:
            from tools.pulse.db import init_db
            init_db()
            with _get_db() as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM pulse_sam_article_log"
                ).fetchone()[0]
                by_status = {}
                status_rows = conn.execute(
                    "SELECT pipeline_status, COUNT(*) as count "
                    "FROM pulse_sam_article_log GROUP BY pipeline_status"
                ).fetchall()
                for row in status_rows:
                    by_status[row["pipeline_status"]] = row["count"]
                by_domain = {}
                domain_rows = conn.execute(
                    "SELECT domain_category, COUNT(*) as count "
                    "FROM pulse_sam_article_log GROUP BY domain_category"
                ).fetchall()
                for row in domain_rows:
                    by_domain[row["domain_category"] or "unknown"] = row["count"]
                recent = conn.execute(
                    "SELECT id, opportunity_title, domain_category, article_topic, "
                    "pipeline_status, created_at FROM pulse_sam_article_log "
                    "ORDER BY created_at DESC LIMIT 10"
                ).fetchall()
            return jsonify({
                "total": total,
                "by_status": by_status,
                "by_domain": by_domain,
                "recent": [dict(r) for r in recent],
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/demand-signals")
    @require_installed("pulse")
    def api_pulse_demand_signals():
        """List demand signals, optionally filtered to high-demand only."""
        try:
            from tools.pulse.db import init_db
            init_db()
            high_only = flask_request.args.get("high_demand", "0") == "1"
            with _get_db() as conn:
                if high_only:
                    rows = conn.execute(
                        "SELECT * FROM pulse_demand_signals WHERE is_high_demand = 1 "
                        "ORDER BY frequency DESC"
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM pulse_demand_signals ORDER BY frequency DESC"
                    ).fetchall()
            return jsonify({"signals": [dict(r) for r in rows], "count": len(rows)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/demand-signals/aggregate")
    @require_installed("pulse")
    def api_pulse_demand_signals_aggregate():
        """Aggregate demand signal stats by domain."""
        try:
            from tools.pulse.db import init_db
            init_db()
            with _get_db() as conn:
                rows = conn.execute(
                    "SELECT domain_category, COUNT(*) as count, "
                    "SUM(CASE WHEN is_high_demand = 1 THEN 1 ELSE 0 END) as high_demand_count, "
                    "AVG(frequency) as avg_frequency "
                    "FROM pulse_demand_signals GROUP BY domain_category "
                    "ORDER BY count DESC"
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) FROM pulse_demand_signals"
                ).fetchone()[0]
                high_total = conn.execute(
                    "SELECT COUNT(*) FROM pulse_demand_signals WHERE is_high_demand = 1"
                ).fetchone()[0]
            return jsonify({
                "by_domain": [dict(r) for r in rows],
                "total": total,
                "high_demand_total": high_total,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/capability-graph")
    @require_installed("pulse")
    def api_pulse_capability_graph():
        """Query capability graph edges, optionally filtered by capability slug."""
        try:
            from tools.pulse.db import init_db
            init_db()
            cap_slug = flask_request.args.get("capability")
            with _get_db() as conn:
                if cap_slug:
                    rows = conn.execute(
                        "SELECT * FROM pulse_capability_graph WHERE capability_slug = ? "
                        "ORDER BY confidence DESC", (cap_slug,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM pulse_capability_graph ORDER BY created_at DESC LIMIT 100"
                    ).fetchall()
            return jsonify({"edges": [dict(r) for r in rows], "count": len(rows)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/capabilities")
    @require_installed("pulse")
    def api_pulse_capabilities():
        """List all ICDEV™ capabilities from the capability catalog."""
        try:
            from tools.pulse.engine.capability_scanner import load_domains
            domains = load_domains(include_capabilities=True)
            total = sum(d["capability_count"] for d in domains)
            return jsonify({"domains": domains, "total_capabilities": total, "total_domains": len(domains)})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/capabilities/match")
    @require_installed("pulse")
    def api_pulse_capabilities_match():
        """Match capabilities by keywords."""
        try:
            from tools.pulse.engine.capability_scanner import match_capabilities
            q = flask_request.args.get("q", "")
            top_n = int(flask_request.args.get("top_n", "5"))
            keywords = [kw for kw in q.split() if len(kw) > 2]
            if not keywords:
                return jsonify({"error": "Provide ?q= with keywords"}), 400
            matched = match_capabilities(keywords, top_n=top_n)
            return jsonify({"query": q, "matched": len(matched), "capabilities": matched})
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.route("/api/pulse/hero-image/<post_id>")
    def api_pulse_hero_image(post_id):
        """Serve a Pulse post hero image from disk."""
        from flask import send_file
        try:
            conn = _get_db()
            row = conn.execute("SELECT hero_image_path FROM pulse_posts WHERE id = ?", (post_id,)).fetchone()
            conn.close()
            if not row or not row["hero_image_path"]:
                return "No image", 404
            img_path = Path(row["hero_image_path"])
            if not img_path.exists():
                return "Image file not found", 404
            mime = "image/png" if str(img_path).endswith(".png") else "image/svg+xml"
            return send_file(str(img_path), mimetype=mime)
        except Exception as e:
            return str(e), 500

    @app.route("/api/pulse/posts/<post_id>/generate-image", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_generate_image(post_id):
        """Generate a hero image for a Pulse post using SDXL Turbo (local GPU)."""
        import threading
        try:
            conn = _get_db()
            row = conn.execute("SELECT id, title, topic FROM pulse_posts WHERE id = ?", (post_id,)).fetchone()
            conn.close()
            if not row:
                return jsonify({"error": "Post not found"}), 404
            title = row["title"]
            category = row["topic"] or ""

            def _gen(pid, t, c):
                try:
                    from tools.pulse.engine.image_generator import generate_hero_image
                    result = generate_hero_image(title=t, category=c)
                    if result.get("success"):
                        conn2 = _get_db()
                        conn2.execute(
                            "UPDATE pulse_posts SET hero_image_path = ?, hero_image_method = ?, hero_image_prompt = ? WHERE id = ?",
                            (result["path"], result["method"], result.get("prompt", ""), pid),
                        )
                        conn2.commit()
                        conn2.close()
                except Exception:
                    pass

            threading.Thread(target=_gen, args=(post_id, title, category), daemon=True).start()
            return jsonify({"success": True, "status": "generating", "method": "sdxl_turbo", "post_id": post_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/pulse/generated-video/<post_id>")
    @require_installed("pulse")
    def api_pulse_generated_video(post_id):
        """Serve a Pulse post generated video from disk."""
        from flask import send_file
        try:
            conn = _get_db()
            row = conn.execute(
                "SELECT generated_video_path, generated_video_method FROM pulse_posts WHERE id = ?",
                (post_id,),
            ).fetchone()
            conn.close()
            if not row or not row["generated_video_path"]:
                return "No video", 404
            vid_path = Path(row["generated_video_path"])
            if not vid_path.exists():
                return "Video file not found", 404
            method = row["generated_video_method"] or ""
            if method == "animated_svg" or str(vid_path).endswith(".svg"):
                mime = "image/svg+xml"
            else:
                mime = "video/mp4"
            return send_file(str(vid_path), mimetype=mime)
        except Exception as e:
            return str(e), 500

    @app.route("/api/pulse/posts/<post_id>/generate-video", methods=["POST"])
    @require_installed("pulse")
    def api_pulse_generate_video(post_id):
        """Generate a hero video for a Pulse post using LTX-Video 2B (local GPU)."""
        import threading
        try:
            conn = _get_db()
            row = conn.execute("SELECT id, title, topic FROM pulse_posts WHERE id = ?", (post_id,)).fetchone()
            conn.close()
            if not row:
                return jsonify({"error": "Post not found"}), 404
            title = row["title"]
            category = row["topic"] or ""

            def _gen(pid, t, c):
                try:
                    from tools.pulse.engine.video_generator import generate_post_video
                    result = generate_post_video(title=t, category=c)
                    if result.get("success"):
                        conn2 = _get_db()
                        conn2.execute(
                            "UPDATE pulse_posts SET generated_video_path = ?, "
                            "generated_video_method = ?, generated_video_duration = ? WHERE id = ?",
                            (result["path"], result["method"], result.get("duration_sec", 0), pid),
                        )
                        conn2.commit()
                        conn2.close()
                except Exception:
                    pass

            threading.Thread(target=_gen, args=(post_id, title, category), daemon=True).start()
            return jsonify({"success": True, "status": "generating", "method": "ltx_video_2b", "post_id": post_id})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ---- File Sync (D-SYNC-1 through D-SYNC-12) ----

    @app.route("/filesync")
    def filesync_page():
        """File Sync — sync jobs, status, conflicts, activity log."""
        stats = {"total_jobs": 0, "active_jobs": 0, "watching_jobs": 0,
                 "completed_syncs": 0,
                 "failed_syncs": 0, "pending_conflicts": 0, "total_bytes": 0,
                 "total_bytes_display": "0 B"}
        jobs = []
        log_entries = []
        conn = _get_db()
        try:
            # Ensure indexes exist for sync_log queries (table can have millions of rows)
            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sync_log_action ON sync_log(action)")
            except Exception:
                pass
            try:
                row = conn.execute("SELECT COUNT(*) as cnt FROM sync_jobs").fetchone()
                stats["total_jobs"] = row["cnt"]
            except Exception:
                pass
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sync_jobs WHERE status IN ('scanning', 'syncing', 'watching')"
                ).fetchone()
                stats["active_jobs"] = row["cnt"]
            except Exception:
                pass
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sync_jobs WHERE status = 'watching'"
                ).fetchone()
                stats["watching_jobs"] = row["cnt"]
            except Exception:
                pass
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sync_log WHERE action = 'sync_completed'"
                ).fetchone()
                stats["completed_syncs"] = row["cnt"]
            except Exception:
                pass
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sync_log WHERE action = 'error'"
                ).fetchone()
                stats["failed_syncs"] = row["cnt"]
            except Exception:
                pass
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt FROM sync_conflicts WHERE resolution = 'pending'"
                ).fetchone()
                stats["pending_conflicts"] = row["cnt"]
            except Exception:
                pass
            # Skip SUM(bytes_transferred) over full sync_log — too expensive on millions of rows.
            # Use SUM from sync_jobs.bytes_transferred (per-job aggregate) as a fast proxy.
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(bytes_transferred), 0) as total FROM sync_jobs"
                ).fetchone()
                total_bytes = row["total"]
                stats["total_bytes"] = total_bytes
                if total_bytes >= 1073741824:
                    stats["total_bytes_display"] = f"{total_bytes / 1073741824:.1f} GB"
                elif total_bytes >= 1048576:
                    stats["total_bytes_display"] = f"{total_bytes / 1048576:.1f} MB"
                elif total_bytes >= 1024:
                    stats["total_bytes_display"] = f"{total_bytes / 1024:.1f} KB"
                else:
                    stats["total_bytes_display"] = f"{total_bytes} B"
            except Exception:
                pass
            try:
                rows = conn.execute(
                    "SELECT * FROM sync_jobs ORDER BY created_at DESC LIMIT 50"
                ).fetchall()
                jobs = [dict(r) for r in rows]
            except Exception:
                pass
            try:
                rows = conn.execute(
                    "SELECT * FROM sync_log ORDER BY created_at DESC LIMIT 30"
                ).fetchall()
                log_entries = [dict(r) for r in rows]
            except Exception:
                pass
        finally:
            conn.close()
        return render_template("filesync.html", stats=stats, jobs=jobs, log_entries=log_entries)

    @app.errorhandler(401)
    def unauthorized(e):
        if flask_request.is_json or flask_request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized", "message": "Valid API key required"}), 401
        return redirect(url_for("login_page"))

    @app.errorhandler(403)
    def forbidden(e):
        if flask_request.is_json or flask_request.path.startswith("/api/"):
            return jsonify({"error": "Forbidden", "message": "Insufficient permissions"}), 403
        return render_template("404.html", message="You do not have permission to access this page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("404.html", message="Page not found"), 404

    # -------------------------------------------------------------------
    # CLI Generator (cherry-picked from icdev main)
    # -------------------------------------------------------------------

    @app.route("/api/cli-generator/generate", methods=["POST"])
    def api_cli_generator_generate():
        try:
            from tools.harness.cli_generator import generate
            data = flask_request.get_json(force=True)
            spec_path = data.get("spec_path", "")
            if not spec_path:
                return jsonify({"status": "error", "error": "spec_path is required"}), 400
            result = generate(
                spec_path=spec_path,
                output_dir=data.get("output_dir"),
                name=data.get("name"),
                dry_run=data.get("dry_run", False),
            )
            return jsonify(result)
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500


    # ═══════════════════════════════════════════════════════════════════════════
    # MCP Wrapper Generator (Phase 3)
    # ═══════════════════════════════════════════════════════════════════════════

    @app.route("/mcp-wrapper")
    def mcp_wrapper_page():
        return render_template("mcp_wrapper.html", app_name="SparkPilot")


    @app.route("/api/mcp-wrapper/scan")
    def api_mcp_wrapper_scan():
        try:
            from tools.harness.mcp_wrapper_generator import scan_tools
            return jsonify(scan_tools())
        except Exception as e:
            return jsonify({"status": "error", "error": str(e), "discovered": [], "total": 0, "with_json_flag": 0})


    @app.route("/api/mcp-wrapper/list")
    def api_mcp_wrapper_list():
        try:
            from tools.harness.mcp_wrapper_generator import list_wrapped
            return jsonify(list_wrapped())
        except Exception as e:
            return jsonify({"wrappers": [], "count": 0, "error": str(e)})


    @app.route("/api/mcp-wrapper/wrap", methods=["POST"])
    def api_mcp_wrapper_wrap():
        try:
            from tools.harness.mcp_wrapper_generator import wrap_tool
            data = flask_request.get_json(force=True)
            tool_path = data.get("tool_path", "")
            if not tool_path:
                return jsonify({"status": "error", "error": "tool_path is required"}), 400
            return jsonify(wrap_tool(tool_path, dry_run=data.get("dry_run", False)))
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500


    @app.route("/api/mcp-wrapper/wrap-all", methods=["POST"])
    def api_mcp_wrapper_wrap_all():
        try:
            from tools.harness.mcp_wrapper_generator import wrap_all
            data = flask_request.get_json(force=True) if flask_request.data else {}
            return jsonify(wrap_all(
                dry_run=data.get("dry_run", False),
                limit=data.get("limit", 20),
            ))
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500


    # ── Page Agent Copilot API ───────────────────────────────────────
    # Inspired by alibaba/page-agent: text-based DOM navigation + AI copilot

    _PAGE_AGENT_ROUTE_MAP = {
        "home": "/", "dashboard": "/", "missions": "/missions",
        "simulator": "/simulator", "fleet": "/devices", "devices": "/devices",
        "firmware": "/firmware", "edge ai": "/edge-ai", "self-heal": "/crashes",
        "agents": "/agents", "govcon": "/govcon", "writeguard": "/writeguard",
        "pulse": "/pulse", "databridge": "/databridge",
        "messaging": "/databridge/messaging", "cloudforge": "/cloudforge",
        "knowledge": "/knowledge-graph", "knowledge graph": "/knowledge-graph",
        "marketplace": "/marketplace", "research": "/research",
        "harness": "/harness", "codelens": "/container-lens",
        "forge studio": "/forge-studio", "dochub": "/dochub",
        "resilience": "/resilience", "architecture": "/architecture",
        "compliance": "/compliance-accel", "agent evolution": "/agent-evolution",
        "intelligence": "/intelligence", "maturity": "/maturity",
        "decisions": "/decisions", "security": "/security-scan",
    }


    @app.route("/api/page-agent/message", methods=["POST"])
    def api_page_agent_message():
        """Process a Page Agent copilot message — navigation, search, or contextual help.

        Route map loaded from DB (page_agent_routes) with fallback to
        _PAGE_AGENT_ROUTE_MAP hardcoded dict.
        """
        # Load custom routes from DB if available
        try:
            conn = _get_db()
            rows = conn.execute("SELECT keyword, route FROM page_agent_routes").fetchall()
            conn.close()
            if rows:
                for r in rows:
                    _PAGE_AGENT_ROUTE_MAP[r["keyword"]] = r["route"]
        except Exception:
            pass  # Table may not exist — use defaults
        try:
            data = flask_request.get_json(force=True) if flask_request.is_json else {}
            message = data.get("message", "").strip()
            page = data.get("page", "/")
            if not message:
                return jsonify({"error": "message required"}), 400

            lower = message.lower().strip()

            # Navigation intent
            for prefix in ("go to ", "navigate to ", "show me ", "open "):
                if lower.startswith(prefix):
                    target = lower[len(prefix):].strip()
                    route = _PAGE_AGENT_ROUTE_MAP.get(target)
                    if route:
                        return jsonify({
                            "response": f"Navigating to **{target}**...",
                            "action": "navigate",
                            "route": route,
                        })
                    # Fuzzy match
                    best, best_score = None, 0
                    for key in _PAGE_AGENT_ROUTE_MAP:
                        score = _bigram_similarity(target, key)
                        if score > best_score and score > 0.4:
                            best_score = score
                            best = key
                    if best:
                        return jsonify({
                            "response": f"Did you mean **{best}**? Navigating...",
                            "action": "navigate",
                            "route": _PAGE_AGENT_ROUTE_MAP[best],
                        })
                    return jsonify({
                        "response": f"Page not found: `{target}`. Try asking `show pages`.",
                        "suggestions": ["show pages", "help"],
                    })

            # Help
            if lower in ("help", "what can you do", "commands"):
                return jsonify({
                    "response": (
                        "**Commands:** `go to <page>`, `search <text>`, "
                        "`show pages`, `where am i`, `describe this page`, "
                        "`scroll up/down`, `click <element>`, `fill <value> in <field>`"
                    ),
                    "suggestions": ["go to compliance", "show pages", "describe this page"],
                })

            # Page listing
            if "show pages" in lower or "list pages" in lower or "list routes" in lower:
                pages = sorted(_PAGE_AGENT_ROUTE_MAP.keys())
                lines = [f"- `{p}` → {_PAGE_AGENT_ROUTE_MAP[p]}" for p in pages]
                return jsonify({
                    "response": f"**Available pages ({len(pages)}):**\n" + "\n".join(lines),
                })

            # Context-aware suggestions based on current page
            suggestions = _page_suggestions(page)
            return jsonify({
                "response": (
                    f"I understand your request: *{message}*. "
                    "For best results, try specific commands like `go to agents` or `search <keyword>`."
                ),
                "suggestions": suggestions,
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    def _bigram_similarity(a, b):
        """Bigram (Dice) similarity for fuzzy page matching."""
        if a == b:
            return 1.0
        if len(a) < 2 or len(b) < 2:
            return 0.0
        a_bigrams = {}
        for i in range(len(a) - 1):
            bg = a[i:i+2]
            a_bigrams[bg] = a_bigrams.get(bg, 0) + 1
        matches = 0
        for i in range(len(b) - 1):
            bg = b[i:i+2]
            if a_bigrams.get(bg, 0) > 0:
                matches += 1
                a_bigrams[bg] -= 1
        return (2.0 * matches) / (len(a) + len(b) - 2)


    def _page_suggestions(current_page):
        """Return contextual suggestions based on current page."""
        suggestions_map = {
            "/": ["go to agents", "go to compliance", "go to missions"],
            "/agents": ["go to agent evolution", "go to harness", "go to intelligence"],
            "/compliance-accel": ["go to dochub", "go to resilience", "go to maturity"],
            "/devices": ["go to firmware", "go to edge ai", "go to simulator"],
            "/knowledge-graph": ["go to research", "go to intelligence", "search compliance"],
            "/cloudforge": ["go to databridge", "go to marketplace", "go to forge studio"],
            "/genesis": ["run research", "run audit", "show promoter stats"],
        }
        return suggestions_map.get(current_page, ["help", "show pages", "go to agents"])

    # ── Proposal Genesis — Autonomous Capture Pipeline Dashboard ─────────────

    @app.route("/proposal-genesis")
    def proposal_genesis():
        """Proposal Genesis — autonomous capture-to-delivery pipeline dashboard."""
        status = {}
        summary = {}
        try:
            import subprocess
            _utf8_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            result = subprocess.run(
                [sys.executable, "tools/proposal_genesis/daemon.py", "--status", "--json"],
                capture_output=True, text=True, timeout=15, cwd=BASE_DIR,
                env=_utf8_env,
            )
            stdout = result.stdout.strip()
            json_start = stdout.find("{")
            if json_start >= 0:
                status = json.loads(stdout[json_start:])
        except Exception as exc:
            status = {"error": str(exc)}
        # Summary stats
        try:
            conn = _get_db()
            try:
                summary["opportunities"] = conn.execute(
                    "SELECT COUNT(*) as cnt FROM proposal_opportunities WHERE status IN ('tracking', 'drafting')"
                ).fetchone()["cnt"]
            except Exception:
                summary["opportunities"] = 0
            try:
                summary["shall_statements"] = conn.execute(
                    "SELECT COUNT(*) as cnt FROM rfp_shall_statements"
                ).fetchone()["cnt"]
            except Exception:
                summary["shall_statements"] = 0
            try:
                summary["drafts"] = conn.execute(
                    "SELECT COUNT(*) as cnt FROM proposal_section_drafts WHERE status = 'draft'"
                ).fetchone()["cnt"]
            except Exception:
                summary["drafts"] = 0
            try:
                row = conn.execute("SELECT AVG(composite_score) as avg_score FROM pg_proposal_quality_scores").fetchone()
                summary["avg_quality"] = round(row["avg_score"] or 0, 3)
            except Exception:
                summary["avg_quality"] = 0
            try:
                summary["pulse_links"] = conn.execute(
                    "SELECT COUNT(*) as cnt FROM pg_pulse_proposal_links"
                ).fetchone()["cnt"]
            except Exception:
                summary["pulse_links"] = 0
            conn.close()
        except Exception:
            pass
        return render_template("proposal_genesis.html", status=status, summary=summary)


    # ── Genesis v2.0 — Autonomous Research Lab Dashboard ──────────────────────

    # Registry of all Genesis-enabled apps (app_key → config)
    GENESIS_APPS = {
        "icdev": {
            "name": "ICDEV™",
            "root": str(BASE_DIR),
            "daemon": "tools/genesis/daemon.py",
            "promoter": "tools/genesis/promoter.py",
            "env_var": "ICDEV_GENESIS_ENABLED",
            "db": str(BASE_DIR / "data" / "icdev.db"),
        },
        "govchain": {
            "name": "GovChain",
            "root": str(Path(BASE_DIR).parent / "govchain"),
            "daemon": "tools/genesis/daemon.py",
            "promoter": None,
            "env_var": "GOVCHAIN_GENESIS_ENABLED",
            "db": str(Path(BASE_DIR).parent / "govchain" / "data" / "govchain.db"),
        },
        "govproposal": {
            "name": "GovProposal",
            "root": str(Path(BASE_DIR).parent / "GovProposal"),
            "daemon": "tools/genesis/daemon.py",
            "promoter": None,
            "env_var": "GOVPROPOSAL_GENESIS_ENABLED",
            "db": str(Path(BASE_DIR).parent / "GovProposal" / "data" / "govproposal.db"),
        },
        "trading-engine": {
            "name": "Trading Engine",
            "root": str(Path(BASE_DIR).parent / "trading-engine"),
            "daemon": "tools/genesis/daemon.py",
            "promoter": None,
            "env_var": "TRADING_GENESIS_ENABLED",
            "db": str(Path(BASE_DIR).parent / "trading-engine" / "data" / "trading-engine.db"),
        },
        "trading-strategy": {
            "name": "Trading Strategy",
            "root": str(Path(BASE_DIR).parent / "Trading_Strategy"),
            "daemon": "tools/genesis/daemon.py",
            "promoter": "tools/genesis/promoter.py",
            "env_var": "TRADING_GENESIS_ENABLED",
            "db": str(Path(BASE_DIR).parent / "Trading_Strategy" / "data" / "trading_strategy.db"),
        },
        "ninjaflow": {
            "name": "NinjaFlow",
            "root": str(Path(BASE_DIR).parent / "ninjaflow-ai" / "ninjaflow-ai"),
            "daemon": "tools/genesis/daemon.py",
            "promoter": None,
            "env_var": "NINJAFLOW_GENESIS_ENABLED",
            "db": str(Path(BASE_DIR).parent / "ninjaflow-ai" / "ninjaflow-ai" / "data" / "ninjaflow-ai.db"),
        },
        "signalforge": {
            "name": "SignalForge",
            "root": str(Path(BASE_DIR).parent / "signalforge"),
            "daemon": "tools/genesis/daemon.py",
            "promoter": None,
            "env_var": "SIGNALFORGE_GENESIS_ENABLED",
            "db": str(Path(BASE_DIR).parent / "signalforge" / "data" / "signalforge.db"),
        },
    }

    def _genesis_app(app_key):
        """Get Genesis app config, default to icdev."""
        return GENESIS_APPS.get(app_key, GENESIS_APPS["icdev"])

    def _genesis_run(app_key, args, timeout=15):
        """Run a Genesis daemon command for a given app."""
        import subprocess as _sp
        cfg = _genesis_app(app_key)
        app_root = cfg["root"]
        daemon_path = cfg["daemon"]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": app_root,
               cfg["env_var"]: "true", "PYTHONUNBUFFERED": "1"}
        result = _sp.run(
            [sys.executable, daemon_path] + args,
            capture_output=True, text=True, timeout=timeout, cwd=app_root, env=env,
        )
        stdout = result.stdout.strip()
        json_start = stdout.find("{")
        if json_start >= 0:
            return json.loads(stdout[json_start:])
        return {"error": "parse_failed", "stderr": result.stderr[:500] if result.stderr else ""}

    def _genesis_db(app_key):
        """Get a DB connection for a Genesis app."""
        _genesis_app(app_key)  # Validate app_key exists
        conn = get_connection(db_path=str(DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ── Contact Form Submissions ─────────────────────────────────────────────

    @app.route("/api/contact/submit", methods=["POST", "OPTIONS"])
    def api_contact_submit():
        """Public endpoint — receives contact form submissions from icdev.ai."""
        # CORS for cross-origin from icdev.ai
        if flask_request.method == "OPTIONS":
            resp = app.make_default_options_response()
            resp.headers["Access-Control-Allow-Origin"] = "https://icdev.ai"
            resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return resp

        try:
            # Accept both JSON and form data
            if flask_request.is_json:
                data = flask_request.get_json()
            else:
                data = flask_request.form.to_dict()

            name = (data.get("name") or "").strip()
            email = (data.get("email") or "").strip()
            if not name or not email:
                return jsonify({"error": "Name and email are required"}), 400

            sub_id = f"lead-{uuid.uuid4().hex[:12]}"
            now = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ).isoformat()

            conn = _get_db()
            try:
                conn.execute(
                    "INSERT INTO contact_submissions "
                    "(id, name, email, organization, role, interest, message, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sub_id, name, email,
                     (data.get("organization") or "").strip(),
                     (data.get("role") or "").strip(),
                     (data.get("interest") or "").strip(),
                     (data.get("message") or "").strip(),
                     "new", now),
                )
                conn.commit()
            finally:
                conn.close()

            resp = jsonify({"ok": True, "id": sub_id})
            resp.headers["Access-Control-Allow-Origin"] = "https://icdev.ai"
            return resp
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/leads")
    def leads_page():
        """Contact form submissions dashboard."""
        conn = _get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM contact_submissions ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
            submissions = [dict(r) for r in rows]
            stats = {
                "total": len(submissions),
                "new": sum(1 for s in submissions if s.get("status") == "new"),
                "contacted": sum(1 for s in submissions if s.get("status") == "contacted"),
                "closed": sum(1 for s in submissions if s.get("status") == "closed"),
            }
        except Exception:
            submissions = []
            stats = {"total": 0, "new": 0, "contacted": 0, "closed": 0}
        finally:
            conn.close()
        return render_template("leads.html", submissions=submissions, stats=stats)

    @app.route("/api/leads/<lead_id>/status", methods=["POST"])
    def api_lead_update_status(lead_id):
        """Update a lead's status."""
        data = flask_request.get_json(silent=True) or {}
        new_status = data.get("status", "")
        notes = data.get("notes", "")
        if new_status not in ("new", "contacted", "qualified", "closed"):
            return jsonify({"error": "Invalid status"}), 400
        now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
        conn = _get_db()
        try:
            conn.execute(
                "UPDATE contact_submissions SET status = ?, notes = ?, updated_at = ? WHERE id = ?",
                (new_status, notes, now, lead_id),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "id": lead_id, "status": new_status})

    # ── Genesis v2.0 — Autonomous Research Lab Dashboard ──────────────────────

    @app.route("/notifications")
    def notifications_page():
        """Notification Gateway — adapter config, delivery history, routing rules."""
        try:
            from tools.notifications.gateway import NotificationGateway
            gw = NotificationGateway()
            health = gw.health()
        except Exception:
            health = {"enabled": False, "adapters": {}, "error": "Gateway unavailable"}
        # Recent delivery log
        history = []
        try:
            conn = _get_db()
            history = conn.execute(
                "SELECT * FROM notification_log ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            conn.close()
        except Exception:
            pass
        return render_template("notifications.html", health=health, history=history)

    @app.route("/genesis")
    def genesis():
        """Genesis v2.0 — Autonomous Research Lab dashboard (multi-app)."""
        app_key = flask_request.args.get("app", "icdev")
        # Gather status for all apps
        all_status = {}
        for key, cfg in GENESIS_APPS.items():
            if Path(cfg["root"]).exists():
                try:
                    all_status[key] = _genesis_run(key, ["--status", "--json"])
                    all_status[key]["_name"] = cfg["name"]
                    all_status[key]["_available"] = True
                except Exception as exc:
                    all_status[key] = {"_name": cfg["name"], "_available": False, "error": str(exc)}
            else:
                all_status[key] = {"_name": cfg["name"], "_available": False, "error": "Directory not found"}
        # Active app status
        status = all_status.get(app_key, all_status.get("icdev", {}))
        return render_template("genesis.html", status=status, all_apps=all_status,
                               active_app=app_key, genesis_apps=GENESIS_APPS)


    @app.route("/api/genesis/status", methods=["GET"])
    def api_genesis_status():
        app_key = flask_request.args.get("app", "icdev")
        try:
            return jsonify(_genesis_run(app_key, ["--status", "--json"]))
        except Exception as exc:
            # DB fallback: query genesis_runs for last known status
            try:
                conn = _get_db()
                row = conn.execute(
                    "SELECT * FROM genesis_runs WHERE app_key = ? ORDER BY started_at DESC LIMIT 1",
                    (app_key,),
                ).fetchone()
                conn.close()
                if row:
                    return jsonify({"status": "cached", "app": app_key, "last_run": dict(row), "daemon_error": str(exc)})
            except Exception:
                pass
            return jsonify({"error": str(exc), "app": app_key}), 500


    @app.route("/api/genesis/all-status", methods=["GET"])
    def api_genesis_all_status():
        """Get status for all Genesis apps."""
        results = {}
        for key, cfg in GENESIS_APPS.items():
            if Path(cfg["root"]).exists():
                try:
                    results[key] = _genesis_run(key, ["--status", "--json"])
                    results[key]["_name"] = cfg["name"]
                except Exception as exc:
                    results[key] = {"_name": cfg["name"], "error": str(exc)}
            else:
                results[key] = {"_name": cfg["name"], "error": "not_found"}
        return jsonify(results)


    @app.route("/api/genesis/reflex/<name>", methods=["POST"])
    def api_genesis_run_reflex(name):
        """Run a single Genesis reflex on-demand."""
        app_key = flask_request.args.get("app", "icdev")
        allowed = ["research", "scout", "audit", "report", "comply", "ingest",
                   "market", "publish", "test", "learn", "heal", "evolve", "docs"]
        if name not in allowed:
            return jsonify({"error": f"Unknown reflex: {name}"}), 400
        try:
            result = _genesis_run(app_key, ["--reflex", name, "--json"], timeout=300)
            # Log to DB for audit trail
            try:
                conn = _get_db()
                conn.execute(
                    "INSERT INTO audit_trail (event_type, action, details, created_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    ("config_changed", f"genesis_reflex:{name}", json.dumps({"app": app_key, "reflex": name})),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    @app.route("/api/genesis/promoter/stats", methods=["GET"])
    def api_genesis_promoter_stats():
        app_key = flask_request.args.get("app", "icdev")
        cfg = _genesis_app(app_key)
        if not cfg.get("promoter"):
            # No promoter — query DB directly for GKP counts
            try:
                conn = _genesis_db(app_key)
                try:
                    total = conn.execute("SELECT COUNT(*) FROM genesis_gkp").fetchone()[0]
                    by_status = {}
                    for row in conn.execute("SELECT promotion_status, COUNT(*) as cnt FROM genesis_gkp GROUP BY promotion_status").fetchall():
                        by_status[row[0]] = row[1]
                    return jsonify({"total_gkps": total, "by_status": by_status})
                finally:
                    conn.close()
            except Exception as exc:
                return jsonify({"total_gkps": 0, "by_status": {}, "note": str(exc)})
        try:
            import subprocess
            _utf8_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": cfg["root"]}
            result = subprocess.run(
                [sys.executable, cfg["promoter"], "--stats", "--json"],
                capture_output=True, text=True, timeout=15, cwd=cfg["root"],
                env=_utf8_env,
            )
            stdout = result.stdout.strip()
            json_start = stdout.find("{")
            if json_start >= 0:
                return jsonify(json.loads(stdout[json_start:]))
            return jsonify({"error": "parse_failed"}), 500
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    def _gkp_hidden_sources():
        """Load source patterns marked hide_from_dashboard in genesis auto_promote rules."""
        try:
            cfg_path = BASE_DIR / "args" / "genesis_config.yaml"
            if cfg_path.exists():
                import yaml
                cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                rules = cfg.get("promoter", {}).get("auto_promote", [])
                return [
                    r["source_contains"].lower()
                    for r in rules
                    if r.get("hide_from_dashboard") and r.get("source_contains")
                ]
        except Exception:
            pass
        return []

    @app.route("/api/genesis/gkps", methods=["GET"])
    def api_genesis_gkps():
        """List GKPs with optional status filter."""
        app_key = flask_request.args.get("app", "icdev")
        status_filter = flask_request.args.get("status", None)
        show_hidden = flask_request.args.get("show_hidden", "false") == "true"
        limit = int(flask_request.args.get("limit", "100"))
        try:
            conn = _genesis_db(app_key)
            try:
                if status_filter:
                    rows = conn.execute(
                        "SELECT * FROM genesis_gkp WHERE promotion_status = ? ORDER BY created_at DESC LIMIT ?",
                        (status_filter, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM genesis_gkp ORDER BY created_at DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                gkps = [dict(r) for r in rows]

                # Filter out hidden sources unless explicitly requested
                if not show_hidden:
                    hidden = _gkp_hidden_sources()
                    if hidden:
                        def _is_hidden(g):
                            try:
                                p = json.loads(g["payload"]) if isinstance(g["payload"], str) else (g["payload"] or {})
                                src = (p.get("source", "") or "").lower()
                                return any(h in src for h in hidden)
                            except Exception:
                                return False
                        gkps = [g for g in gkps if not _is_hidden(g)]

                return jsonify({"gkps": gkps, "count": len(gkps)})
            finally:
                conn.close()
        except Exception as exc:
            return jsonify({"gkps": [], "count": 0, "note": str(exc)})


    @app.route("/api/genesis/gkps/<gkp_id>", methods=["GET"])
    def api_genesis_gkp_detail(gkp_id):
        """Get a single GKP by ID."""
        app_key = flask_request.args.get("app", "icdev")
        try:
            conn = _genesis_db(app_key)
            try:
                row = conn.execute("SELECT * FROM genesis_gkp WHERE id = ?", (gkp_id,)).fetchone()
                if not row:
                    return jsonify({"error": "GKP not found"}), 404
                return jsonify(dict(row))
            finally:
                conn.close()
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    @app.route("/api/genesis/gkps/<gkp_id>/promote", methods=["POST"])
    def api_genesis_promote_gkp(gkp_id):
        """Promote a GKP to v1.x."""
        app_key = flask_request.args.get("app", "icdev")
        cfg = _genesis_app(app_key)
        if not cfg.get("promoter"):
            # Manual DB update for apps without a promoter
            try:
                conn = _genesis_db(app_key)
                try:
                    conn.execute("UPDATE genesis_gkp SET promotion_status = 'promoted', promoted_at = datetime('now') WHERE id = ?", (gkp_id,))
                    conn.commit()
                    return jsonify({"status": "promoted", "gkp_id": gkp_id})
                finally:
                    conn.close()
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500
        try:
            import subprocess as _sp
            _utf8_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": cfg["root"]}
            result = _sp.run(
                [sys.executable, cfg["promoter"], "--promote", gkp_id, "--json"],
                capture_output=True, text=True, timeout=30, cwd=cfg["root"],
                env=_utf8_env,
            )
            stdout = result.stdout.strip()
            json_start = stdout.find("{")
            if json_start >= 0:
                return jsonify(json.loads(stdout[json_start:]))
            return jsonify({"error": result.stderr or "promote failed"}), 500
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    @app.route("/api/genesis/gkps/<gkp_id>/reject", methods=["POST"])
    def api_genesis_reject_gkp(gkp_id):
        """Reject a GKP."""
        app_key = flask_request.args.get("app", "icdev")
        cfg = _genesis_app(app_key)
        data = flask_request.get_json(silent=True) or {}
        reason = data.get("reason", "Rejected via dashboard")
        if not cfg.get("promoter"):
            try:
                conn = _genesis_db(app_key)
                try:
                    conn.execute("UPDATE genesis_gkp SET promotion_status = 'rejected' WHERE id = ?", (gkp_id,))
                    conn.commit()
                    return jsonify({"status": "rejected", "gkp_id": gkp_id, "reason": reason})
                finally:
                    conn.close()
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500
        try:
            import subprocess as _sp
            _utf8_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": cfg["root"]}
            result = _sp.run(
                [sys.executable, cfg["promoter"], "--reject", gkp_id, "--reason", reason, "--json"],
                capture_output=True, text=True, timeout=30, cwd=cfg["root"],
                env=_utf8_env,
            )
            stdout = result.stdout.strip()
            json_start = stdout.find("{")
            if json_start >= 0:
                return jsonify(json.loads(stdout[json_start:]))
            return jsonify({"error": result.stderr or "reject failed"}), 500
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    @app.route("/api/genesis/gkps/auto-promote", methods=["POST"])
    def api_genesis_auto_promote():
        """Auto-promote all eligible GKPs."""
        app_key = flask_request.args.get("app", "icdev")
        cfg = _genesis_app(app_key)
        if not cfg.get("promoter"):
            # DB fallback: check for pending GKPs directly
            try:
                conn = _get_db()
                pending = conn.execute(
                    "SELECT COUNT(*) FROM genesis_knowledge_packets WHERE status = 'pending'"
                ).fetchone()[0]
                conn.close()
                return jsonify({"error": "No promoter configured", "auto_promoted": 0, "pending_gkps": pending}), 400
            except Exception:
                return jsonify({"error": "No promoter configured for this app", "auto_promoted": 0}), 400
        try:
            import subprocess as _sp
            _utf8_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": cfg["root"]}
            result = _sp.run(
                [sys.executable, cfg["promoter"], "--auto-promote", "--json"],
                capture_output=True, text=True, timeout=30, cwd=cfg["root"],
                env=_utf8_env,
            )
            stdout = result.stdout.strip()
            json_start = stdout.find("{")
            if json_start >= 0:
                return jsonify(json.loads(stdout[json_start:]))
            return jsonify({"auto_promoted": 0, "results": []})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500


    @app.route("/api/genesis/feedback/priorities", methods=["GET"])
    def api_genesis_feedback_priorities():
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "tools/genesis/feedback_collector.py", "--priorities", "--json"],
                capture_output=True, text=True, timeout=15, cwd=BASE_DIR,
            )
            stdout = result.stdout.strip()
            json_start = stdout.find("{")
            if json_start >= 0:
                return jsonify(json.loads(stdout[json_start:]))
            return jsonify({"error": "parse_failed"}), 500
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500



    # ---- Phase 67: Engineering Review Board ----
    @app.route("/review-board")
    def review_board_page():
        """Engineering Review Board — multi-persona analysis dashboard."""
        conn = _get_db()
        health_score = None
        health_grade = "N/A"
        health_trend = "stable"
        health_trend_data = []
        correlation_groups = []
        remediation_stats = {}
        try:
            # Reflex states
            try:
                reflex_rows = conn.execute(
                    "SELECT * FROM review_board_reflex_state ORDER BY reflex_name"
                ).fetchall()
                reflexes = [dict(r) for r in reflex_rows]
            except Exception:
                reflexes = []

            # Recent findings
            try:
                finding_rows = conn.execute(
                    "SELECT * FROM review_board_findings "
                    "ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
                findings = [dict(r) for r in finding_rows]
            except Exception:
                findings = []

            # Severity summary
            try:
                severity_rows = conn.execute(
                    "SELECT severity, COUNT(*) as cnt FROM review_board_findings "
                    "GROUP BY severity"
                ).fetchall()
                severity_summary = {r[0]: r[1] for r in severity_rows}
            except Exception:
                severity_summary = {}

            # Recent audit events
            try:
                audit_rows = conn.execute(
                    "SELECT * FROM review_board_audit "
                    "ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
                audit_events = [dict(r) for r in audit_rows]
            except Exception:
                audit_events = []

            total_findings = sum(severity_summary.values())

            # Health score + trend
            try:
                latest_health = conn.execute(
                    "SELECT score, grade, trend FROM review_board_health_history "
                    "ORDER BY created_at DESC LIMIT 1"
                ).fetchone()
                if latest_health:
                    health_score = latest_health[0]
                    health_grade = latest_health[1]
                    health_trend = latest_health[2]
                trend_rows = conn.execute(
                    "SELECT score, created_at FROM review_board_health_history "
                    "ORDER BY created_at DESC LIMIT 20"
                ).fetchall()
                health_trend_data = [{"score": r[0], "created_at": r[1]} for r in reversed(list(trend_rows))]
            except Exception:
                pass

            # Correlation groups
            try:
                from tools.review_board.correlator import correlate_findings
                corr = correlate_findings()
                correlation_groups = corr.get("groups", [])
            except Exception:
                pass

            # Remediation stats
            try:
                rem_row = conn.execute(
                    "SELECT COUNT(*) FROM review_board_remediation_log "
                    "WHERE tier = 'auto_fix' AND status IN ('fixed', 'verified') "
                    "AND created_at > datetime('now', '-1 hour')"
                ).fetchone()
                remediation_stats = {"auto_fixes_last_hour": rem_row[0] if rem_row else 0}
            except Exception:
                pass

            return render_template("review_board.html",
                                   reflexes=reflexes,
                                   findings=findings,
                                   severity_summary=severity_summary,
                                   total_findings=total_findings,
                                   audit_events=audit_events,
                                   health_score=health_score,
                                   health_grade=health_grade,
                                   health_trend=health_trend,
                                   health_trend_data=health_trend_data,
                                   correlation_groups=correlation_groups,
                                   remediation_stats=remediation_stats)
        except Exception as e:
            import traceback
            traceback.print_exc()
            return render_template("review_board.html",
                                   reflexes=[], findings=[],
                                   severity_summary={}, total_findings=0,
                                   audit_events=[], error=str(e))
        finally:
            conn.close()

    @app.route("/api/review-board/status", methods=["GET"])
    def api_review_board_status():
        """Review Board JSON status — daemon CLI with DB fallback."""
        try:
            import subprocess as _sp
            _utf8_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            result = _sp.run(
                [sys.executable, "tools/review_board/daemon.py", "--status", "--json"],
                capture_output=True, text=True, timeout=15, cwd=str(BASE_DIR),
                env=_utf8_env,
            )
            if result.returncode == 0 and result.stdout.strip():
                return jsonify(json.loads(result.stdout))
        except Exception:
            pass
        # DB fallback: query review_board_findings for summary
        conn = _get_db()
        try:
            total = conn.execute("SELECT COUNT(*) FROM review_board_findings").fetchone()[0]
            by_sev = {}
            for row in conn.execute(
                "SELECT severity, COUNT(*) as cnt FROM review_board_findings GROUP BY severity"
            ).fetchall():
                by_sev[row["severity"]] = row["cnt"]
            return jsonify({"status": "db_fallback", "total_findings": total, "by_severity": by_sev})
        except Exception as exc:
            return jsonify({"error": str(exc), "status": "unavailable"}), 500
        finally:
            conn.close()

    @app.route("/api/review-board/findings", methods=["GET"])
    def api_review_board_findings():
        """Get review board findings with optional severity filter."""
        severity = flask_request.args.get("severity")
        limit = int(flask_request.args.get("limit", "100"))
        conn = _get_db()
        try:
            if severity:
                rows = conn.execute(
                    "SELECT * FROM review_board_findings "
                    "WHERE severity = ? ORDER BY created_at DESC LIMIT ?",
                    (severity, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM review_board_findings "
                    "ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return jsonify({"findings": [dict(r) for r in rows], "count": len(rows)})
        except Exception as exc:
            return jsonify({"findings": [], "count": 0, "error": str(exc)})
        finally:
            conn.close()

    @app.route("/api/review-board/reflex/<name>", methods=["POST"])
    def api_review_board_run_reflex(name):
        """Run a single Review Board reflex on-demand — daemon CLI with audit trail."""
        allowed = ["sre", "qa", "security", "perf", "ux", "docs", "product"]
        if name not in allowed:
            return jsonify({"error": f"Unknown reflex: {name}"}), 400
        try:
            import subprocess as _sp
            _utf8_env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            result = _sp.run(
                [sys.executable, "tools/review_board/daemon.py", "--reflex", name, "--json"],
                capture_output=True, text=True, timeout=300, cwd=str(BASE_DIR),
                env=_utf8_env,
            )
            # Log to audit trail
            try:
                conn = _get_db()
                conn.execute(
                    "INSERT INTO audit_trail (event_type, action, details, created_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    ("config_changed", f"review_board_reflex:{name}", json.dumps({"returncode": result.returncode})),
                )
                conn.commit()
                conn.close()
            except Exception:
                pass
            if result.returncode == 0 and result.stdout.strip():
                return jsonify(json.loads(result.stdout))
            return jsonify({"status": "completed", "stdout": result.stdout[:500]}), 200
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ── Bayesian Autoresearch Dashboard (Phase 67) ────────────────────────────

    @app.route("/autoresearch")
    def autoresearch_page():
        """Bayesian Autoresearch — autonomous experiment dashboard."""
        return render_template("autoresearch.html")

    @app.route("/api/autoresearch/summary", methods=["GET"])
    def api_autoresearch_summary():
        """Get autoresearch summary stats."""
        try:
            conn = get_connection(db_path=str(DB_PATH))
            total = conn.execute(
                "SELECT COUNT(*) as cnt FROM experiment_results"
            ).fetchone()
            kept = conn.execute(
                "SELECT COUNT(*) as cnt FROM experiment_results WHERE decision = 'keep'"
            ).fetchone()
            domains = conn.execute(
                "SELECT DISTINCT domain FROM experiment_results"
            ).fetchall()
            best = conn.execute(
                "SELECT MAX(improvement_pct) as best FROM experiment_results WHERE decision = 'keep'"
            ).fetchone()
            conn.close()

            total_count = total["cnt"] if total else 0
            kept_count = kept["cnt"] if kept else 0
            return jsonify({
                "total_experiments": total_count,
                "acceptance_rate": round(kept_count / max(total_count, 1) * 100, 1),
                "active_domains": len(domains) if domains else 0,
                "best_improvement": round(best["best"] or 0, 2) if best else 0,
            })
        except Exception:
            return jsonify({
                "total_experiments": 0,
                "acceptance_rate": 0,
                "active_domains": 0,
                "best_improvement": 0,
            })

    @app.route("/api/autoresearch/experiments", methods=["GET"])
    def api_autoresearch_experiments():
        """Get experiment results list."""
        try:
            conn = get_connection(db_path=str(DB_PATH))
            rows = conn.execute(
                "SELECT * FROM experiment_results ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
            conn.close()
            return jsonify({"experiments": [dict(r) for r in rows]})
        except Exception:
            return jsonify({"experiments": []})

    # ================================================================
    # Phase 69: Chat Personas API (D-CU-3)
    # ================================================================

    @app.route("/api/chat/personas")
    def api_chat_personas():
        """Return agent persona registry as JSON."""
        try:
            import yaml
            personas_path = BASE_DIR / "args" / "chat_personas.yaml"
            if personas_path.exists():
                with open(personas_path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                return jsonify(data)
            return jsonify({"personas": {}})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ================================================================
    # Phase 69: Codebase Assistant API (D-CA-5 to D-CA-8)
    # ================================================================

    @app.route("/api/assistant/query", methods=["POST"])
    def api_assistant_query():
        """Handle codebase assistant queries."""
        try:
            from tools.dashboard.assistant_manager import query
            data = flask_request.get_json(force=True, silent=True) or {}
            result = query(
                question=data.get("question", ""),
                scope=data.get("scope"),
                context_id=data.get("context_id"),
                page_path=data.get("page_path"),
            )
            return jsonify(result)
        except Exception as exc:
            app.logger.error("Assistant query error: %s", exc)
            return jsonify({"answer": f"Error: {exc}", "citations": [], "source": "error"}), 500

    @app.route("/api/assistant/status")
    def api_assistant_status():
        """Return codebase indexer status."""
        try:
            from tools.dashboard.assistant_manager import get_status
            return jsonify(get_status())
        except Exception as exc:
            return jsonify({"indexed_files": 0, "index_status": "unavailable", "error": str(exc)})

    @app.route("/api/assistant/scope", methods=["POST"])
    def api_assistant_scope():
        """Set assistant scope to a specific module."""
        try:
            from tools.dashboard.assistant_config import files_in_scope
            data = flask_request.get_json(force=True, silent=True) or {}
            scope = data.get("scope", "")
            files = files_in_scope(scope) if scope else []
            return jsonify({"ok": True, "files_in_scope": len(files)})
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    @app.route("/api/assistant/suggestions")
    def api_assistant_suggestions():
        """Return contextual question suggestions for the widget."""
        try:
            from tools.dashboard.assistant_manager import get_suggestions
            page_path = flask_request.args.get("page_path", "")
            return jsonify({"suggestions": get_suggestions(page_path)})
        except Exception:
            return jsonify({"suggestions": [
                "How is the ICDEV™ codebase structured?",
                "What does the LLM router do?",
                "How does the RAG retriever work?",
            ]})

    # ── ClawHub Skill Browser (Phase 69) ───────────────────────────────

    @app.route("/clawhub")
    def clawhub():
        """ClawHub — discover and import OpenClaw skills."""
        imports = []
        enabled = os.environ.get("ICDEV_OPENCLAW_ENABLED", "").lower() in ("true", "1", "yes")
        try:
            from tools.marketplace.openclaw_bridge import list_quarantine
            result = list_quarantine()
            if result.get("success"):
                imports = [
                    (i.get("id",""), i.get("skill_name",""), i.get("author", i.get("openclaw_author","")),
                     i.get("scan_status",""), i.get("status",""),
                     i.get("trust_score",0.3), i.get("has_scripts", i.get("has_executable_content", False)),
                     i.get("review_required",False), str(i.get("created_at",""))[:19],
                     i.get("rejected_by",""), i.get("rejected_reason",""),
                     i.get("failed_gates", []))
                    for i in result.get("imports", [])
                ]
        except Exception:
            imports = []
        return render_template("clawhub.html", imports=imports, enabled=enabled)

    @app.route("/api/clawhub/search")
    def api_clawhub_search():
        """Search ClawHub for skills."""
        query = flask_request.args.get("q", "")
        limit = int(flask_request.args.get("limit", "10"))
        if not query:
            return jsonify({"error": "Missing 'q' parameter"})
        try:
            from tools.databridge.connectors.clawhub_connector import ClawHubConnector
            conn = ClawHubConnector()
            conn.connect({})
            results = conn.search_skills(query, limit=limit)
            conn.disconnect()
            return jsonify({"success": True, "results": results or []})
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/skill/<slug>")
    def api_clawhub_detail(slug):
        """Get skill detail from ClawHub."""
        try:
            from tools.databridge.connectors.clawhub_connector import ClawHubConnector
            conn = ClawHubConnector()
            conn.connect({})
            detail = conn.get_skill(slug)
            conn.disconnect()
            return jsonify(detail or {"error": "Not found"})
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/import", methods=["POST"])
    def api_clawhub_import():
        """Fetch + import a skill from ClawHub."""
        data = flask_request.get_json(silent=True) or {}
        slug = data.get("slug", "")
        tenant_id = data.get("tenant_id", "default")
        imported_by = data.get("imported_by", "dashboard-user")
        if not slug:
            return jsonify({"error": "Missing 'slug'"})
        try:
            from tools.marketplace.openclaw_bridge import fetch_and_import
            result = fetch_and_import(slug, tenant_id, imported_by)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/promote", methods=["POST"])
    def api_clawhub_promote():
        """Promote a quarantined import (auto-approves review if needed)."""
        data = flask_request.get_json(silent=True) or {}
        import_id = data.get("import_id", "")
        promoted_by = data.get("promoted_by", "dashboard-isso")
        if not import_id:
            return jsonify({"error": "Missing 'import_id'"})
        try:
            from tools.marketplace.openclaw_bridge import promote_import, _get_db
            # Auto-approve review if not yet done (dashboard user = ISSO)
            conn = _get_db()
            try:
                cur = conn.cursor()
                cur.execute(
                    "UPDATE openclaw_imports SET review_id = %s WHERE id = %s AND review_id IS NULL",
                    (f"rev-dash-{import_id[:8]}", import_id),
                )
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
            finally:
                conn.close()
            result = promote_import(import_id, promoted_by)
            # Trigger companion sync so skill distributes to all 9 LLM platforms
            if result.get("success"):
                try:
                    import subprocess as _sp
                    _sp.Popen(
                        [sys.executable, "tools/dx/companion.py", "--sync", "--write", "--json"],
                        cwd=str(BASE_DIR), stdout=_sp.DEVNULL, stderr=_sp.DEVNULL,
                    )
                except Exception:
                    pass  # Non-blocking — sync failure doesn't fail promotion
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/reject", methods=["POST"])
    def api_clawhub_reject():
        """Reject a quarantined import."""
        data = flask_request.get_json(silent=True) or {}
        import_id = data.get("import_id", "")
        rejected_by = data.get("rejected_by", "dashboard-user")
        reason = data.get("reason", "Rejected via dashboard")
        if not import_id:
            return jsonify({"error": "Missing 'import_id'"})
        try:
            from tools.marketplace.openclaw_bridge import reject_import
            return jsonify(reject_import(import_id, rejected_by, reason))
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/install-to-project", methods=["POST"])
    def api_clawhub_install():
        """Copy a promoted skill to .claude/skills/ for local use."""
        data = flask_request.get_json(silent=True) or {}
        import_id = data.get("import_id", "")
        if not import_id:
            return jsonify({"error": "Missing 'import_id'"})
        try:
            from tools.marketplace.openclaw_bridge import _get_db
            import re as _re
            import shutil as _shutil
            conn = _get_db()
            cur = conn.cursor()
            cur.execute("SELECT skill_name, quarantine_path, status FROM openclaw_imports WHERE id = %s", (import_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return jsonify({"error": "Import not found"})
            skill_name = row[0] if not hasattr(row, "keys") else row["skill_name"]
            qpath = row[1] if not hasattr(row, "keys") else row["quarantine_path"]
            status = row[2] if not hasattr(row, "keys") else row["status"]
            if status != "promoted":
                return jsonify({"error": f"Must be promoted first (current: {status})"})
            src = Path(qpath)
            if not src.is_dir():
                return jsonify({"error": "Quarantine path not found"})
            slug = _re.sub(r"[^a-z0-9-]", "-", skill_name.lower()).strip("-")[:63] or "imported-skill"
            dest = Path(BASE_DIR) / ".claude" / "skills" / slug
            dest.mkdir(parents=True, exist_ok=True)
            for fname in ("SKILL.md", "skill.md"):
                f = src / fname
                if f.exists():
                    _shutil.copy2(f, dest / "SKILL.md")
                    break
            for subdir in ("scripts", "context"):
                sd = src / subdir
                dd = dest / subdir
                if sd.is_dir():
                    if dd.exists():
                        _shutil.rmtree(dd)
                    _shutil.copytree(sd, dd)
            files = [str(f.relative_to(dest)) for f in dest.rglob("*") if f.is_file()]
            return jsonify({"success": True, "installed_to": str(dest), "slug": slug, "files": files, "file_count": len(files)})
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/check-update")
    def api_clawhub_check_update():
        """Check if a ClawHub skill has a newer version."""
        import_id = flask_request.args.get("import_id", "")
        if not import_id:
            return jsonify({"error": "Missing 'import_id'"})
        try:
            from tools.marketplace.openclaw_bridge import _get_db
            conn = _get_db()
            cur = conn.cursor()
            cur.execute("SELECT openclaw_slug, skill_version FROM openclaw_imports WHERE id = %s", (import_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return jsonify({"error": "Import not found"})
            slug = row[0] if not hasattr(row, "keys") else row["openclaw_slug"]
            current_ver = str(row[1] if not hasattr(row, "keys") else row["skill_version"])
            from tools.databridge.connectors.clawhub_connector import ClawHubConnector
            c = ClawHubConnector()
            c.connect({})
            detail = c.get_skill(slug)
            c.disconnect()
            if not detail or not detail.get("latestVersion"):
                return jsonify({"success": True, "update_available": False})
            latest_ver = detail["latestVersion"].get("version", "")
            return jsonify({
                "success": True, "current_version": current_ver, "latest_version": latest_ver,
                "update_available": str(latest_ver) != str(current_ver),
                "changelog": (detail["latestVersion"].get("changelog", "") or "")[:300],
            })
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/bulk-import", methods=["POST"])
    def api_clawhub_bulk_import():
        """Import multiple skills from ClawHub."""
        data = flask_request.get_json(silent=True) or {}
        slugs = data.get("slugs", [])
        tenant_id = data.get("tenant_id", "default")
        imported_by = data.get("imported_by", "dashboard-user")
        if not slugs:
            return jsonify({"error": "Missing 'slugs' list"})
        results = []
        for slug in slugs[:10]:  # Cap at 10
            try:
                from tools.marketplace.openclaw_bridge import fetch_and_import
                r = fetch_and_import(slug, tenant_id, imported_by)
                results.append({"slug": slug, "success": r.get("success", False), "error": r.get("error"), "import_id": r.get("import_id")})
            except Exception as exc:
                results.append({"slug": slug, "success": False, "error": str(exc)})
        succeeded = sum(1 for r in results if r["success"])
        return jsonify({"success": True, "total": len(results), "succeeded": succeeded, "failed": len(results) - succeeded, "results": results})

    @app.route("/api/clawhub/rate", methods=["POST"])
    def api_clawhub_rate():
        """Rate an imported skill (1-5 stars, adjusts trust score)."""
        data = flask_request.get_json(silent=True) or {}
        import_id = data.get("import_id", "")
        rating = data.get("rating", 0)
        if not import_id or not rating:
            return jsonify({"error": "Missing 'import_id' or 'rating'"})
        try:
            rating = int(rating)
            if rating < 1 or rating > 5:
                return jsonify({"error": "Rating must be 1-5"})
            bump = {1: -0.05, 2: -0.02, 3: 0.0, 4: 0.03, 5: 0.05}[rating]
            from tools.marketplace.openclaw_bridge import _get_db
            conn = _get_db()
            conn.cursor().execute(
                "UPDATE openclaw_imports SET trust_score = MIN(1.0, MAX(0.0, trust_score + ?)), updated_at = datetime('now') WHERE id = ?",
                (bump, import_id),
            )
            conn.commit()
            conn.close()
            return jsonify({"success": True, "rating": rating, "trust_adjustment": bump})
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/view-skill")
    def api_clawhub_view_skill():
        """Return the enhanced SKILL.md content for an imported skill."""
        import_id = flask_request.args.get("import_id", "")
        if not import_id:
            return jsonify({"error": "Missing 'import_id'"})
        try:
            from tools.marketplace.openclaw_bridge import _get_db
            conn = _get_db()
            cur = conn.cursor()
            cur.execute("SELECT skill_name, quarantine_path FROM openclaw_imports WHERE id = %s", (import_id,))
            row = cur.fetchone()
            conn.close()
            if not row:
                return jsonify({"error": f"Import not found: {import_id}"})

            skill_name = row[0] if not hasattr(row, "keys") else row["skill_name"]
            qpath = row[1] if not hasattr(row, "keys") else row["quarantine_path"]

            skill_md = Path(qpath) / "SKILL.md"
            if not skill_md.exists():
                skill_md = Path(qpath) / "skill.md"
            if not skill_md.exists():
                return jsonify({"error": "SKILL.md not found in quarantine"})

            content = skill_md.read_text(encoding="utf-8")

            # List context files
            context_dir = Path(qpath) / "context"
            context_files = []
            if context_dir.is_dir():
                context_files = [f.name for f in sorted(context_dir.iterdir()) if f.is_file()]

            return jsonify({
                "success": True,
                "skill_name": skill_name,
                "import_id": import_id,
                "content": content,
                "content_length": len(content),
                "pre_enrichment": (Path(qpath) / "_pre_enrichment.md").read_text(encoding="utf-8") if (Path(qpath) / "_pre_enrichment.md").exists() else None,
                "context_files": context_files,
            })
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/trust", methods=["POST"])
    def api_clawhub_trust():
        """Update trust score for an imported skill."""
        data = flask_request.get_json(silent=True) or {}
        import_id = data.get("import_id", "")
        trust_score = data.get("trust_score")
        if not import_id or trust_score is None:
            return jsonify({"error": "Missing 'import_id' or 'trust_score'"})
        try:
            trust_score = float(trust_score)
            if trust_score < 0 or trust_score > 1.0:
                return jsonify({"error": "Trust score must be between 0.0 and 1.0"})
            from tools.marketplace.openclaw_bridge import _get_db
            conn = _get_db()
            conn.cursor().execute(
                "UPDATE openclaw_imports SET trust_score = ?, updated_at = datetime('now') WHERE id = ?",
                (trust_score, import_id),
            )
            conn.commit()
            conn.close()
            return jsonify({"success": True, "import_id": import_id, "trust_score": trust_score})
        except Exception as exc:
            return jsonify({"error": str(exc)})

    @app.route("/api/clawhub/revoke", methods=["POST"])
    def api_clawhub_revoke():
        """Revoke (unpromote) a promoted import."""
        data = flask_request.get_json(silent=True) or {}
        import_id = data.get("import_id", "")
        revoked_by = data.get("revoked_by", "dashboard-isso")
        reason = data.get("reason", "Revoked via dashboard")
        if not import_id:
            return jsonify({"error": "Missing 'import_id'"})
        try:
            from tools.marketplace.openclaw_bridge import revoke_import
            return jsonify(revoke_import(import_id, revoked_by, reason))
        except Exception as exc:
            return jsonify({"error": str(exc)})

    # ── ICDEV™ Studio (Phase 72) ─────────────────────────────────────

    @app.route("/studio/workflows")
    def studio_workflows():
        """Studio — Visual Workflow Editor."""
        return render_template("studio/workflow_studio.html")

    @app.route("/studio/marketplace")
    def studio_marketplace():
        """Studio — Marketplace Storefront."""
        return render_template("studio/marketplace.html")

    @app.route("/studio/app-builder")
    def studio_app_builder():
        """Studio — NL App Builder."""
        return render_template("studio/app_builder.html")

    @app.route("/studio/dashboards")
    def studio_dashboards():
        """Studio — Dashboard Builder."""
        return render_template("studio/dashboards.html")

    @app.route("/studio/automations")
    def studio_automations():
        """Studio — Citizen Automation Studio."""
        return render_template("studio/automations.html")

    @app.route("/studio/forms")
    def studio_forms():
        """Studio — Form Builder."""
        return render_template("studio/forms.html")

    @app.route("/studio/cases")
    def studio_cases():
        """Studio — Case Management."""
        return render_template("studio/cases.html")

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ICDEV™ Dashboard")
    parser.add_argument("--port", type=int, default=PORT, help="Port to run on (default: 5000)")
    parser.add_argument("--debug", action="store_true", default=DEBUG, help="Enable debug mode")
    args = parser.parse_args()

    app = create_app()
    print(f"[ICDEV™ Dashboard] Starting on http://127.0.0.1:{args.port}")
    print(f"[ICDEV™ Dashboard] Database: {DB_PATH}")
    print(f"[ICDEV™ Dashboard] CUI Marking: {CUI_BANNER_TOP or '(none)'}")

    # Use SocketIO runner if available (D170), otherwise plain Flask
    socketio = get_socketio()
    if socketio:
        print("[ICDEV™ Dashboard] WebSocket enabled (Flask-SocketIO)")
        socketio.run(app, host="0.0.0.0", port=args.port, debug=args.debug)  # nosec B104 -- intentional bind-all for containerized/dev deployment
    else:
        print("[ICDEV™ Dashboard] WebSocket not available — using HTTP polling")
        app.run(host="0.0.0.0", port=args.port, debug=args.debug)  # nosec B104 -- intentional bind-all for containerized/dev deployment
