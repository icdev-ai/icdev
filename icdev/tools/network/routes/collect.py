# CUI // SP-CTI
"""ICDEV Network Design Canvas -- collect route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_collect_routes(bp).
"""
from __future__ import annotations

import json
import uuid as _uuid
from flask import jsonify, render_template, request
from tools.network.routes._common import _classify_imported_nodes
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import (
    _audit,
    _now,
    _row_to_dict,
    invalidate_parsed_graph,
    nc_login_required,
)
from tools.network.compliance import run_compliance_audit
from tools.network.db.init_db import get_connection
from tools.network.export_import import import_drawio, import_svg, import_vdx


def register_collect_routes(bp):
    """Register collect routes on the NDC blueprint."""

    @bp.route("/api/connect-collect", methods=["POST"])
    @nc_login_required
    def nc_api_connect_collect():
        """Connect to a device via SSH, run profile commands (read-only),
        store outputs, and parse routing entries.
        Falls back to manual paste mode if SSH fails or is unavailable."""
        data = request.get_json(force=True, silent=True) or {}
        device_ip = data.get("device_ip", "")
        profile_id = data.get("profile_id", "")
        hostname = data.get("hostname", device_ip)
        topology_id = data.get("topology_id")
        mode = data.get("mode", "ssh")  # ssh or manual
        manual_outputs = data.get("manual_outputs", {})  # {cmd_name: output_text}

        if not device_ip:
            return jsonify({"error": "device_ip required"}), 400

        # Load profile commands
        conn = get_connection()
        _ph = sql_placeholder(conn)
        prof_row = conn.execute(f"SELECT commands_json FROM nc_device_profiles WHERE id={_ph}", (profile_id,)).fetchone()
        if not prof_row:
            conn.close()
            return jsonify({"error": "Profile not found"}), 404
        try:
            commands = json.loads(prof_row[0] or "{}")
        except Exception:
            commands = {}

        now = _now()
        results = []

        if mode == "manual":
            # Manual paste mode — store provided outputs
            for cmd_name, output in manual_outputs.items():
                if cmd_name not in commands:
                    continue
                # Dedup
                conn.execute(
                    f"DELETE FROM nc_collected_configs WHERE device_ip={_ph} AND command_name={_ph}", (device_ip, cmd_name)
                )
                conn.execute(
                    "INSERT INTO nc_collected_configs "
                    "(id, device_ip, hostname, profile_id, "
                    " command_name, output_text, parsed_json, "
                    " collected_at, topology_id) "
                    f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (str(_uuid.uuid4()), device_ip, hostname, profile_id, cmd_name, output, "{}", now, topology_id),
                )
                results.append(
                    {
                        "command": cmd_name,
                        "status": "stored",
                        "lines": len(output.splitlines()),
                    }
                )
        else:
            # SSH mode — attempt live connection
            ssh_ok = False
            try:
                from netmiko import ConnectHandler

                ssh_ok = True
            except ImportError:
                pass

            if ssh_ok:
                username = data.get("username", "")
                password = data.get("password", "")
                device_type = data.get("device_type", "cisco_ios")
                try:
                    net_connect = ConnectHandler(
                        device_type=device_type,
                        host=device_ip,
                        username=username,
                        password=password,
                        timeout=data.get("timeout", 30),
                        read_timeout_override=60,
                    )
                    for cmd_name, cmd_info in commands.items():
                        cli_cmd = cmd_info.get("command", "")
                        timeout = cmd_info.get("timeout_sec", 10)
                        try:
                            output = net_connect.send_command(cli_cmd, read_timeout=timeout)
                            # Dedup and store
                            conn.execute(
                                f"DELETE FROM nc_collected_configs WHERE device_ip={_ph} AND command_name={_ph}",
                                (device_ip, cmd_name),
                            )
                            conn.execute(
                                "INSERT INTO nc_collected_configs "
                                "(id, device_ip, hostname, profile_id, "
                                " command_name, output_text, parsed_json, "
                                " collected_at, topology_id) "
                                f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                                (
                                    str(_uuid.uuid4()),
                                    device_ip,
                                    hostname,
                                    profile_id,
                                    cmd_name,
                                    output,
                                    "{}",
                                    now,
                                    topology_id,
                                ),
                            )
                            results.append(
                                {
                                    "command": cmd_name,
                                    "status": "collected",
                                    "lines": len(output.splitlines()),
                                }
                            )
                        except Exception as cmd_err:
                            results.append(
                                {
                                    "command": cmd_name,
                                    "status": "failed",
                                    "error": str(cmd_err)[:100],
                                }
                            )
                    net_connect.disconnect()
                except Exception as ssh_err:
                    conn.close()
                    return jsonify(
                        {
                            "error": f"SSH connection failed: {str(ssh_err)[:200]}",
                            "hint": "Use mode='manual' to paste command outputs instead",
                        }
                    ), 502
            else:
                conn.close()
                return jsonify(
                    {
                        "error": "netmiko not available for SSH",
                        "hint": "Use mode='manual' to paste command outputs, or install netmiko: pip install netmiko",
                    }
                ), 501

        conn.commit()
        conn.close()
        _audit("CONNECT_COLLECT", "device", device_ip, f"profile={profile_id}, commands={len(results)}")
        return jsonify(
            {
                "device_ip": device_ip,
                "hostname": hostname,
                "mode": mode,
                "commands_executed": len(results),
                "results": results,
            }
        )

    @bp.route("/api/import/extract-data", methods=["POST"])
    @nc_login_required
    def nc_api_import_extract_data():
        """Import a diagram AND extract device data into DB tables.
        Populates: topology, IPAM blocks, circuits, device geo (from labels),
        and collected configs (from node properties)."""
        data = request.get_json(force=True, silent=True) or {}
        fmt = data.get("format", "drawio")
        content = data.get("content", "")
        name = data.get("name", "Extracted Import")
        project_id = data.get("project_id")
        if not content:
            return jsonify({"error": "content required"}), 400

        # Import and classify
        if fmt == "drawio":
            graph = import_drawio(content)
        elif fmt in ("vdx", "visio"):
            graph = import_vdx(content)
        elif fmt == "svg":
            graph = import_svg(content)
        else:
            return jsonify({"error": f"Unsupported: {fmt}"}), 400
        graph = _classify_imported_nodes(graph)

        conn = get_connection()
        _ph = sql_placeholder(conn)
        now = _now()
        topo_id = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, classification, "
            f" created_at, updated_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (topo_id, name, f"Data extraction ({fmt})", json.dumps(graph), "public", now, now),
        )

        if project_id:
            conn.execute(
                f"INSERT OR IGNORE INTO nc_project_topologies (project_id, topology_id) VALUES ({_ph},{_ph})",
                (project_id, topo_id),
            )

        # Extract data from nodes
        ipam_extracted = 0
        devices_extracted = 0
        circuits_extracted = 0

        for n in graph.get("nodes", []):
            cfg = n.get("config") or n.get("configData") or {}
            label = n.get("label", "")
            ntype = n.get("type", "")

            # Extract IP addresses -> IPAM blocks
            ip = cfg.get("ip", "")
            if not ip:
                # Try to extract IP from label (e.g., "10.0.0.1/24")
                import re as _re

                ip_match = _re.search(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?)", label)
                if ip_match:
                    ip = ip_match.group(1)
            if ip and "/" in ip:
                # Extract network from CIDR
                parts = ip.split("/")
                try:
                    octets = parts[0].split(".")
                    mask = int(parts[1])
                    # Simple network calculation
                    network = ".".join(octets[: mask // 8]) + ".0" * (4 - mask // 8) + "/" + parts[1]
                    conn.execute(
                        "INSERT OR IGNORE INTO nc_ipam_blocks "
                        "(id, topology_id, network, description, "
                        f" created_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph})",
                        (str(_uuid.uuid4()), topo_id, network, f"Extracted from {label}", now),
                    )
                    ipam_extracted += 1
                except (ValueError, IndexError):
                    pass

            # Store device as collected config (properties)
            if ntype not in ("text", "heading", "badge", "rect", "circle", "imported", ""):
                devices_extracted += 1

        # Extract circuits from edges
        for e in graph.get("edges", []):
            elabel = e.get("label", "")
            proto = e.get("protocol", "")
            if elabel or proto:
                conn.execute(
                    "INSERT INTO nc_circuits "
                    "(id, topology_id, circuit_id, carrier, "
                    " circuit_type, bandwidth, install_status, "
                    f" created_at, updated_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (
                        str(_uuid.uuid4()),
                        topo_id,
                        elabel or f"link-{e.get('id', '')[:8]}",
                        "",
                        proto or "ethernet",
                        "",
                        "installed",
                        now,
                        now,
                    ),
                )
                circuits_extracted += 1

        # Run compliance audit
        audit_result = run_compliance_audit(topo_id, graph, ["fisma_high"], "CUI")
        total_p = sum(s["passed"] for s in audit_result["scores"].values())
        total_f = sum(s["failed"] for s in audit_result["scores"].values())
        conn.execute(
            "INSERT INTO nc_compliance_checks "
            "(id, topology_id, check_type, passed, failed, "
            f" findings_json, ran_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (str(_uuid.uuid4()), topo_id, "fisma_high", total_p, total_f, json.dumps(audit_result["findings"]), now),
        )

        conn.commit()
        conn.close()
        _audit("IMPORT_EXTRACT", "topology", topo_id, fmt)

        return jsonify(
            {
                "id": topo_id,
                "name": name,
                "nodes": len(graph.get("nodes", [])),
                "edges": len(graph.get("edges", [])),
                "extracted": {
                    "devices": devices_extracted,
                    "ipam_blocks": ipam_extracted,
                    "circuits": circuits_extracted,
                },
                "compliance": {
                    "passed": total_p,
                    "failed": total_f,
                    "findings": len(audit_result["findings"]),
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

    @bp.route("/collect")
    @nc_login_required
    def nc_collect_page():
        return render_template("network/collect.html")

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
        _ph = sql_placeholder(conn)
        now = _now()
        # Dedup: remove existing entries for this device before inserting
        conn.execute(f"DELETE FROM nc_routing_entries WHERE device_ip={_ph}", (device_ip,))
        count = 0
        for e in entries:
            conn.execute(
                "INSERT INTO nc_routing_entries "
                "(id, device_ip, hostname, prefix, next_hop, "
                " protocol, metric, admin_distance, interface, "
                " vrf, address_family, collected_at, topology_id) "
                f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (
                    str(_uuid.uuid4()),
                    device_ip,
                    hostname,
                    e.get("prefix", ""),
                    e.get("next_hop", ""),
                    e.get("protocol", ""),
                    e.get("metric", 0),
                    e.get("admin_distance", 0),
                    e.get("interface", ""),
                    e.get("vrf", "default"),
                    e.get("address_family", "ipv4"),
                    now,
                    topo_id,
                ),
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
        _ph = sql_placeholder(conn)
        if device_ip:
            rows = conn.execute(
                f"SELECT * FROM nc_routing_entries WHERE device_ip={_ph} ORDER BY prefix", (device_ip,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM nc_routing_entries ORDER BY device_ip, prefix LIMIT 500").fetchall()
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
        _ph = sql_placeholder(conn)

        if device_ips:
            placeholders = ",".join(_ph for _ in device_ips)
            rows = conn.execute(
                f"SELECT * FROM nc_routing_entries "  # nosec B608
                f"WHERE device_ip IN ({placeholders}) "
                f"ORDER BY device_ip, prefix",
                device_ips,
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM nc_routing_entries ORDER BY device_ip, prefix").fetchall()

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
            if nh and nh != "0.0.0.0" and nh != "::" and nh != dip:  # nosec B104 — route filter, not socket bind
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
            nodes.append(
                {
                    "id": nid,
                    "label": info["hostname"],
                    "type": "router",
                    "x": x_pos % 800,
                    "y": (x_pos // 800) * 200,
                    "config": {
                        "ip": ip,
                        "protocol": ", ".join(sorted(info["protocols"] - {""})),
                    },
                }
            )
            x_pos += 200

        edges = []
        for (src, dst), info in links.items():
            src_id = ip_to_id.get(src)
            dst_id = ip_to_id.get(dst)
            if src_id and dst_id and src_id != dst_id:
                edges.append(
                    {
                        "id": str(_uuid.uuid4())[:8],
                        "source": src_id,
                        "target": dst_id,
                        "label": ", ".join(sorted(info["protocols"] - {""})),
                        "protocol": ", ".join(sorted(info["protocols"] - {""})),
                    }
                )

        graph = {"nodes": nodes, "edges": edges}
        graph = _classify_imported_nodes(graph)

        topo_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO topologies "
            "(id, name, description, graph_json, "
            " classification, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                topo_id,
                name,
                f"Generated from {len(devices)} device routing tables",
                json.dumps(graph),
                "public",
                now,
                now,
            ),
        )
        conn.commit()
        conn.close()
        _audit("ROUTING_TOPO", "topology", topo_id, f"{len(nodes)} devices, {len(edges)} links")
        return jsonify(
            {
                "id": topo_id,
                "name": name,
                "nodes": len(nodes),
                "edges": len(edges),
                "devices_discovered": len(devices),
            }
        ), 201

    @bp.route("/api/config-to-canvas/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_config_to_canvas(topo_id):
        """Sync collected config data into canvas device properties.
        Matches by hostname or IP address."""
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

        # Get all collected configs
        configs = conn.execute(
            "SELECT device_ip, hostname, command_name, parsed_json "
            "FROM nc_collected_configs "
            f"WHERE topology_id={_ph} OR topology_id IS NULL "
            "ORDER BY collected_at DESC",
            (topo_id,),
        ).fetchall()

        # Build lookup by hostname and IP
        config_by_host = {}  # hostname -> {cmd: parsed}
        config_by_ip = {}  # ip -> {cmd: parsed}
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
                elif cmd_name in ("routing_table_v4", "routing_table_v6") and parsed:
                    if parsed.get("protocol"):
                        cfg["protocol"] = parsed["protocol"]

            n["config"] = cfg
            if "configData" in n:
                n["configData"] = cfg
            updated += 1

        now = _now()
        conn.execute(f"UPDATE topologies SET graph_json={_ph}, updated_at={_ph} WHERE id={_ph}", (json.dumps(graph), now, topo_id))
        conn.commit()
        conn.close()
        invalidate_parsed_graph(topo_id)  # ndc-perf-02
        return jsonify(
            {
                "updated_devices": updated,
                "total_nodes": len(graph.get("nodes", [])),
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # API: Circuits CRUD
    # ══════════════════════════════════════════════════════════════════════
