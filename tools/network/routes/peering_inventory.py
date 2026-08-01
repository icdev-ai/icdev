# CUI // SP-CTI
"""ICDEV Network Design Canvas -- peering_inventory route group.

Extracted verbatim from tools/network/blueprint.py (cvx-net-01 monolith split).
Registered on the shared NDC blueprint via register_peering_inventory_routes(bp).
"""
from __future__ import annotations

import json
import uuid as _uuid
from datetime import datetime, timezone
from flask import jsonify, render_template, request
from tools.db.storage import sql_placeholder
from tools.network.blueprint_helpers import _audit, _crud_create, _crud_delete, _crud_list, _now, _row_to_dict, nc_login_required
from tools.network.db.init_db import get_connection


def register_peering_inventory_routes(bp):
    """Register peering_inventory routes on the NDC blueprint."""

    _PEERING_LIFECYCLE = [
        "evaluation",
        "negotiation",
        "agreement_signed",
        "technical_design",
        "implemented",
        "operational",
        "decommissioned",
    ]

    @bp.route("/api/peering-agreements", methods=["GET"])
    @nc_login_required
    def nc_api_list_peering():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_peering_agreements ORDER BY status, peer_name").fetchall()
        conn.close()
        items = [_row_to_dict(r) for r in rows]
        for it in items:
            try:
                it["locations"] = json.loads(it.get("locations") or "[]")
            except Exception:
                it["locations"] = []
        return jsonify(items)

    @bp.route("/api/peering-agreements", methods=["POST"])
    @nc_login_required
    def nc_api_create_peering():
        data = request.get_json(force=True, silent=True) or {}
        pid = str(_uuid.uuid4())
        locs = data.get("locations", [])
        if isinstance(locs, list):
            locs = json.dumps(locs)
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_peering_agreements "
            "(id, peer_name, peer_asn, our_asn, peering_type, "
            " routing_method, status, purpose, purpose_category, "
            " business_justification, locations, port_speed, "
            " contract_start, contract_end, monthly_cost, "
            " traffic_commit, ratio_limit, sla_uptime_pct, "
            " noc_contact, noc_email, noc_phone, legal_entity, "
            " notes, project_id, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                pid,
                data.get("peer_name", ""),
                data.get("peer_asn"),
                data.get("our_asn"),
                data.get("peering_type", "settlement_free"),
                data.get("routing_method", "bgp"),
                data.get("status", "evaluation"),
                data.get("purpose", ""),
                data.get("purpose_category", "connectivity"),
                data.get("business_justification", ""),
                locs,
                data.get("port_speed"),
                data.get("contract_start"),
                data.get("contract_end"),
                data.get("monthly_cost", 0),
                data.get("traffic_commit"),
                data.get("ratio_limit"),
                data.get("sla_uptime_pct", 99.9),
                data.get("noc_contact"),
                data.get("noc_email"),
                data.get("noc_phone"),
                data.get("legal_entity"),
                data.get("notes"),
                data.get("project_id"),
                _now(),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        _audit("CREATE", "peering_agreement", pid, data.get("peer_name", ""))
        return jsonify({"id": pid}), 201

    @bp.route("/api/peering-agreements/<aid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_peering(aid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        allowed = [
            "peer_name",
            "peer_asn",
            "our_asn",
            "peering_type",
            "routing_method",
            "status",
            "purpose",
            "purpose_category",
            "business_justification",
            "port_speed",
            "contract_start",
            "contract_end",
            "monthly_cost",
            "traffic_commit",
            "ratio_limit",
            "sla_uptime_pct",
            "noc_contact",
            "noc_email",
            "noc_phone",
            "legal_entity",
            "notes",
            "project_id",
        ]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if "locations" in data:
            locs = data["locations"]
            if isinstance(locs, list):
                locs = json.dumps(locs)
            fields.append(f"locations={_ph}")
            values.append(locs)
        if fields:
            fields.append(f"updated_at={_ph}")
            values.append(_now())
            values.append(aid)
            conn.execute(
                f"UPDATE nc_peering_agreements "  # nosec B608
                f"SET {', '.join(fields)} WHERE id={_ph}",
                values,
            )
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/peering-agreements/<aid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_peering(aid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_peering_sessions WHERE agreement_id={_ph}", (aid,))
        conn.execute(f"DELETE FROM nc_peering_agreements WHERE id={_ph}", (aid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # Peering sessions
    @bp.route("/api/peering-sessions/<aid>", methods=["GET"])
    @nc_login_required
    def nc_api_list_peering_sessions(aid):
        return _crud_list("nc_peering_sessions", aid, "location")

    @bp.route("/api/peering-sessions/<aid>", methods=["POST"])
    @nc_login_required
    def nc_api_create_peering_session(aid):
        data = request.get_json(force=True, silent=True) or {}
        sr = data.get("static_routes", [])
        if isinstance(sr, list):
            data["static_routes"] = json.dumps(sr)
        comms = data.get("communities", [])
        if isinstance(comms, list):
            data["communities"] = json.dumps(comms)
        data["md5_enabled"] = str(1 if data.get("md5_enabled") else 0)
        return _crud_create(
            "nc_peering_sessions",
            aid,
            data,
            [
                "location",
                "routing_method",
                "our_ip",
                "peer_ip",
                "our_ipv6",
                "peer_ipv6",
                "our_asn",
                "peer_asn",
                "prefix_limit",
                "md5_enabled",
                "local_pref",
                "med",
                "communities",
                "static_routes",
                "status",
                "port_speed",
                "notes",
            ],
        )

    # Peering evaluations
    @bp.route("/api/peering-evaluations", methods=["GET"])
    @nc_login_required
    def nc_api_list_peering_evals():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_peering_evaluations ORDER BY score DESC").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/peering-evaluations", methods=["POST"])
    @nc_login_required
    def nc_api_create_peering_eval():
        data = request.get_json(force=True, silent=True) or {}
        # Auto-score
        geo = {"low": 1, "medium": 2, "high": 3}
        noc = {"poor": 1, "fair": 2, "good": 3, "excellent": 4, "unknown": 0}
        traffic = min(data.get("traffic_volume", 0) / 1000, 5)  # 0-5 pts
        geo_pts = geo.get(data.get("geographic_overlap", "medium"), 2)
        noc_pts = noc.get(data.get("noc_quality", "unknown"), 0)
        prefix_pts = min(data.get("prefix_count", 0) / 100, 3)
        score = round(traffic + geo_pts + noc_pts + prefix_pts, 2)
        rec = "peer" if score >= 7 else "evaluate" if score >= 4 else "decline"
        data["score"] = str(score)
        data["recommendation"] = rec
        data["prefix_count"] = str(data.get("prefix_count", 0))
        data["traffic_volume"] = str(data.get("traffic_volume", 0))
        return _crud_create(
            "nc_peering_evaluations",
            "",
            data,
            [
                "peer_name",
                "peer_asn",
                "traffic_volume",
                "geographic_overlap",
                "noc_quality",
                "network_capacity",
                "prefix_count",
                "peering_policy",
                "score",
                "recommendation",
                "notes",
            ],
        )

    # Peering cost-benefit
    @bp.route("/api/peering-cost-benefit/<aid>", methods=["GET"])
    @nc_login_required
    def nc_api_peering_cost_benefit(aid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        agr = conn.execute(f"SELECT * FROM nc_peering_agreements WHERE id={_ph}", (aid,)).fetchone()
        if not agr:
            conn.close()
            return jsonify({"error": "Not found"}), 404
        agr = _row_to_dict(agr)
        # Get traffic data
        sessions = conn.execute(f"SELECT id FROM nc_peering_sessions WHERE agreement_id={_ph}", (aid,)).fetchall()
        total_in = total_out = 0
        for s in sessions:
            t = conn.execute(
                "SELECT inbound_mbps, outbound_mbps "
                f"FROM nc_peering_traffic WHERE session_id={_ph} "
                "ORDER BY measured_at DESC LIMIT 1",
                (s[0],),
            ).fetchone()
            if t:
                total_in += t[0] or 0
                total_out += t[1] or 0
        conn.close()
        peer_cost = agr.get("monthly_cost", 0) or 0
        # Estimate transit cost for same traffic (industry avg ~$0.50/Mbps)
        transit_rate = 0.50  # $/Mbps/month
        total_traffic = total_in + total_out
        transit_cost = total_traffic * transit_rate
        savings = transit_cost - peer_cost
        return jsonify(
            {
                "peer_name": agr.get("peer_name"),
                "peering_cost_monthly": peer_cost,
                "transit_equivalent_monthly": round(transit_cost, 2),
                "monthly_savings": round(savings, 2),
                "annual_savings": round(savings * 12, 2),
                "traffic_mbps": {"inbound": total_in, "outbound": total_out},
                "transit_rate_per_mbps": transit_rate,
            }
        )

    @bp.route("/peering")
    @nc_login_required
    def nc_peering_page():
        return render_template("network/peering.html")

    # ══════════════════════════════════════════════════════════════════════
    # Partner Registry
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/partners")
    @nc_login_required
    def nc_partners_page():
        return render_template("network/partners.html")

    @bp.route("/api/partners", methods=["GET"])
    @nc_login_required
    def nc_api_list_partners():
        from tools.network.partner_registry import list_partners
        conn = get_connection()
        try:
            status = request.args.get("status", "")
            return jsonify(list_partners(conn, status=status))
        finally:
            conn.close()

    @bp.route("/api/partners", methods=["POST"])
    @nc_login_required
    def nc_api_create_partner():
        from tools.network.partner_registry import create_partner
        data = request.get_json(force=True) or {}
        if not data.get("name"):
            return jsonify({"error": "name is required"}), 400
        conn = get_connection()
        try:
            result = create_partner(conn, data)
            _audit("CREATE", "partner", result["partner_id"], conn)
            return jsonify(result), 201
        finally:
            conn.close()

    @bp.route("/api/partners/<pid>", methods=["GET"])
    @nc_login_required
    def nc_api_get_partner(pid):
        from tools.network.partner_registry import get_partner
        conn = get_connection()
        try:
            partner = get_partner(conn, pid)
            if not partner:
                return jsonify({"error": "not found"}), 404
            return jsonify(partner)
        finally:
            conn.close()

    @bp.route("/api/partners/<pid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_partner(pid):
        from tools.network.partner_registry import update_partner
        data = request.get_json(force=True) or {}
        conn = get_connection()
        try:
            result = update_partner(conn, pid, data)
            _audit("UPDATE", "partner", pid, conn)
            return jsonify(result)
        finally:
            conn.close()

    @bp.route("/api/partners/<pid>/unified-view", methods=["GET"])
    @nc_login_required
    def nc_api_partner_unified_view(pid):
        from tools.network.partner_registry import get_unified_view
        conn = get_connection()
        try:
            return jsonify(get_unified_view(conn, pid))
        finally:
            conn.close()

    @bp.route("/api/partners/by-asn", methods=["GET"])
    @nc_login_required
    def nc_api_partner_by_asn():
        """Return partner matching ?asn=<int>. Returns {} if not found."""
        asn = request.args.get("asn", type=int)
        if asn is None:
            return jsonify({"error": "asn is required"}), 400
        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            try:
                cur = conn.execute(f"SELECT * FROM nc_partners WHERE asn = {_ph} LIMIT 1", (asn,))
            except Exception:
                cur = conn.cursor()
                cur.execute("SELECT * FROM nc_partners WHERE asn = %s LIMIT 1", (asn,))
            row = cur.fetchone()
            if not row:
                return jsonify({})
            cols = [d[0] for d in cur.description]
            return jsonify(dict(zip(cols, row)))
        finally:
            conn.close()

    # ── Agreement lifecycle routes ─────────────────────────────────────────

    @bp.route("/api/peering-agreements/<aid>/approve", methods=["POST"])
    @nc_login_required
    def nc_api_approve_agreement(aid):
        from tools.network.agreement_lifecycle import approve_agreement
        data = request.get_json(force=True) or {}
        approver = data.get("approver", "")
        role = data.get("role", "")
        if not approver or not role:
            return jsonify({"error": "approver and role are required"}), 400
        conn = get_connection()
        try:
            result = approve_agreement(conn, aid, approver, role)
            _audit("APPROVE", "peering_agreement", aid, conn)
            return jsonify(result)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        finally:
            conn.close()

    @bp.route("/api/peering-agreements/<aid>/amend", methods=["POST"])
    @nc_login_required
    def nc_api_amend_agreement(aid):
        from tools.network.agreement_lifecycle import create_amendment
        data = request.get_json(force=True) or {}
        conn = get_connection()
        try:
            result = create_amendment(
                conn, aid,
                changes=data.get("changes", {}),
                reason=data.get("reason", ""),
                amended_by=data.get("amended_by", ""),
                effective_date=data.get("effective_date", ""),
            )
            _audit("AMEND", "peering_agreement", aid, conn)
            return jsonify(result), 201
        finally:
            conn.close()

    @bp.route("/api/peering-agreements/<aid>/amendments", methods=["GET"])
    @nc_login_required
    def nc_api_list_amendments(aid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        try:
            try:
                rows = conn.execute("SELECT * FROM nc_agreement_amendments WHERE agreement_id=%s ORDER BY amendment_number", (aid,)).fetchall()
            except Exception:
                rows = conn.execute(f"SELECT * FROM nc_agreement_amendments WHERE agreement_id={_ph} ORDER BY amendment_number", (aid,)).fetchall()
            return jsonify([_row_to_dict(r) for r in rows])
        finally:
            conn.close()

    @bp.route("/api/peering-agreements/<aid>/document", methods=["GET"])
    @nc_login_required
    def nc_api_agreement_document(aid):
        from tools.network.agreement_lifecycle import generate_agreement_document
        conn = get_connection()
        try:
            doc = generate_agreement_document(conn, aid)
            return jsonify({"agreement_id": aid, "document": doc})
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        finally:
            conn.close()

    @bp.route("/api/peering-agreements/<aid>/sync-to-pmc", methods=["POST"])
    @nc_login_required
    def nc_api_agreement_sync_to_pmc(aid):
        from tools.network.agreement_lifecycle import sync_to_pmc
        conn = get_connection()
        try:
            result = sync_to_pmc(conn, aid)
            return jsonify(result)
        finally:
            conn.close()

    # ══════════════════════════════════════════════════════════════════════
    # Capacity Planning (Port/Slot/Fiber/Circuit)
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/port-inventory", methods=["GET"])
    @nc_login_required
    def nc_api_list_port_inventory():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_port_inventory ORDER BY device_label").fetchall()
        conn.close()
        items = [_row_to_dict(r) for r in rows]
        for it in items:
            try:
                it["port_breakdown"] = json.loads(it.get("port_breakdown") or "{}")
            except Exception:
                it["port_breakdown"] = {}
        return jsonify(items)

    @bp.route("/api/port-inventory", methods=["POST"])
    @nc_login_required
    def nc_api_set_port_inventory():
        data = request.get_json(force=True, silent=True) or {}
        pb = data.get("port_breakdown", {})
        if isinstance(pb, dict):
            pb = json.dumps(pb)
        conn = get_connection()
        _ph = sql_placeholder(conn)
        # Dedup by device_label + topology
        conn.execute(
            f"DELETE FROM nc_port_inventory WHERE device_label={_ph} AND topology_id={_ph}",
            (data.get("device_label"), data.get("topology_id")),
        )
        rid = str(_uuid.uuid4())
        conn.execute(
            "INSERT INTO nc_port_inventory "
            "(id, device_label, topology_id, total_ports, used_ports, "
            f" port_breakdown, last_updated) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                rid,
                data.get("device_label", ""),
                data.get("topology_id"),
                data.get("total_ports", 0),
                data.get("used_ports", 0),
                pb,
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": rid}), 201

    @bp.route("/api/fiber-inventory", methods=["GET"])
    @nc_login_required
    def nc_api_list_fiber():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_fiber_inventory ORDER BY path_name").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/fiber-inventory", methods=["POST"])
    @nc_login_required
    def nc_api_create_fiber():
        data = request.get_json(force=True, silent=True) or {}
        data["available_strands"] = str(int(data.get("total_strands", 0) or 0) - int(data.get("lit_strands", 0) or 0))
        data["available_lambdas"] = str(
            int(data.get("total_lambdas", 0) or 0) - int(data.get("active_lambdas", 0) or 0)
        )
        return _crud_create(
            "nc_fiber_inventory",
            "",
            data,
            [
                "path_name",
                "path_a",
                "path_z",
                "fiber_type",
                "total_strands",
                "lit_strands",
                "available_strands",
                "total_lambdas",
                "active_lambdas",
                "available_lambdas",
                "per_lambda_gbps",
                "conduit_ducts",
                "conduit_used",
                "diverse_path",
                "notes",
            ],
        )

    @bp.route("/api/carrier-availability", methods=["GET"])
    @nc_login_required
    def nc_api_list_carrier_avail():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_carrier_availability ORDER BY carrier, path_name").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/carrier-availability", methods=["POST"])
    @nc_login_required
    def nc_api_create_carrier_avail():
        data = request.get_json(force=True, silent=True) or {}
        return _crud_create(
            "nc_carrier_availability",
            "",
            data,
            [
                "carrier",
                "path_name",
                "service_type",
                "available_bandwidth",
                "lead_time_days",
                "monthly_cost_est",
                "contract_term",
                "notes",
            ],
        )

    @bp.route("/capacity")
    @nc_login_required
    def nc_capacity_page():
        return render_template("network/capacity.html")

    # ══════════════════════════════════════════════════════════════════════
    # Facilities / DCIM
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/facilities", methods=["GET"])
    @nc_login_required
    def nc_api_list_facilities():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_facilities ORDER BY name").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/facilities", methods=["POST"])
    @nc_login_required
    def nc_api_create_facility():
        data = request.get_json(force=True, silent=True) or {}
        return _crud_create(
            "nc_facilities",
            "",
            data,
            [
                "name",
                "facility_type",
                "address",
                "city",
                "state",
                "country",
                "operator",
                "total_racks",
                "used_racks",
                "total_power_kw",
                "used_power_kw",
                "total_cooling_tons",
                "used_cooling_tons",
                "ups_capacity_kva",
                "ups_load_kva",
                "ups_runtime_min",
                "generator_kw",
                "generator_load_kw",
                "generator_fuel_hours",
                "notes",
            ],
        )

    @bp.route("/api/facilities/<fid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_facility(fid):
        return _crud_delete("nc_facilities", fid)

    @bp.route("/api/racks", methods=["GET"])
    @nc_login_required
    def nc_api_list_racks():
        fid = request.args.get("facility_id", "")
        conn = get_connection()
        _ph = sql_placeholder(conn)
        if fid:
            rows = conn.execute(
                "SELECT r.*, f.name AS facility_name "
                "FROM nc_racks r "
                "LEFT JOIN nc_facilities f ON f.id=r.facility_id "
                f"WHERE r.facility_id={_ph} ORDER BY r.rack_name",
                (fid,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT r.*, f.name AS facility_name "
                "FROM nc_racks r "
                "LEFT JOIN nc_facilities f ON f.id=r.facility_id "
                "ORDER BY f.name, r.rack_name"
            ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/racks", methods=["POST"])
    @nc_login_required
    def nc_api_create_rack():
        data = request.get_json(force=True, silent=True) or {}
        return _crud_create(
            "nc_racks",
            "",
            data,
            [
                "facility_id",
                "rack_name",
                "total_ru",
                "used_ru",
                "reserved_ru",
                "power_circuit_a",
                "power_circuit_b",
                "max_power_kw",
                "current_power_kw",
                "weight_capacity_lbs",
                "current_weight_lbs",
                "notes",
            ],
        )

    @bp.route("/api/racks/<rid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_rack(rid):
        return _crud_delete("nc_racks", rid)

    @bp.route("/facilities")
    @nc_login_required
    def nc_facilities_page():
        return render_template("network/facilities.html")

    # ══════════════════════════════════════════════════════════════════════
    # Unified Readiness Checker
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/readiness-check/<pid>", methods=["GET"])
    @nc_login_required
    def nc_api_readiness_check(pid):
        """Cross-layer feasibility check for a project.
        Checks: peering, ports, fiber, facilities (rack/power/cooling)."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        checks = []

        # Peering
        peer_count = conn.execute(
            "SELECT COUNT(*) FROM nc_peering_agreements "
            f"WHERE project_id={_ph} AND status IN "
            "('agreement_signed','technical_design','implemented','operational')",
            (pid,),
        ).fetchone()[0]
        checks.append(
            {
                "layer": "peering",
                "check": "Active peering agreements",
                "passed": peer_count > 0,
                "detail": f"{peer_count} active agreements",
            }
        )

        # Ports
        port_rows = conn.execute(
            "SELECT * FROM nc_port_inventory "
            "WHERE topology_id IN "
            "(SELECT topology_id FROM nc_project_topologies "
            f" WHERE project_id={_ph})",
            (pid,),
        ).fetchall()
        ports_avail = sum((r["total_ports"] or 0) - (r["used_ports"] or 0) for r in port_rows)
        checks.append(
            {
                "layer": "ports",
                "check": "Available device ports",
                "passed": ports_avail > 0 or len(port_rows) == 0,
                "detail": f"{ports_avail} ports available" if port_rows else "No port inventory data",
            }
        )

        # Fiber
        fiber_rows = conn.execute("SELECT * FROM nc_fiber_inventory").fetchall()
        fiber_avail = sum((r["available_strands"] or 0) for r in fiber_rows)
        checks.append(
            {
                "layer": "fiber",
                "check": "Available fiber strands",
                "passed": fiber_avail > 0 or len(fiber_rows) == 0,
                "detail": f"{fiber_avail} strands available" if fiber_rows else "No fiber inventory data",
            }
        )

        # Facilities
        fac_rows = conn.execute("SELECT * FROM nc_facilities").fetchall()
        for f in fac_rows:
            f = _row_to_dict(f)
            avail_ru = (f.get("total_racks", 0) or 0) * 42 - sum(
                r["used_ru"] or 0
                for r in conn.execute(f"SELECT used_ru FROM nc_racks WHERE facility_id={_ph}", (f["id"],)).fetchall()
            )
            avail_power = (f.get("total_power_kw", 0) or 0) - (f.get("used_power_kw", 0) or 0)
            avail_cooling = (f.get("total_cooling_tons", 0) or 0) - (f.get("used_cooling_tons", 0) or 0)
            power_pct = round((f.get("used_power_kw", 0) or 0) * 100 / max(f.get("total_power_kw", 1) or 1, 1))
            checks.append(
                {
                    "layer": "facility",
                    "check": f"Rack space at {f['name']}",
                    "passed": avail_ru > 4,
                    "detail": f"{avail_ru} RU available",
                }
            )
            checks.append(
                {
                    "layer": "power",
                    "check": f"Power at {f['name']}",
                    "passed": power_pct < 80,
                    "detail": f"{avail_power:.1f} kW available ({power_pct}% used)",
                }
            )
            checks.append(
                {
                    "layer": "cooling",
                    "check": f"Cooling at {f['name']}",
                    "passed": avail_cooling > 0,
                    "detail": f"{avail_cooling:.1f} tons available",
                }
            )

        conn.close()
        passed = sum(1 for c in checks if c["passed"])
        total = len(checks)
        return jsonify(
            {
                "project_id": pid,
                "checks": checks,
                "passed": passed,
                "total": total,
                "ready": passed == total,
                "readiness_pct": round(passed * 100 / max(total, 1)),
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # Phase 7: Innovation Flywheel
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/innovation")
    @nc_login_required
    def nc_innovation_hub():
        return render_template("network/innovation.html")

    # ── Ideas ─────────────────────────────────────────────────────────────
    @bp.route("/api/innovation-ideas", methods=["GET"])
    @nc_login_required
    def nc_api_list_ideas():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_innovation_ideas ORDER BY total_score DESC, created_at DESC").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/innovation-ideas", methods=["POST"])
    @nc_login_required
    def nc_api_submit_idea():
        data = request.get_json(force=True, silent=True) or {}
        iid = str(_uuid.uuid4())
        imp = int(data.get("impact_score", 5) or 5)
        feas = int(data.get("feasibility_score", 5) or 5)
        cost = int(data.get("cost_score", 5) or 5)
        total = round((imp * 0.4 + feas * 0.35 + cost * 0.25), 2)
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_innovation_ideas "
            "(id, title, description, category, submitted_by, "
            " impact_score, feasibility_score, cost_score, "
            " total_score, status, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                iid,
                data.get("title", ""),
                data.get("description", ""),
                data.get("category", "improvement"),
                data.get("submitted_by", ""),
                imp,
                feas,
                cost,
                total,
                "submitted",
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        _audit("SUBMIT_IDEA", "innovation", iid, data.get("title", ""))
        return jsonify({"id": iid, "total_score": total}), 201

    @bp.route("/api/innovation-ideas/<iid>/vote", methods=["POST"])
    @nc_login_required
    def nc_api_vote_idea(iid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"UPDATE nc_innovation_ideas SET votes=votes+1 WHERE id={_ph}", (iid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/innovation-ideas/<iid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_idea(iid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        allowed = ["status", "project_id"]
        fields, values = [], []
        for k in allowed:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if fields:
            values.append(iid)
            conn.execute(
                f"UPDATE nc_innovation_ideas "  # nosec B608
                f"SET {', '.join(fields)} WHERE id={_ph}",
                values,
            )
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/innovation-ideas/<iid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_idea(iid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_innovation_ideas WHERE id={_ph}", (iid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Tech Radar ────────────────────────────────────────────────────────
    @bp.route("/api/tech-radar", methods=["GET"])
    @nc_login_required
    def nc_api_list_tech_radar():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_tech_radar ORDER BY ring, technology").fetchall()
        conn.close()
        items = [_row_to_dict(r) for r in rows]
        by_ring = {"adopt": [], "trial": [], "assess": [], "hold": []}
        for it in items:
            ring = it.get("ring", "assess")
            by_ring.setdefault(ring, []).append(it)
        return jsonify({"items": items, "by_ring": by_ring})

    @bp.route("/api/tech-radar", methods=["POST"])
    @nc_login_required
    def nc_api_add_tech_radar():
        data = request.get_json(force=True, silent=True) or {}
        tid = str(_uuid.uuid4())
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_tech_radar "
            "(id, technology, ring, category, description, "
            " updated_by, created_at, updated_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                tid,
                data.get("technology", ""),
                data.get("ring", "assess"),
                data.get("category", "networking"),
                data.get("description", ""),
                data.get("updated_by", ""),
                _now(),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": tid}), 201

    @bp.route("/api/tech-radar/<tid>", methods=["PUT"])
    @nc_login_required
    def nc_api_update_tech_radar(tid):
        data = request.get_json(force=True, silent=True) or {}
        conn = get_connection()
        _ph = sql_placeholder(conn)
        old = conn.execute(f"SELECT ring FROM nc_tech_radar WHERE id={_ph}", (tid,)).fetchone()
        new_ring = data.get("ring", "")
        fields, values = [], []
        for k in ["technology", "ring", "category", "description", "updated_by"]:
            if k in data:
                fields.append(f"{k}={_ph}")
                values.append(data[k])
        if new_ring and old and new_ring != old[0]:
            fields.append(f"moved_from={_ph}")
            values.append(old[0])
        if fields:
            fields.append(f"updated_at={_ph}")
            values.append(_now())
            values.append(tid)
            conn.execute(
                f"UPDATE nc_tech_radar "  # nosec B608
                f"SET {', '.join(fields)} WHERE id={_ph}",
                values,
            )
            conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/tech-radar/<tid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_tech_radar(tid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_tech_radar WHERE id={_ph}", (tid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ── Lessons Learned ───────────────────────────────────────────────────
    @bp.route("/api/lessons-learned", methods=["GET"])
    @nc_login_required
    def nc_api_list_lessons():
        pid = request.args.get("project_id", "")
        conn = get_connection()
        _ph = sql_placeholder(conn)
        if pid:
            rows = conn.execute(
                "SELECT ll.*, p.name AS project_name "
                "FROM nc_lessons_learned ll "
                "LEFT JOIN nc_projects p ON p.id=ll.project_id "
                f"WHERE ll.project_id={_ph} ORDER BY ll.created_at DESC",
                (pid,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ll.*, p.name AS project_name "
                "FROM nc_lessons_learned ll "
                "LEFT JOIN nc_projects p ON p.id=ll.project_id "
                "ORDER BY ll.created_at DESC"
            ).fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/lessons-learned", methods=["POST"])
    @nc_login_required
    def nc_api_add_lesson():
        data = request.get_json(force=True, silent=True) or {}
        lid = str(_uuid.uuid4())
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_lessons_learned "
            "(id, project_id, title, category, what_happened, "
            " root_cause, lesson, recommendation, submitted_by, "
            f" created_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                lid,
                data.get("project_id"),
                data.get("title", ""),
                data.get("category", "technical"),
                data.get("what_happened", ""),
                data.get("root_cause", ""),
                data.get("lesson", ""),
                data.get("recommendation", ""),
                data.get("submitted_by", ""),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": lid}), 201

    @bp.route("/api/lessons-learned/<lid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_lesson(lid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_lessons_learned WHERE id={_ph}", (lid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    # ══════════════════════════════════════════════════════════════════════
    # Phase 6: Tech Refresh Planner + Replacement Mapper + Budget Forecast
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/replacement-map", methods=["GET"])
    @nc_login_required
    def nc_api_list_replacement_map():
        conn = get_connection()
        rows = conn.execute("SELECT * FROM nc_replacement_map ORDER BY old_vendor, old_model").fetchall()
        conn.close()
        return jsonify([_row_to_dict(r) for r in rows])

    @bp.route("/api/replacement-map", methods=["POST"])
    @nc_login_required
    def nc_api_add_replacement():
        data = request.get_json(force=True, silent=True) or {}
        rid = str(_uuid.uuid4())
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_replacement_map "
            "(id, old_vendor, old_model, new_vendor, new_model, "
            " new_cost, migration_effort, notes, is_builtin, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},0,{_ph})",
            (
                rid,
                data.get("old_vendor", ""),
                data.get("old_model", ""),
                data.get("new_vendor", ""),
                data.get("new_model", ""),
                data.get("new_cost", 0),
                data.get("migration_effort", "medium"),
                data.get("notes", ""),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": rid}), 201

    @bp.route("/api/replacement-map/<rid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_replacement(rid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_replacement_map WHERE id={_ph}", (rid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/projects/<pid>/refresh-plan", methods=["GET"])
    @nc_login_required
    def nc_api_list_refresh_plan(pid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        rows = conn.execute(
            f"SELECT * FROM nc_refresh_plans WHERE project_id={_ph} ORDER BY target_year, priority DESC", (pid,)
        ).fetchall()
        items = [_row_to_dict(r) for r in rows]
        # Budget summary by year
        by_year = {}
        for it in items:
            yr = it.get("target_year", 0)
            by_year.setdefault(yr, 0)
            by_year[yr] += it.get("replacement_cost", 0) or 0
        conn.close()
        return jsonify(
            {
                "items": items,
                "budget_by_year": by_year,
                "total_cost": sum(by_year.values()),
            }
        )

    @bp.route("/api/projects/<pid>/refresh-plan", methods=["POST"])
    @nc_login_required
    def nc_api_add_refresh_item(pid):
        data = request.get_json(force=True, silent=True) or {}
        rid = str(_uuid.uuid4())
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(
            "INSERT INTO nc_refresh_plans "
            "(id, project_id, device_label, old_model, eol_date, "
            " priority, replacement_model, replacement_cost, "
            " target_year, status, notes, created_at) "
            f"VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
            (
                rid,
                pid,
                data.get("device_label", ""),
                data.get("old_model", ""),
                data.get("eol_date", ""),
                data.get("priority", "medium"),
                data.get("replacement_model", ""),
                data.get("replacement_cost", 0),
                data.get("target_year", 2026),
                data.get("status", "planned"),
                data.get("notes", ""),
                _now(),
            ),
        )
        conn.commit()
        conn.close()
        return jsonify({"id": rid}), 201

    @bp.route("/api/refresh-plan/<rid>", methods=["DELETE"])
    @nc_login_required
    def nc_api_delete_refresh_item(rid):
        conn = get_connection()
        _ph = sql_placeholder(conn)
        conn.execute(f"DELETE FROM nc_refresh_plans WHERE id={_ph}", (rid,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @bp.route("/api/projects/<pid>/auto-refresh-plan", methods=["POST"])
    @nc_login_required
    def nc_api_auto_refresh_plan(pid):
        """Auto-generate refresh plan from tech debt analysis.
        Scans all project topologies for EOL/EOS devices and creates
        refresh items with replacement suggestions from the map."""
        conn = get_connection()
        _ph = sql_placeholder(conn)
        topo_ids = [
            r[0]
            for r in conn.execute(f"SELECT topology_id FROM nc_project_topologies WHERE project_id={_ph}", (pid,)).fetchall()
        ]

        # Load replacement map
        rep_map = {}
        for r in conn.execute(
            "SELECT old_model, new_model, new_cost, migration_effort FROM nc_replacement_map"
        ).fetchall():
            rep_map[r[0].lower()] = _row_to_dict(r)

        now = datetime.now(timezone.utc)
        items_created = 0
        for tid in topo_ids:
            row = conn.execute(f"SELECT graph_json FROM topologies WHERE id={_ph}", (tid,)).fetchone()
            if not row:
                continue
            try:
                graph = json.loads(row["graph_json"])
            except Exception:
                continue
            for n in graph.get("nodes", []):
                cfg = n.get("config") or n.get("configData") or {}
                eol = cfg.get("eol_date", "")
                model = cfg.get("model", "")
                if not eol and not model:
                    continue
                # Determine priority from EOL date
                priority = "low"
                if eol:
                    try:
                        eol_dt = datetime.fromisoformat(eol + "T00:00:00+00:00")
                        months = (eol_dt - now).days / 30.44
                        if months <= 0:
                            priority = "critical"
                        elif months <= 12:
                            priority = "high"
                        elif months <= 24:
                            priority = "medium"
                    except (ValueError, TypeError):
                        pass
                # Look up replacement
                rep = rep_map.get(model.lower(), {})
                target_yr = now.year
                if priority == "medium":
                    target_yr = now.year + 1
                elif priority == "low":
                    target_yr = now.year + 2

                conn.execute(
                    "INSERT INTO nc_refresh_plans "
                    "(id, project_id, device_label, old_model, "
                    " eol_date, priority, replacement_model, "
                    " replacement_cost, target_year, status, "
                    f" created_at) VALUES ({_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph},{_ph})",
                    (
                        str(_uuid.uuid4()),
                        pid,
                        n.get("label", ""),
                        model,
                        eol,
                        priority,
                        rep.get("new_model", ""),
                        rep.get("new_cost", 0),
                        target_yr,
                        "planned",
                        _now(),
                    ),
                )
                items_created += 1

        conn.commit()
        conn.close()
        _audit("AUTO_REFRESH", "project", pid, f"{items_created} items")
        return jsonify({"items_created": items_created}), 201

    @bp.route("/api/budget-forecast", methods=["GET"])
    @nc_login_required
    def nc_api_budget_forecast():
        """Enterprise-wide budget forecast from all refresh plans."""
        conn = get_connection()
        rows = conn.execute(
            "SELECT target_year, "
            "SUM(replacement_cost) AS total_cost, "
            "COUNT(*) AS item_count "
            "FROM nc_refresh_plans "
            "GROUP BY target_year ORDER BY target_year"
        ).fetchall()
        conn.close()
        forecast = [_row_to_dict(r) for r in rows]
        grand_total = sum(f.get("total_cost", 0) or 0 for f in forecast)
        return jsonify(
            {
                "forecast": forecast,
                "grand_total": grand_total,
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # Phase 5: Discovered Data -> Simulation/Impact/What-If Bridge
    # ══════════════════════════════════════════════════════════════════════

    @bp.route("/api/what-if/link-failure", methods=["POST"])
    @nc_login_required
    def nc_api_whatif_link_failure():
        """Simulate a link failure using real routing table data.
        Shows which prefixes lose reachability and alternate paths."""
        data = request.get_json(force=True, silent=True) or {}
        failed_link_src = data.get("source_device", "")
        failed_link_dst = data.get("target_device", "")
        if not failed_link_src or not failed_link_dst:
            return jsonify({"error": "source_device and target_device required"}), 400

        conn = get_connection()
        # Get all routing entries
        all_routes = [
            _row_to_dict(r) for r in conn.execute("SELECT * FROM nc_routing_entries ORDER BY device_ip").fetchall()
        ]
        conn.close()

        # Build forwarding table per device
        fwd = {}  # device_ip -> [{prefix, next_hop, protocol}]
        for r in all_routes:
            fwd.setdefault(r["device_ip"], []).append(r)

        # Find routes that use the failed link
        affected_prefixes = []
        surviving_prefixes = []
        for r in all_routes:
            if r["device_ip"] == failed_link_src and r["next_hop"] == failed_link_dst:
                # This route goes through the failed link
                # Check if there's an alternate path
                alternates = [
                    alt
                    for alt in fwd.get(r["device_ip"], [])
                    if alt["prefix"] == r["prefix"]
                    and alt["next_hop"] != failed_link_dst
                    and alt["next_hop"] != "0.0.0.0"  # nosec B104 — route filter, not socket bind
                ]
                entry = {
                    "prefix": r["prefix"],
                    "protocol": r["protocol"],
                    "failed_next_hop": failed_link_dst,
                    "alternate_paths": len(alternates),
                    "alternates": [
                        {"next_hop": a["next_hop"], "protocol": a["protocol"], "metric": a.get("metric", 0)}
                        for a in alternates
                    ],
                }
                if alternates:
                    surviving_prefixes.append(entry)
                else:
                    affected_prefixes.append(entry)

        return jsonify(
            {
                "scenario": f"Link failure: {failed_link_src} -> {failed_link_dst}",
                "affected_prefixes": affected_prefixes,
                "surviving_prefixes": surviving_prefixes,
                "total_affected": len(affected_prefixes),
                "total_surviving": len(surviving_prefixes),
                "has_full_redundancy": len(affected_prefixes) == 0,
            }
        )

    @bp.route("/api/what-if/device-failure", methods=["POST"])
    @nc_login_required
    def nc_api_whatif_device_failure():
        """Simulate a device going offline. Shows impact on all
        devices that route through it."""
        data = request.get_json(force=True, silent=True) or {}
        failed_device = data.get("device_ip", "")
        if not failed_device:
            return jsonify({"error": "device_ip required"}), 400

        conn = get_connection()
        all_routes = [
            _row_to_dict(r) for r in conn.execute("SELECT * FROM nc_routing_entries ORDER BY device_ip").fetchall()
        ]
        conn.close()

        # Find all routes whose next_hop is the failed device
        impacted_devices = {}
        for r in all_routes:
            if r["next_hop"] == failed_device and r["device_ip"] != failed_device:
                dip = r["device_ip"]
                if dip not in impacted_devices:
                    impacted_devices[dip] = {
                        "hostname": r.get("hostname", dip),
                        "lost_prefixes": [],
                    }
                impacted_devices[dip]["lost_prefixes"].append(
                    {
                        "prefix": r["prefix"],
                        "protocol": r["protocol"],
                    }
                )

        # Prefixes hosted by the failed device (connected routes)
        hosted_prefixes = [
            r["prefix"] for r in all_routes if r["device_ip"] == failed_device and r.get("protocol") == "connected"
        ]

        return jsonify(
            {
                "scenario": f"Device failure: {failed_device}",
                "impacted_devices": [{"device_ip": k, **v} for k, v in impacted_devices.items()],
                "total_impacted_devices": len(impacted_devices),
                "hosted_prefixes_lost": hosted_prefixes,
                "total_hosted_lost": len(hosted_prefixes),
            }
        )

    @bp.route("/api/what-if/add-link", methods=["POST"])
    @nc_login_required
    def nc_api_whatif_add_link():
        """Simulate adding a new link — predict how routing would
        change based on protocol and metric."""
        data = request.get_json(force=True, silent=True) or {}
        src = data.get("source_device", "")
        dst = data.get("target_device", "")
        protocol = data.get("protocol", "ospf")
        metric = data.get("metric", 10)

        conn = get_connection()
        _ph = sql_placeholder(conn)
        # Get current routes from src device
        src_routes = [
            _row_to_dict(r)
            for r in conn.execute(f"SELECT * FROM nc_routing_entries WHERE device_ip={_ph}", (src,)).fetchall()
        ]
        dst_routes = [
            _row_to_dict(r)
            for r in conn.execute(f"SELECT * FROM nc_routing_entries WHERE device_ip={_ph}", (dst,)).fetchall()
        ]
        conn.close()

        # Prefixes reachable via new link (dst's connected/local prefixes)
        new_reachable = []
        for r in dst_routes:
            if r.get("protocol") in ("connected", "local"):
                # Check if src already has a route to this prefix
                existing = [e for e in src_routes if e["prefix"] == r["prefix"]]
                if existing:
                    best = min(existing, key=lambda x: x.get("metric", 999))
                    if metric < best.get("metric", 999):
                        new_reachable.append(
                            {
                                "prefix": r["prefix"],
                                "improvement": "better_metric",
                                "old_metric": best.get("metric", 0),
                                "new_metric": metric,
                            }
                        )
                    else:
                        new_reachable.append(
                            {
                                "prefix": r["prefix"],
                                "improvement": "redundant_path",
                                "old_metric": best.get("metric", 0),
                                "new_metric": metric,
                            }
                        )
                else:
                    new_reachable.append(
                        {
                            "prefix": r["prefix"],
                            "improvement": "new_reachability",
                            "new_metric": metric,
                        }
                    )

        return jsonify(
            {
                "scenario": f"Add link: {src} -> {dst} ({protocol}, metric {metric})",
                "new_reachable_prefixes": new_reachable,
                "total_improvements": len(new_reachable),
                "new_reachability": sum(1 for p in new_reachable if p["improvement"] == "new_reachability"),
                "better_metric": sum(1 for p in new_reachable if p["improvement"] == "better_metric"),
                "redundant_paths": sum(1 for p in new_reachable if p["improvement"] == "redundant_path"),
            }
        )

    # ══════════════════════════════════════════════════════════════════════
    # Connect & Collect + Diagram Data Extraction
    # ══════════════════════════════════════════════════════════════════════
