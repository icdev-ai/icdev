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
from tools.network.visio_export import export_vsdx, export_ops_csvs  # noqa: E402
from tools.network.inventory_export import (  # noqa: E402
    to_ansible_inventory, to_terraform_hcl,
)
from tools.network.config_generator import (  # noqa: E402
    generate_device_configs, generate_device_configs_zip, list_configurable_nodes,
)
from tools.network.stig_import import import_stig_file  # noqa: E402
from tools.network.intent_validator import (  # noqa: E402
    validate_intent_policy, CONSTRAINT_TYPES,
)
from tools.network.discovery import (  # noqa: E402
    run_discovery, diff_topologies, ping_sweep,
    _HAS_PYSNMP, _HAS_NETMIKO,
)


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

    def _notify(conn, project_id, event_type, title, body=""):
        """Create an in-app notification for a project event."""
        try:
            conn.execute(
                "INSERT INTO nc_notifications "
                "(id, project_id, event_type, title, body, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (str(_uuid.uuid4()), project_id, event_type,
                 title, body, _now())
            )
        except Exception:
            pass  # notifications are best-effort

    # ── P3: Status transition gate checks ─────────────────────────────────
    # Transitions requiring gates: -> in_review, -> approved, -> deployed
    _STATUS_GATES = {
        "in_review": {"min_topos": 1},
        "approved": {"min_compliance_pct": 80, "max_cat1": 0},
        "deployed": {"min_compliance_pct": 80, "max_cat1": 0,
                      "require_approved": True},
    }

    def _check_status_gate(conn, pid, old_status, new_status):
        """Check if status transition is allowed. Returns gate result dict."""
        gate = _STATUS_GATES.get(new_status)
        if not gate:
            return {"blocked": False}
        topo_ids = [r[0] for r in conn.execute(
            "SELECT topology_id FROM nc_project_topologies "
            "WHERE project_id=?", (pid,)
        ).fetchall()]
        failures = []
        # Min topologies
        if gate.get("min_topos") and len(topo_ids) < gate["min_topos"]:
            failures.append(
                f"Need {gate['min_topos']}+ topologies, have {len(topo_ids)}"
            )
        # Compliance checks
        if gate.get("min_compliance_pct") is not None and topo_ids:
            total_p = total_f = 0
            cat1_count = 0
            for tid in topo_ids:
                row = conn.execute(
                    "SELECT passed, failed FROM nc_compliance_checks "
                    "WHERE topology_id=? ORDER BY ran_at DESC LIMIT 1",
                    (tid,)
                ).fetchone()
                if row:
                    total_p += row[0] or 0
                    total_f += row[1] or 0
                c1 = conn.execute(
                    "SELECT COUNT(*) FROM nc_compliance_findings "
                    "WHERE topology_id=? AND status='open' "
                    "AND severity='CAT1'", (tid,)
                ).fetchone()
                cat1_count += c1[0] if c1 else 0
            total = total_p + total_f
            pct = round(total_p * 100 / total) if total else 0
            if pct < gate["min_compliance_pct"]:
                failures.append(
                    f"Compliance {pct}% < required "
                    f"{gate['min_compliance_pct']}%"
                )
            if gate.get("max_cat1") is not None and cat1_count > gate["max_cat1"]:
                failures.append(
                    f"{cat1_count} CAT1 findings open (max {gate['max_cat1']})"
                )
        # Require previous status
        if gate.get("require_approved") and old_status != "approved":
            failures.append(
                f"Must be 'approved' before deploying (currently '{old_status}')"
            )
        if failures:
            return {"blocked": True, "gate": new_status,
                    "failures": failures,
                    "error": "; ".join(failures)}
        return {"blocked": False}

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
        # Project filter support
        filter_project = request.args.get("project", "")
        if filter_project:
            topologies = [_row_to_dict(r) for r in conn.execute(
                "SELECT t.id, t.name, t.description, t.classification, t.created_at, t.updated_at, "
                "json_array_length(json_extract(t.graph_json,'$.nodes')) AS node_count, "
                "json_array_length(json_extract(t.graph_json,'$.edges')) AS edge_count "
                "FROM topologies t JOIN nc_project_topologies pt ON pt.topology_id=t.id "
                "WHERE pt.project_id=? ORDER BY t.updated_at DESC LIMIT 20", (filter_project,)
            ).fetchall()]
        else:
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
        # Load projects list for filter dropdown
        all_projects = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name FROM nc_projects ORDER BY name"
        ).fetchall()]
        # Active project name for display
        active_project = None
        if filter_project:
            ap_row = conn.execute("SELECT id, name FROM nc_projects WHERE id=?", (filter_project,)).fetchone()
            active_project = _row_to_dict(ap_row) if ap_row else None
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
                               all_projects=all_projects,
                               filter_project=filter_project,
                               active_project=active_project,
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
        if not row:
            conn.close()
            abort(404)
        topo = _row_to_dict(row)
        # Find projects this topology belongs to
        topo_projects = [_row_to_dict(r) for r in conn.execute(
            "SELECT p.id, p.name, p.status FROM nc_projects p "
            "JOIN nc_project_topologies pt ON pt.project_id=p.id "
            "WHERE pt.topology_id=? ORDER BY p.name", (topo_id,)
        ).fetchall()]
        # All projects for quick-switch
        all_projects = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name, status FROM nc_projects ORDER BY name"
        ).fetchall()]
        conn.close()
        return render_template("network/canvas.html",
                               topology_id=topo_id, topology_name=topo["name"],
                               topo_projects=topo_projects, all_projects=all_projects)

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
        filter_project = request.args.get("project", "")
        if filter_project:
            circuits = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_circuits WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies WHERE project_id=?) "
                "ORDER BY updated_at DESC", (filter_project,)
            ).fetchall()]
        else:
            circuits = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_circuits ORDER BY updated_at DESC"
            ).fetchall()]
        stats = {
            "total": len(circuits),
            "installed": sum(1 for c in circuits if c.get("install_status") == "installed"),
            "planned": sum(1 for c in circuits if c.get("install_status") == "planned"),
            "monthly_cost": sum(c.get("monthly_cost_usd") or 0 for c in circuits),
        }
        all_projects = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name FROM nc_projects ORDER BY name"
        ).fetchall()]
        active_project = None
        if filter_project:
            ap_row = conn.execute("SELECT id, name FROM nc_projects WHERE id=?", (filter_project,)).fetchone()
            active_project = _row_to_dict(ap_row) if ap_row else None
        conn.close()
        return render_template("network/circuits.html", circuits=circuits, stats=stats,
                               all_projects=all_projects, filter_project=filter_project,
                               active_project=active_project)

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
        filter_project = request.args.get("project", "")
        if filter_project:
            blocks = [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_ipam_blocks WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies WHERE project_id=?) "
                "ORDER BY network", (filter_project,)
            ).fetchall()]
        else:
            blocks = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_ipam_blocks ORDER BY network").fetchall()]
        all_projects = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name FROM nc_projects ORDER BY name"
        ).fetchall()]
        active_project = None
        if filter_project:
            ap_row = conn.execute("SELECT id, name FROM nc_projects WHERE id=?", (filter_project,)).fetchone()
            active_project = _row_to_dict(ap_row) if ap_row else None
        conn.close()
        return render_template("network/ipam.html", blocks=blocks,
                               all_projects=all_projects, filter_project=filter_project,
                               active_project=active_project)

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

    @bp.route("/netbox")
    @nc_login_required
    def nc_netbox():
        """NetBox IPAM source-of-truth browser — connection config, cached objects, sync log."""
        conn = get_connection()
        # Config (token redacted)
        cfg_row = conn.execute(
            "SELECT url, site_filter, timeout_sec, auto_sync, last_tested FROM nc_netbox_config WHERE id='default'"
        ).fetchone()
        cfg = _row_to_dict(cfg_row) if cfg_row else {}
        cfg["configured"] = bool(cfg.get("url"))
        # Cached objects grouped by resource type
        obj_rows = conn.execute(
            "SELECT netbox_resource, COUNT(*) AS cnt FROM nc_netbox_objects GROUP BY netbox_resource"
        ).fetchall()
        cached_counts = {r[0]: r[1] for r in obj_rows}
        # Recent sync log
        log_rows = conn.execute(
            "SELECT * FROM nc_netbox_sync_log ORDER BY ran_at DESC LIMIT 50"
        ).fetchall()
        sync_log = [_row_to_dict(r) for r in log_rows]
        # Topologies for the import-to-canvas picker
        topo_rows = conn.execute(
            "SELECT id, name FROM topologies ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
        topologies = [_row_to_dict(r) for r in topo_rows]
        conn.close()
        return render_template(
            "network/netbox.html",
            cfg=cfg,
            cached_counts=cached_counts,
            sync_log=sync_log,
            topologies=topologies,
        )

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

        # ── Portfolio health data per project ─────────────────────────────
        for p in projects:
            pid = p["id"]
            topo_ids = [r[0] for r in conn.execute(
                "SELECT topology_id FROM nc_project_topologies WHERE project_id=?", (pid,)
            ).fetchall()]
            # Compliance: latest audit pass/fail across topologies
            total_passed = total_failed = 0
            open_findings = 0
            for tid in topo_ids:
                row = conn.execute(
                    "SELECT passed, failed FROM nc_compliance_checks WHERE topology_id=? "
                    "ORDER BY ran_at DESC LIMIT 1", (tid,)
                ).fetchone()
                if row:
                    total_passed += row[0] or 0
                    total_failed += row[1] or 0
                of = conn.execute(
                    "SELECT COUNT(*) FROM nc_compliance_findings WHERE topology_id=? AND status='open'", (tid,)
                ).fetchone()
                open_findings += of[0] if of else 0
            total_checks = total_passed + total_failed
            p["compliance_pct"] = round(total_passed * 100 / total_checks) if total_checks else None
            p["open_findings"] = open_findings
            # Cost: sum of circuit monthly costs
            cost_row = conn.execute(
                "SELECT COALESCE(SUM(monthly_cost_usd), 0) FROM nc_circuits WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies WHERE project_id=?)", (pid,)
            ).fetchone()
            p["monthly_cost"] = cost_row[0] if cost_row else 0
            # Node/edge totals
            ne = conn.execute(
                "SELECT COALESCE(SUM(json_array_length(json_extract(t.graph_json,'$.nodes'))),0), "
                "COALESCE(SUM(json_array_length(json_extract(t.graph_json,'$.edges'))),0) "
                "FROM topologies t JOIN nc_project_topologies pt ON pt.topology_id=t.id "
                "WHERE pt.project_id=?", (pid,)
            ).fetchone()
            p["total_nodes"] = ne[0] if ne else 0
            p["total_edges"] = ne[1] if ne else 0
            # Last simulation date
            sim_row = conn.execute(
                "SELECT MAX(ran_at) FROM simulation_results WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies WHERE project_id=?)", (pid,)
            ).fetchone()
            p["last_sim"] = sim_row[0] if sim_row and sim_row[0] else None

        # Portfolio-level aggregates
        portfolio_stats = {
            "total_projects": len(projects),
            "by_status": {},
            "total_cost": sum(p.get("monthly_cost", 0) for p in projects),
            "total_findings": sum(p.get("open_findings", 0) for p in projects),
        }
        for p in projects:
            s = p.get("status", "draft")
            portfolio_stats["by_status"][s] = portfolio_stats["by_status"].get(s, 0) + 1

        # P1: Portfolio-wide activity feed (recent 20 events)
        portfolio_activity = [_row_to_dict(r) for r in conn.execute(
            "SELECT action, entity_type, entity_id, details, "
            "user_id, ts FROM nc_audit "
            "ORDER BY ts DESC LIMIT 20"
        ).fetchall()]

        conn.close()
        return render_template("network/projects.html", projects=projects,
                               customers=customers, portfolio=portfolio_stats,
                               activity=portfolio_activity)

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
            "SELECT t.id, t.name, t.description, t.classification, "
            "t.updated_at, t.graph_json, "
            "json_array_length(json_extract(t.graph_json,'$.nodes')) AS node_count, "
            "json_array_length(json_extract(t.graph_json,'$.edges')) AS edge_count "
            "FROM topologies t JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            "WHERE pt.project_id=? ORDER BY t.updated_at DESC", (proj_id,)
        ).fetchall()]
        circuits = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_circuits WHERE topology_id IN "
            "(SELECT topology_id FROM nc_project_topologies WHERE project_id=?) "
            "ORDER BY circuit_id", (proj_id,)
        ).fetchall()]
        all_topos = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name FROM topologies ORDER BY name"
        ).fetchall()]

        # ── P1: Compliance rollup per topology ────────────────────────────
        topo_compliance = []
        agg_passed = agg_failed = 0
        total_open_findings = 0
        for t in topos:
            tid = t["id"]
            audit_row = conn.execute(
                "SELECT passed, failed, ran_at FROM nc_compliance_checks "
                "WHERE topology_id=? ORDER BY ran_at DESC LIMIT 1", (tid,)
            ).fetchone()
            passed = (audit_row[0] or 0) if audit_row else 0
            failed = (audit_row[1] or 0) if audit_row else 0
            last_audit = audit_row[2] if audit_row else None
            total = passed + failed
            pct = round(passed * 100 / total) if total else None
            agg_passed += passed
            agg_failed += failed
            of_row = conn.execute(
                "SELECT COUNT(*) FROM nc_compliance_findings "
                "WHERE topology_id=? AND status='open'", (tid,)
            ).fetchone()
            open_f = of_row[0] if of_row else 0
            total_open_findings += open_f
            # Findings by severity
            sev_rows = conn.execute(
                "SELECT severity, COUNT(*) FROM nc_compliance_findings "
                "WHERE topology_id=? AND status='open' "
                "GROUP BY severity", (tid,)
            ).fetchall()
            by_sev = {r[0]: r[1] for r in sev_rows}
            topo_compliance.append({
                "id": tid, "name": t["name"],
                "passed": passed, "failed": failed,
                "pct": pct, "open_findings": open_f,
                "cat1": by_sev.get("CAT1", 0),
                "cat2": by_sev.get("CAT2", 0),
                "cat3": by_sev.get("CAT3", 0),
                "last_audit": last_audit,
            })
        agg_total = agg_passed + agg_failed
        agg_pct = round(agg_passed * 100 / agg_total) if agg_total else None

        # ── P1: BOM cost rollup per topology ──────────────────────────────
        topo_bom = []
        total_capex = 0
        total_circuit_cost = sum(
            c.get("monthly_cost_usd") or 0 for c in circuits
        )
        for t in topos:
            try:
                graph = json.loads(t.get("graph_json") or '{"nodes":[]}')
            except Exception:
                graph = {"nodes": []}
            type_counts = {}
            for n in graph.get("nodes", []):
                nt = n.get("type", "unknown")
                type_counts[nt] = type_counts.get(nt, 0) + 1
            capex = sum(
                BOM_COSTS.get(dt, 0) * cnt
                for dt, cnt in type_counts.items()
            )
            total_capex += capex
            topo_bom.append({
                "id": t["id"], "name": t["name"],
                "devices": sum(type_counts.values()),
                "unique_types": len(type_counts),
                "capex": capex,
            })

        # ── P1: Activity feed from nc_audit ───────────────────────────────
        topo_ids = [t["id"] for t in topos]
        if topo_ids:
            placeholders = ",".join("?" for _ in topo_ids)
            activity = [_row_to_dict(r) for r in conn.execute(
                "SELECT action, entity_type, entity_id, details, "
                "user_id, ts FROM nc_audit "
                "WHERE entity_id IN (" + placeholders + ") "  # nosec B608
                "OR (entity_type='project' AND entity_id=?) "
                "ORDER BY ts DESC LIMIT 30",
                topo_ids + [proj_id]
            ).fetchall()]
        else:
            activity = [_row_to_dict(r) for r in conn.execute(
                "SELECT action, entity_type, entity_id, details, "
                "user_id, ts FROM nc_audit "
                "WHERE entity_type='project' AND entity_id=? "
                "ORDER BY ts DESC LIMIT 30", (proj_id,)
            ).fetchall()]

        # Strip graph_json from topos (large, not needed in template)
        for t in topos:
            t.pop("graph_json", None)

        # P3: Milestones, Notes, Tags, Assignees
        milestones = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_project_milestones "
            "WHERE project_id=? ORDER BY due_date", (proj_id,)
        ).fetchall()]
        notes = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_project_notes "
            "WHERE project_id=? ORDER BY created_at DESC", (proj_id,)
        ).fetchall()]
        project_tags = [r[0] for r in conn.execute(
            "SELECT tag FROM nc_tags "
            "WHERE entity_type='project' AND entity_id=? ORDER BY tag",
            (proj_id,)
        ).fetchall()]
        # Assignees per topology
        topo_assignees = {}
        for r in conn.execute(
            "SELECT topology_id, assignee FROM nc_project_topologies "
            "WHERE project_id=? AND assignee != ''", (proj_id,)
        ).fetchall():
            topo_assignees[r[0]] = r[1]

        # Phase A: Review board pipeline
        review_boards = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_review_boards ORDER BY sort_order"
        ).fetchall()]
        board_reviews = [_row_to_dict(r) for r in conn.execute(
            "SELECT br.*, rb.name AS board_name, rb.short_name "
            "FROM nc_board_reviews br "
            "JOIN nc_review_boards rb ON rb.id=br.board_id "
            "WHERE br.project_id=? ORDER BY rb.sort_order, br.phase",
            (proj_id,)
        ).fetchall()]
        project_phases = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_project_phases WHERE project_id=? "
            "ORDER BY phase_num", (proj_id,)
        ).fetchall()]
        safe_bridge = conn.execute(
            "SELECT * FROM nc_safe_bridge WHERE project_id=?", (proj_id,)
        ).fetchone()
        safe_bridge = _row_to_dict(safe_bridge) if safe_bridge else None
        if safe_bridge and safe_bridge.get("roi_json"):
            try:
                safe_bridge["roi"] = json.loads(safe_bridge["roi_json"])
            except Exception:
                safe_bridge["roi"] = {}

        conn.close()
        return render_template("network/project_detail.html",
                               project=proj, topologies=topos,
                               circuits=circuits, all_topos=all_topos,
                               topo_compliance=topo_compliance,
                               agg_compliance_pct=agg_pct,
                               total_open_findings=total_open_findings,
                               topo_bom=topo_bom,
                               total_capex=total_capex,
                               total_circuit_cost=total_circuit_cost,
                               activity=activity,
                               milestones=milestones,
                               notes=notes,
                               project_tags=project_tags,
                               topo_assignees=topo_assignees,
                               review_boards=review_boards,
                               board_reviews=board_reviews,
                               project_phases=project_phases,
                               safe_bridge=safe_bridge)

    # ══════════════════════════════════════════════════════════════════════
    # P2: Cross-Project Comparison
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/projects/compare")
    @nc_login_required
    def nc_project_compare():
        conn = get_connection()
        projects = [_row_to_dict(r) for r in conn.execute(
            "SELECT p.*, c.name AS customer_name, "
            "(SELECT COUNT(*) FROM nc_project_topologies pt "
            " WHERE pt.project_id=p.id) AS topo_count "
            "FROM nc_projects p "
            "LEFT JOIN nc_customers c ON c.id=p.customer_id "
            "ORDER BY p.name"
        ).fetchall()]

        comparison = []
        for p in projects:
            pid = p["id"]
            topo_ids = [r[0] for r in conn.execute(
                "SELECT topology_id FROM nc_project_topologies "
                "WHERE project_id=?", (pid,)
            ).fetchall()]

            # Compliance aggregate
            total_passed = total_failed = 0
            open_findings = cat1 = cat2 = cat3 = 0
            for tid in topo_ids:
                row = conn.execute(
                    "SELECT passed, failed FROM nc_compliance_checks "
                    "WHERE topology_id=? ORDER BY ran_at DESC LIMIT 1",
                    (tid,)
                ).fetchone()
                if row:
                    total_passed += row[0] or 0
                    total_failed += row[1] or 0
                sev_rows = conn.execute(
                    "SELECT severity, COUNT(*) FROM nc_compliance_findings "
                    "WHERE topology_id=? AND status='open' "
                    "GROUP BY severity", (tid,)
                ).fetchall()
                for sr in sev_rows:
                    if sr[0] == "CAT1":
                        cat1 += sr[1]
                    elif sr[0] == "CAT2":
                        cat2 += sr[1]
                    elif sr[0] == "CAT3":
                        cat3 += sr[1]
                    open_findings += sr[1]

            total_checks = total_passed + total_failed
            comp_pct = round(
                total_passed * 100 / total_checks
            ) if total_checks else None

            # Cost
            cost_row = conn.execute(
                "SELECT COALESCE(SUM(monthly_cost_usd), 0) "
                "FROM nc_circuits WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies "
                " WHERE project_id=?)", (pid,)
            ).fetchone()
            circuit_cost = cost_row[0] if cost_row else 0

            # BOM CapEx
            capex = 0
            total_devices = 0
            for tid in topo_ids:
                trow = conn.execute(
                    "SELECT graph_json FROM topologies WHERE id=?",
                    (tid,)
                ).fetchone()
                if trow:
                    try:
                        g = json.loads(trow["graph_json"])
                    except Exception:
                        g = {"nodes": []}
                    for n in g.get("nodes", []):
                        nt = n.get("type", "unknown")
                        capex += BOM_COSTS.get(nt, 0)
                        total_devices += 1

            # Node/edge totals
            ne = conn.execute(
                "SELECT "
                "COALESCE(SUM(json_array_length("
                "  json_extract(t.graph_json,'$.nodes'))),0), "
                "COALESCE(SUM(json_array_length("
                "  json_extract(t.graph_json,'$.edges'))),0) "
                "FROM topologies t "
                "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
                "WHERE pt.project_id=?", (pid,)
            ).fetchone()

            # Last MC resilience score
            mc_row = conn.execute(
                "SELECT result_json FROM nc_mc_runs "
                "WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies "
                " WHERE project_id=?) "
                "ORDER BY ran_at DESC LIMIT 1", (pid,)
            ).fetchone()
            mc_score = None
            if mc_row:
                try:
                    mc_data = json.loads(mc_row["result_json"])
                    mc_score = mc_data.get("risk_score")
                except Exception:
                    pass

            comparison.append({
                "id": pid,
                "name": p["name"],
                "status": p["status"],
                "customer": p.get("customer_name", ""),
                "topo_count": p["topo_count"],
                "nodes": ne[0] if ne else 0,
                "edges": ne[1] if ne else 0,
                "compliance_pct": comp_pct,
                "open_findings": open_findings,
                "cat1": cat1,
                "cat2": cat2,
                "cat3": cat3,
                "circuit_cost": circuit_cost,
                "capex": capex,
                "devices": total_devices,
                "mc_resilience": mc_score,
            })
        conn.close()
        return render_template("network/compare.html",
                               comparison=comparison)

    # ══════════════════════════════════════════════════════════════════════
    # P2: Clone Project API
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/projects/<pid>/clone", methods=["POST"])
    @nc_login_required
    def nc_api_clone_project(pid):
        data = request.get_json(force=True, silent=True) or {}
        new_name = data.get("name", "")
        conn = get_connection()
        orig = conn.execute(
            "SELECT * FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        if not orig:
            conn.close()
            return jsonify({"error": "Project not found"}), 404
        orig = _row_to_dict(orig)

        now = _now()
        new_pid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_projects "
            "(id, name, customer_id, description, status, owner, "
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (new_pid,
             new_name or f"{orig['name']} (Copy)",
             orig.get("customer_id"),
             orig.get("description", ""),
             "draft", orig.get("owner", ""), now, now)
        )

        # Deep-copy topologies
        topo_map = {}  # old_id -> new_id
        orig_topos = conn.execute(
            "SELECT topology_id FROM nc_project_topologies "
            "WHERE project_id=?", (pid,)
        ).fetchall()
        for row in orig_topos:
            old_tid = row[0]
            topo = conn.execute(
                "SELECT * FROM topologies WHERE id=?", (old_tid,)
            ).fetchone()
            if not topo:
                continue
            topo = _row_to_dict(topo)
            new_tid = str(_uuid.uuid4())
            topo_map[old_tid] = new_tid
            conn.execute(
                "INSERT INTO topologies "
                "(id, name, description, graph_json, template_id, "
                " classification, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (new_tid, f"{topo['name']} (Copy)",
                 topo.get("description", ""),
                 topo.get("graph_json", '{"nodes":[],"edges":[]}'),
                 topo.get("template_id"),
                 topo.get("classification", "public"), now, now)
            )
            conn.execute(
                "INSERT INTO nc_project_topologies "
                "(project_id, topology_id) VALUES (?,?)",
                (new_pid, new_tid)
            )

            # Copy compliance profile
            profile = conn.execute(
                "SELECT * FROM nc_compliance_profiles "
                "WHERE topology_id=?", (old_tid,)
            ).fetchone()
            if profile:
                conn.execute(
                    "INSERT INTO nc_compliance_profiles "
                    "(id, topology_id, regimes, classification, "
                    " environment, auto_audit, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (str(_uuid.uuid4()), new_tid,
                     profile["regimes"], profile["classification"],
                     profile["environment"], profile["auto_audit"],
                     now, now)
                )

            # Copy circuits
            circuits = conn.execute(
                "SELECT * FROM nc_circuits WHERE topology_id=?",
                (old_tid,)
            ).fetchall()
            for c in circuits:
                c = _row_to_dict(c)
                conn.execute(
                    "INSERT INTO nc_circuits "
                    "(id, topology_id, circuit_id, carrier, "
                    " circuit_type, bandwidth, handoff_a, handoff_z, "
                    " customer, site, monthly_cost_usd, "
                    " contract_start, contract_end, sla_uptime_pct, "
                    " install_status, notes, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (str(_uuid.uuid4()), new_tid,
                     c["circuit_id"], c.get("carrier"),
                     c.get("circuit_type"), c.get("bandwidth"),
                     c.get("handoff_a"), c.get("handoff_z"),
                     c.get("customer"), c.get("site"),
                     c.get("monthly_cost_usd", 0),
                     c.get("contract_start"), c.get("contract_end"),
                     c.get("sla_uptime_pct", 99.9),
                     c.get("install_status", "planned"),
                     c.get("notes"), now, now)
                )

            # Copy IPAM blocks
            blocks = conn.execute(
                "SELECT * FROM nc_ipam_blocks WHERE topology_id=?",
                (old_tid,)
            ).fetchall()
            for b in blocks:
                b = _row_to_dict(b)
                conn.execute(
                    "INSERT INTO nc_ipam_blocks "
                    "(id, topology_id, network, vlan_id, vrf, "
                    " description, site_id, gateway, "
                    " utilization_pct, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (str(_uuid.uuid4()), new_tid,
                     b["network"], b.get("vlan_id"),
                     b.get("vrf", "global"),
                     b.get("description"), b.get("site_id"),
                     b.get("gateway"),
                     b.get("utilization_pct", 0), now)
                )

        conn.commit()
        conn.close()
        _audit("CLONE", "project", new_pid,
               f"Cloned from {orig['name']} ({pid})")
        return jsonify({
            "id": new_pid,
            "name": new_name or f"{orig['name']} (Copy)",
            "topologies_cloned": len(topo_map),
            "source_id": pid,
        }), 201

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
            "nc_device_geo",
            "nc_routing_entries", "nc_collected_configs",
            "nc_intent_validations", "nc_intent_policies",
            "nc_change_request_items", "nc_change_requests",
            "nc_stig_imports", "nc_ato_packages",
            "nc_boundaries", "nc_netbox_objects", "nc_netbox_sync_log",
            "nc_discovery_diffs", "nc_discovery_scans",
            "nc_mc_runs", "nc_mc_scenarios",
            "simulation_results", "nc_objects",
            "nc_circuits", "nc_cables", "nc_cross_connects",
            "nc_versions", "nc_compliance_findings",
            "nc_compliance_checks", "nc_compliance_profiles",
            "nc_ipam_blocks", "nc_project_topologies",
            "nc_groups", "nc_interconnects",
        ]
        for tbl in child_tables:
            conn.execute(f"DELETE FROM {tbl}")  # nosec B608 -- table/column names are internal constants, not user input
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
        conn.execute(f"UPDATE topologies SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
        conn.execute(f"UPDATE nc_templates SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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

    @bp.route("/api/export/<topo_id>/ansible", methods=["POST"])
    @nc_login_required
    def nc_api_export_ansible(topo_id):
        """Export topology as an Ansible inventory INI file.

        Hosts are grouped first by security enclave boundary (from
        nc_boundaries) and then by zone/role derived from node type.
        Cloud-infrastructure nodes (VPCs, subnets, etc.) are emitted as
        comments for reference only.
        """
        conn = get_connection()
        row = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        boundary_rows = conn.execute(
            "SELECT label, classification, node_ids FROM nc_boundaries WHERE topology_id=?",
            (topo_id,),
        ).fetchall()
        conn.close()
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        boundaries = [dict(r) for r in boundary_rows]
        content = to_ansible_inventory(graph, topo["name"], boundaries=boundaries)
        safe_name = topo["name"].replace(" ", "_")
        _audit("EXPORT", "topology", topo_id, "ansible")
        return jsonify({
            "format": "ansible",
            "filename": f"{safe_name}_inventory.ini",
            "content": content,
            "enclave_count": len(boundaries),
        })

    @bp.route("/api/export/<topo_id>/terraform", methods=["POST"])
    @nc_login_required
    def nc_api_export_terraform(topo_id):
        """Export topology as a Terraform HCL skeleton (main.tf).

        Generates provider blocks, resource stubs for every cloud node,
        security-group / NSG / firewall resources for each enclave boundary,
        and a locals block mapping diagram edges to conceptual connectivity
        rules (security-group / NACL / firewall policy inputs).
        """
        conn = get_connection()
        row = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        topo = _row_to_dict(row)
        boundary_rows = conn.execute(
            "SELECT label, classification, node_ids FROM nc_boundaries WHERE topology_id=?",
            (topo_id,),
        ).fetchall()
        conn.close()
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}
        boundaries = [dict(r) for r in boundary_rows]
        content = to_terraform_hcl(graph, topo["name"], boundaries=boundaries)
        safe_name = topo["name"].replace(" ", "_")
        _audit("EXPORT", "topology", topo_id, "terraform")
        return jsonify({
            "format": "terraform",
            "filename": f"{safe_name}_main.tf",
            "content": content,
            "enclave_count": len(boundaries),
        })

    @bp.route("/api/export/<topo_id>/device-configs", methods=["POST"])
    @nc_login_required
    def nc_api_export_device_configs(topo_id):
        """Export per-device configuration files (IOS/EOS/JunOS) as a ZIP archive.

        Body (JSON, optional):
          format: "zip" (default) | "json"
            - "zip"  — base64-encoded ZIP bytes; client decodes and downloads.
            - "json" — dict mapping filename -> config text for in-browser preview.

        Each configurable node (router, switch, firewall) gets its own config file
        rendered from a Jinja2 template using the node's assigned IPs, VLANs,
        routing protocol settings, and connected edges.
        """
        import base64

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

        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "zip")
        safe_name = topo["name"].replace(" ", "_")

        if fmt == "json":
            configs = generate_device_configs(graph, topo["name"])
            _audit("EXPORT", "topology", topo_id, f"device-configs json devices={len(configs)}")
            return jsonify({
                "format": "json",
                "topo_name": topo["name"],
                "device_count": len(configs),
                "configs": configs,
            })

        # Default: ZIP
        zip_bytes = generate_device_configs_zip(graph, topo["name"])
        encoded = base64.b64encode(zip_bytes).decode("ascii")
        device_count = len(list_configurable_nodes(graph))
        _audit("EXPORT", "topology", topo_id, f"device-configs zip devices={device_count}")
        return jsonify({
            "format": "zip",
            "filename": f"{safe_name}_device_configs.zip",
            "content_b64": encoded,
            "device_count": device_count,
        })

    @bp.route("/api/export/<topo_id>/device-configs/preview", methods=["GET"])
    @nc_login_required
    def nc_api_export_device_configs_preview(topo_id):
        """Return a list of configurable nodes and their OS type for UI preview."""
        conn = get_connection()
        row = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row[0] or '{"nodes":[],"edges":[]}')
        except Exception:
            graph = {"nodes": [], "edges": []}
        nodes = list_configurable_nodes(graph)
        return jsonify({"configurable_nodes": nodes, "count": len(nodes)})

    @bp.route("/api/export/<topo_id>/vsdx", methods=["POST"])
    @nc_login_required
    def nc_api_export_vsdx(topo_id):
        """Export topology as modern Visio .vsdx file with embedded metadata."""
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
        import base64
        import re as _re
        vsdx_bytes = export_vsdx(topo["name"], graph)
        encoded = base64.b64encode(vsdx_bytes).decode("ascii")
        safe_name = _re.sub(r'[^a-zA-Z0-9_-]', '_', topo["name"])
        _audit("EXPORT", "topology", topo_id, "vsdx")
        return jsonify({
            "format": "vsdx",
            "filename": f"{safe_name}.vsdx",
            "content_b64": encoded,
        })

    @bp.route("/api/export/<topo_id>/csv", methods=["POST"])
    @nc_login_required
    def nc_api_export_csv(topo_id):
        """Export topology as Ops CSV bundle (device inventory, circuits, cables, IP, peering)."""
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
        csv_files = export_ops_csvs(topo["name"], graph)
        import base64
        import io as _io
        import re as _re
        buf = _io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, content in csv_files.items():
                zf.writestr(fname, content)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        safe_name = _re.sub(r'[^a-zA-Z0-9_-]', '_', topo["name"])
        _audit("EXPORT", "topology", topo_id, "csv")
        return jsonify({
            "format": "csv",
            "filename": f"{safe_name}_ops_csvs.zip",
            "content_b64": encoded,
        })

    @bp.route("/api/export/<topo_id>/inventory", methods=["GET"])
    @nc_login_required
    def nc_api_export_inventory(topo_id):
        """Return JSON inventory of all devices with full metadata."""
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
        devices = []
        for n in graph.get("nodes", []):
            ntype = n.get("type", "")
            if ntype in ("draw-rect", "zone", "boundary", "text-annotation"):
                continue
            cfg = n.get("config", {})
            devices.append({
                "id": n.get("id", ""),
                "label": n.get("label", ""),
                "type": ntype,
                "hostname": cfg.get("hostname", ""),
                "ip": cfg.get("ip", ""),
                "model": cfg.get("model", ""),
                "serial": cfg.get("serial", ""),
                "asset_tag": cfg.get("asset_tag", ""),
                "site": cfg.get("site", ""),
                "location": cfg.get("location", ""),
                "rack": cfg.get("rack", ""),
                "slot": cfg.get("slot", ""),
                "port": cfg.get("port", ""),
                "port_type": cfg.get("port_type", ""),
                "bandwidth": cfg.get("bandwidth", ""),
                "vlan": cfg.get("vlan", ""),
                "vrf": cfg.get("vrf", ""),
                "asn": cfg.get("asn", ""),
                "peer_asn": cfg.get("peer_asn", ""),
                "peer_ip": cfg.get("peer_ip", ""),
                "peering_type": cfg.get("peering_type", ""),
            })
        return jsonify({"topology": topo["name"], "device_count": len(devices), "devices": devices})

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
        # Phase 1: auto-classify imported nodes
        graph = _classify_imported_nodes(graph)
        conn = get_connection()
        conn.execute(
            "UPDATE topologies SET graph_json=? WHERE id=?",
            (json.dumps(graph), topo_id)
        )
        conn.commit()
        conn.close()
        _audit("IMPORT", "topology", topo_id, fmt)
        return jsonify({"id": topo_id, "name": name,
                         "nodes": len(graph.get("nodes", [])),
                         "edges": len(graph.get("edges", []))}), 201

    # ══════════════════════════════════════════════════════════════════════
    # Phase 1: Intelligent Import & Stitching
    # ══════════════════════════════════════════════════════════════════════

    # Semantic node classifier — maps labels to device types
    _NODE_CLASSIFY_PATTERNS = [
        (r"(?i)(core|edge|border|wan|pe|ce|p\b).*r(outer|tr)", "router"),
        (r"(?i)r(outer|tr)", "router"),
        (r"(?i)(fw|firewall|palo|forti|asa|checkpoint)", "firewall"),
        (r"(?i)(sw|switch).*l3|layer.?3.*sw|dist.*sw|core.*sw", "switch-l3"),
        (r"(?i)(sw|switch|access)", "switch-l2"),
        (r"(?i)(lb|load.?bal|f5|netscaler|a10)", "load-balancer"),
        (r"(?i)(wap|access.?point|ap\d|wifi|wireless)", "wap"),
        (r"(?i)(wlc|wireless.*control)", "wlc"),
        (r"(?i)(srv|server|host|vm\b|esxi|hypervisor)", "server"),
        (r"(?i)(pc|workstation|desktop|laptop|endpoint)", "endpoint-pc"),
        (r"(?i)(phone|voip|sip)", "ip-phone"),
        (r"(?i)(sdwan|sd-wan|vmanage|vedge)", "sdwan-edge"),
        (r"(?i)(mpls.*pe|pe.*router)", "mpls-pe"),
        (r"(?i)(mpls.*p\b|p.*router|provider)", "mpls-p"),
        (r"(?i)(route.?reflect|rr\b)", "route-reflector"),
        (r"(?i)(encrypt|kg-|type.?1|nsa)", "type1-encryptor"),
        (r"(?i)(fips|hsm)", "fips-140-l2"),
        (r"(?i)(siem|splunk|qradar|arcsight)", "siem"),
        (r"(?i)(tap|span|mirror)", "network-tap"),
        (r"(?i)(vpc|aws)", "aws-vpc"),
        (r"(?i)(vnet|azure)", "az-vnet"),
        (r"(?i)(gcp|google.?cloud)", "gcp-vpc"),
        (r"(?i)(internet|cloud|wan|isp)", "cloud"),
        (r"(?i)(patch.?panel|pp\b|mdf|idf)", "patch-panel"),
        (r"(?i)(ups|pdu|power)", "server"),
        (r"(?i)(demarc|demarcation)", "demarc"),
        (r"(?i)(meet.?me|mmr|colo)", "meet-me-room"),
    ]

    def _classify_imported_nodes(graph):
        """Auto-classify imported nodes from generic 'imported' type
        to specific device types using label pattern matching."""
        import re as _re
        for n in graph.get("nodes", []):
            if n.get("type") not in ("imported", "", None):
                continue
            label = n.get("label", "")
            matched = False
            for pattern, dtype in _NODE_CLASSIFY_PATTERNS:
                if _re.search(pattern, label):
                    n["type"] = dtype
                    matched = True
                    break
            if not matched:
                n["type"] = "server"  # safe default
        return graph

    @bp.route("/api/import/bulk", methods=["POST"])
    @nc_login_required
    def nc_api_bulk_import():
        """Import multiple diagram files at once.
        Each file becomes a separate topology.
        Optionally group under a project."""
        data = request.get_json(force=True, silent=True) or {}
        files = data.get("files", [])
        project_id = data.get("project_id")
        if not files:
            return jsonify({"error": "files array required"}), 400

        conn = get_connection()
        now = _now()
        results = []
        for f in files:
            fmt = f.get("format", "drawio")
            content = f.get("content", "")
            name = f.get("name", "Imported")
            if not content:
                results.append({"name": name, "error": "empty"})
                continue
            if fmt == "drawio":
                graph = import_drawio(content)
            elif fmt in ("vdx", "visio"):
                graph = import_vdx(content)
            elif fmt == "svg":
                graph = import_svg(content)
            else:
                results.append({"name": name, "error": f"bad format: {fmt}"})
                continue
            graph = _classify_imported_nodes(graph)
            topo_id = str(_uuid.uuid4())
            conn.execute(
                "INSERT INTO topologies "
                "(id, name, description, graph_json, "
                " classification, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (topo_id, name, f"Bulk import ({fmt})",
                 json.dumps(graph), "public", now, now)
            )
            if project_id:
                conn.execute(
                    "INSERT OR IGNORE INTO nc_project_topologies "
                    "(project_id, topology_id) VALUES (?,?)",
                    (project_id, topo_id)
                )
            results.append({
                "id": topo_id, "name": name,
                "nodes": len(graph.get("nodes", [])),
                "edges": len(graph.get("edges", [])),
            })
        conn.commit()
        conn.close()
        _audit("BULK_IMPORT", "topology", "",
               f"{len(results)} files imported")
        return jsonify({
            "imported": len([r for r in results if "id" in r]),
            "failed": len([r for r in results if "error" in r]),
            "results": results,
        }), 201

    @bp.route("/api/import/stitch", methods=["POST"])
    @nc_login_required
    def nc_api_stitch_topologies():
        """Merge multiple topologies into one, with user-defined
        interconnect points between them."""
        data = request.get_json(force=True, silent=True) or {}
        topo_ids = data.get("topology_ids", [])
        interconnects = data.get("interconnects", [])
        name = data.get("name", "Stitched Topology")
        if len(topo_ids) < 2:
            return jsonify({"error": "Need 2+ topology_ids"}), 400

        conn = get_connection()
        merged_nodes = []
        merged_edges = []
        offset_x = 0

        for tid in topo_ids:
            row = conn.execute(
                "SELECT name, graph_json FROM topologies WHERE id=?",
                (tid,)
            ).fetchone()
            if not row:
                continue
            try:
                g = json.loads(row["graph_json"])
            except Exception:
                continue
            prefix = tid[:8]
            topo_name = row["name"]
            # Offset nodes horizontally and namespace IDs
            for n in g.get("nodes", []):
                new_id = f"{prefix}_{n['id']}"
                merged_nodes.append({
                    **n, "id": new_id,
                    "x": (n.get("x") or 0) + offset_x,
                    "config": {
                        **(n.get("config") or n.get("configData") or {}),
                        "_source_topology": topo_name,
                        "_source_id": n["id"],
                    },
                })
            for e in g.get("edges", []):
                merged_edges.append({
                    **e, "id": f"{prefix}_{e.get('id', '')}",
                    "source": f"{prefix}_{e['source']}",
                    "target": f"{prefix}_{e['target']}",
                })
            offset_x += 700

        # Add user-defined interconnect edges
        for ic in interconnects:
            merged_edges.append({
                "id": str(_uuid.uuid4())[:8],
                "source": ic.get("source_node_id", ""),
                "target": ic.get("target_node_id", ""),
                "label": ic.get("label", "Interconnect"),
                "protocol": ic.get("protocol", ""),
            })

        merged_graph = {"nodes": merged_nodes, "edges": merged_edges}
        topo_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, "
            " classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (topo_id, name,
             f"Stitched from {len(topo_ids)} topologies",
             json.dumps(merged_graph), "public", now, now)
        )
        conn.commit()
        conn.close()
        _audit("STITCH", "topology", topo_id,
               f"Merged {len(topo_ids)} topologies")
        return jsonify({
            "id": topo_id, "name": name,
            "nodes": len(merged_nodes),
            "edges": len(merged_edges),
            "source_topologies": len(topo_ids),
        }), 201

    @bp.route("/api/import/audit", methods=["POST"])
    @nc_login_required
    def nc_api_import_and_audit():
        """Import a diagram AND immediately run compliance audit,
        design scorecard, and tech debt analysis."""
        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "drawio")
        content = data.get("content", "")
        name = data.get("name", "Audit Import")
        if not content:
            return jsonify({"error": "content required"}), 400

        # Import
        if fmt == "drawio":
            graph = import_drawio(content)
        elif fmt in ("vdx", "visio"):
            graph = import_vdx(content)
        elif fmt == "svg":
            graph = import_svg(content)
        else:
            return jsonify({"error": f"Unsupported: {fmt}"}), 400
        graph = _classify_imported_nodes(graph)
        topo_id = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, "
            " classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (topo_id, name, f"Audit import ({fmt})",
             json.dumps(graph), "CUI", now, now)
        )
        conn.commit()

        # Run compliance audit
        audit_result = run_compliance_audit(
            topo_id, graph, ["fisma_high", "stig"], "CUI")
        total_p = sum(s["passed"] for s in audit_result["scores"].values())
        total_f = sum(s["failed"] for s in audit_result["scores"].values())
        audit_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_compliance_checks "
            "(id, topology_id, check_type, passed, failed, "
            " findings_json, ran_at) VALUES (?,?,?,?,?,?,?)",
            (audit_id, topo_id, "fisma_high,stig",
             total_p, total_f,
             json.dumps(audit_result["findings"]), now)
        )
        conn.commit()
        conn.close()

        _audit("IMPORT_AUDIT", "topology", topo_id, fmt)

        return jsonify({
            "id": topo_id, "name": name,
            "nodes": len(graph.get("nodes", [])),
            "edges": len(graph.get("edges", [])),
            "compliance": {
                "passed": total_p, "failed": total_f,
                "findings": len(audit_result["findings"]),
                "scores": audit_result["scores"],
            },
            "classified_types": dict(
                sorted(
                    {n["type"]: sum(1 for m in graph["nodes"]
                                    if m["type"] == n["type"])
                     for n in graph["nodes"]}.items()
                )
            ),
        }), 201

    @bp.route("/api/classify-nodes", methods=["POST"])
    @nc_login_required
    def nc_api_classify_nodes():
        """Re-classify nodes in an existing topology.
        Useful after manual edits or to fix imported types."""
        data = request.get_json(force=True, silent=True) or {}
        topo_id = data.get("topology_id")
        if not topo_id:
            return jsonify({"error": "topology_id required"}), 400
        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph"}), 500
        # Only classify nodes that are still "imported" type
        changed = 0
        for n in graph.get("nodes", []):
            old_type = n.get("type", "")
            if old_type in ("imported", ""):
                _classify_imported_nodes({"nodes": [n]})
                if n["type"] != old_type:
                    changed += 1
        conn.execute(
            "UPDATE topologies SET graph_json=?, updated_at=? "
            "WHERE id=?",
            (json.dumps(graph), _now(), topo_id)
        )
        conn.commit()
        conn.close()
        return jsonify({"reclassified": changed,
                         "total_nodes": len(graph.get("nodes", []))})

    # ══════════════════════════════════════════════════════════════════════
    # Phase 2: Device Command Profiles + Non-Intrusive Discovery
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/device-profiles", methods=["GET"])
    @nc_login_required
    def nc_api_list_device_profiles():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, vendor, platform, description, is_builtin, "
            "created_by, created_at FROM nc_device_profiles "
            "ORDER BY vendor, platform"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/device-profiles/<pid>", methods=["GET"])
    @nc_login_required
    def nc_api_get_device_profile(pid):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM nc_device_profiles WHERE id=?", (pid,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        p = _row_to_dict(row)
        try:
            p["commands"] = json.loads(p.get("commands_json") or "{}")
        except Exception:
            p["commands"] = {}
        return jsonify(p)

    @bp.route("/api/device-profiles", methods=["POST"])
    @nc_login_required
    def nc_api_create_device_profile():
        """Create a user-defined device command profile."""
        data = request.get_json(force=True, silent=True) or {}
        pid = str(_uuid.uuid4())
        commands = data.get("commands", {})
        if isinstance(commands, dict):
            commands = json.dumps(commands)
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_device_profiles "
            "(id, vendor, platform, description, commands_json, "
            " is_builtin, created_by, created_at) "
            "VALUES (?,?,?,?,?,0,?,?)",
            (pid, data.get("vendor", "Custom"),
             data.get("platform", "Custom"),
             data.get("description", ""),
             commands,
             data.get("created_by", ""), _now())
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "device_profile", pid,
               f"{data.get('vendor')} {data.get('platform')}")
        return jsonify({"id": pid}), 201

    @bp.route("/api/device-profiles/<pid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_device_profile(pid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        row = conn.execute(
            "SELECT is_builtin FROM nc_device_profiles WHERE id=?",
            (pid,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        fields, values = [], []
        allowed = ["vendor", "platform", "description"]
        if not row[0]:  # user-created can update commands
            allowed.append("commands_json")
        for k in allowed:
            if k in data:
                v = data[k]
                if k == "commands_json" and isinstance(v, dict):
                    v = json.dumps(v)
                fields.append(f"{k}=?")
                values.append(v)
        # Allow adding commands to built-in profiles
        if row[0] and "commands" in data:
            existing = conn.execute(
                "SELECT commands_json FROM nc_device_profiles "
                "WHERE id=?", (pid,)
            ).fetchone()
            try:
                cmds = json.loads(existing[0] or "{}")
            except Exception:
                cmds = {}
            cmds.update(data["commands"])
            fields.append("commands_json=?")
            values.append(json.dumps(cmds))
        if fields:
            values.append(pid)
            conn.execute(
                f"UPDATE nc_device_profiles "  # nosec B608
                f"SET {', '.join(fields)} WHERE id=?", values
            )
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/device-profiles/<pid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_device_profile(pid):
        conn = get_connection()
        row = conn.execute(
            "SELECT is_builtin FROM nc_device_profiles WHERE id=?",
            (pid,)
        ).fetchone()
        if row and row[0]:
            conn.close()
            return jsonify(
                {"error": "Cannot delete built-in profile"}), 403
        conn.execute(
            "DELETE FROM nc_device_profiles WHERE id=?", (pid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Discovery Configs ─────────────────────────────────────────────────
    @bp.route("/api/discovery-configs", methods=["GET"])
    @nc_login_required
    def nc_api_list_discovery_configs():
        conn = get_connection()
        rows = conn.execute(
            "SELECT dc.*, dp.vendor, dp.platform "
            "FROM nc_discovery_configs dc "
            "LEFT JOIN nc_device_profiles dp ON dp.id=dc.profile_id "
            "ORDER BY dc.name"
        ).fetchall()
        conn.close()
        configs = []
        for r in rows:
            c = _row_to_dict(r)
            try:
                c["targets"] = json.loads(c.get("targets") or "[]")
            except Exception:
                c["targets"] = []
            configs.append(c)
        return jsonify(configs)

    @bp.route("/api/discovery-configs", methods=["POST"])
    @nc_login_required
    def nc_api_create_discovery_config():
        data = request.get_json(force=True, silent=True) or {}
        cid = str(_uuid.uuid4())
        targets = data.get("targets", [])
        if isinstance(targets, list):
            targets = json.dumps(targets)
        wl = data.get("whitelist_subnets", [])
        if isinstance(wl, list):
            wl = json.dumps(wl)
        bl = data.get("blacklist_subnets", [])
        if isinstance(bl, list):
            bl = json.dumps(bl)
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_discovery_configs "
            "(id, name, profile_id, targets, credential_ref, "
            " method, read_only, rate_limit_per_sec, "
            " max_concurrent, timeout_per_cmd, timeout_per_device, "
            " hop_limit, max_devices, whitelist_subnets, "
            " blacklist_subnets, created_at) "
            "VALUES (?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)",
            (cid, data.get("name", "Discovery Scan"),
             data.get("profile_id"),
             targets,
             data.get("credential_ref", ""),
             data.get("method", "ssh"),
             data.get("rate_limit_per_sec", 1.0),
             data.get("max_concurrent", 5),
             data.get("timeout_per_cmd", 10),
             data.get("timeout_per_device", 60),
             data.get("hop_limit", 2),
             data.get("max_devices", 100),
             wl, bl, _now())
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "discovery_config", cid)
        return jsonify({"id": cid}), 201

    @bp.route("/api/discovery-configs/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_discovery_config(cid):
        conn = get_connection()
        conn.execute(
            "DELETE FROM nc_discovery_configs WHERE id=?", (cid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Collected Configs ─────────────────────────────────────────────────
    @bp.route("/api/collected-configs", methods=["GET"])
    @nc_login_required
    def nc_api_list_collected_configs():
        device_ip = request.args.get("device_ip", "")
        conn = get_connection()
        if device_ip:
            rows = conn.execute(
                "SELECT id, device_ip, hostname, command_name, "
                "collected_at FROM nc_collected_configs "
                "WHERE device_ip=? ORDER BY collected_at DESC",
                (device_ip,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, device_ip, hostname, command_name, "
                "collected_at FROM nc_collected_configs "
                "ORDER BY collected_at DESC LIMIT 100"
            ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/collected-configs/<cid>", methods=["GET"])
    @nc_login_required
    def nc_api_get_collected_config(cid):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM nc_collected_configs WHERE id=?", (cid,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        c = _row_to_dict(row)
        try:
            c["parsed"] = json.loads(c.get("parsed_json") or "{}")
        except Exception:
            c["parsed"] = {}
        return jsonify(c)

    @bp.route("/api/collected-configs", methods=["POST"])
    @nc_login_required
    def nc_api_store_collected_config():
        """Store a manually captured config output (deduped by device+command)."""
        data = request.get_json(force=True, silent=True) or {}
        cid = str(_uuid.uuid4())
        parsed = data.get("parsed_json", {})
        if isinstance(parsed, dict):
            parsed = json.dumps(parsed)
        conn = get_connection()
        # Dedup: keep latest per device_ip + command_name
        conn.execute(
            "DELETE FROM nc_collected_configs "
            "WHERE device_ip=? AND command_name=?",
            (data.get("device_ip", ""),
             data.get("command_name", "manual"))
        )
        conn.execute(
            "INSERT INTO nc_collected_configs "
            "(id, device_ip, hostname, profile_id, command_name, "
            " output_text, parsed_json, collected_at, topology_id) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (cid, data.get("device_ip", ""),
             data.get("hostname", ""),
             data.get("profile_id"),
             data.get("command_name", "manual"),
             data.get("output_text", ""),
             parsed, _now(),
             data.get("topology_id"))
        )
        conn.commit()
        conn.close()
        return jsonify({"id": cid}), 201

    # ══════════════════════════════════════════════════════════════════════
    # Phase 4: Geolocation + Globe/Map View
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/device-geo", methods=["GET"])
    @nc_login_required
    def nc_api_list_device_geo():
        topo_id = request.args.get("topology_id", "")
        conn = get_connection()
        if topo_id:
            rows = conn.execute(
                "SELECT * FROM nc_device_geo WHERE topology_id=? "
                "ORDER BY site_name, label", (topo_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT dg.*, t.name AS topology_name "
                "FROM nc_device_geo dg "
                "LEFT JOIN topologies t ON t.id=dg.topology_id "
                "ORDER BY dg.site_name, dg.label"
            ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/device-geo", methods=["POST"])
    @nc_login_required
    def nc_api_set_device_geo():
        """Set geolocation for a device (deduped by topology+node)."""
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        # Dedup
        conn.execute(
            "DELETE FROM nc_device_geo "
            "WHERE topology_id=? AND node_id=?",
            (data.get("topology_id"), data.get("node_id"))
        )
        gid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_device_geo "
            "(id, topology_id, node_id, label, site_name, "
            " latitude, longitude, city, state, country, "
            " facility, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (gid, data.get("topology_id"),
             data.get("node_id"), data.get("label", ""),
             data.get("site_name", ""),
             data.get("latitude", 0), data.get("longitude", 0),
             data.get("city", ""), data.get("state", ""),
             data.get("country", "US"),
             data.get("facility", ""), _now())
        )
        conn.commit()
        conn.close()
        return jsonify({"id": gid}), 201

    @bp.route("/api/device-geo/bulk", methods=["POST"])
    @nc_login_required
    def nc_api_bulk_set_geo():
        """Set geolocation for multiple devices at once."""
        data = request.get_json(force=True, silent=True) or {}
        devices = data.get("devices", [])
        conn = get_connection()
        count = 0
        for d in devices:
            conn.execute(
                "DELETE FROM nc_device_geo "
                "WHERE topology_id=? AND node_id=?",
                (d.get("topology_id"), d.get("node_id"))
            )
            conn.execute(
                "INSERT INTO nc_device_geo "
                "(id, topology_id, node_id, label, site_name, "
                " latitude, longitude, city, state, country, "
                " facility, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(_uuid.uuid4()), d.get("topology_id"),
                 d.get("node_id"), d.get("label", ""),
                 d.get("site_name", ""),
                 d.get("latitude", 0), d.get("longitude", 0),
                 d.get("city", ""), d.get("state", ""),
                 d.get("country", "US"),
                 d.get("facility", ""), _now())
            )
            count += 1
        conn.commit()
        conn.close()
        return jsonify({"set": count}), 201

    @bp.route("/api/device-geo/<gid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_device_geo(gid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_device_geo WHERE id=?", (gid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/geo-sites", methods=["GET"])
    @nc_login_required
    def nc_api_geo_sites():
        """Aggregate devices by site for map clustering."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT site_name, latitude, longitude, city, state, "
            "country, facility, COUNT(*) AS device_count "
            "FROM nc_device_geo "
            "WHERE latitude != 0 AND longitude != 0 "
            "GROUP BY site_name, latitude, longitude "
            "ORDER BY site_name"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/geo-links", methods=["GET"])
    @nc_login_required
    def nc_api_geo_links():
        """Get interconnect links with geolocation for map arcs."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT ic.id, ic.circuit_id, ic.protocol, ic.bandwidth, "
            "sg.latitude AS src_lat, sg.longitude AS src_lon, "
            "sg.site_name AS src_site, "
            "dg.latitude AS dst_lat, dg.longitude AS dst_lon, "
            "dg.site_name AS dst_site "
            "FROM nc_interconnects ic "
            "LEFT JOIN nc_device_geo sg "
            "  ON sg.topology_id=ic.src_topology_id "
            "  AND sg.node_id=ic.src_node_id "
            "LEFT JOIN nc_device_geo dg "
            "  ON dg.topology_id=ic.dst_topology_id "
            "  AND dg.node_id=ic.dst_node_id "
            "WHERE sg.latitude IS NOT NULL "
            "  AND dg.latitude IS NOT NULL"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/map")
    @nc_login_required
    def nc_map_view():
        return render_template("network/map.html")

    # ══════════════════════════════════════════════════════════════════════
    # Phase 3: Routing Table Topology + Config-to-Canvas Sync
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/routing-entries", methods=["POST"])
    @nc_login_required
    def nc_api_store_routing_entries():
        """Store parsed routing table entries for a device."""
        data = request.get_json(force=True, silent=True) or {}
        device_ip = data.get("device_ip", "")
        hostname = data.get("hostname", "")
        entries = data.get("entries", [])
        topo_id = data.get("topology_id")
        if not entries:
            return jsonify({"error": "entries required"}), 400
        conn = get_connection()
        now = _now()
        # Dedup: remove existing entries for this device before inserting
        conn.execute(
            "DELETE FROM nc_routing_entries WHERE device_ip=?",
            (device_ip,)
        )
        count = 0
        for e in entries:
            conn.execute(
                "INSERT INTO nc_routing_entries "
                "(id, device_ip, hostname, prefix, next_hop, "
                " protocol, metric, admin_distance, interface, "
                " vrf, address_family, collected_at, topology_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(_uuid.uuid4()), device_ip, hostname,
                 e.get("prefix", ""), e.get("next_hop", ""),
                 e.get("protocol", ""), e.get("metric", 0),
                 e.get("admin_distance", 0),
                 e.get("interface", ""),
                 e.get("vrf", "default"),
                 e.get("address_family", "ipv4"),
                 now, topo_id)
            )
            count += 1
        conn.commit()
        conn.close()
        return jsonify({"stored": count, "device": device_ip}), 201

    @bp.route("/api/routing-entries", methods=["GET"])
    @nc_login_required
    def nc_api_list_routing_entries():
        device_ip = request.args.get("device_ip", "")
        conn = get_connection()
        if device_ip:
            rows = conn.execute(
                "SELECT * FROM nc_routing_entries "
                "WHERE device_ip=? ORDER BY prefix",
                (device_ip,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM nc_routing_entries "
                "ORDER BY device_ip, prefix LIMIT 500"
            ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/routing-topology", methods=["POST"])
    @nc_login_required
    def nc_api_generate_routing_topology():
        """Generate a topology from stored routing entries.
        Each unique device becomes a node; next-hop relationships
        become edges showing actual forwarding paths."""
        data = request.get_json(force=True, silent=True) or {}
        device_ips = data.get("device_ips", [])
        name = data.get("name", "Routing Topology")
        conn = get_connection()

        if device_ips:
            placeholders = ",".join("?" for _ in device_ips)
            rows = conn.execute(
                f"SELECT * FROM nc_routing_entries "  # nosec B608
                f"WHERE device_ip IN ({placeholders}) "
                f"ORDER BY device_ip, prefix", device_ips
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM nc_routing_entries "
                "ORDER BY device_ip, prefix"
            ).fetchall()

        # Build device set and next-hop relationships
        devices = {}  # ip -> {hostname, protocols, prefixes}
        links = {}  # (src_ip, dst_ip) -> {protocols, count}

        for r in rows:
            r = _row_to_dict(r)
            dip = r["device_ip"]
            if dip not in devices:
                devices[dip] = {
                    "hostname": r.get("hostname", dip),
                    "protocols": set(),
                    "prefix_count": 0,
                }
            devices[dip]["protocols"].add(r.get("protocol", ""))
            devices[dip]["prefix_count"] += 1

            nh = r.get("next_hop", "")
            if nh and nh != "0.0.0.0" and nh != "::" and nh != dip:
                key = (dip, nh)
                if key not in links:
                    links[key] = {"protocols": set(), "count": 0}
                links[key]["protocols"].add(r.get("protocol", ""))
                links[key]["count"] += 1
                # Ensure next-hop device exists
                if nh not in devices:
                    devices[nh] = {
                        "hostname": nh,
                        "protocols": set(),
                        "prefix_count": 0,
                    }

        # Generate graph JSON
        nodes = []
        x_pos = 0
        ip_to_id = {}
        for ip, info in sorted(devices.items()):
            nid = str(_uuid.uuid4())[:8]
            ip_to_id[ip] = nid
            nodes.append({
                "id": nid,
                "label": info["hostname"],
                "type": "router",
                "x": x_pos % 800,
                "y": (x_pos // 800) * 200,
                "config": {
                    "ip": ip,
                    "protocol": ", ".join(
                        sorted(info["protocols"] - {""})),
                },
            })
            x_pos += 200

        edges = []
        for (src, dst), info in links.items():
            src_id = ip_to_id.get(src)
            dst_id = ip_to_id.get(dst)
            if src_id and dst_id and src_id != dst_id:
                edges.append({
                    "id": str(_uuid.uuid4())[:8],
                    "source": src_id,
                    "target": dst_id,
                    "label": ", ".join(
                        sorted(info["protocols"] - {""})),
                    "protocol": ", ".join(
                        sorted(info["protocols"] - {""})),
                })

        graph = {"nodes": nodes, "edges": edges}
        graph = _classify_imported_nodes(graph)

        topo_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, "
            " classification, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (topo_id, name,
             f"Generated from {len(devices)} device routing tables",
             json.dumps(graph), "public", now, now)
        )
        conn.commit()
        conn.close()
        _audit("ROUTING_TOPO", "topology", topo_id,
               f"{len(nodes)} devices, {len(edges)} links")
        return jsonify({
            "id": topo_id, "name": name,
            "nodes": len(nodes), "edges": len(edges),
            "devices_discovered": len(devices),
        }), 201

    @bp.route("/api/config-to-canvas/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_config_to_canvas(topo_id):
        """Sync collected config data into canvas device properties.
        Matches by hostname or IP address."""
        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph"}), 500

        # Get all collected configs
        configs = conn.execute(
            "SELECT device_ip, hostname, command_name, parsed_json "
            "FROM nc_collected_configs "
            "WHERE topology_id=? OR topology_id IS NULL "
            "ORDER BY collected_at DESC", (topo_id,)
        ).fetchall()

        # Build lookup by hostname and IP
        config_by_host = {}  # hostname -> {cmd: parsed}
        config_by_ip = {}    # ip -> {cmd: parsed}
        for c in configs:
            c = _row_to_dict(c)
            host = (c.get("hostname") or "").lower()
            ip = c.get("device_ip", "")
            cmd = c.get("command_name", "")
            try:
                parsed = json.loads(c.get("parsed_json") or "{}")
            except Exception:
                parsed = {}
            if host:
                config_by_host.setdefault(host, {})[cmd] = parsed
            if ip:
                config_by_ip.setdefault(ip, {})[cmd] = parsed

        updated = 0
        for n in graph.get("nodes", []):
            cfg = n.get("config") or n.get("configData") or {}
            label = (n.get("label") or "").lower()
            ip = cfg.get("ip", "")

            # Match by hostname then IP
            device_data = config_by_host.get(label, {})
            if not device_data and ip:
                device_data = config_by_ip.get(ip, {})
            if not device_data:
                continue

            # Merge parsed data into config
            for cmd_name, parsed in device_data.items():
                if cmd_name == "version" and parsed:
                    if parsed.get("version"):
                        cfg["sw_version"] = parsed["version"]
                    if parsed.get("hostname"):
                        n["label"] = parsed["hostname"]
                    if parsed.get("model"):
                        cfg["model"] = parsed["model"]
                    if parsed.get("serial"):
                        cfg["serial"] = parsed["serial"]
                elif cmd_name == "interfaces" and parsed:
                    if parsed.get("mgmt_ip"):
                        cfg["ip"] = parsed["mgmt_ip"]
                elif cmd_name in ("routing_table_v4",
                                   "routing_table_v6") and parsed:
                    if parsed.get("protocol"):
                        cfg["protocol"] = parsed["protocol"]

            n["config"] = cfg
            if "configData" in n:
                n["configData"] = cfg
            updated += 1

        now = _now()
        conn.execute(
            "UPDATE topologies SET graph_json=?, updated_at=? "
            "WHERE id=?",
            (json.dumps(graph), now, topo_id)
        )
        conn.commit()
        conn.close()
        return jsonify({
            "updated_devices": updated,
            "total_nodes": len(graph.get("nodes", [])),
        })

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
        conn.execute(f"UPDATE nc_circuits SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
        conn.execute(f"UPDATE nc_customers SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
        conn.execute(f"UPDATE nc_sites SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
        conn.execute(f"UPDATE nc_ipam_blocks SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
        conn.execute(f"UPDATE nc_cables SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
    # API: Cable Plant Report Export
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/cable-plant-report/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_cable_plant_report(topo_id):
        """Export cable plant report as CSV from edge cableData annotations."""
        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json, name FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Topology not found"}), 404

        topo = _row_to_dict(row)
        gj = topo.get("graph_json")
        if isinstance(gj, str):
            gj = json.loads(gj)

        nodes_map = {}
        for n in (gj or {}).get("nodes", []):
            nodes_map[n.get("id", "")] = n.get("label", n.get("id", ""))

        import io
        import csv
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "Link ID", "Source Device", "Target Device",
            "Cable Type", "Distance (m)", "Conduit ID",
            "Fiber Strands", "Pull Tension (lbs)", "Notes",
        ])

        edges = (gj or {}).get("edges", [])
        cable_count = 0
        for e in edges:
            cable = e.get("cableData")
            if not cable:
                continue
            cable_count += 1
            writer.writerow([
                e.get("id", ""),
                nodes_map.get(e.get("source", ""), e.get("source", "")),
                nodes_map.get(e.get("target", ""), e.get("target", "")),
                cable.get("cable_type", ""),
                cable.get("distance_m", ""),
                cable.get("conduit_id", ""),
                cable.get("fiber_strands", ""),
                cable.get("pull_tension_lbs", ""),
                cable.get("notes", ""),
            ])

        topo_name = (topo.get("name") or "topology").replace(" ", "_")
        filename = f"cable-plant-{topo_name}.csv"
        _audit("EXPORT", "cable_plant_report", topo_id, f"{cable_count} cable runs")
        return jsonify({"csv": buf.getvalue(), "filename": filename, "cable_count": cable_count})

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
        conn.execute(f"UPDATE nc_cross_connects SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
            conn.execute(f"UPDATE nc_compliance_profiles SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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

        # P3: Gate-checked status transitions
        new_status = data.get("status")
        if new_status:
            old_row = conn.execute(
                "SELECT status FROM nc_projects WHERE id=?", (pid,)
            ).fetchone()
            old_status = old_row[0] if old_row else "draft"
            gate_result = _check_status_gate(
                conn, pid, old_status, new_status
            )
            if gate_result.get("blocked"):
                conn.close()
                return jsonify(gate_result), 422

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
        conn.execute(f"UPDATE nc_projects SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
    # P3: Milestones, Notes, Tags, Assignee, Search, Templates
    # ══════════════════════════════════════════════════════════════════════

    # ── Milestones ────────────────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/milestones", methods=["GET"])
    @nc_login_required
    def nc_api_list_milestones(pid):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM nc_project_milestones "
            "WHERE project_id=? ORDER BY due_date", (pid,)
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/projects/<pid>/milestones", methods=["POST"])
    @nc_login_required
    def nc_api_create_milestone(pid):
        data = request.get_json(force=True, silent=True) or {}
        mid = str(_uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_project_milestones "
            "(id, project_id, title, due_date, status, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (mid, pid, data.get("title", "Milestone"),
             data.get("due_date"), data.get("status", "pending"),
             data.get("notes", ""), _now())
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "milestone", mid, data.get("title", ""))
        return jsonify({"id": mid}), 201

    @bp.route("/api/milestones/<mid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_milestone(mid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = ["title", "due_date", "status", "notes"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if fields:
            values.append(mid)
            conn.execute(
                f"UPDATE nc_project_milestones "  # nosec B608
                f"SET {', '.join(fields)} WHERE id=?", values
            )
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/milestones/<mid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_milestone(mid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_project_milestones WHERE id=?", (mid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Notes / Comments ──────────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/notes", methods=["GET"])
    @nc_login_required
    def nc_api_list_notes(pid):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM nc_project_notes "
            "WHERE project_id=? ORDER BY created_at DESC", (pid,)
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/projects/<pid>/notes", methods=["POST"])
    @nc_login_required
    def nc_api_create_note(pid):
        data = request.get_json(force=True, silent=True) or {}
        nid = str(_uuid.uuid4())
        author = data.get("author", "")
        try:
            author = author or session.get("user_id", "")
        except RuntimeError:
            pass
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_project_notes "
            "(id, project_id, author, body, created_at) VALUES (?,?,?,?,?)",
            (nid, pid, author, data.get("body", ""), _now())
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "note", nid)
        return jsonify({"id": nid}), 201

    @bp.route("/api/notes/<nid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_note(nid):
        conn = get_connection()
        conn.execute("DELETE FROM nc_project_notes WHERE id=?", (nid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Tags ──────────────────────────────────────────────────────────────
    @bp.route("/api/tags/<entity_type>/<entity_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_tags(entity_type, entity_id):
        conn = get_connection()
        rows = conn.execute(
            "SELECT tag FROM nc_tags "
            "WHERE entity_type=? AND entity_id=? ORDER BY tag",
            (entity_type, entity_id)
        ).fetchall()
        conn.close()
        return jsonify([r[0] for r in rows])

    @bp.route("/api/tags/<entity_type>/<entity_id>", methods=["POST"])
    @nc_login_required
    def nc_api_add_tag(entity_type, entity_id):
        data = request.get_json(force=True, silent=True) or {}
        tag = (data.get("tag") or "").strip().lower()
        if not tag:
            return jsonify({"error": "tag required"}), 400
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO nc_tags "
                "(id, entity_type, entity_id, tag, created_at) "
                "VALUES (?,?,?,?,?)",
                (str(_uuid.uuid4()), entity_type, entity_id, tag, _now())
            )
            conn.commit()
        except Exception:
            pass
        conn.close()
        return jsonify({"ok": True}), 201

    @bp.route("/api/tags/<entity_type>/<entity_id>/<tag>",
              methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_tag(entity_type, entity_id, tag):
        conn = get_connection()
        conn.execute(
            "DELETE FROM nc_tags "
            "WHERE entity_type=? AND entity_id=? AND tag=?",
            (entity_type, entity_id, tag)
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Global Search ─────────────────────────────────────────────────────
    @bp.route("/api/search", methods=["GET"])
    @nc_login_required
    def nc_api_search():
        q = request.args.get("q", "").strip()
        if not q or len(q) < 2:
            return jsonify({"results": []})
        like = f"%{q}%"
        conn = get_connection()
        results = []
        # Projects
        for r in conn.execute(
            "SELECT id, name, 'project' AS type FROM nc_projects "
            "WHERE name LIKE ?", (like,)
        ).fetchall():
            results.append(_row_to_dict(r))
        # Topologies
        for r in conn.execute(
            "SELECT id, name, 'topology' AS type FROM topologies "
            "WHERE name LIKE ?", (like,)
        ).fetchall():
            results.append(_row_to_dict(r))
        # Circuits
        for r in conn.execute(
            "SELECT id, circuit_id AS name, 'circuit' AS type "
            "FROM nc_circuits WHERE circuit_id LIKE ? OR carrier LIKE ?",
            (like, like)
        ).fetchall():
            results.append(_row_to_dict(r))
        # Tags
        for r in conn.execute(
            "SELECT DISTINCT entity_type, entity_id, tag AS name "
            "FROM nc_tags WHERE tag LIKE ?", (like,)
        ).fetchall():
            results.append({
                "type": f"tag:{r[0]}", "id": r[1], "name": r[2]
            })
        conn.close()
        return jsonify({"results": results[:50], "query": q})

    # ── Per-topology assignee ─────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/topologies/<topo_id>/assignee",
              methods=["PUT"])
    @nc_login_required
    def nc_api_set_assignee(pid, topo_id):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        conn.execute(
            "UPDATE nc_project_topologies SET assignee=? "
            "WHERE project_id=? AND topology_id=?",
            (data.get("assignee", ""), pid, topo_id)
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Status gate check (dry run) ───────────────────────────────────────
    @bp.route("/api/projects/<pid>/gate-check", methods=["POST"])
    @nc_login_required
    def nc_api_gate_check(pid):
        data = request.get_json(force=True, silent=True) or {}
        target = data.get("target_status", "approved")
        conn = get_connection()
        old_row = conn.execute(
            "SELECT status FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        old_status = old_row[0] if old_row else "draft"
        result = _check_status_gate(conn, pid, old_status, target)
        conn.close()
        return jsonify(result)

    # ── Project Templates (save/load) ─────────────────────────────────────
    @bp.route("/api/project-templates", methods=["GET"])
    @nc_login_required
    def nc_api_list_project_templates():
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, description, created_by, created_at "
            "FROM nc_project_templates ORDER BY name"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/project-templates", methods=["POST"])
    @nc_login_required
    def nc_api_save_project_template():
        """Save current project structure as reusable template."""
        data = request.get_json(force=True, silent=True) or {}
        pid = data.get("project_id")
        tpl_name = data.get("name", "Untitled Template")
        if not pid:
            return jsonify({"error": "project_id required"}), 400
        conn = get_connection()
        proj = conn.execute(
            "SELECT * FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        if not proj:
            conn.close()
            return jsonify({"error": "Project not found"}), 404
        proj = _row_to_dict(proj)
        topos = []
        for r in conn.execute(
            "SELECT t.name, t.description, t.classification, "
            "t.graph_json FROM topologies t "
            "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            "WHERE pt.project_id=?", (pid,)
        ).fetchall():
            topos.append(_row_to_dict(r))
        structure = {
            "project_name": proj["name"],
            "description": proj.get("description", ""),
            "topologies": topos,
        }
        tid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_project_templates "
            "(id, name, description, structure_json, created_by, "
            " created_at) VALUES (?,?,?,?,?,?)",
            (tid, tpl_name, data.get("description", ""),
             json.dumps(structure), data.get("created_by", ""), _now())
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "project_template", tid, tpl_name)
        return jsonify({"id": tid}), 201

    @bp.route("/api/project-templates/<tid>/load", methods=["POST"])
    @nc_login_required
    def nc_api_load_project_template(tid):
        """Create a new project from a saved template."""
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        tpl = conn.execute(
            "SELECT * FROM nc_project_templates WHERE id=?", (tid,)
        ).fetchone()
        if not tpl:
            conn.close()
            return jsonify({"error": "Template not found"}), 404
        tpl = _row_to_dict(tpl)
        try:
            structure = json.loads(tpl["structure_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad template data"}), 500
        now = _now()
        new_pid = str(_uuid.uuid4())
        proj_name = data.get("name") or structure.get(
            "project_name", "From Template"
        )
        conn.execute(
            "INSERT INTO nc_projects "
            "(id, name, description, status, owner, "
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (new_pid, proj_name,
             structure.get("description", ""), "draft",
             data.get("owner", ""), now, now)
        )
        topo_count = 0
        for t in structure.get("topologies", []):
            new_tid = str(_uuid.uuid4())
            conn.execute(
                "INSERT INTO topologies "
                "(id, name, description, graph_json, classification, "
                " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (new_tid, t.get("name", "Untitled"),
                 t.get("description", ""),
                 t.get("graph_json", '{"nodes":[],"edges":[]}'),
                 t.get("classification", "public"), now, now)
            )
            conn.execute(
                "INSERT INTO nc_project_topologies "
                "(project_id, topology_id) VALUES (?,?)",
                (new_pid, new_tid)
            )
            topo_count += 1
        conn.commit()
        conn.close()
        _audit("CREATE", "project", new_pid,
               f"From template {tpl['name']}")
        return jsonify({
            "id": new_pid, "name": proj_name,
            "topologies_created": topo_count
        }), 201

    @bp.route("/api/project-templates/<tid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_project_template(tid):
        conn = get_connection()
        conn.execute(
            "DELETE FROM nc_project_templates WHERE id=?", (tid,)
        )
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ══════════════════════════════════════════════════════════════════════
    # Phase A: Review Board Pipeline + SAFe Bridge
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/review-boards", methods=["GET"])
    @nc_login_required
    def nc_api_list_review_boards():
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM nc_review_boards ORDER BY sort_order"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/projects/<pid>/reviews", methods=["GET"])
    @nc_login_required
    def nc_api_list_reviews(pid):
        conn = get_connection()
        rows = conn.execute(
            "SELECT br.*, rb.name AS board_name, rb.short_name "
            "FROM nc_board_reviews br "
            "JOIN nc_review_boards rb ON rb.id=br.board_id "
            "WHERE br.project_id=? ORDER BY rb.sort_order, br.phase",
            (pid,)
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/projects/<pid>/reviews", methods=["POST"])
    @nc_login_required
    def nc_api_create_review(pid):
        data = request.get_json(force=True, silent=True) or {}
        board_id = data.get("board_id")
        if not board_id:
            return jsonify({"error": "board_id required"}), 400
        conn = get_connection()
        rid = str(_uuid.uuid4())
        now = _now()

        # Auto-generate review package from project data
        package = _build_review_package(conn, pid)

        conn.execute(
            "INSERT INTO nc_board_reviews "
            "(id, project_id, board_id, phase, status, "
            " scheduled_date, reviewer_names, package_json, "
            " created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rid, pid, board_id,
             data.get("phase", 1), "pending",
             data.get("scheduled_date"),
             json.dumps(data.get("reviewers", [])),
             json.dumps(package), now, now)
        )
        conn.commit()
        # Notification
        board_row = conn.execute(
            "SELECT short_name FROM nc_review_boards WHERE id=?",
            (board_id,)
        ).fetchone()
        bname = board_row[0] if board_row else board_id
        _notify(conn, pid, "review_submitted",
                f"{bname} Review Submitted (Phase {data.get('phase', 1)})",
                f"Review package generated with {package.get('total_devices', 0)} devices")
        conn.commit()
        conn.close()
        _audit("CREATE", "board_review", rid,
               f"board={board_id} phase={data.get('phase', 1)}")
        return jsonify({"id": rid, "package": package}), 201

    @bp.route("/api/reviews/<rid>/decide", methods=["POST"])
    @nc_login_required
    def nc_api_review_decide(rid):
        data = request.get_json(force=True, silent=True) or {}
        decision = data.get("decision")
        if decision not in ("approved", "rejected", "deferred",
                            "conditional"):
            return jsonify({"error": "Invalid decision"}), 400
        conn = get_connection()
        now = _now()
        conn.execute(
            "UPDATE nc_board_reviews SET decision=?, "
            "decision_notes=?, conditions=?, status=?, "
            "presented_date=?, updated_at=? WHERE id=?",
            (decision, data.get("notes", ""),
             json.dumps(data.get("conditions", [])),
             decision, now, now, rid)
        )
        # Notification
        rev_row = conn.execute(
            "SELECT br.project_id, rb.short_name "
            "FROM nc_board_reviews br "
            "JOIN nc_review_boards rb ON rb.id=br.board_id "
            "WHERE br.id=?", (rid,)
        ).fetchone()
        if rev_row:
            _notify(conn, rev_row[0], "review_decided",
                    f"{rev_row[1]} Review: {decision.upper()}",
                    data.get("notes", ""))
        conn.commit()
        conn.close()
        _audit("REVIEW_DECISION", "board_review", rid,
               f"decision={decision}")
        return jsonify({"ok": True, "decision": decision})

    @bp.route("/api/projects/<pid>/pipeline", methods=["GET"])
    @nc_login_required
    def nc_api_project_pipeline(pid):
        """Full pipeline view: phases + board reviews + SAFe bridge."""
        conn = get_connection()
        boards = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_review_boards ORDER BY sort_order"
        ).fetchall()]
        reviews = [_row_to_dict(r) for r in conn.execute(
            "SELECT br.*, rb.name AS board_name, rb.short_name "
            "FROM nc_board_reviews br "
            "JOIN nc_review_boards rb ON rb.id=br.board_id "
            "WHERE br.project_id=? ORDER BY rb.sort_order, br.phase",
            (pid,)
        ).fetchall()]
        phases = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_project_phases WHERE project_id=? "
            "ORDER BY phase_num", (pid,)
        ).fetchall()]
        bridge = conn.execute(
            "SELECT * FROM nc_safe_bridge WHERE project_id=?", (pid,)
        ).fetchone()
        bridge = _row_to_dict(bridge) if bridge else None
        conn.close()
        return jsonify({
            "boards": boards, "reviews": reviews,
            "phases": phases, "bridge": bridge,
        })

    # ── ROI Calculator ────────────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/roi", methods=["PUT"])
    @nc_login_required
    def nc_api_update_roi(pid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        bridge = conn.execute(
            "SELECT id FROM nc_safe_bridge WHERE project_id=?", (pid,)
        ).fetchone()
        now = _now()
        capex = data.get("capex", 0)
        opex = data.get("opex_annual", 0)
        savings = data.get("savings_annual", 0)
        net_annual = savings - opex
        payback = round(capex / net_annual * 12) if net_annual > 0 else 0
        # Simple 5-year NPV at 7% discount
        npv = -capex
        for yr in range(1, 6):
            npv += net_annual / (1.07 ** yr)
        npv = round(npv)
        roi_json = json.dumps({
            "capex": capex, "opex_annual": opex,
            "savings_annual": savings,
            "payback_months": payback, "npv_5yr": npv,
        })
        if bridge:
            conn.execute(
                "UPDATE nc_safe_bridge SET roi_json=?, "
                "justification=?, alternatives=?, updated_at=? "
                "WHERE project_id=?",
                (roi_json, data.get("justification", ""),
                 data.get("alternatives", ""), now, pid)
            )
        else:
            conn.execute(
                "INSERT INTO nc_safe_bridge "
                "(id, project_id, roi_json, justification, "
                " alternatives, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(_uuid.uuid4()), pid, roi_json,
                 data.get("justification", ""),
                 data.get("alternatives", ""), now, now)
            )
        conn.commit()
        conn.close()
        return jsonify({"roi": json.loads(roi_json)})

    # ── Auto-generate review package ──────────────────────────────────────
    def _build_review_package(conn, pid):
        """Build a snapshot of project data for board review."""
        proj = conn.execute(
            "SELECT * FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        proj = _row_to_dict(proj) if proj else {}
        topo_ids = [r[0] for r in conn.execute(
            "SELECT topology_id FROM nc_project_topologies "
            "WHERE project_id=?", (pid,)
        ).fetchall()]
        # Compliance summary
        total_p = total_f = cat1 = 0
        for tid in topo_ids:
            row = conn.execute(
                "SELECT passed, failed FROM nc_compliance_checks "
                "WHERE topology_id=? ORDER BY ran_at DESC LIMIT 1",
                (tid,)
            ).fetchone()
            if row:
                total_p += row[0] or 0
                total_f += row[1] or 0
            c1 = conn.execute(
                "SELECT COUNT(*) FROM nc_compliance_findings "
                "WHERE topology_id=? AND status='open' "
                "AND severity='CAT1'", (tid,)
            ).fetchone()
            cat1 += c1[0] if c1 else 0
        total = total_p + total_f
        comp_pct = round(total_p * 100 / total) if total else None
        # BOM
        total_capex = 0
        total_devices = 0
        for tid in topo_ids:
            trow = conn.execute(
                "SELECT graph_json FROM topologies WHERE id=?", (tid,)
            ).fetchone()
            if trow:
                try:
                    g = json.loads(trow["graph_json"])
                except Exception:
                    g = {"nodes": []}
                for n in g.get("nodes", []):
                    total_capex += BOM_COSTS.get(
                        n.get("type", ""), 0)
                    total_devices += 1
        # Circuit cost
        cost_row = conn.execute(
            "SELECT COALESCE(SUM(monthly_cost_usd), 0) "
            "FROM nc_circuits WHERE topology_id IN "
            "(SELECT topology_id FROM nc_project_topologies "
            " WHERE project_id=?)", (pid,)
        ).fetchone()
        circuit_cost = cost_row[0] if cost_row else 0
        # ROI
        bridge = conn.execute(
            "SELECT roi_json, justification, alternatives "
            "FROM nc_safe_bridge WHERE project_id=?", (pid,)
        ).fetchone()
        roi = {}
        justification = ""
        alternatives = ""
        if bridge:
            try:
                roi = json.loads(bridge["roi_json"] or "{}")
            except Exception:
                pass
            justification = bridge["justification"] or ""
            alternatives = bridge["alternatives"] or ""
        return {
            "project_name": proj.get("name", ""),
            "status": proj.get("status", ""),
            "owner": proj.get("owner", ""),
            "topology_count": len(topo_ids),
            "total_devices": total_devices,
            "compliance_pct": comp_pct,
            "cat1_findings": cat1,
            "total_capex": total_capex,
            "monthly_circuit_cost": circuit_cost,
            "roi": roi,
            "justification": justification,
            "alternatives": alternatives,
            "generated_at": _now(),
        }

    # ── Initialize project phases ─────────────────────────────────────────
    @bp.route("/api/projects/<pid>/init-phases", methods=["POST"])
    @nc_login_required
    def nc_api_init_phases(pid):
        conn = get_connection()
        existing = conn.execute(
            "SELECT COUNT(*) FROM nc_project_phases WHERE project_id=?",
            (pid,)
        ).fetchone()[0]
        if existing:
            conn.close()
            return jsonify({"error": "Phases already initialized"}), 409
        now = _now()
        phase_defs = [
            (1, "Concept"), (2, "Design"),
            (3, "Approval"), (4, "Post-Deploy"),
        ]
        for num, name in phase_defs:
            conn.execute(
                "INSERT INTO nc_project_phases "
                "(id, project_id, phase_num, phase_name, status, "
                " entered_at, created_at) VALUES (?,?,?,?,?,?,?)",
                (str(_uuid.uuid4()), pid, num, name,
                 "active" if num == 1 else "pending",
                 now if num == 1 else None, now)
            )
        conn.commit()
        conn.close()
        _audit("INIT_PHASES", "project", pid)
        return jsonify({"ok": True, "phases": 4}), 201

    # ══════════════════════════════════════════════════════════════════════
    # Phase B: Presentation Generator + Global Connectivity + Conflicts
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/projects/<pid>/presentation", methods=["GET"])
    @nc_login_required
    def nc_api_presentation(pid):
        """Auto-generate a structured business case / review presentation."""
        conn = get_connection()
        package = _build_review_package(conn, pid)
        proj = conn.execute(
            "SELECT * FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        proj = _row_to_dict(proj) if proj else {}
        # Topology details
        topo_rows = conn.execute(
            "SELECT t.id, t.name, t.classification, "
            "json_array_length(json_extract(t.graph_json,'$.nodes')) "
            "  AS node_count, "
            "json_array_length(json_extract(t.graph_json,'$.edges')) "
            "  AS edge_count "
            "FROM topologies t "
            "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            "WHERE pt.project_id=?", (pid,)
        ).fetchall()
        topos = [_row_to_dict(r) for r in topo_rows]
        # Milestones
        milestones = [_row_to_dict(r) for r in conn.execute(
            "SELECT title, due_date, status "
            "FROM nc_project_milestones WHERE project_id=? "
            "ORDER BY due_date", (pid,)
        ).fetchall()]
        # Review history
        reviews = [_row_to_dict(r) for r in conn.execute(
            "SELECT br.phase, br.status, br.decision, "
            "br.decision_notes, rb.short_name AS board "
            "FROM nc_board_reviews br "
            "JOIN nc_review_boards rb ON rb.id=br.board_id "
            "WHERE br.project_id=? ORDER BY rb.sort_order", (pid,)
        ).fetchall()]
        # Circuit details
        circuits = [_row_to_dict(r) for r in conn.execute(
            "SELECT circuit_id, carrier, circuit_type, bandwidth, "
            "monthly_cost_usd, install_status "
            "FROM nc_circuits WHERE topology_id IN "
            "(SELECT topology_id FROM nc_project_topologies "
            " WHERE project_id=?) ORDER BY circuit_id", (pid,)
        ).fetchall()]
        conn.close()
        presentation = {
            "title": proj.get("name", ""),
            "owner": proj.get("owner", ""),
            "status": proj.get("status", ""),
            "description": proj.get("description", ""),
            "executive_summary": package,
            "topologies": topos,
            "circuits": circuits,
            "milestones": milestones,
            "review_history": reviews,
            "generated_at": _now(),
        }
        return jsonify(presentation)

    @bp.route("/projects/<pid>/presentation")
    @nc_login_required
    def nc_project_presentation(pid):
        """Render presentation view page."""
        conn = get_connection()
        proj = conn.execute(
            "SELECT * FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        if not proj:
            conn.close()
            abort(404)
        conn.close()
        return render_template("network/presentation.html",
                               project=_row_to_dict(proj))

    # ── Global Connectivity (Interconnects) ───────────────────────────────
    @bp.route("/api/interconnects", methods=["GET"])
    @nc_login_required
    def nc_api_list_interconnects():
        conn = get_connection()
        rows = conn.execute(
            "SELECT ic.*, "
            "sp.name AS src_project_name, "
            "dp.name AS dst_project_name, "
            "st.name AS src_topology_name, "
            "dt.name AS dst_topology_name "
            "FROM nc_interconnects ic "
            "LEFT JOIN nc_projects sp ON sp.id=ic.src_project_id "
            "LEFT JOIN nc_projects dp ON dp.id=ic.dst_project_id "
            "LEFT JOIN topologies st ON st.id=ic.src_topology_id "
            "LEFT JOIN topologies dt ON dt.id=ic.dst_topology_id "
            "ORDER BY ic.created_at"
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/interconnects", methods=["POST"])
    @nc_login_required
    def nc_api_create_interconnect():
        data = request.get_json(force=True, silent=True) or {}
        iid = str(_uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_interconnects "
            "(id, src_project_id, src_topology_id, src_node_id, "
            " dst_project_id, dst_topology_id, dst_node_id, "
            " circuit_id, protocol, bandwidth, notes, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (iid, data.get("src_project_id"),
             data.get("src_topology_id"), data.get("src_node_id"),
             data.get("dst_project_id"),
             data.get("dst_topology_id"), data.get("dst_node_id"),
             data.get("circuit_id"), data.get("protocol"),
             data.get("bandwidth"), data.get("notes"), _now())
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "interconnect", iid)
        return jsonify({"id": iid}), 201

    @bp.route("/api/interconnects/<iid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_interconnect(iid):
        conn = get_connection()
        conn.execute(
            "DELETE FROM nc_interconnects WHERE id=?", (iid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/global-topology", methods=["GET"])
    @nc_login_required
    def nc_api_global_topology():
        """Composite topology: all deployed/approved project topologies
        + interconnect links."""
        conn = get_connection()
        # Get all approved/deployed project topologies
        rows = conn.execute(
            "SELECT t.id, t.name, t.graph_json, p.id AS project_id, "
            "p.name AS project_name, p.status AS project_status "
            "FROM topologies t "
            "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            "JOIN nc_projects p ON p.id=pt.project_id "
            "WHERE p.status IN ('approved','deployed') "
            "ORDER BY p.name, t.name"
        ).fetchall()
        projects = {}
        for r in rows:
            r = _row_to_dict(r)
            pid = r["project_id"]
            if pid not in projects:
                projects[pid] = {
                    "id": pid,
                    "name": r["project_name"],
                    "status": r["project_status"],
                    "topologies": [],
                }
            try:
                graph = json.loads(r.get("graph_json") or "{}")
            except Exception:
                graph = {"nodes": [], "edges": []}
            projects[pid]["topologies"].append({
                "id": r["id"], "name": r["name"],
                "node_count": len(graph.get("nodes", [])),
                "edge_count": len(graph.get("edges", [])),
            })
        # Interconnects
        interconnects = [_row_to_dict(r) for r in conn.execute(
            "SELECT ic.*, "
            "sp.name AS src_project_name, "
            "dp.name AS dst_project_name "
            "FROM nc_interconnects ic "
            "LEFT JOIN nc_projects sp ON sp.id=ic.src_project_id "
            "LEFT JOIN nc_projects dp ON dp.id=ic.dst_project_id"
        ).fetchall()]
        conn.close()
        return jsonify({
            "projects": list(projects.values()),
            "interconnects": interconnects,
            "total_projects": len(projects),
            "total_interconnects": len(interconnects),
        })

    # ── Conflict Detection ────────────────────────────────────────────────
    @bp.route("/api/conflicts", methods=["GET"])
    @nc_login_required
    def nc_api_detect_conflicts():
        """Detect IPAM overlaps, duplicate circuits, and protocol
        mismatches across all projects."""
        conn = get_connection()
        conflicts = []
        # 1. Duplicate IPAM blocks across projects
        ipam_rows = conn.execute(
            "SELECT ib.network, ib.vrf, ib.topology_id, "
            "p.id AS project_id, p.name AS project_name "
            "FROM nc_ipam_blocks ib "
            "JOIN nc_project_topologies pt "
            "  ON pt.topology_id=ib.topology_id "
            "JOIN nc_projects p ON p.id=pt.project_id "
            "ORDER BY ib.network"
        ).fetchall()
        seen_nets = {}
        for r in ipam_rows:
            key = f"{r[0]}|{r[1]}"  # network|vrf
            if key in seen_nets:
                prev = seen_nets[key]
                if prev["project_id"] != r[3]:
                    conflicts.append({
                        "type": "ipam_overlap",
                        "severity": "high",
                        "detail": f"Network {r[0]} (VRF: {r[1]}) "
                                  f"used in both '{prev['project_name']}' "
                                  f"and '{r[4]}'",
                        "entity_a": prev["project_name"],
                        "entity_b": r[4],
                    })
            else:
                seen_nets[key] = {
                    "project_id": r[3], "project_name": r[4]
                }
        # 2. Duplicate circuit IDs across projects
        circ_rows = conn.execute(
            "SELECT c.circuit_id, c.topology_id, "
            "p.id AS project_id, p.name AS project_name "
            "FROM nc_circuits c "
            "JOIN nc_project_topologies pt "
            "  ON pt.topology_id=c.topology_id "
            "JOIN nc_projects p ON p.id=pt.project_id "
            "ORDER BY c.circuit_id"
        ).fetchall()
        seen_circs = {}
        for r in circ_rows:
            cid = r[0]
            if cid in seen_circs:
                prev = seen_circs[cid]
                if prev["project_id"] != r[2]:
                    conflicts.append({
                        "type": "circuit_duplicate",
                        "severity": "medium",
                        "detail": f"Circuit '{cid}' appears in both "
                                  f"'{prev['project_name']}' and '{r[3]}'",
                        "entity_a": prev["project_name"],
                        "entity_b": r[3],
                    })
            else:
                seen_circs[cid] = {
                    "project_id": r[2], "project_name": r[3]
                }
        conn.close()
        return jsonify({
            "conflicts": conflicts,
            "total": len(conflicts),
            "checked": {
                "ipam_blocks": len(ipam_rows),
                "circuits": len(circ_rows),
            },
        })

    # ── Global Connectivity Page ──────────────────────────────────────────
    @bp.route("/global")
    @nc_login_required
    def nc_global_connectivity():
        return render_template("network/global.html")

    # ══════════════════════════════════════════════════════════════════════
    # Phase C: Impact Analysis + Enterprise Summary
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/projects/<pid>/impact", methods=["GET"])
    @nc_login_required
    def nc_api_impact_analysis(pid):
        """Analyze which projects are affected if this project changes,
        via interconnect graph traversal."""
        conn = get_connection()
        proj = conn.execute(
            "SELECT id, name, status FROM nc_projects WHERE id=?",
            (pid,)
        ).fetchone()
        if not proj:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        proj = _row_to_dict(proj)

        # Build interconnect graph (adjacency list)
        all_ics = conn.execute(
            "SELECT ic.*, "
            "sp.name AS src_name, dp.name AS dst_name "
            "FROM nc_interconnects ic "
            "LEFT JOIN nc_projects sp ON sp.id=ic.src_project_id "
            "LEFT JOIN nc_projects dp ON dp.id=ic.dst_project_id"
        ).fetchall()

        adj = {}  # project_id -> [{peer_id, circuit, protocol, bw}]
        for ic in all_ics:
            ic = _row_to_dict(ic)
            src = ic.get("src_project_id")
            dst = ic.get("dst_project_id")
            if src and dst:
                adj.setdefault(src, []).append({
                    "peer_id": dst,
                    "peer_name": ic.get("dst_name", ""),
                    "circuit_id": ic.get("circuit_id", ""),
                    "protocol": ic.get("protocol", ""),
                    "bandwidth": ic.get("bandwidth", ""),
                    "direction": "outbound",
                })
                adj.setdefault(dst, []).append({
                    "peer_id": src,
                    "peer_name": ic.get("src_name", ""),
                    "circuit_id": ic.get("circuit_id", ""),
                    "protocol": ic.get("protocol", ""),
                    "bandwidth": ic.get("bandwidth", ""),
                    "direction": "inbound",
                })

        # BFS from this project to find all affected
        visited = {pid}
        queue = [pid]
        affected = []
        depth_map = {pid: 0}
        while queue:
            current = queue.pop(0)
            for link in adj.get(current, []):
                peer = link["peer_id"]
                if peer not in visited:
                    visited.add(peer)
                    queue.append(peer)
                    depth_map[peer] = depth_map[current] + 1
                    affected.append({
                        "project_id": peer,
                        "project_name": link["peer_name"],
                        "via_circuit": link["circuit_id"],
                        "protocol": link["protocol"],
                        "bandwidth": link["bandwidth"],
                        "hop_distance": depth_map[peer],
                        "impact": "direct" if depth_map[peer] == 1
                                  else "indirect",
                    })

        # Shared resources (IPAM/circuits that overlap)
        topo_ids = [r[0] for r in conn.execute(
            "SELECT topology_id FROM nc_project_topologies "
            "WHERE project_id=?", (pid,)
        ).fetchall()]
        shared = []
        if topo_ids:
            placeholders = ",".join("?" for _ in topo_ids)
            # IPAM blocks used by this project
            my_nets = [r[0] for r in conn.execute(
                f"SELECT DISTINCT network FROM nc_ipam_blocks "  # nosec B608
                f"WHERE topology_id IN ({placeholders})",
                topo_ids
            ).fetchall()]
            for net in my_nets:
                others = conn.execute(
                    "SELECT DISTINCT p.id, p.name "
                    "FROM nc_ipam_blocks ib "
                    "JOIN nc_project_topologies pt "
                    "  ON pt.topology_id=ib.topology_id "
                    "JOIN nc_projects p ON p.id=pt.project_id "
                    "WHERE ib.network=? AND p.id!=?",
                    (net, pid)
                ).fetchall()
                for o in others:
                    shared.append({
                        "type": "ipam",
                        "resource": net,
                        "project_id": o[0],
                        "project_name": o[1],
                    })

        conn.close()
        return jsonify({
            "project": proj,
            "affected_projects": affected,
            "total_affected": len(affected),
            "direct": sum(1 for a in affected if a["impact"] == "direct"),
            "indirect": sum(
                1 for a in affected if a["impact"] == "indirect"
            ),
            "shared_resources": shared,
            "interconnect_count": len(adj.get(pid, [])),
        })

    @bp.route("/api/enterprise-summary", methods=["GET"])
    @nc_login_required
    def nc_api_enterprise_summary():
        """Aggregate metrics across all projects for executive view."""
        conn = get_connection()
        projects = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, name, status, owner, updated_at "
            "FROM nc_projects ORDER BY updated_at DESC"
        ).fetchall()]

        total_topos = 0
        total_devices = 0
        total_capex = 0
        total_circuit_cost = 0
        total_findings = 0
        total_cat1 = 0
        comp_passed = comp_failed = 0
        status_counts = {}
        board_counts = {"pending": 0, "approved": 0, "rejected": 0}

        for p in projects:
            pid = p["id"]
            s = p.get("status", "draft")
            status_counts[s] = status_counts.get(s, 0) + 1

            topo_ids = [r[0] for r in conn.execute(
                "SELECT topology_id FROM nc_project_topologies "
                "WHERE project_id=?", (pid,)
            ).fetchall()]
            total_topos += len(topo_ids)

            for tid in topo_ids:
                row = conn.execute(
                    "SELECT passed, failed FROM nc_compliance_checks "
                    "WHERE topology_id=? ORDER BY ran_at DESC LIMIT 1",
                    (tid,)
                ).fetchone()
                if row:
                    comp_passed += row[0] or 0
                    comp_failed += row[1] or 0
                c1 = conn.execute(
                    "SELECT COUNT(*) FROM nc_compliance_findings "
                    "WHERE topology_id=? AND status='open' "
                    "AND severity='CAT1'", (tid,)
                ).fetchone()
                total_cat1 += c1[0] if c1 else 0
                of = conn.execute(
                    "SELECT COUNT(*) FROM nc_compliance_findings "
                    "WHERE topology_id=? AND status='open'", (tid,)
                ).fetchone()
                total_findings += of[0] if of else 0
                trow = conn.execute(
                    "SELECT graph_json FROM topologies WHERE id=?",
                    (tid,)
                ).fetchone()
                if trow:
                    try:
                        g = json.loads(trow["graph_json"])
                    except Exception:
                        g = {"nodes": []}
                    for n in g.get("nodes", []):
                        total_capex += BOM_COSTS.get(
                            n.get("type", ""), 0)
                        total_devices += 1

            cost_row = conn.execute(
                "SELECT COALESCE(SUM(monthly_cost_usd), 0) "
                "FROM nc_circuits WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies "
                " WHERE project_id=?)", (pid,)
            ).fetchone()
            total_circuit_cost += cost_row[0] if cost_row else 0

        # Board review stats
        for r in conn.execute(
            "SELECT status, COUNT(*) FROM nc_board_reviews "
            "GROUP BY status"
        ).fetchall():
            board_counts[r[0]] = r[1]

        ic_count = conn.execute(
            "SELECT COUNT(*) FROM nc_interconnects"
        ).fetchone()[0]

        comp_total = comp_passed + comp_failed
        comp_pct = round(
            comp_passed * 100 / comp_total
        ) if comp_total else None

        conn.close()
        return jsonify({
            "total_projects": len(projects),
            "status_counts": status_counts,
            "total_topologies": total_topos,
            "total_devices": total_devices,
            "total_capex": total_capex,
            "total_circuit_cost_monthly": total_circuit_cost,
            "compliance_pct": comp_pct,
            "total_open_findings": total_findings,
            "total_cat1": total_cat1,
            "board_reviews": board_counts,
            "total_interconnects": ic_count,
        })

    @bp.route("/enterprise")
    @nc_login_required
    def nc_enterprise_dashboard():
        return render_template("network/enterprise.html")

    # ══════════════════════════════════════════════════════════════════════
    # Network Design Rulebook (Phase 1)
    # ══════════════════════════════════════════════════════════════════════

    _design_rules = {}
    try:
        import yaml as _yaml
        _rules_path = _ICDEV_ROOT / "args" / "network_design_rules.yaml"
        if _rules_path.exists():
            with open(_rules_path, encoding="utf-8") as _rf:
                _design_rules = _yaml.safe_load(_rf) or {}
    except Exception:
        pass

    @bp.route("/api/design-rules", methods=["GET"])
    @nc_login_required
    def nc_api_design_rules():
        return jsonify(_design_rules)

    @bp.route("/api/design-rules/node/<node_type>", methods=["GET"])
    @nc_login_required
    def nc_api_design_rules_for_type(node_type):
        rules = _design_rules.get("on_node_add", {}).get(node_type)
        if not rules:
            return jsonify({"type": node_type, "found": False})
        return jsonify({"type": node_type, "found": True, **rules})

    @bp.route("/api/design-suggest", methods=["POST"])
    @nc_login_required
    def nc_api_design_suggest():
        """Context-aware suggestions for a dropped node type."""
        data = request.get_json(force=True, silent=True) or {}
        node_type = data.get("node_type", "")
        existing_nodes = data.get("existing_nodes", [])
        node_x = data.get("x", 0)
        node_y = data.get("y", 0)

        rules = _design_rules.get("on_node_add", {}).get(node_type, {})
        if not rules:
            return jsonify({
                "type": node_type, "suggestions": [],
                "checklist": {}, "connection_suggestions": [],
                "warnings": [],
            })

        suggestions = rules.get("suggestions", [])
        checklist = rules.get("checklist", {})

        connection_suggestions = []
        for rule in rules.get("auto_suggest_connections", []):
            match_type = rule.get("match_type", "")
            max_dist = rule.get("max_distance", 500)
            for en in existing_nodes:
                if en.get("type") == match_type:
                    dx = abs((en.get("x") or 0) - node_x)
                    dy = abs((en.get("y") or 0) - node_y)
                    dist = (dx ** 2 + dy ** 2) ** 0.5
                    if dist <= max_dist:
                        connection_suggestions.append({
                            "target_id": en.get("id"),
                            "target_label": en.get("label", ""),
                            "target_type": en.get("type", ""),
                            "distance": round(dist),
                            "message": rule.get("message", ""),
                        })

        warnings = []
        for w in rules.get("warnings", []):
            cond = w.get("condition", "")
            if cond == "single_uplink":
                warnings.append({
                    "severity": w.get("severity", "medium"),
                    "message": w.get("message", ""),
                })
            elif cond == "no_ha_peer":
                has_peer = any(
                    n.get("type") == node_type for n in existing_nodes
                )
                if not has_peer:
                    warnings.append({
                        "severity": w.get("severity", "medium"),
                        "message": w.get("message", ""),
                    })
            elif cond == "no_router_connection":
                has_router = any(
                    n.get("type") in ("router", "switch-l3")
                    for n in existing_nodes
                )
                if not has_router:
                    warnings.append({
                        "severity": w.get("severity", "medium"),
                        "message": w.get("message", ""),
                    })

        return jsonify({
            "type": node_type, "suggestions": suggestions,
            "checklist": checklist,
            "connection_suggestions": connection_suggestions,
            "warnings": warnings,
        })

    @bp.route("/api/design-rules/best-practices", methods=["GET"])
    @nc_login_required
    def nc_api_best_practices():
        return jsonify(_design_rules.get("best_practices", {}))

    # ══════════════════════════════════════════════════════════════════════
    # ARB/ERB Documentation APIs
    # ══════════════════════════════════════════════════════════════════════

    _PROB_SCORE = {"low": 1, "medium": 2, "high": 3}
    _IMPACT_SCORE = {"low": 1, "medium": 2, "high": 3, "critical": 4}

    def _crud_list(table, pid, order="created_at"):
        conn = get_connection()
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE project_id=? "  # nosec B608
            f"ORDER BY {order}", (pid,)
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    def _crud_create(table, pid, data, fields):
        rid = str(_uuid.uuid4())
        conn = get_connection()
        cols = ["id", "project_id"] + fields
        vals = [rid, pid] + [data.get(f, "") for f in fields]
        placeholders = ",".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO {table} ({','.join(cols)}) "  # nosec B608
            f"VALUES ({placeholders})", vals
        )
        conn.commit()
        conn.close()
        return jsonify({"id": rid}), 201

    def _crud_delete(table, rid):
        conn = get_connection()
        conn.execute(f"DELETE FROM {table} WHERE id=?", (rid,))  # nosec B608
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Alternatives Analysis ─────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/alternatives", methods=["GET"])
    @nc_login_required
    def nc_api_list_alternatives(pid):
        conn = get_connection()
        criteria = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_alt_criteria WHERE project_id=? "
            "ORDER BY sort_order", (pid,)
        ).fetchall()]
        options = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_alternatives WHERE project_id=? "
            "ORDER BY total_score DESC", (pid,)
        ).fetchall()]
        for o in options:
            try:
                o["scores"] = json.loads(o.get("scores_json") or "{}")
            except Exception:
                o["scores"] = {}
        conn.close()
        return jsonify({"criteria": criteria, "options": options})

    @bp.route("/api/projects/<pid>/alternatives/criteria",
              methods=["POST"])
    @nc_login_required
    def nc_api_add_criterion(pid):
        data = request.get_json(force=True, silent=True) or {}
        return _crud_create("nc_alt_criteria", pid, data,
                            ["name", "weight_pct", "sort_order"])

    @bp.route("/api/projects/<pid>/alternatives/options",
              methods=["POST"])
    @nc_login_required
    def nc_api_add_alternative(pid):
        data = request.get_json(force=True, silent=True) or {}
        scores = data.get("scores", {})
        # Compute weighted total
        conn = get_connection()
        criteria = {r[0]: r[1] for r in conn.execute(
            "SELECT name, weight_pct FROM nc_alt_criteria "
            "WHERE project_id=?", (pid,)
        ).fetchall()}
        conn.close()
        total = 0
        for cname, weight in criteria.items():
            s = scores.get(cname, {})
            total += (s.get("score", 0) * weight / 100)
        rid = str(_uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_alternatives "
            "(id, project_id, option_name, description, "
            " is_recommended, scores_json, total_score, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rid, pid, data.get("option_name", ""),
             data.get("description", ""),
             1 if data.get("is_recommended") else 0,
             json.dumps(scores), round(total, 2), _now())
        )
        conn.commit()
        conn.close()
        return jsonify({"id": rid, "total_score": round(total, 2)}), 201

    @bp.route("/api/alternatives/<aid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_alternative(aid):
        return _crud_delete("nc_alternatives", aid)

    @bp.route("/api/alt-criteria/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_criterion(cid):
        return _crud_delete("nc_alt_criteria", cid)

    # ── Risk Register ─────────────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/risks", methods=["GET"])
    @nc_login_required
    def nc_api_list_risks(pid):
        return _crud_list("nc_risks", pid, "risk_score DESC")

    @bp.route("/api/projects/<pid>/risks", methods=["POST"])
    @nc_login_required
    def nc_api_create_risk(pid):
        data = request.get_json(force=True, silent=True) or {}
        prob = _PROB_SCORE.get(data.get("probability", "medium"), 2)
        imp = _IMPACT_SCORE.get(data.get("impact", "medium"), 2)
        data["risk_score"] = str(prob * imp)
        return _crud_create("nc_risks", pid, data,
                            ["title", "category", "probability", "impact",
                             "risk_score", "mitigation", "owner", "status"])

    @bp.route("/api/risks/<rid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_risk(rid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        allowed = ["title", "category", "probability", "impact",
                   "mitigation", "owner", "status"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if "probability" in data or "impact" in data:
            row = conn.execute(
                "SELECT probability, impact FROM nc_risks WHERE id=?",
                (rid,)
            ).fetchone()
            p = data.get("probability", row[0] if row else "medium")
            i = data.get("impact", row[1] if row else "medium")
            score = _PROB_SCORE.get(p, 2) * _IMPACT_SCORE.get(i, 2)
            fields.append("risk_score=?")
            values.append(score)
        if fields:
            values.append(rid)
            conn.execute(
                f"UPDATE nc_risks SET {', '.join(fields)} "  # nosec B608
                f"WHERE id=?", values
            )
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/risks/<rid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_risk(rid):
        return _crud_delete("nc_risks", rid)

    # ── Enhanced BOM ──────────────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/bom-items", methods=["GET"])
    @nc_login_required
    def nc_api_list_bom_items(pid):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM nc_bom_items WHERE project_id=? "
            "ORDER BY category, vendor, model", (pid,)
        ).fetchall()
        items = [_row_to_dict(r) for r in rows]
        totals = {
            "hardware": 0, "software": 0, "circuit": 0,
            "labor": 0, "other": 0, "annual_maint": 0,
            "license": 0, "grand_total": 0,
        }
        for it in items:
            cat = it.get("category", "other")
            ext = it.get("extended_cost", 0) or 0
            totals[cat] = totals.get(cat, 0) + ext
            totals["grand_total"] += ext
            totals["annual_maint"] += it.get("annual_maint", 0) or 0
            totals["license"] += it.get("license_cost", 0) or 0
        conn.close()
        return jsonify({"items": items, "totals": totals})

    @bp.route("/api/projects/<pid>/bom-items", methods=["POST"])
    @nc_login_required
    def nc_api_create_bom_item(pid):
        data = request.get_json(force=True, silent=True) or {}
        qty = int(data.get("quantity", 1) or 1)
        unit = float(data.get("unit_cost", 0) or 0)
        data["extended_cost"] = str(round(qty * unit, 2))
        data["quantity"] = str(qty)
        data["unit_cost"] = str(unit)
        data["annual_maint"] = str(data.get("annual_maint", 0) or 0)
        data["license_cost"] = str(data.get("license_cost", 0) or 0)
        data["lead_time_days"] = str(data.get("lead_time_days", 0) or 0)
        return _crud_create("nc_bom_items", pid, data,
                            ["category", "vendor", "model", "part_number",
                             "description", "quantity", "unit_cost",
                             "extended_cost", "annual_maint",
                             "license_cost", "lead_time_days",
                             "contract_vehicle", "notes"])

    @bp.route("/api/bom-items/<bid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_bom_item(bid):
        return _crud_delete("nc_bom_items", bid)

    # ── Lab Test Results ──────────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/lab-tests", methods=["GET"])
    @nc_login_required
    def nc_api_list_lab_tests(pid):
        return _crud_list("nc_lab_tests", pid)

    @bp.route("/api/projects/<pid>/lab-tests", methods=["POST"])
    @nc_login_required
    def nc_api_create_lab_test(pid):
        data = request.get_json(force=True, silent=True) or {}
        m = data.get("measurements", {})
        if isinstance(m, dict):
            data["measurements"] = json.dumps(m)
        fv = data.get("firmware_versions", {})
        if isinstance(fv, dict):
            data["firmware_versions"] = json.dumps(fv)
        return _crud_create("nc_lab_tests", pid, data,
                            ["test_name", "category", "methodology",
                             "result", "measurements",
                             "firmware_versions", "notes",
                             "tested_by", "tested_at"])

    @bp.route("/api/lab-tests/<tid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_lab_test(tid):
        return _crud_delete("nc_lab_tests", tid)

    # ── Migration/Cutover Plan ────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/migration-phases", methods=["GET"])
    @nc_login_required
    def nc_api_list_migration_phases(pid):
        return _crud_list("nc_migration_phases", pid, "phase_num")

    @bp.route("/api/projects/<pid>/migration-phases", methods=["POST"])
    @nc_login_required
    def nc_api_create_migration_phase(pid):
        data = request.get_json(force=True, silent=True) or {}
        deps = data.get("dependencies", [])
        if isinstance(deps, list):
            data["dependencies"] = json.dumps(deps)
        data["duration_days"] = str(data.get("duration_days", 0) or 0)
        data["parallel_run"] = str(
            1 if data.get("parallel_run") else 0)
        return _crud_create("nc_migration_phases", pid, data,
                            ["phase_num", "title", "description",
                             "duration_days", "parallel_run",
                             "rollback_criteria", "maintenance_window",
                             "dependencies", "status"])

    @bp.route("/api/migration-phases/<mid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_migration_phase(mid):
        return _crud_delete("nc_migration_phases", mid)

    # ── Capacity Growth Projections ───────────────────────────────────────
    @bp.route("/api/projects/<pid>/capacity-projections",
              methods=["GET"])
    @nc_login_required
    def nc_api_list_capacity_projections(pid):
        return _crud_list("nc_capacity_projections", pid)

    @bp.route("/api/projects/<pid>/capacity-projections",
              methods=["POST"])
    @nc_login_required
    def nc_api_create_capacity_projection(pid):
        data = request.get_json(force=True, silent=True) or {}
        cur = float(data.get("current_value", 0) or 0)
        rate = float(data.get("growth_rate_pct", 20) or 20)
        data["current_value"] = str(cur)
        data["year1_value"] = str(round(cur * (1 + rate / 100), 2))
        data["year3_value"] = str(
            round(cur * (1 + rate / 100) ** 3, 2))
        data["year5_value"] = str(
            round(cur * (1 + rate / 100) ** 5, 2))
        data["growth_rate_pct"] = str(rate)
        data["threshold_pct"] = str(
            data.get("threshold_pct", 80) or 80)
        return _crud_create("nc_capacity_projections", pid, data,
                            ["metric_name", "current_value",
                             "year1_value", "year3_value",
                             "year5_value", "growth_rate_pct",
                             "threshold_pct", "notes"])

    @bp.route("/api/capacity-projections/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_capacity_projection(cid):
        return _crud_delete("nc_capacity_projections", cid)

    # ── Standards Alignment ───────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/standards-checks", methods=["GET"])
    @nc_login_required
    def nc_api_list_standards_checks(pid):
        return _crud_list("nc_standards_checks", pid)

    @bp.route("/api/projects/<pid>/standards-checks", methods=["POST"])
    @nc_login_required
    def nc_api_create_standards_check(pid):
        data = request.get_json(force=True, silent=True) or {}
        return _crud_create("nc_standards_checks", pid, data,
                            ["standard", "check_item", "status",
                             "deviation_reason", "waiver_ref"])

    @bp.route("/api/standards-checks/<sid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_standards_check(sid):
        return _crud_delete("nc_standards_checks", sid)

    # ── Resource Plan ─────────────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/resource-plan", methods=["GET"])
    @nc_login_required
    def nc_api_list_resource_plan(pid):
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM nc_resource_plan WHERE project_id=? "
            "ORDER BY phase, role", (pid,)
        ).fetchall()
        items = [_row_to_dict(r) for r in rows]
        total_hours = sum(it.get("hours", 0) or 0 for it in items)
        total_cost = sum(
            (it.get("hours", 0) or 0) * (it.get("rate_per_hour", 0) or 0)
            for it in items
        )
        conn.close()
        return jsonify({
            "resources": items,
            "total_hours": total_hours,
            "total_labor_cost": round(total_cost, 2),
        })

    @bp.route("/api/projects/<pid>/resource-plan", methods=["POST"])
    @nc_login_required
    def nc_api_create_resource(pid):
        data = request.get_json(force=True, silent=True) or {}
        data["hours"] = str(data.get("hours", 0) or 0)
        data["rate_per_hour"] = str(
            data.get("rate_per_hour", 0) or 0)
        data["is_contractor"] = str(
            1 if data.get("is_contractor") else 0)
        return _crud_create("nc_resource_plan", pid, data,
                            ["phase", "role", "name", "hours",
                             "rate_per_hour", "is_contractor",
                             "skill_requirements", "notes"])

    @bp.route("/api/resource-plan/<rid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_resource(rid):
        return _crud_delete("nc_resource_plan", rid)

    # ── Business Case Document Generator ──────────────────────────────────
    @bp.route("/api/projects/<pid>/business-case", methods=["GET"])
    @nc_login_required
    def nc_api_business_case(pid):
        """Generate complete business case pulling all ARB/ERB data."""
        conn = get_connection()
        proj = conn.execute(
            "SELECT * FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        if not proj:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        proj = _row_to_dict(proj)
        package = _build_review_package(conn, pid)

        # Alternatives
        alt_criteria = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_alt_criteria WHERE project_id=? "
            "ORDER BY sort_order", (pid,)
        ).fetchall()]
        alt_options = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_alternatives WHERE project_id=? "
            "ORDER BY total_score DESC", (pid,)
        ).fetchall()]
        for o in alt_options:
            try:
                o["scores"] = json.loads(o.get("scores_json") or "{}")
            except Exception:
                o["scores"] = {}

        # Risks
        risks = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_risks WHERE project_id=? "
            "ORDER BY risk_score DESC", (pid,)
        ).fetchall()]

        # BOM
        bom_items = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_bom_items WHERE project_id=? "
            "ORDER BY category", (pid,)
        ).fetchall()]
        bom_total = sum(
            (it.get("extended_cost") or 0) for it in bom_items)
        maint_total = sum(
            (it.get("annual_maint") or 0) for it in bom_items)

        # Capacity
        capacity = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_capacity_projections "
            "WHERE project_id=?", (pid,)
        ).fetchall()]

        # Migration
        migration = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_migration_phases WHERE project_id=? "
            "ORDER BY phase_num", (pid,)
        ).fetchall()]

        # Lab tests
        lab_tests = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_lab_tests WHERE project_id=? "
            "ORDER BY created_at", (pid,)
        ).fetchall()]

        # Standards
        standards = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_standards_checks WHERE project_id=?",
            (pid,)
        ).fetchall()]

        # Resources
        resources = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_resource_plan WHERE project_id=? "
            "ORDER BY phase", (pid,)
        ).fetchall()]
        labor_cost = sum(
            (r.get("hours") or 0) * (r.get("rate_per_hour") or 0)
            for r in resources
        )

        conn.close()
        return jsonify({
            "project": {
                "name": proj.get("name"),
                "owner": proj.get("owner"),
                "status": proj.get("status"),
                "description": proj.get("description"),
            },
            "executive_summary": package,
            "alternatives_analysis": {
                "criteria": alt_criteria,
                "options": alt_options,
            },
            "risk_register": risks,
            "detailed_bom": {
                "items": bom_items,
                "hardware_total": bom_total,
                "annual_maintenance": maint_total,
            },
            "capacity_projections": capacity,
            "migration_plan": migration,
            "lab_test_results": lab_tests,
            "standards_alignment": standards,
            "resource_plan": {
                "resources": resources,
                "total_labor_cost": round(labor_cost, 2),
            },
            "generated_at": _now(),
        })

    # ── Business Case Export (Markdown / HTML / DOCX) ─────────────────────
    def _bc_to_markdown(bc):
        """Convert business case dict to Markdown string."""
        p = bc.get("project", {})
        es = bc.get("executive_summary", {})
        lines = [
            f"# Business Case: {p.get('name', 'Untitled')}",
            f"**Owner:** {p.get('owner', '—')} | "
            f"**Status:** {p.get('status', '—')}",
            "",
            p.get("description", ""),
            "",
            "## Executive Summary",
            f"- Topologies: {es.get('topology_count', 0)}",
            f"- Total Devices: {es.get('total_devices', 0)}",
            f"- Compliance: {es.get('compliance_pct', '—')}%"
            if es.get('compliance_pct') is not None else
            "- Compliance: No audits",
            f"- CAT1 Findings: {es.get('cat1_findings', 0)}",
            f"- Est. CapEx: ${es.get('total_capex', 0):,.0f}",
            f"- Monthly Circuit: ${es.get('monthly_circuit_cost', 0):,.0f}/mo",
        ]
        roi = es.get("roi", {})
        if roi.get("capex"):
            lines += [
                "",
                "## ROI Analysis",
                "| Metric | Value |",
                "|--------|-------|",
                f"| CapEx | ${roi.get('capex', 0):,.0f} |",
                f"| Annual OpEx | ${roi.get('opex_annual', 0):,.0f} |",
                f"| Annual Savings | ${roi.get('savings_annual', 0):,.0f} |",
                f"| Payback | {roi.get('payback_months', 0)} months |",
                f"| 5-Year NPV | ${roi.get('npv_5yr', 0):,.0f} |",
            ]
        if es.get("justification"):
            lines += ["", f"**Justification:** {es['justification']}"]
        if es.get("alternatives"):
            lines += [f"**Alternatives:** {es['alternatives']}"]

        # Alternatives matrix
        alt = bc.get("alternatives_analysis", {})
        if alt.get("options"):
            lines += ["", "## Alternatives Analysis"]
            criteria = alt.get("criteria", [])
            header = "| Option | " + " | ".join(
                c.get("name", "") for c in criteria) + " | Total |"
            sep = "|--------|" + "|".join(
                "------" for _ in criteria) + "|-------|"
            lines += [header, sep]
            for o in alt["options"]:
                scores = o.get("scores", {})
                row = f"| {o.get('option_name', '')} "
                for c in criteria:
                    s = scores.get(c.get("name", ""), {})
                    row += f"| {s.get('score', '—')} "
                row += f"| **{o.get('total_score', 0)}** |"
                lines.append(row)

        # Risks
        risks = bc.get("risk_register", [])
        if risks:
            lines += [
                "", "## Risk Register",
                "| Risk | Category | Prob | Impact | Score | Mitigation | Status |",
                "|------|----------|------|--------|-------|------------|--------|",
            ]
            for r in risks:
                lines.append(
                    f"| {r.get('title', '')} | {r.get('category', '')} "
                    f"| {r.get('probability', '')} | {r.get('impact', '')} "
                    f"| {r.get('risk_score', 0)} | {r.get('mitigation', '')} "
                    f"| {r.get('status', '')} |"
                )

        # BOM
        bom = bc.get("detailed_bom", {})
        if bom.get("items"):
            lines += [
                "", "## Bill of Materials",
                "| Vendor | Model | Qty | Unit $ | Ext $ | Maint/yr | Lead Days |",
                "|--------|-------|-----|--------|-------|----------|-----------|",
            ]
            for it in bom["items"]:
                lines.append(
                    f"| {it.get('vendor', '')} | {it.get('model', '')} "
                    f"| {it.get('quantity', 0)} "
                    f"| ${it.get('unit_cost', 0):,.0f} "
                    f"| ${it.get('extended_cost', 0):,.0f} "
                    f"| ${it.get('annual_maint', 0):,.0f} "
                    f"| {it.get('lead_time_days', 0)} |"
                )
            lines.append(
                f"\n**Total Hardware:** ${bom.get('hardware_total', 0):,.0f} "
                f"| **Annual Maintenance:** ${bom.get('annual_maintenance', 0):,.0f}"
            )

        # Capacity
        caps = bc.get("capacity_projections", [])
        if caps:
            lines += [
                "", "## Capacity Projections",
                "| Metric | Current | Year 1 | Year 3 | Year 5 | Growth % |",
                "|--------|---------|--------|--------|--------|----------|",
            ]
            for c in caps:
                lines.append(
                    f"| {c.get('metric_name', '')} "
                    f"| {c.get('current_value', 0)} "
                    f"| {c.get('year1_value', 0)} "
                    f"| {c.get('year3_value', 0)} "
                    f"| {c.get('year5_value', 0)} "
                    f"| {c.get('growth_rate_pct', 0)}% |"
                )

        # Migration
        mig = bc.get("migration_plan", [])
        if mig:
            lines += [
                "", "## Migration/Cutover Plan",
                "| Phase | Title | Duration | Parallel | Rollback |",
                "|-------|-------|----------|----------|----------|",
            ]
            for m in mig:
                lines.append(
                    f"| {m.get('phase_num', '')} | {m.get('title', '')} "
                    f"| {m.get('duration_days', 0)} days "
                    f"| {'Yes' if m.get('parallel_run') else 'No'} "
                    f"| {m.get('rollback_criteria', '')} |"
                )

        # Lab tests
        labs = bc.get("lab_test_results", [])
        if labs:
            lines += [
                "", "## Lab Test Results",
                "| Test | Category | Result | Tester |",
                "|------|----------|--------|--------|",
            ]
            for t in labs:
                lines.append(
                    f"| {t.get('test_name', '')} | {t.get('category', '')} "
                    f"| {t.get('result', '')} | {t.get('tested_by', '')} |"
                )

        # Standards
        stds = bc.get("standards_alignment", [])
        if stds:
            lines += [
                "", "## Standards Alignment",
                "| Standard | Check | Status |",
                "|----------|-------|--------|",
            ]
            for s in stds:
                lines.append(
                    f"| {s.get('standard', '')} | {s.get('check_item', '')} "
                    f"| {s.get('status', '')} |"
                )

        # Resources
        rp = bc.get("resource_plan", {})
        if rp.get("resources"):
            lines += [
                "", "## Resource Plan",
                "| Phase | Role | Name | Hours | Rate | Cost |",
                "|-------|------|------|-------|------|------|",
            ]
            for r in rp["resources"]:
                cost = (r.get("hours") or 0) * (r.get("rate_per_hour") or 0)
                lines.append(
                    f"| {r.get('phase', '')} | {r.get('role', '')} "
                    f"| {r.get('name', '')} | {r.get('hours', 0)} "
                    f"| ${r.get('rate_per_hour', 0):,.0f} "
                    f"| ${cost:,.0f} |"
                )
            lines.append(
                f"\n**Total Labor:** ${rp.get('total_labor_cost', 0):,.0f}"
            )

        lines += [
            "", "---",
            f"*Generated: {bc.get('generated_at', '')} — ICDEV™ Network Design Canvas*",
        ]
        return "\n".join(lines)

    def _md_to_html(md_text):
        """Simple Markdown to HTML conversion (tables + headers)."""
        html = ["<!DOCTYPE html><html><head><meta charset='utf-8'>",
                "<style>body{font-family:Segoe UI,sans-serif;max-width:900px;margin:40px auto;color:#222;line-height:1.6;}",
                "table{border-collapse:collapse;width:100%;margin:12px 0;}",
                "th,td{border:1px solid #ccc;padding:6px 10px;text-align:left;font-size:13px;}",
                "th{background:#f5f5f5;font-weight:600;}",
                "h1{color:#1a1a2e;border-bottom:2px solid #e94560;padding-bottom:8px;}",
                "h2{color:#0f3460;margin-top:24px;}",
                "strong{color:#333;}</style></head><body>"]
        in_table = False
        for line in md_text.split("\n"):
            if line.startswith("# "):
                html.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                html.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("|") and "---" in line:
                continue  # skip separator
            elif line.startswith("|"):
                if not in_table:
                    html.append("<table><tr>")
                    in_table = True
                    cells = [c.strip() for c in line.split("|")[1:-1]]
                    html.append("".join(
                        f"<th>{c}</th>" for c in cells))
                    html.append("</tr>")
                else:
                    cells = [c.strip() for c in line.split("|")[1:-1]]
                    html.append("<tr>" + "".join(
                        f"<td>{c}</td>" for c in cells) + "</tr>")
            else:
                if in_table:
                    html.append("</table>")
                    in_table = False
                if line.startswith("- "):
                    html.append(f"<li>{line[2:]}</li>")
                elif line.startswith("**") and line.endswith("**"):
                    html.append(f"<p><strong>{line[2:-2]}</strong></p>")
                elif line.strip():
                    html.append(f"<p>{line}</p>")
        if in_table:
            html.append("</table>")
        html.append("</body></html>")
        return "\n".join(html)

    def _bc_to_docx(bc):
        """Generate DOCX bytes from business case dict."""
        from docx import Document
        from docx.shared import Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        import io

        doc = Document()
        style = doc.styles['Normal']
        style.font.name = 'Calibri'
        style.font.size = Pt(11)

        p = bc.get("project", {})
        es = bc.get("executive_summary", {})

        doc.add_heading(f"Business Case: {p.get('name', '')}", 0)
        doc.add_paragraph(
            f"Owner: {p.get('owner', '—')} | "
            f"Status: {p.get('status', '—')}")
        if p.get("description"):
            doc.add_paragraph(p["description"])

        # Executive Summary
        doc.add_heading("Executive Summary", level=1)
        tbl = doc.add_table(rows=1, cols=2)
        tbl.style = 'Light Grid Accent 1'
        for label, val in [
            ("Topologies", es.get("topology_count", 0)),
            ("Devices", es.get("total_devices", 0)),
            ("Compliance", f"{es.get('compliance_pct', '—')}%"),
            ("CAT1 Findings", es.get("cat1_findings", 0)),
            ("Est. CapEx", f"${es.get('total_capex', 0):,.0f}"),
            ("Circuit OpEx", f"${es.get('monthly_circuit_cost', 0):,.0f}/mo"),
        ]:
            row = tbl.add_row().cells
            row[0].text = str(label)
            row[1].text = str(val)

        # ROI
        roi = es.get("roi", {})
        if roi.get("capex"):
            doc.add_heading("ROI Analysis", level=1)
            tbl = doc.add_table(rows=1, cols=2)
            tbl.style = 'Light Grid Accent 1'
            for label, val in [
                ("CapEx", f"${roi.get('capex', 0):,.0f}"),
                ("Annual OpEx", f"${roi.get('opex_annual', 0):,.0f}"),
                ("Annual Savings", f"${roi.get('savings_annual', 0):,.0f}"),
                ("Payback", f"{roi.get('payback_months', 0)} months"),
                ("5-Year NPV", f"${roi.get('npv_5yr', 0):,.0f}"),
            ]:
                row = tbl.add_row().cells
                row[0].text = str(label)
                row[1].text = str(val)

        # Alternatives
        alt = bc.get("alternatives_analysis", {})
        if alt.get("options"):
            doc.add_heading("Alternatives Analysis", level=1)
            criteria = alt.get("criteria", [])
            cols = ["Option"] + [c.get("name", "") for c in criteria] + ["Total"]
            tbl = doc.add_table(rows=1, cols=len(cols))
            tbl.style = 'Light Grid Accent 1'
            for i, h in enumerate(cols):
                tbl.rows[0].cells[i].text = h
            for o in alt["options"]:
                row = tbl.add_row().cells
                row[0].text = o.get("option_name", "")
                scores = o.get("scores", {})
                for j, c in enumerate(criteria):
                    s = scores.get(c.get("name", ""), {})
                    row[j + 1].text = str(s.get("score", "—"))
                row[len(cols) - 1].text = str(o.get("total_score", 0))

        # Risks
        risks = bc.get("risk_register", [])
        if risks:
            doc.add_heading("Risk Register", level=1)
            tbl = doc.add_table(rows=1, cols=5)
            tbl.style = 'Light Grid Accent 1'
            for i, h in enumerate(["Risk", "Prob", "Impact", "Score", "Mitigation"]):
                tbl.rows[0].cells[i].text = h
            for r in risks:
                row = tbl.add_row().cells
                row[0].text = r.get("title", "")
                row[1].text = r.get("probability", "")
                row[2].text = r.get("impact", "")
                row[3].text = str(r.get("risk_score", 0))
                row[4].text = r.get("mitigation", "")

        # BOM
        bom = bc.get("detailed_bom", {})
        if bom.get("items"):
            doc.add_heading("Bill of Materials", level=1)
            tbl = doc.add_table(rows=1, cols=6)
            tbl.style = 'Light Grid Accent 1'
            for i, h in enumerate(["Vendor", "Model", "Qty", "Unit $", "Ext $", "Lead Days"]):
                tbl.rows[0].cells[i].text = h
            for it in bom["items"]:
                row = tbl.add_row().cells
                row[0].text = it.get("vendor", "")
                row[1].text = it.get("model", "")
                row[2].text = str(it.get("quantity", 0))
                row[3].text = f"${it.get('unit_cost', 0):,.0f}"
                row[4].text = f"${it.get('extended_cost', 0):,.0f}"
                row[5].text = str(it.get("lead_time_days", 0))

        # Capacity
        caps = bc.get("capacity_projections", [])
        if caps:
            doc.add_heading("Capacity Projections", level=1)
            tbl = doc.add_table(rows=1, cols=5)
            tbl.style = 'Light Grid Accent 1'
            for i, h in enumerate(["Metric", "Current", "Year 1", "Year 3", "Year 5"]):
                tbl.rows[0].cells[i].text = h
            for c in caps:
                row = tbl.add_row().cells
                row[0].text = c.get("metric_name", "")
                row[1].text = str(c.get("current_value", 0))
                row[2].text = str(c.get("year1_value", 0))
                row[3].text = str(c.get("year3_value", 0))
                row[4].text = str(c.get("year5_value", 0))

        # Footer
        doc.add_paragraph("")
        footer = doc.add_paragraph()
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = footer.add_run(
            f"Generated: {bc.get('generated_at', '')} — ICDEV™")
        run.font.size = Pt(9)
        run.font.italic = True

        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()

    @bp.route("/api/projects/<pid>/business-case/export",
              methods=["POST"])
    @nc_login_required
    def nc_api_export_business_case(pid):
        """Export business case in markdown, html, or docx format."""
        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "markdown")

        # Reuse business case generator logic
        conn = get_connection()
        # Build the same data as business-case GET
        proj = conn.execute(
            "SELECT * FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        if not proj:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        proj = _row_to_dict(proj)
        package = _build_review_package(conn, pid)
        bc = {
            "project": {
                "name": proj.get("name"),
                "owner": proj.get("owner"),
                "status": proj.get("status"),
                "description": proj.get("description"),
            },
            "executive_summary": package,
            "alternatives_analysis": {
                "criteria": [_row_to_dict(r) for r in conn.execute(
                    "SELECT * FROM nc_alt_criteria WHERE project_id=? "
                    "ORDER BY sort_order", (pid,)).fetchall()],
                "options": [],
            },
            "risk_register": [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_risks WHERE project_id=? "
                "ORDER BY risk_score DESC", (pid,)).fetchall()],
            "detailed_bom": {"items": [], "hardware_total": 0,
                             "annual_maintenance": 0},
            "capacity_projections": [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_capacity_projections "
                "WHERE project_id=?", (pid,)).fetchall()],
            "migration_plan": [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_migration_phases WHERE project_id=? "
                "ORDER BY phase_num", (pid,)).fetchall()],
            "lab_test_results": [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_lab_tests WHERE project_id=?",
                (pid,)).fetchall()],
            "standards_alignment": [_row_to_dict(r) for r in conn.execute(
                "SELECT * FROM nc_standards_checks WHERE project_id=?",
                (pid,)).fetchall()],
            "resource_plan": {"resources": [], "total_labor_cost": 0},
            "generated_at": _now(),
        }
        # Alternatives options
        for o in conn.execute(
            "SELECT * FROM nc_alternatives WHERE project_id=? "
            "ORDER BY total_score DESC", (pid,)
        ).fetchall():
            od = _row_to_dict(o)
            try:
                od["scores"] = json.loads(od.get("scores_json") or "{}")
            except Exception:
                od["scores"] = {}
            bc["alternatives_analysis"]["options"].append(od)
        # BOM
        bom_items = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_bom_items WHERE project_id=?",
            (pid,)).fetchall()]
        bc["detailed_bom"]["items"] = bom_items
        bc["detailed_bom"]["hardware_total"] = sum(
            it.get("extended_cost", 0) or 0 for it in bom_items)
        bc["detailed_bom"]["annual_maintenance"] = sum(
            it.get("annual_maint", 0) or 0 for it in bom_items)
        # Resources
        resources = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_resource_plan WHERE project_id=?",
            (pid,)).fetchall()]
        bc["resource_plan"]["resources"] = resources
        bc["resource_plan"]["total_labor_cost"] = round(sum(
            (r.get("hours") or 0) * (r.get("rate_per_hour") or 0)
            for r in resources), 2)
        conn.close()

        proj_name = proj.get("name", "business-case").replace(" ", "-")

        if fmt == "markdown":
            md = _bc_to_markdown(bc)
            return jsonify({
                "format": "markdown",
                "filename": f"{proj_name}.md",
                "content": md,
            })
        elif fmt == "html":
            md = _bc_to_markdown(bc)
            html = _md_to_html(md)
            return jsonify({
                "format": "html",
                "filename": f"{proj_name}.html",
                "content": html,
            })
        elif fmt == "docx":
            import base64
            docx_bytes = _bc_to_docx(bc)
            return jsonify({
                "format": "docx",
                "filename": f"{proj_name}.docx",
                "content_b64": base64.b64encode(
                    docx_bytes).decode("ascii"),
                "size_bytes": len(docx_bytes),
            })
        else:
            return jsonify({"error": f"Unknown format: {fmt}"}), 400

    # ══════════════════════════════════════════════════════════════════════
    # Design Pattern Library (Phase 2)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/design-patterns", methods=["GET"])
    @nc_login_required
    def nc_api_list_design_patterns():
        category = request.args.get("category", "")
        conn = get_connection()
        if category:
            rows = conn.execute(
                "SELECT id, name, category, description, is_builtin, "
                "tags, created_by, created_at "
                "FROM nc_design_patterns WHERE category=? "
                "ORDER BY is_builtin DESC, name", (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, category, description, is_builtin, "
                "tags, created_by, created_at "
                "FROM nc_design_patterns "
                "ORDER BY category, is_builtin DESC, name"
            ).fetchall()
        conn.close()
        patterns = []
        for r in rows:
            p = _row_to_dict(r)
            try:
                p["tags"] = json.loads(p.get("tags") or "[]")
            except Exception:
                p["tags"] = []
            patterns.append(p)
        return jsonify(patterns)

    @bp.route("/api/design-patterns/<pat_id>", methods=["GET"])
    @nc_login_required
    def nc_api_get_design_pattern(pat_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM nc_design_patterns WHERE id=?", (pat_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        p = _row_to_dict(row)
        try:
            p["tags"] = json.loads(p.get("tags") or "[]")
            p["graph_json"] = json.loads(
                p.get("graph_json") or '{"nodes":[],"edges":[]}')
        except Exception:
            pass
        return jsonify(p)

    @bp.route("/api/design-patterns", methods=["POST"])
    @nc_login_required
    def nc_api_create_design_pattern():
        """Create a user-defined design pattern."""
        data = request.get_json(force=True, silent=True) or {}
        pid = str(_uuid.uuid4())
        conn = get_connection()
        graph = data.get("graph_json")
        if isinstance(graph, dict):
            graph = json.dumps(graph)
        elif not graph:
            graph = '{"nodes":[],"edges":[]}'
        tags = data.get("tags", [])
        if isinstance(tags, list):
            tags = json.dumps(tags)
        conn.execute(
            "INSERT INTO nc_design_patterns "
            "(id, name, category, description, graph_json, "
            " is_builtin, tags, created_by, created_at) "
            "VALUES (?,?,?,?,?,0,?,?,?)",
            (pid, data.get("name", "Custom Pattern"),
             data.get("category", "custom"),
             data.get("description", ""), graph, tags,
             data.get("created_by", ""), _now())
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "design_pattern", pid,
               data.get("name", ""))
        return jsonify({"id": pid}), 201

    @bp.route("/api/design-patterns/<pat_id>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_design_pattern(pat_id):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        row = conn.execute(
            "SELECT is_builtin FROM nc_design_patterns WHERE id=?",
            (pat_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        allowed = ["name", "category", "description", "tags"]
        if not row[0]:  # user-created can also update graph
            allowed.append("graph_json")
        fields, values = [], []
        for k in allowed:
            if k in data:
                v = data[k]
                if k == "tags" and isinstance(v, list):
                    v = json.dumps(v)
                if k == "graph_json" and isinstance(v, dict):
                    v = json.dumps(v)
                fields.append(f"{k}=?")
                values.append(v)
        if fields:
            values.append(pat_id)
            conn.execute(
                f"UPDATE nc_design_patterns "  # nosec B608
                f"SET {', '.join(fields)} WHERE id=?", values
            )
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/design-patterns/<pat_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_design_pattern(pat_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT is_builtin FROM nc_design_patterns WHERE id=?",
            (pat_id,)
        ).fetchone()
        if row and row[0]:
            conn.close()
            return jsonify(
                {"error": "Cannot delete built-in pattern"}), 403
        conn.execute(
            "DELETE FROM nc_design_patterns WHERE id=?", (pat_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/design-patterns/save-from-selection",
              methods=["POST"])
    @nc_login_required
    def nc_api_save_pattern_from_selection():
        """Save selected canvas nodes/edges as a user-defined pattern."""
        data = request.get_json(force=True, silent=True) or {}
        graph_json = data.get("graph_json")
        if not graph_json or not graph_json.get("nodes"):
            return jsonify(
                {"error": "Select nodes on canvas first"}), 400
        pid = str(_uuid.uuid4())
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_design_patterns "
            "(id, name, category, description, graph_json, "
            " is_builtin, tags, created_by, created_at) "
            "VALUES (?,?,?,?,?,0,?,?,?)",
            (pid, data.get("name", "My Pattern"),
             data.get("category", "custom"),
             data.get("description", ""),
             json.dumps(graph_json),
             json.dumps(data.get("tags", [])),
             data.get("created_by", ""), _now())
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "design_pattern", pid, "from selection")
        return jsonify({"id": pid}), 201

    # ══════════════════════════════════════════════════════════════════════
    # Proactive Guidance Engine (Phase 3)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/design-scorecard/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_design_scorecard(topo_id):
        """Analyze topology and return design completeness scorecard."""
        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json FROM topologies WHERE id=?", (topo_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        types = [n.get("type", "") for n in nodes]
        type_set = set(types)

        # Build adjacency for analysis
        adj = {}
        for e in edges:
            adj.setdefault(e.get("source"), set()).add(e.get("target"))
            adj.setdefault(e.get("target"), set()).add(e.get("source"))

        checks = []

        def check(cat, name, passed, detail=""):
            checks.append({
                "category": cat, "name": name,
                "passed": passed, "detail": detail,
            })

        # ── Routing ───────────────────────────────────────────────────
        has_router = bool(type_set & {"router", "mpls-pe", "mpls-p"})
        check("routing", "At least one router present",
              has_router)
        has_routing_proto = any(
            n.get("config", {}).get("protocol") or
            any(e.get("protocol") in ("ospf", "bgp", "eigrp", "isis")
                for e in edges
                if e.get("source") == n.get("id") or
                e.get("target") == n.get("id"))
            for n in nodes if n.get("type") in ("router", "mpls-pe")
        ) if has_router else False
        check("routing", "Routing protocol configured",
              has_routing_proto,
              "OSPF/BGP/EIGRP on at least one router")

        # ── Redundancy ────────────────────────────────────────────────
        router_count = sum(1 for t in types if t in (
            "router", "mpls-pe"))
        check("redundancy", "Redundant routers (2+)",
              router_count >= 2, f"{router_count} routers")
        fw_count = sum(1 for t in types if t == "firewall")
        check("redundancy", "Firewall HA pair",
              fw_count >= 2, f"{fw_count} firewalls")
        # Check for nodes with single connection
        single_conn = []
        for n in nodes:
            if n.get("type") in ("router", "switch-l3", "firewall"):
                conns = len(adj.get(n.get("id"), set()))
                if conns <= 1:
                    single_conn.append(n.get("label", n.get("id")))
        check("redundancy", "No single-uplink critical devices",
              len(single_conn) == 0,
              f"SPOF: {', '.join(single_conn[:3])}" if single_conn
              else "All critical devices have 2+ links")

        # ── Security ──────────────────────────────────────────────────
        has_fw = "firewall" in type_set
        check("security", "Firewall present", has_fw)
        enc_types = {"type1-encryptor", "fips-140-l2", "fips-140-l3",
                     "macsec", "hsm"}
        has_enc = bool(type_set & enc_types)
        wan_types = {"cloud", "aws-dx", "az-er", "gcp-ic"}
        has_wan = bool(type_set & wan_types)
        check("security", "WAN encryption present",
              has_enc or not has_wan,
              "Encrypted" if has_enc else
              "No WAN links" if not has_wan else "Missing encryption")
        check("security", "Management plane separation",
              any(n.get("type") == "siem" for n in nodes) or
              any(n.get("config", {}).get("vrf") == "mgmt"
                  for n in nodes),
              "SIEM or mgmt VRF detected")

        # ── Capacity ──────────────────────────────────────────────────
        check("capacity", "Sufficient device count",
              len(nodes) >= 3,
              f"{len(nodes)} devices")
        check("capacity", "Link diversity",
              len(edges) >= len(nodes),
              f"{len(edges)} links for {len(nodes)} nodes")

        # ── Fault tolerance ───────────────────────────────────────────
        check("fault_tolerance", "Monitoring device present",
              bool(type_set & {"siem", "network-tap", "wlc"}))
        check("fault_tolerance", "Power redundancy considered",
              any(n.get("label", "").lower() in (
                  "ups", "pdu", "pdu-a", "pdu-b", "generator")
                  for n in nodes) or len(nodes) < 5,
              "UPS/PDU found" if any(
                  "ups" in n.get("label", "").lower() or
                  "pdu" in n.get("label", "").lower()
                  for n in nodes) else "Consider adding")

        # Score per category
        categories = {}
        for c in checks:
            cat = c["category"]
            if cat not in categories:
                categories[cat] = {"total": 0, "passed": 0}
            categories[cat]["total"] += 1
            if c["passed"]:
                categories[cat]["passed"] += 1

        overall_total = sum(v["total"] for v in categories.values())
        overall_passed = sum(v["passed"] for v in categories.values())
        overall_pct = round(
            overall_passed * 100 / overall_total
        ) if overall_total else 0

        return jsonify({
            "topology_id": topo_id,
            "checks": checks,
            "categories": {
                k: {**v, "pct": round(
                    v["passed"] * 100 / v["total"]
                ) if v["total"] else 0}
                for k, v in categories.items()
            },
            "overall_score": overall_pct,
            "overall_passed": overall_passed,
            "overall_total": overall_total,
        })

    # ══════════════════════════════════════════════════════════════════════
    # NDC Case Workflow (Phase 4)
    # ══════════════════════════════════════════════════════════════════════

    _NDC_LIFECYCLE = {
        "states": [
            "concept", "requirements", "ssp_review",
            "design", "peer_review", "security_approval",
            "lab_test", "change_approval", "implementation",
            "verification", "handoff", "operate",
        ],
        "optional_states": ["ssp_review", "security_approval"],
        "transitions": {
            "concept": ["requirements"],
            "requirements": ["ssp_review", "design", "concept"],
            "ssp_review": ["design", "requirements"],
            "design": ["peer_review", "requirements"],
            "peer_review": ["security_approval", "lab_test", "design"],
            "security_approval": ["lab_test", "peer_review"],
            "lab_test": ["change_approval", "design"],
            "change_approval": ["implementation", "design"],
            "implementation": ["verification"],
            "verification": ["handoff", "implementation"],
            "handoff": ["operate"],
            "operate": [],
        },
        "checklists": {
            "concept": [
                "Business justification documented",
                "Stakeholders identified",
                "High-level topology sketched",
            ],
            "requirements": [
                "Bandwidth requirements defined",
                "Redundancy requirements specified",
                "Security classification determined",
                "SLA targets documented",
            ],
            "ssp_review": [
                "System Security Plan (SSP) drafted or updated",
                "Security boundary defined and documented",
                "Authorization Boundary Diagram included",
                "Information types and data flows documented",
                "SSP submitted to ISSO/ISSM for review",
                "SSP approval obtained (or waiver documented)",
            ],
            "design": [
                "Detailed topology completed",
                "Routing protocol selected and documented",
                "IP addressing plan (IPAM) allocated",
                "Equipment BOM finalized",
                "Circuit orders identified",
            ],
            "peer_review": [
                "Design reviewed by network architect",
                "Compliance audit passed (80%+)",
                "SPOF analysis completed",
                "Design pattern alignment verified",
            ],
            "security_approval": [
                "Firewall rule change request submitted to security team",
                "IDS/IPS policy updates documented",
                "ACL changes reviewed by security analyst",
                "Network segmentation verified per security policy",
                "Security team sign-off obtained",
                "Separation of duty verified (network vs security team)",
            ],
            "lab_test": [
                "Lab environment provisioned",
                "Routing convergence tested",
                "Failover scenarios validated",
                "Traffic engineering verified",
            ],
            "change_approval": [
                "CCB change request submitted",
                "Rollback plan documented",
                "Maintenance window scheduled",
                "Stakeholder sign-off obtained",
            ],
            "implementation": [
                "Device configurations applied",
                "Circuits activated",
                "Routing adjacencies established",
                "Monitoring enabled",
            ],
            "verification": [
                "End-to-end connectivity verified",
                "Performance benchmarks met",
                "Security scan passed",
                "As-built diagram updated",
            ],
            "handoff": [
                "Operations team briefed",
                "Runbook documented",
                "Monitoring dashboards configured",
                "Escalation procedures defined",
            ],
        },
    }

    @bp.route("/api/projects/<pid>/case-workflow", methods=["GET"])
    @nc_login_required
    def nc_api_get_case_workflow(pid):
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM nc_case_workflows WHERE project_id=?",
            (pid,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"exists": False,
                            "lifecycle": _NDC_LIFECYCLE})
        wf = _row_to_dict(row)
        history = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_case_history WHERE workflow_id=? "
            "ORDER BY changed_at", (wf["id"],)
        ).fetchall()]
        conn.close()
        try:
            wf["lifecycle"] = json.loads(wf.get("lifecycle_json", "{}"))
        except Exception:
            wf["lifecycle"] = _NDC_LIFECYCLE
        checklist = wf["lifecycle"].get(
            "checklists", {}).get(wf["current_state"], [])
        allowed = wf["lifecycle"].get(
            "transitions", {}).get(wf["current_state"], [])
        return jsonify({
            "exists": True,
            "workflow_id": wf["id"],
            "current_state": wf["current_state"],
            "checklist": checklist,
            "allowed_transitions": allowed,
            "history": history,
            "lifecycle": wf["lifecycle"],
        })

    @bp.route("/api/projects/<pid>/case-workflow", methods=["POST"])
    @nc_login_required
    def nc_api_init_case_workflow(pid):
        conn = get_connection()
        existing = conn.execute(
            "SELECT id FROM nc_case_workflows WHERE project_id=?",
            (pid,)
        ).fetchone()
        if existing:
            conn.close()
            return jsonify({"error": "Workflow already exists"}), 409
        wid = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_case_workflows "
            "(id, project_id, current_state, lifecycle_json, "
            " created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (wid, pid, "concept",
             json.dumps(_NDC_LIFECYCLE), now, now)
        )
        conn.execute(
            "INSERT INTO nc_case_history "
            "(workflow_id, from_state, to_state, comment, changed_at) "
            "VALUES (?,?,?,?,?)",
            (wid, "", "concept", "Workflow initialized", now)
        )
        conn.commit()
        conn.close()
        _audit("INIT_WORKFLOW", "project", pid)
        return jsonify({"workflow_id": wid,
                        "current_state": "concept"}), 201

    @bp.route("/api/projects/<pid>/case-transition", methods=["POST"])
    @nc_login_required
    def nc_api_case_transition(pid):
        data = request.get_json(force=True, silent=True) or {}
        to_state = data.get("to_state")
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM nc_case_workflows WHERE project_id=?",
            (pid,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "No workflow"}), 404
        wf = _row_to_dict(row)
        try:
            lifecycle = json.loads(wf.get("lifecycle_json", "{}"))
        except Exception:
            lifecycle = _NDC_LIFECYCLE
        current = wf["current_state"]
        allowed = lifecycle.get("transitions", {}).get(current, [])
        if to_state not in allowed:
            conn.close()
            return jsonify({
                "error": f"Cannot transition from '{current}' "
                         f"to '{to_state}'",
                "allowed": allowed,
            }), 422
        now = _now()
        conn.execute(
            "UPDATE nc_case_workflows SET current_state=?, "
            "updated_at=? WHERE project_id=?",
            (to_state, now, pid)
        )
        conn.execute(
            "INSERT INTO nc_case_history "
            "(workflow_id, from_state, to_state, changed_by, "
            " comment, changed_at) VALUES (?,?,?,?,?,?)",
            (wf["id"], current, to_state,
             data.get("changed_by", ""),
             data.get("comment", ""), now)
        )
        conn.commit()
        _notify(conn, pid, "phase_changed",
                f"Workflow: {current} -> {to_state}",
                data.get("comment", ""))
        conn.commit()
        conn.close()
        _audit("CASE_TRANSITION", "project", pid,
               f"{current} -> {to_state}")
        checklist = lifecycle.get("checklists", {}).get(to_state, [])
        return jsonify({
            "from": current, "to": to_state,
            "checklist": checklist,
        })

    # ══════════════════════════════════════════════════════════════════════
    # Deterministic Chat Assistant (Phase 5)
    # ══════════════════════════════════════════════════════════════════════

    _CHAT_INTENTS = [
        {"keywords": ["cross-connect", "cross connect", "xconn", "colo"],
         "template": "pat-cross-connect",
         "response": "For a cross-connect, I recommend starting with the "
                     "Cross-Connect pattern (patch panel + meet-me room + "
                     "demarc). Load this pattern and add your carrier "
                     "handoff details."},
        {"keywords": ["bgp", "peering", "partner", "ix", "exchange"],
         "template": "pat-bgp-peering",
         "response": "For BGP peering, start with the BGP Peering pattern. "
                     "Configure eBGP between your PE and the partner PE. "
                     "Add prefix filters and set local-pref/MED."},
        {"keywords": ["wan", "site-to-site", "branch", "remote"],
         "template": "pat-wan-edge",
         "response": "For WAN connectivity, use the WAN Edge pattern. "
                     "Add an encryptor for classified traffic and "
                     "configure IPSec/GRE tunnels to the carrier handoff."},
        {"keywords": ["sdwan", "sd-wan", "overlay", "vmanage"],
         "template": "pat-sdwan-overlay",
         "response": "For SD-WAN, start with the SD-WAN Overlay pattern. "
                     "Ensure at least 2 WAN transports for path diversity "
                     "and configure application-aware routing."},
        {"keywords": ["redundan", "ha", "high availability", "failover"],
         "template": "pat-redundant-core",
         "response": "For redundancy, use the Redundant Core pattern. "
                     "Add VRRP/HSRP for gateway redundancy and LACP "
                     "for link aggregation. Target zero SPOF."},
        {"keywords": ["dmz", "public", "web server", "internet"],
         "template": "pat-dmz-sandwich",
         "response": "For DMZ/public services, use the DMZ Sandwich "
                     "pattern. Place services between external and "
                     "internal firewalls with default-deny policies."},
        {"keywords": ["mpls", "provider", "carrier", "label"],
         "template": "pat-mpls-pe",
         "response": "For MPLS, start with the MPLS PE Node pattern. "
                     "Configure VRFs per customer and connect to a "
                     "route reflector for iBGP scale."},
        {"keywords": ["campus", "distribution", "access", "building"],
         "template": "pat-dist-block",
         "response": "For campus design, use the Distribution Block "
                     "pattern. Configure STP root/secondary and LACP "
                     "uplinks. Add access switches below."},
        {"keywords": ["encrypt", "classified", "secret", "type 1"],
         "template": "pat-wan-edge",
         "response": "For classified networks, encryption is mandatory. "
                     "Use Type 1 NSA encryptors (KG-175D/KG-250) for "
                     "SECRET+. The WAN Edge pattern includes encryption."},
        {"keywords": ["power", "ups", "pdu", "backup power"],
         "template": "pat-backup-power",
         "response": "For power redundancy, use the Backup Power pattern. "
                     "Dual A/B PDU feeds from separate UPS units. "
                     "Target 15-minute UPS runtime minimum."},
        {"keywords": ["aws", "vpc", "cloud", "azure", "gcp"],
         "template": None,
         "response": "For cloud connectivity, create a VPC/VNet group "
                     "container on the canvas and add subnets. Use "
                     "Direct Connect/ExpressRoute for dedicated links "
                     "to on-prem. Check the Templates gallery for "
                     "cloud-specific starter topologies."},
        {"keywords": ["ospf", "routing", "igp"],
         "template": None,
         "response": "OSPF best practices: Use area 0 for backbone. "
                     "Summarize at area boundaries. Enable BFD for "
                     "sub-second failover. Configure passive-interface "
                     "default and activate only needed interfaces."},
        {"keywords": ["firewall", "security", "zone"],
         "template": "pat-dmz-sandwich",
         "response": "For firewalls, always deploy in HA pairs. "
                     "Default-deny policy, log all denies. Place "
                     "between trust zones. The DMZ Sandwich pattern "
                     "is a good starting point."},
    ]

    @bp.route("/api/chat-assist", methods=["POST"])
    @nc_login_required
    def nc_api_chat_assist():
        """Deterministic chat: keyword match -> template + guidance.
        No LLM required — works fully air-gapped."""
        data = request.get_json(force=True, silent=True) or {}
        message = (data.get("message") or "").lower().strip()
        if not message:
            return jsonify({"response": "How can I help with your "
                            "network design? Try asking about "
                            "cross-connects, BGP peering, WAN, "
                            "SD-WAN, redundancy, or security.",
                            "template_id": None})

        best_match = None
        best_score = 0
        for intent in _CHAT_INTENTS:
            score = sum(
                1 for kw in intent["keywords"] if kw in message
            )
            if score > best_score:
                best_score = score
                best_match = intent

        if best_match and best_score > 0:
            return jsonify({
                "response": best_match["response"],
                "template_id": best_match.get("template"),
                "matched_keywords": [
                    kw for kw in best_match["keywords"]
                    if kw in message
                ],
                "confidence": min(best_score / 2, 1.0),
            })

        # Fallback: check best practices
        bp_cats = _design_rules.get("best_practices", {})
        for cat, practices in bp_cats.items():
            if cat in message:
                return jsonify({
                    "response": f"Best practices for {cat}:\n" +
                                "\n".join(f"- {p}" for p in practices[:5]),
                    "template_id": None,
                    "confidence": 0.5,
                })

        return jsonify({
            "response": "I can help with network design. Try asking "
                        "about: cross-connects, BGP peering, WAN "
                        "design, SD-WAN, redundancy, DMZ, MPLS, "
                        "campus networks, encryption, or routing "
                        "best practices.",
            "template_id": None,
            "confidence": 0.0,
        })

    @bp.route("/api/design-patterns/categories", methods=["GET"])
    @nc_login_required
    def nc_api_pattern_categories():
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT category, COUNT(*) AS cnt "
            "FROM nc_design_patterns GROUP BY category "
            "ORDER BY category"
        ).fetchall()
        conn.close()
        return jsonify([
            {"category": r[0], "count": r[1]} for r in rows
        ])

    # ══════════════════════════════════════════════════════════════════════
    # Extended: Notifications, Topology Diff, Auto-Decompose, Global Canvas
    # ══════════════════════════════════════════════════════════════════════

    # ── Notifications ─────────────────────────────────────────────────────
    @bp.route("/api/notifications", methods=["GET"])
    @nc_login_required
    def nc_api_list_notifications():
        conn = get_connection()
        rows = conn.execute(
            "SELECT n.*, p.name AS project_name "
            "FROM nc_notifications n "
            "LEFT JOIN nc_projects p ON p.id=n.project_id "
            "ORDER BY n.created_at DESC LIMIT 50"
        ).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) FROM nc_notifications WHERE is_read=0"
        ).fetchone()[0]
        conn.close()
        return jsonify({
            "notifications": [_row_to_dict(r) for r in rows],
            "unread": unread,
        })

    @bp.route("/api/notifications/mark-read", methods=["POST"])
    @nc_login_required
    def nc_api_mark_notifications_read():
        conn = get_connection()
        conn.execute("UPDATE nc_notifications SET is_read=1 WHERE is_read=0")
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Topology Diff ─────────────────────────────────────────────────────
    @bp.route("/api/topology-diff", methods=["POST"])
    @nc_login_required
    def nc_api_topology_diff():
        """Compare two topologies side-by-side (node/edge delta)."""
        data = request.get_json(force=True, silent=True) or {}
        topo_a_id = data.get("topology_a")
        topo_b_id = data.get("topology_b")
        if not topo_a_id or not topo_b_id:
            return jsonify({"error": "topology_a and topology_b required"}), 400
        conn = get_connection()
        a_row = conn.execute(
            "SELECT name, graph_json FROM topologies WHERE id=?",
            (topo_a_id,)
        ).fetchone()
        b_row = conn.execute(
            "SELECT name, graph_json FROM topologies WHERE id=?",
            (topo_b_id,)
        ).fetchone()
        conn.close()
        if not a_row or not b_row:
            return jsonify({"error": "Topology not found"}), 404

        try:
            ga = json.loads(a_row["graph_json"])
        except Exception:
            ga = {"nodes": [], "edges": []}
        try:
            gb = json.loads(b_row["graph_json"])
        except Exception:
            gb = {"nodes": [], "edges": []}

        # Nodes diff by label+type
        def node_key(n):
            return f"{n.get('label', '')}|{n.get('type', '')}"

        a_nodes = {node_key(n): n for n in ga.get("nodes", [])}
        b_nodes = {node_key(n): n for n in gb.get("nodes", [])}
        a_keys = set(a_nodes.keys())
        b_keys = set(b_nodes.keys())

        only_a = [{"label": a_nodes[k].get("label"),
                    "type": a_nodes[k].get("type")}
                   for k in sorted(a_keys - b_keys)]
        only_b = [{"label": b_nodes[k].get("label"),
                    "type": b_nodes[k].get("type")}
                   for k in sorted(b_keys - a_keys)]
        common = sorted(a_keys & b_keys)

        # Edges diff
        def edge_key(e):
            return f"{e.get('source', '')}>{e.get('target', '')}"

        a_edges = {edge_key(e): e for e in ga.get("edges", [])}
        b_edges = {edge_key(e): e for e in gb.get("edges", [])}
        a_ekeys = set(a_edges.keys())
        b_ekeys = set(b_edges.keys())

        # Type distribution
        a_types = {}
        for n in ga.get("nodes", []):
            t = n.get("type", "unknown")
            a_types[t] = a_types.get(t, 0) + 1
        b_types = {}
        for n in gb.get("nodes", []):
            t = n.get("type", "unknown")
            b_types[t] = b_types.get(t, 0) + 1

        return jsonify({
            "topology_a": {"id": topo_a_id, "name": a_row["name"],
                           "nodes": len(ga.get("nodes", [])),
                           "edges": len(ga.get("edges", [])),
                           "types": a_types},
            "topology_b": {"id": topo_b_id, "name": b_row["name"],
                           "nodes": len(gb.get("nodes", [])),
                           "edges": len(gb.get("edges", [])),
                           "types": b_types},
            "nodes_only_a": only_a,
            "nodes_only_b": only_b,
            "nodes_common": len(common),
            "edges_only_a": len(a_ekeys - b_ekeys),
            "edges_only_b": len(b_ekeys - a_ekeys),
            "edges_common": len(a_ekeys & b_ekeys),
            "similarity_pct": round(
                len(common) * 100 / max(len(a_keys | b_keys), 1)
            ),
        })

    @bp.route("/projects/diff")
    @nc_login_required
    def nc_topology_diff_page():
        conn = get_connection()
        topos = [_row_to_dict(r) for r in conn.execute(
            "SELECT t.id, t.name, p.name AS project_name "
            "FROM topologies t "
            "LEFT JOIN nc_project_topologies pt "
            "  ON pt.topology_id=t.id "
            "LEFT JOIN nc_projects p ON p.id=pt.project_id "
            "ORDER BY t.name"
        ).fetchall()]
        conn.close()
        return render_template("network/diff.html", topologies=topos)

    # ── Auto-decompose to SAFe ────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/decompose", methods=["POST"])
    @nc_login_required
    def nc_api_decompose_to_safe(pid):
        """Auto-generate SAFe Feature + Stories from network project.
        Stores in nc_safe_bridge and returns the decomposition."""
        conn = get_connection()
        proj = conn.execute(
            "SELECT * FROM nc_projects WHERE id=?", (pid,)
        ).fetchone()
        if not proj:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        proj = _row_to_dict(proj)

        topos = [_row_to_dict(r) for r in conn.execute(
            "SELECT t.id, t.name, t.classification, "
            "json_array_length(json_extract(t.graph_json,'$.nodes')) "
            "  AS node_count "
            "FROM topologies t "
            "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            "WHERE pt.project_id=?", (pid,)
        ).fetchall()]
        circuits = [_row_to_dict(r) for r in conn.execute(
            "SELECT circuit_id, circuit_type, bandwidth "
            "FROM nc_circuits WHERE topology_id IN "
            "(SELECT topology_id FROM nc_project_topologies "
            " WHERE project_id=?)", (pid,)
        ).fetchall()]

        # Build SAFe hierarchy
        feature = {
            "level": "feature",
            "title": f"[Feature] {proj['name']}",
            "description": proj.get("description", ""),
            "t_shirt_size": "L" if len(topos) > 2 else "M"
                            if len(topos) > 0 else "S",
            "status": "draft",
        }
        stories = []
        for t in topos:
            size = "M" if (t.get("node_count") or 0) > 20 else "S"
            stories.append({
                "level": "story",
                "title": f"[Story] Implement {t['name']}",
                "description": f"Network topology: {t['name']} "
                               f"({t.get('node_count', 0)} nodes, "
                               f"{t.get('classification', 'public')})",
                "t_shirt_size": size,
                "source_topology_id": t["id"],
                "status": "draft",
            })
        enablers = []
        for c in circuits:
            enablers.append({
                "level": "enabler",
                "title": f"[Enabler] Provision {c['circuit_id']}",
                "description": f"{c.get('circuit_type', '')} "
                               f"{c.get('bandwidth', '')}",
                "t_shirt_size": "S",
                "status": "draft",
            })

        # WSJF scoring (simplified)
        tshirt_pts = {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8}
        feature["wsjf_score"] = round(
            7 / tshirt_pts.get(feature["t_shirt_size"], 3), 1
        )
        for s in stories:
            s["wsjf_score"] = round(
                5 / tshirt_pts.get(s["t_shirt_size"], 2), 1
            )
        for e in enablers:
            e["wsjf_score"] = round(
                4 / tshirt_pts.get(e["t_shirt_size"], 2), 1
            )

        # Update SAFe bridge
        bridge = conn.execute(
            "SELECT id FROM nc_safe_bridge WHERE project_id=?", (pid,)
        ).fetchone()
        now = _now()
        decomposition = {
            "feature": feature,
            "stories": stories,
            "enablers": enablers,
        }
        decomp_json = json.dumps(decomposition)
        if bridge:
            conn.execute(
                "UPDATE nc_safe_bridge SET safe_feature_id=?, "
                "updated_at=? WHERE project_id=?",
                (decomp_json, now, pid)
            )
        else:
            conn.execute(
                "INSERT INTO nc_safe_bridge "
                "(id, project_id, safe_feature_id, created_at, "
                " updated_at) VALUES (?,?,?,?,?)",
                (str(_uuid.uuid4()), pid, decomp_json, now, now)
            )
        conn.commit()

        _notify(conn, pid, "decomposition",
                f"SAFe decomposition: 1 Feature, {len(stories)} Stories, "
                f"{len(enablers)} Enablers",
                f"WSJF={feature['wsjf_score']}")
        conn.commit()
        conn.close()
        _audit("DECOMPOSE", "project", pid,
               f"{len(stories)} stories, {len(enablers)} enablers")

        return jsonify({
            "feature": feature,
            "stories": stories,
            "enablers": enablers,
            "total_items": 1 + len(stories) + len(enablers),
        })

    # ── Global Topology Canvas (JointJS read-only composite) ──────────────
    @bp.route("/global/canvas")
    @nc_login_required
    def nc_global_canvas():
        return render_template("network/global_canvas.html")

    @bp.route("/api/global-canvas-data", methods=["GET"])
    @nc_login_required
    def nc_api_global_canvas_data():
        """Return combined graph data for all approved/deployed projects."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT t.id, t.name, t.graph_json, "
            "p.id AS project_id, p.name AS project_name "
            "FROM topologies t "
            "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            "JOIN nc_projects p ON p.id=pt.project_id "
            "WHERE p.status IN ('approved','deployed') "
            "ORDER BY p.name, t.name"
        ).fetchall()
        interconnects = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_interconnects"
        ).fetchall()]
        conn.close()

        # Build composite graph with project-namespaced node IDs
        all_nodes = []
        all_edges = []
        project_groups = []
        offset_x = 0
        for r in rows:
            r = _row_to_dict(r)
            try:
                g = json.loads(r.get("graph_json") or "{}")
            except Exception:
                g = {"nodes": [], "edges": []}
            prefix = r["id"][:8]
            group_nodes = []
            for n in g.get("nodes", []):
                nid = f"{prefix}_{n['id']}"
                all_nodes.append({
                    "id": nid,
                    "label": n.get("label", ""),
                    "type": n.get("type", ""),
                    "x": (n.get("x", 0) or 0) + offset_x,
                    "y": n.get("y", 0) or 0,
                    "project": r["project_name"],
                    "topology": r["name"],
                })
                group_nodes.append(nid)
            for e in g.get("edges", []):
                all_edges.append({
                    "source": f"{prefix}_{e['source']}",
                    "target": f"{prefix}_{e['target']}",
                    "label": e.get("label", ""),
                    "protocol": e.get("protocol", ""),
                })
            project_groups.append({
                "project": r["project_name"],
                "topology": r["name"],
                "node_ids": group_nodes,
                "x": offset_x, "y": 0,
            })
            offset_x += 600

        # Add interconnect edges
        for ic in interconnects:
            all_edges.append({
                "source": f"ic_src_{ic['id'][:8]}",
                "target": f"ic_dst_{ic['id'][:8]}",
                "label": ic.get("circuit_id", ""),
                "protocol": ic.get("protocol", ""),
                "is_interconnect": True,
            })

        return jsonify({
            "nodes": all_nodes,
            "edges": all_edges,
            "groups": project_groups,
            "total_nodes": len(all_nodes),
            "total_edges": len(all_edges),
        })

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
        conn.execute(f"UPDATE nc_groups SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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
        conn.execute(f"UPDATE nc_boundaries SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
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

    _AI_TOPO_SYSTEM_PROMPT = """You are a network topology generator for a dark-themed canvas (navy #1a1a2e background). Output ONLY a valid JSON object — no markdown, no explanation, no code fences.

FORMAT:
{"nodes": [...], "edges": [...]}
Each node: {"id": "unique-id", "label": "Display Name", "type": "device-type", "x": number, "y": number, "config": {}}
Each edge: {"id": "unique-id", "source": "node-id", "target": "node-id", "label": "link label", "protocol": "protocol or empty"}

═══ DEVICE TYPES (use ONLY these) ═══
Physical:     router, switch-l2, switch-l3, firewall, load-balancer, wap, server, patch-panel
Endpoints:    endpoint-pc, endpoint-phone, endpoint-iot, endpoint-camera
Cloud:        cloud, aws-vpc, aws-tgw, aws-subnet, az-vnet, az-fw, gcp-vpc
Logical:      vrf, vlan, subnet, security-zone
Encryption:   kg-175d, kg-175g, kg-250, kg-340, type1-encryptor, fips-140-l2, fips-140-l3, hsm, macsec
Monitoring:   siem, sdwan-edge, sase-pop
SP/Carrier:   mpls-pe, mpls-p, route-reflector, pop, sonet-adm, roadm, oadm, edfa, transponder
Media:        media-fiber, media-ge, media-10ge, media-100ge
Colo:         meet-me-room, cross-connect
Drawing:      draw-rect, draw-rounded-rect, text-heading, text-label, text-badge

═══ MANDATORY STRUCTURE — follow this exact order in nodes array ═══

1. ZONE BOXES (draw-rect) — placed FIRST so they render behind devices
2. ZONE HEADINGS (text-heading) — placed ABOVE their zone box (zone_y - 25px)
3. BADGES (text-badge) — top of diagram for topology type name
4. DEVICES — inside their zone boxes, with realistic labels
5. ANNOTATION LABELS (text-label) — protocol/spec notes in clear space
6. LEGEND PANEL — ALWAYS include, on the RIGHT side of the diagram

═══ ZONE COLOR PALETTE (dark fills, bright borders) ═══
Blue:    {"_fill": "#0a1628", "_stroke": "#3498db", "_width": W, "_height": H}
Green:   {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": W, "_height": H}
Orange:  {"_fill": "#1a1500", "_stroke": "#f39c12", "_width": W, "_height": H}
Red:     {"_fill": "#1a0a0a", "_stroke": "#e74c3c", "_width": W, "_height": H}
Purple:  {"_fill": "#120a20", "_stroke": "#9b59b6", "_width": W, "_height": H}
Teal:    {"_fill": "#0a1a1a", "_stroke": "#00cec9", "_width": W, "_height": H}
Legend:  {"_fill": "#0f1520", "_stroke": "#636e72", "_width": 240, "_height": H}

text-heading config: {"_textColor": "<matching zone stroke color>"}
text-badge config:   {"_fill": "#0f3460", "_stroke": "#4a9eff"}
text-label config:   {"_textColor": "#7a8cb0"} (or matching color)

═══ LAYOUT RULES ═══
- Start at x=40, y=60. Leave 25px above zones for headings.
- Space devices 150-200px apart horizontally, 130-160px vertically between tiers.
- Zone boxes: 300-700px wide, 140-250px tall, with 40px padding around devices inside.
- Zone headings: positioned at (zone_x + 20, zone_y - 25) with _textColor matching zone _stroke.
- NEVER overlap text on text. Keep 30px vertical gap between text nodes.
- Use different zone colors for different functional areas (e.g., blue=network, green=customer, orange=core, red=security, purple=control plane).

═══ LEGEND (MANDATORY — always include) ═══
Place a legend panel to the RIGHT of the main diagram (max_device_x + 120).
Structure:
- draw-rect background: {"_fill": "#0f1520", "_stroke": "#636e72", "_width": 240, "_height": <calculated>}
- text-heading "Legend" at top
- PROTOCOLS section: list only protocols used, with color matching link colors:
  OSPF=#27ae60, iBGP=#85c1e9, eBGP=#3498db, MPLS=#ff9800, IPSec=#f7dc6f, mTLS=#fdcb6e, VXLAN=#00bcd4, BGP=#5dade2
- DEVICES section: list device types used with their colors:
  Router=#3498db, Switch=#27ae60, Firewall=#e94560, Server=#1abc9c, Cloud=#7f8c8d, WAP=#9b59b6, etc.
- ZONES section: list zones by color with their heading labels:
  "Blue = <heading text>", "Orange = <heading text>", etc.
Each legend entry: text-label with "• <description>" and appropriate _textColor.
Spacing: 22px between entries, 30px between sections.

═══ PROTOCOLS (use realistic ones) ═══
OSPF, BGP, iBGP, eBGP, MP-BGP, MPLS, LDP, RSVP, IPSec, mTLS, STP, VXLAN, BGP EVPN, GRE, SONET, OC-192, OLSR

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
                lines = [ln for ln in lines if not ln.strip().startswith("```")]
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

            # Apply deterministic style rules (zone ordering, label deconfliction, legend)
            try:
                from tools.network.topology_styler import style_topology
                graph_json = style_topology(graph_json)
            except Exception as style_err:
                logger.warning("Topology styler failed (non-fatal): %s", style_err)

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
    # Heatmap Overlay — device/link metric data
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/heatmap/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_heatmap(topo_id):
        """Return per-node and per-link metric values for heatmap overlay.

        Query param ``metric`` selects which metric:
          - bandwidth   — link utilisation %  (from configData.utilization)
          - vuln        — vulnerability severity (from STIG imports / compliance)
          - stig        — STIG compliance %  (from compliance findings)
          - age         — equipment age in years (from configData.install_date)
        """
        metric = request.args.get("metric", "bandwidth")
        conn = get_connection()
        topo = conn.execute(
            "SELECT id, graph_json FROM topologies WHERE id=?", (topo_id,),
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        node_values = {}  # nodeId -> 0..1 normalised score
        link_values = {}  # edgeId -> 0..1 normalised score

        if metric == "bandwidth":
            for e in edges:
                cfg = e.get("configData") or {}
                util = cfg.get("utilization")
                if util is not None:
                    try:
                        link_values[e["id"]] = max(0.0, min(1.0, float(util) / 100.0))
                    except (ValueError, TypeError):
                        link_values[e["id"]] = 0.0
                else:
                    link_values[e["id"]] = 0.0

        elif metric == "vuln":
            # Pull latest compliance findings for this topology
            rows = conn.execute(
                "SELECT affected_entity, severity, status FROM nc_compliance_findings "
                "WHERE topology_id=? AND status='open' "
                "ORDER BY created_at DESC",
                (topo_id,),
            ).fetchall()
            severity_weight = {"CAT1": 1.0, "CAT2": 0.6, "CAT3": 0.25}
            entity_max = {}
            for r in rows:
                eid = r["affected_entity"]
                w = severity_weight.get(r["severity"], 0.1)
                if eid not in entity_max or w > entity_max[eid]:
                    entity_max[eid] = w
            for n in nodes:
                nid = n["id"]
                label = n.get("label", "")
                val = entity_max.get(nid, entity_max.get(label, 0.0))
                node_values[nid] = val
            for e in edges:
                eid = e["id"]
                label = e.get("label", "")
                val = entity_max.get(eid, entity_max.get(label, 0.0))
                link_values[eid] = val

        elif metric == "stig":
            # STIG import results — per-host pass rate
            stig_rows = conn.execute(
                "SELECT result_json FROM nc_stig_imports "
                "WHERE topology_id=? ORDER BY imported_at DESC LIMIT 1",
                (topo_id,),
            ).fetchone()
            host_compliance = {}
            if stig_rows and stig_rows["result_json"]:
                try:
                    stig_data = json.loads(stig_rows["result_json"])
                    hosts = stig_data.get("hosts", {})
                    for hostname, hdata in hosts.items():
                        s = hdata.get("summary", {})
                        total = s.get("pass", 0) + s.get("fail", 0) + s.get("nr", 0)
                        if total > 0:
                            host_compliance[hostname.lower()] = s.get("pass", 0) / total
                        else:
                            host_compliance[hostname.lower()] = 1.0
                except (json.JSONDecodeError, AttributeError):
                    pass
            for n in nodes:
                nid = n["id"]
                label = (n.get("label") or "").lower()
                # 1.0 = fully compliant (green), 0.0 = no compliance (red)
                # Invert so red=bad: heatmap value 1.0 = worst
                compliance = host_compliance.get(label, 1.0)
                node_values[nid] = 1.0 - compliance

        elif metric == "age":
            now = datetime.now(timezone.utc)
            for n in nodes:
                cfg = n.get("config") or n.get("configData") or {}
                score = 0.0

                # Check EOL/EOS/EoSup dates first (most impactful)
                for date_key, weight in [
                    ("eol_date", 1.0),
                    ("eosup_date", 0.9),
                    ("eos_date", 0.7),
                ]:
                    dval = cfg.get(date_key)
                    if dval:
                        try:
                            dt = datetime.fromisoformat(
                                dval + "T00:00:00+00:00"
                                if "T" not in dval else
                                dval.replace("Z", "+00:00"))
                            if now >= dt:
                                score = max(score, weight)
                                pass  # past EOL/EOS/EoSup
                            else:
                                months_left = (dt - now).days / 30.44
                                if months_left < 6:
                                    score = max(
                                        score, weight * 0.8)
                                elif months_left < 12:
                                    score = max(
                                        score, weight * 0.5)
                                elif months_left < 24:
                                    score = max(
                                        score, weight * 0.2)
                        except (ValueError, TypeError):
                            pass

                # Fall back to install_date age
                if score == 0.0:
                    install_date = (cfg.get("install_date")
                                    or cfg.get("installDate"))
                    if install_date:
                        try:
                            dt = datetime.fromisoformat(
                                install_date + "T00:00:00+00:00"
                                if "T" not in install_date else
                                install_date.replace("Z", "+00:00"))
                            age_years = (now - dt).days / 365.25
                            score = max(
                                0.0, min(1.0, age_years / 10.0))
                            pass  # age computed
                        except (ValueError, TypeError):
                            pass

                node_values[n["id"]] = round(score, 3)

        conn.close()
        return jsonify({
            "metric": metric,
            "node_values": node_values,
            "link_values": link_values,
        })

    # ── Tech Debt Analysis ──────────────────────────────────────────────
    @bp.route("/api/tech-debt/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_tech_debt(topo_id):
        """Analyze lifecycle/tech debt across all devices in a topology."""
        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json, name FROM topologies WHERE id=?",
            (topo_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        now = datetime.now(timezone.utc)
        devices = []
        past_eol = 0
        past_eos = 0
        past_eosup = 0
        approaching_eol = 0
        no_lifecycle = 0

        for n in graph.get("nodes", []):
            cfg = n.get("config") or n.get("configData") or {}
            ntype = n.get("type", "")
            # Skip drawing shapes and text
            if ntype in ("rect", "circle", "text", "heading",
                         "badge", "hline", "vline", "arrow",
                         "diamond", "ellipse", "triangle",
                         "hexagon", "star", "roundedrect"):
                continue

            device = {
                "id": n.get("id"),
                "label": n.get("label", ""),
                "type": ntype,
                "model": cfg.get("model", ""),
                "serial": cfg.get("serial", ""),
                "sw_version": cfg.get("sw_version", ""),
                "install_date": cfg.get("install_date", ""),
                "eos_date": cfg.get("eos_date", ""),
                "eol_date": cfg.get("eol_date", ""),
                "eosup_date": cfg.get("eosup_date", ""),
                "risk_level": "unknown",
                "issues": [],
            }

            has_lifecycle = False
            for dkey, label in [
                ("eol_date", "End of Life"),
                ("eosup_date", "End of Support"),
                ("eos_date", "End of Sale"),
            ]:
                dval = cfg.get(dkey)
                if dval:
                    has_lifecycle = True
                    try:
                        dt = datetime.fromisoformat(
                            dval + "T00:00:00+00:00"
                            if "T" not in dval else
                            dval.replace("Z", "+00:00"))
                        if now >= dt:
                            device["issues"].append(
                                f"PAST {label}: {dval}")
                            if dkey == "eol_date":
                                past_eol += 1
                            elif dkey == "eos_date":
                                past_eos += 1
                            elif dkey == "eosup_date":
                                past_eosup += 1
                        else:
                            months = (dt - now).days / 30.44
                            if months < 12:
                                device["issues"].append(
                                    f"{label} in {int(months)} months: "
                                    f"{dval}")
                                if dkey == "eol_date":
                                    approaching_eol += 1
                    except (ValueError, TypeError):
                        pass

            install = cfg.get("install_date")
            if install:
                has_lifecycle = True
                try:
                    dt = datetime.fromisoformat(
                        install + "T00:00:00+00:00"
                        if "T" not in install else
                        install.replace("Z", "+00:00"))
                    age = (now - dt).days / 365.25
                    if age > 7:
                        device["issues"].append(
                            f"Equipment age: {age:.1f} years (>7yr)")
                except (ValueError, TypeError):
                    pass

            if not has_lifecycle:
                no_lifecycle += 1
                device["issues"].append("No lifecycle data configured")

            # Risk level
            if any("PAST End of Life" in i for i in device["issues"]):
                device["risk_level"] = "critical"
            elif any("PAST" in i for i in device["issues"]):
                device["risk_level"] = "high"
            elif any("months" in i for i in device["issues"]):
                device["risk_level"] = "medium"
            elif device["issues"]:
                device["risk_level"] = "low"
            else:
                device["risk_level"] = "healthy"

            devices.append(device)

        total = len(devices)
        critical = sum(1 for d in devices if d["risk_level"] == "critical")
        high = sum(1 for d in devices if d["risk_level"] == "high")

        return jsonify({
            "topology": row["name"],
            "total_devices": total,
            "devices": devices,
            "summary": {
                "past_eol": past_eol,
                "past_eos": past_eos,
                "past_eosup": past_eosup,
                "approaching_eol_12mo": approaching_eol,
                "no_lifecycle_data": no_lifecycle,
                "critical_risk": critical,
                "high_risk": high,
                "tech_debt_score": round(
                    (critical * 4 + high * 2 + approaching_eol) /
                    max(total, 1) * 25, 1
                ),
            },
        })

    # ── IPv6 Readiness Assessment ───────────────────────────────────────
    @bp.route("/api/ipv6-readiness/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_ipv6_readiness(topo_id):
        """Assess IPv6 readiness of a topology: device capability,
        addressing gaps, migration status."""
        conn = get_connection()
        row = conn.execute(
            "SELECT graph_json, name FROM topologies WHERE id=?",
            (topo_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "Not found"}), 404
        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        nodes = graph.get("nodes", [])
        devices = []
        capable_yes = capable_no = capable_partial = capable_unknown = 0
        has_v6_addr = 0
        has_v4_only = 0
        dual_stack = 0
        infra_types = {"router", "switch-l3", "switch-l2", "firewall",
                       "mpls-pe", "mpls-p", "route-reflector",
                       "load-balancer", "sdwan-edge", "wlc"}

        for n in nodes:
            ntype = n.get("type", "")
            if ntype not in infra_types:
                continue
            cfg = n.get("config") or n.get("configData") or {}
            cap = cfg.get("ipv6_capable", "")
            af = cfg.get("address_family", "")
            has_v6 = bool(cfg.get("ipv6"))
            has_v4 = bool(cfg.get("ip"))

            if cap == "yes":
                capable_yes += 1
            elif cap == "no":
                capable_no += 1
            elif cap == "partial":
                capable_partial += 1
            else:
                capable_unknown += 1

            if has_v6 and has_v4:
                dual_stack += 1
            elif has_v6:
                has_v6_addr += 1
            elif has_v4:
                has_v4_only += 1

            issues = []
            if cap == "no":
                issues.append("Device does NOT support IPv6 — "
                              "requires hardware/software upgrade")
            elif cap == "partial":
                issues.append("Limited IPv6 support — "
                              "verify feature set")
            elif cap == "":
                issues.append("IPv6 capability unknown — "
                              "verify and update device properties")
            if has_v4 and not has_v6 and af != "ipv4":
                issues.append("IPv4 configured but no IPv6 address — "
                              "add IPv6 for dual-stack")

            devices.append({
                "id": n.get("id"),
                "label": n.get("label", ""),
                "type": ntype,
                "ipv6_capable": cap or "unknown",
                "address_family": af or ("dual-stack" if has_v6 and has_v4
                                         else "ipv6" if has_v6
                                         else "ipv4" if has_v4
                                         else "none"),
                "has_ipv4": has_v4,
                "has_ipv6": has_v6,
                "issues": issues,
            })

        total = len(devices)
        ready_pct = round(
            capable_yes * 100 / max(total, 1)
        )
        dual_pct = round(
            dual_stack * 100 / max(total, 1)
        )

        # Migration recommendation
        if capable_no > 0:
            recommendation = (
                f"{capable_no} device(s) do not support IPv6 and "
                f"must be upgraded/replaced before migration. "
                f"Start with Phase 1: Assessment & Inventory."
            )
        elif capable_unknown > total / 2:
            recommendation = (
                f"{capable_unknown} device(s) have unknown IPv6 capability. "
                f"Audit all equipment and update the 'IPv6 Capable' "
                f"property before planning migration."
            )
        elif dual_stack < total:
            recommendation = (
                f"{total - dual_stack} device(s) not yet dual-stack. "
                f"Proceed with Phase 2: Enable dual-stack on core/distribution first."
            )
        else:
            recommendation = (
                "All infrastructure devices are dual-stack. "
                "Proceed to Phase 4: Access layer and endpoint rollout."
            )

        # Load IPv6 rules from design rules
        ipv6_rules = _design_rules.get("ipv6_rules", {})

        return jsonify({
            "topology": row["name"],
            "total_devices": total,
            "devices": devices,
            "summary": {
                "capable_yes": capable_yes,
                "capable_no": capable_no,
                "capable_partial": capable_partial,
                "capable_unknown": capable_unknown,
                "readiness_pct": ready_pct,
                "dual_stack_count": dual_stack,
                "dual_stack_pct": dual_pct,
                "ipv4_only_count": has_v4_only,
                "ipv6_only_count": has_v6_addr,
            },
            "recommendation": recommendation,
            "migration_phases": ipv6_rules.get(
                "migration_phases", {}),
            "transition_mechanisms": ipv6_rules.get(
                "transition_mechanisms", {}),
            "security_checklist": ipv6_rules.get("security", []),
        })

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

    # ══════════════════════════════════════════════════════════════════════
    # Intent-Based Validation — Page + API
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/intent/<topo_id>")
    @nc_login_required
    def nc_intent_page(topo_id):
        conn = get_connection()
        topo = conn.execute("SELECT id, name, graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            abort(404)
        policies = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_intent_policies WHERE topology_id=? ORDER BY created_at DESC",
            (topo_id,)
        ).fetchall()]
        for p in policies:
            p["constraints"] = [_row_to_dict(c) for c in conn.execute(
                "SELECT * FROM nc_intent_constraints WHERE policy_id=? AND is_active=1 ORDER BY created_at",
                (p["id"],)
            ).fetchall()]
            last_run = conn.execute(
                "SELECT * FROM nc_intent_validations WHERE policy_id=? ORDER BY ran_at DESC LIMIT 1",
                (p["id"],)
            ).fetchone()
            p["last_validation"] = _row_to_dict(last_run) if last_run else None
        conn.close()
        return render_template("network/intent_validation.html",
                               topology=_row_to_dict(topo),
                               policies=policies,
                               constraint_types=CONSTRAINT_TYPES)

    @bp.route("/api/intent/<topo_id>/policies", methods=["GET"])
    @nc_login_required
    def nc_api_intent_list_policies(topo_id):
        conn = get_connection()
        policies = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_intent_policies WHERE topology_id=? ORDER BY created_at DESC",
            (topo_id,)
        ).fetchall()]
        for p in policies:
            p["constraints"] = [_row_to_dict(c) for c in conn.execute(
                "SELECT * FROM nc_intent_constraints WHERE policy_id=? ORDER BY created_at",
                (p["id"],)
            ).fetchall()]
        conn.close()
        return jsonify({"policies": policies})

    @bp.route("/api/intent/<topo_id>/policies", methods=["POST"])
    @nc_login_required
    def nc_api_intent_create_policy(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "Policy name required"}), 400
        conn = get_connection()
        topo = conn.execute("SELECT id FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        pid = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_intent_policies (id, topology_id, name, description, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (pid, topo_id, name, data.get("description", ""), now, now)
        )
        conn.commit()
        conn.close()
        _audit("INTENT_POLICY_CREATE", "intent_policy", pid, name)
        return jsonify({"id": pid, "name": name}), 201

    @bp.route("/api/intent/<topo_id>/policies/<policy_id>", methods=["PUT"])
    @nc_login_required
    def nc_api_intent_update_policy(topo_id, policy_id):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        policy = conn.execute("SELECT id FROM nc_intent_policies WHERE id=? AND topology_id=?",
                              (policy_id, topo_id)).fetchone()
        if not policy:
            conn.close()
            return jsonify({"error": "Policy not found"}), 404
        fields, values = [], []
        for k in ["name", "description", "is_active"]:
            if k in data:
                fields.append(f"{k}=?")
                values.append(data[k])
        if fields:
            fields.append("updated_at=?")
            values.append(_now())
            values.append(policy_id)
            conn.execute(f"UPDATE nc_intent_policies SET {', '.join(fields)} WHERE id=?", values)  # nosec B608 -- table/column names are internal constants, not user input
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/intent/<topo_id>/policies/<policy_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_intent_delete_policy(topo_id, policy_id):
        conn = get_connection()
        policy = conn.execute("SELECT id FROM nc_intent_policies WHERE id=? AND topology_id=?",
                              (policy_id, topo_id)).fetchone()
        if not policy:
            conn.close()
            return jsonify({"error": "Policy not found"}), 404
        conn.execute("DELETE FROM nc_intent_constraints WHERE policy_id=?", (policy_id,))
        conn.execute("DELETE FROM nc_intent_validations WHERE policy_id=?", (policy_id,))
        conn.execute("DELETE FROM nc_intent_policies WHERE id=?", (policy_id,))
        conn.commit()
        conn.close()
        _audit("INTENT_POLICY_DELETE", "intent_policy", policy_id, "")
        return jsonify({"ok": True})

    @bp.route("/api/intent/<topo_id>/policies/<policy_id>/constraints", methods=["POST"])
    @nc_login_required
    def nc_api_intent_add_constraint(topo_id, policy_id):
        data = request.get_json(force=True, silent=True) or {}
        ctype = data.get("constraint_type", "")
        if ctype not in CONSTRAINT_TYPES:
            return jsonify({"error": f"Invalid constraint_type. Valid: {list(CONSTRAINT_TYPES.keys())}"}), 400
        conn = get_connection()
        policy = conn.execute("SELECT id FROM nc_intent_policies WHERE id=? AND topology_id=?",
                              (policy_id, topo_id)).fetchone()
        if not policy:
            conn.close()
            return jsonify({"error": "Policy not found"}), 404
        cid = str(_uuid.uuid4())
        rule_json = json.dumps(data.get("rule", {}))
        conn.execute(
            "INSERT INTO nc_intent_constraints (id, policy_id, constraint_type, severity, rule_json, description, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, policy_id, ctype, data.get("severity", "CAT2"), rule_json,
             data.get("description", ""), _now())
        )
        conn.commit()
        conn.close()
        _audit("INTENT_CONSTRAINT_ADD", "intent_constraint", cid, f"{ctype} on policy {policy_id}")
        return jsonify({"id": cid, "constraint_type": ctype}), 201

    @bp.route("/api/intent/<topo_id>/constraints/<constraint_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_intent_delete_constraint(topo_id, constraint_id):
        conn = get_connection()
        constraint = conn.execute(
            "SELECT c.id FROM nc_intent_constraints c "
            "JOIN nc_intent_policies p ON c.policy_id = p.id "
            "WHERE c.id=? AND p.topology_id=?", (constraint_id, topo_id)
        ).fetchone()
        if not constraint:
            conn.close()
            return jsonify({"error": "Constraint not found"}), 404
        conn.execute("DELETE FROM nc_intent_constraints WHERE id=?", (constraint_id,))
        conn.commit()
        conn.close()
        _audit("INTENT_CONSTRAINT_DELETE", "intent_constraint", constraint_id, "")
        return jsonify({"ok": True})

    @bp.route("/api/intent/<topo_id>/policies/<policy_id>/validate", methods=["POST"])
    @nc_login_required
    def nc_api_intent_validate(topo_id, policy_id):
        conn = get_connection()
        topo = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        policy = conn.execute("SELECT * FROM nc_intent_policies WHERE id=? AND topology_id=?",
                              (policy_id, topo_id)).fetchone()
        if not policy:
            conn.close()
            return jsonify({"error": "Policy not found"}), 404
        constraints = [_row_to_dict(c) for c in conn.execute(
            "SELECT * FROM nc_intent_constraints WHERE policy_id=? AND is_active=1",
            (policy_id,)
        ).fetchall()]
        if not constraints:
            conn.close()
            return jsonify({"error": "Policy has no active constraints"}), 400

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph"}), 500

        # Parse rule_json strings
        for c in constraints:
            if isinstance(c.get("rule_json"), str):
                try:
                    c["rule_json"] = json.loads(c["rule_json"])
                except Exception:
                    c["rule_json"] = {}

        result = validate_intent_policy(
            topology_id=topo_id,
            graph=graph,
            constraints=constraints,
            policy_name=policy["name"],
        )

        # Persist validation run
        vid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_intent_validations (id, topology_id, policy_id, total_constraints, "
            "passed, failed, violations_json, ran_at) VALUES (?,?,?,?,?,?,?,?)",
            (vid, topo_id, policy_id, result["total_constraints"], result["passed"],
             result["failed"], json.dumps(result["violations"]), _now())
        )
        conn.commit()
        conn.close()
        _audit("INTENT_VALIDATE", "intent_policy", policy_id,
               f"{result['status']}: {result['passed']}/{result['total_constraints']} passed")
        result["validation_id"] = vid
        return jsonify(result)

    @bp.route("/api/intent/<topo_id>/validate-all", methods=["POST"])
    @nc_login_required
    def nc_api_intent_validate_all(topo_id):
        """Validate topology against ALL active intent policies."""
        conn = get_connection()
        topo = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph"}), 500

        policies = conn.execute(
            "SELECT * FROM nc_intent_policies WHERE topology_id=? AND is_active=1",
            (topo_id,)
        ).fetchall()
        results = []
        now = _now()
        for pol in policies:
            constraints = [_row_to_dict(c) for c in conn.execute(
                "SELECT * FROM nc_intent_constraints WHERE policy_id=? AND is_active=1",
                (pol["id"],)
            ).fetchall()]
            if not constraints:
                continue
            for c in constraints:
                if isinstance(c.get("rule_json"), str):
                    try:
                        c["rule_json"] = json.loads(c["rule_json"])
                    except Exception:
                        c["rule_json"] = {}
            result = validate_intent_policy(topo_id, graph, constraints, pol["name"])
            vid = str(_uuid.uuid4())
            conn.execute(
                "INSERT INTO nc_intent_validations (id, topology_id, policy_id, total_constraints, "
                "passed, failed, violations_json, ran_at) VALUES (?,?,?,?,?,?,?,?)",
                (vid, topo_id, pol["id"], result["total_constraints"], result["passed"],
                 result["failed"], json.dumps(result["violations"]), now)
            )
            result["policy_id"] = pol["id"]
            result["validation_id"] = vid
            results.append(result)
        conn.commit()
        conn.close()
        total_pass = sum(r["passed"] for r in results)
        total_fail = sum(r["failed"] for r in results)
        overall = "PASS" if total_fail == 0 and results else ("FAIL" if results else "NO_POLICIES")
        return jsonify({
            "topology_id": topo_id,
            "overall_status": overall,
            "policies_validated": len(results),
            "total_passed": total_pass,
            "total_failed": total_fail,
            "results": results,
        })

    @bp.route("/api/intent/constraint-types", methods=["GET"])
    @nc_login_required
    def nc_api_intent_constraint_types():
        return jsonify({"constraint_types": CONSTRAINT_TYPES})

    # ══════════════════════════════════════════════════════════════════════
    # CHANGE REQUEST MARKUP MODE
    # ══════════════════════════════════════════════════════════════════════
    from tools.network.change_request import (  # noqa: E402
        generate_cr_document, validate_markup_item, ACTION_TYPES, CR_STATUSES,
    )

    @bp.route("/change-request/<topo_id>")
    @nc_login_required
    def nc_change_request_page(topo_id):
        conn = get_connection()
        topo = conn.execute("SELECT * FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            abort(404)
        topo = _row_to_dict(topo)
        try:
            graph = json.loads(topo.get("graph_json") or '{"nodes":[],"edges":[]}')
            if isinstance(graph, str):  # handle double-encoded JSON
                graph = json.loads(graph)
        except Exception:
            graph = {"nodes": [], "edges": []}
        crs = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, title, status, submitter_name, created_at, updated_at FROM nc_change_requests "
            "WHERE topology_id=? ORDER BY updated_at DESC",
            (topo_id,)
        ).fetchall()]
        conn.close()
        return render_template(
            "network/change_request.html",
            topology_id=topo_id,
            topology_name=topo["name"],
            topology_classification=topo.get("classification", "CUI // SP-CTI"),
            graph_nodes=graph.get("nodes", []),
            graph_edges=graph.get("edges", []),
            change_requests=crs,
            action_types=ACTION_TYPES,
            cr_statuses=list(CR_STATUSES),
        )

    @bp.route("/api/change-request/<topo_id>/list", methods=["GET"])
    @nc_login_required
    def nc_api_cr_list(topo_id):
        conn = get_connection()
        rows = [_row_to_dict(r) for r in conn.execute(
            "SELECT id, title, status, submitter_name, created_at, updated_at FROM nc_change_requests "
            "WHERE topology_id=? ORDER BY updated_at DESC",
            (topo_id,)
        ).fetchall()]
        conn.close()
        return jsonify({"change_requests": rows})

    @bp.route("/api/change-request/<topo_id>/create", methods=["POST"])
    @nc_login_required
    def nc_api_cr_create(topo_id):
        data = request.get_json() or {}
        title = (data.get("title") or "").strip()
        if not title:
            return jsonify({"error": "title is required"}), 400
        conn = get_connection()
        topo = conn.execute("SELECT id FROM topologies WHERE id=?", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "topology not found"}), 404
        cr_id = "cr-" + str(_uuid.uuid4())[:8]
        now = _now()
        conn.execute(
            "INSERT INTO nc_change_requests (id, topology_id, title, description, status, "
            "submitter_name, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (cr_id, topo_id, title, data.get("description", ""),
             "draft", session.get("username", ""), now, now),
        )
        conn.commit()
        _audit("CR_CREATE", "nc_change_requests", cr_id, f"topology={topo_id}")
        conn.close()
        return jsonify({"id": cr_id, "title": title, "status": "draft"}), 201

    @bp.route("/api/change-request/<cr_id>/items", methods=["GET"])
    @nc_login_required
    def nc_api_cr_items(cr_id):
        conn = get_connection()
        items = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_change_request_items WHERE cr_id=? ORDER BY created_at",
            (cr_id,)
        ).fetchall()]
        conn.close()
        return jsonify({"items": items})

    @bp.route("/api/change-request/<cr_id>/markup", methods=["POST"])
    @nc_login_required
    def nc_api_cr_markup(cr_id):
        """Add or update a markup item on a change request."""
        conn = get_connection()
        cr = conn.execute("SELECT * FROM nc_change_requests WHERE id=?", (cr_id,)).fetchone()
        if not cr:
            conn.close()
            return jsonify({"error": "change request not found"}), 404
        cr = _row_to_dict(cr)
        if cr["status"] not in ("draft",):
            conn.close()
            return jsonify({"error": f"Cannot add markup to a CR in '{cr['status']}' status"}), 409

        data = request.get_json() or {}
        errors = validate_markup_item(data)
        if errors:
            conn.close()
            return jsonify({"error": "validation failed", "details": errors}), 400

        item_id = data.get("item_id")
        now = _now()
        if item_id:
            # Update existing item
            existing = conn.execute(
                "SELECT id FROM nc_change_request_items WHERE id=? AND cr_id=?",
                (item_id, cr_id)
            ).fetchone()
            if not existing:
                conn.close()
                return jsonify({"error": "item not found"}), 404
            conn.execute(
                "UPDATE nc_change_request_items SET action_type=?, entity_label=?, "
                "before_json=?, after_json=?, justification=? WHERE id=?",
                (
                    data["action_type"],
                    data.get("entity_label", ""),
                    json.dumps(data.get("before_json") or {}),
                    json.dumps(data.get("after_json") or {}),
                    data.get("justification", ""),
                    item_id,
                ),
            )
        else:
            item_id = "cri-" + str(_uuid.uuid4())[:8]
            conn.execute(
                "INSERT INTO nc_change_request_items "
                "(id, cr_id, topology_id, action_type, entity_id, entity_type, entity_label, "
                "before_json, after_json, justification, created_by, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id, cr_id, cr["topology_id"],
                    data["action_type"],
                    data["entity_id"],
                    data.get("entity_type", "node"),
                    data.get("entity_label", data["entity_id"]),
                    json.dumps(data.get("before_json") or {}),
                    json.dumps(data.get("after_json") or {}),
                    data.get("justification", ""),
                    session.get("username", ""),
                    now,
                ),
            )
        # Bump CR updated_at
        conn.execute(
            "UPDATE nc_change_requests SET updated_at=? WHERE id=?", (now, cr_id)
        )
        conn.commit()
        conn.close()
        return jsonify({"id": item_id, "action_type": data["action_type"]}), 201

    @bp.route("/api/change-request/item/<item_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_cr_item_delete(item_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT cri.id, cri.cr_id, cr.status FROM nc_change_request_items cri "
            "JOIN nc_change_requests cr ON cr.id=cri.cr_id WHERE cri.id=?",
            (item_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "item not found"}), 404
        row = _row_to_dict(row)
        if row["status"] != "draft":
            conn.close()
            return jsonify({"error": "Cannot delete item from a non-draft CR"}), 409
        conn.execute("DELETE FROM nc_change_request_items WHERE id=?", (item_id,))
        conn.execute(
            "UPDATE nc_change_requests SET updated_at=? WHERE id=?", (_now(), row["cr_id"])
        )
        conn.commit()
        conn.close()
        return jsonify({"deleted": item_id})

    @bp.route("/api/change-request/<cr_id>/generate", methods=["POST"])
    @nc_login_required
    def nc_api_cr_generate(cr_id):
        """Generate the CAB review document for a change request."""
        conn = get_connection()
        cr = conn.execute("SELECT * FROM nc_change_requests WHERE id=?", (cr_id,)).fetchone()
        if not cr:
            conn.close()
            return jsonify({"error": "change request not found"}), 404
        cr = _row_to_dict(cr)
        topo = conn.execute(
            "SELECT name, classification FROM topologies WHERE id=?", (cr["topology_id"],)
        ).fetchone()
        topo = _row_to_dict(topo) if topo else {"name": "Unknown", "classification": "CUI // SP-CTI"}
        items = [_row_to_dict(r) for r in conn.execute(
            "SELECT * FROM nc_change_request_items WHERE cr_id=? ORDER BY action_type, created_at",
            (cr_id,)
        ).fetchall()]
        conn.close()

        doc = generate_cr_document(
            cr=cr,
            items=items,
            topology_name=topo["name"],
            topology_classification=topo.get("classification") or "CUI // SP-CTI",
        )

        # Persist document JSON back to CR row
        conn = get_connection()
        conn.execute(
            "UPDATE nc_change_requests SET document_json=?, updated_at=? WHERE id=?",
            (json.dumps(doc), _now(), cr_id),
        )
        conn.commit()
        _audit("CR_GENERATE", "nc_change_requests", cr_id, f"items={len(items)}")
        conn.close()
        return jsonify(doc)

    @bp.route("/api/change-request/<cr_id>/document", methods=["GET"])
    @nc_login_required
    def nc_api_cr_document(cr_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT document_json FROM nc_change_requests WHERE id=?", (cr_id,)
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"error": "change request not found"}), 404
        try:
            doc = json.loads(row[0] or "{}")
        except Exception:
            doc = {}
        if not doc:
            return jsonify({"error": "Document not yet generated. Call /generate first."}), 404
        return jsonify(doc)

    @bp.route("/api/change-request/<cr_id>/status", methods=["PUT"])
    @nc_login_required
    def nc_api_cr_status(cr_id):
        data = request.get_json() or {}
        new_status = data.get("status", "").lower()
        if new_status not in CR_STATUSES:
            return jsonify({"error": f"status must be one of: {', '.join(CR_STATUSES)}"}), 400
        conn = get_connection()
        now = _now()
        extra = {}
        if new_status == "submitted":
            extra["submitted_at"] = now
            extra["submitter_name"] = data.get("submitter_name") or session.get("username", "")
        set_clauses = "status=?, updated_at=?"
        params: list = [new_status, now]
        if extra.get("submitted_at"):
            set_clauses += ", submitted_at=?, submitter_name=?"
            params += [extra["submitted_at"], extra["submitter_name"]]
        params.append(cr_id)
        result = conn.execute(
            f"UPDATE nc_change_requests SET {set_clauses} WHERE id=?", params  # nosec B608 -- table/column names are internal constants, not user input
        )
        conn.commit()
        if result.rowcount == 0:
            conn.close()
            return jsonify({"error": "change request not found"}), 404
        _audit("CR_STATUS", "nc_change_requests", cr_id, f"new_status={new_status}")
        conn.close()
        return jsonify({"id": cr_id, "status": new_status})

    @bp.route("/api/change-request/<cr_id>/delete", methods=["DELETE"])
    @nc_login_required
    def nc_api_cr_delete(cr_id):
        conn = get_connection()
        row = conn.execute(
            "SELECT id, status FROM nc_change_requests WHERE id=?", (cr_id,)
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "change request not found"}), 404
        if _row_to_dict(row)["status"] not in ("draft", "withdrawn"):
            conn.close()
            return jsonify({"error": "Only draft or withdrawn CRs can be deleted"}), 409
        conn.execute("DELETE FROM nc_change_request_items WHERE cr_id=?", (cr_id,))
        conn.execute("DELETE FROM nc_change_requests WHERE id=?", (cr_id,))
        conn.commit()
        conn.close()
        return jsonify({"deleted": cr_id})

    # ── NetBox IPAM Integration ────────────────────────────────────────────

    def _netbox_client_from_db():
        """Build a NetBoxClient from stored config. Raises ValueError if not configured."""
        from tools.network.netbox_client import NetBoxClient
        conn = get_connection()
        row = conn.execute(
            "SELECT url, token, site_filter, timeout_sec FROM nc_netbox_config WHERE id='default'"
        ).fetchone()
        conn.close()
        if not row or not row[0] or not row[1]:
            raise ValueError("NetBox not configured. POST /api/netbox/configure first.")
        cfg = _row_to_dict(row)
        return NetBoxClient(url=cfg["url"], token=cfg["token"], timeout=cfg.get("timeout_sec") or 15), cfg.get("site_filter") or None

    def _log_netbox_sync(direction, resource, topology_id, status, records_in, records_out, error_msg=None):
        conn = get_connection()
        conn.execute(
            "INSERT INTO nc_netbox_sync_log (id, direction, resource, topology_id, status, records_in, records_out, error_msg) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (_uuid.uuid4().hex, direction, resource, topology_id, status, records_in, records_out, error_msg),
        )
        conn.commit()
        conn.close()

    @bp.route("/api/netbox/configure", methods=["POST"])
    @nc_login_required
    def nc_api_netbox_configure():
        """Save NetBox connection settings."""
        data = request.get_json() or {}
        url = (data.get("url") or "").rstrip("/")
        token = data.get("token") or ""
        if not url or not token:
            return jsonify({"error": "url and token are required"}), 400
        site_filter = data.get("site_filter", "")
        timeout_sec = int(data.get("timeout_sec") or 15)
        auto_sync = 1 if data.get("auto_sync") else 0
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO nc_netbox_config (id, url, token, site_filter, timeout_sec, auto_sync, updated_at) "
            "VALUES ('default',?,?,?,?,?,?)",
            (url, token, site_filter, timeout_sec, auto_sync, _now()),
        )
        conn.commit()
        _audit("NETBOX_CONFIGURE", "nc_netbox_config", "default", f"url={url}")
        conn.close()
        return jsonify({"ok": True, "url": url})

    @bp.route("/api/netbox/config", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_config_get():
        """Return current NetBox config (token redacted)."""
        conn = get_connection()
        row = conn.execute(
            "SELECT url, site_filter, timeout_sec, auto_sync, last_tested FROM nc_netbox_config WHERE id='default'"
        ).fetchone()
        conn.close()
        if not row:
            return jsonify({"configured": False})
        cfg = _row_to_dict(row)
        cfg["configured"] = bool(cfg.get("url"))
        return jsonify(cfg)

    @bp.route("/api/netbox/status", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_status():
        """Test NetBox connectivity and return version info."""
        try:
            client, _ = _netbox_client_from_db()
            result = client.test_connection()
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 502
        # Record last successful test timestamp
        conn = get_connection()
        conn.execute("UPDATE nc_netbox_config SET last_tested=? WHERE id='default'", (_now(),))
        conn.commit()
        conn.close()
        return jsonify(result)

    @bp.route("/api/netbox/pull/devices", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_pull_devices():
        """Pull device inventory from NetBox."""
        try:
            client, site = _netbox_client_from_db()
            devices = client.get_devices(site=site)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            _log_netbox_sync("pull", "devices", None, "error", 0, 0, str(exc))
            return jsonify({"error": str(exc)}), 502
        _log_netbox_sync("pull", "devices", None, "ok", len(devices), len(devices))
        return jsonify({"devices": devices, "count": len(devices)})

    @bp.route("/api/netbox/pull/ips", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_pull_ips():
        """Pull IP address allocations from NetBox."""
        try:
            client, _ = _netbox_client_from_db()
            ips = client.get_ip_addresses()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            _log_netbox_sync("pull", "ip-addresses", None, "error", 0, 0, str(exc))
            return jsonify({"error": str(exc)}), 502
        _log_netbox_sync("pull", "ip-addresses", None, "ok", len(ips), len(ips))
        return jsonify({"ip_addresses": ips, "count": len(ips)})

    @bp.route("/api/netbox/pull/vlans", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_pull_vlans():
        """Pull VLANs from NetBox."""
        try:
            client, site = _netbox_client_from_db()
            vlans = client.get_vlans(site=site)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            _log_netbox_sync("pull", "vlans", None, "error", 0, 0, str(exc))
            return jsonify({"error": str(exc)}), 502
        _log_netbox_sync("pull", "vlans", None, "ok", len(vlans), len(vlans))
        return jsonify({"vlans": vlans, "count": len(vlans)})

    @bp.route("/api/netbox/pull/prefixes", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_pull_prefixes():
        """Pull IP prefixes/subnets from NetBox."""
        try:
            client, _ = _netbox_client_from_db()
            prefixes = client.get_prefixes()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            _log_netbox_sync("pull", "prefixes", None, "error", 0, 0, str(exc))
            return jsonify({"error": str(exc)}), 502
        _log_netbox_sync("pull", "prefixes", None, "ok", len(prefixes), len(prefixes))
        return jsonify({"prefixes": prefixes, "count": len(prefixes)})

    @bp.route("/api/netbox/pull/racks", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_pull_racks():
        """Pull rack layouts from NetBox."""
        try:
            client, site = _netbox_client_from_db()
            racks = client.get_racks(site=site)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            _log_netbox_sync("pull", "racks", None, "error", 0, 0, str(exc))
            return jsonify({"error": str(exc)}), 502
        _log_netbox_sync("pull", "racks", None, "ok", len(racks), len(racks))
        return jsonify({"racks": racks, "count": len(racks)})

    @bp.route("/api/netbox/pull/circuits", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_pull_circuits():
        """Pull circuits from NetBox."""
        try:
            client, _ = _netbox_client_from_db()
            circuits = client.get_circuits()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            _log_netbox_sync("pull", "circuits", None, "error", 0, 0, str(exc))
            return jsonify({"error": str(exc)}), 502
        _log_netbox_sync("pull", "circuits", None, "ok", len(circuits), len(circuits))
        return jsonify({"circuits": circuits, "count": len(circuits)})

    @bp.route("/api/netbox/import/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_netbox_import(topo_id):
        """Full NetBox import: pull all resources and merge into topology graph.

        Body (JSON, all optional):
          resource: "devices" | "ips" | "vlans" | "racks" | "circuits" | "all" (default "all")
          merge: true | false — if false, replaces existing graph (default true)
        """
        from tools.network.netbox_client import (
            devices_to_canvas_nodes, prefixes_to_ipam_blocks, circuits_to_nc_circuits,
        )
        data = request.get_json() or {}
        resource = data.get("resource", "all")
        merge = data.get("merge", True)

        conn = get_connection()
        topo_row = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not topo_row:
            return jsonify({"error": "topology not found"}), 404

        try:
            client, site = _netbox_client_from_db()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        try:
            if resource == "all":
                pulled = client.pull_all(site=site)
            elif resource == "devices":
                pulled = {"devices": client.get_devices(site=site)}
            elif resource == "ips":
                pulled = {"ip_addresses": client.get_ip_addresses()}
            elif resource == "vlans":
                pulled = {"vlans": client.get_vlans(site=site)}
            elif resource == "prefixes":
                pulled = {"prefixes": client.get_prefixes()}
            elif resource == "racks":
                pulled = {"racks": client.get_racks(site=site)}
            elif resource == "circuits":
                pulled = {"circuits": client.get_circuits()}
            else:
                return jsonify({"error": f"unknown resource: {resource}"}), 400
        except Exception as exc:  # noqa: BLE001
            _log_netbox_sync("pull", resource, topo_id, "error", 0, 0, str(exc))
            return jsonify({"error": str(exc)}), 502

        # Merge/replace graph nodes
        existing_graph = json.loads(topo_row[0] or '{"nodes":[],"edges":[]}')
        new_nodes = devices_to_canvas_nodes(pulled.get("devices", []))
        if merge:
            # Keep existing nodes that are NOT from NetBox; append new ones
            existing_nb_ids = {n.get("netbox_id") for n in existing_graph.get("nodes", []) if n.get("netbox_id")}
            filtered_existing = [n for n in existing_graph.get("nodes", []) if not n.get("netbox_id")]
            deduped_new = [n for n in new_nodes if n.get("netbox_id") not in existing_nb_ids]
            merged_nodes = filtered_existing + [n for n in existing_graph.get("nodes", []) if n.get("netbox_id")] + deduped_new
            existing_graph["nodes"] = merged_nodes
        else:
            existing_graph["nodes"] = new_nodes
            existing_graph["edges"] = []

        # Persist updated graph
        conn = get_connection()
        conn.execute(
            "UPDATE topologies SET graph_json=?, updated_at=? WHERE id=?",
            (json.dumps(existing_graph), _now(), topo_id),
        )

        # Persist IPAM blocks from prefixes
        prefixes = pulled.get("prefixes", [])
        if prefixes:
            blocks = prefixes_to_ipam_blocks(prefixes)
            for blk in blocks:
                conn.execute(
                    "INSERT OR IGNORE INTO nc_ipam_blocks (id, topology_id, network, vlan_id, vrf, description) "
                    "VALUES (?,?,?,?,?,?)",
                    (blk["id"], topo_id, blk["network"], blk.get("vlan_id"), blk.get("vrf", "global"), blk.get("description", "")),
                )

        # Persist circuits
        circuits = pulled.get("circuits", [])
        if circuits:
            nc_circs = circuits_to_nc_circuits(circuits, topo_id)
            for c in nc_circs:
                conn.execute(
                    "INSERT OR IGNORE INTO nc_circuits (id, topology_id, circuit_id, carrier, circuit_type, bandwidth, handoff_a, handoff_z, install_status) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (c["id"], c["topology_id"], c["circuit_id"], c.get("carrier", ""), c.get("circuit_type", ""),
                     c.get("bandwidth", ""), c.get("handoff_a", ""), c.get("handoff_z", ""), c.get("install_status", "active")),
                )

        # Record object ID mappings
        for node in new_nodes:
            nb_id = node.get("netbox_id")
            if nb_id:
                existing_map = conn.execute(
                    "SELECT id FROM nc_netbox_objects WHERE topology_id=? AND netbox_id=? AND netbox_resource='device'",
                    (topo_id, nb_id),
                ).fetchone()
                if not existing_map:
                    conn.execute(
                        "INSERT INTO nc_netbox_objects (id, topology_id, netbox_id, netbox_resource, canvas_node_id) "
                        "VALUES (?,?,?,?,?)",
                        (_uuid.uuid4().hex, topo_id, nb_id, "device", node["id"]),
                    )

        conn.commit()
        _audit("NETBOX_IMPORT", "topologies", topo_id, f"resource={resource} nodes={len(new_nodes)}")
        records_out = len(new_nodes) + len(prefixes) + len(circuits)
        records_in = sum(len(v) for v in pulled.values() if isinstance(v, list))
        conn.close()
        _log_netbox_sync("pull", resource, topo_id, "ok", records_in, records_out)

        return jsonify({
            "ok": True,
            "topology_id": topo_id,
            "nodes_added": len(new_nodes),
            "ipam_blocks": len(prefixes),
            "circuits": len(circuits),
            "graph": existing_graph,
        })

    @bp.route("/api/netbox/push/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_netbox_push(topo_id):
        """Push canvas topology nodes back to NetBox as devices.

        Only pushes nodes that were NOT originally imported from NetBox
        (no netbox_id) or where the user explicitly requests a push.
        """
        data = request.get_json() or {}
        site_id = data.get("site_id")  # NetBox site numeric ID for new devices
        push_all = data.get("push_all", False)  # also push existing NetBox nodes

        conn = get_connection()
        topo_row = conn.execute("SELECT graph_json FROM topologies WHERE id=?", (topo_id,)).fetchone()
        conn.close()
        if not topo_row:
            return jsonify({"error": "topology not found"}), 404

        try:
            client, _ = _netbox_client_from_db()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        graph = json.loads(topo_row[0] or '{"nodes":[],"edges":[]}')
        nodes_to_push = [
            n for n in graph.get("nodes", [])
            if push_all or not n.get("netbox_id")
        ]

        pushed, errors = [], []
        for node in nodes_to_push:
            try:
                result = client.push_device(node, site_id=site_id)
                nb_id = result.get("id")
                if nb_id:
                    # Update node in graph with new NetBox ID
                    for n in graph["nodes"]:
                        if n["id"] == node["id"]:
                            n["netbox_id"] = nb_id
                            n["netbox_url"] = result.get("url", "")
                    pushed.append({"canvas_id": node["id"], "netbox_id": nb_id, "label": node.get("label")})
            except Exception as exc:  # noqa: BLE001
                errors.append({"canvas_id": node["id"], "label": node.get("label"), "error": str(exc)})

        # Persist updated graph with new netbox_ids
        if pushed:
            conn = get_connection()
            conn.execute(
                "UPDATE topologies SET graph_json=?, updated_at=? WHERE id=?",
                (json.dumps(graph), _now(), topo_id),
            )
            conn.commit()
            conn.close()

        _audit("NETBOX_PUSH", "topologies", topo_id, f"pushed={len(pushed)} errors={len(errors)}")
        _log_netbox_sync("push", "devices", topo_id,
                         "ok" if not errors else "error",
                         len(nodes_to_push), len(pushed),
                         "; ".join(e["error"] for e in errors[:3]) if errors else None)

        return jsonify({
            "ok": len(errors) == 0,
            "pushed": pushed,
            "errors": errors,
            "topology_id": topo_id,
        })

    @bp.route("/api/netbox/sync-log", methods=["GET"])
    @nc_login_required
    def nc_api_netbox_sync_log():
        """Return recent NetBox sync history."""
        limit = int(request.args.get("limit", 50))
        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM nc_netbox_sync_log ORDER BY ran_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return jsonify({"log": [_row_to_dict(r) for r in rows]})

    # ══════════════════════════════════════════════════════════════════════
    # API: AI Topology Reviewer
    # ══════════════════════════════════════════════════════════════════════

    _AI_REVIEW_SYSTEM_PROMPT = """You are a senior network security engineer reviewing a network topology for a US Government system (DoD/FedRAMP).

Analyze the provided topology and return ONLY a valid JSON object with this exact structure — no markdown, no explanation:

{
  "summary": "2-3 sentence plain-English overview of topology health",
  "findings": [
    {
      "id": 1,
      "severity": "HIGH",
      "category": "SPOF",
      "title": "Short title (under 60 chars)",
      "description": "Plain-English explanation of the issue",
      "node_ids": ["affected-node-id-1"],
      "suggestion": "Specific remediation step"
    }
  ]
}

Severity levels: HIGH (immediate risk), MEDIUM (should fix), LOW (best practice), INFO (observation)

Categories:
- SPOF: Single point of failure — device or link whose removal disconnects the network
- REDUNDANCY: Missing redundant paths, dual-homing, or HSRP/VRRP failover
- STIG: STIG violations — unencrypted WAN/cross-zone links, missing firewall between zones, Telnet/HTTP, no management VLAN isolation
- ENCRYPTION: Missing FIPS-140 encryption on sensitive flows, unencrypted inter-enclave links
- SEGMENTATION: Missing firewall between trust zones, flat network, no DMZ separation
- ROUTING: Suboptimal routing (single routing protocol, no route redistribution controls, missing summarization)
- SUGGESTION: General best-practice improvement

Rules:
- SPOF: flag any node that is the sole connection between two or more subnets/zones
- REDUNDANCY: critical devices (router, firewall, core switch) with only 1 uplink edge
- STIG: any WAN/internet-facing link not using IPSec/mTLS/type1-encryptor; any server reachable from internet without firewall
- node_ids must match IDs from the topology JSON exactly; use [] if not device-specific
- Limit findings to 15 maximum; prioritize HIGH and MEDIUM
- If topology has fewer than 2 nodes, return summary="Topology too small to review" and findings=[]

Output ONLY the JSON object."""

    @bp.route("/api/ai-review/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_ai_review(topo_id):
        """Feed topology to LLM and return annotated findings (SPOF, redundancy, STIG, etc.)."""
        import re
        import requests as _req

        conn = get_connection()
        topo = conn.execute(
            "SELECT id, name, graph_json, classification FROM topologies WHERE id=?",
            (topo_id,),
        ).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Invalid graph JSON"}), 500
        conn.close()

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Build compact topology description for the LLM (token-efficient)
        node_lines = []
        for n in nodes:
            cfg = n.get("config", {})
            meta = []
            for k in ("ip", "asn", "vlan", "vrf", "ospf_area"):
                if cfg.get(k):
                    meta.append(f"{k}={cfg[k]}")
            line = f'  {{"id":"{n["id"]}","label":"{n.get("label","")}","type":"{n.get("type","")}"'
            if meta:
                line += f',"meta":"{",".join(meta)}"'
            line += "}"
            node_lines.append(line)

        edge_lines = []
        for e in edges:
            proto = e.get("protocol") or e.get("label") or ""
            encrypted = e.get("_encrypted") or e.get("config", {}).get("_encrypted", False)
            line = (
                f'  {{"id":"{e["id"]}","src":"{e.get("source","")}","dst":"{e.get("target","")}","proto":"{proto}"'
                + (',"encrypted":true' if encrypted else "")
                + "}"
            )
            edge_lines.append(line)

        topo_text = (
            f'Topology: "{topo["name"]}" | Classification: {topo["classification"] or "CUI"}\n'
            f"Nodes ({len(nodes)}):\n" + "\n".join(node_lines) + "\n"
            f"Edges ({len(edges)}):\n" + "\n".join(edge_lines)
        )

        def _parse_review(content):
            text = content.strip()
            text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
            if text.startswith("```"):
                lines = [ln for ln in text.split("\n") if not ln.strip().startswith("```")]
                text = "\n".join(lines).strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start < 0 or end <= start:
                return None
            return json.loads(text[start:end])

        def _call_claude(prompt):
            api_key = os.environ.get("ANTHROPIC_API_KEY", "")
            if not api_key:
                return None, "No ANTHROPIC_API_KEY"
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
                    "temperature": 0.1,
                    "system": _AI_REVIEW_SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=90,
            )
            r.raise_for_status()
            return r.json().get("content", [{}])[0].get("text", ""), None

        def _call_ollama(prompt):
            ollama_url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
            ollama_model = os.environ.get("OLLAMA_TOPO_MODEL", "llama3.2:3b")
            r = _req.post(
                f"{ollama_url}/api/chat",
                json={
                    "model": ollama_model,
                    "messages": [
                        {"role": "system", "content": _AI_REVIEW_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"num_predict": 4096, "temperature": 0.1},
                },
                timeout=120,
            )
            r.raise_for_status()
            return r.json().get("message", {}).get("content", ""), None

        try:
            provider = os.environ.get("NC_AI_PROVIDER", "auto")
            content = None
            used_provider = ""

            if provider in ("auto", "claude"):
                content, err = _call_claude(topo_text)
                if content:
                    used_provider = "claude"
                elif provider == "claude":
                    return jsonify({"error": f"Claude failed: {err}"}), 503

            if not content and provider in ("auto", "ollama"):
                content, err = _call_ollama(topo_text)
                if content:
                    used_provider = "ollama"
                elif provider == "ollama":
                    return jsonify({"error": f"Ollama failed: {err}"}), 503

            if not content:
                return jsonify({"error": "No LLM provider available"}), 503

            result = _parse_review(content)
            if result is None:
                return jsonify({"error": "LLM did not return valid JSON", "raw": content[:500]}), 422

            findings = result.get("findings", [])
            # Validate node_ids exist in topology
            valid_node_ids = {n["id"] for n in nodes}
            for f in findings:
                f["node_ids"] = [nid for nid in f.get("node_ids", []) if nid in valid_node_ids]

            _audit("AI_REVIEW", "topology", topo_id,
                   f"[{used_provider}] {len(findings)} findings | "
                   f"HIGH:{sum(1 for f in findings if f.get('severity')=='HIGH')} "
                   f"MED:{sum(1 for f in findings if f.get('severity')=='MEDIUM')}")

            return jsonify({
                "topology_id": topo_id,
                "topology_name": topo["name"],
                "summary": result.get("summary", ""),
                "findings": findings,
                "finding_count": len(findings),
                "provider": used_provider,
            })

        except _req.exceptions.ConnectionError:
            return jsonify({"error": "Cannot connect to LLM provider"}), 503
        except _req.exceptions.Timeout:
            return jsonify({"error": "LLM timed out"}), 504
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"JSON parse error: {exc}"}), 422
        except Exception as exc:
            logger.exception("AI review failed")
            return jsonify({"error": str(exc)}), 500

    # ── Auto-Discovery ────────────────────────────────────────────────────

    @bp.route("/discovery")
    @nc_login_required
    def nc_discovery_page():
        """Discovery dashboard page."""
        with get_connection() as conn:
            scans = conn.execute(
                "SELECT * FROM nc_discovery_scans ORDER BY created_at DESC LIMIT 50"
            ).fetchall()
            topos = conn.execute(
                "SELECT id, name FROM topologies ORDER BY name"
            ).fetchall()
        return render_template("network/discovery.html",
                               scans=[_row_to_dict(s) for s in scans],
                               topologies=[_row_to_dict(t) for t in topos],
                               has_pysnmp=_HAS_PYSNMP,
                               has_netmiko=_HAS_NETMIKO)

    @bp.route("/api/discovery/scan", methods=["POST"])
    @nc_login_required
    def nc_api_discovery_scan():
        """Launch a network discovery scan."""
        data = request.get_json(force=True)
        targets = data.get("targets", [])
        if isinstance(targets, str):
            targets = [t.strip() for t in targets.split(",") if t.strip()]
        if not targets:
            return jsonify({"error": "targets required"}), 400

        method = data.get("method", "snmp")
        scan_id = str(_uuid.uuid4())
        name = data.get("name", f"Scan {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}")
        topology_id = data.get("topology_id")

        config = {
            "community": data.get("community", "public"),
            "username": data.get("username", ""),
            "device_type": data.get("device_type", "cisco_ios"),
            "port": data.get("port", 0),
            "timeout": data.get("timeout", 2.0),
            "hop_limit": data.get("hop_limit", 2),
            "layout": data.get("layout", "grid"),
        }

        # Save scan record as pending
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO nc_discovery_scans "
                "(id, topology_id, name, method, targets, config_json, status, started_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (scan_id, topology_id, name, method,
                 json.dumps(targets), json.dumps(config),
                 "running", _now()),
            )

        # Run discovery (synchronous for now — small scans)
        try:
            result = run_discovery(
                targets=targets,
                method=method,
                community=config["community"],
                username=config["username"],
                password=data.get("password", ""),
                device_type=config["device_type"],
                port=config["port"],
                timeout=config["timeout"],
                layout=config["layout"],
                hop_limit=config["hop_limit"],
            )
            with get_connection() as conn:
                conn.execute(
                    "UPDATE nc_discovery_scans SET status=?, devices_json=?, "
                    "graph_json=?, stats_json=?, completed_at=? WHERE id=?",
                    ("completed", json.dumps(result["devices"], default=str),
                     json.dumps(result["graph_json"], default=str),
                     json.dumps(result["stats"], default=str),
                     _now(), scan_id),
                )
            _audit("DISCOVERY_SCAN", "scan", scan_id,
                   f"{method} | {len(targets)} targets | "
                   f"{result['stats']['devices_discovered']} devices found")
            return jsonify({"scan_id": scan_id, **result})

        except Exception as exc:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE nc_discovery_scans SET status=?, error=?, completed_at=? WHERE id=?",
                    ("failed", str(exc), _now(), scan_id),
                )
            logger.exception("Discovery scan failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/discovery/scans")
    @nc_login_required
    def nc_api_discovery_list():
        """List all discovery scans."""
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT id, topology_id, name, method, targets, status, "
                "stats_json, error, started_at, completed_at, created_at "
                "FROM nc_discovery_scans ORDER BY created_at DESC"
            ).fetchall()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/discovery/scans/<scan_id>")
    @nc_login_required
    def nc_api_discovery_get(scan_id):
        """Get full scan result with devices and graph."""
        with get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM nc_discovery_scans WHERE id=?", (scan_id,)
            ).fetchone()
        if not row:
            return jsonify({"error": "Scan not found"}), 404
        result = _row_to_dict(row)
        # Parse JSON fields
        for field in ("devices_json", "graph_json", "stats_json", "config_json", "targets"):
            if field in result and isinstance(result[field], str):
                try:
                    result[field] = json.loads(result[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return jsonify(result)

    @bp.route("/api/discovery/scans/<scan_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_discovery_delete(scan_id):
        """Delete a discovery scan."""
        with get_connection() as conn:
            conn.execute("DELETE FROM nc_discovery_diffs WHERE scan_id=?", (scan_id,))
            conn.execute("DELETE FROM nc_discovery_scans WHERE id=?", (scan_id,))
        _audit("DISCOVERY_DELETE", "scan", scan_id, "Scan deleted")
        return jsonify({"deleted": scan_id})

    @bp.route("/api/discovery/scans/<scan_id>/import/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_discovery_import(scan_id, topo_id):
        """Import discovered graph into an existing topology (merge or replace)."""
        data = request.get_json(force=True) if request.is_json else {}
        mode = data.get("mode", "merge")  # merge | replace

        with get_connection() as conn:
            scan = conn.execute(
                "SELECT graph_json FROM nc_discovery_scans WHERE id=?", (scan_id,)
            ).fetchone()
            if not scan:
                return jsonify({"error": "Scan not found"}), 404

            topo = conn.execute(
                "SELECT id, graph_json FROM topologies WHERE id=?", (topo_id,)
            ).fetchone()
            if not topo:
                return jsonify({"error": "Topology not found"}), 404

            disc_graph = json.loads(scan["graph_json"])

            if mode == "replace":
                final_graph = disc_graph
            else:
                # Merge: add discovered nodes/edges not already present
                existing_graph = json.loads(topo["graph_json"])
                existing_labels = {
                    n.get("label", "").lower()
                    for n in existing_graph.get("nodes", [])
                }
                for node in disc_graph.get("nodes", []):
                    if node.get("label", "").lower() not in existing_labels:
                        existing_graph.setdefault("nodes", []).append(node)
                        existing_labels.add(node.get("label", "").lower())
                # Add edges for newly added nodes
                existing_edge_pairs = {
                    tuple(sorted([e["source"], e["target"]]))
                    for e in existing_graph.get("edges", [])
                }
                for edge in disc_graph.get("edges", []):
                    pair = tuple(sorted([edge["source"], edge["target"]]))
                    if pair not in existing_edge_pairs:
                        existing_graph.setdefault("edges", []).append(edge)
                        existing_edge_pairs.add(pair)
                final_graph = existing_graph

            conn.execute(
                "UPDATE topologies SET graph_json=?, updated_at=? WHERE id=?",
                (json.dumps(final_graph, default=str), _now(), topo_id),
            )

        _audit("DISCOVERY_IMPORT", "topology", topo_id,
               f"Imported scan {scan_id} ({mode})")
        return jsonify({
            "topology_id": topo_id,
            "mode": mode,
            "nodes": len(final_graph.get("nodes", [])),
            "edges": len(final_graph.get("edges", [])),
        })

    @bp.route("/api/discovery/diff", methods=["POST"])
    @nc_login_required
    def nc_api_discovery_diff():
        """Compare a discovery scan against a designed topology."""
        data = request.get_json(force=True)
        scan_id = data.get("scan_id")
        topology_id = data.get("topology_id")
        if not scan_id or not topology_id:
            return jsonify({"error": "scan_id and topology_id required"}), 400

        with get_connection() as conn:
            scan = conn.execute(
                "SELECT graph_json FROM nc_discovery_scans WHERE id=?", (scan_id,)
            ).fetchone()
            if not scan:
                return jsonify({"error": "Scan not found"}), 404

            topo = conn.execute(
                "SELECT graph_json FROM topologies WHERE id=?", (topology_id,)
            ).fetchone()
            if not topo:
                return jsonify({"error": "Topology not found"}), 404

            disc_graph = json.loads(scan["graph_json"])
            designed_graph = json.loads(topo["graph_json"])

            diff = diff_topologies(designed_graph, disc_graph)

            diff_id = str(_uuid.uuid4())
            s = diff["summary"]
            conn.execute(
                "INSERT INTO nc_discovery_diffs "
                "(id, scan_id, topology_id, diff_json, drift_score, matched, "
                "designed_only, discovered_only, with_drift) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (diff_id, scan_id, topology_id, json.dumps(diff, default=str),
                 s["drift_score"], s["matched"], s["designed_only"],
                 s["discovered_only"], s["with_drift"]),
            )

        _audit("DISCOVERY_DIFF", "diff", diff_id,
               f"Drift {s['drift_score']}% | matched={s['matched']} "
               f"designed_only={s['designed_only']} "
               f"discovered_only={s['discovered_only']}")
        return jsonify({"diff_id": diff_id, **diff})

    @bp.route("/api/discovery/ping", methods=["POST"])
    @nc_login_required
    def nc_api_discovery_ping():
        """Quick ping sweep of a subnet."""
        data = request.get_json(force=True)
        subnet = data.get("subnet", "")
        if not subnet:
            return jsonify({"error": "subnet required"}), 400
        timeout = data.get("timeout", 1.0)
        alive = ping_sweep(subnet, timeout=timeout)
        return jsonify({"subnet": subnet, "alive": alive, "count": len(alive)})

    @bp.route("/api/discovery/capabilities")
    @nc_login_required
    def nc_api_discovery_capabilities():
        """Report which discovery protocols are available."""
        return jsonify({
            "snmp": _HAS_PYSNMP,
            "ssh": _HAS_NETMIKO,
            "ping": True,
            "protocols": {
                "snmp": "pysnmp (SNMP v2c/v3, LLDP-MIB, CDP-MIB)",
                "ssh": "netmiko (CDP/LLDP via CLI)",
                "ping": "ICMP sweep (stdlib subprocess)",
            },
        })

    # ── Done ───────────────────────────────────────────────────────────────
    logger.info("Network Design Canvas Blueprint created (%d routes)",
                len(bp.deferred_functions))
    return bp
