#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Playground — Read-only demo application.

Standalone Flask app on port 5001 with no authentication.
Pre-loaded with sample projects, compliance data, and assessments
for demonstration purposes.
"""
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from flask import Flask, render_template

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("icdev.playground")

PLAYGROUND_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = PLAYGROUND_DIR / "templates"
STATIC_DIR = PLAYGROUND_DIR / "static"
DB_PATH = PLAYGROUND_DIR / "playground.db"


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def create_app():
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
        static_folder=str(STATIC_DIR),
        static_url_path="/playground/static",
    )
    app.config["SECRET_KEY"] = "playground-demo-key-not-for-production"

    @app.context_processor
    def inject_demo():
        return {"demo_mode": True, "demo_banner": "DEMO ENVIRONMENT \u2014 NOT FOR PRODUCTION USE"}

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/compliance")
    def compliance():
        conn = _get_db()
        try:
            projects = conn.execute(
                "SELECT id, name, status, compliance_score, classification, impact_level FROM projects ORDER BY name"
            ).fetchall()
            stigs = conn.execute(
                "SELECT id, project_id, rule_id, severity, status, title FROM stig_findings ORDER BY severity"
            ).fetchall()
            poams = conn.execute(
                "SELECT id, project_id, finding, status, severity, milestone, due_date FROM poam_items ORDER BY severity"
            ).fetchall()
            return render_template(
                "compliance.html",
                projects=[dict(r) for r in projects],
                stigs=[dict(r) for r in stigs],
                poams=[dict(r) for r in poams],
            )
        finally:
            conn.close()

    @app.route("/crosswalk")
    def crosswalk():
        conn = _get_db()
        try:
            controls = conn.execute(
                "SELECT control_id, title, status FROM nist_controls ORDER BY control_id"
            ).fetchall()
            mappings = conn.execute(
                "SELECT source_control, target_framework, target_requirement, status FROM crosswalk_mappings ORDER BY source_control"
            ).fetchall()
            return render_template(
                "crosswalk.html",
                controls=[dict(r) for r in controls],
                mappings=[dict(r) for r in mappings],
            )
        finally:
            conn.close()

    @app.route("/assessment")
    def assessment():
        conn = _get_db()
        try:
            cmmc = conn.execute(
                "SELECT domain, total, met, partial, not_met, score FROM cmmc_domains ORDER BY domain"
            ).fetchall()
            fedramp = conn.execute(
                "SELECT family, total, satisfied, partial, not_satisfied, score FROM fedramp_families ORDER BY family"
            ).fetchall()
            return render_template(
                "assessment.html",
                cmmc_domains=[dict(r) for r in cmmc],
                fedramp_families=[dict(r) for r in fedramp],
            )
        finally:
            conn.close()

    @app.route("/ssp-preview")
    def ssp_preview():
        conn = _get_db()
        try:
            project = conn.execute(
                "SELECT * FROM projects WHERE id = 'proj-demo-001'"
            ).fetchone()
            controls = conn.execute(
                "SELECT control_id, title, status, implementation_status FROM nist_controls WHERE project_id = 'proj-demo-001' ORDER BY control_id"
            ).fetchall()
            poams = conn.execute(
                "SELECT finding, severity, milestone, due_date FROM poam_items WHERE project_id = 'proj-demo-001' ORDER BY severity"
            ).fetchall()
            return render_template(
                "ssp_preview.html",
                project=dict(project) if project else {},
                controls=[dict(r) for r in controls],
                poams=[dict(r) for r in poams],
            )
        finally:
            conn.close()

    return app


def main():
    logging.basicConfig(level=logging.INFO)
    # Initialize seed data if DB doesn't exist
    if not DB_PATH.exists():
        from tools.playground.seed_data import seed_playground_db
        seed_playground_db(str(DB_PATH))
        logger.info("Playground database seeded at %s", DB_PATH)

    app = create_app()
    port = int(os.environ.get("PLAYGROUND_PORT", 5001))
    logger.info("Starting ICDEV Playground on port %d", port)
    app.run(host="0.0.0.0", port=port, debug=True)


if __name__ == "__main__":
    main()
