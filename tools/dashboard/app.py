#!/usr/bin/env python3
# CUI // SP-CTI
"""
ICDEV Web Dashboard - Flask Application
========================================
Provides a web interface for monitoring projects, agents, compliance,
and system health within the ICDEV framework.

Usage:
    python tools/dashboard/app.py [--port 5000] [--debug]
"""

import argparse
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup  (so `tools.dashboard.config` is importable when run directly)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask, render_template, jsonify, request as flask_request, g, session as flask_session, redirect, url_for, flash

from tools.dashboard.config import (
    DB_PATH,
    CUI_BANNER_TOP,
    CUI_BANNER_BOTTOM,
    CUI_DESIGNATION,
    CUI_BANNER_ENABLED,
    BYOK_ENABLED,
    PORT,
    DEBUG,
)
from tools.dashboard.auth import register_dashboard_auth, validate_api_key, log_auth_event
from tools.dashboard.websocket import init_socketio, get_socketio
from tools.dashboard.api.projects import projects_api
from tools.dashboard.api.agents import agents_api
from tools.dashboard.api.compliance import compliance_api
from tools.dashboard.api.audit import audit_api
from tools.dashboard.api.metrics import metrics_api
from tools.dashboard.api.events import events_bp
from tools.dashboard.api.nlq import nlq_bp
from tools.dashboard.api.batch import batch_api
from tools.dashboard.api.diagrams import diagrams_api
from tools.dashboard.api.cicd import cicd_api
from tools.dashboard.api.intake import intake_api
from tools.dashboard.api.admin import admin_api
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
from tools.dashboard.api.proposals import proposals_api
from tools.dashboard.api.govcon import govcon_api
try:
    from tools.dashboard.api.chat import chat_api
    _HAS_CHAT_API = True
except ImportError:
    _HAS_CHAT_API = False
from tools.dashboard.ux_helpers import register_ux_filters

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )

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
    }

    # Make CUI config, role, and user info available in all templates
    @app.context_processor
    def inject_cui():
        role = flask_request.args.get("role", "")
        role_config = ROLE_VIEWS.get(role, None)
        current_user = getattr(g, "current_user", None)
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
        }

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
    app.register_blueprint(proposals_api)
    app.register_blueprint(govcon_api)
    if _HAS_CHAT_API:
        app.register_blueprint(chat_api)

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
        conn = _get_db()
        try:
            # Project status distribution (donut chart)
            project_statuses = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM projects GROUP BY status"
            ).fetchall()

            # Alert trend: last 7 days (line chart)
            alert_trend = conn.execute(
                "SELECT DATE(created_at) as day, COUNT(*) as cnt "
                "FROM alerts WHERE created_at >= DATE('now', '-7 days') "
                "GROUP BY DATE(created_at) ORDER BY day"
            ).fetchall()

            # Compliance posture: open vs closed across POAM + STIG (bar chart)
            poam_open = conn.execute(
                "SELECT COUNT(*) as cnt FROM poam_items WHERE status = 'open'"
            ).fetchone()["cnt"]
            poam_closed = conn.execute(
                "SELECT COUNT(*) as cnt FROM poam_items WHERE status != 'open'"
            ).fetchone()["cnt"]
            stig_open = conn.execute(
                "SELECT COUNT(*) as cnt FROM stig_findings WHERE status = 'Open'"
            ).fetchone()["cnt"]
            stig_closed = conn.execute(
                "SELECT COUNT(*) as cnt FROM stig_findings WHERE status != 'Open'"
            ).fetchone()["cnt"]

            # Deployment frequency: last 7 days (sparkline)
            deploy_trend = conn.execute(
                "SELECT DATE(created_at) as day, COUNT(*) as cnt "
                "FROM deployments WHERE created_at >= DATE('now', '-7 days') "
                "GROUP BY DATE(created_at) ORDER BY day"
            ).fetchall()

            # Agent health (gauge: % active)
            total_agents = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents"
            ).fetchone()["cnt"]
            active_agents = conn.execute(
                "SELECT COUNT(*) as cnt FROM agents WHERE status = 'active'"
            ).fetchone()["cnt"]

            return jsonify({
                "project_statuses": [dict(r) for r in project_statuses],
                "alert_trend": [dict(r) for r in alert_trend],
                "compliance": {
                    "poam": {"open": poam_open, "closed": poam_closed},
                    "stig": {"open": stig_open, "closed": stig_closed},
                },
                "deploy_trend": [dict(r) for r in deploy_trend],
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

            # Recent audit entries
            recent_audit = conn.execute(
                "SELECT * FROM audit_trail ORDER BY created_at DESC LIMIT 10"
            ).fetchall()

            # Recent alerts
            recent_alerts = conn.execute(
                "SELECT * FROM alerts ORDER BY created_at DESC LIMIT 10"
            ).fetchall()

            # Firing alert count (stat bar)
            firing_alerts = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE status = 'firing'"
            ).fetchone()["cnt"]

            # Open POAM count (stat bar)
            open_poam = conn.execute(
                "SELECT COUNT(*) as cnt FROM poam_items WHERE status = 'open'"
            ).fetchone()["cnt"]

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
                recent_alerts=[dict(r) for r in recent_alerts],
                firing_alerts=firing_alerts,
                open_poam=open_poam,
            )
        finally:
            conn.close()

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
            except sqlite3.OperationalError:
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

        Steps are served from config so admins can customize content
        without modifying JavaScript source. tour.js fetches this
        endpoint on init and falls back to built-in defaults if
        the fetch fails (air-gap safe).
        """
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
            "classification": "CUI",
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
        """Revoke a BYOK LLM key."""
        from tools.dashboard.byok import revoke_llm_key
        revoke_llm_key(key_id)
        return jsonify({"status": "revoked"})

    # ---- Phase roadmap route ----

    @app.route("/phases")
    def phases_page():
        """Phase roadmap — all ICDEV phases with status, categories, and progress."""
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
            except sqlite3.OperationalError:
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
            except sqlite3.OperationalError:
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
            except sqlite3.OperationalError:
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
        if flask_request.method == "POST":
            raw_key = flask_request.form.get("api_key", "").strip()
            user = validate_api_key(raw_key)
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
            except sqlite3.OperationalError:
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
            except sqlite3.OperationalError:
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
            except sqlite3.OperationalError:
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
            except sqlite3.OperationalError:
                units = []

            # Fetch validations
            try:
                validations = conn.execute(
                    """SELECT check_type, passed, score, findings, created_at
                       FROM translation_validations WHERE job_id = ?
                       ORDER BY created_at""", (job_id,)
                ).fetchall()
                validations = [dict(v) for v in validations]
            except sqlite3.OperationalError:
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
            except sqlite3.OperationalError:
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
            except sqlite3.OperationalError:
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
            except sqlite3.OperationalError:
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
                        row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
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

    # ---- Proposal Lifecycle Pages (D-PROP) ----

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
            # Sections (JOIN volumes for volume_number/title)
            sections = [dict(r) for r in conn.execute(
                """SELECT s.*, v.volume_number, v.title as volume_title
                   FROM proposal_sections s
                   LEFT JOIN proposal_volumes v ON s.volume_id = v.id
                   WHERE s.opportunity_id = ?
                   ORDER BY v.volume_number, s.section_number""", (opp_id,)
            ).fetchall()]
            # Overdue flag
            from datetime import date
            today = date.today()
            for s in sections:
                s["overdue"] = False
                if s.get("due_date") and s["status"] not in ("final", "submitted"):
                    try:
                        s["overdue"] = date.fromisoformat(s["due_date"]) < today
                    except (ValueError, TypeError):
                        pass
            # Volumes
            volumes = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_volumes WHERE opportunity_id = ? ORDER BY volume_number", (opp_id,)
            ).fetchall()]
            # Compliance items
            compliance_items = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_compliance_matrix WHERE opportunity_id = ?", (opp_id,)
            ).fetchall()]
            # Reviews
            reviews = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_reviews WHERE opportunity_id = ? ORDER BY scheduled_date", (opp_id,)
            ).fetchall()]
            # Findings
            findings = [dict(r) for r in conn.execute(
                """SELECT f.*, r.review_type FROM proposal_review_findings f
                   JOIN proposal_reviews r ON f.review_id = r.id
                   WHERE r.opportunity_id = ?""", (opp_id,)
            ).fetchall()]
            # Stats
            total_sections = len(sections)
            completed_sections = len([s for s in sections if s["status"] in ("final", "submitted")])
            total_compliance = len(compliance_items)
            compliant_count = len([c for c in compliance_items if c.get("compliance_status") == "compliant"])
            coverage_pct = (compliant_count / total_compliance * 100) if total_compliance > 0 else 0
            open_findings = len([f for f in findings if f.get("status") in ("open", "in_progress")])
            critical_findings = len([f for f in findings if f.get("severity") == "critical" and f.get("status") in ("open", "in_progress")])
            # Section status distribution (for donut chart)
            section_status_dist = {}
            for s in sections:
                st = s.get("status", "not_started")
                section_status_dist[st] = section_status_dist.get(st, 0) + 1
            # Finding severity distribution (for bar chart)
            finding_severity_dist = {}
            for f in findings:
                if f.get("status") in ("open", "in_progress"):
                    sev = f.get("severity", "minor")
                    finding_severity_dist[sev] = finding_severity_dist.get(sev, 0) + 1
            # Compliance stats
            cm_compliant = len([c for c in compliance_items if c.get("compliance_status") == "compliant"])
            cm_partial = len([c for c in compliance_items if c.get("compliance_status") == "partial"])
            cm_non_compliant = len([c for c in compliance_items if c.get("compliance_status") == "non_compliant"])
            cm_not_addressed = len([c for c in compliance_items if c.get("compliance_status") == "not_addressed"])
            cm_not_applicable = len([c for c in compliance_items if c.get("compliance_status") == "not_applicable"])
            cm_gap_pct = round(cm_not_addressed / total_compliance * 100) if total_compliance > 0 else 0
            compliance_stats = {
                "total": total_compliance,
                "compliant": cm_compliant,
                "partial": cm_partial,
                "non_compliant": cm_non_compliant,
                "not_addressed": cm_not_addressed,
                "not_applicable": cm_not_applicable,
                "gap_pct": cm_gap_pct,
            }
            # Reviews with nested findings (for template iteration and JS)
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
            # Days left
            days_left = None
            if opp.get("due_date"):
                try:
                    days_left = (date.fromisoformat(opp["due_date"]) - today).days
                except (ValueError, TypeError):
                    pass

            stats = {
                "sections_total": total_sections,
                "sections_complete": completed_sections,
                "compliance_coverage_pct": round(coverage_pct),
                "open_findings": open_findings,
                "critical_findings": critical_findings,
                "section_status_distribution": section_status_dist,
                "finding_severity_distribution": finding_severity_dist,
            }

            return render_template("proposals/detail.html",
                opp=opp, sections=sections, volumes=volumes,
                compliance_items=compliance_items, reviews=reviews_data, findings=findings,
                stats=stats, compliance_stats=compliance_stats,
                reviews_data=reviews_data, days_left=days_left)
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
            # Opp title for breadcrumb
            opp = conn.execute("SELECT title FROM proposal_opportunities WHERE id = ?", (opp_id,)).fetchone()
            opp_title = opp["title"] if opp else "Unknown"
            # Valid transitions
            from tools.dashboard.api.proposals import SECTION_TRANSITIONS
            section["valid_transitions"] = SECTION_TRANSITIONS.get(section["status"], [])
            # Overdue flag
            from datetime import date
            section["overdue"] = False
            if section.get("due_date") and section["status"] not in ("final", "submitted"):
                try:
                    section["overdue"] = date.fromisoformat(section["due_date"]) < date.today()
                except (ValueError, TypeError):
                    pass
            # Compliance items linked to this section
            section["compliance_items"] = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_compliance_matrix WHERE proposal_section_id = ?", (sec_id,)
            ).fetchall()]
            # Findings for this section
            section["findings"] = [dict(r) for r in conn.execute(
                """SELECT f.*, r.review_type FROM proposal_review_findings f
                   JOIN proposal_reviews r ON f.review_id = r.id
                   WHERE f.section_id = ?""", (sec_id,)
            ).fetchall()]
            # Dependencies
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
            # Status history
            section["history"] = [dict(r) for r in conn.execute(
                "SELECT * FROM proposal_status_history WHERE entity_id = ? ORDER BY created_at DESC", (sec_id,)
            ).fetchall()]

            return render_template("proposals/section_detail.html", section=section, opp_title=opp_title)
        finally:
            conn.close()

    # ---- GovCon Intelligence Pages (Phase 59E) ----

    @app.route("/govcon")
    def govcon_pipeline_page():
        """GovCon Intelligence — pipeline status, recent opportunities, domain distribution."""
        conn = _get_db()
        try:
            from tools.govcon.govcon_engine import get_status
            stats = get_status()

            # Recent opportunities
            try:
                opps = conn.execute(
                    "SELECT * FROM sam_gov_opportunities ORDER BY posted_date DESC LIMIT 25"
                ).fetchall()
                opportunities = [dict(r) for r in opps]
            except Exception:
                opportunities = []

            # Which SAM opps are linked to proposals
            linked_opp_ids = set()
            try:
                linked = conn.execute(
                    "SELECT sam_gov_opportunity_id FROM proposal_opportunities WHERE sam_gov_opportunity_id IS NOT NULL"
                ).fetchall()
                linked_opp_ids = {r["sam_gov_opportunity_id"] for r in linked}
            except Exception:
                pass

            return render_template("govcon/pipeline.html",
                stats=stats, opportunities=opportunities, linked_opp_ids=linked_opp_ids)
        except Exception:
            stats = {"total_opportunities": 0, "total_requirements": 0, "total_patterns": 0,
                     "total_capability_maps": 0, "total_drafts": 0, "total_awards": 0,
                     "knowledge_blocks": 0, "linked_proposals": 0, "domain_distribution": {},
                     "last_pipeline_run": None}
            return render_template("govcon/pipeline.html",
                stats=stats, opportunities=[], linked_opp_ids=set())
        finally:
            conn.close()

    @app.route("/govcon/requirements")
    def govcon_requirements_page():
        """GovCon Requirements — pattern frequency, domain heatmap, statement types."""
        conn = _get_db()
        try:
            # Total requirements
            total_requirements = 0
            try:
                r = conn.execute("SELECT COUNT(*) as cnt FROM rfp_shall_statements").fetchone()
                total_requirements = r["cnt"] if r else 0
            except Exception:
                pass

            # Total patterns
            total_patterns = 0
            try:
                r = conn.execute("SELECT COUNT(*) as cnt FROM rfp_requirement_patterns").fetchone()
                total_patterns = r["cnt"] if r else 0
            except Exception:
                pass

            # Domain stats
            domain_stats = {}
            try:
                rows = conn.execute(
                    "SELECT domain_category, COUNT(*) as cnt FROM rfp_shall_statements "
                    "GROUP BY domain_category ORDER BY cnt DESC"
                ).fetchall()
                domain_stats = {r["domain_category"]: {"count": r["cnt"]} for r in rows}
            except Exception:
                pass

            domain_count = len(domain_stats)

            # Top patterns
            patterns = []
            min_frequency = 3
            try:
                rows = conn.execute(
                    "SELECT * FROM rfp_requirement_patterns WHERE frequency >= ? ORDER BY frequency DESC LIMIT 30",
                    (min_frequency,)
                ).fetchall()
                patterns = [dict(r) for r in rows]
            except Exception:
                pass

            top_frequency = patterns[0]["frequency"] if patterns else 0

            # Statement type distribution
            type_stats = {}
            try:
                rows = conn.execute(
                    "SELECT statement_type, COUNT(*) as cnt FROM rfp_shall_statements "
                    "GROUP BY statement_type ORDER BY cnt DESC"
                ).fetchall()
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
            # Overall coverage
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

            # Domain coverage
            domain_coverage = []
            try:
                rows = conn.execute(
                    """SELECT s.domain_category as domain,
                        COUNT(*) as total,
                        SUM(CASE WHEN m.coverage_score >= 0.80 THEN 1 ELSE 0 END) as L,
                        SUM(CASE WHEN m.coverage_score >= 0.40 AND m.coverage_score < 0.80 THEN 1 ELSE 0 END) as M,
                        SUM(CASE WHEN m.coverage_score < 0.40 OR m.coverage_score IS NULL THEN 1 ELSE 0 END) as N
                    FROM rfp_shall_statements s
                    LEFT JOIN icdev_capability_map m ON s.id = m.pattern_id
                    GROUP BY s.domain_category
                    ORDER BY total DESC"""
                ).fetchall()
                domain_coverage = [dict(r) for r in rows]
            except Exception:
                pass

            # Gaps (coverage < 0.40)
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

            # Enhancement recommendations
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

    return app


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ICDEV Dashboard")
    parser.add_argument("--port", type=int, default=PORT, help="Port to run on (default: 5000)")
    parser.add_argument("--debug", action="store_true", default=DEBUG, help="Enable debug mode")
    args = parser.parse_args()

    app = create_app()
    print(f"[ICDEV Dashboard] Starting on http://127.0.0.1:{args.port}")
    print(f"[ICDEV Dashboard] Database: {DB_PATH}")
    print(f"[ICDEV Dashboard] CUI Marking: {CUI_BANNER_TOP}")

    # Use SocketIO runner if available (D170), otherwise plain Flask
    socketio = get_socketio()
    if socketio:
        print("[ICDEV Dashboard] WebSocket enabled (Flask-SocketIO)")
        socketio.run(app, host="0.0.0.0", port=args.port, debug=args.debug)
    else:
        print("[ICDEV Dashboard] WebSocket not available — using HTTP polling")
        app.run(host="0.0.0.0", port=args.port, debug=args.debug)
