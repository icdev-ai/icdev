# CUI // SP-CTI
"""ICDEV Network Design Canvas -- crud route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_crud_routes(bp).
"""
from __future__ import annotations

import json
import uuid as _uuid
from flask import jsonify, request
from tools.canvas.ai_trace_mixin import record_canvas_decision
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import (
    _audit,
    _now,
    _row_to_dict,
    invalidate_parsed_graph,
    nc_login_required,
)
from tools.network.compliance import apply_compliance_fix, export_fips_report_html, generate_fips_coverage_report, generate_xacta_export, run_compliance_audit
from tools.network.constants import BOM_COSTS
from tools.network.db.init_db import get_connection
from tools.network.stig_import import import_stig_file


def register_crud_routes(bp):
    """Register crud routes on the NDC blueprint."""

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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_circuits (id, topology_id, circuit_id, carrier, circuit_type, bandwidth, "
            "handoff_a, handoff_z, customer, site, monthly_cost_usd, contract_start, contract_end, "
            "sla_uptime_pct, install_status, notes, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                cid,
                data.get("topology_id"),
                data.get("circuit_id", ""),
                data.get("carrier", ""),
                data.get("circuit_type", ""),
                data.get("bandwidth", ""),
                data.get("handoff_a", ""),
                data.get("handoff_z", ""),
                data.get("customer", ""),
                data.get("site", ""),
                data.get("monthly_cost_usd", 0),
                data.get("contract_start"),
                data.get("contract_end"),
                data.get("sla_uptime_pct", 99.9),
                data.get("install_status", "planned"),
                data.get("notes", ""),
                now,
                now,
            ),
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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT id FROM nc_circuits WHERE id={_ph}", (cid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        allowed = [
            "circuit_id",
            "carrier",
            "circuit_type",
            "bandwidth",
            "handoff_a",
            "handoff_z",
            "customer",
            "site",
            "monthly_cost_usd",
            "contract_start",
            "contract_end",
            "sla_uptime_pct",
            "install_status",
            "notes",
            "topology_id",
        ]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append(f"updated_at={_ph}")
        values.append(_now())
        values.append(cid)
        conn.execute(f"UPDATE nc_circuits SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        _audit("UPDATE", "circuit", cid)
        return jsonify({"ok": True})

    @bp.route("/api/circuits/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_circuit(cid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_circuits WHERE id={_ph}", (cid,))
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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_customers (id, name, customer_type, contact_name, contact_email, contract_ref, notes, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                cid,
                data.get("name", ""),
                data.get("customer_type", "customer"),
                data.get("contact_name", ""),
                data.get("contact_email", ""),
                data.get("contract_ref", ""),
                data.get("notes", ""),
                now,
            ),
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
        _ph = sql_placeholder(conn)
        allowed = ["name", "customer_type", "contact_name", "contact_email", "contract_ref", "notes"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(cid)
        conn.execute(f"UPDATE nc_customers SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        _audit("UPDATE", "customer", cid)
        return jsonify({"ok": True})

    @bp.route("/api/customers/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_customer(cid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_sites WHERE customer_id={_ph}", (cid,))
        conn.execute(f"DELETE FROM nc_customers WHERE id={_ph}", (cid,))
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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_sites (id, customer_id, name, address, city, state, country, site_type, classification, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                sid,
                data.get("customer_id"),
                data.get("name", ""),
                data.get("address", ""),
                data.get("city", ""),
                data.get("state", ""),
                data.get("country", "US"),
                data.get("site_type", "office"),
                data.get("classification", "public"),
                now,
            ),
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
        _ph = sql_placeholder(conn)
        allowed = ["customer_id", "name", "address", "city", "state", "country", "site_type", "classification"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(sid)
        conn.execute(f"UPDATE nc_sites SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        _audit("UPDATE", "site", sid)
        return jsonify({"ok": True})

    @bp.route("/api/sites/<sid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_site(sid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_sites WHERE id={_ph}", (sid,))
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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_ipam_blocks (id, topology_id, network, vlan_id, vrf, description, site_id, gateway, utilization_pct, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                bid,
                data.get("topology_id"),
                data.get("network", ""),
                data.get("vlan_id"),
                data.get("vrf", "global"),
                data.get("description", ""),
                data.get("site_id"),
                data.get("gateway", ""),
                data.get("utilization_pct", 0),
                now,
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "ipam_block", bid, data.get("network", ""))
        try:
            from tools.canvas.event_bus import publish as _eb_publish
            _eb_publish("ndc", "ndc.ipam.added", {
                "block_id": bid,
                "network": data.get("network", ""),
                "topology_id": data.get("topology_id"),
                "vrf": data.get("vrf", "global"),
                "vlan_id": data.get("vlan_id"),
                "classification": "CUI",
            }, target_canvas="idc")
        except Exception:
            pass
        return jsonify({"id": bid}), 201

    @bp.route("/api/ipam/<bid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_ipam(bid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        allowed = ["network", "vlan_id", "vrf", "description", "site_id", "gateway", "utilization_pct", "topology_id"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(bid)
        conn.execute(f"UPDATE nc_ipam_blocks SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        _audit("UPDATE", "ipam_block", bid)
        return jsonify({"ok": True})

    @bp.route("/api/ipam/<bid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_ipam(bid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_ipam_blocks WHERE id={_ph}", (bid,))
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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_cables (id, topology_id, cable_id, cable_type, src_device, src_port, "
            "dst_device, dst_port, patch_panel, length_m, status, notes, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                cid,
                data.get("topology_id"),
                data.get("cable_id", ""),
                data.get("cable_type", ""),
                data.get("src_device", ""),
                data.get("src_port", ""),
                data.get("dst_device", ""),
                data.get("dst_port", ""),
                data.get("patch_panel", ""),
                data.get("length_m"),
                data.get("status", "active"),
                data.get("notes", ""),
                now,
            ),
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
        _ph = sql_placeholder(conn)
        allowed = [
            "cable_id",
            "cable_type",
            "src_device",
            "src_port",
            "dst_device",
            "dst_port",
            "patch_panel",
            "length_m",
            "status",
            "notes",
            "topology_id",
        ]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        values.append(cid)
        conn.execute(f"UPDATE nc_cables SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        _audit("UPDATE", "cable", cid)
        return jsonify({"ok": True})

    @bp.route("/api/cables/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_cable(cid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_cables WHERE id={_ph}", (cid,))
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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json, name FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
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
        writer.writerow(
            [
                "Link ID",
                "Source Device",
                "Target Device",
                "Cable Type",
                "Distance (m)",
                "Conduit ID",
                "Fiber Strands",
                "Pull Tension (lbs)",
                "Notes",
            ]
        )

        edges = (gj or {}).get("edges", [])
        cable_count = 0
        for e in edges:
            cable = e.get("cableData")
            if not cable:
                continue
            cable_count += 1
            writer.writerow(
                [
                    e.get("id", ""),
                    nodes_map.get(e.get("source", ""), e.get("source", "")),
                    nodes_map.get(e.get("target", ""), e.get("target", "")),
                    cable.get("cable_type", ""),
                    cable.get("distance_m", ""),
                    cable.get("conduit_id", ""),
                    cable.get("fiber_strands", ""),
                    cable.get("pull_tension_lbs", ""),
                    cable.get("notes", ""),
                ]
            )

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
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_cross_connects (id, topology_id, xconn_id, facility, meet_me_room, "
            "src_device, src_port, dst_device, dst_port, media_type, bandwidth, "
            "provider_a, provider_z, loa_status, monthly_cost_usd, install_date, "
            "status, notes, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                cid,
                data.get("topology_id"),
                data.get("xconn_id", ""),
                data.get("facility", ""),
                data.get("meet_me_room", ""),
                data.get("src_device", ""),
                data.get("src_port", ""),
                data.get("dst_device", ""),
                data.get("dst_port", ""),
                data.get("media_type", "SMF"),
                data.get("bandwidth", ""),
                data.get("provider_a", ""),
                data.get("provider_z", ""),
                data.get("loa_status", "pending"),
                data.get("monthly_cost_usd", 0),
                data.get("install_date"),
                data.get("status", "planned"),
                data.get("notes", ""),
                now,
                now,
            ),
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
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT id FROM nc_cross_connects WHERE id={_ph}", (cid,)).fetchone()
        if not row:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        allowed = [
            "xconn_id",
            "facility",
            "meet_me_room",
            "src_device",
            "src_port",
            "dst_device",
            "dst_port",
            "media_type",
            "bandwidth",
            "provider_a",
            "provider_z",
            "loa_status",
            "monthly_cost_usd",
            "install_date",
            "status",
            "notes",
            "topology_id",
        ]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if not fields:
            conn.close()
            return jsonify({"error": "No fields"}), 400
        fields.append(f"updated_at={_ph}")
        values.append(_now())
        values.append(cid)
        conn.execute(f"UPDATE nc_cross_connects SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
        conn.close()
        _audit("UPDATE", "cross_connect", cid)
        return jsonify({"ok": True})

    @bp.route("/api/cross-connects/<cid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_cross_connect(cid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_cross_connects WHERE id={_ph}", (cid,))
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
        _ph = sql_placeholder(conn)
        rows = conn.execute(
            "SELECT id, topology_id, version_num, label, phase, created_by, notes, created_at "
            f"FROM nc_versions WHERE topology_id={_ph} ORDER BY version_num",
            (topo_id,),
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/versions/<topo_id>", methods=["POST"])
    @nc_login_required
    def nc_api_create_version(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        data = request.get_json(force=True, silent=True) or {}
        last = conn.execute(f"SELECT MAX(version_num) FROM nc_versions WHERE topology_id={_ph}", (topo_id,)).fetchone()[0]
        ver_num = (last or 0) + 1
        vid = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_versions (id, topology_id, version_num, label, phase, graph_json, created_by, notes, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                vid,
                topo_id,
                ver_num,
                data.get("label", f"v{ver_num}"),
                data.get("phase", "as-is"),
                topo["graph_json"],
                data.get("created_by", ""),
                data.get("notes", ""),
                now,
            ),
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
        _ph = sql_placeholder(conn)
        r1 = conn.execute(f"SELECT graph_json, label, phase FROM nc_versions WHERE id={_ph}", (v1_id,)).fetchone()
        r2 = conn.execute(f"SELECT graph_json, label, phase FROM nc_versions WHERE id={_ph}", (v2_id,)).fetchone()
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
        return jsonify(
            {
                "version_a": {"id": v1_id, "label": r1["label"], "phase": r1["phase"]},
                "version_b": {"id": v2_id, "label": r2["label"], "phase": r2["phase"]},
                "nodes_added": len(n2_ids - n1_ids),
                "nodes_removed": len(n1_ids - n2_ids),
                "nodes_unchanged": len(n1_ids & n2_ids),
                "edges_added": len(e2_ids - e1_ids),
                "edges_removed": len(e1_ids - e2_ids),
                "edges_unchanged": len(e1_ids & e2_ids),
                "added_node_ids": list(n2_ids - n1_ids),
                "removed_node_ids": list(n1_ids - n2_ids),
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # API: Bill of Materials & Capacity Planning
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/bom", methods=["GET"])
    @nc_login_required
    def nc_api_bom(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json, name FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
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
            bom_items.append(
                {
                    "device_type": device_type,
                    "quantity": count,
                    "unit_cost_usd": unit_cost,
                    "line_total_usd": line_total,
                }
            )
        return jsonify(
            {
                "topology": row["name"],
                "items": bom_items,
                "total_capex_usd": total,
                "device_count": sum(type_counts.values()),
                "unique_types": len(type_counts),
            }
        )

    @bp.route("/api/topologies/<topo_id>/capacity", methods=["POST"])
    @nc_login_required
    def nc_api_capacity_plan(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        row = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
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
            analysis.append(
                {
                    "edge_id": e.get("id", ""),
                    "label": e.get("label", ""),
                    "source": e.get("source", ""),
                    "target": e.get("target", ""),
                    "current_util_pct": base_util,
                    "growth_projected_pct": round(base_util * (1 + growth_pct / 100), 1),
                    "with_users_pct": projected,
                    "link_bw_mbps": link_bw_mbps,
                    "status": status,
                    "upgrade_needed": projected > 75,
                }
            )
        critical = [a for a in analysis if a["status"] == "critical"]
        return jsonify(
            {
                "links": analysis,
                "total_links": len(analysis),
                "critical_count": len(critical),
                "growth_pct": growth_pct,
                "additional_users": additional_users,
                "recommendations": [
                    f"Upgrade {a['label'] or a['edge_id']}: {a['current_util_pct']}% -> {a['with_users_pct']}%"
                    for a in critical
                ],
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # API: Compliance (quick check)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/topologies/<topo_id>/compliance", methods=["POST"])
    @nc_login_required
    def nc_api_compliance_check(topo_id):
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
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        node_types = [n.get("type", "") for n in nodes]
        findings = []
        if not any(t == "firewall" for t in node_types):
            findings.append(
                {"check": "STIG-NET-001", "severity": "CAT1", "type": "stig", "finding": "No firewall in topology"}
            )
        encrypted = sum(
            1
            for e in edges
            if "ipsec" in (e.get("protocol") or "").lower() or "tls" in (e.get("protocol") or "").lower()
        )
        if encrypted == 0 and edges:
            findings.append(
                {"check": "FIPS-001", "severity": "HIGH", "type": "fips", "finding": "No encrypted links detected"}
            )
        if not any(t in ("firewall", "aws-nfw", "az-fw", "gcp-armor") for t in node_types):
            findings.append(
                {"check": "ZTA-001", "severity": "HIGH", "type": "zta", "finding": "No network segmentation"}
            )
        from collections import Counter

        degree = Counter()
        for e in edges:
            degree[e.get("source")] += 1
            degree[e.get("target")] += 1
        spof = [
            n["id"]
            for n in nodes
            if degree.get(n["id"], 0) <= 1 and n.get("type") in ("router", "switch-l3", "firewall")
        ]
        if spof:
            findings.append(
                {
                    "check": "BP-REDUNDANCY",
                    "severity": "MEDIUM",
                    "type": "best_practice",
                    "finding": f"{len(spof)} critical device(s) with single connection",
                }
            )
        passed = max(0, 10 - len(findings))
        failed = len(findings)
        check_id = str(_uuid.uuid4())
        now = _now()
        conn.execute(
            "INSERT INTO nc_compliance_checks (id, topology_id, check_type, passed, failed, findings_json, ran_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (check_id, topo_id, "full", passed, failed, json.dumps(findings), now),
        )
        conn.commit()
        conn.close()
        result = {
            "check_id": check_id,
            "passed": passed,
            "failed": failed,
            "score_pct": round(passed / max(passed + failed, 1) * 100, 1),
            "findings": findings,
        }
        # Blockchain provenance for assessment
        try:
            from tools.canvas.provenance import register_canvas_provenance
            register_canvas_provenance(
                canvas_key="ndc",
                design_id=topo_id,
                assessment_data=result,
                project_id="",
            )
        except Exception:
            pass
        return jsonify(result)

    # ══════════════════════════════════════════════════════════════════════
    # API: Full Compliance Audit Engine
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/compliance/<topo_id>/profile", methods=["PUT"])
    @nc_login_required
    def nc_api_update_compliance_profile(topo_id):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        profile = conn.execute(f"SELECT id FROM nc_compliance_profiles WHERE topology_id={_ph}", (topo_id,)).fetchone()
        if not profile:
            pid = str(_uuid.uuid4())
            conn.execute(f"INSERT INTO nc_compliance_profiles (id, topology_id) VALUES ({_ph},{_ph})", (pid, topo_id))
            conn.commit()
            profile = conn.execute(f"SELECT id FROM nc_compliance_profiles WHERE id={_ph}", (pid,)).fetchone()
        fields, values = [], []
        for k in ["regimes", "classification", "environment", "auto_audit"]:
            if k in data:
                val = json.dumps(data[k]) if k == "regimes" and isinstance(data[k], list) else data[k]
                fields.append(f"{k}={_ph}")
                values.append(val)
        if fields:
            fields.append(f"updated_at={_ph}")
            values.append(_now())
            values.append(profile["id"])
            conn.execute(f"UPDATE nc_compliance_profiles SET {', '.join(fields)} WHERE id={_ph}", values)  # nosec B608 -- table/column names are internal constants, not user input
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/compliance/<topo_id>/audit", methods=["POST"])
    @nc_login_required
    def nc_api_run_compliance_audit(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404
        profile = conn.execute(f"SELECT * FROM nc_compliance_profiles WHERE topology_id={_ph}", (topo_id,)).fetchone()
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
        record_canvas_decision(
            canvas_type="ndc",
            record_id=topo_id,
            decision_type="compliance_finding",
            decision=f"{len(result.get('findings', []))} finding(s) across regimes: {', '.join(regimes)}",
            rationale=f"Scores: {result.get('scores', {})}",
            model_used=None,
            confidence=None,
            project_id=None,
        )

        audit_id = str(_uuid.uuid4())
        now = _now()
        total_passed = sum(s["passed"] for s in result["scores"].values())
        total_failed = sum(s["failed"] for s in result["scores"].values())
        conn.execute(
            "INSERT INTO nc_compliance_checks (id, topology_id, check_type, passed, failed, findings_json, ran_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (audit_id, topo_id, ",".join(regimes), total_passed, total_failed, json.dumps(result["findings"]), now),
        )
        existing_rule_ids = set()
        for r in conn.execute(
            f"SELECT rule_id FROM nc_compliance_findings WHERE topology_id={_ph} AND status='open'", (topo_id,)
        ).fetchall():
            existing_rule_ids.add(r["rule_id"])
        new_rule_ids = set()
        for f in result["findings"]:
            new_rule_ids.add(f["rule_id"])
            exists = conn.execute(
                f"SELECT id FROM nc_compliance_findings WHERE topology_id={_ph} AND rule_id={_ph} AND status='open'",
                (topo_id, f["rule_id"]),
            ).fetchone()
            if not exists:
                fid = str(_uuid.uuid4())
                conn.execute(
                    "INSERT INTO nc_compliance_findings (id, topology_id, audit_id, rule_id, regime, severity, "
                    "title, description, affected_entity, affected_type, fix_action, created_at) "
                    f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (
                        fid,
                        topo_id,
                        audit_id,
                        f["rule_id"],
                        ",".join(f["regimes"]),
                        f["severity"],
                        f["title"],
                        f["description"],
                        f.get("affected_entity", ""),
                        f.get("affected_type", "topology"),
                        json.dumps(f.get("fix_action")) if f.get("fix_action") else None,
                        now,
                    ),
                )
        remediated_rules = existing_rule_ids - new_rule_ids
        for rid in remediated_rules:
            conn.execute(
                f"UPDATE nc_compliance_findings SET status='remediated', remediated_at={_ph} "
                f"WHERE topology_id={_ph} AND rule_id={_ph} AND status='open'",
                (now, topo_id, rid),
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
        _ph = sql_placeholder(conn)
        finding = conn.execute(f"SELECT * FROM nc_compliance_findings WHERE id={_ph}", (finding_id,)).fetchone()
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
        topo = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
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
            last = conn.execute(f"SELECT MAX(version_num) FROM nc_versions WHERE topology_id={_ph}", (topo_id,)).fetchone()[
                0
            ]
            ver_num = (last or 0) + 1
            vid = str(_uuid.uuid4())
            conn.execute(
                "INSERT INTO nc_versions (id, topology_id, version_num, label, phase, graph_json, created_at) "
                f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                (
                    vid,
                    topo_id,
                    ver_num,
                    fix_action.get("label", "As-Built"),
                    fix_action.get("phase", "as-is"),
                    topo["graph_json"],
                    _now(),
                ),
            )
            applied = True
            detail = f"Created version v{ver_num}"
        if applied:
            if action != "create_version":
                conn.execute(
                    f"UPDATE topologies SET graph_json={_ph}, updated_at={_ph} WHERE id={_ph}", (json.dumps(graph), _now(), topo_id)
                )
            conn.execute(
                f"UPDATE nc_compliance_findings SET status='remediated', remediated_at={_ph} WHERE id={_ph}",
                (_now(), finding_id),
            )
            conn.commit()
            conn.close()
            invalidate_parsed_graph(topo_id)  # ndc-perf-02
            _audit("COMPLIANCE_FIX", "topology", topo_id, detail)
            return jsonify({"applied": True, "detail": detail})
        conn.close()
        return jsonify({"applied": False, "detail": "Fix action not applicable"})

    @bp.route("/api/compliance/<topo_id>/export", methods=["POST"])
    @nc_login_required
    def nc_api_compliance_export(topo_id):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT name FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        profile = conn.execute(f"SELECT * FROM nc_compliance_profiles WHERE topology_id={_ph}", (topo_id,)).fetchone()
        findings = [
            _row_to_dict(r)
            for r in conn.execute(
                f"SELECT * FROM nc_compliance_findings WHERE topology_id={_ph} ORDER BY severity, rule_id", (topo_id,)
            ).fetchall()
        ]
        conn.close()
        profile = _row_to_dict(profile) if profile else {}
        xml = generate_xacta_export(
            topo["name"],
            profile.get("classification", "CUI"),
            profile.get("environment", "IL4"),
            profile.get("regimes", "[]"),
            findings,
        )
        return jsonify(
            {
                "format": "xacta_xml",
                "filename": f"{topo['name']}_compliance_report.xml",
                "content": xml,
                "findings_count": len(findings),
            }
        )

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
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT name, graph_json FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
        if not topo:
            conn.close()
            return jsonify({"error": "Topology not found"}), 404

        try:
            graph = json.loads(topo["graph_json"])
        except Exception:
            conn.close()
            return jsonify({"error": "Bad graph data"}), 500

        profile = conn.execute(
            f"SELECT * FROM nc_compliance_profiles WHERE topology_id={_ph}",
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

        _audit(
            "FIPS_REPORT",
            "topology",
            topo_id,
            f"coverage={report['summary']['coverage_pct']}% risk={report['summary']['risk_level']} format={fmt}",
        )

        if fmt == "html":
            html = export_fips_report_html(report)
            from flask import Response

            return Response(
                html,
                mimetype="text/html",
                headers={
                    "Content-Disposition": f'attachment; filename="{topo["name"]}_fips_report.html"',
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
        _ph = sql_placeholder(conn)
        topo = conn.execute(f"SELECT graph_json, name FROM topologies WHERE id={_ph}", (topo_id,)).fetchone()
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
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                import_id,
                topo_id,
                filename,
                result.get("format", ""),
                result.get("stig_name", ""),
                result.get("stig_version", ""),
                result.get("total_hosts", 0),
                result.get("total_matched", 0),
                json.dumps(result),
                now,
            ),
        )

        # Also create compliance findings for failed STIG checks
        audit_id = str(_uuid.uuid4())
        total_pass = result.get("total_pass", 0)
        total_fail = result.get("total_fail", 0)
        conn.execute(
            "INSERT INTO nc_compliance_checks "
            "(id, topology_id, check_type, passed, failed, findings_json, ran_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                audit_id,
                topo_id,
                f"stig_import:{filename}",
                total_pass,
                total_fail,
                json.dumps(result.get("matched", [])),
                now,
            ),
        )

        # Insert individual findings for each failed check on matched devices
        for device in result.get("matched", []):
            for f in device.get("findings", []):
                fid = str(_uuid.uuid4())
                rule_id = f.get("rule_id") or f.get("vuln_id", "UNKNOWN")
                exists = conn.execute(
                    "SELECT id FROM nc_compliance_findings "
                    f"WHERE topology_id={_ph} AND rule_id={_ph} AND affected_entity={_ph} AND status='open'",
                    (topo_id, rule_id, device["label"]),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO nc_compliance_findings "
                        "(id, topology_id, audit_id, rule_id, regime, severity, "
                        "title, description, affected_entity, affected_type, created_at) "
                        f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                        (
                            fid,
                            topo_id,
                            audit_id,
                            rule_id,
                            "stig",
                            f.get("severity", "CAT2"),
                            f.get("title", rule_id),
                            f.get("finding_details", ""),
                            device["label"],
                            "node",
                            now,
                        ),
                    )

        conn.commit()
        conn.close()
        _audit(
            "STIG_IMPORT",
            "topology",
            topo_id,
            f"{filename}: {result.get('total_matched', 0)}/{result.get('total_hosts', 0)} hosts matched",
        )

        result["import_id"] = import_id
        result["audit_id"] = audit_id
        return jsonify(result)

    @bp.route("/api/compliance/<topo_id>/stig-imports", methods=["GET"])
    @nc_login_required
    def nc_api_stig_import_history(topo_id):
        """List previous STIG import records for a topology."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        rows = conn.execute(
            "SELECT id, filename, format, stig_name, stig_version, "
            "total_hosts, matched_hosts, imported_at "
            f"FROM nc_stig_imports WHERE topology_id={_ph} ORDER BY imported_at DESC LIMIT 20",
            (topo_id,),
        ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    # ── Project routes (extracted to routes/projects.py) ──────────────────


    # ── Governance routes (extracted to routes/governance.py) ─────────────


    # ── Stencil Library routes (Cisco / Juniper / AWS / Azure / Custom) ───


    # ══════════════════════════════════════════════════════════════════════
    # Extended: Notifications, Topology Diff, Auto-Decompose, Global Canvas
    # ══════════════════════════════════════════════════════════════════════

    # ── Notifications ─────────────────────────────────────────────────────
