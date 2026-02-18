#!/usr/bin/env python3
"""
ICDEV Web Dashboard - Flask Application
========================================
Provides a web interface for monitoring projects, agents, compliance,
and system health within the ICDEV framework.

Usage:
    python tools/dashboard/app.py [--port 5000] [--debug]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup  (so `tools.dashboard.config` is importable when run directly)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from flask import Flask, render_template, jsonify

from tools.dashboard.config import (
    DB_PATH,
    CUI_BANNER_TOP,
    CUI_BANNER_BOTTOM,
    CUI_DESIGNATION,
    PORT,
    DEBUG,
)
from tools.dashboard.api.projects import projects_api
from tools.dashboard.api.agents import agents_api
from tools.dashboard.api.compliance import compliance_api
from tools.dashboard.api.audit import audit_api
from tools.dashboard.api.metrics import metrics_api
from tools.dashboard.api.events import events_bp
from tools.dashboard.api.nlq import nlq_bp

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )

    # Make CUI config available in all templates
    @app.context_processor
    def inject_cui():
        return {
            "cui_banner_top": CUI_BANNER_TOP,
            "cui_banner_bottom": CUI_BANNER_BOTTOM,
            "cui_designation": CUI_DESIGNATION,
        }

    # ---- Register API blueprints ----
    app.register_blueprint(projects_api)
    app.register_blueprint(agents_api)
    app.register_blueprint(compliance_api)
    app.register_blueprint(audit_api)
    app.register_blueprint(metrics_api)
    app.register_blueprint(events_bp)
    app.register_blueprint(nlq_bp)

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

    # ---- HTML page routes ----

    @app.route("/")
    def index():
        """Dashboard home page."""
        conn = _get_db()
        try:
            # Project counts
            total_projects = conn.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()["cnt"]
            active_projects = conn.execute(
                "SELECT COUNT(*) as cnt FROM projects WHERE status = 'active'"
            ).fetchone()["cnt"]
            completed_projects = conn.execute(
                "SELECT COUNT(*) as cnt FROM projects WHERE status = 'completed'"
            ).fetchone()["cnt"]

            # Agent counts
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

            # Firing alert count
            firing_alerts = conn.execute(
                "SELECT COUNT(*) as cnt FROM alerts WHERE status = 'firing'"
            ).fetchone()["cnt"]

            # Open POAM count
            open_poam = conn.execute(
                "SELECT COUNT(*) as cnt FROM poam_items WHERE status = 'open'"
            ).fetchone()["cnt"]

            return render_template(
                "index.html",
                total_projects=total_projects,
                active_projects=active_projects,
                completed_projects=completed_projects,
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

    # ---- Error handlers ----

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
    app.run(host="0.0.0.0", port=args.port, debug=args.debug)
