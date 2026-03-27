# [TEMPLATE: CUI // SP-CTI]
"""ICDEV™ Network Design Canvas — Flask Blueprint integration.

Fully self-contained Blueprint mounted at /network/ inside the ICDEV dashboard.
Uses ICDEV's auth system, separate network_canvas.db, and feature flag
ICDEV_NETWORK_ENABLED.

Usage in ICDEV dashboard app.py:
    from tools.network.blueprint import create_network_blueprint
    bp = create_network_blueprint()
    if bp:
        app.register_blueprint(bp, url_prefix="/network")
"""

import json
import logging
import os
import shutil
import uuid as _uuid
import zipfile
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint, abort, g, jsonify, redirect, render_template,
    request, session,
)

logger = logging.getLogger("icdev.network")

# ── Paths ──────────────────────────────────────────────────────────────────────
_NETWORK_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _NETWORK_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

# ── Import helper modules ──────────────────────────────────────────────────────
from tools.network.constants import (  # noqa: E402
    CLOUD_OBJECTS, CSP_GROUP_DEFAULTS, COMPLIANCE_REGIMES, BOM_COSTS,
)
from tools.network.simulation import (  # noqa: E402
    _run_simulation, _add_narrative,
)
from tools.network.compliance import (  # noqa: E402
    run_compliance_audit, apply_compliance_fix, generate_xacta_export,
    generate_fips_coverage_report, export_fips_report_html,
)
from tools.network.montecarlo import run_monte_carlo  # noqa: E402
from tools.network.ato_generator import (  # noqa: E402
    generate_ato_package,
    generate_pps_matrix_for_pair,
    get_topology_enclaves,
    export_pps_as_ssp_table,
)
from tools.network.export_import import (  # noqa: E402
    to_drawio, to_svg, to_vdx, import_drawio, import_vdx, import_svg,
)
from tools.network.stig_import import import_stig_file  # noqa: E402


def create_network_blueprint():
    """Create and return the Network Design Canvas Blueprint.

    Returns None if ICDEV_NETWORK_ENABLED is false.
    """
    enabled = os.environ.get("ICDEV_NETWORK_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Network Canvas disabled (ICDEV_NETWORK_ENABLED=%s)", enabled)
        return None

    # Initialize DB
    try:
        from tools.network.db.init_db import init_db
        init_db()
    except Exception as exc:
        logger.warning("Network DB init failed: %s", exc)

    bp = Blueprint(
        "network_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth wrapper (uses ICDEV dashboard session) ────────────────────────
    def nc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                if request.is_json or request.path.startswith("/network/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)
        return decorated

    # ── DB helpers ─────────────────────────────────────────────────────────
    from tools.network.db.init_db import get_connection

    def _now():
        return datetime.now(timezone.utc).isoformat()

    def _audit(action, entity_type, entity_id, details=""):
        user_id = ""
        try:
            user_id = session.get("user_id", "")
        except RuntimeError:
            pass
        try:
            with get_connection() as conn:
                conn.execute(
                    "INSERT INTO nc_audit (action, entity_type, entity_id, details, user_id, ts) "
                    "VALUES (?,?,?,?,?,?)",
                    (action, entity_type, entity_id, details, user_id, _now()),
                )
        except Exception:
            pass

    def _row_to_dict(row):
        return dict(row) if row else {}

    # ── Load config ────────────────────────────────────────────────────────
    try:
        import yaml
        _config_path = _ICDEV_ROOT / "args" / "network_canvas_config.yaml"
        if _config_path.exists():
            with open(_config_path, encoding="utf-8") as f:
                NC_CONFIG = yaml.safe_load(f) or {}
        else:
            NC_CONFIG = {}
    except Exception:
        NC_CONFIG = {}

    # ── Context processor for network templates ────────────────────────────
    @bp.context_processor
    def inject_nc_context():
        user = None
        try:
            user = getattr(g, "current_user", None)
        except RuntimeError:
            pass
        return {
            "classification_banner": NC_CONFIG.get("app", {}).get("classification", ""),
            "current_user": user,
        }

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/")
    @nc_login_required
    def nc_index():
        conn = get_connection()
        topologies = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name, description, classification, created_at, updated_at, "
            "json_array_length(json_extract(graph_json,'$.nodes')) AS node_count, "
            "json_array_length(json_extract(graph_json,'$.edges')) AS edge_count "
            "FROM topologies ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()]
        templates = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name, category, description, tags FROM nc_templates ORDER BY category, name"
        ).fetchall()]
        sims = [_row_to_dict(r) for r in conn.execute(
            "SELECT sr.id, sr.sim_type, sr.ran_at, t.name AS topology_name, sr.result_json "
            "FROM simulation_results sr JOIN topologies t ON t.id=sr.topology_id "
            "ORDER BY sr.ran_at DESC LIMIT 10"
        ).fetchall()]
        total_sims = conn.execute("SELECT COUNT(*) FROM simulation_results").fetchone()[0]
        conn.close()

        for t in templates:
            try:
                t["tags"] = json.loads(t.get("tags") or "[]")
            except Exception:
                t["tags"] = []
        for s in sims:
            try:
                rj = json.loads(s.get("result_json") or "{}")
                s["summary"] = rj.get("summary", "—")
            except Exception:
                s["summary"] = "—"

        return render_template("network/index.html",
                               topologies=topologies, templates=templates[:6],
                               simulations=sims,
                               stats={"topologies": len(topologies),
                                      "simulations": total_sims,
                                      "templates": len(templates)})

    @bp.route("/canvas/new")
    @nc_login_required
    def nc_canvas_new():
        return render_template("network/canvas.html",
                               topology_id="new", topology_name="Untitled Topology")

    @bp.route("/canvas/<topo_id>")
    @nc_login_required
    def nc_canvas_edit(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not row:
            abort(404)
        topo = _row_to_dict(row)
        return render_template("network/canvas.html",
                               topology_id=topo_id, topology_name=topo["name"])

    @bp.route("/templates")
    @nc_login_required
    def nc_templates():
        conn = get_connection()
        templates = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name, category, description, tags FROM nc_templates ORDER BY category, name"
        ).fetchall()]
        conn.close()
        for t in templates:
            try:
                t["tags"] = json.loads(t.get("tags") or "[]")
            except Exception:
                t["tags"] = []
        categories = {}
        for t in templates:
            categories.setdefault(t.get("category") or "Other", []).append(t)
        return render_template("network/templates_gallery.html",
                               categories=categories, templates=templates)

    @bp.route("/simulation/<sim_id>")
    @nc_login_required
    def nc_simulation_detail(sim_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT sr.*, t.name AS topology_name FROM simulation_results sr "
            "JOIN topologies t ON t.id=sr.topology_id WHERE sr.id=?", (sim_id,)
        ).fetchone()
        conn.close()
        if not row:
            abort(404)
        sim = _row_to_dict(row)
        try:
            sim["result"] = json.loads(sim.get("result_json") or "{}")
        except Exception:
            sim["result"] = {}
        return render_template("network/simulation.html", sim=sim)

    @bp.route("/template/<tpl_id>/edit")
    @nc_login_required
    def nc_template_edit(tpl_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM nc_templates WHERE id=?", (tpl_id,)).fetchone()
        conn.close()
        if not row:
            abort(404)
        tpl = _row_to_dict(row)
        try:
            tpl["tags"] = json.loads(tpl.get("tags") or "[]")
        except Exception:
            tpl["tags"] = []
        try:
            gj = json.loads(tpl["graph_json"]) if isinstance(tpl["graph_json"], str) else tpl["graph_json"]
            tpl["node_count"] = len(gj.get("nodes", []))
            tpl["edge_count"] = len(gj.get("edges", []))
        except Exception:
            tpl["node_count"] = 0
            tpl["edge_count"] = 0
        return render_template("network/template_edit.html", tpl=tpl)

    @bp.route("/versions/<topo_id>")
    @nc_login_required
    def nc_versions_page(topo_id):
        conn = get_connection()
        topo = conn.execute("SELECT id, name FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            abort(404)
        versions = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_versions WHERE topology_id=? ORDER BY version_num", (topo_id,)
        ).fetchall()]
        conn.close()
        return render_template("network/versions.html", topology=_row_to_dict(topo), versions=versions)

    @bp.route("/montecarlo/<topo_id>")
    @nc_login_required
    def nc_montecarlo_page(topo_id):
        conn = get_connection()
        topo = conn.execute("SELECT id, name, graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            abort(404)

        scenarios = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_mc_scenarios WHERE topology_id=? ORDER BY created_at DESC", (topo_id,)
        ).fetchall()]

        _auto_run_scenario = None
        if not scenarios:
            now = _now()
            defaults = [
                ("Random Failure (5% links)", "random", "Random 5% link failure probability per iteration",
                 json.dumps({"iterations": 1000, "node_failure_prob": 0.02, "edge_failure_prob": 0.05})),
                ("Major Outage (20% links)", "random", "Stress test — 20% link failure probability",
                 json.dumps({"iterations": 1000, "node_failure_prob": 0.05, "edge_failure_prob": 0.20})),
                ("Single Node Failure", "random", "What happens when one critical node goes down?",
                 json.dumps({"iterations": 500, "node_failure_prob": 0.08, "edge_failure_prob": 0.0})),
            ]
            for name, stype, desc, cfg in defaults:
                sid = str(_uuid.uuid4())
                conn.execute(
                    "INSERT INTO nc_mc_scenarios (id, topology_id, name, scenario_type, description, config_json, created_at) "
                    "VALUES (?,?,?,?,?,?,?)", (sid, topo_id, name, stype, desc, cfg, now)
                )
            conn.commit()
            scenarios = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_mc_scenarios WHERE topology_id=? ORDER BY created_at DESC", (topo_id,)
            ).fetchall()]
            try:
                graph = json.loads(topo["graph_json"])
                if graph.get("nodes") and graph.get("edges"):
                    _auto_run_scenario = scenarios[-1]["id"]
            except Exception:
                pass

        runs = [_row_to_dict(r) for r in conn.execute(
            "SELECT r.id, r.iterations, r.ran_at, s.name AS scenario_name, s.scenario_type "
            "FROM nc_mc_runs r JOIN nc_mc_scenarios s ON s.id=r.scenario_id "
            "WHERE r.topology_id=? ORDER BY r.ran_at DESC LIMIT 20", (topo_id,)
        ).fetchall()]

        if scenarios and not runs and not _auto_run_scenario:
            _auto_run_scenario = scenarios[-1]["id"]

        conn.close()
        return render_template("network/montecarlo.html", topology=_row_to_dict(topo),
                               scenarios=scenarios, runs=runs,
                               auto_run_scenario=_auto_run_scenario)

    @bp.route("/compliance/<topo_id>")
    @nc_login_required
    def nc_compliance_page(topo_id):
        conn = get_connection()
        topo = conn.execute("SELECT id, name, graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            abort(404)

        profile = conn.execute("SELECT * FROM nc_compliance_profiles WHERE topology_id=?", (topo_id,)).fetchone()
        if not profile:
            pid = str(_uuid.uuid4())
            conn.execute("INSERT INTO nc_compliance_profiles (id, topology_id) VALUES (?,?)", (pid, topo_id))
            conn.commit()
            profile = conn.execute("SELECT * FROM nc_compliance_profiles WHERE id=?", (pid,)).fetchone()
        profile = _row_to_dict(profile)

        audits = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, check_type, passed, failed, ran_at FROM nc_compliance_checks "
            "WHERE topology_id=? ORDER BY ran_at DESC LIMIT 10", (topo_id,)
        ).fetchall()]

        open_findings = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_compliance_findings WHERE topology_id=? AND status='open' ORDER BY severity, rule_id",
            (topo_id,)
        ).fetchall()]

        conn.close()

        try:
            regimes = json.loads(profile.get("regimes", "[]"))
        except Exception:
            regimes = ["fisma_high"]

        return render_template("network/compliance_audit.html",
                               topology=_row_to_dict(topo),
                               profile=profile,
                               regimes=regimes,
                               all_regimes=COMPLIANCE_REGIMES,
                               audits=audits,
                               open_findings=open_findings)

    @bp.route("/circuits")
    @nc_login_required
    def nc_circuits():
        conn = get_connection()
        circuits = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_circuits ORDER BY updated_at DESC"
        ).fetchall()]
        stats = {
            "total": len(circuits),
            "installed": sum(1 for c in circuits if c.get("install_status") == "installed"),
            "planned": sum(1 for c in circuits if c.get("install_status") == "planned"),
            "monthly_cost": sum(c.get("monthly_cost_usd") or 0 for c in circuits),
        }
        conn.close()
        return render_template("network/circuits.html", circuits=circuits, stats=stats)

    @bp.route("/customers")
    @nc_login_required
    def nc_customers():
        conn = get_connection()
        customers = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_customers ORDER BY name").fetchall()]
        sites = [_row_to_dict(r) for r in conn.execute(
            "SELECT s.*, c.name AS customer_name FROM nc_sites s "
            "LEFT JOIN nc_customers c ON c.id=s.customer_id ORDER BY s.name"
        ).fetchall()]
        conn.close()
        return render_template("network/customers.html", customers=customers, sites=sites)

    @bp.route("/ipam")
    @nc_login_required
    def nc_ipam():
        conn = get_connection()
        blocks = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_ipam_blocks ORDER BY network").fetchall()]
        conn.close()
        return render_template("network/ipam.html", blocks=blocks)

    @bp.route("/cables")
    @nc_login_required
    def nc_cables():
        conn = get_connection()
        cables = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_cables ORDER BY cable_id").fetchall()]
        conn.close()
        return render_template("network/cables.html", cables=cables)

    @bp.route("/cross-connects")
    @nc_login_required
    def nc_cross_connects():
        conn = get_connection()
        xconns = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_cross_connects ORDER BY updated_at DESC"
        ).fetchall()]
        stats = {
            "total": len(xconns),
            "active": sum(1 for x in xconns if x.get("status") == "active"),
            "planned": sum(1 for x in xconns if x.get("status") == "planned"),
            "monthly_cost": sum(x.get("monthly_cost_usd") or 0 for x in xconns),
            "facilities": len(set(x.get("facility", "") for x in xconns if x.get("facility"))),
        }
        conn.close()
        return render_template("network/cross_connects.html", cross_connects=xconns, stats=stats)

    @bp.route("/projects")
    @nc_login_required
    def nc_projects():
        conn = get_connection()
        projects = [_row_to_dict(r) for r in conn.execute(
            "SELECT p.*, c.name AS customer_name, "
            "(SELECT COUNT(*) FROM nc_project_topologies pt WHERE pt.project_id=p.id) AS topo_count "
            "FROM nc_projects p LEFT JOIN nc_customers c ON c.id=p.customer_id ORDER BY p.updated_at DESC"
        ).fetchall()]
        customers = [_row_to_dict(r) for r in conn.execute("SELECT id, name FROM nc_customers ORDER BY name").fetchall()]
        conn.close()
        return render_template("network/projects.html", projects=projects, customers=customers)

    @bp.route("/projects/<proj_id>")
    @nc_login_required
    def nc_project_detail(proj_id):
        conn = get_connection()
        proj = conn.execute(
            "SELECT p.*, c.name AS customer_name FROM nc_projects p "
            "LEFT JOIN nc_customers c ON c.id=p.customer_id WHERE p.id=?", (proj_id,)
        ).fetchone()
        if not proj:
            conn.close()
            abort(404)
        proj = _row_to_dict(proj)
        topos = [_row_to_dict(r) for r in conn.execute(
            "SELECT t.id, t.name, t.description, t.classification, t.updated_at, "
            "json_array_length(json_extract(t.graph_json,'$.nodes')) AS node_count, "
            "json_array_length(json_extract(t.graph_json,'$.edges')) AS edge_count "
            "FROM topologies t JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            "WHERE pt.project_id=? ORDER BY t.updated_at DESC", (proj_id,)
        ).fetchall()]
        circuits = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_circuits WHERE topology_id IN "
            "(SELECT topology_id FROM nc_project_topologies WHERE project_id=?) ORDER BY circuit_id", (proj_id,)
        ).fetchall()]
        all_topos = [_row_to_dict(r) for r in conn.execute("SELECT id, name FROM topologies ORDER BY name").fetchall()]
        conn.close()
        return render_template("network/project_detail.html", project=proj,
                               topologies=topos, circuits=circuits, all_topos=all_topos)

    # ══════════════════════════════════════════════════════════════════════
    # API: Health
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/health")
    def nc_api_health():
        try:
            conn = get_connection()
            tc = conn.execute("SELECT COUNT(*) FROM topologies").fetchone()[0]
            tpl = conn.execute("SELECT COUNT(*) FROM nc_templates").fetchone()[0]
            conn.close()
            return jsonify({"status": "ok", "topologies": tc, "templates": tpl, "ts": _now()})
        except Exception as exc:
            return jsonify({"status": "error", "error": str(type(exc).__name__)}), 500

    # ══════════════════════════════════════════════════════════════════════
    # API: Topologies CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies", methods=["GET"])
    @nc_login_required
    def nc_api_list_topologies():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, description, classification, created_at, updated_at "
            "FROM topologies ORDER BY updated_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/topologies", methods=["POST"])
    @nc_login_required
    def nc_api_create_topology():
        data = request.get_json(force=True, silent=True) or {}
        topo_id = str(_uuid.uuid4())
        name = data.get("name", "Untitled Topology")
        graph = json.dumps(data.get("graph_json", {"nodes": [], "edges": []}))
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, template_id, classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (topo_id, name, data.get("description", ""), graph,
             data.get("template_id"), data.get("classification", "public"), now, now),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "topology", topo_id, name)
        return jsonify({"id": topo_id, "name": name, "created_at": now}), 201

    # Clear-all routes MUST be before <topo_id> parameterized routes
    @bp.route("/api/topologies/clear-all", methods=["DELETE"])
    @nc_login_required
    def nc_api_clear_all_topologies():
        conn = get_connection()
        # Delete child tables first (FK constraints require this order)
        child_tables = [
            "simulation_results", "nc_objects", "nc_circuits", "nc_cables",
            "nc_cross_connects", "nc_versions", "nc_compliance_findings",
            "nc_compliance_checks", "nc_compliance_profiles",
            "nc_ipam_blocks", "nc_project_topologies", "nc_groups",
            "nc_mc_runs", "nc_mc_scenarios",
        ]
        for tbl in child_tables:
            conn.execute(f"DELETE FROM {tbl}")
        conn.execute("DELETE FROM topologies")
        conn.commit()
        conn.close()
        _audit("CLEAR_ALL", "topologies", "", "All topologies cleared")
        return jsonify({"cleared": "topologies"})

    @bp.route("/api/simulations/clear-all", methods=["DELETE"])
    @nc_login_required
    def nc_api_clear_all_simulations():
        conn = get_connection()
        conn.execute("DELETE FROM simulation_results")
        conn.execute("DELETE FROM nc_mc_runs")
        conn.execute("DELETE FROM nc_mc_scenarios")
        conn.commit()
        conn.close()
        _audit("CLEAR_ALL", "simulations", "", "All simulations cleared")
        return jsonify({"cleared": "simulations"})

    @bp.route("/api/topologies/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_get_topology(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            topo["graph_json"] = json.loads(topo["graph_json"])
        except Exception:
            topo["graph_json"] = {"nodes": [], "edges": []}
        return jsonify(topo)

    @bp.route("/api/topologies/<topo_id>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_topology(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        fields, values = [], []
        for k in ["name", "description", "graph_json", "classification"]:
            if k in data:
                val = json.dumps(data[k]) if k == "graph_json" and isinstance(data[k], dict) else data[k]
                fields.append(f"{k}=?")
                values.append(val)
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append("updated_at=?")
        values.append(_now())
        values.append(topo_id)
        conn.execute(f"UPDATE topologies SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE", "topology", topo_id)
        return jsonify({"ok": True})

    @bp.route("/api/topologies/<topo_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_topology(topo_id):
        conn = get_connection()
        conn.execute("DELETE FROM topologies WHERE id=?", (topo_id,))
        conn.commit()
        conn.close()
        _audit("DELETE", "topology", topo_id)
        return jsonify({"deleted": topo_id})

    # ══════════════════════════════════════════════════════════════════════
    # API: Simulation
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/simulate", methods=["POST"])
    @nc_login_required
    def nc_api_simulate(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Topology not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        sim_type = data.get("sim_type", "ping")
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        result = _add_narrative(_run_simulation(graph, sim_type, data))
        sim_id = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO simulation_results (id, topology_id, sim_type, input_json, result_json, ran_at) "
            "VALUES (?,?,?,?,?,?)",
            (sim_id, topo_id, sim_type, json.dumps(data), json.dumps(result), now)
        )
        conn.commit()
        conn.close()
        _audit("SIMULATE", "topology", topo_id, sim_type)
        return jsonify({"sim_id": sim_id, "result": result})

    # ══════════════════════════════════════════════════════════════════════
    # API: Templates
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/templates", methods=["GET"])
    @nc_login_required
    def nc_api_list_templates():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, category, description, tags FROM nc_templates ORDER BY category, name"
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/templates/<tpl_id>", methods=["GET"])
    @nc_login_required
    def nc_api_get_template(tpl_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM nc_templates WHERE id=?", (tpl_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        d = _row_to_dict(row)
        try:
            d["graph_json"] = json.loads(d["graph_json"])
        except Exception:
            d["graph_json"] = {"nodes": [], "edges": []}
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        return jsonify(d)

    @bp.route("/api/templates/<tpl_id>/load", methods=["POST"])
    @nc_login_required
    def nc_api_load_template(tpl_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM nc_templates WHERE id=?", (tpl_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        tpl = _row_to_dict(row)
        topo_id = str(_uuid.uuid4())
        now = _now()
        name = f"{tpl['name']} (copy)"
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, template_id, classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (topo_id, name, tpl.get("description", ""), tpl["graph_json"], tpl_id, "public", now, now),
        )
        conn.commit()
        conn.close()
        _audit("LOAD_TEMPLATE", "topology", topo_id, tpl_id)
        return jsonify({"id": topo_id, "name": name, "redirect": f"/network/canvas/{topo_id}"}), 201

    @bp.route("/api/templates/<tpl_id>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_template(tpl_id):
        conn = get_connection()
        row = conn.execute("SELECT id FROM nc_templates WHERE id=?", (tpl_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Template not found"}), 404
        data = request.get_json(force=True)
        fields, values = [], []
        for k in ["name", "description", "category"]:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if "tags" in data:
            fields.append("tags=?")
            values.append(json.dumps(data["tags"]) if isinstance(data["tags"], list) else data["tags"])
        if "graph_json" in data:
            fields.append("graph_json=?")
            values.append(json.dumps(data["graph_json"]) if isinstance(data["graph_json"], dict) else data["graph_json"])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields to update"}), 400
        values.append(tpl_id)
        conn.execute(f"UPDATE nc_templates SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE_TEMPLATE", "template", tpl_id, json.dumps(list(data.keys())))
        return jsonify({"ok": True, "id": tpl_id})

    @bp.route("/api/cloud-objects")
    @nc_login_required
    def nc_api_cloud_objects():
        return jsonify(CLOUD_OBJECTS)

    # ══════════════════════════════════════════════════════════════════════
    # API: Export / Import
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/export/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_export(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        fmt = (request.get_json(force=True, silent=True) or {}).get("format", "drawio")
        if fmt == "drawio":
            xml = to_drawio(graph, topo["name"])
            return jsonify({"format": "drawio", "filename": f"{topo['name']}.drawio", "content": xml})
        if fmt == "svg":
            svg_content = to_svg(graph, topo["name"])
            return jsonify({"format": "svg", "filename": f"{topo['name']}.svg", "content": svg_content})
        return jsonify({"error": f"Unknown format: {fmt}"}), 400

    @bp.route("/api/export/<topo_id>/visio", methods=["POST"])
    @nc_login_required
    def nc_api_export_visio(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        vdx_xml = to_vdx(graph, topo["name"])
        return jsonify({"format": "vdx", "filename": f"{topo['name']}.vdx", "content": vdx_xml})

    @bp.route("/api/import", methods=["POST"])
    @nc_login_required
    def nc_api_import():
        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "drawio")
        content = data.get("content", "")
        name = data.get("name", "Imported Topology")
        if not content:
            return jsonify({"error": "content required"}), 400
        if fmt == "drawio":
            graph = import_drawio(content)
        elif fmt in ("vdx", "visio"):
            graph = import_vdx(content)
        elif fmt == "svg":
            graph = import_svg(content)
        else:
            return jsonify({"error": f"Unsupported format: {fmt}"}), 400
        topo_id = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (topo_id, name, f"Imported from {fmt}", json.dumps(graph), "public", now, now)
        )
        conn.commit()
        conn.close()
        _audit("IMPORT", "topology", topo_id, fmt)
        return jsonify({"id": topo_id, "name": name,
                         "nodes": len(graph.get("nodes", [])),
                         "edges": len(graph.get("edges", []))}), 201

    # ══════════════════════════════════════════════════════════════════════
    # API: Circuits CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/circuits", methods=["GET"])
    @nc_login_required
    def nc_api_list_circuits():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_circuits ORDER BY updated_at DESC").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/circuits", methods=["POST"])
    @nc_login_required
    def nc_api_create_circuit():
        data = request.get_json(force=True, silent=True) or {}
        cid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_circuits (id, topology_id, circuit_id, carrier, circuit_type, bandwidth, "
            "handoff_a, handoff_z, customer, site, monthly_cost_usd, contract_start, contract_end, "
            "sla_uptime_pct, install_status, notes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, data.get("topology_id"), data.get("circuit_id", ""), data.get("carrier", ""),
             data.get("circuit_type", ""), data.get("bandwidth", ""), data.get("handoff_a", ""),
             data.get("handoff_z", ""), data.get("customer", ""), data.get("site", ""),
             data.get("monthly_cost_usd", 0), data.get("contract_start"), data.get("contract_end"),
             data.get("sla_uptime_pct", 99.9), data.get("install_status", "planned"),
             data.get("notes", ""), now, now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "circuit", cid, data.get("circuit_id", ""))
        return jsonify({"id": cid}), 201

    @bp.route("/api/circuits/<cid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_circuit(cid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        row = conn.execute("SELECT id FROM nc_circuits WHERE id=?", (cid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        allowed = ["circuit_id", "carrier", "circuit_type", "bandwidth", "handoff_a", "handoff_z",
                   "customer", "site", "monthly_cost_usd", "contract_start", "contract_end",
                   "sla_uptime_pct", "install_status", "notes", "topology_id"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append("updated_at=?")
        values.append(_now())
        values.append(cid)
        conn.execute(f"UPDATE nc_circuits SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE", "circuit", cid)
        return jsonify({"ok": True})

    @bp.route("/api/circuits/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_circuit(cid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_circuits WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        _audit("DELETE", "circuit", cid)
        return jsonify({"deleted": cid})

    # ══════════════════════════════════════════════════════════════════════
    # API: Customers & Sites CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/customers", methods=["GET"])
    @nc_login_required
    def nc_api_list_customers():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_customers ORDER BY name").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/customers", methods=["POST"])
    @nc_login_required
    def nc_api_create_customer():
        data = request.get_json(force=True, silent=True) or {}
        cid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_customers (id, name, customer_type, contact_name, contact_email, contract_ref, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, data.get("name", ""), data.get("customer_type", "customer"),
             data.get("contact_name", ""), data.get("contact_email", ""),
             data.get("contract_ref", ""), data.get("notes", ""), now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "customer", cid, data.get("name", ""))
        return jsonify({"id": cid}), 201

    @bp.route("/api/customers/<cid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_customer(cid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = ["name", "customer_type", "contact_name", "contact_email", "contract_ref", "notes"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(cid)
        conn.execute(f"UPDATE nc_customers SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE", "customer", cid)
        return jsonify({"ok": True})

    @bp.route("/api/customers/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_customer(cid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_sites WHERE customer_id=?", (cid,))
        conn.execute("DELETE FROM nc_customers WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        _audit("DELETE", "customer", cid)
        return jsonify({"deleted": cid})

    @bp.route("/api/sites", methods=["GET"])
    @nc_login_required
    def nc_api_list_sites():
        conn = get_connection()
        rows = conn.execute(
            "SELECT s.*, c.name AS customer_name FROM nc_sites s "
            "LEFT JOIN nc_customers c ON c.id=s.customer_id ORDER BY s.name"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/sites", methods=["POST"])
    @nc_login_required
    def nc_api_create_site():
        data = request.get_json(force=True, silent=True) or {}
        sid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_sites (id, customer_id, name, address, city, state, country, site_type, classification, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, data.get("customer_id"), data.get("name", ""), data.get("address", ""),
             data.get("city", ""), data.get("state", ""), data.get("country", "US"),
             data.get("site_type", "office"), data.get("classification", "public"), now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "site", sid, data.get("name", ""))
        return jsonify({"id": sid}), 201

    @bp.route("/api/sites/<sid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_site(sid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = ["customer_id", "name", "address", "city", "state", "country", "site_type", "classification"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(sid)
        conn.execute(f"UPDATE nc_sites SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE", "site", sid)
        return jsonify({"ok": True})

    @bp.route("/api/sites/<sid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_site(sid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_sites WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        _audit("DELETE", "site", sid)
        return jsonify({"deleted": sid})

    # ══════════════════════════════════════════════════════════════════════
    # API: IPAM CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/ipam", methods=["GET"])
    @nc_login_required
    def nc_api_list_ipam():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_ipam_blocks ORDER BY network").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/ipam", methods=["POST"])
    @nc_login_required
    def nc_api_create_ipam():
        data = request.get_json(force=True, silent=True) or {}
        bid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_ipam_blocks (id, topology_id, network, vlan_id, vrf, description, site_id, gateway, utilization_pct, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (bid, data.get("topology_id"), data.get("network", ""), data.get("vlan_id"),
             data.get("vrf", "global"), data.get("description", ""), data.get("site_id"),
             data.get("gateway", ""), data.get("utilization_pct", 0), now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "ipam_block", bid, data.get("network", ""))
        return jsonify({"id": bid}), 201

    @bp.route("/api/ipam/<bid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_ipam(bid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = ["network", "vlan_id", "vrf", "description", "site_id", "gateway", "utilization_pct", "topology_id"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(bid)
        conn.execute(f"UPDATE nc_ipam_blocks SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE", "ipam_block", bid)
        return jsonify({"ok": True})

    @bp.route("/api/ipam/<bid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_ipam(bid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_ipam_blocks WHERE id=?", (bid,))
        conn.commit()
        conn.close()
        _audit("DELETE", "ipam_block", bid)
        return jsonify({"deleted": bid})

    # ══════════════════════════════════════════════════════════════════════
    # API: Cables CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/cables", methods=["GET"])
    @nc_login_required
    def nc_api_list_cables():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_cables ORDER BY cable_id").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/cables", methods=["POST"])
    @nc_login_required
    def nc_api_create_cable():
        data = request.get_json(force=True, silent=True) or {}
        cid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_cables (id, topology_id, cable_id, cable_type, src_device, src_port, "
            "dst_device, dst_port, patch_panel, length_m, status, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, data.get("topology_id"), data.get("cable_id", ""), data.get("cable_type", ""),
             data.get("src_device", ""), data.get("src_port", ""), data.get("dst_device", ""),
             data.get("dst_port", ""), data.get("patch_panel", ""), data.get("length_m"),
             data.get("status", "active"), data.get("notes", ""), now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "cable", cid, data.get("cable_id", ""))
        return jsonify({"id": cid}), 201

    @bp.route("/api/cables/<cid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_cable(cid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = ["cable_id", "cable_type", "src_device", "src_port", "dst_device", "dst_port",
                   "patch_panel", "length_m", "status", "notes", "topology_id"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(cid)
        conn.execute(f"UPDATE nc_cables SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE", "cable", cid)
        return jsonify({"ok": True})

    @bp.route("/api/cables/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_cable(cid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_cables WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        _audit("DELETE", "cable", cid)
        return jsonify({"deleted": cid})

    # ══════════════════════════════════════════════════════════════════════
    # API: Cross-Connects CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/cross-connects", methods=["GET"])
    @nc_login_required
    def nc_api_list_cross_connects():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_cross_connects ORDER BY updated_at DESC").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/cross-connects", methods=["POST"])
    @nc_login_required
    def nc_api_create_cross_connect():
        data = request.get_json(force=True, silent=True) or {}
        cid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_cross_connects (id, topology_id, xconn_id, facility, meet_me_room, "
            "src_device, src_port, dst_device, dst_port, media_type, bandwidth, "
            "provider_a, provider_z, loa_status, monthly_cost_usd, install_date, "
            "status, notes, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, data.get("topology_id"), data.get("xconn_id", ""),
             data.get("facility", ""), data.get("meet_me_room", ""),
             data.get("src_device", ""), data.get("src_port", ""),
             data.get("dst_device", ""), data.get("dst_port", ""),
             data.get("media_type", "SMF"), data.get("bandwidth", ""),
             data.get("provider_a", ""), data.get("provider_z", ""),
             data.get("loa_status", "pending"), data.get("monthly_cost_usd", 0),
             data.get("install_date"), data.get("status", "planned"),
             data.get("notes", ""), now, now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "cross_connect", cid, data.get("xconn_id", ""))
        return jsonify({"id": cid}), 201

    @bp.route("/api/cross-connects/<cid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_cross_connect(cid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        row = conn.execute("SELECT id FROM nc_cross_connects WHERE id=?", (cid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        allowed = ["xconn_id", "facility", "meet_me_room", "src_device", "src_port",
                   "dst_device", "dst_port", "media_type", "bandwidth",
                   "provider_a", "provider_z", "loa_status", "monthly_cost_usd",
                   "install_date", "status", "notes", "topology_id"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append("updated_at=?")
        values.append(_now())
        values.append(cid)
        conn.execute(f"UPDATE nc_cross_connects SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE", "cross_connect", cid)
        return jsonify({"ok": True})

    @bp.route("/api/cross-connects/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_cross_connect(cid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_cross_connects WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        _audit("DELETE", "cross_connect", cid)
        return jsonify({"deleted": cid})

    # ══════════════════════════════════════════════════════════════════════
    # API: Versions
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/versions/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_versions(topo_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, topology_id, version_num, label, phase, created_by, notes, created_at "
            "FROM nc_versions WHERE topology_id=? ORDER BY version_num", (topo_id,)
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_create_version(topo_id):
        conn = get_connection()
        topo = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        last = conn.execute("SELECT MAX(version_num) FROM nc_versions WHERE topology_id=?", (topo_id,)).fetchone()[0]
        ver_num = (last or 0) + 1
        vid = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_versions (id, topology_id, version_num, label, phase, graph_json, created_by, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (vid, topo_id, ver_num, data.get("label", f"v{ver_num}"),
             data.get("phase", "as-is"), topo["graph_json"],
             data.get("created_by", ""), data.get("notes", ""), now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "version", vid, f"v{ver_num} ({data.get('phase', 'as-is')})")
        return jsonify({"id": vid, "version_num": ver_num}), 201

    @bp.route("/api/versions/<topo_id>/diff", methods=["POST"])
    @nc_login_required
    def nc_api_diff_versions(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        v1_id = data.get("version_a")
        v2_id = data.get("version_b")
        if not v1_id or not v2_id:
            return jsonify({"error": "version_a and version_b required"}), 400
        conn = get_connection()
        r1 = conn.execute("SELECT graph_json, label, phase FROM nc_versions WHERE id=?", (v1_id,)).fetchone()
        r2 = conn.execute("SELECT graph_json, label, phase FROM nc_versions WHERE id=?", (v2_id,)).fetchone()
        conn.close()
        if not r1 or not r2:
            return jsonify({"error": "Version not found"}), 404
        try:
            g1 = json.loads(r1["graph_json"])
            g2 = json.loads(r2["graph_json"])
        except Exception:
            return jsonify({"error": "Invalid graph JSON"}), 500
        n1_ids = {n["id"] for n in g1.get("nodes", [])}
        n2_ids = {n["id"] for n in g2.get("nodes", [])}
        e1_ids = {e["id"] for e in g1.get("edges", []) if "id" in e}
        e2_ids = {e["id"] for e in g2.get("edges", []) if "id" in e}
        return jsonify({
            "version_a": {"id": v1_id, "label": r1["label"], "phase": r1["phase"]},
            "version_b": {"id": v2_id, "label": r2["label"], "phase": r2["phase"]},
            "nodes_added": len(n2_ids - n1_ids), "nodes_removed": len(n1_ids - n2_ids),
            "nodes_unchanged": len(n1_ids & n2_ids),
            "edges_added": len(e2_ids - e1_ids), "edges_removed": len(e1_ids - e2_ids),
            "edges_unchanged": len(e1_ids & e2_ids),
            "added_node_ids": list(n2_ids - n1_ids), "removed_node_ids": list(n1_ids - n2_ids),
        })

    # ══════════════════════════════════════════════════════════════════════
    # API: Bill of Materials & Capacity Planning
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/bom", methods=["GET"])
    @nc_login_required
    def nc_api_bom(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json, name FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        type_counts = {}
        for n in graph.get("nodes", []):
            t = n.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
        bom_items = []
        total = 0
        for device_type, count in sorted(type_counts.items()):
            unit_cost = BOM_COSTS.get(device_type, 0)
            line_total = unit_cost * count
            total += line_total
            bom_items.append({"device_type": device_type, "quantity": count,
                              "unit_cost_usd": unit_cost, "line_total_usd": line_total})
        return jsonify({"topology": row["name"], "items": bom_items,
                         "total_capex_usd": total, "device_count": sum(type_counts.values()),
                         "unique_types": len(type_counts)})

    @bp.route("/api/topologies/<topo_id>/capacity", methods=["POST"])
    @nc_login_required
    def nc_api_capacity_plan(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            return jsonify({"error": "Bad graph"}), 500
        data = request.get_json(force=True, silent=True) or {}
        growth_pct = data.get("annual_growth_pct", 20)
        additional_users = data.get("additional_users", 0)
        user_bw_mbps = data.get("user_bandwidth_mbps", 2)
        analysis = []
        for e in graph.get("edges", []):
            base_util = 20 + abs(hash(e.get("id", "")) % 60)
            label = (e.get("label") or "").upper()
            link_bw_mbps = 1000
            if "100G" in label:
                link_bw_mbps = 100000
            elif "40G" in label:
                link_bw_mbps = 40000
            elif "25G" in label:
                link_bw_mbps = 25000
            elif "10G" in label:
                link_bw_mbps = 10000
            elif "100M" in label:
                link_bw_mbps = 100
            user_impact = round(additional_users * user_bw_mbps / max(link_bw_mbps, 1) * 100, 1)
            projected = round(base_util + user_impact, 1)
            status = "critical" if projected > 80 else "warning" if projected > 60 else "ok"
            analysis.append({
                "edge_id": e.get("id", ""), "label": e.get("label", ""),
                "source": e.get("source", ""), "target": e.get("target", ""),
                "current_util_pct": base_util,
                "growth_projected_pct": round(base_util * (1 + growth_pct / 100), 1),
                "with_users_pct": projected, "link_bw_mbps": link_bw_mbps,
                "status": status, "upgrade_needed": projected > 75,
            })
        critical = [a for a in analysis if a["status"] == "critical"]
        return jsonify({
            "links": analysis, "total_links": len(analysis),
            "critical_count": len(critical), "growth_pct": growth_pct,
            "additional_users": additional_users,
            "recommendations": [
                f"Upgrade {a['label'] or a['edge_id']}: {a['current_util_pct']}% -> {a['with_users_pct']}%"
                for a in critical
            ],
        })

    # ══════════════════════════════════════════════════════════════════════
    # API: Compliance (quick check)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/compliance", methods=["POST"])
    @nc_login_required
    def nc_api_compliance_check(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph"}), 500
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        node_types = [n.get("type", "") for n in nodes]
        findings = []
        if not any(t == "firewall" for t in node_types):
            findings.append({"check": "STIG-NET-001", "severity": "CAT1", "type": "stig",
                             "finding": "No firewall in topology"})
        encrypted = sum(1 for e in edges if "ipsec" in (e.get("protocol") or "").lower() or "tls" in (e.get("protocol") or "").lower())
        if encrypted == 0 and edges:
            findings.append({"check": "FIPS-001", "severity": "HIGH", "type": "fips",
                             "finding": "No encrypted links detected"})
        if not any(t in ("firewall", "aws-nfw", "az-fw", "gcp-armor") for t in node_types):
            findings.append({"check": "ZTA-001", "severity": "HIGH", "type": "zta",
                             "finding": "No network segmentation"})
        from collections import Counter
        degree = Counter()
        for e in edges:
            degree[e.get("source")] += 1
            degree[e.get("target")] += 1
        spof = [n["id"] for n in nodes if degree.get(n["id"], 0) <= 1 and n.get("type") in ("router", "switch-l3", "firewall")]
        if spof:
            findings.append({"check": "BP-REDUNDANCY", "severity": "MEDIUM", "type": "best_practice",
                             "finding": f"{len(spof)} critical device(s) with single connection"})
        passed = max(0, 10 - len(findings))
        failed = len(findings)
        check_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_compliance_checks (id, topology_id, check_type, passed, failed, findings_json, ran_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (check_id, topo_id, "full", passed, failed, json.dumps(findings), now)
        )
        conn.commit()
        conn.close()
        return jsonify({"check_id": check_id, "passed": passed, "failed": failed,
                         "score_pct": round(passed / max(passed + failed, 1) * 100, 1),
                         "findings": findings})

    # ══════════════════════════════════════════════════════════════════════
    # API: Full Compliance Audit Engine
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/compliance/<topo_id>/profile", methods=["PUT"])
    @nc_login_required
    def nc_api_update_compliance_profile(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        profile = conn.execute("SELECT id FROM nc_compliance_profiles WHERE topology_id=?", (topo_id,)).fetchone()
        if not profile:
            pid = str(_uuid.uuid4())
            conn.execute("INSERT INTO nc_compliance_profiles (id, topology_id) VALUES (?,?)", (pid, topo_id))
            conn.commit()
            profile = conn.execute("SELECT id FROM nc_compliance_profiles WHERE id=?", (pid,)).fetchone()
        fields, values = [], []
        for k in ["regimes", "classification", "environment", "auto_audit"]:
            if k in data:
                val = json.dumps(data[k]) if k == "regimes" and isinstance(data[k], list) else data[k]
                fields.append(f"{k}=?")
                values.append(val)
        if fields:
            fields.append("updated_at=?")
            values.append(_now())
            values.append(profile["id"])
            conn.execute(f"UPDATE nc_compliance_profiles SET {', '.join(fields)} WHERE id=?", values)
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/compliance/<topo_id>/audit", methods=["POST"])
    @nc_login_required
    def nc_api_run_compliance_audit(topo_id):
        conn = get_connection()
        topo = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        profile = conn.execute("SELECT * FROM nc_compliance_profiles WHERE topology_id=?", (topo_id,)).fetchone()
        if profile:
            try:
                regimes = json.loads(profile["regimes"] or "[]")
            except Exception:
                regimes = ["fisma_high"]
            classification = profile["classification"] or "CUI"
        else:
            data = request.get_json(force=True, silent=True) or {}
            regimes = data.get("regimes", ["fisma_high"])
            classification = data.get("classification", "CUI")
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph"}), 500

        result = run_compliance_audit(topo_id, graph, regimes, classification)

        audit_id = str(_uuid.uuid4())
        now = _now()
        total_passed = sum(s["passed"] for s in result["scores"].values())
        total_failed = sum(s["failed"] for s in result["scores"].values())
        conn.execute(
            "INSERT INTO nc_compliance_checks (id, topology_id, check_type, passed, failed, findings_json, ran_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (audit_id, topo_id, ",".join(regimes), total_passed, total_failed,
             json.dumps(result["findings"]), now)
        )
        existing_rule_ids = set()
        for r in conn.execute(
            "SELECT rule_id FROM nc_compliance_findings WHERE topology_id=? AND status='open'", (topo_id,)
        ).fetchall():
            existing_rule_ids.add(r["rule_id"])
        new_rule_ids = set()
        for f in result["findings"]:
            new_rule_ids.add(f["rule_id"])
            exists = conn.execute(
                "SELECT id FROM nc_compliance_findings WHERE topology_id=? AND rule_id=? AND status='open'",
                (topo_id, f["rule_id"])
            ).fetchone()
            if not exists:
                fid = str(_uuid.uuid4())
                conn.execute(
                    "INSERT INTO nc_compliance_findings (id, topology_id, audit_id, rule_id, regime, severity, "
                    "title, description, affected_entity, affected_type, fix_action, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (fid, topo_id, audit_id, f["rule_id"], ",".join(f["regimes"]), f["severity"],
                     f["title"], f["description"], f.get("affected_entity", ""),
                     f.get("affected_type", "topology"),
                     json.dumps(f.get("fix_action")) if f.get("fix_action") else None, now)
                )
        remediated_rules = existing_rule_ids - new_rule_ids
        for rid in remediated_rules:
            conn.execute(
                "UPDATE nc_compliance_findings SET status='remediated', remediated_at=? "
                "WHERE topology_id=? AND rule_id=? AND status='open'", (now, topo_id, rid)
            )
        conn.commit()
        conn.close()
        _audit("COMPLIANCE_AUDIT", "topology", topo_id, f"{len(result['findings'])} findings")
        result["audit_id"] = audit_id
        result["remediated_count"] = len(remediated_rules)
        return jsonify(result)

    @bp.route("/api/compliance/<topo_id>/fix", methods=["POST"])
    @nc_login_required
    def nc_api_compliance_fix(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        finding_id = data.get("finding_id")
        if not finding_id:
            return jsonify({"error": "finding_id required"}), 400
        conn = get_connection()
        finding = conn.execute("SELECT * FROM nc_compliance_findings WHERE id=?", (finding_id,)).fetchone()
        if not finding:
            conn.close()
            return jsonify({"error": "Finding not found"}), 404
        fix_action = None
        try:
            fix_action = json.loads(finding["fix_action"]) if finding["fix_action"] else None
        except Exception:
            pass
        if not fix_action:
            conn.close()
            return jsonify({"error": "No automated fix available"}), 400
        topo = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph"}), 500

        applied, detail = apply_compliance_fix(graph, fix_action)
        action = fix_action.get("action", "")
        if not applied and action == "create_version":
            last = conn.execute("SELECT MAX(version_num) FROM nc_versions WHERE topology_id=?", (topo_id,)).fetchone()[0]
            ver_num = (last or 0) + 1
            vid = str(_uuid.uuid4())
            conn.execute(
                "INSERT INTO nc_versions (id, topology_id, version_num, label, phase, graph_json, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (vid, topo_id, ver_num, fix_action.get("label", "As-Built"),
                 fix_action.get("phase", "as-is"), topo["graph_json"], _now())
            )
            applied = True
            detail = f"Created version v{ver_num}"
        if applied:
            if action != "create_version":
                conn.execute("UPDATE topologies SET graph_json=?, updated_at=? WHERE id=?",
                             (json.dumps(graph), _now(), topo_id))
            conn.execute("UPDATE nc_compliance_findings SET status='remediated', remediated_at=? WHERE id=?",
                         (_now(), finding_id))
            conn.commit()
            conn.close()
            _audit("COMPLIANCE_FIX", "topology", topo_id, detail)
            return jsonify({"applied": True, "detail": detail})
        conn.close()
        return jsonify({"applied": False, "detail": "Fix action not applicable"})

    @bp.route("/api/compliance/<topo_id>/export", methods=["POST"])
    @nc_login_required
    def nc_api_compliance_export(topo_id):
        conn = get_connection()
        topo = conn.execute("SELECT name FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        profile = conn.execute("SELECT * FROM nc_compliance_profiles WHERE topology_id=?", (topo_id,)).fetchone()
        findings = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_compliance_findings WHERE topology_id=? ORDER BY severity, rule_id", (topo_id,)
        ).fetchall()]
        conn.close()
        profile = _row_to_dict(profile) if profile else {}
        xml = generate_xacta_export(
            topo["name"], profile.get("classification", "CUI"),
            profile.get("environment", "IL4"), profile.get("regimes", "[]"), findings,
        )
        return jsonify({"format": "xacta_xml",
                         "filename": f"{topo['name']}_compliance_report.xml",
                         "content": xml, "findings_count": len(findings)})

    # ══════════════════════════════════════════════════════════════════════
    # API: FIPS 140 Encryption Coverage Report Export
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/compliance/<topo_id>/fips-report", methods=["POST"])
    @nc_login_required
    def nc_api_fips_report(topo_id):
        """Generate a FIPS 140 Encryption Coverage Report for ISSM review.

        Returns JSON report data or rendered HTML depending on format param.

        Body JSON (optional):
          {"format": "json" | "html"}   // default "json"
        """
        conn = get_connection()
        topo = conn.execute(
            "SELECT name, graph_json FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph data"}), 500

        profile = conn.execute(
            "SELECT * FROM nc_compliance_profiles WHERE topology_id=?",
            (topo_id,),
        ).fetchone()
        conn.close()
        profile = _row_to_dict(profile) if profile else {}

        now = _now()
        report = generate_fips_coverage_report(
            system_name=topo["name"],
            classification=profile.get("classification", "CUI"),
            environment=profile.get("environment", "IL4"),
            graph=graph,
            now_str=now,
        )

        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "json")

        _audit("FIPS_REPORT", "topology", topo_id,
               f"coverage={report['summary']['coverage_pct']}% "
               f"risk={report['summary']['risk_level']} format={fmt}")

        if fmt == "html":
            html = export_fips_report_html(report)
            from flask import Response
            return Response(
                html,
                mimetype="text/html",
                headers={
                    "Content-Disposition":
                        f'attachment; filename="{topo["name"]}_fips_report.html"',
                },
            )

        return jsonify(report)

    # ══════════════════════════════════════════════════════════════════════
    # API: STIG XCCDF/CKL Import
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/compliance/<topo_id>/stig-import", methods=["POST"])
    @nc_login_required
    def nc_api_stig_import(topo_id):
        """Import a STIG .ckl or XCCDF results file.

        Match hostnames to canvas devices and return per-device compliance
        color (green/yellow/red).
        """
        conn = get_connection()
        topo = conn.execute(
            "SELECT graph_json, name FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph data"}), 500

        # Accept file upload or raw XML body
        content = None
        filename = "upload.xml"
        if request.files and "file" in request.files:
            f = request.files["file"]
            filename = f.filename or filename
            content = f.read().decode("utf-8", errors="replace")
        elif request.data:
            content = request.data.decode("utf-8", errors="replace")
            data = request.form or {}
            filename = data.get("filename", filename)

        if not content or not content.strip():
            conn.close()
            return jsonify({"error": "No file content provided"}), 400

        result = import_stig_file(content, graph)

        if "error" in result:
            conn.close()
            return jsonify(result), 400

        # Persist import record
        import_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_stig_imports "
            "(id, topology_id, filename, format, stig_name, stig_version, "
            "total_hosts, matched_hosts, result_json, imported_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (import_id, topo_id, filename, result.get("format", ""),
             result.get("stig_name", ""), result.get("stig_version", ""),
             result.get("total_hosts", 0), result.get("total_matched", 0),
             json.dumps(result), now),
        )

        # Also create compliance findings for failed STIG checks
        audit_id = str(_uuid.uuid4())
        total_pass = result.get("total_pass", 0)
        total_fail = result.get("total_fail", 0)
        conn.execute(
            "INSERT INTO nc_compliance_checks "
            "(id, topology_id, check_type, passed, failed, findings_json, ran_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (audit_id, topo_id, f"stig_import:{filename}",
             total_pass, total_fail, json.dumps(result.get("matched", [])), now),
        )

        # Insert individual findings for each failed check on matched devices
        for device in result.get("matched", []):
            for f in device.get("findings", []):
                fid = str(_uuid.uuid4())
                rule_id = f.get("rule_id") or f.get("vuln_id", "UNKNOWN")
                exists = conn.execute(
                    "SELECT id FROM nc_compliance_findings "
                    "WHERE topology_id=? AND rule_id=? AND affected_entity=? AND status='open'",
                    (topo_id, rule_id, device["label"]),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO nc_compliance_findings "
                        "(id, topology_id, audit_id, rule_id, regime, severity, "
                        "title, description, affected_entity, affected_type, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, topo_id, audit_id, rule_id, "stig",
                         f.get("severity", "CAT2"), f.get("title", rule_id),
                         f.get("finding_details", ""),
                         device["label"], "node", now),
                    )

        conn.commit()
        conn.close()
        _audit("STIG_IMPORT", "topology", topo_id,
               f"{filename}: {result.get('total_matched', 0)}/{result.get('total_hosts', 0)} hosts matched")

        result["import_id"] = import_id
        result["audit_id"] = audit_id
        return jsonify(result)

    @bp.route("/api/compliance/<topo_id>/stig-imports", methods=["GET"])
    @nc_login_required
    def nc_api_stig_import_history(topo_id):
        """List previous STIG import records for a topology."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, filename, format, stig_name, stig_version, "
            "total_hosts, matched_hosts, imported_at "
            "FROM nc_stig_imports WHERE topology_id=? ORDER BY imported_at DESC LIMIT 20",
            (topo_id,),
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    # ══════════════════════════════════════════════════════════════════════
    # API: Projects CRUD
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/projects", methods=["GET"])
    @nc_login_required
    def nc_api_list_projects():
        conn = get_connection()
        rows = conn.execute(
            "SELECT p.*, c.name AS customer_name FROM nc_projects p "
            "LEFT JOIN nc_customers c ON c.id=p.customer_id ORDER BY p.updated_at DESC"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/projects", methods=["POST"])
    @nc_login_required
    def nc_api_create_project():
        data = request.get_json(force=True, silent=True) or {}
        pid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_projects (id, name, customer_id, description, status, owner, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (pid, data.get("name", "Untitled Project"), data.get("customer_id"),
             data.get("description", ""), data.get("status", "draft"),
             data.get("owner", ""), now, now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "project", pid, data.get("name", ""))
        return jsonify({"id": pid}), 201

    @bp.route("/api/projects/<pid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_project(pid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = ["name", "customer_id", "description", "status", "owner"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append("updated_at=?")
        values.append(_now())
        values.append(pid)
        conn.execute(f"UPDATE nc_projects SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        _audit("UPDATE", "project", pid)
        return jsonify({"ok": True})

    @bp.route("/api/projects/<pid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_project(pid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_project_topologies WHERE project_id=?", (pid,))
        conn.execute("DELETE FROM nc_projects WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        _audit("DELETE", "project", pid)
        return jsonify({"deleted": pid})

    @bp.route("/api/projects/<pid>/topologies", methods=["POST"])
    @nc_login_required
    def nc_api_link_topology(pid):
        data = request.get_json(force=True, silent=True) or {}
        topo_id = data.get("topology_id")
        if not topo_id:
            return jsonify({"error": "topology_id required"}), 400
        conn = get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO nc_project_topologies (project_id, topology_id) VALUES (?,?)", (pid, topo_id))
            conn.commit()
        except Exception:
            pass
        conn.close()
        _audit("LINK", "project_topology", pid, topo_id)
        return jsonify({"ok": True})

    @bp.route("/api/projects/<pid>/topologies/<topo_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_unlink_topology(pid, topo_id):
        conn = get_connection()
        conn.execute("DELETE FROM nc_project_topologies WHERE project_id=? AND topology_id=?", (pid, topo_id))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ══════════════════════════════════════════════════════════════════════
    # API: CSP Groups
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/groups/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_groups(topo_id):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_groups WHERE topology_id=? ORDER BY created_at", (topo_id,)).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/groups/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_create_group(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        gid = str(_uuid.uuid4())
        csp = data.get("csp", "aws")
        group_type = data.get("group_type", "full")
        pos_x = data.get("pos_x", 100)
        pos_y = data.get("pos_y", 100)
        now = _now()
        auto_nodes = []
        if group_type == "full" and csp in CSP_GROUP_DEFAULTS:
            conn = get_connection()
            topo = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
            if topo:
                try:
                    graph = json.loads(topo["graph_json"])
                except Exception:
                    graph = {"nodes": [], "edges": []}
                for comp in CSP_GROUP_DEFAULTS[csp]:
                    nid = f"{csp}-{str(_uuid.uuid4())[:8]}"
                    graph["nodes"].append({
                        "id": nid, "label": comp["label"], "type": comp["type"],
                        "x": pos_x + comp["dx"], "y": pos_y + comp["dy"], "group_id": gid,
                    })
                    auto_nodes.append(nid)
                conn.execute("UPDATE topologies SET graph_json=?, updated_at=? WHERE id=?",
                             (json.dumps(graph), now, topo_id))
                conn.commit()
            conn.close()
        csp_labels = {"aws": "AWS", "azure": "Azure", "gcp": "GCP", "oci": "OCI", "ibm": "IBM Cloud"}
        label = data.get("label", csp_labels.get(csp, csp.upper()))
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_groups (id, topology_id, parent_id, csp, group_type, label, description, "
            "auto_nodes_json, pos_x, pos_y, width, height, color, collapsed, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (gid, topo_id, data.get("parent_id"), csp, group_type, label,
             data.get("description", ""), json.dumps(auto_nodes),
             pos_x, pos_y, data.get("width", 400), data.get("height", 300),
             data.get("color"), 0, now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "group", gid, f"{csp} {group_type}")
        return jsonify({"id": gid, "auto_nodes": auto_nodes}), 201

    @bp.route("/api/groups/<topo_id>/<gid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_group(topo_id, gid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = ["parent_id", "label", "description", "pos_x", "pos_y", "width", "height", "color", "collapsed", "group_type"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(gid)
        conn.execute(f"UPDATE nc_groups SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/groups/<topo_id>/<gid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_group(topo_id, gid):
        conn = get_connection()
        group = conn.execute("SELECT auto_nodes_json FROM nc_groups WHERE id=?", (gid,)).fetchone()
        if group:
            try:
                auto_ids = set(json.loads(group["auto_nodes_json"] or "[]"))
                if auto_ids:
                    topo = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
                    if topo:
                        graph = json.loads(topo["graph_json"])
                        graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in auto_ids]
                        graph["edges"] = [e for e in graph["edges"] if e["source"] not in auto_ids and e["target"] not in auto_ids]
                        conn.execute("UPDATE topologies SET graph_json=?, updated_at=? WHERE id=?",
                                     (json.dumps(graph), _now(), topo_id))
            except Exception:
                pass
        conn.execute("DELETE FROM nc_groups WHERE parent_id=?", (gid,))
        conn.execute("DELETE FROM nc_groups WHERE id=?", (gid,))
        conn.commit()
        conn.close()
        _audit("DELETE", "group", gid)
        return jsonify({"deleted": gid})

    # ══════════════════════════════════════════════════════════════════════
    # API: Security Boundary Auto-Fencing
    # ══════════════════════════════════════════════════════════════════════

    def _auto_stig_tags(node_types: list[str]) -> list[str]:
        """Derive STIG boundary tags from the device types inside a fence."""
        tags = set()
        type_set = set(node_types)
        # Firewall present → perimeter boundary
        fw_types = {"firewall", "aws-nfw", "az-fw", "gcp-armor", "oci-waf", "aws-waf"}
        if type_set & fw_types:
            tags.add("NET-BND-001: Perimeter Firewall Boundary")
        # Encryption devices → FIPS boundary
        enc_types = {
            "fips-140-l1", "fips-140-l2", "fips-140-l3", "fips-140-l4",
            "hsm", "type1-encryptor", "kg-175d", "kg-175g", "kg-250",
            "kg-340", "kg-245x", "kg-255", "macsec",
        }
        if type_set & enc_types:
            tags.add("NET-ENC-BND: FIPS 140 Encryption Boundary")
        # Servers → server enclave STIG
        if "server" in type_set:
            tags.add("NET-SRV-BND: Server Enclave Boundary")
        # User endpoints → user enclave
        user_types = {"endpoint-pc", "endpoint-phone"}
        if type_set & user_types:
            tags.add("NET-USR-BND: User Enclave Boundary")
        # IoT → IoT segment
        iot_types = {"endpoint-iot", "endpoint-camera"}
        if type_set & iot_types:
            tags.add("NET-IOT-BND: IoT Segment Boundary")
        # Management → NOC/management boundary
        mgmt_types = {"siem", "network-tap", "wlc"}
        if type_set & mgmt_types:
            tags.add("NET-MGT-BND: Management / NOC Boundary")
        # Wireless → wireless boundary
        if "wap" in type_set:
            tags.add("NET-WLS-BND: Wireless Access Boundary")
        # Cloud resources → cloud enclave
        cloud_prefixes = ("aws-", "az-", "gcp-", "oci-", "ibm-")
        if any(t.startswith(cloud_prefixes) for t in type_set):
            tags.add("NET-CLD-BND: Cloud Enclave Boundary")
        # Cross-zone (mixed) → micro-segmentation required
        if len(tags) >= 2:
            tags.add("NET-BND-002: Micro-segmentation Required")
        return sorted(tags)

    @bp.route("/api/boundaries/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_boundaries(topo_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM nc_boundaries WHERE topology_id=? ORDER BY created_at",
            (topo_id,),
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            for key in ("node_ids", "stig_tags"):
                try:
                    d[key] = json.loads(d.get(key) or "[]")
                except Exception:
                    d[key] = []
            result.append(d)
        return jsonify(result)

    @bp.route("/api/boundaries/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_create_boundary(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        bid = str(_uuid.uuid4())
        node_ids = data.get("node_ids", [])
        classification = data.get("classification", "CUI")
        snap = data.get("snap_grid", 10)

        # Snap position/size to grid
        pos_x = round(data.get("pos_x", 0) / snap) * snap
        pos_y = round(data.get("pos_y", 0) / snap) * snap
        width = max(snap, round(data.get("width", 400) / snap) * snap)
        height = max(snap, round(data.get("height", 300) / snap) * snap)

        # Auto-derive STIG tags from contained node types
        node_types = []
        if node_ids:
            conn = get_connection()
            topo = conn.execute(
                "SELECT graph_json FROM topologies WHERE id=?", (topo_id,)
            ).fetchone()
            conn.close()
            if topo:
                try:
                    graph = json.loads(topo["graph_json"])
                except Exception:
                    graph = {"nodes": []}
                nid_set = set(node_ids)
                node_types = [
                    n["type"] for n in graph.get("nodes", []) if n["id"] in nid_set
                ]

        stig_tags = data.get("stig_tags") or _auto_stig_tags(node_types)

        # Classification label mapping
        _CLS_LABELS = {
            "CUI": "CUI Enclave",
            "SECRET": "SECRET VLAN",
            "TOP SECRET": "TOP SECRET Enclave",
            "PUBLIC": "Public Zone",
        }
        label = data.get("label") or _CLS_LABELS.get(classification, f"{classification} Enclave")

        # Classification → color mapping
        _CLS_COLORS = {
            "CUI": "#f39c12",
            "SECRET": "#e94560",
            "TOP SECRET": "#9b59b6",
            "PUBLIC": "#27ae60",
        }
        color = data.get("color") or _CLS_COLORS.get(classification, "#4a9eff")

        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_boundaries "
            "(id, topology_id, label, classification, color, fill_opacity, "
            "node_ids, stig_tags, pos_x, pos_y, width, height, snap_grid, notes, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, topo_id, label, classification, color,
             data.get("fill_opacity", 0.08),
             json.dumps(node_ids), json.dumps(stig_tags),
             pos_x, pos_y, width, height, snap,
             data.get("notes", ""), now, now),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "boundary", bid, f"{classification} — {label}")
        return jsonify({
            "id": bid, "label": label, "classification": classification,
            "color": color, "stig_tags": stig_tags,
            "pos_x": pos_x, "pos_y": pos_y, "width": width, "height": height,
            "node_ids": node_ids,
        }), 201

    @bp.route("/api/boundaries/<topo_id>/<bid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_boundary(topo_id, bid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = [
            "label", "classification", "color", "fill_opacity",
            "node_ids", "stig_tags", "pos_x", "pos_y", "width", "height",
            "snap_grid", "notes",
        ]
        fields, values = [], []
        for k in allowed:
            if k in data:
                v = data[k]
                if k in ("node_ids", "stig_tags") and isinstance(v, list):
                    v = json.dumps(v)
                fields.append(f"{k}=?")
                values.append(v)
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append("updated_at=?")
        values.append(_now())
        values.append(bid)
        conn.execute(f"UPDATE nc_boundaries SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/boundaries/<topo_id>/<bid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_boundary(topo_id, bid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_boundaries WHERE id=? AND topology_id=?", (bid, topo_id))
        conn.commit()
        conn.close()
        _audit("DELETE", "boundary", bid)
        return jsonify({"deleted": bid})

    @bp.route("/api/boundaries/<topo_id>/auto-fence", methods=["POST"])
    @nc_login_required
    def nc_api_auto_fence(topo_id):
        """Auto-generate a boundary from a list of selected node IDs.

        Calculates bounding box from node positions, snaps to grid, derives
        classification label and STIG boundary tags automatically.
        """
        data = request.get_json(force=True, silent=True) or {}
        node_ids = data.get("node_ids", [])
        if not node_ids:
            return jsonify({"error": "node_ids required"}), 400

        classification = data.get("classification", "CUI")
        snap = data.get("snap_grid", 10)
        padding = data.get("padding", 40)

        conn = get_connection()
        topo = conn.execute(
            "SELECT graph_json FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        conn.close()
        if not topo:
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            return jsonify({"error": "Invalid graph JSON"}), 400

        nid_set = set(node_ids)
        matched = [n for n in graph.get("nodes", []) if n["id"] in nid_set]
        if not matched:
            return jsonify({"error": "No matching nodes found"}), 404

        # Bounding box from node positions (assume 110x60 default node size)
        xs = [n.get("x", 0) for n in matched]
        ys = [n.get("y", 0) for n in matched]
        node_w = 110
        node_h = 60
        min_x = min(xs) - padding
        min_y = min(ys) - padding
        max_x = max(xs) + node_w + padding
        max_y = max(ys) + node_h + padding

        # Snap to grid
        pos_x = (min_x // snap) * snap
        pos_y = (min_y // snap) * snap
        width = max(snap, ((max_x - pos_x + snap - 1) // snap) * snap)
        height = max(snap, ((max_y - pos_y + snap - 1) // snap) * snap)

        node_types = [n.get("type", "") for n in matched]
        stig_tags = _auto_stig_tags(node_types)

        _CLS_LABELS = {
            "CUI": "CUI Enclave",
            "SECRET": "SECRET VLAN",
            "TOP SECRET": "TOP SECRET Enclave",
            "PUBLIC": "Public Zone",
        }
        _CLS_COLORS = {
            "CUI": "#f39c12",
            "SECRET": "#e94560",
            "TOP SECRET": "#9b59b6",
            "PUBLIC": "#27ae60",
        }
        label = data.get("label") or _CLS_LABELS.get(classification, f"{classification} Enclave")
        color = data.get("color") or _CLS_COLORS.get(classification, "#4a9eff")

        bid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_boundaries "
            "(id, topology_id, label, classification, color, fill_opacity, "
            "node_ids, stig_tags, pos_x, pos_y, width, height, snap_grid, notes, "
            "created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (bid, topo_id, label, classification, color, 0.08,
             json.dumps(node_ids), json.dumps(stig_tags),
             pos_x, pos_y, width, height, snap,
             data.get("notes", ""), now, now),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "boundary", bid, f"auto-fence {classification} — {len(matched)} nodes")
        return jsonify({
            "id": bid, "label": label, "classification": classification,
            "color": color, "fill_opacity": 0.08, "stig_tags": stig_tags,
            "pos_x": pos_x, "pos_y": pos_y, "width": width, "height": height,
            "node_ids": node_ids, "node_count": len(matched),
        }), 201

    # ══════════════════════════════════════════════════════════════════════
    # API: Monte Carlo Simulation
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/mc/scenarios/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_mc_scenarios(topo_id):
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_mc_scenarios WHERE topology_id=? ORDER BY created_at DESC", (topo_id,)).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/mc/scenarios/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_create_mc_scenario(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        sid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_mc_scenarios (id, topology_id, name, scenario_type, description, config_json, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (sid, topo_id, data.get("name", "Untitled Scenario"),
             data.get("scenario_type", "random"), data.get("description", ""),
             json.dumps(data.get("config", {})), now)
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "mc_scenario", sid, data.get("name", ""))
        return jsonify({"id": sid}), 201

    @bp.route("/api/mc/run/<scenario_id>", methods=["POST"])
    @nc_login_required
    def nc_api_run_mc(scenario_id):
        conn = get_connection()
        scenario = conn.execute("SELECT * FROM nc_mc_scenarios WHERE id=?", (scenario_id,)).fetchone()
        if not scenario:
            conn.close()
            return jsonify({"error": "Scenario not found"}), 404
        scenario = _row_to_dict(scenario)
        topo_id = scenario["topology_id"]
        topo = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph"}), 500
        try:
            config = json.loads(scenario["config_json"] or "{}")
        except Exception:
            config = {}
        data = request.get_json(force=True, silent=True) or {}
        iterations = data.get("iterations", config.get("iterations", 1000))
        result = run_monte_carlo(graph=graph, scenario_name=scenario["name"],
                                  scenario_type=scenario["scenario_type"],
                                  config=config, iterations=iterations)
        run_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_mc_runs (id, scenario_id, topology_id, iterations, result_json, ai_recommendations, ran_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, scenario_id, topo_id, iterations, json.dumps(result),
             "\n".join(result.get("recommendations", [])), now)
        )
        conn.commit()
        conn.close()
        _audit("MC_RUN", "mc_scenario", scenario_id, f"{iterations} iters")
        return jsonify({"run_id": run_id, **result})

    @bp.route("/api/mc/runs/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_mc_runs(topo_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT r.id, r.scenario_id, r.iterations, r.ran_at, r.ai_recommendations, "
            "s.name AS scenario_name, s.scenario_type "
            "FROM nc_mc_runs r JOIN nc_mc_scenarios s ON s.id=r.scenario_id "
            "WHERE r.topology_id=? ORDER BY r.ran_at DESC", (topo_id,)
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/mc/runs/<topo_id>/<run_id>", methods=["GET"])
    @nc_login_required
    def nc_api_get_mc_run(topo_id, run_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM nc_mc_runs WHERE id=? AND topology_id=?", (run_id, topo_id)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        r = _row_to_dict(row)
        try:
            r["result"] = json.loads(r.get("result_json") or "{}")
        except Exception:
            r["result"] = {}
        return jsonify(r)

    # ══════════════════════════════════════════════════════════════════════
    # API: Backups
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/backups", methods=["GET"])
    @nc_login_required
    def nc_api_list_backups():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_backups ORDER BY created_at DESC").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/backups", methods=["POST"])
    @nc_login_required
    def nc_api_create_backup():
        data = request.get_json(force=True, silent=True) or {}
        backup_id = str(_uuid.uuid4())
        now = _now()
        ts = now.replace(":", "-").replace("T", "_")[:19]
        backup_dir = _ICDEV_ROOT / "backups" / "network"
        backup_dir.mkdir(parents=True, exist_ok=True)
        zip_name = f"nc-backup-{ts}.zip"
        zip_path = backup_dir / zip_name
        includes = []
        try:
            with zipfile.ZipFile(str(zip_path), "w", zipfile.ZIP_DEFLATED) as zf:
                nc_db = _ICDEV_ROOT / "data" / "network_canvas.db"
                if nc_db.exists():
                    zf.write(str(nc_db), "network_canvas.db")
                    includes.append("network_canvas.db")
                args_dir = _ICDEV_ROOT / "args"
                if args_dir.exists():
                    for f in args_dir.glob("network_canvas_*"):
                        if f.is_file():
                            zf.write(str(f), f"args/{f.name}")
                            includes.append(f"args/{f.name}")
            file_size = zip_path.stat().st_size
            conn = get_connection()
            conn.execute(
                "INSERT INTO nc_backups (id, backup_type, file_path, file_size_bytes, includes_json, notes, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (backup_id, data.get("backup_type", "manual"), str(zip_path), file_size,
                 json.dumps(includes), data.get("notes", ""), now)
            )
            conn.commit()
            conn.close()
            _audit("BACKUP", "system", backup_id, zip_name)
            return jsonify({"id": backup_id, "file": zip_name, "size_bytes": file_size, "includes": includes}), 201
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/backups/<backup_id>/restore", methods=["POST"])
    @nc_login_required
    def nc_api_restore_backup(backup_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM nc_backups WHERE id=?", (backup_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Backup not found"}), 404
        backup = _row_to_dict(row)
        zip_path = Path(backup["file_path"])
        if not zip_path.exists():
            return jsonify({"error": f"Backup file not found: {zip_path}"}), 404
        restored = []
        try:
            with zipfile.ZipFile(str(zip_path), "r") as zf:
                for name in zf.namelist():
                    if name == "network_canvas.db":
                        target = _ICDEV_ROOT / "data" / "network_canvas.db"
                        if target.exists():
                            shutil.copy2(str(target), str(target) + ".pre-restore")
                        zf.extract(name, str(target.parent))
                        restored.append(name)
                    elif name.startswith("args/"):
                        zf.extract(name, str(_ICDEV_ROOT))
                        restored.append(name)
            _audit("RESTORE", "system", backup_id, f"Restored {len(restored)} files")
            return jsonify({"restored": restored, "backup_id": backup_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    # ══════════════════════════════════════════════════════════════════════
    # API: Save As / Clear All
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/save-as", methods=["POST"])
    @nc_login_required
    def nc_api_save_as(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        new_id = str(_uuid.uuid4())
        now = _now()
        name = data.get("name", f"{row['name']} (copy)")
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, template_id, classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (new_id, name, row["description"], row["graph_json"], row["template_id"], row["classification"], now, now)
        )
        conn.commit()
        conn.close()
        _audit("SAVE_AS", "topology", new_id, f"from {topo_id}")
        return jsonify({"id": new_id, "name": name, "redirect": f"/network/canvas/{new_id}"}), 201

    @bp.route("/api/topologies/<topo_id>/save-as-template", methods=["POST"])
    @nc_login_required
    def nc_api_save_as_template(topo_id):
        conn = get_connection()
        row = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        tpl_id = f"tpl-{str(_uuid.uuid4())[:8]}"
        name = data.get("name", row["name"])
        category = data.get("category", "Custom")
        tags = json.dumps(data.get("tags", ["custom", "user-created"]))
        conn.execute(
            "INSERT INTO nc_templates (id, name, category, description, graph_json, tags) VALUES (?,?,?,?,?,?)",
            (tpl_id, name, category, data.get("description", row["description"] or ""), row["graph_json"], tags)
        )
        conn.commit()
        conn.close()
        _audit("SAVE_AS_TEMPLATE", "template", tpl_id, f"from {topo_id}")
        return jsonify({"id": tpl_id, "name": name}), 201

    # ══════════════════════════════════════════════════════════════════════
    # AI Topology Generator — natural language → JointJS graph JSON
    # Uses Ollama (scanner tier) for air-gap compatibility
    # ══════════════════════════════════════════════════════════════════════

    _AI_TOPO_SYSTEM_PROMPT = """You are a network topology generator for a dark-themed canvas (navy background #1a1a2e). Given a natural language description, output ONLY a valid JSON object — no markdown, no explanation, no code fences:

{"nodes": [...], "edges": [...]}

Each node: {"id": "unique-id", "label": "Display Name", "type": "device-type", "x": number, "y": number, "config": {}}

Each edge: {"id": "unique-id", "source": "node-id", "target": "node-id", "label": "link label", "protocol": "protocol or empty"}

Valid device types (use ONLY these):
- router, switch-l2, switch-l3, firewall, load-balancer, wap, server, patch-panel
- endpoint-pc, endpoint-phone, endpoint-iot, endpoint-camera
- cloud, aws-vpc, aws-tgw, aws-subnet, az-vnet, az-fw, gcp-vpc
- vrf, vlan, subnet, security-zone
- kg-175d, kg-175g, kg-250, kg-340, type1-encryptor, fips-140-l2, fips-140-l3, hsm, macsec
- siem, sdwan-edge, mpls-pe, mpls-p, route-reflector, pop
- media-fiber, media-ge, media-10ge, media-100ge
- roadm, oadm, edfa, transponder, sonet-adm
- meet-me-room, cross-connect
- draw-rect (zone boundary box)
- text-heading (zone title label)
- text-badge (pill-shaped tag)

Zone color palette (dark fills, bright borders — ALWAYS use these for draw-rect):
- Blue zone:   config: {"_fill": "#0a1628", "_stroke": "#3498db", "_width": W, "_height": H}
- Green zone:  config: {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": W, "_height": H}
- Orange zone: config: {"_fill": "#1a1500", "_stroke": "#f39c12", "_width": W, "_height": H}
- Red zone:    config: {"_fill": "#1a0a0a", "_stroke": "#e74c3c", "_width": W, "_height": H}
- Purple zone: config: {"_fill": "#120a20", "_stroke": "#9b59b6", "_width": W, "_height": H}
- Teal zone:   config: {"_fill": "#0a1a1a", "_stroke": "#00cec9", "_width": W, "_height": H}

For text-heading: config: {"_textColor": "<matching zone stroke color>"}
For text-badge:   config: {"_fill": "#0f3460", "_stroke": "#4a9eff"}

Layout rules:
- Zone boxes (draw-rect) MUST be placed FIRST in the nodes array (they render behind devices)
- Text-heading labels go right after their zone box, positioned at the zone's top (y + 5)
- Devices go inside their zone boxes
- Space devices 150-200px apart horizontally, 120-150px vertically
- Zone boxes should be wide enough (300-600px) and tall enough (120-250px) to contain their devices with padding
- Start layout at x=40, y=40
- Use realistic protocols: OSPF, BGP, eBGP, iBGP, MPLS, IPSec, mTLS, STP, VXLAN, etc.

Output ONLY the JSON object. No other text."""

    @bp.route("/api/ai-generate", methods=["POST"])
    @nc_login_required
    def nc_api_ai_generate():
        """Generate topology from natural language description using Claude or Ollama."""
        data = request.get_json(force=True, silent=True) or {}
        description = data.get("description", "").strip()
        if not description:
            return jsonify({"error": "description required"}), 400

        import re
        import requests as _req

        def _parse_llm_response(content):
            """Extract and validate JSON from LLM response text."""
            text = content.strip()
            text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                text = "\n".join(lines).strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start < 0 or end <= start:
                return None, text[:500]
            graph_json = json.loads(text[start:end])
            if "nodes" not in graph_json or "edges" not in graph_json:
                return None, text[:500]
            for n in graph_json["nodes"]:
                n.setdefault("id", str(_uuid.uuid4())[:8])
                n.setdefault("label", "")
                n.setdefault("type", "server")
                n.setdefault("x", 100)
                n.setdefault("y", 100)
                n.setdefault("config", {})
            for e in graph_json["edges"]:
                e.setdefault("id", str(_uuid.uuid4())[:8])
                e.setdefault("source", "")
                e.setdefault("target", "")
                e.setdefault("label", "")
                e.setdefault("protocol", "")
            return graph_json, None

        def _call_claude(desc):
            """Call Anthropic Claude API."""
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                return None, "No ANTHROPIC_API_KEY set"
            model = os.environ.get("ANTHROPIC_TOPO_MODEL", "claude-sonnet-4-20250514")
            r = _req.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 4096,
                    "temperature": 0.3,
                    "system": _AI_TOPO_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": desc}],
                },
                timeout=60,
            )
            r.raise_for_status()
            content = r.json().get("content", [{}])[0].get("text", "")
            return content, None

        def _call_ollama(desc):
            """Call Ollama local LLM."""
            ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ollama_model = os.environ.get("OLLAMA_TOPO_MODEL", "llama3.2:3b")
            r = _req.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": [
                        {"role": "system", "content": _AI_TOPO_SYSTEM_PROMPT},
                        {"role": "user", "content": desc},
                    ],
                    "stream": False,
                    "options": {"num_predict": 4096, "temperature": 0.3},
                },
                timeout=120,
            )
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "")
            return content, None

        try:
            # Try Claude first (fast, reliable), fall back to Ollama (air-gap)
            provider = os.environ.get("NC_AI_PROVIDER", "auto")  # auto | claude | ollama
            content = None
            used_provider = ""

            if provider in ("auto", "claude"):
                content, err = _call_claude(description)
                if content:
                    used_provider = "claude"
                elif provider == "claude":
                    return jsonify({"error": f"Claude API failed: {err}"}), 503

            if not content and provider in ("auto", "ollama"):
                content, err = _call_ollama(description)
                if content:
                    used_provider = "ollama"
                elif provider == "ollama":
                    return jsonify({"error": f"Ollama failed: {err}"}), 503

            if not content:
                return jsonify({"error": "No LLM provider available. Set ANTHROPIC_API_KEY or start Ollama."}), 503

            graph_json, raw = _parse_llm_response(content)
            if graph_json is None:
                return jsonify({"error": "LLM did not return valid JSON", "raw": raw}), 422

            _audit("AI_GENERATE", "topology", "", f"[{used_provider}] Generated from: {description[:100]}")
            return jsonify({
                "graph_json": graph_json,
                "description": description,
                "node_count": len(graph_json["nodes"]),
                "edge_count": len(graph_json["edges"]),
                "provider": used_provider,
            })

        except _req.exceptions.ConnectionError:
            return jsonify({"error": "Cannot connect to LLM provider"}), 503
        except _req.exceptions.Timeout:
            return jsonify({"error": "LLM timed out — try a simpler description"}), 504
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"Invalid JSON from LLM: {exc}"}), 422
        except Exception as exc:
            logger.exception("AI generate failed")
            return jsonify({"error": str(exc)}), 500

    # ══════════════════════════════════════════════════════════════════════
    # API: ATO Package Auto-Generator
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/ato/<topo_id>/generate", methods=["POST"])
    @nc_login_required
    def nc_api_ato_generate(topo_id):
        """Generate a partial ATO package from a topology (or region)."""
        conn = get_connection()
        topo = conn.execute(
            "SELECT id, name, graph_json, classification FROM topologies WHERE id=?",
            (topo_id,),
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        data = request.get_json(force=True, silent=True) or {}
        region_id = data.get("region_id")  # optional group ID
        system_name = data.get("system_name", topo["name"])
        classification = data.get("classification", topo["classification"] or "CUI")

        # Load regimes from compliance profile or request
        profile = conn.execute(
            "SELECT regimes, classification, environment FROM nc_compliance_profiles WHERE topology_id=?",
            (topo_id,),
        ).fetchone()
        if profile:
            try:
                regimes = json.loads(profile["regimes"] or "[]")
            except Exception:
                regimes = data.get("regimes", ["fisma_high", "stig"])
            classification = data.get("classification", profile["classification"] or classification)
        else:
            regimes = data.get("regimes", ["fisma_high", "stig"])

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph JSON"}), 500

        # Load groups for region filtering
        groups = []
        if region_id:
            rows = conn.execute(
                "SELECT * FROM nc_groups WHERE topology_id=?", (topo_id,)
            ).fetchall()
            groups = [_row_to_dict(r) for r in rows]

        # Check for as-built version
        as_built = conn.execute(
            "SELECT id FROM nc_versions WHERE topology_id=? AND label='As-Built' LIMIT 1",
            (topo_id,),
        ).fetchone()
        has_as_built = as_built is not None

        # Generate the ATO package
        package = generate_ato_package(
            topology_id=topo_id,
            graph=graph,
            system_name=system_name,
            classification=classification,
            regimes=regimes,
            groups=groups,
            region_id=region_id,
            has_as_built_version=has_as_built,
        )

        # Persist to DB
        pkg_id = package["package_id"]
        now = _now()
        user_id = ""
        try:
            user_id = session.get("user_id", "")
        except RuntimeError:
            pass

        conn.execute(
            "INSERT INTO nc_ato_packages "
            "(id, topology_id, region_id, system_name, classification, regimes, "
            "package_json, summary_json, overall_readiness, stig_pass_rate, "
            "compliance_score, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                pkg_id, topo_id, region_id, system_name, classification,
                json.dumps(regimes), json.dumps(package),
                json.dumps(package["summary"]),
                package["summary"]["overall_readiness"],
                package["summary"]["stig_pass_rate"],
                package["summary"]["compliance_score"],
                user_id, now,
            ),
        )
        conn.commit()
        conn.close()

        _audit("ATO_GENERATE", "topology", topo_id,
               f"ATO package {pkg_id[:8]} | readiness={package['summary']['overall_readiness']} "
               f"| region={region_id or 'full'}")

        return jsonify(package), 201

    @bp.route("/api/ato/<topo_id>/packages", methods=["GET"])
    @nc_login_required
    def nc_api_ato_list(topo_id):
        """List all generated ATO packages for a topology."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, topology_id, region_id, system_name, classification, "
            "regimes, summary_json, overall_readiness, stig_pass_rate, "
            "compliance_score, created_by, created_at "
            "FROM nc_ato_packages WHERE topology_id=? ORDER BY created_at DESC",
            (topo_id,),
        ).fetchall()
        conn.close()
        results = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                d["summary"] = json.loads(d.pop("summary_json", "{}"))
            except Exception:
                d["summary"] = {}
            try:
                d["regimes"] = json.loads(d.get("regimes") or "[]")
            except Exception:
                pass
            results.append(d)
        return jsonify(results)

    @bp.route("/api/ato/<topo_id>/packages/<pkg_id>", methods=["GET"])
    @nc_login_required
    def nc_api_ato_detail(topo_id, pkg_id):
        """Get the full ATO package (all artifacts) by package ID."""
        conn = get_connection()
        row = conn.execute(
            "SELECT package_json FROM nc_ato_packages WHERE id=? AND topology_id=?",
            (pkg_id, topo_id),
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Package not found"}), 404
        try:
            return jsonify(json.loads(row["package_json"]))
        except Exception:
            return jsonify({"error": "Corrupt package data"}), 500

    @bp.route("/api/ato/<topo_id>/packages/<pkg_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_ato_delete(topo_id, pkg_id):
        """Delete an ATO package."""
        conn = get_connection()
        conn.execute(
            "DELETE FROM nc_ato_packages WHERE id=? AND topology_id=?",
            (pkg_id, topo_id),
        )
        conn.commit()
        conn.close()
        _audit("ATO_DELETE", "ato_package", pkg_id, f"topology={topo_id}")
        return jsonify({"ok": True})

    # ══════════════════════════════════════════════════════════════════════
    # PPS Matrix Generator — Enclave / Device Pair
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/pps/<topo_id>")
    @nc_login_required
    def nc_pps_page(topo_id):
        """PPS Matrix Generator page — select two enclaves or device pair."""
        conn = get_connection()
        topo = conn.execute(
            "SELECT id, name, graph_json, classification FROM topologies WHERE id=?",
            (topo_id,),
        ).fetchone()
        if not topo:
            conn.close()
            abort(404)

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        groups = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_groups WHERE topology_id=?", (topo_id,)
        ).fetchall()]
        conn.close()

        meta = get_topology_enclaves(graph, groups)
        classification_banner = (topo["classification"] or "").upper()

        return render_template(
            "network/pps_matrix.html",
            topology=_row_to_dict(topo),
            enclaves=meta["enclaves"],
            nodes=meta["nodes"],
            classification_banner=classification_banner,
        )

    @bp.route("/api/pps/<topo_id>/enclaves", methods=["GET"])
    @nc_login_required
    def nc_api_pps_enclaves(topo_id):
        """Return enclaves and nodes available for pair selection."""
        conn = get_connection()
        topo = conn.execute(
            "SELECT graph_json FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        groups = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_groups WHERE topology_id=?", (topo_id,)
        ).fetchall()]
        conn.close()

        return jsonify(get_topology_enclaves(graph, groups))

    @bp.route("/api/pps/<topo_id>/generate", methods=["POST"])
    @nc_login_required
    def nc_api_pps_generate(topo_id):
        """Generate PPS matrix for a selected enclave or device pair.

        Body JSON:
          {
            "source": "<zone_name | node_id>",
            "dest": "<zone_name | node_id>",
            "selector_type": "zone" | "node"   // default "zone"
          }
        """
        conn = get_connection()
        topo = conn.execute(
            "SELECT graph_json FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        data = request.get_json(force=True, silent=True) or {}
        source = (data.get("source") or "").strip()
        dest = (data.get("dest") or "").strip()
        selector_type = data.get("selector_type", "zone")

        if not source or not dest:
            conn.close()
            return jsonify({"error": "source and dest are required"}), 400
        if source == dest:
            conn.close()
            return jsonify({"error": "source and dest must be different"}), 400
        if selector_type not in ("zone", "node"):
            selector_type = "zone"

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph JSON"}), 500

        groups = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_groups WHERE topology_id=?", (topo_id,)
        ).fetchall()]
        conn.close()

        result = generate_pps_matrix_for_pair(
            graph=graph,
            source_selector=source,
            dest_selector=dest,
            selector_type=selector_type,
            groups=groups,
        )
        _audit("PPS_GENERATE", "topology", topo_id,
               f"pair={source}<->{dest} type={selector_type} "
               f"protocols={result['total_protocols']}")
        return jsonify(result)

    @bp.route("/api/pps/<topo_id>/export", methods=["POST"])
    @nc_login_required
    def nc_api_pps_export(topo_id):
        """Export a PPS matrix as SSP table (CSV or Markdown).

        Body JSON: same as /api/pps/<topo_id>/generate plus:
          {
            "format": "csv" | "markdown"   // default "csv"
          }
        """
        conn = get_connection()
        topo = conn.execute(
            "SELECT graph_json FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        data = request.get_json(force=True, silent=True) or {}
        source = (data.get("source") or "").strip()
        dest = (data.get("dest") or "").strip()
        selector_type = data.get("selector_type", "zone")
        fmt = data.get("format", "csv")

        if not source or not dest:
            conn.close()
            return jsonify({"error": "source and dest are required"}), 400
        if selector_type not in ("zone", "node"):
            selector_type = "zone"
        if fmt not in ("csv", "markdown"):
            fmt = "csv"

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph JSON"}), 500

        groups = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_groups WHERE topology_id=?", (topo_id,)
        ).fetchall()]
        conn.close()

        result = generate_pps_matrix_for_pair(
            graph=graph,
            source_selector=source,
            dest_selector=dest,
            selector_type=selector_type,
            groups=groups,
        )
        content = export_pps_as_ssp_table(result, fmt=fmt)
        src_slug = source.replace(" ", "_").replace("/", "-")
        dst_slug = dest.replace(" ", "_").replace("/", "-")
        filename = f"pps_ssp_{src_slug}_to_{dst_slug}.{'csv' if fmt == 'csv' else 'md'}"

        from flask import Response
        mime = "text/csv" if fmt == "csv" else "text/markdown"
        return Response(
            content,
            mimetype=mime,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ── Done ───────────────────────────────────────────────────────────────
    logger.info("Network Design Canvas Blueprint created (%d routes)",
                len(bp.deferred_functions))
    return bp
