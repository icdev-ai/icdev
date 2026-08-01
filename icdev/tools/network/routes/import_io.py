# CUI // SP-CTI
"""ICDEV Network Design Canvas -- import_io route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_import_io_routes(bp).
"""
from __future__ import annotations

import json
import os
import uuid as _uuid
from flask import jsonify, render_template, request
from tools.network.routes._common import _classify_imported_nodes
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import (
    _NDC_LIFECYCLE,
    _audit,
    _crud_create,
    _crud_delete,
    _now,
    _row_to_dict,
    invalidate_parsed_graph,
    nc_login_required,
)
from tools.network.compliance import run_compliance_audit
from tools.network.db.init_db import get_connection
from tools.network.export_import import import_drawio, import_svg, import_vdx


def register_import_io_routes(bp):
    """Register import_io routes on the NDC blueprint."""

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
        _ph = sql_placeholder(conn)
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
                f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (topo_id, name, f"Bulk import ({fmt})", json.dumps(graph), "public", now, now),
            )
            if project_id:
                conn.execute(
                    f"INSERT OR IGNORE INTO nc_project_topologies (project_id, topology_id) VALUES ({_ph},{_ph})",
                    (project_id, topo_id),
                )
            results.append(
                {
                    "id": topo_id,
                    "name": name,
                    "nodes": len(graph.get("nodes", [])),
                    "edges": len(graph.get("edges", [])),
                }
            )
        conn.commit()
        conn.close()
        _audit("BULK_IMPORT", "topology", "", f"{len(results)} files imported")
        return jsonify(
            {
                "imported": len([r for r in results if "id" in r]),
                "failed": len([r for r in results if "error" in r]),
                "results": results,
            }
        ), 201

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
        _ph = sql_placeholder(conn)
        merged_nodes = []
        merged_edges = []
        offset_x = 0

        for tid in topo_ids:
            row = conn.execute(f"SELECT name, graph_json FROM topologies WHERE id={_ph}", (tid,)).fetchone()
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
                merged_nodes.append(
                    {
                        **n,
                        "id": new_id,
                        "x": (n.get("x") or 0) + offset_x,
                        "config": {
                            **(n.get("config") or n.get("configData") or {}),
                            "_source_topology": topo_name,
                            "_source_id": n["id"],
                        },
                    }
                )
            for e in g.get("edges", []):
                merged_edges.append(
                    {
                        **e,
                        "id": f"{prefix}_{e.get('id', '')}",
                        "source": f"{prefix}_{e['source']}",
                        "target": f"{prefix}_{e['target']}",
                    }
                )
            offset_x += 700

        # Add user-defined interconnect edges
        for ic in interconnects:
            merged_edges.append(
                {
                    "id": str(_uuid.uuid4())[:8],
                    "source": ic.get("source_node_id", ""),
                    "target": ic.get("target_node_id", ""),
                    "label": ic.get("label", "Interconnect"),
                    "protocol": ic.get("protocol", ""),
                }
            )

        merged_graph = {"nodes": merged_nodes, "edges": merged_edges}
        topo_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, "
            " classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (topo_id, name, f"Stitched from {len(topo_ids)} topologies", json.dumps(merged_graph), "public", now, now),
        )
        conn.commit()
        conn.close()
        _audit("STITCH", "topology", topo_id, f"Merged {len(topo_ids)} topologies")
        return jsonify(
            {
                "id": topo_id,
                "name": name,
                "nodes": len(merged_nodes),
                "edges": len(merged_edges),
                "source_topologies": len(topo_ids),
            }
        ), 201

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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, "
            " classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (topo_id, name, f"Audit import ({fmt})", json.dumps(graph), "CUI", now, now),
        )
        conn.commit()

        # Run compliance audit
        audit_result = run_compliance_audit(topo_id, graph, ["fisma_high", "stig"], "CUI")
        total_p = sum(s["passed"] for s in audit_result["scores"].values())
        total_f = sum(s["failed"] for s in audit_result["scores"].values())
        audit_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_compliance_checks "
            "(id, topology_id, check_type, passed, failed, "
            f" findings_json, ran_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (audit_id, topo_id, "fisma_high,stig", total_p, total_f, json.dumps(audit_result["findings"]), now),
        )
        conn.commit()
        conn.close()

        _audit("IMPORT_AUDIT", "topology", topo_id, fmt)

        return jsonify(
            {
                "id": topo_id,
                "name": name,
                "nodes": len(graph.get("nodes", [])),
                "edges": len(graph.get("edges", [])),
                "compliance": {
                    "passed": total_p,
                    "failed": total_f,
                    "findings": len(audit_result["findings"]),
                    "scores": audit_result["scores"],
                },
                "classified_types": dict(
                    sorted(
                        {
                            n["type"]: sum(1 for m in graph["nodes"] if m["type"] == n["type"]) for n in graph["nodes"]
                        }.items()
                    )
                ),
            }
        ), 201

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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
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
            f"UPDATE topologies SET graph_json={_ph}, updated_at={_ph} WHERE id={_ph}", (json.dumps(graph), _now(), topo_id)
        )
        conn.commit()
        conn.close()
        invalidate_parsed_graph(topo_id)  # ndc-perf-02
        return jsonify({"reclassified": changed, "total_nodes": len(graph.get("nodes", []))})

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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_device_profiles WHERE id={_ph}", (pid,)).fetchone()
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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_device_profiles "
            "(id, vendor, platform, description, commands_json, "
            " is_builtin, created_by, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},0,{_ph},{_ph})",
            (
                pid,
                data.get("vendor", "Custom"),
                data.get("platform", "Custom"),
                data.get("description", ""),
                commands,
                data.get("created_by", ""),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "device_profile", pid, f"{data.get('vendor')} {data.get('platform')}")
        return jsonify({"id": pid}), 201

    @bp.route("/api/device-profiles/<pid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_device_profile(pid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT is_builtin FROM nc_device_profiles WHERE id={_ph}", (pid,)).fetchone()
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
                fields.append(f"{k}={_ph}")
                values.append(v)
        # Allow adding commands to built-in profiles
        if row[0] and "commands" in data:
            existing = conn.execute(f"SELECT commands_json FROM nc_device_profiles WHERE id={_ph}", (pid,)).fetchone()
            try:
                cmds = json.loads(existing[0] or "{}")
            except Exception:
                cmds = {}
            cmds.update(data["commands"])
            fields.append(f"commands_json={_ph}")
            values.append(json.dumps(cmds))
        if fields:
            values.append(pid)
            conn.execute(
                f"UPDATE nc_device_profiles "  # nosec B608
                f"SET {', '.join(fields)} WHERE id={_ph}",
                values,
            )
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/device-profiles/<pid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_device_profile(pid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT is_builtin FROM nc_device_profiles WHERE id={_ph}", (pid,)).fetchone()
        if row and row[0]:
            conn.close()
            return jsonify({"error": "Cannot delete built-in profile"}), 403
        conn.execute(f"DELETE FROM nc_device_profiles WHERE id={_ph}", (pid,))
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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_discovery_configs "
            "(id, name, profile_id, targets, credential_ref, "
            " method, read_only, rate_limit_per_sec, "
            " max_concurrent, timeout_per_cmd, timeout_per_device, "
            " hop_limit, max_devices, whitelist_subnets, "
            " blacklist_subnets, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},1,{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                cid,
                data.get("name", "Discovery Scan"),
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
                wl,
                bl,
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "discovery_config", cid)
        return jsonify({"id": cid}), 201

    @bp.route("/api/discovery-configs/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_discovery_config(cid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_discovery_configs WHERE id={_ph}", (cid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Collected Configs ─────────────────────────────────────────────────
    @bp.route("/api/collected-configs", methods=["GET"])
    @nc_login_required
    def nc_api_list_collected_configs():
        device_ip = request.args.get("device_ip", "")
        conn = get_connection()
        _ph = sql_placeholder(conn)
        if device_ip:
            rows = conn.execute(
                "SELECT id, device_ip, hostname, command_name, "
                "collected_at FROM nc_collected_configs "
                f"WHERE device_ip={_ph} ORDER BY collected_at DESC",
                (device_ip,),
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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT * FROM nc_collected_configs WHERE id={_ph}", (cid,)).fetchone()
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
        _ph = sql_placeholder(conn)
        # Dedup: keep latest per device_ip + command_name
        conn.execute(
            f"DELETE FROM nc_collected_configs WHERE device_ip={_ph} AND command_name={_ph}",
            (data.get("device_ip", ""), data.get("command_name", "manual")),
        )
        conn.execute(
            "INSERT INTO nc_collected_configs "
            "(id, device_ip, hostname, profile_id, command_name, "
            " output_text, parsed_json, collected_at, topology_id) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                cid,
                data.get("device_ip", ""),
                data.get("hostname", ""),
                data.get("profile_id"),
                data.get("command_name", "manual"),
                data.get("output_text", ""),
                parsed,
                _now(),
                data.get("topology_id"),
            ),
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
        _ph = sql_placeholder(conn)
        if topo_id:
            rows = conn.execute(
                f"SELECT * FROM nc_device_geo WHERE topology_id={_ph} ORDER BY site_name, label", (topo_id,)
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
        _ph = sql_placeholder(conn)
        # Dedup
        conn.execute(
            f"DELETE FROM nc_device_geo WHERE topology_id={_ph} AND node_id={_ph}",
            (data.get("topology_id"), data.get("node_id")),
        )
        gid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_device_geo "
            "(id, topology_id, node_id, label, site_name, "
            " latitude, longitude, city, state, country, "
            " facility, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                gid,
                data.get("topology_id"),
                data.get("node_id"),
                data.get("label", ""),
                data.get("site_name", ""),
                data.get("latitude", 0),
                data.get("longitude", 0),
                data.get("city", ""),
                data.get("state", ""),
                data.get("country", "US"),
                data.get("facility", ""),
                _now(),
            ),
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
        _ph = sql_placeholder(conn)
        count = 0
        for d in devices:
            conn.execute(
                f"DELETE FROM nc_device_geo WHERE topology_id={_ph} AND node_id={_ph}", (d.get("topology_id"), d.get("node_id"))
            )
            conn.execute(
                "INSERT INTO nc_device_geo "
                "(id, topology_id, node_id, label, site_name, "
                " latitude, longitude, city, state, country, "
                " facility, created_at) "
                f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (
                    str(_uuid.uuid4()),
                    d.get("topology_id"),
                    d.get("node_id"),
                    d.get("label", ""),
                    d.get("site_name", ""),
                    d.get("latitude", 0),
                    d.get("longitude", 0),
                    d.get("city", ""),
                    d.get("state", ""),
                    d.get("country", "US"),
                    d.get("facility", ""),
                    _now(),
                ),
            )
            count += 1
        conn.commit()
        conn.close()
        return jsonify({"set": count}), 201

    @bp.route("/api/device-geo/<gid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_device_geo(gid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_device_geo WHERE id={_ph}", (gid,))
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
        if os.environ.get("NETWORK_MAP_ENABLED", "true").lower() not in ("1", "true", "yes"):
            return render_template(
                "network/index.html",
                flash_msg="Map view is disabled in air-gap mode. Set NETWORK_MAP_ENABLED=true to enable.",
            )
        return render_template("network/map.html")

    # ══════════════════════════════════════════════════════════════════════
    # P2: Charts / Visualization
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/charts/compliance-trend", methods=["GET"])
    @nc_login_required
    def nc_api_chart_compliance_trend():
        """Compliance score trend over time (from history + current)."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        # Get history
        rows = conn.execute(
            "SELECT project_id, compliance_pct, recorded_at FROM nc_compliance_history ORDER BY recorded_at"
        ).fetchall()
        # Group by project
        by_project = {}
        for r in rows:
            r = _row_to_dict(r)
            pid = r.get("project_id", "")
            by_project.setdefault(pid, []).append(
                {
                    "pct": r.get("compliance_pct", 0),
                    "date": (r.get("recorded_at") or "")[:10],
                }
            )
        # Add project names
        projects = {}
        for pid in by_project:
            name_row = conn.execute(f"SELECT name FROM nc_projects WHERE id={_ph}", (pid,)).fetchone()
            projects[pid] = {
                "name": name_row[0] if name_row else pid[:8],
                "data": by_project[pid],
            }
        conn.close()
        return jsonify({"projects": projects})

    @bp.route("/api/charts/compliance-snapshot", methods=["POST"])
    @nc_login_required
    def nc_api_chart_compliance_snapshot():
        """Record current compliance scores for all projects (for trending)."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        now = _now()
        recorded = 0
        for p in conn.execute("SELECT id FROM nc_projects").fetchall():
            pid = p[0]
            tids = [
                r[0]
                for r in conn.execute(
                    f"SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}", (pid,)
                ).fetchall()
            ]
            tp = tf = cat1 = findings = 0
            for tid in tids:
                row = conn.execute(
                    f"SELECT passed, failed FROM nc_compliance_checks WHERE topology_id={_ph} ORDER BY ran_at DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                if row:
                    tp += row[0] or 0
                    tf += row[1] or 0
                of = conn.execute(
                    f"SELECT COUNT(*) FROM nc_compliance_findings WHERE topology_id={_ph} AND status='open'", (tid,)
                ).fetchone()
                findings += of[0] if of else 0
                c1 = conn.execute(
                    "SELECT COUNT(*) FROM nc_compliance_findings "
                    f"WHERE topology_id={_ph} AND status='open' "
                    "AND severity='CAT1'",
                    (tid,),
                ).fetchone()
                cat1 += c1[0] if c1 else 0
            total = tp + tf
            if total:
                pct = round(tp * 100 / total)
                conn.execute(
                    "INSERT INTO nc_compliance_history "
                    "(id, project_id, compliance_pct, open_findings, "
                    f" cat1_count, recorded_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (str(_uuid.uuid4()), pid, pct, findings, cat1, now),
                )
                recorded += 1
        conn.commit()
        conn.close()
        return jsonify({"recorded": recorded})

    @bp.route("/api/charts/capacity-overview", methods=["GET"])
    @nc_login_required
    def nc_api_chart_capacity_overview():
        """Capacity utilization across facilities + ports."""
        conn = get_connection()
        facilities = []
        for f in conn.execute(
            "SELECT name, total_power_kw, used_power_kw, "
            "total_cooling_tons, used_cooling_tons, "
            "total_racks, used_racks "
            "FROM nc_facilities ORDER BY name"
        ).fetchall():
            f = _row_to_dict(f)
            facilities.append(
                {
                    "name": f["name"],
                    "power_pct": round((f.get("used_power_kw") or 0) * 100 / max(f.get("total_power_kw") or 1, 1)),
                    "cooling_pct": round(
                        (f.get("used_cooling_tons") or 0) * 100 / max(f.get("total_cooling_tons") or 1, 1)
                    ),
                    "rack_pct": round((f.get("used_racks") or 0) * 100 / max(f.get("total_racks") or 1, 1)),
                }
            )
        ports = []
        for p in conn.execute(
            "SELECT device_label, total_ports, used_ports FROM nc_port_inventory ORDER BY device_label"
        ).fetchall():
            p = _row_to_dict(p)
            ports.append(
                {
                    "device": p["device_label"],
                    "pct": round((p.get("used_ports") or 0) * 100 / max(p.get("total_ports") or 1, 1)),
                }
            )
        conn.close()
        return jsonify({"facilities": facilities, "ports": ports})

    @bp.route("/api/charts/cost-breakdown", methods=["GET"])
    @nc_login_required
    def nc_api_chart_cost_breakdown():
        """CapEx vs OpEx breakdown across all projects."""
        conn = get_connection()
        # BOM CapEx
        bom_total = conn.execute("SELECT COALESCE(SUM(extended_cost), 0) FROM nc_bom_items").fetchone()[0]
        maint_total = conn.execute("SELECT COALESCE(SUM(annual_maint), 0) FROM nc_bom_items").fetchone()[0]
        license_total = conn.execute("SELECT COALESCE(SUM(license_cost), 0) FROM nc_bom_items").fetchone()[0]
        # Circuit OpEx
        circuit_monthly = conn.execute("SELECT COALESCE(SUM(monthly_cost_usd), 0) FROM nc_circuits").fetchone()[0]
        # Peering cost
        peering_monthly = conn.execute("SELECT COALESCE(SUM(monthly_cost), 0) FROM nc_peering_agreements").fetchone()[0]
        # Labor
        labor = conn.execute("SELECT COALESCE(SUM(hours * rate_per_hour), 0) FROM nc_resource_plan").fetchone()[0]
        conn.close()
        return jsonify(
            {
                "capex": {
                    "hardware": round(bom_total, 2),
                    "labor": round(labor, 2),
                },
                "opex_annual": {
                    "circuits": round(circuit_monthly * 12, 2),
                    "peering": round(peering_monthly * 12, 2),
                    "maintenance": round(maint_total, 2),
                    "licensing": round(license_total, 2),
                },
                "total_capex": round(bom_total + labor, 2),
                "total_opex_annual": round(
                    circuit_monthly * 12 + peering_monthly * 12 + maint_total + license_total, 2
                ),
            }
        )

    @bp.route("/charts")
    @nc_login_required
    def nc_charts_page():
        return render_template("network/charts.html")

    # ══════════════════════════════════════════════════════════════════════
    # P1: New Build Wizard + Alert Engine + Cross-Module Validation
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/wizard/new-build", methods=["POST"])
    @nc_login_required
    def nc_api_wizard_new_build():
        """Create a new network build: project + topology + phases + workflow in one call."""
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        now = _now()

        # Step 1: Create project
        pid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_projects "
            "(id, name, customer_id, description, status, owner, "
            f" created_at, updated_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                pid,
                data.get("name", "New Network Build"),
                data.get("customer_id"),
                data.get("description", ""),
                "draft",
                data.get("owner", ""),
                now,
                now,
            ),
        )

        # Step 2: Create topology (optional template)
        topo_id = str(_uuid.uuid4())
        graph = data.get("graph_json", {"nodes": [], "edges": []})
        if isinstance(graph, dict):
            graph = json.dumps(graph)
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, classification, "
            f" created_at, updated_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                topo_id,
                data.get("topology_name", data.get("name", "New Topology")),
                "",
                graph,
                data.get("classification", "public"),
                now,
                now,
            ),
        )
        conn.execute(f"INSERT INTO nc_project_topologies (project_id, topology_id) VALUES ({_ph},{_ph})", (pid, topo_id))

        # Step 3: Initialize phases
        for num, name in [(1, "Concept"), (2, "Design"), (3, "Approval"), (4, "Post-Deploy")]:
            conn.execute(
                "INSERT INTO nc_project_phases "
                "(id, project_id, phase_num, phase_name, status, "
                f" entered_at, created_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (
                    str(_uuid.uuid4()),
                    pid,
                    num,
                    name,
                    "active" if num == 1 else "pending",
                    now if num == 1 else None,
                    now,
                ),
            )

        # Step 4: Initialize case workflow
        wid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_case_workflows "
            "(id, project_id, current_state, lifecycle_json, "
            f" created_at, updated_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (wid, pid, "concept", json.dumps(_NDC_LIFECYCLE), now, now),
        )
        conn.execute(
            f"INSERT INTO nc_case_history (workflow_id, from_state, to_state, comment, changed_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph})",
            (wid, "", "concept", "Created via New Build Wizard", now),
        )

        conn.commit()
        conn.close()
        _audit("WIZARD_NEW_BUILD", "project", pid, data.get("name", ""))
        return jsonify(
            {
                "project_id": pid,
                "topology_id": topo_id,
                "workflow_id": wid,
                "phases": 4,
                "next_step": f"/network/canvas/{topo_id}",
            }
        ), 201

    @bp.route("/wizard")
    @nc_login_required
    def nc_wizard_page():
        return render_template("network/wizard.html")

    # ── Alert/Threshold Engine ────────────────────────────────────────────
    @bp.route("/api/alert-rules", methods=["GET"])
    @nc_login_required
    def nc_api_list_alert_rules():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_alert_rules ORDER BY metric, name").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/alert-rules", methods=["POST"])
    @nc_login_required
    def nc_api_create_alert_rule():
        data = request.get_json(force=True, silent=True) or {}
        return _crud_create(
            "nc_alert_rules", "", data, ["name", "metric", "operator", "threshold", "severity", "enabled"]
        )

    @bp.route("/api/alert-rules/<rid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_alert_rule(rid):
        return _crud_delete("nc_alert_rules", rid)

    @bp.route("/api/alert-events", methods=["GET"])
    @nc_login_required
    def nc_api_list_alert_events():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_alert_events ORDER BY created_at DESC LIMIT 50").fetchall()
        unack = conn.execute("SELECT COUNT(*) FROM nc_alert_events WHERE acknowledged=0").fetchone()[0]
        conn.close()
        return jsonify(
            {
                "events": [_row_to_dict(r) for r in rows],
                "unacknowledged": unack,
            }
        )

    @bp.route("/api/alert-events/run-check", methods=["POST"])
    @nc_login_required
    def nc_api_run_alert_check():
        """Evaluate all enabled alert rules against current data."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        rules = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_alert_rules WHERE enabled=1").fetchall()]
        now = _now()
        new_events = 0

        for rule in rules:
            metric = rule.get("metric", "")
            op = rule.get("operator", "gt")
            threshold = float(rule.get("threshold", 0))

            def _check(val, entity_type="", entity_id="", msg=""):
                nonlocal new_events
                triggered = False
                if op == "gt" and val > threshold:
                    triggered = True
                elif op == "lt" and val < threshold:
                    triggered = True
                elif op == "gte" and val >= threshold:
                    triggered = True
                elif op == "lte" and val <= threshold:
                    triggered = True
                if triggered:
                    conn.execute(
                        "INSERT INTO nc_alert_events "
                        "(id, rule_id, rule_name, severity, message, "
                        " entity_type, entity_id, created_at) "
                        f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                        (
                            str(_uuid.uuid4()),
                            rule["id"],
                            rule["name"],
                            rule.get("severity", "warning"),
                            msg,
                            entity_type,
                            entity_id,
                            now,
                        ),
                    )
                    new_events += 1

            if metric == "compliance_pct":
                for p in conn.execute("SELECT id, name FROM nc_projects").fetchall():
                    tids = [
                        r[0]
                        for r in conn.execute(
                            f"SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}", (p[0],)
                        ).fetchall()
                    ]
                    tp = tf = 0
                    for tid in tids:
                        row = conn.execute(
                            "SELECT passed, failed "
                            "FROM nc_compliance_checks "
                            f"WHERE topology_id={_ph} "
                            "ORDER BY ran_at DESC LIMIT 1",
                            (tid,),
                        ).fetchone()
                        if row:
                            tp += row[0] or 0
                            tf += row[1] or 0
                    total = tp + tf
                    if total:
                        pct = round(tp * 100 / total)
                        _check(pct, "project", p[0], f"{p[1]}: compliance {pct}%")

            elif metric == "port_utilization":
                for pi in conn.execute("SELECT * FROM nc_port_inventory").fetchall():
                    pi = _row_to_dict(pi)
                    total = pi.get("total_ports", 0) or 1
                    used = pi.get("used_ports", 0)
                    pct = round(used * 100 / total)
                    _check(pct, "device", pi.get("device_label", ""), f"{pi['device_label']}: port util {pct}%")

            elif metric == "power_pct":
                for f in conn.execute("SELECT * FROM nc_facilities").fetchall():
                    f = _row_to_dict(f)
                    total = f.get("total_power_kw", 0) or 1
                    used = f.get("used_power_kw", 0)
                    pct = round(used * 100 / max(total, 1))
                    _check(pct, "facility", f.get("name", ""), f"{f['name']}: power {pct}%")

        conn.commit()
        conn.close()
        return jsonify({"rules_checked": len(rules), "events_created": new_events})

    # ── Cross-Module Auto-Validation ──────────────────────────────────────
    @bp.route("/api/cross-validate/<pid>", methods=["POST"])
    @nc_login_required
    def nc_api_cross_validate(pid):
        """Run cross-module validation for a project.
        Checks peering↔capacity, compliance→findings, discovery→drift."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        findings = []

        # 1. Peering ↔ Capacity: check if peering ports fit device inventory
        peers = [
            _row_to_dict(r)
            for r in conn.execute(f"SELECT * FROM nc_peering_agreements WHERE project_id={_ph}", (pid,)).fetchall()
        ]
        ports = [
            _row_to_dict(r)
            for r in conn.execute(
                "SELECT * FROM nc_port_inventory WHERE topology_id IN "
                "(SELECT topology_id FROM nc_project_topologies "
                f" WHERE project_id={_ph})",
                (pid,),
            ).fetchall()
        ]
        total_avail = sum((p.get("total_ports", 0) or 0) - (p.get("used_ports", 0) or 0) for p in ports)
        if peers and total_avail < len(peers):
            findings.append(
                {
                    "module": "peering↔capacity",
                    "severity": "high",
                    "message": f"{len(peers)} peering agreements but only {total_avail} available ports",
                }
            )

        # 2. Compliance → auto-flag if CAT1 findings exist
        topo_ids = [
            r[0]
            for r in conn.execute(f"SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}", (pid,)).fetchall()
        ]
        cat1_count = 0
        for tid in topo_ids:
            c1 = conn.execute(
                f"SELECT COUNT(*) FROM nc_compliance_findings WHERE topology_id={_ph} AND status='open' AND severity='CAT1'",
                (tid,),
            ).fetchone()
            cat1_count += c1[0] if c1 else 0
        if cat1_count > 0:
            findings.append(
                {
                    "module": "compliance",
                    "severity": "critical",
                    "message": f"{cat1_count} open CAT1 findings — blocks deployment",
                }
            )

        # 3. Facilities → check rack/power for project
        facs = [_row_to_dict(r) for r in conn.execute("SELECT * FROM nc_facilities").fetchall()]
        for f in facs:
            power_pct = round((f.get("used_power_kw", 0) or 0) * 100 / max(f.get("total_power_kw", 1) or 1, 1))
            if power_pct > 80:
                findings.append(
                    {
                        "module": "facilities",
                        "severity": "warning",
                        "message": f"{f['name']}: power at {power_pct}% — augment before adding equipment",
                    }
                )

        conn.close()
        return jsonify(
            {
                "project_id": pid,
                "findings": findings,
                "total_findings": len(findings),
                "has_blockers": any(f["severity"] == "critical" for f in findings),
            }
        )

    # ── Favorites / Pinned Views ──────────────────────────────────────────
    @bp.route("/api/favorites", methods=["GET"])
    @nc_login_required
    def nc_api_list_favorites():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_favorites ORDER BY created_at DESC").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/favorites", methods=["POST"])
    @nc_login_required
    def nc_api_add_favorite():
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO nc_favorites "
                "(id, entity_type, entity_id, label, user_id, "
                f" created_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (
                    str(_uuid.uuid4()),
                    data.get("entity_type", ""),
                    data.get("entity_id", ""),
                    data.get("label", ""),
                    data.get("user_id", ""),
                    _now(),
                ),
            )
            conn.commit()
        except Exception:
            pass
        conn.close()
        return jsonify({"ok": True}), 201

    @bp.route("/api/favorites/<fid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_favorite(fid):
        return _crud_delete("nc_favorites", fid)

    # ══════════════════════════════════════════════════════════════════════
    # Peering Agreements
    # ══════════════════════════════════════════════════════════════════════
