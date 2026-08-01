# CUI // SP-CTI
"""ICDEV Network Design Canvas -- pages route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_pages_routes(bp).
"""
from __future__ import annotations

import json
import os
import uuid as _uuid
from datetime import datetime, timezone
from flask import abort, g, jsonify, render_template, request
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import (
    _normalize_sop_step,
    _now,
    _row_to_dict,
    get_parsed_graph,
    nc_login_required,
)
from tools.network.constants import BOM_COSTS, COMPLIANCE_REGIMES
from tools.network.db.init_db import get_connection


def register_pages_routes(bp, nc_config=None):
    """Register pages routes on the NDC blueprint."""
    NC_CONFIG = nc_config or {}

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

    # ── Chat message helper ────────────────────────────────────────────────

    # ══════════════════════════════════════════════════════════════════════
    # PAGE ROUTES
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/")
    @nc_login_required
    def nc_index():
        conn = get_connection()
        _ph = sql_placeholder(conn)
        # Project filter support
        filter_project = request.args.get("project", "")
        if filter_project:
            rows = conn.execute(
                "SELECT t.id, t.name, t.description, t.classification, t.created_at, t.updated_at, "
                "t.graph_json "
                "FROM topologies t JOIN nc_project_topologies pt ON pt.topology_id=t.id "
                f"WHERE pt.project_id={_ph} ORDER BY t.updated_at DESC LIMIT 20",
                (filter_project,),
            ).fetchall()
            topologies = []
            for r in rows:
                t = _row_to_dict(r)
                try:
                    g = json.loads(t.get("graph_json") or '{"nodes":[],"edges":[]}')
                except Exception:
                    g = {"nodes": [], "edges": []}
                t["node_count"] = len(g.get("nodes", []))
                t["edge_count"] = len(g.get("edges", []))
                topologies.append(t)
        else:
            rows = conn.execute(
                "SELECT id, name, description, classification, created_at, updated_at, graph_json "
                "FROM topologies ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()
            topologies = []
            for r in rows:
                t = _row_to_dict(r)
                try:
                    g = json.loads(t.get("graph_json") or '{"nodes":[],"edges":[]}')
                except Exception:
                    g = {"nodes": [], "edges": []}
                t["node_count"] = len(g.get("nodes", []))
                t["edge_count"] = len(g.get("edges", []))
                topologies.append(t)
        templates = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, category, description, tags FROM nc_templates ORDER BY category, name"
            ).fetchall()
        ]
        sims = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT sr.id, sr.sim_type, sr.ran_at, t.name AS topology_name, sr.result_json "
                "FROM simulation_results sr JOIN topologies t ON t.id=sr.topology_id "
                "ORDER BY sr.ran_at DESC LIMIT 10"
            ).fetchall()
        ]
        total_sims = conn.execute("SELECT COUNT(*) FROM simulation_results").fetchone()[0]
        # Load projects list for filter dropdown
        all_projects = [
            _row_to_dict(r) for r in conn.execute("SELECT id, name FROM nc_projects ORDER BY name").fetchall()
        ]
        # Active project name for display
        active_project = None
        if filter_project:
            ap_row = conn.execute(f"SELECT id, name FROM nc_projects WHERE id={_ph}", (filter_project,)).fetchone()
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

        return render_template(
            "network/index.html",
            topologies=topologies,
            templates=templates[:6],
            simulations=sims,
            all_projects=all_projects,
            filter_project=filter_project,
            active_project=active_project,
            stats={"topologies": len(topologies), "simulations": total_sims, "templates": len(templates)},
        )

    @bp.route("/api/morning-dashboard", methods=["GET"])
    @nc_login_required
    def nc_api_morning_dashboard():
        """Architect's personalized morning dashboard data."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        now = datetime.now(timezone.utc)

        # My projects (recent activity)
        projects = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, status, owner, updated_at FROM nc_projects ORDER BY updated_at DESC LIMIT 10"
            ).fetchall()
        ]

        # Pending reviews
        pending_reviews = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT br.id, br.phase, br.scheduled_date, "
                "rb.short_name AS board, rb.name AS board_name, "
                "p.name AS project_name "
                "FROM nc_board_reviews br "
                "JOIN nc_review_boards rb ON rb.id=br.board_id "
                "JOIN nc_projects p ON p.id=br.project_id "
                "WHERE br.status='pending' "
                "ORDER BY br.scheduled_date"
            ).fetchall()
        ]

        # Compliance alerts (projects below 80%)
        compliance_alerts = []
        for p in projects:
            topo_ids = [
                r[0]
                for r in conn.execute(
                    f"SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}", (p["id"],)
                ).fetchall()
            ]
            tp = tf = 0
            for tid in topo_ids:
                row = conn.execute(
                    f"SELECT passed, failed FROM nc_compliance_checks WHERE topology_id={_ph} ORDER BY ran_at DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                if row:
                    tp += row[0] or 0
                    tf += row[1] or 0
            total = tp + tf
            if total > 0:
                pct = round(tp * 100 / total)
                if pct < 80:
                    compliance_alerts.append(
                        {
                            "project": p["name"],
                            "project_id": p["id"],
                            "compliance_pct": pct,
                        }
                    )

        # Upcoming EOL (devices across all topologies)
        eol_alerts = []
        for r in conn.execute("SELECT t.id AS topo_id, t.name AS topo_name FROM topologies t LIMIT 20").fetchall():
            topo_id = r["topo_id"] if hasattr(r, "keys") else r[0]
            # Read-only iteration → take the shared cached parse (ndc-perf-02).
            graph = get_parsed_graph(conn, topo_id)
            if not graph:
                continue
            for n in graph.get("nodes", []):
                cfg = n.get("config") or n.get("configData") or {}
                eol = cfg.get("eol_date") or cfg.get("eosup_date")
                if eol:
                    try:
                        dt = datetime.fromisoformat(
                            eol + "T00:00:00+00:00" if "T" not in eol else eol.replace("Z", "+00:00")
                        )
                        months = (dt - now).days / 30.44
                        if months <= 12:
                            eol_alerts.append(
                                {
                                    "device": n.get("label", ""),
                                    "model": cfg.get("model", ""),
                                    "eol_date": eol,
                                    "months_remaining": round(months, 1),
                                    "topology": r["topo_name"],
                                    "past_eol": months <= 0,
                                }
                            )
                    except (ValueError, TypeError):
                        pass

        # Recent activity
        activity = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT action, entity_type, details, ts FROM nc_audit ORDER BY ts DESC LIMIT 15"
            ).fetchall()
        ]

        # Unread notifications
        unread = conn.execute("SELECT COUNT(*) FROM nc_notifications WHERE is_read=0").fetchone()[0]

        # Quick stats
        total_projects = conn.execute("SELECT COUNT(*) FROM nc_projects").fetchone()[0]
        total_topos = conn.execute("SELECT COUNT(*) FROM topologies").fetchone()[0]
        total_peers = conn.execute("SELECT COUNT(*) FROM nc_peering_agreements WHERE status='operational'").fetchone()[
            0
        ]

        conn.close()
        return jsonify(
            {
                "projects": projects,
                "pending_reviews": pending_reviews,
                "compliance_alerts": compliance_alerts,
                "eol_alerts": sorted(eol_alerts, key=lambda x: x["months_remaining"])[:10],
                "activity": activity,
                "unread_notifications": unread,
                "stats": {
                    "total_projects": total_projects,
                    "total_topologies": total_topos,
                    "active_peering": total_peers,
                    "pending_reviews": len(pending_reviews),
                    "compliance_alerts": len(compliance_alerts),
                    "eol_alerts": len(eol_alerts),
                },
            }
        )

    @bp.route("/canvas/new")
    @nc_login_required
    def nc_canvas_new():
        return render_template("network/canvas.html", topology_id="new", topology_name="Untitled Topology")

    @bp.route("/canvas/<topo_id>")
    @nc_login_required
    def nc_canvas_edit(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not row:
            conn.close()
            abort(404)
        topo = _row_to_dict(row)
        # Find projects this topology belongs to
        topo_projects = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT p.id, p.name, p.status FROM nc_projects p "
                "JOIN nc_project_topologies pt ON pt.project_id=p.id "
                f"WHERE pt.topology_id={_ph} ORDER BY p.name",
                (topo_id,),
            ).fetchall()
        ]
        # All projects for quick-switch
        all_projects = [
            _row_to_dict(r) for r in conn.execute("SELECT id, name, status FROM nc_projects ORDER BY name").fetchall()
        ]
        conn.close()
        return render_template(
            "network/canvas.html",
            topology_id=topo_id,
            topology_name=topo["name"],
            classification=topo.get("classification", "public"),
            design=topo,
            topo_projects=topo_projects,
            all_projects=all_projects,
        )

    @bp.route("/templates")
    @nc_login_required
    def nc_templates():
        conn = get_connection()
        templates = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT id, name, category, description, tags FROM nc_templates ORDER BY category, name"
            ).fetchall()
        ]
        conn.close()
        for t in templates:
            try:
                t["tags"] = json.loads(t.get("tags") or "[]")
            except Exception:
                t["tags"] = []
        categories = {}
        for t in templates:
            categories.setdefault(t.get("category") or "Other", []).append(t)
        return render_template("network/templates_gallery.html", categories=categories, templates=templates)

    @bp.route("/simulation/<sim_id>")
    @nc_login_required
    def nc_simulation_detail(sim_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(
            "SELECT sr.*, t.name AS topology_name FROM simulation_results sr "
            f"JOIN topologies t ON t.id=sr.topology_id WHERE sr.id={_ph}",
            (sim_id,),
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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_templates WHERE id={_ph}", (tpl_id,)).fetchone()
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
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT id, name FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            abort(404)
        versions = [
            _row_to_dict(r)
            for r in conn.execute(
                f"SELECT * FROM nc_versions WHERE topology_id={_ph} ORDER BY version_num", (topo_id,)
            ).fetchall()
        ]
        conn.close()
        return render_template("network/versions.html", topology=_row_to_dict(topo), versions=versions)

    @bp.route("/montecarlo/<topo_id>")
    @nc_login_required
    def nc_montecarlo_page(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT id, name, graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            abort(404)

        scenarios = [
            _row_to_dict(r)
            for r in conn.execute(
                f"SELECT * FROM nc_mc_scenarios WHERE topology_id={_ph} ORDER BY created_at DESC", (topo_id,)
            ).fetchall()
        ]

        _auto_run_scenario = None
        if not scenarios:
            now = _now()
            defaults = [
                (
                    "Random Failure (5% links)",
                    "random",
                    "Random 5% link failure probability per iteration",
                    json.dumps({"iterations": 1000, "node_failure_prob": 0.02, "edge_failure_prob": 0.05}),
                ),
                (
                    "Major Outage (20% links)",
                    "random",
                    "Stress test — 20% link failure probability",
                    json.dumps({"iterations": 1000, "node_failure_prob": 0.05, "edge_failure_prob": 0.20}),
                ),
                (
                    "Single Node Failure",
                    "random",
                    "What happens when one critical node goes down?",
                    json.dumps({"iterations": 500, "node_failure_prob": 0.08, "edge_failure_prob": 0.0}),
                ),
            ]
            for name, stype, desc, cfg in defaults:
                sid = str(_uuid.uuid4())
                conn.execute(
                    "INSERT INTO nc_mc_scenarios (id, topology_id, name, scenario_type, description, config_json, created_at) "
                    f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (sid, topo_id, name, stype, desc, cfg, now),
                )
            conn.commit()
            scenarios = [
                _row_to_dict(r)
                for r in conn.execute(
                    f"SELECT * FROM nc_mc_scenarios WHERE topology_id={_ph} ORDER BY created_at DESC", (topo_id,)
                ).fetchall()
            ]
            try:
                graph = json.loads(topo["graph_json"])
                if graph.get("nodes") and graph.get("edges"):
                    _auto_run_scenario = scenarios[-1]["id"]
            except Exception:
                pass

        runs = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT r.id, r.iterations, r.ran_at, s.name AS scenario_name, s.scenario_type "
                "FROM nc_mc_runs r JOIN nc_mc_scenarios s ON s.id=r.scenario_id "
                f"WHERE r.topology_id={_ph} ORDER BY r.ran_at DESC LIMIT 20",
                (topo_id,),
            ).fetchall()
        ]

        if scenarios and not runs and not _auto_run_scenario:
            _auto_run_scenario = scenarios[-1]["id"]

        conn.close()
        return render_template(
            "network/montecarlo.html",
            topology=_row_to_dict(topo),
            scenarios=scenarios,
            runs=runs,
            auto_run_scenario=_auto_run_scenario,
        )

    @bp.route("/compliance/<topo_id>")
    @nc_login_required
    def nc_compliance_page(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT id, name, graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            abort(404)

        profile = conn.execute(f"SELECT * FROM nc_compliance_profiles WHERE topology_id={_ph}", (topo_id,)).fetchone()
        if not profile:
            pid = str(_uuid.uuid4())
            conn.execute(f"INSERT INTO nc_compliance_profiles (id, topology_id) VALUES ({_ph},{_ph})", (pid, topo_id))
            conn.commit()
            profile = conn.execute(f"SELECT * FROM nc_compliance_profiles WHERE id={_ph}", (pid,)).fetchone()
        profile = _row_to_dict(profile)

        audits = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT id, check_type, passed, failed, ran_at FROM nc_compliance_checks "
                f"WHERE topology_id={_ph} ORDER BY ran_at DESC LIMIT 10",
                (topo_id,),
            ).fetchall()
        ]

        open_findings = [
            _row_to_dict(r)
            for r in conn.execute(
                f"SELECT * FROM nc_compliance_findings WHERE topology_id={_ph} AND status='open' ORDER BY severity, rule_id",
                (topo_id,),
            ).fetchall()
        ]

        conn.close()

        try:
            regimes = json.loads(profile.get("regimes", "[]"))
        except Exception:
            regimes = ["fisma_high"]

        return render_template(
            "network/compliance_audit.html",
            topology=_row_to_dict(topo),
            profile=profile,
            regimes=regimes,
            all_regimes=COMPLIANCE_REGIMES,
            audits=audits,
            open_findings=open_findings,
        )

    @bp.route("/circuits")
    @nc_login_required
    def nc_circuits():
        conn = get_connection()
        _ph = sql_placeholder(conn)
        filter_project = request.args.get("project", "")
        if filter_project:
            circuits = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT * FROM nc_circuits WHERE topology_id IN "
                    f"(SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}) "
                    "ORDER BY updated_at DESC",
                    (filter_project,),
                ).fetchall()
            ]
        else:
            circuits = [
                _row_to_dict(r) for r in conn.execute("SELECT * FROM nc_circuits ORDER BY updated_at DESC").fetchall()
            ]
        stats = {
            "total": len(circuits),
            "installed": sum(1 for c in circuits if c.get("install_status") == "installed"),
            "planned": sum(1 for c in circuits if c.get("install_status") == "planned"),
            "monthly_cost": sum(c.get("monthly_cost_usd") or 0 for c in circuits),
        }
        all_projects = [
            _row_to_dict(r) for r in conn.execute("SELECT id, name FROM nc_projects ORDER BY name").fetchall()
        ]
        active_project = None
        if filter_project:
            ap_row = conn.execute(f"SELECT id, name FROM nc_projects WHERE id={_ph}", (filter_project,)).fetchone()
            active_project = _row_to_dict(ap_row) if ap_row else None
        conn.close()
        return render_template(
            "network/circuits.html",
            circuits=circuits,
            stats=stats,
            all_projects=all_projects,
            filter_project=filter_project,
            active_project=active_project,
        )

    @bp.route("/customers")
    @nc_login_required
    def nc_customers():
        conn = get_connection()
        customers = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_customers ORDER BY name").fetchall()]
        sites = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT s.*, c.name AS customer_name FROM nc_sites s "
                "LEFT JOIN nc_customers c ON c.id=s.customer_id ORDER BY s.name"
            ).fetchall()
        ]
        conn.close()
        return render_template("network/customers.html", customers=customers, sites=sites)

    @bp.route("/ipam")
    @nc_login_required
    def nc_ipam():
        conn = get_connection()
        _ph = sql_placeholder(conn)
        filter_project = request.args.get("project", "")
        if filter_project:
            blocks = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT * FROM nc_ipam_blocks WHERE topology_id IN "
                    f"(SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}) "
                    "ORDER BY network",
                    (filter_project,),
                ).fetchall()
            ]
        else:
            blocks = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_ipam_blocks ORDER BY network").fetchall()]
        all_projects = [
            _row_to_dict(r) for r in conn.execute("SELECT id, name FROM nc_projects ORDER BY name").fetchall()
        ]
        active_project = None
        if filter_project:
            ap_row = conn.execute(f"SELECT id, name FROM nc_projects WHERE id={_ph}", (filter_project,)).fetchone()
            active_project = _row_to_dict(ap_row) if ap_row else None
        conn.close()
        return render_template(
            "network/ipam.html",
            blocks=blocks,
            all_projects=all_projects,
            filter_project=filter_project,
            active_project=active_project,
        )

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
        xconns = [
            _row_to_dict(r) for r in conn.execute("SELECT * FROM nc_cross_connects ORDER BY updated_at DESC").fetchall()
        ]
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
        log_rows = conn.execute("SELECT * FROM nc_netbox_sync_log ORDER BY ran_at DESC LIMIT 50").fetchall()
        sync_log = [_row_to_dict(r) for r in log_rows]
        # Topologies for the import-to-canvas picker
        topo_rows = conn.execute("SELECT id, name FROM topologies ORDER BY updated_at DESC LIMIT 100").fetchall()
        topologies = [_row_to_dict(r) for r in topo_rows]
        conn.close()
        airgap = os.environ.get("NETWORK_AIRGAP", "").lower() in ("1", "true", "yes")
        return render_template(
            "network/netbox.html",
            cfg=cfg,
            cached_counts=cached_counts,
            sync_log=sync_log,
            topologies=topologies,
            airgap_mode=airgap,
        )

    @bp.route("/projects")
    @nc_login_required
    def nc_projects():
        conn = get_connection()
        _ph = sql_placeholder(conn)
        projects = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT p.*, c.name AS customer_name, "
                "(SELECT COUNT(*) FROM nc_project_topologies pt WHERE pt.project_id=p.id) AS topo_count "
                "FROM nc_projects p LEFT JOIN nc_customers c ON c.id=p.customer_id ORDER BY p.updated_at DESC"
            ).fetchall()
        ]
        customers = [
            _row_to_dict(r) for r in conn.execute("SELECT id, name FROM nc_customers ORDER BY name").fetchall()
        ]

        # ── Portfolio health data per project ─────────────────────────────
        for p in projects:
            pid = p["id"]
            topo_ids = [
                r[0]
                for r in conn.execute(
                    f"SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}", (pid,)
                ).fetchall()
            ]
            # Compliance: latest audit pass/fail across topologies
            total_passed = total_failed = 0
            open_findings = 0
            for tid in topo_ids:
                row = conn.execute(
                    f"SELECT passed, failed FROM nc_compliance_checks WHERE topology_id={_ph} ORDER BY ran_at DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                if row:
                    total_passed += row[0] or 0
                    total_failed += row[1] or 0
                of = conn.execute(
                    f"SELECT COUNT(*) FROM nc_compliance_findings WHERE topology_id={_ph} AND status='open'", (tid,)
                ).fetchone()
                open_findings += of[0] if of else 0
            total_checks = total_passed + total_failed
            p["compliance_pct"] = round(total_passed * 100 / total_checks) if total_checks else None
            p["open_findings"] = open_findings
            # Cost: sum of circuit monthly costs
            cost_row = conn.execute(
                "SELECT COALESCE(SUM(monthly_cost_usd), 0) FROM nc_circuits WHERE topology_id IN "
                f"(SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph})",
                (pid,),
            ).fetchone()
            p["monthly_cost"] = cost_row[0] if cost_row else 0
            # Node/edge totals (computed in Python for PG portability)
            ne_rows = conn.execute(
                "SELECT t.graph_json FROM topologies t "
                "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
                f"WHERE pt.project_id={_ph}",
                (pid,),
            ).fetchall()
            total_nodes = total_edges = 0
            for ner in ne_rows:
                try:
                    g = json.loads(ner["graph_json"] or '{"nodes":[],"edges":[]}')
                except Exception:
                    g = {"nodes": [], "edges": []}
                total_nodes += len(g.get("nodes", []))
                total_edges += len(g.get("edges", []))
            p["total_nodes"] = total_nodes
            p["total_edges"] = total_edges
            # Last simulation date
            sim_row = conn.execute(
                "SELECT MAX(ran_at) FROM simulation_results WHERE topology_id IN "
                f"(SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph})",
                (pid,),
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
        portfolio_activity = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT action, entity_type, entity_id, details, user_id, ts FROM nc_audit ORDER BY ts DESC LIMIT 20"
            ).fetchall()
        ]

        conn.close()
        return render_template(
            "network/projects.html",
            projects=projects,
            customers=customers,
            portfolio=portfolio_stats,
            activity=portfolio_activity,
        )

    @bp.route("/projects/<proj_id>")
    @nc_login_required
    def nc_project_detail(proj_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        proj = conn.execute(
            "SELECT p.*, c.name AS customer_name FROM nc_projects p "
            f"LEFT JOIN nc_customers c ON c.id=p.customer_id WHERE p.id={_ph}",
            (proj_id,),
        ).fetchone()
        if not proj:
            conn.close()
            abort(404)
        proj = _row_to_dict(proj)
        topos = []
        for r in conn.execute(
            "SELECT t.id, t.name, t.description, t.classification, "
            "t.updated_at, t.graph_json "
            "FROM topologies t JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            f"WHERE pt.project_id={_ph} ORDER BY t.updated_at DESC",
            (proj_id,),
        ).fetchall():
            t = _row_to_dict(r)
            try:
                g = json.loads(t.get("graph_json") or '{"nodes":[],"edges":[]}')
            except Exception:
                g = {"nodes": [], "edges": []}
            t["node_count"] = len(g.get("nodes", []))
            t["edge_count"] = len(g.get("edges", []))
            topos.append(t)
        circuits = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM nc_circuits WHERE topology_id IN "
                f"(SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}) "
                "ORDER BY circuit_id",
                (proj_id,),
            ).fetchall()
        ]
        all_topos = [_row_to_dict(r) for r in conn.execute("SELECT id, name FROM topologies ORDER BY name").fetchall()]

        # ── P1: Compliance rollup per topology ────────────────────────────
        topo_compliance = []
        agg_passed = agg_failed = 0
        total_open_findings = 0
        for t in topos:
            tid = t["id"]
            audit_row = conn.execute(
                "SELECT passed, failed, ran_at FROM nc_compliance_checks "
                f"WHERE topology_id={_ph} ORDER BY ran_at DESC LIMIT 1",
                (tid,),
            ).fetchone()
            passed = (audit_row[0] or 0) if audit_row else 0
            failed = (audit_row[1] or 0) if audit_row else 0
            last_audit = audit_row[2] if audit_row else None
            total = passed + failed
            pct = round(passed * 100 / total) if total else None
            agg_passed += passed
            agg_failed += failed
            of_row = conn.execute(
                f"SELECT COUNT(*) FROM nc_compliance_findings WHERE topology_id={_ph} AND status='open'", (tid,)
            ).fetchone()
            open_f = of_row[0] if of_row else 0
            total_open_findings += open_f
            # Findings by severity
            sev_rows = conn.execute(
                "SELECT severity, COUNT(*) FROM nc_compliance_findings "
                f"WHERE topology_id={_ph} AND status='open' "
                "GROUP BY severity",
                (tid,),
            ).fetchall()
            by_sev = {r[0]: r[1] for r in sev_rows}
            topo_compliance.append(
                {
                    "id": tid,
                    "name": t["name"],
                    "passed": passed,
                    "failed": failed,
                    "pct": pct,
                    "open_findings": open_f,
                    "cat1": by_sev.get("CAT1", 0),
                    "cat2": by_sev.get("CAT2", 0),
                    "cat3": by_sev.get("CAT3", 0),
                    "last_audit": last_audit,
                }
            )
        agg_total = agg_passed + agg_failed
        agg_pct = round(agg_passed * 100 / agg_total) if agg_total else None

        # ── P1: BOM cost rollup per topology ──────────────────────────────
        topo_bom = []
        total_capex = 0
        total_circuit_cost = sum(c.get("monthly_cost_usd") or 0 for c in circuits)
        for t in topos:
            try:
                graph = json.loads(t.get("graph_json") or '{"nodes":[]}')
            except Exception:
                graph = {"nodes": []}
            type_counts = {}
            for n in graph.get("nodes", []):
                nt = n.get("type", "unknown")
                type_counts[nt] = type_counts.get(nt, 0) + 1
            capex = sum(BOM_COSTS.get(dt, 0) * cnt for dt, cnt in type_counts.items())
            total_capex += capex
            topo_bom.append(
                {
                    "id": t["id"],
                    "name": t["name"],
                    "devices": sum(type_counts.values()),
                    "unique_types": len(type_counts),
                    "capex": capex,
                }
            )

        # ── P1: Activity feed from nc_audit ───────────────────────────────
        topo_ids = [t["id"] for t in topos]
        if topo_ids:
            placeholders = ",".join(_ph for _ in topo_ids)
            activity = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT action, entity_type, entity_id, details, "
                    "user_id, ts FROM nc_audit "
                    "WHERE entity_id IN (" + placeholders + ") "  # nosec B608
                    f"OR (entity_type='project' AND entity_id={_ph}) "
                    "ORDER BY ts DESC LIMIT 30",
                    topo_ids + [proj_id],
                ).fetchall()
            ]
        else:
            activity = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT action, entity_type, entity_id, details, "
                    "user_id, ts FROM nc_audit "
                    f"WHERE entity_type='project' AND entity_id={_ph} "
                    "ORDER BY ts DESC LIMIT 30",
                    (proj_id,),
                ).fetchall()
            ]

        # Strip graph_json from topos (large, not needed in template)
        for t in topos:
            t.pop("graph_json", None)

        # P3: Milestones, Notes, Tags, Assignees
        milestones = [
            _row_to_dict(r)
            for r in conn.execute(
                f"SELECT * FROM nc_project_milestones WHERE project_id={_ph} ORDER BY due_date", (proj_id,)
            ).fetchall()
        ]
        notes = [
            _row_to_dict(r)
            for r in conn.execute(
                f"SELECT * FROM nc_project_notes WHERE project_id={_ph} ORDER BY created_at DESC", (proj_id,)
            ).fetchall()
        ]
        project_tags = [
            r[0]
            for r in conn.execute(
                f"SELECT tag FROM nc_tags WHERE entity_type='project' AND entity_id={_ph} ORDER BY tag", (proj_id,)
            ).fetchall()
        ]
        # Assignees per topology
        topo_assignees = {}
        for r in conn.execute(
            f"SELECT topology_id, assignee FROM nc_project_topologies WHERE project_id={_ph} AND assignee != ''", (proj_id,)
        ).fetchall():
            topo_assignees[r[0]] = r[1]

        # Phase A: Review board pipeline
        review_boards = [
            _row_to_dict(r) for r in conn.execute("SELECT * FROM nc_review_boards ORDER BY sort_order").fetchall()
        ]
        board_reviews = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT br.*, rb.name AS board_name, rb.short_name "
                "FROM nc_board_reviews br "
                "JOIN nc_review_boards rb ON rb.id=br.board_id "
                f"WHERE br.project_id={_ph} ORDER BY rb.sort_order, br.phase",
                (proj_id,),
            ).fetchall()
        ]
        project_phases = [
            _row_to_dict(r)
            for r in conn.execute(
                f"SELECT * FROM nc_project_phases WHERE project_id={_ph} ORDER BY phase_num", (proj_id,)
            ).fetchall()
        ]
        safe_bridge = conn.execute(f"SELECT * FROM nc_safe_bridge WHERE project_id={_ph}", (proj_id,)).fetchone()
        safe_bridge = _row_to_dict(safe_bridge) if safe_bridge else None
        if safe_bridge and safe_bridge.get("roi_json"):
            try:
                safe_bridge["roi"] = json.loads(safe_bridge["roi_json"])
            except Exception:
                safe_bridge["roi"] = {}

        # Migration phases with linked SOPs and parsed steps
        migration_phases_raw = [
            _row_to_dict(r) for r in conn.execute(
                f"SELECT * FROM nc_migration_phases WHERE project_id={_ph} ORDER BY phase_num",
                (proj_id,)
            ).fetchall()
        ]
        for mphase in migration_phases_raw:
            linked_docs = [
                _row_to_dict(r) for r in conn.execute(
                    f"""SELECT pd.doc_title, pd.doc_type, pd.doc_source, pd.relevance_note,
                              s.steps, s.prerequisites, s.validation,
                              s.rollback AS sop_rollback, s.escalation
                       FROM nc_phase_documents pd
                       LEFT JOIN ndc_sops s ON s.sop_id = pd.doc_id
                       WHERE pd.phase_id = {_ph}
                       ORDER BY pd.display_order""",
                    (mphase['id'],)
                ).fetchall()
            ]
            for doc in linked_docs:
                for field in ('steps', 'prerequisites', 'validation', 'escalation'):
                    raw = doc.get(field)
                    if raw:
                        try:
                            doc[field] = json.loads(raw)
                        except Exception:
                            doc[field] = []
                # Normalize steps to canonical {number, text, verify, time_est, rollback}
                # regardless of which schema variant the SOP was seeded with.
                if doc.get('steps') and isinstance(doc['steps'], list):
                    doc['steps'] = [
                        _normalize_sop_step(s) for s in doc['steps']
                        if isinstance(s, dict)
                    ]
            mphase['linked_docs'] = linked_docs
            mphase['steps'] = [
                s.strip().rstrip('.')
                for s in (mphase.get('description') or '').split('. ')
                if s.strip()
            ]

        # First topology ID for 3-panel diagram link
        first_topo_id = topos[0]['id'] if topos else None

        # ── AI-assisted migration: COA, port mapping, config translation ──
        coa_data = {}
        port_mapping = {}
        config_translation = {}
        target_device_id = None

        # Load stored COA or generate on-the-fly
        if proj.get("coa_json"):
            try:
                coa_data = json.loads(proj["coa_json"])
            except Exception:
                coa_data = {}

        # Pick an EOL edge device from the first topology for demo consistency
        target_node_id = None
        target_device_id_for_tools = None
        if first_topo_id:
            try:
                graph = json.loads(
                    conn.execute(
                        f"SELECT graph_json FROM topologies WHERE id={_ph}", (first_topo_id,)
                    ).fetchone()[0]
                )
                nodes = graph.get("nodes", [])
                eol_nodes = [
                    n for n in nodes
                    if n.get("eol") or n.get("meta", {}).get("eol_date")
                ]
                if eol_nodes:
                    target_node_id = eol_nodes[0].get("id")
                    # Resolve topology node_id → ni_devices.id for tool calls
                    dev_row = conn.execute(
                        f"SELECT id FROM ni_devices WHERE node_id={_ph}",
                        (target_node_id,),
                    ).fetchone()
                    target_device_id_for_tools = dev_row["id"] if dev_row else target_node_id
            except Exception:
                target_node_id = None
                target_device_id_for_tools = None

        target_device_id = target_node_id

        if target_device_id_for_tools:
            # Generate COAs if missing
            if not coa_data:
                try:
                    from tools.ndc.executive_summary_generator import generate_executive_summary
                    _exec_sum = generate_executive_summary(horizon_days=365)  # noqa: F841
                    coa_data = {
                        "coa_1": {
                            "id": 1,
                            "name": "Rip & Replace",
                            "short_name": "Rip",
                            "risk_level": "high",
                            "estimated_downtime_hours": 4,
                            "total_cost": 0,
                            "description": "Swap hardware in single maintenance window per device.",
                        },
                        "coa_2": {
                            "id": 2,
                            "name": "Phased Cutover",
                            "short_name": "Phased",
                            "risk_level": "medium",
                            "estimated_downtime_hours": 1,
                            "total_cost": 0,
                            "description": "Migrate circuits/services in phases over weeks.",
                        },
                        "coa_3": {
                            "id": 3,
                            "name": "Side-by-Side VLAN",
                            "short_name": "Side-by-Side",
                            "risk_level": "low",
                            "estimated_downtime_hours": 0,
                            "total_cost": 0,
                            "description": "Run old+new in parallel on same VLAN domain. Near-zero downtime.",
                            "recommended": True,
                        },
                        "source_device_id": target_device_id,
                    }
                except Exception:
                    pass

            # Port mapping
            try:
                from tools.ndc.port_mapping_generator import generate_port_mapping
                port_mapping = generate_port_mapping(target_device_id_for_tools)
            except Exception:
                port_mapping = {}

            # Config translation
            try:
                from tools.ndc.config_translator import generate_config_translation
                config_translation = generate_config_translation(target_device_id_for_tools, target_vendor="arista")
            except Exception:
                config_translation = {}

        conn.close()
        return render_template(
            "network/project_detail.html",
            project=proj,
            topologies=topos,
            circuits=circuits,
            all_topos=all_topos,
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
            safe_bridge=safe_bridge,
            migration_phases=migration_phases_raw,
            first_topo_id=first_topo_id,
            coa_data=coa_data,
            port_mapping=port_mapping,
            config_translation=config_translation,
            target_device_id=target_device_id,
        )

    # ══════════════════════════════════════════════════════════════════════
    # P2: Cross-Project Comparison
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/projects/compare")
    @nc_login_required
    def nc_project_compare():
        conn = get_connection()
        _ph = sql_placeholder(conn)
        projects = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT p.*, c.name AS customer_name, "
                "(SELECT COUNT(*) FROM nc_project_topologies pt "
                " WHERE pt.project_id=p.id) AS topo_count "
                "FROM nc_projects p "
                "LEFT JOIN nc_customers c ON c.id=p.customer_id "
                "ORDER BY p.name"
            ).fetchall()
        ]

        comparison = []
        for p in projects:
            pid = p["id"]
            topo_ids = [
                r[0]
                for r in conn.execute(
                    f"SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}", (pid,)
                ).fetchall()
            ]

            # Compliance aggregate
            total_passed = total_failed = 0
            open_findings = cat1 = cat2 = cat3 = 0
            for tid in topo_ids:
                row = conn.execute(
                    f"SELECT passed, failed FROM nc_compliance_checks WHERE topology_id={_ph} ORDER BY ran_at DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                if row:
                    total_passed += row[0] or 0
                    total_failed += row[1] or 0
                sev_rows = conn.execute(
                    "SELECT severity, COUNT(*) FROM nc_compliance_findings "
                    f"WHERE topology_id={_ph} AND status='open' "
                    "GROUP BY severity",
                    (tid,),
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
            comp_pct = round(total_passed * 100 / total_checks) if total_checks else None

            # Cost
            cost_row = conn.execute(
                "SELECT COALESCE(SUM(monthly_cost_usd), 0) "
                "FROM nc_circuits WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies "
                f" WHERE project_id={_ph})",
                (pid,),
            ).fetchone()
            circuit_cost = cost_row[0] if cost_row else 0

            # BOM CapEx
            capex = 0
            total_devices = 0
            for tid in topo_ids:
                trow = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (tid,)).fetchone()
                if trow:
                    try:
                        g = json.loads(trow["graph_json"])
                    except Exception:
                        g = {"nodes": []}
                    for n in g.get("nodes", []):
                        nt = n.get("type", "unknown")
                        capex += BOM_COSTS.get(nt, 0)
                        total_devices += 1

            # Node/edge totals (computed in Python for PG portability)
            ne_rows = conn.execute(
                "SELECT t.graph_json FROM topologies t "
                "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
                f"WHERE pt.project_id={_ph}",
                (pid,),
            ).fetchall()
            total_nodes = total_edges = 0
            for ner in ne_rows:
                try:
                    g = json.loads(ner["graph_json"] or '{"nodes":[],"edges":[]}')
                except Exception:
                    g = {"nodes": [], "edges": []}
                total_nodes += len(g.get("nodes", []))
                total_edges += len(g.get("edges", []))

            # Last MC resilience score
            mc_row = conn.execute(
                "SELECT result_json FROM nc_mc_runs "
                "WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies "
                f" WHERE project_id={_ph}) "
                "ORDER BY ran_at DESC LIMIT 1",
                (pid,),
            ).fetchone()
            mc_score = None
            if mc_row:
                try:
                    mc_data = json.loads(mc_row["result_json"])
                    mc_score = mc_data.get("risk_score")
                except Exception:
                    pass

            comparison.append(
                {
                    "id": pid,
                    "name": p["name"],
                    "status": p["status"],
                    "customer": p.get("customer_name", ""),
                    "topo_count": p["topo_count"],
                    "nodes": total_nodes,
                    "edges": total_edges,
                    "compliance_pct": comp_pct,
                    "open_findings": open_findings,
                    "cat1": cat1,
                    "cat2": cat2,
                    "cat3": cat3,
                    "circuit_cost": circuit_cost,
                    "capex": capex,
                    "devices": total_devices,
                    "mc_resilience": mc_score,
                }
            )
        conn.close()
        return render_template("network/compare.html", comparison=comparison)

    # ══════════════════════════════════════════════════════════════════════
    # P2: Clone Project API
    # ══════════════════════════════════════════════════════════════════════
