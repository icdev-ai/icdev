# CUI // SP-CTI
"""ICDEV Network Design Canvas -- topology_ops route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_topology_ops_routes(bp).
"""
from __future__ import annotations

import json
import shutil
import uuid as _uuid
import zipfile
from pathlib import Path
from flask import jsonify, render_template, request
from tools.network.routes._common import _ICDEV_ROOT
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import (
    _audit,
    _notify,
    _now,
    _row_to_dict,
    invalidate_parsed_graph,
    nc_login_required,
)
from tools.network.constants import CSP_GROUP_DEFAULTS
from tools.network.db.init_db import get_connection
from tools.network.montecarlo import run_monte_carlo


def register_topology_ops_routes(bp):
    """Register topology_ops routes on the NDC blueprint."""

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
        unread = conn.execute("SELECT COUNT(*) FROM nc_notifications WHERE is_read=0").fetchone()[0]
        conn.close()
        return jsonify(
            {
                "notifications": [_row_to_dict(r) for r in rows],
                "unread": unread,
            }
        )

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
        _ph = sql_placeholder(conn)
        a_row = conn.execute(f"SELECT name, graph_json FROM topologies WHERE id={_ph}", (topo_a_id,)).fetchone()
        b_row = conn.execute(f"SELECT name, graph_json FROM topologies WHERE id={_ph}", (topo_b_id,)).fetchone()
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

        only_a = [{"label": a_nodes[k].get("label"), "type": a_nodes[k].get("type")} for k in sorted(a_keys - b_keys)]
        only_b = [{"label": b_nodes[k].get("label"), "type": b_nodes[k].get("type")} for k in sorted(b_keys - a_keys)]
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

        return jsonify(
            {
                "topology_a": {
                    "id": topo_a_id,
                    "name": a_row["name"],
                    "nodes": len(ga.get("nodes", [])),
                    "edges": len(ga.get("edges", [])),
                    "types": a_types,
                },
                "topology_b": {
                    "id": topo_b_id,
                    "name": b_row["name"],
                    "nodes": len(gb.get("nodes", [])),
                    "edges": len(gb.get("edges", [])),
                    "types": b_types,
                },
                "nodes_only_a": only_a,
                "nodes_only_b": only_b,
                "nodes_common": len(common),
                "edges_only_a": len(a_ekeys - b_ekeys),
                "edges_only_b": len(b_ekeys - a_ekeys),
                "edges_common": len(a_ekeys & b_ekeys),
                "similarity_pct": round(len(common) * 100 / max(len(a_keys | b_keys), 1)),
            }
        )

    @bp.route("/projects/diff")
    @nc_login_required
    def nc_topology_diff_page():
        conn = get_connection()
        topos = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT t.id, t.name, p.name AS project_name "
                "FROM topologies t "
                "LEFT JOIN nc_project_topologies pt "
                "  ON pt.topology_id=t.id "
                "LEFT JOIN nc_projects p ON p.id=pt.project_id "
                "ORDER BY t.name"
            ).fetchall()
        ]
        conn.close()
        return render_template("network/diff.html", topologies=topos)

    # ── Auto-decompose to SAFe ────────────────────────────────────────────
    @bp.route("/api/projects/<pid>/decompose", methods=["POST"])
    @nc_login_required
    def nc_api_decompose_to_safe(pid):
        """Auto-generate SAFe Feature + Stories from network project.
        Stores in nc_safe_bridge and returns the decomposition."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        proj = conn.execute(f"SELECT * FROM nc_projects WHERE id={_ph}", (pid,)).fetchone()
        if not proj:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        proj = _row_to_dict(proj)

        topos = []
        for r in conn.execute(
            "SELECT t.id, t.name, t.classification, t.graph_json "
            "FROM topologies t "
            "JOIN nc_project_topologies pt ON pt.topology_id=t.id "
            f"WHERE pt.project_id={_ph}",
            (pid,),
        ).fetchall():
            t = _row_to_dict(r)
            try:
                g = json.loads(t.get("graph_json") or '{"nodes":[],"edges":[]}')
            except Exception:
                g = {"nodes": [], "edges": []}
            t["node_count"] = len(g.get("nodes", []))
            topos.append(t)
        circuits = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT circuit_id, circuit_type, bandwidth "
                "FROM nc_circuits WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies "
                f" WHERE project_id={_ph})",
                (pid,),
            ).fetchall()
        ]

        # Build SAFe hierarchy
        feature = {
            "level": "feature",
            "title": f"[Feature] {proj['name']}",
            "description": proj.get("description", ""),
            "t_shirt_size": "L" if len(topos) > 2 else "M" if len(topos) > 0 else "S",
            "status": "draft",
        }
        stories = []
        for t in topos:
            size = "M" if (t.get("node_count") or 0) > 20 else "S"
            stories.append(
                {
                    "level": "story",
                    "title": f"[Story] Implement {t['name']}",
                    "description": f"Network topology: {t['name']} "
                    f"({t.get('node_count', 0)} nodes, "
                    f"{t.get('classification', 'public')})",
                    "t_shirt_size": size,
                    "source_topology_id": t["id"],
                    "status": "draft",
                }
            )
        enablers = []
        for c in circuits:
            enablers.append(
                {
                    "level": "enabler",
                    "title": f"[Enabler] Provision {c['circuit_id']}",
                    "description": f"{c.get('circuit_type', '')} {c.get('bandwidth', '')}",
                    "t_shirt_size": "S",
                    "status": "draft",
                }
            )

        # WSJF scoring (simplified)
        tshirt_pts = {"XS": 1, "S": 2, "M": 3, "L": 5, "XL": 8}
        feature["wsjf_score"] = round(7 / tshirt_pts.get(feature["t_shirt_size"], 3), 1)
        for s in stories:
            s["wsjf_score"] = round(5 / tshirt_pts.get(s["t_shirt_size"], 2), 1)
        for e in enablers:
            e["wsjf_score"] = round(4 / tshirt_pts.get(e["t_shirt_size"], 2), 1)

        # Update SAFe bridge
        bridge = conn.execute(f"SELECT id FROM nc_safe_bridge WHERE project_id={_ph}", (pid,)).fetchone()
        now = _now()
        decomposition = {
            "feature": feature,
            "stories": stories,
            "enablers": enablers,
        }
        decomp_json = json.dumps(decomposition)
        if bridge:
            conn.execute(
                f"UPDATE nc_safe_bridge SET safe_feature_id={_ph}, updated_at={_ph} WHERE project_id={_ph}", (decomp_json, now, pid)
            )
        else:
            conn.execute(
                "INSERT INTO nc_safe_bridge "
                "(id, project_id, safe_feature_id, created_at, "
                f" updated_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph})",
                (str(_uuid.uuid4()), pid, decomp_json, now, now),
            )
        conn.commit()

        _notify(
            conn,
            pid,
            "decomposition",
            f"SAFe decomposition: 1 Feature, {len(stories)} Stories, {len(enablers)} Enablers",
            f"WSJF={feature['wsjf_score']}",
        )
        conn.commit()
        conn.close()
        _audit("DECOMPOSE", "project", pid, f"{len(stories)} stories, {len(enablers)} enablers")

        return jsonify(
            {
                "feature": feature,
                "stories": stories,
                "enablers": enablers,
                "total_items": 1 + len(stories) + len(enablers),
            }
        )

    # ── COA Selection (AI-assisted migration) ─────────────────────────────
    @bp.route("/api/projects/<pid>/select-coa", methods=["POST"])
    @nc_login_required
    def nc_select_coa(pid):
        """Store HITL COA selection and feedback; regenerate migration phases."""
        data = request.get_json(force=True) or {}
        coa_id = data.get("coa_id")
        feedback = data.get("feedback", "")
        if not coa_id or coa_id not in (1, 2, 3):
            return jsonify({"error": "coa_id must be 1, 2, or 3"}), 400

        conn = get_connection()
        _ph = sql_placeholder(conn)
        proj = conn.execute(f"SELECT id FROM nc_projects WHERE id={_ph}", (pid,)).fetchone()
        if not proj:
            conn.close()
            return jsonify({"error": "Project not found"}), 404

        # Build COA JSON payload
        coa_json = json.dumps({
            "selected_coa": coa_id,
            "feedback": feedback,
            "coa_1": {"id": 1, "name": "Rip & Replace", "risk_level": "high", "estimated_downtime_hours": 4},
            "coa_2": {"id": 2, "name": "Phased Cutover", "risk_level": "medium", "estimated_downtime_hours": 1},
            "coa_3": {"id": 3, "name": "Side-by-Side VLAN", "risk_level": "low", "estimated_downtime_hours": 0, "recommended": True},
        })

        conn.execute(
            f"UPDATE nc_projects SET selected_coa={_ph}, coa_feedback={_ph}, coa_json={_ph} WHERE id={_ph}",
            (coa_id, feedback, coa_json, pid),
        )
        conn.commit()

        # Audit trail
        _audit("COA_SELECT", "project", pid, f"Selected COA-{coa_id}")
        conn.commit()
        conn.close()

        return jsonify({"ok": True, "selected_coa": coa_id, "feedback": feedback})

    # ── Global Connectivity Page ────────────────────────────────────────────
    @bp.route("/global")
    @nc_login_required
    def nc_global():
        """Global connectivity map — all approved/deployed projects stitched."""
        return render_template("network/global.html")

    # ── Global Topology API ────────────────────────────────────────────────

    @bp.route("/api/global-topology", methods=["GET"])
    @nc_login_required
    def nc_api_global_topology():
        """Aggregate topology data across all projects for global view."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        projects = []
        try:
            proj_rows = conn.execute(
                "SELECT id, name, status FROM nc_projects ORDER BY name"
            ).fetchall()
        except Exception:
            proj_rows = []

        for pr in proj_rows:
            pid = pr[0] if isinstance(pr, tuple) else pr["id"]
            pname = pr[1] if isinstance(pr, tuple) else pr["name"]
            pstatus = pr[2] if isinstance(pr, tuple) else pr["status"]
            try:
                topo_rows = conn.execute(
                    "SELECT t.id, t.name, t.graph_json FROM topologies t "
                    "JOIN nc_project_topologies pt ON pt.topology_id = t.id "
                    f"WHERE pt.project_id = {_ph}", (pid,)
                ).fetchall()
            except Exception:
                topo_rows = []

            topos = []
            for tr in topo_rows:
                tid = tr[0] if isinstance(tr, tuple) else tr["id"]
                tname = tr[1] if isinstance(tr, tuple) else tr["name"]
                gjson = tr[2] if isinstance(tr, tuple) else tr["graph_json"]
                try:
                    g = json.loads(gjson)
                    nc = len(g.get("nodes", []))
                    ec = len(g.get("edges", []))
                except Exception:
                    nc, ec = 0, 0
                topos.append({"id": tid, "name": tname, "node_count": nc, "edge_count": ec})
            projects.append({"id": pid, "name": pname, "status": pstatus, "topologies": topos})

        # Interconnects
        interconnects = []
        try:
            ic_rows = conn.execute(
                "SELECT id, src_project_id, dst_project_id, circuit_id, protocol, bandwidth, notes "
                "FROM nc_interconnects ORDER BY created_at DESC"
            ).fetchall()
            for ic in ic_rows:
                interconnects.append({
                    "id": ic[0], "src_project_id": ic[1], "dst_project_id": ic[2],
                    "circuit_id": ic[3], "protocol": ic[4], "bandwidth": ic[5], "notes": ic[6],
                })
        except Exception:
            pass

        conn.close()
        return jsonify({
            "total_projects": len(projects),
            "projects": projects,
            "total_interconnects": len(interconnects),
            "interconnects": interconnects,
        })

    @bp.route("/api/interconnects", methods=["POST"])
    @nc_login_required
    def nc_api_add_interconnect():
        """Add a project-to-project interconnect."""
        import uuid as _uid
        body = request.json or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            conn.execute(
                "INSERT INTO nc_interconnects (id, src_project_id, dst_project_id, circuit_id, "
                f"protocol, bandwidth, notes, created_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},datetime('now'))",
                (str(_uid.uuid4())[:12], body.get("src_project_id"), body.get("dst_project_id"),
                 body.get("circuit_id", ""), body.get("protocol", ""), body.get("bandwidth", ""),
                 body.get("notes", "")),
            )
            conn.commit()
        except Exception as e:
            conn.close()
            return jsonify({"error": str(e)}), 500
        conn.close()
        return jsonify({"ok": True}), 201

    @bp.route("/api/interconnects/<ic_id>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_interconnect(ic_id):
        """Delete an interconnect."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_interconnects WHERE id = {_ph}", (ic_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/conflicts", methods=["GET"])
    @nc_login_required
    def nc_api_check_conflicts():
        """Check for IPAM/circuit conflicts across projects."""
        conn = get_connection()
        conflicts = []
        ipam_count, circuit_count = 0, 0
        try:
            # Check overlapping IPAM blocks
            ipam_rows = conn.execute("SELECT network, topology_id FROM nc_ipam_blocks").fetchall()
            ipam_count = len(ipam_rows)
            seen_nets = {}
            for row in ipam_rows:
                net = row[0] if isinstance(row, tuple) else row["network"]
                tid = row[1] if isinstance(row, tuple) else row["topology_id"]
                if net in seen_nets and seen_nets[net] != tid:
                    conflicts.append({
                        "type": "IPAM Overlap",
                        "severity": "high",
                        "detail": f"Network {net} used in topologies {seen_nets[net]} and {tid}",
                    })
                seen_nets[net] = tid

            # Check duplicate circuit IDs
            circ_rows = conn.execute("SELECT circuit_id, COUNT(*) as cnt FROM nc_circuits GROUP BY circuit_id HAVING cnt > 1").fetchall()
            circuit_count = conn.execute("SELECT COUNT(*) FROM nc_circuits").fetchone()[0]
            for row in circ_rows:
                cid = row[0] if isinstance(row, tuple) else row["circuit_id"]
                conflicts.append({
                    "type": "Duplicate Circuit",
                    "severity": "medium",
                    "detail": f"Circuit ID {cid} appears in multiple topologies",
                })
        except Exception:
            pass
        conn.close()
        return jsonify({
            "conflicts": conflicts,
            "checked": {"ipam_blocks": ipam_count, "circuits": circuit_count},
        })

    @bp.route("/conflicts")
    @nc_login_required
    def nc_conflicts_page():
        """HITL conflict resolution UI."""
        return render_template("network/conflicts.html")

    @bp.route("/api/conflicts/resolve", methods=["POST"])
    @nc_login_required
    def nc_api_conflicts_resolve():
        """Record a HITL conflict resolution action."""
        import uuid as _uuid
        from datetime import datetime, timezone
        data = request.get_json(force=True) or {}
        conflict_type = str(data.get("conflict_type") or "").strip()
        detail = str(data.get("detail") or "").strip()
        severity = str(data.get("severity") or "medium").strip()
        action = str(data.get("action") or "acknowledged").strip()
        note = str(data.get("note") or "").strip()
        if not conflict_type or not detail:
            return jsonify({"error": "conflict_type and detail are required"}), 400
        if action not in ("acknowledged", "resolved"):
            return jsonify({"error": "action must be acknowledged or resolved"}), 400
        if severity not in ("high", "medium", "low"):
            severity = "medium"
        resolved_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        row_id = str(_uuid.uuid4())
        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            conn.execute(
                "INSERT INTO nc_conflict_resolutions (id, conflict_type, detail, severity, action, note, resolved_at)"
                f" VALUES ({_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph}, {_ph})",
                (row_id, conflict_type, detail, severity, action, note, resolved_at),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "id": row_id, "resolved_at": resolved_at})

    @bp.route("/api/conflicts/resolutions", methods=["GET"])
    @nc_login_required
    def nc_api_conflicts_resolutions():
        """Return all recorded conflict resolution actions, newest first."""
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT id, conflict_type, detail, severity, action, note, resolved_at"
                " FROM nc_conflict_resolutions ORDER BY resolved_at DESC LIMIT 200"
            ).fetchall()
        finally:
            conn.close()
        resolutions = [_row_to_dict(r) for r in rows]
        return jsonify({"resolutions": resolutions})

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
        interconnects = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_interconnects").fetchall()]
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
                all_nodes.append(
                    {
                        "id": nid,
                        "label": n.get("label", ""),
                        "type": n.get("type", ""),
                        "x": (n.get("x", 0) or 0) + offset_x,
                        "y": n.get("y", 0) or 0,
                        "project": r["project_name"],
                        "topology": r["name"],
                    }
                )
                group_nodes.append(nid)
            for e in g.get("edges", []):
                all_edges.append(
                    {
                        "source": f"{prefix}_{e['source']}",
                        "target": f"{prefix}_{e['target']}",
                        "label": e.get("label", ""),
                        "protocol": e.get("protocol", ""),
                    }
                )
            project_groups.append(
                {
                    "project": r["project_name"],
                    "topology": r["name"],
                    "node_ids": group_nodes,
                    "x": offset_x,
                    "y": 0,
                }
            )
            offset_x += 600

        # Add interconnect edges
        for ic in interconnects:
            all_edges.append(
                {
                    "source": f"ic_src_{ic['id'][:8]}",
                    "target": f"ic_dst_{ic['id'][:8]}",
                    "label": ic.get("circuit_id", ""),
                    "protocol": ic.get("protocol", ""),
                    "is_interconnect": True,
                }
            )

        return jsonify(
            {
                "nodes": all_nodes,
                "edges": all_edges,
                "groups": project_groups,
                "total_nodes": len(all_nodes),
                "total_edges": len(all_edges),
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # API: CSP Groups
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/groups/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_groups(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        rows = conn.execute(f"SELECT * FROM nc_groups WHERE topology_id={_ph} ORDER BY created_at", (topo_id,)).fetchall()
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
            _ph = sql_placeholder(conn)
            topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
            if topo:
                try:
                    graph = json.loads(topo["graph_json"])
                except Exception:
                    graph = {"nodes": [], "edges": []}
                for comp in CSP_GROUP_DEFAULTS[csp]:
                    nid = f"{csp}-{str(_uuid.uuid4())[:8]}"
                    graph["nodes"].append(
                        {
                            "id": nid,
                            "label": comp["label"],
                            "type": comp["type"],
                            "x": pos_x + comp["dx"],
                            "y": pos_y + comp["dy"],
                            "group_id": gid,
                        }
                    )
                    auto_nodes.append(nid)
                conn.execute(
                    f"UPDATE topologies SET graph_json={_ph}, updated_at={_ph} WHERE id={_ph}", (json.dumps(graph), now, topo_id)
                )
                conn.commit()
                invalidate_parsed_graph(topo_id)  # ndc-perf-02
            conn.close()
        csp_labels = {"aws": "AWS", "azure": "Azure", "gcp": "GCP", "oci": "OCI", "ibm": "IBM Cloud"}
        label = data.get("label", csp_labels.get(csp, csp.upper()))
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_groups (id, topology_id, parent_id, csp, group_type, label, description, "
            "auto_nodes_json, pos_x, pos_y, width, height, color, collapsed, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                gid,
                topo_id,
                data.get("parent_id"),
                csp,
                group_type,
                label,
                data.get("description", ""),
                json.dumps(auto_nodes),
                pos_x,
                pos_y,
                data.get("width", 400),
                data.get("height", 300),
                data.get("color"),
                0,
                now,
            ),
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
        _ph = sql_placeholder(conn)
        allowed = [
            "parent_id",
            "label",
            "description",
            "pos_x",
            "pos_y",
            "width",
            "height",
            "color",
            "collapsed",
            "group_type",
        ]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(gid)
        conn.execute(f"UPDATE nc_groups SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/groups/<topo_id>/<gid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_group(topo_id, gid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        group = conn.execute(f"SELECT auto_nodes_json FROM nc_groups WHERE id={_ph}", (gid,)).fetchone()
        if group:
            try:
                auto_ids = set(json.loads(group["auto_nodes_json"] or "[]"))
                if auto_ids:
                    topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
                    if topo:
                        graph = json.loads(topo["graph_json"])
                        graph["nodes"] = [n for n in graph["nodes"] if n["id"] not in auto_ids]
                        graph["edges"] = [
                            e for e in graph["edges"] if e["source"] not in auto_ids and e["target"] not in auto_ids
                        ]
                        conn.execute(
                            f"UPDATE topologies SET graph_json={_ph}, updated_at={_ph} WHERE id={_ph}",
                            (json.dumps(graph), _now(), topo_id),
                        )
                        invalidate_parsed_graph(topo_id)  # ndc-perf-02
            except Exception:
                pass
        conn.execute(f"DELETE FROM nc_groups WHERE parent_id={_ph}", (gid,))
        conn.execute(f"DELETE FROM nc_groups WHERE id={_ph}", (gid,))
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
            "fips-140-l1",
            "fips-140-l2",
            "fips-140-l3",
            "fips-140-l4",
            "hsm",
            "type1-encryptor",
            "kg-175d",
            "kg-175g",
            "kg-250",
            "kg-340",
            "kg-245x",
            "kg-255",
            "macsec",
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
        _ph = sql_placeholder(conn)
        rows = conn.execute(
            f"SELECT * FROM nc_boundaries WHERE topology_id={_ph} ORDER BY created_at",
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
            _ph = sql_placeholder(conn)
            topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
            conn.close()
            if topo:
                try:
                    graph = json.loads(topo["graph_json"])
                except Exception:
                    graph = {"nodes": []}
                nid_set = set(node_ids)
                node_types = [n["type"] for n in graph.get("nodes", []) if n["id"] in nid_set]

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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_boundaries "
            "(id, topology_id, label, classification, color, fill_opacity, "
            "node_ids, stig_tags, pos_x, pos_y, width, height, snap_grid, notes, "
            "created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                bid,
                topo_id,
                label,
                classification,
                color,
                data.get("fill_opacity", 0.08),
                json.dumps(node_ids),
                json.dumps(stig_tags),
                pos_x,
                pos_y,
                width,
                height,
                snap,
                data.get("notes", ""),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "boundary", bid, f"{classification} — {label}")
        return jsonify(
            {
                "id": bid,
                "label": label,
                "classification": classification,
                "color": color,
                "stig_tags": stig_tags,
                "pos_x": pos_x,
                "pos_y": pos_y,
                "width": width,
                "height": height,
                "node_ids": node_ids,
            }
        ), 201

    @bp.route("/api/boundaries/<topo_id>/<bid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_boundary(topo_id, bid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        allowed = [
            "label",
            "classification",
            "color",
            "fill_opacity",
            "node_ids",
            "stig_tags",
            "pos_x",
            "pos_y",
            "width",
            "height",
            "snap_grid",
            "notes",
        ]
        fields, values = [], []
        for k in allowed:
            if k in data:
                v = data[k]
                if k in ("node_ids", "stig_tags") and isinstance(v, list):
                    v = json.dumps(v)
                fields.append(f"{k}={_ph}")
                values.append(v)
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append(f"updated_at={_ph}")
        values.append(_now())
        values.append(bid)
        conn.execute(f"UPDATE nc_boundaries SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/boundaries/<topo_id>/<bid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_boundary(topo_id, bid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_boundaries WHERE id={_ph} AND topology_id={_ph}", (bid, topo_id))
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
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_boundaries "
            "(id, topology_id, label, classification, color, fill_opacity, "
            "node_ids, stig_tags, pos_x, pos_y, width, height, snap_grid, notes, "
            "created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                bid,
                topo_id,
                label,
                classification,
                color,
                0.08,
                json.dumps(node_ids),
                json.dumps(stig_tags),
                pos_x,
                pos_y,
                width,
                height,
                snap,
                data.get("notes", ""),
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "boundary", bid, f"auto-fence {classification} — {len(matched)} nodes")
        return jsonify(
            {
                "id": bid,
                "label": label,
                "classification": classification,
                "color": color,
                "fill_opacity": 0.08,
                "stig_tags": stig_tags,
                "pos_x": pos_x,
                "pos_y": pos_y,
                "width": width,
                "height": height,
                "node_ids": node_ids,
                "node_count": len(matched),
            }
        ), 201

    # ══════════════════════════════════════════════════════════════════════
    # API: Monte Carlo Simulation
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/mc/scenarios/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_mc_scenarios(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        rows = conn.execute(
            f"SELECT * FROM nc_mc_scenarios WHERE topology_id={_ph} ORDER BY created_at DESC", (topo_id,)
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/mc/scenarios/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_create_mc_scenario(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        sid = str(_uuid.uuid4())
        now = _now()
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_mc_scenarios (id, topology_id, name, scenario_type, description, config_json, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                sid,
                topo_id,
                data.get("name", "Untitled Scenario"),
                data.get("scenario_type", "random"),
                data.get("description", ""),
                json.dumps(data.get("config", {})),
                now,
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "mc_scenario", sid, data.get("name", ""))
        return jsonify({"id": sid}), 201

    @bp.route("/api/mc/run/<scenario_id>", methods=["POST"])
    @nc_login_required
    def nc_api_run_mc(scenario_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        scenario = conn.execute(f"SELECT * FROM nc_mc_scenarios WHERE id={_ph}", (scenario_id,)).fetchone()
        if not scenario:
            conn.close()
            return jsonify({"error": "Scenario not found"}), 404
        scenario = _row_to_dict(scenario)
        topo_id = scenario["topology_id"]
        topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
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
        result = run_monte_carlo(
            graph=graph,
            scenario_name=scenario["name"],
            scenario_type=scenario["scenario_type"],
            config=config,
            iterations=iterations,
        )
        run_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_mc_runs (id, scenario_id, topology_id, iterations, result_json, ai_recommendations, ran_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                run_id,
                scenario_id,
                topo_id,
                iterations,
                json.dumps(result),
                "\n".join(result.get("recommendations", [])),
                now,
            ),
        )
        conn.commit()
        conn.close()
        _audit("MC_RUN", "mc_scenario", scenario_id, f"{iterations} iters")
        return jsonify({"run_id": run_id, **result})

    @bp.route("/api/mc/runs/<topo_id>", methods=["GET"])
    @nc_login_required
    def nc_api_list_mc_runs(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        rows = conn.execute(
            "SELECT r.id, r.scenario_id, r.iterations, r.ran_at, r.ai_recommendations, "
            "s.name AS scenario_name, s.scenario_type "
            "FROM nc_mc_runs r JOIN nc_mc_scenarios s ON s.id=r.scenario_id "
            f"WHERE r.topology_id={_ph} ORDER BY r.ran_at DESC",
            (topo_id,),
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/mc/runs/<topo_id>/<run_id>", methods=["GET"])
    @nc_login_required
    def nc_api_get_mc_run(topo_id, run_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_mc_runs WHERE id={_ph} AND topology_id={_ph}", (run_id, topo_id)).fetchone()
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
            _ph = sql_placeholder(conn)
            conn.execute(
                "INSERT INTO nc_backups (id, backup_type, file_path, file_size_bytes, includes_json, notes, created_at) "
                f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (
                    backup_id,
                    data.get("backup_type", "manual"),
                    str(zip_path),
                    file_size,
                    json.dumps(includes),
                    data.get("notes", ""),
                    now,
                ),
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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_backups WHERE id={_ph}", (backup_id,)).fetchone()
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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        new_id = str(_uuid.uuid4())
        now = _now()
        name = data.get("name", f"{row['name']} (copy)")
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, template_id, classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (new_id, name, row["description"], row["graph_json"], row["template_id"], row["classification"], now, now),
        )
        conn.commit()
        conn.close()
        _audit("SAVE_AS", "topology", new_id, f"from {topo_id}")
        return jsonify({"id": new_id, "name": name, "redirect": f"/network/canvas/{new_id}"}), 201

    @bp.route("/api/topologies/<topo_id>/create-lab", methods=["POST"])
    @nc_login_required
    def nc_api_create_lab(topo_id):
        """AI-reduce production topology to a scaled-down representative lab version.

        Reduction rules applied:
        - 1 representative node per nc_group cluster (first non-L1 member)
        - L1 physical media stripped (SFP, fiber, patch panels, media converters)
        - IPs reassigned to 10.99.x.x range
        - Node labels prefixed LAB-
        - Security zones, VRFs, subnets, roles preserved
        - Lineage recorded in nc_lab_clones; version snapshot saved on source (phase=lab)
        """
        import re as _re

        L1_TYPES = {
            "sfp", "sfp-plus", "qsfp", "qsfp-dd",
            "media-ge", "media-10ge", "media-40ge", "media-100ge", "media-400ge",
            "media-fiber", "media-optical", "media-converter",
            "patch-panel", "odf",
        }

        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404

        try:
            graph = json.loads(row["graph_json"])
        except Exception:
            graph = {"nodes": [], "edges": []}

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])

        # Load nc_groups to identify cluster membership
        group_rows = conn.execute(
            f"SELECT id, auto_nodes_json FROM nc_groups WHERE topology_id={_ph}", (topo_id,)
        ).fetchall()

        node_type_map = {n["id"]: n.get("type", "") for n in nodes}

        # Map each node to its first group; pick one representative per group
        node_to_group: dict = {}
        group_representative: dict = {}
        for gr in group_rows:
            gid = gr["id"]
            try:
                members = json.loads(gr["auto_nodes_json"] or "[]")
            except Exception:
                members = []
            for nid in members:
                if nid not in node_to_group:
                    node_to_group[nid] = gid
            rep = next(
                (nid for nid in members if node_type_map.get(nid, "") not in L1_TYPES),
                members[0] if members else None,
            )
            if rep is not None:
                group_representative[gid] = rep

        # Determine kept nodes and the ID remap table
        kept_node_ids: set = set()
        id_remap: dict = {}
        for n in nodes:
            nid = n["id"]
            ntype = n.get("type", "")
            if ntype in L1_TYPES:
                continue
            gid = node_to_group.get(nid)
            if gid is not None:
                rep = group_representative.get(gid)
                if rep and rep != nid:
                    id_remap[nid] = rep
                    continue
            kept_node_ids.add(nid)

        # IP reassignment: 10.99.x.x counter
        _ip_state = [1, 1]

        def _next_lab_ip():
            ip = f"10.99.{_ip_state[0]}.{_ip_state[1]}"
            _ip_state[1] += 1
            if _ip_state[1] > 254:
                _ip_state[1] = 1
                _ip_state[0] += 1
            return ip

        _IP_RE = _re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?\b")

        def _replace_ips(text):
            if not isinstance(text, str):
                return text
            def _sub(m):
                parts = m.group(0).split("/")
                return _next_lab_ip() + ("/" + parts[1] if len(parts) > 1 else "")
            return _IP_RE.sub(_sub, text)

        def _sanitize(val):
            if isinstance(val, dict):
                return {k: _sanitize(v) for k, v in val.items()}
            if isinstance(val, list):
                return [_sanitize(v) for v in val]
            return _replace_ips(val)

        # Build lab nodes
        lab_nodes = []
        for n in nodes:
            if n["id"] not in kept_node_ids:
                continue
            lab_n = dict(n)
            lbl = lab_n.get("label", lab_n["id"])
            lab_n["label"] = f"LAB-{lbl}" if not lbl.startswith("LAB-") else lbl
            lab_n["label"] = _replace_ips(lab_n["label"])
            if "config" in lab_n:
                lab_n["config"] = _sanitize(lab_n["config"])
            lab_nodes.append(lab_n)

        # Build lab edges — remap endpoints, skip self-loops and duplicates
        seen_edges: set = set()
        lab_edges = []
        for e in edges:
            src = id_remap.get(e.get("source"), e.get("source"))
            dst = id_remap.get(e.get("target"), e.get("target"))
            if src not in kept_node_ids or dst not in kept_node_ids:
                continue
            if src == dst:
                continue
            key = (min(src, dst), max(src, dst))
            if key in seen_edges:
                continue
            seen_edges.add(key)
            lab_e = dict(e)
            lab_e["source"] = src
            lab_e["target"] = dst
            lab_edges.append(lab_e)

        lab_name = f"LAB-{row['name']}"
        now = _now()
        new_id = str(_uuid.uuid4())

        # Create the lab topology
        conn.execute(
            "INSERT INTO topologies (id, name, description, graph_json, template_id, classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                new_id,
                lab_name,
                f"Lab of '{row['name']}' (source: {topo_id}). {(row['description'] or '')}".strip(". "),
                json.dumps({"nodes": lab_nodes, "edges": lab_edges}),
                None,
                row["classification"] or "public",
                now,
                now,
            ),
        )

        # Record lineage in nc_lab_clones
        clone_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_lab_clones "
            "(clone_id, parent_design_id, lineage, redaction_log, created_at, classification) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                clone_id,
                topo_id,
                json.dumps([topo_id]),
                json.dumps({
                    "l1_stripped": sorted(L1_TYPES),
                    "ips_replaced": True,
                    "label_prefix": "LAB-",
                    "clusters_reduced": len(group_representative),
                }),
                now,
                row["classification"] or "UNCLASSIFIED",
            ),
        )

        # Save a version snapshot on the source topology linking to the new lab
        last_ver = conn.execute(
            f"SELECT MAX(version_num) FROM nc_versions WHERE topology_id={_ph}", (topo_id,)
        ).fetchone()[0]
        ver_num = (last_ver or 0) + 1
        vid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_versions "
            "(id, topology_id, version_num, label, phase, graph_json, created_by, notes, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                vid,
                topo_id,
                ver_num,
                f"Lab snapshot → {lab_name}",
                "lab",
                row["graph_json"],
                "system",
                json.dumps({"lab_topology_id": new_id, "lab_name": lab_name}),
                now,
            ),
        )

        conn.commit()
        conn.close()
        _audit("CREATE_LAB", "topology", new_id, f"lab of {topo_id}, nodes={len(lab_nodes)}, edges={len(lab_edges)}")
        return jsonify({
            "id": new_id,
            "name": lab_name,
            "source_id": topo_id,
            "nodes": len(lab_nodes),
            "edges": len(lab_edges),
            "redirect": f"/network/canvas/{new_id}",
        }), 201

    @bp.route("/api/topologies/<topo_id>/save-as-template", methods=["POST"])
    @nc_login_required
    def nc_api_save_as_template(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        tpl_id = f"tpl-{str(_uuid.uuid4())[:8]}"
        name = data.get("name", row["name"])
        category = data.get("category", "Custom")
        tags = json.dumps(data.get("tags", ["custom", "user-created"]))
        conn.execute(
            f"INSERT INTO nc_templates (id, name, category, description, graph_json, tags) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (tpl_id, name, category, data.get("description", row["description"] or ""), row["graph_json"], tags),
        )
        conn.commit()
        conn.close()
        _audit("SAVE_AS_TEMPLATE", "template", tpl_id, f"from {topo_id}")
        return jsonify({"id": tpl_id, "name": name}), 201

    # ══════════════════════════════════════════════════════════════════════
    # AI Topology Generator — natural language → JointJS graph JSON
    # Uses Ollama (scanner tier) for air-gap compatibility
    # ══════════════════════════════════════════════════════════════════════
